# engine_v1_4.py - 과거 공식 구간 원국만 승인하는 chart-only runtime을 제공한다.

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.saju_contract import project_model_visible_tool_result

from .calendar_provider import KoreanLunarCalendarProvider
from .contracts import POLICY_ID
from .contracts_v1_2 import ID_CONTRACT_VERSION_V2
from .contracts_v1_4 import (
    APPROVED_END_DATE,
    APPROVED_SCOPE_V14,
    APPROVED_START_DATE,
    ENGINE_VERSION_V14,
    KASI_PAST_UNCERTAINTY_SECONDS,
    OUTPUT_SCHEMA_VERSION_V14,
    runtime_source_versions_v1_4,
    validate_contract_registry_v1_4,
    validate_release_registry_v1_4,
)
from .engine_v1_3 import SajuRuntimeEngineV13
from .errors import RuntimeCalculationError
from .id_signer import RuntimeIdSigner
from .normalize import normalize_tool_birth_input
from .skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from .solar_term_types import (
    PAST_OFFICIAL_CORROBORATED,
    SOURCE_HARD_FACT,
    SolarTermBoundary,
)
from .solar_terms import JIE_TO_MONTH
from .timezone_resolver import resolve_local_datetime

UTC = timezone.utc
APPROVED_START = date.fromisoformat(APPROVED_START_DATE)
APPROVED_END = date.fromisoformat(APPROVED_END_DATE)
OFFICIAL_CUTOFF_UTC = datetime.fromisoformat(OFFICIAL_SNAPSHOT_COLLECTED_AT).astimezone(
    UTC
)


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
        "engine_version": ENGINE_VERSION_V14,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V14,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "runtime_scope": APPROVED_SCOPE_V14,
        "source_versions": deepcopy(source_versions),
        "warnings": [],
        "limitations": [],
        "internal_trace": None,
    }


def is_approved_solar_date(value: date) -> bool:
    return APPROVED_START <= value <= APPROVED_END


def _parse_hhmm(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _precision_minute_range(normalized: dict[str, Any]) -> tuple[int, int]:
    precision = normalized["birth_time_precision"]
    if precision == "exact":
        value = _parse_hhmm(normalized["local_birth_time"])
        return value, value
    if precision == "range":
        value = normalized["birth_time_range"]
        return _parse_hhmm(value["start"]), _parse_hhmm(value["end"])
    return 0, 1439


def _parse_evidence_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 UTC 형식이 다릅니다."
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 UTC 형식이 다릅니다."
        ) from exc
    if parsed.utcoffset() != timedelta(0):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 timezone이 UTC가 아닙니다."
        )
    return parsed


def _validate_past_official_evidence(facts: Any, *, label: str) -> None:
    evidence = facts.get("solar_term_evidence") if isinstance(facts, dict) else None
    if not isinstance(evidence, dict):
        raise RuntimeCalculationError(
            "CHART_ONLY_OFFICIAL_EVIDENCE_REQUIRED",
            f"{label}에 절입 공식 근거가 없습니다.",
        )
    if (
        evidence.get("provider_id") != SkyfieldSolarTermProvider.provider_id
        or evidence.get("root_time_scale") != "TT"
        or evidence.get("boundary_comparison_time_scale") != "TT"
        or evidence.get("official_label_coordinate")
        != "UT1_NOMINAL_PLUS_FIXED_KST"
        or evidence.get("official_snapshot_collected_at")
        != OFFICIAL_SNAPSHOT_COLLECTED_AT
        or evidence.get("provider_generated_value_is_official") is not False
        or evidence.get("authority_classes") != [PAST_OFFICIAL_CORROBORATED]
        or evidence.get("overall_authority") != PAST_OFFICIAL_CORROBORATED
        or evidence.get("contains_future_nonapproval") is not False
    ):
        raise RuntimeCalculationError(
            "CHART_ONLY_OFFICIAL_EVIDENCE_REQUIRED",
            f"{label}은 과거 공식 근거 단일 권한이 아닙니다.",
        )
    boundaries = evidence.get("boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", f"{label} 절입 경계가 비었습니다."
        )
    for boundary in boundaries:
        if (
            not isinstance(boundary, dict)
            or boundary.get("authority_class") != PAST_OFFICIAL_CORROBORATED
            or boundary.get("official_source_evidence_class") != SOURCE_HARD_FACT
            or boundary.get("provider_generated_value_is_official") is not False
            or _parse_evidence_utc(boundary.get("instant_utc")) > OFFICIAL_CUTOFF_UTC
        ):
            raise RuntimeCalculationError(
                "CHART_ONLY_OFFICIAL_EVIDENCE_REQUIRED",
                f"{label} 절입 경계가 과거 공식 근거 범위를 벗어납니다.",
            )


