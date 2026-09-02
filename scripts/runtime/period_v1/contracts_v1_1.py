# contracts_v1_1.py - 일별 label engine·conformance·release hash chain을 검증한다.

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import load_strict_json_object_v1_2

from .contracts import (
    CONFIG_ROOT,
    PARENT_RELEASE_ID,
    REGISTRY_PATH,
    validate_contract_registry,
)
from .errors import PeriodRuntimeError

REGISTRY_V11_PATH = CONFIG_ROOT / "registry-v1.1.0.json"
OUTPUT_SCHEMA_PATH = CONFIG_ROOT / "daily_label_output_schema-v1.0.0.json"
GATE_PATH = CONFIG_ROOT / "conformance_gate-v1.0.0.json"
RELEASE_SCHEMA_PATH = CONFIG_ROOT / "release_registry_schema-v1.0.0.json"
RELEASE_PATH = CONFIG_ROOT / "releases/v1.0.0/release_registry.json"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_period_conformance/v1.0.0"

REGISTRY_V11_SHA256 = "212ee38ebeacc4833661be2659e096290d2d3fd3c800062e6f1152ae83480390"
REGISTRY_V11_ID = "saju-period-contract-registry-v1.1.0"
SUITE_VERSION = "saju-period-daily-label-conformance-v11.0.0"
OUTPUT_SCHEMA_VERSION = "saju-period-daily-label-output-v1.0.0"
EXPECTED_WINDOWS = 263_717
OFFICIAL_DATES = 8_522
MAXIMUM_DAYS = 31

FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
RELEASE_ID = re.compile(
    r"^saju-period-daily-label-release-v1\.0\.0-[0-9a-f]{12}$"
)
EXPECTED_ARTIFACTS = {
    "configs/runtime/period/daily_label_output_schema-v1.0.0.json": (
        "30dd56d23a602478f83d840712fb210254a1266fb904ce514de6cb2c5e2df56c"
    ),
    "configs/runtime/period/conformance_gate-v1.0.0.json": (
        "fdd667269a869b9f4d2a1fdb82e917b078c94744ff5bbb43ea3ff8f9c1ae3142"
    ),
}
CONFORMANCE_IMPLEMENTATIONS = {
    "configs/runtime/period/registry-v1.0.0.json",
    "configs/runtime/period/registry-v1.1.0.json",
    "configs/runtime/period/request_schema-v2.0.0.json",
    "configs/runtime/period/resolved_scope_schema-v1.0.0.json",
    "configs/runtime/period/chart_authority_schema-v1.0.0.json",
    "configs/runtime/period/release_registry_schema-v1.0.0.json",
    "configs/runtime/period/daily_label_output_schema-v1.0.0.json",
    "configs/runtime/period/conformance_gate-v1.0.0.json",
    "scripts/runtime/period_v1/contracts.py",
    "scripts/runtime/period_v1/contracts_v1_1.py",
    "scripts/runtime/period_v1/errors.py",
    "scripts/runtime/period_v1/resolver.py",
    "scripts/runtime/period_v1/rehydration.py",
    "scripts/runtime/period_v1/security.py",
    "scripts/runtime/period_v1/engine.py",
    "scripts/evaluation/saju_runtime/conformance_v11.py",
    "scripts/evaluation/saju_runtime/release_registry_period_v1.py",
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_PATH_INVALID", "기간 산출물 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_PATH_INVALID", "기간 산출물 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_PATH_INVALID", "기간 산출물 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def validate_contract_registry_v1_1() -> dict[str, Any]:
    validate_contract_registry()
    registry = load_strict_json_object_v1_2(REGISTRY_V11_PATH)
    if (
        sha256_file(REGISTRY_V11_PATH) != REGISTRY_V11_SHA256
        or registry.get("schema_version") != "1.1.0"
        or registry.get("registry_id") != REGISTRY_V11_ID
        or registry.get("status") != "candidate_daily_labels_feature_default_off"
        or registry.get("parent")
        != {
            "path": str(REGISTRY_PATH.relative_to(REPO_ROOT)),
            "sha256": sha256_file(REGISTRY_PATH),
        }
        or registry.get("governance")
        != {
            "feature_flag_default": False,
            "daily_label_release_required": True,
            "maximum_days": MAXIMUM_DAYS,
            "intraday_segments_supported": False,
            "strict_full_runtime_approved": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generation_allowed": False,
            "training_promotion_allowed": False,
        }
    ):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 v1.1 registry가 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 v1.1 artifact 수가 다릅니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 v1.1 artifact 형식이 다릅니다."
            )
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in seen
            or EXPECTED_ARTIFACTS.get(relative) != expected
        ):
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 v1.1 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_HASH_MISMATCH", f"기간 v1.1 hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != set(EXPECTED_ARTIFACTS):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 v1.1 artifact 집합이 다릅니다."
        )
    output = load_strict_json_object_v1_2(OUTPUT_SCHEMA_PATH)
    gate = load_strict_json_object_v1_2(GATE_PATH)
    matrix = gate.get("window_matrix")
    if (
        output.get("$id") != OUTPUT_SCHEMA_VERSION
        or output.get("additionalProperties") is not False
        or gate.get("suite_version") != SUITE_VERSION
        or gate.get("parent_conformance", {}).get("build_id")
        != "build-46185262164f"
        or gate.get("approved_date_range", {}).get("official_dates")
        != OFFICIAL_DATES
        or not isinstance(matrix, Mapping)
        or matrix.get("maximum_days") != MAXIMUM_DAYS
        or matrix.get("expected_windows") != EXPECTED_WINDOWS
        or any(
            matrix.get(field) != 0
            for field in (
                "order_mismatches_maximum",
                "duplicate_or_missing_days_maximum",
                "label_mismatches_maximum",
                "authority_mismatches_maximum",
            )
        )
        or any(gate.get("governance", {}).values())
    ):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_STATE_INVALID", "기간 v1.1 schema·Gate가 다릅니다."
        )
    return registry


