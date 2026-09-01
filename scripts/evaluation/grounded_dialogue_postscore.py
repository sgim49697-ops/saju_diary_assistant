# grounded_dialogue_postscore.py - 불변 응답의 입력 확인·완료 오탐을 scope-aware 규칙으로 후처리한다.

from __future__ import annotations

import argparse
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.evaluation.grounded_dialogue.contracts import (
    PRIVATE_FILE_MODE,
    PUBLIC_FILE_MODE,
    exact_sha,
    load_json,
    pretty_json_bytes,
    public_leak_scan,
    read_jsonl,
    safe_path,
    sha256_file,
    sha256_json,
    write_once,
)
from scripts.evaluation.grounded_dialogue.errors import (
    ArtifactError,
    GroundedDialogueError,
)
from scripts.evaluation.grounded_dialogue.graders import (
    COMPLETION_PATTERN,
    NEGATION_OR_LIMIT_PATTERN,
    SENTENCE_SPLIT_PATTERN,
)
from scripts.evaluation.grounded_dialogue_followup.contracts import (
    prepare_context as prepare_followup_context,
)
from scripts.evaluation.grounded_dialogue_followup.graders import (
    grade_response as grade_response_v2,
)
from scripts.evaluation.grounded_dialogue_followup.reporting import (
    build_context_aggregate,
    build_rescore_aggregate,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path("configs/evaluation/grounded_dialogue_postscore-v0.1.0.json")
EXPECTED_CONTEXT_ARMS = ("C0_KI20_ORACLE_2048", "C1_KI20_ORACLE_3584")
DOMAIN_SCOPE_TERMS = ("분석", "계산", "해석", "검증", "명식", "원국", "결과", "사주")
DOMAIN_SCOPE_PATTERN = re.compile("|".join(DOMAIN_SCOPE_TERMS))


def _hashed_item(item: Any, label: str, repo_root: Path) -> None:
    if not isinstance(item, Mapping):
        raise GroundedDialogueError(f"{label} 계약이 object가 아닙니다.")
    safe_path(repo_root, str(item.get("path", "")))
    exact_sha(item.get("sha256"), f"{label}.sha256")


def validate_contract(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "0.1.0"
        or config.get("evaluation_id") != "grounded-dialogue-postscore"
        or config.get("evaluation_version") != "v0.1.0"
        or config.get("status") != "implemented_not_executed"
    ):
        raise GroundedDialogueError("postscore identity가 다릅니다.")
    _hashed_item(config.get("parent_followup_config"), "parent_followup_config", repo_root)
    parent_rescore = config.get("parent_rescore")
    if not isinstance(parent_rescore, Mapping) or parent_rescore.get("build_id") != (
        "eval-34d2c461b3c0"
    ):
        raise GroundedDialogueError("부모 재채점 build가 다릅니다.")
    _hashed_item(parent_rescore.get("aggregate"), "parent_rescore.aggregate", repo_root)
    _hashed_item(parent_rescore.get("manifest"), "parent_rescore.manifest", repo_root)
    parent_context = config.get("parent_context")
    if not isinstance(parent_context, Mapping) or (
        parent_context.get("build_id") != "eval-7f67d5200b31"
        or parent_context.get("build_sha256")
        != "7f67d5200b31f107acda3a4b1f030850809c6f33f9f4bc98c5bdc7441ab56446"
    ):
        raise GroundedDialogueError("부모 장문 build가 다릅니다.")
    for name in ("aggregate", "manifest", "private_manifest"):
        _hashed_item(parent_context.get(name), f"parent_context.{name}", repo_root)
    arm_files = parent_context.get("arm_files")
    if not isinstance(arm_files, Mapping) or tuple(arm_files) != EXPECTED_CONTEXT_ARMS:
        raise GroundedDialogueError("부모 장문 arm 집합이 다릅니다.")
    for arm_id, item in arm_files.items():
        _hashed_item(item, f"parent_context.arm_files.{arm_id}", repo_root)
        if item.get("rows") != 100:
            raise GroundedDialogueError(f"부모 장문 arm row 수가 다릅니다: {arm_id}")
    if config.get("scorer") != {
        "version": "completion-scope-v3",
        "generic_confirmation_requires_domain_scope": True,
        "domain_scope_terms": list(DOMAIN_SCOPE_TERMS),
    }:
        raise GroundedDialogueError("scope-aware 완료 채점 계약이 다릅니다.")
    outputs = config.get("outputs")
    expected_outputs = {
        "rescore_public_root": "data/reports/saju_1b_baseline/grounded-dialogue-rescore/"
        "v0.1.2/{rescore_build_id}",
        "context_public_root": "data/reports/saju_1b_baseline/grounded-dialogue-context/"
        "v0.1.1/{context_rescore_build_id}",
    }
    if outputs != expected_outputs:
        raise GroundedDialogueError("postscore 출력 경로가 다릅니다.")
    safe_path(
        repo_root,
        outputs["rescore_public_root"].format(rescore_build_id="eval-000000000000"),
    )
    safe_path(
        repo_root,
        outputs["context_public_root"].format(
            context_rescore_build_id="eval-000000000000"
        ),
    )
    if config.get("governance") != {
        "diagnostic_only": True,
        "response_regeneration_allowed": False,
        "repository_local_automatic_metrics_only": True,
        "sealed_blind_access": False,
        "training_execution_allowed": False,
        "promotion_allowed": False,
        "release_approval_allowed": False,
        "application_binding_allowed": False,
        "runtime_configuration_change_allowed": False,
        "public_raw_output_allowed": False,
    }:
        raise GroundedDialogueError("postscore 권한 경계가 다릅니다.")
    implementation = config.get("implementation_files")
    base = config.get("base_implementation_files")
    if not isinstance(implementation, list) or len(implementation) != 2:
        raise GroundedDialogueError("postscore 구현 fingerprint 목록이 다릅니다.")
    if not isinstance(base, list) or len(base) != 6:
        raise GroundedDialogueError("postscore 부모 구현 fingerprint 목록이 다릅니다.")
    for relative in [*implementation, *base]:
        path = safe_path(repo_root, relative)
        if path.is_symlink() or not path.is_file():
            raise GroundedDialogueError(f"postscore fingerprint 파일이 없습니다: {relative}")
    return {
        "status": "valid",
        "evaluation_version": "v0.1.0",
        "response_regenerated": False,
        "sealed_blind_accessed": False,
    }


def _assert_file(repo_root: Path, item: Mapping[str, Any], label: str) -> Path:
    path = safe_path(repo_root, str(item["path"]))
    if path.is_symlink() or not path.is_file() or sha256_file(path) != item["sha256"]:
        raise ArtifactError(f"{label} hash가 다릅니다: {path}")
    return path


def validate_local_artifacts(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    _assert_file(repo_root, config["parent_followup_config"], "parent followup config")
    for name in ("aggregate", "manifest"):
        _assert_file(repo_root, config["parent_rescore"][name], f"parent rescore {name}")
    for name in ("aggregate", "manifest", "private_manifest"):
        _assert_file(repo_root, config["parent_context"][name], f"parent context {name}")
    for arm_id, item in config["parent_context"]["arm_files"].items():
        path = _assert_file(repo_root, item, f"parent context arm {arm_id}")
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise ArtifactError(f"부모 장문 arm mode가 0600이 아닙니다: {arm_id}")
    return {"status": "ready", "rescore_rows": 500, "context_rows": 200}


def prepare_context(
    repo_root: Path,
    config_path: Path,
    *,
    require_local_artifacts: bool,
) -> dict[str, Any]:
    config = load_json(config_path, "grounded dialogue postscore config")
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
        "scorer": config["scorer"],
        "implementation_hashes": implementation_hashes,
        "base_implementation_hashes": base_hashes,
        "response_regenerated": False,
        "sealed_blind_accessed": False,
    }
    rescore_inputs = {
        **common,
        "kind": "baseline_rescore_v3",
        "parent_followup_config_sha256": config["parent_followup_config"]["sha256"],
        "parent_rescore_aggregate_sha256": config["parent_rescore"]["aggregate"][
            "sha256"
        ],
    }
    context_inputs = {
        **common,
        "kind": "context_rescore_v3",
        "parent_context_build_sha256": config["parent_context"]["build_sha256"],
        "parent_context_aggregate_sha256": config["parent_context"]["aggregate"][
            "sha256"
        ],
        "parent_context_private_manifest_sha256": config["parent_context"][
            "private_manifest"
        ]["sha256"],
        "parent_context_arm_sha256": {
            arm_id: item["sha256"]
            for arm_id, item in config["parent_context"]["arm_files"].items()
        },
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
            config["outputs"]["rescore_public_root"].format(
                rescore_build_id=rescore_id
            ),
        ),
        "context_rescore_build_inputs": context_inputs,
        "context_rescore_build_sha256": context_sha,
        "context_rescore_build_id": context_id,
        "context_public_root": safe_path(
            repo_root,
            config["outputs"]["context_public_root"].format(
                context_rescore_build_id=context_id
            ),
        ),
    }


