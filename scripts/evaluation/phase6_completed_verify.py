# phase6_completed_verify.py - 완료된 Phase 6 단회 평가를 실행 commit과 manifest로 재검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation import phase6_technical as technical

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase6-technical-evaluation-v1.0.0.json"
)
DEFAULT_BUILD_ID = "eval-e8630962cab2"
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IDENTITY_KEYS = (
    "schema_version",
    "evaluation_id",
    "evaluation_version",
    "evaluation_build_id",
    "build_sha256",
    "config_sha256",
    "implementation_hashes",
    "model_file_hashes",
    "blind_source_sha256",
    "preblind_summary_hashes",
    "git_commit",
)


class Phase6CompletedVerifyError(RuntimeError):
    """완료된 Phase 6 결과의 hash chain 또는 공개 경계 위반."""


def _git_bytes(repo_root: Path, revision_path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", revision_path],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise Phase6CompletedVerifyError(
            f"실행 commit blob을 읽지 못했습니다: {revision_path}"
        )
    return completed.stdout


def _identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(IDENTITY_KEYS) - set(value):
        raise Phase6CompletedVerifyError("Phase 6 marker identity가 불완전합니다.")
    return {key: value[key] for key in IDENTITY_KEYS}


def _verify_execution_commit(
    repo_root: Path, identity: Mapping[str, Any], current_hashes: Mapping[str, str]
) -> None:
    commit = identity.get("git_commit")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        raise Phase6CompletedVerifyError("Phase 6 실행 commit 형식이 다릅니다.")
    hashes = identity.get("implementation_hashes")
    if not isinstance(hashes, Mapping) or set(hashes) != set(current_hashes):
        raise Phase6CompletedVerifyError("동결 구현 파일 집합이 실행 marker와 다릅니다.")
    for relative, expected in hashes.items():
        payload = _git_bytes(repo_root, f"{commit}:{relative}")
        if hashlib.sha256(payload).hexdigest() != expected:
            raise Phase6CompletedVerifyError(
                f"실행 commit의 구현 blob hash가 다릅니다: {relative}"
            )
        if not relative.startswith("tests/") and current_hashes[relative] != expected:
            raise Phase6CompletedVerifyError(
                f"현재 실행 구현 hash가 marker와 다릅니다: {relative}"
            )


def _verify_manifest_files(
    root: Path,
    files: Mapping[str, Any],
    *,
    label: str,
    required_mode: int | None = None,
) -> None:
    if root.is_symlink() or not root.is_dir():
        raise Phase6CompletedVerifyError(f"{label} manifest root가 올바르지 않습니다.")
    if not isinstance(files, Mapping) or not files:
        raise Phase6CompletedVerifyError(f"{label} manifest 파일 목록이 없습니다.")
    for relative, meta in files.items():
        if not isinstance(relative, str) or not isinstance(meta, Mapping):
            raise Phase6CompletedVerifyError(f"{label} manifest 형식이 다릅니다.")
        try:
            path = technical._safe_path(root, relative)
        except technical.Phase6TechnicalError as exc:
            raise Phase6CompletedVerifyError(
                f"{label} manifest 경로가 올바르지 않습니다: {relative}"
            ) from exc
        if (
            path.is_symlink()
            or not path.is_file()
            or technical._sha256_file(path) != meta.get("sha256")
            or path.stat().st_size != meta.get("bytes")
            or (
                required_mode is not None
                and stat.S_IMODE(path.stat().st_mode) != required_mode
            )
        ):
            raise Phase6CompletedVerifyError(
                f"{label} manifest 검증에 실패했습니다: {relative}"
            )


