# mix2k_v4_reviewed_repair.py - 외부 MIX2K 검토안을 부모 정본에 선별 재결합한다.

from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_build import (
    Mix2KV4BuildError,
    _validate_model_snapshot,
)
from scripts.data.mix2k_v4_build import (
    _load_config as _load_parent_config,
)
from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    EXPECTED_ROWS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    Mix2KV4ContractError,
    flatten_runtime_facts,
    jsonl_bytes,
    normalize_answer,
    sha256_bytes,
    sha256_file,
    validate_draft,
    validate_review,
    validate_specs,
)
from scripts.data.mix2k_v4_finalize import (
    Mix2KV4FinalizeError,
    _atomic_build,
    _token_audit,
)
from scripts.data.mix2k_v4_teachers import (
    Mix2KV4TeacherError,
    _auth_check,
    _draft_schema,
    _normalize_draft_answer_layout,
    _normalize_draft_answer_particles,
    _provider_call,
    _result_map,
    _review_schema,
    draft_prompt,
    review_prompt,
    subscription_environment,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.chart_day_adapter import (
    _missing_slots,
    _set_slot,
    empty_session_state,
)

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/mix2k-v4-reviewed-repair-v1.1.0.json"
)
DEFAULT_WORK_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/repair/v1.1.0"
)
DEFAULT_FINAL_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/final/v1.1.0"
)
SCRIPT_PATH = Path(__file__).resolve()
SOURCE_DEPENDENCIES = {
    "contracts": SCRIPT_PATH.with_name("mix2k_v4_contracts.py"),
    "parent_builder": SCRIPT_PATH.with_name("mix2k_v4_build.py"),
    "parent_finalizer": SCRIPT_PATH.with_name("mix2k_v4_finalize.py"),
    "teacher_runner": SCRIPT_PATH.with_name("mix2k_v4_teachers.py"),
    "chart_day_adapter": REPO_ROOT / "scripts/runtime/chart_day_adapter.py",
    "canonical_json": REPO_ROOT / "scripts/runtime/calculation/canonical.py",
}
REPAIR_AXES = {
    "intake_state_correction": 250,
    "uncertainty_blocked_boundary": 100,
    "hard_fact_short_qa": 50,
}
AMBIGUITY_COUNTS = {
    "birth_date_correction": 12,
    "target_date_change": 13,
    "actual_birth_time_correction": 5,
    "hypothetical_unknown_time_policy": 5,
}
AMBIGUITY_AXES = {
    "birth_date_correction": "intake_state_correction",
    "target_date_change": "intake_state_correction",
    "actual_birth_time_correction": "uncertainty_blocked_boundary",
    "hypothetical_unknown_time_policy": "uncertainty_blocked_boundary",
}
PACKAGE_REVIEW = "review/review_2000_with_lineage.jsonl"
PACKAGE_TRAIN = "training/train_2000.jsonl"
PACKAGE_MANIFEST = "build_manifest.json"
PACKAGE_SUMS = "SHA256SUMS.txt"
MAX_CONFIG_BYTES = 256 * 1024
MAX_PACKAGE_MEMBER_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_TOTAL_BYTES = 128 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 16 * 1024 * 1024
REPAIR_SHARD_ROWS = 10
MAXIMUM_REWRITE_ROUNDS = 4
FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
INTERNAL_ASSISTANT_LANGUAGE = re.compile(
    r"(?:학습\s*정답|(?<!일반\s)모델(?:이|은|을|의)?|Gold|canonical|"
    r"runtime|snapshot|capability|fact[_ -]?authority|전문가\s*검토|"
    r"(?:chart|period)\.|hidden_stems)",
    re.IGNORECASE,
)
ANSWER_HINT_WRAPPERS = (
    "근거가 된 값은 그대로 유지",
    "처음 보는 사람도 바로 이해할 수 있게",
    "입력에 없는 내용은 덧붙이지",
    "각 항목의 기준 시점을 섞지",
    "확인된 사실과 해석을 구분",
    "제한보다 결론을 우선",
)
PIPELINE_STATE_FIELDS = {
    "schema_version",
    "dataset_version",
    "artifact_revision",
    "target_id",
    "identity",
    "selection_order",
    "provider_calls",
    "provider_call_log",
    "records",
}
PIPELINE_RECORD_FIELDS = {
    "spec_sha256",
    "task_axis",
    "assigned_drafter",
    "assigned_reviewer",
    "status",
    "current_draft",
    "current_draft_provider",
    "feedback",
    "rewrites_used",
    "duplicate_rewrites_used",
    "draft_attempts",
    "review_attempts",
    "accepted",
}
DRAFT_ATTEMPT_FIELDS = {
    "provider_call_sequence",
    "provider",
    "started_at_utc",
    "elapsed_seconds",
    "provider_draft",
    "provider_draft_sha256",
    "normalized_draft_sha256",
    "draft",
    "layout_normalized",
    "particle_normalized",
    "deterministic_pass",
    "validation_error",
}
REVIEW_ATTEMPT_FIELDS = {
    "provider_call_sequence",
    "provider",
    "started_at_utc",
    "elapsed_seconds",
    "review",
    "review_sha256",
    "reviewed_draft_sha256",
}
PROVIDER_CALL_FIELDS = {
    "provider_call_sequence",
    "provider_scope",
    "provider",
    "kind",
    "record_ids",
    "started_at_utc",
    "elapsed_seconds",
    "prompt_sha256",
    "provider_output",
    "provider_output_sha256",
}
ACCEPTED_FIELDS = {"draft_provider", "review_provider", "draft", "review"}
FALSE_STATE_COMPLETION = re.compile(
    r"(?:저장|수정|계산|연결)(?:이|을|를)?\s*(?:완료|마쳤|끝냈)"
)
FALSE_STATE_COMPLETION_NEGATION = re.compile(
    r"^\s*(?:(?:되|하)지\s*(?:않|못)|(?:된|한)?\s*"
    r"(?:게|건|것이|것은|상태가|상태는|가|이|은|는)?\s*(?:아니|않|못))"
)


class Mix2KV4RepairError(RuntimeError):
    """외부 review intake·교차 teacher·정본화 계약 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _source_dependency_hashes() -> dict[str, str]:
    return {
        label: sha256_file(path) for label, path in sorted(SOURCE_DEPENDENCIES.items())
    }


def _load_bytes_snapshot(
    path: Path, label: str, *, maximum_bytes: int = MAX_STATE_BYTES
) -> bytes:
    """단일 file descriptor로 regular file bytes를 안전하게 고정한다."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise Mix2KV4RepairError(f"{label}이 없거나 안전하지 않습니다: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum_bytes:
            raise Mix2KV4RepairError(f"{label} 크기·형식이 안전하지 않습니다: {path}")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(payload) != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
        ):
            raise Mix2KV4RepairError(f"{label}이 읽는 동안 변경됐습니다: {path}")
    except OSError as exc:
        raise Mix2KV4RepairError(f"{label}을 읽지 못했습니다: {path}") from exc
    finally:
        os.close(descriptor)
    return payload


def _load_json_snapshot(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    payload = _load_bytes_snapshot(path, label)
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4RepairError(f"{label}을 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Mix2KV4RepairError(f"{label} 최상위는 object여야 합니다.")
    return value, payload


def _load_jsonl_snapshot(path: Path, label: str) -> tuple[list[dict[str, Any]], bytes]:
    payload = _load_bytes_snapshot(path, label)
    rows = _parse_jsonl_bytes(payload, label)
    if jsonl_bytes(rows) != payload:
        raise Mix2KV4RepairError(f"{label} JSONL canonical bytes가 다릅니다: {path}")
    return rows, payload


def _load_json(path: Path, label: str) -> dict[str, Any]:
    value, _payload = _load_json_snapshot(path, label)
    return value


def _load_parent_config_snapshot(path: Path, expected_sha256: Any) -> dict[str, Any]:
    payload = _load_bytes_snapshot(
        path, "부모 data config", maximum_bytes=MAX_CONFIG_BYTES
    )
    if sha256_bytes(payload) != expected_sha256:
        raise Mix2KV4RepairError("부모 data config SHA-256이 다릅니다.")
    temporary_root = Path(tempfile.mkdtemp(prefix="mix2k-v11-parent-config-"))
    temporary_root.chmod(PRIVATE_DIR_MODE)
    snapshot_path = temporary_root / "parent-config.json"
    try:
        descriptor = os.open(
            snapshot_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            PRIVATE_FILE_MODE,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return _load_parent_config(snapshot_path)
    except Exception as exc:
        if isinstance(exc, Mix2KV4RepairError):
            raise
        raise Mix2KV4RepairError("부모 data config 계약이 다릅니다.") from exc
    finally:
        shutil.rmtree(temporary_root)


def _load_repair_config(
    path: Path,
) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, str]]:
    config_payload = _load_bytes_snapshot(
        path, "reviewed repair config", maximum_bytes=MAX_CONFIG_BYTES
    )
    config = _parse_json_bytes(config_payload, "reviewed repair config")
    parent = config.get("parent")
    package = config.get("review_package")
    repair = config.get("repair")
    prompts = config.get("prompts")
    teacher = config.get("teacher")
    token = config.get("token_budget")
    serving = config.get("serving_candidate")
    governance = config.get("governance")
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("artifact_revision") != "v1.1.0"
        or config.get("dataset_version") != DATASET_VERSION
        or config.get("status") != "EXPERIMENT_CANDIDATE_NOT_PRODUCTION_APPROVED"
        or not all(
            isinstance(value, dict)
            for value in (
                parent,
                package,
                repair,
                prompts,
                teacher,
                token,
                serving,
                governance,
            )
        )
        or repair.get("rows") != EXPECTED_ROWS
        or repair.get("inherited_assistant_rows") != 1600
        or repair.get("regenerated_assistant_rows") != 400
        or repair.get("assistant_axes") != REPAIR_AXES
        or repair.get("selected_user_rewrites") != AMBIGUITY_COUNTS
        or repair.get("all_other_user_messages_inherited") is not True
        or any(
            repair.get(key) is not False
            for key in (
                "external_ids_allowed",
                "external_system_messages_allowed",
                "external_assistant_answers_are_gold",
                "supplement_blueprints_included",
            )
        )
        or teacher.get("changed_rows_per_direction") != 200
        or teacher.get("shard_rows") != 10
        or teacher.get("maximum_rewrite_rounds") != MAXIMUM_REWRITE_ROUNDS
        or teacher.get("cross_provider_pass_required_for_changed_rows") is not True
        or teacher.get("claude_model") != "sonnet"
        or teacher.get("codex_model") != "configured_subscription_default"
        or teacher.get("provider_fallback_may_draft_but_may_not_finalize") is not True
        or teacher.get("api_keys_allowed") is not False
        or token.get("training_selection_ladder") != [2048, 3584, 4096, 8192]
        or token.get("preferred_training_max_length") != 2048
        or token.get("max_input_tokens") != 4096
        or token.get("max_completion_tokens") != 4096
        or token.get("truncate") is not False
        or token.get("assistant_only_loss") is not True
        or serving.get("feature_enabled_by_default") is not False
        or serving.get("dashboard_version") != "v1.14.0"
        or serving.get("max_input_tokens") != 4096
        or serving.get("max_new_tokens") != 4096
        or serving.get("native_context_tokens_minimum") != 8192
        or serving.get("active_dashboard_changed") is not False
        or serving.get("relation_training_scope_expanded") is not False
        or governance.get("synthetic_public_rows_only") is not True
        or governance.get("aihub_content_allowed") is not False
        or governance.get("personal_data_allowed") is not False
        or governance.get("production_promotion_allowed") is not False
    ):
        raise Mix2KV4RepairError("reviewed repair 고정 계약이 다릅니다.")
    parent_config_path = REPO_ROOT / str(parent.get("config_path", ""))
    parent_config = _load_parent_config_snapshot(
        parent_config_path, parent.get("config_sha256")
    )
    prompt_texts: dict[str, str] = {}
    for key in ("bound", "intake"):
        prompt_path = REPO_ROOT / str(prompts.get(f"{key}_path", ""))
        prompt_payload = _load_bytes_snapshot(
            prompt_path, f"{key} prompt", maximum_bytes=MAX_CONFIG_BYTES
        )
        if sha256_bytes(prompt_payload) != prompts.get(f"{key}_sha256"):
            raise Mix2KV4RepairError(f"{key} prompt SHA-256이 다릅니다.")
        try:
            prompt_texts[key] = prompt_payload.decode("utf-8").strip()
        except UnicodeError as exc:
            raise Mix2KV4RepairError(f"{key} prompt UTF-8이 잘못됐습니다.") from exc
        if not prompt_texts[key]:
            raise Mix2KV4RepairError(f"{key} prompt가 비었습니다.")
    return config, parent_config, config_payload, prompt_texts


def _safe_member_name(name: str, root: str) -> bool:
    path = PurePosixPath(name)
    return bool(
        name
        and "\\" not in name
        and not path.is_absolute()
        and ".." not in path.parts
        and path.parts
        and path.parts[0] == root
    )


def _zip_member_bytes(archive: zipfile.ZipFile, root: str, relative: str) -> bytes:
    name = f"{root}/{relative}"
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise Mix2KV4RepairError(f"ZIP 필수 member가 없습니다: {relative}") from exc
    if info.is_dir() or info.file_size > MAX_PACKAGE_MEMBER_BYTES:
        raise Mix2KV4RepairError(
            f"ZIP member 크기·종류가 안전하지 않습니다: {relative}"
        )
    value = archive.read(info)
    if len(value) != info.file_size:
        raise Mix2KV4RepairError(f"ZIP member 읽기 크기가 다릅니다: {relative}")
    return value


