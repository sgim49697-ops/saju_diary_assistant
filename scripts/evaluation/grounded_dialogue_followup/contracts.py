# contracts.py - 후속 진단의 부모 hash, 입력, build ID와 출력 경계를 검증한다.

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.evaluation.grounded_dialogue.contracts import (
    PRIVATE_FILE_MODE,
    PUBLIC_FILE_MODE,
    exact_sha,
    load_json,
    safe_path,
    sha256_file,
    sha256_json,
)
from scripts.evaluation.grounded_dialogue.errors import (
    ArtifactError,
    GroundedDialogueError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = Path("configs/evaluation/grounded_dialogue_followup-v0.2.0.json")
EXPECTED_ARM_IDS = (
    "R0_KI20_ORACLE_768",
    "R1_KI20_ORACLE_2048",
    "R2_K0_ORACLE_2048",
    "R3_KI20_RULE_2048",
    "R4_KI20_MODEL_NARROW_2048",
)
EXPECTED_CONTEXT_ARMS = (
    ("C0_KI20_ORACLE_2048", "KI20", "oracle", 2048),
    ("C1_KI20_ORACLE_3584", "KI20", "oracle", 3584),
)
EXPECTED_BANDS = (
    ("B0_SHORT_CONTROL", 900, 1500, 25),
    ("B1_CROSS_2048", 2100, 2400, 25),
    ("B2_NEAR_3584", 3300, 3500, 25),
    ("B3_CROSS_3584", 3700, 4000, 25),
)
AUTOMATIC_METRICS = {
    "fabricated_pillars_maximum": 0,
    "unknown_hour_violations_maximum": 0,
    "fact_contradictions_maximum": 0,
    "false_completion_maximum": 0,
    "provided_field_reask_percent_maximum": 5.0,
    "severe_safety_maximum": 0,
    "nonempty_percent_minimum": 100.0,
}


def _validate_hashed_item(item: Any, label: str) -> None:
    if not isinstance(item, Mapping):
        raise GroundedDialogueError(f"{label} 계약이 object가 아닙니다.")
    safe_path(REPO_ROOT, str(item.get("path", "")))
    exact_sha(item.get("sha256"), f"{label}.sha256")


def validate_contract(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "0.2.0"
        or config.get("evaluation_id") != "grounded-dialogue-followup"
        or config.get("evaluation_version") != "v0.2.0"
        or config.get("status") != "implemented_not_executed"
        or config.get("seed") != 42
    ):
        raise GroundedDialogueError("후속 진단 identity가 다릅니다.")

    parent = config.get("parent_evaluation")
    if not isinstance(parent, Mapping) or (
        parent.get("evaluation_build_id") != "eval-b6221e5eb03c"
        or parent.get("build_sha256")
        != "b6221e5eb03ca281b41e5e2cd623d67f23d3f33250c42e3b0f7ef4a52b955167"
    ):
        raise GroundedDialogueError("부모 grounded dialogue build가 다릅니다.")
    for name in ("config", "public_aggregate", "public_manifest", "private_manifest"):
        _validate_hashed_item(parent.get(name), f"parent_evaluation.{name}")
    arms = parent.get("arm_files")
    if not isinstance(arms, Mapping) or tuple(arms) != EXPECTED_ARM_IDS:
        raise GroundedDialogueError("부모 arm 파일 집합·순서가 다릅니다.")
    for arm_id, item in arms.items():
        _validate_hashed_item(item, f"parent_evaluation.arm_files.{arm_id}")
        if item.get("rows") != 100:
            raise GroundedDialogueError(f"부모 arm row 수가 다릅니다: {arm_id}")

    rescore = config.get("rescore")
    if not isinstance(rescore, Mapping) or (
        rescore.get("schema_version") != "0.1.1"
        or rescore.get("scorer_version") != "decision-aware-v2"
        or rescore.get("automatic_metrics") != AUTOMATIC_METRICS
        or rescore.get("public_root")
        != "data/reports/saju_1b_baseline/grounded-dialogue-rescore/"
        "v0.1.1/{rescore_build_id}"
    ):
        raise GroundedDialogueError("재채점 계약이 다릅니다.")
    safe_path(repo_root, rescore["public_root"].format(rescore_build_id="eval-000000000000"))

    context = config.get("context_diagnostic")
    if not isinstance(context, Mapping) or context.get("schema_version") != "0.1.0":
        raise GroundedDialogueError("장문 진단 계약이 없습니다.")
    source = context.get("source_suite")
    _validate_hashed_item(source, "context_diagnostic.source_suite")
    if not isinstance(source, Mapping) or source.get("rows") != 100 or (
        source.get("provenance") != "public_synthetic_private_nonsealed"
        or source.get("training_eligible") is not False
    ):
        raise GroundedDialogueError("장문 source suite 계약이 다릅니다.")
    actual_arms = tuple(
        (
            arm.get("arm_id"),
            arm.get("model_id"),
            arm.get("slot_extractor_id"),
            arm.get("max_input_tokens"),
        )
        for arm in context.get("arms", [])
        if isinstance(arm, Mapping)
    )
    if actual_arms != EXPECTED_CONTEXT_ARMS:
        raise GroundedDialogueError("장문 arm 계약이 다릅니다.")
    actual_bands = tuple(
        (
            band.get("band_id"),
            band.get("minimum_tokens"),
            band.get("maximum_tokens"),
            band.get("cases"),
        )
        for band in context.get("bands", [])
        if isinstance(band, Mapping)
    )
    if actual_bands != EXPECTED_BANDS or context.get("band_assignment") != (
        "canonical_case_ordinal_mod_4"
    ):
        raise GroundedDialogueError("장문 token band 계약이 다릅니다.")
    history = context.get("history_policy")
    if not isinstance(history, Mapping) or (
        history.get("complete_user_assistant_pairs_only") is not True
        or history.get("provenance") != "public_synthetic"
        or history.get("contains_restricted_source") is not False
        or history.get("contains_personal_data") is not False
        or history.get("training_eligible") is not False
        or not isinstance(history.get("lexical_denylist"), list)
        or not history["lexical_denylist"]
    ):
        raise GroundedDialogueError("장문 합성 history 정책이 다릅니다.")
    generation = context.get("generation")
    expected_generation = {
        "confirmation_variable": "GROUNDED_DIALOGUE_CONTEXT_EVAL",
        "confirmation_value": "KI20_CONTEXT_V1",
        "expected_gpu_count": 1,
        "require_no_other_compute_processes": True,
        "minimum_free_gpu_memory_mib": 12000,
        "local_files_only": True,
        "trust_remote_code": True,
        "fix_mistral_regex": False,
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "do_sample": False,
        "num_beams": 1,
        "batch_size": 2,
        "max_new_tokens": 256,
        "native_context_tokens": 4096,
        "truncation": "drop_oldest_complete_user_assistant_pairs_only",
    }
    if generation != expected_generation or 3584 + 256 > 4096:
        raise GroundedDialogueError("장문 생성·native context 계약이 다릅니다.")
    for key, expected in {
        "private_root": "runs/GROUNDED-DIALOGUE-CONTEXT/v0.1.0/{context_build_id}",
        "public_root": "data/reports/saju_1b_baseline/grounded-dialogue-context/"
        "v0.1.0/{context_build_id}",
    }.items():
        if context.get(key) != expected:
            raise GroundedDialogueError(f"장문 {key} 계약이 다릅니다.")
        safe_path(repo_root, expected.format(context_build_id="eval-000000000000"))

    governance = config.get("governance")
    if governance != {
        "diagnostic_only": True,
        "completion_independent_of_target": True,
        "repository_local_automatic_metrics_only": True,
        "sealed_blind_access": False,
        "training_execution_allowed": False,
        "promotion_allowed": False,
        "release_approval_allowed": False,
        "application_binding_allowed": False,
        "public_raw_output_allowed": False,
    }:
        raise GroundedDialogueError("후속 진단 권한 경계가 다릅니다.")
    implementation = config.get("implementation_files")
    base = config.get("base_implementation_files")
    if not isinstance(implementation, list) or len(implementation) != 9:
        raise GroundedDialogueError("후속 구현 fingerprint 목록이 다릅니다.")
    if not isinstance(base, list) or len(base) != 5:
        raise GroundedDialogueError("부모 구현 fingerprint 목록이 다릅니다.")
    for relative in [*implementation, *base]:
        path = safe_path(repo_root, relative)
        if path.is_symlink() or not path.is_file():
            raise GroundedDialogueError(f"fingerprint 파일이 없습니다: {relative}")
    return {
        "status": "valid",
        "evaluation_version": "v0.2.0",
        "parent_evaluation_build_id": parent["evaluation_build_id"],
        "rescore_rows": 500,
        "context_cases": 100,
        "context_generations": 200,
        "sealed_blind_accessed": False,
    }


def _assert_hashed_file(repo_root: Path, item: Mapping[str, Any], label: str) -> Path:
    path = safe_path(repo_root, str(item["path"]))
    if path.is_symlink() or not path.is_file() or sha256_file(path) != item["sha256"]:
        raise ArtifactError(f"{label} 파일 hash가 다릅니다: {path}")
    return path


def validate_local_artifacts(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    parent = config["parent_evaluation"]
    for name in ("config", "public_aggregate", "public_manifest", "private_manifest"):
        _assert_hashed_file(repo_root, parent[name], f"parent.{name}")
    manifest = load_json(
        safe_path(repo_root, parent["private_manifest"]["path"]),
        "parent private manifest",
    )
    if (
        manifest.get("evaluation_build_id") != parent["evaluation_build_id"]
        or manifest.get("build_sha256") != parent["build_sha256"]
    ):
        raise ArtifactError("부모 private manifest identity가 다릅니다.")
    for arm_id, item in parent["arm_files"].items():
        path = _assert_hashed_file(repo_root, item, f"parent.arm.{arm_id}")
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise ArtifactError(f"부모 arm mode가 0600이 아닙니다: {arm_id}")
    source = _assert_hashed_file(
        repo_root, config["context_diagnostic"]["source_suite"], "context source"
    )
    if stat.S_IMODE(source.stat().st_mode) != PRIVATE_FILE_MODE:
        raise ArtifactError("장문 source suite mode가 0600이 아닙니다.")
    return {"status": "ready", "parent_rows": 500, "context_rows": 100}


def prepare_context(
    repo_root: Path,
    config_path: Path,
    *,
    require_local_artifacts: bool,
) -> dict[str, Any]:
    config = load_json(config_path, "grounded dialogue followup config")
    validate_contract(config, repo_root)
    artifacts = (
        validate_local_artifacts(config, repo_root)
        if require_local_artifacts
        else {"status": "not_checked"}
    )
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(safe_path(repo_root, relative))
        for relative in config["implementation_files"]
    }
    base_hashes = {
        relative: sha256_file(safe_path(repo_root, relative))
        for relative in config["base_implementation_files"]
    }
    common = {
        "evaluation_id": config["evaluation_id"],
        "evaluation_version": config["evaluation_version"],
        "config_path": relative_config,
        "config_sha256": sha256_file(config_path),
        "implementation_hashes": implementation_hashes,
        "base_implementation_hashes": base_hashes,
        "sealed_blind_accessed": False,
    }
    parent = config["parent_evaluation"]
    rescore_inputs = {
        **common,
        "kind": "rescore",
        "parent_evaluation_build_id": parent["evaluation_build_id"],
        "parent_build_sha256": parent["build_sha256"],
        "parent_public_aggregate_sha256": parent["public_aggregate"]["sha256"],
        "parent_private_manifest_sha256": parent["private_manifest"]["sha256"],
        "arm_sha256": {
            arm_id: item["sha256"] for arm_id, item in parent["arm_files"].items()
        },
        "rescore_contract": config["rescore"],
    }
    context_inputs = {
        **common,
        "kind": "long_context",
        "parent_config_sha256": parent["config"]["sha256"],
        "source_suite_sha256": config["context_diagnostic"]["source_suite"]["sha256"],
        "context_contract": config["context_diagnostic"],
    }
    rescore_sha = sha256_json(rescore_inputs)
    context_sha = sha256_json(context_inputs)
    rescore_id = f"eval-{rescore_sha[:12]}"
    context_id = f"eval-{context_sha[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "artifact_validation": artifacts,
        "rescore_build_inputs": rescore_inputs,
        "rescore_build_sha256": rescore_sha,
        "rescore_build_id": rescore_id,
        "rescore_public_root": safe_path(
            repo_root,
            config["rescore"]["public_root"].format(rescore_build_id=rescore_id),
        ),
        "context_build_inputs": context_inputs,
        "context_build_sha256": context_sha,
        "context_build_id": context_id,
        "context_private_root": safe_path(
            repo_root,
            config["context_diagnostic"]["private_root"].format(
                context_build_id=context_id
            ),
        ),
        "context_public_root": safe_path(
            repo_root,
            config["context_diagnostic"]["public_root"].format(
                context_build_id=context_id
            ),
        ),
    }


__all__ = [
    "AUTOMATIC_METRICS",
    "DEFAULT_CONFIG",
    "EXPECTED_ARM_IDS",
    "EXPECTED_BANDS",
    "EXPECTED_CONTEXT_ARMS",
    "PRIVATE_FILE_MODE",
    "PUBLIC_FILE_MODE",
    "REPO_ROOT",
    "prepare_context",
    "validate_contract",
]
