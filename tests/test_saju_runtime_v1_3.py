# test_saju_runtime_v1_3.py - Skyfield candidate runtime과 절입 권한 계약을 검증한다.

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.runtime.calculation.contracts import (
    CONFIG_ROOT,
    REPO_ROOT,
    load_json_object,
)
from scripts.runtime.calculation.contracts_v1_3 import (
    EXPECTED_ARTIFACTS,
    runtime_source_versions_v1_3,
    validate_contract_registry_v1_3,
)
from scripts.runtime.calculation.engine_v1_2 import SajuRuntimeEngineV12
from scripts.runtime.calculation.engine_v1_3 import (
    SajuRuntimeEngineV13,
    execute_candidate_runtime_tool_v1_3,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
    validate_de440s,
)
from scripts.runtime.calculation.solar_term_types import (
    FORECAST_DIAGNOSTIC_NONAPPROVAL,
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
    SOURCE_HARD_FACT,
    SolarTermBoundary,
    build_solar_term_evidence,
    merge_solar_term_evidence,
)

UTC = timezone.utc
EPHEMERIS = (
    REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
)


def _chart_arguments(birth_date: str = "1989-01-05") -> dict[str, object]:
    return {
        "birth_date": birth_date,
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "13:00",
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


class _EvidenceProvider:
    provider_id = SkyfieldSolarTermProvider.provider_id

    def evidence_context(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "root_time_scale": "TT",
            "boundary_comparison_time_scale": "TT",
            "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
            "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
            "provider_generated_value_is_official": False,
        }


def _boundary(year: int, index: int, authority: str) -> SolarTermBoundary:
    instant = datetime(year, 2, 4, 0, index, tzinfo=UTC)
    return SolarTermBoundary(
        provider_id=SkyfieldSolarTermProvider.provider_id,
        year=year,
        term_index=index,
        term_name="입춘" if index == 2 else "경칩",
        saju_month_number=1 if index == 2 else 2,
        instant_utc=instant,
        tt_whole=2_400_000,
        tt_fraction=0.1 + index / 100,
        official_display_minute_fixed_kst=instant.astimezone(
            timezone(timedelta(hours=9))
        ).isoformat(timespec="minutes"),
        authority_class=authority,
        official_source_evidence_class=(
            None if authority == PROFILE_DETERMINISTIC else SOURCE_HARD_FACT
        ),
    )


class RuntimeV13ContractTests(unittest.TestCase):
    def test_registry_and_static_source_versions_are_valid(self) -> None:
        registry = validate_contract_registry_v1_3()
        self.assertEqual(
            registry["status"],
            "skyfield_candidate_runtime_bound_release_out_of_scope",
        )
        self.assertEqual(
            {item["path"] for item in registry["artifacts"]}, EXPECTED_ARTIFACTS
        )
        versions = runtime_source_versions_v1_3(
            require_runtime_dependencies=False
        )
        self.assertEqual(
            versions["solar_term_provider"],
            "skyfield-1.55-jpl-de440s-builtin-ut1",
        )
        self.assertEqual(versions["source_registry"], "saju-runtime-sources-v1.6.0")
        self.assertNotIn("astronomy_engine", versions)

    def test_disabled_engine_needs_no_ephemeris_and_stays_blocked(self) -> None:
        engine = SajuRuntimeEngineV13(
            signer=RuntimeIdSigner.for_test(bytes(range(32)))
        )
        result = engine.calculate_chart(_chart_arguments())
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["code"], "RUNTIME_FEATURE_DISABLED")
        self.assertIsNone(result["fact_authority"])
        self.assertEqual(result["engine_version"], "saju-runtime-python-v1.3.0")

    def test_active_engine_requires_one_unambiguous_provider_source(self) -> None:
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        with self.assertRaisesRegex(RuntimeCalculationError, "DE440s"):
            SajuRuntimeEngineV13(
                signer=signer,
                enable_candidate_runtime=True,
            )
        with self.assertRaisesRegex(RuntimeCalculationError, "비활성"):
            SajuRuntimeEngineV13(
                signer=signer,
                ephemeris_path=Path("unused-de440s.bsp"),
            )

    def test_output_schema_requires_structured_solar_term_evidence(self) -> None:
        schema = load_json_object(
            CONFIG_ROOT / "calculation_output_schema-v1.3.0.json"
        )
        evidence = schema["$defs"]["solar_term_evidence"]
        self.assertIn("solar_term_evidence", schema["$defs"]["facts_with_solar_term_evidence"]["required"])
        self.assertEqual(
            set(evidence["properties"]["overall_authority"]["enum"]),
            {
                PROFILE_DETERMINISTIC,
                PAST_OFFICIAL_CORROBORATED,
                FORECAST_DIAGNOSTIC_NONAPPROVAL,
            },
        )


