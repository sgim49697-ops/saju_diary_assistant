# test_saju_runtime_conformance_v6.py - 공식 절기 snapshot과 분리 Gate·시간축 진단을 검증한다.

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation.saju_runtime.conformance_v6 import (
    REPORT_ROOT,
    RuntimeConformanceV6Error,
    _crosscheck_kasi_sources,
    _validate_configs,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    EXPECTED_JIE_ROWS,
    EXPECTED_ROWS,
    KasiOfficialSolarTermsCollectorError,
    parse_download,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v2 import (
    EXPECTED_OFFICIAL_JIE_ROWS,
    _official_evidence,
    _select_provider,
    selected_provider_boundary_checks,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

KST = timezone(timedelta(hours=9))
UTC = timezone.utc


def _download_payload(
    *, missing: set[tuple[int, int]] | None = None, use_24_hour: bool = False
) -> bytes:
    omitted = {(2030, 2)} if missing is None else missing
    lines = [
        "[ 1920년-2100년 24기 입기 시각 ]",
        "역법과 연계된 24기 데이터의 특수성 때문에, 반올림 결과 날짜가 바뀌는 경우 날짜 변동 없이 24시 0분으로 표기됩니다.",
        "이 자료는 최신의 이론, 모델, 상수를 사용하여 계산된 것입니다.",
        "이 자료의 과거 데이터의 불확도는 1초 이내이지만 과거 기록과 다를 수 있습니다.",
        "현재 계산 결과: 1950년 대한, 1월 20일 24시 0분",
        "과거 역서 기록: 1950년 대한, 1월 21일 0시 0분",
    ]
    for year in range(1920, 2101):
        for kind in [23, 24, *range(1, 23)]:
            if (year, kind) in omitted:
                continue
            term_index = (kind + 1) % 24
            month = term_index // 2 + 1
            day = 5 if term_index % 2 == 0 else 20
            hour = 24 if use_24_hour and (year, kind) == (1964, 15) else 12
            minute = 0
            lines.append(f"{kind:2d}, {year:4d}, {month:2d}, {day:2d}, {hour:2d}, {minute:2d}")
    return ("\n".join(lines) + "\n").encode()


def _official_rows_and_records() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
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
            label = local.isoformat(timespec="minutes")
            instant = local.astimezone(UTC).isoformat().replace("+00:00", "Z")
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
                    "astronomy_instant_utc": instant,
                    "skyfield_instant_utc": instant,
                    "astronomy_local_date_fixed_kst": local.date().isoformat(),
                    "skyfield_local_date_fixed_kst": local.date().isoformat(),
                    "astronomy_display_minute_fixed_kst": label,
                    "skyfield_display_minute_fixed_kst": label,
                }
            )
    return official, records


class KasiOfficialSolarTermsTests(unittest.TestCase):
    def test_known_non_jie_omission_preserves_complete_jie_coverage(self) -> None:
        rows, metadata = parse_download(_download_payload())
        self.assertEqual(len(rows), EXPECTED_ROWS - 1)
        self.assertEqual(metadata["jie_rows"], EXPECTED_JIE_ROWS)
        self.assertTrue(metadata["jie_coverage_complete"])
        self.assertEqual(
            metadata["known_upstream_omissions"],
            [{"year": 2030, "source_kind": 2}],
        )
        self.assertTrue(all(row["provider_generated"] is False for row in rows))

    def test_24_hour_label_preserves_printed_date_and_normalizes_instant(self) -> None:
        rows, _ = parse_download(_download_payload(use_24_hour=True))
        row = next(
            value
            for value in rows
            if value["year"] == 1964 and value["source_kind"] == 15
        )
        self.assertEqual(row["printed_local_date"], "1964-09-05")
        self.assertEqual(row["printed_hour"], 24)
        self.assertEqual(row["reference_local_minute"], "1964-09-06T00:00+09:00")

    def test_different_or_jie_omission_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            KasiOfficialSolarTermsCollectorError, "identity"
        ):
            parse_download(_download_payload(missing={(2030, 1)}))

    def test_rounding_notice_tamper_is_rejected(self) -> None:
        payload = _download_payload().replace(b"24\xec\x8b\x9c 0\xeb\xb6\x84", b"23\xec\x8b\x9c 59\xeb\xb6\x84", 1)
        with self.assertRaisesRegex(
            KasiOfficialSolarTermsCollectorError, "고지"
        ):
            parse_download(payload)


