# engine.py - 한국 단일 profile 계산·불확실성·기간 간지를 in-process로 실행한다.

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.saju_contract import SajuContractError, validate_tool_arguments

from .calendar_provider import CalendarProvider, KoreanLunarCalendarProvider
from .canonical import canonical_json_bytes, stable_id
from .contracts import (
    ENGINE_VERSION,
    OUTPUT_SCHEMA_VERSION,
    POLICY_ID,
    runtime_source_versions,
    validate_contract_registry,
)
from .errors import RuntimeCalculationError
from .facts import build_chart_facts, period_point_facts
from .normalize import normalize_tool_birth_input
from .solar_terms import jie_boundaries_between
from .timezone_resolver import resolve_local_datetime

UTC = timezone.utc


def _intersection(values: list[Any]) -> Any:
    if not values:
        return None
    first = values[0]
    if all(value == first for value in values[1:]):
        return deepcopy(first)
    if all(isinstance(value, dict) for value in values):
        common_keys = set.intersection(*(set(value) for value in values))
        result: dict[str, Any] = {}
        for key in sorted(common_keys):
            nested = _intersection([value[key] for value in values])
            if nested not in ({}, None):
                result[key] = nested
        return result
    return None


def _parse_hhmm(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


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
        "engine_version": ENGINE_VERSION,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


class SajuRuntimeEngine:
    """승인 전에는 기본 차단되고 명시적인 candidate mode에서만 계산한다."""

    def __init__(
        self,
        *,
        enable_candidate_runtime: bool = False,
        calendar_provider: CalendarProvider | None = None,
    ) -> None:
        validate_contract_registry()
        self.enable_candidate_runtime = bool(enable_candidate_runtime)
        self.source_versions = runtime_source_versions(
            require_dependencies=enable_candidate_runtime
        )
        self.calendar_provider = calendar_provider
        if enable_candidate_runtime and self.calendar_provider is None:
            self.calendar_provider = KoreanLunarCalendarProvider()
        if self.calendar_provider is not None:
            self.source_versions["korean_lunar_calendar"] = (
                self.calendar_provider.provider_version
            )
        self._chart_cache: dict[str, dict[str, Any]] = {}

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
            for boundary in jie_boundaries_between(period_start, period_end):
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
                    probe_naive, timezone_name=normalized["iana_time_zone"], fold=None
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
            facts = build_chart_facts(
                local_datetime=aware,
                solar_date=solar_date,
                lunar_date=lunar_date,
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
                "engine_version": ENGINE_VERSION,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION,
                "source_versions": self.source_versions,
            }
            item["chart_id"] = stable_id("sc1_", candidate_preimage)
            values.append(item)
        return sorted(values, key=lambda item: item["chart_id"])

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        birthplace = arguments.get("birthplace")
        if isinstance(birthplace, dict) and (
            birthplace.get("country_code") != "KR"
            or birthplace.get("timezone") != "Asia/Seoul"
        ):
            return self._blocked(
                "UNSUPPORTED_REGION",
                "현재 runtime은 대한민국 출생·Asia/Seoul만 지원합니다.",
            )
        if not self.enable_candidate_runtime:
            return self._blocked(
                "RUNTIME_GATE_PENDING",
                "공식 전수 fixture와 절입 경계 Gate가 완료되지 않아 계산 기능이 기본 비활성화되어 있습니다.",
            )
        if self.calendar_provider is None:
            return self._blocked(
                "CALENDAR_DEPENDENCY_MISSING", "음양력 provider가 없습니다."
            )
        try:
            normalized = normalize_tool_birth_input(arguments, self.calendar_provider)
            aware_candidates, trace = self._candidate_datetimes(normalized)
            alternatives = self._calculate_candidate_facts(normalized, aware_candidates)
        except RuntimeCalculationError as exc:
            return self._blocked(exc.code, exc.message)

        facts = [item["hard_facts"] for item in alternatives]
        precision = normalized["birth_time_precision"]
        stable_facts = _intersection(facts)
        if not isinstance(stable_facts, dict):
            stable_facts = {}
        if precision == "unknown":
            stable_facts.setdefault("pillars", {})["hour"] = None
        exact_unique = (
            precision == "exact"
            and len(alternatives) == 1
            and trace["resolution_counts"]["ambiguous"] == 0
        )
        identity = {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION,
            "source_versions": self.source_versions,
        }
        birth_input_id = stable_id("sbi1_", normalized)
        chart_id = (
            stable_id("sc1_", {**identity, "facts": facts[0]}) if exact_unique else None
        )
        chart_set_id = (
            None
            if exact_unique
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
        warnings: list[dict[str, str]] = [
            {
                "code": "RUNTIME_GATE_PENDING",
                "message": "결과는 공식 전수 Gate 전 HARD_CANDIDATE이며 학습 Gold가 아닙니다.",
            }
        ]
        if precision == "unknown":
            warnings.append(
                {
                    "code": "BIRTH_TIME_UNKNOWN",
                    "message": "시주를 확정하지 않고 모든 현지 시각 후보의 공통 사실만 제공합니다.",
                }
            )
        if trace["resolution_counts"]["ambiguous"]:
            warnings.append(
                {
                    "code": "AMBIGUOUS_LOCAL_TIME",
                    "message": "DST fold 후보를 자동 선택하지 않고 모두 유지했습니다.",
                }
            )
        if trace["resolution_counts"]["nonexistent"]:
            warnings.append(
                {
                    "code": "NONEXISTENT_LOCAL_TIME_EXCLUDED",
                    "message": "DST gap에 속한 존재하지 않는 분은 이동하지 않고 후보에서 제외했습니다.",
                }
            )
        result = _base_result(self.source_versions)
        result.update(
            {
                "status": "partial",
                "code": "RUNTIME_GATE_PENDING",
                "message": "후보 계산은 완료됐지만 production·학습 Gold 승인은 보류 중입니다.",
                "normalized_input": normalized,
                "hard_facts": facts[0] if exact_unique else stable_facts,
                "stable_facts": facts[0] if exact_unique else stable_facts,
                "alternative_charts": alternatives,
                "uncertainty": {
                    "birth_time_precision": precision,
                    "candidate_count": len(alternatives),
                    "instant_candidate_count": trace["resolved_instants"],
                    "hour_pillar_confirmed": exact_unique,
                },
                "fact_authority": "HARD_CANDIDATE",
                "birth_input_id": birth_input_id,
                "chart_id": chart_id,
                "chart_set_id": chart_set_id,
                "calculation_run_id": calculation_run_id,
                "warnings": warnings,
                "limitations": [
                    "공식 KASI 1900~2049 전수 및 12절 경계 Gate 전 후보 결과입니다.",
                    "신강약·격국·용신·대운·자동 해석은 계산하지 않습니다.",
                ],
                "internal_trace": {
                    **trace,
                    "profile_id": POLICY_ID,
                    "candidate_runtime_explicitly_enabled": True,
                },
            }
        )
        if chart_id is not None:
            self._chart_cache[chart_id] = {
                "normalized_input": deepcopy(normalized),
                "hard_facts": deepcopy(result["hard_facts"]),
            }
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.enable_candidate_runtime:
            return self._blocked(
                "RUNTIME_GATE_PENDING",
                "runtime Gate 전에는 기간 계산도 기본 비활성화됩니다.",
            )
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
        hard_facts = {
            "period": {
                "period_type": arguments["period_type"],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "start_ganzhi": period_point_facts(start, start_instant),
                "end_ganzhi": period_point_facts(end, end_instant),
                "jie_boundaries": jie_boundaries_between(
                    datetime.combine(start, time.min, tzinfo=zone),
                    datetime.combine(end, time.max, tzinfo=zone),
                ),
            }
        }
        preimage = {
            "arguments": arguments,
            "hard_facts": hard_facts,
            "engine_version": ENGINE_VERSION,
            "policy_id": POLICY_ID,
            "source_versions": self.source_versions,
        }
        result = _base_result(self.source_versions)
        result.update(
            {
                "status": "partial",
                "code": "RUNTIME_GATE_PENDING",
                "message": "기간 간지 후보 계산은 완료됐지만 runtime Gate 승인은 보류 중입니다.",
                "hard_facts": hard_facts,
                "stable_facts": hard_facts,
                "fact_authority": "HARD_CANDIDATE",
                "calculation_run_id": stable_id("scr1_", preimage),
                "limitations": [
                    "기간의 날짜·간지만 반환하며 원국과의 관계 해석이나 미래 사건은 생성하지 않습니다.",
                    "공식 절입 경계 전수 Gate 전 후보 결과입니다.",
                ],
                "internal_trace": {
                    "chart_id": arguments["chart_id"],
                    "cache": "in_process_only",
                },
            }
        )
        return result