def validate_chart_only_candidate(
    result: dict[str, Any], *, normalized: dict[str, Any]
) -> None:
    """v1.3 후보가 chart-only 승격에 필요한 근거를 모두 가졌는지 검증한다."""

    if (
        result.get("status") != "partial"
        or result.get("fact_authority") != "HARD_CANDIDATE"
        or result.get("normalized_input") != normalized
        or result.get("source_versions", {}).get("solar_term_provider")
        != SkyfieldSolarTermProvider.provider_id
    ):
        raise RuntimeCalculationError(
            "CHART_ONLY_CANDIDATE_INVALID", "chart-only 후보 결과 identity가 다릅니다."
        )
    alternatives = result.get("alternative_charts")
    if not isinstance(alternatives, list) or not alternatives:
        raise RuntimeCalculationError(
            "CHART_ONLY_CANDIDATE_INVALID", "chart-only 후보 원국이 비었습니다."
        )
    for index, alternative in enumerate(alternatives):
        if not isinstance(alternative, dict):
            raise RuntimeCalculationError(
                "CHART_ONLY_CANDIDATE_INVALID", "후보 원국 형식이 다릅니다."
            )
        _validate_past_official_evidence(
            alternative.get("hard_facts"), label=f"alternative_charts[{index}]"
        )
    _validate_past_official_evidence(result.get("hard_facts"), label="hard_facts")
    _validate_past_official_evidence(
        result.get("stable_facts"), label="stable_facts"
    )


def boundary_uncertainty_hits(
    normalized: dict[str, Any],
    provider: SkyfieldSolarTermProvider,
) -> list[dict[str, Any]]:
    """입력의 분 격자가 과거 공식 절입 root ±1초와 겹치는지 찾는다."""

    solar_date = date.fromisoformat(normalized["solar_birth_date"])
    first_minute, last_minute = _precision_minute_range(normalized)
    zone = ZoneInfo(normalized["iana_time_zone"])
    hits: dict[tuple[int, int, str], dict[str, Any]] = {}
    for year in range(solar_date.year - 1, solar_date.year + 2):
        for term_index in sorted(JIE_TO_MONTH):
            boundary = provider.boundary(year, term_index)
            if boundary.authority_class != PAST_OFFICIAL_CORROBORATED:
                continue
            local_root = boundary.instant_utc.astimezone(zone)
            floor_value = local_root.replace(second=0, microsecond=0)
            labels = {floor_value, floor_value + timedelta(minutes=1)}
            for label in labels:
                if label.date() != solar_date:
                    continue
                minute_of_day = label.hour * 60 + label.minute
                if not first_minute <= minute_of_day <= last_minute:
                    continue
                naive = label.replace(tzinfo=None)
                resolved = resolve_local_datetime(
                    naive,
                    timezone_name=normalized["iana_time_zone"],
                    fold=normalized.get("fold"),
                )
                for candidate in resolved["candidates"]:
                    distance = abs(
                        (candidate.astimezone(UTC) - boundary.instant_utc).total_seconds()
                    )
                    if distance <= KASI_PAST_UNCERTAINTY_SECONDS:
                        key = (boundary.year, boundary.term_index, label.isoformat())
                        hits[key] = {
                            "year": boundary.year,
                            "term_index": boundary.term_index,
                            "term_name": boundary.term_name,
                            "local_minute": label.isoformat(timespec="minutes"),
                            "distance_seconds": round(distance, 6),
                        }
    return [hits[key] for key in sorted(hits)]


