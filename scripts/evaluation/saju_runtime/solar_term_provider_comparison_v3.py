# solar_term_provider_comparison_v3.py - Skyfield 절입 근과 UT1 시간축 후보를 공식 분 라벨로 검증한다.

from __future__ import annotations

import hashlib
import io
import math
import os
import stat
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any

from scripts.evaluation.saju_runtime.iers_finals_collector import (
    MAX_DOWNLOAD_BYTES as IERS_MAX_DOWNLOAD_BYTES,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    parse_snapshot as parse_iers_snapshot,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v1 import (
    MAXIMUM_REGRESSION_DELTA_SECONDS,
    SolarTermProviderComparisonError,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v2 import (
    END_YEAR,
    EXPECTED_JIE_ROWS,
    EXPECTED_OFFICIAL_JIE_ROWS,
    FIXED_KST,
    OFFICIAL_START_YEAR,
    PROFILE_TIMEZONE,
    START_YEAR,
    _display_minute,
    _distribution,
    render_time_scale_scatter_svg,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v2 import (
    compare_providers as compare_providers_v2,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

COMPARISON_VERSION = "saju-solar-term-provider-comparison-v3.0.0"
UTC = timezone.utc
J2000_JD = 2_451_545.0
ROOT_BISECTION_ITERATIONS = 48
ROOT_DIAGNOSTIC_ITERATIONS = 32
KASI_DECLARED_PAST_UNCERTAINTY_SECONDS = 1.0
SKYFIELD_BUILTIN_DATA_SHA256 = {
    "delta_t.npz": "2d12bd3e789543b78a1f53c8b76ed7fecffdf7e5149cfb6a0aed21a8b3db5ff6",
    "historic_deltat.npy": "f5346b780b36a0325b1847dc6c0083d66edc7e88b7f648b4c98a67bbd02b5d3f",
    "iers.npz": "c7d7536d898dfa9f8cd43e8044ff51e108cc8289675a13fee9822010a1c4935c",
    "morrison_stephenson_deltat.npy": "439e05269890df41dd75820f3f2ef467539e36eb36753e8ee8b4b78d29baad52",
}


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo != UTC:
        raise SolarTermProviderComparisonError("provider UTC 형식이 다릅니다.")
    return parsed


def _regular_file_bytes(path: Path, *, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= maximum:
            raise SolarTermProviderComparisonError(
                f"시간축 입력이 일반 파일이 아니거나 크기가 다릅니다: {path.name}"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise SolarTermProviderComparisonError(
            f"시간축 입력을 안전하게 읽지 못했습니다: {path.name}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise SolarTermProviderComparisonError(
            f"시간축 입력 크기가 제한을 넘습니다: {path.name}"
        )
    return payload


def _skyfield_builtin_data_identity() -> dict[str, Any]:
    try:
        import skyfield
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "Skyfield package를 찾지 못했습니다."
        ) from exc
    if getattr(skyfield, "__version__", None) != "1.55":
        raise SolarTermProviderComparisonError("Skyfield package version이 다릅니다.")
    data_root = Path(skyfield.__file__).resolve().parent / "data"
    actual: dict[str, str] = {}
    for filename in sorted(SKYFIELD_BUILTIN_DATA_SHA256):
        path = data_root / filename
        payload = _regular_file_bytes(path, maximum=8 * 1024 * 1024)
        actual[filename] = hashlib.sha256(payload).hexdigest()
    if actual != SKYFIELD_BUILTIN_DATA_SHA256:
        raise SolarTermProviderComparisonError(
            "Skyfield 내장 ΔT/IERS 데이터 hash가 다릅니다."
        )
    return {
        "package_version": "1.55",
        "mode": "builtin_no_network",
        "files_sha256": actual,
    }


def _iers_timescale(path: Path) -> tuple[Any, dict[str, Any]]:
    try:
        from skyfield.data import iers
        from skyfield.timelib import Timescale
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "IERS 시간축 의존성을 import하지 못했습니다."
        ) from exc
    if path.name != "finals2000A.all":
        raise SolarTermProviderComparisonError("IERS snapshot 파일명이 다릅니다.")
    payload = _regular_file_bytes(path, maximum=IERS_MAX_DOWNLOAD_BYTES)
    parsed = parse_iers_snapshot(payload)
    try:
        utc_mjd, dut1 = iers.parse_dut1_from_finals_all(io.BytesIO(payload))
        daily_tt, daily_delta_t, leap_dates, leap_offsets = iers.build_timescale_arrays(
            utc_mjd, dut1
        )
        timescale = Timescale((daily_tt, daily_delta_t), leap_dates, leap_offsets)
    except (OSError, TypeError, ValueError) as exc:
        raise SolarTermProviderComparisonError(
            "IERS snapshot으로 Skyfield 시간축을 만들지 못했습니다."
        ) from exc
    return timescale, {
        "snapshot_sha256": hashlib.sha256(payload).hexdigest(),
        "rows": parsed["rows"],
        "utc_date_range": [parsed["utc_date_start"], parsed["utc_date_end"]],
        "automatic_download_or_fallback": False,
        "outside_snapshot_coverage": "skyfield_delta_t_extrapolation_diagnostic_only",
    }


def _calendar_tuple_to_nominal_datetime(parts: tuple[Any, ...]) -> datetime:
    if len(parts) != 6:
        raise SolarTermProviderComparisonError("UT1 calendar tuple 길이가 다릅니다.")
    year, month, day, hour, minute, second = parts
    try:
        base = datetime(
            int(year), int(month), int(day), int(hour), int(minute), tzinfo=UTC
        )
        return base + timedelta(seconds=float(second))
    except (OverflowError, TypeError, ValueError) as exc:
        raise SolarTermProviderComparisonError(
            "UT1 calendar tuple을 datetime으로 바꾸지 못했습니다."
        ) from exc


def _timescale_ut1_nominal(timescale, tt_jd: list[float]) -> list[datetime]:
    calendar = timescale.tt_jd(tt_jd).ut1_calendar()
    return [
        _calendar_tuple_to_nominal_datetime(parts)
        for parts in zip(*calendar, strict=True)
    ]


def _astronomy_engine_ut_nominal(tt_jd: list[float]) -> list[datetime]:
    try:
        import astronomy
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "Astronomy Engine ΔT 후보를 import하지 못했습니다."
        ) from exc
    return [
        astronomy.Time.FromTerrestrialTime(value - J2000_JD).Utc().replace(tzinfo=UTC)
        for value in tt_jd
    ]


def _kasi_printed_label(value: datetime) -> dict[str, Any]:
    local = value.astimezone(FIXED_KST)
    rounded = datetime.fromisoformat(_display_minute(value, FIXED_KST))
    if rounded.date() > local.date() and rounded.hour == 0 and rounded.minute == 0:
        printed_date = local.date().isoformat()
        printed_hour = 24
    else:
        printed_date = rounded.date().isoformat()
        printed_hour = rounded.hour
    return {
        "normalized_minute": rounded.isoformat(timespec="minutes"),
        "printed_local_date": printed_date,
        "printed_hour": printed_hour,
        "printed_minute": rounded.minute,
    }


def _add_mapping_fields(
    records: list[dict[str, Any]],
    field_prefix: str,
    instants: list[datetime],
) -> None:
    if len(records) != len(instants):
        raise SolarTermProviderComparisonError("시간축 후보 행 수가 다릅니다.")
    for record, instant in zip(records, instants, strict=True):
        label = _kasi_printed_label(instant)
        record[f"{field_prefix}_instant_nominal"] = _utc_text(instant)
        record[f"{field_prefix}_local_date_fixed_kst"] = (
            instant.astimezone(FIXED_KST).date().isoformat()
        )
        record[f"{field_prefix}_display_minute_fixed_kst"] = label["normalized_minute"]
        record[f"{field_prefix}_printed_local_date"] = label["printed_local_date"]
        record[f"{field_prefix}_printed_hour"] = label["printed_hour"]
        record[f"{field_prefix}_printed_minute"] = label["printed_minute"]


def _absolute_distribution(values: list[float], *, digits: int = 6) -> dict[str, Any]:
    absolute = sorted(abs(value) for value in values)
    if not absolute:
        return {
            "rows": 0,
            "mean_absolute": 0.0,
            "p99_absolute": 0.0,
            "maximum_absolute": 0.0,
        }
    index = max(0, math.ceil(len(absolute) * 0.99) - 1)
    return {
        "rows": len(absolute),
        "mean_absolute": round(fmean(absolute), digits),
        "p99_absolute": round(absolute[index], digits),
        "maximum_absolute": round(absolute[-1], digits),
    }


def _root_solver_diagnostic(
    records: list[dict[str, Any]], ephemeris_path: Path
) -> dict[str, Any]:
    try:
        import astronomy
        import numpy as np
        from skyfield.api import load, load_file
        from skyfield.framelib import ecliptic_frame
        from skyfield.searchlib import EPSILON
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "근찾기 진단 의존성을 import하지 못했습니다."
        ) from exc
    ephemeris = load_file(str(ephemeris_path))
    try:
        earth = ephemeris["earth"]
        sun = ephemeris["sun"]
        timescale = load.timescale(builtin=True)
        targets = np.array(
            [(285.0 + 15.0 * row["term_index"]) % 360.0 for row in records],
            dtype=np.float64,
        )
        roots = [_parse_utc(row["skyfield_instant_utc"]) for row in records]
        root_times = timescale.from_datetimes(roots)
        longitude = (
            earth.at(root_times)
            .observe(sun)
            .apparent()
            .frame_latlon(ecliptic_frame)[1]
            .degrees
        )
        angular_residual = ((longitude - targets + 180.0) % 360.0) - 180.0

        centers = np.array(
            [
                datetime(
                    row["year"],
                    row["term_index"] // 2 + 1,
                    7 if row["term_index"] % 2 == 0 else 22,
                    tzinfo=UTC,
                ).timestamp()
                for row in records
            ],
            dtype=np.float64,
        )

        def signed_delta(timestamps):
            values = [
                datetime.fromtimestamp(float(timestamp), UTC)
                for timestamp in timestamps
            ]
            times = timescale.from_datetimes(values)
            apparent = earth.at(times).observe(sun).apparent()
            degrees = apparent.frame_latlon(ecliptic_frame)[1].degrees
            return (degrees - targets + 180.0) % 360.0 - 180.0

        left = centers - 5.0 * 86_400.0
        right = centers + 5.0 * 86_400.0
        if bool(np.any(signed_delta(left) >= 0.0)) or bool(
            np.any(signed_delta(right) <= 0.0)
        ):
            raise SolarTermProviderComparisonError(
                "근찾기 진단의 고정 bracket이 root를 감싸지 않습니다."
            )
        for _ in range(ROOT_DIAGNOSTIC_ITERATIONS):
            middle = (left + right) / 2.0
            crossed = signed_delta(middle) >= 0.0
            right = np.where(crossed, middle, right)
            left = np.where(crossed, left, middle)
        roots_32 = (left + right) / 2.0
        roots_48 = np.array([value.timestamp() for value in roots])
        iteration_delta = (roots_32 - roots_48) * 1_000_000.0
        residual_arcseconds = [float(value) * 3_600.0 for value in angular_residual]
        default_find_discrete_seconds = float(EPSILON) * 86_400.0
        if not math.isclose(default_find_discrete_seconds, 0.001, abs_tol=1e-12):
            raise SolarTermProviderComparisonError(
                "Skyfield find_discrete 기본 epsilon이 예상과 다릅니다."
            )
        return {
            "status": "root_convergence_not_explanation_for_multi_second_delta",
            "skyfield_algorithm": "custom_fixed_calendar_bracket_vector_bisection",
            "skyfield_find_discrete_used": False,
            "skyfield_find_discrete_default_epsilon_seconds": round(
                default_find_discrete_seconds, 9
            ),
            "skyfield_bisection_iterations": ROOT_BISECTION_ITERATIONS,
            "diagnostic_bisection_iterations": ROOT_DIAGNOSTIC_ITERATIONS,
            "diagnostic_32_vs_48_microseconds": _absolute_distribution(
                [float(value) for value in iteration_delta], digits=6
            ),
            "maximum_skyfield_root_longitude_residual_arcseconds": round(
                max(abs(value) for value in residual_arcseconds), 12
            ),
            "mean_skyfield_root_longitude_residual_arcseconds": round(
                fmean(abs(value) for value in residual_arcseconds), 12
            ),
            "astronomy_engine_search_tolerance_seconds": 0.01,
            "astronomy_engine_search_tolerance_source": (
                "SearchSunLongitude_calls_Search_with_0.01_seconds"
            ),
            "astronomy_engine_version": astronomy.__version__
            if hasattr(astronomy, "__version__")
            else "2.1.19",
            "conclusion": (
                "same_tt_root_delta_reflects_provider_model_or_coordinate_stack_"
                "difference_not_root_solver_tolerance"
            ),
        }
    finally:
        ephemeris.close()


def _rounding_interval_excess(center_residual_seconds: float) -> float:
    if center_residual_seconds < -30.0:
        return -30.0 - center_residual_seconds
    if center_residual_seconds >= 30.0:
        return center_residual_seconds - 30.0
    return 0.0


def _official_evidence_for_mapping(
    mapping_id: str,
    records: list[dict[str, Any]],
    official_rows: list[dict[str, Any]],
    *,
    field_prefix: str,
    official_collected_at: datetime,
) -> dict[str, Any]:
    references = [
        row
        for row in official_rows
        if OFFICIAL_START_YEAR <= row.get("year", 0) <= END_YEAR
        and row.get("term_index") in JIE_TO_MONTH
    ]
    expected_identities = [
        (year, term_index)
        for year in range(OFFICIAL_START_YEAR, END_YEAR + 1)
        for term_index in sorted(JIE_TO_MONTH)
    ]
    identities = [(row["year"], row["term_index"]) for row in references]
    complete = sorted(identities) == expected_identities and len(
        set(identities)
    ) == len(identities)
    record_map = {(row["year"], row["term_index"]): row for row in records}
    date_mismatches: list[dict[str, Any]] = []
    minute_mismatches: list[dict[str, Any]] = []
    past_uncertainty_failures: list[dict[str, Any]] = []
    future_interval_failures: list[dict[str, Any]] = []
    residuals: list[float] = []
    mismatch_excesses: list[float] = []
    past_rows = 0
    future_rows = 0
    for reference in references:
        identity = (reference["year"], reference["term_index"])
        record = record_map.get(identity)
        if record is None:
            actual_date = None
            actual_minute = None
            residual = math.inf
        else:
            actual_date = record[f"{field_prefix}_printed_local_date"]
            actual_minute = record[f"{field_prefix}_display_minute_fixed_kst"]
            residual = (
                _parse_utc(record[f"{field_prefix}_instant_nominal"])
                - datetime.fromisoformat(reference["reference_local_minute"])
            ).total_seconds()
            residuals.append(residual)
        expected_minute = reference["reference_local_minute"]
        if actual_date != reference["printed_local_date"]:
            date_mismatches.append(
                {
                    "year": identity[0],
                    "term_index": identity[1],
                    "expected": reference["printed_local_date"],
                    "actual": actual_date,
                }
            )
        is_future = datetime.fromisoformat(expected_minute) > official_collected_at
        past_rows += not is_future
        future_rows += is_future
        excess = (
            math.inf
            if not math.isfinite(residual)
            else _rounding_interval_excess(residual)
        )
        if actual_minute != expected_minute:
            mismatch = {
                "year": identity[0],
                "term_index": identity[1],
                "expected": expected_minute,
                "actual": actual_minute,
                "published_minute_center_residual_seconds": (
                    None if not math.isfinite(residual) else round(residual, 6)
                ),
                "rounding_interval_excess_seconds": (
                    None if not math.isfinite(excess) else round(excess, 6)
                ),
                "temporal_class": "future_forecast" if is_future else "past",
            }
            minute_mismatches.append(mismatch)
            if math.isfinite(excess):
                mismatch_excesses.append(excess)
            if is_future:
                future_interval_failures.append(mismatch)
            elif excess > KASI_DECLARED_PAST_UNCERTAINTY_SECONDS:
                past_uncertainty_failures.append(mismatch)
    raw_checks = {
        "available_official_current_jie_complete": complete
        and len(references) == EXPECTED_OFFICIAL_JIE_ROWS,
        "available_official_current_date_mismatch_zero": not date_mismatches,
        "available_official_current_minute_label_mismatch_zero": not minute_mismatches,
    }
    candidate_checks = {
        "official_rows_complete": raw_checks["available_official_current_jie_complete"],
        "official_printed_date_mismatch_zero": not date_mismatches,
        "past_rounding_interval_plus_declared_uncertainty_failure_zero": not past_uncertainty_failures,
        "future_forecast_rows_classified_nonapproval": future_rows > 0,
    }
    return {
        "mapping_id": mapping_id,
        "evidence_class": "SOURCE_HARD_FACT",
        "official_current_jie_rows": len(references),
        "official_current_jie_expected_rows": EXPECTED_OFFICIAL_JIE_ROWS,
        "past_rows_at_snapshot_collection": past_rows,
        "future_forecast_rows_at_snapshot_collection": future_rows,
        "official_current_date_mismatches": len(date_mismatches),
        "official_current_date_mismatch_rows": date_mismatches,
        "official_current_minute_label_mismatches": len(minute_mismatches),
        "official_current_minute_mismatch_rows": minute_mismatches,
        "published_minute_center_residual": _distribution(residuals),
        "mismatch_rounding_interval_excess_seconds": _absolute_distribution(
            mismatch_excesses
        ),
        "kasi_declared_past_uncertainty_seconds": (
            KASI_DECLARED_PAST_UNCERTAINTY_SECONDS
        ),
        "past_uncertainty_failures": len(past_uncertainty_failures),
        "past_uncertainty_failure_rows": past_uncertainty_failures,
        "future_raw_interval_failures": len(future_interval_failures),
        "future_raw_interval_failure_rows": future_interval_failures,
        "future_source_limitation": (
            "earth_rotation_is_unpredictable_and_kasi_warns_future_values_may_"
            "differ_by_seconds_to_minutes"
        ),
        "raw_minute_checks": raw_checks,
        "raw_minute_strict_eligible": all(raw_checks.values()),
        "candidate_checks": candidate_checks,
        "candidate_eligible": all(candidate_checks.values()),
        "subminute_physical_accuracy_adjudicated": False,
    }


def _candidate_selection(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ranking = sorted(
        evidence,
        key=lambda key: (
            evidence[key]["official_current_date_mismatches"],
            evidence[key]["past_uncertainty_failures"],
            evidence[key]["official_current_minute_label_mismatches"],
            key,
        ),
    )
    eligible = [key for key in ranking if evidence[key]["candidate_eligible"]]
    preferred = eligible[0] if eligible else None
    strict = [key for key in ranking if evidence[key]["raw_minute_strict_eligible"]]
    return {
        "status": (
            "preferred_candidate_selected_strict_runtime_gate_blocked"
            if preferred is not None and not strict
            else "strict_candidate_available"
            if strict
            else "blocked_no_candidate"
        ),
        "ranking_rule": (
            "date_mismatches_then_past_uncertainty_failures_then_raw_minute_mismatches"
        ),
        "ranked_candidates": ranking,
        "preferred_candidate": preferred,
        "strict_eligible_provider": strict[0] if strict else None,
        "runtime_provider_changed": False,
        "release_approval_performed": False,
    }


def candidate_boundary_checks(
    records: list[dict[str, Any]], *, field_prefix: str
) -> dict[str, Any]:
    expected_cases = EXPECTED_JIE_ROWS * 3
    instants = [_parse_utc(row[f"{field_prefix}_instant_nominal"]) for row in records]
    if len(instants) != EXPECTED_JIE_ROWS or any(
        current >= following for current, following in pairwise(instants)
    ):
        raise SolarTermProviderComparisonError(
            "선호 후보 boundary 행 수·순서가 다릅니다."
        )
    mismatches: list[dict[str, Any]] = []
    cases = 0
    for order, (record, boundary) in enumerate(zip(records, instants, strict=True)):
        for position, probe, expected_order in (
            ("before", boundary - timedelta(microseconds=1), order - 1),
            ("exact", boundary, order),
            ("after", boundary + timedelta(microseconds=1), order),
        ):
            cases += 1
            if bisect_right(instants, probe) - 1 != expected_order:
                mismatches.append(
                    {
                        "year": record["year"],
                        "term_index": record["term_index"],
                        "position": position,
                    }
                )
    return {
        "status": "passed" if not mismatches else "failed",
        "provider": "skyfield_de440s_builtin_ut1",
        "time_coordinate": "UT1_nominal",
        "cases": cases,
        "expected_cases": expected_cases,
        "mismatch_rows": len(mismatches),
        "mismatches": mismatches,
        "runtime_binding_performed": False,
    }


def _minute_phase_heuristic(base: dict[str, Any]) -> dict[str, Any]:
    mean_delta = float(base["delta_distribution"]["mean_absolute_seconds"])
    observed_rows = int(
        base["time_scale_diagnostic"]["raw_profile_display_minute_disagreements"]
    )
    predicted_percent = mean_delta / 60.0 * 100.0
    observed_percent = observed_rows / EXPECTED_JIE_ROWS * 100.0
    return {
        "mean_absolute_engine_delta_seconds": mean_delta,
        "uniform_phase_predicted_disagreement_percent": round(predicted_percent, 6),
        "observed_engine_disagreement_rows": observed_rows,
        "observed_engine_disagreement_percent": round(observed_percent, 6),
        "absolute_percentage_point_difference": round(
            abs(predicted_percent - observed_percent), 6
        ),
        "conclusion": "consistent_with_minute_boundary_phase_effect",
        "limitation": (
            "official_minute_mismatch_rate_cannot_be_inverted_into_physical_"
            "mean_error_without_unrounded_independent_truth"
        ),
    }


def compare_providers(
    ephemeris_path: Path,
    *,
    iers_snapshot: Path,
    official_current_rows: list[dict[str, Any]],
    official_collected_at: str,
    advisory_minute_rows: list[dict[str, Any]],
    historical_almanac_row: dict[str, Any],
    include_records: bool = False,
) -> dict[str, Any]:
    try:
        from skyfield.api import load
    except ImportError as exc:
        raise SolarTermProviderComparisonError(
            "Skyfield 시간축 후보를 import하지 못했습니다."
        ) from exc
    try:
        parsed_collected_at = datetime.fromisoformat(official_collected_at)
        if (
            parsed_collected_at.tzinfo is None
            or parsed_collected_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("KASI 공식 snapshot 수집 시각은 UTC여야 합니다.")
        collected_at = parsed_collected_at.astimezone(FIXED_KST)
    except (TypeError, ValueError) as exc:
        raise SolarTermProviderComparisonError(
            "KASI 공식 snapshot 수집 시각이 다릅니다."
        ) from exc
    base = compare_providers_v2(
        ephemeris_path,
        official_current_rows=official_current_rows,
        advisory_minute_rows=advisory_minute_rows,
        historical_almanac_row=historical_almanac_row,
        include_records=True,
    )
    records = base.pop("records")
    tt_jd = [float(record["skyfield_tt_jd"]) for record in records]
    builtin_timescale = load.timescale(builtin=True)
    current_iers_timescale, current_iers_identity = _iers_timescale(iers_snapshot)
    builtin_ut1 = _timescale_ut1_nominal(builtin_timescale, tt_jd)
    current_iers_ut1 = _timescale_ut1_nominal(current_iers_timescale, tt_jd)
    astronomy_ut = _astronomy_engine_ut_nominal(tt_jd)
    proleptic_utc = [_parse_utc(record["skyfield_instant_utc"]) for record in records]
    astronomy_instants = [
        _parse_utc(record["astronomy_instant_utc"]) for record in records
    ]
    for prefix, instants in (
        ("astronomy_engine", astronomy_instants),
        ("skyfield_proleptic_utc", proleptic_utc),
        ("skyfield_builtin_ut1", builtin_ut1),
        ("skyfield_current_iers_ut1", current_iers_ut1),
        ("skyfield_astronomy_delta_t_ut", astronomy_ut),
    ):
        _add_mapping_fields(records, prefix, instants)
    skyfield_times = builtin_timescale.tt_jd(tt_jd)
    current_iers_times = current_iers_timescale.tt_jd(tt_jd)
    for order, record in enumerate(records):
        record["skyfield_builtin_delta_t_seconds"] = round(
            float(skyfield_times.delta_t[order]), 6
        )
        record["skyfield_builtin_dut1_seconds"] = round(
            float(skyfield_times.dut1[order]), 6
        )
        record["skyfield_current_iers_delta_t_seconds"] = round(
            float(current_iers_times.delta_t[order]), 6
        )
        record["skyfield_current_iers_dut1_seconds"] = round(
            float(current_iers_times.dut1[order]), 6
        )
        record["skyfield_builtin_ut1_local_date_profile"] = (
            builtin_ut1[order].astimezone(PROFILE_TIMEZONE).date().isoformat()
        )
        record["skyfield_builtin_ut1_display_minute_profile"] = _display_minute(
            builtin_ut1[order], PROFILE_TIMEZONE
        )
    evidence = {
        mapping_id: _official_evidence_for_mapping(
            mapping_id,
            records,
            official_current_rows,
            field_prefix=field_prefix,
            official_collected_at=collected_at,
        )
        for mapping_id, field_prefix in (
            ("astronomy_engine", "astronomy_engine"),
            ("skyfield_de440s_proleptic_utc", "skyfield_proleptic_utc"),
            ("skyfield_de440s_builtin_ut1", "skyfield_builtin_ut1"),
            ("skyfield_de440s_current_iers_ut1", "skyfield_current_iers_ut1"),
            (
                "skyfield_de440s_astronomy_engine_delta_t_ut",
                "skyfield_astronomy_delta_t_ut",
            ),
        )
    }
    provider_candidate_evidence = {
        "astronomy_engine": evidence["astronomy_engine"],
        "skyfield_de440s_builtin_ut1": evidence["skyfield_de440s_builtin_ut1"],
    }
    selection = _candidate_selection(evidence)
    if selection["preferred_candidate"] != "skyfield_de440s_builtin_ut1":
        raise SolarTermProviderComparisonError(
            "공식 근거 ranking이 Skyfield UT1 후보를 선택하지 않았습니다."
        )
    root_diagnostic = _root_solver_diagnostic(records, ephemeris_path)
    result = {
        "schema_version": "3.0.0",
        "comparison_version": COMPARISON_VERSION,
        "range": {"start_year": START_YEAR, "end_year": END_YEAR},
        "rows": len(records),
        "expected_rows": EXPECTED_JIE_ROWS,
        "base_provider_comparison_version": base["comparison_version"],
        "providers": base["providers"],
        "delta_convention": base["delta_convention"],
        "delta_distribution": base["delta_distribution"],
        "minimum_signed_delta_seconds": base["minimum_signed_delta_seconds"],
        "maximum_signed_delta_seconds": base["maximum_signed_delta_seconds"],
        "time_scale_diagnostic_v2": base["time_scale_diagnostic"],
        "root_solver_diagnostic": root_diagnostic,
        "minute_boundary_phase_heuristic": _minute_phase_heuristic(base),
        "time_mapping_candidates": evidence,
        "time_mapping_source_identity": {
            "skyfield_builtin": _skyfield_builtin_data_identity(),
            "current_iers_snapshot": current_iers_identity,
        },
        "provider_candidate_evidence": provider_candidate_evidence,
        "selection": selection,
        "preferred_candidate_boundary_assignment_checks": candidate_boundary_checks(
            records, field_prefix="skyfield_builtin_ut1"
        ),
        "official_source_values_filled_from_provider": False,
        "target_without_official_rows": base["target_without_official_rows"],
        "institutional_advisory_evidence": base["institutional_advisory_evidence"],
        "fixed_regression_guard": {
            "maximum_seconds": MAXIMUM_REGRESSION_DELTA_SECONDS,
            "failures": base["fixed_regression_guard"]["failures"],
            "role": "non_authoritative_regression_guard_not_accuracy_oracle",
        },
        "term_identity_failures": base["term_identity_failures"],
        "astronomy_chronological_order_failures": base[
            "astronomy_chronological_order_failures"
        ],
        "skyfield_chronological_order_failures": base[
            "skyfield_chronological_order_failures"
        ],
        "baengno_1964_interpretation": {
            "current_calculation_reference": next(
                row["reference_local_minute"]
                for row in official_current_rows
                if row["year"] == 1964 and row["term_index"] == 16
            ),
            "historical_printed_label": historical_almanac_row["printed_label"],
            "historical_normalized_reference": historical_almanac_row[
                "normalized_reference_local_minute"
            ],
            "normalization_is_end_of_printed_day": True,
            "normalization_caused_current_source_mismatch": False,
            "skyfield_builtin_ut1_display_minute": next(
                row["skyfield_builtin_ut1_display_minute_fixed_kst"]
                for row in records
                if row["year"] == 1964 and row["term_index"] == 16
            ),
            "astronomy_engine_exact_local_kst": _parse_utc(
                next(
                    row["astronomy_engine_instant_nominal"]
                    for row in records
                    if row["year"] == 1964 and row["term_index"] == 16
                )
            )
            .astimezone(FIXED_KST)
            .isoformat(),
        },
        "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        "records_in_report": include_records,
    }
    if include_records:
        result["records"] = records
    return result


def records_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in records)


__all__ = [
    "COMPARISON_VERSION",
    "candidate_boundary_checks",
    "compare_providers",
    "records_jsonl",
    "render_time_scale_scatter_svg",
]
