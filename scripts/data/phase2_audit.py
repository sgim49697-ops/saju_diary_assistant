# phase2_audit.py - Phase 2A 데이터 감사·사람 검토·명시적 승인 CLI를 제공한다.

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

from scripts.data.audit_tools import (  # noqa: E402
    approve_audit,
    audit_plan,
    audit_status,
    execute_scan,
    finalize_audit,
    prepare_audit,
    run_review,
    verify_audit,
)
from scripts.data.errors import Phase1Error, Phase2AuditError  # noqa: E402

DEFAULT_SOURCE_CONFIG = REPO_ROOT / "configs/data_sources.v1.json"
DEFAULT_POLICY = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/audit-policy-v1.0.0.json"
)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="사주 1.3B baseline Phase 2A 읽기 전용 원천 감사 도구"
    )
    parser.add_argument(
        "--source-config",
        type=Path,
        default=DEFAULT_SOURCE_CONFIG,
        help="Phase 1 원천 설정 JSON 경로",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY,
        help="버전별 감사 정책 JSON 경로",
    )
    parser.add_argument(
        "--audit-version", default="v1.0.0", help="vMAJOR.MINOR.PATCH 감사 버전"
    )
    parser.add_argument(
        "--build",
        help="계산된 build-<12자리-hash>와 일치할 때만 명령을 수행한다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("plan", help="원본을 검증하고 build 계획만 출력한다.")
    scan = subparsers.add_parser("scan", help="원천 전체 검사와 검토 큐를 생성한다.")
    scan.add_argument(
        "--execute",
        action="store_true",
        help="실제 감사 산출물을 생성한다. 생략 시 plan만 출력한다.",
    )

    review = subparsers.add_parser(
        "review", help="원문을 즉시 읽어 사람 검토 결정을 기록한다."
    )
    review.add_argument(
        "--source",
        choices=("nemotron_saju", "bazi_sft", "aihub_empathy", "yeji_bazi_rules"),
        help="한 원천만 검토한다.",
    )
    review.add_argument(
        "--required-only", action="store_true", help="필수 150건 큐만 검토한다."
    )
    review.add_argument("--limit", type=int, help="이번 실행에서 검토할 최대 단위 수")

    subparsers.add_parser("status", help="원문 없이 검토 진행률을 집계한다.")
    subparsers.add_parser("finalize", help="필수 검토를 확인하고 build를 seal한다.")
    approve = subparsers.add_parser(
        "approve", help="seal된 통과 build를 명시적으로 승인한다."
    )
    approve.add_argument(
        "--confirm-user-approval",
        action="store_true",
        help="사용자가 승인을 명시했음을 확인한다.",
    )
    subparsers.add_parser(
        "verify", help="원본·비공개 큐·공개 보고서·seal 무결성을 검증한다."
    )
    return parser


def _resolved(arguments: argparse.Namespace) -> tuple[Path, Path]:
    return (
        arguments.source_config.expanduser().resolve(),
        arguments.policy.expanduser().resolve(),
    )


def _assert_requested_build(arguments: argparse.Namespace) -> None:
    if not arguments.build:
        return
    source_config, policy = _resolved(arguments)
    context = prepare_audit(
        REPO_ROOT,
        source_config,
        policy,
        arguments.audit_version,
        verify_raw=False,
    )
    if context["identity"]["build_id"] != arguments.build:
        raise Phase2AuditError(
            "요청한 --build가 현재 코드·정책 fingerprint와 다릅니다."
        )


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    source_config, policy = _resolved(arguments)
    _assert_requested_build(arguments)
    common = (REPO_ROOT, source_config, policy, arguments.audit_version)
    if arguments.command == "plan":
        return audit_plan(*common)
    if arguments.command == "scan":
        if arguments.execute:
            return execute_scan(*common)
        return {**audit_plan(*common), "mode": "scan_dry_run"}
    if arguments.command == "review":
        if arguments.limit is not None and arguments.limit <= 0:
            raise Phase2AuditError("--limit은 양수여야 합니다.")
        return run_review(
            *common,
            source=arguments.source,
            required_only=arguments.required_only,
            limit=arguments.limit,
        )
    if arguments.command == "status":
        return audit_status(*common)
    if arguments.command == "finalize":
        return finalize_audit(*common)
    if arguments.command == "approve":
        return approve_audit(
            *common, confirm_user_approval=arguments.confirm_user_approval
        )
    if arguments.command == "verify":
        return verify_audit(*common)
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
