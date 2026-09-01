# facts_v1_3.py - 기존 표 계산을 보존하며 Skyfield 절입 근거를 구조화해 붙인다.

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from .errors import RuntimeCalculationError
from .facts import (
    _decorate_pillar,
    day_pillar,
    hour_pillar,
    month_pillar,
    tables,
    year_pillar,
)
from .solar_term_types import SolarTermProvider, build_solar_term_evidence
from .solar_terms_v1_3 import saju_year_month_resolution_v1_3


def build_chart_facts_v1_3(
    *,
    local_datetime: datetime,
    solar_date: date,
    lunar_date: dict[str, object],
    solar_term_provider: SolarTermProvider,
) -> dict[str, Any]:
    """기존 원국 표 계산에 provider 경계와 권한 근거를 결합한다."""

    resolution = saju_year_month_resolution_v1_3(
        local_datetime,
        local_calendar_year=solar_date.year,
        solar_term_provider=solar_term_provider,
    )
    raw_day = day_pillar(solar_date)
    raw = {
        "year": year_pillar(resolution.saju_year),
        "month": month_pillar(resolution.saju_year, resolution.month_number),
        "day": raw_day,
        "hour": hour_pillar(
            raw_day["stem"], local_datetime.hour, local_datetime.minute
        ),
    }
    day_stem = raw_day["stem"]
    decorated = {
        name: _decorate_pillar(value, day_stem=day_stem, day=name == "day")
        for name, value in raw.items()
    }
    counts: Counter[str] = Counter()
    for pillar in decorated.values():
        counts[pillar["stem_element"]] += 1
        counts[pillar["branch_element"]] += 1
    return {
        "calendar": {
            "solar_date": solar_date.isoformat(),
            "lunar_date": lunar_date,
        },
        "pillars": decorated,
        "day_master": {
            "stem": day_stem,
            "element": tables()["stem_elements"][day_stem],
            "yin_yang": tables()["stem_yin_yang"][day_stem],
        },
        "surface_five_elements": {
            element: counts[element]
            for element in ("목", "화", "토", "금", "수")
        },
        "calculation_profile": {
            "year_boundary": "lichun_instant",
            "month_boundary": "twelve_jie_instant",
            "day_boundary": "civil_midnight",
            "hour_clock": "civil_time",
            "branch_ten_god_basis": "main_hidden_stem",
            "solar_term_provider": solar_term_provider.provider_id,
        },
        "solar_term_evidence": build_solar_term_evidence(
            solar_term_provider,
            (
                ("year_boundary_lichun", resolution.year_boundary),
                ("month_boundary_previous_jie", resolution.month_boundary),
            ),
        ),
    }


def period_point_facts_v1_3(
    value: date,
    instant: datetime,
    *,
    solar_term_provider: SolarTermProvider,
) -> dict[str, object]:
    """기간 기준점 간지와 그 결정 절입 근거를 반환한다."""

    resolution = saju_year_month_resolution_v1_3(
        instant,
        local_calendar_year=value.year,
        solar_term_provider=solar_term_provider,
    )
    if resolution.year_boundary is None or resolution.month_boundary is None:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "기간 계산 결과에 결정 절입이 없습니다."
        )
    return {
        "date": value.isoformat(),
        "year_ganzhi": year_pillar(resolution.saju_year)["ganzhi"],
        "month_ganzhi": month_pillar(
            resolution.saju_year, resolution.month_number
        )["ganzhi"],
        "day_ganzhi": day_pillar(value)["ganzhi"],
        "solar_term_evidence": build_solar_term_evidence(
            solar_term_provider,
            (
                ("period_point_year_boundary_lichun", resolution.year_boundary),
                (
                    "period_point_month_boundary_previous_jie",
                    resolution.month_boundary,
                ),
            ),
        ),
    }
