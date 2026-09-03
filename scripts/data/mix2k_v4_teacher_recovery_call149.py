# mix2k_v4_teacher_recovery_call149.py - call 149의 3개 실패 행을 감사 가능한 세 번째 복구로 재개한다.

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
    AFTER_STATE_RELATIVE as FIRST_RECOVERY_AFTER_STATE_RELATIVE,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    FAILED_RECORD_ID as FIRST_RECOVERY_OVERFLOW_ID,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    PENDING_RECORD_ID as FIRST_RECOVERY_PENDING_ID,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    RECOVERY_MANIFEST_RELATIVE as FIRST_RECOVERY_MANIFEST_RELATIVE,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    Mix2KV4RecoveryError,
    _decode_object,
    _read_regular_file,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    _recovery_event as first_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    validate_recovery_bundle as validate_first_recovery_bundle,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    AFFECTED_RECORD_IDS as SECOND_RECOVERY_AFFECTED_RECORD_IDS,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    AFTER_STATE_RELATIVE as SECOND_RECOVERY_AFTER_STATE_RELATIVE,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    FAILED_RECORD_IDS as SECOND_RECOVERY_OVERFLOW_IDS,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    RECOVERY_MANIFEST_RELATIVE as SECOND_RECOVERY_MANIFEST_RELATIVE,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    _attempt_overflow_ids,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    _recovery_event as second_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    validate_recovery_bundle as validate_second_recovery_bundle,
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

RECOVERY_ID = "operator-recovery-provider-call-149-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "225dc4910f760480c9961371011eb1d9cca7b689360e35bc52fbf3069b25b770"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 149
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
FAILED_RECORD_IDS = (
    "m2v4_17f19223c200ce872e34b2d8",
    "m2v4_91b95e86fca05f83012bc87f",
    "m2v4_b98b22d7d5bbb0186e289392",
)
FIRST_RECOVERY_AFFECTED_RECORD_IDS = (
    FIRST_RECOVERY_OVERFLOW_ID,
    FIRST_RECOVERY_PENDING_ID,
)

