# phase5_dashboard_v1_12.py - 승인 원국·일별 기간의 명시적 대화 연결과 자동 Gate를 제공한다.

from __future__ import annotations

import argparse
import base64
import binascii
import gc
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
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

from scripts.runtime.period_v1.engine import validate_public_daily_label_result
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.training.phase5_quality import score_generations

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.12.0.json"
)
ASSET_ROOT = Path(__file__).with_name("phase5_dashboard_assets")
V112_ASSET_ROOT = ASSET_ROOT / "v1.12.0"
RUN_BUILD_PATTERN = re.compile(r"^run-[0-9a-f]{12}$")
GUIDED_PROMPT_SHA256 = (
    "d2aa55a54bfab253669a56570ceca63e02b8d688d3699e40c9258ac6f7c18232"
)
BOUND_CHART_PROMPT_SHA256 = (
    "b5d4df4e4e38040aa15c372ca670c91e59dfa3b332369e5477a0a6d43884583d"
)
GROUNDING_GATE_ID = "saju-bound-chart-grounding-v1.0.0"
GROUNDING_FAILURE_CODE = "RUNTIME_GROUNDING_FAILED"
CHECKPOINT_PATTERN = re.compile(r"^checkpoint-([1-9][0-9]*)$")
SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@:-]+\.service$")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
RUNTIME_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
REMOTE_BASIC_AUTH_USER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
REMOTE_PASSWORD_MIN_BYTES = 32
REMOTE_PASSWORD_MAX_BYTES = 4096
AUTHORIZATION_HEADER_MAX_BYTES = 8192
MAX_METRICS_BYTES = 32 * 1024 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
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
    "/prompt-examples.json": (
        "prompt-examples.json",
        "application/json; charset=utf-8",
    ),
}


class Phase5DashboardError(RuntimeError):
    """대시보드 입력·run·비공개 경계가 계약과 다를 때 발생한다."""


class GroundingGateError(Phase5DashboardError):
    """원국 연결 응답이 자동 grounding 계약을 끝내 충족하지 못한 경우다."""


