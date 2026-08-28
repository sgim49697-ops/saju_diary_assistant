# errors.py - Phase 3 검증 실패를 비밀값 없이 전달하는 예외를 정의한다.


class Phase3Error(RuntimeError):
    """사용자에게 안전하게 표시할 수 있는 Phase 3 오류."""
