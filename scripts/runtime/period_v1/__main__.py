# __main__.py - 기간 요청 계약 검증과 결정론적 범위 해석 CLI를 제공한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .contracts import (
    DATE_EXPRESSIONS,
    load_public_period_event,
    validate_contract_registry,
)
from .errors import PeriodRuntimeError
from .resolver import MAX_PERIOD_DAYS, resolve_period_scope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="일별 기간 Runtime 계약 v1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    resolve = commands.add_parser("resolve")
    resolve.add_argument("--input", required=True)
    resolve.add_argument("--reference-date")
    return parser


def _read_input(path: str) -> str:
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_JSON_INVALID", "기간 요청 JSON을 읽을 수 없습니다."
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_contract_registry()
            result = {
                "status": "verified",
                "registry_id": registry["registry_id"],
                "feature_flag_default": False,
            }
        elif args.command == "plan":
            validate_contract_registry()
            result = {
                "status": "planned",
                "request_version": "saju-period-request-v2",
                "date_expressions": sorted(DATE_EXPRESSIONS),
                "maximum_days": MAX_PERIOD_DAYS,
                "timezone": "Asia/Seoul",
                "intraday_segments_supported": False,
                "public_runtime_ids_allowed": False,
                "writes_performed": False,
            }
        else:
            reference = (
                None
                if args.reference_date is None
                else date.fromisoformat(args.reference_date)
            )
            event = load_public_period_event(_read_input(args.input))
            result = resolve_period_scope(event, reference_date=reference)
    except (PeriodRuntimeError, ValueError) as exc:
        code = getattr(exc, "code", "PERIOD_REFERENCE_DATE_INVALID")
        message = getattr(exc, "message", "서버 KST 기준일이 올바르지 않습니다.")
        print(
            json.dumps({"status": "error", "code": code, "message": message}, ensure_ascii=False)
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
