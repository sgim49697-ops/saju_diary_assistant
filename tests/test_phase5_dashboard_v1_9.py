# test_phase5_dashboard_v1_9.py - 공개 chart-only dashboard v1.9의 HTTP·보안·모델 결합 계약을 검증한다.

from __future__ import annotations

import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.runtime.chart_only_dashboard_binding import (
    BINDING_ID,
    ChartOnlyDashboardBindingError,
    _SingleProcessLease,
)
from scripts.training.phase5_dashboard_v1_9 import (
    V19_ASSET_ROOT,
    DashboardHTTPServer,
    SlidingWindowRateLimiter,
    _runtime_model_context_from_binding,
)
from tests.test_phase5_dashboard import DashboardFixture

SESSION_ID = "c" * 24
SNAPSHOT_SHA256 = "d" * 64
CAPABILITY_SHA256 = "e" * 64


def _snapshot() -> dict[str, object]:
    value = {
        "chart": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "pillars": {
                    "year": "庚午",
                    "month": "辛巳",
                    "day": "壬辰",
                    "hour": "丁未",
                },
                "day_master": "壬",
                "surface_five_elements": {"목": 0, "화": 3, "토": 2, "금": 2, "수": 1},
                "calculation_profile": "KR_CIVIL_MIDNIGHT_V1",
                "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
            },
            "message": "합성 원국 계산 완료",
            "limitations": [],
        }
    }
    import hashlib

    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": "1.0.0",
        "binding_id": BINDING_ID,
        "capability_sha256": CAPABILITY_SHA256,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 7,
        "value": value,
    }


class FakeBinding:
    def __init__(self) -> None:
        self.revision = 0
        self.deleted = False
        self.closed = False

    def status(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "status": "limited_public_chart_only_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "활성",
            "release_id": "saju-runtime-release-v1.4.0-63dc8d398e90",
            "adapter_version": "saju-chart-only-app-adapter-v1.0.0",
            "binding_id": BINDING_ID,
            "facts_rendered_without_model": True,
            "period_calculation_allowed": False,
            "production_application_binding": True,
            "model_context_binding": True,
            "state_encrypted": True,
            "retention_seconds": 1800,
            "client_authentication_required": False,
            "request_metadata_logged": True,
            "request_bodies_logged": False,
            "feature_default": False,
        }

    def create_session(self) -> dict[str, object]:
        self.deleted = False
        self.revision = 0
        return {
            "status": "created",
            "session_id": SESSION_ID,
            "state_revision": 0,
            "expires_in_seconds": 1800,
            "governance": {"production_application_binding": True},
        }

    def handle_event(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event: dict[str, object],
    ) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted:
            raise ChartOnlyDashboardBindingError(404, "RUNTIME_SESSION_NOT_FOUND", "없음")
        if expected_revision != self.revision:
            raise ChartOnlyDashboardBindingError(409, "STALE_RUNTIME_REVISION", "stale")
        if event.get("type") != "request_period":
            self.revision += 1
        blocked = event.get("type") == "request_period"
        return {
            "status": "blocked" if blocked else "ready",
            "state_revision": self.revision,
            "decision": {
                "action": "reject_period" if blocked else "render_chart",
                "message": "기간 차단" if blocked else "완료",
                "reason_code": "CHART_ONLY_PERIOD_OUT_OF_SCOPE" if blocked else None,
            },
            "result": None if blocked else _snapshot()["value"]["chart"],
            "governance": {"production_application_binding": True},
        }

    def delete_session(self, session_id: str) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted:
            raise ChartOnlyDashboardBindingError(404, "RUNTIME_SESSION_NOT_FOUND", "없음")
        self.deleted = True
        return {"status": "deleted", "retained": False, "governance": {}}

    def public_snapshot(self, session_id: str) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted:
            raise ChartOnlyDashboardBindingError(404, "RUNTIME_SESSION_NOT_FOUND", "없음")
        return _snapshot()

    def close(self) -> None:
        self.closed = True


