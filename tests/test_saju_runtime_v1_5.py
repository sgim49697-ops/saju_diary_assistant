# test_saju_runtime_v1_5.py - v1.5 단일 일진 범위·원국 결합·release를 검증한다.

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.evaluation.saju_runtime.conformance_v10 import verify_report
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_5 import (
    RELEASE_V15_PATH,
    REPORT_V18_ROOT,
    SINGLE_DAY_CASES,
    validate_contract_registry_v1_5,
    validate_release_registry_v1_5,
)
from scripts.runtime.calculation.engine_v1_5 import (
    ApprovedSajuRuntimeEngineV15,
    effective_single_day_start,
    execute_approved_runtime_tool_v1_5,
)

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


def _chart_arguments(*, precision: str = "exact") -> dict[str, object]:
    return {
        "birth_date": "1990-01-01",
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "12:00" if precision == "exact" else None,
        "time_precision": precision,
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


class RuntimeV15ContractTests(unittest.TestCase):
    def test_contract_release_and_public_report_are_self_consistent(self) -> None:
        registry = validate_contract_registry_v1_5()
        release = validate_release_registry_v1_5(RELEASE_V15_PATH)
        verified = verify_report(REPORT_V18_ROOT / release["conformance_report"]["build_id"])
        self.assertEqual(registry["schema_version"], "1.5.0")
        self.assertEqual(release["single_day_dates"], SINGLE_DAY_CASES)
        self.assertEqual(release["single_day_label_mismatches"], 0)
        self.assertEqual(release["noon_boundary_quarantine_dates"], 0)
        self.assertTrue(verified["chart_and_single_day_gate_passed"])
        self.assertFalse(release["runtime_feature_flag_default"])
        self.assertFalse(release["full_runtime_gate_passed"])
        self.assertFalse(release["training_promotion_allowed"])
        self.assertFalse(release["sealed_blind_accessed"])

    def test_server_kst_today_is_a_dynamic_floor(self) -> None:
        self.assertEqual(effective_single_day_start(date(2026, 9, 1)), date(2026, 9, 2))
        self.assertEqual(effective_single_day_start(date(2031, 5, 6)), date(2031, 5, 6))

    def test_disabled_runtime_opens_no_ephemeris_or_key(self) -> None:
        engine = ApprovedSajuRuntimeEngineV15(enable_approved_runtime=False)
        try:
            result = engine.calculate_period({})
        finally:
            engine.close()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["code"], "RUNTIME_RELEASE_REQUIRED")


@unittest.skipUnless(EPHEMERIS.is_file(), "private DE440s fixture가 없습니다.")
class RuntimeV15IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.key_path = Path(cls.temporary.name) / "runtime-hmac.key"
        cls.key_path.write_bytes(bytes(range(32)))
        os.chmod(cls.key_path, 0o600)
        cls.engine = ApprovedSajuRuntimeEngineV15(
            release_registry=RELEASE_V15_PATH,
            enable_approved_runtime=True,
            ephemeris_path=EPHEMERIS,
            id_key_file=cls.key_path,
            today_provider=lambda: date(2026, 9, 2),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.close()
        cls.temporary.cleanup()

    def test_exact_chart_can_request_today_and_selected_single_day(self) -> None:
        chart = self.engine.calculate_chart(_chart_arguments())
        self.assertEqual(chart["status"], "ok")
        chart_id = chart["chart_id"]
        self.assertIsInstance(chart_id, str)
        for target in ("2026-09-02", "2049-12-31"):
            internal, visible = execute_approved_runtime_tool_v1_5(
                self.engine,
                "calculate_saju_period",
                {
                    "chart_id": chart_id,
                    "period_type": "day",
                    "start_date": target,
                    "end_date": target,
                    "timezone": "Asia/Seoul",
                },
            )
            self.assertEqual(internal["status"], "ok")
            self.assertEqual(internal["fact_authority"], "HARD_GT")
            self.assertEqual(
                internal["hard_facts"]["period"]["evaluation_local_time"],
                "12:00",
            )
            self.assertFalse(
                internal["hard_facts"]["day_assignment_evidence"][
                    "future_physical_instant_claimed"
                ]
            )
            self.assertNotIn("chart_id", visible)
            self.assertNotIn("calculation_run_id", visible)

    def test_period_rejects_unbound_chart_type_range_and_past_date(self) -> None:
        chart = self.engine.calculate_chart(_chart_arguments())
        chart_id = chart["chart_id"]
        base = {
            "chart_id": chart_id,
            "period_type": "day",
            "start_date": "2026-09-02",
            "end_date": "2026-09-02",
            "timezone": "Asia/Seoul",
        }
        cases = [
            ({**base, "chart_id": "sc2_" + "f" * 64}, "EXACT_CHART_NOT_IN_PROCESS"),
            ({**base, "period_type": "week"}, "SINGLE_DAY_PERIOD_TYPE_REQUIRED"),
            ({**base, "end_date": "2026-09-03"}, "SINGLE_DAY_RANGE_REQUIRED"),
            (
                {**base, "start_date": "2026-09-01", "end_date": "2026-09-01"},
                "SINGLE_DAY_OUT_OF_APPROVED_RANGE",
            ),
        ]
        for arguments, code in cases:
            with self.subTest(code=code):
                result = self.engine.calculate_period(arguments)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["code"], code)
