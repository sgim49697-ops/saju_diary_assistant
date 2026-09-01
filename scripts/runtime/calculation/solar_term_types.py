# solar_term_types.py - 절입 경계의 TT 좌표와 근거 권한을 구조화한다.

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from .errors import RuntimeCalculationError

PROFILE_DETERMINISTIC = "PROFILE_DETERMINISTIC"
PAST_OFFICIAL_CORROBORATED = "PAST_OFFICIAL_CORROBORATED"
FORECAST_DIAGNOSTIC_NONAPPROVAL = "FORECAST_DIAGNOSTIC_NONAPPROVAL"
SOURCE_HARD_FACT = "SOURCE_HARD_FACT"
SOLAR_TERM_EVIDENCE_SCHEMA_VERSION = "1.0.0"

_EVIDENCE_CONTEXT_KEYS = (
    "provider_id",
    "root_time_scale",
    "boundary_comparison_time_scale",
    "official_label_coordinate",
    "official_snapshot_collected_at",
    "provider_generated_value_is_official",
)
_EXPECTED_EVIDENCE_CONTEXT = {
    "root_time_scale": "TT",
    "boundary_comparison_time_scale": "TT",
    "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
    "official_snapshot_collected_at": "2026-08-31T15:16:50+00:00",
}
_EVIDENCE_SUMMARY_KEYS = (
    "schema_version",
    "authority_classes",
    "overall_authority",
    "contains_future_nonapproval",
    "boundaries",
)
_BOUNDARY_RECORD_KEYS = {
    "roles",
    "year",
    "term_index",
    "term_name",
    "instant_tt_jd",
    "instant_utc",
    "official_display_minute_fixed_kst",
    "authority_class",
    "official_source_evidence_class",
    "provider_generated_value_is_official",
}
_TT_JD_PATTERN = re.compile(r"^[0-9]{7}\.[0-9]{12}$")

AUTHORITY_PRECEDENCE = {
    PAST_OFFICIAL_CORROBORATED: 0,
    PROFILE_DETERMINISTIC: 1,
    FORECAST_DIAGNOSTIC_NONAPPROVAL: 2,
}


@dataclass(frozen=True)
class SolarTermBoundary:
    """provider가 계산한 한 절입의 물리적 root와 표시 근거."""

    provider_id: str
    year: int
    term_index: int
    term_name: str
    saju_month_number: int | None
    instant_utc: datetime
    tt_whole: int
    tt_fraction: float
    official_display_minute_fixed_kst: str
    authority_class: str
    official_source_evidence_class: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 provider ID가 비었습니다."
            )
        if (
            isinstance(self.year, bool)
            or not isinstance(self.year, int)
            or not 1899 <= self.year <= 2050
            or isinstance(self.term_index, bool)
            or not isinstance(self.term_index, int)
            or not 0 <= self.term_index <= 23
            or not isinstance(self.term_name, str)
            or not self.term_name.strip()
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 경계 identity가 다릅니다."
            )
        if self.saju_month_number is not None and (
            isinstance(self.saju_month_number, bool)
            or not isinstance(self.saju_month_number, int)
            or not 1 <= self.saju_month_number <= 12
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 월 번호가 다릅니다."
            )
        if (
            not isinstance(self.instant_utc, datetime)
            or self.instant_utc.tzinfo is None
            or self.instant_utc.utcoffset() != timedelta(0)
            or isinstance(self.tt_whole, bool)
            or not isinstance(self.tt_whole, int)
            or not 1_000_000 <= self.tt_whole <= 9_999_999
            or isinstance(self.tt_fraction, bool)
            or not isinstance(self.tt_fraction, (int, float))
            or not math.isfinite(float(self.tt_fraction))
            or not 0.0 <= float(self.tt_fraction) < 1.0
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 물리시각 좌표가 다릅니다."
            )
        try:
            display = datetime.fromisoformat(
                self.official_display_minute_fixed_kst
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 공식 표시 분이 다릅니다."
            ) from exc
        if (
            display.utcoffset() != timedelta(hours=9)
            or display.second != 0
            or display.microsecond != 0
            or display.isoformat(timespec="minutes")
            != self.official_display_minute_fixed_kst
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 공식 표시 분이 다릅니다."
            )
        if (
            not isinstance(self.authority_class, str)
            or self.authority_class not in AUTHORITY_PRECEDENCE
            or (
                self.official_source_evidence_class is not None
                and not isinstance(self.official_source_evidence_class, str)
            )
            or self.official_source_evidence_class not in {None, SOURCE_HARD_FACT}
            or (
                self.authority_class == PROFILE_DETERMINISTIC
                and self.official_source_evidence_class is not None
            )
            or (
                self.authority_class == PAST_OFFICIAL_CORROBORATED
                and self.official_source_evidence_class != SOURCE_HARD_FACT
            )
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 권한이 다릅니다."
            )

    @property
    def tt_sort_key(self) -> tuple[int, float]:
        return self.tt_whole, self.tt_fraction

    @property
    def tt_jd_text(self) -> str:
        value = Decimal(self.tt_whole) + Decimal(str(self.tt_fraction))
        return format(value.quantize(Decimal("0.000000000001")), "f")

    def evidence_record(self, role: str) -> dict[str, Any]:
        if not isinstance(role, str) or not role.strip() or role != role.strip():
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 role이 비었습니다."
            )
        return {
            "roles": [role],
            "year": self.year,
            "term_index": self.term_index,
            "term_name": self.term_name,
            "instant_tt_jd": self.tt_jd_text,
            "instant_utc": self.instant_utc.isoformat().replace("+00:00", "Z"),
            "official_display_minute_fixed_kst": (
                self.official_display_minute_fixed_kst
            ),
            "authority_class": self.authority_class,
            "official_source_evidence_class": (
                self.official_source_evidence_class
            ),
            "provider_generated_value_is_official": False,
        }


