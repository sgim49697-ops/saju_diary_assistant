# solar_terms_v1_3.py - Skyfield provider로 연·월주 절입과 기간 경계를 TT 기준 판정한다.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .errors import RuntimeCalculationError
from .solar_term_types import SolarTermBoundary, SolarTermProvider
from .solar_terms import JIE_TO_MONTH

UTC = timezone.utc


@dataclass(frozen=True)
class SajuYearMonthResolutionV13:
    """연주·월주와 이를 결정한 두 Skyfield 절입 경계."""

    saju_year: int
    month_number: int
    year_boundary: SolarTermBoundary
    month_boundary: SolarTermBoundary


def saju_year_month_resolution_v1_3(
    instant: datetime,
    *,
    local_calendar_year: int,
    solar_term_provider: SolarTermProvider,
) -> SajuYearMonthResolutionV13:
    """timezone instant를 provider의 TT root와 비교해 연·월주를 결정한다."""

    if instant.tzinfo is None or instant.utcoffset() is None:
        raise RuntimeCalculationError(
            "INVALID_INSTANT", "절입 비교 instant에는 timezone이 필요합니다."
        )
    lichun_boundary = solar_term_provider.boundary(local_calendar_year, 2)
    saju_year = (
        local_calendar_year
        if solar_term_provider.compare_instant(instant, lichun_boundary) >= 0
        else local_calendar_year - 1
    )
    best_boundary: SolarTermBoundary | None = None
    best_month: int | None = None
    for year in (
        local_calendar_year - 1,
        local_calendar_year,
        local_calendar_year + 1,
    ):
        for index, month_number in JIE_TO_MONTH.items():
            boundary = solar_term_provider.boundary(year, index)
            if solar_term_provider.compare_instant(instant, boundary) >= 0 and (
                best_boundary is None
                or boundary.tt_sort_key > best_boundary.tt_sort_key
            ):
                best_boundary = boundary
                best_month = month_number
    if best_boundary is None or best_month is None:
        raise RuntimeCalculationError(
            "SOLAR_TERM_RESOLUTION_FAILED", "직전 절입을 찾지 못했습니다."
        )
    return SajuYearMonthResolutionV13(
        saju_year=saju_year,
        month_number=best_month,
        year_boundary=lichun_boundary,
        month_boundary=best_boundary,
    )


def jie_boundaries_between_v1_3(
    start: datetime,
    end: datetime,
    *,
    solar_term_provider: SolarTermProvider,
) -> list[dict[str, object]]:
    """기간에 포함되는 12절 경계를 TT 비교 결과와 권한 메타데이터로 반환한다."""

    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
        or end < start
    ):
        raise RuntimeCalculationError(
            "INVALID_PERIOD", "절입 검색 기간이 올바르지 않습니다."
        )
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    first_year = max(1899, start_utc.year - 1)
    last_year = min(2050, end_utc.year + 1)
    values: list[tuple[SolarTermBoundary, dict[str, object]]] = []
    for year in range(first_year, last_year + 1):
        for index, month_number in JIE_TO_MONTH.items():
            boundary = solar_term_provider.boundary(year, index)
            if (
                solar_term_provider.compare_instant(start, boundary) <= 0
                and solar_term_provider.compare_instant(end, boundary) >= 0
            ):
                values.append(
                    (
                        boundary,
                        {
                            "name": boundary.term_name,
                            "year": boundary.year,
                            "index": index,
                            "saju_month_number": month_number,
                            "instant_tt_jd": boundary.tt_jd_text,
                            "instant_utc": boundary.instant_utc.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "official_display_minute_fixed_kst": (
                                boundary.official_display_minute_fixed_kst
                            ),
                            "solar_term_authority": boundary.authority_class,
                            "official_source_evidence_class": (
                                boundary.official_source_evidence_class
                            ),
                            "provider_generated_value_is_official": False,
                        },
                    )
                )
    return [
        value
        for _, value in sorted(values, key=lambda item: item[0].tt_sort_key)
    ]
