# contracts_v1_4.py - 과거 공식 구간 chart-only runtime v1.4 계약과 release를 검증한다.

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .contracts import CONFIG_ROOT, POLICY_ID, REPO_ROOT, sha256_file
from .contracts_v1_2 import (
    ID_CONTRACT_VERSION_V2,
    load_strict_json_object_v1_2,
)
from .contracts_v1_3 import (
    REGISTRY_V13_PATH,
    runtime_source_versions_v1_3,
    validate_contract_registry_v1_3,
)
from .errors import RuntimeCalculationError
from .skyfield_solar_terms import (
    DE440S_SHA256,
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)

REGISTRY_V14_PATH = CONFIG_ROOT / "registry-v1.4.0.json"
CONTRACT_V14_PATH = CONFIG_ROOT / "runtime_contract-v1.4.0.json"
SOURCE_REGISTRY_V17_PATH = CONFIG_ROOT / "source_registry-v1.7.0.json"
GATE_V17_PATH = CONFIG_ROOT / "conformance_gate-v1.7.0.json"
PROFILE_V14_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.4.0.json"
OUTPUT_SCHEMA_V14_PATH = CONFIG_ROOT / "calculation_output_schema-v1.4.0.json"
RELEASE_SCHEMA_V14_PATH = CONFIG_ROOT / "release_registry_schema-v1.4.0.json"
REQUIREMENTS_V14_PATH = REPO_ROOT / "requirements-runtime-calculator-v1.4.txt"
REPORT_V17_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.7.0"
RELEASE_V14_PATH = CONFIG_ROOT / "releases/v1.4.0/release_registry.json"

ENGINE_VERSION_V14 = "saju-runtime-python-v1.4.0"
OUTPUT_SCHEMA_VERSION_V14 = "1.4.0"
SUITE_VERSION_V9 = "saju-runtime-conformance-v9.0.0"
SOURCE_REGISTRY_VERSION_V17 = "saju-runtime-sources-v1.7.0"
APPROVED_SCOPE_V14 = "PAST_OFFICIAL_CHART_ONLY_1920_2026"
APPROVED_START_DATE = "1920-01-07"
APPROVED_END_DATE = "2026-08-31"
APPROVED_START = date.fromisoformat(APPROVED_START_DATE)
APPROVED_END = date.fromisoformat(APPROVED_END_DATE)
KASI_PAST_UNCERTAINTY_SECONDS = 1.0
REGISTRY_V14_SHA256 = "3ec859653127f6b77dacf7a352c6b22bbaade924a27997c68027ebfccf48bd9a"

FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
RELEASE_ID = re.compile(r"^saju-runtime-release-v1\.4\.0-[0-9a-f]{12}$")

EXPECTED_ARTIFACTS = {
    "configs/runtime/calculation/runtime_contract-v1.4.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.4.0.json",
    "configs/runtime/calculation/source_registry-v1.7.0.json",
    "configs/runtime/calculation/conformance_gate-v1.7.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.4.0.json",
    "configs/runtime/calculation/release_registry_schema-v1.4.0.json",
    "configs/runtime/calculation/id_canonicalization-v2.0.0.json",
    "requirements-runtime-calculator-v1.4.txt",
}

CONFORMANCE_V9_IMPLEMENTATIONS = {
    "scripts/runtime/calculation/contracts_v1_4.py",
    "scripts/runtime/calculation/engine_v1_4.py",
    "scripts/runtime/saju_runtime_v1_4.py",
    "scripts/evaluation/saju_runtime/conformance_v9.py",
    "scripts/evaluation/saju_runtime/release_registry_v1_4.py",
    *EXPECTED_ARTIFACTS,
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.4 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "v1.4 계약 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.4 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _validate_parent(identity: Any, *, path: Path) -> None:
    relative = str(path.relative_to(REPO_ROOT))
    expected = {"path": relative, "sha256": sha256_file(path)}
    if identity != expected:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", f"v1.4 parent identity가 다릅니다: {relative}"
        )