class SkyfieldSourceValidationTests(unittest.TestCase):
    def test_relative_small_and_symlink_ephemeris_are_rejected(self) -> None:
        with self.assertRaises(RuntimeCalculationError):
            validate_de440s(Path("de440s.bsp"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            small = root / "de440s.bsp"
            small.write_bytes(b"not-de440s")
            with self.assertRaisesRegex(RuntimeCalculationError, "크기"):
                validate_de440s(small)
            target = root / "target-de440s.bsp"
            target.write_bytes(b"not-de440s")
            small.unlink()
            small.symlink_to(target)
            with self.assertRaisesRegex(RuntimeCalculationError, "symlink"):
                validate_de440s(small)


class SolarTermEvidenceTests(unittest.TestCase):
    def test_merge_deduplicates_roles_and_uses_conservative_precedence(self) -> None:
        provider = _EvidenceProvider()
        past = _boundary(1964, 2, PAST_OFFICIAL_CORROBORATED)
        future = _boundary(2026, 4, FORECAST_DIAGNOSTIC_NONAPPROVAL)
        first = build_solar_term_evidence(provider, (("year", past),))
        repeated = build_solar_term_evidence(provider, (("month", past),))
        forecast = build_solar_term_evidence(provider, (("period", future),))
        merged = merge_solar_term_evidence((first, repeated, forecast))
        self.assertEqual(merged["overall_authority"], FORECAST_DIAGNOSTIC_NONAPPROVAL)
        self.assertTrue(merged["contains_future_nonapproval"])
        self.assertEqual(len(merged["boundaries"]), 2)
        self.assertEqual(merged["boundaries"][0]["roles"], ["month", "year"])
        self.assertFalse(merged["provider_generated_value_is_official"])


@unittest.skipUnless(EPHEMERIS.is_file(), "로컬 Git 제외 DE440s가 필요합니다.")
class RuntimeV13SkyfieldIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provider = SkyfieldSolarTermProvider(EPHEMERIS)
        cls.signer = RuntimeIdSigner.for_test(bytes(range(32)))
        cls.engine = SajuRuntimeEngineV13(
            signer=cls.signer,
            enable_candidate_runtime=True,
            solar_term_provider=cls.provider,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.provider.close()

    def test_tt_boundary_before_exact_after_and_identity(self) -> None:
        boundary = self.provider.boundary(1964, 16)
        self.assertEqual(self.provider.compare_instant(boundary.instant_utc, boundary), 0)
        self.assertEqual(
            self.provider.compare_instant(
                boundary.instant_utc - timedelta(microseconds=1), boundary
            ),
            -1,
        )
        self.assertEqual(
            self.provider.compare_instant(
                boundary.instant_utc + timedelta(microseconds=1), boundary
            ),
            1,
        )
        self.assertEqual(
            boundary.official_display_minute_fixed_kst,
            "1964-09-07T23:59+09:00",
        )
        identity = self.provider.identity()
        self.assertFalse(identity["automatic_download_or_fallback"])
        self.assertFalse(identity["astronomy_engine_fallback"])

    def test_chart_authority_is_visible_without_hard_gt_promotion(self) -> None:
        expected = {
            "1919-06-01": PROFILE_DETERMINISTIC,
            "1964-09-08": PAST_OFFICIAL_CORROBORATED,
            "2026-09-08": FORECAST_DIAGNOSTIC_NONAPPROVAL,
        }
        for birth_date, authority in expected.items():
            with self.subTest(birth_date=birth_date):
                internal, visible = execute_candidate_runtime_tool_v1_3(
                    self.engine, "calculate_saju_chart", _chart_arguments(birth_date)
                )
                evidence = internal["hard_facts"]["solar_term_evidence"]
                self.assertEqual(internal["fact_authority"], "HARD_CANDIDATE")
                self.assertEqual(evidence["overall_authority"], authority)
                self.assertEqual(
                    visible["hard_facts"]["solar_term_evidence"], evidence
                )

    def test_period_exposes_future_boundary_and_ids_change_from_v12(self) -> None:
        arguments = _chart_arguments()
        chart = self.engine.calculate_chart(arguments)
        period = self.engine.calculate_period(
            {
                "chart_id": chart["chart_id"],
                "period_type": "week",
                "start_date": "2026-09-07",
                "end_date": "2026-09-08",
                "timezone": "Asia/Seoul",
            }
        )
        self.assertEqual(
            period["hard_facts"]["solar_term_evidence"]["overall_authority"],
            FORECAST_DIAGNOSTIC_NONAPPROVAL,
        )
        self.assertEqual(
            period["hard_facts"]["period"]["jie_boundaries"][0][
                "solar_term_authority"
            ],
            FORECAST_DIAGNOSTIC_NONAPPROVAL,
        )
        v12 = SajuRuntimeEngineV12(
            signer=self.signer, enable_candidate_runtime=True
        ).calculate_chart(arguments)
        self.assertNotEqual(chart["chart_id"], v12["chart_id"])

    def test_unknown_time_preserves_candidates_and_merges_evidence(self) -> None:
        arguments = _chart_arguments("1989-01-05")
        arguments.update(
            {
                "birth_time": None,
                "time_precision": "unknown",
            }
        )
        result = self.engine.calculate_chart(arguments)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["hard_facts"]["pillars"]["hour"])
        self.assertIsNone(result["chart_id"])
        self.assertIsNotNone(result["chart_set_id"])
        self.assertGreaterEqual(result["uncertainty"]["candidate_count"], 12)
        evidence = result["hard_facts"]["solar_term_evidence"]
        self.assertEqual(evidence["overall_authority"], PAST_OFFICIAL_CORROBORATED)
        self.assertTrue(evidence["boundaries"])
        self.assertTrue(
            all(
                item["hard_facts"].get("solar_term_evidence")
                for item in result["alternative_charts"]
            )
        )

    def test_dst_gap_blocks_and_fold_keeps_both_candidates(self) -> None:
        gap_arguments = _chart_arguments("1987-05-10")
        gap_arguments["birth_time"] = "02:30"
        gap = self.engine.calculate_chart(gap_arguments)
        self.assertEqual(gap["status"], "blocked")
        self.assertEqual(gap["code"], "NONEXISTENT_LOCAL_TIME")

        fold_arguments = _chart_arguments("1987-10-11")
        fold_arguments["birth_time"] = "02:30"
        fold = self.engine.calculate_chart(fold_arguments)
        self.assertEqual(fold["status"], "partial")
        self.assertIsNone(fold["chart_id"])
        self.assertIsNotNone(fold["chart_set_id"])
        self.assertIn(
            "AMBIGUOUS_LOCAL_TIME",
            {warning["code"] for warning in fold["warnings"]},
        )

    def test_provider_close_is_idempotent_and_use_after_close_fails(self) -> None:
        provider = SkyfieldSolarTermProvider(EPHEMERIS)
        provider.close()
        provider.close()
        with self.assertRaisesRegex(RuntimeCalculationError, "종료"):
            provider.boundary(1964, 16)


if __name__ == "__main__":
    unittest.main()