class SolarTermProvider(Protocol):
    """runtime이 사용하는 절입 provider의 최소 계약."""

    provider_id: str

    def boundary(self, year: int, term_index: int) -> SolarTermBoundary: ...

    def compare_instant(
        self, instant: datetime, boundary: SolarTermBoundary
    ) -> int: ...

    def evidence_context(self) -> dict[str, Any]: ...


def build_solar_term_evidence(
    provider: SolarTermProvider,
    role_boundaries: Sequence[tuple[str, SolarTermBoundary]],
) -> dict[str, Any]:
    """하나 이상의 결정 경계를 공개 가능한 권한 요약으로 만든다."""

    if not role_boundaries:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 경계가 비었습니다."
        )
    context = provider.evidence_context()
    _validate_context(context)
    if context["provider_id"] != provider.provider_id:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 provider identity가 다릅니다."
        )
    records: list[dict[str, Any]] = []
    for role, boundary in role_boundaries:
        if (
            not isinstance(boundary, SolarTermBoundary)
            or boundary.provider_id != provider.provider_id
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID",
                "절입 경계와 provider identity가 다릅니다.",
            )
        records.append(boundary.evidence_record(role))
    return _assemble_evidence(context, records)


def merge_solar_term_evidence(
    values: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """불확실 시각 후보들의 경계를 유실 없이 하나의 안정 근거로 합친다."""

    if not values:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "병합할 절입 근거가 비었습니다."
        )
    validated = [_validate_evidence(value) for value in values]
    first = validated[0]
    context = {key: first[key] for key in _EVIDENCE_CONTEXT_KEYS}
    records: list[dict[str, Any]] = []
    for value in validated:
        if any(value[key] != context[key] for key in _EVIDENCE_CONTEXT_KEYS):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID",
                "서로 다른 절입 provider 근거를 병합할 수 없습니다.",
            )
        boundaries = value.get("boundaries")
        if not isinstance(boundaries, list) or not boundaries:
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 boundary가 비었습니다."
            )
        records.extend(boundaries)
    return _assemble_evidence(context, records)


def _assemble_evidence(
    context: dict[str, Any], records: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    _validate_context(context)
    deduplicated: dict[tuple[int, int, str], dict[str, Any]] = {}
    for raw in records:
        _validate_record(raw)
        roles = raw["roles"]
        key = (raw["year"], raw["term_index"], raw["instant_tt_jd"])
        existing = deduplicated.get(key)
        if existing is None:
            copied = dict(raw)
            copied["roles"] = sorted(set(roles))
            deduplicated[key] = copied
            continue
        comparable = {key: value for key, value in raw.items() if key != "roles"}
        existing_comparable = {
            key: value for key, value in existing.items() if key != "roles"
        }
        if comparable != existing_comparable:
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "동일 절입 근거 값이 충돌합니다."
            )
        existing["roles"] = sorted(
            {*existing["roles"], *roles}
        )
    ordered = sorted(
        deduplicated.values(),
        key=lambda row: (row["instant_tt_jd"], row["year"], row["term_index"]),
    )
    if not ordered:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "유효한 절입 근거가 없습니다."
        )
    classes = sorted(
        {str(row["authority_class"]) for row in ordered},
        key=lambda value: AUTHORITY_PRECEDENCE[value],
    )
    overall = max(classes, key=lambda value: AUTHORITY_PRECEDENCE[value])
    return {
        "schema_version": SOLAR_TERM_EVIDENCE_SCHEMA_VERSION,
        **context,
        "authority_classes": classes,
        "overall_authority": overall,
        "contains_future_nonapproval": (
            FORECAST_DIAGNOSTIC_NONAPPROVAL in classes
        ),
        "boundaries": ordered,
    }


