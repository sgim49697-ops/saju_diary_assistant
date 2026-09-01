# engine_v1_3.py - Skyfield candidate runtime과 구조화 절입 권한을 HMAC v2로 발급한다.

from __future__ import annotations

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

from .calendar_provider import CalendarProvider, KoreanLunarCalendarProvider
from .canonical import canonical_json_bytes, stable_id
from .contracts import POLICY_ID
from .contracts_v1_2 import ID_CONTRACT_VERSION_V2
from .contracts_v1_3 import (
    ENGINE_VERSION_V13,
    OUTPUT_SCHEMA_VERSION_V13,
    runtime_source_versions_v1_3,
    validate_contract_registry_v1_3,
)
from .engine import SajuRuntimeEngine
from .errors import RuntimeCalculationError
from .facts_v1_3 import build_chart_facts_v1_3, period_point_facts_v1_3
from .id_signer import RuntimeIdSigner
from .skyfield_solar_terms import SkyfieldSolarTermProvider
from .solar_term_types import (
    FORECAST_DIAGNOSTIC_NONAPPROVAL,
    PROFILE_DETERMINISTIC,
    SolarTermProvider,
    build_solar_term_evidence,
    merge_solar_term_evidence,
)
from .solar_terms_v1_3 import jie_boundaries_between_v1_3
from .timezone_resolver import resolve_local_datetime


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
        "engine_version": ENGINE_VERSION_V13,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


