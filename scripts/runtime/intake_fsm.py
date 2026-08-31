# intake_fsm.py - 자유문 파싱 없이 구조화 event로 사주 입력·계산 handoff를 전이한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from typing import Any

from scripts.runtime.calculation.id_signer import RuntimeIdSigner

FSM_VERSION = "saju-intake-fsm-v1.1.0"
SESSION_SCHEMA_VERSION = "saju-session-state-v2.1"
DECISION_ACTIONS = frozenset(
    {
        "ask_birth_date",
        "ask_calendar",
        "ask_leap_month",
        "ask_time_precision",
        "ask_exact_time_or_range",
        "ask_birthplace",
        "call_chart",
        "explain_runtime_blocked",
        "call_period",
        "render_result",
    }
)
EVENT_TYPES = frozenset(
    {
        "opt_in",
        "set_slot",
        "correct_slot",
        "set_time_unknown",
        "request_chart",
        "chart_result",
        "request_period",
        "period_result",
        "reset",
    }
)
RUNTIME_STATUS_FIELDS = frozenset(
    {
        "runtime_release_ready",
        "feature_enabled",
        "production_id_key_ready",
        "fsm_gate_passed",
        "encrypted_persistence_ready",
        "retention_policy_ready",
    }
)
SLOT_FIELDS = frozenset(
    {
        "birth_date",
        "calendar",
        "leap_month",
        "birth_time",
        "time_precision",
        "time_range",
        "birthplace",
    }
)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
HMAC_ID_PATTERNS = {
    "chart_id": re.compile(r"^sc2_[0-9a-f]{64}$"),
    "chart_set_id": re.compile(r"^scs2_[0-9a-f]{64}$"),
    "calculation_run_id": re.compile(r"^scr2_[0-9a-f]{64}$"),
    "chart_input_fingerprint": re.compile(r"^sif2_[0-9a-f]{64}$"),
}
PROVENANCE_VALUES = frozenset(
    {"user_explicit", "deterministic_default", "derived_from_policy"}
)


class IntakeFsmError(RuntimeError):
    """구조화 intake event·state 계약 위반."""


def empty_intake_state() -> dict[str, Any]:
    return {
        "session_schema_version": SESSION_SCHEMA_VERSION,
        "fsm_version": FSM_VERSION,
        "saju_opt_in": False,
        "birth_slots": {
            "birth_date": None,
            "calendar": None,
            "leap_month": None,
            "birth_time": None,
            "time_precision": None,
            "time_range": None,
            "birthplace": None,
            "timezone": None,
            "gender_for_daeun": "unspecified",
        },
        "confirmed_fields": [],
        "explicit_unknown_fields": [],
        "field_provenance": {"gender_for_daeun": "deterministic_default"},
        "chart": {
            "chart_id": None,
            "chart_set_id": None,
            "chart_valid": False,
            "chart_input_fingerprint": None,
            "hard_facts": None,
            "fact_authority": None,
        },
        "period": {"request": None, "result": None},
        "current_intent": None,
        "last_tool_status": None,
        "state_revision": 0,
    }


def _clear_calculated(state: dict[str, Any]) -> None:
    state["chart"] = {
        "chart_id": None,
        "chart_set_id": None,
        "chart_valid": False,
        "chart_input_fingerprint": None,
        "hard_facts": None,
        "fact_authority": None,
    }
    state["period"] = {"request": None, "result": None}
    state["last_tool_status"] = None


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return 1900 <= parsed.year <= 2049


def _valid_range(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"start", "end"}
        and all(isinstance(value[key], str) and TIME_PATTERN.fullmatch(value[key]) for key in value)
        and value["start"] <= value["end"]
    )


