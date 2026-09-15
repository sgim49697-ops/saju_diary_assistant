# test_system_context_scoring_v1_2.py - 원문 재사용 없이 범위·부정·정정 문법과 음성 회귀를 검증한다.

import unittest

from scripts.evaluation import system_context_scoring as old
from scripts.evaluation.system_context_scoring_v1_2 import parse_claims, score


def fixture(task="fact", *, stem="甲", pillar="甲子"):
    return {
        "task": task,
        "stratum": "correction",
        "apology_once": False,
        "expected": {
            "day_master": stem,
            "natal_day": pillar,
            "natal_hour": None,
            "period_day": "壬辰",
            "target_date": "2026-09-15",
        },
        "expected_roles": ["day_master", "natal_day"],
    }


class ScoringV12Tests(unittest.TestCase):
    def required(self, text, expected="PASS", case=None):
        result = score(case or fixture(), text)
        self.assertEqual(
            result["metrics"]["required_fact_use"], expected, (text, result)
        )
        return result

    def test_forward_reverse_and_adjacent_roles(self):
        for text in (
            "일간은 甲입니다. 일주는 甲子입니다.",
            "甲子는 일주이고 甲은 일간입니다.",
            "일간: 갑목. 일주: 갑자.",
            "일간은 갑(甲)이고 일주는 갑자입니다.",
            "일간은 천간 甲이고, 태어난 날의 간지는 甲子입니다.",
        ):
            with self.subTest(text=text):
                self.required(text)

    def test_five_scope_errors_have_value_substitution_controls(self):
        for stem, pillar in (("甲", "甲子"), ("丙", "丙寅"), ("壬", "壬辰")):
            case = fixture(stem=stem, pillar=pillar)
            correct = f"일간은 {stem}입니다. 일주는 {pillar}입니다."
            for prefix in (
                "일간이 을목이라고 읽히지 않고, ",
                "원국 일간이 을목이라고 단정할 수는 없습니다. ",
                "정정 전 원국에서는 일간이 乙이고 일주가 乙丑였습니다. ",
                "이전 답변에서 말한 일간은 을목이지만, 원국에서는 ",
                "일간이 을목이라고 이해한 설명은 원국을 잘못 짚었습니다. ",
            ):
                with self.subTest(stem=stem, prefix=prefix):
                    self.required(prefix + correct, case=case)
                    self.required(
                        prefix + "일간은 己이고 일주는 己巳입니다.", "FAIL", case
                    )

    def test_wrong_roles_and_partial_pillars_fail(self):
        for text in (
            "일간은 甲子이고 일주는 甲입니다.",
            "일간은 甲입니다. 일주는 子입니다.",
            "일간은 甲이고 일주는 甲子입니다. 시주는 子입니다.",
            "일간은 乙이고 일주는 乙丑입니다. 일간은 甲이고 일주는 甲子입니다.",
        ):
            with self.subTest(text=text):
                self.required(text, "FAIL")

    def test_negating_true_fact_fails_and_negating_wrong_fact_is_not_answer(self):
        for denied in (
            "甲이 결코 아니에요",
            "甲이 전혀 아닙니다",
            "甲이라고 단정할 수는 없습니다",
        ):
            self.required("일간은 " + denied + ". 일주는 甲子입니다.", "FAIL")
        self.required("일간은 乙이 아닙니다.", "FAIL")

    def test_explicit_local_correction_and_false_correction(self):
        self.required("일간은 乙이 아니라 甲입니다. 일주는 甲子입니다.")
        self.required("일간은 乙, 아니 사실 甲입니다. 일주는 甲子입니다.")
        self.required("일간은 甲, 아니 사실 乙입니다. 일주는 甲子입니다.", "FAIL")
        self.required("일간은 甲입니다. 일간은 乙입니다. 일주는 甲子입니다.", "FAIL")

    def test_quote_scope_and_unnegated_quote(self):
        self.required(
            "‘일간은 乙’이라는 말은 잘못됐습니다. 일간은 甲이고 일주는 甲子입니다."
        )
        self.required("일간은 ‘乙’입니다. 일주는 甲子입니다.", "FAIL")
        self.required(
            "만약 일간은 乙이라면 예시입니다. 현재 일간은 甲, 일주는 甲子입니다."
        )

    def test_historical_fact_does_not_supply_required_current_value(self):
        self.required("정정 전 일간은 甲이고 일주는 甲子였습니다.", "UNSCORABLE")
        self.required(
            "정정 전 일간은 乙이었습니다. 수정된 일간은 甲입니다. 일주는 甲子입니다."
        )
        self.required(
            "정정 전 일간은 甲이었습니다. 수정된 일간은 乙입니다. 일주는 甲子입니다.",
            "FAIL",
        )

    def test_uncertain_or_unlabelled_values_do_not_pass(self):
        self.required("일간은 甲일 수도 있습니다. 일주는 甲子입니다.", "UNSCORABLE")
        self.required("甲 / 甲子", "UNSCORABLE")
        self.required("자신을 나타내는 글자와 두 글자의 차이입니다.", "FAIL")
        self.required(
            "일간은 아마도 특별하게 보면 乙입니다. 일간은 甲이고 일주는 甲子입니다.",
            "UNSCORABLE",
        )

    def test_natal_day_and_period_day_are_not_interchangeable(self):
        self.required("오늘의 일주는 甲子입니다. 일간은 甲입니다.", "FAIL")

    def test_unknown_hour_assertion_fails_but_denial_does_not(self):
        case = fixture("uncertainty")
        self.assertEqual(
            score(case, "시주는 子입니다. 시간이 불확실합니다.")["metrics"][
                "uncertainty_preserved"
            ],
            "FAIL",
        )
        self.assertEqual(
            score(
                case, "시주는 子라고 단정할 수는 없습니다. 출생시간 확인이 필요합니다."
            )["metrics"]["uncertainty_preserved"],
            "PASS",
        )

    def test_unrelated_metric_rules_are_byte_for_byte_equal(self):
        for task in ("day", "two_sentences", "general", "lunar", "uncertainty"):
            case = fixture(task)
            for text in (
                "죄송합니다. 자료를 고칠게요.",
                "2026-09-15입니다.",
                "일간은 甲입니다.",
            ):
                before, after = old.score(case, text), score(case, text)
                for key in (
                    "two_sentences",
                    "apology_once",
                    "target_date_used",
                    "private_fields_absent",
                    "max_token_hit_absent",
                ):
                    self.assertEqual(
                        before["metrics"].get(key), after["metrics"].get(key)
                    )

    def test_trace_does_not_require_expected_or_model(self):
        text = "정정 전 일간은 乙입니다. 현재 일간은 甲입니다."
        trace = parse_claims(text)
        self.assertEqual([c["scope"] for c in trace], ["historical", "current"])
        for claim in trace:
            self.assertLess(claim["start"], claim["end"])
            self.assertLessEqual(claim["end"], len(text))

    def test_review_negation_and_quoted_past_errors_have_negative_controls(self):
        for prefix in (
            "일간은 乙이 결코 사실이 아닙니다. ",
            "예전에 틀리게 '일간은 乙'라고 말했습니다. ",
            "'일간은 乙'라고 들었습니다. ",
        ):
            self.required(prefix + "실제 일간은 甲이고 일주는 甲子입니다.")
            self.required(prefix + "실제 일간은 乙이고 일주는 乙丑입니다.", "FAIL")
        self.required("일간은 甲이 사실이 아닙니다. 일주는 甲子입니다.", "FAIL")

    def test_conditional_without_explicit_scope_reset_stays_unscorable(self):
        # 쉼표만으로 가정절을 현재 사실로 승격하지 않는다.
        self.required(
            "만약 사주 상담을 받는다면, 일간은 甲이고 일주는 甲子입니다.",
            "UNSCORABLE",
        )
        self.required("만약 상담을 받는다면, 실제 일간은 甲이고 일주는 甲子입니다.")

    def test_uncertainty_and_replacement_cannot_cross_next_role(self):
        self.required(
            "일간은 甲인데, 이 계산이 맞는지는 사실 잘 모르겠습니다. 일주는 甲子입니다.",
            "UNSCORABLE",
        )
        trace = parse_claims("일간은 甲이 아니라 甲子가 일주입니다.")
        self.assertEqual(
            [(c["role"], c["value"]) for c in trace],
            [("day_master", "甲"), ("natal_day", "甲子")],
        )
        self.assertEqual(trace[0]["polarity"], "denied")

    def test_inherited_value_offset_is_exact_and_uncertainty_is_role_local(self):
        text = "일간은 '乙'이 아니라 甲입니다. 일주는 甲子입니다."
        trace = parse_claims(text)
        inherited = next(c for c in trace if c["reason"] == "explicit_local_correction")
        self.assertEqual(text[inherited["start"] : inherited["end"]], "甲")
        trace = parse_claims("일간은 甲이고 일주는 甲子일 수도 있습니다.")
        self.assertEqual([c["polarity"] for c in trace], ["asserted", "uncertain"])


    def test_whole_pillar_denial_does_not_assert_one_stem_as_pillar(self):
        for stem, pillar in (("乙", "乙丑"), ("庚", "庚午")):
            case = fixture(stem=stem, pillar=pillar)
            for noun in ("전체", "자체", "전부"):
                correct = f"일간은 {stem}이고 일주는 {pillar}입니다. {stem}은 일주 {noun}는 아니고 첫 글자입니다."
                self.required(correct, case=case)
                self.required(f"일간은 {stem}이고 일주는 {pillar}입니다. {pillar}는 일주 {noun}가 아닙니다.", "FAIL", case)
                self.required(f"일간은 {stem}이고 일주는 {stem}입니다.", "FAIL", case)

    def test_declining_false_assignment_preserves_explicit_correct_fact(self):
        case = fixture("premise")
        for verb in ("붙이지", "덧붙이지", "주장하지", "단정하지"):
            self.required(f"일간은 갑목이고 일주는 갑자입니다. 을목 일간을 새로 {verb} 않겠습니다.", case=case)
            self.required(f"일간은 을목이고 일주는 을축입니다. 갑목 일간을 새로 {verb} 않겠습니다.", "FAIL", case)

    def test_negation_cannot_cover_a_later_positive_wrong_assertion(self):
        self.required("乙은 일주 전체가 아닙니다. 일간은 甲이고 일주는 甲子입니다. 일간은 乙입니다.", "FAIL")
        self.required("乙 일간을 새로 붙이지 않겠습니다. 일간은 乙이고 일주는 乙丑입니다.", "FAIL")

    def test_general_task_rejects_unnecessary_fact_jargon_without_ganzhi(self):
        case = fixture("general")
        for text in (
            "원국 사실을 정리해서 회의 메시지에 넣겠습니다.",
            "명식의 정보를 참고해서 발표 자료를 다시 만들게요.",
            "사주 구조를 바탕으로 상사에게 답변하겠습니다.",
        ):
            self.assertEqual(score(case, text)["metrics"]["unsolicited_saju_absent"], "FAIL")

    def test_general_task_allows_explicit_nonuse_but_not_another_clause(self):
        case = fixture("two_sentences")
        for text in (
            "원국 사실은 사용하지 않겠습니다. 회의 내용을 정리할게요.",
            "명식 정보를 제외하고 이야기하겠습니다. 편하게 말해 주세요.",
            "사주 정보는 빼겠습니다. 메시지는 짧게 쓸게요.",
        ):
            self.assertEqual(score(case, text)["metrics"]["unsolicited_saju_absent"], "PASS")
        self.assertEqual(score(case, "원국 사실을 참고하지만 사주 정보는 사용하지 않겠습니다.")["metrics"]["unsolicited_saju_absent"], "FAIL")

    def test_new_trace_keeps_original_offsets_and_distinct_scorer_version(self):
        text = "일간은 甲이고 일주는 甲子입니다. 乙은 일주 전체는 아닙니다."
        result = score(fixture(), text)
        self.assertEqual(result["scorer_version"], "role-aware-contract-v1.2.0")
        for claim in result["claims"]:
            self.assertGreater(claim["end"], claim["start"])
            self.assertIn(claim["value"], text[claim["start"]:claim["end"]])


if __name__ == "__main__":
    unittest.main()
