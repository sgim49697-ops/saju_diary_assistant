# mix2k_v4_teacher_recovery_call181.py - call 181 검수 FAIL 4건을 여덟 번째 감사 복구로 재개한다.

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
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
from scripts.data import mix2k_v4_teacher_recovery_call178 as prior_recovery
from scripts.data import mix2k_v4_teachers as teachers
from scripts.data.mix2k_v4_contracts import sentence_count, sha256_bytes, sha256_file
from scripts.runtime.calculation.canonical import canonical_json_bytes

Mix2KV4RecoveryError = base_recovery.Mix2KV4RecoveryError

RECOVERY_ID = "operator-recovery-provider-call-181-v1"
TARGET_NAME = "full-build-da9014c5f24a-6e5149a5-117d55cb"
EXPECTED_PRE_STATE_SHA256 = (
    "d8fd11472dbefb8deea0bfbd424b62733c7d11ad09e469a53ca3f688f2d4f047"
)
EXPECTED_RUNNER_SHA256 = prior_recovery.EXPECTED_RUNNER_SHA256
EXPECTED_CONTRACTS_SHA256 = prior_recovery.EXPECTED_CONTRACTS_SHA256
EXPECTED_PROVIDER_CALLS = 181
RECOVERY_DIR_RELATIVE = Path("provenance") / RECOVERY_ID
RECOVERY_MANIFEST_RELATIVE = RECOVERY_DIR_RELATIVE / "recovery_manifest.json"
BEFORE_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.before.json"
AFTER_STATE_RELATIVE = RECOVERY_DIR_RELATIVE / "pipeline_state.after.json"

STRENGTH_RECORD_ID = prior_recovery.STRENGTH_RECORD_ID
GUARD_RECORD_ID = prior_recovery.GUARD_RECORD_ID
FIELD_RECORD_ID = "m2v4_953e7b897cb8336d7ecf704f"
GOLD_RECORD_ID = "m2v4_5fd86b954ae761e3a9a41be3"
AFFECTED_RECORD_IDS = (
    STRENGTH_RECORD_ID,
    GUARD_RECORD_ID,
    FIELD_RECORD_ID,
    GOLD_RECORD_ID,
)
PRIOR_PROJECTION_RECORD_IDS = (
    STRENGTH_RECORD_ID,
    GUARD_RECORD_ID,
)
EXPECTED_OVERFLOW_IDS = frozenset(prior_recovery.EXPECTED_OVERFLOW_IDS)