class _ShiftedPastBoundaryProvider:
    """±1초 정책 검증에만 쓰는 base provider의 비공개 view다."""

    provider_id = SkyfieldSolarTermProvider.provider_id

    def __init__(self, base: SkyfieldSolarTermProvider, offset_seconds: float) -> None:
        self._base = base
        self._offset = float(offset_seconds)
        self._cache: dict[tuple[int, int], SolarTermBoundary] = {}

    def identity(self) -> dict[str, Any]:
        return self._base.identity()

    def evidence_context(self) -> dict[str, Any]:
        return self._base.evidence_context()

    def boundary(self, year: int, term_index: int) -> SolarTermBoundary:
        key = (year, term_index)
        existing = self._cache.get(key)
        if existing is not None:
            return existing
        original = self._base.boundary(year, term_index)
        if original.authority_class != PAST_OFFICIAL_CORROBORATED:
            self._cache[key] = original
            return original
        tt = (
            Decimal(original.tt_whole)
            + Decimal(str(original.tt_fraction))
            + Decimal(str(self._offset)) / Decimal(86_400)
        )
        whole = int(tt)
        shifted = SolarTermBoundary(
            provider_id=original.provider_id,
            year=original.year,
            term_index=original.term_index,
            term_name=original.term_name,
            saju_month_number=original.saju_month_number,
            instant_utc=original.instant_utc + timedelta(seconds=self._offset),
            tt_whole=whole,
            tt_fraction=float(tt - Decimal(whole)),
            official_display_minute_fixed_kst=(
                original.official_display_minute_fixed_kst
            ),
            authority_class=original.authority_class,
            official_source_evidence_class=original.official_source_evidence_class,
        )
        self._cache[key] = shifted
        return shifted

    def compare_instant(self, instant: datetime, boundary: SolarTermBoundary) -> int:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise RuntimeCalculationError(
                "INVALID_INSTANT", "절입 비교 instant에는 timezone이 필요합니다."
            )
        if boundary != self.boundary(boundary.year, boundary.term_index):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "shifted 절입 경계 identity가 다릅니다."
            )
        value = instant.astimezone(UTC)
        if value < boundary.instant_utc:
            return -1
        if value > boundary.instant_utc:
            return 1
        return 0


def _semantic_facts(value: Any) -> Any:
    copied = deepcopy(value)
    if isinstance(copied, dict):
        copied.pop("solar_term_evidence", None)
    return copied


def uncertain_result_is_stable(
    arguments: dict[str, Any],
    *,
    base_result: dict[str, Any],
    signer: RuntimeIdSigner,
    calendar_provider: KoreanLunarCalendarProvider,
    solar_term_provider: SkyfieldSolarTermProvider,
) -> bool:
    """같은 입력을 과거 root ±1초로 계산해 공통 사실이 유지되는지 검증한다."""

    projections = [_semantic_facts(base_result.get("stable_facts"))]
    for offset in (-KASI_PAST_UNCERTAINTY_SECONDS, KASI_PAST_UNCERTAINTY_SECONDS):
        provider = _ShiftedPastBoundaryProvider(solar_term_provider, offset)
        engine = SajuRuntimeEngineV13(
            signer=signer,
            enable_candidate_runtime=True,
            calendar_provider=calendar_provider,
            solar_term_provider=provider,  # type: ignore[arg-type]
        )
        try:
            shifted = engine.calculate_chart(arguments)
        finally:
            engine.close()
        if shifted.get("status") == "blocked":
            return False
        projections.append(_semantic_facts(shifted.get("stable_facts")))
    return projections[0] == projections[1] == projections[2]