def _valid_birthplace(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == {"country_code", "city", "timezone", "longitude", "latitude"}
        and value["country_code"] == "KR"
        and isinstance(value["city"], str)
        and bool(value["city"].strip())
        and value["timezone"] == "Asia/Seoul"
        and (
            value["longitude"] is None
            or (
                not isinstance(value["longitude"], bool)
                and isinstance(value["longitude"], (int, float))
                and -180 <= value["longitude"] <= 180
            )
        )
        and (
            value["latitude"] is None
            or (
                not isinstance(value["latitude"], bool)
                and isinstance(value["latitude"], (int, float))
                and -90 <= value["latitude"] <= 90
            )
        )
    )


def _validate_slot(field: str, value: Any) -> None:
    valid = False
    if field == "birth_date":
        valid = _valid_date(value)
    elif field == "calendar":
        valid = isinstance(value, str) and value in {"solar", "lunar"}
    elif field == "leap_month":
        valid = isinstance(value, bool)
    elif field == "birth_time":
        valid = isinstance(value, str) and TIME_PATTERN.fullmatch(value) is not None
    elif field == "time_precision":
        valid = isinstance(value, str) and value in {"exact", "range"}
    elif field == "time_range":
        valid = _valid_range(value)
    elif field == "birthplace":
        valid = _valid_birthplace(value)
    if not valid:
        raise IntakeFsmError(f"구조화 slot 값이 계약과 다릅니다: {field}")