EXPECTED_FAILURE_CODE_BY_ID = {
    STRENGTH_RECORD_ID: "MISSING_REQUIRED_INTAKE",
    GUARD_RECORD_ID: "MISSING_EXPLICIT_FIELD_LABEL",
    FIELD_RECORD_ID: "MISSING_EXPLICIT_FIELD_LABEL",
    GOLD_RECORD_ID: "MISSING_EXPLICIT_FIELD_LABEL",
}
EXPECTED_DRAFT_COUNT_BY_ID = {
    STRENGTH_RECORD_ID: 7,
    GUARD_RECORD_ID: 3,
    FIELD_RECORD_ID: 2,
    GOLD_RECORD_ID: 2,
}
EXPECTED_REVIEW_COUNT_BY_ID = {
    STRENGTH_RECORD_ID: 4,
    GUARD_RECORD_ID: 2,
    FIELD_RECORD_ID: 2,
    GOLD_RECORD_ID: 2,
}
EXPECTED_REWRITES_BY_ID = {
    STRENGTH_RECORD_ID: 2,
    GUARD_RECORD_ID: 2,
    FIELD_RECORD_ID: 1,
    GOLD_RECORD_ID: 1,
}
OLD_FEEDBACK_BY_ID = {
    STRENGTH_RECORD_ID: (
        '{"fact_errors":["구조화 명식이 없는 상황에서 production system이 요구하는 '
        '필수 입력 요청을 하지 않았습니다."],"failure_codes":["MISSING_REQUIRED_INTAKE"],'
        '"rewrite_instructions":"현재 정보로 용신과 신강약을 정할 수 없다고 밝힌 뒤, '
        "아직 제공되지 않은 생년월일, 양력·음력 구분, 출생시각 또는 시간 미상 여부, "
        "출생 도시·국가를 짧고 구체적으로 요청하세요. 최소 3개의 비어 있지 않은 줄과 "
        "3문장을 유지하고, 추가 원국이나 간지를 받으면 용신·신강약을 계산할 수 있다고 "
        '암시하지 마세요.","style_notes":[]}'
    ),
    GUARD_RECORD_ID: (
        'Independent same-provider review FAIL: {"fact_errors":["질문의 대상인 '
        "‘신강약’을 답변에서 명시하지 않고 ‘해당 판단’으로만 지칭해 field grounding이 "
        '불충분합니다."],"failure_codes":["MISSING_EXPLICIT_FIELD_LABEL"],'
        '"rewrite_instructions":"‘표면 오행 개수만으로 신강약을 바로 정할 수 없습니다’처럼 '
        "질문의 field label인 ‘신강약’을 명시하세요. 제공된 schema 규칙만 사용하고, "
        "검증된 신강약 결과가 별도로 제공된 경우에만 설명할 수 있다는 범위로 "
        '한정하세요.","style_notes":[]}'
    ),
    FIELD_RECORD_ID: (
        'Independent same-provider review FAIL: {"fact_errors":["질문의 대상인 '
        "‘신강약’을 답변에서 명시하지 않고 ‘해당 판단’으로만 지칭해 field grounding이 "
        '불충분합니다."],"failure_codes":["MISSING_EXPLICIT_FIELD_LABEL"],'
        '"rewrite_instructions":"‘표면에 드러난 오행의 개수만으로 신강약을 바로 정할 수 '
        "없습니다’처럼 ‘신강약’을 직접 명시하세요. 신강약을 새로 도출하지 않는다는 "
        '제공된 schema 규칙 안에서만 답하세요.","style_notes":[]}'
    ),
    GOLD_RECORD_ID: (
        'Independent same-provider review FAIL: {"fact_errors":["schema의 핵심 label인 '
        "‘Gold’와 ‘현재 허용 evidence’를 답변에서 명시하지 않아 정확한 field·label "
        'grounding 요건을 충족하지 못했습니다."],"failure_codes":'
        '["MISSING_EXPLICIT_FIELD_LABEL"],"rewrite_instructions":"‘K0의 자연스러운 설명에 '
        "새로 나온 간지를 곧바로 Gold로 사용하면 안 됩니다. 사주 사실은 현재 허용 "
        "evidence와 일치하는 값만 Gold로 사용할 수 있습니다.’처럼 ‘K0’, ‘Gold’, ‘현재 "
        '허용 evidence’를 명시하세요.","style_notes":[]}'
    ),
}
NEW_FEEDBACK_BY_ID = {
    STRENGTH_RECORD_ID: (
        "재생성 지시: 아래 검증 완료 문구의 사실·입력 항목·문장 구조를 빠뜨리지 말고 "
        "정확히 3개 완결 문장·3개 비어 있지 않은 줄로 작성하세요. `현재 정보만으로는 "
        "용신과 신강약을 정할 수 없습니다.` / `확인을 위해 생년월일, 양력·음력 구분, "
        "출생시각 또는 시간 미상, 출생 도시·국가를 알려주시고, 음력이라면 윤달 여부도 "
        "적어 주세요.` / `현재 승인된 명식 계산 도구가 연결되어 있지 않아 검증된 "
        "계산기의 네 기둥이 필요하지만, 그 자료만으로 두 판단을 새로 만들지 않고 "
        "별도로 검증되어 입력된 판단만 설명할 수 있습니다.` 이전 review: "
        + OLD_FEEDBACK_BY_ID[STRENGTH_RECORD_ID]
    ),
    GUARD_RECORD_ID: (
        "재생성 지시: 질문의 field label `신강약`을 한 번 직접 명시하세요. pinned "
        "validator로 검증한 안전한 목표 문구는 `표면 오행 개수만으로는 신강약을 바로 "
        "정할 수 없으며, 별도로 검증되어 입력된 결과만 설명할 수 있습니다.`입니다. "
        "이전 review: " + OLD_FEEDBACK_BY_ID[GUARD_RECORD_ID]
    ),
    FIELD_RECORD_ID: (
        "재생성 지시: 질문의 field label `신강약`을 직접 명시하세요. pinned validator로 "
        "검증한 안전한 목표 문구는 `표면에 드러난 오행의 수만으로 신강약을 바로 정할 "
        "수 없습니다. 별도로 검증되어 입력된 해당 판단만 설명할 수 있습니다.`입니다. "
        "이전 review: " + OLD_FEEDBACK_BY_ID[FIELD_RECORD_ID]
    ),
    GOLD_RECORD_ID: (
        "재생성 지시: `K0`, `Gold`, `현재 허용 evidence` 세 label을 모두 직접 "
        "명시하세요. pinned validator로 검증한 안전한 목표 문구는 `K0의 자연스러운 "
        "설명에 새로 나온 간지를 곧바로 Gold로 사용하면 안 됩니다. 현재 허용 evidence와 "
        "일치하는 사주 사실만 Gold로 사용할 수 있습니다.`입니다. 이전 review: "
        + OLD_FEEDBACK_BY_ID[GOLD_RECORD_ID]
    ),
}


