# reporting.py - private arm 결과를 공개 안전 집계로 만들고 불변 manifest를 검증한다.

from __future__ import annotations

import stat
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .contracts import (
    PRIVATE_FILE_MODE,
    PUBLIC_FILE_MODE,
    load_json,
    pretty_json_bytes,
    public_leak_scan,
    sha256_file,
    sha256_json,
    write_once,
)
from .errors import ArtifactError, GroundedDialogueError


def aggregate_arm(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) != 100:
        raise GroundedDialogueError("arm aggregate row 수가 100이 아닙니다.")
    response_fields = (
        "fabricated_pillars",
        "unknown_hour_violations",
        "fact_contradictions",
        "false_completion",
        "provided_field_reasks",
        "severe_safety",
        "max_token_hit",
    )
    response_counts = {
        name: sum(bool(row["response_grade"][name]) for row in rows)
        for name in response_fields
    }
    nonempty = sum(bool(row["response_grade"]["nonempty"]) for row in rows)
    generations = sum(bool(row["generation"]["attempted"]) for row in rows)
    budget_failures = sum(
        row["generation"].get("error_code") == "MINIMAL_PROMPT_OVER_BUDGET"
        for row in rows
    )
    exact = sum(bool(row["extraction"]["score"]["exact_state"]) for row in rows)
    invalid = sum(bool(row["extraction"]["score"]["invalid_extraction"]) for row in rows)
    time_pass = sum(bool(row["extraction"]["score"]["time_semantics_pass"]) for row in rows)
    correction_rows = [
        row for row in rows if row["extraction"]["score"]["correction_required"]
    ]
    corrections_passed = sum(
        bool(row["extraction"]["score"]["correction_pass"]) for row in correction_rows
    )
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["stratum"])].append(row)
    by_stratum = {
        stratum: {
            "cases": len(values),
            "slot_exact_state_percent": round(
                sum(row["extraction"]["score"]["exact_state"] for row in values)
                * 100
                / len(values),
                6,
            ),
            "fabricated_pillar_cases": sum(
                bool(row["response_grade"]["fabricated_pillars"]) for row in values
            ),
            "fact_contradiction_cases": sum(
                bool(row["response_grade"]["fact_contradictions"]) for row in values
            ),
            "provided_field_reask_cases": sum(
                bool(row["response_grade"]["provided_field_reasks"]) for row in values
            ),
        }
        for stratum, values in sorted(grouped.items())
    }
    return {
        "cases": len(rows),
        "slot_extraction": {
            "exact_state_cases": exact,
            "exact_state_percent": round(exact * 100 / len(rows), 6),
            "invalid_cases": invalid,
            "time_semantics_passed": time_pass,
            "corrections_required": len(correction_rows),
            "corrections_passed": corrections_passed,
        },
        "response": {
            **{f"{name}_cases": value for name, value in response_counts.items()},
            "generation_attempted_cases": generations,
            "prompt_budget_failure_cases": budget_failures,
            "provided_field_reask_percent": round(
                response_counts["provided_field_reasks"] * 100 / len(rows), 6
            ),
            "nonempty_cases": nonempty,
            "nonempty_percent": round(nonempty * 100 / len(rows), 6),
        },
        "by_stratum": by_stratum,
    }


