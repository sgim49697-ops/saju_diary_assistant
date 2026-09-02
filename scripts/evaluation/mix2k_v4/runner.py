# runner.py - 동결 dev200을 5개 arm에서 순차·재개 가능하게 생성한다.

from __future__ import annotations

import argparse
import fcntl
import gc
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import (
    Mix2KV4ContractError,
    jsonl_bytes,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.training.mix2k_v4_lora import (
    Mix2KV4LoRAError,
    acquire_mix2k_v4_gpu_lock,
)
from scripts.training.mix2k_v4_lora import _run_identity as lora_run_identity
from scripts.training.mix2k_v4_lora import (
    _runtime_versions as validate_runtime_versions,
)
from scripts.training.mix2k_v4_lora import (
    _validate_config as validate_lora_config,
)
from scripts.training.mix2k_v4_lora import (
    _validate_data_build as validate_data_build,
)

from .backends import LocalArmRunner, effective_generation_payload
from .contracts import (
    DEFAULT_CONFIG,
    DEFAULT_SPEC_BUILD,
    EXPECTED_ARMS,
    Mix2KV4EvaluationError,
    absolute,
    atomic_write,
    ensure_directory,
    json_bytes,
    load_json,
    reject_symlink_components,
    validate_config,
    validate_dev_cases,
    validate_directory,
    validate_model_files,
)
from .graders import grade_turn
from .reporting import build_aggregate
from .reviews import load_quality_reviews, run_quality_review

RUNNER_PATH = Path(__file__).resolve()
CONTRACTS_PATH = RUNNER_PATH.with_name("contracts.py")
GRADERS_PATH = RUNNER_PATH.with_name("graders.py")
BACKENDS_PATH = RUNNER_PATH.with_name("backends.py")
REVIEWS_PATH = RUNNER_PATH.with_name("reviews.py")
REPORTING_PATH = RUNNER_PATH.with_name("reporting.py")
DATA_CONTRACTS_PATH = REPO_ROOT / "scripts/data/mix2k_v4_contracts.py"
TEACHER_RUNNER_PATH = REPO_ROOT / "scripts/data/mix2k_v4_teachers.py"
LORA_TRAINER_PATH = REPO_ROOT / "scripts/training/mix2k_v4_lora.py"
CANONICAL_PATH = REPO_ROOT / "scripts/runtime/calculation/canonical.py"
ARM_IDS = tuple(item[0] for item in EXPECTED_ARMS)
EXPECTED_LORA_TARGET_MODULES = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}
ITEM_FIELDS = {
    "schema_version",
    "evaluation_id",
    "arm_id",
    "arm_artifact_sha256",
    "case_id",
    "case_sha256",
    "axis",
    "runtime_snapshot_sha256",
    "turns",
    "peak_allocated_bytes",
    "peak_reserved_bytes",
    "raw_outputs_private",
    "correction_retry_performed",
    "generated_at_utc",
}
TURN_FIELDS = {
    "turn_index",
    "user",
    "output",
    "input_tokens",
    "new_tokens",
    "max_token_hit",
    "input_over_budget",
    "elapsed_seconds",
    "grade",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_artifact_root(artifact_root: Path) -> None:
    if not artifact_root.is_absolute():
        raise Mix2KV4EvaluationError("artifact root는 절대경로여야 합니다.")
    reject_symlink_components(artifact_root, "artifact root")


def _gpu_snapshot() -> dict[str, Any]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(lines) != 1:
        raise Mix2KV4EvaluationError("단일 GPU 상태를 확인하지 못했습니다.")
    fields = [value.strip() for value in lines[0].split(",")]
    if len(fields) != 7:
        raise Mix2KV4EvaluationError("GPU 상태 field 수가 다릅니다.")
    try:
        return {
            "index": int(fields[0]),
            "name": fields[1],
            "uuid": fields[2],
            "total_mib": int(fields[3]),
            "used_mib": int(fields[4]),
            "free_mib": int(fields[5]),
            "driver_version": fields[6],
        }
    except ValueError as exc:
        raise Mix2KV4EvaluationError("GPU 상태 숫자 field가 잘못됐습니다.") from exc


def _compute_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise Mix2KV4EvaluationError("GPU compute process 조회가 실패했습니다.")
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = [value.strip() for value in line.split(",")]
        if len(fields) != 3:
            raise Mix2KV4EvaluationError("GPU compute process field 수가 다릅니다.")
        try:
            processes.append(
                {
                    "pid": int(fields[0]),
                    "process_name": fields[1],
                    "used_gpu_memory_mib": int(fields[2]),
                }
            )
        except ValueError as exc:
            raise Mix2KV4EvaluationError(
                "GPU process 숫자 field가 잘못됐습니다."
            ) from exc
    return processes


def hardware_gate(config: Mapping[str, Any]) -> dict[str, Any]:
    try:
        import torch
    except Exception as exc:
        raise Mix2KV4EvaluationError("PyTorch import가 실패했습니다.") from exc
    limits = config["operational_limits"]
    gpu = _gpu_snapshot()
    processes = [item for item in _compute_processes() if item["pid"] != os.getpid()]
    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != limits["expected_gpu_count"]
        or not torch.cuda.is_bf16_supported()
        or gpu["used_mib"] > limits["max_total_gpu_memory_used_mib"]
        or gpu["free_mib"] < limits["min_free_gpu_memory_before_start_mib"]
        or (limits["require_no_active_compute_process_before_start"] and processes)
    ):
        raise Mix2KV4EvaluationError(
            "평가 GPU·BF16·free memory·compute process gate를 통과하지 못했습니다."
        )
    return {"gpu": gpu, "active_compute_processes": processes, "bf16_supported": True}


