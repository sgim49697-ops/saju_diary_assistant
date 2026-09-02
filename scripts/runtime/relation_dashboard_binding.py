# relation_dashboard_binding.py - 단일 날짜 관계를 v1.13 기간 session 위에 원자적으로 결합한다.

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
from scripts.runtime.chart_day_dashboard_binding import _validate_session_id
from scripts.runtime.chart_only_security import ChartOnlySecurityError
from scripts.runtime.period_dashboard_binding import (
    PeriodDashboardBinding,
    PeriodDashboardBindingError,
    _public_period_from_state,
    _stored_period,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.relation_v1.contracts import (
    RELEASE_PATH as RELATION_RELEASE_PATH,
)
from scripts.runtime.relation_v1.contracts import validate_release_registry
from scripts.runtime.relation_v1.engine import (
    ApprovedSingleDateRelationEngine,
    public_relation_result,
)
from scripts.runtime.relation_v1.errors import RelationRuntimeError
from scripts.runtime.relation_v1.security import RelationIdSigner

BINDING_ID = "saju-relation-dashboard-binding-v1.3.0"
SCHEMA_VERSION = "1.3.0"
STORED_RELATION_MARKER = "saju-relation-dashboard-state-v1.0"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _governance(
    parent_release_id: str, period_release_id: str, relation_release_id: str
) -> dict[str, Any]:
    return {
        "binding_id": BINDING_ID,
        "parent_runtime_release_id": parent_release_id,
        "period_release_id": period_release_id,
        "relation_release_id": relation_release_id,
        "runtime_feature_default": False,
        "encrypted_persistence": True,
        "retention_seconds": 1800,
        "daily_label_range_allowed": True,
        "maximum_days": 31,
        "single_date_relation_allowed": True,
        "range_relation_arrays_supported": False,
        "relation_interpretation_included": False,
        "intraday_segments_supported": False,
        "production_application_binding": True,
        "model_context_binding": True,
        "public_client_authentication_required": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }


def _stored_period_and_relation(
    period_internal: Mapping[str, Any],
    relation_internal: Mapping[str, Any] | None,
    period_release_id: str,
) -> dict[str, Any]:
    value = _stored_period(period_internal, period_release_id)
    value["relation_state_schema"] = STORED_RELATION_MARKER
    value["relation_internal"] = (
        deepcopy(dict(relation_internal))
        if isinstance(relation_internal, Mapping)
        else None
    )
    return value


class RelationDashboardBinding(PeriodDashboardBinding):
    """단일 날짜에만 relation을 붙이고 범위에는 공식 label만 유지한다."""

    def __init__(
        self,
        *,
        parent_release_registry: Path,
        period_release_registry: Path,
        relation_release_registry: Path,
        ephemeris_path: Path,
        hmac_key_file: Path,
        encryption_key_file: Path,
        store_root: Path,
        process_lease_file: Path,
        previous_encryption_key_file: Path | None = None,
    ) -> None:
        if relation_release_registry.resolve(
            strict=False
        ) != RELATION_RELEASE_PATH.resolve(strict=False):
            raise PeriodDashboardBindingError(
                HTTPStatus.BAD_REQUEST,
                "RELATION_RELEASE_PATH_INVALID",
                "고정 단일 날짜 relation release만 허용합니다.",
            )
        relation_release = validate_release_registry(relation_release_registry)
        super().__init__(
            parent_release_registry=parent_release_registry,
            period_release_registry=period_release_registry,
            ephemeris_path=ephemeris_path,
            hmac_key_file=hmac_key_file,
            encryption_key_file=encryption_key_file,
            previous_encryption_key_file=previous_encryption_key_file,
            store_root=store_root,
            process_lease_file=process_lease_file,
        )
        self.relation_release = relation_release
        self.relation_release_id = str(relation_release["release_id"])
        self.relation_engine = ApprovedSingleDateRelationEngine(
            period_signer=self.period_engine.period_signer,
            relation_signer=RelationIdSigner.from_runtime_secret(
                self.adapter.hmac_key
            ),
            release_registry=relation_release_registry,
            enable_approved_runtime=True,
        )

    def _governance(self) -> dict[str, Any]:
        return _governance(
            self.parent_release_id,
            self.period_release_id,
            self.relation_release_id,
        )

    def _relation_from_state(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        stored = state.get("period")
        if stored is None:
            return None
        if (
            not isinstance(stored, Mapping)
            or stored.get("relation_state_schema") != STORED_RELATION_MARKER
            or not isinstance(stored.get("daily_label_internal"), Mapping)
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_RELATION_STATE_INVALID",
                "저장된 relation 상태를 검증할 수 없습니다.",
            )
        period = _public_period_from_state(stored)
        relation_internal = stored.get("relation_internal")
        if period["period_scope"]["day_count"] != 1:
            if relation_internal is not None:
                raise PeriodDashboardBindingError(
                    HTTPStatus.CONFLICT,
                    "RUNTIME_RANGE_RELATION_FORBIDDEN",
                    "범위 기간에는 relation 배열을 저장할 수 없습니다.",
                )
            return None
        chart = state.get("chart")
        if not isinstance(chart, Mapping) or not isinstance(
            relation_internal, Mapping
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_SINGLE_DATE_RELATION_REQUIRED",
                "단일 날짜 relation snapshot이 필요합니다.",
            )
        try:
            recomputed_internal, recomputed_public = self.relation_engine.calculate(
                chart_snapshot=chart,
                period_snapshot=stored["daily_label_internal"],
            )
            stored_public = public_relation_result(relation_internal)
        except RelationRuntimeError as exc:
            raise PeriodDashboardBindingError(
                HTTPStatus.CONFLICT,
                exc.code,
                "저장된 relation 부모와 hash가 다릅니다.",
            ) from exc
        if (
            stored_public != recomputed_public
            or relation_internal.get("relation_snapshot_id")
            != recomputed_internal.get("relation_snapshot_id")
            or relation_internal.get("parent_chart_id")
            != recomputed_internal.get("parent_chart_id")
            or relation_internal.get("parent_period_id")
            != recomputed_internal.get("parent_period_id")
        ):
            raise PeriodDashboardBindingError(
                HTTPStatus.CONFLICT,
                "RUNTIME_RELATION_REHYDRATION_MISMATCH",
                "저장 relation과 재계산 relation이 다릅니다.",
            )
        return stored_public

    def status(self) -> dict[str, Any]:
        value = super().status()
        value.update(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "limited_public_chart_period_and_single_date_relation_active",
                "message": "승인 원국·기간과 단일 날짜 관계 사실이 활성화됐습니다.",
                "binding_id": BINDING_ID,
                "relation_release_id": self.relation_release_id,
                "single_date_relation_allowed": True,
                "range_relation_arrays_supported": False,
                "relation_interpretation_included": False,
            }
        )
        return value

    def create_session(self) -> dict[str, Any]:
        result = super().create_session()
        result["governance"] = self._governance()
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
            self._acquire()
            try:
                state = self.adapter.store.read(session_id)
                relation = self._relation_from_state(state)
            finally:
                self._operation_lock.release()
            if isinstance(result.get("result"), dict):
                result["result"]["relation"] = relation
                assert_public_response(result)
            result["governance"] = self._governance()
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
                period_internal, period_public = self.period_engine.calculate(
                    state,
                    event,
                    expected_revision=expected_revision,
                    reference_date=self.adapter.engine._today_provider(),
                )
                chart = state.get("chart")
                if not isinstance(chart, Mapping):
                    raise PeriodRuntimeError(
                        "PERIOD_CHART_REQUIRED", "승인 원국이 필요합니다."
                    )
                relation_internal: dict[str, Any] | None = None
                relation_public: dict[str, Any] | None = None
                if period_public["period_scope"]["day_count"] == 1:
                    relation_internal, relation_public = self.relation_engine.calculate(
                        chart_snapshot=chart,
                        period_snapshot=period_internal,
                    )
                state["current_intent"] = "period"
                state["period"] = _stored_period_and_relation(
                    period_internal,
                    relation_internal,
                    self.period_release_id,
                )
                state["state_revision"] += 1
                self.adapter.store.put(session_id, state)
                response = {
                    "status": "ready",
                    "state_revision": state["state_revision"],
                    "decision": {
                        "action": "render_chart_period_and_optional_relation",
                        "message": str(period_public["message"]),
                        "reason_code": None,
                    },
                    "result": {
                        "chart": public_chart(chart),
                        "period": period_public,
                        "relation": relation_public,
                    },
                    "governance": self._governance(),
                }
                assert_public_response(response)
                return response
            except PeriodDashboardBindingError:
                raise
            except (
                PeriodRuntimeError,
                RelationRuntimeError,
                RuntimeCalculationError,
            ) as exc:
                code = getattr(exc, "code", "RUNTIME_REQUEST_REJECTED")
                conflict_codes = {
                    "PERIOD_CHART_REQUIRED",
                    "PERIOD_CHART_NOT_EXACT",
                    "PERIOD_CHART_AUTHORITY_INVALID",
                    "PERIOD_CHART_STALE",
                    "PERIOD_FEATURE_DISABLED",
                    "RELATION_FEATURE_DISABLED",
                    "RELATION_RELEASE_REQUIRED",
                    "RELATION_PARENT_LINK_MISMATCH",
                }
                raise PeriodDashboardBindingError(
                    (
                        HTTPStatus.CONFLICT
                        if code in conflict_codes
                        else HTTPStatus.BAD_REQUEST
                    ),
                    str(code),
                    "기간·relation Runtime 요청이 계약 검증에서 거부됐습니다.",
                ) from exc
            except (ChartOnlySecurityError, OSError, ValueError) as exc:
                raise PeriodDashboardBindingError(
                    HTTPStatus.BAD_REQUEST,
                    "RUNTIME_REQUEST_REJECTED",
                    "기간·relation Runtime 요청이 계약 검증에서 거부됐습니다.",
                ) from exc
        finally:
            self._operation_lock.release()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        result = super().delete_session(session_id)
        result["governance"] = self._governance()
        return result

    def public_snapshot(self, session_id: str) -> dict[str, Any]:
        _validate_session_id(session_id)
        self._acquire()
        try:
            try:
                state = self.adapter.store.read(session_id)
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
                relation = self._relation_from_state(state)
            except PeriodDashboardBindingError:
                raise
            except (ChartOnlySecurityError, OSError) as exc:
                raise PeriodDashboardBindingError(
                    HTTPStatus.NOT_FOUND,
                    "RUNTIME_SESSION_NOT_FOUND",
                    "runtime session을 찾을 수 없습니다.",
                ) from exc
        finally:
            self._operation_lock.release()
        value = {
            "chart": public_chart(chart),
            "period": period,
            "relation": relation,
        }
        assert_public_response(value)
        snapshot_sha256 = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        capability_sha256 = hashlib.sha256(
            b"saju-dashboard-runtime-capability-v4\0" + session_id.encode("ascii")
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
