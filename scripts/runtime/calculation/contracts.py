# contracts.py - versioned runtime 계약·파일 해시·고정 환경을 fail-closed로 검증한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
from typing import Any
from zoneinfo import TZPATH

from .errors import RuntimeCalculationError

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = REPO_ROOT / "configs/runtime/calculation"
REGISTRY_PATH = CONFIG_ROOT / "registry-v1.0.0.json"
CONTRACT_PATH = CONFIG_ROOT / "runtime_contract-v1.0.0.json"
PROFILE_PATH = CONFIG_ROOT / "profiles/KR_CIVIL_MIDNIGHT_V1.json"
SOURCE_REGISTRY_PATH = CONFIG_ROOT / "source_registry-v1.0.0.json"
TABLE_POLICY_PATH = REPO_ROOT / "configs/saju_calculation_policy-v1.0.0.json"
KASI_COLLECTOR_PATH = REPO_ROOT / "scripts/evaluation/saju_runtime/kasi_collector.py"
ENGINE_VERSION = "saju-runtime-python-v1.0.0"
OUTPUT_SCHEMA_VERSION = "1.0.0"
POLICY_ID = "KR_CIVIL_MIDNIGHT_V1"
EXPECTED_TZDB_RELEASE = "2026c"
EXPECTED_TZDATA_VERSION = "2026.3"
EXPECTED_LUNAR_PACKAGE_VERSION = "0.4.0"
EXPECTED_ASTRONOMY_ENGINE_VERSION = "2.1.19"
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_REGISTRY_ARTIFACTS = {
    "configs/runtime/calculation/runtime_contract-v1.0.0.json",
    "configs/runtime/calculation/birth_input_schema-v1.0.0.json",
    "configs/runtime/calculation/calculation_output_schema-v1.0.0.json",
    "configs/runtime/calculation/source_registry-v1.0.0.json",
    "configs/runtime/calculation/mismatch_taxonomy-v1.0.0.json",
    "configs/runtime/calculation/id_canonicalization-v1.0.0.json",
    "configs/runtime/calculation/conformance_gate-v1.0.0.json",
    "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1.json",
    "requirements-runtime-calculator.txt",
}
EXPECTED_PARENT_NAMES = {
    "tool_schema",
    "session_state_schema",
    "workflow_calculation_policy",
    "approved_table_policy",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RuntimeCalculationError(
            "CONTRACT_READ_FAILED", f"계약 파일을 읽을 수 없습니다: {path}"
        ) from exc
    return digest.hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeCalculationError(
            "CONTRACT_FILE_INVALID", f"계약 파일이 없거나 symlink입니다: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeCalculationError(
            "CONTRACT_JSON_INVALID", f"계약 JSON을 읽을 수 없습니다: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeCalculationError(
            "CONTRACT_JSON_INVALID", f"계약 최상위는 object여야 합니다: {path}"
        )
    return value


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCalculationError(
                "UNSAFE_CONTRACT_PATH", "계약 경로에 symlink가 포함됐습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def validate_contract_registry() -> dict[str, Any]:
    registry = load_json_object(REGISTRY_PATH)
    if (
        registry.get("schema_version") != "1.0.0"
        or registry.get("registry_id") != "saju-runtime-calculation-registry-v1.0.0"
        or registry.get("status") != "candidate_runtime_gate_blocked"
    ):
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "runtime registry 값이 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID", "runtime artifact 목록이 비었습니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256"}:
            raise RuntimeCalculationError(
                "CONTRACT_REGISTRY_INVALID", "artifact 계약이 다릅니다."
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
                "CONTRACT_REGISTRY_INVALID", "artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise RuntimeCalculationError(
                "CONTRACT_HASH_MISMATCH",
                f"runtime artifact hash가 다릅니다: {relative}",
            )
        seen.add(relative)
    if seen != EXPECTED_REGISTRY_ARTIFACTS:
        raise RuntimeCalculationError(
            "CONTRACT_REGISTRY_INVALID",
            "runtime registry artifact 집합이 고정 계약과 다릅니다.",
        )
    contract = load_json_object(CONTRACT_PATH)
    profile = load_json_object(PROFILE_PATH)
    if (
        contract.get("engine_version") != ENGINE_VERSION
        or contract.get("profile_id") != POLICY_ID
        or contract.get("runtime_approved") is not False
        or profile.get("policy_id") != POLICY_ID
        or profile.get("runtime_approved") is not False
    ):
        raise RuntimeCalculationError(
            "CONTRACT_STATE_INVALID", "runtime 승인 상태가 계약과 다릅니다."
        )
    parents = contract.get("parents")
    if not isinstance(parents, dict) or set(parents) != EXPECTED_PARENT_NAMES:
        raise RuntimeCalculationError(
            "CONTRACT_PARENT_INVALID", "runtime parent 계약 집합이 다릅니다."
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
                "CONTRACT_PARENT_INVALID", f"runtime parent identity가 다릅니다: {name}"
            )
        parent_path = _safe_repo_path(identity["path"])
        if (
            parent_path.is_symlink()
            or not parent_path.is_file()
            or sha256_file(parent_path) != identity["sha256"]
        ):
            raise RuntimeCalculationError(
                "CONTRACT_PARENT_HASH_MISMATCH",
                f"runtime parent hash가 다릅니다: {name}",
            )
    sources = load_json_object(SOURCE_REGISTRY_PATH).get("sources")
    if not isinstance(sources, dict):
        raise RuntimeCalculationError(
            "SOURCE_REGISTRY_INVALID", "runtime source 목록이 object가 아닙니다."
        )
    kasi = sources.get("kasi_lunisolar_openapi", {})
    iana = sources.get("iana_tzdb", {})
    python_tzdata = sources.get("python_tzdata", {})
    lunar = sources.get("korean_lunar_calendar", {})
    astronomy = sources.get("astronomy_engine", {})
    if (
        not isinstance(kasi, dict)
        or kasi.get("collector_version") != "kasi-lunisolar-collector-v1.0.0"
        or kasi.get("collector_sha256") != sha256_file(KASI_COLLECTOR_PATH)
        or not isinstance(iana, dict)
        or iana.get("release") != EXPECTED_TZDB_RELEASE
        or not isinstance(python_tzdata, dict)
        or python_tzdata.get("version") != EXPECTED_TZDATA_VERSION
        or not isinstance(lunar, dict)
        or lunar.get("version") != EXPECTED_LUNAR_PACKAGE_VERSION
        or not isinstance(astronomy, dict)
        or astronomy.get("version") != EXPECTED_ASTRONOMY_ENGINE_VERSION
    ):
        raise RuntimeCalculationError(
            "SOURCE_REGISTRY_INVALID",
            "runtime source version·collector hash가 다릅니다.",
        )
    return registry


def detect_tzdb_release() -> str:
    candidates = [Path(root) / "tzdata.zi" for root in TZPATH]
    try:
        import tzdata  # type: ignore

        candidates.append(Path(tzdata.__file__).resolve().parent / "zoneinfo/tzdata.zi")
    except (ImportError, AttributeError, TypeError):
        pass
    for path in candidates:
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, UnicodeError, IndexError):
            continue
        match = re.fullmatch(r"# version (\S+)", first_line.strip())
        if match:
            return match.group(1)
    raise RuntimeCalculationError(
        "TZDB_VERSION_UNKNOWN", "사용 중인 tzdb release를 확인하지 못했습니다."
    )


