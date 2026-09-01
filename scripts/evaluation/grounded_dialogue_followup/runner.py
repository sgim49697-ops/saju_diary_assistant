# runner.py - 후속 재채점, 장문 plan, GPU 실행, arm 단위 재개를 관리한다.

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.evaluation.grounded_dialogue.backends import (
    CandidateCalculator,
    TransformersModelRunner,
)
from scripts.evaluation.grounded_dialogue.cases import load_cases
from scripts.evaluation.grounded_dialogue.contracts import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    jsonl_bytes,
    load_json,
    pretty_json_bytes,
    read_jsonl,
    safe_path,
    sha256_file,
    write_once,
)
from scripts.evaluation.grounded_dialogue.errors import (
    ArtifactError,
    GroundedDialogueError,
)
from scripts.evaluation.grounded_dialogue.harness import ArmConfig

from .graders import grade_response
from .harness import TokenizerOnlyRunner, prepare_context_suite, run_context_arm
from .reporting import (
    build_context_aggregate,
    build_rescore_aggregate,
    verify_context,
    verify_rescore,
    write_context_reports,
    write_rescore_reports,
)


def rescore_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": "ready_not_executed",
        "rescore_build_id": context["rescore_build_id"],
        "build_sha256": context["rescore_build_sha256"],
        "parent_evaluation_build_id": context["config"]["parent_evaluation"][
            "evaluation_build_id"
        ],
        "rows": 500,
        "response_regenerated": False,
        "writes_performed": False,
        "sealed_blind_accessed": False,
    }


def _load_parent_rows(context: Mapping[str, Any], repo_root: Path) -> dict[str, list[dict[str, Any]]]:
    values: dict[str, list[dict[str, Any]]] = {}
    for arm_id, item in context["config"]["parent_evaluation"]["arm_files"].items():
        path = safe_path(repo_root, item["path"])
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != item["sha256"]
            or stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE
        ):
            raise ArtifactError(f"부모 arm 파일이 변경됐습니다: {arm_id}")
        rows = read_jsonl(path, f"parent {arm_id}")
        if len(rows) != 100 or any(row.get("arm_id") != arm_id for row in rows):
            raise ArtifactError(f"부모 arm identity가 다릅니다: {arm_id}")
        values[arm_id] = rows
    return values


