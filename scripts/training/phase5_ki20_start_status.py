# phase5_ki20_start_status.py - WSL2를 포함해 KI20 첫 정상 step의 실행 상태를 읽기 전용 검증한다.

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import load_json, read_jsonl
from scripts.training.phase5_ki20_train import (
    DEFAULT_CONFIG,
    Phase5KI20TrainingError,
    _compute_processes,
    _gpu_snapshot,
    _process_is_training,
    prepare_context,
)

MINIMUM_GPU_GROWTH_MIB = 4096


class Phase5KI20StartStatusError(RuntimeError):
    """KI20 첫 정상 step 상태 검증 위반."""


def _is_wsl2() -> bool:
    try:
        value = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in value.lower() and "wsl2" in value.lower()


def _service_is_active(unit: Any) -> bool:
    if not isinstance(unit, str) or not unit.endswith(".service"):
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def verify_start_status(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["run_root"]
    if root.is_symlink() or not root.is_dir():
        raise Phase5KI20StartStatusError("KI20 run 디렉터리가 없습니다.")
    marker_path = root / "training_started.json"
    manifest_path = root / "run_manifest.json"
    metrics_path = root / "metrics.jsonl"
    if any(path.is_symlink() for path in (marker_path, manifest_path, metrics_path)):
        raise Phase5KI20StartStatusError("KI20 시작 증거에 symlink가 있습니다.")
    marker = load_json(marker_path, "KI20 training start marker")
    manifest = load_json(manifest_path, "KI20 run manifest")
    metrics = read_jsonl(metrics_path, "KI20 metrics")
    step_one = [value for value in metrics if value.get("global_step") == 1]
    numeric = (marker.get("loss"), marker.get("grad_norm"))
    if (
        marker.get("status") != "training_started"
        or marker.get("run_id") != "KI20-MIX-v2"
        or marker.get("run_build_id") != context["run_build_id"]
        or marker.get("run_sha256") != context["run_sha256"]
        or marker.get("global_step") != 1
        or marker.get("goal_completion_criterion_met") is not True
        or not all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in numeric
        )
        or marker.get("gradient_probe", {}).get("finite") is not True
        or marker.get("gradient_probe", {}).get("nonzero") is not True
        or marker.get("production_promotion_allowed") is not False
        or marker.get("blind_source_test_inspected") is not False
        or len(step_one) != 1
        or float(step_one[0].get("loss", math.nan)) != float(marker["loss"])
        or float(step_one[0].get("grad_norm", math.nan)) != float(marker["grad_norm"])
    ):
        raise Phase5KI20StartStatusError("KI20 첫 step marker·metrics 증거가 다릅니다.")
    if (
        manifest.get("run_sha256") != context["run_sha256"]
        or manifest.get("status") not in {"running", "trained_and_reloaded"}
        or manifest.get("phase5_training_performed") is not True
        or manifest.get("first_optimizer_step") != marker
        or manifest.get("production_promotion_allowed") is not False
        or manifest.get("blind_source_test_inspected") is not False
    ):
        raise Phase5KI20StartStatusError("KI20 run manifest 시작 상태가 다릅니다.")
    if not _process_is_training(marker):
        raise Phase5KI20StartStatusError("KI20 고정 runner process가 계속 실행 중이지 않습니다.")
    if not _service_is_active(marker.get("service_unit")):
        raise Phase5KI20StartStatusError("KI20 systemd user service가 active가 아닙니다.")

    gpu = _gpu_snapshot()
    cap = context["config"]["operational_limits"]["max_total_gpu_memory_used_mib"]
    if gpu["used_mib"] >= min(cap, gpu["total_mib"]):
        raise Phase5KI20StartStatusError("KI20 GPU 전체 사용량이 16 GiB 상한 이상입니다.")
    baseline = manifest.get("operational_precheck", {}).get("gpu", {}).get("used_mib")
    if not isinstance(baseline, int):
        raise Phase5KI20StartStatusError("KI20 시작 전 GPU baseline이 없습니다.")
    growth = gpu["used_mib"] - baseline
    processes = _compute_processes()
    process_ids = {value["pid"] for value in processes}
    if marker["process_id"] in process_ids:
        evidence_mode = "nvidia_compute_pid"
    elif not processes and _is_wsl2() and growth >= MINIMUM_GPU_GROWTH_MIB:
        evidence_mode = "wsl2_runner_pid_and_gpu_growth"
    else:
        raise Phase5KI20StartStatusError(
            "KI20 CUDA 실행 증거가 compute PID 또는 WSL2 GPU 증가 조건을 충족하지 않습니다."
        )
    return {
        "status": "verified_training_started",
        "run_id": "KI20-MIX-v2",
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "global_step": marker["global_step"],
        "loss": marker["loss"],
        "grad_norm": marker["grad_norm"],
        "gradient_finite": True,
        "gradient_nonzero": True,
        "process_id": marker["process_id"],
        "service_unit": marker["service_unit"],
        "service_active": True,
        "gpu_total_memory_used_mib": gpu["used_mib"],
        "gpu_memory_growth_from_precheck_mib": growth,
        "cuda_process_evidence": evidence_mode,
        "goal_completion_criterion_met": True,
        "phase5_training_performed": True,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KI20 첫 정상 step 상태 검증")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        result = verify_start_status(prepare_context(REPO_ROOT, config_path))
    except (
        Phase5KI20StartStatusError,
        Phase5KI20TrainingError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
