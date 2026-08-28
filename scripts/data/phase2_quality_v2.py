# phase2_quality_v2.py - 품질 보정 staging v1 생성·검증 CLI를 제공한다.

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

from scripts.data.errors import Phase2AuditError
from scripts.data.quality_v2_tools import (
    execute_quality_build,
    load_quality_config,
    prepare_quality_context,
    validate_calculation_policy,
    verify_quality_build,
)

DEFAULT_CONFIG = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/preprocessing-staging-v1.0.0.json"
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B 품질 보정 staging v1 생성·검증 도구"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--build", help="현재 fingerprint의 build ID와 일치해야 한다.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    build = commands.add_parser("build")
    build.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    if not config_path.is_relative_to(REPO_ROOT.resolve()):
        raise Phase2AuditError("품질 보정 설정은 저장소 안의 파일이어야 합니다.")
    if arguments.command == "validate-contract":
        config = load_quality_config(config_path, REPO_ROOT)
        return {
            "status": "valid",
            "staging_version": config["staging_version"],
            "record_schema_version": config["record_schema_version"],
            "row_counts": {
                axis: values["staging_rows"] for axis, values in config["axes"].items()
            },
            "calculation_policy": validate_calculation_policy(config, REPO_ROOT),
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
        }
    context = prepare_quality_context(REPO_ROOT, config_path)
    if arguments.build and arguments.build != context["build_id"]:
        raise Phase2AuditError(
            f"요청한 build가 현재 fingerprint와 다릅니다: {arguments.build}"
        )
    if arguments.command == "plan":
        return {
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "private_root": context["private_root"].relative_to(REPO_ROOT).as_posix(),
            "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
            "mode": "plan",
            "writes_performed": False,
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
        }
    if arguments.command == "build":
        if not arguments.execute:
            return {
                "build_id": context["build_id"],
                "mode": "build_dry_run",
                "writes_performed": False,
                "training_promotion_allowed": False,
                "phase5_training_performed": False,
            }
        return execute_quality_build(context, REPO_ROOT)
    if arguments.command == "verify":
        return verify_quality_build(context, REPO_ROOT)
    raise Phase2AuditError(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        _print_json(run(arguments))
        return 0
    except Phase2AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
