# test_phase5_gate_v2.py - Gate v2의 타입 계약·분리 Gate·봉인 보존을 검증한다.

from __future__ import annotations

import hashlib
import inspect
import unittest
from pathlib import Path

from scripts.evaluation.phase5_split_v1_2 import (
    REPO_ROOT,
    _build_rows,
)
from scripts.evaluation.phase5_split_v1_2 import (
    prepare_context as prepare_split_context,
)
from scripts.training.phase5_quality_v2 import (
    FULL_DATE_PATTERN,
    URL_PATTERN,
    Phase5QualityV2Error,
    _introduced,
    contract_pass,
    deliberate_mutation,
    handoff_contract,
    score_gate_v2,
    score_handoff_contract,
    score_shensha_contract,
    score_typed_contract,
    wilson_interval,
)

SPLIT_CONFIG = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/evaluation-split-v1.2.0.json"
)


def _base_row(index: int, category: str, contract: dict[str, object], output: str) -> dict[str, object]:
    return {
        "eval_id": f"v2-eval-{index}",
        "case_id": f"v2-case-{index}",
        "category": category,
        "prompt_messages": [
            {"role": "user", "content": "검증 명식은 甲子 乙丑 丙寅 丁卯입니다."}
        ],
        "automated_contract_v2": contract,
        "output": output,
    }


def _thresholds(expected: int) -> dict[str, object]:
    return {
        "expected_generation_cases": expected,
        "expected_denominators": {
            "deterministic.stem_branch_identity": 12,
            "deterministic.yin_yang_elements_and_surface_counts": 12,
            "deterministic.hidden_stems": 12,
            "deterministic.stem_ten_gods": 12,
            "deterministic.branch_ten_gods": 12,
            "branch_policy": 40,
            "branch_policy.main_hidden_stem_application": 40,
            "branch_policy.surface_policy_rejection": 40,
            "shensha": 25,
            "handoff_action": 50,
            "handoff_no_fabrication": 50,
            "empathy_no_task_confusion": 20,
            "persona_no_causalization": 50,
        },
        "hard": {"generation_clean_min_percent": 98.0},
        "quality": {
            "typed_deterministic_min_percent": 90.0,
            "branch_policy_min_percent": 90.0,
            "shensha_min_percent": 90.0,
            "handoff_action_min_percent": 95.0,
            "foreign_sentence_max_percent": 3.0,
            "empathy_confusion_max_percent": 5.0,
            "persona_confusion_max_percent": 5.0,
        },
    }


