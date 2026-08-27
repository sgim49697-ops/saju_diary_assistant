# phase2_verify_history.py - 과거 Phase 2A build를 당시 Git 코드와 함께 읽기 전용 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.audit_tools import assert_public_report_safe, sha256_json
from scripts.data.errors import Phase2AuditError
from scripts.data.source_tools import sha256_file

DATASET_NAME = "saju_1b_baseline"
HISTORICAL_CODE_PATHS = (
    "scripts/data/audit_tools.py",
    "scripts/data/phase2_audit.py",
    "scripts/data/source_tools.py",
    "scripts/data/archive_safety.py",
    "scripts/data/errors.py",
    "requirements-data.txt",
    "configs/data_sources.v1.json",
)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _git_blob(repo_root: Path, commit: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase2AuditError(
            f"과거 구현 파일을 Git에서 읽을 수 없습니다: {relative}"
        ) from exc
    return result.stdout


def _json_blob(repo_root: Path, commit: str, relative: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_git_blob(repo_root, commit, relative))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"과거 {label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"과거 {label} 최상위 값은 object여야 합니다.")
    return value


def _historical_code_sha256(repo_root: Path, commit: str) -> str:
    entries = []
    for relative in sorted(HISTORICAL_CODE_PATHS, key=lambda value: Path(value).name):
        payload = _git_blob(repo_root, commit, relative)
        entries.append(
            {
                "name": Path(relative).name,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return sha256_json(entries)


def verify_historical_build(
    repo_root: Path,
    *,
    audit_version: str,
    build_id: str,
    implementation_commit: str | None,
) -> dict[str, Any]:
    registry_path = repo_root / "configs/data_versions/saju_1b_baseline/registry.json"
    registry = _load_json(registry_path, "registry")
    entry = next(
        (
            item
            for item in registry.get("audit_builds", [])
            if item.get("version") == audit_version and item.get("build_id") == build_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise Phase2AuditError("registry에 요청한 과거 audit build가 없습니다.")
    commit = implementation_commit or entry.get("implementation_commit")
    if not isinstance(commit, str) or not commit:
        raise Phase2AuditError("과거 audit build의 implementation_commit이 없습니다.")

    private_root = (
        repo_root / "data/audit" / DATASET_NAME / audit_version / build_id
    )
    public_root = (
        repo_root / "data/reports" / DATASET_NAME / "audit" / audit_version / build_id
    )
    if not private_root.is_dir() or not public_root.is_dir():
        raise Phase2AuditError("과거 audit build 경로가 없습니다.")
    build_manifest = _load_json(public_root / "build_manifest.json", "build manifest")
    queue_manifest = _load_json(private_root / "queue_manifest.json", "queue manifest")
    if (
        build_manifest.get("dataset_name") != DATASET_NAME
        or build_manifest.get("audit_version") != audit_version
        or build_manifest.get("build_id") != build_id
    ):
        raise Phase2AuditError("과거 build manifest identity가 다릅니다.")

    policy_relative = (
        f"configs/data_versions/{DATASET_NAME}/audit-policy-{audit_version}.json"
    )
    policy_blob = _git_blob(repo_root, commit, policy_relative)
    policy = json.loads(policy_blob)
    bundle_relative = str(policy["source_bundle"])
    bundle = _json_blob(repo_root, commit, bundle_relative, "source bundle")
    source_payload = {
        "dataset_name": bundle.get("dataset_name"),
        "schema_version": bundle.get("schema_version"),
        "sources": bundle.get("sources"),
        "version": bundle.get("version"),
    }
    if sha256_json(source_payload) != bundle.get("source_build_sha256"):
        raise Phase2AuditError("과거 source bundle fingerprint가 다릅니다.")
    code_sha256 = _historical_code_sha256(repo_root, commit)
    identity = {
        "audit_version": audit_version,
        "code_sha256": code_sha256,
        "dataset_name": DATASET_NAME,
        "policy_sha256": hashlib.sha256(policy_blob).hexdigest(),
        "schema_version": build_manifest.get("schema_version"),
        "seed": build_manifest.get("seed"),
        "source_build_sha256": bundle["source_build_sha256"],
    }
    if (
        code_sha256 != build_manifest.get("code_sha256")
        or sha256_json(identity) != build_manifest.get("build_sha256")
    ):
        raise Phase2AuditError("과거 audit 코드 또는 build fingerprint가 다릅니다.")

    current_manifest_hashes = {}
    for source in bundle["sources"]:
        path = repo_root / str(source["manifest_path"])
        actual = sha256_file(path)
        if actual != source.get("manifest_sha256"):
            raise Phase2AuditError(
                f"현재 source manifest가 과거 build와 다릅니다: {source.get('source')}"
            )
        current_manifest_hashes[str(source["source"])] = actual
    if queue_manifest.get("source_manifest_sha256") != current_manifest_hashes:
        raise Phase2AuditError("과거 queue의 source manifest hash가 다릅니다.")

    queue_path = private_root / "review_queue.jsonl"
    decisions_path = private_root / "decisions.jsonl"
    queue_lines = [line for line in queue_path.read_text(encoding="utf-8").splitlines() if line]
    if len(queue_lines) != 301 or queue_manifest.get("queue_sha256") != sha256_file(
        queue_path
    ):
        raise Phase2AuditError("과거 review queue 무결성이 다릅니다.")
    for name, expected in build_manifest.get("artifact_sha256", {}).items():
        path = public_root / name
        if not path.is_file() or sha256_file(path) != expected:
            raise Phase2AuditError(f"과거 공개 산출물 hash가 다릅니다: {name}")
        assert_public_report_safe(_load_json(path, name))
    assert_public_report_safe(build_manifest)

    sealed = (private_root / "SEALED.json").exists()
    expected_mode = 0o400 if sealed else 0o600
    for path in (queue_path, decisions_path, private_root / "queue_manifest.json"):
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise Phase2AuditError(f"과거 비공개 파일 권한이 다릅니다: {path.name}")
    if stat.S_IMODE(private_root.stat().st_mode) & 0o077:
        raise Phase2AuditError("과거 비공개 디렉터리 권한이 너무 넓습니다.")
    return {
        "audit_version": audit_version,
        "build_id": build_id,
        "implementation_commit": commit,
        "review_queue_units": len(queue_lines),
        "sealed": sealed,
        "status": "historical_verified",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="과거 Phase 2A audit build와 당시 Git 구현을 검증한다."
    )
    parser.add_argument("--audit-version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--implementation-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = verify_historical_build(
            REPO_ROOT,
            audit_version=arguments.audit_version,
            build_id=arguments.build,
            implementation_commit=arguments.implementation_commit,
        )
    except Phase2AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
