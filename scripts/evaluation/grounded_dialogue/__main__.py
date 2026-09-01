# __main__.py - grounded dialogue 계약 검증·계획·실행·검증 CLI 진입점이다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .contracts import (
    DEFAULT_CONFIG,
    REPO_ROOT,
    load_json,
    prepare_context,
    validate_contract,
)
from .reporting import verify
from .runner import execute, plan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="계산기 연결 grounded dialogue 진단")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("execute")
    run.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                load_json(config_path, "grounded dialogue config"), REPO_ROOT
            )
        elif args.command == "verify":
            context = prepare_context(
                REPO_ROOT, config_path, require_local_artifacts=False
            )
            result = verify(context)
        else:
            context = prepare_context(
                REPO_ROOT, config_path, require_local_artifacts=True
            )
            if args.command == "plan":
                result = plan(context, REPO_ROOT)
            elif args.execute:
                result = execute(context, REPO_ROOT)
            else:
                planned = plan(context, REPO_ROOT)
                result = {
                    **planned,
                    "status": "dry_run",
                    "gpu_execution_performed": False,
                    "writes_performed": False,
                }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계는 구조화 실패를 반환한다.
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