OLD_FEEDBACK_BY_ID = {
    FAILED_RECORD_IDS[0]: (
        '{"fact_errors":[],"failure_codes":["INSTRUCTION_VIOLATION"],'
        '"rewrite_instructions":"구조화 명식이 없는 경우의 안내를 1~3문장으로 직접 '
        "제시하라는 production instruction에 맞게 현재 4문장을 3문장 이내로 고치세요. "
        "마지막 두 문장의 내용을 합쳐, 검증된 원국·오늘의 기간 사실이 필요하지만 특정 "
        '사건은 확정 예측하지 않는다는 제한을 한 문장으로 안내하면 됩니다.",'
        '"style_notes":["세 번째 줄에 두 문장이 있어 전체 답변이 4문장입니다."]}'
    ),
    FAILED_RECORD_IDS[1]: (
        '{"fact_errors":[],"failure_codes":["INSTRUCTION_VIOLATION"],'
        '"rewrite_instructions":"구조화 명식이 없는 경우 현재 가능한 다음 행동이나 제한을 '
        "1~3문장으로 안내하라는 production instruction을 지키도록 4문장을 3문장 이내로 "
        "줄이세요. 검증된 네 기둥·오늘의 기간 사실과 별도의 검증된 관계 판정 결과가 모두 "
        '필요하다는 내용을 한 문장으로 합치면 됩니다.",'
        '"style_notes":["세 번째 줄이 두 문장으로 구성되어 전체 답변이 4문장입니다."]}'
    ),
    FAILED_RECORD_IDS[2]: (
        '{"fact_errors":[],"failure_codes":["INSTRUCTION_VIOLATION"],'
        '"rewrite_instructions":"구조화 명식이 없는 경우 답변을 1~3문장으로 직접 안내하라는 '
        "production instruction에 맞게 현재 4문장을 3문장 이내로 고치세요. 검증된 네 "
        "기둥과 오늘의 기간 사실을 요청하는 내용에 현실적인 일정 점검 조언을 같은 문장으로 "
        '자연스럽게 결합할 수 있습니다.",'
        '"style_notes":["세 번째 줄에 두 문장이 있어 전체 답변이 4문장입니다."]}'
    ),
}
RECOVERY_GUIDANCE = (
    "재생성 지시: 정확히 3개 완결 문장·3줄로 작성하고, 필요한 입력과 도구 제한은 "
    "세 번째 문장 하나에 결합하세요. 이전 review: "
)
NEW_FEEDBACK_BY_ID = {
    record_id: RECOVERY_GUIDANCE + feedback
    for record_id, feedback in OLD_FEEDBACK_BY_ID.items()
}


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "intake_three_sentence_limit_exhausted_rewrites",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(FAILED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _expected_last_review(record_id: str) -> dict[str, Any]:
    review = json.loads(OLD_FEEDBACK_BY_ID[record_id])
    review["decision"] = "FAIL"
    review["record_id"] = record_id
    return review


def _validate_pre_state(state: Mapping[str, Any], payload: bytes) -> None:
    records = state.get("records")
    expected_events = [first_recovery_event(), second_recovery_event()]
    if (
        sha256_bytes(payload) != EXPECTED_PRE_STATE_SHA256
        or state.get("schema_version") != "1.3.0"
        or state.get("provider_calls") != EXPECTED_PROVIDER_CALLS
        or state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or state.get("operator_recoveries") != expected_events
        or not isinstance(records, Mapping)
    ):
        raise Mix2KV4RecoveryError("세 번째 복구 전 pipeline state identity가 다릅니다.")

    for record_id in FAILED_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"세 번째 복구 전 record 계약이 다릅니다: {record_id}"
            )
        drafts = record.get("draft_attempts")
        reviews = record.get("review_attempts")
        current_draft = record.get("current_draft")
        if (
            record.get("status") != "failed"
            or record.get("feedback") != OLD_FEEDBACK_BY_ID[record_id]
            or record.get("rewrites_used") != 2
            or record.get("duplicate_rewrites_used") != 0
            or record.get("accepted") is not None
            or not isinstance(current_draft, Mapping)
            or not isinstance(drafts, list)
            or len(drafts) != 3
            or not isinstance(reviews, list)
            or len(reviews) != 3
        ):
            raise Mix2KV4RecoveryError(
                f"세 번째 복구 전 record 계약이 다릅니다: {record_id}"
            )
        last_draft = drafts[-1]
        last_review = reviews[-1]
        if (
            not isinstance(last_draft, Mapping)
            or last_draft.get("provider") != "codex"
            or last_draft.get("provider_call_sequence") != 148
            or last_draft.get("execution_pass") != "draft"
            or last_draft.get("deterministic_pass") is not True
            or last_draft.get("draft") != current_draft
            or not isinstance(last_review, Mapping)
            or last_review.get("provider") != "codex"
            or last_review.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
            or last_review.get("execution_pass") != "review"
            or last_review.get("review_mode") != "same_provider_separate_pass"
            or last_review.get("review") != _expected_last_review(record_id)
        ):
            raise Mix2KV4RecoveryError(
                f"세 번째 복구 전 최종 attempt 계약이 다릅니다: {record_id}"
            )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 149 pre-state에서 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [
        first_recovery_event(),
        second_recovery_event(),
        _recovery_event(),
    ]
    for record_id in FAILED_RECORD_IDS:
        record = recovered["records"][record_id]
        record["status"] = "needs_draft"
        record["feedback"] = NEW_FEEDBACK_BY_ID[record_id]
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    return [
        {
            "record_id": record_id,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_FEEDBACK_BY_ID[record_id],
                "after": NEW_FEEDBACK_BY_ID[record_id],
            },
        }
        for record_id in FAILED_RECORD_IDS
    ]


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


