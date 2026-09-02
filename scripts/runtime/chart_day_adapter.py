# chart_day_adapter.py - v1.5 원국과 단일 일진을 암호화 구조화 session FSM에 연결한다.

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import POLICY_ID
from scripts.runtime.calculation.contracts_v1_5 import (
    APPROVED_SCOPE_V15,
    ENGINE_VERSION_V15,
    OUTPUT_SCHEMA_VERSION_V15,
    RELEASE_V15_PATH,
)
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.id_signer import ID_CONTRACT_VERSION, RuntimeIdSigner
from scripts.runtime.chart_only_operations_contracts import validate_operations_registry
from scripts.runtime.chart_only_security import (
    EncryptedSessionStore,
    SecretKey,
    assert_key_separation,
    load_secret_key,
)
from scripts.runtime.intake_fsm import HMAC_ID_PATTERNS, TIME_PATTERN

ADAPTER_ID = "saju-chart-day-app-adapter-v1.1.0"
SESSION_SCHEMA_VERSION = "saju-chart-day-session-state-v1.1"
FSM_VERSION = "saju-chart-day-intake-fsm-v1.1.0"
EVENT_TYPES = frozenset(
    {
        "opt_in",
        "set_slot",
        "correct_slot",
        "set_time_unknown",
        "request_chart",
        "request_period",
        "reset",
    }
)
SLOT_FIELDS = frozenset(
    {"calendar", "birth_date", "leap_month", "birth_time", "time_range", "birthplace"}
)
PUBLIC_CHART_FACT_FIELDS = frozenset(
    {
        "pillars",
        "day_master",
        "surface_five_elements",
        "calculation_profile",
        "solar_term_evidence",
    }
)
PUBLIC_PERIOD_FACT_FIELDS = frozenset({"period", "day_assignment_evidence"})
PUBLIC_FORBIDDEN_FIELDS = frozenset(
    {
        "normalized_input",
        "birth_input_id",
        "chart_id",
        "chart_set_id",
        "calculation_run_id",
        "internal_trace",
        "local_birth_date",
        "local_birth_time",
        "birth_date",
        "birth_time",
        "ciphertext",
        "nonce",
    }
)


class ChartDayAdapterError(RuntimeError):
    """원국+단일 일진 adapter event·state·결과 계약 위반."""


def empty_session_state() -> dict[str, Any]:
    return {
        "session_schema_version": SESSION_SCHEMA_VERSION,
        "fsm_version": FSM_VERSION,
        "state_revision": 0,
        "saju_opt_in": False,
        "current_intent": None,
        "birth_slots": {
            "calendar": None,
            "birth_date": None,
            "leap_month": None,
            "time_precision": None,
            "birth_time": None,
            "time_range": None,
            "birthplace": None,
        },
        "chart": None,
        "period": None,
    }


def _clear_calculations(state: dict[str, Any]) -> None:
    state["chart"] = None
    state["period"] = None


