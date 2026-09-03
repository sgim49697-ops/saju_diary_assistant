# test_mix2k_v4_teacher_recovery.py - MIX2K v4 1회성 checkpoint 복구의 SHA·이력 계약을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.data import mix2k_v4_teacher_recovery as recovery
from scripts.data.mix2k_v4_contracts import sha256_bytes


def _attempt(sequence: int, *, deterministic_pass: bool) -> dict[str, object]:
    return {
        "provider": "codex",
        "provider_call_sequence": sequence,
        "deterministic_pass": deterministic_pass,
    }


def _pre_state() -> dict[str, object]:
    return {
        "schema_version": "1.3.0",
        "runner_sha256": recovery.EXPECTED_RUNNER_SHA256,
        "contracts_sha256": recovery.EXPECTED_CONTRACTS_SHA256,
        "provider_calls": recovery.EXPECTED_PROVIDER_CALLS,
        "records": {
            recovery.FAILED_RECORD_ID: {
                "status": "failed",
                "feedback": recovery.OLD_FAILED_FEEDBACK,
                "rewrites_used": 2,
                "duplicate_rewrites_used": 0,
                "current_draft": None,
                "accepted": None,
                "review_attempts": [],
                "draft_attempts": [
                    _attempt(56, deterministic_pass=False),
                    _attempt(58, deterministic_pass=False),
                    _attempt(60, deterministic_pass=False),
                ],
            },
            recovery.PENDING_RECORD_ID: {
                "status": "needs_draft",
                "feedback": recovery.OLD_PENDING_FEEDBACK,
                "rewrites_used": 2,
                "duplicate_rewrites_used": 0,
                "current_draft": None,
                "accepted": None,
                "review_attempts": [],
                "draft_attempts": [
                    _attempt(58, deterministic_pass=False),
                    _attempt(60, deterministic_pass=False),
                ],
            },
            "unrelated": {"status": "accepted", "sentinel": [1, 2, 3]},
        },
    }


class Mix2KV4TeacherRecoveryTests(unittest.TestCase):
    def test_recovery_changes_only_status_feedback_and_audit_event(self) -> None:
        before = _pre_state()
        before_payload = recovery._json_bytes(before)
        with patch.object(
            recovery,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            after = recovery.build_recovered_state(before, before_payload)

        self.assertEqual(before, _pre_state())
        self.assertEqual(
            after["records"][recovery.FAILED_RECORD_ID]["status"],
            "needs_draft",
        )
        self.assertEqual(
            after["records"][recovery.FAILED_RECORD_ID]["feedback"],
            recovery.NEW_FAILED_FEEDBACK,
        )
        self.assertEqual(
            after["records"][recovery.PENDING_RECORD_ID]["feedback"],
            recovery.NEW_PENDING_FEEDBACK,
        )
        for record_id in (recovery.FAILED_RECORD_ID, recovery.PENDING_RECORD_ID):
            for field in (
                "rewrites_used",
                "duplicate_rewrites_used",
                "draft_attempts",
                "review_attempts",
                "current_draft",
                "accepted",
            ):
                self.assertEqual(
                    after["records"][record_id][field],
                    before["records"][record_id][field],
                )
        self.assertEqual(
            after["records"]["unrelated"], before["records"]["unrelated"]
        )

    def test_recover_is_locked_hash_bound_and_idempotent(self) -> None:
        before = _pre_state()
        before_payload = recovery._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory).resolve()
            target = output_root / recovery.TARGET_NAME
            target.mkdir(mode=0o700)
            (target / ".pipeline.lock").write_bytes(b"lock\n")
            (target / "pipeline_state.json").write_bytes(before_payload)
            with (
                patch.object(recovery, "DEFAULT_OUTPUT_ROOT", output_root),
                patch.object(
                    recovery, "EXPECTED_PRE_STATE_SHA256", before_sha
                ),
            ):
                first = recovery.recover(target)
                second = recovery.recover(target)

            self.assertFalse(first["already_applied"])
            self.assertTrue(second["already_applied"])
            live = json.loads((target / "pipeline_state.json").read_bytes())
            self.assertEqual(
                live["operator_recoveries"][0]["recovery_id"],
                recovery.RECOVERY_ID,
            )
            self.assertEqual(
                (target / recovery.BEFORE_STATE_RELATIVE).read_bytes(),
                before_payload,
            )

    def test_bundle_requires_real_later_draft_and_review_for_finalization(
        self,
    ) -> None:
        before = _pre_state()
        before_payload = recovery._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / recovery.TARGET_NAME
            (target / recovery.RECOVERY_DIR_RELATIVE).mkdir(parents=True)
            with patch.object(
                recovery, "EXPECTED_PRE_STATE_SHA256", before_sha
            ):
                after = recovery.build_recovered_state(before, before_payload)
                after_payload = recovery._json_bytes(after)
                manifest = recovery._expected_manifest(after_payload)
                (target / recovery.BEFORE_STATE_RELATIVE).write_bytes(before_payload)
                (target / recovery.AFTER_STATE_RELATIVE).write_bytes(after_payload)
                (target / recovery.RECOVERY_MANIFEST_RELATIVE).write_bytes(
                    recovery._json_bytes(manifest)
                )
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError, "실제 draft"
                ):
                    recovery.validate_recovery_bundle(
                        target,
                        after,
                        require_completed_provider_passes=True,
                    )

                completed = deepcopy(after)
                completed["provider_calls"] = 64
                for offset, record_id in enumerate(
                    (recovery.FAILED_RECORD_ID, recovery.PENDING_RECORD_ID)
                ):
                    record = completed["records"][record_id]
                    draft = {"answer": f"교정 초안 {offset}"}
                    record["draft_attempts"].append(
                        {
                            **_attempt(61 + offset * 2, deterministic_pass=True),
                            "draft": draft,
                        }
                    )
                    review = {"decision": "PASS"}
                    record["review_attempts"].append(
                        {
                            "provider": "codex",
                            "provider_call_sequence": 62 + offset * 2,
                            "review": review,
                        }
                    )
                    record["current_draft"] = draft
                    record["accepted"] = {"draft": draft, "review": review}
                    record["status"] = "accepted"
                report = recovery.validate_recovery_bundle(
                    target,
                    completed,
                    require_completed_provider_passes=True,
                )
            self.assertTrue(
                report["provider_draft_and_separate_review_passed"]
            )


if __name__ == "__main__":
    unittest.main()
