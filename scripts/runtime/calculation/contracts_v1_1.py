# contracts_v1_1.py - 계층형 Gate·release registry·v1.1 source identity를 검증한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import re
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .contracts import (
    CONFIG_ROOT,
    POLICY_ID,
    REPO_ROOT,
    load_json_object,
    runtime_source_versions,
    sha256_file,
    validate_contract_registry,
)
from .errors import RuntimeCalculationError

REGISTRY_V11_PATH = CONFIG_ROOT / "registry-v1.1.0.json"
CONTRACT_V11_PATH = CONFIG_ROOT / "runtime_contract-v1.1.0.json"
SOURCE_REGISTRY_V11_PATH = CONFIG_ROOT / "source_registry-v1.1.0.json"
GATE_V11_PATH = CONFIG_ROOT / "conformance_gate-v1.1.0.json"
PROFILE_V11_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.1.0.json"
OUTPUT_SCHEMA_V11_PATH = CONFIG_ROOT / "calculation_output_schema-v1.1.0.json"
RELEASE_SCHEMA_V11_PATH = CONFIG_ROOT / "release_registry_schema-v1.1.0.json"
REPORT_V11_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.1.0"
ENGINE_VERSION_V11 = "saju-runtime-python-v1.1.0"
OUTPUT_SCHEMA_VERSION_V11 = "1.1.0"
SUITE_VERSION_V3 = "saju-runtime-conformance-v3.0.0"
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID = re.compile(r"^saju-runtime-release-v1\.1\.0-[0-9a-f]{12}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/calculation/runtime_contract-v1.1.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.1.0.json",
    "configs/runtime/calculation/source_registry-v1.1.0.json",
    "configs/runtime/calculation/conformance_gate-v1.1.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.1.0.json",
    "configs/runtime/calculation/release_registry_schema-v1.1.0.json",
    "requirements-runtime-calculator-v1.1.txt",
}
VALIDATOR_VERSIONS = {
    "skyfield": "1.55",
    "jplephem": "2.24",
    "numpy": "2.2.6",
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.1 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "v1.1 계약 경로에 symlink가 포함됐습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.1 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def validate_contract_registry_v1_1() -> dict[str, Any]:
    validate_contract_registry()
    registry = load_json_object(REGISTRY_V11_PATH)
    if (
        registry.get("schema_version") != "1.1.0"
        or registry.get("registry_id")
        != "saju-runtime-calculation-registry-v1.1.0"
        or registry.get("status") != "tiered_runtime_gate_release_pending"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.1 runtime registry 값이 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.1 artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.1 artifact 계약이 다릅니다."
            )
        relative = artifact["path"]
        expected_hash = artifact["sha256"]
        if (
            not isinstance(relative, str)
            or relative in seen
            or not isinstance(expected_hash, str)
            or FULL_SHA.fullmatch(expected_hash) is None
        ):
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.1 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH", f"v1.1 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.1 artifact 집합이 다릅니다."
        )
    contract = load_json_object(CONTRACT_V11_PATH)
    profile = load_json_object(PROFILE_V11_PATH)
    gate = load_json_object(GATE_V11_PATH)
    if (
        contract.get("engine_version") != ENGINE_VERSION_V11
        or contract.get("profile_id") != POLICY_ID
        or contract.get("runtime_approved_in_static_contract") is not False
        or contract.get("approval_requires_release_registry") is not True
        or profile.get("policy_id") != POLICY_ID
        or profile.get("runtime_approved_in_static_contract") is not False
        or gate.get("suite_version") != SUITE_VERSION_V3
        or gate.get("profile_id") != POLICY_ID
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID", "v1.1 runtime 승인·Gate 상태가 다릅니다."
        )
    parents = contract.get("parents")
    if not isinstance(parents, dict) or not parents:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", "v1.1 parent 계약이 비었습니다."
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
                "CONTRACT_PARENT_INVALID", f"v1.1 parent identity가 다릅니다: {name}"
            )
        path = _safe_repo_path(identity["path"])
        if not path.is_file() or sha256_file(path) != identity["sha256"]:
            raise RuntimeCalculationError(
                "CONTRACT_PARENT_HASH_MISMATCH", f"v1.1 parent hash가 다릅니다: {name}"
            )
    sources = load_json_object(SOURCE_REGISTRY_V11_PATH).get("sources")
    if not isinstance(sources, dict):
        raise RuntimeCalculationError(
            "SOURCE_REGISTRY_INVALID", "v1.1 source 목록이 object가 아닙니다."
        )
    collector_path = REPO_ROOT / "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py"
    minute_collector_path = REPO_ROOT / (
        "scripts/evaluation/saju_runtime/kasi_minute_collector_v1_1.py"
    )
    crosscheck_path = REPO_ROOT / "scripts/evaluation/saju_runtime/jie_crosscheck.py"
    lunar = sources.get("kasi_lunisolar_openapi", {})
    terms = sources.get("kasi_24_divisions_openapi", {})
    minute = sources.get("kasi_calendar_data", {})
    skyfield = sources.get("skyfield", {})
    ephemeris = sources.get("jpl_de440s", {})
    if (
        not isinstance(lunar, dict)
        or not isinstance(terms, dict)
        or lunar.get("collector_sha256") != sha256_file(collector_path)
        or terms.get("collector_sha256") != sha256_file(collector_path)
        or not isinstance(minute, dict)
        or minute.get("collector_sha256") != sha256_file(minute_collector_path)
        or minute.get("url")
        != "https://astro.kasi.re.kr/kor/life/post/calendarData"
        or minute.get("role")
        != "institutional_minute_display_reference_not_formal_almanac"
        or minute.get("generated_substitution_allowed") is not False
        or not isinstance(skyfield, dict)
        or skyfield.get("crosscheck_sha256") != sha256_file(crosscheck_path)
        or skyfield.get("version") != VALIDATOR_VERSIONS["skyfield"]
        or not isinstance(ephemeris, dict)
        or ephemeris.get("sha256")
        != "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"
    ):
        raise RuntimeCalculationError(
            "SOURCE_REGISTRY_INVALID", "v1.1 source version·구현 hash가 다릅니다."
        )
    return registry


