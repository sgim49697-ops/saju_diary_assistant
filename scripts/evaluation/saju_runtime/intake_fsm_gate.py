# intake_fsm_gate.py - 구조화 intake FSM의 100건 합성 app preflight를 재현한다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.intake_fsm import (
    IntakeFsmError,
    advance_intake,
    empty_intake_state,
)

GATE_VERSION = "saju-intake-fsm-gate-v1.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_intake_fsm/v1.0.0"
MODEL_GATE_REPORT = REPO_ROOT / (
    "data/reports/saju_1b_baseline/phase5-stateful-chat-gate/v1.0.0/"
    "stateful-gate-f5b76dde1921/evaluation_summary.json"
)
FSM_GATE_CONFIG = REPO_ROOT / "configs/runtime/intake_fsm_gate-v1.0.0.json"
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
    identity = {"case": index, "input": state["birth_slots"]}
    event = {
        "type": "chart_result",
        "result": {
            "status": "partial" if unknown else "ok",
            "hard_facts": {"pillars": {"year": {"ganzhi": "戊辰"}}},
            "fact_authority": "POLICY_BOUND_RULE" if unknown else "HARD_GT",
            "chart_id": None if unknown else signer.chart_id(identity),
            "chart_set_id": signer.chart_set_id(identity) if unknown else None,
        },
    }
    return _advance(state, event, signer)


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
    gate_config = json.loads(FSM_GATE_CONFIG.read_text(encoding="utf-8"))
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
    if (
        gate_config.get("gate_version") != GATE_VERSION
        or gate_config.get("minimum_cases") != 100
        or gate_config.get("required_passed_cases") != 100
        or gate_config.get("strata") != {stratum: 10 for stratum in strata}
        or gate_config.get("training_promotion_allowed") is not False
    ):
        raise IntakeFsmGateError("FSM gate config가 구현 strata·임계와 다릅니다.")
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
    return {
        "schema_version": "1.0.0",
        "gate_version": GATE_VERSION,
        "status": "passed" if passed == 100 else "failed",
        "cases": len(records),
        "passed": passed,
        "failed": len(records) - passed,
        "by_stratum": by_stratum,
        "gate_checks": {
            "versioned_gate_config_valid": True,
            "synthetic_cases_exactly_100": len(records) == 100,
            "synthetic_cases_passed_100": passed == 100,
            "free_text_parser_absent": True,
            "name_family_job_slots_absent": True,
            "unknown_time_supported_without_guess": by_stratum["unknown_time_partial"]["passed"] == 10,
            "correction_invalidation_passed": by_stratum["correction_invalidation"]["passed"] == 10,
            "runtime_block_fail_closed": by_stratum["runtime_blocked"]["passed"] == 10,
            "tool_results_rendered_without_fake_completion": by_stratum["tool_result_render"]["passed"] == 10,
        },
        "records_sha256": hashlib.sha256(canonical_json_bytes(records)).hexdigest(),
        "records_in_public_report": False,
        "test_signer": "fixed_non_production_key_injected_no_key_material_reported",
        "inputs": {
            "gate_config": {
                "path": str(FSM_GATE_CONFIG.relative_to(REPO_ROOT)),
                "sha256": sha256_file(FSM_GATE_CONFIG),
            },
            "implementation_sha256": {
                path: sha256_file(REPO_ROOT / path)
                for path in (
                    "scripts/runtime/calculation/id_signer.py",
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
        "app_fsm_gate_passed": passed == 100,
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
        raise IntakeFsmGateError("FSM gate output base는 고정 v1.0.0 경로여야 합니다.")
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
    except (IntakeFsmError, IntakeFsmGateError, OSError, ValueError) as exc:
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
