# intake_contracts_v1_2.py - 과거 공식 근거 후보 session·FSM·Gate의 hash chain을 검증한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_3 import validate_contract_registry_v1_3
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.intake_contracts_v1_1 import (
    load_strict_json_object,
    validate_intake_registry_v1_1,
)
from scripts.runtime.intake_fsm_v1_2 import (
    CANDIDATE_RUNTIME_STATUS_FIELDS,
    CANDIDATE_SCOPE,
    DECISION_ACTIONS,
    EVENT_TYPES,
    FSM_VERSION,
    PUBLIC_EVENT_TYPES,
    SESSION_SCHEMA_VERSION,
)

INTAKE_REGISTRY = REPO_ROOT / "configs/runtime/intake_registry-v1.2.0.json"
SESSION_SCHEMA = REPO_ROOT / "configs/runtime/session_state_schema_v2.2.0.json"
FSM_CONFIG = REPO_ROOT / "configs/runtime/intake_fsm-v1.2.0.json"
CANDIDATE_GATE = REPO_ROOT / "configs/runtime/historical_candidate_gate-v1.0.0.json"
PARENT_RUNTIME = REPO_ROOT / "configs/runtime/calculation/registry-v1.3.0.json"
PARENT_INTAKE = REPO_ROOT / "configs/runtime/intake_registry-v1.1.0.json"
REGISTRY_SHA256 = "fc8288890869315730ca74cc03cf39818d554dbbf97d69cc1c8ad0f01f51425c"
PARENT_RUNTIME_SHA256 = (
    "556e99a076511bb99b65a9dae155fe93b9a33de4d0f2f57cee009461d2befd07"
)
PARENT_INTAKE_SHA256 = (
    "9fd5c179d3980c2d192d596d5158645062931f5c191c2c35e382fc59ec42184a"
)
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/session_state_schema_v2.2.0.json",
    "configs/runtime/intake_fsm-v1.2.0.json",
    "configs/runtime/historical_candidate_gate-v1.0.0.json",
}
EXPECTED_STRATA = {
    "past_exact_official": 10,
    "past_range_official": 10,
    "past_unknown_official": 10,
    "baengno_1964_boundary": 10,
    "lunar_past_official": 10,
    "correction_invalidation": 10,
    "stale_call_rejection": 10,
    "tampered_hmac_rejection": 10,
    "profile_coverage_block": 10,
    "future_cutoff_block": 10,
    "period_scope_block": 10,
    "public_event_privacy": 10,
}
REQUIRED_CHECKS = {
    "all_cases_passed",
    "past_authority_only",
    "all_alternatives_checked",
    "cutoff_enforced",
    "hmac_identity_recomputed",
    "period_disabled",
    "public_chart_result_rejected",
    "no_raw_birth_data_in_public_report",
    "no_internal_trace_in_public_response",
    "loopback_only",
    "ephemeral_bounded_store",
    "existing_dashboard_assets_unchanged",
}
FALSE_GOVERNANCE_FIELDS = {
    "production_application_binding_allowed",
    "runtime_release_approved",
    "context_window_change_allowed",
    "mix20k_v3_1_generation_allowed",
    "additional_training_allowed",
    "model_promotion_allowed",
    "sealed_blind_access_allowed",
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PATH_INVALID", "candidate intake 경로가 안전하지 않습니다."
        )
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise RuntimeCalculationError(
                "INTAKE_CONTRACT_PATH_INVALID",
                "candidate intake 경로에 symlink가 포함됐습니다.",
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PATH_INVALID",
            "candidate intake 경로가 저장소를 벗어납니다.",
        ) from exc
    return resolved


def _parent(identity: Any, path: str, expected_sha256: str) -> None:
    if identity != {"path": path, "sha256": expected_sha256}:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PARENT_INVALID", f"candidate parent가 다릅니다: {path}"
        )
    parent = _safe_repo_path(path)
    if sha256_file(parent) != expected_sha256:
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_PARENT_HASH_MISMATCH",
            f"candidate parent hash가 다릅니다: {path}",
        )


def _validate_artifacts(registry: Mapping[str, Any]) -> None:
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "candidate artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "INTAKE_REGISTRY_INVALID", "candidate artifact identity가 다릅니다."
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
                "INTAKE_REGISTRY_INVALID", "candidate artifact hash 계약이 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "INTAKE_CONTRACT_HASH_MISMATCH",
                f"candidate artifact hash가 다릅니다: {relative}",
            )
        load_strict_json_object(path)
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "candidate artifact 집합이 다릅니다."
        )


