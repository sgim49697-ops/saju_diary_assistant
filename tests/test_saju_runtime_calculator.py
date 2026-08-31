# test_saju_runtime_calculator.py - 한국 단일 profile 계산·불확실성·Gate 경계를 검증한다.

from __future__ import annotations

import json
import unittest
from datetime import datetime

from scripts.runtime.calculation.bridge import execute_runtime_tool
from scripts.runtime.calculation.canonical import canonical_json_bytes, stable_id
from scripts.runtime.calculation.contracts import (
    CONFIG_ROOT,
    EXPECTED_PARENT_NAMES,
    EXPECTED_REGISTRY_ARTIFACTS,
    load_json_object,
    runtime_source_versions,
    validate_contract_registry,
)
from scripts.runtime.calculation.engine import SajuRuntimeEngine
from scripts.runtime.calculation.normalize import normalize_colloquial_time_hint
from scripts.runtime.calculation.timezone_resolver import resolve_local_datetime


def chart_arguments(
    *,
    birth_date: str = "1989-01-05",
    birth_time: str | None = "13:00",
    precision: str = "exact",
    time_range: dict[str, str] | None = None,
    country_code: str = "KR",
    timezone: str = "Asia/Seoul",
) -> dict:
    return {
        "birth_date": birth_date,
        "calendar": "solar",
        "leap_month": None,
        "birth_time": birth_time,
        "time_precision": precision,
        "time_range": time_range,
        "birthplace": {
            "country_code": country_code,
            "city": "서울",
            "timezone": timezone,
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "male",
    }


class RuntimeContractTests(unittest.TestCase):
    def test_registry_and_pinned_environment(self) -> None:
        registry = validate_contract_registry()
        self.assertEqual(registry["status"], "candidate_runtime_gate_blocked")
        self.assertEqual(
            {item["path"] for item in registry["artifacts"]},
            EXPECTED_REGISTRY_ARTIFACTS,
        )
        contract = load_json_object(CONFIG_ROOT / "runtime_contract-v1.0.0.json")
        self.assertEqual(set(contract["parents"]), EXPECTED_PARENT_NAMES)
        versions = runtime_source_versions(require_dependencies=True)
        self.assertEqual(versions["tzdb"], "2026c")
        self.assertEqual(versions["python_tzdata"], "2026.3")
        self.assertEqual(versions["korean_lunar_calendar"], "0.4.0")
        self.assertEqual(versions["astronomy_engine"], "2.1.19")

    def test_output_matches_versioned_schema_field_set(self) -> None:
        schema = load_json_object(CONFIG_ROOT / "calculation_output_schema-v1.0.0.json")
        output = SajuRuntimeEngine().calculate_chart(chart_arguments())
        self.assertEqual(set(output), set(schema["required"]))
        self.assertEqual(set(output), set(schema["properties"]))

    def test_normalized_input_matches_versioned_schema_field_set(self) -> None:
        schema = load_json_object(CONFIG_ROOT / "birth_input_schema-v1.0.0.json")
        output = SajuRuntimeEngine(enable_candidate_runtime=True).calculate_chart(
            chart_arguments()
        )
        normalized = output["normalized_input"]
        self.assertEqual(set(normalized), set(schema["required"]))
        self.assertEqual(set(normalized), set(schema["properties"]))

    def test_default_engine_is_blocked_but_foreign_is_explicitly_unsupported(
        self,
    ) -> None:
        engine = SajuRuntimeEngine()
        blocked = engine.calculate_chart(chart_arguments())
        self.assertEqual(blocked["code"], "RUNTIME_GATE_PENDING")
        foreign = engine.calculate_chart(
            chart_arguments(country_code="US", timezone="America/New_York")
        )
        self.assertEqual(foreign["code"], "UNSUPPORTED_REGION")

    def test_am_pm_hints_use_existing_range_enum(self) -> None:
        morning = normalize_colloquial_time_hint(chart_arguments(), "오전")
        afternoon = normalize_colloquial_time_hint(chart_arguments(), "pm")
        self.assertEqual(morning["time_precision"], "range")
        self.assertEqual(morning["time_range"], {"start": "00:00", "end": "11:59"})
        self.assertEqual(afternoon["time_range"], {"start": "12:00", "end": "23:59"})

    def test_canonical_ids_normalize_unicode_and_change_with_versions(self) -> None:
        self.assertEqual(stable_id("sc1_", "가"), stable_id("sc1_", "가"))
        first = {"input": "same", "versions": {"tzdb": "2026c"}}
        second = {"input": "same", "versions": {"tzdb": "next"}}
        self.assertNotEqual(stable_id("sc1_", first), stable_id("sc1_", second))
        self.assertEqual(
            canonical_json_bytes(first),
            canonical_json_bytes(json.loads(canonical_json_bytes(first))),
        )


class RuntimeCalculationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = SajuRuntimeEngine(enable_candidate_runtime=True)

    def test_exact_chart_returns_expected_candidate_facts(self) -> None:
        result = self.engine.calculate_chart(chart_arguments())
        pillars = result["hard_facts"]["pillars"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["fact_authority"], "HARD_CANDIDATE")
        self.assertEqual(
            {key: pillars[key]["ganzhi"] for key in ("year", "month", "day", "hour")},
            {"year": "戊辰", "month": "甲子", "day": "乙丑", "hour": "癸未"},
        )
        self.assertEqual(pillars["month"]["branch_ten_god"], "편인")
        self.assertIsNotNone(result["chart_id"])
        self.assertIsNone(result["chart_set_id"])

    def test_unknown_time_never_confirms_hour_and_preserves_candidates(self) -> None:
        result = self.engine.calculate_chart(
            chart_arguments(birth_time=None, precision="unknown")
        )
        self.assertIsNone(result["hard_facts"]["pillars"]["hour"])
        self.assertFalse(result["uncertainty"]["hour_pillar_confirmed"])
        self.assertGreaterEqual(result["uncertainty"]["candidate_count"], 12)
        self.assertIsNone(result["chart_id"])
        self.assertIsNotNone(result["chart_set_id"])

    def test_candidate_chart_ids_include_normalized_birth_input(self) -> None:
        first_arguments = chart_arguments(birth_time=None, precision="unknown")
        second_arguments = chart_arguments(birth_time=None, precision="unknown")
        second_arguments["birthplace"]["city"] = "서울특별시"
        first = self.engine.calculate_chart(first_arguments)
        second = self.engine.calculate_chart(second_arguments)
        self.assertNotEqual(
            [item["chart_id"] for item in first["alternative_charts"]],
            [item["chart_id"] for item in second["alternative_charts"]],
        )

    def test_dst_gap_is_blocked_and_fold_is_not_auto_selected(self) -> None:
        gap = self.engine.calculate_chart(
            chart_arguments(birth_date="1987-05-10", birth_time="02:30")
        )
        self.assertEqual(gap["code"], "NONEXISTENT_LOCAL_TIME")
        fold = self.engine.calculate_chart(
            chart_arguments(birth_date="1987-10-11", birth_time="02:30")
        )
        self.assertIsNone(fold["chart_id"])
        self.assertIsNotNone(fold["chart_set_id"])
        self.assertIn(
            "AMBIGUOUS_LOCAL_TIME", {item["code"] for item in fold["warnings"]}
        )

    def test_timezone_resolver_reports_gap_and_fold(self) -> None:
        self.assertEqual(
            resolve_local_datetime(datetime(1987, 5, 10, 2, 30))[  # noqa: DTZ001
                "status"
            ],
            "nonexistent",
        )
        self.assertEqual(
            resolve_local_datetime(datetime(1987, 10, 11, 2, 30))[  # noqa: DTZ001
                "status"
            ],
            "ambiguous",
        )

    def test_model_projection_hides_internal_trace_and_ids(self) -> None:
        internal, visible = execute_runtime_tool(
            self.engine, "calculate_saju_chart", chart_arguments()
        )
        self.assertIn("chart_id", internal)
        self.assertNotIn("chart_id", visible)
        self.assertNotIn("internal_trace", visible)
        self.assertEqual(
            set(visible),
            {
                "status",
                "hard_facts",
                "fact_authority",
                "code",
                "message",
                "limitations",
            },
        )

    def test_period_requires_in_process_chart_and_excludes_interpretation(self) -> None:
        chart = self.engine.calculate_chart(chart_arguments())
        result = self.engine.calculate_period(
            {
                "chart_id": chart["chart_id"],
                "period_type": "week",
                "start_date": "2026-08-31",
                "end_date": "2026-09-06",
                "timezone": "Asia/Seoul",
            }
        )
        self.assertEqual(result["status"], "partial")
        period = result["hard_facts"]["period"]
        self.assertEqual(period["start_date"], "2026-08-31")
        self.assertEqual(period["end_date"], "2026-09-06")
        serialized = json.dumps(result["hard_facts"], ensure_ascii=False)
        for field in ("yongsin", "geukguk", "automatic_interpretation"):
            self.assertNotIn(field, serialized)

    def test_period_outside_supported_year_fails_closed(self) -> None:
        chart = self.engine.calculate_chart(chart_arguments())
        result = self.engine.calculate_period(
            {
                "chart_id": chart["chart_id"],
                "period_type": "day",
                "start_date": "2050-01-01",
                "end_date": None,
                "timezone": "Asia/Seoul",
            }
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["code"], "UNSUPPORTED_YEAR")


if __name__ == "__main__":
    unittest.main()
