# product_dashboard_binding_v1.py - 기존 원국·일진 계약을 보존하며 요청 범위와 저장 시점의 상태를 검증한다.

import hashlib
from collections.abc import Mapping
from contextlib import contextmanager

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.chart_day_adapter import assert_public_response, public_chart
from scripts.runtime.chart_day_dashboard_binding import (
    BINDING_OPERATION_ERRORS,
    ChartDayDashboardBinding,
    ChartDayDashboardBindingError,
    _validate_session_id,
)


class ProductDashboardBinding(ChartDayDashboardBinding):
    """기존 암호화 저장·TTL·HMAC·single process lease를 그대로 사용한다."""

    def public_snapshot(self, session_id, *, scope="chart_day"):
        if scope == "chart_day":
            return super().public_snapshot(session_id)
        if scope != "chart":
            raise ChartDayDashboardBindingError(400, "PRODUCT_BINDING_SCOPE_INVALID", "연결 범위가 다릅니다.")
        _validate_session_id(session_id)
        self._acquire()
        try:
            state = self._read_state(session_id)
            return self._chart_snapshot(session_id, state)
        finally:
            self._operation_lock.release()

    def _read_state(self, session_id):
        try:
            return self.adapter.store.read(session_id)
        except BINDING_OPERATION_ERRORS as exc:
            self._raise_safe(exc, missing_as_not_found=True)

    @staticmethod
    def _chart_snapshot(session_id, state):
        chart = state.get("chart")
        if not isinstance(chart, Mapping) or chart.get("status") not in {"ok", "partial"} or chart.get("fact_authority") not in {"HARD_GT", "POLICY_BOUND_RULE"}:
            raise ChartDayDashboardBindingError(409, "RUNTIME_CHART_REQUIRED", "승인된 원국을 먼저 계산해 주세요.")
        value = {"chart": public_chart(chart)}
        assert_public_response(value)
        return {
            "schema_version": "1.0.0", "binding_id": "saju-chart-only-dashboard-binding-v1.0.0",
            "capability_sha256": hashlib.sha256(b"saju-dashboard-runtime-capability-v1\0" + session_id.encode("ascii")).hexdigest(),
            "snapshot_sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
            "state_revision": state["state_revision"], "value": value,
        }

    @contextmanager
    def guard_snapshot(self, session_id, expected, *, scope="chart_day"):
        _validate_session_id(session_id)
        self._acquire()
        try:
            state = self._read_state(session_id)
            if scope == "chart":
                actual = self._chart_snapshot(session_id, state)
            elif scope == "chart_day":
                # 부모와 같은 public projection. 잠금을 중첩 획득하지 않는다.
                from scripts.runtime.chart_day_adapter import public_period

                chart, period = state.get("chart"), state.get("period")
                if any(not isinstance(v, Mapping) or v.get("status") != "ok" or v.get("fact_authority") != "HARD_GT" for v in (chart, period)):
                    raise ChartDayDashboardBindingError(409, "RUNTIME_REQUEST_STATE_CHANGED", "응답 생성 중 계산 상태가 변경됐습니다.")
                value = {"chart": public_chart(chart), "period": public_period(period)}
                assert_public_response(value)
                actual = {
                    "schema_version": "1.1.0", "binding_id": "saju-chart-day-dashboard-binding-v1.1.0",
                    "capability_sha256": hashlib.sha256(b"saju-dashboard-runtime-capability-v2\0" + session_id.encode("ascii")).hexdigest(),
                    "snapshot_sha256": hashlib.sha256(canonical_json_bytes(value)).hexdigest(),
                    "state_revision": state["state_revision"], "value": value,
                }
            else:
                raise ChartDayDashboardBindingError(400, "PRODUCT_BINDING_SCOPE_INVALID", "연결 범위가 다릅니다.")
            if actual != expected:
                raise ChartDayDashboardBindingError(409, "RUNTIME_REQUEST_STATE_CHANGED", "응답 생성 중 원국·날짜가 바뀌었습니다. 새 연결에서 다시 요청해 주세요.")
            yield
        finally:
            self._operation_lock.release()
