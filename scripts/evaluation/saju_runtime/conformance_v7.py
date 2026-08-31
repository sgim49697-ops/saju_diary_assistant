# conformance_v7.py - Skyfield/UT1 선호 후보와 엄격 runtime Gate를 분리해 집계한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
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
from scripts.evaluation.saju_runtime.conformance_v6 import (
    EXPECTED_PROFILE_DETERMINISTIC_ROWS,
    OFFICIAL_TERM_MANIFEST_FILENAME,
    RuntimeConformanceV6Error,
    _crosscheck_kasi_sources,
    _load_official_terms,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    COLLECTOR_VERSION as IERS_COLLECTOR_VERSION,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    DOWNLOAD_ENDPOINT as IERS_DOWNLOAD_ENDPOINT,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    MANIFEST_FILENAME as IERS_MANIFEST_FILENAME,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    MAX_DOWNLOAD_BYTES as IERS_MAX_DOWNLOAD_BYTES,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    RESPONSE_FILENAME as IERS_RESPONSE_FILENAME,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    IersFinalsCollectorError,
)
from scripts.evaluation.saju_runtime.iers_finals_collector import (
    parse_snapshot as parse_iers_snapshot,
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
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    KasiTermCoverageCollectorError,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v2 import (
    EXPECTED_OFFICIAL_JIE_ROWS,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v3 import (
    COMPARISON_VERSION,
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

SCHEMA_VERSION = "1.5.0"
SUITE_VERSION = "saju-runtime-conformance-v7.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.5.0"
GATE_PATH = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.5.0.json"
SOURCE_REGISTRY_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.5.0.json"
)
GATE_SHA256 = "132408a4085e4eeb52dbe613c501f3c15d101cd794b85b360638f2f03ac8c8b7"
SOURCE_REGISTRY_SHA256 = (
    "d549780199898ca04a1f7c1b71204825a79a657c6cf8c691827b097ac5580b46"
)
GATE_PARENT_PATH = (
    REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.4.0.json"
)
GATE_PARENT_SHA256 = "7353131db05abc3c66783e88bc8df100852048b32b69cf6681c6d623fcd8e154"
SOURCE_REGISTRY_PARENT_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.4.0.json"
)
SOURCE_REGISTRY_PARENT_SHA256 = (
    "83d2c6e168d1e45166622ff9462a130fd25594395ce8102de384892652f401e9"
)
IERS_COLLECTOR_PATH = (
    REPO_ROOT / "scripts/evaluation/saju_runtime/iers_finals_collector.py"
)
IERS_COLLECTOR_SHA256 = (
    "0c72388a84f03356ff62ccd49eb41399780eb62a5510d8555ddc8e5696264b05"
)
IERS_SNAPSHOT_SHA256 = (
    "e3905ff7a74b791744704aa3e900a2161e96db97a30095d8fc442b04e4cfe058"
)
IMPLEMENTATION_PATHS = frozenset(
    {
        "configs/runtime/calculation/conformance_gate-v1.5.0.json",
        "configs/runtime/calculation/source_registry-v1.5.0.json",
        "scripts/evaluation/saju_runtime/conformance_v5.py",
        "scripts/evaluation/saju_runtime/conformance_v6.py",
        "scripts/evaluation/saju_runtime/conformance_v7.py",
        "scripts/evaluation/saju_runtime/iers_finals_collector.py",
        "scripts/evaluation/saju_runtime/kasi_almanac_1964_collector.py",
        "scripts/evaluation/saju_runtime/kasi_official_solar_terms_collector.py",
        "scripts/evaluation/saju_runtime/kasi_term_coverage_collector_v1_2.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v1.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v2.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v3.py",
        "scripts/evaluation/saju_runtime/strict_json.py",
        "scripts/runtime/calculation/engine_v1_2.py",
        "scripts/runtime/calculation/solar_terms.py",
    }
)


class RuntimeConformanceV7Error(RuntimeError):
    """conformance v7 입력·후보/엄격 Gate·산출물 계약 위반."""


def _validate_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    gate = _strict_json(GATE_PATH)
    registry = _strict_json(SOURCE_REGISTRY_PATH)
    if (
        sha256_file(GATE_PATH) != GATE_SHA256
        or sha256_file(SOURCE_REGISTRY_PATH) != SOURCE_REGISTRY_SHA256
        or sha256_file(GATE_PARENT_PATH) != GATE_PARENT_SHA256
        or sha256_file(SOURCE_REGISTRY_PARENT_PATH) != SOURCE_REGISTRY_PARENT_SHA256
        or gate.get("parent")
        != {
            "path": "configs/runtime/calculation/conformance_gate-v1.4.0.json",
            "sha256": GATE_PARENT_SHA256,
        }
        or registry.get("parent")
        != {
            "path": "configs/runtime/calculation/source_registry-v1.4.0.json",
            "sha256": SOURCE_REGISTRY_PARENT_SHA256,
        }
    ):
        raise RuntimeConformanceV7Error("v1.5.0 설정 hash chain이 다릅니다.")
    promotion = gate.get("promotion_effects")
    candidate_gate = gate.get("provider_candidate_gate")
    strict_gate = gate.get("strict_runtime_provider_gate")
    iers = registry.get("time_scale_sources", {}).get("iers_finals2000a_2026_09_01", {})
    skyfield = registry.get("provider_candidates", {}).get(
        "skyfield_de440s_builtin_ut1", {}
    )
    if (
        gate.get("schema_version") != SCHEMA_VERSION
        or gate.get("suite_version") != SUITE_VERSION
        or gate.get("profile_id") != POLICY_ID
        or not isinstance(candidate_gate, dict)
        or candidate_gate.get("preferred_candidate") != "skyfield_de440s_builtin_ut1"
        or candidate_gate.get("candidate_selection_changes_runtime") is not False
        or not isinstance(strict_gate, dict)
        or strict_gate.get("available_official_current_minute_label_mismatch_zero")
        is not True
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
        or registry.get("registry_version") != "saju-runtime-sources-v1.5.0"
        or iers.get("collector_version") != IERS_COLLECTOR_VERSION
        or iers.get("collector_sha256") != IERS_COLLECTOR_SHA256
        or iers.get("snapshot_sha256") != IERS_SNAPSHOT_SHA256
        or iers.get("automatic_fallback_used") is not False
        or skyfield.get("preferred_candidate") is not True
        or skyfield.get("strict_runtime_approved") is not False
        or skyfield.get("runtime_bound") is not False
        or registry.get("coverage_policy", {}).get(
            "missing_official_evidence_may_be_filled_by_generated_values"
        )
        is not False
        or registry.get("interpretation", {}).get(
            "preferred_candidate_selection_is_runtime_approval"
        )
        is not False
    ):
        raise RuntimeConformanceV7Error(
            "v1.5.0 후보 선택·엄격 Gate 또는 source registry가 다릅니다."
        )
    return gate, registry


def _load_iers_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if path.name != IERS_RESPONSE_FILENAME:
        raise RuntimeConformanceV7Error("IERS snapshot 파일명이 다릅니다.")
    manifest_path = path.with_name(IERS_MANIFEST_FILENAME)
    payload = _regular_bytes(
        path,
        maximum=IERS_MAX_DOWNLOAD_BYTES,
        private=True,
        description="IERS finals2000A snapshot",
    )
    manifest = _private_json(manifest_path)
    parsed = parse_iers_snapshot(payload)
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "complete"
        or manifest.get("collector_version") != IERS_COLLECTOR_VERSION
        or manifest.get("collector_sha256") != IERS_COLLECTOR_SHA256
        or sha256_file(IERS_COLLECTOR_PATH) != IERS_COLLECTOR_SHA256
        or manifest.get("download_endpoint") != IERS_DOWNLOAD_ENDPOINT
        or manifest.get("artifact")
        != {
            "filename": IERS_RESPONSE_FILENAME,
            **artifact_hash(payload),
        }
        or manifest.get("parsed") != parsed
        or manifest.get("credential_used") is not False
        or manifest.get("automatic_fallback_used") is not False
        or not _is_utc_timestamp(manifest.get("collected_at"))
        or sha256_file(path) != IERS_SNAPSHOT_SHA256
    ):
        raise RuntimeConformanceV7Error("IERS snapshot provenance가 다릅니다.")
    return manifest, {
        "kind": "private_official_iers_finals2000a_diagnostic",
        "provided": True,
        "rows": parsed["rows"],
        "utc_date_range": [parsed["utc_date_start"], parsed["utc_date_end"]],
        "snapshot_sha256": sha256_file(path),
        "manifest_sha256": sha256_file(manifest_path),
        "origin_collector_version": IERS_COLLECTOR_VERSION,
        "origin_collector_sha256": IERS_COLLECTOR_SHA256,
        "automatic_fallback_used": False,
    }