def _later_attempts(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
) -> tuple[list[Any], list[Any]]:
    current_drafts = current.get("draft_attempts")
    checkpoint_drafts = checkpoint.get("draft_attempts")
    current_reviews = current.get("review_attempts")
    checkpoint_reviews = checkpoint.get("review_attempts")
    if (
        not isinstance(current_drafts, list)
        or not isinstance(checkpoint_drafts, list)
        or not isinstance(current_reviews, list)
        or not isinstance(checkpoint_reviews, list)
        or current_drafts[: len(checkpoint_drafts)] != checkpoint_drafts
        or current_reviews[: len(checkpoint_reviews)] != checkpoint_reviews
    ):
        raise Mix2KV4RecoveryError(
            f"call 149 recovery 이후 attempt prefix가 다릅니다: {record_id}"
        )
    return (
        current_drafts[len(checkpoint_drafts) :],
        current_reviews[len(checkpoint_reviews) :],
    )


def _validate_later_provenance(
    later_drafts: Sequence[Any],
    later_reviews: Sequence[Any],
    *,
    record_id: str,
) -> None:
    for attempt in later_drafts:
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "draft"
            or not isinstance(attempt.get("draft"), Mapping)
            or not attempt["draft"]
            or not isinstance(attempt.get("provider_call_sequence"), int)
            or isinstance(attempt.get("provider_call_sequence"), bool)
            or attempt["provider_call_sequence"] <= EXPECTED_PROVIDER_CALLS
        ):
            raise Mix2KV4RecoveryError(
                f"call 149 recovery 이후 draft provenance가 다릅니다: {record_id}"
            )
    for attempt in later_reviews:
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "review"
            or attempt.get("review_mode") != "same_provider_separate_pass"
            or not isinstance(attempt.get("provider_call_sequence"), int)
            or isinstance(attempt.get("provider_call_sequence"), bool)
            or attempt["provider_call_sequence"] <= EXPECTED_PROVIDER_CALLS
        ):
            raise Mix2KV4RecoveryError(
                f"call 149 recovery 이후 review provenance가 다릅니다: {record_id}"
            )


