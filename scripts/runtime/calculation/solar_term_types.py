# solar_term_types.py - 절입 경계의 TT 좌표와 근거 권한을 구조화한다.

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .errors import RuntimeCalculationError

PROFILE_DETERMINISTIC = "PROFILE_DETERMINISTIC"
PAST_OFFICIAL_CORROBORATED = "PAST_OFFICIAL_CORROBORATED"
FORECAST_DIAGNOSTIC_NONAPPROVAL = "FORECAST_DIAGNOSTIC_NONAPPROVAL"
SOURCE_HARD_FACT = "SOURCE_HARD_FACT"
SOLAR_TERM_EVIDENCE_SCHEMA_VERSION = "1.0.0"

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

    @property
    def tt_sort_key(self) -> tuple[int, float]:
        return self.tt_whole, self.tt_fraction

    @property
    def tt_jd_text(self) -> str:
        value = Decimal(self.tt_whole) + Decimal(str(self.tt_fraction))
        return format(value.quantize(Decimal("0.000000000001")), "f")

    def evidence_record(self, role: str) -> dict[str, Any]:
        if not role:
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
    if context.get("provider_id") != provider.provider_id:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "절입 provider identity가 다릅니다."
        )
    records = [boundary.evidence_record(role) for role, boundary in role_boundaries]
    return _assemble_evidence(context, records)


def merge_solar_term_evidence(
    values: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """불확실 시각 후보들의 경계를 유실 없이 하나의 안정 근거로 합친다."""

    if not values:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EVIDENCE_INVALID", "병합할 절입 근거가 비었습니다."
        )
    context_keys = (
        "provider_id",
        "root_time_scale",
        "boundary_comparison_time_scale",
        "official_label_coordinate",
        "official_snapshot_collected_at",
        "provider_generated_value_is_official",
    )
    first = values[0]
    context = {key: first.get(key) for key in context_keys}
    records: list[dict[str, Any]] = []
    for value in values:
        if any(value.get(key) != context[key] for key in context_keys):
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
    deduplicated: dict[tuple[int, int, str], dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, dict):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 record가 object가 아닙니다."
            )
        authority = raw.get("authority_class")
        roles = raw.get("roles")
        if authority not in AUTHORITY_PRECEDENCE or not isinstance(roles, list):
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 enum·role이 다릅니다."
            )
        try:
            key = (
                int(raw["year"]),
                int(raw["term_index"]),
                str(raw["instant_tt_jd"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeCalculationError(
                "SOLAR_TERM_EVIDENCE_INVALID", "절입 근거 identity가 다릅니다."
            ) from exc
        existing = deduplicated.get(key)
        if existing is None:
            copied = dict(raw)
            copied["roles"] = sorted({str(role) for role in roles if str(role)})
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
            {*existing["roles"], *(str(role) for role in roles if str(role))}
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
