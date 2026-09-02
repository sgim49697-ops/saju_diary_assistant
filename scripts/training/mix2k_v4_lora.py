# mix2k_v4_lora.py - K0 기반 MIX2K v4 LoRA rank를 사전 검증·학습·재개한다.

from __future__ import annotations

import argparse
import fcntl
import gc
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    EXPECTED_ROWS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    RUNTIME_AXES,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from scripts.data.mix2k_v4_teachers import (
    Mix2KV4TeacherError,
    _validate_spec_build,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/model_versions/saju_1b_baseline/mix2k-v4-lora-v1.0.1.json"
)
DEFAULT_MODEL_SNAPSHOT = REPO_ROOT / (
    "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
    "bf4786aa2a1908adce942d53976270132732f720"
)
PREFLIGHT_SUBDIR = Path(
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/lora-preflight/v1.0.1"
)
GPU_LOCK_NAMESPACE = Path("saju-diary-assistant/mix2k-v4-gpu/v1.0.0")
SCRIPT_PATH = Path(__file__).resolve()
DATA_CONFIG_PATH = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/mix2k-v4-chart-day-8k-v1.0.1.json"
)
FINALIZER_PATH = REPO_ROOT / "scripts/data/mix2k_v4_finalize.py"
DATA_CONTRACTS_PATH = REPO_ROOT / "scripts/data/mix2k_v4_contracts.py"
BOUND_PROMPT_PATH = REPO_ROOT / "configs/chat_prompts/saju_bound_chart_v2.txt"
SPEC_BUILD_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/specs/v1.0.1"
)
EXPECTED_DATA_CONFIG_SHA256 = (
    "c8267ec438e1bebe46670553a846fa81db371d23c5004a3a3c2aeecafe440f1c"
)
MAX_JSON_BYTES = 64 * 1024 * 1024
TRAIN_ROW_FIELDS = {
    "schema_version",
    "dataset_version",
    "id",
    "task_axis",
    "messages",
    "assistant_only_loss",
    "runtime_snapshot_sha256",
    "restricted_local_only",
}
TOKEN_AUDIT_ROW_FIELDS = {
    "id",
    "task_axis",
    "rendered_tokens",
    "prompt_tokens",
    "supervised_assistant_tokens",
    "truncated",
    "assistant_mask_nonzero",
    "assistant_mask_sha256",
    "final_eos_supervised",
    "user_system_loss_leakage_tokens",
}
TRAINING_METRIC_FIELDS = {
    "global_step",
    "training_loss",
    "elapsed_seconds",
    "trainable_parameters",
    "target_linear_modules",
    "total_parameters_with_adapter",
    "trainable_ratio",
    "gradient",
    "peak_allocated_bytes",
    "peak_reserved_bytes",
    "adapter_model_sha256",
    "adapter_config_sha256",
    "adapter_reload_rank",
    "adapter_reload_match",
}
GRADIENT_METRIC_FIELDS = {
    "called",
    "lora_gradient_finite",
    "lora_gradient_nonzero",
    "base_gradient_absent",
}
PREFLIGHT_MANIFEST_FIELDS = {
    "schema_version",
    "status",
    "passed",
    "rank",
    "run_id",
    "identity",
    "runtime_versions",
    "data_build_id",
    "max_length",
    "rows",
    "hardware_before",
    "metrics",
    "base_weights_unchanged",
    "full_fine_tuning_performed",
    "ki20_training_performed",
    "training_performed",
    "production_promotion_allowed",
    "sealed_blind_accessed",
    "completed_at_utc",
}
TRAINING_MANIFEST_FIELDS = {
    "schema_version",
    "status",
    "completed",
    "rank",
    "run_id",
    "identity",
    "runtime_versions",
    "data_build_id",
    "preflight_run_id",
    "max_length",
    "rows",
    "hardware_before",
    "metrics",
    "base_weights_unchanged",
    "adapter_only",
    "num_train_epochs",
    "full_fine_tuning_performed",
    "ki20_training_performed",
    "production_promotion_allowed",
    "sealed_blind_accessed",
    "completed_at_utc",
}
REQUIRED_RESUME_CHECKPOINT_FILES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "optimizer.pt",
        "scheduler.pt",
        "trainer_state.json",
    }
)


class Mix2KV4LoRAError(RuntimeError):
    """MIX2K v4 LoRA 계약·GPU gate·adapter 검증 실패."""


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _runtime_hash_matches_axis(axis: Any, runtime_hash: Any) -> bool:
    """필수·선택·금지 runtime 축의 final-row hash 계약을 구분한다."""

    valid_hash = _is_sha256(runtime_hash)
    if axis in RUNTIME_AXES:
        return valid_hash
    if axis == "uncertainty_blocked_boundary":
        return runtime_hash is None or valid_hash
    return runtime_hash is None


def _training_row_matches_spec(
    row: Mapping[str, Any], spec: Mapping[str, Any]
) -> bool:
    """final row를 frozen spec의 prompt·axis·runtime snapshot 값에 묶는다."""

    messages = row.get("messages")
    prompt = spec.get("prompt")
    binding = spec.get("runtime_binding")
    expected_runtime_hash = (
        binding.get("snapshot_sha256") if isinstance(binding, Mapping) else None
    )
    return (
        isinstance(messages, list)
        and len(messages) >= 1
        and isinstance(prompt, list)
        and messages[:-1] == prompt
        and row.get("task_axis") == spec.get("task_axis")
        and row.get("runtime_snapshot_sha256") == expected_runtime_hash
    )