class DashboardV19HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        fixture = DashboardFixture(Path(self.temporary.name))
        self.context = fixture.context()
        config = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.9.0.json"
            ).read_text(encoding="utf-8")
        )
        self.context["config"] = config
        self.context["chart_only_runtime"] = {
            "contract": config["chart_only_runtime"],
            "release": {"release_id": config["chart_only_runtime"]["release_id"]},
        }
        self.context["chart_only_runtime_active"] = True
        self.binding = FakeBinding()
        self.received_bindings: list[dict[str, object]] = []

        def generation_runner(
            _context: dict[str, object],
            _prompt: str,
            _session_id: str | None,
            _profile: str | None,
            _selection: str | None,
            _legacy_runtime_id: str | None,
            runtime_binding: dict[str, object] | None,
        ) -> dict[str, object]:
            assert runtime_binding is not None
            self.received_bindings.append(runtime_binding)
            snapshot_sha256 = runtime_binding["snapshot_sha256"]
            return {
                "status": "generated",
                "persisted": True,
                "local_only": True,
                "session_id": "a" * 24,
                "runtime_binding_applied": True,
                "runtime_snapshot_sha256": snapshot_sha256,
                "outputs": {"k0_instruct": "K0 합성", "ki20_final": "KI20 합성"},
                "contexts": {
                    "k0_instruct": {"runtime_snapshot_sha256": snapshot_sha256},
                    "ki20_final": {"runtime_snapshot_sha256": snapshot_sha256},
                },
            }

        self.stderr = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0),
            self.context,
            V19_ASSET_ROOT,
            "a" * 48,
            chart_only_binding=self.binding,
            chart_only_runtime_requested=True,
            generation_runner=generation_runner,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.redirect.__exit__(None, None, None)
        self.temporary.cleanup()

    def request(
        self, method: str, path: str, *, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, str], dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Host": f"127.0.0.1:{self.port}",
            "X-CSRF-Token": "a" * 48,
            "Origin": f"http://127.0.0.1:{self.port}",
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_text": raw.decode("utf-8")}
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, payload

    def test_structured_session_revision_delete_and_legacy_routes(self) -> None:
        status, _, payload = self.request("GET", "/api/runtime/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        self.assertFalse(payload["client_authentication_required"])

        status, _, created = self.request("POST", "/api/runtime/sessions", body={})
        self.assertEqual(status, 201)
        self.assertEqual(created["session_id"], SESSION_ID)
        status, _, response = self.request(
            "POST",
            f"/api/runtime/sessions/{SESSION_ID}/events",
            body={"expected_revision": 0, "event": {"type": "request_chart"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["state_revision"], 1)
        self.assertNotIn("session_id", response)
        status, _, stale = self.request(
            "POST",
            f"/api/runtime/sessions/{SESSION_ID}/events",
            body={"expected_revision": 0, "event": {"type": "request_chart"}},
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale["code"], "STALE_RUNTIME_REVISION")
        for path in ("/api/runtime/chart", "/api/runtime/period"):
            status, _, payload = self.request("POST", path, body={})
            self.assertEqual(status, 410)
            self.assertEqual(payload["code"], "LEGACY_RUNTIME_ROUTE_REMOVED")
        status, _, _ = self.request("DELETE", f"/api/runtime/sessions/{SESSION_ID}")
        self.assertEqual(status, 200)

    @patch("scripts.training.phase5_dashboard_v1_9._generation_gate")
    def test_model_pair_receives_one_canonical_snapshot(self, gate: object) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        self.request("POST", "/api/runtime/sessions", body={})
        status, _, payload = self.request(
            "POST",
            "/api/generate",
            body={
                "prompt": "원국 사실만 설명해줘",
                "session_id": None,
                "profile": "guided_diagnostic_v1",
                "engine_selection": "k0_vs_ki20",
                "runtime_session_id": SESSION_ID,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self.received_bindings), 1)
        hashes = {
            item["runtime_snapshot_sha256"]
            for item in payload["contexts"].values()
        }
        self.assertEqual(hashes, {payload["runtime_snapshot_sha256"]})
        self.assertNotIn(SESSION_ID, json.dumps(self.received_bindings))

    def test_rate_limit_returns_retry_after_and_logs_redact_capability(self) -> None:
        self.server.rate_limiters["session_or_chart"] = SlidingWindowRateLimiter(1)
        status, _, _ = self.request("POST", "/api/runtime/sessions", body={})
        self.assertEqual(status, 201)
        status, headers, payload = self.request("POST", "/api/runtime/sessions", body={})
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(headers["retry-after"]), 1)
        self.assertEqual(payload["code"], "SESSION_OR_CHART_RATE_LIMITED")
        self.server.rate_limiters["runtime_event"] = SlidingWindowRateLimiter(300)
        self.request("DELETE", f"/api/runtime/sessions/{SESSION_ID}")
        logs = self.stderr.getvalue()
        self.assertNotIn(SESSION_ID, logs)
        self.assertIn("/api/runtime/sessions/{opaque_id}", logs)

    def test_versioned_assets_remove_period_coordinates_and_visible_id(self) -> None:
        status, _, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        html = (V19_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V19_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        for forbidden in (
            "runtime-longitude",
            "runtime-latitude",
            "runtime-gender",
            "runtime-period-form",
            "runtime-session-id",
            "/api/runtime/chart",
            "/api/runtime/period",
            "/api/runtime/states/",
        ):
            self.assertNotIn(forbidden, html + javascript)


class DashboardV19BindingContractTests(unittest.TestCase):
    def test_model_snapshot_validator_is_hash_bound_and_allowlisted(self) -> None:
        snapshot = _snapshot()
        prompt, digest, capability = _runtime_model_context_from_binding(snapshot)
        self.assertIn("두 비교 모델에는 같은 snapshot", prompt)
        self.assertEqual(digest, snapshot["snapshot_sha256"])
        self.assertEqual(capability, CAPABILITY_SHA256)
        tampered = json.loads(json.dumps(snapshot))
        tampered["value"]["chart"]["hard_facts"]["birth_date"] = "1990-01-01"
        with self.assertRaisesRegex(RuntimeError, "금지된 내부 field"):
            _runtime_model_context_from_binding(tampered)

    def test_single_process_lease_rejects_second_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            first = _SingleProcessLease(root / "service.lock")
            try:
                with self.assertRaisesRegex(
                    ChartOnlyDashboardBindingError, "이미 실행 중"
                ):
                    _SingleProcessLease(root / "service.lock")
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
