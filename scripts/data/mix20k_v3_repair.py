# mix20k_v3_repair.py - MIX20K-v3 외부 후보를 감사하고 불변 보정 후보·검수 큐를 만든다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.runtime.saju_contract import (
    CALCULATION_POLICY_ID,
    MODEL_VISIBLE_TOOL_RESULT_FIELDS,
    TOOL_SCHEMA_VERSION,
    SajuContractError,
    birth_input_fingerprint,
    canonical_json_bytes,
    empty_session_state,
    load_tool_schema,
    project_model_visible_tool_result,
    resolve_relative_period,
    sha256_json,
    tool_by_name,
    validate_argument_provenance,
    validate_session_state,
    validate_tool_arguments,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/mix20k-v3-repair-v1.0.0.json"
)
REVIEW_FILENAME = "mix20k_v3_review_with_metadata.jsonl"
TRAINING_FILENAME = "training_mix20k_v3_candidate.jsonl"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ISO_DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
RELATIVE_DATE_PATTERN = re.compile(
    r"(?P<value>오늘|내일|이번 주말|다음 주말|이번 주|다음 주|이번 달|다음 달|올해|내년)"
)
FACT_TOKEN_PATTERN = re.compile(
    r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]"
    r"|[甲乙丙丁戊己庚辛壬癸]"
    r"|비견|겁재|식신|상관|편재|정재|편관|정관|편인|정인"
)
HANJA_PARTICLE_PATTERN = re.compile(
    r"(?P<word>[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]{1,2})"
    r"(?P<particle>으로|은|는|이|가|을|를|과|와|로)"
)
HANJA_ENDING = {
    "甲": "consonant",
    "乙": "rieul",
    "丙": "consonant",
    "丁": "consonant",
    "戊": "vowel",
    "己": "vowel",
    "庚": "consonant",
    "辛": "consonant",
    "壬": "consonant",
    "癸": "vowel",
    "子": "vowel",
    "丑": "consonant",
    "寅": "consonant",
    "卯": "vowel",
    "辰": "consonant",
    "巳": "vowel",
    "午": "vowel",
    "未": "vowel",
    "申": "consonant",
    "酉": "vowel",
    "戌": "rieul",
    "亥": "vowel",
}
PARTICLE_PAIR = {
    "은": ("은", "는"),
    "는": ("은", "는"),
    "이": ("이", "가"),
    "가": ("이", "가"),
    "을": ("을", "를"),
    "를": ("을", "를"),
    "과": ("과", "와"),
    "와": ("과", "와"),
}
COUNTRY_BY_TIMEZONE = {
    "Asia/Seoul": "KR",
    "Asia/Tokyo": "JP",
    "Asia/Singapore": "SG",
    "Australia/Sydney": "AU",
    "America/New_York": "US",
    "America/Los_Angeles": "US",
    "America/Vancouver": "CA",
    "Europe/Paris": "FR",
    "Europe/London": "GB",
}
EXPERT_TERMS = ("신강약", "격국", "용신", "희신", "기신")


