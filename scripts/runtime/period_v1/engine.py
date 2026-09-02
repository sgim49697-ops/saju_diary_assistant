# engine.py - 승인 원국에 결합된 1~31일 공식 날짜 label 범위를 계산한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.chart_day_adapter import (
    ChartDayAdapterError,
    _validate_period_result,
)

from .contracts import (
    PARENT_RELEASE_ID,
    validate_chart_authorization,
    validate_resolved_scope,
)
from .contracts_v1_1 import validate_contract_registry_v1_1, validate_release_registry
from .errors import PeriodRuntimeError
from .rehydration import rehydrate_exact_chart
from .resolver import resolve_period_scope
from .security import PeriodIdSigner

PERIOD_ID_PATTERN = re.compile(r"^spd1_[0-9a-f]{64}$")
PUBLIC_RESULT_FIELDS = {
    "status",
    "fact_authority",
    "period_scope",
    "days",
    "boundary_capability",
    "message",
    "limitations",
}
PUBLIC_FORBIDDEN_KEYS = {
    "period_id",
    "chart_id",
    "chart_authorization",
    "birth_input_id",
    "normalized_input",
    "reference_date",
    "segments",
    "relations",
}


def _contains_private_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in PUBLIC_FORBIDDEN_KEYS or _contains_private_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_private_value(item) for item in value)
    if isinstance(value, str):
        return PERIOD_ID_PATTERN.fullmatch(value) is not None or re.fullmatch(
            r"(?:sc2_|sbi2_|scr2_|scs2_)[0-9a-f]{64}", value
        ) is not None
    return False


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def validate_public_daily_label_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PUBLIC_RESULT_FIELDS:
        raise PeriodRuntimeError(
            "PERIOD_OUTPUT_INVALID", "일별 기간 공개 결과 field 집합이 다릅니다."
        )
    scope = value.get("period_scope")
    days = value.get("days")
    boundary = value.get("boundary_capability")
    limitations = value.get("limitations")
    if (
        value.get("status") != "ok"
        or value.get("fact_authority") != "HARD_GT"
        or not isinstance(scope, Mapping)
        or set(scope)
        != {
            "date_expression",
            "start_date",
            "end_date",
            "day_count",
            "timezone",
            "evaluation_local_time",
        }
        or scope.get("timezone") != "Asia/Seoul"
        or scope.get("evaluation_local_time") != "12:00"
        or not isinstance(days, list)
        or not 1 <= len(days) <= 31
        or scope.get("day_count") != len(days)
        or boundary
        != {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        }
        or not isinstance(value.get("message"), str)
        or not value["message"]
        or not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise PeriodRuntimeError(
            "PERIOD_OUTPUT_INVALID", "일별 기간 공개 결과 값이 다릅니다."
        )
    start = date.fromisoformat(str(scope["start_date"]))
    end = date.fromisoformat(str(scope["end_date"]))
    expected_dates = _dates(start, end)
    if len(expected_dates) != len(days):
        raise PeriodRuntimeError(
            "PERIOD_OUTPUT_INVALID", "일별 기간 결과 날짜 수가 다릅니다."
        )
    for expected_date, day in zip(expected_dates, days, strict=True):
        if (
            not isinstance(day, Mapping)
            or set(day)
            != {"date", "year_ganzhi", "month_ganzhi", "day_ganzhi", "authority"}
            or day.get("date") != expected_date.isoformat()
            or day.get("authority") != "SOURCE_HARD_FACT"
            or any(
                not isinstance(day.get(field), str) or not day[field]
                for field in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
            )
        ):
            raise PeriodRuntimeError(
                "PERIOD_OUTPUT_INVALID", "일별 기간 label·순서·권한이 다릅니다."
            )
    if _contains_private_value(value):
        raise PeriodRuntimeError(
            "PERIOD_OUTPUT_PRIVATE_DATA", "일별 기간 공개 결과에 내부 정보가 있습니다."
        )
    return deepcopy(dict(value))


def public_daily_label_result(internal: Mapping[str, Any]) -> dict[str, Any]:
    return validate_public_daily_label_result(
        {field: deepcopy(internal[field]) for field in PUBLIC_RESULT_FIELDS}
    )


def calculate_authorized_daily_labels(
    *,
    parent_engine: ApprovedSajuRuntimeEngineV15,
    runtime_signer: RuntimeIdSigner,
    period_signer: PeriodIdSigner,
    authorization: Mapping[str, Any],
    resolved_scope: Mapping[str, Any],
    authority_release_id: str,
) -> dict[str, Any]:
    """검증된 원국 권한으로 v1.5 단일 일진을 날짜별로 합성한다."""
    validate_contract_registry_v1_1()
    auth = validate_chart_authorization(authorization)
    if not isinstance(parent_engine, ApprovedSajuRuntimeEngineV15):
        raise PeriodRuntimeError(
            "PERIOD_RUNTIME_ENGINE_INVALID", "v1.5 Runtime engine이 필요합니다."
        )
    if not isinstance(runtime_signer, RuntimeIdSigner) or not isinstance(
        period_signer, PeriodIdSigner
    ):
        raise PeriodRuntimeError(
            "PERIOD_RUNTIME_SIGNER_INVALID", "기간 Runtime signer가 다릅니다."
        )
    scope = validate_resolved_scope(resolved_scope)
    canonical_scope = resolve_period_scope(
        {
            "type": "request_period",
            "request": {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": resolved_scope.get("start_date"),
                "end_date": resolved_scope.get("end_date"),
            },
        },
        reference_date=date.fromisoformat(scope["reference_date"]),
    )
    if any(
        canonical_scope.get(key) != scope.get(key)
        for key in canonical_scope
        if key != "date_expression"
    ):
        raise PeriodRuntimeError(
            "PERIOD_SCOPE_INVALID", "기간 범위가 executor 해석과 다릅니다."
        )

    days: list[dict[str, str]] = []
    for target in _dates(
        date.fromisoformat(scope["start_date"]), date.fromisoformat(scope["end_date"])
    ):
        arguments = {
            "chart_id": auth["chart_id"],
            "period_type": "day",
            "start_date": target.isoformat(),
            "end_date": target.isoformat(),
            "timezone": "Asia/Seoul",
        }
        result = parent_engine.calculate_period(arguments)
        try:
            _validate_period_result(
                result,
                arguments=arguments,
                signer=runtime_signer,
                release_id=PARENT_RELEASE_ID,
            )
        except (ChartDayAdapterError, RuntimeCalculationError) as exc:
            raise PeriodRuntimeError(
                "PERIOD_PARENT_RESULT_INVALID", "부모 단일 일진 검증에 실패했습니다."
            ) from exc
        if result.get("status") != "ok":
            raise PeriodRuntimeError(
                str(result.get("code") or "PERIOD_PARENT_CALCULATION_BLOCKED"),
                "부모 단일 일진 계산이 차단됐습니다.",
            )
        hard_facts = result["hard_facts"]
        period = hard_facts["period"]
        evidence = hard_facts["day_assignment_evidence"]
        if (
            evidence.get("authority") != "SOURCE_HARD_FACT"
            or evidence.get("provider_generated_value_is_official") is not False
            or evidence.get("future_physical_instant_claimed") is not False
        ):
            raise PeriodRuntimeError(
                "PERIOD_PARENT_AUTHORITY_INVALID", "부모 단일 일진 권한이 다릅니다."
            )
        days.append(
            {
                "date": target.isoformat(),
                "year_ganzhi": period["year_ganzhi"],
                "month_ganzhi": period["month_ganzhi"],
                "day_ganzhi": period["day_ganzhi"],
                "authority": "SOURCE_HARD_FACT",
            }
        )

    public_scope = {
        key: scope[key]
        for key in (
            "date_expression",
            "start_date",
            "end_date",
            "day_count",
            "timezone",
            "evaluation_local_time",
        )
    }
    preimage = {
        "authority": auth,
        "period_scope": scope,
        "days": days,
        "authority_release_id": authority_release_id,
    }
    internal = {
        "schema_version": "saju-period-daily-label-internal-v1",
        "status": "ok",
        "fact_authority": "HARD_GT",
        "period_scope": public_scope,
        "days": days,
        "boundary_capability": {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        },
        "message": "승인된 공식 날짜 label로 기간 범위를 계산했습니다.",
        "limitations": [
            "날짜별 연주·월주·일주 label만 제공하며 분 단위 절입 구간은 계산하지 않습니다.",
            "원국 관계·대운·세운·사건 예측은 제공하지 않습니다.",
        ],
        "period_id": period_signer.period_id(authority_release_id, preimage),
        "chart_authorization": deepcopy(auth),
        "authority_release_id": authority_release_id,
        "reference_date": scope["reference_date"],
    }
    if PERIOD_ID_PATTERN.fullmatch(internal["period_id"]) is None:
        raise PeriodRuntimeError(
            "PERIOD_ID_INVALID", "기간 내부 ID 생성에 실패했습니다."
        )
    public_daily_label_result(internal)
    return internal


def calculate_daily_labels_candidate(
    state: Any,
    event: Any,
    *,
    expected_revision: int,
    reference_date: date,
    parent_engine: ApprovedSajuRuntimeEngineV15,
    runtime_signer: RuntimeIdSigner,
    period_signer: PeriodIdSigner,
    authority_release_id: str,
) -> dict[str, Any]:
    authorization = rehydrate_exact_chart(
        state,
        expected_revision=expected_revision,
        engine=parent_engine,
        signer=runtime_signer,
    )
    scope = resolve_period_scope(event, reference_date=reference_date)
    return calculate_authorized_daily_labels(
        parent_engine=parent_engine,
        runtime_signer=runtime_signer,
        period_signer=period_signer,
        authorization=authorization,
        resolved_scope=scope,
        authority_release_id=authority_release_id,
    )


class ApprovedDailyLabelPeriodEngine:
    """유효 daily-label release와 명시 flag가 있을 때만 기간 범위를 연다."""

    def __init__(
        self,
        *,
        parent_engine: ApprovedSajuRuntimeEngineV15,
        runtime_signer: RuntimeIdSigner,
        period_signer: PeriodIdSigner,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
    ) -> None:
        validate_contract_registry_v1_1()
        self.parent_engine = parent_engine
        self.runtime_signer = runtime_signer
        self.period_signer = period_signer
        self.release = (
            validate_release_registry(release_registry)
            if release_registry is not None
            else None
        )
        self.enable_approved_runtime = bool(enable_approved_runtime)
        if self.enable_approved_runtime:
            if self.release is None:
                raise PeriodRuntimeError(
                    "PERIOD_RELEASE_REQUIRED", "승인된 기간 release가 필요합니다."
                )
            if not runtime_signer.production_key or not period_signer.production_key:
                raise PeriodRuntimeError(
                    "PERIOD_ID_KEY_INVALID", "운영 기간 Runtime에는 production signer가 필요합니다."
                )

    def calculate(
        self,
        state: Any,
        event: Any,
        *,
        expected_revision: int,
        reference_date: date,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.release is None:
            raise PeriodRuntimeError(
                "PERIOD_RELEASE_REQUIRED", "승인된 기간 release가 필요합니다."
            )
        if not self.enable_approved_runtime:
            raise PeriodRuntimeError(
                "PERIOD_FEATURE_DISABLED", "기간 Runtime은 기본 off입니다."
            )
        internal = calculate_daily_labels_candidate(
            state,
            event,
            expected_revision=expected_revision,
            reference_date=reference_date,
            parent_engine=self.parent_engine,
            runtime_signer=self.runtime_signer,
            period_signer=self.period_signer,
            authority_release_id=self.release["release_id"],
        )
        return internal, public_daily_label_result(internal)
