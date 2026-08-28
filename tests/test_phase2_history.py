# test_phase2_history.py - 과거 audit·staging build의 Git 코드 기반 무결성 검증을 확인한다.

from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.data.errors import Phase2AuditError
from scripts.data.phase2_verify_history import _load_json as load_historical_json
from scripts.data.phase2_verify_history import verify_historical_build
from scripts.data.phase2b_verify_history import (
    _git_blob,
    _verify_hash_map,
    verify_historical_staging,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class HistoricalAuditTests(unittest.TestCase):
    def test_registry_approval_status_must_match_approval_artifact(self) -> None:
        private_root = (
            REPO_ROOT
            / "data/audit/saju_1b_baseline/v1.2.0/build-ca756f3eb89f"
        )
        if not private_root.is_dir():
            self.skipTest("Git 제외 audit build가 없는 환경입니다.")

        def altered_registry(path: Path, label: str) -> dict[str, object]:
            value = copy.deepcopy(load_historical_json(path, label))
            if label == "registry":
                for entry in value["audit_builds"]:  # type: ignore[index]
                    if entry["build_id"] == "build-ca756f3eb89f":
                        entry["status"] = "human_review_required"
            return value

        with (
            patch(
                "scripts.data.phase2_verify_history._load_json",
                side_effect=altered_registry,
            ),
            self.assertRaises(Phase2AuditError),
        ):
            verify_historical_build(
                REPO_ROOT,
                audit_version="v1.2.0",
                build_id="build-ca756f3eb89f",
                implementation_commit=None,
            )

    def test_unsafe_audit_identity_fails_before_path_access(self) -> None:
        with self.assertRaises(Phase2AuditError):
            verify_historical_build(
                REPO_ROOT,
                audit_version="../v1.2.0",
                build_id="build-ca756f3eb89f",
                implementation_commit=None,
            )

    def test_approved_v12_audit_verifies_with_correction_fingerprint(self) -> None:
        private_root = (
            REPO_ROOT
            / "data/audit/saju_1b_baseline/v1.2.0/build-ca756f3eb89f"
        )
        if not private_root.is_dir():
            self.skipTest("Git 제외 audit build가 없는 환경입니다.")
        result = verify_historical_build(
            REPO_ROOT,
            audit_version="v1.2.0",
            build_id="build-ca756f3eb89f",
            implementation_commit=None,
        )
        self.assertTrue(result["sealed"])
        self.assertTrue(result["approved"])
        self.assertEqual(result["review_queue_units"], 300)
        self.assertEqual(result["decision_history_entries"], 300)


class HistoricalStagingTests(unittest.TestCase):
    def test_approved_staging_verifies_without_current_code_fingerprint(self) -> None:
        private_root = (
            REPO_ROOT
            / "data/staging/saju_1b_baseline/v0.1.0/build-109815ee6879"
        )
        if not private_root.is_dir():
            self.skipTest("Git 제외 staging build가 없는 환경입니다.")
        result = verify_historical_staging(
            REPO_ROOT,
            staging_version="v0.1.0",
            build_id="build-109815ee6879",
            implementation_commit=None,
        )
        self.assertEqual(result["record_validation"]["total_rows"], 24_000)
        self.assertEqual(result["record_validation"]["unique_record_ids"], 24_000)
        self.assertEqual(result["record_validation"]["unique_message_hashes"], 24_000)
        self.assertEqual(
            result["record_validation"]["aihub_cross_axis_group_overlap"], 0
        )
        self.assertEqual(result["review_decision_count"], 300)
        self.assertFalse(result["training_promotion_allowed"])

    def test_invalid_commit_and_tampered_artifact_fail_closed(self) -> None:
        with self.assertRaises(Phase2AuditError):
            _git_blob(REPO_ROOT, "--help", "README.md")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact.json"
            artifact.write_text("{}\n", encoding="utf-8")
            expected = hashlib.sha256(artifact.read_bytes()).hexdigest()
            _verify_hash_map(root, {"artifact.json": expected}, "fixture")
            artifact.write_text('{"tampered":true}\n', encoding="utf-8")
            with self.assertRaises(Phase2AuditError):
                _verify_hash_map(root, {"artifact.json": expected}, "fixture")


if __name__ == "__main__":
    unittest.main()
