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
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/model_versions/saju_1b_baseline/mix2k-v4-lora-v1.0.0.json"
)
DEFAULT_MODEL_SNAPSHOT = REPO_ROOT / (
    "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
    "bf4786aa2a1908adce942d53976270132732f720"
)
PREFLIGHT_SUBDIR = Path(
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/lora-preflight/v1.0.0"
)
SCRIPT_PATH = Path(__file__).resolve()
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


class Mix2KV4LoRAError(RuntimeError):
    """MIX2K v4 LoRA 계약·GPU gate·adapter 검증 실패."""


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
    if (
        path.is_symlink()
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
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
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


def _validate_config(path: Path) -> dict[str, Any]:
    config = _load_json(path, "LoRA config")
    model = config.get("base_model")
    data = config.get("required_data")
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
            "tokenizer.json": "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab",
            "tokenizer_config.json": "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5",
            "chat_template.jinja": "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3",
            "config.json": "fe14b20b4b616d62ca0682312c2fcd2b90d9a836d14a1ff6448db3f533fd15a1",
        },
    }
    expected_data = {
        "rows": 2000,
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
        or config.get("training_version") != "v1.0.0"
        or config.get("dataset_version") != DATASET_VERSION
        or model != expected_model
        or data != expected_data
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
            "run_root": "runs/K0-MIX2K-V4-LORA/v1.0.0/{rank}/{run_build_id}",
            "report_root": "data/reports/saju_1b_baseline/mix2k-v4-lora/v1.0.0/{rank}/{run_build_id}",
        }
    ):
        raise Mix2KV4LoRAError("K0 MIX2K v4 LoRA 고정 계약이 다릅니다.")
    for item in (runtime["base_lock"], runtime["lora_overlay"]):
        file_path = REPO_ROOT / item["path"]
        if file_path.is_symlink() or not file_path.is_file() or sha256_file(file_path) != item["sha256"]:
            raise Mix2KV4LoRAError("LoRA package lock hash가 다릅니다.")
    if set(model.get("files", {})) != {
        "model.safetensors",
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
    if data_build.is_symlink() or not data_build.is_dir():
        raise Mix2KV4LoRAError("final data build가 없거나 symlink입니다.")
    required = config["required_data"]
    manifest_path = data_build / required["manifest_name"]
    manifest = _load_json(manifest_path, "final data manifest")
    max_length = manifest.get("selected_max_length")
    if (
        manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("build_id") != data_build.name
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
    for path, relative in (
        (training_path, required["training_path"]),
        (audit_path, required["token_audit_path"]),
        (audit_rows_path, "reports/token_audit_2000.jsonl"),
    ):
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != manifest.get("artifact_sha256", {}).get(relative)
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
    token_by_id = {row.get("id"): row for row in token_rows}
    ids: set[str] = set()
    axes: Counter[str] = Counter()
    for row in rows:
        messages = row.get("messages")
        if (
            not isinstance(row, dict)
            or set(row) != TRAIN_ROW_FIELDS
            or row.get("schema_version") != "1.0.0"
            or row.get("dataset_version") != DATASET_VERSION
            or not isinstance(row.get("id"), str)
            or row["id"] in ids
            or row.get("task_axis") not in EXPECTED_AXES
            or row.get("assistant_only_loss") is not True
            or row.get("restricted_local_only") is not False
            or not isinstance(messages, list)
            or len(messages) < 3
            or messages[0].get("role") != "system"
            or messages[-1].get("role") != "assistant"
            or not isinstance(messages[-1].get("content"), str)
            or row["id"] not in token_by_id
            or token_by_id[row["id"]].get("truncated") is not False
            or token_by_id[row["id"]].get("user_system_loss_leakage_tokens") != 0
            or token_by_id[row["id"]].get("rendered_tokens", max_length + 1)
            > max_length
        ):
            raise Mix2KV4LoRAError(f"final training row 계약이 다릅니다: {row.get('id')}")
        ids.add(row["id"])
        axes[row["task_axis"]] += 1
    if dict(axes) != EXPECTED_AXES or len(token_by_id) != EXPECTED_ROWS:
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
            raise Mix2KV4LoRAError("GPU compute process 숫자 field가 잘못됐습니다.") from exc
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
    output_root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    ram = _available_ram_bytes()
    disk = shutil.disk_usage(output_root).free
    if (
        not torch.cuda.is_available()
        or torch.cuda.device_count() != limits["expected_gpu_count"]
        or not torch.cuda.is_bf16_supported()
        or gpu["total_mib"] > limits["max_total_gpu_memory_used_mib"]
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
                if "lora_" in name and parameter.requires_grad and parameter.grad is not None:
                    lora_gradients.append(parameter.grad.detach())
                elif "lora_" not in name and parameter.grad is not None:
                    base_gradient_present = True
            observed["called"] = True
            observed["lora_gradient_finite"] = bool(lora_gradients) and all(
                bool(torch.isfinite(gradient).all().item())
                for gradient in lora_gradients
            )
            observed["lora_gradient_nonzero"] = bool(lora_gradients) and any(
                bool(torch.count_nonzero(gradient).item()) for gradient in lora_gradients
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
    reloaded = PeftModel.from_pretrained(reloaded_base, final_adapter, is_trainable=False)
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
        raise Mix2KV4LoRAError("LoRA loss·gradient·reload·memory 계약을 통과하지 못했습니다.")
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
    ordered = sorted(
        token_rows,
        key=lambda row: (-int(row["rendered_tokens"]), str(row["id"])),
    )
    selected = [by_id[row["id"]] for row in ordered[:count]]
    if len(selected) != count:
        raise Mix2KV4LoRAError("LoRA preflight longest row 선택이 다릅니다.")
    return selected


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
    runtime = _runtime_versions(config)
    data_manifest, rows, token_rows = _validate_data_build(data_build, config)
    run_id, identity = _run_identity(
        mode="preflight", config_path=config_path, data_manifest=data_manifest, rank=rank
    )
    target = _preflight_root(config, artifact_root, rank, run_id)
    if not execute:
        return {
            "status": "preflight_dry_run",
            "rank": rank,
            "run_id": run_id,
            "rows": config["preflight"]["longest_rows"],
            "max_length": data_manifest["selected_max_length"],
            "execute_required": True,
        }
    hardware = _hardware_gate(config, target.parent)
    target.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    lock_path = target.parent / f".{run_id}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAError("LoRA preflight가 이미 실행 중입니다.") from exc
        manifest_path = target / "preflight_manifest.json"
        if manifest_path.is_file():
            value = _load_json(manifest_path, "LoRA preflight manifest")
            if value.get("identity") != identity or value.get("passed") is not True:
                raise Mix2KV4LoRAError("기존 LoRA preflight identity가 다릅니다.")
            return {**value, "mode": "reused", "path": str(target)}
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{run_id}.", dir=target.parent)
        )
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
                "base_weights_unchanged": sha256_file(
                    model_root / "model.safetensors"
                )
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
            (temporary / "preflight_manifest.json").write_bytes(_json_bytes(manifest))
            (temporary / "preflight_manifest.json").chmod(PRIVATE_FILE_MODE)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return {**manifest, "mode": "created", "path": str(target)}
    finally:
        os.close(descriptor)


def _expected_preflight(
    config_path: Path,
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    artifact_root: Path,
    rank: int,
) -> dict[str, Any]:
    run_id, identity = _run_identity(
        mode="preflight", config_path=config_path, data_manifest=data_manifest, rank=rank
    )
    path = (
        _preflight_root(config, artifact_root, rank, run_id)
        / "preflight_manifest.json"
    )
    value = _load_json(path, "required LoRA preflight")
    if (
        value.get("identity") != identity
        or value.get("rank") != rank
        or value.get("passed") is not True
        or value.get("base_weights_unchanged") is not True
        or value.get("training_performed") is not False
    ):
        raise Mix2KV4LoRAError("required LoRA preflight가 유효하지 않습니다.")
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
    runtime = _runtime_versions(config)
    data_manifest, rows, _ = _validate_data_build(data_build, config)
    preflight = _expected_preflight(
        config_path, config, data_manifest, artifact_root, rank
    )
    run_id, identity = _run_identity(
        mode="train", config_path=config_path, data_manifest=data_manifest, rank=rank
    )
    relative = config["outputs"]["run_root"].format(rank=f"r{rank}", run_build_id=run_id)
    target = artifact_root / relative
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
    hardware = _hardware_gate(config, target.parent)
    target.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    target.chmod(PRIVATE_DIR_MODE)
    lock_descriptor = os.open(
        target / ".training.lock", os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE
    )
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAError("LoRA training이 이미 실행 중입니다.") from exc
        manifest_path = target / "training_manifest.json"
        if manifest_path.is_file():
            value = _load_json(manifest_path, "LoRA training manifest")
            if value.get("identity") != identity or value.get("completed") is not True:
                raise Mix2KV4LoRAError("기존 LoRA training identity가 다릅니다.")
            return {**value, "mode": "reused", "path": str(target)}
        trainer_root = target / "trainer"
        resume = None
        checkpoints = sorted(
            (
                path
                for path in trainer_root.glob("checkpoint-*")
                if path.is_dir() and path.name.removeprefix("checkpoint-").isdigit()
            ),
            key=lambda path: int(path.name.removeprefix("checkpoint-")),
        )
        if checkpoints:
            resume = str(checkpoints[-1])
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
            "base_weights_unchanged": sha256_file(
                model_root / "model.safetensors"
            )
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
