# phase4_k0.py - 고정 Kanana 원본 모델의 비학습 K0 생성·자동 검증을 수행한다.

from __future__ import annotations

import gc
import os
import re
import stat
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    load_json,
    read_jsonl,
    runtime_environment,
    sha256_bytes,
    sha256_file,
    sha256_json,
    utc_now,
    verify_candidate_build,
    verify_hash_map,
    verify_runtime_headers,
    write_json_once,
    write_jsonl_once,
)

CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SPECIAL_TOKEN_PATTERN = re.compile(r"<\|[^|\r\n]{1,80}\|>")
GANJI_PATTERN = re.compile(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]")
HANGUL_PATTERN = re.compile(r"[가-힣]")
K0_ARTIFACTS = ("run_config.json", "results.jsonl", "summary.json")


def _prepare_runtime(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verification = verify_runtime_headers(config, repo_root)
    environment = runtime_environment(config, repo_root)
    for key in (
        "CPATH",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_DATASETS_OFFLINE",
    ):
        os.environ[key] = environment[key]
    if os.environ.get("TORCH_DISABLE_NATIVE_JIT"):
        raise Phase4Error(
            "정식 K0에서는 TORCH_DISABLE_NATIVE_JIT를 사용할 수 없습니다."
        )
    return verification


def _load_model(
    context: dict[str, Any], repo_root: Path
) -> tuple[Any, Any, Any, dict[str, Any]]:
    _prepare_runtime(context["config"], repo_root)
    try:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase4Error(
            "고정 PyTorch/Transformers 모델 경로를 import하지 못했습니다."
        ) from exc

    if not torch.cuda.is_available():
        raise Phase4Error("K0에는 CUDA GPU가 필요합니다.")
    device_index = torch.cuda.current_device()
    if device_index != 0 or torch.cuda.device_count() != 1:
        raise Phase4Error("K0는 단일 cuda:0 장비 계약을 요구합니다.")
    config = context["config"]
    model_config = config["model"]
    snapshot = repo_root / model_config["local_subdir"]
    torch.manual_seed(config["seed"])
    torch.cuda.manual_seed_all(config["seed"])
    torch.backends.cudnn.benchmark = False
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device_index)
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            snapshot,
            local_files_only=True,
            trust_remote_code=True,
        )
        model = AutoModelForCausalLM.from_pretrained(
            snapshot,
            local_files_only=True,
            trust_remote_code=True,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
        ).to("cuda:0")
    except Exception as exc:
        raise Phase4Error(
            "고정 Kanana 모델을 BF16 SDPA CUDA 경로로 로드하지 못했습니다."
        ) from exc
    model.eval()
    template = config["chat_template"]
    if (
        tokenizer.bos_token_id != template["bos_token_id"]
        or tokenizer.eos_token_id != template["eos_token_id"]
        or tokenizer.pad_token_id != template["pad_token_id"]
        or not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8")) != template["sha256"]
    ):
        raise Phase4Error("K0 tokenizer/chat template/special token 계약이 다릅니다.")
    parameters = list(model.parameters())
    parameter_count = sum(parameter.numel() for parameter in parameters)
    if parameter_count != model_config["expected_parameter_count"]:
        raise Phase4Error(f"K0 모델 parameter 수가 다릅니다: {parameter_count}")
    if any(parameter.dtype != torch.bfloat16 for parameter in parameters):
        raise Phase4Error("K0 모델 parameter가 전부 BF16이 아닙니다.")
    if any(parameter.device.type != "cuda" for parameter in parameters):
        raise Phase4Error("K0 모델 parameter가 전부 CUDA에 있지 않습니다.")
    runtime = {
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "transformers": transformers.__version__,
        "gpu_name": torch.cuda.get_device_name(device_index),
        "gpu_capability": list(torch.cuda.get_device_capability(device_index)),
        "parameter_count": parameter_count,
        "parameter_dtype": "torch.bfloat16",
        "attention_backend": getattr(model.config, "_attn_implementation", None),
        "device": "cuda:0",
    }
    if runtime["attention_backend"] != "sdpa":
        raise Phase4Error(
            f"K0 attention backend가 SDPA가 아닙니다: {runtime['attention_backend']}"
        )
    return torch, tokenizer, model, runtime


def _release_model(torch: Any, model: Any) -> None:
    del model
    gc.collect()
    torch.cuda.empty_cache()


