# contracts_v1_2.py - 천문 v4 Gate·HMAC ID·release v1.2 hash chain을 검증한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .contracts import CONFIG_ROOT, POLICY_ID, REPO_ROOT, load_json_object, sha256_file
from .contracts_v1_1 import (
    VALIDATOR_VERSIONS,
    runtime_source_versions_v1_1,
    validate_contract_registry_v1_1,
)
from .errors import RuntimeCalculationError

REGISTRY_V12_PATH = CONFIG_ROOT / "registry-v1.2.0.json"
CONTRACT_V12_PATH = CONFIG_ROOT / "runtime_contract-v1.2.0.json"
SOURCE_REGISTRY_V12_PATH = CONFIG_ROOT / "source_registry-v1.2.0.json"
GATE_V12_PATH = CONFIG_ROOT / "conformance_gate-v1.2.0.json"
PROFILE_V12_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.2.0.json"
OUTPUT_SCHEMA_V12_PATH = CONFIG_ROOT / "calculation_output_schema-v1.2.0.json"
ID_CONTRACT_V2_PATH = CONFIG_ROOT / "id_canonicalization-v2.0.0.json"
RELEASE_SCHEMA_V12_PATH = CONFIG_ROOT / "release_registry_schema-v1.2.0.json"
REPORT_V12_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.2.0"
ENGINE_VERSION_V12 = "saju-runtime-python-v1.2.0"
OUTPUT_SCHEMA_VERSION_V12 = "1.2.0"
ID_CONTRACT_VERSION_V2 = "saju-runtime-id-hmac-v2.0.0"
SUITE_VERSION_V4 = "saju-runtime-conformance-v4.0.0"
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID = re.compile(r"^saju-runtime-release-v1\.2\.0-[0-9a-f]{12}$")
CONFORMANCE_BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
CONFORMANCE_V4_IMPLEMENTATIONS = frozenset(
    {
        "scripts/runtime/calculation/canonical.py",
        "scripts/runtime/calculation/contracts.py",
        "scripts/runtime/calculation/contracts_v1_1.py",
        "scripts/runtime/calculation/contracts_v1_2.py",
        "scripts/runtime/calculation/id_signer.py",
        "scripts/runtime/calculation/timezone_resolver.py",
        "scripts/runtime/calculation/calendar_provider.py",
        "scripts/runtime/calculation/normalize.py",
        "scripts/runtime/calculation/solar_terms.py",
        "scripts/runtime/calculation/facts.py",
        "scripts/runtime/calculation/engine.py",
        "scripts/runtime/calculation/engine_v1_2.py",
        "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/kasi_minute_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/jie_crosscheck.py",
        "scripts/evaluation/saju_runtime/jie_crosscheck_v1_2.py",
        "scripts/evaluation/saju_runtime/conformance_v4.py",
    }
)
EXPECTED_ARTIFACTS = {
    "configs/runtime/calculation/runtime_contract-v1.2.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.2.0.json",
    "configs/runtime/calculation/source_registry-v1.2.0.json",
    "configs/runtime/calculation/conformance_gate-v1.2.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.2.0.json",
    "configs/runtime/calculation/release_registry_schema-v1.2.0.json",
    "configs/runtime/calculation/id_canonicalization-v2.0.0.json",
    "configs/runtime/session_state_schema_v2.json",
    "configs/runtime/intake_fsm-v1.0.0.json",
    "configs/runtime/intake_fsm_gate-v1.0.0.json",
    "requirements-runtime-calculator-v1.2.txt",
}
REPORT_ARTIFACTS = {
    "aggregate.json",
    "independent_records.jsonl",
    "delta_by_year.svg",
}
EXPECTED_MINIMUM_CASES = {
    "kasi_lunar_days": 54_787,
    "kasi_all_solar_term_dates_collected": 3_600,
    "kasi_jie_dates_compared": 1_800,
    "kasi_jie_minute_references": 84,
    "independent_jie_instants": 1_800,
    "internal_profile_boundary_assignment": 5_400,
    "unknown_range": 500,
    "hmac_id_vectors": 200,
    "unsupported_foreign": 20,
}
EXPECTED_MAXIMUM_FAILURES = {
    "kasi_lunar_conversion_mismatch": 0,
    "kasi_day_ganzhi_mismatch": 0,
    "runtime_kasi_jie_date_mismatch": 0,
    "unresolved_engine_local_date_disagreement": 0,
    "runtime_kasi_nearest_minute_label_mismatch": 0,
    "independent_kasi_nearest_minute_label_mismatch": 0,
    "independent_jie_fixed_regression_delta_seconds": 120,
    "independent_term_identity_mismatch": 0,
    "independent_chronological_order_failure": 0,
    "internal_profile_boundary_assignment_mismatch": 0,
    "hmac_id_mismatch": 0,
    "guessed_unknown_hour": 0,
    "dst_gap_auto_shift": 0,
    "dst_fold_auto_pick": 0,
    "host_timezone_or_locale_drift": 0,
    "heuristic_fact_leak": 0,
    "unclassified_mismatch": 0,
    "silent_unsupported_fallback": 0,
    "id_version_instability": 0,
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


def load_strict_json_object_v1_2(path: Path) -> dict[str, Any]:
    """v1.2 활성 계약·release·report의 중복 key를 모든 수준에서 거부한다."""

    if path.is_symlink() or not path.is_file():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", f"v1.2 JSON 파일이 없거나 symlink입니다: {path}"
        )
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicates,
        )
    except _DuplicateJsonKey as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", f"v1.2 JSON에 중복 key가 있습니다: {exc}"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", f"v1.2 JSON을 읽지 못했습니다: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 JSON 최상위는 object여야 합니다."
        )
    return value


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.2 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "v1.2 계약 경로에 symlink가 포함됐습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.2 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def validate_contract_registry_v1_2() -> dict[str, Any]:
    validate_contract_registry_v1_1()
    registry = load_strict_json_object_v1_2(REGISTRY_V12_PATH)
    if (
        registry.get("schema_version") != "1.2.0"
        or registry.get("registry_id")
        != "saju-runtime-calculation-registry-v1.2.0"
        or registry.get("status") != "v1_2_runtime_gate_release_pending"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.2 runtime registry 값이 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.2 artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.2 artifact 계약이 다릅니다."
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
                "CONTRACT_REGISTRY_INVALID", "v1.2 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH", f"v1.2 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.2 artifact 집합이 다릅니다."
        )

    contract = load_strict_json_object_v1_2(CONTRACT_V12_PATH)
    profile = load_strict_json_object_v1_2(PROFILE_V12_PATH)
    gate = load_strict_json_object_v1_2(GATE_V12_PATH)
    id_contract = load_strict_json_object_v1_2(ID_CONTRACT_V2_PATH)
    session = load_json_object(REPO_ROOT / "configs/runtime/session_state_schema_v2.json")
    fsm = load_json_object(REPO_ROOT / "configs/runtime/intake_fsm-v1.0.0.json")
    fsm_gate = load_json_object(REPO_ROOT / "configs/runtime/intake_fsm_gate-v1.0.0.json")
    if (
        contract.get("engine_version") != ENGINE_VERSION_V12
        or contract.get("output_schema_version") != OUTPUT_SCHEMA_VERSION_V12
        or contract.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or contract.get("runtime_approved_in_static_contract") is not False
        or contract.get("production_id_key_required") is not True
        or profile.get("policy_id") != POLICY_ID
        or profile.get("runtime_approved_in_static_contract") is not False
        or gate.get("suite_version") != SUITE_VERSION_V4
        or gate.get("profile_id") != POLICY_ID
        or gate.get("minimum_cases") != EXPECTED_MINIMUM_CASES
        or gate.get("maximum_failures") != EXPECTED_MAXIMUM_FAILURES
        or gate.get("minute_label_policy", {}).get("absolute_tolerance_gate_allowed")
        is not False
        or gate.get("minute_label_policy", {}).get("project_equivalence_rule")
        != "nearest_minute_half_up_in_asia_seoul"
        or gate.get("official_adjudication", {}).get(
            "engine_date_disagreement_without_official_row"
        )
        != "blocking_unresolved"
        or gate.get("independent_crosscheck", {}).get(
            "fixed_regression_guard_seconds"
        )
        != 120
        or gate.get("independent_crosscheck", {}).get(
            "guard_is_physical_accuracy_budget"
        )
        is not False
        or id_contract.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or id_contract.get("mac") != "HMAC-SHA-256"
        or id_contract.get("key_contract", {}).get("production_missing_key_policy")
        != "fail_closed"
        or session.get("session_state_schema_version") != "saju-session-state-v2"
        or fsm.get("fsm_version") != "saju-intake-fsm-v1.0.0"
        or fsm.get("free_text_parser_in_fsm") is not False
        or fsm_gate.get("gate_version") != "saju-intake-fsm-gate-v1.0.0"
        or fsm_gate.get("required_passed_cases") != 100
        or fsm_gate.get("training_promotion_allowed") is not False
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID", "v1.2 runtime·HMAC·FSM 상태가 다릅니다."
        )
    parents = contract.get("parents")
    if not isinstance(parents, dict) or not parents:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", "v1.2 parent 계약이 비었습니다."
        )
    for name, identity in parents.items():
        if (
            not isinstance(identity, dict)
            or set(identity) != {"path", "sha256"}
            or not isinstance(identity["path"], str)
            or not isinstance(identity["sha256"], str)
            or FULL_SHA.fullmatch(identity["sha256"]) is None
        ):
            raise RuntimeCalculationError(
                "CONTRACT_PARENT_INVALID", f"v1.2 parent identity가 다릅니다: {name}"
            )
        path = _safe_repo_path(identity["path"])
        if not path.is_file() or sha256_file(path) != identity["sha256"]:
            raise RuntimeCalculationError(
                "CONTRACT_PARENT_HASH_MISMATCH", f"v1.2 parent hash가 다릅니다: {name}"
            )
    sources = load_strict_json_object_v1_2(SOURCE_REGISTRY_V12_PATH)
    parent = sources.get("parent")
    validator = sources.get("validator")
    if (
        sources.get("registry_version") != "saju-runtime-sources-v1.2.0"
        or not isinstance(parent, dict)
        or parent.get("sha256")
        != sha256_file(CONFIG_ROOT / "source_registry-v1.1.0.json")
        or not isinstance(validator, dict)
        or validator.get("crosscheck_version") != "saju-jie-crosscheck-v1.2.0"
        or validator.get("implementation_sha256")
        != sha256_file(REPO_ROOT / validator.get("implementation_path", "missing"))
        or validator.get("local_date_scope") != "engine_vs_engine_unadjudicated"
    ):
        raise RuntimeCalculationError(
            "SOURCE_REGISTRY_INVALID", "v1.2 source·validator identity가 다릅니다."
        )
    return registry


