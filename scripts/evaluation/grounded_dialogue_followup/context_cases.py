# context_cases.py - 비봉인 100건에 중립적인 완전 대화쌍을 붙여 고정 token band를 만든다.

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from scripts.evaluation.grounded_dialogue.errors import GroundedDialogueError

USER_FILLER = (
    "오늘의 일반 기록을 차분하게 이어서 정리하고 싶습니다. "
    "앞에서 나눈 흐름을 유지하며 핵심만 간결하게 연결해 주세요. "
)
ASSISTANT_FILLER = (
    "알겠습니다. 앞선 일반 기록의 흐름을 유지하면서 현재 요청에 필요한 내용만 "
    "차분하고 간결하게 이어가겠습니다. "
)
PAIR_LABELS = (
    "첫째",
    "둘째",
    "셋째",
    "넷째",
    "다섯째",
    "여섯째",
    "일곱째",
    "여덟째",
)


@dataclass(frozen=True)
class ContextCase:
    """원본 case와 공개 합성 history, token band identity를 묶는다."""

    context_case_id: str
    source_case: Mapping[str, Any]
    band_id: str
    minimum_tokens: int
    maximum_tokens: int
    base_input_tokens: int
    original_input_tokens: int
    history_messages: tuple[dict[str, str], ...]

    def full_messages(self, system_content: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_content},
            *deepcopy(list(self.history_messages)),
            *deepcopy(list(self.source_case["messages"])),
        ]

    def suite_row(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1.0",
            "context_case_id": self.context_case_id,
            "source_case_id": self.source_case["case_id"],
            "stratum": self.source_case["stratum"],
            "band_id": self.band_id,
            "base_input_tokens": self.base_input_tokens,
            "original_input_tokens": self.original_input_tokens,
            "history_messages": deepcopy(list(self.history_messages)),
            "provenance": {
                "kind": "public_synthetic",
                "contains_restricted_source": False,
                "contains_personal_data": False,
                "training_eligible": False,
            },
        }


def _pair(index: int, repeats: int) -> list[dict[str, str]]:
    label = PAIR_LABELS[index % len(PAIR_LABELS)]
    return [
        {"role": "user", "content": f"{label} 기록입니다. " + USER_FILLER * repeats},
        {
            "role": "assistant",
            "content": f"{label} 흐름을 확인했습니다. " + ASSISTANT_FILLER * repeats,
        },
    ]


def _validate_history(messages: Sequence[Mapping[str, str]], denylist: Sequence[str]) -> None:
    if len(messages) % 2:
        raise GroundedDialogueError("장문 history가 완전한 대화쌍이 아닙니다.")
    for index in range(0, len(messages), 2):
        user, assistant = messages[index : index + 2]
        if user.get("role") != "user" or assistant.get("role") != "assistant":
            raise GroundedDialogueError("장문 history role 순서가 다릅니다.")
    joined = "\n".join(str(message.get("content", "")) for message in messages)
    if any(value.casefold() in joined.casefold() for value in denylist):
        raise GroundedDialogueError("장문 history가 lexical denylist를 위반했습니다.")


def _build_history(
    system_content: str,
    source_messages: Sequence[Mapping[str, str]],
    *,
    minimum_tokens: int,
    maximum_tokens: int,
    token_counter: Callable[[Sequence[Mapping[str, str]]], int],
    denylist: Sequence[str],
) -> tuple[tuple[dict[str, str], ...], int, int]:
    base_messages = [{"role": "system", "content": system_content}, *source_messages]
    base_tokens = int(token_counter(base_messages))
    if base_tokens > maximum_tokens:
        raise GroundedDialogueError(
            f"원본 prompt가 지정 band 상한을 넘습니다: {base_tokens}>{maximum_tokens}"
        )
    history: list[dict[str, str]] = []
    current = base_tokens
    pair_index = 0
    while current < minimum_tokens:
        candidates: list[tuple[int, list[dict[str, str]]]] = []
        for repeats in range(1, 17):
            pair = _pair(pair_index, repeats)
            messages = [
                {"role": "system", "content": system_content},
                *history,
                *pair,
                *source_messages,
            ]
            tokens = int(token_counter(messages))
            if current < tokens <= maximum_tokens and tokens - current <= 260:
                candidates.append((tokens, pair))
        if not candidates:
            raise GroundedDialogueError(
                f"장문 history로 token band를 구성하지 못했습니다: {current}, "
                f"{minimum_tokens}~{maximum_tokens}"
            )
        below = [item for item in candidates if item[0] <= minimum_tokens]
        selected = max(below or candidates, key=lambda item: item[0])
        current, pair = selected
        history.extend(pair)
        pair_index += 1
        if pair_index > 64:
            raise GroundedDialogueError("장문 history pair 상한을 넘었습니다.")
    _validate_history(history, denylist)
    return tuple(history), base_tokens, current


def build_context_cases(
    base_cases: Sequence[Mapping[str, Any]],
    system_contents: Mapping[str, str],
    *,
    bands: Sequence[Mapping[str, Any]],
    token_counter: Callable[[Sequence[Mapping[str, str]]], int],
    denylist: Sequence[str],
) -> list[ContextCase]:
    if len(base_cases) != 100 or len(bands) != 4:
        raise GroundedDialogueError("장문 suite 입력 수가 고정 계약과 다릅니다.")
    values: list[ContextCase] = []
    counts: Counter[str] = Counter()
    for ordinal, source_case in enumerate(base_cases):
        band = bands[ordinal % len(bands)]
        source_id = str(source_case["case_id"])
        system_content = system_contents.get(source_id)
        if not isinstance(system_content, str) or not system_content:
            raise GroundedDialogueError("장문 case system content가 없습니다.")
        history, base_tokens, original_tokens = _build_history(
            system_content,
            source_case["messages"],
            minimum_tokens=int(band["minimum_tokens"]),
            maximum_tokens=int(band["maximum_tokens"]),
            token_counter=token_counter,
            denylist=denylist,
        )
        if base_tokens > 2048:
            raise GroundedDialogueError("history 제거 후 최소 prompt가 2,048을 넘습니다.")
        band_id = str(band["band_id"])
        values.append(
            ContextCase(
                context_case_id=f"context-v1-{ordinal:03d}",
                source_case=source_case,
                band_id=band_id,
                minimum_tokens=int(band["minimum_tokens"]),
                maximum_tokens=int(band["maximum_tokens"]),
                base_input_tokens=base_tokens,
                original_input_tokens=original_tokens,
                history_messages=history,
            )
        )
        counts[band_id] += 1
    expected = Counter({str(band["band_id"]): int(band["cases"]) for band in bands})
    if counts != expected or len({value.context_case_id for value in values}) != 100:
        raise GroundedDialogueError("장문 suite band·identity 분포가 다릅니다.")
    return values


__all__ = ["ContextCase", "build_context_cases"]