def _validate_state(state: Mapping[str, Any], signer: RuntimeIdSigner) -> None:
    if not isinstance(state, Mapping):
        raise IntakeFsmError("intake state는 object여야 합니다.")
    expected = set(empty_intake_state())
    if set(state) != expected:
        raise IntakeFsmError("intake state field 집합이 다릅니다.")
    if (
        state.get("session_schema_version") != SESSION_SCHEMA_VERSION
        or state.get("fsm_version") != FSM_VERSION
        or not isinstance(state.get("saju_opt_in"), bool)
        or isinstance(state.get("state_revision"), bool)
        or not isinstance(state.get("state_revision"), int)
        or state["state_revision"] < 0
    ):
        raise IntakeFsmError("intake state version·revision이 다릅니다.")
    if not state["saju_opt_in"] and state != empty_intake_state():
        raise IntakeFsmError("opt-in하지 않은 state에는 출생·계산 상태를 저장할 수 없습니다.")
    slots = state.get("birth_slots")
    if not isinstance(slots, Mapping) or set(slots) != set(empty_intake_state()["birth_slots"]):
        raise IntakeFsmError("intake birth_slots field 집합이 다릅니다.")
    if slots.get("gender_for_daeun") != "unspecified":
        raise IntakeFsmError("v1 FSM은 대운 성별을 묻지 않고 unspecified로 고정합니다.")
    timezone = slots.get("timezone")
    if timezone is not None and (
        not isinstance(timezone, str) or timezone != "Asia/Seoul"
    ):
        raise IntakeFsmError("v1 FSM timezone은 Asia/Seoul만 허용합니다.")
    precision = slots.get("time_precision")
    if precision is not None and (
        not isinstance(precision, str)
        or precision not in {"exact", "range", "unknown"}
    ):
        raise IntakeFsmError("time_precision 값이 다릅니다.")
    if precision == "exact" and slots.get("time_range") is not None:
        raise IntakeFsmError("exact 시간과 range가 함께 저장됐습니다.")
    if precision == "range" and slots.get("birth_time") is not None:
        raise IntakeFsmError("range 시간과 exact 값이 함께 저장됐습니다.")
    if precision == "unknown" and (
        slots.get("birth_time") is not None or slots.get("time_range") is not None
    ):
        raise IntakeFsmError("시간 미상 state에 시각 값이 남아 있습니다.")
    if slots.get("birth_time") is not None and precision != "exact":
        raise IntakeFsmError("정확한 출생시각은 exact precision에서만 저장할 수 있습니다.")
    if slots.get("time_range") is not None and precision != "range":
        raise IntakeFsmError("출생시각 범위는 range precision에서만 저장할 수 있습니다.")
    if slots.get("birth_date") is not None and not _valid_date(slots["birth_date"]):
        raise IntakeFsmError("state birth_date가 지원 범위·형식과 다릅니다.")
    calendar = slots.get("calendar")
    if calendar is not None and (
        not isinstance(calendar, str) or calendar not in {"solar", "lunar"}
    ):
        raise IntakeFsmError("state calendar 값이 다릅니다.")
    if slots.get("calendar") == "solar" and slots.get("leap_month") is not None:
        raise IntakeFsmError("양력 state에 윤달 값이 남아 있습니다.")
    if slots.get("leap_month") is not None and slots.get("calendar") != "lunar":
        raise IntakeFsmError("윤달 값은 음력 state에서만 저장할 수 있습니다.")
    if slots.get("leap_month") is not None and not isinstance(
        slots["leap_month"], bool
    ):
        raise IntakeFsmError("state leap_month는 boolean이어야 합니다.")
    if slots.get("birth_time") is not None and (
        not isinstance(slots["birth_time"], str)
        or TIME_PATTERN.fullmatch(slots["birth_time"]) is None
    ):
        raise IntakeFsmError("state birth_time 형식이 다릅니다.")
    if slots.get("time_range") is not None and not _valid_range(slots["time_range"]):
        raise IntakeFsmError("state time_range 형식이 다릅니다.")
    if slots.get("birthplace") is not None and not _valid_birthplace(
        slots["birthplace"]
    ):
        raise IntakeFsmError("state birthplace 형식이 다릅니다.")
    if slots.get("birthplace") is not None and slots.get("timezone") != slots[
        "birthplace"
    ]["timezone"]:
        raise IntakeFsmError("state birthplace와 timezone이 다릅니다.")
    if slots.get("birthplace") is None and slots.get("timezone") is not None:
        raise IntakeFsmError("출생지 없는 state에 timezone 값이 있습니다.")
    for key in ("confirmed_fields", "explicit_unknown_fields"):
        value = state.get(key)
        if (
            not isinstance(value, list)
            or any(not isinstance(item, str) for item in value)
            or len(value) != len(set(value))
        ):
            raise IntakeFsmError(f"{key}는 중복 없는 list여야 합니다.")
    confirmed = set(state["confirmed_fields"])
    explicit_unknown = set(state["explicit_unknown_fields"])
    if not confirmed.issubset(SLOT_FIELDS) or not explicit_unknown.issubset(
        {"birth_time"}
    ):
        raise IntakeFsmError("state 확인·미상 field가 허용 집합 밖입니다.")
    provenance = state.get("field_provenance")
    if (
        not isinstance(provenance, Mapping)
        or not set(provenance).issubset(
            {*SLOT_FIELDS, "timezone", "gender_for_daeun"}
        )
        or any(
            not isinstance(value, str) or value not in PROVENANCE_VALUES
            for value in provenance.values()
        )
    ):
        raise IntakeFsmError("state field provenance가 다릅니다.")
    expected_confirmed = {
        field for field in SLOT_FIELDS if slots.get(field) is not None
    }
    if confirmed != expected_confirmed or any(
        provenance.get(field) != "user_explicit" for field in confirmed
    ):
        raise IntakeFsmError("state 확인 field와 실제 slot·provenance가 다릅니다.")
    if (precision == "unknown" and explicit_unknown != {"birth_time"}) or (
        precision != "unknown" and explicit_unknown
    ):
        raise IntakeFsmError("시간 미상 표식이 time_precision과 다릅니다.")
    allowed_provenance = confirmed | {"gender_for_daeun"}
    if slots.get("calendar") == "solar":
        allowed_provenance.add("leap_month")
        if provenance.get("leap_month") != "deterministic_default":
            raise IntakeFsmError("양력 leap_month 기본값 provenance가 다릅니다.")
    if slots.get("birthplace") is not None:
        allowed_provenance.add("timezone")
        if provenance.get("timezone") != "derived_from_policy":
            raise IntakeFsmError("timezone provenance가 다릅니다.")
    elif "timezone" in provenance:
        raise IntakeFsmError("출생지 없는 state에 timezone provenance가 있습니다.")
    if (
        provenance.get("gender_for_daeun") != "deterministic_default"
        or set(provenance) != allowed_provenance
    ):
        raise IntakeFsmError("state provenance field 집합이 slot 상태와 다릅니다.")
    chart = state.get("chart")
    if not isinstance(chart, Mapping) or set(chart) != set(empty_intake_state()["chart"]):
        raise IntakeFsmError("chart state field 집합이 다릅니다.")
    if not isinstance(chart.get("chart_valid"), bool):
        raise IntakeFsmError("chart_valid는 boolean이어야 합니다.")
    if chart.get("chart_valid"):
        exact_chart = chart.get("chart_id") is not None
        if (
            not isinstance(chart.get("chart_input_fingerprint"), str)
            or HMAC_ID_PATTERNS["chart_input_fingerprint"].fullmatch(
                chart["chart_input_fingerprint"]
            )
            is None
            or not isinstance(chart.get("hard_facts"), Mapping)
            or not (chart.get("chart_id") or chart.get("chart_set_id"))
            or bool(chart.get("chart_id")) == bool(chart.get("chart_set_id"))
            or (
                chart.get("chart_id") is not None
                and HMAC_ID_PATTERNS["chart_id"].fullmatch(str(chart["chart_id"]))
                is None
            )
            or (
                chart.get("chart_set_id") is not None
                and HMAC_ID_PATTERNS["chart_set_id"].fullmatch(
                    str(chart["chart_set_id"])
                )
                is None
            )
            or chart.get("fact_authority") not in {"HARD_GT", "POLICY_BOUND_RULE"}
            or (
                chart.get("chart_id") is not None
                and chart.get("fact_authority") != "HARD_GT"
            )
            or (
                chart.get("chart_set_id") is not None
                and chart.get("fact_authority") != "POLICY_BOUND_RULE"
            )
            or (precision == "exact") != exact_chart
            or precision not in {"exact", "range", "unknown"}
            or slots.get("birth_date") is None
            or slots.get("calendar") is None
            or (slots.get("calendar") == "lunar" and slots.get("leap_month") is None)
            or slots.get("birthplace") is None
            or chart.get("chart_input_fingerprint")
            != signer.chart_input_fingerprint(_chart_arguments(slots))
        ):
            raise IntakeFsmError("유효 chart state의 HMAC ID·사실이 불완전합니다.")
    elif any(value is not None for key, value in chart.items() if key != "chart_valid"):
        raise IntakeFsmError("무효 chart state에 계산값이 남아 있습니다.")
    period = state.get("period")
    if not isinstance(period, Mapping) or set(period) != {"request", "result"}:
        raise IntakeFsmError("period state field 집합이 다릅니다.")
    if period["request"] is not None:
        _validate_period_request(period["request"])
    if period["result"] is not None and (
        not isinstance(period["result"], Mapping)
        or set(period["result"]) != {"hard_facts", "fact_authority"}
        or not isinstance(period["result"]["hard_facts"], Mapping)
        or period["result"]["fact_authority"] != "HARD_GT"
    ):
        raise IntakeFsmError("period result state가 다릅니다.")
    current_intent = state.get("current_intent")
    if current_intent is not None and (
        not isinstance(current_intent, str)
        or current_intent not in {"chart", "period"}
    ):
        raise IntakeFsmError("current_intent 값이 다릅니다.")
    last_tool_status = state.get("last_tool_status")
    if last_tool_status is not None and (
        not isinstance(last_tool_status, str)
        or last_tool_status not in {"ok", "partial", "blocked", "error"}
    ):
        raise IntakeFsmError("last_tool_status 값이 다릅니다.")


