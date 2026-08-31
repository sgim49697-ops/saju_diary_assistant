# facts.py - 고정 표와 단일 profile로 사주 기둥·오행·십신 사실 후보를 계산한다.

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from functools import lru_cache
from typing import Any

from .contracts import load_table_policy
from .errors import RuntimeCalculationError
from .solar_terms import saju_year_month

STEMS = ("甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸")
BRANCHES = ("子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥")
MONTH_BRANCHES = {number: BRANCHES[(number + 1) % 12] for number in range(1, 13)}
DAY_ANCHOR = date(1992, 10, 24)
DAY_ANCHOR_INDEX = 9
ELEMENT_GENERATES = {"목": "화", "화": "토", "토": "금", "금": "수", "수": "목"}
ELEMENT_CONTROLS = {"목": "토", "토": "수", "수": "화", "화": "금", "금": "목"}


@lru_cache(maxsize=1)
def tables() -> dict[str, Any]:
    return load_table_policy()["tables"]


def pillar_from_cycle(index: int) -> dict[str, str]:
    value = index % 60
    stem, branch = STEMS[value % 10], BRANCHES[value % 12]
    return {"stem": stem, "branch": branch, "ganzhi": stem + branch}


def day_pillar(value: date) -> dict[str, str]:
    return pillar_from_cycle(DAY_ANCHOR_INDEX + (value - DAY_ANCHOR).days)


def year_pillar(saju_year: int) -> dict[str, str]:
    return {
        "stem": STEMS[(saju_year - 4) % 10],
        "branch": BRANCHES[(saju_year - 4) % 12],
        "ganzhi": STEMS[(saju_year - 4) % 10] + BRANCHES[(saju_year - 4) % 12],
    }


def month_pillar(saju_year: int, month_number: int) -> dict[str, str]:
    if month_number not in MONTH_BRANCHES:
        raise RuntimeCalculationError(
            "INVALID_SAJU_MONTH", "절기 월 번호가 올바르지 않습니다."
        )
    year_stem = (saju_year - 4) % 10
    stem = STEMS[((year_stem % 5) * 2 + month_number + 1) % 10]
    branch = MONTH_BRANCHES[month_number]
    return {"stem": stem, "branch": branch, "ganzhi": stem + branch}


def hour_pillar(day_stem: str, hour: int, minute: int) -> dict[str, str]:
    if day_stem not in STEMS or not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise RuntimeCalculationError(
            "INVALID_HOUR_INPUT", "시주 입력이 올바르지 않습니다."
        )
    shichen = ((hour * 60 + minute + 60) % 1440) // 120
    stem_index = ((STEMS.index(day_stem) % 5) * 2 + shichen) % 10
    stem, branch = STEMS[stem_index], BRANCHES[shichen]
    return {"stem": stem, "branch": branch, "ganzhi": stem + branch}


def ten_god(day_stem: str, target_stem: str) -> str:
    table = tables()
    try:
        day_element = table["stem_elements"][day_stem]
        target_element = table["stem_elements"][target_stem]
        same_polarity = (
            table["stem_yin_yang"][day_stem] == table["stem_yin_yang"][target_stem]
        )
    except KeyError as exc:
        raise RuntimeCalculationError(
            "UNKNOWN_STEM", "십신 계산 천간이 고정 표에 없습니다."
        ) from exc
    if day_element == target_element:
        return "비견" if same_polarity else "겁재"
    if ELEMENT_GENERATES[day_element] == target_element:
        return "식신" if same_polarity else "상관"
    if ELEMENT_CONTROLS[day_element] == target_element:
        return "편재" if same_polarity else "정재"
    if ELEMENT_CONTROLS[target_element] == day_element:
        return "편관" if same_polarity else "정관"
    if ELEMENT_GENERATES[target_element] == day_element:
        return "편인" if same_polarity else "정인"
    raise RuntimeCalculationError(
        "TEN_GOD_UNRESOLVED", "십신 관계를 결정하지 못했습니다."
    )


def _decorate_pillar(
    value: dict[str, str], *, day_stem: str, day: bool = False
) -> dict[str, Any]:
    table = tables()
    stem, branch = value["stem"], value["branch"]
    hidden = list(table["hidden_stems_main_first"][branch])
    return {
        **value,
        "stem_element": table["stem_elements"][stem],
        "branch_element": table["branch_elements"][branch],
        "stem_yin_yang": table["stem_yin_yang"][stem],
        "branch_yin_yang": table["branch_yin_yang"][branch],
        "hidden_stems": hidden,
        "stem_ten_god": "일간" if day else ten_god(day_stem, stem),
        "branch_ten_god": ten_god(day_stem, hidden[0]),
    }


def build_chart_facts(
    *,
    local_datetime: datetime,
    solar_date: date,
    lunar_date: dict[str, object],
) -> dict[str, Any]:
    saju_year, month_number = saju_year_month(
        local_datetime, local_calendar_year=solar_date.year
    )
    raw_day = day_pillar(solar_date)
    raw = {
        "year": year_pillar(saju_year),
        "month": month_pillar(saju_year, month_number),
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
            element: counts[element] for element in ("목", "화", "토", "금", "수")
        },
        "calculation_profile": {
            "year_boundary": "lichun_instant",
            "month_boundary": "twelve_jie_instant",
            "day_boundary": "civil_midnight",
            "hour_clock": "civil_time",
            "branch_ten_god_basis": "main_hidden_stem",
        },
    }


def period_point_facts(value: date, instant: datetime) -> dict[str, object]:
    saju_year, month_number = saju_year_month(instant, local_calendar_year=value.year)
    return {
        "date": value.isoformat(),
        "year_ganzhi": year_pillar(saju_year)["ganzhi"],
        "month_ganzhi": month_pillar(saju_year, month_number)["ganzhi"],
        "day_ganzhi": day_pillar(value)["ganzhi"],
    }
