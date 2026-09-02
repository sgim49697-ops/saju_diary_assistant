# contracts_v1_5.py - 과거 원국과 단일 일진 runtime v1.5 계약·release를 검증한다.

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .contracts import CONFIG_ROOT, POLICY_ID, REPO_ROOT, sha256_file
from .contracts_v1_2 import ID_CONTRACT_VERSION_V2, load_strict_json_object_v1_2
from .contracts_v1_4 import (
    APPROVED_END_DATE,
    APPROVED_START_DATE,
    REGISTRY_V14_PATH,
    RELEASE_V14_PATH,
    runtime_source_versions_v1_4,
    validate_contract_registry_v1_4,
    validate_release_registry_v1_4,
)
from .errors import RuntimeCalculationError
from .skyfield_solar_terms import DE440S_SHA256, SkyfieldSolarTermProvider

REGISTRY_V15_PATH = CONFIG_ROOT / "registry-v1.5.0.json"
CONTRACT_V15_PATH = CONFIG_ROOT / "runtime_contract-v1.5.0.json"
SOURCE_REGISTRY_V18_PATH = CONFIG_ROOT / "source_registry-v1.8.0.json"
GATE_V18_PATH = CONFIG_ROOT / "conformance_gate-v1.8.0.json"
PROFILE_V15_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.5.0.json"
OUTPUT_SCHEMA_V15_PATH = CONFIG_ROOT / "calculation_output_schema-v1.5.0.json"
RELEASE_SCHEMA_V15_PATH = CONFIG_ROOT / "release_registry_schema-v1.5.0.json"
REQUIREMENTS_V15_PATH = REPO_ROOT / "requirements-runtime-calculator-v1.5.txt"
REPORT_V18_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.8.0"
RELEASE_V15_PATH = CONFIG_ROOT / "releases/v1.5.0/release_registry.json"

ENGINE_VERSION_V15 = "saju-runtime-python-v1.5.0"
OUTPUT_SCHEMA_VERSION_V15 = "1.5.0"
SUITE_VERSION_V10 = "saju-runtime-conformance-v10.0.0"
SOURCE_REGISTRY_VERSION_V18 = "saju-runtime-sources-v1.8.0"
APPROVED_SCOPE_V15 = "PAST_OFFICIAL_CHART_PLUS_SINGLE_DAY_1920_2049"
SINGLE_DAY_START_DATE = "2026-09-02"
SINGLE_DAY_END_DATE = "2049-12-31"
SINGLE_DAY_START = date.fromisoformat(SINGLE_DAY_START_DATE)
SINGLE_DAY_END = date.fromisoformat(SINGLE_DAY_END_DATE)
SINGLE_DAY_CASES = 8_522
SINGLE_DAY_EVALUATION_LOCAL_TIME = "12:00"
REGISTRY_V15_SHA256 = "79b538f231744b994e2881437f5529bddcbb0fcebc5983676c65a28814d5e8bd"

FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
RELEASE_ID = re.compile(r"^saju-runtime-release-v1\.5\.0-[0-9a-f]{12}$")

EXPECTED_ARTIFACTS_V15 = {
    "configs/runtime/calculation/runtime_contract-v1.5.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.5.0.json",
    "configs/runtime/calculation/source_registry-v1.8.0.json",
    "configs/runtime/calculation/conformance_gate-v1.8.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.5.0.json",
    "configs/runtime/calculation/release_registry_schema-v1.5.0.json",
    "configs/runtime/calculation/id_canonicalization-v2.0.0.json",
    "requirements-runtime-calculator-v1.5.txt",
}

CONFORMANCE_V10_IMPLEMENTATIONS = {
    "scripts/runtime/calculation/contracts_v1_5.py",
    "scripts/runtime/calculation/engine_v1_5.py",
    "scripts/runtime/calculation/facts_v1_3.py",
    "scripts/runtime/calculation/solar_terms_v1_3.py",
    "scripts/runtime/saju_runtime_v1_5.py",
    "scripts/evaluation/saju_runtime/conformance_v10.py",
    "scripts/evaluation/saju_runtime/release_registry_v1_5.py",
    "scripts/evaluation/saju_runtime/conformance_v3.py",
    "scripts/evaluation/saju_runtime/conformance_v6.py",
    "scripts/evaluation/saju_runtime/kasi_official_solar_terms_collector.py",
    *EXPECTED_ARTIFACTS_V15,
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.5 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "v1.5 계약 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.5 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _validate_parent(identity: Any, *, path: Path) -> None:
    expected = {
        "path": str(path.relative_to(REPO_ROOT)),
        "sha256": sha256_file(path),
    }
    if identity != expected:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", f"v1.5 parent identity가 다릅니다: {expected['path']}"
        )


