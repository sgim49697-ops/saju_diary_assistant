# solar_term_provider_comparison_v1.py - Astronomy Engine과 Skyfield 절입 provider를 같은 근거로 비교한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from statistics import fmean
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.jie_crosscheck import (
    DE440S_SHA256,
    JPLEPHEM_VERSION,
    NUMPY_VERSION,
    SKYFIELD_VERSION,
    validate_ephemeris,
)
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import display_minute_label
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.solar_terms import (
    JIE_TO_MONTH,
    SOLAR_TERM_NAMES,
    solar_term_instant,
)

COMPARISON_VERSION = "saju-solar-term-provider-comparison-v1.0.0"
START_YEAR = 1900
END_YEAR = 2049
EXPECTED_JIE_ROWS = (END_YEAR - START_YEAR + 1) * 12
EXPECTED_MINUTE_ROWS = 84
MAXIMUM_REGRESSION_DELTA_SECONDS = 120.0
UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")


class SolarTermProviderComparisonError(RuntimeError):
    """절입 provider 계산·비교 계약 위반."""


@dataclass(frozen=True)
class SolarTermRequest:
    """한 절입 순간 계산 요청."""

    year: int
    term_index: int


class SolarTermProvider(Protocol):
    """conformance에서 비교하는 batch 절입 provider 계약."""

    provider_id: str

    def instants(self, requests: Sequence[SolarTermRequest]) -> list[datetime]: ...

    def identity(self) -> dict[str, Any]: ...


def _validate_requests(requests: Sequence[SolarTermRequest]) -> None:
    if not requests:
        raise SolarTermProviderComparisonError("절입 요청이 비었습니다.")
    seen: set[tuple[int, int]] = set()
    previous: tuple[int, int] | None = None
    for request in requests:
        identity = (request.year, request.term_index)
        if (
            not START_YEAR <= request.year <= END_YEAR
            or request.term_index not in JIE_TO_MONTH
            or identity in seen
            or (previous is not None and identity <= previous)
        ):
            raise SolarTermProviderComparisonError("절입 요청 identity·순서가 다릅니다.")
        seen.add(identity)
        previous = identity


class AstronomyEngineSolarTermProvider:
    """현재 v1.2 runtime과 같은 Astronomy Engine 후보 provider."""

    provider_id = "astronomy-engine-2.1.19"

    def instants(self, requests: Sequence[SolarTermRequest]) -> list[datetime]:
        _validate_requests(requests)
        return [solar_term_instant(row.year, row.term_index) for row in requests]

    def identity(self) -> dict[str, Any]:
        version = importlib.metadata.version("astronomy-engine")
        if version != "2.1.19":
            raise SolarTermProviderComparisonError(
                f"astronomy-engine 버전이 다릅니다: {version}"
            )
        return {
            "provider_id": self.provider_id,
            "package": "astronomy-engine",
            "version": version,
            "ephemeris": "embedded_truncated_vsop87_novas_lineage",
            "production_selected": False,
        }


def _validator_dependencies() -> dict[str, str]:
    expected = {
        "skyfield": SKYFIELD_VERSION,
        "jplephem": JPLEPHEM_VERSION,
        "numpy": NUMPY_VERSION,
    }
    try:
        actual = {name: importlib.metadata.version(name) for name in expected}
    except importlib.metadata.PackageNotFoundError as exc:
        raise SolarTermProviderComparisonError(
            "Skyfield provider 의존성이 없습니다."
        ) from exc
    if actual != expected:
        raise SolarTermProviderComparisonError(
            f"Skyfield provider 의존성 버전이 다릅니다: {actual}"
        )
    return actual


