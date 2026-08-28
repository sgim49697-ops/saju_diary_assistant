# phase1_sources.py - 정본 설정에 따라 Phase 1 수집·inventory·검증 명령을 제공한다.

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

from scripts.data.source_tools import (
    Phase1Error,
    download_aihub,
    download_hf_sources,
    inventory_all,
    load_config,
    migrate_nemotron,
    plan_aihub_download,
    plan_hf_downloads,
    validate_config,
    verify_sources,
)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _add_execution_mode(parser: argparse.ArgumentParser) -> None:
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="요청 계획만 확인한다(기본값).")
    mode.add_argument("--execute", action="store_true", help="실제 다운로드를 수행한다.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B baseline Phase 1 원천 수집 도구"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/data_sources.v1.1.json",
        help="정본 원천 설정 JSON 경로",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate-contract", help="원천·혼합·제외 계약을 검증한다.")
    subparsers.add_parser("migrate-nemotron", help="기존 shard를 raw 정본 경로로 이동한다.")

    hf_parser = subparsers.add_parser("download-hf", help="허용된 HF 원천만 수집한다.")
    hf_parser.add_argument(
        "--source",
        action="append",
        choices=("nemotron_saju", "bazi_sft", "yeji_bazi_rules"),
        help=(
            "대상 원천. 생략하면 소용량 공개 원천 두 개만 처리하며 "
            "Nemotron 전체 수집은 명시해야 한다."
        ),
    )
    _add_execution_mode(hf_parser)

    aihub_parser = subparsers.add_parser(
        "download-aihub", help="AI Hub #86 고정 file key만 안전하게 수집한다."
    )
    _add_execution_mode(aihub_parser)

    subparsers.add_parser("inventory", help="원문을 노출하지 않는 집계 inventory를 생성한다.")
    verify_parser = subparsers.add_parser("verify", help="manifest·해시·allowlist를 재검증한다.")
    verify_parser.add_argument(
        "--allow-missing-aihub",
        action="store_true",
        help="AI Hub #86 미수집 상태를 명시적 차단으로 보고하고 공개 원천만 검증한다.",
    )
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    config = load_config(config_path)
    repo_root = REPO_ROOT
    validate_config(config, repo_root)

    if arguments.command == "validate-contract":
        return validate_config(config, repo_root)
    if arguments.command == "migrate-nemotron":
        return migrate_nemotron(config, repo_root)
    if arguments.command == "download-hf":
        sources = arguments.source or ["bazi_sft", "yeji_bazi_rules"]
        if arguments.execute:
            return download_hf_sources(config, repo_root, sources)
        return plan_hf_downloads(config, sources)
    if arguments.command == "download-aihub":
        if arguments.execute:
            return download_aihub(config, repo_root)
        return plan_aihub_download(config)
    if arguments.command == "inventory":
        return inventory_all(config, repo_root)
    if arguments.command == "verify":
        return verify_sources(config, repo_root, arguments.allow_missing_aihub)
    raise Phase1Error(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        _print_json(run(arguments))
        return 0
    except Phase1Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
