# historical_candidate_gate.py - 실제 Skyfield 후보·FSM·공개 응답을 12개 층화 120건으로 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.engine_v1_3 import SajuRuntimeEngineV13
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    DE440S_SHA256,
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
)
from scripts.runtime.calculation.solar_term_types import (
    PAST_OFFICIAL_CORROBORATED,
)
from scripts.runtime.intake_contracts_v1_1 import load_strict_json_object
from scripts.runtime.intake_contracts_v1_2 import (
    EXPECTED_STRATA,
    INTAKE_REGISTRY,
    validate_intake_registry_v1_2,
)
from scripts.runtime.intake_fsm import IntakeFsmError
from scripts.runtime.intake_fsm_v1_2 import (
    CANDIDATE_RUNTIME_STATUS_FIELDS,
    CANDIDATE_SCOPE,
    advance_intake,
    assert_public_event,
    empty_intake_state,
)
from scripts.training.historical_candidate_dashboard import (
    CANDIDATE_IDLE_SECONDS,
    CANDIDATE_MAX_SESSIONS,
    CandidateSessionStore,
    _public_transition,
)

GATE_VERSION = "saju-historical-candidate-gate-v1.0.0"
CONFIG_PATH = REPO_ROOT / "configs/runtime/historical_candidate_dashboard-v1.0.0.json"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_historical_candidate/v1.0.0"
TEST_SIGNER_KEY = bytes(range(32))
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
READY = {field: True for field in CANDIDATE_RUNTIME_STATUS_FIELDS}
PAST_YEARS = (1921, 1930, 1940, 1950, 1964, 1970, 1980, 1990, 2000, 2010)
PROFILE_YEARS = tuple(range(1900, 1920, 2))
PUBLIC_FORBIDDEN_KEYS = (
    "normalized_input",
    "birth_input_id",
    "chart_id",
    "chart_set_id",
    "calculation_run_id",
    "internal_trace",
    "local_birth_time",
    "local_birth_date",
)
FALSE_GOVERNANCE = {
    "production_application_binding",
    "runtime_release_approved",
    "context_window_changed",
    "mix20k_v3_1_generated",
    "additional_training_performed",
    "model_promotion_performed",
    "sealed_blind_accessed",
}


class HistoricalCandidateGateError(RuntimeError):
    """후보 통합 Gate의 계약·실행·산출물 위반."""


