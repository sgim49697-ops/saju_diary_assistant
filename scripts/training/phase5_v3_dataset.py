# phase5_v3_dataset.py - tools를 보존한 v3 projection을 로드하고 정확한 target mask를 검증한다.

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.saju_contract import (
    SajuContractError,
    validate_tool_arguments,
)

TOOL_BLOCK_PATTERN = re.compile(
    r"<tool_call>\n<function=(?P<name>[^>\n]+)>\n"
    r"(?P<body>.*?)</function>\n</tool_call>",
    re.DOTALL,
)
PARAMETER_PATTERN = re.compile(
    r"<parameter=(?P<name>[^>\n]+)>\n"
    r"(?P<value>.*?)\n</parameter>\n",
    re.DOTALL,
)


class Phase5V3DatasetError(RuntimeError):
    """v3 training projection·token/mask·tool parser 계약 위반."""


def read_training_projection(build_root: Path) -> list[dict[str, Any]]:
    path = build_root / "training" / "training_mix20k_v3.0.1_candidate.jsonl"
    if path.is_symlink() or not path.is_file():
        raise Phase5V3DatasetError(f"v3 training projection이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase5V3DatasetError(
                        f"training projection {line_number}행이 비었습니다."
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase5V3DatasetError(
                        f"training projection {line_number}행은 object여야 합니다."
                    )
                _validate_projection_row(value, line_number)
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Phase5V3DatasetError):
            raise
        raise Phase5V3DatasetError(
            f"v3 training projection을 읽을 수 없습니다: {path}"
        ) from exc
    if len(rows) != 20_000 or len({row["id"] for row in rows}) != 20_000:
        raise Phase5V3DatasetError(
            "v3 training projection은 고유 ID 20,000행이어야 합니다."
        )
    return rows


def _validate_projection_row(row: Mapping[str, Any], line_number: int) -> None:
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
    }
    if set(row) != required or row.get("schema_version") != "3.0.1":
        raise Phase5V3DatasetError(
            f"training projection field 집합이 다릅니다: {line_number}"
        )
    messages = row["messages"]
    if (
        not isinstance(messages, list)
        or len(messages) < 3
        or messages[0].get("role") != "system"
        or messages[-1].get("role") != "assistant"
        or row["target_assistant_message_index"] != len(messages) - 1
        or row["assistant_target_policy"] != "last_user_suffix"
    ):
        raise Phase5V3DatasetError(
            f"training target/message 경계가 다릅니다: {line_number}"
        )
    if not isinstance(row["tools"], list):
        raise Phase5V3DatasetError(f"tools가 list가 아닙니다: {line_number}")
    if not isinstance(row["training_blockers"], list):
        raise Phase5V3DatasetError(
            f"training blockers가 list가 아닙니다: {line_number}"
        )
    if row["train_candidate"] != (not row["training_blockers"]):
        raise Phase5V3DatasetError(
            f"train_candidate와 blocker가 충돌합니다: {line_number}"
        )
    declared = {tool.get("function", {}).get("name") for tool in row["tools"]}
    for message in messages:
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            if function.get("name") not in declared:
                raise Phase5V3DatasetError(
                    f"선언되지 않은 tool call입니다: {line_number}"
                )
            try:
                validate_tool_arguments(function["name"], function["arguments"])
            except (KeyError, SajuContractError) as exc:
                raise Phase5V3DatasetError(
                    f"tool argument가 strict runtime 계약과 다릅니다: {line_number}"
                ) from exc


def load_training_rows(
    build_root: Path, *, eligible_only: bool = True
) -> list[dict[str, Any]]:
    """SFT 소비 shape를 반환하며 tools를 절대 버리지 않는다."""
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


