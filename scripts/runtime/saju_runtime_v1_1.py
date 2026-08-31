# saju_runtime_v1_1.py - 계층형 Gate release 기반 runtime 검증·계산 CLI를 제공한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.approved_engine import (
    ApprovedSajuRuntimeEngine,
    execute_approved_runtime_tool,
)
from scripts.runtime.calculation.contracts_v1_1 import (
    runtime_source_versions_v1_1,
    validate_contract_registry_v1_1,
    validate_release_registry,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError


def _load_input(path: str) -> dict[str, Any]:
    try:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
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
    parser = argparse.ArgumentParser(description="한국 만세력 release runtime v1.1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify-contract")
    environment = commands.add_parser("environment")
    environment.add_argument("--include-validator", action="store_true")
    release = commands.add_parser("verify-release")
    release.add_argument("--release-registry", type=Path, required=True)
    calculate = commands.add_parser("calculate")
    calculate.add_argument(
        "--tool",
        choices=["calculate_saju_chart", "calculate_saju_period"],
        default="calculate_saju_chart",
    )
    calculate.add_argument("--input", required=True)
    calculate.add_argument("--release-registry", type=Path)
    calculate.add_argument("--enable-approved-runtime", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-contract":
            result = {
                "status": "verified",
                "registry": validate_contract_registry_v1_1()["registry_id"],
            }
        elif args.command == "environment":
            result = {
                "status": "verified",
                "source_versions": runtime_source_versions_v1_1(
                    require_runtime_dependencies=True,
                    require_validator_dependencies=args.include_validator,
                ),
            }
        elif args.command == "verify-release":
            release = validate_release_registry(args.release_registry)
            result = {
                "status": "verified",
                "release_id": release["release_id"],
                "runtime_feature_flag_default": False,
            }
        else:
            engine = ApprovedSajuRuntimeEngine(
                release_registry=args.release_registry,
                enable_approved_runtime=args.enable_approved_runtime,
            )
            internal, visible = execute_approved_runtime_tool(
                engine, args.tool, _load_input(args.input)
            )
            result = {"internal": internal, "model_visible": visible}
    except RuntimeCalculationError as exc:
        print(
            json.dumps(
                {"status": "error", "code": exc.code, "message": exc.message},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
