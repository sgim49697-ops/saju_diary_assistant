# intake_fsm_v1_2.py - 공식 근거가 있는 과거 Skyfield 후보 결과만 세션에 수용한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.calculation.contracts import POLICY_ID
from scripts.runtime.calculation.contracts_v1_2 import ID_CONTRACT_VERSION_V2
from scripts.runtime.calculation.contracts_v1_3 import (
    ENGINE_VERSION_V13,
    OUTPUT_SCHEMA_VERSION_V13,
)
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_term_types import (
    PAST_OFFICIAL_CORROBORATED,
    SOURCE_HARD_FACT,
)
from scripts.runtime.intake_fsm import (
    HMAC_ID_PATTERNS,
    SLOT_FIELDS,
    IntakeFsmError,
    _chart_arguments,
    _validate_slot,
)
from scripts.runtime.intake_fsm import (
    _validate_state as _validate_state_v1_1,
)
from scripts.runtime.intake_fsm import (
    empty_intake_state as _empty_intake_state_v1_1,
)

FSM_VERSION = "saju-intake-fsm-v1.2.0"
SESSION_SCHEMA_VERSION = "saju-session-state-v2.2"
CANDIDATE_SCOPE = "historical_official_corroborated_only"
CANDIDATE_RUNTIME_STATUS_FIELDS = frozenset(
    {
        "candidate_runtime_enabled",
        "candidate_id_key_ready",
        "candidate_fsm_gate_passed",
        "ephemeris_ready",
        "loopback_only",
        "ephemeral_session_store",
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
        "reset",
    }
)
PUBLIC_EVENT_TYPES = EVENT_TYPES - {"chart_result"}
DECISION_ACTIONS = frozenset(
    {
        "ask_birth_date",
        "ask_calendar",
        "ask_leap_month",
        "ask_time_precision",
        "ask_exact_time_or_range",
        "ask_birthplace",
        "call_candidate_chart",
        "explain_candidate_blocked",
        "render_candidate",
    }
)
RESULT_FIELDS = frozenset(
    {
        "status",
        "code",
        "message",
        "normalized_input",
        "hard_facts",
        "stable_facts",
        "alternative_charts",
        "uncertainty",
        "fact_authority",
        "birth_input_id",
        "chart_id",
        "chart_set_id",
        "calculation_run_id",
        "engine_version",
        "calculation_schema_version",
        "id_contract_version",
        "policy_id",
        "source_versions",
        "warnings",
        "limitations",
        "internal_trace",
        "call_id",
    }
)
OFFICIAL_CUTOFF_UTC = datetime.fromisoformat(OFFICIAL_SNAPSHOT_COLLECTED_AT).astimezone(
    timezone.utc
)
ISO_UTC_PATTERN = re.compile(r"Z$")