def _validate_completed_record(
    current: Mapping[str, Any],
    later_drafts: Sequence[Any],
    later_reviews: Sequence[Any],
    *,
    record_id: str,
) -> None:
    if not later_drafts or not later_reviews:
        raise Mix2KV4RecoveryError(
            f"call 149 recovery 행에 새 draft·review가 없습니다: {record_id}"
        )
    last_draft = later_drafts[-1]
    last_review = later_reviews[-1]
    accepted = current.get("accepted")
    current_draft = current.get("current_draft")
    if not (
        isinstance(last_draft, Mapping)
        and isinstance(last_review, Mapping)
        and isinstance(accepted, Mapping)
        and isinstance(current_draft, Mapping)
        and current_draft
        and isinstance(last_draft.get("draft"), Mapping)
        and last_draft["draft"]
        and isinstance(accepted.get("draft"), Mapping)
        and accepted["draft"]
    ):
        raise Mix2KV4RecoveryError(
            f"call 149 recovery 완료 provenance가 없습니다: {record_id}"
        )
    draft_sequence = last_draft.get("provider_call_sequence")
    review_sequence = last_review.get("provider_call_sequence")
    accepted_review = accepted.get("review")
    if (
        current.get("status") != "accepted"
        or last_draft.get("deterministic_pass") is not True
        or not isinstance(draft_sequence, int)
        or isinstance(draft_sequence, bool)
        or not isinstance(review_sequence, int)
        or isinstance(review_sequence, bool)
        or review_sequence <= draft_sequence
        or last_review.get("review", {}).get("decision") != "PASS"
        or accepted.get("draft_provider") != "codex"
        or accepted.get("review_provider") != "codex"
        or accepted.get("review_mode") != "same_provider_separate_pass"
        or accepted.get("draft") != current_draft
        or last_draft.get("draft") != current_draft
        or accepted_review != last_review.get("review")
    ):
        raise Mix2KV4RecoveryError(
            "call 149 recovery 행이 실제 Codex draft·별도 review PASS를 "
            f"끝내지 않았습니다: {record_id}"
        )


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """세 번째 sidecar와 현재 descendant state를 함께 검증한다."""

    before_payload = _read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 149 recovery before state"
    )
    after_payload = _read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 149 recovery after state"
    )
    manifest_payload = _read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 149 recovery manifest"
    )
    before_state = _decode_object(before_payload, "call 149 recovery before state")
    after_state = _decode_object(after_payload, "call 149 recovery after state")
    manifest = _decode_object(manifest_payload, "call 149 recovery manifest")
    expected_after = build_recovered_state(before_state, before_payload)
    expected_events = [
        first_recovery_event(),
        second_recovery_event(),
        _recovery_event(),
    ]
    provider_calls = current_state.get("provider_calls")
    if (
        target.name != TARGET_NAME
        or after_payload != _json_bytes(expected_after)
        or after_state != expected_after
        or manifest != _expected_manifest(after_payload)
        or current_state.get("operator_recoveries") != expected_events
        or current_state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or current_state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("call 149 operator recovery bundle이 다릅니다.")

    current_records = current_state.get("records")
    after_records = after_state["records"]
    if not isinstance(current_records, Mapping):
        raise Mix2KV4RecoveryError("call 149 recovery descendant record가 없습니다.")
    for record_id in FAILED_RECORD_IDS:
        current = current_records.get(record_id)
        checkpoint = after_records[record_id]
        if (
            not isinstance(current, Mapping)
            or current.get("rewrites_used") != checkpoint["rewrites_used"]
            or current.get("duplicate_rewrites_used")
            != checkpoint["duplicate_rewrites_used"]
        ):
            raise Mix2KV4RecoveryError(
                f"call 149 recovery 이후 counter가 다릅니다: {record_id}"
            )
        later_drafts, later_reviews = _later_attempts(
            current, checkpoint, record_id=record_id
        )
        _validate_later_provenance(
            later_drafts, later_reviews, record_id=record_id
        )
        if require_completed_provider_passes:
            _validate_completed_record(
                current, later_drafts, later_reviews, record_id=record_id
            )
    return {
        "recovery_id": RECOVERY_ID,
        "manifest_sha256": sha256_bytes(manifest_payload),
        "before_state_sha256": sha256_bytes(before_payload),
        "after_state_sha256": sha256_bytes(after_payload),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "provider_draft_and_separate_review_passed": (
            require_completed_provider_passes
        ),
    }


def _validate_sidecar_chain(target: Path, event_count: int) -> None:
    paths = (
        target / FIRST_RECOVERY_MANIFEST_RELATIVE,
        target / SECOND_RECOVERY_MANIFEST_RELATIVE,
        target / RECOVERY_MANIFEST_RELATIVE,
    )
    existence = tuple(os.path.lexists(path) for path in paths)
    expected = tuple(index < event_count for index in range(3))
    if existence != expected:
        raise Mix2KV4RecoveryError(
            "operator recovery state event와 sidecar chain이 일치하지 않습니다."
        )


def _validate_attempt_growth(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    event_count: int,
    require_completed_provider_passes: bool,
) -> None:
    """각 operator recovery가 허용한 추가 draft·review를 정확히 한 쌍으로 제한한다."""

    checkpoints = (
        (
            "call 60",
            FIRST_RECOVERY_AFTER_STATE_RELATIVE,
            FIRST_RECOVERY_AFFECTED_RECORD_IDS,
        ),
        (
            "call 148",
            SECOND_RECOVERY_AFTER_STATE_RELATIVE,
            SECOND_RECOVERY_AFFECTED_RECORD_IDS,
        ),
        ("call 149", AFTER_STATE_RELATIVE, FAILED_RECORD_IDS),
    )
    current_records = current_state.get("records")
    if not isinstance(current_records, Mapping):
        raise Mix2KV4RecoveryError("operator recovery descendant record가 없습니다.")
    for index, (label, relative_path, record_ids) in enumerate(checkpoints, start=1):
        if index > event_count:
            break
        checkpoint_payload = _read_regular_file(
            target / relative_path, f"{label} recovery after state"
        )
        checkpoint = _decode_object(
            checkpoint_payload, f"{label} recovery after state"
        )
        checkpoint_records = checkpoint.get("records")
        if not isinstance(checkpoint_records, Mapping):
            raise Mix2KV4RecoveryError(
                f"{label} recovery checkpoint record가 없습니다."
            )
        for record_id in record_ids:
            current = current_records.get(record_id)
            saved = checkpoint_records.get(record_id)
            if not isinstance(current, Mapping) or not isinstance(saved, Mapping):
                raise Mix2KV4RecoveryError(
                    f"{label} recovery record가 없습니다: {record_id}"
                )
            current_drafts = current.get("draft_attempts")
            saved_drafts = saved.get("draft_attempts")
            current_reviews = current.get("review_attempts")
            saved_reviews = saved.get("review_attempts")
            if not all(
                isinstance(value, list)
                for value in (
                    current_drafts,
                    saved_drafts,
                    current_reviews,
                    saved_reviews,
                )
            ):
                raise Mix2KV4RecoveryError(
                    f"{label} recovery attempt 목록이 없습니다: {record_id}"
                )
            draft_growth = len(current_drafts) - len(saved_drafts)
            review_growth = len(current_reviews) - len(saved_reviews)
            expected_growth = 1 if require_completed_provider_passes else None
            invalid_progress = (
                draft_growth not in {0, 1}
                or review_growth not in {0, 1}
                or review_growth > draft_growth
            )
            invalid_completed = expected_growth is not None and (
                draft_growth != expected_growth or review_growth != expected_growth
            )
            if invalid_progress or invalid_completed:
                raise Mix2KV4RecoveryError(
                    f"{label} recovery 추가 attempt 수가 다릅니다: {record_id}"
                )


def validate_recovery_chain(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any] | None:
    """알려진 1·2·3차 recovery bundle과 attempt 예외 집합을 검증한다."""

    events = current_state.get("operator_recoveries")
    first_event = first_recovery_event()
    second_event = second_recovery_event()
    third_event = _recovery_event()
    known_chains = (
        [first_event],
        [first_event, second_event],
        [first_event, second_event, third_event],
    )
    overflow_ids = _attempt_overflow_ids(current_state)
    sidecar_signaled = any(
        os.path.lexists(path)
        for path in (
            target / FIRST_RECOVERY_MANIFEST_RELATIVE,
            target / SECOND_RECOVERY_MANIFEST_RELATIVE,
            target / RECOVERY_MANIFEST_RELATIVE,
        )
    )
    if events is None and not overflow_ids and not sidecar_signaled:
        return None
    if events not in known_chains:
        raise Mix2KV4RecoveryError("알 수 없는 operator recovery event chain입니다.")

    event_count = len(events)
    _validate_sidecar_chain(target, event_count)
    first_state = deepcopy(dict(current_state))
    first_state["operator_recoveries"] = [first_event]
    first_report = validate_first_recovery_bundle(
        target,
        first_state,
        require_completed_provider_passes=(
            event_count >= 2 or require_completed_provider_passes
        ),
    )
    reports = [first_report]

    if event_count >= 2:
        second_state = deepcopy(dict(current_state))
        second_state["operator_recoveries"] = [first_event, second_event]
        second_report = validate_second_recovery_bundle(
            target,
            second_state,
            require_completed_provider_passes=require_completed_provider_passes,
        )
        reports.append(second_report)
    if event_count == 3:
        reports.append(
            validate_recovery_bundle(
                target,
                current_state,
                require_completed_provider_passes=require_completed_provider_passes,
            )
        )

    _validate_attempt_growth(
        target,
        current_state,
        event_count=event_count,
        require_completed_provider_passes=require_completed_provider_passes,
    )

    expected_first = {FIRST_RECOVERY_OVERFLOW_ID}
    expected_second = expected_first | set(SECOND_RECOVERY_OVERFLOW_IDS)
    expected_third = expected_second | set(FAILED_RECORD_IDS)
    expected_by_count = {
        1: expected_first,
        2: expected_second,
        3: expected_third,
    }
    expected_overflow = expected_by_count[event_count]
    if require_completed_provider_passes:
        if overflow_ids != expected_overflow:
            raise Mix2KV4RecoveryError(
                "완료된 operator recovery attempt 예외 집합이 다릅니다."
            )
    else:
        required_overflow = expected_first if event_count >= 2 else set()
        if (
            not required_overflow.issubset(overflow_ids)
            or not overflow_ids.issubset(expected_overflow)
        ):
            raise Mix2KV4RecoveryError(
                "진행 중 operator recovery attempt 예외 집합이 다릅니다."
            )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": reports,
    }


