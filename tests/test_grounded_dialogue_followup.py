# test_grounded_dialogue_followup.py - 재채점·장문 계약과 자동 결과 경계를 검증한다.

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.evaluation.grounded_dialogue.contracts import (
    prepare_context as prepare_parent_context,
)
from scripts.evaluation.grounded_dialogue_followup.context_cases import (
    build_context_cases,
)
from scripts.evaluation.grounded_dialogue_followup.contracts import (
    prepare_context,
    validate_contract,
)
from scripts.evaluation.grounded_dialogue_followup.graders import (
    grade_response,
    provided_field_reask,
)
from scripts.evaluation.grounded_dialogue_followup.reporting import (
    build_context_aggregate,
    build_rescore_aggregate,
)
from scripts.evaluation.grounded_dialogue_followup.runner import _load_parent_rows

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs/evaluation/grounded_dialogue_followup-v0.2.0.json"
PARENT_CONFIG_PATH = REPO_ROOT / "configs/evaluation/grounded_dialogue_eval-v0.1.0.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class FollowupContractTest(unittest.TestCase):
    def test_contract_and_builds_are_stable(self) -> None:
        result = validate_contract(CONFIG, REPO_ROOT)
        first = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        second = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(first["rescore_build_id"], second["rescore_build_id"])
        self.assertEqual(first["context_build_id"], second["context_build_id"])

    def test_parent_v010_build_identity_is_unchanged(self) -> None:
        parent = prepare_parent_context(
            REPO_ROOT,
            PARENT_CONFIG_PATH,
            require_local_artifacts=False,
        )
        self.assertEqual(parent["evaluation_build_id"], "eval-b6221e5eb03c")

    def test_governance_is_automatic_and_nonpromoting(self) -> None:
        governance = CONFIG["governance"]
        self.assertTrue(governance["repository_local_automatic_metrics_only"])
        self.assertFalse(governance["sealed_blind_access"])
        self.assertFalse(governance["training_execution_allowed"])
        self.assertFalse(governance["promotion_allowed"])
        self.assertFalse(governance["release_approval_allowed"])
        self.assertFalse(governance["application_binding_allowed"])


class DecisionAwareReaskTest(unittest.TestCase):
    def test_leap_month_request_does_not_reask_confirmed_calendar(self) -> None:
        state = {"confirmed_fields": ["birth_date", "calendar"], "explicit_unknown_fields": []}
        self.assertEqual(
            provided_field_reask(
                "음력 생일의 평달 여부 확인 요청.",
                state,
                decision_action="ask_leap_month",
            ),
            [],
        )

    def test_calendar_choice_still_flags_confirmed_calendar(self) -> None:
        state = {"confirmed_fields": ["calendar"], "explicit_unknown_fields": []}
        self.assertEqual(
            provided_field_reask(
                "양력인지 음력인지 다시 알려 주세요.",
                state,
                decision_action="ask_leap_month",
            ),
            ["calendar"],
        )

    def test_unrelated_confirmed_fields_are_not_exempted_by_action(self) -> None:
        state = {
            "confirmed_fields": ["birth_date", "birthplace"],
            "explicit_unknown_fields": [],
        }
        self.assertEqual(
            provided_field_reask(
                "생년월일과 출생지를 다시 알려 주세요.",
                state,
                decision_action="ask_time_precision",
            ),
            ["birth_date", "birthplace"],
        )

    def test_time_precision_and_exact_time_intents_are_separated(self) -> None:
        empty = {"confirmed_fields": [], "explicit_unknown_fields": []}
        self.assertEqual(
            provided_field_reask(
                "출생시각을 정확히 아는지 범위만 아는지 알려 주세요.",
                empty,
                decision_action="ask_time_precision",
            ),
            [],
        )
        unknown = {
            "confirmed_fields": [],
            "explicit_unknown_fields": ["birth_time"],
        }
        self.assertEqual(
            provided_field_reask(
                "정확한 출생시각을 다시 알려 주세요.",
                unknown,
                decision_action="call_chart",
            ),
            ["time_precision"],
        )


class ContextSuiteTest(unittest.TestCase):
    def test_four_token_bands_have_25_cases_and_complete_pairs(self) -> None:
        base_cases = [
            {
                "case_id": f"source-{index:03d}",
                "stratum": f"stratum-{index % 10}",
                "messages": [{"role": "user", "content": "현재 요청입니다."}],
            }
            for index in range(100)
        ]
        systems = {case["case_id"]: "고정 시스템 문장" for case in base_cases}

        def token_counter(messages: object) -> int:
            values = list(messages)
            return sum(len(value["content"]) for value in values) + len(values) * 10

        cases = build_context_cases(
            base_cases,
            systems,
            bands=CONFIG["context_diagnostic"]["bands"],
            token_counter=token_counter,
            denylist=CONFIG["context_diagnostic"]["history_policy"]["lexical_denylist"],
        )
        counts: dict[str, int] = {}
        for case in cases:
            counts[case.band_id] = counts.get(case.band_id, 0) + 1
            self.assertEqual(len(case.history_messages) % 2, 0)
            self.assertLessEqual(case.base_input_tokens, 2048)
            self.assertGreaterEqual(case.original_input_tokens, case.minimum_tokens)
            self.assertLessEqual(case.original_input_tokens, case.maximum_tokens)
        self.assertEqual(set(counts.values()), {25})


