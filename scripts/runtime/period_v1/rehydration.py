# rehydration.py - 암호화 session의 출생 slot으로 exact 원국을 재계산해 내부 권한을 복원한다.

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from scripts.runtime.calculation.contracts import POLICY_ID
from scripts.runtime.calculation.contracts_v1_5 import ENGINE_VERSION_V15
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.chart_day_adapter import (
    ChartDayAdapterError,
    _chart_arguments,
    _validate_chart_result,
    _validate_state,
    public_chart,
)

from .contracts import (
    CHART_AUTHORIZATION_VERSION,
    PARENT_RELEASE_ID,
    sha256_value,
    validate_chart_authorization,
    validate_contract_registry,
)
from .errors import PeriodRuntimeError


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PeriodRuntimeError(
            "PERIOD_STATE_REVISION_INVALID", "현재 session revision이 필요합니다."
        )
    return value


def _chart_fingerprints(chart: Mapping[str, Any]) -> dict[str, str]:
    normalized = chart.get("normalized_input")
    source_versions = chart.get("source_versions")
    if not isinstance(normalized, Mapping) or not isinstance(source_versions, Mapping):
        raise PeriodRuntimeError(
            "PERIOD_STORED_CHART_INVALID", "저장된 원국 identity가 없습니다."
        )
    try:
        hard_facts = public_chart(chart)["hard_facts"]
    except (KeyError, TypeError, ChartDayAdapterError) as exc:
        raise PeriodRuntimeError(
            "PERIOD_STORED_CHART_INVALID", "저장된 원국 사실을 검증할 수 없습니다."
        ) from exc
    return {
        "normalized_input_sha256": sha256_value(normalized),
        "public_hard_facts_sha256": sha256_value(hard_facts),
        "source_versions_sha256": sha256_value(source_versions),
    }


def rehydrate_exact_chart(
    state: Any,
    *,
    expected_revision: int,
    engine: ApprovedSajuRuntimeEngineV15,
    signer: RuntimeIdSigner,
) -> dict[str, Any]:
    """새 process에서 저장 원국을 신뢰하지 않고 같은 입력으로 다시 계산한다."""
    validate_contract_registry()
    revision = _revision(expected_revision)
    if not isinstance(engine, ApprovedSajuRuntimeEngineV15):
        raise PeriodRuntimeError(
            "PERIOD_RUNTIME_ENGINE_INVALID", "v1.5 Runtime engine이 필요합니다."
        )
    if not isinstance(signer, RuntimeIdSigner):
        raise PeriodRuntimeError(
            "PERIOD_RUNTIME_SIGNER_INVALID", "검증된 Runtime signer가 필요합니다."
        )
    try:
        current = _validate_state(deepcopy(state))
    except ChartDayAdapterError as exc:
        raise PeriodRuntimeError(
            "PERIOD_SESSION_STATE_INVALID", "암호화 session 상태가 다릅니다."
        ) from exc
    if current["state_revision"] != revision:
        raise PeriodRuntimeError(
            "PERIOD_STALE_REVISION", "session revision이 변경됐습니다."
        )
    if current.get("saju_opt_in") is not True:
        raise PeriodRuntimeError(
            "PERIOD_OPT_IN_REQUIRED", "원국 계산 동의가 유지된 session이 필요합니다."
        )
    slots = current["birth_slots"]
    if slots.get("time_precision") != "exact" or not isinstance(
        slots.get("birth_time"), str
    ):
        raise PeriodRuntimeError(
            "PERIOD_EXACT_CHART_REQUIRED", "기간 계산에는 exact 출생시각이 필요합니다."
        )
    stored = current.get("chart")
    release = engine.release
    if (
        not isinstance(stored, Mapping)
        or stored.get("status") != "ok"
        or stored.get("fact_authority") != "HARD_GT"
        or stored.get("engine_version") != ENGINE_VERSION_V15
        or stored.get("policy_id") != POLICY_ID
        or not isinstance(stored.get("chart_id"), str)
        or not isinstance(release, Mapping)
        or release.get("release_id") != PARENT_RELEASE_ID
        or not isinstance(stored.get("source_versions"), Mapping)
        or stored["source_versions"].get("runtime_release") != PARENT_RELEASE_ID
    ):
        raise PeriodRuntimeError(
            "PERIOD_STORED_CHART_INVALID", "저장된 exact 원국 권한이 다릅니다."
        )

    try:
        arguments = _chart_arguments(current)
        recomputed = engine.calculate_chart(arguments)
        _validate_chart_result(
            recomputed,
            arguments=arguments,
            signer=signer,
            release_id=PARENT_RELEASE_ID,
        )
    except (ChartDayAdapterError, RuntimeCalculationError) as exc:
        raise PeriodRuntimeError(
            "PERIOD_CHART_RECOMPUTATION_FAILED", "원국 재계산 검증에 실패했습니다."
        ) from exc
    if recomputed.get("status") != "ok" or recomputed.get("fact_authority") != "HARD_GT":
        raise PeriodRuntimeError(
            "PERIOD_CHART_RECOMPUTATION_BLOCKED", "exact 원국을 다시 확정하지 못했습니다."
        )

    stored_fingerprints = _chart_fingerprints(stored)
    recomputed_fingerprints = _chart_fingerprints(recomputed)
    if (
        stored.get("chart_id") != recomputed.get("chart_id")
        or stored.get("birth_input_id") != recomputed.get("birth_input_id")
        or stored.get("calculation_run_id") != recomputed.get("calculation_run_id")
        or stored_fingerprints != recomputed_fingerprints
    ):
        raise PeriodRuntimeError(
            "PERIOD_CHART_REHYDRATION_MISMATCH",
            "저장 원국과 재계산 원국 identity가 일치하지 않습니다.",
        )

    return validate_chart_authorization(
        {
            "authorization_version": CHART_AUTHORIZATION_VERSION,
            "chart_id": recomputed["chart_id"],
            "state_revision": revision,
            **recomputed_fingerprints,
            "release_id": PARENT_RELEASE_ID,
            "engine_version": ENGINE_VERSION_V15,
            "policy_id": POLICY_ID,
            "fact_authority": "HARD_GT",
            "publicly_exposable": False,
        }
    )
