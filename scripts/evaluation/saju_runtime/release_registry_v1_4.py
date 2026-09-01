# release_registry_v1_4.py - 통과한 conformance v9만 chart-only v1.4 release로 승인한다.

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
    APPROVED_SCOPE_V14,
    APPROVED_START_DATE,
    ENGINE_VERSION_V14,
    KASI_PAST_UNCERTAINTY_SECONDS,
    REGISTRY_V14_PATH,
    RELEASE_V14_PATH,
    _validate_report_identity_v1_4,
    release_id_for_v1_4,
    validate_contract_registry_v1_4,
    validate_release_registry_v1_4,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.4 release 입력이 저장소 밖에 있습니다."
        ) from exc


def build_release(report_path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_4()
    manifest_path = report_path.parent / "build_manifest.json"
    identity = {
        "path": _relative(report_path),
        "sha256": sha256_file(report_path),
        "manifest_path": _relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "build_id": report_path.parent.name,
    }
    report, _manifest = _validate_report_identity_v1_4(identity)
    implementations = report.get("inputs", {}).get("implementation_sha256")
    official = report.get("inputs", {}).get("official_snapshots")
    if not isinstance(implementations, dict) or not isinstance(official, dict):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v9 conformance identity가 비었습니다."
        )
    return {
        "release_id": release_id_for_v1_4(
            identity["sha256"], identity["manifest_sha256"]
        ),
        "status": "approved_chart_only_feature_default_off",
        "engine_version": ENGINE_VERSION_V14,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "profile_id": report["profile_id"],
        "approval_scope": APPROVED_SCOPE_V14,
        "approved_tools": ["calculate_saju_chart"],
        "blocked_tools": ["calculate_saju_period"],
        "approved_solar_date_range": {
            "minimum": APPROVED_START_DATE,
            "maximum": APPROVED_END_DATE,
        },
        "boundary_uncertainty_seconds": KASI_PAST_UNCERTAINTY_SECONDS,
        "quarantined_boundary_minutes": 50,
        "runtime_registry_sha256": sha256_file(REGISTRY_V14_PATH),
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


def write_release(value: dict[str, Any], output: Path = RELEASE_V14_PATH) -> Path:
    resolved = output.resolve(strict=False)
    if resolved != RELEASE_V14_PATH.resolve(strict=False):
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.4 release 파일명은 고정 경로여야 합니다."
        )
    if output.exists() or output.is_symlink():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_EXISTS", "기존 v1.4 release를 덮어쓰지 않습니다."
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
    validate_release_registry_v1_4(output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saju runtime v1.4 chart-only 승인기")
    parser.add_argument("command", choices=["approve"])
    parser.add_argument("--conformance-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=RELEASE_V14_PATH)
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
                "status": "approved_chart_only",
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
