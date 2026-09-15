# dashboard_intent_canary.py - v1.17의 CPU 합성 경로 검사만 실행하고 불변 공개 집계를 발행한다.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts.evaluation.dashboard_intent_canary import CODE as PARENT_CODE
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_contracts import (
    REPO_ROOT,
    file_sha,
    read_json,
    safe_path,
    validate_public,
    write_new,
)
from scripts.evaluation.system_context_rescore import PARENT_BUILD, PARENT_PINS

PUBLIC_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0"
CODE = (
    "scripts/evaluation/dashboard_intent_canary_v2.py",
    "scripts/evaluation/dashboard_intent_ui_canary.cjs",
    "scripts/training/phase5_dashboard_v1_17.py",
    "scripts/training/dashboard_grounding_v4.py",
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.17.0-intent-candidate.json",
    "tests/test_dashboard_intent_v2.py",
    "tests/test_dashboard_intent_canary_v2.py",
    *(
        "scripts/training/phase5_dashboard_assets/v1.17.0/" + name
        for name in ("dashboard.js", "index.html", "dashboard.css", "prompt-examples.json")
    ),
)
TESTS = (
    "tests.test_dashboard_grounding_v3",
    "tests.test_phase5_dashboard_v1_16",
    "tests.test_dashboard_intent_canary",
    "tests.test_dashboard_intent_v2",
    "tests.test_dashboard_intent_canary_v2",
)
TEST_COUNT = 45
PUBLIC_NAMES = {"aggregate.json", "build_manifest.json", "verification.json"}
GOVERNANCE = {
    "cpu_only": True,
    "synthetic_only": True,
    "new_model_generations": 0,
    "fake_generation_used": True,
    "model_loading_cli_executed": False,
    "sealed_blind_accessed": False,
    "service_changed": False,
    "training_performed": False,
    "production_promotion_allowed": False,
    "runtime_release_changed": False,
}


def identity():
    parent = (
        REPO_ROOT
        / "data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0"
        / PARENT_BUILD
        / "build_manifest.json"
    )
    if file_sha(parent) != PARENT_PINS["build_manifest.json"]:
        raise ValueError("부모 manifest pin 불일치")
    source = read_json(parent)["identity"]["code_sha256"]
    for relative, expected in source.items():
        if file_sha(REPO_ROOT / relative) != expected:
            raise ValueError("보존된 부모 source 불일치")
    return {
        "canary_id": "dashboard-intent-cpu-v2.0.0",
        "parent_manifest_sha256": PARENT_PINS["build_manifest.json"],
        "source_sha256": {**source, **{p: file_sha(REPO_ROOT / p) for p in (*PARENT_CODE, *CODE)}},
        "python": list(sys.version_info[:3]),
        "test_modules": list(TESTS),
        "browser": browser_identity(),
    }


def run_checks():
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", *TESTS, "-q", "-b"],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "CUDA_VISIBLE_DEVICES": "",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    counts = re.search(r"Ran (\d+) tests? in", completed.stderr)
    if (
        completed.returncode
        or not counts
        or not re.search(r"\nOK\s*$", completed.stderr)
    ):
        raise ValueError("CPU canary 테스트 실패 또는 건너뜀")
    if int(counts[1]) != TEST_COUNT:
        raise ValueError("CPU canary 동결 테스트 수 불일치")
    return int(counts[1])