def verify_completed(
    repo_root: Path, config_path: Path, *, expected_build_id: str
) -> dict[str, Any]:
    current_context = technical.prepare_context(repo_root, config_path)
    config = current_context["config"]
    private_root = technical._safe_path(
        repo_root,
        config["outputs"]["private_root"].format(
            evaluation_build_id=expected_build_id
        ),
    )
    public_root = technical._safe_path(
        repo_root,
        config["outputs"]["public_root"].format(
            evaluation_build_id=expected_build_id
        ),
    )
    if (
        private_root.is_symlink()
        or not private_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) != 0o700
    ):
        raise Phase6CompletedVerifyError("Phase 6 private root 권한이 다릅니다.")
    completion_path = private_root / "blind_access_completed.json"
    started_path = private_root / "blind_access_started.json"
    global_started_path = technical._safe_path(
        repo_root, config["blind_source"]["consumption_marker"]
    )
    completion = technical._load_json(
        completion_path, "blind completion marker"
    )
    started = technical._load_json(started_path, "blind start marker")
    global_started = technical._load_json(
        global_started_path,
        "global blind start marker",
    )
    identity = _identity(completion)
    if _identity(started) != identity or _identity(global_started) != identity:
        raise Phase6CompletedVerifyError("blind 시작·완료 marker identity가 다릅니다.")
    if (
        identity["evaluation_build_id"] != expected_build_id
        or identity["config_sha256"] != current_context["config_sha256"]
        or identity["blind_source_sha256"] != config["blind_source"]["sha256"]
    ):
        raise Phase6CompletedVerifyError("완료 build의 config·blind identity가 다릅니다.")
    historical_build_inputs = {
        **current_context["build_inputs"],
        "implementation_hashes": identity["implementation_hashes"],
    }
    historical_build_sha256 = technical._sha256_json(historical_build_inputs)
    if (
        identity["build_sha256"] != historical_build_sha256
        or expected_build_id != f"eval-{historical_build_sha256[:12]}"
    ):
        raise Phase6CompletedVerifyError("완료 build fingerprint를 재계산하지 못했습니다.")
    for marker_path in (started_path, completion_path, global_started_path):
        if (
            marker_path.is_symlink()
            or not marker_path.is_file()
            or stat.S_IMODE(marker_path.stat().st_mode) != 0o600
        ):
            raise Phase6CompletedVerifyError("blind marker 권한이 다릅니다.")
    if (
        started.get("status") != "spent_in_progress"
        or global_started.get("status") != "spent_in_progress"
        or started.get("maximum_evaluation_runs") != 1
        or global_started.get("maximum_evaluation_runs") != 1
        or completion.get("status") != "spent_completed"
        or completion.get("rows") != 500
        or completion.get("components") != 350
    ):
        raise Phase6CompletedVerifyError("blind 단회 소비 상태가 다릅니다.")
    _verify_execution_commit(
        repo_root, identity, current_context["implementation_hashes"]
    )
    expected_model_hashes = {
        name: model["required_files"] for name, model in config["models"].items()
    }
    if identity["model_file_hashes"] != expected_model_hashes:
        raise Phase6CompletedVerifyError("완료 build의 모델 파일 계약이 다릅니다.")
    technical._validate_model_hashes(current_context, repo_root)
    expected_preblind = identity["preblind_summary_hashes"]
    if not isinstance(expected_preblind, Mapping) or set(expected_preblind) != set(
        technical.MODEL_SLUGS
    ):
        raise Phase6CompletedVerifyError("preblind summary hash 계약이 다릅니다.")
    for model_name, slug in technical.MODEL_SLUGS.items():
        path = private_root / "preblind" / slug / "summary.json"
        if (
            path.is_symlink()
            or not path.is_file()
            or technical._sha256_file(path) != expected_preblind[model_name]
        ):
            raise Phase6CompletedVerifyError(
                f"preblind summary hash가 다릅니다: {model_name}"
            )

    blind_path = technical._safe_path(
        repo_root, config["blind_source"]["path"]
    )
    if (
        blind_path.is_symlink()
        or not blind_path.is_file()
        or stat.S_IMODE(blind_path.stat().st_mode) != 0o600
        or technical._sha256_file(blind_path) != identity["blind_source_sha256"]
    ):
        raise Phase6CompletedVerifyError("소비된 blind source hash·mode가 다릅니다.")

    aggregate = technical._load_json(
        public_root / "aggregate.json", "Phase 6 aggregate"
    )
    public_manifest = technical._load_json(
        public_root / "build_manifest.json", "Phase 6 public manifest"
    )
    technical._public_leak_scan(aggregate)
    technical._public_leak_scan(public_manifest)
    if (
        aggregate.get("evaluation_build_id") != expected_build_id
        or aggregate.get("status") != "completed"
        or aggregate.get("phase6_completed") is not True
        or aggregate.get("blind_usage", {}).get("status") != "spent_completed"
        or aggregate.get("policy", {}).get("domain_semantics") != "not_measured"
        or aggregate.get("baseline_decision", {}).get("decision")
        != completion.get("baseline_decision")
        or any(aggregate.get("promotion", {}).values())
    ):
        raise Phase6CompletedVerifyError("Phase 6 공개 결정·금지 경계가 다릅니다.")
    _verify_manifest_files(
        public_root,
        public_manifest.get("public_files", {}),
        label="public",
        required_mode=0o644,
    )

    private_manifest = technical._load_json(
        private_root / "private_manifest.json", "Phase 6 private manifest"
    )
    if (
        private_manifest.get("evaluation_build_id") != expected_build_id
        or private_manifest.get("build_sha256") != identity["build_sha256"]
        or private_manifest.get("blind_status") != "spent_completed"
        or private_manifest.get("public_raw_output_allowed") is not False
    ):
        raise Phase6CompletedVerifyError("Phase 6 private manifest 상태가 다릅니다.")
    _verify_manifest_files(
        private_root,
        private_manifest.get("files", {}),
        label="private",
        required_mode=0o600,
    )
    return {
        "status": "verified",
        "evaluation_build_id": expected_build_id,
        "execution_commit": identity["git_commit"],
        "blind_status": "spent_completed",
        "baseline_decision": completion["baseline_decision"],
        "domain_semantics": "not_measured",
        "release_approved": False,
        "application_binding_performed": False,
        "mix20k_v3_1_generated": False,
        "additional_training_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="완료된 Phase 6 자동 기술평가 검증")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--build-id", default=DEFAULT_BUILD_ID)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        result = verify_completed(
            REPO_ROOT, config_path, expected_build_id=args.build_id
        )
    except (OSError, ValueError, technical.Phase6TechnicalError, Phase6CompletedVerifyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