def runtime_source_versions_v1_1(
    *,
    require_runtime_dependencies: bool,
    require_validator_dependencies: bool = False,
) -> dict[str, str]:
    validate_contract_registry_v1_1()
    versions = runtime_source_versions(
        require_dependencies=require_runtime_dependencies
    )
    versions.update(
        {
            "source_registry": "saju-runtime-sources-v1.1.0",
            "runtime_contract": "saju-runtime-contract-v1.1.0",
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
            versions[package] = actual
    return versions


def _load_release(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_REQUIRED", "승인된 runtime release registry가 없습니다."
        )
    return load_json_object(path)


def _validate_report_identity(identity: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {"path", "sha256", "manifest_path", "manifest_sha256", "build_id"}
    if not isinstance(identity, dict) or set(identity) != required:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release의 conformance identity가 다릅니다."
        )
    report_path = _safe_repo_path(identity["path"])
    manifest_path = _safe_repo_path(identity["manifest_path"])
    try:
        report_path.resolve().relative_to(REPORT_V11_ROOT.resolve())
        manifest_path.resolve().relative_to(REPORT_V11_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "conformance 보고서 경로가 v1.1 범위를 벗어납니다."
        ) from exc
    if (
        report_path.name != "aggregate.json"
        or manifest_path.name != "build_manifest.json"
        or report_path.parent != manifest_path.parent
        or report_path.parent.name != identity["build_id"]
        or sha256_file(report_path) != identity["sha256"]
        or sha256_file(manifest_path) != identity["manifest_sha256"]
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_HASH_MISMATCH", "conformance report·manifest hash가 다릅니다."
        )
    report = load_json_object(report_path)
    manifest = load_json_object(manifest_path)
    if (
        report.get("suite_version") != SUITE_VERSION_V3
        or report.get("engine_version") != ENGINE_VERSION_V11
        or report.get("profile_id") != POLICY_ID
        or report.get("status") != "passed"
        or report.get("runtime_gate_passed") is not True
        or report.get("training_promotion_allowed") is not False
        or report.get("sealed_blind_accessed") is not False
        or manifest.get("report_type") != "saju_runtime_conformance_v3"
        or manifest.get("build_id") != identity["build_id"]
        or manifest.get("aggregate_sha256") != identity["sha256"]
        or manifest.get("runtime_gate_passed") is not True
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "conformance 통과 상태가 release 계약과 다릅니다."
        )
    checks = report.get("gate_checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or any(value is not True for value in checks.values())
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "conformance Gate check가 전부 true가 아닙니다."
        )
    return report, manifest


def release_id_for(report_sha256: str, manifest_sha256: str) -> str:
    registry_hash = sha256_file(REGISTRY_V11_PATH)
    preimage = {
        "runtime_registry_sha256": registry_hash,
        "conformance_report_sha256": report_sha256,
        "conformance_manifest_sha256": manifest_sha256,
        "engine_version": ENGINE_VERSION_V11,
        "profile_id": POLICY_ID,
    }
    return "saju-runtime-release-v1.1.0-" + hashlib.sha256(
        canonical_json_bytes(preimage)
    ).hexdigest()[:12]


def validate_release_registry(path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_1()
    release = _load_release(path)
    required = {
        "release_id",
        "status",
        "engine_version",
        "profile_id",
        "runtime_registry_sha256",
        "conformance_report",
        "official_snapshots",
        "implementation_sha256",
        "runtime_feature_flag_default",
        "training_promotion_allowed",
        "sealed_blind_accessed",
    }
    if set(release) != required or RELEASE_ID.fullmatch(
        str(release.get("release_id", ""))
    ) is None:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "runtime release registry schema가 다릅니다."
        )
    registry_hash = sha256_file(REGISTRY_V11_PATH)
    if (
        release.get("status") != "approved_runtime_feature_default_off"
        or release.get("engine_version") != ENGINE_VERSION_V11
        or release.get("profile_id") != POLICY_ID
        or release.get("runtime_registry_sha256") != registry_hash
        or release.get("runtime_feature_flag_default") is not False
        or release.get("training_promotion_allowed") is not False
        or release.get("sealed_blind_accessed") is not False
    ):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "runtime release 승인 값이 다릅니다."
        )
    report, manifest = _validate_report_identity(release["conformance_report"])
    expected_release_id = release_id_for(
        release["conformance_report"]["sha256"],
        release["conformance_report"]["manifest_sha256"],
    )
    if release["release_id"] != expected_release_id:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "runtime release ID preimage가 다릅니다."
        )
    implementations = release["implementation_sha256"]
    if not isinstance(implementations, dict) or not implementations:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "runtime 구현 hash가 비었습니다."
        )
    for relative, expected_hash in implementations.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected_hash, str)
            or FULL_SHA.fullmatch(expected_hash) is None
        ):
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_INVALID", "runtime 구현 identity가 다릅니다."
            )
        implementation = _safe_repo_path(relative)
        if not implementation.is_file() or sha256_file(implementation) != expected_hash:
            raise RuntimeCalculationError(
                "RUNTIME_RELEASE_HASH_MISMATCH",
                f"runtime 구현 hash가 다릅니다: {relative}",
            )
    if implementations != report.get("inputs", {}).get("implementation_sha256"):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 conformance 구현 hash가 다릅니다."
        )
    official = release["official_snapshots"]
    report_official = report.get("inputs", {}).get("official_snapshots")
    if not isinstance(official, dict) or official != report_official:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "release와 공식 snapshot identity가 다릅니다."
        )
    return {
        **release,
        "release_registry_sha256": sha256_file(path),
        "conformance_report_data": report,
        "conformance_manifest_data": manifest,
    }
