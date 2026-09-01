# test_phase6_completed_verify.py - 완료된 Phase 6 결과의 공개·실행 commit 검증을 확인한다.

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import phase6_completed_verify as completed

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = (
    REPO_ROOT
    / "data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2"
)


class Phase6CompletedVerifyTests(unittest.TestCase):
    def test_committed_public_result_has_no_raw_content(self) -> None:
        aggregate = json.loads(
            (PUBLIC_ROOT / "aggregate.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (PUBLIC_ROOT / "build_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(aggregate["status"], "completed")
        self.assertEqual(
            aggregate["baseline_decision"]["decision"],
            "AUTOMATED_REPAIR_REQUIRED",
        )
        self.assertEqual(aggregate["policy"]["domain_semantics"], "not_measured")
        self.assertFalse(any(aggregate["promotion"].values()))
        self.assertFalse(manifest["private_content_included"])
        self.assertFalse(manifest["raw_outputs_included"])

    def test_registry_points_to_completed_automatic_evaluation(self) -> None:
        registry = json.loads(
            (
                REPO_ROOT
                / "configs/data_versions/saju_1b_baseline/registry.json"
            ).read_text(encoding="utf-8")
        )
        approved = registry["approved_phase6_technical_evaluation"]
        self.assertEqual(approved["build_id"], "eval-e8630962cab2")
        self.assertEqual(approved["blind_status"], "spent_completed")
        self.assertEqual(approved["blind_consumption_runs"], 1)
        self.assertEqual(
            approved["baseline_decision"], "AUTOMATED_REPAIR_REQUIRED"
        )
        self.assertEqual(approved["domain_semantics"], "not_measured")
        self.assertTrue(approved["phase6_completed"])
        self.assertFalse(approved["release_approved"])
        self.assertFalse(approved["application_binding_performed"])
        self.assertFalse(approved["mix20k_v3_1_generated"])
        self.assertFalse(approved["additional_training_performed"])
        split = registry["approved_evaluation_split"]
        self.assertTrue(split["blind_source_test_inspected"])
        self.assertEqual(split["blind_source_test_status"], "spent_completed")
        self.assertEqual(split["blind_consumption_runs"], 1)

    def test_execution_commit_blob_must_match_marker_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = b"frozen implementation\n"
            identity = {
                "git_commit": "1" * 40,
                "implementation_hashes": {
                    "scripts/frozen.py": hashlib.sha256(payload).hexdigest()
                },
            }
            with patch.object(completed, "_git_bytes", return_value=payload):
                completed._verify_execution_commit(
                    root, identity, identity["implementation_hashes"]
                )
            with (
                patch.object(completed, "_git_bytes", return_value=b"tampered\n"),
                self.assertRaises(completed.Phase6CompletedVerifyError),
            ):
                completed._verify_execution_commit(
                    root, identity, identity["implementation_hashes"]
                )

    def test_current_lifecycle_test_may_change_but_execution_code_may_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = {
                "scripts/frozen.py": b"execution code\n",
                "tests/test_frozen.py": b"pre-execution assertion\n",
            }
            identity = {
                "git_commit": "1" * 40,
                "implementation_hashes": {
                    relative: hashlib.sha256(payload).hexdigest()
                    for relative, payload in frozen.items()
                },
            }

            def git_payload(_repo_root: Path, revision_path: str) -> bytes:
                return frozen[revision_path.split(":", maxsplit=1)[1]]

            current = dict(identity["implementation_hashes"])
            current["tests/test_frozen.py"] = hashlib.sha256(
                b"post-completion assertion\n"
            ).hexdigest()
            with patch.object(completed, "_git_bytes", side_effect=git_payload):
                completed._verify_execution_commit(root, identity, current)

            current["scripts/frozen.py"] = hashlib.sha256(b"changed code\n").hexdigest()
            with (
                patch.object(completed, "_git_bytes", side_effect=git_payload),
                self.assertRaises(completed.Phase6CompletedVerifyError),
            ):
                completed._verify_execution_commit(root, identity, current)

    def test_manifest_verification_rejects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "artifact.json"
            path.write_bytes(b"one\n")
            files = {
                "artifact.json": {
                    "sha256": hashlib.sha256(b"one\n").hexdigest(),
                    "bytes": 4,
                }
            }
            completed._verify_manifest_files(root, files, label="test")
            path.chmod(0o600)
            completed._verify_manifest_files(
                root, files, label="test", required_mode=0o600
            )
            path.chmod(0o644)
            with self.assertRaises(completed.Phase6CompletedVerifyError):
                completed._verify_manifest_files(
                    root, files, label="test", required_mode=0o600
                )
            path.write_bytes(b"two\n")
            with self.assertRaises(completed.Phase6CompletedVerifyError):
                completed._verify_manifest_files(root, files, label="test")
            escaped = {"../artifact.json": files["artifact.json"]}
            with self.assertRaises(completed.Phase6CompletedVerifyError):
                completed._verify_manifest_files(root, escaped, label="test")


if __name__ == "__main__":
    unittest.main()
