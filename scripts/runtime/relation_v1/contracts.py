# contracts.py - 단일 날짜 relation 정책·Gate·release hash chain을 검증한다.

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import load_strict_json_object_v1_2
from scripts.runtime.calculation.contracts_v1_5 import (
    RELEASE_V15_PATH,
    validate_release_registry_v1_5,
)
from scripts.runtime.period_v1.contracts_v1_1 import (
    RELEASE_PATH as PERIOD_RELEASE_PATH,
)
from scripts.runtime.period_v1.contracts_v1_1 import (
    validate_release_registry as validate_period_release_registry,
)

from .errors import RelationRuntimeError

CONFIG_ROOT = REPO_ROOT / "configs/runtime/relations"
REGISTRY_PATH = CONFIG_ROOT / "registry-v1.0.0.json"
POLICY_PATH = CONFIG_ROOT / "relation_policy-v1.0.0.json"
OUTPUT_SCHEMA_PATH = CONFIG_ROOT / "relation_output_schema-v1.0.0.json"
GATE_PATH = CONFIG_ROOT / "conformance_gate-v1.0.0.json"
RELEASE_SCHEMA_PATH = CONFIG_ROOT / "release_registry_schema-v1.0.0.json"
RELEASE_PATH = CONFIG_ROOT / "releases/v1.0.0/release_registry.json"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_relation_conformance/v1.0.0"

REGISTRY_SHA256 = "612eb8ea719824765ab82fa260e8935272a3eaee7cde6c09cb35f495e63479ca"
REGISTRY_ID = "saju-natal-day-relation-contract-registry-v1.0.0"
POLICY_ID = "KR_NATAL_DAY_RELATIONS_V1"
TABLE_VERSION = "branch-relations-v1.0.0"
TEN_GOD_TABLE_VERSION = "saju-calculation-policy-v1.0.0"
OUTPUT_SCHEMA_ID = "saju-natal-day-relation-output-v1.0.0"
SUITE_VERSION = "saju-natal-day-relation-conformance-v1.0.0"
CHART_RELEASE_ID = "saju-runtime-release-v1.5.0-8b1d6ea2d46e"
PERIOD_RELEASE_ID = "saju-period-daily-label-release-v1.0.0-59e326f8f086"

FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
RELEASE_ID = re.compile(
    r"^saju-natal-day-relation-release-v1\.0\.0-[0-9a-f]{12}$"
)
EXPECTED_ARTIFACTS = {
    "configs/runtime/relations/relation_policy-v1.0.0.json": (
        "43741098660317ad37f41bb4ea7eebb62cee02897b5cff291e884913a89baab4"
    ),
    "configs/runtime/relations/relation_output_schema-v1.0.0.json": (
        "f23b43c5a2efda0dfe66f4d9276b68d6f5b5d2a539cb10b05a4efc2b61e67b18"
    ),
    "configs/runtime/relations/conformance_gate-v1.0.0.json": (
        "457ce645c5ce7bcfd802332cbf74691434841c6d1fe693a13918aa3a84823ebf"
    ),
    "configs/runtime/relations/release_registry_schema-v1.0.0.json": (
        "70f9872246971ae58c8661582698afe3fae1188b2fa23f47583ed05ad4d55c71"
    ),
}
CONFORMANCE_IMPLEMENTATIONS = {
    *EXPECTED_ARTIFACTS,
    "configs/runtime/relations/registry-v1.0.0.json",
    "scripts/runtime/relation_v1/__init__.py",
    "scripts/runtime/relation_v1/contracts.py",
    "scripts/runtime/relation_v1/engine.py",
    "scripts/runtime/relation_v1/errors.py",
    "scripts/runtime/relation_v1/security.py",
    "scripts/evaluation/saju_runtime/relation_conformance_v1.py",
    "scripts/evaluation/saju_runtime/relation_release_registry_v1.py",
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise RelationRuntimeError(
            "RELATION_CONTRACT_PATH_INVALID", "relation 산출물 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise RelationRuntimeError(
                "RELATION_CONTRACT_PATH_INVALID", "relation 산출물 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise RelationRuntimeError(
            "RELATION_CONTRACT_PATH_INVALID", "relation 산출물 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def load_relation_policy() -> dict[str, Any]:
    policy = load_strict_json_object_v1_2(POLICY_PATH)
    pair_relations = policy.get("pair_relations")
    punishment = policy.get("punishment")
    scope = policy.get("scope")
    if (
        policy.get("schema_version") != "1.0.0"
        or policy.get("policy_id") != POLICY_ID
        or policy.get("table_version") != TABLE_VERSION
        or policy.get("ten_god_table_version") != TEN_GOD_TABLE_VERSION
        or policy.get("status") != "candidate_for_automatic_conformance"
        or policy.get("authority") != "PROFILE_DETERMINISTIC"
        or policy.get("relation_order") != ["합", "충", "형", "파", "해"]
        or not isinstance(pair_relations, Mapping)
        or set(pair_relations) != {"합", "충", "파", "해"}
        or any(not isinstance(pair_relations[name], list) for name in pair_relations)
        or not isinstance(punishment, Mapping)
        or punishment.get("distinct_group_rule")
        != "all_distinct_pairs_symmetric"
        or punishment.get("self_rule") != "same_branch_only_symmetric"
        or not isinstance(scope, Mapping)
        or scope
        != {
            "single_date_only": True,
            "direct_relation_period_part": "day_branch",
            "relation_priority_included": False,
            "transformation_completion_included": False,
            "interpretation_included": False,
            "event_prediction_included": False,
        }
    ):
        raise RelationRuntimeError(
            "RELATION_POLICY_INVALID", "단일 날짜 relation 정책이 다릅니다."
        )
    parent = policy.get("ten_god_parent")
    if not isinstance(parent, Mapping) or set(parent) != {
        "path",
        "sha256",
        "branch_basis",
    }:
        raise RelationRuntimeError(
            "RELATION_POLICY_INVALID", "십신 부모 정책 identity가 없습니다."
        )
    parent_path = _safe_repo_path(str(parent["path"]))
    if (
        parent_path.is_symlink()
        or not parent_path.is_file()
        or parent.get("sha256") != sha256_file(parent_path)
        or parent.get("branch_basis") != "main_hidden_stem"
    ):
        raise RelationRuntimeError(
            "RELATION_POLICY_PARENT_MISMATCH", "십신 부모 정책 hash가 다릅니다."
        )
    return deepcopy(policy)


def validate_contract_registry() -> dict[str, Any]:
    chart_release = validate_release_registry_v1_5(RELEASE_V15_PATH)
    period_release = validate_period_release_registry(PERIOD_RELEASE_PATH)
    registry = load_strict_json_object_v1_2(REGISTRY_PATH)
    if (
        sha256_file(REGISTRY_PATH) != REGISTRY_SHA256
        or registry.get("schema_version") != "1.0.0"
        or registry.get("registry_id") != REGISTRY_ID
        or registry.get("status") != "candidate_relation_feature_default_off"
        or registry.get("governance")
        != {
            "feature_flag_default": False,
            "single_date_only": True,
            "range_relation_arrays_supported": False,
            "interpretation_included": False,
            "dashboard_v1_13_activated": False,
            "strict_full_runtime_approved": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generation_allowed": False,
            "training_execution_performed": False,
            "model_promotion_performed": False,
        }
    ):
        raise RelationRuntimeError(
            "RELATION_CONTRACT_REGISTRY_INVALID", "relation registry가 다릅니다."
        )
    parents = registry.get("parent_releases")
    expected_parents = {
        "chart": (RELEASE_V15_PATH, CHART_RELEASE_ID, chart_release),
        "period": (PERIOD_RELEASE_PATH, PERIOD_RELEASE_ID, period_release),
    }
    if not isinstance(parents, Mapping) or set(parents) != set(expected_parents):
        raise RelationRuntimeError(
            "RELATION_PARENT_RELEASE_INVALID", "relation 부모 release 집합이 다릅니다."
        )
    for name, (path, release_id, release) in expected_parents.items():
        identity = parents.get(name)
        if (
            not isinstance(identity, Mapping)
            or identity
            != {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "release_id": release_id,
                "sha256": sha256_file(path),
            }
            or release.get("release_id") != release_id
        ):
            raise RelationRuntimeError(
                "RELATION_PARENT_RELEASE_INVALID", f"{name} 부모 release가 다릅니다."
            )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise RelationRuntimeError(
            "RELATION_CONTRACT_REGISTRY_INVALID", "relation artifact 수가 다릅니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise RelationRuntimeError(
                "RELATION_CONTRACT_REGISTRY_INVALID", "relation artifact 형식이 다릅니다."
            )
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in seen
            or EXPECTED_ARTIFACTS.get(relative) != expected
        ):
            raise RelationRuntimeError(
                "RELATION_CONTRACT_REGISTRY_INVALID", "relation artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise RelationRuntimeError(
                "RELATION_CONTRACT_HASH_MISMATCH", f"relation hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != set(EXPECTED_ARTIFACTS):
        raise RelationRuntimeError(
            "RELATION_CONTRACT_REGISTRY_INVALID", "relation artifact 집합이 다릅니다."
        )
    policy = load_relation_policy()
    output = load_strict_json_object_v1_2(OUTPUT_SCHEMA_PATH)
    gate = load_strict_json_object_v1_2(GATE_PATH)
    release_schema = load_strict_json_object_v1_2(RELEASE_SCHEMA_PATH)
    if (
        policy.get("policy_id") != POLICY_ID
        or output.get("$id") != OUTPUT_SCHEMA_ID
        or output.get("additionalProperties") is not False
        or gate.get("suite_version") != SUITE_VERSION
        or gate.get("matrices", {}).get("stem_ten_gods", {}).get("expected_cases")
        != 100
        or gate.get("matrices", {}).get("branch_ten_gods", {}).get("expected_cases")
        != 120
        or gate.get("matrices", {}).get("branch_relations", {}).get("expected_cases")
        != 144
        or any(gate.get("governance", {}).values())
        or release_schema.get("$id")
        != "saju-natal-day-relation-release-registry-v1.0.0"
        or release_schema.get("additionalProperties") is not False
    ):
        raise RelationRuntimeError(
            "RELATION_CONTRACT_STATE_INVALID", "relation schema·Gate가 다릅니다."
        )
    return deepcopy(registry)


def validate_release_registry(path: Path = RELEASE_PATH) -> dict[str, Any]:
    validate_contract_registry()
    if path.resolve(strict=False) != RELEASE_PATH.resolve(strict=False) or path.is_symlink():
        raise RelationRuntimeError(
            "RELATION_RELEASE_PATH_INVALID", "relation release 경로가 다릅니다."
        )
    release = load_strict_json_object_v1_2(path)
    expected_fields = {
        "schema_version",
        "release_id",
        "status",
        "parent_chart_release",
        "parent_period_release",
        "contract_registry_sha256",
        "conformance_report",
        "feature_flag_default",
        "single_date_only",
        "dashboard_v1_13_activated",
        "strict_full_runtime_approved",
        "sealed_blind_accessed",
        "mix20k_v3_1_generation_allowed",
        "training_execution_performed",
        "model_promotion_performed",
    }
    core = dict(release)
    release_id = core.pop("release_id", None)
    expected_id = (
        "saju-natal-day-relation-release-v1.0.0-"
        + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    )
    report = release.get("conformance_report")
    if (
        set(release) != expected_fields
        or release.get("schema_version") != "1.0.0"
        or release_id != expected_id
        or RELEASE_ID.fullmatch(str(release_id)) is None
        or release.get("status")
        != "approved_single_date_relations_feature_default_off"
        or release.get("parent_chart_release") != CHART_RELEASE_ID
        or release.get("parent_period_release") != PERIOD_RELEASE_ID
        or release.get("contract_registry_sha256") != sha256_file(REGISTRY_PATH)
        or release.get("feature_flag_default") is not False
        or release.get("single_date_only") is not True
        or any(
            release.get(field) is not False
            for field in (
                "dashboard_v1_13_activated",
                "strict_full_runtime_approved",
                "sealed_blind_accessed",
                "mix20k_v3_1_generation_allowed",
                "training_execution_performed",
                "model_promotion_performed",
            )
        )
        or not isinstance(report, Mapping)
        or set(report)
        != {"build_id", "path", "sha256", "manifest_path", "manifest_sha256"}
        or BUILD_ID.fullmatch(str(report.get("build_id", ""))) is None
        or FULL_SHA.fullmatch(str(report.get("sha256", ""))) is None
        or FULL_SHA.fullmatch(str(report.get("manifest_sha256", ""))) is None
    ):
        raise RelationRuntimeError(
            "RELATION_RELEASE_INVALID", "relation release 값이 다릅니다."
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
        raise RelationRuntimeError(
            "RELATION_RELEASE_REPORT_INVALID", "relation conformance hash가 다릅니다."
        )
    from scripts.evaluation.saju_runtime.relation_conformance_v1 import verify_report

    verified = verify_report(aggregate_path.parent)
    if verified.get("build_id") != report["build_id"]:
        raise RelationRuntimeError(
            "RELATION_RELEASE_REPORT_INVALID", "relation conformance build가 다릅니다."
        )
    return {**release, "release_registry_sha256": sha256_file(path)}
