# extractors.py - 규칙·KI20 narrow JSON 추출을 같은 최소 슬롯 계약으로 정규화한다.

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
from typing import Any, Protocol

from .cases import CITIES, ExtractionResult
from .contracts import canonical_json_bytes, strict_loads
from .errors import ExtractionError, GroundedDialogueError

DATE_PATTERN = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
EXACT_TIME_PATTERN = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*시\s*([0-5]?\d)\s*분")
RANGE_PATTERN = re.compile(
    r"(오전|오후|저녁)\s*(\d{1,2})\s*시(?:부터|에서)\s*(\d{1,2})\s*시"
)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
CALENDAR_AMBIGUITY_PATTERN = re.compile(r"양력인지\s*음력|양력.*음력.*(?:적지|모르)")
LEAP_AMBIGUITY_PATTERN = re.compile(r"평달인지\s*윤달인지|윤달인지\s*평달인지")
EXPLICIT_TIME_UNKNOWN_PATTERN = re.compile(
    r"(?:출생\s*)?시간(?:은|을)?\s*모릅|시간\s*미상"
)
MODEL_NARROW_SYSTEM_PROMPT = """당신은 현재 사용자 발화에 명시된 출생 슬롯만 추출합니다.
반드시 JSON object 하나만 출력하세요. 설명·markdown·도구 호출·행동 판단은 금지합니다.
최상위 key는 updates, explicit_unknown_fields 두 개뿐입니다.
updates 허용 key는 birth_date, calendar, leap_month, birth_time, time_range, birthplace_city뿐입니다.
현재 발화에 없는 값은 추측하거나 이전 state에서 복사하지 마세요.
birth_date는 YYYY-MM-DD, calendar는 solar 또는 lunar, birth_time은 HH:MM입니다.
time_range는 {"start":"HH:MM","end":"HH:MM"}, 출생시각을 명시적으로 모른다고 한 경우에만 explicit_unknown_fields를 ["birth_time"]으로 출력하세요.
그 외 explicit_unknown_fields는 []입니다."""


class NarrowModelRunner(Protocol):
    def generate(
        self, messages: Sequence[Mapping[str, str]], *, max_new_tokens: int
    ) -> str:
        ...


def _birthplace(city: str) -> dict[str, Any]:
    return {
        "country_code": "KR",
        "city": city,
        "timezone": "Asia/Seoul",
        "longitude": None,
        "latitude": None,
    }


def _dates_in_text(text: str) -> set[str]:
    values: set[str] = set()
    for year, month, day_value in DATE_PATTERN.findall(text):
        try:
            parsed = date(int(year), int(month), int(day_value))
        except ValueError:
            continue
        if 1900 <= parsed.year <= 2049:
            values.add(parsed.isoformat())
    return values


def _times_in_text(text: str) -> set[str]:
    return {
        f"{int(hour):02d}:{int(minute):02d}"
        for hour, minute in EXACT_TIME_PATTERN.findall(text)
    }


