# conformance_v6.py - KASI 데이터 가용성과 절입 provider 적격성을 분리해 fail-closed 집계한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.evaluation.external_conformance import sha256_file
from scripts.evaluation.saju_runtime.conformance import (
    KASI_FIXTURE,
    POLICY_FIXTURE,
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
    TEST_SIGNER_KEY,
    _hmac_invariants,
    _internal_boundary_checks,
)
from scripts.evaluation.saju_runtime.conformance_v5 import (
    RuntimeConformanceV5Error,
    _is_utc_timestamp,
    _load_almanac,
    _load_term_coverage,
    _private_json,
    _private_jsonl,
    _regular_bytes,
    _strict_json,
)
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import (
    artifact_hash,
    render_delta_by_year_svg,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    KasiAlmanac1964CollectorError,
)
from scripts.evaluation.saju_runtime.kasi_collector_v1_1 import (
    KasiCollectorV11Error,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    COLLECTOR_VERSION as OFFICIAL_TERM_COLLECTOR_VERSION,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    DOWNLOAD_ENDPOINT as OFFICIAL_TERM_DOWNLOAD_ENDPOINT,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    END_YEAR as OFFICIAL_TERM_END_YEAR,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    EXPECTED_JIE_ROWS as OFFICIAL_DOWNLOAD_EXPECTED_JIE_ROWS,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    EXPECTED_ROWS as OFFICIAL_DOWNLOAD_EXPECTED_ROWS,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    KNOWN_UPSTREAM_OMISSIONS,
    MAX_DOWNLOAD_BYTES,
    KasiOfficialSolarTermsCollectorError,
    parse_download,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    MANIFEST_FILENAME as OFFICIAL_TERM_MANIFEST_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    RESPONSE_FILENAME as OFFICIAL_TERM_RESPONSE_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    SNAPSHOT_FILENAME as OFFICIAL_TERM_SNAPSHOT_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    SOURCE_PAGE as OFFICIAL_TERM_SOURCE_PAGE,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    START_YEAR as OFFICIAL_TERM_START_YEAR,
)
from scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector import (
    _canonical_jsonl as official_canonical_jsonl,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    KasiTermCoverageCollectorError,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v2 import (
    COMPARISON_VERSION,
    EXPECTED_OFFICIAL_JIE_ROWS,
    SolarTermProviderComparisonError,
    compare_providers,
    records_jsonl,
    render_time_scale_scatter_svg,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import (
    ENGINE_VERSION_V12,
    ID_CONTRACT_VERSION_V2,
    runtime_source_versions_v1_2,
    validate_contract_registry_v1_2,
)
from scripts.runtime.calculation.engine_v1_2 import SajuRuntimeEngineV12
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

SCHEMA_VERSION = "1.4.0"
SUITE_VERSION = "saju-runtime-conformance-v6.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.4.0"
GATE_PATH = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.4.0.json"
SOURCE_REGISTRY_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.4.0.json"
)
GATE_SHA256 = "7353131db05abc3c66783e88bc8df100852048b32b69cf6681c6d623fcd8e154"
SOURCE_REGISTRY_SHA256 = (
    "83d2c6e168d1e45166622ff9462a130fd25594395ce8102de384892652f401e9"
)
GATE_PARENT_PATH = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.3.1.json"
GATE_PARENT_SHA256 = (
    "c8fbd682e54a5c0a1e9590029d0ad2da0587e239783aaac0d0c9a7eac8ec9278"
)
SOURCE_REGISTRY_PARENT_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.3.1.json"
)
SOURCE_REGISTRY_PARENT_SHA256 = (
    "cfcb9ced0830a1817fab13f0450eefb3492cab8f964ca82b07f06efa52ba69cd"
)
OFFICIAL_TERM_COLLECTOR_PATH = (
    REPO_ROOT
    / "scripts/evaluation/saju_runtime/kasi_official_solar_terms_collector.py"
)
OFFICIAL_TERM_COLLECTOR_SHA256 = (
    "cd4556ad762e6fbc97f71f9a4271682c338932ee8055624c18643c8d9175ec25"
)
EXPECTED_OPENAPI_ROWS = 696
EXPECTED_OPENAPI_JIE_ROWS = 348
EXPECTED_OPENAPI_SCAN_YEARS = 150
EXPECTED_OFFICIAL_DOWNLOAD_ROWS = 4343
EXPECTED_PROFILE_DETERMINISTIC_ROWS = 240
MAX_PRIVATE_JSONL_BYTES = 256 * 1024 * 1024

