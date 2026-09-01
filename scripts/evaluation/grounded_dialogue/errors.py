# errors.py - grounded dialogue 진단 레인의 계약·실행 오류를 구분한다.

from __future__ import annotations


class GroundedDialogueError(RuntimeError):
    """진단 하네스 계약 위반."""


class ExtractionError(GroundedDialogueError):
    """슬롯 추출 결과가 고정 계약을 위반함."""


class ArtifactError(GroundedDialogueError):
    """고정 입력이나 불변 산출물이 계약과 다름."""


class PromptBudgetError(GroundedDialogueError):
    """보존 필수 prompt가 arm 입력 예산보다 큼."""

    def __init__(self, input_tokens: int, maximum: int, dropped_pairs: int) -> None:
        super().__init__(f"최소 prompt가 입력 상한을 넘습니다: {input_tokens} > {maximum}")
        self.input_tokens = input_tokens
        self.maximum = maximum
        self.dropped_pairs = dropped_pairs