def _validate_runtime_status(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or set(value) != RUNTIME_STATUS_FIELDS or any(
        not isinstance(value[key], bool) for key in RUNTIME_STATUS_FIELDS
    ):
        raise IntakeFsmError("runtime_status field·boolean 계약이 다릅니다.")


def _runtime_ready(value: Mapping[str, bool]) -> bool:
    return all(value[field] for field in RUNTIME_STATUS_FIELDS)


def _decision(action: str, *, reason_code: str | None = None, **extra: Any) -> dict[str, Any]:
    if action not in DECISION_ACTIONS:
        raise IntakeFsmError("FSM decision action이 허용 집합 밖입니다.")
    messages = {
        "ask_birth_date": "출생일을 YYYY-MM-DD 형식으로 알려주세요.",
        "ask_calendar": "출생일이 양력인지 음력인지 알려주세요.",
        "ask_leap_month": "음력 생일이 윤달인지 평달인지 알려주세요.",
        "ask_time_precision": "출생시각을 정확히 아는지, 범위만 아는지, 모르는지 알려주세요.",
        "ask_exact_time_or_range": "정확한 HH:MM 시각 또는 시작·끝 시각 범위를 알려주세요.",
        "ask_birthplace": "출생 도시를 선택해주세요. 현재는 대한민국·Asia/Seoul만 지원합니다.",
        "call_chart": "확인된 입력으로 만세력 계산을 요청합니다.",
        "explain_runtime_blocked": "계산 결과를 만들 준비가 완료되지 않아 임의 결과를 생성하지 않습니다.",
        "call_period": "확정된 원국 ID로 기간 간지 계산을 요청합니다.",
        "render_result": "계산기가 반환한 구조화 결과를 현재 화면에 표시합니다.",
    }
    return {
        "action": action,
        "template_id": f"{FSM_VERSION}:{action}",
        "message": messages[action],
        "reason_code": reason_code,
        **extra,
    }


def _chart_arguments(slots: Mapping[str, Any]) -> dict[str, Any]:
    precision = slots["time_precision"]
    return {
        "birth_date": slots["birth_date"],
        "calendar": slots["calendar"],
        "leap_month": slots["leap_month"],
        "birth_time": slots["birth_time"] if precision == "exact" else None,
        "time_precision": precision,
        "time_range": slots["time_range"] if precision == "range" else None,
        "birthplace": deepcopy(slots["birthplace"]),
        "gender_for_daeun": "unspecified",
    }


def _call_id(
    signer: RuntimeIdSigner,
    *,
    call_kind: str,
    state_revision: int,
    arguments: Mapping[str, Any],
) -> str:
    return signer.calculation_run_id(
        {
            "fsm_version": FSM_VERSION,
            "call_kind": call_kind,
            "state_revision": state_revision,
            "arguments": arguments,
        }
    )


def _next_decision(
    state: Mapping[str, Any],
    runtime_status: Mapping[str, bool],
    signer: RuntimeIdSigner,
) -> dict[str, Any]:
    if not state["saju_opt_in"]:
        return _decision("explain_runtime_blocked", reason_code="SAJU_OPT_IN_REQUIRED")
    slots = state["birth_slots"]
    if slots["birth_date"] is None:
        return _decision("ask_birth_date")
    if slots["calendar"] is None:
        return _decision("ask_calendar")
    if slots["calendar"] == "lunar" and slots["leap_month"] is None:
        return _decision("ask_leap_month")
    if slots["time_precision"] is None:
        return _decision("ask_time_precision")
    if slots["time_precision"] == "exact" and slots["birth_time"] is None:
        return _decision("ask_exact_time_or_range")
    if slots["time_precision"] == "range" and slots["time_range"] is None:
        return _decision("ask_exact_time_or_range")
    if slots["birthplace"] is None:
        return _decision("ask_birthplace")
    if state["current_intent"] == "period":
        if not state["chart"]["chart_valid"] or state["chart"]["chart_id"] is None:
            return _decision(
                "explain_runtime_blocked", reason_code="PERIOD_REQUIRES_EXACT_CHART"
            )
        if not _runtime_ready(runtime_status):
            return _decision("explain_runtime_blocked", reason_code="APP_RUNTIME_NOT_READY")
        request = state["period"]["request"]
        arguments = {"chart_id": state["chart"]["chart_id"], **deepcopy(request)}
        return _decision(
            "call_period",
            tool_name="calculate_saju_period",
            call_id=_call_id(
                signer,
                call_kind="period",
                state_revision=state["state_revision"],
                arguments=arguments,
            ),
            arguments=arguments,
        )
    if state["chart"]["chart_valid"]:
        return _decision(
            "render_result",
            result_kind="chart",
            payload={
                "hard_facts": deepcopy(state["chart"]["hard_facts"]),
                "fact_authority": state["chart"]["fact_authority"],
            },
        )
    if not _runtime_ready(runtime_status):
        return _decision("explain_runtime_blocked", reason_code="APP_RUNTIME_NOT_READY")
    arguments = _chart_arguments(slots)
    return _decision(
        "call_chart",
        tool_name="calculate_saju_chart",
        call_id=_call_id(
            signer,
            call_kind="chart",
            state_revision=state["state_revision"],
            arguments=arguments,
        ),
        arguments=arguments,
    )


def _set_slot(state: dict[str, Any], field: str, value: Any, *, correction: bool) -> None:
    if field not in SLOT_FIELDS:
        raise IntakeFsmError("FSM이 허용하지 않는 slot입니다.")
    _validate_slot(field, value)
    if correction and state["birth_slots"][field] is None:
        raise IntakeFsmError("값이 없는 slot은 correction event로 설정할 수 없습니다.")
    changed = state["birth_slots"][field] != value
    if changed:
        state["birth_slots"][field] = deepcopy(value)
        _clear_calculated(state)
        state["state_revision"] += 1
    if field == "calendar" and value == "solar":
        state["birth_slots"]["leap_month"] = None
        state["field_provenance"]["leap_month"] = "deterministic_default"
        state["confirmed_fields"] = [
            item for item in state["confirmed_fields"] if item != "leap_month"
        ]
    elif (
        field == "calendar"
        and value == "lunar"
        and state["birth_slots"]["leap_month"] is None
    ):
        state["field_provenance"].pop("leap_month", None)
    if field == "time_precision":
        if value == "exact":
            state["birth_slots"]["time_range"] = None
            state["field_provenance"].pop("time_range", None)
            state["confirmed_fields"] = [
                item for item in state["confirmed_fields"] if item != "time_range"
            ]
        else:
            state["birth_slots"]["birth_time"] = None
            state["field_provenance"].pop("birth_time", None)
            state["confirmed_fields"] = [
                item for item in state["confirmed_fields"] if item != "birth_time"
            ]
        state["explicit_unknown_fields"] = []
    if field in {"birth_time", "time_range"}:
        state["explicit_unknown_fields"] = []
    if field == "birthplace":
        state["birth_slots"]["timezone"] = value["timezone"]
        state["field_provenance"]["timezone"] = "derived_from_policy"
    state["field_provenance"][field] = "user_explicit"
    state["confirmed_fields"] = sorted({*state["confirmed_fields"], field})


def _validate_period_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "period_type",
        "start_date",
        "end_date",
        "timezone",
    }:
        raise IntakeFsmError("기간 요청 field 집합이 다릅니다.")
    if not isinstance(value["period_type"], str) or value["period_type"] not in {
        "day",
        "week",
        "month",
        "year",
    }:
        raise IntakeFsmError("period_type이 다릅니다.")
    if not _valid_date(value["start_date"]):
        raise IntakeFsmError("기간 시작일이 지원 범위 밖입니다.")
    if value["end_date"] is not None and not _valid_date(value["end_date"]):
        raise IntakeFsmError("기간 종료일이 지원 범위 밖입니다.")
    if value["timezone"] != "Asia/Seoul":
        raise IntakeFsmError("기간 timezone은 Asia/Seoul이어야 합니다.")
    if value["end_date"] is not None and value["end_date"] < value["start_date"]:
        raise IntakeFsmError("기간 종료일이 시작일보다 빠릅니다.")
    return deepcopy(dict(value))


