# mix2k_v4_teacher_recovery_call174.py - call 174 duplicate 후속 상태를 다섯 번째 감사 복구로 재개한다.

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
from scripts.data import mix2k_v4_teacher_recovery_call154 as prior_recovery
from scripts.data import mix2k_v4_teachers as teachers
from scripts.data.mix2k_v4_contracts import (
    Mix2KV4ContractError,
    sha256_bytes,
    sha256_file,
    validate_draft,
    validate_review,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

Mix2KV4RecoveryError = base_recovery.Mix2KV4RecoveryError

RECOVERY_ID = "operator-recovery-provider-call-174-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "d54270a3d8d0e8d1824d3f2296f0aaa098725d8241aedcf1f09747aa1ea3f0e4"
)
EXPECTED_RUNNER_SHA256 = (
    "77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835"
)
EXPECTED_CONTRACTS_SHA256 = (
    "bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a"
)
EXPECTED_PROVIDER_CALLS = 174
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"
SPEC_BUILD_PATH = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/"
    "specs/v1.0.1/build-da9014c5f24a"
)
DRAFT_ATTEMPT_FIELDS = frozenset(
    {
        "assigned_provider",
        "provider",
        "fallback_used",
        "execution_pass",
        "provider_call_sequence",
        "attempt",
        "provider_draft",
        "draft",
        "particle_normalized",
        "particle_normalizer_version",
        "layout_normalized",
        "layout_normalizer_version",
        "deterministic_pass",
        "deterministic_error",
    }
)
REVIEW_ATTEMPT_FIELDS = frozenset(
    {
        "assigned_provider",
        "provider",
        "review_mode",
        "fallback_used",
        "execution_pass",
        "provider_call_sequence",
        "attempt",
        "review",
    }
)
DRAFT_PAYLOAD_FIELDS = frozenset(
    {
        "record_id",
        "answer",
        "used_fact_paths",
        "used_fact_values",
        "soft_interpretation_used",
        "limitations",
        "self_check",
    }
)

STRENGTH_RECORD_ID = "m2v4_99b224076cafeba65e696b29"
RELATION_RECORD_IDS = (
    "m2v4_0f1fc1adf220e2bdf6e6fa85",
    "m2v4_7de74a3e13e9240d3f406fac",
)
AFFECTED_RECORD_IDS = (STRENGTH_RECORD_ID, *RELATION_RECORD_IDS)
DUPLICATE_REVIEW_RECORD_ID = "m2v4_04a03609c2525768fe53777e"

FIRST_RECOVERY_RECORD_IDS = (
    "m2v4_67ad3171b4f72afa5168ecc6",
    "m2v4_437e3b58e25808533390db66",
)
SECOND_RECOVERY_RECORD_IDS = (
    DUPLICATE_REVIEW_RECORD_ID,
    "m2v4_9a689b213b285e1bda18f24f",
    "m2v4_27ca49a80737af0a331ab558",
    STRENGTH_RECORD_ID,
)
THIRD_RECOVERY_RECORD_IDS = (
    "m2v4_17f19223c200ce872e34b2d8",
    "m2v4_91b95e86fca05f83012bc87f",
    "m2v4_b98b22d7d5bbb0186e289392",
)
FOURTH_RECOVERY_RECORD_IDS = (
    RELATION_RECORD_IDS[0],
    "m2v4_72b78ba78948ce9852dad13c",
    RELATION_RECORD_IDS[1],
    "m2v4_daeffba875b39af3062f99fb",
)
RECOVERY_PAIR_CONTRACTS = {
    FIRST_RECOVERY_RECORD_IDS[0]: (4, 62, 1, 63),
    FIRST_RECOVERY_RECORD_IDS[1]: (3, 62, 1, 63),
    SECOND_RECOVERY_RECORD_IDS[0]: (4, 150, 2, 151),
    SECOND_RECOVERY_RECORD_IDS[1]: (4, 150, 2, 151),
    SECOND_RECOVERY_RECORD_IDS[2]: (4, 150, 3, 151),
    SECOND_RECOVERY_RECORD_IDS[3]: (3, 150, 2, 151),
    THIRD_RECOVERY_RECORD_IDS[0]: (4, 150, 4, 151),
    THIRD_RECOVERY_RECORD_IDS[1]: (4, 150, 4, 151),
    THIRD_RECOVERY_RECORD_IDS[2]: (4, 150, 4, 151),
    FOURTH_RECOVERY_RECORD_IDS[0]: (4, 156, 2, 157),
    FOURTH_RECOVERY_RECORD_IDS[1]: (4, 156, 2, 157),
    FOURTH_RECOVERY_RECORD_IDS[2]: (4, 156, 2, 157),
    FOURTH_RECOVERY_RECORD_IDS[3]: (3, 156, 1, 157),
}
ALL_PRIOR_RECOVERY_RECORD_IDS = tuple(RECOVERY_PAIR_CONTRACTS)
DUPLICATE_RECORD_IDS = frozenset(
    {DUPLICATE_REVIEW_RECORD_ID, *AFFECTED_RECORD_IDS}
)
STATIC_PRIOR_RECORD_IDS = tuple(
    record_id
    for record_id in ALL_PRIOR_RECOVERY_RECORD_IDS
    if record_id not in DUPLICATE_RECORD_IDS
)
PRIOR_OVERFLOW_IDS = frozenset(prior_recovery.FINAL_OVERFLOW_IDS)
FINAL_OVERFLOW_IDS = PRIOR_OVERFLOW_IDS | {STRENGTH_RECORD_ID}
PRE_RETRY_OVERFLOW_IDS = PRIOR_OVERFLOW_IDS - set(RELATION_RECORD_IDS)

