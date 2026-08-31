# errors.py - 만세력 입력·환경·계산 실패를 안정적인 code로 표현한다.

from __future__ import annotations


class RuntimeCalculationError(RuntimeError):
    """사용자 입력 또는 runtime 계약 위반을 code와 함께 전달한다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
