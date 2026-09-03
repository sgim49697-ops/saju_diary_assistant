# mix2k_v4_teacher_recovery_call177.py - call 177 길이 FAIL 3건을 여섯 번째 감사 복구로 재개한다.

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

from scripts.data import mix2k_v4_teacher_recovery as base_recovery
from scripts.data import mix2k_v4_teacher_recovery_call148 as recovery_call148
from scripts.data import mix2k_v4_teacher_recovery_call174 as prior_recovery
from scripts.data import mix2k_v4_teachers as teachers
from scripts.data.mix2k_v4_contracts import sentence_count, sha256_bytes, sha256_file

Mix2KV4RecoveryError = base_recovery.Mix2KV4RecoveryError

RECOVERY_ID = "operator-recovery-provider-call-177-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "74055a4ad1ee5f1ef1b80fecd66e7c0a22d38d0633ea76168dcc376149373f3e"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 177
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
AFFECTED_RECORD_IDS = tuple(prior_recovery.AFFECTED_RECORD_IDS)
EXPECTED_OVERFLOW_IDS = frozenset(prior_recovery.FINAL_OVERFLOW_IDS)

OLD_FEEDBACK_BY_ID = {
    AFFECTED_RECORD_IDS[0]: (
        '{"fact_errors":[],"failure_codes":['
        '"PRODUCTION_INSTRUCTION_LENGTH_VIOLATION"],'
        '"rewrite_instructions":"production instruction의 1~3문장 제한을 지키도록 현재 '
        "4문장을 3문장으로 합치세요. 최소 3문장·3개 비어 있지 않은 줄도 충족해야 "
        "하므로, 마지막 두 문장의 내용을 한 문장으로 자연스럽게 통합해 검증된 "
        "년주·월주·일주·시주만으로 신강약·용신을 새로 판정하지 않으며 별도로 검증된 "
        '판단이 필요하다고 안내하세요.","style_notes":[]}'
    ),
    AFFECTED_RECORD_IDS[1]: (
        '{"fact_errors":[],"failure_codes":['
        '"PRODUCTION_INSTRUCTION_LENGTH_VIOLATION"],'
        '"rewrite_instructions":"production instruction의 1~3문장 제한을 지키도록 '
        "4문장을 3문장으로 합치세요. 최소 3문장·3개 비어 있지 않은 줄을 유지하면서, "
        "검증된 년주·월주·일주·시주와 오늘의 간지 자료만으로 관계를 새로 계산하지 않고 "
        '별도로 검증된 관계 결과가 있어야 설명할 수 있다는 점을 명확히 남기세요.",'
        '"style_notes":[]}'
    ),
    AFFECTED_RECORD_IDS[2]: (
        '{"fact_errors":[],"failure_codes":['
        '"PRODUCTION_INSTRUCTION_LENGTH_VIOLATION"],'
        '"rewrite_instructions":"production instruction의 1~3문장 제한을 지키도록 '
        "4문장을 3문장으로 줄이되, 최소 3문장·3개 비어 있지 않은 줄은 유지하세요. "
        "검증된 계산기의 년주·월주·일주·시주와 오늘의 간지뿐 아니라 별도로 검증된 "
        "원국×오늘 관계 결과가 필요하며, 해당 관계를 답변자가 새로 계산하지 않는다는 "
        '내용을 한 문장에 통합하세요.","style_notes":[]}'
    ),
}
RECOVERY_GUIDANCE = (
    "재생성 지시: 정확히 3개 완결 문장·3줄로 작성하고, 기존 제한과 필수 입력을 모두 "
    "유지하세요. 이전 review: "
)
NEW_FEEDBACK_BY_ID = {
    record_id: RECOVERY_GUIDANCE + feedback
    for record_id, feedback in OLD_FEEDBACK_BY_ID.items()
}


def _prior_events() -> list[dict[str, Any]]:
    return [*prior_recovery._prior_events(), prior_recovery._recovery_event()]


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "duplicate_retry_three_line_review_failure",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _expected_failed_review(record_id: str) -> dict[str, Any]:
    review = json.loads(OLD_FEEDBACK_BY_ID[record_id])
    review["decision"] = "FAIL"
    review["record_id"] = record_id
    return review


