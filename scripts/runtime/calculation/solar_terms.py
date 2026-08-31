# solar_terms.py - 고정 Astronomy Engine으로 24절기 절입 순간을 계산한다.

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from .contracts import EXPECTED_ASTRONOMY_ENGINE_VERSION
from .errors import RuntimeCalculationError

SOLAR_TERM_NAMES = (
    "소한",
    "대한",
    "입춘",
    "우수",
    "경칩",
    "춘분",
    "청명",
    "곡우",
    "입하",
    "소만",
    "망종",
    "하지",
    "소서",
    "대서",
    "입추",
    "처서",
    "백로",
    "추분",
    "한로",
    "상강",
    "입동",
    "소설",
    "대설",
    "동지",
)
JIE_TO_MONTH = {
    2: 1,
    4: 2,
    6: 3,
    8: 4,
    10: 5,
    12: 6,
    14: 7,
    16: 8,
    18: 9,
    20: 10,
    22: 11,
    0: 12,
}
UTC = timezone.utc


@lru_cache(maxsize=4096)
def solar_term_instant(year: int, index: int) -> datetime:
    if not 1899 <= year <= 2050 or not 0 <= index <= 23:
        raise RuntimeCalculationError(
            "UNSUPPORTED_SOLAR_TERM", "절입 계산 범위를 벗어났습니다."
        )
    try:
        import importlib.metadata

        import astronomy
    except Exception as exc:
        raise RuntimeCalculationError(
            "ASTRONOMY_DEPENDENCY_MISSING", "astronomy-engine을 import하지 못했습니다."
        ) from exc
    version = importlib.metadata.version("astronomy-engine")
    if version != EXPECTED_ASTRONOMY_ENGINE_VERSION:
        raise RuntimeCalculationError(
            "ASTRONOMY_VERSION_MISMATCH",
            f"astronomy-engine {EXPECTED_ASTRONOMY_ENGINE_VERSION}가 필요하지만 {version}입니다.",
        )
    target = (285.0 + 15.0 * index) % 360.0
    month = index // 2 + 1
    start_day = 1 if index % 2 == 0 else 15
    start = astronomy.Time.Make(year, month, start_day, 0, 0, 0)
    result = astronomy.SearchSunLongitude(target, start, 12.0)
    if result is None:
        raise RuntimeCalculationError(
            "SOLAR_TERM_RESOLUTION_FAILED",
            "태양 황경 검색 범위에서 절입을 찾지 못했습니다.",
        )
    return result.Utc().replace(tzinfo=UTC)


def saju_year_month(instant: datetime, *, local_calendar_year: int) -> tuple[int, int]:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise RuntimeCalculationError(
            "INVALID_INSTANT", "절입 비교 instant에는 timezone이 필요합니다."
        )
    utc = instant.astimezone(UTC)
    lichun = solar_term_instant(local_calendar_year, 2)
    saju_year = local_calendar_year if utc >= lichun else local_calendar_year - 1
    best: tuple[datetime, int] | None = None
    for year in (local_calendar_year - 1, local_calendar_year, local_calendar_year + 1):
        for index, month_number in JIE_TO_MONTH.items():
            boundary = solar_term_instant(year, index)
            if boundary <= utc and (best is None or boundary > best[0]):
                best = (boundary, month_number)
    if best is None:
        raise RuntimeCalculationError(
            "SOLAR_TERM_RESOLUTION_FAILED", "직전 절입을 찾지 못했습니다."
        )
    return saju_year, best[1]


def jie_boundaries_between(start: datetime, end: datetime) -> list[dict[str, object]]:
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise RuntimeCalculationError(
            "INVALID_PERIOD", "절입 검색 기간이 올바르지 않습니다."
        )
    start_utc, end_utc = start.astimezone(UTC), end.astimezone(UTC)
    values: list[dict[str, object]] = []
    first_year = max(1899, start_utc.year - 1)
    last_year = min(2050, end_utc.year + 1)
    for year in range(first_year, last_year + 1):
        for index, month_number in JIE_TO_MONTH.items():
            instant = solar_term_instant(year, index)
            if start_utc <= instant <= end_utc:
                values.append(
                    {
                        "name": SOLAR_TERM_NAMES[index],
                        "index": index,
                        "saju_month_number": month_number,
                        "instant_utc": instant.isoformat().replace("+00:00", "Z"),
                    }
                )
    return sorted(values, key=lambda item: str(item["instant_utc"]))
