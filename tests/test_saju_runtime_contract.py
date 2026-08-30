# test_saju_runtime_contract.py - session·상대날짜·tool strict 계약의 핵심 경계를 검증한다.

from __future__ import annotations

import copy
import unittest

from scripts.runtime.saju_contract import (
    CALCULATION_POLICY_ID,
    SajuContractError,
    birth_input_fingerprint,
    empty_session_state,
    invalidate_chart_for_correction,
    load_tool_schema,
    project_model_visible_tool_result,
    render_runtime_system_message,
    resolve_relative_period,
    tool_by_name,
    validate_argument_provenance,
    validate_session_state,
    validate_tool_arguments,
)


class SajuRuntimeContractTests(unittest.TestCase):
    def _complete_state(self) -> dict:
        state = empty_session_state(saju_opt_in=True)
        state["birth_slots"] = {
            "birth_date": "1989-01-05",
            "calendar": "solar",
            "leap_month": None,
            "birth_time": None,
            "time_precision": "unknown",
            "time_range": None,
            "birthplace": {
                "country_code": "KR",
                "city": "서울",
                "timezone": "Asia/Seoul",
                "longitude": None,
                "latitude": None,
            },
            "timezone": "Asia/Seoul",
            "gender_for_daeun": "male",
        }
        state["confirmed_fields"] = [
            "birth_date",
            "calendar",
            "birthplace",
            "timezone",
            "gender_for_daeun",
            "time_precision",
        ]
        state["explicit_unknown_fields"] = ["birth_time"]
        state["field_provenance"] = {
            field: "user_explicit" for field in state["confirmed_fields"]
        }
        return state

    def test_unknown_birth_time_is_valid_and_does_not_require_a_time(self) -> None:
        state = self._complete_state()
        validate_session_state(state)
        rendered = render_runtime_system_message(state)
        self.assertIn('"explicit_unknown_fields":["birth_time"]', rendered)
        self.assertIn("<runtime_context>", rendered)

    def test_chart_correction_invalidates_cached_chart(self) -> None:
        state = self._complete_state()
        state["chart"] = {
            "chart_id": "chart-fixture",
            "chart_valid": True,
            "chart_input_fingerprint": birth_input_fingerprint(state),
            "chart_policy_version": CALCULATION_POLICY_ID,
            "hard_facts": {"day_master": {"stem": "乙"}},
        }
        validate_session_state(state)
        updated = invalidate_chart_for_correction(state, {"birth_date": "1989-01-06"})
        self.assertFalse(updated["chart"]["chart_valid"])
        self.assertIsNone(updated["chart"]["chart_id"])
        self.assertEqual(updated["state_revision"], 1)

    def test_relative_date_example_and_calendar_ranges(self) -> None:
        kwargs = {
            "reference_datetime": "2026-08-30T20:04:00+09:00",
            "timezone": "Asia/Seoul",
        }
        self.assertEqual(
            resolve_relative_period("이번 주말", **kwargs),
            {
                "period_type": "week",
                "start_date": "2026-09-05",
                "end_date": "2026-09-06",
                "normalizer_version": "saju-relative-date-policy-v1",
            },
        )
        self.assertEqual(
            resolve_relative_period("다음 달", **kwargs)["end_date"],
            "2026-09-30",
        )
        self.assertEqual(
            resolve_relative_period("내년", **kwargs)["end_date"],
            "2027-12-31",
        )

    def test_relative_date_requires_offset_timezone_and_supported_expression(
        self,
    ) -> None:
        with self.assertRaisesRegex(SajuContractError, "offset"):
            resolve_relative_period(
                "오늘",
                reference_datetime="2026-08-30T20:04:00",
                timezone="Asia/Seoul",
            )
        with self.assertRaisesRegex(SajuContractError, "지원하지"):
            resolve_relative_period(
                "조만간",
                reference_datetime="2026-08-30T20:04:00+09:00",
                timezone="Asia/Seoul",
            )

    def test_tool_schema_is_single_strict_contract(self) -> None:
        chart = tool_by_name("calculate_saju_chart")
        function = chart["function"]
        self.assertTrue(function["strict"])
        self.assertFalse(function["parameters"]["additionalProperties"])
        self.assertEqual(
            set(function["parameters"]["properties"]),
            set(function["parameters"]["required"]),
        )
        contract = load_tool_schema()
        self.assertFalse(
            contract["executor_validation"]["nested_object_contracts"]["birthplace"][
                "additional_properties"
            ]
        )

    def test_tool_result_projection_keeps_evidence_and_hides_internal_ids(self) -> None:
        projected = project_model_visible_tool_result(
            {
                "status": "partial",
                "chart_id": "chart-private",
                "calculation_policy_id": CALCULATION_POLICY_ID,
                "tool_schema_version": "saju-tools-v1",
                "hard_facts": {"day_master": {"stem": "乙"}},
                "fact_authority": "HARD_CANDIDATE",
                "limitations": ["시주 미포함"],
            }
        )
        self.assertEqual(
            set(projected),
            {"status", "hard_facts", "fact_authority", "limitations"},
        )
        self.assertNotIn("chart_id", projected)
        state = empty_session_state(saju_opt_in=True)
        state["last_tool_status"] = "partial"
        validate_session_state(state)

    def test_failed_tool_result_requires_error_evidence(self) -> None:
        with self.assertRaisesRegex(SajuContractError, "code 또는 message"):
            project_model_visible_tool_result({"status": "error"})

    def test_tool_arguments_and_leaf_provenance_are_fail_closed(self) -> None:
        arguments = {
            "birth_date": "1989-01-05",
            "calendar": "solar",
            "leap_month": None,
            "birth_time": None,
            "time_precision": "unknown",
            "time_range": None,
            "birthplace": {
                "country_code": "KR",
                "city": "서울",
                "timezone": "Asia/Seoul",
                "longitude": None,
                "latitude": None,
            },
            "gender_for_daeun": "male",
        }
        validate_tool_arguments("calculate_saju_chart", arguments)
        provenance = {
            "birth_date": "session_confirmed",
            "calendar": "session_confirmed",
            "leap_month": "deterministic_default",
            "birth_time": "user_explicit",
            "time_precision": "user_explicit",
            "time_range": "deterministic_default",
            "birthplace.country_code": "runtime_normalized",
            "birthplace.city": "session_confirmed",
            "birthplace.timezone": "runtime_normalized",
            "birthplace.longitude": "runtime_normalized",
            "birthplace.latitude": "runtime_normalized",
            "gender_for_daeun": "session_confirmed",
        }
        validate_argument_provenance(arguments, provenance)
        broken = copy.deepcopy(provenance)
        broken["birth_time"] = "unsupported"
        with self.assertRaisesRegex(SajuContractError, "허용할 수 없는"):
            validate_argument_provenance(arguments, broken)

    def test_lunar_date_is_not_rejected_by_gregorian_leap_year_rules(self) -> None:
        arguments = {
            "birth_date": "2002-02-29",
            "calendar": "lunar",
            "leap_month": False,
            "birth_time": "16:10",
            "time_precision": "exact",
            "time_range": None,
            "birthplace": {
                "country_code": "KR",
                "city": "대전",
                "timezone": "Asia/Seoul",
                "longitude": None,
                "latitude": None,
            },
            "gender_for_daeun": "male",
        }
        validate_tool_arguments("calculate_saju_chart", arguments)
        arguments["calendar"] = "solar"
        arguments["leap_month"] = None
        with self.assertRaisesRegex(SajuContractError, "유효한 날짜"):
            validate_tool_arguments("calculate_saju_chart", arguments)


if __name__ == "__main__":
    unittest.main()
