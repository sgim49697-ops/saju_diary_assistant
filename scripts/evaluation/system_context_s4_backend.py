# system_context_s4_backend.py - K0·3B를 각 공식 loader로 격리 실행하고 공통 생성·CUDA·소비 경로를 검증한다.

from __future__ import annotations

import fcntl
import gc
import os
import time
from collections import Counter

from scripts.evaluation.dashboard_v115_replay import header_check
from scripts.evaluation.system_context_backend import (
    ObservedModel,
    replay_output_consumers,
)
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_contracts import (
    environment_identity,
    prepare_context,
    read_json,
    safe_path,
    write_new,
)
from scripts.evaluation.system_context_s4_contracts import (
    RAW_ROOT,
    build_path,
    code_fingerprint,
)
from scripts.evaluation.system_context_s4_models import (
    ENGINES,
    load_tokenizer,
    model_root,
    registration,
    verify_models,
)
from scripts.evaluation.system_context_s4_projection import input_identity
from scripts.training import phase5_dashboard_v1_15 as dashboard


def generation_kwargs():
    return {
        "do_sample": False, "num_beams": 1, "num_beam_groups": 1, "num_return_sequences": 1,
        "max_new_tokens": 4096, "min_new_tokens": 0, "use_cache": True,
        "bos_token_id": 128000, "pad_token_id": 128001, "eos_token_id": [128010],
        "return_dict_in_generate": False, "output_scores": False,
        "renormalize_logits": False, "remove_invalid_values": False,
    }


def load_model(context, engine):
    from transformers import GenerationConfig

    if engine == "k0_instruct":
        torch, tokenizer, model = dashboard._load_engine_model(context, engine)
    elif engine == "kanana3b_instruct":
        import torch
        from transformers import AutoModelForCausalLM

        tokenizer = load_tokenizer(engine)
        model = AutoModelForCausalLM.from_pretrained(
            model_root(), local_files_only=True, trust_remote_code=False,
            dtype=torch.bfloat16, attn_implementation="sdpa", low_cpu_mem_usage=True,
        )
        if model.__class__.__name__ != registration()["architecture"]:
            raise ValueError("등록한 3B native loader와 다릅니다.")
        torch.cuda.init()
        torch.cuda.reset_peak_memory_stats()
        model = model.to("cuda:0")
        model.eval()
    else:
        raise ValueError("미등록 S4 loader")
    # 모델별 숨은 decoding default를 공통 기본값으로 초기화한다.
    model.generation_config = GenerationConfig()
    return torch, tokenizer, model


def generate_loaded(torch, tokenizer, observed, request):
    messages = request["render"]["messages"]
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt")
    actual = ids[0].tolist()
    if actual != request["render"]["input_token_ids"] or len(actual) > 4096:
        raise ValueError("S4 실제 입력이 동결 token과 다르거나 잘렸습니다.")
    generated = observed.generate(ids.to("cuda:0"), **generation_kwargs())
    text = tokenizer.decode(generated[0, ids.shape[-1]:], skip_special_tokens=True).strip()
    if not text:
        raise ValueError("S4 모델 출력이 비었습니다.")
    return {
        "output": text, "input_tokens": len(actual), "omitted_messages": 0,
        **input_identity(
            tokenizer, messages, actual, engine=request["engine"],
            expected_backend=request["render"]["tokenizer_backend_sha256"],
        ),
    }