def validate_contract_registry_v1_5() -> dict[str, Any]:
    """v1.4 원국 권한을 보존한 v1.5 hash chain을 검증한다."""

    validate_contract_registry_v1_4()
    registry = load_strict_json_object_v1_2(REGISTRY_V15_PATH)
    if (
        sha256_file(REGISTRY_V15_PATH) != REGISTRY_V15_SHA256
        or registry.get("schema_version") != "1.5.0"
        or registry.get("registry_id") != "saju-runtime-calculation-registry-v1.5.0"
        or registry.get("status")
        != "approved_chart_and_single_day_feature_default_off"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.5 runtime registry 값·hash가 다릅니다."
        )
    _validate_parent(registry.get("parent"), path=REGISTRY_V14_PATH)
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.5 artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.5 artifact 계약이 다릅니다."
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
                "CONTRACT_REGISTRY_INVALID", "v1.5 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH", f"v1.5 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS_V15:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.5 artifact 집합이 다릅니다."
        )

    contract = load_strict_json_object_v1_2(CONTRACT_V15_PATH)
    source = load_strict_json_object_v1_2(SOURCE_REGISTRY_V18_PATH)
    gate = load_strict_json_object_v1_2(GATE_V18_PATH)
    profile = load_strict_json_object_v1_2(PROFILE_V15_PATH)
    schema = load_strict_json_object_v1_2(OUTPUT_SCHEMA_V15_PATH)
    release_schema = load_strict_json_object_v1_2(RELEASE_SCHEMA_V15_PATH)
    minimums = gate.get("minimum_cases", {})
    if (
        contract.get("runtime_contract_version") != "saju-runtime-contract-v1.5.0"
        or contract.get("engine_version") != ENGINE_VERSION_V15
        or contract.get("output_schema_version") != OUTPUT_SCHEMA_VERSION_V15
        or contract.get("conformance_suite_version") != SUITE_VERSION_V10
        or contract.get("approval_scope") != APPROVED_SCOPE_V15
        or contract.get("approved_tools")
        != ["calculate_saju_chart", "calculate_saju_period"]
        or contract.get("blocked_tools") != []
        or contract.get("feature_flag_default") is not False
        or contract.get("single_day_range")
        != {"minimum": SINGLE_DAY_START_DATE, "maximum": SINGLE_DAY_END_DATE}
        or source.get("registry_version") != SOURCE_REGISTRY_VERSION_V18
        or source.get("runtime_provider", {}).get("provider_id")
        != SkyfieldSolarTermProvider.provider_id
        or source.get("runtime_provider", {}).get("strict_runtime_approved")
        is not False
        or source.get("single_day_official_scope", {}).get("civil_dates")
        != SINGLE_DAY_CASES
        or gate.get("suite_version") != SUITE_VERSION_V10
        or gate.get("approval_scope") != APPROVED_SCOPE_V15
        or minimums.get("single_day_dates") != SINGLE_DAY_CASES
        or minimums.get("provider_label_mismatches") != 0
        or minimums.get("noon_boundary_quarantine_dates") != 0
        or profile.get("profile_revision") != "KR_CIVIL_MIDNIGHT_V1-v1.5.0"
        or profile.get("approved_single_day_range")
        != {"minimum": SINGLE_DAY_START_DATE, "maximum": SINGLE_DAY_END_DATE}
        or schema.get("$id") != "saju-calculation-output-v1.5.0"
        or release_schema.get("$id") != "saju-runtime-release-registry-v1.5.0"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID", "v1.5 runtime·source·gate·profile 상태가 다릅니다."
        )
    _validate_parent(
        source.get("parent"), path=CONFIG_ROOT / "source_registry-v1.7.0.json"
    )
    _validate_parent(
        gate.get("parent"), path=CONFIG_ROOT / "conformance_gate-v1.7.0.json"
    )
    _validate_parent(
        profile.get("parent"),
        path=CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.4.0.json",
    )
    return registry


def runtime_source_versions_v1_5(
    *,
    require_runtime_dependencies: bool,
    provider_identity: dict[str, Any] | None = None,
    release_id: str | None = None,
    release_registry_sha256: str | None = None,
) -> dict[str, str]:
    validate_contract_registry_v1_5()
    parent = validate_release_registry_v1_4(RELEASE_V14_PATH)
    versions = runtime_source_versions_v1_4(
        require_runtime_dependencies=require_runtime_dependencies,
        provider_identity=provider_identity,
        release_id=parent["release_id"],
        release_registry_sha256=parent["release_registry_sha256"],
    )
    versions["parent_chart_runtime_release"] = versions.pop("runtime_release")
    versions["parent_chart_runtime_release_registry_sha256"] = versions.pop(
        "runtime_release_registry_sha256"
    )
    versions.update(
        {
            "source_registry": SOURCE_REGISTRY_VERSION_V18,
            "runtime_contract": "saju-runtime-contract-v1.5.0",
            "runtime_scope": APPROVED_SCOPE_V15,
            "approved_single_day_range": (
                f"{SINGLE_DAY_START_DATE}/{SINGLE_DAY_END_DATE}"
            ),
            "single_day_evaluation_local_time": SINGLE_DAY_EVALUATION_LOCAL_TIME,
        }
    )
    if release_id is not None:
        if RELEASE_ID.fullmatch(release_id) is None:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_INVALID", "v1.5 release ID가 다릅니다."
            )
        versions["runtime_release"] = release_id
    if release_registry_sha256 is not None:
        if FULL_SHA.fullmatch(release_registry_sha256) is None:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_INVALID", "v1.5 release hash가 다릅니다."
            )
        versions["runtime_release_registry_sha256"] = release_registry_sha256
    return versions


