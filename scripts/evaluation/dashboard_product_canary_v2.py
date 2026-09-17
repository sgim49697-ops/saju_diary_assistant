# dashboard_product_canary_v2.py - Phase 10의 CPU·합성 화면 검증만 불변 공개 집계로 발행한다.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date

from scripts.evaluation.dashboard_intent_canary_v2 import browser_identity
from scripts.evaluation.system_context_contracts import (
    REPO_ROOT,
    file_sha,
    read_json,
    safe_path,
    validate_public,
    write_new,
)
from scripts.training import dashboard_product_policy_v2 as policy
from scripts.training import phase5_dashboard_v1_19 as dashboard

PUBLIC_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-product-canary/v2.0.0"
CODE = (
    "scripts/training/phase5_dashboard_v1_19.py",
    "scripts/training/dashboard_product_policy_v2.py",
    "scripts/training/dashboard_product_session_v2.py",
    "scripts/runtime/product_dashboard_binding_v1.py",
    "scripts/training/dashboard_grounding_v4.py",
    "scripts/training/dashboard_tokenizer_v1.py",
    "scripts/runtime/chart_day_dashboard_binding.py",
    "scripts/runtime/chart_day_adapter.py",
    "scripts/runtime/chart_only_security.py",
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.19.0-product-candidate.json",
    "configs/chat_prompts/saju_product_v1.txt",
    "scripts/evaluation/dashboard_product_canary_v2.py",
    "scripts/evaluation/dashboard_product_ui_canary_v2.cjs",
    "tests/test_dashboard_product_v2.py",
    "tests/test_dashboard_product_http_v2.py",
    "tests/test_dashboard_product_canary_v2.py",
    *("scripts/training/phase5_dashboard_assets/v1.19.0/" + name for name in ("dashboard.js", "dashboard.css", "index.html", "prompt-examples.json")),
)
PARENTS = {
    "data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91/build_manifest.json": "8d7a0fae54c9af5cd4d9dc55a20364abf11d14797b3b4a83ff899c374223dbe3",
    "data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0/build-49b9aed70565/build_manifest.json": "69ec9feeee97e597d5cd4ce1bfd5887b41ecf2b04b34ee4de5f19eda90346223",
    "data/reports/saju_1b_baseline/system-context-s3/v1.0.0/build-ffd985905b51/build_manifest.json": "c1e83a8423da5e74a5f9c0eed5de0b7eeca6c851fde7893f4eca3d0122c8e5a0",
    "data/reports/saju_1b_baseline/system-context-s4/v1.1.0/build-1f851d69a91f/build_manifest.json": "61122bd326762fb9acb98389ac3fab0876bef5dcbf300612c0f3e218a9a2e29a",
}
TESTS = ("tests.test_dashboard_product_v2", "tests.test_dashboard_product_http_v2", "tests.test_dashboard_product_canary_v2")
TEST_COUNT = 56
PUBLIC_NAMES = {"aggregate.json", "build_manifest.json", "verification.json"}
GOVERNANCE = {
    "cpu_only": True, "synthetic_only": True, "new_model_generations": 0,
    "fake_generation_used": True, "real_cli_nonmodel_executed": True,
    "production_service_accessed": False, "service_changed": False,
    "training_performed": False, "sealed_blind_accessed": False,
    "runtime_release_changed": False, "production_promotion_allowed": False,
    "raw_outputs_published": False, "phase12_executed": False,
}


