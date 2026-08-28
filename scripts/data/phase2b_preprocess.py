# phase2b_preprocess.py - MIX20K 전용 24K 전처리 staging CLI를 제공한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.errors import Phase1Error, Phase2AuditError
from scripts.data.preprocess_tools import (
    execute_staging_build,
    record_owner_risk_acceptance,
    staging_plan,
    verify_staging,
)

DEFAULT_CONFIG = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/preprocessing-staging-v0.1.0.json"
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B MIX20K용 24K 전처리 staging 도구"
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="staging 설정 JSON 경로"
    )
    parser.add_argument(
        "--build", help="현재 입력으로 계산한 build-<12자리-hash>와 일치해야 한다."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan", help="입력·수량·build identity만 검증한다.")
    build = commands.add_parser("build", help="24K staging 후보와 검수 보고서를 만든다.")
    build.add_argument(
        "--execute", action="store_true", help="실제 산출물을 생성한다. 생략 시 plan만 출력한다."
    )
    commands.add_parser("verify", help="기존 staging build 전체를 재검증한다.")
    approve = commands.add_parser(
        "approve", help="사용자 지시로 300건을 일괄 위험 수용해 Phase 4 입력을 승인한다."
    )
    approve.add_argument(
        "--confirm-owner-risk-acceptance",
        action="store_true",
        help="항목별 전문 검수 없이 자동 검사 결과의 위험을 수용했음을 확인한다.",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    plan = staging_plan(REPO_ROOT, config_path)
    if arguments.build and arguments.build != plan["build_id"]:
        raise Phase2AuditError(
            f"요청한 --build가 현재 입력 fingerprint와 다릅니다: {arguments.build}"
        )
    if arguments.command == "plan":
        return plan
    if arguments.command == "build":
        if not arguments.execute:
            return {**plan, "mode": "build_dry_run"}
        return execute_staging_build(REPO_ROOT, config_path)
    if arguments.command == "verify":
        if not arguments.build:
            raise Phase2AuditError("verify에는 --build가 필요합니다.")
        return verify_staging(REPO_ROOT, config_path, arguments.build)
    if arguments.command == "approve":
        if not arguments.build:
            raise Phase2AuditError("approve에는 --build가 필요합니다.")
        return record_owner_risk_acceptance(
            REPO_ROOT,
            config_path,
            arguments.build,
            confirm_owner_risk_acceptance=arguments.confirm_owner_risk_acceptance,
        )
    raise Phase2AuditError(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        _print_json(run(arguments))
        return 0
    except (Phase1Error, Phase2AuditError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