def _write_or_verify_bundle_file(path: Path, payload: bytes, label: str) -> None:
    if os.path.lexists(path):
        if _read_regular_file(path, label) != payload:
            raise Mix2KV4RecoveryError(f"불완전한 {label} 내용이 다릅니다.")
        return
    _atomic_write(path, payload)


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
        raise Mix2KV4RecoveryError("허용된 call 149 recovery target이 아닙니다.")
    _reject_symlink_components(target, "call 149 recovery target")
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
                prior_report = validate_recovery_chain(
                    target,
                    state,
                    require_completed_provider_passes=False,
                )
                if prior_report is None:
                    raise Mix2KV4RecoveryError(
                        "불완전한 call 149 recovery 이전 이력이 없습니다."
                    )
                recovered = build_recovered_state(state, state_payload)
                after_payload = _json_bytes(recovered)
                recovery_manifest = _expected_manifest(after_payload)
                _ensure_private_directory(
                    recovery_dir, "call 149 partial recovery provenance"
                )
                _write_or_verify_bundle_file(
                    target / BEFORE_STATE_RELATIVE,
                    state_payload,
                    "call 149 recovery before state",
                )
                _write_or_verify_bundle_file(
                    target / AFTER_STATE_RELATIVE,
                    after_payload,
                    "call 149 recovery after state",
                )
                _write_or_verify_bundle_file(
                    manifest_path,
                    _json_bytes(recovery_manifest),
                    "call 149 recovery manifest",
                )
                validate_recovery_bundle(
                    target,
                    recovered,
                    require_completed_provider_passes=False,
                )
                _atomic_write(state_path, after_payload)
                report = validate_recovery_chain(
                    target,
                    recovered,
                    require_completed_provider_passes=False,
                )
                if report is None:
                    raise Mix2KV4RecoveryError(
                        "부분 call 149 recovery 검증 결과가 없습니다."
                    )
                report["already_applied"] = False
                report["resumed_prepared_bundle"] = True
                return report
            if (
                sha256_bytes(state_payload) == EXPECTED_PRE_STATE_SHA256
                and state.get("operator_recoveries")
                == [first_recovery_event(), second_recovery_event()]
            ):
                first_state = deepcopy(state)
                first_state["operator_recoveries"] = [first_recovery_event()]
                validate_first_recovery_bundle(
                    target,
                    first_state,
                    require_completed_provider_passes=True,
                )
                validate_second_recovery_bundle(
                    target,
                    state,
                    require_completed_provider_passes=False,
                )
                prior_overflows = _attempt_overflow_ids(state)
                expected_prior_overflows = {
                    FIRST_RECOVERY_OVERFLOW_ID,
                    *SECOND_RECOVERY_OVERFLOW_IDS,
                }
                if (
                    FIRST_RECOVERY_OVERFLOW_ID not in prior_overflows
                    or not prior_overflows.issubset(expected_prior_overflows)
                ):
                    raise Mix2KV4RecoveryError(
                        "prepared recovery 이전 attempt 예외 집합이 다릅니다."
                    )
                before_payload = _read_regular_file(
                    target / BEFORE_STATE_RELATIVE,
                    "prepared call 149 recovery before state",
                )
                after_payload = _read_regular_file(
                    target / AFTER_STATE_RELATIVE,
                    "prepared call 149 recovery after state",
                )
                after_state = _decode_object(
                    after_payload, "prepared call 149 recovery after state"
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
                        "prepared call 149 recovery 검증 결과가 없습니다."
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
                raise Mix2KV4RecoveryError("call 149 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        prior_report = validate_recovery_chain(
            target,
            state,
            require_completed_provider_passes=False,
        )
        if prior_report is None:
            raise Mix2KV4RecoveryError("call 149 이전 recovery chain이 없습니다.")
        recovered = build_recovered_state(state, state_payload)
        after_payload = _json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        _ensure_private_directory(recovery_dir, "call 149 operator recovery provenance")
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
            raise Mix2KV4RecoveryError("call 149 recovery 검증 결과가 없습니다.")
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 149 checkpoint의 감사 가능한 복구"
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