def policy_cases():
    from tests.test_dashboard_product_v2 import binding_fixture, chart_binding

    specs = (
        ("fact-master", "내 일간이 뭐야?", "day", "direct_fact"),
        ("fact-pillar", "일주 알려줘", "day", "direct_fact"),
        ("fact-date", "선택 날짜 알려줘", "day", "direct_fact"),
        ("general", "사주 말고 하소연을 들어줘", "day", "model_generated"),
        ("chart", "내 일간을 쉽게 설명해줘", "day", "model_generated"),
        ("day", "오늘 일진을 쉽게 설명해줘", "day", "model_generated"),
        ("mixed", "이번 주 운세를 봐줘. 팀장님께 메시지를 써줘.", "day", "model_generated"),
        ("ambiguous", "그럼 내일은 어때?", "day", "clarification"),
        ("unknown", "시주 알려줘", "partial", "clarification"),
        ("intake", "양력 1994년 4월 18일 대전 태생이야", "none", "clarification"),
        ("range", "이번 주 운세를 봐줘", "day", "blocked"),
        ("unsupported", "용신을 계산해줘", "day", "blocked"),
        ("music-not-pillar", "피아노 연주가 뭐야?", "day", "model_generated"),
        ("newspaper-not-master", "일간신문이 뭐야?", "day", "model_generated"),
        ("unsupported-strong", "내 사주가 신강한 편이야?", "day", "blocked"),
        ("concept-not-calculation", "신강과 신약의 뜻을 설명해줘", "day", "model_generated"),
    )
    rows = []
    for name, prompt, binding_type, expected in specs:
        binding = binding_fixture() if binding_type == "day" else chart_binding(partial=True) if binding_type == "partial" else None
        plan = policy.plan_response(prompt, binding, [], today=date(2026, 9, 5))
        if plan.kind != expected:
            raise ValueError("CPU 고정 응답 경로 불일치: " + name)
        rows.append({"case_id": name, "response_kind": plan.kind, "response_mode": plan.mode})
    for name, prompt, mode, paths, expected in (
        ("day-reference", "그 설명을 쉽게 해줘", "day_explanation", ["period"], "day_explanation"),
        ("new-general-task", "아까 회의 실수를 만회할 메시지 두 문장만 써줘", "chart_explanation", ["chart.hard_facts.day_master"], "general"),
    ):
        messages = [{"role": "user", "content": "이전 요청"}, {"role": "assistant", "content": "이전 답변", "response_mode": mode, "diagnostics": {"selected_paths": paths}}]
        plan = policy.plan_response(prompt, binding_fixture(), messages, today=date(2026, 9, 5))
        if plan.mode != expected or (expected == "day_explanation" and set(plan.selected_facts) != {"period"}) or (expected == "general" and plan.selected_facts):
            raise ValueError("CPU 후속 요청의 선택 사실 불일치: " + name)
        rows.append({"case_id": name, "response_kind": plan.kind, "response_mode": plan.mode})
    return rows


def identity():
    config = read_json(REPO_ROOT / dashboard.DEFAULT_CONFIG)
    dashboard.validate_config(config)
    dashboard._load_prompt_profiles(REPO_ROOT, config)
    for path, expected in PARENTS.items():
        if file_sha(REPO_ROOT / path) != expected:
            raise ValueError("보존 부모 manifest 불일치: " + path)
    return {
        "canary_id": "dashboard-product-cpu-v2.0.0", "dashboard_version": "1.19.0",
        "candidate_engine": "lora_r16", "response_policy_version": policy.POLICY_VERSION,
        "source_sha256": {path: file_sha(REPO_ROOT / path) for path in CODE},
        "parent_manifest_sha256": PARENTS, "test_modules": list(TESTS),
        "python": list(sys.version_info[:3]), "browser": browser_identity(),
        "policy_cases_sha256": policy.digest(policy_cases()),
    }


def run_checks():
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS, "-q", "-b"], cwd=REPO_ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}, capture_output=True, text=True, timeout=120, check=False)
    count = re.search(r"Ran (\d+) tests? in", result.stderr)
    if result.returncode or not count or int(count[1]) != TEST_COUNT or not re.search(r"\nOK\s*$", result.stderr):
        raise ValueError("제품 CPU 회귀 실패·누락·건너뜀")
    return int(count[1])


def run_browser():
    result = subprocess.run(["node", str(REPO_ROOT / "scripts/evaluation/dashboard_product_ui_canary_v2.cjs")], cwd=REPO_ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""}, capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError("제품 합성 화면 검사 실패")
    value = json.loads(result.stdout)
    if value.get("passed") is not True or value.get("cases_passed") != 32 or value.get("model_generations") != 0 or value.get("production_service_accessed") is not False:
        raise ValueError("제품 합성 화면 검사 수·범위 불일치")
    return value


