# mix2k_v4_teachers.py - subscription teacher 교차 초안·검수를 재개 가능하게 실행한다.

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_build import DEFAULT_CONFIG, _load_config
from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_ROWS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    Mix2KV4ContractError,
    jsonl_bytes,
    normalize_answer,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    validate_draft,
    validate_review,
    validate_specs,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes

DEFAULT_OUTPUT_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/teachers/v1.0.0"
)
RUNNER_PATH = Path(__file__).resolve()
CONTRACTS_PATH = RUNNER_PATH.with_name("mix2k_v4_contracts.py")
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 32 * 1024 * 1024
STATE_SCHEMA_VERSION = "1.0.0"
PROVIDER_NAMES = frozenset({"claude", "codex"})
SECRET_ENV = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|ACCESS[_-]?KEY)",
    re.IGNORECASE,
)
FORBIDDEN_INFERENCE = (
    "제공되지 않은 통근·득령·뿌리 판정",
    "신강약·격국·용신",
    "원국×기간의 합·충·형·파·해를 새로 계산",
    "대운 또는 제공 범위 밖 기간 풀이",
    "특정 사건·합격·결혼·재물 예측",
    "입력에 없는 간지·십신·날짜 추가",
    "period year/month/day label을 서로 바꿔 부르기",
    "일주 하나를 원국 전체라고 부르기",
    "K0가 생성한 사주 fact를 Gold로 사용",
)


