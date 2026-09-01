# test_historical_candidate_dashboard.py - 별도 loopback 후보 화면의 API·메모리·공개 경계를 검증한다.

from __future__ import annotations

import http.client
import json
import threading
import unittest
from http import HTTPStatus

from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.training.historical_candidate_dashboard import (
    CANDIDATE_ASSET_ROOT,
    CANDIDATE_IDLE_SECONDS,
    CANDIDATE_MAX_SESSIONS,
    CandidateRequestError,
    CandidateSessionStore,
    HistoricalCandidateDashboardServer,
    candidate_status_payload,
)
from scripts.training.phase5_dashboard import _parser
from tests.test_saju_historical_candidate_intake import _candidate_result


class _FakeCandidateEngine:
    def __init__(self, signer: RuntimeIdSigner) -> None:
        self.signer = signer
        self.closed = False
        self.calls = 0

    def calculate_chart(self, arguments: dict) -> dict:
        self.calls += 1
        return _candidate_result(
            self.signer,
            {"arguments": arguments, "call_id": "scr2_" + "0" * 64},
        )

    def close(self) -> None:
        self.closed = True


class CandidateSessionStoreTests(unittest.TestCase):
    def test_store_is_bounded_and_expires_without_disk(self) -> None:
        now = [0.0]
        store = CandidateSessionStore(clock=lambda: now[0])
        for _ in range(CANDIDATE_MAX_SESSIONS):
            session_id, state = store.create()
            self.assertEqual(len(session_id), 24)
            self.assertEqual(state["state_revision"], 0)
        with self.assertRaises(CandidateRequestError) as caught:
            store.create()
        self.assertEqual(caught.exception.status, HTTPStatus.SERVICE_UNAVAILABLE)
        now[0] = float(CANDIDATE_IDLE_SECONDS)
        self.assertEqual(store.count(), 0)
        store.create()
        self.assertEqual(store.count(), 1)


class HistoricalCandidateDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RuntimeIdSigner.for_test(bytes(range(32)))
        self.engine = _FakeCandidateEngine(self.signer)
        self.server = HistoricalCandidateDashboardServer(
            ("127.0.0.1", 0),
            engine=self.engine,
            signer=self.signer,
            csrf_token="a" * 48,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        origin: str | None = None,
        csrf: str = "a" * 48,
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": f"127.0.0.1:{self.port}", "X-CSRF-Token": csrf}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Origin"] = origin or f"http://127.0.0.1:{self.port}"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def _event(self, session_id: str, event: dict) -> dict:
        status, raw = self._request(
            "POST",
            f"/api/runtime/historical-candidate/sessions/{session_id}/events",
            {"event": event},
        )
        self.assertEqual(status, HTTPStatus.OK, raw.decode("utf-8"))
        return json.loads(raw)

    def test_candidate_assets_are_versioned_and_parser_needs_no_run_root(self) -> None:
        self.assertTrue((CANDIDATE_ASSET_ROOT / "index.html").is_file())
        args = _parser().parse_args(
            [
                "serve-candidate",
                "--ephemeris",
                "/tmp/de440s.bsp",
                "--id-key-file",
                "/tmp/id.key",
            ]
        )
        self.assertIsNone(args.run_root)
        self.assertEqual(args.port, 8766)

    def test_static_page_and_status_keep_candidate_governance(self) -> None:
        status, page = self._request("GET", "/", csrf="")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn(b"a" * 48, page)
        self.assertNotIn(b"__CSRF_TOKEN__", page)
        status, raw = self._request(
            "GET", "/api/runtime/historical-candidate/status"
        )
        self.assertEqual(status, HTTPStatus.OK)
        payload = json.loads(raw)
        self.assertEqual(payload, candidate_status_payload(self.server))
        self.assertTrue(payload["loopback_only"])
        self.assertFalse(payload["disk_persistence"])
        self.assertFalse(payload["runtime_release_approved"])
        self.assertFalse(payload["production_application_binding"])
        self.assertFalse(payload["model_context_binding"])

    def test_full_http_flow_returns_facts_without_raw_input_or_runtime_ids(self) -> None:
        status, raw = self._request(
            "POST", "/api/runtime/historical-candidate/sessions", {}
        )
        self.assertEqual(status, HTTPStatus.CREATED)
        created = json.loads(raw)
        session_id = created["session_id"]
        events = (
            {"type": "opt_in", "accepted": True},
            {"type": "set_slot", "field": "birth_date", "value": "1964-09-08"},
            {"type": "set_slot", "field": "calendar", "value": "solar"},
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            {"type": "set_slot", "field": "birth_time", "value": "00:01"},
            {
                "type": "set_slot",
                "field": "birthplace",
                "value": {
                    "country_code": "KR",
                    "city": "서울",
                    "timezone": "Asia/Seoul",
                    "longitude": None,
                    "latitude": None,
                },
            },
        )
        result = created
        for event in events:
            result = self._event(session_id, event)
        self.assertEqual(result["status"], "candidate_ready")
        self.assertEqual(result["result"]["fact_authority"], "HARD_CANDIDATE")
        self.assertEqual(self.engine.calls, 1)
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in (
            "1964-09-08",
            "00:01",
            "normalized_input",
            "birth_input_id",
            "chart_id",
            "chart_set_id",
            "calculation_run_id",
            "internal_trace",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertNotIn("calendar", result["result"]["hard_facts"])

    def test_period_internal_result_get_and_cross_origin_are_blocked(self) -> None:
        status, raw = self._request(
            "POST", "/api/runtime/historical-candidate/sessions", {}
        )
        session_id = json.loads(raw)["session_id"]
        period = self._event(session_id, {"type": "request_period"})
        self.assertEqual(
            period["decision"]["reason_code"], "CANDIDATE_PERIOD_OUT_OF_SCOPE"
        )
        status, _ = self._request(
            "GET", f"/api/runtime/historical-candidate/sessions/{session_id}"
        )
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        status, _ = self._request(
            "POST",
            f"/api/runtime/historical-candidate/sessions/{session_id}/events",
            {"event": {"type": "chart_result", "result": {}}},
        )
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        status, _ = self._request(
            "POST",
            f"/api/runtime/historical-candidate/sessions/{session_id}/events",
            {"event": {"type": "request_chart"}},
            origin="http://evil.example",
        )
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_host_and_csrf_are_fail_closed(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/", headers={"Host": "evil.example"})
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, HTTPStatus.MISDIRECTED_REQUEST)
        connection.close()
        status, _ = self._request(
            "GET", "/api/runtime/historical-candidate/status", csrf="wrong"
        )
        self.assertEqual(status, HTTPStatus.FORBIDDEN)

    def test_server_close_closes_only_its_candidate_engine(self) -> None:
        self.assertFalse(self.engine.closed)
        self.server.server_close()
        self.assertTrue(self.engine.closed)


if __name__ == "__main__":
    unittest.main()
