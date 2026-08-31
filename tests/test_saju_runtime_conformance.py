# test_saju_runtime_conformance.py - KASI 수집 계약과 conformance 보고서 차단 상태를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from scripts.evaluation.saju_runtime.conformance import (
    RuntimeConformanceError,
    _load_kasi_rows,
    run_conformance,
)
from scripts.evaluation.saju_runtime.kasi_collector import (
    COLLECTOR_VERSION,
    CONFIRMATION,
    ENDPOINT,
    SOURCE_PAGE,
    KasiCollectorError,
    _load_resume_manifest,
    _parse_response,
    _RejectRedirect,
    _sha256,
    collect,
    collection_plan,
)


class KasiCollectorTests(unittest.TestCase):
    def test_resume_manifest_is_bound_to_snapshot_and_collector(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "kasi_lunisolar.jsonl"
            manifest_path = root / "collection_manifest.json"
            snapshot.write_text('{"solar_date":"1900-01-01"}\n', encoding="utf-8")
            manifest = {
                "source": SOURCE_PAGE,
                "endpoint": ENDPOINT,
                "collector_version": COLLECTOR_VERSION,
                "collector_sha256": _sha256(
                    Path("scripts/evaluation/saju_runtime/kasi_collector.py")
                ),
                "start_date": "1900-01-01",
                "end_date": "2049-12-31",
                "rows": 1,
                "snapshot_sha256": _sha256(snapshot),
                "credential_value_recorded": False,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = _load_resume_manifest(
                manifest_path,
                snapshot,
                start=date(1900, 1, 1),
                end=date(2049, 12, 31),
            )
            self.assertEqual(loaded["rows"], 1)
            manifest["snapshot_sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(KasiCollectorError, "provenance"):
                _load_resume_manifest(
                    manifest_path,
                    snapshot,
                    start=date(1900, 1, 1),
                    end=date(2049, 12, 31),
                )

    def test_redirect_is_rejected_before_service_key_can_leave_origin(self) -> None:
        with self.assertRaisesRegex(KasiCollectorError, "redirect"):
            _RejectRedirect().redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://example.invalid/steal",
            )

    def test_full_range_plan_has_exact_day_count_without_network(self) -> None:
        plan = collection_plan(date(1900, 1, 1), date(2049, 12, 31))
        self.assertEqual(plan["expected_rows"], 54_787)
        self.assertEqual(plan["development_quota_batches_at_10000"], 6)
        self.assertEqual(plan["collector_version"], "kasi-lunisolar-collector-v1.0.0")
        self.assertFalse(plan["credential_value_exposed"])

    def test_official_xml_is_normalized_without_service_key(self) -> None:
        payload = b"""<?xml version='1.0' encoding='UTF-8'?>
        <response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
        <body><items><item><solYear>1989</solYear><solMonth>01</solMonth><solDay>05</solDay>
        <lunYear>1988</lunYear><lunMonth>11</lunMonth><lunDay>28</lunDay>
        <lunLeapmonth>\xed\x8f\x89</lunLeapmonth><lunSecha>\xeb\xac\xb4\xec\xa7\x84</lunSecha>
        <lunWolgeon>\xea\xb0\x91\xec\x9e\x90</lunWolgeon><lunIljin>\xec\x9d\x84\xec\xb6\x95</lunIljin>
        </item></items></body></response>"""
        row = _parse_response(payload, date(1989, 1, 5))
        self.assertEqual(row["day_ganzhi"], "乙丑")
        self.assertEqual(row["lunar_date"], {"year": 1988, "month": 11, "day": 28})
        self.assertNotIn("serviceKey", json.dumps(row))

    def test_official_xml_rejects_invalid_numeric_fields_with_domain_error(
        self,
    ) -> None:
        payload = b"""<response><header><resultCode>00</resultCode></header>
        <body><items><item><solYear>invalid</solYear><solMonth>01</solMonth>
        <solDay>01</solDay></item></items></body></response>"""
        with self.assertRaisesRegex(KasiCollectorError, "정수"):
            _parse_response(payload, date(1989, 1, 1))

    def test_collection_requires_confirmation_and_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary)
            with self.assertRaises(KasiCollectorError):
                collect(
                    start=date(2000, 1, 1),
                    end=date(2000, 1, 1),
                    output=outside,
                    max_requests=1,
                    request_interval=0,
                    timeout=1,
                    confirmation=CONFIRMATION,
                )
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(KasiCollectorError, "환경변수"),
        ):
            collect(
                start=date(2000, 1, 1),
                end=date(2000, 1, 1),
                output=Path("data/raw/saju_runtime/kasi_lunisolar/test"),
                max_requests=1,
                request_interval=0,
                timeout=1,
                confirmation=CONFIRMATION,
            )


class RuntimeConformanceTests(unittest.TestCase):
    def test_full_snapshot_rejects_missing_required_field_even_with_extra_field(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "invalid.jsonl"
            snapshot.write_text(
                json.dumps(
                    {
                        "solar_date": "1900-01-01",
                        "lunar_date": {"year": 1899, "month": 12, "day": 1},
                        "leap_month": False,
                        "unexpected": "day_ganzhi 누락을 가리면 안 됨",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeConformanceError, "schema"):
                _load_kasi_rows(snapshot)

    def test_committed_fixture_passes_available_checks_but_gate_stays_blocked(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            dir="data/reports/saju_runtime_conformance"
        ) as temporary:
            report, output = run_conformance(output_base=Path(temporary))
            self.assertTrue(output.is_dir())
            self.assertEqual(report["official_kasi"]["rows"], 63)
            self.assertEqual(report["official_kasi"]["official_hard_mismatches"], 0)
            self.assertEqual(
                report["coverage"]["kasi_supported_solar_days"],
                {"observed": 63, "required": 54_787},
            )
            self.assertEqual(
                report["coverage"]["jie_boundary_before_at_after"],
                {"observed": 2, "required": 5_400},
            )
            self.assertEqual(report["policy_comparison"]["passed_rows"], 16)
            self.assertEqual(report["policy_comparison"]["mismatch_rows"], 0)
            self.assertEqual(report["synthetic_invariants"]["unknown_range_cases"], 500)
            self.assertFalse(report["runtime_gate_passed"])
            self.assertFalse(report["gate_checks"]["kasi_supported_solar_days"])
            self.assertFalse(report["gate_checks"]["jie_boundary_before_at_after"])
            manifest = json.loads(
                (output / "build_manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["training_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
