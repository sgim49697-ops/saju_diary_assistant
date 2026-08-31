# intake_contracts_v1_1.py - session v2.1·FSM v1.1 계약의 중복 JSON key와 hash chain을 검증한다.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.errors import RuntimeCalculationError

INTAKE_REGISTRY = REPO_ROOT / "configs/runtime/intake_registry-v1.1.0.json"
SESSION_SCHEMA = REPO_ROOT / "configs/runtime/session_state_schema_v2.1.0.json"
FSM_CONFIG = REPO_ROOT / "configs/runtime/intake_fsm-v1.1.0.json"
FSM_GATE_CONFIG = REPO_ROOT / "configs/runtime/intake_fsm_gate-v1.1.0.json"
PARENT_RUNTIME_REGISTRY = (
    REPO_ROOT / "configs/runtime/calculation/registry-v1.2.0.json"
)
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/session_state_schema_v2.1.0.json",
    "configs/runtime/intake_fsm-v1.1.0.json",
    "configs/runtime/intake_fsm_gate-v1.1.0.json",
}
EVENT_TYPES = (
    "opt_in",
    "set_slot",
    "correct_slot",
    "set_time_unknown",
    "request_chart",
    "chart_result",
    "request_period",
    "period_result",
    "reset",
)
DECISION_ACTIONS = (
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
)
STRATA = {
    "ask_birth_date": 10,
    "no_reask_confirmed_date": 10,
    "lunar_leap_month": 10,
    "exact_time_value": 10,
    "unknown_time_partial": 10,
    "birthplace_then_handoff": 10,
    "runtime_blocked": 10,
    "correction_invalidation": 10,
    "period_handoff": 10,
    "tool_result_render": 10,
}
MAXIMUM_FAILURES = {
    "free_text_parser_present": 0,
    "name_family_job_or_gender_slot_requested": 0,
    "unknown_time_guessed": 0,
    "confirmed_field_reasked": 0,
    "correction_cache_not_invalidated": 0,
    "runtime_block_bypassed": 0,
    "fake_ui_or_completion_claim": 0,
    "non_hmac_session_fingerprint": 0,
    "replayed_period_result_accepted": 0,
    "stale_tool_result_accepted": 0,
    "state_semantic_mismatch_accepted": 0,
    "cached_input_fingerprint_mismatch_accepted": 0,
    "malformed_state_type_accepted": 0,
}
APP_PRECONDITIONS = (
    "runtime_release_ready",
    "feature_enabled",
    "production_id_key_ready",
    "fsm_gate_passed",
    "encrypted_persistence_ready",
    "retention_policy_ready",
)
SUPERSEDED_ARTIFACTS = {
    "session_state_schema": "configs/runtime/session_state_schema_v2.json",
    "intake_fsm": "configs/runtime/intake_fsm-v1.0.0.json",
    "intake_fsm_gate": "configs/runtime/intake_fsm_gate-v1.0.0.json",
    "reason": "duplicate_period_schema_key_and_semantic_gate_hardening",
}


class _DuplicateJsonKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def load_strict_json_object(path: Path) -> dict[str, Any]:
    """모든 중첩 object에서 duplicate key를 거부해 JSON 해석 차이를 없앤다."""

    if path.is_symlink() or not path.is_file():
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_FILE_INVALID",
            f"intake 계약 파일이 없거나 symlink입니다: {path}",
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except _DuplicateJsonKey as exc:
        raise RuntimeCalculationError(
            "INTAKE_DUPLICATE_JSON_KEY",
            f"intake 계약 JSON에 중복 key가 있습니다: {exc}",
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_JSON_INVALID",
            f"intake 계약 JSON을 읽지 못했습니다: {path}",
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_JSON_INVALID",
            f"intake 계약 최상위는 object여야 합니다: {path}",
        )
    return value


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PATH_INVALID", "intake 계약 경로가 안전하지 않습니다."
        )
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise RuntimeCalculationError(
                "INTAKE_CONTRACT_PATH_INVALID",
                "intake 계약 경로에 symlink가 포함됐습니다.",
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PATH_INVALID",
            "intake 계약 경로가 저장소를 벗어납니다.",
        ) from exc
    return resolved


