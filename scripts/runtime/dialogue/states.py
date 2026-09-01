# states.py - 세션 상태와 실행기 행동을 고정 어휘로 정의한다.

from __future__ import annotations

from enum import Enum

POLICY_SCHEMA_VERSION = "saju-dialogue-policy-v0.1.0"

# 계산기 호출에 필요한 필수 슬롯. saju-tools-v1의 required와 정렬한다.
REQUIRED_SLOTS = ("birth_date", "calendar", "birthplace")
# 없어도 계산은 가능하지만 확정도가 달라지는 슬롯.
OPTIONAL_SLOTS = ("birth_time", "time_precision", "gender_for_daeun")


class DialogueState(str, Enum):
    """슬롯과 chart 보유 여부만으로 결정되는 상태. 모델 출력은 상태를 바꾸지 않는다."""

    NO_BIRTH_INPUT = "no_birth_input"
    PARTIAL_BIRTH_INPUT = "partial_birth_input"
    BIRTH_INPUT_READY = "birth_input_ready"
    CHART_READY = "chart_ready"
    CHART_INVALIDATED = "chart_invalidated"


class ExecutorAction(str, Enum):
    """실행기가 정하는 다음 행동. 모든 행동은 결국 모델이 문장을 생성하며 끝난다."""

    MODEL_FREE_REPLY = "model_free_reply"
    MODEL_ASK_MISSING_SLOT = "model_ask_missing_slot"
    CALL_CALCULATOR = "call_calculator"
    MODEL_GROUNDED_REPLY = "model_grounded_reply"
    MODEL_LIMITED_REPLY = "model_limited_reply"
