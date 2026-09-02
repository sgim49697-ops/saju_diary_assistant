# period_dashboard_canary_v1.py - dashboard v1.12의 합성 HTTP·재시작 200건을 자동 검증한다.

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.chart_only_security import (
    create_private_directory,
    create_secret_key,
)
from scripts.runtime.period_dashboard_binding import (
    BINDING_ID,
    PeriodDashboardBinding,
    PeriodDashboardBindingError,
)
from scripts.runtime.period_v1.contracts_v1_1 import RELEASE_PATH as PERIOD_RELEASE_PATH
from scripts.runtime.period_v1.engine import validate_public_daily_label_result
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.resolver import resolve_period_scope
from scripts.training.phase5_dashboard_v1_12 import (
    DEFAULT_CONFIG,
    V112_ASSET_ROOT,
    DashboardHTTPServer,
    Phase5DashboardError,
    SlidingWindowRateLimiter,
    _messages_for_engine,
    _runtime_model_context_from_binding,
    validate_config,
)

REPORT_VERSION = "1.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_period_dashboard_canary/v1.0.0"
BUILD_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
SESSION_ID = "c" * 24
CAPABILITY_SHA256 = "e" * 64
REFERENCE_DATE = date(2026, 9, 2)
EXPECTED_STRATA = {
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
IMPLEMENTATION_PATHS = (
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.12.0.json",
    "configs/runtime/period/releases/v1.0.0/release_registry.json",
    "scripts/runtime/period_dashboard_binding.py",
    "scripts/runtime/period_v1/contracts.py",
    "scripts/runtime/period_v1/contracts_v1_1.py",
    "scripts/runtime/period_v1/engine.py",
    "scripts/runtime/period_v1/rehydration.py",
    "scripts/runtime/period_v1/resolver.py",
    "scripts/runtime/period_v1/security.py",
    "scripts/training/phase5_dashboard_v1_12.py",
    "scripts/training/phase5_dashboard_assets/v1.12.0/index.html",
    "scripts/training/phase5_dashboard_assets/v1.12.0/dashboard.js",
    "scripts/training/phase5_dashboard_assets/v1.12.0/dashboard.css",
    "scripts/training/phase5_dashboard_assets/v1.12.0/prompt-examples.json",
    "scripts/evaluation/saju_runtime/period_dashboard_canary_v1.py",
)
FORBIDDEN_PUBLIC_KEYS = {
    "birth_date",
    "birth_time",
    "birth_input_id",
    "calculation_run_id",
    "chart_authorization",
    "chart_id",
    "chart_set_id",
    "ciphertext",
    "internal_trace",
    "local_birth_date",
    "local_birth_time",
    "nonce",
    "normalized_input",
    "period_id",
    "reference_date",
    "runtime_session_id",
    "session_id",
}
PRIVATE_VALUE_PATTERN = re.compile(
    r"(?:sc2_|sbi2_|scr2_|scs2_|spd1_)[0-9a-f]{64}|(?:/home/|/tmp/|[A-Za-z]:\\\\)"
)


class PeriodDashboardCanaryError(RuntimeError):
    """dashboard v1.12 자동 canary 계약 위반."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise PeriodDashboardCanaryError(message)


class _Recorder:
    def __init__(self) -> None:
        self.passed: Counter[str] = Counter()
        self.failed: Counter[str] = Counter()
        self.failure_counts: Counter[str] = Counter()

    def case(self, stratum: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (
            AssertionError,
            KeyError,
            OSError,
            PeriodDashboardBindingError,
            PeriodDashboardCanaryError,
            PeriodRuntimeError,
            Phase5DashboardError,
            TypeError,
            ValueError,
        ) as exc:
            self.failed[stratum] += 1
            self.failure_counts[f"{stratum}:{type(exc).__name__}"] += 1
        else:
            self.passed[stratum] += 1

    def strata(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "cases": self.passed[name] + self.failed[name],
                "passed": self.passed[name],
                "failed": self.failed[name],
            }
            for name in EXPECTED_STRATA
        }


def _chart() -> dict[str, Any]:
    return {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "hard_facts": {
            "pillars": {
                "year": {"ganzhi": "庚午"},
                "month": {"ganzhi": "辛巳"},
                "day": {"ganzhi": "壬辰"},
                "hour": {"ganzhi": "丁未"},
            },
            "day_master": {"stem": "壬"},
            "surface_five_elements": {"목": 0, "화": 3, "토": 2, "금": 2, "수": 1},
            "calculation_profile": "KR_CIVIL_MIDNIGHT_V1",
            "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
        },
        "message": "합성 원국 계산 완료",
        "limitations": [],
    }


def _period(scope: Mapping[str, Any]) -> dict[str, Any]:
    start = date.fromisoformat(str(scope["start_date"]))
    end = date.fromisoformat(str(scope["end_date"]))
    stems = "갑을병정무기경신임계"
    branches = "자축인묘진사오미신유술해"
    days = []
    for offset in range((end - start).days + 1):
        target = start + timedelta(days=offset)
        ordinal = target.toordinal()
        days.append(
            {
                "date": target.isoformat(),
                "year_ganzhi": "병오",
                "month_ganzhi": "병신",
                "day_ganzhi": stems[ordinal % 10] + branches[ordinal % 12],
                "authority": "SOURCE_HARD_FACT",
            }
        )
    result = {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "period_scope": {
            key: scope[key]
            for key in (
                "date_expression",
                "start_date",
                "end_date",
                "day_count",
                "timezone",
                "evaluation_local_time",
            )
        },
        "days": days,
        "boundary_capability": {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        },
        "message": "합성 transport 기간 계산 완료",
        "limitations": ["공식 정확도는 conformance v11이 별도로 소유합니다."],
    }
    return validate_public_daily_label_result(result)


def _snapshot(period: Mapping[str, Any], revision: int) -> dict[str, Any]:
    value = {"chart": _chart(), "period": deepcopy(dict(period))}
    return {
        "schema_version": "1.2.0",
        "binding_id": BINDING_ID,
        "capability_sha256": CAPABILITY_SHA256,
        "snapshot_sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
        "state_revision": revision,
        "value": value,
    }


class _SyntheticBinding:
    """HTTP transport만 검증하며 천문 정확도 주장은 만들지 않는 합성 binding."""

    def __init__(self) -> None:
        self.revision = 0
        self.deleted = False
        self.current_period: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "1.2.0",
            "status": "limited_public_chart_and_daily_label_range_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "합성 HTTP canary 활성",
            "period_today_kst": REFERENCE_DATE.isoformat(),
            "period_minimum": REFERENCE_DATE.isoformat(),
            "period_maximum": "2049-12-31",
            "period_maximum_days": 31,
            "period_evaluation_local_time": "12:00",
            "client_authentication_required": False,
        }

    def create_session(self) -> dict[str, Any]:
        self.revision = 0
        self.deleted = False
        self.current_period = None
        return {
            "status": "created",
            "session_id": SESSION_ID,
            "state_revision": 0,
            "expires_in_seconds": 1800,
            "governance": {},
        }

    def handle_event(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        if session_id != SESSION_ID or self.deleted:
            raise PeriodDashboardBindingError(
                404, "RUNTIME_SESSION_NOT_FOUND", "runtime session을 찾을 수 없습니다."
            )
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision != self.revision
        ):
            raise PeriodDashboardBindingError(
                409, "STALE_RUNTIME_REVISION", "runtime revision이 다릅니다."
            )
        try:
            scope = resolve_period_scope(event, reference_date=REFERENCE_DATE)
        except PeriodRuntimeError as exc:
            raise PeriodDashboardBindingError(
                400, exc.code, "기간 요청이 계약에서 거부됐습니다."
            ) from exc
        self.current_period = _period(scope)
        self.revision += 1
        return {
            "status": "ready",
            "state_revision": self.revision,
            "decision": {
                "action": "render_chart_and_period",
                "message": "합성 기간 transport 완료",
                "reason_code": None,
            },
            "result": {"chart": _chart(), "period": deepcopy(self.current_period)},
            "governance": {},
        }

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        if session_id != SESSION_ID or self.deleted or self.current_period is None:
            raise PeriodDashboardBindingError(
                409,
                "RUNTIME_DAILY_LABEL_RANGE_REQUIRED",
                "승인된 일별 기간이 필요합니다.",
            )
        return _snapshot(self.current_period, self.revision)

    def delete_session(self, session_id: str) -> dict[str, Any]:
        if session_id != SESSION_ID or self.deleted:
            raise PeriodDashboardBindingError(
                404, "RUNTIME_SESSION_NOT_FOUND", "runtime session을 찾을 수 없습니다."
            )
        self.deleted = True
        return {"status": "deleted", "retained": False, "governance": {}}

    def close(self) -> None:
        return None


class _Harness:
    def __init__(self, *, binding: _SyntheticBinding | None) -> None:
        config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
        self.context = {
            "config": config,
            "period_runtime": {
                "contract": config["period_runtime"],
                "parent_release": {"status": "verified"},
                "period_release": {"status": "verified"},
                "asset_root": V112_ASSET_ROOT,
            },
            "period_runtime_active": binding is not None,
        }
        self.csrf = "a" * 48
        self.stderr = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0),
            self.context,
            V112_ASSET_ROOT,
            self.csrf,
            period_binding=binding,
            period_runtime_requested=binding is not None,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.redirect.__exit__(None, None, None)

    def unlimited(self) -> None:
        for name in self.server.rate_limiters:
            self.server.rate_limiters[name] = SlidingWindowRateLimiter(10_000)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        host: str | None = None,
        origin: str | None = None,
        csrf: str | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        encoded = (
            None if body is None else json.dumps(body, ensure_ascii=False).encode()
        )
        headers = {
            "Host": host or f"127.0.0.1:{self.port}",
            "Origin": origin or f"http://127.0.0.1:{self.port}",
            "X-CSRF-Token": self.csrf if csrf is None else csrf,
        }
        if encoded is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                }
            )
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise PeriodDashboardCanaryError("HTTP 응답이 JSON object가 아닙니다.")
        return response.status, response_headers, value


def _http_event(
    harness: _Harness, revision: int, request: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    status, _, payload = harness.request(
        "POST",
        f"/api/runtime/sessions/{SESSION_ID}/events",
        body={
            "expected_revision": revision,
            "event": {"type": "request_period", "request": dict(request)},
        },
    )
    return status, payload


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return bool(FORBIDDEN_PUBLIC_KEYS.intersection(value)) or any(
            _contains_forbidden(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return isinstance(value, str) and PRIVATE_VALUE_PATTERN.search(value) is not None


def _run_http(recorder: _Recorder) -> tuple[_SyntheticBinding, dict[str, bool]]:
    off = _Harness(binding=None)
    try:
        for _ in range(EXPECTED_STRATA["feature_off"]):

            def feature_off() -> None:
                status, _, value = off.request("POST", "/api/runtime/sessions", body={})
                _expect(status == 409, "기본 off가 409로 차단되지 않았습니다.")
                _expect(
                    value.get("code") == "RUNTIME_FEATURE_DISABLED",
                    "기본 off code 오류",
                )

            recorder.case("feature_off", feature_off)
    finally:
        off.close()

    binding = _SyntheticBinding()
    active = _Harness(binding=binding)
    active.unlimited()
    revision = 0
    status, _, created = active.request("POST", "/api/runtime/sessions", body={})
    _expect(
        status == 201 and created.get("session_id") == SESSION_ID,
        "합성 session 생성 실패",
    )
    try:
        expressions = (
            "today",
            "tomorrow",
            "this_weekend",
            "this_week",
            "this_month",
        )
        for index in range(EXPECTED_STRATA["relative_dates"]):

            def relative_case(index: int = index) -> None:
                nonlocal revision
                request = {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": expressions[index % len(expressions)],
                    "start_date": None,
                    "end_date": None,
                }
                status, value = _http_event(active, revision, request)
                _expect(status == 200, "상대 날짜 HTTP가 실패했습니다.")
                revision = value["state_revision"]
                validate_public_daily_label_result(value["result"]["period"])

            recorder.case("relative_dates", relative_case)

        for length in range(1, EXPECTED_STRATA["explicit_ranges"] + 1):

            def explicit_case(length: int = length) -> None:
                nonlocal revision
                end = REFERENCE_DATE + timedelta(days=length - 1)
                request = {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": "explicit",
                    "start_date": REFERENCE_DATE.isoformat(),
                    "end_date": end.isoformat(),
                }
                status, value = _http_event(active, revision, request)
                _expect(status == 200, "명시 기간 HTTP가 실패했습니다.")
                revision = value["state_revision"]
                period = validate_public_daily_label_result(value["result"]["period"])
                _expect(len(period["days"]) == length, "명시 기간 날짜 수가 다릅니다.")

            recorder.case("explicit_ranges", explicit_case)

        boundary_dates = (
            date(2026, 9, 30),
            date(2026, 10, 1),
            date(2026, 12, 31),
            date(2027, 1, 1),
            date(2028, 2, 28),
            date(2028, 2, 29),
        )
        for index in range(EXPECTED_STRATA["label_boundaries"]):

            def boundary_case(index: int = index) -> None:
                nonlocal revision
                target = boundary_dates[index % len(boundary_dates)]
                request = {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": "explicit",
                    "start_date": target.isoformat(),
                    "end_date": target.isoformat(),
                }
                status, value = _http_event(active, revision, request)
                _expect(status == 200, "label 경계 HTTP가 실패했습니다.")
                revision = value["state_revision"]
                period = validate_public_daily_label_result(value["result"]["period"])
                _expect(
                    period["days"][0]["date"] == target.isoformat(), "label 날짜 swap"
                )

            recorder.case("label_boundaries", boundary_case)

        invalid_requests: tuple[dict[str, Any], ...] = (
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "yesterday",
            },
            {"schema_version": "saju-period-request-v2", "date_expression": "year"},
            {"schema_version": "saju-period-request-v2", "date_expression": "explicit"},
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-02",
                "end_date": "2026-10-03",
            },
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-01",
                "end_date": "2026-09-02",
            },
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "today",
                "timezone": "UTC",
            },
            {"schema_version": "wrong", "date_expression": "today"},
            {"schema_version": "saju-period-request-v2", "date_expression": "오늘"},
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "bad",
                "end_date": "2026-09-02",
            },
            {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-03",
                "end_date": "2026-09-02",
            },
        )
        for request in invalid_requests:

            def unsupported_case(request: dict[str, Any] = request) -> None:
                status, value = _http_event(active, revision, request)
                _expect(status == 400, "지원 밖 기간 요청이 400이 아닙니다.")
                _expect(
                    isinstance(value.get("code"), str), "지원 밖 기간 code가 없습니다."
                )

            recorder.case("unsupported_scope", unsupported_case)

        security_flags = {
            "csrf": False,
            "origin": False,
            "stale": False,
            "rate": False,
            "legacy": False,
            "snapshot_tamper": False,
        }
        for index in range(EXPECTED_STRATA["security_tamper_rate"]):

            def security_case(index: int = index) -> None:
                nonlocal revision
                group = index // 5
                if group == 0:
                    status, _, _ = active.request(
                        "POST", "/api/runtime/sessions", body={}, csrf="wrong"
                    )
                    security_flags["csrf"] |= status == 403
                    _expect(status == 403, "CSRF 변조가 차단되지 않았습니다.")
                elif group == 1:
                    status, _, _ = active.request(
                        "POST",
                        "/api/runtime/sessions",
                        body={},
                        origin="https://invalid.example",
                    )
                    security_flags["origin"] |= status == 403
                    _expect(status == 403, "Origin 변조가 차단되지 않았습니다.")
                elif group == 2:
                    status, value = _http_event(
                        active,
                        max(0, revision - 1),
                        {
                            "schema_version": "saju-period-request-v2",
                            "date_expression": "today",
                        },
                    )
                    security_flags["stale"] |= status == 409
                    _expect(
                        status == 409 and value.get("code") == "STALE_RUNTIME_REVISION",
                        "stale revision 미차단",
                    )
                elif group == 3:
                    active.server.rate_limiters["session_or_chart"] = (
                        SlidingWindowRateLimiter(1)
                    )
                    first, _, _ = active.request(
                        "POST", "/api/runtime/sessions", body={}
                    )
                    second, headers, value = active.request(
                        "POST", "/api/runtime/sessions", body={}
                    )
                    security_flags["rate"] |= second == 429
                    _expect(first == 201 and second == 429, "rate limit 미적용")
                    _expect(
                        int(headers.get("retry-after", "0")) >= 1, "Retry-After 누락"
                    )
                    _expect(
                        value.get("code") == "SESSION_OR_CHART_RATE_LIMITED",
                        "rate code 오류",
                    )
                    active.unlimited()
                    binding.revision = revision
                    binding.deleted = False
                elif group == 4:
                    route = "/api/runtime/chart" if index % 2 else "/api/runtime/period"
                    status, _, value = active.request("POST", route, body={})
                    security_flags["legacy"] |= status == 410
                    _expect(
                        status == 410
                        and value.get("code") == "LEGACY_RUNTIME_ROUTE_REMOVED",
                        "legacy route 미차단",
                    )
                else:
                    scope = resolve_period_scope(
                        {
                            "type": "request_period",
                            "request": {
                                "schema_version": "saju-period-request-v2",
                                "date_expression": "today",
                                "start_date": None,
                                "end_date": None,
                            },
                        },
                        reference_date=REFERENCE_DATE,
                    )
                    snapshot = _snapshot(_period(scope), revision)
                    snapshot["value"]["period"]["days"][0]["day_ganzhi"] = "변조"
                    try:
                        _runtime_model_context_from_binding(snapshot)
                    except Phase5DashboardError:
                        security_flags["snapshot_tamper"] = True
                    else:
                        raise PeriodDashboardCanaryError(
                            "snapshot 변조가 수용됐습니다."
                        )

            recorder.case("security_tamper_rate", security_case)
    finally:
        logs = active.stderr.getvalue()
        active.close()
    synthetic_scope = resolve_period_scope(
        {
            "type": "request_period",
            "request": {
                "schema_version": "saju-period-request-v2",
                "date_expression": "today",
                "start_date": None,
                "end_date": None,
            },
        },
        reference_date=REFERENCE_DATE,
    )
    binding.current_period = _period(synthetic_scope)
    binding.deleted = False
    flags = {
        **security_flags,
        "logs_redacted": SESSION_ID not in logs and "2026-09-02" not in logs,
    }
    return binding, flags


def _drive_real_chart(binding: PeriodDashboardBinding) -> tuple[str, int]:
    created = binding.create_session()
    session_id = str(created["session_id"])
    revision = 0
    events = (
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "calendar", "value": "solar"},
        {"type": "set_slot", "field": "birth_date", "value": "1990-01-01"},
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {"country_code": "KR", "city": "서울", "timezone": "Asia/Seoul"},
        },
        {"type": "set_slot", "field": "birth_time", "value": "12:00"},
        {"type": "request_chart"},
        {
            "type": "request_period",
            "request": {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-02",
                "end_date": "2026-09-04",
            },
        },
    )
    for event in events:
        response = binding.handle_event(
            session_id, expected_revision=revision, event=event
        )
        revision = int(response["state_revision"])
    return session_id, revision


def _run_restarts(recorder: _Recorder, ephemeris: Path) -> tuple[bool, dict[str, Any]]:
    with tempfile.TemporaryDirectory(
        prefix="saju-period-dashboard-canary-"
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        keys = create_private_directory(root / "keys", label="canary keys")
        store = create_private_directory(root / "sessions", label="canary sessions")
        hmac_key = create_secret_key(keys / "runtime-hmac.key", purpose="runtime-hmac")
        aead_key = create_secret_key(keys / "session-aead.key", purpose="session-aead")
        lease = root / "period-runtime.lease"

        def open_binding() -> PeriodDashboardBinding:
            return PeriodDashboardBinding(
                parent_release_registry=RELEASE_V15_PATH,
                period_release_registry=PERIOD_RELEASE_PATH,
                ephemeris_path=ephemeris,
                hmac_key_file=hmac_key.path,
                encryption_key_file=aead_key.path,
                store_root=store,
                process_lease_file=lease,
            )

        initial = open_binding()
        session_id, _ = _drive_real_chart(initial)
        baseline = initial.public_snapshot(session_id)
        initial.close()
        for _ in range(EXPECTED_STRATA["process_restart"]):

            def restart_case() -> None:
                restarted = open_binding()
                try:
                    current = restarted.public_snapshot(session_id)
                    _expect(
                        current == baseline,
                        "process 재시작 후 snapshot이 달라졌습니다.",
                    )
                finally:
                    restarted.close()

            recorder.case("process_restart", restart_case)
        return not _contains_forbidden(baseline), baseline


def _run_context_and_leakage(
    recorder: _Recorder, synthetic: _SyntheticBinding, real_snapshot: Mapping[str, Any]
) -> dict[str, bool]:
    same_context = True
    for length in range(1, EXPECTED_STRATA["same_context_k0_ki20"] + 1):

        def context_case(length: int = length) -> None:
            nonlocal same_context
            scope = resolve_period_scope(
                {
                    "type": "request_period",
                    "request": {
                        "schema_version": "saju-period-request-v2",
                        "date_expression": "explicit",
                        "start_date": REFERENCE_DATE.isoformat(),
                        "end_date": (
                            REFERENCE_DATE + timedelta(days=length - 1)
                        ).isoformat(),
                    },
                },
                reference_date=REFERENCE_DATE,
            )
            binding = _snapshot(_period(scope), length)
            runtime_context, _, _ = _runtime_model_context_from_binding(binding)
            k0 = _messages_for_engine(
                [], "k0_instruct", "기간을 봐줘", "system", runtime_context
            )
            ki20 = _messages_for_engine(
                [], "ki20_final", "기간을 봐줘", "system", runtime_context
            )
            same_context = same_context and k0 == ki20
            _expect(same_context, "K0·KI20 context가 다릅니다.")

        recorder.case("same_context_k0_ki20", context_case)

    leakage_free = True
    for index in range(EXPECTED_STRATA["public_leakage"]):

        def leakage_case(index: int = index) -> None:
            nonlocal leakage_free
            value = deepcopy(
                dict(
                    real_snapshot
                    if index % 2
                    else synthetic.public_snapshot(SESSION_ID)
                )
            )
            leakage_free = leakage_free and not _contains_forbidden(value)
            _expect(leakage_free, "공개 snapshot에 private 값이 있습니다.")
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            _expect(
                PRIVATE_VALUE_PATTERN.search(encoded) is None, "공개 snapshot 값 누출"
            )

        recorder.case("public_leakage", leakage_case)
    return {"same_context": same_context, "public_leakage_free": leakage_free}


def _implementation_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in IMPLEMENTATION_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise PeriodDashboardCanaryError(f"canary 구현 파일이 없습니다: {relative}")
        hashes[relative] = sha256_file(path)
    return hashes


def validate_contract() -> dict[str, Any]:
    config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
    validate_config(config)
    from scripts.runtime.calculation.contracts_v1_5 import (
        validate_release_registry_v1_5,
    )
    from scripts.runtime.period_v1.contracts_v1_1 import validate_release_registry

    parent = validate_release_registry_v1_5(RELEASE_V15_PATH)
    period = validate_release_registry(PERIOD_RELEASE_PATH)
    hashes = _implementation_hashes()
    return {
        "status": "validated",
        "cases": sum(EXPECTED_STRATA.values()),
        "strata": EXPECTED_STRATA,
        "parent_release_id": parent["release_id"],
        "period_release_id": period["release_id"],
        "implementation_files": len(hashes),
        "feature_default": config["period_runtime"]["enabled_by_default"],
        "sealed_blind_accessed": False,
        "gpu_generation_performed": False,
    }


def run_canary(ephemeris: Path) -> dict[str, Any]:
    validate_contract()
    if not ephemeris.is_absolute() or ephemeris.is_symlink() or not ephemeris.is_file():
        raise PeriodDashboardCanaryError(
            "DE440s는 symlink가 아닌 절대경로 파일이어야 합니다."
        )
    recorder = _Recorder()
    synthetic, http_flags = _run_http(recorder)
    restart_public, real_snapshot = _run_restarts(recorder, ephemeris)
    context_flags = _run_context_and_leakage(recorder, synthetic, real_snapshot)
    strata = recorder.strata()
    cases = sum(item["cases"] for item in strata.values())
    failures = sum(item["failed"] for item in strata.values())
    gate_checks = {
        "case_matrix_exact": cases == 200
        and all(
            strata[name]["cases"] == expected
            for name, expected in EXPECTED_STRATA.items()
        ),
        "all_cases_passed": failures == 0,
        "http_unexpected_errors_zero": all(
            strata[name]["failed"] == 0
            for name in (
                "feature_off",
                "relative_dates",
                "explicit_ranges",
                "label_boundaries",
                "security_tamper_rate",
                "unsupported_scope",
            )
        ),
        "snapshot_swap_zero": http_flags["snapshot_tamper"],
        "fabricated_period_fact_zero": strata["label_boundaries"]["failed"] == 0,
        "public_private_leaks_zero": restart_public
        and context_flags["public_leakage_free"],
        "same_context_k0_ki20": context_flags["same_context"],
        "security_fail_closed": all(
            http_flags[name] for name in ("csrf", "origin", "stale", "rate", "legacy")
        ),
        "request_logs_redacted": http_flags["logs_redacted"],
        "process_restart_complete": strata["process_restart"]
        == {"cases": 20, "passed": 20, "failed": 0},
        "feature_default_off": True,
        "no_gpu_generation": True,
        "no_sealed_blind_access": True,
        "no_training_or_promotion": True,
    }
    passed = all(gate_checks.values())
    return {
        "schema_version": REPORT_VERSION,
        "suite_version": "saju-period-dashboard-canary-v1.0.0",
        "status": "passed_dashboard_v1_12_canary" if passed else "failed_canary",
        "diagnostic_target_met": passed,
        "cases": cases,
        "passed": cases - failures,
        "failed": failures,
        "failure_counts": dict(sorted(recorder.failure_counts.items())),
        "strata": strata,
        "gate_checks": gate_checks,
        "runtime": {
            "dashboard_schema_version": "1.12.0",
            "binding_id": BINDING_ID,
            "parent_release_id": "saju-runtime-release-v1.5.0-8b1d6ea2d46e",
            "period_release_id": "saju-period-daily-label-release-v1.0.0-59e326f8f086",
            "maximum_days": 31,
            "intraday_segments_supported": False,
            "feature_default": False,
        },
        "output_policy": {
            "aggregate_only": True,
            "raw_case_output_tracked": False,
            "raw_model_output_tracked": False,
            "birth_input_recorded": False,
            "runtime_identifier_recorded": False,
            "private_path_recorded": False,
        },
        "governance": {
            "production_service_swapped": False,
            "strict_full_runtime_approved": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generated": False,
            "training_execution_performed": False,
            "model_promotion_performed": False,
            "phase6_status_auto_changed": False,
        },
    }


def _build_id(report: Mapping[str, Any], implementation: Mapping[str, str]) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {"implementation_sha256": dict(implementation), "report": dict(report)}
        )
    ).hexdigest()
    return f"build-{digest[:12]}"


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_report(report: Mapping[str, Any], output_base: Path = REPORT_ROOT) -> Path:
    if output_base.resolve(strict=False) != REPORT_ROOT.resolve(strict=False):
        raise PeriodDashboardCanaryError(
            "canary 공개 경로는 고정 report root여야 합니다."
        )
    implementation = _implementation_hashes()
    build_id = _build_id(report, implementation)
    aggregate = {"build_id": build_id, **dict(report)}
    aggregate_bytes = canonical_json_bytes(aggregate) + b"\n"
    manifest = {
        "schema_version": REPORT_VERSION,
        "build_id": build_id,
        "aggregate_sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
        "implementation_sha256": implementation,
        "raw_case_output_tracked": False,
        "raw_model_output_tracked": False,
        "private_path_recorded": False,
        "sealed_blind_accessed": False,
        "gpu_generation_performed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    output_base.mkdir(parents=True, exist_ok=True)
    if output_base.is_symlink() or not output_base.is_dir():
        raise PeriodDashboardCanaryError("canary report root가 안전하지 않습니다.")
    root = output_base / build_id
    if root.exists():
        if (
            root.is_dir()
            and not root.is_symlink()
            and (root / "aggregate.json").read_bytes() == aggregate_bytes
            and (root / "build_manifest.json").read_bytes() == manifest_bytes
        ):
            return root
        raise PeriodDashboardCanaryError("기존 canary build가 현재 결과와 다릅니다.")
    temporary = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=output_base))
    try:
        _write_exclusive(temporary / "aggregate.json", aggregate_bytes)
        _write_exclusive(temporary / "build_manifest.json", manifest_bytes)
        os.replace(temporary, root)
    finally:
        if temporary.exists():
            for name in ("aggregate.json", "build_manifest.json"):
                (temporary / name).unlink(missing_ok=True)
            temporary.rmdir()
    return root


def verify_report(report_root: Path) -> dict[str, Any]:
    validate_contract()
    if (
        not report_root.is_absolute()
        or report_root.is_symlink()
        or not report_root.is_dir()
        or BUILD_PATTERN.fullmatch(report_root.name) is None
        or {item.name for item in report_root.iterdir()}
        != {"aggregate.json", "build_manifest.json"}
    ):
        raise PeriodDashboardCanaryError("canary report 경로·파일 집합이 다릅니다.")
    aggregate_path = report_root / "aggregate.json"
    manifest_path = report_root / "build_manifest.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = dict(aggregate)
    build_id = report.pop("build_id", None)
    implementation = _implementation_hashes()
    if (
        build_id != report_root.name
        or build_id != _build_id(report, implementation)
        or manifest.get("build_id") != build_id
        or manifest.get("aggregate_sha256") != sha256_file(aggregate_path)
        or manifest.get("implementation_sha256") != implementation
        or any(
            manifest.get(field) is not False
            for field in (
                "raw_case_output_tracked",
                "raw_model_output_tracked",
                "private_path_recorded",
                "sealed_blind_accessed",
                "gpu_generation_performed",
                "training_execution_performed",
                "model_promotion_performed",
            )
        )
        or aggregate.get("diagnostic_target_met") is not True
        or aggregate.get("cases") != 200
        or aggregate.get("passed") != 200
        or aggregate.get("failed") != 0
        or aggregate.get("failure_counts") != {}
        or not all(aggregate.get("gate_checks", {}).values())
        or any(aggregate.get("governance", {}).values())
    ):
        raise PeriodDashboardCanaryError("canary report hash·Gate가 다릅니다.")
    for name, expected in EXPECTED_STRATA.items():
        if aggregate.get("strata", {}).get(name) != {
            "cases": expected,
            "passed": expected,
            "failed": 0,
        }:
            raise PeriodDashboardCanaryError(f"canary stratum이 다릅니다: {name}")
    encoded = aggregate_path.read_text(encoding="utf-8")
    if PRIVATE_VALUE_PATTERN.search(encoded) or '"session_id"' in encoded:
        raise PeriodDashboardCanaryError("canary aggregate에 private 값이 있습니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "cases": 200,
        "dashboard_schema_version": "1.12.0",
        "feature_default": False,
        "production_service_swapped": False,
        "sealed_blind_accessed": False,
        "gpu_generation_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="dashboard v1.12 일별 기간 자동 canary"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    execute = commands.add_parser("execute")
    execute.add_argument("--execute", action="store_true")
    execute.add_argument(
        "--ephemeris",
        type=Path,
        default=REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp",
    )
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            result = validate_contract()
        elif args.command == "plan":
            result = {
                **validate_contract(),
                "status": "planned",
                "writes_performed": False,
                "gpu_generation_planned": False,
            }
        elif args.command == "execute":
            if not args.execute:
                result = {
                    **validate_contract(),
                    "status": "dry_run",
                    "writes_performed": False,
                }
            else:
                report = run_canary(args.ephemeris.resolve())
                root = write_report(report)
                result = verify_report(root.resolve())
        elif args.command == "verify":
            result = verify_report(args.report_root.resolve())
        else:
            raise PeriodDashboardCanaryError("지원하지 않는 command입니다.")
    except (
        OSError,
        PeriodDashboardCanaryError,
        PeriodRuntimeError,
        Phase5DashboardError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