def _safe_repo_path(relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise HistoricalCandidateGateError(f"{label} 경로가 안전하지 않습니다.")
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise HistoricalCandidateGateError(f"{label} 경로에 symlink가 있습니다.")
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise HistoricalCandidateGateError(f"{label} 경로가 저장소를 벗어납니다.") from exc
    return resolved


def _validate_hashed_paths(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise HistoricalCandidateGateError(f"{label} 목록이 비었습니다.")
    result: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise HistoricalCandidateGateError(f"{label} identity가 다릅니다.")
        relative = item["path"]
        expected = item["sha256"]
        if (
            not isinstance(relative, str)
            or relative in result
            or not isinstance(expected, str)
            or FULL_SHA.fullmatch(expected) is None
        ):
            raise HistoricalCandidateGateError(f"{label} hash 계약이 다릅니다.")
        path = _safe_repo_path(relative, label)
        if not path.is_file() or sha256_file(path) != expected:
            raise HistoricalCandidateGateError(f"{label} hash가 다릅니다: {relative}")
        result[relative] = expected
    return result


def validate_contract(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    validate_intake_registry_v1_2()
    config = load_strict_json_object(config_path)
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("dashboard_id")
        != "saju-historical-candidate-dashboard-v1.0.0"
        or config.get("gate_version") != GATE_VERSION
        or config.get("candidate_scope") != CANDIDATE_SCOPE
        or config.get("report_root")
        != "data/reports/saju_historical_candidate/v1.0.0"
        or config.get("parent_intake_registry")
        != {
            "path": "configs/runtime/intake_registry-v1.2.0.json",
            "sha256": sha256_file(INTAKE_REGISTRY),
        }
        or config.get("service")
        != {
            "host": "127.0.0.1",
            "default_port": 8766,
            "maximum_sessions": CANDIDATE_MAX_SESSIONS,
            "idle_timeout_seconds": CANDIDATE_IDLE_SECONDS,
            "persistence": "process_memory_only",
            "public_session_read_api": False,
            "public_chart_result_event": False,
            "model_context_binding": False,
            "period_calculation": False,
        }
        or config.get("governance")
        != {
            "diagnostic_dashboard_binding": True,
            "production_application_binding": False,
            "runtime_release_approved": False,
            "context_window_changed": False,
            "mix20k_v3_1_generated": False,
            "additional_training_performed": False,
            "model_promotion_performed": False,
            "sealed_blind_accessed": False,
        }
    ):
        raise HistoricalCandidateGateError("candidate dashboard config identity가 다릅니다.")
    hashes = {
        key: _validate_hashed_paths(config.get(key), key)
        for key in (
            "candidate_assets",
            "existing_dashboard_assets",
            "implementation",
        )
    }
    gate_contract = config.get("candidate_gate_contract")
    if not isinstance(gate_contract, Mapping) or set(gate_contract) != {
        "path",
        "sha256",
    }:
        raise HistoricalCandidateGateError("candidate Gate parent가 다릅니다.")
    gate_path = _safe_repo_path(str(gate_contract["path"]), "candidate Gate")
    if sha256_file(gate_path) != gate_contract.get("sha256"):
        raise HistoricalCandidateGateError("candidate Gate parent hash가 다릅니다.")
    gate = load_strict_json_object(gate_path)
    if (
        gate.get("gate_version") != GATE_VERSION
        or gate.get("strata") != EXPECTED_STRATA
        or sum(gate.get("strata", {}).values()) != 120
        or gate.get("required_passed_cases") != 120
    ):
        raise HistoricalCandidateGateError("candidate Gate strata 계약이 다릅니다.")
    config["validated_hashes"] = hashes
    return config


def _arguments(
    *,
    birth_date: str,
    birth_time: str | None = "12:00",
    precision: str = "exact",
    time_range: dict[str, str] | None = None,
    calendar: str = "solar",
    leap_month: bool | None = None,
) -> dict[str, Any]:
    return {
        "birth_date": birth_date,
        "calendar": calendar,
        "leap_month": leap_month,
        "birth_time": birth_time if precision == "exact" else None,
        "time_precision": precision,
        "time_range": time_range if precision == "range" else None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _complete_input(
    arguments: Mapping[str, Any], signer: RuntimeIdSigner
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = empty_intake_state()
    events: list[dict[str, Any]] = [
        {"type": "opt_in", "accepted": True},
        {
            "type": "set_slot",
            "field": "birth_date",
            "value": arguments["birth_date"],
        },
        {
            "type": "set_slot",
            "field": "calendar",
            "value": arguments["calendar"],
        },
    ]
    if arguments["calendar"] == "lunar":
        events.append(
            {
                "type": "set_slot",
                "field": "leap_month",
                "value": arguments["leap_month"],
            }
        )
    if arguments["time_precision"] == "unknown":
        events.append({"type": "set_time_unknown"})
    else:
        events.extend(
            [
                {
                    "type": "set_slot",
                    "field": "time_precision",
                    "value": arguments["time_precision"],
                },
                {
                    "type": "set_slot",
                    "field": (
                        "birth_time"
                        if arguments["time_precision"] == "exact"
                        else "time_range"
                    ),
                    "value": (
                        arguments["birth_time"]
                        if arguments["time_precision"] == "exact"
                        else arguments["time_range"]
                    ),
                },
            ]
        )
    events.append(
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": deepcopy(arguments["birthplace"]),
        }
    )
    transition: dict[str, Any] = {}
    for event in events:
        transition = advance_intake(state, event, signer, READY)
        state = transition["session_state"]
    if transition["decision"]["action"] != "call_candidate_chart":
        raise HistoricalCandidateGateError("완성 입력이 candidate call로 전이되지 않았습니다.")
    return state, transition["decision"]


def _engine_result(
    engine: SajuRuntimeEngineV13,
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
    signer: RuntimeIdSigner,
) -> tuple[dict[str, Any], dict[str, Any]]:
    internal = engine.calculate_chart(deepcopy(decision["arguments"]))
    transition = advance_intake(
        state,
        {
            "type": "chart_result",
            "result": {**internal, "call_id": decision["call_id"]},
        },
        signer,
        READY,
    )
    return internal, transition


def _all_past_evidence(internal: Mapping[str, Any]) -> bool:
    facts = [internal.get("hard_facts"), internal.get("stable_facts")]
    alternatives = internal.get("alternative_charts")
    if not isinstance(alternatives, list) or not alternatives:
        return False
    facts.extend(
        item.get("hard_facts") if isinstance(item, Mapping) else None
        for item in alternatives
    )
    for value in facts:
        evidence = (
            value.get("solar_term_evidence") if isinstance(value, Mapping) else None
        )
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("authority_classes")
            != [PAST_OFFICIAL_CORROBORATED]
            or evidence.get("overall_authority")
            != PAST_OFFICIAL_CORROBORATED
            or evidence.get("contains_future_nonapproval") is not False
            or any(
                not isinstance(boundary, Mapping)
                or boundary.get("authority_class")
                != PAST_OFFICIAL_CORROBORATED
                for boundary in evidence.get("boundaries", [])
            )
        ):
            return False
    return True


def _accepted(
    engine: SajuRuntimeEngineV13,
    signer: RuntimeIdSigner,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state, decision = _complete_input(arguments, signer)
    internal, transition = _engine_result(engine, state, decision, signer)
    if (
        transition["decision"]["action"] != "render_candidate"
        or transition["session_state"]["chart"]["fact_authority"]
        != "HARD_CANDIDATE"
        or not _all_past_evidence(internal)
    ):
        raise HistoricalCandidateGateError("과거 공식 후보가 render되지 않았습니다.")
    return internal, transition, decision


def _record(
    strata: dict[str, dict[str, int]],
    failures: Counter[str],
    stratum: str,
    operation: Any,
) -> None:
    strata[stratum]["cases"] += 1
    try:
        passed = operation() is not False
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        IntakeFsmError,
        RuntimeCalculationError,
        HistoricalCandidateGateError,
    ):
        passed = False
    if passed:
        strata[stratum]["passed"] += 1
    else:
        strata[stratum]["failed"] += 1
        failures[stratum] += 1


def evaluate(ephemeris_path: Path, config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    config = validate_contract(config_path)
    if (
        not ephemeris_path.is_absolute()
        or ephemeris_path.is_symlink()
        or not ephemeris_path.is_file()
        or sha256_file(ephemeris_path) != DE440S_SHA256
    ):
        raise HistoricalCandidateGateError("고정 DE440s 절대경로·SHA-256이 다릅니다.")
    signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    strata = {
        name: {"cases": 0, "passed": 0, "failed": 0}
        for name in EXPECTED_STRATA
    }
    failures: Counter[str] = Counter()
    authority_accepted = 0
    exact_ids = 0
    chart_set_ids = 0
    public_privacy_checks = 0

    with SajuRuntimeEngineV13(
        signer=signer,
        enable_candidate_runtime=True,
        ephemeris_path=ephemeris_path,
    ) as engine:
        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-06-15", birth_time=f"{index + 1:02d}:20"
            )

            def past_exact(arguments=arguments) -> bool:
                nonlocal authority_accepted, exact_ids
                internal, transition, _ = _accepted(engine, signer, arguments)
                authority_accepted += 1
                exact_ids += 1
                return (
                    internal.get("chart_id") is not None
                    and internal.get("chart_set_id") is None
                    and transition["session_state"]["chart"]["chart_id"]
                    == internal["chart_id"]
                )

            _record(strata, failures, "past_exact_official", past_exact)

        for index, year in enumerate(PAST_YEARS):
            start = 8 + index % 5
            arguments = _arguments(
                birth_date=f"{year:04d}-07-15",
                birth_time=None,
                precision="range",
                time_range={"start": f"{start:02d}:00", "end": f"{start + 1:02d}:00"},
            )

            def past_range(arguments=arguments) -> bool:
                nonlocal authority_accepted, chart_set_ids
                internal, transition, _ = _accepted(engine, signer, arguments)
                authority_accepted += 1
                chart_set_ids += 1
                return (
                    internal.get("chart_id") is None
                    and internal.get("chart_set_id") is not None
                    and transition["session_state"]["chart"]["chart_set_id"]
                    == internal["chart_set_id"]
                )

            _record(strata, failures, "past_range_official", past_range)

        for year in PAST_YEARS:
            arguments = _arguments(
                birth_date=f"{year:04d}-08-15",
                birth_time=None,
                precision="unknown",
            )

            def past_unknown(arguments=arguments) -> bool:
                nonlocal authority_accepted, chart_set_ids
                internal, transition, _ = _accepted(engine, signer, arguments)
                authority_accepted += 1
                chart_set_ids += 1
                return (
                    internal.get("chart_id") is None
                    and internal.get("chart_set_id") is not None
                    and transition["session_state"]["chart"]["chart_set_id"]
                    == internal["chart_set_id"]
                )

            _record(strata, failures, "past_unknown_official", past_unknown)

        baengno_inputs = [
            ("1964-09-07", f"23:{minute:02d}", "申", False)
            for minute in range(54, 59)
        ] + [
            ("1964-09-08", f"00:{minute:02d}", "酉", True)
            for minute in range(5)
        ]
        for birth_date, birth_time, expected_branch, after in baengno_inputs:
            arguments = _arguments(
                birth_date=birth_date, birth_time=birth_time
            )

            def baengno(
                arguments=arguments,
                expected_branch=expected_branch,
                after=after,
            ) -> bool:
                nonlocal authority_accepted, exact_ids
                internal, _, _ = _accepted(engine, signer, arguments)
                authority_accepted += 1
                exact_ids += 1
                facts = internal["hard_facts"]
                boundaries = facts["solar_term_evidence"]["boundaries"]
                has_baengno = any(
                    boundary.get("term_name") == "백로"
                    and boundary.get("official_display_minute_fixed_kst")
                    == "1964-09-07T23:59+09:00"
                    for boundary in boundaries
                )
                return (
                    facts["pillars"]["month"]["branch"] == expected_branch
                    and has_baengno is after
                )

            _record(strata, failures, "baengno_1964_boundary", baengno)

        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-01-{index + 10:02d}",
                birth_time="12:00",
                calendar="lunar",
                leap_month=False,
            )

            def lunar(arguments=arguments) -> bool:
                nonlocal authority_accepted, exact_ids
                internal, _, _ = _accepted(engine, signer, arguments)
                authority_accepted += 1
                exact_ids += 1
                return internal["normalized_input"]["calendar"] == "lunar"

            _record(strata, failures, "lunar_past_official", lunar)

        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-10-15", birth_time=f"{index + 1:02d}:10"
            )

            def correction(arguments=arguments) -> bool:
                internal, transition, previous = _accepted(engine, signer, arguments)
                del internal
                minute = int(arguments["birth_time"].split(":")[1]) + 1
                corrected = advance_intake(
                    transition["session_state"],
                    {
                        "type": "correct_slot",
                        "field": "birth_time",
                        "value": f"{arguments['birth_time'][:2]}:{minute:02d}",
                    },
                    signer,
                    READY,
                )
                return (
                    corrected["session_state"]["chart"]["chart_valid"] is False
                    and corrected["decision"]["action"] == "call_candidate_chart"
                    and corrected["decision"]["call_id"] != previous["call_id"]
                )

            _record(strata, failures, "correction_invalidation", correction)

        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-11-15", birth_time=f"{index + 1:02d}:20"
            )

            def stale(arguments=arguments) -> bool:
                state, decision = _complete_input(arguments, signer)
                internal = engine.calculate_chart(deepcopy(decision["arguments"]))
                corrected = advance_intake(
                    state,
                    {
                        "type": "correct_slot",
                        "field": "birth_time",
                        "value": f"{arguments['birth_time'][:2]}:21",
                    },
                    signer,
                    READY,
                )
                try:
                    advance_intake(
                        corrected["session_state"],
                        {
                            "type": "chart_result",
                            "result": {**internal, "call_id": decision["call_id"]},
                        },
                        signer,
                        READY,
                    )
                except IntakeFsmError:
                    return corrected["decision"]["call_id"] != decision["call_id"]
                return False

            _record(strata, failures, "stale_call_rejection", stale)

        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-12-15", birth_time=f"{index + 1:02d}:30"
            )

            def tampered(arguments=arguments, index=index) -> bool:
                state, decision = _complete_input(arguments, signer)
                internal = engine.calculate_chart(deepcopy(decision["arguments"]))
                altered = deepcopy(internal)
                if index % 2 == 0:
                    altered["chart_id"] = "sc2_" + "0" * 64
                else:
                    altered["alternative_charts"][0]["chart_id"] = "sc2_" + "0" * 64
                try:
                    advance_intake(
                        state,
                        {
                            "type": "chart_result",
                            "result": {**altered, "call_id": decision["call_id"]},
                        },
                        signer,
                        READY,
                    )
                except IntakeFsmError:
                    return True
                return False

            _record(strata, failures, "tampered_hmac_rejection", tampered)

        for index, year in enumerate(PROFILE_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-06-15", birth_time=f"{index + 1:02d}:40"
            )

            def profile(arguments=arguments) -> bool:
                state, decision = _complete_input(arguments, signer)
                internal, transition = _engine_result(engine, state, decision, signer)
                return (
                    internal["hard_facts"]["solar_term_evidence"]["overall_authority"]
                    != PAST_OFFICIAL_CORROBORATED
                    and transition["decision"]["reason_code"]
                    == "CANDIDATE_OFFICIAL_EVIDENCE_REQUIRED"
                    and transition["session_state"]["chart"]["chart_valid"] is False
                )

            _record(strata, failures, "profile_coverage_block", profile)

        for minute in range(17, 27):
            arguments = _arguments(
                birth_date="2026-09-01", birth_time=f"00:{minute:02d}"
            )

            def future(arguments=arguments) -> bool:
                state, decision = _complete_input(arguments, signer)
                internal, transition = _engine_result(engine, state, decision, signer)
                return (
                    internal["hard_facts"]["solar_term_evidence"]["overall_authority"]
                    == PAST_OFFICIAL_CORROBORATED
                    and transition["decision"]["reason_code"]
                    == "CANDIDATE_AFTER_OFFICIAL_SNAPSHOT"
                    and transition["session_state"]["chart"]["chart_valid"] is False
                )

            _record(strata, failures, "future_cutoff_block", future)

        for index in range(10):

            def period(index=index) -> bool:
                state = empty_intake_state()
                if index % 2:
                    state = advance_intake(
                        state, {"type": "opt_in", "accepted": True}, signer, READY
                    )["session_state"]
                transition = advance_intake(
                    state, {"type": "request_period"}, signer, READY
                )
                return (
                    transition["decision"]["reason_code"]
                    == "CANDIDATE_PERIOD_OUT_OF_SCOPE"
                    and transition["session_state"]["period"]
                    == {"request": None, "result": None}
                )

            _record(strata, failures, "period_scope_block", period)

        for index, year in enumerate(PAST_YEARS):
            arguments = _arguments(
                birth_date=f"{year:04d}-05-16", birth_time=f"{index + 1:02d}:50"
            )

            def privacy(arguments=arguments) -> bool:
                nonlocal public_privacy_checks
                _, transition, _ = _accepted(engine, signer, arguments)
                public = _public_transition(transition)
                encoded = json.dumps(public, ensure_ascii=False, allow_nan=False)
                internal_event_rejected = False
                try:
                    assert_public_event({"type": "chart_result", "result": {}})
                except IntakeFsmError:
                    internal_event_rejected = True
                passed = (
                    internal_event_rejected
                    and all(key not in encoded for key in PUBLIC_FORBIDDEN_KEYS)
                    and str(arguments["birth_date"]) not in encoded
                    and str(arguments["birth_time"]) not in encoded
                    and str(arguments["birthplace"]["city"]) not in encoded
                    and public["governance"]["model_context_binding"] is False
                )
                public_privacy_checks += int(passed)
                return passed

            _record(strata, failures, "public_event_privacy", privacy)

    cases = sum(value["cases"] for value in strata.values())
    passed = sum(value["passed"] for value in strata.values())
    failed = sum(value["failed"] for value in strata.values())
    store = CandidateSessionStore()
    store_contract = (
        store.maximum == CANDIDATE_MAX_SESSIONS
        and store.idle_seconds == CANDIDATE_IDLE_SECONDS
    )
    gate_checks = {
        "all_cases_passed": cases == passed == 120 and failed == 0,
        "past_authority_only": authority_accepted == 50,
        "all_alternatives_checked": (
            strata["past_range_official"]["passed"] == 10
            and strata["past_unknown_official"]["passed"] == 10
        ),
        "cutoff_enforced": strata["future_cutoff_block"]["passed"] == 10,
        "hmac_identity_recomputed": (
            strata["stale_call_rejection"]["passed"] == 10
            and strata["tampered_hmac_rejection"]["passed"] == 10
        ),
        "period_disabled": strata["period_scope_block"]["passed"] == 10,
        "public_chart_result_rejected": strata["public_event_privacy"]["passed"] == 10,
        "no_raw_birth_data_in_public_report": public_privacy_checks == 10,
        "no_internal_trace_in_public_response": public_privacy_checks == 10,
        "loopback_only": config["service"]["host"] == "127.0.0.1",
        "ephemeral_bounded_store": store_contract,
        "existing_dashboard_assets_unchanged": bool(
            config["validated_hashes"]["existing_dashboard_assets"]
        ),
    }
    diagnostic_target_met = all(gate_checks.values())
    report = {
        "schema_version": "1.0.0",
        "gate_version": GATE_VERSION,
        "status": (
            "passed_diagnostic_dashboard_only"
            if diagnostic_target_met
            else "failed"
        ),
        "diagnostic_target_met": diagnostic_target_met,
        "cases": cases,
        "passed": passed,
        "failed": failed,
        "strata": strata,
        "failure_counts": dict(sorted(failures.items())),
        "gate_checks": gate_checks,
        "runtime_behavior": {
            "accepted_past_official_cases": authority_accepted,
            "exact_chart_id_cases": exact_ids,
            "chart_set_id_cases": chart_set_ids,
            "baengno_official_display_minute": "1964-09-07T23:59+09:00",
            "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
            "candidate_fact_authority": "HARD_CANDIDATE",
            "period_block_code": "CANDIDATE_PERIOD_OUT_OF_SCOPE",
        },
        "inputs": {
            "dashboard_config_sha256": sha256_file(config_path),
            "intake_registry_sha256": sha256_file(INTAKE_REGISTRY),
            "ephemeris_sha256": DE440S_SHA256,
            "gate_implementation_sha256": sha256_file(Path(__file__)),
            "candidate_assets": config["validated_hashes"]["candidate_assets"],
            "existing_dashboard_assets": config["validated_hashes"][
                "existing_dashboard_assets"
            ],
            "implementation": config["validated_hashes"]["implementation"],
            "test_signer_only": True,
            "production_id_key_used": False,
        },
        "governance": deepcopy(config["governance"]),
    }
    if any(report["governance"].get(field) is not False for field in FALSE_GOVERNANCE):
        raise HistoricalCandidateGateError("candidate report governance가 열렸습니다.")
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False)
    if any(
        re.search(pattern, encoded)
        for pattern in (
            r'"birth_date"\s*:',
            r'"birth_time"\s*:',
            r'"normalized_input"\s*:',
            r'"internal_trace"\s*:',
            r'"chart_id"\s*:',
            r'"chart_set_id"\s*:',
        )
    ):
        raise HistoricalCandidateGateError("candidate 공개 report에 원시 입력·ID가 포함됐습니다.")
    return report