def _parse_hhmm(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


class _SkyfieldCandidateEngine(SajuRuntimeEngine):
    """불변 v1 엔진 파일을 수정하지 않고 Skyfield 계산 경로만 교체한다."""

    def __init__(
        self,
        *,
        calendar_provider: CalendarProvider | None,
        solar_term_provider: SolarTermProvider,
        source_versions: dict[str, str],
    ) -> None:
        self.enable_candidate_runtime = True
        self.solar_term_provider = solar_term_provider
        self.source_versions = deepcopy(source_versions)
        self.calendar_provider = calendar_provider or KoreanLunarCalendarProvider()
        self.source_versions["korean_lunar_calendar"] = (
            self.calendar_provider.provider_version
        )
        self._chart_cache: dict[str, dict[str, Any]] = {}

    def _candidate_datetimes(
        self, normalized: dict[str, Any]
    ) -> tuple[list[datetime], dict[str, Any]]:
        solar_date = date.fromisoformat(normalized["solar_birth_date"])
        precision = normalized["birth_time_precision"]
        if precision == "exact":
            minutes = [_parse_hhmm(normalized["local_birth_time"])]
        else:
            if precision == "range":
                first = _parse_hhmm(normalized["birth_time_range"]["start"])
                last = _parse_hhmm(normalized["birth_time_range"]["end"])
            else:
                first, last = 0, 1439
            boundary_minutes = {first, last}
            boundary_minutes.update(range(((first + 59) // 60) * 60, last + 1, 60))
            zone = ZoneInfo(normalized["iana_time_zone"])
            period_start = datetime.combine(
                solar_date, time(first // 60, first % 60), tzinfo=zone
            )
            period_end = datetime.combine(
                solar_date, time(last // 60, last % 60), tzinfo=zone
            )
            for boundary in jie_boundaries_between_v1_3(
                period_start,
                period_end,
                solar_term_provider=self.solar_term_provider,
            ):
                instant = datetime.fromisoformat(
                    str(boundary["instant_utc"]).replace("Z", "+00:00")
                ).astimezone(zone)
                minute = instant.hour * 60 + instant.minute
                boundary_minutes.update(
                    value
                    for value in (minute - 1, minute, minute + 1)
                    if first <= value <= last
                )
            transition_probe = sorted(
                {first, last, *range(((first + 29) // 30) * 30, last + 1, 30)}
            )
            probe_statuses: set[str] = set()
            probe_offsets: set[object] = set()
            for minute_of_day in transition_probe:
                probe_naive = datetime.combine(
                    solar_date,
                    time(hour=minute_of_day // 60, minute=minute_of_day % 60),
                )
                probe = resolve_local_datetime(
                    probe_naive,
                    timezone_name=normalized["iana_time_zone"],
                    fold=None,
                )
                probe_statuses.add(str(probe["status"]))
                for value in probe["candidates"]:
                    probe_offsets.add(value.utcoffset())
            if probe_statuses != {"unique"} or len(probe_offsets) > 1:
                minutes = list(range(first, last + 1))
            else:
                minutes = sorted(boundary_minutes)
        candidates: list[datetime] = []
        resolution_counts = {"unique": 0, "ambiguous": 0, "nonexistent": 0}
        for minute_of_day in minutes:
            naive = datetime.combine(
                solar_date,
                time(hour=minute_of_day // 60, minute=minute_of_day % 60),
            )
            resolved = resolve_local_datetime(
                naive,
                timezone_name=normalized["iana_time_zone"],
                fold=normalized.get("fold"),
            )
            status = str(resolved["status"])
            if status == "nonexistent":
                resolution_counts["nonexistent"] += 1
                if precision == "exact":
                    raise RuntimeCalculationError(
                        "NONEXISTENT_LOCAL_TIME",
                        "해당 현지시각은 DST 전환으로 존재하지 않으며 자동 이동하지 않습니다.",
                    )
                continue
            aware_values = list(resolved["candidates"])
            if status in {"ambiguous", "resolved_fold"}:
                resolution_counts["ambiguous"] += 1
                if status == "resolved_fold":
                    aware_values = [resolved["selected"]]
            else:
                resolution_counts["unique"] += 1
            candidates.extend(aware_values)
        if not candidates:
            raise RuntimeCalculationError(
                "NO_VALID_LOCAL_TIME", "지정 범위에 유효한 현지시각 후보가 없습니다."
            )
        return candidates, {
            "sampled_boundary_points": len(minutes),
            "resolved_instants": len(candidates),
            "resolution_counts": resolution_counts,
        }

    def _calculate_candidate_facts(
        self, normalized: dict[str, Any], candidates: list[datetime]
    ) -> list[dict[str, Any]]:
        solar_date = date.fromisoformat(normalized["solar_birth_date"])
        lunar_date = normalized["lunar_birth_date"]
        grouped: dict[bytes, dict[str, Any]] = {}
        for aware in candidates:
            facts = build_chart_facts_v1_3(
                local_datetime=aware,
                solar_date=solar_date,
                lunar_date=lunar_date,
                solar_term_provider=self.solar_term_provider,
            )
            key = canonical_json_bytes(facts)
            local_label = aware.strftime("%Y-%m-%dT%H:%M%z")
            if key not in grouped:
                grouped[key] = {
                    "hard_facts": facts,
                    "local_time_first": local_label,
                    "local_time_last": local_label,
                    "sample_count": 1,
                    "folds": [aware.fold],
                }
            else:
                item = grouped[key]
                item["local_time_last"] = local_label
                item["sample_count"] += 1
                item["folds"] = sorted(set(item["folds"] + [aware.fold]))
        values: list[dict[str, Any]] = []
        for item in grouped.values():
            candidate_preimage = {
                "normalized_birth_input": normalized,
                "facts": item["hard_facts"],
                "policy_id": POLICY_ID,
                "engine_version": ENGINE_VERSION_V13,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
                "source_versions": self.source_versions,
            }
            item["chart_id"] = stable_id("sc1_", candidate_preimage)
            values.append(item)
        return sorted(values, key=lambda item: item["chart_id"])

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        result = super().calculate_chart(arguments)
        if result["status"] == "blocked":
            return result
        evidence = [
            item.get("hard_facts", {}).get("solar_term_evidence")
            for item in result["alternative_charts"]
        ]
        if not evidence or any(not isinstance(value, dict) for value in evidence):
            return self._blocked(
                "SOLAR_TERM_EVIDENCE_INVALID",
                "후보 계산의 절입 근거가 완전하지 않습니다.",
                normalized_input=result.get("normalized_input"),
            )
        merged = merge_solar_term_evidence(evidence)
        for key in ("hard_facts", "stable_facts"):
            if isinstance(result.get(key), dict):
                result[key]["solar_term_evidence"] = deepcopy(merged)
        if isinstance(result.get("internal_trace"), dict):
            result["internal_trace"]["solar_term_provider"] = (
                self.solar_term_provider.provider_id
            )
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_tool_arguments("calculate_saju_period", arguments)
        except SajuContractError as exc:
            return self._blocked("INVALID_TOOL_ARGUMENTS", str(exc))
        if arguments["timezone"] != "Asia/Seoul":
            return self._blocked(
                "UNSUPPORTED_REGION", "기간 계산은 Asia/Seoul만 지원합니다."
            )
        if arguments["chart_id"] not in self._chart_cache:
            return self._blocked(
                "CHART_NOT_IN_PROCESS",
                "현재 process에서 확정한 단일 chart_id가 없어 기간 계산을 진행할 수 없습니다.",
            )
        start = date.fromisoformat(arguments["start_date"])
        end = date.fromisoformat(arguments["end_date"] or arguments["start_date"])
        if not (1900 <= start.year <= 2049 and 1900 <= end.year <= 2049):
            return self._blocked(
                "UNSUPPORTED_YEAR", "기간 계산 지원 연도는 1900~2049년입니다."
            )
        span_days = (end - start).days + 1
        maxima = {"day": 1, "week": 7, "month": 31, "year": 366}
        if span_days > maxima[arguments["period_type"]]:
            return self._blocked(
                "PERIOD_TOO_LARGE", "period_type보다 날짜 범위가 큽니다."
            )
        zone = ZoneInfo("Asia/Seoul")
        start_instant = datetime.combine(start, time(12, 0), tzinfo=zone)
        end_instant = datetime.combine(end, time(12, 0), tzinfo=zone)
        start_ganzhi = period_point_facts_v1_3(
            start,
            start_instant,
            solar_term_provider=self.solar_term_provider,
        )
        end_ganzhi = period_point_facts_v1_3(
            end,
            end_instant,
            solar_term_provider=self.solar_term_provider,
        )
        jie_boundaries = jie_boundaries_between_v1_3(
            datetime.combine(start, time.min, tzinfo=zone),
            datetime.combine(end, time.max, tzinfo=zone),
            solar_term_provider=self.solar_term_provider,
        )
        point_evidence: list[dict[str, Any]] = []
        for point in (start_ganzhi, end_ganzhi):
            evidence = point.pop("solar_term_evidence", None)
            if not isinstance(evidence, dict):
                return self._blocked(
                    "SOLAR_TERM_EVIDENCE_INVALID",
                    "기간 기준점의 절입 근거가 없습니다.",
                )
            point_evidence.append(evidence)
        boundary_evidence = [
            build_solar_term_evidence(
                self.solar_term_provider,
                (
                    (
                        "period_returned_jie",
                        self.solar_term_provider.boundary(
                            int(boundary["year"]), int(boundary["index"])
                        ),
                    ),
                ),
            )
            for boundary in jie_boundaries
        ]
        hard_facts: dict[str, Any] = {
            "period": {
                "period_type": arguments["period_type"],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "start_ganzhi": start_ganzhi,
                "end_ganzhi": end_ganzhi,
                "jie_boundaries": jie_boundaries,
            },
            "solar_term_evidence": merge_solar_term_evidence(
                [*point_evidence, *boundary_evidence]
            ),
        }
        preimage = {
            "arguments": arguments,
            "hard_facts": hard_facts,
            "engine_version": ENGINE_VERSION_V13,
            "policy_id": POLICY_ID,
            "source_versions": self.source_versions,
        }
        result = self._blocked("RUNTIME_GATE_PENDING", "runtime 승인이 보류 중입니다.")
        result.update(
            {
                "status": "partial",
                "hard_facts": hard_facts,
                "stable_facts": hard_facts,
                "fact_authority": "HARD_CANDIDATE",
                "calculation_run_id": stable_id("scr1_", preimage),
                "limitations": [
                    "기간의 날짜·간지만 반환하며 원국과의 관계 해석이나 미래 사건은 생성하지 않습니다.",
                    "Skyfield candidate runtime은 release 승인을 받지 않았습니다.",
                ],
                "internal_trace": {
                    "chart_id": arguments["chart_id"],
                    "cache": "in_process_only",
                    "solar_term_provider": self.solar_term_provider.provider_id,
                },
            }
        )
        return result


class SajuRuntimeEngineV13:
    """release 전 Skyfield provider를 명시적으로만 활성화하는 v1.3 engine."""

    def __init__(
        self,
        *,
        signer: RuntimeIdSigner,
        enable_candidate_runtime: bool = False,
        calendar_provider: CalendarProvider | None = None,
        ephemeris_path: Path | None = None,
        solar_term_provider: SkyfieldSolarTermProvider | None = None,
    ) -> None:
        validate_contract_registry_v1_3()
        if ephemeris_path is not None and solar_term_provider is not None:
            raise RuntimeCalculationError(
                "SOLAR_TERM_PROVIDER_SOURCE_AMBIGUOUS",
                "DE440s 경로와 주입 provider를 동시에 지정할 수 없습니다.",
            )
        self.signer = signer
        self.enable_candidate_runtime = bool(enable_candidate_runtime)
        self._owns_provider = False
        self._provider: SkyfieldSolarTermProvider | None = None
        if self.enable_candidate_runtime:
            if solar_term_provider is not None:
                self._provider = solar_term_provider
            elif ephemeris_path is not None:
                self._provider = SkyfieldSolarTermProvider(ephemeris_path)
                self._owns_provider = True
            else:
                raise RuntimeCalculationError(
                    "SOLAR_TERM_EPHEMERIS_REQUIRED",
                    "활성 v1.3 candidate runtime에는 DE440s 절대경로가 필요합니다.",
                )
        elif ephemeris_path is not None or solar_term_provider is not None:
            raise RuntimeCalculationError(
                "SOLAR_TERM_PROVIDER_WITH_DISABLED_RUNTIME",
                "비활성 candidate runtime에는 절입 provider를 열지 않습니다.",
            )
        identity = None if self._provider is None else self._provider.identity()
        try:
            self.source_versions = runtime_source_versions_v1_3(
                require_runtime_dependencies=self.enable_candidate_runtime,
                provider_identity=identity,
            )
            if self.enable_candidate_runtime:
                assert self._provider is not None
                self._candidate = _SkyfieldCandidateEngine(
                    calendar_provider=calendar_provider,
                    solar_term_provider=self._provider,
                    source_versions=self.source_versions,
                )
                self.source_versions = deepcopy(self._candidate.source_versions)
            else:
                self._candidate = SajuRuntimeEngine(
                    enable_candidate_runtime=False,
                    calendar_provider=calendar_provider,
                )
        except Exception:
            if self._owns_provider and self._provider is not None:
                self._provider.close()
            raise
        self._chart_to_candidate: dict[str, str] = {}

    def __enter__(self) -> SajuRuntimeEngineV13:  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        if self._owns_provider and self._provider is not None:
            self._provider.close()

    def _identity(self, normalized: dict[str, Any]) -> dict[str, Any]:
        return {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION_V13,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "source_versions": self.source_versions,
        }

    def _version_result(self, result: dict[str, Any]) -> dict[str, Any]:
        updated = deepcopy(result)
        updated.update(
            {
                "engine_version": ENGINE_VERSION_V13,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
                "id_contract_version": ID_CONTRACT_VERSION_V2,
                "source_versions": deepcopy(self.source_versions),
            }
        )
        if not self.enable_candidate_runtime and updated.get("code") == "RUNTIME_GATE_PENDING":
            message = "Skyfield candidate runtime은 기본 비활성화 상태입니다."
            updated.update(
                {
                    "code": "RUNTIME_FEATURE_DISABLED",
                    "message": message,
                    "limitations": [message],
                }
            )
        return updated

    @staticmethod
    def _evidence(result: dict[str, Any]) -> dict[str, Any]:
        facts = result.get("hard_facts")
        evidence = facts.get("solar_term_evidence") if isinstance(facts, dict) else None
        if not isinstance(evidence, dict):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "v1.3 결과에 절입 권한이 없습니다."
            )
        return evidence

    def _mark_candidate(self, result: dict[str, Any]) -> None:
        evidence = self._evidence(result)
        classes = set(evidence.get("authority_classes", []))
        warnings = [
            warning
            for warning in result.get("warnings", [])
            if warning.get("code") != "RUNTIME_GATE_PENDING"
        ]
        warnings.insert(
            0,
            {
                "code": "RUNTIME_RELEASE_PENDING",
                "message": "Skyfield candidate 계산은 완료됐지만 release·HARD_GT 승인은 하지 않았습니다.",
            },
        )
        limitations = [
            "v1.3 결과는 HARD_CANDIDATE이며 release·학습 Gold 승인이 아닙니다.",
            "신강약·격국·용신·대운·자동 해석은 계산하지 않습니다.",
        ]
        if FORECAST_DIAGNOSTIC_NONAPPROVAL in classes:
            warnings.append(
                {
                    "code": "FUTURE_SOLAR_TERM_FORECAST_NONAPPROVAL",
                    "message": "snapshot 수집시점 이후 절입은 지구 자전 예측 한계로 승인 근거가 아닙니다.",
                }
            )
            limitations.append(
                "미래 절입은 FORECAST_DIAGNOSTIC_NONAPPROVAL이며 HARD_GT로 승격할 수 없습니다."
            )
        if PROFILE_DETERMINISTIC in classes:
            warnings.append(
                {
                    "code": "SOLAR_TERM_OFFICIAL_COVERAGE_MISSING",
                    "message": "공식 절기 미coverage 구간은 PROFILE_DETERMINISTIC으로 계산했습니다.",
                }
            )
        result.update(
            {
                "status": "partial",
                "code": "RUNTIME_RELEASE_PENDING",
                "message": "Skyfield v1.3 candidate 계산과 절입 권한 분류를 완료했습니다.",
                "fact_authority": "HARD_CANDIDATE",
                "warnings": warnings,
                "limitations": limitations,
            }
        )

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        candidate = self._candidate.calculate_chart(arguments)
        result = self._version_result(candidate)
        if candidate["status"] == "blocked":
            return result
        normalized = deepcopy(candidate["normalized_input"])
        identity = self._identity(normalized)
        alternatives: list[dict[str, Any]] = []
        for item in candidate["alternative_charts"]:
            updated = deepcopy(item)
            new_id = self.signer.chart_id({**identity, "facts": updated["hard_facts"]})
            updated["chart_id"] = new_id
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
        self._mark_candidate(result)
        if chart_id is not None:
            candidate_chart_id = candidate.get("chart_id")
            if not isinstance(candidate_chart_id, str):
                raise RuntimeCalculationError(
                    "RUNTIME_ID_INVALID",
                    "내부 candidate chart_id가 없습니다.",
                )
            self._chart_to_candidate[chart_id] = candidate_chart_id
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
            "engine_version": ENGINE_VERSION_V13,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "policy_id": POLICY_ID,
            "source_versions": self.source_versions,
        }
        result["calculation_run_id"] = self.signer.calculation_run_id(preimage)
        if isinstance(result.get("internal_trace"), dict):
            result["internal_trace"]["chart_id"] = new_chart_id
        self._mark_candidate(result)
        result["limitations"] = [
            item
            for item in result["limitations"]
            if "신강약" not in item
        ]
        result["limitations"].append(
            "기간의 날짜·간지만 반환하며 원국 관계 해석이나 미래 사건은 생성하지 않습니다."
        )
        return result


def execute_candidate_runtime_tool_v1_3(
    engine: SajuRuntimeEngineV13,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if name == "calculate_saju_chart":
        internal = engine.calculate_chart(arguments)
    elif name == "calculate_saju_period":
        internal = engine.calculate_period(arguments)
    else:
        internal = _base_result(engine.source_versions)
        internal.update(
            {
                "code": "UNSUPPORTED_TOOL",
                "message": f"지원하지 않는 tool입니다: {name}",
                "limitations": [f"지원하지 않는 tool입니다: {name}"],
            }
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
