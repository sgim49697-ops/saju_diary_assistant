# normalize.py - saju-tools-v1 인자를 한국 단일 profile 입력으로 정규화한다.

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from scripts.runtime.saju_contract import SajuContractError, validate_tool_arguments

from .calendar_provider import CalendarProvider
from .contracts import POLICY_ID
from .errors import RuntimeCalculationError

MIN_YEAR = 1900
MAX_YEAR = 2049


def normalize_colloquial_time_hint(
    arguments: dict[str, Any], hint: str
) -> dict[str, Any]:
    """오전/오후를 saju-tools-v1의 기존 range 표현으로 바꾼다."""
    normalized = " ".join(hint.strip().lower().split())
    ranges = {
        "am": ("00:00", "11:59"),
        "오전": ("00:00", "11:59"),
        "pm": ("12:00", "23:59"),
        "오후": ("12:00", "23:59"),
    }
    if normalized not in ranges:
        raise RuntimeCalculationError(
            "UNSUPPORTED_TIME_HINT", "지원하지 않는 시간 힌트입니다."
        )
    result = deepcopy(arguments)
    start, end = ranges[normalized]
    result["birth_time"] = None
    result["time_precision"] = "range"
    result["time_range"] = {"start": start, "end": end}
    return result


def _parse_input_date(value: object, *, calendar: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise RuntimeCalculationError(
            "INVALID_BIRTH_DATE", "출생일은 YYYY-MM-DD 문자열이어야 합니다."
        )
    parts = value.split("-")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise RuntimeCalculationError(
            "INVALID_BIRTH_DATE", "출생일은 YYYY-MM-DD 형식이어야 합니다."
        )
    year, month, day = (int(part) for part in parts)
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise RuntimeCalculationError(
            "UNSUPPORTED_YEAR", f"현재 지원 연도는 {MIN_YEAR}~{MAX_YEAR}년입니다."
        )
    if calendar == "solar":
        try:
            date(year, month, day)
        except ValueError as exc:
            raise RuntimeCalculationError(
                "INVALID_BIRTH_DATE", "존재하지 않는 양력 날짜입니다."
            ) from exc
    elif not (1 <= month <= 12 and 1 <= day <= 30):
        raise RuntimeCalculationError(
            "INVALID_BIRTH_DATE", "음력 월·일 범위가 올바르지 않습니다."
        )
    return year, month, day


def normalize_tool_birth_input(
    arguments: dict[str, Any], provider: CalendarProvider
) -> dict[str, Any]:
    try:
        validate_tool_arguments("calculate_saju_chart", arguments)
    except SajuContractError as exc:
        raise RuntimeCalculationError("INVALID_TOOL_ARGUMENTS", str(exc)) from exc
    birthplace = arguments["birthplace"]
    if birthplace["country_code"] != "KR" or birthplace["timezone"] != "Asia/Seoul":
        raise RuntimeCalculationError(
            "UNSUPPORTED_REGION",
            "현재 runtime은 대한민국 출생·Asia/Seoul만 지원합니다.",
        )
    calendar = str(arguments["calendar"])
    year, month, day = _parse_input_date(arguments["birth_date"], calendar=calendar)
    if calendar == "solar":
        solar_date = date(year, month, day)
        lunar = provider.solar_to_lunar(solar_date)
        leap_month: bool | None = None
    else:
        leap_month = bool(arguments["leap_month"])
        solar_date = provider.lunar_to_solar(year, month, day, leap_month=leap_month)
        if not MIN_YEAR <= solar_date.year <= MAX_YEAR:
            raise RuntimeCalculationError(
                "UNSUPPORTED_YEAR",
                "음력 변환 결과가 runtime 지원 양력 연도를 벗어났습니다.",
            )
        lunar = {"year": year, "month": month, "day": day, "leap_month": leap_month}
    precision = arguments["time_precision"]
    time_range = deepcopy(arguments["time_range"])
    if precision == "range" and time_range["start"] > time_range["end"]:
        raise RuntimeCalculationError(
            "CROSS_MIDNIGHT_RANGE_UNSUPPORTED",
            "날짜를 넘는 시간 범위는 두 날짜로 나눠 확인해야 합니다.",
        )
    return {
        "calendar": calendar,
        "local_birth_date": str(arguments["birth_date"]),
        "solar_birth_date": solar_date.isoformat(),
        "lunar_birth_date": {
            "year": int(lunar["year"]),
            "month": int(lunar["month"]),
            "day": int(lunar["day"]),
            "leap_month": bool(lunar["leap_month"]),
        },
        "lunar_leap_month": leap_month,
        "birth_time_precision": precision,
        "local_birth_time": arguments["birth_time"],
        "birth_time_range": time_range,
        "country_code": "KR",
        "city": str(birthplace["city"]).strip(),
        "iana_time_zone": "Asia/Seoul",
        "fold": None,
        "policy_id": POLICY_ID,
    }