def _prior_events() -> list[dict[str, Any]]:
    return [*prior_recovery._prior_events(), prior_recovery._recovery_event()]


def _recovery_event() -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "recovery_id": RECOVERY_ID,
        "authorized_date_kst": "2026-09-03",
        "reason_code": "review_required_intake_and_explicit_labels",
        "pre_state_sha256": EXPECTED_PRE_STATE_SHA256,
        "provider_calls_before": EXPECTED_PROVIDER_CALLS,
        "affected_record_ids": list(AFFECTED_RECORD_IDS),
        "manual_answer_edit": False,
        "manual_acceptance": False,
        "rewrite_counter_reset": False,
        "provider_draft_and_separate_review_required": True,
    }


def _fixed_review_failure(
    record: Mapping[str, Any], *, record_id: str, spec: Mapping[str, Any]
) -> None:
    drafts = record.get("draft_attempts")
    reviews = record.get("review_attempts")
    if not isinstance(drafts, list) or not isinstance(reviews, list):
        raise Mix2KV4RecoveryError(f"call 181 attempt 이력이 없습니다: {record_id}")
    expected_drafts = EXPECTED_DRAFT_COUNT_BY_ID[record_id]
    expected_reviews = EXPECTED_REVIEW_COUNT_BY_ID[record_id]
    if len(drafts) != expected_drafts or len(reviews) != expected_reviews:
        raise Mix2KV4RecoveryError(f"call 181 attempt prefix가 다릅니다: {record_id}")
    draft = recovery_call174._validate_attempt_common(
        drafts[-1],
        record_id=record_id,
        execution_pass="draft",
        expected_sequence=180,
    )
    review = recovery_call174._validate_attempt_common(
        reviews[-1],
        record_id=record_id,
        execution_pass="review",
        expected_sequence=EXPECTED_PROVIDER_CALLS,
    )
    recovery_call174._validate_draft_attempt_payload(
        draft, record_id=record_id, spec=spec
    )
    recovery_call174._validate_review_attempt_payload(
        review, record_id=record_id, spec=spec
    )
    expected_status = "failed" if record_id == STRENGTH_RECORD_ID else "needs_draft"
    review_payload = review.get("review")
    answer = draft["draft"].get("answer")
    if (
        draft.get("attempt") != expected_drafts
        or draft.get("deterministic_pass") is not True
        or draft.get("deterministic_error") is not None
        or review.get("attempt") != expected_reviews
        or review.get("review_mode") != "same_provider_separate_pass"
        or not isinstance(review_payload, Mapping)
        or review_payload.get("decision") != "FAIL"
        or review_payload.get("failure_codes")
        != [EXPECTED_FAILURE_CODE_BY_ID[record_id]]
        or record.get("status") != expected_status
        or record.get("feedback") != OLD_FEEDBACK_BY_ID[record_id]
        or record.get("rewrites_used") != EXPECTED_REWRITES_BY_ID[record_id]
        or record.get("duplicate_rewrites_used") != 1
        or record.get("accepted") is not None
        or record.get("current_draft") != draft.get("draft")
        or (
            record_id == STRENGTH_RECORD_ID
            and (
                not isinstance(answer, str)
                or sentence_count(answer) != 3
                or len([line for line in answer.splitlines() if line.strip()]) != 3
            )
        )
    ):
        raise Mix2KV4RecoveryError(f"call 181 review FAIL이 다릅니다: {record_id}")


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
            "여덟 번째 복구 전 pipeline state identity가 다릅니다."
        )
    specs = prior_recovery._load_fixed_specs(state)
    for record_id in AFFECTED_RECORD_IDS:
        record = records.get(record_id)
        spec = specs.get(record_id)
        if not isinstance(record, Mapping) or not isinstance(spec, Mapping):
            raise Mix2KV4RecoveryError(
                f"여덟 번째 복구 대상 record/spec이 없습니다: {record_id}"
            )
        expected_spec_sha256 = sha256_bytes(canonical_json_bytes(spec))
        if record.get("spec_sha256") != expected_spec_sha256:
            raise Mix2KV4RecoveryError(
                f"여덟 번째 복구 대상 spec SHA가 다릅니다: {record_id}"
            )
        _fixed_review_failure(record, record_id=record_id, spec=spec)


