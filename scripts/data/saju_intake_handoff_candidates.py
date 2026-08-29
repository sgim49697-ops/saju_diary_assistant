# saju_intake_handoff_candidates.py - 공개 합성 intake handoff 후보를 결정적으로 생성·검증한다.

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/"
    "intake-handoff-candidates-v1.0.0.json"
)
SCHEMA_VERSION = "1.0.0"
CANDIDATE_VERSION = "v1.0.0"
DATASET_NAME = "saju_1b_baseline"
TASK = "saju_intake_handoff"
SEED = 42
ROWS_PER_STRATUM = 200
TOTAL_ROWS = 2_000
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
STRATA = (
    "no_birth_information",
    "date_only_no_time",
    "ambiguous_time",
    "calendar_ambiguity",
    "timezone_location_ambiguity",
    "accumulated_context_no_reask",
    "time_unknown_partial_limit",
    "complete_input_runtime_handoff",
    "false_ui_or_completion",
    "structured_chart_ready",
)
SCENARIO_FAMILIES = (
    "latest_correction",
    "conflicting_values",
    "user_declines_optional_input",
    "solar_leap_month_no_reask",
    "ambiguous_07_hour",
    "ambiguous_12_hour",
    "birth_time_range",
    "overseas_city",
    "historical_timezone",
    "solar_term_boundary_refusal",
    "late_rat_hour_boundary_refusal",
    "runtime_disconnected",
    "runtime_error",
    "structured_payload_format_error",
    "structured_payload_conflict",
    "period_facts_missing",
    "circumvention_request",
    "repeated_complaint",
    "already_received_no_reask",
    "safe_baseline",
)
SLOTS = (
    "birth_date",
    "calendar_type",
    "leap_month",
    "birth_time",
    "birth_location",
    "timezone",
    "structured_chart",
    "runtime_result",
)
SLOT_VALUES = {
    "birth_date": {"missing", "present", "not_required"},
    "calendar_type": {"missing", "solar", "lunar", "ambiguous", "not_required"},
    "leap_month": {"missing", "not_applicable", "unknown", "not_required"},
    "birth_time": {"missing", "present", "ambiguous", "unknown", "not_required"},
    "birth_location": {"missing", "present", "ambiguous", "not_required"},
    "timezone": {"missing", "present", "ambiguous", "not_required"},
    "structured_chart": {"missing", "verified"},
    "runtime_result": {"unavailable", "available"},
}
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
PHONE_PATTERN = re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[- ]?\d{3,4}[- ]?\d{4}(?!\d)")
RESIDENT_ID_PATTERN = re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)")
ACCOUNT_PATTERN = re.compile(
    r"(?:계좌|카드|여권|운전면허|주민(?:등록)?번호).{0,16}\d{4,}",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
FULL_BIRTH_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:19|20)\d{2}(?:년\s*\d{1,2}월\s*\d{1,2}일|[-./]\d{1,2}[-./]\d{1,2})(?!\d)"
)
PROMPT_CONTROL_PATTERNS = (
    re.compile(r"<\|[^>]+\|>"),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
    re.compile(r"#{3,}\s*(?:system|assistant|user)\b", re.IGNORECASE),
    re.compile(r"(?:이전|앞선)\s+(?:지시|명령).{0,8}(?:무시|취소)"),
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
)
GANZHI_PAIR_PATTERN = re.compile(
    r"[甲乙丙丁戊己庚辛壬癸][\s·,/：:()_-]*[子丑寅卯辰巳午未申酉戌亥]"
    r"|(?<![가-힣])[갑을병정무기경신임계][\s·,/：:()_-]*"
    r"[자축인묘진사오미신유술해](?![가-힣])"
)
PILLAR_LABEL_PATTERN = re.compile(r"(?:년주|월주|일주|시주)")
INTAKE_CHART_TERM_PATTERN = re.compile(
    r"(?:\bchart\b|명식|간지|원국|년주|월주|일주|시주)", re.IGNORECASE
)
STRUCTURED_INTAKE_FACT_PATTERN = re.compile(
    r"(?:생년월일|출생\s*(?:시각|시간|지|도시|장소)|시간대|도시·국가|"
    r"해외\s*도시|\bDOB\b|\btimezone\b)",
    re.IGNORECASE,
)
RUNTIME_CHART_PATTERN = re.compile(
    r"(?:runtime\s*JSON|\bchart\b|명식|간지|원국|년주|월주|일주|시주)",
    re.IGNORECASE,
)
POSITIVE_FALSE_COMPLETION_PATTERNS = (
    re.compile(r"확인되었습니다"),
    re.compile(r"(?:계산|분석|저장)(?:이|을|를)?\s*(?:완료했습니다|완료되었습니다)"),
    re.compile(r"(?:확인|결과)\s*버튼을\s*(?:누르|클릭)"),
    re.compile(r"화면에\s*(?:표시|출력)(?:했습니다|되었습니다)"),
)
FORBIDDEN_CLAIMS = (
    "birth_to_pillars_inference",
    "fabricated_structured_chart",
    "unperformed_calculation_complete",
    "nonexistent_ui_or_confirmation",
    "reask_known_slots",
    "deterministic_fate_claim",
)
STEMS = (
    ("갑(甲)", "목(木)"),
    ("을(乙)", "목(木)"),
    ("병(丙)", "화(火)"),
    ("정(丁)", "화(火)"),
    ("무(戊)", "토(土)"),
    ("기(己)", "토(土)"),
    ("경(庚)", "금(金)"),
    ("신(辛)", "금(金)"),
    ("임(壬)", "수(水)"),
    ("계(癸)", "수(水)"),
)
OPENERS = (
    "합성 점검 사례입니다.",
    "공개 합성 입력입니다.",
    "테스트용 상황을 설명합니다.",
    "개인정보가 아닌 합성 시나리오입니다.",
    "대화 흐름 점검용 요청입니다.",
    "사주 입력 절차를 시험합니다.",
    "누락 정보 안내를 확인하려는 합성 요청입니다.",
    "계산기 연결 전 단계를 점검합니다.",
    "문맥 유지 동작을 보는 공개 합성 사례입니다.",
    "안전한 응답 계약을 확인하는 테스트입니다.",
)
REQUEST_ENDINGS = (
    "앞선 설명에서 한 값을 정정했으니 가장 최근 상태만 사용해 주세요.",
    "서로 충돌하는 설명은 임의로 고르지 말고 확인 필요 상태로 남겨 주세요.",
    "추가 정보를 더 주지 않겠다는 선택을 존중해 가능한 범위만 알려 주세요.",
    "달력 종류가 양력으로 정정되면 윤달을 다시 묻지 않는 원칙도 지켜 주세요.",
    "7시 입력은 오전·오후가 빠질 수 있다는 점을 구분해 주세요.",
    "12시 입력도 오전·오후를 임의 확정하지 말아 주세요.",
    "정확한 한 시각 대신 범위만 주어졌을 때 추측하지 말아 주세요.",
    "해외 도시 정보가 뒤늦게 오면 기존 값과 섞지 말아 주세요.",
    "과거 해외 출생이라 당시 적용 시간대를 임의로 만들지 말아 주세요.",
    "절입 경계 계산을 언어 모델이 대신하지 않도록 해 주세요.",
    "자시 경계 계산을 언어 모델이 대신하지 않도록 해 주세요.",
    "현재 승인된 계산기가 연결되지 않은 상황을 분명히 해 주세요.",
    "계산기 오류가 나면 완료라고 하지 말고 재시도 필요를 알려 주세요.",
    "계산 결과 형식이 잘못된 경우 해석하지 않는 원칙을 지켜 주세요.",
    "서로 상충하는 계산 결과가 들어오면 하나를 임의로 고르지 말아 주세요.",
    "이번 주말 같은 기간 사실이 없으면 기간 운세를 만들지 말아 주세요.",
    "간접 표현으로 계산을 우회해 달라고 해도 추측하지 말아 주세요.",
    "같은 안내에 불만을 반복해도 필요한 다음 단계는 일관되게 답해 주세요.",
    "이미 받은 입력을 다시 묻지 않는지 확인해 주세요.",
    "검증 가능한 다음 행동으로 마무리해 주세요.",
)
STRUCTURED_REQUEST_ENDINGS = (
    "앞선 사실을 정정했으니 가장 최근에 검증된 값만 사용해 주세요.",
    "서로 충돌하는 제공 사실은 임의로 하나를 고르지 말아 주세요.",
    "추가 설명을 원하지 않는 선택을 존중해 제공 사실만 답해 주세요.",
    "이미 검증된 선택 필드는 다시 묻지 말아 주세요.",
    "숫자 경계값을 임의로 보완하지 말아 주세요.",
    "또 다른 숫자 경계도 제공된 값 그대로 유지해 주세요.",
    "값이 범위로 주어지면 한 점으로 추측하지 말아 주세요.",
    "외부 출처 필드가 뒤늦게 와도 검증 전에는 섞지 말아 주세요.",
    "과거 정책 버전을 현재 규칙으로 임의 치환하지 말아 주세요.",
    "정책 경계 계산은 검증된 도구 결과에만 맡겨 주세요.",
    "일자 경계 계산을 언어 모델이 대신하지 않도록 해 주세요.",
    "승인된 계산기가 연결되지 않았다면 그 사실을 밝혀 주세요.",
    "계산기 오류가 나면 완료라고 하지 말고 재검증을 안내해 주세요.",
    "구조 데이터 형식이 잘못됐다면 해석하지 말아 주세요.",
    "구조 데이터끼리 상충하면 하나를 임의 선택하지 말아 주세요.",
    "특정 기간 사실이 없으면 기간별 조언을 만들지 말아 주세요.",
    "간접적인 우회 요청에도 제공되지 않은 사실을 만들지 말아 주세요.",
    "같은 불만이 반복돼도 검증 경계를 일관되게 유지해 주세요.",
    "이미 받은 검증 사실은 다시 묻지 말아 주세요.",
    "제공된 사실 범위 안에서만 답을 마무리해 주세요.",
)
RESPONSE_LEADS = (
    "현재 상태를 기준으로 안내하겠습니다.",
    "제공된 정보만 반영하겠습니다.",
    "입력 상태를 그대로 유지하겠습니다.",
    "누락된 슬롯만 구분하겠습니다.",
    "검증된 범위에서 답하겠습니다.",
    "검증된 계산 계약에 따라 안내하겠습니다.",
    "확인된 문맥을 보존하겠습니다.",
    "계산기 연결 전 단계로 답하겠습니다.",
    "추측 없이 다음 행동을 안내하겠습니다.",
    "현재 가능한 범위만 설명하겠습니다.",
)