def validate_contract_registry_v1_4() -> dict[str, Any]:
    """v1.3을 보존한 chart-only v1.4 계약 hash chain을 검증한다."""

    validate_contract_registry_v1_3()
    registry = load_strict_json_object_v1_2(REGISTRY_V14_PATH)
    if (
        sha256_file(REGISTRY_V14_PATH) != REGISTRY_V14_SHA256
        or registry.get("schema_version") != "1.4.0"
        or registry.get("registry_id") != "saju-runtime-calculation-registry-v1.4.0"
        or registry.get("status") != "approved_chart_only_feature_default_off"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.4 runtime registry 값·hash가 다릅니다."
        )
    _validate_parent(registry.get("parent"), path=REGISTRY_V13_PATH)
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.4 artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.4 artifact 계약이 다릅니다."
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
                "CONTRACT_REGISTRY_INVALID", "v1.4 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH", f"v1.4 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.4 artifact 집합이 다릅니다."
        )

    contract = load_strict_json_object_v1_2(CONTRACT_V14_PATH)
    source = load_strict_json_object_v1_2(SOURCE_REGISTRY_V17_PATH)
    gate = load_strict_json_object_v1_2(GATE_V17_PATH)
    profile = load_strict_json_object_v1_2(PROFILE_V14_PATH)
    schema = load_strict_json_object_v1_2(OUTPUT_SCHEMA_V14_PATH)
    release_schema = load_strict_json_object_v1_2(RELEASE_SCHEMA_V14_PATH)
    gate_minimums = gate.get("minimum_cases", {})
    if (
        contract.get("runtime_contract_version") != "saju-runtime-contract-v1.4.0"
        or contract.get("engine_version") != ENGINE_VERSION_V14
        or contract.get("output_schema_version") != OUTPUT_SCHEMA_VERSION_V14
        or contract.get("conformance_suite_version") != SUITE_VERSION_V9
        or contract.get("approval_scope") != APPROVED_SCOPE_V14
        or contract.get("approved_tools") != ["calculate_saju_chart"]
        or contract.get("blocked_tools") != ["calculate_saju_period"]
        or contract.get("feature_flag_default") is not False
        or source.get("registry_version") != SOURCE_REGISTRY_VERSION_V17
        or source.get("runtime_provider", {}).get("provider_id")
        != SkyfieldSolarTermProvider.provider_id
        or source.get("runtime_provider", {}).get("strict_runtime_approved")
        is not False
        or gate.get("suite_version") != SUITE_VERSION_V9
        or gate.get("approval_scope") != APPROVED_SCOPE_V14
        or gate_minimums.get("scope_matrix") != 328_722
        or gate_minimums.get("exact_chart_calculations") != 77_908
        or gate_minimums.get("past_jie_rows_in_scope") != 1_279
        or gate_minimums.get("boundary_probe_cases") != 2_558
        or gate_minimums.get("range_unknown_month_cases") != 2_560
        or gate_minimums.get("range_unknown_total_cases") != 2_660
        or gate_minimums.get("boundary_range_blocked_cases") != 50
        or gate_minimums.get("boundary_unknown_stable_cases") != 50
        or profile.get("profile_revision") != "KR_CIVIL_MIDNIGHT_V1-v1.4.0"
        or profile.get("approved_solar_date_range")
        != {"minimum": APPROVED_START_DATE, "maximum": APPROVED_END_DATE}
        or schema.get("$id") != "saju-calculation-output-v1.4.0"
        or release_schema.get("$id") != "saju-runtime-release-registry-v1.4.0"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID",
            "v1.4 runtime·source·gate·profile 상태가 다릅니다.",
        )
    _validate_parent(
        source.get("parent"), path=CONFIG_ROOT / "source_registry-v1.6.0.json"
    )
    _validate_parent(
        gate.get("parent"), path=GATE_V17_PATH.with_name("conformance_gate-v1.6.0.json")
    )
    return registry


def runtime_source_versions_v1_4(
    *,
    require_runtime_dependencies: bool,
    provider_identity: dict[str, Any] | None = None,
    release_id: str | None = None,
    release_registry_sha256: str | None = None,
) -> dict[str, str]:
    validate_contract_registry_v1_4()
    versions = runtime_source_versions_v1_3(
        require_runtime_dependencies=require_runtime_dependencies,
        provider_identity=provider_identity,
    )
    versions.update(
        {
            "source_registry": SOURCE_REGISTRY_VERSION_V17,
            "runtime_contract": "saju-runtime-contract-v1.4.0",
            "runtime_scope": APPROVED_SCOPE_V14,
            "approved_solar_date_range": f"{APPROVED_START_DATE}/{APPROVED_END_DATE}",
            "boundary_uncertainty_seconds": str(KASI_PAST_UNCERTAINTY_SECONDS),
        }
    )
    if release_id is not None:
        if RELEASE_ID.fullmatch(release_id) is None:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_INVALID", "v1.4 release ID가 다릅니다."
            )
        versions["runtime_release"] = release_id
    if release_registry_sha256 is not None:
        if FULL_SHA.fullmatch(release_registry_sha256) is None:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_INVALID", "v1.4 release hash가 다릅니다."
            )
        versions["runtime_release_registry_sha256"] = release_registry_sha256
    return versions