def _canonical_review_feedback(review: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "failure_codes": review["failure_codes"],
            "fact_errors": review["fact_errors"],
            "style_notes": review["style_notes"],
            "rewrite_instructions": review["rewrite_instructions"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fixed_d5_r3(
    record: Mapping[str, Any], *, record_id: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    drafts = record.get("draft_attempts")
    reviews = record.get("review_attempts")
    if (
        not isinstance(drafts, list)
        or len(drafts) < 5
        or not isinstance(reviews, list)
        or len(reviews) < 3
    ):
        raise Mix2KV4RecoveryError(f"D5/R3 prefix가 없습니다: {record_id}")
    for index, attempt in enumerate(drafts[:5], start=1):
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("attempt") != index
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "draft"
            or not isinstance(attempt.get("draft"), Mapping)
            or attempt["draft"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(f"D5 draft prefix가 다릅니다: {record_id}")
    for index, attempt in enumerate(reviews[:3], start=1):
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("attempt") != index
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "review"
            or attempt.get("review_mode") != "same_provider_separate_pass"
            or not isinstance(attempt.get("review"), Mapping)
            or attempt["review"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(f"R3 review prefix가 다릅니다: {record_id}")
    draft = drafts[4]
    review = reviews[2]
    provider_draft = draft.get("provider_draft")
    draft_payload = draft.get("draft")
    review_payload = review.get("review")
    if (
        draft.get("provider_call_sequence") != 176
        or draft.get("deterministic_pass") is not True
        or draft.get("deterministic_error") is not None
        or not isinstance(provider_draft, Mapping)
        or provider_draft.get("record_id") != record_id
        or not isinstance(draft_payload, Mapping)
        or not isinstance(draft_payload.get("answer"), str)
        or sentence_count(draft_payload["answer"]) != 4
        or review.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
        or review.get("assigned_provider") != "claude"
        or review.get("provider") != "codex"
        or review.get("fallback_used") is not True
        or not isinstance(review_payload, Mapping)
        or review_payload.get("decision") != "FAIL"
        or review_payload.get("failure_codes")
        != ["PRODUCTION_INSTRUCTION_LENGTH_VIOLATION"]
        or review_payload.get("fact_errors") != []
        or review_payload.get("style_notes") != []
        or review_payload != _expected_failed_review(record_id)
        or _canonical_review_feedback(review_payload)
        != OLD_FEEDBACK_BY_ID[record_id]
    ):
        raise Mix2KV4RecoveryError(f"고정 D5@176/R3@177이 다릅니다: {record_id}")
    return draft, review


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
        raise Mix2KV4RecoveryError("여섯 번째 복구 전 pipeline state identity가 다릅니다.")
    for record_id in AFFECTED_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"여섯 번째 복구 전 record가 없습니다: {record_id}"
            )
        draft, _review = _fixed_d5_r3(record, record_id=record_id)
        drafts = record.get("draft_attempts")
        reviews = record.get("review_attempts")
        if (
            record.get("status") != "failed"
            or record.get("feedback") != OLD_FEEDBACK_BY_ID[record_id]
            or record.get("rewrites_used") != 2
            or record.get("duplicate_rewrites_used") != 1
            or record.get("accepted") is not None
            or record.get("current_draft") != draft.get("draft")
            or len(drafts) != 5
            or len(reviews) != 3
        ):
            raise Mix2KV4RecoveryError(
                f"여섯 번째 복구 전 record 계약이 다릅니다: {record_id}"
            )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 177 pre-state에서 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [*_prior_events(), _recovery_event()]
    for record_id in AFFECTED_RECORD_IDS:
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
        for record_id in AFFECTED_RECORD_IDS
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


def _project_prior_recovery_state(
    before_state: Mapping[str, Any],
    before_payload: bytes,
    current_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_pre_state(before_state, before_payload)
    records = before_state.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("audit projection record가 없습니다.")
    projected = deepcopy(dict(current_state or before_state))
    projected["operator_recoveries"] = _prior_events()
    for record_id in AFFECTED_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"audit projection record가 없습니다: {record_id}"
            )
        draft, _review = _fixed_d5_r3(record, record_id=record_id)
        drafts = record.get("draft_attempts")
        reviews = record.get("review_attempts")
        projected_record = deepcopy(dict(record))
        projected_record["draft_attempts"] = deepcopy(drafts[:5])
        projected_record["review_attempts"] = deepcopy(reviews[:2])
        projected_record["status"] = "needs_review"
        projected_record["feedback"] = ""
        projected_record["current_draft"] = deepcopy(draft["draft"])
        projected_record["accepted"] = None
        projected["records"][record_id] = projected_record
    return projected


def _validate_prior_projection(
    target: Path,
    before_state: Mapping[str, Any],
    before_payload: bytes,
    current_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    projected = _project_prior_recovery_state(
        before_state, before_payload, current_state
    )
    report = prior_recovery.validate_recovery_chain(
        target,
        projected,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("projection한 call 174 recovery chain이 없습니다.")
    audit_report = deepcopy(report)
    audit_report["all_recoveries_completed"] = False
    audit_report["audit_status"] = "partial_superseded"
    audit_report["superseded_by"] = RECOVERY_ID
    audit_report["projection_provider_calls"] = projected.get("provider_calls")
    return audit_report


def _validate_unaffected_prior_descendants(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed: bool,
) -> None:
    checkpoint_payload = base_recovery._read_regular_file(
        target / prior_recovery.AFTER_STATE_RELATIVE,
        "call 174 recovery after state",
    )
    checkpoint = base_recovery._decode_object(
        checkpoint_payload, "call 174 recovery after state"
    )
    prior_recovery._validate_current_descendant(
        current_state,
        checkpoint,
        require_completed=require_completed,
    )


def _later_attempts(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
) -> tuple[list[Any], list[Any]]:
    current_drafts = current.get("draft_attempts")
    saved_drafts = checkpoint.get("draft_attempts")
    current_reviews = current.get("review_attempts")
    saved_reviews = checkpoint.get("review_attempts")
    if (
        set(current) != set(checkpoint)
        or current.get("spec_sha256") != checkpoint.get("spec_sha256")
        or current.get("rewrites_used") != checkpoint.get("rewrites_used")
        or not isinstance(current_drafts, list)
        or not isinstance(saved_drafts, list)
        or not isinstance(current_reviews, list)
        or not isinstance(saved_reviews, list)
        or current_drafts[: len(saved_drafts)] != saved_drafts
        or current_reviews[: len(saved_reviews)] != saved_reviews
    ):
        raise Mix2KV4RecoveryError(
            f"call 177 recovery 이후 record·attempt prefix가 다릅니다: {record_id}"
        )
    later_drafts = current_drafts[len(saved_drafts) :]
    later_reviews = current_reviews[len(saved_reviews) :]
    return later_drafts, later_reviews


def _validate_retry_progress(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
    provider_calls: int,
    require_completed: bool,
) -> None:
    later_drafts, later_reviews = _later_attempts(
        current, checkpoint, record_id=record_id
    )
    for raw_draft in later_drafts:
        draft_payload = (
            raw_draft.get("draft") if isinstance(raw_draft, Mapping) else None
        )
        answer = (
            draft_payload.get("answer")
            if isinstance(draft_payload, Mapping)
            else None
        )
        if (
            not isinstance(answer, str)
            or sentence_count(answer) != 3
            or len([line for line in answer.splitlines() if line.strip()]) != 3
        ):
            raise Mix2KV4RecoveryError(
                f"call 177 recovery 후속 draft 길이가 다릅니다: {record_id}"
            )
    if not later_drafts:
        if current != checkpoint or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 177 recovery D6가 완료되지 않았습니다: {record_id}"
            )
        return

    saved_drafts = checkpoint["draft_attempts"]
    saved_reviews = checkpoint["review_attempts"]
    draft = prior_recovery._validate_pass_draft(
        later_drafts[0],
        record_id=record_id,
        attempt_number=len(saved_drafts) + 1,
        after_sequence=EXPECTED_PROVIDER_CALLS,
        provider_calls=provider_calls,
    )
    expected = deepcopy(dict(checkpoint))
    expected["draft_attempts"].append(deepcopy(dict(draft)))
    expected["status"] = "needs_review"
    expected["feedback"] = ""
    expected["current_draft"] = deepcopy(draft["draft"])
    expected["accepted"] = None
    if not later_reviews:
        if current != expected or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 177 recovery R4가 완료되지 않았습니다: {record_id}"
            )
        return

    review = prior_recovery._validate_pass_review(
        later_reviews[0],
        record_id=record_id,
        attempt_number=len(saved_reviews) + 1,
        after_sequence=int(draft["provider_call_sequence"]),
        provider_calls=provider_calls,
    )
    expected["review_attempts"].append(deepcopy(dict(review)))
    expected["status"] = "accepted"
    expected["accepted"] = prior_recovery._accepted_from_attempts(draft, review)
    prior_recovery._validate_duplicate_descendant(
        current,
        expected,
        record_id=record_id,
        provider_calls=provider_calls,
        base_duplicate_rewrites=1,
        require_completed=require_completed,
    )


def _validate_checkpoint_layout(checkpoint: Mapping[str, Any]) -> None:
    records = checkpoint.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("call 177 checkpoint record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 177 checkpoint record가 없습니다: {record_id}"
            )
        draft, _review = _fixed_d5_r3(record, record_id=record_id)
        if (
            record.get("status") != "needs_draft"
            or record.get("feedback") != NEW_FEEDBACK_BY_ID[record_id]
            or record.get("rewrites_used") != 2
            or record.get("duplicate_rewrites_used") != 1
            or record.get("accepted") is not None
            or record.get("current_draft") != draft.get("draft")
            or len(record.get("draft_attempts", [])) != 5
            or len(record.get("review_attempts", [])) != 3
        ):
            raise Mix2KV4RecoveryError(
                f"call 177 checkpoint record 계약이 다릅니다: {record_id}"
            )


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """여섯 번째 sidecar와 D6·R4 한 쌍의 실제 provenance를 검증한다."""

    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 177 recovery before state"
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 177 recovery after state"
    )
    manifest_payload = base_recovery._read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 177 recovery manifest"
    )
    before_state = base_recovery._decode_object(
        before_payload, "call 177 recovery before state"
    )
    after_state = base_recovery._decode_object(
        after_payload, "call 177 recovery after state"
    )
    manifest = base_recovery._decode_object(
        manifest_payload, "call 177 recovery manifest"
    )
    expected_after = build_recovered_state(before_state, before_payload)
    provider_calls = current_state.get("provider_calls")
    if (
        target.name != TARGET_NAME
        or after_payload != teachers._json_bytes(expected_after)
        or after_state != expected_after
        or manifest != _expected_manifest(after_payload)
        or current_state.get("operator_recoveries")
        != [*_prior_events(), _recovery_event()]
        or current_state.get("runner_sha256") != EXPECTED_RUNNER_SHA256
        or current_state.get("contracts_sha256") != EXPECTED_CONTRACTS_SHA256
        or not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
        or provider_calls < EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("call 177 operator recovery bundle이 다릅니다.")

    _validate_checkpoint_layout(after_state)
    prior_recovery._validate_descendant_payload_contracts(
        current_state, after_state
    )
    current_records = current_state.get("records")
    checkpoint_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 177 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        current = current_records.get(record_id)
        checkpoint = checkpoint_records.get(record_id)
        if not isinstance(current, Mapping) or not isinstance(checkpoint, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 177 recovery descendant record가 없습니다: {record_id}"
            )
        _validate_retry_progress(
            current,
            checkpoint,
            record_id=record_id,
            provider_calls=provider_calls,
            require_completed=require_completed_provider_passes,
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


def validate_recovery_chain(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any] | None:
    """call 174 audit projection과 실제 R3/D6/R4 이력을 분리해 검증한다."""

    events = current_state.get("operator_recoveries")
    expected_events = [*_prior_events(), _recovery_event()]
    recovery_dir_exists = os.path.lexists(target / RECOVERY_DIR_RELATIVE)
    manifest_exists = os.path.lexists(target / RECOVERY_MANIFEST_RELATIVE)
    if events != expected_events:
        if not recovery_dir_exists and not manifest_exists:
            return prior_recovery.validate_recovery_chain(
                target,
                current_state,
                require_completed_provider_passes=(
                    require_completed_provider_passes
                ),
            )
        raise Mix2KV4RecoveryError(
            "여섯 번째 recovery sidecar와 state event가 일치하지 않습니다."
        )
    if not recovery_dir_exists or not manifest_exists:
        raise Mix2KV4RecoveryError(
            "여섯 번째 recovery state event에 대응하는 sidecar가 없습니다."
        )

    sixth_report = validate_recovery_bundle(
        target,
        current_state,
        require_completed_provider_passes=require_completed_provider_passes,
    )
    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 177 recovery before state"
    )
    before_state = base_recovery._decode_object(
        before_payload, "call 177 recovery before state"
    )
    prior_report = _validate_prior_projection(
        target, before_state, before_payload, current_state
    )
    _validate_unaffected_prior_descendants(
        target,
        current_state,
        require_completed=require_completed_provider_passes,
    )
    overflow_ids = recovery_call148._attempt_overflow_ids(current_state)
    if require_completed_provider_passes:
        if overflow_ids != EXPECTED_OVERFLOW_IDS:
            raise Mix2KV4RecoveryError(
                "완료된 여섯 번째 recovery attempt 예외 집합이 다릅니다."
            )
    elif not overflow_ids.issubset(EXPECTED_OVERFLOW_IDS):
        raise Mix2KV4RecoveryError(
            "진행 중 여섯 번째 recovery attempt 예외 집합이 다릅니다."
        )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": [*prior_report["recoveries"], sixth_report],
        "prior_recovery_projection": {
            "all_recoveries_completed": prior_report[
                "all_recoveries_completed"
            ],
            "audit_status": prior_report["audit_status"],
            "superseded_by": prior_report["superseded_by"],
            "projection_provider_calls": prior_report[
                "projection_provider_calls"
            ],
        },
    }


def _validate_incident_pre_state(
    target: Path, state: Mapping[str, Any], payload: bytes
) -> None:
    _validate_pre_state(state, payload)
    _validate_prior_projection(target, state, payload)
    if recovery_call148._attempt_overflow_ids(state) != EXPECTED_OVERFLOW_IDS:
        raise Mix2KV4RecoveryError("call 177 pre-state attempt 예외 집합이 다릅니다.")


def _write_or_verify_bundle_file(path: Path, payload: bytes, label: str) -> None:
    if os.path.lexists(path):
        if base_recovery._read_regular_file(path, label) != payload:
            raise Mix2KV4RecoveryError(f"불완전한 {label} 내용이 다릅니다.")
        return
    teachers._atomic_write(path, payload)


def _validate_full_chain_before_live_write(
    target: Path, recovered: Mapping[str, Any]
) -> dict[str, Any]:
    report = validate_recovery_chain(
        target,
        recovered,
        require_completed_provider_passes=False,
    )
    if report is None or len(report.get("recoveries", [])) != 6:
        raise Mix2KV4RecoveryError(
            "live write 전 여섯 단계 recovery chain 검증에 실패했습니다."
        )
    return report


def _resume_partial_bundle(
    target: Path,
    state_path: Path,
    state_payload: bytes,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_incident_pre_state(target, state, state_payload)
    recovered = build_recovered_state(state, state_payload)
    after_payload = teachers._json_bytes(recovered)
    manifest_payload = teachers._json_bytes(_expected_manifest(after_payload))
    recovery_dir = target / RECOVERY_DIR_RELATIVE
    teachers._ensure_private_directory(
        recovery_dir, "call 177 partial recovery provenance"
    )
    _write_or_verify_bundle_file(
        target / BEFORE_STATE_RELATIVE,
        state_payload,
        "call 177 recovery before state",
    )
    _write_or_verify_bundle_file(
        target / AFTER_STATE_RELATIVE,
        after_payload,
        "call 177 recovery after state",
    )
    _write_or_verify_bundle_file(
        target / RECOVERY_MANIFEST_RELATIVE,
        manifest_payload,
        "call 177 recovery manifest",
    )
    report = _validate_full_chain_before_live_write(target, recovered)
    teachers._atomic_write(state_path, after_payload)
    report["already_applied"] = False
    report["resumed_prepared_bundle"] = True
    return report


def _finish_prepared_recovery(
    target: Path,
    state_path: Path,
    state_payload: bytes,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_incident_pre_state(target, state, state_payload)
    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE,
        "prepared call 177 recovery before state",
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE,
        "prepared call 177 recovery after state",
    )
    after_state = base_recovery._decode_object(
        after_payload, "prepared call 177 recovery after state"
    )
    if before_payload != state_payload:
        raise Mix2KV4RecoveryError(
            "prepared recovery의 before snapshot이 live state와 다릅니다."
        )
    report = _validate_full_chain_before_live_write(target, after_state)
    teachers._atomic_write(state_path, after_payload)
    report["already_applied"] = False
    report["resumed_prepared_bundle"] = True
    return report


def recover(target: Path) -> dict[str, Any]:
    target = teachers._absolute(target)
    expected_target = teachers._absolute(teachers.DEFAULT_OUTPUT_ROOT / TARGET_NAME)
    if (
        target != expected_target
        or target.is_symlink()
        or not target.is_dir()
        or sha256_file(teachers.RUNNER_PATH) != EXPECTED_RUNNER_SHA256
        or sha256_file(teachers.CONTRACTS_PATH) != EXPECTED_CONTRACTS_SHA256
    ):
        raise Mix2KV4RecoveryError("허용된 call 177 recovery target이 아닙니다.")
    teachers._reject_symlink_components(target, "call 177 recovery target")
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
        state_payload = base_recovery._read_regular_file(
            state_path, "teacher pipeline state"
        )
        state = base_recovery._decode_object(state_payload, "teacher pipeline state")
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
                raise Mix2KV4RecoveryError("call 177 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        _validate_incident_pre_state(target, state, state_payload)
        recovered = build_recovered_state(state, state_payload)
        after_payload = teachers._json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        teachers._ensure_private_directory(
            recovery_dir, "call 177 operator recovery provenance"
        )
        teachers._atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        teachers._atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        teachers._atomic_write(
            manifest_path, teachers._json_bytes(recovery_manifest)
        )
        report = _validate_full_chain_before_live_write(target, recovered)
        teachers._atomic_write(state_path, after_payload)
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 177 checkpoint의 감사 가능한 복구"
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
