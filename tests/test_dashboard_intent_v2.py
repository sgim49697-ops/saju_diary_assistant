# test_dashboard_intent_v2.py - 활성 요청·읽기 전용 맥락·동일 세션·CPU HTTP 경로를 검증한다.

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.training import dashboard_grounding_v3 as parent
from scripts.training import dashboard_grounding_v4 as policy
from scripts.training import phase5_dashboard_v1_17 as dashboard
from tests.test_dashboard_grounding_v3 import binding_fixture
from tests.test_phase5_dashboard_v1_16 import context_fixture as parent_context
from tests.test_phase5_dashboard_v1_16 import generated_fixture

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 6)
MATRIX = (
    ("오늘 야근했어", None),
    ("어제 영화 보고 왔어", None),
    ("내일 면접이라 긴장돼", None),
    ("이번 주에 운동 시작했어", None),
    ("내일 점심은 뭘 먹지?", None),
    ("사주 말고 그냥 들어줘", None),
    ("운세 얘기는 하지 말고 내일 보낼 메시지만 다듬어줘", None),
    ("내일 날씨 알려줄래?", None),
    ("내 일간을 설명해줘", None),
    ("2026-09-05 일진", None),
    ("어제 운세", None),
    ("사주 말고 내일 운세 봐줘", policy.DATE_REBIND),
    ("그냥 내일 운세를 메시지로 써줘", policy.DATE_REBIND),
    ("그럼 내일은 어때?", policy.INTENT_CONFIRMATION),
    ("오늘 힘들었어. 그럼 내일은 어때?", None),
    ("그럼 내일이 어때?", policy.INTENT_CONFIRMATION),
    ("오늘 운세", policy.DATE_REBIND),
    ("이번 주 운세", policy.SCOPE_UNSUPPORTED),
    ("운세 좀", policy.DATE_AMBIGUOUS),
    ("9월 5일 운세", policy.DATE_AMBIGUOUS),
    ("2026-02-30 일진", policy.DATE_AMBIGUOUS),
    ("2026-09-05와 2026-09-06 운세", policy.SCOPE_UNSUPPORTED),
    ("오늘은 힘들었어. 내일 운세는 어때?", policy.DATE_REBIND),
    ("오늘 야근했어. 2026-09-05 일진을 알려줘", None),
)


def context_fixture(root):
    context = parent_context(root)
    config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
    context.update(
        config=config, config_path=ROOT / dashboard.DEFAULT_CONFIG,
        inference_engines=config["inference_engines"],
        prompt_profiles=dashboard._load_prompt_profiles(ROOT, config),
    )
    return context


@contextmanager
def synthetic_app():
    with tempfile.TemporaryDirectory(prefix="saju-intent-v2-") as directory:
        context = context_fixture(Path(directory))
        with (
            patch.object(policy, "kst_today", return_value=TODAY),
            patch.object(dashboard, "_generation_gate", return_value={"allowed": True}),
            patch.object(dashboard, "_engine_availability", return_value={"available": True}),
            patch.object(
                dashboard, "_generate_engine_conversation",
                return_value=generated_fixture("오늘도 수고했어요. 천천히 이야기해 주세요."),
            ) as generate,
            patch(
                "scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock",
                side_effect=lambda _: os.open(os.devnull, os.O_RDONLY),
            ),
        ):
            yield context, generate