def false_completion_v3(text: str, tool_status: str) -> bool:
    if tool_status not in {"partial", "blocked", "error"}:
        return False
    for sentence in SENTENCE_SPLIT_PATTERN.split(text or ""):
        if not COMPLETION_PATTERN.search(sentence) or NEGATION_OR_LIMIT_PATTERN.search(sentence):
            continue
        if "확인되" in sentence and DOMAIN_SCOPE_PATTERN.search(sentence) is None:
            continue
        return True
    return False


def grade_response_v3(row: Mapping[str, Any]) -> dict[str, Any]:
    tool = row["tool_result"]
    grade = grade_response_v2(
        row["generation"]["output"],
        hard_facts=tool.get("hard_facts"),
        tool_status=tool["status"],
        session_state=row["grading_session_state"],
        decision_action=row["fsm"]["decision_action"],
        max_token_hit=row["generation"]["max_token_hit"],
    )
    grade["false_completion"] = false_completion_v3(
        row["generation"]["output"], tool["status"]
    )
    return grade


def _load_rows(
    repo_root: Path,
    items: Mapping[str, Mapping[str, Any]],
    *,
    identity_key: str,
) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = {}
    for arm_id, item in items.items():
        path = _assert_file(repo_root, item, f"postscore arm {arm_id}")
        rows = read_jsonl(path, f"postscore {arm_id}")
        if (
            len(rows) != item["rows"]
            or any(row.get("arm_id") != arm_id for row in rows)
            or len({row.get(identity_key) for row in rows}) != len(rows)
        ):
            raise ArtifactError(f"postscore arm identity가 다릅니다: {arm_id}")
        updated: list[dict[str, Any]] = []
        for row in rows:
            value = deepcopy(row)
            value["response_grade"] = grade_response_v3(value)
            updated.append(value)
        values[arm_id] = updated
    return values