def runtime_source_versions_v1_2(
    *,
    require_runtime_dependencies: bool,
    require_validator_dependencies: bool = False,
) -> dict[str, str]:
    validate_contract_registry_v1_2()
    versions = runtime_source_versions_v1_1(
        require_runtime_dependencies=require_runtime_dependencies,
        require_validator_dependencies=require_validator_dependencies,
    )
    versions.update(
        {
            "source_registry": "saju-runtime-sources-v1.2.0",
            "runtime_contract": "saju-runtime-contract-v1.2.0",
            "id_contract": ID_CONTRACT_VERSION_V2,
        }
    )
    if require_validator_dependencies:
        for package, expected in VALIDATOR_VERSIONS.items():
            try:
                actual = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError as exc:
                raise RuntimeCalculationError(
                    "VALIDATOR_DEPENDENCY_MISSING",
                    f"절입 validator 패키지가 없습니다: {package}",
                ) from exc
            if actual != expected:
                raise RuntimeCalculationError(
                    "VALIDATOR_VERSION_MISMATCH",
                    f"{package} {expected}가 필요하지만 {actual}입니다.",
                )
    return versions


def _load_release(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_REQUIRED", "승인된 v1.2 runtime release registry가 없습니다."
        )
    return load_strict_json_object_v1_2(path)


