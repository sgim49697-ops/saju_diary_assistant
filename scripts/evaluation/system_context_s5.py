# system_context_s5.py - 고정 2K·보정 상태와 기존 진단을 읽어 CPU 전용 학습 가설 근거를 발행한다.

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import stat
import sys
from collections import Counter, defaultdict
from pathlib import Path

from scripts.data.mix2k_v4_contracts import normalize_answer
from scripts.evaluation.system_context_contracts import (
    REPO_ROOT,
    file_sha,
    read_json,
    safe_path,
    validate_public,
    write_new,
)
from scripts.training import dashboard_product_policy_v2 as policy

CONFIG = REPO_ROOT / "configs/model_versions/saju_1b_baseline/system-context-s5-v1.0.0.json"
CONFIG_SHA = "a7a65e20127ee6455e82b4cdd22af1dc362f4ea898b052f7c9def9bf08f7d979"
PUBLIC_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s5/v1.0.0"
PRIVATE_ROOT = REPO_ROOT / "private/system-context-s5/v1.0.0"
PUBLIC_NAMES = {"aggregate.json", "build_manifest.json", "verification.json"}
CODE = (
    "scripts/evaluation/system_context_s5.py", "tests/test_system_context_s5.py",
    "scripts/data/mix2k_v4_finalize.py", "scripts/data/mix2k_v4_contracts.py",
    "scripts/data/mix2k_v4_teachers.py", "scripts/data/mix2k_v4_reviewed_repair.py",
    "scripts/training/dashboard_tokenizer_v1.py", "scripts/training/dashboard_product_policy_v2.py",
    "scripts/evaluation/system_context_contracts.py",
)
BEHAVIOR_AXES = {
    "bound_general": ("general_korean_empathy",),
    "topic_switch": ("general_korean_empathy", "followup_explain_grounding"),
    "short_format": ("hard_fact_short_qa",),
    "false_premise": ("structured_fact_schema_literacy", "hard_fact_short_qa"),
    "correction_history": ("intake_state_correction",),
    "fact_selection": ("structured_fact_schema_literacy", "chart_day_today_flow", "followup_explain_grounding"),
    "uncertainty": ("uncertainty_blocked_boundary",),
}
REQUEST_MARKERS = {
    "two_sentences": r"두\s*문장|2\s*문장",
    "one_word": r"한\s*단어|1\s*단어",
    "domain_opt_out": r"(?:사주|운세|원국).{0,12}(?:말고|빼고|하지\s*말)",
    "correction": r"정정|수정|아니라|잘못",
    "false_premise_check": r"맞(?:아|나요|는지)|팩트\s*체크",
    "reference": r"아까|방금|그\s*(?:답변|설명|말)|다시",
}
DRAFT_ERROR_CLASSES = {
    "MINIMUM_LENGTH": "최소 줄·문장 계약",
    "INTERNAL_TERMS": "내부 계약 용어",
    "EVENT_PREDICTION": "확정적 사건 예측",
    "CLAIM_GRAMMAR": "구조 사실 claim 오류",
    "MISSING_FACT_PATHS": "used_fact_paths에",
    "MISSING_FACT_VALUES": "used_fact_values에",
    "DUPLICATE_PROVENANCE": "provenance에 중복값",
    "DRAFT_IDENTITY": "identity·self-check",
}


def unique_object(pairs):
    result = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("입력 JSON 중복 key")
        result[key] = item
    return result


def checked_bytes(path, expected, *, max_bytes=128 * 1024 * 1024):
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ValueError("S5 입력 크기·파일 형식 위반")
    data = path.read_bytes()
    if len(data) > max_bytes or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("S5 입력 해시 불일치")
    return data


def decode(data):
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("비유한 JSON")))