def derive_gate_checks_v1_4(report: dict[str, Any]) -> dict[str, bool]:
    """보고된 pass boolean을 신뢰하지 않고 v9 집계값으로 다시 계산한다."""

    try:
        scope = report["scope_matrix"]
        exact = report["exact_chart_calculations"]
        boundaries = report["boundary_minute_gate"]
        uncertain = report["uncertain_time_gate"]
        negative = report["negative_and_governance_gate"]
        parent = report["parent_v8"]
    except (KeyError, TypeError) as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v9 집계에서 Gate를 재계산할 수 없습니다."
        ) from exc
    return {
        "parent_v8_recalculated_and_passed": parent.get("verified") is True,
        "scope_matrix_complete": scope.get("cases") == 328_722,
        "scope_matrix_allowed_exact": scope.get("allowed") == 233_724,
        "scope_matrix_blocked_exact": scope.get("blocked") == 94_998,
        "scope_matrix_failures_zero": scope.get("failures") == 0,
        "exact_chart_cases_complete": exact.get("cases") == 77_908,
        "exact_chart_failures_zero": exact.get("failures") == 0,
        "past_jie_rows_complete": boundaries.get("past_jie_rows") == 1_279,
        "boundary_probe_cases_complete": boundaries.get("probe_cases") == 2_558,
        "boundary_probe_failures_zero": boundaries.get("probe_failures") == 0,
        "past_raw_minute_mismatches_preserved": boundaries.get(
            "past_raw_minute_mismatches"
        )
        == 14,
        "subsecond_quarantine_exact": boundaries.get("quarantined_minutes") == 50,
        "unclassified_boundary_failures_zero": boundaries.get("unclassified_failures")
        == 0,
        "range_unknown_cases_complete": uncertain.get("cases") == 2_660,
        "range_unknown_failures_zero": uncertain.get("failures") == 0,
        "boundary_range_instability_classified_exact": uncertain.get(
            "blocked_boundary_ranges"
        )
        == 50,
        "boundary_unknown_stability_exact": uncertain.get("stable_boundary_unknown")
        == 50,
        "period_always_blocked": negative.get("period_block_failures") == 0,
        "date_guard_failures_zero": negative.get("date_guard_failures") == 0,
        "authority_tamper_failures_zero": negative.get("authority_tamper_failures")
        == 0,
        "governance_remains_closed": all(
            negative.get(field) is False
            for field in (
                "production_application_binding",
                "mix20k_v3_1_regeneration_allowed",
                "training_promotion_allowed",
                "sealed_blind_accessed",
            )
        ),
    }


