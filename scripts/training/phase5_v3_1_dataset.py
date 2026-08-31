# phase5_v3_1_dataset.py - v3.1 projection을 엄격히 로드하고 기존 Kanana mask 검증을 재사용한다.

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.runtime.saju_contract import (
    SajuContractError,
    project_model_visible_tool_result,
    validate_tool_arguments,
)
from scripts.training.phase5_v3_dataset import tokenize_training_row

TRAINING_RELATIVE = "training/training_mix20k_v3.1_runtime_grounded.jsonl"
RELEASE_PATTERN = re.compile(r"^saju-runtime-release-v1\.1\.0-[0-9a-f]{12}$")


class Phase5V31DatasetError(RuntimeError):
    """v3.1 training projection·tool result·release identity 위반."""


def _validate_row(row: Mapping[str, Any], line_number: int) -> None:
    required = {
        "schema_version",
        "id",
        "conversation_id",
        "task_axis",
        "source",
        "fact_authority",
        "promotion_status",
        "messages",
        "tools",
        "target_assistant_message_index",
        "assistant_target_policy",
        "train_candidate",
        "training_blockers",
        "restricted_local_only",
        "runtime_release_id",
        "runtime_fact_source",
    }
    if set(row) != required or row.get("schema_version") != "3.1.0":
        raise Phase5V31DatasetError(
            f"v3.1 training field 집합이 다릅니다: {line_number}"
        )
    if RELEASE_PATTERN.fullmatch(str(row.get("runtime_release_id", ""))) is None:
        raise Phase5V31DatasetError(f"runtime release ID가 다릅니다: {line_number}")
    messages = row.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) < 3
        or not isinstance(messages[0], dict)
        or messages[0].get("role") != "system"
        or not isinstance(messages[-1], dict)
        or messages[-1].get("role") != "assistant"
        or row.get("target_assistant_message_index") != len(messages) - 1
        or row.get("assistant_target_policy") != "last_user_suffix"
        or not isinstance(row.get("tools"), list)
        or not isinstance(row.get("training_blockers"), list)
        or row.get("train_candidate") != (not row["training_blockers"])
    ):
        raise Phase5V31DatasetError(
            f"v3.1 message/target/blocker 계약이 다릅니다: {line_number}"
        )
    declared = {
        tool.get("function", {}).get("name")
        for tool in row["tools"]
        if isinstance(tool, dict)
    }
    pending_calls = 0
    tool_call_count = 0
    for message in messages:
        if not isinstance(message, dict):
            raise Phase5V31DatasetError(f"message가 object가 아닙니다: {line_number}")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list):
            raise Phase5V31DatasetError(f"tool_calls가 list가 아닙니다: {line_number}")
        for call in calls:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name") if isinstance(function, dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if name not in declared or not isinstance(arguments, dict):
                raise Phase5V31DatasetError(
                    f"선언되지 않거나 잘못된 tool call입니다: {line_number}"
                )
            try:
                validate_tool_arguments(name, arguments)
            except SajuContractError as exc:
                raise Phase5V31DatasetError(
                    f"strict tool arguments가 다릅니다: {line_number}"
                ) from exc
            pending_calls += 1
            tool_call_count += 1
        if message.get("role") == "tool":
            if pending_calls < 1 or not isinstance(message.get("content"), str):
                raise Phase5V31DatasetError(
                    f"tool result 순서가 다릅니다: {line_number}"
                )
            try:
                visible = json.loads(message["content"])
                projected = project_model_visible_tool_result(visible)
            except (json.JSONDecodeError, SajuContractError) as exc:
                raise Phase5V31DatasetError(
                    f"model-visible tool result가 다릅니다: {line_number}"
                ) from exc
            if visible != projected:
                raise Phase5V31DatasetError(
                    f"tool result에 내부 field가 섞였습니다: {line_number}"
                )
            pending_calls -= 1
    expected_fact_source = (
        "approved_saju_runtime_v1_1" if tool_call_count else None
    )
    if row.get("runtime_fact_source") != expected_fact_source:
        raise Phase5V31DatasetError(
            f"runtime fact source가 tool call과 다릅니다: {line_number}"
        )


def read_training_projection(build_root: Path) -> list[dict[str, Any]]:
    path = build_root / TRAINING_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise Phase5V31DatasetError(f"v3.1 training projection이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase5V31DatasetError(
                        f"v3.1 training projection {line_number}행이 비었습니다."
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase5V31DatasetError(
                        f"v3.1 training projection {line_number}행은 object여야 합니다."
                    )
                _validate_row(value, line_number)
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Phase5V31DatasetError):
            raise
        raise Phase5V31DatasetError("v3.1 training projection을 읽지 못했습니다.") from exc
    if len(rows) != 20_000 or len({row["id"] for row in rows}) != 20_000:
        raise Phase5V31DatasetError("v3.1 projection은 고유 ID 20,000행이어야 합니다.")
    if len({row["runtime_release_id"] for row in rows}) != 1:
        raise Phase5V31DatasetError("v3.1 projection에 runtime release가 섞였습니다.")
    return rows


def load_training_rows(
    build_root: Path, *, eligible_only: bool = True
) -> list[dict[str, Any]]:
    rows = read_training_projection(build_root)
    if eligible_only:
        rows = [row for row in rows if row["train_candidate"]]
    return [
        {
            "id": row["id"],
            "task_axis": row["task_axis"],
            "messages": row["messages"],
            "tools": row["tools"],
            "target_assistant_message_index": row["target_assistant_message_index"],
            "assistant_target_policy": row["assistant_target_policy"],
        }
        for row in rows
    ]


__all__ = [
    "Phase5V31DatasetError",
    "load_training_rows",
    "read_training_projection",
    "tokenize_training_row",
]