def derive_gate_checks_v1_5(report: dict[str, Any]) -> dict[str, bool]:
    """보고된 pass boolean을 신뢰하지 않고 v10 집계값으로 다시 계산한다."""

    try:
        parent = report["parent_v9"]
        matrix = report["single_day_official_matrix"]
        runtime = report["runtime_contract_gate"]
        governance = report["governance"]
    except (KeyError, TypeError) as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v10 집계에서 Gate를 재계산할 수 없습니다."
        ) from exc
    return {
        "parent_v9_verified": parent.get("verified") is True,
        "single_day_cases_complete": matrix.get("cases") == SINGLE_DAY_CASES,
        "single_day_range_exact": matrix.get("date_range")
        == [SINGLE_DAY_START_DATE, SINGLE_DAY_END_DATE],
        "official_day_rows_complete": matrix.get("official_day_rows")
        == SINGLE_DAY_CASES,
        "official_jie_coverage_complete": matrix.get("official_jie_rows") == 2_172,
        "provider_label_mismatches_zero": matrix.get("provider_label_mismatches")
        == 0,
        "noon_boundary_quarantine_zero": matrix.get(
            "noon_boundary_quarantine_dates"
        )
        == 0,
        "provider_values_not_written_as_official": matrix.get(
            "provider_values_written_to_official_snapshot"
        )
        is False,
        "runtime_positive_cases_complete": runtime.get("positive_cases") == 3,
        "runtime_positive_failures_zero": runtime.get("positive_failures") == 0,
        "runtime_negative_cases_complete": runtime.get("negative_cases") == 12,
        "runtime_negative_failures_zero": runtime.get("negative_failures") == 0,
        "server_kst_today_floor_enforced": runtime.get(
            "server_kst_today_floor_enforced"
        )
        is True,
        "only_exact_current_process_chart": runtime.get(
            "only_exact_current_process_chart"
        )
        is True,
        "governance_remains_closed": all(
            governance.get(field) is False
            for field in (
                "runtime_feature_flag_default",
                "strict_runtime_provider_gate_passed",
                "full_runtime_gate_passed",
                "production_application_binding",
                "mix20k_v3_1_regeneration_allowed",
                "training_promotion_allowed",
                "sealed_blind_accessed",
            )
        ),
    }


def _validate_report_identity_v1_5(
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
            "RUNTIME_RELEASE_INVALID", "v1.5 conformance identity가 다릅니다."
        )
    report_path = _safe_repo_path(identity["path"])
    manifest_path = _safe_repo_path(identity["manifest_path"])
    try:
        report_path.relative_to(REPORT_V18_ROOT.resolve())
        manifest_path.relative_to(REPORT_V18_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v10 보고서가 고정 경로를 벗어납니다."
        ) from exc
    if (
        report_path.name != "aggregate.json"
        or manifest_path.name != "build_manifest.json"
        or report_path.parent != manifest_path.parent
        or report_path.parent.parent != REPORT_V18_ROOT.resolve()
        or report_path.parent.name != identity["build_id"]
        or report_path.is_symlink()
        or manifest_path.is_symlink()
        or not report_path.is_file()
        or not manifest_path.is_file()
        or sha256_file(report_path) != identity["sha256"]
        or sha256_file(manifest_path) != identity["manifest_sha256"]
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_HASH_MISMATCH", "v10 report·manifest hash가 다릅니다."
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
        or report.get("schema_version") != "1.8.0"
        or report.get("suite_version") != SUITE_VERSION_V10
        or report.get("engine_version") != ENGINE_VERSION_V15
        or report.get("approval_scope") != APPROVED_SCOPE_V15
        or report.get("chart_and_single_day_gate_passed") is not True
        or report.get("release_registry_creation_allowed") is not True
        or report.get("strict_runtime_provider_gate_passed") is not False
        or report.get("full_runtime_gate_passed") is not False
        or report.get("runtime_feature_flag_default") is not False
        or not isinstance(implementations, dict)
        or set(implementations) != CONFORMANCE_V10_IMPLEMENTATIONS
        or any(
            not isinstance(value, str)
            or FULL_SHA.fullmatch(value) is None
            or sha256_file(_safe_repo_path(relative)) != value
            for relative, value in implementations.items()
        )
        or manifest.get("schema_version") != "1.0.0"
        or manifest.get("suite_version") != SUITE_VERSION_V10
        or manifest.get("build_id") != build_id
        or manifest.get("private_content_included") is not False
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or not isinstance(artifact, dict)
        or artifact.get("sha256") != identity["sha256"]
        or artifact.get("bytes") != report_path.stat().st_size
        or not all(derive_gate_checks_v1_5(report).values())
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v10 report·manifest 내용이 승인 조건과 다릅니다."
        )
    return report, manifest