def _arm_target(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> bool:
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


def build_aggregate(
    context: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    rule_gate: Mapping[str, Any],
) -> dict[str, Any]:
    config = context["config"]
    arms = {arm_id: aggregate_arm(rows) for arm_id, rows in rows_by_arm.items()}
    contrasts: dict[str, Any] = {}
    delta_fields = (
        ("slot_exact_state_percent", "slot_extraction", "exact_state_percent"),
        ("fabricated_pillar_cases", "response", "fabricated_pillars_cases"),
        ("fact_contradiction_cases", "response", "fact_contradictions_cases"),
        ("provided_field_reask_percent", "response", "provided_field_reask_percent"),
        ("nonempty_percent", "response", "nonempty_percent"),
    )
    for name, pair in config["contrasts"].items():
        baseline, comparison = pair
        contrasts[name] = {
            "baseline_arm": baseline,
            "comparison_arm": comparison,
            "comparison_minus_baseline": {
                label: round(
                    arms[comparison][section][field] - arms[baseline][section][field],
                    6,
                )
                for label, section, field in delta_fields
            },
        }
    targets = {
        arm_id: _arm_target(metrics, config["automatic_metrics"])
        for arm_id, metrics in arms.items()
    }
    return {
        "schema_version": "0.1.0",
        "evaluation_id": config["evaluation_id"],
        "evaluation_version": config["evaluation_version"],
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "status": "completed_diagnostic",
        "diagnostic_completed": True,
        "diagnostic_target_met": all(targets.values()),
        "diagnostic_target_by_arm": targets,
        "cases_per_arm": 100,
        "planned_response_cases": 500,
        "response_generations": sum(
            metrics["response"]["generation_attempted_cases"] for metrics in arms.values()
        ),
        "model_narrow_extractions": 120,
        "rule_harness_gate": dict(rule_gate),
        "arms": arms,
        "contrasts": contrasts,
        "quality_dimensions": {
            "semantics": "not_measured",
            "naturalness": "not_measured",
            "human_gate": False,
        },
        "authority": {
            "diagnostic_only": True,
            "candidate_fact_authority": "HARD_CANDIDATE",
            "candidate_result_inserted_into_app_fsm": False,
            "runtime_release_approved": False,
            "application_binding_performed": False,
            "training_performed": False,
            "promotion_allowed": False,
            "sealed_blind_accessed": False,
        },
    }


def write_reports(
    context: Mapping[str, Any],
    aggregate: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    public_leak_scan(aggregate)
    aggregate_path = context["public_root"] / "aggregate.json"
    write_once(aggregate_path, pretty_json_bytes(aggregate), mode=PUBLIC_FILE_MODE)
    public_manifest = {
        "schema_version": "0.1.0",
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "implementation_sha256": sha256_json(
            context["build_inputs"]["implementation_hashes"]
        ),
        "public_files": {
            "aggregate.json": {
                "sha256": sha256_file(aggregate_path),
                "bytes": aggregate_path.stat().st_size,
            }
        },
        "raw_outputs_included": False,
        "private_paths_included": False,
    }
    public_leak_scan(public_manifest)
    write_once(
        context["public_root"] / "build_manifest.json",
        pretty_json_bytes(public_manifest),
        mode=PUBLIC_FILE_MODE,
    )
    private_files: dict[str, Any] = {}
    for arm_id in sorted(rows_by_arm):
        relative = f"arms/{arm_id}.jsonl"
        path = context["private_root"] / relative
        private_files[relative] = {
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "rows": len(rows_by_arm[arm_id]),
        }
    metadata_path = context["private_root"] / "run_metadata.json"
    private_files["run_metadata.json"] = {
        "sha256": sha256_file(metadata_path),
        "bytes": metadata_path.stat().st_size,
    }
    write_once(
        context["private_root"] / "private_manifest.json",
        pretty_json_bytes(
            {
                "schema_version": "0.1.0",
                "evaluation_build_id": context["evaluation_build_id"],
                "build_sha256": context["build_sha256"],
                "files": private_files,
            }
        ),
        mode=PRIVATE_FILE_MODE,
    )
    write_once(
        context["private_root"] / "completed.json",
        pretty_json_bytes(
            {
                "schema_version": "0.1.0",
                "evaluation_build_id": context["evaluation_build_id"],
                "build_sha256": context["build_sha256"],
                "status": "completed_diagnostic",
                "diagnostic_target_met": aggregate["diagnostic_target_met"],
                "sealed_blind_accessed": False,
                "promotion_allowed": False,
            }
        ),
        mode=PRIVATE_FILE_MODE,
    )


def verify(context: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = load_json(context["public_root"] / "aggregate.json", "public aggregate")
    manifest_path = context["public_root"] / "build_manifest.json"
    manifest = load_json(manifest_path, "public build manifest")
    public_leak_scan(aggregate)
    public_leak_scan(manifest)
    if (
        aggregate.get("evaluation_build_id") != context["evaluation_build_id"]
        or aggregate.get("build_sha256") != context["build_sha256"]
        or aggregate.get("status") != "completed_diagnostic"
        or aggregate.get("diagnostic_completed") is not True
        or aggregate.get("authority", {}).get("sealed_blind_accessed") is not False
    ):
        raise ArtifactError("공개 aggregate identity·상태가 다릅니다.")
    if (
        manifest.get("evaluation_build_id") != context["evaluation_build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or stat.S_IMODE(manifest_path.stat().st_mode) != PUBLIC_FILE_MODE
    ):
        raise ArtifactError("공개 manifest identity·mode가 다릅니다.")
    for relative, metadata in manifest.get("public_files", {}).items():
        path = context["public_root"] / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != metadata.get("sha256")
            or path.stat().st_size != metadata.get("bytes")
            or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE
        ):
            raise ArtifactError(f"공개 manifest 검증에 실패했습니다: {relative}")
    private_manifest_path = context["private_root"] / "private_manifest.json"
    private_manifest = load_json(private_manifest_path, "private manifest")
    if (
        private_manifest.get("evaluation_build_id") != context["evaluation_build_id"]
        or private_manifest.get("build_sha256") != context["build_sha256"]
        or stat.S_IMODE(private_manifest_path.stat().st_mode) != PRIVATE_FILE_MODE
    ):
        raise ArtifactError("private manifest identity·mode가 다릅니다.")
    for relative, metadata in private_manifest.get("files", {}).items():
        path = context["private_root"] / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != metadata.get("sha256")
            or path.stat().st_size != metadata.get("bytes")
            or stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE
        ):
            raise ArtifactError(f"private manifest 검증에 실패했습니다: {relative}")
    completion_path = context["private_root"] / "completed.json"
    completion = load_json(completion_path, "completion")
    if (
        completion.get("evaluation_build_id") != context["evaluation_build_id"]
        or completion.get("build_sha256") != context["build_sha256"]
        or completion.get("status") != "completed_diagnostic"
        or completion.get("sealed_blind_accessed") is not False
        or completion.get("promotion_allowed") is not False
        or stat.S_IMODE(completion_path.stat().st_mode) != PRIVATE_FILE_MODE
    ):
        raise ArtifactError("private completion 상태가 다릅니다.")
    return {
        "status": "verified",
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "diagnostic_completed": True,
        "diagnostic_target_met": aggregate["diagnostic_target_met"],
        "semantics": "not_measured",
        "naturalness": "not_measured",
        "sealed_blind_accessed": False,
        "runtime_release_approved": False,
        "application_binding_performed": False,
        "promotion_allowed": False,
    }