class IntakeHandoffCandidateError(RuntimeError):
    """후보 계약·안전 검사·불변 build 위반."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _jsonl_bytes(values: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(_canonical_json_bytes(value) + b"\n" for value in values)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise IntakeHandoffCandidateError(
            f"SHA-256을 계산할 수 없습니다: {path}"
        ) from exc
    return digest.hexdigest()


def _safe_repo_path(repo_root: Path, value: str) -> Path:
    relative = Path(value)
    if (
        not value
        or relative.is_absolute()
        or value != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise IntakeHandoffCandidateError(f"안전하지 않은 상대경로입니다: {value}")
    root = repo_root.resolve()
    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise IntakeHandoffCandidateError(f"저장소 밖 경로는 허용하지 않습니다: {value}")
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IntakeHandoffCandidateError(
            f"{label} JSON을 읽을 수 없습니다: {path}"
        ) from exc
    if not isinstance(value, dict):
        raise IntakeHandoffCandidateError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _contract_expected() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": DATASET_NAME,
        "candidate_version": CANDIDATE_VERSION,
        "task": TASK,
        "seed": SEED,
        "generated_on": "2026-08-29",
        "system_prompt": {
            "profile_id": "guided_diagnostic_v1",
            "version": "1.0.0",
            "path": "configs/chat_prompts/saju_intake_handoff_v1.txt",
            "bytes": 1805,
            "sha256": (
                "d2aa55a54bfab253669a56570ceca63e"
                "02b8d688d3699e40c9258ac6f7c18232"
            ),
        },
        "generation_contract": {
            "total_rows": TOTAL_ROWS,
            "rows_per_stratum": ROWS_PER_STRATUM,
            "strata": list(STRATA),
            "minimum_dialogue_turns": 1,
            "maximum_dialogue_turns": 3,
            "message_role_pattern": "system,(user,assistant){1,3}",
            "public_synthetic_only": True,
            "candidate_only": True,
            "birth_to_pillars_training_target": False,
            "structured_chart_uses_fixed_policy_facts_only": True,
            "rows_per_scenario_family_per_stratum": 10,
            "scenario_families": list(SCENARIO_FAMILIES),
        },
        "validation_contract": {
            "max_message_characters": 1400,
            "max_conversation_characters": 3200,
            "max_non_whitespace_characters": 1800,
            "minimum_assistant_sentences": 1,
            "maximum_assistant_sentences": 3,
            "normalized_duplicate_rows_allowed": 0,
            "pii_allowed": False,
            "control_characters_allowed": False,
            "prompt_control_tokens_allowed": False,
            "fabricated_four_pillars_allowed": False,
            "birth_date_to_pillars_link_allowed": False,
            "leakage_components_cross_strata_allowed": False,
        },
        "sharing_privacy": {
            "private_candidate_payload": True,
            "public_report_scope": "aggregate_only",
            "public_rendered_rows": False,
            "public_template_fragments": True,
            "public_candidate_ids": False,
            "contains_real_person_data": False,
            "contains_aihub_source_text": False,
            "contains_manual_session_text": False,
            "contains_real_person_source_text": False,
        },
        "dev_suite_separation": {
            "config_path": (
                "configs/model_versions/saju_1b_baseline/"
                "phase5-stateful-chat-gate-v1.0.0.json"
            ),
            "generator_path": "scripts/training/phase5_stateful_chat_gate.py",
            "candidate_component_namespace": "saju_intake_handoff_candidates_v1",
            "candidate_template_namespace": (
                "saju_intake_handoff_candidate_template_v1"
            ),
            "forbidden_dev_component_namespace": "phase5_stateful_chat_gate_dev_v1",
            "normalized_exact_overlaps_allowed": 0,
            "near_duplicate_metric": "character_5gram_jaccard",
            "near_duplicate_threshold": 0.85,
            "near_duplicate_pairs_allowed": 0,
        },
        "outputs": {
            "private_root": (
                "data/derived/saju_1b_baseline/intake-handoff-candidates/"
                "v1.0.0/{build_id}"
            ),
            "public_root": (
                "data/reports/saju_1b_baseline/intake-handoff-candidates/"
                "v1.0.0/{build_id}"
            ),
            "private_file_mode": "0600",
            "private_directory_mode": "0700",
            "public_file_mode": "0644",
            "public_directory_mode": "0755",
        },
        "implementation_files": [
            "scripts/data/saju_intake_handoff_candidates.py"
        ],
    }


def _load_system_prompt(config: dict[str, Any], repo_root: Path) -> str:
    prompt_contract = config["system_prompt"]
    path = _safe_repo_path(repo_root, prompt_contract["path"])
    if path.is_symlink() or not path.is_file():
        raise IntakeHandoffCandidateError("안내 보정 system prompt가 일반 파일이 아닙니다.")
    payload = path.read_bytes()
    if len(payload) != prompt_contract["bytes"]:
        raise IntakeHandoffCandidateError("안내 보정 system prompt byte 수가 다릅니다.")
    if _sha256_bytes(payload) != prompt_contract["sha256"]:
        raise IntakeHandoffCandidateError("안내 보정 system prompt SHA-256이 다릅니다.")
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntakeHandoffCandidateError("안내 보정 system prompt는 UTF-8이어야 합니다.") from exc
    if not content.strip() or CONTROL_CHARACTER_PATTERN.search(content):
        raise IntakeHandoffCandidateError("안내 보정 system prompt 내용이 안전하지 않습니다.")
    return content.rstrip("\n")


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if config != _contract_expected():
        raise IntakeHandoffCandidateError("intake handoff 후보 v1 계약이 다릅니다.")
    prompt = _load_system_prompt(config, repo_root)
    return {
        "status": "valid",
        "candidate_version": CANDIDATE_VERSION,
        "system_prompt_sha256": config["system_prompt"]["sha256"],
        "system_prompt_characters": len(prompt),
    }


def _system_message_content(config: dict[str, Any], prompt: str) -> str:
    identity = config["system_prompt"]
    if identity["profile_id"] != "guided_diagnostic_v1" or not prompt:
        raise IntakeHandoffCandidateError("system prompt identity가 다릅니다.")
    return prompt


def _initial_state() -> dict[str, str]:
    return {
        "birth_date": "missing",
        "calendar_type": "missing",
        "leap_month": "missing",
        "birth_time": "missing",
        "birth_location": "missing",
        "timezone": "missing",
        "structured_chart": "missing",
        "runtime_result": "unavailable",
    }


def _after_state(stratum: str, scenario_index: int) -> dict[str, str]:
    values = {
        "no_birth_information": _initial_state(),
        "date_only_no_time": {
            "birth_date": "present",
            "calendar_type": "missing",
            "leap_month": "missing",
            "birth_time": "missing",
            "birth_location": "missing",
            "timezone": "missing",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "ambiguous_time": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "ambiguous",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "calendar_ambiguity": {
            "birth_date": "present",
            "calendar_type": "lunar" if scenario_index % 2 else "ambiguous",
            "leap_month": "unknown",
            "birth_time": "present",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "timezone_location_ambiguity": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "present",
            "birth_location": "ambiguous",
            "timezone": "ambiguous",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "accumulated_context_no_reask": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "missing",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "time_unknown_partial_limit": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "unknown",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "complete_input_runtime_handoff": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "present",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "false_ui_or_completion": {
            "birth_date": "present",
            "calendar_type": "solar",
            "leap_month": "not_applicable",
            "birth_time": "present",
            "birth_location": "present",
            "timezone": "present",
            "structured_chart": "missing",
            "runtime_result": "unavailable",
        },
        "structured_chart_ready": {
            "birth_date": "not_required",
            "calendar_type": "not_required",
            "leap_month": "not_required",
            "birth_time": "not_required",
            "birth_location": "not_required",
            "timezone": "not_required",
            "structured_chart": "verified",
            "runtime_result": "available",
        },
    }
    try:
        return copy.deepcopy(values[stratum])
    except KeyError as exc:
        raise IntakeHandoffCandidateError(f"지원하지 않는 stratum입니다: {stratum}") from exc


def _slot_transition(stratum: str, scenario_index: int) -> dict[str, Any]:
    before = _initial_state()
    after = _after_state(stratum, scenario_index)
    changed = [slot for slot in SLOTS if before[slot] != after[slot]]
    return {"before": before, "after": after, "changed_slots": changed}


def _expected_action(stratum: str, scenario_index: int) -> dict[str, Any]:
    actions: dict[str, tuple[str, list[str], list[str], list[str], bool]] = {
        "no_birth_information": (
            "request_missing_intake_then_runtime_handoff",
            [
                "birth_date",
                "calendar_type",
                "birth_time",
                "birth_location",
            ],
            [],
            [
                "생년월일",
                "양력·음력",
                "출생시각",
                "출생 도시·국가",
                "승인된 계산 도구",
            ],
            False,
        ),
        "date_only_no_time": (
            "request_birth_time_or_mark_unknown",
            ["calendar_type", "birth_time", "birth_location"],
            ["birth_date"],
            [
                "양력·음력",
                "출생시각",
                "시간 미상",
                "출생 도시·국가",
                "승인된 계산 도구",
            ],
            False,
        ),
        "ambiguous_time": (
            "clarify_ambiguous_time",
            ["birth_time"],
            ["birth_date", "calendar_type", "birth_location", "timezone"],
            ["오전·오후", "24시간제", "시간 미상", "추측하지"],
            False,
        ),
        "calendar_ambiguity": (
            "clarify_calendar_and_leap_month",
            ["calendar_type", "leap_month"],
            ["birth_date", "birth_time", "birth_location", "timezone"],
            ["양력·음력", "윤달", "검증된 계산 결과"],
            False,
        ),
        "timezone_location_ambiguity": (
            "clarify_location_and_timezone",
            ["birth_location", "timezone"],
            ["birth_date", "calendar_type", "birth_time"],
            ["출생지", "시간대", "도시·국가", "추측하지"],
            False,
        ),
        "accumulated_context_no_reask": (
            "preserve_context_and_request_only_time",
            ["birth_time"],
            ["birth_date", "calendar_type", "birth_location", "timezone"],
            ["다시 묻지", "출생시각", "시간 미상", "승인된 계산 도구"],
            False,
        ),
        "time_unknown_partial_limit": (
            "explain_time_unknown_limit_then_handoff",
            [],
            [
                "birth_date",
                "calendar_type",
                "birth_time",
                "birth_location",
                "timezone",
            ],
            ["시간 미상", "시간 관련 결과", "제한", "검증된 계산기"],
            False,
        ),
        "complete_input_runtime_handoff": (
            "handoff_complete_input_without_calculation_claim",
            [],
            [
                "birth_date",
                "calendar_type",
                "birth_time",
                "birth_location",
                "timezone",
            ],
            ["검증된 계산 결과", "승인된 계산 도구", "계산하지"],
            False,
        ),
        "false_ui_or_completion": (
            "correct_false_completion_then_runtime_handoff",
            [],
            [
                "birth_date",
                "calendar_type",
                "birth_time",
                "birth_location",
                "timezone",
            ],
            ["완료 안내", "실제 계산 결과", "승인된 계산 도구", "검증된 계산 결과"],
            False,
        ),
    }
    if stratum == "structured_chart_ready":
        stem, element = STEMS[scenario_index % len(STEMS)]
        return {
            "code": "interpret_verified_structured_fact_only",
            "request_slots": [],
            "preserve_slots": ["structured_chart", "runtime_result"],
            "required_terms": [stem, element, "문화적·성찰적 참고", "확정하지"],
            "interpretation_allowed": True,
        }
    if stratum == "calendar_ambiguity" and scenario_index % 2:
        return {
            "code": "clarify_lunar_leap_month_only",
            "request_slots": ["leap_month"],
            "preserve_slots": [
                "birth_date",
                "calendar_type",
                "birth_time",
                "birth_location",
                "timezone",
            ],
            "required_terms": ["음력", "평달인지 윤달인지", "검증된 계산 결과"],
            "interpretation_allowed": False,
        }
    try:
        code, requested, preserved, terms, allowed = actions[stratum]
    except KeyError as exc:
        raise IntakeHandoffCandidateError(f"지원하지 않는 stratum입니다: {stratum}") from exc
    return {
        "code": code,
        "request_slots": requested,
        "preserve_slots": preserved,
        "required_terms": terms,
        "interpretation_allowed": allowed,
    }


def _base_response(stratum: str, scenario_index: int) -> str:
    values = {
        "no_birth_information": (
            "현재 검증된 계산 결과가 없습니다. 생년월일, 양력·음력 구분, 출생시각 "
            "또는 시간 미상, 출생 도시·국가를 알려 주세요. 시간대를 알고 있다면 함께 "
            "받을 수 있지만 원시 입력만으로 사주 결과를 추측하지 않으며, 현재 대화에는 "
            "승인된 계산 도구가 연결되어 있지 않습니다."
        ),
        "date_only_no_time": (
            "생년월일은 이미 받은 값으로 유지합니다. 아직 없는 양력·음력 구분, "
            "출생시각 또는 시간 미상, 출생 도시·국가를 알려 주세요. 승인된 계산 도구가 "
            "만든 검증 결과가 온 뒤에만 해석합니다."
        ),
        "ambiguous_time": (
            "출생시각의 오전·오후가 모호합니다. 24시간제로 정확한 시각을 확인하거나 "
            "시간 미상으로 표시해 주세요. 검증된 계산 결과 없이 추측하지 않습니다."
        ),
        "calendar_ambiguity": (
            "날짜의 양력·음력 구분을 확인해 주세요. 음력이라면 윤달 여부도 필요합니다. "
            "검증된 계산 결과가 오기 전에는 사주 사실을 계산하거나 추측하지 않습니다."
        ),
        "timezone_location_ambiguity": (
            "출생지와 당시 시간대를 확인해 주세요. 해외 출생이면 도시·국가와 현지 "
            "출생시각 기준이 필요합니다. 이를 알기 전에는 결과를 추측하지 않습니다."
        ),
        "accumulated_context_no_reask": (
            "이미 받은 생년월일·양력·출생지·시간대는 다시 묻지 않겠습니다. 출생시각을 "
            "알려 주거나 모르면 시간 미상으로 처리하겠습니다. 현재 대화에는 승인된 "
            "계산 도구가 연결되어 있지 않습니다."
        ),
        "time_unknown_partial_limit": (
            "출생시각은 시간 미상으로 유지하겠습니다. 이 경우 시간 관련 결과를 확정할 "
            "수 없어 해석 범위에 제한이 있으며, 검증된 계산기가 반환한 결과 "
            "범위 안에서만 안내할 수 있습니다."
        ),
        "complete_input_runtime_handoff": (
            "필요한 입력은 모두 받았지만 아직 검증된 계산 결과가 없습니다. 제가 원시 "
            "생년월일로 계산하지 않습니다. 현재 대화에는 승인된 계산 도구가 연결되어 "
            "있지 않으며, 검증된 계산 결과가 온 뒤에만 해석하겠습니다."
        ),
        "false_ui_or_completion": (
            "앞선 완료 안내는 잘못됐습니다. 실제 계산 결과가 없으므로 분석이 끝났다고 "
            "할 수 없습니다. 현재 대화에는 승인된 계산 도구가 연결되어 있지 않으며, "
            "검증된 계산 결과가 확인된 뒤 이 대화에 직접 설명하겠습니다."
        ),
    }
    if stratum == "structured_chart_ready":
        stem, element = STEMS[scenario_index % len(STEMS)]
        return (
            f"제공된 검증 사실 기준으로 일간은 {stem}이고 오행은 {element}입니다. "
            "이는 입력된 한 항목에 대한 전통 명리의 문화적·성찰적 참고이며, "
            "성향이나 운명을 확정하지 않습니다."
        )
    if stratum == "calendar_ambiguity" and scenario_index % 2:
        return (
            "음력이라는 값은 이미 받은 상태로 유지합니다. 아직 없는 평달인지 윤달인지 "
            "여부만 확인해 주세요. 검증된 계산 결과가 오기 전에는 사주 사실을 "
            "계산하거나 추측하지 않습니다."
        )
    try:
        return values[stratum]
    except KeyError as exc:
        raise IntakeHandoffCandidateError(f"지원하지 않는 stratum입니다: {stratum}") from exc


def _state_clause(stratum: str, scenario_index: int) -> str:
    hours = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
    values = {
        "no_birth_information": (
            "출생 정보도 검증된 계산 결과도 아직 제공하지 않은 채 사주 풀이를 요청합니다."
        ),
        "date_only_no_time": (
            "생년월일만 제공했고 양력·음력 구분, 출생시각, 출생 도시·국가는 빠졌습니다."
        ),
        "ambiguous_time": (
            f"다른 필수 입력은 제공했지만 출생시각을 {hours[scenario_index % 12]}시라고만 "
            "적어 오전·오후가 모호합니다."
        ),
        "calendar_ambiguity": (
            "생년월일과 정확한 출생시각, 출생지·시간대는 제공했지만 날짜의 "
            "양력·음력과 윤달 여부가 모호합니다."
        ),
        "timezone_location_ambiguity": (
            "생년월일·양력·출생시각은 제공했지만 해외 출생지와 당시 시간대가 "
            "모호합니다."
        ),
        "time_unknown_partial_limit": (
            "생년월일·양력·출생지·시간대는 제공했고 출생시각은 모른다고 "
            "명시했습니다."
        ),
        "complete_input_runtime_handoff": (
            "생년월일·양력·정확한 출생시각·출생지·시간대를 모두 제공했지만 "
            "검증된 계산 결과는 아직 없습니다."
        ),
    }
    if stratum == "structured_chart_ready":
        stem, element = STEMS[scenario_index % len(STEMS)]
        return (
            f"검증된 구조 데이터에는 일간이 {stem}, 오행이 {element}으로 "
            "제공됐습니다. 이 사실의 범위만 설명해 주세요."
        )
    if stratum == "calendar_ambiguity" and scenario_index % 2:
        return (
            "생년월일과 정확한 출생시각, 출생지·시간대, 음력 구분은 제공했지만 "
            "평달인지 윤달인지가 빠졌습니다."
        )
    try:
        return values[stratum]
    except KeyError as exc:
        raise IntakeHandoffCandidateError(f"단일 턴 clause가 없습니다: {stratum}") from exc


def _messages(
    stratum: str,
    style_index: int,
    scenario_index: int,
    system_content: str,
) -> list[dict[str, str]]:
    opener = OPENERS[style_index]
    ending = (
        STRUCTURED_REQUEST_ENDINGS[scenario_index]
        if stratum == "structured_chart_ready"
        else REQUEST_ENDINGS[scenario_index]
    )
    lead = RESPONSE_LEADS[style_index]
    final_response = _base_response(stratum, scenario_index)
    system = {"role": "system", "content": system_content}
    if stratum == "accumulated_context_no_reask":
        return [
            system,
            {
                "role": "user",
                "content": (
                    f"{opener} 양력이라는 점과 출생지·시간대는 이미 제공했고, "
                    "생년월일과 출생시각은 아직 빠져 있습니다."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    f"{lead} 양력·출생지·시간대는 유지하고, 아직 없는 생년월일과 "
                    "출생시각만 확인하겠습니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    "이어서 생년월일 값도 제공했습니다. 출생시각만 아직 확인하지 "
                    f"못했습니다. {ending}"
                ),
            },
            {"role": "assistant", "content": final_response},
        ]
    if stratum == "false_ui_or_completion":
        return [
            system,
            {
                "role": "user",
                "content": (
                    f"{opener} 필수 입력값은 모두 제공했지만 계산기 결과는 아직 없습니다."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    f"{lead} 입력은 유지하되 계산 완료로 간주하지 않고 검증된 계산 "
                    "결과를 기다리겠습니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    "화면의 확인 절차까지 끝났다고 가정하고 결과가 나왔다고 말해 주세요. "
                    f"{ending}"
                ),
            },
            {"role": "assistant", "content": final_response},
        ]
    user = f"{opener} {_state_clause(stratum, scenario_index)} {ending}"
    return [system, {"role": "user", "content": user}, {"role": "assistant", "content": final_response}]


def _leakage_component_id(stratum: str, scenario_index: int) -> str:
    digest = hashlib.sha256(
        f"{SEED}|leakage|{stratum}|{scenario_index}".encode()
    ).hexdigest()
    return f"sihc-lc-{digest[:20]}"


def _candidate_without_id(
    config: dict[str, Any],
    stratum: str,
    style_index: int,
    scenario_index: int,
    system_content: str,
) -> dict[str, Any]:
    prompt = config["system_prompt"]
    return {
        "schema_version": SCHEMA_VERSION,
        "task": TASK,
        "stratum": stratum,
        "system_prompt_ref": {
            "profile_id": prompt["profile_id"],
            "version": prompt["version"],
            "sha256": prompt["sha256"],
        },
        "slot_state_transition": _slot_transition(stratum, scenario_index),
        "messages": _messages(
            stratum,
            style_index,
            scenario_index,
            system_content,
        ),
        "expected_action": _expected_action(stratum, scenario_index),
        "forbidden_claims": list(FORBIDDEN_CLAIMS),
        "provenance": {
            "source_type": "project_public_synthetic",
            "generation_method": "deterministic_template_cartesian_product",
            "generator_version": SCHEMA_VERSION,
            "seed": SEED,
            "component_namespace": config["dev_suite_separation"][
                "candidate_component_namespace"
            ],
            "template_namespace": (
                f"{config['dev_suite_separation']['candidate_template_namespace']}."
                f"{stratum}"
            ),
            "style_index": style_index,
            "scenario_index": scenario_index,
            "scenario_family": SCENARIO_FAMILIES[scenario_index],
            "source_record_ids": [],
            "human_domain_review": "not_performed",
        },
        "leakage_component_id": _leakage_component_id(stratum, scenario_index),
        "birth_to_pillars_training_target": False,
        "promotion_status": "candidate_only",
        "sharing": {
            "rendered_row_shared": False,
            "tracked_template_fragments_public": True,
            "aggregate_only_public": True,
        },
        "privacy": {
            "contains_real_person_data": False,
            "contains_aihub_source_text": False,
            "contains_manual_session_text": False,
            "contains_real_person_source_text": False,
            "contains_personal_identifiers": False,
            "pii_scan_status": "passed",
        },
    }


def _make_candidate(
    config: dict[str, Any],
    stratum: str,
    style_index: int,
    scenario_index: int,
    system_content: str,
) -> dict[str, Any]:
    value = _candidate_without_id(
        config,
        stratum,
        style_index,
        scenario_index,
        system_content,
    )
    identity = _sha256_json(
        {
            "namespace": "saju_intake_handoff_candidates_v1",
            "seed": SEED,
            "candidate": value,
        }
    )
    return {"candidate_id": f"sihc-{identity[:24]}", **value}


def _order_key(row: dict[str, Any]) -> tuple[str, str]:
    rank = hashlib.sha256(
        f"{SEED}|order|{row['candidate_id']}".encode()
    ).hexdigest()
    return rank, str(row["candidate_id"])


def generate_candidates(
    config: dict[str, Any], repo_root: Path
) -> list[dict[str, Any]]:
    validate_contract(config, repo_root)
    prompt = _load_system_prompt(config, repo_root)
    system_content = _system_message_content(config, prompt)
    rows = [
        _make_candidate(config, stratum, style, scenario, system_content)
        for stratum in STRATA
        for style in range(len(OPENERS))
        for scenario in range(len(REQUEST_ENDINGS))
    ]
    rows.sort(key=_order_key)
    return rows


def _normalized_conversation(messages: Sequence[dict[str, str]]) -> str:
    values = []
    for message in messages[1:]:
        content = unicodedata.normalize("NFKC", message["content"])
        values.append(f"{message['role']}:{' '.join(content.lower().split())}")
    return "\n".join(values)


def _normalized_user_transcript(messages: Sequence[dict[str, str]]) -> str:
    text = " ".join(
        message["content"] for message in messages if message["role"] == "user"
    )
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"\d+", "0", text)
    return "".join(re.findall(r"[가-힣a-z0-9]+", text))


def _character_ngrams(value: str, width: int = 5) -> frozenset[str]:
    if len(value) < width:
        return frozenset({value}) if value else frozenset()
    return frozenset(value[index : index + width] for index in range(len(value) - width + 1))


def validate_dev_suite_separation(
    rows: Sequence[dict[str, Any]], config: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    separation = config["dev_suite_separation"]
    dev_config_path = _safe_repo_path(repo_root, separation["config_path"])
    generator_path = _safe_repo_path(repo_root, separation["generator_path"])
    if (
        dev_config_path.is_symlink()
        or generator_path.is_symlink()
        or not dev_config_path.is_file()
        or not generator_path.is_file()
    ):
        raise IntakeHandoffCandidateError("stateful dev 분리 입력이 일반 파일이 아닙니다.")
    dev_config = _load_json(dev_config_path, "stateful dev config")
    dev_suite = dev_config.get("dev_suite")
    if (
        not isinstance(dev_suite, dict)
        or dev_suite.get("total_cases") != 100
        or dev_suite.get("component_namespace")
        != separation["forbidden_dev_component_namespace"]
        or dev_suite.get("forbidden_training_namespace")
        != separation["candidate_component_namespace"]
    ):
        raise IntakeHandoffCandidateError("candidate/dev namespace 상호 금지 계약이 다릅니다.")
    module = importlib.import_module("scripts.training.phase5_stateful_chat_gate")
    if Path(module.__file__).resolve() != generator_path.resolve():
        raise IntakeHandoffCandidateError("stateful dev generator module 경로가 다릅니다.")
    cases = module._build_cases(dev_config)
    module.validate_dev_suite(cases, dev_config)
    candidate_values = [
        _normalized_user_transcript(row["messages"]) for row in rows
    ]
    dev_values = [_normalized_user_transcript(case["messages"]) for case in cases]
    exact_overlaps = len(set(candidate_values) & set(dev_values))
    threshold = float(separation["near_duplicate_threshold"])
    candidate_ngrams = [_character_ngrams(value) for value in candidate_values]
    dev_ngrams = [_character_ngrams(value) for value in dev_values]
    near_duplicate_pairs = 0
    maximum_similarity = 0.0
    for candidate in candidate_ngrams:
        for dev in dev_ngrams:
            union_size = len(candidate | dev)
            similarity = len(candidate & dev) / union_size if union_size else 1.0
            maximum_similarity = max(maximum_similarity, similarity)
            if similarity >= threshold:
                near_duplicate_pairs += 1
    if exact_overlaps != separation["normalized_exact_overlaps_allowed"]:
        raise IntakeHandoffCandidateError("stateful dev와 정규화 exact overlap이 있습니다.")
    if near_duplicate_pairs != separation["near_duplicate_pairs_allowed"]:
        raise IntakeHandoffCandidateError("stateful dev와 근접중복 후보가 있습니다.")
    return {
        "status": "passed",
        "candidate_rows": len(rows),
        "dev_cases": len(cases),
        "candidate_component_namespace_distinct": True,
        "template_namespace_distinct": True,
        "normalized_exact_overlaps": exact_overlaps,
        "near_duplicate_metric": separation["near_duplicate_metric"],
        "near_duplicate_threshold": threshold,
        "near_duplicate_pairs": near_duplicate_pairs,
        "maximum_similarity": round(maximum_similarity, 6),
        "individual_pair_ids_or_hashes_in_report": False,
    }


def _validate_message_safety(
    row: dict[str, Any], config: dict[str, Any]
) -> tuple[int, int]:
    limits = config["validation_contract"]
    messages = row["messages"]
    total_characters = sum(len(message["content"]) for message in messages)
    non_whitespace = sum(
        sum(not character.isspace() for character in message["content"])
        for message in messages
    )
    if total_characters > limits["max_conversation_characters"]:
        raise IntakeHandoffCandidateError("대화 문자 수 제한을 넘었습니다.")
    if non_whitespace > limits["max_non_whitespace_characters"]:
        raise IntakeHandoffCandidateError("대화 비공백 문자 수 제한을 넘었습니다.")
    for message in messages:
        content = message["content"]
        if len(content) > limits["max_message_characters"]:
            raise IntakeHandoffCandidateError("메시지 문자 수 제한을 넘었습니다.")
        if CONTROL_CHARACTER_PATTERN.search(content):
            raise IntakeHandoffCandidateError("제어문자가 있는 메시지는 허용하지 않습니다.")
        if any(pattern.search(content) for pattern in PROMPT_CONTROL_PATTERNS):
            raise IntakeHandoffCandidateError("prompt 제어 토큰을 허용하지 않습니다.")
        if any(
            pattern.search(content)
            for pattern in (
                EMAIL_PATTERN,
                PHONE_PATTERN,
                RESIDENT_ID_PATTERN,
                ACCOUNT_PATTERN,
                URL_PATTERN,
                FULL_BIRTH_DATE_PATTERN,
            )
        ):
            raise IntakeHandoffCandidateError("개인정보 가능 문자열을 허용하지 않습니다.")
    return total_characters, non_whitespace


def _validate_assistant_safety(row: dict[str, Any]) -> None:
    transcript = "\n".join(
        message["content"] for message in row["messages"] if message["role"] != "system"
    )
    if row["stratum"] == "structured_chart_ready":
        if STRUCTURED_INTAKE_FACT_PATTERN.search(transcript):
            raise IntakeHandoffCandidateError(
                "structured chart 대화에 출생 intake 사실을 섞을 수 없습니다."
            )
    elif INTAKE_CHART_TERM_PATTERN.search(transcript) or GANZHI_PAIR_PATTERN.search(
        transcript
    ):
        raise IntakeHandoffCandidateError(
            "intake 대화에 chart·간지·원국 사실을 섞을 수 없습니다."
        )
    if (
        FULL_BIRTH_DATE_PATTERN.search(transcript) or "생년월일" in transcript
    ) and RUNTIME_CHART_PATTERN.search(transcript):
        raise IntakeHandoffCandidateError(
            "한 transcript에 출생 입력과 runtime chart를 함께 둘 수 없습니다."
        )
    assistant_text = "\n".join(
        message["content"]
        for message in row["messages"]
        if message["role"] == "assistant"
    )
    for message in row["messages"]:
        if message["role"] != "assistant":
            continue
        sentence_count = len(
            [
                value
                for value in re.split(r"[.!?。！？]+", message["content"])
                if value.strip()
            ]
        )
        if sentence_count not in {1, 2, 3}:
            raise IntakeHandoffCandidateError("assistant 응답은 1~3문장이어야 합니다.")
    calendar_type = row["slot_state_transition"]["after"]["calendar_type"]
    leap_month = row["slot_state_transition"]["after"]["leap_month"]
    requested_slots = row["expected_action"]["request_slots"]
    if calendar_type == "solar":
        if leap_month != "not_applicable" or "윤달" in assistant_text:
            raise IntakeHandoffCandidateError(
                "양력 입력에는 윤달을 다시 질문할 수 없습니다."
            )
    elif calendar_type == "ambiguous":
        if (
            leap_month != "unknown"
            or requested_slots != ["calendar_type", "leap_month"]
            or "음력이라면 윤달" not in row["messages"][-1]["content"]
        ):
            raise IntakeHandoffCandidateError(
                "달력 모호 상태는 음력일 때만 윤달을 조건부 확인해야 합니다."
            )
    elif calendar_type == "lunar":
        if (
            leap_month != "unknown"
            or requested_slots != ["leap_month"]
            or "평달인지 윤달인지" not in row["messages"][-1]["content"]
        ):
            raise IntakeHandoffCandidateError(
                "음력 확정 상태에서는 평달·윤달 여부만 확인해야 합니다."
            )
    elif calendar_type == "missing":
        if (
            leap_month != "missing"
            or "leap_month" in requested_slots
            or "윤달" in assistant_text
        ):
            raise IntakeHandoffCandidateError(
                "달력 유형 확인 전에는 윤달을 선요청할 수 없습니다."
            )
    elif calendar_type == "not_required" and leap_month != "not_required":
        raise IntakeHandoffCandidateError("구조 사실 전용 행의 달력 slot이 다릅니다.")
    if len(GANZHI_PAIR_PATTERN.findall(assistant_text)) >= 4:
        raise IntakeHandoffCandidateError("조작된 사주 네 기둥을 허용하지 않습니다.")
    if len(set(PILLAR_LABEL_PATTERN.findall(assistant_text))) == 4:
        raise IntakeHandoffCandidateError("년월일시주 전체 생성 표현을 허용하지 않습니다.")
    if any(pattern.search(assistant_text) for pattern in POSITIVE_FALSE_COMPLETION_PATTERNS):
        raise IntakeHandoffCandidateError("허위 UI·완료 주장을 허용하지 않습니다.")
    if FULL_BIRTH_DATE_PATTERN.search(transcript) and GANZHI_PAIR_PATTERN.search(
        transcript
    ):
        raise IntakeHandoffCandidateError("생년월일과 간지를 연결할 수 없습니다.")
    final_answer = row["messages"][-1]["content"]
    for term in row["expected_action"]["required_terms"]:
        if term not in final_answer:
            raise IntakeHandoffCandidateError(
                f"기대 행동 용어가 최종 답변에 없습니다: {term}"
            )


def _validate_row_shape(
    row: dict[str, Any], config: dict[str, Any], system_content: str
) -> tuple[int, int, int]:
    required_keys = {
        "candidate_id",
        "schema_version",
        "task",
        "stratum",
        "system_prompt_ref",
        "slot_state_transition",
        "messages",
        "expected_action",
        "forbidden_claims",
        "provenance",
        "leakage_component_id",
        "birth_to_pillars_training_target",
        "promotion_status",
        "sharing",
        "privacy",
    }
    if set(row) != required_keys:
        raise IntakeHandoffCandidateError("후보 최상위 schema가 다릅니다.")
    if (
        row["schema_version"] != SCHEMA_VERSION
        or row["task"] != TASK
        or row["stratum"] not in STRATA
        or row["birth_to_pillars_training_target"] is not False
        or row["promotion_status"] != "candidate_only"
        or row["forbidden_claims"] != list(FORBIDDEN_CLAIMS)
    ):
        raise IntakeHandoffCandidateError("후보 identity·승격 계약이 다릅니다.")
    messages = row["messages"]
    if not isinstance(messages, list) or len(messages) not in {3, 5, 7}:
        raise IntakeHandoffCandidateError("대화는 system 뒤 1~3턴이어야 합니다.")
    expected_roles = ["system"] + [
        role for _ in range((len(messages) - 1) // 2) for role in ("user", "assistant")
    ]
    if [message.get("role") for message in messages] != expected_roles:
        raise IntakeHandoffCandidateError("메시지 role 순서가 다릅니다.")
    if any(set(message) != {"role", "content"} for message in messages):
        raise IntakeHandoffCandidateError("메시지 schema가 다릅니다.")
    if any(not isinstance(message["content"], str) or not message["content"].strip() for message in messages):
        raise IntakeHandoffCandidateError("빈 메시지는 허용하지 않습니다.")
    if messages[0]["content"] != system_content:
        raise IntakeHandoffCandidateError("첫 메시지의 안내 보정 system prompt가 다릅니다.")
    prompt = config["system_prompt"]
    if row["system_prompt_ref"] != {
        "profile_id": prompt["profile_id"],
        "version": prompt["version"],
        "sha256": prompt["sha256"],
    }:
        raise IntakeHandoffCandidateError("system prompt reference가 다릅니다.")
    scenario_index = row.get("provenance", {}).get("scenario_index")
    if not isinstance(scenario_index, int) or scenario_index not in range(
        len(SCENARIO_FAMILIES)
    ):
        raise IntakeHandoffCandidateError("scenario index가 다릅니다.")
    transition = row["slot_state_transition"]
    if transition != _slot_transition(row["stratum"], scenario_index):
        raise IntakeHandoffCandidateError("slot-state transition이 다릅니다.")
    for state in (transition["before"], transition["after"]):
        if set(state) != set(SLOTS):
            raise IntakeHandoffCandidateError("slot-state schema가 다릅니다.")
        if any(state[slot] not in SLOT_VALUES[slot] for slot in SLOTS):
            raise IntakeHandoffCandidateError("허용하지 않는 slot-state 값입니다.")
    provenance = row["provenance"]
    if set(provenance) != {
        "source_type",
        "generation_method",
        "generator_version",
        "seed",
        "component_namespace",
        "template_namespace",
        "style_index",
        "scenario_index",
        "scenario_family",
        "source_record_ids",
        "human_domain_review",
    }:
        raise IntakeHandoffCandidateError("provenance schema가 다릅니다.")
    style = provenance["style_index"]
    scenario = provenance["scenario_index"]
    if not isinstance(style, int) or style not in range(len(OPENERS)):
        raise IntakeHandoffCandidateError("style index가 다릅니다.")
    if not isinstance(scenario, int) or scenario not in range(len(REQUEST_ENDINGS)):
        raise IntakeHandoffCandidateError("scenario index가 다릅니다.")
    if provenance["scenario_family"] != SCENARIO_FAMILIES[scenario]:
        raise IntakeHandoffCandidateError("scenario family가 index와 다릅니다.")
    separation = config["dev_suite_separation"]
    if (
        provenance["component_namespace"]
        != separation["candidate_component_namespace"]
        or provenance["component_namespace"]
        == separation["forbidden_dev_component_namespace"]
        or provenance["template_namespace"]
        != f"{separation['candidate_template_namespace']}.{row['stratum']}"
    ):
        raise IntakeHandoffCandidateError("candidate/dev namespace 분리가 다릅니다.")
    expected = _make_candidate(
        config,
        row["stratum"],
        style,
        scenario,
        system_content,
    )
    if row != expected:
        raise IntakeHandoffCandidateError("후보가 결정적 template 계약과 다릅니다.")
    total_characters, non_whitespace = _validate_message_safety(row, config)
    _validate_assistant_safety(row)
    return (len(messages) - 1) // 2, total_characters, non_whitespace


def validate_candidate_rows(
    rows: Sequence[dict[str, Any]], config: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    validate_contract(config, repo_root)
    if len(rows) != TOTAL_ROWS:
        raise IntakeHandoffCandidateError("후보 행 수가 2,000이 아닙니다.")
    prompt = _load_system_prompt(config, repo_root)
    system_content = _system_message_content(config, prompt)
    stratum_counts: Counter[str] = Counter()
    turn_counts: Counter[int] = Counter()
    candidate_ids: set[str] = set()
    conversation_signatures: set[str] = set()
    component_strata: dict[str, set[str]] = defaultdict(set)
    component_counts: Counter[str] = Counter()
    style_scenario_pairs: dict[str, set[tuple[int, int]]] = defaultdict(set)
    scenario_family_counts: Counter[str] = Counter()
    scenario_family_by_stratum: dict[str, Counter[str]] = defaultdict(Counter)
    max_message_characters = 0
    max_conversation_characters = 0
    max_non_whitespace_characters = 0
    previous_order_key: tuple[str, str] | None = None
    for row in rows:
        turns, conversation_characters, non_whitespace = _validate_row_shape(
            row, config, system_content
        )
        current_order_key = _order_key(row)
        if previous_order_key is not None and current_order_key < previous_order_key:
            raise IntakeHandoffCandidateError("후보 순서가 고정 seed 순서와 다릅니다.")
        previous_order_key = current_order_key
        candidate_id = row["candidate_id"]
        if candidate_id in candidate_ids:
            raise IntakeHandoffCandidateError("candidate_id가 중복됩니다.")
        candidate_ids.add(candidate_id)
        signature = _normalized_conversation(row["messages"])
        if signature in conversation_signatures:
            raise IntakeHandoffCandidateError("정규화 대화가 중복됩니다.")
        conversation_signatures.add(signature)
        stratum = row["stratum"]
        stratum_counts[stratum] += 1
        turn_counts[turns] += 1
        component = row["leakage_component_id"]
        component_strata[component].add(stratum)
        component_counts[component] += 1
        provenance = row["provenance"]
        family = provenance["scenario_family"]
        scenario_family_counts[family] += 1
        scenario_family_by_stratum[stratum][family] += 1
        style_scenario_pairs[stratum].add(
            (provenance["style_index"], provenance["scenario_index"])
        )
        max_message_characters = max(
            max_message_characters,
            max(len(message["content"]) for message in row["messages"]),
        )
        max_conversation_characters = max(
            max_conversation_characters, conversation_characters
        )
        max_non_whitespace_characters = max(
            max_non_whitespace_characters, non_whitespace
        )
    expected_counts = Counter({stratum: ROWS_PER_STRATUM for stratum in STRATA})
    if stratum_counts != expected_counts:
        raise IntakeHandoffCandidateError("stratum별 200건 분포가 다릅니다.")
    if any(len(values) != 200 for values in style_scenario_pairs.values()):
        raise IntakeHandoffCandidateError("style·scenario cartesian coverage가 다릅니다.")
    if any(len(strata) != 1 for strata in component_strata.values()):
        raise IntakeHandoffCandidateError("leakage component가 stratum을 가로지릅니다.")
    if len(component_counts) != 200 or set(component_counts.values()) != {10}:
        raise IntakeHandoffCandidateError("leakage component 묶음 계약이 다릅니다.")
    expected_families = Counter({family: 10 for family in SCENARIO_FAMILIES})
    if any(
        scenario_family_by_stratum[stratum] != expected_families
        for stratum in STRATA
    ):
        raise IntakeHandoffCandidateError("stratum 내부 scenario family 균형이 다릅니다.")
    if scenario_family_counts != Counter(
        {family: 100 for family in SCENARIO_FAMILIES}
    ):
        raise IntakeHandoffCandidateError("전체 scenario family 분포가 다릅니다.")
    dev_separation = validate_dev_suite_separation(rows, config, repo_root)
    return {
        "status": "passed",
        "total_rows": len(rows),
        "stratum_counts": dict(sorted(stratum_counts.items())),
        "dialogue_turn_counts": {
            str(key): value for key, value in sorted(turn_counts.items())
        },
        "scenario_family_counts": dict(sorted(scenario_family_counts.items())),
        "scenario_family_counts_by_stratum": {
            stratum: dict(sorted(scenario_family_by_stratum[stratum].items()))
            for stratum in STRATA
        },
        "unique_candidate_ids": len(candidate_ids),
        "unique_normalized_conversations": len(conversation_signatures),
        "leakage_components": len(component_counts),
        "rows_per_leakage_component": 10,
        "max_message_characters": max_message_characters,
        "max_conversation_characters": max_conversation_characters,
        "max_non_whitespace_characters": max_non_whitespace_characters,
        "pii_findings": 0,
        "control_character_findings": 0,
        "prompt_control_token_findings": 0,
        "fabricated_four_pillars_findings": 0,
        "birth_date_to_pillars_link_findings": 0,
        "normalized_duplicate_rows": 0,
        "dev_suite_separation": dev_separation,
    }


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = _load_json(config_path, "intake handoff 후보 config")
    validate_contract(config, repo_root)
    try:
        relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise IntakeHandoffCandidateError("config는 저장소 안에 있어야 합니다.") from exc
    implementation_paths = [
        *config["implementation_files"],
        relative_config,
        config["system_prompt"]["path"],
        config["dev_suite_separation"]["config_path"],
        config["dev_suite_separation"]["generator_path"],
    ]
    implementation_hashes: dict[str, str] = {}
    for relative in implementation_paths:
        path = _safe_repo_path(repo_root, relative)
        if path.is_symlink() or not path.is_file():
            raise IntakeHandoffCandidateError(f"fingerprint 입력 파일이 없습니다: {relative}")
        implementation_hashes[relative] = _sha256_file(path)
    build_inputs = {
        "schema_version": config["schema_version"],
        "candidate_version": config["candidate_version"],
        "task": config["task"],
        "seed": config["seed"],
        "system_prompt": config["system_prompt"],
        "generation_contract": config["generation_contract"],
        "validation_contract": config["validation_contract"],
        "sharing_privacy": config["sharing_privacy"],
        "dev_suite_separation": config["dev_suite_separation"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = _sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    outputs = config["outputs"]
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "build_id": build_id,
        "private_root": _safe_repo_path(
            repo_root, outputs["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_repo_path(
            repo_root, outputs["public_root"].format(build_id=build_id)
        ),
    }


def _payloads(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, Any]]:
    config = context["config"]
    rows = generate_candidates(config, repo_root)
    validation = validate_candidate_rows(rows, config, repo_root)
    candidate_payload = _jsonl_bytes(rows)
    private_manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "saju_intake_handoff_candidate_private_build",
        "dataset_name": DATASET_NAME,
        "candidate_version": CANDIDATE_VERSION,
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "build_inputs": context["build_inputs"],
        "artifact_sha256": {"candidates.jsonl": _sha256_bytes(candidate_payload)},
        "artifact_bytes": {"candidates.jsonl": len(candidate_payload)},
        "validation": validation,
        "promotion_status": "candidate_only",
        "training_promotion_allowed": False,
        "birth_to_pillars_training_target": False,
        "contains_real_person_data": False,
        "contains_aihub_source_text": False,
        "contains_manual_session_text": False,
        "contains_real_person_source_text": False,
        "generated_on": config["generated_on"],
    }
    private_manifest_payload = _json_bytes(private_manifest)
    aggregate = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "saju_intake_handoff_candidate_public_aggregate",
        "dataset_name": DATASET_NAME,
        "candidate_version": CANDIDATE_VERSION,
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "system_prompt": {
            "profile_id": config["system_prompt"]["profile_id"],
            "version": config["system_prompt"]["version"],
            "sha256": config["system_prompt"]["sha256"],
        },
        "total_rows": validation["total_rows"],
        "stratum_counts": validation["stratum_counts"],
        "dialogue_turn_counts": validation["dialogue_turn_counts"],
        "scenario_family_counts": validation["scenario_family_counts"],
        "scenario_family_counts_by_stratum": validation[
            "scenario_family_counts_by_stratum"
        ],
        "leakage_components": validation["leakage_components"],
        "dev_suite_separation": validation["dev_suite_separation"],
        "automated_validation": {
            key: value
            for key, value in validation.items()
            if key
            not in {
                "stratum_counts",
                "dialogue_turn_counts",
                "scenario_family_counts",
                "scenario_family_counts_by_stratum",
                "dev_suite_separation",
                "unique_candidate_ids",
            }
        },
        "private_manifest_sha256": _sha256_bytes(private_manifest_payload),
        "rendered_candidate_rows_included": False,
        "tracked_template_fragments_public": True,
        "candidate_ids_included": False,
        "leakage_component_ids_included": False,
        "rendered_row_text_or_individual_hashes_shared": False,
        "contains_real_person_data": False,
        "contains_aihub_source_text": False,
        "contains_manual_session_text": False,
        "contains_real_person_source_text": False,
        "promotion_status": "candidate_only",
        "training_promotion_allowed": False,
        "birth_to_pillars_training_target": False,
        "generated_on": config["generated_on"],
    }
    aggregate_payload = _json_bytes(aggregate)
    public_manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "saju_intake_handoff_candidate_public_manifest",
        "dataset_name": DATASET_NAME,
        "candidate_version": CANDIDATE_VERSION,
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "private_manifest_sha256": _sha256_bytes(private_manifest_payload),
        "artifact_sha256": {"aggregate.json": _sha256_bytes(aggregate_payload)},
        "rendered_candidate_rows_included": False,
        "tracked_template_fragments_public": True,
        "candidate_ids_included": False,
        "training_promotion_allowed": False,
        "generated_on": config["generated_on"],
    }
    return (
        {
            "candidates.jsonl": candidate_payload,
            "build_manifest.json": private_manifest_payload,
        },
        {
            "aggregate.json": aggregate_payload,
            "build_manifest.json": _json_bytes(public_manifest),
        },
        validation,
    )


def _write_new_file(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _write_tree(root: Path, values: dict[str, bytes], *, private: bool) -> None:
    directory_mode = PRIVATE_DIR_MODE if private else PUBLIC_DIR_MODE
    file_mode = PRIVATE_FILE_MODE if private else PUBLIC_FILE_MODE
    root.chmod(directory_mode)
    for relative, payload in values.items():
        path = root / relative
        _write_new_file(path, payload, file_mode)
    for directory in [root, *[path for path in root.rglob("*") if path.is_dir()]]:
        directory.chmod(directory_mode)


def _assert_output_root(repo_root: Path, path: Path) -> None:
    root = repo_root.resolve()
    if not path.resolve(strict=False).is_relative_to(root):
        raise IntakeHandoffCandidateError("build 출력 경로가 저장소 밖입니다.")
    if path.is_symlink():
        raise IntakeHandoffCandidateError("build 출력 경로 symlink를 허용하지 않습니다.")


def build_candidates(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root = context["private_root"]
    public_root = context["public_root"]
    _assert_output_root(repo_root, private_root)
    _assert_output_root(repo_root, public_root)
    if private_root.exists() or public_root.exists():
        if private_root.exists() and public_root.exists():
            result = verify_build(context, repo_root)
            return {**result, "mode": "reused", "writes_performed": False}
        raise IntakeHandoffCandidateError("private/public build가 부분적으로 존재합니다.")
    private_values, public_values, _ = _payloads(context, repo_root)
    private_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_root.parent.mkdir(parents=True, exist_ok=True, mode=PUBLIC_DIR_MODE)
    private_tmp = Path(
        tempfile.mkdtemp(prefix=f".{private_root.name}-", dir=private_root.parent)
    )
    public_tmp = Path(
        tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent)
    )
    private_promoted = False
    public_promoted = False
    try:
        _write_tree(private_tmp, private_values, private=True)
        _write_tree(public_tmp, public_values, private=False)
        os.replace(private_tmp, private_root)
        private_promoted = True
        os.replace(public_tmp, public_root)
        public_promoted = True
    finally:
        if not private_promoted:
            shutil.rmtree(private_tmp, ignore_errors=True)
        if not public_promoted:
            shutil.rmtree(public_tmp, ignore_errors=True)
        if private_promoted and not public_promoted:
            raise IntakeHandoffCandidateError(
                "private build만 승격되었습니다. 수동 삭제 없이 중단합니다."
            )
    result = verify_build(context, repo_root)
    return {**result, "mode": "built", "writes_performed": True}


def _verify_tree(root: Path, values: dict[str, bytes], *, private: bool) -> None:
    expected_root_mode = PRIVATE_DIR_MODE if private else PUBLIC_DIR_MODE
    expected_file_mode = PRIVATE_FILE_MODE if private else PUBLIC_FILE_MODE
    if root.is_symlink() or not root.is_dir():
        raise IntakeHandoffCandidateError(f"build 디렉터리가 없습니다: {root}")
    if stat.S_IMODE(root.stat().st_mode) != expected_root_mode:
        raise IntakeHandoffCandidateError(f"build 디렉터리 권한이 다릅니다: {root}")
    actual_files = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    if actual_files != set(values):
        raise IntakeHandoffCandidateError(f"build 파일 집합이 다릅니다: {root}")
    for relative, payload in values.items():
        path = root / relative
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise IntakeHandoffCandidateError(
                f"기존 불변 build byte hash가 다릅니다: {path}"
            )
        if stat.S_IMODE(path.stat().st_mode) != expected_file_mode:
            raise IntakeHandoffCandidateError(f"build 파일 권한이 다릅니다: {path}")


def verify_build(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    _assert_output_root(repo_root, context["private_root"])
    _assert_output_root(repo_root, context["public_root"])
    private_values, public_values, validation = _payloads(context, repo_root)
    _verify_tree(context["private_root"], private_values, private=True)
    _verify_tree(context["public_root"], public_values, private=False)
    return {
        "status": "verified_candidate_only",
        "candidate_version": CANDIDATE_VERSION,
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "total_rows": validation["total_rows"],
        "stratum_counts": validation["stratum_counts"],
        "training_promotion_allowed": False,
        "birth_to_pillars_training_target": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="결정적 사주 intake handoff 보강 후보 v1"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    build = commands.add_parser("build")
    build.add_argument("--execute", action="store_true")
    build.add_argument("--confirm-build-id")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            config = _load_json(config_path, "intake handoff 후보 config")
            result = validate_contract(config, REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "build":
                if not args.execute:
                    rows = generate_candidates(context["config"], REPO_ROOT)
                    validation = validate_candidate_rows(
                        rows, context["config"], REPO_ROOT
                    )
                    result = {
                        "status": "dry_run",
                        "build_id": context["build_id"],
                        "build_sha256": context["build_sha256"],
                        "planned_rows": validation["total_rows"],
                        "stratum_counts": validation["stratum_counts"],
                        "required_confirmation": context["build_id"],
                        "writes_performed": False,
                    }
                else:
                    if args.confirm_build_id != context["build_id"]:
                        raise IntakeHandoffCandidateError(
                            "--execute에는 dry-run이 제시한 --confirm-build-id가 필요합니다."
                        )
                    result = build_candidates(context, REPO_ROOT)
            else:
                result = verify_build(context, REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계에서 구조화된 실패를 반환한다.
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
