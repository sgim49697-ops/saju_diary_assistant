# reporting.py - 재채점·장문 private 결과를 공개 안전 집계와 불변 manifest로 만든다.

from __future__ import annotations

import math
import re
import stat
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.evaluation.grounded_dialogue.contracts import (
    PRIVATE_FILE_MODE,
    PUBLIC_FILE_MODE,
    load_json,
    pretty_json_bytes,
    public_leak_scan,
    sha256_file,
    sha256_json,
    write_once,
)
from scripts.evaluation.grounded_dialogue.errors import (
    ArtifactError,
    GroundedDialogueError,
)
from scripts.evaluation.grounded_dialogue.reporting import aggregate_arm

RESPONSE_DEFECT_FIELDS = (
    "fabricated_pillars_cases",
    "unknown_hour_violations_cases",
    "fact_contradictions_cases",
    "false_completion_cases",
    "provided_field_reasks_cases",
    "severe_safety_cases",
    "max_token_hit_cases",
)
GRADE_DEFECT_FIELDS = (
    "fabricated_pillars",
    "unknown_hour_violations",
    "fact_contradictions",
    "false_completion",
    "provided_field_reasks",
    "severe_safety",
    "max_token_hit",
)


def _nearest_rank(values: Sequence[int], percentile: int) -> int:
    if not values:
        return 0
    ordered = sorted(int(value) for value in values)
    index = max(0, math.ceil(percentile * len(ordered) / 100) - 1)
    return ordered[index]


def distribution(values: Sequence[int]) -> dict[str, int]:
    return {
        "minimum": min(values, default=0),
        "p50": _nearest_rank(values, 50),
        "p90": _nearest_rank(values, 90),
        "p95": _nearest_rank(values, 95),
        "p99": _nearest_rank(values, 99),
        "maximum": max(values, default=0),
    }


