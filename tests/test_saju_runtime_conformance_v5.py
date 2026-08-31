# test_saju_runtime_conformance_v5.py - R4~R5 KASI coverage·1964 판정·provider 선택 차단을 검증한다.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.conformance_v5 import (
    RuntimeConformanceV5Error,
    _private_json,
    _private_jsonl,
    _strict_json,
    _validate_configs,
)
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import display_minute_label
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    KasiAlmanac1964CollectorError,
    _parse_response,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    KasiTermCoverageCollectorError,
    _canonical_jsonl,
    _coverage_ranges,
    _manifest,
    _validate_resume_manifest,
    _validated_scan,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v1 import (
    AstronomyEngineSolarTermProvider,
    SolarTermRequest,
    _select_provider,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import SOLAR_TERM_NAMES

KST = ZoneInfo("Asia/Seoul")
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.3.1"


def _almanac_payload() -> dict[str, object]:
    return {
        "map": {
            "ALMN_ID": "KASI_A188_Z_001",
            "KOR_NM": "역서(曆書)1964년",
            "PBLS_YYYY": "1964",
            "WEB_SRVC_DVSN": "이미지,텍스트",
        },
        "pages": [
            {
                "ALMN_ID": "KASI_A188_Z_001",
                "PAGE_SEQ": 20,
                "ARTL_AFT_FILENM": "00020.jpg",
                "PAGE_CONT": "<p>9 월 소</p><li>백로</li><li>7일</li><li>24시</li><li>00분</li>",
            },
            {
                "ALMN_ID": "KASI_A188_Z_001",
                "PAGE_SEQ": 21,
                "ARTL_AFT_FILENM": "00021.jpg",
                "PAGE_CONT": "<p>계속</p>",
            },
        ],
    }


class KasiTermCoverageV12Tests(unittest.TestCase):
    def test_scan_preserves_zero_row_years_without_provider_fill(self) -> None:
        empty = {
            "schema_version": "1.2.0",
            "year": 1900,
            "item_count": 0,
            "response_sha256": "0" * 64,
            "terms": [],
            "source_id": "kasi_24_divisions_openapi",
        }
        terms = [
            {
                "schema_version": "1.1.0",
                "year": 1901,
                "term_index": index,
                "term_name": name,
                "local_date": f"1901-{index // 2 + 1:02d}-01",
                "reference_precision": "date",
                "source_id": "kasi_24_divisions_openapi",
            }
            for index, name in enumerate(SOLAR_TERM_NAMES)
        ]
        supported = {
            "schema_version": "1.2.0",
            "year": 1901,
            "item_count": 24,
            "response_sha256": "1" * 64,
            "terms": terms,
            "source_id": "kasi_24_divisions_openapi",
        }
        self.assertEqual(_validated_scan([empty, supported]), terms)
        self.assertEqual(
            _coverage_ranges([2000, 2001, 2003]), [[2000, 2001], [2003, 2003]]
        )

    def test_scan_rejects_non_api_term_identity(self) -> None:
        row = {
            "schema_version": "1.2.0",
            "year": 1900,
            "item_count": 24,
            "response_sha256": "0" * 64,
            "terms": [
                {
                    "schema_version": "1.1.0",
                    "year": 1900,
                    "term_index": index,
                    "term_name": name,
                    "local_date": f"1900-{index // 2 + 1:02d}-01",
                    "reference_precision": "date",
                    "source_id": "provider_fill"
                    if index == 3
                    else "kasi_24_divisions_openapi",
                }
                for index, name in enumerate(SOLAR_TERM_NAMES)
            ],
            "source_id": "kasi_24_divisions_openapi",
        }
        with self.assertRaisesRegex(KasiTermCoverageCollectorError, "24절기"):
            _validated_scan([row])

    def test_scan_rejects_non_object_term_without_leaking_exception(self) -> None:
        row = {
            "schema_version": "1.2.0",
            "year": 1900,
            "item_count": 24,
            "response_sha256": "0" * 64,
            "terms": [None] * 24,
            "source_id": "kasi_24_divisions_openapi",
        }
        with self.assertRaisesRegex(KasiTermCoverageCollectorError, "block"):
            _validated_scan([row])

    def test_scan_rejects_non_hex_response_hash(self) -> None:
        row = {
            "schema_version": "1.2.0",
            "year": 1900,
            "item_count": 0,
            "response_sha256": "z" * 64,
            "terms": [],
            "source_id": "kasi_24_divisions_openapi",
        }
        with self.assertRaisesRegex(KasiTermCoverageCollectorError, "scan 행"):
            _validated_scan([row])

    def test_resume_manifest_rejects_hash_tamper(self) -> None:
        row = {
            "schema_version": "1.2.0",
            "year": 1900,
            "item_count": 0,
            "response_sha256": "0" * 64,
            "terms": [],
            "source_id": "kasi_24_divisions_openapi",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_path = root / "scan.jsonl"
            snapshot_path = root / "snapshot.jsonl"
            scan_path.write_bytes(_canonical_jsonl([row]))
            os.chmod(scan_path, 0o600)
            manifest = _manifest(
                scan_path=scan_path,
                snapshot_path=snapshot_path,
                scan_rows=[row],
                terms=[],
                requests_this_run=1,
            )
            _validate_resume_manifest(
                manifest,
                scan_path=scan_path,
                snapshot_path=snapshot_path,
                scan_rows=[row],
                terms=[],
            )
            manifest["scan_sha256"] = "f" * 64
            with self.assertRaisesRegex(
                KasiTermCoverageCollectorError, "resume provenance"
            ):
                _validate_resume_manifest(
                    manifest,
                    scan_path=scan_path,
                    snapshot_path=snapshot_path,
                    scan_rows=[row],
                    terms=[],
                )


class KasiAlmanac1964Tests(unittest.TestCase):
    def test_printed_24_hour_is_normalized_to_next_civil_day(self) -> None:
        payload = json.dumps(_almanac_payload(), ensure_ascii=False).encode()
        _, normalized = _parse_response(payload)
        self.assertEqual(normalized["printed_label"], "1964-09-07 24:00 KST")
        self.assertEqual(
            normalized["normalized_reference_local_minute"],
            "1964-09-08T00:00+09:00",
        )
        self.assertFalse(normalized["subminute_instant_claimed"])

    def test_wrong_printed_minute_is_rejected(self) -> None:
        value = _almanac_payload()
        value["pages"][0]["PAGE_CONT"] = (
            "<p>9 월 소</p><li>백로</li><li>7일</li><li>23시</li><li>59분</li>"
        )
        payload = json.dumps(value, ensure_ascii=False).encode()
        with self.assertRaisesRegex(KasiAlmanac1964CollectorError, "예상"):
            _parse_response(payload)

    def test_duplicate_page_sequence_is_rejected(self) -> None:
        value = _almanac_payload()
        value["pages"].insert(0, dict(value["pages"][0]))
        payload = json.dumps(value, ensure_ascii=False).encode()
        with self.assertRaisesRegex(KasiAlmanac1964CollectorError, "metadata"):
            _parse_response(payload)

    def test_nested_duplicate_json_key_is_rejected(self) -> None:
        payload = json.dumps(
            _almanac_payload(), ensure_ascii=False, separators=(",", ":")
        )
        payload = payload.replace(
            '"ALMN_ID":"KASI_A188_Z_001"',
            '"ALMN_ID":"wrong","ALMN_ID":"KASI_A188_Z_001"',
            1,
        ).encode()
        with self.assertRaisesRegex(KasiAlmanac1964CollectorError, "JSON"):
            _parse_response(payload)


class ConformanceV5StrictInputTests(unittest.TestCase):
    def test_all_json_loaders_reject_nested_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public_path = root / "public.json"
            private_path = root / "private.json"
            jsonl_path = root / "private.jsonl"
            payload = '{"outer":{"value":1,"value":2}}\n'
            public_path.write_text(payload, encoding="utf-8")
            private_path.write_text(payload, encoding="utf-8")
            jsonl_path.write_text(payload, encoding="utf-8")
            os.chmod(private_path, 0o600)
            os.chmod(jsonl_path, 0o600)
            with self.assertRaises(RuntimeConformanceV5Error):
                _strict_json(public_path)
            with self.assertRaises(RuntimeConformanceV5Error):
                _private_json(private_path)
            with self.assertRaises(RuntimeConformanceV5Error):
                _private_jsonl(jsonl_path)

    def test_gate_byte_tamper_is_rejected_even_when_json_is_valid(self) -> None:
        source = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.3.1.json"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / source.name
            value = json.loads(source.read_text(encoding="utf-8"))
            value["status"] = "tampered"
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with patch(
                "scripts.evaluation.saju_runtime.conformance_v5.GATE_PATH", path
            ), self.assertRaisesRegex(RuntimeConformanceV5Error, "hash chain"):
                _validate_configs()


class SolarTermProviderDecisionTests(unittest.TestCase):
    def test_astronomy_1964_baengno_matches_normalized_kasi_minute(self) -> None:
        instant = AstronomyEngineSolarTermProvider().instants(
            [SolarTermRequest(1964, 16)]
        )[0]
        self.assertEqual(instant.astimezone(KST).date().isoformat(), "1964-09-08")
        self.assertEqual(display_minute_label(instant), "1964-09-08T00:00+09:00")

    def test_selection_blocks_none_and_uses_declared_tie_break(self) -> None:
        blocked = _select_provider(
            {
                "astronomy_engine": {"eligible": False},
                "skyfield_de440s": {"eligible": False},
            }
        )
        self.assertEqual(blocked["status"], "blocked_no_eligible_provider")
        self.assertIsNone(blocked["selected_provider"])
        single = _select_provider(
            {
                "astronomy_engine": {"eligible": False},
                "skyfield_de440s": {"eligible": True},
            }
        )
        self.assertEqual(single["selected_provider"], "skyfield_de440s")
        tied = _select_provider(
            {
                "astronomy_engine": {"eligible": True},
                "skyfield_de440s": {"eligible": True},
            }
        )
        self.assertEqual(tied["selected_provider"], "astronomy_engine")
        self.assertTrue(tied["tie_break_applied"])


class ConformanceV5ArtifactTests(unittest.TestCase):
    def test_committed_report_is_blocked_and_never_approves_release_or_training(
        self,
    ) -> None:
        reports = sorted(REPORT_ROOT.glob("build-*/aggregate.json"))
        self.assertEqual(len(reports), 1)
        report_path = reports[0]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        manifest = json.loads(
            (report_path.parent / "build_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["suite_version"], "saju-runtime-conformance-v5.0.1")
        self.assertEqual(
            report["status"], "blocked_official_coverage_and_no_eligible_provider"
        )
        self.assertEqual(
            report["official_kasi_solar_term_coverage"]["supported_year_ranges"],
            [[2000, 2028]],
        )
        self.assertEqual(
            report["official_kasi_1964_baengno"]["printed_label"],
            "1964-09-07 24:00 KST",
        )
        self.assertIsNone(
            report["solar_term_provider_comparison"]["selection"]["selected_provider"]
        )
        for key in (
            "runtime_approved",
            "release_approval_performed",
            "app_binding_performed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
        ):
            self.assertFalse(report[key])
        aggregate = report_path.read_bytes()
        self.assertEqual(
            manifest["artifacts"]["aggregate.json"]["sha256"],
            hashlib.sha256(aggregate).hexdigest(),
        )
        self.assertFalse(manifest["release_approval_performed"])
        self.assertEqual(
            manifest["build_id"],
            "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12],
        )
        self.assertEqual(
            set(manifest["artifacts"]),
            {"aggregate.json", "provider_records.jsonl", "provider_delta_by_year.svg"},
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
            report["solar_term_provider_comparison"]["records_sha256"],
            hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