def worker(request_path, output_path):
    if os.environ.get("SYSTEM_CONTEXT_S4") != "K0_KANANA3B_P0_V1":
        raise ValueError("명시 실행 환경 확인이 필요합니다.")
    header_check()
    request_path, output_path = safe_path(request_path), safe_path(output_path)
    request_path.relative_to(RAW_ROOT)
    if (
        output_path.parent != request_path.parent
        or output_path.name
        != request_path.name.replace(".input.json", ".response.json")
    ):
        raise ValueError("worker 출력 경로가 요청과 다릅니다.")
    lock_fd = int(os.environ.get("SYSTEM_CONTEXT_GPU_LOCK_FD", "-1"))
    if lock_fd < 0 or os.fstat(lock_fd).st_uid != os.getuid():
        raise ValueError("부모 GPU lock descriptor가 필요합니다.")
    lock_path = os.readlink(f"/proc/self/fd/{lock_fd}")
    if not lock_path.endswith("mix2k-v4-gpu/v1.0.0/.gpu-global.lock"):
        raise ValueError("공유 GPU lock이 아닙니다.")
    # 상속 fd에서는 재진입하며 독립 fd라면 배타성을 직접 획득한다.
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    request = read_json(request_path, private=True)
    frozen = read_json(
        request_path.parent / "prepared.json", private=True, max_bytes=128 * 1024 * 1024
    )
    if (
        request not in frozen["requests"]
        or request_path.parent != build_path(frozen["build_id"])
        or request_path.name != f"{request['request_id']}.input.json"
        or "build-" + digest(frozen["identity"])[:12] != frozen["build_id"]
        or digest(frozen["requests"]) != frozen["identity"]["input_sha256"]
        or frozen["identity"]["code_sha256"] != code_fingerprint()
        or frozen["identity"]["environment"] != environment_identity()
    ):
        raise ValueError("worker 입력/코드/환경이 동결 계약과 다릅니다.")
    marker = read_json(
        request_path.parent / f"{request['request_id']}.started.json", private=True
    )
    if marker["request_sha256"] != digest(request) or output_path.exists():
        raise ValueError("worker 시작/재생성 계약 위반")
    if marker.get("parent_pid") != os.getppid():
        raise ValueError("동결 실행을 시작한 부모 process가 아닙니다.")
    context = prepare_context()
    engine = request["engine"]
    if engine not in ENGINES:
        raise ValueError("등록하지 않은 모델")
    if verify_models(context) != frozen["identity"]["verified_model_files"]:
        raise ValueError("S4 모델 artifact identity 변경")
    started = time.monotonic()
    torch, tokenizer, model = load_model(context, engine)
    try:
        torch.manual_seed(20260915)
        torch.cuda.manual_seed_all(20260915)
        if torch.version.cuda != "13.0" or "sm_120" not in torch.cuda.get_arch_list():
            raise ValueError("검증된 Blackwell CUDA 환경이 아닙니다.")
        native = int(getattr(model.config, "max_position_embeddings", 0))
        generation = context["config"]["model_check"]["generation"]
        if (
            native < 8192
            or request["render"]["input_tokens"] + generation["max_new_tokens"] > native
        ):
            raise ValueError("모델 native context 상한 위반")
        dtypes = {
            name: str(parameter.dtype) for name, parameter in model.named_parameters()
        }
        if any(
            parameter.dtype != torch.bfloat16
            for parameter in model.parameters()
        ):
            raise ValueError("등록한 BF16 정밀도와 다릅니다.")
        if any(
            parameter.dtype not in {torch.bfloat16, torch.float32}
            for parameter in model.parameters()
        ):
            raise ValueError("승인 loader에 없는 정밀도입니다.")
        if any(str(parameter.device) != "cuda:0" for parameter in model.parameters()):
            raise ValueError("CPU offload나 분산 device는 허용하지 않습니다.")
        # native JIT 환경을 유지한 채 실제 BF16 CUDA 연산을 확인한다.
        check = torch.ones((8, 8), device="cuda", dtype=torch.bfloat16)
        if float((check @ check)[0, 0]) != 8.0:
            raise ValueError("BF16 CUDA 검증 실패")
        observed = ObservedModel(model, tokenizer, request["render"]["input_token_ids"])
        with torch.inference_mode():
            result = generate_loaded(torch, tokenizer, observed, request)
        if (
            result["omitted_messages"] != 0
            or result["input_token_ids_sha256"]
            != request["render"]["input_token_ids_sha256"]
        ):
            raise ValueError("생성 입력이 무삭제 비교 계약과 다릅니다.")
        torch.cuda.synchronize()
        telemetry = {
            **observed.telemetry,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "peak_allocated_bytes": int(torch.cuda.max_memory_allocated()),
            "peak_reserved_bytes": int(torch.cuda.max_memory_reserved()),
            "gpu_total_memory_used_mib": dashboard._gpu_snapshot().get("used_mib"),
            "precision": "bfloat16",
            "engine": engine,
            "adapter_used": False,
            "cpu_offload": False,
            "generation_defaults_policy": "fresh_common_GenerationConfig_explicit_kwargs",
            "native_context_tokens": native,
        }
        telemetry["parameter_dtypes"] = dict(Counter(dtypes.values()))
        telemetry["seed"] = 20260915
        telemetry["model_generation_defaults"] = model.generation_config.to_dict()
        if (
            telemetry["gpu_total_memory_used_mib"] is None
            or telemetry["gpu_total_memory_used_mib"] > 16384
        ):
            raise ValueError("GPU 메모리 상한 검증 실패")
        result.update(
            {
                k: telemetry[k]
                for k in ("peak_allocated_bytes", "gpu_total_memory_used_mib")
            }
        )
        consumers = replay_output_consumers(context, request["case"], "k0_instruct", result)
        consumers["consumer_path"] = "v1.15_k0_slot_cpu_replay_not_3b_app_integration"
        consumers["model_engine"] = engine
        consumers["storage_engine_slot"] = "k0_instruct"
        response = {
            "request_sha256": digest(request),
            "status": "generated",
            "output": result["output"],
            "generated": result,
            "telemetry": telemetry,
            "consumers": consumers,
        }
        write_new(output_path, response)
    finally:
        del model
        del tokenizer
        gc.collect()
        torch.cuda.empty_cache()