def _generate(
    torch: Any,
    tokenizer: Any,
    model: Any,
    messages: list[dict[str, str]],
    generation: dict[str, Any],
) -> dict[str, Any]:
    try:
        encoded = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
    except Exception as exc:
        raise Phase4Error("K0 prompt chat serialization이 실패했습니다.") from exc
    if not hasattr(encoded, "to") or "input_ids" not in encoded:
        raise Phase4Error("K0 tokenizer가 tensor BatchEncoding을 반환하지 않았습니다.")
    encoded = encoded.to("cuda:0")
    input_length = int(encoded["input_ids"].shape[-1])
    kwargs = {
        "do_sample": generation["do_sample"],
        "max_new_tokens": generation["max_new_tokens"],
        "num_beams": generation["num_beams"],
        "use_cache": generation["use_cache"],
        "eos_token_id": tokenizer.eos_token_id,
        "pad_token_id": tokenizer.pad_token_id,
    }
    try:
        with torch.inference_mode():
            output = model.generate(**encoded, **kwargs)
    except Exception as exc:
        raise Phase4Error("K0 greedy generation이 실패했습니다.") from exc
    generated_ids = output[0, input_length:].detach().cpu().tolist()
    text = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    return {
        "input_tokens": input_length,
        "generated_ids": generated_ids,
        "generated_tokens": len(generated_ids),
        "finished_with_eos": tokenizer.eos_token_id in generated_ids,
        "text": text,
    }


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _char_bigrams(value: str) -> Counter[str]:
    normalized = re.sub(r"\s+", "", value)
    return Counter(
        normalized[index : index + 2] for index in range(max(0, len(normalized) - 1))
    )


def _bigram_f1(reference: str | None, output: str) -> float | None:
    if not reference:
        return None
    left = _char_bigrams(reference)
    right = _char_bigrams(output)
    left_total = sum(left.values())
    right_total = sum(right.values())
    if not left_total or not right_total:
        return 0.0
    overlap = sum((left & right).values())
    precision = overlap / right_total
    recall = overlap / left_total
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 6)


def _repetition_ratio(value: str) -> float:
    compact = re.sub(r"\s+", "", value)
    grams = [compact[index : index + 4] for index in range(max(0, len(compact) - 3))]
    if not grams:
        return 0.0
    return round(1 - len(set(grams)) / len(grams), 6)


def _instruction_contract(output: str, contract: dict[str, Any]) -> bool:
    normalized = _normalized_text(output)
    if "exact_text" in contract:
        return normalized == contract["exact_text"]
    if "sentence_count" in contract:
        sentences = [
            value for value in re.split(r"(?<=[.!?다요])\s+", normalized) if value
        ]
        return len(sentences) == contract["sentence_count"]
    if "numbered_items" in contract:
        matches = re.findall(r"(?m)^\s*\d+[.)]\s+", output)
        return len(matches) == contract["numbered_items"]
    if "comma_items" in contract:
        return (
            len([value for value in re.split(r"[,，]", normalized) if value.strip()])
            == contract["comma_items"]
        )
    if "must_include" in contract:
        return all(value in output for value in contract["must_include"])
    return False


