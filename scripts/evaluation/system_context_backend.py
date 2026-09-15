# system_context_backend.py - 승인 v1.15 loader를 격리 process에서 실행하고 실제 토큰·저장 경로를 추적한다.

from __future__ import annotations

import fcntl
import gc
import os
import tempfile
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation.dashboard_v115_replay import header_check
from scripts.evaluation.system_context_cases import FROZEN_DATE, digest
from scripts.evaluation.system_context_contracts import (
    RAW_ROOT,
    code_fingerprint,
    environment_identity,
    prepare_context,
    read_json,
    safe_path,
    write_new,
)
from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_tokenizer_v1 import backend_sha256


class ObservedModel:
    def __init__(self, model, tokenizer, expected_ids):
        self.model = model
        self.tokenizer = tokenizer
        self.expected_ids = expected_ids
        self.telemetry = None

    def generate(self, input_ids, **kwargs):
        actual = input_ids[0].tolist()
        if actual != self.expected_ids or kwargs.get("do_sample") is not False:
            raise ValueError("생성 직전 실제 토큰/decoding이 동결 입력과 다릅니다.")
        output = self.model.generate(input_ids, **kwargs)
        ids = output[0, input_ids.shape[-1] :].tolist()
        eos = kwargs["eos_token_id"]
        eos = eos if isinstance(eos, list) else [eos]
        stop = (
            "eos"
            if ids and ids[-1] in eos
            else "max_tokens"
            if len(ids) == kwargs["max_new_tokens"]
            else "other"
        )
        self.telemetry = {
            "actual_generation_kwargs": kwargs,
            "output_token_ids": ids,
            "output_tokens": len(ids),
            "stop_reason": stop,
            "cache_scope": "fresh_generate_no_past_key_values",
            "retry_count": 0,
            "decoded_before_strip": self.tokenizer.decode(
                ids, skip_special_tokens=True
            ),
            "effective_backend_sha256": backend_sha256(self.tokenizer),
        }
        return output


def replay_output_consumers(context, case, engine, generated):
    """GPU 재호출 없이 실제 v1.15 저장/API 소비 함수를 격리 재생한다.

    입력 조건은 외부 진단 projection에서 이미 고정했다. 이 재생은 표시·저장만
    검증하므로 실제 서비스·브라우저 실행이라고 부르지 않는다.
    """
    with tempfile.TemporaryDirectory(prefix="saju-context-output-") as directory:
        local = deepcopy(context)
        local["repo_root"] = Path(directory)
        local["run_root"] = Path(directory) / "runs" / context["run_root"].name
        local["run_root"].mkdir(parents=True, mode=0o700)
        local["chart_only_runtime_active"] = True
        with (
            patch(
                "scripts.training.dashboard_grounding_v2.kst_today",
                return_value=FROZEN_DATE,
            ),
            patch.object(dashboard, "_generation_gate", return_value={"allowed": True}),
            patch.object(
                dashboard, "_engine_availability", return_value={"available": True}
            ),
            patch.object(
                dashboard, "_generate_engine_conversation", return_value=generated
            ) as generation,
        ):
            # GPU lock은 호출자가 소유하며 여기서는 CPU 소비 경로만 재생한다.
            result = dashboard._execute_manual_generation_with_gpu_lock_held(
                local,
                case["prompt"],
                engine_selection=engine,
                runtime_binding=case["binding"],
            )
            stored = dashboard.manual_session_payload(local, result["session_id"])
        if generation.call_count != 1:
            raise ValueError("출력 소비 경로가 응답을 재생성했습니다.")
        raw, displayed, saved = (
            generated["output"],
            result["output"],
            stored["messages"][-1]["content"],
        )
        if raw != displayed or raw != saved:
            raise ValueError("원응답/표시/API 저장 내용이 달라졌습니다.")
        return {
            "raw_sha256": digest(raw),
            "api_display_sha256": digest(displayed),
            "stored_sha256": digest(saved),
            "changed": False,
            "change_reason": None,
            "consumer_path": "v1.15_cpu_replay",
            "browser_executed": False,
        }


def worker(request_path, output_path):
    if os.environ.get("SYSTEM_CONTEXT_DIAGNOSIS") != "S0_S1_S2_V1":
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
    if engine not in {"k0_instruct", "lora_r16", "ki20_final"}:
        raise ValueError("등록하지 않은 모델")
    started = time.monotonic()
    torch, tokenizer, model = dashboard._load_engine_model(context, engine)
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
            for name, parameter in model.named_parameters()
            if "lora_" not in name
        ):
            raise ValueError("등록한 BF16 정밀도와 다릅니다.")
        if any(
            parameter.dtype not in {torch.bfloat16, torch.float32}
            for parameter in model.parameters()
        ):
            raise ValueError("승인 loader에 없는 정밀도입니다.")
        # native JIT 환경을 유지한 채 실제 BF16 CUDA 연산을 확인한다.
        check = torch.ones((8, 8), device="cuda", dtype=torch.bfloat16)
        if float((check @ check)[0, 0]) != 8.0:
            raise ValueError("BF16 CUDA 검증 실패")
        observed = ObservedModel(model, tokenizer, request["render"]["input_token_ids"])
        with torch.inference_mode():
            result = dashboard._generate_loaded(
                torch,
                tokenizer,
                observed,
                request["render"]["messages"],
                generation,
                max_input_tokens=4096,
            )
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
        consumers = replay_output_consumers(context, request["case"], engine, result)
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