class ProviderEligibilityV2Tests(unittest.TestCase):
    def test_only_hard_official_rows_determine_eligibility(self) -> None:
        official, records = _official_rows_and_records()
        astronomy = _official_evidence("astronomy_engine", records, official)
        self.assertEqual(astronomy["official_current_jie_rows"], EXPECTED_OFFICIAL_JIE_ROWS)
        self.assertTrue(astronomy["eligible"])
        records[0]["skyfield_display_minute_fixed_kst"] = "1920-01-05T12:01+09:00"
        skyfield = _official_evidence("skyfield_de440s", records, official)
        self.assertFalse(skyfield["eligible"])
        selection = _select_provider(
            {"astronomy_engine": astronomy, "skyfield_de440s": skyfield}
        )
        self.assertEqual(selection["selected_provider"], "astronomy_engine")

    def test_none_eligible_is_blocked_without_runtime_change(self) -> None:
        selection = _select_provider(
            {
                "astronomy_engine": {"eligible": False},
                "skyfield_de440s": {"eligible": False},
            }
        )
        self.assertEqual(selection["status"], "blocked_no_eligible_provider")
        self.assertIsNone(selection["selected_provider"])
        self.assertFalse(selection["runtime_provider_changed"])

    def test_selected_provider_runs_all_before_exact_after_cases(self) -> None:
        start = datetime(1900, 1, 1, tzinfo=UTC)
        records = [
            {
                "year": 1900 + order // 12,
                "term_index": sorted(JIE_TO_MONTH)[order % 12],
                "astronomy_instant_utc": (
                    start + timedelta(days=order * 30)
                ).isoformat().replace("+00:00", "Z"),
                "skyfield_instant_utc": (
                    start + timedelta(days=order * 30)
                ).isoformat().replace("+00:00", "Z"),
            }
            for order in range(1_800)
        ]
        result = selected_provider_boundary_checks(records, "skyfield_de440s")
        self.assertEqual(result["cases"], 5_400)
        self.assertEqual(result["mismatch_rows"], 0)


class ConformanceV6ContractTests(unittest.TestCase):
    def test_config_hash_chain_and_split_gate_are_valid(self) -> None:
        gate, registry = _validate_configs()
        self.assertTrue(
            gate["data_availability_gate"]["all_accessible_official_data_collected"]
        )
        self.assertFalse(
            gate["provider_eligibility_gate"]["institutional_advisory_can_block"]
        )
        self.assertEqual(
            registry["coverage_policy"]["target_without_official_coverage"][
                "evidence_class"
            ],
            "PROFILE_DETERMINISTIC",
        )

    def test_gate_byte_tamper_is_rejected(self) -> None:
        source = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.4.0.json"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / source.name
            value = json.loads(source.read_text(encoding="utf-8"))
            value["status"] = "tampered"
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with patch(
                "scripts.evaluation.saju_runtime.conformance_v6.GATE_PATH", path
            ), self.assertRaisesRegex(RuntimeConformanceV6Error, "hash chain"):
                _validate_configs()

    def test_openapi_and_official_download_dates_crosscheck(self) -> None:
        openapi = [{"year": 2024, "term_index": 2, "local_date": "2024-02-04"}]
        official = [
            {
                "year": 2024,
                "term_index": 2,
                "printed_local_date": "2024-02-04",
                "reference_local_minute": "2024-02-04T12:00+09:00",
            }
        ]
        result = _crosscheck_kasi_sources(openapi, official)
        self.assertEqual(result["official_download_missing_rows"], 0)
        self.assertEqual(result["normalized_date_mismatches"], 0)

    def test_openapi_normalized_date_accepts_official_24_hour_notation(self) -> None:
        openapi = [{"year": 2011, "term_index": 1, "local_date": "2011-01-21"}]
        official = [
            {
                "year": 2011,
                "term_index": 1,
                "printed_local_date": "2011-01-20",
                "reference_local_minute": "2011-01-21T00:00+09:00",
            }
        ]
        result = _crosscheck_kasi_sources(openapi, official)
        self.assertEqual(result["normalized_date_mismatches"], 0)
        self.assertEqual(result["printed_date_convention_differences"], 1)


class ConformanceV6ArtifactTests(unittest.TestCase):
    def test_committed_report_separates_availability_from_provider_eligibility(
        self,
    ) -> None:
        reports = sorted(REPORT_ROOT.glob("build-*/aggregate.json"))
        self.assertEqual(len(reports), 1)
        report_path = reports[0]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        manifest = json.loads(
            (report_path.parent / "build_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["suite_version"], "saju-runtime-conformance-v6.0.0")
        self.assertEqual(
            report["status"], "data_availability_passed_provider_ineligible"
        )
        self.assertTrue(report["data_availability_gate_passed"])
        self.assertTrue(report["baseline_conformance_gate_passed"])
        self.assertFalse(report["provider_eligibility_gate_passed"])
        self.assertFalse(report["technical_gate_passed"])
        comparison = report["solar_term_provider_comparison"]
        self.assertEqual(
            comparison["time_scale_diagnostic"][
                "raw_profile_display_minute_disagreements"
            ],
            494,
        )
        self.assertEqual(
            comparison["time_scale_diagnostic"][
                "same_tt_profile_display_minute_disagreements"
            ],
            330,
        )
        self.assertEqual(
            comparison["official_hard_evidence"]["astronomy_engine"][
                "official_current_minute_label_mismatches"
            ],
            303,
        )
        self.assertEqual(
            comparison["official_hard_evidence"]["skyfield_de440s"][
                "official_current_minute_label_mismatches"
            ],
            157,
        )
        self.assertIsNone(comparison["selection"]["selected_provider"])
        self.assertFalse(report["baengno_1964_evidence"]["labels_equal"])
        for key in (
            "runtime_approved",
            "release_approval_performed",
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


if __name__ == "__main__":
    unittest.main()