OLD_STRENGTH_FEEDBACK = (
    "teacher 구조 사실 claim 오류: "
    "unsupported_structural_claim:strength_pattern_yongshin"
)
SAFE_STRENGTH_LIMITATION = (
    "네 기둥이 있어도 두 판단을 제가 새로 만들지 않으며, 별도로 검증되어 입력된 "
    "판단만 설명할 수 있습니다."
)
STRENGTH_REWRITE_GUIDANCE = (
    "재생성 지시: 거절과 필수 입력·도구 제한은 유지하되 마지막 제한은 '"
    + SAFE_STRENGTH_LIMITATION
    + "'로 그대로 쓰세요. '검증된 신강약·용신 판정 결과'처럼 대상 명칭과 "
    "'판정 결과'를 붙여 새 구조 사실 claim처럼 쓰지 마세요. "
)
STRENGTH_DUPLICATE_ANSWER = (
    "지금 정보만으로는 용신과 신강약을 정할 수 없습니다.\n"
    "먼저 생년월일, 양력·음력 구분, 출생시각 또는 시간 미상, 출생 도시·국가를 "
    "알려 주세요.\n"
    "현재는 명식 계산 도구가 연결되어 있지 않아 검증된 계산기의 "
    "년주·월주·일주·시주가 필요하지만, 네 기둥이 있어도 두 판단을 제가 새로 만들지 "
    "않으며 별도로 검증되어 입력된 판단만 설명할 수 있습니다."
)
STRENGTH_DUPLICATE_GUIDANCE = (
    "전체 dataset 중복 교정 재시도입니다. 사실은 유지하되 첫 문장, 설명 순서와 문장 "
    "구조를 모두 바꿔 자연스럽게 다시 작성하고, 아래 답변의 문장 골격이나 구절을 "
    "그대로 재사용하지 마세요.\n"
    "[PREVIOUS ANSWER TO AVOID]\n"
    + STRENGTH_DUPLICATE_ANSWER
)
NEW_STRENGTH_FEEDBACK = (
    STRENGTH_REWRITE_GUIDANCE
    + STRENGTH_DUPLICATE_GUIDANCE
    + "\n이전 오류: "
    + OLD_STRENGTH_FEEDBACK
)

SAFE_RELATION_LIMITATION = (
    "이 자료가 있어도 그 관계를 제가 새로 계산하지 않으며, 별도로 검증되어 입력된 "
    "관계만 설명할 수 있습니다."
)
RELATION_REWRITE_GUIDANCE = (
    "재생성 지시: 다음 초안에서도 관계 제한 문장은 '"
    + SAFE_RELATION_LIMITATION
    + "'로 그대로 유지하세요. '검증된 합·충 관계 판정 결과'처럼 합·충과 "
    "'판정 결과'를 붙여 새 구조 사실 claim처럼 쓰지 마세요. 기존 duplicate 지시: "
)
DUPLICATE_FEEDBACK_SUFFIX = (
    " 답변 중복이 발견됐습니다. 사실은 유지하되 첫 문장, 설명 순서, 문장 구조와 "
    "예시를 모두 달리해 자연스럽게 다시 작성하세요. 아래 이전 답변의 문장 골격이나 "
    "구절을 그대로 재사용하지 마세요.\n"
    "[PREVIOUS ANSWER TO AVOID]\n"
    "현재는 원국과 오늘의 자료가 없어 둘 사이의 합·충을 계산하거나 단정할 수 "
    "없습니다.\n"
    "먼저 생년월일, 양력·음력 구분, 출생시각 또는 시간 미상, 출생 도시·국가가 "
    "필요하며, 음력이라면 윤달 여부도 필요합니다. 현재는 명식 계산 도구가 연결되어 "
    "있지 않으므로, 검증된 계산기가 만든 년주·월주·일주·시주도 함께 입력해 주세요.\n"
    + SAFE_RELATION_LIMITATION
)
OLD_RELATION_FEEDBACK_BY_ID = {
    RELATION_RECORD_IDS[0]: (
        "전체 dataset에서 exact/normalized" + DUPLICATE_FEEDBACK_SUFFIX
    ),
    RELATION_RECORD_IDS[1]: "전체 dataset에서 exact" + DUPLICATE_FEEDBACK_SUFFIX,
}
NEW_RELATION_FEEDBACK_BY_ID = {
    record_id: RELATION_REWRITE_GUIDANCE + feedback
    for record_id, feedback in OLD_RELATION_FEEDBACK_BY_ID.items()
}


