# intake_fsm_gate.py - 구조화 intake FSM의 100건 합성 app preflight를 재현한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.intake_contracts_v1_1 import (
    DECISION_ACTIONS as CONTRACT_DECISION_ACTIONS,
)
from scripts.runtime.intake_contracts_v1_1 import EVENT_TYPES as CONTRACT_EVENT_TYPES
from scripts.runtime.intake_contracts_v1_1 import (
    FSM_GATE_CONFIG,
    INTAKE_REGISTRY,
    load_strict_json_object,
    validate_intake_registry_v1_1,
)
from scripts.runtime.intake_fsm import (
    DECISION_ACTIONS,
    EVENT_TYPES,
    SLOT_FIELDS,
    IntakeFsmError,
    advance_intake,
    empty_intake_state,
)

GATE_VERSION = "saju-intake-fsm-gate-v1.1.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_intake_fsm/v1.1.0"
MODEL_GATE_REPORT = REPO_ROOT / (
    "data/reports/saju_1b_baseline/phase5-stateful-chat-gate/v1.0.0/"
    "stateful-gate-f5b76dde1921/evaluation_summary.json"
)
READY = {
    "runtime_release_ready": True,
    "feature_enabled": True,
    "production_id_key_ready": True,
    "fsm_gate_passed": True,
    "encrypted_persistence_ready": True,
    "retention_policy_ready": True,
}
BLOCKED = {key: False for key in READY}


class IntakeFsmGateError(RuntimeError):
    """FSM app preflight 산출물·경로 계약 위반."""


