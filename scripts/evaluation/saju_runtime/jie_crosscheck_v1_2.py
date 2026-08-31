# jie_crosscheck_v1_2.py - 절입 엔진 차이를 무판정 기록하고 ΔT·분 표기 진단을 만든다.

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.jie_crosscheck import (
    JieCrosscheckError,
    compare_jie_boundaries,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

CROSSCHECK_VERSION = "saju-jie-crosscheck-v1.2.0"
KST_ROUNDING_POLICY = "nearest_minute_half_up_in_asia_seoul"
KST = ZoneInfo("Asia/Seoul")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise JieCrosscheckError("절입 UTC 시각에 timezone offset이 없습니다.")
    return parsed


def nearest_display_minute(value: datetime) -> datetime:
    """KST 현지시각을 30초 이상 올림하는 최근접 분으로 변환한다."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise JieCrosscheckError("분 표기 변환에는 timezone-aware datetime이 필요합니다.")
    local = value.astimezone(KST)
    base = local.replace(second=0, microsecond=0)
    elapsed = local.second + local.microsecond / 1_000_000
    return base + (timedelta(minutes=1) if elapsed >= 30.0 else timedelta())


def display_minute_label(value: datetime) -> str:
    return nearest_display_minute(value).isoformat(timespec="minutes")


def _distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"rows": 0, "mean_abs_seconds": 0.0, "p99_abs_seconds": 0.0, "max_abs_seconds": 0.0}
    absolute = sorted(abs(value) for value in values)
    p99_index = max(0, math.ceil(len(absolute) * 0.99) - 1)
    return {
        "rows": len(values),
        "mean_abs_seconds": round(fmean(absolute), 6),
        "p99_abs_seconds": round(absolute[p99_index], 6),
        "max_abs_seconds": round(absolute[-1], 6),
    }


def _delta_t_values(instants: list[datetime]) -> tuple[list[float], list[float]]:
    try:
        import astronomy
        from skyfield.api import load
    except ImportError as exc:
        raise JieCrosscheckError("ΔT 진단 의존성을 import하지 못했습니다.") from exc
    astronomy_values: list[float] = []
    for value in instants:
        utc = value.astimezone(timezone.utc)
        second = utc.second + utc.microsecond / 1_000_000
        astronomy_time = astronomy.Time.Make(
            utc.year, utc.month, utc.day, utc.hour, utc.minute, second
        )
        astronomy_values.append(float(astronomy.DeltaT_EspenakMeeus(astronomy_time.ut)))
    timescale = load.timescale(builtin=True)
    skyfield_time = timescale.from_datetimes(instants)
    try:
        skyfield_values = [float(value) for value in skyfield_time.delta_t]
    except TypeError:
        skyfield_values = [float(skyfield_time.delta_t)]
    if len(skyfield_values) != len(instants):
        raise JieCrosscheckError("Skyfield ΔT 진단 행 수가 절입 행 수와 다릅니다.")
    return astronomy_values, skyfield_values


def _enrich_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    astronomy_instants = [parse_utc(row["astronomy_instant_utc"]) for row in records]
    astronomy_delta_t, skyfield_delta_t = _delta_t_values(astronomy_instants)
    enriched: list[dict[str, Any]] = []
    raw_deltas: list[float] = []
    aligned_deltas: list[float] = []
    for row, runtime_instant, runtime_dt, comparator_dt in zip(
        records,
        astronomy_instants,
        astronomy_delta_t,
        skyfield_delta_t,
        strict=True,
    ):
        comparator_instant = parse_utc(row["skyfield_instant_utc"])
        raw_delta = float(row["delta_seconds"])
        # 동일한 TT root라는 제한적 가정에서 Astronomy UTC를 Skyfield ΔT로
        # 환산하면 UTC_A' = UTC_A + ΔT_A - ΔT_S가 된다.
        aligned_delta = raw_delta + runtime_dt - comparator_dt
        raw_deltas.append(raw_delta)
        aligned_deltas.append(aligned_delta)
        enriched.append(
            {
                **row,
                "astronomy_display_minute_kst": display_minute_label(runtime_instant),
                "skyfield_display_minute_kst": display_minute_label(comparator_instant),
                "display_minute_match": display_minute_label(runtime_instant)
                == display_minute_label(comparator_instant),
                "astronomy_delta_t_seconds": round(runtime_dt, 6),
                "skyfield_delta_t_seconds": round(comparator_dt, 6),
                "delta_t_model_difference_seconds": round(runtime_dt - comparator_dt, 6),
                "delta_t_aligned_residual_seconds": round(aligned_delta, 6),
            }
        )
    raw = _distribution(raw_deltas)
    aligned = _distribution(aligned_deltas)
    raw_mean = float(raw["mean_abs_seconds"])
    aligned_mean = float(aligned["mean_abs_seconds"])
    retained_percent = 0.0 if raw_mean == 0 else aligned_mean / raw_mean * 100.0
    status = (
        "delta_t_not_primary"
        if raw_mean == 0 or retained_percent >= 75.0
        else "delta_t_may_be_primary"
    )
    diagnostic = {
        "status": status,
        "gate_role": "diagnostic_only",
        "alignment_assumption": "same_tt_root_then_replace_astronomy_delta_t_with_skyfield_delta_t",
        "classification_rule": "delta_t_not_primary_when_aligned_mean_abs_retains_at_least_75_percent",
        "raw_engine_delta": raw,
        "delta_t_aligned_residual": aligned,
        "aligned_mean_abs_retained_percent": round(retained_percent, 6),
        "remaining_cause_assessment": "astronomical_model_difference_consistent_but_not_fully_isolated",
    }
    return enriched, diagnostic


def _group_stats(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in records:
        grouped[str(row[key])].append(float(row["delta_seconds"]))
    return {group: _distribution(values) for group, values in sorted(grouped.items())}


def _decade_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in records:
        decade = int(row["year"]) // 10 * 10
        grouped[f"{decade}s"].append(float(row["delta_seconds"]))
    return {group: _distribution(values) for group, values in sorted(grouped.items())}


def compare_jie_boundaries_v1_2(
    ephemeris_path: Path,
    *,
    start_year: int = 1900,
    end_year: int = 2049,
    maximum_regression_delta_seconds: float = 120.0,
    include_records: bool = False,
) -> dict[str, Any]:
    base = compare_jie_boundaries(
        ephemeris_path,
        start_year=start_year,
        end_year=end_year,
        maximum_delta_seconds=maximum_regression_delta_seconds,
        include_records=True,
    )
    records, delta_t = _enrich_records(base.pop("records"))
    disagreements = [
        {
            "year": row["year"],
            "term_index": row["term_index"],
            "term_name": row["term_name"],
            "astronomy_local_date": row["astronomy_local_date"],
            "skyfield_local_date": row["skyfield_local_date"],
        }
        for row in records
        if row["astronomy_local_date"] != row["skyfield_local_date"]
    ]
    numerical_passed = base["status"] == "passed"
    result = {
        key: value
        for key, value in base.items()
        if key not in {"schema_version", "crosscheck_version", "status", "local_date_mismatches", "local_date_adjudicator", "records_sha256", "records_in_report"}
    }
    result.update(
        {
            "schema_version": "1.2.0",
            "crosscheck_version": CROSSCHECK_VERSION,
            "status": (
                "needs_official_date_adjudication"
                if numerical_passed and disagreements
                else "passed_numeric_crosscheck"
                if numerical_passed
                else "failed_numeric_crosscheck"
            ),
            "fixed_regression_guard": {
                "maximum_delta_seconds": maximum_regression_delta_seconds,
                "status": "passed" if numerical_passed else "failed",
                "role": "non_authoritative_fixed_regression_guard_not_physical_accuracy_budget",
            },
            "delta_convention": "astronomy_instant_utc_minus_skyfield_instant_utc",
            "local_date_comparison_scope": "astronomy_engine_vs_skyfield_only",
            "official_local_date_adjudication_performed": False,
            "engine_local_date_disagreements": len(disagreements),
            "engine_local_date_disagreement_rows": disagreements,
            "engine_display_minute_disagreements": sum(
                not row["display_minute_match"] for row in records
            ),
            "delta_t_diagnostic": delta_t,
            "by_year": _group_stats(records, "year"),
            "by_decade": _decade_stats(records),
            "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
            "records_in_report": include_records,
        }
    )
    if include_records:
        result["records"] = records
    return result


def records_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in records)


def render_delta_by_year_svg(records: list[dict[str, Any]]) -> bytes:
    if not records:
        raise JieCrosscheckError("빈 절입 기록으로 산점도를 만들 수 없습니다.")
    width, height = 960, 420
    left, right, top, bottom = 70, 30, 35, 55
    years = [int(row["year"]) for row in records]
    values = [abs(float(row["delta_seconds"])) for row in records]
    minimum_year, maximum_year = min(years), max(years)
    maximum_value = max(120.0, math.ceil(max(values) / 10.0) * 10.0)

    def x(year: int) -> float:
        span = max(1, maximum_year - minimum_year)
        return left + (year - minimum_year) / span * (width - left - right)

    def y(value: float) -> float:
        return top + (maximum_value - value) / maximum_value * (height - top - bottom)

    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fffdf8"/>',
        '<text x="70" y="22" font-family="sans-serif" font-size="15" fill="#3b3832">Astronomy Engine ↔ Skyfield/JPL DE440s 절입 |Δ| (초)</text>',
    ]
    for seconds in range(0, int(maximum_value) + 1, 20):
        line_y = y(float(seconds))
        elements.append(
            f'<line x1="{left}" y1="{line_y:.2f}" x2="{width-right}" y2="{line_y:.2f}" stroke="#ddd5c8" stroke-width="1"/>'
        )
        elements.append(
            f'<text x="{left-10}" y="{line_y+4:.2f}" text-anchor="end" font-family="sans-serif" font-size="11" fill="#665f55">{seconds}</text>'
        )
    for year in range((minimum_year // 25) * 25, maximum_year + 1, 25):
        if year < minimum_year:
            continue
        line_x = x(year)
        elements.append(
            f'<text x="{line_x:.2f}" y="{height-25}" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#665f55">{year}</text>'
        )
    for row in records:
        title = escape(
            f'{row["year"]} {row["term_name"]}: {abs(float(row["delta_seconds"])):.6f}s'
        )
        elements.append(
            f'<circle cx="{x(int(row["year"])):.2f}" cy="{y(abs(float(row["delta_seconds"]))):.2f}" r="1.7" fill="#8f5c4a" fill-opacity="0.55"><title>{title}</title></circle>'
        )
    elements.extend(
        [
            f'<line x1="{left}" y1="{y(120.0):.2f}" x2="{width-right}" y2="{y(120.0):.2f}" stroke="#b34b3f" stroke-dasharray="5 4"/>',
            f'<text x="{width-right}" y="{y(120.0)-6:.2f}" text-anchor="end" font-family="sans-serif" font-size="11" fill="#8d3d35">120초 비권위 회귀 가드</text>',
            "</svg>",
        ]
    )
    return ("\n".join(elements) + "\n").encode("utf-8")


def artifact_hash(value: bytes) -> dict[str, int | str]:
    return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}


def summary_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
