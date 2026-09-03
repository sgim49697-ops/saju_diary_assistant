# test_mix2k_v4_teacher_recovery.py - MIX2K v4 1회성 checkpoint 복구의 SHA·이력 계약을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.data import mix2k_v4_teacher_recovery as recovery
from scripts.data import mix2k_v4_teacher_recovery_call148 as recovery_call148
from scripts.data import mix2k_v4_teacher_recovery_call149 as recovery_call149
from scripts.data import mix2k_v4_teacher_recovery_call154 as recovery_call154
from scripts.data import mix2k_v4_teacher_recovery_call174 as recovery_call174
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


def _call148_pre_state() -> dict[str, object]:
    records: dict[str, object] = {
        recovery_call148.FIRST_RECOVERY_OVERFLOW_ID: {
            "status": "accepted",
            "draft_attempts": [
                _attempt(sequence, deterministic_pass=sequence == 61)
                for sequence in (56, 58, 60, 61)
            ],
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
        }
    }
    for record_id, expected in recovery_call148._expected_before_contract().items():
        draft_attempts = [
            _attempt(140 + index, deterministic_pass=False)
            for index in range(expected["draft_attempts"])
        ]
        draft_attempts[-1]["provider_call_sequence"] = 148
        review_attempts = [
            {
                "provider": "codex",
                "provider_call_sequence": 38 + index,
                "review": {"decision": "FAIL"},
            }
            for index in range(expected["review_attempts"])
        ]
        records[record_id] = {
            "status": expected["status"],
            "feedback": expected["feedback"],
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
            "current_draft": {"answer": "기존 초안"},
            "accepted": None,
            "draft_attempts": draft_attempts,
            "review_attempts": review_attempts,
        }
    return {
        "schema_version": "1.3.0",
        "runner_sha256": recovery_call148.EXPECTED_RUNNER_SHA256,
        "contracts_sha256": recovery_call148.EXPECTED_CONTRACTS_SHA256,
        "provider_calls": 148,
        "operator_recoveries": [recovery_call148.first_recovery_event()],
        "records": records,
    }


def _call149_pre_state() -> dict[str, object]:
    records: dict[str, object] = {}
    for record_id in recovery_call149.FAILED_RECORD_IDS:
        current_draft = {"answer": f"기존 초안 {record_id}"}
        drafts = [
            {
                **_attempt(sequence, deterministic_pass=True),
                "execution_pass": "draft",
                "draft": current_draft,
            }
            for sequence in (100, 120, 148)
        ]
        reviews = [
            {
                "provider": "codex",
                "provider_call_sequence": sequence,
                "execution_pass": "review",
                "review_mode": "same_provider_separate_pass",
                "review": {"decision": "FAIL"},
            }
            for sequence in (101, 121)
        ]
        reviews.append(
            {
                "provider": "codex",
                "provider_call_sequence": 149,
                "execution_pass": "review",
                "review_mode": "same_provider_separate_pass",
                "review": recovery_call149._expected_last_review(record_id),
            }
        )
        records[record_id] = {
            "status": "failed",
            "feedback": recovery_call149.OLD_FEEDBACK_BY_ID[record_id],
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
            "current_draft": current_draft,
            "accepted": None,
            "draft_attempts": drafts,
            "review_attempts": reviews,
        }
    return {
        "schema_version": "1.3.0",
        "runner_sha256": recovery_call149.EXPECTED_RUNNER_SHA256,
        "contracts_sha256": recovery_call149.EXPECTED_CONTRACTS_SHA256,
        "provider_calls": 149,
        "operator_recoveries": [
            recovery_call149.first_recovery_event(),
            recovery_call149.second_recovery_event(),
        ],
        "records": records,
    }


def _call154_pre_state() -> dict[str, object]:
    records: dict[str, object] = {}
    for record_id in recovery_call154.AFFECTED_RECORD_IDS:
        failed = record_id in recovery_call154.FAILED_RECORD_IDS
        draft_count = 3 if failed else 2
        review_count = 1 if failed else 0
        feedback = (
            recovery_call154.OLD_FAILED_FEEDBACK
            if failed
            else recovery_call154.OLD_PENDING_FEEDBACK
        )
        current_draft = {"record_id": record_id, "answer": "기존 초안"} if failed else None
        drafts = [
            {
                "provider": "codex",
                "provider_call_sequence": 100 + index,
                "execution_pass": "draft",
                "attempt": index + 1,
                "deterministic_pass": True,
                "draft": {"record_id": record_id, "answer": "과거 초안"},
            }
            for index in range(draft_count)
        ]
        drafts[-1].update(
            {
                "provider_call_sequence": 154,
                "deterministic_pass": False,
                "deterministic_error": recovery_call154.OLD_FAILED_FEEDBACK,
            }
        )
        records[record_id] = {
            "spec_sha256": f"spec-{record_id}",
            "status": "failed" if failed else "needs_draft",
            "feedback": feedback,
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
            "current_draft": current_draft,
            "accepted": None,
            "draft_attempts": drafts,
            "review_attempts": [
                {
                    "provider": "codex",
                    "provider_call_sequence": 120,
                    "execution_pass": "review",
                    "attempt": 1,
                    "review_mode": "same_provider_separate_pass",
                    "review": {"decision": "FAIL", "record_id": record_id},
                }
            ][:review_count],
        }
    return {
        "schema_version": "1.3.0",
        "runner_sha256": recovery_call154.EXPECTED_RUNNER_SHA256,
        "contracts_sha256": recovery_call154.EXPECTED_CONTRACTS_SHA256,
        "provider_calls": 154,
        "operator_recoveries": recovery_call154._prior_events(),
        "records": records,
    }