class SkyfieldDe440sSolarTermProvider:
    """고정 DE440s와 달력 기반 bracket만 사용하는 Skyfield 후보 provider."""

    provider_id = "skyfield-1.55-jpl-de440s"

    def __init__(self, ephemeris_path: Path) -> None:
        self._ephemeris_path = ephemeris_path
        self._ephemeris_identity = validate_ephemeris(ephemeris_path)
        self._dependencies = _validator_dependencies()

    def instants(self, requests: Sequence[SolarTermRequest]) -> list[datetime]:
        _validate_requests(requests)
        try:
            import numpy as np
            from skyfield.api import load, load_file
            from skyfield.framelib import ecliptic_frame
        except ImportError as exc:
            raise SolarTermProviderComparisonError(
                "Skyfield provider를 import하지 못했습니다."
            ) from exc
        ephemeris = load_file(str(self._ephemeris_path))
        try:
            earth = ephemeris["earth"]
            sun = ephemeris["sun"]
            timescale = load.timescale(builtin=True)
            centers = np.array(
                [
                    datetime(
                        row.year,
                        row.term_index // 2 + 1,
                        7 if row.term_index % 2 == 0 else 22,
                        tzinfo=UTC,
                    ).timestamp()
                    for row in requests
                ],
                dtype=np.float64,
            )
            targets = np.array(
                [(285.0 + 15.0 * row.term_index) % 360.0 for row in requests],
                dtype=np.float64,
            )

            def signed_delta(timestamps):
                values = [
                    datetime.fromtimestamp(float(timestamp), UTC)
                    for timestamp in timestamps
                ]
                times = timescale.from_datetimes(values)
                apparent = earth.at(times).observe(sun).apparent()
                longitude = apparent.frame_latlon(ecliptic_frame)[1].degrees
                return (longitude - targets + 180.0) % 360.0 - 180.0

            # Astronomy Engine 결과를 bracket 중심으로 사용하지 않는다. 절기별 고정
            # 달력 중심 ±5일은 1900~2049의 각 목표 황경을 독립적으로 감싼다.
            left = centers - 5.0 * 86_400.0
            right = centers + 5.0 * 86_400.0
            left_delta = signed_delta(left)
            right_delta = signed_delta(right)
            if bool(np.any(left_delta >= 0.0)) or bool(np.any(right_delta <= 0.0)):
                raise SolarTermProviderComparisonError(
                    "Skyfield root가 고정 달력 bracket에 없습니다."
                )
            for _ in range(48):
                middle = (left + right) / 2.0
                crossed = signed_delta(middle) >= 0.0
                right = np.where(crossed, middle, right)
                left = np.where(crossed, left, middle)
            return [
                datetime.fromtimestamp(float(timestamp), UTC)
                for timestamp in (left + right) / 2.0
            ]
        finally:
            ephemeris.close()

    def identity(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "packages": self._dependencies,
            "ephemeris": self._ephemeris_identity,
            "ephemeris_expected_sha256": DE440S_SHA256,
            "timescale": "skyfield_builtin_no_network",
            "root_bracket": "fixed_calendar_center_plus_minus_5_days",
            "astronomy_engine_center_used": False,
            "automatic_download_or_fallback": False,
            "production_selected": False,
        }


def _requests() -> list[SolarTermRequest]:
    return [
        SolarTermRequest(year, index)
        for year in range(START_YEAR, END_YEAR + 1)
        for index in sorted(JIE_TO_MONTH)
    ]


def _distribution(values: list[float]) -> dict[str, int | float]:
    absolute = sorted(abs(value) for value in values)
    if not absolute:
        return {
            "rows": 0,
            "mean_absolute_seconds": 0.0,
            "p99_absolute_seconds": 0.0,
            "maximum_absolute_seconds": 0.0,
        }
    index = max(0, math.ceil(len(absolute) * 0.99) - 1)
    return {
        "rows": len(absolute),
        "mean_absolute_seconds": round(fmean(absolute), 6),
        "p99_absolute_seconds": round(absolute[index], 6),
        "maximum_absolute_seconds": round(absolute[-1], 6),
    }


