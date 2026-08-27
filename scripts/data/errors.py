# errors.py - 데이터 파이프라인에서 비밀값 없이 보고할 수 있는 단계별 예외를 정의한다.


class Phase1Error(RuntimeError):
    """사용자에게 비밀값 없이 표시할 수 있는 Phase 1 오류."""


class Phase2AuditError(RuntimeError):
    """원문이나 locator를 노출하지 않고 보고할 수 있는 Phase 2A 감사 오류."""
