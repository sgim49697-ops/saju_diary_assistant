# errors.py - 대화 정책 루프 전용 예외.

from __future__ import annotations


class DialoguePolicyError(RuntimeError):
    """정책 루프 계약 위반. 호출자는 복구하지 말고 실패로 처리한다."""