class DashboardRequestError(RuntimeError):
    """HTTP 요청에 안전하게 반환할 상태를 보관한다."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        reason_code: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason_code = reason_code or f"HTTP_{int(status)}"
        self.retry_after = retry_after


class SlidingWindowRateLimiter:
    """신뢰할 수 없는 proxy IP 대신 process 전체 요청량을 제한한다."""

    def __init__(self, maximum: int, *, window_seconds: float = 60.0) -> None:
        if maximum < 1 or window_seconds <= 0:
            raise ValueError("rate limiter 한도가 잘못됐습니다.")
        self.maximum = maximum
        self.window_seconds = float(window_seconds)
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, *, now: float | None = None) -> tuple[bool, int]:
        current = time.monotonic() if now is None else float(now)
        cutoff = current - self.window_seconds
        with self._lock:
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.maximum:
                retry_after = max(
                    1,
                    math.ceil(self.window_seconds - (current - self._timestamps[0])),
                )
                return False, retry_after
            self._timestamps.append(current)
        return True, 0


def _validated_trusted_origin(value: str) -> str:
    if not isinstance(value, str) or len(value) > 261:
        raise Phase5DashboardError("원격 공유 Origin이 유효하지 않습니다.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise Phase5DashboardError("원격 공유 Origin이 유효하지 않습니다.") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or hostname is None
        or parsed.netloc != hostname
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or len(hostname) > 253
    ):
        raise Phase5DashboardError(
            "원격 공유 Origin은 port·경로 없는 정확한 HTTPS DNS origin이어야 합니다."
        )
    labels = hostname.split(".")
    if len(labels) < 2 or any(
        not 1 <= len(label) <= 63
        or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is None
        for label in labels
    ):
        raise Phase5DashboardError("원격 공유 Origin DNS 이름이 유효하지 않습니다.")
    return value


def _load_remote_password(path: Path) -> str:
    if not path.is_absolute():
        raise Phase5DashboardError("원격 공유 비밀번호 파일은 절대 경로여야 합니다.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != PRIVATE_FILE_MODE
            or metadata.st_uid != os.getuid()
            or not 1 <= metadata.st_size <= REMOTE_PASSWORD_MAX_BYTES
        ):
            raise Phase5DashboardError(
                "원격 공유 비밀번호 파일은 현재 사용자 소유의 0600 일반 파일이어야 합니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(REMOTE_PASSWORD_MAX_BYTES + 1)
    except OSError as exc:
        raise Phase5DashboardError(
            "원격 공유 비밀번호 파일을 안전하게 읽을 수 없습니다."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if payload.endswith(b"\n"):
        payload = payload[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
    if not REMOTE_PASSWORD_MIN_BYTES <= len(
        payload
    ) <= REMOTE_PASSWORD_MAX_BYTES or any(
        byte < 0x21 or byte > 0x7E for byte in payload
    ):
        raise Phase5DashboardError(
            "원격 공유 비밀번호는 32자 이상의 한 줄 ASCII 값이어야 합니다."
        )
    return payload.decode("ascii")


def _remote_access_settings(
    trusted_origin: str | None,
    basic_auth_user: str | None,
    basic_auth_password_file: Path | None,
    allow_unauthenticated_remote: bool = False,
) -> tuple[str | None, tuple[str, str] | None]:
    if allow_unauthenticated_remote:
        if (
            trusted_origin is None
            or basic_auth_user is not None
            or basic_auth_password_file is not None
        ):
            raise Phase5DashboardError(
                "무인증 원격 공유에는 exact trusted origin만 함께 지정해야 합니다."
            )
        return _validated_trusted_origin(trusted_origin), None
    supplied = (
        trusted_origin is not None,
        basic_auth_user is not None,
        basic_auth_password_file is not None,
    )
    if not any(supplied):
        return None, None
    if not all(supplied):
        raise Phase5DashboardError(
            "원격 공유에는 trusted origin·사용자·비밀번호 파일이 모두 필요합니다."
        )
    assert trusted_origin is not None
    assert basic_auth_user is not None
    assert basic_auth_password_file is not None
    if REMOTE_BASIC_AUTH_USER_PATTERN.fullmatch(basic_auth_user) is None:
        raise Phase5DashboardError("원격 공유 Basic 인증 사용자명이 유효하지 않습니다.")
    return (
        _validated_trusted_origin(trusted_origin),
        (basic_auth_user, _load_remote_password(basic_auth_password_file)),
    )


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


def _validate_prompt_profiles(
    value: Any, governance: dict[str, Any], schema_version: str
) -> None:
    if not isinstance(value, dict):
        raise Phase5DashboardError("Phase 5 prompt profile 계약이 없습니다.")
    profiles = value.get("profiles")
    if (
        value.get("default_profile") != "guided_diagnostic_v1"
        or value.get("legacy_profile") != "raw_legacy"
        or not isinstance(profiles, dict)
        or set(profiles)
        != (
            {"guided_diagnostic_v1", "bound_chart_v1", "raw_no_system"}
            if schema_version in {"1.10.0", "1.12.0"}
            else {"guided_diagnostic_v1", "raw_no_system"}
        )
        or (
            schema_version in {"1.10.0", "1.12.0"}
            and value.get("bound_profile") != "bound_chart_v1"
        )
        or governance.get("manual_default_profile_guided") is not True
        or governance.get("raw_profile_diagnostic_only") is not True
    ):
        raise Phase5DashboardError("Phase 5 prompt profile 계약이 다릅니다.")
    production = profiles["guided_diagnostic_v1"]
    raw = profiles["raw_no_system"]
    prompt = production.get("system_prompt") if isinstance(production, dict) else None
    if (
        not isinstance(production, dict)
        or production.get("label") != "안내 보정 진단"
        or not isinstance(production.get("description"), str)
        or production.get("production_like") is not False
        or production.get("diagnostic_only") is not True
        or not isinstance(prompt, dict)
        or prompt.get("path") != "configs/chat_prompts/saju_intake_handoff_v1.txt"
        or prompt.get("bytes") != 1805
        or prompt.get("sha256") != GUIDED_PROMPT_SHA256
        or not isinstance(raw, dict)
        or raw.get("label") != "무지시 원출력"
        or not isinstance(raw.get("description"), str)
        or raw.get("system_prompt") is not None
        or raw.get("production_like") is not False
        or raw.get("diagnostic_only") is not True
    ):
        raise Phase5DashboardError("Phase 5 prompt profile 세부 계약이 다릅니다.")
    if schema_version in {"1.10.0", "1.12.0"}:
        bound = profiles["bound_chart_v1"]
        bound_prompt = bound.get("system_prompt") if isinstance(bound, dict) else None
        if (
            not isinstance(bound, dict)
            or bound.get("label") != "승인 원국 연결"
            or not isinstance(bound.get("description"), str)
            or bound.get("production_like") is not True
            or bound.get("diagnostic_only") is not False
            or not isinstance(bound_prompt, dict)
            or bound_prompt.get("path")
            != "configs/chat_prompts/saju_bound_chart_v1.txt"
            or bound_prompt.get("bytes") != 1886
            or bound_prompt.get("sha256") != BOUND_CHART_PROMPT_SHA256
            or governance.get("runtime_bound_profile_forced") is not True
            or governance.get("runtime_grounding_gate_required") is not True
            or governance.get("runtime_rejected_output_persisted") is not False
        ):
            raise Phase5DashboardError("Phase 5 원국 연결 prompt 계약이 다릅니다.")


def _validate_runtime_canary(value: Any, governance: dict[str, Any]) -> None:
    expected_allowlist = [
        "status",
        "hard_facts",
        "fact_authority",
        "code",
        "message",
        "limitations",
    ]
    if (
        not isinstance(value, dict)
        or value.get("enabled_by_default") is not False
        or value.get("release_registry")
        != "configs/runtime/calculation/releases/v1.1.0/release_registry.json"
        or value.get("engine_version") != "saju-runtime-python-v1.1.0"
        or value.get("profile_id") != "KR_CIVIL_MIDNIGHT_V1"
        or value.get("private_output_relative") != "dashboard/v1.8.0/runtime_sessions"
        or value.get("max_state_bytes") != 1_000_000
        or value.get("country_code") != "KR"
        or value.get("timezone") != "Asia/Seoul"
        or value.get("calendars") != ["solar", "lunar"]
        or value.get("time_precisions") != ["exact", "range", "unknown"]
        or value.get("period_types") != ["day", "week", "month", "year"]
        or value.get("remote_unauthenticated_requires_explicit_flag") is not True
        or value.get("model_visible_allowlist") != expected_allowlist
        or governance.get("runtime_feature_default_off") is not True
        or governance.get("runtime_release_required") is not True
        or governance.get("runtime_facts_rendered_without_model") is not True
        or governance.get("runtime_internal_trace_model_visible") is not False
        or governance.get("runtime_requests_logged") is not False
        or governance.get("runtime_state_local_only") is not True
    ):
        raise Phase5DashboardError(
            "Phase 5 dashboard v1.8 runtime canary 계약이 다릅니다."
        )


def _validate_period_runtime(
    value: Any, governance: dict[str, Any], schema_version: str
) -> None:
    chart_allowlist = [
        "status",
        "fact_authority",
        "hard_facts",
        "message",
        "limitations",
    ]
    period_allowlist = [
        "status",
        "fact_authority",
        "period_scope",
        "days",
        "boundary_capability",
        "message",
        "limitations",
    ]
    expected_expressions = [
        "today",
        "tomorrow",
        "this_weekend",
        "this_week",
        "this_month",
        "explicit",
    ]
    expected_canary = {
        "total_cases": 200,
        "feature_off": 10,
        "relative_dates": 40,
        "explicit_ranges": 30,
        "label_boundaries": 30,
        "process_restart": 20,
        "security_tamper_rate": 30,
        "unsupported_scope": 10,
        "same_context_k0_ki20": 10,
        "public_leakage": 20,
    }
    if (
        schema_version != "1.12.0"
        or not isinstance(value, dict)
        or value.get("enabled_by_default") is not False
        or value.get("explicit_enable_required") is not True
        or value.get("binding_id") != "saju-period-dashboard-binding-v1.2.0"
        or value.get("parent_release_registry")
        != "configs/runtime/calculation/releases/v1.5.0/release_registry.json"
        or value.get("parent_release_id") != "saju-runtime-release-v1.5.0-8b1d6ea2d46e"
        or value.get("period_release_registry")
        != "configs/runtime/period/releases/v1.0.0/release_registry.json"
        or value.get("period_release_id")
        != "saju-period-daily-label-release-v1.0.0-59e326f8f086"
        or value.get("engine_version") != "saju-period-daily-label-runtime-v1.0.0"
        or value.get("profile_id") != "KR_CIVIL_MIDNIGHT_V1"
        or value.get("asset_root") != "scripts/training/phase5_dashboard_assets/v1.12.0"
        or value.get("country_code") != "KR"
        or value.get("timezone") != "Asia/Seoul"
        or value.get("calendars") != ["solar", "lunar"]
        or value.get("time_precisions") != ["exact", "range", "unknown"]
        or value.get("minimum_solar_date") != "1920-01-07"
        or value.get("maximum_solar_date") != "2026-08-31"
        or value.get("retention_seconds") != 1800
        or value.get("maximum_sessions") != 100
        or value.get("client_authentication") != "none"
        or value.get("exact_host_origin_and_csrf_required") is not True
        or value.get("single_owning_process_required") is not True
        or value.get("stale_revision_rejected") is not True
        or value.get("period_calculation_allowed") is not True
        or value.get("explicit_conversation_binding_required") is not True
        or value.get("allowed_period_types") != ["daily_label_range"]
        or value.get("allowed_date_expressions") != expected_expressions
        or value.get("period_minimum") != "2026-09-02"
        or value.get("period_maximum") != "2049-12-31"
        or value.get("period_maximum_days") != 31
        or value.get("period_evaluation_local_time") != "12:00"
        or value.get("period_requires_exact_time") is not True
        or value.get("period_server_kst_today_floor") is not True
        or value.get("intraday_segments_supported") is not False
        or value.get("free_text_date_parser_allowed") is not False
        or value.get("model_context_binding") is not True
        or value.get("chart_model_visible_allowlist") != chart_allowlist
        or value.get("period_model_visible_allowlist") != period_allowlist
        or value.get("rate_limits_per_minute")
        != {
            "session_or_chart": 30,
            "runtime_event": 300,
            "model_generation": 10,
        }
        or value.get("legacy_runtime_routes_status") != 410
        or value.get("automatic_canary") != expected_canary
        or governance.get("runtime_feature_default_off") is not True
        or governance.get("runtime_release_required") is not True
        or governance.get("runtime_facts_rendered_without_model") is not True
        or governance.get("runtime_internal_trace_model_visible") is not False
        or governance.get("runtime_requests_logged") is not False
        or governance.get("runtime_state_local_only") is not True
        or governance.get("runtime_state_encrypted") is not True
        or governance.get("runtime_birth_data_logged") is not False
        or governance.get("runtime_identifiers_logged") is not False
        or governance.get("production_application_binding") is not True
        or governance.get("public_client_authentication_required") is not False
        or governance.get("period_runtime_allowed") is not True
        or governance.get("period_runtime_daily_labels_only") is not True
        or governance.get("period_feature_default_off") is not True
        or governance.get("mix20k_v3_1_generation_allowed") is not False
        or governance.get("training_execution_allowed") is not False
        or governance.get("model_promotion_allowed") is not False
    ):
        raise Phase5DashboardError("Phase 5 dashboard 제한 runtime 계약이 다릅니다.")


def _validate_grounding_gate(value: Any) -> None:
    if value != {
        "gate_id": GROUNDING_GATE_ID,
        "bound_profile": "bound_chart_v1",
        "maximum_correction_retries": 1,
        "failure_code": GROUNDING_FAILURE_CODE,
        "rejected_output_persisted": False,
    }:
        raise Phase5DashboardError("Phase 5 원국 grounding Gate 계약이 다릅니다.")


def _validate_inference_engines(value: Any, governance: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise Phase5DashboardError("Phase 5 inference engine 계약이 없습니다.")
    engines = value.get("engines")
    selections = value.get("selections")
    if (
        value.get("default_selection") != "ki20_final"
        or value.get("sequential_load_only") is not True
        or value.get("single_timeout_seconds") != 300
        or value.get("paired_timeout_seconds") != 600
        or not isinstance(engines, dict)
        or set(engines) != {"ki20_final", "k0_instruct"}
        or not isinstance(selections, dict)
        or set(selections) != {"ki20_final", "k0_instruct", "k0_vs_ki20"}
        or governance.get("manual_inference_diagnostic_only") is not True
        or governance.get("paired_models_loaded_sequentially") is not True
    ):
        raise Phase5DashboardError("Phase 5 inference engine 계약이 다릅니다.")
    ki20 = engines["ki20_final"]
    k0 = engines["k0_instruct"]
    if (
        not isinstance(ki20, dict)
        or ki20.get("label") != "KI20"
        or ki20.get("kind") != "run_final"
        or ki20.get("revision") != "run-1f5d732cae67"
        or ki20.get("model_sha256")
        != "2fae23e28471c07d7db0c338bc6370493191722180ecc502de7e1e1d5fe5872d"
        or not isinstance(k0, dict)
        or k0.get("label") != "K0 원본"
        or k0.get("kind") != "fixed_snapshot"
        or k0.get("repo_id") != "kakaocorp/kanana-2-1.3b-instruct"
        or k0.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or k0.get("path")
        != "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
        "bf4786aa2a1908adce942d53976270132732f720"
        or k0.get("model_sha256")
        != "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942"
        or k0.get("snapshot_manifest_sha256")
        != "5786d04831c93192d234651df0894a1912b974cfab96011ce0676563185cc93d"
    ):
        raise Phase5DashboardError("Phase 5 inference engine identity가 다릅니다.")
    required_names = {
        "chat_template.jinja",
        "config.json",
        "configuration_kanana2_tiny.py",
        "model.safetensors",
        "modeling_kanana2_tiny.py",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    for engine_id, engine in engines.items():
        file_hashes = engine.get("required_file_sha256")
        if (
            not isinstance(file_hashes, dict)
            or set(file_hashes) != required_names
            or file_hashes.get("model.safetensors") != engine["model_sha256"]
            or any(
                not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                for digest in file_hashes.values()
            )
        ):
            raise Phase5DashboardError(
                f"Phase 5 inference engine file hash 계약이 다릅니다: {engine_id}"
            )
    expected = {
        "ki20_final": ("single", ["ki20_final"]),
        "k0_instruct": ("single", ["k0_instruct"]),
        "k0_vs_ki20": ("paired", ["k0_instruct", "ki20_final"]),
    }
    for selection_id, (mode, engine_ids) in expected.items():
        selection = selections[selection_id]
        if (
            not isinstance(selection, dict)
            or not isinstance(selection.get("label"), str)
            or not selection["label"]
            or selection.get("mode") != mode
            or selection.get("engine_ids") != engine_ids
        ):
            raise Phase5DashboardError(
                f"Phase 5 inference selection 계약이 다릅니다: {selection_id}"
            )


def validate_config(config: dict[str, Any]) -> None:
    server = config.get("server")
    remote_share = server.get("remote_share") if isinstance(server, dict) else None
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
    schema_version = config.get("schema_version")
    if (
        schema_version
        not in {
            "1.0.0",
            "1.1.0",
            "1.2.0",
            "1.3.0",
            "1.4.0",
            "1.5.0",
            "1.6.0",
            "1.7.0",
            "1.8.0",
            "1.9.0",
            "1.10.0",
            "1.12.0",
        }
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
        or model_check.get("diagnostic_thresholds", {}).get("expected_generation_cases")
        != 20
        or not isinstance(governance, dict)
        or governance.get("training_control_actions_allowed") is not False
        or governance.get("sealed_blind_access_allowed") is not False
        or governance.get("production_promotion_allowed") is not False
        or governance.get("fixed_probe_results_private") is not True
    ):
        raise Phase5DashboardError("Phase 5 dashboard config 계약이 다릅니다.")
    expected_authenticated_remote_share = {
        "enabled_by_default": False,
        "exact_https_origin_required": True,
        "basic_auth_required": True,
        "wildcard_origins_allowed": False,
        "password_file_mode": "0600",
        "minimum_password_bytes": REMOTE_PASSWORD_MIN_BYTES,
    }
    if schema_version == "1.6.0":
        if remote_share != expected_authenticated_remote_share:
            raise Phase5DashboardError(
                "Phase 5 dashboard v1.6 원격 공유 계약이 다릅니다."
            )
    elif schema_version in {"1.7.0", "1.8.0", "1.9.0", "1.10.0", "1.12.0"}:
        expected_unauthenticated_remote_share = {
            "enabled_by_default": False,
            "exact_https_origin_required": True,
            "basic_auth_supported": True,
            "unauthenticated_remote_requires_explicit_flag": True,
            "wildcard_origins_allowed": False,
            "password_file_mode": "0600",
            "minimum_password_bytes": REMOTE_PASSWORD_MIN_BYTES,
        }
        if remote_share != expected_unauthenticated_remote_share:
            raise Phase5DashboardError(
                "Phase 5 dashboard v1.7+ 원격 공유 계약이 다릅니다."
            )
    elif remote_share is not None:
        raise Phase5DashboardError("과거 dashboard config에 원격 공유 계약이 있습니다.")
    manual_session = config.get("manual_session")
    if schema_version == "1.0.0":
        if (
            governance.get("manual_prompts_persisted") is not False
            or manual_session is not None
        ):
            raise Phase5DashboardError(
                "Phase 5 dashboard v1.0 수동 질문 계약이 다릅니다."
            )
    elif (
        governance.get("manual_prompts_persisted") is not True
        or governance.get("manual_sessions_local_only") is not True
        or not isinstance(manual_session, dict)
        or manual_session.get("private_output_relative")
        != "dashboard/v1.1.0/manual_sessions"
        or manual_session.get("max_sessions") != 100
        or manual_session.get("max_turns_per_session") != 50
        or manual_session.get("max_context_tokens") != 3584
        or manual_session.get("max_session_bytes") != 2_000_000
        or manual_session.get("title_max_chars") != 60
    ):
        raise Phase5DashboardError("Phase 5 dashboard v1.1 세션 계약이 다릅니다.")
    dataset_browser = config.get("dataset_browser")
    prompt_profiles = config.get("prompt_profiles")
    inference_engines = config.get("inference_engines")
    if schema_version in {"1.0.0", "1.1.0"}:
        if dataset_browser is not None:
            raise Phase5DashboardError(
                "과거 dashboard config에 dataset browser가 있습니다."
            )
    else:
        _validate_dataset_browser(dataset_browser, governance, schema_version)
    if schema_version in {
        "1.3.0",
        "1.4.0",
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
        "1.9.0",
        "1.10.0",
        "1.12.0",
    }:
        _validate_prompt_profiles(prompt_profiles, governance, schema_version)
    elif prompt_profiles is not None:
        raise Phase5DashboardError("과거 dashboard config에 prompt profile이 있습니다.")
    if schema_version in {
        "1.4.0",
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
        "1.9.0",
        "1.10.0",
        "1.12.0",
    }:
        _validate_inference_engines(inference_engines, governance)
    elif inference_engines is not None:
        raise Phase5DashboardError(
            "과거 dashboard config에 inference engine이 있습니다."
        )
    runtime_canary = config.get("runtime_canary")
    period_runtime = config.get("period_runtime")
    if schema_version == "1.8.0":
        _validate_runtime_canary(runtime_canary, governance)
        if period_runtime is not None:
            raise Phase5DashboardError(
                "dashboard v1.8 config에 chart-only production binding이 있습니다."
            )
    elif schema_version in {"1.9.0", "1.10.0", "1.12.0"}:
        if runtime_canary is not None:
            raise Phase5DashboardError(
                "dashboard chart-only config에 legacy runtime canary가 있습니다."
            )
        _validate_period_runtime(period_runtime, governance, schema_version)
    elif runtime_canary is not None:
        raise Phase5DashboardError("과거 dashboard config에 runtime canary가 있습니다.")
    elif period_runtime is not None:
        raise Phase5DashboardError(
            "과거 dashboard config에 chart-only production binding이 있습니다."
        )
    grounding_gate = config.get("grounding_gate")
    if schema_version in {"1.10.0", "1.12.0"}:
        _validate_grounding_gate(grounding_gate)
    elif grounding_gate is not None:
        raise Phase5DashboardError("과거 dashboard config에 grounding Gate가 있습니다.")
    generation = model_check.get("generation")
    if generation != {"do_sample": False, "num_beams": 1, "max_new_tokens": 256}:
        raise Phase5DashboardError("대시보드 generation 계약이 다릅니다.")


def _validate_dataset_browser(
    value: Any, governance: dict[str, Any], schema_version: str
) -> None:
    if not isinstance(value, dict):
        raise Phase5DashboardError("Phase 5 dataset browser 계약이 없습니다.")
    splits = value.get("splits")
    labels = value.get("axis_labels")
    restricted = value.get("restricted_axes")
    blind = value.get("sealed_blind")
    expected_splits = {
        "ki10_train",
        "ki20_train",
        "dev_monitor",
        "dev_diagnostic",
        "persona_guard",
        "external_conformance",
    }
    if (
        value.get("preflight_config")
        != "configs/data_versions/saju_1b_baseline/preflight-v2.0.0.json"
        or restricted != ["aihub_empathy_single", "aihub_empathy_multiturn"]
        or not isinstance(labels, dict)
        or any(
            not isinstance(key, str) or not isinstance(label, str)
            for key, label in labels.items()
        )
        or not isinstance(splits, dict)
        or set(splits) != expected_splits
        or not isinstance(blind, dict)
        or blind.get("rows") != 500
        or blind.get("components") != 350
        or blind.get("sample_access_allowed") is not False
        or blind.get("inspected") is not False
        or governance.get("dataset_samples_local_only") is not True
        or governance.get("restricted_samples_visible") is not True
        or governance.get("sealed_blind_access_allowed") is not False
    ):
        raise Phase5DashboardError("Phase 5 dataset browser 계약이 다릅니다.")
    if schema_version in {
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
        "1.9.0",
        "1.10.0",
        "1.12.0",
    }:
        if (
            value.get("selection_seed") != "phase5-dashboard-v1.5.0-dataset-samples"
            or value.get("sample_selection")
            != {
                "mode": "cryptographic_random",
                "samples_per_request": 10,
                "unique_within_request": True,
                "repeat_across_requests_possible": True,
                "persisted": False,
            }
            or "samples_per_axis" in value
            or "all_axis_samples_per_axis" in value
            or governance.get("dataset_random_sampling_read_only") is not True
            or governance.get("dataset_random_samples_persisted") is not False
        ):
            raise Phase5DashboardError(
                "Phase 5 dashboard v1.5+ 무작위 샘플 계약이 다릅니다."
            )
    elif (
        value.get("selection_seed") != "phase5-dashboard-v1.2.0-dataset-samples"
        or value.get("samples_per_axis") != 3
        or value.get("all_axis_samples_per_axis") != 1
        or "sample_selection" in value
    ):
        raise Phase5DashboardError("Phase 5 dashboard 과거 샘플 계약이 다릅니다.")
    for split_id, split in splits.items():
        axes = split.get("axes") if isinstance(split, dict) else None
        if (
            not isinstance(split, dict)
            or split.get("kind") not in {"training", "evaluation", "external"}
            or not isinstance(split.get("label"), str)
            or not isinstance(split.get("role"), str)
            or not isinstance(split.get("rows"), int)
            or not isinstance(axes, dict)
            or any(
                axis not in labels
                or isinstance(rows, bool)
                or not isinstance(rows, int)
                or rows
                < (
                    10
                    if schema_version
                    in {
                        "1.5.0",
                        "1.6.0",
                        "1.7.0",
                        "1.8.0",
                        "1.9.0",
                        "1.10.0",
                        "1.12.0",
                    }
                    else 1
                )
                for axis, rows in axes.items()
            )
            or sum(axes.values()) != split["rows"]
        ):
            raise Phase5DashboardError(f"dataset split 계약이 다릅니다: {split_id}")
        if split["kind"] == "external":
            fixtures = split.get("fixtures")
            if not isinstance(fixtures, dict) or set(fixtures) != set(split["axes"]):
                raise Phase5DashboardError("외부 정합성 fixture 계약이 다릅니다.")
            sources = fixtures.values()
        else:
            sources = [split]
        for source in sources:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("path"), str)
                or not isinstance(source.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", source["sha256"]) is None
            ):
                raise Phase5DashboardError(
                    f"dataset source 계약이 다릅니다: {split_id}"
                )
    blind_axes = blind.get("axes")
    if (
        not isinstance(blind_axes, dict)
        or any(
            axis not in labels
            or isinstance(rows, bool)
            or not isinstance(rows, int)
            or rows < 1
            for axis, rows in blind_axes.items()
        )
        or sum(blind_axes.values()) != blind["rows"]
    ):
        raise Phase5DashboardError("봉인 split 축 계약이 다릅니다.")


def _load_prompt_profiles(repo_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    contract = config.get("prompt_profiles")
    if contract is None:
        return {
            "default_profile": "raw_no_system",
            "bound_profile": None,
            "legacy_profile": "raw_legacy",
            "profiles": {
                "raw_no_system": {
                    "label": "무지시 원출력",
                    "description": "과거 dashboard 호환 profile",
                    "system_prompt_text": None,
                    "system_prompt_sha256": None,
                    "production_like": False,
                    "diagnostic_only": True,
                }
            },
        }
    loaded: dict[str, dict[str, Any]] = {}
    for profile_id, profile in contract["profiles"].items():
        prompt = profile["system_prompt"]
        text: str | None = None
        digest: str | None = None
        if prompt is not None:
            path = _safe_under(repo_root, prompt["path"], "수동 대화 system prompt")
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != prompt["bytes"]
            ):
                raise Phase5DashboardError(
                    "수동 대화 system prompt 파일 계약이 다릅니다."
                )
            digest = _sha256_file(path)
            if digest != prompt["sha256"]:
                raise Phase5DashboardError(
                    "수동 대화 system prompt SHA-256이 다릅니다."
                )
            try:
                text = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError) as exc:
                raise Phase5DashboardError(
                    "수동 대화 system prompt를 읽을 수 없습니다."
                ) from exc
            if not text or CONTROL_PATTERN.search(text):
                raise Phase5DashboardError(
                    "수동 대화 system prompt 내용이 안전하지 않습니다."
                )
        loaded[profile_id] = {
            "label": profile["label"],
            "description": profile["description"],
            "system_prompt_text": text,
            "system_prompt_sha256": digest,
            "production_like": profile["production_like"],
            "diagnostic_only": profile["diagnostic_only"],
        }
    return {
        "default_profile": contract["default_profile"],
        "bound_profile": contract.get("bound_profile"),
        "legacy_profile": contract["legacy_profile"],
        "profiles": loaded,
    }


def _load_inference_engines(
    repo_root: Path, run_root: Path, config: dict[str, Any]
) -> dict[str, Any]:
    contract = config.get("inference_engines")
    if contract is None:
        return {
            "default_selection": "ki20_final",
            "sequential_load_only": True,
            "single_timeout_seconds": 300,
            "paired_timeout_seconds": 600,
            "engines": {
                "ki20_final": {
                    "label": "KI20",
                    "kind": "run_final",
                    "revision": run_root.name,
                    "model_sha256": None,
                    "resolved_path": run_root / "final",
                }
            },
            "selections": {
                "ki20_final": {
                    "label": "KI20 단독",
                    "mode": "single",
                    "engine_ids": ["ki20_final"],
                }
            },
        }
    engines: dict[str, dict[str, Any]] = {}
    for engine_id, configured in contract["engines"].items():
        if configured["kind"] == "run_final":
            resolved_path = run_root / "final"
        else:
            resolved_path = _safe_under(
                repo_root, configured["path"], f"{engine_id} model snapshot"
            )
        engines[engine_id] = {**configured, "resolved_path": resolved_path}
    return {
        "default_selection": contract["default_selection"],
        "sequential_load_only": contract["sequential_load_only"],
        "single_timeout_seconds": contract["single_timeout_seconds"],
        "paired_timeout_seconds": contract["paired_timeout_seconds"],
        "engines": engines,
        "selections": contract["selections"],
    }


def _engine_availability(context: dict[str, Any], engine_id: str) -> dict[str, Any]:
    engine = context["inference_engines"]["engines"].get(engine_id)
    if not isinstance(engine, dict):
        raise Phase5DashboardError("허용되지 않은 inference engine입니다.")
    path = engine["resolved_path"]
    reasons: list[str] = []
    if path.is_symlink() or not path.is_dir():
        reasons.append("model_path_unavailable")
    else:
        present = {child.name for child in path.iterdir() if child.is_file()}
        if set(engine.get("required_file_sha256", REQUIRED_FINAL_FILES)) - present:
            reasons.append("required_model_files_missing")
    if (
        engine["kind"] == "run_final"
        and not (context["run_root"] / "reload_summary.json").is_file()
    ):
        reasons.append("final_reload_not_available")
    return {"available": not reasons, "reasons": reasons}


def _engine_snapshot(context: dict[str, Any], engine_id: str) -> dict[str, Any]:
    engine = context["inference_engines"]["engines"][engine_id]
    return {
        "engine_id": engine_id,
        "label": engine["label"],
        "kind": engine["kind"],
        "revision": engine["revision"],
        "model_sha256": engine["model_sha256"],
    }


def inference_engines_payload(context: dict[str, Any]) -> dict[str, Any]:
    contract = context["inference_engines"]
    return {
        "default_selection": contract["default_selection"],
        "diagnostic_only": True,
        "calculator_connected": bool(
            context.get("runtime_canary_active", False)
            or context.get("period_runtime_active", False)
        ),
        "items": [
            {
                **_engine_snapshot(context, engine_id),
                **_engine_availability(context, engine_id),
            }
            for engine_id in contract["engines"]
        ],
        "selections": [
            {"selection_id": selection_id, **selection}
            for selection_id, selection in contract["selections"].items()
        ],
    }


def _prepare_runtime_canary(
    root: Path, config: dict[str, Any]
) -> dict[str, Any] | None:
    contract = config.get("runtime_canary")
    if not isinstance(contract, dict):
        return None
    from scripts.runtime.calculation.contracts_v1_1 import (
        validate_contract_registry_v1_1,
        validate_release_registry,
    )
    from scripts.runtime.calculation.errors import RuntimeCalculationError

    try:
        validate_contract_registry_v1_1()
    except RuntimeCalculationError as exc:
        raise Phase5DashboardError(
            f"runtime v1.1 정적 계약 검증이 실패했습니다: {exc.code}"
        ) from exc
    release_path = _safe_under(
        root, contract["release_registry"], "runtime release registry"
    )
    if not release_path.exists():
        return {
            "contract": contract,
            "release_path": release_path,
            "release": None,
            "availability_code": "RUNTIME_RELEASE_REQUIRED",
        }
    try:
        release = validate_release_registry(release_path)
    except RuntimeCalculationError as exc:
        raise Phase5DashboardError(
            f"runtime release registry 검증이 실패했습니다: {exc.code}"
        ) from exc
    return {
        "contract": contract,
        "release_path": release_path,
        "release": release,
        "availability_code": None,
    }


def _prepare_period_runtime(
    root: Path, config: dict[str, Any]
) -> dict[str, Any] | None:
    contract = config.get("period_runtime")
    if not isinstance(contract, dict):
        return None
    from scripts.runtime.calculation.contracts_v1_5 import (
        validate_release_registry_v1_5,
    )
    from scripts.runtime.calculation.errors import RuntimeCalculationError
    from scripts.runtime.period_v1.contracts_v1_1 import validate_release_registry
    from scripts.runtime.period_v1.errors import PeriodRuntimeError

    parent_release_path = _safe_under(
        root, contract["parent_release_registry"], "parent runtime release registry"
    )
    period_release_path = _safe_under(
        root, contract["period_release_registry"], "period runtime release registry"
    )
    asset_root = _safe_under(root, contract["asset_root"], "dashboard period assets")
    if not asset_root.is_dir() or asset_root.is_symlink():
        raise Phase5DashboardError("dashboard period asset root가 안전하지 않습니다.")
    if not parent_release_path.exists() or not period_release_path.exists():
        return {
            "contract": contract,
            "parent_release_path": parent_release_path,
            "parent_release": None,
            "period_release_path": period_release_path,
            "period_release": None,
            "asset_root": asset_root,
            "availability_code": "RUNTIME_RELEASE_REQUIRED",
        }
    try:
        parent_release = validate_release_registry_v1_5(parent_release_path)
        period_release = validate_release_registry(period_release_path)
    except (RuntimeCalculationError, PeriodRuntimeError) as exc:
        raise Phase5DashboardError(
            f"제한 runtime release registry 검증이 실패했습니다: {exc.code}"
        ) from exc
    if (
        parent_release.get("release_id") != contract["parent_release_id"]
        or period_release.get("release_id") != contract["period_release_id"]
    ):
        raise Phase5DashboardError("기간 release ID가 dashboard 계약과 다릅니다.")
    return {
        "contract": contract,
        "parent_release_path": parent_release_path,
        "parent_release": parent_release,
        "period_release_path": period_release_path,
        "period_release": period_release,
        "asset_root": asset_root,
        "availability_code": None,
    }


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
        training.get("expected_optimizer_steps") != expected["expected_optimizer_steps"]
        or training.get("preserved_milestone_steps")
        != expected["preserved_milestone_steps"]
        or limits.get("max_total_gpu_memory_used_mib") != expected["gpu_hard_cap_mib"]
    ):
        raise Phase5DashboardError("KI20 학습 계약과 dashboard가 다릅니다.")
    prompt_profiles = _load_prompt_profiles(root, config)
    inference_engines = _load_inference_engines(root, run_target, config)
    runtime_canary = _prepare_runtime_canary(root, config)
    period_runtime = _prepare_period_runtime(root, config)
    return {
        "repo_root": root,
        "config_path": config_target,
        "config": config,
        "run_root": run_target,
        "manifest": manifest,
        "resolved": resolved,
        "prompt_profiles": prompt_profiles,
        "inference_engines": inference_engines,
        "runtime_canary": runtime_canary,
        "runtime_canary_active": False,
        "period_runtime": period_runtime,
        "period_runtime_active": False,
    }


def _dataset_contract(context: dict[str, Any]) -> dict[str, Any]:
    contract = context["config"].get("dataset_browser")
    if not isinstance(contract, dict):
        raise Phase5DashboardError(
            "이 dashboard config는 dataset browser를 지원하지 않습니다."
        )
    return contract


def _dataset_path(context: dict[str, Any], relative: str, label: str) -> Path:
    return _safe_under(context["repo_root"], relative, label)


def _read_dataset_jsonl(
    context: dict[str, Any], source: dict[str, Any], label: str
) -> list[dict[str, Any]]:
    path = _dataset_path(context, source["path"], label)
    if path.is_symlink() or not path.is_file():
        raise Phase5DashboardError(f"{label} 파일이 없거나 symlink입니다.")
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise Phase5DashboardError(f"{label} 크기가 허용 범위를 넘습니다.")
    if _sha256_file(path) != source["sha256"]:
        raise Phase5DashboardError(f"{label} SHA-256이 다릅니다.")
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase5DashboardError(f"{label} {line_number}행이 비었습니다.")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase5DashboardError(
                        f"{label} {line_number}행이 JSON object가 아닙니다."
                    )
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5DashboardError(f"{label} JSONL을 읽을 수 없습니다.") from exc
    expected_rows = source.get("rows")
    if isinstance(expected_rows, int) and len(values) != expected_rows:
        raise Phase5DashboardError(f"{label} 행 수가 계약과 다릅니다.")
    return values


def _safe_sample_messages(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise Phase5DashboardError(f"{label} messages가 없습니다.")
    messages: list[dict[str, str]] = []
    for message in value:
        if (
            not isinstance(message, dict)
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            or not message["content"]
        ):
            raise Phase5DashboardError(f"{label} message 계약이 다릅니다.")
        messages.append({"role": message["role"], "content": message["content"]})
    return messages


def _normalized_axis(value: Any) -> str:
    return value if isinstance(value, str) and value else "general_instruction"


def _verify_axis_counts(
    rows: Sequence[dict[str, Any]], expected: dict[str, int], field: str, label: str
) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[_normalized_axis(row.get(field))] += 1
    if dict(counts) != expected:
        raise Phase5DashboardError(f"{label} 축별 수량이 계약과 다릅니다.")


def _training_record_index(
    context: dict[str, Any], cache: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    cached = cache.get("training_record_index")
    if isinstance(cached, dict):
        return cached
    try:
        from scripts.preflight.phase4_common import load_candidate_staging_records
        from scripts.preflight.phase4_common import (
            prepare_context as prepare_phase4_context,
        )

        preflight_path = _dataset_path(
            context,
            _dataset_contract(context)["preflight_config"],
            "dataset preflight config",
        )
        phase4_context = prepare_phase4_context(
            context["repo_root"], preflight_path, verify_parents=False
        )
        records, _, _, _ = load_candidate_staging_records(
            phase4_context, context["repo_root"]
        )
    except Exception as exc:
        raise Phase5DashboardError("학습 샘플 원본 검증·해석이 실패했습니다.") from exc
    cache["training_record_index"] = records
    return records


def _training_candidates(
    context: dict[str, Any], split: dict[str, Any], cache: dict[str, Any]
) -> list[dict[str, Any]]:
    manifest = _read_dataset_jsonl(context, split, split["label"])
    _verify_axis_counts(manifest, split["axes"], "mix_axis", split["label"])
    records = _training_record_index(context, cache)
    restricted_axes = set(_dataset_contract(context)["restricted_axes"])
    candidates: list[dict[str, Any]] = []
    for row in manifest:
        record_id = row.get("id")
        axis = row.get("mix_axis")
        record = records.get(record_id) if isinstance(record_id, str) else None
        if (
            record is None
            or axis not in split["axes"]
            or record.get("mix_axis") != axis
            or record.get("meta", {}).get("phase4_parent_record_sha256")
            != row.get("record_sha256")
        ):
            raise Phase5DashboardError("학습 샘플 manifest 연결이 다릅니다.")
        candidates.append(
            {
                "identity": record_id,
                "axis": axis,
                "task": record.get("task"),
                "format": "messages",
                "messages": _safe_sample_messages(record.get("messages"), "학습 샘플"),
                "restricted_local_only": axis in restricted_axes,
            }
        )
    return candidates


def _evaluation_candidates(
    context: dict[str, Any], split: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = _read_dataset_jsonl(context, split, split["label"])
    _verify_axis_counts(rows, split["axes"], "source_axis", split["label"])
    restricted_axes = set(_dataset_contract(context)["restricted_axes"])
    candidates: list[dict[str, Any]] = []
    for row in rows:
        axis = _normalized_axis(row.get("source_axis"))
        cases = row.get("cases")
        if not isinstance(cases, list) or not cases:
            raise Phase5DashboardError("개발 진단 case 계약이 다릅니다.")
        for case_index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise Phase5DashboardError("개발 진단 case가 object가 아닙니다.")
            messages = _safe_sample_messages(case.get("prompt_messages"), "개발 진단")
            reference = case.get("reference_assistant")
            if isinstance(reference, str) and reference:
                messages.append({"role": "assistant", "content": reference})
            identity = f"{row.get('eval_id')}|{case.get('case_id')}|{case_index}"
            candidates.append(
                {
                    "identity": identity,
                    "axis": axis,
                    "task": row.get("category"),
                    "format": "messages",
                    "messages": messages,
                    "reference_available": isinstance(reference, str)
                    and bool(reference),
                    "restricted_local_only": axis in restricted_axes,
                }
            )
    return candidates


def _external_candidates(
    context: dict[str, Any], split: dict[str, Any], axis: str
) -> list[dict[str, Any]]:
    fixture = split["fixtures"][axis]
    if axis == "policy_cases":
        rows = _read_dataset_jsonl(
            context, {**fixture, "rows": split["axes"][axis]}, "정책 경계 fixture"
        )
        return [
            {
                "identity": f"policy|{index}",
                "axis": axis,
                "task": row.get("case_type"),
                "format": "structured",
                "input": row.get("input"),
                "expected": row.get("expected"),
                "restricted_local_only": False,
            }
            for index, row in enumerate(rows)
        ]
    path = _dataset_path(context, fixture["path"], "KASI fixture")
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > MAX_DATASET_BYTES
    ):
        raise Phase5DashboardError("KASI fixture가 없거나 안전하지 않습니다.")
    if _sha256_file(path) != fixture["sha256"]:
        raise Phase5DashboardError("KASI fixture SHA-256이 다릅니다.")
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5DashboardError("KASI fixture를 읽을 수 없습니다.") from exc
    if (
        not isinstance(rows, list)
        or len(rows) != split["axes"][axis]
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise Phase5DashboardError("KASI fixture 행 수가 계약과 다릅니다.")
    return [
        {
            "identity": f"kasi|{index}",
            "axis": axis,
            "task": "solar_lunar_conversion",
            "format": "structured",
            "input": {"solar": row.get("solar")},
            "expected": {key: row.get(key) for key in ("lunar", "leap", "ko", "cn")},
            "restricted_local_only": False,
        }
        for index, row in enumerate(rows)
    ]


def _dataset_candidates(
    context: dict[str, Any], split_id: str, axis: str, cache: dict[str, Any]
) -> list[dict[str, Any]]:
    split = _dataset_contract(context)["splits"][split_id]
    if split["kind"] == "training":
        return _training_candidates(context, split, cache)
    if split["kind"] == "evaluation":
        return _evaluation_candidates(context, split)
    axes = split["axes"] if axis == "all" else {axis: split["axes"][axis]}
    return [
        candidate
        for axis_id in axes
        for candidate in _external_candidates(context, split, axis_id)
    ]


def _sample_projection(
    context: dict[str, Any], split_id: str, candidate: dict[str, Any]
) -> dict[str, Any]:
    contract = _dataset_contract(context)
    digest = hashlib.sha256(
        f"{contract['selection_seed']}|{split_id}|{candidate['identity']}".encode()
    ).hexdigest()
    projected = {
        key: value
        for key, value in candidate.items()
        if key
        in {
            "axis",
            "task",
            "format",
            "messages",
            "input",
            "expected",
            "reference_available",
            "restricted_local_only",
        }
    }
    projected["sample_key"] = digest[:12]
    projected["axis_label"] = contract["axis_labels"][candidate["axis"]]
    return projected


def dataset_samples_payload(
    context: dict[str, Any],
    split_id: str,
    axis: str,
    cache: dict[str, Any] | None = None,
    *,
    randomize: bool = False,
    random_source: Any | None = None,
) -> dict[str, Any]:
    contract = _dataset_contract(context)
    schema_version = context["config"]["schema_version"]
    split = contract["splits"].get(split_id)
    if not isinstance(split, dict):
        raise DashboardRequestError(
            HTTPStatus.NOT_FOUND, "허용되지 않은 dataset split입니다."
        )
    if axis != "all" and axis not in split["axes"]:
        raise DashboardRequestError(
            HTTPStatus.NOT_FOUND, "허용되지 않은 dataset 축입니다."
        )
    if randomize and schema_version not in {
        "1.5.0",
        "1.6.0",
        "1.7.0",
        "1.8.0",
    }:
        raise DashboardRequestError(
            HTTPStatus.NOT_FOUND,
            "이 dashboard config는 무작위 샘플을 지원하지 않습니다.",
        )
    active_cache = cache if cache is not None else {}
    cache_key = f"sample_payload:{split_id}:{axis}"
    if not randomize:
        cached = active_cache.get(cache_key)
        if isinstance(cached, dict):
            return cached
    candidates = _dataset_candidates(context, split_id, axis, active_cache)
    selected: list[dict[str, Any]] = []
    if schema_version in {"1.5.0", "1.6.0", "1.7.0", "1.8.0"}:
        matching = (
            candidates
            if axis == "all"
            else [candidate for candidate in candidates if candidate["axis"] == axis]
        )
        requested = contract["sample_selection"]["samples_per_request"]
        identities = [candidate.get("identity") for candidate in matching]
        if (
            len(matching) < requested
            or any(
                not isinstance(identity, str) or not identity for identity in identities
            )
            or len(set(identities)) != len(identities)
        ):
            raise Phase5DashboardError(
                f"dataset 무작위 sample 후보가 10건 미만이거나 중복입니다: {split_id}/{axis}"
            )
        if randomize:
            generator = (
                random_source if random_source is not None else secrets.SystemRandom()
            )
            selected = generator.sample(matching, requested)
            selection_mode = "cryptographic_random"
        else:
            matching.sort(
                key=lambda candidate: hashlib.sha256(
                    f"{contract['selection_seed']}|{split_id}|{axis}|{candidate['identity']}".encode()
                ).hexdigest()
            )
            selected = matching[:requested]
            selection_mode = "deterministic_compatibility"
        selected_identities = [candidate["identity"] for candidate in selected]
        if len(selected) != requested or len(set(selected_identities)) != requested:
            raise Phase5DashboardError(
                "dataset 무작위 sample 결과가 10개 고유 행이 아닙니다."
            )
    else:
        target_axes = list(split["axes"]) if axis == "all" else [axis]
        per_axis = (
            contract["all_axis_samples_per_axis"]
            if axis == "all"
            else contract["samples_per_axis"]
        )
        for axis_id in target_axes:
            matching = [
                candidate for candidate in candidates if candidate["axis"] == axis_id
            ]
            matching.sort(
                key=lambda candidate: hashlib.sha256(
                    f"{contract['selection_seed']}|{split_id}|{axis_id}|{candidate['identity']}".encode()
                ).hexdigest()
            )
            if not matching:
                raise Phase5DashboardError(
                    f"dataset sample 후보가 없습니다: {split_id}/{axis_id}"
                )
            selected.extend(matching[:per_axis])
        requested = len(selected)
        selection_mode = "deterministic_legacy"
    items = [_sample_projection(context, split_id, candidate) for candidate in selected]
    result = {
        "split_id": split_id,
        "split_label": split["label"],
        "axis": axis,
        "items": items,
        "selection": {
            "mode": selection_mode,
            "requested": requested,
            "returned": len(items),
            "unique_within_request": True,
            "repeat_across_requests_possible": randomize,
            "persisted": False,
        },
        "local_only": True,
        "restricted_content_included": any(
            item["restricted_local_only"] for item in items
        ),
        "sealed_blind_accessed": False,
        "internal_identifiers_included": False,
    }
    if not randomize:
        active_cache[cache_key] = result
    return result


def dataset_splits_payload(context: dict[str, Any]) -> dict[str, Any]:
    contract = _dataset_contract(context)
    restricted = set(contract["restricted_axes"])

    def split_projection(split_id: str, split: dict[str, Any]) -> dict[str, Any]:
        return {
            "split_id": split_id,
            "label": split["label"],
            "role": split["role"],
            "kind": split["kind"],
            "rows": split["rows"],
            "sample_access_allowed": True,
            "axes": [
                {
                    "axis": axis,
                    "label": contract["axis_labels"][axis],
                    "rows": rows,
                    "percent": round(rows * 100 / split["rows"], 2),
                    "restricted_local_only": axis in restricted,
                }
                for axis, rows in split["axes"].items()
            ],
        }

    blind = contract["sealed_blind"]
    return {
        "splits": [
            split_projection(split_id, split)
            for split_id, split in contract["splits"].items()
        ],
        "sealed_blind": {
            "label": blind["label"],
            "role": blind["role"],
            "rows": blind["rows"],
            "components": blind["components"],
            "sample_access_allowed": False,
            "inspected": False,
            "axes": [
                {
                    "axis": axis,
                    "label": contract["axis_labels"][axis],
                    "rows": rows,
                }
                for axis, rows in blind["axes"].items()
            ],
        },
        "local_only": True,
        "training_data_modified": False,
        "blind_source_test_inspected": False,
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
    try:
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
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "error": "nvidia-smi unavailable"}
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
        key=lambda path: (
            int(CHECKPOINT_PATTERN.fullmatch(path.name).group(1))
            if CHECKPOINT_PATTERN.fullmatch(path.name)
            else sys.maxsize
        ),
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
                "preserved_milestone": step in milestones
                if step is not None
                else False,
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
        eta = (
            current + timedelta(seconds=(expected - step) * seconds_per_step)
        ).isoformat()
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
        alerts.append(
            {
                "level": "critical",
                "code": "nonfinite_metric",
                "message": "NaN/Inf metric이 있습니다.",
            }
        )
    if service["active"] and metric_age > stale_after:
        alerts.append(
            {
                "level": "warning",
                "code": "stale_metrics",
                "message": "학습 서비스가 active지만 metric 갱신이 지연됐습니다.",
            }
        )
    if checkpoints["incomplete_count"]:
        alerts.append(
            {
                "level": "warning",
                "code": "incomplete_checkpoint",
                "message": "안정화 후에도 불완전하거나 안전하지 않은 checkpoint가 있습니다.",
            }
        )
    configured_cap = context["config"]["training_contract"]["gpu_hard_cap_mib"]
    cap = min(configured_cap, gpu.get("total_mib", configured_cap))
    if gpu.get("available") and gpu["used_mib"] >= cap:
        alerts.append(
            {
                "level": "critical",
                "code": "gpu_cap",
                "message": "GPU 전체 사용량이 16 GiB 상한 이상입니다.",
            }
        )
    elif gpu.get("available") and gpu["used_mib"] >= int(cap * 0.95):
        alerts.append(
            {
                "level": "warning",
                "code": "gpu_near_cap",
                "message": "GPU 전체 사용량이 상한의 95% 이상입니다.",
            }
        )
    if lifecycle == "stopped_unexpectedly":
        alerts.append(
            {
                "level": "critical",
                "code": "unexpected_stop",
                "message": "완료 상태가 아닌데 학습 서비스가 중지됐습니다.",
            }
        )
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
            "elapsed_seconds": round(elapsed_seconds, 3)
            if elapsed_seconds is not None
            else None,
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
    output_root = (
        context["run_root"]
        / context["config"]["model_check"]["private_output_relative"]
    )
    summary_path = output_root / "summary.json"
    rows_path = output_root / "model_checks.jsonl"
    if not summary_path.exists() and not rows_path.exists():
        return {
            "status": "not_run",
            "generation_gate": gate,
            "rows": [],
            "summary": None,
        }
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
    return {
        "status": "available",
        "generation_gate": gate,
        "rows": visible,
        "summary": summary,
    }


def _select_probes(context: dict[str, Any]) -> list[dict[str, Any]]:
    config = context["config"]["model_check"]
    source = config["probe_source"]
    path = _safe_under(context["repo_root"], source["path"], "KI10 probe source")
    if (
        path.is_symlink()
        or not path.is_file()
        or _sha256_file(path) != source["sha256"]
    ):
        raise Phase5DashboardError("KI10 probe source hash가 다릅니다.")
    values: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Phase5DashboardError(
                f"KI10 probe source {index}행이 손상됐습니다."
            ) from exc
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
    selected.sort(
        key=lambda row: (str(row["category"]), str(row["eval_id"]), str(row["case_id"]))
    )
    if (
        len(selected) != 20
        or len({(row["eval_id"], row["case_id"]) for row in selected}) != 20
    ):
        raise Phase5DashboardError("고정 probe 20건 선택이 결정적이지 않습니다.")
    return selected


def _load_model(
    final_root: Path,
    expected_model_sha256: str | None = None,
    required_file_sha256: dict[str, str] | None = None,
) -> tuple[Any, Any, Any]:
    if final_root.is_symlink() or not final_root.is_dir():
        raise Phase5DashboardError("final 모델 경로가 없거나 symlink입니다.")
    missing = REQUIRED_FINAL_FILES - {
        path.name for path in final_root.iterdir() if path.is_file()
    }
    if missing:
        raise Phase5DashboardError(f"final 모델 파일이 부족합니다: {sorted(missing)}")
    expected_files = dict(required_file_sha256 or {})
    if expected_model_sha256 is not None:
        if (
            expected_files.get("model.safetensors", expected_model_sha256)
            != expected_model_sha256
        ):
            raise Phase5DashboardError("inference engine model hash 계약이 다릅니다.")
        expected_files["model.safetensors"] = expected_model_sha256
    for relative, expected_sha256 in expected_files.items():
        path = final_root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != expected_sha256
        ):
            raise Phase5DashboardError(
                f"inference engine {relative} SHA-256이 다릅니다."
            )
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5DashboardError("모델 검사 runtime import가 실패했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        final_root,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        final_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    )
    torch.cuda.init()
    torch.cuda.reset_peak_memory_stats()
    model = model.to("cuda:0")
    model.eval()
    return torch, tokenizer, model


def _generate_many(
    final_root: Path,
    prompts: Sequence[list[dict[str, str]]],
    generation: dict[str, Any],
) -> list[str]:
    torch, tokenizer, model = _load_model(final_root)
    try:
        outputs: list[str] = []
        with torch.inference_mode():
            for messages in prompts:
                outputs.append(
                    _generate_loaded(
                        torch,
                        tokenizer,
                        model,
                        messages,
                        generation,
                        max_input_tokens=None,
                    )["output"]
                )
        return outputs
    finally:
        del model
        del tokenizer
        gc.collect()
        torch.cuda.empty_cache()


def _generate_loaded(
    torch: Any,
    tokenizer: Any,
    model: Any,
    messages: Sequence[dict[str, str]],
    generation: dict[str, Any],
    *,
    max_input_tokens: int | None,
) -> dict[str, Any]:
    prepared = [dict(message) for message in messages]
    omitted_messages = 0
    while True:
        input_ids = tokenizer.apply_chat_template(
            prepared,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        )
        input_tokens = int(input_ids.shape[-1])
        if max_input_tokens is None or input_tokens <= max_input_tokens:
            break
        minimum_messages = 4 if prepared[0].get("role") == "system" else 3
        if len(prepared) < minimum_messages:
            raise Phase5DashboardError(
                "현재 수동 질문 하나가 세션 context token 상한을 넘습니다. 질문을 줄여 주세요."
            )
        if prepared[0].get("role") == "system":
            if (
                prepared[1].get("role") != "user"
                or prepared[2].get("role") != "assistant"
            ):
                raise Phase5DashboardError(
                    "수동 대화 system profile 뒤의 turn 계약이 다릅니다."
                )
            prepared = [prepared[0], *prepared[3:]]
            omitted_messages += 2
        else:
            drop_count = (
                2
                if prepared[0].get("role") == "user"
                and prepared[1].get("role") == "assistant"
                else 1
            )
            prepared = prepared[drop_count:]
            omitted_messages += drop_count
    input_ids = input_ids.to("cuda:0")
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
    return {
        "output": text,
        "input_tokens": input_tokens,
        "omitted_messages": omitted_messages,
    }


def _generate_conversation(
    final_root: Path,
    messages: Sequence[dict[str, str]],
    generation: dict[str, Any],
    max_input_tokens: int,
    expected_model_sha256: str | None = None,
    required_file_sha256: dict[str, str] | None = None,
    gpu_hard_cap_mib: int = 16384,
) -> dict[str, Any]:
    torch, tokenizer, model = _load_model(
        final_root, expected_model_sha256, required_file_sha256
    )
    try:
        with torch.inference_mode():
            result = _generate_loaded(
                torch,
                tokenizer,
                model,
                messages,
                generation,
                max_input_tokens=max_input_tokens,
            )
        peak_allocated = int(torch.cuda.max_memory_allocated(0))
        gpu = _gpu_snapshot()
        if peak_allocated > gpu_hard_cap_mib * 1024 * 1024 or (
            gpu.get("available")
            and gpu.get("used_mib", gpu_hard_cap_mib + 1) > gpu_hard_cap_mib
        ):
            raise Phase5DashboardError(
                "inference engine이 16GiB GPU 상한을 넘었습니다."
            )
        return {
            **result,
            "peak_allocated_bytes": peak_allocated,
            "gpu_total_memory_used_mib": gpu.get("used_mib"),
        }
    finally:
        del model
        del tokenizer
        gc.collect()
        torch.cuda.empty_cache()


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
        json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
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
    target = (
        context["run_root"]
        / context["config"]["model_check"]["private_output_relative"]
    )
    _atomic_private_directory(
        target,
        {"model_checks.jsonl": rows_payload, "summary.json": _json_bytes(summary)},
    )
    return summary


def _manual_session_contract(context: dict[str, Any]) -> dict[str, Any]:
    contract = context["config"].get("manual_session")
    if not isinstance(contract, dict):
        raise Phase5DashboardError(
            "이 dashboard config는 대화 세션 저장을 지원하지 않습니다."
        )
    return contract


def _inference_selection(context: dict[str, Any], selection_id: str) -> dict[str, Any]:
    selection = context["inference_engines"]["selections"].get(selection_id)
    if not isinstance(selection, dict):
        raise Phase5DashboardError("허용되지 않은 inference engine 선택입니다.")
    return {"selection_id": selection_id, **selection}


def _session_inference_selection(
    context: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    if session.get("schema_version") in {"1.0.0", "1.1.0"}:
        return _inference_selection(context, "ki20_final")
    return _inference_selection(context, str(session.get("engine_selection", "")))


def _selection_snapshots(
    context: dict[str, Any], selection: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return {
        engine_id: _engine_snapshot(context, engine_id)
        for engine_id in selection["engine_ids"]
    }


def _prompt_profile(
    context: dict[str, Any], profile_id: str, *, allow_legacy: bool = False
) -> dict[str, Any]:
    profiles = context["prompt_profiles"]
    if allow_legacy and profile_id == profiles["legacy_profile"]:
        return {
            "profile_id": profile_id,
            "label": "기존 무지시",
            "description": "v1.0 세션에서 보존한 무지시 원출력",
            "system_prompt_text": None,
            "system_prompt_sha256": None,
            "production_like": False,
            "diagnostic_only": True,
        }
    profile = profiles["profiles"].get(profile_id)
    if not isinstance(profile, dict):
        raise Phase5DashboardError("수동 대화 prompt profile이 허용되지 않습니다.")
    return {"profile_id": profile_id, **profile}


def prompt_profiles_payload(context: dict[str, Any]) -> dict[str, Any]:
    profiles = context["prompt_profiles"]
    return {
        "default_profile": profiles["default_profile"],
        "bound_profile": profiles.get("bound_profile"),
        "legacy_profile": profiles["legacy_profile"],
        "items": [
            {
                "profile_id": profile_id,
                "label": profile["label"],
                "description": profile["description"],
                "system_prompt_sha256": profile["system_prompt_sha256"],
                "production_like": profile["production_like"],
                "diagnostic_only": profile["diagnostic_only"],
            }
            for profile_id, profile in profiles["profiles"].items()
        ],
    }


def _session_prompt_profile(
    context: dict[str, Any], session: dict[str, Any]
) -> dict[str, Any]:
    if session.get("schema_version") == "1.0.0":
        return _prompt_profile(
            context, context["prompt_profiles"]["legacy_profile"], allow_legacy=True
        )
    return _prompt_profile(context, str(session.get("prompt_profile", "")))


def _manual_session_root(context: dict[str, Any], *, create: bool) -> Path:
    contract = _manual_session_contract(context)
    root = _safe_under(
        context["run_root"], contract["private_output_relative"], "수동 대화 세션"
    )
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        if root.is_symlink() or not root.is_dir():
            raise Phase5DashboardError(
                "수동 대화 세션 경로가 안전한 directory가 아닙니다."
            )
        root.chmod(PRIVATE_DIR_MODE)
    elif root.exists() and (root.is_symlink() or not root.is_dir()):
        raise Phase5DashboardError("수동 대화 세션 경로가 안전한 directory가 아닙니다.")
    return root


def _manual_session_path(context: dict[str, Any], session_id: str) -> Path:
    if SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise Phase5DashboardError("수동 대화 session_id가 잘못됐습니다.")
    return _manual_session_root(context, create=False) / f"{session_id}.json"


def _validate_manual_session(
    context: dict[str, Any], value: dict[str, Any], session_id: str
) -> dict[str, Any]:
    messages = value.get("messages")
    schema_version = value.get("schema_version")
    if (
        schema_version not in {"1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"}
        or value.get("session_id") != session_id
        or value.get("run_id") != context["manifest"]["run_id"]
        or value.get("run_build_id") != context["manifest"]["run_build_id"]
        or value.get("run_sha256") != context["manifest"]["run_sha256"]
        or not isinstance(value.get("title"), str)
        or not value["title"]
        or not isinstance(value.get("created_at_utc"), str)
        or not isinstance(value.get("updated_at_utc"), str)
        or not isinstance(messages, list)
        or not messages
        or isinstance(value.get("turn_count"), bool)
        or not isinstance(value.get("turn_count"), int)
        or value["turn_count"] < 1
    ):
        raise Phase5DashboardError("수동 대화 세션 계약이 다릅니다.")
    profile = _session_prompt_profile(context, value)
    if schema_version in {"1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0"} and (
        value.get("prompt_profile") != profile["profile_id"]
        or value.get("system_prompt_sha256") != profile["system_prompt_sha256"]
    ):
        raise Phase5DashboardError("수동 대화 세션 prompt profile 계약이 다릅니다.")
    if schema_version == "1.3.0":
        runtime_session_id = value.get("runtime_session_id")
        runtime_snapshot_sha256 = value.get("runtime_snapshot_sha256")
        if (runtime_session_id is None) != (runtime_snapshot_sha256 is None) or (
            runtime_session_id is not None
            and (
                not isinstance(runtime_session_id, str)
                or RUNTIME_SESSION_ID_PATTERN.fullmatch(runtime_session_id) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(runtime_snapshot_sha256 or ""))
                is None
            )
        ):
            raise Phase5DashboardError("수동 대화 runtime binding이 다릅니다.")
        if (
            runtime_session_id is not None
            and context["config"]["schema_version"] == "1.8.0"
        ):
            state = _read_runtime_state(context, runtime_session_id)
            history_hashes = {
                item["snapshot_sha256"] for item in state["snapshot_history"]
            }
            if runtime_snapshot_sha256 not in history_hashes:
                raise Phase5DashboardError(
                    "수동 대화 runtime snapshot이 state history에 없습니다."
                )
    if schema_version in {"1.4.0", "1.5.0"}:
        runtime_binding_sha256 = value.get("runtime_binding_sha256")
        runtime_snapshot_sha256 = value.get("runtime_snapshot_sha256")
        if (
            not isinstance(runtime_binding_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", runtime_binding_sha256) is None
            or not isinstance(runtime_snapshot_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", runtime_snapshot_sha256) is None
            or "runtime_session_id" in value
        ):
            raise Phase5DashboardError(
                "수동 대화 runtime binding fingerprint가 다릅니다."
            )
    if schema_version == "1.5.0":
        selection = _session_inference_selection(context, value)
        grounding = value.get("grounding_gate")
        retries = (
            grounding.get("retries_by_engine") if isinstance(grounding, dict) else None
        )
        passed = (
            grounding.get("passed_by_engine") if isinstance(grounding, dict) else None
        )
        engine_ids = set(selection["engine_ids"])
        if (
            not isinstance(grounding, dict)
            or set(grounding)
            != {"gate_id", "intent", "retries_by_engine", "passed_by_engine"}
            or grounding.get("gate_id") != GROUNDING_GATE_ID
            or grounding.get("intent")
            not in {"chart_interpretation", "period_request", "general_followup"}
            or not isinstance(retries, dict)
            or set(retries) != engine_ids
            or any(
                isinstance(count, bool)
                or not isinstance(count, int)
                or count not in {0, 1}
                for count in retries.values()
            )
            or not isinstance(passed, dict)
            or set(passed) != engine_ids
            or any(result is not True for result in passed.values())
        ):
            raise Phase5DashboardError("수동 대화 grounding Gate 기록이 다릅니다.")
    if schema_version in {"1.0.0", "1.1.0"}:
        if len(messages) % 2 != 0 or value["turn_count"] != len(messages) // 2:
            raise Phase5DashboardError("기존 수동 대화 turn 계약이 다릅니다.")
        for index, message in enumerate(messages):
            expected_role = "user" if index % 2 == 0 else "assistant"
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content", "created_at_utc"}
                or message.get("role") != expected_role
                or not isinstance(message.get("content"), str)
                or not message["content"]
                or not isinstance(message.get("created_at_utc"), str)
            ):
                raise Phase5DashboardError("수동 대화 메시지 계약이 다릅니다.")
    else:
        selection = _session_inference_selection(context, value)
        snapshots = _selection_snapshots(context, selection)
        if value.get("engine_snapshots") != snapshots:
            raise Phase5DashboardError(
                "수동 대화 inference engine snapshot이 다릅니다."
            )
        chunk_size = 1 + len(selection["engine_ids"])
        if len(messages) != value["turn_count"] * chunk_size:
            raise Phase5DashboardError("수동 비교 대화 turn 계약이 다릅니다.")
        for offset in range(0, len(messages), chunk_size):
            user_message = messages[offset]
            if (
                not isinstance(user_message, dict)
                or set(user_message) != {"role", "content", "created_at_utc"}
                or user_message.get("role") != "user"
                or not isinstance(user_message.get("content"), str)
                or not user_message["content"]
                or not isinstance(user_message.get("created_at_utc"), str)
            ):
                raise Phase5DashboardError("수동 비교 대화 user 메시지가 다릅니다.")
            for index, engine_id in enumerate(selection["engine_ids"], 1):
                assistant = messages[offset + index]
                diagnostics = (
                    assistant.get("diagnostics")
                    if isinstance(assistant, dict)
                    else None
                )
                if (
                    not isinstance(assistant, dict)
                    or set(assistant)
                    != {"role", "engine_id", "content", "created_at_utc", "diagnostics"}
                    or assistant.get("role") != "assistant"
                    or assistant.get("engine_id") != engine_id
                    or not isinstance(assistant.get("content"), str)
                    or not assistant["content"]
                    or not isinstance(assistant.get("created_at_utc"), str)
                ):
                    raise Phase5DashboardError(
                        "수동 비교 대화 assistant 메시지가 다릅니다."
                    )
                if diagnostics is not None and (
                    not isinstance(diagnostics, dict)
                    or set(diagnostics)
                    != {
                        "input_tokens",
                        "omitted_turns",
                        "elapsed_seconds",
                        "peak_allocated_bytes",
                        "gpu_total_memory_used_mib",
                    }
                    or any(
                        isinstance(diagnostics.get(key), bool)
                        or not isinstance(diagnostics.get(key), (int, float))
                        or diagnostics[key] < 0
                        for key in (
                            "input_tokens",
                            "omitted_turns",
                            "elapsed_seconds",
                            "peak_allocated_bytes",
                        )
                    )
                    or (
                        diagnostics.get("gpu_total_memory_used_mib") is not None
                        and (
                            isinstance(diagnostics["gpu_total_memory_used_mib"], bool)
                            or not isinstance(
                                diagnostics["gpu_total_memory_used_mib"], (int, float)
                            )
                            or diagnostics["gpu_total_memory_used_mib"] < 0
                        )
                    )
                ):
                    raise Phase5DashboardError("수동 비교 대화 진단값이 다릅니다.")
    if value["turn_count"] > _manual_session_contract(context)["max_turns_per_session"]:
        raise Phase5DashboardError("수동 대화 세션 turn 수가 계약을 넘습니다.")
    return value


def manual_session_payload(context: dict[str, Any], session_id: str) -> dict[str, Any]:
    path = _manual_session_path(context, session_id)
    if path.is_symlink() or not path.is_file():
        raise DashboardRequestError(
            HTTPStatus.NOT_FOUND, "수동 대화 세션을 찾을 수 없습니다."
        )
    maximum = _manual_session_contract(context)["max_session_bytes"]
    if path.stat().st_size > maximum:
        raise Phase5DashboardError("수동 대화 세션 크기가 계약을 넘습니다.")
    session = _validate_manual_session(
        context, _load_json(path, "수동 대화 세션"), session_id
    )
    profile = _session_prompt_profile(context, session)
    selection = _session_inference_selection(context, session)
    return {
        **session,
        "prompt_profile": profile["profile_id"],
        "prompt_profile_label": profile["label"],
        "system_prompt_sha256": profile["system_prompt_sha256"],
        "engine_selection": selection["selection_id"],
        "engine_selection_label": selection["label"],
        "engine_mode": selection["mode"],
        "engine_snapshots": session.get("engine_snapshots")
        or _selection_snapshots(context, selection),
    }


def manual_sessions_payload(context: dict[str, Any]) -> dict[str, Any]:
    root = _manual_session_root(context, create=False)
    if not root.exists():
        return {
            "items": [],
            "prompt_profiles": prompt_profiles_payload(context),
            "inference_engines": inference_engines_payload(context),
            "persisted": True,
            "local_only": True,
        }
    items: list[dict[str, Any]] = []
    for path in root.iterdir():
        if path.name.startswith("."):
            continue
        match = re.fullmatch(r"([0-9a-f]{24})\.json", path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise Phase5DashboardError("수동 대화 세션 경로에 예상 밖 항목이 있습니다.")
        session = manual_session_payload(context, match.group(1))
        profile = _session_prompt_profile(context, session)
        selection = _session_inference_selection(context, session)
        items.append(
            {
                "session_id": session["session_id"],
                "title": session["title"],
                "turn_count": session["turn_count"],
                "created_at_utc": session["created_at_utc"],
                "updated_at_utc": session["updated_at_utc"],
                "prompt_profile": profile["profile_id"],
                "prompt_profile_label": profile["label"],
                "engine_selection": selection["selection_id"],
                "engine_selection_label": selection["label"],
                "engine_mode": selection["mode"],
                "runtime_session_id": session.get("runtime_session_id"),
                "runtime_bound": session.get("runtime_session_id") is not None
                or session.get("runtime_binding_sha256") is not None,
            }
        )
    maximum = _manual_session_contract(context)["max_sessions"]
    if len(items) > maximum:
        raise Phase5DashboardError("수동 대화 session 수가 계약을 넘습니다.")
    items.sort(
        key=lambda item: (item["updated_at_utc"], item["session_id"]), reverse=True
    )
    return {
        "items": items,
        "prompt_profiles": prompt_profiles_payload(context),
        "inference_engines": inference_engines_payload(context),
        "persisted": True,
        "local_only": True,
    }


def _write_manual_session(context: dict[str, Any], session: dict[str, Any]) -> None:
    session_id = session["session_id"]
    root = _manual_session_root(context, create=True)
    target = root / f"{session_id}.json"
    if target.is_symlink():
        raise Phase5DashboardError("수동 대화 세션 대상이 symlink입니다.")
    payload = _json_bytes(session)
    if len(payload) > _manual_session_contract(context)["max_session_bytes"]:
        raise Phase5DashboardError("수동 대화 세션 저장 크기가 계약을 넘습니다.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{session_id}.", dir=root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        target.chmod(PRIVATE_FILE_MODE)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _runtime_canary_contract(context: dict[str, Any]) -> dict[str, Any]:
    prepared = context.get("runtime_canary")
    if not isinstance(prepared, dict) or not isinstance(prepared.get("contract"), dict):
        raise Phase5DashboardError(
            "이 dashboard config는 runtime canary를 지원하지 않습니다."
        )
    return prepared["contract"]


def _runtime_state_root(context: dict[str, Any], *, create: bool) -> Path:
    contract = _runtime_canary_contract(context)
    root = _safe_under(
        context["run_root"], contract["private_output_relative"], "runtime canary state"
    )
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
        if root.is_symlink() or not root.is_dir():
            raise Phase5DashboardError("runtime canary state 경로가 안전하지 않습니다.")
        root.chmod(PRIVATE_DIR_MODE)
    elif root.exists() and (root.is_symlink() or not root.is_dir()):
        raise Phase5DashboardError("runtime canary state 경로가 안전하지 않습니다.")
    return root


def _runtime_state_path(context: dict[str, Any], runtime_session_id: str) -> Path:
    if RUNTIME_SESSION_ID_PATTERN.fullmatch(runtime_session_id) is None:
        raise Phase5DashboardError("runtime_session_id가 잘못됐습니다.")
    return _runtime_state_root(context, create=False) / f"{runtime_session_id}.json"


def _validate_runtime_state(
    context: dict[str, Any], value: dict[str, Any], runtime_session_id: str
) -> dict[str, Any]:
    prepared = context.get("runtime_canary")
    release = prepared.get("release") if isinstance(prepared, dict) else None
    chart_result = value.get("chart_result")
    history = value.get("snapshot_history")
    if (
        not isinstance(release, dict)
        or value.get("schema_version") != "1.0.0"
        or value.get("runtime_session_id") != runtime_session_id
        or value.get("run_id") != context["manifest"]["run_id"]
        or value.get("run_build_id") != context["manifest"]["run_build_id"]
        or value.get("release_id") != release.get("release_id")
        or value.get("engine_version") != "saju-runtime-python-v1.1.0"
        or not isinstance(value.get("created_at_utc"), str)
        or not isinstance(value.get("updated_at_utc"), str)
        or isinstance(value.get("revision"), bool)
        or not isinstance(value.get("revision"), int)
        or value["revision"] < 1
        or not isinstance(value.get("chart_arguments"), dict)
        or not isinstance(chart_result, dict)
        or chart_result.get("status") not in {"ok", "partial"}
        or chart_result.get("fact_authority") not in {"HARD_GT", "POLICY_BOUND_RULE"}
        or not isinstance(history, list)
        or len(history) != value["revision"]
        or not history
    ):
        raise Phase5DashboardError("runtime canary state 계약이 다릅니다.")
    for index, snapshot in enumerate(history, 1):
        if (
            not isinstance(snapshot, dict)
            or set(snapshot)
            != {
                "revision",
                "snapshot_sha256",
                "created_at_utc",
                "period_present",
            }
            or snapshot.get("revision") != index
            or re.fullmatch(r"[0-9a-f]{64}", str(snapshot.get("snapshot_sha256", "")))
            is None
            or not isinstance(snapshot.get("created_at_utc"), str)
            or not isinstance(snapshot.get("period_present"), bool)
        ):
            raise Phase5DashboardError("runtime canary snapshot history가 다릅니다.")
    period_arguments = value.get("period_arguments")
    period_result = value.get("period_result")
    if (period_arguments is None) != (period_result is None) or (
        period_result is not None
        and (
            not isinstance(period_arguments, dict)
            or not isinstance(period_result, dict)
            or period_result.get("status") != "ok"
            or period_result.get("fact_authority") != "HARD_GT"
        )
    ):
        raise Phase5DashboardError("runtime canary 기간 state가 다릅니다.")
    return value


def _read_runtime_state(
    context: dict[str, Any], runtime_session_id: str
) -> dict[str, Any]:
    path = _runtime_state_path(context, runtime_session_id)
    maximum = _runtime_canary_contract(context)["max_state_bytes"]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != PRIVATE_FILE_MODE
            or metadata.st_uid != os.getuid()
            or not 1 <= metadata.st_size <= maximum
        ):
            raise Phase5DashboardError(
                "runtime canary state 파일 권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except FileNotFoundError as exc:
        raise DashboardRequestError(
            HTTPStatus.NOT_FOUND, "runtime canary state를 찾을 수 없습니다."
        ) from exc
    except OSError as exc:
        raise Phase5DashboardError(
            "runtime canary state를 안전하게 읽을 수 없습니다."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5DashboardError("runtime canary state JSON이 잘못됐습니다.") from exc
    if not isinstance(value, dict):
        raise Phase5DashboardError("runtime canary state가 JSON object가 아닙니다.")
    return _validate_runtime_state(context, value, runtime_session_id)


def _write_runtime_state(context: dict[str, Any], state: dict[str, Any]) -> None:
    runtime_session_id = state["runtime_session_id"]
    root = _runtime_state_root(context, create=True)
    target = root / f"{runtime_session_id}.json"
    if target.is_symlink():
        raise Phase5DashboardError("runtime canary state 대상이 symlink입니다.")
    payload = _json_bytes(state)
    if len(payload) > _runtime_canary_contract(context)["max_state_bytes"]:
        raise Phase5DashboardError("runtime canary state 저장 크기가 계약을 넘습니다.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{runtime_session_id}.", dir=root
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        target.chmod(PRIVATE_FILE_MODE)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _runtime_visible(result: dict[str, Any]) -> dict[str, Any]:
    allowlist = {
        "status",
        "hard_facts",
        "fact_authority",
        "code",
        "message",
        "limitations",
    }
    return {
        key: value
        for key, value in result.items()
        if key in allowlist and value is not None and value != []
    }


def _runtime_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    value = {
        "chart": _runtime_visible(state["chart_result"]),
        "period": (
            _runtime_visible(state["period_result"])
            if isinstance(state.get("period_result"), dict)
            else None
        ),
    }
    return {
        "value": value,
        "sha256": hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        ).hexdigest(),
    }


def runtime_state_payload(
    context: dict[str, Any], runtime_session_id: str
) -> dict[str, Any]:
    state = _read_runtime_state(context, runtime_session_id)
    snapshot = _runtime_snapshot(state)
    return {
        "runtime_session_id": runtime_session_id,
        "revision": state["revision"],
        "snapshot_sha256": snapshot["sha256"],
        "facts": snapshot["value"],
        "period_allowed": state["chart_result"].get("fact_authority") == "HARD_GT"
        and state["chart_result"].get("chart_id") is not None,
        "updated_at_utc": state["updated_at_utc"],
        "local_only": True,
        "model_visible_allowlist_applied": True,
    }


def _runtime_model_context(
    context: dict[str, Any], runtime_session_id: str | None
) -> tuple[str | None, str | None]:
    if runtime_session_id is None:
        return None, None
    if context.get("runtime_canary_active") is not True:
        raise Phase5DashboardError(
            "runtime 사실을 모델에 연결하려면 명시적인 runtime canary flag가 필요합니다."
        )
    state = _read_runtime_state(context, runtime_session_id)
    snapshot = _runtime_snapshot(state)
    prompt = (
        "[서버에서 계산한 승인 만세력 사실]\n"
        "아래 JSON만 계산 사실로 사용하세요. 없는 사실은 추측하지 말고, "
        "신강약·격국·용신·미래 사건으로 확대하지 마세요.\n"
        + json.dumps(snapshot["value"], ensure_ascii=False, sort_keys=True)
    )
    return prompt, snapshot["sha256"]


def _runtime_model_context_from_binding(
    binding: dict[str, Any],
) -> tuple[str, str, str]:
    if set(binding) != {
        "binding_id",
        "capability_sha256",
        "schema_version",
        "snapshot_sha256",
        "state_revision",
        "value",
    }:
        raise Phase5DashboardError("runtime model binding field 집합이 다릅니다.")
    value = binding.get("value")
    chart = value.get("chart") if isinstance(value, dict) else None
    period = value.get("period") if isinstance(value, dict) else None
    if (
        binding.get("schema_version") != "1.2.0"
        or binding.get("binding_id") != "saju-period-dashboard-binding-v1.2.0"
        or re.fullmatch(r"[0-9a-f]{64}", str(binding.get("capability_sha256", "")))
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(binding.get("snapshot_sha256", "")))
        is None
        or isinstance(binding.get("state_revision"), bool)
        or not isinstance(binding.get("state_revision"), int)
        or binding["state_revision"] < 1
        or not isinstance(chart, dict)
        or set(chart)
        != {"status", "fact_authority", "hard_facts", "message", "limitations"}
        or chart.get("status") != "ok"
        or chart.get("fact_authority") != "HARD_GT"
        or not isinstance(chart.get("hard_facts"), dict)
        or not isinstance(chart.get("message"), str)
        or not isinstance(chart.get("limitations"), list)
        or not isinstance(value, dict)
        or set(value) != {"chart", "period"}
    ):
        raise Phase5DashboardError("runtime model binding identity가 다릅니다.")
    try:
        validate_public_daily_label_result(period)
    except (PeriodRuntimeError, ValueError, TypeError) as exc:
        raise Phase5DashboardError(
            "일별 기간 model binding identity가 다릅니다."
        ) from exc
    forbidden = {
        "birth_input_id",
        "birth_date",
        "birth_time",
        "calculation_run_id",
        "chart_id",
        "chart_set_id",
        "ciphertext",
        "internal_trace",
        "local_birth_date",
        "local_birth_time",
        "nonce",
        "normalized_input",
        "period_id",
        "chart_authorization",
        "reference_date",
        "runtime_session_id",
        "session_id",
    }
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if forbidden.intersection(current):
                raise Phase5DashboardError(
                    "runtime model binding에 금지된 내부 field가 있습니다."
                )
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    canonical = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    actual_sha256 = hashlib.sha256(canonical).hexdigest()
    if actual_sha256 != binding["snapshot_sha256"]:
        raise Phase5DashboardError("runtime model binding hash가 다릅니다.")
    prompt = (
        "[서버에서 계산한 승인 원국·일별 기간 사실]\n"
        "이 JSON은 현재 사용자에게 이미 연결된 계산 결과입니다. 생년월일이나 "
        "출생시간을 다시 묻지 말고 질문에 바로 답하세요. 원국 질문에는 아래 간지 "
        "또는 일간을 최소 하나 그대로 사용하세요. 두 비교 모델에는 같은 snapshot을 "
        "전달합니다. 기간 질문에는 JSON의 날짜·연주·월주·일주 label을 그대로 사용하고 "
        "분 단위 절입, 원국 관계, 대운·세운, 사건 예측으로 확대하지 마세요. 없는 사실은 "
        "추측하지 마세요.\n" + canonical.decode("utf-8")
    )
    return prompt, binding["snapshot_sha256"], binding["capability_sha256"]


def _bound_prompt_intent(prompt: str) -> str:
    normalized = re.sub(r"\s+", "", prompt.casefold())
    if re.search(
        r"오늘|내일|모레|어제|날짜|운세|일진|이번주|이번달|올해|기간", normalized
    ):
        return "period_request"
    if re.search(r"사주|원국|명식|팔자|오행|일간|간지|천간|지지|성향|해석", normalized):
        return "chart_interpretation"
    return "general_followup"


def _chart_grounding_markers(binding: dict[str, Any]) -> list[str]:
    chart = binding["value"]["chart"]
    facts = chart["hard_facts"]
    pillars = facts.get("pillars")
    markers: list[str] = []
    if isinstance(pillars, dict):
        for value in pillars.values():
            if isinstance(value, str) and len(value.strip()) >= 2:
                markers.append(value)
            elif isinstance(value, dict):
                ganzhi = value.get("ganzhi")
                if isinstance(ganzhi, str) and len(ganzhi.strip()) == 2:
                    markers.append(ganzhi.strip())
    day_master = facts.get("day_master")
    if isinstance(day_master, str) and day_master.strip():
        markers.append(f"일간 {day_master.strip()}")
        markers.append(f"일간은 {day_master.strip()}")
    elif isinstance(day_master, dict):
        stem = day_master.get("stem")
        if isinstance(stem, str) and stem.strip():
            markers.append(f"일간 {stem.strip()}")
            markers.append(f"일간은 {stem.strip()}")
    return list(dict.fromkeys(markers))


def _period_grounding_markers(binding: dict[str, Any]) -> list[str]:
    period = binding["value"].get("period")
    if not isinstance(period, dict):
        return []
    markers: list[str] = []
    stack: list[Any] = [period]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str) and (
            re.fullmatch(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]", current)
            or re.fullmatch(
                r"[갑을병정무기경신임계][자축인묘진사오미신유술해]", current
            )
            or re.fullmatch(r"20[0-4][0-9]-[01][0-9]-[0-3][0-9]", current)
        ):
            markers.append(current)
    return list(dict.fromkeys(markers))


def evaluate_bound_output(
    prompt: str, output: str, binding: dict[str, Any]
) -> dict[str, Any]:
    """연결 응답이 재입력 요청 없이 승인 사실에 근거했는지 결정론적으로 검사한다."""

    intent = _bound_prompt_intent(prompt)
    compact = re.sub(r"\s+", "", output)
    reasons: list[str] = []
    reintake_patterns = (
        r"생년월일.{0,16}(알려|입력|필요)",
        r"출생(시간|시각|정보).{0,16}(알려|입력|필요)",
        r"태어난(날짜|시간).{0,16}(알려|입력|필요)",
        r"사주(정보|명식).{0,16}(알려|입력|필요)",
        r"알려주시면.{0,20}(사주|분석|봐드)",
    )
    if any(re.search(pattern, compact) for pattern in reintake_patterns):
        reasons.append("birth_input_reasked")
    if any(
        re.search(pattern, compact)
        for pattern in (
            r"(원국|사주정보|명식).{0,12}(없|제공되지|연결되지)",
            r"계산(기|도구).{0,12}(없|연결되지)",
        )
    ):
        reasons.append("bound_chart_denied")
    if re.search(
        r"snapshot|capability|systemprompt|내부검증|해시|hash", compact, re.IGNORECASE
    ):
        reasons.append("internal_contract_exposed")
    chart_markers = _chart_grounding_markers(binding)
    if intent in {"chart_interpretation", "period_request"} and not any(
        marker in output for marker in chart_markers
    ):
        reasons.append("chart_fact_missing")
    period_markers = _period_grounding_markers(binding)
    if intent == "period_request":
        if period_markers:
            if not any(marker in output for marker in period_markers):
                reasons.append("period_fact_missing")
        elif not (
            re.search(r"(오늘|날짜|기간|운세).{0,30}(제공범위|지원|계산|연결)", compact)
            and re.search(r"(아직|아니|않|밖|불가)", compact)
        ):
            reasons.append("period_limitation_missing")
    return {
        "gate_id": GROUNDING_GATE_ID,
        "intent": intent,
        "passed": not reasons,
        "reasons": reasons,
        "chart_markers": chart_markers,
        "period_markers": period_markers,
    }


def _grounding_correction(result: dict[str, Any]) -> str:
    chart_example = (
        result["chart_markers"][0] if result["chart_markers"] else "원국 간지"
    )
    if result["period_markers"]:
        safe_answer = (
            f"연결된 승인 원국 사실 {chart_example}과 승인된 날짜 사실 "
            f"{result['period_markers'][0]}을 기준으로 전통적 성찰을 도울 수 있습니다."
        )
    elif result["intent"] == "period_request":
        safe_answer = (
            f"연결된 승인 원국 사실 {chart_example}을 기준으로 전통적 성찰을 도울 수 "
            "있습니다. 정확한 오늘 날짜 운세는 아직 계산·연결되지 않아 제공 범위가 "
            "아닙니다."
        )
    else:
        safe_answer = (
            f"연결된 승인 원국 사실 {chart_example}을 기준으로 보면, 사주는 삶을 "
            "단정하기보다 현재를 성찰하는 참고로 해석할 수 있습니다."
        )
    return (
        "다른 설명이나 질문 없이 다음 따옴표 안의 답변만 그대로 출력하세요. "
        f"`{safe_answer}`"
    )


def _messages_for_engine(
    previous_messages: Sequence[dict[str, Any]],
    engine_id: str,
    prompt: str,
    system_prompt: str | None,
    runtime_context: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    system_parts = [part for part in (system_prompt, runtime_context) if part]
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    for message in previous_messages:
        if (
            message["role"] == "user"
            or message.get("engine_id", engine_id) == engine_id
        ):
            messages.append({"role": message["role"], "content": message["content"]})
    messages.append({"role": "user", "content": prompt})
    return messages


def _messages_for_v12(
    previous_messages: Sequence[dict[str, Any]], engine_id: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in previous_messages:
        if message["role"] == "user" or "engine_id" in message:
            normalized.append(dict(message))
        else:
            normalized.append(
                {
                    **message,
                    "engine_id": engine_id,
                    "diagnostics": None,
                }
            )
    return normalized


def execute_manual_generation(
    context: dict[str, Any],
    prompt: str,
    session_id: str | None = None,
    profile: str | None = None,
    engine_selection: str | None = None,
    runtime_session_id: str | None = None,
    runtime_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    clean_prompt = prompt.strip()
    contract = _manual_session_contract(context)
    if session_id is None:
        if len(manual_sessions_payload(context)["items"]) >= contract["max_sessions"]:
            raise Phase5DashboardError("수동 대화 세션 최대 개수에 도달했습니다.")
        session_id = secrets.token_hex(12)
        previous_messages: list[dict[str, Any]] = []
        previous_turn_count = 0
        created_at = datetime.now(timezone.utc).isoformat()
        title = clean_prompt.splitlines()[0][: contract["title_max_chars"]]
        selected_profile = _prompt_profile(
            context,
            (
                context["prompt_profiles"]["bound_profile"]
                if runtime_binding is not None
                else profile or context["prompt_profiles"]["default_profile"]
            ),
        )
        selected_engines = _inference_selection(
            context,
            engine_selection or context["inference_engines"]["default_selection"],
        )
        selected_runtime_session_id = runtime_session_id
        selected_runtime_binding = runtime_binding
    else:
        try:
            current = manual_session_payload(context, session_id)
        except DashboardRequestError as exc:
            raise Phase5DashboardError(str(exc)) from exc
        if current["turn_count"] >= contract["max_turns_per_session"]:
            raise Phase5DashboardError(
                "이 수동 대화 세션은 최대 turn 수에 도달했습니다."
            )
        previous_messages = list(current["messages"])
        previous_turn_count = current["turn_count"]
        created_at = current["created_at_utc"]
        title = current["title"]
        selected_profile = _session_prompt_profile(context, current)
        selected_engines = _session_inference_selection(context, current)
        if current.get("schema_version") == "1.4.0":
            raise Phase5DashboardError(
                "LEGACY_BOUND_SESSION_READ_ONLY: 기존 v1.9 원국 대화는 읽기 전용입니다. "
                "원국에서 새 연결 대화를 시작하세요."
            )
        stored_runtime_session_id = current.get("runtime_session_id")
        if (
            stored_runtime_session_id is not None
            and runtime_session_id is not None
            and runtime_session_id != stored_runtime_session_id
        ):
            raise Phase5DashboardError(
                "기존 세션에 결합된 runtime_session_id는 변경할 수 없습니다."
            )
        selected_runtime_session_id = runtime_session_id or stored_runtime_session_id
        selected_runtime_binding = runtime_binding
        stored_binding_sha256 = current.get("runtime_binding_sha256")
        if stored_binding_sha256 is not None:
            if runtime_binding is None:
                raise Phase5DashboardError(
                    "기존 v1.9 대화에는 활성 runtime session이 필요합니다."
                )
            supplied_binding_sha256 = runtime_binding.get("capability_sha256")
            if supplied_binding_sha256 != stored_binding_sha256:
                raise Phase5DashboardError(
                    "기존 대화에 결합된 runtime capability는 변경할 수 없습니다."
                )
            if runtime_binding.get("snapshot_sha256") != current.get(
                "runtime_snapshot_sha256"
            ):
                raise Phase5DashboardError(
                    "기존 대화의 원국·기간 snapshot은 변경할 수 없습니다. "
                    "새 기간은 새 연결 대화에서 사용하세요."
                )
        if profile is not None and profile != selected_profile["profile_id"]:
            raise Phase5DashboardError(
                "기존 세션의 prompt profile은 변경할 수 없습니다."
            )
        if (
            engine_selection is not None
            and engine_selection != selected_engines["selection_id"]
        ):
            raise Phase5DashboardError(
                "기존 세션의 inference engine은 변경할 수 없습니다."
            )
    runtime_binding_sha256: str | None = None
    if selected_runtime_binding is not None:
        if (
            context["config"]["schema_version"] != "1.12.0"
            or selected_runtime_session_id is not None
        ):
            raise Phase5DashboardError(
                "기간 runtime model binding은 dashboard v1.12 전용입니다."
            )
        (
            runtime_context,
            runtime_snapshot_sha256,
            runtime_binding_sha256,
        ) = _runtime_model_context_from_binding(selected_runtime_binding)
    else:
        runtime_context, runtime_snapshot_sha256 = _runtime_model_context(
            context, selected_runtime_session_id
        )
    for engine_id in selected_engines["engine_ids"]:
        availability = _engine_availability(context, engine_id)
        if not availability["available"]:
            raise Phase5DashboardError(
                f"{engine_id} 모델을 사용할 수 없습니다: "
                + ", ".join(availability["reasons"])
            )
    requested_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    generated_by_engine: dict[str, dict[str, Any]] = {}
    grounding_by_engine: dict[str, dict[str, Any]] = {}
    for index, engine_id in enumerate(selected_engines["engine_ids"]):
        if index:
            gpu = _gpu_snapshot()
            idle_max = context["config"]["model_check"]["gpu_idle_max_used_mib"]
            if not gpu.get("available") or gpu.get("used_mib", idle_max + 1) > idle_max:
                raise Phase5DashboardError(
                    "첫 모델 해제 뒤 GPU가 비교 모델 순차 로드 기준으로 복귀하지 않았습니다."
                )
        engine = context["inference_engines"]["engines"][engine_id]
        model_messages = _messages_for_engine(
            previous_messages,
            engine_id,
            clean_prompt,
            selected_profile["system_prompt_text"],
            runtime_context,
        )
        engine_started = time.monotonic()
        generated = _generate_conversation(
            engine["resolved_path"],
            model_messages,
            context["config"]["model_check"]["generation"],
            contract["max_context_tokens"],
            engine["model_sha256"],
            engine.get("required_file_sha256"),
            context["config"]["training_contract"]["gpu_hard_cap_mib"],
        )
        retry_count = 0
        if selected_runtime_binding is not None:
            grounding = evaluate_bound_output(
                clean_prompt, generated["output"], selected_runtime_binding
            )
            if not grounding["passed"]:
                retry_count = 1
                correction = _grounding_correction(grounding)
                corrected_messages = [
                    dict(message)
                    for message in model_messages
                    if message["role"] == "system"
                ]
                corrected_messages.append({"role": "user", "content": correction})
                generated = _generate_conversation(
                    engine["resolved_path"],
                    corrected_messages,
                    context["config"]["model_check"]["generation"],
                    contract["max_context_tokens"],
                    engine["model_sha256"],
                    engine.get("required_file_sha256"),
                    context["config"]["training_contract"]["gpu_hard_cap_mib"],
                )
                grounding = evaluate_bound_output(
                    clean_prompt, generated["output"], selected_runtime_binding
                )
            if not grounding["passed"]:
                reason_copy = ",".join(grounding["reasons"])
                raise GroundingGateError(
                    f"{GROUNDING_FAILURE_CODE}: 연결된 원국을 안전하게 사용한 응답을 "
                    f"생성하지 못했습니다({engine_id}:{reason_copy})."
                )
            grounding_by_engine[engine_id] = {
                "intent": grounding["intent"],
                "retry_count": retry_count,
                "passed": True,
            }
        generated_by_engine[engine_id] = {
            **generated,
            "elapsed_seconds": round(time.monotonic() - engine_started, 3),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    completed_at = datetime.now(timezone.utc).isoformat()
    stored_profile = selected_profile
    if selected_profile["profile_id"] == context["prompt_profiles"]["legacy_profile"]:
        stored_profile = _prompt_profile(context, "raw_no_system")
    normalized_previous = _messages_for_v12(
        previous_messages, selected_engines["engine_ids"][0]
    )
    assistant_messages = []
    for engine_id in selected_engines["engine_ids"]:
        generated = generated_by_engine[engine_id]
        assistant_messages.append(
            {
                "role": "assistant",
                "engine_id": engine_id,
                "content": generated["output"],
                "created_at_utc": generated["completed_at_utc"],
                "diagnostics": {
                    "input_tokens": generated["input_tokens"],
                    "omitted_turns": generated["omitted_messages"] // 2,
                    "elapsed_seconds": generated["elapsed_seconds"],
                    "peak_allocated_bytes": generated.get("peak_allocated_bytes", 0),
                    "gpu_total_memory_used_mib": generated.get(
                        "gpu_total_memory_used_mib"
                    ),
                },
            }
        )
    grounding_intent = (
        next(iter(grounding_by_engine.values()))["intent"]
        if grounding_by_engine
        else None
    )
    session = {
        "schema_version": "1.5.0" if runtime_binding_sha256 else "1.3.0",
        "session_id": session_id,
        "run_id": context["manifest"]["run_id"],
        "run_build_id": context["manifest"]["run_build_id"],
        "run_sha256": context["manifest"]["run_sha256"],
        "title": title,
        "created_at_utc": created_at,
        "updated_at_utc": completed_at,
        "turn_count": previous_turn_count + 1,
        "prompt_profile": stored_profile["profile_id"],
        "system_prompt_sha256": stored_profile["system_prompt_sha256"],
        "engine_selection": selected_engines["selection_id"],
        "engine_snapshots": _selection_snapshots(context, selected_engines),
        **(
            {
                "runtime_binding_sha256": runtime_binding_sha256,
                "runtime_snapshot_sha256": runtime_snapshot_sha256,
            }
            if runtime_binding_sha256
            else {
                "runtime_session_id": selected_runtime_session_id,
                "runtime_snapshot_sha256": runtime_snapshot_sha256,
            }
        ),
        **(
            {
                "grounding_gate": {
                    "gate_id": GROUNDING_GATE_ID,
                    "intent": grounding_intent,
                    "retries_by_engine": {
                        engine_id: grounding_by_engine[engine_id]["retry_count"]
                        for engine_id in selected_engines["engine_ids"]
                    },
                    "passed_by_engine": {
                        engine_id: grounding_by_engine[engine_id]["passed"]
                        for engine_id in selected_engines["engine_ids"]
                    },
                }
            }
            if grounding_by_engine
            else {}
        ),
        "messages": normalized_previous
        + [
            {"role": "user", "content": clean_prompt, "created_at_utc": requested_at},
            *assistant_messages,
        ],
        "quality_gate_evaluated": bool(grounding_by_engine),
        "production_promotion_allowed": False,
    }
    _validate_manual_session(context, session, session_id)
    _write_manual_session(context, session)
    stored_session = manual_session_payload(context, session_id)
    outputs = {
        engine_id: generated_by_engine[engine_id]["output"]
        for engine_id in selected_engines["engine_ids"]
    }
    contexts = {
        engine_id: {
            "input_tokens": generated_by_engine[engine_id]["input_tokens"],
            "omitted_turns": generated_by_engine[engine_id]["omitted_messages"] // 2,
            "max_context_tokens": contract["max_context_tokens"],
            "elapsed_seconds": generated_by_engine[engine_id]["elapsed_seconds"],
            "peak_allocated_bytes": generated_by_engine[engine_id].get(
                "peak_allocated_bytes", 0
            ),
            "gpu_total_memory_used_mib": generated_by_engine[engine_id].get(
                "gpu_total_memory_used_mib"
            ),
            "runtime_snapshot_sha256": runtime_snapshot_sha256,
        }
        for engine_id in selected_engines["engine_ids"]
    }
    return {
        "status": "generated",
        **(
            {
                "output": outputs[selected_engines["engine_ids"][0]],
                "context": contexts[selected_engines["engine_ids"][0]],
            }
            if selected_engines["mode"] == "single"
            else {}
        ),
        "outputs": outputs,
        "session_id": session_id,
        "session": stored_session,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "contexts": contexts,
        "engine_selection": {
            "selection_id": selected_engines["selection_id"],
            "label": selected_engines["label"],
            "mode": selected_engines["mode"],
            "engine_ids": selected_engines["engine_ids"],
        },
        "runtime_snapshot_sha256": runtime_snapshot_sha256,
        "runtime_binding_applied": runtime_binding_sha256 is not None,
        "grounding_gate": (
            {
                "gate_id": GROUNDING_GATE_ID,
                "intent": grounding_intent,
                "retries_by_engine": {
                    engine_id: grounding_by_engine[engine_id]["retry_count"]
                    for engine_id in selected_engines["engine_ids"]
                },
                "passed_by_engine": {
                    engine_id: grounding_by_engine[engine_id]["passed"]
                    for engine_id in selected_engines["engine_ids"]
                },
            }
            if grounding_by_engine
            else None
        ),
        "prompt_profile": {
            "profile_id": stored_profile["profile_id"],
            "label": stored_profile["label"],
            "system_prompt_sha256": stored_profile["system_prompt_sha256"],
            "production_like": stored_profile["production_like"],
            "diagnostic_only": stored_profile["diagnostic_only"],
        },
        "persisted": True,
        "local_only": True,
        "quality_gate_evaluated": bool(grounding_by_engine),
        "production_promotion_allowed": False,
    }


def _new_runtime_engine(context: dict[str, Any]) -> Any:
    prepared = context.get("runtime_canary")
    if not isinstance(prepared, dict) or not isinstance(prepared.get("release"), dict):
        raise DashboardRequestError(
            HTTPStatus.CONFLICT,
            "통과한 conformance v3 runtime release가 아직 없습니다.",
        )
    try:
        from scripts.runtime.calculation.approved_engine import (
            ApprovedSajuRuntimeEngine,
        )
        from scripts.runtime.calculation.errors import RuntimeCalculationError

        try:
            return ApprovedSajuRuntimeEngine(
                release_registry=prepared["release_path"],
                enable_approved_runtime=True,
            )
        except RuntimeCalculationError as exc:
            raise Phase5DashboardError(
                f"runtime engine 준비가 실패했습니다: {exc.code}"
            ) from exc
    except ImportError as exc:
        raise Phase5DashboardError(
            "runtime v1.1 고정 의존성이 현재 dashboard Python 환경에 없습니다."
        ) from exc


def runtime_status_payload(server: DashboardHTTPServer) -> dict[str, Any]:
    prepared = server.context.get("runtime_canary")
    configured = isinstance(prepared, dict)
    release = prepared.get("release") if configured else None
    release_available = isinstance(release, dict)
    enabled = bool(server.runtime_canary_requested and release_available)
    if not configured:
        code = "RUNTIME_CANARY_NOT_CONFIGURED"
        message = "이 dashboard 버전에는 runtime canary가 없습니다."
    elif not release_available:
        code = "RUNTIME_RELEASE_REQUIRED"
        message = (
            "KASI 공식 Gate를 통과한 runtime release가 없어 계산기를 차단했습니다."
        )
    elif not server.runtime_canary_requested:
        code = "RUNTIME_FEATURE_DISABLED"
        message = "승인 runtime은 기본 off입니다. 명시적 실행 flag가 필요합니다."
    else:
        code = None
        message = "승인 runtime canary가 활성화됐습니다."
    return {
        "schema_version": "1.0.0",
        "configured": configured,
        "release_available": release_available,
        "feature_requested": bool(server.runtime_canary_requested),
        "enabled": enabled,
        "code": code,
        "message": message,
        "release_id": release.get("release_id") if release_available else None,
        "engine_version": (
            prepared["contract"]["engine_version"] if configured else None
        ),
        "profile_id": prepared["contract"]["profile_id"] if configured else None,
        "remote_unauthenticated": server.remote_unauthenticated,
        "unsafe_remote_runtime_acknowledged": bool(
            server.allow_unauthenticated_runtime_canary
        ),
        "facts_rendered_without_model": True,
        "request_metadata_logged": True,
        "request_bodies_logged": False,
        "state_local_only": True,
    }


def period_runtime_status_payload(server: DashboardHTTPServer) -> dict[str, Any]:
    prepared = server.context.get("period_runtime")
    configured = isinstance(prepared, dict)
    release_available = (
        configured
        and isinstance(prepared.get("parent_release"), dict)
        and isinstance(prepared.get("period_release"), dict)
    )
    binding = server.period_binding
    if binding is not None:
        status = dict(binding.status())
        status["remote_unauthenticated"] = server.remote_unauthenticated
        status["public_url_requires_login"] = False
        status["explicit_conversation_binding_required"] = True
        status["grounding_gate_id"] = GROUNDING_GATE_ID
        return status
    if not configured:
        code = "RUNTIME_NOT_CONFIGURED"
        message = "이 dashboard 버전에는 제한 runtime이 없습니다."
    elif not release_available:
        code = "RUNTIME_RELEASE_REQUIRED"
        message = "승인된 제한 runtime release가 없어 계산기를 차단했습니다."
    else:
        code = "RUNTIME_FEATURE_DISABLED"
        message = "제한 runtime은 기본 off입니다. 명시적 실행 flag가 필요합니다."
    return {
        "schema_version": "1.2.0",
        "status": "disabled",
        "configured": configured,
        "release_available": bool(release_available),
        "feature_requested": bool(server.period_runtime_requested),
        "enabled": False,
        "code": code,
        "message": message,
        "parent_runtime_release_id": (
            prepared["contract"]["parent_release_id"] if configured else None
        ),
        "period_release_id": (
            prepared["contract"]["period_release_id"] if configured else None
        ),
        "facts_rendered_without_model": True,
        "daily_label_range_allowed": bool(configured),
        "period_minimum": (
            prepared["contract"].get("period_minimum") if configured else None
        ),
        "period_maximum": (
            prepared["contract"].get("period_maximum") if configured else None
        ),
        "period_maximum_days": 31 if configured else None,
        "period_evaluation_local_time": "12:00" if configured else None,
        "intraday_segments_supported": False,
        "production_application_binding": False,
        "model_context_binding": bool(release_available),
        "state_encrypted": True,
        "retention_seconds": 1800,
        "client_authentication_required": False,
        "public_url_requires_login": False,
        "remote_unauthenticated": server.remote_unauthenticated,
        "request_metadata_logged": True,
        "request_bodies_logged": False,
        "feature_default": False,
        "explicit_conversation_binding_required": True,
        "grounding_gate_id": GROUNDING_GATE_ID,
    }


def _require_runtime_enabled(server: DashboardHTTPServer) -> None:
    status = runtime_status_payload(server)
    if not status["enabled"]:
        raise DashboardRequestError(HTTPStatus.CONFLICT, status["message"])


def _runtime_engine_for_state(
    server: DashboardHTTPServer,
    runtime_session_id: str,
    state: dict[str, Any],
) -> Any:
    cached = server.runtime_engines.get(runtime_session_id)
    if cached is not None:
        return cached
    engine = _new_runtime_engine(server.context)
    replay = engine.calculate_chart(state["chart_arguments"])
    expected = state["chart_result"]
    identity_fields = (
        "status",
        "fact_authority",
        "chart_id",
        "chart_set_id",
        "calculation_run_id",
    )
    if any(replay.get(key) != expected.get(key) for key in identity_fields) or (
        _runtime_visible(replay) != _runtime_visible(expected)
    ):
        raise Phase5DashboardError(
            "runtime state 재현 결과가 저장된 chart fingerprint와 다릅니다."
        )
    server.runtime_engines[runtime_session_id] = engine
    return engine


def _runtime_response_from_result(
    result: dict[str, Any], runtime_session_id: str | None
) -> dict[str, Any]:
    return {
        "runtime_session_id": runtime_session_id,
        "facts": {"chart": _runtime_visible(result), "period": None},
        "period_allowed": result.get("fact_authority") == "HARD_GT"
        and result.get("chart_id") is not None,
        "persisted": False,
        "local_only": True,
        "model_visible_allowlist_applied": True,
    }


def execute_runtime_chart(
    server: DashboardHTTPServer, payload: dict[str, Any]
) -> dict[str, Any]:
    _require_runtime_enabled(server)
    if set(payload) != {"runtime_session_id", "arguments"} or not isinstance(
        payload.get("arguments"), dict
    ):
        raise DashboardRequestError(
            HTTPStatus.BAD_REQUEST,
            "runtime chart 요청은 runtime_session_id와 arguments만 허용합니다.",
        )
    requested_id = payload.get("runtime_session_id")
    if requested_id is not None and (
        not isinstance(requested_id, str)
        or RUNTIME_SESSION_ID_PATTERN.fullmatch(requested_id) is None
    ):
        raise DashboardRequestError(
            HTTPStatus.BAD_REQUEST, "runtime_session_id가 잘못됐습니다."
        )
    with server.runtime_lock:
        previous = (
            _read_runtime_state(server.context, requested_id)
            if requested_id is not None
            else None
        )
        runtime_session_id = requested_id or secrets.token_hex(12)
        engine = (
            _runtime_engine_for_state(server, runtime_session_id, previous)
            if previous is not None
            else _new_runtime_engine(server.context)
        )
        result = engine.calculate_chart(payload["arguments"])
        if result.get("status") not in {"ok", "partial"}:
            return _runtime_response_from_result(result, requested_id)
        now = datetime.now(timezone.utc).isoformat()
        revision = (previous["revision"] + 1) if previous is not None else 1
        state = {
            "schema_version": "1.0.0",
            "runtime_session_id": runtime_session_id,
            "run_id": server.context["manifest"]["run_id"],
            "run_build_id": server.context["manifest"]["run_build_id"],
            "release_id": server.context["runtime_canary"]["release"]["release_id"],
            "engine_version": "saju-runtime-python-v1.1.0",
            "created_at_utc": previous["created_at_utc"] if previous else now,
            "updated_at_utc": now,
            "revision": revision,
            "chart_arguments": payload["arguments"],
            "chart_result": result,
            "period_arguments": None,
            "period_result": None,
            "snapshot_history": list(previous["snapshot_history"]) if previous else [],
        }
        snapshot = _runtime_snapshot(state)
        state["snapshot_history"].append(
            {
                "revision": revision,
                "snapshot_sha256": snapshot["sha256"],
                "created_at_utc": now,
                "period_present": False,
            }
        )
        _validate_runtime_state(server.context, state, runtime_session_id)
        _write_runtime_state(server.context, state)
        server.runtime_engines[runtime_session_id] = engine
        return {
            **runtime_state_payload(server.context, runtime_session_id),
            "persisted": True,
        }


def execute_runtime_period(
    server: DashboardHTTPServer, payload: dict[str, Any]
) -> dict[str, Any]:
    _require_runtime_enabled(server)
    if (
        set(payload) != {"runtime_session_id", "arguments"}
        or not isinstance(payload.get("runtime_session_id"), str)
        or not isinstance(payload.get("arguments"), dict)
    ):
        raise DashboardRequestError(
            HTTPStatus.BAD_REQUEST,
            "runtime period 요청은 runtime_session_id와 arguments만 허용합니다.",
        )
    runtime_session_id = payload["runtime_session_id"]
    if RUNTIME_SESSION_ID_PATTERN.fullmatch(runtime_session_id) is None:
        raise DashboardRequestError(
            HTTPStatus.BAD_REQUEST, "runtime_session_id가 잘못됐습니다."
        )
    supplied = payload["arguments"]
    if set(supplied) != {"period_type", "start_date", "end_date"}:
        raise DashboardRequestError(
            HTTPStatus.BAD_REQUEST, "runtime period arguments가 잘못됐습니다."
        )
    with server.runtime_lock:
        state = _read_runtime_state(server.context, runtime_session_id)
        chart_id = state["chart_result"].get("chart_id")
        if state["chart_result"].get("fact_authority") != "HARD_GT" or not isinstance(
            chart_id, str
        ):
            raise DashboardRequestError(
                HTTPStatus.CONFLICT,
                "기간 계산에는 exact 입력으로 확정된 HARD_GT chart가 필요합니다.",
            )
        engine = _runtime_engine_for_state(server, runtime_session_id, state)
        arguments = {
            "chart_id": chart_id,
            "period_type": supplied["period_type"],
            "start_date": supplied["start_date"],
            "end_date": supplied["end_date"],
            "timezone": "Asia/Seoul",
        }
        result = engine.calculate_period(arguments)
        if result.get("status") != "ok":
            return {
                **runtime_state_payload(server.context, runtime_session_id),
                "period_attempt": _runtime_visible(result),
                "persisted": False,
            }
        now = datetime.now(timezone.utc).isoformat()
        revision = state["revision"] + 1
        updated = {
            **state,
            "updated_at_utc": now,
            "revision": revision,
            "period_arguments": arguments,
            "period_result": result,
            "snapshot_history": list(state["snapshot_history"]),
        }
        snapshot = _runtime_snapshot(updated)
        updated["snapshot_history"].append(
            {
                "revision": revision,
                "snapshot_sha256": snapshot["sha256"],
                "created_at_utc": now,
                "period_present": True,
            }
        )
        _validate_runtime_state(server.context, updated, runtime_session_id)
        _write_runtime_state(server.context, updated)
        return {
            **runtime_state_payload(server.context, runtime_session_id),
            "persisted": True,
        }


class DashboardHTTPServer(ThreadingHTTPServer):
    """loopback 전용 대시보드 context와 CSRF token을 보관한다."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        context: dict[str, Any],
        asset_root: Path,
        csrf_token: str,
        trusted_origin: str | None = None,
        basic_auth: tuple[str, str] | None = None,
        runtime_canary_requested: bool = False,
        allow_unauthenticated_runtime_canary: bool = False,
        period_binding: Any | None = None,
        period_runtime_requested: bool = False,
        generation_runner: Any | None = None,
    ) -> None:
        if trusted_origin is None and basic_auth is not None:
            raise Phase5DashboardError("Basic 인증에는 원격 공유 Origin이 필요합니다.")
        self.context = context
        self.asset_root = asset_root
        self.csrf_token = csrf_token
        self.basic_auth = basic_auth
        self.runtime_canary_requested = bool(runtime_canary_requested)
        self.allow_unauthenticated_runtime_canary = bool(
            allow_unauthenticated_runtime_canary
        )
        self.period_binding = period_binding
        self.period_runtime_requested = bool(period_runtime_requested)
        self.remote_unauthenticated = trusted_origin is not None and basic_auth is None
        self.generation_lock = threading.Lock()
        self.runtime_lock = threading.Lock()
        self.generation_runner = generation_runner
        self.runtime_engines: dict[str, Any] = {}
        self.dataset_cache: dict[str, Any] = {}
        self.dataset_cache_lock = threading.Lock()
        self.rate_limiters: dict[str, SlidingWindowRateLimiter] = {}
        runtime_contract = context["config"].get("period_runtime")
        if isinstance(runtime_contract, dict):
            limits = runtime_contract["rate_limits_per_minute"]
            self.rate_limiters = {
                name: SlidingWindowRateLimiter(maximum)
                for name, maximum in limits.items()
            }
        super().__init__(address, DashboardRequestHandler)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{value}" for value in self.allowed_hosts}
        if trusted_origin is not None:
            validated_origin = _validated_trusted_origin(trusted_origin)
            self.allowed_origins.add(validated_origin)
            hostname = urlsplit(validated_origin).hostname
            if hostname is not None:
                self.allowed_hosts.add(hostname)

    def server_close(self) -> None:
        binding = getattr(self, "period_binding", None)
        if binding is not None:
            binding.close()
            self.period_binding = None
        super().server_close()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """정적 UI와 read-mostly JSON API를 보안 헤더와 함께 제공한다."""

    server: DashboardHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        del format
        path = urlsplit(self.path).path
        route = re.sub(r"(?<=/)(?:[0-9a-f]{24})(?=/|$)", "{opaque_id}", path)
        status = str(args[1]) if len(args) > 1 else "unknown"
        reason = getattr(self, "_log_reason_code", "OK")
        sys.stderr.write(
            "phase5-dashboard "
            f"method={self.command} route={route} status={status} reason={reason}\n"
        )

    def _headers(
        self,
        status: int,
        content_type: str,
        length: int,
        *,
        basic_challenge: bool = False,
        retry_after: int | None = None,
    ) -> None:
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
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if basic_challenge:
            self.send_header(
                "WWW-Authenticate", 'Basic realm="KI20 dashboard", charset="UTF-8"'
            )
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()

    def _send_bytes(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        *,
        basic_challenge: bool = False,
        retry_after: int | None = None,
    ) -> None:
        self._headers(
            status,
            content_type,
            len(payload),
            basic_challenge=basic_challenge,
            retry_after=retry_after,
        )
        self.wfile.write(payload)

    def _send_json(self, status: int, value: Any, *, reason_code: str = "OK") -> None:
        self._log_reason_code = reason_code
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    def _error(
        self,
        status: int,
        message: str,
        *,
        reason_code: str | None = None,
        retry_after: int | None = None,
        basic_challenge: bool = False,
    ) -> None:
        self.close_connection = True
        code = reason_code or f"HTTP_{int(status)}"
        self._log_reason_code = code
        value: dict[str, Any] = {"status": status, "error": message}
        if self.server.context["config"]["schema_version"] in {"1.10.0", "1.12.0"}:
            value["code"] = code
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        self._send_bytes(
            status,
            payload,
            "application/json; charset=utf-8",
            basic_challenge=basic_challenge,
            retry_after=retry_after,
        )

    def _rate_limit(self, name: str) -> None:
        limiter = self.server.rate_limiters.get(name)
        if limiter is None:
            return
        allowed, retry_after = limiter.acquire()
        if not allowed:
            raise DashboardRequestError(
                HTTPStatus.TOO_MANY_REQUESTS,
                "요청 한도를 넘었습니다. 잠시 후 다시 시도하세요.",
                reason_code=f"{name.upper()}_RATE_LIMITED",
                retry_after=retry_after,
            )

    def _period_binding(self) -> Any:
        binding = self.server.period_binding
        if binding is None:
            raise DashboardRequestError(
                HTTPStatus.CONFLICT,
                "기간 runtime은 기본 off이며 명시적 실행 flag가 필요합니다.",
                reason_code="RUNTIME_FEATURE_DISABLED",
            )
        return binding

    @staticmethod
    def _binding_request(call: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            if type(exc).__name__ not in {
                "ChartOnlyDashboardBindingError",
                "ChartDayDashboardBindingError",
                "PeriodDashboardBindingError",
            }:
                raise
            status = int(getattr(exc, "status", HTTPStatus.INTERNAL_SERVER_ERROR))
            reason = str(getattr(exc, "reason_code", "RUNTIME_INTERNAL_ERROR"))
            raise DashboardRequestError(
                status,
                str(exc),
                reason_code=reason,
                retry_after=1 if reason == "RUNTIME_BUSY" else None,
            ) from exc

    def _internal_error(self, exc: Exception) -> None:
        if self.server.context["config"]["schema_version"] in {"1.10.0", "1.12.0"}:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "요청을 안전하게 처리하지 못했습니다.",
                reason_code="INTERNAL_ERROR",
            )
            return
        self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _guard(self, *, require_origin: bool = False) -> str:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            raise DashboardRequestError(
                HTTPStatus.MISDIRECTED_REQUEST, "허용되지 않은 Host입니다."
            )
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or "%" in parsed.path:
            raise DashboardRequestError(
                HTTPStatus.BAD_REQUEST, "쿼리·인코딩 경로는 지원하지 않습니다."
            )
        if (
            require_origin
            and self.headers.get("Origin") not in self.server.allowed_origins
        ):
            raise DashboardRequestError(
                HTTPStatus.FORBIDDEN, "허용되지 않은 Origin입니다."
            )
        return parsed.path

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-CSRF-Token", ""), self.server.csrf_token
        )

    def _basic_authenticated(self) -> bool:
        credentials = self.server.basic_auth
        if credentials is None:
            return True
        header = self.headers.get("Authorization", "")
        if not header or len(header) > AUTHORIZATION_HEADER_MAX_BYTES:
            return False
        scheme, separator, encoded = header.partition(" ")
        if separator != " " or scheme.lower() != "basic" or not encoded:
            return False
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            return False
        expected = f"{credentials[0]}:{credentials[1]}".encode("ascii")
        return len(
            decoded
        ) <= AUTHORIZATION_HEADER_MAX_BYTES and secrets.compare_digest(
            decoded, expected
        )

    def _require_basic_auth(self) -> bool:
        if self._basic_authenticated():
            return True
        self._error(
            HTTPStatus.UNAUTHORIZED,
            "원격 공유 인증이 필요합니다.",
            basic_challenge=True,
        )
        return False

    def _static(self, path: str) -> tuple[bytes, str]:
        asset = STATIC_ASSETS.get(path)
        if asset is None:
            raise DashboardRequestError(
                HTTPStatus.NOT_FOUND, "정적 자산을 찾을 수 없습니다."
            )
        filename, content_type = asset
        target = self.server.asset_root / filename
        if target.is_symlink() or not target.is_file():
            raise DashboardRequestError(
                HTTPStatus.INTERNAL_SERVER_ERROR, "정적 자산이 없습니다."
            )
        payload = target.read_bytes()
        if filename == "index.html":
            placeholder = b"__CSRF_TOKEN__"
            if payload.count(placeholder) != 1:
                raise DashboardRequestError(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "CSRF placeholder가 잘못됐습니다."
                )
            payload = payload.replace(
                placeholder, self.server.csrf_token.encode("ascii")
            )
        return payload, content_type

    def do_GET(self) -> None:
        try:
            path = self._guard()
            if not self._require_basic_auth():
                return
            if path.startswith("/api/"):
                if not self._authorized():
                    raise DashboardRequestError(
                        HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다."
                    )
                if path == "/api/status":
                    self._send_json(HTTPStatus.OK, status_payload(self.server.context))
                    return
                if path == "/api/runtime/status":
                    status = (
                        period_runtime_status_payload(self.server)
                        if self.server.context["config"]["schema_version"]
                        in {"1.10.0", "1.12.0"}
                        else runtime_status_payload(self.server)
                    )
                    self._send_json(HTTPStatus.OK, status)
                    return
                runtime_state_match = re.fullmatch(
                    r"/api/runtime/states/([0-9a-f]{24})", path
                )
                if runtime_state_match is not None:
                    if self.server.context["config"]["schema_version"] in {
                        "1.10.0",
                        "1.12.0",
                    }:
                        raise DashboardRequestError(
                            HTTPStatus.GONE,
                            "기간 dashboard에서는 runtime state 조회 API를 제공하지 않습니다.",
                            reason_code="LEGACY_RUNTIME_ROUTE_REMOVED",
                        )
                    _require_runtime_enabled(self.server)
                    self._send_json(
                        HTTPStatus.OK,
                        runtime_state_payload(
                            self.server.context, runtime_state_match.group(1)
                        ),
                    )
                    return
                if path == "/api/metrics":
                    self._send_json(HTTPStatus.OK, metrics_payload(self.server.context))
                    return
                if path == "/api/checkpoints":
                    self._send_json(
                        HTTPStatus.OK, checkpoints_payload(self.server.context)
                    )
                    return
                if path == "/api/model-checks":
                    self._send_json(
                        HTTPStatus.OK, model_checks_payload(self.server.context)
                    )
                    return
                if path == "/api/dataset-splits":
                    self._send_json(
                        HTTPStatus.OK, dataset_splits_payload(self.server.context)
                    )
                    return
                sample_match = re.fullmatch(
                    r"/api/dataset-samples/([a-z0-9_]+)/([a-z0-9_]+)", path
                )
                if sample_match is not None:
                    with self.server.dataset_cache_lock:
                        result = dataset_samples_payload(
                            self.server.context,
                            sample_match.group(1),
                            sample_match.group(2),
                            self.server.dataset_cache,
                        )
                    self._send_json(HTTPStatus.OK, result)
                    return
                if path == "/api/sessions":
                    self._send_json(
                        HTTPStatus.OK, manual_sessions_payload(self.server.context)
                    )
                    return
                session_match = re.fullmatch(r"/api/sessions/([0-9a-f]{24})", path)
                if session_match is not None:
                    self._send_json(
                        HTTPStatus.OK,
                        manual_session_payload(
                            self.server.context, session_match.group(1)
                        ),
                    )
                    return
                raise DashboardRequestError(
                    HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다."
                )
            payload, content_type = self._static(path)
            self._send_bytes(HTTPStatus.OK, payload, content_type)
        except DashboardRequestError as exc:
            self._error(
                exc.status,
                str(exc),
                reason_code=exc.reason_code,
                retry_after=exc.retry_after,
            )
        except (OSError, Phase5DashboardError, subprocess.SubprocessError) as exc:
            self._internal_error(exc)

    def _request_json(self) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise DashboardRequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON 요청만 허용됩니다."
            )
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError as exc:
            raise DashboardRequestError(
                HTTPStatus.BAD_REQUEST, "Content-Length가 잘못됐습니다."
            ) from exc
        maximum = self.server.context["config"]["server"]["max_request_bytes"]
        if length < 2 or length > maximum:
            raise DashboardRequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "요청 크기가 허용 범위를 벗어납니다.",
            )
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DashboardRequestError(
                HTTPStatus.BAD_REQUEST, "JSON 요청이 잘못됐습니다."
            ) from exc
        if not isinstance(value, dict):
            raise DashboardRequestError(
                HTTPStatus.BAD_REQUEST, "JSON object만 허용됩니다."
            )
        return value

    def do_POST(self) -> None:
        try:
            path = self._guard(require_origin=True)
            if not self._require_basic_auth():
                return
            if not self._authorized():
                raise DashboardRequestError(
                    HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다."
                )
            random_sample_match = re.fullmatch(
                r"/api/dataset-samples/([a-z0-9_]+)/([a-z0-9_]+)/random", path
            )
            if random_sample_match is not None:
                payload = self._request_json()
                if payload:
                    raise DashboardRequestError(
                        HTTPStatus.BAD_REQUEST,
                        "무작위 dataset sample 요청은 빈 object여야 합니다.",
                    )
                with self.server.dataset_cache_lock:
                    result = dataset_samples_payload(
                        self.server.context,
                        random_sample_match.group(1),
                        random_sample_match.group(2),
                        self.server.dataset_cache,
                        randomize=True,
                    )
                self._send_json(HTTPStatus.OK, result)
                return
            is_chart_binding = self.server.context["config"]["schema_version"] in {
                "1.10.0",
                "1.12.0",
            }
            if is_chart_binding and path == "/api/runtime/sessions":
                payload = self._request_json()
                if payload:
                    raise DashboardRequestError(
                        HTTPStatus.BAD_REQUEST,
                        "runtime session 생성 요청은 빈 object여야 합니다.",
                        reason_code="RUNTIME_SESSION_REQUEST_INVALID",
                    )
                self._rate_limit("session_or_chart")
                binding = self._period_binding()
                result = self._binding_request(binding.create_session)
                self._send_json(
                    HTTPStatus.CREATED,
                    result,
                    reason_code="RUNTIME_SESSION_CREATED",
                )
                return
            event_match = (
                re.fullmatch(r"/api/runtime/sessions/([0-9a-f]{24})/events", path)
                if is_chart_binding
                else None
            )
            if event_match is not None:
                payload = self._request_json()
                if set(payload) != {"expected_revision", "event"} or not isinstance(
                    payload.get("event"), dict
                ):
                    raise DashboardRequestError(
                        HTTPStatus.BAD_REQUEST,
                        "runtime event는 expected_revision과 event만 허용합니다.",
                        reason_code="RUNTIME_EVENT_REQUEST_INVALID",
                    )
                self._rate_limit("runtime_event")
                if payload["event"].get("type") in {"request_chart", "request_period"}:
                    self._rate_limit("session_or_chart")
                binding = self._period_binding()
                result = self._binding_request(
                    binding.handle_event,
                    event_match.group(1),
                    expected_revision=payload["expected_revision"],
                    event=payload["event"],
                )
                reason = (
                    result.get("decision", {}).get("reason_code")
                    or "RUNTIME_EVENT_APPLIED"
                )
                self._send_json(HTTPStatus.OK, result, reason_code=str(reason))
                return
            if path in {"/api/runtime/chart", "/api/runtime/period"}:
                if is_chart_binding:
                    raise DashboardRequestError(
                        HTTPStatus.GONE,
                        "legacy runtime API는 기간 dashboard에서 제거됐습니다.",
                        reason_code="LEGACY_RUNTIME_ROUTE_REMOVED",
                    )
                payload = self._request_json()
                result = (
                    execute_runtime_chart(self.server, payload)
                    if path == "/api/runtime/chart"
                    else execute_runtime_period(self.server, payload)
                )
                self._send_json(HTTPStatus.OK, result)
                return
            if path not in {"/api/generate", "/api/probe"}:
                raise DashboardRequestError(
                    HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다."
                )
            payload = self._request_json()
            gate = _generation_gate(self.server.context)
            if not gate["allowed"]:
                raise DashboardRequestError(
                    HTTPStatus.CONFLICT,
                    "학습 중이거나 final 모델이 준비되지 않았습니다.",
                )
            if path == "/api/generate" and is_chart_binding:
                self._rate_limit("model_generation")
            if not self.server.generation_lock.acquire(blocking=False):
                raise DashboardRequestError(
                    HTTPStatus.TOO_MANY_REQUESTS
                    if is_chart_binding
                    else HTTPStatus.CONFLICT,
                    "다른 모델 생성이 실행 중입니다.",
                    reason_code="MODEL_GENERATION_BUSY",
                    retry_after=1 if is_chart_binding else None,
                )
            try:
                if path == "/api/generate":
                    if (
                        not {"prompt", "session_id"}.issubset(payload)
                        or not set(payload).issubset(
                            {
                                "prompt",
                                "session_id",
                                "profile",
                                "engine_selection",
                                "runtime_session_id",
                            }
                        )
                        or not isinstance(payload["prompt"], str)
                        or (
                            payload["session_id"] is not None
                            and not isinstance(payload["session_id"], str)
                        )
                        or (
                            payload.get("profile") is not None
                            and not isinstance(payload.get("profile"), str)
                        )
                        or (
                            payload.get("engine_selection") is not None
                            and not isinstance(payload.get("engine_selection"), str)
                        )
                        or (
                            payload.get("runtime_session_id") is not None
                            and not isinstance(payload.get("runtime_session_id"), str)
                        )
                    ):
                        raise DashboardRequestError(
                            HTTPStatus.BAD_REQUEST,
                            "prompt·profile·engine_selection 문자열과 session_id 문자열 또는 null만 허용됩니다.",
                        )
                    if is_chart_binding:
                        generation_runner = (
                            self.server.generation_runner
                            or _manual_generation_subprocess
                        )
                        runtime_binding = None
                        runtime_session_id = payload.get("runtime_session_id")
                        if runtime_session_id is not None:
                            binding = self._period_binding()
                            runtime_binding = self._binding_request(
                                binding.public_snapshot, runtime_session_id
                            )
                        elif payload["session_id"] is not None:
                            current = manual_session_payload(
                                self.server.context, payload["session_id"]
                            )
                            if current.get("runtime_binding_sha256") is not None:
                                if current.get("schema_version") == "1.4.0":
                                    raise DashboardRequestError(
                                        HTTPStatus.CONFLICT,
                                        "기존 v1.9 원국 대화는 읽기 전용입니다. 원국에서 새 연결 대화를 시작하세요.",
                                        reason_code="LEGACY_BOUND_SESSION_READ_ONLY",
                                    )
                                raise DashboardRequestError(
                                    HTTPStatus.CONFLICT,
                                    "기존 대화에 결합된 활성 runtime session이 필요합니다.",
                                    reason_code="RUNTIME_SESSION_REQUIRED",
                                )
                        result = generation_runner(
                            self.server.context,
                            payload["prompt"],
                            payload["session_id"],
                            payload.get("profile"),
                            payload.get("engine_selection"),
                            None,
                            runtime_binding,
                        )
                    else:
                        generation_runner = (
                            self.server.generation_runner
                            or _manual_generation_subprocess
                        )
                        bound_runtime_id = payload.get("runtime_session_id")
                        if (
                            bound_runtime_id is None
                            and payload["session_id"] is not None
                        ):
                            bound_runtime_id = manual_session_payload(
                                self.server.context, payload["session_id"]
                            ).get("runtime_session_id")
                        if (
                            bound_runtime_id is not None
                            and self.server.context.get("runtime_canary_active")
                            is not True
                        ):
                            raise DashboardRequestError(
                                HTTPStatus.CONFLICT,
                                "runtime canary가 비활성인 동안 계산 사실을 모델에 연결할 수 없습니다.",
                            )
                        result = generation_runner(
                            self.server.context,
                            payload["prompt"],
                            payload["session_id"],
                            payload.get("profile"),
                            payload.get("engine_selection"),
                            payload.get("runtime_session_id"),
                        )
                else:
                    if payload:
                        raise DashboardRequestError(
                            HTTPStatus.BAD_REQUEST,
                            "고정 probe 요청은 빈 object여야 합니다.",
                        )
                    output_root = (
                        self.server.context["run_root"]
                        / self.server.context["config"]["model_check"][
                            "private_output_relative"
                        ]
                    )
                    if output_root.exists():
                        raise DashboardRequestError(
                            HTTPStatus.CONFLICT, "고정 probe 결과가 이미 있습니다."
                        )
                    result = _fixed_probe_subprocess(self.server.context)
            finally:
                self.server.generation_lock.release()
            self._send_json(HTTPStatus.OK, result)
        except GroundingGateError:
            self._error(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "연결된 원국을 안전하게 사용한 답변을 만들지 못했습니다. 다시 시도해 주세요.",
                reason_code=GROUNDING_FAILURE_CODE,
            )
        except DashboardRequestError as exc:
            self._error(
                exc.status,
                str(exc),
                reason_code=exc.reason_code,
                retry_after=exc.retry_after,
            )
        except (OSError, Phase5DashboardError, subprocess.SubprocessError) as exc:
            self._internal_error(exc)

    def do_DELETE(self) -> None:
        try:
            path = self._guard(require_origin=True)
            if not self._require_basic_auth():
                return
            if not self._authorized():
                raise DashboardRequestError(
                    HTTPStatus.FORBIDDEN,
                    "CSRF 검증에 실패했습니다.",
                    reason_code="CSRF_REJECTED",
                )
            if self.server.context["config"]["schema_version"] not in {
                "1.10.0",
                "1.12.0",
            }:
                raise DashboardRequestError(
                    HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다."
                )
            match = re.fullmatch(r"/api/runtime/sessions/([0-9a-f]{24})", path)
            if match is None:
                raise DashboardRequestError(
                    HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다."
                )
            self._rate_limit("runtime_event")
            binding = self._period_binding()
            result = self._binding_request(binding.delete_session, match.group(1))
            self._send_json(
                HTTPStatus.OK, result, reason_code="RUNTIME_SESSION_DELETED"
            )
        except DashboardRequestError as exc:
            self._error(
                exc.status,
                str(exc),
                reason_code=exc.reason_code,
                retry_after=exc.retry_after,
            )
        except (OSError, Phase5DashboardError, subprocess.SubprocessError) as exc:
            self._internal_error(exc)