def _valid_token_audit_row(row: Any, max_length: int) -> bool:
    """손상된 token audit 값도 예외 없이 fail-closed로 거부한다."""

    if not isinstance(row, dict) or set(row) != TOKEN_AUDIT_ROW_FIELDS:
        return False
    integer_fields = (
        "rendered_tokens",
        "prompt_tokens",
        "supervised_assistant_tokens",
        "user_system_loss_leakage_tokens",
    )
    if any(
        isinstance(row.get(field), bool) or not isinstance(row.get(field), int)
        for field in integer_fields
    ):
        return False
    rendered = row["rendered_tokens"]
    prompt = row["prompt_tokens"]
    supervised = row["supervised_assistant_tokens"]
    return (
        isinstance(row.get("id"), str)
        and bool(row["id"])
        and row.get("task_axis") in EXPECTED_AXES
        and 1 <= rendered <= max_length
        and prompt >= 1
        and supervised >= 1
        and rendered == prompt + supervised
        and row.get("truncated") is False
        and row.get("assistant_mask_nonzero") is True
        and _is_sha256(row.get("assistant_mask_sha256"))
        and row.get("final_eos_supervised") is True
        and row.get("user_system_loss_leakage_tokens") == 0
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4LoRAError(f"{label} 경로에 symlink component가 있습니다.")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    _reject_symlink_components(path, label)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_JSON_BYTES
    ):
        raise Mix2KV4LoRAError(f"{label}이 없거나 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4LoRAError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4LoRAError(f"{label} 최상위는 object여야 합니다.")
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _atomic_write(path: Path, payload: bytes, mode: int = PRIVATE_FILE_MODE) -> None:
    if not path.is_absolute():
        raise Mix2KV4LoRAError("LoRA output file은 절대경로여야 합니다.")
    _reject_symlink_components(path, "LoRA output file")
    _ensure_private_directory(path.parent, "LoRA output parent")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise Mix2KV4LoRAError("기존 LoRA output file이 안전하지 않습니다.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validate_directory(path: Path, label: str) -> None:
    _reject_symlink_components(path, label)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise Mix2KV4LoRAError(f"{label} 경로가 없거나 안전하지 않습니다.")


def _ensure_private_directory(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise Mix2KV4LoRAError(f"{label}은 절대경로여야 합니다.")
    _reject_symlink_components(path, label)
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise Mix2KV4LoRAError(f"{label} 경로가 안전하지 않습니다.")
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _reject_symlink_components(path, label)
    path.chmod(PRIVATE_DIR_MODE)


def acquire_mix2k_v4_gpu_lock(artifact_root: Path) -> int:
    """모든 worktree의 학습·preflight·평가가 공유하는 GPU nonblocking lock."""

    _validate_directory(artifact_root, "artifact root")
    uid = os.getuid()
    runtime_root = Path(f"/run/user/{uid}")
    if (
        runtime_root.is_absolute()
        and runtime_root.is_dir()
        and not runtime_root.is_symlink()
        and runtime_root.stat().st_uid == uid
    ):
        lock_root = runtime_root / GPU_LOCK_NAMESPACE
    else:
        lock_root = (
            Path(tempfile.gettempdir())
            / f"saju-diary-assistant-{uid}"
            / "mix2k-v4-gpu/v1.0.0"
        )
    _ensure_private_directory(lock_root, "MIX2K v4 GPU lock root")
    if lock_root.stat().st_uid != uid:
        raise Mix2KV4LoRAError(
            "MIX2K v4 GPU lock root 소유자가 현재 사용자와 다릅니다."
        )
    lock_path = lock_root / ".gpu-global.lock"
    _reject_symlink_components(lock_path, "MIX2K v4 GPU lock")
    descriptor = os.open(
        lock_path,
        os.O_CREAT
        | os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise Mix2KV4LoRAError(
            "다른 MIX2K v4 학습·preflight·평가가 GPU를 사용 중입니다."
        ) from exc
    return descriptor


def _validated_resume_checkpoint(
    *,
    target: Path,
    trainer_root: Path,
    identity: Mapping[str, Any],
    rank: int,
    run_id: str,
    expected_steps: int,
) -> str | None:
    """현재 run identity에 묶인 미완료 checkpoint만 fail-closed로 선택한다."""

    checkpoint_candidates = [
        path
        for path in trainer_root.glob("checkpoint-*")
        if path.name.removeprefix("checkpoint-").isdigit()
    ]
    state_path = target / "training_state.json"
    state = _load_json(state_path, "LoRA running state") if state_path.is_file() else None
    if state is not None and (
        state.get("schema_version") != "1.0.0"
        or state.get("status") != "running"
        or state.get("rank") != rank
        or state.get("run_id") != run_id
        or state.get("identity") != identity
        or not isinstance(state.get("started_at_utc"), str)
    ):
        raise Mix2KV4LoRAError("기존 LoRA running state identity가 다릅니다.")
    if checkpoint_candidates and state is None:
        raise Mix2KV4LoRAError(
            "LoRA checkpoint lineage를 증명할 running state가 없습니다."
        )

    checkpoints: list[tuple[int, Path]] = []
    for path in checkpoint_candidates:
        _reject_symlink_components(path, "LoRA checkpoint")
        if path.is_symlink() or not path.is_dir():
            raise Mix2KV4LoRAError("LoRA checkpoint 경로가 안전하지 않습니다.")
        step = int(path.name.removeprefix("checkpoint-"))
        if step < 1 or step > expected_steps:
            raise Mix2KV4LoRAError("LoRA checkpoint optimizer step이 범위를 벗어났습니다.")
        present = {item.name for item in path.iterdir() if item.is_file()}
        missing = REQUIRED_RESUME_CHECKPOINT_FILES - present
        if missing:
            raise Mix2KV4LoRAError(
                "LoRA checkpoint 필수 파일이 없습니다: " + ", ".join(sorted(missing))
            )
        for name in REQUIRED_RESUME_CHECKPOINT_FILES:
            file_path = path / name
            if file_path.is_symlink() or file_path.stat().st_size < 1:
                raise Mix2KV4LoRAError("LoRA checkpoint 파일이 안전하지 않습니다.")
        trainer_state = _load_json(path / "trainer_state.json", "LoRA trainer state")
        if (
            isinstance(trainer_state.get("global_step"), bool)
            or trainer_state.get("global_step") != step
            or trainer_state.get("max_steps") != expected_steps
        ):
            raise Mix2KV4LoRAError("LoRA checkpoint trainer step 계약이 다릅니다.")
        adapter_config = _load_json(path / "adapter_config.json", "LoRA adapter config")
        if (
            adapter_config.get("peft_type") != "LORA"
            or adapter_config.get("r") != rank
            or adapter_config.get("use_rslora") is not True
        ):
            raise Mix2KV4LoRAError("LoRA checkpoint adapter rank·type 계약이 다릅니다.")
        checkpoints.append((step, path))

    if not checkpoints:
        return None
    step, latest = max(checkpoints, key=lambda item: item[0])
    if step == expected_steps:
        raise Mix2KV4LoRAError(
            "최종 optimizer step checkpoint만 남은 run은 gradient audit을 재현할 수 없어 "
            "자동 resume하지 않습니다. 별도 복구 검수가 필요합니다."
        )
    return str(latest)


def _validate_config(path: Path) -> dict[str, Any]:
    config = _load_json(path, "LoRA config")
    model = config.get("base_model")
    data = config.get("required_data")
    serving = config.get("serving_contract")
    runtime = config.get("runtime_versions")
    lora = config.get("lora")
    training = config.get("training")
    preflight = config.get("preflight")
    governance = config.get("governance")
    expected_model = {
        "repository": "kakaocorp/kanana-2-1.3b-instruct",
        "revision": "bf4786aa2a1908adce942d53976270132732f720",
        "local_subdir": (
            "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
            "bf4786aa2a1908adce942d53976270132732f720"
        ),
        "parameter_count": 1_291_478_272,
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "local_files_only": True,
        "trust_remote_code": True,
        "files": {
            "model.safetensors": "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942",
            "configuration_kanana2_tiny.py": "191fb6fbfd63968cc24b3beeb8190aaa88868d4cf1695f8c5a379fb0a077d79d",
            "modeling_kanana2_tiny.py": "e47cd8cc99e71fc69eea9bf5ba1221526fb8c6d4fc8677177e82de997b766500",
            "tokenizer.json": "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab",
            "tokenizer_config.json": "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5",
            "chat_template.jinja": "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3",
            "config.json": "fe14b20b4b616d62ca0682312c2fcd2b90d9a836d14a1ff6448db3f533fd15a1",
        },
    }
    expected_data = {
        "rows": 2000,
        "spec_build_id": "build-59d68bc841a0",
        "spec_build_sha256": (
            "59d68bc841a02e366711045383ebea0f37be138244e0e213fe7eb15bfa109826"
        ),
        "manifest_name": "build_manifest.json",
        "training_path": "training/train_2000.jsonl",
        "token_audit_path": "reports/token_audit_summary.json",
        "training_execution_allowed": True,
        "full_runtime_snapshot_used": True,
        "compact_projection_used_for_training": False,
        "development_targets_accessed": False,
        "truncation": False,
        "assistant_only_loss": True,
        "allowed_max_lengths": [2048, 3584],
        "larger_context_requires_separate_preflight": True,
    }
    expected_training = {
        "max_length_source": "required_data_manifest",
        "max_length_floor": 2048,
        "pad_to_multiple_of": 8,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "effective_batch_size": 8,
        "num_train_epochs": 1,
        "expected_optimizer_steps": 250,
        "learning_rate": 0.00005,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
        "optim": "adamw_torch_fused",
        "gradient_checkpointing": True,
        "gradient_checkpointing_use_reentrant": False,
        "assistant_only_loss": True,
        "packing": False,
        "padding_free": False,
        "loss_type": "chunked_nll",
        "bf16": True,
        "fp16": False,
        "tf32": False,
        "use_cache": False,
        "logging_steps": 5,
        "logging_nan_inf_filter": False,
        "save_steps": 50,
        "save_total_limit": 2,
        "save_only_model": False,
        "save_safetensors": True,
        "dataloader_num_workers": 0,
        "seed": 42,
        "data_seed": 42,
        "torch_compile": False,
        "report_to": [],
    }
    expected_preflight = {
        "required_for_each_rank": True,
        "longest_rows": 8,
        "optimizer_steps": 1,
        "require_finite_loss": True,
        "require_finite_nonzero_lora_gradient": True,
        "require_base_gradient_absent": True,
        "require_adapter_reload_match": True,
        "require_peak_memory_within_limit": True,
    }
    expected_governance = {
        "explicit_user_confirmation_received": True,
        "confirmation_date": "2026-09-02",
        "confirmation_scope": "k0_mix2k_v4_lora_r8_r16_r32_one_epoch",
        "ki20_training_allowed": False,
        "full_fine_tuning_allowed": False,
        "partial_fine_tuning_allowed": False,
        "production_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("experiment_id") != "K0-MIX2K-V4-LORA"
        or config.get("training_version") != "v1.0.1"
        or config.get("dataset_version") != DATASET_VERSION
        or model != expected_model
        or data != expected_data
        or serving
        != {
            "model_projection_id": "saju-chart-day-model-projection-v1.0.0",
            "required_prompt_profile": "bound_chart_v2",
            "required_prompt_path": "configs/chat_prompts/saju_bound_chart_v2.txt",
            "required_prompt_sha256": (
                "d93a8f03a45697dbf5df2d78eaa4dde480f5bef70f30a148a0c146576406e917"
            ),
            "current_dashboard_version": "v1.11",
            "current_dashboard_prompt_profile": "bound_chart_v1",
            "prompt_upgrade_completed": False,
            "must_pass_before_production_release": True,
        }
        or runtime
        != {
            "python": "3.10",
            "torch": "2.13.0+cu130",
            "torch_cuda": "13.0",
            "transformers": "4.57.6",
            "trl": "1.12.0",
            "datasets": "4.7.0",
            "accelerate": "1.14.0",
            "bitsandbytes": "0.50.2",
            "peft": "0.20.0",
            "base_lock": {
                "path": "requirements-phase3.lock.txt",
                "sha256": "0301de92dea4a21eb8077abb01aae6eaf412590ac670e436bcd5d7b3717b8aed",
            },
            "lora_overlay": {
                "path": "requirements-lora-v1.0.txt",
                "sha256": "467334a76497e7f691e6e9848dc9b7dbe23f622470c1db0a6f3e953fbbd3fe10",
            },
        }
        or lora
        != {
            "ranks": [8, 16, 32],
            "primary_rank": 16,
            "target_modules": "all-linear",
            "use_rslora": True,
            "bias": "none",
            "lora_dropout": 0.05,
            "lora_alpha": 32,
            "alpha_policy": "fixed_across_rank_ablation",
            "task_type": "CAUSAL_LM",
            "modules_to_save": None,
            "expected_target_linear_modules": 224,
            "expected_trainable_parameters": {
                "8": 9_338_880,
                "16": 18_677_760,
                "32": 37_355_520,
            },
            "base_weights_frozen": True,
            "adapter_only_save": True,
        }
        or training != expected_training
        or preflight != expected_preflight
        or config.get("operational_limits")
        != {
            "expected_gpu_count": 1,
            "max_total_gpu_memory_used_mib": 16384,
            "min_free_gpu_memory_before_start_mib": 12000,
            "min_system_ram_available_bytes": 4294967296,
            "min_disk_available_bytes": 32212254720,
            "require_no_active_compute_process_before_start": True,
            "run_ranks_sequentially": True,
        }
        or config.get("evaluation")
        != {
            "frozen_dev_rows": 200,
            "arms": ["K0", "LORA_R8", "LORA_R16", "LORA_R32", "KI20"],
            "primary_arm": "LORA_R16",
            "same_runtime_snapshot": True,
            "same_system_prompt": True,
            "same_generation_config": True,
            "loss_only_selection_forbidden": True,
            "actual_regression_is_release_blocker": True,
        }
        or governance != expected_governance
        or config.get("outputs")
        != {
            "run_root": "runs/K0-MIX2K-V4-LORA/v1.0.1/{rank}/{run_build_id}",
            "report_root": "data/reports/saju_1b_baseline/mix2k-v4-lora/v1.0.1/{rank}/{run_build_id}",
        }
    ):
        raise Mix2KV4LoRAError("K0 MIX2K v4 LoRA 고정 계약이 다릅니다.")
    for item in (runtime["base_lock"], runtime["lora_overlay"]):
        file_path = REPO_ROOT / item["path"]
        if (
            file_path.is_symlink()
            or not file_path.is_file()
            or sha256_file(file_path) != item["sha256"]
        ):
            raise Mix2KV4LoRAError("LoRA package lock hash가 다릅니다.")
    if sha256_file(BOUND_PROMPT_PATH) != serving["required_prompt_sha256"]:
        raise Mix2KV4LoRAError("LoRA serving prompt hash가 다릅니다.")
    if set(model.get("files", {})) != {
        "model.safetensors",
        "configuration_kanana2_tiny.py",
        "modeling_kanana2_tiny.py",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "config.json",
    }:
        raise Mix2KV4LoRAError("K0 base model file 계약이 다릅니다.")
    return config


def _validate_model_snapshot(
    config: Mapping[str, Any], model_root: Path
) -> dict[str, str]:
    model = config["base_model"]
    _reject_symlink_components(model_root, "K0 base model snapshot")
    if (
        not model_root.is_absolute()
        or model_root.is_symlink()
        or not model_root.is_dir()
        or model_root.name != model["revision"]
    ):
        raise Mix2KV4LoRAError("K0 base model snapshot이 없거나 identity가 다릅니다.")
    observed: dict[str, str] = {}
    for name, expected in model["files"].items():
        file_path = model_root / name
        if file_path.is_symlink() or not file_path.is_file():
            raise Mix2KV4LoRAError(f"K0 base model 파일이 없거나 symlink입니다: {name}")
        observed[name] = sha256_file(file_path)
        if observed[name] != expected:
            raise Mix2KV4LoRAError(f"K0 base model hash가 다릅니다: {name}")
    return observed


def _runtime_versions(config: Mapping[str, Any]) -> dict[str, str]:
    try:
        import torch
        import transformers
    except Exception as exc:
        raise Mix2KV4LoRAError("PyTorch·Transformers import가 실패했습니다.") from exc
    observed = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "torch": torch.__version__,
        "torch_cuda": str(torch.version.cuda),
        "transformers": transformers.__version__,
    }
    for name in ("trl", "datasets", "accelerate", "bitsandbytes", "peft"):
        try:
            observed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise Mix2KV4LoRAError(f"필수 package가 없습니다: {name}") from exc
    expected = {
        key: value
        for key, value in config["runtime_versions"].items()
        if isinstance(value, str)
    }
    if observed != expected:
        raise Mix2KV4LoRAError(
            "LoRA runtime version이 다릅니다: "
            + json.dumps(observed, ensure_ascii=False, sort_keys=True)
        )
    return observed


def _validate_data_build(
    data_build: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    _reject_symlink_components(data_build, "final data build")
    if (
        not data_build.is_absolute()
        or data_build.is_symlink()
        or not data_build.is_dir()
    ):
        raise Mix2KV4LoRAError("final data build가 없거나 symlink입니다.")
    required = config["required_data"]
    manifest_path = data_build / required["manifest_name"]
    manifest = _load_json(manifest_path, "final data manifest")
    data_config = _load_json(DATA_CONFIG_PATH, "MIX2K v4 data config")
    spec_build = SPEC_BUILD_ROOT / required["spec_build_id"]
    try:
        _, spec_manifest, specs = _validate_spec_build(spec_build, DATA_CONFIG_PATH)
    except Mix2KV4TeacherError as exc:
        raise Mix2KV4LoRAError("frozen training spec 계약이 다릅니다.") from exc
    if spec_manifest.get("build_sha256") != required["spec_build_sha256"]:
        raise Mix2KV4LoRAError("frozen training spec hash가 다릅니다.")
    specs_by_id = {spec["id"]: spec for spec in specs}
    data_base_model = data_config.get("base_model")
    data_base_files = (
        data_base_model.get("files") if isinstance(data_base_model, Mapping) else None
    )
    identity = manifest.get("identity")
    identity_fields = {
        "dataset_version",
        "config_sha256",
        "spec_build_sha256",
        "teacher_candidate_sha256",
        "teacher_manifest_sha256",
        "finalizer_sha256",
        "contracts_sha256",
        "base_model_files",
        "training_rows_sha256",
        "token_audit_rows_sha256",
        "token_audit_summary_sha256",
    }
    if (
        not isinstance(identity, Mapping)
        or set(identity) != identity_fields
        or identity.get("dataset_version") != DATASET_VERSION
        or identity.get("config_sha256") != EXPECTED_DATA_CONFIG_SHA256
        or identity.get("spec_build_sha256") != required["spec_build_sha256"]
        or sha256_file(DATA_CONFIG_PATH) != EXPECTED_DATA_CONFIG_SHA256
        or data_config.get("dataset_version") != DATASET_VERSION
        or not isinstance(data_base_files, Mapping)
        or identity.get("finalizer_sha256") != sha256_file(FINALIZER_PATH)
        or identity.get("contracts_sha256") != sha256_file(DATA_CONTRACTS_PATH)
        or identity.get("base_model_files") != data_base_files
        or any(
            not isinstance(identity.get(key), str)
            or len(identity[key]) != 64
            or any(character not in "0123456789abcdef" for character in identity[key])
            for key in (
                "spec_build_sha256",
                "teacher_candidate_sha256",
                "teacher_manifest_sha256",
                "training_rows_sha256",
                "token_audit_rows_sha256",
                "token_audit_summary_sha256",
            )
        )
    ):
        raise Mix2KV4LoRAError("final data identity hash chain이 다릅니다.")
    calculated_build_sha = sha256_bytes(canonical_json_bytes(identity))
    max_length = manifest.get("selected_max_length")
    if (
        manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("build_id") != data_build.name
        or manifest.get("build_id") != f"build-{calculated_build_sha[:12]}"
        or manifest.get("build_sha256") != calculated_build_sha
        or manifest.get("rows") != EXPECTED_ROWS
        or manifest.get("training_execution_allowed") is not True
        or manifest.get("full_runtime_snapshot_used") is not True
        or manifest.get("compact_projection_used_for_training") is not False
        or manifest.get("development_targets_accessed") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("truncation") is not False
        or manifest.get("assistant_only_loss") is not True
        or max_length not in required["allowed_max_lengths"]
    ):
        raise Mix2KV4LoRAError("final data build 학습 승인 계약이 다릅니다.")
    training_path = data_build / required["training_path"]
    audit_path = data_build / required["token_audit_path"]
    audit_rows_path = data_build / "reports/token_audit_2000.jsonl"
    artifact_sha = manifest.get("artifact_sha256")
    expected_artifact_paths = {
        required["training_path"],
        required["token_audit_path"],
        "reports/token_audit_2000.jsonl",
    }
    if not isinstance(artifact_sha, Mapping) or set(artifact_sha) != expected_artifact_paths:
        raise Mix2KV4LoRAError("final data artifact hash map이 다릅니다.")
    for path, relative in (
        (training_path, required["training_path"]),
        (audit_path, required["token_audit_path"]),
        (audit_rows_path, "reports/token_audit_2000.jsonl"),
    ):
        _reject_symlink_components(path, f"final data artifact {relative}")
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != artifact_sha.get(relative)
            or sha256_file(path)
            != identity[
                {
                    required["training_path"]: "training_rows_sha256",
                    required["token_audit_path"]: "token_audit_summary_sha256",
                    "reports/token_audit_2000.jsonl": "token_audit_rows_sha256",
                }[relative]
            ]
        ):
            raise Mix2KV4LoRAError(f"final data artifact hash가 다릅니다: {relative}")
    audit = _load_json(audit_path, "token audit summary")
    if (
        audit.get("rows") != EXPECTED_ROWS
        or audit.get("selected_max_length") != max_length
        or audit.get("truncated_rows") != 0
        or audit.get("zero_assistant_mask_rows") != 0
        or audit.get("missing_supervised_eos_rows") != 0
        or audit.get("user_system_loss_leakage_rows") != 0
        or audit.get("training_blocked_pending_projection_review") is not False
    ):
        raise Mix2KV4LoRAError("token audit summary가 학습 가능 상태가 아닙니다.")
    rows = read_jsonl(training_path)
    token_rows = read_jsonl(audit_rows_path)
    if len(rows) != EXPECTED_ROWS or len(token_rows) != EXPECTED_ROWS:
        raise Mix2KV4LoRAError("final training·token audit가 2,000행이 아닙니다.")
    token_by_id: dict[str, dict[str, Any]] = {}
    for token_row in token_rows:
        if not _valid_token_audit_row(token_row, max_length):
            token_id = token_row.get("id") if isinstance(token_row, Mapping) else None
            raise Mix2KV4LoRAError(
                f"token audit row 계약이 다릅니다: {token_id}"
            )
        token_id = token_row["id"]
        if token_id in token_by_id:
            raise Mix2KV4LoRAError(f"token audit ID가 중복됐습니다: {token_id}")
        token_by_id[token_id] = token_row
    ids: set[str] = set()
    axes: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise Mix2KV4LoRAError("final training row는 object여야 합니다.")
        record_id = row.get("id")
        if not isinstance(record_id, str):
            raise Mix2KV4LoRAError("final training row ID는 문자열이어야 합니다.")
        messages = row.get("messages")
        roles = (
            [message.get("role") for message in messages]
            if isinstance(messages, list)
            and all(isinstance(message, Mapping) for message in messages)
            else []
        )
        expected_roles = ["system"] + [
            role for _ in range((len(roles) - 1) // 2) for role in ("user", "assistant")
        ]
        runtime_hash = row.get("runtime_snapshot_sha256")
        spec = specs_by_id.get(record_id)
        token_row = token_by_id.get(record_id)
        if (
            set(row) != TRAIN_ROW_FIELDS
            or row.get("schema_version") != "1.0.0"
            or row.get("dataset_version") != DATASET_VERSION
            or record_id in ids
            or row.get("task_axis") not in EXPECTED_AXES
            or row.get("assistant_only_loss") is not True
            or row.get("restricted_local_only") is not False
            or not isinstance(messages, list)
            or len(messages) < 3
            or len(messages) % 2 != 1
            or roles != expected_roles
            or any(
                set(message) != {"role", "content"}
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
                for message in messages
            )
            or not _runtime_hash_matches_axis(row.get("task_axis"), runtime_hash)
            or spec is None
            or not _training_row_matches_spec(row, spec)
            or token_row is None
            or token_row.get("task_axis") != row.get("task_axis")
        ):
            raise Mix2KV4LoRAError(f"final training row 계약이 다릅니다: {record_id}")
        ids.add(record_id)
        axes[row["task_axis"]] += 1
    if (
        dict(axes) != EXPECTED_AXES
        or ids != set(specs_by_id)
        or set(token_by_id) != ids
    ):
        raise Mix2KV4LoRAError("final training axis·token ID 집합이 다릅니다.")
    return manifest, rows, token_rows


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
        raise Mix2KV4LoRAError("단일 GPU 상태를 확인하지 못했습니다.")
    fields = [value.strip() for value in lines[0].split(",")]
    if len(fields) != 7:
        raise Mix2KV4LoRAError("GPU 상태 field 수가 다릅니다.")
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
        raise Mix2KV4LoRAError("GPU 상태 숫자 field가 잘못됐습니다.") from exc


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
        raise Mix2KV4LoRAError("GPU compute process 조회가 실패했습니다.")
    processes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = [value.strip() for value in line.split(",")]
        if len(fields) != 3:
            raise Mix2KV4LoRAError("GPU compute process field 수가 다릅니다.")
        try:
            processes.append(
                {
                    "pid": int(fields[0]),
                    "process_name": fields[1],
                    "used_gpu_memory_mib": int(fields[2]),
                }
            )
        except ValueError as exc:
            raise Mix2KV4LoRAError(
                "GPU compute process 숫자 field가 잘못됐습니다."
            ) from exc
    return processes


def _available_ram_bytes() -> int:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, raw = line.split(":", 1)
                parts = raw.strip().split()
                if parts and parts[0].isdigit():
                    values[key] = int(parts[0]) * 1024
    except (OSError, UnicodeError) as exc:
        raise Mix2KV4LoRAError("사용 가능 RAM을 확인하지 못했습니다.") from exc
    if "MemAvailable" not in values:
        raise Mix2KV4LoRAError("MemAvailable이 없습니다.")
    return values["MemAvailable"]


def _hardware_gate(config: Mapping[str, Any], output_root: Path) -> dict[str, Any]:
    try:
        import torch
    except Exception as exc:
        raise Mix2KV4LoRAError("PyTorch import가 실패했습니다.") from exc
    limits = config["operational_limits"]
    gpu = _gpu_snapshot()
    processes = [item for item in _compute_processes() if item["pid"] != os.getpid()]
    _ensure_private_directory(output_root, "LoRA hardware output root")
    ram = _available_ram_bytes()
    disk = shutil.disk_usage(output_root).free
    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != limits["expected_gpu_count"]
        or not torch.cuda.is_bf16_supported()
        or gpu["used_mib"] > limits["max_total_gpu_memory_used_mib"]
        or gpu["free_mib"] < limits["min_free_gpu_memory_before_start_mib"]
        or ram < limits["min_system_ram_available_bytes"]
        or disk < limits["min_disk_available_bytes"]
        or (limits["require_no_active_compute_process_before_start"] and processes)
    ):
        raise Mix2KV4LoRAError(
            "LoRA GPU·RAM·disk·compute process gate를 통과하지 못했습니다."
        )
    return {
        "gpu": gpu,
        "active_compute_processes": processes,
        "available_ram_bytes": ram,
        "available_disk_bytes": disk,
        "bf16_supported": True,
    }


def _run_identity(
    *, mode: str, config_path: Path, data_manifest: Mapping[str, Any], rank: int
) -> tuple[str, dict[str, Any]]:
    identity = {
        "mode": mode,
        "config_sha256": sha256_file(config_path),
        "data_build_sha256": data_manifest["build_sha256"],
        "script_sha256": sha256_file(SCRIPT_PATH),
        "rank": rank,
    }
    digest = sha256_bytes(canonical_json_bytes(identity))
    return f"{mode}-{digest[:12]}", identity


def _training_args(
    sft_config: Any,
    *,
    output_dir: Path,
    config: Mapping[str, Any],
    max_length: int,
    preflight: bool,
) -> Any:
    training = config["training"]
    return sft_config(
        output_dir=str(output_dir),
        overwrite_output_dir=False,
        do_train=True,
        do_eval=False,
        per_device_train_batch_size=training["per_device_train_batch_size"],
        gradient_accumulation_steps=training["gradient_accumulation_steps"],
        num_train_epochs=training["num_train_epochs"],
        max_steps=config["preflight"]["optimizer_steps"] if preflight else -1,
        learning_rate=training["learning_rate"],
        weight_decay=training["weight_decay"],
        max_grad_norm=training["max_grad_norm"],
        warmup_ratio=0.0 if preflight else training["warmup_ratio"],
        lr_scheduler_type=training["lr_scheduler_type"],
        optim=training["optim"],
        logging_strategy="steps",
        logging_steps=1 if preflight else training["logging_steps"],
        logging_nan_inf_filter=training["logging_nan_inf_filter"],
        save_strategy="no" if preflight else "steps",
        save_steps=training["save_steps"],
        save_total_limit=training["save_total_limit"],
        save_only_model=training["save_only_model"],
        save_safetensors=training["save_safetensors"],
        seed=training["seed"],
        data_seed=training["data_seed"],
        bf16=training["bf16"],
        fp16=training["fp16"],
        tf32=training["tf32"],
        gradient_checkpointing=training["gradient_checkpointing"],
        gradient_checkpointing_kwargs={
            "use_reentrant": training["gradient_checkpointing_use_reentrant"]
        },
        report_to=training["report_to"],
        dataloader_num_workers=training["dataloader_num_workers"],
        dataloader_pin_memory=True,
        remove_unused_columns=True,
        skip_memory_metrics=False,
        max_length=max_length,
        pad_to_multiple_of=training["pad_to_multiple_of"],
        assistant_only_loss=training["assistant_only_loss"],
        packing=training["packing"],
        padding_free=training["padding_free"],
        loss_type=training["loss_type"],
        shuffle_dataset=False,
        dataset_num_proc=1,
        trust_remote_code=True,
        torch_compile=training["torch_compile"],
        group_by_length=False,
        use_liger_kernel=False,
        activation_offloading=False,
        load_best_model_at_end=False,
    )


def _gradient_callback(torch: Any, callback_class: Any) -> tuple[Any, dict[str, Any]]:
    observed = {
        "called": False,
        "lora_gradient_finite": False,
        "lora_gradient_nonzero": False,
        "base_gradient_absent": False,
    }

    class GradientContractCallback(callback_class):
        def on_pre_optimizer_step(
            self, args: Any, state: Any, control: Any, **kwargs: Any
        ) -> None:
            del args, state, control
            model = kwargs.get("model")
            if model is None or observed["called"]:
                return
            lora_gradients = []
            base_gradient_present = False
            for name, parameter in model.named_parameters():
                if (
                    "lora_" in name
                    and parameter.requires_grad
                    and parameter.grad is not None
                ):
                    lora_gradients.append(parameter.grad.detach())
                elif "lora_" not in name and parameter.grad is not None:
                    base_gradient_present = True
            observed["called"] = True
            observed["lora_gradient_finite"] = bool(lora_gradients) and all(
                bool(torch.isfinite(gradient).all().item())
                for gradient in lora_gradients
            )
            observed["lora_gradient_nonzero"] = bool(lora_gradients) and any(
                bool(torch.count_nonzero(gradient).item())
                for gradient in lora_gradients
            )
            observed["base_gradient_absent"] = not base_gradient_present

    return GradientContractCallback(), observed


def _execute_trainer(
    *,
    config: Mapping[str, Any],
    rows: Sequence[dict[str, Any]],
    rank: int,
    max_length: int,
    model_root: Path,
    output_dir: Path,
    preflight: bool,
    resume_from_checkpoint: str | None,
) -> dict[str, Any]:
    try:
        import torch
        from datasets import Dataset
        from peft import LoraConfig, PeftModel, get_peft_model_state_dict
        from safetensors.torch import load_file as load_safetensors
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
        from trl import SFTConfig, SFTTrainer
    except Exception as exc:
        raise Mix2KV4LoRAError("LoRA training package import가 실패했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation=config["base_model"]["attention_backend"],
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = config["training"]["use_cache"]
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    callback, gradient = _gradient_callback(torch, TrainerCallback)
    lora = config["lora"]
    peft_config = LoraConfig(
        task_type=lora["task_type"],
        r=rank,
        lora_alpha=lora["lora_alpha"],
        target_modules=lora["target_modules"],
        lora_dropout=lora["lora_dropout"],
        bias=lora["bias"],
        use_rslora=lora["use_rslora"],
        modules_to_save=lora["modules_to_save"],
    )
    dataset = Dataset.from_list([{"messages": row["messages"]} for row in rows])
    args = _training_args(
        SFTConfig,
        output_dir=output_dir,
        config=config,
        max_length=max_length,
        preflight=preflight,
    )
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
        callbacks=[callback],
    )
    trainable = sum(
        parameter.numel()
        for parameter in trainer.model.parameters()
        if parameter.requires_grad
    )
    total = sum(parameter.numel() for parameter in trainer.model.parameters())
    non_lora_trainable = [
        name
        for name, parameter in trainer.model.named_parameters()
        if parameter.requires_grad and "lora_" not in name
    ]
    target_modules = {
        name.rsplit(".lora_", 1)[0]
        for name, parameter in trainer.model.named_parameters()
        if parameter.requires_grad and ".lora_" in name
    }
    if (
        trainable != lora["expected_trainable_parameters"][str(rank)]
        or len(target_modules) != lora["expected_target_linear_modules"]
        or non_lora_trainable
    ):
        raise Mix2KV4LoRAError("base freeze·LoRA trainable parameter 계약이 다릅니다.")
    started = time.monotonic()
    result = trainer.train(resume_from_checkpoint=resume_from_checkpoint)
    elapsed = time.monotonic() - started
    final_adapter = output_dir / "final_adapter"
    trainer.save_model(str(final_adapter))
    tokenizer.save_pretrained(final_adapter)
    adapter_path = final_adapter / "adapter_model.safetensors"
    adapter_config_path = final_adapter / "adapter_config.json"
    if (
        not adapter_path.is_file()
        or not adapter_config_path.is_file()
        or (final_adapter / "model.safetensors").exists()
    ):
        raise Mix2KV4LoRAError("adapter-only 저장 계약이 다릅니다.")
    global_step = int(trainer.state.global_step)
    training_loss = float(result.training_loss)
    peak_allocated = int(torch.cuda.max_memory_allocated())
    peak_reserved = int(torch.cuda.max_memory_reserved())
    del trainer, model, dataset
    gc.collect()
    torch.cuda.empty_cache()
    reloaded_base = AutoModelForCausalLM.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation=config["base_model"]["attention_backend"],
        low_cpu_mem_usage=True,
    )
    reloaded = PeftModel.from_pretrained(
        reloaded_base, final_adapter, is_trainable=False
    )
    reloaded_rank = int(reloaded.peft_config["default"].r)
    stored_adapter_state = load_safetensors(str(adapter_path))
    reloaded_adapter_state = get_peft_model_state_dict(reloaded)
    adapter_reload_match = set(stored_adapter_state) == set(
        reloaded_adapter_state
    ) and all(
        torch.equal(
            stored_adapter_state[name],
            reloaded_adapter_state[name].detach().cpu(),
        )
        for name in stored_adapter_state
    )
    del reloaded, reloaded_base
    gc.collect()
    torch.cuda.empty_cache()
    if (
        not math.isfinite(training_loss)
        or not gradient["called"]
        or not gradient["lora_gradient_finite"]
        or not gradient["lora_gradient_nonzero"]
        or not gradient["base_gradient_absent"]
        or reloaded_rank != rank
        or not adapter_reload_match
        or peak_reserved
        > int(config["operational_limits"]["max_total_gpu_memory_used_mib"])
        * 1024
        * 1024
    ):
        raise Mix2KV4LoRAError(
            "LoRA loss·gradient·reload·memory 계약을 통과하지 못했습니다."
        )
    return {
        "global_step": global_step,
        "training_loss": training_loss,
        "elapsed_seconds": round(elapsed, 3),
        "trainable_parameters": trainable,
        "target_linear_modules": len(target_modules),
        "total_parameters_with_adapter": total,
        "trainable_ratio": trainable / total,
        "gradient": gradient,
        "peak_allocated_bytes": peak_allocated,
        "peak_reserved_bytes": peak_reserved,
        "adapter_model_sha256": sha256_file(adapter_path),
        "adapter_config_sha256": sha256_file(adapter_config_path),
        "adapter_reload_rank": reloaded_rank,
        "adapter_reload_match": adapter_reload_match,
    }


def _longest_rows(
    rows: Sequence[dict[str, Any]],
    token_rows: Sequence[dict[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in rows}
    token_ids: set[str] = set()
    for token_row in token_rows:
        token_id = token_row.get("id") if isinstance(token_row, Mapping) else None
        if (
            not isinstance(token_id, str)
            or token_id not in by_id
            or token_id in token_ids
        ):
            raise Mix2KV4LoRAError("LoRA preflight token row ID 계약이 다릅니다.")
        token_ids.add(token_id)
    if count < 1 or len(token_rows) < count:
        raise Mix2KV4LoRAError("LoRA preflight longest row 수가 부족합니다.")
    ordered = sorted(
        token_rows,
        key=lambda row: (-int(row["rendered_tokens"]), str(row["id"])),
    )
    selected = [by_id[row["id"]] for row in ordered[:count]]
    if len(selected) != count:
        raise Mix2KV4LoRAError("LoRA preflight longest row 선택이 다릅니다.")
    return selected


def _manifest_runtime_versions(config: Mapping[str, Any]) -> dict[str, str]:
    """실제 import로 관측하는 문자열 version만 반환한다; lock은 config hash가 고정한다."""

    return {
        key: value
        for key, value in config["runtime_versions"].items()
        if isinstance(value, str)
    }


def _valid_hardware_manifest(
    value: Any, config: Mapping[str, Any]
) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "gpu",
        "active_compute_processes",
        "available_ram_bytes",
        "available_disk_bytes",
        "bf16_supported",
    }:
        return False
    gpu = value.get("gpu")
    if not isinstance(gpu, Mapping) or set(gpu) != {
        "index",
        "name",
        "uuid",
        "total_mib",
        "used_mib",
        "free_mib",
        "driver_version",
    }:
        return False

    def valid_int(candidate: Any, minimum: int = 0) -> bool:
        return (
            not isinstance(candidate, bool)
            and isinstance(candidate, int)
            and candidate >= minimum
        )

    limits = config["operational_limits"]
    processes = value.get("active_compute_processes")
    return (
        valid_int(gpu.get("index"))
        and isinstance(gpu.get("name"), str)
        and bool(gpu["name"])
        and isinstance(gpu.get("uuid"), str)
        and bool(gpu["uuid"])
        and valid_int(gpu.get("total_mib"), 1)
        and valid_int(gpu.get("used_mib"))
        and valid_int(gpu.get("free_mib"))
        and gpu["used_mib"] <= limits["max_total_gpu_memory_used_mib"]
        and gpu["free_mib"] >= limits["min_free_gpu_memory_before_start_mib"]
        and isinstance(gpu.get("driver_version"), str)
        and bool(gpu["driver_version"])
        and isinstance(processes, list)
        and (
            not limits["require_no_active_compute_process_before_start"]
            or not processes
        )
        and valid_int(value.get("available_ram_bytes"), 1)
        and value["available_ram_bytes"] >= limits["min_system_ram_available_bytes"]
        and valid_int(value.get("available_disk_bytes"), 1)
        and value["available_disk_bytes"] >= limits["min_disk_available_bytes"]
        and value.get("bf16_supported") is True
    )


def _validate_adapter_metrics(
    *,
    metrics: Any,
    config: Mapping[str, Any],
    target: Path,
    rank: int,
    expected_steps: int,
) -> None:
    """기록된 metric과 현재 adapter artifact를 함께 재검증한다."""

    if not isinstance(metrics, Mapping) or set(metrics) != TRAINING_METRIC_FIELDS:
        raise Mix2KV4LoRAError("LoRA metric field 계약이 다릅니다.")
    gradient = metrics.get("gradient")
    integer_fields = (
        "global_step",
        "trainable_parameters",
        "target_linear_modules",
        "total_parameters_with_adapter",
        "peak_allocated_bytes",
        "peak_reserved_bytes",
        "adapter_reload_rank",
    )
    if any(
        isinstance(metrics.get(field), bool)
        or not isinstance(metrics.get(field), int)
        for field in integer_fields
    ):
        raise Mix2KV4LoRAError("LoRA metric 정수 field가 잘못됐습니다.")
    numeric_fields = ("training_loss", "elapsed_seconds", "trainable_ratio")
    if any(
        isinstance(metrics.get(field), bool)
        or not isinstance(metrics.get(field), (int, float))
        or not math.isfinite(float(metrics[field]))
        for field in numeric_fields
    ):
        raise Mix2KV4LoRAError("LoRA metric 실수 field가 잘못됐습니다.")
    trainable = metrics["trainable_parameters"]
    total = metrics["total_parameters_with_adapter"]
    peak_allocated = metrics["peak_allocated_bytes"]
    peak_reserved = metrics["peak_reserved_bytes"]
    if (
        metrics["global_step"] != expected_steps
        or float(metrics["training_loss"]) < 0
        or float(metrics["elapsed_seconds"]) < 0
        or trainable != config["lora"]["expected_trainable_parameters"][str(rank)]
        or metrics["target_linear_modules"]
        != config["lora"]["expected_target_linear_modules"]
        or total <= trainable
        or not math.isclose(
            float(metrics["trainable_ratio"]),
            trainable / total,
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
        or not isinstance(gradient, Mapping)
        or set(gradient) != GRADIENT_METRIC_FIELDS
        or any(gradient.get(field) is not True for field in GRADIENT_METRIC_FIELDS)
        or peak_allocated < 0
        or peak_reserved < peak_allocated
        or peak_reserved
        > config["operational_limits"]["max_total_gpu_memory_used_mib"]
        * 1024
        * 1024
        or metrics["adapter_reload_rank"] != rank
        or metrics.get("adapter_reload_match") is not True
        or not _is_sha256(metrics.get("adapter_model_sha256"))
        or not _is_sha256(metrics.get("adapter_config_sha256"))
    ):
        raise Mix2KV4LoRAError("LoRA metric 값 계약이 다릅니다.")

    final_adapter = target / "trainer/final_adapter"
    adapter_path = final_adapter / "adapter_model.safetensors"
    adapter_config_path = final_adapter / "adapter_config.json"
    _reject_symlink_components(final_adapter, "LoRA final adapter")
    if final_adapter.is_symlink() or not final_adapter.is_dir():
        raise Mix2KV4LoRAError("LoRA final adapter 경로가 없거나 안전하지 않습니다.")
    for item in final_adapter.iterdir():
        if item.is_symlink() or item.is_dir():
            raise Mix2KV4LoRAError("LoRA final adapter 내부 경로가 안전하지 않습니다.")
        name = item.name
        if (
            (
                item.suffix.casefold()
                in {".safetensors", ".bin", ".pt", ".pth", ".gguf"}
                and name != "adapter_model.safetensors"
            )
            or name in {"model.safetensors.index.json", "pytorch_model.bin.index.json"}
        ):
            raise Mix2KV4LoRAError(
                f"LoRA adapter-only 경로에 full weight 후보가 있습니다: {name}"
            )
    for path, label in (
        (adapter_path, "LoRA adapter model"),
        (adapter_config_path, "LoRA adapter config"),
    ):
        _reject_symlink_components(path, label)
        if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
            raise Mix2KV4LoRAError(f"{label} artifact가 없거나 안전하지 않습니다.")
    if (
        sha256_file(adapter_path) != metrics["adapter_model_sha256"]
        or sha256_file(adapter_config_path) != metrics["adapter_config_sha256"]
    ):
        raise Mix2KV4LoRAError("LoRA adapter artifact hash·adapter-only 계약이 다릅니다.")
    adapter_config = _load_json(adapter_config_path, "saved LoRA adapter config")
    if (
        adapter_config.get("peft_type") != "LORA"
        or adapter_config.get("r") != rank
        or adapter_config.get("use_rslora") is not True
        or adapter_config.get("bias") != config["lora"]["bias"]
        or adapter_config.get("lora_alpha") != config["lora"]["lora_alpha"]
        or adapter_config.get("lora_dropout") != config["lora"]["lora_dropout"]
        or adapter_config.get("task_type") != config["lora"]["task_type"]
    ):
        raise Mix2KV4LoRAError("saved LoRA adapter config 계약이 다릅니다.")


def _validate_preflight_manifest(
    *,
    value: Mapping[str, Any],
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    target: Path,
    rank: int,
    run_id: str,
    identity: Mapping[str, Any],
    runtime: Mapping[str, str],
) -> None:
    false_fields = (
        "full_fine_tuning_performed",
        "ki20_training_performed",
        "training_performed",
        "production_promotion_allowed",
        "sealed_blind_accessed",
    )
    if (
        set(value) != PREFLIGHT_MANIFEST_FIELDS
        or value.get("schema_version") != "1.0.0"
        or value.get("status") != "preflight_passed"
        or value.get("passed") is not True
        or value.get("rank") != rank
        or value.get("run_id") != run_id
        or value.get("identity") != identity
        or dict(runtime) != _manifest_runtime_versions(config)
        or value.get("runtime_versions") != dict(runtime)
        or value.get("data_build_id") != data_manifest.get("build_id")
        or value.get("max_length") != data_manifest.get("selected_max_length")
        or value.get("rows") != config["preflight"]["longest_rows"]
        or not _valid_hardware_manifest(value.get("hardware_before"), config)
        or value.get("base_weights_unchanged") is not True
        or any(value.get(field) is not False for field in false_fields)
        or not isinstance(value.get("completed_at_utc"), str)
        or not value["completed_at_utc"]
    ):
        raise Mix2KV4LoRAError("기존 LoRA preflight manifest 계약이 다릅니다.")
    _validate_adapter_metrics(
        metrics=value.get("metrics"),
        config=config,
        target=target,
        rank=rank,
        expected_steps=config["preflight"]["optimizer_steps"],
    )


def _validate_training_manifest(
    *,
    value: Mapping[str, Any],
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    target: Path,
    rank: int,
    run_id: str,
    identity: Mapping[str, Any],
    runtime: Mapping[str, str],
    preflight_run_id: str,
    rows: int,
) -> None:
    false_fields = (
        "full_fine_tuning_performed",
        "ki20_training_performed",
        "production_promotion_allowed",
        "sealed_blind_accessed",
    )
    if (
        set(value) != TRAINING_MANIFEST_FIELDS
        or value.get("schema_version") != "1.0.0"
        or value.get("status") != "training_completed"
        or value.get("completed") is not True
        or value.get("rank") != rank
        or value.get("run_id") != run_id
        or value.get("identity") != identity
        or dict(runtime) != _manifest_runtime_versions(config)
        or value.get("runtime_versions") != dict(runtime)
        or value.get("data_build_id") != data_manifest.get("build_id")
        or value.get("preflight_run_id") != preflight_run_id
        or value.get("max_length") != data_manifest.get("selected_max_length")
        or value.get("rows") != rows
        or not _valid_hardware_manifest(value.get("hardware_before"), config)
        or value.get("base_weights_unchanged") is not True
        or value.get("adapter_only") is not True
        or value.get("num_train_epochs") != config["training"]["num_train_epochs"]
        or any(value.get(field) is not False for field in false_fields)
        or not isinstance(value.get("completed_at_utc"), str)
        or not value["completed_at_utc"]
    ):
        raise Mix2KV4LoRAError("기존 LoRA training manifest 계약이 다릅니다.")
    _validate_adapter_metrics(
        metrics=value.get("metrics"),
        config=config,
        target=target,
        rank=rank,
        expected_steps=config["training"]["expected_optimizer_steps"],
    )


def _preflight_root(
    config: Mapping[str, Any], artifact_root: Path, rank: int, run_id: str
) -> Path:
    del config
    return artifact_root / PREFLIGHT_SUBDIR / f"r{rank}" / run_id


def run_preflight(
    *,
    config_path: Path,
    data_build: Path,
    model_root: Path,
    artifact_root: Path,
    rank: int,
    execute: bool,
) -> dict[str, Any]:
    config = _validate_config(config_path)
    _validate_directory(artifact_root, "artifact root")
    _validate_model_snapshot(config, model_root)
    runtime = _runtime_versions(config)
    data_manifest, rows, token_rows = _validate_data_build(data_build, config)
    run_id, identity = _run_identity(
        mode="preflight",
        config_path=config_path,
        data_manifest=data_manifest,
        rank=rank,
    )
    target = _preflight_root(config, artifact_root, rank, run_id)
    _reject_symlink_components(target, "LoRA preflight target")
    if not execute:
        return {
            "status": "preflight_dry_run",
            "rank": rank,
            "run_id": run_id,
            "rows": config["preflight"]["longest_rows"],
            "max_length": data_manifest["selected_max_length"],
            "execute_required": True,
        }
    _ensure_private_directory(target.parent, "LoRA preflight parent")
    lock_path = target.parent / f".{run_id}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE)
    global_descriptor: int | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAError("LoRA preflight가 이미 실행 중입니다.") from exc
        global_descriptor = acquire_mix2k_v4_gpu_lock(artifact_root)
        hardware = _hardware_gate(config, target.parent)
        manifest_path = target / "preflight_manifest.json"
        if manifest_path.is_file():
            value = _load_json(manifest_path, "LoRA preflight manifest")
            _validate_preflight_manifest(
                value=value,
                config=config,
                data_manifest=data_manifest,
                target=target,
                rank=rank,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
            )
            return {**value, "mode": "reused", "path": str(target)}
        temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=target.parent))
        temporary.chmod(PRIVATE_DIR_MODE)
        try:
            selected = _longest_rows(
                rows, token_rows, int(config["preflight"]["longest_rows"])
            )
            metrics = _execute_trainer(
                config=config,
                rows=selected,
                rank=rank,
                max_length=int(data_manifest["selected_max_length"]),
                model_root=model_root,
                output_dir=temporary / "trainer",
                preflight=True,
                resume_from_checkpoint=None,
            )
            if metrics["global_step"] != config["preflight"]["optimizer_steps"]:
                raise Mix2KV4LoRAError("LoRA preflight optimizer step이 다릅니다.")
            manifest = {
                "schema_version": "1.0.0",
                "status": "preflight_passed",
                "passed": True,
                "rank": rank,
                "run_id": run_id,
                "identity": identity,
                "runtime_versions": runtime,
                "data_build_id": data_manifest["build_id"],
                "max_length": data_manifest["selected_max_length"],
                "rows": len(selected),
                "hardware_before": hardware,
                "metrics": metrics,
                "base_weights_unchanged": sha256_file(model_root / "model.safetensors")
                == config["base_model"]["files"]["model.safetensors"],
                "full_fine_tuning_performed": False,
                "ki20_training_performed": False,
                "training_performed": False,
                "production_promotion_allowed": False,
                "sealed_blind_accessed": False,
                "completed_at_utc": _utc_now(),
            }
            if manifest["base_weights_unchanged"] is not True:
                raise Mix2KV4LoRAError("K0 base weight hash가 바뀌었습니다.")
            _validate_preflight_manifest(
                value=manifest,
                config=config,
                data_manifest=data_manifest,
                target=temporary,
                rank=rank,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
            )
            (temporary / "preflight_manifest.json").write_bytes(_json_bytes(manifest))
            (temporary / "preflight_manifest.json").chmod(PRIVATE_FILE_MODE)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return {**manifest, "mode": "created", "path": str(target)}
    finally:
        if global_descriptor is not None:
            os.close(global_descriptor)
        os.close(descriptor)


def _expected_preflight(
    config_path: Path,
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    artifact_root: Path,
    rank: int,
    runtime: Mapping[str, str],
) -> dict[str, Any]:
    run_id, identity = _run_identity(
        mode="preflight",
        config_path=config_path,
        data_manifest=data_manifest,
        rank=rank,
    )
    path = (
        _preflight_root(config, artifact_root, rank, run_id) / "preflight_manifest.json"
    )
    value = _load_json(path, "required LoRA preflight")
    _validate_preflight_manifest(
        value=value,
        config=config,
        data_manifest=data_manifest,
        target=path.parent,
        rank=rank,
        run_id=run_id,
        identity=identity,
        runtime=runtime,
    )
    return value


def run_training(
    *,
    config_path: Path,
    data_build: Path,
    model_root: Path,
    artifact_root: Path,
    rank: int,
    execute: bool,
) -> dict[str, Any]:
    config = _validate_config(config_path)
    _validate_directory(artifact_root, "artifact root")
    _validate_model_snapshot(config, model_root)
    runtime = _runtime_versions(config)
    data_manifest, rows, _ = _validate_data_build(data_build, config)
    preflight = _expected_preflight(
        config_path, config, data_manifest, artifact_root, rank, runtime
    )
    run_id, identity = _run_identity(
        mode="train", config_path=config_path, data_manifest=data_manifest, rank=rank
    )
    relative = config["outputs"]["run_root"].format(
        rank=f"r{rank}", run_build_id=run_id
    )
    target = artifact_root / relative
    _reject_symlink_components(target, "LoRA training target")
    if not execute:
        return {
            "status": "training_dry_run",
            "rank": rank,
            "run_id": run_id,
            "rows": len(rows),
            "max_length": data_manifest["selected_max_length"],
            "preflight_run_id": preflight["run_id"],
            "execute_required": True,
        }
    _ensure_private_directory(target, "LoRA training target")
    lock_descriptor = os.open(
        target / ".training.lock", os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE
    )
    global_descriptor: int | None = None
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAError("LoRA training이 이미 실행 중입니다.") from exc
        global_descriptor = acquire_mix2k_v4_gpu_lock(artifact_root)
        hardware = _hardware_gate(config, target.parent)
        manifest_path = target / "training_manifest.json"
        if manifest_path.is_file():
            value = _load_json(manifest_path, "LoRA training manifest")
            _validate_training_manifest(
                value=value,
                config=config,
                data_manifest=data_manifest,
                target=target,
                rank=rank,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
                preflight_run_id=preflight["run_id"],
                rows=len(rows),
            )
            return {**value, "mode": "reused", "path": str(target)}
        trainer_root = target / "trainer"
        _reject_symlink_components(trainer_root, "LoRA trainer root")
        if trainer_root.exists() and (
            trainer_root.is_symlink() or not trainer_root.is_dir()
        ):
            raise Mix2KV4LoRAError("LoRA trainer root가 안전하지 않습니다.")
        resume = _validated_resume_checkpoint(
            target=target,
            trainer_root=trainer_root,
            identity=identity,
            rank=rank,
            run_id=run_id,
            expected_steps=int(config["training"]["expected_optimizer_steps"]),
        )
        _atomic_write(
            target / "training_state.json",
            _json_bytes(
                {
                    "schema_version": "1.0.0",
                    "status": "running",
                    "rank": rank,
                    "run_id": run_id,
                    "identity": identity,
                    "resume_from_checkpoint": resume,
                    "started_at_utc": _utc_now(),
                }
            ),
        )
        metrics = _execute_trainer(
            config=config,
            rows=rows,
            rank=rank,
            max_length=int(data_manifest["selected_max_length"]),
            model_root=model_root,
            output_dir=trainer_root,
            preflight=False,
            resume_from_checkpoint=resume,
        )
        if metrics["global_step"] != config["training"]["expected_optimizer_steps"]:
            raise Mix2KV4LoRAError("LoRA training optimizer step이 250이 아닙니다.")
        manifest = {
            "schema_version": "1.0.0",
            "status": "training_completed",
            "completed": True,
            "rank": rank,
            "run_id": run_id,
            "identity": identity,
            "runtime_versions": runtime,
            "data_build_id": data_manifest["build_id"],
            "preflight_run_id": preflight["run_id"],
            "max_length": data_manifest["selected_max_length"],
            "rows": len(rows),
            "hardware_before": hardware,
            "metrics": metrics,
            "base_weights_unchanged": sha256_file(model_root / "model.safetensors")
            == config["base_model"]["files"]["model.safetensors"],
            "adapter_only": True,
            "num_train_epochs": 1,
            "full_fine_tuning_performed": False,
            "ki20_training_performed": False,
            "production_promotion_allowed": False,
            "sealed_blind_accessed": False,
            "completed_at_utc": _utc_now(),
        }
        if manifest["base_weights_unchanged"] is not True:
            raise Mix2KV4LoRAError("K0 base weight hash가 바뀌었습니다.")
        _validate_training_manifest(
            value=manifest,
            config=config,
            data_manifest=data_manifest,
            target=target,
            rank=rank,
            run_id=run_id,
            identity=identity,
            runtime=runtime,
            preflight_run_id=preflight["run_id"],
            rows=len(rows),
        )
        _atomic_write(manifest_path, _json_bytes(manifest))
        _atomic_write(
            target / "training_state.json",
            _json_bytes(
                {
                    "schema_version": "1.0.0",
                    "status": "completed",
                    "rank": rank,
                    "run_id": run_id,
                    "completed_at_utc": manifest["completed_at_utc"],
                }
            ),
        )
        return {**manifest, "mode": "created", "path": str(target)}
    finally:
        if global_descriptor is not None:
            os.close(global_descriptor)
        os.close(lock_descriptor)


def _ranks(value: str, config: Mapping[str, Any]) -> list[int]:
    if value == "all":
        return list(config["lora"]["ranks"])
    rank = int(value)
    if rank not in config["lora"]["ranks"]:
        raise Mix2KV4LoRAError("LoRA rank가 고정 실험군에 없습니다.")
    return [rank]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="K0 MIX2K v4 LoRA preflight/training")
    parser.add_argument("command", choices=("validate-contract", "preflight", "train"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-build", type=Path)
    parser.add_argument("--model-snapshot", type=Path, default=DEFAULT_MODEL_SNAPSHOT)
    parser.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--rank", choices=("8", "16", "32", "all"), default="16")
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = _absolute(args.config)
        _reject_symlink_components(config_path, "config")
        config = _validate_config(config_path)
        model_root = _absolute(args.model_snapshot)
        artifact_root = _absolute(args.artifact_root)
        _validate_model_snapshot(config, model_root)
        _reject_symlink_components(artifact_root, "artifact root")
        runtime = _runtime_versions(config)
        if args.command == "validate-contract":
            result: Any = {
                "status": "valid",
                "config_sha256": sha256_file(config_path),
                "ranks": config["lora"]["ranks"],
                "primary_rank": config["lora"]["primary_rank"],
                "runtime_versions": runtime,
            }
        else:
            if args.data_build is None:
                raise Mix2KV4LoRAError("preflight·train에 --data-build가 필요합니다.")
            data_build = _absolute(args.data_build)
            _reject_symlink_components(data_build, "final data build")
            reports = []
            for rank in _ranks(args.rank, config):
                if args.command == "preflight":
                    reports.append(
                        run_preflight(
                            config_path=config_path,
                            data_build=data_build,
                            model_root=model_root,
                            artifact_root=artifact_root,
                            rank=rank,
                            execute=args.execute,
                        )
                    )
                else:
                    reports.append(
                        run_training(
                            config_path=config_path,
                            data_build=data_build,
                            model_root=model_root,
                            artifact_root=artifact_root,
                            rank=rank,
                            execute=args.execute,
                        )
                    )
            result = reports
    except (Mix2KV4LoRAError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
