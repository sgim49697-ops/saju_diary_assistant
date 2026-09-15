# test_dashboard_grounding_v3.py - 실제 기간 schema·날짜 경계·역할별 한국어 사실 검사를 검증한다.

import hashlib
import json
import unittest
from datetime import date

from scripts.training.dashboard_grounding_v3 import (
    DATE_AMBIGUOUS,
    DATE_REBIND,
    SCOPE_UNSUPPORTED,
    audit_output,
    date_scope,
    prompt_intent,
)


def binding_fixture():
    value = {
        "chart": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "pillars": {
                    key: {"ganzhi": value}
                    for key, value in zip(
                        ("year", "month", "day", "hour"),
                        ("己巳", "丙子", "丙寅", "甲午"),
                    )
                },
                "day_master": {"stem": "丙", "element": "화", "yin_yang": "양"},
            },
            "message": "원국 계산 완료",
            "limitations": [],
        },
        "period": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "period": {
                    "period_type": "day",
                    "target_date": "2026-09-05",
                    "timezone": "Asia/Seoul",
                    "evaluation_local_time": "12:00",
                    "year_ganzhi": "丙午",
                    "month_ganzhi": "丙申",
                    "day_ganzhi": "壬午",
                },
                "day_assignment_evidence": {},
            },
            "message": "단일 일진 계산 완료",
            "limitations": [],
        },
    }
    return {
        "schema_version": "1.1.0",
        "binding_id": "saju-chart-day-dashboard-binding-v1.1.0",
        "capability_sha256": "e" * 64,
        "state_revision": 7,
        "value": value,
        "snapshot_sha256": hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }


class GroundingV3Tests(unittest.TestCase):
    def test_opt_out_is_topic_local_and_ordinary_chat_ignores_stale_day(self):
        prompts = (
            "오늘 힘들었어",
            "오늘은 사주 얘기를 쉬고 싶어",
            "오늘은 운세를 안 볼래",
            "오늘 사주 말고 내 하소연을 들어줘",
            "내일 팀장님께 보낼 메시지 써줘",
            "운세 얘기는 하지 말고 내일 보낼 메시지만 다듬어줘",
            "사주와 운세는 빼고 그냥 얘기 좀 하자",
            "오늘 기분이 꿀꿀해",
            "내일 날씨 알려줄래?",
            "내일은 쉬고 싶어. 사주 얘기는 잠깐 쉬고 싶어",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(prompt_intent(prompt), "general_followup")
                scope = date_scope(prompt, binding_fixture(), today=date(2026, 9, 6))
                self.assertTrue(scope["allowed"])
                self.assertEqual(scope["requested_dates"], [])
                audited = audit_output(
                    prompt, "그랬구나. 잠깐 쉬어도 괜찮아.", binding_fixture()
                )
                self.assertEqual(audited["intent"], "general_followup")
                self.assertNotIn("period_day_fact_missing", audited["reasons"])
                self.assertEqual(
                    audited["intent_policy_version"], scope["intent_policy_version"]
                )

    def test_active_forecast_and_ambiguous_followup_cannot_bypass_guard(self):
        for prompt in (
            "사주 말고 내일 운세 봐줘",
            "그냥 내일 운세를 메시지로 써줘",
            "오늘 힘들었어. 내일 운세 봐줄래?",
            "내일 운세를 쉬운 말로 설명해줘",
            "사주 얘기는 쉬고 싶어. 내일 일진은 알려줘",
            "그럼 내일은 어때?",
            "내일은?",
            "사주 말고 내일은 어때?",
            "내일 사주 흐름 봐줘",
            "운세 말고 내일 일진",
            "그냥 얘기해줘. 내일 운세는?",
            "오늘 힘들었어. 그럼 내일은 어때?",
            "그냥 얘기해줘. 내일은?",
            "그럼 내일이 어때?",
            "운세 말고 사주 말고, 내일 결과 확인해줘.",
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(prompt_intent(prompt), "period_request")
                self.assertFalse(
                    date_scope(prompt, binding_fixture(), today=date(2026, 9, 5))[
                        "allowed"
                    ]
                )
        self.assertEqual(
            prompt_intent("운세 말고 내 일간을 설명해줘"), "chart_interpretation"
        )

    def test_date_extraction_and_claim_syntax_remain_the_parent_rules(self):
        from scripts.training import dashboard_grounding_v2 as previous
        from scripts.training import dashboard_grounding_v3 as current

        for name in ("_DATE", "_RELATIVE", "_RANGE", "_CLAIM", "_REVERSE", "_PAIR"):
            self.assertEqual(
                getattr(previous, name).pattern, getattr(current, name).pattern
            )
        for prompt in ("내 일간", "오늘 일진", "내 원국", "일반 대화"):
            for output in (
                "일간은 甲입니다.",
                "일진은 임오입니다.",
                "일주는 병화입니다.",
            ):
                before, after = (
                    previous.audit_output(prompt, output, binding_fixture()),
                    audit_output(prompt, output, binding_fixture()),
                )
                after.pop("intent_policy_version")
                for name in ("gate_id", "scorer_version"):
                    before.pop(name)
                    after.pop(name)
                self.assertEqual(before, after)

    def test_korean_single_stem_cannot_replace_pillar_on_easy_followup(self):
        result = audit_output(
            "쉬운 말로 설명",
            "일간이 병(丙)이고, 일주는 임(壬)입니다.",
            binding_fixture(),
        )
        self.assertIn("natal_day_value_mismatch", result["reasons"])
        self.assertNotIn("day_master_value_mismatch", result["reasons"])
        self.assertTrue(
            audit_output("내 일간", "일간은 병(丙)입니다.", binding_fixture())["passed"]
        )
        self.assertFalse(
            audit_output("내 일간", "일간은 임(壬)입니다.", binding_fixture())["passed"]
        )

    def test_plain_counseling_and_instruction_echo(self):
        self.assertEqual(
            prompt_intent("오늘 회의에서 실수해서 잠이 안 와"), "general_followup"
        )
        self.assertEqual(prompt_intent("내일 운세를 메시지로 써줘"), "period_request")
        result = audit_output(
            "오늘 사주",
            "원국 질문에는 JSON에 실제로 있는 원국 사실을 최소 하나 명시해 답하세요.",
            binding_fixture(),
        )
        self.assertIn("prompt_instruction_echo", result["reasons"])

    def test_intent_boundaries(self):
        for prompt, expected in [
            (
                "내 일간이 병화라고 들었는데 맞아? 연결된 원국 기준으로 확인해줘.",
                "chart_interpretation",
            ),
            ("내일 운세 봐줘", "period_request"),
            ("내일운세", "period_request"),
            ("내 원국 장점과 조심할 점", "chart_interpretation"),
            ("오늘 회의 실수했어. 사주 말고 그냥 얘기 좀 들어줘", "general_followup"),
            ("내일 팀장님한테 보낼 메시지를 두 문장으로 써줄래?", "general_followup"),
            (
                "한자는 어려워. 오늘 회사에서 어떻게 행동하면 좋을지 쉬운 말로 세 가지만 말해줘.",
                "general_followup",
            ),
            (
                "그럼 내일은 오늘보다 나아? 이번 주 전체 흐름도 알려줘.",
                "period_request",
            ),
        ]:
            with self.subTest(prompt=prompt):
                self.assertEqual(prompt_intent(prompt), expected)

    def test_dates_require_explicit_rebind_or_scope_guidance(self):
        cases = [
            ("오늘 사주", None),
            ("2026-09-05 일진", None),
            ("2026년 9월 5일 운세", None),
            ("내일 운세", DATE_REBIND),
            ("모레 일진", DATE_REBIND),
            ("어제 운세", DATE_REBIND),
            ("이번 주 흐름", SCOPE_UNSUPPORTED),
            ("이번 달 운세", SCOPE_UNSUPPORTED),
            ("올해 운세", SCOPE_UNSUPPORTED),
            ("9월 5일 운세", DATE_AMBIGUOUS),
            ("2026-02-30 일진", DATE_AMBIGUOUS),
            ("운세 좀", DATE_AMBIGUOUS),
            ("내 일간이 병화야?", None),
            ("내일 보낼 메시지 써줘", None),
        ]
        for prompt, code in cases:
            with self.subTest(prompt=prompt):
                scope = date_scope(prompt, binding_fixture(), today=date(2026, 9, 5))
                self.assertEqual(scope["reason_code"], code)
                self.assertEqual(scope["allowed"], code is None)

    def test_server_clock_not_snapshot_controls_relative_day(self):
        scope = date_scope("오늘 운세", binding_fixture(), today=date(2026, 9, 6))
        self.assertEqual(scope["reason_code"], DATE_REBIND)
        self.assertEqual(scope["requested_dates"], ["2026-09-06"])
        self.assertTrue(date_scope("오늘 사주", None)["allowed"])

    def test_actual_period_schema_detects_year_as_day_and_wrong_date(self):
        result = audit_output(
            "오늘 사주",
            "원국 일주 丙寅, 오늘 일진 丙午. 날짜는 2024-02-05입니다.",
            binding_fixture(),
        )
        self.assertIn("period_day_value_mismatch", result["reasons"])
        self.assertIn("period_target_date_mismatch", result["reasons"])
        self.assertFalse(result["passed"])

    def test_korean_and_hanja_label_claims(self):
        for output in ("일간은 병화입니다.", "일간 丙입니다.", "병화가 일간입니다."):
            self.assertTrue(
                audit_output("내 일간 확인해줘", output, binding_fixture())["passed"],
                output,
            )
        for output in (
            "일진은 임오, 연간지는 병오, 월간지는 병신입니다.",
            "일진 壬午, 연간지 丙午, 월간지 丙申입니다.",
        ):
            self.assertTrue(
                audit_output("오늘 운세", output, binding_fixture())["passed"], output
            )
        self.assertIn(
            "natal_day_value_mismatch",
            audit_output("내 원국", "일주 병오입니다.", binding_fixture())["reasons"],
        )
        self.assertIn(
            "day_master_value_mismatch",
            audit_output("내 일간", "일간은 병인입니다.", binding_fixture())["reasons"],
        )

    def test_correct_negation_and_quoted_correction(self):
        for output in (
            "일진은 병오가 아니라 임오입니다. 일진 임오입니다.",
            "‘일진 병오’라는 말은 잘못됐습니다. 일진 임오입니다.",
            "일간은 갑목이 아닙니다. 일간은 병화입니다.",
        ):
            prompt = "내 일간" if "일간" in output else "오늘 운세"
            result = audit_output(prompt, output, binding_fixture())
            self.assertTrue(result["passed"], result)

    def test_unnegated_quote_is_not_ignored(self):
        result = audit_output(
            "오늘 운세", '일진은 "병오"입니다. 일진 임오입니다.', binding_fixture()
        )
        self.assertFalse(result["passed"])

    def test_only_requested_facts_required(self):
        self.assertTrue(
            audit_output("내 일간 확인", "일간은 병화입니다.", binding_fixture())[
                "passed"
            ]
        )
        self.assertTrue(
            audit_output("내 원국 장점 설명", "일주는 병인입니다.", binding_fixture())[
                "passed"
            ]
        )
        self.assertTrue(
            audit_output(
                "한자는 어려워. 쉬운 말로 설명",
                "잠시 쉬고 할 일을 정리해 보세요.",
                binding_fixture(),
            )["passed"]
        )
        result = audit_output(
            "원국 네 기둥 전부 알려줘", "일주 병인", binding_fixture()
        )
        self.assertIn("natal_pillars_omitted", result["reasons"])
        self.assertTrue(
            audit_output(
                "원국 네 기둥",
                "연주 기사, 월주 병자, 일주 병인, 시주 갑오",
                binding_fixture(),
            )["passed"]
        )

    def test_natal_and_period_roles_cannot_substitute_each_other(self):
        for output, expected in [
            ("원국 월주 병신", "natal_month_value_mismatch"),
            ("연간지 임오, 일진 임오", "period_year_value_mismatch"),
            ("월간지 병오, 일진 임오", "period_month_value_mismatch"),
        ]:
            result = audit_output("오늘 사주", output, binding_fixture())
            self.assertIn(expected, result["reasons"])