def _provider_evidence(
    provider_key: str,
    records: list[dict[str, Any]],
    official_term_rows: list[dict[str, Any]],
    minute_rows: list[dict[str, Any]],
    almanac_row: dict[str, Any],
) -> dict[str, Any]:
    prefix = "astronomy" if provider_key == "astronomy_engine" else "skyfield"
    record_map = {(row["year"], row["term_index"]): row for row in records}
    official_jie = [
        row for row in official_term_rows if row.get("term_index") in JIE_TO_MONTH
    ]
    api_mismatches = []
    for row in official_jie:
        record = record_map.get((row["year"], row["term_index"]))
        actual = None if record is None else record[f"{prefix}_local_date"]
        if actual != row["local_date"]:
            api_mismatches.append(
                {
                    "year": row["year"],
                    "term_index": row["term_index"],
                    "expected": row["local_date"],
                    "actual": actual,
                }
            )
    minute_mismatches = []
    for row in minute_rows:
        record = record_map.get((row["year"], row["term_index"]))
        expected = datetime.fromisoformat(row["reference_local_minute"]).isoformat(
            timespec="minutes"
        )
        actual = None if record is None else record[f"{prefix}_display_minute_kst"]
        if actual != expected:
            minute_mismatches.append(
                {
                    "year": row["year"],
                    "term_index": row["term_index"],
                    "expected": expected,
                    "actual": actual,
                }
            )
    almanac_record = record_map.get((1964, 16))
    if almanac_record is None:
        raise SolarTermProviderComparisonError("1964년 백로 provider 행이 없습니다.")
    normalized_minute = almanac_row["normalized_reference_local_minute"]
    formal_date_match = (
        almanac_record[f"{prefix}_local_date"]
        == datetime.fromisoformat(normalized_minute).date().isoformat()
    )
    formal_minute_match = (
        almanac_record[f"{prefix}_display_minute_kst"] == normalized_minute
    )
    official_coverage_rows = len(official_jie) + 1
    checks = {
        "official_jie_date_coverage_complete": official_coverage_rows
        == EXPECTED_JIE_ROWS,
        "available_openapi_date_mismatch_zero": not api_mismatches,
        "formal_1964_normalized_civil_date_match": formal_date_match,
        "institutional_minute_reference_complete": len(minute_rows)
        == EXPECTED_MINUTE_ROWS,
        "institutional_minute_label_mismatch_zero": not minute_mismatches,
    }
    return {
        "provider": provider_key,
        "official_jie_date_rows": official_coverage_rows,
        "official_jie_date_expected_rows": EXPECTED_JIE_ROWS,
        "openapi_jie_rows": len(official_jie),
        "openapi_date_mismatches": len(api_mismatches),
        "openapi_date_mismatch_rows": api_mismatches[:100],
        "formal_1964_almanac": {
            "source_precision": almanac_row["reference_precision"],
            "printed_label": almanac_row["printed_label"],
            "normalized_reference_local_minute": normalized_minute,
            "provider_instant_utc": almanac_record[f"{prefix}_instant_utc"],
            "provider_exact_local_date": almanac_record[f"{prefix}_local_date"],
            "provider_display_minute_kst": almanac_record[
                f"{prefix}_display_minute_kst"
            ],
            "normalized_civil_date_match": formal_date_match,
            "display_minute_match": formal_minute_match,
            "subminute_physical_accuracy_adjudicated": False,
        },
        "institutional_minute_rows": len(minute_rows),
        "institutional_minute_label_mismatches": len(minute_mismatches),
        "institutional_minute_mismatch_rows": minute_mismatches[:100],
        "eligibility_checks": checks,
        "eligible": all(checks.values()),
        "blocking_reasons": sorted(key for key, value in checks.items() if not value),
    }


def _select_provider(evidence: dict[str, dict[str, Any]]) -> dict[str, Any]:
    eligible = [key for key, value in evidence.items() if value["eligible"]]
    if not eligible:
        return {
            "status": "blocked_no_eligible_provider",
            "selected_provider": None,
            "tie_break_applied": False,
            "runtime_provider_changed": False,
        }
    if len(eligible) == 1:
        return {
            "status": "selected_single_eligible_provider",
            "selected_provider": eligible[0],
            "tie_break_applied": False,
            "runtime_provider_changed": False,
        }
    return {
        "status": "selected_astronomy_engine_by_dependency_tie_break",
        "selected_provider": "astronomy_engine",
        "tie_break_applied": True,
        "tie_break_rule": "retain_current_provider_when_both_pass_identical_gate",
        "runtime_provider_changed": False,
    }