def runtime_source_versions(*, require_dependencies: bool) -> dict[str, str]:
    tzdb_release = detect_tzdb_release()
    if tzdb_release != EXPECTED_TZDB_RELEASE:
        raise RuntimeCalculationError(
            "TZDB_VERSION_MISMATCH",
            f"tzdb {EXPECTED_TZDB_RELEASE}가 필요하지만 {tzdb_release}입니다.",
        )
    versions = {
        "tzdb": tzdb_release,
        "solar_term_provider": "astronomy-engine-2.1.19",
        "table_policy": "saju-calculation-policy-v1.0.0",
        "source_registry": "saju-runtime-sources-v1.0.0",
    }
    try:
        tzdata_version = importlib.metadata.version("tzdata")
    except importlib.metadata.PackageNotFoundError:
        tzdata_version = "system-tzdb"
    if require_dependencies and tzdata_version != EXPECTED_TZDATA_VERSION:
        raise RuntimeCalculationError(
            "TZDATA_VERSION_MISMATCH",
            f"Python tzdata {EXPECTED_TZDATA_VERSION}가 필요하지만 {tzdata_version}입니다.",
        )
    versions["python_tzdata"] = tzdata_version
    try:
        lunar_version = importlib.metadata.version("korean-lunar-calendar")
    except importlib.metadata.PackageNotFoundError:
        if require_dependencies:
            raise RuntimeCalculationError(
                "CALENDAR_DEPENDENCY_MISSING",
                "korean-lunar-calendar 고정 패키지가 설치되지 않았습니다.",
            )
        lunar_version = "unavailable"
    if require_dependencies and lunar_version != EXPECTED_LUNAR_PACKAGE_VERSION:
        raise RuntimeCalculationError(
            "CALENDAR_VERSION_MISMATCH",
            f"korean-lunar-calendar {EXPECTED_LUNAR_PACKAGE_VERSION}가 필요하지만 {lunar_version}입니다.",
        )
    versions["korean_lunar_calendar"] = lunar_version
    try:
        astronomy_version = importlib.metadata.version("astronomy-engine")
    except importlib.metadata.PackageNotFoundError:
        if require_dependencies:
            raise RuntimeCalculationError(
                "ASTRONOMY_DEPENDENCY_MISSING",
                "astronomy-engine 고정 패키지가 설치되지 않았습니다.",
            )
        astronomy_version = "unavailable"
    if require_dependencies and astronomy_version != EXPECTED_ASTRONOMY_ENGINE_VERSION:
        raise RuntimeCalculationError(
            "ASTRONOMY_VERSION_MISMATCH",
            f"astronomy-engine {EXPECTED_ASTRONOMY_ENGINE_VERSION}가 필요하지만 {astronomy_version}입니다.",
        )
    versions["astronomy_engine"] = astronomy_version
    return versions


def load_table_policy() -> dict[str, Any]:
    policy = load_json_object(TABLE_POLICY_PATH)
    if (
        policy.get("policy_id") != "saju-calculation-policy-v1.0.0"
        or policy.get("status") != "approved_project_policy_not_expert_certified"
        or policy.get("scope", {}).get("immutable_build_mutation_allowed") is not False
    ):
        raise RuntimeCalculationError(
            "TABLE_POLICY_INVALID", "승인 표 정책이 고정 계약과 다릅니다."
        )
    return policy