def _prior_events() -> list[dict[str, Any]]:
    return [*prior_recovery._prior_events(), prior_recovery._recovery_event()]


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "duplicate_repair_relation_strength_false_positive",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


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
        raise Mix2KV4RecoveryError("다섯 번째 복구 전 pipeline state identity가 다릅니다.")

    strength = records.get(STRENGTH_RECORD_ID)
    if not isinstance(strength, Mapping):
        raise Mix2KV4RecoveryError("다섯 번째 복구 전 strength record가 없습니다.")
    strength_drafts = strength.get("draft_attempts")
    strength_reviews = strength.get("review_attempts")
    if (
        strength.get("status") != "failed"
        or strength.get("feedback") != OLD_STRENGTH_FEEDBACK
        or strength.get("rewrites_used") != 2
        or strength.get("duplicate_rewrites_used") != 1
        or strength.get("accepted") is not None
        or not isinstance(strength.get("current_draft"), Mapping)
        or not isinstance(strength_drafts, list)
        or len(strength_drafts) != 4
        or not isinstance(strength_reviews, list)
        or len(strength_reviews) != 2
    ):
        raise Mix2KV4RecoveryError(
            "다섯 번째 복구 전 strength record 계약이 다릅니다."
        )
    failed_duplicate = strength_drafts[-1]
    if (
        not isinstance(failed_duplicate, Mapping)
        or failed_duplicate.get("provider") != "codex"
        or failed_duplicate.get("provider_call_sequence") != EXPECTED_PROVIDER_CALLS
        or failed_duplicate.get("execution_pass") != "draft"
        or failed_duplicate.get("deterministic_pass") is not False
        or failed_duplicate.get("deterministic_error") != OLD_STRENGTH_FEEDBACK
    ):
        raise Mix2KV4RecoveryError(
            "다섯 번째 복구 전 strength duplicate attempt가 다릅니다."
        )

    for record_id in RELATION_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"다섯 번째 복구 전 relation record가 없습니다: {record_id}"
            )
        drafts = record.get("draft_attempts")
        reviews = record.get("review_attempts")
        if (
            record.get("status") != "needs_draft"
            or record.get("feedback") != OLD_RELATION_FEEDBACK_BY_ID[record_id]
            or record.get("rewrites_used") != 2
            or record.get("duplicate_rewrites_used") != 1
            or record.get("accepted") is not None
            or not isinstance(record.get("current_draft"), Mapping)
            or not isinstance(drafts, list)
            or len(drafts) != 4
            or not isinstance(reviews, list)
            or len(reviews) != 2
            or not isinstance(drafts[-1], Mapping)
            or drafts[-1].get("provider_call_sequence") != 156
            or drafts[-1].get("deterministic_pass") is not True
            or not isinstance(reviews[-1], Mapping)
            or reviews[-1].get("provider_call_sequence") != 157
            or reviews[-1].get("review", {}).get("decision") != "PASS"
            or record.get("current_draft") != drafts[-1].get("draft")
        ):
            raise Mix2KV4RecoveryError(
                f"다섯 번째 복구 전 relation record 계약이 다릅니다: {record_id}"
            )


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 174 pre-state에서 세 행의 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [*_prior_events(), _recovery_event()]
    strength = recovered["records"][STRENGTH_RECORD_ID]
    strength["status"] = "needs_draft"
    strength["feedback"] = NEW_STRENGTH_FEEDBACK
    for record_id in RELATION_RECORD_IDS:
        recovered["records"][record_id]["feedback"] = (
            NEW_RELATION_FEEDBACK_BY_ID[record_id]
        )
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    changes = [
        {
            "record_id": STRENGTH_RECORD_ID,
            "status": {"before": "failed", "after": "needs_draft"},
            "feedback": {
                "before": OLD_STRENGTH_FEEDBACK,
                "after": NEW_STRENGTH_FEEDBACK,
            },
        }
    ]
    changes.extend(
        {
            "record_id": record_id,
            "status": {"before": "needs_draft", "after": "needs_draft"},
            "feedback": {
                "before": OLD_RELATION_FEEDBACK_BY_ID[record_id],
                "after": NEW_RELATION_FEEDBACK_BY_ID[record_id],
            },
        }
        for record_id in RELATION_RECORD_IDS
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


def _attempt_lists(
    record: Mapping[str, Any], *, record_id: str
) -> tuple[list[Any], list[Any]]:
    drafts = record.get("draft_attempts")
    reviews = record.get("review_attempts")
    if not isinstance(drafts, list) or not isinstance(reviews, list):
        raise Mix2KV4RecoveryError(f"attempt 목록이 없습니다: {record_id}")
    return drafts, reviews


def _accepted_from_attempts(
    draft_attempt: Mapping[str, Any], review_attempt: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "assigned_drafter": draft_attempt["assigned_provider"],
        "assigned_reviewer": review_attempt["assigned_provider"],
        "draft_provider": draft_attempt["provider"],
        "review_provider": review_attempt["provider"],
        "review_mode": review_attempt["review_mode"],
        "fallback_used": (
            draft_attempt["fallback_used"] or review_attempt["fallback_used"]
        ),
        "draft": deepcopy(draft_attempt["draft"]),
        "review": deepcopy(review_attempt["review"]),
    }


def _validate_attempt_common(
    attempt: Any,
    *,
    record_id: str,
    execution_pass: str,
    expected_sequence: int | None = None,
    minimum_sequence: int | None = None,
    maximum_sequence: int | None = None,
) -> Mapping[str, Any]:
    if not isinstance(attempt, Mapping):
        raise Mix2KV4RecoveryError(f"attempt provenance가 없습니다: {record_id}")
    sequence = attempt.get("provider_call_sequence")
    if (
        attempt.get("provider") != "codex"
        or attempt.get("execution_pass") != execution_pass
        or not isinstance(attempt.get("assigned_provider"), str)
        or not isinstance(attempt.get("fallback_used"), bool)
        or not isinstance(sequence, int)
        or isinstance(sequence, bool)
        or (expected_sequence is not None and sequence != expected_sequence)
        or (minimum_sequence is not None and sequence <= minimum_sequence)
        or (maximum_sequence is not None and sequence > maximum_sequence)
    ):
        raise Mix2KV4RecoveryError(f"attempt provenance가 다릅니다: {record_id}")
    payload_name = "draft" if execution_pass == "draft" else "review"
    payload = attempt.get(payload_name)
    if (
        not isinstance(payload, Mapping)
        or not payload
        or payload.get("record_id") != record_id
    ):
        raise Mix2KV4RecoveryError(f"attempt payload가 다릅니다: {record_id}")
    return attempt


def _recovery_pair(
    record: Mapping[str, Any], *, record_id: str
) -> tuple[int, Mapping[str, Any], int, Mapping[str, Any]]:
    (
        draft_ordinal,
        draft_sequence,
        review_ordinal,
        review_sequence,
    ) = RECOVERY_PAIR_CONTRACTS[record_id]
    drafts, reviews = _attempt_lists(record, record_id=record_id)
    if len(drafts) < draft_ordinal or len(reviews) < review_ordinal:
        raise Mix2KV4RecoveryError(f"recovery pair prefix가 짧습니다: {record_id}")
    for index, attempt in enumerate(drafts[:draft_ordinal], start=1):
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("attempt") != index
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "draft"
            or not isinstance(attempt.get("draft"), Mapping)
            or attempt["draft"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(
                f"recovery draft prefix가 다릅니다: {record_id}"
            )
    for index, attempt in enumerate(reviews[:review_ordinal], start=1):
        if (
            not isinstance(attempt, Mapping)
            or attempt.get("attempt") != index
            or attempt.get("provider") != "codex"
            or attempt.get("execution_pass") != "review"
            or attempt.get("review_mode") != "same_provider_separate_pass"
            or not isinstance(attempt.get("review"), Mapping)
            or attempt["review"].get("record_id") != record_id
        ):
            raise Mix2KV4RecoveryError(
                f"recovery review prefix가 다릅니다: {record_id}"
            )
    draft_index = draft_ordinal - 1
    review_index = review_ordinal - 1
    raw_draft = drafts[draft_index]
    raw_review = reviews[review_index]
    draft = _validate_attempt_common(
        raw_draft,
        record_id=record_id,
        execution_pass="draft",
        expected_sequence=draft_sequence,
    )
    review = _validate_attempt_common(
        raw_review,
        record_id=record_id,
        execution_pass="review",
        expected_sequence=review_sequence,
    )
    if (
        draft.get("deterministic_pass") is not True
        or review.get("review_mode") != "same_provider_separate_pass"
        or review["review"].get("decision") != "PASS"
        or review_sequence <= draft_sequence
    ):
        raise Mix2KV4RecoveryError(f"recovery pair 결과가 다릅니다: {record_id}")
    return draft_index, draft, review_index, review


def _project_record_to_recovery_pair(
    record: Mapping[str, Any], *, record_id: str
) -> dict[str, Any]:
    draft_index, draft, review_index, review = _recovery_pair(
        record, record_id=record_id
    )
    drafts, reviews = _attempt_lists(record, record_id=record_id)
    projected = deepcopy(dict(record))
    projected["draft_attempts"] = deepcopy(drafts[: draft_index + 1])
    projected["review_attempts"] = deepcopy(reviews[: review_index + 1])
    projected["duplicate_rewrites_used"] = 0
    projected["status"] = "accepted"
    projected["feedback"] = ""
    projected["current_draft"] = deepcopy(draft["draft"])
    projected["accepted"] = _accepted_from_attempts(draft, review)
    return projected


def _project_prior_recovery_state(state: Mapping[str, Any]) -> dict[str, Any]:
    records = state.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("projection할 recovery record가 없습니다.")
    projected = deepcopy(dict(state))
    projected["operator_recoveries"] = _prior_events()
    for record_id in ALL_PRIOR_RECOVERY_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"projection할 recovery record가 없습니다: {record_id}"
            )
        projected["records"][record_id] = _project_record_to_recovery_pair(
            record, record_id=record_id
        )
    return projected


def _validate_prior_projection(target: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    projected = _project_prior_recovery_state(state)
    report = prior_recovery.validate_recovery_chain(
        target,
        projected,
        require_completed_provider_passes=True,
    )
    if report is None:
        raise Mix2KV4RecoveryError("projection한 이전 recovery chain이 없습니다.")
    return report


def _validate_duplicate_draft(
    attempt: Any,
    *,
    record_id: str,
    attempt_number: int,
    deterministic_pass: bool,
) -> Mapping[str, Any]:
    validated = _validate_attempt_common(
        attempt,
        record_id=record_id,
        execution_pass="draft",
        expected_sequence=EXPECTED_PROVIDER_CALLS,
    )
    provider_draft = validated.get("provider_draft")
    expected_error = None if deterministic_pass else OLD_STRENGTH_FEEDBACK
    if (
        validated.get("attempt") != attempt_number
        or validated.get("deterministic_pass") is not deterministic_pass
        or validated.get("deterministic_error") != expected_error
        or not isinstance(provider_draft, Mapping)
        or provider_draft.get("record_id") != record_id
    ):
        raise Mix2KV4RecoveryError(f"duplicate draft가 다릅니다: {record_id}")
    return validated


def _checkpoint_expected_record(
    record: Mapping[str, Any], *, record_id: str
) -> dict[str, Any]:
    baseline = _project_record_to_recovery_pair(record, record_id=record_id)
    baseline_drafts, baseline_reviews = _attempt_lists(
        baseline, record_id=record_id
    )
    drafts, reviews = _attempt_lists(record, record_id=record_id)
    extra_drafts = drafts[len(baseline_drafts) :]
    extra_reviews = reviews[len(baseline_reviews) :]

    if record_id in STATIC_PRIOR_RECORD_IDS:
        if extra_drafts or extra_reviews:
            raise Mix2KV4RecoveryError(
                f"dup=0 recovery 행에 추가 attempt가 있습니다: {record_id}"
            )
        return baseline

    expected = deepcopy(baseline)
    expected["duplicate_rewrites_used"] = 1
    expected["accepted"] = None
    if record_id == DUPLICATE_REVIEW_RECORD_ID:
        if len(extra_drafts) != 1 or extra_reviews:
            raise Mix2KV4RecoveryError("04a duplicate checkpoint attempt가 다릅니다.")
        duplicate_draft = _validate_duplicate_draft(
            extra_drafts[0],
            record_id=record_id,
            attempt_number=len(baseline_drafts) + 1,
            deterministic_pass=True,
        )
        expected["draft_attempts"].append(deepcopy(dict(duplicate_draft)))
        expected["status"] = "needs_review"
        expected["feedback"] = ""
        expected["current_draft"] = deepcopy(duplicate_draft["draft"])
        return expected

    if record_id == STRENGTH_RECORD_ID:
        if len(extra_drafts) != 1 or extra_reviews:
            raise Mix2KV4RecoveryError("99b duplicate checkpoint attempt가 다릅니다.")
        duplicate_draft = _validate_duplicate_draft(
            extra_drafts[0],
            record_id=record_id,
            attempt_number=len(baseline_drafts) + 1,
            deterministic_pass=False,
        )
        expected["draft_attempts"].append(deepcopy(dict(duplicate_draft)))
        expected["status"] = "needs_draft"
        expected["feedback"] = NEW_STRENGTH_FEEDBACK
        return expected

    if extra_drafts or extra_reviews or record_id not in RELATION_RECORD_IDS:
        raise Mix2KV4RecoveryError(
            f"relation duplicate checkpoint attempt가 다릅니다: {record_id}"
        )
    expected["status"] = "needs_draft"
    expected["feedback"] = NEW_RELATION_FEEDBACK_BY_ID[record_id]
    return expected


def _validate_checkpoint_layout(checkpoint: Mapping[str, Any]) -> None:
    records = checkpoint.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("call 174 checkpoint record가 없습니다.")
    for record_id in ALL_PRIOR_RECOVERY_RECORD_IDS:
        record = records.get(record_id)
        if not isinstance(record, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 174 checkpoint record가 없습니다: {record_id}"
            )
        expected = _checkpoint_expected_record(record, record_id=record_id)
        if record != expected:
            raise Mix2KV4RecoveryError(
                f"call 174 checkpoint duplicate 상태가 다릅니다: {record_id}"
            )


def _later_attempts(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
) -> tuple[list[Any], list[Any]]:
    current_drafts, current_reviews = _attempt_lists(current, record_id=record_id)
    saved_drafts, saved_reviews = _attempt_lists(checkpoint, record_id=record_id)
    if (
        set(current) != set(checkpoint)
        or current.get("spec_sha256") != checkpoint.get("spec_sha256")
        or current.get("rewrites_used") != checkpoint.get("rewrites_used")
        or current_drafts[: len(saved_drafts)] != saved_drafts
        or current_reviews[: len(saved_reviews)] != saved_reviews
    ):
        raise Mix2KV4RecoveryError(
            f"call 174 recovery 이후 record·attempt prefix가 다릅니다: {record_id}"
        )
    later_drafts = current_drafts[len(saved_drafts) :]
    later_reviews = current_reviews[len(saved_reviews) :]
    return later_drafts, later_reviews


def _load_fixed_specs(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        _, _, specs = teachers._validate_spec_build(
            SPEC_BUILD_PATH, teachers.DEFAULT_CONFIG.resolve()
        )
    except (OSError, ValueError, Mix2KV4ContractError, teachers.Mix2KV4TeacherError) as exc:
        raise Mix2KV4RecoveryError(
            "call 174 recovery의 고정 spec build를 검증하지 못했습니다."
        ) from exc
    manifest_path = SPEC_BUILD_PATH / "build_manifest.json"
    if (
        state.get("spec_manifest_sha256") != sha256_file(manifest_path)
        or len(specs) != 2000
    ):
        raise Mix2KV4RecoveryError(
            "call 174 recovery의 spec manifest identity가 다릅니다."
        )
    specs_by_id = {
        str(spec.get("id")): spec
        for spec in specs
        if isinstance(spec, dict) and isinstance(spec.get("id"), str)
    }
    if len(specs_by_id) != len(specs):
        raise Mix2KV4RecoveryError("call 174 recovery spec ID가 다릅니다.")
    return specs_by_id


def _validate_draft_attempt_payload(
    attempt: Mapping[str, Any],
    *,
    record_id: str,
    spec: Mapping[str, Any],
) -> None:
    provider_draft = attempt.get("provider_draft")
    draft = attempt.get("draft")
    assigned_provider = spec.get("drafter")
    if (
        set(attempt) != DRAFT_ATTEMPT_FIELDS
        or attempt.get("assigned_provider") != assigned_provider
        or attempt.get("fallback_used") is not (
            attempt.get("provider") != assigned_provider
        )
        or not isinstance(provider_draft, Mapping)
        or set(provider_draft) != DRAFT_PAYLOAD_FIELDS
        or provider_draft.get("record_id") != record_id
        or not isinstance(draft, Mapping)
        or set(draft) != DRAFT_PAYLOAD_FIELDS
    ):
        raise Mix2KV4RecoveryError(
            f"descendant draft provenance가 다릅니다: {record_id}"
        )

    candidate = deepcopy(dict(provider_draft))
    if not all(
        isinstance(candidate.get(field), list)
        and all(isinstance(value, str) for value in candidate[field])
        for field in ("used_fact_paths", "used_fact_values")
    ):
        raise Mix2KV4RecoveryError(
            f"descendant provider draft schema가 다릅니다: {record_id}"
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
        raise Mix2KV4RecoveryError(
            f"descendant draft payload가 다릅니다: {record_id}"
        ) from exc
    if (
        dict(draft) != candidate
        or attempt.get("particle_normalized") is not particle_normalized
        or attempt.get("particle_normalizer_version")
        != (
            teachers.PARTICLE_NORMALIZER_VERSION
            if particle_normalized
            else None
        )
        or attempt.get("layout_normalized") is not layout_normalized
        or attempt.get("layout_normalizer_version")
        != (
            teachers.LAYOUT_NORMALIZER_VERSION
            if layout_normalized
            else None
        )
    ):
        raise Mix2KV4RecoveryError(
            f"descendant draft normalization이 다릅니다: {record_id}"
        )


def _validate_review_attempt_payload(
    attempt: Mapping[str, Any],
    *,
    record_id: str,
    spec: Mapping[str, Any],
) -> None:
    review = attempt.get("review")
    assigned_provider = spec.get("reviewer")
    if (
        set(attempt) != REVIEW_ATTEMPT_FIELDS
        or attempt.get("assigned_provider") != assigned_provider
        or attempt.get("fallback_used") is not (
            attempt.get("provider") != assigned_provider
        )
        or not isinstance(review, Mapping)
    ):
        raise Mix2KV4RecoveryError(
            f"descendant review provenance가 다릅니다: {record_id}"
        )
    try:
        validate_review(spec, review)
    except Mix2KV4ContractError as exc:
        raise Mix2KV4RecoveryError(
            f"descendant review payload가 다릅니다: {record_id}"
        ) from exc


def _validate_descendant_payload_contracts(
    state: Mapping[str, Any], checkpoint: Mapping[str, Any]
) -> None:
    records = state.get("records")
    checkpoint_records = checkpoint.get("records")
    if not isinstance(records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("descendant payload record가 없습니다.")
    specs_by_id = _load_fixed_specs(state)
    for record_id in ALL_PRIOR_RECOVERY_RECORD_IDS:
        current = records.get(record_id)
        saved = checkpoint_records.get(record_id)
        spec = specs_by_id.get(record_id)
        if (
            not isinstance(current, Mapping)
            or not isinstance(saved, Mapping)
            or not isinstance(spec, Mapping)
        ):
            raise Mix2KV4RecoveryError(
                f"descendant payload 검증 입력이 없습니다: {record_id}"
            )
        spec_sha256 = sha256_bytes(canonical_json_bytes(spec))
        if (
            current.get("spec_sha256") != spec_sha256
            or saved.get("spec_sha256") != spec_sha256
        ):
            raise Mix2KV4RecoveryError(
                f"descendant payload spec SHA가 다릅니다: {record_id}"
            )
        later_drafts, later_reviews = _later_attempts(
            current, saved, record_id=record_id
        )
        for attempt in later_drafts:
            if not isinstance(attempt, Mapping):
                raise Mix2KV4RecoveryError(
                    f"descendant draft attempt가 없습니다: {record_id}"
                )
            _validate_draft_attempt_payload(
                attempt, record_id=record_id, spec=spec
            )
        for attempt in later_reviews:
            if not isinstance(attempt, Mapping):
                raise Mix2KV4RecoveryError(
                    f"descendant review attempt가 없습니다: {record_id}"
                )
            _validate_review_attempt_payload(
                attempt, record_id=record_id, spec=spec
            )


def _validate_pass_draft(
    attempt: Any,
    *,
    record_id: str,
    attempt_number: int,
    after_sequence: int,
    provider_calls: int,
) -> Mapping[str, Any]:
    draft = _validate_attempt_common(
        attempt,
        record_id=record_id,
        execution_pass="draft",
        minimum_sequence=after_sequence,
        maximum_sequence=provider_calls,
    )
    provider_draft = draft.get("provider_draft")
    if (
        draft.get("attempt") != attempt_number
        or draft.get("deterministic_pass") is not True
        or draft.get("deterministic_error") is not None
        or not isinstance(provider_draft, Mapping)
        or provider_draft.get("record_id") != record_id
    ):
        raise Mix2KV4RecoveryError(
            f"duplicate/recovery retry draft가 다릅니다: {record_id}"
        )
    return draft


def _validate_pass_review(
    attempt: Any,
    *,
    record_id: str,
    attempt_number: int,
    after_sequence: int,
    provider_calls: int,
) -> Mapping[str, Any]:
    review = _validate_attempt_common(
        attempt,
        record_id=record_id,
        execution_pass="review",
        minimum_sequence=after_sequence,
        maximum_sequence=provider_calls,
    )
    if (
        review.get("attempt") != attempt_number
        or review.get("review_mode") != "same_provider_separate_pass"
        or review["review"].get("decision") != "PASS"
    ):
        raise Mix2KV4RecoveryError(
            f"duplicate/recovery retry review가 다릅니다: {record_id}"
        )
    return review


def _duplicate_feedback_options(previous_answer: str) -> set[str]:
    suffix = (
        " 답변 중복이 발견됐습니다. 사실은 유지하되 첫 문장, 설명 순서, "
        "문장 구조와 예시를 모두 달리해 자연스럽게 다시 작성하세요. "
        "아래 이전 답변의 문장 골격이나 구절을 그대로 재사용하지 마세요.\n"
        "[PREVIOUS ANSWER TO AVOID]\n"
        + previous_answer.strip()
    )
    return {
        "전체 dataset에서 " + reason + suffix
        for reason in ("exact", "normalized", "exact/normalized")
    }


def _validate_duplicate_descendant(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    record_id: str,
    provider_calls: int,
    base_duplicate_rewrites: int,
    require_completed: bool,
) -> None:
    later_drafts, later_reviews = _later_attempts(
        current, checkpoint, record_id=record_id
    )
    saved_drafts, saved_reviews = _attempt_lists(checkpoint, record_id=record_id)
    duplicate_rewrites = current.get("duplicate_rewrites_used")
    if (
        not isinstance(duplicate_rewrites, int)
        or isinstance(duplicate_rewrites, bool)
        or duplicate_rewrites < base_duplicate_rewrites
        or duplicate_rewrites > teachers.MAXIMUM_DUPLICATE_REWRITE_ROUNDS
    ):
        raise Mix2KV4RecoveryError(
            f"duplicate rewrite counter가 다릅니다: {record_id}"
        )
    rounds = duplicate_rewrites - base_duplicate_rewrites
    if rounds == 0:
        if later_drafts or later_reviews or current != checkpoint:
            raise Mix2KV4RecoveryError(
                f"duplicate round 없이 record가 변경됐습니다: {record_id}"
            )
        return

    if (
        len(later_drafts) > rounds
        or len(later_reviews) > rounds
        or len(later_reviews) > len(later_drafts)
        or len(later_drafts) - len(later_reviews) > 1
    ):
        raise Mix2KV4RecoveryError(
            f"duplicate round attempt 수가 다릅니다: {record_id}"
        )

    previous_sequence = max(
        int(saved_drafts[-1]["provider_call_sequence"]),
        int(saved_reviews[-1]["provider_call_sequence"]),
    )
    validated_drafts: list[Mapping[str, Any]] = []
    validated_reviews: list[Mapping[str, Any]] = []
    for index, raw_draft in enumerate(later_drafts):
        if index > len(validated_reviews):
            raise Mix2KV4RecoveryError(
                f"이전 duplicate review 전에 다음 draft가 추가됐습니다: {record_id}"
            )
        draft = _validate_pass_draft(
            raw_draft,
            record_id=record_id,
            attempt_number=len(saved_drafts) + index + 1,
            after_sequence=previous_sequence,
            provider_calls=provider_calls,
        )
        validated_drafts.append(draft)
        if index < len(later_reviews):
            review = _validate_pass_review(
                later_reviews[index],
                record_id=record_id,
                attempt_number=len(saved_reviews) + index + 1,
                after_sequence=int(draft["provider_call_sequence"]),
                provider_calls=provider_calls,
            )
            validated_reviews.append(review)
            previous_sequence = int(review["provider_call_sequence"])

    expected = deepcopy(dict(checkpoint))
    expected["duplicate_rewrites_used"] = duplicate_rewrites
    expected["draft_attempts"].extend(deepcopy([dict(x) for x in validated_drafts]))
    expected["review_attempts"].extend(
        deepcopy([dict(x) for x in validated_reviews])
    )
    if validated_drafts:
        expected["current_draft"] = deepcopy(validated_drafts[-1]["draft"])

    if len(later_drafts) == rounds and len(later_reviews) == rounds:
        draft = validated_drafts[-1]
        review = validated_reviews[-1]
        expected["status"] = "accepted"
        expected["feedback"] = ""
        expected["accepted"] = _accepted_from_attempts(draft, review)
    elif len(later_drafts) == rounds and len(later_reviews) == rounds - 1:
        expected["status"] = "needs_review"
        expected["feedback"] = ""
        expected["accepted"] = None
    elif (
        len(later_drafts) == rounds - 1
        and len(later_reviews) == rounds - 1
    ):
        previous_draft = (
            validated_drafts[-1] if validated_drafts else saved_drafts[-1]
        )
        previous_answer = previous_draft["draft"].get("answer")
        if (
            not isinstance(previous_answer, str)
            or current.get("feedback")
            not in _duplicate_feedback_options(previous_answer)
        ):
            raise Mix2KV4RecoveryError(
                f"duplicate 재작성 feedback이 다릅니다: {record_id}"
            )
        expected["status"] = "needs_draft"
        expected["feedback"] = current["feedback"]
        expected["accepted"] = None
    else:
        raise Mix2KV4RecoveryError(
            f"duplicate round 상태 전이가 다릅니다: {record_id}"
        )
    if require_completed and expected["status"] != "accepted":
        raise Mix2KV4RecoveryError(
            f"duplicate round가 완료되지 않았습니다: {record_id}"
        )
    if current != expected:
        raise Mix2KV4RecoveryError(
            f"duplicate descendant record가 다릅니다: {record_id}"
        )


def _validate_fifth_progress(
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
    if not later_drafts:
        if later_reviews or current != checkpoint or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 174 recovery draft가 완료되지 않았습니다: {record_id}"
            )
        return

    saved_drafts, saved_reviews = _attempt_lists(checkpoint, record_id=record_id)
    draft = _validate_pass_draft(
        later_drafts[0],
        record_id=record_id,
        attempt_number=len(saved_drafts) + 1,
        after_sequence=EXPECTED_PROVIDER_CALLS,
        provider_calls=provider_calls,
    )
    accepted_checkpoint = deepcopy(dict(checkpoint))
    accepted_checkpoint["draft_attempts"].append(deepcopy(dict(draft)))
    accepted_checkpoint["status"] = "needs_review"
    accepted_checkpoint["feedback"] = ""
    accepted_checkpoint["current_draft"] = deepcopy(draft["draft"])
    accepted_checkpoint["accepted"] = None
    if not later_reviews:
        if len(later_drafts) != 1 or current != accepted_checkpoint or require_completed:
            raise Mix2KV4RecoveryError(
                f"call 174 recovery review가 완료되지 않았습니다: {record_id}"
            )
        return

    review = _validate_pass_review(
        later_reviews[0],
        record_id=record_id,
        attempt_number=len(saved_reviews) + 1,
        after_sequence=int(draft["provider_call_sequence"]),
        provider_calls=provider_calls,
    )
    accepted_checkpoint["review_attempts"].append(deepcopy(dict(review)))
    accepted_checkpoint["status"] = "accepted"
    accepted_checkpoint["accepted"] = _accepted_from_attempts(draft, review)
    _validate_duplicate_descendant(
        current,
        accepted_checkpoint,
        record_id=record_id,
        provider_calls=provider_calls,
        base_duplicate_rewrites=1,
        require_completed=require_completed,
    )


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """다섯 번째 sidecar와 필수 retry pair·후속 duplicate를 검증한다."""

    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 174 recovery before state"
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 174 recovery after state"
    )
    manifest_payload = base_recovery._read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 174 recovery manifest"
    )
    before_state = base_recovery._decode_object(
        before_payload, "call 174 recovery before state"
    )
    after_state = base_recovery._decode_object(
        after_payload, "call 174 recovery after state"
    )
    manifest = base_recovery._decode_object(
        manifest_payload, "call 174 recovery manifest"
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
        raise Mix2KV4RecoveryError("call 174 operator recovery bundle이 다릅니다.")

    _validate_checkpoint_layout(after_state)
    current_records = current_state.get("records")
    checkpoint_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 174 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        current = current_records.get(record_id)
        checkpoint = checkpoint_records.get(record_id)
        if not isinstance(current, Mapping) or not isinstance(checkpoint, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 174 recovery descendant record가 없습니다: {record_id}"
            )
        _validate_fifth_progress(
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


def _validate_duplicate_review_progress(
    current: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    provider_calls: int,
    require_completed: bool,
) -> None:
    record_id = DUPLICATE_REVIEW_RECORD_ID
    later_drafts, later_reviews = _later_attempts(
        current, checkpoint, record_id=record_id
    )
    saved_drafts, saved_reviews = _attempt_lists(checkpoint, record_id=record_id)
    if not later_reviews:
        if later_drafts or current != checkpoint or require_completed:
            raise Mix2KV4RecoveryError("04a duplicate review가 완료되지 않았습니다.")
        return

    duplicate_draft = saved_drafts[-1]
    review = _validate_pass_review(
        later_reviews[0],
        record_id=record_id,
        attempt_number=len(saved_reviews) + 1,
        after_sequence=int(duplicate_draft["provider_call_sequence"]),
        provider_calls=provider_calls,
    )
    accepted_checkpoint = deepcopy(dict(checkpoint))
    accepted_checkpoint["review_attempts"].append(deepcopy(dict(review)))
    accepted_checkpoint["status"] = "accepted"
    accepted_checkpoint["accepted"] = _accepted_from_attempts(
        duplicate_draft, review
    )
    _validate_duplicate_descendant(
        current,
        accepted_checkpoint,
        record_id=record_id,
        provider_calls=provider_calls,
        base_duplicate_rewrites=1,
        require_completed=require_completed,
    )


def _validate_current_descendant(
    current_state: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    *,
    require_completed: bool,
) -> None:
    current_records = current_state.get("records")
    checkpoint_records = checkpoint.get("records")
    provider_calls = current_state.get("provider_calls")
    if (
        not isinstance(current_records, Mapping)
        or not isinstance(checkpoint_records, Mapping)
        or not isinstance(provider_calls, int)
        or isinstance(provider_calls, bool)
    ):
        raise Mix2KV4RecoveryError("duplicate descendant state가 없습니다.")
    for record_id in STATIC_PRIOR_RECORD_IDS:
        current = current_records.get(record_id)
        saved = checkpoint_records.get(record_id)
        if not isinstance(current, Mapping) or not isinstance(saved, Mapping):
            raise Mix2KV4RecoveryError(
                f"dup=0 recovery 행이 없습니다: {record_id}"
            )
        _validate_duplicate_descendant(
            current,
            saved,
            record_id=record_id,
            provider_calls=provider_calls,
            base_duplicate_rewrites=0,
            require_completed=require_completed,
        )
    current_duplicate_review = current_records.get(DUPLICATE_REVIEW_RECORD_ID)
    saved_duplicate_review = checkpoint_records.get(DUPLICATE_REVIEW_RECORD_ID)
    if not isinstance(current_duplicate_review, Mapping) or not isinstance(
        saved_duplicate_review, Mapping
    ):
        raise Mix2KV4RecoveryError("04a duplicate descendant record가 없습니다.")
    _validate_duplicate_review_progress(
        current_duplicate_review,
        saved_duplicate_review,
        provider_calls=provider_calls,
        require_completed=require_completed,
    )


def validate_recovery_chain(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any] | None:
    """이전 PASS projection과 실제 duplicate descendant를 분리해 검증한다."""

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
            "다섯 번째 recovery sidecar와 state event가 일치하지 않습니다."
        )
    if not recovery_dir_exists or not manifest_exists:
        raise Mix2KV4RecoveryError(
            "다섯 번째 recovery state event에 대응하는 sidecar가 없습니다."
        )

    fifth_report = validate_recovery_bundle(
        target,
        current_state,
        require_completed_provider_passes=require_completed_provider_passes,
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 174 recovery after state"
    )
    checkpoint = base_recovery._decode_object(
        after_payload, "call 174 recovery after state"
    )
    _validate_descendant_payload_contracts(current_state, checkpoint)
    _validate_current_descendant(
        current_state,
        checkpoint,
        require_completed=require_completed_provider_passes,
    )
    prior_report = _validate_prior_projection(target, current_state)
    overflow_ids = recovery_call148._attempt_overflow_ids(current_state)
    if require_completed_provider_passes:
        if overflow_ids != FINAL_OVERFLOW_IDS:
            raise Mix2KV4RecoveryError(
                "완료된 다섯 번째 recovery attempt 예외 집합이 다릅니다."
            )
    elif not overflow_ids.issubset(FINAL_OVERFLOW_IDS):
        raise Mix2KV4RecoveryError(
            "진행 중 다섯 번째 recovery attempt 예외 집합이 다릅니다."
        )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": [*prior_report["recoveries"], fifth_report],
    }


def _validate_incident_pre_state(
    target: Path, state: Mapping[str, Any], payload: bytes
) -> None:
    _validate_pre_state(state, payload)
    _validate_prior_projection(target, state)
    recovered = build_recovered_state(state, payload)
    _validate_checkpoint_layout(recovered)
    if recovery_call148._attempt_overflow_ids(state) != PRE_RETRY_OVERFLOW_IDS:
        raise Mix2KV4RecoveryError("call 174 pre-state attempt 예외 집합이 다릅니다.")


def _write_or_verify_bundle_file(path: Path, payload: bytes, label: str) -> None:
    if os.path.lexists(path):
        if base_recovery._read_regular_file(path, label) != payload:
            raise Mix2KV4RecoveryError(f"불완전한 {label} 내용이 다릅니다.")
        return
    teachers._atomic_write(path, payload)


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
        recovery_dir, "call 174 partial recovery provenance"
    )
    _write_or_verify_bundle_file(
        target / BEFORE_STATE_RELATIVE,
        state_payload,
        "call 174 recovery before state",
    )
    _write_or_verify_bundle_file(
        target / AFTER_STATE_RELATIVE,
        after_payload,
        "call 174 recovery after state",
    )
    _write_or_verify_bundle_file(
        target / RECOVERY_MANIFEST_RELATIVE,
        manifest_payload,
        "call 174 recovery manifest",
    )
    report = validate_recovery_chain(
        target,
        recovered,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("부분 call 174 recovery 검증 결과가 없습니다.")
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
        "prepared call 174 recovery before state",
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE,
        "prepared call 174 recovery after state",
    )
    after_state = base_recovery._decode_object(
        after_payload, "prepared call 174 recovery after state"
    )
    if before_payload != state_payload:
        raise Mix2KV4RecoveryError(
            "prepared recovery의 before snapshot이 live state와 다릅니다."
        )
    report = validate_recovery_chain(
        target,
        after_state,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("prepared call 174 recovery 검증 결과가 없습니다.")
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
        raise Mix2KV4RecoveryError("허용된 call 174 recovery target이 아닙니다.")
    teachers._reject_symlink_components(target, "call 174 recovery target")
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
                raise Mix2KV4RecoveryError("call 174 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        _validate_incident_pre_state(target, state, state_payload)
        recovered = build_recovered_state(state, state_payload)
        after_payload = teachers._json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        teachers._ensure_private_directory(
            recovery_dir, "call 174 operator recovery provenance"
        )
        teachers._atomic_write(target / BEFORE_STATE_RELATIVE, state_payload)
        teachers._atomic_write(target / AFTER_STATE_RELATIVE, after_payload)
        teachers._atomic_write(
            manifest_path, teachers._json_bytes(recovery_manifest)
        )
        report = validate_recovery_chain(
            target,
            recovered,
            require_completed_provider_passes=False,
        )
        if report is None:
            raise Mix2KV4RecoveryError("call 174 recovery 검증 결과가 없습니다.")
        teachers._atomic_write(state_path, after_payload)
        report["already_applied"] = False
        report["resumed_prepared_bundle"] = False
        return report
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 provider call 174 checkpoint의 감사 가능한 복구"
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
