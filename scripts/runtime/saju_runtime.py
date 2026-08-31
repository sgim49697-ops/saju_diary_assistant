# saju_runtime.py - 한국 만세력 후보 runtime 계약 검증과 로컬 계산 CLI를 제공한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation import RuntimeCalculationError, SajuRuntimeEngine
from scripts.runtime.calculation.bridge import execute_runtime_tool
from scripts.runtime.calculation.contracts import (
    runtime_source_versions,
    validate_contract_registry,
)


def _load_input(path: str) -> dict[str, Any]:
    try:
        text = (
            sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        )
        value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeCalculationError(
            "INPUT_JSON_INVALID", "입력 JSON을 읽을 수 없습니다."
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeCalculationError(
            "INPUT_JSON_INVALID", "입력 JSON 최상위는 object여야 합니다."
        )
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 후보 runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify-contract", help="versioned 계약과 SHA-256을 검증")
    commands.add_parser("environment", help="tzdb와 고정 패키지 버전을 확인")
    calculate = commands.add_parser("calculate", help="chart tool JSON을 로컬 계산")
    calculate.add_argument(
        "--input", required=True, help="tool arguments JSON 경로 또는 -"
    )
    calculate.add_argument(
        "--enable-candidate-runtime",
        action="store_true",
        help="승인 전 후보 계산을 명시적으로 허용",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-contract":
            output = {
                "status": "verified",
                "registry": validate_contract_registry()["registry_id"],
            }
        elif args.command == "environment":
            output = {
                "status": "verified",
                "source_versions": runtime_source_versions(require_dependencies=True),
            }
        else:
            engine = SajuRuntimeEngine(
                enable_candidate_runtime=args.enable_candidate_runtime
            )
            internal, visible = execute_runtime_tool(
                engine, "calculate_saju_chart", _load_input(args.input)
            )
            output = {"internal": internal, "model_visible": visible}
    except RuntimeCalculationError as exc:
        print(
            json.dumps(
                {"status": "error", "code": exc.code, "message": exc.message},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
