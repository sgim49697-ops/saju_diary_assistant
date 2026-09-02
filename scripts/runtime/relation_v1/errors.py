# errors.py - 단일 날짜 relation 계약 위반을 안정적인 코드로 전달한다.

from __future__ import annotations


class RelationRuntimeError(RuntimeError):
    """Relation Runtime의 fail-closed 오류."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
