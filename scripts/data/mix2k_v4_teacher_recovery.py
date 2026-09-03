# mix2k_v4_teacher_recovery.py - 중단된 MIX2K v4 teacher checkpoint를 감사 가능한 1회성 절차로 재개한다.

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import sha256_bytes, sha256_file
from scripts.data.mix2k_v4_teachers import (
    CONTRACTS_PATH,
    DEFAULT_OUTPUT_ROOT,
    MAX_JSON_BYTES,
    RUNNER_PATH,
    _absolute,
    _atomic_write,
    _ensure_private_directory,
    _json_bytes,
    _reject_symlink_components,
)

RECOVERY_ID = "operator-recovery-provider-call-60-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "156b92ddb824634379045e2d50b9036875b4d632dcfb8022665804ec01d6c14f"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 60
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
FAILED_RECORD_ID = "m2v4_67ad3171b4f72afa5168ecc6"
PENDING_RECORD_ID = "m2v4_437e3b58e25808533390db66"

OLD_FAILED_FEEDBACK = (
    "teacher 구조 사실 claim 오류: "
    "required_schema_fact_omitted:chart.hard_facts.pillars.month.stem_ten_god,"
    "required_schema_fact_omitted:chart.hard_facts.pillars.month.branch_ten_god"
)
OLD_PENDING_FEEDBACK = (
    "Deterministic validator 실패: teacher 구조 사실 claim 오류: "
    "required_schema_fact_omitted:chart.hard_facts.pillars.hour.stem_ten_god,"
    "required_schema_fact_omitted:chart.hard_facts.pillars.hour.branch_ten_god"
)
NEW_FAILED_FEEDBACK = (
    "재생성 지시: 같은 값이어도 위치를 묶어 쓰지 마세요. "
    "'월주의 천간 십신은 정재이고, 월주의 지지 십신도 정재입니다'처럼 "
    "월주 천간 십신과 월주 지지 십신을 각각 완전한 절로 명시하세요. "
    "이전 오류: "
    + OLD_FAILED_FEEDBACK
)
NEW_PENDING_FEEDBACK = (
    "재생성 지시: 같은 값이어도 위치를 묶어 쓰지 마세요. "
    "'시주의 천간 십신은 편재이고, 시주의 지지 십신도 편재입니다'처럼 "
    "시주 천간 십신과 시주 지지 십신을 각각 완전한 절로 명시하세요. "
    "이전 오류: "
    + OLD_PENDING_FEEDBACK
)


class Mix2KV4RecoveryError(RuntimeError):
    """고정 checkpoint 복구 계약 위반."""


