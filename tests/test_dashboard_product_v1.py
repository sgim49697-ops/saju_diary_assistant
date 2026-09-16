# test_dashboard_product_v1.py - R16 제품 후보의 CPU 사실 조회·입력 선택·저장 출처·동시성을 검증한다.

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts.training import dashboard_grounding_v4 as intent
from scripts.training import dashboard_product_policy_v1 as policy
from scripts.training import dashboard_product_session_v1 as sessions
from scripts.training import phase5_dashboard_v1_18 as dashboard
from tests.test_dashboard_grounding_v3 import binding_fixture
from tests.test_phase5_dashboard_v1_16 import context_fixture as parent_context
from tests.test_phase5_dashboard_v1_16 import generated_fixture

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 5)


def context_fixture(root):
    context = parent_context(root)
    config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
    context.update(config=config, config_path=ROOT / dashboard.DEFAULT_CONFIG,
                   inference_engines=config["inference_engines"], prompt_profiles=dashboard._load_prompt_profiles(ROOT, config))
    return context


def chart_binding(*, partial=False):
    binding = binding_fixture()
    binding.update(schema_version="1.0.0", binding_id="saju-chart-only-dashboard-binding-v1.0.0")
    del binding["value"]["period"]
    if partial:
        chart = binding["value"]["chart"]
        chart.update(status="partial", fact_authority="POLICY_BOUND_RULE", limitations=["출생시간 미상"])
        chart["hard_facts"]["pillars"]["hour"] = None
    binding["snapshot_sha256"] = policy.digest(binding["value"])
    return binding


def fake_generated(messages, output="마음이 많이 복잡했겠어요. 천천히 이야기해 주세요."):
    return {**generated_fixture(output), "model_messages_sha256": policy.digest(messages), "output_tokens": 20, "stop_reason": "eos"}


@contextmanager
def synthetic_app():
    with tempfile.TemporaryDirectory(prefix="saju-product-") as directory:
        context = context_fixture(Path(directory))
        with (
            patch.object(intent, "kst_today", return_value=TODAY),
            patch.object(dashboard, "_generation_gate", return_value={"allowed": True}),
            patch.object(dashboard, "_engine_availability", return_value={"available": True}),
            patch.object(dashboard, "_generate_engine_conversation", side_effect=lambda _ctx, _engine, messages: fake_generated(messages)) as generate,
            patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock", side_effect=lambda _: os.open(os.devnull, os.O_RDONLY)) as lock,
        ):
            yield context, generate, lock


