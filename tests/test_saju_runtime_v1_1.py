# test_saju_runtime_v1_1.py - 계층형 KASI 수집·독립 절입·release 차단 계약을 검증한다.

from __future__ import annotations

import calendar
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from scripts.data.mix20k_v3_runtime_build import (
    Mix20KV31BuildError,
    _replace_foreign,
)
from scripts.data.mix20k_v3_runtime_build import (
    build as build_v31,
)
from scripts.evaluation.saju_runtime.conformance_v3 import (
    RuntimeConformanceV3Error,
    _boundary_checks,
    _load_minute_snapshot,
)
from scripts.evaluation.saju_runtime.jie_crosscheck import compare_jie_boundaries
from scripts.evaluation.saju_runtime.kasi_collector_v1_1 import (
    KasiCollectorV11Error,
    collection_plan,
    load_service_key,
    parse_lunar_month,
    parse_solar_term_year,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    CONFIRMATION as MINUTE_CONFIRMATION,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    KasiMinuteCollectorError,
    parse_calendar_html,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    collect as collect_minute_references,
)
from scripts.runtime.calculation.approved_engine import ApprovedSajuRuntimeEngine
from scripts.runtime.calculation.contracts_v1_1 import (
    validate_contract_registry_v1_1,
)
from scripts.runtime.calculation.solar_terms import (
    JIE_TO_MONTH,
    SOLAR_TERM_NAMES,
    solar_term_instant,
)
from scripts.training.phase5_v3_1_preflight import (
    Phase5V31PreflightError,
)
from scripts.training.phase5_v3_1_preflight import (
    analyze as analyze_v31,
)


def _xml(items: list[str]) -> bytes:
    return (
        "<response><header><resultCode>00</resultCode><resultMsg>OK</resultMsg>"
        "</header><body><items>"
        + "".join(f"<item>{item}</item>" for item in items)
        + f"</items><totalCount>{len(items)}</totalCount></body></response>"
    ).encode()


def _calendar_html(year: int, *, minute_offset: int = 0) -> bytes:
    rows = []
    for index, name in enumerate(SOLAR_TERM_NAMES):
        month = index // 2 + 1
        minute = (index + minute_offset) % 60
        rows.append(
            f"<tr><td>{name}</td><td>{month}</td><td>1</td>"
            f"<td>12</td><td>{minute}</td></tr>"
        )
    return (
        f"<html><body><h2>{year}년 달력자료</h2>"
        "<p>이 자료는 공식 발표 자료가 아닙니다.</p><h2>24절기</h2>"
        f"<table>{''.join(rows)}</table></body></html>"
    ).encode()


