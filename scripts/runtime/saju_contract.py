# saju_contract.py - session state·상대날짜·strict tool 인자 계약을 fail-closed로 검증한다.

from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIG_ROOT = REPO_ROOT / "configs/runtime"
TOOL_SCHEMA_VERSION = "saju-tools-v1"
SESSION_SCHEMA_VERSION = "saju-session-state-v1"
RELATIVE_DATE_POLICY_VERSION = "saju-relative-date-policy-v1"
CALCULATION_POLICY_ID = "saju-calculation-policy-v1.0.0"
MODEL_VISIBLE_TOOL_RESULT_FIELDS = frozenset(
    {"status", "hard_facts", "fact_authority", "code", "message", "limitations"}
)
TOOL_RESULT_STATUS_VALUES = frozenset({"ok", "partial", "error", "blocked"})
PROVENANCE_VALUES = {
    "user_explicit",
    "session_confirmed",
    "runtime_normalized",
    "deterministic_default",
    "derived_from_policy",
    "unsupported",
}
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
FINGERPRINT_FIELDS = (
    "birth_date",
    "calendar",
    "leap_month",
    "birth_time",
    "time_precision",
    "time_range",
    "birthplace",
    "timezone",
    "gender_for_daeun",
)


class SajuContractError(RuntimeError):
    """Runtime state나 tool 계약을 위반했을 때 발생한다."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SajuContractError(f"JSON 계약을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise SajuContractError(f"JSON 계약 최상위는 object여야 합니다: {path}")
    return value


def load_tool_schema(path: Path | None = None) -> dict[str, Any]:
    contract = load_json_object(path or RUNTIME_CONFIG_ROOT / "tool_schema_v1.json")
    if contract.get("tool_schema_version") != TOOL_SCHEMA_VERSION:
        raise SajuContractError("tool schema version이 고정 계약과 다릅니다.")
    tools = contract.get("tools")
    if not isinstance(tools, list) or len(tools) != 2:
        raise SajuContractError("tool schema는 정확히 두 function을 가져야 합니다.")
    return contract


def tool_by_name(name: str, path: Path | None = None) -> dict[str, Any]:
    for tool in load_tool_schema(path)["tools"]:
        if tool.get("function", {}).get("name") == name:
            return deepcopy(tool)
    raise SajuContractError(f"허용되지 않은 tool입니다: {name}")


def project_model_visible_tool_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Executor 내부 결과에서 모델이 답변 근거로 볼 최소 allowlist만 반환한다."""
    status = value.get("status")
    if status not in TOOL_RESULT_STATUS_VALUES:
        raise SajuContractError("tool result status가 허용 enum이 아닙니다.")
    result = {
        key: deepcopy(item)
        for key, item in value.items()
        if key in MODEL_VISIBLE_TOOL_RESULT_FIELDS
        and item is not None
        and item != ""
        and item != []
    }
    facts = result.get("hard_facts")
    if status in {"ok", "partial"} and not isinstance(facts, Mapping):
        raise SajuContractError("성공·부분 tool result에는 hard_facts가 필요합니다.")
    if "fact_authority" in result and result["fact_authority"] not in {
        "HARD_GT",
        "HARD_CANDIDATE",
        "POLICY_BOUND_RULE",
    }:
        raise SajuContractError("tool result fact_authority가 허용 enum이 아닙니다.")
    limitations = result.get("limitations")
    if limitations is not None and (
        not isinstance(limitations, list)
        or any(not isinstance(item, str) or not item.strip() for item in limitations)
    ):
        raise SajuContractError("tool result limitations가 문자열 list가 아닙니다.")
    if status in {"error", "blocked"} and not any(
        isinstance(result.get(key), str) and result[key].strip()
        for key in ("code", "message")
    ):
        raise SajuContractError("실패 tool result에는 code 또는 message가 필요합니다.")
    return result