class Mix20KV3Error(RuntimeError):
    """v3 package·보정 build 계약 위반."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise Mix20KV3Error(f"파일 SHA-256을 계산할 수 없습니다: {path}") from exc
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix20KV3Error(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Mix20KV3Error(f"{label}의 최상위는 object여야 합니다.")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Mix20KV3Error(f"{label} {line_number}행이 비었습니다.")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Mix20KV3Error(f"{label} {line_number}행은 object여야 합니다.")
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Mix20KV3Error):
            raise
        raise Mix20KV3Error(f"{label} JSONL을 읽을 수 없습니다: {path}") from exc
    return values


def _safe_regular_file(path: Path, label: str) -> Path:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise Mix20KV3Error(f"{label}을 찾을 수 없습니다: {path}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or stat.S_ISLNK(file_stat.st_mode):
        raise Mix20KV3Error(f"{label}은 symlink가 아닌 일반 파일이어야 합니다.")
    return path.resolve()


def _safe_directory(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    try:
        directory_stat = candidate.lstat()
    except OSError as exc:
        raise Mix20KV3Error(f"{label}을 찾을 수 없습니다: {candidate}") from exc
    if not stat.S_ISDIR(directory_stat.st_mode) or stat.S_ISLNK(directory_stat.st_mode):
        raise Mix20KV3Error(f"{label}은 symlink가 아닌 디렉터리여야 합니다.")
    return candidate.resolve()


def _resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or value != path.as_posix() or ".." in path.parts:
        raise Mix20KV3Error(f"안전하지 않은 저장소 상대경로입니다: {value}")
    resolved = (REPO_ROOT / path).resolve(strict=False)
    if not resolved.is_relative_to(REPO_ROOT.resolve()):
        raise Mix20KV3Error("저장소 밖 경로를 허용하지 않습니다.")
    return resolved


def _write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any, *, mode: int) -> None:
    _write_bytes(path, _json_bytes(value), mode=mode)


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]], *, mode: int) -> None:
    payload = b"".join(canonical_json_bytes(value) + b"\n" for value in values)
    _write_bytes(path, payload, mode=mode)


def _replace_directory(temporary: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise Mix20KV3Error(f"기존 불변 산출물 경로가 있습니다: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, destination)


def _config(path: Path) -> dict[str, Any]:
    config = _load_json(
        _safe_regular_file(path, "v3 repair config"), "v3 repair config"
    )
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("repair_version") != "v3.0.1-repaired"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("governance", {}).get("full_training_execution_enabled")
        is not False
        or config.get("governance", {}).get("diagnostic_training_execution_enabled")
        is not False
        or config.get("governance", {}).get("training_promotion_allowed") is not False
        or config.get("governance", {}).get("sealed_blind_payload_read_allowed")
        is not False
    ):
        raise Mix20KV3Error("v3 repair identity·비학습 governance가 다릅니다.")
    runtime = config.get("runtime_contract", {})
    if (
        runtime.get("calculation_policy_id") != CALCULATION_POLICY_ID
        or runtime.get("tool_schema_version") != TOOL_SCHEMA_VERSION
    ):
        raise Mix20KV3Error("runtime policy/tool schema version이 다릅니다.")
    for key in (
        "production_prompt",
        "tool_schema",
        "session_state_schema",
        "calculation_policy",
        "relative_date_policy",
    ):
        _safe_regular_file(_resolve_repo_path(str(runtime.get(key, ""))), key)
    model = config.get("model", {})
    if (
        model.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or model.get("max_length") != 768
        or model.get("assistant_only_loss") is not True
        or model.get("target_policy") != "last_user_suffix"
        or _sha256_file(_resolve_repo_path(str(model.get("chat_template", ""))))
        != model.get("chat_template_sha256")
    ):
        raise Mix20KV3Error("Kanana tokenizer/template 계약이 다릅니다.")
    load_tool_schema(_resolve_repo_path(runtime["tool_schema"]))
    return config


def _relative_member(root: Path, value: str) -> Path:
    relative = Path(value)
    if (
        relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or CONTROL_PATTERN.search(value)
    ):
        raise Mix20KV3Error(f"패키지 내부 경로가 안전하지 않습니다: {value!r}")
    path = root.joinpath(*relative.parts)
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise Mix20KV3Error("패키지 경로가 root를 벗어납니다.")
    return _safe_regular_file(path, value)


def verify_source_package(package_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    root = _safe_directory(package_dir, "MIX20K-v3 package")
    source = config["source_package"]
    fixed_hashes = {
        "build_manifest.json": source["build_manifest_sha256"],
        "SHA256SUMS": source["sha256sums_sha256"],
        source["review_file"]: source["review_sha256"],
        source["training_file"]: source["training_sha256"],
    }
    for name, expected in fixed_hashes.items():
        if not SHA256_PATTERN.fullmatch(str(expected)):
            raise Mix20KV3Error(f"고정 SHA-256 계약이 잘못됐습니다: {name}")
        actual = _sha256_file(_relative_member(root, name))
        if actual != expected:
            raise Mix20KV3Error(f"고정 package hash가 다릅니다: {name}")

    checksum_path = _relative_member(root, "SHA256SUMS")
    checksum_entries: dict[str, str] = {}
    try:
        checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Mix20KV3Error("SHA256SUMS를 읽을 수 없습니다.") from exc
    for line in checksum_lines:
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if match is None or match.group(2) in checksum_entries:
            raise Mix20KV3Error("SHA256SUMS 행 계약이 잘못됐습니다.")
        checksum_entries[match.group(2)] = match.group(1)
    failures: list[str] = []
    for name, expected in sorted(checksum_entries.items()):
        if _sha256_file(_relative_member(root, name)) != expected:
            failures.append(name)
    if failures:
        raise Mix20KV3Error(f"패키지 checksum이 다릅니다: {failures}")

    manifest = _load_json(
        _relative_member(root, "build_manifest.json"), "source manifest"
    )
    if (
        manifest.get("dataset_version") != source["dataset_version"]
        or manifest.get("metrics", {}).get("rows") != source["review_rows"]
        or manifest.get("status", {}).get("production_training_promotion")
        != "not_approved"
    ):
        raise Mix20KV3Error("source manifest identity·promotion 계약이 다릅니다.")
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "package_directory_name": root.name,
        "checksum_entries": len(checksum_entries),
        "checksum_failures": 0,
        "review_rows_expected": source["review_rows"],
        "training_rows_expected": source["training_rows"],
        "review_sha256": source["review_sha256"],
        "training_sha256": source["training_sha256"],
        "build_manifest_sha256": source["build_manifest_sha256"],
        "sha256sums_sha256": source["sha256sums_sha256"],
        "source_package_modified": False,
        "raw_samples_in_report": False,
    }


def _assistant_turns(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(message.get("role") == "assistant" for message in messages)


def _validate_message_structure(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    messages = row.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        return ["messages_invalid"]
    if messages[0].get("role") != "system" or messages[-1].get("role") != "assistant":
        errors.append("message_boundary_invalid")
    declared = {
        tool.get("function", {}).get("name")
        for tool in row.get("tools", [])
        if isinstance(tool, dict)
    }
    pending_calls: Counter[str] = Counter()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            errors.append("message_role_invalid")
            continue
        content = message.get("content")
        calls = message.get("tool_calls", [])
        if not isinstance(content, str) or CONTROL_PATTERN.search(content):
            errors.append("message_content_invalid")
        if message.get("role") == "assistant":
            if not content.strip() and not calls:
                errors.append("empty_assistant_without_tool")
            for call in calls:
                name = call.get("function", {}).get("name")
                if name not in declared:
                    errors.append("undeclared_tool_call")
                pending_calls[str(name)] += 1
        elif calls:
            errors.append("tool_calls_on_non_assistant")
        if message.get("role") == "tool":
            name = str(message.get("name"))
            if pending_calls[name] <= 0:
                errors.append("tool_result_without_call")
            else:
                pending_calls[name] -= 1
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                errors.append("tool_result_invalid_json")
            else:
                if not isinstance(parsed, dict):
                    errors.append("tool_result_not_object")
    return errors


def _normalized_signature(row: Mapping[str, Any]) -> str:
    messages = []
    for message in row["messages"]:
        value = {
            "role": message["role"],
            "content": " ".join(message.get("content", "").split()),
        }
        if message.get("tool_calls"):
            value["tool_calls"] = message["tool_calls"]
        messages.append(value)
    return sha256_json(messages)


def _source_text_before_call(messages: Sequence[Mapping[str, Any]], index: int) -> str:
    return "\n".join(
        message.get("content", "")
        for message in messages[:index]
        if message.get("role") in {"system", "user"}
    )


def _missing_explicit_chart_values(
    arguments: Mapping[str, Any], source: str
) -> list[str]:
    missing: list[str] = []
    checks = {
        "birth_date": arguments.get("birth_date"),
        "birth_time": arguments.get("birth_time"),
        "birthplace": arguments.get("birthplace"),
        "gender_for_daeun": arguments.get("gender_for_daeun"),
    }
    for key, value in checks.items():
        if value is None or value == "unspecified":
            continue
        if isinstance(value, Mapping):
            value = value.get("city")
        if isinstance(value, str) and value not in source:
            missing.append(key)
    return missing


def repair_hanja_particles(text: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        word = match.group("word")
        particle = match.group("particle")
        ending = HANJA_ENDING[word[-1]]
        if particle in {"로", "으로"}:
            expected = "로" if ending in {"vowel", "rieul"} else "으로"
        else:
            consonant, vowel = PARTICLE_PAIR[particle]
            expected = vowel if ending == "vowel" else consonant
        if expected != particle:
            count += 1
        return f"{word}{expected}"

    return HANJA_PARTICLE_PATTERN.sub(replace, text), count


def audit_rows(
    rows: list[dict[str, Any]],
    training_rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    expected_rows = config["source_package"]["review_rows"]
    if len(rows) != expected_rows or len(training_rows) != expected_rows:
        raise Mix20KV3Error("외부 후보 review/training은 각각 20,000행이어야 합니다.")
    ids: set[str] = set()
    axes = Counter()
    promotions = Counter()
    authorities = Counter()
    sources = Counter()
    structures = Counter()
    projection_mismatches = 0
    tool_names = Counter()
    tool_rows = 0
    tool_result_rows = 0
    old_policy_rows: set[str] = set()
    ungrounded_chart_by_axis = Counter()
    ungrounded_chart_rows: set[str] = set()
    ungrounded_period_by_axis = Counter()
    relative_period_rows: set[str] = set()
    cached_false_hard_gt = 0
    particle_errors_by_axis = Counter()
    why_evidence_contradictions = 0
    heuristic_hard_gt_rows = 0
    duplicate_groups: dict[str, Counter[str]] = defaultdict(Counter)
    restricted_rows = 0
    restricted_by_source = Counter()
    source_refs_missing = 0
    errors = Counter()

    for index, (row, training) in enumerate(zip(rows, training_rows, strict=True), 1):
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id or row_id in ids:
            errors["duplicate_or_invalid_id"] += 1
        else:
            ids.add(row_id)
        axis = str(row.get("task_axis"))
        axes[axis] += 1
        promotions[str(row.get("promotion_status"))] += 1
        authorities[str(row.get("fact_authority"))] += 1
        source_name = str(row.get("source"))
        sources[source_name] += 1
        if source_name in config["restricted_sources"]:
            restricted_rows += 1
            restricted_by_source[source_name] += 1
        if not row.get("source_refs"):
            source_refs_missing += 1
        row_errors = _validate_message_structure(row)
        errors.update(row_errors)
        roles = ">".join(
            message.get("role", "?") for message in row.get("messages", [])
        )
        structures[roles] += 1
        if training.get("messages") != row.get("messages") or training.get(
            "tools"
        ) != row.get("tools"):
            projection_mismatches += 1
        signature = _normalized_signature(row)
        duplicate_groups[axis][signature] += 1
        row_has_call = False
        row_has_result = False
        messages = row["messages"]
        for message_index, message in enumerate(messages):
            if message.get("role") == "tool":
                row_has_result = True
            for call in message.get("tool_calls", []):
                row_has_call = True
                function = call.get("function", {})
                name = function.get("name")
                arguments = function.get("arguments")
                tool_names[str(name)] += 1
                if not isinstance(arguments, Mapping):
                    errors["tool_arguments_not_object"] += 1
                    continue
                source_text = _source_text_before_call(messages, message_index)
                if name == "calculate_saju_chart":
                    missing = _missing_explicit_chart_values(arguments, source_text)
                    if missing:
                        ungrounded_chart_rows.add(str(row_id))
                        ungrounded_chart_by_axis[axis] += 1
                elif name == "calculate_saju_period":
                    dates = [arguments.get("start_date"), arguments.get("end_date")]
                    if any(value and str(value) not in source_text for value in dates):
                        ungrounded_period_by_axis[axis] += 1
                    if RELATIVE_DATE_PATTERN.search(source_text):
                        relative_period_rows.add(str(row_id))
                if arguments.get("policy_version") == "kr-saju-v1":
                    old_policy_rows.add(str(row_id))
        if row_has_call:
            tool_rows += 1
        if row_has_result:
            tool_result_rows += 1
        if (
            source_name == "synthetic_cached_tool_result"
            and row.get("fact_authority") == "HARD_GT"
        ):
            cached_false_hard_gt += 1
        particle_count = 0
        for message in messages:
            if message.get("role") == "assistant":
                _, fixed = repair_hanja_particles(message.get("content", ""))
                particle_count += fixed
        if particle_count:
            particle_errors_by_axis[axis] += particle_count
        if axis == "stateful_followup" and len(messages) >= 5:
            final_user = messages[-2].get("content", "")
            if "왜" in final_user:
                previous = "\n".join(
                    message.get("content", "")
                    for message in messages[:-2]
                    if message.get("role") == "assistant"
                )
                final_tokens = set(
                    FACT_TOKEN_PATTERN.findall(messages[-1].get("content", ""))
                )
                if final_tokens - set(FACT_TOKEN_PATTERN.findall(previous)):
                    why_evidence_contradictions += 1
        assistant_text = "\n".join(
            message.get("content", "")
            for message in messages
            if message.get("role") == "assistant"
        )
        if row.get("fact_authority") == "HARD_GT" and any(
            term in assistant_text for term in EXPERT_TERMS
        ):
            heuristic_hard_gt_rows += 1
        if index % 5000 == 0:
            print(
                f"audit_progress={index}/{expected_rows}", file=sys.stderr, flush=True
            )

    if dict(axes) != config["repair"]["expected_axis_counts"]:
        raise Mix20KV3Error(f"축별 수량이 계약과 다릅니다: {dict(axes)}")
    if errors:
        raise Mix20KV3Error(f"구조 감사가 실패했습니다: {dict(errors)}")
    duplicate_report: dict[str, Any] = {}
    for axis, signatures in sorted(duplicate_groups.items()):
        participating = sum(count for count in signatures.values() if count > 1)
        duplicate_report[axis] = {
            "rows": axes[axis],
            "unique_signatures": len(signatures),
            "participating_rows": participating,
            "max_multiplicity": max(signatures.values()),
        }
    return {
        "semantic_audit": {
            "schema_version": "1.0.0",
            "status": "repair_required",
            "rows": len(rows),
            "axis_counts": dict(axes),
            "assistant_turn_distribution": dict(
                sorted(
                    Counter(_assistant_turns(row["messages"]) for row in rows).items()
                )
            ),
            "ungrounded_chart_argument_rows": len(ungrounded_chart_rows),
            "ungrounded_chart_argument_rows_by_axis": dict(ungrounded_chart_by_axis),
            "ungrounded_period_argument_rows_by_axis": dict(ungrounded_period_by_axis),
            "relative_period_rows_without_runtime_reference": len(relative_period_rows),
            "stateful_why_new_evidence_rows": why_evidence_contradictions,
            "obvious_hanja_particle_errors": sum(particle_errors_by_axis.values()),
            "obvious_hanja_particle_errors_by_axis": dict(particle_errors_by_axis),
            "heuristic_terms_in_hard_gt_rows": heuristic_hard_gt_rows,
            "raw_samples_in_report": False,
        },
        "provenance_audit": {
            "schema_version": "1.0.0",
            "status": "repair_required",
            "source_counts": dict(sources),
            "fact_authority_counts": dict(authorities),
            "promotion_status_counts": dict(promotions),
            "source_refs_missing_rows": source_refs_missing,
            "old_or_unregistered_policy_rows": len(old_policy_rows),
            "synthetic_cached_rows_mislabeled_hard_gt": cached_false_hard_gt,
            "restricted_rows": restricted_rows,
            "restricted_rows_by_source": dict(restricted_by_source),
            "external_queue_must_exclude_restricted": True,
            "raw_samples_in_report": False,
        },
        "duplicate_audit": {
            "schema_version": "1.0.0",
            "status": "measured",
            "signature": "normalized_messages_with_structured_tool_calls_sha256",
            "axes": duplicate_report,
            "raw_samples_in_report": False,
        },
        "tool_audit": {
            "schema_version": "1.0.0",
            "status": "repair_required",
            "rows_with_tool_calls": tool_rows,
            "rows_with_tool_results": tool_result_rows,
            "tool_call_counts": dict(tool_names),
            "source_tool_schema_version": "unregistered_kr-saju-v1",
            "target_tool_schema_version": TOOL_SCHEMA_VERSION,
            "source_rows_with_argument_provenance": 0,
            "source_rows_with_old_policy_argument": len(old_policy_rows),
            "training_projection_mismatches": projection_mismatches,
            "raw_samples_in_report": False,
        },
    }


def _stable_rank(seed: str, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{seed}|{namespace}|{value}".encode()).hexdigest()


def _tool_arguments(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for call in message.get("tool_calls", []):
        function = call.get("function")
        if not isinstance(function, dict) or not isinstance(
            function.get("arguments"), dict
        ):
            raise Mix20KV3Error("tool call function/arguments가 object가 아닙니다.")
        values.append(function)
    return values


def _normalize_chart_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    timezone_name = str(arguments.get("timezone") or "Asia/Seoul")
    city_value = arguments.get("birthplace")
    if isinstance(city_value, Mapping):
        city = str(city_value.get("city") or "").strip()
        country_code = str(
            city_value.get("country_code")
            or COUNTRY_BY_TIMEZONE.get(timezone_name, "ZZ")
        )
        longitude = city_value.get("longitude")
        latitude = city_value.get("latitude")
    else:
        city = str(city_value or "").strip()
        country_code = COUNTRY_BY_TIMEZONE.get(timezone_name, "ZZ")
        longitude = None
        latitude = None
    if not city or country_code == "ZZ":
        raise Mix20KV3Error(f"출생지를 정규화할 수 없습니다: {city_value!r}")
    precision = str(arguments.get("time_precision"))
    birth_time = arguments.get("birth_time")
    time_range = arguments.get("time_range")
    if precision != "range":
        time_range = None
    result = {
        "birth_date": arguments.get("birth_date"),
        "calendar": arguments.get("calendar"),
        "leap_month": arguments.get("leap_month"),
        "birth_time": birth_time,
        "time_precision": precision,
        "time_range": time_range,
        "birthplace": {
            "country_code": country_code,
            "city": city,
            "timezone": timezone_name,
            "longitude": longitude,
            "latitude": latitude,
        },
        "gender_for_daeun": arguments.get("gender_for_daeun", "unspecified"),
    }
    validate_tool_arguments("calculate_saju_chart", result)
    return result


def _normalize_period_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "chart_id": arguments.get("chart_id"),
        "period_type": arguments.get("period_type"),
        "start_date": arguments.get("start_date"),
        "end_date": arguments.get("end_date"),
        "timezone": arguments.get("timezone"),
    }
    validate_tool_arguments("calculate_saju_period", result)
    return result


def _chart_argument_provenance(arguments: Mapping[str, Any]) -> dict[str, str]:
    provenance = {
        "birth_date": "user_explicit",
        "calendar": "user_explicit",
        "leap_month": (
            "deterministic_default"
            if arguments["calendar"] == "solar"
            else "user_explicit"
        ),
        "birth_time": "user_explicit",
        "time_precision": "user_explicit",
        "time_range": "deterministic_default",
        "birthplace.country_code": "runtime_normalized",
        "birthplace.city": "user_explicit",
        "birthplace.timezone": "runtime_normalized",
        "birthplace.longitude": "runtime_normalized",
        "birthplace.latitude": "runtime_normalized",
        "gender_for_daeun": "user_explicit",
    }
    if arguments["time_precision"] == "range":
        provenance.pop("time_range")
        provenance["time_range.start"] = "user_explicit"
        provenance["time_range.end"] = "user_explicit"
    validate_argument_provenance(arguments, provenance)
    return provenance


def _period_argument_provenance(arguments: Mapping[str, Any]) -> dict[str, str]:
    provenance = {
        "chart_id": "session_confirmed",
        "period_type": "runtime_normalized",
        "start_date": "runtime_normalized",
        "end_date": "runtime_normalized",
        "timezone": "runtime_normalized",
    }
    validate_argument_provenance(arguments, provenance)
    return provenance


def _gender_label(value: str) -> str:
    return {"male": "남성", "female": "여성", "unspecified": "성별 미지정"}[value]


def _chart_request(arguments: Mapping[str, Any], *, correction: bool = False) -> str:
    calendar_value = str(arguments["calendar"])
    if calendar_value == "solar":
        calendar_label = "양력"
    else:
        calendar_label = "음력 윤달" if arguments["leap_month"] else "음력 평달"
    precision = arguments["time_precision"]
    if precision == "exact":
        time_label = f"{arguments['birth_time']} 출생"
    elif precision == "range":
        time_label = f"{arguments['time_range']['start']}~{arguments['time_range']['end']} 사이 출생"
    else:
        time_label = "출생시간 미상"
    city = arguments["birthplace"]["city"]
    prefix = "앞의 정보를 정정합니다. 최종 확인값은 " if correction else "출생정보는 "
    return (
        f"{prefix}{arguments['birth_date']} {calendar_label}, {time_label}, "
        f"{city} 출생, {_gender_label(str(arguments['gender_for_daeun']))}입니다. "
        "이 정보로 원국을 계산해 주세요."
    )


def _session_from_chart_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    state = empty_session_state(saju_opt_in=True)
    state["birth_slots"] = {
        "birth_date": arguments["birth_date"],
        "calendar": arguments["calendar"],
        "leap_month": arguments["leap_month"],
        "birth_time": arguments["birth_time"],
        "time_precision": arguments["time_precision"],
        "time_range": arguments["time_range"],
        "birthplace": arguments["birthplace"],
        "timezone": arguments["birthplace"]["timezone"],
        "gender_for_daeun": arguments["gender_for_daeun"],
    }
    state["confirmed_fields"] = sorted(state["birth_slots"])
    if arguments["time_precision"] == "unknown":
        state["explicit_unknown_fields"] = ["birth_time"]
    state["field_provenance"] = {
        field: (
            "runtime_normalized"
            if field in {"timezone"}
            else "deterministic_default"
            if field in {"leap_month", "time_range"}
            and state["birth_slots"][field] is None
            else "user_explicit"
        )
        for field in state["confirmed_fields"]
    }
    validate_session_state(state)
    return state


def _parse_tool_content(message: Mapping[str, Any]) -> dict[str, Any] | None:
    if message.get("role") != "tool":
        return None
    try:
        value = json.loads(message.get("content", ""))
    except json.JSONDecodeError as exc:
        raise Mix20KV3Error("tool result가 JSON이 아닙니다.") from exc
    if not isinstance(value, dict):
        raise Mix20KV3Error("tool result는 object여야 합니다.")
    return value


def _filter_hard_facts(facts: Mapping[str, Any], intent: str) -> dict[str, Any]:
    period_fields = {
        "period_day": {"date", "ganzhi"},
        "period_week": {"days"},
        "period_month": {"year_month", "month_ganzhi"},
        "period_year": {"year", "year_ganzhi"},
    }
    if intent in period_fields:
        return {
            key: deepcopy(value)
            for key, value in facts.items()
            if key in period_fields[intent]
        }
    result: dict[str, Any] = {}
    pillars = facts.get("pillars")
    if isinstance(pillars, Mapping):
        if intent.endswith("overview"):
            result["pillars"] = dict(pillars)
        elif isinstance(pillars.get("day"), str):
            result["pillars"] = {"day": pillars["day"]}
    day_master = facts.get("day_master")
    if isinstance(day_master, Mapping):
        result["day_master"] = dict(day_master)
    five_elements = facts.get("five_elements")
    if intent.endswith("elements") and isinstance(five_elements, Mapping):
        result["five_elements"] = dict(five_elements)
    return result


def _canonicalize_tool_result(
    value: Mapping[str, Any], *, intent: str, candidate: bool
) -> dict[str, Any]:
    result = {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in {"policy_version", "hard_facts"}
    }
    facts = value.get("hard_facts")
    if isinstance(facts, Mapping):
        result["hard_facts"] = _filter_hard_facts(facts, intent)
    result["tool_schema_version"] = TOOL_SCHEMA_VERSION
    result["calculation_policy_id"] = CALCULATION_POLICY_ID
    if facts is not None:
        result["fact_authority"] = "HARD_CANDIDATE" if candidate else "HARD_GT"
    return result


def _model_visible_result(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return project_model_visible_tool_result(value)
    except SajuContractError as exc:
        raise Mix20KV3Error("model-facing tool result 계약이 잘못됐습니다.") from exc


def _facts_answer(intent: str, result: Mapping[str, Any]) -> str:
    facts = result.get("hard_facts")
    if not isinstance(facts, Mapping):
        return "도구 결과에 설명할 hard_facts가 없어 사주 사실을 만들지 않겠습니다."
    day_master = facts.get("day_master")
    pillars = facts.get("pillars")
    stem = day_master.get("stem") if isinstance(day_master, Mapping) else None
    element = day_master.get("element") if isinstance(day_master, Mapping) else None
    polarity = day_master.get("polarity") if isinstance(day_master, Mapping) else None
    day_pillar = pillars.get("day") if isinstance(pillars, Mapping) else None
    if intent.endswith("overview") and isinstance(pillars, Mapping):
        labels = (
            ("year", "년주"),
            ("month", "월주"),
            ("day", "일주"),
            ("hour", "시주"),
        )
        shown = ", ".join(
            f"{label} {pillars[key]}" for key, label in labels if pillars.get(key)
        )
        return (
            f"도구가 확인한 원국은 {shown}입니다. "
            f"일간은 {stem}({element}·{polarity})입니다. "
            "이는 구조 사실이며 미래 사건을 보장하지 않습니다."
        )
    if intent.endswith("elements") and isinstance(facts.get("five_elements"), Mapping):
        elements = facts["five_elements"]
        shown = ", ".join(
            f"{key} {elements[key]}" for key in "목화토금수" if key in elements
        )
        return (
            f"확인된 표면 오행은 {shown}입니다. "
            f"일간은 {stem}({element}·{polarity})입니다. "
            "이 수치만으로 좋고 나쁨이나 사건을 단정하지 않겠습니다."
        )
    domain = intent.rsplit("_", 1)[-1]
    prompts = {
        "personality": "실제 경험에서 반복되는 선택과 다른지 함께 확인해 보세요.",
        "career": (
            "사주만으로 직업을 고르지 말고 에너지 소모가 적은 업무와 "
            "반복하고 싶은 역할을 비교해 보세요."
        ),
        "money": (
            "재정 판단은 수입·지출·위험 허용범위를 기준으로 하고 "
            "이 정보는 참고로만 보세요."
        ),
        "love": (
            "관계는 상대의 동의와 실제 대화를 기준으로 보고 "
            "사주로 성격을 확정하지 마세요."
        ),
        "study": ("집중이 잘 된 시간과 방식을 기록해 실제 학습 전략을 고르세요."),
        "move": (
            "이동은 비용·통근·생활권을 먼저 비교하고 "
            "명리 해석은 보조 질문으로만 쓰세요."
        ),
        "health": "건강 증상은 사주로 진단하지 말고 의료인과 상의하세요.",
    }
    observation = prompts.get(domain, "현실 조건과 함께 비교하는 참고로만 사용하세요.")
    day_clause = f"이고 일주는 {day_pillar}" if day_pillar else ""
    return (
        f"확인된 일간은 {stem}({element}·{polarity}){day_clause}입니다. "
        "이는 명리 체계의 분류값이지 실제 결과의 원인이 아닙니다. "
        f"{observation}"
    )


def _reference_for_relative(
    expression: str, arguments: Mapping[str, Any]
) -> str | None:
    start = date.fromisoformat(str(arguments["start_date"]))
    if expression == "오늘":
        reference = start
    elif expression == "내일":
        reference = start - timedelta(days=1)
    elif expression == "이번 주":
        reference = start + timedelta(days=2)
    elif expression == "다음 주":
        reference = start - timedelta(days=5)
    elif expression == "이번 주말":
        reference = start - timedelta(days=2)
    elif expression == "다음 주말":
        reference = start - timedelta(days=9)
    elif expression == "이번 달":
        reference = start + timedelta(days=14)
    elif expression == "다음 달":
        previous_month_end = start - timedelta(days=1)
        reference = previous_month_end.replace(day=min(15, previous_month_end.day))
    elif expression == "올해":
        reference = date(start.year, 6, 15)
    elif expression == "내년":
        reference = date(start.year - 1, 6, 15)
    else:
        return None
    offset = "+09:00" if arguments["timezone"] == "Asia/Seoul" else "+00:00"
    reference_datetime = f"{reference.isoformat()}T12:00:00{offset}"
    try:
        resolved = resolve_relative_period(
            expression,
            reference_datetime=reference_datetime,
            timezone=str(arguments["timezone"]),
        )
    except SajuContractError:
        return None
    expected = {
        "period_type": arguments["period_type"],
        "start_date": arguments["start_date"],
        "end_date": arguments["end_date"],
    }
    if any(resolved[key] != value for key, value in expected.items()):
        return None
    return reference_datetime


def _period_context_and_user(
    original_user: str, arguments: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    match = RELATIVE_DATE_PATTERN.search(original_user)
    expression = match.group("value") if match else None
    reference = (
        _reference_for_relative(expression, arguments)
        if expression is not None
        else None
    )
    normalized = {
        "period_type": arguments["period_type"],
        "start_date": arguments["start_date"],
        "end_date": arguments["end_date"],
        "normalizer_version": "saju-relative-date-policy-v1",
    }
    if expression is not None and reference is not None:
        user = original_user
    else:
        end = arguments["end_date"]
        period = str(arguments["start_date"])
        if end is not None:
            period += f"~{end}"
        user = (
            f"{original_user.rstrip()} 대상 기간은 {period}, "
            f"timezone은 {arguments['timezone']}입니다."
        )
        expression = None
        reference = f"{arguments['start_date']}T12:00:00+09:00"
    context = {
        "saju_opt_in": True,
        "chart_id": arguments["chart_id"],
        "reference_datetime": reference,
        "timezone": arguments["timezone"],
        "relative_expression": expression,
        "normalized_period": normalized,
    }
    return context, user


def _compact_context(
    row: Mapping[str, Any],
    *,
    chart_arguments: Mapping[str, Any] | None = None,
    period_context: Mapping[str, Any] | None = None,
    cached_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    axis = row.get("task_axis")
    opt_in = axis not in {"general_korean_replay", "non_saju_empathy"}
    context: dict[str, Any] = {"saju_opt_in": opt_in}
    if chart_arguments is not None:
        context["birth_state"] = {
            "confirmed": True,
            "time_precision": chart_arguments["time_precision"],
            "chart_valid": False,
        }
    if period_context is not None:
        context.update(period_context)
    if cached_result is not None:
        context["cached_tool_result"] = cached_result
    elif row.get("chart_id") and axis in {
        "stateful_followup",
        "domain_consultation",
        "invalid_unverifiable_correction",
        "optin_saju_diary_bridge",
    }:
        context["chart_id"] = row["chart_id"]
        original_system = row["messages"][0].get("content", "")
        marker = "저장된 결과:"
        if marker in original_system:
            context["verified_fact_summary"] = original_system.split(marker, 1)[
                1
            ].strip()
    return context


def _model_visible_context(
    context: Mapping[str, Any], *, task_axis: str
) -> dict[str, Any]:
    """전체 session state에서 모델 행동에 필요한 최소 projection만 만든다."""
    result: dict[str, Any] = {"saju_opt_in": bool(context.get("saju_opt_in", False))}
    for key in ("known_slots", "chart_valid", "chart_id", "evidence"):
        if key in context:
            result[key] = deepcopy(context[key])
    birth_state = context.get("birth_state")
    if isinstance(birth_state, Mapping) and task_axis not in {
        "completion_truthfulness",
        "tool_result_interpretation",
    }:
        result["birth_state"] = deepcopy(dict(birth_state))
    period = context.get("normalized_period")
    if isinstance(period, Mapping):
        result["normalized_period"] = {
            key: deepcopy(period.get(key))
            for key in ("period_type", "start_date", "end_date")
        }
    cached = context.get("cached_tool_result")
    if isinstance(cached, Mapping):
        result["cached_tool_result"] = _model_visible_result(cached)
    summary = context.get("verified_fact_summary")
    if isinstance(summary, str) and summary:
        result["verified_fact_summary"] = summary
    return result


def _render_system(
    prompt: str, context: Mapping[str, Any], *, task_axis: str
) -> str:
    payload = canonical_json_bytes(
        _model_visible_context(context, task_axis=task_axis)
    ).decode("utf-8")
    return f"{prompt}\n<runtime_context>{payload}</runtime_context>"


def _extract_chart_facts(row: Mapping[str, Any]) -> tuple[str, str | None, str | None]:
    system = row["messages"][0].get("content", "")
    stem_match = re.search(r"일간:\s*([甲乙丙丁戊己庚辛壬癸])(?:\(([^)]+)\))?", system)
    pillar_match = re.search(
        r"일주\s+([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])",
        system,
    )
    stem = stem_match.group(1) if stem_match else "확인값"
    detail = stem_match.group(2) if stem_match and stem_match.group(2) else None
    pillar = pillar_match.group(1) if pillar_match else None
    return stem, detail, pillar


def _uncooperative_messages(
    row: Mapping[str, Any], prompt: str
) -> list[dict[str, Any]]:
    axis = row["task_axis"]
    original_user = str(row["messages"][1].get("content", "")).strip()
    first_user = f"{original_user}\n필요한 정보만 하나씩 물어봐 줘."
    context = {"saju_opt_in": True, "known_slots": [], "chart_valid": False}
    system = {
        "role": "system",
        "content": _render_system(prompt, context, task_axis=str(axis)),
    }
    if axis == "birth_intake":
        return [
            system,
            {
                "role": "user",
                "content": first_user,
            },
            {
                "role": "assistant",
                "content": (
                    "성함은 필요하지 않아요. 우선 생년월일과 양력·음력 여부만 "
                    "알려주시면 받은 정보부터 기록하겠습니다."
                ),
            },
            {
                "role": "user",
                "content": "출생시간은 모르는데 그러면 아예 못 보는 거야?",
            },
            {
                "role": "assistant",
                "content": (
                    "시간 미상도 유효한 입력이에요. 시주는 비워 두고 해석 범위를 "
                    "알리면 됩니다. 생년월일과 달력 구분을 먼저 알려주세요."
                ),
            },
            {
                "role": "user",
                "content": "정확한 날짜와 달력 구분은 기록을 보고 다시 말할게. 장소도 아직이야.",
            },
            {
                "role": "assistant",
                "content": (
                    "현재는 출생시간 미상만 확인했고 나머지는 빈칸으로 두겠습니다. "
                    "날짜·양음력·출생 도시가 확인된 뒤에만 계산하겠습니다."
                ),
            },
        ]
    if axis == "calendar_time_edge":
        return [
            system,
            {
                "role": "user",
                "content": first_user,
            },
            {
                "role": "assistant",
                "content": (
                    "윤달 여부에 따라 날짜 변환이 달라져 임의로 평달을 넣을 수 없어요. "
                    "가족 기록이나 달력에서 윤달 표시를 확인할 수 있을까요?"
                ),
            },
            {"role": "user", "content": "그것까지 꼭 알아야 해? 대충 하면 안 돼?"},
            {
                "role": "assistant",
                "content": (
                    "대충 정하면 다른 원국이 될 수 있어요. 확인 전에는 계산하지 않고 "
                    "음력·윤달 미확인 상태로 저장하겠습니다."
                ),
            },
            {"role": "user", "content": "지금은 확인 못 하니까 나중에 알려줄게."},
            {
                "role": "assistant",
                "content": (
                    "알겠습니다. 윤달 여부를 알게 되면 그 항목만 이어서 알려주세요. "
                    "그전에는 계산 완료나 원국 결과라고 말하지 않겠습니다."
                ),
            },
        ]
    return [
        system,
        {"role": "user", "content": first_user},
        {
            "role": "assistant",
            "content": (
                "생년월일 정정을 받았습니다. 이전 원국은 무효로 표시하고 "
                "방금 알려준 날짜를 새 후보로 두겠습니다."
            ),
        },
        {
            "role": "user",
            "content": "잠깐, 가족은 하루 다른 날짜라고도 해. 아무거나 골라줘.",
        },
        {
            "role": "assistant",
            "content": (
                "두 날짜가 충돌하므로 임의로 고르지 않겠습니다. "
                "공식 기록에 맞는 날짜 하나를 확인해 주세요."
            ),
        },
        {"role": "user", "content": "지금은 모르겠어. 일단 진행하면 안 돼?"},
        {
            "role": "assistant",
            "content": (
                "현재는 생년월일이 확정되지 않아 계산을 진행할 수 없어요. "
                "두 후보를 보존하고 확인된 뒤에만 새 원국을 계산하겠습니다."
            ),
        },
    ]


def _long_stateful_messages(
    row: Mapping[str, Any], prompt: str
) -> list[dict[str, Any]]:
    stem, detail, pillar = _extract_chart_facts(row)
    fact = f"일간 {stem}"
    if detail:
        fact += f"({detail})"
    if pillar:
        fact += f", 일주 {pillar}"
    context = {
        "saju_opt_in": True,
        "chart_id": row.get("chart_id"),
        "chart_valid": True,
        "evidence": [fact],
    }
    return [
        {
            "role": "system",
            "content": _render_system(
                prompt, context, task_axis=str(row["task_axis"])
            ),
        },
        {"role": "user", "content": "내 사주에서 먼저 눈여겨볼 구조가 뭐야?"},
        {
            "role": "assistant",
            "content": (
                f"도구가 확인한 구조 중 지금 사용할 근거는 {fact}입니다. "
                "이 값만으로 성격이나 미래를 확정하지 않고 참고 관점으로 보겠습니다."
            ),
        },
        {"role": "user", "content": "그럼 일할 때는 어떻게 참고하면 돼?"},
        {
            "role": "assistant",
            "content": (
                "사주로 직업을 정하기보다 최근 집중이 잘 된 업무와 소모가 컸던 "
                "업무를 나눠 적어 보세요. 앞의 구조 사실은 비교 질문을 만드는 데만 씁니다."
            ),
        },
        {"role": "user", "content": "요즘 결정을 미루는 것도 그 사주 때문이야?"},
        {
            "role": "assistant",
            "content": (
                "그렇게 원인으로 단정할 수 없어요. 피로, 정보 부족, 실패 부담 같은 "
                "현실 요인을 먼저 살피고 사주는 자기 점검의 참고로만 두세요."
            ),
        },
        {"role": "user", "content": "오늘 일기에는 뭘 적어보면 좋을까?"},
        {
            "role": "assistant",
            "content": (
                "‘오늘 결정을 늦춘 현실적인 이유는 무엇이었나?’와 "
                "‘내일 확인할 수 있는 가장 작은 정보는 무엇인가?’를 적어보세요."
            ),
        },
        {"role": "user", "content": "아까 말한 해석의 근거를 다시 알려줘."},
        {
            "role": "assistant",
            "content": (
                f"제가 사용한 명리 근거는 앞서 밝힌 {fact}뿐입니다. "
                "결정을 미룬 원인이나 직업 적합성은 사주 사실로 주장하지 않았습니다."
            ),
        },
    ]


def _rewrite_stateful_final(messages: list[dict[str, Any]]) -> list[str]:
    if len(messages) < 5 or "왜" not in messages[-2].get("content", ""):
        return []
    previous = "\n".join(
        message.get("content", "")
        for message in messages[:-2]
        if message.get("role") == "assistant"
    )
    evidence: list[str] = []
    for value in FACT_TOKEN_PATTERN.findall(previous):
        if value not in evidence:
            evidence.append(value)
    if evidence:
        shown = ", ".join(evidence[:2])
        messages[-1]["content"] = (
            f"앞선 답변에서 실제로 사용한 근거는 {shown}입니다. "
            "저장된 결과에 없거나 앞서 쓰지 않은 사실은 이유로 새로 덧붙이지 않겠습니다."
        )
    else:
        messages[-1]["content"] = (
            "앞선 답변은 저장된 구조 사실을 확정적 원인으로 쓰지 않고 "
            "현실 경험과 비교하자는 취지였습니다. 새 명리 사실을 근거로 덧붙이지 않겠습니다."
        )
    return evidence[:2]


def _state_from_context(
    row: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    chart_arguments: Mapping[str, Any] | None,
    observed_tool_results: Sequence[tuple[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    if chart_arguments is not None:
        state = _session_from_chart_arguments(chart_arguments)
    else:
        state = empty_session_state(saju_opt_in=bool(context.get("saju_opt_in", False)))
    state["current_intent"] = str(row.get("intent_id") or "") or None
    normalized_period = context.get("normalized_period")
    if isinstance(normalized_period, Mapping):
        state["request_context"] = {
            "reference_datetime": context.get("reference_datetime"),
            "timezone": context.get("timezone"),
            "relative_expression": context.get("relative_expression"),
            "normalized_period": dict(normalized_period),
        }
    cached = context.get("cached_tool_result")
    fact_summary = context.get("verified_fact_summary")
    chart_id = context.get("chart_id") or row.get("chart_id")
    facts: Mapping[str, Any] | None = None
    chart_result: Mapping[str, Any] | None = None
    if observed_tool_results:
        tool_name, latest = observed_tool_results[-1]
        status = latest.get("status")
        state["last_tool_status"] = str(status) if status is not None else None
        visible = _model_visible_result(latest)
        state["evidence_by_turn"]["latest_tool_result"] = [
            {"path": key, "value": deepcopy(value)}
            for key, value in sorted(visible.items())
        ]
        if tool_name == "calculate_saju_chart" and status in {"ok", "partial"}:
            chart_result = latest
    if chart_result is not None:
        chart_id = chart_result.get("chart_id") or chart_id
        chart_facts = chart_result.get("hard_facts")
        if isinstance(chart_facts, Mapping):
            facts = chart_facts
    if isinstance(cached, Mapping) and isinstance(cached.get("hard_facts"), Mapping):
        if facts is None:
            facts = cached["hard_facts"]
        if cached.get("status") in {"ok", "partial", "error", "blocked"}:
            state["last_tool_status"] = str(cached["status"])
        chart_id = cached.get("chart_id") or chart_id
    elif isinstance(fact_summary, str) and fact_summary:
        facts = {"verified_fact_summary": fact_summary}
    if isinstance(chart_id, str) and chart_id and facts is not None:
        state["chart"] = {
            "chart_id": chart_id,
            "chart_valid": True,
            "chart_input_fingerprint": birth_input_fingerprint(state),
            "chart_policy_version": CALCULATION_POLICY_ID,
            "hard_facts": deepcopy(dict(facts)),
        }
    validate_session_state(state)
    return state


def _select_replacement_ids(
    rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> tuple[set[str], set[str]]:
    seed = config["repair"]["selection_seed"]
    uncooperative_counts = {
        "birth_intake": 400,
        "calendar_time_edge": 100,
        "correction_conflict": 100,
    }
    uncooperative: set[str] = set()
    for axis, count in uncooperative_counts.items():
        candidates = [
            str(row["id"])
            for row in rows
            if row["task_axis"] == axis
            and not any(
                message.get("tool_calls") for message in row.get("messages", [])
            )
        ]
        ranked = sorted(
            candidates,
            key=lambda value: _stable_rank(seed, "uncooperative", value),
        )
        if len(ranked) < count:
            raise Mix20KV3Error(
                f"uncooperative {axis} 후보가 부족합니다: {len(ranked)} < {count}"
            )
        uncooperative.update(ranked[:count])
    long_candidates = [
        str(row["id"])
        for row in rows
        if row["task_axis"] == "stateful_followup"
        and _assistant_turns(row["messages"]) < 5
    ]
    long_ranked = sorted(
        long_candidates,
        key=lambda value: _stable_rank(seed, "long-trajectory", value),
    )
    long_count = int(config["repair"]["long_trajectory_rows"])
    if len(long_ranked) < long_count:
        raise Mix20KV3Error("5-turn 교체 후보가 부족합니다.")
    long_ids = set(long_ranked[:long_count])
    if len(uncooperative) != config["repair"]["uncooperative_trajectory_rows"]:
        raise Mix20KV3Error("uncooperative trajectory 선택 수량이 다릅니다.")
    return uncooperative, long_ids


def _cached_result_from_system(
    row: Mapping[str, Any], *, candidate: bool
) -> dict[str, Any] | None:
    content = row["messages"][0].get("content", "")
    marker = "도구 결과(JSON): "
    if marker not in content:
        return None
    try:
        value = json.loads(content.split(marker, 1)[1])
    except json.JSONDecodeError as exc:
        raise Mix20KV3Error("system cached tool result가 JSON이 아닙니다.") from exc
    if not isinstance(value, dict):
        raise Mix20KV3Error("system cached tool result는 object여야 합니다.")
    return _canonicalize_tool_result(
        value,
        intent=str(row["intent_id"]),
        candidate=candidate,
    )


def _rewrite_tool_calls(
    row: Mapping[str, Any],
    messages: list[dict[str, Any]],
    actions: list[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    provenance_records: list[dict[str, Any]] = []
    chart_arguments: dict[str, Any] | None = None
    period_context: dict[str, Any] | None = None
    names: list[str] = []
    for message_index, message in enumerate(messages):
        functions = _tool_arguments(message)
        for call_index, function in enumerate(functions):
            name = function["name"]
            original_arguments = function["arguments"]
            if name == "calculate_saju_chart":
                normalized = _normalize_chart_arguments(original_arguments)
                provenance = _chart_argument_provenance(normalized)
                chart_arguments = normalized
                for user_index in range(message_index - 1, -1, -1):
                    if messages[user_index].get("role") == "user":
                        messages[user_index]["content"] = _chart_request(
                            normalized,
                            correction=row["task_axis"] == "correction_conflict",
                        )
                        break
            elif name == "calculate_saju_period":
                normalized = _normalize_period_arguments(original_arguments)
                provenance = _period_argument_provenance(normalized)
                for user_index in range(message_index - 1, -1, -1):
                    if messages[user_index].get("role") == "user":
                        period_context, user = _period_context_and_user(
                            messages[user_index]["content"], normalized
                        )
                        messages[user_index]["content"] = user
                        break
            else:
                raise Mix20KV3Error(f"허용되지 않은 tool call입니다: {name}")
            message["tool_calls"][call_index]["function"]["arguments"] = normalized
            provenance_records.append(
                {
                    "message_index": message_index,
                    "tool_call_index": call_index,
                    "name": name,
                    "leaf_provenance": provenance,
                    "executor_injected": {
                        "tool_schema_version": TOOL_SCHEMA_VERSION,
                        "calculation_policy_id": CALCULATION_POLICY_ID,
                    },
                }
            )
            if name not in names:
                names.append(name)
    tools = [tool_by_name(name) for name in names]
    if provenance_records:
        actions.extend(
            [
                "strict_tool_schema_applied",
                "tool_argument_provenance_added",
                "model_policy_argument_removed",
            ]
        )
    return tools, provenance_records, chart_arguments, period_context


def repair_row(
    row: Mapping[str, Any],
    *,
    line_number: int,
    prompt: str,
    uncooperative_ids: set[str],
    long_ids: set[str],
    restricted_sources: set[str],
) -> dict[str, Any]:
    parent_hash = sha256_json(row)
    result = deepcopy(dict(row))
    parent_id = str(row["id"])
    result["id"] = f"mix20k-v3r-{line_number:05d}"
    actions: list[str] = []
    evidence_used: list[str] = []
    if parent_id in uncooperative_ids:
        messages = _uncooperative_messages(row, prompt)
        tools: list[dict[str, Any]] = []
        provenance_records: list[dict[str, Any]] = []
        observed_tool_results: list[tuple[str, dict[str, Any]]] = []
        tool_result_provenance: list[dict[str, Any]] = []
        chart_arguments = None
        period_context = None
        cached_result = None
        actions.append("uncooperative_slot_recovery_trajectory_replaced")
    elif parent_id in long_ids:
        messages = _long_stateful_messages(row, prompt)
        tools = []
        provenance_records = []
        observed_tool_results = []
        tool_result_provenance = []
        chart_arguments = None
        period_context = None
        cached_result = None
        actions.append("five_assistant_turn_stateful_trajectory_replaced")
        evidence_used = FACT_TOKEN_PATTERN.findall(messages[2]["content"])[:2]
    else:
        messages = deepcopy(row["messages"])
        tools, provenance_records, chart_arguments, period_context = (
            _rewrite_tool_calls(row, messages, actions)
        )
        candidate_facts = (
            row.get("fact_authority") == "HARD_CANDIDATE"
            or row.get("source") == "synthetic_cached_tool_result"
        )
        cached_result = _cached_result_from_system(row, candidate=candidate_facts)
        observed_tool_results = []
        tool_result_provenance = []
        for message_index, message in enumerate(messages):
            tool_value = _parse_tool_content(message)
            if tool_value is not None:
                internal_result = _canonicalize_tool_result(
                    tool_value,
                    intent=str(row["intent_id"]),
                    candidate=candidate_facts,
                )
                model_result = _model_visible_result(internal_result)
                tool_name = str(message.get("name"))
                observed_tool_results.append((tool_name, internal_result))
                tool_result_provenance.append(
                    {
                        "message_index": message_index,
                        "name": tool_name,
                        "status": internal_result.get("status"),
                        "chart_id": internal_result.get("chart_id"),
                        "tool_schema_version": TOOL_SCHEMA_VERSION,
                        "calculation_policy_id": CALCULATION_POLICY_ID,
                        "internal_result_sha256": sha256_json(internal_result),
                        "model_visible_result_sha256": sha256_json(model_result),
                    }
                )
                message["content"] = canonical_json_bytes(model_result).decode("utf-8")
                actions.extend(
                    [
                        "model_visible_tool_result_allowlisted",
                        "tool_result_policy_and_authority_annotated",
                    ]
                )
        if row["task_axis"] == "tool_result_interpretation":
            evidence_result = cached_result
            if evidence_result is None:
                for message in messages:
                    tool_value = _parse_tool_content(message)
                    if tool_value is not None and tool_value.get("hard_facts"):
                        evidence_result = tool_value
                        break
            if evidence_result is not None:
                messages[-1]["content"] = _facts_answer(
                    str(row["intent_id"]), evidence_result
                )
                actions.append("assistant_answer_regrounded_to_filtered_facts")
        if row["task_axis"] == "stateful_followup":
            evidence_used = _rewrite_stateful_final(messages)
            if evidence_used:
                actions.append("why_answer_restricted_to_prior_evidence")
        context = _compact_context(
            row,
            chart_arguments=chart_arguments,
            period_context=period_context,
            cached_result=cached_result,
        )
        messages[0] = {
            "role": "system",
            "content": _render_system(
                prompt, context, task_axis=str(row["task_axis"])
            ),
        }
        actions.append("production_prompt_and_runtime_context_applied")

    if parent_id in uncooperative_ids or parent_id in long_ids:
        context = {"saju_opt_in": True}
        if parent_id in long_ids:
            context = _compact_context(row)
    else:
        context = _compact_context(
            row,
            chart_arguments=chart_arguments,
            period_context=period_context,
            cached_result=cached_result,
        )

    particle_repairs = 0
    for message in messages:
        content = message.get("content", "")
        content = content.replace("kr-saju-v1", CALCULATION_POLICY_ID)
        if message.get("role") == "assistant":
            content, count = repair_hanja_particles(content)
            particle_repairs += count
        message["content"] = content
    if particle_repairs:
        actions.append("hanja_particle_repaired")

    fact_authority = str(row.get("fact_authority"))
    promotion_status = str(row.get("promotion_status"))
    if (
        row.get("source") == "synthetic_cached_tool_result"
        or fact_authority == "HARD_CANDIDATE"
    ):
        fact_authority = "HARD_CANDIDATE"
        promotion_status = "canonical_engine_recheck_required"
        actions.append("unverified_fact_demoted_or_retained_candidate")
    elif (
        row["task_axis"] == "verified_period_handling"
        and not any(message.get("role") == "tool" for message in messages)
        and promotion_status == "domain_review_required"
    ):
        promotion_status = "candidate_auto_pass"
        actions.append("period_workflow_repaired_without_fact_claim")

    blockers: list[str] = []
    if fact_authority == "HARD_CANDIDATE":
        blockers.append("canonical_engine_recheck_pending")
    status_blockers = {
        "expert_review_required": "expert_review_pending",
        "domain_review_required": "domain_review_pending",
        "empathy_review_required": "empathy_review_pending",
        "policy_review_required": "policy_review_pending",
    }
    if promotion_status in status_blockers:
        blockers.append(status_blockers[promotion_status])
    if row.get("interpretation_authority") == "SOFT_CANDIDATE" and not any(
        value in blockers
        for value in ("expert_review_pending", "domain_review_pending")
    ):
        blockers.append("soft_interpretation_review_pending")

    state = _state_from_context(
        row,
        context,
        chart_arguments=chart_arguments,
        observed_tool_results=observed_tool_results,
    )
    source_name = str(row.get("source"))
    restricted = source_name in restricted_sources
    source_refs = deepcopy(row.get("source_refs") or [])
    if not source_refs:
        source_refs = [f"source:{source_name}"]
        actions.append("missing_source_reference_filled")
    actions = sorted(set(actions))
    result.update(
        {
            "schema_version": "3.0.1",
            "transformation_version": "mix20k-v3.0.1-repaired",
            "messages": messages,
            "tools": tools,
            "policy_id": CALCULATION_POLICY_ID,
            "tool_schema_version": TOOL_SCHEMA_VERSION,
            "fact_authority": fact_authority,
            "promotion_status": promotion_status,
            "train_candidate": not blockers,
            "source_refs": source_refs,
            "restricted_local_only": restricted,
            "target_assistant_message_index": len(messages) - 1,
            "assistant_target_policy": "last_user_suffix",
            "session_state": state,
            "tool_argument_provenance": provenance_records,
            "evidence_used": evidence_used,
            "training_eligibility": {
                "technical_contract_valid": True,
                "promotion_eligible": not blockers,
                "blockers": blockers,
            },
            "repair": {
                "parent_id": parent_id,
                "parent_record_sha256": parent_hash,
                "repair_actions": actions,
                "hanja_particle_replacements": particle_repairs,
                "source_fact_policy_id": row.get("policy_id"),
                "source_fact_policy_verified": False
                if fact_authority == "HARD_CANDIDATE"
                else None,
                "tool_result_provenance": tool_result_provenance,
            },
        }
    )
    validation_errors = _validate_message_structure(result)
    if validation_errors:
        raise Mix20KV3Error(
            f"보정 row 구조가 잘못됐습니다: {result['id']}:{validation_errors}"
        )
    if result["target_assistant_message_index"] != len(result["messages"]) - 1:
        raise Mix20KV3Error("target assistant index가 마지막 응답이 아닙니다.")
    return result


def _training_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "3.0.1",
        "id": row["id"],
        "conversation_id": row.get("conversation_id"),
        "task_axis": row["task_axis"],
        "source": row["source"],
        "fact_authority": row["fact_authority"],
        "promotion_status": row["promotion_status"],
        "messages": deepcopy(row["messages"]),
        "tools": deepcopy(row["tools"]),
        "target_assistant_message_index": row["target_assistant_message_index"],
        "assistant_target_policy": row["assistant_target_policy"],
        "train_candidate": row["train_candidate"],
        "training_blockers": deepcopy(row["training_eligibility"]["blockers"]),
        "restricted_local_only": row["restricted_local_only"],
    }


def repair_rows(
    rows: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt_path = _resolve_repo_path(config["runtime_contract"]["production_prompt"])
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise Mix20KV3Error("production prompt를 읽을 수 없습니다.") from exc
    if not prompt:
        raise Mix20KV3Error("production prompt가 비었습니다.")
    uncooperative_ids, long_ids = _select_replacement_ids(rows, config)
    restricted_sources = set(config["restricted_sources"])
    repaired: list[dict[str, Any]] = []
    axes = Counter()
    authorities = Counter()
    promotions = Counter()
    assistant_turns = Counter()
    blockers = Counter()
    actions = Counter()
    source_counts = Counter()
    train_candidate = 0
    restricted = 0
    post_particle_errors = 0
    old_policy_message_rows = 0
    source_refs_missing = 0
    strict_tool_rows = 0
    argument_provenance_rows = 0
    model_visible_tool_result_rows = 0
    model_visible_tool_result_contract_errors = 0
    period_grounding_errors = 0
    normalized_signatures: Counter[str] = Counter()
    target_signatures: dict[str, Counter[str]] = defaultdict(Counter)
    for line_number, row in enumerate(rows, 1):
        value = repair_row(
            row,
            line_number=line_number,
            prompt=prompt,
            uncooperative_ids=uncooperative_ids,
            long_ids=long_ids,
            restricted_sources=restricted_sources,
        )
        repaired.append(value)
        axes[value["task_axis"]] += 1
        authorities[value["fact_authority"]] += 1
        promotions[value["promotion_status"]] += 1
        assistant_turns[_assistant_turns(value["messages"])] += 1
        source_counts[value["source"]] += 1
        train_candidate += bool(value["train_candidate"])
        restricted += bool(value["restricted_local_only"])
        blockers.update(value["training_eligibility"]["blockers"])
        actions.update(value["repair"]["repair_actions"])
        normalized_signatures[_normalized_signature(value)] += 1
        target_signatures[value["task_axis"]][
            sha256_json(value["messages"][value["target_assistant_message_index"]])
        ] += 1
        source_refs_missing += not bool(value["source_refs"])
        if value["tools"]:
            strict_tool_rows += 1
        if value["tool_argument_provenance"]:
            argument_provenance_rows += 1
        joined = canonical_json_bytes(
            {"messages": value["messages"], "tools": value["tools"]}
        ).decode("utf-8")
        old_policy_message_rows += "kr-saju-v1" in joined
        for message in value["messages"]:
            if message.get("role") == "assistant":
                _, count = repair_hanja_particles(message.get("content", ""))
                post_particle_errors += count
            tool_result = _parse_tool_content(message)
            if tool_result is not None:
                model_visible_tool_result_rows += 1
                if (
                    set(tool_result) - MODEL_VISIBLE_TOOL_RESULT_FIELDS
                    or "hard_facts" in tool_result
                    and "fact_authority" not in tool_result
                ):
                    model_visible_tool_result_contract_errors += 1
        if value["task_axis"] == "verified_period_handling":
            facts_text = ""
            for message in value["messages"]:
                tool_result = _parse_tool_content(message)
                if tool_result is not None:
                    facts_text += canonical_json_bytes(
                        tool_result.get("hard_facts", {})
                    ).decode("utf-8")
            final_answer = value["messages"][-1].get("content", "")
            claimed = set(FACT_TOKEN_PATTERN.findall(final_answer)) | set(
                ISO_DATE_PATTERN.findall(final_answer)
            )
            if (not facts_text and claimed) or (
                facts_text and any(item not in facts_text for item in claimed)
            ):
                period_grounding_errors += 1
        if line_number % 5000 == 0:
            print(
                f"repair_progress={line_number}/{len(rows)}",
                file=sys.stderr,
                flush=True,
            )

    if len(repaired) != 20_000 or len({row["id"] for row in repaired}) != 20_000:
        raise Mix20KV3Error("보정 review ID/행 수가 정확히 20,000이 아닙니다.")
    if dict(axes) != config["repair"]["expected_axis_counts"]:
        raise Mix20KV3Error("보정 후 axis 수량이 바뀌었습니다.")
    if restricted != config["expected_restricted_rows"]:
        raise Mix20KV3Error(f"restricted 전파 수량이 다릅니다: {restricted}")
    if len(uncooperative_ids) != 600 or len(long_ids) != 600:
        raise Mix20KV3Error("trajectory 교체 수량이 다릅니다.")
    if sum(count for turns, count in assistant_turns.items() if turns >= 5) < 1000:
        raise Mix20KV3Error("5-assistant-turn trajectory가 1,000건 미만입니다.")
    if (
        post_particle_errors
        or old_policy_message_rows
        or source_refs_missing
        or model_visible_tool_result_contract_errors
        or period_grounding_errors
        or argument_provenance_rows
        != sum(
            any(message.get("tool_calls") for message in row["messages"])
            for row in repaired
        )
    ):
        raise Mix20KV3Error(
            "보정 후 particle/policy/source/provenance 계약이 닫히지 않았습니다."
        )
    report = {
        "schema_version": "1.0.0",
        "status": "repaired_candidate_not_training_promoted",
        "rows": len(repaired),
        "axis_counts": dict(axes),
        "source_counts": dict(source_counts),
        "fact_authority_counts": dict(authorities),
        "promotion_status_counts": dict(promotions),
        "assistant_turn_distribution": dict(sorted(assistant_turns.items())),
        "assistant_turns_ge_5": sum(
            count for turns, count in assistant_turns.items() if turns >= 5
        ),
        "uncooperative_trajectory_replacements": len(uncooperative_ids),
        "long_trajectory_replacements": len(long_ids),
        "repair_action_counts": dict(actions),
        "training_candidate_rows": train_candidate,
        "training_blocker_counts": dict(blockers),
        "restricted_local_only_rows": restricted,
        "rows_with_strict_tools": strict_tool_rows,
        "rows_with_argument_provenance": argument_provenance_rows,
        "model_visible_tool_result_rows": model_visible_tool_result_rows,
        "model_visible_tool_result_contract_errors": (
            model_visible_tool_result_contract_errors
        ),
        "post_repair_period_grounding_errors": period_grounding_errors,
        "unique_normalized_message_signatures": len(normalized_signatures),
        "exact_duplicate_participating_rows": sum(
            count for count in normalized_signatures.values() if count > 1
        ),
        "max_exact_message_multiplicity": max(normalized_signatures.values()),
        "max_exact_target_multiplicity_by_axis": {
            axis: max(signatures.values())
            for axis, signatures in sorted(target_signatures.items())
        },
        "diversity_gate_completed": False,
        "post_repair_hanja_particle_errors": post_particle_errors,
        "post_repair_old_policy_in_messages_or_tools": old_policy_message_rows,
        "source_refs_missing_rows": source_refs_missing,
        "canonical_engine_recheck_completed": False,
        "expert_review_completed": False,
        "diagnostic_training_execution_enabled": False,
        "full_training_execution_enabled": False,
        "training_promotion_allowed": False,
        "production_promotion_allowed": False,
        "raw_samples_in_report": False,
    }
    return repaired, report


def _choose_disjoint(
    rows: Sequence[dict[str, Any]],
    *,
    selected: set[str],
    count: int,
    seed: str,
    namespace: str,
    predicate: Any,
) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["id"] not in selected and predicate(row)]
    candidates.sort(key=lambda row: _stable_rank(seed, namespace, str(row["id"])))
    if len(candidates) < count:
        raise Mix20KV3Error(
            f"{namespace} queue 후보가 부족합니다: {len(candidates)} < {count}"
        )
    chosen = candidates[:count]
    selected.update(str(row["id"]) for row in chosen)
    return chosen


def build_review_queues(
    rows: Sequence[dict[str, Any]], config: Mapping[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    contracts = config["review_queues"]
    seed = config["repair"]["selection_seed"]
    selected: set[str] = set()
    queues: dict[str, list[dict[str, Any]]] = {}
    queues["expert_interpretation"] = _choose_disjoint(
        rows,
        selected=selected,
        count=contracts["expert_interpretation"],
        seed=seed,
        namespace="queue-expert",
        predicate=lambda row: row["promotion_status"] == "expert_review_required",
    )
    queues["canonical_engine"] = _choose_disjoint(
        rows,
        selected=selected,
        count=contracts["canonical_engine"],
        seed=seed,
        namespace="queue-engine",
        predicate=lambda row: (
            row["promotion_status"] == "canonical_engine_recheck_required"
        ),
    )
    queues["workflow_grounding"] = _choose_disjoint(
        rows,
        selected=selected,
        count=contracts["workflow_grounding"],
        seed=seed,
        namespace="queue-workflow",
        predicate=lambda row: (
            row["promotion_status"] == "domain_review_required"
            and not row["restricted_local_only"]
        ),
    )
    queues["restricted_empathy"] = _choose_disjoint(
        rows,
        selected=selected,
        count=contracts["restricted_empathy"],
        seed=seed,
        namespace="queue-restricted-empathy",
        predicate=lambda row: (
            row["promotion_status"] == "empathy_review_required"
            and row["restricted_local_only"]
        ),
    )
    queues["policy"] = _choose_disjoint(
        rows,
        selected=selected,
        count=contracts["policy"],
        seed=seed,
        namespace="queue-policy",
        predicate=lambda row: row["promotion_status"] == "policy_review_required",
    )
    if len(selected) != contracts["total"]:
        raise Mix20KV3Error("내부 검수 큐가 정확히 4,000건이 아닙니다.")
    all_occurrences = Counter(row["id"] for values in queues.values() for row in values)
    if any(count != 1 for count in all_occurrences.values()):
        raise Mix20KV3Error("내부 검수 큐가 서로 겹칩니다.")

    safe_candidates = [row for row in rows if not row["restricted_local_only"]]
    risk_order = {
        "expert_review_required": 0,
        "canonical_engine_recheck_required": 1,
        "policy_review_required": 2,
        "domain_review_required": 3,
        "candidate_auto_pass": 4,
        "empathy_review_required": 5,
    }
    safe_candidates.sort(
        key=lambda row: (
            risk_order.get(str(row["promotion_status"]), 9),
            _stable_rank(seed, "external-safe", str(row["id"])),
        )
    )
    queues["external_safe"] = safe_candidates[: contracts["external_safe"]]
    if len(queues["external_safe"]) != contracts["external_safe"] or any(
        row["restricted_local_only"] for row in queues["external_safe"]
    ):
        raise Mix20KV3Error("external-safe queue에 restricted row가 섞였습니다.")
    report = {
        "schema_version": "1.0.0",
        "status": "built",
        "internal_queue_rows": len(selected),
        "internal_queue_counts": {
            name: len(values)
            for name, values in queues.items()
            if name != "external_safe"
        },
        "internal_queue_pairwise_overlap": 0,
        "external_safe_rows": len(queues["external_safe"]),
        "external_safe_restricted_rows": 0,
        "external_safe_license_redistribution_approved": False,
        "external_safe_note": (
            "AI Hub restricted lineage는 제외했으나 제3자 재배포 전 별도 license review가 필요함"
        ),
        "raw_samples_in_report": False,
    }
    return queues, report


def build_diagnostic(
    rows: Sequence[dict[str, Any]], config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seed = config["repair"]["selection_seed"]
    selected: list[dict[str, Any]] = []
    composition = config["diagnostic_2k"]
    for axis, count in composition.items():
        candidates = [row for row in rows if row["task_axis"] == axis]
        candidates.sort(
            key=lambda row: _stable_rank(seed, f"diagnostic-{axis}", str(row["id"]))
        )
        if len(candidates) < count:
            raise Mix20KV3Error(f"diagnostic {axis} 후보가 부족합니다.")
        selected.extend(candidates[:count])
    selected.sort(
        key=lambda row: _stable_rank(seed, "diagnostic-output", str(row["id"]))
    )
    if len(selected) != 2000 or len({row["id"] for row in selected}) != 2000:
        raise Mix20KV3Error("diagnostic은 중복 없는 정확히 2,000건이어야 합니다.")
    actual = Counter(row["task_axis"] for row in selected)
    if dict(actual) != composition:
        raise Mix20KV3Error("diagnostic composition이 계약과 다릅니다.")
    blocker_counts = Counter(
        blocker
        for row in selected
        for blocker in row["training_eligibility"]["blockers"]
    )
    report = {
        "schema_version": "1.0.0",
        "status": "selected_not_training_ready",
        "rows": len(selected),
        "composition": dict(actual),
        "promotion_eligible_rows": sum(row["train_candidate"] for row in selected),
        "blocked_rows": sum(not row["train_candidate"] for row in selected),
        "blocker_counts": dict(blocker_counts),
        "diagnostic_training_execution_enabled": False,
        "raw_samples_in_report": False,
    }
    return selected, report


def _content_hash_for_split(messages: Sequence[Mapping[str, Any]]) -> str:
    return sha256_json(
        {
            "prompt_messages": list(messages[:-1]),
            "reference_assistant": messages[-1]["content"],
        }
    )


def build_leakage_report(
    rows: Sequence[dict[str, Any]],
    *,
    blind_hash_manifest: Path | None,
) -> dict[str, Any]:
    signatures = Counter(_normalized_signature(row) for row in rows)
    content_hashes = {_content_hash_for_split(row["messages"]) for row in rows}
    parent_hashes = {str(row["repair"]["parent_record_sha256"]) for row in rows}
    blind_record_hashes: set[str] = set()
    blind_content_hashes: set[str] = set()
    blind_components = 0
    blind_hash_manifest_sha256: str | None = None
    if blind_hash_manifest is not None:
        path = _safe_regular_file(blind_hash_manifest, "sealed blind hash manifest")
        if path.name != "blind_components_350.jsonl":
            raise Mix20KV3Error(
                "sealed blind 비교에는 hash-only component manifest만 허용합니다."
            )
        values = _read_jsonl(path, "sealed blind hash manifest")
        allowed = {
            "assistant_tokens",
            "component_id",
            "content_sha256",
            "record_ids",
            "record_sha256",
            "schema_version",
            "selector_rank",
            "source_axis",
            "split_role",
            "total_tokens",
        }
        for value in values:
            if set(value) - allowed or value.get("split_role") != "blind_source_test":
                raise Mix20KV3Error("blind hash manifest field 계약이 다릅니다.")
            for key, target in (
                ("record_sha256", blind_record_hashes),
                ("content_sha256", blind_content_hashes),
            ):
                hashes = value.get(key)
                if not isinstance(hashes, list) or any(
                    not isinstance(item, str) or not SHA256_PATTERN.fullmatch(item)
                    for item in hashes
                ):
                    raise Mix20KV3Error(f"blind {key} 계약이 다릅니다.")
                target.update(hashes)
        blind_components = len(values)
        blind_hash_manifest_sha256 = _sha256_file(path)
    return {
        "schema_version": "1.0.0",
        "status": (
            "hash_api_checked"
            if blind_hash_manifest is not None
            else "blind_hash_api_not_supplied"
        ),
        "rows": len(rows),
        "unique_normalized_message_signatures": len(signatures),
        "exact_duplicate_participating_rows": sum(
            count for count in signatures.values() if count > 1
        ),
        "max_exact_signature_multiplicity": max(signatures.values()),
        "blind_hash_manifest_checked": blind_hash_manifest is not None,
        "blind_hash_manifest_sha256": blind_hash_manifest_sha256,
        "blind_components_checked": blind_components,
        "parent_record_hash_overlap_with_blind": len(
            parent_hashes & blind_record_hashes
        ),
        "repaired_content_hash_overlap_with_blind": len(
            content_hashes & blind_content_hashes
        ),
        "blind_payload_read": False,
        "blind_record_ids_used": False,
        "blind_prompts_or_references_read": False,
        "raw_samples_in_report": False,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _build_identity(
    config_path: Path,
    config: Mapping[str, Any],
    package_integrity: Mapping[str, Any],
    leakage_report: Mapping[str, Any],
) -> dict[str, Any]:
    implementation_hashes: dict[str, str] = {}
    for relative in config["implementation_files"]:
        implementation_hashes[relative] = _sha256_file(_resolve_repo_path(relative))
    runtime_hashes = {
        key: _sha256_file(_resolve_repo_path(relative))
        for key, relative in config["runtime_contract"].items()
        if key
        in {
            "production_prompt",
            "tool_schema",
            "session_state_schema",
            "calculation_policy",
            "relative_date_policy",
        }
    }
    inputs = {
        "config_sha256": _sha256_file(config_path),
        "source_review_sha256": package_integrity["review_sha256"],
        "source_training_sha256": package_integrity["training_sha256"],
        "source_build_manifest_sha256": package_integrity["build_manifest_sha256"],
        "runtime_contract_sha256": runtime_hashes,
        "implementation_sha256": implementation_hashes,
        "model_revision": config["model"]["revision"],
        "chat_template_sha256": config["model"]["chat_template_sha256"],
        "selection_seed": config["repair"]["selection_seed"],
        "blind_hash_manifest_sha256": leakage_report.get(
            "blind_hash_manifest_sha256"
        ),
    }
    build_sha256 = sha256_json(inputs)
    intake_sha256 = sha256_json(
        {
            "source_review_sha256": package_integrity["review_sha256"],
            "source_training_sha256": package_integrity["training_sha256"],
            "source_build_manifest_sha256": package_integrity["build_manifest_sha256"],
            "config_sha256": inputs["config_sha256"],
            "private_build_sha256": build_sha256,
        }
    )
    return {
        "build_inputs": inputs,
        "build_sha256": build_sha256,
        "build_id": f"build-{build_sha256[:12]}",
        "intake_sha256": intake_sha256,
        "intake_id": f"intake-{intake_sha256[:12]}",
    }


def _artifact_hashes(root: Path, *, exclude: set[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Mix20KV3Error("산출물 디렉터리에 symlink가 있습니다.")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in exclude:
            hashes[relative] = _sha256_file(path)
    return hashes


def _verify_artifact_manifest(
    root: Path,
    *,
    expected_id_key: str,
    expected_id: str,
) -> dict[str, Any]:
    directory = _safe_directory(root, "불변 build")
    manifest = _load_json(
        _safe_regular_file(directory / "build_manifest.json", "build manifest"),
        "build manifest",
    )
    if manifest.get(expected_id_key) != expected_id:
        raise Mix20KV3Error("기존 build identity가 요청과 다릅니다.")
    expected = manifest.get("artifact_sha256")
    if not isinstance(expected, dict) or not expected:
        raise Mix20KV3Error("build artifact hash map이 없습니다.")
    actual = _artifact_hashes(directory, exclude={"build_manifest.json"})
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            name
            for name in set(expected) & set(actual)
            if expected[name] != actual[name]
        )
        raise Mix20KV3Error(
            f"기존 불변 build artifact가 다릅니다: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )
    return manifest


def _output_base(value: Path | None, default: Path, expected_name: str) -> Path:
    candidate = (value or default).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.name != expected_name:
        raise Mix20KV3Error(
            f"출력 base의 마지막 경로는 {expected_name!r}이어야 합니다."
        )
    if candidate.exists() or candidate.is_symlink():
        return _safe_directory(candidate, "output base")
    candidate.mkdir(
        parents=True,
        mode=PRIVATE_DIR_MODE if "derived" in candidate.parts else PUBLIC_DIR_MODE,
    )
    return _safe_directory(candidate, "output base")


def _queue_payload(name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "queue": name,
        "review_id": row["id"],
        "review_record": deepcopy(dict(row)),
        "decision": None,
        "reviewer": None,
        "reviewed_at": None,
    }


def _build_private(
    destination: Path,
    *,
    identity: Mapping[str, Any],
    config: Mapping[str, Any],
    repaired: list[dict[str, Any]],
    repair_report: Mapping[str, Any],
    queues: Mapping[str, list[dict[str, Any]]],
    queue_report: Mapping[str, Any],
    diagnostic: list[dict[str, Any]],
    diagnostic_report: Mapping[str, Any],
    leakage_report: Mapping[str, Any],
    package_integrity: Mapping[str, Any],
) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        return _verify_artifact_manifest(
            destination,
            expected_id_key="build_id",
            expected_id=str(identity["build_id"]),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        training = [_training_projection(row) for row in repaired]
        record_index = [
            {
                "line_number": index,
                "id": row["id"],
                "parent_id": row["repair"]["parent_id"],
                "parent_record_sha256": row["repair"]["parent_record_sha256"],
                "repaired_record_sha256": sha256_json(row),
                "training_record_sha256": sha256_json(training[index - 1]),
                "task_axis": row["task_axis"],
                "restricted_local_only": row["restricted_local_only"],
                "train_candidate": row["train_candidate"],
            }
            for index, row in enumerate(repaired, 1)
        ]
        trajectory_catalog = [
            {
                "schema_version": "1.0.0",
                "trajectory_id": row.get("conversation_id") or row["id"],
                "record_id": row["id"],
                "assistant_turns": _assistant_turns(row["messages"]),
                "target_assistant_message_index": row["target_assistant_message_index"],
                "messages": deepcopy(row["messages"]),
                "tools": deepcopy(row["tools"]),
                "evidence_used": deepcopy(row["evidence_used"]),
                "training_blockers": deepcopy(row["training_eligibility"]["blockers"]),
            }
            for row in repaired
            if _assistant_turns(row["messages"]) >= 2
        ]
        _write_jsonl(
            temporary / "review/mix20k_v3.0.1_review.jsonl",
            repaired,
            mode=PRIVATE_FILE_MODE,
        )
        _write_jsonl(
            temporary / "training/training_mix20k_v3.0.1_candidate.jsonl",
            training,
            mode=PRIVATE_FILE_MODE,
        )
        _write_jsonl(
            temporary / "manifests/record_index.jsonl",
            record_index,
            mode=PRIVATE_FILE_MODE,
        )
        _write_jsonl(
            temporary / "catalog/trajectory_catalog.jsonl",
            trajectory_catalog,
            mode=PRIVATE_FILE_MODE,
        )
        _write_jsonl(
            temporary / "diagnostic/diagnostic_2k.jsonl",
            [_training_projection(row) for row in diagnostic],
            mode=PRIVATE_FILE_MODE,
        )
        for name, values in queues.items():
            _write_jsonl(
                temporary / f"review_queues/{name}.jsonl",
                [_queue_payload(name, row) for row in values],
                mode=PRIVATE_FILE_MODE,
            )
        for name, value in (
            ("repair_summary.json", repair_report),
            ("review_queue_summary.json", queue_report),
            ("diagnostic_summary.json", diagnostic_report),
            ("leakage_report.json", leakage_report),
        ):
            _write_json(temporary / f"reports/{name}", value, mode=PRIVATE_FILE_MODE)
        artifacts = _artifact_hashes(temporary, exclude=set())
        manifest = {
            "schema_version": "1.0.0",
            "dataset_version": config["repair_version"],
            "build_id": identity["build_id"],
            "build_sha256": identity["build_sha256"],
            "generated_at": _utc_now(),
            "build_inputs": identity["build_inputs"],
            "source_package": package_integrity,
            "artifact_sha256": artifacts,
            "rows": {
                "review": len(repaired),
                "training_candidate_projection": len(training),
                "diagnostic": len(diagnostic),
                "trajectory_catalog": len(trajectory_catalog),
                "internal_review_queue": queue_report["internal_queue_rows"],
                "external_safe_queue": queue_report["external_safe_rows"],
            },
            "privacy": {
                "contains_restricted_local_only_rows": True,
                "restricted_local_only_rows": repair_report[
                    "restricted_local_only_rows"
                ],
                "public_redistribution_allowed": False,
            },
            "governance": deepcopy(config["governance"]),
            "phase5_training_performed": False,
        }
        _write_json(temporary / "build_manifest.json", manifest, mode=PRIVATE_FILE_MODE)
        for directory in [temporary, *temporary.rglob("*")]:
            if directory.is_dir():
                directory.chmod(PRIVATE_DIR_MODE)
        _replace_directory(temporary, destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _build_public(
    destination: Path,
    *,
    identity: Mapping[str, Any],
    package_integrity: Mapping[str, Any],
    audits: Mapping[str, Mapping[str, Any]],
    repair_report: Mapping[str, Any],
    queue_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    leakage_report: Mapping[str, Any],
    private_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        return _verify_artifact_manifest(
            destination,
            expected_id_key="intake_id",
            expected_id=str(identity["intake_id"]),
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    temporary.chmod(PUBLIC_DIR_MODE)
    try:
        reports = {
            "package_integrity.json": package_integrity,
            "semantic_audit.json": audits["semantic_audit"],
            "provenance_audit.json": audits["provenance_audit"],
            "duplicate_audit.json": audits["duplicate_audit"],
            "tool_audit.json": audits["tool_audit"],
            "leakage_report.json": leakage_report,
            "repair_summary.json": repair_report,
            "review_queue_summary.json": queue_report,
            "diagnostic_summary.json": diagnostic_report,
            "build_reference.json": {
                "schema_version": "1.0.0",
                "intake_id": identity["intake_id"],
                "intake_sha256": identity["intake_sha256"],
                "private_build_id": identity["build_id"],
                "private_build_sha256": identity["build_sha256"],
                "private_manifest_sha256": _sha256_bytes(_json_bytes(private_manifest)),
                "source_review_sha256": package_integrity["review_sha256"],
                "training_promotion_allowed": False,
                "production_promotion_allowed": False,
                "phase5_training_performed": False,
                "raw_samples_in_report": False,
            },
        }
        for name, value in reports.items():
            _write_json(temporary / name, value, mode=PUBLIC_FILE_MODE)
        artifacts = _artifact_hashes(temporary, exclude=set())
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "mix20k_v3_intake_and_repair",
            "intake_id": identity["intake_id"],
            "intake_sha256": identity["intake_sha256"],
            "generated_at": _utc_now(),
            "private_build_id": identity["build_id"],
            "private_build_sha256": identity["build_sha256"],
            "artifact_sha256": artifacts,
            "status": "repaired_candidate_blocked_before_training",
            "public_reports_are_aggregate_only": True,
            "restricted_content_in_public_reports": False,
            "raw_samples_in_report": False,
            "training_promotion_allowed": False,
            "production_promotion_allowed": False,
            "phase5_training_performed": False,
        }
        _write_json(temporary / "build_manifest.json", manifest, mode=PUBLIC_FILE_MODE)
        for directory in [temporary, *temporary.rglob("*")]:
            if directory.is_dir():
                directory.chmod(PUBLIC_DIR_MODE)
        _replace_directory(temporary, destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _verify_projection(
    review: Sequence[Mapping[str, Any]],
    training: Sequence[Mapping[str, Any]],
) -> None:
    if len(review) != 20_000 or len(training) != 20_000:
        raise Mix20KV3Error("보정 review/training projection 수량이 다릅니다.")
    for line_number, (row, projected) in enumerate(
        zip(review, training, strict=True), 1
    ):
        expected = _training_projection(row)
        if projected != expected:
            raise Mix20KV3Error(
                f"training projection이 review와 다릅니다: {line_number}"
            )


def verify_private_build(root: Path, expected_id: str | None = None) -> dict[str, Any]:
    directory = _safe_directory(root, "private repaired build")
    manifest = _load_json(directory / "build_manifest.json", "private build manifest")
    build_id = str(manifest.get("build_id"))
    if expected_id is not None and build_id != expected_id:
        raise Mix20KV3Error("private build ID가 예상과 다릅니다.")
    _verify_artifact_manifest(
        directory, expected_id_key="build_id", expected_id=build_id
    )
    review = _read_jsonl(
        directory / "review/mix20k_v3.0.1_review.jsonl", "repaired review"
    )
    training = _read_jsonl(
        directory / "training/training_mix20k_v3.0.1_candidate.jsonl",
        "repaired training",
    )
    _verify_projection(review, training)
    if (
        Counter(row["task_axis"] for row in review)
        != Counter(manifest["rows"].get("axis_counts", {}))
        and "axis_counts" in manifest["rows"]
    ):
        raise Mix20KV3Error("private manifest axis count가 다릅니다.")
    restricted = sum(row.get("restricted_local_only") is True for row in review)
    if restricted != 3200:
        raise Mix20KV3Error("private build restricted 전파 수량이 다릅니다.")
    for row in review:
        if _validate_message_structure(row):
            raise Mix20KV3Error(f"private row 구조가 다릅니다: {row.get('id')}")
        validate_session_state(row["session_state"])
        for provenance in row["tool_argument_provenance"]:
            message = row["messages"][provenance["message_index"]]
            function = message["tool_calls"][provenance["tool_call_index"]]["function"]
            validate_tool_arguments(function["name"], function["arguments"])
            validate_argument_provenance(
                function["arguments"], provenance["leaf_provenance"]
            )
    queue_names = (
        "expert_interpretation",
        "canonical_engine",
        "workflow_grounding",
        "restricted_empathy",
        "policy",
    )
    seen: set[str] = set()
    for name in queue_names:
        values = _read_jsonl(directory / f"review_queues/{name}.jsonl", f"{name} queue")
        for value in values:
            review_id = str(value.get("review_id"))
            if review_id in seen:
                raise Mix20KV3Error("private review queue가 겹칩니다.")
            seen.add(review_id)
    if len(seen) != 4000:
        raise Mix20KV3Error("private review queue 합계가 4,000이 아닙니다.")
    external = _read_jsonl(
        directory / "review_queues/external_safe.jsonl", "external safe queue"
    )
    if len(external) != 4000 or any(
        value.get("review_record", {}).get("restricted_local_only") is not False
        for value in external
    ):
        raise Mix20KV3Error("external safe queue privacy 계약이 다릅니다.")
    diagnostic = _read_jsonl(
        directory / "diagnostic/diagnostic_2k.jsonl", "diagnostic 2k"
    )
    if len(diagnostic) != 2000:
        raise Mix20KV3Error("diagnostic 수량이 2,000이 아닙니다.")
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "build_id": build_id,
        "build_sha256": manifest.get("build_sha256"),
        "manifest_sha256": _sha256_file(directory / "build_manifest.json"),
        "review_rows": len(review),
        "training_rows": len(training),
        "restricted_rows": restricted,
        "internal_review_queue_rows": len(seen),
        "external_safe_rows": len(external),
        "diagnostic_rows": len(diagnostic),
        "phase5_training_performed": False,
    }


def _validate_public_private_reference(
    manifest: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    expected_private_build_id: str | None,
    expected_private_build_sha256: str | None,
    expected_private_manifest_sha256: str | None,
) -> None:
    if (
        reference.get("intake_id") != manifest.get("intake_id")
        or reference.get("intake_sha256") != manifest.get("intake_sha256")
        or reference.get("private_build_id") != manifest.get("private_build_id")
        or reference.get("private_build_sha256")
        != manifest.get("private_build_sha256")
    ):
        raise Mix20KV3Error("public manifest와 private build reference가 다릅니다.")
    expected = (
        ("private_build_id", expected_private_build_id),
        ("private_build_sha256", expected_private_build_sha256),
        ("private_manifest_sha256", expected_private_manifest_sha256),
    )
    for key, value in expected:
        if value is not None and reference.get(key) != value:
            raise Mix20KV3Error(f"public {key}가 선택한 private build와 다릅니다.")


def verify_public_build(
    root: Path,
    expected_id: str | None = None,
    *,
    expected_private_build_id: str | None = None,
    expected_private_build_sha256: str | None = None,
    expected_private_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    directory = _safe_directory(root, "public intake build")
    manifest = _load_json(directory / "build_manifest.json", "public build manifest")
    intake_id = str(manifest.get("intake_id"))
    if expected_id is not None and intake_id != expected_id:
        raise Mix20KV3Error("public intake ID가 예상과 다릅니다.")
    _verify_artifact_manifest(
        directory, expected_id_key="intake_id", expected_id=intake_id
    )
    reference = _load_json(
        directory / "build_reference.json", "public private build reference"
    )
    _validate_public_private_reference(
        manifest,
        reference,
        expected_private_build_id=expected_private_build_id,
        expected_private_build_sha256=expected_private_build_sha256,
        expected_private_manifest_sha256=expected_private_manifest_sha256,
    )
    forbidden = (
        '"messages":',
        '"prompt_messages":',
        '"reference_assistant":',
        '"review_id":',
        "aihub-talk:",
    )
    for path in directory.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        if path.name != "build_manifest.json" and any(
            token in text for token in forbidden
        ):
            raise Mix20KV3Error(f"public report에 raw/ID field가 있습니다: {path.name}")
    if (
        manifest.get("restricted_content_in_public_reports") is not False
        or manifest.get("training_promotion_allowed") is not False
        or manifest.get("phase5_training_performed") is not False
    ):
        raise Mix20KV3Error("public governance 계약이 다릅니다.")
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "intake_id": intake_id,
        "private_build_id": reference.get("private_build_id"),
        "aggregate_only": True,
        "restricted_content": False,
    }


def _load_source_rows(
    package_dir: Path, config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = _safe_directory(package_dir, "MIX20K-v3 package")
    source = config["source_package"]
    review = _read_jsonl(_relative_member(root, source["review_file"]), "source review")
    training = _read_jsonl(
        _relative_member(root, source["training_file"]), "source training"
    )
    return review, training


def audit_command(
    package_dir: Path,
    config_path: Path,
) -> dict[str, Any]:
    config = _config(config_path)
    package_integrity = verify_source_package(package_dir, config)
    review, training = _load_source_rows(package_dir, config)
    audits = audit_rows(review, training, config)
    return {
        "package_integrity": package_integrity,
        **audits,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }


def build_command(
    package_dir: Path,
    config_path: Path,
    *,
    private_base: Path | None,
    public_base: Path | None,
    blind_hash_manifest: Path | None,
) -> dict[str, Any]:
    config = _config(config_path)
    package_integrity = verify_source_package(package_dir, config)
    review, training = _load_source_rows(package_dir, config)
    audits = audit_rows(review, training, config)
    repaired, repair_report = repair_rows(review, config)
    queues, queue_report = build_review_queues(repaired, config)
    diagnostic, diagnostic_report = build_diagnostic(repaired, config)
    leakage_report = build_leakage_report(
        repaired, blind_hash_manifest=blind_hash_manifest
    )
    identity = _build_identity(
        config_path, config, package_integrity, leakage_report
    )
    private_default = _resolve_repo_path(
        config["outputs"]["private_root"].format(build_id=identity["build_id"])
    ).parent
    public_default = _resolve_repo_path(
        config["outputs"]["public_root"].format(intake_id=identity["intake_id"])
    ).parent
    private_root = _output_base(
        private_base,
        private_default,
        "mix20k-v3.0.1-repaired",
    ) / str(identity["build_id"])
    public_root = _output_base(
        public_base,
        public_default,
        "v3.0.0",
    ) / str(identity["intake_id"])
    private_manifest = _build_private(
        private_root,
        identity=identity,
        config=config,
        repaired=repaired,
        repair_report=repair_report,
        queues=queues,
        queue_report=queue_report,
        diagnostic=diagnostic,
        diagnostic_report=diagnostic_report,
        leakage_report=leakage_report,
        package_integrity=package_integrity,
    )
    public_manifest = _build_public(
        public_root,
        identity=identity,
        package_integrity=package_integrity,
        audits=audits,
        repair_report=repair_report,
        queue_report=queue_report,
        diagnostic_report=diagnostic_report,
        leakage_report=leakage_report,
        private_manifest=private_manifest,
    )
    private_verify = verify_private_build(
        private_root, expected_id=str(identity["build_id"])
    )
    public_verify = verify_public_build(
        public_root,
        expected_id=str(identity["intake_id"]),
        expected_private_build_id=str(identity["build_id"]),
        expected_private_build_sha256=str(identity["build_sha256"]),
        expected_private_manifest_sha256=_sha256_file(
            private_root / "build_manifest.json"
        ),
    )
    return {
        "schema_version": "1.0.0",
        "status": "built_and_verified",
        "build_id": identity["build_id"],
        "build_sha256": identity["build_sha256"],
        "intake_id": identity["intake_id"],
        "intake_sha256": identity["intake_sha256"],
        "private_root": str(private_root),
        "public_root": str(public_root),
        "private_manifest_sha256": _sha256_file(private_root / "build_manifest.json"),
        "public_manifest_sha256": _sha256_file(public_root / "build_manifest.json"),
        "private_verify": private_verify,
        "public_verify": public_verify,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "public_artifacts": len(public_manifest["artifact_sha256"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX20K-v3 외부 후보를 감사하고 v3.0.1 보정 후보를 만든다."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="저장소 상대 또는 절대 repair config 경로",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="원본 package를 변경 없이 감사한다.")
    audit.add_argument("--source-dir", type=Path, required=True)
    build = commands.add_parser(
        "build", help="감사 후 private 보정 build와 public 집계 보고서를 만든다."
    )
    build.add_argument("--source-dir", type=Path, required=True)
    build.add_argument(
        "--private-base",
        type=Path,
        help="마지막 경로명이 mix20k-v3.0.1-repaired인 private base",
    )
    build.add_argument(
        "--public-base",
        type=Path,
        help="마지막 경로명이 v3.0.0인 public report base",
    )
    build.add_argument(
        "--blind-hash-manifest",
        type=Path,
        help="blind payload가 아닌 blind_components_350.jsonl hash manifest",
    )
    verify = commands.add_parser(
        "verify", help="기존 불변 build hash와 계약을 검증한다."
    )
    verify.add_argument("--private-build", type=Path, required=True)
    verify.add_argument("--public-build", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config_path = arguments.config
    if not config_path.is_absolute():
        config_path = (REPO_ROOT / config_path).resolve()
    try:
        if arguments.command == "audit":
            result = audit_command(arguments.source_dir, config_path)
        elif arguments.command == "build":
            result = build_command(
                arguments.source_dir,
                config_path,
                private_base=arguments.private_base,
                public_base=arguments.public_base,
                blind_hash_manifest=arguments.blind_hash_manifest,
            )
        else:
            private = verify_private_build(arguments.private_build)
            result = {
                "private": private,
                "public": verify_public_build(
                    arguments.public_build,
                    expected_private_build_id=str(private["build_id"]),
                    expected_private_build_sha256=str(private["build_sha256"]),
                    expected_private_manifest_sha256=str(private["manifest_sha256"]),
                ),
                "training_promotion_allowed": False,
                "phase5_training_performed": False,
            }
    except Mix20KV3Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