class Mix2KV4TeacherError(RuntimeError):
    """subscription teacher pipeline이 fail-closed 계약을 충족하지 못함."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4TeacherError(f"{label} 경로에 symlink component가 있습니다.")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_JSON_BYTES
    ):
        raise Mix2KV4TeacherError(f"{label}이 없거나 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4TeacherError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4TeacherError(f"{label} 최상위는 object여야 합니다.")
    return value


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def subscription_environment() -> dict[str, str]:
    """OAuth/subscription auth만 남기고 API·cloud credential은 자식에서 제거한다."""

    environment = dict(os.environ)
    for key in list(environment):
        if SECRET_ENV.search(key):
            environment.pop(key, None)
    for key in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "OPENAI_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        environment.pop(key, None)
    environment["NO_COLOR"] = "1"
    return environment


def _auth_check(environment: Mapping[str, str]) -> dict[str, str]:
    if shutil.which("claude") is None or shutil.which("codex") is None:
        raise Mix2KV4TeacherError("Claude·Codex subscription CLI가 모두 필요합니다.")
    try:
        claude = subprocess.run(
            ["claude", "auth", "status", "--json"],
            env=dict(environment),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        claude_status = json.loads(claude.stdout) if claude.returncode == 0 else None
        codex = subprocess.run(
            ["codex", "login", "status"],
            env=dict(environment),
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise Mix2KV4TeacherError("subscription CLI auth 상태를 확인하지 못했습니다.") from exc
    if (
        not isinstance(claude_status, dict)
        or claude_status.get("loggedIn") is not True
        or claude_status.get("authMethod") != "claude.ai"
        or not claude_status.get("subscriptionType")
    ):
        raise Mix2KV4TeacherError("Claude가 claude.ai subscription auth 상태가 아닙니다.")
    codex_status = (codex.stdout + codex.stderr).casefold()
    if codex.returncode != 0 or "chatgpt" not in codex_status:
        raise Mix2KV4TeacherError("Codex가 ChatGPT subscription auth 상태가 아닙니다.")
    return {"claude": "claude.ai_subscription", "codex": "chatgpt_subscription"}


def _validate_spec_build(
    spec_build: Path, config_path: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if spec_build.is_symlink() or not spec_build.is_dir() or not spec_build.is_absolute():
        raise Mix2KV4TeacherError("spec build는 symlink가 아닌 절대경로 디렉터리여야 합니다.")
    config = _load_config(config_path)
    manifest_path = spec_build / "build_manifest.json"
    manifest = _load_json(manifest_path, "spec build manifest")
    specs_path = spec_build / "training/specs_2000.jsonl"
    if specs_path.is_symlink() or not specs_path.is_file():
        raise Mix2KV4TeacherError("training spec이 없거나 symlink입니다.")
    if (
        manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("build_id") != spec_build.name
        or manifest.get("development_frozen_before_teacher_generation") is not True
        or manifest.get("teacher_target_access_allowed") is not False
        or manifest.get("training_execution_allowed") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("identity", {}).get("config_sha256") != sha256_file(config_path)
        or manifest.get("artifact_sha256", {}).get("training/specs_2000.jsonl")
        != sha256_file(specs_path)
    ):
        raise Mix2KV4TeacherError("spec build identity·dev 격리 계약이 다릅니다.")
    try:
        specs = read_jsonl(specs_path)
        validate_specs(specs, config)
    except Mix2KV4ContractError as exc:
        raise Mix2KV4TeacherError(str(exc)) from exc
    return config, manifest, specs


def _draft_schema(record_ids: Sequence[str]) -> dict[str, Any]:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "record_id",
            "answer",
            "used_fact_paths",
            "used_fact_values",
            "soft_interpretation_used",
            "limitations",
            "self_check",
        ],
        "properties": {
            "record_id": {"type": "string", "enum": list(record_ids)},
            "answer": {"type": "string", "minLength": 1},
            "used_fact_paths": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "used_fact_values": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
            },
            "soft_interpretation_used": {"type": "boolean"},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "self_check": {"type": "string", "enum": ["PASS"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["drafts"],
        "properties": {
            "drafts": {
                "type": "array",
                "minItems": len(record_ids),
                "maxItems": len(record_ids),
                "items": item,
            }
        },
    }


def _review_schema(record_ids: Sequence[str]) -> dict[str, Any]:
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "record_id",
            "decision",
            "failure_codes",
            "fact_errors",
            "style_notes",
            "rewrite_instructions",
        ],
        "properties": {
            "record_id": {"type": "string", "enum": list(record_ids)},
            "decision": {"type": "string", "enum": ["PASS", "FAIL"]},
            "failure_codes": {"type": "array", "items": {"type": "string"}},
            "fact_errors": {"type": "array", "items": {"type": "string"}},
            "style_notes": {"type": "array", "items": {"type": "string"}},
            "rewrite_instructions": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["reviews"],
        "properties": {
            "reviews": {
                "type": "array",
                "minItems": len(record_ids),
                "maxItems": len(record_ids),
                "items": item,
            }
        },
    }


def _task_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    system = spec["prompt"][0]["content"]
    marker = "\n\n[서버에서 계산한 승인 원국·단일 일진 사실]"
    if spec["runtime_binding"] is not None and marker in system:
        system = system.split(marker, 1)[0]
    return {
        "record_id": spec["id"],
        "task_axis": spec["task_axis"],
        "production_system_instruction": system,
        "conversation": spec["prompt"][1:],
        "response_contract": spec["response_contract"],
    }


def _evidence_payload(spec: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"path": path, "value": value}
        for path, value in zip(
            spec["allowed_fact_paths"], spec["allowed_fact_values"], strict=True
        )
    ]


def draft_prompt(
    specs: Sequence[Mapping[str, Any]], feedback: Mapping[str, str]
) -> str:
    sections: list[str] = [
        (
            "당신은 K0의 자연스러운 한국어 설명·후속 응답 능력을 보존하면서 "
            "구조화 사실 grounding을 교정하는 teacher입니다. K0의 사주 fact는 "
            "Gold가 아니며, 아래 RAW·ALLOWED에 없는 사실은 추가하지 마세요. 모든 record에 "
            "대해 서로 독립된 JSON draft를 하나씩 작성하세요. 실질 답변은 최소 3개의 "
            "완결 문장과 3개의 의미 있는 줄을 쓰고, 1줄 계약인 intake·HARD QA만 예외입니다. "
            "답을 짧게 끝내려고 하지 말고 질문에 필요한 만큼 자연스럽게 풀어 쓰세요. "
            "사용자 답변에 JSON·runtime·allowlist·Gold 같은 내부 용어를 노출하지 마세요. "
            "used_fact_paths·used_fact_values에는 answer에 명시한 날짜·간지·십신의 "
            "정확한 ALLOWED 값을 누락 없이 기록하세요. 도구를 사용하지 마세요."
        )
    ]
    for spec in specs:
        raw = (
            spec["runtime_binding"]["value"]
            if spec["runtime_binding"] is not None
            else None
        )
        sections.extend(
            [
                f"\n=== RECORD {spec['id']} ===",
                "[RAW RUNTIME FACTS]\n"
                + json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                "[ALLOWED EVIDENCE]\n"
                + json.dumps(
                    _evidence_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "[FORBIDDEN INFERENCE]\n- " + "\n- ".join(FORBIDDEN_INFERENCE),
                "[TASK]\n"
                + json.dumps(
                    _task_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ]
        )
        if feedback.get(spec["id"]):
            sections.append("[REWRITE FEEDBACK]\n" + feedback[spec["id"]])
    return "\n".join(sections)


def review_prompt(
    specs: Sequence[Mapping[str, Any]], drafts: Mapping[str, Mapping[str, Any]]
) -> str:
    sections: list[str] = [
        (
            "당신은 반대 teacher의 초안을 교차 검수합니다. 정확한 field·label grounding, "
            "제공되지 않은 관계·신강약·예측 금지, 후속 evidence 일관성, 일반인이 "
            "이해할 수 있는 자연스러운 한국어, 3줄·3문장 계약, 의미 없는 입력 재진술 "
            "여부를 모두 보세요. 하나라도 문제가 있으면 FAIL이며 구체적 rewrite_instructions를 "
            "적습니다. PASS일 때 failure_codes, fact_errors, rewrite_instructions는 비우세요. "
            "도구를 사용하지 마세요."
        )
    ]
    for spec in specs:
        raw = (
            spec["runtime_binding"]["value"]
            if spec["runtime_binding"] is not None
            else None
        )
        sections.extend(
            [
                f"\n=== RECORD {spec['id']} ===",
                "[RAW RUNTIME FACTS]\n"
                + json.dumps(raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                "[ALLOWED EVIDENCE]\n"
                + json.dumps(
                    _evidence_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "[FORBIDDEN INFERENCE]\n- " + "\n- ".join(FORBIDDEN_INFERENCE),
                "[TASK]\n"
                + json.dumps(
                    _task_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "[DRAFT TO REVIEW]\n"
                + json.dumps(
                    drafts[spec["id"]],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ]
        )
    return "\n".join(sections)


def _provider_call(
    *,
    provider: str,
    prompt: str,
    schema: Mapping[str, Any],
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"mix2k-v4-{provider}-") as directory:
        working = Path(directory)
        working.chmod(PRIVATE_DIR_MODE)
        if provider == "claude":
            command = [
                "claude",
                "-p",
                "--safe-mode",
                "--disable-slash-commands",
                "--tools",
                "",
                "--permission-mode",
                "dontAsk",
                "--no-session-persistence",
                "--model",
                "sonnet",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema, separators=(",", ":"), sort_keys=True),
            ]
            output_path = None
        elif provider == "codex":
            schema_path = working / "output.schema.json"
            output_path = working / "last-message.json"
            schema_path.write_bytes(_json_bytes(schema))
            schema_path.chmod(PRIVATE_FILE_MODE)
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--cd",
                str(working),
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        else:
            raise Mix2KV4TeacherError(f"알 수 없는 provider입니다: {provider}")
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
            raise Mix2KV4TeacherError(f"{provider} subscription call이 중단됐습니다.") from exc
        if result.returncode != 0:
            raise Mix2KV4TeacherError(
                f"{provider} subscription call이 exit {result.returncode}로 실패했습니다."
            )
        try:
            if provider == "claude":
                if len(result.stdout.encode("utf-8")) > MAX_PROVIDER_OUTPUT_BYTES:
                    raise Mix2KV4TeacherError("Claude output이 용량 상한을 넘었습니다.")
                envelope = json.loads(result.stdout)
                structured = envelope.get("structured_output")
            else:
                if output_path is None or not output_path.is_file():
                    raise Mix2KV4TeacherError("Codex structured output 파일이 없습니다.")
                if output_path.stat().st_size > MAX_PROVIDER_OUTPUT_BYTES:
                    raise Mix2KV4TeacherError("Codex output이 용량 상한을 넘었습니다.")
                structured = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            if isinstance(exc, Mix2KV4TeacherError):
                raise
            raise Mix2KV4TeacherError(f"{provider} structured output을 읽지 못했습니다.") from exc
        if not isinstance(structured, dict):
            raise Mix2KV4TeacherError(f"{provider} structured output이 object가 아닙니다.")
    return {
        "structured": structured,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _result_map(
    value: Any, *, key: str, record_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    rows = value.get(key) if isinstance(value, dict) else None
    if not isinstance(rows, list) or len(rows) != len(record_ids):
        raise Mix2KV4TeacherError(f"provider {key} 행 수가 다릅니다.")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("record_id"), str):
            raise Mix2KV4TeacherError(f"provider {key} record identity가 다릅니다.")
        if row["record_id"] in result:
            raise Mix2KV4TeacherError(f"provider {key}에 중복 record가 있습니다.")
        result[row["record_id"]] = row
    if set(result) != set(record_ids):
        raise Mix2KV4TeacherError(f"provider {key} record 집합이 다릅니다.")
    return result


def _selection(
    specs: Sequence[dict[str, Any]], mode: str, rows_per_provider: int
) -> list[dict[str, Any]]:
    if mode == "full":
        return list(specs)
    selected: list[dict[str, Any]] = []
    for provider in ("claude", "codex"):
        eligible = [row for row in specs if row["drafter"] == provider]
        selected.extend(eligible[:rows_per_provider])
    order = {row["id"]: index for index, row in enumerate(specs)}
    return sorted(selected, key=lambda row: order[row["id"]])


def _new_state(
    *,
    mode: str,
    selected: Sequence[Mapping[str, Any]],
    config_path: Path,
    spec_manifest_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "mode": mode,
        "runner_sha256": sha256_file(RUNNER_PATH),
        "contracts_sha256": sha256_file(CONTRACTS_PATH),
        "config_sha256": sha256_file(config_path),
        "spec_manifest_sha256": sha256_file(spec_manifest_path),
        "selection_sha256": sha256_bytes(
            canonical_json_bytes([row["id"] for row in selected])
        ),
        "selection_order": [row["id"] for row in selected],
        "provider_calls": 0,
        "records": {
            row["id"]: {
                "spec_sha256": sha256_bytes(canonical_json_bytes(row)),
                "status": "needs_draft",
                "rewrites_used": 0,
                "feedback": "",
                "draft_attempts": [],
                "review_attempts": [],
                "current_draft": None,
                "accepted": None,
            }
            for row in selected
        },
    }


def _state_path(target: Path) -> Path:
    return target / "pipeline_state.json"


def _load_or_create_state(
    *,
    target: Path,
    mode: str,
    selected: Sequence[Mapping[str, Any]],
    config_path: Path,
    spec_manifest_path: Path,
) -> dict[str, Any]:
    expected = _new_state(
        mode=mode,
        selected=selected,
        config_path=config_path,
        spec_manifest_path=spec_manifest_path,
    )
    path = _state_path(target)
    if not path.exists():
        _atomic_write(path, _json_bytes(expected))
        return expected
    state = _load_json(path, "teacher pipeline state")
    for key in (
        "schema_version",
        "dataset_version",
        "mode",
        "runner_sha256",
        "contracts_sha256",
        "config_sha256",
        "spec_manifest_sha256",
        "selection_sha256",
        "selection_order",
    ):
        if state.get(key) != expected[key]:
            raise Mix2KV4TeacherError(f"teacher pipeline state {key}가 다릅니다.")
    if set(state.get("records", {})) != set(expected["records"]):
        raise Mix2KV4TeacherError("teacher pipeline state record 집합이 다릅니다.")
    return state


def _status_counts(state: Mapping[str, Any]) -> dict[str, int]:
    return dict(sorted(Counter(row["status"] for row in state["records"].values()).items()))


def _ordered_pending(
    state: Mapping[str, Any], status: str, specs_by_id: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    return [
        record_id
        for record_id in state["selection_order"]
        if state["records"][record_id]["status"] == status
        and record_id in specs_by_id
    ]


def _process_draft_batch(
    *,
    record_ids: Sequence[str],
    provider: str,
    specs_by_id: Mapping[str, dict[str, Any]],
    state: dict[str, Any],
    environment: Mapping[str, str],
    timeout_seconds: int,
    maximum_rewrites: int,
) -> float:
    specs = [specs_by_id[record_id] for record_id in record_ids]
    feedback = {
        record_id: state["records"][record_id]["feedback"]
        for record_id in record_ids
    }
    call = _provider_call(
        provider=provider,
        prompt=draft_prompt(specs, feedback),
        schema=_draft_schema(record_ids),
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    drafts = _result_map(call["structured"], key="drafts", record_ids=record_ids)
    for record_id in record_ids:
        record = state["records"][record_id]
        draft = drafts[record_id]
        attempt = {
            "provider": provider,
            "attempt": len(record["draft_attempts"]) + 1,
            "draft": draft,
            "deterministic_pass": False,
            "deterministic_error": None,
        }
        try:
            validate_draft(specs_by_id[record_id], draft)
        except Mix2KV4ContractError as exc:
            attempt["deterministic_error"] = str(exc)
            record["draft_attempts"].append(attempt)
            if record["rewrites_used"] >= maximum_rewrites:
                record["status"] = "failed"
                record["feedback"] = str(exc)
            else:
                record["rewrites_used"] += 1
                record["status"] = "needs_draft"
                record["feedback"] = "Deterministic validator 실패: " + str(exc)
            continue
        attempt["deterministic_pass"] = True
        record["draft_attempts"].append(attempt)
        record["current_draft"] = draft
        record["status"] = "needs_review"
        record["feedback"] = ""
    state["provider_calls"] += 1
    return float(call["elapsed_seconds"])


def _review_feedback(review: Mapping[str, Any]) -> str:
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


def _process_review_batch(
    *,
    record_ids: Sequence[str],
    provider: str,
    specs_by_id: Mapping[str, dict[str, Any]],
    state: dict[str, Any],
    environment: Mapping[str, str],
    timeout_seconds: int,
    maximum_rewrites: int,
) -> float:
    specs = [specs_by_id[record_id] for record_id in record_ids]
    current = {
        record_id: state["records"][record_id]["current_draft"]
        for record_id in record_ids
    }
    call = _provider_call(
        provider=provider,
        prompt=review_prompt(specs, current),
        schema=_review_schema(record_ids),
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    reviews = _result_map(call["structured"], key="reviews", record_ids=record_ids)
    for record_id in record_ids:
        record = state["records"][record_id]
        review = reviews[record_id]
        try:
            validate_review(specs_by_id[record_id], review)
        except Mix2KV4ContractError as exc:
            raise Mix2KV4TeacherError(str(exc)) from exc
        record["review_attempts"].append(
            {
                "provider": provider,
                "attempt": len(record["review_attempts"]) + 1,
                "review": review,
            }
        )
        if review["decision"] == "PASS":
            try:
                validate_draft(specs_by_id[record_id], record["current_draft"])
            except Mix2KV4ContractError as exc:
                raise Mix2KV4TeacherError(str(exc)) from exc
            record["status"] = "accepted"
            record["accepted"] = {
                "draft_provider": specs_by_id[record_id]["drafter"],
                "review_provider": provider,
                "draft": record["current_draft"],
                "review": review,
            }
            continue
        record["accepted"] = None
        if record["rewrites_used"] >= maximum_rewrites:
            record["status"] = "failed"
            record["feedback"] = _review_feedback(review)
        else:
            record["rewrites_used"] += 1
            record["status"] = "needs_draft"
            record["feedback"] = "Peer review FAIL: " + _review_feedback(review)
    state["provider_calls"] += 1
    return float(call["elapsed_seconds"])


def _duplicate_repairs(
    state: dict[str, Any], *, maximum_rewrites: int, normalized_maximum: int
) -> int:
    exact_groups: dict[str, list[str]] = {}
    normalized_groups: dict[str, list[str]] = {}
    for record_id in state["selection_order"]:
        record = state["records"][record_id]
        if record["status"] != "accepted":
            continue
        answer = record["accepted"]["draft"]["answer"].strip()
        exact_groups.setdefault(answer, []).append(record_id)
        normalized_groups.setdefault(normalize_answer(answer), []).append(record_id)
    repair: set[str] = set()
    for ids in exact_groups.values():
        repair.update(ids[1:])
    for ids in normalized_groups.values():
        repair.update(ids[normalized_maximum:])
    for record_id in repair:
        record = state["records"][record_id]
        record["accepted"] = None
        if record["rewrites_used"] >= maximum_rewrites:
            record["status"] = "failed"
            record["feedback"] = "전체 dataset 답변 중복을 해소하지 못했습니다."
        else:
            record["rewrites_used"] += 1
            record["status"] = "needs_draft"
            record["feedback"] = (
                "전체 dataset에서 답변이 중복됐습니다. 사실은 유지하되 "
                "문장 구조·예시·설명 순서를 자연스럽게 다시 작성하세요."
            )
    return len(repair)


def _candidate_rows(
    state: Mapping[str, Any], specs_by_id: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record_id in state["selection_order"]:
        record = state["records"][record_id]
        if record["status"] != "accepted" or record["accepted"] is None:
            raise Mix2KV4TeacherError("accepted candidate 상태가 완결되지 않았습니다.")
        spec = specs_by_id[record_id]
        accepted = record["accepted"]
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
                "assistant": accepted["draft"]["answer"],
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
                    "used_fact_paths": accepted["draft"]["used_fact_paths"],
                    "used_fact_values": accepted["draft"]["used_fact_values"],
                    "soft_interpretation_used": accepted["draft"][
                        "soft_interpretation_used"
                    ],
                    "limitations": accepted["draft"]["limitations"],
                },
                "restricted_local_only": False,
            }
        )
    return rows


def _write_candidates(
    *,
    target: Path,
    mode: str,
    state: Mapping[str, Any],
    specs_by_id: Mapping[str, Mapping[str, Any]],
    spec_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    rows = _candidate_rows(state, specs_by_id)
    payload = jsonl_bytes(rows)
    relative = (
        "accepted/training_candidates_2000.jsonl"
        if mode == "full"
        else "accepted/pilot_candidates.jsonl"
    )
    path = target / relative
    _atomic_write(path, payload)
    manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "mode": mode,
        "spec_build_id": spec_manifest["build_id"],
        "spec_build_sha256": spec_manifest["build_sha256"],
        "runner_sha256": state["runner_sha256"],
        "contracts_sha256": state["contracts_sha256"],
        "selection_sha256": state["selection_sha256"],
        "rows": len(rows),
        "candidate_path": relative,
        "candidate_sha256": sha256_bytes(payload),
        "teacher_roles": dict(
            sorted(Counter(row["teacher"]["drafter"] for row in rows).items())
        ),
        "review_roles": dict(
            sorted(Counter(row["teacher"]["reviewer"] for row in rows).items())
        ),
        "peer_review_passed": True,
        "deterministic_validation_passed": True,
        "full_runtime_snapshot_used": True,
        "development_targets_accessed": False,
        "api_keys_used": False,
        "sealed_blind_accessed": False,
        "training_execution_allowed": False,
        "training_performed": False,
    }
    _atomic_write(target / "teacher_manifest.json", _json_bytes(manifest))
    return manifest


def run_pipeline(
    *,
    mode: str,
    config_path: Path,
    spec_build: Path,
    output_root: Path,
    rows_per_provider: int,
    shard_rows: int,
    timeout_seconds: int,
    max_provider_calls: int,
) -> dict[str, Any]:
    _reject_symlink_components(config_path, "config")
    _reject_symlink_components(spec_build, "spec build")
    _reject_symlink_components(output_root, "private output")
    config, spec_manifest, specs = _validate_spec_build(spec_build, config_path)
    selected = _selection(specs, mode, rows_per_provider)
    if mode == "full" and len(selected) != EXPECTED_ROWS:
        raise Mix2KV4TeacherError("full teacher selection이 2,000행이 아닙니다.")
    if not selected:
        raise Mix2KV4TeacherError("teacher selection이 비었습니다.")
    runner_sha = sha256_file(RUNNER_PATH)
    code_sha = sha256_bytes(
        canonical_json_bytes(
            {
                "runner_sha256": runner_sha,
                "contracts_sha256": sha256_file(CONTRACTS_PATH),
            }
        )
    )
    selection_sha = sha256_bytes(canonical_json_bytes([row["id"] for row in selected]))
    target = output_root / (
        f"{mode}-{spec_manifest['build_id']}-{code_sha[:8]}-{selection_sha[:8]}"
    )
    target.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    target.chmod(PRIVATE_DIR_MODE)
    lock_path = target / ".pipeline.lock"
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Mix2KV4TeacherError("teacher pipeline이 이미 실행 중입니다.") from exc
        environment = subscription_environment()
        auth = _auth_check(environment)
        state = _load_or_create_state(
            target=target,
            mode=mode,
            selected=selected,
            config_path=config_path,
            spec_manifest_path=spec_build / "build_manifest.json",
        )
        specs_by_id = {row["id"]: row for row in selected}
        maximum_rewrites = int(config["teacher"]["maximum_rewrite_rounds"])
        calls_this_run = 0
        while True:
            counts = _status_counts(state)
            if counts.get("failed", 0):
                break
            review_ids = _ordered_pending(state, "needs_review", specs_by_id)
            draft_ids = _ordered_pending(state, "needs_draft", specs_by_id)
            if not review_ids and not draft_ids:
                repaired = _duplicate_repairs(
                    state,
                    maximum_rewrites=maximum_rewrites,
                    normalized_maximum=int(
                        config["diversity"]["normalized_answer_multiplicity_maximum"]
                    ),
                )
                if repaired:
                    _atomic_write(_state_path(target), _json_bytes(state))
                    print(f"duplicate_repair_scheduled={repaired}", flush=True)
                    continue
                break
            if max_provider_calls and calls_this_run >= max_provider_calls:
                break
            if review_ids:
                first = review_ids[0]
                provider = specs_by_id[first]["reviewer"]
                batch = [
                    record_id
                    for record_id in review_ids
                    if specs_by_id[record_id]["reviewer"] == provider
                ][:shard_rows]
                elapsed = _process_review_batch(
                    record_ids=batch,
                    provider=provider,
                    specs_by_id=specs_by_id,
                    state=state,
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    maximum_rewrites=maximum_rewrites,
                )
                phase = "review"
            else:
                first = draft_ids[0]
                provider = specs_by_id[first]["drafter"]
                batch = [
                    record_id
                    for record_id in draft_ids
                    if specs_by_id[record_id]["drafter"] == provider
                ][:shard_rows]
                elapsed = _process_draft_batch(
                    record_ids=batch,
                    provider=provider,
                    specs_by_id=specs_by_id,
                    state=state,
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                    maximum_rewrites=maximum_rewrites,
                )
                phase = "draft"
            calls_this_run += 1
            _atomic_write(_state_path(target), _json_bytes(state))
            print(
                f"provider_call={state['provider_calls']} phase={phase} provider={provider} "
                f"rows={len(batch)} elapsed={elapsed:.3f}s status={_status_counts(state)}",
                flush=True,
            )
        counts = _status_counts(state)
        complete = counts == {"accepted": len(selected)}
        manifest = None
        if complete:
            manifest = _write_candidates(
                target=target,
                mode=mode,
                state=state,
                specs_by_id=specs_by_id,
                spec_manifest=spec_manifest,
            )
        return {
            "schema_version": "1.0.0",
            "dataset_version": DATASET_VERSION,
            "mode": mode,
            "target": str(target),
            "selected_rows": len(selected),
            "status": counts,
            "complete": complete,
            "provider_calls_total": state["provider_calls"],
            "provider_calls_this_run": calls_this_run,
            "auth": auth,
            "manifest": manifest,
        }
    finally:
        os.close(lock_descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 subscription teacher cross-review pipeline"
    )
    parser.add_argument("mode", choices=("pilot", "full"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--spec-build", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--rows-per-provider", type=int, default=2)
    parser.add_argument("--shard-rows", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--max-provider-calls", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (
        not 1 <= args.rows_per_provider <= 20
        or not 1 <= args.shard_rows <= 20
        or not 60 <= args.timeout_seconds <= 3600
        or args.max_provider_calls < 0
    ):
        print("teacher 실행 숫자 인자가 허용 범위 밖입니다.", file=sys.stderr)
        return 2
    try:
        report = run_pipeline(
            mode=args.mode,
            config_path=_absolute(args.config),
            spec_build=_absolute(args.spec_build),
            output_root=_absolute(args.output_root),
            rows_per_provider=args.rows_per_provider,
            shard_rows=args.shard_rows,
            timeout_seconds=args.timeout_seconds,
            max_provider_calls=args.max_provider_calls,
        )
    except (Mix2KV4TeacherError, Mix2KV4ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