def derive_gate_checks_v1_2(report: dict[str, Any]) -> dict[str, bool]:
    """release 승인 시 보고된 bool을 신뢰하지 않고 집계값에서 다시 계산한다."""

    try:
        gate = load_strict_json_object_v1_2(GATE_V12_PATH)
        minimum = gate["minimum_cases"]
        lunar = report["official_kasi_lunisolar"]
        term_dates = report["official_kasi_solar_term_dates"]
        minute = report["institutional_kasi_minute_reference"]
        independent = report["independent_jie_crosscheck"]
        adjudication = report["official_local_date_adjudication"]
        boundary = report["internal_profile_boundary_assignment_checks"]
        policy = report["policy_comparison"]
        invariants = report["synthetic_invariants"]
        hmac_invariants = report["hmac_id_invariants"]
        host = report["host_invariance"]
        lunar_complete = lunar["rows"] == minimum["kasi_lunar_days"]
        terms_complete = (
            term_dates["all_term_rows_collected"]
            == minimum["kasi_all_solar_term_dates_collected"]
            and term_dates["jie_rows_compared"]
            == minimum["kasi_jie_dates_compared"]
        )
        minute_complete = (
            minute["rows"] == minimum["kasi_jie_minute_references"]
        )
        independent_complete = (
            independent["rows"] == minimum["independent_jie_instants"]
        )
        mismatch_rows = [
            *term_dates["mismatches"],
            *boundary["mismatches"],
            *policy["mismatches"],
        ]
    except (KeyError, TypeError) as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID",
            "v1.2 conformance 집계에서 Gate를 재계산할 수 없습니다.",
        ) from exc
    return {
        "kasi_lunar_days_complete": lunar_complete,
        "kasi_lunar_conversion_mismatch_zero": lunar_complete
        and lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar_complete
        and lunar["day_ganzhi_mismatches"] == 0,
        "kasi_all_solar_term_dates_complete": terms_complete,
        "runtime_kasi_jie_date_mismatch_zero": terms_complete
        and term_dates["runtime_date_mismatches"] == 0,
        "engine_local_date_adjudication_evidence_complete": independent_complete
        and adjudication["unresolved_disagreements"] == 0,
        "runtime_matches_official_on_engine_date_disagreements": independent_complete
        and adjudication["unresolved_disagreements"] == 0
        and adjudication["runtime_official_mismatches_on_disagreements"] == 0,
        "kasi_jie_minute_references_complete": minute_complete,
        "runtime_kasi_nearest_minute_label_match": minute_complete
        and minute["runtime_display_minute_mismatches"] == 0,
        "independent_kasi_nearest_minute_label_match": minute_complete
        and independent_complete
        and minute["independent_missing_rows"] == 0
        and minute["independent_display_minute_mismatches"] == 0,
        "independent_jie_instants_complete": independent_complete,
        "independent_jie_fixed_120_second_regression_guard": independent_complete
        and independent["threshold_failures"] == 0
        and independent.get("fixed_regression_guard", {}).get(
            "maximum_delta_seconds"
        )
        == 120.0,
        "independent_term_identity_zero": independent_complete
        and independent["term_identity_failures"] == 0,
        "independent_chronological_order_zero": independent_complete
        and independent["chronological_order_failures"] == 0,
        "delta_t_diagnostic_complete": independent_complete
        and independent.get("delta_t_diagnostic", {}).get("status")
        in {"delta_t_not_primary", "delta_t_may_be_primary"},
        "internal_profile_boundary_cases_complete": boundary["cases"]
        == minimum["internal_profile_boundary_assignment"],
        "internal_profile_boundary_mismatch_zero": boundary["mismatch_rows"] == 0,
        "policy_fixture_mismatch_zero": policy["mismatch_rows"] == 0,
        "unknown_range_complete": invariants["unknown_range_cases"]
        >= minimum["unknown_range"],
        "guessed_unknown_hour_zero": invariants["guessed_unknown_hour"] == 0,
        "hmac_id_vectors_complete": hmac_invariants["vectors"]
        >= minimum["hmac_id_vectors"],
        "hmac_id_mismatch_zero": all(
            hmac_invariants[key] == 0
            for key in (
                "reproducibility_mismatches",
                "prefix_failures",
                "domain_collisions",
                "key_separation_failures",
            )
        ),
        "unsupported_foreign_complete": invariants["unsupported_foreign_cases"]
        >= minimum["unsupported_foreign"],
        "unsupported_foreign_failure_zero": invariants[
            "unsupported_foreign_failures"
        ]
        == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "source_version_id_failure_zero": invariants[
            "source_version_id_failures"
        ]
        == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
        "unclassified_mismatch_zero": all(
            isinstance(item, dict) and bool(item.get("category"))
            for item in mismatch_rows
        ),
    }


