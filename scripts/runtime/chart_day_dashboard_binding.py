# chart_day_dashboard_binding.py - v1.5 원국+단일 일진 adapter를 dashboard에 결합한다.

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts_v1_5 import (
    SINGLE_DAY_END_DATE,
    SINGLE_DAY_START_DATE,
)
from scripts.runtime.calculation.engine_v1_5 import effective_single_day_start
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.chart_day_adapter import (
    ADAPTER_ID,
    ChartDayAdapterError,
    ChartDayAppAdapter,
    assert_public_response,
    build_chart_day_app_adapter,
    public_chart,
    public_period,
)
from scripts.runtime.chart_only_dashboard_binding import (
    ChartOnlyDashboardBindingError,
    _SingleProcessLease,
)
from scripts.runtime.chart_only_security import ChartOnlySecurityError

BINDING_ID = "saju-chart-day-dashboard-binding-v1.1.0"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BINDING_OPERATION_ERRORS = (
    ChartDayAdapterError,
    ChartOnlySecurityError,
    RuntimeCalculationError,
    OSError,
    ValueError,
)


class ChartDayDashboardBindingError(RuntimeError):
    """dashboard binding 요청을 안전한 HTTP 상태와 reason code로 닫는다."""

    def __init__(self, status: int, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.reason_code = reason_code


def _governance(release_id: str) -> dict[str, Any]:
    return {
        "binding_id": BINDING_ID,
        "adapter_version": ADAPTER_ID,
        "release_id": release_id,
        "runtime_feature_default": False,
        "encrypted_persistence": True,
        "retention_seconds": 1800,
        "single_day_calculation_allowed": True,
        "production_application_binding": True,
        "model_context_binding": True,
        "public_client_authentication_required": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ChartDayDashboardBindingError(
            HTTPStatus.NOT_FOUND,
            "RUNTIME_SESSION_NOT_FOUND",
            "runtime session을 찾을 수 없습니다.",
        )


class ChartDayDashboardBinding:
    """단일 process·다중 thread에서 v1.5 adapter를 직렬화한다."""

    def __init__(
        self,
        *,
        release_registry: Path,
        ephemeris_path: Path,
        hmac_key_file: Path,
        encryption_key_file: Path,
        store_root: Path,
        process_lease_file: Path,
        previous_encryption_key_file: Path | None = None,
    ) -> None:
        try:
            self._lease = _SingleProcessLease(process_lease_file)
        except ChartOnlyDashboardBindingError as exc:
            raise ChartDayDashboardBindingError(
                exc.status, exc.reason_code, str(exc)
            ) from exc
        try:
            adapter = build_chart_day_app_adapter(
                enable_adapter=True,
                release_registry=release_registry,
                ephemeris_path=ephemeris_path,
                hmac_key_file=hmac_key_file,
                encryption_key_file=encryption_key_file,
                previous_encryption_key_file=previous_encryption_key_file,
                store_root=store_root,
            )
        except Exception:
            self._lease.close()
            raise
        if not isinstance(adapter, ChartDayAppAdapter):
            self._lease.close()
            raise ChartDayDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "RUNTIME_ADAPTER_NOT_ACTIVE",
                "활성 원국+단일 일진 adapter를 만들지 못했습니다.",
            )
        self.adapter = adapter
        self.release_id = adapter.release_id
        self._operation_lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> ChartDayDashboardBinding:  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.adapter.close()
        finally:
            self._lease.close()

    def _date_bounds(self) -> tuple[str, str, str]:
        today = self.adapter.engine._today_provider()
        minimum = effective_single_day_start(today)
        return today.isoformat(), minimum.isoformat(), SINGLE_DAY_END_DATE

    def status(self) -> dict[str, Any]:
        today, minimum, maximum = self._date_bounds()
        return {
            "schema_version": "1.1.0",
            "status": "limited_public_chart_and_single_day_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "승인된 과거 원국과 단일 일진 runtime이 제한 공개 상태로 활성화됐습니다.",
            "release_id": self.release_id,
            "adapter_version": ADAPTER_ID,
            "binding_id": BINDING_ID,
            "facts_rendered_without_model": True,
            "single_day_calculation_allowed": True,
            "single_day_today_kst": today,
            "single_day_minimum": minimum,
            "single_day_release_minimum": SINGLE_DAY_START_DATE,
            "single_day_maximum": maximum,
            "single_day_evaluation_local_time": "12:00",
            "production_application_binding": True,
            "model_context_binding": True,
            "state_encrypted": True,
            "retention_seconds": 1800,
            "client_authentication_required": False,
            "request_metadata_logged": True,
            "request_bodies_logged": False,
            "feature_default": False,
        }

    def _acquire(self) -> None:
        if self._closed:
            raise ChartDayDashboardBindingError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "RUNTIME_BINDING_CLOSED",
                "runtime binding이 닫혀 있습니다.",
            )
        if not self._operation_lock.acquire(blocking=False):
            raise ChartDayDashboardBindingError(
                HTTPStatus.TOO_MANY_REQUESTS,
                "RUNTIME_BUSY",
                "다른 runtime 요청을 처리 중입니다. 잠시 후 다시 시도하세요.",
            )

    def create_session(self) -> dict[str, Any]:
        self._acquire()
        try:
            created = self.adapter.create_session()
        except BINDING_OPERATION_ERRORS as exc:
            self._raise_safe(exc)
        finally:
            self._operation_lock.release()
        return {
            "status": "created",
            "session_id": created["session_id"],
            "state_revision": 0,
            "expires_in_seconds": 1800,
            "governance": _governance(self.release_id),
        }

    def handle_event(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_session_id(session_id)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ChartDayDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "EXPECTED_REVISION_INVALID",
                "expected_revision은 0 이상의 정수여야 합니다.",
            )
        if not isinstance(event, Mapping):
            raise ChartDayDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "RUNTIME_EVENT_INVALID",
                "구조화 runtime event object가 필요합니다.",
            )
        self._acquire()
        try:
            try:
                current = self.adapter.store.read(session_id)
            except BINDING_OPERATION_ERRORS as exc:
                self._raise_safe(exc, missing_as_not_found=True)
            if current.get("state_revision") != expected_revision:
                raise ChartDayDashboardBindingError(
                    HTTPStatus.CONFLICT,
                    "STALE_RUNTIME_REVISION",
                    "runtime session revision이 변경됐습니다. 최신 상태에서 다시 시도하세요.",
                )
            try:
                response = self.adapter.handle_event(session_id, event)
            except BINDING_OPERATION_ERRORS as exc:
                self._raise_safe(exc)
        finally:
            self._operation_lock.release()
        assert_public_response(response)
        projected = deepcopy(response)
        projected["governance"] = _governance(self.release_id)
        return projected

    def delete_session(self, session_id: str) -> dict[str, Any]:
        _validate_session_id(session_id)
        self._acquire()
        try:
            try:
                deleted = self.adapter.store.delete(session_id)
            except BINDING_OPERATION_ERRORS as exc:
                self._raise_safe(exc)
        finally:
            self._operation_lock.release()
        if not deleted:
            raise ChartDayDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            )
        return {
            "status": "deleted",
            "retained": False,
            "governance": _governance(self.release_id),
        }

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        """exact 원국·선택 일진 allowlist만 읽고 capability 원문은 반환하지 않는다."""

        _validate_session_id(session_id)
        self._acquire()
        try:
            try:
                state = self.adapter.store.read(session_id)
            except BINDING_OPERATION_ERRORS as exc:
                self._raise_safe(exc, missing_as_not_found=True)
        finally:
            self._operation_lock.release()
        chart = state.get("chart")
        period = state.get("period")
        if (
            not isinstance(chart, Mapping)
            or chart.get("status") != "ok"
            or chart.get("fact_authority") != "HARD_GT"
        ):
            raise ChartDayDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_EXACT_CHART_REQUIRED",
                "모델 연결 전에 exact 입력의 승인 원국을 계산해야 합니다.",
            )
        if (
            not isinstance(period, Mapping)
            or period.get("status") != "ok"
            or period.get("fact_authority") != "HARD_GT"
        ):
            raise ChartDayDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_SINGLE_DAY_REQUIRED",
                "모델 연결 전에 승인된 단일 일진을 계산해야 합니다.",
            )
        value = {"chart": public_chart(chart), "period": public_period(period)}
        assert_public_response(value)
        snapshot_sha256 = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        capability_sha256 = hashlib.sha256(
            b"saju-dashboard-runtime-capability-v2\0" + session_id.encode("ascii")
        ).hexdigest()
        if (
            SHA256_PATTERN.fullmatch(snapshot_sha256) is None
            or SHA256_PATTERN.fullmatch(capability_sha256) is None
        ):
            raise ChartDayDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "RUNTIME_SNAPSHOT_INVALID",
                "runtime snapshot fingerprint 생성에 실패했습니다.",
            )
        return {
            "schema_version": "1.1.0",
            "binding_id": BINDING_ID,
            "capability_sha256": capability_sha256,
            "snapshot_sha256": snapshot_sha256,
            "state_revision": state["state_revision"],
            "value": value,
        }

    @staticmethod
    def _raise_safe(exc: Exception, *, missing_as_not_found: bool = False) -> None:
        if isinstance(exc, ChartDayDashboardBindingError):
            raise exc
        if missing_as_not_found and isinstance(exc, (FileNotFoundError, OSError)):
            raise ChartDayDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            ) from exc
        if isinstance(exc, ChartOnlySecurityError) and (
            "보존 기한" in str(exc) or "열지 못했습니다" in str(exc)
        ):
            raise ChartDayDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            ) from exc
        if isinstance(exc, (ChartDayAdapterError, ChartOnlySecurityError, RuntimeCalculationError)):
            raise ChartDayDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "RUNTIME_REQUEST_REJECTED",
                "runtime 요청이 계약 검증에서 거부됐습니다.",
            ) from exc
        raise ChartDayDashboardBindingError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "RUNTIME_INTERNAL_ERROR",
            "runtime 요청 처리에 실패했습니다.",
        ) from exc