def _score_output(
    category: str,
    contract: dict[str, Any],
    reference: str | None,
    output: str,
) -> dict[str, Any]:
    nonempty = bool(output.strip())
    hangul_count = len(HANGUL_PATTERN.findall(output))
    visible_letters = len(re.findall(r"[A-Za-z가-힣]", output))
    fabricated_pairs = (
        GANJI_PATTERN.findall(output) if category == "missing_chart_handoff" else []
    )
    safety_violation = (
        category == "missing_chart_handoff" and len(fabricated_pairs) >= 4
    )
    contract_pass: bool | None = None
    if category == "missing_chart_handoff":
        contract_pass = not safety_violation and any(
            term in output for term in contract.get("expected_handoff_terms", [])
        )
    elif category == "general_korean_instruction":
        contract_pass = _instruction_contract(output, contract)
    elif category == "contradiction_hallucination":
        correct = str(contract.get("correct_chart_signature", ""))
        false = str(contract.get("false_chart_signature", ""))
        contract_pass = (
            correct in output
            and false not in output
            and any(term in output for term in contract.get("denial_terms", []))
        )
    elif category == "shensha_rule_qa" and isinstance(
        contract.get("expected_outcome"), bool
    ):
        positive = "성립" in output and not any(
            term in output for term in ("성립하지", "불성립")
        )
        contract_pass = positive is contract["expected_outcome"]
    elif category in {
        "deterministic_hard_fact",
        "branch_policy_contradiction",
        "saju_diary_bridge",
    }:
        required = contract.get("required_terms", [])
        contract_pass = bool(required) and all(term in output for term in required)
        if category == "branch_policy_contradiction":
            contract_pass = contract_pass and any(
                term in output for term in contract.get("denial_terms", [])
            )
    return {
        "nonempty": nonempty,
        "control_character_free": CONTROL_PATTERN.search(output) is None,
        "special_token_text_free": SPECIAL_TOKEN_PATTERN.search(output) is None,
        "hangul_ratio": round(hangul_count / visible_letters, 6)
        if visible_letters
        else 0.0,
        "repetition_4gram_ratio": _repetition_ratio(output),
        "reference_char_bigram_f1": _bigram_f1(reference, output),
        "automated_contract_pass": contract_pass,
        "fabricated_ganji_pair_count": len(fabricated_pairs),
        "safety_violation": safety_violation,
    }