def evaluate():
    frozen = identity()
    tests = run_checks()
    browser = run_browser()
    rows = policy_cases()
    if identity() != frozen:
        raise ValueError("제품 검증 중 source identity 변경")
    build = "build-" + policy.digest(frozen)[:12]
    aggregate = {
        "schema_version": "1.0.0", "build_id": build, "status": "passed",
        "dashboard_version": "1.19.0", "candidate_engine": "lora_r16",
        "candidate_selected_for_cpu_implementation": True, "candidate_adopted_for_production": False,
        "tests_passed": tests, "synthetic_browser_cases_passed": browser["cases_passed"],
        "policy_cases": rows, "policy_case_count": len(rows),
        "cpu_policy_response_kind_counts": dict(sorted(Counter(r["response_kind"] for r in rows).items())),
        "quality_dimensions": {"naturalness": "not_measured", "semantics": "not_measured"},
        "request_budget": {"actual_model_requests_added": 0, "previous_total": 631, "current_total": 631, "remaining": 49},
        "governance": GOVERNANCE,
    }
    manifest = {"schema_version": "1.0.0", "build_id": build, "identity": frozen, "aggregate_sha256": policy.digest(aggregate), "governance": GOVERNANCE}
    validate_public(aggregate)
    validate_public(manifest)
    return aggregate, manifest


def build_path(build):
    if not isinstance(build, str) or re.fullmatch(r"build-[0-9a-f]{12}", build) is None:
        raise ValueError("제품 canary build ID 오류")
    return safe_path(PUBLIC_ROOT / build)


def verification(public, aggregate):
    return {"schema_version": "1.0.0", "build_id": aggregate["build_id"], "status": "verified", "aggregate_file_sha256": file_sha(public / "aggregate.json"), "manifest_file_sha256": file_sha(public / "build_manifest.json"), "governance": GOVERNANCE}


def execute():
    aggregate, manifest = evaluate()
    public = build_path(aggregate["build_id"])
    public.mkdir(parents=True, exist_ok=True)
    if {p.name for p in public.iterdir()} - PUBLIC_NAMES:
        raise ValueError("제품 canary 공개 범위 위반")
    for name, value in (("aggregate.json", aggregate), ("build_manifest.json", manifest)):
        path = public / name
        if path.exists():
            if read_json(path) != value:
                raise ValueError("기존 제품 canary를 덮어쓸 수 없습니다.")
        else:
            write_new(path, value, private=False)
    result = verification(public, aggregate)
    target = public / "verification.json"
    if target.exists():
        if read_json(target) != result:
            raise ValueError("기존 제품 canary 검증이 다릅니다.")
    else:
        write_new(target, result, private=False)
    return result


def verify(build):
    public = build_path(build)
    if {p.name for p in public.iterdir()} != PUBLIC_NAMES:
        raise ValueError("제품 canary 파일 누락·추가")
    aggregate, manifest = evaluate()
    if aggregate["build_id"] != build:
        raise ValueError("제품 source identity 변경")
    result = verification(public, aggregate)
    for name, expected in (("aggregate.json", aggregate), ("build_manifest.json", manifest), ("verification.json", result)):
        if read_json(public / name) != expected:
            raise ValueError("제품 canary 재계산 불일치")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 10 CPU·합성 화면 전용 검증; GPU 생성·운영 변경 없음")
    parser.add_argument("command", choices=("validate-contract", "plan", "execute", "verify"))
    parser.add_argument("--build")
    args = parser.parse_args(argv)
    if args.command in {"validate-contract", "plan"}:
        frozen = identity()
        result = {"build_id": "build-" + policy.digest(frozen)[:12], "status": "validated" if args.command == "validate-contract" else "dry_run", "tests_planned": TEST_COUNT, "browser_cases_planned": 32, "writes_performed": False, "governance": GOVERNANCE}
    elif args.command == "execute":
        result = execute()
    else:
        result = verify(args.build)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