def _call174_draft_attempt(
    record_id: str,
    *,
    attempt: int,
    sequence: int,
    deterministic_pass: bool = True,
    deterministic_error: str | None = None,
) -> dict[str, object]:
    draft = {
        "record_id": record_id,
        "answer": f"synthetic draft {record_id} #{attempt}",
        "used_fact_paths": [],
        "used_fact_values": [],
        "soft_interpretation_used": False,
        "limitations": [],
        "self_check": "PASS",
    }
    return {
        "assigned_provider": "codex",
        "provider": "codex",
        "fallback_used": False,
        "execution_pass": "draft",
        "provider_call_sequence": sequence,
        "attempt": attempt,
        "provider_draft": deepcopy(draft),
        "draft": draft,
        "particle_normalized": False,
        "particle_normalizer_version": None,
        "layout_normalized": False,
        "layout_normalizer_version": None,
        "deterministic_pass": deterministic_pass,
        "deterministic_error": deterministic_error,
    }


def _call174_review_attempt(
    record_id: str,
    *,
    attempt: int,
    sequence: int,
    decision: str = "PASS",
) -> dict[str, object]:
    review = {
        "record_id": record_id,
        "decision": decision,
        "failure_codes": [],
        "fact_errors": [],
        "style_notes": [],
        "rewrite_instructions": "",
    }
    return {
        "assigned_provider": "codex",
        "provider": "codex",
        "fallback_used": False,
        "execution_pass": "review",
        "provider_call_sequence": sequence,
        "attempt": attempt,
        "review_mode": "same_provider_separate_pass",
        "review": review,
    }


def _call174_accepted(
    draft_attempt: dict[str, object], review_attempt: dict[str, object]
) -> dict[str, object]:
    return {
        "assigned_drafter": draft_attempt["assigned_provider"],
        "assigned_reviewer": review_attempt["assigned_provider"],
        "draft_provider": draft_attempt["provider"],
        "review_provider": review_attempt["provider"],
        "review_mode": review_attempt["review_mode"],
        "fallback_used": bool(
            draft_attempt["fallback_used"] or review_attempt["fallback_used"]
        ),
        "draft": deepcopy(draft_attempt["draft"]),
        "review": deepcopy(review_attempt["review"]),
    }


def _call174_pre_state() -> dict[str, object]:
    records: dict[str, object] = {}
    for record_id, contract in recovery_call174.RECOVERY_PAIR_CONTRACTS.items():
        draft_count, draft_sequence, review_count, review_sequence = contract
        drafts = [
            _call174_draft_attempt(
                record_id,
                attempt=index,
                sequence=(
                    draft_sequence
                    if index == draft_count
                    else draft_sequence - (draft_count - index) * 2
                ),
            )
            for index in range(1, draft_count + 1)
        ]
        reviews = [
            _call174_review_attempt(
                record_id,
                attempt=index,
                sequence=(
                    review_sequence
                    if index == review_count
                    else review_sequence - (review_count - index) * 2
                ),
                decision="PASS" if index == review_count else "FAIL",
            )
            for index in range(1, review_count + 1)
        ]
        records[record_id] = {
            "spec_sha256": f"spec-{record_id}",
            "status": "accepted",
            "feedback": "",
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
            "draft_attempts": drafts,
            "review_attempts": reviews,
            "current_draft": deepcopy(drafts[-1]["draft"]),
            "accepted": _call174_accepted(drafts[-1], reviews[-1]),
        }

    duplicate_review = records[recovery_call174.DUPLICATE_REVIEW_RECORD_ID]
    duplicate_review_draft = _call174_draft_attempt(
        recovery_call174.DUPLICATE_REVIEW_RECORD_ID,
        attempt=len(duplicate_review["draft_attempts"]) + 1,
        sequence=recovery_call174.EXPECTED_PROVIDER_CALLS,
    )
    duplicate_review["duplicate_rewrites_used"] = 1
    duplicate_review["draft_attempts"].append(duplicate_review_draft)
    duplicate_review["status"] = "needs_review"
    duplicate_review["current_draft"] = deepcopy(duplicate_review_draft["draft"])
    duplicate_review["accepted"] = None

    strength = records[recovery_call174.STRENGTH_RECORD_ID]
    failed_duplicate = _call174_draft_attempt(
        recovery_call174.STRENGTH_RECORD_ID,
        attempt=len(strength["draft_attempts"]) + 1,
        sequence=recovery_call174.EXPECTED_PROVIDER_CALLS,
        deterministic_pass=False,
        deterministic_error=recovery_call174.OLD_STRENGTH_FEEDBACK,
    )
    strength["duplicate_rewrites_used"] = 1
    strength["draft_attempts"].append(failed_duplicate)
    strength["status"] = "failed"
    strength["feedback"] = recovery_call174.OLD_STRENGTH_FEEDBACK
    strength["accepted"] = None

    for record_id in recovery_call174.RELATION_RECORD_IDS:
        relation = records[record_id]
        relation["duplicate_rewrites_used"] = 1
        relation["status"] = "needs_draft"
        relation["feedback"] = recovery_call174.OLD_RELATION_FEEDBACK_BY_ID[
            record_id
        ]
        relation["accepted"] = None

    return {
        "schema_version": "1.3.0",
        "runner_sha256": recovery_call174.EXPECTED_RUNNER_SHA256,
        "contracts_sha256": recovery_call174.EXPECTED_CONTRACTS_SHA256,
        "provider_calls": recovery_call174.EXPECTED_PROVIDER_CALLS,
        "operator_recoveries": recovery_call174._prior_events(),
        "records": records,
    }