class ApprovedSajuRuntimeEngineV14:
    """유효 v1.4 release가 있을 때 과거 공식 구간 원국만 승인한다."""

    def __init__(
        self,
        *,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
        ephemeris_path: Path | None = None,
        signer: RuntimeIdSigner | None = None,
        id_key_file: Path | None = None,
    ) -> None:
        validate_contract_registry_v1_4()
        self.enable_approved_runtime = bool(enable_approved_runtime)
        self.release = (
            validate_release_registry_v1_4(release_registry)
            if release_registry is not None
            else None
        )
        if signer is not None and id_key_file is not None:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_SOURCE_AMBIGUOUS",
                "approved runtime signer와 key 파일을 동시에 지정할 수 없습니다.",
            )
        active = self.enable_approved_runtime and self.release is not None
        if not active and (ephemeris_path is not None or signer is not None or id_key_file is not None):
            raise RuntimeCalculationError(
                "RUNTIME_RESOURCE_WITH_DISABLED_RUNTIME",
                "비활성 chart-only runtime에는 ephemeris·key를 열지 않습니다.",
            )
        self._provider: SkyfieldSolarTermProvider | None = None
        self._calendar: KoreanLunarCalendarProvider | None = None
        self._candidate: SajuRuntimeEngineV13 | None = None
        self._signer: RuntimeIdSigner | None = None
        release_id = None if self.release is None else self.release["release_id"]
        release_sha = (
            None if self.release is None else self.release["release_registry_sha256"]
        )
        if active:
            if ephemeris_path is None:
                raise RuntimeCalculationError(
                    "SOLAR_TERM_EPHEMERIS_REQUIRED",
                    "활성 v1.4 runtime에는 고정 DE440s 절대경로가 필요합니다.",
                )
            production_signer = signer or RuntimeIdSigner.from_key_file(id_key_file)
            if not production_signer.production_key:
                raise RuntimeCalculationError(
                    "RUNTIME_ID_KEY_INVALID",
                    "approved runtime에는 production key signer가 필요합니다.",
                )
            provider = SkyfieldSolarTermProvider(ephemeris_path)
            calendar = KoreanLunarCalendarProvider()
            try:
                source_versions = runtime_source_versions_v1_4(
                    require_runtime_dependencies=True,
                    provider_identity=provider.identity(),
                    release_id=release_id,
                    release_registry_sha256=release_sha,
                )
                candidate = SajuRuntimeEngineV13(
                    signer=production_signer,
                    enable_candidate_runtime=True,
                    calendar_provider=calendar,
                    solar_term_provider=provider,
                )
            except Exception:
                provider.close()
                raise
            self._provider = provider
            self._calendar = calendar
            self._candidate = candidate
            self._signer = production_signer
            self.source_versions = source_versions
        else:
            self.source_versions = runtime_source_versions_v1_4(
                require_runtime_dependencies=False,
                release_id=release_id,
                release_registry_sha256=release_sha,
            )

    def __enter__(self) -> ApprovedSajuRuntimeEngineV14:  # noqa: PYI034
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        if self._candidate is not None:
            self._candidate.close()
        if self._provider is not None:
            self._provider.close()

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
                "통과한 conformance v9에 결합된 v1.4 chart-only release가 필요합니다.",
            )
        if not self.enable_approved_runtime:
            return self._blocked(
                "RUNTIME_FEATURE_DISABLED",
                "승인된 chart-only runtime도 기본 off이며 명시적 feature flag가 필요합니다.",
            )
        if any(
            value is None
            for value in (self._provider, self._calendar, self._candidate, self._signer)
        ):
            return self._blocked(
                "RUNTIME_RESOURCE_REQUIRED", "chart-only runtime 자원이 준비되지 않았습니다."
            )
        return None

    def calculate_chart(self, arguments: dict[str, Any]) -> dict[str, Any]:
        unavailable = self._availability_block()
        if unavailable is not None:
            return unavailable
        assert self._provider is not None
        assert self._calendar is not None
        assert self._candidate is not None
        assert self._signer is not None
        try:
            normalized = normalize_tool_birth_input(arguments, self._calendar)
        except RuntimeCalculationError as exc:
            return self._blocked(exc.code, exc.message)
        solar_date = date.fromisoformat(normalized["solar_birth_date"])
        if not is_approved_solar_date(solar_date):
            return self._blocked(
                "BIRTH_DATE_OUT_OF_APPROVED_RANGE",
                f"chart-only 승인 양력 생일은 {APPROVED_START_DATE}~{APPROVED_END_DATE}입니다.",
                normalized_input=normalized,
            )
        candidate = self._candidate.calculate_chart(arguments)
        if candidate.get("status") == "blocked":
            return self._blocked(
                str(candidate.get("code") or "CHART_CALCULATION_BLOCKED"),
                str(candidate.get("message") or "원국 계산이 차단됐습니다."),
                normalized_input=normalized,
            )
        try:
            validate_chart_only_candidate(candidate, normalized=normalized)
            hits = boundary_uncertainty_hits(normalized, self._provider)
        except RuntimeCalculationError as exc:
            return self._blocked(exc.code, exc.message, normalized_input=normalized)
        precision = normalized["birth_time_precision"]
        uncertainty_stable = True
        if hits and precision == "exact":
            return self._blocked(
                "SOLAR_TERM_BOUNDARY_UNCERTAIN",
                "출생 분이 공식 과거 절입의 ±1초 불확실 경계와 겹칩니다.",
                normalized_input=normalized,
            )
        if hits:
            uncertainty_stable = uncertain_result_is_stable(
                arguments,
                base_result=candidate,
                signer=self._signer,
                calendar_provider=self._calendar,
                solar_term_provider=self._provider,
            )
            if not uncertainty_stable:
                return self._blocked(
                    "SOLAR_TERM_BOUNDARY_UNCERTAIN",
                    "절입 ±1초 양끝에서 공통 원국 사실이 유지되지 않습니다.",
                    normalized_input=normalized,
                )
        return self._promote(
            candidate,
            normalized=normalized,
            uncertainty_checked=bool(hits),
            uncertainty_stable=uncertainty_stable,
        )

    def _promote(
        self,
        candidate: dict[str, Any],
        *,
        normalized: dict[str, Any],
        uncertainty_checked: bool,
        uncertainty_stable: bool,
    ) -> dict[str, Any]:
        assert self._signer is not None
        result = deepcopy(candidate)
        identity = {
            "normalized_birth_input": normalized,
            "policy_id": POLICY_ID,
            "engine_version": ENGINE_VERSION_V14,
            "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V14,
            "id_contract_version": ID_CONTRACT_VERSION_V2,
            "runtime_scope": APPROVED_SCOPE_V14,
            "source_versions": self.source_versions,
        }
        alternatives: list[dict[str, Any]] = []
        for item in candidate["alternative_charts"]:
            updated = deepcopy(item)
            updated["chart_id"] = self._signer.chart_id(
                {**identity, "facts": updated["hard_facts"]}
            )
            alternatives.append(updated)
        alternatives.sort(key=lambda item: item["chart_id"])
        exact = normalized["birth_time_precision"] == "exact" and len(alternatives) == 1
        chart_id = alternatives[0]["chart_id"] if exact else None
        chart_set_id = (
            None
            if exact
            else self._signer.chart_set_id(
                {
                    **identity,
                    "candidate_chart_ids": [item["chart_id"] for item in alternatives],
                }
            )
        )
        warnings = [
            warning
            for warning in candidate.get("warnings", [])
            if warning.get("code")
            not in {
                "RUNTIME_GATE_PENDING",
                "RUNTIME_RELEASE_PENDING",
                "SOLAR_TERM_OFFICIAL_COVERAGE_MISSING",
                "FUTURE_SOLAR_TERM_FORECAST_NONAPPROVAL",
            }
        ]
        if uncertainty_checked:
            warnings.append(
                {
                    "code": "SOLAR_TERM_BOUNDARY_UNCERTAINTY_CHECKED",
                    "message": "공식 과거 절입 ±1초 양끝에서 공통 사실이 유지됨을 확인했습니다.",
                }
            )
        result.update(
            {
                "status": "ok" if exact else "partial",
                "code": None if exact else "BIRTH_TIME_UNCERTAIN",
                "message": (
                    "승인된 v1.4 chart-only runtime으로 원국 계산을 완료했습니다."
                    if exact
                    else "생시 불확실성을 유지하고 모든 후보의 공통 원국 사실만 제공합니다."
                ),
                "fact_authority": "HARD_GT" if exact else "POLICY_BOUND_RULE",
                "alternative_charts": alternatives,
                "birth_input_id": self._signer.birth_input_id(normalized),
                "chart_id": chart_id,
                "chart_set_id": chart_set_id,
                "calculation_run_id": self._signer.calculation_run_id(
                    {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id}
                ),
                "engine_version": ENGINE_VERSION_V14,
                "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V14,
                "runtime_scope": APPROVED_SCOPE_V14,
                "source_versions": deepcopy(self.source_versions),
                "warnings": warnings,
                "limitations": [
                    "원국만 승인하며 대운·기간·신강약·격국·용신·자동 해석은 제공하지 않습니다."
                ],
                "internal_trace": {
                    **(candidate.get("internal_trace") or {}),
                    "runtime_scope": APPROVED_SCOPE_V14,
                    "boundary_uncertainty_checked": uncertainty_checked,
                    "boundary_uncertainty_stable": uncertainty_stable,
                },
            }
        )
        return result

    def calculate_period(self, arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments
        return self._blocked(
            "CHART_ONLY_PERIOD_OUT_OF_SCOPE",
            "v1.4 release는 원국 계산만 승인하며 기간 계산은 항상 차단합니다.",
        )


def execute_approved_runtime_tool_v1_4(
    engine: ApprovedSajuRuntimeEngineV14,
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