def _parse_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4RepairError(f"{label} JSON이 잘못됐습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4RepairError(f"{label} 최상위는 object여야 합니다.")
    return value


def _parse_jsonl_bytes(payload: bytes, label: str) -> list[dict[str, Any]]:
    try:
        lines = payload.decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4RepairError(f"{label} JSONL이 잘못됐습니다.") from exc
    if len(lines) != len(rows) or any(not isinstance(row, dict) for row in rows):
        raise Mix2KV4RepairError(f"{label} JSONL에 빈 행 또는 비-object가 있습니다.")
    return rows


def _load_review_package_snapshot(
    package_path: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """동일 ZIP snapshot bytes로 identity 감사와 row 파싱을 함께 수행한다."""

    contract = config["review_package"]
    if package_path.name != contract["expected_filename"]:
        raise Mix2KV4RepairError("외부 review ZIP identity가 다릅니다.")
    package_payload = _load_bytes_snapshot(
        package_path,
        "외부 review ZIP",
        maximum_bytes=MAX_PACKAGE_TOTAL_BYTES,
    )
    if (
        len(package_payload) != contract["bytes"]
        or sha256_bytes(package_payload) != contract["sha256"]
    ):
        raise Mix2KV4RepairError("외부 review ZIP identity가 다릅니다.")
    root = str(contract["root"])
    try:
        with zipfile.ZipFile(io.BytesIO(package_payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            total = sum(info.file_size for info in infos)
            if (
                len(infos) != contract["members"]
                or len(names) != len(set(names))
                or total != contract["uncompressed_bytes"]
                or total > MAX_PACKAGE_TOTAL_BYTES
                or any(not _safe_member_name(name, root) for name in names)
                or any(info.flag_bits & 0x1 for info in infos)
                or any(
                    stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF) for info in infos
                )
                or archive.testzip() is not None
            ):
                raise Mix2KV4RepairError("외부 review ZIP 구조·CRC 계약이 다릅니다.")
            sums = _zip_member_bytes(archive, root, PACKAGE_SUMS).decode("utf-8")
            verified = 0
            for number, line in enumerate(sums.splitlines(), 1):
                parts = line.split(maxsplit=1)
                if len(parts) != 2 or FULL_SHA256.fullmatch(parts[0]) is None:
                    raise Mix2KV4RepairError(f"SHA256SUMS 형식 오류: {number}")
                relative = parts[1].lstrip("* ")
                while relative.startswith("./"):
                    relative = relative[2:]
                if not relative or not _safe_member_name(f"{root}/{relative}", root):
                    raise Mix2KV4RepairError(f"SHA256SUMS member 경로 오류: {number}")
                if relative == PACKAGE_SUMS:
                    raise Mix2KV4RepairError(
                        "SHA256SUMS 자기 참조는 허용하지 않습니다."
                    )
                payload = _zip_member_bytes(archive, root, relative)
                if sha256_bytes(payload) != parts[0]:
                    raise Mix2KV4RepairError(f"ZIP 내부 SHA-256이 다릅니다: {relative}")
                verified += 1
            manifest = _parse_json_bytes(
                _zip_member_bytes(archive, root, PACKAGE_MANIFEST), PACKAGE_MANIFEST
            )
            review_payload = _zip_member_bytes(archive, root, PACKAGE_REVIEW)
            train_payload = _zip_member_bytes(archive, root, PACKAGE_TRAIN)
    except (OSError, zipfile.BadZipFile, UnicodeError) as exc:
        if isinstance(exc, Mix2KV4RepairError):
            raise
        raise Mix2KV4RepairError("외부 review ZIP을 안전하게 읽지 못했습니다.") from exc
    if (
        verified != 40
        or manifest.get("parent", {}).get("canonical_v1_0_1_train_sha256")
        != config["parent"]["train_sha256"]
        or manifest.get("parent", {}).get("uploaded_zip_sha256")
        != contract["declared_parent_upload_sha256"]
        or manifest.get("output", {}).get("review_sha256")
        != contract["review_rows_sha256"]
        or manifest.get("output", {}).get("training_sha256")
        != contract["candidate_train_sha256"]
        or sha256_bytes(review_payload) != contract["review_rows_sha256"]
        or sha256_bytes(train_payload) != contract["candidate_train_sha256"]
    ):
        raise Mix2KV4RepairError("외부 review manifest·payload hash 계약이 다릅니다.")
    report = {
        "schema_version": "1.0.0",
        "package_sha256": contract["sha256"],
        "package_bytes": contract["bytes"],
        "members": len(names),
        "uncompressed_bytes": total,
        "internal_checksums_verified": verified,
        "declared_parent_upload_sha256": contract["declared_parent_upload_sha256"],
        "declared_parent_upload_verified": False,
        "candidate_direct_adoption_allowed": False,
        "crc_passed": True,
        "unsafe_members": 0,
    }
    return (
        report,
        _parse_jsonl_bytes(review_payload, PACKAGE_REVIEW),
        _parse_jsonl_bytes(train_payload, PACKAGE_TRAIN),
    )


def audit_package(package_path: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    report, _review, _training = _load_review_package_snapshot(package_path, config)
    return report


AMBIGUITY_PROMPTS = {
    "birth_date_correction": [
        "조회 날짜가 아니라 제 생년월일을 정정하려고 해요. 기존 원국은 어떻게 처리해야 하나요?",
        "태어난 날짜를 잘못 입력했어요. 새 생년월일로 고치면 이전 계산은 계속 쓸 수 있나요?",
        "운세를 볼 날짜가 아니라 출생일 자체를 바꾸려는 거예요. 무엇을 다시 계산해야 해요?",
        "제 생년월일 입력이 틀렸어요. 이전 원국과 날짜 결과는 어떻게 해야 하나요?",
        "출생일을 최신 정보로 바로잡고 싶어요. 기존 결과부터 다시 써도 되나요?",
        "태어난 날짜를 정정하면 원국도 새로 받아야 하나요?",
        "출생 생년월일을 수정하려고 해요. 현재 대화의 계산 결과는 유효한가요?",
        "제가 처음 말한 생년월일이 잘못됐어요. 정정 뒤 어떤 계산이 다시 필요한가요?",
        "선택 날짜가 아니라 제 출생 날짜를 고칠게요. 이전 원국을 유지해도 돼요?",
        "생년월일 오입력을 발견했어요. 새 값으로 바꾸면 기존 일진 결과도 다시 봐야 하나요?",
        "출생일을 잘못 적었는데 지금 고치면 어떤 결과가 무효가 되나요?",
        "제 생년월일 정정이 필요해요. 이전 계산을 그대로 두지 않으려면 다음에 무엇을 해야 해요?",
    ],
    "target_date_change": [
        "출생일은 그대로 두고 오늘 흐름을 볼 날짜만 바꾸고 싶어요. 무엇만 갱신하면 되나요?",
        "원국은 그대로인데 조회 날짜를 다른 날로 바꾸려 해요. 원국도 다시 계산해야 하나요?",
        "태어난 날짜가 아니라 운세 대상 날짜만 변경할게요. 다음 단계가 뭐예요?",
        "현재 원국으로 다른 날짜의 흐름을 보고 싶어요. 날짜 결과만 새로 받으면 되나요?",
        "선택한 날짜만 바꾸려는데 기존 원국은 유지해도 돼요?",
        "오늘 대신 다른 하루를 보고 싶어요. 출생정보부터 다시 넣어야 하나요?",
        "조회 중인 날짜를 변경할게요. 원국과 날짜 결과 중 무엇을 다시 계산하나요?",
        "같은 원국으로 대상 날짜만 새로 고르고 싶어요. 어떻게 이어가면 돼요?",
        "출생 원국은 맞고 운세 날짜만 틀렸어요. 어느 결과만 바꾸면 되나요?",
        "다른 날짜의 흐름으로 전환하려 해요. 원국 재계산도 필요한가요?",
        "보고 있던 하루만 다른 날로 바꿀게요. 기존 원국 대화를 계속 써도 되나요?",
        "원국은 수정하지 않고 선택 날짜만 바꾸고 싶어요. 무엇을 새로 요청해야 해요?",
        "대상 날짜를 다시 선택했어요. 이전 날짜 결과만 교체하면 되나요?",
    ],
    "actual_birth_time_correction": [
        "기존에는 시간을 입력했지만 실제로는 출생시간을 몰라요. 현재 시주는 어떻게 처리해야 하나요?",
        "제 출생시간을 미상으로 정정할게요. 이전 시주와 원국을 그대로 써도 되나요?",
        "태어난 시간을 안다고 했던 게 잘못이에요. 시간 미상으로 바꾸면 무엇을 다시 계산해야 해요?",
        "실제 최신 정보는 출생시간 미상이에요. 기존 시간 기준 결과를 계속 사용하면 안 되죠?",
        "출생시간을 모르는 것으로 수정하려고 해요. 이전 시주를 해석하기 전에 무엇을 해야 하나요?",
    ],
    "hypothetical_unknown_time_policy": [
        "현재 원국은 바꾸지 않고 일반적으로 출생시간을 모를 때 어떻게 처리하는지 알려줘.",
        "제 정보 정정이 아니라 가정 질문이에요. 생시 미상이면 어느 사실까지 확인할 수 있나요?",
        "지금 계산 결과는 그대로 두고, 출생시간이 없는 경우의 원칙만 설명해줘.",
        "일반적으로 태어난 시간을 모르면 대표 시각을 하나 정해도 되는지 궁금해요.",
        "현재 시주를 수정하려는 건 아니에요. 시간 미상 사용자는 결과를 어떻게 받아야 하나요?",
    ],
}
AMBIGUITY_BY_PROMPT = {
    prompt: ambiguity
    for ambiguity, prompts in AMBIGUITY_PROMPTS.items()
    for prompt in prompts
}


def _set_full_slots(state: dict[str, Any]) -> None:
    state["saju_opt_in"] = True
    state["current_intent"] = "chart"
    state["state_revision"] += 1
    for field, value in (
        ("calendar", "solar"),
        ("birth_date", "1988-07-14"),
        ("birth_time", "13:30"),
        (
            "birthplace",
            {"country_code": "KR", "city": "서울", "timezone": "Asia/Seoul"},
        ),
    ):
        _set_slot(state, field, value)


def _projection(
    original_question: str, ambiguity: str | None, local_index: int
) -> dict[str, Any]:
    state = empty_session_state()
    chart_status = "missing"
    period_status = "missing"
    last_tool_status = None
    action = "request_slots"
    reason = "BIRTH_SLOTS_REQUIRED"

    if original_question.startswith("생년월일은 말했는데"):
        state["saju_opt_in"] = True
        state["current_intent"] = "chart"
        state["state_revision"] += 1
        _set_slot(state, "birth_date", "1988-07-14")
        state["birth_slots"]["time_precision"] = "unknown"
        state["state_revision"] += 1
    elif original_question.startswith("양력이 아니라 음력이야"):
        _set_full_slots(state)
        state["chart"] = {"status": "ok"}
        state["period"] = {"status": "ok"}
        _set_slot(state, "calendar", "lunar")
    elif original_question.startswith("출생지는 서울이 아니라 부산"):
        _set_full_slots(state)
        state["chart"] = {"status": "ok"}
        state["period"] = {"status": "ok"}
        _set_slot(
            state,
            "birthplace",
            {"country_code": "KR", "city": "부산", "timezone": "Asia/Seoul"},
        )
        action, reason = "request_chart", "CHART_REQUEST_REQUIRED"
    elif original_question.startswith("아직 원국을 계산하지 않았는데"):
        _set_full_slots(state)
        state["current_intent"] = "period"
        action, reason = "request_chart", "EXACT_CHART_REQUIRED"
    elif original_question.startswith("이미 원국과 날짜를 연결했는데"):
        _set_full_slots(state)
        state["chart"] = {"status": "ok"}
        state["period"] = {"status": "ok"}
        chart_status = period_status = "valid"
        action, reason = "render_period", None
    elif original_question.startswith("날짜를 바꾸고 싶으면"):
        _set_full_slots(state)
        state["chart"] = {"status": "ok"}
        state["period"] = {"status": "ok"}
        if ambiguity == "birth_date_correction":
            _set_slot(
                state,
                "birth_date",
                f"1988-07-{15 + (local_index % 10):02d}",
            )
            action, reason = "request_chart", "CHART_REQUEST_REQUIRED"
        elif ambiguity == "target_date_change":
            state["period"] = None
            state["state_revision"] += 1
            chart_status = "valid"
            action, reason = "request_period", "PERIOD_REQUEST_REQUIRED"
    elif original_question.startswith("출생시간 범위만"):
        _set_full_slots(state)
        _set_slot(state, "time_range", {"start": "12:00", "end": "14:00"})
        action, reason = "request_chart", "CHART_REQUEST_REQUIRED"
    elif original_question.startswith("사주를 원하지 않으니"):
        action, reason = "general_chat", None
    elif original_question.startswith("계산이 차단됐다고"):
        _set_full_slots(state)
        last_tool_status = "blocked"
        action, reason = "explain_blocked", "CALCULATION_BLOCKED"
    elif original_question.startswith("입력한 정보를 정정했으니"):
        _set_full_slots(state)
        state["chart"] = {"status": "ok"}
        state["period"] = {"status": "ok"}
        _set_slot(state, "birth_time", "14:10")
        action, reason = "request_chart", "CHART_REQUEST_REQUIRED"
    else:
        state["saju_opt_in"] = True
        state["current_intent"] = "chart"

    if state.get("chart") is not None and chart_status == "missing":
        chart_status = "valid"
    if state.get("period") is not None and period_status == "missing":
        period_status = "valid"
    if chart_status != "valid" and original_question.startswith(
        ("양력이 아니라", "출생지는", "날짜를 바꾸고", "입력한 정보를")
    ):
        chart_status = "invalidated"
    if period_status != "valid" and original_question.startswith(
        ("양력이 아니라", "출생지는", "날짜를 바꾸고", "입력한 정보를")
    ):
        period_status = "invalidated"

    slots = deepcopy(state["birth_slots"])
    confirmed = sorted(key for key, value in slots.items() if value is not None)
    explicit_unknown = (
        ["birth_time"] if slots.get("time_precision") == "unknown" else []
    )
    missing = _missing_slots(state) if state["saju_opt_in"] else []
    return {
        "schema_version": "saju-intake-model-projection-v1.0.0",
        "state_revision": state["state_revision"],
        "saju_opt_in": state["saju_opt_in"],
        "current_intent": state["current_intent"],
        "birth_slots": slots,
        "confirmed_fields": confirmed,
        "explicit_unknown_fields": explicit_unknown,
        "missing_fields": missing,
        "chart_status": chart_status,
        "period_status": period_status,
        "last_tool_status": last_tool_status,
        "next_decision": {"action": action, "reason_code": reason},
    }


def _projection_evidence(
    projection: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    flattened = flatten_runtime_facts(projection, "intake_state")
    return (
        [path for path, _ in flattened],
        [value for _, value in flattened],
    )


def _prompt_with_projection(
    intake_prompt: str,
    projection: Mapping[str, Any],
) -> str:
    return (
        intake_prompt
        + "\n\n[앱의 구조화 입력 상태]\n"
        + json.dumps(
            projection, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    )


def _validate_parent_inputs(
    *,
    config: Mapping[str, Any],
    parent_spec_build: Path,
    parent_final_build: Path,
    parent_teacher_build: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bytes,
]:
    parent = config["parent"]
    paths = {
        "spec_manifest": parent_spec_build / "build_manifest.json",
        "specs": parent_spec_build / "training/specs_2000.jsonl",
        "dev": parent_spec_build / "evaluation/dev_cases_200.jsonl",
        "final_manifest": parent_final_build / "build_manifest.json",
        "train": parent_final_build / "training/train_2000.jsonl",
        "token_audit": parent_final_build / "reports/token_audit_2000.jsonl",
        "teacher_manifest": parent_teacher_build / "teacher_manifest.json",
        "teacher_candidates": parent_teacher_build
        / "accepted/training_candidates_2000.jsonl",
    }
    expected = {
        "spec_manifest": parent["spec_manifest_sha256"],
        "specs": parent["specs_sha256"],
        "dev": parent["dev_sha256"],
        "final_manifest": parent["final_manifest_sha256"],
        "train": parent["train_sha256"],
        "token_audit": parent["token_audit_sha256"],
        "teacher_manifest": parent["teacher_manifest_sha256"],
        "teacher_candidates": parent["teacher_candidates_sha256"],
    }
    spec_manifest, spec_manifest_payload = _load_json_snapshot(
        paths["spec_manifest"], "부모 spec manifest"
    )
    final_manifest, final_manifest_payload = _load_json_snapshot(
        paths["final_manifest"], "부모 final manifest"
    )
    teacher_manifest, teacher_manifest_payload = _load_json_snapshot(
        paths["teacher_manifest"], "부모 teacher manifest"
    )
    specs, specs_payload = _load_jsonl_snapshot(paths["specs"], "부모 specs")
    dev, dev_payload = _load_jsonl_snapshot(paths["dev"], "부모 dev")
    train, train_payload = _load_jsonl_snapshot(paths["train"], "부모 train")
    candidates, candidates_payload = _load_jsonl_snapshot(
        paths["teacher_candidates"], "부모 teacher candidates"
    )
    audits, audits_payload = _load_jsonl_snapshot(
        paths["token_audit"], "부모 token audit"
    )
    payloads = {
        "spec_manifest": spec_manifest_payload,
        "specs": specs_payload,
        "dev": dev_payload,
        "final_manifest": final_manifest_payload,
        "train": train_payload,
        "token_audit": audits_payload,
        "teacher_manifest": teacher_manifest_payload,
        "teacher_candidates": candidates_payload,
    }
    for label, digest in expected.items():
        if sha256_bytes(payloads[label]) != digest:
            raise Mix2KV4RepairError(f"부모 {label} identity가 다릅니다.")
    if (
        spec_manifest.get("build_id") != parent["spec_build_id"]
        or spec_manifest.get("build_sha256") != parent["spec_build_sha256"]
        or final_manifest.get("build_id") != parent["final_build_id"]
        or final_manifest.get("build_sha256") != parent["final_build_sha256"]
        or final_manifest.get("artifact_sha256", {}).get("training/train_2000.jsonl")
        != parent["train_sha256"]
        or final_manifest.get("artifact_sha256", {}).get(
            "reports/token_audit_2000.jsonl"
        )
        != parent["token_audit_sha256"]
        or teacher_manifest.get("candidate_sha256")
        != parent["teacher_candidates_sha256"]
    ):
        raise Mix2KV4RepairError("부모 build hash chain이 다릅니다.")
    if not (
        len(specs) == len(train) == len(candidates) == len(audits) == EXPECTED_ROWS
        and len(dev) == 200
    ):
        raise Mix2KV4RepairError("부모 2K artifact 행 수가 다릅니다.")
    return specs, train, candidates, audits, dev_payload


def _lineage_and_specs(
    *,
    config: Mapping[str, Any],
    parent_config: Mapping[str, Any],
    package_review: Sequence[dict[str, Any]],
    package_training: Sequence[dict[str, Any]],
    parent_specs: Sequence[dict[str, Any]],
    parent_train: Sequence[dict[str, Any]],
    parent_candidates: Sequence[dict[str, Any]],
    parent_audits: Sequence[dict[str, Any]],
    intake_prompt: str,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    specs_by_id = {row["id"]: row for row in parent_specs}
    train_by_id = {row["id"]: row for row in parent_train}
    candidates_by_id = {row["id"]: row for row in parent_candidates}
    audits_by_id = {row["id"]: row for row in parent_audits}
    external_train_by_id = {row["id"]: row for row in package_training}
    review_by_parent: dict[str, dict[str, Any]] = {}
    for row in package_review:
        parent_id = row.get("original_parent_id")
        external_id = row.get("id")
        if (
            not isinstance(parent_id, str)
            or parent_id in review_by_parent
            or not isinstance(external_id, str)
            or external_id not in external_train_by_id
        ):
            raise Mix2KV4RepairError("외부 review parent/final ID 집합이 다릅니다.")
        review_by_parent[parent_id] = row
    expected_ids = [row["id"] for row in parent_specs]
    if (
        len(specs_by_id) != EXPECTED_ROWS
        or len(train_by_id) != EXPECTED_ROWS
        or len(candidates_by_id) != EXPECTED_ROWS
        or len(audits_by_id) != EXPECTED_ROWS
        or set(review_by_parent) != set(expected_ids)
        or len(external_train_by_id) != EXPECTED_ROWS
    ):
        raise Mix2KV4RepairError("부모·외부 2K ID 대응이 완전하지 않습니다.")

    ambiguity_seen: Counter[str] = Counter()
    axis_seen: Counter[str] = Counter()
    external_assistant_changes: Counter[str] = Counter()
    new_specs: list[dict[str, Any]] = []
    repair_seeds: list[dict[str, Any]] = []

    for position, record_id in enumerate(expected_ids):
        spec = specs_by_id[record_id]
        train = train_by_id[record_id]
        candidate = candidates_by_id[record_id]
        audit = audits_by_id[record_id]
        review = review_by_parent[record_id]
        external = external_train_by_id[review["id"]]
        axis = spec["task_axis"]
        expected_parent_spec = {
            key: spec[key]
            for key in (
                "template_family",
                "allowed_fact_paths",
                "allowed_fact_values",
                "response_contract",
            )
        }
        axis_seen[axis] += 1
        parent_messages = train.get("messages")
        if (
            train.get("dataset_version") != DATASET_VERSION
            or candidate.get("prompt") != spec.get("prompt")
            or candidate.get("assistant") != parent_messages[-1].get("content")
            or parent_messages[:-1] != spec.get("prompt")
            or review.get("task_axis") != axis
            or review.get("parent_spec") != expected_parent_spec
            or review.get("parent_teacher") != candidate.get("teacher")
            or review.get("parent_token_audit") != audit
            or review.get("original_assistant") != candidate.get("assistant")
            or review.get("original_user_messages")
            != [m["content"] for m in spec["prompt"] if m["role"] == "user"]
            or external.get("task_axis") != axis
            or external.get("messages")[-1].get("content")
            != review.get("final_assistant")
            or sha256_bytes(canonical_json_bytes(external.get("messages")))
            != review.get("messages_sha256")
            or external.get("runtime_snapshot_sha256")
            != train.get("runtime_snapshot_sha256")
            or external.get("restricted_local_only") is not False
            or review.get("new_saju_fact_calculated") is not False
        ):
            raise Mix2KV4RepairError(
                f"외부 lineage가 부모 정본과 다릅니다: {record_id}"
            )
        ambiguity = review.get("ambiguity_resolution")
        if ambiguity is not None:
            if ambiguity not in AMBIGUITY_COUNTS:
                raise Mix2KV4RepairError(
                    f"알 수 없는 ambiguity label입니다: {ambiguity}"
                )
            if axis != AMBIGUITY_AXES[ambiguity]:
                raise Mix2KV4RepairError(
                    f"ambiguity와 task axis가 다릅니다: {record_id}"
                )
            ambiguity_seen[ambiguity] += 1
        assistant_changed = review.get("assistant_changed_from_original") is True
        external_assistant_changes[axis] += int(assistant_changed)

        revised = deepcopy(spec)
        original_question = revised["prompt"][-1]["content"]
        if ambiguity is not None:
            variants = AMBIGUITY_PROMPTS[ambiguity]
            revised["prompt"][-1]["content"] = variants[ambiguity_seen[ambiguity] - 1]
        if axis == "intake_state_correction" or (
            axis == "uncertainty_blocked_boundary"
            and revised["runtime_binding"] is None
        ):
            projection = _projection(original_question, ambiguity, position)
            revised["prompt"][0]["content"] = _prompt_with_projection(
                intake_prompt, projection
            )
            (
                revised["allowed_fact_paths"],
                revised["allowed_fact_values"],
            ) = _projection_evidence(projection)
        new_specs.append(revised)

        if axis in REPAIR_AXES:
            repair_seeds.append(
                {
                    "schema_version": "1.0.0",
                    "record_id": record_id,
                    "task_axis": axis,
                    "ambiguity_resolution": ambiguity,
                    "external_answer": review["final_assistant"],
                    "external_answer_sha256": sha256_bytes(
                        review["final_assistant"].encode("utf-8")
                    ),
                    "external_answer_is_gold": False,
                    "assigned_drafter": revised["drafter"],
                    "assigned_reviewer": revised["reviewer"],
                }
            )

    if (
        dict(axis_seen) != EXPECTED_AXES
        or dict(ambiguity_seen) != AMBIGUITY_COUNTS
        or {key: value for key, value in external_assistant_changes.items() if value}
        != REPAIR_AXES
        or len(repair_seeds) != 400
        or Counter(row["assigned_drafter"] for row in repair_seeds)
        != {"claude": 200, "codex": 200}
    ):
        raise Mix2KV4RepairError("외부 repair 분포가 고정 계약과 다릅니다.")
    try:
        validation = validate_specs(new_specs, parent_config)
    except Mix2KV4ContractError as exc:
        raise Mix2KV4RepairError(str(exc)) from exc

    unchanged_user_rows = 0
    hint_rows = 0
    for old, new in zip(parent_specs, new_specs, strict=True):
        old_users = [m["content"] for m in old["prompt"] if m["role"] == "user"]
        new_users = [m["content"] for m in new["prompt"] if m["role"] == "user"]
        unchanged_user_rows += int(old_users == new_users)
        hint_rows += int(
            any(
                wrapper in content
                for wrapper in ANSWER_HINT_WRAPPERS
                for content in new_users
            )
        )
    if (
        unchanged_user_rows != EXPECTED_ROWS - sum(AMBIGUITY_COUNTS.values())
        or hint_rows
    ):
        raise Mix2KV4RepairError("선별 user rewrite 또는 answer-hint 계약이 다릅니다.")
    return (
        new_specs,
        repair_seeds,
        {
            "rows": EXPECTED_ROWS,
            "axes": dict(sorted(axis_seen.items())),
            "stable_parent_ids": EXPECTED_ROWS,
            "external_ids_adopted": 0,
            "runtime_snapshot_hashes_changed": 0,
            "selected_user_rewrites": dict(sorted(ambiguity_seen.items())),
            "selected_user_rewrite_axes": dict(sorted(AMBIGUITY_AXES.items())),
            "inherited_user_rows": unchanged_user_rows,
            "answer_hint_wrapper_rows": hint_rows,
            "external_assistant_seed_rows": len(repair_seeds),
            "external_assistant_seed_is_gold": False,
            "spec_validation": validation,
        },
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    path.parent.chmod(PRIVATE_DIR_MODE)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(PRIVATE_FILE_MODE)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _inherited_answer_reservations(
    *,
    specs: Sequence[Mapping[str, Any]],
    parent_candidates: Sequence[Mapping[str, Any]],
    parent_config: Mapping[str, Any],
) -> dict[str, Any]:
    """상속 1,600행의 답변 지문을 새 400행 중복 예약 집합으로 고정한다."""

    inherited_ids = [
        str(spec["id"]) for spec in specs if spec.get("task_axis") not in REPAIR_AXES
    ]
    candidates_by_id = {row.get("id"): row for row in parent_candidates}
    exact: Counter[str] = Counter()
    normalized: Counter[str] = Counter()
    for record_id in inherited_ids:
        candidate = candidates_by_id.get(record_id)
        answer = candidate.get("assistant") if isinstance(candidate, Mapping) else None
        if not isinstance(answer, str) or not answer.strip():
            raise Mix2KV4RepairError(
                f"상속 답변 중복 예약 원본이 비었습니다: {record_id}"
            )
        answer = answer.strip()
        exact[sha256_bytes(answer.encode("utf-8"))] += 1
        normalized[sha256_bytes(normalize_answer(answer).encode("utf-8"))] += 1
    limits = parent_config["diversity"]
    value = {
        "schema_version": "1.0.0",
        "rows": len(inherited_ids),
        "record_ids_sha256": sha256_bytes(canonical_json_bytes(inherited_ids)),
        "exact_duplicate_answers_maximum": limits["exact_duplicate_answers_maximum"],
        "normalized_answer_multiplicity_maximum": limits[
            "normalized_answer_multiplicity_maximum"
        ],
        "exact_sha256_counts": dict(sorted(exact.items())),
        "normalized_sha256_counts": dict(sorted(normalized.items())),
    }
    _validate_inherited_answer_reservations(value, specs)
    return value


def _validate_inherited_answer_reservations(
    value: Mapping[str, Any], specs: Sequence[Mapping[str, Any]]
) -> tuple[Counter[str], Counter[str]]:
    expected_ids = [
        str(spec["id"]) for spec in specs if spec.get("task_axis") not in REPAIR_AXES
    ]
    exact = value.get("exact_sha256_counts")
    normalized = value.get("normalized_sha256_counts")

    def valid_counts(counts: Any) -> bool:
        return isinstance(counts, Mapping) and all(
            isinstance(digest, str)
            and FULL_SHA256.fullmatch(digest) is not None
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0
            for digest, count in counts.items()
        )

    if (
        set(value)
        != {
            "schema_version",
            "rows",
            "record_ids_sha256",
            "exact_duplicate_answers_maximum",
            "normalized_answer_multiplicity_maximum",
            "exact_sha256_counts",
            "normalized_sha256_counts",
        }
        or value.get("schema_version") != "1.0.0"
        or value.get("rows") != 1600
        or len(expected_ids) != 1600
        or value.get("record_ids_sha256")
        != sha256_bytes(canonical_json_bytes(expected_ids))
        or value.get("exact_duplicate_answers_maximum") != 0
        or value.get("normalized_answer_multiplicity_maximum") != 2
        or not valid_counts(exact)
        or not valid_counts(normalized)
    ):
        raise Mix2KV4RepairError("상속 답변 중복 예약 계약이 다릅니다.")
    exact_counter = Counter(exact)
    normalized_counter = Counter(normalized)
    if (
        sum(exact_counter.values()) != 1600
        or sum(normalized_counter.values()) != 1600
        or sum(count - 1 for count in exact_counter.values() if count > 1) != 0
        or max(normalized_counter.values(), default=0) > 2
    ):
        raise Mix2KV4RepairError("상속 답변 중복 예약 분포가 다릅니다.")
    return exact_counter, normalized_counter


def _new_pipeline_state(
    *, target_id: str, identity: Mapping[str, Any], specs: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    changed = [row for row in specs if row["task_axis"] in REPAIR_AXES]
    return {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "target_id": target_id,
        "identity": deepcopy(dict(identity)),
        "selection_order": [row["id"] for row in changed],
        "provider_calls": 0,
        "provider_call_log": [],
        "records": {
            row["id"]: {
                "spec_sha256": sha256_bytes(canonical_json_bytes(row)),
                "task_axis": row["task_axis"],
                "assigned_drafter": row["drafter"],
                "assigned_reviewer": row["reviewer"],
                "status": "needs_draft",
                "current_draft": None,
                "current_draft_provider": None,
                "feedback": "",
                "rewrites_used": 0,
                "duplicate_rewrites_used": 0,
                "draft_attempts": [],
                "review_attempts": [],
                "accepted": None,
            }
            for row in changed
        },
    }


def prepare(
    *,
    config_path: Path,
    package_path: Path,
    parent_spec_build: Path,
    parent_final_build: Path,
    parent_teacher_build: Path,
    output_root: Path,
) -> dict[str, Any]:
    config, parent_config, config_payload, prompt_texts = _load_repair_config(
        config_path
    )
    package_audit, review, external_training = _load_review_package_snapshot(
        package_path, config
    )
    parent_specs, parent_train, parent_candidates, parent_audits, dev_payload = (
        _validate_parent_inputs(
            config=config,
            parent_spec_build=parent_spec_build,
            parent_final_build=parent_final_build,
            parent_teacher_build=parent_teacher_build,
        )
    )
    specs, seeds, lineage = _lineage_and_specs(
        config=config,
        parent_config=parent_config,
        package_review=review,
        package_training=external_training,
        parent_specs=parent_specs,
        parent_train=parent_train,
        parent_candidates=parent_candidates,
        parent_audits=parent_audits,
        intake_prompt=prompt_texts["intake"],
    )
    specs_payload = jsonl_bytes(specs)
    seed_payload = jsonl_bytes(seeds)
    package_audit_payload = _json_bytes(package_audit)
    lineage_payload = _json_bytes(lineage)
    answer_reservations = _inherited_answer_reservations(
        specs=specs,
        parent_candidates=parent_candidates,
        parent_config=parent_config,
    )
    identity = {
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "config_sha256": sha256_bytes(config_payload),
        "generator_sha256": sha256_file(SCRIPT_PATH),
        "source_dependency_sha256": _source_dependency_hashes(),
        "package_sha256": config["review_package"]["sha256"],
        "parent_spec_build_sha256": config["parent"]["spec_build_sha256"],
        "parent_train_sha256": config["parent"]["train_sha256"],
        "parent_token_audit_sha256": config["parent"]["token_audit_sha256"],
        "parent_teacher_candidates_sha256": config["parent"][
            "teacher_candidates_sha256"
        ],
        "dev_sha256": sha256_bytes(dev_payload),
        "package_audit_sha256": sha256_bytes(package_audit_payload),
        "lineage_summary_sha256": sha256_bytes(lineage_payload),
        "inherited_answer_reservations": answer_reservations,
        "specs_sha256": sha256_bytes(specs_payload),
        "repair_seed_sha256": sha256_bytes(seed_payload),
        "bound_prompt_sha256": config["prompts"]["bound_sha256"],
        "intake_prompt_sha256": config["prompts"]["intake_sha256"],
    }
    target_sha256 = sha256_bytes(canonical_json_bytes(identity))
    target_id = f"repair-{target_sha256[:12]}"
    target = output_root / target_id
    manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "target_id": target_id,
        "target_sha256": target_sha256,
        "identity": identity,
        "artifact_sha256": {
            "training/specs_2000.jsonl": identity["specs_sha256"],
            "evaluation/dev_cases_200.jsonl": identity["dev_sha256"],
            "review/external_seed_400.jsonl": identity["repair_seed_sha256"],
            "reports/package_audit.json": identity["package_audit_sha256"],
            "reports/lineage_summary.json": identity["lineage_summary_sha256"],
        },
        "rows": {"specs": 2000, "repair_seeds": 400, "dev": 200},
        "teacher_generation_complete": False,
        "training_execution_allowed": False,
        "production_promotion_allowed": False,
    }
    files = {
        "training/specs_2000.jsonl": specs_payload,
        "evaluation/dev_cases_200.jsonl": dev_payload,
        "review/external_seed_400.jsonl": seed_payload,
        "reports/package_audit.json": package_audit_payload,
        "reports/lineage_summary.json": lineage_payload,
        "prepare_manifest.json": _json_bytes(manifest),
    }
    if target.exists():
        if target.is_symlink() or not target.is_dir():
            raise Mix2KV4RepairError("기존 repair target이 안전하지 않습니다.")
        for relative, payload in files.items():
            path = target / relative
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise Mix2KV4RepairError("기존 repair target identity가 다릅니다.")
        state = _load_json(target / "pipeline_state.json", "repair pipeline state")
        if state.get("identity") != identity or state.get("target_id") != target_id:
            raise Mix2KV4RepairError("기존 repair state identity가 다릅니다.")
        return {**manifest, "mode": "reused", "path": str(target)}
    output_root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    output_root.chmod(PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target_id}.", dir=output_root))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for relative, payload in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
            path.write_bytes(payload)
            path.chmod(PRIVATE_FILE_MODE)
        state = _new_pipeline_state(target_id=target_id, identity=identity, specs=specs)
        state_path = temporary / "pipeline_state.json"
        state_path.write_bytes(_json_bytes(state))
        state_path.chmod(PRIVATE_FILE_MODE)
        lock = temporary / ".pipeline.lock"
        lock.touch(mode=PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {**manifest, "mode": "created", "path": str(target)}


def _select_teacher_batch(
    state: Mapping[str, Any], provider_scope: Sequence[str]
) -> tuple[str | None, str | None, list[str]]:
    for provider in provider_scope:
        review_ids = [
            record_id
            for record_id in state["selection_order"]
            if state["records"][record_id]["status"] == "needs_review"
            and state["records"][record_id]["assigned_reviewer"] == provider
        ]
        if review_ids:
            return provider, "review", review_ids[:REPAIR_SHARD_ROWS]
        draft_ids = [
            record_id
            for record_id in state["selection_order"]
            if state["records"][record_id]["status"] == "needs_draft"
            and state["records"][record_id]["assigned_drafter"] == provider
        ]
        if draft_ids:
            return provider, "draft", draft_ids[:REPAIR_SHARD_ROWS]
    return None, None, []


def _replay_provider_call_log(
    *,
    specs: Sequence[Mapping[str, Any]],
    seeds: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    draft_attempts_by_sequence: Mapping[int, Mapping[str, Mapping[str, Any]]],
    review_attempts_by_sequence: Mapping[int, Mapping[str, Mapping[str, Any]]],
) -> None:
    specs_by_id = {row["id"]: row for row in specs}
    seeds_by_id = {row["record_id"]: row for row in seeds}
    simulated = _new_pipeline_state(
        target_id=str(state["target_id"]),
        identity=state["identity"],
        specs=specs,
    )
    call_log = state.get("provider_call_log")
    if not isinstance(call_log, list) or len(call_log) != state["provider_calls"]:
        raise Mix2KV4RepairError("provider call log 길이가 다릅니다.")

    for expected_sequence, call in enumerate(call_log, 1):
        if not isinstance(call, Mapping) or set(call) != PROVIDER_CALL_FIELDS:
            raise Mix2KV4RepairError("provider call log field 집합이 다릅니다.")
        scope = call.get("provider_scope")
        if scope not in (["claude"], ["codex"], ["claude", "codex"]):
            raise Mix2KV4RepairError("provider call scope가 허용 범위 밖입니다.")
        if any(
            record["status"] == "failed" for record in simulated["records"].values()
        ):
            raise Mix2KV4RepairError("failed record 뒤 provider call이 추가됐습니다.")
        if (
            all(
                record["status"] == "accepted"
                for record in simulated["records"].values()
            )
            and _mark_duplicate_repairs(simulated, specs) == 0
        ):
            raise Mix2KV4RepairError("완료 상태 뒤 provider call이 추가됐습니다.")
        provider, kind, record_ids = _select_teacher_batch(simulated, scope)
        if (
            call.get("provider_call_sequence") != expected_sequence
            or call.get("provider") != provider
            or call.get("kind") != kind
            or call.get("record_ids") != record_ids
            or not isinstance(call.get("started_at_utc"), str)
            or not call["started_at_utc"]
            or isinstance(call.get("elapsed_seconds"), bool)
            or not isinstance(call.get("elapsed_seconds"), (int, float))
            or call["elapsed_seconds"] < 0
            or not isinstance(call.get("prompt_sha256"), str)
            or FULL_SHA256.fullmatch(call["prompt_sha256"]) is None
            or not isinstance(call.get("provider_output"), Mapping)
            or not isinstance(call.get("provider_output_sha256"), str)
            or FULL_SHA256.fullmatch(call["provider_output_sha256"]) is None
            or call["provider_output_sha256"]
            != sha256_bytes(canonical_json_bytes(call["provider_output"]))
        ):
            raise Mix2KV4RepairError(
                f"provider call scheduler replay가 다릅니다: {expected_sequence}"
            )
        batch_specs = [specs_by_id[record_id] for record_id in record_ids]
        if kind == "draft":
            feedback = {
                record_id: simulated["records"][record_id]["feedback"]
                for record_id in record_ids
            }
            prompt = _repair_draft_prompt(batch_specs, feedback, seeds_by_id)
            attempt_map = draft_attempts_by_sequence.get(expected_sequence, {})
            output_key = "drafts"
        else:
            draft_map = {
                record_id: simulated["records"][record_id]["current_draft"]
                for record_id in record_ids
            }
            prompt = _repair_review_prompt(batch_specs, draft_map)
            attempt_map = review_attempts_by_sequence.get(expected_sequence, {})
            output_key = "reviews"
        if call["prompt_sha256"] != sha256_bytes(prompt.encode("utf-8")):
            raise Mix2KV4RepairError(
                f"provider call prompt hash를 재현하지 못했습니다: {expected_sequence}"
            )
        try:
            output_rows = _result_map(
                call["provider_output"], key=output_key, record_ids=record_ids
            )
        except Mix2KV4TeacherError as exc:
            raise Mix2KV4RepairError(str(exc)) from exc
        if set(attempt_map) != set(record_ids):
            raise Mix2KV4RepairError(
                f"provider call attempt 집합이 다릅니다: {expected_sequence}"
            )
        for record_id in record_ids:
            attempt = attempt_map[record_id]
            if (
                attempt["provider"] != provider
                or attempt["started_at_utc"] != call["started_at_utc"]
                or attempt["elapsed_seconds"] != call["elapsed_seconds"]
            ):
                raise Mix2KV4RepairError(
                    f"provider call과 row attempt가 다릅니다: {record_id}"
                )
            record = simulated["records"][record_id]
            if kind == "draft":
                if output_rows[record_id] != attempt["provider_draft"]:
                    raise Mix2KV4RepairError(
                        f"provider raw draft가 call output과 다릅니다: {record_id}"
                    )
                validation_error = attempt["validation_error"]
                record["draft_attempts"].append(deepcopy(dict(attempt)))
                if validation_error is None:
                    record["status"] = "needs_review"
                    record["current_draft"] = deepcopy(attempt["draft"])
                    record["current_draft_provider"] = provider
                    record["feedback"] = ""
                else:
                    record["rewrites_used"] += 1
                    record["feedback"] = validation_error
                    if record["rewrites_used"] > MAXIMUM_REWRITE_ROUNDS:
                        record["status"] = "failed"
            else:
                review = attempt["review"]
                if output_rows[record_id] != review or attempt[
                    "reviewed_draft_sha256"
                ] != sha256_bytes(canonical_json_bytes(record["current_draft"])):
                    raise Mix2KV4RepairError(
                        f"peer review가 현재 draft와 연결되지 않았습니다: {record_id}"
                    )
                record["review_attempts"].append(deepcopy(dict(attempt)))
                if review["decision"] == "PASS":
                    record["status"] = "accepted"
                    record["accepted"] = {
                        "draft_provider": record["current_draft_provider"],
                        "review_provider": provider,
                        "draft": deepcopy(record["current_draft"]),
                        "review": deepcopy(review),
                    }
                else:
                    record["rewrites_used"] += 1
                    record["status"] = "needs_draft"
                    record["feedback"] = _state_feedback(review)
                    record["current_draft"] = None
                    record["current_draft_provider"] = None
                    record["accepted"] = None
                    if record["rewrites_used"] > MAXIMUM_REWRITE_ROUNDS:
                        record["status"] = "failed"
        simulated["provider_calls"] = expected_sequence
        simulated["provider_call_log"].append(deepcopy(dict(call)))

    comparable_fields = (
        "status",
        "current_draft",
        "current_draft_provider",
        "feedback",
        "rewrites_used",
        "duplicate_rewrites_used",
        "accepted",
    )
    if any(
        any(
            simulated["records"][record_id][field] != state["records"][record_id][field]
            for field in comparable_fields
        )
        for record_id in simulated["selection_order"]
    ):
        if all(
            record["status"] == "accepted" for record in simulated["records"].values()
        ):
            _mark_duplicate_repairs(simulated, specs)
        if any(
            any(
                simulated["records"][record_id][field]
                != state["records"][record_id][field]
                for field in comparable_fields
            )
            for record_id in simulated["selection_order"]
        ):
            raise Mix2KV4RepairError(
                "provider call log로 현재 state를 재현하지 못했습니다."
            )


def _validate_pipeline_state(
    *,
    specs: Sequence[Mapping[str, Any]],
    seeds: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """mutable teacher state를 spec과 실제 attempt chain에서 전수 재생한다."""

    if set(state) != PIPELINE_STATE_FIELDS:
        raise Mix2KV4RepairError("repair pipeline state field 집합이 다릅니다.")
    specs_by_id = {
        row["id"]: row for row in specs if row.get("task_axis") in REPAIR_AXES
    }
    seeds_by_id = {row.get("record_id"): row for row in seeds}
    records = state.get("records")
    selection = state.get("selection_order")
    provider_calls = state.get("provider_calls")
    if (
        len(specs_by_id) != 400
        or len(seeds_by_id) != 400
        or not isinstance(records, Mapping)
        or not isinstance(selection, list)
        or selection != [row["id"] for row in specs if row["task_axis"] in REPAIR_AXES]
        or set(records) != set(selection)
        or isinstance(provider_calls, bool)
        or not isinstance(provider_calls, int)
        or provider_calls < 0
    ):
        raise Mix2KV4RepairError("repair pipeline state identity가 다릅니다.")

    call_batches: dict[int, dict[str, Any]] = {}
    draft_attempts_by_sequence: dict[int, dict[str, Mapping[str, Any]]] = {}
    review_attempts_by_sequence: dict[int, dict[str, Mapping[str, Any]]] = {}
    counts: Counter[str] = Counter()
    for record_id in selection:
        spec = specs_by_id[record_id]
        seed = seeds_by_id[record_id]
        record = records[record_id]
        if (
            not isinstance(record, Mapping)
            or set(record) != PIPELINE_RECORD_FIELDS
            or record.get("spec_sha256") != sha256_bytes(canonical_json_bytes(spec))
            or record.get("task_axis") != spec["task_axis"]
            or record.get("assigned_drafter") != spec["drafter"]
            or record.get("assigned_reviewer") != spec["reviewer"]
            or record.get("assigned_drafter") == record.get("assigned_reviewer")
            or record.get("status")
            not in {"needs_draft", "needs_review", "accepted", "failed"}
            or not isinstance(record.get("feedback"), str)
            or isinstance(record.get("rewrites_used"), bool)
            or not isinstance(record.get("rewrites_used"), int)
            or record["rewrites_used"] < 0
            or isinstance(record.get("duplicate_rewrites_used"), bool)
            or not isinstance(record.get("duplicate_rewrites_used"), int)
            or not 0 <= record["duplicate_rewrites_used"] <= 3
            or not isinstance(record.get("draft_attempts"), list)
            or not isinstance(record.get("review_attempts"), list)
        ):
            raise Mix2KV4RepairError(
                f"repair pipeline record identity가 다릅니다: {record_id}"
            )

        events: list[tuple[int, str, bool, Mapping[str, Any]]] = []
        reviewed_draft_hashes: dict[int, str] = {}
        rewrite_failures = 0
        for attempt in record["draft_attempts"]:
            expected_normalized = None
            expected_layout = False
            expected_particle = False
            if isinstance(attempt, Mapping) and isinstance(
                attempt.get("provider_draft"), Mapping
            ):
                expected_normalized, expected_layout = _normalize_draft_answer_layout(
                    spec, attempt["provider_draft"]
                )
                expected_normalized, expected_particle = (
                    _normalize_draft_answer_particles(expected_normalized)
                )
            if (
                not isinstance(attempt, Mapping)
                or set(attempt) != DRAFT_ATTEMPT_FIELDS
                or attempt.get("provider") != spec["drafter"]
                or isinstance(attempt.get("provider_call_sequence"), bool)
                or not isinstance(attempt.get("provider_call_sequence"), int)
                or not 1 <= attempt["provider_call_sequence"] <= provider_calls
                or not isinstance(attempt.get("started_at_utc"), str)
                or not attempt["started_at_utc"]
                or isinstance(attempt.get("elapsed_seconds"), bool)
                or not isinstance(attempt.get("elapsed_seconds"), (int, float))
                or attempt["elapsed_seconds"] < 0
                or not isinstance(attempt.get("provider_draft"), Mapping)
                or not isinstance(attempt.get("draft"), Mapping)
                or not isinstance(attempt.get("provider_draft_sha256"), str)
                or FULL_SHA256.fullmatch(attempt["provider_draft_sha256"]) is None
                or attempt["provider_draft_sha256"]
                != sha256_bytes(canonical_json_bytes(attempt["provider_draft"]))
                or not isinstance(attempt.get("normalized_draft_sha256"), str)
                or FULL_SHA256.fullmatch(attempt["normalized_draft_sha256"]) is None
                or attempt["normalized_draft_sha256"]
                != sha256_bytes(canonical_json_bytes(attempt["draft"]))
                or attempt["draft"] != expected_normalized
                or attempt.get("layout_normalized") is not expected_layout
                or attempt.get("particle_normalized") is not expected_particle
                or type(attempt.get("layout_normalized")) is not bool
                or type(attempt.get("particle_normalized")) is not bool
                or type(attempt.get("deterministic_pass")) is not bool
            ):
                raise Mix2KV4RepairError(
                    f"repair draft attempt가 손상됐습니다: {record_id}"
                )
            validation_error = _draft_validation_error(
                spec, attempt["draft"], seed["external_answer"]
            )
            if (
                attempt["deterministic_pass"] is not (validation_error is None)
                or attempt.get("validation_error") != validation_error
            ):
                raise Mix2KV4RepairError(
                    f"repair draft 판정을 재현하지 못했습니다: {record_id}"
                )
            rewrite_failures += int(validation_error is not None)
            sequence = attempt["provider_call_sequence"]
            events.append(
                (sequence, "draft", validation_error is None, attempt["draft"])
            )
            batch = call_batches.setdefault(
                sequence,
                {"provider": attempt["provider"], "kind": "draft", "ids": []},
            )
            if batch["provider"] != attempt["provider"] or batch["kind"] != "draft":
                raise Mix2KV4RepairError("provider call batch 종류가 섞였습니다.")
            batch["ids"].append(record_id)
            draft_attempts_by_sequence.setdefault(sequence, {})[record_id] = attempt

        for attempt in record["review_attempts"]:
            if (
                not isinstance(attempt, Mapping)
                or set(attempt) != REVIEW_ATTEMPT_FIELDS
                or attempt.get("provider") != spec["reviewer"]
                or isinstance(attempt.get("provider_call_sequence"), bool)
                or not isinstance(attempt.get("provider_call_sequence"), int)
                or not 1 <= attempt["provider_call_sequence"] <= provider_calls
                or not isinstance(attempt.get("started_at_utc"), str)
                or not attempt["started_at_utc"]
                or isinstance(attempt.get("elapsed_seconds"), bool)
                or not isinstance(attempt.get("elapsed_seconds"), (int, float))
                or attempt["elapsed_seconds"] < 0
                or not isinstance(attempt.get("review"), Mapping)
                or not isinstance(attempt.get("review_sha256"), str)
                or FULL_SHA256.fullmatch(attempt["review_sha256"]) is None
                or attempt["review_sha256"]
                != sha256_bytes(canonical_json_bytes(attempt["review"]))
                or not isinstance(attempt.get("reviewed_draft_sha256"), str)
                or FULL_SHA256.fullmatch(attempt["reviewed_draft_sha256"]) is None
            ):
                raise Mix2KV4RepairError(
                    f"repair review attempt가 손상됐습니다: {record_id}"
                )
            try:
                validate_review(spec, attempt["review"])
            except Mix2KV4ContractError as exc:
                raise Mix2KV4RepairError(str(exc)) from exc
            passed = attempt["review"]["decision"] == "PASS"
            rewrite_failures += int(not passed)
            sequence = attempt["provider_call_sequence"]
            events.append((sequence, "review", passed, attempt["review"]))
            reviewed_draft_hashes[sequence] = attempt["reviewed_draft_sha256"]
            batch = call_batches.setdefault(
                sequence,
                {"provider": attempt["provider"], "kind": "review", "ids": []},
            )
            if batch["provider"] != attempt["provider"] or batch["kind"] != "review":
                raise Mix2KV4RepairError("provider call batch 종류가 섞였습니다.")
            batch["ids"].append(record_id)
            review_attempts_by_sequence.setdefault(sequence, {})[record_id] = attempt

        events.sort(key=lambda item: item[0])
        if len({event[0] for event in events}) != len(events):
            raise Mix2KV4RepairError(
                f"한 record에 provider call sequence가 중복됐습니다: {record_id}"
            )
        for index, event in enumerate(events):
            if event[1] == "review" and (
                index == 0
                or events[index - 1][1] != "draft"
                or events[index - 1][2] is not True
                or reviewed_draft_hashes[event[0]]
                != sha256_bytes(canonical_json_bytes(events[index - 1][3]))
            ):
                raise Mix2KV4RepairError(
                    f"review가 직전 deterministic PASS draft와 연결되지 않았습니다: {record_id}"
                )
            if (
                event[1] == "draft"
                and index > 0
                and events[index - 1][1] == "review"
                and events[index - 1][2] is True
                and record["duplicate_rewrites_used"] == 0
            ):
                raise Mix2KV4RepairError(
                    f"PASS 뒤 원인 없는 draft가 추가됐습니다: {record_id}"
                )
        if record["rewrites_used"] != rewrite_failures:
            raise Mix2KV4RepairError(
                f"repair rewrite counter를 재현하지 못했습니다: {record_id}"
            )

        status = record["status"]
        accepted = record["accepted"]
        current_draft = record["current_draft"]
        current_provider = record["current_draft_provider"]
        latest = events[-1] if events else None
        latest_draft = next(
            (event for event in reversed(events) if event[1] == "draft"), None
        )
        if status in {"needs_review", "accepted"}:
            if (
                latest_draft is None
                or latest_draft[2] is not True
                or current_draft != latest_draft[3]
                or current_provider != spec["drafter"]
                or record["feedback"]
            ):
                raise Mix2KV4RepairError(
                    f"current draft가 최신 PASS attempt와 다릅니다: {record_id}"
                )
        elif current_draft is not None or current_provider is not None:
            raise Mix2KV4RepairError(
                f"draft 대기·실패 record에 current draft가 남았습니다: {record_id}"
            )

        if status == "accepted":
            if (
                not isinstance(accepted, Mapping)
                or set(accepted) != ACCEPTED_FIELDS
                or latest is None
                or latest[1:3] != ("review", True)
                or accepted.get("draft_provider") != spec["drafter"]
                or accepted.get("review_provider") != spec["reviewer"]
                or accepted.get("draft") != current_draft
                or accepted.get("review") != latest[3]
            ):
                raise Mix2KV4RepairError(
                    f"accepted가 실제 draft·peer PASS와 연결되지 않았습니다: {record_id}"
                )
        elif accepted is not None:
            raise Mix2KV4RepairError(
                f"미승인 record에 accepted가 있습니다: {record_id}"
            )
        if status == "needs_review" and (
            latest is None or latest[1:3] != ("draft", True)
        ):
            raise Mix2KV4RepairError(
                f"review 대기 상태가 최신 PASS draft와 다릅니다: {record_id}"
            )
        if status == "failed" and (
            latest is None or latest[2] is not False or not record["feedback"]
        ):
            raise Mix2KV4RepairError(
                f"failed 상태가 실제 실패 attempt와 다릅니다: {record_id}"
            )
        if status == "needs_draft":
            if not events:
                if (
                    record["feedback"]
                    or record["rewrites_used"]
                    or record["duplicate_rewrites_used"]
                ):
                    raise Mix2KV4RepairError(
                        f"초기 draft 대기 상태가 다릅니다: {record_id}"
                    )
            elif latest[1] == "draft" and latest[2] is True:
                raise Mix2KV4RepairError(
                    f"PASS draft가 review 대기 상태가 아닙니다: {record_id}"
                )
            elif latest[1] == "review" and latest[2] is True:
                if record["duplicate_rewrites_used"] == 0 or not record["feedback"]:
                    raise Mix2KV4RepairError(
                        f"duplicate 재작성 상태의 근거가 없습니다: {record_id}"
                    )
            elif not record["feedback"]:
                raise Mix2KV4RepairError(
                    f"실패 뒤 재작성 feedback이 없습니다: {record_id}"
                )
        counts[status] += 1

    if set(call_batches) != set(range(1, provider_calls + 1)):
        raise Mix2KV4RepairError("provider call sequence를 재현하지 못했습니다.")
    _replay_provider_call_log(
        specs=specs,
        seeds=seeds,
        state=state,
        draft_attempts_by_sequence=draft_attempts_by_sequence,
        review_attempts_by_sequence=review_attempts_by_sequence,
    )
    return {
        "provider_calls": provider_calls,
        "status_counts": dict(sorted(counts.items())),
        "attempt_linkage_valid": True,
        "raw_and_normalized_draft_hashes_valid": True,
    }


def _load_static_payloads(
    target: Path, identity: Mapping[str, Any]
) -> dict[str, bytes]:
    dev_rows, dev_payload = _load_jsonl_snapshot(
        target / "evaluation/dev_cases_200.jsonl", "repair frozen dev"
    )
    package_audit, package_audit_payload = _load_json_snapshot(
        target / "reports/package_audit.json", "repair package audit"
    )
    lineage, lineage_payload = _load_json_snapshot(
        target / "reports/lineage_summary.json", "repair lineage summary"
    )
    if (
        len(dev_rows) != 200
        or sha256_bytes(dev_payload) != identity.get("dev_sha256")
        or sha256_bytes(package_audit_payload) != identity.get("package_audit_sha256")
        or sha256_bytes(lineage_payload) != identity.get("lineage_summary_sha256")
        or _json_bytes(package_audit) != package_audit_payload
        or _json_bytes(lineage) != lineage_payload
    ):
        raise Mix2KV4RepairError("repair 고정 dev·report hash chain이 다릅니다.")
    return {
        "evaluation/dev_cases_200.jsonl": dev_payload,
        "reports/package_audit.json": package_audit_payload,
        "reports/lineage_summary.json": lineage_payload,
    }


def _load_target(
    target: Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, bytes],
]:
    if target.is_symlink() or not target.is_dir() or not target.is_absolute():
        raise Mix2KV4RepairError("repair target은 안전한 절대경로여야 합니다.")
    manifest, manifest_payload = _load_json_snapshot(
        target / "prepare_manifest.json", "repair prepare manifest"
    )
    specs, specs_payload = _load_jsonl_snapshot(
        target / "training/specs_2000.jsonl", "repair specs"
    )
    seeds, seed_payload = _load_jsonl_snapshot(
        target / "review/external_seed_400.jsonl", "repair seeds"
    )
    state, state_payload = _load_json_snapshot(
        target / "pipeline_state.json", "repair pipeline state"
    )
    identity = manifest.get("identity")
    static_payloads = (
        _load_static_payloads(target, identity) if isinstance(identity, Mapping) else {}
    )
    if isinstance(identity, Mapping):
        reservations = identity.get("inherited_answer_reservations")
        if not isinstance(reservations, Mapping):
            raise Mix2KV4RepairError("상속 답변 중복 예약 identity가 없습니다.")
        _validate_inherited_answer_reservations(reservations, specs)
    if (
        not isinstance(identity, Mapping)
        or manifest.get("target_id") != target.name
        or manifest.get("target_sha256") != sha256_bytes(canonical_json_bytes(identity))
        or manifest.get("artifact_sha256")
        != {
            "evaluation/dev_cases_200.jsonl": identity.get("dev_sha256"),
            "reports/lineage_summary.json": identity.get("lineage_summary_sha256"),
            "reports/package_audit.json": identity.get("package_audit_sha256"),
            "review/external_seed_400.jsonl": identity.get("repair_seed_sha256"),
            "training/specs_2000.jsonl": identity.get("specs_sha256"),
        }
        or identity.get("generator_sha256") != sha256_file(SCRIPT_PATH)
        or identity.get("source_dependency_sha256") != _source_dependency_hashes()
        or state.get("target_id") != target.name
        or state.get("identity") != identity
        or sha256_bytes(specs_payload) != identity.get("specs_sha256")
        or sha256_bytes(seed_payload) != identity.get("repair_seed_sha256")
        or len(specs) != EXPECTED_ROWS
        or len(seeds) != 400
        or _json_bytes(manifest) != manifest_payload
        or _json_bytes(state) != state_payload
    ):
        raise Mix2KV4RepairError("repair target hash chain이 다릅니다.")
    records = state.get("records")
    selection = state.get("selection_order")
    if (
        not isinstance(records, dict)
        or not isinstance(selection, list)
        or len(records) != 400
        or len(selection) != 400
        or set(records) != set(selection)
    ):
        raise Mix2KV4RepairError("repair pipeline record 집합이 다릅니다.")
    _validate_pipeline_state(specs=specs, seeds=seeds, state=state)
    return manifest, specs, seeds, state, static_payloads


def _ambiguity_answer_error(ambiguity: str | None, answer: str) -> str | None:
    if ambiguity == "birth_date_correction":
        if not any(term in answer for term in ("생년월일", "출생일", "태어난 날짜")):
            return "birth_date_correction_not_acknowledged"
        if "원국" not in answer or not any(
            term in answer for term in ("다시 계산", "재계산", "새로 계산")
        ):
            return "birth_date_chart_recalculation_missing"
    elif ambiguity == "target_date_change":
        if "원국" not in answer or not any(
            term in answer for term in ("유지", "그대로", "바뀌지")
        ):
            return "target_date_natal_retention_missing"
        if "날짜" not in answer or not any(
            term in answer for term in ("새로", "갱신", "다시 요청", "교체")
        ):
            return "target_date_refresh_missing"
    elif ambiguity == "actual_birth_time_correction":
        if not any(
            term in answer for term in ("시간 미상", "출생시간을 모르", "생시 미상")
        ):
            return "actual_unknown_time_not_acknowledged"
        if "시주" not in answer or not any(
            term in answer
            for term in ("무효", "제외", "사용하지", "쓰지", "확정값이 아니")
        ):
            return "stale_hour_pillar_not_invalidated"
        if "원국" not in answer or not any(
            term in answer for term in ("다시 계산", "재계산", "새로 계산")
        ):
            return "unknown_time_chart_recalculation_missing"
    elif ambiguity == "hypothetical_unknown_time_policy":
        if not any(term in answer for term in ("가정", "일반적", "원칙")):
            return "hypothetical_policy_scope_missing"
        if (
            "시주" not in answer
            or "임의" not in answer
            or not any(
                term in answer
                for term in ("고르지", "정하지", "선택하지", "만들지", "넣지")
            )
        ):
            return "hypothetical_hour_pillar_policy_missing"
        if not any(
            term in answer
            for term in (
                "현재 원국",
                "기존 원국",
                "지금 원국",
                "현재 결과",
                "기존 결과",
            )
        ) or not any(
            term in answer for term in ("유지", "바꾸지", "정정하지", "그대로")
        ):
            return "hypothetical_current_state_retention_missing"
    return None


def _has_unnegated_state_completion(answer: str) -> bool:
    for match in FALSE_STATE_COMPLETION.finditer(answer):
        tail = answer[match.end() : match.end() + 32]
        if FALSE_STATE_COMPLETION_NEGATION.match(tail) is None:
            return True
    return False


def _repair_answer_error(spec: Mapping[str, Any], answer: str) -> str | None:
    if INTERNAL_ASSISTANT_LANGUAGE.search(answer):
        return "internal_or_false_authority_language"
    if any(wrapper in answer for wrapper in ANSWER_HINT_WRAPPERS):
        return "prompt_wrapper_echo"
    ambiguity = AMBIGUITY_BY_PROMPT.get(spec["prompt"][-1]["content"])
    ambiguity_error = _ambiguity_answer_error(ambiguity, answer)
    if ambiguity_error is not None:
        return ambiguity_error
    if spec["task_axis"] == "intake_state_correction":
        system = spec["prompt"][0]["content"]
        marker = "[앱의 구조화 입력 상태]\n"
        if marker not in system:
            return "intake_projection_missing"
        projection = json.loads(system.split(marker, 1)[1])
        action = projection["next_decision"]["action"]
        if action not in {
            "render_chart",
            "render_period",
        } and _has_unnegated_state_completion(answer):
            return "false_state_completion"
        missing = projection["missing_fields"]
        if (
            action == "request_slots"
            and missing
            and not any(
                any(
                    term in answer
                    for term in {
                        "calendar": ("양력", "음력", "달력"),
                        "birth_date": ("생년월일", "출생일", "태어난 날짜"),
                        "birthplace": ("출생지", "태어난 도시", "태어난 국가"),
                        "leap_month": ("윤달", "평달"),
                        "time_precision": ("출생시간", "태어난 시간", "시간 미상"),
                        "birth_time": ("출생시간", "태어난 시간", "시간 미상"),
                        "time_range": ("시간 범위", "태어난 시간", "출생시간"),
                    }[field]
                )
                for field in missing
            )
        ):
            return "missing_fsm_next_field"
    return None


def _draft_validation_error(
    spec: Mapping[str, Any],
    draft: Any,
    external_answer: str,
) -> str | None:
    try:
        validate_draft(spec, draft)
        error = _repair_answer_error(spec, draft["answer"])
        if error is None and normalize_answer(draft["answer"]) == normalize_answer(
            external_answer
        ):
            error = "external_proposal_copied"
        return error
    except (Mix2KV4ContractError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return str(exc)


def _repair_draft_prompt(
    specs: Sequence[Mapping[str, Any]],
    feedback: Mapping[str, str],
    seeds: Mapping[str, Mapping[str, Any]],
) -> str:
    base = draft_prompt(specs, feedback)
    seed_rows = [
        {
            "record_id": spec["id"],
            "ambiguity_resolution": seeds[spec["id"]]["ambiguity_resolution"],
            "proposal_only_not_gold": seeds[spec["id"]]["external_answer"],
        }
        for spec in specs
    ]
    return (
        "외부 검토 답변은 문제 발견용 제안일 뿐 teacher 초안이나 Gold가 아닙니다. "
        "구조화 상태와 현재 TASK를 우선해 새 답변을 직접 작성하고, `모델`, `학습 정답`, "
        "`Gold`, `전문가 검토`, 내부 JSON 경로를 사용자 답변에 쓰지 마세요. intake 답변은 "
        "앱이 실제로 수행할 다음 행동을 설명하되 저장·정정·계산을 이미 완료했다고 말하지 "
        "마세요. ambiguity_resolution이 있으면 birth date 정정은 원국·날짜 결과 재계산, "
        "target date 변경은 원국 유지·날짜 결과만 갱신, actual unknown time 정정은 기존 "
        "시주 배제·원국 재계산, hypothetical unknown time은 현재 결과 유지·임의 시주 금지로 "
        "구분하세요. HARD QA도 내부 field 이름 대신 일반인이 이해할 한국어 명칭을 사용하세요.\n\n"
        "used_fact_paths와 used_fact_values는 각각 중복 없이 작성하고, 같은 값이 여러 경로에 "
        "있어도 used_fact_values에는 한 번만 기록하세요.\n\n"
        + base
        + "\n\n[EXTERNAL REVIEW PROPOSALS - NOT GOLD]\n"
        + json.dumps(
            seed_rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    )


def _repair_review_prompt(
    specs: Sequence[Mapping[str, Any]], drafts: Mapping[str, Mapping[str, Any]]
) -> str:
    return (
        "추가 FAIL 기준: 사용자 답변에 `모델`, `학습 정답`, `Gold`, `전문가 검토`, "
        "`chart.*`, `period.*`, `hidden_stems` 같은 내부 구현 표현이 있으면 FAIL하세요. "
        "intake 상태의 next_decision과 반대로 안내하거나 앱이 저장·정정·계산을 이미 완료했다고 "
        "말하면 FAIL하세요. uncertainty의 3문장·3개 의미 줄을 짧게 축약하지 마세요.\n\n"
        + review_prompt(specs, drafts)
    )


def _repair_provider_call(
    *,
    provider: str,
    prompt: str,
    schema: Mapping[str, Any],
    environment: Mapping[str, str],
    timeout_seconds: int,
    model: str | None = None,
) -> dict[str, Any]:
    if provider != "claude":
        return _provider_call(
            provider=provider,
            prompt=prompt,
            schema=schema,
            environment=environment,
            timeout_seconds=timeout_seconds,
            model=model,
        )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="mix2k-v4-repair-claude-") as directory:
        working = Path(directory)
        working.chmod(PRIVATE_DIR_MODE)
        command = [
            "claude",
            "-p",
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--model",
            model or "sonnet",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":"), sort_keys=True),
        ]
        try:
            result = subprocess.run(
                command,
                input=prompt,
                cwd=working,
                env=dict(environment),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Mix2KV4RepairError(
                "Claude subscription call이 중단됐습니다."
            ) from exc
        if result.returncode != 0:
            raise Mix2KV4RepairError(
                f"Claude subscription call이 exit {result.returncode}로 실패했습니다."
            )
        if len(result.stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
            raise Mix2KV4RepairError("Claude output이 용량 상한을 넘었습니다.")
        try:
            envelope = json.loads(result.stdout)
            structured = envelope.get("structured_output")
        except (UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise Mix2KV4RepairError(
                "Claude structured output을 읽지 못했습니다."
            ) from exc
        if not isinstance(structured, dict):
            raise Mix2KV4RepairError("Claude structured output이 object가 아닙니다.")
    return {
        "structured": structured,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _state_feedback(review: Mapping[str, Any]) -> str:
    parts = [
        *review.get("failure_codes", []),
        *review.get("fact_errors", []),
        *review.get("style_notes", []),
    ]
    instruction = str(review.get("rewrite_instructions", "")).strip()
    if instruction:
        parts.append(instruction)
    return "; ".join(str(value) for value in parts if str(value).strip())


def _mark_duplicate_repairs(
    state: dict[str, Any], specs: Sequence[Mapping[str, Any]] | None = None
) -> int:
    reservations = state.get("identity", {}).get("inherited_answer_reservations")
    if reservations is None:
        exact_seen: Counter[str] = Counter()
        normalized_seen: Counter[str] = Counter()
    elif isinstance(reservations, Mapping) and specs is not None:
        exact_seen, normalized_seen = _validate_inherited_answer_reservations(
            reservations, specs
        )
    else:
        raise Mix2KV4RepairError("상속 답변 중복 예약을 검증할 spec이 없습니다.")
    repair: dict[str, set[str]] = {}
    for record_id in state["selection_order"]:
        accepted = state["records"][record_id].get("accepted")
        if not isinstance(accepted, Mapping):
            return 0
        answer = accepted["draft"]["answer"].strip()
        exact_digest = sha256_bytes(answer.encode("utf-8"))
        normalized_digest = sha256_bytes(normalize_answer(answer).encode("utf-8"))
        if exact_seen[exact_digest] > 0:
            repair.setdefault(record_id, set()).add("exact")
        if normalized_seen[normalized_digest] >= 2:
            repair.setdefault(record_id, set()).add("normalized")
        exact_seen[exact_digest] += 1
        normalized_seen[normalized_digest] += 1
    exhausted = [
        record_id
        for record_id in state["selection_order"]
        if record_id in repair
        and state["records"][record_id]["duplicate_rewrites_used"] >= 3
    ]
    if exhausted:
        raise Mix2KV4RepairError(
            "전체 repair 답변 중복을 세 차례 해소하지 못했습니다: "
            + ",".join(exhausted[:10])
        )
    for record_id, reasons in repair.items():
        record = state["records"][record_id]
        used = record["duplicate_rewrites_used"]
        previous = record["accepted"]["draft"]["answer"]
        record["duplicate_rewrites_used"] = used + 1
        record["status"] = "needs_draft"
        record["accepted"] = None
        record["current_draft"] = None
        record["current_draft_provider"] = None
        record["feedback"] = (
            "repair dataset의 "
            + "/".join(sorted(reasons))
            + " 답변 중복을 피하세요. 사실과 결론은 유지하되 문장 구조와 설명 순서를 새로 "
            "작성하세요. 이전 답변: " + previous
        )
    return len(repair)


def _teacher_candidates(
    state: Mapping[str, Any],
    specs: Sequence[dict[str, Any]],
    seeds: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    _validate_pipeline_state(specs=specs, seeds=seeds, state=state)
    specs_by_id = {row["id"]: row for row in specs}
    rows: list[dict[str, Any]] = []
    for record_id in state["selection_order"]:
        record = state["records"][record_id]
        accepted = record.get("accepted")
        if record.get("status") != "accepted" or not isinstance(accepted, Mapping):
            raise Mix2KV4RepairError("repair teacher 후보가 완결되지 않았습니다.")
        spec = specs_by_id[record_id]
        draft = accepted["draft"]
        rows.append(
            {
                "schema_version": "1.0.0",
                "dataset_version": DATASET_VERSION,
                "id": record_id,
                "conversation_id": spec["conversation_id"],
                "task_axis": spec["task_axis"],
                "template_family": spec["template_family"],
                "substantive": spec["substantive"],
                "multiturn": spec["multiturn"],
                "prompt": spec["prompt"],
                "assistant": draft["answer"],
                "runtime_snapshot_sha256": (
                    spec["runtime_binding"]["snapshot_sha256"]
                    if spec["runtime_binding"] is not None
                    else None
                ),
                "teacher": {
                    "drafter": accepted["draft_provider"],
                    "reviewer": accepted["review_provider"],
                    "peer_review": "PASS",
                    "deterministic_validation": "PASS",
                    "rewrites_used": record["rewrites_used"],
                    "used_fact_paths": draft["used_fact_paths"],
                    "used_fact_values": draft["used_fact_values"],
                    "soft_interpretation_used": draft["soft_interpretation_used"],
                    "limitations": draft["limitations"],
                },
                "restricted_local_only": False,
            }
        )
    return rows


def _expected_teacher_completion(
    target: Path,
    manifest: Mapping[str, Any],
    specs: Sequence[dict[str, Any]],
    seeds: Sequence[dict[str, Any]],
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    duplicate_check = deepcopy(dict(state))
    if _mark_duplicate_repairs(duplicate_check, specs) != 0:
        raise Mix2KV4RepairError("repair teacher 완료 전에 답변 중복이 남았습니다.")
    rows = _teacher_candidates(state, specs, seeds)
    payload = jsonl_bytes(rows)
    state_payload = _json_bytes(state)
    teacher_roles = Counter(row["teacher"]["drafter"] for row in rows)
    review_roles = Counter(row["teacher"]["reviewer"] for row in rows)
    if (
        teacher_roles != {"claude": 200, "codex": 200}
        or review_roles != {"claude": 200, "codex": 200}
        or any(row["teacher"]["drafter"] == row["teacher"]["reviewer"] for row in rows)
    ):
        raise Mix2KV4RepairError("repair teacher 역할 분포가 다릅니다.")
    teacher_manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "target_id": target.name,
        "prepare_target_sha256": manifest["target_sha256"],
        "pipeline_state_sha256": sha256_bytes(state_payload),
        "candidate_path": "accepted/repaired_candidates_400.jsonl",
        "candidate_sha256": sha256_bytes(payload),
        "rows": len(rows),
        "axes": dict(sorted(Counter(row["task_axis"] for row in rows).items())),
        "teacher_roles": dict(sorted(teacher_roles.items())),
        "review_roles": dict(sorted(review_roles.items())),
        "changed_rows_cross_provider_passed": all(
            row["teacher"]["drafter"] != row["teacher"]["reviewer"] for row in rows
        ),
        "all_2000_rows_cross_provider_contract_met": False,
        "external_answers_counted_as_teacher": False,
        "development_targets_accessed": False,
        "production_promotion_allowed": False,
    }
    return teacher_manifest, rows, payload


def _load_teacher_completion(
    target: Path,
    manifest: Mapping[str, Any],
    specs: Sequence[dict[str, Any]],
    seeds: Sequence[dict[str, Any]],
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], bytes, bytes]:
    expected_manifest, expected_rows, expected_candidates_payload = (
        _expected_teacher_completion(target, manifest, specs, seeds, state)
    )
    observed_manifest, manifest_payload = _load_json_snapshot(
        target / "teacher_manifest.json", "repair teacher manifest"
    )
    observed_rows, candidates_payload = _load_jsonl_snapshot(
        target / "accepted/repaired_candidates_400.jsonl",
        "repair teacher candidates",
    )
    if (
        observed_manifest != expected_manifest
        or manifest_payload != _json_bytes(expected_manifest)
        or observed_rows != expected_rows
        or candidates_payload != expected_candidates_payload
    ):
        raise Mix2KV4RepairError("repair teacher 완료 artifact가 state와 다릅니다.")
    return observed_manifest, observed_rows, manifest_payload, candidates_payload


def _write_teacher_completion(
    target: Path,
    manifest: Mapping[str, Any],
    specs: Sequence[dict[str, Any]],
    seeds: Sequence[dict[str, Any]],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    teacher_manifest, _rows, payload = _expected_teacher_completion(
        target, manifest, specs, seeds, state
    )
    _atomic_write(target / "accepted/repaired_candidates_400.jsonl", payload)
    _atomic_write(target / "teacher_manifest.json", _json_bytes(teacher_manifest))
    return teacher_manifest


def run_teachers(
    *,
    config_path: Path,
    target: Path,
    provider_only: str | None,
    timeout_seconds: int,
    max_provider_calls: int,
) -> dict[str, Any]:
    config, parent_config, _config_payload, _prompt_texts = _load_repair_config(
        config_path
    )
    del parent_config
    manifest, specs, seeds, state, _static_payloads = _load_target(target)
    specs_by_id = {row["id"]: row for row in specs}
    seeds_by_id = {row["record_id"]: row for row in seeds}
    allowed_providers = [provider_only] if provider_only else ["claude", "codex"]
    if any(provider not in {"claude", "codex"} for provider in allowed_providers):
        raise Mix2KV4RepairError("teacher provider 선택이 잘못됐습니다.")
    environment = subscription_environment()
    auth = _auth_check(environment, allowed_providers)
    lock_path = target / ".pipeline.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4RepairError(
                "다른 repair teacher 실행이 진행 중입니다."
            ) from exc
        _, specs, seeds, state, _static_payloads = _load_target(target)
        specs_by_id = {row["id"]: row for row in specs}
        seeds_by_id = {row["record_id"]: row for row in seeds}
        calls_this_run = 0
        while max_provider_calls == 0 or calls_this_run < max_provider_calls:
            failed = [
                record_id
                for record_id in state["selection_order"]
                if state["records"][record_id]["status"] == "failed"
            ]
            if failed:
                raise Mix2KV4RepairError(
                    "영구 실패 repair row가 있습니다: " + ",".join(failed[:10])
                )
            selected_provider, selected_kind, selected_ids = _select_teacher_batch(
                state, allowed_providers
            )
            if selected_provider is None:
                incomplete = [
                    record_id
                    for record_id in state["selection_order"]
                    if state["records"][record_id]["status"] != "accepted"
                ]
                if incomplete:
                    break
                duplicate_repairs = _mark_duplicate_repairs(state, specs)
                if duplicate_repairs:
                    _atomic_write(target / "pipeline_state.json", _json_bytes(state))
                    continue
                teacher_manifest = _write_teacher_completion(
                    target, manifest, specs, seeds, state
                )
                return {
                    "complete": True,
                    "target": str(target),
                    "provider_calls_this_run": calls_this_run,
                    "provider_calls_total": state["provider_calls"],
                    "auth": auth,
                    "teacher_manifest": teacher_manifest,
                }

            batch_specs = [specs_by_id[record_id] for record_id in selected_ids]
            sequence = state["provider_calls"] + 1
            started = _utc_now()
            if selected_kind == "draft":
                feedback = {
                    record_id: state["records"][record_id]["feedback"]
                    for record_id in selected_ids
                }
                prompt = _repair_draft_prompt(batch_specs, feedback, seeds_by_id)
                result = _repair_provider_call(
                    provider=selected_provider,
                    prompt=prompt,
                    schema=_draft_schema(selected_ids),
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    model=(
                        config["teacher"]["claude_model"]
                        if selected_provider == "claude"
                        else None
                    ),
                )
                drafts = _result_map(
                    result["structured"], key="drafts", record_ids=selected_ids
                )
                for record_id in selected_ids:
                    record = state["records"][record_id]
                    raw = drafts[record_id]
                    normalized, layout = _normalize_draft_answer_layout(
                        specs_by_id[record_id], raw
                    )
                    normalized, particle = _normalize_draft_answer_particles(normalized)
                    validation_error = _draft_validation_error(
                        specs_by_id[record_id],
                        normalized,
                        seeds_by_id[record_id]["external_answer"],
                    )
                    record["draft_attempts"].append(
                        {
                            "provider_call_sequence": sequence,
                            "provider": selected_provider,
                            "started_at_utc": started,
                            "elapsed_seconds": result["elapsed_seconds"],
                            "provider_draft": raw,
                            "provider_draft_sha256": sha256_bytes(
                                canonical_json_bytes(raw)
                            ),
                            "normalized_draft_sha256": sha256_bytes(
                                canonical_json_bytes(normalized)
                            ),
                            "draft": normalized,
                            "layout_normalized": layout,
                            "particle_normalized": particle,
                            "deterministic_pass": validation_error is None,
                            "validation_error": validation_error,
                        }
                    )
                    if validation_error is None:
                        record["status"] = "needs_review"
                        record["current_draft"] = normalized
                        record["current_draft_provider"] = selected_provider
                        record["feedback"] = ""
                    else:
                        record["rewrites_used"] += 1
                        record["feedback"] = validation_error
                        if (
                            record["rewrites_used"]
                            > config["teacher"]["maximum_rewrite_rounds"]
                        ):
                            record["status"] = "failed"
            else:
                draft_map = {
                    record_id: state["records"][record_id]["current_draft"]
                    for record_id in selected_ids
                }
                prompt = _repair_review_prompt(batch_specs, draft_map)
                result = _repair_provider_call(
                    provider=selected_provider,
                    prompt=prompt,
                    schema=_review_schema(selected_ids),
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    model=(
                        config["teacher"]["claude_model"]
                        if selected_provider == "claude"
                        else None
                    ),
                )
                reviews = _result_map(
                    result["structured"], key="reviews", record_ids=selected_ids
                )
                for record_id in selected_ids:
                    record = state["records"][record_id]
                    review = reviews[record_id]
                    try:
                        validate_review(specs_by_id[record_id], review)
                    except Mix2KV4ContractError as exc:
                        raise Mix2KV4RepairError(str(exc)) from exc
                    record["review_attempts"].append(
                        {
                            "provider_call_sequence": sequence,
                            "provider": selected_provider,
                            "started_at_utc": started,
                            "elapsed_seconds": result["elapsed_seconds"],
                            "review": review,
                            "review_sha256": sha256_bytes(canonical_json_bytes(review)),
                            "reviewed_draft_sha256": sha256_bytes(
                                canonical_json_bytes(draft_map[record_id])
                            ),
                        }
                    )
                    if review["decision"] == "PASS":
                        if (
                            any(review[key] for key in ("failure_codes", "fact_errors"))
                            or review["rewrite_instructions"].strip()
                        ):
                            raise Mix2KV4RepairError(
                                f"PASS review에 실패 정보가 있습니다: {record_id}"
                            )
                        record["status"] = "accepted"
                        record["accepted"] = {
                            "draft_provider": record["current_draft_provider"],
                            "review_provider": selected_provider,
                            "draft": record["current_draft"],
                            "review": review,
                        }
                    else:
                        if not review["failure_codes"]:
                            raise Mix2KV4RepairError(
                                f"FAIL review에 failure code가 없습니다: {record_id}"
                            )
                        record["rewrites_used"] += 1
                        record["status"] = "needs_draft"
                        record["feedback"] = _state_feedback(review)
                        record["current_draft"] = None
                        record["current_draft_provider"] = None
                        record["accepted"] = None
                        if (
                            record["rewrites_used"]
                            > config["teacher"]["maximum_rewrite_rounds"]
                        ):
                            record["status"] = "failed"
            state["provider_calls"] = sequence
            state["provider_call_log"].append(
                {
                    "provider_call_sequence": sequence,
                    "provider_scope": list(allowed_providers),
                    "provider": selected_provider,
                    "kind": selected_kind,
                    "record_ids": list(selected_ids),
                    "started_at_utc": started,
                    "elapsed_seconds": result["elapsed_seconds"],
                    "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                    "provider_output": deepcopy(result["structured"]),
                    "provider_output_sha256": sha256_bytes(
                        canonical_json_bytes(result["structured"])
                    ),
                }
            )
            calls_this_run += 1
            _atomic_write(target / "pipeline_state.json", _json_bytes(state))

        counts = Counter(
            state["records"][record_id]["status"]
            for record_id in state["selection_order"]
        )
        return {
            "complete": False,
            "target": str(target),
            "provider_calls_this_run": calls_this_run,
            "provider_calls_total": state["provider_calls"],
            "status_counts": dict(sorted(counts.items())),
            "auth": auth,
        }


def _draft_from_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    teacher = row.get("teacher")
    if not isinstance(teacher, Mapping):
        raise Mix2KV4RepairError(f"teacher provenance가 없습니다: {row.get('id')}")
    return {
        "record_id": row.get("id"),
        "answer": row.get("assistant"),
        "used_fact_paths": teacher.get("used_fact_paths"),
        "used_fact_values": teacher.get("used_fact_values"),
        "soft_interpretation_used": teacher.get("soft_interpretation_used"),
        "limitations": teacher.get("limitations"),
        "self_check": "PASS",
    }


def _validate_combined_candidates(
    *,
    candidates: Sequence[dict[str, Any]],
    specs: Sequence[dict[str, Any]],
    parent_config: Mapping[str, Any],
) -> dict[str, Any]:
    if len(candidates) != EXPECTED_ROWS or len(specs) != EXPECTED_ROWS:
        raise Mix2KV4RepairError("결합 candidate와 spec은 각각 2,000행이어야 합니다.")
    specs_by_id = {row["id"]: row for row in specs}
    if len(specs_by_id) != EXPECTED_ROWS:
        raise Mix2KV4RepairError("결합 spec ID가 중복됐습니다.")
    seen: set[str] = set()
    answers: list[str] = []
    origins: Counter[str] = Counter()
    cross_provider_repaired = 0
    for row in candidates:
        record_id = row.get("id")
        spec = specs_by_id.get(record_id)
        teacher = row.get("teacher")
        origin = row.get("repair_origin")
        expected_snapshot = (
            spec["runtime_binding"]["snapshot_sha256"]
            if spec is not None and spec["runtime_binding"] is not None
            else None
        )
        if (
            spec is None
            or not isinstance(record_id, str)
            or record_id in seen
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
            or not isinstance(teacher, Mapping)
            or teacher.get("deterministic_validation") != "PASS"
            or origin not in {"parent_v1.0.1", "regenerated_v1.1.0"}
        ):
            raise Mix2KV4RepairError(f"결합 candidate identity가 다릅니다: {record_id}")
        if origin == "regenerated_v1.1.0":
            if (
                spec["task_axis"] not in REPAIR_AXES
                or teacher.get("peer_review") != "PASS"
                or teacher.get("drafter") != spec["drafter"]
                or teacher.get("reviewer") != spec["reviewer"]
                or teacher.get("drafter") == teacher.get("reviewer")
                or _repair_answer_error(spec, row["assistant"]) is not None
            ):
                raise Mix2KV4RepairError(
                    f"재생성 candidate 교차 검수 계약이 다릅니다: {record_id}"
                )
            cross_provider_repaired += 1
        elif spec["task_axis"] in REPAIR_AXES:
            raise Mix2KV4RepairError(
                f"repair axis가 부모 답변을 유지했습니다: {record_id}"
            )
        try:
            validate_draft(spec, _draft_from_candidate(row))
        except Mix2KV4ContractError as exc:
            raise Mix2KV4RepairError(str(exc)) from exc
        origins[origin] += 1
        seen.add(record_id)
        answers.append(row["assistant"].strip())
    if seen != set(specs_by_id):
        raise Mix2KV4RepairError("결합 candidate ID 집합이 spec과 다릅니다.")
    exact = Counter(answers)
    normalized = Counter(normalize_answer(answer) for answer in answers)
    exact_duplicates = sum(count - 1 for count in exact.values() if count > 1)
    normalized_maximum = max(normalized.values(), default=0)
    if (
        exact_duplicates
        > int(parent_config["diversity"]["exact_duplicate_answers_maximum"])
        or normalized_maximum
        > int(parent_config["diversity"]["normalized_answer_multiplicity_maximum"])
        or origins != {"parent_v1.0.1": 1600, "regenerated_v1.1.0": 400}
        or cross_provider_repaired != 400
    ):
        raise Mix2KV4RepairError("결합 candidate 분포·다양성 계약이 다릅니다.")
    return {
        "rows": EXPECTED_ROWS,
        "axes": dict(sorted(Counter(row["task_axis"] for row in candidates).items())),
        "origins": dict(sorted(origins.items())),
        "repaired_cross_provider_pass_rows": cross_provider_repaired,
        "all_2000_rows_cross_provider_contract_met": False,
        "exact_duplicate_answers": exact_duplicates,
        "normalized_answer_multiplicity_maximum": normalized_maximum,
    }


def _combine_candidates(
    *,
    specs: Sequence[dict[str, Any]],
    parent_candidates: Sequence[dict[str, Any]],
    repaired_candidates: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent_by_id = {row["id"]: row for row in parent_candidates}
    repaired_by_id = {row["id"]: row for row in repaired_candidates}
    if len(parent_by_id) != EXPECTED_ROWS or len(repaired_by_id) != 400:
        raise Mix2KV4RepairError("부모·repair candidate ID 집합 크기가 다릅니다.")
    combined: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for spec in specs:
        record_id = spec["id"]
        repair_required = spec["task_axis"] in REPAIR_AXES
        source = (
            repaired_by_id.get(record_id)
            if repair_required
            else parent_by_id.get(record_id)
        )
        if source is None or (not repair_required and record_id in repaired_by_id):
            raise Mix2KV4RepairError(f"candidate source 선택이 다릅니다: {record_id}")
        candidate = deepcopy(source)
        candidate["schema_version"] = "1.1.0"
        candidate["prompt"] = deepcopy(spec["prompt"])
        candidate["repair_origin"] = (
            "regenerated_v1.1.0" if repair_required else "parent_v1.0.1"
        )
        combined.append(candidate)
        provenance.append(
            {
                "schema_version": "1.0.0",
                "record_id": record_id,
                "task_axis": spec["task_axis"],
                "origin": candidate["repair_origin"],
                "assistant_sha256": sha256_bytes(
                    candidate["assistant"].encode("utf-8")
                ),
                "prompt_sha256": sha256_bytes(
                    canonical_json_bytes(candidate["prompt"])
                ),
                "cross_provider_pass": repair_required,
                "external_answer_adopted": False,
            }
        )
    if set(repaired_by_id) != {
        row["id"] for row in specs if row["task_axis"] in REPAIR_AXES
    }:
        raise Mix2KV4RepairError("repair candidate가 고정 400행과 일치하지 않습니다.")
    return combined, provenance


def _provenance_token_ab(
    *,
    new_audits: Sequence[dict[str, Any]],
    parent_audits: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    parent_by_id = {row["id"]: row for row in parent_audits}
    deltas: list[int] = []
    prompt_deltas: list[int] = []
    assistant_deltas: list[int] = []
    changed_prompt_rows = 0
    changed_assistant_rows = 0
    for row in new_audits:
        parent = parent_by_id.get(row["id"])
        if parent is None:
            raise Mix2KV4RepairError("부모 token audit ID가 없습니다.")
        rendered_delta = row["rendered_tokens"] - parent["rendered_tokens"]
        prompt_delta = row["prompt_tokens"] - parent["prompt_tokens"]
        assistant_delta = (
            row["supervised_assistant_tokens"] - parent["supervised_assistant_tokens"]
        )
        deltas.append(rendered_delta)
        prompt_deltas.append(prompt_delta)
        assistant_deltas.append(assistant_delta)
        changed_prompt_rows += int(prompt_delta != 0)
        changed_assistant_rows += int(assistant_delta != 0)
    return {
        "schema_version": "1.0.0",
        "comparison": "repo_native_v1.1.0_minus_parent_v1.0.1",
        "rows": len(new_audits),
        "changed_prompt_token_rows": changed_prompt_rows,
        "changed_assistant_token_rows": changed_assistant_rows,
        "rendered_token_delta_total": sum(deltas),
        "prompt_token_delta_total": sum(prompt_deltas),
        "supervised_assistant_token_delta_total": sum(assistant_deltas),
        "audit_provenance_removed_from_model_context": False,
        "compact_projection_used": False,
        "production_like_format_preserved": True,
    }


def finalize(
    *,
    config_path: Path,
    target: Path,
    parent_spec_build: Path,
    parent_final_build: Path,
    parent_teacher_build: Path,
    tokenizer_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    config, parent_config, config_payload, _prompt_texts = _load_repair_config(
        config_path
    )
    manifest, specs, seeds, state, static_payloads = _load_target(target)
    (
        _teacher_manifest,
        repaired_candidates,
        teacher_manifest_payload,
        repaired_payload,
    ) = _load_teacher_completion(target, manifest, specs, seeds, state)
    if manifest.get("identity", {}).get("config_sha256") != sha256_bytes(
        config_payload
    ):
        raise Mix2KV4RepairError("repair teacher 완료 hash chain이 다릅니다.")
    (
        _parent_specs,
        _parent_train,
        parent_candidates,
        parent_audits,
        _parent_dev_payload,
    ) = _validate_parent_inputs(
        config=config,
        parent_spec_build=parent_spec_build,
        parent_final_build=parent_final_build,
        parent_teacher_build=parent_teacher_build,
    )
    expected_reservations = _inherited_answer_reservations(
        specs=specs,
        parent_candidates=parent_candidates,
        parent_config=parent_config,
    )
    if (
        manifest.get("identity", {}).get("inherited_answer_reservations")
        != expected_reservations
    ):
        raise Mix2KV4RepairError("상속 답변 중복 예약 원본이 다릅니다.")
    model_files = _validate_model_snapshot(tokenizer_path, parent_config)
    combined, provenance = _combine_candidates(
        specs=specs,
        parent_candidates=parent_candidates,
        repaired_candidates=repaired_candidates,
    )
    candidate_validation = _validate_combined_candidates(
        candidates=combined,
        specs=specs,
        parent_config=parent_config,
    )
    audits, audit_summary = _token_audit(combined, tokenizer_path, parent_config)
    if (
        audit_summary["selected_max_length"]
        != config["token_budget"]["preferred_training_max_length"]
        or audit_summary["truncated_rows"] != 0
        or audit_summary["zero_assistant_mask_rows"] != 0
        or audit_summary["missing_supervised_eos_rows"] != 0
        or audit_summary["user_system_loss_leakage_rows"] != 0
    ):
        raise Mix2KV4RepairError("2,048 token 무절단 학습 gate를 통과하지 못했습니다.")
    token_ab = _provenance_token_ab(
        new_audits=audits,
        parent_audits=parent_audits,
    )
    training_rows = [
        {
            "schema_version": "1.1.0",
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
        for row in combined
    ]
    artifacts = {
        "training/train_2000.jsonl": jsonl_bytes(training_rows),
        "provenance/combined_candidates_2000.jsonl": jsonl_bytes(combined),
        "provenance/row_lineage_2000.jsonl": jsonl_bytes(provenance),
        "reports/token_audit_2000.jsonl": jsonl_bytes(audits),
        "reports/token_audit_summary.json": _json_bytes(
            {
                **audit_summary,
                "candidate_validation": candidate_validation,
                "parent_comparison": token_ab,
            }
        ),
        "reports/package_audit.json": static_payloads["reports/package_audit.json"],
        "reports/lineage_summary.json": static_payloads["reports/lineage_summary.json"],
        "evaluation/dev_cases_200.jsonl": static_payloads[
            "evaluation/dev_cases_200.jsonl"
        ],
    }
    artifact_hashes = {
        relative: sha256_bytes(payload) for relative, payload in artifacts.items()
    }
    identity = {
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "config_sha256": sha256_bytes(config_payload),
        "generator_sha256": manifest["identity"]["generator_sha256"],
        "source_dependency_sha256": manifest["identity"]["source_dependency_sha256"],
        "prepare_target_sha256": manifest["target_sha256"],
        "repair_teacher_manifest_sha256": sha256_bytes(teacher_manifest_payload),
        "repair_candidates_sha256": sha256_bytes(repaired_payload),
        "parent_final_build_sha256": config["parent"]["final_build_sha256"],
        "parent_teacher_candidates_sha256": config["parent"][
            "teacher_candidates_sha256"
        ],
        "review_package_sha256": config["review_package"]["sha256"],
        "base_model_files": model_files,
        "artifact_sha256": artifact_hashes,
    }
    build_sha256 = sha256_bytes(canonical_json_bytes(identity))
    build_id = f"build-{build_sha256[:12]}"
    final_manifest = {
        "schema_version": "1.1.0",
        "dataset_version": DATASET_VERSION,
        "artifact_revision": "v1.1.0",
        "build_id": build_id,
        "build_sha256": build_sha256,
        "identity": identity,
        "rows": EXPECTED_ROWS,
        "axes": candidate_validation["axes"],
        "selected_max_length": audit_summary["selected_max_length"],
        "assistant_only_loss": True,
        "truncation": False,
        "full_runtime_snapshot_used": True,
        "compact_projection_used_for_training": False,
        "inherited_parent_rows": 1600,
        "regenerated_cross_provider_rows": 400,
        "all_2000_rows_cross_provider_contract_met": False,
        "development_targets_accessed": False,
        "sealed_blind_accessed": False,
        "training_execution_allowed": True,
        "lora_r16_experimental_training_allowed": True,
        "training_performed": False,
        "production_promotion_allowed": False,
        "artifact_sha256": artifact_hashes,
    }
    files = {**artifacts, "build_manifest.json": _json_bytes(final_manifest)}
    output_root = output_root.resolve()
    target_path, mode = _atomic_build(output_root, build_id, files)
    return {**final_manifest, "mode": mode, "path": str(target_path)}


def status(target: Path) -> dict[str, Any]:
    manifest, specs, seeds, state, _static_payloads = _load_target(target)
    counts = Counter(
        state["records"][record_id]["status"] for record_id in state["selection_order"]
    )
    completion_paths = (
        target / "teacher_manifest.json",
        target / "accepted/repaired_candidates_400.jsonl",
    )
    completion_present = any(
        path.exists() or path.is_symlink() for path in completion_paths
    )
    teacher_generation_complete = False
    if counts == {"accepted": 400} and completion_present:
        _load_teacher_completion(target, manifest, specs, seeds, state)
        teacher_generation_complete = True
    return {
        "target": str(target),
        "target_sha256": manifest["target_sha256"],
        "provider_calls_total": state["provider_calls"],
        "status_counts": dict(sorted(counts.items())),
        "teacher_generation_complete": teacher_generation_complete,
        "production_promotion_allowed": False,
    }


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="외부 MIX2K 검토안을 repo-native v1.1.0으로 선별 정본화"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit-package")
    audit.add_argument("--package", type=Path, required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--package", type=Path, required=True)
    prepare_parser.add_argument("--parent-spec-build", type=Path, required=True)
    prepare_parser.add_argument("--parent-final-build", type=Path, required=True)
    prepare_parser.add_argument("--parent-teacher-build", type=Path, required=True)
    prepare_parser.add_argument("--output-root", type=Path, default=DEFAULT_WORK_ROOT)

    teachers = subparsers.add_parser("run-teachers")
    teachers.add_argument("--target", type=Path, required=True)
    teachers.add_argument("--provider-only", choices=("claude", "codex"))
    teachers.add_argument("--timeout-seconds", type=int, default=1200)
    teachers.add_argument("--max-provider-calls", type=int, default=0)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--target", type=Path, required=True)

    final = subparsers.add_parser("finalize")
    final.add_argument("--target", type=Path, required=True)
    final.add_argument("--parent-spec-build", type=Path, required=True)
    final.add_argument("--parent-final-build", type=Path, required=True)
    final.add_argument("--parent-teacher-build", type=Path, required=True)
    final.add_argument("--tokenizer", type=Path, required=True)
    final.add_argument("--output-root", type=Path, default=DEFAULT_FINAL_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = _absolute(args.config)
    try:
        if args.command == "audit-package":
            config, _parent, _config_payload, _prompt_texts = _load_repair_config(
                config_path
            )
            report = audit_package(_absolute(args.package), config)
        elif args.command == "prepare":
            report = prepare(
                config_path=config_path,
                package_path=_absolute(args.package),
                parent_spec_build=_absolute(args.parent_spec_build),
                parent_final_build=_absolute(args.parent_final_build),
                parent_teacher_build=_absolute(args.parent_teacher_build),
                output_root=_absolute(args.output_root),
            )
        elif args.command == "run-teachers":
            if args.timeout_seconds < 60 or args.max_provider_calls < 0:
                raise Mix2KV4RepairError("teacher timeout·call 상한이 잘못됐습니다.")
            report = run_teachers(
                config_path=config_path,
                target=_absolute(args.target),
                provider_only=args.provider_only,
                timeout_seconds=args.timeout_seconds,
                max_provider_calls=args.max_provider_calls,
            )
        elif args.command == "status":
            report = status(_absolute(args.target))
        else:
            report = finalize(
                config_path=config_path,
                target=_absolute(args.target),
                parent_spec_build=_absolute(args.parent_spec_build),
                parent_final_build=_absolute(args.parent_final_build),
                parent_teacher_build=_absolute(args.parent_teacher_build),
                tokenizer_path=_absolute(args.tokenizer),
                output_root=_absolute(args.output_root),
            )
    except (
        Mix2KV4RepairError,
        Mix2KV4BuildError,
        Mix2KV4ContractError,
        Mix2KV4FinalizeError,
        Mix2KV4TeacherError,
        OSError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "run-teachers" and report.get("complete") is not True:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