def _validate_date(value: Any, *, label: str = "birth_date") -> str:
    if not isinstance(value, str):
        raise ChartDayAdapterError(f"{label}는 ISO 날짜 문자열이어야 합니다.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ChartDayAdapterError(f"{label}가 유효한 ISO 날짜가 아닙니다.") from exc
    if parsed.isoformat() != value:
        raise ChartDayAdapterError(f"{label}는 YYYY-MM-DD 형식이어야 합니다.")
    return value


def _validate_time(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or TIME_PATTERN.fullmatch(value) is None:
        raise ChartDayAdapterError(f"{label}은 HH:MM 형식이어야 합니다.")
    return value


def _normalize_birthplace(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "country_code",
        "city",
        "timezone",
    }:
        raise ChartDayAdapterError("birthplace field 집합이 다릅니다.")
    city = value.get("city")
    if (
        value.get("country_code") != "KR"
        or value.get("timezone") != "Asia/Seoul"
        or not isinstance(city, str)
        or not city.strip()
        or len(city.strip()) > 100
    ):
        raise ChartDayAdapterError("원국 birthplace는 KR·Asia/Seoul이어야 합니다.")
    return {"country_code": "KR", "city": city.strip(), "timezone": "Asia/Seoul"}


def _set_slot(state: dict[str, Any], field: str, value: Any) -> None:
    if field not in SLOT_FIELDS:
        raise ChartDayAdapterError("허용되지 않은 birth slot입니다.")
    slots = state["birth_slots"]
    if field == "calendar":
        if value not in {"solar", "lunar"}:
            raise ChartDayAdapterError("calendar는 solar 또는 lunar여야 합니다.")
        normalized: Any = value
        if value == "solar":
            slots["leap_month"] = None
    elif field == "birth_date":
        normalized = _validate_date(value)
    elif field == "leap_month":
        if type(value) is not bool:
            raise ChartDayAdapterError("leap_month는 boolean이어야 합니다.")
        if slots.get("calendar") != "lunar":
            raise ChartDayAdapterError("leap_month는 lunar에서만 설정할 수 있습니다.")
        normalized = value
    elif field == "birth_time":
        normalized = _validate_time(value, label="birth_time")
        slots["time_precision"] = "exact"
        slots["time_range"] = None
    elif field == "time_range":
        if not isinstance(value, Mapping) or set(value) != {"start", "end"}:
            raise ChartDayAdapterError("time_range field 집합이 다릅니다.")
        start = _validate_time(value.get("start"), label="time_range.start")
        end = _validate_time(value.get("end"), label="time_range.end")
        if start > end:
            raise ChartDayAdapterError("time_range는 같은 날짜 안에서 증가해야 합니다.")
        normalized = {"start": start, "end": end}
        slots["time_precision"] = "range"
        slots["birth_time"] = None
    else:
        normalized = _normalize_birthplace(value)
    if slots.get(field) != normalized:
        slots[field] = normalized
        _clear_calculations(state)
        state["state_revision"] += 1


def _validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict) or set(state) != {
        "birth_slots",
        "chart",
        "period",
        "current_intent",
        "fsm_version",
        "saju_opt_in",
        "session_schema_version",
        "state_revision",
    }:
        raise ChartDayAdapterError("암호화 session state field 집합이 다릅니다.")
    if (
        state.get("session_schema_version") != SESSION_SCHEMA_VERSION
        or state.get("fsm_version") != FSM_VERSION
        or type(state.get("saju_opt_in")) is not bool
        or not isinstance(state.get("state_revision"), int)
        or state["state_revision"] < 0
        or state.get("current_intent") not in {None, "chart", "period"}
    ):
        raise ChartDayAdapterError("암호화 session state identity가 다릅니다.")
    slots = state.get("birth_slots")
    if not isinstance(slots, dict) or set(slots) != {
        "birth_date",
        "birth_time",
        "birthplace",
        "calendar",
        "leap_month",
        "time_precision",
        "time_range",
    }:
        raise ChartDayAdapterError("암호화 birth_slots field 집합이 다릅니다.")
    return state


def _missing_slots(state: Mapping[str, Any]) -> list[str]:
    slots = state["birth_slots"]
    missing: list[str] = []
    for field in ("calendar", "birth_date", "birthplace"):
        if slots[field] is None:
            missing.append(field)
    if slots["calendar"] == "lunar" and type(slots["leap_month"]) is not bool:
        missing.append("leap_month")
    precision = slots["time_precision"]
    if precision is None:
        missing.append("time_precision")
    elif precision == "exact" and slots["birth_time"] is None:
        missing.append("birth_time")
    elif precision == "range" and slots["time_range"] is None:
        missing.append("time_range")
    return missing


def _chart_arguments(state: Mapping[str, Any]) -> dict[str, Any]:
    slots = state["birth_slots"]
    missing = _missing_slots(state)
    if missing:
        raise ChartDayAdapterError(f"원국 계산 필수 slot이 없습니다: {','.join(missing)}")
    birthplace = slots["birthplace"]
    return {
        "birth_date": slots["birth_date"],
        "calendar": slots["calendar"],
        "leap_month": slots["leap_month"] if slots["calendar"] == "lunar" else None,
        "birth_time": slots["birth_time"] if slots["time_precision"] == "exact" else None,
        "time_precision": slots["time_precision"],
        "time_range": slots["time_range"] if slots["time_precision"] == "range" else None,
        "birthplace": {**birthplace, "longitude": None, "latitude": None},
        "gender_for_daeun": "unspecified",
    }


def _validate_normalized_input(normalized: Any, arguments: Mapping[str, Any]) -> None:
    if not isinstance(normalized, Mapping):
        raise ChartDayAdapterError("runtime normalized_input이 object가 아닙니다.")
    expected = {
        "calendar": arguments["calendar"],
        "local_birth_date": arguments["birth_date"],
        "lunar_leap_month": arguments["leap_month"] if arguments["calendar"] == "lunar" else None,
        "birth_time_precision": arguments["time_precision"],
        "local_birth_time": arguments["birth_time"],
        "birth_time_range": arguments["time_range"],
        "country_code": "KR",
        "city": arguments["birthplace"]["city"],
        "iana_time_zone": "Asia/Seoul",
        "policy_id": POLICY_ID,
    }
    if any(normalized.get(key) != value for key, value in expected.items()):
        raise ChartDayAdapterError("runtime normalized_input이 현재 session slot과 다릅니다.")


def _validate_common_result(result: Any, *, release_id: str) -> Mapping[str, Any]:
    if not isinstance(result, Mapping):
        raise ChartDayAdapterError("runtime 결과가 object가 아닙니다.")
    source_versions = result.get("source_versions")
    if (
        result.get("engine_version") != ENGINE_VERSION_V15
        or result.get("calculation_schema_version") != OUTPUT_SCHEMA_VERSION_V15
        or result.get("id_contract_version") != ID_CONTRACT_VERSION
        or result.get("policy_id") != POLICY_ID
        or result.get("runtime_scope") != APPROVED_SCOPE_V15
        or not isinstance(source_versions, Mapping)
        or source_versions.get("runtime_release") != release_id
    ):
        raise ChartDayAdapterError("runtime 결과 version·release identity가 다릅니다.")
    return result


def _validate_chart_result(
    result: Any,
    *,
    arguments: Mapping[str, Any],
    signer: RuntimeIdSigner,
    release_id: str,
) -> None:
    value = _validate_common_result(result, release_id=release_id)
    if value.get("status") == "blocked":
        if not isinstance(value.get("code"), str) or value.get("fact_authority") is not None:
            raise ChartDayAdapterError("차단 원국 결과 권한이 다릅니다.")
        if isinstance(value.get("normalized_input"), Mapping):
            _validate_normalized_input(value["normalized_input"], arguments)
        return
    if value.get("status") not in {"ok", "partial"}:
        raise ChartDayAdapterError("원국 결과 status가 다릅니다.")
    normalized = value.get("normalized_input")
    _validate_normalized_input(normalized, arguments)
    identity = {
        "normalized_birth_input": normalized,
        "policy_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V15,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
        "id_contract_version": ID_CONTRACT_VERSION,
        "runtime_scope": APPROVED_SCOPE_V15,
        "source_versions": value["source_versions"],
    }
    alternatives = value.get("alternative_charts")
    if not isinstance(alternatives, list) or not alternatives:
        raise ChartDayAdapterError("runtime alternative_charts가 비었습니다.")
    candidate_ids: list[str] = []
    for alternative in alternatives:
        if not isinstance(alternative, Mapping) or not isinstance(alternative.get("hard_facts"), Mapping):
            raise ChartDayAdapterError("runtime alternative chart 형식이 다릅니다.")
        expected_id = signer.chart_id({**identity, "facts": alternative["hard_facts"]})
        if alternative.get("chart_id") != expected_id:
            raise ChartDayAdapterError("runtime chart_id HMAC 재검산에 실패했습니다.")
        candidate_ids.append(expected_id)
    if candidate_ids != sorted(set(candidate_ids)):
        raise ChartDayAdapterError("runtime chart_id 순서·중복이 다릅니다.")
    exact = arguments["time_precision"] == "exact" and len(candidate_ids) == 1
    chart_id = candidate_ids[0] if exact else None
    chart_set_id = None if exact else signer.chart_set_id(
        {**identity, "candidate_chart_ids": candidate_ids}
    )
    if (
        value.get("birth_input_id") != signer.birth_input_id(normalized)
        or value.get("chart_id") != chart_id
        or value.get("chart_set_id") != chart_set_id
        or value.get("calculation_run_id")
        != signer.calculation_run_id(
            {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id}
        )
        or value.get("status") != ("ok" if exact else "partial")
        or value.get("fact_authority") != ("HARD_GT" if exact else "POLICY_BOUND_RULE")
    ):
        raise ChartDayAdapterError("runtime 원국 HMAC·precision 권한이 다릅니다.")


def _validate_period_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "period_type",
        "start_date",
        "end_date",
        "timezone",
    }:
        raise ChartDayAdapterError("request_period.request field 집합이 다릅니다.")
    start = _validate_date(value.get("start_date"), label="start_date")
    end = _validate_date(value.get("end_date"), label="end_date")
    if (
        value.get("period_type") != "day"
        or value.get("timezone") != "Asia/Seoul"
        or end != start
    ):
        raise ChartDayAdapterError("단일 일진은 같은 날짜·day·Asia/Seoul만 허용합니다.")
    return {
        "period_type": "day",
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Seoul",
    }


