# test_phase6_scoring.py - Phase 6 자동 기술 scorer의 계약·집계·결정을 검증한다.

from __future__ import annotations

import copy
import unittest

from scripts.evaluation.phase6_scoring import (
    AXES,
    Phase6ScoringError,
    aggregate_model_records,
    model_gate,
    no_regression,
    score_generation,
    select_baseline,
)


class Phase6ScoringTests(unittest.TestCase):
    def _case(self, axis: str) -> tuple[list[dict[str, str]], str, str]:
        if axis == "deterministic_saju_qa":
            prompt = "구조화 명식: 년주 戊申 월주 甲寅 일주 甲子 시주 戊辰\n네 기둥을 년주부터 시주 순서로 정확히 적어 주세요."
            reference = "년주 戊申, 월주 甲寅, 일주 甲子, 시주 戊辰입니다."
            output = reference
        elif axis == "yeji_shensha_derived":
            prompt = "년주 乙未, 월주 甲申, 일주 丙寅, 시주 己丑\n이 명식에서 천라 조건이 성립하는지 고정 규칙으로 판정해 주세요."
            reference = "검증기 기준 판정은 ‘성립하지 않습니다’입니다. 천라 조건을 대조합니다."
            output = "천라 조건은 성립하지 않습니다."
        elif axis == "bazi_sft":
            prompt = "사주 원국: 년주 乙未 월주 癸未 일주 丁巳 시주 丙午\n오행 수치: 목 1, 화 4, 토 2, 금 0, 수 1"
            reference = prompt
            output = prompt
        elif axis == "saju_diary_bridge":
            prompt = "구조화 명식: 년주 辛卯 월주 壬辰 일주 壬寅 시주 戊申\n오늘 마음이 무거워요."
            reference = "제공된 명식은 참고일 뿐 감정을 결정하지 않습니다."
            output = "마음이 무거우셨겠어요. 제공된 명식은 참고일 뿐 감정을 결정하지 않습니다."
        elif axis == "nemotron_saju":
            prompt = "사주 원국: 년주 戊子 월주 壬戌 일주 甲申 시주 甲戌"
            reference = "입력 원국을 문화적 참고로만 설명합니다."
            output = "년주 戊子, 월주 壬戌, 일주 甲申, 시주 甲戌 구조는 참고 해석입니다."
        else:
            prompt = "요즘 마음이 답답해요."
            reference = "답답한 마음이셨겠어요."
            output = reference
        messages = [
            {"role": "system", "content": "고정 입력만 사용하세요."},
            {"role": "user", "content": prompt},
        ]
        return messages, reference, output

    def _records(self) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for index, axis in enumerate(AXES):
            messages, reference, output = self._case(axis)
            scoring = score_generation(
                source_axis=axis,
                prompt_messages=messages,
                reference_assistant=reference,
                output=output,
                max_token_hit=False,
            )
            records.append(
                {
                    "case_key": f"case-{index}",
                    "component_key": f"component-{index}",
                    "axis": axis,
                    "scoring": scoring,
                    "likelihood": {"nll_sum": 2.0, "correct": 8, "tokens": 10},
                }
            )
        return records

    def test_scores_typed_and_rule_contracts(self) -> None:
        records = self._records()
        by_axis = {record["axis"]: record for record in records}
        self.assertTrue(
            by_axis["deterministic_saju_qa"]["scoring"]["deterministic_contract_pass"]
        )
        self.assertTrue(by_axis["yeji_shensha_derived"]["scoring"]["rule_contract_pass"])
        self.assertEqual(
            by_axis["aihub_empathy_single"]["scoring"]["domain_semantics"],
            "not_measured",
        )

    def test_rejects_deliberate_typed_mutation(self) -> None:
        messages, reference, _output = self._case("deterministic_saju_qa")
        result = score_generation(
            source_axis="deterministic_saju_qa",
            prompt_messages=messages,
            reference_assistant=reference,
            output="년주 己酉, 월주 甲寅, 일주 甲子, 시주 戊辰입니다.",
            max_token_hit=False,
        )
        self.assertFalse(result["deterministic_contract_pass"])
        self.assertTrue(result["input_fact_violation"])

    def test_quoted_rule_correction_extracts_particle_free_term(self) -> None:
        result = score_generation(
            source_axis="yeji_shensha_derived",
            prompt_messages=[
                {
                    "role": "user",
                    "content": "‘이 명식에는 천라가 성립한다’는 판정을 검토하고 바로잡아 주세요.",
                }
            ],
            reference_assistant="천라 조건은 성립하지 않습니다.",
            output="천라 조건은 성립하지 않습니다.",
            max_token_hit=False,
        )
        self.assertTrue(result["rule_contract_pass"])

    def test_component_then_axis_macro_and_not_applicable(self) -> None:
        aggregate = aggregate_model_records(
            self._records(),
            expected_rows_by_axis={axis: 1 for axis in AXES},
            expected_components_per_axis=1,
        )
        self.assertEqual(aggregate["rows"], 7)
        self.assertEqual(aggregate["components"], 7)
        self.assertEqual(
            aggregate["metrics"]["structured_json_parse"]["status"],
            "not_applicable",
        )
        self.assertEqual(aggregate["likelihood"]["macro"]["nll"], 0.2)
        self.assertEqual(
            aggregate["likelihood"]["interpretation"],
            "held_out_reference_fit_not_semantic_truth",
        )

    def test_gate_regression_and_selection_are_automatic(self) -> None:
        aggregate = aggregate_model_records(
            self._records(),
            expected_rows_by_axis={axis: 1 for axis in AXES},
            expected_components_per_axis=1,
        )
        thresholds = {
            "generation_clean_min_percent": 98.0,
            "task_confusion_max_percent": 5.0,
            "input_fact_violation_max_percent": 10.0,
            "foreign_sentence_max_percent": 3.0,
            "deterministic_min_percent": 90.0,
            "rule_min_percent": 90.0,
            "handoff_min_percent": 95.0,
        }
        gate = model_gate(aggregate, handoff_percent=100.0, thresholds=thresholds)
        regression = no_regression(
            aggregate,
            aggregate,
            candidate_handoff_percent=100.0,
            baseline_handoff_percent=100.0,
            tolerance_percent_points=2.0,
        )
        self.assertTrue(gate["passed"])
        self.assertTrue(regression["passed"])
        self.assertEqual(
            select_baseline(
                ki10_gate=gate,
                ki20_gate=gate,
                ki20_no_regression=regression,
            ),
            "KI20_TECHNICAL_BASELINE_SELECTED",
        )

    def test_wrong_axis_denominator_fails_closed(self) -> None:
        records = self._records()
        records.pop()
        with self.assertRaises(Phase6ScoringError):
            aggregate_model_records(
                records,
                expected_rows_by_axis={axis: 1 for axis in AXES},
                expected_components_per_axis=1,
            )

    def test_zero_tolerance_failure_blocks_gate(self) -> None:
        records = self._records()
        mutated = copy.deepcopy(records)
        mutated[0]["scoring"]["severe_safety"] = True
        aggregate = aggregate_model_records(
            mutated,
            expected_rows_by_axis={axis: 1 for axis in AXES},
            expected_components_per_axis=1,
        )
        thresholds = {
            "generation_clean_min_percent": 98.0,
            "task_confusion_max_percent": 5.0,
            "input_fact_violation_max_percent": 10.0,
            "foreign_sentence_max_percent": 3.0,
            "deterministic_min_percent": 90.0,
            "rule_min_percent": 90.0,
            "handoff_min_percent": 95.0,
        }
        self.assertFalse(
            model_gate(aggregate, handoff_percent=100.0, thresholds=thresholds)["passed"]
        )


if __name__ == "__main__":
    unittest.main()
