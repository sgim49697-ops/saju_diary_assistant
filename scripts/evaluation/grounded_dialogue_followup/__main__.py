# __main__.py - grounded dialogue 후속 재채점·장문 진단 CLI 진입점이다.

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
from .reporting import verify_context, verify_rescore
from .runner import (
    context_plan,
    execute_context,
    execute_rescore,
    rescore_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="grounded dialogue 자동 후속 진단")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    rescore = commands.add_parser("rescore")
    rescore.add_argument("--execute", action="store_true")
    execute = commands.add_parser("execute")
    execute.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                load_json(config_path, "grounded dialogue followup config"), REPO_ROOT
            )
        else:
            context = prepare_context(
                REPO_ROOT,
                config_path,
                require_local_artifacts=True,
            )
            if args.command == "plan":
                result = {
                    "status": "ready_not_executed",
                    "rescore": rescore_plan(context),
                    "context": context_plan(context, REPO_ROOT),
                    "writes_performed": False,
                    "sealed_blind_accessed": False,
                }
            elif args.command == "rescore":
                result = (
                    execute_rescore(context, REPO_ROOT)
                    if args.execute
                    else {**rescore_plan(context), "status": "dry_run"}
                )
            elif args.command == "execute":
                result = (
                    execute_context(context, REPO_ROOT)
                    if args.execute
                    else {**context_plan(context, REPO_ROOT), "status": "dry_run"}
                )
            else:
                result = {
                    "status": "verified",
                    "rescore": verify_rescore(context),
                    "context": verify_context(context),
                    "sealed_blind_accessed": False,
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