def _safe_output_base(path: Path) -> Path:
    if path in {Path("/"), Path.home()} or path.is_symlink():
        raise HistoricalCandidateGateError("candidate report 출력 경로가 안전하지 않습니다.")
    resolved = path.resolve()
    if resolved == Path("/") or resolved == Path.home().resolve():
        raise HistoricalCandidateGateError("candidate report 출력 경로가 너무 넓습니다.")
    return resolved


def write_report(report: dict[str, Any], output_base: Path = REPORT_ROOT) -> Path:
    core = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(core).hexdigest()[:12]
    aggregate = {**report, "build_id": build_id}
    aggregate_bytes = canonical_json_bytes(aggregate) + b"\n"
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build_id,
        "gate_version": GATE_VERSION,
        "artifacts": {
            "aggregate.json": {
                "bytes": len(aggregate_bytes),
                "sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
            }
        },
        "governance": deepcopy(report["governance"]),
        "raw_case_output_tracked": False,
        "sealed_blind_accessed": False,
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    directory = _safe_output_base(output_base) / build_id
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or (directory / "aggregate.json").read_bytes() != aggregate_bytes
            or (directory / "build_manifest.json").read_bytes() != manifest_bytes
        ):
            raise HistoricalCandidateGateError("기존 candidate report build가 다릅니다.")
        return directory
    directory.mkdir(mode=0o755)
    try:
        with (directory / "aggregate.json").open("xb") as stream:
            stream.write(aggregate_bytes)
        with (directory / "build_manifest.json").open("xb") as stream:
            stream.write(manifest_bytes)
    except Exception:
        for filename in ("aggregate.json", "build_manifest.json"):
            target = directory / filename
            if target.is_file() and not target.is_symlink():
                target.unlink()
        directory.rmdir()
        raise
    return directory