def _manual_generation_subprocess(
    context: dict[str, Any],
    prompt: str,
    session_id: str | None,
    profile: str | None,
    engine_selection: str | None,
    runtime_session_id: str | None = None,
    runtime_binding: dict[str, Any] | None = None,
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
    if session_id is not None:
        current = manual_session_payload(context, session_id)
        selected = _session_inference_selection(context, current)
        effective_runtime_session_id = runtime_session_id or current.get(
            "runtime_session_id"
        )
    else:
        selected = _inference_selection(
            context,
            engine_selection or context["inference_engines"]["default_selection"],
        )
        effective_runtime_session_id = runtime_session_id
    if (
        effective_runtime_session_id is not None
        and context.get("runtime_canary_active") is not True
    ):
        raise Phase5DashboardError(
            "runtime canary가 비활성인 동안 계산 사실을 모델에 연결할 수 없습니다."
        )
    if context.get("runtime_canary_active") is True:
        command.append("--enable-runtime-canary")
    if runtime_binding is not None:
        if (
            context["config"]["schema_version"] != "1.12.0"
            or context.get("period_runtime_active") is not True
            or runtime_session_id is not None
        ):
            raise Phase5DashboardError(
                "활성 dashboard v1.12에서만 기간 model binding을 전달할 수 있습니다."
            )
        _runtime_model_context_from_binding(runtime_binding)
        command.append("--enable-period-runtime-binding")
    timeout = (
        context["inference_engines"]["paired_timeout_seconds"]
        if selected["mode"] == "paired"
        else context["inference_engines"]["single_timeout_seconds"]
    )
    result = subprocess.run(
        command,
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": session_id,
                "profile": profile,
                "engine_selection": engine_selection,
                "runtime_session_id": runtime_session_id,
                "runtime_binding": runtime_binding,
            },
            ensure_ascii=False,
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=context["repo_root"],
    )
    if result.returncode != 0:
        if GROUNDING_FAILURE_CODE in result.stderr:
            raise DashboardRequestError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "연결된 원국을 안전하게 사용한 답변을 만들지 못했습니다. 다시 시도해 주세요.",
                reason_code=GROUNDING_FAILURE_CODE,
            )
        if "LEGACY_BOUND_SESSION_READ_ONLY" in result.stderr:
            raise DashboardRequestError(
                HTTPStatus.CONFLICT,
                "기존 원국 대화는 읽기 전용입니다. 원국에서 새 연결 대화를 시작하세요.",
                reason_code="LEGACY_BOUND_SESSION_READ_ONLY",
            )
        raise Phase5DashboardError(
            "수동 모델 생성이 실패했습니다: " + result.stderr[-500:]
        )
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Phase5DashboardError("수동 모델 생성 결과가 JSON이 아닙니다.") from exc
    if (
        not isinstance(value, dict)
        or value.get("persisted") is not True
        or value.get("local_only") is not True
        or SESSION_ID_PATTERN.fullmatch(str(value.get("session_id", ""))) is None
        or (runtime_binding is not None)
        != (value.get("runtime_binding_applied") is True)
        or (
            runtime_binding is not None
            and value.get("runtime_snapshot_sha256")
            != runtime_binding["snapshot_sha256"]
        )
    ):
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
        raise Phase5DashboardError(
            "고정 20건 모델 검사 결과가 JSON이 아닙니다."
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("status") != "diagnostic_complete"
        or value.get("probe_count") != 20
        or value.get("production_promotion_allowed") is not False
    ):
        raise Phase5DashboardError("고정 20건 모델 검사 결과 계약이 다릅니다.")
    return value