def _call174_completed_record(
    checkpoint: dict[str, object],
    *,
    record_id: str,
    draft_sequence: int,
    review_sequence: int,
) -> dict[str, object]:
    completed = deepcopy(checkpoint)
    draft = _call174_draft_attempt(
        record_id,
        attempt=len(completed["draft_attempts"]) + 1,
        sequence=draft_sequence,
    )
    review = _call174_review_attempt(
        record_id,
        attempt=len(completed["review_attempts"]) + 1,
        sequence=review_sequence,
    )
    completed["draft_attempts"].append(draft)
    completed["review_attempts"].append(review)
    completed["status"] = "accepted"
    completed["feedback"] = ""
    completed["current_draft"] = deepcopy(draft["draft"])
    completed["accepted"] = _call174_accepted(draft, review)
    return completed


def _call174_add_duplicate_pass(
    record: dict[str, object], *, draft_sequence: int, review_sequence: int
) -> None:
    record_id = str(record["current_draft"]["record_id"])
    record["duplicate_rewrites_used"] += 1
    draft = _call174_draft_attempt(
        record_id,
        attempt=len(record["draft_attempts"]) + 1,
        sequence=draft_sequence,
    )
    review = _call174_review_attempt(
        record_id,
        attempt=len(record["review_attempts"]) + 1,
        sequence=review_sequence,
    )
    record["draft_attempts"].append(draft)
    record["review_attempts"].append(review)
    record["status"] = "accepted"
    record["feedback"] = ""
    record["current_draft"] = deepcopy(draft["draft"])
    record["accepted"] = _call174_accepted(draft, review)


