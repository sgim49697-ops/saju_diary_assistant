# chart_only_dashboard_operations.py - dashboard v1.9 binding 계약과 기본 off 실행 계획을 출력한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from scripts.runtime.chart_only_dashboard_contracts import (
    ChartOnlyDashboardContractError,
    validate_dashboard_operations_registry,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="chart-only dashboard v1.9 운영 계약")
    parser.add_argument(
        "command", choices=("validate-contract", "environment", "plan")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        registry = validate_dashboard_operations_registry(
            require_dependencies=args.command == "environment"
        )
        if args.command == "validate-contract":
            result = {
                "status": "valid",
                "registry_id": registry["registry_id"],
                "production_application_binding": True,
                "runtime_feature_default": False,
                "writes_performed": False,
            }
        elif args.command == "environment":
            result = {
                "status": "environment_valid",
                "registry_id": registry["registry_id"],
                "dependencies_verified": True,
                "writes_performed": False,
            }
        else:
            result = {
                "status": "planned_feature_disabled",
                "registry_id": registry["registry_id"],
                "dashboard_schema_version": "1.9.0",
                "runtime_feature_enabled": False,
                "resources_opened": False,
                "http_canary_required": 100,
                "gpu_pair_required": 1,
                "period_runtime_allowed": False,
                "sealed_blind_accessed": False,
                "training_execution_performed": False,
                "model_promotion_performed": False,
            }
    except (OSError, ValueError, ChartOnlyDashboardContractError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