def empty_session_state(*, saju_opt_in: bool = False) -> dict[str, Any]:
    return {
        "birth_slots": {
            "birth_date": None,
            "calendar": None,
            "leap_month": None,
            "birth_time": None,
            "time_precision": None,
            "time_range": None,
            "birthplace": None,
            "timezone": None,
            "gender_for_daeun": None,
        },
        "confirmed_fields": [],
        "explicit_unknown_fields": [],
        "field_provenance": {},
        "chart": {
            "chart_id": None,
            "chart_valid": False,
            "chart_input_fingerprint": None,
            "chart_policy_version": None,
            "hard_facts": None,
        },
        "request_context": {
            "reference_datetime": None,
            "timezone": None,
            "relative_expression": None,
            "normalized_period": None,
        },
        "evidence_by_turn": {},
        "state_revision": 0,
        "last_tool_status": None,
        "current_intent": None,
        "saju_opt_in": bool(saju_opt_in),
    }


def _parse_date(value: str, label: str) -> date:
    if not DATE_PATTERN.fullmatch(value):
        raise SajuContractError(f"{label}이 YYYY-MM-DD 형식이 아닙니다.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SajuContractError(f"{label}이 유효한 날짜가 아닙니다.") from exc


def _validate_birth_date(value: str, calendar_type: Any) -> None:
    if calendar_type == "solar":
        _parse_date(value, "birth_date")
        return
    if calendar_type != "lunar" or not DATE_PATTERN.fullmatch(value):
        raise SajuContractError("birth_date·calendar 형식이 잘못됐습니다.")
    year, month, day_value = (int(part) for part in value.split("-"))
    if not 1 <= year <= 9999 or not 1 <= month <= 12 or not 1 <= day_value <= 30:
        raise SajuContractError("음력 birth_date 범위가 잘못됐습니다.")


def _validate_timezone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SajuContractError("timezone은 비지 않은 IANA 문자열이어야 합니다.")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise SajuContractError(f"IANA timezone을 찾을 수 없습니다: {value}") from exc


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _next_month(value: date) -> tuple[int, int]:
    if value.month == 12:
        return value.year + 1, 1
    return value.year, value.month + 1


def resolve_relative_period(
    expression: str,
    *,
    reference_datetime: str,
    timezone: str,
) -> dict[str, str | None]:
    """한국어 상대 날짜를 고정 정책의 Gregorian 범위로 정규화한다."""
    zone = _validate_timezone(timezone)
    try:
        parsed = datetime.fromisoformat(reference_datetime)
    except (TypeError, ValueError) as exc:
        raise SajuContractError(
            "reference_datetime은 offset이 있는 ISO-8601이어야 합니다."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SajuContractError("reference_datetime에 timezone offset이 필요합니다.")
    local = parsed.astimezone(zone).date()
    normalized = " ".join(expression.strip().split())

    if normalized == "오늘":
        start = end = local
        period_type = "day"
    elif normalized == "내일":
        start = end = local + timedelta(days=1)
        period_type = "day"
    elif normalized in {"이번 주", "다음 주"}:
        monday = local - timedelta(days=local.weekday())
        if normalized == "다음 주":
            monday += timedelta(days=7)
        start, end, period_type = monday, monday + timedelta(days=6), "week"
    elif normalized in {"이번 주말", "다음 주말"}:
        days_until_saturday = (5 - local.weekday()) % 7
        # 토요일에만 이미 시작된 주말을 건너뛴다. 일요일의 modulo 결과는
        # 이미 바로 다음 토요일이므로 7일을 더하면 한 주를 과도하게 건너뛴다.
        if local.weekday() == 5:
            days_until_saturday += 7
        if normalized == "다음 주말":
            days_until_saturday += 7
        start = local + timedelta(days=days_until_saturday)
        end, period_type = start + timedelta(days=1), "week"
    elif normalized in {"이번 달", "다음 달"}:
        year, month = local.year, local.month
        if normalized == "다음 달":
            year, month = _next_month(local)
        start, end = _month_bounds(year, month)
        period_type = "month"
    elif normalized in {"올해", "내년"}:
        year = local.year + (normalized == "내년")
        start, end, period_type = date(year, 1, 1), date(year, 12, 31), "year"
    else:
        raise SajuContractError(f"지원하지 않는 상대 날짜 표현입니다: {expression!r}")
    return {
        "period_type": period_type,
        "start_date": start.isoformat(),
        "end_date": None if period_type == "day" else end.isoformat(),
        "normalizer_version": RELATIVE_DATE_POLICY_VERSION,
    }


def birth_input_fingerprint(state: Mapping[str, Any]) -> str:
    slots = state.get("birth_slots")
    if not isinstance(slots, Mapping):
        raise SajuContractError("birth_slots가 object가 아닙니다.")
    return sha256_json({field: slots.get(field) for field in FINGERPRINT_FIELDS})


def invalidate_chart_for_correction(
    state: Mapping[str, Any],
    updates: Mapping[str, Any],
    *,
    provenance: str = "user_explicit",
) -> dict[str, Any]:
    if provenance not in PROVENANCE_VALUES or provenance == "unsupported":
        raise SajuContractError("정정 provenance가 허용되지 않습니다.")
    result = deepcopy(dict(state))
    validate_session_state(result)
    slots = result["birth_slots"]
    changed = False
    for field, value in updates.items():
        if field not in FINGERPRINT_FIELDS:
            raise SajuContractError(f"정정할 수 없는 field입니다: {field}")
        if slots.get(field) != value:
            slots[field] = value
            changed = True
        result["field_provenance"][field] = provenance
        if field not in result["confirmed_fields"]:
            result["confirmed_fields"].append(field)
    result["confirmed_fields"] = sorted(set(result["confirmed_fields"]))
    if changed:
        result["chart"] = {
            "chart_id": None,
            "chart_valid": False,
            "chart_input_fingerprint": None,
            "chart_policy_version": None,
            "hard_facts": None,
        }
        result["state_revision"] += 1
        result["last_tool_status"] = None
    validate_session_state(result)
    return result


def _validate_birth_slots(state: Mapping[str, Any]) -> None:
    slots = state["birth_slots"]
    precision = slots["time_precision"]
    birth_time = slots["birth_time"]
    time_range = slots["time_range"]
    if slots["birth_date"] is not None:
        _validate_birth_date(slots["birth_date"], slots["calendar"])
    if birth_time is not None and not TIME_PATTERN.fullmatch(birth_time):
        raise SajuContractError("birth_time이 HH:MM 형식이 아닙니다.")
    if precision == "exact" and (birth_time is None or time_range is not None):
        raise SajuContractError("exact 시간은 birth_time만 필요합니다.")
    if precision == "range" and (
        birth_time is not None or not isinstance(time_range, Mapping)
    ):
        raise SajuContractError("range 시간은 time_range만 필요합니다.")
    if precision == "unknown" and (birth_time is not None or time_range is not None):
        raise SajuContractError("unknown 시간은 시각을 가질 수 없습니다.")
    if slots["calendar"] == "solar" and slots["leap_month"] is not None:
        raise SajuContractError("양력은 leap_month=null이어야 합니다.")
    if slots["calendar"] == "lunar" and not isinstance(slots["leap_month"], bool):
        raise SajuContractError("음력은 윤달 여부가 필요합니다.")
    if "birth_time" in state["explicit_unknown_fields"] and precision != "unknown":
        raise SajuContractError("시간 미상 표시와 time_precision이 충돌합니다.")


def validate_session_state(state: Mapping[str, Any]) -> None:
    expected = {
        "birth_slots",
        "confirmed_fields",
        "explicit_unknown_fields",
        "field_provenance",
        "chart",
        "request_context",
        "evidence_by_turn",
        "state_revision",
        "last_tool_status",
        "current_intent",
        "saju_opt_in",
    }
    if set(state) != expected:
        raise SajuContractError("session state field 집합이 고정 계약과 다릅니다.")
    if not isinstance(state["birth_slots"], Mapping):
        raise SajuContractError("birth_slots가 object가 아닙니다.")
    if set(state["birth_slots"]) != set(FINGERPRINT_FIELDS):
        raise SajuContractError("birth slot field 집합이 다릅니다.")
    if not isinstance(state["confirmed_fields"], list) or len(
        state["confirmed_fields"]
    ) != len(set(state["confirmed_fields"])):
        raise SajuContractError("confirmed_fields는 중복 없는 list여야 합니다.")
    if not isinstance(state["explicit_unknown_fields"], list) or len(
        state["explicit_unknown_fields"]
    ) != len(set(state["explicit_unknown_fields"])):
        raise SajuContractError("explicit_unknown_fields는 중복 없는 list여야 합니다.")
    if not isinstance(state["field_provenance"], Mapping) or any(
        value not in PROVENANCE_VALUES for value in state["field_provenance"].values()
    ):
        raise SajuContractError("field provenance가 허용 enum이 아닙니다.")
    if "unsupported" in state["field_provenance"].values():
        raise SajuContractError("unsupported provenance가 남아 있습니다.")
    _validate_birth_slots(state)
    chart = state["chart"]
    if not isinstance(chart, Mapping):
        raise SajuContractError("chart state가 object가 아닙니다.")
    if chart.get("chart_valid"):
        expected_fingerprint = birth_input_fingerprint(state)
        if (
            not chart.get("chart_id")
            or chart.get("chart_input_fingerprint") != expected_fingerprint
            or chart.get("chart_policy_version") != CALCULATION_POLICY_ID
            or not isinstance(chart.get("hard_facts"), Mapping)
        ):
            raise SajuContractError(
                "유효한 chart의 ID·fingerprint·policy·facts가 일치하지 않습니다."
            )
    elif any(
        chart.get(key) is not None
        for key in (
            "chart_id",
            "chart_input_fingerprint",
            "chart_policy_version",
            "hard_facts",
        )
    ):
        raise SajuContractError("무효 chart는 캐시된 값을 가질 수 없습니다.")
    request = state["request_context"]
    if not isinstance(request, Mapping):
        raise SajuContractError("request_context가 object가 아닙니다.")
    period = request.get("normalized_period")
    if period is not None:
        if not request.get("reference_datetime") or not request.get("timezone"):
            raise SajuContractError(
                "정규화 period에는 기준 시각과 timezone이 필요합니다."
            )
        if period.get("normalizer_version") != RELATIVE_DATE_POLICY_VERSION:
            raise SajuContractError("relative-date normalizer version이 다릅니다.")
    if state["last_tool_status"] not in {
        None,
        "ok",
        "partial",
        "error",
        "blocked",
    }:
        raise SajuContractError("last_tool_status가 허용 enum이 아닙니다.")
    if not isinstance(state["state_revision"], int) or state["state_revision"] < 0:
        raise SajuContractError("state_revision은 0 이상 integer여야 합니다.")
    if not isinstance(state["saju_opt_in"], bool):
        raise SajuContractError("saju_opt_in은 boolean이어야 합니다.")


def _validate_birthplace(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "country_code",
        "city",
        "timezone",
        "longitude",
        "latitude",
    }:
        raise SajuContractError("birthplace object field가 정확하지 않습니다.")
    if not re.fullmatch(r"[A-Z]{2}", str(value["country_code"])):
        raise SajuContractError("birthplace country_code가 ISO alpha-2가 아닙니다.")
    if not isinstance(value["city"], str) or not value["city"].strip():
        raise SajuContractError("birthplace city가 비어 있습니다.")
    _validate_timezone(value["timezone"])
    for key, minimum, maximum in (("longitude", -180, 180), ("latitude", -90, 90)):
        coordinate = value[key]
        if coordinate is not None and (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not minimum <= coordinate <= maximum
        ):
            raise SajuContractError(f"birthplace {key}가 허용 범위를 벗어났습니다.")


def validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> None:
    if name == "calculate_saju_chart":
        expected = {
            "birth_date",
            "calendar",
            "leap_month",
            "birth_time",
            "time_precision",
            "time_range",
            "birthplace",
            "gender_for_daeun",
        }
        if set(arguments) != expected:
            raise SajuContractError("chart tool argument field 집합이 다릅니다.")
        if arguments["calendar"] not in {"solar", "lunar"}:
            raise SajuContractError("calendar enum이 잘못됐습니다.")
        _validate_birth_date(str(arguments["birth_date"]), arguments["calendar"])
        if arguments["calendar"] == "solar" and arguments["leap_month"] is not None:
            raise SajuContractError("양력은 leap_month=null이어야 합니다.")
        if arguments["calendar"] == "lunar" and not isinstance(
            arguments["leap_month"], bool
        ):
            raise SajuContractError("음력은 leap_month boolean이 필요합니다.")
        precision = arguments["time_precision"]
        birth_time = arguments["birth_time"]
        time_range = arguments["time_range"]
        if precision == "exact":
            if (
                not isinstance(birth_time, str)
                or not TIME_PATTERN.fullmatch(birth_time)
                or time_range is not None
            ):
                raise SajuContractError("exact 시간 인자가 잘못됐습니다.")
        elif precision == "range":
            if (
                birth_time is not None
                or not isinstance(time_range, Mapping)
                or set(time_range) != {"start", "end"}
            ):
                raise SajuContractError("range 시간 인자가 잘못됐습니다.")
            if not all(
                isinstance(time_range[key], str)
                and TIME_PATTERN.fullmatch(time_range[key])
                for key in ("start", "end")
            ):
                raise SajuContractError("time_range가 HH:MM 형식이 아닙니다.")
        elif precision == "unknown":
            if birth_time is not None or time_range is not None:
                raise SajuContractError("unknown 시간에 시각을 생성했습니다.")
        else:
            raise SajuContractError("time_precision enum이 잘못됐습니다.")
        _validate_birthplace(arguments["birthplace"])
        if arguments["gender_for_daeun"] not in {"male", "female", "unspecified"}:
            raise SajuContractError("gender_for_daeun enum이 잘못됐습니다.")
        return
    if name == "calculate_saju_period":
        expected = {"chart_id", "period_type", "start_date", "end_date", "timezone"}
        if set(arguments) != expected:
            raise SajuContractError("period tool argument field 집합이 다릅니다.")
        if not isinstance(arguments["chart_id"], str) or not arguments["chart_id"]:
            raise SajuContractError("period tool에 chart_id가 필요합니다.")
        if arguments["period_type"] not in {"day", "week", "month", "year"}:
            raise SajuContractError("period_type enum이 잘못됐습니다.")
        start = _parse_date(str(arguments["start_date"]), "start_date")
        end_value = arguments["end_date"]
        if end_value is not None and _parse_date(str(end_value), "end_date") < start:
            raise SajuContractError("end_date가 start_date보다 빠릅니다.")
        _validate_timezone(arguments["timezone"])
        return
    raise SajuContractError(f"허용되지 않은 tool입니다: {name}")


def validate_argument_provenance(
    arguments: Mapping[str, Any],
    provenance: Mapping[str, str],
) -> None:
    def leaves(value: Any, prefix: str = "") -> list[str]:
        if isinstance(value, Mapping):
            result: list[str] = []
            for key in sorted(value):
                result.extend(leaves(value[key], f"{prefix}.{key}" if prefix else key))
            return result
        return [prefix]

    expected = set(leaves(arguments))
    if set(provenance) != expected:
        missing = sorted(expected - set(provenance))
        extra = sorted(set(provenance) - expected)
        raise SajuContractError(
            f"tool provenance leaf가 다릅니다: missing={missing}, extra={extra}"
        )
    invalid = {
        path: value
        for path, value in provenance.items()
        if value not in PROVENANCE_VALUES or value == "unsupported"
    }
    if invalid:
        raise SajuContractError(f"허용할 수 없는 tool provenance가 있습니다: {invalid}")


def render_runtime_system_message(
    state: Mapping[str, Any],
    *,
    prompt_path: Path | None = None,
) -> str:
    validate_session_state(state)
    path = prompt_path or RUNTIME_CONFIG_ROOT / "production_system_prompt_v1.txt"
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise SajuContractError("production system prompt를 읽을 수 없습니다.") from exc
    if not prompt:
        raise SajuContractError("production system prompt가 비어 있습니다.")
    return f"{prompt}\n<runtime_context>{canonical_json_bytes(state).decode('utf-8')}</runtime_context>"


def evidence_values(messages: Sequence[Mapping[str, Any]]) -> set[str]:
    """tool result에 실제로 존재하는 scalar를 근거 검사용으로 반환한다."""
    values: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
        elif value is not None and not isinstance(value, bool):
            values.add(str(value))

    for message in messages:
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        try:
            parsed = json.loads(message["content"])
        except json.JSONDecodeError:
            continue
        walk(parsed)
    return values
