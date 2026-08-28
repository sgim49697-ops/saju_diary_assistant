# phase4_triage.py - K0 700항목을 자동 위험 분류하고 우선 검토 대상을 고정한다.

from __future__ import annotations

import stat
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    load_json,
    read_jsonl,
    verify_hash_map,
    write_json_once,
    write_jsonl_once,
)
from scripts.preflight.phase4_data import verify_private_build
from scripts.preflight.phase4_k0 import verify_k0_run

TRIAGE_ARTIFACTS = (
    "triage_items.jsonl",
    "triage_priority_40.jsonl",
    "triage_summary.json",
)
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _case_risk(item: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        raise Phase4Error("K0 triage 대상 metrics가 없습니다.")
    signals: list[str] = []
    severity = "low"
    score = 0

    def add(signal: str, candidate: str, points: int) -> None:
        nonlocal severity, score
        signals.append(signal)
        score += points
        if SEVERITY_RANK[candidate] > SEVERITY_RANK[severity]:
            severity = candidate

    if metrics.get("safety_violation") is True:
        add("safety_violation", "critical", 100)
    if metrics.get("nonempty") is not True:
        add("empty_output", "critical", 100)
    if metrics.get("control_character_free") is not True:
        add("control_character", "critical", 100)
    if metrics.get("special_token_text_free") is not True:
        add("special_token_text", "critical", 100)

    contract_pass = metrics.get("automated_contract_pass")
    if contract_pass is False:
        if str(item.get("hardness", "")).startswith("hard"):
            add("hard_contract_failed", "high", 70)
        else:
            add("automated_contract_failed", "medium", 40)
    if (
        result.get("finished_with_eos") is not True
        and result.get("generated_tokens") == 512
    ):
        add("generation_limit_reached", "medium", 30)
    repetition = metrics.get("repetition_4gram_ratio")
    if isinstance(repetition, (int, float)) and repetition >= 0.35:
        add("high_repetition", "medium", 25)
    hangul_ratio = metrics.get("hangul_ratio")
    if isinstance(hangul_ratio, (int, float)) and hangul_ratio < 0.2:
        add("low_hangul_ratio", "medium", 20)
    return {
        "severity": severity,
        "risk_score": score,
        "signals": sorted(set(signals)),
    }


def _build_triage(context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    private_root: Path = context["private_root"]
    k0_root: Path = context["k0_root"]
    eval_items = [
        *read_jsonl(private_root / "eval/core_eval_200.jsonl", "Core Eval"),
        *read_jsonl(private_root / "eval/source_holdout_500.jsonl", "source holdout"),
    ]
    result_rows = read_jsonl(k0_root / "results.jsonl", "K0 results")
    result_by_case: dict[tuple[str, str], dict[str, Any]] = {}
    for row in result_rows:
        item = load_json(k0_root / row["item_path"], "K0 item")
        key = (str(item.get("eval_id")), str(item.get("case_id")))
        if key in result_by_case:
            raise Phase4Error("K0 triage case가 중복됐습니다.")
        result_by_case[key] = item

    triage_items: list[dict[str, Any]] = []
    seen_cases: set[tuple[str, str]] = set()
    for item in eval_items:
        case_risks: list[dict[str, Any]] = []
        for case in item["cases"]:
            key = (item["eval_id"], case["case_id"])
            result = result_by_case.get(key)
            if result is None:
                raise Phase4Error("K0 triage eval/result case가 일치하지 않습니다.")
            seen_cases.add(key)
            case_risks.append(
                {
                    "case_id": case["case_id"],
                    **_case_risk(item, result),
                }
            )
        severity = max(
            (value["severity"] for value in case_risks),
            key=SEVERITY_RANK.__getitem__,
        )
        triage_items.append(
            {
                "schema_version": "1.0.0",
                "eval_id": item["eval_id"],
                "split": (
                    "source_holdout"
                    if item["category"] == "source_holdout"
                    else "core_eval"
                ),
                "category": item["category"],
                "hardness": item["hardness"],
                "source_axis": item.get("source_axis"),
                "severity": severity,
                "risk_score": sum(value["risk_score"] for value in case_risks),
                "signals": sorted(
                    {signal for value in case_risks for signal in value["signals"]}
                ),
                "case_count": len(case_risks),
                "case_risks": case_risks,
                "automated_second_pass_status": (
                    "priority_review_required"
                    if severity in {"critical", "high"}
                    else "no_blocking_signal_detected"
                ),
                "human_domain_review_performed": False,
            }
        )
    if len(triage_items) != 700 or len(seen_cases) != 720:
        raise Phase4Error("K0 triage 수량이 700항목·720case와 다릅니다.")

    severity_order = context["config"]["triage"]["severity_order"]
    priority_limit = int(context["config"]["triage"]["priority_limit"])
    priority = sorted(
        triage_items,
        key=lambda value: (
            severity_order.index(value["severity"]),
            -int(value["risk_score"]),
            value["category"],
            value["eval_id"],
        ),
    )[:priority_limit]
    severity_counts = Counter(value["severity"] for value in triage_items)
    signal_counts = Counter(
        signal for value in triage_items for signal in value["signals"]
    )
    category_severity: dict[str, Counter[str]] = defaultdict(Counter)
    for value in triage_items:
        category_severity[value["category"]][value["severity"]] += 1
    summary = {
        "schema_version": "1.0.0",
        "report_type": "phase4_k0_automated_risk_triage",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "completed",
        "evaluation_items": len(triage_items),
        "generation_cases": len(seen_cases),
        "severity_counts": dict(sorted(severity_counts.items())),
        "signal_counts": dict(sorted(signal_counts.items())),
        "category_severity_counts": {
            category: dict(sorted(counts.items()))
            for category, counts in sorted(category_severity.items())
        },
        "priority_limit": priority_limit,
        "priority_items": len(priority),
        "critical_or_high_items": sum(
            severity_counts[value] for value in ("critical", "high")
        ),
        "automated_second_pass_performed": True,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "raw_samples_in_summary": False,
        "training_promotion_allowed": False,
    }
    return triage_items, priority, summary


def run_triage(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_private_build(context, repo_root)
    verify_k0_run(context, repo_root)
    root: Path = context["k0_root"]
    manifest_path = root / "triage_manifest.json"
    if manifest_path.exists():
        return {**verify_triage(context, repo_root), "mode": "reused", "writes_performed": False}
    items, priority, summary = _build_triage(context)
    write_jsonl_once(root / "triage_items.jsonl", items)
    write_jsonl_once(root / "triage_priority_40.jsonl", priority)
    write_json_once(
        root / "triage_summary.json", summary, mode=PRIVATE_FILE_MODE
    )
    manifest = {
        "schema_version": "1.0.0",
        "report_type": "phase4_k0_triage_manifest",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "artifact_sha256": artifact_hash_map(root, list(TRIAGE_ARTIFACTS)),
        "status": "completed",
        "training_promotion_allowed": False,
    }
    write_json_once(manifest_path, manifest, mode=PRIVATE_FILE_MODE)
    return {**verify_triage(context, repo_root), "mode": "built", "writes_performed": True}


def verify_triage(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_private_build(context, repo_root)
    verify_k0_run(context, repo_root)
    root: Path = context["k0_root"]
    manifest = load_json(root / "triage_manifest.json", "K0 triage manifest")
    summary = load_json(root / "triage_summary.json", "K0 triage summary")
    if (
        manifest.get("build_id") != context["build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or manifest.get("training_promotion_allowed") is not False
        or summary.get("evaluation_items") != 700
        or summary.get("generation_cases") != 720
        or summary.get("human_domain_review_performed") is not False
        or summary.get("raw_samples_in_summary") is not False
    ):
        raise Phase4Error("K0 triage identity·수량·공개 경계가 다릅니다.")
    for relative in (*TRIAGE_ARTIFACTS, "triage_manifest.json"):
        path = root / relative
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase4Error(f"K0 triage 파일 권한이 0600이 아닙니다: {relative}")
    verify_hash_map(root, manifest.get("artifact_sha256"), "K0 triage")
    items = read_jsonl(root / "triage_items.jsonl", "K0 triage items")
    priority = read_jsonl(root / "triage_priority_40.jsonl", "K0 triage priority")
    if len(items) != 700 or len(priority) != 40:
        raise Phase4Error("K0 triage item/priority 수량이 다릅니다.")
    return {
        "build_id": context["build_id"],
        "status": "verified",
        "evaluation_items": len(items),
        "priority_items": len(priority),
        "severity_counts": summary["severity_counts"],
        "human_domain_review_performed": False,
        "training_promotion_allowed": False,
    }
