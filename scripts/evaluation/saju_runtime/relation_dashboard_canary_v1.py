# relation_dashboard_canary_v1.py - dashboard v1.13의 단일 날짜 관계 경로 160건을 자동 검증한다.

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

from scripts.evaluation.saju_runtime.relation_conformance_v1 import (
    TEST_PERIOD_KEY,
    TEST_RELATION_KEY,
    _chart_fixture,
    _period_fixture,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.calculation.facts import BRANCHES, STEMS
from scripts.runtime.chart_day_adapter import public_chart
from scripts.runtime.chart_only_security import (
    create_private_directory,
    create_secret_key,
)
from scripts.runtime.period_dashboard_binding import PeriodDashboardBindingError
from scripts.runtime.period_v1.contracts_v1_1 import RELEASE_PATH as PERIOD_RELEASE_PATH
from scripts.runtime.period_v1.engine import (
    public_daily_label_result,
    validate_public_daily_label_result,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.resolver import resolve_period_scope
from scripts.runtime.period_v1.security import PeriodIdSigner
from scripts.runtime.relation_dashboard_binding import (
    BINDING_ID,
    RelationDashboardBinding,
)
from scripts.runtime.relation_v1.contracts import RELEASE_PATH as RELATION_RELEASE_PATH
from scripts.runtime.relation_v1.engine import (
    branch_relations,
    calculate_relation_candidate,
    public_relation_result,
    validate_public_relation_result,
)
from scripts.runtime.relation_v1.errors import RelationRuntimeError
from scripts.runtime.relation_v1.security import RelationIdSigner
from scripts.training.phase5_dashboard_v1_13 import (
    DEFAULT_CONFIG,
    V113_ASSET_ROOT,
    DashboardHTTPServer,
    Phase5DashboardError,
    SlidingWindowRateLimiter,
    _messages_for_engine,
    _runtime_model_context_from_binding,
    validate_config,
)

REPORT_VERSION = "1.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_relation_dashboard_canary/v1.0.0"
BUILD_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
SESSION_ID = "c" * 24
CAPABILITY_SHA256 = "e" * 64
REFERENCE_DATE = date(2026, 9, 2)
EXPECTED_STRATA = {
    "feature_off": 10,
    "single_date_relation": 30,
    "range_relation_absent": 20,
    "overlap_and_punishment": 30,
    "process_restart": 20,
    "security_tamper": 20,
    "same_context_k0_ki20": 10,
    "public_leakage": 20,
}
IMPLEMENTATION_PATHS = (
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.13.0.json",
    "configs/runtime/calculation/releases/v1.5.0/release_registry.json",
    "configs/runtime/period/releases/v1.0.0/release_registry.json",
    "configs/runtime/relations/releases/v1.0.0/release_registry.json",
    "scripts/runtime/period_dashboard_binding.py",
    "scripts/runtime/relation_dashboard_binding.py",
    "scripts/runtime/relation_v1/contracts.py",
    "scripts/runtime/relation_v1/engine.py",
    "scripts/runtime/relation_v1/errors.py",
    "scripts/runtime/relation_v1/security.py",
    "scripts/training/phase5_dashboard_v1_13.py",
    "scripts/training/phase5_dashboard_assets/v1.13.0/index.html",
    "scripts/training/phase5_dashboard_assets/v1.13.0/dashboard.js",
    "scripts/training/phase5_dashboard_assets/v1.13.0/dashboard.css",
    "scripts/training/phase5_dashboard_assets/v1.13.0/prompt-examples.json",
    "scripts/evaluation/saju_runtime/relation_dashboard_canary_v1.py",
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
    "relation_snapshot_id",
    "runtime_session_id",
    "session_id",
}
PRIVATE_VALUE_PATTERN = re.compile(
    r"(?:sc2_|sbi2_|scr2_|scs2_|spd1_|sr1_)[0-9a-f]{64}|"
    r"(?:/home/|/tmp/|[A-Za-z]:\\\\)"
)


class RelationDashboardCanaryError(RuntimeError):
    """dashboard v1.13 자동 canary 계약 위반."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RelationDashboardCanaryError(message)


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
            PeriodRuntimeError,
            Phase5DashboardError,
            RelationDashboardCanaryError,
            RelationRuntimeError,
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


def _fixture_value() -> dict[str, Any]:
    chart_internal, authorization = _chart_fixture()
    period_signer = PeriodIdSigner.for_test(TEST_PERIOD_KEY)
    period_internal = _period_fixture(authorization, period_signer)
    relation_internal = calculate_relation_candidate(
        chart_snapshot=chart_internal,
        period_snapshot=period_internal,
        period_signer=period_signer,
        relation_signer=RelationIdSigner.for_test(TEST_RELATION_KEY),
        authority_release_id="saju-relation-dashboard-canary-candidate-v1.0.0",
    )
    return {
        "chart": public_chart(chart_internal),
        "period": public_daily_label_result(period_internal),
        "relation": public_relation_result(relation_internal),
    }


def _range_period(scope: Mapping[str, Any]) -> dict[str, Any]:
    start = date.fromisoformat(str(scope["start_date"]))
    end = date.fromisoformat(str(scope["end_date"]))
    days = []
    for offset in range((end - start).days + 1):
        target = start + timedelta(days=offset)
        ordinal = target.toordinal()
        days.append(
            {
                "date": target.isoformat(),
                "year_ganzhi": "丙午",
                "month_ganzhi": "丙申",
                "day_ganzhi": STEMS[ordinal % 10] + BRANCHES[ordinal % 12],
                "authority": "SOURCE_HARD_FACT",
            }
        )
    value = {
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
        "limitations": ["공식 정확도는 period conformance가 별도로 소유합니다."],
    }
    return validate_public_daily_label_result(value)


def _snapshot(value: Mapping[str, Any], revision: int) -> dict[str, Any]:
    copied = deepcopy(dict(value))
    return {
        "schema_version": "1.3.0",
        "binding_id": BINDING_ID,
        "capability_sha256": CAPABILITY_SHA256,
        "snapshot_sha256": hashlib.sha256(canonical_json_bytes(copied)).hexdigest(),
        "state_revision": revision,
        "value": copied,
    }


def _single_snapshot(revision: int = 1) -> dict[str, Any]:
    return _snapshot(_fixture_value(), revision)


def _range_snapshot(length: int, revision: int = 1) -> dict[str, Any]:
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
    fixture = _fixture_value()
    return _snapshot(
        {"chart": fixture["chart"], "period": _range_period(scope), "relation": None},
        revision,
    )


class _SyntheticBinding:
    """HTTP transport만 검증하며 천문 정확도 주장은 만들지 않는 합성 binding."""

    def __init__(self) -> None:
        self.revision = 0
        self.deleted = False
        self.current = _fixture_value()

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "1.3.0",
            "status": "limited_public_chart_period_and_single_date_relation_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "합성 HTTP relation canary 활성",
            "period_today_kst": REFERENCE_DATE.isoformat(),
            "period_minimum": REFERENCE_DATE.isoformat(),
            "period_maximum": "2049-12-31",
            "period_maximum_days": 31,
            "period_evaluation_local_time": "12:00",
            "single_date_relation_allowed": True,
            "range_relation_arrays_supported": False,
            "client_authentication_required": False,
        }

    def create_session(self) -> dict[str, Any]:
        self.revision = 0
        self.deleted = False
        self.current = _fixture_value()
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
        if scope["day_count"] == 1:
            if scope["start_date"] != REFERENCE_DATE.isoformat():
                raise PeriodDashboardBindingError(
                    400,
                    "SYNTHETIC_SINGLE_DATE_UNSUPPORTED",
                    "합성 canary는 기준 단일 날짜만 사용합니다.",
                )
            self.current = _fixture_value()
        else:
            fixture = _fixture_value()
            self.current = {
                "chart": fixture["chart"],
                "period": _range_period(scope),
                "relation": None,
            }
        self.revision += 1
        return {
            "status": "ready",
            "state_revision": self.revision,
            "decision": {
                "action": "render_chart_period_and_optional_relation",
                "message": "합성 relation transport 완료",
                "reason_code": None,
            },
            "result": deepcopy(self.current),
            "governance": {},
        }

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        if session_id != SESSION_ID or self.deleted:
            raise PeriodDashboardBindingError(
                404, "RUNTIME_SESSION_NOT_FOUND", "runtime session을 찾을 수 없습니다."
            )
        return _snapshot(self.current, self.revision)

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
                "relation_release": {"status": "verified"},
                "asset_root": V113_ASSET_ROOT,
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
            V113_ASSET_ROOT,
            self.csrf,
            period_binding=binding,
            period_runtime_requested=binding is not None,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = int(self.server.server_address[1])

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
        origin: str | None = None,
        csrf: str | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        encoded = (
            None if body is None else json.dumps(body, ensure_ascii=False).encode()
        )
        headers = {
            "Host": f"127.0.0.1:{self.port}",
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
        response_headers = {
            key.lower(): value for key, value in response.getheaders()
        }
        connection.close()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RelationDashboardCanaryError("HTTP 응답이 JSON object가 아닙니다.")
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


def _run_http(recorder: _Recorder) -> tuple[dict[str, bool], dict[str, Any]]:
    off = _Harness(binding=None)
    try:
        for _ in range(EXPECTED_STRATA["feature_off"]):

            def feature_off() -> None:
                status, _, runtime_status = off.request("GET", "/api/runtime/status")
                _expect(status == 200, "비활성 runtime status 조회가 실패했습니다.")
                _expect(
                    runtime_status.get("schema_version") == "1.3.0",
                    "비활성 runtime status schema 오류",
                )
                _expect(runtime_status.get("enabled") is False, "기본 off status 오류")
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
    security_flags = {
        "csrf": False,
        "origin": False,
        "outer_hash": False,
        "parent_hash": False,
        "range_injection": False,
    }
    try:
        for _ in range(EXPECTED_STRATA["single_date_relation"]):

            def single_case() -> None:
                nonlocal revision
                status, value = _http_event(
                    active,
                    revision,
                    {
                        "schema_version": "saju-period-request-v2",
                        "date_expression": "today",
                        "start_date": None,
                        "end_date": None,
                    },
                )
                _expect(status == 200, "단일 날짜 HTTP가 실패했습니다.")
                revision = int(value["state_revision"])
                relation = validate_public_relation_result(
                    value["result"]["relation"]
                )
                _expect(
                    relation["selected_date"] == REFERENCE_DATE.isoformat(),
                    "relation 선택 날짜가 다릅니다.",
                )
                snapshot = binding.public_snapshot(SESSION_ID)
                _runtime_model_context_from_binding(snapshot)

            recorder.case("single_date_relation", single_case)

        for length in range(2, 2 + EXPECTED_STRATA["range_relation_absent"]):

            def range_case(length: int = length) -> None:
                nonlocal revision
                end = REFERENCE_DATE + timedelta(days=length - 1)
                status, value = _http_event(
                    active,
                    revision,
                    {
                        "schema_version": "saju-period-request-v2",
                        "date_expression": "explicit",
                        "start_date": REFERENCE_DATE.isoformat(),
                        "end_date": end.isoformat(),
                    },
                )
                _expect(status == 200, "기간 HTTP가 실패했습니다.")
                revision = int(value["state_revision"])
                _expect(value["result"]["relation"] is None, "범위 relation 생성")
                period = validate_public_daily_label_result(
                    value["result"]["period"]
                )
                _expect(period["period_scope"]["day_count"] == length, "기간 길이 오류")
                prompt, _, _ = _runtime_model_context_from_binding(
                    binding.public_snapshot(SESSION_ID)
                )
                _expect('"relation":null' in prompt, "범위 null relation 누락")

            recorder.case("range_relation_absent", range_case)

        for index in range(EXPECTED_STRATA["security_tamper"]):

            def security_case(index: int = index) -> None:
                group = index // 4
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
                    tampered = _single_snapshot()
                    tampered["snapshot_sha256"] = "0" * 64
                    try:
                        _runtime_model_context_from_binding(tampered)
                    except Phase5DashboardError:
                        security_flags["outer_hash"] = True
                    else:
                        raise RelationDashboardCanaryError("snapshot hash 변조 수용")
                elif group == 3:
                    tampered = _single_snapshot()
                    tampered["value"]["relation"]["provenance"][
                        "chart_snapshot_sha256"
                    ] = "0" * 64
                    tampered["snapshot_sha256"] = hashlib.sha256(
                        canonical_json_bytes(tampered["value"])
                    ).hexdigest()
                    try:
                        _runtime_model_context_from_binding(tampered)
                    except Phase5DashboardError:
                        security_flags["parent_hash"] = True
                    else:
                        raise RelationDashboardCanaryError("부모 hash 변조 수용")
                else:
                    tampered = _range_snapshot(2)
                    tampered["value"]["relation"] = _fixture_value()["relation"]
                    tampered["snapshot_sha256"] = hashlib.sha256(
                        canonical_json_bytes(tampered["value"])
                    ).hexdigest()
                    try:
                        _runtime_model_context_from_binding(tampered)
                    except Phase5DashboardError:
                        security_flags["range_injection"] = True
                    else:
                        raise RelationDashboardCanaryError("범위 relation 변조 수용")

            recorder.case("security_tamper", security_case)
    finally:
        logs = active.stderr.getvalue()
        active.close()
    return (
        {**security_flags, "logs_redacted": SESSION_ID not in logs},
        _single_snapshot(),
    )


def _run_relation_cases(recorder: _Recorder) -> None:
    pair_cases: tuple[tuple[str, str, set[tuple[str, str]]], ...] = (
        ("寅", "亥", {("합", "symmetric_pair"), ("파", "symmetric_pair")}),
        (
            "巳",
            "申",
            {
                ("합", "symmetric_pair"),
                ("형", "symmetric_group_pair"),
                ("파", "symmetric_pair"),
            },
        ),
        ("寅", "申", {("충", "symmetric_pair"), ("형", "symmetric_group_pair")}),
        ("寅", "巳", {("형", "symmetric_group_pair"), ("해", "symmetric_pair")}),
        ("丑", "未", {("충", "symmetric_pair"), ("형", "symmetric_group_pair")}),
        ("丑", "戌", {("형", "symmetric_group_pair")}),
        ("未", "戌", {("형", "symmetric_group_pair"), ("파", "symmetric_pair")}),
        ("子", "卯", {("형", "symmetric_group_pair")}),
        ("子", "丑", {("합", "symmetric_pair")}),
        ("子", "午", {("충", "symmetric_pair")}),
        ("子", "酉", {("파", "symmetric_pair")}),
        ("子", "未", {("해", "symmetric_pair")}),
        ("辰", "酉", {("합", "symmetric_pair")}),
    )
    cases: list[tuple[str, str, set[tuple[str, str]]]] = []
    for left, right, expected in pair_cases:
        cases.extend(((left, right, expected), (right, left, expected)))
    cases.extend(
        (branch, branch, {("형", "symmetric_self")})
        for branch in ("辰", "午", "酉", "亥")
    )
    _expect(len(cases) == EXPECTED_STRATA["overlap_and_punishment"], "관계 case 수 오류")
    for left, right, expected in cases:

        def relation_case(
            left: str = left,
            right: str = right,
            expected: set[tuple[str, str]] = expected,
        ) -> None:
            actual = set(branch_relations(left, right))
            _expect(actual == expected, f"관계표 불일치: {left}/{right}")

        recorder.case("overlap_and_punishment", relation_case)


def _drive_real_chart(binding: RelationDashboardBinding) -> tuple[str, int]:
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
                "start_date": REFERENCE_DATE.isoformat(),
                "end_date": REFERENCE_DATE.isoformat(),
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
        prefix="saju-relation-dashboard-canary-"
    ) as directory:
        root = Path(directory)
        root.chmod(0o700)
        keys = create_private_directory(root / "keys", label="canary keys")
        store = create_private_directory(root / "sessions", label="canary sessions")
        hmac_key = create_secret_key(keys / "runtime-hmac.key", purpose="runtime-hmac")
        aead_key = create_secret_key(keys / "session-aead.key", purpose="session-aead")
        lease = root / "relation-runtime.lease"

        def open_binding() -> RelationDashboardBinding:
            return RelationDashboardBinding(
                parent_release_registry=RELEASE_V15_PATH,
                period_release_registry=PERIOD_RELEASE_PATH,
                relation_release_registry=RELATION_RELEASE_PATH,
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
                    _expect(current == baseline, "재시작 후 relation snapshot 변경")
                    relation = validate_public_relation_result(
                        current["value"]["relation"]
                    )
                    _expect(
                        relation["selected_date"] == REFERENCE_DATE.isoformat(),
                        "재시작 후 relation 날짜 변경",
                    )
                finally:
                    restarted.close()

            recorder.case("process_restart", restart_case)
        return not _contains_forbidden(baseline), baseline


def _run_context_and_leakage(
    recorder: _Recorder,
    synthetic_snapshot: Mapping[str, Any],
    real_snapshot: Mapping[str, Any],
) -> dict[str, bool]:
    same_context = True
    for _ in range(EXPECTED_STRATA["same_context_k0_ki20"]):

        def context_case() -> None:
            nonlocal same_context
            runtime_context, _, _ = _runtime_model_context_from_binding(
                deepcopy(dict(synthetic_snapshot))
            )
            k0 = _messages_for_engine(
                [], "k0_instruct", "오늘을 봐줘", "system", runtime_context
            )
            ki20 = _messages_for_engine(
                [], "ki20_final", "오늘을 봐줘", "system", runtime_context
            )
            same_context = same_context and k0 == ki20
            _expect(same_context, "K0·KI20 relation context가 다릅니다.")

        recorder.case("same_context_k0_ki20", context_case)

    leakage_free = True
    for index in range(EXPECTED_STRATA["public_leakage"]):

        def leakage_case(index: int = index) -> None:
            nonlocal leakage_free
            value = deepcopy(
                dict(real_snapshot if index % 2 else synthetic_snapshot)
            )
            leakage_free = leakage_free and not _contains_forbidden(value)
            _expect(leakage_free, "공개 snapshot에 private 값이 있습니다.")
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            _expect(PRIVATE_VALUE_PATTERN.search(encoded) is None, "공개 값 누출")

        recorder.case("public_leakage", leakage_case)
    return {"same_context": same_context, "public_leakage_free": leakage_free}


def _implementation_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in IMPLEMENTATION_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RelationDashboardCanaryError(f"canary 구현 파일이 없습니다: {relative}")
        hashes[relative] = sha256_file(path)
    return hashes


def validate_contract() -> dict[str, Any]:
    config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
    validate_config(config)
    from scripts.runtime.calculation.contracts_v1_5 import (
        validate_release_registry_v1_5,
    )
    from scripts.runtime.period_v1.contracts_v1_1 import validate_release_registry
    from scripts.runtime.relation_v1.contracts import (
        validate_release_registry as validate_relation_release_registry,
    )

    parent = validate_release_registry_v1_5(RELEASE_V15_PATH)
    period = validate_release_registry(PERIOD_RELEASE_PATH)
    relation = validate_relation_release_registry(RELATION_RELEASE_PATH)
    configured = config["period_runtime"]["relation_automatic_canary"]
    expected = {"total_cases": sum(EXPECTED_STRATA.values()), **EXPECTED_STRATA}
    _expect(configured == expected, "dashboard relation canary matrix가 다릅니다.")
    hashes = _implementation_hashes()
    return {
        "status": "validated",
        "cases": sum(EXPECTED_STRATA.values()),
        "strata": EXPECTED_STRATA,
        "parent_release_id": parent["release_id"],
        "period_release_id": period["release_id"],
        "relation_release_id": relation["release_id"],
        "implementation_files": len(hashes),
        "feature_default": config["period_runtime"]["enabled_by_default"],
        "sealed_blind_accessed": False,
        "gpu_generation_performed": False,
    }


def run_canary(ephemeris: Path) -> dict[str, Any]:
    contract = validate_contract()
    if not ephemeris.is_absolute() or ephemeris.is_symlink() or not ephemeris.is_file():
        raise RelationDashboardCanaryError(
            "DE440s는 symlink가 아닌 절대경로 파일이어야 합니다."
        )
    recorder = _Recorder()
    security_flags, synthetic_snapshot = _run_http(recorder)
    _run_relation_cases(recorder)
    restart_public, real_snapshot = _run_restarts(recorder, ephemeris)
    context_flags = _run_context_and_leakage(
        recorder, synthetic_snapshot, real_snapshot
    )
    strata = recorder.strata()
    cases = sum(item["cases"] for item in strata.values())
    failures = sum(item["failed"] for item in strata.values())
    gate_checks = {
        "case_matrix_exact": cases == 160
        and all(
            strata[name]["cases"] == expected
            for name, expected in EXPECTED_STRATA.items()
        ),
        "all_cases_passed": failures == 0,
        "single_date_relation_complete": strata["single_date_relation"]
        == {"cases": 30, "passed": 30, "failed": 0},
        "range_relation_arrays_zero": strata["range_relation_absent"]
        == {"cases": 20, "passed": 20, "failed": 0},
        "overlap_and_punishment_complete": strata["overlap_and_punishment"]
        == {"cases": 30, "passed": 30, "failed": 0},
        "process_restart_complete": strata["process_restart"]
        == {"cases": 20, "passed": 20, "failed": 0},
        "security_fail_closed": all(security_flags.values()),
        "same_context_k0_ki20": context_flags["same_context"],
        "public_private_leaks_zero": restart_public
        and context_flags["public_leakage_free"],
        "feature_default_off": contract["feature_default"] is False,
        "no_relation_interpretation": True,
        "no_gpu_generation": True,
        "no_sealed_blind_access": True,
        "no_training_or_promotion": True,
    }
    passed = all(gate_checks.values())
    return {
        "schema_version": REPORT_VERSION,
        "suite_version": "saju-relation-dashboard-canary-v1.0.0",
        "status": "passed_dashboard_v1_13_relation_canary" if passed else "failed_canary",
        "diagnostic_target_met": passed,
        "cases": cases,
        "passed": cases - failures,
        "failed": failures,
        "failure_counts": dict(sorted(recorder.failure_counts.items())),
        "strata": strata,
        "gate_checks": gate_checks,
        "runtime": {
            "dashboard_schema_version": "1.13.0",
            "binding_id": BINDING_ID,
            "parent_release_id": contract["parent_release_id"],
            "period_release_id": contract["period_release_id"],
            "relation_release_id": contract["relation_release_id"],
            "single_date_relation_allowed": True,
            "range_relation_arrays_supported": False,
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
        raise RelationDashboardCanaryError("canary 공개 경로는 고정 report root여야 합니다.")
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
        raise RelationDashboardCanaryError("canary report root가 안전하지 않습니다.")
    root = output_base / build_id
    if root.exists():
        if (
            root.is_dir()
            and not root.is_symlink()
            and (root / "aggregate.json").read_bytes() == aggregate_bytes
            and (root / "build_manifest.json").read_bytes() == manifest_bytes
        ):
            return root
        raise RelationDashboardCanaryError("기존 canary build가 현재 결과와 다릅니다.")
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
        raise RelationDashboardCanaryError("canary report 경로·파일 집합이 다릅니다.")
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
        or aggregate.get("cases") != 160
        or aggregate.get("passed") != 160
        or aggregate.get("failed") != 0
        or aggregate.get("failure_counts") != {}
        or not all(aggregate.get("gate_checks", {}).values())
        or any(aggregate.get("governance", {}).values())
    ):
        raise RelationDashboardCanaryError("canary report hash·Gate가 다릅니다.")
    for name, expected in EXPECTED_STRATA.items():
        if aggregate.get("strata", {}).get(name) != {
            "cases": expected,
            "passed": expected,
            "failed": 0,
        }:
            raise RelationDashboardCanaryError(f"canary stratum이 다릅니다: {name}")
    encoded = aggregate_path.read_text(encoding="utf-8")
    if PRIVATE_VALUE_PATTERN.search(encoded) or '"session_id"' in encoded:
        raise RelationDashboardCanaryError("canary aggregate에 private 값이 있습니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "cases": 160,
        "dashboard_schema_version": "1.13.0",
        "feature_default": False,
        "production_service_swapped": False,
        "sealed_blind_accessed": False,
        "gpu_generation_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="dashboard v1.13 단일 날짜 relation 자동 canary"
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
            raise RelationDashboardCanaryError("지원하지 않는 command입니다.")
    except (
        OSError,
        PeriodRuntimeError,
        Phase5DashboardError,
        RelationDashboardCanaryError,
        RelationRuntimeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
