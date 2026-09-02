# chart_only_dashboard_canary.py - dashboard v1.9의 합성 HTTP 100건과 실제 GPU 모델 1쌍을 자동 검증한다.

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import io
import json
import os
import re
import stat
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.chart_only_dashboard_binding import (
    ChartOnlyDashboardBinding,
    ChartOnlyDashboardBindingError,
)
from scripts.runtime.chart_only_dashboard_contracts import (
    CANARY_PATH,
    EXPECTED_CHECKS,
    EXPECTED_STRATA,
    REGISTRY_SHA256,
    ChartOnlyDashboardContractError,
    validate_dashboard_operations_registry,
)
from scripts.runtime.chart_only_operations_contracts import (
    load_strict_json,
    sha256_file,
)
from scripts.runtime.chart_only_security import (
    create_private_directory,
    create_secret_key,
)
from scripts.training.phase5_dashboard import (
    DEFAULT_CONFIG,
    DashboardHTTPServer,
    SlidingWindowRateLimiter,
    prepare_context,
)

REPORT_VERSION = "1.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_dashboard_canary/v1.0.0"
BUILD_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
HMAC_VALUE_PATTERN = re.compile(r"(?:sbi2|sc2|scs2|scr2|sif2)_[0-9a-f]{64}")
PRIVATE_PATH_PATTERN = re.compile(r"(?:/home/|/tmp/|[A-Za-z]:\\\\)")
IMPLEMENTATION_PATHS = (
    "configs/runtime/operations/registry-v1.1.0.json",
    "configs/runtime/operations/chart_only_dashboard_binding-v1.0.0.json",
    "configs/runtime/operations/chart_only_dashboard_canary_gate-v1.0.0.json",
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.9.0.json",
    "scripts/runtime/chart_only_dashboard_contracts.py",
    "scripts/runtime/chart_only_dashboard_binding.py",
    "scripts/runtime/chart_only_dashboard_operations.py",
    "scripts/training/phase5_dashboard.py",
    "scripts/training/phase5_dashboard_assets/v1.9.0/index.html",
    "scripts/training/phase5_dashboard_assets/v1.9.0/dashboard.js",
    "scripts/training/phase5_dashboard_assets/v1.9.0/dashboard.css",
    "scripts/training/phase5_dashboard_assets/v1.9.0/prompt-examples.json",
    "scripts/evaluation/saju_runtime/chart_only_dashboard_canary.py",
)


class ChartOnlyDashboardCanaryError(RuntimeError):
    """dashboard v1.9 자동 canary 계약 위반."""


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
            ChartOnlyDashboardBindingError,
            ChartOnlyDashboardCanaryError,
            KeyError,
            OSError,
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


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ChartOnlyDashboardCanaryError(message)


