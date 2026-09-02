# test_phase5_dashboard_v1_11.py - 원국·단일 일진 연결 UI와 grounding 경계를 검증한다.

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.runtime.chart_day_dashboard_binding import (
    BINDING_ID,
    ChartDayDashboardBindingError,
)
from scripts.training.phase5_dashboard_v1_11 import (
    V111_ASSET_ROOT,
    DashboardHTTPServer,
    Phase5DashboardError,
    _runtime_model_context_from_binding,
    evaluate_bound_output,
    execute_manual_generation,
    main,
    serve,
    validate_config,
)
from tests.test_phase5_dashboard import DashboardFixture

SESSION_ID = "c" * 24
CAPABILITY_SHA256 = "e" * 64


def _binding(target_date: str = "2026-09-02") -> dict[str, object]:
    value = {
        "chart": {
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
                "surface_five_elements": {
                    "목": 0,
                    "화": 3,
                    "토": 2,
                    "금": 2,
                    "수": 1,
                },
                "calculation_profile": "KR_CIVIL_MIDNIGHT_V1",
                "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
            },
            "message": "합성 원국 계산 완료",
            "limitations": [],
        },
        "period": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "period": {
                    "period_type": "day",
                    "target_date": target_date,
                    "start_date": target_date,
                    "end_date": target_date,
                    "timezone": "Asia/Seoul",
                    "evaluation_local_time": "12:00",
                },
                "pillars": {
                    "year": {"ganzhi": "丙午"},
                    "month": {"ganzhi": "丙申"},
                    "day": {"ganzhi": "己卯"},
                },
                "day_assignment_evidence": {
                    "authority": "SOURCE_HARD_FACT",
                    "future_physical_instant_claimed": False,
                },
            },
            "message": "합성 단일 일진 계산 완료",
            "limitations": ["단일 날짜 12:00 기준"],
        },
    }
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": "1.1.0",
        "binding_id": BINDING_ID,
        "capability_sha256": CAPABILITY_SHA256,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 8,
        "value": value,
    }


def _generated(output: str) -> dict[str, object]:
    return {
        "output": output,
        "input_tokens": 100,
        "omitted_messages": 0,
        "peak_allocated_bytes": 0,
        "gpu_total_memory_used_mib": 0,
    }


def _dashboard_context(root: Path) -> dict[str, object]:
    fixture = DashboardFixture(root)
    context = fixture.context()
    config = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.11.0.json"
        ).read_text(encoding="utf-8")
    )
    context["config"] = config
    context["prompt_profiles"] = {
        "default_profile": "guided_diagnostic_v1",
        "bound_profile": "bound_chart_v1",
        "legacy_profile": "raw_legacy",
        "profiles": {
            "guided_diagnostic_v1": {
                "label": "안내 보정 진단",
                "description": "진단",
                "system_prompt_text": "출생정보 intake",
                "system_prompt_sha256": "a" * 64,
                "production_like": False,
                "diagnostic_only": True,
            },
            "bound_chart_v1": {
                "label": "승인 원국·날짜 연결",
                "description": "원국·단일 일진 연결",
                "system_prompt_text": (
                    "연결된 원국과 선택 날짜를 사용하고 출생정보를 다시 묻지 마세요."
                ),
                "system_prompt_sha256": "b" * 64,
                "production_like": True,
                "diagnostic_only": False,
            },
            "raw_no_system": {
                "label": "무지시 원출력",
                "description": "진단",
                "system_prompt_text": None,
                "system_prompt_sha256": None,
                "production_like": False,
                "diagnostic_only": True,
            },
        },
    }
    context["chart_only_runtime_active"] = True
    return context