IMPLEMENTATION_PATHS = frozenset(
    {
        "configs/runtime/calculation/conformance_gate-v1.4.0.json",
        "configs/runtime/calculation/source_registry-v1.4.0.json",
        "scripts/evaluation/saju_runtime/conformance_v5.py",
        "scripts/evaluation/saju_runtime/conformance_v6.py",
        "scripts/evaluation/saju_runtime/kasi_almanac_1964_collector.py",
        "scripts/evaluation/saju_runtime/kasi_official_solar_terms_collector.py",
        "scripts/evaluation/saju_runtime/kasi_term_coverage_collector_v1_2.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v1.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v2.py",
        "scripts/evaluation/saju_runtime/strict_json.py",
        "scripts/runtime/calculation/engine_v1_2.py",
        "scripts/runtime/calculation/solar_terms.py",
    }
)


class RuntimeConformanceV6Error(RuntimeError):
    """conformance v6 입력·분리 Gate·산출물 계약 위반."""


def _validate_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    gate = _strict_json(GATE_PATH)
    registry = _strict_json(SOURCE_REGISTRY_PATH)
    if (
        sha256_file(GATE_PATH) != GATE_SHA256
        or sha256_file(SOURCE_REGISTRY_PATH) != SOURCE_REGISTRY_SHA256
        or sha256_file(GATE_PARENT_PATH) != GATE_PARENT_SHA256
        or sha256_file(SOURCE_REGISTRY_PARENT_PATH)
        != SOURCE_REGISTRY_PARENT_SHA256
        or gate.get("parent")
        != {
            "path": "configs/runtime/calculation/conformance_gate-v1.3.1.json",
            "sha256": GATE_PARENT_SHA256,
        }
        or registry.get("parent")
        != {
            "path": "configs/runtime/calculation/source_registry-v1.3.1.json",
            "sha256": SOURCE_REGISTRY_PARENT_SHA256,
        }
    ):
        raise RuntimeConformanceV6Error("v1.4.0 설정 hash chain이 다릅니다.")
    official_sources = registry.get("official_sources")
    official_terms = (
        official_sources.get("kasi_official_solar_terms_download")
        if isinstance(official_sources, dict)
        else None
    )
    coverage = registry.get("coverage_policy")
    promotion = gate.get("promotion_effects")
    availability_gate = gate.get("data_availability_gate")
    eligibility_gate = gate.get("provider_eligibility_gate")
    if (
        gate.get("schema_version") != SCHEMA_VERSION
        or gate.get("suite_version") != SUITE_VERSION
        or gate.get("profile_id") != POLICY_ID
        or not isinstance(availability_gate, dict)
        or availability_gate.get("all_accessible_official_data_collected") is not True
        or availability_gate.get("official_snapshot_values_filled_from_provider")
        is not False
        or not isinstance(eligibility_gate, dict)
        or eligibility_gate.get("institutional_advisory_can_block") is not False
        or not isinstance(promotion, dict)
        or any(
            promotion.get(key) is not False
            for key in (
                "runtime_approved",
                "release_approval_performed",
                "app_binding_performed",
                "mix20k_v3_1_regeneration_allowed",
                "training_promotion_allowed",
                "phase5_training_performed",
            )
        )
        or registry.get("schema_version") != SCHEMA_VERSION
        or registry.get("registry_version") != "saju-runtime-sources-v1.4.0"
        or not isinstance(official_terms, dict)
        or official_terms.get("collector_version")
        != OFFICIAL_TERM_COLLECTOR_VERSION
        or official_terms.get("collector_sha256")
        != OFFICIAL_TERM_COLLECTOR_SHA256
        or official_terms.get("evidence_class") != "SOURCE_HARD_FACT"
        or official_terms.get("provider_values_used") is not False
        or not isinstance(coverage, dict)
        or coverage.get("missing_official_evidence_may_be_filled_by_generated_values")
        is not False
        or coverage.get("target_without_official_coverage", {}).get(
            "evidence_class"
        )
        != "PROFILE_DETERMINISTIC"
        or registry.get("official_sources", {})
        .get("kasi_calendar_display_minutes", {})
        .get("hard_blocking")
        is not False
    ):
        raise RuntimeConformanceV6Error(
            "v1.4.0 분리 Gate 또는 source registry가 다릅니다."
        )
    return gate, registry


