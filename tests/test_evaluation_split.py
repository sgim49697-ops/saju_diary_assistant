# test_evaluation_split.py - component blind split과 외부 conformance 계약을 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.evaluation.external_conformance import validate_external_conformance
from scripts.evaluation.phase5_split import (
    AXES,
    EvaluationSplitError,
    _parser,
    select_blind_components,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/evaluation-split-v1.0.0.json"
)


class ExternalConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_kasi_and_policy_fixture_contract(self) -> None:
        report = validate_external_conformance(
            self.config["external_conformance"], REPO_ROOT
        )
        self.assertEqual(report["kasi_primary_snapshot"]["rows"], 200)
        self.assertEqual(report["policy_cases"]["rows"], 20)
        self.assertEqual(
            report["lunar_python_comparison"]["field_conflicts"],
            {
                "lunar_date": 3,
                "leap_month": 0,
                "calendar_year_ganzhi": 0,
                "calendar_month_ganzhi": 64,
                "day_ganzhi": 0,
            },
        )
        self.assertFalse(report["runtime_engine_approved"])
        self.assertFalse(report["evaluation_gold_automatically_promoted"])

    def test_calendar_ganzhi_is_not_saju_pillar_alias(self) -> None:
        report = validate_external_conformance(
            self.config["external_conformance"], REPO_ROOT
        )
        guards = report["lunar_python_comparison"]["semantic_guard"]
        self.assertTrue(guards["calendar_year_ganzhi_is_not_saju_year_pillar"])
        self.assertTrue(guards["calendar_month_ganzhi_is_not_saju_month_pillar"])


class BlindSelectionTests(unittest.TestCase):
    @staticmethod
    def _candidate(
        axis: str,
        index: int,
        **values: object,
    ) -> dict[str, object]:
        return {
            "component_id": f"component:{axis}:{index:03d}",
            "record_ids": [f"{axis}:{index:03d}"],
            "row_count": 1,
            "source_variant": None,
            "qa_category": None,
            "task_presentation": None,
            "emotion_type": None,
            "question_types": ["None"],
            **values,
        }

    @classmethod
    def _candidates(cls) -> dict[str, list[dict[str, object]]]:
        values: dict[str, list[dict[str, object]]] = {axis: [] for axis in AXES}
        for index in range(60):
            values["nemotron_saju"].append(
                cls._candidate(
                    "nemotron_saju",
                    index,
                    source_variant="v6" if index < 20 else "v7",
                )
            )
            values["bazi_sft"].append(
                cls._candidate(
                    "bazi_sft",
                    index,
                    record_ids=[f"bazi:{index:03d}:{member}" for member in range(4)],
                    row_count=4,
                    question_types=[
                        "career",
                        "element_balance",
                        "general_natal",
                        "relationships",
                    ],
                )
            )
            for axis in (
                "aihub_empathy_single",
                "aihub_empathy_multiturn",
                "saju_diary_bridge",
            ):
                values[axis].append(
                    cls._candidate(axis, index, emotion_type=f"E{index:02d}")
                )
            values["yeji_shensha_derived"].append(
                cls._candidate(
                    "yeji_shensha_derived",
                    index,
                    task_presentation=(
                        "shensha_neutral_explanation"
                        if index < 30
                        else "shensha_rule_validation"
                    ),
                )
            )
        categories = (
            "branch_ten_gods",
            "hidden_stems",
            "stem_branch_identity",
            "stem_ten_gods",
            "yin_yang_elements_and_surface_counts",
        )
        for index in range(60):
            values["deterministic_saju_qa"].append(
                cls._candidate(
                    "deterministic_saju_qa",
                    index,
                    qa_category=categories[index % len(categories)],
                )
            )
        return values

    @staticmethod
    def _strata() -> dict[str, dict[str, object]]:
        return {
            "nemotron_saju": {
                "field": "source_variant",
                "quotas": {"v6": 10, "v7": 40},
            },
            "bazi_sft": {
                "component_rows": 4,
                "required_member_values": [
                    "career",
                    "element_balance",
                    "general_natal",
                    "relationships",
                ],
            },
            "aihub_empathy_single": {"field": "emotion_type", "distinct_min": 50},
            "aihub_empathy_multiturn": {
                "field": "emotion_type",
                "distinct_min": 50,
            },
            "yeji_shensha_derived": {
                "field": "task_presentation",
                "quotas": {
                    "shensha_neutral_explanation": 25,
                    "shensha_rule_validation": 25,
                },
            },
            "deterministic_saju_qa": {
                "field": "qa_category",
                "quotas": {
                    "branch_ten_gods": 10,
                    "hidden_stems": 10,
                    "stem_branch_identity": 10,
                    "stem_ten_gods": 10,
                    "yin_yang_elements_and_surface_counts": 10,
                },
            },
            "saju_diary_bridge": {"field": "emotion_type", "distinct_min": 50},
        }

    def test_component_selection_is_deterministic_and_stratified(self) -> None:
        candidates = self._candidates()
        first = select_blind_components(
            candidates,
            self._strata(),
            seed=42,
            namespace="saju-blind-source-v1",
            count_per_axis=50,
        )
        reversed_candidates = {
            axis: list(reversed(rows)) for axis, rows in candidates.items()
        }
        second = select_blind_components(
            reversed_candidates,
            self._strata(),
            seed=42,
            namespace="saju-blind-source-v1",
            count_per_axis=50,
        )
        self.assertEqual(first, second)
        self.assertEqual({axis: len(rows) for axis, rows in first.items()}, {axis: 50 for axis in AXES})
        self.assertEqual(
            {value: sum(row["source_variant"] == value for row in first["nemotron_saju"]) for value in ("v6", "v7")},
            {"v6": 10, "v7": 40},
        )
        self.assertEqual(
            len({row["emotion_type"] for row in first["aihub_empathy_single"]}),
            50,
        )

    def test_bazi_component_must_keep_all_four_question_types(self) -> None:
        candidates = self._candidates()
        for candidate in candidates["bazi_sft"]:
            candidate["row_count"] = 3
        with self.assertRaises(EvaluationSplitError):
            select_blind_components(
                candidates,
                self._strata(),
                seed=42,
                namespace="saju-blind-source-v1",
                count_per_axis=50,
            )


class EvaluationSplitContractTests(unittest.TestCase):
    def test_committed_contract_is_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        self.assertEqual(result["blind_components"], 350)
        self.assertEqual(result["blind_rows"], 500)
        self.assertFalse(result["phase5_training_performed"])

    def test_prepare_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)

    def test_split_module_contains_no_training_execution(self) -> None:
        source = (
            REPO_ROOT / "scripts/evaluation/phase5_split.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(".train(", source)
        self.assertNotIn(".backward(", source)
        self.assertNotIn("optimizer.step(", source)


if __name__ == "__main__":
    unittest.main()