class KasiCollectorV11Test(unittest.TestCase):
    def test_plan_uses_monthly_and_yearly_calls_under_quota(self) -> None:
        plan = collection_plan()
        self.assertEqual(plan["requests"]["lunar_months"], 1800)
        self.assertEqual(plan["requests"]["solar_term_years"], 150)
        self.assertEqual(plan["requests"]["total"], 1950)
        self.assertFalse(plan["credential"]["value_exposed"])

    def test_lunar_month_parser_accepts_korean_and_matching_hanja(self) -> None:
        year, month = 2024, 1
        items = []
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            items.append(
                f"<solYear>{year}</solYear><solMonth>{month}</solMonth>"
                f"<solDay>{day}</solDay><lunYear>2023</lunYear>"
                f"<lunMonth>12</lunMonth><lunDay>{min(day, 30)}</lunDay>"
                "<lunLeapmonth>평</lunLeapmonth><lunSecha>계묘(癸卯)</lunSecha>"
                "<lunWolgeon>을축(乙丑)</lunWolgeon>"
                "<lunIljin>갑자(甲子)</lunIljin>"
            )
        rows = parse_lunar_month(_xml(items), year, month)
        self.assertEqual(len(rows), 31)
        self.assertEqual(rows[0]["solar_date"], "2024-01-01")
        self.assertEqual(rows[0]["day_ganzhi"], "甲子")

    def test_lunar_month_parser_rejects_hanja_disagreement(self) -> None:
        year, month = 2023, 2
        items = []
        for day in range(1, 29):
            items.append(
                f"<solYear>{year}</solYear><solMonth>{month}</solMonth>"
                f"<solDay>{day}</solDay><lunYear>2023</lunYear>"
                f"<lunMonth>1</lunMonth><lunDay>{day}</lunDay>"
                "<lunLeapmonth>평</lunLeapmonth><lunIljin>갑자(乙丑)</lunIljin>"
            )
        with self.assertRaises(KasiCollectorV11Error):
            parse_lunar_month(_xml(items), year, month)

    def test_solar_term_year_parser_requires_all_24_terms(self) -> None:
        items = [
            f"<dateName>{name}</dateName><locdate>2024{index // 2 + 1:02d}01</locdate>"
            for index, name in enumerate(SOLAR_TERM_NAMES)
        ]
        rows = parse_solar_term_year(_xml(items), 2024)
        self.assertEqual(len(rows), 24)
        self.assertEqual([row["term_index"] for row in rows], list(range(24)))
        malformed = list(items)
        malformed[0] = malformed[0].replace("20240101", "202401010")
        with self.assertRaisesRegex(KasiCollectorV11Error, "YYYYMMDD"):
            parse_solar_term_year(_xml(malformed), 2024)

    def test_service_key_requires_0600_and_rejects_dual_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key_path = Path(temporary) / "key"
            key_path.write_text("decoded-key\n", encoding="ascii")
            key_path.chmod(0o600)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KASI_SERVICE_KEY", None)
                self.assertEqual(load_service_key(key_path), "decoded-key")
            with (
                mock.patch.dict(os.environ, {"KASI_SERVICE_KEY": "other"}),
                self.assertRaises(KasiCollectorV11Error),
            ):
                load_service_key(key_path)
            key_path.chmod(0o644)
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KASI_SERVICE_KEY", None)
                with self.assertRaises(KasiCollectorV11Error):
                    load_service_key(key_path)


class KasiMinuteCollectorV11Test(unittest.TestCase):
    def test_calendar_parser_extracts_only_twelve_jie_rows(self) -> None:
        rows = parse_calendar_html(_calendar_html(2027), 2027)
        self.assertEqual(len(rows), 12)
        self.assertEqual({row["term_index"] for row in rows}, set(JIE_TO_MONTH))
        self.assertTrue(
            all(row["reference_local_minute"].endswith("+09:00") for row in rows)
        )
        self.assertTrue(all(row["generated_value"] is False for row in rows))

    def test_minute_collection_is_immutable_and_source_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            raw_root = repository / "raw"
            output = raw_root / "minute-references"
            fetch = lambda year: _calendar_html(year)
            with (
                mock.patch(
                    "scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1.REPO_ROOT",
                    repository,
                ),
                mock.patch(
                    "scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1.ALLOWED_ROOT",
                    raw_root,
                ),
            ):
                snapshot, manifest = collect_minute_references(
                    output,
                    confirmation=MINUTE_CONFIRMATION,
                    fetch=fetch,
                )
                self.assertEqual(manifest["rows"], 84)
                self.assertEqual(len(snapshot.read_text(encoding="utf-8").splitlines()), 84)
                repeated, _ = collect_minute_references(
                    output,
                    confirmation=MINUTE_CONFIRMATION,
                    fetch=fetch,
                )
                self.assertEqual(repeated, snapshot)
                with self.assertRaises(KasiMinuteCollectorError):
                    collect_minute_references(
                        output,
                        confirmation=MINUTE_CONFIRMATION,
                        fetch=lambda year: _calendar_html(year, minute_offset=1),
                    )
                loaded, identity = _load_minute_snapshot(snapshot)
                self.assertEqual(len(loaded), 84)
                self.assertTrue(identity["complete"])
                (output / "kasi_calendar_data_2021.html").write_bytes(
                    _calendar_html(2021, minute_offset=1)
                )
                with self.assertRaises(RuntimeConformanceV3Error):
                    _load_minute_snapshot(snapshot)


