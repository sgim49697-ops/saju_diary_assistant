# conformance_v3.py - KASI 계층형 공식 근거와 독립 절입 검증을 fail-closed로 집계한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.evaluation.external_conformance import sha256_file
from scripts.evaluation.saju_runtime.conformance import (
    KASI_FIXTURE,
    POLICY_FIXTURE,
    _host_invariance,
    _kasi_checks,
    _load_kasi_rows,
    _policy_checks,
    _synthetic_invariant_checks,
)
from scripts.evaluation.saju_runtime.jie_crosscheck import (
    CROSSCHECK_VERSION,
    DE440S_SHA256,
    compare_jie_boundaries,
)
from scripts.evaluation.saju_runtime.kasi_collector_v1_1 import (
    COLLECTOR_VERSION,
    LUNAR_ENDPOINT,
    LUNAR_SOURCE_PAGE,
    SOLAR_TERM_ENDPOINT,
    SOLAR_TERM_SOURCE_PAGE,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    COLLECTOR_VERSION as MINUTE_COLLECTOR_VERSION,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    SOURCE_PAGE as CALENDAR_DATA_PAGE,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    SOURCE_TIER as MINUTE_SOURCE_TIER,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    KasiMinuteCollectorError,
    parse_calendar_html,
)
from scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 import (
    source_url as minute_source_url,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_1 import (
    ENGINE_VERSION_V11,
    GATE_V11_PATH,
    REGISTRY_V11_PATH,
    SOURCE_REGISTRY_V11_PATH,
    SUITE_VERSION_V3,
    runtime_source_versions_v1_1,
    validate_contract_registry_v1_1,
)
from scripts.runtime.calculation.engine import SajuRuntimeEngine
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.solar_terms import (
    JIE_TO_MONTH,
    SOLAR_TERM_NAMES,
    saju_year_month,
    solar_term_instant,
)

REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.1.0"
KST = ZoneInfo("Asia/Seoul")
EXPECTED_LUNAR_ROWS = 54_787
EXPECTED_TERM_ROWS = 3_600
EXPECTED_JIE_ROWS = 1_800
EXPECTED_MINUTE_ROWS = 84


class RuntimeConformanceV3Error(RuntimeError):
    """conformance v3 입력·산출물 계약 위반."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConformanceV3Error(f"snapshot이 없거나 symlink입니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise RuntimeConformanceV3Error(
                        f"snapshot에 빈 행이 있습니다: {number}"
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeConformanceV3Error(
                        f"snapshot 행은 object여야 합니다: {number}"
                    )
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceV3Error(f"snapshot을 읽지 못했습니다: {path}") from exc
    return rows


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConformanceV3Error(f"snapshot manifest가 없습니다: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceV3Error("snapshot manifest를 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise RuntimeConformanceV3Error("snapshot manifest 최상위가 object가 아닙니다.")
    return value


def _manifest_identity(
    snapshot: Path,
    manifest: Path,
    *,
    kind: str,
    complete: bool,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "provided": True,
        "complete": complete,
        "sha256": sha256_file(snapshot),
        "manifest_sha256": sha256_file(manifest),
        "private_path_recorded": False,
    }


def _load_lunar_snapshot(
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None:
        rows, old_identity = _load_kasi_rows(None)
        return rows, {
            "kind": "committed_stratified_reference",
            "provided": True,
            "complete": False,
            "sha256": old_identity["sha256"],
            "manifest_sha256": None,
            "supported_rows": len(rows),
            "private_path_recorded": False,
        }
    rows = _load_jsonl(path)
    manifest_path = path.with_name("kasi_lunisolar_manifest.json")
    manifest = _load_manifest(manifest_path)
    source_registry = json.loads(SOURCE_REGISTRY_V11_PATH.read_text(encoding="utf-8"))
    expected_collector_hash = source_registry["sources"]["kasi_lunisolar_openapi"][
        "collector_sha256"
    ]
    if (
        len(rows) != EXPECTED_LUNAR_ROWS
        or manifest.get("status") != "complete"
        or manifest.get("source_kind") != "lunar"
        or manifest.get("source_page") != LUNAR_SOURCE_PAGE
        or manifest.get("endpoint") != LUNAR_ENDPOINT
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != expected_collector_hash
        or manifest.get("start_year") != 1900
        or manifest.get("end_year") != 2049
        or manifest.get("expected_periods") != 1800
        or manifest.get("completed_periods") != 1800
        or manifest.get("expected_rows") != EXPECTED_LUNAR_ROWS
        or manifest.get("rows") != EXPECTED_LUNAR_ROWS
        or manifest.get("snapshot_sha256") != sha256_file(path)
        or manifest.get("credential_value_recorded") is not False
    ):
        raise RuntimeConformanceV3Error("KASI 음양력 snapshot provenance가 다릅니다.")
    expected = date(1900, 1, 1)
    normalized: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        try:
            actual = date.fromisoformat(row["solar_date"])
            lunar = row["lunar_date"]
            lunar_values = [lunar[key] for key in ("year", "month", "day")]
            leap = row["leap_month"]
            ganzhi = row["day_ganzhi"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV3Error(
                f"KASI 음양력 snapshot schema가 다릅니다: {number}"
            ) from exc
        if (
            actual != expected
            or row.get("schema_version") != "1.1.0"
            or row.get("source_id") != "kasi_lunisolar_openapi"
            or any(isinstance(value, bool) or not isinstance(value, int) for value in lunar_values)
            or not isinstance(leap, bool)
            or not isinstance(ganzhi, str)
            or len(ganzhi) != 2
        ):
            raise RuntimeConformanceV3Error(
                f"KASI 음양력 snapshot 값이 다릅니다: {number}"
            )
        normalized.append(
            {
                "solar": [actual.year, actual.month, actual.day],
                "lunar": lunar_values,
                "leap": leap,
                "cn": ["", "", ganzhi],
            }
        )
        expected += timedelta(days=1)
    if expected != date(2050, 1, 1):
        raise RuntimeConformanceV3Error("KASI 음양력 snapshot 날짜 범위가 다릅니다.")
    return normalized, {
        **_manifest_identity(
            path,
            manifest_path,
            kind="private_official_full_lunisolar",
            complete=True,
        ),
        "supported_rows": len(normalized),
    }


def _load_term_snapshot(
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None:
        return [], {
            "kind": "missing_official_24_divisions",
            "provided": False,
            "complete": False,
            "sha256": None,
            "manifest_sha256": None,
            "rows": 0,
            "private_path_recorded": False,
        }
    rows = _load_jsonl(path)
    manifest_path = path.with_name("kasi_solar_terms_manifest.json")
    manifest = _load_manifest(manifest_path)
    source_registry = json.loads(SOURCE_REGISTRY_V11_PATH.read_text(encoding="utf-8"))
    expected_collector_hash = source_registry["sources"][
        "kasi_24_divisions_openapi"
    ]["collector_sha256"]
    if (
        len(rows) != EXPECTED_TERM_ROWS
        or manifest.get("status") != "complete"
        or manifest.get("source_kind") != "solar-terms"
        or manifest.get("source_page") != SOLAR_TERM_SOURCE_PAGE
        or manifest.get("endpoint") != SOLAR_TERM_ENDPOINT
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != expected_collector_hash
        or manifest.get("expected_periods") != 150
        or manifest.get("completed_periods") != 150
        or manifest.get("expected_rows") != EXPECTED_TERM_ROWS
        or manifest.get("rows") != EXPECTED_TERM_ROWS
        or manifest.get("snapshot_sha256") != sha256_file(path)
        or manifest.get("credential_value_recorded") is not False
    ):
        raise RuntimeConformanceV3Error("KASI 24절기 snapshot provenance가 다릅니다.")
    seen: set[tuple[int, int]] = set()
    for number, row in enumerate(rows, 1):
        year = row.get("year")
        index = row.get("term_index")
        name = row.get("term_name")
        try:
            local_date = date.fromisoformat(row["local_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV3Error(
                f"KASI 24절기 snapshot 날짜가 다릅니다: {number}"
            ) from exc
        identity = (year, index)
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1900 <= year <= 2049
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < 24
            or name != SOLAR_TERM_NAMES[index]
            or local_date.year != year
            or identity in seen
            or row.get("source_id") != "kasi_24_divisions_openapi"
            or row.get("reference_precision") != "date"
        ):
            raise RuntimeConformanceV3Error(
                f"KASI 24절기 snapshot 값이 다릅니다: {number}"
            )
        seen.add(identity)
    expected = {(year, index) for year in range(1900, 2050) for index in range(24)}
    if seen != expected:
        raise RuntimeConformanceV3Error("KASI 24절기 snapshot identity가 불완전합니다.")
    return rows, {
        **_manifest_identity(
            path,
            manifest_path,
            kind="private_official_24_divisions_dates",
            complete=True,
        ),
        "rows": len(rows),
    }


def _load_minute_snapshot(
    path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None:
        return [], {
            "kind": "missing_institutional_minute_reference",
            "provided": False,
            "complete": False,
            "sha256": None,
            "manifest_sha256": None,
            "rows": 0,
            "source_tier": MINUTE_SOURCE_TIER,
            "private_path_recorded": False,
        }
    rows = _load_jsonl(path)
    manifest_path = path.with_name("kasi_minute_references_manifest.json")
    manifest = _load_manifest(manifest_path)
    source_registry = json.loads(SOURCE_REGISTRY_V11_PATH.read_text(encoding="utf-8"))
    expected_collector_hash = source_registry["sources"]["kasi_calendar_data"][
        "collector_sha256"
    ]
    if (
        len(rows) != EXPECTED_MINUTE_ROWS
        or manifest.get("status") != "complete"
        or manifest.get("source_page") != CALENDAR_DATA_PAGE
        or manifest.get("source_tier") != MINUTE_SOURCE_TIER
        or manifest.get("collector_version") != MINUTE_COLLECTOR_VERSION
        or manifest.get("collector_sha256") != expected_collector_hash
        or manifest.get("years") != list(range(2021, 2028))
        or manifest.get("rows") != EXPECTED_MINUTE_ROWS
        or manifest.get("snapshot_sha256") != sha256_file(path)
        or manifest.get("generated_values") is not False
        or manifest.get("second_precision_available") is not False
        or not isinstance(manifest.get("source_artifacts"), dict)
        or set(manifest["source_artifacts"]) != {str(year) for year in range(2021, 2028)}
        or manifest.get("source_urls")
        != {str(year): minute_source_url(year) for year in range(2021, 2028)}
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in manifest["source_artifacts"].values()
        )
    ):
        raise RuntimeConformanceV3Error("KASI 분 단위 reference provenance가 다릅니다.")
    reconstructed: list[dict[str, Any]] = []
    for year in range(2021, 2028):
        artifact = path.with_name(f"kasi_calendar_data_{year}.html")
        expected_hash = manifest["source_artifacts"][str(year)]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or sha256_file(artifact) != expected_hash
        ):
            raise RuntimeConformanceV3Error(
                f"KASI {year}년 달력자료 원문 provenance가 다릅니다."
            )
        try:
            parsed = parse_calendar_html(artifact.read_bytes(), year)
        except (OSError, KasiMinuteCollectorError) as exc:
            raise RuntimeConformanceV3Error(
                f"KASI {year}년 달력자료 원문을 재검증하지 못했습니다."
            ) from exc
        for row in parsed:
            row["source_artifact_sha256"] = expected_hash
        reconstructed.extend(parsed)
    if rows != reconstructed:
        raise RuntimeConformanceV3Error(
            "KASI 분 단위 reference가 원문 HTML 재파싱 결과와 다릅니다."
        )
    seen: set[tuple[int, int]] = set()
    for number, row in enumerate(rows, 1):
        year = row.get("year")
        index = row.get("term_index")
        try:
            instant = datetime.fromisoformat(row["reference_local_minute"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV3Error(
                f"KASI 분 단위 reference 시각이 다릅니다: {number}"
            ) from exc
        identity = (year, index)
        if (
            year not in range(2021, 2028)
            or index not in JIE_TO_MONTH
            or row.get("term_name") != SOLAR_TERM_NAMES[index]
            or instant.utcoffset() != timedelta(hours=9)
            or instant.second != 0
            or instant.microsecond != 0
            or identity in seen
            or row.get("source_page") != CALENDAR_DATA_PAGE
            or row.get("source_tier") != MINUTE_SOURCE_TIER
            or row.get("reference_precision") != "displayed_minute"
            or row.get("generated_value") is not False
            or row.get("source_artifact_sha256")
            != manifest["source_artifacts"].get(str(year))
        ):
            raise RuntimeConformanceV3Error(
                f"KASI 분 단위 reference 값이 다릅니다: {number}"
            )
        seen.add(identity)
    expected = {(year, index) for year in range(2021, 2028) for index in JIE_TO_MONTH}
    if seen != expected:
        raise RuntimeConformanceV3Error("KASI 분 단위 reference identity가 불완전합니다.")
    return rows, {
        **_manifest_identity(
            path,
            manifest_path,
            kind="private_institutional_minute_reference",
            complete=True,
        ),
        "rows": len(rows),
        "source_tier": MINUTE_SOURCE_TIER,
    }


def _term_date_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    jie_rows = [row for row in rows if row["term_index"] in JIE_TO_MONTH]
    mismatches: list[dict[str, Any]] = []
    for row in jie_rows:
        actual = solar_term_instant(row["year"], row["term_index"]).astimezone(KST)
        if actual.date().isoformat() != row["local_date"]:
            mismatches.append(
                {
                    "year": row["year"],
                    "term_index": row["term_index"],
                    "category": "runtime_kasi_jie_date_mismatch",
                }
            )
    return {
        "all_term_rows_collected": len(rows),
        "jie_rows_compared": len(jie_rows),
        "runtime_date_mismatches": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_details_truncated": len(mismatches) > 100,
    }


def _minute_checks(
    rows: list[dict[str, Any]], independent_records: list[dict[str, Any]]
) -> dict[str, Any]:
    independent = {
        (row["year"], row["term_index"]): datetime.fromisoformat(
            row["skyfield_instant_utc"].replace("Z", "+00:00")
        )
        for row in independent_records
    }
    runtime_failures = 0
    independent_failures = 0
    maximum_runtime = 0.0
    maximum_independent = 0.0
    for row in rows:
        reference = datetime.fromisoformat(row["reference_local_minute"])
        runtime = solar_term_instant(row["year"], row["term_index"])
        runtime_delta = abs((runtime - reference).total_seconds())
        maximum_runtime = max(maximum_runtime, runtime_delta)
        runtime_failures += runtime_delta > 60.0
        comparator = independent.get((row["year"], row["term_index"]))
        if comparator is None:
            independent_failures += 1
            continue
        independent_delta = abs((comparator - reference).total_seconds())
        maximum_independent = max(maximum_independent, independent_delta)
        independent_failures += independent_delta > 60.0
    return {
        "rows": len(rows),
        "maximum_runtime_delta_seconds": round(maximum_runtime, 6),
        "maximum_independent_delta_seconds": round(maximum_independent, 6),
        "runtime_over_60_seconds": runtime_failures,
        "independent_over_60_seconds_or_missing": independent_failures,
    }


def _boundary_checks() -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    cases = 0
    sorted_jie = sorted(JIE_TO_MONTH)
    for year in range(1900, 2050):
        for index in sorted_jie:
            boundary = solar_term_instant(year, index)
            target_month = JIE_TO_MONTH[index]
            previous_index = 22 if index == 0 else index - 2
            expected_previous_month = JIE_TO_MONTH[previous_index]
            expected_before_year = year - 1 if index in {0, 2} else year
            expected_after_year = year - 1 if index == 0 else year
            for position, probe, expected in (
                (
                    "before",
                    boundary - timedelta(microseconds=1),
                    (expected_before_year, expected_previous_month),
                ),
                ("exact", boundary, (expected_after_year, target_month)),
                (
                    "after",
                    boundary + timedelta(microseconds=1),
                    (expected_after_year, target_month),
                ),
            ):
                actual = saju_year_month(
                    probe,
                    local_calendar_year=probe.astimezone(KST).year,
                )
                cases += 1
                if actual != expected:
                    mismatches.append(
                        {
                            "year": year,
                            "term_index": index,
                            "position": position,
                            "category": "profile_boundary_mismatch",
                        }
                    )
    return {
        "cases": cases,
        "mismatch_rows": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_details_truncated": len(mismatches) > 100,
    }


def _missing_independent() -> dict[str, Any]:
    return {
        "schema_version": "1.1.0",
        "crosscheck_version": CROSSCHECK_VERSION,
        "status": "missing_ephemeris",
        "rows": 0,
        "expected_rows": EXPECTED_JIE_ROWS,
        "maximum_allowed_delta_seconds": 120.0,
        "threshold_failures": None,
        "term_identity_failures": None,
        "chronological_order_failures": None,
        "local_date_mismatches": None,
        "local_date_adjudicator": "kasi_24_divisions_openapi",
        "ephemeris": {
            "sha256": DE440S_SHA256,
            "provided": False,
            "local_path_recorded": False,
        },
        "records": [],
    }


def _safe_output_base(path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPORT_ROOT.resolve().parent)
    except ValueError as exc:
        raise RuntimeConformanceV3Error(
            "conformance v3 보고서가 허용 경로 밖입니다."
        ) from exc
    if resolved != REPORT_ROOT.resolve() or path.is_symlink():
        raise RuntimeConformanceV3Error("conformance v3 output base는 고정 경로여야 합니다.")
    return resolved


def _write_artifacts(report: dict[str, Any], output_base: Path) -> Path:
    core_bytes = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(core_bytes).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    aggregate_hash = hashlib.sha256(aggregate.encode()).hexdigest()
    manifest = {
        "schema_version": "1.1.0",
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v3",
        "aggregate_sha256": aggregate_hash,
        "runtime_gate_passed": report["runtime_gate_passed"],
        "release_registry_creation_allowed": report["runtime_gate_passed"],
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if directory.exists() or directory.is_symlink():
        aggregate_path = directory / "aggregate.json"
        manifest_path = directory / "build_manifest.json"
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or aggregate_path.is_symlink()
            or not aggregate_path.is_file()
            or manifest_path.is_symlink()
            or not manifest_path.is_file()
            or aggregate_path.read_text(encoding="utf-8") != aggregate
            or manifest_path.read_text(encoding="utf-8") != manifest_text
        ):
            raise RuntimeConformanceV3Error(
                "같은 build ID의 기존 conformance v3 내용이 다릅니다."
            )
        return directory
    try:
        directory.mkdir(parents=True, mode=0o755)
        with (directory / "aggregate.json").open("x", encoding="utf-8") as stream:
            stream.write(aggregate)
        with (directory / "build_manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(manifest_text)
    except OSError as exc:
        raise RuntimeConformanceV3Error(
            "conformance v3 build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    lunar_snapshot: Path | None = None,
    solar_term_snapshot: Path | None = None,
    minute_snapshot: Path | None = None,
    ephemeris: Path | None = None,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_1()
    source_versions = runtime_source_versions_v1_1(
        require_runtime_dependencies=True,
        require_validator_dependencies=ephemeris is not None,
    )
    provider = KoreanLunarCalendarProvider()
    engine = SajuRuntimeEngine(
        enable_candidate_runtime=True, calendar_provider=provider
    )
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    term_rows, term_identity = _load_term_snapshot(solar_term_snapshot)
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    lunar = _kasi_checks(provider, lunar_rows)
    term_dates = _term_date_checks(term_rows)
    if ephemeris is None:
        independent = _missing_independent()
    else:
        independent = compare_jie_boundaries(ephemeris, include_records=True)
    independent_records = independent.pop("records", [])
    independent["records_in_report"] = False
    minute = _minute_checks(minute_rows, independent_records)
    boundary = _boundary_checks()
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    gate = json.loads(GATE_V11_PATH.read_text(encoding="utf-8"))
    minimum = gate["minimum_cases"]
    checks = {
        "kasi_lunar_days_complete": lunar["rows"] == minimum["kasi_lunar_days"],
        "kasi_lunar_conversion_mismatch_zero": lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar["day_ganzhi_mismatches"] == 0,
        "kasi_all_solar_term_dates_complete": term_dates["all_term_rows_collected"]
        == minimum["kasi_all_solar_term_dates_collected"],
        "kasi_jie_dates_complete": term_dates["jie_rows_compared"]
        == minimum["kasi_jie_dates_compared"],
        "runtime_kasi_jie_date_mismatch_zero": term_dates[
            "runtime_date_mismatches"
        ]
        == 0,
        "kasi_jie_minute_references_complete": minute["rows"]
        == minimum["kasi_jie_minute_references"],
        "runtime_kasi_minute_within_60_seconds": minute["runtime_over_60_seconds"]
        == 0,
        "independent_kasi_minute_within_60_seconds": minute[
            "independent_over_60_seconds_or_missing"
        ]
        == 0,
        "independent_jie_instants_complete": independent["rows"]
        == minimum["independent_jie_instants"],
        "independent_jie_within_120_seconds": independent["threshold_failures"] == 0,
        "independent_term_identity_zero": independent["term_identity_failures"] == 0,
        "independent_chronological_order_zero": independent[
            "chronological_order_failures"
        ]
        == 0,
        "jie_boundary_cases_complete": boundary["cases"]
        == minimum["jie_boundary_before_at_after"],
        "jie_boundary_mismatch_zero": boundary["mismatch_rows"] == 0,
        "policy_fixture_mismatch_zero": policy["mismatch_rows"] == 0,
        "unknown_range_complete": invariants["unknown_range_cases"]
        >= minimum["unknown_range"],
        "guessed_unknown_hour_zero": invariants["guessed_unknown_hour"] == 0,
        "canonical_hash_vectors_complete": invariants["canonical_hash_vectors"]
        >= minimum["canonical_hash_vectors"],
        "canonical_hash_mismatch_zero": invariants["canonical_hash_mismatches"] == 0,
        "unsupported_foreign_complete": invariants["unsupported_foreign_cases"]
        >= minimum["unsupported_foreign"],
        "unsupported_foreign_failure_zero": invariants[
            "unsupported_foreign_failures"
        ]
        == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "source_version_id_failure_zero": invariants["source_version_id_failures"] == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
        "unclassified_mismatch_zero": all(
            item.get("category")
            for item in [*term_dates["mismatches"], *boundary["mismatches"], *policy["mismatches"]]
        ),
    }
    blocking_reasons = sorted(key for key, value in checks.items() if not value)
    gate_passed = not blocking_reasons
    if not lunar_identity["complete"] or not term_identity["complete"] or not minute_identity["complete"]:
        status = "blocked_missing_official_tiered_snapshots"
    elif independent["rows"] != EXPECTED_JIE_ROWS:
        status = "blocked_missing_independent_ephemeris"
    elif not gate_passed:
        status = "blocked_conformance_failures"
    else:
        status = "passed"
    implementation_paths = [
        "scripts/runtime/calculation/canonical.py",
        "scripts/runtime/calculation/contracts.py",
        "scripts/runtime/calculation/contracts_v1_1.py",
        "scripts/runtime/calculation/timezone_resolver.py",
        "scripts/runtime/calculation/calendar_provider.py",
        "scripts/runtime/calculation/normalize.py",
        "scripts/runtime/calculation/solar_terms.py",
        "scripts/runtime/calculation/facts.py",
        "scripts/runtime/calculation/engine.py",
        "scripts/runtime/calculation/approved_engine.py",
        "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/kasi_minute_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/jie_crosscheck.py",
        "scripts/evaluation/saju_runtime/conformance_v3.py",
    ]
    report = {
        "schema_version": "1.1.0",
        "suite_version": SUITE_VERSION_V3,
        "profile_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V11,
        "status": status,
        "source_versions": source_versions,
        "inputs": {
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_24_divisions": term_identity,
                "kasi_minute_reference": minute_identity,
            },
            "public_fallback_fixture": {
                "path": str(KASI_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(KASI_FIXTURE),
            },
            "policy_fixture": {
                "path": str(POLICY_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(POLICY_FIXTURE),
            },
            "runtime_registry_sha256": sha256_file(REGISTRY_V11_PATH),
            "gate_sha256": sha256_file(GATE_V11_PATH),
            "implementation_sha256": {
                relative: sha256_file(REPO_ROOT / relative)
                for relative in implementation_paths
            },
        },
        "official_kasi_lunisolar": lunar,
        "official_kasi_solar_term_dates": term_dates,
        "institutional_kasi_minute_reference": minute,
        "independent_jie_crosscheck": independent,
        "profile_boundary_checks": boundary,
        "policy_comparison": policy,
        "synthetic_invariants": {
            key: value for key, value in invariants.items() if key != "sample_outputs"
        },
        "host_invariance": host,
        "gate_checks": checks,
        "blocking_reasons": blocking_reasons,
        "runtime_gate_passed": gate_passed,
        "release_registry_creation_allowed": gate_passed,
        "runtime_feature_flag_default": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
    }
    directory = _write_artifacts(report, output_base)
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v3")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--kasi-lunar-snapshot", type=Path)
    parser.add_argument("--kasi-solar-term-snapshot", type=Path)
    parser.add_argument("--kasi-minute-snapshot", type=Path)
    parser.add_argument("--ephemeris", type=Path)
    parser.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            lunar_snapshot=args.kasi_lunar_snapshot,
            solar_term_snapshot=args.kasi_solar_term_snapshot,
            minute_snapshot=args.kasi_minute_snapshot,
            ephemeris=args.ephemeris,
            output_base=args.output_base,
        )
    except (RuntimeConformanceV3Error, RuntimeCalculationError) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "runtime_gate_passed": report["runtime_gate_passed"],
                "release_registry_creation_allowed": report[
                    "release_registry_creation_allowed"
                ],
                "output": str(directory),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