def build_postscore_aggregates(
    context: Mapping[str, Any], repo_root: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = context["config"]
    followup_path = safe_path(repo_root, config["parent_followup_config"]["path"])
    followup = prepare_followup_context(
        repo_root, followup_path, require_local_artifacts=True
    )
    original_items = followup["config"]["parent_evaluation"]["arm_files"]
    baseline_rows = _load_rows(repo_root, original_items, identity_key="case_id")
    original_aggregate = load_json(
        safe_path(
            repo_root,
            followup["config"]["parent_evaluation"]["public_aggregate"]["path"],
        ),
        "original grounded dialogue aggregate",
    )
    baseline_context = {
        **followup,
        "rescore_build_id": context["rescore_build_id"],
        "rescore_build_sha256": context["rescore_build_sha256"],
    }
    baseline = build_rescore_aggregate(
        baseline_context, baseline_rows, original_aggregate
    )
    baseline.update(
        {
            "schema_version": "0.1.2",
            "evaluation_version": "v0.1.2",
            "scorer_version": config["scorer"]["version"],
            "postscore_parent": {
                "rescore_build_id": config["parent_rescore"]["build_id"],
                "aggregate_sha256": config["parent_rescore"]["aggregate"]["sha256"],
            },
        }
    )
    parent_rescore = load_json(
        safe_path(repo_root, config["parent_rescore"]["aggregate"]["path"]),
        "parent rescore aggregate",
    )
    baseline["comparison_to_previous_rescore"] = {
        arm_id: {
            "false_completion_cases_before": parent_rescore["arms"][arm_id][
                "response"
            ]["false_completion_cases"],
            "false_completion_cases_after": baseline["arms"][arm_id]["response"][
                "false_completion_cases"
            ],
        }
        for arm_id in baseline["arms"]
    }

    context_rows = _load_rows(
        repo_root,
        config["parent_context"]["arm_files"],
        identity_key="context_case_id",
    )
    context_mapping = {
        **followup,
        "context_build_id": context["context_rescore_build_id"],
        "context_build_sha256": context["context_rescore_build_sha256"],
    }
    context_aggregate = build_context_aggregate(context_mapping, context_rows)
    context_aggregate["schema_version"] = "0.1.1"
    context_aggregate["evaluation_version"] = "v0.1.1"
    context_aggregate["context_rescore_build_id"] = context_aggregate.pop(
        "context_build_id"
    )
    context_aggregate["scorer_version"] = config["scorer"]["version"]
    context_aggregate["response_regenerated"] = False
    parent_context = load_json(
        safe_path(repo_root, config["parent_context"]["aggregate"]["path"]),
        "parent context aggregate",
    )
    context_aggregate["postscore_parent"] = {
        "context_build_id": config["parent_context"]["build_id"],
        "build_sha256": config["parent_context"]["build_sha256"],
        "aggregate_sha256": config["parent_context"]["aggregate"]["sha256"],
        "private_manifest_sha256": config["parent_context"]["private_manifest"][
            "sha256"
        ],
    }
    context_aggregate["comparison_to_parent"] = {
        arm_id: {
            "false_completion_cases_before": parent_context["arms"][arm_id][
                "response"
            ]["false_completion_cases"],
            "false_completion_cases_after": context_aggregate["arms"][arm_id][
                "response"
            ]["false_completion_cases"],
            "target_before": parent_context["context_target_by_arm"][arm_id],
            "target_after": context_aggregate["context_target_by_arm"][arm_id],
        }
        for arm_id in EXPECTED_CONTEXT_ARMS
    }
    return baseline, context_aggregate


def _write_public(
    root: Path,
    aggregate: Mapping[str, Any],
    *,
    identity_key: str,
    identity_value: str,
    build_sha256: str,
    implementation_hashes: Mapping[str, str],
    parent_aggregate_sha256: str,
    parent_private_manifest_sha256: str | None,
) -> None:
    public_leak_scan(aggregate)
    aggregate_path = root / "aggregate.json"
    write_once(aggregate_path, pretty_json_bytes(aggregate), mode=PUBLIC_FILE_MODE)
    manifest = {
        "schema_version": aggregate["schema_version"],
        identity_key: identity_value,
        "build_sha256": build_sha256,
        "implementation_sha256": sha256_json(implementation_hashes),
        "parent_aggregate_sha256": parent_aggregate_sha256,
        "public_files": {
            "aggregate.json": {
                "sha256": sha256_file(aggregate_path),
                "bytes": aggregate_path.stat().st_size,
            }
        },
        "raw_outputs_included": False,
        "private_paths_included": False,
        **(
            {"parent_private_manifest_sha256": parent_private_manifest_sha256}
            if parent_private_manifest_sha256 is not None
            else {}
        ),
    }
    public_leak_scan(manifest)
    write_once(root / "build_manifest.json", pretty_json_bytes(manifest), mode=PUBLIC_FILE_MODE)


def execute(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        (context["rescore_public_root"] / "aggregate.json").exists()
        and (context["context_public_root"] / "aggregate.json").exists()
    ):
        return verify(context)
    baseline, context_aggregate = build_postscore_aggregates(context, repo_root)
    if (
        baseline["comparison_to_previous_rescore"]["R2_K0_ORACLE_2048"][
            "false_completion_cases_after"
        ]
        != 0
        or baseline["comparison_to_previous_rescore"][
            "R4_KI20_MODEL_NARROW_2048"
        ]["false_completion_cases_after"]
        != 1
        or context_aggregate["2048_target_met"] is not True
        or context_aggregate["3584_target_met"] is not True
        or context_aggregate["3584_strict_advantage"] is not True
        or context_aggregate["capacity_recommendation"]
        != "retain_3584_as_runtime_candidate_ceiling"
    ):
        raise GroundedDialogueError("postscore 회귀 기대값이 다릅니다.")
    _write_public(
        context["rescore_public_root"],
        baseline,
        identity_key="rescore_build_id",
        identity_value=context["rescore_build_id"],
        build_sha256=context["rescore_build_sha256"],
        implementation_hashes=context["rescore_build_inputs"]["implementation_hashes"],
        parent_aggregate_sha256=context["config"]["parent_rescore"]["aggregate"][
            "sha256"
        ],
        parent_private_manifest_sha256=None,
    )
    _write_public(
        context["context_public_root"],
        context_aggregate,
        identity_key="context_rescore_build_id",
        identity_value=context["context_rescore_build_id"],
        build_sha256=context["context_rescore_build_sha256"],
        implementation_hashes=context["context_rescore_build_inputs"][
            "implementation_hashes"
        ],
        parent_aggregate_sha256=context["config"]["parent_context"]["aggregate"][
            "sha256"
        ],
        parent_private_manifest_sha256=context["config"]["parent_context"][
            "private_manifest"
        ]["sha256"],
    )
    return verify(context)


def _verify_public(
    root: Path,
    *,
    identity_key: str,
    identity_value: str,
    build_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        root.is_symlink()
        or not root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) != 0o755
        or {path.name for path in root.iterdir()} != {"aggregate.json", "build_manifest.json"}
    ):
        raise ArtifactError("postscore 공개 root 파일 집합·mode가 다릅니다.")
    aggregate = load_json(root / "aggregate.json", "postscore aggregate")
    manifest = load_json(root / "build_manifest.json", "postscore manifest")
    public_leak_scan(aggregate)
    public_leak_scan(manifest)
    metadata = manifest.get("public_files", {}).get("aggregate.json", {})
    if (
        aggregate.get(identity_key) != identity_value
        or aggregate.get("build_sha256") != build_sha256
        or manifest.get(identity_key) != identity_value
        or manifest.get("build_sha256") != build_sha256
        or sha256_file(root / "aggregate.json") != metadata.get("sha256")
        or (root / "aggregate.json").stat().st_size != metadata.get("bytes")
        or stat.S_IMODE((root / "aggregate.json").stat().st_mode) != PUBLIC_FILE_MODE
        or stat.S_IMODE((root / "build_manifest.json").stat().st_mode) != PUBLIC_FILE_MODE
    ):
        raise ArtifactError("postscore 공개 파일 검증에 실패했습니다.")
    return aggregate, manifest


