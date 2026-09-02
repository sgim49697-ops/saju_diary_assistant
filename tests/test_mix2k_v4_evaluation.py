# test_mix2k_v4_evaluation.py - frozen dev 5-arm 평가 계약과 핵심 지표를 검증한다.

from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation.mix2k_v4.backends import effective_generation_payload
from scripts.evaluation.mix2k_v4.contracts import (
    DEFAULT_CONFIG,
    REGRESSION_ID,
    Mix2KV4EvaluationError,
    validate_config,
)
from scripts.evaluation.mix2k_v4.graders import (
    grade_turn,
    repeated_ngram_ratio,
    required_fact_omissions,
)
from scripts.evaluation.mix2k_v4.reporting import aggregate_arm, build_aggregate
from scripts.evaluation.mix2k_v4.reviews import (
    _external_review_preflight,
    _review_input_set_sha256,
    _validate_provider_output,
    run_quality_review,
)
from scripts.evaluation.mix2k_v4.runner import _validate_case_item, sha256z
from tests.test_mix2k_v4 import _binding


def _case(
    *, axis: str = "natal_and_today", regression: bool = True
) -> dict[str, object]:
    binding = _binding()
    return {
        "schema_version": "1.0.0",
        "case_id": REGRESSION_ID if regression else "dev2k_test",
        "axis": axis,
        "messages": [
            {"role": "system", "content": "승인 사실을 사용하세요."},
            {"role": "user", "content": "오늘의 흐름을 원국과 함께 이야기해줘"},
        ],
        "followup_turns": ["무슨 말인지 모르겠어 좀 풀어서 설명해줘"],
        "runtime_binding": binding,
        "expected_structural_facts": {
            "natal_pillars": {
                "year": "戊辰",
                "month": "甲子",
                "day": "乙丑",
                "hour": "壬午",
            },
            "day_master": "乙",
            "target_date": "2026-09-02",
            "period_year_ganzhi": "丙午",
            "period_month_ganzhi": "丙申",
            "period_day_ganzhi": "己卯",
        },
        "minimum_substantive_sentences": 3,
        "minimum_substantive_nonempty_lines": 3,
    }


def _regression_row(arm_id: str) -> dict[str, object]:
    case = _case()
    initial = (
        "원국 전체는 연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午이며 일간은 乙입니다.\n"
        "2026-09-02의 연간지는 丙午, 월간지는 丙申, 그날의 일진은 己卯입니다.\n"
        "관계 계산은 제공되지 않았으므로, 두 자료를 구분해 오늘의 성찰 참고로만 보겠습니다."
    )
    followup = (
        "쉽게 말하면 원국의 일주 乙丑은 태어날 때의 네 기둥 중 한 자리입니다.\n"
        "오늘 따로 확인된 값은 일진 己卯이므로, 원국 전체와 오늘 날짜를 같은 것으로 보면 안 됩니다.\n"
        "두 값의 관계는 계산되어 있지 않으니, 확정적인 사건 예측 대신 하루를 돌아보는 참고로 써 주세요."
    )
    return {
        "schema_version": "1.0.0",
        "evaluation_id": "eval-test",
        "arm_id": arm_id,
        "arm_artifact_sha256": "a" * 64,
        "case_id": case["case_id"],
        "case_sha256": sha256z(case),
        "axis": case["axis"],
        "runtime_snapshot_sha256": case["runtime_binding"]["snapshot_sha256"],
        "turns": [
            {
                "turn_index": 0,
                "user": case["messages"][-1]["content"],
                "output": initial,
                "input_tokens": 1600,
                "new_tokens": 120,
                "max_token_hit": False,
                "input_over_budget": False,
                "elapsed_seconds": 1.0,
                "grade": grade_turn(case, initial, turn_index=0),
            },
            {
                "turn_index": 1,
                "user": case["followup_turns"][0],
                "output": followup,
                "input_tokens": 1800,
                "new_tokens": 110,
                "max_token_hit": False,
                "input_over_budget": False,
                "elapsed_seconds": 1.0,
                "grade": grade_turn(
                    case, followup, turn_index=1, prior_outputs=[initial]
                ),
            },
        ],
        "peak_allocated_bytes": 100,
        "peak_reserved_bytes": 120,
        "raw_outputs_private": True,
        "correction_retry_performed": False,
        "generated_at_utc": "2026-09-02T00:00:00Z",
    }