def _load_official_terms(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path.name != OFFICIAL_TERM_SNAPSHOT_FILENAME:
        raise RuntimeConformanceV6Error("KASI 공식 24기 snapshot 파일명이 다릅니다.")
    directory = path.parent
    response_path = directory / OFFICIAL_TERM_RESPONSE_FILENAME
    manifest_path = directory / OFFICIAL_TERM_MANIFEST_FILENAME
    rows = _private_jsonl(path)
    manifest = _private_json(manifest_path)
    response_payload = _regular_bytes(
        response_path,
        maximum=MAX_DOWNLOAD_BYTES,
        private=True,
        description="KASI 공식 24기 원문",
    )
    snapshot_payload = _regular_bytes(
        path,
        maximum=MAX_PRIVATE_JSONL_BYTES,
        private=True,
        description="KASI 공식 24기 snapshot",
    )
    derived, parsed = parse_download(response_payload)
    actual_artifacts = {
        response_path.name: artifact_hash(response_payload),
        path.name: artifact_hash(snapshot_payload),
    }
    expected_omissions = [
        {"year": year, "source_kind": kind}
        for year, kind in sorted(KNOWN_UPSTREAM_OMISSIONS)
    ]
    if (
        rows != derived
        or snapshot_payload != official_canonical_jsonl(derived)
        or manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "complete_available_official_download"
        or manifest.get("collector_version") != OFFICIAL_TERM_COLLECTOR_VERSION
        or manifest.get("collector_sha256") != OFFICIAL_TERM_COLLECTOR_SHA256
        or sha256_file(OFFICIAL_TERM_COLLECTOR_PATH)
        != OFFICIAL_TERM_COLLECTOR_SHA256
        or manifest.get("source_page") != OFFICIAL_TERM_SOURCE_PAGE
        or manifest.get("download_endpoint") != OFFICIAL_TERM_DOWNLOAD_ENDPOINT
        or manifest.get("artifacts") != actual_artifacts
        or manifest.get("parsed") != parsed
        or parsed.get("source_rows") != EXPECTED_OFFICIAL_DOWNLOAD_ROWS
        or parsed.get("expected_source_rows") != OFFICIAL_DOWNLOAD_EXPECTED_ROWS
        or parsed.get("known_upstream_omissions") != expected_omissions
        or parsed.get("jie_rows") != OFFICIAL_DOWNLOAD_EXPECTED_JIE_ROWS
        or parsed.get("jie_coverage_complete") is not True
        or manifest.get("credential_used") is not False
        or manifest.get("private_path_recorded") is not False
        or manifest.get("provider_values_used") is not False
        or not _is_utc_timestamp(manifest.get("collected_at"))
    ):
        raise RuntimeConformanceV6Error(
            "KASI 공식 24기 snapshot provenance가 다릅니다."
        )
    return rows, {
        "kind": "private_official_current_calculation_solar_terms",
        "evidence_class": "SOURCE_HARD_FACT",
        "provided": True,
        "source_range": [OFFICIAL_TERM_START_YEAR, OFFICIAL_TERM_END_YEAR],
        "source_rows": len(rows),
        "expected_rows_before_known_omission": OFFICIAL_DOWNLOAD_EXPECTED_ROWS,
        "known_upstream_omissions": expected_omissions,
        "jie_rows": parsed["jie_rows"],
        "expected_jie_rows": OFFICIAL_DOWNLOAD_EXPECTED_JIE_ROWS,
        "jie_coverage_complete": True,
        "snapshot_sha256": sha256_file(path),
        "response_sha256": sha256_file(response_path),
        "manifest_sha256": sha256_file(manifest_path),
        "origin_collector_version": OFFICIAL_TERM_COLLECTOR_VERSION,
        "origin_collector_sha256": OFFICIAL_TERM_COLLECTOR_SHA256,
        "provider_values_used": False,
        "private_path_recorded": False,
    }


def _crosscheck_kasi_sources(
    openapi_rows: list[dict[str, Any]], official_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    official = {
        (row["year"], row["term_index"]): row for row in official_rows
    }
    mismatches: list[dict[str, Any]] = []
    missing: list[dict[str, int]] = []
    printed_date_convention_differences: list[dict[str, Any]] = []
    for row in openapi_rows:
        identity = (row["year"], row["term_index"])
        current = official.get(identity)
        if current is None:
            missing.append({"year": identity[0], "term_index": identity[1]})
            continue
        normalized_date = datetime.fromisoformat(
            current["reference_local_minute"]
        ).date().isoformat()
        if row["local_date"] != normalized_date:
            mismatches.append(
                {
                    "year": identity[0],
                    "term_index": identity[1],
                    "openapi_date": row["local_date"],
                    "official_download_normalized_date": normalized_date,
                }
            )
        elif row["local_date"] != current["printed_local_date"]:
            printed_date_convention_differences.append(
                {
                    "year": identity[0],
                    "term_index": identity[1],
                    "openapi_normalized_date": row["local_date"],
                    "official_download_printed_date": current[
                        "printed_local_date"
                    ],
                    "official_download_reference_local_minute": current[
                        "reference_local_minute"
                    ],
                }
            )
    return {
        "openapi_rows": len(openapi_rows),
        "openapi_jie_rows": sum(
            row["term_index"] in JIE_TO_MONTH for row in openapi_rows
        ),
        "official_download_missing_rows": len(missing),
        "official_download_missing_row_details": missing[:100],
        "normalized_date_mismatches": len(mismatches),
        "normalized_date_mismatch_rows": mismatches[:100],
        "printed_date_convention_differences": len(
            printed_date_convention_differences
        ),
        "printed_date_convention_difference_rows": printed_date_convention_differences[
            :100
        ],
        "mismatch_details_truncated": len(mismatches) > 100,
    }


def _safe_output_base(path: Path) -> Path:
    if path.is_symlink() or path.resolve(strict=False) != REPORT_ROOT.resolve(
        strict=False
    ):
        raise RuntimeConformanceV6Error(
            "conformance v6 output base는 고정 v1.4.0 경로여야 합니다."
        )
    return path.resolve(strict=False)


def _write_artifacts(
    report: dict[str, Any],
    records_payload: bytes,
    raw_svg_payload: bytes,
    time_scale_svg_payload: bytes,
    output_base: Path,
) -> Path:
    build_id = "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate_payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    artifact_identities = {
        "aggregate.json": artifact_hash(aggregate_payload),
        "provider_records.jsonl": artifact_hash(records_payload),
        "provider_delta_by_year.svg": artifact_hash(raw_svg_payload),
        "provider_time_scale_scatter.svg": artifact_hash(time_scale_svg_payload),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v6",
        "artifacts": artifact_identities,
        "data_availability_gate_passed": report[
            "data_availability_gate_passed"
        ],
        "provider_eligibility_gate_passed": report[
            "provider_eligibility_gate_passed"
        ],
        "technical_gate_passed": report["technical_gate_passed"],
        "runtime_approved": False,
        "release_approval_performed": False,
        "release_registry_creation_allowed": False,
        "app_binding_performed": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    payloads = {
        "aggregate.json": aggregate_payload,
        "provider_records.jsonl": records_payload,
        "provider_delta_by_year.svg": raw_svg_payload,
        "provider_time_scale_scatter.svg": time_scale_svg_payload,
        "build_manifest.json": (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeConformanceV6Error("기존 v6 build가 일반 디렉터리가 아닙니다.")
        for filename, payload in payloads.items():
            artifact = directory / filename
            if (
                artifact.is_symlink()
                or not artifact.is_file()
                or artifact.read_bytes() != payload
            ):
                raise RuntimeConformanceV6Error(
                    f"같은 v6 build ID의 artifact가 다릅니다: {filename}"
                )
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        for filename, payload in payloads.items():
            with (directory / filename).open("xb") as stream:
                stream.write(payload)
    except OSError as exc:
        raise RuntimeConformanceV6Error(
            "v6 build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    lunar_snapshot: Path,
    openapi_solar_term_snapshot: Path,
    official_solar_term_snapshot: Path,
    minute_snapshot: Path,
    almanac_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    gate, source_registry = _validate_configs()
    validate_contract_registry_v1_2()
    source_versions = runtime_source_versions_v1_2(
        require_runtime_dependencies=True,
        require_validator_dependencies=True,
    )
    source_versions["conformance_source_registry"] = source_registry[
        "registry_version"
    ]
    _private_jsonl(lunar_snapshot)
    _private_json(lunar_snapshot.with_name("kasi_lunisolar_manifest.json"))
    _private_jsonl(minute_snapshot)
    _private_json(minute_snapshot.with_name("kasi_minute_references_manifest.json"))
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    openapi_rows, openapi_identity = _load_term_coverage(
        openapi_solar_term_snapshot
    )
    official_rows, official_identity = _load_official_terms(
        official_solar_term_snapshot
    )
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    almanac_row, almanac_identity = _load_almanac(almanac_snapshot)
    provider = KoreanLunarCalendarProvider()
    signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    engine = SajuRuntimeEngineV12(
        signer=signer,
        enable_candidate_runtime=True,
        calendar_provider=provider,
    )
    lunar = _kasi_checks(provider, lunar_rows)
    source_crosscheck = _crosscheck_kasi_sources(openapi_rows, official_rows)
    comparison = compare_providers(
        ephemeris,
        official_current_rows=official_rows,
        advisory_minute_rows=minute_rows,
        historical_almanac_row=almanac_row,
        include_records=True,
    )
    records = comparison.pop("records")
    comparison["records_in_report"] = False
    selected_boundary = comparison["selected_provider_boundary_assignment_checks"]
    baseline_boundary = _internal_boundary_checks()
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    hmac_invariants = _hmac_invariants(signer)
    minimum = gate["minimum_cases"]
    historical_current_row = next(
        row
        for row in official_rows
        if row["year"] == 1964 and row["term_index"] == 16
    )
    data_availability_checks = {
        "kasi_lunar_snapshot_complete": lunar["rows"]
        == minimum["kasi_lunar_days"],
        "openapi_requested_range_scan_complete": openapi_identity["years_scanned"]
        == minimum["kasi_term_api_years_scanned"],
        "openapi_observed_rows_complete": openapi_identity["rows"]
        == minimum["kasi_term_api_rows_observed"],
        "official_download_snapshot_collected": official_identity["source_rows"]
        == minimum["kasi_official_download_rows"],
        "official_download_known_omission_exact": official_identity[
            "expected_rows_before_known_omission"
        ]
        == minimum["kasi_official_download_expected_rows_before_known_omission"]
        and len(official_identity["known_upstream_omissions"])
        == minimum["kasi_official_download_known_non_jie_omissions"]
        and official_identity["known_upstream_omissions"]
        == [{"year": 2030, "source_kind": 2}],
        "official_download_jie_coverage_complete": official_identity["jie_rows"]
        == minimum["kasi_official_download_jie_rows"]
        and official_identity["jie_coverage_complete"],
        "openapi_and_official_download_crosscheck_completed": source_crosscheck[
            "official_download_missing_rows"
        ]
        == 0
        and source_crosscheck["openapi_rows"]
        == minimum["kasi_term_api_rows_observed"],
        "institutional_advisory_rows_collected": len(minute_rows)
        == minimum["institutional_advisory_minute_rows"],
        "historical_1964_document_collected": almanac_identity["complete"]
        and minimum["historical_1964_baengno_rows"] == 1,
        "official_snapshot_values_not_provider_filled": official_identity[
            "provider_values_used"
        ]
        is False
        and comparison["official_source_values_filled_from_provider"] is False,
        "target_without_official_rows_classified_profile_deterministic": comparison[
            "target_without_official_rows"
        ]["jie_rows"]
        == minimum["profile_deterministic_jie_rows"]
        and comparison["target_without_official_rows"]["evidence_class"]
        == "PROFILE_DETERMINISTIC",
    }
    hard_evidence = comparison["official_hard_evidence"]
    selection = comparison["selection"]
    provider_eligibility_checks = {
        "at_least_one_provider_matches_all_hard_evidence": any(
            value["eligible"] for value in hard_evidence.values()
        ),
        "eligible_provider_selected": selection["selected_provider"] is not None,
        "selected_provider_boundary_cases_complete": selected_boundary["cases"]
        == minimum["selected_provider_boundary_assignment"],
        "selected_provider_boundary_mismatch_zero": selected_boundary[
            "mismatch_rows"
        ]
        == 0,
    }
    baseline_checks = {
        "kasi_lunar_conversion_mismatch_zero": lunar["rows"]
        == minimum["kasi_lunar_days"]
        and lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar["rows"]
        == minimum["kasi_lunar_days"]
        and lunar["day_ganzhi_mismatches"] == 0,
        "provider_rows_complete": comparison["rows"]
        == minimum["provider_jie_instants_each"],
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
        "time_scale_diagnostic_completed": comparison["time_scale_diagnostic"][
            "status"
        ]
        == "not_delta_t_only",
        "baseline_internal_boundary_cases_complete": baseline_boundary["cases"]
        == minimum["selected_provider_boundary_assignment"],
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
    data_availability_gate_passed = all(data_availability_checks.values())
    provider_eligibility_gate_passed = all(provider_eligibility_checks.values())
    baseline_conformance_gate_passed = all(baseline_checks.values())
    technical_gate_passed = (
        data_availability_gate_passed
        and provider_eligibility_gate_passed
        and baseline_conformance_gate_passed
    )
    if technical_gate_passed:
        status = "technical_gate_passed_release_not_performed"
    elif data_availability_gate_passed and not provider_eligibility_gate_passed:
        status = "data_availability_passed_provider_ineligible"
    elif not data_availability_gate_passed:
        status = "blocked_incomplete_available_official_data"
    else:
        status = "blocked_baseline_conformance_failures"
    records_payload = records_jsonl(records)
    raw_svg_payload = render_delta_by_year_svg(records)
    time_scale_svg_payload = render_time_scale_scatter_svg(records)
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION,
        "profile_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "comparison_version": COMPARISON_VERSION,
        "status": status,
        "source_versions": source_versions,
        "inputs": {
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_24_divisions_openapi_observed_coverage": openapi_identity,
                "kasi_official_current_solar_terms": official_identity,
                "kasi_nonformal_minute_reference": {
                    **minute_identity,
                    "evidence_class": "INSTITUTIONAL_ADVISORY",
                    "hard_blocking": False,
                },
                "kasi_1964_historical_almanac": {
                    **almanac_identity,
                    "document_fact_evidence_class": "SOURCE_HARD_FACT",
                    "provider_adjudication_evidence_class": "INSTITUTIONAL_ADVISORY",
                    "hard_blocking": False,
                },
            },
            "public_fallback_fixture": {
                "path": str(KASI_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(KASI_FIXTURE),
            },
            "policy_fixture": {
                "path": str(POLICY_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(POLICY_FIXTURE),
            },
            "gate_sha256": sha256_file(GATE_PATH),
            "source_registry_sha256": sha256_file(SOURCE_REGISTRY_PATH),
            "source_registry_version": source_registry["registry_version"],
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
        "official_kasi_lunisolar": lunar,
        "kasi_solar_term_source_crosscheck": source_crosscheck,
        "official_kasi_solar_term_availability": {
            "openapi_years_scanned": openapi_identity["years_scanned"],
            "openapi_supported_year_ranges": openapi_identity[
                "supported_year_ranges"
            ],
            "openapi_rows": openapi_identity["rows"],
            "official_download_source_range": official_identity["source_range"],
            "official_download_rows": official_identity["source_rows"],
            "official_download_known_upstream_omissions": official_identity[
                "known_upstream_omissions"
            ],
            "official_download_jie_rows": official_identity["jie_rows"],
            "official_runtime_range_jie_rows": EXPECTED_OFFICIAL_JIE_ROWS,
            "profile_deterministic_jie_rows": EXPECTED_PROFILE_DETERMINISTIC_ROWS,
            "official_snapshot_values_filled_from_provider": False,
        },
        "baengno_1964_evidence": {
            "current_kasi_calculation": {
                "evidence_class": "SOURCE_HARD_FACT",
                "reference_local_minute": historical_current_row[
                    "reference_local_minute"
                ],
                "printed_local_date": historical_current_row[
                    "printed_local_date"
                ],
            },
            "historical_kasi_almanac": {
                "document_fact_evidence_class": "SOURCE_HARD_FACT",
                "provider_adjudication_evidence_class": "INSTITUTIONAL_ADVISORY",
                "printed_label": almanac_row["printed_label"],
                "normalized_reference_local_minute": almanac_row[
                    "normalized_reference_local_minute"
                ],
                "subminute_instant_claimed": False,
            },
            "labels_equal": historical_current_row["reference_local_minute"]
            == almanac_row["normalized_reference_local_minute"],
            "interpretation": "historic_printed_fact_and_current_calculation_are_distinct_vintages",
        },
        "solar_term_provider_comparison": comparison,
        "selected_provider_boundary_assignment_checks": selected_boundary,
        "baseline_internal_boundary_assignment_checks": baseline_boundary,
        "policy_comparison": policy,
        "synthetic_invariants": {
            key: value for key, value in invariants.items() if key != "sample_outputs"
        },
        "hmac_id_invariants": hmac_invariants,
        "host_invariance": host,
        "public_diagnostics": {
            "provider_records": {
                "rows": len(records),
                **artifact_hash(records_payload),
                "contains_birth_or_session_data": False,
            },
            "provider_delta_by_year_svg": artifact_hash(raw_svg_payload),
            "provider_time_scale_scatter_svg": artifact_hash(
                time_scale_svg_payload
            ),
        },
        "data_availability_checks": data_availability_checks,
        "provider_eligibility_checks": provider_eligibility_checks,
        "baseline_conformance_checks": baseline_checks,
        "data_availability_blocking_reasons": sorted(
            key for key, passed in data_availability_checks.items() if not passed
        ),
        "provider_eligibility_blocking_reasons": sorted(
            key for key, passed in provider_eligibility_checks.items() if not passed
        ),
        "baseline_conformance_blocking_reasons": sorted(
            key for key, passed in baseline_checks.items() if not passed
        ),
        "data_availability_gate_passed": data_availability_gate_passed,
        "provider_eligibility_gate_passed": provider_eligibility_gate_passed,
        "baseline_conformance_gate_passed": baseline_conformance_gate_passed,
        "technical_gate_passed": technical_gate_passed,
        "runtime_gate_passed": technical_gate_passed,
        "runtime_approved": False,
        "release_approval_performed": False,
        "release_registry_creation_allowed": False,
        "runtime_provider_changed": False,
        "runtime_feature_flag_default": False,
        "app_binding_performed": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "mix20k_v3_1_generated": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
    }
    if any(
        report[key] is not False
        for key in (
            "runtime_approved",
            "release_approval_performed",
            "release_registry_creation_allowed",
            "runtime_provider_changed",
            "app_binding_performed",
            "mix20k_v3_1_regeneration_allowed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
            "sealed_blind_accessed",
            "raw_restricted_samples_in_report",
        )
    ):
        raise RuntimeConformanceV6Error(
            "현재 R4~R5 범위를 넘는 상태 변경이 감지됐습니다."
        )
    directory = _write_artifacts(
        report,
        records_payload,
        raw_svg_payload,
        time_scale_svg_payload,
        output_base,
    )
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v6")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--kasi-lunar-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-openapi-solar-term-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-official-solar-term-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-minute-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-almanac-1964-snapshot", type=Path, required=True)
    parser.add_argument("--ephemeris", type=Path, required=True)
    parser.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            lunar_snapshot=args.kasi_lunar_snapshot,
            openapi_solar_term_snapshot=args.kasi_openapi_solar_term_snapshot,
            official_solar_term_snapshot=args.kasi_official_solar_term_snapshot,
            minute_snapshot=args.kasi_minute_snapshot,
            almanac_snapshot=args.kasi_almanac_1964_snapshot,
            ephemeris=args.ephemeris,
            output_base=args.output_base,
        )
    except (
        KasiAlmanac1964CollectorError,
        KasiCollectorV11Error,
        KasiOfficialSolarTermsCollectorError,
        KasiTermCoverageCollectorError,
        RuntimeCalculationError,
        RuntimeConformanceV3Error,
        RuntimeConformanceV5Error,
        RuntimeConformanceV6Error,
        SolarTermProviderComparisonError,
    ) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "data_availability_gate_passed": report[
                    "data_availability_gate_passed"
                ],
                "provider_eligibility_gate_passed": report[
                    "provider_eligibility_gate_passed"
                ],
                "technical_gate_passed": report["technical_gate_passed"],
                "runtime_approved": report["runtime_approved"],
                "release_approval_performed": report[
                    "release_approval_performed"
                ],
                "output": str(directory),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
