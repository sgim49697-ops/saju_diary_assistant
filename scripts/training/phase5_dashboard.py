# phase5_dashboard.py - 실행 중 KI20 학습과 완료 모델을 로컬에서 안전하게 관제한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.training.phase5_quality import score_generations

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.0.0.json"
)
ASSET_ROOT = Path(__file__).with_name("phase5_dashboard_assets")
RUN_BUILD_PATTERN = re.compile(r"^run-[0-9a-f]{12}$")
CHECKPOINT_PATTERN = re.compile(r"^checkpoint-([1-9][0-9]*)$")
SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
MAX_METRICS_BYTES = 32 * 1024 * 1024
REQUIRED_CHECKPOINT_FILES = frozenset(
    {
        "config.json",
        "model.safetensors",
        "optimizer.pt",
        "scheduler.pt",
        "trainer_state.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
REQUIRED_FINAL_FILES = frozenset(
    {
        "config.json",
        "model.safetensors",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
METRIC_FIELDS = frozenset(
    {
        "global_step",
        "epoch",
        "loss",
        "grad_norm",
        "learning_rate",
        "entropy",
        "mean_token_accuracy",
        "num_tokens",
        "eval_loss",
        "eval_entropy",
        "eval_mean_token_accuracy",
        "eval_num_tokens",
        "eval_runtime",
        "eval_samples_per_second",
        "eval_steps_per_second",
        "gpu_memory_allocated_bytes",
        "gpu_memory_reserved_bytes",
        "gpu_peak_memory_allocated_bytes",
        "gpu_total_memory_used_mib",
    }
)
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
}


class Phase5DashboardError(RuntimeError):
    """대시보드 입력·run·비공개 경계가 계약과 다를 때 발생한다."""


class DashboardRequestError(RuntimeError):
    """HTTP 요청에 안전하게 반환할 상태를 보관한다."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase5DashboardError(f"{label} 파일이 없거나 symlink입니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5DashboardError(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase5DashboardError(f"{label} JSON object가 아닙니다.")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_under(root: Path, value: str | Path, label: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    unresolved = candidate.absolute()
    boundary = root.resolve()
    try:
        unresolved.relative_to(root.absolute())
    except ValueError as exc:
        raise Phase5DashboardError(f"{label} 경로가 저장소 밖입니다.") from exc
    cursor = root.absolute()
    for part in unresolved.relative_to(root.absolute()).parts:
        cursor /= part
        if cursor.is_symlink():
            raise Phase5DashboardError(f"{label} 경로에 symlink가 있습니다.")
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(boundary)
    except ValueError as exc:
        raise Phase5DashboardError(f"{label} 경로가 저장소 밖으로 이탈합니다.") from exc
    return resolved


def validate_config(config: dict[str, Any]) -> None:
    server = config.get("server")
    training = config.get("training_contract")
    model_check = config.get("model_check")
    governance = config.get("governance")
    category_counts = (
        model_check.get("category_counts") if isinstance(model_check, dict) else None
    )
    if (
        not isinstance(category_counts, dict)
        or not category_counts
        or any(
            not isinstance(key, str)
            or not key
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1
            for key, value in category_counts.items()
        )
    ):
        raise Phase5DashboardError("대시보드 고정 probe 범주 계약이 다릅니다.")
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("dashboard_id") != "KI20-MIX-v2-dashboard"
        or config.get("allowed_run_id") != "KI20-MIX-v2"
        or config.get("refresh_seconds") != 10
        or not isinstance(server, dict)
        or server.get("host") != "127.0.0.1"
        or server.get("port") != 8765
        or server.get("max_request_bytes") != 16384
        or server.get("max_prompt_chars") != 4000
        or not isinstance(training, dict)
        or training.get("expected_optimizer_steps") != 2500
        or training.get("logging_steps") != 10
        or training.get("eval_steps") != 250
        or training.get("save_steps") != 250
        or training.get("preserved_milestone_steps") != [1250, 2500]
        or training.get("gpu_hard_cap_mib") != 16384
        or not isinstance(model_check, dict)
        or sum(category_counts.values()) != 20
        or model_check.get("diagnostic_thresholds", {}).get(
            "expected_generation_cases"
        )
        != 20
        or not isinstance(governance, dict)
        or governance.get("training_control_actions_allowed") is not False
        or governance.get("sealed_blind_access_allowed") is not False
        or governance.get("production_promotion_allowed") is not False
        or governance.get("manual_prompts_persisted") is not False
        or governance.get("fixed_probe_results_private") is not True
    ):
        raise Phase5DashboardError("Phase 5 dashboard config 계약이 다릅니다.")
    generation = model_check.get("generation")
    if generation != {"do_sample": False, "num_beams": 1, "max_new_tokens": 256}:
        raise Phase5DashboardError("대시보드 generation 계약이 다릅니다.")


def prepare_context(
    repo_root: Path, config_path: Path, run_root: Path
) -> dict[str, Any]:
    root = repo_root.resolve()
    config_target = _safe_under(root, config_path, "dashboard config")
    config = _load_json(config_target, "dashboard config")
    validate_config(config)
    run_target = _safe_under(root, run_root, "KI20 run")
    runs_root = (root / "runs").resolve(strict=False)
    try:
        run_target.relative_to(runs_root)
    except ValueError as exc:
        raise Phase5DashboardError("KI20 run은 runs/ 아래여야 합니다.") from exc
    if not RUN_BUILD_PATTERN.fullmatch(run_target.name) or not run_target.is_dir():
        raise Phase5DashboardError("KI20 run build 경로가 올바르지 않습니다.")
    manifest = _load_json(run_target / "run_manifest.json", "KI20 run manifest")
    if (
        manifest.get("run_id") != config["allowed_run_id"]
        or manifest.get("run_build_id") != run_target.name
        or not isinstance(manifest.get("run_sha256"), str)
        or len(manifest["run_sha256"]) != 64
        or manifest.get("production_promotion_allowed") is not False
        or manifest.get("blind_source_test_inspected") is not False
    ):
        raise Phase5DashboardError("KI20 run identity·governance가 다릅니다.")
    resolved = _load_json(run_target / "config.resolved.json", "KI20 resolved config")
    expected = config["training_contract"]
    training = resolved.get("training", {})
    limits = resolved.get("operational_limits", {})
    if any(
        training.get(key) != expected[key]
        for key in ("logging_steps", "eval_steps", "save_steps")
    ) or (
        training.get("expected_optimizer_steps")
        != expected["expected_optimizer_steps"]
        or training.get("preserved_milestone_steps")
        != expected["preserved_milestone_steps"]
        or limits.get("max_total_gpu_memory_used_mib")
        != expected["gpu_hard_cap_mib"]
    ):
        raise Phase5DashboardError("KI20 학습 계약과 dashboard가 다릅니다.")
    return {
        "repo_root": root,
        "config_path": config_target,
        "config": config,
        "run_root": run_target,
        "manifest": manifest,
        "resolved": resolved,
    }


def read_live_metrics(path: Path) -> tuple[list[dict[str, Any]], bool]:
    if path.is_symlink() or not path.is_file():
        raise Phase5DashboardError("metrics.jsonl이 없거나 symlink입니다.")
    payload = path.read_bytes()
    if len(payload) > MAX_METRICS_BYTES:
        raise Phase5DashboardError("metrics.jsonl 크기가 허용 범위를 넘습니다.")
    pieces = payload.split(b"\n")
    trailing_partial_ignored = False
    values: list[dict[str, Any]] = []
    for index, piece in enumerate(pieces):
        if not piece.strip():
            continue
        try:
            value = json.loads(piece)
        except (UnicodeError, json.JSONDecodeError) as exc:
            if index == len(pieces) - 1 and not payload.endswith(b"\n"):
                trailing_partial_ignored = True
                continue
            raise Phase5DashboardError(
                f"metrics.jsonl {index + 1}행이 손상됐습니다."
            ) from exc
        if not isinstance(value, dict):
            raise Phase5DashboardError(
                f"metrics.jsonl {index + 1}행이 JSON object가 아닙니다."
            )
        step = value.get("global_step")
        if not isinstance(step, int) or step < 1:
            raise Phase5DashboardError(
                f"metrics.jsonl {index + 1}행 global_step이 잘못됐습니다."
            )
        values.append(value)
    return values, trailing_partial_ignored


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def metrics_payload(context: dict[str, Any]) -> dict[str, Any]:
    rows, trailing = read_live_metrics(context["run_root"] / "metrics.jsonl")
    train: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    nonfinite: list[dict[str, Any]] = []
    for row in rows:
        safe: dict[str, Any] = {}
        for key in METRIC_FIELDS:
            if key not in row:
                continue
            numeric = _safe_number(row[key])
            safe[key] = numeric
            if numeric is None:
                nonfinite.append({"global_step": row["global_step"], "field": key})
        target = evaluation if "eval_loss" in row else train
        target.append(safe)
    return {
        "train": train,
        "evaluation": evaluation,
        "nonfinite": nonfinite,
        "trailing_partial_ignored": trailing,
        "source_mtime_utc": datetime.fromtimestamp(
            (context["run_root"] / "metrics.jsonl").stat().st_mtime, timezone.utc
        ).isoformat(),
    }


def _service_snapshot(unit: Any) -> dict[str, Any]:
    if not isinstance(unit, str) or SERVICE_PATTERN.fullmatch(unit) is None:
        return {"unit": None, "active": False, "sub_state": "invalid", "main_pid": None}
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "MainPID",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    values = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    active = values.get("ActiveState", "unknown")
    sub_state = values.get("SubState", "unknown")
    try:
        main_pid = int(values.get("MainPID", "0"))
    except ValueError:
        main_pid = 0
    return {
        "unit": unit,
        "active": result.returncode == 0 and active == "active",
        "active_state": active,
        "sub_state": sub_state,
        "main_pid": main_pid or None,
    }


def _gpu_snapshot() -> dict[str, Any]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"available": False, "error": "nvidia-smi unavailable"}
    values = [value.strip() for value in result.stdout.splitlines()[0].split(",")]
    if len(values) != 5:
        return {"available": False, "error": "unexpected nvidia-smi output"}
    try:
        return {
            "available": True,
            "index": int(values[0]),
            "name": values[1],
            "used_mib": int(values[2]),
            "total_mib": int(values[3]),
            "driver_version": values[4],
        }
    except ValueError:
        return {"available": False, "error": "invalid nvidia-smi numbers"}


def _directory_size(path: Path) -> tuple[int, bool]:
    total = 0
    symlink_seen = False
    for current, directories, files in os.walk(path, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                symlink_seen = True
            else:
                kept.append(name)
        directories[:] = kept
        for name in files:
            child = current_path / name
            if child.is_symlink():
                symlink_seen = True
                continue
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total, symlink_seen


def checkpoints_payload(
    context: dict[str, Any], *, now: float | None = None
) -> dict[str, Any]:
    root = context["run_root"]
    now_value = time.time() if now is None else now
    stabilization = context["config"]["training_contract"][
        "checkpoint_stabilization_seconds"
    ]
    milestones = set(
        context["config"]["training_contract"]["preserved_milestone_steps"]
    )
    values: list[dict[str, Any]] = []
    candidates = sorted(
        (path for path in root.glob("checkpoint-*") if path.is_dir()),
        key=lambda path: int(CHECKPOINT_PATTERN.fullmatch(path.name).group(1))
        if CHECKPOINT_PATTERN.fullmatch(path.name)
        else sys.maxsize,
    )
    if (root / "final").is_dir():
        candidates.append(root / "final")
    for path in candidates:
        try:
            match = CHECKPOINT_PATTERN.fullmatch(path.name)
            step = int(match.group(1)) if match else None
            required = REQUIRED_CHECKPOINT_FILES if match else REQUIRED_FINAL_FILES
            present = {child.name for child in path.iterdir() if child.is_file()}
            missing = sorted(required - present)
            modified = path.stat().st_mtime
            age = max(0.0, now_value - modified)
            size, symlink_seen = _directory_size(path)
        except FileNotFoundError:
            # save_total_limit rotation과 동시에 관측한 경로는 다음 refresh에서 다시 읽는다.
            continue
        if symlink_seen:
            status = "unsafe"
        elif missing and age < stabilization:
            status = "saving"
        elif missing:
            status = "incomplete"
        else:
            status = "complete"
        values.append(
            {
                "name": path.name,
                "step": step,
                "status": status,
                "size_bytes": size,
                "modified_at_utc": datetime.fromtimestamp(
                    modified, timezone.utc
                ).isoformat(),
                "missing_files": missing,
                "preserved_milestone": step in milestones if step is not None else False,
            }
        )
    disk = shutil.disk_usage(root)
    return {
        "items": values,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "incomplete_count": sum(
            value["status"] in {"incomplete", "unsafe"} for value in values
        ),
    }


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def status_payload(
    context: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    root = context["run_root"]
    manifest = _load_json(root / "run_manifest.json", "KI20 run manifest")
    marker = _load_json(root / "training_started.json", "KI20 start marker")
    metrics = metrics_payload(context)
    checkpoints = checkpoints_payload(context)
    service = _service_snapshot(marker.get("service_unit"))
    gpu = _gpu_snapshot()
    current = now or datetime.now(timezone.utc)
    latest_train = metrics["train"][-1] if metrics["train"] else None
    latest_eval = metrics["evaluation"][-1] if metrics["evaluation"] else None
    step = int(latest_train["global_step"]) if latest_train else 0
    expected = context["config"]["training_contract"]["expected_optimizer_steps"]
    started = _parse_utc(marker.get("started_at_utc"))
    eta: str | None = None
    elapsed_seconds: float | None = None
    if started is not None and step > 1:
        elapsed_seconds = max(0.0, (current - started).total_seconds())
        seconds_per_step = elapsed_seconds / (step - 1)
        eta = (current + timedelta(seconds=(expected - step) * seconds_per_step)).isoformat()
    mtime = datetime.fromtimestamp(
        (root / "metrics.jsonl").stat().st_mtime, timezone.utc
    )
    metric_age = max(0.0, (current - mtime).total_seconds())
    stale_after = max(
        context["config"]["training_contract"]["minimum_stale_seconds"],
        context["config"]["refresh_seconds"] * 12,
    )
    if service["active"]:
        lifecycle = "running"
    elif manifest.get("status") == "trained_and_reloaded":
        lifecycle = "complete"
    elif manifest.get("status") in {"failed", "interrupted"}:
        lifecycle = str(manifest["status"])
    else:
        lifecycle = "stopped_unexpectedly"
    alerts: list[dict[str, str]] = []
    if metrics["nonfinite"]:
        alerts.append({"level": "critical", "code": "nonfinite_metric", "message": "NaN/Inf metric이 있습니다."})
    if service["active"] and metric_age > stale_after:
        alerts.append({"level": "warning", "code": "stale_metrics", "message": "학습 서비스가 active지만 metric 갱신이 지연됐습니다."})
    if checkpoints["incomplete_count"]:
        alerts.append({"level": "warning", "code": "incomplete_checkpoint", "message": "안정화 후에도 불완전하거나 안전하지 않은 checkpoint가 있습니다."})
    configured_cap = context["config"]["training_contract"]["gpu_hard_cap_mib"]
    cap = min(configured_cap, gpu.get("total_mib", configured_cap))
    if gpu.get("available") and gpu["used_mib"] >= cap:
        alerts.append({"level": "critical", "code": "gpu_cap", "message": "GPU 전체 사용량이 16 GiB 상한 이상입니다."})
    elif gpu.get("available") and gpu["used_mib"] >= int(cap * 0.95):
        alerts.append({"level": "warning", "code": "gpu_near_cap", "message": "GPU 전체 사용량이 상한의 95% 이상입니다."})
    if lifecycle == "stopped_unexpectedly":
        alerts.append({"level": "critical", "code": "unexpected_stop", "message": "완료 상태가 아닌데 학습 서비스가 중지됐습니다."})
    return {
        "schema_version": "1.0.0",
        "dashboard_id": context["config"]["dashboard_id"],
        "run": {
            "run_id": manifest["run_id"],
            "run_build_id": manifest["run_build_id"],
            "run_sha256": manifest["run_sha256"],
            "workspace_commit": manifest.get("workspace_commit"),
            "manifest_status": manifest.get("status"),
            "lifecycle": lifecycle,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
        },
        "progress": {
            "global_step": step,
            "expected_optimizer_steps": expected,
            "percent": round(step * 100 / expected, 3),
            "epoch": latest_train.get("epoch") if latest_train else None,
            "started_at_utc": started.isoformat() if started else None,
            "elapsed_seconds": round(elapsed_seconds, 3) if elapsed_seconds is not None else None,
            "estimated_finish_at_utc": eta,
            "metric_age_seconds": round(metric_age, 3),
        },
        "latest_train": latest_train,
        "latest_eval": latest_eval,
        "service": service,
        "gpu": gpu,
        "checkpoint_summary": {
            "count": len(checkpoints["items"]),
            "incomplete_count": checkpoints["incomplete_count"],
            "disk_free_bytes": checkpoints["disk_free_bytes"],
        },
        "alerts": alerts,
        "diagnostic_only": True,
        "quality_gate_evaluated": False,
        "refreshed_at_utc": current.isoformat(),
    }


def _generation_gate(context: dict[str, Any]) -> dict[str, Any]:
    root = context["run_root"]
    manifest = _load_json(root / "run_manifest.json", "KI20 run manifest")
    marker = _load_json(root / "training_started.json", "KI20 start marker")
    service = _service_snapshot(marker.get("service_unit"))
    gpu = _gpu_snapshot()
    reasons: list[str] = []
    if service["active"]:
        reasons.append("training_service_active")
    if manifest.get("status") != "trained_and_reloaded":
        reasons.append("run_not_trained_and_reloaded")
    if not (root / "final").is_dir() or not (root / "reload_summary.json").is_file():
        reasons.append("final_reload_not_available")
    max_used = context["config"]["model_check"]["gpu_idle_max_used_mib"]
    if not gpu.get("available") or gpu.get("used_mib", max_used + 1) > max_used:
        reasons.append("gpu_not_idle")
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "service": service,
        "gpu": gpu,
    }


def model_checks_payload(context: dict[str, Any]) -> dict[str, Any]:
    gate = _generation_gate(context)
    output_root = context["run_root"] / context["config"]["model_check"][
        "private_output_relative"
    ]
    summary_path = output_root / "summary.json"
    rows_path = output_root / "model_checks.jsonl"
    if not summary_path.exists() and not rows_path.exists():
        return {"status": "not_run", "generation_gate": gate, "rows": [], "summary": None}
    if summary_path.is_symlink() or rows_path.is_symlink():
        raise Phase5DashboardError("model check 결과에 symlink가 있습니다.")
    summary = _load_json(summary_path, "model check summary")
    rows, trailing = read_live_metrics(rows_path)
    if trailing:
        raise Phase5DashboardError("완료된 model check JSONL이 부분 기록입니다.")
    visible = []
    for row in rows:
        visible.append(
            {
                key: row.get(key)
                for key in (
                    "global_step",
                    "eval_id",
                    "case_id",
                    "category",
                    "prompt_messages",
                    "ki10_output",
                    "ki20_output",
                )
            }
        )
    return {"status": "available", "generation_gate": gate, "rows": visible, "summary": summary}


def _select_probes(context: dict[str, Any]) -> list[dict[str, Any]]:
    config = context["config"]["model_check"]
    source = config["probe_source"]
    path = _safe_under(context["repo_root"], source["path"], "KI10 probe source")
    if path.is_symlink() or not path.is_file() or _sha256_file(path) != source["sha256"]:
        raise Phase5DashboardError("KI10 probe source hash가 다릅니다.")
    values: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Phase5DashboardError(f"KI10 probe source {index}행이 손상됐습니다.") from exc
        if not isinstance(row, dict):
            raise Phase5DashboardError("KI10 probe source 행이 object가 아닙니다.")
        values.append(row)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seed = config["selection_seed"]
    for row in values:
        category = row.get("category")
        if category in config["category_counts"]:
            grouped[category].append(row)
    selected: list[dict[str, Any]] = []
    for category, count in config["category_counts"].items():
        candidates = grouped.get(category, [])
        candidates.sort(
            key=lambda row: hashlib.sha256(
                f"{seed}|{row.get('eval_id')}|{row.get('case_id')}".encode()
            ).hexdigest()
        )
        if len(candidates) < count:
            raise Phase5DashboardError(f"probe category가 부족합니다: {category}")
        selected.extend(candidates[:count])
    selected.sort(key=lambda row: (str(row["category"]), str(row["eval_id"]), str(row["case_id"])))
    if len(selected) != 20 or len({(row["eval_id"], row["case_id"]) for row in selected}) != 20:
        raise Phase5DashboardError("고정 probe 20건 선택이 결정적이지 않습니다.")
    return selected


def _load_model(final_root: Path) -> tuple[Any, Any, Any]:
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5DashboardError("모델 검사 runtime import가 실패했습니다.") from exc
    if final_root.is_symlink() or not final_root.is_dir():
        raise Phase5DashboardError("final 모델 경로가 없거나 symlink입니다.")
    missing = REQUIRED_FINAL_FILES - {path.name for path in final_root.iterdir() if path.is_file()}
    if missing:
        raise Phase5DashboardError(f"final 모델 파일이 부족합니다: {sorted(missing)}")
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
    return torch, tokenizer, model


def _generate_many(
    final_root: Path, prompts: Sequence[list[dict[str, str]]], generation: dict[str, Any]
) -> list[str]:
    torch, tokenizer, model = _load_model(final_root)
    outputs: list[str] = []
    with torch.inference_mode():
        for messages in prompts:
            input_ids = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
            ).to("cuda:0")
            generated = model.generate(
                input_ids,
                do_sample=generation["do_sample"],
                num_beams=generation["num_beams"],
                max_new_tokens=generation["max_new_tokens"],
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            text = tokenizer.decode(
                generated[0, input_ids.shape[-1] :], skip_special_tokens=True
            ).strip()
            if not text:
                raise Phase5DashboardError("모델 검사 출력이 비었습니다.")
            outputs.append(text)
    return outputs


def _atomic_private_directory(target: Path, files: dict[str, bytes]) -> None:
    if target.exists():
        raise Phase5DashboardError("기존 model check 결과를 덮어쓸 수 없습니다.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for name, payload in files.items():
            path = temporary / name
            path.write_bytes(payload)
            path.chmod(PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def execute_fixed_probe(context: dict[str, Any]) -> dict[str, Any]:
    gate = _generation_gate(context)
    if not gate["allowed"]:
        raise Phase5DashboardError(
            "모델 검사 실행 조건을 충족하지 않습니다: " + ", ".join(gate["reasons"])
        )
    selected = _select_probes(context)
    generation = context["config"]["model_check"]["generation"]
    started = time.monotonic()
    ki20_outputs = _generate_many(
        context["run_root"] / "final",
        [row["prompt_messages"] for row in selected],
        generation,
    )
    comparison: list[dict[str, Any]] = []
    ki10_for_score: list[dict[str, Any]] = []
    ki20_for_score: list[dict[str, Any]] = []
    for index, (row, output) in enumerate(zip(selected, ki20_outputs, strict=True), 1):
        common = {
            "eval_id": row["eval_id"],
            "case_id": row["case_id"],
            "category": row["category"],
            "source_axis": row.get("source_axis"),
            "automated_contract": row["automated_contract"],
            "prompt_messages": row["prompt_messages"],
        }
        ki10_for_score.append({**common, "output": row["output"]})
        ki20_for_score.append({**common, "output": output})
        comparison.append(
            {
                "global_step": index,
                **common,
                "ki10_output": row["output"],
                "ki20_output": output,
            }
        )
    thresholds = context["config"]["model_check"]["diagnostic_thresholds"]
    ki10_score = score_generations(ki10_for_score, thresholds)
    ki20_score = score_generations(ki20_for_score, thresholds)
    rows_payload = b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
        for row in comparison
    )
    summary = {
        "schema_version": "1.0.0",
        "status": "diagnostic_complete",
        "run_id": context["manifest"]["run_id"],
        "run_build_id": context["manifest"]["run_build_id"],
        "run_sha256": context["manifest"]["run_sha256"],
        "probe_count": 20,
        "selection_seed": context["config"]["model_check"]["selection_seed"],
        "generation": generation,
        "ki10_diagnostic": ki10_score,
        "ki20_diagnostic": ki20_score,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "quality_gate_evaluated": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "raw_prompts_in_summary": False,
        "model_checks_sha256": hashlib.sha256(rows_payload).hexdigest(),
    }
    target = context["run_root"] / context["config"]["model_check"][
        "private_output_relative"
    ]
    _atomic_private_directory(
        target,
        {"model_checks.jsonl": rows_payload, "summary.json": _json_bytes(summary)},
    )
    return summary


def execute_manual_generation(context: dict[str, Any], prompt: str) -> dict[str, Any]:
    gate = _generation_gate(context)
    if not gate["allowed"]:
        raise Phase5DashboardError(
            "수동 생성 조건을 충족하지 않습니다: " + ", ".join(gate["reasons"])
        )
    if (
        not prompt.strip()
        or len(prompt) > context["config"]["server"]["max_prompt_chars"]
        or CONTROL_PATTERN.search(prompt)
    ):
        raise Phase5DashboardError("수동 질문 길이 또는 문자가 허용 범위를 벗어납니다.")
    started = time.monotonic()
    output = _generate_many(
        context["run_root"] / "final",
        [[{"role": "user", "content": prompt.strip()}]],
        context["config"]["model_check"]["generation"],
    )[0]
    return {
        "status": "generated",
        "output": output,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "persisted": False,
        "quality_gate_evaluated": False,
        "production_promotion_allowed": False,
    }


class DashboardHTTPServer(ThreadingHTTPServer):
    """loopback 전용 대시보드 context와 CSRF token을 보관한다."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        context: dict[str, Any],
        asset_root: Path,
        csrf_token: str,
    ) -> None:
        self.context = context
        self.asset_root = asset_root
        self.csrf_token = csrf_token
        self.generation_lock = threading.Lock()
        super().__init__(address, DashboardRequestHandler)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{value}" for value in self.allowed_hosts}


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """정적 UI와 read-mostly JSON API를 보안 헤더와 함께 제공한다."""

    server: DashboardHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
        sys.stderr.write(f"phase5-dashboard {self.command} {urlsplit(self.path).path}\n")

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self._headers(status, content_type, len(payload))
        self.wfile.write(payload)

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self.close_connection = True
        self._send_json(status, {"status": status, "error": message})

    def _guard(self, *, require_origin: bool = False) -> str:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            raise DashboardRequestError(HTTPStatus.MISDIRECTED_REQUEST, "허용되지 않은 Host입니다.")
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or "%" in parsed.path:
            raise DashboardRequestError(HTTPStatus.BAD_REQUEST, "쿼리·인코딩 경로는 지원하지 않습니다.")
        if require_origin and self.headers.get("Origin") not in self.server.allowed_origins:
            raise DashboardRequestError(HTTPStatus.FORBIDDEN, "허용되지 않은 Origin입니다.")
        return parsed.path

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-CSRF-Token", ""), self.server.csrf_token
        )

    def _static(self, path: str) -> tuple[bytes, str]:
        asset = STATIC_ASSETS.get(path)
        if asset is None:
            raise DashboardRequestError(HTTPStatus.NOT_FOUND, "정적 자산을 찾을 수 없습니다.")
        filename, content_type = asset
        target = self.server.asset_root / filename
        if target.is_symlink() or not target.is_file():
            raise DashboardRequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "정적 자산이 없습니다.")
        payload = target.read_bytes()
        if filename == "index.html":
            placeholder = b"__CSRF_TOKEN__"
            if payload.count(placeholder) != 1:
                raise DashboardRequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "CSRF placeholder가 잘못됐습니다.")
            payload = payload.replace(placeholder, self.server.csrf_token.encode("ascii"))
        return payload, content_type

    def do_GET(self) -> None:
        try:
            path = self._guard()
            if path.startswith("/api/"):
                if not self._authorized():
                    raise DashboardRequestError(HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다.")
                if path == "/api/status":
                    self._send_json(HTTPStatus.OK, status_payload(self.server.context))
                    return
                if path == "/api/metrics":
                    self._send_json(HTTPStatus.OK, metrics_payload(self.server.context))
                    return
                if path == "/api/checkpoints":
                    self._send_json(HTTPStatus.OK, checkpoints_payload(self.server.context))
                    return
                if path == "/api/model-checks":
                    self._send_json(HTTPStatus.OK, model_checks_payload(self.server.context))
                    return
                raise DashboardRequestError(HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다.")
            payload, content_type = self._static(path)
            self._send_bytes(HTTPStatus.OK, payload, content_type)
        except DashboardRequestError as exc:
            self._error(exc.status, str(exc))
        except (OSError, Phase5DashboardError, subprocess.SubprocessError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _request_json(self) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise DashboardRequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON 요청만 허용됩니다.")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError as exc:
            raise DashboardRequestError(HTTPStatus.BAD_REQUEST, "Content-Length가 잘못됐습니다.") from exc
        maximum = self.server.context["config"]["server"]["max_request_bytes"]
        if length < 2 or length > maximum:
            raise DashboardRequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "요청 크기가 허용 범위를 벗어납니다.")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DashboardRequestError(HTTPStatus.BAD_REQUEST, "JSON 요청이 잘못됐습니다.") from exc
        if not isinstance(value, dict):
            raise DashboardRequestError(HTTPStatus.BAD_REQUEST, "JSON object만 허용됩니다.")
        return value

    def do_POST(self) -> None:
        try:
            path = self._guard(require_origin=True)
            if not self._authorized():
                raise DashboardRequestError(HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다.")
            if path not in {"/api/generate", "/api/probe"}:
                raise DashboardRequestError(HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다.")
            payload = self._request_json()
            gate = _generation_gate(self.server.context)
            if not gate["allowed"]:
                raise DashboardRequestError(HTTPStatus.CONFLICT, "학습 중이거나 final 모델이 준비되지 않았습니다.")
            if not self.server.generation_lock.acquire(blocking=False):
                raise DashboardRequestError(HTTPStatus.CONFLICT, "다른 모델 생성이 실행 중입니다.")
            try:
                if path == "/api/generate":
                    if set(payload) != {"prompt"} or not isinstance(
                        payload["prompt"], str
                    ):
                        raise DashboardRequestError(
                            HTTPStatus.BAD_REQUEST, "prompt 문자열만 허용됩니다."
                        )
                    result = _manual_generation_subprocess(
                        self.server.context, payload["prompt"]
                    )
                else:
                    if payload:
                        raise DashboardRequestError(
                            HTTPStatus.BAD_REQUEST,
                            "고정 probe 요청은 빈 object여야 합니다.",
                        )
                    output_root = self.server.context["run_root"] / self.server.context[
                        "config"
                    ]["model_check"]["private_output_relative"]
                    if output_root.exists():
                        raise DashboardRequestError(
                            HTTPStatus.CONFLICT, "고정 probe 결과가 이미 있습니다."
                        )
                    result = _fixed_probe_subprocess(self.server.context)
            finally:
                self.server.generation_lock.release()
            self._send_json(HTTPStatus.OK, result)
        except DashboardRequestError as exc:
            self._error(exc.status, str(exc))
        except (OSError, Phase5DashboardError, subprocess.SubprocessError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def _manual_generation_subprocess(
    context: dict[str, Any], prompt: str
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(context["config_path"]),
        "--run-root",
        str(context["run_root"]),
        "generate",
        "--execute",
    ]
    result = subprocess.run(
        command,
        input=json.dumps({"prompt": prompt}, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=context["repo_root"],
    )
    if result.returncode != 0:
        raise Phase5DashboardError(
            "수동 모델 생성이 실패했습니다: " + result.stderr[-500:]
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Phase5DashboardError("수동 모델 생성 결과가 JSON이 아닙니다.") from exc
    if not isinstance(value, dict) or value.get("persisted") is not False:
        raise Phase5DashboardError("수동 모델 생성 결과 계약이 다릅니다.")
    return value


def _fixed_probe_subprocess(context: dict[str, Any]) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--config",
        str(context["config_path"]),
        "--run-root",
        str(context["run_root"]),
        "probe",
        "--execute",
    ]
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
        cwd=context["repo_root"],
    )
    if result.returncode != 0:
        raise Phase5DashboardError(
            "고정 20건 모델 검사가 실패했습니다: " + result.stderr[-500:]
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Phase5DashboardError("고정 20건 모델 검사 결과가 JSON이 아닙니다.") from exc
    if (
        not isinstance(value, dict)
        or value.get("status") != "diagnostic_complete"
        or value.get("probe_count") != 20
        or value.get("production_promotion_allowed") is not False
    ):
        raise Phase5DashboardError("고정 20건 모델 검사 결과 계약이 다릅니다.")
    return value


def serve(context: dict[str, Any], host: str, port: int) -> None:
    if host != "127.0.0.1" or not 1 <= port <= 65535:
        raise Phase5DashboardError("대시보드는 127.0.0.1의 유효한 port에만 열 수 있습니다.")
    server = DashboardHTTPServer(
        (host, port), context, ASSET_ROOT, secrets.token_hex(24)
    )
    actual_port = server.server_address[1]
    print(f"Phase 5 dashboard: http://127.0.0.1:{actual_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KI20 로컬 학습·모델 검사 대시보드")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-root", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="현재 상태 JSON 출력")
    status.set_defaults(execute=False)
    serve_parser = subparsers.add_parser("serve", help="loopback dashboard 실행")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    probe = subparsers.add_parser("probe", help="완료 모델 고정 20건 비교")
    probe.add_argument("--execute", action="store_true")
    generate = subparsers.add_parser("generate", help=argparse.SUPPRESS)
    generate.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    run_root = args.run_root if args.run_root.is_absolute() else REPO_ROOT / args.run_root
    try:
        context = prepare_context(REPO_ROOT, config_path, run_root)
        if args.command == "status":
            result = status_payload(context)
        elif args.command == "serve":
            serve(context, args.host, args.port)
            return 0
        elif args.command == "probe":
            if not args.execute:
                result = {"status": "dry_run", "generation_gate": _generation_gate(context), "writes_performed": False}
            else:
                result = execute_fixed_probe(context)
        elif args.command == "generate":
            if not args.execute:
                raise Phase5DashboardError("수동 generation에는 --execute가 필요합니다.")
            payload = json.loads(sys.stdin.read())
            if not isinstance(payload, dict) or set(payload) != {"prompt"} or not isinstance(payload["prompt"], str):
                raise Phase5DashboardError("수동 generation stdin 계약이 다릅니다.")
            result = execute_manual_generation(context, payload["prompt"])
        else:
            raise Phase5DashboardError("지원하지 않는 command입니다.")
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        Phase5DashboardError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