class _Harness:
    def __init__(
        self,
        context: dict[str, Any],
        *,
        port: int,
        binding: ChartOnlyDashboardBinding | None,
        generation_runner: Any | None = None,
    ) -> None:
        self.context = context
        self.csrf = "a" * 48
        self.stderr = io.StringIO()
        self._redirect = contextlib.redirect_stderr(self.stderr)
        self._redirect.__enter__()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", port),
            context,
            context["chart_only_runtime"]["asset_root"],
            self.csrf,
            chart_only_binding=binding,
            chart_only_runtime_requested=binding is not None,
            generation_runner=generation_runner,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._redirect.__exit__(None, None, None)

    def reset_limit(self, name: str) -> None:
        maximum = self.context["config"]["chart_only_runtime"][
            "rate_limits_per_minute"
        ][name]
        self.server.rate_limiters[name] = SlidingWindowRateLimiter(maximum)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        host: str | None = None,
        origin: str | None = None,
        csrf: str | None = None,
        timeout: float = 15,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=timeout
        )
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        headers = {
            "Host": host or f"127.0.0.1:{self.port}",
            "Origin": origin or f"http://127.0.0.1:{self.port}",
            "X-CSRF-Token": csrf or self.csrf,
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ChartOnlyDashboardCanaryError("HTTP 응답이 JSON이 아닙니다.") from exc
        if not isinstance(payload, dict):
            raise ChartOnlyDashboardCanaryError("HTTP 응답 최상위가 object가 아닙니다.")
        return response.status, response_headers, payload


def _event(
    harness: _Harness,
    session_id: str,
    revision: int,
    event: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    status, _, response = harness.request(
        "POST",
        f"/api/runtime/sessions/{session_id}/events",
        body={"expected_revision": revision, "event": event},
    )
    return status, response


def _drive_chart(
    harness: _Harness,
    *,
    birth_date: str,
    precision: str,
    city: str,
    calendar: str = "solar",
    exact_time: str = "12:00",
) -> tuple[str, dict[str, Any]]:
    status, _, created = harness.request("POST", "/api/runtime/sessions", body={})
    _expect(status == 201, "runtime session 생성 실패")
    session_id = created.get("session_id")
    _expect(
        isinstance(session_id, str) and SESSION_ID_PATTERN.fullmatch(session_id) is not None,
        "runtime session capability 형식 오류",
    )
    revision = 0
    events: list[dict[str, Any]] = [
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "calendar", "value": calendar},
        {"type": "set_slot", "field": "birth_date", "value": birth_date},
    ]
    if calendar == "lunar":
        events.append({"type": "set_slot", "field": "leap_month", "value": False})
    events.append(
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {"country_code": "KR", "city": city, "timezone": "Asia/Seoul"},
        }
    )
    if precision == "exact":
        events.append({"type": "set_slot", "field": "birth_time", "value": exact_time})
    elif precision == "range":
        events.append(
            {
                "type": "set_slot",
                "field": "time_range",
                "value": {"start": "10:00", "end": "10:30"},
            }
        )
    else:
        events.append({"type": "set_time_unknown"})
    events.append({"type": "request_chart"})
    response: dict[str, Any] = {}
    for item in events:
        status, response = _event(harness, session_id, revision, item)
        _expect(status == 200, "runtime 구조화 event 실패")
        revision = response["state_revision"]
    return session_id, response


def _delete(harness: _Harness, session_id: str) -> None:
    status, _, response = harness.request(
        "DELETE", f"/api/runtime/sessions/{session_id}"
    )
    _expect(status == 200 and response.get("status") == "deleted", "session 삭제 실패")


def _implementation_hashes() -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in IMPLEMENTATION_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise ChartOnlyDashboardCanaryError(f"canary 구현 파일이 없습니다: {relative}")
        result[relative] = sha256_file(path)
    return result


