# mix2k_v4_finalize.py - 교차 검수 2K candidate를 전수 token audit해 학습 build로 고정한다.

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_build import (
    DEFAULT_CONFIG,
    _load_config,
    _validate_model_snapshot,
)
from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    EXPECTED_ROWS,
    MAX_COMPLETION_TOKENS,
    MAX_PROMPT_TOKENS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    Mix2KV4ContractError,
    jsonl_bytes,
    normalize_answer,
    sha256_bytes,
    sha256_file,
    validate_draft,
    validate_review,
)
from scripts.data.mix2k_v4_teachers import (
    CODEX_FALLBACK_EXECUTION_MODE,
    CODEX_FALLBACK_POLICY_PATH,
    CROSS_PROVIDER_REVIEW_MODE,
    MAXIMUM_DUPLICATE_REWRITE_ROUNDS,
    SAME_PROVIDER_REVIEW_MODE,
    STRICT_EXECUTION_MODE,
    Mix2KV4TeacherError,
    _load_execution_policy,
    _normalize_draft_answer_particles,
    _recoverable_seed_attempt,
    _seed_cross_acceptance,
    _seed_draft_provider,
    _validate_spec_build,
)
from scripts.data.mix2k_v4_teachers import (
    RUNNER_PATH as TEACHER_RUNNER_PATH,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

DEFAULT_OUTPUT_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/final/v1.0.1"
)
FINALIZER_PATH = Path(__file__).resolve()
CONTRACTS_PATH = FINALIZER_PATH.with_name("mix2k_v4_contracts.py")
MAX_JSON_BYTES = 64 * 1024 * 1024
CANDIDATE_FIELDS = {
    "schema_version",
    "dataset_version",
    "id",
    "conversation_id",
    "task_axis",
    "template_family",
    "substantive",
    "multiturn",
    "prompt",
    "assistant",
    "runtime_snapshot_sha256",
    "teacher",
    "restricted_local_only",
}
STRICT_TEACHER_FIELDS = {
    "drafter",
    "reviewer",
    "peer_review",
    "deterministic_validation",
    "rewrites_used",
    "used_fact_paths",
    "used_fact_values",
    "soft_interpretation_used",
    "limitations",
}
FALLBACK_TEACHER_FIELDS = {
    "assigned_drafter",
    "assigned_reviewer",
    "actual_drafter",
    "actual_reviewer",
    "review_mode",
    "review_result",
    "deterministic_validation",
    "fallback_policy_id",
    "fallback_used",
    "rewrites_used",
    "used_fact_paths",
    "used_fact_values",
    "soft_interpretation_used",
    "limitations",
}
FALLBACK_ACCEPTED_FIELDS = {
    "assigned_drafter",
    "assigned_reviewer",
    "draft_provider",
    "review_provider",
    "review_mode",
    "fallback_used",
    "draft",
    "review",
}
FALLBACK_IMPORTED_ACCEPTED_FIELDS = {
    *FALLBACK_ACCEPTED_FIELDS,
    "imported_cross_provider_pass",
}


