# contracts_v1_3.py - Skyfield candidate runtime v1.3 계약과 source identity를 검증한다.

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import (
    CONFIG_ROOT,
    EXPECTED_LUNAR_PACKAGE_VERSION,
    EXPECTED_TZDATA_VERSION,
    POLICY_ID,
    REPO_ROOT,
    runtime_source_versions,
    sha256_file,
)
from .contracts_v1_2 import (
    ID_CONTRACT_VERSION_V2,
    load_strict_json_object_v1_2,
    validate_contract_registry_v1_2,
)
from .errors import RuntimeCalculationError
from .skyfield_solar_terms import (
    DE440S_BYTES,
    DE440S_SHA256,
    JPLEPHEM_VERSION,
    NUMPY_VERSION,
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SKYFIELD_BUILTIN_DATA_SHA256,
    SKYFIELD_VERSION,
    SkyfieldSolarTermProvider,
)

REGISTRY_V13_PATH = CONFIG_ROOT / "registry-v1.3.0.json"
CONTRACT_V13_PATH = CONFIG_ROOT / "runtime_contract-v1.3.0.json"
SOURCE_REGISTRY_V16_PATH = CONFIG_ROOT / "source_registry-v1.6.0.json"
GATE_V16_PATH = CONFIG_ROOT / "conformance_gate-v1.6.0.json"
PROFILE_V13_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1-v1.3.0.json"
OUTPUT_SCHEMA_V13_PATH = CONFIG_ROOT / "calculation_output_schema-v1.3.0.json"
REQUIREMENTS_V13_PATH = REPO_ROOT / "requirements-runtime-calculator-v1.3.txt"
ENGINE_VERSION_V13 = "saju-runtime-python-v1.3.0"
OUTPUT_SCHEMA_VERSION_V13 = "1.3.0"
SUITE_VERSION_V8 = "saju-runtime-conformance-v8.0.0"
SOURCE_REGISTRY_VERSION_V16 = "saju-runtime-sources-v1.6.0"
REGISTRY_V13_SHA256 = "556e99a076511bb99b65a9dae155fe93b9a33de4d0f2f57cee009461d2befd07"
PARENT_REGISTRY_V12_SHA256 = (
    "6d036ee9b9cb6591de2d0248ccf7aad150869240ccbabfe9b81c211f537b1ede"
)
PARENT_SOURCE_V15_SHA256 = (
    "d549780199898ca04a1f7c1b71204825a79a657c6cf8c691827b097ac5580b46"
)
PARENT_GATE_V15_SHA256 = (
    "132408a4085e4eeb52dbe613c501f3c15d101cd794b85b360638f2f03ac8c8b7"
)
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/calculation/runtime_contract-v1.3.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.3.0.json",
    "configs/runtime/calculation/source_registry-v1.6.0.json",
    "configs/runtime/calculation/conformance_gate-v1.6.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.3.0.json",
    "configs/runtime/calculation/id_canonicalization-v2.0.0.json",
    "requirements-runtime-calculator-v1.3.txt",
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.3 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "v1.3 계약 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.3 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _validate_parent(identity: Any, *, expected_path: str, expected_sha256: str) -> None:
    if identity != {"path": expected_path, "sha256": expected_sha256}:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", f"v1.3 parent identity가 다릅니다: {expected_path}"
        )
    path = _safe_repo_path(expected_path)
    if sha256_file(path) != expected_sha256:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_HASH_MISMATCH", f"v1.3 parent hash가 다릅니다: {expected_path}"
        )


