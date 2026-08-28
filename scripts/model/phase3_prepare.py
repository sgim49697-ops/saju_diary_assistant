# phase3_prepare.py - Phase 3 모델 다운로드·환경 smoke·보고서 명령을 제공한다.

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

from scripts.model.errors import Phase3Error
from scripts.model.phase3_tools import (
    download_model,
    load_config,
    run_smoke,
    validate_contract,
    verify_report,
    verify_snapshot,
)

DEFAULT_CONFIG = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/model-preparation-v1.0.0.json"
)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B baseline Phase 3 모델·환경 준비 도구"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Phase 3 모델·환경 계약 JSON",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract", help="고정 환경·모델 계약을 검증한다.")

    download = commands.add_parser(
        "download-model", help="고정 revision snapshot을 계획하거나 다운로드한다."
    )
    mode = download.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="다운로드 계획만 확인한다(기본값).")
    mode.add_argument("--execute", action="store_true", help="검증 후 snapshot을 실제 저장한다.")

    commands.add_parser("verify-snapshot", help="모델 payload·remote code hash를 재검증한다.")
    smoke = commands.add_parser("smoke", help="GPU·BF16·bitsandbytes·모델 load를 검증한다.")
    smoke.add_argument(
        "--write-report",
        action="store_true",
        help="불변 build 경로에 공개 가능한 보고서를 기록한다.",
    )
    report = commands.add_parser("verify-report", help="기존 Phase 3 보고서를 재검증한다.")
    report.add_argument("--report", type=Path, help="검증할 verification_report.json")
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    config = load_config(config_path)
    if arguments.command == "validate-contract":
        return validate_contract(config, REPO_ROOT)
    if arguments.command == "download-model":
        return download_model(config, REPO_ROOT, execute=bool(arguments.execute))
    if arguments.command == "verify-snapshot":
        return verify_snapshot(config, REPO_ROOT)
    if arguments.command == "smoke":
        return run_smoke(config, REPO_ROOT, write_report=arguments.write_report)
    if arguments.command == "verify-report":
        return verify_report(config, REPO_ROOT, arguments.report)
    raise Phase3Error(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        _print_json(run(arguments))
        return 0
    except Phase3Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
