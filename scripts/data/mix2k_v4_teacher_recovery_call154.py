# mix2k_v4_teacher_recovery_call154.py - call 154 relation 오탐 4건을 네 번째 감사 복구로 재개한다.

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
    Mix2KV4RecoveryError,
    _decode_object,
    _read_regular_file,
)
from scripts.data.mix2k_v4_teacher_recovery import (
    _recovery_event as first_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    _attempt_overflow_ids,
)
from scripts.data.mix2k_v4_teacher_recovery_call148 import (
    _recovery_event as second_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery_call149 import (
    _recovery_event as third_recovery_event,
)
from scripts.data.mix2k_v4_teacher_recovery_call149 import (
    validate_recovery_chain as validate_prior_recovery_chain,
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

RECOVERY_ID = "operator-recovery-provider-call-154-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "53a6709281505bdb81077a0ac610ff4274d56d7a03174edbb5b183fe95f41b52"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 154
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
FAILED_RECORD_IDS = (
    "m2v4_0f1fc1adf220e2bdf6e6fa85",
    "m2v4_72b78ba78948ce9852dad13c",
    "m2v4_7de74a3e13e9240d3f406fac",
)
PENDING_RECORD_ID = "m2v4_daeffba875b39af3062f99fb"
AFFECTED_RECORD_IDS = (*FAILED_RECORD_IDS, PENDING_RECORD_ID)
PRIOR_OVERFLOW_IDS = frozenset(
    {
        "m2v4_04a03609c2525768fe53777e",
        "m2v4_17f19223c200ce872e34b2d8",
        "m2v4_27ca49a80737af0a331ab558",
        "m2v4_67ad3171b4f72afa5168ecc6",
        "m2v4_91b95e86fca05f83012bc87f",
        "m2v4_9a689b213b285e1bda18f24f",
        "m2v4_b98b22d7d5bbb0186e289392",
    }
)
FINAL_OVERFLOW_IDS = PRIOR_OVERFLOW_IDS | frozenset(FAILED_RECORD_IDS)

OLD_FAILED_FEEDBACK = (
    "teacher 구조 사실 claim 오류: unsupported_structural_claim:relation"
)
OLD_PENDING_FEEDBACK = (
    "Deterministic validator 실패: teacher 구조 사실 claim 오류: "
    "unsupported_structural_claim:relation"
)
SAFE_RELATION_LIMITATION = (
    "이 자료가 있어도 그 관계를 제가 새로 계산하지 않으며, 별도로 검증되어 입력된 "
    "관계만 설명할 수 있습니다."
)
RELATION_REWRITE_GUIDANCE = (
    "재생성 지시: 거절과 필수 입력·도구 제한은 유지하되 마지막 제한은 '"
    + SAFE_RELATION_LIMITATION
    + "'로 그대로 쓰세요. '검증된 합·충 관계 판정 결과'처럼 합·충과 '판정 결과'를 "
    "붙여 새 구조 사실 claim처럼 쓰지 마세요. 이전 오류: "
)
NEW_FAILED_FEEDBACK = RELATION_REWRITE_GUIDANCE + OLD_FAILED_FEEDBACK
NEW_PENDING_FEEDBACK = RELATION_REWRITE_GUIDANCE + OLD_PENDING_FEEDBACK


def _prior_events() -> list[dict[str, Any]]:
    return [
        first_recovery_event(),
        second_recovery_event(),
        third_recovery_event(),
    ]


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "verified_result_requirement_misread_as_relation_claim",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
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
    draft_attempts: int,
    review_attempts: int,
    current_draft_required: bool,
) -> None:
    if not isinstance(record, Mapping):
        raise Mix2KV4RecoveryError(
            f"네 번째 복구 전 record 계약이 다릅니다: {record_id}"
        )
    drafts = record.get("draft_attempts")
    reviews = record.get("review_attempts")
    current_draft = record.get("current_draft")
    valid_current_draft = (
        isinstance(current_draft, Mapping)
        if current_draft_required
        else current_draft is None
    )
    if (
        record.get("status") != status
        or record.get("feedback") != feedback
        or record.get("rewrites_used") != 2
        or record.get("duplicate_rewrites_used") != 0
        or record.get("accepted") is not None
        or not valid_current_draft
        or not isinstance(drafts, list)
        or len(drafts) != draft_attempts
        or not isinstance(reviews, list)
        or len(reviews) != review_attempts
    ):
        raise Mix2KV4RecoveryError(
            f"네 번째 복구 전 record 계약이 다릅니다: {record_id}"
        )
    last_draft = drafts[-1]
    if (
        not isinstance(last_draft, Mapping)
        or last_draft.get("provider") != "codex"
        or last_draft.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
        or last_draft.get("execution_pass") != "draft"
        or last_draft.get("deterministic_pass") is not False
        or last_draft.get("deterministic_error") != feedback.removeprefix(
            "Deterministic validator 실패: "
        )
    ):
        raise Mix2KV4RecoveryError(
            f"네 번째 복구 전 최종 draft 계약이 다릅니다: {record_id}"
        )


def _validate_pre_state(state: Mapping[str, Any], payload: bytes) -> None:
    records = state.get("records")
    if (
        sha256_bytes(payload) != EXPECTED_PRE_STATE_SHA256
        or state.get("schema_version") != "1.3.0"
        or state.get("provider_calls") != EXPECTED_PROVIDER_CALLS
        or state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or state.get("operator_recoveries") != _prior_events()
        or not isinstance(records, Mapping)
    ):
        raise Mix2KV4RecoveryError("네 번째 복구 전 pipeline state identity가 다릅니다.")
    for record_id in FAILED_RECORD_IDS:
        _validate_record_before(
            records.get(record_id),
            record_id=record_id,
            status="failed",
            feedback=OLD_FAILED_FEEDBACK,
            draft_attempts=3,
            review_attempts=1,
            current_draft_required=True,
        )
    _validate_record_before(
        records.get(PENDING_RECORD_ID),
        record_id=PENDING_RECORD_ID,
        status="needs_draft",
        feedback=OLD_PENDING_FEEDBACK,
        draft_attempts=2,
        review_attempts=0,
        current_draft_required=False,
    )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 154 pre-state에서 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [*_prior_events(), _recovery_event()]
    for record_id in FAILED_RECORD_IDS:
        record = recovered["records"][record_id]
        record["status"] = "needs_draft"
        record["feedback"] = NEW_FAILED_FEEDBACK
    recovered["records"][PENDING_RECORD_ID]["feedback"] = NEW_PENDING_FEEDBACK
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    changes = [
        {
            "record_id": record_id,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_FAILED_FEEDBACK,
                "after": NEW_FAILED_FEEDBACK,
            },
        }
        for record_id in FAILED_RECORD_IDS
    ]
    changes.append(
        {
            "record_id": PENDING_RECORD_ID,
            "status": {"before": "needs_draft", "after": "needs_draft"},
            "feedback": {
                "before": OLD_PENDING_FEEDBACK,
                "after": NEW_PENDING_FEEDBACK,
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
        set(current) != set(checkpoint)
        or current.get("spec_sha256") != checkpoint.get("spec_sha256")
        or not isinstance(current_drafts, list)
        or not isinstance(checkpoint_drafts, list)
        or not isinstance(current_reviews, list)
        or not isinstance(checkpoint_reviews, list)
        or current_drafts[: len(checkpoint_drafts)] != checkpoint_drafts
        or current_reviews[: len(checkpoint_reviews)] != checkpoint_reviews
    ):
        raise Mix2KV4RecoveryError(
            f"call 154 recovery 이후 record·attempt prefix가 다릅니다: {record_id}"
        )
    later_drafts = current_drafts[len(checkpoint_drafts) :]
    later_reviews = current_reviews[len(checkpoint_reviews) :]
    if (
        len(later_drafts) > 1
        or len(later_reviews) > 1
        or len(later_reviews) > len(later_drafts)
        or (
            later_drafts
            and (
                not isinstance(later_drafts[0], Mapping)
                or later_drafts[0].get("attempt") != len(checkpoint_drafts) + 1
            )
        )
        or (
            later_reviews
            and (
                not isinstance(later_reviews[0], Mapping)
                or later_reviews[0].get("attempt") != len(checkpoint_reviews) + 1
            )
        )
    ):
        raise Mix2KV4RecoveryError(
            f"call 154 recovery 추가 attempt 수가 다릅니다: {record_id}"
        )
    return later_drafts, later_reviews


def _validate_later_provenance(
    later_drafts: Sequence[Any],
    later_reviews: Sequence[Any],
    *,
    record_id: str,
    provider_calls: int,
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
            or attempt["provider_call_sequence"] > provider_calls
            or attempt["draft"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(
                f"call 154 recovery 이후 draft provenance가 다릅니다: {record_id}"
            )
    for attempt in later_reviews:
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "review"
            or attempt.get("review_mode") != "same_provider_separate_pass"
            or not isinstance(attempt.get("review"), Mapping)
            or not isinstance(attempt.get("provider_call_sequence"), int)
            or isinstance(attempt.get("provider_call_sequence"), bool)
            or attempt["provider_call_sequence"] <= EXPECTED_PROVIDER_CALLS
            or attempt["provider_call_sequence"] > provider_calls
            or attempt["review"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(
                f"call 154 recovery 이후 review provenance가 다릅니다: {record_id}"
            )
    if later_reviews and (
        later_reviews[0]["provider_call_sequence"]
        <= later_drafts[0]["provider_call_sequence"]
    ):
        raise Mix2KV4RecoveryError(
            f"call 154 recovery review가 draft보다 늦지 않습니다: {record_id}"
        )


def _validate_completed_record(
    current: Mapping[str, Any],
    later_drafts: Sequence[Any],
    later_reviews: Sequence[Any],
    *,
    record_id: str,
) -> None:
    if len(later_drafts) != 1 or len(later_reviews) != 1:
        raise Mix2KV4RecoveryError(
            f"call 154 recovery 행의 새 attempt가 정확히 한 쌍이 아닙니다: {record_id}"
        )
    draft_attempt = later_drafts[0]
    review_attempt = later_reviews[0]
    accepted = current.get("accepted")
    current_draft = current.get("current_draft")
    if not (
        isinstance(draft_attempt, Mapping)
        and isinstance(review_attempt, Mapping)
        and isinstance(accepted, Mapping)
        and isinstance(current_draft, Mapping)
        and current_draft
        and isinstance(draft_attempt.get("draft"), Mapping)
        and draft_attempt["draft"]
        and isinstance(accepted.get("draft"), Mapping)
        and accepted["draft"]
    ):
        raise Mix2KV4RecoveryError(
            f"call 154 recovery 완료 provenance가 없습니다: {record_id}"
        )
    review = review_attempt.get("review")
    if (
        current.get("status") != "accepted"
        or draft_attempt.get("deterministic_pass") is not True
        or not isinstance(review, Mapping)
        or review.get("decision") != "PASS"
        or accepted.get("draft_provider") != "codex"
        or accepted.get("review_provider") != "codex"
        or accepted.get("review_mode") != "same_provider_separate_pass"
        or accepted.get("draft") != current_draft
        or draft_attempt.get("draft") != current_draft
        or accepted.get("review") != review
    ):
        raise Mix2KV4RecoveryError(
            "call 154 recovery 행이 실제 Codex draft·별도 review PASS를 "
            f"끝내지 않았습니다: {record_id}"
        )


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """네 번째 sidecar와 현재 descendant state를 함께 검증한다."""

    before_payload = _read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 154 recovery before state"
    )
    after_payload = _read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 154 recovery after state"
    )
    manifest_payload = _read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 154 recovery manifest"
    )
    before_state = _decode_object(before_payload, "call 154 recovery before state")
    after_state = _decode_object(after_payload, "call 154 recovery after state")
    manifest = _decode_object(manifest_payload, "call 154 recovery manifest")
    expected_after = build_recovered_state(before_state, before_payload)
    expected_events = [*_prior_events(), _recovery_event()]
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
        raise Mix2KV4RecoveryError("call 154 operator recovery bundle이 다릅니다.")

    current_records = current_state.get("records")
    after_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        after_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 154 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        current = current_records.get(record_id)
        checkpoint = after_records.get(record_id)
        if (
            not isinstance(current, Mapping)
            or not isinstance(checkpoint, Mapping)
            or current.get("rewrites_used") != checkpoint.get("rewrites_used")
            or current.get("duplicate_rewrites_used")
            != checkpoint.get("duplicate_rewrites_used")
        ):
            raise Mix2KV4RecoveryError(
                f"call 154 recovery 이후 counter가 다릅니다: {record_id}"
            )
        later_drafts, later_reviews = _later_attempts(
            current, checkpoint, record_id=record_id
        )
        _validate_later_provenance(
            later_drafts,
            later_reviews,
            record_id=record_id,
            provider_calls=provider_calls,
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
        "exact_attempt_pairs_required": True,
    }


def _prior_state_for_validation(
    current_state: Mapping[str, Any], after_state: Mapping[str, Any]
) -> dict[str, Any]:
    current_records = current_state.get("records")
    checkpoint_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("이전 recovery 검증용 record가 없습니다.")
    prior_state = deepcopy(dict(current_state))
    prior_state["operator_recoveries"] = _prior_events()
    for record_id in AFFECTED_RECORD_IDS:
        checkpoint = checkpoint_records.get(record_id)
        if not isinstance(checkpoint, Mapping):
            raise Mix2KV4RecoveryError(
                f"이전 recovery 검증용 checkpoint가 없습니다: {record_id}"
            )
        prior_state["records"][record_id] = deepcopy(dict(checkpoint))
    return prior_state


def validate_recovery_chain(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any] | None:
    """완료된 1~3차와 incident-specific 4차 recovery를 연쇄 검증한다."""

    events = current_state.get("operator_recoveries")
    prior_events = _prior_events()
    fourth_events = [*prior_events, _recovery_event()]
    fourth_dir_exists = os.path.lexists(target / RECOVERY_DIR_RELATIVE)
    fourth_manifest_exists = os.path.lexists(target / RECOVERY_MANIFEST_RELATIVE)
    if events != fourth_events:
        if fourth_dir_exists or fourth_manifest_exists:
            raise Mix2KV4RecoveryError(
                "네 번째 recovery sidecar와 state event가 일치하지 않습니다."
            )
        return validate_prior_recovery_chain(
            target,
            current_state,
            require_completed_provider_passes=require_completed_provider_passes,
        )
    if not fourth_dir_exists or not fourth_manifest_exists:
        raise Mix2KV4RecoveryError(
            "네 번째 recovery state event에 대응하는 sidecar가 없습니다."
        )

    fourth_report = validate_recovery_bundle(
        target,
        current_state,
        require_completed_provider_passes=require_completed_provider_passes,
    )
    after_payload = _read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 154 recovery after state"
    )
    after_state = _decode_object(after_payload, "call 154 recovery after state")
    prior_state = _prior_state_for_validation(current_state, after_state)
    prior_report = validate_prior_recovery_chain(
        target,
        prior_state,
        require_completed_provider_passes=True,
    )
    if prior_report is None:
        raise Mix2KV4RecoveryError("완료된 이전 recovery chain이 없습니다.")

    overflow_ids = _attempt_overflow_ids(current_state)
    if require_completed_provider_passes:
        if overflow_ids != FINAL_OVERFLOW_IDS:
            raise Mix2KV4RecoveryError(
                "완료된 네 번째 recovery attempt 예외 집합이 다릅니다."
            )
    elif (
        not PRIOR_OVERFLOW_IDS.issubset(overflow_ids)
        or not overflow_ids.issubset(FINAL_OVERFLOW_IDS)
    ):
        raise Mix2KV4RecoveryError(
            "진행 중 네 번째 recovery attempt 예외 집합이 다릅니다."
        )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": [*prior_report["recoveries"], fourth_report],
    }