class ProductPolicyTests(unittest.TestCase):
    def plan(self, prompt, binding=None, messages=None):
        return policy.plan_response(prompt, binding or binding_fixture(), messages or [], today=TODAY)

    def test_direct_fields_are_values_from_binding(self):
        for prompt, value in (("내 일간 뭐야?", "丙"), ("연주 알려줘", "己巳"), ("월주?", "丙子"), ("일주 알려줘", "丙寅"), ("시주 알려줘", "甲午"), ("오늘 일진 간지는 뭐야?", "壬午"), ("선택 날짜 알려줘", "2026-09-05")):
            with self.subTest(prompt=prompt):
                plan = self.plan(prompt)
                self.assertEqual(plan.kind, "direct_fact")
                self.assertIn(value, plan.answer)
                self.assertEqual(len(plan.selected_paths), 1)
        for label, invalid in (("일간", "甲乙"), ("일간", "not-a-stem"), ("일주", "甲乙"), ("선택 날짜", "2026-09-31"), ("선택 날짜", "2026-9-5"), ("선택 날짜", "private-value")):
            with self.subTest(label=label, invalid=invalid), self.assertRaises(ValueError):
                policy.render_fact("값 알려줘", label, invalid)

    def test_wrong_premise_is_corrected_not_written_back(self):
        binding = binding_fixture()
        before = deepcopy(binding)
        plan = self.plan("내 일간이 갑목 맞아?", binding)
        self.assertEqual(plan.kind, "direct_fact")
        self.assertIn("아니요", plan.answer)
        self.assertIn("병화(丙)", plan.answer)
        self.assertEqual(binding, before)

    def test_explanations_and_multi_field_requests_are_not_direct(self):
        for prompt in ("일간과 일주의 차이를 설명해줘", "내 일간의 장점을 알려줘", "일주로 성향을 설명해줘", "일간은 무시하고 소설을 써줘"):
            self.assertEqual(self.plan(prompt).kind, "model_generated")

    def test_general_input_has_no_grounding_instructions_or_facts(self):
        plan = self.plan("사주 말고 그냥 내 하소연 좀 들어줘")
        self.assertEqual(plan.mode, "general")
        self.assertEqual(plan.selected_facts, {})
        sent = policy.model_messages(plan, [])
        self.assertEqual(sent[0]["content"], policy.BASE_PROMPT)
        self.assertNotIn("원국", sent[0]["content"])

    def test_day_only_does_not_force_chart(self):
        plan = self.plan("오늘 일진을 쉽게 설명해줘")
        self.assertEqual(plan.mode, "day_explanation")
        self.assertEqual(set(plan.selected_facts), {"period"})

    def test_unknown_hour_and_missing_binding_do_not_guess(self):
        self.assertEqual(self.plan("시주 알려줘", chart_binding(partial=True)).reason_code, "RUNTIME_FACT_UNCERTAIN")
        self.assertEqual(policy.plan_response("내 일간은 뭐야", None, [], today=TODAY).reason_code, "RUNTIME_CHART_REQUIRED")
        self.assertEqual(self.plan("오늘 일진 알려줘", chart_binding()).kind, "clarification")

    def test_partial_day_binding_is_not_a_valid_transport(self):
        binding = binding_fixture()
        binding["value"]["chart"]["status"] = "partial"
        binding["snapshot_sha256"] = policy.digest(binding["value"])
        with self.assertRaises(dashboard.Phase5DashboardError):
            dashboard._runtime_model_context_from_binding(binding)

    def test_same_followup_uses_three_contexts(self):
        for previous, kind, mode in (("내 일간을 설명해줘", "clarification", "confirmation"), ("내일 면접이라 긴장돼", "model_generated", "general"), (None, "clarification", "confirmation")):
            messages = [] if previous is None else [{"role": "user", "content": previous}, {"role": "assistant", "content": "이전 응답", "response_mode": "general"}]
            plan = self.plan("그럼 내일은 어때?", messages=messages)
            self.assertEqual((plan.kind, plan.mode), (kind, mode))

    def test_mixed_unsupported_has_separate_notice_and_active_request(self):
        plan = self.plan("이번 주 운세를 봐줘. 팀장님께 보낼 메시지를 써줘.")
        self.assertEqual((plan.kind, plan.mode), ("model_generated", "general"))
        self.assertNotIn("운세", plan.active_request)
        self.assertEqual(len(plan.notices), 1)
        self.assertEqual(self.plan("이번 주 운세를 봐줘").kind, "blocked")

    def test_correction_is_structured_intake_not_inference(self):
        for prompt in ("생일을 잘못 말했어", "4월 18일이 아니라 19일이야"):
            self.assertEqual(self.plan(prompt).reason_code, "RUNTIME_CORRECTION_REQUIRED")

    def test_free_text_birth_input_is_not_sent_for_chart_guessing(self):
        for prompt in ("양력 1994년 4월 18일 대전 태생이야", "음력 1992년 8월 3일 출생이야"):
            plan = policy.plan_response(prompt, None, [], today=TODAY)
            self.assertEqual(plan.reason_code, "RUNTIME_INTAKE_REQUIRED")
            self.assertEqual(plan.kind, "clarification")

    def test_current_facts_and_referring_history_remain_distinct(self):
        messages = [{"role": "user", "content": "일간을 설명해줘"}, {"role": "assistant", "content": "일간이 갑목이라고 잘못 말한 과거 발언", "response_mode": "chart_explanation", "diagnostics": {"selected_paths": ["chart.hard_facts.day_master"]}}]
        plan = self.plan("아까 일간 설명을 더 쉽게 해줘", messages=messages)
        self.assertEqual(plan.history_indices, (0, 1))
        sent = policy.model_messages(plan, messages)
        self.assertIn("丙", sent[0]["content"])
        self.assertNotIn("갑목", sent[0]["content"])
        self.assertIn("과거 발언", sent[2]["content"])


