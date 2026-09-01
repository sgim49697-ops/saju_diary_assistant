# harness.py - arm 하나를 실행해 원시 생성 결과를 남긴다. 채점은 graders가 한다.
#
# 이 레인은 진단 전용이다. 다음을 절대 하지 않는다.
#   - KI20 weight·run manifest·checkpoint 수정
#   - sealed blind 접근
#   - runtime Gate·release·앱 연결 상태 변경
# 계산기는 승인 전 후보 상태로도 충분하다. 여기서 재는 것은 계산기가 아니라 모델이다.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from scripts.runtime.dialogue.policy import Decision
from scripts.runtime.dialogue.states import ExecutorAction

from .errors import GroundedDialogueError


@dataclass(frozen=True)
class ArmConfig:
    """실험 arm 하나. config의 arms[] 항목과 1:1 대응한다."""

    arm_id: str
    model_id: str            # "K0" | "KI20"
    max_length: int          # 768 | 2048
    system_prompt_id: str    # "terse_v1" | "full_v1"
    slot_extractor_id: str   # "rule" | "model_narrow"


class ModelRunner(Protocol):
    """생성 백엔드. 기존 phase5_stateful_chat_gate의 생성 경로를 재사용한다.

    새로 만들지 말고 그쪽 로더·generation contract를 그대로 감싼다.
    determinism(do_sample=False, num_beams=1)을 유지해야 재현된다.
    """

    def generate(self, messages: Sequence[Mapping[str, str]], *, max_new_tokens: int) -> str:
        ...


class CalculatorRunner(Protocol):
    """scripts.runtime.calculation.bridge.execute_runtime_tool 을 감싼다."""

    def calculate_chart(self, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


def run_case(
    case: Mapping[str, Any],
    *,
    arm: ArmConfig,
    model: ModelRunner,
    calculator: CalculatorRunner,
) -> dict[str, Any]:
    """dev case 하나를 FSM 루프로 재생하고 턴별 기록을 남긴다.

    루프 골격
        for 사용자 발화 in case.turns:
            슬롯 추출 -> session_state 갱신
            decision = decide(session_state, saju_intent=..., last_tool_status=...)
            if decision.action is CALL_CALCULATOR:
                internal, visible = calculator.calculate_chart(...)
                session_state["hard_facts"] = visible.get("hard_facts")
                decision = decide(...)          # 사실 확보 후 재판정
            messages = build_prompt(decision, session_state, arm)
            output = model.generate(messages, max_new_tokens=...)
            기록(turn, decision, visible, output)
    """
    raise NotImplementedError("run_case는 로컬에서 구현한다.")


def run_arm(
    arm: ArmConfig,
    cases: Sequence[Mapping[str, Any]],
    *,
    model: ModelRunner,
    calculator: CalculatorRunner,
) -> list[dict[str, Any]]:
    if not cases:
        raise GroundedDialogueError("dev case가 비었습니다.")
    return [run_case(case, arm=arm, model=model, calculator=calculator) for case in cases]


# TODO(local):
#   - build_report(): arm별 지표 집계 -> data/reports/saju_1b_baseline/
#       grounded-dialogue/v0.1.0/build-<fingerprint>/aggregate.json
#     기존 리포트 규약(build_manifest, implementation_sha256, 원문 비포함)을 따른다.
#   - 원시 생성문에는 개인정보가 없더라도 private 경로에만 둔다.
#     공개 aggregate에는 집계 수치와 위반 코드만 남긴다.
_ = (Decision, ExecutorAction)  # 로컬 구현에서 사용할 계약 심볼
