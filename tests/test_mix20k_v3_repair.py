# test_mix20k_v3_repair.py - v3 보정의 provenance·상대날짜·언어 경계를 검증한다.

from __future__ import annotations

import unittest

from scripts.data.mix20k_v3_repair import (
    Mix20KV3Error,
    _chart_argument_provenance,
    _filter_hard_facts,
    _model_visible_context,
    _model_visible_result,
    _normalize_chart_arguments,
    _period_context_and_user,
    _validate_public_private_reference,
    repair_hanja_particles,
    repair_row,
)
from scripts.runtime.saju_contract import (
    CALCULATION_POLICY_ID,
    validate_session_state,
)


class Mix20KV3RepairTests(unittest.TestCase):
    def test_hanja_particles_follow_korean_reading(self) -> None:
        fixed, count = repair_hanja_particles("己과 癸이 甲子이 乙로 戊은 비교합니다.")
        self.assertEqual(
            fixed,
            "己와 癸가 甲子가 乙로 戊는 비교합니다.",
        )
        self.assertEqual(count, 4)

    def test_chart_arguments_drop_model_policy_and_add_leaf_provenance(self) -> None:
        normalized = _normalize_chart_arguments(
            {
                "birth_date": "1989-01-05",
                "calendar": "solar",
                "leap_month": None,
                "birth_time": None,
                "time_precision": "unknown",
                "birthplace": "서울",
                "timezone": "Asia/Seoul",
                "gender_for_daeun": "male",
                "policy_version": "kr-saju-v1",
            }
        )
        self.assertNotIn("policy_version", normalized)
        self.assertEqual(normalized["birthplace"]["country_code"], "KR")
        self.assertIsNone(normalized["time_range"])
        provenance = _chart_argument_provenance(normalized)
        self.assertEqual(
            provenance["birthplace.timezone"],
            "runtime_normalized",
        )
        self.assertEqual(provenance["birth_time"], "user_explicit")

    def test_relative_weekend_gets_reproducible_reference_datetime(self) -> None:
        context, user = _period_context_and_user(
            "이번 주말 흐름을 봐줘.",
            {
                "chart_id": "chart-fixture",
                "period_type": "week",
                "start_date": "2026-09-05",
                "end_date": "2026-09-06",
                "timezone": "Asia/Seoul",
            },
        )
        self.assertEqual(user, "이번 주말 흐름을 봐줘.")
        self.assertEqual(
            context["reference_datetime"],
            "2026-09-03T12:00:00+09:00",
        )
        self.assertEqual(context["relative_expression"], "이번 주말")

    def test_cached_fixture_is_demoted_and_answer_is_regrounded(self) -> None:
        row = {
            "schema_version": "1.0.0",
            "id": "mix20k-v3-fixture",
            "conversation_id": "conversation-fixture",
            "task_axis": "tool_result_interpretation",
            "intent_id": "interpret_elements",
            "scenario_id": "cached",
            "template_family": "cached-fixture",
            "source": "synthetic_cached_tool_result",
            "source_refs": [],
            "fact_authority": "HARD_GT",
            "interpretation_authority": "SOFT_CANDIDATE",
            "promotion_status": "domain_review_required",
            "policy_id": "kr-saju-v1",
            "chart_id": "chart-fixture",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "도구 결과(JSON): "
                        '{"status":"ok","chart_id":"chart-fixture",'
                        '"policy_version":"kr-saju-v1","hard_facts":{'
                        '"pillars":{"year":"戊辰","month":"甲子",'
                        '"day":"乙丑","hour":"丁亥"},'
                        '"day_master":{"stem":"乙","element":"목","polarity":"음"},'
                        '"five_elements":{"목":2,"화":1,"토":3,"금":0,"수":2}}}'
                    ),
                },
                {"role": "user", "content": "오행 균형을 알려줘."},
                {"role": "assistant", "content": "기존 후보 답변"},
            ],
            "tools": [],
        }
        repaired = repair_row(
            row,
            line_number=1,
            prompt="한국어 사주 도우미입니다.",
            uncooperative_ids=set(),
            long_ids=set(),
            restricted_sources=set(),
        )
        self.assertEqual(repaired["fact_authority"], "HARD_CANDIDATE")
        self.assertFalse(repaired["train_candidate"])
        self.assertEqual(repaired["policy_id"], CALCULATION_POLICY_ID)
        self.assertIn("목 2", repaired["messages"][-1]["content"])
        self.assertNotIn("ten_gods", repaired["messages"][0]["content"])
        validate_session_state(repaired["session_state"])

    def test_period_hard_facts_survive_model_visible_projection(self) -> None:
        facts = {
            "days": [
                {"date": "2026-09-05", "ganzhi": "壬午"},
                {"date": "2026-09-06", "ganzhi": "癸未"},
            ],
            "unapproved_relation": "drop",
        }
        filtered = _filter_hard_facts(facts, "period_week")
        self.assertEqual(set(filtered), {"days"})
        visible = _model_visible_result(
            {
                "status": "ok",
                "chart_id": "chart-internal",
                "calculation_policy_id": CALCULATION_POLICY_ID,
                "tool_schema_version": "saju-tools-v1",
                "hard_facts": filtered,
                "fact_authority": "HARD_CANDIDATE",
                "limitations": [],
            }
        )
        self.assertEqual(
            set(visible), {"status", "hard_facts", "fact_authority"}
        )
        self.assertIn("壬午", str(visible["hard_facts"]))

    def test_model_context_keeps_normalized_period_without_internal_duplicates(
        self,
    ) -> None:
        visible = _model_visible_context(
            {
                "saju_opt_in": True,
                "chart_id": "chart-fixture",
                "reference_datetime": "2026-08-30T12:00:00+09:00",
                "timezone": "Asia/Seoul",
                "relative_expression": "이번 주말",
                "normalized_period": {
                    "period_type": "week",
                    "start_date": "2026-09-05",
                    "end_date": "2026-09-06",
                    "normalizer_version": "saju-relative-date-policy-v1",
                },
            },
            task_axis="verified_period_handling",
        )
        self.assertEqual(
            set(visible), {"saju_opt_in", "chart_id", "normalized_period"}
        )
        self.assertNotIn("normalizer_version", visible["normalized_period"])

    def test_public_intake_must_reference_the_selected_private_build(self) -> None:
        manifest = {
            "intake_id": "intake-fixture",
            "intake_sha256": "a" * 64,
            "private_build_id": "build-new",
            "private_build_sha256": "b" * 64,
        }
        reference = {
            **manifest,
            "private_manifest_sha256": "c" * 64,
        }
        _validate_public_private_reference(
            manifest,
            reference,
            expected_private_build_id="build-new",
            expected_private_build_sha256="b" * 64,
            expected_private_manifest_sha256="c" * 64,
        )
        reference["private_build_id"] = "build-old"
        with self.assertRaisesRegex(Mix20KV3Error, "reference"):
            _validate_public_private_reference(
                manifest,
                reference,
                expected_private_build_id="build-new",
                expected_private_build_sha256="b" * 64,
                expected_private_manifest_sha256="c" * 64,
            )


if __name__ == "__main__":
    unittest.main()