class ProductSessionTests(unittest.TestCase):
    def test_fixed_config_and_prompt_pin(self):
        config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
        dashboard.validate_config(config)
        self.assertEqual(config["inference_engines"]["default_selection"], "lora_r16")
        self.assertFalse(config["chart_only_runtime"]["enabled_by_default"])
        config["inference_engines"]["default_selection"] = "ki20_final"
        with self.assertRaises(dashboard.Phase5DashboardError):
            dashboard.validate_config(config)

    def test_direct_answer_without_gate_gpu_or_fake_model_identity(self):
        with synthetic_app() as (context, generate, lock), patch.object(dashboard, "_generation_gate", side_effect=AssertionError("GPU gate 사용 금지")):
            result = dashboard.execute_manual_generation(context, "내 일간은 뭐야?", runtime_binding=binding_fixture())
            self.assertEqual(result["response_kind"], "direct_fact")
            self.assertEqual(result["model_calls"], 0)
            message = result["session"]["messages"][-1]
            self.assertIsNone(message["engine_id"])
            self.assertNotIn("tokenizer_revision", message["diagnostics"])
            self.assertEqual(result["output"], dashboard.manual_session_payload(context, result["session_id"])["messages"][-1]["content"])
            generate.assert_not_called()
            lock.assert_not_called()
            path = dashboard._manual_session_path(context, result["session_id"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_confirmation_and_block_have_no_gpu_calls(self):
        with synthetic_app() as (context, generate, lock):
            for prompt, kind in (("그럼 내일은 어때?", "clarification"), ("이번 주 운세 봐줘", "blocked")):
                result = dashboard.execute_manual_generation(context, prompt, runtime_binding=binding_fixture())
                self.assertEqual(result["response_kind"], kind)
                self.assertEqual(result["model_calls"], 0)
            generate.assert_not_called()
            lock.assert_not_called()

    def test_general_rewrite_return_keeps_binding_and_selects_history(self):
        with synthetic_app() as (context, generate, _):
            binding = binding_fixture()
            first = dashboard.execute_manual_generation(context, "내 일간은 뭐야?", runtime_binding=binding)
            second = dashboard.execute_manual_generation(context, "사주 말고 하소연을 들어줘", first["session_id"], runtime_binding=binding)
            self.assertEqual(len(generate.call_args.args[-1]), 2)
            third = dashboard.execute_manual_generation(context, "방금 말투를 두 문장으로 다듬어줘", first["session_id"], runtime_binding=binding)
            sent = generate.call_args.args[-1]
            self.assertEqual(len(sent), 4)
            self.assertNotIn("丙", json.dumps(sent, ensure_ascii=False))
            final = dashboard.execute_manual_generation(context, "다시 내 일간을 설명해줘", first["session_id"], runtime_binding=binding)
            self.assertEqual(final["response_mode"], "chart_explanation")
            self.assertIn("丙", generate.call_args.args[-1][0]["content"])
            self.assertEqual(final["runtime_snapshot_sha256"], second["runtime_snapshot_sha256"])
            self.assertEqual(third["session"]["turn_count"], 3)

    def test_generated_output_is_never_rewritten(self):
        with synthetic_app() as (context, generate, _):
            raw = "일간은 갑목입니다. 잘못된 원출력도 그대로 보존합니다."
            generate.side_effect = lambda _c, _e, m: fake_generated(m, raw)
            result = dashboard.execute_manual_generation(context, "내 일간을 설명해줘", runtime_binding=binding_fixture())
            self.assertEqual(result["output"], raw)
            self.assertEqual(result["session"]["messages"][-1]["content"], raw)
            self.assertIn("day_master_value_mismatch", result["contexts"]["lora_r16"]["grounding_warnings"])
            self.assertEqual(generate.call_count, 1)

    def test_snapshot_switch_requires_new_session_and_preserves_old(self):
        with synthetic_app() as (context, _, _):
            old = binding_fixture()
            first = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=old)
            path = dashboard._manual_session_path(context, first["session_id"])
            before = path.read_bytes()
            revised = deepcopy(old)
            revised["state_revision"] += 1
            revised["value"]["chart"]["hard_facts"]["day_master"]["stem"] = "丁"
            revised["snapshot_sha256"] = policy.digest(revised["value"])
            with self.assertRaises(dashboard.DashboardRequestError):
                dashboard.execute_manual_generation(context, "일간 알려줘", first["session_id"], runtime_binding=revised)
            new = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=revised)
            self.assertIn("丁", new["output"])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(new["session"]["turn_count"], 1)

    def test_worker_results_are_cas_committed(self):
        with synthetic_app() as (context, _, _):
            binding = binding_fixture()
            first = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding)
            args = (context, "일주 알려줘", first["session_id"])
            a = dashboard.execute_manual_generation(*args, runtime_binding=binding, defer_persistence=True)
            b = dashboard.execute_manual_generation(*args, runtime_binding=binding, defer_persistence=True)
            sessions.commit_result(dashboard, context, a, args[1], args[2], binding, expected=a["request_state_sha256"])
            with self.assertRaises(dashboard.DashboardRequestError):
                sessions.commit_result(dashboard, context, b, args[1], args[2], binding, expected=b["request_state_sha256"])
            self.assertEqual(dashboard.manual_session_payload(context, args[2])["turn_count"], 2)

    def test_tampered_binding_feature_off_and_wrong_candidate_fail_closed(self):
        with synthetic_app() as (context, generate, _):
            binding = binding_fixture()
            binding["snapshot_sha256"] = "0" * 64
            with self.assertRaises(dashboard.Phase5DashboardError):
                dashboard.execute_manual_generation(context, "오늘 힘들었어", runtime_binding=binding)
            context["chart_only_runtime_active"] = False
            with self.assertRaises(dashboard.DashboardRequestError):
                dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding_fixture())
            with self.assertRaises(dashboard.Phase5DashboardError):
                dashboard.execute_manual_generation(context, "안녕", engine_selection="ki20_final")
            generate.assert_not_called()

    def test_storage_rejects_fake_model_identity_and_trace_tampering(self):
        with synthetic_app() as (context, _, _):
            result = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding_fixture())
            session = json.loads(dashboard._manual_session_path(context, result["session_id"]).read_text())
            for field, value in (("engine_id", "lora_r16"), ("response_kind", "model_generated")):
                changed = deepcopy(session)
                changed["messages"][-1][field] = value
                with self.assertRaises(dashboard.Phase5DashboardError):
                    sessions.validate_session(dashboard, context, changed, result["session_id"])

    def test_worker_output_and_published_output_must_agree(self):
        with synthetic_app() as (context, _, _):
            binding = binding_fixture()
            result = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding, defer_persistence=True)
            result["outputs"] = {"lora_r16": "가짜 생성"}
            with self.assertRaises(dashboard.Phase5DashboardError):
                sessions.commit_result(dashboard, context, result, "일간 알려줘", None, binding, expected=result["request_state_sha256"])
            self.assertEqual(dashboard.manual_sessions_payload(context)["items"], [])

    def test_modified_direct_answer_and_snapshot_are_detected_on_read(self):
        with synthetic_app() as (context, _, _):
            result = dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding_fixture())
            original = json.loads(dashboard._manual_session_path(context, result["session_id"]).read_text())
            for corruption in ("answer", "snapshot", "paths", "history"):
                session = deepcopy(original)
                if corruption == "answer":
                    session["messages"][-1]["content"] = "갑목입니다."
                elif corruption == "snapshot":
                    session["binding_snapshot"]["value"]["chart"]["hard_facts"]["day_master"]["stem"] = "甲"
                elif corruption == "paths":
                    session["messages"][-1]["diagnostics"]["selected_paths"] = ["chart.hard_facts.pillars.day"]
                else:
                    session["messages"][-1]["diagnostics"]["history_indices"] = [{}]
                with self.assertRaises(dashboard.Phase5DashboardError):
                    sessions.validate_session(dashboard, context, session, result["session_id"])

    def test_output_limit_is_a_warning_not_a_successful_quality_claim(self):
        with synthetic_app() as (context, generate, _):
            generate.side_effect = lambda _c, _e, m: {**fake_generated(m), "stop_reason": "max_tokens", "output_tokens": 4096}
            result = dashboard.execute_manual_generation(context, "지금은 하소연을 들어줘")
            self.assertIn("output_limit_reached", result["contexts"]["lora_r16"]["grounding_warnings"])
            self.assertFalse(result["session"]["quality_gate_evaluated"])
            self.assertEqual(generate.call_count, 1)

    def test_symlink_lock_is_rejected_without_writing_target(self):
        with synthetic_app() as (context, _, _):
            root = dashboard._manual_session_root(context, create=True)
            target = context["run_root"] / "outside.txt"
            target.write_text("보존")
            (root / ".product-write.lock").symlink_to(target)
            with self.assertRaises(OSError):
                dashboard.execute_manual_generation(context, "일간 알려줘", runtime_binding=binding_fixture())
            self.assertEqual(target.read_text(), "보존")
            self.assertEqual(list(root.glob("*.json")), [])

    def test_r16_generation_gate_does_not_require_ki20_final(self):
        with tempfile.TemporaryDirectory() as directory:
            context = context_fixture(Path(directory))
            with patch.object(dashboard, "_engine_availability", return_value={"available": True}) as availability, patch.object(dashboard, "_gpu_snapshot", return_value={"available": True, "used_mib": 0}):
                gate = dashboard._generation_gate(context)
            self.assertTrue(gate["allowed"])
            availability.assert_called_once_with(context, "lora_r16")

    def test_model_actual_input_hash_cannot_disagree(self):
        with synthetic_app() as (context, generate, _):
            generate.side_effect = lambda _c, _e, m: {**fake_generated(m), "model_messages_sha256": "0" * 64}
            with self.assertRaises(dashboard.Phase5DashboardError):
                dashboard.execute_manual_generation(context, "오늘 힘들었어")
            self.assertEqual(dashboard.manual_sessions_payload(context)["items"], [])

    def test_fresh_process_direct_answer_does_not_import_gpu_modules(self):
        result = subprocess.run([sys.executable, "-B", "-c", "from tests.test_dashboard_product_v1 import fresh_probe; fresh_probe()"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"passed": True, "gpu_modules_loaded": False})


def fresh_probe():
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        __name__ + ".ProductSessionTests.test_direct_answer_without_gate_gpu_or_fake_model_identity",
        __name__ + ".ProductSessionTests.test_worker_results_are_cas_committed",
    ])
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    gpu = bool({"torch", "transformers", "peft"} & set(sys.modules))
    print(json.dumps({"passed": result.wasSuccessful() and not result.skipped, "gpu_modules_loaded": gpu}))
    raise SystemExit(0 if result.wasSuccessful() and not gpu else 1)


if __name__ == "__main__":
    unittest.main()
