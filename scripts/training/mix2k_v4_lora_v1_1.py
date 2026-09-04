# mix2k_v4_lora_v1_1.py - reviewed-repair v1.1 final에서 K0 LoRA r16만 검증·학습한다.

from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    EXPECTED_ROWS,
    PRIVATE_FILE_MODE,
    RUNTIME_AXES,
    sha256_bytes,
    sha256_file,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.training import mix2k_v4_lora as core

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/model_versions/saju_1b_baseline/mix2k-v4-reviewed-repair-lora-v1.1.0.json"
)
DEFAULT_MODEL_SNAPSHOT = core.DEFAULT_MODEL_SNAPSHOT
SCRIPT_PATH = Path(__file__).resolve()
RANK = 16
UNPINNED = "unpinned_pending_final_build"
PINNED = "pinned"
CHECKPOINT_REQUIRED_FILES = frozenset(
    {
        "adapter_config.json",
        "adapter_model.safetensors",
        "optimizer.pt",
        "rng_state.pth",
        "scheduler.pt",
        "trainer_state.json",
        "training_args.bin",
    }
)
CHECKPOINT_OPTIONAL_FILES = frozenset(
    {
        "README.md",
        "chat_template.jinja",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
CHECKPOINT_ALLOWED_FILES = CHECKPOINT_REQUIRED_FILES | CHECKPOINT_OPTIONAL_FILES
CHECKPOINT_FINGERPRINT_FIELDS = {
    "checkpoint_name",
    "step",
    "files_sha256",
    "checkpoint_sha256",
}
EXPECTED_TARGET_MODULES = frozenset(
    {"down_proj", "gate_proj", "k_proj", "o_proj", "q_proj", "up_proj", "v_proj"}
)
MAX_CHECKPOINT_FILE_BYTES = 1024 * 1024 * 1024

ARTIFACT_PATHS = {
    "evaluation/dev_cases_200.jsonl",
    "provenance/combined_candidates_2000.jsonl",
    "provenance/row_lineage_2000.jsonl",
    "reports/lineage_summary.json",
    "reports/package_audit.json",
    "reports/token_audit_2000.jsonl",
    "reports/token_audit_summary.json",
    "training/train_2000.jsonl",
}
PIN_FIELDS = {
    "source_config_sha256",
    "generator_sha256",
    "final_build_id",
    "final_build_sha256",
    "final_manifest_sha256",
    "artifact_sha256",
}
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
FINAL_IDENTITY_FIELDS = {
    "dataset_version",
    "artifact_revision",
    "config_sha256",
    "generator_sha256",
    "source_dependency_sha256",
    "prepare_target_sha256",
    "repair_teacher_manifest_sha256",
    "repair_candidates_sha256",
    "parent_final_build_sha256",
    "parent_teacher_candidates_sha256",
    "review_package_sha256",
    "base_model_files",
    "artifact_sha256",
}
FINAL_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_version",
    "artifact_revision",
    "build_id",
    "build_sha256",
    "identity",
    "rows",
    "axes",
    "selected_max_length",
    "assistant_only_loss",
    "truncation",
    "full_runtime_snapshot_used",
    "compact_projection_used_for_training",
    "inherited_parent_rows",
    "regenerated_cross_provider_rows",
    "all_2000_rows_cross_provider_contract_met",
    "development_targets_accessed",
    "sealed_blind_accessed",
    "training_execution_allowed",
    "lora_r16_experimental_training_allowed",
    "training_performed",
    "production_promotion_allowed",
    "artifact_sha256",
}
TOKEN_SUMMARY_FIELDS = {
    "schema_version",
    "rows",
    "rendered_tokens",
    "prompt_tokens",
    "supervised_assistant_tokens",
    "rows_over_2048",
    "rows_over_3584",
    "rows_over_4096",
    "rows_over_8192",
    "truncated_rows",
    "zero_assistant_mask_rows",
    "missing_supervised_eos_rows",
    "user_system_loss_leakage_rows",
    "selected_max_length",
    "provisional_ladder_value",
    "many_rows_over_2048",
    "runtime_projection_review_required",
    "training_blocked_pending_projection_review",
    "candidate_validation",
    "parent_comparison",
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
    "data_build_sha256",
    "data_manifest_sha256",
    "max_length",
    "rows",
    "selected_row_ids",
    "selected_token_audit_sha256",
    "hardware_before",
    "metrics",
    "base_model_files_unchanged",
    "base_weights_unchanged",
    "adapter_only",
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
    "data_build_sha256",
    "data_manifest_sha256",
    "preflight_run_id",
    "preflight_manifest_sha256",
    "max_length",
    "rows",
    "hardware_before",
    "metrics",
    "base_model_files_unchanged",
    "base_weights_unchanged",
    "adapter_only",
    "resume_checkpoint",
    "num_train_epochs",
    "full_fine_tuning_performed",
    "ki20_training_performed",
    "production_promotion_allowed",
    "sealed_blind_accessed",
    "completed_at_utc",
}


class Mix2KV4LoRAV11Error(RuntimeError):
    """reviewed-repair v1.1 R16 학습 계약 위반."""


def _is_sha256(value: Any) -> bool:
    return core._is_sha256(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _valid_checkpoint_fingerprint(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != CHECKPOINT_FINGERPRINT_FIELDS:
        return False
    files = value.get("files_sha256")
    payload = {
        "checkpoint_name": value.get("checkpoint_name"),
        "step": value.get("step"),
        "files_sha256": files,
    }
    return (
        isinstance(value.get("checkpoint_name"), str)
        and value["checkpoint_name"] == f"checkpoint-{value.get('step')}"
        and isinstance(value.get("step"), int)
        and not isinstance(value.get("step"), bool)
        and isinstance(files, Mapping)
        and CHECKPOINT_REQUIRED_FILES <= set(files) <= CHECKPOINT_ALLOWED_FILES
        and all(
            isinstance(name, str) and _is_sha256(digest)
            for name, digest in files.items()
        )
        and value.get("checkpoint_sha256")
        == sha256_bytes(canonical_json_bytes(payload))
    )


def _validate_torch_archive(path: Path, label: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if (
                archive.testzip() is not None
                or not any(name.endswith("/data.pkl") for name in names)
                or not any(name.endswith("/version") for name in names)
                or any(
                    name.startswith("/") or "\\" in name or ".." in Path(name).parts
                    for name in names
                )
            ):
                raise Mix2KV4LoRAV11Error(f"{label} torch archive 형식이 다릅니다.")
    except (OSError, zipfile.BadZipFile) as exc:
        raise Mix2KV4LoRAV11Error(f"{label} torch archive를 읽지 못했습니다.") from exc


def _validate_adapter_safetensors(path: Path, expected_tensors: int) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            prefix = stream.read(8)
            if len(prefix) != 8:
                raise Mix2KV4LoRAV11Error(
                    "resume adapter safetensors header가 없습니다."
                )
            header_size = struct.unpack("<Q", prefix)[0]
            if not 2 <= header_size <= min(size - 8, 16 * 1024 * 1024):
                raise Mix2KV4LoRAV11Error(
                    "resume adapter safetensors header 크기가 다릅니다."
                )
            header = json.loads(stream.read(header_size))
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error) as exc:
        raise Mix2KV4LoRAV11Error(
            "resume adapter safetensors를 읽지 못했습니다."
        ) from exc
    if not isinstance(header, Mapping):
        raise Mix2KV4LoRAV11Error("resume adapter safetensors index가 다릅니다.")
    tensors = {name: item for name, item in header.items() if name != "__metadata__"}
    data_size = size - 8 - header_size
    spans: list[tuple[int, int]] = []
    for name, item in tensors.items():
        if (
            not isinstance(name, str)
            or not name.endswith((".lora_A.weight", ".lora_B.weight"))
            or not isinstance(item, Mapping)
            or set(item) != {"dtype", "shape", "data_offsets"}
            or item.get("dtype") not in {"BF16", "F16", "F32"}
            or not isinstance(item.get("shape"), list)
            or not item["shape"]
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 1
                for value in item["shape"]
            )
            or not isinstance(item.get("data_offsets"), list)
            or len(item["data_offsets"]) != 2
        ):
            raise Mix2KV4LoRAV11Error("resume adapter tensor 계약이 다릅니다.")
        start, end = item["data_offsets"]
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not 0 <= start < end <= data_size
        ):
            raise Mix2KV4LoRAV11Error("resume adapter tensor offset이 다릅니다.")
        spans.append((start, end))
    ordered_spans = sorted(spans)
    if (
        len(tensors) != expected_tensors
        or ordered_spans[0][0] != 0
        or ordered_spans[-1][1] != data_size
        or any(left[1] != right[0] for left, right in pairwise(ordered_spans))
    ):
        raise Mix2KV4LoRAV11Error("resume adapter tensor 집합이 다릅니다.")


def _validate_live_adapter_tensors(path: Path, expected_modules: int) -> None:
    try:
        import torch
        from safetensors.torch import load_file

        tensors = load_file(str(path), device="cpu")
    except Exception as exc:
        raise Mix2KV4LoRAV11Error("R16 adapter tensor를 읽지 못했습니다.") from exc
    pairs: dict[str, dict[str, Any]] = {}
    for name, tensor in tensors.items():
        if name.endswith(".lora_A.weight"):
            prefix, side = name.removesuffix(".lora_A.weight"), "A"
        elif name.endswith(".lora_B.weight"):
            prefix, side = name.removesuffix(".lora_B.weight"), "B"
        else:
            raise Mix2KV4LoRAV11Error("adapter에 LoRA 외 tensor가 있습니다.")
        if side in pairs.setdefault(prefix, {}):
            raise Mix2KV4LoRAV11Error("adapter tensor key가 중복됐습니다.")
        pairs[prefix][side] = tensor
        if (
            tensor.ndim != 2
            or tensor.numel() < 1
            or tensor.dtype not in {torch.bfloat16, torch.float16, torch.float32}
            or not bool(torch.isfinite(tensor).all().item())
        ):
            raise Mix2KV4LoRAV11Error("adapter tensor 값이 다릅니다.")
    if len(pairs) != expected_modules or any(
        set(pair) != {"A", "B"}
        or pair["A"].shape[0] != RANK
        or pair["B"].shape[1] != RANK
        for pair in pairs.values()
    ):
        raise Mix2KV4LoRAV11Error("adapter A/B rank 계약이 다릅니다.")
    resolved_modules = Counter(prefix.rsplit(".", 1)[-1] for prefix in pairs)
    expected_per_module = expected_modules // len(EXPECTED_TARGET_MODULES)
    if resolved_modules != Counter(
        {name: expected_per_module for name in EXPECTED_TARGET_MODULES}
    ):
        raise Mix2KV4LoRAV11Error("adapter all-linear module 집합이 다릅니다.")


def _validated_resume_checkpoint(
    *,
    target: Path,
    trainer_root: Path,
    identity: Mapping[str, Any],
    config: Mapping[str, Any],
    run_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """검증된 R16 checkpoint와 재현 가능한 content fingerprint를 선택한다."""

    expected_steps = int(config["training"]["expected_optimizer_steps"])
    save_steps = int(config["training"]["save_steps"])
    state_path = target / "training_state.json"
    state = (
        core._load_json(state_path, "reviewed-repair running state")
        if state_path.is_file()
        else None
    )
    if state is not None and (
        set(state)
        != {
            "schema_version",
            "status",
            "rank",
            "run_id",
            "identity",
            "resume_checkpoint",
            "started_at_utc",
        }
        or state.get("schema_version") != "1.1.0"
        or state.get("status") != "running"
        or state.get("rank") != RANK
        or state.get("run_id") != run_id
        or state.get("identity") != identity
        or state.get("resume_checkpoint") is not None
        and not _valid_checkpoint_fingerprint(state["resume_checkpoint"])
        or not isinstance(state.get("started_at_utc"), str)
    ):
        raise Mix2KV4LoRAV11Error("기존 reviewed-repair running state가 다릅니다.")

    if not trainer_root.exists():
        return None, None
    core._reject_symlink_components(trainer_root, "reviewed-repair trainer root")
    if trainer_root.is_symlink() or not trainer_root.is_dir():
        raise Mix2KV4LoRAV11Error("reviewed-repair trainer root가 안전하지 않습니다.")
    children = list(trainer_root.iterdir())
    checkpoints: list[tuple[int, Path, dict[str, Any]]] = []
    for path in children:
        if (
            path.is_symlink()
            or not path.is_dir()
            or not path.name.startswith("checkpoint-")
            or not path.name.removeprefix("checkpoint-").isdigit()
        ):
            raise Mix2KV4LoRAV11Error("trainer root에 허용되지 않은 경로가 있습니다.")
        step = int(path.name.removeprefix("checkpoint-"))
        if step < 1 or step >= expected_steps or step % save_steps != 0:
            raise Mix2KV4LoRAV11Error("resume checkpoint 저장 step 계약이 다릅니다.")
        entries = list(path.iterdir())
        if any(item.is_symlink() or not item.is_file() for item in entries):
            raise Mix2KV4LoRAV11Error(
                "resume checkpoint 내부 경로가 안전하지 않습니다."
            )
        names = {item.name for item in entries}
        if not CHECKPOINT_REQUIRED_FILES <= names <= CHECKPOINT_ALLOWED_FILES:
            raise Mix2KV4LoRAV11Error("resume checkpoint 파일 집합이 다릅니다.")
        for item in entries:
            size = item.stat().st_size
            if not 1 <= size <= MAX_CHECKPOINT_FILE_BYTES:
                raise Mix2KV4LoRAV11Error("resume checkpoint 파일 크기가 다릅니다.")
        trainer_state = core._load_json(
            path / "trainer_state.json", "resume trainer state"
        )
        if (
            trainer_state.get("global_step") != step
            or isinstance(trainer_state.get("global_step"), bool)
            or trainer_state.get("max_steps") != expected_steps
        ):
            raise Mix2KV4LoRAV11Error("resume trainer step 계약이 다릅니다.")
        adapter = core._load_json(path / "adapter_config.json", "resume adapter config")
        target_modules = adapter.get("target_modules")
        if (
            adapter.get("peft_type") != "LORA"
            or adapter.get("r") != RANK
            or adapter.get("use_rslora") is not True
            or adapter.get("bias") != config["lora"]["bias"]
            or adapter.get("lora_alpha") != config["lora"]["lora_alpha"]
            or adapter.get("lora_dropout") != config["lora"]["lora_dropout"]
            or adapter.get("task_type") != config["lora"]["task_type"]
            or adapter.get("modules_to_save") is not None
            or not isinstance(target_modules, list)
            or set(target_modules) != EXPECTED_TARGET_MODULES
        ):
            raise Mix2KV4LoRAV11Error("resume adapter 설정 계약이 다릅니다.")
        _validate_adapter_safetensors(
            path / "adapter_model.safetensors",
            config["lora"]["expected_target_linear_modules"] * 2,
        )
        _validate_live_adapter_tensors(
            path / "adapter_model.safetensors",
            config["lora"]["expected_target_linear_modules"],
        )
        for name in (
            "optimizer.pt",
            "rng_state.pth",
            "scheduler.pt",
            "training_args.bin",
        ):
            _validate_torch_archive(path / name, f"resume {name}")
        files = {name: sha256_file(path / name) for name in sorted(names)}
        fingerprint = {
            "checkpoint_name": path.name,
            "step": step,
            "files_sha256": files,
        }
        fingerprint["checkpoint_sha256"] = sha256_bytes(
            canonical_json_bytes(fingerprint)
        )
        checkpoints.append((step, path, fingerprint))
    if checkpoints and state is None:
        raise Mix2KV4LoRAV11Error(
            "resume checkpoint lineage를 증명할 running state가 없습니다."
        )
    if not checkpoints:
        return None, None
    step, selected, fingerprint = max(checkpoints, key=lambda item: item[0])
    recorded = state.get("resume_checkpoint") if state is not None else None
    if isinstance(recorded, Mapping):
        recorded_step = recorded["step"]
        if recorded_step > step or recorded_step == step and recorded != fingerprint:
            raise Mix2KV4LoRAV11Error(
                "기록된 resume checkpoint fingerprint가 다릅니다."
            )
    return str(selected), fingerprint


def _expected_training() -> dict[str, Any]:
    return {
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


def _expected_base_model() -> dict[str, Any]:
    return {
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
        "snapshot_allowlist": {
            ".gitattributes": "bcdabdd4731312282016b3cee5e7a57581ebd5ce198e3eec3860234749cca4f2",
            "LICENSE": "80745468c3213787d08ae8c3b1bc1ffb6b3563d6214ba53626547c39dfcdffd8",
            "README.md": "fe33ea775030c376cbf27f5fde4e474c647467596de55cc2741d1ab2a179c2f6",
            "chat_template.jinja": "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3",
            "config.json": "fe14b20b4b616d62ca0682312c2fcd2b90d9a836d14a1ff6448db3f533fd15a1",
            "configuration_kanana2_tiny.py": "191fb6fbfd63968cc24b3beeb8190aaa88868d4cf1695f8c5a379fb0a077d79d",
            "generation_config.json": "c9737da2d70d630fab5c7ad22a57b6628e60de0f6b8de64787603d71c9acf997",
            "model.safetensors": "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942",
            "modeling_kanana2_tiny.py": "e47cd8cc99e71fc69eea9bf5ba1221526fb8c6d4fc8677177e82de997b766500",
            "tokenizer.json": "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab",
            "tokenizer_config.json": "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5",
        },
    }


def _validate_closed_model_snapshot(
    config: Mapping[str, Any], model_root: Path
) -> dict[str, str]:
    """Transformers가 볼 수 있는 K0 snapshot 전체를 고정 allowlist로 검증한다."""

    core._validate_model_snapshot(config, model_root)
    allowed = config["base_model"]["snapshot_allowlist"]
    entries = list(model_root.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in entries) or {
        item.name for item in entries
    } != set(allowed):
        raise Mix2KV4LoRAV11Error("K0 base snapshot 파일 allowlist가 다릅니다.")
    observed = {item.name: sha256_file(item) for item in entries}
    if observed != allowed:
        raise Mix2KV4LoRAV11Error("K0 base snapshot 전체 hash가 다릅니다.")
    return dict(sorted(observed.items()))


def _expected_runtime() -> dict[str, Any]:
    return {
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


def _validate_config(path: Path) -> dict[str, Any]:
    config = core._load_json(path, "reviewed-repair LoRA config")
    required = config.get("required_data")
    pins = required.get("pins") if isinstance(required, Mapping) else None
    training_core = config.get("training_core")
    lora = config.get("lora")
    governance = config.get("governance")
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("experiment_id") != "K0-MIX2K-V4-REVIEWED-REPAIR-LORA-R16"
        or config.get("training_version") != "v1.1.0"
        or config.get("dataset_version") != DATASET_VERSION
        or config.get("base_model") != _expected_base_model()
        or not isinstance(required, Mapping)
        or required.get("manifest_name") != "build_manifest.json"
        or required.get("manifest_schema_version") != "1.1.0"
        or required.get("artifact_revision") != "v1.1.0"
        or required.get("source_config_path")
        != "configs/data_versions/saju_1b_baseline/mix2k-v4-reviewed-repair-v1.1.0.json"
        or required.get("generator_path") != "scripts/data/mix2k_v4_reviewed_repair.py"
        or required.get("rows") != EXPECTED_ROWS
        or required.get("training_path") != "training/train_2000.jsonl"
        or required.get("token_audit_rows_path") != "reports/token_audit_2000.jsonl"
        or required.get("token_audit_summary_path")
        != "reports/token_audit_summary.json"
        or required.get("selected_max_length") != 2048
        or not isinstance(pins, Mapping)
        or set(pins) != PIN_FIELDS
        or not isinstance(training_core, Mapping)
        or set(training_core) != {"path", "sha256"}
        or training_core.get("path") != "scripts/training/mix2k_v4_lora.py"
        or not _is_sha256(training_core.get("sha256"))
        or not isinstance(lora, Mapping)
        or lora.get("ranks") != [RANK]
        or lora.get("primary_rank") != RANK
        or lora.get("target_modules") != "all-linear"
        or lora.get("use_rslora") is not True
        or lora.get("bias") != "none"
        or lora.get("lora_dropout") != 0.05
        or lora.get("lora_alpha") != 32
        or lora.get("alpha_policy") != "fixed_r16_experiment"
        or lora.get("task_type") != "CAUSAL_LM"
        or lora.get("modules_to_save") is not None
        or lora.get("expected_target_linear_modules") != 224
        or lora.get("expected_trainable_parameters") != {"16": 18_677_760}
        or lora.get("base_weights_frozen") is not True
        or lora.get("adapter_only_save") is not True
        or config.get("training") != _expected_training()
        or config.get("runtime_versions") != _expected_runtime()
        or config.get("preflight")
        != {
            "required_for_rank": RANK,
            "longest_rows": 8,
            "optimizer_steps": 1,
            "require_finite_loss": True,
            "require_finite_nonzero_lora_gradient": True,
            "require_base_gradient_absent": True,
            "require_adapter_reload_match": True,
            "require_peak_memory_within_limit": True,
        }
        or not isinstance(governance, Mapping)
        or governance.get("explicit_user_confirmation_received") is not True
        or governance.get("confirmation_date") != "2026-09-04"
        or governance.get("confirmation_scope")
        != "k0_mix2k_v4_reviewed_repair_v1_1_lora_r16_one_epoch"
        or governance.get("training_scope") != "lora_r16_experimental_only"
        or governance.get("ki20_training_allowed") is not False
        or governance.get("full_fine_tuning_allowed") is not False
        or governance.get("partial_fine_tuning_allowed") is not False
        or governance.get("production_promotion_allowed") is not False
        or governance.get("sealed_blind_accessed") is not False
        or config.get("operational_limits")
        != {
            "expected_gpu_count": 1,
            "max_total_gpu_memory_used_mib": 16_384,
            "min_free_gpu_memory_before_start_mib": 12_000,
            "min_system_ram_available_bytes": 4_294_967_296,
            "min_disk_available_bytes": 32_212_254_720,
            "require_no_active_compute_process_before_start": True,
            "run_ranks_sequentially": True,
        }
        or config.get("outputs")
        != {
            "preflight_root": (
                "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/"
                "lora-preflight/v1.1.0/{rank}/{run_build_id}"
            ),
            "run_root": (
                "runs/K0-MIX2K-V4-REVIEWED-REPAIR-LORA/v1.1.0/{rank}/{run_build_id}"
            ),
        }
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair LoRA 고정 계약이 다릅니다.")

    artifact_pins = pins.get("artifact_sha256")
    if not isinstance(artifact_pins, Mapping) or set(artifact_pins) != ARTIFACT_PATHS:
        raise Mix2KV4LoRAV11Error("reviewed-repair artifact pin 집합이 다릅니다.")
    pin_state = required.get("pin_state")
    scalar_pins = [pins.get(field) for field in PIN_FIELDS - {"artifact_sha256"}]
    if pin_state == UNPINNED:
        if any(value is not None for value in scalar_pins) or any(
            value is not None for value in artifact_pins.values()
        ):
            raise Mix2KV4LoRAV11Error("unpinned config에 부분 pin이 있습니다.")
    elif pin_state == PINNED:
        if any(
            not _is_sha256(pins.get(field))
            for field in (
                "source_config_sha256",
                "generator_sha256",
                "final_build_sha256",
                "final_manifest_sha256",
            )
        ):
            raise Mix2KV4LoRAV11Error("reviewed-repair scalar pin이 잘못됐습니다.")
        build_sha = pins.get("final_build_sha256")
        if (
            not isinstance(pins.get("final_build_id"), str)
            or pins["final_build_id"] != f"build-{str(build_sha)[:12]}"
            or any(not _is_sha256(value) for value in artifact_pins.values())
        ):
            raise Mix2KV4LoRAV11Error("reviewed-repair final build pin이 잘못됐습니다.")
    else:
        raise Mix2KV4LoRAV11Error("알 수 없는 reviewed-repair pin state입니다.")

    core_path = REPO_ROOT / str(training_core["path"])
    if core_path.is_symlink() or sha256_file(core_path) != training_core["sha256"]:
        raise Mix2KV4LoRAV11Error("공유 LoRA training core hash가 다릅니다.")
    runtime = config.get("runtime_versions")
    if not isinstance(runtime, Mapping):
        raise Mix2KV4LoRAV11Error("LoRA runtime 계약이 없습니다.")
    for item in (runtime.get("base_lock"), runtime.get("lora_overlay")):
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise Mix2KV4LoRAV11Error("LoRA package lock 계약이 다릅니다.")
        lock_path = REPO_ROOT / str(item["path"])
        if lock_path.is_symlink() or sha256_file(lock_path) != item["sha256"]:
            raise Mix2KV4LoRAV11Error("LoRA package lock hash가 다릅니다.")
    return config


def _require_pinned_data(config: Mapping[str, Any]) -> Mapping[str, Any]:
    required = config["required_data"]
    if required.get("pin_state") != PINNED:
        raise Mix2KV4LoRAV11Error(
            "reviewed-repair final build가 아직 unpinned이므로 preflight/train을 실행할 수 없습니다."
        )
    return required["pins"]


def _stats(values: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        raise Mix2KV4LoRAV11Error("token 통계 대상이 비었습니다.")
    return {
        "minimum": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p90": ordered[math.ceil(len(ordered) * 0.9) - 1],
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "maximum": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 3),
    }


def _valid_training_row(row: Any) -> bool:
    if not isinstance(row, Mapping) or set(row) != TRAIN_ROW_FIELDS:
        return False
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
    axis = row.get("task_axis")
    runtime_hash = row.get("runtime_snapshot_sha256")
    return bool(
        row.get("schema_version") == "1.1.0"
        and row.get("dataset_version") == DATASET_VERSION
        and isinstance(row.get("id"), str)
        and row["id"]
        and axis in EXPECTED_AXES
        and row.get("assistant_only_loss") is True
        and row.get("restricted_local_only") is False
        and isinstance(messages, list)
        and len(messages) >= 3
        and len(messages) % 2 == 1
        and roles == expected_roles
        and all(
            set(message) == {"role", "content"}
            and isinstance(message.get("content"), str)
            and bool(message["content"].strip())
            for message in messages
        )
        and (
            _is_sha256(runtime_hash)
            if axis in RUNTIME_AXES
            else (
                runtime_hash is None or _is_sha256(runtime_hash)
                if axis == "uncertainty_blocked_boundary"
                else runtime_hash is None
            )
        )
    )


def _validate_token_summary(
    summary: Mapping[str, Any], token_rows: Sequence[dict[str, Any]]
) -> None:
    rendered = [int(row["rendered_tokens"]) for row in token_rows]
    prompt = [int(row["prompt_tokens"]) for row in token_rows]
    supervised = [int(row["supervised_assistant_tokens"]) for row in token_rows]
    candidate = summary.get("candidate_validation")
    comparison = summary.get("parent_comparison")
    if (
        set(summary) != TOKEN_SUMMARY_FIELDS
        or summary.get("schema_version") != "1.0.0"
        or summary.get("rows") != EXPECTED_ROWS
        or summary.get("rendered_tokens") != _stats(rendered)
        or summary.get("prompt_tokens") != _stats(prompt)
        or summary.get("supervised_assistant_tokens") != _stats(supervised)
        or summary.get("rows_over_2048") != sum(value > 2048 for value in rendered)
        or summary.get("rows_over_3584") != sum(value > 3584 for value in rendered)
        or summary.get("rows_over_4096") != sum(value > 4096 for value in rendered)
        or summary.get("rows_over_8192") != sum(value > 8192 for value in rendered)
        or summary.get("truncated_rows") != 0
        or summary.get("zero_assistant_mask_rows") != 0
        or summary.get("missing_supervised_eos_rows") != 0
        or summary.get("user_system_loss_leakage_rows") != 0
        or summary.get("selected_max_length") != 2048
        or summary.get("provisional_ladder_value") != 2048
        or summary.get("many_rows_over_2048") is not False
        or summary.get("runtime_projection_review_required") is not False
        or summary.get("training_blocked_pending_projection_review") is not False
        or not isinstance(candidate, Mapping)
        or candidate.get("rows") != EXPECTED_ROWS
        or candidate.get("axes") != dict(sorted(EXPECTED_AXES.items()))
        or candidate.get("origins")
        != {"parent_v1.0.1": 1600, "regenerated_v1.1.0": 400}
        or candidate.get("repaired_cross_provider_pass_rows") != 400
        or candidate.get("all_2000_rows_cross_provider_contract_met") is not False
        or not isinstance(comparison, Mapping)
        or comparison.get("schema_version") != "1.0.0"
        or comparison.get("comparison") != "repo_native_v1.1.0_minus_parent_v1.0.1"
        or comparison.get("rows") != EXPECTED_ROWS
        or comparison.get("audit_provenance_removed_from_model_context") is not False
        or comparison.get("compact_projection_used") is not False
        or comparison.get("production_like_format_preserved") is not True
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair token audit summary가 다릅니다.")


def _validate_data_build(
    data_build: Path, config: Mapping[str, Any]
) -> tuple[
    dict[str, Any],
    str,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    pins = _require_pinned_data(config)
    core._reject_symlink_components(data_build, "reviewed-repair final build")
    if (
        not data_build.is_absolute()
        or data_build.is_symlink()
        or not data_build.is_dir()
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair final build가 안전하지 않습니다.")
    required = config["required_data"]
    manifest, manifest_sha = core._load_json_snapshot(
        data_build / required["manifest_name"], "reviewed-repair final manifest"
    )
    identity = manifest.get("identity")
    artifact_hashes = manifest.get("artifact_sha256")
    if (
        manifest_sha != pins["final_manifest_sha256"]
        or set(manifest) != FINAL_MANIFEST_FIELDS
        or not isinstance(identity, Mapping)
        or set(identity) != FINAL_IDENTITY_FIELDS
        or not isinstance(artifact_hashes, Mapping)
        or set(artifact_hashes) != ARTIFACT_PATHS
        or artifact_hashes != pins["artifact_sha256"]
        or identity.get("artifact_sha256") != artifact_hashes
        or identity.get("dataset_version") != DATASET_VERSION
        or identity.get("artifact_revision") != "v1.1.0"
        or identity.get("config_sha256") != pins["source_config_sha256"]
        or identity.get("generator_sha256") != pins["generator_sha256"]
        or identity.get("base_model_files") != config["base_model"]["files"]
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair final identity가 다릅니다.")
    calculated = sha256_bytes(canonical_json_bytes(identity))
    if (
        manifest.get("schema_version") != "1.1.0"
        or manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("artifact_revision") != "v1.1.0"
        or manifest.get("build_id") != data_build.name
        or manifest.get("build_id") != pins["final_build_id"]
        or manifest.get("build_id") != f"build-{calculated[:12]}"
        or manifest.get("build_sha256") != calculated
        or calculated != pins["final_build_sha256"]
        or manifest.get("rows") != EXPECTED_ROWS
        or manifest.get("axes") != dict(sorted(EXPECTED_AXES.items()))
        or manifest.get("selected_max_length") != 2048
        or manifest.get("assistant_only_loss") is not True
        or manifest.get("truncation") is not False
        or manifest.get("full_runtime_snapshot_used") is not True
        or manifest.get("compact_projection_used_for_training") is not False
        or manifest.get("inherited_parent_rows") != 1600
        or manifest.get("regenerated_cross_provider_rows") != 400
        or manifest.get("all_2000_rows_cross_provider_contract_met") is not False
        or manifest.get("development_targets_accessed") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("training_execution_allowed") is not True
        or manifest.get("lora_r16_experimental_training_allowed") is not True
        or manifest.get("training_performed") is not False
        or manifest.get("production_promotion_allowed") is not False
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair final 학습 Gate가 다릅니다.")
    source_config = REPO_ROOT / required["source_config_path"]
    generator = REPO_ROOT / required["generator_path"]
    if (
        sha256_file(source_config) != pins["source_config_sha256"]
        or sha256_file(generator) != pins["generator_sha256"]
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair 생성 입력·코드 hash가 다릅니다.")

    snapshots: dict[str, tuple[Any, str]] = {}
    for relative in ARTIFACT_PATHS:
        path = data_build / relative
        if relative.endswith(".jsonl"):
            snapshots[relative] = core._load_jsonl_snapshot(path, relative)
        else:
            snapshots[relative] = core._load_json_snapshot(path, relative)
        if snapshots[relative][1] != artifact_hashes[relative]:
            raise Mix2KV4LoRAV11Error(
                f"reviewed-repair artifact hash가 다릅니다: {relative}"
            )
    rows = snapshots[required["training_path"]][0]
    token_rows = snapshots[required["token_audit_rows_path"]][0]
    summary = snapshots[required["token_audit_summary_path"]][0]
    if len(rows) != EXPECTED_ROWS or len(token_rows) != EXPECTED_ROWS:
        raise Mix2KV4LoRAV11Error(
            "reviewed-repair train/token audit가 2,000행이 아닙니다."
        )
    ids: set[str] = set()
    axes: Counter[str] = Counter()
    for row, token_row in zip(rows, token_rows, strict=True):
        record_id = row.get("id") if isinstance(row, Mapping) else None
        if (
            not _valid_training_row(row)
            or record_id in ids
            or not core._valid_token_audit_row(token_row, 2048)
            or token_row.get("id") != record_id
            or token_row.get("task_axis") != row.get("task_axis")
        ):
            raise Mix2KV4LoRAV11Error(
                f"reviewed-repair train/token row 계약이 다릅니다: {record_id}"
            )
        ids.add(record_id)
        axes[row["task_axis"]] += 1
    if dict(axes) != EXPECTED_AXES:
        raise Mix2KV4LoRAV11Error("reviewed-repair training axis 분포가 다릅니다.")
    _validate_token_summary(summary, token_rows)
    return manifest, manifest_sha, rows, token_rows


def _run_identity(
    *,
    mode: str,
    config_path: Path,
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    data_manifest_sha256: str,
    preflight_manifest_sha256: str | None = None,
) -> tuple[str, dict[str, Any]]:
    identity: dict[str, Any] = {
        "mode": mode,
        "config_sha256": sha256_file(config_path),
        "data_manifest_sha256": data_manifest_sha256,
        "data_build_sha256": data_manifest["build_sha256"],
        "runner_sha256": sha256_file(SCRIPT_PATH),
        "training_core_sha256": config["training_core"]["sha256"],
        "base_model_files": config["base_model"]["files"],
        "base_model_snapshot_files": config["base_model"]["snapshot_allowlist"],
        "rank": RANK,
    }
    if mode == "train":
        if not _is_sha256(preflight_manifest_sha256):
            raise Mix2KV4LoRAV11Error(
                "train identity에 preflight manifest hash가 없습니다."
            )
        identity["preflight_manifest_sha256"] = preflight_manifest_sha256
    digest = sha256_bytes(canonical_json_bytes(identity))
    return f"{mode}-{digest[:12]}", identity


def _target(
    config: Mapping[str, Any], artifact_root: Path, key: str, run_id: str
) -> Path:
    relative = config["outputs"][key].format(rank="r16", run_build_id=run_id)
    target = artifact_root / relative
    core._reject_symlink_components(target, "reviewed-repair LoRA target")
    return target


def _selection(
    rows: Sequence[dict[str, Any]], token_rows: Sequence[dict[str, Any]], count: int
) -> tuple[list[dict[str, Any]], list[str], str]:
    selected = core._longest_rows(rows, token_rows, count)
    token_by_id = {row["id"]: row for row in token_rows}
    selected_ids = [row["id"] for row in selected]
    selected_audit = [token_by_id[record_id] for record_id in selected_ids]
    digest = sha256_bytes(canonical_json_bytes(selected_audit))
    return selected, selected_ids, digest


def _validate_preflight_manifest(
    *,
    value: Mapping[str, Any],
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    data_manifest_sha256: str,
    target: Path,
    run_id: str,
    identity: Mapping[str, Any],
    runtime: Mapping[str, str],
    selected_ids: Sequence[str],
    selection_sha256: str,
    model_root: Path,
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
        or value.get("schema_version") != "1.1.0"
        or value.get("status") != "preflight_passed"
        or value.get("passed") is not True
        or value.get("rank") != RANK
        or value.get("run_id") != run_id
        or value.get("identity") != identity
        or value.get("runtime_versions") != dict(runtime)
        or value.get("data_build_id") != data_manifest["build_id"]
        or value.get("data_build_sha256") != data_manifest["build_sha256"]
        or value.get("data_manifest_sha256") != data_manifest_sha256
        or value.get("max_length") != 2048
        or value.get("rows") != 8
        or value.get("selected_row_ids") != list(selected_ids)
        or value.get("selected_token_audit_sha256") != selection_sha256
        or not core._valid_hardware_manifest(value.get("hardware_before"), config)
        or value.get("base_model_files_unchanged") is not True
        or value.get("base_weights_unchanged") is not True
        or value.get("adapter_only") is not True
        or any(value.get(field) is not False for field in false_fields)
        or not isinstance(value.get("completed_at_utc"), str)
        or not value["completed_at_utc"]
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair preflight manifest가 다릅니다.")
    core._validate_adapter_metrics(
        metrics=value.get("metrics"),
        config=config,
        target=target,
        rank=RANK,
        expected_steps=1,
    )
    _validate_final_adapter_artifact(
        config=config,
        target=target,
        model_root=model_root,
    )


def _required_preflight(
    *,
    config_path: Path,
    config: Mapping[str, Any],
    artifact_root: Path,
    data_manifest: Mapping[str, Any],
    data_manifest_sha256: str,
    rows: Sequence[dict[str, Any]],
    token_rows: Sequence[dict[str, Any]],
    runtime: Mapping[str, str],
    model_root: Path,
) -> tuple[dict[str, Any], str]:
    run_id, identity = _run_identity(
        mode="preflight",
        config_path=config_path,
        config=config,
        data_manifest=data_manifest,
        data_manifest_sha256=data_manifest_sha256,
    )
    target = _target(config, artifact_root, "preflight_root", run_id)
    path = target / "preflight_manifest.json"
    value, digest = core._load_json_snapshot(path, "required reviewed-repair preflight")
    _, selected_ids, selection_sha = _selection(rows, token_rows, 8)
    _validate_preflight_manifest(
        value=value,
        config=config,
        data_manifest=data_manifest,
        data_manifest_sha256=data_manifest_sha256,
        target=target,
        run_id=run_id,
        identity=identity,
        runtime=runtime,
        selected_ids=selected_ids,
        selection_sha256=selection_sha,
        model_root=model_root,
    )
    return value, digest


def run_preflight(
    *,
    config_path: Path,
    data_build: Path,
    model_root: Path,
    artifact_root: Path,
    execute: bool,
) -> dict[str, Any]:
    config = _validate_config(config_path)
    _require_pinned_data(config)
    core._validate_directory(artifact_root, "artifact root")
    base_before = _validate_closed_model_snapshot(config, model_root)
    runtime = core._runtime_versions(config)
    manifest, manifest_sha, rows, token_rows = _validate_data_build(data_build, config)
    run_id, identity = _run_identity(
        mode="preflight",
        config_path=config_path,
        config=config,
        data_manifest=manifest,
        data_manifest_sha256=manifest_sha,
    )
    selected, selected_ids, selection_sha = _selection(rows, token_rows, 8)
    target = _target(config, artifact_root, "preflight_root", run_id)
    if not execute:
        return {
            "status": "preflight_dry_run",
            "rank": RANK,
            "run_id": run_id,
            "rows": len(selected),
            "max_length": 2048,
            "execute_required": True,
        }
    core._ensure_private_directory(target.parent, "reviewed-repair preflight parent")
    lock_path = target.parent / f".{run_id}.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    global_descriptor: int | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAV11Error(
                "reviewed-repair preflight가 이미 실행 중입니다."
            ) from exc
        global_descriptor = core.acquire_mix2k_v4_gpu_lock(artifact_root)
        hardware = core._hardware_gate(config, target.parent)
        manifest_path = target / "preflight_manifest.json"
        if manifest_path.is_file():
            value = core._load_json(manifest_path, "reviewed-repair preflight manifest")
            _validate_preflight_manifest(
                value=value,
                config=config,
                data_manifest=manifest,
                data_manifest_sha256=manifest_sha,
                target=target,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
                selected_ids=selected_ids,
                selection_sha256=selection_sha,
                model_root=model_root,
            )
            return {**value, "mode": "reused", "path": str(target)}
        temporary = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=target.parent))
        temporary.chmod(0o700)
        try:
            metrics = core._execute_trainer(
                config=config,
                rows=selected,
                rank=RANK,
                max_length=2048,
                model_root=model_root,
                output_dir=temporary / "trainer",
                preflight=True,
                resume_from_checkpoint=None,
            )
            base_unchanged = (
                _validate_closed_model_snapshot(config, model_root) == base_before
            )
            value = {
                "schema_version": "1.1.0",
                "status": "preflight_passed",
                "passed": True,
                "rank": RANK,
                "run_id": run_id,
                "identity": identity,
                "runtime_versions": runtime,
                "data_build_id": manifest["build_id"],
                "data_build_sha256": manifest["build_sha256"],
                "data_manifest_sha256": manifest_sha,
                "max_length": 2048,
                "rows": len(selected),
                "selected_row_ids": selected_ids,
                "selected_token_audit_sha256": selection_sha,
                "hardware_before": hardware,
                "metrics": metrics,
                "base_model_files_unchanged": base_unchanged,
                "base_weights_unchanged": base_unchanged,
                "adapter_only": True,
                "full_fine_tuning_performed": False,
                "ki20_training_performed": False,
                "training_performed": False,
                "production_promotion_allowed": False,
                "sealed_blind_accessed": False,
                "completed_at_utc": _utc_now(),
            }
            _validate_preflight_manifest(
                value=value,
                config=config,
                data_manifest=manifest,
                data_manifest_sha256=manifest_sha,
                target=temporary,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
                selected_ids=selected_ids,
                selection_sha256=selection_sha,
                model_root=model_root,
            )
            core._atomic_write(
                temporary / "preflight_manifest.json", core._json_bytes(value)
            )
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return {**value, "mode": "created", "path": str(target)}
    finally:
        if global_descriptor is not None:
            os.close(global_descriptor)
        os.close(descriptor)


def _validate_final_adapter_artifact(
    *, config: Mapping[str, Any], target: Path, model_root: Path
) -> None:
    """완료·reuse 모두에서 adapter를 실제 tensor까지 다시 검증한다."""

    final_adapter = target / "trainer/final_adapter"
    adapter_path = final_adapter / "adapter_model.safetensors"
    adapter_config_path = final_adapter / "adapter_config.json"
    adapter = core._load_json(
        adapter_config_path, "reviewed-repair final adapter config"
    )
    target_modules = adapter.get("target_modules")
    if (
        adapter.get("peft_type") != "LORA"
        or adapter.get("task_type") != "CAUSAL_LM"
        or adapter.get("r") != RANK
        or adapter.get("lora_alpha") != 32
        or adapter.get("lora_dropout") != 0.05
        or adapter.get("bias") != "none"
        or adapter.get("use_rslora") is not True
        or adapter.get("modules_to_save") is not None
        or adapter.get("inference_mode") is not True
        or adapter.get("use_dora") is not False
        or adapter.get("use_qalora") is not False
        or adapter.get("rank_pattern") != {}
        or adapter.get("alpha_pattern") != {}
        or adapter.get("exclude_modules") is not None
        or not isinstance(target_modules, list)
        or len(target_modules) != len(EXPECTED_TARGET_MODULES)
        or set(target_modules) != EXPECTED_TARGET_MODULES
        or _absolute(Path(str(adapter.get("base_model_name_or_path"))))
        != _absolute(model_root)
    ):
        raise Mix2KV4LoRAV11Error("완료된 R16 adapter config 계약이 다릅니다.")
    _validate_adapter_safetensors(
        adapter_path,
        config["lora"]["expected_target_linear_modules"] * 2,
    )
    _validate_live_adapter_tensors(
        adapter_path,
        config["lora"]["expected_target_linear_modules"],
    )


def _validate_training_manifest(
    *,
    value: Mapping[str, Any],
    config: Mapping[str, Any],
    data_manifest: Mapping[str, Any],
    data_manifest_sha256: str,
    target: Path,
    run_id: str,
    identity: Mapping[str, Any],
    runtime: Mapping[str, str],
    preflight: Mapping[str, Any],
    preflight_sha256: str,
    model_root: Path,
) -> None:
    false_fields = (
        "full_fine_tuning_performed",
        "ki20_training_performed",
        "production_promotion_allowed",
        "sealed_blind_accessed",
    )
    if (
        set(value) != TRAINING_MANIFEST_FIELDS
        or value.get("schema_version") != "1.1.0"
        or value.get("status") != "training_completed"
        or value.get("completed") is not True
        or value.get("rank") != RANK
        or value.get("run_id") != run_id
        or value.get("identity") != identity
        or value.get("runtime_versions") != dict(runtime)
        or value.get("data_build_id") != data_manifest["build_id"]
        or value.get("data_build_sha256") != data_manifest["build_sha256"]
        or value.get("data_manifest_sha256") != data_manifest_sha256
        or value.get("preflight_run_id") != preflight["run_id"]
        or value.get("preflight_manifest_sha256") != preflight_sha256
        or value.get("max_length") != 2048
        or value.get("rows") != EXPECTED_ROWS
        or not core._valid_hardware_manifest(value.get("hardware_before"), config)
        or value.get("base_model_files_unchanged") is not True
        or value.get("base_weights_unchanged") is not True
        or value.get("adapter_only") is not True
        or value.get("resume_checkpoint") is not None
        and not _valid_checkpoint_fingerprint(value["resume_checkpoint"])
        or value.get("num_train_epochs") != 1
        or any(value.get(field) is not False for field in false_fields)
        or not isinstance(value.get("completed_at_utc"), str)
        or not value["completed_at_utc"]
    ):
        raise Mix2KV4LoRAV11Error("reviewed-repair training manifest가 다릅니다.")
    core._validate_adapter_metrics(
        metrics=value.get("metrics"),
        config=config,
        target=target,
        rank=RANK,
        expected_steps=250,
    )
    _validate_final_adapter_artifact(
        config=config,
        target=target,
        model_root=model_root,
    )


def run_training(
    *,
    config_path: Path,
    data_build: Path,
    model_root: Path,
    artifact_root: Path,
    execute: bool,
) -> dict[str, Any]:
    config = _validate_config(config_path)
    _require_pinned_data(config)
    core._validate_directory(artifact_root, "artifact root")
    base_before = _validate_closed_model_snapshot(config, model_root)
    runtime = core._runtime_versions(config)
    manifest, manifest_sha, rows, token_rows = _validate_data_build(data_build, config)
    preflight, preflight_sha = _required_preflight(
        config_path=config_path,
        config=config,
        artifact_root=artifact_root,
        data_manifest=manifest,
        data_manifest_sha256=manifest_sha,
        rows=rows,
        token_rows=token_rows,
        runtime=runtime,
        model_root=model_root,
    )
    run_id, identity = _run_identity(
        mode="train",
        config_path=config_path,
        config=config,
        data_manifest=manifest,
        data_manifest_sha256=manifest_sha,
        preflight_manifest_sha256=preflight_sha,
    )
    target = _target(config, artifact_root, "run_root", run_id)
    if not execute:
        return {
            "status": "training_dry_run",
            "rank": RANK,
            "run_id": run_id,
            "rows": len(rows),
            "max_length": 2048,
            "preflight_run_id": preflight["run_id"],
            "execute_required": True,
        }
    core._ensure_private_directory(target, "reviewed-repair training target")
    lock_path = target / ".training.lock"
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    global_descriptor: int | None = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4LoRAV11Error(
                "reviewed-repair training이 이미 실행 중입니다."
            ) from exc
        global_descriptor = core.acquire_mix2k_v4_gpu_lock(artifact_root)
        hardware = core._hardware_gate(config, target.parent)
        manifest_path = target / "training_manifest.json"
        if manifest_path.is_file():
            value = core._load_json(manifest_path, "reviewed-repair training manifest")
            _validate_training_manifest(
                value=value,
                config=config,
                data_manifest=manifest,
                data_manifest_sha256=manifest_sha,
                target=target,
                run_id=run_id,
                identity=identity,
                runtime=runtime,
                preflight=preflight,
                preflight_sha256=preflight_sha,
                model_root=model_root,
            )
            return {**value, "mode": "reused", "path": str(target)}
        trainer_root = target / "trainer"
        core._reject_symlink_components(trainer_root, "reviewed-repair trainer root")
        if trainer_root.exists() and (
            trainer_root.is_symlink() or not trainer_root.is_dir()
        ):
            raise Mix2KV4LoRAV11Error(
                "reviewed-repair trainer root가 안전하지 않습니다."
            )
        resume, resume_checkpoint = _validated_resume_checkpoint(
            target=target,
            trainer_root=trainer_root,
            identity=identity,
            config=config,
            run_id=run_id,
        )
        core._atomic_write(
            target / "training_state.json",
            core._json_bytes(
                {
                    "schema_version": "1.1.0",
                    "status": "running",
                    "rank": RANK,
                    "run_id": run_id,
                    "identity": identity,
                    "resume_checkpoint": resume_checkpoint,
                    "started_at_utc": _utc_now(),
                }
            ),
        )
        metrics = core._execute_trainer(
            config=config,
            rows=rows,
            rank=RANK,
            max_length=2048,
            model_root=model_root,
            output_dir=trainer_root,
            preflight=False,
            resume_from_checkpoint=resume,
        )
        base_unchanged = (
            _validate_closed_model_snapshot(config, model_root) == base_before
        )
        if metrics.get("global_step") != 250:
            raise Mix2KV4LoRAV11Error("reviewed-repair training step이 250이 아닙니다.")
        value = {
            "schema_version": "1.1.0",
            "status": "training_completed",
            "completed": True,
            "rank": RANK,
            "run_id": run_id,
            "identity": identity,
            "runtime_versions": runtime,
            "data_build_id": manifest["build_id"],
            "data_build_sha256": manifest["build_sha256"],
            "data_manifest_sha256": manifest_sha,
            "preflight_run_id": preflight["run_id"],
            "preflight_manifest_sha256": preflight_sha,
            "max_length": 2048,
            "rows": len(rows),
            "hardware_before": hardware,
            "metrics": metrics,
            "base_model_files_unchanged": base_unchanged,
            "base_weights_unchanged": base_unchanged,
            "adapter_only": True,
            "resume_checkpoint": resume_checkpoint,
            "num_train_epochs": 1,
            "full_fine_tuning_performed": False,
            "ki20_training_performed": False,
            "production_promotion_allowed": False,
            "sealed_blind_accessed": False,
            "completed_at_utc": _utc_now(),
        }
        _validate_training_manifest(
            value=value,
            config=config,
            data_manifest=manifest,
            data_manifest_sha256=manifest_sha,
            target=target,
            run_id=run_id,
            identity=identity,
            runtime=runtime,
            preflight=preflight,
            preflight_sha256=preflight_sha,
            model_root=model_root,
        )
        core._atomic_write(manifest_path, core._json_bytes(value))
        core._atomic_write(
            target / "training_state.json",
            core._json_bytes(
                {
                    "schema_version": "1.0.0",
                    "status": "completed",
                    "rank": RANK,
                    "run_id": run_id,
                    "resume_checkpoint": resume_checkpoint,
                    "completed_at_utc": value["completed_at_utc"],
                }
            ),
        )
        return {**value, "mode": "created", "path": str(target)}
    finally:
        if global_descriptor is not None:
            os.close(global_descriptor)
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="K0 MIX2K v4 reviewed-repair v1.1 LoRA R16 preflight/training"
    )
    parser.add_argument("command", choices=("validate-contract", "preflight", "train"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--data-build", type=Path)
    parser.add_argument("--model-snapshot", type=Path, default=DEFAULT_MODEL_SNAPSHOT)
    parser.add_argument("--artifact-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = _absolute(args.config)
        config = _validate_config(config_path)
        if args.command == "validate-contract":
            pinned = config["required_data"]["pin_state"] == PINNED
            result: Any = {
                "status": "valid_pinned" if pinned else "valid_unpinned",
                "rank": RANK,
                "training_execution_allowed": pinned,
                "production_promotion_allowed": False,
                "config_sha256": sha256_file(config_path),
            }
        else:
            if args.data_build is None:
                raise Mix2KV4LoRAV11Error(
                    "preflight/train에 --data-build가 필요합니다."
                )
            kwargs = {
                "config_path": config_path,
                "data_build": _absolute(args.data_build),
                "model_root": _absolute(args.model_snapshot),
                "artifact_root": _absolute(args.artifact_root),
                "execute": args.execute,
            }
            result = (
                run_preflight(**kwargs)
                if args.command == "preflight"
                else run_training(**kwargs)
            )
    except (Mix2KV4LoRAV11Error, core.Mix2KV4LoRAError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