class CandidateEligibilityError(IntakeFsmError):
    """정상 후보 결과가 진단 화면의 보수적 과거 허용 범위를 벗어남."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def empty_intake_state() -> dict[str, Any]:
    state = _empty_intake_state_v1_1()
    state["session_schema_version"] = SESSION_SCHEMA_VERSION
    state["fsm_version"] = FSM_VERSION
    return state


def _empty_chart() -> dict[str, Any]:
    return deepcopy(empty_intake_state()["chart"])


def _clear_calculated(state: dict[str, Any]) -> None:
    state["chart"] = _empty_chart()
    state["period"] = {"request": None, "result": None}
    state["last_tool_status"] = None


def _validate_candidate_runtime_status(value: Mapping[str, Any]) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != CANDIDATE_RUNTIME_STATUS_FIELDS
        or any(type(value[field]) is not bool for field in CANDIDATE_RUNTIME_STATUS_FIELDS)
    ):
        raise IntakeFsmError("candidate_runtime_status field·boolean 계약이 다릅니다.")


def _candidate_ready(value: Mapping[str, bool]) -> bool:
    return all(value[field] for field in CANDIDATE_RUNTIME_STATUS_FIELDS)


def _validate_state(state: Mapping[str, Any], signer: RuntimeIdSigner) -> None:
    if not isinstance(state, Mapping) or set(state) != set(empty_intake_state()):
        raise IntakeFsmError("candidate intake state field 집합이 다릅니다.")
    if (
        state.get("session_schema_version") != SESSION_SCHEMA_VERSION
        or state.get("fsm_version") != FSM_VERSION
    ):
        raise IntakeFsmError("candidate intake state version이 다릅니다.")
    if state.get("period") != {"request": None, "result": None}:
        raise IntakeFsmError("candidate 진단 세션에는 기간 결과를 저장할 수 없습니다.")

    candidate_chart = state.get("chart")
    if not isinstance(candidate_chart, Mapping):
        raise IntakeFsmError("candidate chart state가 object가 아닙니다.")
    parent_state = deepcopy(dict(state))
    parent_state["session_schema_version"] = "saju-session-state-v2.1"
    parent_state["fsm_version"] = "saju-intake-fsm-v1.1.0"
    parent_state["chart"] = deepcopy(_empty_intake_state_v1_1()["chart"])
    _validate_state_v1_1(parent_state, signer)

    if set(candidate_chart) != set(_empty_chart()):
        raise IntakeFsmError("candidate chart state field 집합이 다릅니다.")
    if candidate_chart.get("chart_valid") is not True:
        if candidate_chart != _empty_chart():
            raise IntakeFsmError("무효 candidate chart state에 계산값이 남아 있습니다.")
        return
    chart_id = candidate_chart.get("chart_id")
    chart_set_id = candidate_chart.get("chart_set_id")
    if (
        bool(chart_id) == bool(chart_set_id)
        or (
            chart_id is not None
            and (
                not isinstance(chart_id, str)
                or HMAC_ID_PATTERNS["chart_id"].fullmatch(chart_id) is None
            )
        )
        or (
            chart_set_id is not None
            and (
                not isinstance(chart_set_id, str)
                or HMAC_ID_PATTERNS["chart_set_id"].fullmatch(chart_set_id) is None
            )
        )
        or candidate_chart.get("fact_authority") != "HARD_CANDIDATE"
        or not isinstance(candidate_chart.get("hard_facts"), Mapping)
        or not isinstance(candidate_chart.get("chart_input_fingerprint"), str)
        or HMAC_ID_PATTERNS["chart_input_fingerprint"].fullmatch(
            candidate_chart["chart_input_fingerprint"]
        )
        is None
        or candidate_chart["chart_input_fingerprint"]
        != signer.chart_input_fingerprint(_chart_arguments(state["birth_slots"]))
        or state.get("last_tool_status") != "partial"
    ):
        raise IntakeFsmError("candidate chart state의 HMAC ID·권한이 다릅니다.")
    _validate_evidence(candidate_chart["hard_facts"], label="stored hard_facts")


def _decision(
    action: str, *, reason_code: str | None = None, **extra: Any
) -> dict[str, Any]:
    if action not in DECISION_ACTIONS:
        raise IntakeFsmError("candidate FSM decision action이 허용 집합 밖입니다.")
    messages = {
        "ask_birth_date": "출생일을 YYYY-MM-DD 형식으로 알려주세요.",
        "ask_calendar": "출생일이 양력인지 음력인지 알려주세요.",
        "ask_leap_month": "음력 생일이 윤달인지 평달인지 알려주세요.",
        "ask_time_precision": "출생시각을 정확히 아는지, 범위만 아는지, 모르는지 알려주세요.",
        "ask_exact_time_or_range": "정확한 HH:MM 시각 또는 시작·끝 시각 범위를 알려주세요.",
        "ask_birthplace": "출생 도시를 선택해주세요. 현재는 대한민국·Asia/Seoul만 지원합니다.",
        "call_candidate_chart": "공식 근거 과거 구간 후보 계산을 요청합니다.",
        "explain_candidate_blocked": "후보 진단 범위 밖이므로 계산 결과를 표시하지 않습니다.",
        "render_candidate": "release 전 과거 구간 후보 결과를 진단 화면에 표시합니다.",
    }
    return {
        "action": action,
        "template_id": f"{FSM_VERSION}:{action}",
        "message": messages[action],
        "reason_code": reason_code,
        **extra,
    }


def _call_id(
    signer: RuntimeIdSigner,
    *,
    state_revision: int,
    arguments: Mapping[str, Any],
) -> str:
    return signer.calculation_run_id(
        {
            "fsm_version": FSM_VERSION,
            "call_kind": "historical_candidate_chart",
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
        return _decision(
            "explain_candidate_blocked", reason_code="SAJU_OPT_IN_REQUIRED"
        )
    if state["current_intent"] == "period":
        return _decision(
            "explain_candidate_blocked",
            reason_code="CANDIDATE_PERIOD_OUT_OF_SCOPE",
        )
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
    if state["chart"]["chart_valid"]:
        return _decision(
            "render_candidate",
            candidate_scope=CANDIDATE_SCOPE,
            release_approved=False,
            payload={
                "hard_facts": deepcopy(state["chart"]["hard_facts"]),
                "fact_authority": "HARD_CANDIDATE",
            },
        )
    if not _candidate_ready(runtime_status):
        return _decision(
            "explain_candidate_blocked", reason_code="CANDIDATE_RUNTIME_NOT_READY"
        )
    arguments = _chart_arguments(slots)
    return _decision(
        "call_candidate_chart",
        tool_name="calculate_saju_chart",
        call_id=_call_id(
            signer,
            state_revision=state["state_revision"],
            arguments=arguments,
        ),
        arguments=arguments,
        candidate_scope=CANDIDATE_SCOPE,
        release_approved=False,
    )


def _set_slot(
    state: dict[str, Any], field: str, value: Any, *, correction: bool
) -> None:
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


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or ISO_UTC_PATTERN.search(value) is None:
        raise IntakeFsmError(f"{label} UTC 형식이 다릅니다.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IntakeFsmError(f"{label} UTC 형식이 다릅니다.") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise IntakeFsmError(f"{label} timezone이 UTC가 아닙니다.")
    return parsed


def _validate_evidence(facts: Any, *, label: str) -> None:
    evidence = facts.get("solar_term_evidence") if isinstance(facts, Mapping) else None
    if not isinstance(evidence, Mapping):
        raise IntakeFsmError(f"{label}에 solar_term_evidence가 없습니다.")
    if (
        evidence.get("provider_id") != SkyfieldSolarTermProvider.provider_id
        or evidence.get("official_snapshot_collected_at")
        != OFFICIAL_SNAPSHOT_COLLECTED_AT
        or evidence.get("provider_generated_value_is_official") is not False
        or evidence.get("authority_classes") != [PAST_OFFICIAL_CORROBORATED]
        or evidence.get("overall_authority") != PAST_OFFICIAL_CORROBORATED
        or evidence.get("contains_future_nonapproval") is not False
    ):
        raise CandidateEligibilityError(
            "CANDIDATE_OFFICIAL_EVIDENCE_REQUIRED",
            f"{label}은 과거 공식 근거 단일 권한이 아닙니다.",
        )
    boundaries = evidence.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise IntakeFsmError(f"{label} 절입 경계가 비었습니다.")
    for boundary in boundaries:
        if (
            not isinstance(boundary, Mapping)
            or boundary.get("authority_class") != PAST_OFFICIAL_CORROBORATED
            or boundary.get("official_source_evidence_class") != SOURCE_HARD_FACT
            or boundary.get("provider_generated_value_is_official") is not False
            or _parse_utc(boundary.get("instant_utc"), f"{label} boundary")
            > OFFICIAL_CUTOFF_UTC
        ):
            raise CandidateEligibilityError(
                "CANDIDATE_OFFICIAL_EVIDENCE_REQUIRED",
                f"{label} 절입 경계가 과거 공식 근거 범위를 벗어납니다.",
            )


def _validate_normalized_input(
    normalized: Any, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    if not isinstance(normalized, Mapping):
        raise IntakeFsmError("candidate normalized_input이 object가 아닙니다.")
    birthplace = arguments["birthplace"]
    expected = {
        "calendar": arguments["calendar"],
        "local_birth_date": arguments["birth_date"],
        "lunar_leap_month": (
            arguments["leap_month"] if arguments["calendar"] == "lunar" else None
        ),
        "birth_time_precision": arguments["time_precision"],
        "local_birth_time": arguments["birth_time"],
        "birth_time_range": arguments["time_range"],
        "country_code": birthplace["country_code"],
        "city": birthplace["city"].strip(),
        "iana_time_zone": birthplace["timezone"],
        "policy_id": POLICY_ID,
    }
    if any(normalized.get(key) != value for key, value in expected.items()):
        raise IntakeFsmError("candidate normalized_input이 현재 slot과 다릅니다.")
    solar_date = normalized.get("solar_birth_date")
    try:
        parsed_date = date.fromisoformat(solar_date)
    except (TypeError, ValueError) as exc:
        raise IntakeFsmError("candidate solar_birth_date가 다릅니다.") from exc
    precision = arguments["time_precision"]
    if precision == "exact":
        latest_label = arguments["birth_time"]
    elif precision == "range":
        latest_label = arguments["time_range"]["end"]
    else:
        latest_label = "23:59"
    hour, minute = (int(part) for part in latest_label.split(":"))
    naive = datetime.combine(parsed_date, time(hour, minute))
    zone = ZoneInfo(str(normalized["iana_time_zone"]))
    possible_utc = {
        naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        for fold in (0, 1)
    }
    if any(value > OFFICIAL_CUTOFF_UTC for value in possible_utc):
        raise CandidateEligibilityError(
            "CANDIDATE_AFTER_OFFICIAL_SNAPSHOT",
            "가능한 출생시각이 공식 snapshot 수집시점 이후입니다.",
        )
    return normalized


def _validate_candidate_result(
    tool_result: Any,
    *,
    arguments: Mapping[str, Any],
    expected_call_id: str,
    signer: RuntimeIdSigner,
) -> None:
    if not isinstance(tool_result, Mapping) or set(tool_result) != RESULT_FIELDS:
        raise IntakeFsmError("candidate chart_result field 집합이 다릅니다.")
    if (
        tool_result.get("call_id") != expected_call_id
        or HMAC_ID_PATTERNS["calculation_run_id"].fullmatch(
            str(tool_result.get("call_id", ""))
        )
        is None
        or tool_result.get("engine_version") != ENGINE_VERSION_V13
        or tool_result.get("calculation_schema_version") != OUTPUT_SCHEMA_VERSION_V13
        or tool_result.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or tool_result.get("policy_id") != POLICY_ID
    ):
        raise IntakeFsmError("candidate chart_result 호출·버전 identity가 다릅니다.")
    status = tool_result.get("status")
    if status in {"blocked", "error"}:
        if not isinstance(tool_result.get("code"), str):
            raise IntakeFsmError("차단 candidate 결과에 code가 없습니다.")
        return
    if (
        status != "partial"
        or tool_result.get("code") != "RUNTIME_RELEASE_PENDING"
        or tool_result.get("fact_authority") != "HARD_CANDIDATE"
        or not isinstance(tool_result.get("source_versions"), Mapping)
        or tool_result["source_versions"].get("solar_term_provider")
        != SkyfieldSolarTermProvider.provider_id
    ):
        raise IntakeFsmError("candidate 결과의 status·권한·provider가 다릅니다.")
    normalized = _validate_normalized_input(tool_result["normalized_input"], arguments)
    source_versions = tool_result["source_versions"]
    identity = {
        "normalized_birth_input": normalized,
        "policy_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V13,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "source_versions": source_versions,
    }
    if tool_result.get("birth_input_id") != signer.birth_input_id(normalized):
        raise IntakeFsmError("candidate birth_input_id HMAC이 다릅니다.")
    alternatives = tool_result.get("alternative_charts")
    if not isinstance(alternatives, list) or not alternatives:
        raise IntakeFsmError("candidate alternative_charts가 비었습니다.")
    candidate_ids: list[str] = []
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, Mapping) or not isinstance(
            alternative.get("hard_facts"), Mapping
        ):
            raise IntakeFsmError("candidate alternative chart 형식이 다릅니다.")
        _validate_evidence(
            alternative["hard_facts"], label=f"alternative_charts[{index}]"
        )
        expected_id = signer.chart_id(
            {**identity, "facts": alternative["hard_facts"]}
        )
        if alternative.get("chart_id") != expected_id:
            raise IntakeFsmError("candidate alternative chart_id HMAC이 다릅니다.")
        candidate_ids.append(expected_id)
    if candidate_ids != sorted(set(candidate_ids)):
        raise IntakeFsmError("candidate alternative chart_id 순서·중복이 다릅니다.")
    uncertainty = tool_result.get("uncertainty")
    exact_unique = (
        isinstance(uncertainty, Mapping)
        and uncertainty.get("birth_time_precision") == "exact"
        and uncertainty.get("candidate_count") == 1
        and len(candidate_ids) == 1
    )
    expected_chart_id = candidate_ids[0] if exact_unique else None
    expected_chart_set_id = (
        None
        if exact_unique
        else signer.chart_set_id(
            {**identity, "candidate_chart_ids": candidate_ids}
        )
    )
    if (
        tool_result.get("chart_id") != expected_chart_id
        or tool_result.get("chart_set_id") != expected_chart_set_id
        or tool_result.get("calculation_run_id")
        != signer.calculation_run_id(
            {
                **identity,
                "chart_id": expected_chart_id,
                "chart_set_id": expected_chart_set_id,
            }
        )
    ):
        raise IntakeFsmError("candidate 결과 HMAC ID 집합이 다릅니다.")
    _validate_evidence(tool_result["hard_facts"], label="hard_facts")
    _validate_evidence(tool_result["stable_facts"], label="stable_facts")


def advance_intake(
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    signer: RuntimeIdSigner,
    runtime_status: Mapping[str, bool],
) -> dict[str, Any]:
    """후보 진단 event 하나를 적용하고 다음 단일 action을 결정한다."""

    _validate_state(state, signer)
    _validate_candidate_runtime_status(runtime_status)
    event_type = event.get("type") if isinstance(event, Mapping) else None
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise IntakeFsmError("허용되지 않은 candidate 구조화 event입니다.")
    allowed_fields = {
        "opt_in": {"type", "accepted"},
        "set_slot": {"type", "field", "value"},
        "correct_slot": {"type", "field", "value"},
        "set_time_unknown": {"type"},
        "request_chart": {"type"},
        "chart_result": {"type", "result"},
        "request_period": {"type"},
        "reset": {"type"},
    }[event_type]
    if set(event) != allowed_fields:
        raise IntakeFsmError("candidate event field 집합이 고정 계약과 다릅니다.")
    result = deepcopy(dict(state))

    if event_type == "reset":
        result = empty_intake_state()
    elif event_type == "opt_in":
        if type(event["accepted"]) is not bool:
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
        result["explicit_unknown_fields"] = ["birth_time"]
        if changed:
            _clear_calculated(result)
            result["state_revision"] += 1
    elif event_type == "request_chart":
        result["current_intent"] = "chart"
    elif event_type == "request_period":
        if not result["saju_opt_in"]:
            decision = _decision(
                "explain_candidate_blocked",
                reason_code="CANDIDATE_PERIOD_OUT_OF_SCOPE",
            )
            _validate_state(result, signer)
            return {"session_state": result, "decision": decision}
        result["current_intent"] = "period"
    elif event_type == "chart_result":
        expected = _next_decision(result, runtime_status, signer)
        if expected["action"] != "call_candidate_chart":
            raise IntakeFsmError(
                "chart_result는 직전 state가 call_candidate_chart일 때만 받을 수 있습니다."
            )
        tool_result = event["result"]
        try:
            _validate_candidate_result(
                tool_result,
                arguments=expected["arguments"],
                expected_call_id=expected["call_id"],
                signer=signer,
            )
        except CandidateEligibilityError as exc:
            _clear_calculated(result)
            result["last_tool_status"] = "blocked"
            decision = _decision("explain_candidate_blocked", reason_code=exc.code)
            _validate_state(result, signer)
            return {"session_state": result, "decision": decision}
        if tool_result["status"] in {"blocked", "error"}:
            _clear_calculated(result)
            result["last_tool_status"] = tool_result["status"]
            decision = _decision(
                "explain_candidate_blocked",
                reason_code=str(tool_result["code"]),
            )
            _validate_state(result, signer)
            return {"session_state": result, "decision": decision}
        result["chart"] = {
            "chart_id": tool_result["chart_id"],
            "chart_set_id": tool_result["chart_set_id"],
            "chart_valid": True,
            "chart_input_fingerprint": signer.chart_input_fingerprint(
                expected["arguments"]
            ),
            "hard_facts": deepcopy(tool_result["hard_facts"]),
            "fact_authority": "HARD_CANDIDATE",
        }
        result["period"] = {"request": None, "result": None}
        result["current_intent"] = "chart"
        result["last_tool_status"] = "partial"

    decision = _next_decision(result, runtime_status, signer)
    _validate_state(result, signer)
    return {"session_state": result, "decision": decision}


def assert_public_event(event: Mapping[str, Any]) -> None:
    """HTTP 경계에서 내부 chart_result 주입을 차단한다."""

    event_type = event.get("type") if isinstance(event, Mapping) else None
    if not isinstance(event_type, str) or event_type not in PUBLIC_EVENT_TYPES:
        raise IntakeFsmError("공개 API에서 허용되지 않은 candidate event입니다.")