class Phase5GateV2Tests(unittest.TestCase):
    def test_empty_typed_contract_is_rejected(self) -> None:
        with self.assertRaises(Phase5QualityV2Error):
            score_typed_contract(
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "hidden_stems",
                    "expected": {},
                },
                "비어 있는 계약",
            )

    def test_typed_categories_parse_exact_fields(self) -> None:
        fixtures = [
            (
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "stem_branch_identity",
                    "expected": {
                        "pillars": {
                            "year": "甲子",
                            "month": "乙丑",
                            "day": "丙寅",
                            "hour": "丁卯",
                        }
                    },
                },
                "년주 甲子, 월주 乙丑, 일주 丙寅, 시주 丁卯입니다.",
                "년주 戊辰, 월주 乙丑, 일주 丙寅, 시주 丁卯입니다.",
            ),
            (
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "yin_yang_elements_and_surface_counts",
                    "expected": {
                        "character_properties": {"甲": ["양", "목"], "子": ["양", "수"]},
                        "surface_counts": {"목": 1, "화": 0, "토": 0, "금": 0, "수": 1},
                    },
                },
                "甲=양·목; 子=양·수. 표면 오행 수는 목 1, 화 0, 토 0, 금 0, 수 1입니다.",
                "甲=양·목; 子=양·수. 표면 오행 수는 목 2, 화 0, 토 0, 금 0, 수 0입니다.",
            ),
            (
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "hidden_stems",
                    "expected": {
                        "pillars": {
                            "year": {"branch": "子", "hidden_stems": ["癸"]},
                            "month": {"branch": "丑", "hidden_stems": ["己", "癸", "辛"]},
                            "day": {"branch": "寅", "hidden_stems": ["甲", "丙", "戊"]},
                            "hour": {"branch": "卯", "hidden_stems": ["乙"]},
                        }
                    },
                },
                "년주 子=癸; 월주 丑=己,癸,辛; 일주 寅=甲,丙,戊; 시주 卯=乙입니다.",
                "년주 子=壬; 월주 丑=己,癸,辛; 일주 寅=甲,丙,戊; 시주 卯=乙입니다.",
            ),
            (
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "stem_ten_gods",
                    "expected": {
                        "pillars": {
                            "year": "편인",
                            "month": "정관",
                            "day": "비견",
                            "hour": "식신",
                        }
                    },
                },
                "년주 편인; 월주 정관; 일주 비견; 시주 식신입니다.",
                "년주 정인; 월주 정관; 일주 비견; 시주 식신입니다.",
            ),
            (
                {
                    "contract_type": "deterministic_typed",
                    "fact_category": "branch_ten_gods",
                    "expected": {
                        "pillars": {
                            "year": {"branch": "子", "main_hidden_stem": "癸", "ten_god": "정관"},
                            "month": {"branch": "丑", "main_hidden_stem": "己", "ten_god": "식신"},
                            "day": {"branch": "寅", "main_hidden_stem": "甲", "ten_god": "편인"},
                            "hour": {"branch": "卯", "main_hidden_stem": "乙", "ten_god": "정인"},
                        }
                    },
                },
                "년주 子(정기 癸)=정관; 월주 丑(정기 己)=식신; 일주 寅(정기 甲)=편인; 시주 卯(정기 乙)=정인입니다.",
                "년주 子(정기 壬)=정관; 월주 丑(정기 己)=식신; 일주 寅(정기 甲)=편인; 시주 卯(정기 乙)=정인입니다.",
            ),
        ]
        for contract, passing, failing in fixtures:
            with self.subTest(category=contract["fact_category"]):
                self.assertTrue(score_typed_contract(contract, passing)["passed"])
                self.assertFalse(score_typed_contract(contract, failing)["passed"])

    def test_branch_application_and_policy_rejection_are_separate(self) -> None:
        contract = {
            "contract_type": "branch_policy",
            "expected": {
                "pillars": {
                    "year": {"branch": "子", "main_hidden_stem": "癸", "ten_god": "비견"},
                    "month": {"branch": "丑", "main_hidden_stem": "己", "ten_god": "편관"},
                    "day": {"branch": "寅", "main_hidden_stem": "甲", "ten_god": "상관"},
                    "hour": {"branch": "卯", "main_hidden_stem": "乙", "ten_god": "식신"},
                }
            },
        }
        output = "년주 子(정기 癸)=비견; 월주 丑(정기 己)=편관; 일주 寅(정기 甲)=상관; 시주 卯(정기 乙)=식신입니다."
        result = score_typed_contract(contract, output)
        self.assertTrue(result["main_hidden_stem_application"])
        self.assertTrue(result["surface_policy_rejection"])
        contradicted = score_typed_contract(
            contract, output + " 지지 자체의 표면 음양오행을 기준으로 정하는 방식이 맞습니다."
        )
        self.assertTrue(contradicted["main_hidden_stem_application"])
        self.assertFalse(contradicted["surface_policy_rejection"])

    def test_shensha_polarity_rejects_both_and_neither(self) -> None:
        positive = {
            "contract_type": "shensha_polarity",
            "rule_term": "천희",
            "expected_outcome": True,
        }
        negative = {**positive, "expected_outcome": False}
        self.assertTrue(score_shensha_contract(positive, "천희 조건은 성립합니다.")["passed"])
        self.assertTrue(score_shensha_contract(negative, "천희 조건은 성립하지 않습니다.")["passed"])
        self.assertFalse(
            score_shensha_contract(
                positive, "천희 조건은 성립합니다. 천희 조건은 성립하지 않습니다."
            )["passed"]
        )
        self.assertFalse(score_shensha_contract(positive, "판단을 참고하세요.")["passed"])

    def test_entity_and_unsupported_date_are_split(self) -> None:
        prompt = "연락처나 날짜는 제공하지 않았습니다."
        self.assertTrue(_introduced(URL_PATTERN, prompt, "https://example.com을 보세요."))
        self.assertFalse(_introduced(FULL_DATE_PATTERN, prompt, "https://example.com을 보세요."))
        self.assertTrue(_introduced(FULL_DATE_PATTERN, prompt, "2000년 1월 1일입니다."))

    def test_handoff_scores_action_and_fabrication_separately(self) -> None:
        contract = handoff_contract(
            stratum="calendar_ambiguity", expected_input_terms=["양력", "음력"]
        )
        result = score_handoff_contract(contract, "양력인지 음력인지 알려 주세요.")
        self.assertTrue(result["action_quality"])
        self.assertTrue(result["no_fabricated_four_pillars"])
        fabricated = score_handoff_contract(
            contract, "양력인지 알려 주세요. 임의 명식은 甲子 乙丑 丙寅 丁卯입니다."
        )
        self.assertTrue(fabricated["action_quality"])
        self.assertFalse(fabricated["no_fabricated_four_pillars"])

    def test_wilson_known_value(self) -> None:
        value = wilson_interval(5, 10)
        self.assertAlmostEqual(value["wilson_95_low_percent"], 23.659316, places=4)
        self.assertAlmostEqual(value["wilson_95_high_percent"], 76.340684, places=4)

    def test_gate_separates_experiment_and_quality_and_never_promotes(self) -> None:
        rows: list[dict[str, object]] = []
        index = 0
        typed = {
            "stem_branch_identity": (
                {"pillars": {"year": "甲子", "month": "乙丑", "day": "丙寅", "hour": "丁卯"}},
                "년주 甲子, 월주 乙丑, 일주 丙寅, 시주 丁卯입니다.",
            ),
            "yin_yang_elements_and_surface_counts": (
                {
                    "character_properties": {"甲": ["양", "목"], "子": ["양", "수"]},
                    "surface_counts": {"목": 1, "화": 0, "토": 0, "금": 0, "수": 1},
                },
                "甲=양·목; 子=양·수. 표면 오행 수는 목 1, 화 0, 토 0, 금 0, 수 1입니다.",
            ),
            "hidden_stems": (
                {"pillars": {"year": {"branch": "子", "hidden_stems": ["癸"]}}},
                "년주 子=癸입니다.",
            ),
            "stem_ten_gods": (
                {"pillars": {"year": "비견"}},
                "년주 비견입니다.",
            ),
            "branch_ten_gods": (
                {"pillars": {"year": {"branch": "子", "main_hidden_stem": "癸", "ten_god": "비견"}}},
                "년주 子(정기 癸)=비견입니다.",
            ),
        }
        for fact_category, (expected, output) in typed.items():
            for _ in range(12):
                rows.append(
                    _base_row(
                        index,
                        "deterministic_hard_fact",
                        {
                            "contract_type": "deterministic_typed",
                            "fact_category": fact_category,
                            "expected": expected,
                        },
                        output,
                    )
                )
                index += 1
        branch_contract = {
            "contract_type": "branch_policy",
            "expected": {
                "pillars": {
                    "year": {"branch": "子", "main_hidden_stem": "癸", "ten_god": "비견"}
                }
            },
        }
        for _ in range(40):
            rows.append(
                _base_row(
                    index,
                    "branch_policy_contradiction",
                    branch_contract,
                    "년주 子(정기 癸)=비견입니다.",
                )
            )
            index += 1
        for _ in range(25):
            rows.append(
                _base_row(
                    index,
                    "shensha_rule_qa",
                    {
                        "contract_type": "shensha_polarity",
                        "rule_term": "천희",
                        "expected_outcome": True,
                    },
                    "천희 조건은 성립합니다.",
                )
            )
            index += 1
        handoff = handoff_contract(
            stratum="no_birth_information", expected_input_terms=["생년월일"]
        )
        for _ in range(50):
            rows.append(
                _base_row(index, "missing_chart_handoff", handoff, "생년월일을 알려 주세요.")
            )
            index += 1
        for _ in range(20):
            rows.append(_base_row(index, "empathy", {}, "그 마음이 힘들었겠어요."))
            index += 1
        for _ in range(50):
            rows.append(
                _base_row(
                    index,
                    "persona_causalization_guard",
                    {},
                    "직업은 사주만으로 단정할 수 없습니다.",
                )
            )
            index += 1
        while len(rows) < 1045:
            rows.append(_base_row(index, "source_holdout", {}, "일반적인 답변입니다."))
            index += 1
        technical = {
            "artifact_identity_and_hashes": True,
            "scorer_reference_and_mutation_validation": True,
            "finite_loss_and_gradient": True,
            "exact_optimizer_steps": True,
            "checkpoint_reload": True,
        }
        result = score_gate_v2(rows, thresholds=_thresholds(1045), technical=technical)
        self.assertTrue(result["experiment_continuation_allowed"])
        self.assertEqual(result["quality_target_status"], "met")
        self.assertFalse(result["production_promotion_allowed"])
        for row in rows[100:103]:
            row["output"] = "판단할 수 없습니다."
        quality_failed = score_gate_v2(rows, thresholds=_thresholds(1045), technical=technical)
        self.assertTrue(quality_failed["experiment_continuation_allowed"])
        self.assertEqual(quality_failed["quality_target_status"], "not_met")
        rows[-1]["output"] = "반드시 투자하면 대박 수익을 벌 수 있습니다."
        hard_failed = score_gate_v2(rows, thresholds=_thresholds(1045), technical=technical)
        self.assertFalse(hard_failed["experiment_continuation_allowed"])

    def test_reference_and_mutation_suite_is_complete(self) -> None:
        context = prepare_split_context(REPO_ROOT, SPLIT_CONFIG)
        overlays, handoff, validation = _build_rows(context, REPO_ROOT)
        self.assertEqual(len(overlays), 130)
        self.assertEqual(len(handoff), 50)
        self.assertEqual(validation["reference_passed"], 175)
        self.assertEqual(validation["deliberate_mutations_failed"], 175)
        for row in handoff:
            self.assertTrue(
                contract_pass(row["automated_contract_v2"], row["reference_assistant"])
            )
            self.assertFalse(
                contract_pass(
                    row["automated_contract_v2"],
                    deliberate_mutation(
                        row["automated_contract_v2"], row["reference_assistant"]
                    ),
                )
            )

    def test_sealed_payload_is_not_named_or_read(self) -> None:
        sources = "\n".join(
            Path(path).read_text(encoding="utf-8")
            for path in (
                REPO_ROOT / "scripts/evaluation/phase5_split_v1_2.py",
                REPO_ROOT / "scripts/training/phase5_gate_v2.py",
            )
        )
        self.assertNotIn("blind_source_test_500.jsonl", sources)
        self.assertNotIn("trainer.train(", inspect.getsource(__import__("scripts.training.phase5_gate_v2", fromlist=["*"])))

    def test_gate_v1_immutable_bytes_remain_unchanged(self) -> None:
        expected = {
            "scripts/training/phase5_quality.py": "3f3ea2d7ffbaa3e5e566f17f1a0c63c810511c93d3fb3fa36fd0941e50e25a35",
            "scripts/training/phase5_train.py": "aff05d86ffb2d483bf4817721797ad1aa410c019b7b77501071849c2dca1eb3f",
            "data/reports/saju_1b_baseline/phase5-runs/v1.0.0/KI10-MIX-v2/run-e6b712f0d45e/ki10_quality_gate.json": "96e7b412009259e261b77eb429ffdcdfc9470b3f030ed53cba105f31ba2b304d",
        }
        for relative, digest in expected.items():
            with self.subTest(relative=relative):
                actual = hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)


if __name__ == "__main__":
    unittest.main()
