# engine_v1_2.py - v1 계산 결과의 모든 출생 파생 ID를 HMAC v2로 재발급한다.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.runtime.saju_contract import project_model_visible_tool_result

from .calendar_provider import CalendarProvider
from .contracts import POLICY_ID
from .contracts_v1_2 import (
    ENGINE_VERSION_V12,
    ID_CONTRACT_VERSION_V2,
    OUTPUT_SCHEMA_VERSION_V12,
    runtime_source_versions_v1_2,
    validate_contract_registry_v1_2,
    validate_release_registry_v1_2,
)
from .engine import SajuRuntimeEngine
from .errors import RuntimeCalculationError
from .id_signer import RuntimeIdSigner


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
        "engine_version": ENGINE_VERSION_V12,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


class SajuRuntimeEngineV12:
    """기존 계산 의미는 보존하고 출력 identity만 versioned HMAC 계약으로 만든다."""

    def __init__(
        self,
        *,
        signer: RuntimeIdSigner,
        enable_candidate_runtime: bool = False,
        calendar_provider: CalendarProvider | None = None,
        source_versions: dict[str, str] | None = None,
    ) -> None:
        validate_contract_registry_v1_2()
        self.signer = signer
        self.source_versions = deepcopy(
            source_versions
            if source_versions is not None
            else runtime_source_versions_v1_2(
                require_runtime_dependencies=enable_candidate_runtime
            )
        )
        self._candidate = SajuRuntimeEngine(
            enable_candidate_runtime=enable_candidate_runtime,
            calendar_provider=calendar_provider,
        )
        self.enable_candidate_runtime = bool(enable_candidate_runtime)
        self._chart_to_candidate: dict[str, str] = {}

    def _identity(self, normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION_V12,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V12,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "source_versions": self.source_versions,
        }

    def _version_result(self, result: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(result)
        updated.update(
            {
                "engine_version": ENGINE_VERSION_V12,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V12,
                "id_contract_version": ID_CONTRACT_VERSION_V2,
                "source_versions": deepcopy(self.source_versions),
            }
        )
        return updated

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidate.calculate_chart(arguments)
        result = self._version_result(candidate)
        if candidate["status"] == "blocked":
            return result
        normalized = deepcopy(candidate["normalized_input"])
        identity = self._identity(normalized)
        alternatives: list[dict[str, Any]] = []
        new_to_old: dict[str, str] = {}
        for item in candidate["alternative_charts"]:
            updated = deepcopy(item)
            old_id = updated["chart_id"]
            new_id = self.signer.chart_id({**identity, "facts": updated["hard_facts"]})
            updated["chart_id"] = new_id
            new_to_old[new_id] = old_id
            alternatives.append(updated)
        alternatives.sort(key=lambda item: item["chart_id"])
        exact = (
            candidate.get("uncertainty", {}).get("birth_time_precision") == "exact"
            and candidate.get("chart_id") is not None
            and len(alternatives) == 1
        )
        chart_id = alternatives[0]["chart_id"] if exact else None
        chart_set_id = (
            None
            if exact
            else self.signer.chart_set_id(
                {
                    **identity,
                    "candidate_chart_ids": [item["chart_id"] for item in alternatives],
                }
            )
        )
        calculation_run_id = self.signer.calculation_run_id(
            {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id}
        )
        result.update(
            {
                "alternative_charts": alternatives,
                "birth_input_id": self.signer.birth_input_id(normalized),
                "chart_id": chart_id,
                "chart_set_id": chart_set_id,
                "calculation_run_id": calculation_run_id,
            }
        )
        if chart_id is not None:
            self._chart_to_candidate[chart_id] = new_to_old[chart_id]
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        new_chart_id = arguments.get("chart_id")
        candidate_id = self._chart_to_candidate.get(str(new_chart_id))
        candidate_arguments = (
            {**arguments, "chart_id": candidate_id}
            if candidate_id is not None
            else arguments
        )
        candidate = self._candidate.calculate_period(candidate_arguments)
        result = self._version_result(candidate)
        if candidate["status"] == "blocked":
            return result
        hard_facts = deepcopy(candidate["hard_facts"])
        preimage = {
            "arguments": arguments,
            "hard_facts": hard_facts,
            "engine_version": ENGINE_VERSION_V12,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V12,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "policy_id": POLICY_ID,
            "source_versions": self.source_versions,
        }
        result["calculation_run_id"] = self.signer.calculation_run_id(preimage)
        if isinstance(result.get("internal_trace"), dict):
            result["internal_trace"]["chart_id"] = new_chart_id
        return result


class ApprovedSajuRuntimeEngineV12:
    """유효 release와 production signer가 모두 있을 때만 v1.2 권위를 발급한다."""

    def __init__(
        self,
        *,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
        signer: RuntimeIdSigner | None = None,
        id_key_file: Path | None = None,
    ) -> None:
        validate_contract_registry_v1_2()
        self.enable_approved_runtime = bool(enable_approved_runtime)
        self.release = (
            validate_release_registry_v1_2(release_registry)
            if release_registry is not None
            else None
        )
        if signer is not None and id_key_file is not None:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_SOURCE_AMBIGUOUS",
                "approved runtime signer와 key 파일을 동시에 지정할 수 없습니다.",
            )
        active = self.enable_approved_runtime and self.release is not None
        self.source_versions = runtime_source_versions_v1_2(
            require_runtime_dependencies=active
        )
        self._engine: SajuRuntimeEngineV12 | None = None
        if active:
            production_signer = signer or RuntimeIdSigner.from_key_file(id_key_file)
            if not production_signer.production_key:
                raise RuntimeCalculationError(
                    "RUNTIME_ID_KEY_INVALID",
                    "approved runtime에는 production key 파일에서 읽은 signer가 필요합니다.",
                )
            self.source_versions["runtime_release"] = self.release["release_id"]
            self._engine = SajuRuntimeEngineV12(
                signer=production_signer,
                enable_candidate_runtime=True,
                source_versions=self.source_versions,
            )

    def _blocked(self, code: str, message: str) -> dict[str, Any]:
        result = _base_result(self.source_versions)
        result.update({"code": code, "message": message, "limitations": [message]})
        return result

    def _availability_block(self) -> dict[str, Any] | None:
        if self.release is None:
            return self._blocked(
                "RUNTIME_RELEASE_REQUIRED",
                "통과한 conformance v4에 결합된 v1.2 release registry가 필요합니다.",
            )
        if not self.enable_approved_runtime:
            return self._blocked(
                "RUNTIME_FEATURE_DISABLED",
                "승인된 runtime도 기본 off이며 명시적인 feature flag가 필요합니다.",
            )
        if self._engine is None:
            return self._blocked(
                "RUNTIME_ID_KEY_REQUIRED", "production HMAC ID key가 준비되지 않았습니다."
            )
        return None

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        assert self._engine is not None
        result = self._engine.calculate_chart(arguments)
        if result["status"] == "blocked":
            result["fact_authority"] = None
            return result
        exact = result.get("chart_id") is not None
        result.update(
            {
                "status": "ok" if exact else "partial",
                "code": None if exact else "BIRTH_TIME_UNCERTAIN",
                "message": (
                    "승인된 v1.2 runtime으로 원국 계산을 완료했습니다."
                    if exact
                    else "생시 불확실성을 유지한 채 모든 후보의 공통 사실만 제공합니다."
                ),
                "fact_authority": "HARD_GT" if exact else "POLICY_BOUND_RULE",
                "warnings": [
                    warning
                    for warning in result["warnings"]
                    if warning.get("code") != "RUNTIME_GATE_PENDING"
                ],
                "limitations": [
                    "신강약·격국·용신·대운·자동 해석은 계산하지 않습니다."
                ],
            }
        )
        if not exact:
            result["warnings"].append(
                {
                    "code": "BIRTH_TIME_UNCERTAIN",
                    "message": "대표 시각을 추측하지 않아 시주와 후보별 사실은 확정하지 않습니다.",
                }
            )
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        assert self._engine is not None
        result = self._engine.calculate_period(arguments)
        if result["status"] == "blocked":
            result["fact_authority"] = None
            return result
        result.update(
            {
                "status": "ok",
                "code": None,
                "message": "승인된 v1.2 runtime으로 기간 간지를 계산했습니다.",
                "fact_authority": "HARD_GT",
                "warnings": [],
                "limitations": [
                    "기간의 날짜·간지만 반환하며 원국 관계 해석이나 미래 사건은 생성하지 않습니다."
                ],
            }
        )
        return result


def execute_approved_runtime_tool_v1_2(
    engine: ApprovedSajuRuntimeEngineV12,
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