def release_id_for_v1_5(report_sha256: str, manifest_sha256: str) -> str:
    if FULL_SHA.fullmatch(report_sha256) is None or FULL_SHA.fullmatch(
        manifest_sha256
    ) is None:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.5 release preimage hash가 다릅니다."
        )
    preimage = {
        "conformance_report_sha256": report_sha256,
        "conformance_manifest_sha256": manifest_sha256,
        "engine_version": ENGINE_VERSION_V15,
        "approval_scope": APPROVED_SCOPE_V15,
        "approved_tools": ["calculate_saju_chart", "calculate_saju_period"],
    }
    digest = hashlib.sha256(canonical_json_bytes(preimage)).hexdigest()[:12]
    return f"saju-runtime-release-v1.5.0-{digest}"


def validate_release_registry_v1_5(path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_5()
    candidate = Path(path)
    expected = _safe_repo_path(str(RELEASE_V15_PATH.relative_to(REPO_ROOT)))
    if (
        candidate.resolve(strict=False) != expected.resolve(strict=False)
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_REQUIRED", "고정 v1.5 원국+단일 일진 release가 없습니다."
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
        "approved_single_day_range",
        "single_day_evaluation_local_time",
        "single_day_dates",
        "single_day_label_mismatches",
        "noon_boundary_quarantine_dates",
        "parent_v1_4_release",
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
    if set(release) != required or RELEASE_ID.fullmatch(
        str(release.get("release_id", ""))
    ) is None:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.5 release registry schema가 다릅니다."
        )
    parent = validate_release_registry_v1_4(RELEASE_V14_PATH)
    expected_parent = {
        "release_id": parent["release_id"],
        "path": str(RELEASE_V14_PATH.relative_to(REPO_ROOT)),
        "sha256": parent["release_registry_sha256"],
    }
    if (
        release.get("status")
        != "approved_chart_and_single_day_feature_default_off"
        or release.get("engine_version") != ENGINE_VERSION_V15
        or release.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or release.get("profile_id") != POLICY_ID
        or release.get("approval_scope") != APPROVED_SCOPE_V15
        or release.get("approved_tools")
        != ["calculate_saju_chart", "calculate_saju_period"]
        or release.get("blocked_tools") != []
        or release.get("approved_solar_date_range")
        != {"minimum": APPROVED_START_DATE, "maximum": APPROVED_END_DATE}
        or release.get("approved_single_day_range")
        != {"minimum": SINGLE_DAY_START_DATE, "maximum": SINGLE_DAY_END_DATE}
        or release.get("single_day_evaluation_local_time")
        != SINGLE_DAY_EVALUATION_LOCAL_TIME
        or release.get("single_day_dates") != SINGLE_DAY_CASES
        or release.get("single_day_label_mismatches") != 0
        or release.get("noon_boundary_quarantine_dates") != 0
        or release.get("parent_v1_4_release") != expected_parent
        or release.get("runtime_registry_sha256") != sha256_file(REGISTRY_V15_PATH)
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
            "RUNTIME_RELEASE_INVALID", "v1.5 원국+단일 일진 승인 값이 다릅니다."
        )
    report, manifest = _validate_report_identity_v1_5(
        release["conformance_report"]
    )
    if release["release_id"] != release_id_for_v1_5(
        release["conformance_report"]["sha256"],
        release["conformance_report"]["manifest_sha256"],
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.5 release ID preimage가 다릅니다."
        )
    if release["implementation_sha256"] != report["inputs"]["implementation_sha256"]:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v10 구현 hash가 다릅니다."
        )
    if release["official_snapshots"] != report["inputs"]["official_snapshots"]:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v10 공식 snapshot이 다릅니다."
        )
    return {
        **release,
        "release_registry_sha256": sha256_file(candidate),
        "conformance_report_data": report,
        "conformance_manifest_data": manifest,
        "ephemeris_sha256": DE440S_SHA256,
    }
