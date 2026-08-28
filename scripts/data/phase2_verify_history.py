# phase2_verify_history.py - 과거 Phase 2A build를 당시 Git 코드와 함께 읽기 전용 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.archive_safety import validate_relative_archive_path
from scripts.data.audit_tools import assert_public_report_safe, sha256_json
from scripts.data.errors import Phase1Error, Phase2AuditError
from scripts.data.source_tools import sha256_file

DATASET_NAME = "saju_1b_baseline"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{7,40}")
VERSION_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
BUILD_PATTERN = re.compile(r"build-[0-9a-f]{12}")
HISTORICAL_BASE_CODE_PATHS = (
    "scripts/data/audit_tools.py",
    "scripts/data/phase2_audit.py",
    "scripts/data/source_tools.py",
    "scripts/data/archive_safety.py",
    "scripts/data/errors.py",
    "requirements-data.txt",
)
SOURCE_CONFIG_CANDIDATES = (
    "configs/data_sources.v1.1.json",
    "configs/data_sources.v1.json",
)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase2AuditError(f"{label}은 symlink가 아닌 일반 파일이어야 합니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _git_blob(repo_root: Path, commit: str, relative: str) -> bytes:
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise Phase2AuditError("과거 구현 commit은 7~40자 16진 Git SHA여야 합니다.")
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


def _historical_code_sha256(
    repo_root: Path, commit: str, source_config: str
) -> str:
    entries = []
    paths = (*HISTORICAL_BASE_CODE_PATHS, source_config)
    for relative in sorted(paths, key=lambda value: Path(value).name):
        payload = _git_blob(repo_root, commit, relative)
        entries.append(
            {
                "name": Path(relative).name,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return sha256_json(entries)


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Phase2AuditError(f"과거 {label}은 symlink가 아닌 일반 파일이어야 합니다.")
    values: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"line {line_number}")
            values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase2AuditError(f"과거 {label} JSONL을 읽을 수 없습니다.") from exc
    return values


def _validate_identity(value: str, pattern: re.Pattern[str], label: str) -> None:
    if pattern.fullmatch(value) is None:
        raise Phase2AuditError(f"{label} 형식이 올바르지 않습니다: {value!r}")


def _reject_symlink_components(repo_root: Path, path: Path, label: str) -> None:
    try:
        relative = path.relative_to(repo_root)
    except ValueError as exc:
        raise Phase2AuditError(f"{label} 경로가 저장소 밖입니다.") from exc
    cursor = repo_root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise Phase2AuditError(f"{label} 경로에 symlink를 허용하지 않습니다.")


def _safe_repo_file(repo_root: Path, relative: str, label: str) -> Path:
    try:
        validated = validate_relative_archive_path(relative)
    except Phase1Error as exc:
        raise Phase2AuditError(f"{label} 상대경로가 안전하지 않습니다.") from exc
    path = repo_root.joinpath(*validated.parts)
    _reject_symlink_components(repo_root, path, label)
    if not path.is_file() or not path.resolve().is_relative_to(repo_root):
        raise Phase2AuditError(f"{label}은 저장소 내부 일반 파일이어야 합니다.")
    return path


def _safe_child_file(root: Path, relative: str, label: str) -> Path:
    try:
        validated = validate_relative_archive_path(relative)
    except Phase1Error as exc:
        raise Phase2AuditError(f"{label} 상대경로가 안전하지 않습니다.") from exc
    path = root.joinpath(*validated.parts)
    _reject_symlink_components(root, path, label)
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise Phase2AuditError(f"{label}은 build 내부 일반 파일이어야 합니다.")
    return path


def _resolve_historical_source_config(
    repo_root: Path, commit: str, expected_code_sha256: str
) -> tuple[str, str]:
    for relative in SOURCE_CONFIG_CANDIDATES:
        try:
            code_sha256 = _historical_code_sha256(repo_root, commit, relative)
        except Phase2AuditError:
            continue
        if code_sha256 == expected_code_sha256:
            return relative, code_sha256
    raise Phase2AuditError("과거 audit code fingerprint에 맞는 source config를 찾지 못했습니다.")


def verify_historical_build(
    repo_root: Path,
    *,
    audit_version: str,
    build_id: str,
    implementation_commit: str | None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    _validate_identity(audit_version, VERSION_PATTERN, "audit version")
    _validate_identity(build_id, BUILD_PATTERN, "build ID")
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
    _reject_symlink_components(repo_root, private_root, "과거 비공개 audit build")
    _reject_symlink_components(repo_root, public_root, "과거 공개 audit build")
    if not private_root.is_dir() or not public_root.is_dir():
        raise Phase2AuditError("과거 audit build 경로가 없습니다.")
    build_manifest = _load_json(public_root / "build_manifest.json", "build manifest")
    queue_manifest = _load_json(private_root / "queue_manifest.json", "queue manifest")
    if (
        build_manifest.get("dataset_name") != DATASET_NAME
        or build_manifest.get("audit_version") != audit_version
        or build_manifest.get("build_id") != build_id
        or entry.get("build_sha256") != build_manifest.get("build_sha256")
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
    source_config_relative, code_sha256 = _resolve_historical_source_config(
        repo_root, commit, str(build_manifest.get("code_sha256", ""))
    )
    identity = {
        "audit_version": audit_version,
        "code_sha256": code_sha256,
        "dataset_name": DATASET_NAME,
        "policy_sha256": hashlib.sha256(policy_blob).hexdigest(),
        "schema_version": build_manifest.get("schema_version"),
        "seed": build_manifest.get("seed"),
        "source_build_sha256": bundle["source_build_sha256"],
    }
    correction_relative = policy.get("correction_manifest")
    if correction_relative is not None:
        if not isinstance(correction_relative, str):
            raise Phase2AuditError("과거 correction manifest 경로가 올바르지 않습니다.")
        correction_sha256 = hashlib.sha256(
            _git_blob(repo_root, commit, correction_relative)
        ).hexdigest()
        identity["correction_sha256"] = correction_sha256
        if (
            build_manifest.get("correction_manifest_sha256") != correction_sha256
            or queue_manifest.get("correction_manifest_sha256") != correction_sha256
        ):
            raise Phase2AuditError("과거 correction manifest fingerprint가 다릅니다.")
    if (
        code_sha256 != build_manifest.get("code_sha256")
        or sha256_json(identity) != build_manifest.get("build_sha256")
        or build_manifest.get("build_id") != f"build-{str(build_manifest.get('build_sha256'))[:12]}"
        or queue_manifest.get("build_sha256") != build_manifest.get("build_sha256")
    ):
        raise Phase2AuditError("과거 audit 코드 또는 build fingerprint가 다릅니다.")

    current_manifest_hashes = {}
    for source in bundle["sources"]:
        path = _safe_repo_file(
            repo_root,
            str(source["manifest_path"]),
            f"{source.get('source')} source manifest",
        )
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
    queue = _read_jsonl(queue_path, "review queue")
    decisions = _read_jsonl(decisions_path, "review decisions")
    expected_queue_units = sum(policy["required_review"].values()) + sum(
        policy["reference_review"].values()
    )
    if (
        len(queue) != expected_queue_units
        or len({item.get("review_id") for item in queue}) != len(queue)
        or queue_manifest.get("required_review_units")
        != sum(policy["required_review"].values())
        or queue_manifest.get("reference_review_units")
        != sum(policy["reference_review"].values())
        or queue_manifest.get("queue_sha256") != sha256_file(queue_path)
    ):
        raise Phase2AuditError("과거 review queue 무결성이 다릅니다.")
    artifacts = build_manifest.get("artifact_sha256")
    if not isinstance(artifacts, dict):
        raise Phase2AuditError("과거 공개 산출물 hash map이 없습니다.")
    for name, expected in artifacts.items():
        if not isinstance(name, str) or not isinstance(expected, str):
            raise Phase2AuditError("과거 공개 산출물 hash map 형식이 올바르지 않습니다.")
        path = _safe_child_file(public_root, name, "과거 공개 산출물")
        if sha256_file(path) != expected:
            raise Phase2AuditError(f"과거 공개 산출물 hash가 다릅니다: {name}")
        assert_public_report_safe(_load_json(path, name))
    assert_public_report_safe(build_manifest)

    sealed_path = private_root / "SEALED.json"
    if sealed_path.is_symlink():
        raise Phase2AuditError("과거 audit seal에 symlink를 허용하지 않습니다.")
    sealed = sealed_path.exists()
    approval_path = public_root / "APPROVAL.json"
    if approval_path.is_symlink():
        raise Phase2AuditError("과거 audit approval에 symlink를 허용하지 않습니다.")
    approved = approval_path.exists()
    expected_approved = entry.get("status") == "approved"
    if approved != expected_approved:
        raise Phase2AuditError("registry 상태와 과거 audit approval 존재가 다릅니다.")
    if sealed:
        seal = _load_json(sealed_path, "seal")
        final_gate_path = _safe_child_file(
            public_root, "gate.final.json", "과거 audit final gate"
        )
        if (
            seal.get("build_sha256") != build_manifest.get("build_sha256")
            or seal.get("queue_sha256") != sha256_file(queue_path)
            or seal.get("decisions_sha256") != sha256_file(decisions_path)
            or not final_gate_path.is_file()
            or seal.get("public_gate_sha256") != sha256_file(final_gate_path)
            or seal.get("public_build_manifest_sha256")
            != sha256_file(public_root / "build_manifest.json")
        ):
            raise Phase2AuditError("과거 audit seal 무결성이 다릅니다.")
        assert_public_report_safe(_load_json(final_gate_path, "final gate"))
    if approved:
        if not sealed:
            raise Phase2AuditError("과거 audit에 seal 없이 승인 파일이 있습니다.")
        approval = _load_json(approval_path, "approval")
        if (
            approval.get("build_sha256") != build_manifest.get("build_sha256")
            or approval.get("approval_basis") != "explicit_user_instruction"
            or entry.get("approval_manifest_sha256") != sha256_file(approval_path)
        ):
            raise Phase2AuditError("과거 audit approval 무결성이 다릅니다.")
        assert_public_report_safe(approval)
        approved_pointer = registry.get("approved_audit")
        if not isinstance(approved_pointer, dict) or any(
            approved_pointer.get(key) != expected
            for key, expected in {
                "version": audit_version,
                "build_id": build_id,
                "build_sha256": build_manifest.get("build_sha256"),
                "status": "approved",
            }.items()
        ):
            raise Phase2AuditError("registry approved_audit 포인터가 다릅니다.")
    if entry.get("build_manifest_sha256") != sha256_file(
        public_root / "build_manifest.json"
    ):
        raise Phase2AuditError("registry의 과거 audit build manifest hash가 다릅니다.")
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
        "source_config": source_config_relative,
        "review_queue_units": len(queue),
        "decision_history_entries": len(decisions),
        "sealed": sealed,
        "approved": approved,
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