def _validate_period_result(
    result: Any,
    *,
    arguments: Mapping[str, Any],
    signer: RuntimeIdSigner,
    release_id: str,
) -> None:
    value = _validate_common_result(result, release_id=release_id)
    if value.get("status") == "blocked":
        if not isinstance(value.get("code"), str) or value.get("fact_authority") is not None:
            raise ChartDayAdapterError("차단 일진 결과 권한이 다릅니다.")
        return
    hard_facts = value.get("hard_facts")
    period = hard_facts.get("period") if isinstance(hard_facts, Mapping) else None
    evidence = hard_facts.get("day_assignment_evidence") if isinstance(hard_facts, Mapping) else None
    expected_normalized = {
        "period_type": "day",
        "start_date": arguments["start_date"],
        "end_date": arguments["start_date"],
        "timezone": "Asia/Seoul",
        "evaluation_local_time": "12:00",
    }
    preimage = {
        "arguments": {**arguments, "end_date": arguments["start_date"]},
        "hard_facts": hard_facts,
        "engine_version": ENGINE_VERSION_V15,
        "policy_id": POLICY_ID,
        "runtime_scope": APPROVED_SCOPE_V15,
        "source_versions": value["source_versions"],
    }
    if (
        value.get("status") != "ok"
        or value.get("fact_authority") != "HARD_GT"
        or value.get("normalized_input") != expected_normalized
        or value.get("chart_id") != arguments["chart_id"]
        or value.get("chart_set_id") is not None
        or value.get("birth_input_id") is not None
        or value.get("calculation_run_id") != signer.calculation_run_id(preimage)
        or not isinstance(period, Mapping)
        or set(period)
        != {
            "period_type",
            "target_date",
            "timezone",
            "evaluation_local_time",
            "year_ganzhi",
            "month_ganzhi",
            "day_ganzhi",
        }
        or period.get("target_date") != arguments["start_date"]
        or period.get("period_type") != "day"
        or period.get("timezone") != "Asia/Seoul"
        or period.get("evaluation_local_time") != "12:00"
        or not isinstance(evidence, Mapping)
        or evidence.get("authority") != "SOURCE_HARD_FACT"
        or evidence.get("provider_generated_value_is_official") is not False
        or evidence.get("future_physical_instant_claimed") is not False
        or evidence.get("release_id") != release_id
    ):
        raise ChartDayAdapterError("runtime 단일 일진 HMAC·공식 권한이 다릅니다.")