def verify_report(report_root: Path, config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    validate_contract(config_path)
    if report_root.is_symlink() or not report_root.is_dir():
        raise HistoricalCandidateGateError("candidate report root가 없거나 symlink입니다.")
    aggregate_path = report_root / "aggregate.json"
    manifest_path = report_root / "build_manifest.json"
    aggregate = load_strict_json_object(aggregate_path)
    manifest = load_strict_json_object(manifest_path)
    build_id = aggregate.get("build_id")
    core = dict(aggregate)
    core.pop("build_id", None)
    expected_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    if (
        build_id != expected_id
        or report_root.name != build_id
        or aggregate.get("gate_version") != GATE_VERSION
        or aggregate.get("diagnostic_target_met") is not True
        or aggregate.get("cases") != 120
        or aggregate.get("passed") != 120
        or aggregate.get("failed") != 0
        or not all(aggregate.get("gate_checks", {}).values())
        or any(aggregate.get("governance", {}).get(field) is not False for field in FALSE_GOVERNANCE)
        or aggregate.get("governance", {}).get("diagnostic_dashboard_binding") is not True
        or manifest.get("build_id") != build_id
        or manifest.get("gate_version") != GATE_VERSION
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("artifacts", {}).get("aggregate.json")
        != {
            "bytes": aggregate_path.stat().st_size,
            "sha256": sha256_file(aggregate_path),
        }
    ):
        raise HistoricalCandidateGateError("candidate report 검증에 실패했습니다.")
    return {
        "status": "verified_diagnostic_dashboard_only",
        "build_id": build_id,
        "cases": 120,
        "diagnostic_target_met": True,
        "runtime_release_approved": False,
        "production_application_binding": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="과거 공식 근거 후보 통합 Gate")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--ephemeris", type=Path, required=True)
    run.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            config = validate_contract(args.config)
            result = {"status": "valid", "dashboard_id": config["dashboard_id"]}
        elif args.command == "plan":
            config = validate_contract(args.config)
            result = {
                "status": "planned",
                "gate_version": GATE_VERSION,
                "cases": sum(EXPECTED_STRATA.values()),
                "strata": EXPECTED_STRATA,
                "report_root": config["report_root"],
                "writes_performed": False,
                "gpu_execution": False,
                "training_execution": False,
                "sealed_blind_access": False,
            }
        elif args.command == "run":
            report = evaluate(args.ephemeris, args.config)
            output = write_report(report, args.output_base)
            result = {
                "status": report["status"],
                "build_id": output.name,
                "output": str(output),
                "cases": report["cases"],
                "passed": report["passed"],
                "diagnostic_target_met": report["diagnostic_target_met"],
                "runtime_release_approved": False,
                "production_application_binding": False,
            }
        else:
            result = verify_report(args.report_root, args.config)
    except (
        OSError,
        ValueError,
        IntakeFsmError,
        RuntimeCalculationError,
        HistoricalCandidateGateError,
    ) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