def _validate_report_identity_v1_4(
    identity: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {"path", "sha256", "manifest_path", "manifest_sha256", "build_id"}
    if (
        not isinstance(identity, dict)
        or set(identity) != required
        or not all(isinstance(identity[key], str) for key in required)
        or FULL_SHA.fullmatch(identity["sha256"]) is None
        or FULL_SHA.fullmatch(identity["manifest_sha256"]) is None
        or BUILD_ID.fullmatch(identity["build_id"]) is None
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.4 conformance identity가 다릅니다."
        )
    report_path = _safe_repo_path(identity["path"])
    manifest_path = _safe_repo_path(identity["manifest_path"])
    try:
        report_path.relative_to(REPORT_V17_ROOT.resolve())
        manifest_path.relative_to(REPORT_V17_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v9 보고서가 고정 경로를 벗어납니다."
        ) from exc
    if (
        report_path.name != "aggregate.json"
        or manifest_path.name != "build_manifest.json"
        or report_path.parent != manifest_path.parent
        or report_path.parent.parent != REPORT_V17_ROOT.resolve()
        or report_path.parent.name != identity["build_id"]
        or report_path.is_symlink()
        or manifest_path.is_symlink()
        or not report_path.is_file()
        or not manifest_path.is_file()
        or sha256_file(report_path) != identity["sha256"]
        or sha256_file(manifest_path) != identity["manifest_sha256"]
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_HASH_MISMATCH", "v9 report·manifest hash가 다릅니다."
        )
    report = load_strict_json_object_v1_2(report_path)
    manifest = load_strict_json_object_v1_2(manifest_path)
    core = dict(report)
    build_id = core.pop("build_id", None)
    expected_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    artifact = manifest.get("artifacts", {}).get("aggregate.json")
    implementations = report.get("inputs", {}).get("implementation_sha256")
    if (
        build_id != expected_id
        or build_id != identity["build_id"]
        or report.get("schema_version") != "1.7.0"
        or report.get("suite_version") != SUITE_VERSION_V9
        or report.get("engine_version") != ENGINE_VERSION_V14
        or report.get("approval_scope") != APPROVED_SCOPE_V14
        or report.get("chart_only_gate_passed") is not True
        or report.get("chart_release_registry_creation_allowed") is not True
        or report.get("strict_runtime_provider_gate_passed") is not False
        or report.get("full_runtime_gate_passed") is not False
        or report.get("runtime_feature_flag_default") is not False
        or not isinstance(implementations, dict)
        or set(implementations) != CONFORMANCE_V9_IMPLEMENTATIONS
        or any(
            not isinstance(value, str)
            or FULL_SHA.fullmatch(value) is None
            or sha256_file(_safe_repo_path(relative)) != value
            for relative, value in implementations.items()
        )
        or manifest.get("schema_version") != "1.7.0"
        or manifest.get("build_id") != build_id
        or manifest.get("report_type") != "saju_runtime_conformance_v9"
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or artifact
        != {"bytes": report_path.stat().st_size, "sha256": sha256_file(report_path)}
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v9 통과 보고서 상태가 다릅니다."
        )
    derived = derive_gate_checks_v1_4(report)
    if report.get("gate_checks") != derived or any(
        value is not True for value in derived.values()
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v9 Gate 재계산 결과가 전부 true가 아닙니다."
        )
    return report, manifest


def release_id_for_v1_4(report_sha256: str, manifest_sha256: str) -> str:
    preimage = {
        "runtime_registry_sha256": sha256_file(REGISTRY_V14_PATH),
        "conformance_report_sha256": report_sha256,
        "conformance_manifest_sha256": manifest_sha256,
        "engine_version": ENGINE_VERSION_V14,
        "approval_scope": APPROVED_SCOPE_V14,
        "approved_tools": ["calculate_saju_chart"],
    }
    return (
        "saju-runtime-release-v1.4.0-"
        + hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()[:12]
    )


def validate_release_registry_v1_4(path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_4()
    candidate = Path(path)
    expected = _safe_repo_path(str(RELEASE_V14_PATH.relative_to(REPO_ROOT)))
    if (
        candidate.resolve(strict=False) != expected.resolve(strict=False)
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_REQUIRED",
            "고정 v1.4 chart-only release registry가 없습니다.",
        )
    release = load_strict_json_object_v1_2(candidate)
    required = {
        "release_id",
        "status",
        "engine_version",
        "id_contract_version",
        "profile_id",
        "approval_scope",
        "approved_tools",
        "blocked_tools",
        "approved_solar_date_range",
        "boundary_uncertainty_seconds",
        "quarantined_boundary_minutes",
        "runtime_registry_sha256",
        "conformance_report",
        "official_snapshots",
        "implementation_sha256",
        "production_id_key_required",
        "runtime_feature_flag_default",
        "strict_runtime_provider_gate_passed",
        "full_runtime_gate_passed",
        "production_application_binding",
        "mix20k_v3_1_regeneration_allowed",
        "training_promotion_allowed",
        "sealed_blind_accessed",
    }
    if (
        set(release) != required
        or RELEASE_ID.fullmatch(str(release.get("release_id", ""))) is None
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.4 release registry schema가 다릅니다."
        )
    if (
        release.get("status") != "approved_chart_only_feature_default_off"
        or release.get("engine_version") != ENGINE_VERSION_V14
        or release.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or release.get("profile_id") != POLICY_ID
        or release.get("approval_scope") != APPROVED_SCOPE_V14
        or release.get("approved_tools") != ["calculate_saju_chart"]
        or release.get("blocked_tools") != ["calculate_saju_period"]
        or release.get("approved_solar_date_range")
        != {"minimum": APPROVED_START_DATE, "maximum": APPROVED_END_DATE}
        or release.get("boundary_uncertainty_seconds") != KASI_PAST_UNCERTAINTY_SECONDS
        or release.get("quarantined_boundary_minutes") != 50
        or release.get("runtime_registry_sha256") != sha256_file(REGISTRY_V14_PATH)
        or release.get("production_id_key_required") is not True
        or release.get("runtime_feature_flag_default") is not False
        or any(
            release.get(field) is not False
            for field in (
                "strict_runtime_provider_gate_passed",
                "full_runtime_gate_passed",
                "production_application_binding",
                "mix20k_v3_1_regeneration_allowed",
                "training_promotion_allowed",
                "sealed_blind_accessed",
            )
        )
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.4 chart-only release 승인 값이 다릅니다."
        )
    report, manifest = _validate_report_identity_v1_4(release["conformance_report"])
    if release["release_id"] != release_id_for_v1_4(
        release["conformance_report"]["sha256"],
        release["conformance_report"]["manifest_sha256"],
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.4 release ID preimage가 다릅니다."
        )
    if release["implementation_sha256"] != report["inputs"]["implementation_sha256"]:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v9 구현 hash가 다릅니다."
        )
    if release["official_snapshots"] != report["inputs"]["official_snapshots"]:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v9 공식 snapshot이 다릅니다."
        )
    return {
        **release,
        "release_registry_sha256": sha256_file(candidate),
        "conformance_report_data": report,
        "conformance_manifest_data": manifest,
        "ephemeris_sha256": DE440S_SHA256,
        "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
    }
