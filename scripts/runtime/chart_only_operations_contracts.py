# chart_only_operations_contracts.py - chart-only 운영 보안·adapter·canary hash chain을 검증한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.evaluation.saju_runtime.release_registry_v1_4 import (
    validate_release_registry_v1_4,
)
from scripts.runtime.calculation.contracts import REPO_ROOT

REGISTRY_PATH = REPO_ROOT / "configs/runtime/operations/registry-v1.0.0.json"
SECURITY_PATH = (
    REPO_ROOT / "configs/runtime/operations/chart_only_security-v1.0.0.json"
)
ADAPTER_PATH = (
    REPO_ROOT / "configs/runtime/operations/chart_only_adapter-v1.0.0.json"
)
CANARY_GATE_PATH = (
    REPO_ROOT / "configs/runtime/operations/chart_only_canary_gate-v1.0.0.json"
)
REGISTRY_SHA256 = "2fe57a7f36c871d3374b1a33e02df9acdfeb44a91274a851d04c5c59ac2e9f76"
PARENT_RELEASE_SHA256 = (
    "5f63edacf2d1736715304283eab56ff45a83de749e2a1a64f7775d2a505d00e9"
)
PARENT_RELEASE_ID = "saju-runtime-release-v1.4.0-63dc8d398e90"
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/operations/chart_only_security-v1.0.0.json": (
        "fc35a4a4fe4a9c719134adeae81280262347007aaf010e27c2b9d8fb4e001ce4"
    ),
    "configs/runtime/operations/chart_only_adapter-v1.0.0.json": (
        "92f363f0a63d3e6c0aa6288be69cfa8e606a4e60be18fcd9e360893ced39ed43"
    ),
    "configs/runtime/operations/chart_only_canary_gate-v1.0.0.json": (
        "1296788acf1d0cded5e61f0effcdeb72961d884f12c9f0402a041de209d998fe"
    ),
    "requirements-runtime-adapter-v1.0.txt": (
        "abecdc234a10ffa940c892934e9f8e41e6ba7648926eb9571bba0e98e49066f0"
    ),
}
EXPECTED_GOVERNANCE = {
    "production_application_binding": False,
    "runtime_feature_default": False,
    "raw_case_output_tracking_allowed": False,
    "sealed_blind_access_allowed": False,
    "mix20k_v3_1_generation_allowed": False,
    "training_execution_allowed": False,
    "model_promotion_allowed": False,
}
EXPECTED_STRATA = {
    "feature_disabled": 10,
    "past_exact_solar": 10,
    "past_exact_lunar": 10,
    "past_range": 10,
    "past_unknown": 10,
    "boundary_uncertain_block": 10,
    "scope_before_block": 10,
    "scope_after_block": 10,
    "period_block": 10,
    "tamper_rejection": 10,
    "key_rotation_and_separation": 10,
    "retention_and_deletion": 10,
    "public_leakage": 10,
}
EXPECTED_CHECKS = {
    "all_cases_passed",
    "release_identity_verified",
    "feature_default_off",
    "disabled_resources_not_opened",
    "hmac_encryption_keys_separated",
    "encrypted_persistence_only",
    "tamper_rejected",
    "retention_enforced",
    "period_always_blocked",
    "boundary_uncertainty_blocked",
    "public_response_allowlisted",
    "no_raw_birth_data_in_public_report",
    "no_runtime_ids_in_public_report",
    "no_sealed_blind_access",
    "no_training_or_model_binding",
}


