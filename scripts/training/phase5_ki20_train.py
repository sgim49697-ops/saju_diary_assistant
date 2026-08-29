# phase5_ki20_train.py - 승인된 KI20 1 epoch Full FT를 시작·재개·검증한다.

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_json,
    read_jsonl,
    sha256_file,
    sha256_json,
)
from scripts.training import phase5_train as legacy

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-training-v1.2.0.json"
)
REGISTRY = Path("configs/data_versions/saju_1b_baseline/registry.json")
RUN_ID = "KI20-MIX-v2"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644


class Phase5KI20TrainingError(RuntimeError):
    """KI20 v1.2 본학습 실행 계약 위반."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _assert_file(
    repo_root: Path, value: dict[str, Any], *, path_key: str, hash_key: str, label: str
) -> Path:
    path = legacy._safe_path(repo_root, str(value.get(path_key, "")))
    if not path.is_file() or sha256_file(path) != value.get(hash_key):
        raise Phase5KI20TrainingError(f"{label} SHA-256이 다릅니다.")
    return path


def _gpu_snapshot() -> dict[str, Any]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,driver_version",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode != 0 or len(rows) != 1:
        raise Phase5KI20TrainingError("단일 GPU의 nvidia-smi 상태를 확인할 수 없습니다.")
    fields = [value.strip() for value in rows[0].split(",")]
    if len(fields) != 7:
        raise Phase5KI20TrainingError("nvidia-smi GPU 필드 수가 다릅니다.")
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
        raise Phase5KI20TrainingError("nvidia-smi GPU 수치 형식이 다릅니다.") from exc


def _compute_processes() -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Phase5KI20TrainingError("nvidia-smi compute process 조회가 실패했습니다.")
    values: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        fields = [value.strip() for value in line.split(",")]
        if len(fields) != 3:
            raise Phase5KI20TrainingError("nvidia-smi compute process 형식이 다릅니다.")
        try:
            values.append(
                {
                    "pid": int(fields[0]),
                    "process_name": fields[1],
                    "used_gpu_memory_mib": int(fields[2]),
                }
            )
        except ValueError as exc:
            raise Phase5KI20TrainingError(
                "nvidia-smi compute process 수치 형식이 다릅니다."
            ) from exc
    return values


def _validate_fixed_contract(config: dict[str, Any], repo_root: Path) -> None:
    if (
        config.get("schema_version") != "1.2.0"
        or config.get("canonical_plan_version") != "3.4.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("training_version") != "v1.2.0"
        or config.get("run_id") != RUN_ID
    ):
        raise Phase5KI20TrainingError("KI20 v1.2 identity가 다릅니다.")

    readiness = config.get("required_readiness")
    if (
        not isinstance(readiness, dict)
        or readiness.get("version") != "v1.3.0"
        or readiness.get("build_id") != "build-7eb4c34364cc"
        or readiness.get("build_sha256")
        != "7eb4c34364ccdbcb0a6d026d9062ca592727f6b1c4ec801ec96ab8b2178def44"
        or readiness.get("experiment_continuation_allowed") is not True
        or readiness.get("full_training_requires_explicit_new_confirmation") is not True
        or readiness.get("production_promotion_allowed") is not False
    ):
        raise Phase5KI20TrainingError("KI20 readiness v1.3 계약이 다릅니다.")
    readiness_summary = _assert_file(
        repo_root,
        readiness,
        path_key="summary_path",
        hash_key="summary_sha256",
        label="KI20 readiness summary",
    )
    readiness_value = load_json(readiness_summary, "KI20 readiness summary")
    if (
        readiness_value.get("build_sha256") != readiness["build_sha256"]
        or readiness_value.get("experiment_continuation_allowed") is not True
        or readiness_value.get("ki20_preflight_ready") is not True
        or readiness_value.get("quality_target_status") != "not_met"
        or readiness_value.get("production_promotion_allowed") is not False
    ):
        raise Phase5KI20TrainingError("KI20 readiness summary 판정이 다릅니다.")

    preflight = config.get("required_preflight")
    if (
        not isinstance(preflight, dict)
        or preflight.get("version") != "v1.1.0"
        or preflight.get("build_id") != "preflight-b47fe12f03a4"
        or preflight.get("build_sha256")
        != "b47fe12f03a4114a8b9c30bb39b1cc57c12bcfc0525747209b50d0113fcbcd2e"
    ):
        raise Phase5KI20TrainingError("KI20 preflight v1.1 계약이 다릅니다.")
    preflight_report = _assert_file(
        repo_root,
        preflight,
        path_key="report_path",
        hash_key="report_sha256",
        label="KI20 preflight report",
    )
    preflight_value = load_json(preflight_report, "KI20 preflight report")
    if (
        preflight_value.get("status") != "ki20_preflight_ready"
        or preflight_value.get("preflight_build_sha256") != preflight["build_sha256"]
        or preflight_value.get("selected_training") != preflight.get("selected_training")
        or preflight_value.get("forward_backward_optimizer_preflight_passed") is not True
        or preflight_value.get("blind_source_test_inspected") is not False
    ):
        raise Phase5KI20TrainingError("KI20 preflight report 판정이 다릅니다.")

    model = config.get("model")
    if (
        not isinstance(model, dict)
        or model.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or model.get("model_sha256")
        != "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942"
        or model.get("chat_template_sha256")
        != "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3"
        or model.get("package_lock_sha256")
        != "0301de92dea4a21eb8077abb01aae6eaf412590ac670e436bcd5d7b3717b8aed"
        or model.get("dtype") != "bfloat16"
        or model.get("attention_backend") != "sdpa"
        or model.get("parameter_count") != 1_291_478_272
        or model.get("local_files_only") is not True
        or model.get("trust_remote_code") is not True
    ):
        raise Phase5KI20TrainingError("KI20 모델 계약이 다릅니다.")
    snapshot = legacy._safe_path(repo_root, str(model.get("local_subdir", "")))
    if (
        not snapshot.is_dir()
        or sha256_file(snapshot / "model.safetensors") != model["model_sha256"]
        or sha256_file(legacy._safe_path(repo_root, model["chat_template_path"]))
        != model["chat_template_sha256"]
        or sha256_file(legacy._safe_path(repo_root, model["package_lock"]))
        != model["package_lock_sha256"]
    ):
        raise Phase5KI20TrainingError("KI20 모델·template·lock hash가 다릅니다.")

    if config.get("runtime_versions") != {
        "torch": "2.13.0+cu130",
        "torch_cuda": "13.0",
        "transformers": "4.57.6",
        "trl": "1.12.0",
        "bitsandbytes": "0.50.2",
        "datasets": "4.7.0",
    }:
        raise Phase5KI20TrainingError("KI20 runtime 버전 계약이 다릅니다.")

    data = config.get("data")
    if (
        not isinstance(data, dict)
        or data.get("canonical_build_id") != "build-6f32d52c2868"
        or data.get("rows") != 20_000
        or data.get("manifest") != "manifests/mix20k_v2.jsonl"
        or data.get("manifest_sha256")
        != "731ace0ac5584fd97fc38f157a4ecdb1babedefd79e2ec5b2d755fa26e48a550"
    ):
        raise Phase5KI20TrainingError("KI20 데이터 계약이 다릅니다.")
    manifest = legacy._safe_path(repo_root, data["canonical_root"]) / data["manifest"]
    if sha256_file(manifest) != data["manifest_sha256"]:
        raise Phase5KI20TrainingError("KI20 manifest hash가 다릅니다.")

    dev = config.get("dev_monitor")
    if not isinstance(dev, dict) or dev.get("rows") != 70:
        raise Phase5KI20TrainingError("KI20 dev monitor 계약이 다릅니다.")
    _assert_file(
        repo_root,
        dev,
        path_key="path",
        hash_key="sha256",
        label="KI20 dev monitor",
    )

    required_training = {
        "max_length": 768,
        "pad_to_multiple_of": 8,
        "per_device_train_batch_size": 4,
        "per_device_eval_batch_size": 8,
        "gradient_accumulation_steps": 2,
        "eval_accumulation_steps": 1,
        "effective_batch_size": 8,
        "num_train_epochs": 1,
        "expected_optimizer_steps": 2500,
        "optim": "paged_adamw_8bit",
        "learning_rate": 0.000008,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
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
        "logging_steps": 10,
        "logging_nan_inf_filter": False,
        "eval_steps": 250,
        "save_steps": 250,
        "save_total_limit": 6,
        "preserved_milestone_steps": [1250, 2500],
        "save_only_model": False,
        "save_safetensors": True,
        "dataloader_num_workers": 0,
        "seed": 42,
        "data_seed": 42,
        "torch_compile": False,
        "report_to": [],
    }
    if config.get("training") != required_training:
        raise Phase5KI20TrainingError("KI20 1 epoch 학습 설정이 다릅니다.")

    if config.get("objective") != {
        "name": "assistant_only_token_nll",
        "trainer_loss_type": "chunked_nll",
        "token_weighting": "uniform_over_supervised_assistant_tokens",
        "weighted_sampler": False,
        "dft": False,
        "absolute_loss_is_quality_gate": False,
    }:
        raise Phase5KI20TrainingError("KI20 목적함수 계약이 다릅니다.")

    if config.get("operational_limits") != {
        "expected_gpu_count": 1,
        "max_total_gpu_memory_used_mib": 16384,
        "min_free_gpu_memory_before_start_mib": 12000,
        "min_system_ram_available_bytes": 4294967296,
        "min_disk_available_bytes": 68719476736,
        "require_no_active_compute_process_before_start": True,
    }:
        raise Phase5KI20TrainingError("KI20 운영 한계 계약이 다릅니다.")

    if config.get("governance") != {
        "explicit_user_confirmation_received": True,
        "confirmation_date": "2026-08-29",
        "confirmation_scope": "ki20_mix_v2_one_epoch_full_fine_tuning",
        "full_training_execution_enabled": True,
        "goal_completion_criterion": "first_finite_optimizer_step_while_process_alive",
        "quality_target_status": "not_met",
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "ki10_rerun_allowed": False,
    }:
        raise Phase5KI20TrainingError("KI20 사용자 승인·거버넌스 계약이 다릅니다.")

    if config.get("outputs") != {
        "run_root": "runs/KI20-MIX-v2/v1.2.0/{run_build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-runs/v1.2.0/KI20-MIX-v2/{run_build_id}",
    }:
        raise Phase5KI20TrainingError("KI20 출력 경로 계약이 다릅니다.")
    if config.get("implementation_files") != [
        "scripts/training/phase5_train.py",
        "scripts/training/phase5_ki20_train.py",
    ]:
        raise Phase5KI20TrainingError("KI20 구현 fingerprint 목록이 다릅니다.")


def _validate_registry(
    config: dict[str, Any], config_path: Path, repo_root: Path
) -> dict[str, Any]:
    registry = load_json(repo_root / REGISTRY, "dataset registry")
    approved = registry.get("approved_phase5_training_execution")
    expected = {
        "version": "v1.2.0",
        "config": config_path.resolve().relative_to(repo_root.resolve()).as_posix(),
        "config_sha256": sha256_file(config_path),
        "run_id": RUN_ID,
        "num_train_epochs": 1,
        "expected_optimizer_steps": 2500,
        "explicit_user_confirmation_received": True,
        "full_training_execution_enabled": True,
        "goal_completion_criterion": "first_finite_optimizer_step_while_process_alive",
        "quality_target_status": "not_met",
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "status": "approved_for_ki20_one_epoch_execution",
    }
    if approved != expected:
        raise Phase5KI20TrainingError("registry의 KI20 본학습 승인 포인터가 다릅니다.")
    contracts = registry.get("phase5_training_execution_contracts")
    if not isinstance(contracts, list) or contracts.count(expected) != 1:
        raise Phase5KI20TrainingError("registry의 KI20 본학습 승인 이력이 다릅니다.")
    return approved


def validate_contract(
    config: dict[str, Any], config_path: Path, repo_root: Path
) -> dict[str, Any]:
    _validate_fixed_contract(config, repo_root)
    approved = _validate_registry(config, config_path, repo_root)
    return {
        "status": "validated_ki20_one_epoch_execution_contract",
        "run_id": RUN_ID,
        "training_version": config["training_version"],
        "approval": approved,
        "writes_performed": False,
    }


def prepare_context(
    repo_root: Path, config_path: Path = DEFAULT_CONFIG
) -> dict[str, Any]:
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    config = load_json(config_path, "KI20 v1.2 training config")
    validate_contract(config, config_path, repo_root)
    implementation_hashes = {
        relative: sha256_file(legacy._safe_path(repo_root, relative))
        for relative in config["implementation_files"]
    }
    run_contract = {
        "manifest": config["data"]["manifest"],
        "rows": config["data"]["rows"],
        "sha256": config["data"]["manifest_sha256"],
        "expected_optimizer_steps": config["training"]["expected_optimizer_steps"],
    }
    run_inputs = {
        "training_version": config["training_version"],
        "run_id": RUN_ID,
        "config_sha256": sha256_file(config_path),
        "readiness_build_sha256": config["required_readiness"]["build_sha256"],
        "preflight_build_sha256": config["required_preflight"]["build_sha256"],
        "model_sha256": config["model"]["model_sha256"],
        "manifest_sha256": config["data"]["manifest_sha256"],
        "training_sha256": sha256_json(config["training"]),
        "implementation_hashes": implementation_hashes,
    }
    run_sha256 = sha256_json(run_inputs)
    run_build_id = f"run-{run_sha256[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "run_id": RUN_ID,
        "run_inputs": run_inputs,
        "run_sha256": run_sha256,
        "run_build_id": run_build_id,
        "run_contract": run_contract,
        "run_root": legacy._safe_path(
            repo_root,
            config["outputs"]["run_root"].format(run_build_id=run_build_id),
        ),
        "public_root": legacy._safe_path(
            repo_root,
            config["outputs"]["public_root"].format(run_build_id=run_build_id),
        ),
    }


def _dev_monitor_rows(context: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    path = legacy._safe_path(repo_root, context["config"]["dev_monitor"]["path"])
    if sha256_file(path) != context["config"]["dev_monitor"]["sha256"]:
        raise Phase5KI20TrainingError("dev monitor SHA-256이 다릅니다.")
    values = read_jsonl(path, "KI20 dev monitor")
    rows: list[dict[str, Any]] = []
    for item in values:
        for case in item["cases"]:
            rows.append(
                {
                    "messages": [
                        *case["prompt_messages"],
                        {"role": "assistant", "content": case["reference_assistant"]},
                    ]
                }
            )
    if len(rows) != 70:
        raise Phase5KI20TrainingError("KI20 dev monitor가 70건이 아닙니다.")
    return rows


def _operational_precheck(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    limits = context["config"]["operational_limits"]
    gpu = _gpu_snapshot()
    if gpu["free_mib"] < limits["min_free_gpu_memory_before_start_mib"]:
        raise Phase5KI20TrainingError(
            f"학습 시작 전 가용 VRAM이 부족합니다: {gpu['free_mib']} MiB"
        )
    if gpu["used_mib"] >= min(limits["max_total_gpu_memory_used_mib"], gpu["total_mib"]):
        raise Phase5KI20TrainingError("학습 시작 전 GPU 전체 사용량이 상한 이상입니다.")
    processes = _compute_processes()
    if limits["require_no_active_compute_process_before_start"] and processes:
        raise Phase5KI20TrainingError("학습 시작 전 다른 CUDA compute process가 있습니다.")
    ram = legacy._system_ram()
    if (
        not isinstance(ram.get("available_bytes"), int)
        or ram["available_bytes"] < limits["min_system_ram_available_bytes"]
    ):
        raise Phase5KI20TrainingError("학습 시작 전 system RAM이 부족합니다.")
    disk = shutil.disk_usage(repo_root)
    if disk.free < limits["min_disk_available_bytes"]:
        raise Phase5KI20TrainingError("학습 checkpoint용 디스크 여유가 부족합니다.")
    return {
        "gpu": gpu,
        "compute_processes": processes,
        "system_ram": ram,
        "disk_available_bytes": disk.free,
    }


def _initial_manifest(
    context: dict[str, Any], repo_root: Path, manifest_sha256: str, precheck: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": "1.2.0",
        "report_type": "phase5_ki20_full_ft_run",
        "status": "initializing",
        "run_id": RUN_ID,
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "run_inputs": context["run_inputs"],
        "workspace_commit": legacy._git_head(repo_root),
        "working_tree_clean_at_start": True,
        "manifest_sha256": manifest_sha256,
        "initial_checkpoint_is_fixed_instruct": True,
        "independent_from_ki10": True,
        "process_id": os.getpid(),
        "service_unit": os.environ.get("PHASE5_SERVICE_UNIT"),
        "created_at_utc": _utc_now(),
        "operational_precheck": precheck,
        "first_optimizer_step": None,
        "phase5_training_performed": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
    }


class _KI20TrainingProbe(legacy._TrainingProbe):
    def __init__(
        self,
        torch: Any,
        metrics_path: Path,
        context: dict[str, Any],
        repo_root: Path,
    ) -> None:
        super().__init__(torch, metrics_path)
        self.context = context
        self.repo_root = repo_root

    def on_log(
        self,
        args: Any,
        state: Any,
        control: Any,
        logs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        snapshot = _gpu_snapshot()
        cap = self.context["config"]["operational_limits"][
            "max_total_gpu_memory_used_mib"
        ]
        if snapshot["used_mib"] >= min(cap, snapshot["total_mib"]):
            raise Phase5KI20TrainingError("학습 중 GPU 전체 사용량이 16 GiB 상한 이상입니다.")
        enriched = dict(logs)
        enriched["gpu_total_memory_used_mib"] = snapshot["used_mib"]
        super().on_log(args, state, control, enriched, **kwargs)

        marker_path = self.context["run_root"] / "training_started.json"
        if marker_path.exists() or int(state.global_step) < 1:
            return
        loss = enriched.get("loss")
        grad_norm = enriched.get("grad_norm")
        if not isinstance(loss, (int, float)) or not math.isfinite(float(loss)):
            raise Phase5KI20TrainingError("첫 optimizer step loss가 유한하지 않습니다.")
        if not isinstance(grad_norm, (int, float)) or not math.isfinite(float(grad_norm)):
            raise Phase5KI20TrainingError("첫 optimizer step grad_norm이 유한하지 않습니다.")
        if (
            not self.gradient_probe
            or self.gradient_probe.get("finite") is not True
            or self.gradient_probe.get("nonzero") is not True
        ):
            raise Phase5KI20TrainingError("첫 optimizer step gradient가 유한한 nonzero가 아닙니다.")
        marker = {
            "schema_version": "1.2.0",
            "status": "training_started",
            "run_id": RUN_ID,
            "run_build_id": self.context["run_build_id"],
            "run_sha256": self.context["run_sha256"],
            "workspace_commit": legacy._git_head(self.repo_root),
            "process_id": os.getpid(),
            "service_unit": os.environ.get("PHASE5_SERVICE_UNIT"),
            "global_step": int(state.global_step),
            "epoch": float(state.epoch) if state.epoch is not None else None,
            "loss": float(loss),
            "grad_norm": float(grad_norm),
            "gradient_probe": self.gradient_probe,
            "gpu": snapshot,
            "started_at_utc": _utc_now(),
            "goal_completion_criterion_met": True,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
        }
        manifest_path = self.context["run_root"] / "run_manifest.json"
        manifest = load_json(manifest_path, "KI20 initializing run manifest")
        if (
            manifest.get("status") != "initializing"
            or manifest.get("run_sha256") != self.context["run_sha256"]
        ):
            raise Phase5KI20TrainingError("KI20 시작 manifest identity가 다릅니다.")
        manifest["status"] = "running"
        manifest["first_optimizer_step"] = marker
        manifest["phase5_training_performed"] = True
        legacy._atomic_replace(
            manifest_path, legacy._json_bytes(manifest), mode=PRIVATE_FILE_MODE
        )
        legacy._write_once(
            marker_path, legacy._json_bytes(marker), mode=PRIVATE_FILE_MODE
        )


def _process_is_training(marker: dict[str, Any]) -> bool:
    pid = marker.get("process_id")
    if not isinstance(pid, int) or pid <= 1:
        return False
    proc = Path(f"/proc/{pid}")
    try:
        command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", errors="replace"
        )
        cwd = (proc / "cwd").resolve()
    except OSError:
        return False
    return (
        Path(__file__).name in command
        and " train " in f" {command} "
        and cwd == REPO_ROOT.resolve()
    )


def verify_start(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["run_root"]
    if root.is_symlink() or not root.is_dir():
        raise Phase5KI20TrainingError("KI20 run 디렉터리가 없습니다.")
    marker_path = root / "training_started.json"
    manifest_path = root / "run_manifest.json"
    if marker_path.is_symlink() or manifest_path.is_symlink():
        raise Phase5KI20TrainingError("KI20 시작 산출물에 symlink가 있습니다.")
    marker = load_json(marker_path, "KI20 training start marker")
    manifest = load_json(manifest_path, "KI20 run manifest")
    numeric = (marker.get("loss"), marker.get("grad_norm"))
    if (
        marker.get("status") != "training_started"
        or marker.get("run_sha256") != context["run_sha256"]
        or marker.get("global_step") != 1
        or marker.get("goal_completion_criterion_met") is not True
        or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in numeric)
        or marker.get("gradient_probe", {}).get("finite") is not True
        or marker.get("gradient_probe", {}).get("nonzero") is not True
        or marker.get("production_promotion_allowed") is not False
        or manifest.get("run_sha256") != context["run_sha256"]
        or manifest.get("status") not in {"running", "trained_and_reloaded"}
        or manifest.get("phase5_training_performed") is not True
        or manifest.get("first_optimizer_step") != marker
    ):
        raise Phase5KI20TrainingError("KI20 첫 optimizer step 증거가 다릅니다.")
    if not _process_is_training(marker):
        raise Phase5KI20TrainingError("KI20 학습 프로세스가 계속 실행 중이지 않습니다.")
    gpu = _gpu_snapshot()
    cap = context["config"]["operational_limits"]["max_total_gpu_memory_used_mib"]
    if gpu["used_mib"] >= min(cap, gpu["total_mib"]):
        raise Phase5KI20TrainingError("KI20 시작 확인 시 GPU 사용량이 상한 이상입니다.")
    processes = _compute_processes()
    if marker["process_id"] not in {value["pid"] for value in processes}:
        raise Phase5KI20TrainingError("KI20 process가 CUDA compute process로 확인되지 않습니다.")
    return {
        "status": "verified_training_started",
        "run_id": RUN_ID,
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "global_step": marker["global_step"],
        "loss": marker["loss"],
        "grad_norm": marker["grad_norm"],
        "process_id": marker["process_id"],
        "service_unit": marker["service_unit"],
        "gpu_total_memory_used_mib": gpu["used_mib"],
        "goal_completion_criterion_met": True,
        "phase5_training_performed": True,
        "production_promotion_allowed": False,
        "writes_performed": False,
    }


def _public_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "schema_version",
            "status",
            "run_id",
            "run_build_id",
            "run_sha256",
            "workspace_commit",
            "manifest_sha256",
            "rows",
            "optimizer_steps",
            "expected_optimizer_steps",
            "training_loss",
            "loss_summary",
            "gradient_probe",
            "runtime",
            "peak_vram_bytes",
            "vram_free_bytes_at_finish",
            "system_ram",
            "elapsed_seconds",
            "resumed_from_checkpoint",
            "final_reload_passed",
            "phase5_training_performed",
            "production_promotion_allowed",
            "blind_source_test_inspected",
            "raw_samples_in_report",
        )
    }


def _publish_run(context: dict[str, Any], summary: dict[str, Any]) -> None:
    root: Path = context["public_root"]
    if root.exists():
        raise Phase5KI20TrainingError("기존 KI20 공개 run 보고서를 덮어쓸 수 없습니다.")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent))
    public = _public_summary(summary)
    payload = legacy._json_bytes(public)
    manifest = {
        "schema_version": "1.2.0",
        "report_type": "phase5_ki20_full_ft_public_manifest",
        "run_id": RUN_ID,
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "artifact_sha256": {"training_summary.json": hashlib.sha256(payload).hexdigest()},
        "status": "trained_and_reloaded",
        "quality_gate_evaluated": False,
        "phase5_training_performed": True,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "raw_samples_in_report": False,
    }
    try:
        legacy._write_once(
            temporary / "training_summary.json", payload, mode=PUBLIC_FILE_MODE
        )
        legacy._write_once(
            temporary / "build_manifest.json",
            legacy._json_bytes(manifest),
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(temporary, root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def reload_final(
    context: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    root: Path = context["run_root"]
    final_root = root / "final"
    if root.is_symlink() or final_root.is_symlink() or not final_root.is_dir():
        raise Phase5KI20TrainingError("KI20 reload 대상 final checkpoint가 없습니다.")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5KI20TrainingError("KI20 reload runtime import가 실패했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        final_root, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        final_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda:0")
    model.eval()
    generations: list[dict[str, Any]] = []
    started = time.monotonic()
    with torch.inference_mode():
        for index, row in enumerate(_dev_monitor_rows(context, repo_root)[:5]):
            prompt = row["messages"][:-1]
            input_ids = tokenizer.apply_chat_template(
                prompt,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
            ).to("cuda:0")
            output = model.generate(
                input_ids,
                do_sample=False,
                num_beams=1,
                max_new_tokens=64,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            decoded = tokenizer.decode(
                output[0, input_ids.shape[-1] :], skip_special_tokens=True
            ).strip()
            if not decoded:
                raise Phase5KI20TrainingError("KI20 reload fixture 출력이 비었습니다.")
            generations.append(
                {
                    "fixture_index": index,
                    "prompt_sha256": sha256_json(prompt),
                    "output": decoded,
                }
            )
    legacy._write_once(
        root / "reload_fixtures.jsonl",
        b"".join(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
            + b"\n"
            for value in generations
        ),
        mode=PRIVATE_FILE_MODE,
    )
    summary = {
        "schema_version": "1.2.0",
        "status": "passed",
        "run_id": RUN_ID,
        "run_build_id": context["run_build_id"],
        "task_count": 5,
        "nonempty_outputs": 5,
        "model_parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "model_dtype": "torch.bfloat16",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "new_process": os.environ.get("PHASE5_RELOAD_CHILD") == "1",
        "raw_outputs_in_summary": False,
    }
    legacy._write_once(
        root / "reload_summary.json",
        legacy._json_bytes(summary),
        mode=PRIVATE_FILE_MODE,
    )
    return summary


def _assert_resume_checkpoint(root: Path, checkpoint: Path) -> None:
    if (
        checkpoint.is_symlink()
        or not checkpoint.is_dir()
        or checkpoint.parent != root
        or not (checkpoint / "optimizer.pt").is_file()
        or not (checkpoint / "scheduler.pt").is_file()
        or not (checkpoint / "trainer_state.json").is_file()
    ):
        raise Phase5KI20TrainingError("KI20 resume checkpoint state가 완전하지 않습니다.")


def train(
    context: dict[str, Any], repo_root: Path, *, resume_checkpoint: Path | None
) -> dict[str, Any]:
    if os.environ.get("PHASE5_TRAINING") != RUN_ID:
        raise Phase5KI20TrainingError(f"PHASE5_TRAINING={RUN_ID} 확인값이 필요합니다.")
    root: Path = context["run_root"]
    if resume_checkpoint is None:
        if root.exists() or context["public_root"].exists():
            raise Phase5KI20TrainingError("기존 KI20 Run을 덮어쓸 수 없습니다.")
        if not legacy._git_clean(repo_root):
            raise Phase5KI20TrainingError("KI20 학습 시작 전 working tree가 깨끗해야 합니다.")
        precheck = _operational_precheck(context, repo_root)
        rows, manifest_sha256 = legacy._training_rows(context, repo_root)
        legacy._private_dir(root)
        legacy._atomic_replace(
            root / "run_manifest.json",
            legacy._json_bytes(
                _initial_manifest(context, repo_root, manifest_sha256, precheck)
            ),
            mode=PRIVATE_FILE_MODE,
        )
        legacy._write_once(
            root / "config.resolved.json",
            legacy._json_bytes(context["config"]),
            mode=PRIVATE_FILE_MODE,
        )
    else:
        if root.is_symlink() or not root.is_dir():
            raise Phase5KI20TrainingError("KI20 resume 대상 Run이 없습니다.")
        if not legacy._git_clean(repo_root):
            raise Phase5KI20TrainingError("KI20 resume 전 working tree가 깨끗해야 합니다.")
        _operational_precheck(context, repo_root)
        rows, manifest_sha256 = legacy._training_rows(context, repo_root)
        manifest = load_json(root / "run_manifest.json", "KI20 resume run manifest")
        if (
            manifest.get("run_sha256") != context["run_sha256"]
            or manifest.get("manifest_sha256") != manifest_sha256
            or manifest.get("status") not in {"running", "interrupted", "failed"}
        ):
            raise Phase5KI20TrainingError("KI20 resume Run identity가 다릅니다.")
        _assert_resume_checkpoint(root, resume_checkpoint)

    (
        torch,
        dataset_class,
        callback_class,
        sft_config,
        sft_trainer,
        model,
        tokenizer,
        runtime,
    ) = legacy._load_runtime(context, repo_root)
    trainer: Any | None = None
    started = time.monotonic()
    try:
        dataset = dataset_class.from_list(rows)
        eval_dataset = dataset_class.from_list(_dev_monitor_rows(context, repo_root))
        probe = _KI20TrainingProbe(
            torch, root / "metrics.jsonl", context, repo_root
        )
        args = legacy._sft_args(context, sft_config, root, train=True)
        trainer = sft_trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            eval_dataset=eval_dataset,
            processing_class=tokenizer,
            callbacks=[legacy._make_callback(probe, callback_class)],
        )
        output = trainer.train(
            resume_from_checkpoint=(str(resume_checkpoint) if resume_checkpoint else None)
        )
        global_step = int(trainer.state.global_step)
        expected_steps = context["run_contract"]["expected_optimizer_steps"]
        if global_step != expected_steps:
            raise Phase5KI20TrainingError(
                f"KI20 optimizer step 수가 다릅니다: {global_step} != {expected_steps}"
            )
        training_loss = float(output.training_loss)
        if not math.isfinite(training_loss) or probe.nonfinite_seen:
            raise Phase5KI20TrainingError("KI20 training loss가 NaN/Inf입니다.")
        if (
            not probe.gradient_probe
            or probe.gradient_probe.get("finite") is not True
            or probe.gradient_probe.get("nonzero") is not True
        ):
            raise Phase5KI20TrainingError("KI20 gradient가 유한한 nonzero가 아닙니다.")

        final_root = root / "final"
        trainer.save_model(str(final_root))
        tokenizer.save_pretrained(final_root)
        trainer.save_state()
        for path in [root, final_root, *[value for value in root.rglob("*") if value.is_dir()]]:
            path.chmod(PRIVATE_DIR_MODE)
        checkpoints = sorted(
            (path for path in root.glob("checkpoint-*") if path.is_dir()),
            key=lambda path: int(path.name.split("-")[-1]),
        )
        milestone_names = {
            f"checkpoint-{step}"
            for step in context["config"]["training"]["preserved_milestone_steps"]
        }
        if not milestone_names.issubset({path.name for path in checkpoints}):
            raise Phase5KI20TrainingError("KI20 보존 milestone checkpoint가 없습니다.")
        inventories = {
            path.name: legacy._checkpoint_inventory(path)
            for path in [*checkpoints, final_root]
        }
        legacy._write_once(
            root / "checkpoint_inventory.json",
            legacy._json_bytes(inventories),
            mode=PRIVATE_FILE_MODE,
        )
        logs = legacy._read_jsonl_if_exists(root / "metrics.jsonl")
        loss_summary = legacy._loss_summary(logs)
        if not loss_summary["losses_finite"] or not loss_summary["grad_norms_finite"]:
            raise Phase5KI20TrainingError("KI20 저장 loss/grad_norm 로그가 유한하지 않습니다.")

        del trainer
        trainer = None
        del model
        gc.collect()
        torch.cuda.empty_cache()
        reload_result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(context["config_path"]),
                "reload",
            ],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PHASE5_RELOAD_CHILD": "1"},
        )
        if reload_result.returncode != 0:
            raise Phase5KI20TrainingError(
                f"KI20 새 프로세스 final reload가 실패했습니다: {reload_result.stderr[-1000:]}"
            )
        reload_summary = load_json(root / "reload_summary.json", "KI20 reload summary")
        if (
            reload_summary.get("status") != "passed"
            or reload_summary.get("nonempty_outputs") != 5
            or reload_summary.get("new_process") is not True
        ):
            raise Phase5KI20TrainingError("KI20 새 프로세스 reload 결과가 다릅니다.")
        summary = {
            "schema_version": "1.2.0",
            "status": "trained_and_reloaded",
            "run_id": RUN_ID,
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "workspace_commit": load_json(
                root / "run_manifest.json", "KI20 completion manifest"
            )["workspace_commit"],
            "manifest_sha256": manifest_sha256,
            "rows": len(rows),
            "optimizer_steps": global_step,
            "expected_optimizer_steps": expected_steps,
            "training_loss": training_loss,
            "loss_summary": loss_summary,
            "gradient_probe": probe.gradient_probe,
            "runtime": runtime,
            "peak_vram_bytes": int(torch.cuda.max_memory_allocated(0)),
            "vram_free_bytes_at_finish": int(torch.cuda.mem_get_info(0)[0]),
            "system_ram": legacy._system_ram(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "resumed_from_checkpoint": resume_checkpoint is not None,
            "final_reload_passed": True,
            "phase5_training_performed": True,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
            "raw_samples_in_report": False,
        }
        legacy._write_once(
            root / "training_summary.json",
            legacy._json_bytes(summary),
            mode=PRIVATE_FILE_MODE,
        )
        manifest_value = load_json(root / "run_manifest.json", "KI20 running manifest")
        manifest_value.update(
            {
                "status": "trained_and_reloaded",
                "artifact_sha256": {
                    "training_started.json": sha256_file(root / "training_started.json"),
                    "training_summary.json": sha256_file(root / "training_summary.json"),
                    "checkpoint_inventory.json": sha256_file(
                        root / "checkpoint_inventory.json"
                    ),
                    "reload_summary.json": sha256_file(root / "reload_summary.json"),
                },
                "final_reload_passed": True,
                "completed_at_utc": _utc_now(),
            }
        )
        legacy._atomic_replace(
            root / "run_manifest.json",
            legacy._json_bytes(manifest_value),
            mode=PRIVATE_FILE_MODE,
        )
        _publish_run(context, summary)
        return verify_run(context)
    except Exception as exc:
        if root.is_dir():
            failure = {
                "schema_version": "1.2.0",
                "status": "failed",
                "run_id": RUN_ID,
                "run_build_id": context["run_build_id"],
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:1000],
                "checkpoints_preserved": sorted(path.name for path in root.glob("checkpoint-*")),
                "failed_at_utc": _utc_now(),
            }
            legacy._atomic_replace(
                root / "failure.json", legacy._json_bytes(failure), mode=PRIVATE_FILE_MODE
            )
            manifest_path = root / "run_manifest.json"
            if manifest_path.is_file():
                manifest_value = load_json(manifest_path, "KI20 failed manifest")
                manifest_value["status"] = "failed"
                legacy._atomic_replace(
                    manifest_path,
                    legacy._json_bytes(manifest_value),
                    mode=PRIVATE_FILE_MODE,
                )
        raise
    finally:
        if trainer is not None:
            del trainer
        gc.collect()
        if "torch" in locals():
            torch.cuda.empty_cache()


def verify_run(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["run_root"]
    public_root: Path = context["public_root"]
    if (
        root.is_symlink()
        or public_root.is_symlink()
        or not root.is_dir()
        or not public_root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) & 0o077
    ):
        raise Phase5KI20TrainingError("완료된 KI20 private/public Run이 없습니다.")
    manifest = load_json(root / "run_manifest.json", "KI20 run manifest")
    summary = load_json(root / "training_summary.json", "KI20 training summary")
    reload_summary = load_json(root / "reload_summary.json", "KI20 reload summary")
    public = load_json(public_root / "training_summary.json", "KI20 public summary")
    if (
        manifest.get("status") != "trained_and_reloaded"
        or manifest.get("run_sha256") != context["run_sha256"]
        or summary.get("optimizer_steps") != 2500
        or summary.get("final_reload_passed") is not True
        or summary.get("production_promotion_allowed") is not False
        or reload_summary.get("status") != "passed"
        or reload_summary.get("new_process") is not True
        or public != _public_summary(summary)
    ):
        raise Phase5KI20TrainingError("KI20 Run 완료 계약이 다릅니다.")
    for relative, digest in manifest.get("artifact_sha256", {}).items():
        if sha256_file(root / relative) != digest:
            raise Phase5KI20TrainingError(f"KI20 Run artifact hash가 다릅니다: {relative}")
    return {
        "status": "verified_trained_and_reloaded",
        "run_id": RUN_ID,
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "optimizer_steps": summary["optimizer_steps"],
        "training_loss": summary["training_loss"],
        "phase5_training_performed": True,
        "production_promotion_allowed": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KI20 Phase 5 v1.2 1 epoch Full FT runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    train_parser = commands.add_parser("train")
    train_parser.add_argument("--execute", action="store_true")
    resume_parser = commands.add_parser("resume")
    resume_parser.add_argument("--checkpoint", type=Path, required=True)
    resume_parser.add_argument("--execute", action="store_true")
    commands.add_parser("verify-start")
    commands.add_parser("reload")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                load_json(config_path, "KI20 v1.2 training config"),
                config_path,
                REPO_ROOT,
            )
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "run_id": RUN_ID,
                    "run_build_id": context["run_build_id"],
                    "run_sha256": context["run_sha256"],
                    "rows": context["run_contract"]["rows"],
                    "num_train_epochs": 1,
                    "expected_optimizer_steps": 2500,
                    "goal_completion_criterion": context["config"]["governance"][
                        "goal_completion_criterion"
                    ],
                    "run_root": context["run_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "train":
                result = (
                    train(context, REPO_ROOT, resume_checkpoint=None)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "run_build_id": context["run_build_id"],
                        "writes_performed": False,
                    }
                )
            elif args.command == "resume":
                checkpoint = args.checkpoint
                if not checkpoint.is_absolute():
                    checkpoint = (REPO_ROOT / checkpoint).resolve()
                result = (
                    train(context, REPO_ROOT, resume_checkpoint=checkpoint)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "run_build_id": context["run_build_id"],
                        "writes_performed": False,
                    }
                )
            elif args.command == "verify-start":
                result = verify_start(context)
            elif args.command == "reload":
                result = reload_final(context, REPO_ROOT)
            else:
                result = verify_run(context)
    except (
        Phase5KI20TrainingError,
        legacy.Phase5TrainingError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
