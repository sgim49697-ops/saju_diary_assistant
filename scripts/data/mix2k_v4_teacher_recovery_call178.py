# mix2k_v4_teacher_recovery_call178.py - call 178 부정형 validator 오탐을 일곱 번째 감사 복구로 재개한다.

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
from scripts.data import mix2k_v4_teacher_recovery_call174 as recovery_call174
from scripts.data import mix2k_v4_teacher_recovery_call177 as prior_recovery
from scripts.data import mix2k_v4_teachers as teachers
from scripts.data.mix2k_v4_contracts import (
    Mix2KV4ContractError,
    sentence_count,
    sha256_bytes,
    sha256_file,
    validate_draft,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

Mix2KV4RecoveryError = base_recovery.Mix2KV4RecoveryError

RECOVERY_ID = "operator-recovery-provider-call-178-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "0d90068687419f473393cda2fcf2ac282ea7a21db62adb29e1b3a067fd8d0fba"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 178
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"

STRENGTH_RECORD_ID = "m2v4_99b224076cafeba65e696b29"
GUARD_RECORD_ID = "m2v4_dc9ad501f2460c7db35d1e80"
AFFECTED_RECORD_IDS = (STRENGTH_RECORD_ID, GUARD_RECORD_ID)
UNAFFECTED_CALL177_RECORD_IDS = tuple(
    record_id
    for record_id in prior_recovery.AFFECTED_RECORD_IDS
    if record_id != STRENGTH_RECORD_ID
)
EXPECTED_OVERFLOW_IDS = frozenset(prior_recovery.EXPECTED_OVERFLOW_IDS)
OLD_ERROR = (
    "teacher 구조 사실 claim 오류: "
    "unsupported_structural_claim:strength_pattern_yongshin"
)
OLD_FEEDBACK_BY_ID = {
    STRENGTH_RECORD_ID: OLD_ERROR,
    GUARD_RECORD_ID: "Deterministic validator 실패: " + OLD_ERROR,
}
NEW_FEEDBACK_BY_ID = {
    STRENGTH_RECORD_ID: (
        "재생성 지시: 정확히 3개 완결 문장·3개 비어 있지 않은 줄을 유지하세요. "
        "첫 문장에서는 `용신과 신강약을 정할 수 없습니다`처럼 두 용어를 묶어 "
        "명시적으로 거절하고, 이후에는 두 용어를 다시 목적어로 쓰지 말고 `두 판단`으로 "
        "지칭하세요. 마지막 문장은 `네 기둥이 있어도 두 판단을 제가 새로 만들지 않으며, "
        "별도로 검증되어 입력된 판단만 설명할 수 있습니다`라는 구조로 작성하세요. "
        "이전 deterministic validator: " + OLD_ERROR
    ),
    GUARD_RECORD_ID: (
        "재생성 지시: surface five elements의 개수만으로 결론을 낼 수 없다는 사실은 "
        "유지하되, 답변에서는 `신강약 판정`을 반복하지 마세요. `표면 오행 개수만으로는 "
        "해당 판단을 내릴 수 없으며, 별도로 검증되어 입력된 결과만 설명할 수 있습니다`처럼 "
        "짧고 직접적으로 다시 작성하세요. 이전 deterministic validator: " + OLD_ERROR
    ),
}


def _prior_events() -> list[dict[str, Any]]:
    return [*prior_recovery._prior_events(), prior_recovery._recovery_event()]


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "negated_structural_claim_validator_false_positive",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _load_fixed_specs(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return recovery_call174._load_fixed_specs(state)


def _validate_failed_draft_payload(
    attempt: Mapping[str, Any], *, record_id: str, spec: Mapping[str, Any]
) -> None:
    provider_draft = attempt.get("provider_draft")
    draft = attempt.get("draft")
    assigned_provider = spec.get("drafter")
    if (
        set(attempt) != recovery_call174.DRAFT_ATTEMPT_FIELDS
        or attempt.get("assigned_provider") != assigned_provider
        or attempt.get("fallback_used")
        is not (attempt.get("provider") != assigned_provider)
        or not isinstance(provider_draft, Mapping)
        or set(provider_draft) != recovery_call174.DRAFT_PAYLOAD_FIELDS
        or provider_draft.get("record_id") != record_id
        or not isinstance(draft, Mapping)
        or set(draft) != recovery_call174.DRAFT_PAYLOAD_FIELDS
    ):
        raise Mix2KV4RecoveryError(
            f"call 178 실패 draft provenance가 다릅니다: {record_id}"
        )

    candidate = deepcopy(dict(provider_draft))
    if not all(
        isinstance(candidate.get(field), list)
        and all(isinstance(value, str) for value in candidate[field])
        for field in ("used_fact_paths", "used_fact_values")
    ):
        raise Mix2KV4RecoveryError(
            f"call 178 실패 provider draft schema가 다릅니다: {record_id}"
        )
    for field in ("used_fact_paths", "used_fact_values"):
        candidate[field] = list(dict.fromkeys(candidate[field]))
    candidate, particle_normalized = teachers._normalize_draft_answer_particles(
        candidate
    )
    candidate, layout_normalized = teachers._normalize_draft_answer_layout(
        spec, candidate
    )
    try:
        validate_draft(spec, candidate)
    except Mix2KV4ContractError as exc:
        reproduced_error = str(exc)
    else:
        raise Mix2KV4RecoveryError(
            f"call 178 실패 draft가 더 이상 동일하게 실패하지 않습니다: {record_id}"
        )
    if (
        reproduced_error != OLD_ERROR
        or dict(draft) != candidate
        or attempt.get("deterministic_pass") is not False
        or attempt.get("deterministic_error") != OLD_ERROR
        or attempt.get("particle_normalized") is not particle_normalized
        or attempt.get("particle_normalizer_version")
        != (teachers.PARTICLE_NORMALIZER_VERSION if particle_normalized else None)
        or attempt.get("layout_normalized") is not layout_normalized
        or attempt.get("layout_normalizer_version")
        != (teachers.LAYOUT_NORMALIZER_VERSION if layout_normalized else None)
    ):
        raise Mix2KV4RecoveryError(
            f"call 178 실패 draft 재생 결과가 다릅니다: {record_id}"
        )


def _fixed_failed_attempt(
    record: Mapping[str, Any], *, record_id: str, spec: Mapping[str, Any]
) -> Mapping[str, Any]:
    drafts = record.get("draft_attempts")
    if not isinstance(drafts, list):
        raise Mix2KV4RecoveryError(f"call 178 draft 이력이 없습니다: {record_id}")
    expected_attempt = 6 if record_id == STRENGTH_RECORD_ID else 2
    if len(drafts) != expected_attempt:
        raise Mix2KV4RecoveryError(f"call 178 draft prefix가 다릅니다: {record_id}")
    attempt = recovery_call174._validate_attempt_common(
        drafts[-1],
        record_id=record_id,
        execution_pass="draft",
        expected_sequence=EXPECTED_PROVIDER_CALLS,
    )
    if attempt.get("attempt") != expected_attempt:
        raise Mix2KV4RecoveryError(f"call 178 draft ordinal이 다릅니다: {record_id}")
    _validate_failed_draft_payload(attempt, record_id=record_id, spec=spec)
    answer = attempt["draft"].get("answer")
    if record_id == STRENGTH_RECORD_ID and (
        not isinstance(answer, str)
        or sentence_count(answer) != 3
        or len([line for line in answer.splitlines() if line.strip()]) != 3
    ):
        raise Mix2KV4RecoveryError("call 178 strength D6 길이가 다릅니다.")
    return attempt


def _validate_strength_pre_record(
    record: Mapping[str, Any], spec: Mapping[str, Any]
) -> None:
    d5, _r3 = prior_recovery._fixed_d5_r3(record, record_id=STRENGTH_RECORD_ID)
    failed = _fixed_failed_attempt(record, record_id=STRENGTH_RECORD_ID, spec=spec)
    if (
        record.get("status") != "failed"
        or record.get("feedback") != OLD_FEEDBACK_BY_ID[STRENGTH_RECORD_ID]
        or record.get("rewrites_used") != 2
        or record.get("duplicate_rewrites_used") != 1
        or record.get("accepted") is not None
        or record.get("current_draft") != d5.get("draft")
        or len(record.get("review_attempts", [])) != 3
        or failed.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("call 178 strength record 계약이 다릅니다.")


def _validate_guard_pre_record(
    record: Mapping[str, Any], spec: Mapping[str, Any]
) -> None:
    drafts = record.get("draft_attempts")
    reviews = record.get("review_attempts")
    if not isinstance(drafts, list) or not isinstance(reviews, list):
        raise Mix2KV4RecoveryError("call 178 guard attempt 이력이 없습니다.")
    passed_draft = recovery_call174._validate_attempt_common(
        drafts[0],
        record_id=GUARD_RECORD_ID,
        execution_pass="draft",
        expected_sequence=156,
    )
    passed_review = recovery_call174._validate_attempt_common(
        reviews[0],
        record_id=GUARD_RECORD_ID,
        execution_pass="review",
        expected_sequence=157,
    )
    recovery_call174._validate_draft_attempt_payload(
        passed_draft, record_id=GUARD_RECORD_ID, spec=spec
    )
    recovery_call174._validate_review_attempt_payload(
        passed_review, record_id=GUARD_RECORD_ID, spec=spec
    )
    failed = _fixed_failed_attempt(record, record_id=GUARD_RECORD_ID, spec=spec)
    if (
        passed_draft.get("attempt") != 1
        or passed_draft.get("deterministic_pass") is not True
        or passed_draft.get("deterministic_error") is not None
        or passed_review.get("attempt") != 1
        or passed_review.get("review_mode") != "same_provider_separate_pass"
        or passed_review["review"].get("decision") != "PASS"
        or record.get("status") != "needs_draft"
        or record.get("feedback") != OLD_FEEDBACK_BY_ID[GUARD_RECORD_ID]
        or record.get("rewrites_used") != 1
        or record.get("duplicate_rewrites_used") != 1
        or record.get("accepted") is not None
        or record.get("current_draft") != passed_draft.get("draft")
        or len(drafts) != 2
        or len(reviews) != 1
        or failed.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
    ):
        raise Mix2KV4RecoveryError("call 178 guard record 계약이 다릅니다.")


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
        raise Mix2KV4RecoveryError(
            "일곱 번째 복구 전 pipeline state identity가 다릅니다."
        )
    specs = _load_fixed_specs(state)
    strength = records.get(STRENGTH_RECORD_ID)
    guard = records.get(GUARD_RECORD_ID)
    strength_spec = specs.get(STRENGTH_RECORD_ID)
    guard_spec = specs.get(GUARD_RECORD_ID)
    if not all(
        isinstance(value, Mapping)
        for value in (strength, guard, strength_spec, guard_spec)
    ):
        raise Mix2KV4RecoveryError("일곱 번째 복구 대상 record/spec이 없습니다.")
    _validate_strength_pre_record(strength, strength_spec)
    _validate_guard_pre_record(guard, guard_spec)


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 178 pre-state에서 두 행의 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [*_prior_events(), _recovery_event()]
    recovered["records"][STRENGTH_RECORD_ID]["status"] = "needs_draft"
    for record_id in AFFECTED_RECORD_IDS:
        recovered["records"][record_id]["feedback"] = NEW_FEEDBACK_BY_ID[record_id]
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    return [
        {
            "record_id": STRENGTH_RECORD_ID,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_FEEDBACK_BY_ID[STRENGTH_RECORD_ID],
                "after": NEW_FEEDBACK_BY_ID[STRENGTH_RECORD_ID],
            },
        },
        {
            "record_id": GUARD_RECORD_ID,
            "status": {"before": "needs_draft", "after": "needs_draft"},
            "feedback": {
                "before": OLD_FEEDBACK_BY_ID[GUARD_RECORD_ID],
                "after": NEW_FEEDBACK_BY_ID[GUARD_RECORD_ID],
            },
        },
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


def _call177_checkpoint(target: Path) -> dict[str, Any]:
    payload = base_recovery._read_regular_file(
        target / prior_recovery.AFTER_STATE_RELATIVE,
        "call 177 recovery after state",
    )
    checkpoint = base_recovery._decode_object(payload, "call 177 recovery after state")
    prior_recovery._validate_checkpoint_layout(checkpoint)
    return checkpoint


def _project_prior_recovery_state(
    target: Path, current_state: Mapping[str, Any]
) -> dict[str, Any]:
    checkpoint = _call177_checkpoint(target)
    checkpoint_records = checkpoint.get("records")
    current_records = current_state.get("records")
    if not isinstance(checkpoint_records, Mapping) or not isinstance(
        current_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 177 projection record가 없습니다.")
    saved = checkpoint_records.get(STRENGTH_RECORD_ID)
    if not isinstance(saved, Mapping):
        raise Mix2KV4RecoveryError("call 177 projection strength record가 없습니다.")
    projected = deepcopy(dict(current_state))
    projected["operator_recoveries"] = _prior_events()
    projected["records"][STRENGTH_RECORD_ID] = deepcopy(dict(saved))
    return projected


def _validate_prior_projection(
    target: Path, current_state: Mapping[str, Any]
) -> dict[str, Any]:
    projected = _project_prior_recovery_state(target, current_state)
    report = prior_recovery.validate_recovery_chain(
        target,
        projected,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("projection한 call 177 recovery chain이 없습니다.")
    audit_report = deepcopy(report)
    audit_report["all_recoveries_completed"] = False
    audit_report["audit_status"] = "partial_superseded"
    audit_report["superseded_by"] = RECOVERY_ID
    audit_report["projection_provider_calls"] = projected.get("provider_calls")
    return audit_report


def _later_attempts(
    current: Mapping[str, Any], checkpoint: Mapping[str, Any], *, record_id: str
) -> tuple[list[Any], list[Any]]:
    return recovery_call174._later_attempts(current, checkpoint, record_id=record_id)


def _validate_descendant_payloads(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
    spec: Mapping[str, Any],
) -> None:
    later_drafts, later_reviews = _later_attempts(
        current, checkpoint, record_id=record_id
    )
    for attempt in later_drafts:
        if not isinstance(attempt, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 178 후속 draft attempt가 없습니다: {record_id}"
            )
        recovery_call174._validate_draft_attempt_payload(
            attempt, record_id=record_id, spec=spec
        )
        answer = attempt["draft"].get("answer")
        if record_id == STRENGTH_RECORD_ID and (
            not isinstance(answer, str)
            or sentence_count(answer) != 3
            or len([line for line in answer.splitlines() if line.strip()]) != 3
        ):
            raise Mix2KV4RecoveryError(
                f"call 178 recovery 후속 draft 길이가 다릅니다: {record_id}"
            )
    for attempt in later_reviews:
        if not isinstance(attempt, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 178 후속 review attempt가 없습니다: {record_id}"
            )
        recovery_call174._validate_review_attempt_payload(
            attempt, record_id=record_id, spec=spec
        )


def _validate_retry_progress(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
    spec: Mapping[str, Any],
    provider_calls: int,
    require_completed: bool,
) -> None:
    _validate_descendant_payloads(current, checkpoint, record_id=record_id, spec=spec)
    later_drafts, later_reviews = _later_attempts(
        current, checkpoint, record_id=record_id
    )
    if not later_drafts:
        if later_reviews or current != checkpoint or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 178 recovery draft가 완료되지 않았습니다: {record_id}"
            )
        return

    saved_drafts = checkpoint["draft_attempts"]
    saved_reviews = checkpoint["review_attempts"]
    draft = recovery_call174._validate_pass_draft(
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
        if len(later_drafts) != 1 or current != expected or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 178 recovery review가 완료되지 않았습니다: {record_id}"
            )
        return

    review = recovery_call174._validate_pass_review(
        later_reviews[0],
        record_id=record_id,
        attempt_number=len(saved_reviews) + 1,
        after_sequence=int(draft["provider_call_sequence"]),
        provider_calls=provider_calls,
    )
    expected["review_attempts"].append(deepcopy(dict(review)))
    expected["status"] = "accepted"
    expected["accepted"] = recovery_call174._accepted_from_attempts(draft, review)
    recovery_call174._validate_duplicate_descendant(
        current,
        expected,
        record_id=record_id,
        provider_calls=provider_calls,
        base_duplicate_rewrites=1,
        require_completed=require_completed,
    )


def _validate_prior_descendants(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed: bool,
) -> None:
    checkpoint = _call177_checkpoint(target)
    current_records = current_state.get("records")
    checkpoint_records = checkpoint.get("records")
    provider_calls = current_state.get("provider_calls")
    if (
        not isinstance(current_records, Mapping)
        or not isinstance(checkpoint_records, Mapping)
        or not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
    ):
        raise Mix2KV4RecoveryError("call 177 descendant state가 없습니다.")
    for record_id in UNAFFECTED_CALL177_RECORD_IDS:
        current = current_records.get(record_id)
        saved = checkpoint_records.get(record_id)
        if not isinstance(current, Mapping) or not isinstance(saved, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 177 descendant record가 없습니다: {record_id}"
            )
        prior_recovery._validate_retry_progress(
            current,
            saved,
            record_id=record_id,
            provider_calls=provider_calls,
            require_completed=require_completed,
        )
    prior_recovery._validate_unaffected_prior_descendants(
        target,
        current_state,
        require_completed=require_completed,
    )


def _validate_checkpoint_layout(checkpoint: Mapping[str, Any]) -> None:
    records = checkpoint.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("call 178 checkpoint record가 없습니다.")
    strength = records.get(STRENGTH_RECORD_ID)
    guard = records.get(GUARD_RECORD_ID)
    if not isinstance(strength, Mapping) or not isinstance(guard, Mapping):
        raise Mix2KV4RecoveryError("call 178 checkpoint 대상 record가 없습니다.")
    if (
        strength.get("status") != "needs_draft"
        or strength.get("feedback") != NEW_FEEDBACK_BY_ID[STRENGTH_RECORD_ID]
        or guard.get("status") != "needs_draft"
        or guard.get("feedback") != NEW_FEEDBACK_BY_ID[GUARD_RECORD_ID]
    ):
        raise Mix2KV4RecoveryError("call 178 checkpoint 상태가 다릅니다.")


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """일곱 번째 sidecar와 두 행의 실제 retry provenance를 검증한다."""

    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 178 recovery before state"
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 178 recovery after state"
    )
    manifest_payload = base_recovery._read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 178 recovery manifest"
    )
    before_state = base_recovery._decode_object(
        before_payload, "call 178 recovery before state"
    )
    after_state = base_recovery._decode_object(
        after_payload, "call 178 recovery after state"
    )
    manifest = base_recovery._decode_object(
        manifest_payload, "call 178 recovery manifest"
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
        raise Mix2KV4RecoveryError("call 178 operator recovery bundle이 다릅니다.")

    _validate_checkpoint_layout(after_state)
    specs = _load_fixed_specs(current_state)
    current_records = current_state.get("records")
    checkpoint_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 178 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        current = current_records.get(record_id)
        saved = checkpoint_records.get(record_id)
        spec = specs.get(record_id)
        if not all(isinstance(value, Mapping) for value in (current, saved, spec)):
            raise Mix2KV4RecoveryError(
                f"call 178 recovery descendant 입력이 없습니다: {record_id}"
            )
        expected_spec_sha256 = sha256_bytes(canonical_json_bytes(spec))
        if (
            current.get("spec_sha256") != expected_spec_sha256
            or saved.get("spec_sha256") != expected_spec_sha256
        ):
            raise Mix2KV4RecoveryError(
                f"call 178 recovery spec SHA가 다릅니다: {record_id}"
            )
        _validate_retry_progress(
            current,
            saved,
            record_id=record_id,
            spec=spec,
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
    """call 177 audit projection과 실제 D6 FAIL/D7·R4 이력을 분리한다."""

    events = current_state.get("operator_recoveries")
    expected_events = [*_prior_events(), _recovery_event()]
    recovery_dir_exists = os.path.lexists(target / RECOVERY_DIR_RELATIVE)
    manifest_exists = os.path.lexists(target / RECOVERY_MANIFEST_RELATIVE)
    if events != expected_events:
        if not recovery_dir_exists and not manifest_exists:
            return prior_recovery.validate_recovery_chain(
                target,
                current_state,
                require_completed_provider_passes=require_completed_provider_passes,
            )
        raise Mix2KV4RecoveryError(
            "일곱 번째 recovery sidecar와 state event가 일치하지 않습니다."
        )
    if not recovery_dir_exists or not manifest_exists:
        raise Mix2KV4RecoveryError(
            "일곱 번째 recovery state event에 대응하는 sidecar가 없습니다."
        )

    seventh_report = validate_recovery_bundle(
        target,
        current_state,
        require_completed_provider_passes=require_completed_provider_passes,
    )
    prior_report = _validate_prior_projection(target, current_state)
    _validate_prior_descendants(
        target,
        current_state,
        require_completed=require_completed_provider_passes,
    )
    overflow_ids = recovery_call148._attempt_overflow_ids(current_state)
    if require_completed_provider_passes:
        if overflow_ids != EXPECTED_OVERFLOW_IDS:
            raise Mix2KV4RecoveryError(
                "완료된 일곱 번째 recovery attempt 예외 집합이 다릅니다."
            )
    elif not overflow_ids.issubset(EXPECTED_OVERFLOW_IDS):
        raise Mix2KV4RecoveryError(
            "진행 중 일곱 번째 recovery attempt 예외 집합이 다릅니다."
        )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": [*prior_report["recoveries"], seventh_report],
        "prior_recovery_projection": {
            "all_recoveries_completed": False,
            "audit_status": prior_report["audit_status"],
            "superseded_by": prior_report["superseded_by"],
            "projection_provider_calls": prior_report["projection_provider_calls"],
            "nested_prior_projection": prior_report.get("prior_recovery_projection"),
        },
    }


def _validate_incident_pre_state(
    target: Path, state: Mapping[str, Any], payload: bytes
) -> None:
    _validate_pre_state(state, payload)
    _validate_prior_projection(target, state)
    recovered = build_recovered_state(state, payload)
    _validate_checkpoint_layout(recovered)
    if recovery_call148._attempt_overflow_ids(state) != EXPECTED_OVERFLOW_IDS:
        raise Mix2KV4RecoveryError("call 178 pre-state attempt 예외 집합이 다릅니다.")


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
    if report is None or len(report.get("recoveries", [])) != 7:
        raise Mix2KV4RecoveryError(
            "live write 전 일곱 단계 recovery chain 검증에 실패했습니다."
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
        recovery_dir, "call 178 partial recovery provenance"
    )
    _write_or_verify_bundle_file(
        target / BEFORE_STATE_RELATIVE,
        state_payload,
        "call 178 recovery before state",
    )
    _write_or_verify_bundle_file(
        target / AFTER_STATE_RELATIVE,
        after_payload,
        "call 178 recovery after state",
    )
    _write_or_verify_bundle_file(
        target / RECOVERY_MANIFEST_RELATIVE,
        manifest_payload,
        "call 178 recovery manifest",
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
        "prepared call 178 recovery before state",
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE,
        "prepared call 178 recovery after state",
    )
    after_state = base_recovery._decode_object(
        after_payload, "prepared call 178 recovery after state"
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
        raise Mix2KV4RecoveryError("허용된 call 178 recovery target이 아닙니다.")
    teachers._reject_symlink_components(target, "call 178 recovery target")
    for forbidden in (
        target / "teacher_manifest.json",
        target / "accepted/training_candidates_2000.jsonl",
    ):
        if forbidden.exists():
            raise Mix2KV4RecoveryError(
                "이미 candidate가 생성된 target은 복구할 수 없습니다."
            )

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
                return _resume_partial_bundle(target, state_path, state_payload, state)
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
                raise Mix2KV4RecoveryError("call 178 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        _validate_incident_pre_state(target, state, state_payload)
        recovered = build_recovered_state(state, state_payload)
        after_payload = teachers._json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        teachers._ensure_private_directory(
            recovery_dir, "call 178 operator recovery provenance"
        )
        teachers._atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        teachers._atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        teachers._atomic_write(manifest_path, teachers._json_bytes(recovery_manifest))
        report = _validate_full_chain_before_live_write(target, recovered)
        teachers._atomic_write(state_path, after_payload)
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 178 checkpoint의 감사 가능한 복구"
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
