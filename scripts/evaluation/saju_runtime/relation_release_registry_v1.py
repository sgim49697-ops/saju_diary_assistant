# relation_release_registry_v1.py - 통과한 자동 Gate만 단일 날짜 relation release로 승인한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.relation_v1.contracts import (
    CHART_RELEASE_ID,
    PERIOD_RELEASE_ID,
    REGISTRY_PATH,
    RELEASE_PATH,
    validate_contract_registry,
    validate_release_registry,
)
from scripts.runtime.relation_v1.errors import RelationRuntimeError


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _report_directory(path: Path) -> Path:
    target = path if path.is_absolute() else REPO_ROOT / path
    if target.is_symlink() or not target.is_dir():
        raise RelationRuntimeError(
            "RELATION_RELEASE_REPORT_INVALID", "relation conformance build 경로가 필요합니다."
        )
    return target.resolve()


def prepare_release(report_root: Path) -> dict[str, Any]:
    validate_contract_registry()
    from scripts.evaluation.saju_runtime.relation_conformance_v1 import verify_report

    directory = _report_directory(report_root)
    verified = verify_report(directory)
    aggregate_path = directory / "aggregate.json"
    manifest_path = directory / "build_manifest.json"
    core = {
        "schema_version": "1.0.0",
        "status": "approved_single_date_relations_feature_default_off",
        "parent_chart_release": CHART_RELEASE_ID,
        "parent_period_release": PERIOD_RELEASE_ID,
        "contract_registry_sha256": sha256_file(REGISTRY_PATH),
        "conformance_report": {
            "build_id": verified["build_id"],
            "path": aggregate_path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_file(aggregate_path),
            "manifest_path": manifest_path.relative_to(REPO_ROOT).as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
        },
        "feature_flag_default": False,
        "single_date_only": True,
        "dashboard_v1_13_activated": False,
        "strict_full_runtime_approved": False,
        "sealed_blind_accessed": False,
        "mix20k_v3_1_generation_allowed": False,
        "training_execution_performed": False,
        "model_promotion_performed": False,
    }
    release_id = (
        "saju-natal-day-relation-release-v1.0.0-"
        + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    )
    return {"release_id": release_id, **core}


def write_release(value: dict[str, Any]) -> dict[str, Any]:
    payload = _json_bytes(value)
    RELEASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if RELEASE_PATH.exists() or RELEASE_PATH.is_symlink():
        if RELEASE_PATH.is_symlink() or RELEASE_PATH.read_bytes() != payload:
            raise RelationRuntimeError(
                "RELATION_RELEASE_WRITE_ONCE", "기존 relation release를 덮어쓸 수 없습니다."
            )
        release = validate_release_registry(RELEASE_PATH)
        return {"status": "reused", "release_id": release["release_id"]}
    descriptor = os.open(
        RELEASE_PATH,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("release write returned zero bytes")
            written += count
        os.fsync(descriptor)
    except OSError:
        try:
            RELEASE_PATH.unlink()
        except OSError:
            pass
        raise
    finally:
        os.close(descriptor)
    release = validate_release_registry(RELEASE_PATH)
    return {"status": "created", "release_id": release["release_id"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="single-date relation release registry")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    plan = commands.add_parser("plan")
    plan.add_argument("--report-root", type=Path, required=True)
    create = commands.add_parser("create")
    create.add_argument("--report-root", type=Path, required=True)
    create.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_contract_registry()
            result = {"status": "verified", "registry_id": registry["registry_id"]}
        elif args.command in {"plan", "create"}:
            value = prepare_release(args.report_root)
            if args.command == "create" and args.execute:
                result = write_release(value)
            else:
                result = {
                    "status": "planned" if args.command == "plan" else "dry_run",
                    "release_id": value["release_id"],
                    "output": RELEASE_PATH.relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
        else:
            release = validate_release_registry(RELEASE_PATH)
            result = {
                "status": "verified",
                "release_id": release["release_id"],
                "feature_flag_default": False,
                "single_date_only": True,
            }
    except (OSError, ValueError, RelationRuntimeError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": getattr(exc, "code", "RELATION_RELEASE_ERROR"),
                    "message": getattr(exc, "message", str(exc)),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
