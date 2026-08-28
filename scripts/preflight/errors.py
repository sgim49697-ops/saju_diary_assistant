# errors.py - Phase 4 비학습 preflight의 fail-closed 오류를 정의한다.


class Phase4Error(RuntimeError):
    """Phase 4 계약·데이터·추론 검증 실패."""