def _safe_output_base(path: Path) -> Path:
    if path.is_symlink() or path.resolve(strict=False) != REPORT_ROOT.resolve(
        strict=False
    ):
        raise RuntimeConformanceV7Error(
            "conformance v7 output base는 고정 v1.5.0 경로여야 합니다."
        )
    return path.resolve(strict=False)


def _conformance_status(
    *,
    data_availability_gate_passed: bool,
    provider_candidate_gate_passed: bool,
    strict_runtime_provider_gate_passed: bool,
    baseline_conformance_gate_passed: bool,
) -> str:
    if (
        data_availability_gate_passed
        and provider_candidate_gate_passed
        and strict_runtime_provider_gate_passed
        and baseline_conformance_gate_passed
    ):
        return "technical_gate_passed_release_not_performed"
    if not data_availability_gate_passed:
        return "blocked_incomplete_available_official_data"
    if not baseline_conformance_gate_passed:
        return "blocked_baseline_conformance_failures"
    if provider_candidate_gate_passed and not strict_runtime_provider_gate_passed:
        return "preferred_candidate_selected_strict_runtime_gate_blocked"
    return "blocked_provider_candidate_failures"


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
        "report_type": "saju_runtime_conformance_v7",
        "artifacts": artifact_identities,
        "data_availability_gate_passed": report["data_availability_gate_passed"],
        "provider_candidate_gate_passed": report["provider_candidate_gate_passed"],
        "strict_runtime_provider_gate_passed": report[
            "strict_runtime_provider_gate_passed"
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
            raise RuntimeConformanceV7Error("기존 v7 build가 일반 디렉터리가 아닙니다.")
        for filename, payload in payloads.items():
            artifact = directory / filename
            if (
                artifact.is_symlink()
                or not artifact.is_file()
                or artifact.read_bytes() != payload
            ):
                raise RuntimeConformanceV7Error(
                    f"같은 v7 build ID의 artifact가 다릅니다: {filename}"
                )
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        for filename, payload in payloads.items():
            with (directory / filename).open("xb") as stream:
                stream.write(payload)
    except OSError as exc:
        raise RuntimeConformanceV7Error(
            "v7 build를 배타적으로 기록하지 못했습니다."
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
    gate, source_registry = _validate_configs()
    validate_contract_registry_v1_2()
    source_versions = runtime_source_versions_v1_2(
        require_runtime_dependencies=True,
        require_validator_dependencies=True,
    )
    source_versions["conformance_source_registry"] = source_registry["registry_version"]
    _private_jsonl(lunar_snapshot)
    _private_json(lunar_snapshot.with_name("kasi_lunisolar_manifest.json"))
    _private_jsonl(minute_snapshot)
    _private_json(minute_snapshot.with_name("kasi_minute_references_manifest.json"))
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    openapi_rows, openapi_identity = _load_term_coverage(openapi_solar_term_snapshot)
    official_rows, official_identity = _load_official_terms(
        official_solar_term_snapshot
    )
    official_manifest = _private_json(
        official_solar_term_snapshot.with_name(OFFICIAL_TERM_MANIFEST_FILENAME)
    )
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    almanac_row, almanac_identity = _load_almanac(almanac_snapshot)
    _, iers_identity = _load_iers_snapshot(iers_snapshot)
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
        iers_snapshot=iers_snapshot,
        official_current_rows=official_rows,
        official_collected_at=official_manifest["collected_at"],
        advisory_minute_rows=minute_rows,
        historical_almanac_row=almanac_row,
        include_records=True,
    )
    records = comparison.pop("records")
    comparison["records_in_report"] = False
    preferred_evidence = comparison["provider_candidate_evidence"][
        "skyfield_de440s_builtin_ut1"
    ]
    selection = comparison["selection"]
    preferred_boundary = comparison["preferred_candidate_boundary_assignment_checks"]
    baseline_boundary = _internal_boundary_checks()
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    hmac_invariants = _hmac_invariants(signer)
    minimum = gate["minimum_cases"]
    data_availability_checks = {
        "kasi_lunar_snapshot_complete": lunar["rows"] == minimum["kasi_lunar_days"],
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
        == minimum["kasi_official_download_known_non_jie_omissions"],
        "official_download_jie_coverage_complete": official_identity["jie_rows"]
        == minimum["kasi_official_download_jie_rows"]
        and official_identity["jie_coverage_complete"],
        "openapi_and_official_download_crosscheck_completed": source_crosscheck[
            "official_download_missing_rows"
        ]
        == 0
        and source_crosscheck["openapi_rows"] == minimum["kasi_term_api_rows_observed"],
        "institutional_advisory_rows_collected": len(minute_rows)
        == minimum["institutional_advisory_minute_rows"],
        "historical_1964_document_collected": almanac_identity["complete"]
        and minimum["historical_1964_baengno_rows"] == 1,
        "iers_diagnostic_snapshot_collected": iers_identity["rows"]
        == minimum["iers_finals_rows"],
        "official_snapshot_values_not_provider_filled": official_identity[
            "provider_values_used"
        ]
        is False
        and comparison["official_source_values_filled_from_provider"] is False,
        "target_without_official_rows_classified_profile_deterministic": comparison[
            "target_without_official_rows"
        ]["jie_rows"]
        == minimum["profile_deterministic_jie_rows"],
    }
    provider_candidate_checks = {
        "preferred_candidate_selected": selection["preferred_candidate"]
        == "skyfield_de440s_builtin_ut1",
        "preferred_candidate_evidence_passed": preferred_evidence["candidate_eligible"],
        "official_past_future_partition_complete": preferred_evidence[
            "past_rows_at_snapshot_collection"
        ]
        == minimum["kasi_official_past_rows_at_collection"]
        and preferred_evidence["future_forecast_rows_at_snapshot_collection"]
        == minimum["kasi_official_future_rows_at_collection"],
        "root_solver_convergence_not_blocking": comparison["root_solver_diagnostic"][
            "status"
        ]
        == "root_convergence_not_explanation_for_multi_second_delta",
        "preferred_candidate_boundary_cases_complete": preferred_boundary["cases"]
        == minimum["preferred_candidate_boundary_assignment"],
        "preferred_candidate_boundary_mismatch_zero": preferred_boundary[
            "mismatch_rows"
        ]
        == 0,
        "candidate_selection_did_not_change_runtime": selection[
            "runtime_provider_changed"
        ]
        is False,
    }
    strict_runtime_provider_checks = {
        "preferred_candidate_raw_minute_label_mismatch_zero": preferred_evidence[
            "official_current_minute_label_mismatches"
        ]
        == 0,
        "future_physical_instant_adjudicated": False,
        "strict_eligible_provider_selected": selection["strict_eligible_provider"]
        is not None,
    }
    baseline_checks = {
        "kasi_lunar_conversion_mismatch_zero": lunar["rows"]
        == minimum["kasi_lunar_days"]
        and lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar["rows"] == minimum["kasi_lunar_days"]
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
        "time_scale_diagnostic_completed": comparison["time_scale_diagnostic_v2"][
            "status"
        ]
        == "not_delta_t_only",
        "baseline_internal_boundary_cases_complete": baseline_boundary["cases"]
        == minimum["preferred_candidate_boundary_assignment"],
        "baseline_internal_boundary_mismatch_zero": baseline_boundary["mismatch_rows"]
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
        "unsupported_foreign_failure_zero": invariants["unsupported_foreign_failures"]
        == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "source_version_id_failure_zero": invariants["source_version_id_failures"] == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
    }
    data_availability_gate_passed = all(data_availability_checks.values())
    provider_candidate_gate_passed = all(provider_candidate_checks.values())
    strict_runtime_provider_gate_passed = all(strict_runtime_provider_checks.values())
    baseline_conformance_gate_passed = all(baseline_checks.values())
    technical_gate_passed = (
        data_availability_gate_passed
        and provider_candidate_gate_passed
        and strict_runtime_provider_gate_passed
        and baseline_conformance_gate_passed
    )
    status = _conformance_status(
        data_availability_gate_passed=data_availability_gate_passed,
        provider_candidate_gate_passed=provider_candidate_gate_passed,
        strict_runtime_provider_gate_passed=strict_runtime_provider_gate_passed,
        baseline_conformance_gate_passed=baseline_conformance_gate_passed,
    )
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
                "iers_finals2000a_diagnostic": iers_identity,
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
            "openapi_rows": openapi_identity["rows"],
            "official_download_source_range": official_identity["source_range"],
            "official_download_rows": official_identity["source_rows"],
            "official_download_jie_rows": official_identity["jie_rows"],
            "official_runtime_range_jie_rows": EXPECTED_OFFICIAL_JIE_ROWS,
            "official_past_rows_at_collection": preferred_evidence[
                "past_rows_at_snapshot_collection"
            ],
            "official_future_rows_at_collection": preferred_evidence[
                "future_forecast_rows_at_snapshot_collection"
            ],
            "profile_deterministic_jie_rows": EXPECTED_PROFILE_DETERMINISTIC_ROWS,
            "official_snapshot_values_filled_from_provider": False,
        },
        "baengno_1964_evidence": comparison["baengno_1964_interpretation"],
        "solar_term_provider_comparison": comparison,
        "preferred_candidate_boundary_assignment_checks": preferred_boundary,
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
            "provider_time_scale_scatter_svg": artifact_hash(time_scale_svg_payload),
        },
        "data_availability_checks": data_availability_checks,
        "provider_candidate_checks": provider_candidate_checks,
        "strict_runtime_provider_checks": strict_runtime_provider_checks,
        "baseline_conformance_checks": baseline_checks,
        "data_availability_blocking_reasons": sorted(
            key for key, passed in data_availability_checks.items() if not passed
        ),
        "provider_candidate_blocking_reasons": sorted(
            key for key, passed in provider_candidate_checks.items() if not passed
        ),
        "strict_runtime_provider_blocking_reasons": sorted(
            key for key, passed in strict_runtime_provider_checks.items() if not passed
        ),
        "baseline_conformance_blocking_reasons": sorted(
            key for key, passed in baseline_checks.items() if not passed
        ),
        "data_availability_gate_passed": data_availability_gate_passed,
        "provider_candidate_gate_passed": provider_candidate_gate_passed,
        "strict_runtime_provider_gate_passed": strict_runtime_provider_gate_passed,
        "provider_eligibility_gate_passed": strict_runtime_provider_gate_passed,
        "baseline_conformance_gate_passed": baseline_conformance_gate_passed,
        "technical_gate_passed": technical_gate_passed,
        "runtime_gate_passed": technical_gate_passed,
        "preferred_provider_candidate": selection["preferred_candidate"],
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
        raise RuntimeConformanceV7Error(
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
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v7")
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
        IersFinalsCollectorError,
        KasiAlmanac1964CollectorError,
        KasiCollectorV11Error,
        KasiTermCoverageCollectorError,
        RuntimeCalculationError,
        RuntimeConformanceV3Error,
        RuntimeConformanceV5Error,
        RuntimeConformanceV6Error,
        RuntimeConformanceV7Error,
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
                "preferred_provider_candidate": report["preferred_provider_candidate"],
                "provider_candidate_gate_passed": report[
                    "provider_candidate_gate_passed"
                ],
                "strict_runtime_provider_gate_passed": report[
                    "strict_runtime_provider_gate_passed"
                ],
                "runtime_gate_passed": report["runtime_gate_passed"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