def response_shape(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [
        re.sub(r"\s+", " ", str(row["generation"]["output"])).strip().casefold()
        for row in rows
        if row["generation"]["attempted"]
    ]
    counts = Counter(normalized)
    generated = len(normalized)
    return {
        "generated_cases": generated,
        "unique_normalized_responses": len(counts),
        "unique_percent": round(len(counts) * 100 / generated, 6) if generated else 0.0,
        "largest_duplicate_cluster": max(counts.values(), default=0),
        "new_tokens": distribution(
            [
                int(row["generation"]["new_tokens"])
                for row in rows
                if row["generation"]["attempted"]
            ]
        ),
    }


def arm_target(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> bool:
    response = metrics["response"]
    return (
        response["fabricated_pillars_cases"] <= thresholds["fabricated_pillars_maximum"]
        and response["unknown_hour_violations_cases"]
        <= thresholds["unknown_hour_violations_maximum"]
        and response["fact_contradictions_cases"]
        <= thresholds["fact_contradictions_maximum"]
        and response["false_completion_cases"] <= thresholds["false_completion_maximum"]
        and response["provided_field_reask_percent"]
        <= thresholds["provided_field_reask_percent_maximum"]
        and response["severe_safety_cases"] <= thresholds["severe_safety_maximum"]
        and response["nonempty_percent"] >= thresholds["nonempty_percent_minimum"]
    )


def build_rescore_aggregate(
    context: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    parent_aggregate: Mapping[str, Any],
) -> dict[str, Any]:
    config = context["config"]
    metrics = {arm_id: aggregate_arm(rows) for arm_id, rows in rows_by_arm.items()}
    thresholds = config["rescore"]["automatic_metrics"]
    targets = {arm_id: arm_target(value, thresholds) for arm_id, value in metrics.items()}
    comparisons: dict[str, Any] = {}
    for arm_id, value in metrics.items():
        before = parent_aggregate["arms"][arm_id]
        response_delta = {
            field: value["response"][field] - before["response"][field]
            for field in RESPONSE_DEFECT_FIELDS
        }
        response_delta["nonempty_cases"] = (
            value["response"]["nonempty_cases"] - before["response"]["nonempty_cases"]
        )
        comparisons[arm_id] = {
            "response_case_delta_after_minus_before": response_delta,
            "provided_field_reask_percent_before": before["response"][
                "provided_field_reask_percent"
            ],
            "provided_field_reask_percent_after": value["response"][
                "provided_field_reask_percent"
            ],
            "target_before": bool(parent_aggregate["diagnostic_target_by_arm"][arm_id]),
            "target_after": targets[arm_id],
        }
    return {
        "schema_version": "0.1.1",
        "evaluation_id": "grounded-dialogue-rescore",
        "evaluation_version": "v0.1.1",
        "rescore_build_id": context["rescore_build_id"],
        "build_sha256": context["rescore_build_sha256"],
        "status": "completed_diagnostic_rescore",
        "rescore_completed": True,
        "parent": {
            "evaluation_build_id": config["parent_evaluation"]["evaluation_build_id"],
            "build_sha256": config["parent_evaluation"]["build_sha256"],
            "public_aggregate_sha256": config["parent_evaluation"]["public_aggregate"][
                "sha256"
            ],
            "private_manifest_sha256": config["parent_evaluation"]["private_manifest"][
                "sha256"
            ],
        },
        "scorer_version": config["rescore"]["scorer_version"],
        "rows_rescored": sum(len(rows) for rows in rows_by_arm.values()),
        "response_regenerated": False,
        "diagnostic_target_met": all(targets.values()),
        "diagnostic_target_by_arm": targets,
        "arms": {
            arm_id: {**value, "response_shape": response_shape(rows_by_arm[arm_id])}
            for arm_id, value in metrics.items()
        },
        "comparison_to_parent": comparisons,
        "authority": {
            "diagnostic_only": True,
            "sealed_blind_accessed": False,
            "runtime_release_approved": False,
            "application_binding_performed": False,
            "training_performed": False,
            "promotion_allowed": False,
        },
    }


def _aggregate_band(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise GroundedDialogueError("장문 band row가 비었습니다.")
    defect_counts = {
        f"{field}_cases": sum(bool(row["response_grade"][field]) for row in rows)
        for field in GRADE_DEFECT_FIELDS
    }
    nonempty = sum(bool(row["response_grade"]["nonempty"]) for row in rows)
    return {
        "cases": len(rows),
        "token_budget": {
            "original_input_tokens": distribution(
                [int(row["prompt_metadata"]["original_input_tokens"]) for row in rows]
            ),
            "final_input_tokens": distribution(
                [int(row["prompt_metadata"]["final_input_tokens"]) for row in rows]
            ),
            "dropped_complete_pairs": distribution(
                [int(row["prompt_metadata"]["dropped_complete_pairs"]) for row in rows]
            ),
            "budget_failure_cases": sum(
                row["prompt_metadata"]["error_code"] == "MINIMAL_PROMPT_OVER_BUDGET"
                for row in rows
            ),
        },
        "response": {
            **defect_counts,
            "nonempty_cases": nonempty,
            "nonempty_percent": round(nonempty * 100 / len(rows), 6),
        },
    }


def aggregate_context_arm(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = aggregate_arm(rows)
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["band_id"])].append(row)
    return {
        **base,
        "token_budget": {
            "original_input_tokens": distribution(
                [int(row["prompt_metadata"]["original_input_tokens"]) for row in rows]
            ),
            "base_input_tokens": distribution(
                [int(row["prompt_metadata"]["base_input_tokens"]) for row in rows]
            ),
            "final_input_tokens": distribution(
                [int(row["prompt_metadata"]["final_input_tokens"]) for row in rows]
            ),
            "dropped_complete_pairs": distribution(
                [int(row["prompt_metadata"]["dropped_complete_pairs"]) for row in rows]
            ),
        },
        "response_shape": response_shape(rows),
        "by_token_band": {
            band_id: _aggregate_band(values) for band_id, values in sorted(grouped.items())
        },
    }


def _paired_comparison(
    baseline_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline = {row["context_case_id"]: row for row in baseline_rows}
    comparison = {row["context_case_id"]: row for row in comparison_rows}
    if set(baseline) != set(comparison) or len(baseline) != 100:
        raise GroundedDialogueError("장문 paired case identity가 다릅니다.")
    invariants = 0
    fields: dict[str, dict[str, int]] = {}
    for field in GRADE_DEFECT_FIELDS:
        improved = worsened = unchanged = 0
        for case_id in sorted(baseline):
            left = bool(baseline[case_id]["response_grade"][field])
            right = bool(comparison[case_id]["response_grade"][field])
            if left and not right:
                improved += 1
            elif not left and right:
                worsened += 1
            else:
                unchanged += 1
        fields[field] = {
            "comparison_improved_cases": improved,
            "comparison_worsened_cases": worsened,
            "unchanged_cases": unchanged,
        }
    nonempty = {"comparison_improved_cases": 0, "comparison_worsened_cases": 0, "unchanged_cases": 0}
    for case_id in sorted(baseline):
        left_row, right_row = baseline[case_id], comparison[case_id]
        if (
            left_row["fsm"] == right_row["fsm"]
            and left_row["route"] == right_row["route"]
            and left_row["tool_result"] == right_row["tool_result"]
            and left_row["extraction"] == right_row["extraction"]
        ):
            invariants += 1
        left = bool(left_row["response_grade"]["nonempty"])
        right = bool(right_row["response_grade"]["nonempty"])
        key = (
            "comparison_improved_cases"
            if not left and right
            else "comparison_worsened_cases"
            if left and not right
            else "unchanged_cases"
        )
        nonempty[key] += 1
    fields["nonempty"] = nonempty
    return {"paired_cases": 100, "pre_generation_invariant_cases": invariants, "grades": fields}


def build_context_aggregate(
    context: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    config = context["config"]
    arm_ids = [arm["arm_id"] for arm in config["context_diagnostic"]["arms"]]
    if set(rows_by_arm) != set(arm_ids):
        raise GroundedDialogueError("장문 aggregate arm 집합이 다릅니다.")
    arms = {arm_id: aggregate_context_arm(rows_by_arm[arm_id]) for arm_id in arm_ids}
    thresholds = config["rescore"]["automatic_metrics"]
    targets = {arm_id: arm_target(arms[arm_id], thresholds) for arm_id in arm_ids}
    paired = _paired_comparison(rows_by_arm[arm_ids[0]], rows_by_arm[arm_ids[1]])
    left_response = arms[arm_ids[0]]["response"]
    right_response = arms[arm_ids[1]]["response"]
    defect_fields = [
        "fabricated_pillars_cases",
        "unknown_hour_violations_cases",
        "fact_contradictions_cases",
        "false_completion_cases",
        "provided_field_reasks_cases",
        "severe_safety_cases",
        "max_token_hit_cases",
    ]
    left_defects = [left_response[field] for field in defect_fields] + [
        100 - left_response["nonempty_cases"]
    ]
    right_defects = [right_response[field] for field in defect_fields] + [
        100 - right_response["nonempty_cases"]
    ]
    strict_advantage = all(right <= left for left, right in zip(left_defects, right_defects, strict=True)) and any(
        right < left for left, right in zip(left_defects, right_defects, strict=True)
    )
    if targets[arm_ids[0]] and not strict_advantage:
        recommendation = "2048_sufficient_for_structured_path"
    elif targets[arm_ids[1]] and strict_advantage:
        recommendation = "retain_3584_as_runtime_candidate_ceiling"
    else:
        recommendation = "mixed_or_inconclusive"
    prompt_failures = sum(
        arms[arm_id]["response"]["prompt_budget_failure_cases"] for arm_id in arm_ids
    )
    attempted = sum(
        arms[arm_id]["response"]["generation_attempted_cases"] for arm_id in arm_ids
    )
    return {
        "schema_version": "0.1.0",
        "evaluation_id": "grounded-dialogue-context",
        "evaluation_version": "v0.1.0",
        "context_build_id": context["context_build_id"],
        "build_sha256": context["context_build_sha256"],
        "status": "completed_context_diagnostic",
        "diagnostic_completed": True,
        "cases_per_arm": 100,
        "planned_response_cases": 200,
        "response_generations": attempted,
        "prompt_budget_failure_cases": prompt_failures,
        "context_target_by_arm": targets,
        "2048_target_met": targets[arm_ids[0]],
        "3584_target_met": targets[arm_ids[1]],
        "3584_strict_advantage": strict_advantage,
        "capacity_recommendation": recommendation,
        "runtime_configuration_changed": False,
        "arms": arms,
        "paired_comparison": paired,
        "authority": {
            "diagnostic_only": True,
            "sealed_blind_accessed": False,
            "runtime_release_approved": False,
            "application_binding_performed": False,
            "training_performed": False,
            "promotion_allowed": False,
        },
    }


def _write_public(
    root: Path,
    aggregate: Mapping[str, Any],
    *,
    identity_key: str,
    identity_value: str,
    build_sha256: str,
    implementation_hashes: Mapping[str, str],
    extra_manifest: Mapping[str, Any] | None = None,
) -> None:
    public_leak_scan(aggregate)
    aggregate_path = root / "aggregate.json"
    write_once(aggregate_path, pretty_json_bytes(aggregate), mode=PUBLIC_FILE_MODE)
    manifest = {
        "schema_version": aggregate["schema_version"],
        identity_key: identity_value,
        "build_sha256": build_sha256,
        "implementation_sha256": sha256_json(implementation_hashes),
        "public_files": {
            "aggregate.json": {
                "sha256": sha256_file(aggregate_path),
                "bytes": aggregate_path.stat().st_size,
            }
        },
        "raw_outputs_included": False,
        "private_paths_included": False,
        **dict(extra_manifest or {}),
    }
    public_leak_scan(manifest)
    write_once(root / "build_manifest.json", pretty_json_bytes(manifest), mode=PUBLIC_FILE_MODE)


def write_rescore_reports(context: Mapping[str, Any], aggregate: Mapping[str, Any]) -> None:
    _write_public(
        context["rescore_public_root"],
        aggregate,
        identity_key="rescore_build_id",
        identity_value=context["rescore_build_id"],
        build_sha256=context["rescore_build_sha256"],
        implementation_hashes=context["rescore_build_inputs"]["implementation_hashes"],
        extra_manifest={
            "parent_public_aggregate_sha256": context["config"]["parent_evaluation"][
                "public_aggregate"
            ]["sha256"],
            "parent_private_manifest_sha256": context["config"]["parent_evaluation"][
                "private_manifest"
            ]["sha256"],
        },
    )


def write_context_reports(
    context: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    _write_public(
        context["context_public_root"],
        aggregate,
        identity_key="context_build_id",
        identity_value=context["context_build_id"],
        build_sha256=context["context_build_sha256"],
        implementation_hashes=context["context_build_inputs"]["implementation_hashes"],
    )
    private_root = context["context_private_root"]
    files: dict[str, Any] = {}
    for relative in ["suite.jsonl", "run_metadata.json", *[f"arms/{arm_id}.jsonl" for arm_id in sorted(rows_by_arm)]]:
        path = private_root / relative
        files[relative] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            **({"rows": len(rows_by_arm[path.stem])} if relative.startswith("arms/") else {}),
        }
    write_once(
        private_root / "private_manifest.json",
        pretty_json_bytes(
            {
                "schema_version": "0.1.0",
                "context_build_id": context["context_build_id"],
                "build_sha256": context["context_build_sha256"],
                "files": files,
            }
        ),
        mode=PRIVATE_FILE_MODE,
    )
    write_once(
        private_root / "completed.json",
        pretty_json_bytes(
            {
                "schema_version": "0.1.0",
                "context_build_id": context["context_build_id"],
                "build_sha256": context["context_build_sha256"],
                "status": "completed_context_diagnostic",
                "response_generations": aggregate["response_generations"],
                "prompt_budget_failure_cases": aggregate["prompt_budget_failure_cases"],
                "sealed_blind_accessed": False,
                "promotion_allowed": False,
            }
        ),
        mode=PRIVATE_FILE_MODE,
    )


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
        raise ArtifactError("후속 공개 root 파일 집합·mode가 다릅니다.")
    aggregate = load_json(root / "aggregate.json", "followup public aggregate")
    manifest = load_json(root / "build_manifest.json", "followup public manifest")
    public_leak_scan(aggregate)
    public_leak_scan(manifest)
    if (
        aggregate.get(identity_key) != identity_value
        or aggregate.get("build_sha256") != build_sha256
        or manifest.get(identity_key) != identity_value
        or manifest.get("build_sha256") != build_sha256
        or set(manifest.get("public_files", {})) != {"aggregate.json"}
    ):
        raise ArtifactError("후속 공개 identity가 다릅니다.")
    metadata = manifest["public_files"]["aggregate.json"]
    aggregate_path = root / "aggregate.json"
    if (
        sha256_file(aggregate_path) != metadata.get("sha256")
        or aggregate_path.stat().st_size != metadata.get("bytes")
        or stat.S_IMODE(aggregate_path.stat().st_mode) != PUBLIC_FILE_MODE
        or stat.S_IMODE((root / "build_manifest.json").stat().st_mode) != PUBLIC_FILE_MODE
    ):
        raise ArtifactError("후속 공개 manifest 검증에 실패했습니다.")
    return aggregate, manifest


def verify_rescore(context: Mapping[str, Any]) -> dict[str, Any]:
    aggregate, manifest = _verify_public(
        context["rescore_public_root"],
        identity_key="rescore_build_id",
        identity_value=context["rescore_build_id"],
        build_sha256=context["rescore_build_sha256"],
    )
    parent = context["config"]["parent_evaluation"]
    if (
        aggregate.get("status") != "completed_diagnostic_rescore"
        or aggregate.get("rescore_completed") is not True
        or aggregate.get("rows_rescored") != 500
        or aggregate.get("response_regenerated") is not False
        or manifest.get("parent_public_aggregate_sha256")
        != parent["public_aggregate"]["sha256"]
        or manifest.get("parent_private_manifest_sha256")
        != parent["private_manifest"]["sha256"]
    ):
        raise ArtifactError("재채점 결과 계약이 다릅니다.")
    return {
        "status": "verified",
        "rescore_build_id": context["rescore_build_id"],
        "rows_rescored": 500,
        "diagnostic_target_met": aggregate["diagnostic_target_met"],
        "sealed_blind_accessed": False,
    }


def verify_context(context: Mapping[str, Any]) -> dict[str, Any]:
    aggregate, _ = _verify_public(
        context["context_public_root"],
        identity_key="context_build_id",
        identity_value=context["context_build_id"],
        build_sha256=context["context_build_sha256"],
    )
    private_root = context["context_private_root"]
    expected_arm_ids = {
        arm["arm_id"] for arm in context["config"]["context_diagnostic"]["arms"]
    }
    expected_root = {
        "arms",
        "completed.json",
        "execution.lock",
        "private_manifest.json",
        "run_metadata.json",
        "suite.jsonl",
    }
    if (
        private_root.is_symlink()
        or not private_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) != 0o700
        or {path.name for path in private_root.iterdir()} != expected_root
        or {path.stem for path in (private_root / "arms").iterdir()} != expected_arm_ids
    ):
        raise ArtifactError("장문 private root 파일 집합이 다릅니다.")
    manifest = load_json(private_root / "private_manifest.json", "context private manifest")
    expected_files = {
        "suite.jsonl",
        "run_metadata.json",
        *{f"arms/{arm_id}.jsonl" for arm_id in expected_arm_ids},
    }
    if (
        manifest.get("context_build_id") != context["context_build_id"]
        or manifest.get("build_sha256") != context["context_build_sha256"]
        or set(manifest.get("files", {})) != expected_files
    ):
        raise ArtifactError("장문 private manifest identity가 다릅니다.")
    for relative, metadata in manifest["files"].items():
        path = private_root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != metadata.get("sha256")
            or path.stat().st_size != metadata.get("bytes")
            or stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE
        ):
            raise ArtifactError(f"장문 private 파일 검증에 실패했습니다: {relative}")
    completion = load_json(private_root / "completed.json", "context completion")
    if (
        aggregate.get("status") != "completed_context_diagnostic"
        or aggregate.get("diagnostic_completed") is not True
        or aggregate.get("response_generations") != 200
        or aggregate.get("prompt_budget_failure_cases") != 0
        or completion.get("status") != "completed_context_diagnostic"
        or completion.get("sealed_blind_accessed") is not False
        or completion.get("promotion_allowed") is not False
    ):
        raise ArtifactError("장문 완료 조건이 다릅니다.")
    return {
        "status": "verified",
        "context_build_id": context["context_build_id"],
        "response_generations": 200,
        "prompt_budget_failure_cases": 0,
        "capacity_recommendation": aggregate["capacity_recommendation"],
        "runtime_configuration_changed": False,
        "sealed_blind_accessed": False,
    }


__all__ = [
    "aggregate_context_arm",
    "arm_target",
    "build_context_aggregate",
    "build_rescore_aggregate",
    "distribution",
    "response_shape",
    "verify_context",
    "verify_rescore",
    "write_context_reports",
    "write_rescore_reports",
]