def _run_http_cases(
    context: dict[str, Any],
    *,
    port: int,
    binding: ChartOnlyDashboardBinding,
    store_root: Path,
) -> tuple[dict[str, Any], _Harness, list[str]]:
    recorder = _Recorder()
    runtime_ids: list[str] = []

    off = _Harness(context, port=port, binding=None)
    try:
        for _ in range(10):
            def disabled_case() -> None:
                status, _, payload = off.request("POST", "/api/runtime/sessions", body={})
                _expect(status == 409, "기본 off session 요청이 차단되지 않았습니다.")
                _expect(payload.get("code") == "RUNTIME_FEATURE_DISABLED", "기본 off code 오류")

            recorder.case("feature_disabled", disabled_case)
    finally:
        off.close()

    model_binding_seen: list[dict[str, Any]] = []

    def fake_generation(
        _context: dict[str, Any],
        _prompt: str,
        _manual_id: str | None,
        _profile: str | None,
        _selection: str | None,
        _legacy_runtime_id: str | None,
        runtime_binding: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if runtime_binding is None:
            raise ChartOnlyDashboardCanaryError("모델 binding snapshot이 없습니다.")
        model_binding_seen.append(runtime_binding)
        digest = runtime_binding["snapshot_sha256"]
        return {
            "status": "generated",
            "persisted": True,
            "local_only": True,
            "session_id": "a" * 24,
            "runtime_binding_applied": True,
            "runtime_snapshot_sha256": digest,
            "outputs": {"k0_instruct": "합성 K0", "ki20_final": "합성 KI20"},
            "contexts": {
                "k0_instruct": {"runtime_snapshot_sha256": digest},
                "ki20_final": {"runtime_snapshot_sha256": digest},
            },
        }

    active = _Harness(
        context,
        port=port,
        binding=binding,
        generation_runner=fake_generation,
    )
    active.context["chart_only_runtime_active"] = True
    public_allowlisted = True
    encrypted_store_only = True
    exact_host_origin_csrf = False
    stale_rejected = False
    legacy_gone = False
    rate_retry = False
    busy_429 = False
    duplicate_process = False
    try:
        for index in range(20):
            if index == 10:
                active.reset_limit("session_or_chart")

            def normal_case(index: int = index) -> None:
                precision = "exact" if index < 10 else "range" if index < 15 else "unknown"
                session_id, response = _drive_chart(
                    active,
                    birth_date=f"{1930 + index * 4:04d}-05-17",
                    precision=precision,
                    city=f"합성정상도시{index}",
                )
                runtime_ids.append(session_id)
                _expect(response.get("status") == "ready", "정상 원국이 준비되지 않았습니다.")
                _expect("session_id" not in response, "event 응답에 capability가 노출됐습니다.")
                _delete(active, session_id)

            recorder.case("normal_chart", normal_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("runtime_event")
        for index in range(10):
            def boundary_case(index: int = index) -> None:
                session_id, response = _drive_chart(
                    active,
                    birth_date="1958-05-06",
                    precision="exact",
                    city=f"합성경계도시{index}",
                    exact_time="10:19",
                )
                runtime_ids.append(session_id)
                _expect(response.get("status") == "blocked", "절입 경계가 차단되지 않았습니다.")
                _expect(
                    response.get("decision", {}).get("reason_code")
                    == "SOLAR_TERM_BOUNDARY_UNCERTAIN",
                    "절입 경계 code가 다릅니다.",
                )
                _delete(active, session_id)

            recorder.case("boundary_block", boundary_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("runtime_event")
        for index in range(20):
            if index == 10:
                active.reset_limit("session_or_chart")

            def scope_case(index: int = index) -> None:
                birth_date = (
                    f"1919-12-{index + 1:02d}"
                    if index < 10
                    else f"2026-09-{index - 9:02d}"
                )
                session_id, response = _drive_chart(
                    active,
                    birth_date=birth_date,
                    precision="exact",
                    city="합성범위밖도시",
                )
                runtime_ids.append(session_id)
                _expect(response.get("status") == "blocked", "승인 범위 밖 입력이 차단되지 않았습니다.")
                _expect(
                    response.get("decision", {}).get("reason_code")
                    == "BIRTH_DATE_OUT_OF_APPROVED_RANGE",
                    "승인 범위 차단 code가 다릅니다.",
                )
                _delete(active, session_id)

            recorder.case("scope_block", scope_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("runtime_event")
        for index in range(10):
            def tamper_case(index: int = index) -> None:
                status, _, created = active.request("POST", "/api/runtime/sessions", body={})
                _expect(status == 201, "변조 case session 생성 실패")
                session_id = created["session_id"]
                runtime_ids.append(session_id)
                replacement = "0" if session_id[0] != "0" else "1"
                tampered = replacement + session_id[1:]
                status, _, payload = active.request(
                    "POST",
                    f"/api/runtime/sessions/{tampered}/events",
                    body={"expected_revision": 0, "event": {"type": "request_chart"}},
                )
                _expect(status == 404, "변조 capability가 거부되지 않았습니다.")
                _expect(payload.get("code") == "RUNTIME_SESSION_NOT_FOUND", "변조 거부 code 오류")
                _delete(active, session_id)

            recorder.case("tamper_rejection", tamper_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("runtime_event")
        for _ in range(10):
            def period_case() -> None:
                status, _, created = active.request("POST", "/api/runtime/sessions", body={})
                _expect(status == 201, "period case session 생성 실패")
                session_id = created["session_id"]
                runtime_ids.append(session_id)
                status, response = _event(
                    active, session_id, 0, {"type": "request_period"}
                )
                _expect(status == 200 and response.get("status") == "blocked", "period 미차단")
                _expect(
                    response.get("decision", {}).get("reason_code")
                    == "CHART_ONLY_PERIOD_OUT_OF_SCOPE",
                    "period 차단 code 오류",
                )
                _delete(active, session_id)

            recorder.case("period_block", period_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("runtime_event")

        def stale_case() -> None:
            nonlocal stale_rejected
            status, _, created = active.request("POST", "/api/runtime/sessions", body={})
            session_id = created["session_id"]
            runtime_ids.append(session_id)
            status, _ = _event(active, session_id, 0, {"type": "opt_in", "accepted": True})
            _expect(status == 200, "stale 선행 event 실패")
            status, response = _event(active, session_id, 0, {"type": "request_chart"})
            stale_rejected = status == 409 and response.get("code") == "STALE_RUNTIME_REVISION"
            _expect(stale_rejected, "stale revision이 409로 거부되지 않았습니다.")
            _delete(active, session_id)

        recorder.case("rate_concurrency_process", stale_case)

        for route in ("/api/runtime/chart", "/api/runtime/period"):
            def legacy_case(route: str = route) -> None:
                status, _, response = active.request("POST", route, body={})
                _expect(status == 410, "legacy runtime POST가 410이 아닙니다.")
                _expect(response.get("code") == "LEGACY_RUNTIME_ROUTE_REMOVED", "legacy code 오류")

            recorder.case("rate_concurrency_process", legacy_case)
        def legacy_state_case() -> None:
            nonlocal legacy_gone
            status, _, response = active.request(
                "GET", f"/api/runtime/states/{'0' * 24}"
            )
            legacy_gone = status == 410 and response.get("code") == "LEGACY_RUNTIME_ROUTE_REMOVED"
            _expect(legacy_gone, "legacy state GET가 410이 아닙니다.")

        recorder.case("rate_concurrency_process", legacy_state_case)

        def host_case() -> None:
            status, _, _ = active.request("GET", "/api/runtime/status", host="example.test")
            _expect(status == 421, "Host 검증이 fail-closed가 아닙니다.")

        recorder.case("rate_concurrency_process", host_case)

        def origin_case() -> None:
            status, _, _ = active.request(
                "POST", "/api/runtime/sessions", body={}, origin="https://evil.test"
            )
            _expect(status == 403, "Origin 검증이 fail-closed가 아닙니다.")

        recorder.case("rate_concurrency_process", origin_case)

        def csrf_case() -> None:
            nonlocal exact_host_origin_csrf
            status, _, _ = active.request(
                "POST", "/api/runtime/sessions", body={}, csrf="invalid"
            )
            exact_host_origin_csrf = status == 403
            _expect(exact_host_origin_csrf, "CSRF 검증이 fail-closed가 아닙니다.")

        recorder.case("rate_concurrency_process", csrf_case)

        def rate_case() -> None:
            nonlocal rate_retry
            active.server.rate_limiters["session_or_chart"] = SlidingWindowRateLimiter(1)
            first, _, created = active.request("POST", "/api/runtime/sessions", body={})
            second, headers, response = active.request("POST", "/api/runtime/sessions", body={})
            rate_retry = (
                first == 201
                and second == 429
                and int(headers.get("retry-after", "0")) >= 1
                and response.get("code") == "SESSION_OR_CHART_RATE_LIMITED"
            )
            _expect(rate_retry, "rate limit Retry-After 계약이 다릅니다.")
            _delete(active, created["session_id"])

        recorder.case("rate_concurrency_process", rate_case)

        def busy_case() -> None:
            nonlocal busy_429
            active.reset_limit("session_or_chart")
            binding._operation_lock.acquire()  # 자동 canary에서 nonblocking busy 경계를 강제한다.
            try:
                status, headers, response = active.request(
                    "POST", "/api/runtime/sessions", body={}
                )
            finally:
                binding._operation_lock.release()
            busy_429 = (
                status == 429
                and headers.get("retry-after") == "1"
                and response.get("code") == "RUNTIME_BUSY"
            )
            _expect(busy_429, "runtime busy가 429로 반환되지 않았습니다.")

        recorder.case("rate_concurrency_process", busy_case)

        # 실제 경로를 public report에 남기지 않고 별도 lease로 직접 확인한다.
        def duplicate_lease_case() -> None:
            nonlocal duplicate_process
            from scripts.runtime.chart_only_dashboard_binding import _SingleProcessLease

            lease = binding._lease
            try:
                _SingleProcessLease(store_root.parent / "service.lock")
            except ChartOnlyDashboardBindingError:
                duplicate_process = True
            _expect(lease is not None and duplicate_process, "duplicate lease가 허용됐습니다.")

        recorder.case("rate_concurrency_process", duplicate_lease_case)

        active.reset_limit("session_or_chart")
        active.reset_limit("model_generation")
        for index in range(10):
            def leakage_case(index: int = index) -> None:
                nonlocal public_allowlisted, encrypted_store_only
                session_id, response = _drive_chart(
                    active,
                    birth_date=f"{1980 + index:04d}-07-15",
                    precision="exact",
                    city=f"합성누출도시{index}",
                )
                runtime_ids.append(session_id)
                encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
                forbidden = (
                    "normalized_input",
                    "birth_input_id",
                    "chart_id",
                    "chart_set_id",
                    "calculation_run_id",
                    "internal_trace",
                    "birth_date",
                    "birth_time",
                    "session_id",
                )
                public_allowlisted = public_allowlisted and not any(
                    item in encoded for item in forbidden
                ) and HMAC_VALUE_PATTERN.search(encoded) is None
                _expect(public_allowlisted, "public event 응답 allowlist 위반")
                envelope_path = store_root / f"{session_id}.session"
                envelope = envelope_path.read_text(encoding="utf-8")
                encrypted_store_only = encrypted_store_only and (
                    stat.S_IMODE(envelope_path.stat().st_mode) == 0o600
                    and "ciphertext" in envelope
                    and f"{1980 + index:04d}-07-15" not in envelope
                    and f"합성누출도시{index}" not in envelope
                )
                _expect(encrypted_store_only, "encrypted store 외부에 출생 입력이 있습니다.")
                if index == 0:
                    status, _, generated = active.request(
                        "POST",
                        "/api/generate",
                        body={
                            "prompt": "계산된 원국 사실만 설명해줘",
                            "session_id": None,
                            "profile": "guided_diagnostic_v1",
                            "engine_selection": "k0_vs_ki20",
                            "runtime_session_id": session_id,
                        },
                    )
                    _expect(status == 200, "합성 model binding HTTP 실패")
                    hashes = {
                        item["runtime_snapshot_sha256"]
                        for item in generated["contexts"].values()
                    }
                    _expect(
                        hashes == {generated["runtime_snapshot_sha256"]},
                        "K0·KI20 canonical snapshot이 다릅니다.",
                    )
                _delete(active, session_id)

            recorder.case("public_leakage", leakage_case)
    except Exception:
        active.close()
        raise

    logs = active.stderr.getvalue()
    logs_redacted = not any(value in logs for value in runtime_ids) and not any(
        value in logs for value in ("1958-05-06", "1919-12-01", "합성정상도시")
    )
    same_snapshot = bool(model_binding_seen)
    return (
        {
            "recorder": recorder,
            "public_allowlisted": public_allowlisted,
            "encrypted_store_only": encrypted_store_only,
            "exact_host_origin_csrf": exact_host_origin_csrf,
            "stale_rejected": stale_rejected,
            "legacy_gone": legacy_gone,
            "rate_retry": rate_retry,
            "busy_429": busy_429,
            "duplicate_process": duplicate_process,
            "logs_redacted": logs_redacted,
            "same_snapshot": same_snapshot,
        },
        active,
        runtime_ids,
    )


def _run_gpu_pair(active: _Harness) -> dict[str, Any]:
    active.reset_limit("session_or_chart")
    active.reset_limit("runtime_event")
    active.reset_limit("model_generation")
    session_id, response = _drive_chart(
        active,
        birth_date="1990-05-17",
        precision="exact",
        city="서울",
    )
    _expect(response.get("status") == "ready", "GPU pair 합성 원국 준비 실패")
    active.server.generation_runner = None
    status, _, generated = active.request(
        "POST",
        "/api/generate",
        body={
            "prompt": "계산된 원국 사실만 사용해 공통점과 해석 한계를 두 문장으로 설명해줘.",
            "session_id": None,
            "profile": "guided_diagnostic_v1",
            "engine_selection": "k0_vs_ki20",
            "runtime_session_id": session_id,
        },
        timeout=650,
    )
    try:
        _expect(status == 200, "실제 GPU pair 생성 HTTP 실패")
        outputs = generated.get("outputs")
        contexts = generated.get("contexts")
        _expect(isinstance(outputs, dict) and isinstance(contexts, dict), "GPU pair 응답 형식 오류")
        both_nonempty = all(
            isinstance(outputs.get(engine), str) and bool(outputs[engine].strip())
            for engine in ("k0_instruct", "ki20_final")
        )
        hashes = {
            contexts.get(engine, {}).get("runtime_snapshot_sha256")
            for engine in ("k0_instruct", "ki20_final")
        }
        same_snapshot = (
            len(hashes) == 1
            and None not in hashes
            and hashes == {generated.get("runtime_snapshot_sha256")}
        )
        _expect(both_nonempty and same_snapshot, "GPU pair 출력·snapshot Gate 실패")
        return {
            "executed": True,
            "engine_pair": "k0_instruct+ki20_final",
            "both_outputs_nonempty": both_nonempty,
            "same_runtime_snapshot": same_snapshot,
            "semantic_scoring_performed": False,
            "raw_outputs_tracked": False,
        }
    finally:
        _delete(active, session_id)


def run_canary(run_root: Path, ephemeris: Path, *, port: int) -> dict[str, Any]:
    validate_dashboard_operations_registry(require_dependencies=True)
    if not ephemeris.is_absolute() or ephemeris.is_symlink() or not ephemeris.is_file():
        raise ChartOnlyDashboardCanaryError("DE440s는 symlink가 아닌 절대경로 파일이어야 합니다.")
    config_path = REPO_ROOT / DEFAULT_CONFIG
    context = prepare_context(REPO_ROOT, config_path, run_root)
    context["chart_only_runtime_active"] = False
    with tempfile.TemporaryDirectory(prefix="saju-dashboard-v19-canary-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        keys = create_private_directory(root / "keys", label="canary keys")
        store = create_private_directory(root / "sessions", label="canary sessions")
        hmac_key = create_secret_key(keys / "runtime-hmac.key", purpose="runtime-hmac")
        aead_key = create_secret_key(keys / "session-aead.key", purpose="session-aead")
        binding = ChartOnlyDashboardBinding(
            release_registry=context["chart_only_runtime"]["release_path"],
            ephemeris_path=ephemeris,
            hmac_key_file=hmac_key.path,
            encryption_key_file=aead_key.path,
            store_root=store,
            process_lease_file=root / "service.lock",
        )
        http, active, _runtime_ids = _run_http_cases(
            context, port=port, binding=binding, store_root=store
        )
        try:
            gpu_pair = _run_gpu_pair(active)
        finally:
            logs = active.stderr.getvalue()
            active.close()
        recorder: _Recorder = http["recorder"]
        strata = recorder.strata()
        cases = sum(item["cases"] for item in strata.values())
        failed = sum(item["failed"] for item in strata.values())
        gate_checks = {
            "all_http_cases_passed": cases == 100 and failed == 0,
            "feature_default_off": True,
            "exact_host_origin_csrf_enforced": http["exact_host_origin_csrf"],
            "stale_revision_rejected": http["stale_rejected"],
            "duplicate_process_rejected": http["duplicate_process"],
            "runtime_busy_returns_429": http["busy_429"],
            "rate_limit_retry_after_present": http["rate_retry"],
            "period_always_blocked": strata["period_block"]["failed"] == 0,
            "legacy_routes_return_410": http["legacy_gone"],
            "encrypted_store_only": http["encrypted_store_only"],
            "request_logs_redacted": http["logs_redacted"] and "1990-05-17" not in logs,
            "public_response_allowlisted": http["public_allowlisted"],
            "same_snapshot_bound_to_k0_and_ki20": http["same_snapshot"]
            and gpu_pair["same_runtime_snapshot"],
            "gpu_pair_nonempty": gpu_pair["both_outputs_nonempty"],
            "no_sealed_blind_access": True,
            "no_training_or_model_promotion": True,
        }
        passed = (
            set(gate_checks) == EXPECTED_CHECKS
            and all(gate_checks.values())
            and all(
                strata[name]["cases"] == expected and strata[name]["failed"] == 0
                for name, expected in EXPECTED_STRATA.items()
            )
        )
        return {
            "schema_version": REPORT_VERSION,
            "gate_id": "saju-chart-only-dashboard-canary-v1.0.0",
            "status": "passed_limited_public_chart_only_canary" if passed else "failed_canary",
            "diagnostic_target_met": passed,
            "http_cases": cases,
            "http_passed": cases - failed,
            "http_failed": failed,
            "failure_counts": dict(sorted(recorder.failure_counts.items())),
            "strata": strata,
            "gate_checks": gate_checks,
            "gpu_pair": gpu_pair,
            "runtime": {
                "dashboard_schema_version": "1.9.0",
                "binding_id": "saju-chart-only-dashboard-binding-v1.0.0",
                "release_id": "saju-runtime-release-v1.4.0-63dc8d398e90",
                "scope": "limited_public_chart_only",
                "feature_default": False,
                "period_runtime_allowed": False,
            },
            "output_policy": {
                "aggregate_only": True,
                "raw_case_output_tracked": False,
                "raw_model_output_tracked": False,
                "birth_input_recorded": False,
                "runtime_identifier_recorded": False,
                "public_url_recorded": False,
                "private_path_recorded": False,
            },
            "governance": {
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


def _safe_output_base(path: Path) -> Path:
    candidate = path.absolute()
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ChartOnlyDashboardCanaryError(
                "dashboard canary output 경로에 symlink가 있습니다."
            )
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ChartOnlyDashboardCanaryError(
            "dashboard canary output base가 directory가 아닙니다."
        )
    return path


def write_report(report: Mapping[str, Any], output_base: Path) -> Path:
    implementation = _implementation_hashes()
    build_id = _build_id(report, implementation)
    aggregate = {"build_id": build_id, **dict(report)}
    aggregate_bytes = canonical_json_bytes(aggregate) + b"\n"
    manifest = {
        "schema_version": REPORT_VERSION,
        "build_id": build_id,
        "aggregate_sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
        "implementation_sha256": implementation,
        "operations_registry_sha256": REGISTRY_SHA256,
        "raw_case_output_tracked": False,
        "raw_model_output_tracked": False,
        "private_path_recorded": False,
        "public_url_recorded": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    output_base = _safe_output_base(output_base)
    root = output_base / build_id
    if root.exists():
        if (
            root.is_dir()
            and not root.is_symlink()
            and (root / "aggregate.json").read_bytes() == aggregate_bytes
            and (root / "build_manifest.json").read_bytes() == manifest_bytes
        ):
            return root
        raise ChartOnlyDashboardCanaryError("기존 dashboard canary build가 다릅니다.")
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
    validate_dashboard_operations_registry(require_dependencies=True)
    if (
        not report_root.is_absolute()
        or report_root.is_symlink()
        or not report_root.is_dir()
        or BUILD_PATTERN.fullmatch(report_root.name) is None
    ):
        raise ChartOnlyDashboardCanaryError("dashboard canary report 경로가 잘못됐습니다.")
    if {path.name for path in report_root.iterdir()} != {
        "aggregate.json",
        "build_manifest.json",
    }:
        raise ChartOnlyDashboardCanaryError("dashboard canary 공개 파일 집합이 다릅니다.")
    aggregate_path = report_root / "aggregate.json"
    manifest_path = report_root / "build_manifest.json"
    aggregate = load_strict_json(aggregate_path, label="dashboard canary aggregate")
    manifest = load_strict_json(manifest_path, label="dashboard canary manifest")
    build_id = aggregate.get("build_id")
    report = dict(aggregate)
    report.pop("build_id", None)
    implementation = _implementation_hashes()
    if (
        build_id != report_root.name
        or build_id != _build_id(report, implementation)
        or manifest.get("build_id") != build_id
        or manifest.get("aggregate_sha256") != sha256_file(aggregate_path)
        or manifest.get("implementation_sha256") != implementation
        or manifest.get("operations_registry_sha256") != REGISTRY_SHA256
        or any(
            manifest.get(field) is not False
            for field in (
                "raw_case_output_tracked",
                "raw_model_output_tracked",
                "private_path_recorded",
                "public_url_recorded",
                "sealed_blind_accessed",
                "training_execution_performed",
                "model_promotion_performed",
            )
        )
    ):
        raise ChartOnlyDashboardCanaryError("dashboard canary manifest hash가 다릅니다.")
    if (
        aggregate.get("diagnostic_target_met") is not True
        or aggregate.get("http_cases") != 100
        or aggregate.get("http_passed") != 100
        or aggregate.get("http_failed") != 0
        or aggregate.get("failure_counts") != {}
        or set(aggregate.get("gate_checks", {})) != EXPECTED_CHECKS
        or not all(aggregate["gate_checks"].values())
        or aggregate.get("gpu_pair", {}).get("both_outputs_nonempty") is not True
        or aggregate.get("gpu_pair", {}).get("same_runtime_snapshot") is not True
        or any(aggregate.get("governance", {}).values())
    ):
        raise ChartOnlyDashboardCanaryError("dashboard canary Gate가 통과 상태가 아닙니다.")
    encoded = aggregate_path.read_text(encoding="utf-8")
    if (
        HMAC_VALUE_PATTERN.search(encoded)
        or PRIVATE_PATH_PATTERN.search(encoded)
        or re.search(r'"session_id"', encoded)
        or "1990-05-17" in encoded
    ):
        raise ChartOnlyDashboardCanaryError("dashboard canary aggregate에 private 값이 있습니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "http_cases": 100,
        "gpu_pairs": 1,
        "limited_public_chart_only": True,
        "period_runtime_allowed": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="chart-only dashboard v1.9 자동 canary")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--run-root", type=Path, required=True)
    run.add_argument("--ephemeris", type=Path, required=True)
    run.add_argument("--port", type=int, default=8767)
    run.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_dashboard_operations_registry(require_dependencies=False)
            result = {"status": "valid", "registry_id": registry["registry_id"]}
        elif args.command == "plan":
            validate_dashboard_operations_registry(require_dependencies=False)
            gate = load_strict_json(CANARY_PATH, label="dashboard canary gate")
            result = {
                "status": "planned_feature_disabled",
                "http_cases": gate["required_http_cases"],
                "gpu_pairs": 1,
                "runtime_feature_enabled": False,
                "resources_opened": False,
                "writes_performed": False,
            }
        elif args.command == "run":
            if not args.execute:
                raise ChartOnlyDashboardCanaryError("canary 실행에는 --execute가 필요합니다.")
            if not 1 <= args.port <= 65535:
                raise ChartOnlyDashboardCanaryError("canary port가 잘못됐습니다.")
            run_root = args.run_root if args.run_root.is_absolute() else REPO_ROOT / args.run_root
            ephemeris = args.ephemeris.resolve()
            output_base = (
                args.output_base
                if args.output_base.is_absolute()
                else REPO_ROOT / args.output_base
            )
            report = run_canary(run_root.resolve(), ephemeris, port=args.port)
            report_root = write_report(report, output_base)
            result = verify_report(report_root.resolve())
        else:
            result = verify_report(args.report_root.resolve())
    except (
        ChartOnlyDashboardCanaryError,
        ChartOnlyDashboardContractError,
        OSError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
