# calendar_provider.py - 고정 한국 음양력 패키지를 후보 provider로 감싸고 실패를 차단한다.

from __future__ import annotations

import importlib.metadata
from datetime import date
from typing import Protocol

from .contracts import EXPECTED_LUNAR_PACKAGE_VERSION
from .errors import RuntimeCalculationError


class CalendarProvider(Protocol):
    provider_id: str
    provider_version: str

    def solar_to_lunar(self, value: date) -> dict[str, object]: ...

    def lunar_to_solar(
        self, year: int, month: int, day: int, *, leap_month: bool
    ) -> date: ...


class KoreanLunarCalendarProvider:
    """KASI 전수 Gate 전까지 HARD_CANDIDATE로만 쓰는 한국 음양력 provider."""

    provider_id = "korean-lunar-calendar"

    def __init__(self) -> None:
        try:
            from korean_lunar_calendar import KoreanLunarCalendar
        except Exception as exc:
            raise RuntimeCalculationError(
                "CALENDAR_DEPENDENCY_MISSING",
                "고정 korean-lunar-calendar 패키지를 import하지 못했습니다.",
            ) from exc
        try:
            version = importlib.metadata.version("korean-lunar-calendar")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeCalculationError(
                "CALENDAR_DEPENDENCY_MISSING", "음양력 패키지 metadata가 없습니다."
            ) from exc
        if version != EXPECTED_LUNAR_PACKAGE_VERSION:
            raise RuntimeCalculationError(
                "CALENDAR_VERSION_MISMATCH",
                f"음양력 패키지 {EXPECTED_LUNAR_PACKAGE_VERSION}가 필요하지만 {version}입니다.",
            )
        self.provider_version = version
        self._calendar_type = KoreanLunarCalendar

    def solar_to_lunar(self, value: date) -> dict[str, object]:
        calendar = self._calendar_type()
        if not calendar.setSolarDate(value.year, value.month, value.day):
            raise RuntimeCalculationError(
                "UNSUPPORTED_CALENDAR_DATE", "양력 날짜를 음력으로 변환하지 못했습니다."
            )
        return {
            "year": int(calendar.lunarYear),
            "month": int(calendar.lunarMonth),
            "day": int(calendar.lunarDay),
            "leap_month": bool(calendar.isIntercalation),
        }

    def lunar_to_solar(
        self, year: int, month: int, day: int, *, leap_month: bool
    ) -> date:
        calendar = self._calendar_type()
        if not calendar.setLunarDate(year, month, day, leap_month):
            raise RuntimeCalculationError(
                "INVALID_LUNAR_DATE",
                "존재하지 않거나 지원 범위를 벗어난 음력 날짜입니다.",
            )
        try:
            return date(
                int(calendar.solarYear),
                int(calendar.solarMonth),
                int(calendar.solarDay),
            )
        except ValueError as exc:
            raise RuntimeCalculationError(
                "INVALID_CALENDAR_RESULT", "음양력 provider 결과가 올바르지 않습니다."
            ) from exc
