# mix2k_v4_teacher_recovery_call148.py - call 148 계약 충돌 checkpoint를 두 번째 감사 이력으로 재개한다.

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
from scripts.data.mix2k_v4_teacher_recovery import (
    EXPECTED_CONTRACTS_SHA256,
    EXPECTED_RUNNER_SHA256,
    TARGET_NAME,
    Mix2KV4RecoveryError,
    _decode_object,
    _read_regular_file,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    FAILED_RECORD_ID as FIRST_RECOVERY_OVERFLOW_ID,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    RECOVERY_MANIFEST_RELATIVE as FIRST_RECOVERY_MANIFEST_RELATIVE,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    _recovery_event as first_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    validate_recovery_bundle as validate_first_recovery_bundle,
)
from scripts.data.mix2k_v4_teachers import (
    CONTRACTS_PATH,
    DEFAULT_OUTPUT_ROOT,
    RUNNER_PATH,
    _absolute,
    _atomic_write,
    _ensure_private_directory,
    _json_bytes,
    _reject_symlink_components,
)

RECOVERY_ID = "operator-recovery-provider-call-148-v1"
EXPECTED_PRE_STATE_SHA256 = (
    "61b9efc0cbfe95cc72cce8e259ec42b2de6e52611dacf9e34448fb78cb85a80c"
)
EXPECTED_PROVIDER_CALLS = 148
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
STRENGTH_FAILED_IDS = (
    "m2v4_04a03609c2525768fe53777e",
    "m2v4_9a689b213b285e1bda18f24f",
)
RELATION_FAILED_ID = "m2v4_27ca49a80737af0a331ab558"
STRENGTH_PENDING_ID = "m2v4_99b224076cafeba65e696b29"
FAILED_RECORD_IDS = (*STRENGTH_FAILED_IDS, RELATION_FAILED_ID)
AFFECTED_RECORD_IDS = (*FAILED_RECORD_IDS, STRENGTH_PENDING_ID)

OLD_STRENGTH_FAILED_FEEDBACK = (
    "teacher 구조 사실 claim 오류: "
    "unsupported_structural_claim:strength_pattern_yongshin"
)
OLD_RELATION_FAILED_FEEDBACK = (
    "teacher 구조 사실 claim 오류: unsupported_structural_claim:relation"
)
OLD_STRENGTH_PENDING_FEEDBACK = (
    "Deterministic validator 실패: teacher 구조 사실 claim 오류: "
    "unsupported_structural_claim:strength_pattern_yongshin"
)
STRENGTH_REWRITE_GUIDANCE = (
    "재생성 지시: 거절과 필수 입력·도구 제한은 유지하되 마지막 제한은 "
    "'네 기둥이 있어도 두 판단을 제가 새로 만들지 않으며, 별도로 검증되어 "
    "입력된 판단만 설명할 수 있습니다'처럼 쓰세요. "
    "'검증된 신강약·용신 판정 결과'처럼 대상 명칭과 '판정 결과'를 붙여 "
    "새 구조 사실 claim처럼 쓰지 마세요. 이전 오류: "
)
RELATION_REWRITE_GUIDANCE = (
    "재생성 지시: 거절과 필수 입력·도구 제한은 유지하되 마지막 제한은 "
    "'이 자료가 있어도 그 관계를 제가 새로 계산하지 않으며, 별도로 검증되어 "
    "입력된 관계만 설명할 수 있습니다'처럼 쓰세요. "
    "'검증된 합·충 관계 판정 결과'처럼 합·충과 '판정 결과'를 붙여 "
    "새 구조 사실 claim처럼 쓰지 마세요. 이전 오류: "
)
NEW_STRENGTH_FAILED_FEEDBACK = (
    STRENGTH_REWRITE_GUIDANCE + OLD_STRENGTH_FAILED_FEEDBACK
)
NEW_RELATION_FAILED_FEEDBACK = (
    RELATION_REWRITE_GUIDANCE + OLD_RELATION_FAILED_FEEDBACK
)
NEW_STRENGTH_PENDING_FEEDBACK = (
    STRENGTH_REWRITE_GUIDANCE + OLD_STRENGTH_PENDING_FEEDBACK
)


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "verified_result_requirement_misread_as_structural_claim",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _expected_before_contract() -> dict[str, dict[str, Any]]:
    return {
        STRENGTH_FAILED_IDS[0]: {
            "status": "failed",
            "feedback": OLD_STRENGTH_FAILED_FEEDBACK,
            "draft_attempts": 3,
            "review_attempts": 1,
        },
        STRENGTH_FAILED_IDS[1]: {
            "status": "failed",
            "feedback": OLD_STRENGTH_FAILED_FEEDBACK,
            "draft_attempts": 3,
            "review_attempts": 1,
        },
        RELATION_FAILED_ID: {
            "status": "failed",
            "feedback": OLD_RELATION_FAILED_FEEDBACK,
            "draft_attempts": 3,
            "review_attempts": 2,
        },
        STRENGTH_PENDING_ID: {
            "status": "needs_draft",
            "feedback": OLD_STRENGTH_PENDING_FEEDBACK,
            "draft_attempts": 2,
            "review_attempts": 1,
        },
    }


