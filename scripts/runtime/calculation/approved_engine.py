# approved_engine.py - 유효한 v1.1 release에서만 HARD_GT/POLICY_BOUND_RULE 결과를 발급한다.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.runtime.saju_contract import project_model_visible_tool_result

from .canonical import stable_id
from .contracts import POLICY_ID
from .contracts_v1_1 import (
    ENGINE_VERSION_V11,
    OUTPUT_SCHEMA_VERSION_V11,
    runtime_source_versions_v1_1,
    validate_contract_registry_v1_1,
    validate_release_registry,
)
from .engine import SajuRuntimeEngine


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
        "engine_version": ENGINE_VERSION_V11,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V11,
        "policy_id": POLICY_ID,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


class ApprovedSajuRuntimeEngine:
    """정적 config가 아니라 통과 보고서에 결합된 release로 승인 상태를 정한다."""

    def __init__(
        self,
        *,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
    ) -> None:
        validate_contract_registry_v1_1()
        self.enable_approved_runtime = bool(enable_approved_runtime)
        self.release = (
            validate_release_registry(release_registry)
            if release_registry is not None
            else None
        )
        active = self.enable_approved_runtime and self.release is not None
        self.source_versions = runtime_source_versions_v1_1(
            require_runtime_dependencies=active
        )
        if self.release is not None:
            self.source_versions["runtime_release"] = self.release["release_id"]
            for name, identity in sorted(self.release["official_snapshots"].items()):
                if isinstance(identity, dict) and isinstance(identity.get("sha256"), str):
                    self.source_versions[f"official_{name}_sha256"] = identity["sha256"]
        self._candidate = SajuRuntimeEngine(enable_candidate_runtime=active)
        self._approved_to_candidate_chart: dict[str, str] = {}

    def _blocked(self, code: str, message: str) -> dict[str, Any]:
        result = _base_result(self.source_versions)
        result.update({"code": code, "message": message, "limitations": [message]})
        return result

    def _availability_block(self) -> dict[str, Any] | None:
        if self.release is None:
            return self._blocked(
                "RUNTIME_RELEASE_REQUIRED",
                "통과한 conformance v3에 결합된 runtime release registry가 필요합니다.",
            )
        if not self.enable_approved_runtime:
            return self._blocked(
                "RUNTIME_FEATURE_DISABLED",
                "승인된 runtime도 기본 off이며 명시적인 feature flag가 필요합니다.",
            )
        return None

    def _identity(self, normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION_V11,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V11,
            "source_versions": self.source_versions,
        }

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        candidate = self._candidate.calculate_chart(arguments)
        if candidate["status"] == "blocked":
            result = _base_result(self.source_versions)
            result.update(
                {
                    "code": candidate["code"],
                    "message": candidate["message"],
                    "normalized_input": candidate["normalized_input"],
                    "limitations": candidate["limitations"],
                }
            )
            return result
        normalized = deepcopy(candidate["normalized_input"])
        identity = self._identity(normalized)
        alternatives: list[dict[str, Any]] = []
        candidate_ids: dict[str, str] = {}
        for item in candidate["alternative_charts"]:
            updated = deepcopy(item)
            candidate_id = updated["chart_id"]
            approved_id = stable_id(
                "sc1_", {**identity, "facts": updated["hard_facts"]}
            )
            updated["chart_id"] = approved_id
            candidate_ids[approved_id] = candidate_id
            alternatives.append(updated)
        exact = (
            candidate.get("uncertainty", {}).get("birth_time_precision") == "exact"
            and candidate.get("chart_id") is not None
            and len(alternatives) == 1
        )
        chart_id = alternatives[0]["chart_id"] if exact else None
        chart_set_id = (
            None
            if exact
            else stable_id(
                "scs1_",
                {
                    **identity,
                    "candidate_chart_ids": [item["chart_id"] for item in alternatives],
                },
            )
        )
        calculation_run_id = stable_id(
            "scr1_",
            {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id},
        )
        authority = "HARD_GT" if exact else "POLICY_BOUND_RULE"
        status = "ok" if exact else "partial"
        code = None if exact else "BIRTH_TIME_UNCERTAIN"
        message = (
            "승인된 runtime으로 원국 계산을 완료했습니다."
            if exact
            else "생시 불확실성을 유지한 채 모든 후보의 공통 사실만 제공합니다."
        )
        warnings = [
            warning
            for warning in deepcopy(candidate["warnings"])
            if warning.get("code") != "RUNTIME_GATE_PENDING"
        ]
        if not exact:
            warnings.append(
                {
                    "code": "BIRTH_TIME_UNCERTAIN",
                    "message": "대표 시각을 추측하지 않아 시주와 후보별 사실은 확정하지 않습니다.",
                }
            )
        limitations = [
            "신강약·격국·용신·대운·자동 해석은 계산하지 않습니다."
        ]
        if not exact:
            limitations.append(
                "POLICY_BOUND_RULE은 입력 불확실성 안에서 공통인 사실이며 단일 원국 HARD_GT가 아닙니다."
            )
        result = _base_result(self.source_versions)
        result.update(
            {
                "status": status,
                "code": code,
                "message": message,
                "normalized_input": normalized,
                "hard_facts": deepcopy(candidate["hard_facts"]),
                "stable_facts": deepcopy(candidate["stable_facts"]),
                "alternative_charts": alternatives,
                "uncertainty": deepcopy(candidate["uncertainty"]),
                "fact_authority": authority,
                "birth_input_id": stable_id("sbi1_", normalized),
                "chart_id": chart_id,
                "chart_set_id": chart_set_id,
                "calculation_run_id": calculation_run_id,
                "warnings": warnings,
                "limitations": limitations,
                "internal_trace": {
                    **deepcopy(candidate["internal_trace"]),
                    "candidate_runtime_explicitly_enabled": False,
                    "approved_runtime_explicitly_enabled": True,
                    "release_id": self.release["release_id"],
                    "release_registry_sha256": self.release[
                        "release_registry_sha256"
                    ],
                },
            }
        )
        if chart_id is not None:
            candidate_chart_id = candidate["chart_id"]
            self._approved_to_candidate_chart[chart_id] = candidate_chart_id
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        chart_id = arguments.get("chart_id")
        candidate_chart_id = self._approved_to_candidate_chart.get(str(chart_id))
        if candidate_chart_id is None:
            return self._blocked(
                "CHART_NOT_IN_PROCESS",
                "현재 process에서 승인된 exact chart_id가 없어 기간 계산을 진행할 수 없습니다.",
            )
        candidate_arguments = {**arguments, "chart_id": candidate_chart_id}
        candidate = self._candidate.calculate_period(candidate_arguments)
        if candidate["status"] == "blocked":
            return self._blocked(candidate["code"], candidate["message"])
        hard_facts = deepcopy(candidate["hard_facts"])
        preimage = {
            "arguments": arguments,
            "hard_facts": hard_facts,
            "engine_version": ENGINE_VERSION_V11,
            "policy_id": POLICY_ID,
            "source_versions": self.source_versions,
        }
        result = _base_result(self.source_versions)
        result.update(
            {
                "status": "ok",
                "message": "승인된 runtime으로 기간 간지를 계산했습니다.",
                "hard_facts": hard_facts,
                "stable_facts": hard_facts,
                "fact_authority": "HARD_GT",
                "calculation_run_id": stable_id("scr1_", preimage),
                "limitations": [
                    "기간의 날짜·간지만 반환하며 원국 관계 해석이나 미래 사건은 생성하지 않습니다."
                ],
                "internal_trace": {
                    "chart_id": chart_id,
                    "cache": "in_process_only",
                    "release_id": self.release["release_id"],
                },
            }
        )
        return result


def execute_approved_runtime_tool(
    engine: ApprovedSajuRuntimeEngine,
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
