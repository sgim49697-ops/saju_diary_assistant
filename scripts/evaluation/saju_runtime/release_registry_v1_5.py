# release_registry_v1_5.py - 통과한 conformance v10만 원국+단일 일진 v1.5로 승인한다.

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import ID_CONTRACT_VERSION_V2
from scripts.runtime.calculation.contracts_v1_4 import (
    APPROVED_END_DATE,
    APPROVED_START_DATE,
    RELEASE_V14_PATH,
    validate_release_registry_v1_4,
)
from scripts.runtime.calculation.contracts_v1_5 import (
    APPROVED_SCOPE_V15,
    ENGINE_VERSION_V15,
    REGISTRY_V15_PATH,
    RELEASE_V15_PATH,
    SINGLE_DAY_CASES,
    SINGLE_DAY_END_DATE,
    SINGLE_DAY_START_DATE,
    _validate_report_identity_v1_5,
    release_id_for_v1_5,
    validate_contract_registry_v1_5,
    validate_release_registry_v1_5,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.5 release 입력이 저장소 밖에 있습니다."
        ) from exc


def build_release(report_path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_5()
    manifest_path = report_path.parent / "build_manifest.json"
    identity = {
        "path": _relative(report_path),
        "sha256": sha256_file(report_path),
        "manifest_path": _relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "build_id": report_path.parent.name,
    }
    report, _manifest = _validate_report_identity_v1_5(identity)
    implementations = report.get("inputs", {}).get("implementation_sha256")
    official = report.get("inputs", {}).get("official_snapshots")
    if not isinstance(implementations, dict) or not isinstance(official, dict):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v10 conformance identity가 비었습니다."
        )
    parent = validate_release_registry_v1_4(RELEASE_V14_PATH)
    return {
        "release_id": release_id_for_v1_5(
            identity["sha256"], identity["manifest_sha256"]
        ),
        "status": "approved_chart_and_single_day_feature_default_off",
        "engine_version": ENGINE_VERSION_V15,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "profile_id": report["profile_id"],
        "approval_scope": APPROVED_SCOPE_V15,
        "approved_tools": ["calculate_saju_chart", "calculate_saju_period"],
        "blocked_tools": [],
        "approved_solar_date_range": {
            "minimum": APPROVED_START_DATE,
            "maximum": APPROVED_END_DATE,
        },
        "approved_single_day_range": {
            "minimum": SINGLE_DAY_START_DATE,
            "maximum": SINGLE_DAY_END_DATE,
        },
        "single_day_evaluation_local_time": "12:00",
        "single_day_dates": SINGLE_DAY_CASES,
        "single_day_label_mismatches": 0,
        "noon_boundary_quarantine_dates": 0,
        "parent_v1_4_release": {
            "release_id": parent["release_id"],
            "path": str(RELEASE_V14_PATH.relative_to(REPO_ROOT)),
            "sha256": parent["release_registry_sha256"],
        },
        "runtime_registry_sha256": sha256_file(REGISTRY_V15_PATH),
        "conformance_report": identity,
        "official_snapshots": official,
        "implementation_sha256": implementations,
        "production_id_key_required": True,
        "runtime_feature_flag_default": False,
        "strict_runtime_provider_gate_passed": False,
        "full_runtime_gate_passed": False,
        "production_application_binding": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }


def write_release(value: dict[str, Any], output: Path = RELEASE_V15_PATH) -> Path:
    resolved = output.resolve(strict=False)
    if resolved != RELEASE_V15_PATH.resolve(strict=False):
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.5 release 파일명은 고정 경로여야 합니다."
        )
    if output.exists() or output.is_symlink():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_EXISTS", "기존 v1.5 release를 덮어쓰지 않습니다."
        )
    output.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    descriptor = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    validate_release_registry_v1_5(output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saju runtime v1.5 원국+단일 일진 승인기")
    parser.add_argument("command", choices=["approve"])
    parser.add_argument("--conformance-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=RELEASE_V15_PATH)
    args = parser.parse_args(argv)
    try:
        value = build_release(args.conformance_report)
        output = write_release(value, args.output)
    except (OSError, RuntimeCalculationError) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "approved_chart_and_single_day",
                "release_id": value["release_id"],
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