class DashboardV111GroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.context = _dashboard_context(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_committed_config_assets_and_server_date_contract_are_versioned(self) -> None:
        validate_config(self.context["config"])
        html = (V111_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V111_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('id="runtime-target-date"', html)
        self.assertIn('id="runtime-today-button"', html)
        self.assertIn("이 원국·날짜로 새 대화 시작", html)
        self.assertIn('type: "request_period"', javascript)
        self.assertIn("single_day_today_kst", javascript)
        self.assertIn("connectedRuntimeDate", javascript)
        self.assertNotIn("new Date().toISOString", javascript)

    def test_model_binding_requires_hash_bound_exact_chart_and_single_day(self) -> None:
        binding = _binding()
        prompt, digest, capability = _runtime_model_context_from_binding(binding)
        self.assertIn("원국·단일 일진", prompt)
        self.assertIn("2026-09-02", prompt)
        self.assertEqual(digest, binding["snapshot_sha256"])
        self.assertEqual(capability, CAPABILITY_SHA256)

        missing_period = json.loads(json.dumps(binding))
        missing_period["value"]["period"] = None
        with self.assertRaisesRegex(Phase5DashboardError, "단일 일진"):
            _runtime_model_context_from_binding(missing_period)

        tampered = json.loads(json.dumps(binding))
        tampered["value"]["period"]["hard_facts"]["birth_date"] = "1990-01-01"
        with self.assertRaisesRegex(Phase5DashboardError, "금지된 내부 field"):
            _runtime_model_context_from_binding(tampered)

    def test_grounding_gate_requires_chart_and_selected_date_facts(self) -> None:
        binding = _binding()
        natal_only = evaluate_bound_output(
            "내 원국 네 기둥을 설명해줘.",
            "연결된 원국의 일주는 壬辰입니다.",
            binding,
        )
        self.assertTrue(natal_only["passed"])
        self.assertNotIn("period_fact_missing", natal_only["reasons"])

        passed = evaluate_bound_output(
            "내 오늘 사주 봐줄래?",
            "연결된 壬辰 원국과 2026-09-02의 己卯 일진을 함께 살펴보겠습니다.",
            binding,
        )
        self.assertTrue(passed["passed"])
        chart_only = evaluate_bound_output(
            "내 오늘 사주 봐줄래?",
            "연결된 壬辰 원국을 중심으로 살펴보겠습니다.",
            binding,
        )
        self.assertIn("period_fact_missing", chart_only["reasons"])
        period_only = evaluate_bound_output(
            "내 오늘 사주 봐줄래?",
            "2026-09-02의 己卯 일진을 살펴보겠습니다.",
            binding,
        )
        self.assertIn("chart_fact_missing", period_only["reasons"])

    @patch("scripts.training.phase5_dashboard_v1_11._generate_conversation")
    @patch("scripts.training.phase5_dashboard_v1_11._generation_gate")
    def test_failed_output_is_corrected_without_persistence(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        bad = "생년월일과 출생시간을 알려주시면 사주를 봐드릴게요."
        good = "연결된 壬辰 원국과 2026-09-02 일진을 함께 살펴보겠습니다."
        generate.side_effect = [_generated(bad), _generated(good)]
        result = execute_manual_generation(
            self.context,
            "내 오늘 사주 봐줄래?",
            runtime_binding=_binding(),
        )
        self.assertEqual(result["session"]["schema_version"], "1.5.0")
        self.assertEqual(result["session"]["prompt_profile"], "bound_chart_v1")
        self.assertEqual(
            result["grounding_gate"]["retries_by_engine"], {"ki20_final": 1}
        )
        stored = json.dumps(result["session"], ensure_ascii=False)
        self.assertIn(good, stored)
        self.assertNotIn(bad, stored)

    @patch("scripts.training.phase5_dashboard_v1_11._generate_conversation")
    @patch("scripts.training.phase5_dashboard_v1_11._generation_gate")
    def test_existing_conversation_rejects_selected_date_snapshot_swap(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.return_value = _generated(
            "연결된 壬辰 원국과 2026-09-02 일진을 함께 살펴보겠습니다."
        )
        first = execute_manual_generation(
            self.context,
            "내 오늘 사주 봐줄래?",
            runtime_binding=_binding(),
        )
        with self.assertRaisesRegex(Phase5DashboardError, "snapshot은 변경"):
            execute_manual_generation(
                self.context,
                "다른 날도 봐줘",
                session_id=first["session_id"],
                runtime_binding=_binding("2026-09-03"),
            )
        self.assertEqual(generate.call_count, 1)


class _FakeBinding:
    def __init__(self) -> None:
        self.revision = 0
        self.period_ready = False
        self.deleted = False

    def status(self) -> dict[str, object]:
        return {
            "schema_version": "1.1.0",
            "status": "limited_public_chart_and_single_day_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "활성",
            "single_day_calculation_allowed": True,
            "single_day_today_kst": "2026-09-02",
            "single_day_minimum": "2026-09-02",
            "single_day_maximum": "2049-12-31",
            "single_day_evaluation_local_time": "12:00",
            "client_authentication_required": False,
        }

    def create_session(self) -> dict[str, object]:
        self.revision = 0
        self.period_ready = False
        self.deleted = False
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
        event: dict[str, object],
    ) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted:
            raise ChartDayDashboardBindingError(
                404, "RUNTIME_SESSION_NOT_FOUND", "없음"
            )
        if expected_revision != self.revision:
            raise ChartDayDashboardBindingError(409, "STALE_RUNTIME_REVISION", "stale")
        if event.get("type") != "request_period":
            raise ChartDayDashboardBindingError(
                400, "RUNTIME_REQUEST_REJECTED", "단일 일진 요청만 허용"
            )
        request = event.get("request")
        expected = {
            "period_type": "day",
            "start_date": "2026-09-02",
            "end_date": "2026-09-02",
            "timezone": "Asia/Seoul",
        }
        if request != expected:
            raise ChartDayDashboardBindingError(
                400, "RUNTIME_REQUEST_REJECTED", "구조화 날짜가 잘못됨"
            )
        self.revision += 1
        self.period_ready = True
        return {
            "status": "ready",
            "state_revision": self.revision,
            "decision": {
                "action": "render_chart_and_period",
                "message": "완료",
                "reason_code": None,
            },
            "result": _binding()["value"],
            "governance": {},
        }

    def public_snapshot(self, session_id: str) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted or not self.period_ready:
            raise ChartDayDashboardBindingError(
                409, "RUNTIME_SINGLE_DAY_REQUIRED", "단일 일진 필요"
            )
        return _binding()

    def delete_session(self, session_id: str) -> dict[str, object]:
        if session_id != SESSION_ID or self.deleted:
            raise ChartDayDashboardBindingError(
                404, "RUNTIME_SESSION_NOT_FOUND", "없음"
            )
        self.deleted = True
        return {"status": "deleted", "retained": False, "governance": {}}

    def close(self) -> None:
        return None


class DashboardV111HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.context = _dashboard_context(Path(self.temporary.name))
        self.binding = _FakeBinding()
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
            return {
                "status": "generated",
                "persisted": True,
                "local_only": True,
                "session_id": "a" * 24,
                "runtime_binding_applied": True,
                "runtime_snapshot_sha256": runtime_binding["snapshot_sha256"],
            }

        self.stderr = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0),
            self.context,
            V111_ASSET_ROOT,
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
    ) -> tuple[int, dict[str, object]]:
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
        payload = json.loads(response.read())
        status = response.status
        connection.close()
        return status, payload

    @patch("scripts.training.phase5_dashboard_v1_11._generation_gate")
    def test_structured_day_then_generation_receives_one_snapshot(
        self, gate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        status, payload = self.request("GET", "/api/runtime/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["single_day_today_kst"], "2026-09-02")
        status, _ = self.request("POST", "/api/runtime/sessions", body={})
        self.assertEqual(status, 201)
        status, day = self.request(
            "POST",
            f"/api/runtime/sessions/{SESSION_ID}/events",
            body={
                "expected_revision": 0,
                "event": {
                    "type": "request_period",
                    "request": {
                        "period_type": "day",
                        "start_date": "2026-09-02",
                        "end_date": "2026-09-02",
                        "timezone": "Asia/Seoul",
                    },
                },
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(day["result"]["period"]["fact_authority"], "HARD_GT")
        status, generated = self.request(
            "POST",
            "/api/generate",
            body={
                "prompt": "내 오늘 사주 봐줄래?",
                "session_id": None,
                "profile": "guided_diagnostic_v1",
                "engine_selection": "ki20_final",
                "runtime_session_id": SESSION_ID,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(generated["runtime_binding_applied"])
        self.assertEqual(len(self.received_bindings), 1)
        self.assertNotIn(SESSION_ID, json.dumps(self.received_bindings))

    def test_free_text_period_and_legacy_routes_are_rejected(self) -> None:
        self.request("POST", "/api/runtime/sessions", body={})
        status, payload = self.request(
            "POST",
            f"/api/runtime/sessions/{SESSION_ID}/events",
            body={
                "expected_revision": 0,
                "event": {
                    "type": "request_period",
                    "request": {"date": "오늘"},
                },
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "RUNTIME_REQUEST_REJECTED")
        for path in ("/api/runtime/chart", "/api/runtime/period"):
            status, payload = self.request("POST", path, body={})
            self.assertEqual(status, 410)
            self.assertEqual(payload["code"], "LEGACY_RUNTIME_ROUTE_REMOVED")


class DashboardV111RemoteShareTests(unittest.TestCase):
    @patch("scripts.training.phase5_dashboard_v1_11.DashboardHTTPServer")
    def test_v111_accepts_explicit_unauthenticated_https_origin(
        self, server_type: object
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = _dashboard_context(Path(directory))
            context["chart_only_runtime"] = {"asset_root": V111_ASSET_ROOT}
            server = server_type.return_value
            server.server_address = ("127.0.0.1", 8766)
            serve(
                context,
                "127.0.0.1",
                8766,
                trusted_origin="https://example.invalid",
                allow_unauthenticated_remote=True,
            )
            server.serve_forever.assert_called_once()
            server.server_close.assert_called_once()

    @patch(
        "scripts.training.phase5_dashboard_v1_11.serve",
        side_effect=KeyboardInterrupt,
    )
    @patch("scripts.training.phase5_dashboard_v1_11.prepare_context", return_value={})
    def test_cli_ctrl_c_is_a_clean_shutdown(
        self, _prepare: object, _serve: object
    ) -> None:
        self.assertEqual(
            main(
                [
                    "--config",
                    "unused.json",
                    "--run-root",
                    "runs/KI20-MIX-v2/v1.2.0/run-unused",
                    "serve",
                ]
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