def _validate_report_identity_v1_2(identity: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {"path", "sha256", "manifest_path", "manifest_sha256", "build_id"}
    if (
        not isinstance(identity, dict)
        or set(identity) != required
        or not all(isinstance(identity[key], str) for key in required)
        or FULL_SHA.fullmatch(identity["sha256"]) is None
        or FULL_SHA.fullmatch(identity["manifest_sha256"]) is None
        or CONFORMANCE_BUILD_ID.fullmatch(identity["build_id"]) is None
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 conformance identity가 다릅니다."
        )
    report_path = _safe_repo_path(identity["path"])
    manifest_path = _safe_repo_path(identity["manifest_path"])
    try:
        report_path.resolve().relative_to(REPORT_V12_ROOT.resolve())
        manifest_path.resolve().relative_to(REPORT_V12_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "conformance 보고서가 v1.2 범위를 벗어납니다."
        ) from exc
    if (
        report_path.name != "aggregate.json"
        or manifest_path.name != "build_manifest.json"
        or report_path.parent != manifest_path.parent
        or report_path.parent.name != identity["build_id"]
        or report_path.is_symlink()
        or manifest_path.is_symlink()
        or not report_path.is_file()
        or not manifest_path.is_file()
        or sha256_file(report_path) != identity["sha256"]
        or sha256_file(manifest_path) != identity["manifest_sha256"]
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_HASH_MISMATCH", "v1.2 report·manifest hash가 다릅니다."
        )
    report = load_strict_json_object_v1_2(report_path)
    manifest = load_strict_json_object_v1_2(manifest_path)
    expected_build_id = "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != REPORT_ARTIFACTS:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 report artifact 집합이 다릅니다."
        )
    for filename, artifact in artifacts.items():
        path = report_path.parent / filename
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"bytes", "sha256"}
            or isinstance(artifact["bytes"], bool)
            or not isinstance(artifact["bytes"], int)
            or not isinstance(artifact["sha256"], str)
            or FULL_SHA.fullmatch(artifact["sha256"]) is None
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != artifact["bytes"]
            or sha256_file(path) != artifact["sha256"]
        ):
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_HASH_MISMATCH", f"v1.2 report artifact가 다릅니다: {filename}"
            )
    inputs = report.get("inputs")
    implementations = inputs.get("implementation_sha256") if isinstance(inputs, dict) else None
    official = inputs.get("official_snapshots") if isinstance(inputs, dict) else None
    diagnostics = report.get("public_diagnostics")
    manifest_fields = {
        "schema_version",
        "build_id",
        "report_type",
        "artifacts",
        "runtime_gate_passed",
        "release_registry_creation_allowed",
        "mix20k_v3_1_regeneration_allowed",
        "training_promotion_allowed",
        "phase5_training_performed",
    }
    if (
        expected_build_id != identity["build_id"]
        or report.get("schema_version") != "1.2.0"
        or report.get("suite_version") != SUITE_VERSION_V4
        or report.get("engine_version") != ENGINE_VERSION_V12
        or report.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or report.get("profile_id") != POLICY_ID
        or report.get("status") != "passed"
        or report.get("runtime_gate_passed") is not True
        or report.get("release_registry_creation_allowed") is not True
        or report.get("runtime_feature_flag_default") is not False
        or report.get("mix20k_v3_1_regeneration_allowed") is not False
        or report.get("training_promotion_allowed") is not False
        or report.get("phase5_training_performed") is not False
        or report.get("blocking_reasons") != []
        or not isinstance(inputs, dict)
        or inputs.get("runtime_registry_sha256") != sha256_file(REGISTRY_V12_PATH)
        or inputs.get("gate_sha256") != sha256_file(GATE_V12_PATH)
        or not isinstance(implementations, dict)
        or set(implementations) != CONFORMANCE_V4_IMPLEMENTATIONS
        or not isinstance(official, dict)
        or set(official)
        != {"kasi_lunisolar", "kasi_24_divisions", "kasi_minute_reference"}
        or any(
            not isinstance(value, dict)
            or value.get("provided") is not True
            or value.get("complete") is not True
            or value.get("private_path_recorded") is not False
            or FULL_SHA.fullmatch(str(value.get("sha256", ""))) is None
            or FULL_SHA.fullmatch(str(value.get("manifest_sha256", ""))) is None
            for value in official.values()
        )
        or manifest.get("schema_version") != "1.2.0"
        or set(manifest) != manifest_fields
        or manifest.get("report_type") != "saju_runtime_conformance_v4"
        or manifest.get("build_id") != identity["build_id"]
        or manifest.get("runtime_gate_passed") is not True
        or manifest.get("release_registry_creation_allowed") is not True
        or manifest.get("mix20k_v3_1_regeneration_allowed") is not False
        or manifest.get("training_promotion_allowed") is not False
        or manifest.get("phase5_training_performed") is not False
        or not isinstance(diagnostics, dict)
        or diagnostics.get("independent_records", {}).get("sha256")
        != artifacts["independent_records.jsonl"]["sha256"]
        or diagnostics.get("delta_by_year_svg", {}).get("sha256")
        != artifacts["delta_by_year.svg"]["sha256"]
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 conformance 통과 상태가 다릅니다."
        )
    checks = report.get("gate_checks")
    derived_checks = derive_gate_checks_v1_2(report)
    if (
        not isinstance(checks, dict)
        or not checks
        or checks != derived_checks
        or any(value is not True for value in checks.values())
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID",
            "v1.2 conformance Gate가 집계 재계산과 일치하며 전부 true가 아닙니다.",
        )
    return report, manifest