def execute_rescore(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (context["rescore_public_root"] / "aggregate.json").exists():
        return verify_rescore(context)
    parent_rows = _load_parent_rows(context, repo_root)
    rescored: dict[str, list[dict[str, Any]]] = {}
    for arm_id, rows in parent_rows.items():
        updated: list[dict[str, Any]] = []
        for row in rows:
            value = deepcopy(row)
            tool_result = value["tool_result"]
            value["response_grade"] = grade_response(
                value["generation"]["output"],
                hard_facts=tool_result.get("hard_facts"),
                tool_status=tool_result["status"],
                session_state=value["grading_session_state"],
                decision_action=value["fsm"]["decision_action"],
                max_token_hit=value["generation"]["max_token_hit"],
            )
            updated.append(value)
        rescored[arm_id] = updated
    parent_aggregate = load_json(
        safe_path(
            repo_root,
            context["config"]["parent_evaluation"]["public_aggregate"]["path"],
        ),
        "parent public aggregate",
    )
    aggregate = build_rescore_aggregate(context, rescored, parent_aggregate)
    r1 = aggregate["comparison_to_parent"]["R1_KI20_ORACLE_2048"]
    r3 = aggregate["comparison_to_parent"]["R3_KI20_RULE_2048"]
    if (
        r1["provided_field_reask_percent_before"] != 7.0
        or r1["provided_field_reask_percent_after"] != 2.0
        or r3["provided_field_reask_percent_before"] != 7.0
        or r3["provided_field_reask_percent_after"] != 2.0
        or r1["target_after"] is not True
        or r3["target_after"] is not True
    ):
        raise GroundedDialogueError("R1·R3 재채점 회귀 기대값이 다릅니다.")
    write_rescore_reports(context, aggregate)
    return verify_rescore(context)


def _load_parent_config(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    return load_json(
        safe_path(repo_root, context["config"]["parent_evaluation"]["config"]["path"]),
        "parent grounded dialogue config",
    )


def _prepare_suite(
    context: Mapping[str, Any],
    repo_root: Path,
    *,
    model: Any,
    calculator: CandidateCalculator,
) -> tuple[list[Any], dict[str, dict[str, Any]], dict[str, str]]:
    parent_config = _load_parent_config(context, repo_root)
    cases = load_cases(parent_config, repo_root)
    prompt = safe_path(repo_root, parent_config["system_prompt"]["path"]).read_text(
        encoding="utf-8"
    )
    return prepare_context_suite(
        cases,
        model=model,
        calculator=calculator,
        parent_config=parent_config,
        followup_config=context["config"],
        system_prompt=prompt,
    )


def context_plan(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    parent_config = _load_parent_config(context, repo_root)
    model = TokenizerOnlyRunner(
        safe_path(repo_root, parent_config["models"]["KI20"]["path"]),
        context["config"]["context_diagnostic"]["generation"],
    )
    calculator = CandidateCalculator(
        safe_path(repo_root, parent_config["runtime_inputs"]["ephemeris"]["path"])
    )
    try:
        cases, _, _ = _prepare_suite(context, repo_root, model=model, calculator=calculator)
    finally:
        calculator.close()
    counts = Counter(value.band_id for value in cases)
    return {
        "status": "ready_not_executed",
        "context_build_id": context["context_build_id"],
        "build_sha256": context["context_build_sha256"],
        "cases": len(cases),
        "arms": 2,
        "response_generations": 200,
        "band_cases": dict(sorted(counts.items())),
        "base_input_tokens_maximum": max(value.base_input_tokens for value in cases),
        "original_input_tokens_minimum": min(value.original_input_tokens for value in cases),
        "original_input_tokens_maximum": max(value.original_input_tokens for value in cases),
        "gpu_execution_performed": False,
        "writes_performed": False,
        "sealed_blind_accessed": False,
    }


def confirmation(config: Mapping[str, Any]) -> None:
    generation = config["context_diagnostic"]["generation"]
    if os.environ.get(generation["confirmation_variable"]) != generation[
        "confirmation_value"
    ]:
        raise GroundedDialogueError(
            f"실행 확인값이 없습니다: {generation['confirmation_variable']}="
            f"{generation['confirmation_value']}"
        )


def _gpu_preflight(config: Mapping[str, Any]) -> dict[str, Any]:
    generation = config["context_diagnostic"]["generation"]
    processes = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
    )
    if processes.returncode != 0:
        raise GroundedDialogueError("GPU compute process를 확인할 수 없습니다.")
    try:
        active = {
            int(line.strip())
            for line in processes.stdout.splitlines()
            if line.strip() and line.strip() not in {"N/A", "[N/A]"}
        }
    except ValueError as exc:
        raise GroundedDialogueError("GPU compute PID 형식이 다릅니다.") from exc
    others = sorted(active - {os.getpid()})
    if others:
        raise GroundedDialogueError(f"다른 GPU compute process가 있습니다: {others}")
    memory = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        free = [int(value.strip()) for value in memory.stdout.splitlines() if value.strip()]
    except ValueError as exc:
        raise GroundedDialogueError("GPU free memory 형식이 다릅니다.") from exc
    if memory.returncode != 0 or len(free) != generation["expected_gpu_count"]:
        raise GroundedDialogueError("GPU 수·메모리를 확인할 수 없습니다.")
    if free[0] < generation["minimum_free_gpu_memory_mib"]:
        raise GroundedDialogueError(
            f"GPU free memory가 부족합니다: {free[0]} < "
            f"{generation['minimum_free_gpu_memory_mib']} MiB"
        )
    return {"gpu_count": len(free), "gpu_free_memory_mib": free[0]}


@contextmanager
def _execution_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    path.parent.chmod(PRIVATE_DIR_MODE)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise GroundedDialogueError("같은 장문 build가 이미 실행 중입니다.") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _load_context_arm(path: Path, arm_id: str) -> list[dict[str, Any]]:
    rows = read_jsonl(path, f"context {arm_id}")
    expected_ids = [f"context-v1-{index:03d}" for index in range(100)]
    if (
        len(rows) != 100
        or [row.get("context_case_id") for row in rows] != expected_ids
        or any(row.get("arm_id") != arm_id for row in rows)
    ):
        raise ArtifactError(f"기존 장문 arm 산출물이 불완전합니다: {arm_id}")
    return rows


def execute_context(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    confirmation(context["config"])
    private_root = context["context_private_root"]
    with _execution_lock(private_root / "execution.lock"):
        if (private_root / "completed.json").exists():
            return verify_context(context)
        arms = [
            ArmConfig.from_mapping(value)
            for value in context["config"]["context_diagnostic"]["arms"]
        ]
        missing = [
            arm for arm in arms if not (private_root / "arms" / f"{arm.arm_id}.jsonl").exists()
        ]
        gpu = _gpu_preflight(context["config"]) if missing else {
            "status": "not_required_all_arm_files_present"
        }
        metadata_path = private_root / "run_metadata.json"
        if metadata_path.exists():
            metadata = load_json(metadata_path, "context run metadata")
            if (
                metadata.get("context_build_id") != context["context_build_id"]
                or metadata.get("build_sha256") != context["context_build_sha256"]
                or metadata.get("status") != "started"
                or metadata.get("sealed_blind_accessed") is not False
            ):
                raise ArtifactError("기존 장문 run metadata가 다릅니다.")
        else:
            write_once(
                metadata_path,
                pretty_json_bytes(
                    {
                        "schema_version": "0.1.0",
                        "context_build_id": context["context_build_id"],
                        "build_sha256": context["context_build_sha256"],
                        "status": "started",
                        "gpu_contract": gpu,
                        "sealed_blind_accessed": False,
                    }
                ),
                mode=PRIVATE_FILE_MODE,
            )

        rows_by_arm: dict[str, list[dict[str, Any]]] = {}
        model: TransformersModelRunner | None = None
        calculator: CandidateCalculator | None = None
        try:
            if missing:
                parent_config = _load_parent_config(context, repo_root)
                model = TransformersModelRunner(
                    safe_path(repo_root, parent_config["models"]["KI20"]["path"]),
                    context["config"]["context_diagnostic"]["generation"],
                )
                calculator = CandidateCalculator(
                    safe_path(
                        repo_root,
                        parent_config["runtime_inputs"]["ephemeris"]["path"],
                    )
                )
                cases, records, system_contents = _prepare_suite(
                    context, repo_root, model=model, calculator=calculator
                )
                suite_payload = jsonl_bytes([value.suite_row() for value in cases])
                write_once(private_root / "suite.jsonl", suite_payload, mode=PRIVATE_FILE_MODE)
            else:
                cases = records = system_contents = None
            for arm in arms:
                path = private_root / "arms" / f"{arm.arm_id}.jsonl"
                if path.exists():
                    rows = _load_context_arm(path, arm.arm_id)
                else:
                    if model is None or cases is None or records is None or system_contents is None:
                        raise GroundedDialogueError("미완료 장문 arm backend가 없습니다.")
                    rows = run_context_arm(
                        arm,
                        cases,
                        base_records=records,
                        system_contents=system_contents,
                        model=model,
                        config=context["config"],
                    )
                    write_once(path, jsonl_bytes(rows), mode=PRIVATE_FILE_MODE)
                rows_by_arm[arm.arm_id] = rows
        finally:
            if calculator is not None:
                calculator.close()
            if model is not None:
                model.close()
        if set(rows_by_arm) != {arm.arm_id for arm in arms}:
            raise GroundedDialogueError("완료된 장문 arm 집합이 다릅니다.")
        aggregate = build_context_aggregate(context, rows_by_arm)
        if (
            aggregate["response_generations"] != 200
            or aggregate["prompt_budget_failure_cases"] != 0
            or aggregate["paired_comparison"]["pre_generation_invariant_cases"] != 100
        ):
            raise GroundedDialogueError("장문 진단 완료 조건을 충족하지 못했습니다.")
        write_context_reports(context, aggregate, rows_by_arm)
        return verify_context(context)


__all__ = [
    "confirmation",
    "context_plan",
    "execute_context",
    "execute_rescore",
    "rescore_plan",
]