def _advance(
    state: dict[str, Any],
    event: dict[str, Any],
    signer: RuntimeIdSigner,
    *,
    ready: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = advance_intake(state, event, signer, READY if ready else BLOCKED)
    return value["session_state"], value["decision"]


def _complete_input(
    index: int,
    signer: RuntimeIdSigner,
    *,
    unknown_time: bool = False,
    lunar: bool = False,
    ready: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = empty_intake_state()
    state, decision = _advance(state, {"type": "opt_in", "accepted": True}, signer, ready=ready)
    state, decision = _advance(
        state,
        {"type": "set_slot", "field": "birth_date", "value": f"19{80 + index % 10:02d}-01-05"},
        signer,
        ready=ready,
    )
    state, decision = _advance(
        state,
        {"type": "set_slot", "field": "calendar", "value": "lunar" if lunar else "solar"},
        signer,
        ready=ready,
    )
    if lunar:
        state, decision = _advance(
            state,
            {"type": "set_slot", "field": "leap_month", "value": bool(index % 2)},
            signer,
            ready=ready,
        )
    if unknown_time:
        state, decision = _advance(state, {"type": "set_time_unknown"}, signer, ready=ready)
    else:
        state, decision = _advance(
            state,
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            signer,
            ready=ready,
        )
        state, decision = _advance(
            state,
            {"type": "set_slot", "field": "birth_time", "value": f"{index % 24:02d}:30"},
            signer,
            ready=ready,
        )
    state, decision = _advance(
        state,
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {
                "country_code": "KR",
                "city": f"서울-{index}",
                "timezone": "Asia/Seoul",
                "longitude": None,
                "latitude": None,
            },
        },
        signer,
        ready=ready,
    )
    return state, decision


def _chart_result(
    state: dict[str, Any], signer: RuntimeIdSigner, index: int, *, unknown: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    preview = advance_intake(state, {"type": "request_chart"}, signer, READY)
    call_id = preview["decision"].get("call_id")
    identity = {"case": index, "input": state["birth_slots"]}
    event = {
        "type": "chart_result",
        "result": {
            "status": "partial" if unknown else "ok",
            "hard_facts": {"pillars": {"year": {"ganzhi": "戊辰"}}},
            "fact_authority": "POLICY_BOUND_RULE" if unknown else "HARD_GT",
            "chart_id": None if unknown else signer.chart_id(identity),
            "chart_set_id": signer.chart_set_id(identity) if unknown else None,
            "call_id": call_id,
        },
    }
    return _advance(state, event, signer)


def _structural_checks(signer: RuntimeIdSigner) -> dict[str, bool]:
    free_text_rejected = False
    try:
        advance_intake(
            empty_intake_state(),
            {"type": "free_text", "text": "내 사주를 봐줘"},
            signer,
            READY,
        )
    except IntakeFsmError:
        free_text_rejected = True

    semantic_mismatch_rejected = False
    tampered = empty_intake_state()
    tampered["saju_opt_in"] = True
    tampered["current_intent"] = "chart"
    tampered["birth_slots"]["birth_date"] = "1989-01-05"
    tampered["field_provenance"]["birth_date"] = "user_explicit"
    try:
        advance_intake(tampered, {"type": "request_chart"}, signer, READY)
    except IntakeFsmError:
        semantic_mismatch_rejected = True

    non_hmac_id_rejected = False
    state, _decision_value = _complete_input(97, signer)
    call_id = _decision_value["call_id"]
    try:
        _advance(
            state,
            {
                "type": "chart_result",
                "result": {
                    "status": "ok",
                    "hard_facts": {"pillars": {}},
                    "fact_authority": "HARD_GT",
                    "chart_id": "sc2_not-a-complete-hmac",
                    "chart_set_id": None,
                    "call_id": call_id,
                },
            },
            signer,
        )
    except IntakeFsmError:
        non_hmac_id_rejected = True

    replayed_period_result_rejected = False
    state, _decision_value = _complete_input(98, signer)
    state, _decision_value = _chart_result(state, signer, 98)
    state, period_decision = _advance(
        state,
        {
            "type": "request_period",
            "request": {
                "period_type": "day",
                "start_date": "2026-08-31",
                "end_date": None,
                "timezone": "Asia/Seoul",
            },
        },
        signer,
    )
    period_event = {
        "type": "period_result",
        "result": {
            "status": "ok",
            "hard_facts": {"period": {}},
            "fact_authority": "HARD_GT",
            "call_id": period_decision["call_id"],
        },
    }
    state, _decision_value = _advance(state, period_event, signer)
    try:
        _advance(state, period_event, signer)
    except IntakeFsmError:
        replayed_period_result_rejected = True

    stale_result_rejected = False
    state, _decision_value = _complete_input(99, signer)
    state, _decision_value = _chart_result(state, signer, 99)
    state, first_period_decision = _advance(
        state,
        {
            "type": "request_period",
            "request": {
                "period_type": "day",
                "start_date": "2026-08-31",
                "end_date": None,
                "timezone": "Asia/Seoul",
            },
        },
        signer,
    )
    state, second_period_decision = _advance(
        state,
        {
            "type": "request_period",
            "request": {
                "period_type": "day",
                "start_date": "2026-09-01",
                "end_date": None,
                "timezone": "Asia/Seoul",
            },
        },
        signer,
    )
    stale_period_event = {
        "type": "period_result",
        "result": {
            "status": "ok",
            "hard_facts": {"period": {}},
            "fact_authority": "HARD_GT",
            "call_id": first_period_decision["call_id"],
        },
    }
    try:
        _advance(state, stale_period_event, signer)
    except IntakeFsmError:
        stale_result_rejected = (
            first_period_decision["call_id"] != second_period_decision["call_id"]
        )

    cached_fingerprint_mismatch_rejected = False
    state, _decision_value = _complete_input(96, signer)
    state, _decision_value = _chart_result(state, signer, 96)
    state["chart"]["chart_input_fingerprint"] = "sif2_" + "0" * 64
    try:
        _advance(state, {"type": "request_chart"}, signer)
    except IntakeFsmError:
        cached_fingerprint_mismatch_rejected = True

    malformed_state_type_rejected = False
    malformed = empty_intake_state()
    malformed["saju_opt_in"] = True
    malformed["current_intent"] = "chart"
    malformed["birth_slots"]["timezone"] = []
    try:
        _advance(malformed, {"type": "request_chart"}, signer)
    except IntakeFsmError:
        malformed_state_type_rejected = True

    forbidden = {"name", "family_relationship", "job", "gender_for_daeun"}
    return {
        "event_types_match_contract": tuple(sorted(EVENT_TYPES))
        == tuple(sorted(CONTRACT_EVENT_TYPES)),
        "decision_actions_match_contract": tuple(sorted(DECISION_ACTIONS))
        == tuple(sorted(CONTRACT_DECISION_ACTIONS)),
        "free_text_event_rejected": free_text_rejected,
        "name_family_job_gender_slots_absent": SLOT_FIELDS.isdisjoint(forbidden),
        "full_hmac_id_required": non_hmac_id_rejected,
        "replayed_period_result_rejected": replayed_period_result_rejected,
        "stale_tool_result_rejected": stale_result_rejected,
        "state_semantic_mismatch_rejected": semantic_mismatch_rejected,
        "cached_input_fingerprint_mismatch_rejected": (
            cached_fingerprint_mismatch_rejected
        ),
        "malformed_state_type_rejected": malformed_state_type_rejected,
    }


def _failure_counts(
    records: list[dict[str, Any]], structural: dict[str, bool]
) -> dict[str, int]:
    case_failures = Counter(
        failure
        for record in records
        for failure in record.get("failure_codes", [])
        if isinstance(failure, str)
    )
    return {
        "free_text_parser_present": int(not structural["free_text_event_rejected"]),
        "name_family_job_or_gender_slot_requested": int(
            not structural["name_family_job_gender_slots_absent"]
        ),
        "unknown_time_guessed": case_failures["unknown_time_not_preserved_partial"],
        "confirmed_field_reasked": case_failures["confirmed_date_reasked"],
        "correction_cache_not_invalidated": case_failures[
            "correction_did_not_invalidate_and_recalculate"
        ],
        "runtime_block_bypassed": case_failures["runtime_block_did_not_fail_closed"],
        "fake_ui_or_completion_claim": case_failures["chart_facts_not_rendered"],
        "non_hmac_session_fingerprint": int(
            not structural["full_hmac_id_required"]
        ),
        "replayed_period_result_accepted": int(
            not structural["replayed_period_result_rejected"]
        ),
        "stale_tool_result_accepted": int(
            not structural["stale_tool_result_rejected"]
        ),
        "state_semantic_mismatch_accepted": int(
            not structural["state_semantic_mismatch_rejected"]
        ),
        "cached_input_fingerprint_mismatch_accepted": int(
            not structural["cached_input_fingerprint_mismatch_rejected"]
        ),
        "malformed_state_type_accepted": int(
            not structural["malformed_state_type_rejected"]
        ),
    }


def _evaluate_case(index: int, stratum: str, signer: RuntimeIdSigner) -> list[str]:
    failures: list[str] = []
    if stratum == "ask_birth_date":
        state, decision = _advance(
            empty_intake_state(), {"type": "opt_in", "accepted": True}, signer
        )
        if decision["action"] != "ask_birth_date":
            failures.append("birth_date_not_requested_first")
        if state["birth_slots"]["gender_for_daeun"] != "unspecified":
            failures.append("gender_default_missing")
    elif stratum == "no_reask_confirmed_date":
        state, _ = _advance(
            empty_intake_state(), {"type": "opt_in", "accepted": True}, signer
        )
        state, decision = _advance(
            state,
            {"type": "set_slot", "field": "birth_date", "value": "1989-01-05"},
            signer,
        )
        if decision["action"] != "ask_calendar":
            failures.append("confirmed_date_reasked")
    elif stratum == "lunar_leap_month":
        state, _ = _advance(
            empty_intake_state(), {"type": "opt_in", "accepted": True}, signer
        )
        for field, value in (("birth_date", "1989-01-05"), ("calendar", "lunar")):
            state, decision = _advance(
                state, {"type": "set_slot", "field": field, "value": value}, signer
            )
        if decision["action"] != "ask_leap_month":
            failures.append("lunar_leap_not_requested")
    elif stratum == "exact_time_value":
        state, _ = _advance(
            empty_intake_state(), {"type": "opt_in", "accepted": True}, signer
        )
        for field, value in (
            ("birth_date", "1989-01-05"),
            ("calendar", "solar"),
            ("time_precision", "exact"),
        ):
            state, decision = _advance(
                state, {"type": "set_slot", "field": field, "value": value}, signer
            )
        if decision["action"] != "ask_exact_time_or_range":
            failures.append("exact_time_value_not_requested")
    elif stratum == "unknown_time_partial":
        state, decision = _complete_input(index, signer, unknown_time=True)
        if decision["action"] != "call_chart":
            failures.append("unknown_time_did_not_handoff")
        state, decision = _chart_result(state, signer, index, unknown=True)
        if decision["action"] != "render_result" or state["chart"]["chart_id"] is not None:
            failures.append("unknown_time_not_preserved_partial")
    elif stratum == "birthplace_then_handoff":
        _state, decision = _complete_input(index, signer)
        if decision["action"] != "call_chart":
            failures.append("complete_input_did_not_handoff")
        elif decision.get("arguments", {}).get("gender_for_daeun") != "unspecified":
            failures.append("gender_default_not_in_tool_call")
    elif stratum == "runtime_blocked":
        _state, decision = _complete_input(index, signer, ready=False)
        if (
            decision["action"] != "explain_runtime_blocked"
            or decision["reason_code"] != "APP_RUNTIME_NOT_READY"
            or "payload" in decision
        ):
            failures.append("runtime_block_did_not_fail_closed")
    elif stratum == "correction_invalidation":
        state, _ = _complete_input(index, signer)
        state, _ = _chart_result(state, signer, index)
        state, decision = _advance(
            state,
            {"type": "correct_slot", "field": "birth_date", "value": "1990-01-05"},
            signer,
        )
        if state["chart"]["chart_valid"] or decision["action"] != "call_chart":
            failures.append("correction_did_not_invalidate_and_recalculate")
    elif stratum == "period_handoff":
        state, _ = _complete_input(index, signer)
        state, _ = _chart_result(state, signer, index)
        state, decision = _advance(
            state,
            {
                "type": "request_period",
                "request": {
                    "period_type": "day",
                    "start_date": "2026-08-31",
                    "end_date": None,
                    "timezone": "Asia/Seoul",
                },
            },
            signer,
        )
        if decision["action"] != "call_period" or not decision["arguments"]["chart_id"].startswith("sc2_"):
            failures.append("period_did_not_use_exact_hmac_chart")
    elif stratum == "tool_result_render":
        state, _ = _complete_input(index, signer)
        state, decision = _chart_result(state, signer, index)
        if decision["action"] != "render_result" or not decision.get("payload", {}).get("hard_facts"):
            failures.append("chart_facts_not_rendered")
        elif not state["chart"]["chart_input_fingerprint"].startswith("sif2_"):
            failures.append("hmac_fingerprint_missing")
    else:
        failures.append("unknown_stratum")
    return failures


def evaluate() -> dict[str, Any]:
    intake_registry = validate_intake_registry_v1_1()
    gate_config = load_strict_json_object(FSM_GATE_CONFIG)
    signer = RuntimeIdSigner.for_test(bytes(range(32)))
    strata = (
        "ask_birth_date",
        "no_reask_confirmed_date",
        "lunar_leap_month",
        "exact_time_value",
        "unknown_time_partial",
        "birthplace_then_handoff",
        "runtime_blocked",
        "correction_invalidation",
        "period_handoff",
        "tool_result_render",
    )
    if gate_config.get("gate_version") != GATE_VERSION:
        raise IntakeFsmGateError("FSM gate version이 구현과 다릅니다.")
    records: list[dict[str, Any]] = []
    for number in range(100):
        stratum = strata[number // 10]
        failures = _evaluate_case(number, stratum, signer)
        records.append(
            {
                "case_id": f"fsm-{number + 1:03d}",
                "stratum": stratum,
                "passed": not failures,
                "failure_codes": failures,
            }
        )
    model_report = json.loads(MODEL_GATE_REPORT.read_text(encoding="utf-8"))
    model_metric = model_report["metrics"]["required_handoff_action"]
    passed = sum(row["passed"] for row in records)
    by_stratum = {
        stratum: {
            "cases": 10,
            "passed": sum(row["passed"] for row in records if row["stratum"] == stratum),
        }
        for stratum in strata
    }
    structural = _structural_checks(signer)
    failure_counts = _failure_counts(records, structural)
    maximum_failures = gate_config["maximum_failures"]
    maximum_failures_satisfied = set(failure_counts) == set(
        maximum_failures
    ) and all(
        failure_counts[name] <= maximum_failures[name] for name in failure_counts
    )
    checks = {
        "versioned_gate_config_valid": True,
        "synthetic_cases_exactly_100": len(records) == 100,
        "synthetic_cases_passed_100": passed == 100,
        "free_text_parser_absent": structural["free_text_event_rejected"],
        "event_types_match_contract": structural["event_types_match_contract"],
        "decision_actions_match_contract": structural[
            "decision_actions_match_contract"
        ],
        "name_family_job_slots_absent": structural[
            "name_family_job_gender_slots_absent"
        ],
        "full_hmac_id_required": structural["full_hmac_id_required"],
        "replayed_period_result_rejected": structural[
            "replayed_period_result_rejected"
        ],
        "stale_tool_result_rejected": structural["stale_tool_result_rejected"],
        "state_semantic_mismatch_rejected": structural[
            "state_semantic_mismatch_rejected"
        ],
        "cached_input_fingerprint_mismatch_rejected": structural[
            "cached_input_fingerprint_mismatch_rejected"
        ],
        "malformed_state_type_rejected": structural[
            "malformed_state_type_rejected"
        ],
        "configured_maximum_failures_satisfied": maximum_failures_satisfied,
        "unknown_time_supported_without_guess": by_stratum[
            "unknown_time_partial"
        ]["passed"]
        == 10,
        "correction_invalidation_passed": by_stratum[
            "correction_invalidation"
        ]["passed"]
        == 10,
        "runtime_block_fail_closed": by_stratum["runtime_blocked"]["passed"]
        == 10,
        "tool_results_rendered_without_fake_completion": by_stratum[
            "tool_result_render"
        ]["passed"]
        == 10,
    }
    gate_passed = len(records) == 100 and passed == 100 and all(checks.values())
    return {
        "schema_version": "1.0.0",
        "gate_version": GATE_VERSION,
        "status": "passed" if gate_passed else "failed",
        "cases": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "by_stratum": by_stratum,
        "gate_checks": checks,
        "failure_counts": failure_counts,
        "maximum_failures": maximum_failures,
        "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        "records_in_public_report": False,
        "test_signer": "fixed_non_production_key_injected_no_key_material_reported",
        "inputs": {
            "intake_registry": {
                "path": str(INTAKE_REGISTRY.relative_to(REPO_ROOT)),
                "sha256": sha256_file(INTAKE_REGISTRY),
                "registry_id": intake_registry["registry_id"],
            },
            "gate_config": {
                "path": str(FSM_GATE_CONFIG.relative_to(REPO_ROOT)),
                "sha256": sha256_file(FSM_GATE_CONFIG),
            },
            "implementation_sha256": {
                path: sha256_file(REPO_ROOT / path)
                for path in (
                    "scripts/runtime/calculation/id_signer.py",
                    "scripts/runtime/intake_contracts_v1_1.py",
                    "scripts/runtime/intake_fsm.py",
                    "scripts/evaluation/saju_runtime/intake_fsm_gate.py",
                )
            },
        },
        "model_gate_comparison": {
            "path": str(MODEL_GATE_REPORT.relative_to(REPO_ROOT)),
            "sha256": sha256_file(MODEL_GATE_REPORT),
            "required_handoff_action_passed": model_metric["passed"],
            "required_handoff_action_total": model_metric["total"],
            "status": "unchanged_model_result_not_replaced_by_fsm_gate",
        },
        "app_fsm_gate_passed": gate_passed,
        "runtime_release_ready": False,
        "production_id_key_ready": False,
        "encrypted_persistence_ready": False,
        "retention_policy_ready": False,
        "app_integration_allowed": False,
        "training_promotion_allowed": False,
        "training_execution_performed": False,
    }


def _safe_output_base(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved != REPORT_ROOT.resolve(strict=False) or path.is_symlink():
        raise IntakeFsmGateError("FSM gate output base는 고정 v1.1.0 경로여야 합니다.")
    return resolved


def write_report(report: dict[str, Any], output_base: Path = REPORT_ROOT) -> Path:
    core = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(core).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build_id,
        "report_type": "saju_runtime_intake_fsm_gate",
        "aggregate_sha256": hashlib.sha256(aggregate.encode()).hexdigest(),
        "app_fsm_gate_passed": report["app_fsm_gate_passed"],
        "app_integration_allowed": report["app_integration_allowed"],
        "training_promotion_allowed": False,
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if directory.exists() or directory.is_symlink():
        if (
            directory.is_symlink()
            or (directory / "aggregate.json").read_text(encoding="utf-8") != aggregate
            or (directory / "build_manifest.json").read_text(encoding="utf-8") != manifest_text
        ):
            raise IntakeFsmGateError("같은 build ID의 FSM gate 보고서가 다릅니다.")
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        with (directory / "aggregate.json").open("x", encoding="utf-8") as stream:
            stream.write(aggregate)
        with (directory / "build_manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(manifest_text)
    except OSError as exc:
        raise IntakeFsmGateError("FSM gate 보고서를 배타적으로 쓰지 못했습니다.") from exc
    return directory


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="구조화 사주 intake FSM 100건 gate")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = evaluate()
        output = write_report(report, args.output_base)
    except (
        IntakeFsmError,
        IntakeFsmGateError,
        OSError,
        RuntimeCalculationError,
        ValueError,
    ) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "app_fsm_gate_passed": report["app_fsm_gate_passed"],
                "app_integration_allowed": report["app_integration_allowed"],
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