def _static_arm(
    arm_id: str, config: Mapping[str, Any], artifact_root: Path
) -> dict[str, Any]:
    key = "k0" if arm_id == "K0" else "ki20"
    contract = config["model_contracts"][key]
    root = artifact_root / contract["relative_path"]
    observed = validate_model_files(root, contract, arm_id)
    if arm_id == "KI20":
        run_root = root.parent
        manifest = load_json(run_root / "run_manifest.json", "KI20 run manifest")
        if (
            manifest.get("run_id") != contract["run_id"]
            or manifest.get("run_build_id") != contract["revision"]
            or manifest.get("status") != "trained_and_reloaded"
            or manifest.get("final_reload_passed") is not True
            or manifest.get("blind_source_test_inspected") is not False
            or manifest.get("production_promotion_allowed") is not False
        ):
            raise Mix2KV4EvaluationError("KI20 comparator run 계약이 다릅니다.")
    return {
        "arm_id": arm_id,
        "kind": "fixed_base" if arm_id == "K0" else "fixed_comparator",
        "rank": None,
        "model_root": str(root),
        "adapter_root": None,
        "artifact_sha256": sha256z(observed),
        "model_files": observed,
        "training_manifest": None,
    }


def sha256z(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _lora_arm(
    *,
    arm_id: str,
    rank: int,
    config: Mapping[str, Any],
    artifact_root: Path,
    data_manifest: Mapping[str, Any],
    lora_config_path: Path,
    lora_config: Mapping[str, Any],
    runtime_versions: Mapping[str, str],
    model_root: Path,
) -> dict[str, Any]:
    expected_run_id, expected_identity = lora_run_identity(
        mode="train",
        config_path=lora_config_path,
        data_manifest=data_manifest,
        rank=rank,
    )
    expected_preflight_id, _ = lora_run_identity(
        mode="preflight",
        config_path=lora_config_path,
        data_manifest=data_manifest,
        rank=rank,
    )
    parent = artifact_root / f"runs/K0-MIX2K-V4-LORA/v1.0.1/r{rank}"
    reject_symlink_components(parent, f"{arm_id} run root")
    candidates: list[tuple[Path, dict[str, Any]]] = []
    if parent.is_dir() and not parent.is_symlink():
        for path in sorted(parent.iterdir()):
            manifest_path = path / "training_manifest.json"
            if path.is_dir() and not path.is_symlink() and manifest_path.is_file():
                manifest = load_json(manifest_path, f"{arm_id} training manifest")
                metrics = manifest.get("metrics")
                if (
                    path.name == expected_run_id
                    and manifest.get("status") == "training_completed"
                    and manifest.get("completed") is True
                    and manifest.get("rank") == rank
                    and manifest.get("run_id") == expected_run_id
                    and manifest.get("identity") == expected_identity
                    and manifest.get("runtime_versions") == runtime_versions
                    and manifest.get("data_build_id") == data_manifest["build_id"]
                    and manifest.get("preflight_run_id") == expected_preflight_id
                    and manifest.get("max_length")
                    == data_manifest["selected_max_length"]
                    and manifest.get("rows") == 2000
                    and manifest.get("num_train_epochs") == 1
                    and manifest.get("base_weights_unchanged") is True
                    and manifest.get("adapter_only") is True
                    and manifest.get("full_fine_tuning_performed") is False
                    and manifest.get("ki20_training_performed") is False
                    and manifest.get("production_promotion_allowed") is False
                    and manifest.get("sealed_blind_accessed") is False
                    and isinstance(metrics, Mapping)
                    and metrics.get("adapter_reload_rank") == rank
                    and metrics.get("adapter_reload_match") is True
                    and metrics.get("target_linear_modules")
                    == lora_config["lora"]["expected_target_linear_modules"]
                ):
                    candidates.append((path, manifest))
    if len(candidates) != 1:
        raise Mix2KV4EvaluationError(
            f"{arm_id}의 현재 data build와 일치하는 완료 run은 정확히 하나여야 합니다."
        )
    run_root, manifest = candidates[0]
    adapter_root = run_root / "trainer/final_adapter"
    adapter_path = adapter_root / "adapter_model.safetensors"
    adapter_config = adapter_root / "adapter_config.json"
    for path, label in (
        (adapter_root, f"{arm_id} adapter root"),
        (adapter_path, f"{arm_id} adapter weights"),
        (adapter_config, f"{arm_id} adapter config"),
    ):
        reject_symlink_components(path, label)
    metrics = manifest["metrics"]
    if (
        adapter_root.is_symlink()
        or not adapter_root.is_dir()
        or adapter_path.is_symlink()
        or not adapter_path.is_file()
        or adapter_config.is_symlink()
        or not adapter_config.is_file()
        or (adapter_root / "model.safetensors").exists()
        or sha256_file(adapter_path) != metrics["adapter_model_sha256"]
        or sha256_file(adapter_config) != metrics["adapter_config_sha256"]
    ):
        raise Mix2KV4EvaluationError(f"{arm_id} adapter-only artifact가 다릅니다.")
    adapter_semantics = load_json(adapter_config, f"{arm_id} adapter config")
    target_modules = adapter_semantics.get("target_modules")
    if (
        adapter_semantics.get("r") != rank
        or adapter_semantics.get("lora_alpha") != lora_config["lora"]["lora_alpha"]
        or adapter_semantics.get("lora_dropout") != lora_config["lora"]["lora_dropout"]
        or adapter_semantics.get("bias") != lora_config["lora"]["bias"]
        or adapter_semantics.get("use_rslora") is not lora_config["lora"]["use_rslora"]
        or adapter_semantics.get("task_type") != lora_config["lora"]["task_type"]
        or adapter_semantics.get("peft_type") != "LORA"
        or adapter_semantics.get("inference_mode") is not True
        or adapter_semantics.get("modules_to_save") is not None
        or not isinstance(target_modules, list)
        or any(not isinstance(value, str) for value in target_modules)
        or set(target_modules) != EXPECTED_LORA_TARGET_MODULES
        or adapter_semantics.get("base_model_name_or_path") != str(model_root)
    ):
        raise Mix2KV4EvaluationError(f"{arm_id} adapter config 의미 계약이 다릅니다.")
    return {
        "arm_id": arm_id,
        "kind": "lora_adapter",
        "rank": rank,
        "model_root": None,
        "adapter_root": str(adapter_root),
        "artifact_sha256": sha256z(
            {
                "adapter_model_sha256": metrics["adapter_model_sha256"],
                "adapter_config_sha256": metrics["adapter_config_sha256"],
                "training_identity": manifest["identity"],
            }
        ),
        "model_files": None,
        "training_manifest": {
            "run_id": manifest["run_id"],
            "identity": manifest["identity"],
            "data_build_id": manifest["data_build_id"],
            "adapter_model_sha256": metrics["adapter_model_sha256"],
            "adapter_config_sha256": metrics["adapter_config_sha256"],
        },
    }


def resolve_arms(
    *,
    config: Mapping[str, Any],
    artifact_root: Path,
    data_manifest: Mapping[str, Any],
    lora_config_path: Path,
    lora_config: Mapping[str, Any],
    runtime_versions: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    k0 = _static_arm("K0", config, artifact_root)
    ki20 = _static_arm("KI20", config, artifact_root)
    values = {"K0": k0, "KI20": ki20}
    for arm_id, _, rank in EXPECTED_ARMS:
        if rank is not None:
            values[arm_id] = _lora_arm(
                arm_id=arm_id,
                rank=rank,
                config=config,
                artifact_root=artifact_root,
                data_manifest=data_manifest,
                lora_config_path=lora_config_path,
                lora_config=lora_config,
                runtime_versions=runtime_versions,
                model_root=Path(str(k0["model_root"])),
            )
            values[arm_id]["model_root"] = k0["model_root"]
    return {arm_id: values[arm_id] for arm_id in ARM_IDS}


def evaluation_identity(
    *,
    config_path: Path,
    spec_manifest: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    arms: Mapping[str, Mapping[str, Any]],
    generation: Mapping[str, Any],
    quality_review: Mapping[str, Any],
    runtime_versions: Mapping[str, str],
) -> tuple[str, dict[str, Any]]:
    effective_generation = effective_generation_payload(generation)
    identity = {
        "config_sha256": sha256_file(config_path),
        "spec_build_sha256": spec_manifest["build_sha256"],
        "dev_sha256": spec_manifest["artifact_sha256"][
            "evaluation/dev_cases_200.jsonl"
        ],
        "data_build_sha256": data_manifest["build_sha256"],
        "arm_artifact_sha256": {
            arm_id: arms[arm_id]["artifact_sha256"] for arm_id in ARM_IDS
        },
        "effective_generation": effective_generation,
        "effective_generation_sha256": sha256z(effective_generation),
        "runtime_versions": dict(runtime_versions),
        "quality_review_provider_contracts": quality_review["provider_contracts"],
        "external_review_transmission_contract": quality_review[
            "external_transmission"
        ],
        "implementation_sha256": {
            "runner": sha256_file(RUNNER_PATH),
            "contracts": sha256_file(CONTRACTS_PATH),
            "graders": sha256_file(GRADERS_PATH),
            "backends": sha256_file(BACKENDS_PATH),
            "reviews": sha256_file(REVIEWS_PATH),
            "reporting": sha256_file(REPORTING_PATH),
            "data_contracts": sha256_file(DATA_CONTRACTS_PATH),
            "teacher_runner": sha256_file(TEACHER_RUNNER_PATH),
            "lora_trainer": sha256_file(LORA_TRAINER_PATH),
            "canonical": sha256_file(CANONICAL_PATH),
        },
    }
    digest = sha256z(identity)
    return f"eval-{digest[:12]}", identity


def prepare_evaluation(
    *,
    config_path: Path,
    spec_build: Path,
    data_build: Path,
    artifact_root: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, dict[str, Any]],
    str,
    dict[str, Any],
]:
    _validate_artifact_root(artifact_root)
    config = validate_config(config_path)
    lora_config_path = REPO_ROOT / config["dependency_contracts"]["lora_config"]["path"]
    lora_config = validate_lora_config(lora_config_path)
    runtime_versions = validate_runtime_versions(lora_config)
    reject_symlink_components(data_build, "final data build")
    if not data_build.is_absolute():
        raise Mix2KV4EvaluationError("final data build는 절대경로여야 합니다.")
    spec_manifest, cases = validate_dev_cases(spec_build, config)
    data_manifest, _, _ = validate_data_build(
        data_build,
        lora_config,
    )
    if data_manifest["identity"]["spec_build_sha256"] != spec_manifest["build_sha256"]:
        raise Mix2KV4EvaluationError(
            "final data build와 frozen dev의 spec lineage가 다릅니다."
        )
    arms = resolve_arms(
        config=config,
        artifact_root=artifact_root,
        data_manifest=data_manifest,
        lora_config_path=lora_config_path,
        lora_config=lora_config,
        runtime_versions=runtime_versions,
    )
    eval_id, identity = evaluation_identity(
        config_path=config_path,
        spec_manifest=spec_manifest,
        data_manifest=data_manifest,
        arms=arms,
        generation=config["generation"],
        quality_review=config["quality_review"],
        runtime_versions=runtime_versions,
    )
    return config, spec_manifest, cases, data_manifest, arms, eval_id, identity


def _evaluation_root(
    config: Mapping[str, Any], artifact_root: Path, eval_id: str
) -> Path:
    return artifact_root / config["outputs"]["private_root"] / eval_id


def _case_item(
    *,
    case: Mapping[str, Any],
    arm: Mapping[str, Any],
    runner: LocalArmRunner,
    eval_id: str,
    generation: Mapping[str, Any],
) -> dict[str, Any]:
    messages = deepcopy(case["messages"])
    turns: list[dict[str, Any]] = []
    prior_outputs: list[str] = []
    user_turns = [case["messages"][-1]["content"], *case["followup_turns"]]
    for turn_index, user_text in enumerate(user_turns):
        if turn_index:
            messages.append({"role": "user", "content": user_text})
        started = time.monotonic()
        generated = runner.generate(messages)
        elapsed = round(time.monotonic() - started, 3)
        grade = grade_turn(
            case,
            generated.text,
            turn_index=turn_index,
            prior_outputs=prior_outputs,
            max_token_hit=generated.max_token_hit,
            input_over_budget=generated.input_over_budget,
            ngram_size=int(generation.get("minimum_ngram_size", 6)),
            repetition_threshold=float(
                generation.get("within_response_repeated_ngram_ratio", 0.35)
            ),
        )
        turns.append(
            {
                "turn_index": turn_index,
                "user": user_text,
                "output": generated.text,
                "input_tokens": generated.input_tokens,
                "new_tokens": generated.new_tokens,
                "max_token_hit": generated.max_token_hit,
                "input_over_budget": generated.input_over_budget,
                "elapsed_seconds": elapsed,
                "grade": grade,
            }
        )
        messages.append({"role": "assistant", "content": generated.text})
        prior_outputs.append(generated.text)
    return {
        "schema_version": "1.0.0",
        "evaluation_id": eval_id,
        "arm_id": arm["arm_id"],
        "arm_artifact_sha256": arm["artifact_sha256"],
        "case_id": case["case_id"],
        "case_sha256": sha256z(case),
        "axis": case["axis"],
        "runtime_snapshot_sha256": (
            case["runtime_binding"]["snapshot_sha256"]
            if case["runtime_binding"] is not None
            else None
        ),
        "turns": turns,
        "raw_outputs_private": True,
        "correction_retry_performed": False,
        "generated_at_utc": utc_now(),
    }


def _validate_case_item(
    item: Mapping[str, Any],
    *,
    case: Mapping[str, Any],
    arm: Mapping[str, Any],
    eval_id: str,
    generation: Mapping[str, Any],
) -> dict[str, Any]:
    expected_runtime_hash = (
        case["runtime_binding"]["snapshot_sha256"]
        if case["runtime_binding"] is not None
        else None
    )
    turns = item.get("turns")
    user_turns = [case["messages"][-1]["content"], *case["followup_turns"]]
    if (
        set(item) != ITEM_FIELDS
        or item.get("schema_version") != "1.0.0"
        or item.get("evaluation_id") != eval_id
        or item.get("arm_id") != arm["arm_id"]
        or item.get("arm_artifact_sha256") != arm["artifact_sha256"]
        or item.get("case_id") != case["case_id"]
        or item.get("case_sha256") != sha256z(case)
        or item.get("axis") != case["axis"]
        or item.get("runtime_snapshot_sha256") != expected_runtime_hash
        or not isinstance(turns, list)
        or len(turns) != len(user_turns)
        or not isinstance(item.get("peak_allocated_bytes"), int)
        or item["peak_allocated_bytes"] < 0
        or not isinstance(item.get("peak_reserved_bytes"), int)
        or item["peak_reserved_bytes"] < item["peak_allocated_bytes"]
        or item.get("raw_outputs_private") is not True
        or item.get("correction_retry_performed") is not False
        or not isinstance(item.get("generated_at_utc"), str)
    ):
        raise Mix2KV4EvaluationError(
            f"기존 {arm['arm_id']}/{case['case_id']} item 계약이 다릅니다."
        )
    prior_outputs: list[str] = []
    for turn_index, (turn, user_text) in enumerate(zip(turns, user_turns, strict=True)):
        if (
            not isinstance(turn, Mapping)
            or set(turn) != TURN_FIELDS
            or turn.get("turn_index") != turn_index
            or turn.get("user") != user_text
            or not isinstance(turn.get("output"), str)
            or not isinstance(turn.get("input_tokens"), int)
            or turn["input_tokens"] < 0
            or not isinstance(turn.get("new_tokens"), int)
            or not 0 <= turn["new_tokens"] <= generation["max_new_tokens"]
            or not isinstance(turn.get("max_token_hit"), bool)
            or not isinstance(turn.get("input_over_budget"), bool)
            or not isinstance(turn.get("elapsed_seconds"), (int, float))
            or turn["elapsed_seconds"] < 0
            or (
                turn["input_over_budget"]
                and (turn["output"] or turn["new_tokens"] != 0)
            )
            or (
                not turn["input_over_budget"]
                and turn["input_tokens"] > generation["max_input_tokens"]
            )
        ):
            raise Mix2KV4EvaluationError(
                f"기존 {arm['arm_id']}/{case['case_id']} turn 계약이 다릅니다."
            )
        expected_grade = grade_turn(
            case,
            turn["output"],
            turn_index=turn_index,
            prior_outputs=prior_outputs,
            max_token_hit=turn["max_token_hit"],
            input_over_budget=turn["input_over_budget"],
            ngram_size=int(generation["minimum_ngram_size"]),
            repetition_threshold=float(
                generation["within_response_repeated_ngram_ratio"]
            ),
        )
        if turn.get("grade") != expected_grade:
            raise Mix2KV4EvaluationError(
                f"기존 {arm['arm_id']}/{case['case_id']} grade가 재계산과 다릅니다."
            )
        prior_outputs.append(turn["output"])
    return dict(item)


def _validate_completed_arm(
    *,
    manifest: Mapping[str, Any],
    arm_root: Path,
    cases: Sequence[Mapping[str, Any]],
    arm: Mapping[str, Any],
    eval_id: str,
    identity: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    validate_directory(arm_root, f"{arm['arm_id']} evaluation root")
    validate_directory(arm_root / "items", f"{arm['arm_id']} evaluation items")
    results_path = arm_root / "results_200.jsonl"
    if (
        manifest.get("evaluation_id") != eval_id
        or manifest.get("evaluation_identity") != identity
        or manifest.get("status") != "arm_generation_completed"
        or manifest.get("completed") is not True
        or manifest.get("arm_id") != arm["arm_id"]
        or manifest.get("arm_artifact_sha256") != arm["artifact_sha256"]
        or manifest.get("cases") != len(cases)
        or manifest.get("turns")
        != sum(1 + len(case["followup_turns"]) for case in cases)
        or manifest.get("results_path") != "results_200.jsonl"
        or results_path.is_symlink()
        or not results_path.is_file()
        or sha256_file(results_path) != manifest.get("results_sha256")
        or manifest.get("same_generation_config") is not True
        or manifest.get("effective_generation") != identity["effective_generation"]
        or manifest.get("effective_generation_sha256")
        != identity["effective_generation_sha256"]
        or manifest.get("correction_retry_performed") is not False
        or manifest.get("model_training_performed") is not False
        or manifest.get("production_promotion_allowed") is not False
        or manifest.get("sealed_blind_accessed") is not False
    ):
        raise Mix2KV4EvaluationError(
            f"기존 {arm['arm_id']} 완료 manifest 계약이 다릅니다."
        )
    try:
        rows = read_jsonl(results_path)
    except Mix2KV4ContractError as exc:
        raise Mix2KV4EvaluationError(
            f"기존 {arm['arm_id']} results를 읽지 못했습니다."
        ) from exc
    generation = {**config["generation"], **config["repetition"]}
    if len(rows) != len(cases):
        raise Mix2KV4EvaluationError(f"기존 {arm['arm_id']} results 행 수가 다릅니다.")
    validated: list[dict[str, Any]] = []
    for case, row in zip(cases, rows, strict=True):
        value = _validate_case_item(
            row,
            case=case,
            arm=arm,
            eval_id=eval_id,
            generation=generation,
        )
        item_path = arm_root / "items" / f"{case['case_id']}.json"
        if load_json(item_path, f"{arm['arm_id']} 완료 case item") != value:
            raise Mix2KV4EvaluationError(
                f"기존 {arm['arm_id']}/{case['case_id']} item과 results가 다릅니다."
            )
        validated.append(value)
    observed_peak_allocated = max(item["peak_allocated_bytes"] for item in validated)
    observed_peak_reserved = max(item["peak_reserved_bytes"] for item in validated)
    manifest_peak_allocated = manifest.get("peak_allocated_bytes")
    manifest_peak_reserved = manifest.get("peak_reserved_bytes")
    if (
        not isinstance(manifest_peak_allocated, int)
        or not isinstance(manifest_peak_reserved, int)
        or manifest_peak_allocated < observed_peak_allocated
        or manifest_peak_reserved < observed_peak_reserved
        or manifest_peak_reserved
        > int(config["operational_limits"]["max_total_gpu_memory_used_mib"])
        * 1024
        * 1024
    ):
        raise Mix2KV4EvaluationError(f"기존 {arm['arm_id']} peak VRAM 계약이 다릅니다.")


def _run_arm_unlocked(
    *,
    config_path: Path,
    spec_build: Path,
    data_build: Path,
    artifact_root: Path,
    arm_id: str,
    execute: bool,
) -> dict[str, Any]:
    (
        config,
        _,
        cases,
        data_manifest,
        arms,
        eval_id,
        identity,
    ) = prepare_evaluation(
        config_path=config_path,
        spec_build=spec_build,
        data_build=data_build,
        artifact_root=artifact_root,
    )
    if arm_id not in arms:
        raise Mix2KV4EvaluationError("평가 arm이 고정 5-arm 계약에 없습니다.")
    target = _evaluation_root(config, artifact_root, eval_id)
    if not execute:
        return {
            "status": "evaluation_dry_run",
            "evaluation_id": eval_id,
            "arm_id": arm_id,
            "cases": len(cases),
            "turns": sum(1 + len(case["followup_turns"]) for case in cases),
            "data_build_id": data_manifest["build_id"],
            "execute_required": True,
            "target": str(target),
        }
    hardware = hardware_gate(config)
    ensure_directory(target, "private evaluation build")
    arm_root = target / "arms" / arm_id
    ensure_directory(arm_root, f"{arm_id} evaluation root")
    lock_descriptor = os.open(
        arm_root / ".evaluation.lock", os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4EvaluationError(
                f"{arm_id} 평가가 이미 실행 중입니다."
            ) from exc
        manifest_path = arm_root / "arm_manifest.json"
        if manifest_path.is_file():
            manifest = load_json(manifest_path, f"{arm_id} 평가 manifest")
            _validate_completed_arm(
                manifest=manifest,
                arm_root=arm_root,
                cases=cases,
                arm=arms[arm_id],
                eval_id=eval_id,
                identity=identity,
                config=config,
            )
            return {**manifest, "mode": "reused", "path": str(arm_root)}
        model_root = Path(str(arms[arm_id]["model_root"]))
        adapter_root = (
            Path(str(arms[arm_id]["adapter_root"]))
            if arms[arm_id]["adapter_root"] is not None
            else None
        )
        items_root = arm_root / "items"
        ensure_directory(items_root, f"{arm_id} evaluation items")
        torch_module: Any = None
        try:
            import torch

            torch_module = torch
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            with LocalArmRunner(
                model_root=model_root,
                adapter_root=adapter_root,
                generation=config["generation"],
            ) as model_runner:
                grading_generation = {
                    **config["generation"],
                    **config["repetition"],
                }
                for index, case in enumerate(cases, 1):
                    item_path = items_root / f"{case['case_id']}.json"
                    if item_path.is_file():
                        item = _validate_case_item(
                            load_json(item_path, f"{arm_id} case item"),
                            case=case,
                            arm=arms[arm_id],
                            eval_id=eval_id,
                            generation=grading_generation,
                        )
                    else:
                        item = _case_item(
                            case=case,
                            arm=arms[arm_id],
                            runner=model_runner,
                            eval_id=eval_id,
                            generation=grading_generation,
                        )
                        item["peak_allocated_bytes"] = int(
                            torch.cuda.max_memory_allocated()
                        )
                        item["peak_reserved_bytes"] = int(
                            torch.cuda.max_memory_reserved()
                        )
                        atomic_write(item_path, json_bytes(item))
                    if (
                        item["peak_reserved_bytes"]
                        > int(
                            config["operational_limits"][
                                "max_total_gpu_memory_used_mib"
                            ]
                        )
                        * 1024
                        * 1024
                    ):
                        raise Mix2KV4EvaluationError(
                            f"{arm_id} case peak VRAM이 16GiB Gate를 넘었습니다."
                        )
                    atomic_write(
                        arm_root / "progress.json",
                        json_bytes(
                            {
                                "schema_version": "1.0.0",
                                "evaluation_id": eval_id,
                                "arm_id": arm_id,
                                "completed_cases": index,
                                "total_cases": len(cases),
                                "updated_at_utc": utc_now(),
                            }
                        ),
                    )
                    print(
                        f"evaluation_id={eval_id} arm={arm_id} case={index}/{len(cases)} "
                        f"case_id={case['case_id']}",
                        flush=True,
                    )
            peak_allocated = int(torch.cuda.max_memory_allocated())
            peak_reserved = int(torch.cuda.max_memory_reserved())
        finally:
            gc.collect()
            if torch_module is not None:
                torch_module.cuda.empty_cache()
                torch_module.cuda.synchronize()
        grading_generation = {**config["generation"], **config["repetition"]}
        items = [
            _validate_case_item(
                load_json(
                    items_root / f"{case['case_id']}.json", f"{arm_id} case item"
                ),
                case=case,
                arm=arms[arm_id],
                eval_id=eval_id,
                generation=grading_generation,
            )
            for case in cases
        ]
        peak_allocated = max(
            peak_allocated, *(item["peak_allocated_bytes"] for item in items)
        )
        peak_reserved = max(
            peak_reserved, *(item["peak_reserved_bytes"] for item in items)
        )
        if (
            peak_reserved
            > int(config["operational_limits"]["max_total_gpu_memory_used_mib"])
            * 1024
            * 1024
        ):
            raise Mix2KV4EvaluationError(
                f"{arm_id} peak VRAM이 16GiB Gate를 넘었습니다."
            )
        results_payload = jsonl_bytes(items)
        results_path = arm_root / "results_200.jsonl"
        atomic_write(results_path, results_payload)
        manifest = {
            "schema_version": "1.0.0",
            "evaluation_id": eval_id,
            "evaluation_identity": identity,
            "status": "arm_generation_completed",
            "completed": True,
            "arm_id": arm_id,
            "arm_kind": arms[arm_id]["kind"],
            "rank": arms[arm_id]["rank"],
            "arm_artifact_sha256": arms[arm_id]["artifact_sha256"],
            "data_build_id": data_manifest["build_id"],
            "cases": len(items),
            "turns": sum(len(item["turns"]) for item in items),
            "results_path": "results_200.jsonl",
            "results_sha256": sha256_bytes(results_payload),
            "hardware_before": hardware,
            "peak_allocated_bytes": peak_allocated,
            "peak_reserved_bytes": peak_reserved,
            "same_generation_config": True,
            "effective_generation": identity["effective_generation"],
            "effective_generation_sha256": identity["effective_generation_sha256"],
            "correction_retry_performed": False,
            "model_training_performed": False,
            "production_promotion_allowed": False,
            "sealed_blind_accessed": False,
            "completed_at_utc": utc_now(),
        }
        atomic_write(manifest_path, json_bytes(manifest))
        return {**manifest, "mode": "created", "path": str(arm_root)}
    finally:
        os.close(lock_descriptor)


def run_arm(
    *,
    config_path: Path,
    spec_build: Path,
    data_build: Path,
    artifact_root: Path,
    arm_id: str,
    execute: bool,
) -> dict[str, Any]:
    _validate_artifact_root(artifact_root)
    if not execute:
        return _run_arm_unlocked(
            config_path=config_path,
            spec_build=spec_build,
            data_build=data_build,
            artifact_root=artifact_root,
            arm_id=arm_id,
            execute=False,
        )
    descriptor = acquire_mix2k_v4_gpu_lock(artifact_root)
    try:
        return _run_arm_unlocked(
            config_path=config_path,
            spec_build=spec_build,
            data_build=data_build,
            artifact_root=artifact_root,
            arm_id=arm_id,
            execute=True,
        )
    finally:
        os.close(descriptor)


def _load_completed_rows(
    *,
    config: Mapping[str, Any],
    artifact_root: Path,
    eval_id: str,
    identity: Mapping[str, Any],
    cases: Sequence[Mapping[str, Any]],
    arms: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    target = _evaluation_root(config, artifact_root, eval_id)
    validate_directory(target, "private evaluation build")
    rows_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm_id in ARM_IDS:
        arm_root = target / "arms" / arm_id
        manifest = load_json(arm_root / "arm_manifest.json", f"{arm_id} 평가 manifest")
        _validate_completed_arm(
            manifest=manifest,
            arm_root=arm_root,
            cases=cases,
            arm=arms[arm_id],
            eval_id=eval_id,
            identity=identity,
            config=config,
        )
        try:
            rows_by_arm[arm_id] = read_jsonl(arm_root / "results_200.jsonl")
        except Mix2KV4ContractError as exc:
            raise Mix2KV4EvaluationError(
                f"{arm_id} 완료 결과를 읽지 못했습니다."
            ) from exc
    return rows_by_arm


def run_review(
    *,
    config_path: Path,
    spec_build: Path,
    data_build: Path,
    artifact_root: Path,
    provider: str,
    execute: bool,
    external_transmission_approved: bool,
) -> dict[str, Any]:
    config, _, cases, _, arms, eval_id, identity = prepare_evaluation(
        config_path=config_path,
        spec_build=spec_build,
        data_build=data_build,
        artifact_root=artifact_root,
    )
    rows_by_arm = _load_completed_rows(
        config=config,
        artifact_root=artifact_root,
        eval_id=eval_id,
        identity=identity,
        cases=cases,
        arms=arms,
    )
    return run_quality_review(
        provider=provider,
        eval_id=eval_id,
        identity=identity,
        cases=cases,
        rows_by_arm=rows_by_arm,
        target_root=_evaluation_root(config, artifact_root, eval_id),
        config=config,
        execute=execute,
        external_transmission_approved=external_transmission_approved,
    )


def _write_once_or_same(path: Path, payload: bytes, *, mode: int) -> str:
    if path.exists():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != mode
            or path.read_bytes() != payload
        ):
            raise Mix2KV4EvaluationError(
                f"기존 평가 보고서가 현재 identity와 다릅니다: {path.name}"
            )
        return "reused"
    atomic_write(path, payload, mode=mode)
    return "created"


def finalize_report(
    *,
    config_path: Path,
    spec_build: Path,
    data_build: Path,
    artifact_root: Path,
    execute: bool,
) -> dict[str, Any]:
    config, _, cases, data_manifest, arms, eval_id, identity = prepare_evaluation(
        config_path=config_path,
        spec_build=spec_build,
        data_build=data_build,
        artifact_root=artifact_root,
    )
    private_root = _evaluation_root(config, artifact_root, eval_id)
    rows_by_arm = _load_completed_rows(
        config=config,
        artifact_root=artifact_root,
        eval_id=eval_id,
        identity=identity,
        cases=cases,
        arms=arms,
    )
    reviews_by_provider = {
        provider: load_quality_reviews(
            provider=provider,
            eval_id=eval_id,
            identity=identity,
            cases=cases,
            rows_by_arm=rows_by_arm,
            target_root=private_root,
            config=config,
        )
        for provider in config["quality_review"]["providers"]
    }
    aggregate = build_aggregate(
        eval_id=eval_id,
        identity=identity,
        rows_by_arm=rows_by_arm,
        reviews_by_provider=reviews_by_provider,
        config=config,
    )
    if aggregate["quality_review"]["reviews_complete"] is not True:
        raise Mix2KV4EvaluationError(
            "Claude·Codex 이중 품질검수가 완성되지 않았습니다."
        )
    if not execute:
        return {
            "status": "final_report_dry_run",
            "evaluation_id": eval_id,
            "candidate_status": aggregate["status"],
            "execute_required": True,
        }
    public_root = artifact_root / config["outputs"]["public_root"] / eval_id
    ensure_directory(public_root, "public evaluation report", mode=0o755)
    aggregate_payload = json_bytes(aggregate)
    aggregate_sha = sha256_bytes(aggregate_payload)
    identity_sha = sha256z(identity)
    public_manifest = {
        "schema_version": "1.0.0",
        "evaluation_id": eval_id,
        "evaluation_identity_sha256": identity_sha,
        "config_sha256": sha256_file(config_path),
        "data_build_id": data_manifest["build_id"],
        "aggregate_sha256": aggregate_sha,
        "status": aggregate["status"],
        "primary_candidate_gate_passed": aggregate["release"][
            "primary_candidate_gate_passed"
        ],
        "production_release_ready": aggregate["release"][
            "production_release_ready"
        ],
        "raw_outputs_included": False,
        "case_ids_included": False,
        "private_paths_included": False,
        "automatic_production_promotion_allowed": False,
    }
    public_manifest_payload = json_bytes(public_manifest)
    aggregate_mode = _write_once_or_same(
        public_root / "aggregate.json", aggregate_payload, mode=0o644
    )
    manifest_mode = _write_once_or_same(
        public_root / "build_manifest.json", public_manifest_payload, mode=0o644
    )
    private_manifest = {
        **public_manifest,
        "arm_results_sha256": {
            arm_id: sha256_file(private_root / "arms" / arm_id / "results_200.jsonl")
            for arm_id in ARM_IDS
        },
        "quality_review_sha256": {
            provider: sha256_file(
                private_root / "reviews" / provider / "reviews_200.json"
            )
            for provider in config["quality_review"]["providers"]
        },
    }
    private_mode = _write_once_or_same(
        private_root / "final_report_manifest.json",
        json_bytes(private_manifest),
        mode=0o600,
    )
    return {
        "status": aggregate["status"],
        "evaluation_id": eval_id,
        "primary_candidate_gate_passed": aggregate["release"][
            "primary_candidate_gate_passed"
        ],
        "production_release_ready": aggregate["release"][
            "production_release_ready"
        ],
        "production_promotion_performed": False,
        "mode": {
            "aggregate": aggregate_mode,
            "public_manifest": manifest_mode,
            "private_manifest": private_mode,
        },
    }


def validate_static_contract(
    *, config_path: Path, spec_build: Path, artifact_root: Path
) -> dict[str, Any]:
    _validate_artifact_root(artifact_root)
    config = validate_config(config_path)
    spec_manifest, cases = validate_dev_cases(spec_build, config)
    lora_config = validate_lora_config(
        REPO_ROOT / config["dependency_contracts"]["lora_config"]["path"]
    )
    runtime_versions = validate_runtime_versions(lora_config)
    k0 = _static_arm("K0", config, artifact_root)
    ki20 = _static_arm("KI20", config, artifact_root)
    return {
        "status": "static_contract_valid",
        "config_sha256": sha256_file(config_path),
        "spec_build_id": spec_manifest["build_id"],
        "dev_rows": len(cases),
        "arms": list(ARM_IDS),
        "static_arm_artifacts": {
            "K0": k0["artifact_sha256"],
            "KI20": ki20["artifact_sha256"],
        },
        "lora_ranks": lora_config["lora"]["ranks"],
        "generation": config["generation"],
        "effective_generation": effective_generation_payload(config["generation"]),
        "quality_review_provider_contracts": config["quality_review"][
            "provider_contracts"
        ],
        "external_review_transmission_contract": config["quality_review"][
            "external_transmission"
        ],
        "runtime_versions": runtime_versions,
        "training_or_promotion_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIX2K v4 frozen dev200 5-arm runner")
    parser.add_argument(
        "command",
        choices=("validate-contract", "run-arm", "run-review", "finalize-report"),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--spec-build", type=Path, default=DEFAULT_SPEC_BUILD)
    parser.add_argument("--data-build", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--arm", choices=ARM_IDS, default="LORA_R16")
    parser.add_argument("--provider", choices=("claude", "codex"), default="codex")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--approve-external-review-transmission",
        action="store_true",
        help=(
            "공개 합성 dev 입력과 5-arm 원문 출력을 subscription reviewer에 "
            "전송하며, 휴리스틱 검사가 KI20 암기 가능성을 배제하지 못함을 승인"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = absolute(args.config)
        spec_build = absolute(args.spec_build)
        artifact_root = absolute(args.artifact_root)
        reject_symlink_components(config_path, "evaluation config")
        reject_symlink_components(artifact_root, "artifact root")
        if args.command == "validate-contract":
            report = validate_static_contract(
                config_path=config_path,
                spec_build=spec_build,
                artifact_root=artifact_root,
            )
        elif args.command == "run-arm":
            if args.data_build is None:
                raise Mix2KV4EvaluationError("run-arm에는 --data-build가 필요합니다.")
            report = run_arm(
                config_path=config_path,
                spec_build=spec_build,
                data_build=absolute(args.data_build),
                artifact_root=artifact_root,
                arm_id=args.arm,
                execute=args.execute,
            )
        elif args.command == "run-review":
            if args.data_build is None:
                raise Mix2KV4EvaluationError(
                    "run-review에는 --data-build가 필요합니다."
                )
            report = run_review(
                config_path=config_path,
                spec_build=spec_build,
                data_build=absolute(args.data_build),
                artifact_root=artifact_root,
                provider=args.provider,
                execute=args.execute,
                external_transmission_approved=(
                    args.approve_external_review_transmission
                ),
            )
        else:
            if args.data_build is None:
                raise Mix2KV4EvaluationError(
                    "finalize-report에는 --data-build가 필요합니다."
                )
            report = finalize_report(
                config_path=config_path,
                spec_build=spec_build,
                data_build=absolute(args.data_build),
                artifact_root=artifact_root,
                execute=args.execute,
            )
    except (Mix2KV4EvaluationError, Mix2KV4ContractError, Mix2KV4LoRAError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
