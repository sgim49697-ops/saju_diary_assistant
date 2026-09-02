# test_phase5_dashboard_v1_10.py - 명시 원국 연결과 자동 grounding Gate를 검증한다.

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

from scripts.runtime.chart_only_dashboard_binding import BINDING_ID
from scripts.training.phase5_dashboard_v1_10 import (
    GROUNDING_FAILURE_CODE,
    V110_ASSET_ROOT,
    DashboardHTTPServer,
    GroundingGateError,
    evaluate_bound_output,
    execute_manual_generation,
    manual_sessions_payload,
    validate_config,
)
from tests.test_phase5_dashboard import DashboardFixture


def _binding() -> dict[str, object]:
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
        }
    }
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": "1.0.0",
        "binding_id": BINDING_ID,
        "capability_sha256": "e" * 64,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 7,
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


class DashboardV110GroundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        fixture = DashboardFixture(Path(self.temporary.name))
        self.context = fixture.context()
        config_path = (
            Path(__file__).resolve().parents[1]
            / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.10.0.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.context["config"] = config
        self.context["prompt_profiles"] = {
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
                    "label": "승인 원국 연결",
                    "description": "원국 연결",
                    "system_prompt_text": "연결된 원국을 사용하고 출생정보를 다시 묻지 마세요.",
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
        self.context["chart_only_runtime_active"] = True

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_committed_config_and_assets_are_versioned(self) -> None:
        validate_config(self.context["config"])
        html = (V110_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V110_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("runtime-connect-button", html)
        self.assertIn("이 원국으로 새 대화 시작", html)
        self.assertIn("connectedRuntimeSessionId", javascript)
        self.assertNotIn(
            "runtime_session_id: activeRuntimeSessionId", javascript
        )

    def test_deterministic_gate_rejects_reintake_and_requires_limitation(self) -> None:
        binding = _binding()
        rejected = evaluate_bound_output(
            "내 오늘 사주 봐줄래?",
            "생년월일과 출생시간을 알려주시면 분석해드릴게요.",
            binding,
        )
        self.assertFalse(rejected["passed"])
        self.assertIn("birth_input_reasked", rejected["reasons"])
        self.assertIn("chart_fact_missing", rejected["reasons"])
        accepted = evaluate_bound_output(
            "내 오늘 사주 봐줄래?",
            "연결된 壬辰 원국을 중심으로 차분히 보겠습니다. 정확한 오늘 운세 계산은 아직 제공 범위가 아닙니다.",
            binding,
        )
        self.assertTrue(accepted["passed"])
        self.assertEqual(accepted["intent"], "period_request")

    @patch("scripts.training.phase5_dashboard_v1_10._generate_conversation")
    @patch("scripts.training.phase5_dashboard_v1_10._generation_gate")
    def test_failed_first_output_is_retried_but_not_persisted(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        bad = "생년월일과 출생시간을 알려주시면 사주를 봐드릴게요."
        good = (
            "연결된 壬辰 원국은 수 기운의 관점을 보여줍니다. "
            "정확한 오늘 운세 계산은 아직 제공 범위가 아닙니다."
        )
        generate.side_effect = [_generated(bad), _generated(good)]
        result = execute_manual_generation(
            self.context,
            "내 오늘 사주 봐줄래?",
            profile="guided_diagnostic_v1",
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
        self.assertIn(
            "다른 설명이나 질문 없이",
            generate.call_args_list[1].args[1][-1]["content"],
        )

    @patch("scripts.training.phase5_dashboard_v1_10._generate_conversation")
    @patch("scripts.training.phase5_dashboard_v1_10._generation_gate")
    def test_double_failure_does_not_create_session(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.side_effect = [
            _generated("생년월일을 알려주세요."),
            _generated("출생시간을 알려주세요."),
        ]
        with self.assertRaisesRegex(GroundingGateError, GROUNDING_FAILURE_CODE):
            execute_manual_generation(
                self.context,
                "내 원국을 설명해줘",
                runtime_binding=_binding(),
            )
        self.assertEqual(manual_sessions_payload(self.context)["items"], [])


class _FakeBinding:
    def public_snapshot(self, _session_id: str) -> dict[str, object]:
        return _binding()

    def close(self) -> None:
        return None


class DashboardV110HTTPFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        fixture = DashboardFixture(Path(self.temporary.name))
        self.context = fixture.context()
        config = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.10.0.json"
            ).read_text(encoding="utf-8")
        )
        self.context["config"] = config
        self.context["chart_only_runtime_active"] = True

        def fail_generation(*_args: object) -> dict[str, object]:
            raise GroundingGateError(GROUNDING_FAILURE_CODE)

        self.stderr = io.StringIO()
        self.redirect = contextlib.redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0),
            self.context,
            V110_ASSET_ROOT,
            "a" * 48,
            chart_only_binding=_FakeBinding(),
            chart_only_runtime_requested=True,
            generation_runner=fail_generation,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.redirect.__exit__(None, None, None)
        self.temporary.cleanup()

    @patch("scripts.training.phase5_dashboard_v1_10._generation_gate")
    def test_http_returns_safe_grounding_error(self, gate: object) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        port = self.server.server_address[1]
        body = json.dumps(
            {
                "prompt": "내 원국을 설명해줘",
                "session_id": None,
                "profile": "guided_diagnostic_v1",
                "engine_selection": "ki20_final",
                "runtime_session_id": "c" * 24,
            }
        ).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request(
            "POST",
            "/api/generate",
            body=body,
            headers={
                "Host": f"127.0.0.1:{port}",
                "Origin": f"http://127.0.0.1:{port}",
                "X-CSRF-Token": "a" * 48,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 422)
        self.assertEqual(payload["code"], GROUNDING_FAILURE_CODE)
        self.assertNotIn("birth", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