class IntentPolicyV2Tests(unittest.TestCase):
    def test_known_false_blocks_are_general_without_word_allowlist(self):
        for prompt in (
            "오늘 야근했어", "어제 영화 보고 왔어", "내일 면접이라 긴장돼",
            "이번 주에 운동 시작했어", "2026-09-05에 도서관에 갔어",
            "내일은 자전거를 고쳐야 해", "이 문장의 흐름을 해석해 줘",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(policy.prompt_intent(prompt), "general_followup")

    def test_same_followup_has_three_contexts_and_ignores_binding_as_intent(self):
        for previous, expected in (
            ("내 일간을 설명해줘", "period_request"),
            ("내일 면접이라 긴장돼", "general_followup"),
            (None, "clarification_required"),
            ("그럼 내일은 어때?", "clarification_required"),
        ):
            with self.subTest(previous=previous):
                result = policy.resolve_intent("그럼 내일은 어때?", prior_user_request=previous)
                self.assertEqual(result.intent, expected)
        self.assertEqual(
            policy.date_scope("그럼 내일은 어때?", None, today=TODAY)["reason_code"],
            policy.INTENT_CONFIRMATION,
        )

    def test_date_extraction_uses_only_active_forecast_clause(self):
        binding = binding_fixture()
        before = deepcopy(binding)
        value = policy.date_scope(
            "오늘은 힘들었어. 2026-09-05 일진은 어때?", binding, today=TODAY,
        )
        self.assertTrue(value["allowed"])
        self.assertEqual(value["requested_dates"], ["2026-09-05"])
        self.assertEqual(binding, before)
        value = policy.date_scope("오늘은 힘들었어. 내일 운세는 어때?", binding, today=TODAY)
        self.assertEqual(value["reason_code"], policy.DATE_REBIND)
        self.assertEqual(value["requested_dates"], ["2026-09-07"])

    def test_explicit_current_request_wins_and_opt_out_is_topic_local(self):
        for prompt, previous, expected in (
            ("사주 말고 내일 운세를 알려줘", "회의가 걱정돼", "period_request"),
            ("사주 말고 그냥 들어줘", "오늘 운세", "general_followup"),
            ("운세 말고 내 일간을 설명해줘", "내일 운세", "chart_interpretation"),
            ("내일 점심은 뭘 먹지?", "내 일간 설명", "general_followup"),
            ("오늘 힘들었어. 그럼 내일은 어때?", "내 일간 설명", "general_followup"),
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(policy.prompt_intent(prompt, prior_user_request=previous), expected)

    def test_parent_date_grammar_and_fact_claims_are_not_rewritten(self):
        for prompt in ("2026-09-05 일진", "이번 주 운세", "그럼 내일이 어때?", "9월 5일 운세"):
            old = parent.date_scope(prompt, binding_fixture(), today=TODAY)
            new = policy.date_scope(prompt, binding_fixture(), today=TODAY, prior_user_request="내 일간")
            for field in ("allowed", "reason_code", "requested_dates", "snapshot_date"):
                self.assertEqual(old[field], new[field])
        for output in ("일간은 丁입니다.", "일간은 甲입니다.", "일간은 甲이 아니라 丁입니다."):
            old = parent.audit_output("내 일간", output, binding_fixture())
            new = policy.audit_output("내 일간", output, binding_fixture())
            for field in ("claims", "reasons", "passed"):
                self.assertEqual(old[field], new[field])

    def test_audit_uses_the_same_resolved_intent(self):
        for previous, expected in (("내 일간", "period_request"), ("내일 점심", "general_followup")):
            result = policy.audit_output(
                "그럼 내일은 어때?", "천천히 준비해 보세요.", binding_fixture(),
                prior_user_request=previous,
            )
            self.assertEqual(result["intent"], expected)
            self.assertEqual("period_day_fact_missing" in result["reasons"], expected == "period_request")

    def test_matrix_and_unsupported_ranges(self):
        for prompt, code in MATRIX:
            with self.subTest(prompt=prompt):
                self.assertEqual(policy.date_scope(prompt, binding_fixture(), today=TODAY)["reason_code"], code)


class DashboardV117Tests(unittest.TestCase):
    def test_candidate_config_preserves_defaults_and_parent(self):
        config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
        dashboard.validate_config(config)
        self.assertFalse(config["chart_only_runtime"]["enabled_by_default"])
        self.assertFalse(config["governance"]["production_promotion_allowed"])
        self.assertEqual(config["inference_engines"]["default_selection"], "ki20_final")
        original = json.loads((ROOT / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.16.0-intent-candidate.json").read_text())
        for key in ("prompt_profiles", "inference_engines", "governance"):
            self.assertEqual(config[key], original[key])

    def test_http_matrix_and_client_history_cannot_bypass_precheck(self):
        with synthetic_app() as (context, _):
            context["config"]["chart_only_runtime"]["rate_limits_per_minute"]["model_generation"] = 100
            binding = Mock()
            binding.public_snapshot.return_value = binding_fixture()
            runner = Mock(return_value={"synthetic_only": True})
            server = dashboard.DashboardHTTPServer(
                ("127.0.0.1", 0), context, dashboard.V117_ASSET_ROOT, "synthetic-csrf",
                chart_only_binding=binding, chart_only_runtime_requested=True,
                generation_runner=runner,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            try:
                matrix = [(p, c, {}) for p, c in MATRIX] + [
                    ("그럼 내일은 어때?", "HTTP_400", {field: "general"})
                    for field in ("history", "prior_user_request", "intent", "today", "expected_request_state_sha256")
                ]
                for previous, code in (("내 일간을 설명해줘", policy.DATE_REBIND), ("내일 면접이라 긴장돼", None)):
                    session = dashboard.execute_manual_generation(context, previous, runtime_binding=binding_fixture())
                    matrix.append(("그럼 내일은 어때?", code, {"session_id": session["session_id"]}))
                for prompt, code, extra in matrix:
                    with self.subTest(prompt=prompt, extra=extra):
                        runner.reset_mock()
                        request = urllib.request.Request(
                            origin + "/api/generate",
                            data=json.dumps({"prompt": prompt, "session_id": None, "runtime_session_id": "a" * 24, **extra}).encode(),
                            headers={"Origin": origin, "X-CSRF-Token": "synthetic-csrf", "Content-Type": "application/json"},
                        )
                        if code:
                            with self.assertRaises(urllib.error.HTTPError) as raised:
                                urllib.request.urlopen(request, timeout=5)
                            self.assertEqual(json.loads(raised.exception.read())["code"], code)
                            runner.assert_not_called()
                        else:
                            with urllib.request.urlopen(request, timeout=5) as response:
                                self.assertEqual(response.status, 200)
                            runner.assert_called_once()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_direct_and_stored_policy_use_read_only_previous_user(self):
        with synthetic_app() as (context, generate):
            binding = binding_fixture()
            # 이전 모델이 사주를 끼워 넣어도 사용자의 일반 주제를 바꾸지 않는다.
            generate.return_value = generated_fixture("내일 운세가 궁금하시군요.")
            first = dashboard.execute_manual_generation(context, "내일 면접이라 긴장돼", runtime_binding=binding)
            session_id = first["session_id"]
            old_messages = deepcopy(first["session"]["messages"])
            snapshot = deepcopy(binding)
            scope, prior, state = dashboard._request_scope(context, "그럼 내일은 어때?", session_id, binding)
            self.assertEqual(prior, "내일 면접이라 긴장돼")
            self.assertTrue(scope["allowed"])
            self.assertEqual(dashboard.manual_session_payload(context, session_id)["messages"], old_messages)
            generate.reset_mock()
            second = dashboard.execute_manual_generation(
                context, "그럼 내일은 어때?", session_id, runtime_binding=binding,
                expected_request_state_sha256=state,
            )
            self.assertEqual(second["grounding_gate"]["intent"], "general_followup")
            self.assertEqual(second["session"]["messages"][:len(old_messages)], old_messages)
            self.assertEqual(binding, snapshot)
            generate.assert_called_once()
            sent = generate.call_args.args[2]
            self.assertEqual([m["content"] for m in sent[1:-1]], [m["content"] for m in old_messages])
            self.assertIn(dashboard._runtime_model_context_from_binding(binding)[0], sent[0]["content"])

    def test_other_session_or_changed_state_cannot_supply_context(self):
        with synthetic_app() as (context, generate):
            binding = binding_fixture()
            general = dashboard.execute_manual_generation(context, "내일 면접이라 긴장돼", runtime_binding=binding)
            chart = dashboard.execute_manual_generation(context, "내 일간을 설명해줘", runtime_binding=binding)
            _, _, state = dashboard._request_scope(context, "그럼 내일은 어때?", general["session_id"], binding)
            generate.reset_mock()
            for session_id, expected, code in (
                (chart["session_id"], None, policy.DATE_REBIND),
                (chart["session_id"], state, "RUNTIME_REQUEST_STATE_CHANGED"),
                (general["session_id"], "0" * 64, "RUNTIME_REQUEST_STATE_CHANGED"),
            ):
                with self.subTest(session_id=session_id, code=code), self.assertRaises(dashboard.DashboardRequestError) as raised:
                    dashboard.execute_manual_generation(context, "그럼 내일은 어때?", session_id, runtime_binding=binding, expected_request_state_sha256=expected)
                self.assertEqual(raised.exception.reason_code, code)
            generate.assert_not_called()

    def test_confirmation_and_tamper_never_acquire_gpu_or_persist(self):
        with (
            synthetic_app() as (context, generate),
            patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock") as lock,
        ):
            with self.assertRaises(dashboard.DashboardRequestError) as raised:
                dashboard.execute_manual_generation(context, "그럼 내일은 어때?", runtime_binding=binding_fixture())
            self.assertEqual(raised.exception.reason_code, policy.INTENT_CONFIRMATION)
            tampered = binding_fixture()
            tampered["snapshot_sha256"] = "f" * 64
            with self.assertRaises(dashboard.Phase5DashboardError):
                dashboard.execute_manual_generation(context, "오늘 야근했어", runtime_binding=tampered)
            lock.assert_not_called()
            generate.assert_not_called()
            self.assertFalse(dashboard._manual_session_root(context, create=False).exists())

    def test_subprocess_prechecks_and_pins_the_parent_state(self):
        with synthetic_app() as (context, _):
            binding = binding_fixture()
            with patch.object(dashboard.subprocess, "run") as run:
                with self.assertRaises(dashboard.DashboardRequestError):
                    dashboard._manual_generation_subprocess(context, "그럼 내일은 어때?", None, None, None, runtime_binding=binding)
                run.assert_not_called()
            response = {
                "persisted": True, "local_only": True, "session_id": "b" * 24,
                "runtime_binding_applied": True, "runtime_snapshot_sha256": binding["snapshot_sha256"],
            }
            with patch.object(dashboard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(response), "")) as run:
                dashboard._manual_generation_subprocess(context, "오늘 야근했어", None, None, None, runtime_binding=binding)
                self.assertIn("phase5_dashboard_v1_17.py", run.call_args.args[0][1])
                payload = json.loads(run.call_args.kwargs["input"])
                self.assertRegex(payload["expected_request_state_sha256"], r"^[0-9a-f]{64}$")

    def test_worker_conflict_remains_a_structured_409(self):
        with synthetic_app() as (context, _):
            for code in (policy.INTENT_CONFIRMATION, "RUNTIME_REQUEST_STATE_CHANGED"):
                response = subprocess.CompletedProcess([], 1, "", json.dumps({"code": code, "error": "확인 필요"}))
                with (
                    self.subTest(code=code),
                    patch.object(dashboard.subprocess, "run", return_value=response),
                    self.assertRaises(dashboard.DashboardRequestError) as raised,
                ):
                    dashboard._manual_generation_subprocess(context, "오늘 야근했어", None, None, None, runtime_binding=binding_fixture())
                self.assertEqual(raised.exception.reason_code, code)
                self.assertEqual(raised.exception.status, 409)

    def test_fresh_process_runs_http_direct_and_without_model_import(self):
        code = (
            "import io,json,sys,unittest;"
            "names=['tests.test_dashboard_intent_v2.DashboardV117Tests.'+name for name in "
            "['test_http_matrix_and_client_history_cannot_bypass_precheck','test_direct_and_stored_policy_use_read_only_previous_user']];"
            "result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.loadTestsFromNames(names));"
            "loaded=bool({'torch','transformers','peft'}&set(sys.modules));"
            "print(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,'model_imported':loaded}));"
            "sys.exit(0 if result.wasSuccessful() and not loaded else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", code], cwd=ROOT,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {"passed": True, "tests": 2, "model_imported": False})


if __name__ == "__main__":
    unittest.main()