def validate_intake_registry_v1_2() -> dict[str, Any]:
    """부모 계약과 과거 후보 진단 계약의 identity·hash·금지 상태를 검증한다."""

    validate_contract_registry_v1_3()
    validate_intake_registry_v1_1()
    registry = load_strict_json_object(INTAKE_REGISTRY)
    if (
        sha256_file(INTAKE_REGISTRY) != REGISTRY_SHA256
        or registry.get("schema_version") != "1.2.0"
        or registry.get("registry_id") != "saju-runtime-intake-registry-v1.2.0"
        or registry.get("status")
        != "historical_candidate_contract_implemented_dashboard_gate_pending"
        or registry.get("candidate_scope") != CANDIDATE_SCOPE
        or registry.get("diagnostic_dashboard_binding_allowed_on_gate_pass") is not True
        or registry.get("diagnostic_dashboard_binding_performed") is not False
        or any(registry.get(field) is not False for field in FALSE_GOVERNANCE_FIELDS)
    ):
        raise RuntimeCalculationError(
            "INTAKE_REGISTRY_INVALID", "candidate intake registry 값·hash가 다릅니다."
        )
    _parent(
        registry.get("parent_runtime_registry"),
        "configs/runtime/calculation/registry-v1.3.0.json",
        PARENT_RUNTIME_SHA256,
    )
    _parent(
        registry.get("parent_intake_registry"),
        "configs/runtime/intake_registry-v1.1.0.json",
        PARENT_INTAKE_SHA256,
    )
    _validate_artifacts(registry)

    session = load_strict_json_object(SESSION_SCHEMA)
    fsm = load_strict_json_object(FSM_CONFIG)
    gate = load_strict_json_object(CANDIDATE_GATE)
    chart_authority = (
        session.get("properties", {})
        .get("chart", {})
        .get("properties", {})
        .get("fact_authority", {})
        .get("enum")
    )
    period_properties = (
        session.get("properties", {}).get("period", {}).get("properties", {})
    )
    if (
        session.get("schema_version") != "2.2.0"
        or session.get("session_state_schema_version") != SESSION_SCHEMA_VERSION
        or session.get("fsm_version") != FSM_VERSION
        or session.get("candidate_policy")
        != {
            "scope": CANDIDATE_SCOPE,
            "fact_authority": "HARD_CANDIDATE",
            "production_release_approved": False,
            "application_binding_performed": False,
            "period_calculation_allowed": False,
            "disk_persistence_allowed": False,
        }
        or chart_authority != ["HARD_CANDIDATE", None]
        or period_properties
        != {"request": {"const": None}, "result": {"const": None}}
        or fsm.get("schema_version") != "1.2.0"
        or fsm.get("fsm_version") != FSM_VERSION
        or fsm.get("session_state_schema_version") != SESSION_SCHEMA_VERSION
        or fsm.get("candidate_scope") != CANDIDATE_SCOPE
        or set(fsm.get("event_types", ())) != EVENT_TYPES
        or set(fsm.get("public_event_types", ())) != PUBLIC_EVENT_TYPES
        or set(fsm.get("decision_actions", ())) != DECISION_ACTIONS
        or set(fsm.get("runtime_status_fields", ()))
        != CANDIDATE_RUNTIME_STATUS_FIELDS
        or fsm.get("accepted_result", {}).get("official_snapshot_collected_at")
        != OFFICIAL_SNAPSHOT_COLLECTED_AT
        or fsm.get("accepted_runtime_identity", {}).get("solar_term_provider")
        != SkyfieldSolarTermProvider.provider_id
        or fsm.get("period_policy")
        != "always_block_with_CANDIDATE_PERIOD_OUT_OF_SCOPE"
        or fsm.get("production_application_binding") is not False
        or fsm.get("runtime_release_approved") is not False
        or fsm.get("training_promotion_allowed") is not False
        or gate.get("schema_version") != "1.0.0"
        or gate.get("gate_version") != "saju-historical-candidate-gate-v1.0.0"
        or gate.get("fsm_version") != FSM_VERSION
        or gate.get("session_state_schema_version") != SESSION_SCHEMA_VERSION
        or gate.get("minimum_cases") != 120
        or gate.get("required_passed_cases") != 120
        or gate.get("strata") != EXPECTED_STRATA
        or sum(gate.get("strata", {}).values()) != 120
        or set(gate.get("required_checks", ())) != REQUIRED_CHECKS
        or gate.get("maximum_failures") != 0
        or gate.get("diagnostic_dashboard_binding_allowed_on_pass") is not True
        or any(gate.get(field) is not False for field in FALSE_GOVERNANCE_FIELDS)
    ):
        raise RuntimeCalculationError(
            "INTAKE_CONTRACT_STATE_INVALID",
            "candidate session v2.2·FSM v1.2·Gate 계약이 다릅니다.",
        )
    return registry