def _ranges_in_text(text: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for qualifier, start_raw, end_raw in RANGE_PATTERN.findall(text):
        start, end = int(start_raw), int(end_raw)
        if not (0 <= start <= 12 and 0 <= end <= 12):
            continue
        if qualifier in {"오후", "저녁"}:
            if start < 12:
                start += 12
            if end < 12:
                end += 12
        if qualifier == "오전" and start == 12:
            start = 0
        if qualifier == "오전" and end == 12:
            end = 0
        if 0 <= start <= end <= 23:
            values.append({"start": f"{start:02d}:00", "end": f"{end:02d}:00"})
    return values


class RuleSlotExtractor:
    """고정 공개합성 suite에 한정한 결정론적 진단 parser다."""

    extractor_id = "rule"

    def extract(self, utterance: str, state: Mapping[str, Any]) -> ExtractionResult:
        del state
        updates: dict[str, Any] = {}
        dates = _dates_in_text(utterance)
        if len(dates) == 1:
            updates["birth_date"] = next(iter(dates))
        elif len(dates) > 1:
            return ExtractionResult(valid=False, error_code="MULTIPLE_BIRTH_DATES")

        calendar_ambiguous = CALENDAR_AMBIGUITY_PATTERN.search(utterance) is not None
        if not calendar_ambiguous:
            if "음력" in utterance:
                updates["calendar"] = "lunar"
            elif "양력" in utterance:
                updates["calendar"] = "solar"
        leap_ambiguous = LEAP_AMBIGUITY_PATTERN.search(utterance) is not None
        if not leap_ambiguous and updates.get("calendar") == "lunar":
            if "윤달" in utterance or "윤월" in utterance:
                updates["leap_month"] = True
            elif "평달" in utterance:
                updates["leap_month"] = False

        exact_times = _times_in_text(utterance)
        ranges = _ranges_in_text(utterance)
        if len(exact_times) == 1 and not ranges:
            updates["birth_time"] = next(iter(exact_times))
        elif len(exact_times) > 1 or len(ranges) > 1:
            return ExtractionResult(valid=False, error_code="MULTIPLE_BIRTH_TIMES")
        elif ranges:
            updates["time_range"] = ranges[0]

        if "해외" not in utterance:
            cities = [city for city in CITIES if city in utterance]
            if len(cities) == 1:
                updates["birthplace"] = _birthplace(cities[0])
            elif len(cities) > 1:
                return ExtractionResult(valid=False, error_code="MULTIPLE_BIRTHPLACES")

        explicit_unknown = (
            ("birth_time",)
            if EXPLICIT_TIME_UNKNOWN_PATTERN.search(utterance)
            else ()
        )
        if explicit_unknown:
            updates.pop("birth_time", None)
            updates.pop("time_range", None)
        return ExtractionResult(updates, explicit_unknown)


class OracleSlotExtractor:
    """case ID로 재구성한 turn별 Gold를 순서대로 반환한다."""

    extractor_id = "oracle"

    def __init__(self, results: Sequence[ExtractionResult]) -> None:
        self._results = list(results)
        self._index = 0

    def extract(self, utterance: str, state: Mapping[str, Any]) -> ExtractionResult:
        del utterance, state
        if self._index >= len(self._results):
            raise ExtractionError("oracle turn 수를 초과했습니다.")
        result = self._results[self._index]
        self._index += 1
        return result

    def assert_consumed(self) -> None:
        if self._index != len(self._results):
            raise ExtractionError("oracle turn을 모두 사용하지 않았습니다.")


def _validate_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ExtractionError("birth_date_type")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ExtractionError("birth_date_format") from exc
    if parsed.isoformat() != value or not 1900 <= parsed.year <= 2049:
        raise ExtractionError("birth_date_range")
    return value


def _validate_time(value: Any, label: str) -> str:
    if not isinstance(value, str) or TIME_PATTERN.fullmatch(value) is None:
        raise ExtractionError(f"{label}_format")
    return value


def parse_model_narrow_output(
    output: str,
    *,
    utterance: str,
    state: Mapping[str, Any],
) -> ExtractionResult:
    """단일 strict JSON 응답을 검증한다. 수정·재시도·fence 제거는 하지 않는다."""

    try:
        parsed = strict_loads(output, "model narrow output")
        if not isinstance(parsed, dict) or set(parsed) != {
            "updates",
            "explicit_unknown_fields",
        }:
            raise ExtractionError("top_level_fields")
        updates = parsed["updates"]
        unknown = parsed["explicit_unknown_fields"]
        allowed = {
            "birth_date",
            "calendar",
            "leap_month",
            "birth_time",
            "time_range",
            "birthplace_city",
        }
        if not isinstance(updates, dict) or not set(updates) <= allowed:
            raise ExtractionError("updates_fields")
        if unknown not in ([], ["birth_time"]):
            raise ExtractionError("explicit_unknown_fields")
        normalized: dict[str, Any] = {}
        if "birth_date" in updates:
            normalized["birth_date"] = _validate_date(updates["birth_date"])
            if normalized["birth_date"] not in _dates_in_text(utterance):
                raise ExtractionError("birth_date_not_mentioned")
        if "calendar" in updates:
            value = updates["calendar"]
            if value not in {"solar", "lunar"}:
                raise ExtractionError("calendar_enum")
            surface = "양력" if value == "solar" else "음력"
            if surface not in utterance or CALENDAR_AMBIGUITY_PATTERN.search(utterance):
                raise ExtractionError("calendar_not_unambiguous")
            normalized["calendar"] = value
        if "leap_month" in updates:
            value = updates["leap_month"]
            if not isinstance(value, bool) or LEAP_AMBIGUITY_PATTERN.search(utterance):
                raise ExtractionError("leap_month_invalid")
            expected_surface = ("윤달", "윤월") if value else ("평달",)
            if not any(surface in utterance for surface in expected_surface):
                raise ExtractionError("leap_month_not_mentioned")
            effective_calendar = normalized.get(
                "calendar", state.get("birth_slots", {}).get("calendar")
            )
            if effective_calendar != "lunar":
                raise ExtractionError("leap_month_without_lunar")
            normalized["leap_month"] = value
        if "birth_time" in updates:
            normalized["birth_time"] = _validate_time(updates["birth_time"], "birth_time")
            if normalized["birth_time"] not in _times_in_text(utterance):
                raise ExtractionError("birth_time_not_mentioned")
        if "time_range" in updates:
            value = updates["time_range"]
            if not isinstance(value, dict) or set(value) != {"start", "end"}:
                raise ExtractionError("time_range_fields")
            value = {
                "start": _validate_time(value["start"], "time_range_start"),
                "end": _validate_time(value["end"], "time_range_end"),
            }
            if value["start"] > value["end"] or value not in _ranges_in_text(utterance):
                raise ExtractionError("time_range_not_mentioned")
            normalized["time_range"] = value
        if "birthplace_city" in updates:
            city = updates["birthplace_city"]
            if not isinstance(city, str) or city not in CITIES or city not in utterance:
                raise ExtractionError("birthplace_not_mentioned")
            if "해외" in utterance:
                raise ExtractionError("unsupported_overseas_birthplace")
            normalized["birthplace"] = _birthplace(city)
        if "birth_time" in normalized and "time_range" in normalized:
            raise ExtractionError("conflicting_time_values")
        explicit_unknown = tuple(unknown)
        if explicit_unknown and not EXPLICIT_TIME_UNKNOWN_PATTERN.search(utterance):
            raise ExtractionError("time_unknown_not_explicit")
        if explicit_unknown and {"birth_time", "time_range"} & set(normalized):
            raise ExtractionError("time_unknown_conflict")
        return ExtractionResult(
            deepcopy(normalized),
            explicit_unknown,
            raw_output=output,
        )
    except GroundedDialogueError as exc:
        code = str(exc) or type(exc).__name__
        return ExtractionResult(
            valid=False,
            error_code=code,
            raw_output=output,
        )


class ModelNarrowSlotExtractor:
    """KI20에 추출만 한 번 요청하고 strict JSON 실패를 그대로 측정한다."""

    extractor_id = "model_narrow"

    def __init__(self, model: NarrowModelRunner, *, max_new_tokens: int = 160) -> None:
        self._model = model
        self._max_new_tokens = max_new_tokens

    def extract(self, utterance: str, state: Mapping[str, Any]) -> ExtractionResult:
        visible_state = {
            key: value
            for key, value in state.get("birth_slots", {}).items()
            if key
            in {
                "birth_date",
                "calendar",
                "leap_month",
                "birth_time",
                "time_precision",
                "time_range",
                "birthplace",
            }
            and value is not None
        }
        payload = canonical_json_bytes(
            {"current_slots": visible_state, "utterance": utterance}
        ).decode("utf-8")
        output = self._model.generate(
            [
                {"role": "system", "content": MODEL_NARROW_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            max_new_tokens=self._max_new_tokens,
        )
        return parse_model_narrow_output(output, utterance=utterance, state=state)
