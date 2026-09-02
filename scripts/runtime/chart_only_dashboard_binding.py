# chart_only_dashboard_binding.py - v1.4 원국 adapter를 공개 대시보드의 제한 운영 경계에 결합한다.

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import threading
from collections.abc import Mapping
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.chart_only_adapter import (
    ADAPTER_ID,
    ChartOnlyAdapterError,
    ChartOnlyAppAdapter,
    _assert_public_response,
    _public_hard_facts,
    build_chart_only_app_adapter,
)
from scripts.runtime.chart_only_operations_contracts import PARENT_RELEASE_ID
from scripts.runtime.chart_only_security import (
    ChartOnlySecurityError,
    validate_private_directory,
)

BINDING_ID = "saju-chart-only-dashboard-binding-v1.0.0"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
SNAPSHOT_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BINDING_OPERATION_ERRORS = (
    ChartOnlyAdapterError,
    ChartOnlySecurityError,
    RuntimeCalculationError,
    OSError,
    ValueError,
)


class ChartOnlyDashboardBindingError(RuntimeError):
    """공개 dashboard binding 요청을 안전한 HTTP 상태와 reason code로 닫는다."""

    def __init__(self, status: int, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.reason_code = reason_code


class _SingleProcessLease:
    """하나의 encrypted store를 한 dashboard process만 소유하게 한다."""

    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "PROCESS_LEASE_PATH_INVALID",
                "runtime process lease는 절대경로여야 합니다.",
            )
        validate_private_directory(path.parent, label="dashboard binding root")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "PROCESS_LEASE_OPEN_FAILED",
                "runtime process lease를 열 수 없습니다.",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != os.getuid()
                or metadata.st_nlink != 1
            ):
                raise ChartOnlyDashboardBindingError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "PROCESS_LEASE_PERMISSION_INVALID",
                    "runtime process lease 권한이 운영 계약과 다릅니다.",
                )
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ChartOnlyDashboardBindingError(
                    HTTPStatus.CONFLICT,
                    "DUPLICATE_RUNTIME_PROCESS",
                    "동일 runtime store를 소유한 dashboard process가 이미 실행 중입니다.",
                ) from exc
        except Exception:
            os.close(descriptor)
            raise
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor = getattr(self, "_descriptor", -1)
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _governance() -> dict[str, Any]:
    return {
        "binding_id": BINDING_ID,
        "adapter_version": ADAPTER_ID,
        "release_id": PARENT_RELEASE_ID,
        "runtime_feature_default": False,
        "encrypted_persistence": True,
        "retention_seconds": 1800,
        "period_calculation_allowed": False,
        "production_application_binding": True,
        "model_context_binding": True,
        "public_client_authentication_required": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ChartOnlyDashboardBindingError(
            HTTPStatus.NOT_FOUND,
            "RUNTIME_SESSION_NOT_FOUND",
            "runtime session을 찾을 수 없습니다.",
        )


class ChartOnlyDashboardBinding:
    """단일 process·다중 thread에서 v1.4 chart-only adapter를 직렬화한다."""

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
        self._lease = _SingleProcessLease(process_lease_file)
        try:
            adapter = build_chart_only_app_adapter(
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
        if not isinstance(adapter, ChartOnlyAppAdapter):
            self._lease.close()
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "RUNTIME_ADAPTER_NOT_ACTIVE",
                "활성 chart-only adapter를 만들지 못했습니다.",
            )
        self.adapter = adapter
        self._operation_lock = threading.Lock()
        self._closed = False

    def __enter__(self) -> ChartOnlyDashboardBinding:  # noqa: PYI034
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

    def status(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "status": "limited_public_chart_only_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "승인된 과거 원국 전용 runtime이 제한 공개 상태로 활성화됐습니다.",
            "release_id": PARENT_RELEASE_ID,
            "adapter_version": ADAPTER_ID,
            "binding_id": BINDING_ID,
            "facts_rendered_without_model": True,
            "period_calculation_allowed": False,
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
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "RUNTIME_BINDING_CLOSED",
                "runtime binding이 닫혀 있습니다.",
            )
        if not self._operation_lock.acquire(blocking=False):
            raise ChartOnlyDashboardBindingError(
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
            "governance": _governance(),
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
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "EXPECTED_REVISION_INVALID",
                "expected_revision은 0 이상의 정수여야 합니다.",
            )
        if not isinstance(event, Mapping):
            raise ChartOnlyDashboardBindingError(
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
                raise ChartOnlyDashboardBindingError(
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
        _assert_public_response(response)
        projected = deepcopy(response)
        projected["governance"] = _governance()
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
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            )
        return {
            "status": "deleted",
            "retained": False,
            "governance": _governance(),
        }

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        """모델에 전달할 allowlist 사실만 읽고 capability 원문은 반환하지 않는다."""

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
        if not isinstance(chart, Mapping) or chart.get("status") not in {"ok", "partial"}:
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_CHART_REQUIRED",
                "모델 연결 전에 승인된 원국 계산을 완료해야 합니다.",
            )
        value = {
            "chart": {
                "status": chart["status"],
                "fact_authority": chart["fact_authority"],
                "hard_facts": _public_hard_facts(chart["hard_facts"]),
                "message": chart["message"],
                "limitations": deepcopy(chart.get("limitations", [])),
            }
        }
        snapshot_sha256 = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        capability_sha256 = hashlib.sha256(
            b"saju-dashboard-runtime-capability-v1\0" + session_id.encode("ascii")
        ).hexdigest()
        if (
            SNAPSHOT_SHA256_PATTERN.fullmatch(snapshot_sha256) is None
            or SNAPSHOT_SHA256_PATTERN.fullmatch(capability_sha256) is None
        ):
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "RUNTIME_SNAPSHOT_INVALID",
                "runtime snapshot fingerprint 생성에 실패했습니다.",
            )
        return {
            "schema_version": "1.0.0",
            "binding_id": BINDING_ID,
            "capability_sha256": capability_sha256,
            "snapshot_sha256": snapshot_sha256,
            "state_revision": state["state_revision"],
            "value": value,
        }

    @staticmethod
    def _raise_safe(exc: Exception, *, missing_as_not_found: bool = False) -> None:
        if isinstance(exc, ChartOnlyDashboardBindingError):
            raise exc
        if missing_as_not_found and isinstance(exc, (FileNotFoundError, OSError)):
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            ) from exc
        name = type(exc).__name__
        if name == "ChartOnlySecurityError" and (
            "보존 기한" in str(exc) or "열지 못했습니다" in str(exc)
        ):
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.NOT_FOUND,
                "RUNTIME_SESSION_NOT_FOUND",
                "runtime session을 찾을 수 없습니다.",
            ) from exc
        if name in {"ChartOnlyAdapterError", "ChartOnlySecurityError", "RuntimeCalculationError"}:
            raise ChartOnlyDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "RUNTIME_REQUEST_REJECTED",
                "runtime 요청이 계약 검증에서 거부됐습니다.",
            ) from exc
        raise ChartOnlyDashboardBindingError(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "RUNTIME_INTERNAL_ERROR",
            "runtime 요청을 안전하게 처리하지 못했습니다.",
        ) from exc
