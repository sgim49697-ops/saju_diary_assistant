# policy.py - 세션 상태에서 다음 행동을 결정론적으로 고른다. 모델은 관여하지 않는다.
#
# 설계 원칙
#   1. 정책 루프는 "무슨 말을 할지"를 정하지 않는다. "지금 어떤 사실이 확정됐는지"만 정한다.
#   2. 계산기 호출 여부를 모델에게 맡기지 않는다. 슬롯이 차면 실행기가 부른다.
#   3. 같은 상태 입력에는 항상 같은 행동이 나온다. 전이 표는 전역(total)이어야 한다.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import DialoguePolicyError
from .states import (
    OPTIONAL_SLOTS,
    REQUIRED_SLOTS,
    DialogueState,
    ExecutorAction,
)


@dataclass(frozen=True)
class Decision:
    """실행기 한 턴의 결정. constraint_id는 프롬프트 조립기가 해석한다."""

    action: ExecutorAction
    state: DialogueState
    missing_slot: str | None = None
    inject_hard_facts: bool = False
    constraint_id: str = "none"
    reasons: tuple[str, ...] = field(default_factory=tuple)


class SlotExtractor(Protocol):
    """사용자 발화에서 출생 슬롯을 뽑는다. 구현체를 바꿔 실험 변수로 쓴다.

    두 후보를 비교 대상으로 둔다.
      - rule        : 정규식·규칙 기반. 결정론적이지만 표현 다양성에 약하다.
      - model_narrow: 모델에게 "이 발화에 출생정보가 있으면 뽑아라"만 시킨다.
                      도구 호출 시점 판단보다 훨씬 좁은 과제라 1.3B에도 부담이 적다.
    """

    def extract(self, utterance: str, state: Mapping[str, Any]) -> dict[str, Any]:
        ...


def classify_state(session_state: Mapping[str, Any]) -> DialogueState:
    """슬롯과 chart 보유 여부만 보고 상태를 판정한다."""
    if not isinstance(session_state, Mapping):
        raise DialoguePolicyError("session_state가 Mapping이 아닙니다.")

    slots = session_state.get("birth_slots")
    if not isinstance(slots, Mapping):
        raise DialoguePolicyError("session_state.birth_slots가 없습니다.")

    if session_state.get("chart_invalidated") is True:
        return DialogueState.CHART_INVALIDATED
    if session_state.get("hard_facts") is not None:
        return DialogueState.CHART_READY

    filled = [name for name in REQUIRED_SLOTS if slots.get(name) not in (None, "")]
    if not filled:
        return DialogueState.NO_BIRTH_INPUT
    if len(filled) < len(REQUIRED_SLOTS):
        return DialogueState.PARTIAL_BIRTH_INPUT
    return DialogueState.BIRTH_INPUT_READY


def first_missing_slot(session_state: Mapping[str, Any]) -> str | None:
    slots = session_state["birth_slots"]
    for name in (*REQUIRED_SLOTS, *OPTIONAL_SLOTS):
        if slots.get(name) in (None, ""):
            return name
    return None


def decide(
    session_state: Mapping[str, Any],
    *,
    saju_intent: bool,
    last_tool_status: str | None = None,
) -> Decision:
    """다음 행동 하나를 고른다.

    saju_intent는 "사용자가 사주 얘기를 원하는가"이며, 분류기 또는 규칙이 채운다.
    이 값이 False면 슬롯이 다 차 있어도 계산기를 부르지 않는다. 사주를 강요하지 않기
    위한 장치이고, production_system_prompt_v1의 '사주 강요 금지'와 같은 계약이다.
    """
    state = classify_state(session_state)

    if last_tool_status in {"error", "blocked", "partial"}:
        return Decision(
            action=ExecutorAction.MODEL_LIMITED_REPLY,
            state=state,
            inject_hard_facts=state is DialogueState.CHART_READY,
            constraint_id="limited_reply_v1",
            reasons=(f"tool_status={last_tool_status}",),
        )

    if not saju_intent:
        return Decision(
            action=ExecutorAction.MODEL_FREE_REPLY,
            state=state,
            constraint_id="free_reply_v1",
            reasons=("saju_intent=false",),
        )

    if state is DialogueState.CHART_READY:
        return Decision(
            action=ExecutorAction.MODEL_GROUNDED_REPLY,
            state=state,
            inject_hard_facts=True,
            constraint_id="grounded_reply_v1",
        )

    if state in {DialogueState.BIRTH_INPUT_READY, DialogueState.CHART_INVALIDATED}:
        return Decision(
            action=ExecutorAction.CALL_CALCULATOR,
            state=state,
            constraint_id="none",
            reasons=("required_slots_filled",),
        )

    missing = first_missing_slot(session_state)
    if missing is None:
        raise DialoguePolicyError("슬롯이 비었다고 판정했으나 missing slot이 없습니다.")
    return Decision(
        action=ExecutorAction.MODEL_ASK_MISSING_SLOT,
        state=state,
        missing_slot=missing,
        constraint_id="ask_missing_v1",
        reasons=(f"missing={missing}",),
    )


# TODO(local): 아래는 로컬 보완 지점이다.
#   - build_prompt(decision, session_state, budget, system_prompt_id) -> messages
#     constraint_id별 지침 블록과 hard_facts 직렬화를 예산 안에서 조립한다.
#     hard_facts 투영은 scripts/runtime/calculation/bridge.execute_runtime_tool의
#     model-visible allowlist를 그대로 쓴다. 새 필드를 임의로 노출하지 않는다.
#   - 예산 초과 시 대화 이력을 어느 순서로 버릴지 정책을 정한다(오래된 턴부터).
def build_prompt(*_args: Any, **_kwargs: Any) -> Sequence[dict[str, str]]:
    raise NotImplementedError("build_prompt는 로컬에서 구현한다.")