class Mix2KV4FinalizeError(RuntimeError):
    """2K candidate·token audit·immutable final build 계약 위반."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4FinalizeError(f"{label} 경로에 symlink component가 있습니다.")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    value, _ = _load_json_snapshot(path, label)
    return value


def _read_snapshot_bytes(path: Path, label: str) -> tuple[bytes, str]:
    """regular file을 no-follow fd로 열어 상한까지만 읽고 같은 bytes를 hash한다."""

    _reject_symlink_components(path, label)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Mix2KV4FinalizeError(f"{label}이 없거나 안전하지 않습니다.")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Mix2KV4FinalizeError(f"{label}을 열지 못했습니다.") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_JSON_BYTES:
            raise Mix2KV4FinalizeError(f"{label} 크기·형식이 허용 범위 밖입니다.")
        chunks: list[bytes] = []
        remaining = MAX_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            not 1 <= len(payload) <= MAX_JSON_BYTES
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or after.st_size != len(payload)
        ):
            raise Mix2KV4FinalizeError(f"{label}이 읽는 동안 변경됐습니다.")
    except OSError as exc:
        raise Mix2KV4FinalizeError(f"{label}을 읽지 못했습니다.") from exc
    finally:
        os.close(descriptor)
    return payload, sha256_bytes(payload)


def _load_json_snapshot(path: Path, label: str) -> tuple[dict[str, Any], str]:
    """한 번 읽은 bytes로 JSON parse와 SHA 검증 입력을 함께 만든다."""

    payload, payload_sha256 = _read_snapshot_bytes(path, label)
    try:
        value = json.loads(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4FinalizeError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4FinalizeError(f"{label} 최상위는 object여야 합니다.")
    return value, payload_sha256


def _load_jsonl_snapshot(
    path: Path, label: str
) -> tuple[list[dict[str, Any]], str]:
    """candidate JSONL도 동일 bytes를 hash하고 parse해 TOCTOU를 피한다."""

    payload, payload_sha256 = _read_snapshot_bytes(path, label)
    try:
        text = payload.decode("utf-8")
        rows: list[dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                raise Mix2KV4FinalizeError(f"{label} 빈 행: {number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise Mix2KV4FinalizeError(f"{label} object 오류: {number}")
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4FinalizeError(f"{label}을 읽지 못했습니다.") from exc
    return rows, payload_sha256


def _atomic_build(
    root: Path, build_id: str, files: Mapping[str, bytes]
) -> tuple[Path, str]:
    if not root.is_absolute():
        raise Mix2KV4FinalizeError("final build root는 절대경로여야 합니다.")
    _reject_symlink_components(root, "final build root")
    target = root / build_id
    _reject_symlink_components(target, "final build target")
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise Mix2KV4FinalizeError("기존 final build 경로가 안전하지 않습니다.")
        for relative, payload in files.items():
            path = target / relative
            _reject_symlink_components(path, f"final build artifact {relative}")
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise Mix2KV4FinalizeError(
                    "기존 final build가 동일 identity와 다릅니다."
                )
        return target, "reused"
    root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    root.chmod(PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=root))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for relative, payload in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
            path.parent.chmod(PRIVATE_DIR_MODE)
            path.write_bytes(payload)
            path.chmod(PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target, "created"


def select_training_max_length(
    maximum_tokens: int, ladder: Sequence[int]
) -> int | None:
    for value in ladder:
        if maximum_tokens <= value:
            return value
    return None


def _teacher_inputs(
    teacher_build: Path,
) -> tuple[
    dict[str, Any],
    Path,
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, str],
]:
    if (
        not teacher_build.is_absolute()
        or teacher_build.is_symlink()
        or not teacher_build.is_dir()
    ):
        raise Mix2KV4FinalizeError("teacher build가 없거나 symlink입니다.")
    manifest, manifest_sha256 = _load_json_snapshot(
        teacher_build / "teacher_manifest.json", "teacher manifest"
    )
    relative = manifest.get("candidate_path")
    contract_mode = manifest.get("teacher_contract_mode")
    if contract_mode not in {None, STRICT_EXECUTION_MODE, CODEX_FALLBACK_EXECUTION_MODE}:
        raise Mix2KV4FinalizeError("알 수 없는 teacher contract mode입니다.")
    fallback = contract_mode == CODEX_FALLBACK_EXECUTION_MODE
    seed_state = None
    common_invalid = (
        manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("mode") != "full"
        or manifest.get("rows") != EXPECTED_ROWS
        or relative != "accepted/training_candidates_2000.jsonl"
        or manifest.get("deterministic_validation_passed") is not True
        or manifest.get("contracts_sha256") != sha256_file(CONTRACTS_PATH)
        or manifest.get("runner_sha256") != sha256_file(TEACHER_RUNNER_PATH)
        or manifest.get("development_targets_accessed") is not False
        or manifest.get("api_keys_used") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("training_execution_allowed") is not False
    )
    if fallback:
        runtime_identity = manifest.get("runtime_identity")
        state_path = teacher_build / "pipeline_state.json"
        seed_path = teacher_build / "provenance/seed_pipeline_state.json"
        for path, label in (
            (state_path, "teacher pipeline state"),
            (seed_path, "teacher seed state snapshot"),
        ):
            _reject_symlink_components(path, label)
            if path.is_symlink() or not path.is_file():
                raise Mix2KV4FinalizeError(f"{label}가 없거나 안전하지 않습니다.")
        if (
            common_invalid
            or manifest.get("schema_version") != "1.1.0"
            or manifest.get("peer_review_passed") is not False
            or manifest.get("all_second_pass_reviews_passed") is not True
            or manifest.get("execution_policy_path")
            != CODEX_FALLBACK_POLICY_PATH.relative_to(REPO_ROOT).as_posix()
            or manifest.get("execution_policy_sha256")
            != sha256_file(CODEX_FALLBACK_POLICY_PATH)
            or manifest.get("lora_experimental_training_allowed") is not True
            or manifest.get("production_promotion_allowed") is not False
            or not isinstance(runtime_identity, Mapping)
            or set(runtime_identity)
            != {
                "actual_provider",
                "cli_version",
                "configured_model_selector",
                "auth_type",
                "execution_policy_sha256",
            }
            or runtime_identity.get("actual_provider") != "codex"
            or not isinstance(runtime_identity.get("cli_version"), str)
            or not runtime_identity["cli_version"].strip()
            or "\n" in runtime_identity["cli_version"]
            or len(runtime_identity["cli_version"].encode("utf-8")) > 256
            or runtime_identity.get("configured_model_selector")
            != "configured_subscription_default"
            or runtime_identity.get("auth_type") != "chatgpt_subscription"
            or runtime_identity.get("execution_policy_sha256")
            != manifest.get("execution_policy_sha256")
        ):
            raise Mix2KV4FinalizeError("teacher fallback manifest 계약이 다릅니다.")
        state, state_sha256 = _load_json_snapshot(
            state_path, "teacher pipeline state"
        )
        seed_state, seed_state_sha256 = _load_json_snapshot(
            seed_path, "teacher seed state snapshot"
        )
        if (
            state_sha256 != manifest.get("pipeline_state_sha256")
            or seed_state_sha256 != manifest.get("seed_state_sha256")
            or state.get("runner_sha256") != manifest.get("runner_sha256")
            or state.get("execution", {}).get("mode")
            != CODEX_FALLBACK_EXECUTION_MODE
            or state.get("execution", {}).get("policy_sha256")
            != manifest.get("execution_policy_sha256")
            or state.get("runtime_identity") != runtime_identity
            or state.get("seed_state_sha256") != manifest.get("seed_state_sha256")
            or state.get("seed_import") != manifest.get("seed_import")
        ):
            raise Mix2KV4FinalizeError("teacher fallback state provenance가 다릅니다.")
    elif (
        common_invalid
        or manifest.get("peer_review_passed") is not True
        or manifest.get("teacher_roles") != {"claude": 1000, "codex": 1000}
        or manifest.get("review_roles") != {"claude": 1000, "codex": 1000}
    ):
        raise Mix2KV4FinalizeError("teacher manifest 교차검수·격리 계약이 다릅니다.")
    else:
        state = None
    candidate_path = teacher_build / relative
    rows, candidate_sha256 = _load_jsonl_snapshot(candidate_path, "teacher candidate")
    if candidate_sha256 != manifest.get("candidate_sha256"):
        raise Mix2KV4FinalizeError("teacher candidate hash가 다릅니다.")
    return (
        manifest,
        candidate_path,
        rows,
        state,
        seed_state,
        {
            "teacher_manifest_sha256": manifest_sha256,
            "teacher_candidate_sha256": candidate_sha256,
        },
    )


def _positive_call_sequence(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _seed_imported_draft_matches(
    *,
    seed_record: Mapping[str, Any],
    attempt: Mapping[str, Any],
    spec: Mapping[str, Any],
    actual_drafter: str,
) -> bool:
    """고정 seed에서 이관한 draft를 같은 변환 규칙으로 다시 계산한다."""

    source_kind = attempt.get("imported_source_kind")
    if source_kind == "current_draft":
        source_draft = seed_record.get("current_draft")
        provider = _seed_draft_provider(seed_record)
    elif source_kind == "deterministic_recheck":
        recovered = _recoverable_seed_attempt(seed_record)
        if recovered is None:
            return False
        source_draft, provider = recovered
    else:
        return False
    if not isinstance(source_draft, Mapping) or provider != actual_drafter:
        return False
    provider_draft = deepcopy(dict(source_draft))
    candidate, particle_normalized = _normalize_draft_answer_particles(
        provider_draft
    )
    try:
        validate_draft(spec, candidate)
    except Mix2KV4ContractError:
        return False
    return bool(
        attempt.get("provider_draft") == provider_draft
        and attempt.get("draft") == candidate
        and attempt.get("particle_normalized") is particle_normalized
        and attempt.get("particle_normalizer_version")
        == ("ganzhi-particle-v1" if particle_normalized else None)
        and attempt.get("layout_normalized") is False
        and attempt.get("layout_normalizer_version") is None
        and attempt.get("source_rewrites_used") == seed_record.get("rewrites_used")
    )


def _validate_fallback_row_provenance(
    *,
    row: Mapping[str, Any],
    spec: Mapping[str, Any],
    teacher: Mapping[str, Any],
    teacher_manifest: Mapping[str, Any],
    state: Mapping[str, Any],
    seed_state: Mapping[str, Any],
) -> bool:
    """candidate가 최신 draft 이후의 별도 PASS와 고정 seed에 묶였는지 검증한다."""

    record_id = spec["id"]
    assigned_drafter = teacher.get("assigned_drafter")
    assigned_reviewer = teacher.get("assigned_reviewer")
    actual_drafter = teacher.get("actual_drafter")
    actual_reviewer = teacher.get("actual_reviewer")
    expected_review_mode = (
        CROSS_PROVIDER_REVIEW_MODE
        if actual_drafter != actual_reviewer
        else SAME_PROVIDER_REVIEW_MODE
    )
    allowed_drafter = actual_drafter == assigned_drafter or (
        assigned_drafter == "claude" and actual_drafter == "codex"
    )
    allowed_reviewer = actual_reviewer == assigned_reviewer or (
        assigned_reviewer == "claude" and actual_reviewer == "codex"
    )
    state_records = state.get("records")
    seed_records = seed_state.get("records")
    state_record = (
        state_records.get(record_id) if isinstance(state_records, Mapping) else None
    )
    seed_record = (
        seed_records.get(record_id) if isinstance(seed_records, Mapping) else None
    )
    if not isinstance(state_record, Mapping) or not isinstance(seed_record, Mapping):
        raise Mix2KV4FinalizeError(
            f"teacher fallback state·seed record가 없습니다: {record_id}"
        )
    accepted = state_record.get("accepted")
    current_draft = state_record.get("current_draft")
    draft_attempts = state_record.get("draft_attempts")
    review_attempts = state_record.get("review_attempts")
    if (
        not isinstance(accepted, Mapping)
        or not isinstance(current_draft, Mapping)
        or not isinstance(draft_attempts, list)
        or not draft_attempts
        or not isinstance(review_attempts, list)
        or not review_attempts
        or not isinstance(draft_attempts[-1], Mapping)
        or not isinstance(review_attempts[-1], Mapping)
    ):
        raise Mix2KV4FinalizeError(
            f"teacher fallback 최신 attempt provenance가 없습니다: {record_id}"
        )
    latest_draft = draft_attempts[-1]
    latest_review = review_attempts[-1]
    imported_acceptance = accepted.get("imported_cross_provider_pass") is True
    expected_accepted_fields = (
        FALLBACK_IMPORTED_ACCEPTED_FIELDS
        if imported_acceptance
        else FALLBACK_ACCEPTED_FIELDS
    )
    review = accepted.get("review")
    review_contract_valid = True
    try:
        validate_review(spec, review)
    except Mix2KV4ContractError:
        review_contract_valid = False
    spec_sha256 = sha256_bytes(canonical_json_bytes(spec))
    common_invalid = (
        set(teacher) != FALLBACK_TEACHER_FIELDS
        or assigned_drafter != spec.get("drafter")
        or assigned_reviewer != spec.get("reviewer")
        or actual_drafter not in {"claude", "codex"}
        or actual_reviewer not in {"claude", "codex"}
        or not allowed_drafter
        or not allowed_reviewer
        or teacher.get("review_mode") != expected_review_mode
        or teacher.get("review_result") != "PASS"
        or teacher.get("fallback_policy_id")
        != teacher_manifest.get("execution_policy_id")
        or teacher.get("fallback_used")
        is not (
            actual_drafter != assigned_drafter
            or actual_reviewer != assigned_reviewer
        )
        or state_record.get("spec_sha256") != spec_sha256
        or seed_record.get("spec_sha256") != spec_sha256
        or state_record.get("status") != "accepted"
        or set(accepted) != expected_accepted_fields
        or accepted.get("assigned_drafter") != assigned_drafter
        or accepted.get("assigned_reviewer") != assigned_reviewer
        or accepted.get("draft_provider") != actual_drafter
        or accepted.get("review_provider") != actual_reviewer
        or accepted.get("review_mode") != expected_review_mode
        or accepted.get("fallback_used") is not teacher.get("fallback_used")
        or accepted.get("draft") != current_draft
        or accepted.get("draft", {}).get("answer") != row.get("assistant")
        or not review_contract_valid
        or review.get("decision") != "PASS"
        or latest_draft.get("assigned_provider") != assigned_drafter
        or latest_draft.get("provider") != actual_drafter
        or latest_draft.get("fallback_used")
        is not (actual_drafter != assigned_drafter)
        or latest_draft.get("execution_pass") != "draft"
        or latest_draft.get("attempt") != len(draft_attempts)
        or latest_draft.get("deterministic_pass") is not True
        or latest_draft.get("draft") != current_draft
        or latest_review.get("assigned_provider") != assigned_reviewer
        or latest_review.get("provider") != actual_reviewer
        or latest_review.get("fallback_used")
        is not (actual_reviewer != assigned_reviewer)
        or latest_review.get("execution_pass") != "review"
        or latest_review.get("attempt") != len(review_attempts)
        or latest_review.get("review_mode") != expected_review_mode
        or latest_review.get("review") != review
        or teacher.get("rewrites_used") != state_record.get("rewrites_used")
    )
    if common_invalid:
        raise Mix2KV4FinalizeError(
            f"teacher fallback candidate provenance가 다릅니다: {record_id}"
        )

    draft_imported = latest_draft.get("imported_from_seed") is True
    review_imported = latest_review.get("imported_from_seed") is True
    if imported_acceptance:
        replayed, _ = _seed_cross_acceptance(
            source=seed_record,
            spec=spec,
            candidate=current_draft,
        )
        separate_pass = bool(
            expected_review_mode == CROSS_PROVIDER_REVIEW_MODE
            and draft_imported
            and review_imported
            and replayed == accepted
        )
    else:
        seed_draft_valid = True
        if draft_imported:
            seed_draft_valid = _seed_imported_draft_matches(
                seed_record=seed_record,
                attempt=latest_draft,
                spec=spec,
                actual_drafter=str(actual_drafter),
            )
        draft_sequence = latest_draft.get("provider_call_sequence")
        review_sequence = latest_review.get("provider_call_sequence")
        provider_calls = state.get("provider_calls")
        sequence_valid = (
            _positive_call_sequence(review_sequence)
            and _positive_call_sequence(provider_calls)
            and review_sequence <= provider_calls
            and (
                draft_imported
                or (
                    _positive_call_sequence(draft_sequence)
                    and draft_sequence < review_sequence
                )
            )
        )
        provider_pair_valid = (
            expected_review_mode == SAME_PROVIDER_REVIEW_MODE
            and actual_drafter == actual_reviewer == "codex"
        ) or (
            expected_review_mode == CROSS_PROVIDER_REVIEW_MODE
            and draft_imported
            and actual_drafter == "claude"
            and actual_reviewer == "codex"
        )
        separate_pass = bool(
            provider_pair_valid
            and not review_imported
            and seed_draft_valid
            and sequence_valid
        )
    if not separate_pass:
        raise Mix2KV4FinalizeError(
            f"teacher fallback draft·review 호출 순서가 다릅니다: {record_id}"
        )
    return imported_acceptance


def _validate_candidates(
    candidates: Sequence[dict[str, Any]],
    specs: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
    teacher_manifest: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    seed_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if len(candidates) != EXPECTED_ROWS:
        raise Mix2KV4FinalizeError("teacher candidate가 2,000행이 아닙니다.")
    specs_by_id = {row["id"]: row for row in specs}
    seen: set[str] = set()
    answers: list[str] = []
    fallback = (
        teacher_manifest is not None
        and teacher_manifest.get("teacher_contract_mode")
        == CODEX_FALLBACK_EXECUTION_MODE
    )
    inherited_seed_passes = 0
    if fallback and (
        not isinstance(state, Mapping) or not isinstance(seed_state, Mapping)
    ):
        raise Mix2KV4FinalizeError("teacher fallback state·seed snapshot이 없습니다.")
    for row in candidates:
        if not isinstance(row, dict) or set(row) != CANDIDATE_FIELDS:
            raise Mix2KV4FinalizeError("teacher candidate field 집합이 다릅니다.")
        record_id = row.get("id")
        spec = specs_by_id.get(record_id)
        teacher = row.get("teacher")
        expected_snapshot = (
            spec["runtime_binding"]["snapshot_sha256"]
            if spec is not None and spec["runtime_binding"] is not None
            else None
        )
        teacher_invalid = not isinstance(teacher, dict)
        if not teacher_invalid and fallback and spec is not None:
            inherited_seed_passes += int(
                _validate_fallback_row_provenance(
                    row=row,
                    spec=spec,
                    teacher=teacher,
                    teacher_manifest=teacher_manifest,
                    state=state,  # type: ignore[arg-type]
                    seed_state=seed_state,  # type: ignore[arg-type]
                )
            )
        elif not teacher_invalid and spec is not None:
            teacher_invalid = (
                set(teacher) != STRICT_TEACHER_FIELDS
                or teacher.get("drafter") != spec.get("drafter")
                or teacher.get("reviewer") != spec.get("reviewer")
                or teacher.get("peer_review") != "PASS"
            )
        if (
            spec is None
            or record_id in seen
            or row.get("schema_version") != ("1.1.0" if fallback else "1.0.0")
            or row.get("dataset_version") != DATASET_VERSION
            or row.get("conversation_id") != spec["conversation_id"]
            or row.get("task_axis") != spec["task_axis"]
            or row.get("template_family") != spec["template_family"]
            or row.get("substantive") is not spec["substantive"]
            or row.get("multiturn") is not spec["multiturn"]
            or row.get("prompt") != spec["prompt"]
            or row.get("runtime_snapshot_sha256") != expected_snapshot
            or row.get("restricted_local_only") is not False
            or not isinstance(row.get("assistant"), str)
            or not row["assistant"].strip()
            or teacher_invalid
            or teacher.get("deterministic_validation") != "PASS"
            or isinstance(teacher.get("rewrites_used"), bool)
            or not isinstance(teacher.get("rewrites_used"), int)
            or not 0 <= teacher["rewrites_used"] <= 2
        ):
            raise Mix2KV4FinalizeError(
                f"teacher candidate identity가 다릅니다: {record_id}"
            )
        draft = {
            "record_id": record_id,
            "answer": row["assistant"],
            "used_fact_paths": teacher["used_fact_paths"],
            "used_fact_values": teacher["used_fact_values"],
            "soft_interpretation_used": teacher["soft_interpretation_used"],
            "limitations": teacher["limitations"],
            "self_check": "PASS",
        }
        if (
            fallback
            and state["records"][record_id]["accepted"].get("draft") != draft  # type: ignore[index]
        ):
            raise Mix2KV4FinalizeError(
                f"teacher fallback accepted draft provenance가 다릅니다: {record_id}"
            )
        try:
            validate_draft(spec, draft)
        except Mix2KV4ContractError as exc:
            raise Mix2KV4FinalizeError(str(exc)) from exc
        seen.add(record_id)
        answers.append(row["assistant"].strip())
    if seen != set(specs_by_id):
        raise Mix2KV4FinalizeError("teacher candidate ID 집합이 spec과 다릅니다.")
    exact = Counter(answers)
    normalized = Counter(normalize_answer(answer) for answer in answers)
    exact_duplicates = sum(count - 1 for count in exact.values() if count > 1)
    normalized_maximum = max(normalized.values(), default=0)
    if exact_duplicates > int(
        config["diversity"]["exact_duplicate_answers_maximum"]
    ) or normalized_maximum > int(
        config["diversity"]["normalized_answer_multiplicity_maximum"]
    ):
        raise Mix2KV4FinalizeError("teacher candidate 답변 중복 계약을 넘었습니다.")
    axes = Counter(row["task_axis"] for row in candidates)
    assigned_drafter_field = "assigned_drafter" if fallback else "drafter"
    assigned_reviewer_field = "assigned_reviewer" if fallback else "reviewer"
    drafters = Counter(
        row["teacher"][assigned_drafter_field] for row in candidates
    )
    reviewers = Counter(
        row["teacher"][assigned_reviewer_field] for row in candidates
    )
    expected_drafters = (
        teacher_manifest.get("assigned_teacher_roles")
        if fallback
        else {"claude": 1000, "codex": 1000}
    )
    expected_reviewers = (
        teacher_manifest.get("assigned_review_roles")
        if fallback
        else {"claude": 1000, "codex": 1000}
    )
    if dict(axes) != EXPECTED_AXES or dict(drafters) != expected_drafters:
        raise Mix2KV4FinalizeError("teacher candidate axis·drafter 비율이 다릅니다.")
    if dict(reviewers) != expected_reviewers:
        raise Mix2KV4FinalizeError("teacher candidate reviewer 비율이 다릅니다.")
    actual_drafters = Counter(
        row["teacher"]["actual_drafter"] for row in candidates
    ) if fallback else drafters
    actual_reviewers = Counter(
        row["teacher"]["actual_reviewer"] for row in candidates
    ) if fallback else reviewers
    review_modes = (
        Counter(row["teacher"]["review_mode"] for row in candidates)
        if fallback
        else Counter({CROSS_PROVIDER_REVIEW_MODE: len(candidates)})
    )
    fallback_state_counts_valid = True
    if fallback:
        state_records = state.get("records")  # type: ignore[union-attr]
        if not isinstance(state_records, Mapping):
            fallback_state_counts_valid = False
        else:
            layout_normalized_rows = 0
            particle_normalized_rows = 0
            duplicate_rewrite_rows = 0
            duplicate_rewrite_attempts = 0
            call_sequences: set[int] = set()
            non_import_attempts = 0
            attempt_sequences_valid = True
            for state_record in state_records.values():
                if not isinstance(state_record, Mapping):
                    fallback_state_counts_valid = False
                    break
                draft_attempts = state_record.get("draft_attempts")
                review_attempts = state_record.get("review_attempts")
                duplicate_rewrites = state_record.get("duplicate_rewrites_used")
                if (
                    not isinstance(draft_attempts, list)
                    or not draft_attempts
                    or not isinstance(review_attempts, list)
                    or not review_attempts
                    or isinstance(duplicate_rewrites, bool)
                    or not isinstance(duplicate_rewrites, int)
                    or not 0
                    <= duplicate_rewrites
                    <= MAXIMUM_DUPLICATE_REWRITE_ROUNDS
                ):
                    fallback_state_counts_valid = False
                    break
                latest_draft = draft_attempts[-1]
                if not isinstance(latest_draft, Mapping):
                    fallback_state_counts_valid = False
                    break
                layout_normalized_rows += int(
                    latest_draft.get("layout_normalized") is True
                )
                particle_normalized_rows += int(
                    latest_draft.get("particle_normalized") is True
                )
                duplicate_rewrite_rows += int(duplicate_rewrites > 0)
                duplicate_rewrite_attempts += duplicate_rewrites
                for attempt in [*draft_attempts, *review_attempts]:
                    if not isinstance(attempt, Mapping):
                        attempt_sequences_valid = False
                        continue
                    sequence = attempt.get("provider_call_sequence")
                    imported = attempt.get("imported_from_seed") is True
                    if imported:
                        attempt_sequences_valid = (
                            attempt_sequences_valid and sequence is None
                        )
                    elif _positive_call_sequence(sequence):
                        non_import_attempts += 1
                        call_sequences.add(sequence)
                    else:
                        attempt_sequences_valid = False
            provider_calls = state.get("provider_calls")
            fallback_state_counts_valid = bool(
                fallback_state_counts_valid
                and isinstance(provider_calls, int)
                and not isinstance(provider_calls, bool)
                and provider_calls >= 0
                and provider_calls <= non_import_attempts
                and attempt_sequences_valid
                and len(call_sequences) == provider_calls
                and (
                    provider_calls == 0
                    or (min(call_sequences) == 1 and max(call_sequences) == provider_calls)
                )
                and teacher_manifest.get("layout_normalized_rows")
                == layout_normalized_rows
                and teacher_manifest.get("particle_normalized_rows")
                == particle_normalized_rows
                and teacher_manifest.get("duplicate_rewrite_rows")
                == duplicate_rewrite_rows
                and teacher_manifest.get("duplicate_rewrite_attempts")
                == duplicate_rewrite_attempts
                and teacher_manifest.get("maximum_duplicate_rewrite_rounds")
                == MAXIMUM_DUPLICATE_REWRITE_ROUNDS
            )
    if fallback and (
        dict(sorted(drafters.items()))
        != teacher_manifest.get("assigned_teacher_roles")
        or dict(sorted(reviewers.items()))
        != teacher_manifest.get("assigned_review_roles")
        or dict(sorted(actual_drafters.items()))
        != teacher_manifest.get("actual_teacher_roles")
        or dict(sorted(actual_reviewers.items()))
        != teacher_manifest.get("actual_review_roles")
        or dict(sorted(review_modes.items())) != teacher_manifest.get("review_modes")
        or dict(sorted(actual_drafters.items()))
        != teacher_manifest.get("teacher_roles")
        or dict(sorted(actual_reviewers.items()))
        != teacher_manifest.get("review_roles")
        or teacher_manifest.get("selection_sha256")
        != sha256_bytes(canonical_json_bytes([row["id"] for row in specs]))
        or teacher_manifest.get("all_rows_cross_provider_reviewed")
        is not (review_modes == {CROSS_PROVIDER_REVIEW_MODE: len(candidates)})
        or not isinstance(teacher_manifest.get("seed_import"), Mapping)
        or inherited_seed_passes
        != teacher_manifest["seed_import"].get("cross_provider_passes_inherited")
        or not fallback_state_counts_valid
    ):
        raise Mix2KV4FinalizeError("teacher fallback provider 집계가 다릅니다.")
    return {
        "rows": len(candidates),
        "axes": dict(sorted(axes.items())),
        "drafters": dict(sorted(drafters.items())),
        "reviewers": dict(sorted(reviewers.items())),
        "actual_drafters": dict(sorted(actual_drafters.items())),
        "actual_reviewers": dict(sorted(actual_reviewers.items())),
        "review_modes": dict(sorted(review_modes.items())),
        "exact_duplicate_answers": exact_duplicates,
        "normalized_answer_multiplicity_maximum": normalized_maximum,
    }


def _stats(values: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        raise Mix2KV4FinalizeError("token 통계 대상이 비었습니다.")
    return {
        "minimum": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p90": ordered[math.ceil(len(ordered) * 0.9) - 1],
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "maximum": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 3),
    }


def _tokenize_row(tokenizer: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    messages = [
        *deepcopy(row["prompt"]),
        {"role": "assistant", "content": row["assistant"]},
    ]
    try:
        processed = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            add_generation_prompt=False,
        )
        rendered = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        direct_ids = tokenizer(rendered, add_special_tokens=False)["input_ids"]
        prefix_ids = tokenizer.apply_chat_template(
            row["prompt"], tokenize=True, add_generation_prompt=False
        )
    except Exception as exc:
        raise Mix2KV4FinalizeError(
            f"chat template tokenization이 실패했습니다: {row.get('id')}"
        ) from exc
    if not isinstance(processed, Mapping):
        raise Mix2KV4FinalizeError("tokenizer BatchEncoding이 Mapping이 아닙니다.")
    input_ids = processed.get("input_ids")
    assistant_masks = processed.get("assistant_masks")
    attention_mask = processed.get("attention_mask")
    if (
        not isinstance(input_ids, list)
        or not isinstance(assistant_masks, list)
        or not isinstance(attention_mask, list)
        or not isinstance(prefix_ids, list)
        or input_ids != direct_ids
        or len(input_ids) != len(assistant_masks)
        or len(input_ids) != len(attention_mask)
        or input_ids[: len(prefix_ids)] != prefix_ids
        or any(value not in {0, 1} for value in assistant_masks)
        or any(value != 1 for value in attention_mask)
    ):
        raise Mix2KV4FinalizeError(f"token·mask 계약이 다릅니다: {row.get('id')}")
    supervised = int(sum(assistant_masks))
    prompt_tokens = len(input_ids) - supervised
    eos_positions = [
        index
        for index, token_id in enumerate(input_ids)
        if token_id == tokenizer.eos_token_id
    ]
    leakage = int(sum(assistant_masks[: len(prefix_ids)]))
    if (
        supervised <= 0
        or leakage != 0
        or not eos_positions
        or assistant_masks[eos_positions[-1]] != 1
        or not any(
            token_id == tokenizer.eos_token_id and mask == 1
            for token_id, mask in zip(input_ids, assistant_masks, strict=True)
        )
    ):
        raise Mix2KV4FinalizeError(
            f"assistant-only loss·EOS 계약이 다릅니다: {row.get('id')}"
        )
    if prompt_tokens > MAX_PROMPT_TOKENS:
        raise Mix2KV4FinalizeError(f"입력 4K token 상한을 넘었습니다: {row.get('id')}")
    if supervised > MAX_COMPLETION_TOKENS:
        raise Mix2KV4FinalizeError(f"출력 4K token 상한을 넘었습니다: {row.get('id')}")
    return {
        "id": row["id"],
        "task_axis": row["task_axis"],
        "rendered_tokens": len(input_ids),
        "prompt_tokens": prompt_tokens,
        "supervised_assistant_tokens": supervised,
        "truncated": False,
        "assistant_mask_nonzero": True,
        "assistant_mask_sha256": sha256_bytes(bytes(assistant_masks)),
        "final_eos_supervised": True,
        "user_system_loss_leakage_tokens": leakage,
    }


def _token_audit(
    candidates: Sequence[dict[str, Any]],
    tokenizer_path: Path,
    config: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from transformers import AutoTokenizer
    except Exception as exc:
        raise Mix2KV4FinalizeError(
            "Transformers tokenizer import가 실패했습니다."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    expected_template = config["base_model"]["files"]["chat_template.jinja"]
    if (
        not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8")) != expected_template
    ):
        raise Mix2KV4FinalizeError("pinned Kanana chat template hash가 다릅니다.")
    rows = [_tokenize_row(tokenizer, row) for row in candidates]
    rendered = [row["rendered_tokens"] for row in rows]
    prompt = [row["prompt_tokens"] for row in rows]
    supervised = [row["supervised_assistant_tokens"] for row in rows]
    ladder = config["token_budget"]["training_selection_ladder"]
    selected = select_training_max_length(max(rendered), ladder)
    over_2048 = sum(value > 2048 for value in rendered)
    many_over_2048 = over_2048 > max(20, math.floor(len(rows) * 0.01))
    if selected is None:
        raise Mix2KV4FinalizeError("8,192 token 합산 상한을 넘는 행이 있습니다.")
    return rows, {
        "schema_version": "1.0.0",
        "rows": len(rows),
        "rendered_tokens": _stats(rendered),
        "prompt_tokens": _stats(prompt),
        "supervised_assistant_tokens": _stats(supervised),
        "rows_over_2048": over_2048,
        "rows_over_3584": sum(value > 3584 for value in rendered),
        "rows_over_4096": sum(value > 4096 for value in rendered),
        "rows_over_8192": sum(value > 8192 for value in rendered),
        "truncated_rows": 0,
        "zero_assistant_mask_rows": 0,
        "missing_supervised_eos_rows": 0,
        "user_system_loss_leakage_rows": 0,
        "selected_max_length": None if many_over_2048 else selected,
        "provisional_ladder_value": selected,
        "many_rows_over_2048": many_over_2048,
        "runtime_projection_review_required": many_over_2048,
        "training_blocked_pending_projection_review": many_over_2048,
    }


def finalize(
    *,
    config_path: Path,
    spec_build: Path,
    teacher_build: Path,
    tokenizer_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    for path, label in (
        (config_path, "config"),
        (spec_build, "spec build"),
        (teacher_build, "teacher build"),
        (tokenizer_path, "K0 model snapshot"),
        (output_root, "private output"),
    ):
        _reject_symlink_components(path, label)
    config = _load_config(config_path)
    model_files = _validate_model_snapshot(tokenizer_path, config)
    _, spec_manifest, specs = _validate_spec_build(spec_build, config_path)
    (
        teacher_manifest,
        _candidate_path,
        candidates,
        teacher_state,
        teacher_seed_state,
        teacher_input_hashes,
    ) = _teacher_inputs(teacher_build)
    if (
        teacher_manifest.get("spec_build_id") != spec_manifest["build_id"]
        or teacher_manifest.get("spec_build_sha256") != spec_manifest["build_sha256"]
    ):
        raise Mix2KV4FinalizeError("teacher와 spec build identity가 다릅니다.")
    fallback = (
        teacher_manifest.get("teacher_contract_mode")
        == CODEX_FALLBACK_EXECUTION_MODE
    )
    execution_policy_sha256 = None
    if fallback:
        try:
            _, execution = _load_execution_policy(
                CODEX_FALLBACK_POLICY_PATH, spec_manifest
            )
        except Mix2KV4TeacherError as exc:
            raise Mix2KV4FinalizeError(str(exc)) from exc
        if (
            execution.get("policy_id") != teacher_manifest.get("execution_policy_id")
            or execution.get("policy_sha256")
            != teacher_manifest.get("execution_policy_sha256")
            or execution.get("production_promotion_allowed") is not False
            or execution.get("lora_experimental_training_allowed") is not True
            or execution.get("seed_state_sha256")
            != teacher_manifest.get("seed_state_sha256")
            or execution.get("seed_cross_provider_passes")
            != teacher_manifest.get("seed_import", {}).get(
                "cross_provider_passes_inherited"
            )
        ):
            raise Mix2KV4FinalizeError("teacher fallback execution policy가 다릅니다.")
        if (
            not isinstance(teacher_state, Mapping)
            or teacher_state.get("schema_version") != "1.3.0"
            or teacher_state.get("dataset_version") != DATASET_VERSION
            or teacher_state.get("mode") != "full"
            or teacher_state.get("execution") != execution
            or teacher_state.get("runner_sha256")
            != teacher_manifest.get("runner_sha256")
            or teacher_state.get("contracts_sha256") != sha256_file(CONTRACTS_PATH)
            or teacher_state.get("config_sha256") != sha256_file(config_path)
            or teacher_state.get("spec_manifest_sha256")
            != sha256_file(spec_build / "build_manifest.json")
            or teacher_state.get("selection_order") != [row["id"] for row in specs]
            or set(teacher_state.get("records", {})) != {row["id"] for row in specs}
            or teacher_state.get("runtime_identity", {}).get(
                "configured_model_selector"
            )
            != config["teacher"]["codex_model"]
        ):
            raise Mix2KV4FinalizeError("teacher fallback state identity가 다릅니다.")
        execution_policy_sha256 = execution["policy_sha256"]
    candidate_validation = _validate_candidates(
        candidates,
        specs,
        config,
        teacher_manifest=teacher_manifest,
        state=teacher_state,
        seed_state=teacher_seed_state,
    )
    audits, audit_summary = _token_audit(candidates, tokenizer_path, config)
    projection_path = spec_build / "reports/full_runtime_projection_ab.json"
    projection = _load_json(projection_path, "full runtime projection report")
    if (
        sha256_file(projection_path)
        != spec_manifest.get("artifact_sha256", {}).get(
            "reports/full_runtime_projection_ab.json"
        )
        or projection.get("training_uses_full_runtime_snapshot") is not True
        or projection.get("compact_projection_used_for_training") is not False
    ):
        raise Mix2KV4FinalizeError("full runtime projection report 계약이 다릅니다.")
    training_rows = [
        {
            "schema_version": "1.0.0",
            "dataset_version": DATASET_VERSION,
            "id": row["id"],
            "task_axis": row["task_axis"],
            "messages": [
                *deepcopy(row["prompt"]),
                {"role": "assistant", "content": row["assistant"]},
            ],
            "assistant_only_loss": True,
            "runtime_snapshot_sha256": row["runtime_snapshot_sha256"],
            "restricted_local_only": False,
        }
        for row in candidates
    ]
    training_bytes = jsonl_bytes(training_rows)
    audits_bytes = jsonl_bytes(audits)
    summary_bytes = _json_bytes(
        {
            **audit_summary,
            "candidate_validation": candidate_validation,
            "full_runtime_vs_audit_projection": projection,
        }
    )
    teacher_governance = {
        "teacher_contract_mode": (
            CODEX_FALLBACK_EXECUTION_MODE if fallback else STRICT_EXECUTION_MODE
        ),
        "teacher_quality_tier": (
            CODEX_FALLBACK_EXECUTION_MODE
            if fallback
            else "cross_provider_verified"
        ),
        "cross_provider_teacher_contract_met": not fallback,
        "training_scope": "lora_experimental_only",
        "lora_experimental_training_allowed": True,
        "production_promotion_allowed": False,
        "execution_policy_sha256": execution_policy_sha256,
        "teacher_pipeline_state_sha256": (
            teacher_manifest["pipeline_state_sha256"] if fallback else None
        ),
    }
    identity = {
        "dataset_version": DATASET_VERSION,
        "config_sha256": sha256_file(config_path),
        "spec_build_sha256": spec_manifest["build_sha256"],
        "teacher_candidate_sha256": teacher_input_hashes[
            "teacher_candidate_sha256"
        ],
        "teacher_manifest_sha256": teacher_input_hashes[
            "teacher_manifest_sha256"
        ],
        "teacher_runner_sha256": sha256_file(TEACHER_RUNNER_PATH),
        "finalizer_sha256": sha256_file(FINALIZER_PATH),
        "contracts_sha256": sha256_file(CONTRACTS_PATH),
        "base_model_files": model_files,
        "training_rows_sha256": sha256_bytes(training_bytes),
        "token_audit_rows_sha256": sha256_bytes(audits_bytes),
        "token_audit_summary_sha256": sha256_bytes(summary_bytes),
        "teacher_governance": teacher_governance,
    }
    build_sha = sha256_bytes(canonical_json_bytes(identity))
    build_id = f"build-{build_sha[:12]}"
    manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "build_id": build_id,
        "build_sha256": build_sha,
        "identity": identity,
        "rows": EXPECTED_ROWS,
        "selected_max_length": audit_summary["selected_max_length"],
        "assistant_only_loss": True,
        "truncation": False,
        "full_runtime_snapshot_used": True,
        "compact_projection_used_for_training": False,
        "development_targets_accessed": False,
        "sealed_blind_accessed": False,
        "training_execution_allowed": not audit_summary[
            "training_blocked_pending_projection_review"
        ],
        "training_performed": False,
        "artifact_sha256": {
            "training/train_2000.jsonl": identity["training_rows_sha256"],
            "reports/token_audit_2000.jsonl": identity["token_audit_rows_sha256"],
            "reports/token_audit_summary.json": identity["token_audit_summary_sha256"],
        },
        **teacher_governance,
    }
    if fallback:
        manifest.update(
            {
                "schema_version": "1.1.0",
            }
        )
    files = {
        "training/train_2000.jsonl": training_bytes,
        "reports/token_audit_2000.jsonl": audits_bytes,
        "reports/token_audit_summary.json": summary_bytes,
        "build_manifest.json": _json_bytes(manifest),
    }
    target, mode = _atomic_build(output_root, build_id, files)
    return {
        **manifest,
        "mode": mode,
        "path": str(target),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIX2K v4 final token audit builder")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--spec-build", type=Path, required=True)
    parser.add_argument("--teacher-build", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = finalize(
            config_path=_absolute(args.config),
            spec_build=_absolute(args.spec_build),
            teacher_build=_absolute(args.teacher_build),
            tokenizer_path=_absolute(args.tokenizer),
            output_root=_absolute(args.output_root),
        )
    except (
        Mix2KV4FinalizeError,
        Mix2KV4ContractError,
        Mix2KV4TeacherError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["training_execution_allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