def compare_providers(
    ephemeris_path: Path,
    *,
    official_term_rows: list[dict[str, Any]],
    minute_rows: list[dict[str, Any]],
    almanac_row: dict[str, Any],
    include_records: bool = False,
) -> dict[str, Any]:
    requests = _requests()
    astronomy_provider = AstronomyEngineSolarTermProvider()
    skyfield_provider = SkyfieldDe440sSolarTermProvider(ephemeris_path)
    astronomy = astronomy_provider.instants(requests)
    skyfield = skyfield_provider.instants(requests)
    records: list[dict[str, Any]] = []
    for order, (request, runtime, comparator) in enumerate(
        zip(requests, astronomy, skyfield, strict=True)
    ):
        records.append(
            {
                "order": order,
                "year": request.year,
                "term_index": request.term_index,
                "term_name": SOLAR_TERM_NAMES[request.term_index],
                "astronomy_instant_utc": runtime.isoformat().replace("+00:00", "Z"),
                "skyfield_instant_utc": comparator.isoformat().replace("+00:00", "Z"),
                "delta_seconds": round((runtime - comparator).total_seconds(), 6),
                "astronomy_local_date": runtime.astimezone(KST).date().isoformat(),
                "skyfield_local_date": comparator.astimezone(KST).date().isoformat(),
                "astronomy_display_minute_kst": display_minute_label(runtime),
                "skyfield_display_minute_kst": display_minute_label(comparator),
            }
        )
    identity_failures = sum(
        row["term_name"] != SOLAR_TERM_NAMES[row["term_index"]]
        or row["order"] != order
        for order, row in enumerate(records)
    )
    astronomy_order_failures = sum(
        current["astronomy_instant_utc"] >= following["astronomy_instant_utc"]
        for current, following in pairwise(records)
    )
    skyfield_order_failures = sum(
        current["skyfield_instant_utc"] >= following["skyfield_instant_utc"]
        for current, following in pairwise(records)
    )
    deltas = [float(row["delta_seconds"]) for row in records]
    threshold_failures = sum(
        abs(value) > MAXIMUM_REGRESSION_DELTA_SECONDS for value in deltas
    )
    evidence = {
        "astronomy_engine": _provider_evidence(
            "astronomy_engine", records, official_term_rows, minute_rows, almanac_row
        ),
        "skyfield_de440s": _provider_evidence(
            "skyfield_de440s", records, official_term_rows, minute_rows, almanac_row
        ),
    }
    shared_checks = {
        "rows_complete": len(records) == EXPECTED_JIE_ROWS,
        "term_identity_failure_zero": identity_failures == 0,
        "astronomy_chronological_order_failure_zero": astronomy_order_failures == 0,
        "skyfield_chronological_order_failure_zero": skyfield_order_failures == 0,
        "fixed_120_second_regression_guard": threshold_failures == 0,
    }
    for value in evidence.values():
        value["eligibility_checks"].update(shared_checks)
        value["eligible"] = all(value["eligibility_checks"].values())
        value["blocking_reasons"] = sorted(
            key for key, passed in value["eligibility_checks"].items() if not passed
        )
    selection = _select_provider(evidence)
    result = {
        "schema_version": "1.0.0",
        "comparison_version": COMPARISON_VERSION,
        "range": {"start_year": START_YEAR, "end_year": END_YEAR},
        "rows": len(records),
        "expected_rows": EXPECTED_JIE_ROWS,
        "providers": {
            "astronomy_engine": astronomy_provider.identity(),
            "skyfield_de440s": skyfield_provider.identity(),
        },
        "delta_convention": "astronomy_instant_utc_minus_skyfield_instant_utc",
        "delta_distribution": _distribution(deltas),
        "minimum_signed_delta_seconds": round(min(deltas), 6),
        "maximum_signed_delta_seconds": round(max(deltas), 6),
        "fixed_regression_guard": {
            "maximum_seconds": MAXIMUM_REGRESSION_DELTA_SECONDS,
            "failures": threshold_failures,
            "role": "non_authoritative_regression_guard_not_accuracy_oracle",
        },
        "term_identity_failures": identity_failures,
        "astronomy_chronological_order_failures": astronomy_order_failures,
        "skyfield_chronological_order_failures": skyfield_order_failures,
        "engine_exact_local_date_disagreements": sum(
            row["astronomy_local_date"] != row["skyfield_local_date"] for row in records
        ),
        "engine_display_minute_disagreements": sum(
            row["astronomy_display_minute_kst"]
            != row["skyfield_display_minute_kst"]
            for row in records
        ),
        "official_evidence": evidence,
        "adjudication_1964_baengno": {
            "status": "resolved_for_normalized_civil_date_not_subminute_physics",
            "printed_kasi_label": almanac_row["printed_label"],
            "normalized_kasi_reference": almanac_row[
                "normalized_reference_local_minute"
            ],
            "civil_date_match": {
                key: value["formal_1964_almanac"]["normalized_civil_date_match"]
                for key, value in evidence.items()
            },
            "display_minute_match": {
                key: value["formal_1964_almanac"]["display_minute_match"]
                for key, value in evidence.items()
            },
            "civil_date_policy_result": "astronomy_engine",
            "subminute_physical_accuracy_result": "not_adjudicated_by_minute_precision_source",
        },
        "selection": selection,
        "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        "records_in_report": include_records,
    }
    if include_records:
        result["records"] = records
    return result


def records_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in records)


def summary_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
