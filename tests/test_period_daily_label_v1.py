# test_period_daily_label_v1.py - 일별 기간 engine·conformance·release 경계를 검증한다.

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.evaluation.saju_runtime.conformance_v11 import verify_report
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.chart_day_adapter import empty_session_state
from scripts.runtime.period_v1.contracts_v1_1 import (
    RELEASE_PATH,
    validate_contract_registry_v1_1,
    validate_release_registry,
)
from scripts.runtime.period_v1.engine import (
    calculate_daily_labels_candidate,
    public_daily_label_result,
    validate_public_daily_label_result,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.security import PeriodIdSigner

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
REPORT = (
    REPO_ROOT
    / "data/reports/saju_period_conformance/v1.0.0/build-cd8eaaf50792"
)


def _public_fixture() -> dict[str, object]:
    return {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "period_scope": {
            "date_expression": "today",
            "start_date": "2026-09-02",
            "end_date": "2026-09-02",
            "day_count": 1,
            "timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
        },
        "days": [
            {
                "date": "2026-09-02",
                "year_ganzhi": "병오",
                "month_ganzhi": "병신",
                "day_ganzhi": "기묘",
                "authority": "SOURCE_HARD_FACT",
            }
        ],
        "boundary_capability": {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        },
        "message": "공식 날짜 label 계산 완료",
        "limitations": ["날짜 label만 제공합니다."],
    }


def _chart_arguments() -> dict[str, object]:
    return {
        "birth_date": "1990-01-01",
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "12:00",
        "time_precision": "exact",
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


def _state(chart: dict[str, object]) -> dict[str, object]:
    state = empty_session_state()
    state.update(
        {
            "state_revision": 1,
            "saju_opt_in": True,
            "current_intent": "period",
            "birth_slots": {
                "calendar": "solar",
                "birth_date": "1990-01-01",
                "leap_month": None,
                "time_precision": "exact",
                "birth_time": "12:00",
                "time_range": None,
                "birthplace": {
                    "country_code": "KR",
                    "city": "서울",
                    "timezone": "Asia/Seoul",
                },
            },
            "chart": chart,
        }
    )
    return state


class PeriodDailyLabelContractTests(unittest.TestCase):
    def test_contract_conformance_and_release_are_self_consistent(self) -> None:
        registry = validate_contract_registry_v1_1()
        verified = verify_report(REPORT)
        release = validate_release_registry(RELEASE_PATH)
        self.assertEqual(registry["registry_id"], "saju-period-contract-registry-v1.1.0")
        self.assertEqual(verified["official_dates"], 8522)
        self.assertEqual(verified["windows"], 263717)
        self.assertEqual(verified["label_mismatches"], 0)
        self.assertEqual(verified["authority_mismatches"], 0)
        self.assertEqual(
            release["conformance_report"]["build_id"], "build-cd8eaaf50792"
        )
        self.assertFalse(release["feature_flag_default"])
        self.assertFalse(release["strict_full_runtime_approved"])
        self.assertFalse(release["training_promotion_allowed"])

    def test_public_output_rejects_internal_ids_and_wrong_order(self) -> None:
        value = _public_fixture()
        self.assertEqual(validate_public_daily_label_result(value)["status"], "ok")
        with self.assertRaisesRegex(PeriodRuntimeError, "field 집합"):
            validate_public_daily_label_result(
                {**value, "period_id": "spd1_" + "a" * 64}
            )
        wrong_date = _public_fixture()
        wrong_date["days"][0]["date"] = "2026-09-03"  # type: ignore[index]
        with self.assertRaisesRegex(PeriodRuntimeError, "순서"):
            validate_public_daily_label_result(wrong_date)

    def test_period_id_is_release_domain_separated(self) -> None:
        signer = PeriodIdSigner.for_test(bytes(range(32)))
        first = signer.period_id("release-a", {"scope": "same"})
        second = signer.period_id("release-b", {"scope": "same"})
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("spd1_"))


@unittest.skipUnless(EPHEMERIS.is_file(), "private DE440s fixture가 없습니다.")
class PeriodDailyLabelIntegrationTests(unittest.TestCase):
    def test_three_day_candidate_uses_parent_official_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "runtime-hmac.key"
            key_path.write_bytes(bytes(range(32)))
            os.chmod(key_path, 0o600)
            runtime_signer = RuntimeIdSigner.from_key_file(key_path)
            with ApprovedSajuRuntimeEngineV15(
                release_registry=RELEASE_V15_PATH,
                enable_approved_runtime=True,
                ephemeris_path=EPHEMERIS,
                id_key_file=key_path,
                today_provider=lambda: date(2026, 9, 2),
            ) as engine:
                chart = engine.calculate_chart(_chart_arguments())
                internal = calculate_daily_labels_candidate(
                    _state(chart),
                    {
                        "type": "request_period",
                        "request": {
                            "schema_version": "saju-period-request-v2",
                            "date_expression": "explicit",
                            "start_date": "2026-09-02",
                            "end_date": "2026-09-04",
                        },
                    },
                    expected_revision=1,
                    reference_date=date(2026, 9, 2),
                    parent_engine=engine,
                    runtime_signer=runtime_signer,
                    period_signer=PeriodIdSigner.for_test(bytes(reversed(range(32)))),
                    authority_release_id="candidate-test",
                )
            public = public_daily_label_result(internal)
            self.assertEqual(public["period_scope"]["day_count"], 3)
            self.assertEqual(
                [item["date"] for item in public["days"]],
                ["2026-09-02", "2026-09-03", "2026-09-04"],
            )
            self.assertTrue(
                all(item["authority"] == "SOURCE_HARD_FACT" for item in public["days"])
            )
            self.assertNotIn("period_id", public)
            self.assertNotIn("chart_authorization", public)


if __name__ == "__main__":
    unittest.main()
