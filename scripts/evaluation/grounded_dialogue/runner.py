# runner.py - 진단 plan, GPU preflight, arm 단위 재개, 실행 순서를 관리한다.

from __future__ import annotations

import fcntl
import os
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .backends import CandidateCalculator, TransformersModelRunner
from .cases import load_cases
from .contracts import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    jsonl_bytes,
    load_json,
    pretty_json_bytes,
    read_jsonl,
    safe_path,
    write_once,
)
from .errors import ArtifactError, GroundedDialogueError
from .harness import ArmConfig, rule_harness_gate, run_arm
from .reporting import build_aggregate, verify, write_reports


def confirmation(config: Mapping[str, Any]) -> None:
    generation = config["generation"]
    if os.environ.get(generation["confirmation_variable"]) != generation[
        "confirmation_value"
    ]:
        raise GroundedDialogueError(
            f"실행 확인값이 없습니다: {generation['confirmation_variable']}="
            f"{generation['confirmation_value']}"
        )


def _gpu_preflight(config: Mapping[str, Any]) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise GroundedDialogueError("GPU compute process를 확인할 수 없습니다.")
    try:
        active = {
            int(line.strip())
            for line in completed.stdout.splitlines()
            if line.strip() and line.strip() not in {"N/A", "[N/A]"}
        }
    except ValueError as exc:
        raise GroundedDialogueError("GPU compute process PID 형식이 다릅니다.") from exc
    others = sorted(active - {os.getpid()})
    if others:
        raise GroundedDialogueError(f"다른 GPU compute process가 있습니다: {others}")
    memory = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        free_values = [int(value.strip()) for value in memory.stdout.splitlines() if value.strip()]
    except ValueError as exc:
        raise GroundedDialogueError("GPU free memory 형식이 다릅니다.") from exc
    if memory.returncode != 0 or len(free_values) != config["generation"]["expected_gpu_count"]:
        raise GroundedDialogueError("GPU 수·메모리를 확인할 수 없습니다.")
    minimum = int(config["generation"]["minimum_free_gpu_memory_mib"])
    if free_values[0] < minimum:
        raise GroundedDialogueError(
            f"GPU free memory가 부족합니다: {free_values[0]} < {minimum} MiB"
        )
    return {"gpu_count": len(free_values), "gpu_free_memory_mib": free_values[0]}


def plan(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    cases = load_cases(context["config"], repo_root)
    gate = rule_harness_gate(cases)
    return {
        "status": "ready_not_executed",
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "cases": len(cases),
        "arms": len(context["config"]["arms"]),
        "response_generations": 500,
        "model_narrow_extractions": context["config"]["source_suite"]["user_turns"],
        "rule_harness_gate": gate,
        "models": ["K0", "KI20"],
        "gpu_execution_performed": False,
        "writes_performed": False,
        "sealed_blind_accessed": False,
        "candidate_runtime_release_approved": False,
    }


def _load_arm_rows(
    path: Path, arm: ArmConfig, cases: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = read_jsonl(path, f"{arm.arm_id} private rows")
    expected_ids = [case["case_id"] for case in cases]
    if (
        len(rows) != 100
        or [row.get("case_id") for row in rows] != expected_ids
        or any(row.get("arm_id") != arm.arm_id for row in rows)
    ):
        raise ArtifactError(f"기존 arm 산출물이 불완전합니다: {arm.arm_id}")
    return rows


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
        raise GroundedDialogueError("같은 build의 진단이 이미 실행 중입니다.") from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def execute(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    config = context["config"]
    confirmation(config)
    with _execution_lock(context["private_root"] / "execution.lock"):
        if (context["private_root"] / "completed.json").exists():
            return verify(context)
        cases = load_cases(config, repo_root)
        rule_gate = rule_harness_gate(cases)
        arms = [ArmConfig.from_mapping(value) for value in config["arms"]]
        missing_arms = [
            arm
            for arm in arms
            if not (context["private_root"] / "arms" / f"{arm.arm_id}.jsonl").exists()
        ]
        gpu = (
            _gpu_preflight(config)
            if missing_arms
            else {"status": "not_required_all_arm_files_present"}
        )
        system_prompt = safe_path(repo_root, config["system_prompt"]["path"]).read_text(
            encoding="utf-8"
        )
        metadata_path = context["private_root"] / "run_metadata.json"
        if metadata_path.exists():
            metadata = load_json(metadata_path, "existing run metadata")
            if (
                metadata.get("evaluation_build_id") != context["evaluation_build_id"]
                or metadata.get("build_sha256") != context["build_sha256"]
                or metadata.get("status") != "started"
                or metadata.get("sealed_blind_accessed") is not False
            ):
                raise ArtifactError("기존 run metadata identity·상태가 다릅니다.")
        else:
            write_once(
                metadata_path,
                pretty_json_bytes(
                    {
                        "schema_version": "0.1.0",
                        "evaluation_build_id": context["evaluation_build_id"],
                        "build_sha256": context["build_sha256"],
                        "status": "started",
                        "gpu_contract": gpu,
                        "sealed_blind_accessed": False,
                    }
                ),
                mode=PRIVATE_FILE_MODE,
            )
        calculator: CandidateCalculator | None = None
        if missing_arms:
            ephemeris = safe_path(
                repo_root, config["runtime_inputs"]["ephemeris"]["path"]
            )
            calculator = CandidateCalculator(ephemeris)
        rows_by_arm: dict[str, list[dict[str, Any]]] = {}
        try:
            for model_id in ("KI20", "K0"):
                pending = [arm for arm in arms if arm.model_id == model_id]
                missing = [
                    arm
                    for arm in pending
                    if not (context["private_root"] / "arms" / f"{arm.arm_id}.jsonl").exists()
                ]
                runner: TransformersModelRunner | None = None
                if missing:
                    runner = TransformersModelRunner(
                        safe_path(repo_root, config["models"][model_id]["path"]),
                        config["generation"],
                    )
                try:
                    for arm in pending:
                        path = context["private_root"] / "arms" / f"{arm.arm_id}.jsonl"
                        if path.exists():
                            rows = _load_arm_rows(path, arm, cases)
                        else:
                            if runner is None or calculator is None:
                                raise GroundedDialogueError(
                                    "미완료 arm에 model·calculator backend가 없습니다."
                                )
                            rows = run_arm(
                                arm,
                                cases,
                                model=runner,
                                calculator=calculator,
                                config=config,
                                system_prompt=system_prompt,
                            )
                            write_once(path, jsonl_bytes(rows), mode=PRIVATE_FILE_MODE)
                        rows_by_arm[arm.arm_id] = rows
                finally:
                    if runner is not None:
                        runner.close()
        finally:
            if calculator is not None:
                calculator.close()
        if set(rows_by_arm) != {arm["arm_id"] for arm in config["arms"]}:
            raise GroundedDialogueError("완료된 arm 집합이 계약과 다릅니다.")
        aggregate = build_aggregate(context, rows_by_arm, rule_gate)
        write_reports(context, aggregate, rows_by_arm)
        return verify(context)