def validate_contract(repair_root):
    config = decode(checked_bytes(CONFIG, CONFIG_SHA))
    repair_root = safe_path(Path(repair_root))
    external = config["external_repair_state"]
    if repair_root.name != external["target_id"]:
        raise ValueError("S5 보정 target 경로가 다릅니다.")
    paths = {}
    for name, pin in config["input_pins"].items():
        relative = Path(pin["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("S5 입력 상대 경로 계약 위반")
        path = safe_path(REPO_ROOT / relative)
        if path.stat().st_size != pin["bytes"] or file_sha(path) != pin["sha256"]:
            raise ValueError("S5 고정 입력 불일치: " + name)
        paths[name] = path
    if len({(p.stat().st_dev, p.stat().st_ino) for p in paths.values()}) != len(paths):
        raise ValueError("S5 입력 alias 중복")
    repair_path = repair_root / external["filename"]
    checked_bytes(repair_path, external["sha256"])
    return config, paths, repair_path


def load_inputs(config, paths, repair_path):
    result = {}
    for name, path in paths.items():
        if name.startswith("tokenizer_") or name == "adapter_weights":
            continue
        data = checked_bytes(path, config["input_pins"][name]["sha256"])
        result[name] = [decode(line) for line in data.splitlines() if line.strip()] if path.suffix == ".jsonl" else decode(data)
    result["repair_state"] = decode(checked_bytes(repair_path, config["external_repair_state"]["sha256"]))
    return result


def by_id(rows):
    if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in rows):
        raise ValueError("S5 행·ID 형식 위반")
    result = {r["id"]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError("S5 중복 행 ID")
    return result


def validate_lineage(data, expected_rows=2000, expected_dev=200):
    rows, specs, candidates = (by_id(data[k]) for k in ("training_rows", "specs", "candidates"))
    if len(rows) != expected_rows or rows.keys() != specs.keys() or rows.keys() != candidates.keys() or len(data["development"]) != expected_dev:
        raise ValueError("S5 행 수·계열 join 불일치")
    for key, row in rows.items():
        spec, candidate = specs[key], candidates[key]
        if any(x.get("restricted_local_only") is not False for x in (row, spec, candidate)):
            raise ValueError("제한 데이터는 S5 입력으로 허용하지 않습니다.")
        if (row["messages"] != [*spec["prompt"], {"role": "assistant", "content": candidate["assistant"]}]
                or candidate["prompt"] != spec["prompt"] or row["task_axis"] != spec["task_axis"]
                or row["task_axis"] != candidate["task_axis"] or row["assistant_only_loss"] is not True
                or row["runtime_snapshot_sha256"] != candidate["runtime_snapshot_sha256"]):
            raise ValueError("학습 행과 승인 spec·teacher 대조 불일치")
    if any(r.get("training_eligible") is not False for r in data["development"]):
        raise ValueError("개발 split의 비학습 계약 불일치")
    return rows, specs, candidates


def validate_graph(data, config):
    pins = config["input_pins"]
    manifest, run = data["data_manifest"], data["run_manifest"]
    if (manifest["build_id"] != "build-54836f556b4f" or manifest["rows"] != 2000
            or manifest["sealed_blind_accessed"] is not False or manifest["truncation"] is not False
            or manifest["full_runtime_snapshot_used"] is not True or manifest["assistant_only_loss"] is not True
            or run["data_build_id"] != manifest["build_id"] or run["identity"]["data_build_sha256"] != manifest["build_sha256"]
            or run["identity"]["config_sha256"] != pins["training_config"]["sha256"]
            or run["completed"] is not True or run["rank"] != 16 or run["rows"] != 2000
            or run["max_length"] != 2048 or run["num_train_epochs"] != 1):
        raise ValueError("실제 R16 학습과 입력 manifest 계보 불일치")
    for artifact, name in (("training/train_2000.jsonl", "training_rows"), ("reports/token_audit_2000.jsonl", "token_rows"), ("reports/token_audit_summary.json", "token_summary")):
        if manifest["artifact_sha256"][artifact] != pins[name]["sha256"]:
            raise ValueError("학습 artifact 계보 불일치")
    for key, name in (("teacher_candidate_sha256", "candidates"), ("teacher_manifest_sha256", "teacher_manifest"), ("config_sha256", "data_config")):
        if manifest["identity"][key] != pins[name]["sha256"]:
            raise ValueError("승인 teacher 계보 불일치")
    if manifest["teacher_pipeline_state_sha256"] != pins["teacher_state"]["sha256"]:
        raise ValueError("승인 teacher state 불일치")
    repair = data["repair_state"]
    if repair["target_id"] != config["external_repair_state"]["target_id"] or repair["identity"]["parent_train_sha256"] != pins["training_rows"]["sha256"]:
        raise ValueError("400건 보정의 부모 불일치")
    contract = data["repair_config"]["repair"]
    if (contract["rows"], contract["inherited_assistant_rows"], contract["regenerated_assistant_rows"]) != (2000, 1600, 400):
        raise ValueError("400건 교체 계약 불일치")


def analyze_data(data):
    rows, specs, candidates = validate_lineage(data, len(data["training_rows"]), len(data["development"]))
    axes = defaultdict(Counter)
    systems, exact_answers, normalized_answers = Counter(), Counter(), Counter()
    drafter, reviewer, review_mode, contract_lines = Counter(), Counter(), Counter(), Counter()
    prompts, parents, chart_keys = set(), set(), set()
    markers = Counter({key: 0 for key in REQUEST_MARKERS})
    details = []
    for key in sorted(rows):
        row, spec, candidate = rows[key], specs[key], candidates[key]
        answer = row["messages"][-1]["content"]
        bound = bool(spec["runtime_binding"])
        group = axes[row["task_axis"]]
        group.update(rows=1, bound=int(bound), multiturn=int(spec["multiturn"]), final_three_or_more_lines=int(sum(bool(line.strip()) for line in answer.splitlines()) >= 3))
        group["inherited_assistant_turns"] += sum(m["role"] == "assistant" for m in row["messages"][:-1])
        exact_answers[answer] += 1
        normalized_answers[normalize_answer(answer)] += 1
        systems.update(hashlib.sha256(m["content"].encode()).hexdigest() for m in row["messages"] if m["role"] == "system")
        last_user = next(m["content"] for m in reversed(spec["prompt"]) if m["role"] == "user")
        prompts.add(normalize_answer(last_user))
        for name, pattern in REQUEST_MARKERS.items():
            markers[name] += bool(re.search(pattern, last_user))
        parent = [m for m in spec["prompt"][:-1] if m["role"] != "system"]
        if parent:
            parents.add(policy.digest(parent))
        chart = (spec["runtime_binding"] or {}).get("value", {}).get("chart")
        if chart:
            chart_keys.add(policy.digest(chart))
        teacher = candidate["teacher"]
        drafter[teacher["actual_drafter"]] += 1
        reviewer[teacher["actual_reviewer"]] += 1
        review_mode[teacher["review_mode"]] += 1
        contract_lines[str(spec["response_contract"]["minimum_nonempty_lines"])] += 1
        details.append({"record_id": key, "task_axis": row["task_axis"], "template_family": spec["template_family"], "bound": bound, "final_nonempty_lines": sum(bool(line.strip()) for line in answer.splitlines()), "system_prompt_sha256": [hashlib.sha256(m["content"].encode()).hexdigest() for m in row["messages"] if m["role"] == "system"]})
    overlap = Counter()
    for row in data["development"]:
        users = [m["content"] for m in row["messages"] if m["role"] == "user"]
        overlap["normalized_last_user_overlap_rows"] += bool(users and normalize_answer(users[-1]) in prompts)
        parent = [m for m in row["messages"][:-1] if m["role"] != "system"]
        overlap["exact_parent_dialogue_overlap_rows"] += bool(parent and policy.digest(parent) in parents)
        chart = (row.get("runtime_binding") or {}).get("value", {}).get("chart")
        overlap["chart_fingerprint_overlap_rows"] += bool(chart and policy.digest(chart) in chart_keys)
    failures, rewrites, review_codes, review_decisions = Counter(), Counter(), Counter(), Counter()
    for record in data["teacher_state"]["records"].values():
        rewrites[str(record["rewrites_used"])] += 1
        for attempt in record["draft_attempts"]:
            if attempt.get("deterministic_pass") is False:
                error = str(attempt.get("deterministic_error", ""))
                found = re.search(r"\b[A-Z][A-Z0-9_]{3,}\b", error)
                category = next((name for name, text in DRAFT_ERROR_CLASSES.items() if text in error), None)
                failures[category or (found[0] if found else "UNCLASSIFIED")] += 1
        for attempt in record.get("review_attempts", []):
            review = attempt.get("review")
            if not isinstance(review, dict):
                continue
            codes = review.get("failure_codes", [])
            decision = review.get("decision", "UNCLASSIFIED")
            if not isinstance(codes, list) or any(not isinstance(c, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", c) for c in [decision, *codes]):
                raise ValueError("판정 코드의 공개 집계 형식 위반")
            review_codes.update(codes)
            review_decisions[decision] += 1
    repair_records = data["repair_state"]["records"]
    repair_counts = Counter(r["status"] for r in repair_records.values())
    if len(repair_records) != 400 or sum(repair_counts.values()) != 400:
        raise ValueError("보정 상태 분모 불일치")
    report = {
        "rows": len(rows), "axes": dict(axes), "final_three_or_more_nonempty_lines": sum(r["final_nonempty_lines"] >= 3 for r in details),
        "minimum_nonempty_line_contract_counts": dict(contract_lines),
        "distinct_full_system_texts": len(systems),
        "full_system_text_multiplicity_histogram": dict(Counter(str(n) for n in systems.values())),
        "prompt_match_scope": "entire_system_text_including_embedded_facts_not_template_equivalence",
        "candidate_base_prompt_sha256": policy.PROMPT_PIN,
        "candidate_base_prompt_exact_matches": systems[policy.PROMPT_PIN],
        "lexical_last_request_markers": dict(markers), "lexical_zero_proves_semantic_absence": False,
        "teacher": {"actual_drafters": dict(drafter), "actual_reviewers": dict(reviewer), "review_modes": dict(review_mode), "deterministic_failed_attempts_by_code": dict(failures), "automatic_review_failure_codes": dict(review_codes), "automatic_review_decisions": dict(review_decisions), "rewrite_count_histogram": dict(rewrites), "semantic_bias_causality": "hypothesis_not_proven", "self_reported_fact_paths_are_semantic_proof": False},
        "duplicates": {"exact_duplicate_answer_excess": sum(n - 1 for n in exact_answers.values()), "normalized_answer_multiplicity_maximum": max(normalized_answers.values()), "template_families": len({s["template_family"] for s in specs.values()}), "conversation_ids": len({s["conversation_id"] for s in specs.values()}), "semantic_similarity": "not_measured"},
        "development_overlap": {"rows": len(data["development"]), **dict(overlap), "development_template_family_metadata": "not_recorded", "semantic_family_overlap": "not_measured", "targets_used_for_training": False},
        "repair": {"rows": 400, "status_counts": dict(repair_counts), "provider_calls_preserved": data["repair_state"]["provider_calls"], "axes": dict(Counter(r["task_axis"] for r in repair_records.values())), "inherited_rows_if_finalized": 1600, "replaced_rows_if_finalized": 400, "total_rows_if_finalized": 2000, "applied_to_current_r16": False, "proposal": "hold_pending_phase12", "automatic_resume_allowed": False},
    }
    validate_public(report)
    return report, details


def check_token_rows(stored, recomputed):
    expected, actual = by_id(stored), by_id(recomputed)
    if expected.keys() != actual.keys() or expected != actual:
        raise ValueError("전수 token/loss mask 재계산 불일치")
    for row in actual.values():
        if (row["truncated"] is not False or row["assistant_mask_nonzero"] is not True
                or row["final_eos_supervised"] is not True or row["user_system_loss_leakage_tokens"] != 0
                or row["supervised_assistant_tokens"] <= 0):
            raise ValueError("assistant-only loss·EOS·잘림 계약 위반")


def audit_tokens(data, paths):
    from transformers import AutoTokenizer

    from scripts.data.mix2k_v4_finalize import _tokenize_row
    from scripts.training.dashboard_tokenizer_v1 import (
        backend_sha256,
        load_canonical_tokenizer,
    )

    root = paths["tokenizer_tokenizer.json"].parent
    training = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False, fix_mistral_regex=True)
    serving = load_canonical_tokenizer(root)
    if training.chat_template != root.joinpath("chat_template.jinja").read_text():
        raise ValueError("학습 template 실효 원문 불일치")
    recomputed, equal_ids, equal_masks = [], 0, 0
    maximum_delta = 0
    for row in data["training_rows"]:
        candidate = {"id": row["id"], "task_axis": row["task_axis"], "prompt": row["messages"][:-1], "assistant": row["messages"][-1]["content"]}
        recomputed.append(_tokenize_row(training, candidate))
        options = {"tokenize": True, "return_dict": True, "return_assistant_tokens_mask": True, "add_generation_prompt": False}
        a = training.apply_chat_template(row["messages"], **options)
        b = serving.apply_chat_template(row["messages"], **options)
        equal_ids += a["input_ids"] == b["input_ids"]
        equal_masks += a["assistant_masks"] == b["assistant_masks"]
        maximum_delta = max(maximum_delta, abs(len(a["input_ids"]) - len(b["input_ids"])))
    check_token_rows(data["token_rows"], recomputed)
    return {"rows_recomputed": len(recomputed), "stored_audit_exact_matches": len(recomputed), "training_backend_sha256": backend_sha256(training), "serving_backend_sha256": backend_sha256(serving), "same_serialized_dialogue_token_ids_equal_rows": equal_ids, "same_serialized_dialogue_masks_equal_rows": equal_masks, "maximum_token_count_delta": maximum_delta, "maximum_rendered_tokens": max(r["rendered_tokens"] for r in recomputed), "maximum_prompt_tokens": max(r["prompt_tokens"] for r in recomputed), "maximum_supervised_tokens": max(r["supervised_assistant_tokens"] for r in recomputed), "training_length": data["run_manifest"]["max_length"], "truncated_rows": 0, "loss_leakage_rows": 0, "unsupervised_final_eos_rows": 0, "candidate_information_selection_matches_old_full_snapshot": False, "weights_loaded": False}


def training_proposal(data):
    old = data["training_config"]
    return {
        "execution_authorized": False, "status": "conditional_specification_only",
        "source_model_repository": old["base_model"]["repository"], "source_model_revision": old["base_model"]["revision"],
        "method": "fresh_lora_from_pinned_k0_not_r16_continuation", "rank": 16, "lora_alpha": 32,
        "training": {k: old["training"][k] for k in ("learning_rate", "num_train_epochs", "effective_batch_size", "assistant_only_loss", "packing", "seed", "save_steps")},
        "provisional_rows": 2000, "row_count_rule": "replacement_not_addition_confirm_with_new_manifest",
        "preferred_max_length": 2048, "length_rule": "new_full_token_mask_audit_no_truncation_before_new_training_contract",
        "split_rule": "family_separated_train_and_development_fresh_post_training_confirmation",
        "phase12_consumed_questions_reusable": False, "training_budget": "one_r16_one_epoch_separate_execution_decision_required",
        "post_training_evaluation_budget": "separately_register_before_model_calls",
        "resume_rule": "same_input_config_code_checkpoint_identity_only",
        "stop_conditions": ["nonfinite_loss_or_gradient", "input_or_mask_or_split_mismatch", "resource_gate_violation"],
        "decision_rule": "only_after_phase12_normal_input_residual_model_errors_and_justified_data_change",
        "skip_rule": "skip_training_if_application_or_instructions_resolve_target_errors",
    }


def identity(config, paths, repair_path):
    # 재검증도 전 입력을 읽는다. 다른 세션의 가변 state를 과거 hash로 취급하지 않는다.
    for name, path in paths.items():
        if file_sha(path) != config["input_pins"][name]["sha256"]:
            raise ValueError("S5 처리 중 입력 변경: " + name)
    if file_sha(repair_path) != config["external_repair_state"]["sha256"]:
        raise ValueError("다른 세션 보정 state 변경: 재분석 필요")
    if file_sha(CONFIG) != CONFIG_SHA:
        raise ValueError("S5 실행 중 계약 변경")
    source_hashes = {p: file_sha(REPO_ROOT / p) for p in CODE}
    candidate = decode(checked_bytes(paths["candidate_manifest"], config["input_pins"]["candidate_manifest"]["sha256"]))
    for relative, expected in candidate["identity"]["source_sha256"].items():
        if Path(relative).is_absolute() or ".." in Path(relative).parts or file_sha(REPO_ROOT / relative) != expected:
            raise ValueError("S5의 CPU 제품 후보 근거 변경")
        source_hashes[relative] = expected
    return {"analysis_id": config["analysis_id"], "config_sha256": file_sha(CONFIG), "source_sha256": source_hashes, "input_sha256": {k: v["sha256"] for k, v in config["input_pins"].items()}, "repair_state_sha256": config["external_repair_state"]["sha256"], "python": list(sys.version_info[:3]), "packages": {name: importlib.metadata.version(name) for name in ("transformers", "tokenizers")}}


def evaluate(repair_root):
    config, paths, repair_path = validate_contract(repair_root)
    frozen = identity(config, paths, repair_path)
    data = load_inputs(config, paths, repair_path)
    validate_lineage(data, config["expected_rows"], config["expected_development_rows"])
    validate_graph(data, config)
    distribution, details = analyze_data(data)
    prior = data["token_summary"]["candidate_validation"]
    for field in ("actual_drafters", "actual_reviewers", "review_modes"):
        if distribution["teacher"][field] != prior[field]:
            raise ValueError("승인 teacher 집계와 재집계 불일치")
    token_audit = audit_tokens(data, paths)
    hypotheses = {axis: {"related_training_axes": list(axes), "related_rows_not_semantic_coverage": sum(distribution["axes"][a]["rows"] for a in axes), "candidate_live_result": "not_measured", "falsification": "phase12_target_behavior_succeeds_without_data_change"} for axis, axes in BEHAVIOR_AXES.items()}
    if identity(config, paths, repair_path) != frozen:
        raise ValueError("S5 실행 중 코드·입력 변경")
    private = {"schema_version": "1.0.0", "row_evidence": details}
    build = "build-" + policy.digest(frozen)[:12]
    diagnostic = {stage: {**{k: data[stage][k] for k in ("build_id", "requests", "new_generations", "candidate_selected", "quality_dimensions")}, "all_strata_summaries": [row for row in data[stage]["summaries"] if row["stratum"] == "all"]} for stage in ("s3", "s4")}
    aggregate = {"schema_version": "1.0.0", "build_id": build, "status": "completed_analysis_only", "data_distribution": distribution, "token_audit": token_audit, "behavior_hypotheses": hypotheses, "conditional_training_specification": training_proposal(data), "diagnostic_evidence": diagnostic, "candidate_canary": {k: data["candidate_canary"][k] for k in ("build_id", "tests_passed", "synthetic_browser_cases_passed", "quality_dimensions")}, "decision": "hold_repair_and_training_until_phase12", "request_budget": {"actual_model_requests_added": 0, "total": 631, "remaining": 49}, "governance": config["governance"]}
    aggregate["current_training"] = {k: data["run_manifest"][k] for k in ("run_id", "data_build_id", "rows", "rank", "max_length", "num_train_epochs", "completed", "adapter_only", "base_weights_unchanged", "full_fine_tuning_performed", "production_promotion_allowed")}
    aggregate["source_scope"] = "approved_synthetic_2k_and_existing_development_no_restricted_or_sealed_payload"
    manifest = {"schema_version": "1.0.0", "build_id": build, "identity": frozen, "aggregate_sha256": policy.digest(aggregate), "private_evidence_sha256": policy.digest(private), "governance": config["governance"]}
    validate_public(aggregate)
    validate_public(manifest)
    return aggregate, manifest, private


def build_path(root, build):
    if not isinstance(build, str) or re.fullmatch(r"build-[0-9a-f]{12}", build) is None:
        raise ValueError("S5 build ID 오류")
    return safe_path(root / build)


def put(path, value, *, private):
    if path.exists():
        if read_json(path, private=private) != value:
            raise ValueError("S5 기존 산출물 덮어쓰기 금지")
    else:
        write_new(path, value, private=private)


def publish(aggregate, manifest, private):
    public = build_path(PUBLIC_ROOT, aggregate["build_id"])
    target = build_path(PRIVATE_ROOT, aggregate["build_id"]) / "row_evidence.json"
    validate_public(aggregate)
    validate_public(manifest)
    public.mkdir(parents=True, exist_ok=True)
    if {p.name for p in public.iterdir()} - PUBLIC_NAMES:
        raise ValueError("S5 공개 파일 범위 위반")
    put(target, private, private=True)
    put(public / "aggregate.json", aggregate, private=False)
    put(public / "build_manifest.json", manifest, private=False)
    result = {"schema_version": "1.0.0", "build_id": aggregate["build_id"], "status": "verified", "aggregate_file_sha256": file_sha(public / "aggregate.json"), "manifest_file_sha256": file_sha(public / "build_manifest.json"), "governance": aggregate["governance"]}
    put(public / "verification.json", result, private=False)
    return result


def verify(build, repair_root):
    public = build_path(PUBLIC_ROOT, build)
    if {p.name for p in public.iterdir()} != PUBLIC_NAMES:
        raise ValueError("S5 공개 파일 누락·혼입")
    aggregate, manifest, private = evaluate(repair_root)
    if aggregate["build_id"] != build:
        raise ValueError("S5 source identity 변경")
    for name, expected in (("aggregate.json", aggregate), ("build_manifest.json", manifest)):
        if read_json(public / name) != expected:
            raise ValueError("S5 공개 재집계 불일치")
    private_root = build_path(PRIVATE_ROOT, build)
    if {p.name for p in private_root.iterdir()} != {"row_evidence.json"}:
        raise ValueError("S5 비공개 근거 파일 누락·혼입")
    evidence = read_json(private_root / "row_evidence.json", private=True)
    if evidence != private:
        raise ValueError("S5 비공개 행 근거 불일치")
    result = {"schema_version": "1.0.0", "build_id": build, "status": "verified", "aggregate_file_sha256": file_sha(public / "aggregate.json"), "manifest_file_sha256": file_sha(public / "build_manifest.json"), "governance": aggregate["governance"]}
    if read_json(public / "verification.json") != result:
        raise ValueError("S5 verification 불일치")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="S5 CPU 전용: 기존 데이터·진단 대조, 생성·학습·배포 없음")
    parser.add_argument("command", choices=("validate-contract", "plan", "execute", "verify"))
    parser.add_argument("--repair-root", type=Path, required=True)
    parser.add_argument("--build")
    args = parser.parse_args(argv)
    if args.command in {"validate-contract", "plan"}:
        config, paths, repair = validate_contract(args.repair_root)
        frozen = identity(config, paths, repair)
        result = {"status": "validated" if args.command == "validate-contract" else "dry_run", "build_id": "build-" + policy.digest(frozen)[:12], "writes_performed": False, "rows_planned": config["expected_rows"], "governance": config["governance"]}
    elif args.command == "execute":
        result = publish(*evaluate(args.repair_root))
    else:
        result = verify(args.build, args.repair_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
