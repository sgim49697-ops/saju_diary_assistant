# errors.py - 데이터 파이프라인에서 비밀값 없이 보고할 수 있는 예외를 정의한다.


class Phase1Error(RuntimeError):
    """사용자에게 비밀값 없이 표시할 수 있는 Phase 1 오류."""