def verify(context: Mapping[str, Any]) -> dict[str, Any]:
    baseline, baseline_manifest = _verify_public(
        context["rescore_public_root"],
        identity_key="rescore_build_id",
        identity_value=context["rescore_build_id"],
        build_sha256=context["rescore_build_sha256"],
    )
    long_context, context_manifest = _verify_public(
        context["context_public_root"],
        identity_key="context_rescore_build_id",
        identity_value=context["context_rescore_build_id"],
        build_sha256=context["context_rescore_build_sha256"],
    )
    config = context["config"]
    if (
        baseline.get("schema_version") != "0.1.2"
        or baseline.get("scorer_version") != "completion-scope-v3"
        or baseline.get("response_regenerated") is not False
        or baseline.get("diagnostic_target_met") is not False
        or long_context.get("schema_version") != "0.1.1"
        or long_context.get("scorer_version") != "completion-scope-v3"
        or long_context.get("response_regenerated") is not False
        or long_context.get("response_generations") != 200
        or long_context.get("prompt_budget_failure_cases") != 0
        or long_context.get("2048_target_met") is not True
        or long_context.get("3584_target_met") is not True
        or long_context.get("capacity_recommendation")
        != "retain_3584_as_runtime_candidate_ceiling"
        or long_context.get("runtime_configuration_changed") is not False
        or baseline_manifest.get("parent_aggregate_sha256")
        != config["parent_rescore"]["aggregate"]["sha256"]
        or context_manifest.get("parent_aggregate_sha256")
        != config["parent_context"]["aggregate"]["sha256"]
        or context_manifest.get("parent_private_manifest_sha256")
        != config["parent_context"]["private_manifest"]["sha256"]
    ):
        raise ArtifactError("postscore 결과 계약이 다릅니다.")
    return {
        "status": "verified",
        "rescore_build_id": context["rescore_build_id"],
        "context_rescore_build_id": context["context_rescore_build_id"],
        "response_regenerated": False,
        "context_2048_target_met": True,
        "context_3584_target_met": True,
        "capacity_recommendation": "retain_3584_as_runtime_candidate_ceiling",
        "runtime_configuration_changed": False,
        "sealed_blind_accessed": False,
    }


def plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "ready_not_executed",
        "rescore_build_id": context["rescore_build_id"],
        "context_rescore_build_id": context["context_rescore_build_id"],
        "baseline_rows": 500,
        "context_rows": 200,
        "response_regenerated": False,
        "writes_performed": False,
        "sealed_blind_accessed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="grounded dialogue scope-aware 후처리")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("execute")
    run.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "postscore config"), REPO_ROOT)
        else:
            context = prepare_context(
                REPO_ROOT, config_path, require_local_artifacts=True
            )
            if args.command == "plan":
                result = plan(context)
            elif args.command == "execute":
                result = execute(context, REPO_ROOT) if args.execute else {
                    **plan(context),
                    "status": "dry_run",
                }
            else:
                result = verify(context)
        print(pretty_json_bytes(result).decode("utf-8"), end="")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계는 구조화 실패를 반환한다.
        print(pretty_json_bytes({"status": "failed", "error": str(exc)}).decode(), end="", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
