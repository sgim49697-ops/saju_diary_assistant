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
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_build import (
    BOUND_PROMPT,
    DASHBOARD_CONTEXT_PATH,
    DEFAULT_CONFIG,
    GENERATOR_PATH,
    INTAKE_PROMPT,
    MODEL_PROJECTION_PATH,
    _load_config,
)
from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_ROWS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    Mix2KV4ContractError,
    jsonl_bytes,
    nonempty_lines,
    normalize_answer,
    read_jsonl,
    sentence_count,
    sha256_bytes,
    sha256_file,
    validate_draft,
    validate_review,
    validate_specs,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.chart_day_model_projection import MODEL_PROJECTION_ID

DEFAULT_OUTPUT_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/teachers/v1.0.1"
)
RUNNER_PATH = Path(__file__).resolve()
CONTRACTS_PATH = RUNNER_PATH.with_name("mix2k_v4_contracts.py")
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_OUTPUT_BYTES = 32 * 1024 * 1024
STATE_SCHEMA_VERSION = "1.1.0"
SEED_IMPORT_VERSION = "1.0.0"
EXPECTED_SPEC_BUILD_ID = "build-8ba27d3b5bb0"
EXPECTED_SPEC_BUILD_SHA256 = (
    "8ba27d3b5bb0b8fdb0e4bd4030a87c03c7daab1542e390db638bbc70532069ac"
)
SPEC_IDENTITY_FIELDS = {
    "dataset_version",
    "config_sha256",
    "generator_sha256",
    "contracts_sha256",
    "dashboard_context_source_sha256",
    "model_projection_id",
    "model_projection_source_sha256",
    "bound_prompt_sha256",
    "intake_prompt_sha256",
    "runtime_release_registry_sha256",
    "ephemeris_sha256",
    "runtime_release_id",
    "base_model_repository",
    "base_model_revision",
    "base_model_files",
    "dev_sha256",
    "specs_sha256",
    "projection_report_sha256",
}
PROVIDER_NAMES = frozenset({"claude", "codex"})
LAYOUT_NORMALIZER_VERSION = "sentence-whitespace-v1"
ANSWER_HORIZONTAL_SENTENCE_BOUNDARY = re.compile(
    r"(([다요죠까네군라자])(?:[.!?。！？])(?:[\"'”’)]*))[ \t]+"
)
MEANINGFUL_LAYOUT_CHARACTER = re.compile(r"[0-9A-Za-z가-힣甲-龥]")
UNSAFE_LAYOUT_MARKUP = re.compile(
    r"```|`|https?://|\[[^\]]*\]\(|(?<![A-Za-z])(?:[A-Za-z]\.){2,}|"
    r"(?:^|\n)\s*(?:>|[-*+]|\d{1,3}[.)])\s+",
    re.MULTILINE,
)
CODEX_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "multi_agent",
    "plugin_sharing",
    "remote_plugin",
    "shell_snapshot",
    "shell_tool",
    "skill_search",
    "tool_suggest",
    "unified_exec",
    "view_image",
    "workspace_dependencies",
)
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
    _reject_symlink_components(path, label)
    if (
        not path.is_absolute()
        or path.is_symlink()
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
    if not path.is_absolute():
        raise Mix2KV4TeacherError("teacher output file은 절대경로여야 합니다.")
    _reject_symlink_components(path, "teacher output file")
    _ensure_private_directory(path.parent, "teacher output parent")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise Mix2KV4TeacherError("기존 teacher output file이 안전하지 않습니다.")
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


def _ensure_private_directory(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise Mix2KV4TeacherError(f"{label}은 절대경로여야 합니다.")
    _reject_symlink_components(path, label)
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise Mix2KV4TeacherError(f"{label} 경로가 안전하지 않습니다.")
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    _reject_symlink_components(path, label)
    path.chmod(PRIVATE_DIR_MODE)


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
        raise Mix2KV4TeacherError(
            "subscription CLI auth 상태를 확인하지 못했습니다."
        ) from exc
    if (
        not isinstance(claude_status, dict)
        or claude_status.get("loggedIn") is not True
        or claude_status.get("authMethod") != "claude.ai"
        or not claude_status.get("subscriptionType")
    ):
        raise Mix2KV4TeacherError(
            "Claude가 claude.ai subscription auth 상태가 아닙니다."
        )
    codex_status = (codex.stdout + codex.stderr).casefold()
    if codex.returncode != 0 or "chatgpt" not in codex_status:
        raise Mix2KV4TeacherError("Codex가 ChatGPT subscription auth 상태가 아닙니다.")
    return {"claude": "claude.ai_subscription", "codex": "chatgpt_subscription"}


def _validate_spec_build(
    spec_build: Path, config_path: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if (
        spec_build.is_symlink()
        or not spec_build.is_dir()
        or not spec_build.is_absolute()
    ):
        raise Mix2KV4TeacherError(
            "spec build는 symlink가 아닌 절대경로 디렉터리여야 합니다."
        )
    config = _load_config(config_path)
    manifest_path = spec_build / "build_manifest.json"
    manifest = _load_json(manifest_path, "spec build manifest")
    identity = manifest.get("identity")
    specs_path = spec_build / "training/specs_2000.jsonl"
    dev_path = spec_build / "evaluation/dev_cases_200.jsonl"
    projection_path = spec_build / "reports/full_runtime_projection_ab.json"
    for path, label in (
        (manifest_path, "spec build manifest"),
        (specs_path, "training spec"),
        (dev_path, "frozen dev"),
        (projection_path, "projection report"),
    ):
        _reject_symlink_components(path, label)
    if specs_path.is_symlink() or not specs_path.is_file():
        raise Mix2KV4TeacherError("training spec이 없거나 symlink입니다.")
    artifact_sha = manifest.get("artifact_sha256")
    expected_artifacts = {
        "evaluation/dev_cases_200.jsonl": (dev_path, "dev_sha256"),
        "training/specs_2000.jsonl": (specs_path, "specs_sha256"),
        "reports/full_runtime_projection_ab.json": (
            projection_path,
            "projection_report_sha256",
        ),
    }
    if (
        not isinstance(identity, Mapping)
        or set(identity) != SPEC_IDENTITY_FIELDS
        or manifest.get("schema_version") != "1.0.0"
        or manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("build_id") != EXPECTED_SPEC_BUILD_ID
        or manifest.get("build_id") != spec_build.name
        or manifest.get("build_sha256") != EXPECTED_SPEC_BUILD_SHA256
        or manifest.get("build_sha256") != sha256_bytes(canonical_json_bytes(identity))
        or identity.get("dataset_version") != DATASET_VERSION
        or identity.get("config_sha256") != sha256_file(config_path)
        or identity.get("generator_sha256") != sha256_file(GENERATOR_PATH)
        or identity.get("contracts_sha256") != sha256_file(CONTRACTS_PATH)
        or identity.get("dashboard_context_source_sha256")
        != sha256_file(DASHBOARD_CONTEXT_PATH)
        or identity.get("model_projection_id") != MODEL_PROJECTION_ID
        or identity.get("model_projection_id")
        != config["runtime"]["model_projection_id"]
        or identity.get("model_projection_source_sha256")
        != sha256_file(MODEL_PROJECTION_PATH)
        or identity.get("bound_prompt_sha256") != sha256_file(BOUND_PROMPT)
        or identity.get("intake_prompt_sha256") != sha256_file(INTAKE_PROMPT)
        or identity.get("runtime_release_registry_sha256")
        != sha256_file(REPO_ROOT / config["runtime"]["release_registry"])
        or identity.get("runtime_release_id") != config["runtime"]["release_id"]
        or identity.get("base_model_repository") != config["base_model"]["repository"]
        or identity.get("base_model_revision") != config["base_model"]["revision"]
        or identity.get("base_model_files") != config["base_model"]["files"]
        or manifest.get("rows")
        != {"development_evaluation": 200, "training_specs": EXPECTED_ROWS}
        or manifest.get("development_frozen_before_teacher_generation") is not True
        or manifest.get("teacher_target_access_allowed") is not False
        or manifest.get("full_runtime_snapshot_used") is not True
        or manifest.get("training_execution_allowed") is not False
        or manifest.get("training_performed") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or not isinstance(artifact_sha, Mapping)
        or set(artifact_sha) != set(expected_artifacts)
        or any(
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != artifact_sha.get(relative)
            or artifact_sha.get(relative) != identity.get(identity_field)
            for relative, (path, identity_field) in expected_artifacts.items()
        )
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
            },
            "used_fact_values": {
                "type": "array",
                "items": {"type": "string"},
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


def _mandatory_answer_checklist(spec: Mapping[str, Any]) -> list[str]:
    """질문별 필수 구조 사실을 teacher가 바로 대조할 수 있게 표시한다."""

    facts = dict(
        zip(
            spec["allowed_fact_paths"],
            spec["allowed_fact_values"],
            strict=True,
        )
    )

    def fact(path: str) -> str:
        value = facts.get(path)
        if not isinstance(value, str):
            raise Mix2KV4TeacherError(f"필수 checklist fact가 없습니다: {path}")
        return value

    def pillar_ganzhi() -> list[str]:
        labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
        return [
            f"{labels[pillar]}={fact(f'chart.hard_facts.pillars.{pillar}.ganzhi')}"
            for pillar in ("year", "month", "day", "hour")
        ]

    def period_ganzhi() -> list[str]:
        return [
            "선택 날짜의 연간지=" + fact("period.hard_facts.period.year_ganzhi"),
            "선택 날짜의 월간지=" + fact("period.hard_facts.period.month_ganzhi"),
            "선택 날짜의 일진=" + fact("period.hard_facts.period.day_ganzhi"),
        ]

    question = str(spec["prompt"][-1]["content"])
    checklist = [
        "질문과 production system이 요구한 항목만 답하고, 아래 literal 값과 위치 label을 answer에서 모두 명시하세요.",
        "RAW에 있더라도 질문하지 않은 기간·관계·대운·신강약·용신은 덧붙이지 마세요.",
    ]
    if spec["task_axis"] == "chart_facts_natural_explanation":
        checklist.append(
            "이 질문은 원국 설명만 요구합니다. 선택 날짜와 period의 날짜·간지는 answer에 추가하지 마세요."
        )
        if "표면 구성" in question or "오행 분포" in question:
            checklist.append(
                "질문이 요구한 표면 오행 개수: "
                + ", ".join(
                    f"{element}={fact(f'chart.hard_facts.surface_five_elements.{element}')}"
                    for element in "목화토금수"
                )
            )
    elif spec["task_axis"] in {
        "chart_day_today_flow",
        "followup_explain_grounding",
    }:
        checklist.append(
            "이 질문은 선택 날짜를 다룹니다. 날짜 사실과 원국 사실을 각각 최소 하나 명시하세요."
        )
    if spec["task_axis"] != "structured_fact_schema_literacy":
        return checklist
    if "원국 전체 네 기둥과 일주" in question or "연주·월주·일주·시주" in question:
        checklist.extend(pillar_ganzhi())
        checklist.append(
            "원국 전체는 위 네 기둥 전부이며, 일주는 그중 하나라고 구분하세요."
        )
    elif "일간과 그 오행·음양" in question:
        checklist.extend(
            [
                "일간=" + fact("chart.hard_facts.day_master.stem"),
                "일간 오행=" + fact("chart.hard_facts.day_master.element"),
                "일간 음양=" + fact("chart.hard_facts.day_master.yin_yang"),
            ]
        )
    elif "선택 날짜의 연간지" in question or "year/month/day ganzhi" in question:
        checklist.extend(period_ganzhi())
        checklist.append(
            "선택 날짜 질문의 동시 근거로 원국 사실도 명시하세요: "
            "원국 일주=" + fact("chart.hard_facts.pillars.day.ganzhi")
        )
    elif "각 기둥의 천간·지지" in question:
        labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
        for pillar in ("year", "month", "day", "hour"):
            prefix = f"chart.hard_facts.pillars.{pillar}"
            checklist.append(
                f"{labels[pillar]}: 천간={fact(f'{prefix}.stem')}, "
                f"천간 오행={fact(f'{prefix}.stem_element')}, "
                f"천간 음양={fact(f'{prefix}.stem_yin_yang')}, "
                f"지지={fact(f'{prefix}.branch')}, "
                f"지지 오행={fact(f'{prefix}.branch_element')}, "
                f"지지 음양={fact(f'{prefix}.branch_yin_yang')}"
            )
    elif "일주의 천간·지지·지장간" in question:
        prefix = "chart.hard_facts.pillars.day"
        hidden = [
            value
            for path, value in facts.items()
            if path.startswith(f"{prefix}.hidden_stems[")
        ]
        checklist.append(
            f"일주 천간={fact(f'{prefix}.stem')}, "
            f"일주 지지={fact(f'{prefix}.branch')}, "
            f"일주 지장간={','.join(hidden)}"
        )
    elif "각 기둥의 stem ten-god" in question:
        labels = {"year": "연주", "month": "월주", "day": "일주", "hour": "시주"}
        for pillar in ("year", "month", "day", "hour"):
            prefix = f"chart.hard_facts.pillars.{pillar}"
            checklist.append(
                f"{labels[pillar]}: stem ten-god={fact(f'{prefix}.stem_ten_god')}, "
                f"branch ten-god={fact(f'{prefix}.branch_ten_god')}"
            )
        checklist.append(
            "특히 일주 stem ten-god는 runtime literal '일간'을 다른 명칭으로 바꾸지 마세요."
        )
    elif "표면 오행 개수를 누락 없이" in question:
        checklist.append(
            "표면 오행 개수: "
            + ", ".join(
                f"{element}={fact(f'chart.hard_facts.surface_five_elements.{element}')}"
                for element in "목화토금수"
            )
        )
    elif "원국 네 기둥과 선택 날짜 세 간지" in question:
        checklist.extend(pillar_ganzhi())
        checklist.extend(period_ganzhi())
        checklist.append(
            "원국 네 기둥과 선택 날짜의 세 label을 서로 다른 자료로 설명하세요."
        )
    return checklist


def _normalize_draft_answer_layout(
    spec: Mapping[str, Any], draft: Mapping[str, Any]
) -> tuple[dict[str, Any], bool]:
    """충분한 완결 문장이 한 줄에 몰린 경우에만 공백을 줄바꿈으로 바꾼다."""

    normalized = dict(draft)
    answer = normalized.get("answer")
    contract = spec.get("response_contract")
    if not isinstance(answer, str) or not isinstance(contract, Mapping):
        return normalized, False
    minimum_lines = contract.get("minimum_nonempty_lines")
    minimum_sentences = contract.get("minimum_sentences")
    if (
        not isinstance(minimum_lines, int)
        or not isinstance(minimum_sentences, int)
        or minimum_lines <= 1
        or len(nonempty_lines(answer)) >= minimum_lines
        or sentence_count(answer) < minimum_sentences
        or UNSAFE_LAYOUT_MARKUP.search(answer)
    ):
        return normalized, False

    candidate = ANSWER_HORIZONTAL_SENTENCE_BOUNDARY.sub(r"\1\n", answer)
    candidate_lines = nonempty_lines(candidate)
    if len(candidate_lines) < minimum_lines or any(
        len(MEANINGFUL_LAYOUT_CHARACTER.findall(line)) < 2
        for line in candidate_lines
    ):
        return normalized, False
    normalized["answer"] = candidate
    return normalized, candidate != answer


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
            "answer 문자열에는 완결 문장 사이 실제 줄바꿈(구조화 출력에서는 \\n)을 넣으세요. "
            "답을 짧게 끝내려고 하지 말고 질문에 필요한 만큼 자연스럽게 풀어 쓰세요. "
            "사용자 답변에 JSON·runtime·allowlist·Gold 같은 내부 용어를 노출하지 마세요. "
            "used_fact_paths·used_fact_values에는 answer에 명시한 날짜·간지·십신의 "
            "정확한 ALLOWED 값을 누락 없이 기록하세요. period의 year_ganzhi·month_ganzhi·"
            "day_ganzhi는 반드시 선택 날짜의 연간지·월간지·일진이라고 부르고, 이를 "
            "연주·월주·일주 또는 날짜의 원국이라고 부르지 마세요. "
            "해당 record의 MANDATORY ANSWER CHECKLIST가 동시 사용을 요구할 때는 "
            "날짜 사실과 원국 사실을 각각 최소 하나 answer에 명시하세요. "
            "FORBIDDEN 목록은 금지 기준이지 답변에 되풀이할 문구가 아닙니다. "
            "사용자가 한계나 근거를 묻지 "
            "않았다면 answer에 금지 항목을 기계적으로 나열하지 말고 질문한 내용만 답하세요. "
            "간지 literal 뒤에 독음과 맞지 않는 조사를 붙여 `甲寅로`, `己丑는`처럼 "
            "쓰지 마세요. `甲寅으로`, `己丑은` 또는 `연주는 甲寅입니다`, "
            "`일주는 己丑입니다`처럼 자연스럽게 작성하세요. "
            "limitations는 내부 audit metadata이므로 실제로 필요한 한계만 적고, 없으면 빈 "
            "배열로 두세요. 도구를 사용하지 마세요."
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
                + json.dumps(
                    raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
                "[ALLOWED EVIDENCE]\n"
                + json.dumps(
                    _evidence_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "[MANDATORY ANSWER CHECKLIST]\n- "
                + "\n- ".join(_mandatory_answer_checklist(spec)),
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
            "이해할 수 있는 자연스러운 한국어, 최소 3줄·최소 3문장 계약, 의미 없는 입력 재진술 "
            "여부를 모두 보세요. 하나라도 문제가 있으면 FAIL이며 구체적 rewrite_instructions를 "
            "적습니다. PASS일 때 failure_codes, fact_errors, rewrite_instructions는 비우세요. "
            "3줄·3문장은 최소 조건이며 최대 길이 제한이 아닙니다. 4줄 이상이라는 이유만으로 "
            "FAIL하거나 정확히 3줄로 줄이라고 요구하지 마세요. "
            "used_fact_paths·used_fact_values·limitations는 사용자에게 보이는 answer가 아니라 "
            "audit metadata입니다. 정확한 비어 있지 않은 limitations 자체를 범위 이탈이나 "
            "재진술로 판정하지 말고, answer의 내용과 metadata의 정확성을 구분해서 보세요. "
            "period의 year_ganzhi·month_ganzhi·day_ganzhi는 선택 날짜의 연간지·월간지·"
            "일진이어야 하며 연주·월주·일주 또는 날짜의 원국이라고 부르면 FAIL하세요. "
            "해당 record의 MANDATORY ANSWER CHECKLIST가 동시 사용을 요구하는데 날짜 "
            "사실과 원국 사실을 각각 최소 하나 명시하지 않으면 FAIL하세요. "
            "`甲寅로`, `己丑는`처럼 한자 간지의 독음에 맞지 않는 조사를 붙인 문장도 "
            "자연성 오류로 FAIL하고 `甲寅으로`, `己丑은`처럼 고치게 하세요. "
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
                + json.dumps(
                    raw, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
                "[ALLOWED EVIDENCE]\n"
                + json.dumps(
                    _evidence_payload(spec),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                "[MANDATORY ANSWER CHECKLIST]\n- "
                + "\n- ".join(_mandatory_answer_checklist(spec)),
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
    model: str | None = None,
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
                "--strict-config",
                *(
                    argument
                    for feature in CODEX_DISABLED_FEATURES
                    for argument in ("--disable", feature)
                ),
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--cd",
                str(working),
                "--color",
                "never",
                *(["--model", model] if model is not None else []),
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
            raise Mix2KV4TeacherError(
                f"{provider} subscription call이 중단됐습니다."
            ) from exc
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
                    raise Mix2KV4TeacherError(
                        "Codex structured output 파일이 없습니다."
                    )
                if output_path.stat().st_size > MAX_PROVIDER_OUTPUT_BYTES:
                    raise Mix2KV4TeacherError("Codex output이 용량 상한을 넘었습니다.")
                structured = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            if isinstance(exc, Mix2KV4TeacherError):
                raise
            raise Mix2KV4TeacherError(
                f"{provider} structured output을 읽지 못했습니다."
            ) from exc
        if not isinstance(structured, dict):
            raise Mix2KV4TeacherError(
                f"{provider} structured output이 object가 아닙니다."
            )
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


def _seed_draft_provider(record: Mapping[str, Any]) -> str | None:
    """현재 초안을 실제로 만든 provider를 과거 attempt에서 확인한다."""

    current = record.get("current_draft")
    attempts = record.get("draft_attempts")
    if not isinstance(current, Mapping) or not isinstance(attempts, list):
        return None
    for attempt in reversed(attempts):
        if (
            isinstance(attempt, Mapping)
            and attempt.get("deterministic_pass") is True
            and attempt.get("draft") == current
            and attempt.get("provider") in PROVIDER_NAMES
        ):
            return str(attempt["provider"])
    return None


def _import_seed_drafts(
    *,
    state: dict[str, Any],
    seed_state: Mapping[str, Any],
    seed_state_sha256: str,
    specs_by_id: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """이전 불변 target의 초안을 새 계약으로 재검증해 review 대기로 이관한다."""

    existing = state.get("seed_import")
    if existing is not None:
        if (
            not isinstance(existing, Mapping)
            or existing.get("version") != SEED_IMPORT_VERSION
            or existing.get("source_state_sha256") != seed_state_sha256
        ):
            raise Mix2KV4TeacherError("teacher seed import identity가 다릅니다.")
        return dict(existing)
    if state.get("provider_calls") != 0 or any(
        record.get("status") != "needs_draft"
        or record.get("draft_attempts")
        or record.get("current_draft") is not None
        for record in state["records"].values()
    ):
        raise Mix2KV4TeacherError("비어 있지 않은 target에는 seed 초안을 넣을 수 없습니다.")
    if (
        seed_state.get("dataset_version") != DATASET_VERSION
        or seed_state.get("mode") != state.get("mode")
        or seed_state.get("selection_order") != state.get("selection_order")
        or not isinstance(seed_state.get("records"), Mapping)
    ):
        raise Mix2KV4TeacherError("teacher seed selection identity가 다릅니다.")

    imported = 0
    eligible = 0
    rejected = Counter()
    seed_records = seed_state["records"]
    for record_id in state["selection_order"]:
        source = seed_records.get(record_id)
        if not isinstance(source, Mapping) or source.get("status") not in {
            "needs_review",
            "accepted",
        }:
            continue
        draft = source.get("current_draft")
        if not isinstance(draft, Mapping):
            rejected["missing_current_draft"] += 1
            continue
        eligible += 1
        provider = _seed_draft_provider(source)
        if provider != specs_by_id[record_id]["drafter"]:
            rejected["draft_provider_mismatch"] += 1
            continue
        candidate = deepcopy(dict(draft))
        try:
            validate_draft(specs_by_id[record_id], candidate)
        except Mix2KV4ContractError as exc:
            message = str(exc)
            prefix = "teacher 구조 사실 claim 오류: "
            reasons = (
                message.removeprefix(prefix).split(",")
                if message.startswith(prefix)
                else [message.split(":", 1)[0]]
            )
            for reason in set(reasons):
                rejected[reason] += 1
            continue
        target_record = state["records"][record_id]
        target_record["draft_attempts"].append(
            {
                "provider": provider,
                "attempt": 1,
                "provider_draft": deepcopy(candidate),
                "draft": candidate,
                "layout_normalized": False,
                "layout_normalizer_version": None,
                "deterministic_pass": True,
                "deterministic_error": None,
                "imported_from_seed": True,
                "source_rewrites_used": source.get("rewrites_used"),
            }
        )
        target_record["current_draft"] = candidate
        target_record["status"] = "needs_review"
        target_record["feedback"] = ""
        imported += 1

    report = {
        "version": SEED_IMPORT_VERSION,
        "source_state_sha256": seed_state_sha256,
        "source_state_schema_version": seed_state.get("schema_version"),
        "eligible_current_drafts": eligible,
        "imported_current_drafts": imported,
        "rejected_current_drafts": eligible - imported,
        "rejection_counts": dict(sorted(rejected.items())),
        "peer_review_reused": False,
    }
    state["seed_import"] = report
    return report


def _load_seed_state(
    *, seed_target: Path, output_root: Path, current_target: Path
) -> tuple[dict[str, Any], str]:
    """동일 private output root의 과거 target state만 seed로 연다."""

    _reject_symlink_components(seed_target, "teacher seed target")
    if (
        not seed_target.is_absolute()
        or seed_target.is_symlink()
        or not seed_target.is_dir()
        or seed_target.parent != output_root
        or seed_target == current_target
    ):
        raise Mix2KV4TeacherError("teacher seed target 경로가 안전하지 않습니다.")
    state_path = _state_path(seed_target)
    return (
        _load_json(state_path, "teacher seed pipeline state"),
        sha256_file(state_path),
    )


def _status_counts(state: Mapping[str, Any]) -> dict[str, int]:
    return dict(
        sorted(Counter(row["status"] for row in state["records"].values()).items())
    )


def _ordered_pending(
    state: Mapping[str, Any], status: str, specs_by_id: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    return [
        record_id
        for record_id in state["selection_order"]
        if state["records"][record_id]["status"] == status and record_id in specs_by_id
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
        record_id: state["records"][record_id]["feedback"] for record_id in record_ids
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
        provider_draft = dict(draft)
        if all(
            isinstance(draft.get(field), list)
            and all(isinstance(value, str) for value in draft[field])
            for field in ("used_fact_paths", "used_fact_values")
        ):
            draft = dict(draft)
            for field in ("used_fact_paths", "used_fact_values"):
                draft[field] = list(dict.fromkeys(draft[field]))
        draft, layout_normalized = _normalize_draft_answer_layout(
            specs_by_id[record_id], draft
        )
        attempt = {
            "provider": provider,
            "attempt": len(record["draft_attempts"]) + 1,
            "provider_draft": provider_draft,
            "draft": draft,
            "layout_normalized": layout_normalized,
            "layout_normalizer_version": (
                LAYOUT_NORMALIZER_VERSION if layout_normalized else None
            ),
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
        "layout_normalized_rows": sum(
            bool(record["draft_attempts"][-1].get("layout_normalized"))
            for record in state["records"].values()
        ),
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
    provider_only: str | None = None,
    seed_target: Path | None = None,
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
    _ensure_private_directory(target, "teacher pipeline target")
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
        seed_import = state.get("seed_import")
        if seed_target is not None:
            seed_state, seed_state_sha256 = _load_seed_state(
                seed_target=seed_target,
                output_root=output_root,
                current_target=target,
            )
            seed_import = _import_seed_drafts(
                state=state,
                seed_state=seed_state,
                seed_state_sha256=seed_state_sha256,
                specs_by_id=specs_by_id,
            )
            _atomic_write(_state_path(target), _json_bytes(state))
            print(
                "seed_import="
                + json.dumps(
                    seed_import,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                flush=True,
            )
        maximum_rewrites = int(config["teacher"]["maximum_rewrite_rounds"])
        calls_this_run = 0
        while True:
            counts = _status_counts(state)
            if counts.get("failed", 0):
                break
            all_review_ids = _ordered_pending(state, "needs_review", specs_by_id)
            all_draft_ids = _ordered_pending(state, "needs_draft", specs_by_id)
            review_ids = all_review_ids
            draft_ids = all_draft_ids
            if provider_only is not None:
                review_ids = [
                    record_id
                    for record_id in review_ids
                    if specs_by_id[record_id]["reviewer"] == provider_only
                ]
                draft_ids = [
                    record_id
                    for record_id in draft_ids
                    if specs_by_id[record_id]["drafter"] == provider_only
                ]
            if not review_ids and not draft_ids:
                if provider_only is not None and (
                    all_review_ids or all_draft_ids
                ):
                    break
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
            "provider_only": provider_only,
            "seed_import": seed_import,
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
    parser.add_argument(
        "--provider-only",
        choices=tuple(sorted(PROVIDER_NAMES)),
        help=(
            "지정 provider가 담당하는 pending draft/review만 처리합니다. "
            "다른 provider의 교차 PASS 요건은 유지됩니다."
        ),
    )
    parser.add_argument(
        "--seed-target",
        type=Path,
        help=(
            "동일 private output root의 과거 teacher target에서 현재 초안만 "
            "새 spec으로 재검증해 이관합니다. 과거 peer PASS는 재사용하지 않습니다."
        ),
    )
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
            provider_only=args.provider_only,
            seed_target=(
                _absolute(args.seed_target) if args.seed_target is not None else None
            ),
        )
    except (Mix2KV4TeacherError, Mix2KV4ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