class ChartOnlyOperationsContractError(RuntimeError):
    """chart-only 운영 계약 또는 hash chain 위반."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChartOnlyOperationsContractError(
                f"JSON duplicate key를 허용하지 않습니다: {key}"
            )
        result[key] = value
    return result


def load_strict_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChartOnlyOperationsContractError(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise ChartOnlyOperationsContractError(f"{label} 최상위가 object가 아닙니다.")
    return value


def _safe_repo_path(relative: str, *, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ChartOnlyOperationsContractError(f"{label} 경로가 안전하지 않습니다.")
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ChartOnlyOperationsContractError(f"{label} 경로에 symlink가 있습니다.")
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ChartOnlyOperationsContractError(
            f"{label} 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _validate_registry(registry: Mapping[str, Any]) -> None:
    if (
        sha256_file(REGISTRY_PATH) != REGISTRY_SHA256
        or registry.get("schema_version") != "1.0.0"
        or registry.get("registry_id")
        != "saju-chart-only-operations-registry-v1.0.0"
        or registry.get("status")
        != "dry_run_and_synthetic_canary_ready_production_binding_blocked"
        or registry.get("governance") != EXPECTED_GOVERNANCE
    ):
        raise ChartOnlyOperationsContractError("operations registry identity가 다릅니다.")
    parent = registry.get("parent_release")
    expected_parent = {
        "path": "configs/runtime/calculation/releases/v1.4.0/release_registry.json",
        "sha256": PARENT_RELEASE_SHA256,
        "release_id": PARENT_RELEASE_ID,
    }
    if parent != expected_parent:
        raise ChartOnlyOperationsContractError("operations parent release가 다릅니다.")
    parent_path = _safe_repo_path(expected_parent["path"], label="parent release")
    if sha256_file(parent_path) != PARENT_RELEASE_SHA256:
        raise ChartOnlyOperationsContractError("operations parent release hash가 다릅니다.")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise ChartOnlyOperationsContractError("operations artifact 목록이 다릅니다.")
    observed: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise ChartOnlyOperationsContractError("operations artifact 형식이 다릅니다.")
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in observed
            or not isinstance(expected, str)
            or FULL_SHA.fullmatch(expected) is None
        ):
            raise ChartOnlyOperationsContractError("operations artifact identity가 다릅니다.")
        path = _safe_repo_path(relative, label="operations artifact")
        actual = sha256_file(path)
        if actual != expected:
            raise ChartOnlyOperationsContractError(
                f"operations artifact hash가 다릅니다: {relative}"
            )
        observed[relative] = expected
    if observed != EXPECTED_ARTIFACTS:
        raise ChartOnlyOperationsContractError("operations artifact 집합이 다릅니다.")


def _validate_security(value: Mapping[str, Any]) -> None:
    key_files = value.get("key_files")
    persistence = value.get("persistence")
    encryption = value.get("encryption_lifecycle")
    logging = value.get("logging")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("policy_id") != "saju-chart-only-security-v1.0.0"
        or value.get("status") != "dry_run_ready_production_binding_blocked"
        or not isinstance(key_files, Mapping)
        or key_files.get("bytes") != 32
        or key_files.get("mode") != "0600"
        or key_files.get("single_hardlink_required") is not True
        or key_files.get("hmac_and_encryption_key_separation_required") is not True
        or key_files.get("secret_material_in_git_allowed") is not False
        or not isinstance(persistence, Mapping)
        or persistence.get("algorithm") != "AES-256-GCM"
        or persistence.get("nonce_bytes") != 12
        or persistence.get("root_mode") != "0700"
        or persistence.get("record_mode") != "0600"
        or persistence.get("maximum_sessions") != 100
        or persistence.get("retention_seconds") != 1800
        or persistence.get("raw_birth_data_outside_ciphertext_allowed") is not False
        or persistence.get("tamper_detection_required") is not True
        or not isinstance(encryption, Mapping)
        or encryption.get("maximum_decryption_keys") != 2
        or encryption.get("physical_secure_overwrite_claimed") is not False
        or not isinstance(logging, Mapping)
        or any(
            logging.get(field) is not False
            for field in (
                "request_body_allowed",
                "birth_slots_allowed",
                "runtime_ids_allowed",
                "ciphertext_allowed",
            )
        )
        or governance
        != {
            "production_application_binding": False,
            "runtime_feature_default": False,
            "sealed_blind_access_allowed": False,
            "training_execution_allowed": False,
            "model_promotion_allowed": False,
        }
    ):
        raise ChartOnlyOperationsContractError("chart-only security 정책이 다릅니다.")


def _validate_adapter(value: Mapping[str, Any]) -> None:
    feature = value.get("feature")
    release = value.get("release")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("adapter_id") != "saju-chart-only-app-adapter-v1.0.0"
        or value.get("session_schema_version")
        != "saju-chart-only-session-state-v1.0"
        or value.get("fsm_version") != "saju-chart-only-intake-fsm-v1.0.0"
        or value.get("status") != "dry_run_only_feature_default_off"
        or release
        != {
            "path": "configs/runtime/calculation/releases/v1.4.0/release_registry.json",
            "sha256": PARENT_RELEASE_SHA256,
            "release_id": PARENT_RELEASE_ID,
        }
        or feature
        != {
            "enabled_by_default": False,
            "explicit_enable_required": True,
            "resources_opened_when_disabled": False,
        }
        or value.get("free_text_parser_allowed") is not False
        or value.get("allowed_tool") != "calculate_saju_chart"
        or value.get("blocked_tool") != "calculate_saju_period"
        or set(value.get("allowed_fact_authorities", ()))
        != {"HARD_GT", "POLICY_BOUND_RULE"}
        or governance
        != {
            "dry_run_only": True,
            "production_application_binding": False,
            "model_context_binding": False,
            "mix20k_v3_1_generation_allowed": False,
            "training_execution_allowed": False,
            "sealed_blind_access_allowed": False,
        }
    ):
        raise ChartOnlyOperationsContractError("chart-only adapter 계약이 다릅니다.")


def _validate_canary_gate(value: Mapping[str, Any]) -> None:
    output = value.get("output")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("gate_id") != "saju-chart-only-app-canary-v1.0.0"
        or value.get("status") != "synthetic_local_canary_only"
        or value.get("required_cases") != 130
        or value.get("maximum_failures") != 0
        or value.get("strata") != EXPECTED_STRATA
        or sum(value.get("strata", {}).values()) != 130
        or set(value.get("required_checks", ())) != EXPECTED_CHECKS
        or output
        != {
            "aggregate_only": True,
            "raw_case_output_allowed": False,
            "private_path_recording_allowed": False,
            "birth_input_recording_allowed": False,
            "runtime_id_recording_allowed": False,
        }
        or governance
        != {
            "production_application_binding": False,
            "runtime_feature_default": False,
            "sealed_blind_accessed": False,
            "training_execution_performed": False,
            "model_context_binding": False,
            "mix20k_v3_1_generated": False,
        }
    ):
        raise ChartOnlyOperationsContractError("chart-only canary Gate가 다릅니다.")


def validate_operations_registry(*, require_dependencies: bool = False) -> dict[str, Any]:
    """parent release와 운영 계약의 identity·hash·금지 상태를 검증한다."""

    release = validate_release_registry_v1_4(
        REPO_ROOT
        / "configs/runtime/calculation/releases/v1.4.0/release_registry.json"
    )
    if release.get("release_id") != PARENT_RELEASE_ID:
        raise ChartOnlyOperationsContractError("parent chart-only release ID가 다릅니다.")
    registry = load_strict_json(REGISTRY_PATH, label="operations registry")
    _validate_registry(registry)
    _validate_security(load_strict_json(SECURITY_PATH, label="security policy"))
    _validate_adapter(load_strict_json(ADAPTER_PATH, label="adapter contract"))
    _validate_canary_gate(load_strict_json(CANARY_GATE_PATH, label="canary gate"))
    if require_dependencies:
        expected = {
            "cryptography": "50.0.1",
            "cffi": "2.0.0",
            "pycparser": "2.23",
            "typing_extensions": "4.16.0",
        }
        actual = {
            package: importlib.metadata.version(package) for package in expected
        }
        if actual != expected:
            raise ChartOnlyOperationsContractError(
                f"chart-only adapter dependency가 다릅니다: {actual}"
            )
    return dict(registry)