def validate_intake_registry_v1_1() -> dict[str, Any]:
    registry = load_strict_json_object(INTAKE_REGISTRY)
    parent = registry.get("parent_runtime_registry")
    if (
        registry.get("schema_version") != "1.1.0"
        or registry.get("registry_id") != "saju-runtime-intake-registry-v1.1.0"
        or registry.get("status") != "implemented_app_integration_blocked"
        or registry.get("app_integration_allowed") is not False
        or registry.get("training_promotion_allowed") is not False
        or registry.get("supersedes_for_app_integration")
        != SUPERSEDED_ARTIFACTS
        or not isinstance(parent, dict)
        or set(parent) != {"path", "sha256"}
        or parent.get("path")
        != "configs/runtime/calculation/registry-v1.2.0.json"
        or parent.get("sha256") != sha256_file(PARENT_RUNTIME_REGISTRY)
    ):
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "intake v1.1 registry 값이 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "intake artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "INTAKE_REGISTRY_INVALID", "intake artifact identity가 다릅니다."
            )
        relative = artifact["path"]
        expected = artifact["sha256"]
        if (
            not isinstance(relative, str)
            or relative in seen
            or not isinstance(expected, str)
            or FULL_SHA.fullmatch(expected) is None
        ):
            raise RuntimeCalculationError(
                "INTAKE_REGISTRY_INVALID", "intake artifact hash 계약이 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "INTAKE_CONTRACT_HASH_MISMATCH",
                f"intake artifact hash가 다릅니다: {relative}",
            )
        load_strict_json_object(path)
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "intake artifact 집합이 다릅니다."
        )

    session = load_strict_json_object(SESSION_SCHEMA)
    fsm = load_strict_json_object(FSM_CONFIG)
    gate = load_strict_json_object(FSM_GATE_CONFIG)
    session_period = session.get("properties", {}).get("period")
    if (
        session.get("schema_version") != "2.1.0"
        or session.get("session_state_schema_version") != "saju-session-state-v2.1"
        or session.get("fsm_version") != "saju-intake-fsm-v1.1.0"
        or session.get("pii_at_rest")
        != {
            "contains_birth_data": True,
            "hmac_is_encryption": False,
            "production_requires_encrypted_persistence": True,
            "production_requires_retention_policy": True,
        }
        or not isinstance(session_period, dict)
        or session_period.get("additionalProperties") is not False
        or set(session_period.get("properties", {})) != {"request", "result"}
        or fsm.get("schema_version") != "1.1.0"
        or fsm.get("fsm_version") != "saju-intake-fsm-v1.1.0"
        or fsm.get("session_state_schema_version") != "saju-session-state-v2.1"
        or tuple(fsm.get("event_types", ())) != EVENT_TYPES
        or tuple(fsm.get("decision_actions", ())) != DECISION_ACTIONS
        or fsm.get("input_mode") != "structured_events_only"
        or fsm.get("free_text_parser_in_fsm") is not False
        or fsm.get("never_request_slots")
        != ["name", "family_relationship", "job", "gender_for_daeun"]
        or fsm.get("deterministic_defaults")
        != {"gender_for_daeun": "unspecified"}
        or fsm.get("unknown_time_policy")
        != "valid_partial_input_without_representative_time"
        or fsm.get("state_semantic_policy")
        != "slot_confirmation_provenance_precision_and_authority_must_agree"
        or fsm.get("correction_policy")
        != "invalidate_chart_period_and_hmac_fingerprint"
        or fsm.get("tool_result_policy")
        != "accept_only_when_current_hmac_call_id_matches_and_reject_stale_or_rendered_result_replay"
        or fsm.get("result_policy")
        != "render_only_structured_tool_result_never_claim_hidden_ui_or_completion"
        or tuple(fsm.get("app_preconditions", ())) != APP_PRECONDITIONS
        or fsm.get("model_gate_is_replaced") is not False
        or gate.get("schema_version") != "1.1.0"
        or gate.get("gate_version") != "saju-intake-fsm-gate-v1.1.0"
        or gate.get("fsm_version") != "saju-intake-fsm-v1.1.0"
        or gate.get("session_state_schema_version") != "saju-session-state-v2.1"
        or gate.get("minimum_cases") != 100
        or gate.get("required_passed_cases") != 100
        or gate.get("strata") != STRATA
        or gate.get("maximum_failures") != MAXIMUM_FAILURES
        or gate.get("model_gate_relation")
        != {
            "replaces_model_required_handoff_action_gate": False,
            "recorded_model_result": "14/100",
            "model_improvement_claim_allowed": False,
        }
        or tuple(gate.get("app_integration_requires", ())) != APP_PRECONDITIONS
        or gate.get("training_promotion_allowed") is not False
    ):
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_STATE_INVALID",
            "session v2.1·FSM v1.1·Gate v1.1 계약이 다릅니다.",
        )
    return registry