class RuntimeV11ContractTest(unittest.TestCase):
    def test_static_registry_is_valid_but_not_an_approval(self) -> None:
        registry = validate_contract_registry_v1_1()
        self.assertEqual(
            registry["status"], "tiered_runtime_gate_release_pending"
        )
        engine = ApprovedSajuRuntimeEngine(enable_approved_runtime=True)
        result = engine.calculate_chart({})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["code"], "RUNTIME_RELEASE_REQUIRED")
        self.assertIsNone(result["fact_authority"])

    def test_all_boundary_before_exact_after_cases_match_profile(self) -> None:
        result = _boundary_checks()
        self.assertEqual(result["cases"], 5400)
        self.assertEqual(result["mismatch_rows"], 0)

    @mock.patch(
        "scripts.evaluation.saju_runtime.jie_crosscheck._dependency_versions",
        return_value={"skyfield": "1.55", "jplephem": "2.24", "numpy": "2.2.6"},
    )
    @mock.patch(
        "scripts.evaluation.saju_runtime.jie_crosscheck.validate_ephemeris",
        return_value={
            "filename": "de440s.bsp",
            "bytes": 32_726_016,
            "sha256": "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2",
            "coverage_years": [1849, 2150],
            "local_path_recorded": False,
        },
    )
    def test_independent_date_disagreement_is_reported_and_officially_adjudicated(
        self, _ephemeris: mock.Mock, _versions: mock.Mock
    ) -> None:
        instants = [
            solar_term_instant(2024, index) + timedelta(seconds=40)
            for index in sorted(JIE_TO_MONTH)
        ]
        with mock.patch(
            "scripts.evaluation.saju_runtime.jie_crosscheck._skyfield_jie_instants",
            return_value=instants,
        ):
            result = compare_jie_boundaries(
                Path("unused-de440s.bsp"), start_year=2024, end_year=2024
            )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["threshold_failures"], 0)
        self.assertEqual(
            result["local_date_adjudicator"], "kasi_24_divisions_openapi"
        )


class RuntimeV31GateOrderingTest(unittest.TestCase):
    def test_foreign_replacement_preserves_prior_multiturn_and_source(self) -> None:
        row = {
            "id": "foreign-fixture",
            "source": "licensed_original_source",
            "messages": [
                {"role": "user", "content": "앞선 일반 질문"},
                {"role": "assistant", "content": "앞선 답변"},
                {"role": "user", "content": "해외 출생 원국을 계산해줘"},
                {"role": "assistant", "content": "tool call"},
            ],
        }
        arguments = {
            "birth_date": "1989-01-05",
            "calendar": "solar",
            "leap_month": None,
            "birth_time": "13:00",
            "time_precision": "exact",
            "time_range": None,
            "birthplace": {
                "country_code": "US",
                "city": "New York",
                "timezone": "America/New_York",
                "longitude": -74.0,
                "latitude": 40.7,
            },
            "gender_for_daeun": "male",
        }
        updated = _replace_foreign(row, arguments, 3)
        self.assertEqual(row["messages"][0]["content"], "앞선 일반 질문")
        self.assertIn("출생정보는 1989-01-05", row["messages"][2]["content"])
        self.assertEqual(row["source"], "licensed_original_source")
        self.assertEqual(updated["birthplace"]["country_code"], "KR")

    @mock.patch("scripts.data.mix20k_v3_runtime_build._load_source")
    def test_dataset_build_rejects_missing_release_before_reading_source(
        self, load_source: mock.Mock
    ) -> None:
        with self.assertRaises(Mix20KV31BuildError):
            build_v31(
                source_build=Path("/not-read/source-build"),
                release_registry=Path("/missing/release.json"),
            )
        load_source.assert_not_called()

    @mock.patch("scripts.training.phase5_v3_1_preflight._verify_build")
    def test_preflight_rejects_missing_release_before_reading_build(
        self, verify_build: mock.Mock
    ) -> None:
        with self.assertRaises(Phase5V31PreflightError):
            analyze_v31(
                build_root=Path("/not-read/v3.1"),
                tokenizer_path=Path("/not-read/model"),
                release_registry=Path("/missing/release.json"),
            )
        verify_build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
