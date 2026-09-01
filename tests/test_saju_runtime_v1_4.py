# test_saju_runtime_v1_4.py - chart-only v1.4 범위·권한·경계·release 계약을 검증한다.

from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.conformance_v9 import verify_report
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_4 import (
    APPROVED_SCOPE_V14,
    ENGINE_VERSION_V14,
    RELEASE_V14_PATH,
    REPORT_V17_ROOT,
    derive_gate_checks_v1_4,
    validate_contract_registry_v1_4,
    validate_release_registry_v1_4,
)
from scripts.runtime.calculation.engine_v1_4 import (
    ApprovedSajuRuntimeEngineV14,
    boundary_uncertainty_hits,
    is_approved_solar_date,
    validate_chart_only_candidate,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_term_types import (
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
    SOURCE_HARD_FACT,
    SolarTermBoundary,
)


def _evidence(boundary: SolarTermBoundary) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "provider_id": SkyfieldSolarTermProvider.provider_id,
        "root_time_scale": "TT",
        "boundary_comparison_time_scale": "TT",
        "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
        "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
        "provider_generated_value_is_official": False,
        "authority_classes": [PAST_OFFICIAL_CORROBORATED],
        "overall_authority": PAST_OFFICIAL_CORROBORATED,
        "contains_future_nonapproval": False,
        "boundaries": [boundary.evidence_record("month_boundary_previous_jie")],
    }


def _normalized(
    *, precision: str = "exact", birth_time: str | None = "09:00"
) -> dict[str, object]:
    return {
        "calendar": "solar",
        "local_birth_date": "1958-05-06",
        "solar_birth_date": "1958-05-06",
        "lunar_birth_date": {
            "year": 1958,
            "month": 3,
            "day": 18,
            "leap_month": False,
        },
        "lunar_leap_month": None,
        "birth_time_precision": precision,
        "local_birth_time": birth_time if precision == "exact" else None,
        "birth_time_range": (
            {"start": "08:59", "end": "09:01"} if precision == "range" else None
        ),
        "country_code": "KR",
        "city": "서울",
        "iana_time_zone": "Asia/Seoul",
        "fold": None,
        "policy_id": "KR_CIVIL_MIDNIGHT_V1",
    }


def _candidate(
    boundary: SolarTermBoundary, *, precision: str = "exact"
) -> dict[str, object]:
    normalized = _normalized(precision=precision)
    facts = {
        "calendar": {"solar_date": "1958-05-06"},
        "pillars": {"year": {"ganzhi": "무술"}},
        "solar_term_evidence": _evidence(boundary),
    }
    alternative = {"hard_facts": deepcopy(facts), "chart_id": "sc2_" + "1" * 64}
    return {
        "status": "partial",
        "code": "RUNTIME_RELEASE_PENDING",
        "message": "candidate",
        "normalized_input": normalized,
        "hard_facts": deepcopy(facts),
        "stable_facts": deepcopy(facts),
        "alternative_charts": [alternative],
        "uncertainty": {"birth_time_precision": precision, "candidate_count": 1},
        "fact_authority": "HARD_CANDIDATE",
        "birth_input_id": "sbi2_" + "2" * 64,
        "chart_id": "sc2_" + "1" * 64 if precision == "exact" else None,
        "chart_set_id": None if precision == "exact" else "scs2_" + "3" * 64,
        "calculation_run_id": "scr2_" + "4" * 64,
        "engine_version": "saju-runtime-python-v1.3.0",
        "calculation_schema_version": "1.3.0",
        "id_contract_version": "saju-runtime-id-hmac-v2.0.0",
        "policy_id": "KR_CIVIL_MIDNIGHT_V1",
        "source_versions": {
            "solar_term_provider": SkyfieldSolarTermProvider.provider_id
        },
        "warnings": [{"code": "RUNTIME_RELEASE_PENDING", "message": "candidate"}],
        "limitations": [],
        "internal_trace": {},
    }


