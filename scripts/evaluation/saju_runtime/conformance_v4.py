# conformance_v4.py - 공식 날짜·최근접 분·독립 천문·HMAC Gate를 fail-closed 집계한다.

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
    EXPECTED_JIE_ROWS,
    _boundary_checks,
    _load_lunar_snapshot,
    _load_minute_snapshot,
    _load_term_snapshot,
    _term_date_checks,
)
from scripts.evaluation.saju_runtime.jie_crosscheck import DE440S_SHA256
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import (
    CROSSCHECK_VERSION,
    KST_ROUNDING_POLICY,
    artifact_hash,
    compare_jie_boundaries_v1_2,
    display_minute_label,
    records_jsonl,
    render_delta_by_year_svg,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import (
    CONFORMANCE_V4_IMPLEMENTATIONS,
    ENGINE_VERSION_V12,
    GATE_V12_PATH,
    ID_CONTRACT_VERSION_V2,
    REGISTRY_V12_PATH,
    SUITE_VERSION_V4,
    derive_gate_checks_v1_2,
    runtime_source_versions_v1_2,
    validate_contract_registry_v1_2,
)
from scripts.runtime.calculation.engine_v1_2 import SajuRuntimeEngineV12
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner

REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.2.0"
TEST_SIGNER_KEY = bytes(range(32))


class RuntimeConformanceV4Error(RuntimeError):
    """conformance v4 입력·산출물 계약 위반."""


def _minute_checks_v1_2(
    rows: list[dict[str, Any]], independent_records: list[dict[str, Any]]
) -> dict[str, Any]:
    independent = {
        (row["year"], row["term_index"]): datetime.fromisoformat(
            row["skyfield_instant_utc"].replace("Z", "+00:00")
        )
        for row in independent_records
    }
    from scripts.runtime.calculation.solar_terms import solar_term_instant

    mismatch_rows: list[dict[str, Any]] = []
    runtime_signed: list[float] = []
    comparator_signed: list[float] = []
    runtime_mismatches = 0
    comparator_mismatches = 0
    comparator_missing = 0
    for row in rows:
        reference = datetime.fromisoformat(row["reference_local_minute"])
        runtime = solar_term_instant(row["year"], row["term_index"])
        comparator = independent.get((row["year"], row["term_index"]))
        runtime_label = display_minute_label(runtime)
        reference_label = reference.isoformat(timespec="minutes")
        runtime_delta = (runtime - reference).total_seconds()
        runtime_signed.append(runtime_delta)
        runtime_match = runtime_label == reference_label
        runtime_mismatches += not runtime_match
        comparator_label = None
        comparator_delta = None
        comparator_match = False
        if comparator is None:
            comparator_missing += 1
        else:
            comparator_label = display_minute_label(comparator)
            comparator_delta = (comparator - reference).total_seconds()
            comparator_signed.append(comparator_delta)
            comparator_match = comparator_label == reference_label
            comparator_mismatches += not comparator_match
        if not runtime_match or not comparator_match:
            mismatch_rows.append(
                {
                    "year": row["year"],
                    "term_index": row["term_index"],
                    "reference_display_minute": reference_label,
                    "runtime_display_minute": runtime_label,
                    "independent_display_minute": comparator_label,
                    "runtime_signed_delta_seconds": round(runtime_delta, 6),
                    "independent_signed_delta_seconds": (
                        None if comparator_delta is None else round(comparator_delta, 6)
                    ),
                    "runtime_label_match": runtime_match,
                    "independent_label_match": comparator_match,
                }
            )
    return {
        "rows": len(rows),
        "comparison_policy": KST_ROUNDING_POLICY,
        "comparison_policy_authority": "project_inference_not_official_kasi_rounding_rule",
        "reference_precision": "displayed_minute",
        "runtime_display_minute_mismatches": runtime_mismatches,
        "independent_display_minute_mismatches": comparator_mismatches,
        "independent_missing_rows": comparator_missing,
        "minimum_runtime_signed_delta_seconds": (
            None if not runtime_signed else round(min(runtime_signed), 6)
        ),
        "maximum_runtime_signed_delta_seconds": (
            None if not runtime_signed else round(max(runtime_signed), 6)
        ),
        "minimum_independent_signed_delta_seconds": (
            None if not comparator_signed else round(min(comparator_signed), 6)
        ),
        "maximum_independent_signed_delta_seconds": (
            None if not comparator_signed else round(max(comparator_signed), 6)
        ),
        "signed_delta_role": "diagnostic_only_not_gate_threshold",
        "mismatches": mismatch_rows[:100],
        "mismatch_details_truncated": len(mismatch_rows) > 100,
    }


def _adjudicate_engine_local_dates(
    independent_records: list[dict[str, Any]], official_term_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    official = {
        (row["year"], row["term_index"]): row["local_date"]
        for row in official_term_rows
    }
    disagreements = [
        row
        for row in independent_records
        if row["astronomy_local_date"] != row["skyfield_local_date"]
    ]
    rows: list[dict[str, Any]] = []
    unresolved = 0
    runtime_mismatches = 0
    comparator_mismatches = 0
    for row in disagreements:
        official_date = official.get((row["year"], row["term_index"]))
        if official_date is None:
            unresolved += 1
            resolution = "unresolved_missing_official_kasi_row"
        else:
            runtime_match = row["astronomy_local_date"] == official_date
            comparator_match = row["skyfield_local_date"] == official_date
            runtime_mismatches += not runtime_match
            comparator_mismatches += not comparator_match
            resolution = (
                "resolved_runtime_matches_official_comparator_differs"
                if runtime_match and not comparator_match
                else "resolved_comparator_matches_official_runtime_differs"
                if comparator_match and not runtime_match
                else "resolved_neither_engine_matches_official"
            )
        rows.append(
            {
                "year": row["year"],
                "term_index": row["term_index"],
                "term_name": row["term_name"],
                "runtime_local_date": row["astronomy_local_date"],
                "independent_local_date": row["skyfield_local_date"],
                "official_kasi_local_date": official_date,
                "resolution": resolution,
            }
        )
    crosscheck_complete = len(independent_records) == EXPECTED_JIE_ROWS
    if not crosscheck_complete:
        status = "missing_independent_crosscheck"
    elif unresolved:
        status = "blocked_unresolved_official_adjudication"
    elif runtime_mismatches:
        status = "blocked_runtime_official_date_mismatch"
    else:
        status = "resolved"
    return {
        "status": status,
        "crosscheck_rows_complete": crosscheck_complete,
        "comparison_scope": "engine_disagreement_rows_only",
        "official_adjudicator": "kasi_24_divisions_openapi",
        "engine_local_date_disagreements": len(disagreements),
        "official_rows_available_for_disagreements": len(disagreements) - unresolved,
        "unresolved_disagreements": unresolved,
        "runtime_official_mismatches_on_disagreements": runtime_mismatches,
        "independent_official_mismatches_on_disagreements": comparator_mismatches,
        "rows": rows,
    }


def _missing_independent() -> dict[str, Any]:
    return {
        "schema_version": "1.2.0",
        "crosscheck_version": CROSSCHECK_VERSION,
        "status": "missing_ephemeris",
        "rows": 0,
        "expected_rows": EXPECTED_JIE_ROWS,
        "threshold_failures": None,
        "term_identity_failures": None,
        "chronological_order_failures": None,
        "local_date_comparison_scope": "not_performed",
        "official_local_date_adjudication_performed": False,
        "engine_local_date_disagreements": None,
        "delta_t_diagnostic": {"status": "missing_ephemeris", "gate_role": "diagnostic_only"},
        "fixed_regression_guard": {
            "maximum_delta_seconds": 120.0,
            "status": "not_run",
            "role": "non_authoritative_fixed_regression_guard_not_physical_accuracy_budget",
        },
        "ephemeris": {
            "sha256": DE440S_SHA256,
            "provided": False,
            "local_path_recorded": False,
        },
        "records": [],
    }


def _hmac_invariants(signer: RuntimeIdSigner) -> dict[str, Any]:
    mismatch = 0
    prefix_failures = 0
    domain_collisions = 0
    key_separation_failures = 0
    second = RuntimeIdSigner.for_test(bytes(reversed(range(32))))
    kinds = {
        "birth_input_id": "sbi2_",
        "chart_id": "sc2_",
        "chart_set_id": "scs2_",
        "calculation_run_id": "scr2_",
        "chart_input_fingerprint": "sif2_",
    }
    for index in range(200):
        payload = {
            "vector": index,
            "profile_id": POLICY_ID,
            "source": "non_pii_conformance_vector",
        }
        values = {kind: signer.sign(kind, payload) for kind in kinds}
        mismatch += any(
            value != signer.sign(kind, dict(payload)) for kind, value in values.items()
        )
        prefix_failures += any(
            not values[kind].startswith(prefix) for kind, prefix in kinds.items()
        )
        domain_collisions += len(set(values.values())) != len(values)
        key_separation_failures += any(
            value == second.sign(kind, payload) for kind, value in values.items()
        )
    return {
        "vectors": 200,
        "id_kinds_per_vector": len(kinds),
        "reproducibility_mismatches": mismatch,
        "prefix_failures": prefix_failures,
        "domain_collisions": domain_collisions,
        "key_separation_failures": key_separation_failures,
        "signer_kind": "fixed_non_production_injected",
        "key_value_recorded": False,
        "production_signer_accepted": False,
    }


def _internal_boundary_checks() -> dict[str, Any]:
    value = _boundary_checks()
    return {
        **value,
        "scope": "internal_assignment_around_runtime_generated_boundary_instants",
        "validates_assignment_logic": True,
        "validates_boundary_instant_accuracy": False,
        "reporting_label": "internal_profile_boundary_assignment_checks",
    }


def _empty_svg() -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="120">'
        '<rect width="100%" height="100%" fill="#fffdf8"/>'
        '<text x="20" y="65" font-family="sans-serif" fill="#665f55">'
        "독립 ephemeris가 없어 절입 산점도를 생성하지 않았습니다.</text></svg>\n"
    ).encode()


def _safe_output_base(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved != REPORT_ROOT.resolve(strict=False) or path.is_symlink():
        raise RuntimeConformanceV4Error(
            "conformance v4 output base는 고정 v1.2.0 경로여야 합니다."
        )
    return resolved


def _write_artifacts(
    report: dict[str, Any],
    records_payload: bytes,
    svg_payload: bytes,
    output_base: Path,
) -> Path:
    build_id = "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate_payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    artifacts = {
        "aggregate.json": artifact_hash(aggregate_payload),
        "independent_records.jsonl": artifact_hash(records_payload),
        "delta_by_year.svg": artifact_hash(svg_payload),
    }
    manifest = {
        "schema_version": "1.2.0",
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v4",
        "artifacts": artifacts,
        "runtime_gate_passed": report["runtime_gate_passed"],
        "release_registry_creation_allowed": report["runtime_gate_passed"],
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    payloads = {
        "aggregate.json": aggregate_payload,
        "independent_records.jsonl": records_payload,
        "delta_by_year.svg": svg_payload,
        "build_manifest.json": manifest_payload,
    }
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeConformanceV4Error("기존 conformance v4 build가 일반 디렉터리가 아닙니다.")
        for filename, payload in payloads.items():
            path = directory / filename
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise RuntimeConformanceV4Error(
                    f"같은 build ID의 conformance v4 artifact가 다릅니다: {filename}"
                )
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        for filename, payload in payloads.items():
            with (directory / filename).open("xb") as stream:
                stream.write(payload)
    except OSError as exc:
        raise RuntimeConformanceV4Error(
            "conformance v4 build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    lunar_snapshot: Path | None = None,
    solar_term_snapshot: Path | None = None,
    minute_snapshot: Path | None = None,
    ephemeris: Path | None = None,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_2()
    source_versions = runtime_source_versions_v1_2(
        require_runtime_dependencies=True,
        require_validator_dependencies=ephemeris is not None,
    )
    signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    provider = KoreanLunarCalendarProvider()
    engine = SajuRuntimeEngineV12(
        signer=signer,
        enable_candidate_runtime=True,
        calendar_provider=provider,
    )
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    term_rows, term_identity = _load_term_snapshot(solar_term_snapshot)
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    lunar = _kasi_checks(provider, lunar_rows)
    term_dates = _term_date_checks(term_rows)
    independent = (
        _missing_independent()
        if ephemeris is None
        else compare_jie_boundaries_v1_2(ephemeris, include_records=True)
    )
    independent_records = independent.pop("records", [])
    independent["records_in_report"] = False
    adjudication = _adjudicate_engine_local_dates(independent_records, term_rows)
    minute = _minute_checks_v1_2(minute_rows, independent_records)
    boundary = _internal_boundary_checks()
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    hmac_invariants = _hmac_invariants(signer)
    gate = json.loads(GATE_V12_PATH.read_text(encoding="utf-8"))
    minimum = gate["minimum_cases"]
    lunar_complete = lunar["rows"] == minimum["kasi_lunar_days"]
    terms_complete = (
        term_dates["all_term_rows_collected"]
        == minimum["kasi_all_solar_term_dates_collected"]
        and term_dates["jie_rows_compared"] == minimum["kasi_jie_dates_compared"]
    )
    minute_complete = minute["rows"] == minimum["kasi_jie_minute_references"]
    independent_complete = independent["rows"] == minimum["independent_jie_instants"]
    checks = {
        "kasi_lunar_days_complete": lunar_complete,
        "kasi_lunar_conversion_mismatch_zero": lunar_complete
        and lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar_complete
        and lunar["day_ganzhi_mismatches"] == 0,
        "kasi_all_solar_term_dates_complete": terms_complete,
        "runtime_kasi_jie_date_mismatch_zero": terms_complete
        and term_dates["runtime_date_mismatches"] == 0,
        "engine_local_date_adjudication_evidence_complete": independent_complete
        and adjudication["unresolved_disagreements"] == 0,
        "runtime_matches_official_on_engine_date_disagreements": independent_complete
        and adjudication["unresolved_disagreements"] == 0
        and adjudication["runtime_official_mismatches_on_disagreements"] == 0,
        "kasi_jie_minute_references_complete": minute_complete,
        "runtime_kasi_nearest_minute_label_match": minute_complete
        and minute["runtime_display_minute_mismatches"] == 0,
        "independent_kasi_nearest_minute_label_match": minute_complete
        and independent_complete
        and minute["independent_missing_rows"] == 0
        and minute["independent_display_minute_mismatches"] == 0,
        "independent_jie_instants_complete": independent_complete,
        "independent_jie_fixed_120_second_regression_guard": independent_complete
        and independent["threshold_failures"] == 0,
        "independent_term_identity_zero": independent_complete
        and independent["term_identity_failures"] == 0,
        "independent_chronological_order_zero": independent_complete
        and independent["chronological_order_failures"] == 0,
        "delta_t_diagnostic_complete": independent_complete
        and independent.get("delta_t_diagnostic", {}).get("status")
        in {"delta_t_not_primary", "delta_t_may_be_primary"},
        "internal_profile_boundary_cases_complete": boundary["cases"]
        == minimum["internal_profile_boundary_assignment"],
        "internal_profile_boundary_mismatch_zero": boundary["mismatch_rows"] == 0,
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
        "source_version_id_failure_zero": invariants["source_version_id_failures"] == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
        "unclassified_mismatch_zero": all(
            item.get("category")
            for item in [
                *term_dates["mismatches"],
                *boundary["mismatches"],
                *policy["mismatches"],
            ]
        ),
    }
    blocking_reasons = sorted(key for key, value in checks.items() if not value)
    gate_passed = not blocking_reasons
    records_payload = records_jsonl(independent_records)
    svg_payload = (
        render_delta_by_year_svg(independent_records)
        if independent_records
        else _empty_svg()
    )
    if gate_passed:
        status = "passed"
    elif not (lunar_identity["complete"] and term_identity["complete"] and minute_identity["complete"]):
        status = "blocked_missing_official_and_conformance_failures"
    elif not independent_complete:
        status = "blocked_missing_independent_ephemeris"
    else:
        status = "blocked_conformance_failures"
    report = {
        "schema_version": "1.2.0",
        "suite_version": SUITE_VERSION_V4,
        "profile_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "status": status,
        "source_versions": source_versions,
        "inputs": {
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_24_divisions": term_identity,
                "kasi_minute_reference": minute_identity,
            },
            "public_fallback_fixture": {
                "path": str(KASI_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(KASI_FIXTURE),
            },
            "policy_fixture": {
                "path": str(POLICY_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(POLICY_FIXTURE),
            },
            "runtime_registry_sha256": sha256_file(REGISTRY_V12_PATH),
            "gate_sha256": sha256_file(GATE_V12_PATH),
            "implementation_sha256": {
                relative: sha256_file(REPO_ROOT / relative)
                for relative in sorted(CONFORMANCE_V4_IMPLEMENTATIONS)
            },
            "test_signer": {
                "kind": "fixed_non_production_injected",
                "key_value_recorded": False,
                "accepted_for_production": False,
            },
        },
        "official_kasi_lunisolar": lunar,
        "official_kasi_solar_term_dates": term_dates,
        "institutional_kasi_minute_reference": minute,
        "independent_jie_crosscheck": independent,
        "official_local_date_adjudication": adjudication,
        "internal_profile_boundary_assignment_checks": boundary,
        "policy_comparison": policy,
        "synthetic_invariants": {
            key: value for key, value in invariants.items() if key != "sample_outputs"
        },
        "hmac_id_invariants": hmac_invariants,
        "host_invariance": host,
        "public_diagnostics": {
            "independent_records": {
                "rows": len(independent_records),
                **artifact_hash(records_payload),
                "contains_birth_or_session_data": False,
            },
            "delta_by_year_svg": artifact_hash(svg_payload),
        },
        "gate_checks": checks,
        "blocking_reasons": blocking_reasons,
        "runtime_gate_passed": gate_passed,
        "release_registry_creation_allowed": gate_passed,
        "runtime_feature_flag_default": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
    }
    if derive_gate_checks_v1_2(report) != checks:
        raise RuntimeConformanceV4Error(
            "conformance v4 Gate bool이 release 재계산 규칙과 다릅니다."
        )
    directory = _write_artifacts(report, records_payload, svg_payload, output_base)
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v4")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--kasi-lunar-snapshot", type=Path)
    parser.add_argument("--kasi-solar-term-snapshot", type=Path)
    parser.add_argument("--kasi-minute-snapshot", type=Path)
    parser.add_argument("--ephemeris", type=Path)
    parser.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            lunar_snapshot=args.kasi_lunar_snapshot,
            solar_term_snapshot=args.kasi_solar_term_snapshot,
            minute_snapshot=args.kasi_minute_snapshot,
            ephemeris=args.ephemeris,
            output_base=args.output_base,
        )
    except (RuntimeConformanceV4Error, RuntimeCalculationError) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "runtime_gate_passed": report["runtime_gate_passed"],
                "release_registry_creation_allowed": report[
                    "release_registry_creation_allowed"
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