def validate_contract_registry_v1_3() -> dict[str, Any]:
    """v1.2를 보존한 새 candidate runtime artifact hash chain을 검증한다."""

    validate_contract_registry_v1_2()
    registry = load_strict_json_object_v1_2(REGISTRY_V13_PATH)
    if (
        sha256_file(REGISTRY_V13_PATH) != REGISTRY_V13_SHA256
        or registry.get("schema_version") != "1.3.0"
        or registry.get("registry_id")
        != "saju-runtime-calculation-registry-v1.3.0"
        or registry.get("status")
        != "skyfield_candidate_runtime_bound_release_out_of_scope"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.3 runtime registry 값·hash가 다릅니다."
        )
    _validate_parent(
        registry.get("parent"),
        expected_path="configs/runtime/calculation/registry-v1.2.0.json",
        expected_sha256=PARENT_REGISTRY_V12_SHA256,
    )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.3 artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "v1.3 artifact 계약이 다릅니다."
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
                "CONTRACT_REGISTRY_INVALID", "v1.3 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH", f"v1.3 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != EXPECTED_ARTIFACTS:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "v1.3 artifact 집합이 다릅니다."
        )

    contract = load_strict_json_object_v1_2(CONTRACT_V13_PATH)
    source = load_strict_json_object_v1_2(SOURCE_REGISTRY_V16_PATH)
    gate = load_strict_json_object_v1_2(GATE_V16_PATH)
    profile = load_strict_json_object_v1_2(PROFILE_V13_PATH)
    schema = load_strict_json_object_v1_2(OUTPUT_SCHEMA_V13_PATH)
    if (
        contract.get("runtime_contract_version")
        != "saju-runtime-contract-v1.3.0"
        or contract.get("engine_version") != ENGINE_VERSION_V13
        or contract.get("output_schema_version") != OUTPUT_SCHEMA_VERSION_V13
        or contract.get("id_contract_version") != ID_CONTRACT_VERSION_V2
        or contract.get("conformance_suite_version") != SUITE_VERSION_V8
        or contract.get("runtime_approved_in_static_contract") is not False
        or contract.get("release_contract_created") is not False
        or contract.get("candidate_runtime", {}).get("provider")
        != SkyfieldSolarTermProvider.provider_id
        or contract.get("candidate_runtime", {}).get("provider_bound") is not True
        or contract.get("candidate_runtime", {}).get("production_provider_changed")
        is not False
        or source.get("registry_version") != SOURCE_REGISTRY_VERSION_V16
        or source.get("runtime_provider", {}).get("candidate_runtime_bound")
        is not True
        or source.get("runtime_provider", {}).get("production_runtime_bound")
        is not False
        or gate.get("suite_version") != SUITE_VERSION_V8
        or gate.get("expected_report_state", {}).get(
            "candidate_runtime_conformance_passed"
        )
        is not True
        or gate.get("expected_report_state", {}).get("runtime_gate_passed")
        is not False
        or profile.get("policy_id") != POLICY_ID
        or profile.get("profile_revision") != "KR_CIVIL_MIDNIGHT_V1-v1.3.0"
        or profile.get("solar_term_provider") != SkyfieldSolarTermProvider.provider_id
        or schema.get("$id") != "saju-calculation-output-v1.3.0"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID", "v1.3 runtime·source·gate·profile 상태가 다릅니다."
        )
    _validate_parent(
        source.get("parent"),
        expected_path="configs/runtime/calculation/source_registry-v1.5.0.json",
        expected_sha256=PARENT_SOURCE_V15_SHA256,
    )
    _validate_parent(
        gate.get("parent"),
        expected_path="configs/runtime/calculation/conformance_gate-v1.5.0.json",
        expected_sha256=PARENT_GATE_V15_SHA256,
    )
    parents = contract.get("parents")
    if not isinstance(parents, dict):
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", "v1.3 runtime parent가 비었습니다."
        )
    _validate_parent(
        parents.get("runtime_registry_v1_2"),
        expected_path="configs/runtime/calculation/registry-v1.2.0.json",
        expected_sha256=PARENT_REGISTRY_V12_SHA256,
    )
    _validate_parent(
        parents.get("source_registry_v1_5"),
        expected_path="configs/runtime/calculation/source_registry-v1.5.0.json",
        expected_sha256=PARENT_SOURCE_V15_SHA256,
    )
    _validate_parent(
        parents.get("id_canonicalization"),
        expected_path="configs/runtime/calculation/id_canonicalization-v2.0.0.json",
        expected_sha256=(
            "54290f788af0a57483e8bd6483697a480fc527456227b81acab3c7ff4ab218f5"
        ),
    )
    return registry


def _validate_provider_identity(identity: dict[str, Any]) -> None:
    packages = identity.get("packages")
    ephemeris = identity.get("ephemeris")
    timescale = identity.get("timescale")
    if (
        identity.get("provider_id") != SkyfieldSolarTermProvider.provider_id
        or packages
        != {
            "skyfield": SKYFIELD_VERSION,
            "jplephem": JPLEPHEM_VERSION,
            "numpy": NUMPY_VERSION,
        }
        or not isinstance(ephemeris, dict)
        or ephemeris.get("bytes") != DE440S_BYTES
        or ephemeris.get("sha256") != DE440S_SHA256
        or not isinstance(timescale, dict)
        or timescale.get("mode") != "builtin_no_network"
        or timescale.get("files_sha256") != SKYFIELD_BUILTIN_DATA_SHA256
        or identity.get("root_time_scale") != "TT"
        or identity.get("boundary_comparison_time_scale") != "TT"
        or identity.get("automatic_download_or_fallback") is not False
        or identity.get("astronomy_engine_fallback") is not False
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_PROVIDER_IDENTITY_MISMATCH",
            "Skyfield runtime provider identity가 v1.3 계약과 다릅니다.",
        )


def runtime_source_versions_v1_3(
    *,
    require_runtime_dependencies: bool,
    provider_identity: dict[str, Any] | None = None,
) -> dict[str, str]:
    validate_contract_registry_v1_3()
    versions = runtime_source_versions(require_dependencies=False)
    if require_runtime_dependencies and (
        versions.get("python_tzdata") != EXPECTED_TZDATA_VERSION
        or versions.get("korean_lunar_calendar")
        != EXPECTED_LUNAR_PACKAGE_VERSION
    ):
        raise RuntimeCalculationError(
            "RUNTIME_DEPENDENCY_VERSION_MISMATCH",
            "v1.3 runtime의 tzdata·음양력 의존성 버전이 다릅니다.",
        )
    versions.pop("astronomy_engine", None)
    if provider_identity is not None:
        _validate_provider_identity(provider_identity)
    elif require_runtime_dependencies:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EPHEMERIS_REQUIRED",
            "활성 v1.3 candidate runtime에는 검증된 DE440s provider가 필요합니다.",
        )
    versions.update(
        {
            "solar_term_provider": SkyfieldSolarTermProvider.provider_id,
            "skyfield": SKYFIELD_VERSION,
            "jplephem": JPLEPHEM_VERSION,
            "numpy": NUMPY_VERSION,
            "jpl_ephemeris": f"de440s:{DE440S_SHA256}",
            "skyfield_timescale": "builtin-no-network-v1.55",
            "official_solar_term_snapshot_cutoff": OFFICIAL_SNAPSHOT_COLLECTED_AT,
            "source_registry": SOURCE_REGISTRY_VERSION_V16,
            "runtime_contract": "saju-runtime-contract-v1.3.0",
            "id_contract": ID_CONTRACT_VERSION_V2,
        }
    )
    return versions
