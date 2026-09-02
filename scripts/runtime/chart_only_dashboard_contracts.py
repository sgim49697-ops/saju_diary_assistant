# chart_only_dashboard_contracts.py - dashboard v1.9 production binding의 고정 계약과 hash chain을 검증한다.

from __future__ import annotations

import importlib.metadata
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.chart_only_operations_contracts import (
    PARENT_RELEASE_ID,
    PARENT_RELEASE_SHA256,
    load_strict_json,
    sha256_file,
    validate_operations_registry,
)

REGISTRY_PATH = REPO_ROOT / "configs/runtime/operations/registry-v1.1.0.json"
BINDING_PATH = (
    REPO_ROOT
    / "configs/runtime/operations/chart_only_dashboard_binding-v1.0.0.json"
)
CANARY_PATH = (
    REPO_ROOT
    / "configs/runtime/operations/chart_only_dashboard_canary_gate-v1.0.0.json"
)
REGISTRY_SHA256 = "5bfab61235d9841cd0b7f515c43d060fc5b25182bbfb1ebcce282170e15fa881"
PARENT_REGISTRY_SHA256 = (
    "2fe57a7f36c871d3374b1a33e02df9acdfeb44a91274a851d04c5c59ac2e9f76"
)
EXPECTED_ARTIFACTS = {
    "configs/runtime/operations/chart_only_dashboard_binding-v1.0.0.json": (
        "b8ef4f14604866029136b6774e42b4fdefa184a6a160249b7c19632fbcc7957e"
    ),
    "configs/runtime/operations/chart_only_dashboard_canary_gate-v1.0.0.json": (
        "d2cf2eb5064ddbb371653401c513afd4ee22e7d14ebfd54d87c3b1fae56c36c5"
    ),
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.9.0.json": (
        "f65ca1aa3fea9ebf1ca02a06e568f3114f1867491ee56dcde87f93ccb737f567"
    ),
    "scripts/training/phase5_dashboard_assets/v1.9.0/index.html": (
        "a94153b6acfb6d83fcc9d806ad594f5d2bebdb6c312dd7a6dd0807c2a3e2ae34"
    ),
    "scripts/training/phase5_dashboard_assets/v1.9.0/dashboard.js": (
        "eaa2dbe1e24acc25b2a0eb17130307419dec97126455c30a61cdccbbad00d985"
    ),
    "scripts/training/phase5_dashboard_assets/v1.9.0/dashboard.css": (
        "10a2bb69eb25dc461a3b3ba3a9679bd6f906270d56bbb6d65b309be1c4434db9"
    ),
    "scripts/training/phase5_dashboard_assets/v1.9.0/prompt-examples.json": (
        "568e0535d112edadbb31f6f2b66f0870ebaa47ab7a2ca20f0224bd48d3fde009"
    ),
    "requirements-runtime-adapter-v1.0.txt": (
        "abecdc234a10ffa940c892934e9f8e41e6ba7648926eb9571bba0e98e49066f0"
    ),
}
EXPECTED_STRATA = {
    "feature_disabled": 10,
    "normal_chart": 20,
    "boundary_block": 10,
    "scope_block": 20,
    "tamper_rejection": 10,
    "period_block": 10,
    "rate_concurrency_process": 10,
    "public_leakage": 10,
}
EXPECTED_CHECKS = {
    "all_http_cases_passed",
    "feature_default_off",
    "exact_host_origin_csrf_enforced",
    "stale_revision_rejected",
    "duplicate_process_rejected",
    "runtime_busy_returns_429",
    "rate_limit_retry_after_present",
    "period_always_blocked",
    "legacy_routes_return_410",
    "encrypted_store_only",
    "request_logs_redacted",
    "public_response_allowlisted",
    "same_snapshot_bound_to_k0_and_ki20",
    "gpu_pair_nonempty",
    "no_sealed_blind_access",
    "no_training_or_model_promotion",
}
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")


class ChartOnlyDashboardContractError(RuntimeError):
    """dashboard binding contract·hash chain 위반."""


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ChartOnlyDashboardContractError("dashboard artifact 경로가 안전하지 않습니다.")
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ChartOnlyDashboardContractError("dashboard artifact 경로에 symlink가 있습니다.")
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ChartOnlyDashboardContractError(
            "dashboard artifact 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _validate_binding(value: Mapping[str, Any]) -> None:
    api = value.get("api")
    security = value.get("security")
    model = value.get("model_binding")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("binding_id") != "saju-chart-only-dashboard-binding-v1.0.0"
        or value.get("status") != "limited_public_chart_only_binding_candidate"
        or not isinstance(api, Mapping)
        or api.get("legacy_routes_status") != 410
        or api.get("exact_host_origin_and_csrf_required") is not True
        or api.get("free_text_runtime_parser_allowed") is not False
        or api.get("period_calculation_allowed") is not False
        or not isinstance(security, Mapping)
        or security.get("persistence") != "AES-256-GCM"
        or security.get("retention_seconds") != 1800
        or security.get("single_owning_process") is not True
        or security.get("stale_revision_status") != 409
        or security.get("busy_status") != 429
        or any(
            security.get(key) is not False
            for key in (
                "request_body_logged",
                "birth_data_logged",
                "runtime_identifier_logged",
            )
        )
        or value.get("rate_limits_per_minute")
        != {"session_or_chart": 30, "runtime_event": 300, "model_generation": 10}
        or not isinstance(model, Mapping)
        or model.get("same_canonical_snapshot_for_all_selected_engines") is not True
        or model.get("runtime_capability_passed_to_model_process") is not False
        or not isinstance(governance, Mapping)
        or governance.get("production_application_binding") is not True
        or governance.get("runtime_feature_default") is not False
        or governance.get("period_runtime_allowed") is not False
        or governance.get("sealed_blind_access_allowed") is not False
        or governance.get("training_execution_allowed") is not False
        or governance.get("model_promotion_allowed") is not False
    ):
        raise ChartOnlyDashboardContractError("dashboard binding 계약이 다릅니다.")


