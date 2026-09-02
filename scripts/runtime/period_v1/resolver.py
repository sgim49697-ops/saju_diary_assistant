# resolver.py - 서버 KST 기준으로 제한된 상대·명시 기간을 1~31일 범위로 해석한다.

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.calculation.contracts_v1_5 import SINGLE_DAY_END, SINGLE_DAY_START

from .contracts import validate_public_period_event, validate_resolved_scope
from .errors import PeriodRuntimeError

KST = ZoneInfo("Asia/Seoul")
MAX_PERIOD_DAYS = 31


def server_kst_today() -> date:
    return datetime.now(KST).date()


def _validated_reference_date(value: date) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise PeriodRuntimeError(
            "PERIOD_REFERENCE_DATE_INVALID", "서버 KST 기준일을 확정할 수 없습니다."
        )
    return value


def _relative_bounds(expression: str, reference: date) -> tuple[date, date]:
    if expression == "today":
        return reference, reference
    if expression == "tomorrow":
        target = reference + timedelta(days=1)
        return target, target
    if expression == "this_weekend":
        if reference.weekday() == 5:
            return reference, reference + timedelta(days=1)
        if reference.weekday() == 6:
            return reference, reference
        saturday = reference + timedelta(days=5 - reference.weekday())
        return saturday, saturday + timedelta(days=1)
    if expression == "this_week":
        return reference, reference + timedelta(days=6 - reference.weekday())
    if expression == "this_month":
        last_day = monthrange(reference.year, reference.month)[1]
        return reference, reference.replace(day=last_day)
    raise PeriodRuntimeError(
        "PERIOD_REQUEST_EXPRESSION_INVALID", "명시 날짜가 필요한 기간 표현입니다."
    )


def resolve_period_scope(
    event: Any,
    *,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """공개 요청을 서버 기준의 정오 일별 label 범위로 확정한다."""
    validated = validate_public_period_event(event)
    request = validated["request"]
    reference = _validated_reference_date(
        server_kst_today() if reference_date is None else reference_date
    )
    expression = request["date_expression"]
    if expression == "explicit":
        start = date.fromisoformat(request["start_date"])
        end = date.fromisoformat(request["end_date"])
    else:
        start, end = _relative_bounds(expression, reference)

    minimum = max(reference, SINGLE_DAY_START)
    if start > end:
        raise PeriodRuntimeError(
            "PERIOD_RANGE_INVALID", "기간 종료일은 시작일보다 빠를 수 없습니다."
        )
    if start < minimum:
        raise PeriodRuntimeError(
            "PERIOD_PAST_NOT_ALLOWED",
            f"기간 시작일은 서버 KST 기준 {minimum.isoformat()} 이후여야 합니다.",
        )
    if end > SINGLE_DAY_END:
        raise PeriodRuntimeError(
            "PERIOD_OUT_OF_RELEASE_RANGE",
            f"기간 종료일은 {SINGLE_DAY_END.isoformat()} 이하여야 합니다.",
        )
    day_count = (end - start).days + 1
    if day_count > MAX_PERIOD_DAYS:
        raise PeriodRuntimeError(
            "PERIOD_RANGE_TOO_LONG", "한 기간 요청은 최대 31일까지 허용합니다."
        )
    return validate_resolved_scope(
        {
            "schema_version": "saju-period-resolved-scope-v1",
            "date_expression": expression,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "day_count": day_count,
            "timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
            "reference_date": reference.isoformat(),
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        }
    )
