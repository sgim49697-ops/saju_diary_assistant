# chart_only_operations.py - chart-only 운영 계약·키 준비·기본 off adapter 계획 CLI를 제공한다.

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .chart_only_adapter import adapter_plan
from .chart_only_operations_contracts import (
    ChartOnlyOperationsContractError,
    validate_operations_registry,
)
from .chart_only_security import (
    ChartOnlySecurityError,
    assert_key_separation,
    create_secret_key,
    load_secret_key,
)

CREATE_CONFIRMATION = "CREATE_CHART_ONLY_SECRET_KEY"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="chart-only 운영 준비 CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("environment")
    commands.add_parser("plan")
    create = commands.add_parser("create-key")
    create.add_argument("--purpose", choices=("runtime-hmac", "session-aead"), required=True)
    create.add_argument("--path", type=Path, required=True)
    create.add_argument("--confirm", required=True)
    verify = commands.add_parser("verify-key-pair")
    verify.add_argument("--hmac-key-file", type=Path, required=True)
    verify.add_argument("--encryption-key-file", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_operations_registry(require_dependencies=False)
            result = {"status": "valid", "registry_id": registry["registry_id"]}
        elif args.command == "environment":
            registry = validate_operations_registry(require_dependencies=True)
            result = {
                "status": "verified",
                "registry_id": registry["registry_id"],
                "dependencies": {
                    package: importlib.metadata.version(package)
                    for package in (
                        "cryptography",
                        "cffi",
                        "pycparser",
                        "typing_extensions",
                    )
                },
                "production_application_binding": False,
            }
        elif args.command == "plan":
            result = adapter_plan()
        elif args.command == "create-key":
            validate_operations_registry(require_dependencies=True)
            if args.confirm != CREATE_CONFIRMATION:
                raise ChartOnlySecurityError(
                    f"key 생성에는 --confirm {CREATE_CONFIRMATION}가 필요합니다."
                )
            key = create_secret_key(args.path, purpose=args.purpose)
            result = {
                "status": "created",
                "purpose": key.purpose,
                "key_id": key.key_id,
                "secret_material_printed": False,
                "production_application_binding": False,
            }
        else:
            validate_operations_registry(require_dependencies=True)
            hmac_key = load_secret_key(
                args.hmac_key_file, purpose="runtime-hmac"
            )
            encryption_key = load_secret_key(
                args.encryption_key_file, purpose="session-aead"
            )
            assert_key_separation(hmac_key, encryption_key)
            result = {
                "status": "verified_separate_keys",
                "hmac_key_id": hmac_key.key_id,
                "encryption_key_id": encryption_key.key_id,
                "secret_material_printed": False,
                "production_application_binding": False,
            }
    except (
        ChartOnlyOperationsContractError,
        ChartOnlySecurityError,
        OSError,
    ) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