def _validate_canary(value: Mapping[str, Any]) -> None:
    gpu = value.get("gpu_pair")
    output = value.get("public_output")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("gate_id") != "saju-chart-only-dashboard-canary-v1.0.0"
        or value.get("required_http_cases") != 100
        or value.get("maximum_http_failures") != 0
        or value.get("strata") != EXPECTED_STRATA
        or sum(value.get("strata", {}).values()) != 100
        or set(value.get("required_checks", ())) != EXPECTED_CHECKS
        or not isinstance(gpu, Mapping)
        or gpu.get("required") is not True
        or gpu.get("engine_selection") != "k0_vs_ki20"
        or gpu.get("raw_output_tracking_allowed") is not False
        or not isinstance(output, Mapping)
        or output.get("aggregate_only") is not True
        or any(
            output.get(key) is not False
            for key in (
                "raw_case_output_allowed",
                "raw_model_output_allowed",
                "birth_input_recording_allowed",
                "runtime_identifier_recording_allowed",
                "public_url_recording_allowed",
                "private_path_recording_allowed",
            )
        )
        or not isinstance(governance, Mapping)
        or governance.get("runtime_scope") != "limited_public_chart_only"
        or governance.get("period_runtime_allowed") is not False
        or any(
            governance.get(key) is not False
            for key in (
                "sealed_blind_accessed",
                "mix20k_v3_1_generated",
                "training_execution_performed",
                "model_promotion_performed",
                "phase6_status_auto_changed",
            )
        )
    ):
        raise ChartOnlyDashboardContractError("dashboard canary Gate 계약이 다릅니다.")


def validate_dashboard_operations_registry(
    *, require_dependencies: bool = False
) -> dict[str, Any]:
    """v1.0 parent와 v1.9 binding artifact의 byte identity를 검증한다."""

    validate_operations_registry(require_dependencies=require_dependencies)
    registry = load_strict_json(REGISTRY_PATH, label="dashboard operations registry")
    if (
        sha256_file(REGISTRY_PATH) != REGISTRY_SHA256
        or registry.get("schema_version") != "1.1.0"
        or registry.get("registry_id")
        != "saju-chart-only-operations-registry-v1.1.0"
        or registry.get("status")
        != "production_binding_implementation_ready_canary_required"
        or registry.get("parent_registry")
        != {
            "path": "configs/runtime/operations/registry-v1.0.0.json",
            "sha256": PARENT_REGISTRY_SHA256,
            "registry_id": "saju-chart-only-operations-registry-v1.0.0",
        }
        or registry.get("parent_release")
        != {
            "path": "configs/runtime/calculation/releases/v1.4.0/release_registry.json",
            "sha256": PARENT_RELEASE_SHA256,
            "release_id": PARENT_RELEASE_ID,
        }
    ):
        raise ChartOnlyDashboardContractError("dashboard operations registry identity가 다릅니다.")
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list):
        raise ChartOnlyDashboardContractError("dashboard artifact 목록이 없습니다.")
    observed: dict[str, str] = {}
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise ChartOnlyDashboardContractError("dashboard artifact 형식이 다릅니다.")
        relative = artifact.get("path")
        digest = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in observed
            or not isinstance(digest, str)
            or FULL_SHA.fullmatch(digest) is None
        ):
            raise ChartOnlyDashboardContractError("dashboard artifact identity가 다릅니다.")
        if sha256_file(_safe_repo_path(relative)) != digest:
            raise ChartOnlyDashboardContractError(
                f"dashboard artifact hash가 다릅니다: {relative}"
            )
        observed[relative] = digest
    if observed != EXPECTED_ARTIFACTS:
        raise ChartOnlyDashboardContractError("dashboard artifact 집합이 다릅니다.")
    _validate_binding(load_strict_json(BINDING_PATH, label="dashboard binding"))
    _validate_canary(load_strict_json(CANARY_PATH, label="dashboard canary"))
    if require_dependencies:
        expected = {
            "cryptography": "50.0.1",
            "cffi": "2.0.0",
            "pycparser": "2.23",
            "typing_extensions": "4.16.0",
        }
        actual = {name: importlib.metadata.version(name) for name in expected}
        if actual != expected:
            raise ChartOnlyDashboardContractError(
                f"dashboard binding dependency가 다릅니다: {actual}"
            )
    return dict(registry)