def advance_intake(
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    signer: RuntimeIdSigner,
    runtime_status: Mapping[str, bool],
) -> dict[str, Any]:
    """검증된 구조화 event 하나를 적용하고 다음 단일 action을 결정한다."""

    _validate_state(state, signer)
    _validate_runtime_status(runtime_status)
    event_type_value = event.get("type") if isinstance(event, Mapping) else None
    if (
        not isinstance(event, Mapping)
        or not isinstance(event_type_value, str)
        or event_type_value not in EVENT_TYPES
    ):
        raise IntakeFsmError("허용되지 않은 구조화 event입니다.")
    result = deepcopy(dict(state))
    event_type = event_type_value
    allowed_fields = {
        "opt_in": {"type", "accepted"},
        "set_slot": {"type", "field", "value"},
        "correct_slot": {"type", "field", "value"},
        "set_time_unknown": {"type"},
        "request_chart": {"type"},
        "chart_result": {"type", "result"},
        "request_period": {"type", "request"},
        "period_result": {"type", "result"},
        "reset": {"type"},
    }[event_type]
    if set(event) != allowed_fields:
        raise IntakeFsmError("event field 집합이 고정 계약과 다릅니다.")

    if event_type == "reset":
        result = empty_intake_state()
    elif event_type == "opt_in":
        if not isinstance(event["accepted"], bool):
            raise IntakeFsmError("opt_in accepted는 boolean이어야 합니다.")
        if not event["accepted"]:
            result = empty_intake_state()
        else:
            result["saju_opt_in"] = True
            result["current_intent"] = "chart"
    elif event_type in {"set_slot", "correct_slot"}:
        if not isinstance(event["field"], str):
            raise IntakeFsmError("slot field는 문자열이어야 합니다.")
        _set_slot(
            result,
            event["field"],
            event["value"],
            correction=event_type == "correct_slot",
        )
    elif event_type == "set_time_unknown":
        changed = result["birth_slots"]["time_precision"] != "unknown"
        result["birth_slots"].update(
            {"time_precision": "unknown", "birth_time": None, "time_range": None}
        )
        result["field_provenance"]["time_precision"] = "user_explicit"
        result["field_provenance"].pop("birth_time", None)
        result["field_provenance"].pop("time_range", None)
        result["confirmed_fields"] = sorted(
            {
                *(
                    item
                    for item in result["confirmed_fields"]
                    if item not in {"birth_time", "time_range"}
                ),
                "time_precision",
            }
        )
        result["explicit_unknown_fields"] = sorted(
            {*result["explicit_unknown_fields"], "birth_time"}
        )
        if changed:
            _clear_calculated(result)
            result["state_revision"] += 1
    elif event_type == "request_chart":
        result["current_intent"] = "chart"
    elif event_type == "request_period":
        result["current_intent"] = "period"
        result["period"]["request"] = _validate_period_request(event["request"])
        result["period"]["result"] = None
        result["state_revision"] += 1
    elif event_type == "chart_result":
        expected_decision = _next_decision(result, runtime_status, signer)
        if expected_decision["action"] != "call_chart":
            raise IntakeFsmError(
                "chart_result는 직전 state가 call_chart일 때만 받을 수 있습니다."
            )
        tool_result = event["result"]
        tool_status = tool_result.get("status") if isinstance(tool_result, Mapping) else None
        if (
            not isinstance(tool_status, str)
            or tool_status not in {
            "ok",
            "partial",
            "blocked",
            "error",
            }
            or not isinstance(tool_result.get("call_id"), str)
            or HMAC_ID_PATTERNS["calculation_run_id"].fullmatch(
                tool_result["call_id"]
            )
            is None
            or tool_result["call_id"] != expected_decision.get("call_id")
        ):
            raise IntakeFsmError("chart_result status가 다릅니다.")
        result["last_tool_status"] = tool_result["status"]
        if tool_result["status"] in {"blocked", "error"}:
            _clear_calculated(result)
            result["last_tool_status"] = tool_result["status"]
            decision = _decision(
                "explain_runtime_blocked",
                reason_code=str(tool_result.get("code") or "CHART_TOOL_FAILED"),
            )
            _validate_state(result, signer)
            return {"session_state": result, "decision": decision}
        required = {"hard_facts", "fact_authority", "chart_id", "chart_set_id"}
        if not required.issubset(tool_result) or not isinstance(
            tool_result["hard_facts"], Mapping
        ):
            raise IntakeFsmError("성공 chart_result에 구조화 사실·ID가 없습니다.")
        chart_id = tool_result["chart_id"]
        chart_set_id = tool_result["chart_set_id"]
        precision = result["birth_slots"]["time_precision"]
        if not (
            (
                isinstance(chart_id, str)
                and HMAC_ID_PATTERNS["chart_id"].fullmatch(chart_id) is not None
                and chart_set_id is None
            )
            or (
                chart_id is None
                and isinstance(chart_set_id, str)
                and HMAC_ID_PATTERNS["chart_set_id"].fullmatch(chart_set_id)
                is not None
            )
        ):
            raise IntakeFsmError("chart_result HMAC ID 종류가 입력 정밀도와 다릅니다.")
        if (
            precision == "exact"
            and (
                chart_id is None
                or tool_result["status"] != "ok"
                or tool_result["fact_authority"] != "HARD_GT"
            )
        ) or (
            precision in {"range", "unknown"}
            and (
                chart_set_id is None
                or tool_result["status"] != "partial"
                or tool_result["fact_authority"] != "POLICY_BOUND_RULE"
            )
        ):
            raise IntakeFsmError("chart_result 권위·ID가 입력 정밀도와 다릅니다.")
        chart_arguments = _chart_arguments(result["birth_slots"])
        result["chart"] = {
            "chart_id": chart_id,
            "chart_set_id": chart_set_id,
            "chart_valid": True,
            "chart_input_fingerprint": signer.chart_input_fingerprint(chart_arguments),
            "hard_facts": deepcopy(tool_result["hard_facts"]),
            "fact_authority": tool_result["fact_authority"],
        }
        result["period"] = {"request": None, "result": None}
        result["current_intent"] = "chart"
    elif event_type == "period_result":
        expected_decision = _next_decision(result, runtime_status, signer)
        if expected_decision["action"] != "call_period":
            raise IntakeFsmError(
                "period_result는 직전 state가 call_period일 때만 받을 수 있습니다."
            )
        tool_result = event["result"]
        tool_status = tool_result.get("status") if isinstance(tool_result, Mapping) else None
        if (
            not isinstance(tool_status, str)
            or tool_status not in {
            "ok",
            "blocked",
            "error",
            }
            or not isinstance(tool_result.get("call_id"), str)
            or HMAC_ID_PATTERNS["calculation_run_id"].fullmatch(
                tool_result["call_id"]
            )
            is None
            or tool_result["call_id"] != expected_decision.get("call_id")
        ):
            raise IntakeFsmError("period_result status가 다릅니다.")
        result["last_tool_status"] = tool_result["status"]
        if tool_result["status"] != "ok":
            result["current_intent"] = "chart"
            decision = _decision(
                "explain_runtime_blocked",
                reason_code=str(tool_result.get("code") or "PERIOD_TOOL_FAILED"),
            )
            _validate_state(result, signer)
            return {"session_state": result, "decision": decision}
        if (
            not isinstance(tool_result.get("hard_facts"), Mapping)
            or tool_result.get("fact_authority") != "HARD_GT"
        ):
            raise IntakeFsmError("성공 period_result에 구조화 사실이 없습니다.")
        result["period"]["result"] = {
            "hard_facts": deepcopy(tool_result["hard_facts"]),
            "fact_authority": tool_result.get("fact_authority"),
        }
        result["current_intent"] = "chart"
        decision = _decision(
            "render_result",
            result_kind="period",
            payload=deepcopy(result["period"]["result"]),
        )
        _validate_state(result, signer)
        return {"session_state": result, "decision": decision}

    decision = _next_decision(result, runtime_status, signer)
    _validate_state(result, signer)
    return {"session_state": result, "decision": decision}
