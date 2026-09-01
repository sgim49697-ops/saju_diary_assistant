# conformance_v8.py - Skyfield candidate runtime의 TT 경계와 시간 권한을 전수 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.evaluation.external_conformance import sha256_file
from scripts.evaluation.saju_runtime.conformance import (
    _host_invariance,
    _kasi_checks,
    _policy_checks,
    _synthetic_invariant_checks,
)
from scripts.evaluation.saju_runtime.conformance_v3 import (
    RuntimeConformanceV3Error,
    _load_lunar_snapshot,
    _load_minute_snapshot,
)
from scripts.evaluation.saju_runtime.conformance_v4 import (
    _hmac_invariants,
    _internal_boundary_checks,
)
from scripts.evaluation.saju_runtime.conformance_v5 import (
    RuntimeConformanceV5Error,
    _load_almanac,
    _load_term_coverage,
)
from scripts.evaluation.saju_runtime.conformance_v6 import (
    OFFICIAL_TERM_MANIFEST_FILENAME,
    RuntimeConformanceV6Error,
    _crosscheck_kasi_sources,
    _load_official_terms,
)
from scripts.evaluation.saju_runtime.conformance_v7 import (
    RuntimeConformanceV7Error,
    _load_iers_snapshot,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v1 import (
    SolarTermProviderComparisonError,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v3 import (
    compare_providers,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import (
    ID_CONTRACT_VERSION_V2,
    load_strict_json_object_v1_2,
)
from scripts.runtime.calculation.contracts_v1_3 import (
    ENGINE_VERSION_V13,
    GATE_V16_PATH,
    OUTPUT_SCHEMA_VERSION_V13,
    REGISTRY_V13_PATH,
    SOURCE_REGISTRY_V16_PATH,
    SUITE_VERSION_V8,
    validate_contract_registry_v1_3,
)
from scripts.runtime.calculation.engine_v1_2 import SajuRuntimeEngineV12
from scripts.runtime.calculation.engine_v1_3 import (
    SajuRuntimeEngineV13,
    execute_candidate_runtime_tool_v1_3,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_term_types import (
    FORECAST_DIAGNOSTIC_NONAPPROVAL,
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
)
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

SCHEMA_VERSION = "1.6.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.6.0"
UTC = timezone.utc
TEST_SIGNER_KEY = bytes(range(32))
EXPECTED_ROWS = 1_800
EXPECTED_BOUNDARY_CASES = 5_400
EXPECTED_AUTHORITY_COUNTS = {
    PROFILE_DETERMINISTIC: 240,
    PAST_OFFICIAL_CORROBORATED: 1_280,
    FORECAST_DIAGNOSTIC_NONAPPROVAL: 280,
}
IMPLEMENTATION_PATHS = frozenset(
    {
        "configs/runtime/calculation/registry-v1.3.0.json",
        "configs/runtime/calculation/runtime_contract-v1.3.0.json",
        "configs/runtime/calculation/calculation_output_schema-v1.3.0.json",
        "configs/runtime/calculation/source_registry-v1.6.0.json",
        "configs/runtime/calculation/conformance_gate-v1.6.0.json",
        "configs/runtime/calculation/profiles/KR_CIVIL_MIDNIGHT_V1-v1.3.0.json",
        "requirements-runtime-calculator-v1.3.txt",
        "scripts/runtime/calculation/contracts.py",
        "scripts/runtime/calculation/contracts_v1_3.py",
        "scripts/runtime/calculation/engine.py",
        "scripts/runtime/calculation/engine_v1_3.py",
        "scripts/runtime/calculation/facts.py",
        "scripts/runtime/calculation/facts_v1_3.py",
        "scripts/runtime/calculation/skyfield_solar_terms.py",
        "scripts/runtime/calculation/solar_term_types.py",
        "scripts/runtime/calculation/solar_terms.py",
        "scripts/runtime/calculation/solar_terms_v1_3.py",
        "scripts/evaluation/saju_runtime/conformance_v7.py",
        "scripts/evaluation/saju_runtime/conformance_v8.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v3.py",
    }
)


class RuntimeConformanceV8Error(RuntimeError):
    """conformance v8 입력·runtime binding·artifact 계약 위반."""


def _chart_arguments(birth_date: str) -> dict[str, Any]:
    return {
        "birth_date": birth_date,
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "13:00",
        "time_precision": "exact",
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _load_independent_records(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConformanceV8Error("v7 독립 provider records가 없습니다.")
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.endswith("\n"):
                    raise RuntimeConformanceV8Error(
                        "v7 provider records 마지막 줄 형식이 다릅니다."
                    )
                value = json.loads(line, parse_float=Decimal)
                if not isinstance(value, dict):
                    raise RuntimeConformanceV8Error(
                        "v7 provider record가 object가 아닙니다."
                    )
                records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceV8Error(
            "v7 provider records를 읽지 못했습니다."
        ) from exc
    if len(records) != EXPECTED_ROWS:
        raise RuntimeConformanceV8Error("v7 provider records 행 수가 다릅니다.")
    return records


def _runtime_provider_checks(
    provider: SkyfieldSolarTermProvider,
    independent: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        from skyfield.api import load
    except ImportError as exc:
        raise RuntimeConformanceV8Error(
            "독립 TT 비교용 Skyfield를 import하지 못했습니다."
        ) from exc
    independent_timescale = load.timescale(builtin=True)
    records: list[dict[str, Any]] = []
    authority_counts: Counter[str] = Counter()
    identity_failures = 0
    utc_mismatches = 0
    label_mismatches = 0
    official_class_failures = 0
    boundary_failures = 0
    boundary_cases = 0
    deltas: list[float] = []
    previous = None
    for order, reference in enumerate(independent):
        try:
            year = int(reference["year"])
            term_index = int(reference["term_index"])
            independent_tt = Decimal(str(reference["skyfield_tt_jd"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV8Error(
                "v7 provider record identity가 다릅니다."
            ) from exc
        boundary = provider.boundary(year, term_index)
        expected_order = (year - 1900) * 12 + sorted(JIE_TO_MONTH).index(term_index)
        identity_failures += (
            order != expected_order
            or boundary.term_name != reference.get("term_name")
            or term_index not in JIE_TO_MONTH
        )
        if previous is not None and previous >= boundary.tt_sort_key:
            identity_failures += 1
        previous = boundary.tt_sort_key
        runtime_utc = boundary.instant_utc.isoformat().replace("+00:00", "Z")
        independent_utc = str(reference.get("skyfield_instant_utc"))
        try:
            independent_datetime = datetime.fromisoformat(
                independent_utc.replace("Z", "+00:00")
            )
            if independent_datetime.tzinfo != UTC:
                raise ValueError("독립 root는 UTC여야 합니다.")
            independent_time = independent_timescale.from_datetime(
                independent_datetime
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeConformanceV8Error(
                "독립 provider UTC root 형식이 다릅니다."
            ) from exc
        delta_microseconds = (
            (float(independent_time.whole) - boundary.tt_whole)
            + (float(independent_time.tt_fraction) - boundary.tt_fraction)
        ) * 86_400_000_000.0
        deltas.append(delta_microseconds)
        runtime_label = boundary.official_display_minute_fixed_kst
        independent_label = str(
            reference.get("skyfield_builtin_ut1_display_minute_fixed_kst")
        )
        utc_mismatches += runtime_utc != independent_utc
        label_mismatches += runtime_label != independent_label
        authority_counts[boundary.authority_class] += 1
        expected_official = None if year < 1920 else "SOURCE_HARD_FACT"
        official_class_failures += (
            boundary.official_source_evidence_class != expected_official
        )
        for position, instant, expected in (
            (
                "before",
                boundary.instant_utc - timedelta(microseconds=1),
                -1,
            ),
            ("exact", boundary.instant_utc, 0),
            ("after", boundary.instant_utc + timedelta(microseconds=1), 1),
        ):
            boundary_cases += 1
            actual = provider.compare_instant(instant, boundary)
            if actual != expected:
                boundary_failures += 1
        records.append(
            {
                "order": order,
                "year": year,
                "term_index": term_index,
                "term_name": boundary.term_name,
                "runtime_tt_jd": boundary.tt_jd_text,
                "independent_tt_jd": str(independent_tt),
                "tt_delta_microseconds": round(delta_microseconds, 6),
                "runtime_instant_utc": runtime_utc,
                "independent_instant_utc": independent_utc,
                "utc_exact_match": runtime_utc == independent_utc,
                "runtime_official_display_minute_fixed_kst": runtime_label,
                "independent_official_display_minute_fixed_kst": independent_label,
                "official_display_minute_match": runtime_label == independent_label,
                "authority_class": boundary.authority_class,
                "official_source_evidence_class": (
                    boundary.official_source_evidence_class
                ),
                "provider_generated_value_is_official": False,
            }
        )
    maximum = max(abs(value) for value in deltas)
    checks = {
        "runtime_provider_rows_complete": len(records) == EXPECTED_ROWS,
        "runtime_provider_identity_failure_zero": identity_failures == 0,
        "runtime_vs_independent_tt_delta_within_one_microsecond": maximum <= 1.0,
        "runtime_vs_independent_utc_mismatch_zero": utc_mismatches == 0,
        "runtime_vs_independent_ut1_label_mismatch_zero": label_mismatches == 0,
        "tt_boundary_cases_complete": boundary_cases == EXPECTED_BOUNDARY_CASES,
        "tt_boundary_mismatch_zero": boundary_failures == 0,
        "authority_counts_exact": dict(authority_counts)
        == EXPECTED_AUTHORITY_COUNTS,
        "official_source_class_failure_zero": official_class_failures == 0,
    }
    return (
        {
            "provider_identity": provider.identity(),
            "rows": len(records),
            "expected_rows": EXPECTED_ROWS,
            "identity_failures": identity_failures,
            "tt_delta_microseconds": {
                "maximum_absolute": round(maximum, 6),
                "mean_absolute": round(
                    sum(abs(value) for value in deltas) / len(deltas), 6
                ),
            },
            "utc_mismatches": utc_mismatches,
            "ut1_label_mismatches": label_mismatches,
            "boundary_cases": boundary_cases,
            "boundary_failures": boundary_failures,
            "authority_counts": dict(sorted(authority_counts.items())),
            "official_source_class_failures": official_class_failures,
            "checks": checks,
        },
        records,
    )


def _runtime_output_checks(engine: SajuRuntimeEngineV13) -> dict[str, Any]:
    cases = (
        ("profile_deterministic", "1919-06-01", PROFILE_DETERMINISTIC),
        ("past_official", "1964-09-08", PAST_OFFICIAL_CORROBORATED),
        (
            "future_nonapproval",
            "2026-09-08",
            FORECAST_DIAGNOSTIC_NONAPPROVAL,
        ),
    )
    rows: list[dict[str, Any]] = []
    failures = 0
    for name, birth_date, expected in cases:
        internal, visible = execute_candidate_runtime_tool_v1_3(
            engine, "calculate_saju_chart", _chart_arguments(birth_date)
        )
        evidence = internal.get("hard_facts", {}).get("solar_term_evidence", {})
        visible_evidence = visible.get("hard_facts", {}).get(
            "solar_term_evidence", {}
        )
        warning_codes = {
            warning.get("code") for warning in internal.get("warnings", [])
        }
        passed = (
            internal.get("status") == "partial"
            and internal.get("code") == "RUNTIME_RELEASE_PENDING"
            and internal.get("fact_authority") == "HARD_CANDIDATE"
            and evidence.get("overall_authority") == expected
            and visible_evidence == evidence
            and internal.get("chart_id", "").startswith("sc2_")
            and "RUNTIME_RELEASE_PENDING" in warning_codes
            and (
                expected != FORECAST_DIAGNOSTIC_NONAPPROVAL
                or "FUTURE_SOLAR_TERM_FORECAST_NONAPPROVAL" in warning_codes
            )
        )
        failures += not passed
        rows.append(
            {
                "case": name,
                "expected_authority": expected,
                "actual_authority": evidence.get("overall_authority"),
                "fact_authority": internal.get("fact_authority"),
                "status": internal.get("status"),
                "warning_codes": sorted(str(value) for value in warning_codes),
                "model_visible_evidence": visible_evidence == evidence,
                "passed": passed,
            }
        )
    return {
        "cases": len(rows),
        "failures": failures,
        "rows": rows,
        "candidate_fact_authority_only": all(
            row["fact_authority"] == "HARD_CANDIDATE" for row in rows
        ),
        "future_hard_gt_emitted": False,
    }


def _records_payload(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(record) + b"\n" for record in records)


def _artifact_identity(payload: bytes) -> dict[str, Any]:
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _write_artifacts(
    report: dict[str, Any], records_payload: bytes, output_base: Path
) -> Path:
    canonical_report = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(canonical_report).hexdigest()[:12]
    directory = output_base / build_id
    aggregate_payload = canonical_report + b"\n"
    artifacts = {
        "aggregate.json": aggregate_payload,
        "runtime_provider_records.jsonl": records_payload,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v8",
        "artifacts": {
            name: _artifact_identity(payload)
            for name, payload in sorted(artifacts.items())
        },
        "candidate_runtime_conformance_passed": report[
            "candidate_runtime_conformance_passed"
        ],
        "strict_runtime_provider_gate_passed": report[
            "strict_runtime_provider_gate_passed"
        ],
        "runtime_gate_passed": False,
        "runtime_approved": False,
        "release_registry_creation_allowed": False,
        "mix20k_v3_1_generated": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    artifacts["build_manifest.json"] = canonical_json_bytes(manifest) + b"\n"
    if directory.exists():
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeConformanceV8Error("같은 v8 build 경로가 directory가 아닙니다.")
        for filename, payload in artifacts.items():
            path = directory / filename
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise RuntimeConformanceV8Error(
                    f"같은 v8 build ID의 artifact가 다릅니다: {filename}"
                )
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        for filename, payload in artifacts.items():
            with (directory / filename).open("xb") as stream:
                stream.write(payload)
    except OSError as exc:
        raise RuntimeConformanceV8Error(
            "v8 build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    lunar_snapshot: Path,
    openapi_solar_term_snapshot: Path,
    official_solar_term_snapshot: Path,
    minute_snapshot: Path,
    almanac_snapshot: Path,
    iers_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_3()
    gate = load_strict_json_object_v1_2(GATE_V16_PATH)
    minimum = gate["minimum_cases"]
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    openapi_rows, openapi_identity = _load_term_coverage(openapi_solar_term_snapshot)
    official_rows, official_identity = _load_official_terms(
        official_solar_term_snapshot
    )
    official_manifest = load_strict_json_object_v1_2(
        official_solar_term_snapshot.with_name(OFFICIAL_TERM_MANIFEST_FILENAME)
    )
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    almanac_row, almanac_identity = _load_almanac(almanac_snapshot)
    _, iers_identity = _load_iers_snapshot(iers_snapshot)
    calendar_provider = KoreanLunarCalendarProvider()
    baseline_signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    baseline_engine = SajuRuntimeEngineV12(
        signer=baseline_signer,
        enable_candidate_runtime=True,
        calendar_provider=calendar_provider,
    )
    lunar = _kasi_checks(calendar_provider, lunar_rows)
    source_crosscheck = _crosscheck_kasi_sources(openapi_rows, official_rows)
    comparison = compare_providers(
        ephemeris,
        iers_snapshot=iers_snapshot,
        official_current_rows=official_rows,
        official_collected_at=official_manifest["collected_at"],
        advisory_minute_rows=minute_rows,
        historical_almanac_row=almanac_row,
        include_records=True,
    )
    independent = comparison.pop("records")
    comparison["records_in_report"] = False
    preferred = comparison["provider_candidate_evidence"][
        "skyfield_de440s_builtin_ut1"
    ]
    selection = comparison["selection"]
    preferred_boundary = comparison[
        "preferred_candidate_boundary_assignment_checks"
    ]
    baseline_boundary = _internal_boundary_checks()
    policy = _policy_checks(baseline_engine)
    invariants = _synthetic_invariant_checks(baseline_engine)
    host = _host_invariance(baseline_engine)
    hmac_invariants = _hmac_invariants(baseline_signer)
    data_availability_checks = {
        "kasi_lunar_snapshot_complete": lunar["rows"]
        == minimum["kasi_lunar_days"],
        "openapi_requested_range_scan_complete": openapi_identity["years_scanned"]
        == 150,
        "openapi_observed_rows_complete": openapi_identity["rows"] == 696,
        "official_download_snapshot_collected": official_identity["source_rows"]
        == 4_343,
        "official_download_jie_coverage_complete": official_identity["jie_rows"]
        == 2_172
        and official_identity["jie_coverage_complete"],
        "openapi_and_official_download_crosscheck_completed": source_crosscheck[
            "official_download_missing_rows"
        ]
        == 0,
        "institutional_advisory_rows_collected": len(minute_rows) == 84,
        "historical_1964_document_collected": almanac_identity["complete"],
        "iers_diagnostic_snapshot_collected": iers_identity["rows"] == 19_969,
        "official_snapshot_values_not_provider_filled": official_identity[
            "provider_values_used"
        ]
        is False
        and comparison["official_source_values_filled_from_provider"] is False,
    }
    provider_candidate_checks = {
        "preferred_candidate_selected": selection["preferred_candidate"]
        == "skyfield_de440s_builtin_ut1",
        "preferred_candidate_evidence_passed": preferred["candidate_eligible"],
        "official_past_future_partition_complete": preferred[
            "past_rows_at_snapshot_collection"
        ]
        == 1_280
        and preferred["future_forecast_rows_at_snapshot_collection"] == 280,
        "root_solver_convergence_not_blocking": comparison[
            "root_solver_diagnostic"
        ]["status"]
        == "root_convergence_not_explanation_for_multi_second_delta",
        "preferred_candidate_boundary_cases_complete": preferred_boundary["cases"]
        == minimum["tt_boundary_assignment"],
        "preferred_candidate_boundary_mismatch_zero": preferred_boundary[
            "mismatch_rows"
        ]
        == 0,
    }
    baseline_conformance_checks = {
        "kasi_lunar_conversion_mismatch_zero": lunar["solar_lunar_mismatches"]
        == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar["day_ganzhi_mismatches"] == 0,
        "provider_rows_complete": comparison["rows"]
        == minimum["provider_jie_instants"],
        "provider_term_identity_failure_zero": comparison["term_identity_failures"]
        == 0,
        "provider_chronological_order_failure_zero": comparison[
            "astronomy_chronological_order_failures"
        ]
        == 0
        and comparison["skyfield_chronological_order_failures"] == 0,
        "provider_fixed_120_second_regression_guard": comparison[
            "fixed_regression_guard"
        ]["failures"]
        == 0,
        "baseline_internal_boundary_cases_complete": baseline_boundary["cases"]
        == minimum["tt_boundary_assignment"],
        "baseline_internal_boundary_mismatch_zero": baseline_boundary[
            "mismatch_rows"
        ]
        == 0,
        "policy_fixture_mismatch_zero": policy["mismatch_rows"] == 0,
        "unknown_range_complete": invariants["unknown_range_cases"]
        >= minimum["unknown_range"],
        "guessed_unknown_hour_zero": invariants["guessed_unknown_hour"] == 0,
        "hmac_id_vectors_complete": hmac_invariants["vectors"]
        >= minimum["hmac_id_vectors"],
        "hmac_id_mismatch_zero": all(
            hmac_invariants[key] == 0
            for key in (
                "reproducibility_mismatches",
                "prefix_failures",
                "domain_collisions",
                "key_separation_failures",
            )
        ),
        "unsupported_foreign_complete": invariants["unsupported_foreign_cases"]
        >= minimum["unsupported_foreign"],
        "unsupported_foreign_failure_zero": invariants[
            "unsupported_foreign_failures"
        ]
        == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "source_version_id_failure_zero": invariants[
            "source_version_id_failures"
        ]
        == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
    }
    baseline_summary = {
        "suite_version": "saju-runtime-conformance-v7.0.0-recalculated-in-v8",
        "data_availability_gate_passed": all(data_availability_checks.values()),
        "provider_candidate_gate_passed": all(provider_candidate_checks.values()),
        "strict_runtime_provider_gate_passed": False,
        "baseline_conformance_gate_passed": all(
            baseline_conformance_checks.values()
        ),
        "data_availability_checks": data_availability_checks,
        "provider_candidate_checks": provider_candidate_checks,
        "baseline_conformance_checks": baseline_conformance_checks,
        "official_kasi_solar_term_availability": {
            "openapi_years_scanned": openapi_identity["years_scanned"],
            "openapi_rows": openapi_identity["rows"],
            "official_download_rows": official_identity["source_rows"],
            "official_download_jie_rows": official_identity["jie_rows"],
            "official_runtime_range_jie_rows": 1_560,
            "official_past_rows_at_collection": preferred[
                "past_rows_at_snapshot_collection"
            ],
            "official_future_rows_at_collection": preferred[
                "future_forecast_rows_at_snapshot_collection"
            ],
        },
        "preferred_provider_candidate": selection["preferred_candidate"],
        "preferred_provider_evidence": preferred,
        "baengno_1964_evidence": comparison["baengno_1964_interpretation"],
    }
    baseline_sha256 = hashlib.sha256(
        canonical_json_bytes(baseline_summary)
    ).hexdigest()

    provider = SkyfieldSolarTermProvider(ephemeris)
    try:
        provider_summary, runtime_records = _runtime_provider_checks(
            provider, independent
        )
        engine = SajuRuntimeEngineV13(
            signer=RuntimeIdSigner.for_test(TEST_SIGNER_KEY),
            enable_candidate_runtime=True,
            calendar_provider=KoreanLunarCalendarProvider(),
            solar_term_provider=provider,
        )
        output_checks = _runtime_output_checks(engine)
    finally:
        provider.close()

    mismatch_rows = preferred["official_current_minute_mismatch_rows"]
    past_raw_mismatches = sum(
        row.get("temporal_class") == "past" for row in mismatch_rows
    )
    future_raw_mismatches = sum(
        row.get("temporal_class") == "future_forecast" for row in mismatch_rows
    )
    binding_checks = dict(provider_summary["checks"])
    binding_checks.update(
        {
            "provider_identity_is_skyfield_runtime": provider_summary[
                "provider_identity"
            ]["provider_id"]
            == SkyfieldSolarTermProvider.provider_id,
            "automatic_download_or_fallback_false": provider_summary[
                "provider_identity"
            ]["automatic_download_or_fallback"]
            is False,
            "astronomy_engine_fallback_false": provider_summary[
                "provider_identity"
            ]["astronomy_engine_fallback"]
            is False,
            "runtime_output_authority_cases_pass": output_checks["failures"] == 0,
        }
    )
    past_checks = {
        "past_authority_rows_exact": provider_summary["authority_counts"].get(
            PAST_OFFICIAL_CORROBORATED
        )
        == 1_280,
        "official_printed_date_mismatch_zero": preferred[
            "official_current_date_mismatches"
        ]
        == 0,
        "past_rounding_interval_plus_uncertainty_failure_zero": preferred[
            "past_uncertainty_failures"
        ]
        == 0,
        "past_raw_minute_mismatches_recorded": past_raw_mismatches == 14,
    }
    future_checks = {
        "future_authority_rows_exact": provider_summary["authority_counts"].get(
            FORECAST_DIAGNOSTIC_NONAPPROVAL
        )
        == 280,
        "future_raw_minute_mismatches_recorded": future_raw_mismatches == 8,
        "future_output_is_not_hard_gt": output_checks["future_hard_gt_emitted"]
        is False,
        "fixed_snapshot_cutoff_used": OFFICIAL_SNAPSHOT_COLLECTED_AT
        == "2026-08-31T15:16:50+00:00",
    }
    baseline_checks = {
        "v7_data_availability_gate_passed": baseline_summary[
            "data_availability_gate_passed"
        ]
        is True,
        "v7_provider_candidate_gate_passed": baseline_summary[
            "provider_candidate_gate_passed"
        ]
        is True,
        "v7_baseline_conformance_gate_passed": baseline_summary[
            "baseline_conformance_gate_passed"
        ]
        is True,
        "v7_strict_runtime_provider_gate_remains_blocked": baseline_summary[
            "strict_runtime_provider_gate_passed"
        ]
        is False,
        "raw_minute_mismatch_total_is_22": preferred[
            "official_current_minute_label_mismatches"
        ]
        == 22,
        "baengno_1964_current_and_historical_sources_remain_distinct": (
            baseline_summary["baengno_1964_evidence"][
                "current_calculation_reference"
            ]
            == "1964-09-07T23:59+09:00"
            and baseline_summary["baengno_1964_evidence"]["historical_printed_label"]
            == "1964-09-07 24:00 KST"
            and baseline_summary["baengno_1964_evidence"][
                "normalization_caused_current_source_mismatch"
            ]
            is False
        ),
    }
    candidate_passed = all(
        [
            *binding_checks.values(),
            *past_checks.values(),
            *future_checks.values(),
            *baseline_checks.values(),
        ]
    )
    records_payload = _records_payload(runtime_records)
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION_V8,
        "profile_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V13,
        "output_schema_version": OUTPUT_SCHEMA_VERSION_V13,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "status": (
            "candidate_runtime_conformance_passed_release_blocked"
            if candidate_passed
            else "candidate_runtime_conformance_failed"
        ),
        "inputs": {
            "runtime_registry_sha256": sha256_file(REGISTRY_V13_PATH),
            "gate_sha256": sha256_file(GATE_V16_PATH),
            "source_registry_sha256": sha256_file(SOURCE_REGISTRY_V16_PATH),
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_24_divisions_openapi_observed_coverage": openapi_identity,
                "kasi_official_current_solar_terms": official_identity,
                "kasi_nonformal_minute_reference": minute_identity,
                "kasi_1964_historical_almanac": almanac_identity,
                "iers_finals2000a_diagnostic": iers_identity,
            },
            "baseline_v7_recalculation_sha256": baseline_sha256,
            "implementation_sha256": {
                path: sha256_file(REPO_ROOT / path)
                for path in sorted(IMPLEMENTATION_PATHS)
            },
            "test_signer": {
                "kind": "fixed_non_production_injected",
                "key_value_recorded": False,
                "accepted_for_production": False,
            },
        },
        "baseline_v7_recalculation": baseline_summary,
        "runtime_provider_conformance": provider_summary,
        "runtime_output_authority_checks": output_checks,
        "candidate_runtime_binding_checks": binding_checks,
        "past_authority_checks": past_checks,
        "future_authority_separation_checks": future_checks,
        "baseline_checks": baseline_checks,
        "candidate_runtime_provider_bound": candidate_passed,
        "candidate_runtime_conformance_passed": candidate_passed,
        "past_authority_gate_passed": all(past_checks.values()),
        "future_authority_separation_gate_passed": all(future_checks.values()),
        "strict_runtime_provider_gate_passed": False,
        "runtime_gate_passed": False,
        "runtime_approved": False,
        "release_approval_performed": False,
        "release_registry_creation_allowed": False,
        "candidate_runtime_provider_changed": True,
        "production_runtime_provider_changed": False,
        "runtime_feature_flag_default": False,
        "app_binding_performed": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "mix20k_v3_1_generated": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
        "public_diagnostics": {
            "runtime_provider_records": {
                "rows": len(runtime_records),
                **_artifact_identity(records_payload),
                "contains_birth_or_session_data": False,
            }
        },
    }
    if any(
        report[key] is not False
        for key in (
            "runtime_gate_passed",
            "runtime_approved",
            "release_approval_performed",
            "release_registry_creation_allowed",
            "production_runtime_provider_changed",
            "runtime_feature_flag_default",
            "app_binding_performed",
            "mix20k_v3_1_regeneration_allowed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
            "sealed_blind_accessed",
            "raw_restricted_samples_in_report",
        )
    ):
        raise RuntimeConformanceV8Error("v8 범위를 넘는 상태 변경이 감지됐습니다.")
    directory = _write_artifacts(report, records_payload, output_base)
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v8")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--kasi-lunar-snapshot", type=Path, required=True)
    run.add_argument("--kasi-solar-term-snapshot", type=Path, required=True)
    run.add_argument("--kasi-official-solar-term-snapshot", type=Path, required=True)
    run.add_argument("--kasi-minute-snapshot", type=Path, required=True)
    run.add_argument("--kasi-almanac-1964-snapshot", type=Path, required=True)
    run.add_argument("--iers-snapshot", type=Path, required=True)
    run.add_argument("--ephemeris", type=Path, required=True)
    run.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            lunar_snapshot=args.kasi_lunar_snapshot,
            openapi_solar_term_snapshot=args.kasi_solar_term_snapshot,
            official_solar_term_snapshot=args.kasi_official_solar_term_snapshot,
            minute_snapshot=args.kasi_minute_snapshot,
            almanac_snapshot=args.kasi_almanac_1964_snapshot,
            iers_snapshot=args.iers_snapshot,
            ephemeris=args.ephemeris,
            output_base=args.output_base,
        )
    except (
        RuntimeCalculationError,
        RuntimeConformanceV3Error,
        RuntimeConformanceV5Error,
        RuntimeConformanceV6Error,
        RuntimeConformanceV7Error,
        RuntimeConformanceV8Error,
        SolarTermProviderComparisonError,
        OSError,
        ValueError,
    ) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "build": str(directory),
                "candidate_runtime_provider_bound": report[
                    "candidate_runtime_provider_bound"
                ],
                "candidate_runtime_conformance_passed": report[
                    "candidate_runtime_conformance_passed"
                ],
                "strict_runtime_provider_gate_passed": False,
                "runtime_gate_passed": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
