# engine_v1_5.py - 승인 원국에 결합된 KST 정오 단일 일진만 제공한다.

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.saju_contract import (
    SajuContractError,
    project_model_visible_tool_result,
    validate_tool_arguments,
)

from .contracts import POLICY_ID
from .contracts_v1_2 import ID_CONTRACT_VERSION_V2
from .contracts_v1_4 import RELEASE_V14_PATH
from .contracts_v1_5 import (
    APPROVED_SCOPE_V15,
    ENGINE_VERSION_V15,
    OUTPUT_SCHEMA_VERSION_V15,
    SINGLE_DAY_END,
    SINGLE_DAY_END_DATE,
    SINGLE_DAY_START,
    runtime_source_versions_v1_5,
    validate_contract_registry_v1_5,
    validate_release_registry_v1_5,
)
from .engine_v1_4 import ApprovedSajuRuntimeEngineV14
from .errors import RuntimeCalculationError
from .facts_v1_3 import period_point_facts_v1_3
from .id_signer import RuntimeIdSigner
from .skyfield_solar_terms import SkyfieldSolarTermProvider

KST = ZoneInfo("Asia/Seoul")


def _base_result(source_versions: dict[str, str]) -> dict[str, Any]:
    return {
        "status": "blocked",
        "code": None,
        "message": None,
        "normalized_input": None,
        "hard_facts": None,
        "stable_facts": None,
        "alternative_charts": [],
        "uncertainty": None,
        "fact_authority": None,
        "birth_input_id": None,
        "chart_id": None,
        "chart_set_id": None,
        "calculation_run_id": None,
        "engine_version": ENGINE_VERSION_V15,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "runtime_scope": APPROVED_SCOPE_V15,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


def server_kst_today() -> date:
    return datetime.now(KST).date()


def effective_single_day_start(today: date) -> date:
    if not isinstance(today, date) or isinstance(today, datetime):
        raise RuntimeCalculationError(
            "SERVER_DATE_INVALID", "서버 KST 기준일을 확정할 수 없습니다."
        )
    return max(SINGLE_DAY_START, today)


def single_day_hard_facts_v1_5(
    value: date,
    *,
    provider: SkyfieldSolarTermProvider,
    release_id: str,
) -> dict[str, Any]:
    """정오의 연·월·일 간지와 공식 전수 대조 권한만 공개한다."""

    instant = datetime.combine(value, time(12, 0), tzinfo=KST)
    point = period_point_facts_v1_3(
        value,
        instant,
        solar_term_provider=provider,
    )
    evidence = point.pop("solar_term_evidence", None)
    if (
        set(point) != {"date", "year_ganzhi", "month_ganzhi", "day_ganzhi"}
        or point.get("date") != value.isoformat()
        or not all(
            isinstance(point.get(field), str) and point[field]
            for field in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        )
        or not isinstance(evidence, dict)
        or evidence.get("provider_id") != SkyfieldSolarTermProvider.provider_id
        or evidence.get("provider_generated_value_is_official") is not False
    ):
        raise RuntimeCalculationError(
            "SINGLE_DAY_EVIDENCE_INVALID", "단일 일진의 계산 근거가 계약과 다릅니다."
        )
    return {
        "period": {
            "period_type": "day",
            "target_date": value.isoformat(),
            "timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
            "year_ganzhi": point["year_ganzhi"],
            "month_ganzhi": point["month_ganzhi"],
            "day_ganzhi": point["day_ganzhi"],
        },
        "day_assignment_evidence": {
            "authority": "SOURCE_HARD_FACT",
            "official_day_oracle": "kasi_lunisolar_openapi",
            "official_year_month_oracle": "kasi_official_solar_terms_download",
            "provider_crosscheck": SkyfieldSolarTermProvider.provider_id,
            "provider_generated_value_is_official": False,
            "future_physical_instant_claimed": False,
            "release_id": release_id,
        },
    }


class ApprovedSajuRuntimeEngineV15:
    """유효 v1.5 release가 있을 때 원국과 단일 일진만 승인한다."""

    def __init__(
        self,
        *,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
        ephemeris_path: Path | None = None,
        signer: RuntimeIdSigner | None = None,
        id_key_file: Path | None = None,
        today_provider: Callable[[], date] = server_kst_today,
    ) -> None:
        validate_contract_registry_v1_5()
        self.enable_approved_runtime = bool(enable_approved_runtime)
        self.release = (
            validate_release_registry_v1_5(release_registry)
            if release_registry is not None
            else None
        )
        if signer is not None and id_key_file is not None:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_SOURCE_AMBIGUOUS",
                "approved runtime signer와 key 파일을 동시에 지정할 수 없습니다.",
            )
        active = self.enable_approved_runtime and self.release is not None
        if not active and any(
            item is not None for item in (ephemeris_path, signer, id_key_file)
        ):
            raise RuntimeCalculationError(
                "RUNTIME_RESOURCE_WITH_DISABLED_RUNTIME",
                "비활성 v1.5 runtime에는 ephemeris·key를 열지 않습니다.",
            )
        if not callable(today_provider):
            raise RuntimeCalculationError(
                "SERVER_DATE_INVALID", "서버 KST 기준일 provider가 callable이 아닙니다."
            )
        self._today_provider = today_provider
        self._chart_engine: ApprovedSajuRuntimeEngineV14 | None = None
        self._provider: SkyfieldSolarTermProvider | None = None
        self._signer: RuntimeIdSigner | None = None
        self._exact_chart_ids: set[str] = set()
        release_id = None if self.release is None else self.release["release_id"]
        release_sha = (
            None if self.release is None else self.release["release_registry_sha256"]
        )
        if active:
            if ephemeris_path is None:
                raise RuntimeCalculationError(
                    "SOLAR_TERM_EPHEMERIS_REQUIRED",
                    "활성 v1.5 runtime에는 고정 DE440s 절대경로가 필요합니다.",
                )
            production_signer = signer or RuntimeIdSigner.from_key_file(id_key_file)
            if not production_signer.production_key:
                raise RuntimeCalculationError(
                    "RUNTIME_ID_KEY_INVALID",
                    "approved runtime에는 production key signer가 필요합니다.",
                )
            chart_engine = ApprovedSajuRuntimeEngineV14(
                release_registry=RELEASE_V14_PATH,
                enable_approved_runtime=True,
                ephemeris_path=ephemeris_path,
                signer=production_signer,
            )
            provider = chart_engine._provider
            if provider is None:
                chart_engine.close()
                raise RuntimeCalculationError(
                    "RUNTIME_RESOURCE_REQUIRED", "v1.5 Skyfield provider가 준비되지 않았습니다."
                )
            try:
                source_versions = runtime_source_versions_v1_5(
                    require_runtime_dependencies=True,
                    provider_identity=provider.identity(),
                    release_id=release_id,
                    release_registry_sha256=release_sha,
                )
            except Exception:
                chart_engine.close()
                raise
            self._chart_engine = chart_engine
            self._provider = provider
            self._signer = production_signer
            self.source_versions = source_versions
        else:
            self.source_versions = runtime_source_versions_v1_5(
                require_runtime_dependencies=False,
                release_id=release_id,
                release_registry_sha256=release_sha,
            )

    def __enter__(self) -> ApprovedSajuRuntimeEngineV15:  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        self._exact_chart_ids.clear()
        if self._chart_engine is not None:
            self._chart_engine.close()

    def _blocked(
        self,
        code: str,
        message: str,
        *,
        normalized_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = _base_result(self.source_versions)
        result.update(
            {
                "code": code,
                "message": message,
                "normalized_input": deepcopy(normalized_input),
                "limitations": [message],
            }
        )
        return result

    def _availability_block(self) -> dict[str, Any] | None:
        if self.release is None:
            return self._blocked(
                "RUNTIME_RELEASE_REQUIRED",
                "통과한 conformance v10에 결합된 v1.5 release가 필요합니다.",
            )
        if not self.enable_approved_runtime:
            return self._blocked(
                "RUNTIME_FEATURE_DISABLED",
                "승인된 원국+단일 일진 runtime도 기본 off이며 명시적 feature flag가 필요합니다.",
            )
        if any(
            value is None
            for value in (self._chart_engine, self._provider, self._signer)
        ):
            return self._blocked(
                "RUNTIME_RESOURCE_REQUIRED", "v1.5 runtime 자원이 준비되지 않았습니다."
            )
        return None

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        assert self._chart_engine is not None
        assert self._signer is not None
        parent = self._chart_engine.calculate_chart(arguments)
        if parent.get("status") == "blocked":
            return self._blocked(
                str(parent.get("code") or "CHART_CALCULATION_BLOCKED"),
                str(parent.get("message") or "원국 계산이 차단됐습니다."),
                normalized_input=parent.get("normalized_input"),
            )
        normalized = parent.get("normalized_input")
        alternatives = parent.get("alternative_charts")
        if not isinstance(normalized, dict) or not isinstance(alternatives, list):
            return self._blocked(
                "CHART_PARENT_RESULT_INVALID", "v1.4 원국 결과 identity가 다릅니다."
            )
        identity = {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION_V15,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "runtime_scope": APPROVED_SCOPE_V15,
            "source_versions": self.source_versions,
        }
        promoted_alternatives: list[dict[str, Any]] = []
        for item in alternatives:
            if not isinstance(item, dict) or not isinstance(
                item.get("hard_facts"), dict
            ):
                return self._blocked(
                    "CHART_PARENT_RESULT_INVALID", "v1.4 후보 원국 형식이 다릅니다."
                )
            updated = deepcopy(item)
            updated["chart_id"] = self._signer.chart_id(
                {**identity, "facts": updated["hard_facts"]}
            )
            promoted_alternatives.append(updated)
        promoted_alternatives.sort(key=lambda item: item["chart_id"])
        exact = (
            normalized.get("birth_time_precision") == "exact"
            and len(promoted_alternatives) == 1
        )
        chart_id = promoted_alternatives[0]["chart_id"] if exact else None
        chart_set_id = (
            None
            if exact
            else self._signer.chart_set_id(
                {
                    **identity,
                    "candidate_chart_ids": [
                        item["chart_id"] for item in promoted_alternatives
                    ],
                }
            )
        )
        result = deepcopy(parent)
        result.update(
            {
                "message": (
                    "승인된 v1.5 runtime으로 원국 계산을 완료했습니다."
                    if exact
                    else "생시 불확실성을 유지하고 공통 원국 사실만 제공합니다."
                ),
                "alternative_charts": promoted_alternatives,
                "birth_input_id": self._signer.birth_input_id(normalized),
                "chart_id": chart_id,
                "chart_set_id": chart_set_id,
                "calculation_run_id": self._signer.calculation_run_id(
                    {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id}
                ),
                "engine_version": ENGINE_VERSION_V15,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
                "runtime_scope": APPROVED_SCOPE_V15,
                "source_versions": deepcopy(self.source_versions),
                "limitations": [
                    "원국과 단일 일진 라벨만 승인하며 대운·세운·미래 사건은 제공하지 않습니다."
                ],
                "internal_trace": {
                    **(parent.get("internal_trace") or {}),
                    "runtime_scope": APPROVED_SCOPE_V15,
                    "parent_chart_engine_version": parent.get("engine_version"),
                },
            }
        )
        if exact and chart_id is not None:
            self._exact_chart_ids.add(chart_id)
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        assert self._provider is not None
        assert self._signer is not None
        assert self.release is not None
        try:
            validate_tool_arguments("calculate_saju_period", arguments)
        except SajuContractError as exc:
            return self._blocked("INVALID_TOOL_ARGUMENTS", str(exc))
        if arguments["period_type"] != "day":
            return self._blocked(
                "SINGLE_DAY_PERIOD_TYPE_REQUIRED",
                "v1.5 release는 period_type=day인 단일 일진만 허용합니다.",
            )
        if arguments["timezone"] != "Asia/Seoul":
            return self._blocked(
                "UNSUPPORTED_REGION", "단일 일진은 Asia/Seoul만 지원합니다."
            )
        if arguments["chart_id"] not in self._exact_chart_ids:
            return self._blocked(
                "EXACT_CHART_NOT_IN_PROCESS",
                "현재 process에서 exact HARD_GT로 확정한 원국이 필요합니다.",
            )
        start = date.fromisoformat(arguments["start_date"])
        end = date.fromisoformat(arguments["end_date"] or arguments["start_date"])
        if end != start:
            return self._blocked(
                "SINGLE_DAY_RANGE_REQUIRED", "start_date와 end_date는 같아야 합니다."
            )
        try:
            today = self._today_provider()
            minimum = effective_single_day_start(today)
        except RuntimeCalculationError as exc:
            return self._blocked(exc.code, exc.message)
        if not minimum <= start <= SINGLE_DAY_END:
            return self._blocked(
                "SINGLE_DAY_OUT_OF_APPROVED_RANGE",
                f"단일 일진 날짜는 서버 KST 기준 {minimum.isoformat()}~{SINGLE_DAY_END_DATE}입니다.",
            )
        try:
            hard_facts = single_day_hard_facts_v1_5(
                start,
                provider=self._provider,
                release_id=self.release["release_id"],
            )
        except RuntimeCalculationError as exc:
            return self._blocked(exc.code, exc.message)
        normalized = {
            "period_type": "day",
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
            "timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
        }
        preimage = {
            "arguments": {**arguments, "end_date": start.isoformat()},
            "hard_facts": hard_facts,
            "engine_version": ENGINE_VERSION_V15,
            "policy_id": POLICY_ID,
            "runtime_scope": APPROVED_SCOPE_V15,
            "source_versions": self.source_versions,
        }
        result = _base_result(self.source_versions)
        result.update(
            {
                "status": "ok",
                "message": "승인된 v1.5 runtime으로 단일 일진 라벨을 계산했습니다.",
                "normalized_input": normalized,
                "hard_facts": hard_facts,
                "stable_facts": deepcopy(hard_facts),
                "fact_authority": "HARD_GT",
                "chart_id": arguments["chart_id"],
                "calculation_run_id": self._signer.calculation_run_id(preimage),
                "warnings": [
                    {
                        "code": "NO_FUTURE_PHYSICAL_INSTANT_CLAIM",
                        "message": "미래 절입의 물리 시각이 아니라 공식 전수 대조를 통과한 날짜 라벨만 제공합니다.",
                    }
                ],
                "limitations": [
                    "선택한 하루의 연주·월주·일주 라벨만 제공하며 원국 관계·대운·세운·사건 예측은 계산하지 않습니다.",
                    "건강·재정 등 현실 결과를 확정적으로 예측하지 않습니다.",
                ],
                "internal_trace": {
                    "chart_id": arguments["chart_id"],
                    "chart_cache": "current_process_exact_only",
                    "evaluation_local_time": "12:00",
                    "server_kst_today": today.isoformat(),
                    "provider": self._provider.provider_id,
                    "future_physical_instant_claimed": False,
                },
            }
        )
        return result


def execute_approved_runtime_tool_v1_5(
    engine: ApprovedSajuRuntimeEngineV15,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if name == "calculate_saju_chart":
        internal = engine.calculate_chart(arguments)
    elif name == "calculate_saju_period":
        internal = engine.calculate_period(arguments)
    else:
        internal = engine._blocked(
            "UNSUPPORTED_TOOL", f"지원하지 않는 tool입니다: {name}"
        )
    visible_input = {
        key: internal.get(key)
        for key in (
            "status",
            "hard_facts",
            "fact_authority",
            "code",
            "message",
            "limitations",
        )
        if internal.get(key) is not None
    }
    return internal, project_model_visible_tool_result(visible_input)
