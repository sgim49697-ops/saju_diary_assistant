# errors.py - grounded dialogue 진단 레인 전용 예외.

from __future__ import annotations


class GroundedDialogueError(RuntimeError):
    """진단 하네스 계약 위반."""