def _public_facts(value: Any, *, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ChartDayAdapterError("runtime hard_facts가 object가 아닙니다.")
    return {key: deepcopy(item) for key, item in value.items() if key in allowed}


def _forbidden_key_present(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in PUBLIC_FORBIDDEN_FIELDS or _forbidden_key_present(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_forbidden_key_present(item) for item in value)
    return False


def _runtime_id_present(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_runtime_id_present(item) for item in value.values())
    if isinstance(value, list):
        return any(_runtime_id_present(item) for item in value)
    if isinstance(value, str):
        return any(
            pattern.fullmatch(value) is not None
            for pattern in (
                HMAC_ID_PATTERNS["chart_id"],
                HMAC_ID_PATTERNS["chart_set_id"],
                HMAC_ID_PATTERNS["calculation_run_id"],
            )
        )
    return False


def assert_public_response(value: Mapping[str, Any]) -> None:
    if _forbidden_key_present(value):
        raise ChartDayAdapterError("공개 adapter 응답에 금지 field가 포함됐습니다.")
    json.dumps(value, ensure_ascii=False, allow_nan=False)
    if _runtime_id_present(value):
        raise ChartDayAdapterError("공개 adapter 응답에 runtime ID가 포함됐습니다.")


def public_chart(chart: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": chart["status"],
        "fact_authority": chart["fact_authority"],
        "hard_facts": _public_facts(chart["hard_facts"], allowed=PUBLIC_CHART_FACT_FIELDS),
        "message": chart["message"],
        "limitations": deepcopy(chart.get("limitations", [])),
    }


def public_period(period: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": period["status"],
        "fact_authority": period["fact_authority"],
        "hard_facts": _public_facts(period["hard_facts"], allowed=PUBLIC_PERIOD_FACT_FIELDS),
        "message": period["message"],
        "limitations": deepcopy(period.get("limitations", [])),
    }


class DisabledChartDayAdapter:
    resources_opened = False

    def status(self) -> dict[str, Any]:
        return {
            "status": "disabled",
            "adapter_version": ADAPTER_ID,
            "feature_enabled": False,
            "resources_opened": False,
            "production_application_binding": False,
        }

    def create_session(self) -> dict[str, Any]:
        raise ChartDayAdapterError("원국+단일 일진 adapter feature가 비활성입니다.")


class ChartDayAppAdapter:
    """실제 v1.5 engine과 encrypted session store를 묶는다."""

    resources_opened = True

    def __init__(
        self,
        *,
        engine: ApprovedSajuRuntimeEngineV15,
        signer: RuntimeIdSigner,
        store: EncryptedSessionStore,
        hmac_key: SecretKey,
        encryption_key: SecretKey,
    ) -> None:
        assert_key_separation(hmac_key, encryption_key)
        if not signer.production_key:
            raise ChartDayAdapterError("adapter에는 production HMAC signer가 필요합니다.")
        if engine.release is None:
            raise ChartDayAdapterError("adapter에는 유효한 v1.5 release가 필요합니다.")
        self.engine = engine
        self.signer = signer
        self.store = store
        self.hmac_key = hmac_key
        self.encryption_key = encryption_key
        self.release_id = engine.release["release_id"]
        self._lock = threading.RLock()

    def __enter__(self) -> ChartDayAppAdapter:  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        self.engine.close()

    def status(self) -> dict[str, Any]:
        return {
            "status": "production_binding_ready",
            "adapter_version": ADAPTER_ID,
            "fsm_version": FSM_VERSION,
            "session_schema_version": SESSION_SCHEMA_VERSION,
            "release_id": self.release_id,
            "feature_enabled": True,
            "feature_default": False,
            "resources_opened": True,
            "encrypted_persistence": True,
            "single_day_calculation_allowed": True,
            "production_application_binding": True,
            "model_context_binding": True,
            "sealed_blind_accessed": False,
        }

    def create_session(self) -> dict[str, Any]:
        with self._lock:
            session_id = self.store.create(empty_session_state())
        return {
            "status": "created",
            "session_id": session_id,
            "adapter_version": ADAPTER_ID,
            "feature_default": False,
            "production_application_binding": True,
        }

    def _public_response(
        self,
        state: Mapping[str, Any],
        *,
        action: str,
        message: str,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        chart = state.get("chart")
        period = state.get("period")
        result = None
        status = "needs_input"
        if action in {"render_chart", "render_period"} and isinstance(chart, Mapping):
            status = "ready"
            result = {
                "chart": public_chart(chart),
                "period": public_period(period) if isinstance(period, Mapping) else None,
            }
        elif action == "explain_blocked":
            status = "blocked"
        response = {
            "status": status,
            "state_revision": state["state_revision"],
            "decision": {"action": action, "message": message, "reason_code": reason_code},
            "result": result,
            "governance": {
                "adapter_version": ADAPTER_ID,
                "release_id": self.release_id,
                "runtime_feature_default": False,
                "encrypted_persistence": True,
                "single_day_calculation_allowed": True,
                "production_application_binding": True,
                "model_context_binding": True,
                "sealed_blind_accessed": False,
            },
        }
        assert_public_response(response)
        return response

    def handle_event(self, session_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._handle_event_unlocked(session_id, event)

    def _handle_event_unlocked(
        self, session_id: str, event: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise ChartDayAdapterError("구조화 event object가 필요합니다.")
        event_type = event.get("type")
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            raise ChartDayAdapterError("허용되지 않은 구조화 event입니다.")
        allowed_fields = {
            "opt_in": {"type", "accepted"},
            "set_slot": {"type", "field", "value"},
            "correct_slot": {"type", "field", "value"},
            "set_time_unknown": {"type"},
            "request_chart": {"type"},
            "request_period": {"type", "request"},
            "reset": {"type"},
        }[event_type]
        if set(event) != allowed_fields:
            raise ChartDayAdapterError("구조화 event field 집합이 다릅니다.")
        state = _validate_state(self.store.read(session_id))
        opt_in_message = "사주 원국 계산 동의가 필요합니다."

        if event_type == "reset":
            state = empty_session_state()
        elif event_type == "opt_in":
            if type(event["accepted"]) is not bool:
                raise ChartDayAdapterError("opt_in accepted는 boolean이어야 합니다.")
            if event["accepted"]:
                state["saju_opt_in"] = True
                state["current_intent"] = "chart"
                state["state_revision"] += 1
            else:
                state = empty_session_state()
        elif event_type in {"set_slot", "correct_slot"}:
            if not isinstance(event["field"], str):
                raise ChartDayAdapterError("slot field는 문자열이어야 합니다.")
            _set_slot(state, event["field"], event["value"])
        elif event_type == "set_time_unknown":
            slots = state["birth_slots"]
            if (slots["time_precision"], slots["birth_time"], slots["time_range"]) != (
                "unknown",
                None,
                None,
            ):
                slots["time_precision"] = "unknown"
                slots["birth_time"] = None
                slots["time_range"] = None
                _clear_calculations(state)
                state["state_revision"] += 1
        elif event_type == "request_chart":
            state["current_intent"] = "chart"
            if not state["saju_opt_in"]:
                self.store.put(session_id, state)
                return self._public_response(
                    state,
                    action="request_opt_in",
                    message=opt_in_message,
                    reason_code="OPT_IN_REQUIRED",
                )
            missing = _missing_slots(state)
            if missing:
                self.store.put(session_id, state)
                return self._public_response(
                    state,
                    action="request_slots",
                    message=f"필수 입력이 더 필요합니다: {','.join(missing)}",
                    reason_code="BIRTH_SLOTS_REQUIRED",
                )
            arguments = _chart_arguments(state)
            result = self.engine.calculate_chart(arguments)
            _validate_chart_result(
                result,
                arguments=arguments,
                signer=self.signer,
                release_id=self.release_id,
            )
            state["chart"] = deepcopy(dict(result))
            state["period"] = None
            state["state_revision"] += 1
            self.store.put(session_id, state)
            if result["status"] == "blocked":
                return self._public_response(
                    state,
                    action="explain_blocked",
                    message=str(result["message"]),
                    reason_code=str(result["code"]),
                )
            return self._public_response(state, action="render_chart", message=str(result["message"]))
        elif event_type == "request_period":
            request = _validate_period_request(event["request"])
            state["current_intent"] = "period"
            chart = state.get("chart")
            if (
                not isinstance(chart, Mapping)
                or chart.get("status") != "ok"
                or chart.get("fact_authority") != "HARD_GT"
                or not isinstance(chart.get("chart_id"), str)
            ):
                state["period"] = None
                state["state_revision"] += 1
                self.store.put(session_id, state)
                return self._public_response(
                    state,
                    action="explain_blocked",
                    message="단일 일진에는 exact 입력으로 확정된 HARD_GT 원국이 필요합니다.",
                    reason_code="EXACT_CHART_REQUIRED",
                )
            arguments = {"chart_id": chart["chart_id"], **request}
            result = self.engine.calculate_period(arguments)
            _validate_period_result(
                result,
                arguments=arguments,
                signer=self.signer,
                release_id=self.release_id,
            )
            state["period"] = deepcopy(dict(result)) if result["status"] == "ok" else None
            state["state_revision"] += 1
            self.store.put(session_id, state)
            if result["status"] != "ok":
                return self._public_response(
                    state,
                    action="explain_blocked",
                    message=str(result["message"]),
                    reason_code=str(result["code"]),
                )
            return self._public_response(state, action="render_period", message=str(result["message"]))

        self.store.put(session_id, state)
        if not state["saju_opt_in"]:
            return self._public_response(
                state,
                action="request_opt_in",
                message=opt_in_message,
                reason_code="OPT_IN_REQUIRED",
            )
        missing = _missing_slots(state)
        if missing:
            return self._public_response(
                state,
                action="request_slots",
                message=f"필수 입력이 더 필요합니다: {','.join(missing)}",
                reason_code="BIRTH_SLOTS_REQUIRED",
            )
        chart = state.get("chart")
        if isinstance(chart, Mapping) and chart.get("status") in {"ok", "partial"}:
            return self._public_response(
                state,
                action="render_chart",
                message=str(chart["message"]),
            )
        return self._public_response(
            state,
            action="request_chart",
            message="구조화 입력이 준비됐습니다. 원국 계산을 요청할 수 있습니다.",
            reason_code="CHART_REQUEST_REQUIRED",
        )


def build_chart_day_app_adapter(
    *,
    enable_adapter: bool = False,
    release_registry: Path | None = None,
    ephemeris_path: Path | None = None,
    hmac_key_file: Path | None = None,
    encryption_key_file: Path | None = None,
    previous_encryption_key_file: Path | None = None,
    store_root: Path | None = None,
) -> DisabledChartDayAdapter | ChartDayAppAdapter:
    """기본 off를 지키고 명시 활성화 때만 v1.5 resource를 연다."""

    validate_operations_registry(require_dependencies=enable_adapter)
    resources = (
        release_registry,
        ephemeris_path,
        hmac_key_file,
        encryption_key_file,
        previous_encryption_key_file,
        store_root,
    )
    if not enable_adapter:
        if any(item is not None for item in resources):
            raise ChartDayAdapterError("비활성 adapter에는 운영 resource를 전달할 수 없습니다.")
        return DisabledChartDayAdapter()
    if any(
        item is None
        for item in (
            release_registry,
            ephemeris_path,
            hmac_key_file,
            encryption_key_file,
            store_root,
        )
    ):
        raise ChartDayAdapterError("활성 adapter resource가 모두 필요합니다.")
    assert release_registry is not None
    assert ephemeris_path is not None
    assert hmac_key_file is not None
    assert encryption_key_file is not None
    assert store_root is not None
    if release_registry.resolve(strict=False) != RELEASE_V15_PATH.resolve(strict=False):
        raise ChartDayAdapterError("adapter는 고정 v1.5 release만 허용합니다.")
    hmac_key = load_secret_key(hmac_key_file, purpose="runtime-hmac")
    encryption_key = load_secret_key(encryption_key_file, purpose="session-aead")
    assert_key_separation(hmac_key, encryption_key)
    previous_key = (
        None
        if previous_encryption_key_file is None
        else load_secret_key(previous_encryption_key_file, purpose="session-aead")
    )
    if previous_key is not None and previous_key.key_id == encryption_key.key_id:
        raise ChartDayAdapterError("현재·이전 encryption key가 같습니다.")
    if previous_key is not None:
        assert_key_separation(hmac_key, previous_key)
    signer = RuntimeIdSigner.from_key_file(hmac_key_file)
    try:
        engine = ApprovedSajuRuntimeEngineV15(
            release_registry=release_registry,
            enable_approved_runtime=True,
            ephemeris_path=ephemeris_path,
            signer=signer,
        )
        store = EncryptedSessionStore(
            store_root,
            active_key=encryption_key,
            decryption_keys=(() if previous_key is None else (previous_key,)),
        )
    except Exception:
        if "engine" in locals():
            engine.close()
        raise
    return ChartDayAppAdapter(
        engine=engine,
        signer=signer,
        store=store,
        hmac_key=hmac_key,
        encryption_key=encryption_key,
    )
