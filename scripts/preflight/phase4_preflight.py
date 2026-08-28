# phase4_preflight.py - Phase 4A~C 비학습 preflight 명령을 제공한다.

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

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    load_json,
    prepare_context,
    prepare_runtime_headers,
    validate_contract,
)

DEFAULT_CONFIG = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/preflight-v1.0.0.json"
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B baseline Phase 4A~C 비학습 preflight 도구"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--build", help="현재 fingerprint의 build ID와 일치해야 한다.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    runtime = commands.add_parser("prepare-runtime")
    runtime.add_argument("--execute", action="store_true")
    runtime.add_argument("--probe", action="store_true")
    build = commands.add_parser("build")
    build.add_argument("--execute", action="store_true")
    k0 = commands.add_parser("run-k0")
    k0.add_argument("--execute", action="store_true")
    resume = commands.add_parser("resume-k0")
    resume.add_argument("--execute", action="store_true")
    review = commands.add_parser("export-review")
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--confirm-authorized-reviewer", action="store_true")
    verify_review = commands.add_parser("verify-review")
    verify_review.add_argument("--archive", type=Path, required=True)
    commands.add_parser("verify")
    return parser


def _load_lazy_modules() -> tuple[Any, Any, Any]:
    from scripts.preflight import phase4_data, phase4_k0, phase4_review

    return phase4_data, phase4_k0, phase4_review


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    config = load_json(config_path, "Phase 4 설정")
    if arguments.command == "validate-contract":
        return validate_contract(config, REPO_ROOT)
    context = prepare_context(REPO_ROOT, config_path)
    if arguments.build and arguments.build != context["build_id"]:
        raise Phase4Error(
            f"요청한 build가 현재 fingerprint와 다릅니다: {arguments.build}"
        )
    if arguments.command == "plan":
        return {
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "mode": "plan",
            "private_root": context["private_root"].relative_to(REPO_ROOT).as_posix(),
            "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
            "training_promotion_allowed": False,
        }
    phase4_data, phase4_k0, phase4_review = _load_lazy_modules()
    if arguments.command == "prepare-runtime":
        result = prepare_runtime_headers(config, REPO_ROOT, execute=arguments.execute)
        if arguments.probe:
            if not arguments.execute and result.get("mode") == "dry_run":
                raise Phase4Error("--probe에는 준비된 sysroot 또는 --execute가 필요합니다.")
            result["probe"] = phase4_k0.probe_runtime(context, REPO_ROOT)
        return result
    if arguments.command == "build":
        if not arguments.execute:
            return {"build_id": context["build_id"], "mode": "build_dry_run"}
        return phase4_data.execute_build(context, REPO_ROOT)
    if arguments.command in {"run-k0", "resume-k0"}:
        if not arguments.execute:
            return {"build_id": context["build_id"], "mode": "k0_dry_run"}
        return phase4_k0.run_k0(context, REPO_ROOT)
    if arguments.command == "export-review":
        return phase4_review.export_review_package(
            context,
            REPO_ROOT,
            arguments.output.expanduser().resolve(),
            confirm_authorized_reviewer=arguments.confirm_authorized_reviewer,
        )
    if arguments.command == "verify-review":
        return phase4_review.verify_review_archive(arguments.archive.expanduser().resolve())
    if arguments.command == "verify":
        return phase4_review.verify_preflight(context, REPO_ROOT)
    raise Phase4Error(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        _print_json(run(arguments))
        return 0
    except Phase4Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
