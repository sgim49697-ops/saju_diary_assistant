# test_saju_runtime_conformance_v7.py - Skyfield/UT1 후보 선택과 엄격 release 차단을 검증한다.

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

from scripts.evaluation.saju_runtime.conformance_v7 import (
    REPORT_ROOT,
    RuntimeConformanceV7Error,
    _conformance_status,
    _load_iers_snapshot,
    _validate_configs,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    IersFinalsCollectorError,
    _safe_output,
    collect,
    parse_snapshot,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v3 import (
    EXPECTED_OFFICIAL_JIE_ROWS,
    _candidate_selection,
    _iers_timescale,
    _kasi_printed_label,
    _official_evidence_for_mapping,
    candidate_boundary_checks,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
IERS_SNAPSHOT = (
    REPO_ROOT
    / "data/raw/saju_runtime/iers/v1.0.0/snapshot-2026-09-01-v3/finals2000A.all"
)


def _official_rows_and_records() -> tuple[
    list[dict[str, object]], list[dict[str, object]]
]:
    official: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    for year in range(1920, 2050):
        for term_index in sorted(JIE_TO_MONTH):
            local = datetime(
                year,
                term_index // 2 + 1,
                5 if term_index % 2 == 0 else 20,
                12,
                tzinfo=KST,
            )
            instant = local.astimezone(UTC)
            label = local.isoformat(timespec="minutes")
            official.append(
                {
                    "year": year,
                    "term_index": term_index,
                    "printed_local_date": local.date().isoformat(),
                    "reference_local_minute": label,
                }
            )
            records.append(
                {
                    "year": year,
                    "term_index": term_index,
                    "candidate_instant_nominal": instant.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "candidate_printed_local_date": local.date().isoformat(),
                    "candidate_display_minute_fixed_kst": label,
                }
            )
    return official, records


class IersFinalsCollectorTests(unittest.TestCase):
    def test_collection_rejects_wrong_confirmation_before_network_or_write(
        self,
    ) -> None:
        output = (
            REPO_ROOT
            / "data/raw/saju_runtime/iers/v1.0.0/unit-test-must-not-be-created"
        )
        with (
            patch(
                "scripts.evaluation.saju_runtime.iers_finals_collector._download"
            ) as download,
            self.assertRaisesRegex(IersFinalsCollectorError, "confirm-network"),
        ):
            collect(output=output, timeout=1.0, confirmation="wrong")
        download.assert_not_called()
        self.assertFalse(output.exists())

    def test_output_outside_private_raw_root_is_rejected(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(IersFinalsCollectorError, "아래여야"),
        ):
            _safe_output(Path(temporary) / "snapshot")

    def test_iers_timescale_uses_snapshot_without_network_fallback(self) -> None:
        if not IERS_SNAPSHOT.exists():
            self.skipTest("로컬 IERS snapshot이 없습니다.")
        with patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("network fallback attempted"),
        ):
            _, identity = _iers_timescale(IERS_SNAPSHOT)
        self.assertEqual(identity["rows"], 19_969)
        self.assertFalse(identity["automatic_download_or_fallback"])

    def test_parser_records_coverage_from_skyfield_parser(self) -> None:
        mjd = np.arange(41_684.0, 61_653.0)
        dut1 = np.linspace(0.8, -0.1, len(mjd))
        with patch(
            "skyfield.data.iers.parse_dut1_from_finals_all",
            return_value=(mjd, dut1),
        ):
            parsed = parse_snapshot(b"synthetic finals2000A")
        self.assertEqual(parsed["rows"], 19_969)
        self.assertEqual(parsed["utc_date_start"], "1973-01-02")
        self.assertEqual(parsed["utc_date_end"], "2027-09-04")

    def test_parser_rejects_short_coverage(self) -> None:
        mjd = np.arange(41_684.0, 60_684.0)
        dut1 = np.zeros(len(mjd))
        with (
            patch(
                "skyfield.data.iers.parse_dut1_from_finals_all",
                return_value=(mjd, dut1),
            ),
            self.assertRaisesRegex(IersFinalsCollectorError, "coverage"),
        ):
            parse_snapshot(b"synthetic finals2000A")

    def test_parser_rejects_nonfinite_dut1(self) -> None:
        mjd = np.arange(41_684.0, 61_653.0)
        dut1 = np.zeros(len(mjd))
        dut1[-1] = np.nan
        with (
            patch(
                "skyfield.data.iers.parse_dut1_from_finals_all",
                return_value=(mjd, dut1),
            ),
            self.assertRaisesRegex(IersFinalsCollectorError, "coverage"),
        ):
            parse_snapshot(b"synthetic finals2000A")


class ProviderTimeMappingTests(unittest.TestCase):
    def test_kasi_24_hour_label_preserves_pre_rounding_date(self) -> None:
        value = datetime(1964, 9, 7, 14, 59, 40, tzinfo=UTC)
        label = _kasi_printed_label(value)
        self.assertEqual(label["normalized_minute"], "1964-09-08T00:00+09:00")
        self.assertEqual(label["printed_local_date"], "1964-09-07")
        self.assertEqual(label["printed_hour"], 24)

    def test_exact_next_day_midnight_is_not_rewritten_as_24_hour(self) -> None:
        value = datetime(1964, 9, 7, 15, 0, 0, 17_704, tzinfo=UTC)
        label = _kasi_printed_label(value)
        self.assertEqual(label["printed_local_date"], "1964-09-08")
        self.assertEqual(label["printed_hour"], 0)

    def test_past_subsecond_interval_excess_selects_candidate_but_not_strict(
        self,
    ) -> None:
        official, records = _official_rows_and_records()
        reference = datetime.fromisoformat(official[0]["reference_local_minute"])
        instant = reference.astimezone(UTC) - timedelta(seconds=30.5)
        records[0]["candidate_instant_nominal"] = instant.isoformat().replace(
            "+00:00", "Z"
        )
        records[0]["candidate_display_minute_fixed_kst"] = (
            reference - timedelta(minutes=1)
        ).isoformat(timespec="minutes")
        evidence = _official_evidence_for_mapping(
            "candidate",
            records,
            official,
            field_prefix="candidate",
            official_collected_at=datetime(2026, 9, 1, tzinfo=KST),
        )
        self.assertEqual(evidence["official_current_minute_label_mismatches"], 1)
        self.assertEqual(evidence["past_uncertainty_failures"], 0)
        self.assertTrue(evidence["candidate_eligible"])
        self.assertFalse(evidence["raw_minute_strict_eligible"])

    def test_past_excess_over_declared_uncertainty_blocks_candidate(self) -> None:
        official, records = _official_rows_and_records()
        reference = datetime.fromisoformat(official[0]["reference_local_minute"])
        instant = reference.astimezone(UTC) - timedelta(seconds=31.1)
        records[0]["candidate_instant_nominal"] = instant.isoformat().replace(
            "+00:00", "Z"
        )
        records[0]["candidate_display_minute_fixed_kst"] = (
            reference - timedelta(minutes=1)
        ).isoformat(timespec="minutes")
        evidence = _official_evidence_for_mapping(
            "candidate",
            records,
            official,
            field_prefix="candidate",
            official_collected_at=datetime(2026, 9, 1, tzinfo=KST),
        )
        self.assertEqual(evidence["past_uncertainty_failures"], 1)
        self.assertFalse(evidence["candidate_eligible"])

    def test_future_raw_mismatch_is_classified_nonapproval(self) -> None:
        official, records = _official_rows_and_records()
        reference = datetime.fromisoformat(official[-1]["reference_local_minute"])
        instant = reference.astimezone(UTC) - timedelta(seconds=35)
        records[-1]["candidate_instant_nominal"] = instant.isoformat().replace(
            "+00:00", "Z"
        )
        records[-1]["candidate_display_minute_fixed_kst"] = (
            reference - timedelta(minutes=1)
        ).isoformat(timespec="minutes")
        evidence = _official_evidence_for_mapping(
            "candidate",
            records,
            official,
            field_prefix="candidate",
            official_collected_at=datetime(2049, 1, 1, tzinfo=KST),
        )
        self.assertGreater(evidence["future_raw_interval_failures"], 0)
        self.assertEqual(evidence["past_uncertainty_failures"], 0)
        self.assertTrue(evidence["candidate_eligible"])
        self.assertFalse(evidence["raw_minute_strict_eligible"])

    def test_candidate_ranking_prefers_skyfield_without_runtime_change(self) -> None:
        selection = _candidate_selection(
            {
                "astronomy_engine": {
                    "official_current_date_mismatches": 1,
                    "past_uncertainty_failures": 218,
                    "official_current_minute_label_mismatches": 303,
                    "candidate_eligible": False,
                    "raw_minute_strict_eligible": False,
                },
                "skyfield_de440s_builtin_ut1": {
                    "official_current_date_mismatches": 0,
                    "past_uncertainty_failures": 0,
                    "official_current_minute_label_mismatches": 22,
                    "candidate_eligible": True,
                    "raw_minute_strict_eligible": False,
                },
                "skyfield_de440s_current_iers_ut1": {
                    "official_current_date_mismatches": 0,
                    "past_uncertainty_failures": 0,
                    "official_current_minute_label_mismatches": 29,
                    "candidate_eligible": True,
                    "raw_minute_strict_eligible": False,
                },
                "skyfield_de440s_astronomy_engine_delta_t_ut": {
                    "official_current_date_mismatches": 0,
                    "past_uncertainty_failures": 7,
                    "official_current_minute_label_mismatches": 97,
                    "candidate_eligible": False,
                    "raw_minute_strict_eligible": False,
                },
            }
        )
        self.assertEqual(
            selection["preferred_candidate"], "skyfield_de440s_builtin_ut1"
        )
        self.assertIsNone(selection["strict_eligible_provider"])
        self.assertFalse(selection["runtime_provider_changed"])
        self.assertEqual(
            selection["ranked_candidates"][:2],
            ["skyfield_de440s_builtin_ut1", "skyfield_de440s_current_iers_ut1"],
        )

    def test_candidate_boundary_checks_all_5400_cases(self) -> None:
        start = datetime(1900, 1, 1, tzinfo=UTC)
        records = [
            {
                "year": 1900 + order // 12,
                "term_index": sorted(JIE_TO_MONTH)[order % 12],
                "candidate_instant_nominal": (start + timedelta(days=order * 30))
                .isoformat()
                .replace("+00:00", "Z"),
            }
            for order in range(1_800)
        ]
        result = candidate_boundary_checks(records, field_prefix="candidate")
        self.assertEqual(result["cases"], 5_400)
        self.assertEqual(result["mismatch_rows"], 0)


class ConformanceV7ContractTests(unittest.TestCase):
    def test_status_prioritizes_missing_data_and_baseline_failures(self) -> None:
        self.assertEqual(
            _conformance_status(
                data_availability_gate_passed=False,
                provider_candidate_gate_passed=True,
                strict_runtime_provider_gate_passed=False,
                baseline_conformance_gate_passed=True,
            ),
            "blocked_incomplete_available_official_data",
        )
        self.assertEqual(
            _conformance_status(
                data_availability_gate_passed=True,
                provider_candidate_gate_passed=True,
                strict_runtime_provider_gate_passed=False,
                baseline_conformance_gate_passed=False,
            ),
            "blocked_baseline_conformance_failures",
        )

    def test_config_hash_chain_and_candidate_release_split_are_valid(self) -> None:
        gate, registry = _validate_configs()
        self.assertEqual(
            gate["provider_candidate_gate"]["preferred_candidate"],
            "skyfield_de440s_builtin_ut1",
        )
        self.assertIsNone(gate["selection_rule"]["strict_eligible_provider"])
        self.assertTrue(
            registry["provider_candidates"]["skyfield_de440s_builtin_ut1"][
                "preferred_candidate"
            ]
        )
        self.assertFalse(
            registry["interpretation"][
                "preferred_candidate_selection_is_runtime_approval"
            ]
        )

    def test_gate_byte_tamper_is_rejected(self) -> None:
        source = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.5.0.json"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / source.name
            value = json.loads(source.read_text(encoding="utf-8"))
            value["status"] = "tampered"
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with (
                patch("scripts.evaluation.saju_runtime.conformance_v7.GATE_PATH", path),
                self.assertRaisesRegex(RuntimeConformanceV7Error, "hash chain"),
            ):
                _validate_configs()

    def test_private_iers_snapshot_provenance_is_valid(self) -> None:
        if not IERS_SNAPSHOT.exists():
            self.skipTest("로컬 IERS snapshot이 없습니다.")
        _, identity = _load_iers_snapshot(IERS_SNAPSHOT)
        self.assertEqual(identity["rows"], 19_969)
        self.assertEqual(identity["utc_date_range"], ["1973-01-02", "2027-09-04"])
        self.assertFalse(identity["automatic_fallback_used"])


class ConformanceV7ArtifactTests(unittest.TestCase):
    def test_committed_report_selects_candidate_but_blocks_strict_runtime(self) -> None:
        reports = sorted(REPORT_ROOT.glob("build-*/aggregate.json"))
        self.assertEqual(len(reports), 1)
        report_path = reports[0]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        manifest = json.loads(
            (report_path.parent / "build_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["suite_version"], "saju-runtime-conformance-v7.0.0")
        self.assertEqual(
            report["status"],
            "preferred_candidate_selected_strict_runtime_gate_blocked",
        )
        self.assertTrue(report["data_availability_gate_passed"])
        self.assertTrue(report["provider_candidate_gate_passed"])
        self.assertTrue(report["baseline_conformance_gate_passed"])
        self.assertFalse(report["strict_runtime_provider_gate_passed"])
        self.assertFalse(report["technical_gate_passed"])
        self.assertEqual(
            report["preferred_provider_candidate"],
            "skyfield_de440s_builtin_ut1",
        )
        comparison = report["solar_term_provider_comparison"]
        mappings = comparison["time_mapping_candidates"]
        self.assertEqual(
            mappings["skyfield_de440s_proleptic_utc"][
                "official_current_minute_label_mismatches"
            ],
            157,
        )
        self.assertEqual(
            mappings["skyfield_de440s_builtin_ut1"][
                "official_current_minute_label_mismatches"
            ],
            22,
        )
        self.assertEqual(
            mappings["skyfield_de440s_current_iers_ut1"][
                "official_current_minute_label_mismatches"
            ],
            29,
        )
        self.assertEqual(
            mappings["skyfield_de440s_astronomy_engine_delta_t_ut"][
                "official_current_minute_label_mismatches"
            ],
            97,
        )
        self.assertEqual(
            comparison["time_mapping_source_identity"]["skyfield_builtin"][
                "files_sha256"
            ]["iers.npz"],
            "c7d7536d898dfa9f8cd43e8044ff51e108cc8289675a13fee9822010a1c4935c",
        )
        self.assertEqual(
            comparison["time_mapping_source_identity"]["current_iers_snapshot"][
                "snapshot_sha256"
            ],
            "e3905ff7a74b791744704aa3e900a2161e96db97a30095d8fc442b04e4cfe058",
        )
        self.assertFalse(
            comparison["time_mapping_source_identity"]["current_iers_snapshot"][
                "automatic_download_or_fallback"
            ]
        )
        self.assertEqual(
            comparison["selection"]["ranked_candidates"][:2],
            ["skyfield_de440s_builtin_ut1", "skyfield_de440s_current_iers_ut1"],
        )
        preferred = mappings["skyfield_de440s_builtin_ut1"]
        self.assertEqual(preferred["past_uncertainty_failures"], 0)
        self.assertEqual(preferred["future_raw_interval_failures"], 8)
        self.assertEqual(preferred["past_rows_at_snapshot_collection"], 1_280)
        self.assertEqual(preferred["future_forecast_rows_at_snapshot_collection"], 280)
        self.assertEqual(
            comparison["root_solver_diagnostic"]["skyfield_find_discrete_used"],
            False,
        )
        self.assertLess(
            comparison["root_solver_diagnostic"][
                "maximum_skyfield_root_longitude_residual_arcseconds"
            ],
            1e-6,
        )
        self.assertFalse(
            report["baengno_1964_evidence"][
                "normalization_caused_current_source_mismatch"
            ]
        )
        self.assertEqual(
            report["baengno_1964_evidence"]["astronomy_engine_exact_local_kst"],
            "1964-09-08T00:00:00.017704+09:00",
        )
        for key in (
            "runtime_approved",
            "release_approval_performed",
            "runtime_provider_changed",
            "app_binding_performed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
        ):
            self.assertFalse(report[key])
        self.assertEqual(
            manifest["build_id"],
            "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12],
        )
        for filename, identity in manifest["artifacts"].items():
            payload = (report_path.parent / filename).read_bytes()
            self.assertEqual(identity["bytes"], len(payload))
            self.assertEqual(identity["sha256"], hashlib.sha256(payload).hexdigest())
        records = [
            json.loads(line)
            for line in (report_path.parent / "provider_records.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(len(records), 1_800)
        self.assertEqual(
            comparison["records_sha256"],
            hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        )
        self.assertEqual(EXPECTED_OFFICIAL_JIE_ROWS, 1_560)


if __name__ == "__main__":
    unittest.main()
