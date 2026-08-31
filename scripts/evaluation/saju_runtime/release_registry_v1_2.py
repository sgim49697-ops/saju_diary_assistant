# release_registry_v1_2.py - 통과한 conformance v4만 v1.2 runtime release로 승인한다.

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import (
    ENGINE_VERSION_V12,
    ID_CONTRACT_VERSION_V2,
    REGISTRY_V12_PATH,
    _validate_report_identity_v1_2,
    release_id_for_v1_2,
    validate_contract_registry_v1_2,
    validate_release_registry_v1_2,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError

RELEASE_ROOT = REPO_ROOT / "configs/runtime/calculation/releases/v1.2.0"
DEFAULT_OUTPUT = RELEASE_ROOT / "release_registry.json"


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError as exc:
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 release 입력이 저장소 밖에 있습니다."
        ) from exc


def build_release(report_path: Path) -> dict[str, Any]:
    validate_contract_registry_v1_2()
    manifest_path = report_path.parent / "build_manifest.json"
    identity = {
        "path": _relative(report_path),
        "sha256": sha256_file(report_path),
        "manifest_path": _relative(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "build_id": report_path.parent.name,
    }
    report, _manifest = _validate_report_identity_v1_2(identity)
    implementations = report.get("inputs", {}).get("implementation_sha256")
    official = report.get("inputs", {}).get("official_snapshots")
    if not isinstance(implementations, dict) or not isinstance(official, dict):
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_INVALID", "v1.2 conformance identity가 비었습니다."
        )
    return {
        "release_id": release_id_for_v1_2(
            identity["sha256"], identity["manifest_sha256"]
        ),
        "status": "approved_runtime_feature_default_off",
        "engine_version": ENGINE_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "profile_id": report["profile_id"],
        "runtime_registry_sha256": sha256_file(REGISTRY_V12_PATH),
        "conformance_report": identity,
        "official_snapshots": official,
        "implementation_sha256": implementations,
        "production_id_key_required": True,
        "runtime_feature_flag_default": False,
        "training_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }


def write_release(value: dict[str, Any], output: Path) -> Path:
    resolved = output.resolve(strict=False)
    try:
        resolved.relative_to(RELEASE_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", f"v1.2 release는 {RELEASE_ROOT} 아래여야 합니다."
        ) from exc
    if resolved != DEFAULT_OUTPUT.resolve(strict=False):
        raise RuntimeCalculationError(
            "UNSAFE_CONTRACT_PATH", "v1.2 release 파일명은 고정 경로여야 합니다."
        )
    if output.exists() or output.is_symlink():
        raise RuntimeCalculationError(
            "RUNTIME_RELEASE_EXISTS", "기존 v1.2 release를 덮어쓰지 않습니다."
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
    validate_release_registry_v1_2(output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="saju runtime v1.2 release 승인기")
    parser.add_argument("command", choices=["approve"])
    parser.add_argument("--conformance-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
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
            {"status": "approved", "release_id": value["release_id"], "output": str(output)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