def _chart_arguments(
    birth_date: str,
    *,
    birth_time: str | None = "13:00",
    precision: str = "exact",
    time_range: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "birth_date": birth_date,
        "calendar": "solar",
        "leap_month": None,
        "birth_time": birth_time if precision == "exact" else None,
        "time_precision": precision,
        "time_range": time_range if precision == "range" else None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


class _BoundaryProvider:
    provider_id = SkyfieldSolarTermProvider.provider_id

    def __init__(self, target: SolarTermBoundary) -> None:
        self.target = target

    def boundary(self, year: int, term_index: int) -> SolarTermBoundary:
        if (year, term_index) == (self.target.year, self.target.term_index):
            return self.target
        return SolarTermBoundary(
            provider_id=self.provider_id,
            year=year,
            term_index=term_index,
            term_name=f"절기-{term_index}",
            saju_month_number=1,
            instant_utc=datetime(
                year, min(term_index // 2 + 1, 12), 1, tzinfo=timezone.utc
            ),
            tt_whole=2_400_000,
            tt_fraction=(term_index + 1) / 100,
            official_display_minute_fixed_kst=(
                datetime(
                    year,
                    min(term_index // 2 + 1, 12),
                    1,
                    9,
                    tzinfo=timezone(timedelta(hours=9)),
                ).isoformat(timespec="minutes")
            ),
            authority_class=PROFILE_DETERMINISTIC,
            official_source_evidence_class=None,
        )


class SajuRuntimeV14Test(unittest.TestCase):
    def setUp(self) -> None:
        local_boundary = datetime(
            1958, 5, 6, 9, 0, tzinfo=ZoneInfo("Asia/Seoul")
        ).astimezone(timezone.utc) + timedelta(microseconds=6_009)
        self.boundary = SolarTermBoundary(
            provider_id=SkyfieldSolarTermProvider.provider_id,
            year=1958,
            term_index=8,
            term_name="입하",
            saju_month_number=4,
            instant_utc=local_boundary,
            tt_whole=2_436_330,
            tt_fraction=0.5008,
            official_display_minute_fixed_kst="1958-05-06T09:00+09:00",
            authority_class=PAST_OFFICIAL_CORROBORATED,
            official_source_evidence_class=SOURCE_HARD_FACT,
        )

    def test_contract_registry_is_valid(self) -> None:
        registry = validate_contract_registry_v1_4()
        self.assertEqual(
            registry["registry_id"], "saju-runtime-calculation-registry-v1.4.0"
        )

    def test_complete_date_scope_boundaries(self) -> None:
        self.assertFalse(is_approved_solar_date(date(1920, 1, 6)))
        self.assertTrue(is_approved_solar_date(date(1920, 1, 7)))
        self.assertTrue(is_approved_solar_date(date(2026, 8, 31)))
        self.assertFalse(is_approved_solar_date(date(2026, 9, 1)))

    def test_boundary_hit_detects_subsecond_minute(self) -> None:
        hits = boundary_uncertainty_hits(
            _normalized(),
            _BoundaryProvider(self.boundary),  # type: ignore[arg-type]
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["term_name"], "입하")
        self.assertLess(hits[0]["distance_seconds"], 0.01)

    def test_candidate_requires_only_past_official_evidence(self) -> None:
        value = _candidate(self.boundary)
        validate_chart_only_candidate(value, normalized=value["normalized_input"])
        tampered = deepcopy(value)
        tampered["hard_facts"]["solar_term_evidence"]["authority_classes"] = [
            PROFILE_DETERMINISTIC
        ]
        with self.assertRaisesRegex(RuntimeCalculationError, "과거 공식"):
            validate_chart_only_candidate(
                tampered, normalized=tampered["normalized_input"]
            )

    def test_period_is_always_out_of_scope(self) -> None:
        engine = object.__new__(ApprovedSajuRuntimeEngineV14)
        engine.source_versions = {}
        result = engine.calculate_period({"chart_id": "ignored"})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["code"], "CHART_ONLY_PERIOD_OUT_OF_SCOPE")

    def test_missing_release_stays_blocked(self) -> None:
        engine = ApprovedSajuRuntimeEngineV14()
        try:
            result = engine.calculate_chart({})
        finally:
            engine.close()
        self.assertEqual(result["code"], "RUNTIME_RELEASE_REQUIRED")
        self.assertFalse(engine.enable_approved_runtime)

    def test_promote_rekeys_exact_result_and_removes_candidate_authority(self) -> None:
        engine = object.__new__(ApprovedSajuRuntimeEngineV14)
        engine._signer = RuntimeIdSigner.for_test(bytes(range(32)))
        engine.source_versions = {
            "runtime_release": "saju-runtime-release-v1.4.0-000000000000"
        }
        value = _candidate(self.boundary)
        result = engine._promote(
            value,
            normalized=value["normalized_input"],
            uncertainty_checked=False,
            uncertainty_stable=True,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fact_authority"], "HARD_GT")
        self.assertEqual(result["engine_version"], ENGINE_VERSION_V14)
        self.assertEqual(result["runtime_scope"], APPROVED_SCOPE_V14)
        self.assertTrue(result["chart_id"].startswith("sc2_"))
        self.assertNotEqual(result["chart_id"], value["chart_id"])
        self.assertNotIn(
            "RUNTIME_RELEASE_PENDING",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_derived_gate_checks_reject_count_drift(self) -> None:
        report = {
            "parent_v8": {"verified": True},
            "scope_matrix": {
                "cases": 328722,
                "allowed": 233724,
                "blocked": 94998,
                "failures": 0,
            },
            "exact_chart_calculations": {"cases": 77908, "failures": 0},
            "boundary_minute_gate": {
                "past_jie_rows": 1279,
                "probe_cases": 2558,
                "probe_failures": 0,
                "past_raw_minute_mismatches": 14,
                "quarantined_minutes": 50,
                "unclassified_failures": 0,
            },
            "uncertain_time_gate": {
                "cases": 2660,
                "failures": 0,
                "blocked_boundary_ranges": 50,
                "stable_boundary_unknown": 50,
            },
            "negative_and_governance_gate": {
                "period_block_failures": 0,
                "date_guard_failures": 0,
                "authority_tamper_failures": 0,
                "production_application_binding": False,
                "mix20k_v3_1_regeneration_allowed": False,
                "training_promotion_allowed": False,
                "sealed_blind_accessed": False,
            },
        }
        checks = derive_gate_checks_v1_4(report)
        self.assertTrue(all(checks.values()))
        report["boundary_minute_gate"]["quarantined_minutes"] = 49
        self.assertFalse(derive_gate_checks_v1_4(report)["subsecond_quarantine_exact"])

    def test_derived_gate_checks_reject_uncertain_boundary_drift(self) -> None:
        report = {
            "parent_v8": {"verified": True},
            "scope_matrix": {
                "cases": 328722,
                "allowed": 233724,
                "blocked": 94998,
                "failures": 0,
            },
            "exact_chart_calculations": {"cases": 77908, "failures": 0},
            "boundary_minute_gate": {
                "past_jie_rows": 1279,
                "probe_cases": 2558,
                "probe_failures": 0,
                "past_raw_minute_mismatches": 14,
                "quarantined_minutes": 50,
                "unclassified_failures": 0,
            },
            "uncertain_time_gate": {
                "cases": 2660,
                "failures": 0,
                "blocked_boundary_ranges": 50,
                "stable_boundary_unknown": 50,
            },
            "negative_and_governance_gate": {
                "period_block_failures": 0,
                "date_guard_failures": 0,
                "authority_tamper_failures": 0,
                "production_application_binding": False,
                "mix20k_v3_1_regeneration_allowed": False,
                "training_promotion_allowed": False,
                "sealed_blind_accessed": False,
            },
        }
        self.assertTrue(all(derive_gate_checks_v1_4(report).values()))
        report["uncertain_time_gate"]["stable_boundary_unknown"] = 49
        self.assertFalse(
            derive_gate_checks_v1_4(report)["boundary_unknown_stability_exact"]
        )
        report["uncertain_time_gate"]["stable_boundary_unknown"] = 50
        report["uncertain_time_gate"]["blocked_boundary_ranges"] = 49
        self.assertFalse(
            derive_gate_checks_v1_4(report)[
                "boundary_range_instability_classified_exact"
            ]
        )

    def test_committed_report_and_release_are_self_consistent(self) -> None:
        report_root = REPORT_V17_ROOT / "build-9f1784e74a4e"
        verified = verify_report(report_root)
        release = validate_release_registry_v1_4(RELEASE_V14_PATH)
        self.assertEqual(verified["build_id"], "build-9f1784e74a4e")
        self.assertEqual(
            release["release_id"], "saju-runtime-release-v1.4.0-63dc8d398e90"
        )
        self.assertEqual(
            release["conformance_report"]["build_id"], verified["build_id"]
        )
        self.assertEqual(
            {path.name for path in report_root.iterdir()},
            {"aggregate.json", "build_manifest.json"},
        )
        self.assertFalse(
            release["conformance_manifest_data"]["raw_case_output_tracked"]
        )
        self.assertFalse(release["sealed_blind_accessed"])


_DEFAULT_EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
_INTEGRATION_EPHEMERIS = Path(
    os.environ.get("SAJU_RUNTIME_TEST_DE440S", str(_DEFAULT_EPHEMERIS))
)


@unittest.skipUnless(
    _INTEGRATION_EPHEMERIS.is_file(), "로컬 Git 제외 DE440s가 필요합니다."
)
class SajuRuntimeV14IntegrationTest(unittest.TestCase):
    def test_active_chart_only_release_guards_real_boundaries(self) -> None:
        cases = (
            (
                "lower",
                _chart_arguments("1920-01-07", birth_time="00:00"),
                "ok",
                "HARD_GT",
            ),
            (
                "before_lower",
                _chart_arguments("1920-01-06", birth_time="23:59"),
                "blocked",
                None,
            ),
            (
                "baengno_1964",
                _chart_arguments("1964-09-07", birth_time="23:59"),
                "ok",
                "HARD_GT",
            ),
            (
                "upper",
                _chart_arguments("2026-08-31", birth_time="23:59"),
                "ok",
                "HARD_GT",
            ),
            (
                "after_upper",
                _chart_arguments("2026-09-01", birth_time="00:00"),
                "blocked",
                None,
            ),
            (
                "quarantine_exact",
                _chart_arguments("1958-05-06", birth_time="10:19"),
                "blocked",
                None,
            ),
            (
                "quarantine_range",
                _chart_arguments(
                    "1958-05-06",
                    birth_time=None,
                    precision="range",
                    time_range={"start": "10:19", "end": "10:19"},
                ),
                "blocked",
                None,
            ),
            (
                "quarantine_unknown",
                _chart_arguments("1958-05-06", birth_time=None, precision="unknown"),
                "partial",
                "POLICY_BOUND_RULE",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="saju-v14-test-") as directory:
            key = Path(directory) / "runtime-id.key"
            key.write_bytes(os.urandom(32))
            key.chmod(0o600)
            with ApprovedSajuRuntimeEngineV14(
                release_registry=RELEASE_V14_PATH,
                enable_approved_runtime=True,
                ephemeris_path=_INTEGRATION_EPHEMERIS,
                id_key_file=key,
            ) as engine:
                for name, arguments, expected_status, expected_authority in cases:
                    with self.subTest(name=name):
                        result = engine.calculate_chart(arguments)
                        self.assertEqual(result["status"], expected_status)
                        if expected_authority is not None:
                            self.assertEqual(
                                result["fact_authority"], expected_authority
                            )
                period = engine.calculate_period({"chart_id": "unused"})
        self.assertEqual(period["status"], "blocked")
        self.assertEqual(period["code"], "CHART_ONLY_PERIOD_OUT_OF_SCOPE")


if __name__ == "__main__":
    unittest.main()