def validate_release_registry(path: Path = RELEASE_PATH) -> dict[str, Any]:
    validate_contract_registry_v1_1()
    if path.resolve(strict=False) != RELEASE_PATH.resolve(strict=False) or path.is_symlink():
        raise PeriodRuntimeError(
            "PERIOD_RELEASE_PATH_INVALID", "기간 release 경로가 다릅니다."
        )
    release = load_strict_json_object_v1_2(path)
    expected_fields = {
        "schema_version",
        "release_id",
        "status",
        "parent_runtime_release",
        "contract_registry_sha256",
        "conformance_report",
        "feature_flag_default",
        "strict_full_runtime_approved",
        "training_promotion_allowed",
        "sealed_blind_accessed",
    }
    core = dict(release)
    release_id = core.pop("release_id", None)
    expected_id = (
        "saju-period-daily-label-release-v1.0.0-"
        + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    )
    report = release.get("conformance_report")
    if (
        set(release) != expected_fields
        or release.get("schema_version") != "1.0.0"
        or release_id != expected_id
        or RELEASE_ID.fullmatch(str(release_id)) is None
        or release.get("status") != "approved_daily_labels_feature_default_off"
        or release.get("parent_runtime_release") != PARENT_RELEASE_ID
        or release.get("contract_registry_sha256") != sha256_file(REGISTRY_V11_PATH)
        or release.get("feature_flag_default") is not False
        or release.get("strict_full_runtime_approved") is not False
        or release.get("training_promotion_allowed") is not False
        or release.get("sealed_blind_accessed") is not False
        or not isinstance(report, Mapping)
        or set(report)
        != {"build_id", "path", "sha256", "manifest_path", "manifest_sha256"}
        or BUILD_ID.fullmatch(str(report.get("build_id", ""))) is None
        or FULL_SHA.fullmatch(str(report.get("sha256", ""))) is None
        or FULL_SHA.fullmatch(str(report.get("manifest_sha256", ""))) is None
    ):
        raise PeriodRuntimeError(
            "PERIOD_RELEASE_INVALID", "기간 daily-label release 값이 다릅니다."
        )
    aggregate_path = _safe_repo_path(str(report["path"]))
    manifest_path = _safe_repo_path(str(report["manifest_path"]))
    if (
        not aggregate_path.is_file()
        or aggregate_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
        or aggregate_path.parent != manifest_path.parent
        or aggregate_path.parent.name != report["build_id"]
        or sha256_file(aggregate_path) != report["sha256"]
        or sha256_file(manifest_path) != report["manifest_sha256"]
    ):
        raise PeriodRuntimeError(
            "PERIOD_RELEASE_REPORT_INVALID", "기간 conformance 산출물 hash가 다릅니다."
        )
    from scripts.evaluation.saju_runtime.conformance_v11 import verify_report

    verified = verify_report(aggregate_path.parent)
    if verified.get("build_id") != report["build_id"]:
        raise PeriodRuntimeError(
            "PERIOD_RELEASE_REPORT_INVALID", "기간 conformance build가 다릅니다."
        )
    return {**release, "release_registry_sha256": sha256_file(path)}