def _call174_payload_contract_fixture() -> tuple[
    dict[str, object], dict[str, object], dict[str, dict[str, object]]
]:
    checkpoint = _call174_pre_state()
    specs: dict[str, dict[str, object]] = {}
    for record_id, record in checkpoint["records"].items():
        spec = {"id": record_id, "drafter": "codex", "reviewer": "codex"}
        specs[record_id] = spec
        record["spec_sha256"] = sha256_bytes(
            recovery_call174.canonical_json_bytes(spec)
        )

    current = deepcopy(checkpoint)
    strength = _call174_completed_record(
        current["records"][recovery_call174.STRENGTH_RECORD_ID],
        record_id=recovery_call174.STRENGTH_RECORD_ID,
        draft_sequence=175,
        review_sequence=176,
    )
    _call174_add_duplicate_pass(
        strength,
        draft_sequence=177,
        review_sequence=178,
    )
    current["records"][recovery_call174.STRENGTH_RECORD_ID] = strength
    return current, checkpoint, specs


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

    def test_call148_recovery_preserves_attempts_and_rewrite_counters(self) -> None:
        before = _call148_pre_state()
        before_payload = recovery_call148._json_bytes(before)
        with patch.object(
            recovery_call148,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            after = recovery_call148.build_recovered_state(before, before_payload)

        self.assertEqual(before["provider_calls"], after["provider_calls"])
        self.assertEqual(len(after["operator_recoveries"]), 2)
        for record_id in recovery_call148.AFFECTED_RECORD_IDS:
            self.assertEqual(
                after["records"][record_id]["draft_attempts"],
                before["records"][record_id]["draft_attempts"],
            )
            self.assertEqual(
                after["records"][record_id]["review_attempts"],
                before["records"][record_id]["review_attempts"],
            )
            self.assertEqual(after["records"][record_id]["rewrites_used"], 2)
        for record_id in recovery_call148.FAILED_RECORD_IDS:
            self.assertEqual(after["records"][record_id]["status"], "needs_draft")

    def test_call148_recover_resumes_prepared_bundle_after_crash(self) -> None:
        before = _call148_pre_state()
        before_payload = recovery_call148._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory).resolve()
            target = output_root / recovery_call148.TARGET_NAME
            target.mkdir(mode=0o700)
            (target / ".pipeline.lock").write_bytes(b"lock\n")
            (target / "pipeline_state.json").write_bytes(before_payload)
            (target / recovery_call148.RECOVERY_DIR_RELATIVE).mkdir(parents=True)
            with patch.object(
                recovery_call148, "EXPECTED_PRE_STATE_SHA256", before_sha
            ):
                after = recovery_call148.build_recovered_state(
                    before, before_payload
                )
                after_payload = recovery_call148._json_bytes(after)
                manifest = recovery_call148._expected_manifest(after_payload)
                (target / recovery_call148.BEFORE_STATE_RELATIVE).write_bytes(
                    before_payload
                )
                (target / recovery_call148.AFTER_STATE_RELATIVE).write_bytes(
                    after_payload
                )
                (target / recovery_call148.RECOVERY_MANIFEST_RELATIVE).write_bytes(
                    recovery_call148._json_bytes(manifest)
                )
                with (
                    patch.object(
                        recovery_call148, "DEFAULT_OUTPUT_ROOT", output_root
                    ),
                    patch.object(
                        recovery_call148,
                        "validate_first_recovery_bundle",
                        return_value={"recovery_id": recovery.RECOVERY_ID},
                    ),
                ):
                    report = recovery_call148.recover(target)

            self.assertTrue(report["resumed_prepared_bundle"])
            self.assertFalse(report["already_applied"])
            self.assertEqual(
                (target / "pipeline_state.json").read_bytes(), after_payload
            )

    def test_recovery_chain_requires_exact_known_attempt_overflows(self) -> None:
        first = recovery_call148.first_recovery_event()
        second = recovery_call148._recovery_event()
        records = {
            record_id: {
                "draft_attempts": [object()] * (4 if record_id != recovery_call148.STRENGTH_PENDING_ID else 3),
                "rewrites_used": 2,
                "duplicate_rewrites_used": 0,
            }
            for record_id in (
                recovery_call148.FIRST_RECOVERY_OVERFLOW_ID,
                *recovery_call148.AFFECTED_RECORD_IDS,
            )
        }
        state = {"operator_recoveries": [first, second], "records": records}
        with (
            patch.object(
                recovery_call148,
                "validate_first_recovery_bundle",
                return_value={"recovery_id": recovery.RECOVERY_ID},
            ),
            patch.object(
                recovery_call148,
                "validate_recovery_bundle",
                return_value={"recovery_id": recovery_call148.RECOVERY_ID},
            ),
            patch.object(
                recovery_call148.os.path, "lexists", return_value=True
            ),
        ):
            report = recovery_call148.validate_recovery_chain(
                Path("/tmp") / recovery_call148.TARGET_NAME,
                state,
                require_completed_provider_passes=True,
            )
        self.assertEqual(
            [item["recovery_id"] for item in report["recoveries"]],
            [recovery.RECOVERY_ID, recovery_call148.RECOVERY_ID],
        )

        forged = deepcopy(state)
        forged["records"][recovery_call148.STRENGTH_PENDING_ID][
            "draft_attempts"
        ].append(object())
        with (
            patch.object(
                recovery_call148,
                "validate_first_recovery_bundle",
                return_value={"recovery_id": recovery.RECOVERY_ID},
            ),
            patch.object(
                recovery_call148,
                "validate_recovery_bundle",
                return_value={"recovery_id": recovery_call148.RECOVERY_ID},
            ),
            patch.object(
                recovery_call148.os.path, "lexists", return_value=True
            ),
            self.assertRaisesRegex(
                recovery.Mix2KV4RecoveryError, "예외 집합"
            ),
        ):
            recovery_call148.validate_recovery_chain(
                Path("/tmp") / recovery_call148.TARGET_NAME,
                forged,
                require_completed_provider_passes=True,
            )

    def test_call149_recovery_changes_only_status_feedback_and_event(self) -> None:
        before = _call149_pre_state()
        before_payload = recovery_call149._json_bytes(before)
        with patch.object(
            recovery_call149,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            after = recovery_call149.build_recovered_state(before, before_payload)

        self.assertEqual(before, _call149_pre_state())
        self.assertEqual(before["provider_calls"], after["provider_calls"])
        self.assertEqual(len(after["operator_recoveries"]), 3)
        for record_id in recovery_call149.FAILED_RECORD_IDS:
            self.assertEqual(after["records"][record_id]["status"], "needs_draft")
            self.assertEqual(
                after["records"][record_id]["feedback"],
                recovery_call149.NEW_FEEDBACK_BY_ID[record_id],
            )
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

    def test_call149_bundle_requires_new_draft_and_later_review(self) -> None:
        before = _call149_pre_state()
        before_payload = recovery_call149._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / recovery_call149.TARGET_NAME
            (target / recovery_call149.RECOVERY_DIR_RELATIVE).mkdir(parents=True)
            with patch.object(
                recovery_call149, "EXPECTED_PRE_STATE_SHA256", before_sha
            ):
                after = recovery_call149.build_recovered_state(
                    before, before_payload
                )
                after_payload = recovery_call149._json_bytes(after)
                manifest = recovery_call149._expected_manifest(after_payload)
                (target / recovery_call149.BEFORE_STATE_RELATIVE).write_bytes(
                    before_payload
                )
                (target / recovery_call149.AFTER_STATE_RELATIVE).write_bytes(
                    after_payload
                )
                (target / recovery_call149.RECOVERY_MANIFEST_RELATIVE).write_bytes(
                    recovery_call149._json_bytes(manifest)
                )
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError, "새 draft"
                ):
                    recovery_call149.validate_recovery_bundle(
                        target,
                        after,
                        require_completed_provider_passes=True,
                    )

                completed = deepcopy(after)
                completed["provider_calls"] = 151
                for record_id in recovery_call149.FAILED_RECORD_IDS:
                    record = completed["records"][record_id]
                    draft = {"answer": f"교정 초안 {record_id}"}
                    record["draft_attempts"].append(
                        {
                            "provider": "codex",
                            "provider_call_sequence": 150,
                            "execution_pass": "draft",
                            "deterministic_pass": True,
                            "draft": draft,
                        }
                    )
                    review = {"decision": "PASS"}
                    record["review_attempts"].append(
                        {
                            "provider": "codex",
                            "provider_call_sequence": 151,
                            "execution_pass": "review",
                            "review_mode": "same_provider_separate_pass",
                            "review": review,
                        }
                    )
                    record["current_draft"] = draft
                    record["accepted"] = {
                        "draft_provider": "codex",
                        "review_provider": "codex",
                        "review_mode": "same_provider_separate_pass",
                        "draft": draft,
                        "review": review,
                    }
                    record["status"] = "accepted"
                report = recovery_call149.validate_recovery_bundle(
                    target,
                    completed,
                    require_completed_provider_passes=True,
                )
                malformed = deepcopy(completed)
                malformed_record = malformed["records"][
                    recovery_call149.FAILED_RECORD_IDS[0]
                ]
                malformed_record["current_draft"] = None
                malformed_record["draft_attempts"][-1].pop("draft")
                malformed_record["accepted"].pop("draft")
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError, "draft"
                ):
                    recovery_call149.validate_recovery_bundle(
                        target,
                        malformed,
                        require_completed_provider_passes=True,
                    )
        self.assertTrue(report["provider_draft_and_separate_review_passed"])

    def test_call149_attempt_growth_is_exactly_one_pair(self) -> None:
        checkpoints = (
            (
                recovery_call149.FIRST_RECOVERY_AFTER_STATE_RELATIVE,
                recovery_call149.FIRST_RECOVERY_AFFECTED_RECORD_IDS,
            ),
            (
                recovery_call149.SECOND_RECOVERY_AFTER_STATE_RELATIVE,
                recovery_call149.SECOND_RECOVERY_AFFECTED_RECORD_IDS,
            ),
            (
                recovery_call149.AFTER_STATE_RELATIVE,
                recovery_call149.FAILED_RECORD_IDS,
            ),
        )
        current_records: dict[str, object] = {}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for relative_path, record_ids in checkpoints:
                saved_records = {
                    record_id: {"draft_attempts": [], "review_attempts": []}
                    for record_id in record_ids
                }
                path = target / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(
                    recovery_call149._json_bytes({"records": saved_records})
                )
                for record_id in record_ids:
                    current_records[record_id] = {
                        "draft_attempts": [{"provider": "codex"}],
                        "review_attempts": [{"provider": "codex"}],
                    }
            current = {"records": current_records}
            recovery_call149._validate_attempt_growth(
                target,
                current,
                event_count=3,
                require_completed_provider_passes=True,
            )

            forged = deepcopy(current)
            forged["records"][recovery_call149.FAILED_RECORD_IDS[0]][
                "draft_attempts"
            ].append({"provider": "codex"})
            with self.assertRaisesRegex(
                recovery.Mix2KV4RecoveryError, "추가 attempt 수"
            ):
                recovery_call149._validate_attempt_growth(
                    target,
                    forged,
                    event_count=3,
                    require_completed_provider_passes=True,
                )

    def test_call149_chain_requires_exact_overflow_allowlist(self) -> None:
        overflow_ids = {
            recovery_call149.FIRST_RECOVERY_OVERFLOW_ID,
            *recovery_call149.SECOND_RECOVERY_OVERFLOW_IDS,
            *recovery_call149.FAILED_RECORD_IDS,
        }
        state = {
            "operator_recoveries": [
                recovery_call149.first_recovery_event(),
                recovery_call149.second_recovery_event(),
                recovery_call149._recovery_event(),
            ],
            "records": {
                record_id: {
                    "draft_attempts": [object()] * 4,
                    "rewrites_used": 2,
                    "duplicate_rewrites_used": 0,
                }
                for record_id in overflow_ids
            },
        }
        patches = (
            patch.object(
                recovery_call149,
                "validate_first_recovery_bundle",
                return_value={"recovery_id": recovery.RECOVERY_ID},
            ),
            patch.object(
                recovery_call149,
                "validate_second_recovery_bundle",
                return_value={"recovery_id": recovery_call148.RECOVERY_ID},
            ),
            patch.object(
                recovery_call149,
                "validate_recovery_bundle",
                return_value={"recovery_id": recovery_call149.RECOVERY_ID},
            ),
            patch.object(recovery_call149, "_validate_attempt_growth"),
            patch.object(recovery_call149.os.path, "lexists", return_value=True),
        )
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            report = recovery_call149.validate_recovery_chain(
                Path("/tmp") / recovery_call149.TARGET_NAME,
                state,
                require_completed_provider_passes=True,
            )
        self.assertTrue(report["all_recoveries_completed"])
        self.assertEqual(len(report["recoveries"]), 3)

        forged = deepcopy(state)
        forged["unrelated"] = object()
        forged["records"]["unrelated"] = {
            "draft_attempts": [object()] * 4,
            "rewrites_used": 2,
            "duplicate_rewrites_used": 0,
        }
        with (
            patch.object(
                recovery_call149,
                "validate_first_recovery_bundle",
                return_value={"recovery_id": recovery.RECOVERY_ID},
            ),
            patch.object(
                recovery_call149,
                "validate_second_recovery_bundle",
                return_value={"recovery_id": recovery_call148.RECOVERY_ID},
            ),
            patch.object(
                recovery_call149,
                "validate_recovery_bundle",
                return_value={"recovery_id": recovery_call149.RECOVERY_ID},
            ),
            patch.object(recovery_call149, "_validate_attempt_growth"),
            patch.object(recovery_call149.os.path, "lexists", return_value=True),
            self.assertRaisesRegex(
                recovery.Mix2KV4RecoveryError, "예외 집합"
            ),
        ):
            recovery_call149.validate_recovery_chain(
                Path("/tmp") / recovery_call149.TARGET_NAME,
                forged,
                require_completed_provider_passes=True,
            )

    def test_call149_recover_completes_partial_bundle_without_deleting(self) -> None:
        before = _call149_pre_state()
        before_payload = recovery_call149._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory).resolve()
            target = output_root / recovery_call149.TARGET_NAME
            target.mkdir(mode=0o700)
            (target / ".pipeline.lock").write_bytes(b"lock\n")
            (target / "pipeline_state.json").write_bytes(before_payload)
            (target / recovery_call149.RECOVERY_DIR_RELATIVE).mkdir(parents=True)
            with (
                patch.object(recovery_call149, "DEFAULT_OUTPUT_ROOT", output_root),
                patch.object(
                    recovery_call149, "EXPECTED_PRE_STATE_SHA256", before_sha
                ),
                patch.object(
                    recovery_call149,
                    "validate_recovery_chain",
                    side_effect=lambda *_args, **_kwargs: {
                        "schema_version": "1.0.0",
                        "recoveries": [],
                    },
                ),
            ):
                first = recovery_call149.recover(target)
                second = recovery_call149.recover(target)

            self.assertTrue(first["resumed_prepared_bundle"])
            self.assertFalse(first["already_applied"])
            self.assertTrue(second["already_applied"])
            self.assertTrue(
                (target / recovery_call149.RECOVERY_MANIFEST_RELATIVE).is_file()
            )

    def test_call154_recovery_preserves_attempts_and_counters(self) -> None:
        before = _call154_pre_state()
        before_payload = recovery_call154._json_bytes(before)
        with patch.object(
            recovery_call154,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            after = recovery_call154.build_recovered_state(before, before_payload)

        self.assertEqual(before, _call154_pre_state())
        self.assertEqual(before["provider_calls"], after["provider_calls"])
        self.assertEqual(len(after["operator_recoveries"]), 4)
        for record_id in recovery_call154.AFFECTED_RECORD_IDS:
            expected_status = (
                "needs_draft"
                if record_id in recovery_call154.FAILED_RECORD_IDS
                else before["records"][record_id]["status"]
            )
            self.assertEqual(after["records"][record_id]["status"], expected_status)
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

    def test_call154_bundle_requires_exact_new_attempt_pair(self) -> None:
        before = _call154_pre_state()
        before_payload = recovery_call154._json_bytes(before)
        before_sha = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory).resolve() / recovery_call154.TARGET_NAME
            (target / recovery_call154.RECOVERY_DIR_RELATIVE).mkdir(parents=True)
            with patch.object(
                recovery_call154, "EXPECTED_PRE_STATE_SHA256", before_sha
            ):
                after = recovery_call154.build_recovered_state(
                    before, before_payload
                )
                after_payload = recovery_call154._json_bytes(after)
                manifest = recovery_call154._expected_manifest(after_payload)
                (target / recovery_call154.BEFORE_STATE_RELATIVE).write_bytes(
                    before_payload
                )
                (target / recovery_call154.AFTER_STATE_RELATIVE).write_bytes(
                    after_payload
                )
                (target / recovery_call154.RECOVERY_MANIFEST_RELATIVE).write_bytes(
                    recovery_call154._json_bytes(manifest)
                )
                completed = deepcopy(after)
                completed["provider_calls"] = 156
                for record_id in recovery_call154.AFFECTED_RECORD_IDS:
                    record = completed["records"][record_id]
                    draft = {"record_id": record_id, "answer": "교정 초안"}
                    record["draft_attempts"].append(
                        {
                            "provider": "codex",
                            "provider_call_sequence": 155,
                            "execution_pass": "draft",
                            "attempt": len(record["draft_attempts"]) + 1,
                            "deterministic_pass": True,
                            "draft": draft,
                        }
                    )
                    review = {"decision": "PASS", "record_id": record_id}
                    record["review_attempts"].append(
                        {
                            "provider": "codex",
                            "provider_call_sequence": 156,
                            "execution_pass": "review",
                            "attempt": len(record["review_attempts"]) + 1,
                            "review_mode": "same_provider_separate_pass",
                            "review": review,
                        }
                    )
                    record["current_draft"] = draft
                    record["accepted"] = {
                        "draft_provider": "codex",
                        "review_provider": "codex",
                        "review_mode": "same_provider_separate_pass",
                        "draft": draft,
                        "review": review,
                    }
                    record["status"] = "accepted"
                report = recovery_call154.validate_recovery_bundle(
                    target,
                    completed,
                    require_completed_provider_passes=True,
                )
                forged = deepcopy(completed)
                forged["records"][recovery_call154.FAILED_RECORD_IDS[0]][
                    "draft_attempts"
                ].append(deepcopy(forged["records"][recovery_call154.FAILED_RECORD_IDS[0]]["draft_attempts"][-1]))
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError, "추가 attempt 수"
                ):
                    recovery_call154.validate_recovery_bundle(
                        target,
                        forged,
                        require_completed_provider_passes=True,
                    )
        self.assertTrue(report["provider_draft_and_separate_review_passed"])

    def test_call174_recovery_preserves_attempts_counters_and_answers(self) -> None:
        before = _call174_pre_state()
        before_payload = recovery_call174.teachers._json_bytes(before)
        with patch.object(
            recovery_call174,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            after = recovery_call174.build_recovered_state(before, before_payload)
            expected_event = recovery_call174._recovery_event()

        expected = deepcopy(before)
        expected["operator_recoveries"] = [
            *recovery_call174._prior_events(),
            expected_event,
        ]
        strength = expected["records"][recovery_call174.STRENGTH_RECORD_ID]
        strength["status"] = "needs_draft"
        strength["feedback"] = recovery_call174.NEW_STRENGTH_FEEDBACK
        for record_id in recovery_call174.RELATION_RECORD_IDS:
            expected["records"][record_id]["feedback"] = (
                recovery_call174.NEW_RELATION_FEEDBACK_BY_ID[record_id]
            )

        self.assertEqual(before, _call174_pre_state())
        self.assertEqual(after, expected)
        for record_id in recovery_call174.AFFECTED_RECORD_IDS:
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

    def test_call174_strength_requires_new_d5_r3_and_rejects_stale_or_extra(
        self,
    ) -> None:
        before = _call174_pre_state()
        before_payload = recovery_call174.teachers._json_bytes(before)
        with patch.object(
            recovery_call174,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            checkpoint_state = recovery_call174.build_recovered_state(
                before, before_payload
            )
        checkpoint = checkpoint_state["records"][
            recovery_call174.STRENGTH_RECORD_ID
        ]
        completed = _call174_completed_record(
            checkpoint,
            record_id=recovery_call174.STRENGTH_RECORD_ID,
            draft_sequence=175,
            review_sequence=176,
        )
        recovery_call174._validate_fifth_progress(
            completed,
            checkpoint,
            record_id=recovery_call174.STRENGTH_RECORD_ID,
            provider_calls=176,
            require_completed=True,
        )

        stale_review = deepcopy(completed)
        stale_review["review_attempts"][-1]["provider_call_sequence"] = 151
        stale_review["accepted"]["review"] = deepcopy(
            stale_review["review_attempts"][-1]["review"]
        )
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "attempt provenance"
        ):
            recovery_call174._validate_fifth_progress(
                stale_review,
                checkpoint,
                record_id=recovery_call174.STRENGTH_RECORD_ID,
                provider_calls=176,
                require_completed=True,
            )

        extra_attempt = deepcopy(completed)
        extra_attempt["draft_attempts"].append(
            _call174_draft_attempt(
                recovery_call174.STRENGTH_RECORD_ID,
                attempt=6,
                sequence=177,
            )
        )
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "duplicate round 없이"
        ):
            recovery_call174._validate_fifth_progress(
                extra_attempt,
                checkpoint,
                record_id=recovery_call174.STRENGTH_RECORD_ID,
                provider_calls=177,
                require_completed=True,
            )

    def test_call174_duplicate_round_two_and_three_descendants_are_allowed(
        self,
    ) -> None:
        before = _call174_pre_state()
        before_payload = recovery_call174.teachers._json_bytes(before)
        with patch.object(
            recovery_call174,
            "EXPECTED_PRE_STATE_SHA256",
            sha256_bytes(before_payload),
        ):
            checkpoint_state = recovery_call174.build_recovered_state(
                before, before_payload
            )
        checkpoint = checkpoint_state["records"][
            recovery_call174.STRENGTH_RECORD_ID
        ]

        for duplicate_round in (2, 3):
            with self.subTest(
                duplicate_round=duplicate_round,
                invalid="counter_without_attempt_pair",
            ):
                counter_only = _call174_completed_record(
                    checkpoint,
                    record_id=recovery_call174.STRENGTH_RECORD_ID,
                    draft_sequence=175,
                    review_sequence=176,
                )
                counter_only["duplicate_rewrites_used"] = duplicate_round
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError,
                    "duplicate 재작성 feedback|duplicate round 상태 전이",
                ):
                    recovery_call174._validate_fifth_progress(
                        counter_only,
                        checkpoint,
                        record_id=recovery_call174.STRENGTH_RECORD_ID,
                        provider_calls=176,
                        require_completed=True,
                    )

        for duplicate_round in (2, 3):
            with self.subTest(duplicate_round=duplicate_round):
                descendant = _call174_completed_record(
                    checkpoint,
                    record_id=recovery_call174.STRENGTH_RECORD_ID,
                    draft_sequence=175,
                    review_sequence=176,
                )
                next_sequence = 177
                for _ in range(2, duplicate_round + 1):
                    _call174_add_duplicate_pass(
                        descendant,
                        draft_sequence=next_sequence,
                        review_sequence=next_sequence + 1,
                    )
                    next_sequence += 2
                recovery_call174._validate_fifth_progress(
                    descendant,
                    checkpoint,
                    record_id=recovery_call174.STRENGTH_RECORD_ID,
                    provider_calls=next_sequence - 1,
                    require_completed=True,
                )

                stale_review = deepcopy(descendant)
                stale_review["review_attempts"][-1][
                    "provider_call_sequence"
                ] = next_sequence - 3
                with self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError, "attempt provenance"
                ):
                    recovery_call174._validate_fifth_progress(
                        stale_review,
                        checkpoint,
                        record_id=recovery_call174.STRENGTH_RECORD_ID,
                        provider_calls=next_sequence - 1,
                        require_completed=True,
                    )

    def test_call174_duplicate_review_allows_in_progress_round_two_states(
        self,
    ) -> None:
        before = _call174_pre_state()
        checkpoint = before["records"][
            recovery_call174.DUPLICATE_REVIEW_RECORD_ID
        ]
        completed = deepcopy(checkpoint)
        review = _call174_review_attempt(
            recovery_call174.DUPLICATE_REVIEW_RECORD_ID,
            attempt=len(completed["review_attempts"]) + 1,
            sequence=175,
        )
        completed["review_attempts"].append(review)
        completed["status"] = "accepted"
        completed["accepted"] = _call174_accepted(
            completed["draft_attempts"][-1], review
        )

        needs_draft = deepcopy(completed)
        needs_draft["duplicate_rewrites_used"] = 2
        needs_draft["status"] = "needs_draft"
        needs_draft["feedback"] = min(
            recovery_call174._duplicate_feedback_options(
                str(needs_draft["current_draft"]["answer"])
            )
        )
        needs_draft["accepted"] = None
        recovery_call174._validate_duplicate_review_progress(
            needs_draft,
            checkpoint,
            provider_calls=175,
            require_completed=False,
        )

        needs_review = deepcopy(needs_draft)
        draft = _call174_draft_attempt(
            recovery_call174.DUPLICATE_REVIEW_RECORD_ID,
            attempt=len(needs_review["draft_attempts"]) + 1,
            sequence=176,
        )
        needs_review["draft_attempts"].append(draft)
        needs_review["status"] = "needs_review"
        needs_review["feedback"] = ""
        needs_review["current_draft"] = deepcopy(draft["draft"])
        recovery_call174._validate_duplicate_review_progress(
            needs_review,
            checkpoint,
            provider_calls=176,
            require_completed=False,
        )

    def test_call174_overflow_is_subset_in_progress_and_exact_when_complete(
        self,
    ) -> None:
        target = Path("/tmp") / recovery_call174.TARGET_NAME
        state = {
            "operator_recoveries": [
                *recovery_call174._prior_events(),
                recovery_call174._recovery_event(),
            ]
        }

        def validate(
            overflow_ids: set[str], *, require_completed: bool
        ) -> dict[str, object]:
            with (
                patch.object(
                    recovery_call174.os.path, "lexists", return_value=True
                ),
                patch.object(
                    recovery_call174,
                    "validate_recovery_bundle",
                    return_value={"recovery_id": recovery_call174.RECOVERY_ID},
                ),
                patch.object(
                    recovery_call174.base_recovery,
                    "_read_regular_file",
                    return_value=b"{}",
                ),
                patch.object(
                    recovery_call174.base_recovery,
                    "_decode_object",
                    return_value={},
                ),
                patch.object(
                    recovery_call174,
                    "_validate_descendant_payload_contracts",
                ),
                patch.object(recovery_call174, "_validate_current_descendant"),
                patch.object(
                    recovery_call174,
                    "_validate_prior_projection",
                    return_value={"recoveries": []},
                ),
                patch.object(
                    recovery_call174.recovery_call148,
                    "_attempt_overflow_ids",
                    return_value=overflow_ids,
                ),
            ):
                report = recovery_call174.validate_recovery_chain(
                    target,
                    state,
                    require_completed_provider_passes=require_completed,
                )
            self.assertIsNotNone(report)
            return report

        in_progress = validate(
            {recovery_call174.DUPLICATE_REVIEW_RECORD_ID},
            require_completed=False,
        )
        self.assertFalse(in_progress["all_recoveries_completed"])

        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "진행 중.*예외 집합"
        ):
            validate(
                {*recovery_call174.FINAL_OVERFLOW_IDS, "unknown-overflow"},
                require_completed=False,
            )

        completed = validate(
            set(recovery_call174.FINAL_OVERFLOW_IDS),
            require_completed=True,
        )
        self.assertTrue(completed["all_recoveries_completed"])

        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "완료된.*예외 집합"
        ):
            validate(
                set(recovery_call174.FINAL_OVERFLOW_IDS)
                - {recovery_call174.STRENGTH_RECORD_ID},
                require_completed=True,
            )

    def test_call174_descendant_payload_contracts_reject_mutations(self) -> None:
        current, checkpoint, specs = _call174_payload_contract_fixture()

        def validate(state: dict[str, object]) -> tuple[int, int]:
            with (
                patch.object(
                    recovery_call174,
                    "_load_fixed_specs",
                    return_value=specs,
                ),
                patch.object(recovery_call174, "validate_draft") as draft_check,
                patch.object(recovery_call174, "validate_review") as review_check,
            ):
                recovery_call174._validate_descendant_payload_contracts(
                    state, checkpoint
                )
            return draft_check.call_count, review_check.call_count

        self.assertEqual(validate(current), (2, 2))

        record_id = recovery_call174.STRENGTH_RECORD_ID
        missing_payload_field = deepcopy(current)
        missing_payload_field["records"][record_id]["draft_attempts"][-1][
            "provider_draft"
        ].pop("limitations")
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "descendant draft provenance"
        ):
            validate(missing_payload_field)

        wrong_assignment = deepcopy(current)
        wrong_assignment["records"][record_id]["review_attempts"][-1][
            "assigned_provider"
        ] = "claude"
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "descendant review provenance"
        ):
            validate(wrong_assignment)

        wrong_fallback = deepcopy(current)
        wrong_fallback["records"][record_id]["draft_attempts"][-1][
            "fallback_used"
        ] = True
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "descendant draft provenance"
        ):
            validate(wrong_fallback)

        wrong_normalization = deepcopy(current)
        wrong_normalization["records"][record_id]["draft_attempts"][-1][
            "draft"
        ]["answer"] += " normalization mismatch"
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "descendant draft normalization"
        ):
            validate(wrong_normalization)

    def test_call174_04a_accepts_only_review_after_call174_d5(self) -> None:
        before = _call174_pre_state()
        checkpoint = before["records"][
            recovery_call174.DUPLICATE_REVIEW_RECORD_ID
        ]
        completed = deepcopy(checkpoint)
        review = _call174_review_attempt(
            recovery_call174.DUPLICATE_REVIEW_RECORD_ID,
            attempt=3,
            sequence=175,
        )
        completed["review_attempts"].append(review)
        completed["status"] = "accepted"
        completed["accepted"] = _call174_accepted(
            completed["draft_attempts"][-1], review
        )
        recovery_call174._validate_duplicate_review_progress(
            completed,
            checkpoint,
            provider_calls=175,
            require_completed=True,
        )

        stale = deepcopy(completed)
        stale["review_attempts"][-1]["provider_call_sequence"] = 151
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "attempt provenance"
        ):
            recovery_call174._validate_duplicate_review_progress(
                stale,
                checkpoint,
                provider_calls=175,
                require_completed=True,
            )

        extra = deepcopy(completed)
        extra["review_attempts"].append(
            _call174_review_attempt(
                recovery_call174.DUPLICATE_REVIEW_RECORD_ID,
                attempt=4,
                sequence=176,
            )
        )
        with self.assertRaisesRegex(
            recovery.Mix2KV4RecoveryError, "duplicate round 없이"
        ):
            recovery_call174._validate_duplicate_review_progress(
                extra,
                checkpoint,
                provider_calls=176,
                require_completed=True,
            )

    def test_call174_recover_does_not_write_live_state_when_validation_fails(
        self,
    ) -> None:
        before = _call174_pre_state()
        before_payload = recovery_call174.teachers._json_bytes(before)
        before_sha256 = sha256_bytes(before_payload)
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory).resolve()
            target = output_root / recovery_call174.TARGET_NAME
            target.mkdir(mode=0o700)
            (target / ".pipeline.lock").write_bytes(b"lock\n")
            state_path = target / "pipeline_state.json"
            state_path.write_bytes(before_payload)
            with (
                patch.object(
                    recovery_call174.teachers,
                    "DEFAULT_OUTPUT_ROOT",
                    output_root,
                ),
                patch.object(
                    recovery_call174,
                    "EXPECTED_PRE_STATE_SHA256",
                    before_sha256,
                ),
                patch.object(
                    recovery_call174,
                    "_validate_incident_pre_state",
                ),
                patch.object(
                    recovery_call174,
                    "validate_recovery_chain",
                    side_effect=recovery.Mix2KV4RecoveryError(
                        "synthetic validation failure"
                    ),
                ),
                self.assertRaisesRegex(
                    recovery.Mix2KV4RecoveryError,
                    "synthetic validation failure",
                ),
            ):
                recovery_call174.recover(target)

            self.assertEqual(state_path.read_bytes(), before_payload)


if __name__ == "__main__":
    unittest.main()
