# phase4_verify_history.py - 과거 Phase 4 v1.0 산출물과 알려진 구현 추적 한계를 재검증한다.

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    load_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    verify_hash_map,
)

HISTORICAL_BUILD = {
    "version": "v1.0.0",
    "build_id": "build-a6813ba3b778",
    "build_sha256": "a6813ba3b778bbf7c663e7f2493ae3465bb35416c5fd4233ad4a913dce54023a",
    "implementation_commit": "72e643eceb70f301a0f0258ddafdb4eb11d4208b",
    "private_manifest_sha256": "2ed5c03ccc2481046c72e5964422cca38f2305daee3e14f742feb36215b49a50",
    "public_manifest_sha256": "7750f462a5cbc3bace25fc931c953f46d5ef23588a964de69a14a87fda08791b",
    "k0_manifest_sha256": "67d6ca3b80dea0b43d0dab033ca24add166bd170ed7143b103827475f1602ab1",
    "k0_config_sha256": "7d3811241801a41a317208f1a34459d7f20820f79059fbec3eaa6be0e7f22b42",
    "unreachable_at_commit": {
        "scripts/preflight/errors.py": "eb19d98acc11f0b58682365043f7d40d7efb24c7d63bc356f593d12485cd6180",
        "scripts/preflight/phase4_preflight.py": "1cbbd1530010b4bec2e9ad089a3b863a83183cdd2a55dd3fd16c738c17ad0bab",
    },
}


def _git_blob(repo_root: Path, commit: str, relative: str) -> bytes | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
    )
    return completed.stdout if completed.returncode == 0 else None


def verify_historical_phase4(repo_root: Path) -> dict[str, Any]:
    fixed = HISTORICAL_BUILD
    build_id = fixed["build_id"]
    private_root = (
        repo_root / "data/derived/saju_1b_baseline/v1.0.0" / build_id
    )
    public_root = (
        repo_root / "data/reports/saju_1b_baseline/preflight/v1.0.0" / build_id
    )
    k0_root = repo_root / "runs/K0-INSTRUCT/v1.0.0" / build_id
    private_path = private_root / "build_manifest.json"
    public_path = public_root / "build_manifest.json"
    k0_path = k0_root / "run_manifest.json"
    k0_config_path = k0_root / "run_config.json"
    expected_files = {
        private_path: fixed["private_manifest_sha256"],
        public_path: fixed["public_manifest_sha256"],
        k0_path: fixed["k0_manifest_sha256"],
        k0_config_path: fixed["k0_config_sha256"],
    }
    for path, expected in expected_files.items():
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise Phase4Error(f"과거 Phase 4 고정 파일 hash가 다릅니다: {path}")
    private = load_json(private_path, "과거 Phase 4 private manifest")
    public = load_json(public_path, "과거 Phase 4 public manifest")
    k0 = load_json(k0_path, "과거 Phase 4 K0 manifest")
    run_config = load_json(k0_config_path, "과거 Phase 4 K0 config")
    if (
        private.get("build_id") != build_id
        or private.get("build_sha256") != fixed["build_sha256"]
        or sha256_json(private.get("build_inputs")) != fixed["build_sha256"]
        or public.get("build_inputs") != private.get("build_inputs")
        or public.get("status") != "gates_a_b_c_passed"
        or public.get("training_promotion_allowed") is not False
        or k0.get("status") != "passed"
        or k0.get("training_promotion_allowed") is not False
        or run_config.get("training_promotion_allowed") is not False
    ):
        raise Phase4Error("과거 Phase 4 identity/Gate 계약이 다릅니다.")
    verify_hash_map(private_root, private.get("artifact_sha256"), "과거 Phase 4 private")
    verify_hash_map(public_root, public.get("artifact_sha256"), "과거 Phase 4 public")
    verify_hash_map(k0_root, k0.get("artifact_sha256"), "과거 Phase 4 K0")
    if len(read_jsonl(k0_root / "results.jsonl", "과거 K0 results")) != 720:
        raise Phase4Error("과거 K0는 정확히 720case여야 합니다.")

    implementation = private["build_inputs"].get("implementation_hashes")
    if not isinstance(implementation, dict):
        raise Phase4Error("과거 Phase 4 implementation hash map이 없습니다.")
    reachable = 0
    unreachable: dict[str, str] = {}
    for relative, expected in implementation.items():
        payload = _git_blob(repo_root, fixed["implementation_commit"], relative)
        if payload is not None and hashlib.sha256(payload).hexdigest() == expected:
            reachable += 1
            continue
        if fixed["unreachable_at_commit"].get(relative) != expected:
            raise Phase4Error(f"과거 Phase 4 구현 blob이 예상 밖으로 불일치합니다: {relative}")
        unreachable[relative] = expected
    if unreachable != fixed["unreachable_at_commit"]:
        raise Phase4Error("과거 Phase 4 알려진 구현 추적 한계 집합이 다릅니다.")
    return {
        "status": "verified_with_known_implementation_traceability_limit",
        "version": fixed["version"],
        "build_id": build_id,
        "build_sha256": fixed["build_sha256"],
        "artifact_hash_chains_verified": True,
        "implementation_hashes_total": len(implementation),
        "implementation_hashes_reachable_at_commit": reachable,
        "implementation_hashes_not_reachable_at_commit": unreachable,
        "training_promotion_allowed": False,
    }
