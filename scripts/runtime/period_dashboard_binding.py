# period_dashboard_binding.py - 승인 원국과 일별 기간 Runtime을 암호화 dashboard session에 결합한다.

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.chart_day_adapter import assert_public_response, public_chart
from scripts.runtime.chart_day_dashboard_binding import (
    ChartDayDashboardBinding,
    _validate_session_id,
)
from scripts.runtime.chart_only_security import ChartOnlySecurityError

from .period_v1.contracts_v1_1 import RELEASE_PATH, validate_release_registry
from .period_v1.engine import (
    ApprovedDailyLabelPeriodEngine,
    public_daily_label_result,
)
from .period_v1.errors import PeriodRuntimeError
from .period_v1.security import PeriodIdSigner

BINDING_ID = "saju-period-dashboard-binding-v1.2.0"
SCHEMA_VERSION = "1.2.0"
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
STORED_PERIOD_MARKER = "saju-period-dashboard-state-v1.0"


class PeriodDashboardBindingError(RuntimeError):
    """기간 dashboard 요청을 안전한 HTTP 상태와 reason code로 닫는다."""

    def __init__(self, status: int, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.status = int(status)
        self.reason_code = reason_code


def _governance(parent_release_id: str, period_release_id: str) -> dict[str, Any]:
    return {
        "binding_id": BINDING_ID,
        "parent_runtime_release_id": parent_release_id,
        "period_release_id": period_release_id,
        "runtime_feature_default": False,
        "encrypted_persistence": True,
        "retention_seconds": 1800,
        "daily_label_range_allowed": True,
        "maximum_days": 31,
        "intraday_segments_supported": False,
        "production_application_binding": True,
        "model_context_binding": True,
        "public_client_authentication_required": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }


def _stored_period(
    internal: Mapping[str, Any], period_release_id: str
) -> dict[str, Any]:
    public = public_daily_label_result(internal)
    scope = public["period_scope"]
    return {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "hard_facts": {
            "period": {
                "period_type": "daily_label_range",
                "start_date": scope["start_date"],
                "end_date": scope["end_date"],
                "day_count": scope["day_count"],
                "timezone": scope["timezone"],
                "evaluation_local_time": scope["evaluation_local_time"],
                "days": deepcopy(public["days"]),
            },
            "day_assignment_evidence": {
                "authority": "SOURCE_HARD_FACT",
                "release_id": period_release_id,
                "provider_generated_value_is_official": False,
                "future_physical_instant_claimed": False,
            },
        },
        "message": public["message"],
        "limitations": deepcopy(public["limitations"]),
        "period_state_schema": STORED_PERIOD_MARKER,
        "daily_label_internal": deepcopy(dict(internal)),
    }


def _public_period_from_state(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or value.get("period_state_schema") != STORED_PERIOD_MARKER
        or not isinstance(value.get("daily_label_internal"), Mapping)
    ):
        raise PeriodDashboardBindingError(
            HTTPStatus.CONFLICT,
            "RUNTIME_DAILY_LABEL_RANGE_REQUIRED",
            "모델 연결 전에 승인된 일별 기간을 계산해야 합니다.",
        )
    return public_daily_label_result(value["daily_label_internal"])


class PeriodDashboardBinding(ChartDayDashboardBinding):
    """기존 원국 session을 보존하며 승인 일별 기간 release만 추가한다."""

    def __init__(
        self,
        *,
        parent_release_registry: Path,
        period_release_registry: Path,
        ephemeris_path: Path,
        hmac_key_file: Path,
        encryption_key_file: Path,
        store_root: Path,
        process_lease_file: Path,
        previous_encryption_key_file: Path | None = None,
    ) -> None:
        if period_release_registry.resolve(strict=False) != RELEASE_PATH.resolve(
            strict=False
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "PERIOD_RELEASE_PATH_INVALID",
                "고정 일별 기간 release만 허용합니다.",
            )
        release = validate_release_registry(period_release_registry)
        super().__init__(
            release_registry=parent_release_registry,
            ephemeris_path=ephemeris_path,
            hmac_key_file=hmac_key_file,
            encryption_key_file=encryption_key_file,
            previous_encryption_key_file=previous_encryption_key_file,
            store_root=store_root,
            process_lease_file=process_lease_file,
        )
        self.parent_release_id = self.release_id
        self.period_release = release
        self.period_release_id = str(release["release_id"])
        self.period_engine = ApprovedDailyLabelPeriodEngine(
            parent_engine=self.adapter.engine,
            runtime_signer=self.adapter.signer,
            period_signer=PeriodIdSigner.from_runtime_secret(self.adapter.hmac_key),
            release_registry=period_release_registry,
            enable_approved_runtime=True,
        )

    def status(self) -> dict[str, Any]:
        today, minimum, maximum = self._date_bounds()
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "limited_public_chart_and_daily_label_range_active",
            "configured": True,
            "release_available": True,
            "feature_requested": True,
            "enabled": True,
            "code": None,
            "message": "승인 원국과 최대 31일 공식 날짜 label 범위가 활성화됐습니다.",
            "parent_runtime_release_id": self.parent_release_id,
            "period_release_id": self.period_release_id,
            "binding_id": BINDING_ID,
            "facts_rendered_without_model": True,
            "daily_label_range_allowed": True,
            "period_today_kst": today,
            "period_minimum": minimum,
            "period_maximum": maximum,
            "period_maximum_days": 31,
            "period_evaluation_local_time": "12:00",
            "intraday_segments_supported": False,
            "production_application_binding": True,
            "model_context_binding": True,
            "state_encrypted": True,
            "retention_seconds": 1800,
            "client_authentication_required": False,
            "request_metadata_logged": True,
            "request_bodies_logged": False,
            "feature_default": False,
        }

    def create_session(self) -> dict[str, Any]:
        result = super().create_session()
        result["governance"] = _governance(
            self.parent_release_id, self.period_release_id
        )
        return result

    def handle_event(
        self,
        session_id: str,
        *,
        expected_revision: int,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(event, Mapping) or event.get("type") != "request_period":
            result = super().handle_event(
                session_id,
                expected_revision=expected_revision,
                event=event,
            )
            state = self.adapter.store.read(session_id)
            stored_period = state.get("period")
            if (
                isinstance(result.get("result"), dict)
                and isinstance(stored_period, Mapping)
                and stored_period.get("period_state_schema") == STORED_PERIOD_MARKER
            ):
                result["result"]["period"] = _public_period_from_state(stored_period)
                assert_public_response(result)
            result["governance"] = _governance(
                self.parent_release_id, self.period_release_id
            )
            return result

        _validate_session_id(session_id)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "EXPECTED_REVISION_INVALID",
                "expected_revision은 0 이상의 정수여야 합니다.",
            )
        self._acquire()
        try:
            try:
                state = self.adapter.store.read(session_id)
                if state.get("state_revision") != expected_revision:
                    raise PeriodDashboardBindingError(
                        HTTPStatus.CONFLICT,
                        "STALE_RUNTIME_REVISION",
                        "runtime session revision이 변경됐습니다.",
                    )
                internal, public = self.period_engine.calculate(
                    state,
                    event,
                    expected_revision=expected_revision,
                    reference_date=self.adapter.engine._today_provider(),
                )
                state["current_intent"] = "period"
                state["period"] = _stored_period(internal, self.period_release_id)
                state["state_revision"] += 1
                self.adapter.store.put(session_id, state)
                chart = state.get("chart")
                if not isinstance(chart, Mapping):
                    raise PeriodRuntimeError(
                        "PERIOD_CHART_REQUIRED", "승인 원국이 필요합니다."
                    )
                response = {
                    "status": "ready",
                    "state_revision": state["state_revision"],
                    "decision": {
                        "action": "render_chart_and_period",
                        "message": str(public["message"]),
                        "reason_code": None,
                    },
                    "result": {"chart": public_chart(chart), "period": public},
                    "governance": _governance(
                        self.parent_release_id, self.period_release_id
                    ),
                }
                assert_public_response(response)
                return response
            except PeriodDashboardBindingError:
                raise
            except (PeriodRuntimeError, RuntimeCalculationError) as exc:
                code = getattr(exc, "code", "RUNTIME_REQUEST_REJECTED")
                status = (
                    HTTPStatus.CONFLICT
                    if code
                    in {
                        "PERIOD_CHART_REQUIRED",
                        "PERIOD_CHART_NOT_EXACT",
                        "PERIOD_CHART_AUTHORITY_INVALID",
                        "PERIOD_CHART_STALE",
                        "PERIOD_FEATURE_DISABLED",
                    }
                    else HTTPStatus.BAD_REQUEST
                )
                raise PeriodDashboardBindingError(
                    status, str(code), "기간 Runtime 요청이 계약 검증에서 거부됐습니다."
                ) from exc
            except (ChartOnlySecurityError, OSError, ValueError) as exc:
                raise PeriodDashboardBindingError(
                    HTTPStatus.BAD_REQUEST,
                    "RUNTIME_REQUEST_REJECTED",
                    "기간 Runtime 요청이 계약 검증에서 거부됐습니다.",
                ) from exc
        finally:
            self._operation_lock.release()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        result = super().delete_session(session_id)
        result["governance"] = _governance(
            self.parent_release_id, self.period_release_id
        )
        return result

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        _validate_session_id(session_id)
        self._acquire()
        try:
            try:
                state = self.adapter.store.read(session_id)
            except (ChartOnlySecurityError, OSError) as exc:
                raise PeriodDashboardBindingError(
                    HTTPStatus.NOT_FOUND,
                    "RUNTIME_SESSION_NOT_FOUND",
                    "runtime session을 찾을 수 없습니다.",
                ) from exc
        finally:
            self._operation_lock.release()
        chart = state.get("chart")
        if (
            not isinstance(chart, Mapping)
            or chart.get("status") != "ok"
            or chart.get("fact_authority") != "HARD_GT"
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_EXACT_CHART_REQUIRED",
                "모델 연결 전에 exact 입력의 승인 원국을 계산해야 합니다.",
            )
        period = _public_period_from_state(state.get("period"))
        value = {"chart": public_chart(chart), "period": period}
        assert_public_response(value)
        snapshot_sha256 = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        capability_sha256 = hashlib.sha256(
            b"saju-dashboard-runtime-capability-v3\0" + session_id.encode("ascii")
        ).hexdigest()
        if (
            SHA256_PATTERN.fullmatch(snapshot_sha256) is None
            or SHA256_PATTERN.fullmatch(capability_sha256) is None
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "RUNTIME_SNAPSHOT_INVALID",
                "runtime snapshot fingerprint 생성에 실패했습니다.",
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "binding_id": BINDING_ID,
            "capability_sha256": capability_sha256,
            "snapshot_sha256": snapshot_sha256,
            "state_revision": state["state_revision"],
            "value": value,
        }