def _validate_pre_state(state: Mapping[str, Any], payload: bytes) -> None:
    records = state.get("records")
    if (
        sha256_bytes(payload) != EXPECTED_PRE_STATE_SHA256
        or state.get("schema_version") != "1.3.0"
        or state.get("provider_calls") != EXPECTED_PROVIDER_CALLS
        or state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or state.get("operator_recoveries") != [first_recovery_event()]
        or not isinstance(records, Mapping)
    ):
        raise Mix2KV4RecoveryError("두 번째 복구 전 pipeline state identity가 다릅니다.")
    for record_id, expected in _expected_before_contract().items():
        record = records.get(record_id)
        if (
            not isinstance(record, Mapping)
            or record.get("status") != expected["status"]
            or record.get("feedback") != expected["feedback"]
            or record.get("rewrites_used") != 2
            or record.get("duplicate_rewrites_used") != 0
            or record.get("accepted") is not None
            or not isinstance(record.get("current_draft"), Mapping)
            or not isinstance(record.get("draft_attempts"), list)
            or len(record["draft_attempts"]) != expected["draft_attempts"]
            or not isinstance(record.get("review_attempts"), list)
            or len(record["review_attempts"]) != expected["review_attempts"]
            or record["draft_attempts"][-1].get("provider") != "codex"
            or record["draft_attempts"][-1].get("provider_call_sequence")
            != EXPECTED_PROVIDER_CALLS
            or record["draft_attempts"][-1].get("deterministic_pass") is not False
        ):
            raise Mix2KV4RecoveryError(
                f"두 번째 복구 전 record 계약이 다릅니다: {record_id}"
            )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 148 pre-state에서 허용된 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [first_recovery_event(), _recovery_event()]
    for record_id in STRENGTH_FAILED_IDS:
        record = recovered["records"][record_id]
        record["status"] = "needs_draft"
        record["feedback"] = NEW_STRENGTH_FAILED_FEEDBACK
    relation = recovered["records"][RELATION_FAILED_ID]
    relation["status"] = "needs_draft"
    relation["feedback"] = NEW_RELATION_FAILED_FEEDBACK
    recovered["records"][STRENGTH_PENDING_ID]["feedback"] = (
        NEW_STRENGTH_PENDING_FEEDBACK
    )
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    changes = [
        {
            "record_id": record_id,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_STRENGTH_FAILED_FEEDBACK,
                "after": NEW_STRENGTH_FAILED_FEEDBACK,
            },
        }
        for record_id in STRENGTH_FAILED_IDS
    ]
    changes.append(
        {
            "record_id": RELATION_FAILED_ID,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_RELATION_FAILED_FEEDBACK,
                "after": NEW_RELATION_FAILED_FEEDBACK,
            },
        }
    )
    changes.append(
        {
            "record_id": STRENGTH_PENDING_ID,
            "status": {"before": "needs_draft", "after": "needs_draft"},
            "feedback": {
                "before": OLD_STRENGTH_PENDING_FEEDBACK,
                "after": NEW_STRENGTH_PENDING_FEEDBACK,
            },
        }
    )
    return changes


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
        "exact_changes": _exact_changes(),
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
    before_payload = _read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 148 recovery before state"
    )
    after_payload = _read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 148 recovery after state"
    )
    manifest_payload = _read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 148 recovery manifest"
    )
    before_state = _decode_object(before_payload, "call 148 recovery before state")
    after_state = _decode_object(after_payload, "call 148 recovery after state")
    manifest = _decode_object(manifest_payload, "call 148 recovery manifest")
    expected_after = build_recovered_state(before_state, before_payload)
    if (
        target.name != TARGET_NAME
        or after_payload != _json_bytes(expected_after)
        or after_state != expected_after
        or manifest != _expected_manifest(after_payload)
        or current_state.get("operator_recoveries")
        != [first_recovery_event(), _recovery_event()]
        or current_state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or current_state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or not isinstance(current_state.get("provider_calls"), int)
        or current_state["provider_calls"] < EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("call 148 operator recovery bundle이 다릅니다.")
    current_records = current_state.get("records")
    after_records = after_state["records"]
    if not isinstance(current_records, Mapping):
        raise Mix2KV4RecoveryError("call 148 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
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
                f"call 148 recovery 이후 attempt prefix가 다릅니다: {record_id}"
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
                f"call 148 recovery 이후 provider provenance가 다릅니다: {record_id}"
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
                f"call 148 recovery 행이 실제 draft·별도 review PASS를 끝내지 않았습니다: {record_id}"
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


def _attempt_overflow_ids(state: Mapping[str, Any]) -> set[str]:
    overflows: set[str] = set()
    records = state.get("records")
    if not isinstance(records, Mapping):
        return overflows
    for record_id, record in records.items():
        if not isinstance(record, Mapping):
            continue
        drafts = record.get("draft_attempts")
        rewrites = record.get("rewrites_used")
        duplicate_rewrites = record.get("duplicate_rewrites_used")
        if (
            isinstance(drafts, list)
            and isinstance(rewrites, int)
            and not isinstance(rewrites, bool)
            and isinstance(duplicate_rewrites, int)
            and not isinstance(duplicate_rewrites, bool)
            and len(drafts) > 1 + rewrites + duplicate_rewrites
        ):
            overflows.add(str(record_id))
    return overflows


def validate_recovery_chain(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any] | None:
    """알려진 operator recovery chain만 허용하고 모든 sidecar를 재생한다."""

    events = current_state.get("operator_recoveries")
    first_event = first_recovery_event()
    second_event = _recovery_event()
    overflow_ids = _attempt_overflow_ids(current_state)
    first_sidecar_exists = os.path.lexists(
        target / FIRST_RECOVERY_MANIFEST_RELATIVE
    )
    second_sidecar_exists = os.path.lexists(target / RECOVERY_MANIFEST_RELATIVE)
    sidecar_signaled = first_sidecar_exists or second_sidecar_exists
    if events is None and not overflow_ids and not sidecar_signaled:
        return None
    if events not in ([first_event], [first_event, second_event]):
        raise Mix2KV4RecoveryError("알 수 없는 operator recovery event chain입니다.")

    first_state = deepcopy(dict(current_state))
    first_state["operator_recoveries"] = [first_event]
    first_report = validate_first_recovery_bundle(
        target,
        first_state,
        require_completed_provider_passes=True,
    )
    reports = [first_report]
    expected_first_overflow = {FIRST_RECOVERY_OVERFLOW_ID}
    if events == [first_event]:
        if second_sidecar_exists:
            raise Mix2KV4RecoveryError(
                "두 번째 recovery sidecar와 state event가 일치하지 않습니다."
            )
        if overflow_ids != expected_first_overflow:
            raise Mix2KV4RecoveryError("첫 operator recovery attempt 예외 집합이 다릅니다.")
    else:
        if not second_sidecar_exists:
            raise Mix2KV4RecoveryError(
                "두 번째 recovery state event에 대응하는 sidecar가 없습니다."
            )
        second_report = validate_recovery_bundle(
            target,
            current_state,
            require_completed_provider_passes=require_completed_provider_passes,
        )
        reports.append(second_report)
        expected_all = expected_first_overflow | set(FAILED_RECORD_IDS)
        if require_completed_provider_passes:
            if overflow_ids != expected_all:
                raise Mix2KV4RecoveryError(
                    "두 번째 operator recovery attempt 예외 집합이 다릅니다."
                )
        elif not expected_first_overflow.issubset(overflow_ids) or not overflow_ids.issubset(
            expected_all
        ):
            raise Mix2KV4RecoveryError(
                "진행 중 operator recovery attempt 예외 집합이 다릅니다."
            )
    return {"schema_version": "1.0.0", "recoveries": reports}


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
        raise Mix2KV4RecoveryError("허용된 call 148 recovery target이 아닙니다.")
    _reject_symlink_components(target, "call 148 recovery target")
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
                raise Mix2KV4RecoveryError("불완전한 call 148 recovery bundle이 있습니다.")
            if (
                sha256_bytes(state_payload) == EXPECTED_PRE_STATE_SHA256
                and state.get("operator_recoveries") == [first_recovery_event()]
            ):
                validate_first_recovery_bundle(
                    target,
                    state,
                    require_completed_provider_passes=True,
                )
                before_payload = _read_regular_file(
                    target / BEFORE_STATE_RELATIVE,
                    "prepared call 148 recovery before state",
                )
                after_payload = _read_regular_file(
                    target / AFTER_STATE_RELATIVE,
                    "prepared call 148 recovery after state",
                )
                after_state = _decode_object(
                    after_payload, "prepared call 148 recovery after state"
                )
                if before_payload != state_payload:
                    raise Mix2KV4RecoveryError(
                        "prepared recovery의 before snapshot이 live state와 다릅니다."
                    )
                validate_recovery_bundle(
                    target,
                    after_state,
                    require_completed_provider_passes=False,
                )
                _atomic_write(state_path, after_payload)
                report = validate_recovery_chain(
                    target,
                    after_state,
                    require_completed_provider_passes=False,
                )
                if report is None:
                    raise Mix2KV4RecoveryError(
                        "prepared call 148 recovery 검증 결과가 없습니다."
                    )
                report["already_applied"] = False
                report["resumed_prepared_bundle"] = True
                return report
            report = validate_recovery_chain(
                target,
                state,
                require_completed_provider_passes=False,
            )
            if report is None:
                raise Mix2KV4RecoveryError("call 148 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        validate_first_recovery_bundle(
            target,
            state,
            require_completed_provider_passes=True,
        )
        recovered = build_recovered_state(state, state_payload)
        after_payload = _json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        _ensure_private_directory(recovery_dir, "call 148 operator recovery provenance")
        _atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        _atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        _atomic_write(manifest_path, _json_bytes(recovery_manifest))
        _atomic_write(state_path, after_payload)
        report = validate_recovery_chain(
            target,
            recovered,
            require_completed_provider_passes=False,
        )
        if report is None:
            raise Mix2KV4RecoveryError("call 148 recovery 검증 결과가 없습니다.")
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 148 checkpoint의 감사 가능한 복구"
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