def _write_or_verify_bundle_file(path: Path, payload: bytes, label: str) -> None:
    if os.path.lexists(path):
        if _read_regular_file(path, label) != payload:
            raise Mix2KV4RecoveryError(f"불완전한 {label} 내용이 다릅니다.")
        return
    _atomic_write(path, payload)


def _finish_prepared_recovery(
    target: Path,
    state_path: Path,
    state_payload: bytes,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    validate_prior_recovery_chain(
        target,
        state,
        require_completed_provider_passes=True,
    )
    before_payload = _read_regular_file(
        target / BEFORE_STATE_RELATIVE,
        "prepared call 154 recovery before state",
    )
    after_payload = _read_regular_file(
        target / AFTER_STATE_RELATIVE,
        "prepared call 154 recovery after state",
    )
    after_state = _decode_object(
        after_payload, "prepared call 154 recovery after state"
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
    report = validate_recovery_chain(
        target,
        after_state,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("prepared call 154 recovery 검증 결과가 없습니다.")
    _atomic_write(state_path, after_payload)
    report["already_applied"] = False
    report["resumed_prepared_bundle"] = True
    return report


def _resume_partial_bundle(
    target: Path,
    state_path: Path,
    state_payload: bytes,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    prior_report = validate_prior_recovery_chain(
        target,
        state,
        require_completed_provider_passes=True,
    )
    if prior_report is None:
        raise Mix2KV4RecoveryError("불완전한 call 154 recovery 이전 이력이 없습니다.")
    recovered = build_recovered_state(state, state_payload)
    after_payload = _json_bytes(recovered)
    recovery_manifest = _expected_manifest(after_payload)
    recovery_dir = target / RECOVERY_DIR_RELATIVE
    _ensure_private_directory(recovery_dir, "call 154 partial recovery provenance")
    _write_or_verify_bundle_file(
        target / BEFORE_STATE_RELATIVE,
        state_payload,
        "call 154 recovery before state",
    )
    _write_or_verify_bundle_file(
        target / AFTER_STATE_RELATIVE,
        after_payload,
        "call 154 recovery after state",
    )
    _write_or_verify_bundle_file(
        target / RECOVERY_MANIFEST_RELATIVE,
        _json_bytes(recovery_manifest),
        "call 154 recovery manifest",
    )
    validate_recovery_bundle(
        target,
        recovered,
        require_completed_provider_passes=False,
    )
    report = validate_recovery_chain(
        target,
        recovered,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("부분 call 154 recovery 검증 결과가 없습니다.")
    _atomic_write(state_path, after_payload)
    report["already_applied"] = False
    report["resumed_prepared_bundle"] = True
    return report


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
        raise Mix2KV4RecoveryError("허용된 call 154 recovery target이 아닙니다.")
    _reject_symlink_components(target, "call 154 recovery target")
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
                return _resume_partial_bundle(
                    target, state_path, state_payload, state
                )
            if (
                sha256_bytes(state_payload) == EXPECTED_PRE_STATE_SHA256
                and state.get("operator_recoveries") == _prior_events()
            ):
                return _finish_prepared_recovery(
                    target, state_path, state_payload, state
                )
            report = validate_recovery_chain(
                target,
                state,
                require_completed_provider_passes=False,
            )
            if report is None:
                raise Mix2KV4RecoveryError("call 154 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        prior_report = validate_prior_recovery_chain(
            target,
            state,
            require_completed_provider_passes=True,
        )
        if prior_report is None:
            raise Mix2KV4RecoveryError("call 154 이전 recovery chain이 없습니다.")
        recovered = build_recovered_state(state, state_payload)
        after_payload = _json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        _ensure_private_directory(recovery_dir, "call 154 operator recovery provenance")
        _atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        _atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        _atomic_write(manifest_path, _json_bytes(recovery_manifest))
        report = validate_recovery_chain(
            target,
            recovered,
            require_completed_provider_passes=False,
        )
        if report is None:
            raise Mix2KV4RecoveryError("call 154 recovery 검증 결과가 없습니다.")
        _atomic_write(state_path, after_payload)
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 154 checkpoint의 감사 가능한 복구"
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