def _context_row(index: int, arm_id: str) -> dict[str, object]:
    return {
        "context_case_id": f"context-v1-{index:03d}",
        "stratum": f"stratum-{index % 10}",
        "band_id": CONFIG["context_diagnostic"]["bands"][index % 4]["band_id"],
        "arm_id": arm_id,
        "extraction": {
            "score": {
                "exact_state": True,
                "invalid_extraction": False,
                "time_semantics_pass": True,
                "correction_required": False,
                "correction_pass": True,
            }
        },
        "fsm": {"decision_action": "ask_birth_date"},
        "route": "input_limited_reply",
        "tool_result": {"status": "blocked"},
        "prompt_metadata": {
            "base_input_tokens": 400,
            "original_input_tokens": 1000 + (index % 4) * 1000,
            "final_input_tokens": 1000,
            "max_input_tokens": 2048 if arm_id.startswith("C0") else 3584,
            "dropped_complete_pairs": 0,
            "error_code": None,
        },
        "generation": {
            "attempted": True,
            "error_code": None,
            "output": "확인된 범위에서 안내합니다.",
            "input_tokens": 1000,
            "new_tokens": 12,
            "max_token_hit": False,
        },
        "response_grade": {
            "nonempty": True,
            "max_token_hit": False,
            "fabricated_pillars": [],
            "unknown_hour_violations": [],
            "fact_contradictions": [],
            "false_completion": False,
            "provided_field_reasks": [],
            "severe_safety": [],
        },
    }


class AggregateTest(unittest.TestCase):
    def test_context_aggregate_keeps_2048_when_both_arms_are_clean(self) -> None:
        context = {
            "config": CONFIG,
            "context_build_id": "eval-aaaaaaaaaaaa",
            "context_build_sha256": "a" * 64,
        }
        rows = {
            arm["arm_id"]: [_context_row(index, arm["arm_id"]) for index in range(100)]
            for arm in CONFIG["context_diagnostic"]["arms"]
        }
        aggregate = build_context_aggregate(context, rows)
        self.assertEqual(aggregate["response_generations"], 200)
        self.assertEqual(aggregate["prompt_budget_failure_cases"], 0)
        self.assertEqual(
            aggregate["capacity_recommendation"],
            "2048_sufficient_for_structured_path",
        )
        self.assertFalse(aggregate["runtime_configuration_changed"])
        self.assertEqual(
            aggregate["paired_comparison"]["pre_generation_invariant_cases"], 100
        )


@unittest.skipUnless(
    (REPO_ROOT / CONFIG["parent_evaluation"]["private_manifest"]["path"]).is_file(),
    "완료된 비봉인 부모 진단이 로컬에 없음",
)
class ActualRescoreRegressionTest(unittest.TestCase):
    def test_r1_and_r3_reask_false_positives_are_removed(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=True)
        parent_rows = _load_parent_rows(context, REPO_ROOT)
        rescored: dict[str, list[dict[str, object]]] = {}
        for arm_id, rows in parent_rows.items():
            values = []
            for row in rows:
                value = deepcopy(row)
                tool = value["tool_result"]
                value["response_grade"] = grade_response(
                    value["generation"]["output"],
                    hard_facts=tool.get("hard_facts"),
                    tool_status=tool["status"],
                    session_state=value["grading_session_state"],
                    decision_action=value["fsm"]["decision_action"],
                    max_token_hit=value["generation"]["max_token_hit"],
                )
                values.append(value)
            rescored[arm_id] = values
        parent = json.loads(
            (REPO_ROOT / CONFIG["parent_evaluation"]["public_aggregate"]["path"]).read_text(
                encoding="utf-8"
            )
        )
        aggregate = build_rescore_aggregate(context, rescored, parent)
        for arm_id in ("R1_KI20_ORACLE_2048", "R3_KI20_RULE_2048"):
            comparison = aggregate["comparison_to_parent"][arm_id]
            self.assertEqual(comparison["provided_field_reask_percent_before"], 7.0)
            self.assertEqual(comparison["provided_field_reask_percent_after"], 2.0)
            self.assertTrue(comparison["target_after"])
        self.assertFalse(aggregate["diagnostic_target_met"])
        self.assertFalse(aggregate["response_regenerated"])


if __name__ == "__main__":
    unittest.main()
