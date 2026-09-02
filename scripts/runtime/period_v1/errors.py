# errors.py - 기간 Runtime 계약 위반을 안정적인 reason code로 표현한다.

from __future__ import annotations


class PeriodRuntimeError(RuntimeError):
    """기간 요청·복원·권한 위반."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