def release_id_for_v1_2(report_sha256: str, manifest_sha256: str) -> str:
    preimage = {
        "runtime_registry_sha256": sha256_file(REGISTRY_V12_PATH),
        "conformance_report_sha256": report_sha256,
        "conformance_manifest_sha256": manifest_sha256,
        "engine_version": ENGINE_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "profile_id": POLICY_ID,
    }
    return "saju-runtime-release-v1.2.0-" + hashlib.sha256(
        canonical_json_bytes(preimage)
    ).hexdigest()[:12]


def validate_release_registry_v1_2(path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_2()
    release = _load_release(path)
    required = {
        "release_id",
        "status",
        "engine_version",
        "id_contract_version",
        "profile_id",
        "runtime_registry_sha256",
        "conformance_report",
        "official_snapshots",
        "implementation_sha256",
        "production_id_key_required",
        "runtime_feature_flag_default",
        "training_promotion_allowed",
        "sealed_blind_accessed",
    }
    if set(release) != required or RELEASE_ID.fullmatch(str(release.get("release_id", ""))) is None:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 release registry schema가 다릅니다."
        )
    if (
        release.get("status") != "approved_runtime_feature_default_off"
        or release.get("engine_version") != ENGINE_VERSION_V12
        or release.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or release.get("profile_id") != POLICY_ID
        or release.get("runtime_registry_sha256") != sha256_file(REGISTRY_V12_PATH)
        or release.get("production_id_key_required") is not True
        or release.get("runtime_feature_flag_default") is not False
        or release.get("training_promotion_allowed") is not False
        or release.get("sealed_blind_accessed") is not False
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 release 승인 값이 다릅니다."
        )
    report, manifest = _validate_report_identity_v1_2(release["conformance_report"])
    if release["release_id"] != release_id_for_v1_2(
        release["conformance_report"]["sha256"],
        release["conformance_report"]["manifest_sha256"],
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 release ID preimage가 다릅니다."
        )
    implementations = release["implementation_sha256"]
    if implementations != report.get("inputs", {}).get("implementation_sha256"):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v1.2 구현 hash가 다릅니다."
        )
    for relative, expected in implementations.items():
        path_value = _safe_repo_path(relative)
        if not path_value.is_file() or sha256_file(path_value) != expected:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_HASH_MISMATCH", f"v1.2 구현 hash가 다릅니다: {relative}"
            )
    if release["official_snapshots"] != report.get("inputs", {}).get("official_snapshots"):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 v1.2 공식 snapshot이 다릅니다."
        )
    return {
        **release,
        "release_registry_sha256": sha256_file(path),
        "conformance_report_data": report,
        "conformance_manifest_data": manifest,
    }