def _load_eval_cases(
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root: Path = context["private_root"]
    config = context["config"]
    artifacts = config.get("artifacts", {})
    core_name = artifacts.get("core_eval", "core_eval_200.jsonl")
    holdout_name = artifacts.get("source_holdout", "source_holdout_500.jsonl")
    core = read_jsonl(root / f"eval/{core_name}", "Core Eval")
    holdout = read_jsonl(root / f"eval/{holdout_name}", "source holdout")
    flattened: list[dict[str, Any]] = []
    for split, items in (("core_eval", core), ("source_holdout", holdout)):
        for item_index, item in enumerate(items):
            cases = item.get("cases")
            if not isinstance(cases, list) or not cases:
                raise Phase4Error(
                    f"K0 eval item의 cases가 비었습니다: {item.get('eval_id')}"
                )
            for case_index, case in enumerate(cases):
                identity = {
                    "build_id": context["build_id"],
                    "eval_id": item["eval_id"],
                    "case_id": case["case_id"],
                    "split": split,
                }
                flattened.append(
                    {
                        **identity,
                        "result_id": sha256_json(identity)[:24],
                        "item_index": item_index,
                        "case_index": case_index,
                        "category": item["category"],
                        "hardness": item["hardness"],
                        "source_axis": item.get("source_axis"),
                        "automated_contract": item["automated_contract"],
                        "prompt_messages": case["prompt_messages"],
                        "reference_assistant": case.get("reference_assistant"),
                        "prompt_sha256": case["prompt_sha256"],
                    }
                )
    expected_items = config["triage"]["evaluation_items"]
    expected_cases = config["triage"]["generation_cases"]
    expected_core = sum(config["split"]["core_eval"].values())
    expected_holdout = sum(
        value["holdout"] for value in config["split"]["axes"].values()
    )
    if (
        len(core) != expected_core
        or len(holdout) != expected_holdout
        or len(core) + len(holdout) != expected_items
        or len(flattened) != expected_cases
    ):
        raise Phase4Error(
            f"K0 평가 수량이 다릅니다: core={len(core)}, holdout={len(holdout)}, cases={len(flattened)}"
        )
    if len({value["result_id"] for value in flattened}) != len(flattened):
        raise Phase4Error("K0 result_id가 중복됐습니다.")
    return [*core, *holdout], flattened


def _run_config(
    context: dict[str, Any], runtime_manifest: dict[str, Any]
) -> dict[str, Any]:
    config = context["config"]
    core_items = sum(config["split"]["core_eval"].values())
    holdout_items = sum(value["holdout"] for value in config["split"]["axes"].values())
    return {
        "schema_version": "1.0.0",
        "report_type": "k0_instruct_non_training_baseline",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "model": {
            key: config["model"][key]
            for key in (
                "repo_id",
                "revision",
                "phase3_build_id",
                "snapshot_manifest_sha256",
                "dtype",
                "attention_backend",
                "expected_parameter_count",
            )
        },
        "chat_template_sha256": config["chat_template"]["sha256"],
        "generation": config["generation"],
        "seed": config["seed"],
        "runtime_header_manifest_sha256": runtime_manifest["manifest_sha256"],
        "evaluation": {
            "core_items": core_items,
            "source_holdout_items": holdout_items,
            "generation_cases": config["triage"]["generation_cases"],
        },
        "execution_contract": {
            "training": False,
            "optimizer": False,
            "backward": False,
            "gradient": False,
            "torch_compile": False,
            "native_jit_disabled": False,
        },
        "training_promotion_allowed": False,
    }


def probe_runtime(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    torch, tokenizer, model, runtime = _load_model(context, repo_root)
    try:
        generated = _generate(
            torch,
            tokenizer,
            model,
            [{"role": "user", "content": "한국어로 '준비 완료'라고 짧게 답해 주세요."}],
            {
                **context["config"]["generation"],
                "max_new_tokens": 16,
            },
        )
        if not generated["text"] or not generated["generated_ids"]:
            raise Phase4Error("K0 runtime probe가 빈 출력을 생성했습니다.")
        peak = int(torch.cuda.max_memory_allocated(0))
        return {
            "status": "passed",
            "runtime": runtime,
            "input_tokens": generated["input_tokens"],
            "generated_tokens": generated["generated_tokens"],
            "finished_with_eos": generated["finished_with_eos"],
            "nonempty": True,
            "peak_vram_bytes": peak,
            "training_performed": False,
        }
    finally:
        _release_model(torch, model)


def _load_existing_result(
    path: Path, expected: dict[str, Any]
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = load_json(path, "K0 case result")
    for key in ("result_id", "eval_id", "case_id", "prompt_sha256"):
        if value.get(key) != expected.get(key):
            raise Phase4Error(f"기존 K0 case identity가 다릅니다: {path.name}:{key}")
    if not isinstance(value.get("generated_ids"), list) or not isinstance(
        value.get("output"), str
    ):
        raise Phase4Error(f"기존 K0 case output이 올바르지 않습니다: {path.name}")
    if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
        raise Phase4Error(f"기존 K0 case 파일 권한이 0600이 아닙니다: {path.name}")
    return value


def _reuse_contract(run_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": run_config.get("model"),
        "chat_template_sha256": run_config.get("chat_template_sha256"),
        "generation": run_config.get("generation"),
        "seed": run_config.get("seed"),
        "runtime_header_manifest_sha256": run_config.get(
            "runtime_header_manifest_sha256"
        ),
        "execution_contract": run_config.get("execution_contract"),
        "training_promotion_allowed": run_config.get("training_promotion_allowed"),
    }


def _load_cross_build_reuse(
    context: dict[str, Any], repo_root: Path, current_run_config: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    reuse = context["config"]["k0_reuse"]
    source_root = repo_root / reuse["source_root"]
    manifest_path = source_root / "run_manifest.json"
    config_path = source_root / "run_config.json"
    if (
        source_root.is_symlink()
        or not source_root.is_dir()
        or sha256_file(manifest_path) != reuse["source_run_manifest_sha256"]
        or sha256_file(config_path) != reuse["source_run_config_sha256"]
    ):
        raise Phase4Error("K0 재사용 원본 경로 또는 고정 hash가 다릅니다.")
    manifest = load_json(manifest_path, "K0 재사용 원본 manifest")
    source_config = load_json(config_path, "K0 재사용 원본 config")
    if (
        manifest.get("build_id") != reuse["source_build_id"]
        or manifest.get("status") != "passed"
        or manifest.get("training_promotion_allowed") is not False
        or _reuse_contract(source_config) != _reuse_contract(current_run_config)
    ):
        raise Phase4Error("K0 재사용 원본의 모델·template·generation 계약이 다릅니다.")
    verify_hash_map(source_root, manifest.get("artifact_sha256"), "K0 재사용 원본")
    rows = read_jsonl(source_root / "results.jsonl", "K0 재사용 results")
    reuse_by_prompt: dict[str, dict[str, Any]] = {}
    for row in rows:
        relative = row.get("item_path")
        if not isinstance(relative, str):
            raise Phase4Error("K0 재사용 result item 경로가 없습니다.")
        item = load_json(source_root / relative, "K0 재사용 item")
        prompt_sha256 = item.get("prompt_sha256")
        if not isinstance(prompt_sha256, str):
            raise Phase4Error("K0 재사용 item prompt hash가 없습니다.")
        existing = reuse_by_prompt.get(prompt_sha256)
        if existing is not None and (
            existing.get("prompt_messages") != item.get("prompt_messages")
            or existing.get("generated_ids") != item.get("generated_ids")
            or existing.get("output") != item.get("output")
        ):
            raise Phase4Error("같은 K0 prompt의 재사용 출력이 서로 다릅니다.")
        item["source_item_sha256"] = sha256_file(source_root / relative)
        reuse_by_prompt[prompt_sha256] = item
    expected_source_cases = int(reuse.get("source_generation_cases", 720))
    if len(rows) != expected_source_cases:
        raise Phase4Error(
            f"K0 재사용 원본 수량이 다릅니다: {len(rows)}/{expected_source_cases}"
        )
    return reuse_by_prompt


def _result_projection(value: dict[str, Any], relative: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "result_id": value["result_id"],
        "eval_id": value["eval_id"],
        "case_id": value["case_id"],
        "split": value["split"],
        "category": value["category"],
        "hardness": value["hardness"],
        "source_axis": value.get("source_axis"),
        "item_path": relative,
        "item_sha256": sha256_file(Path(value["absolute_path"])),
        "input_tokens": value["input_tokens"],
        "generated_tokens": value["generated_tokens"],
        "finished_with_eos": value["finished_with_eos"],
        "metrics": value["metrics"],
    }


def _system_ram() -> dict[str, int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, raw = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable"}:
                values[name] = int(raw.strip().split()[0]) * 1024
    except (OSError, UnicodeError, ValueError):
        return {"total_bytes": None, "available_bytes_at_finish": None}
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes_at_finish": values.get("MemAvailable"),
    }


def run_k0(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_candidate_build(context, repo_root)
    runtime_manifest = _prepare_runtime(context["config"], repo_root)
    _, cases = _load_eval_cases(context)
    root: Path = context["k0_root"]
    items_root = root / "items"
    root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    root.chmod(PRIVATE_DIR_MODE)
    items_root.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
    items_root.chmod(PRIVATE_DIR_MODE)
    run_config = _run_config(context, runtime_manifest)
    write_json_once(root / "run_config.json", run_config, mode=PRIVATE_FILE_MODE)
    if (root / "run_manifest.json").exists():
        return {
            **verify_k0_run(context, repo_root),
            "mode": "reused",
            "writes_performed": False,
        }

    reuse_by_prompt = _load_cross_build_reuse(context, repo_root, run_config)
    started = time.monotonic()
    torch, tokenizer, model, runtime = _load_model(context, repo_root)
    completed_values: list[dict[str, Any]] = []
    new_count = 0
    cross_build_reused = 0
    first_generation_ids: list[int] | None = None
    try:
        for index, case in enumerate(cases, 1):
            relative = f"items/{case['result_id']}.json"
            path = root / relative
            existing = _load_existing_result(path, case)
            if existing is None:
                reused = reuse_by_prompt.get(case["prompt_sha256"])
                if (
                    reused is not None
                    and reused.get("prompt_messages") != case["prompt_messages"]
                ):
                    raise Phase4Error("K0 재사용 prompt hash와 messages가 다릅니다.")
                generated = (
                    {
                        "text": reused["output"],
                        "generated_ids": reused["generated_ids"],
                        "input_tokens": reused["input_tokens"],
                        "generated_tokens": reused["generated_tokens"],
                        "finished_with_eos": reused["finished_with_eos"],
                    }
                    if reused is not None
                    else _generate(
                        torch,
                        tokenizer,
                        model,
                        case["prompt_messages"],
                        context["config"]["generation"],
                    )
                )
                metrics = _score_output(
                    case["category"],
                    case["automated_contract"],
                    case["reference_assistant"],
                    generated["text"],
                )
                existing = {
                    "schema_version": "1.0.0",
                    "result_id": case["result_id"],
                    "eval_id": case["eval_id"],
                    "case_id": case["case_id"],
                    "prompt_sha256": case["prompt_sha256"],
                    "split": case["split"],
                    "category": case["category"],
                    "hardness": case["hardness"],
                    "source_axis": case.get("source_axis"),
                    "prompt_messages": case["prompt_messages"],
                    "reference_assistant": case["reference_assistant"],
                    "automated_contract": case["automated_contract"],
                    "output": generated["text"],
                    "generated_ids": generated["generated_ids"],
                    "input_tokens": generated["input_tokens"],
                    "generated_tokens": generated["generated_tokens"],
                    "finished_with_eos": generated["finished_with_eos"],
                    "metrics": metrics,
                }
                if reused is not None:
                    existing["reuse_provenance"] = {
                        "source_build_id": context["config"]["k0_reuse"][
                            "source_build_id"
                        ],
                        "source_result_id": reused["result_id"],
                        "source_item_sha256": reused["source_item_sha256"],
                        "metrics_recomputed": True,
                    }
                    cross_build_reused += 1
                else:
                    new_count += 1
                write_json_once(path, existing, mode=PRIVATE_FILE_MODE)
            existing["absolute_path"] = str(path)
            completed_values.append(existing)
            if index == 1:
                first_generation_ids = existing["generated_ids"]
            if index % 10 == 0 or index == len(cases):
                print(
                    f"k0_progress={index}/{len(cases)} new={new_count} reused={cross_build_reused}",
                    file=sys.stderr,
                    flush=True,
                )

        first = cases[0]
        repeated = _generate(
            torch,
            tokenizer,
            model,
            first["prompt_messages"],
            context["config"]["generation"],
        )
        deterministic = repeated["generated_ids"] == first_generation_ids
        peak_vram = int(torch.cuda.max_memory_allocated(0))
        free_vram, total_vram = torch.cuda.mem_get_info(0)
    finally:
        _release_model(torch, model)

    projections = [
        _result_projection(value, f"items/{value['result_id']}.json")
        for value in completed_values
    ]
    cross_build_reused = sum("reuse_provenance" in value for value in completed_values)
    locally_generated = len(completed_values) - cross_build_reused
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in projections:
        by_category[value["category"]].append(value)
    safety_violations = [
        value
        for value in projections
        if value["metrics"].get("safety_violation") is True
    ]
    empty_count = sum(not value["metrics"]["nonempty"] for value in projections)
    control_count = sum(
        not value["metrics"]["control_character_free"] for value in projections
    )
    special_count = sum(
        not value["metrics"]["special_token_text_free"] for value in projections
    )
    gate_passed = (
        not safety_violations
        and empty_count == 0
        and control_count == 0
        and special_count == 0
        and deterministic
    )
    category_summary: dict[str, Any] = {}
    for category, values in sorted(by_category.items()):
        contract_values = [
            value["metrics"]["automated_contract_pass"]
            for value in values
            if value["metrics"]["automated_contract_pass"] is not None
        ]
        overlaps = [
            value["metrics"]["reference_char_bigram_f1"]
            for value in values
            if value["metrics"]["reference_char_bigram_f1"] is not None
        ]
        category_summary[category] = {
            "cases": len(values),
            "nonempty": sum(value["metrics"]["nonempty"] for value in values),
            "eos_finished": sum(value["finished_with_eos"] for value in values),
            "automated_contract_evaluated": len(contract_values),
            "automated_contract_passed": sum(contract_values),
            "mean_reference_char_bigram_f1": (
                round(sum(overlaps) / len(overlaps), 6) if overlaps else None
            ),
            "mean_generated_tokens": round(
                sum(value["generated_tokens"] for value in values) / len(values), 4
            ),
        }
    summary = {
        "schema_version": "1.0.0",
        "report_type": "k0_instruct_summary",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "generated_at": utc_now(),
        "status": "passed" if gate_passed else "blocked",
        "gate_c_passed": gate_passed,
        "evaluation_items": context["config"]["triage"]["evaluation_items"],
        "generation_cases": len(projections),
        "new_generation_cases": new_count,
        "cross_build_reused_cases": cross_build_reused,
        "locally_generated_cases": locally_generated,
        "reuse_contract": context["config"]["k0_reuse"],
        "empty_outputs": empty_count,
        "control_character_outputs": control_count,
        "special_token_text_outputs": special_count,
        "safety_violations": len(safety_violations),
        "safety_violation_result_ids": [
            value["result_id"] for value in safety_violations
        ],
        "determinism_replay_passed": deterministic,
        "max_new_tokens": context["config"]["generation"]["max_new_tokens"],
        "runtime": runtime,
        "runtime_headers": runtime_manifest,
        "peak_vram_bytes": peak_vram,
        "vram_total_bytes": int(total_vram),
        "vram_free_bytes_at_finish": int(free_vram),
        "system_ram": _system_ram(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "categories": category_summary,
        "quality_threshold_policy": "report_only_except_pipeline_integrity_and_missing_chart_safety",
        "raw_samples_in_summary": False,
        "training_performed": False,
        "optimizer_created": False,
        "backward_performed": False,
        "training_promotion_allowed": False,
        "phase4_training_smoke_allowed": gate_passed,
        "yarn_warning": {
            "upstream_factor": 40.0,
            "implicit_original_context_ratio": 8.0,
            "configuration_modified": False,
        },
    }
    write_jsonl_once(root / "results.jsonl", projections)
    write_json_once(root / "summary.json", summary, mode=PRIVATE_FILE_MODE)
    artifacts = artifact_hash_map(
        root, [*K0_ARTIFACTS, *[value["item_path"] for value in projections]]
    )
    run_manifest = {
        "schema_version": "1.0.0",
        "report_type": "k0_instruct_run_manifest",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "artifact_sha256": artifacts,
        "status": summary["status"],
        "completed_gates": ["A", "B", "C"] if gate_passed else ["A", "B"],
        "evaluated_gates": ["A", "B", "C"],
        "training_promotion_allowed": False,
    }
    write_json_once(root / "run_manifest.json", run_manifest, mode=PRIVATE_FILE_MODE)
    return {
        **verify_k0_run(context, repo_root),
        "mode": "completed",
        "writes_performed": True,
    }


def verify_k0_run(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_candidate_build(context, repo_root)
    root: Path = context["k0_root"]
    if (
        root.is_symlink()
        or not root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) & 0o077
    ):
        raise Phase4Error("K0 run 경로가 없거나 권한이 너무 넓습니다.")
    manifest = load_json(root / "run_manifest.json", "K0 run manifest")
    run_config = load_json(root / "run_config.json", "K0 run config")
    summary = load_json(root / "summary.json", "K0 summary")
    expected_cases = int(context["config"]["triage"]["generation_cases"])
    if (
        manifest.get("build_id") != context["build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or run_config
        != _run_config(context, verify_runtime_headers(context["config"], repo_root))
        or summary.get("build_id") != context["build_id"]
        or summary.get("generation_cases") != expected_cases
        or summary.get("training_performed") is not False
        or summary.get("optimizer_created") is not False
        or summary.get("backward_performed") is not False
        or summary.get("training_promotion_allowed") is not False
        or summary.get("cross_build_reused_cases", 0)
        + summary.get("locally_generated_cases", 0)
        != expected_cases
        or summary.get("reuse_contract") != context["config"]["k0_reuse"]
    ):
        raise Phase4Error("K0 run identity 또는 비학습 계약이 다릅니다.")
    verify_hash_map(root, manifest.get("artifact_sha256"), "K0")
    results = read_jsonl(root / "results.jsonl", "K0 results")
    if (
        len(results) != expected_cases
        or len({value.get("result_id") for value in results}) != expected_cases
    ):
        raise Phase4Error("K0 result 수량 또는 ID가 다릅니다.")
    for result in results:
        relative = result.get("item_path")
        if (
            not isinstance(relative, str)
            or relative != f"items/{result.get('result_id')}.json"
        ):
            raise Phase4Error("K0 result item 경로가 다릅니다.")
        item = load_json(root / relative, "K0 item")
        if (
            item.get("eval_id") != result.get("eval_id")
            or item.get("case_id") != result.get("case_id")
            or sha256_file(root / relative) != result.get("item_sha256")
        ):
            raise Phase4Error(f"K0 item/result identity가 다릅니다: {relative}")
    expected_status = "passed" if summary.get("gate_c_passed") is True else "blocked"
    if (
        manifest.get("status") != expected_status
        or summary.get("status") != expected_status
    ):
        raise Phase4Error("K0 Gate C status가 서로 다릅니다.")
    return {
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": f"verified_gate_c_{expected_status}",
        "gate_c_passed": summary["gate_c_passed"],
        "generation_cases": len(results),
        "safety_violations": summary.get("safety_violations"),
        "training_promotion_allowed": False,
        "phase4_training_smoke_allowed": summary.get("phase4_training_smoke_allowed"),
    }
