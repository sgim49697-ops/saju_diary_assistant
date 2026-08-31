# solar_term_provider_comparison_v2.py - 공식 KASI 분 라벨과 동일 TT 축으로 절입 provider를 비교한다.

from __future__ import annotations

import hashlib
import math
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from html import escape
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v1 import (
    MAXIMUM_REGRESSION_DELTA_SECONDS,
    AstronomyEngineSolarTermProvider,
    SkyfieldDe440sSolarTermProvider,
    SolarTermProviderComparisonError,
    SolarTermRequest,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH, SOLAR_TERM_NAMES

COMPARISON_VERSION = "saju-solar-term-provider-comparison-v2.0.0"
START_YEAR = 1900
END_YEAR = 2049
OFFICIAL_START_YEAR = 1920
EXPECTED_JIE_ROWS = (END_YEAR - START_YEAR + 1) * 12
EXPECTED_OFFICIAL_JIE_ROWS = (END_YEAR - OFFICIAL_START_YEAR + 1) * 12
EXPECTED_ADVISORY_MINUTE_ROWS = 84
UTC = timezone.utc
FIXED_KST = timezone(timedelta(hours=9))
PROFILE_TIMEZONE = ZoneInfo("Asia/Seoul")


def _requests() -> list[SolarTermRequest]:
    return [
        SolarTermRequest(year, term_index)
        for year in range(START_YEAR, END_YEAR + 1)
        for term_index in sorted(JIE_TO_MONTH)
    ]


def _distribution(values: list[float]) -> dict[str, int | float]:
    absolute = sorted(abs(value) for value in values)
    if not absolute:
        return {
            "rows": 0,
            "mean_absolute_seconds": 0.0,
            "p99_absolute_seconds": 0.0,
            "maximum_absolute_seconds": 0.0,
        }
    index = max(0, math.ceil(len(absolute) * 0.99) - 1)
    return {
        "rows": len(absolute),
        "mean_absolute_seconds": round(fmean(absolute), 6),
        "p99_absolute_seconds": round(absolute[index], 6),
        "maximum_absolute_seconds": round(absolute[-1], 6),
    }


def _display_minute(value: datetime, target_timezone) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SolarTermProviderComparisonError(
            "분 표기 변환에는 timezone-aware datetime이 필요합니다."
        )
    local = value.astimezone(target_timezone)
    base = local.replace(second=0, microsecond=0)
    elapsed = local.second + local.microsecond / 1_000_000
    rounded = base + (timedelta(minutes=1) if elapsed >= 30.0 else timedelta())
    return rounded.isoformat(timespec="minutes")


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise SolarTermProviderComparisonError("provider UTC instant 형식이 다릅니다.")
    return parsed


def _same_scale_diagnostic(
    astronomy_instants: list[datetime], skyfield_instants: list[datetime]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import astronomy
        from skyfield.api import load
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "동일 TT 축 진단 의존성을 import하지 못했습니다."
        ) from exc
    astronomy_tt_jd: list[float] = []
    for value in astronomy_instants:
        utc = value.astimezone(UTC)
        second = utc.second + utc.microsecond / 1_000_000
        astronomy_time = astronomy.Time.Make(
            utc.year, utc.month, utc.day, utc.hour, utc.minute, second
        )
        astronomy_tt_jd.append(float(astronomy_time.tt) + 2_451_545.0)
    timescale = load.timescale(builtin=True)
    skyfield_times = timescale.from_datetimes(skyfield_instants)
    skyfield_tt_jd = [float(value) for value in skyfield_times.tt]
    projected = timescale.tt_jd(astronomy_tt_jd).utc_datetime()
    projected_instants = [value.replace(tzinfo=UTC) for value in projected]
    rows: list[dict[str, Any]] = []
    raw_values: list[float] = []
    same_scale_values: list[float] = []
    mapping_values: list[float] = []
    raw_display_disagreements = 0
    projected_display_disagreements = 0
    astronomy_labels_changed = 0
    for astronomy_instant, skyfield_instant, astronomy_tt, skyfield_tt, projected_utc in zip(
        astronomy_instants,
        skyfield_instants,
        astronomy_tt_jd,
        skyfield_tt_jd,
        projected_instants,
        strict=True,
    ):
        raw_delta = (astronomy_instant - skyfield_instant).total_seconds()
        same_scale_delta = (astronomy_tt - skyfield_tt) * 86_400.0
        mapping_component = raw_delta - same_scale_delta
        raw_astronomy_label = _display_minute(astronomy_instant, PROFILE_TIMEZONE)
        projected_astronomy_label = _display_minute(projected_utc, PROFILE_TIMEZONE)
        skyfield_label = _display_minute(skyfield_instant, PROFILE_TIMEZONE)
        raw_display_disagreements += raw_astronomy_label != skyfield_label
        projected_display_disagreements += projected_astronomy_label != skyfield_label
        astronomy_labels_changed += raw_astronomy_label != projected_astronomy_label
        raw_values.append(raw_delta)
        same_scale_values.append(same_scale_delta)
        mapping_values.append(mapping_component)
        rows.append(
            {
                "astronomy_tt_jd": round(astronomy_tt, 12),
                "skyfield_tt_jd": round(skyfield_tt, 12),
                "astronomy_reprojected_with_skyfield_utc": _utc_text(projected_utc),
                "same_scale_tt_root_delta_seconds": round(same_scale_delta, 6),
                "utc_mapping_component_seconds": round(mapping_component, 6),
                "astronomy_reprojected_display_minute_profile": projected_astronomy_label,
            }
        )
    raw = _distribution(raw_values)
    same_scale = _distribution(same_scale_values)
    raw_mean = float(raw["mean_absolute_seconds"])
    retained = (
        0.0
        if raw_mean == 0.0
        else float(same_scale["mean_absolute_seconds"]) / raw_mean * 100.0
    )
    diagnostic = {
        "status": "not_delta_t_only",
        "gate_role": "diagnostic_only",
        "method": "compare_provider_roots_on_tt_then_reproject_astronomy_tt_with_skyfield_utc_mapping",
        "raw_utc_delta": raw,
        "same_scale_tt_root_delta": same_scale,
        "utc_mapping_component": _distribution(mapping_values),
        "same_scale_mean_absolute_retained_percent": round(retained, 6),
        "raw_profile_display_minute_disagreements": raw_display_disagreements,
        "same_tt_profile_display_minute_disagreements": projected_display_disagreements,
        "astronomy_profile_labels_changed_by_utc_mapping": astronomy_labels_changed,
        "conclusion": "utc_mapping_contributes_but_astronomical_root_difference_remains",
        "limitation": "diagnostic_reprojection_is_not_an_official_accuracy_oracle",
    }
    return rows, diagnostic


def _official_evidence(
    provider_key: str,
    records: list[dict[str, Any]],
    official_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    prefix = "astronomy" if provider_key == "astronomy_engine" else "skyfield"
    record_map = {(row["year"], row["term_index"]): row for row in records}
    references = [
        row
        for row in official_rows
        if OFFICIAL_START_YEAR <= row.get("year", 0) <= END_YEAR
        and row.get("term_index") in JIE_TO_MONTH
    ]
    identities = [(row["year"], row["term_index"]) for row in references]
    expected_identities = [
        (year, term_index)
        for year in range(OFFICIAL_START_YEAR, END_YEAR + 1)
        for term_index in sorted(JIE_TO_MONTH)
    ]
    complete = sorted(identities) == expected_identities and len(set(identities)) == len(
        identities
    )
    date_mismatches: list[dict[str, Any]] = []
    minute_mismatches: list[dict[str, Any]] = []
    residuals: list[float] = []
    for reference in references:
        identity = (reference["year"], reference["term_index"])
        record = record_map.get(identity)
        expected_minute = reference["reference_local_minute"]
        expected_date = reference["printed_local_date"]
        if record is None:
            actual_date = None
            actual_minute = None
        else:
            actual_date = record[f"{prefix}_local_date_fixed_kst"]
            actual_minute = record[f"{prefix}_display_minute_fixed_kst"]
            residuals.append(
                (
                    _parse_utc(record[f"{prefix}_instant_utc"])
                    - datetime.fromisoformat(expected_minute)
                ).total_seconds()
            )
        if actual_date != expected_date:
            date_mismatches.append(
                {
                    "year": identity[0],
                    "term_index": identity[1],
                    "expected": expected_date,
                    "actual": actual_date,
                }
            )
        if actual_minute != expected_minute:
            minute_mismatches.append(
                {
                    "year": identity[0],
                    "term_index": identity[1],
                    "expected": expected_minute,
                    "actual": actual_minute,
                }
            )
    checks = {
        "available_official_current_jie_complete": complete
        and len(references) == EXPECTED_OFFICIAL_JIE_ROWS,
        "available_official_current_date_mismatch_zero": not date_mismatches,
        "available_official_current_minute_label_mismatch_zero": not minute_mismatches,
    }
    current_1964 = next(
        (
            reference
            for reference in references
            if reference["year"] == 1964 and reference["term_index"] == 16
        ),
        None,
    )
    if current_1964 is None:
        raise SolarTermProviderComparisonError(
            "KASI 현재 계산의 1964년 백로 행이 없습니다."
        )
    current_record = record_map[(1964, 16)]
    return {
        "provider": provider_key,
        "evidence_class": "SOURCE_HARD_FACT",
        "hard_blocking": True,
        "official_current_jie_rows": len(references),
        "official_current_jie_expected_rows": EXPECTED_OFFICIAL_JIE_ROWS,
        "official_current_date_mismatches": len(date_mismatches),
        "official_current_date_mismatch_rows": date_mismatches[:100],
        "official_current_date_mismatch_details_truncated": len(date_mismatches) > 100,
        "official_current_minute_label_mismatches": len(minute_mismatches),
        "official_current_minute_mismatch_rows": minute_mismatches[:100],
        "official_current_minute_mismatch_details_truncated": len(minute_mismatches)
        > 100,
        "published_minute_center_residual": _distribution(residuals),
        "published_minute_center_is_exact_instant": False,
        "current_calculation_1964_baengno": {
            "reference_local_minute": current_1964["reference_local_minute"],
            "provider_display_minute_fixed_kst": current_record[
                f"{prefix}_display_minute_fixed_kst"
            ],
            "display_minute_match": current_record[
                f"{prefix}_display_minute_fixed_kst"
            ]
            == current_1964["reference_local_minute"],
        },
        "eligibility_checks": checks,
        "eligible": all(checks.values()),
        "blocking_reasons": sorted(key for key, value in checks.items() if not value),
    }


def _advisory_evidence(
    records: list[dict[str, Any]],
    minute_rows: list[dict[str, Any]],
    historical_almanac: dict[str, Any],
) -> dict[str, Any]:
    record_map = {(row["year"], row["term_index"]): row for row in records}
    providers: dict[str, Any] = {}
    for provider_key, prefix in (
        ("astronomy_engine", "astronomy"),
        ("skyfield_de440s", "skyfield"),
    ):
        minute_mismatches: list[dict[str, Any]] = []
        for reference in minute_rows:
            record = record_map.get((reference["year"], reference["term_index"]))
            expected = reference["reference_local_minute"]
            actual = (
                None
                if record is None
                else record[f"{prefix}_display_minute_fixed_kst"]
            )
            if actual != expected:
                minute_mismatches.append(
                    {
                        "year": reference["year"],
                        "term_index": reference["term_index"],
                        "expected": expected,
                        "actual": actual,
                    }
                )
        almanac_record = record_map[(1964, 16)]
        historical_reference = historical_almanac[
            "normalized_reference_local_minute"
        ]
        providers[provider_key] = {
            "nonformal_calendar_rows": len(minute_rows),
            "nonformal_calendar_expected_rows": EXPECTED_ADVISORY_MINUTE_ROWS,
            "nonformal_calendar_minute_label_mismatches": len(minute_mismatches),
            "nonformal_calendar_minute_mismatch_rows": minute_mismatches[:100],
            "historical_1964_printed_label": historical_almanac["printed_label"],
            "historical_1964_normalized_reference": historical_reference,
            "historical_1964_display_minute_match": almanac_record[
                f"{prefix}_display_minute_fixed_kst"
            ]
            == historical_reference,
        }
    return {
        "evidence_class": "INSTITUTIONAL_ADVISORY",
        "hard_blocking": False,
        "reason": "calendarData_is_not_formal_and_historic_almanac_may_differ_from_current_kasi_calculation",
        "providers": providers,
    }


def _select_provider(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eligible = [key for key, value in evidence.items() if value["eligible"]]
    if not eligible:
        return {
            "status": "blocked_no_eligible_provider",
            "selected_provider": None,
            "tie_break_applied": False,
            "runtime_provider_changed": False,
        }
    if len(eligible) == 1:
        return {
            "status": "selected_single_eligible_candidate",
            "selected_provider": eligible[0],
            "tie_break_applied": False,
            "runtime_provider_changed": False,
        }
    return {
        "status": "selected_astronomy_engine_by_dependency_tie_break",
        "selected_provider": "astronomy_engine",
        "tie_break_applied": True,
        "tie_break_rule": "retain_current_provider_when_both_pass_identical_gate",
        "runtime_provider_changed": False,
    }


def selected_provider_boundary_checks(
    records: list[dict[str, Any]], selected_provider: str | None
) -> dict[str, Any]:
    expected_cases = EXPECTED_JIE_ROWS * 3
    if selected_provider is None:
        return {
            "status": "not_run_no_selected_provider",
            "cases": 0,
            "expected_cases": expected_cases,
            "mismatch_rows": None,
            "reason": "provider_selection_blocked",
        }
    prefix = {
        "astronomy_engine": "astronomy",
        "skyfield_de440s": "skyfield",
    }.get(selected_provider)
    if prefix is None:
        raise SolarTermProviderComparisonError("알 수 없는 selected provider입니다.")
    instants = [
        _parse_utc(row[f"{prefix}_instant_utc"]) for row in records
    ]
    if len(instants) != EXPECTED_JIE_ROWS or any(
        current >= following for current, following in pairwise(instants)
    ):
        raise SolarTermProviderComparisonError(
            "selected provider boundary 행 수·순서가 다릅니다."
        )
    mismatches: list[dict[str, Any]] = []
    cases = 0
    for order, (row, boundary) in enumerate(zip(records, instants, strict=True)):
        for position, probe, expected_order in (
            ("before", boundary - timedelta(microseconds=1), order - 1),
            ("exact", boundary, order),
            ("after", boundary + timedelta(microseconds=1), order),
        ):
            actual_order = bisect_right(instants, probe) - 1
            cases += 1
            if actual_order != expected_order:
                mismatches.append(
                    {
                        "year": row["year"],
                        "term_index": row["term_index"],
                        "position": position,
                    }
                )
    return {
        "status": "passed" if not mismatches else "failed",
        "provider": selected_provider,
        "scope": "candidate_provider_before_exact_after_assignment",
        "cases": cases,
        "expected_cases": expected_cases,
        "mismatch_rows": len(mismatches),
        "mismatches": mismatches[:100],
        "mismatch_details_truncated": len(mismatches) > 100,
        "runtime_binding_performed": False,
    }


def compare_providers(
    ephemeris_path: Path,
    *,
    official_current_rows: list[dict[str, Any]],
    advisory_minute_rows: list[dict[str, Any]],
    historical_almanac_row: dict[str, Any],
    include_records: bool = False,
) -> dict[str, Any]:
    requests = _requests()
    astronomy_provider = AstronomyEngineSolarTermProvider()
    skyfield_provider = SkyfieldDe440sSolarTermProvider(ephemeris_path)
    astronomy_instants = astronomy_provider.instants(requests)
    skyfield_instants = skyfield_provider.instants(requests)
    time_rows, time_diagnostic = _same_scale_diagnostic(
        astronomy_instants, skyfield_instants
    )
    records: list[dict[str, Any]] = []
    for order, (request, astronomy, skyfield, time_row) in enumerate(
        zip(
            requests,
            astronomy_instants,
            skyfield_instants,
            time_rows,
            strict=True,
        )
    ):
        records.append(
            {
                "order": order,
                "year": request.year,
                "term_index": request.term_index,
                "term_name": SOLAR_TERM_NAMES[request.term_index],
                "evidence_class": (
                    "SOURCE_HARD_FACT"
                    if request.year >= OFFICIAL_START_YEAR
                    else "PROFILE_DETERMINISTIC"
                ),
                "astronomy_instant_utc": _utc_text(astronomy),
                "skyfield_instant_utc": _utc_text(skyfield),
                "delta_seconds": round(
                    (astronomy - skyfield).total_seconds(), 6
                ),
                "astronomy_local_date_profile": astronomy.astimezone(
                    PROFILE_TIMEZONE
                ).date().isoformat(),
                "skyfield_local_date_profile": skyfield.astimezone(
                    PROFILE_TIMEZONE
                ).date().isoformat(),
                "astronomy_display_minute_profile": _display_minute(
                    astronomy, PROFILE_TIMEZONE
                ),
                "skyfield_display_minute_profile": _display_minute(
                    skyfield, PROFILE_TIMEZONE
                ),
                "astronomy_local_date_fixed_kst": astronomy.astimezone(
                    FIXED_KST
                ).date().isoformat(),
                "skyfield_local_date_fixed_kst": skyfield.astimezone(
                    FIXED_KST
                ).date().isoformat(),
                "astronomy_display_minute_fixed_kst": _display_minute(
                    astronomy, FIXED_KST
                ),
                "skyfield_display_minute_fixed_kst": _display_minute(
                    skyfield, FIXED_KST
                ),
                **time_row,
            }
        )
    identity_failures = sum(
        row["term_name"] != SOLAR_TERM_NAMES[row["term_index"]]
        or row["order"] != order
        for order, row in enumerate(records)
    )
    astronomy_order_failures = sum(
        current["astronomy_instant_utc"] >= following["astronomy_instant_utc"]
        for current, following in pairwise(records)
    )
    skyfield_order_failures = sum(
        current["skyfield_instant_utc"] >= following["skyfield_instant_utc"]
        for current, following in pairwise(records)
    )
    deltas = [float(row["delta_seconds"]) for row in records]
    threshold_failures = sum(
        abs(value) > MAXIMUM_REGRESSION_DELTA_SECONDS for value in deltas
    )
    evidence = {
        "astronomy_engine": _official_evidence(
            "astronomy_engine", records, official_current_rows
        ),
        "skyfield_de440s": _official_evidence(
            "skyfield_de440s", records, official_current_rows
        ),
    }
    shared_checks = {
        "provider_rows_complete": len(records) == EXPECTED_JIE_ROWS,
        "term_identity_failure_zero": identity_failures == 0,
        "provider_chronological_order_failure_zero": astronomy_order_failures == 0
        and skyfield_order_failures == 0,
        "fixed_120_second_regression_guard": threshold_failures == 0,
    }
    for provider_evidence in evidence.values():
        provider_evidence["eligibility_checks"].update(shared_checks)
        provider_evidence["eligible"] = all(
            provider_evidence["eligibility_checks"].values()
        )
        provider_evidence["blocking_reasons"] = sorted(
            key
            for key, passed in provider_evidence["eligibility_checks"].items()
            if not passed
        )
    selection = _select_provider(evidence)
    result = {
        "schema_version": "2.0.0",
        "comparison_version": COMPARISON_VERSION,
        "range": {"start_year": START_YEAR, "end_year": END_YEAR},
        "rows": len(records),
        "expected_rows": EXPECTED_JIE_ROWS,
        "providers": {
            "astronomy_engine": astronomy_provider.identity(),
            "skyfield_de440s": skyfield_provider.identity(),
        },
        "evidence_classes": {
            "SOURCE_HARD_FACT": "official_source_rows_that_may_block_provider_eligibility",
            "PROFILE_DETERMINISTIC": "provider_generated_target_rows_without_official_source_coverage",
            "INSTITUTIONAL_ADVISORY": "institutional_but_nonblocking_reference",
        },
        "official_source_values_filled_from_provider": False,
        "target_without_official_rows": {
            "year_ranges": [[1900, 1919]],
            "jie_rows": (OFFICIAL_START_YEAR - START_YEAR) * 12,
            "evidence_class": "PROFILE_DETERMINISTIC",
        },
        "delta_convention": "astronomy_instant_utc_minus_skyfield_instant_utc",
        "delta_distribution": _distribution(deltas),
        "minimum_signed_delta_seconds": round(min(deltas), 6),
        "maximum_signed_delta_seconds": round(max(deltas), 6),
        "time_scale_diagnostic": time_diagnostic,
        "fixed_regression_guard": {
            "maximum_seconds": MAXIMUM_REGRESSION_DELTA_SECONDS,
            "failures": threshold_failures,
            "role": "non_authoritative_regression_guard_not_accuracy_oracle",
        },
        "term_identity_failures": identity_failures,
        "astronomy_chronological_order_failures": astronomy_order_failures,
        "skyfield_chronological_order_failures": skyfield_order_failures,
        "official_hard_evidence": evidence,
        "institutional_advisory_evidence": _advisory_evidence(
            records, advisory_minute_rows, historical_almanac_row
        ),
        "selection": selection,
        "selected_provider_boundary_assignment_checks": selected_provider_boundary_checks(
            records, selection["selected_provider"]
        ),
        "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        "records_in_report": include_records,
    }
    if include_records:
        result["records"] = records
    return result


def records_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in records)


def render_time_scale_scatter_svg(records: list[dict[str, Any]]) -> bytes:
    if not records:
        raise SolarTermProviderComparisonError(
            "빈 절입 기록으로 시간축 산점도를 만들 수 없습니다."
        )
    width, height = 960, 430
    left, right, top, bottom = 70, 30, 45, 60
    minimum_year = min(int(row["year"]) for row in records)
    maximum_year = max(int(row["year"]) for row in records)
    maximum_value = 120.0

    def x(year: int) -> float:
        return left + (year - minimum_year) / (maximum_year - minimum_year) * (
            width - left - right
        )

    def y(value: float) -> float:
        return top + (maximum_value - min(maximum_value, value)) / maximum_value * (
            height - top - bottom
        )

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8"/>',
        '<text x="70" y="23" font-family="sans-serif" font-size="15" fill="#3b3832">Provider 절입 차이: 원 UTC와 동일 TT root (초)</text>',
    ]
    for seconds in range(0, 121, 20):
        line_y = y(float(seconds))
        elements.append(
            f'<line x1="{left}" y1="{line_y:.2f}" x2="{width-right}" y2="{line_y:.2f}" stroke="#ddd5c8" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{left-10}" y="{line_y+4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11" fill="#665f55">{seconds}</text>'
        )
    for year in range(1900, 2050, 25):
        line_x = x(year)
        elements.append(
            f'<text x="{line_x:.2f}" y="{height-27}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#665f55">{year}</text>'
        )
    for row in records:
        year = int(row["year"])
        raw = abs(float(row["delta_seconds"]))
        same = abs(float(row["same_scale_tt_root_delta_seconds"]))
        title = escape(f'{year} {row["term_name"]}')
        elements.append(
            f'<circle cx="{x(year):.2f}" cy="{y(raw):.2f}" r="1.55" fill="#9d4f3f" fill-opacity="0.42"><title>{title} 원 UTC {raw:.6f}s</title></circle>'
        )
        elements.append(
            f'<circle cx="{x(year):.2f}" cy="{y(same):.2f}" r="1.35" fill="#2f6f8f" fill-opacity="0.48"><title>{title} 동일 TT {same:.6f}s</title></circle>'
        )
    elements.extend(
        [
            '<circle cx="700" cy="30" r="4" fill="#9d4f3f"/><text x="710" y="34" font-family="sans-serif" font-size="11" fill="#665f55">원 UTC 차이</text>',
            '<circle cx="800" cy="30" r="4" fill="#2f6f8f"/><text x="810" y="34" font-family="sans-serif" font-size="11" fill="#665f55">동일 TT root 차이</text>',
            "</svg>",
        ]
    )
    return ("\n".join(elements) + "\n").encode("utf-8")