def build_recovered_state(state: Mapping[str, Any], payload: bytes) -> dict[str, Any]:
    """call 181 pre-state에서 네 행의 status·feedback·감사 event만 바꾼다."""

    _validate_pre_state(state, payload)
    recovered = deepcopy(dict(state))
    recovered["operator_recoveries"] = [*_prior_events(), _recovery_event()]
    recovered["records"][STRENGTH_RECORD_ID]["status"] = "needs_draft"
    for record_id in AFFECTED_RECORD_IDS:
        recovered["records"][record_id]["feedback"] = NEW_FEEDBACK_BY_ID[record_id]
    return recovered


def _exact_changes() -> list[dict[str, Any]]:
    changes = []
    for record_id in AFFECTED_RECORD_IDS:
        before_status = "failed" if record_id == STRENGTH_RECORD_ID else "needs_draft"
        changes.append(
            {
                "record_id": record_id,
                "status": {"before": before_status, "after": "needs_draft"},
                "feedback": {
                    "before": OLD_FEEDBACK_BY_ID[record_id],
                    "after": NEW_FEEDBACK_BY_ID[record_id],
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


def _call178_checkpoint(target: Path) -> dict[str, Any]:
    payload = base_recovery._read_regular_file(
        target / prior_recovery.AFTER_STATE_RELATIVE,
        "call 178 recovery after state",
    )
    checkpoint = base_recovery._decode_object(payload, "call 178 recovery after state")
    prior_recovery._validate_checkpoint_layout(checkpoint)
    return checkpoint


def _project_prior_recovery_state(
    target: Path,
    current_state: Mapping[str, Any],
    incident_state: Mapping[str, Any],
) -> dict[str, Any]:
    checkpoint = _call178_checkpoint(target)
    checkpoint_records = checkpoint.get("records")
    current_records = current_state.get("records")
    incident_records = incident_state.get("records")
    if (
        not isinstance(checkpoint_records, Mapping)
        or not isinstance(current_records, Mapping)
        or not isinstance(incident_records, Mapping)
    ):
        raise Mix2KV4RecoveryError("call 178 projection record가 없습니다.")
    projected = deepcopy(dict(current_state))
    projected["operator_recoveries"] = _prior_events()
    for record_id in PRIOR_PROJECTION_RECORD_IDS:
        saved = checkpoint_records.get(record_id)
        incident = incident_records.get(record_id)
        if not isinstance(saved, Mapping) or not isinstance(incident, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 178 projection 대상 record가 없습니다: {record_id}"
            )
        saved_drafts = saved.get("draft_attempts")
        saved_reviews = saved.get("review_attempts")
        incident_drafts = incident.get("draft_attempts")
        incident_reviews = incident.get("review_attempts")
        if (
            set(incident) != set(saved)
            or not isinstance(saved_drafts, list)
            or not isinstance(saved_reviews, list)
            or not isinstance(incident_drafts, list)
            or not isinstance(incident_reviews, list)
            or incident_drafts[: len(saved_drafts)] != saved_drafts
            or incident_reviews[: len(saved_reviews)] != saved_reviews
            or len(incident_drafts) != len(saved_drafts) + 1
            or len(incident_reviews) != len(saved_reviews) + 1
        ):
            raise Mix2KV4RecoveryError(
                f"call 181 projection attempt prefix가 다릅니다: {record_id}"
            )
        projected_record = deepcopy(dict(incident))
        projected_record["review_attempts"] = deepcopy(incident_reviews[:-1])
        projected_record["rewrites_used"] = saved.get("rewrites_used")
        projected_record["status"] = "needs_review"
        projected_record["feedback"] = ""
        projected_record["accepted"] = None
        projected["records"][record_id] = projected_record
    return projected


def _validate_prior_projection(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    incident_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if incident_state is None:
        payload = base_recovery._read_regular_file(
            target / BEFORE_STATE_RELATIVE,
            "call 181 recovery before state",
        )
        incident_state = base_recovery._decode_object(
            payload, "call 181 recovery before state"
        )
    projected = _project_prior_recovery_state(target, current_state, incident_state)
    report = prior_recovery.validate_recovery_chain(
        target,
        projected,
        require_completed_provider_passes=False,
    )
    if report is None:
        raise Mix2KV4RecoveryError("projection한 call 178 recovery chain이 없습니다.")
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


def _validate_strength_intake_answer(answer: Any) -> None:
    if not isinstance(answer, str):
        raise Mix2KV4RecoveryError("call 181 strength 후속 답변이 없습니다.")
    required_groups = (
        ("생년월일",),
        ("양력", "음력"),
        ("출생시각", "시간 미상"),
        ("출생 도시", "국가"),
        ("윤달",),
        ("계산 도구",),
        ("검증된",),
        ("두 판단",),
    )
    has_four_pillars = "네 기둥" in answer or all(
        label in answer for label in ("년주", "월주", "일주", "시주")
    )
    has_non_inference = any(
        marker in answer
        for marker in (
            "새로 만들지",
            "새로 판단하지",
            "새로 판정하지",
            "새로 계산하지",
        )
    )
    clauses = [
        clause.strip()
        for clause in re.split(r"[\n.!?。！？]+", answer)
        if clause.strip()
    ]
    decision_unavailable = re.compile(r"(?:정|판단|판정|계산)할\s*수(?:는|가)?\s*없")
    decision_available = re.compile(
        r"(?:정|판단|판정|계산)할\s*수(?:는|가)?\s*있|"
        r"(?:정|판단|판정|계산|결정|도출)(?:이|가)?\s*가능"
    )
    tool_unavailable = re.compile(
        r"(?:연결(?:되어)?\s*있지(?:는)?\s*않|연결되지\s*않|제공되지\s*않|"
        r"사용할\s*수\s*없|(?:연결|도구)[^\n.!?。！？]{0,16}없어)"
    )
    tool_available = re.compile(
        r"(?:연결(?:되어)?\s*있(?!지(?:는)?\s*않)|연결됐|"
        r"제공되어\s*있(?!지(?:는)?\s*않)|사용할\s*수\s*있|"
        r"(?:연결|사용|이용)(?:이|가)?\s*가능)"
    )
    has_unavailable_decision_clause = any(
        "용신" in clause
        and "신강약" in clause
        and decision_unavailable.search(clause) is not None
        for clause in clauses
    )
    tool_clauses = [clause for clause in clauses if "계산 도구" in clause]
    calculator_clauses = [
        clause
        for clause in clauses
        if any(label in clause for label in ("계산 도구", "명식 계산기", "계산기"))
    ]
    has_unavailable_tool_clause = any(
        tool_unavailable.search(clause) is not None for clause in tool_clauses
    )
    has_opposite_claim = decision_available.search(answer) is not None or any(
        tool_available.search(clause) is not None for clause in calculator_clauses
    )
    if (
        any(any(value not in answer for value in group) for group in required_groups)
        or not has_four_pillars
        or not has_non_inference
        or not has_unavailable_decision_clause
        or not has_unavailable_tool_clause
        or has_opposite_claim
    ):
        raise Mix2KV4RecoveryError(
            "call 181 strength 후속 intake·비추론 계약이 다릅니다."
        )


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
                f"call 181 후속 draft attempt가 없습니다: {record_id}"
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
                f"call 181 recovery 후속 draft 길이가 다릅니다: {record_id}"
            )
        if record_id == STRENGTH_RECORD_ID:
            _validate_strength_intake_answer(answer)
    for attempt in later_reviews:
        if not isinstance(attempt, Mapping):
            raise Mix2KV4RecoveryError(
                f"call 181 후속 review attempt가 없습니다: {record_id}"
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
                f"call 181 recovery draft가 완료되지 않았습니다: {record_id}"
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
                f"call 181 recovery review가 완료되지 않았습니다: {record_id}"
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


def _validate_checkpoint_layout(checkpoint: Mapping[str, Any]) -> None:
    records = checkpoint.get("records")
    if not isinstance(records, Mapping):
        raise Mix2KV4RecoveryError("call 181 checkpoint record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        record = records.get(record_id)
        if (
            not isinstance(record, Mapping)
            or record.get("status") != "needs_draft"
            or record.get("feedback") != NEW_FEEDBACK_BY_ID[record_id]
            or record.get("rewrites_used") != EXPECTED_REWRITES_BY_ID[record_id]
            or record.get("duplicate_rewrites_used") != 1
            or record.get("accepted") is not None
        ):
            raise Mix2KV4RecoveryError(
                f"call 181 checkpoint 상태가 다릅니다: {record_id}"
            )


def validate_recovery_bundle(
    target: Path,
    current_state: Mapping[str, Any],
    *,
    require_completed_provider_passes: bool,
) -> dict[str, Any]:
    """여덟 번째 sidecar와 네 행의 실제 retry provenance를 검증한다."""

    before_payload = base_recovery._read_regular_file(
        target / BEFORE_STATE_RELATIVE, "call 181 recovery before state"
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE, "call 181 recovery after state"
    )
    manifest_payload = base_recovery._read_regular_file(
        target / RECOVERY_MANIFEST_RELATIVE, "call 181 recovery manifest"
    )
    before_state = base_recovery._decode_object(
        before_payload, "call 181 recovery before state"
    )
    after_state = base_recovery._decode_object(
        after_payload, "call 181 recovery after state"
    )
    manifest = base_recovery._decode_object(
        manifest_payload, "call 181 recovery manifest"
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
        raise Mix2KV4RecoveryError("call 181 operator recovery bundle이 다릅니다.")

    _validate_checkpoint_layout(after_state)
    specs = prior_recovery._load_fixed_specs(current_state)
    current_records = current_state.get("records")
    checkpoint_records = after_state.get("records")
    if not isinstance(current_records, Mapping) or not isinstance(
        checkpoint_records, Mapping
    ):
        raise Mix2KV4RecoveryError("call 181 recovery descendant record가 없습니다.")
    for record_id in AFFECTED_RECORD_IDS:
        current = current_records.get(record_id)
        saved = checkpoint_records.get(record_id)
        spec = specs.get(record_id)
        if not all(isinstance(value, Mapping) for value in (current, saved, spec)):
            raise Mix2KV4RecoveryError(
                f"call 181 recovery descendant 입력이 없습니다: {record_id}"
            )
        expected_spec_sha256 = sha256_bytes(canonical_json_bytes(spec))
        if (
            current.get("spec_sha256") != expected_spec_sha256
            or saved.get("spec_sha256") != expected_spec_sha256
        ):
            raise Mix2KV4RecoveryError(
                f"call 181 recovery spec SHA가 다릅니다: {record_id}"
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
    """call 178 audit projection과 실제 R4/R2/R2/R2 FAIL 후속 이력을 분리한다."""

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
            "여덟 번째 recovery sidecar와 state event가 일치하지 않습니다."
        )
    if not recovery_dir_exists or not manifest_exists:
        raise Mix2KV4RecoveryError(
            "여덟 번째 recovery state event에 대응하는 sidecar가 없습니다."
        )

    eighth_report = validate_recovery_bundle(
        target,
        current_state,
        require_completed_provider_passes=require_completed_provider_passes,
    )
    prior_report = _validate_prior_projection(target, current_state)
    prior_recovery._validate_prior_descendants(
        target,
        current_state,
        require_completed=require_completed_provider_passes,
    )
    overflow_ids = recovery_call148._attempt_overflow_ids(current_state)
    if require_completed_provider_passes:
        if overflow_ids != EXPECTED_OVERFLOW_IDS:
            raise Mix2KV4RecoveryError(
                "완료된 여덟 번째 recovery attempt 예외 집합이 다릅니다."
            )
    elif not overflow_ids.issubset(EXPECTED_OVERFLOW_IDS):
        raise Mix2KV4RecoveryError(
            "진행 중 여덟 번째 recovery attempt 예외 집합이 다릅니다."
        )
    return {
        "schema_version": "1.0.0",
        "all_recoveries_completed": require_completed_provider_passes,
        "recoveries": [*prior_report["recoveries"], eighth_report],
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
    _validate_prior_projection(target, state, incident_state=state)
    recovered = build_recovered_state(state, payload)
    _validate_checkpoint_layout(recovered)
    if recovery_call148._attempt_overflow_ids(state) != EXPECTED_OVERFLOW_IDS:
        raise Mix2KV4RecoveryError("call 181 pre-state attempt 예외 집합이 다릅니다.")


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
    if report is None or len(report.get("recoveries", [])) != 8:
        raise Mix2KV4RecoveryError(
            "live write 전 여덟 단계 recovery chain 검증에 실패했습니다."
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
        recovery_dir, "call 181 partial recovery provenance"
    )
    _write_or_verify_bundle_file(
        target / BEFORE_STATE_RELATIVE,
        state_payload,
        "call 181 recovery before state",
    )
    _write_or_verify_bundle_file(
        target / AFTER_STATE_RELATIVE,
        after_payload,
        "call 181 recovery after state",
    )
    _write_or_verify_bundle_file(
        target / RECOVERY_MANIFEST_RELATIVE,
        manifest_payload,
        "call 181 recovery manifest",
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
        "prepared call 181 recovery before state",
    )
    after_payload = base_recovery._read_regular_file(
        target / AFTER_STATE_RELATIVE,
        "prepared call 181 recovery after state",
    )
    after_state = base_recovery._decode_object(
        after_payload, "prepared call 181 recovery after state"
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
        raise Mix2KV4RecoveryError("허용된 call 181 recovery target이 아닙니다.")
    teachers._reject_symlink_components(target, "call 181 recovery target")
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
                raise Mix2KV4RecoveryError("call 181 recovery 이력이 없습니다.")
            report["already_applied"] = True
            report["resumed_prepared_bundle"] = False
            return report

        _validate_incident_pre_state(target, state, state_payload)
        recovered = build_recovered_state(state, state_payload)
        after_payload = teachers._json_bytes(recovered)
        recovery_manifest = _expected_manifest(after_payload)
        teachers._ensure_private_directory(
            recovery_dir, "call 181 operator recovery provenance"
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
        description="MIX2K v4 provider call 181 checkpoint의 감사 가능한 복구"
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
