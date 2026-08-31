# timezone_resolver.py - Asia/Seoul 현지시각의 DST fold·gap을 추측 없이 해석한다.

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import RuntimeCalculationError

UTC = timezone.utc


def _candidate(naive: datetime, zone: ZoneInfo, fold: int) -> datetime | None:
    aware = naive.replace(tzinfo=zone, fold=fold)
    round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
    return aware if round_trip == naive else None


def resolve_local_datetime(
    naive: datetime,
    *,
    timezone_name: str = "Asia/Seoul",
    fold: int | None = None,
) -> dict[str, object]:
    if naive.tzinfo is not None:
        raise RuntimeCalculationError(
            "INVALID_LOCAL_TIME", "현지시각 입력은 timezone-naive여야 합니다."
        )
    if fold not in {None, 0, 1}:
        raise RuntimeCalculationError(
            "INVALID_FOLD", "fold는 0, 1 또는 null이어야 합니다."
        )
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeCalculationError(
            "UNKNOWN_TIMEZONE", "IANA timezone을 찾을 수 없습니다."
        ) from exc
    valid = [
        item
        for item in (_candidate(naive, zone, 0), _candidate(naive, zone, 1))
        if item
    ]
    unique_by_utc: dict[datetime, datetime] = {}
    for item in valid:
        unique_by_utc.setdefault(item.astimezone(UTC), item)
    if not unique_by_utc:
        return {"status": "nonexistent", "candidates": []}
    ordered = [unique_by_utc[key] for key in sorted(unique_by_utc)]
    if len(ordered) == 1:
        selected = ordered[0]
        return {"status": "unique", "candidates": [selected], "selected": selected}
    if fold is not None:
        selected = naive.replace(tzinfo=zone, fold=fold)
        if selected.astimezone(UTC) not in unique_by_utc:
            raise RuntimeCalculationError(
                "INVALID_FOLD", "요청한 fold가 유효한 후보가 아닙니다."
            )
        return {"status": "resolved_fold", "candidates": ordered, "selected": selected}
    return {"status": "ambiguous", "candidates": ordered}