def run_browser():
    result = subprocess.run(
        ["node", str(REPO_ROOT / "scripts/evaluation/dashboard_intent_ui_canary.cjs")],
        cwd=REPO_ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode:
        raise ValueError("합성 브라우저 검사 실패")
    value = json.loads(result.stdout)
    if (
        value.get("passed") is not True or value.get("cases_passed") != 6
        or value.get("model_generations") != 0
        or value.get("production_service_accessed") is not False
    ):
        raise ValueError("브라우저 검사 결과 불일치")
    return value


def browser_identity():
    package = os.environ.get("SAJU_PLAYWRIGHT_MODULE")
    executable = os.environ.get("SAJU_CHROMIUM_EXECUTABLE")
    if not package or not executable:
        raise ValueError("기존 로컬 Playwright·Chromium 경로를 명시해야 합니다.")
    return {
        "playwright_package_sha256": file_sha(Path(package) / "package.json"),
        "chromium_sha256": file_sha(Path(executable)),
    }


def evaluate():
    frozen = identity()
    tests = run_checks()
    browser = run_browser()
    if identity() != frozen:
        raise ValueError("검사 중 코드 변경")
    build = "build-" + digest(frozen)[:12]
    summary = {
        "schema_version": "2.0.0",
        "build_id": build,
        "status": "passed",
        "dashboard_version": "1.17.0",
        "intent_policy_version": "saju-request-intent-v2.0.0",
        "scorer_version": "saju-bound-chart-grounding-v4.0.0",
        "tests_passed": tests,
        "tests_failed": 0,
        "tests_skipped": 0,
        "matrix_cases_per_path": 24,
        "previous_user_context_cases": 3,
        "browser": browser,
        "paths": [
            "loopback_http",
            "direct_generation_with_fake_engine",
            "subprocess_launcher_target",
            "fresh_process_http_and_direct",
            "stored_audit_policy",
        ],
        "parent_date_grammar_unchanged": True,
        "active_request_clause_only": True,
        "history_and_binding_read_only": True,
        "parent_fact_syntax_unchanged": True,
        "parent_prompt_and_generation_unchanged": True,
        "feature_default_off": True,
        "governance": GOVERNANCE,
        "limitations": [
            "새 합성 경로 검사이며 모델 응답 품질 점수가 아니다.",
            "CLI 모델 로딩·GPU 생성·운영 브라우저·서비스 배포는 실행하지 않았다.",
            "의도 정책은 유한 규칙이며 미지원 표현의 일반적 이해를 보장하지 않는다.",
            "진단용 새 주장 검사기는 앱 사실 검사기에 적용하지 않았다.",
        ],
    }
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build,
        "identity": frozen,
        "aggregate_sha256": digest(summary),
        "governance": GOVERNANCE,
    }
    validate_public(summary)
    validate_public(manifest)
    return summary, manifest


def build_path(build):
    if not isinstance(build, str) or re.fullmatch(r"build-[0-9a-f]{12}", build) is None:
        raise ValueError("build ID 오류")
    return safe_path(PUBLIC_ROOT / build)


def verification(public, summary):
    return {
        "schema_version": "1.0.0",
        "build_id": summary["build_id"],
        "status": "verified",
        "tests_passed": summary["tests_passed"],
        "aggregate_file_sha256": file_sha(public / "aggregate.json"),
        "manifest_file_sha256": file_sha(public / "build_manifest.json"),
        "governance": GOVERNANCE,
    }


def execute():
    summary, manifest = evaluate()
    public = build_path(summary["build_id"])
    public.mkdir(parents=True, exist_ok=True)
    if {p.name for p in public.iterdir()} - PUBLIC_NAMES:
        raise ValueError("공개 범위 위반")
    for name, value in (("aggregate.json", summary), ("build_manifest.json", manifest)):
        path = public / name
        if path.exists():
            if digest(read_json(path)) != digest(value):
                raise ValueError("기존 canary 결과 불일치")
        else:
            write_new(path, value, private=False)
    result = verification(public, summary)
    path = public / "verification.json"
    if path.exists():
        if digest(read_json(path)) != digest(result):
            raise ValueError("기존 canary 검증 불일치")
    else:
        write_new(path, result, private=False)
    return result


def verify(build):
    public = build_path(build)
    if {p.name for p in public.iterdir()} != PUBLIC_NAMES:
        raise ValueError("공개 파일 누락/추가")
    summary, manifest = evaluate()
    if summary["build_id"] != build:
        raise ValueError("canary source identity 변경")
    result = verification(public, summary)
    for name, expected in (
        ("aggregate.json", summary),
        ("build_manifest.json", manifest),
        ("verification.json", result),
    ):
        if digest(read_json(public / name)) != digest(expected):
            raise ValueError("canary 재계산 불일치")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="v1.17 CPU 합성 canary; 모델 생성·서비스 변경 없음"
    )
    parser.add_argument("command", choices=("plan", "execute", "verify"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--build")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result = verify(args.build)
        elif args.command == "execute" and args.execute:
            result = execute()
        else:
            result = {
                "status": "dry_run",
                "build_id": "build-" + digest(identity())[:12],
                "artifact_writes": False,
                "tests_to_run": TEST_COUNT,
                "governance": GOVERNANCE,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            json.dumps({"status": "blocked", "error_type": type(exc).__name__}),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