def _read_regular_file(path: Path, label: str) -> bytes:
    """symlink를 따르지 않고 크기 상한 안의 regular file bytes를 한 번 연다."""

    _reject_symlink_components(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Mix2KV4RecoveryError(f"{label}을 안전하게 열지 못했습니다.") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not 1 <= metadata.st_size <= MAX_JSON_BYTES:
            raise Mix2KV4RecoveryError(f"{label}이 regular file 또는 크기 계약과 다릅니다.")
        payload = os.read(descriptor, metadata.st_size + 1)
        if len(payload) != metadata.st_size:
            raise Mix2KV4RecoveryError(f"{label}을 단일 snapshot으로 읽지 못했습니다.")
        return payload
    finally:
        os.close(descriptor)


def _decode_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4RecoveryError(f"{label} JSON을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4RecoveryError(f"{label} 최상위는 object여야 합니다.")
    return value


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "equal_positioned_ten_god_shorthand_exhausted_rewrites",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": [FAILED_RECORD_ID, PENDING_RECORD_ID],
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _validate_record_before(
    record: Any,
    *,
    record_id: str,
    status: str,
    feedback: str,
    attempts: int,
) -> None:
    if (
        not isinstance(record, Mapping)
        or record.get("status") != status
        or record.get("feedback") != feedback
        or record.get("rewrites_used") != 2
        or record.get("duplicate_rewrites_used") != 0
        or record.get("current_draft") is not None
        or record.get("accepted") is not None
        or record.get("review_attempts") != []
        or not isinstance(record.get("draft_attempts"), list)
        or len(record["draft_attempts"]) != attempts
        or record["draft_attempts"][-1].get("provider") != "codex"
        or record["draft_attempts"][-1].get("provider_call_sequence")
        != EXPECTED_PROVIDER_CALLS
        or record["draft_attempts"][-1].get("deterministic_pass") is not False
    ):
        raise Mix2KV4RecoveryError(
            f"복구 전 record 계약이 다릅니다: {record_id}"
        )


def _validate_pre_state(state: Mapping[str, Any], payload: bytes) -> None:
    records = state.get("records")
    if (
        sha256_bytes(payload) != EXPECTED_PRE_STATE_SHA256
        or state.get("schema_version") != "1.3.0"
        or state.get("provider_calls") != EXPECTED_PROVIDER_CALLS
        or state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or state.get("operator_recoveries") is not None
        or not isinstance(records, Mapping)
    ):
        raise Mix2KV4RecoveryError("복구 전 pipeline state identity가 다릅니다.")
    _validate_record_before(
        records.get(FAILED_RECORD_ID),
        record_id=FAILED_RECORD_ID,
        status="failed",
        feedback=OLD_FAILED_FEEDBACK,
        attempts=3,
    )
    _validate_record_before(
        records.get(PENDING_RECORD_ID),
        record_id=PENDING_RECORD_ID,
        status="needs_draft",
        feedback=OLD_PENDING_FEEDBACK,
        attempts=2,
    )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """고정 pre-state에서 허용된 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [_recovery_event()]
    failed = recovered["records"][FAILED_RECORD_ID]
    pending = recovered["records"][PENDING_RECORD_ID]
    failed["status"] = "needs_draft"
    failed["feedback"] = NEW_FAILED_FEEDBACK
    pending["feedback"] = NEW_PENDING_FEEDBACK
    return recovered


def _expected_manifest(after_payload: bytes) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "target_name": TARGET_NAME,
        "recovery_tool_sha256": sha256_file(Path(__file__).resolve()),
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "contracts_sha256": EXPECTED_CONTRACTS_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "before_state_path": BEFORE_STATE_RELATIVE.as_posix(),
        "before_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "after_state_path": AFTER_STATE_RELATIVE.as_posix(),
        "after_state_sha256": sha256_bytes(after_payload),
        "event": _recovery_event(),
        "exact_changes": [
            {
                "record_id": FAILED_RECORD_ID,
                "status": {"before": "failed", "after": "needs_draft"},
                "feedback": {
                    "before": OLD_FAILED_FEEDBACK,
                    "after": NEW_FAILED_FEEDBACK,
                },
            },
            {
                "record_id": PENDING_RECORD_ID,
                "status": {"before": "needs_draft", "after": "needs_draft"},
                "feedback": {
                    "before": OLD_PENDING_FEEDBACK,
                    "after": NEW_PENDING_FEEDBACK,
                },
            },
        ],
        "unchanged_fields": [
            "provider_calls",
            "rewrites_used",
            "duplicate_rewrites_used",
            "draft_attempts",
            "review_attempts",
            "current_draft",
            "accepted",
        ],
    }


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """sidecar·pre/post snapshot·현재 descendant state를 함께 검증한다."""

    before_payload = _read_regular_file(target / BEFORE_STATE_RELATIVE, "recovery before state")
    after_payload = _read_regular_file(target / AFTER_STATE_RELATIVE, "recovery after state")
    manifest_payload = _read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "recovery manifest"
    )
    before_state = _decode_object(before_payload, "recovery before state")
    after_state = _decode_object(after_payload, "recovery after state")
    manifest = _decode_object(manifest_payload, "recovery manifest")
    expected_after = build_recovered_state(before_state, before_payload)
    expected_after_payload = _json_bytes(expected_after)
    if (
        target.name != TARGET_NAME
        or after_payload != expected_after_payload
        or after_state != expected_after
        or manifest != _expected_manifest(after_payload)
        or current_state.get("operator_recoveries") != [_recovery_event()]
        or current_state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or current_state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or not isinstance(current_state.get("provider_calls"), int)
        or current_state["provider_calls"] < EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("operator recovery bundle identity가 다릅니다.")

    current_records = current_state.get("records")
    after_records = after_state["records"]
    if not isinstance(current_records, Mapping):
        raise Mix2KV4RecoveryError("현재 recovery descendant record가 없습니다.")
    for record_id in (FAILED_RECORD_ID, PENDING_RECORD_ID):
        current = current_records.get(record_id)
        checkpoint = after_records[record_id]
        if (
            not isinstance(current, Mapping)
            or current.get("rewrites_used") != checkpoint["rewrites_used"]
            or current.get("duplicate_rewrites_used")
            != checkpoint["duplicate_rewrites_used"]
            or current.get("draft_attempts", [])[: len(checkpoint["draft_attempts"])]
            != checkpoint["draft_attempts"]
            or current.get("review_attempts", [])[: len(checkpoint["review_attempts"])]
            != checkpoint["review_attempts"]
        ):
            raise Mix2KV4RecoveryError(
                f"operator recovery 이후 attempt prefix가 다릅니다: {record_id}"
            )
        later_drafts = current.get("draft_attempts", [])[len(checkpoint["draft_attempts"]) :]
        later_reviews = current.get("review_attempts", [])[len(checkpoint["review_attempts"]) :]
        if any(
            attempt.get("provider") != "codex"
            or not isinstance(attempt.get("provider_call_sequence"), int)
            or attempt["provider_call_sequence"] <= EXPECTED_PROVIDER_CALLS
            for attempt in [*later_drafts, *later_reviews]
            if isinstance(attempt, Mapping)
        ):
            raise Mix2KV4RecoveryError(
                f"operator recovery 이후 provider provenance가 다릅니다: {record_id}"
            )
        if require_completed_provider_passes and (
            current.get("status") != "accepted"
            or len(later_drafts) < 1
            or len(later_reviews) < 1
            or later_drafts[-1].get("deterministic_pass") is not True
            or later_reviews[-1].get("review", {}).get("decision") != "PASS"
            or current.get("accepted", {}).get("draft") != current.get("current_draft")
        ):
            raise Mix2KV4RecoveryError(
                f"operator recovery 행이 실제 draft·별도 review PASS를 끝내지 않았습니다: {record_id}"
            )
    return {
        "recovery_id": RECOVERY_ID,
        "manifest_sha256": sha256_bytes(manifest_payload),
        "before_state_sha256": sha256_bytes(before_payload),
        "after_state_sha256": sha256_bytes(after_payload),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "provider_draft_and_separate_review_passed": require_completed_provider_passes,
    }


def recover(target: Path) -> dict[str, Any]:
    target = _absolute(target)
    expected_target = _absolute(DEFAULT_OUTPUT_ROOT / TARGET_NAME)
    if (
        target != expected_target
        or target.is_symlink()
        or not target.is_dir()
        or sha256_file(RUNNER_PATH) != EXPECTED_RUNNER_SHA256
        or sha256_file(CONTRACTS_PATH) != EXPECTED_CONTRACTS_SHA256
    ):
        raise Mix2KV4RecoveryError("허용된 recovery target이 아닙니다.")
    _reject_symlink_components(target, "recovery target")
    for forbidden in (
        target / "teacher_manifest.json",
        target / "accepted/training_candidates_2000.jsonl",
    ):
        if forbidden.exists():
            raise Mix2KV4RecoveryError("이미 candidate가 생성된 target은 복구할 수 없습니다.")

    lock_path = target / ".pipeline.lock"
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_descriptor = os.open(lock_path, flags)
    except OSError as exc:
        raise Mix2KV4RecoveryError("pipeline lock을 안전하게 열지 못했습니다.") from exc
    try:
        if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
            raise Mix2KV4RecoveryError("pipeline lock이 regular file이 아닙니다.")
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4RecoveryError("teacher pipeline이 실행 중입니다.") from exc

        state_path = target / "pipeline_state.json"
        state_payload = _read_regular_file(state_path, "teacher pipeline state")
        state = _decode_object(state_payload, "teacher pipeline state")
        recovery_dir = target / RECOVERY_DIR_RELATIVE
        manifest_path = target / RECOVERY_MANIFEST_RELATIVE
        if recovery_dir.exists():
            if not manifest_path.is_file():
                raise Mix2KV4RecoveryError("불완전한 operator recovery bundle이 있습니다.")
            report = validate_recovery_bundle(
                target, state, require_completed_provider_passes=False
            )
            report["already_applied"] = True
            return report

        recovered = build_recovered_state(state, state_payload)
        after_payload = _json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        _ensure_private_directory(recovery_dir, "operator recovery provenance")
        _atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        _atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        _atomic_write(manifest_path, _json_bytes(recovery_manifest))
        _atomic_write(state_path, after_payload)
        report = validate_recovery_bundle(
            target, recovered, require_completed_provider_passes=False
        )
        report["already_applied"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="고정 MIX2K v4 teacher checkpoint의 감사 가능한 1회성 복구"
    )
    parser.add_argument("--target", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = recover(args.target)
    except Mix2KV4RecoveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