def _validate_context(context: Any) -> None:
    if (
        not isinstance(context, dict)
        or set(context) != set(_EVIDENCE_CONTEXT_KEYS)
        or not isinstance(context.get("provider_id"), str)
        or not context["provider_id"].strip()
        or context.get("provider_generated_value_is_official") is not False
        or any(
            context.get(key) != expected
            for key, expected in _EXPECTED_EVIDENCE_CONTEXT.items()
        )
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 provider context가 다릅니다."
        )


def _validate_record(raw: Any) -> None:
    if not isinstance(raw, dict) or set(raw) != _BOUNDARY_RECORD_KEYS:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 record 계약이 다릅니다."
        )
    roles = raw["roles"]
    if (
        not isinstance(roles, list)
        or not roles
        or any(
            not isinstance(role, str)
            or not role.strip()
            or role != role.strip()
            for role in roles
        )
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 role이 다릅니다."
        )
    if (
        isinstance(raw["year"], bool)
        or not isinstance(raw["year"], int)
        or not 1899 <= raw["year"] <= 2050
        or isinstance(raw["term_index"], bool)
        or not isinstance(raw["term_index"], int)
        or not 0 <= raw["term_index"] <= 23
        or not isinstance(raw["term_name"], str)
        or not raw["term_name"].strip()
        or not isinstance(raw["instant_tt_jd"], str)
        or _TT_JD_PATTERN.fullmatch(raw["instant_tt_jd"]) is None
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 identity가 다릅니다."
        )
    try:
        instant = datetime.fromisoformat(raw["instant_utc"].replace("Z", "+00:00"))
        display = datetime.fromisoformat(raw["official_display_minute_fixed_kst"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 표시 시각이 다릅니다."
        ) from exc
    if (
        not isinstance(raw["instant_utc"], str)
        or not raw["instant_utc"].endswith("Z")
        or instant.utcoffset() != timedelta(0)
        or instant.isoformat().replace("+00:00", "Z") != raw["instant_utc"]
        or not isinstance(raw["official_display_minute_fixed_kst"], str)
        or display.utcoffset() != timedelta(hours=9)
        or display.second != 0
        or display.microsecond != 0
        or display.isoformat(timespec="minutes")
        != raw["official_display_minute_fixed_kst"]
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 표시 시각이 다릅니다."
        )
    authority = raw["authority_class"]
    official = raw["official_source_evidence_class"]
    if (
        not isinstance(authority, str)
        or authority not in AUTHORITY_PRECEDENCE
        or (official is not None and not isinstance(official, str))
        or official not in {None, SOURCE_HARD_FACT}
        or raw["provider_generated_value_is_official"] is not False
        or (authority == PROFILE_DETERMINISTIC and official is not None)
        or (
            authority == PAST_OFFICIAL_CORROBORATED
            and official != SOURCE_HARD_FACT
        )
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 권한이 다릅니다."
        )


def _validate_evidence(value: Any) -> dict[str, Any]:
    expected_keys = {*_EVIDENCE_CONTEXT_KEYS, *_EVIDENCE_SUMMARY_KEYS}
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema_version") != SOLAR_TERM_EVIDENCE_SCHEMA_VERSION
        or not isinstance(value.get("authority_classes"), list)
        or not isinstance(value.get("overall_authority"), str)
        or not isinstance(value.get("contains_future_nonapproval"), bool)
        or not isinstance(value.get("boundaries"), list)
        or not value["boundaries"]
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 요약 계약이 다릅니다."
        )
    context = {key: value[key] for key in _EVIDENCE_CONTEXT_KEYS}
    reconstructed = _assemble_evidence(context, value["boundaries"])
    if reconstructed != value:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 요약 값이 다릅니다."
        )
    return value