def serve(
    context: dict[str, Any],
    host: str,
    port: int,
    trusted_origin: str | None = None,
    basic_auth_user: str | None = None,
    basic_auth_password_file: Path | None = None,
    allow_unauthenticated_remote: bool = False,
    enable_runtime_canary: bool = False,
    allow_unauthenticated_runtime_canary: bool = False,
    enable_period_runtime: bool = False,
    runtime_ephemeris: Path | None = None,
    runtime_hmac_key_file: Path | None = None,
    runtime_encryption_key_file: Path | None = None,
    runtime_previous_encryption_key_file: Path | None = None,
    runtime_store_root: Path | None = None,
    runtime_process_lease_file: Path | None = None,
) -> None:
    if host != "127.0.0.1" or not 1 <= port <= 65535:
        raise Phase5DashboardError(
            "대시보드는 127.0.0.1의 유효한 port에만 열 수 있습니다."
        )
    resolved_origin, basic_auth = _remote_access_settings(
        trusted_origin,
        basic_auth_user,
        basic_auth_password_file,
        allow_unauthenticated_remote,
    )
    schema_version = context["config"]["schema_version"]
    if (
        resolved_origin is not None
        and basic_auth is None
        and schema_version
        not in {
            "1.7.0",
            "1.8.0",
            "1.9.0",
            "1.10.0",
            "1.12.0",
        }
    ):
        raise Phase5DashboardError(
            "무인증 원격 공유에는 dashboard v1.7.0+ 계약이 필요합니다."
        )
    if (
        resolved_origin is not None
        and basic_auth is not None
        and schema_version
        not in {
            "1.6.0",
            "1.7.0",
            "1.8.0",
            "1.9.0",
            "1.10.0",
            "1.12.0",
        }
    ):
        raise Phase5DashboardError(
            "인증 원격 공유에는 dashboard v1.6.0+ 계약이 필요합니다."
        )
    if enable_runtime_canary and schema_version != "1.8.0":
        raise Phase5DashboardError(
            "runtime canary에는 dashboard v1.8.0 계약이 필요합니다."
        )
    if enable_period_runtime and schema_version != "1.12.0":
        raise Phase5DashboardError(
            "기간 production binding에는 dashboard v1.12.0 계약이 필요합니다."
        )
    period_resources = (
        runtime_ephemeris,
        runtime_hmac_key_file,
        runtime_encryption_key_file,
        runtime_store_root,
        runtime_process_lease_file,
    )
    if enable_period_runtime and any(item is None for item in period_resources):
        raise Phase5DashboardError(
            "활성 제한 runtime에는 ephemeris·분리 key·store·lease가 필요합니다."
        )
    if not enable_period_runtime and any(
        item is not None
        for item in (
            *period_resources,
            runtime_previous_encryption_key_file,
        )
    ):
        raise Phase5DashboardError(
            "비활성 제한 runtime에는 운영 resource를 전달할 수 없습니다."
        )
    remote_unauthenticated = resolved_origin is not None and basic_auth is None
    if allow_unauthenticated_runtime_canary and (
        not enable_runtime_canary or not remote_unauthenticated
    ):
        raise Phase5DashboardError(
            "무인증 runtime 확인 flag는 무인증 원격 runtime canary와만 사용할 수 있습니다."
        )
    if (
        enable_runtime_canary
        and remote_unauthenticated
        and not allow_unauthenticated_runtime_canary
    ):
        raise Phase5DashboardError(
            "무인증 원격 runtime canary에는 별도 위험 확인 flag가 필요합니다."
        )
    release_available = isinstance(
        (context.get("runtime_canary") or {}).get("release"), dict
    )
    context["runtime_canary_active"] = bool(enable_runtime_canary and release_available)
    period_binding = None
    if enable_period_runtime:
        prepared = context.get("period_runtime")
        if (
            not isinstance(prepared, dict)
            or not isinstance(prepared.get("parent_release"), dict)
            or not isinstance(prepared.get("period_release"), dict)
        ):
            raise Phase5DashboardError(
                "승인된 원국·기간 release 없이 runtime을 활성화할 수 없습니다."
            )
        from scripts.runtime.period_dashboard_binding import PeriodDashboardBinding

        assert runtime_ephemeris is not None
        assert runtime_hmac_key_file is not None
        assert runtime_encryption_key_file is not None
        assert runtime_store_root is not None
        assert runtime_process_lease_file is not None
        period_binding = PeriodDashboardBinding(
            parent_release_registry=prepared["parent_release_path"],
            period_release_registry=prepared["period_release_path"],
            ephemeris_path=runtime_ephemeris,
            hmac_key_file=runtime_hmac_key_file,
            encryption_key_file=runtime_encryption_key_file,
            previous_encryption_key_file=runtime_previous_encryption_key_file,
            store_root=runtime_store_root,
            process_lease_file=runtime_process_lease_file,
        )
        context["period_runtime_active"] = True
    asset_root = (
        context["period_runtime"]["asset_root"]
        if schema_version in {"1.10.0", "1.12.0"}
        else ASSET_ROOT
    )
    server = DashboardHTTPServer(
        (host, port),
        context,
        asset_root,
        secrets.token_hex(24),
        resolved_origin,
        basic_auth,
        enable_runtime_canary,
        allow_unauthenticated_runtime_canary,
        period_binding,
        enable_period_runtime,
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
    parser.add_argument("--run-root", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="현재 상태 JSON 출력")
    status.set_defaults(execute=False)
    serve_parser = subparsers.add_parser("serve", help="loopback dashboard 실행")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--trusted-origin")
    serve_parser.add_argument("--basic-auth-user")
    serve_parser.add_argument("--basic-auth-password-file", type=Path)
    serve_parser.add_argument("--allow-unauthenticated-remote", action="store_true")
    serve_parser.add_argument("--enable-runtime-canary", action="store_true")
    serve_parser.add_argument(
        "--allow-unauthenticated-runtime-canary", action="store_true"
    )
    serve_parser.add_argument("--enable-period-runtime", action="store_true")
    serve_parser.add_argument("--runtime-ephemeris", type=Path)
    serve_parser.add_argument("--runtime-hmac-key-file", type=Path)
    serve_parser.add_argument("--runtime-encryption-key-file", type=Path)
    serve_parser.add_argument("--runtime-previous-encryption-key-file", type=Path)
    serve_parser.add_argument("--runtime-store-root", type=Path)
    serve_parser.add_argument("--runtime-process-lease-file", type=Path)
    probe = subparsers.add_parser("probe", help="완료 모델 고정 20건 비교")
    probe.add_argument("--execute", action="store_true")
    generate = subparsers.add_parser("generate", help=argparse.SUPPRESS)
    generate.add_argument("--execute", action="store_true")
    generate.add_argument("--enable-runtime-canary", action="store_true")
    generate.add_argument("--enable-period-runtime-binding", action="store_true")
    candidate = subparsers.add_parser(
        "serve-candidate",
        help="기존 화면과 분리된 과거 공식 근거 후보 dashboard 실행",
    )
    candidate.add_argument("--host", default="127.0.0.1")
    candidate.add_argument("--port", type=int, default=8766)
    candidate.add_argument("--ephemeris", type=Path, required=True)
    candidate.add_argument("--id-key-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve-candidate":
        from scripts.runtime.calculation.errors import RuntimeCalculationError
        from scripts.training.historical_candidate_dashboard import (
            CandidateDashboardError,
            serve_candidate,
        )

        try:
            serve_candidate(
                ephemeris_path=args.ephemeris,
                id_key_file=args.id_key_file,
                host=args.host,
                port=args.port,
            )
        except (OSError, RuntimeCalculationError, CandidateDashboardError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.run_root is None:
        print("ERROR: 이 command에는 --run-root가 필요합니다.", file=sys.stderr)
        return 1
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    run_root = (
        args.run_root if args.run_root.is_absolute() else REPO_ROOT / args.run_root
    )
    try:
        context = prepare_context(REPO_ROOT, config_path, run_root)
        if args.command == "status":
            result = status_payload(context)
        elif args.command == "serve":
            try:
                serve(
                    context,
                    args.host,
                    args.port,
                    args.trusted_origin,
                    args.basic_auth_user,
                    args.basic_auth_password_file,
                    args.allow_unauthenticated_remote,
                    args.enable_runtime_canary,
                    args.allow_unauthenticated_runtime_canary,
                    args.enable_period_runtime,
                    args.runtime_ephemeris,
                    args.runtime_hmac_key_file,
                    args.runtime_encryption_key_file,
                    args.runtime_previous_encryption_key_file,
                    args.runtime_store_root,
                    args.runtime_process_lease_file,
                )
            except KeyboardInterrupt:
                pass
            return 0
        elif args.command == "probe":
            if not args.execute:
                result = {
                    "status": "dry_run",
                    "generation_gate": _generation_gate(context),
                    "writes_performed": False,
                }
            else:
                result = execute_fixed_probe(context)
        elif args.command == "generate":
            if not args.execute:
                raise Phase5DashboardError(
                    "수동 generation에는 --execute가 필요합니다."
                )
            if args.enable_runtime_canary:
                release_available = isinstance(
                    (context.get("runtime_canary") or {}).get("release"), dict
                )
                if not release_available:
                    raise Phase5DashboardError(
                        "유효한 runtime release 없이 canary를 활성화할 수 없습니다."
                    )
                context["runtime_canary_active"] = True
            if args.enable_period_runtime_binding:
                prepared = context.get("period_runtime")
                if (
                    context["config"]["schema_version"] != "1.12.0"
                    or not isinstance(prepared, dict)
                    or not isinstance(prepared.get("parent_release"), dict)
                    or not isinstance(prepared.get("period_release"), dict)
                ):
                    raise Phase5DashboardError(
                        "유효한 dashboard v1.12 release 없이 model binding을 활성화할 수 없습니다."
                    )
                context["period_runtime_active"] = True
            payload = json.loads(sys.stdin.read())
            if (
                not isinstance(payload, dict)
                or not {"prompt", "session_id"}.issubset(payload)
                or not set(payload).issubset(
                    {
                        "prompt",
                        "session_id",
                        "profile",
                        "engine_selection",
                        "runtime_session_id",
                        "runtime_binding",
                    }
                )
                or not isinstance(payload["prompt"], str)
                or (
                    payload["session_id"] is not None
                    and not isinstance(payload["session_id"], str)
                )
                or (
                    payload.get("profile") is not None
                    and not isinstance(payload.get("profile"), str)
                )
                or (
                    payload.get("engine_selection") is not None
                    and not isinstance(payload.get("engine_selection"), str)
                )
                or (
                    payload.get("runtime_session_id") is not None
                    and not isinstance(payload.get("runtime_session_id"), str)
                )
                or (
                    payload.get("runtime_binding") is not None
                    and not isinstance(payload.get("runtime_binding"), dict)
                )
                or (
                    (payload.get("runtime_binding") is not None)
                    != bool(args.enable_period_runtime_binding)
                )
            ):
                raise Phase5DashboardError("수동 generation stdin 계약이 다릅니다.")
            result = execute_manual_generation(
                context,
                payload["prompt"],
                payload["session_id"],
                payload.get("profile"),
                payload.get("engine_selection"),
                payload.get("runtime_session_id"),
                payload.get("runtime_binding"),
            )
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
    print(
        json.dumps(
            result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