def _decode_argument(raw: str, original: Any) -> Any:
    if isinstance(original, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Phase5V3DatasetError(
            f"tool parameter JSON 값을 복원할 수 없습니다: {raw!r}"
        ) from exc


def parse_kanana_tool_output(
    output: str,
    expected_calls: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Kanana/Qwen3-coder XML 출력에서 function과 typed arguments를 복원한다."""
    matches = list(TOOL_BLOCK_PATTERN.finditer(output))
    if len(matches) != len(expected_calls):
        raise Phase5V3DatasetError(
            f"tool block 수가 다릅니다: {len(matches)} != {len(expected_calls)}"
        )
    if matches:
        suffix = output[matches[-1].end() :]
        if suffix.strip():
            raise Phase5V3DatasetError("tool call 뒤 suffix text를 허용하지 않습니다.")
    parsed: list[dict[str, Any]] = []
    for match, expected in zip(matches, expected_calls, strict=True):
        expected_function = expected["function"]
        if match.group("name") != expected_function["name"]:
            raise Phase5V3DatasetError("tool function name round-trip이 다릅니다.")
        body = match.group("body")
        parameters = list(PARAMETER_PATTERN.finditer(body))
        consumed = "".join(parameter.group(0) for parameter in parameters)
        if consumed != body:
            raise Phase5V3DatasetError(
                "tool parameter XML 사이에 잔여 text가 있습니다."
            )
        expected_arguments = expected_function["arguments"]
        arguments: dict[str, Any] = {}
        for parameter in parameters:
            name = parameter.group("name")
            if name not in expected_arguments or name in arguments:
                raise Phase5V3DatasetError("tool parameter가 누락·중복·추가됐습니다.")
            arguments[name] = _decode_argument(
                parameter.group("value"), expected_arguments[name]
            )
        if arguments != expected_arguments:
            raise Phase5V3DatasetError("tool arguments round-trip이 다릅니다.")
        parsed.append(
            {
                "type": "function",
                "function": {
                    "name": expected_function["name"],
                    "arguments": arguments,
                },
            }
        )
    return parsed


def _render_assistant_output(tokenizer: Any, message: Mapping[str, Any]) -> str:
    fixture_messages = [
        {"role": "system", "content": ""},
        {"role": "user", "content": "도구를 호출하세요."},
        dict(message),
    ]
    rendered = tokenizer.apply_chat_template(
        fixture_messages,
        tools=None,
        tokenize=False,
        add_generation_prompt=False,
    )
    marker = "<|im_start|>assistant\n"
    if marker not in rendered:
        raise Phase5V3DatasetError("assistant serialization marker가 없습니다.")
    output = rendered.rsplit(marker, 1)[1]
    if not output.endswith("<|im_end|>\n"):
        raise Phase5V3DatasetError("assistant serialization EOS가 다릅니다.")
    return output[: -len("<|im_end|>\n")]


def verify_tool_roundtrip(tokenizer: Any, row: Mapping[str, Any]) -> int:
    messages = row["messages"]
    last_user = max(
        index for index, message in enumerate(messages) if message.get("role") == "user"
    )
    calls_checked = 0
    for message in messages[last_user + 1 :]:
        calls = message.get("tool_calls", [])
        if not calls:
            continue
        output = _render_assistant_output(tokenizer, message)
        parsed = parse_kanana_tool_output(output, calls)
        if parsed != calls:
            raise Phase5V3DatasetError("structured tool call round-trip이 다릅니다.")
        calls_checked += len(calls)
    return calls_checked


def tokenize_training_row(
    tokenizer: Any,
    row: Mapping[str, Any],
    *,
    max_length: int,
) -> dict[str, Any]:
    tools = row["tools"] or None
    try:
        processed = tokenizer.apply_chat_template(
            row["messages"],
            tools=tools,
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            add_generation_prompt=False,
        )
        rendered = tokenizer.apply_chat_template(
            row["messages"],
            tools=tools,
            tokenize=False,
            add_generation_prompt=False,
        )
        direct_ids = tokenizer(rendered, add_special_tokens=False)["input_ids"]
        last_user = max(
            index
            for index, message in enumerate(row["messages"])
            if message.get("role") == "user"
        )
        prefix_ids = tokenizer.apply_chat_template(
            row["messages"][: last_user + 1],
            tools=tools,
            tokenize=True,
            add_generation_prompt=False,
        )
    except Exception as exc:
        raise Phase5V3DatasetError(
            f"chat template tokenization이 실패했습니다: {row.get('id')}"
        ) from exc
    if not isinstance(processed, Mapping):
        raise Phase5V3DatasetError(
            "Transformers BatchEncoding을 Mapping으로 받지 못했습니다."
        )
    input_ids = processed.get("input_ids")
    assistant_masks = processed.get("assistant_masks")
    attention_mask = processed.get("attention_mask")
    if (
        not isinstance(input_ids, list)
        or not isinstance(assistant_masks, list)
        or not isinstance(attention_mask, list)
        or input_ids != direct_ids
        or len(input_ids) != len(assistant_masks)
        or len(input_ids) != len(attention_mask)
        or not isinstance(prefix_ids, list)
        or input_ids[: len(prefix_ids)] != prefix_ids
        or any(value not in {0, 1} for value in assistant_masks)
        or any(value != 1 for value in attention_mask)
    ):
        raise Phase5V3DatasetError(
            f"token/mask/serialization 계약이 다릅니다: {row.get('id')}"
        )
    assistant_tokens = sum(assistant_masks)
    if assistant_tokens <= 0:
        raise Phase5V3DatasetError(
            f"assistant target mask가 비었습니다: {row.get('id')}"
        )
    if not any(
        token_id == tokenizer.eos_token_id and mask == 1
        for token_id, mask in zip(input_ids, assistant_masks, strict=True)
    ):
        raise Phase5V3DatasetError(
            f"supervised assistant EOS가 없습니다: {row.get('id')}"
        )
    if any(assistant_masks[: len(prefix_ids)]):
        raise Phase5V3DatasetError(
            f"마지막 사용자 이전 assistant가 supervised됐습니다: {row.get('id')}"
        )
    eos_positions = [
        index for index, token_id in enumerate(input_ids) if token_id == tokenizer.eos_token_id
    ]
    if not eos_positions or assistant_masks[eos_positions[-1]] != 1:
        raise Phase5V3DatasetError(
            f"마지막 assistant EOS가 supervised되지 않았습니다: {row.get('id')}"
        )
    supervised_text = tokenizer.decode(
        [
            token_id
            for token_id, mask in zip(input_ids, assistant_masks, strict=True)
            if mask
        ],
        skip_special_tokens=False,
    )
    if "<tool_response>" in supervised_text:
        raise Phase5V3DatasetError(
            f"tool response가 loss mask에 포함됐습니다: {row.get('id')}"
        )
    calls_checked = verify_tool_roundtrip(tokenizer, row)
    if calls_checked and supervised_text.count("<tool_call>") < calls_checked:
        raise Phase5V3DatasetError(
            f"tool call body가 assistant loss mask 밖에 있습니다: {row.get('id')}"
        )
    return {
        "id": row["id"],
        "task_axis": row["task_axis"],
        "total_tokens": len(input_ids),
        "assistant_tokens": assistant_tokens,
        "input_tokens": len(input_ids) - assistant_tokens,
        "over_max_length": len(input_ids) > max_length,
        "tool_calls_roundtripped": calls_checked,
        "pre_last_user_supervised_tokens": 0,
        "final_assistant_eos_supervised": True,
        "has_tools": bool(row["tools"]),
        "train_candidate": row["train_candidate"],
        "restricted_local_only": row["restricted_local_only"],
    }
