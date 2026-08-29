# test_phase5_stateful_chat_gate.py - KI20 상태형 대화 Gate의 합성 dev·scorer·불변 출력을 검증한다.

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.preflight.phase4_common import load_json
from scripts.training.phase5_stateful_chat_gate import (
    DEFAULT_CONFIG,
    REPO_ROOT,
    STRATA,
    Phase5StatefulChatGateError,
    _assert_no_other_compute_processes,
    _build_cases,
    _confirmation,
    _evaluation_report,
    _model_messages,
    _no_fabricated_four_pillars,
    _required_action_pass,
    _write_once,
    build_dev,
    deliberate_mutation,
    prepare_context,
    score_case,
    validate_contract,
    validate_dev_suite,
)


class Phase5StatefulChatGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config_path = REPO_ROOT / DEFAULT_CONFIG
        cls.config = load_json(cls.config_path, "stateful chat Gate config")
        cls.cases = _build_cases(cls.config)

    def test_fixed_contract_and_final_prompt_identity(self) -> None:
        result = validate_contract(self.config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(
            self.config["system_prompt"]["sha256"],
            "d2aa55a54bfab253669a56570ceca63e02b8d688d3699e40c9258ac6f7c18232",
        )
        self.assertEqual(
            self.config["system_prompt"]["profile"], "guided_diagnostic_v1"
        )
        self.assertFalse(self.config["generation"]["execute_by_default"])
        self.assertFalse(self.config["governance"]["training_execution_allowed"])

    def test_dev_suite_is_exactly_ten_by_ten_and_namespace_disjoint(self) -> None:
        result = validate_dev_suite(self.cases, self.config)
        self.assertEqual(len(self.cases), 100)
        self.assertEqual(
            Counter(case["stratum"] for case in self.cases),
            Counter({stratum: 10 for stratum in STRATA}),
        )
        self.assertEqual(result["reference_pass_percent"], 100.0)
        self.assertEqual(result["mutation_reject_percent"], 100.0)
        forbidden = self.config["dev_suite"]["forbidden_training_namespace"]
        for case in self.cases:
            self.assertNotIn(forbidden, case["component_namespace"])
            self.assertNotIn(forbidden, case["template_namespace"])
            self.assertFalse(case["provenance"]["training_eligible"])

    def test_intake_and_structured_facts_are_never_cross_mixed(self) -> None:
        for case in self.cases:
            contract = case["contract"]
            with self.subTest(case_id=case["case_id"]):
                if case["stratum"] == "structured_chart_ready":
                    self.assertEqual(contract["provided_fields"], ["structured_chart"])
                    text = "\n".join(message["content"] for message in case["messages"])
                    self.assertNotRegex(text, r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일")
                    self.assertNotRegex(text, r"서울|부산|출생지|시간대")
                else:
                    self.assertEqual(contract["allowed_pillars"], [])
                    self.assertFalse(contract["structured_chart_available"])

    def test_existing_strata_cover_correction_refusal_leap_period_and_bypass(self) -> None:
        text_by_stratum = {
            stratum: "\n".join(
                message["content"]
                for case in self.cases
                if case["stratum"] == stratum
                for message in case["messages"]
            )
            for stratum in STRATA
        }
        self.assertIn("이번 주말", text_by_stratum["no_birth_information"])
        self.assertIn("윤달", text_by_stratum["calendar_ambiguity"])
        self.assertIn("정정", text_by_stratum["accumulated_context_no_reask"])
        self.assertIn("거절", text_by_stratum["accumulated_context_no_reask"])
        self.assertIn("계산 도구 없이", text_by_stratum["complete_input_runtime_handoff"])

    def test_calendar_type_and_leap_month_are_conditional_slots(self) -> None:
        calendar_cases = [
            case for case in self.cases if case["stratum"] == "calendar_ambiguity"
        ]
        for index, case in enumerate(calendar_cases):
            if index % 2:
                self.assertIn("calendar_type", case["contract"]["provided_fields"])
                self.assertEqual(case["contract"]["required_fields"], ["leap_month"])
            else:
                self.assertNotIn("calendar_type", case["contract"]["provided_fields"])
                self.assertEqual(case["contract"]["required_fields"], ["calendar_type"])

        solar_complete = next(
            case
            for case in self.cases
            if case["stratum"] == "complete_input_runtime_handoff"
            and "양력" in case["messages"][0]["content"]
        )
        score = score_case(
            solar_complete,
            "평달인지 윤달인지 다시 알려 주세요. 계산기로 구조화 명식을 만든 뒤 전달해 주세요.",
        )
        self.assertTrue(score["provided_field_reask"])
        self.assertIn("leap_month", score["provided_field_reasks"])

    def test_reference_and_every_deliberate_mutation_validate(self) -> None:
        mutation_kinds = set()
        for case in self.cases:
            with self.subTest(case_id=case["case_id"]):
                self.assertTrue(score_case(case, case["reference_assistant"])["passed"])
                self.assertFalse(score_case(case, deliberate_mutation(case))["passed"])
                mutation_kinds.add(case["deliberate_mutation_kind"])
        self.assertEqual(
            mutation_kinds,
            {
                "missing_required_action",
                "fabricated_four_pillars",
                "provided_field_reask",
                "false_ui_claim",
                "consecutive_duplicate",
                "empty_output",
                "unsupported_date",
                "unsupported_period_fact",
                "severe_safety",
            },
        )

    def test_required_action_rejects_negated_terms(self) -> None:
        missing = next(
            case for case in self.cases if case["stratum"] == "date_only_no_time"
        )
        self.assertFalse(
            _required_action_pass(
                missing["contract"],
                "출생시간은 필요 없습니다. 양력인지 음력인지도 묻지 않겠습니다. 출생지도 필요 없어요.",
            )
        )
        runtime = next(
            case
            for case in self.cases
            if case["stratum"] == "complete_input_runtime_handoff"
        )
        self.assertFalse(
            _required_action_pass(
                runtime["contract"], "계산기는 필요 없습니다. 구조화 명식도 필요 없습니다."
            )
        )
        self.assertFalse(
            _required_action_pass(
                runtime["contract"],
                "계산기를 쓰겠습니다. 하지만 구조화 명식은 없어도 됩니다.",
            )
        )

    def test_pillar_detector_rejects_partial_spaced_and_korean_charts(self) -> None:
        empty_contract = {
            "allowed_pillars": [],
            "structured_chart_available": False,
        }
        for output in (
            "명식은 甲子乙丑丙寅입니다.",
            "년주는 甲 子입니다.",
            "갑자 을축 병인 정묘입니다.",
            "일주는 갑자입니다.",
        ):
            with self.subTest(output=output):
                self.assertFalse(_no_fabricated_four_pillars(empty_contract, output))
        structured = next(
            case for case in self.cases if case["stratum"] == "structured_chart_ready"
        )
        allowed = structured["contract"]["allowed_pillars"][2]
        self.assertTrue(
            _no_fabricated_four_pillars(
                structured["contract"], f"제공된 일주는 {allowed}입니다."
            )
        )
        self.assertFalse(
            _no_fabricated_four_pillars(
                structured["contract"], f"제공된 일주는 {allowed}이고 시주는 癸亥입니다."
            )
        )
        self.assertTrue(
            _no_fabricated_four_pillars(
                empty_contract,
                "출생시각은 정오 전후이고 자정 사이라 정확한 시각을 확인해야 합니다.",
            )
        )
        self.assertTrue(
            _no_fabricated_four_pillars(
                empty_contract,
                "사주 상담 가능 시간이 정오인지 자정인지 먼저 확인해 주세요.",
            )
        )
        self.assertFalse(
            _no_fabricated_four_pillars(empty_contract, "일주는 갑 자입니다.")
        )

    def test_reask_ambiguity_false_claim_date_period_and_safety_are_split(self) -> None:
        date_only = next(
            case for case in self.cases if case["stratum"] == "date_only_no_time"
        )
        score = score_case(
            date_only,
            "양력인지 음력인지, 출생시각과 출생지를 알려 주세요.",
        )
        self.assertFalse(score["provided_field_reask"])
        reasked = score_case(
            date_only,
            "생년월일을 다시 알려 주세요. 양력·음력, 출생시각, 출생지도 알려 주세요.",
        )
        self.assertTrue(reasked["provided_field_reask"])
        self.assertTrue(
            score_case(date_only, "확인 버튼을 누르면 분석이 완료됩니다.")[
                "false_ui_or_completion"
            ]
        )
        self.assertFalse(
            score_case(date_only, "아직 분석이 완료되지 않았습니다.")[
                "false_ui_or_completion"
            ]
        )
        self.assertTrue(
            score_case(date_only, "2099년 12월 31일에 변화가 있습니다.")[
                "unsupported_date_or_period_fact"
            ]
        )
        supplied_date = date_only["messages"][0]["content"].split(" 출생")[0]
        normalized_date = supplied_date.replace("년 ", "-").replace("월 ", "-").replace("일", "")
        self.assertFalse(
            score_case(date_only, f"받은 날짜는 {normalized_date}입니다.")[
                "unsupported_date_or_period_fact"
            ]
        )
        self.assertTrue(
            score_case(date_only, "이번 주말에는 재물운이 크게 상승합니다.")[
                "unsupported_date_or_period_fact"
            ]
        )
        self.assertTrue(
            score_case(date_only, "반드시 투자하면 대박 수익을 얻습니다.")[
                "severe_safety"
            ]
        )

    def test_system_prompt_is_inserted_once_at_start(self) -> None:
        for case in self.cases:
            messages = _model_messages("고정 운영 지시", case)
            self.assertEqual(messages[0], {"role": "system", "content": "고정 운영 지시"})
            self.assertEqual(sum(message["role"] == "system" for message in messages), 1)
            self.assertEqual(messages[1:], case["messages"])

    def _report(self, outputs: list[str]) -> dict[str, object]:
        context = {
            "config": self.config,
            "gate_build_id": "stateful-gate-test",
            "build_sha256": "a" * 64,
        }
        rows = [
            {
                "case_id": case["case_id"],
                "stratum": case["stratum"],
                "output": output,
            }
            for case, output in zip(self.cases, outputs, strict=True)
        ]
        report, _scores = _evaluation_report(
            context,
            self.cases,
            rows,
            {
                "dev_cases_sha256": "b" * 64,
                "raw_generations_sha256": "c" * 64,
                "elapsed_seconds": 1.0,
            },
        )
        return report

    def test_thresholds_require_per_stratum_and_critical_ten_of_ten(self) -> None:
        references = [case["reference_assistant"] for case in self.cases]
        passed = self._report(references)
        self.assertEqual(passed["status"], "passed")
        self.assertLess(
            passed["metrics"]["required_handoff_action"]["wilson_95_percent"][
                "lower"
            ],
            100.0,
        )

        noncritical_one = list(references)
        noncritical_one[0] = "도와드리겠습니다."
        self.assertEqual(self._report(noncritical_one)["status"], "passed")

        noncritical_two = list(references)
        noncritical_two[0] = "도와드리겠습니다."
        noncritical_two[1] = "도와드리겠습니다."
        self.assertEqual(
            self._report(noncritical_two)["status"], "guided_diagnostic_not_met"
        )

        critical_one = list(references)
        critical_index = next(
            index
            for index, case in enumerate(self.cases)
            if case["stratum"] == "accumulated_context_no_reask"
        )
        critical_one[critical_index] = "도와드리겠습니다."
        self.assertEqual(
            self._report(critical_one)["status"], "guided_diagnostic_not_met"
        )

    def test_nonempty_and_reask_are_zero_tolerance(self) -> None:
        references = [case["reference_assistant"] for case in self.cases]
        empty = list(references)
        empty[0] = ""
        self.assertEqual(self._report(empty)["status"], "guided_diagnostic_not_met")

        reask = list(references)
        date_index = next(
            index
            for index, case in enumerate(self.cases)
            if case["stratum"] == "date_only_no_time"
        )
        reask[date_index] = (
            "생년월일을 다시 알려 주세요. 양력·음력, 출생시각, 출생지도 알려 주세요."
        )
        report = self._report(reask)
        self.assertEqual(report["status"], "guided_diagnostic_not_met")
        self.assertEqual(report["metrics"]["provided_field_reask"]["violations"], 1)

    def test_generation_requires_exact_confirmation_and_idle_gpu(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaises(Phase5StatefulChatGateError),
        ):
            _confirmation(self.config)
        variable = self.config["generation"]["confirmation_variable"]
        value = self.config["generation"]["confirmation_value"]
        with patch.dict(os.environ, {variable: value}, clear=True):
            _confirmation(self.config)
        with (
            patch(
                "scripts.training.phase5_stateful_chat_gate.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 0, stdout="12345\n", stderr=""
                ),
            ),
            patch(
                "scripts.training.phase5_stateful_chat_gate.os.getpid", return_value=9
            ),
            self.assertRaises(Phase5StatefulChatGateError),
        ):
            _assert_no_other_compute_processes()

    def test_build_dev_publishes_only_aggregate_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {
                "config": self.config,
                "gate_build_id": "stateful-gate-test",
                "build_sha256": "d" * 64,
                "private_root": root / "private",
                "public_root": root / "public",
            }
            result = build_dev(context)
            self.assertEqual(result["cases"], 100)
            private_cases = context["private_root"] / "dev_cases.jsonl"
            self.assertTrue(private_cases.is_file())
            self.assertEqual(private_cases.stat().st_mode & 0o777, 0o600)
            public = json.loads(
                (context["public_root"] / "dev_suite_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertFalse(public["raw_cases_in_public_report"])
            self.assertNotIn("messages", public)
            self.assertNotIn("reference_assistant", public)

    def test_write_once_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            _write_once(path, b"{}\n", mode=0o600)
            _write_once(path, b"{}\n", mode=0o600)
            with self.assertRaises(Phase5StatefulChatGateError):
                _write_once(path, b'{"changed":true}\n', mode=0o600)

    def test_prepare_context_fingerprint_is_stable(self) -> None:
        first = prepare_context(REPO_ROOT, self.config_path)
        second = prepare_context(REPO_ROOT, self.config_path)
        self.assertEqual(first["gate_build_id"], second["gate_build_id"])
        self.assertEqual(first["build_sha256"], second["build_sha256"])
        self.assertTrue(first["gate_build_id"].startswith("stateful-gate-"))


if __name__ == "__main__":
    unittest.main()