class Mix2KV4EvaluationTests(unittest.TestCase):
    def test_config_pins_five_arms_and_raw_4k_generation(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        self.assertEqual(
            [item["arm_id"] for item in config["arms"]],
            ["K0", "LORA_R8", "LORA_R16", "LORA_R32", "KI20"],
        )
        self.assertEqual(config["generation"]["max_input_tokens"], 4096)
        self.assertEqual(config["generation"]["max_new_tokens"], 4096)
        self.assertFalse(config["generation"]["retry_or_grounding_rewrite_allowed"])
        self.assertTrue(
            config["generation"]["ignore_model_directory_generation_config"]
        )
        self.assertEqual(config["generation"]["eos_token_id"], [128010])
        self.assertTrue(config["quality_review"]["required"])
        self.assertTrue(
            config["quality_review"]["external_transmission"][
                "explicit_operator_approval_required"
            ]
        )
        self.assertTrue(
            config["quality_review"]["external_transmission"][
                "heuristic_scan_cannot_exclude_memorization"
            ]
        )
        self.assertEqual(
            config["release"]["metric_thresholds"][
                "task_fulfillment_minimum_score_minimum"
            ],
            3.0,
        )
        for model in config["model_contracts"].values():
            self.assertIn(
                "configuration_kanana2_tiny.py", model["required_file_sha256"]
            )
            self.assertIn("modeling_kanana2_tiny.py", model["required_file_sha256"])

    def test_config_rejects_model_output_and_review_contract_override(self) -> None:
        original = validate_config(DEFAULT_CONFIG)
        mutations = []
        model = deepcopy(original)
        model["model_contracts"]["k0"]["relative_path"] = "/tmp/evil"
        mutations.append(model)
        outputs = deepcopy(original)
        outputs["outputs"]["private_root"] = "../../outside"
        mutations.append(outputs)
        review = deepcopy(original)
        review["quality_review"]["dimensions"] = []
        mutations.append(review)
        for value in mutations:
            with (
                self.subTest(value=value),
                patch(
                    "scripts.evaluation.mix2k_v4.contracts.load_json",
                    return_value=value,
                ),
                self.assertRaises(Mix2KV4EvaluationError),
            ):
                validate_config(DEFAULT_CONFIG)

    def test_exact_regression_initial_and_followup_pass(self) -> None:
        case = _case()
        initial = (
            "원국 전체는 연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午이며 일간은 乙입니다.\n"
            "2026-09-02의 연간지는 丙午, 월간지는 丙申, 그날의 일진은 己卯입니다.\n"
            "관계 계산은 제공되지 않았으므로, 두 자료를 구분해 오늘의 성찰 참고로만 보겠습니다."
        )
        first = grade_turn(case, initial, turn_index=0)
        self.assertTrue(first["regression_turn_pass"])
        self.assertEqual(first["structural_claim_errors"], [])

        followup = (
            "쉽게 말하면 원국의 일주 乙丑은 태어날 때의 네 기둥 중 한 자리입니다.\n"
            "오늘 따로 확인된 값은 일진 己卯이므로, 원국 전체와 오늘 날짜를 같은 것으로 보면 안 됩니다.\n"
            "두 값의 관계는 계산되어 있지 않으니, 확정적인 사건 예측 대신 하루를 돌아보는 참고로 써 주세요."
        )
        second = grade_turn(
            case,
            followup,
            turn_index=1,
            prior_outputs=[initial],
        )
        self.assertTrue(second["regression_turn_pass"])
        self.assertTrue(second["followup_evidence_consistent"])

    def test_actual_bad_dashboard_response_fails_release_blocker(self) -> None:
        output = (
            "연결된 승인 원국 사실 乙丑과 오늘 일진 丙午를 볼 수 있습니다.\n"
            "두 원국을 나란히 둘 수 있습니다.\n"
            "이 내용을 참고할 수 있습니다."
        )
        grade = grade_turn(_case(), output, turn_index=0)
        self.assertFalse(grade["regression_turn_pass"])
        self.assertTrue(grade["natal_period_label_confusion"])
        self.assertIn(
            "period_year_called_day_ganzhi",
            grade["structural_claim_errors"],
        )

    def test_general_reply_detects_false_saju_injection(self) -> None:
        case = {
            **_case(axis="general_empathy", regression=False),
            "messages": [
                {"role": "system", "content": "자연스럽게 답하세요."},
                {"role": "user", "content": "오늘 일이 안 풀려 마음이 무거워."},
            ],
            "followup_turns": [],
            "runtime_binding": None,
            "expected_structural_facts": None,
        }
        output = (
            "마음이 무거웠겠어요.\n"
            "우선 오늘 가장 힘들었던 한 가지를 적어 보세요.\n"
            "그다음 지금 바꿀 수 있는 작은 행동 하나만 골라도 충분합니다."
        )
        self.assertTrue(
            grade_turn(case, output, turn_index=0)[
                "general_conversation_retention_deterministic"
            ]
        )
        injected = output + "\n사주의 오행도 함께 보면 좋습니다."
        grade = grade_turn(case, injected, turn_index=0)
        self.assertTrue(grade["false_saju_injection"])
        self.assertFalse(grade["general_conversation_retention_deterministic"])
        for ordinary in (
            "그 결과와 상관이 없어도 괜찮습니다.\n음악 연주를 들어 보세요.\n일간 계획은 작게 잡아도 됩니다.",
            "일주일 동안 애썼네요.\n결과와 상관은 없어요.\n오늘은 쉬어도 됩니다.",
        ):
            with self.subTest(ordinary=ordinary):
                self.assertFalse(
                    grade_turn(case, ordinary, turn_index=0)["false_saju_injection"]
                )

    def test_schema_omission_accepts_parallel_and_natural_day_master_forms(
        self,
    ) -> None:
        stem_branch_case = {
            **_case(axis="schema_literacy", regression=False),
            "messages": [
                {"role": "system", "content": "승인 사실을 사용하세요."},
                {
                    "role": "user",
                    "content": (
                        "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘."
                    ),
                },
            ],
            "followup_turns": [],
        }
        parallel = (
            "연주는 천간 戊(토·양), 지지 辰(토·양)입니다.\n"
            "월주는 천간 甲(목·양), 지지 子(수·양), 일주는 천간 乙(목·음), 지지 丑(토·음)입니다.\n"
            "시주는 천간 壬(수·양), 지지 午(화·양)입니다."
        )
        self.assertEqual(required_fact_omissions(stem_branch_case, parallel, 0), [])

        ten_god_case = deepcopy(stem_branch_case)
        ten_god_case["messages"][-1]["content"] = (
            "각 기둥의 stem ten-god와 branch ten-god를 위치별로 구분해줘."
        )
        ten_gods = (
            "연주·월주·일주·시주의 stem ten-god는 각각 정재·겁재·일간·정인입니다.\n"
            "같은 순서의 branch ten-god는 각각 편재·편인·편재·식신입니다.\n"
            "천간과 지지의 십신을 서로 섞지 않았습니다."
        )
        grade = grade_turn(ten_god_case, ten_gods, turn_index=0)
        self.assertEqual(grade["structural_claim_errors"], [])
        self.assertEqual(grade["provided_fact_omissions"], [])

        day_master_case = deepcopy(stem_branch_case)
        day_master_case["messages"][-1]["content"] = (
            "이 원국의 일간과 그 오행·음양을 근거와 함께 풀어줘."
        )
        natural = (
            "이 원국의 일간은 乙입니다.\n"
            "쉽게 말해 乙木, 곧 음의 목으로 기록되어 있습니다.\n"
            "이는 JSON의 일간 오행과 음양을 그대로 읽은 값입니다."
        )
        self.assertEqual(required_fact_omissions(day_master_case, natural, 0), [])

    def test_followup_consistency_requires_correct_prior_turn(self) -> None:
        wrong_prior = (
            "원국 전체는 乙丑입니다.\n오늘 일진은 丙午입니다.\n"
            "두 원국을 나란히 보면 됩니다."
        )
        corrected_followup = (
            "앞 설명을 바로잡으면 원국의 일주가 乙丑입니다.\n"
            "선택한 날짜의 일진은 己卯입니다.\n"
            "원국 전체와 일주, 날짜 일진을 구분해서 보아야 합니다."
        )
        grade = grade_turn(
            _case(), corrected_followup, turn_index=1, prior_outputs=[wrong_prior]
        )
        self.assertFalse(grade["followup_evidence_consistent"])

    def test_resume_item_recomputes_grade_and_generation_contract(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        case = _case()
        arm = {
            "arm_id": "K0",
            "artifact_sha256": "a" * 64,
        }
        item = _regression_row("K0")
        generation = {**config["generation"], **config["repetition"]}
        self.assertEqual(
            _validate_case_item(
                item,
                case=case,
                arm=arm,
                eval_id="eval-test",
                generation=generation,
            ),
            item,
        )
        corrupted = deepcopy(item)
        corrupted["turns"][0]["grade"]["deterministic_turn_pass"] = False
        with self.assertRaises(Mix2KV4EvaluationError):
            _validate_case_item(
                corrupted,
                case=case,
                arm=arm,
                eval_id="eval-test",
                generation=generation,
            )
        payload = effective_generation_payload(config["generation"])
        self.assertNotIn("max_input_tokens", payload)
        self.assertEqual(payload["eos_token_id"], [128010])

    def test_ten_metrics_and_dual_review_release_blocker_are_executed(self) -> None:
        config = validate_config(DEFAULT_CONFIG)
        rows_by_arm = {
            arm_id: [_regression_row(arm_id)]
            for arm_id in ("K0", "LORA_R8", "LORA_R16", "LORA_R32", "KI20")
        }
        score = {
            "natural_explanation": 5,
            "task_fulfillment": 5,
            "followup_quality": 5,
            "general_conversation_retention": None,
            "preference_rank": 1,
        }
        reviews = {
            provider: [
                {
                    "provider": provider,
                    "case_id": REGRESSION_ID,
                    "scores": {
                        arm_id: {**score, "preference_rank": index + 1}
                        for index, arm_id in enumerate(rows_by_arm)
                    },
                }
            ]
            for provider in ("claude", "codex")
        }
        aggregate = build_aggregate(
            eval_id="eval-test",
            identity={"effective_generation_sha256": "b" * 64},
            rows_by_arm=rows_by_arm,
            reviews_by_provider=reviews,
            config=config,
        )
        self.assertEqual(
            list(aggregate["arms"]["LORA_R16"]["metrics"]), config["metrics"]
        )
        self.assertFalse(aggregate["release"]["primary_candidate_gate_passed"])
        self.assertFalse(aggregate["release"]["all_metric_thresholds_pass"])

        fully_qualified = deepcopy(aggregate["arms"]["LORA_R16"])
        fully_qualified["deterministic_turn_pass_rate"]["rate"] = 1.0
        rates = {
            "schema_field_accuracy": 1.0,
            "natal_period_label_confusion": 0.0,
            "unsupported_fact_rate": 0.0,
            "provided_fact_omission_rate": 0.0,
            "followup_evidence_consistency": 1.0,
            "repetitive_template_response_rate": 0.0,
            "false_saju_injection": 0.0,
            "reask_rate": 0.0,
        }
        for metric, rate in rates.items():
            fully_qualified["metrics"][metric]["rate"] = rate
        fully_qualified["metrics"]["natural_explanation_preference"]["mean_score"] = 5.0
        fully_qualified["metrics"]["followup_evidence_consistency"][
            "review_mean_score"
        ] = 5.0
        fully_qualified["metrics"]["general_conversation_retention"].update(
            {"rate": 1.0, "review_mean_score": 5.0}
        )
        with patch(
            "scripts.evaluation.mix2k_v4.reporting.aggregate_arm",
            return_value=fully_qualified,
        ):
            qualified = build_aggregate(
                eval_id="eval-test",
                identity={"effective_generation_sha256": "b" * 64},
                rows_by_arm=rows_by_arm,
                reviews_by_provider=reviews,
                config=config,
            )
        self.assertTrue(qualified["release"]["primary_candidate_gate_passed"])
        self.assertTrue(qualified["release"]["all_metric_thresholds_pass"])
        self.assertTrue(qualified["release"]["k0_noninferiority_pass"])
        self.assertFalse(qualified["release"]["serving_contract_passed"])
        self.assertFalse(qualified["release"]["production_release_ready"])
        self.assertEqual(qualified["status"], "SERVING_INTEGRATION_BLOCKED")

        low_fulfillment = deepcopy(fully_qualified)
        low_fulfillment["supplemental_quality_review"].update(
            {
                "task_fulfillment_mean_score": 3.4,
                "task_fulfillment_minimum_score": 1,
            }
        )
        with patch(
            "scripts.evaluation.mix2k_v4.reporting.aggregate_arm",
            return_value=low_fulfillment,
        ):
            task_blocked = build_aggregate(
                eval_id="eval-test",
                identity={"effective_generation_sha256": "b" * 64},
                rows_by_arm=rows_by_arm,
                reviews_by_provider=reviews,
                config=config,
            )
        self.assertFalse(task_blocked["release"]["all_metric_thresholds_pass"])
        self.assertFalse(
            task_blocked["release"]["metric_threshold_results"][
                "task_fulfillment_minimum_score"
            ]["passed"]
        )

        blocked_rows = deepcopy(rows_by_arm)
        blocked_rows["LORA_R16"][0]["turns"][0]["grade"]["regression_turn_pass"] = False
        blocked = build_aggregate(
            eval_id="eval-test",
            identity={"effective_generation_sha256": "b" * 64},
            rows_by_arm=blocked_rows,
            reviews_by_provider=reviews,
            config=config,
        )
        self.assertEqual(blocked["status"], "RELEASE_BLOCKED")

    def test_cross_case_template_multiplicity_is_counted(self) -> None:
        rows = []
        for index in range(3):
            row = deepcopy(_regression_row("K0"))
            row["case_id"] = f"case-{index}"
            row["turns"] = [deepcopy(row["turns"][0])]
            rows.append(row)
        metrics = aggregate_arm(
            rows,
            reviews={},
            arm_id="K0",
            repetition=validate_config(DEFAULT_CONFIG)["repetition"],
        )["metrics"]
        self.assertEqual(metrics["repetitive_template_response_rate"]["numerator"], 3)

    def test_identical_initial_and_followup_are_counted_as_repetition(self) -> None:
        row = _regression_row("KI20")
        row["turns"][1]["output"] = row["turns"][0]["output"]
        metrics = aggregate_arm(
            [row],
            reviews={},
            arm_id="KI20",
            repetition=validate_config(DEFAULT_CONFIG)["repetition"],
        )["metrics"]
        self.assertEqual(metrics["repetitive_template_response_rate"]["numerator"], 2)

    def test_external_review_preflight_blocks_pii_and_binds_raw_outputs(self) -> None:
        case = {
            **_case(),
            "forbidden_claims": [],
            "provenance": "public_synthetic_runtime_v1.5",
        }
        rows_by_arm = {
            arm_id: [_regression_row(arm_id)]
            for arm_id in ("K0", "LORA_R8", "LORA_R16", "LORA_R32", "KI20")
        }
        preflight = _external_review_preflight(cases=[case], rows_by_arm=rows_by_arm)
        self.assertTrue(preflight["passed"])
        self.assertTrue(preflight["heuristic_scan_cannot_exclude_memorization"])
        before = _review_input_set_sha256(
            eval_id="eval-test", cases=[case], rows_by_arm=rows_by_arm
        )
        changed = deepcopy(rows_by_arm)
        changed["KI20"][0]["turns"][0]["output"] += "\n연락처는 010-1234-5678입니다."
        after = _review_input_set_sha256(
            eval_id="eval-test", cases=[case], rows_by_arm=changed
        )
        self.assertNotEqual(before, after)
        with self.assertRaises(Mix2KV4EvaluationError):
            _external_review_preflight(cases=[case], rows_by_arm=changed)
        for leaked in (
            "계좌번호는 123-456-789012입니다.",
            "주소는 서울특별시 종로구 세종대로 175입니다.",
            "로컬 파일은 /home/user/private.txt입니다.",
        ):
            with self.subTest(leaked=leaked):
                unsafe = deepcopy(rows_by_arm)
                unsafe["KI20"][0]["turns"][0]["output"] += "\n" + leaked
                with self.assertRaises(Mix2KV4EvaluationError):
                    _external_review_preflight(cases=[case], rows_by_arm=unsafe)

        with self.assertRaisesRegex(
            Mix2KV4EvaluationError, "approve-external-review-transmission"
        ):
            run_quality_review(
                provider="codex",
                eval_id="eval-test",
                identity={},
                cases=[case],
                rows_by_arm=rows_by_arm,
                target_root=Path("/tmp/not-used-without-approval"),
                config=validate_config(DEFAULT_CONFIG),
                execute=True,
                external_transmission_approved=False,
            )

    def test_blind_review_rejects_boolean_scores_and_non_permutation_rank(
        self,
    ) -> None:
        review_id = "review-test"
        payloads = [
            {
                "review_id": review_id,
                "axis": "natal_and_today",
                "followup_turns": ["쉽게 설명해줘"],
            }
        ]
        mappings = {
            review_id: {
                f"candidate_{letter}": arm_id
                for letter, arm_id in zip(
                    "abcde",
                    ("K0", "LORA_R8", "LORA_R16", "LORA_R32", "KI20"),
                    strict=True,
                )
            }
        }

        def structured() -> dict[str, object]:
            return {
                "reviews": [
                    {
                        "review_id": review_id,
                        "scores": [
                            {
                                "candidate_id": f"candidate_{letter}",
                                "natural_explanation": 5,
                                "task_fulfillment": 5,
                                "followup_quality": 5,
                                "general_conversation_retention": None,
                                "preference_rank": index,
                            }
                            for index, letter in enumerate("abcde", 1)
                        ],
                    }
                ]
            }

        boolean_score = structured()
        boolean_score["reviews"][0]["scores"][0]["natural_explanation"] = True
        with self.assertRaises(Mix2KV4EvaluationError):
            _validate_provider_output(
                boolean_score,
                payloads=payloads,
                mappings=mappings,
            )

        duplicate_rank = structured()
        duplicate_rank["reviews"][0]["scores"][1]["preference_rank"] = 1
        with self.assertRaises(Mix2KV4EvaluationError):
            _validate_provider_output(
                duplicate_rank,
                payloads=payloads,
                mappings=mappings,
            )

    def test_repeated_ngram_ratio_marks_template_loop(self) -> None:
        normal = (
            "오늘 한 가지를 정하고 천천히 실행해 보세요. 결과는 저녁에 돌아보면 됩니다."
        )
        loop = "하나 둘 셋 넷 다섯 여섯 " * 4
        self.assertEqual(repeated_ngram_ratio(normal), 0.0)
        self.assertGreater(repeated_ngram_ratio(loop), 0.35)


if __name__ == "__main__":
    unittest.main()
