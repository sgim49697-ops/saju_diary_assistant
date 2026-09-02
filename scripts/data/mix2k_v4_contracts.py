# mix2k_v4_contracts.py - 8K MIX2K v4 행·teacher·토큰 계약을 fail-closed로 검증한다.

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes

DATASET_VERSION = "mix2k-v4-chart-day-8k"
RECORD_SCHEMA_VERSION = "4.0.0"
RUNTIME_RELEASE_ID = "saju-runtime-release-v1.5.0-8b1d6ea2d46e"
RUNTIME_BINDING_ID = "saju-chart-day-dashboard-binding-v1.1.0"
RUNTIME_BINDING_SCHEMA = "1.1.0"
EXPECTED_ROWS = 2_000
MAX_PROMPT_TOKENS = 4_096
MAX_COMPLETION_TOKENS = 4_096
MAX_LENGTH = 8_192
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

EXPECTED_AXES = {
    "structured_fact_schema_literacy": 300,
    "chart_facts_natural_explanation": 300,
    "chart_day_today_flow": 450,
    "followup_explain_grounding": 300,
    "intake_state_correction": 250,
    "general_korean_empathy": 250,
    "uncertainty_blocked_boundary": 100,
    "hard_fact_short_qa": 50,
}
SUBSTANTIVE_AXES = frozenset(
    {
        "structured_fact_schema_literacy",
        "chart_facts_natural_explanation",
        "chart_day_today_flow",
        "followup_explain_grounding",
        "general_korean_empathy",
        "uncertainty_blocked_boundary",
    }
)
RUNTIME_AXES = frozenset(
    {
        "structured_fact_schema_literacy",
        "chart_facts_natural_explanation",
        "chart_day_today_flow",
        "followup_explain_grounding",
    }
)
DAY_AXES = RUNTIME_AXES
MULTITURN_AXES = frozenset({"followup_explain_grounding"})

FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GANYI = re.compile(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]")
ISO_DATE = re.compile(r"20[0-4][0-9]-[01][0-9]-[0-3][0-9]")
SENTENCE_END = re.compile(r"(?:[.!?]|[。！？])(?:[\"'”’)]*)?(?=\s|$)")
NEGATED_STRUCTURAL_CLAIM = re.compile(
    r"(?:제공되지|주어지지|확인되지|계산하지|판단하지|"
    r"단정하지|알\s*수\s*없|근거가\s*없|범위가\s*아니)"
)
INTERNAL_LANGUAGE = re.compile(
    r"(?:canonical|runtime|snapshot|capability|fact[_ -]?authority|내부\s*해시|승인된\s*사실)",
    re.IGNORECASE,
)
FORBIDDEN_PREDICTION = re.compile(
    r"(?:반드시|확실히|틀림없이).{0,24}(?:생긴다|일어난다|된다|성공|이별|결혼|합격|부자)",
)
TEN_GODS = frozenset(
    {"비견", "겁재", "식신", "상관", "편재", "정재", "편관", "정관", "편인", "정인"}
)
UNSUPPORTED_STRUCTURAL_PATTERNS = (
    (
        "rooting",
        re.compile(
            r"(?:일간|[甲乙丙丁戊己庚辛壬癸](?:목|화|토|금|수)?)"
            r"[^\n.!?]{0,24}(?:지지|[子丑寅卯辰巳午未申酉戌亥])[^\n.!?]{0,12}"
            r"뿌리(?:를|가|는)?\s*(?:두|내리|있)"
        ),
    ),
    ("rooting", re.compile(r"통근(?:하|했|되|된|이|을|은|는)")),
    ("seasonal_strength", re.compile(r"득령(?:하|했|되|된|이|을|은|는)")),
    (
        "strength_pattern_yongshin",
        re.compile(
            r"(?:신강약|신강|신약|격국|용신)(?:은|는|이|가|을|를|의|으로|이다|입니다|에)"
        ),
    ),
    (
        "relation",
        re.compile(
            r"(?:삼합|육합|방합|암합|천간합|지지합|합충(?:형파해)?|상충|형살|파살|해살|"
            r"충돌?\s*관계)"
        ),
    ),
    (
        "relation",
        re.compile(
            r"(?:[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]|서로|둘이|두\s*기운)"
            r"[^\n.!?]{0,16}(?:합|충|형|파|해)(?:을|이|으로)?\s*"
            r"(?:이루|성립|작용|한다|합니다|됩니다|이다|입니다)"
        ),
    ),
    (
        "fortune_cycle",
        re.compile(
            r"(?:대운|세운)(?:은|는|이|가|을|를|의|에서|으로|이다|입니다)"
        ),
    ),
)
PILLAR_LABELS = {"연주": "year", "년주": "year", "월주": "month", "일주": "day", "시주": "hour"}
PERIOD_LABELS = {
    "연간지": "year_ganzhi",
    "해의 간지": "year_ganzhi",
    "월간지": "month_ganzhi",
    "달의 간지": "month_ganzhi",
    "일진": "day_ganzhi",
    "일간지": "day_ganzhi",
    "날의 간지": "day_ganzhi",
}
RESTRICTED_MARKERS = re.compile(
    r"(?:AI\s*Hub|aihub|개인정보|주민등록|전화번호|restricted_local_only\s*[=:]\s*true)",
    re.IGNORECASE,
)

SPEC_FIELDS = {
    "schema_version",
    "dataset_version",
    "id",
    "conversation_id",
    "task_axis",
    "template_family",
    "substantive",
    "multiturn",
    "drafter",
    "reviewer",
    "prompt",
    "runtime_binding",
    "allowed_fact_paths",
    "allowed_fact_values",
    "response_contract",
    "restricted_local_only",
}
DRAFT_FIELDS = {
    "record_id",
    "answer",
    "used_fact_paths",
    "used_fact_values",
    "soft_interpretation_used",
    "limitations",
    "self_check",
}
REVIEW_FIELDS = {
    "record_id",
    "decision",
    "failure_codes",
    "fact_errors",
    "style_notes",
    "rewrite_instructions",
}


class Mix2KV4ContractError(RuntimeError):
    """MIX2K v4의 데이터·외부 전송·학습 후보 계약 위반."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z가-힣甲-龥]+", "", normalized)


def nonempty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def sentence_count(value: str) -> int:
    return len(SENTENCE_END.findall(value.strip()))


def flatten_runtime_facts(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(flatten_runtime_facts(value[key], child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flattened.extend(flatten_runtime_facts(item, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        flattened.append((prefix, value))
    elif isinstance(value, (int, float, bool)) or value is None:
        flattened.append((prefix, json.dumps(value, ensure_ascii=False)))
    return flattened


def _path_value(spec: Mapping[str, Any], suffix: str) -> str | None:
    for path, value in zip(
        spec["allowed_fact_paths"], spec["allowed_fact_values"], strict=True
    ):
        if path.endswith(suffix):
            return value
    return None


def structural_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """일반 해석이 아니라 명시적인 schema label·entity claim만 강하게 검사한다."""

    errors: list[str] = []
    allowed = set(spec["allowed_fact_values"])
    for ganzhi in GANYI.findall(answer):
        if ganzhi not in allowed:
            errors.append(f"unprovided_ganzhi:{ganzhi}")
    for target_date in ISO_DATE.findall(answer):
        if target_date not in allowed:
            errors.append(f"unprovided_date:{target_date}")
    for label, field in PILLAR_LABELS.items():
        expected = _path_value(spec, f"chart.hard_facts.pillars.{field}.ganzhi")
        if expected is None:
            continue
        for match in re.finditer(rf"{label}(?:는|은|가|:|=|\s)+[^\n.!?]{{0,18}}?({GANYI.pattern})", answer):
            if match.group(1) != expected:
                errors.append(f"natal_{field}_label_confusion:{match.group(1)}")
    for label, field in PERIOD_LABELS.items():
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        if expected is None:
            continue
        for match in re.finditer(rf"{re.escape(label)}(?:은|는|이|가|:|=|\s)+[^\n.!?]{{0,18}}?({GANYI.pattern})", answer):
            if match.group(1) != expected:
                errors.append(f"period_{field}_label_confusion:{match.group(1)}")
    natal_day = _path_value(spec, "chart.hard_facts.pillars.day.ganzhi")
    if natal_day and any(
        re.search(pattern, answer)
        for pattern in (
            (
                rf"원국\s*(?:전체)?\s*(?:은|는|이|가|:|=|\()\s*{natal_day}"
                rf"(?:이라고|이다|입니다|인|\)|\s|[,.:;!?]|$)"
            ),
            rf"{natal_day}\s*(?:하나만|한 기둥만)?\s*(?:이|가)?\s*원국\s*전체",
        )
    ):
        errors.append("natal_day_called_full_chart")
    period_year = _path_value(spec, "period.hard_facts.period.year_ganzhi")
    if period_year and any(
        re.search(pattern, answer)
        for pattern in (
            rf"(?:오늘(?:의)?\s*)?일진(?:은|는|이|가|:|=|\s)+[^\n.!?]{{0,12}}{period_year}",
            rf"{period_year}[^\n.!?]{{0,12}}(?:오늘(?:의)?\s*)?일진",
        )
    ):
        errors.append("period_year_called_day_ganzhi")
    period_day = _path_value(spec, "period.hard_facts.period.day_ganzhi")
    if period_day and re.search(rf"{period_day}[^\n.!?]{{0,12}}세운|세운[^\n.!?]{{0,12}}{period_day}", answer):
        errors.append("period_day_called_seun")
    day_master = _path_value(spec, "chart.hard_facts.day_master.stem")
    if day_master:
        for match in re.finditer(
            r"일간(?:은|는|이|가|:|=|\s)+[^\n.!?]{0,10}?([甲乙丙丁戊己庚辛壬癸])",
            answer,
        ):
            if match.group(1) != day_master:
                errors.append(f"day_master_confusion:{match.group(1)}")
    for match in re.finditer(
        r"(?:천간|지지|지장간|hidden\s*stems?)[^\n.!?]{0,36}",
        answer,
        re.IGNORECASE,
    ):
        entities = re.findall(
            r"[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]", match.group(0)
        )
        for entity in entities:
            if entity not in allowed:
                errors.append(f"unprovided_stem_or_branch:{entity}")
    mentioned_ten_gods = {value for value in TEN_GODS if value in answer}
    missing_ten_gods = mentioned_ten_gods - allowed
    errors.extend(f"unprovided_ten_god:{value}" for value in sorted(missing_ten_gods))
    for label, pattern in UNSUPPORTED_STRUCTURAL_PATTERNS:
        for match in pattern.finditer(answer):
            window = answer[
                max(0, match.start() - 48) : min(len(answer), match.end() + 48)
            ]
            if not NEGATED_STRUCTURAL_CLAIM.search(window):
                errors.append(f"unsupported_structural_claim:{label}")
                break
    return list(dict.fromkeys(errors))


def required_fact_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """질문 축이 요구하는 최소 natal·period evidence 누락만 검사한다."""

    errors: list[str] = []
    natal = [
        _path_value(spec, f"chart.hard_facts.pillars.{field}.ganzhi")
        for field in ("year", "month", "day", "hour")
    ]
    natal = [value for value in natal if value is not None]
    axis = spec["task_axis"]
    if axis == "chart_facts_natural_explanation" and not any(
        value in answer for value in natal
    ):
        errors.append("provided_natal_fact_omitted")
    if axis in {"chart_day_today_flow", "followup_explain_grounding"}:
        period_day = _path_value(spec, "period.hard_facts.period.day_ganzhi")
        if period_day is not None and period_day not in answer:
            errors.append("provided_period_day_fact_omitted")
        if natal and not any(value in answer for value in natal):
            errors.append("provided_natal_fact_omitted")
    return errors


def _validate_message(message: Any, *, assistant_allowed: bool) -> None:
    if (
        not isinstance(message, Mapping)
        or set(message) != {"role", "content"}
        or message.get("role") not in ({"system", "user", "assistant"} if assistant_allowed else {"system", "user"})
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
    ):
        raise Mix2KV4ContractError("prompt message 계약이 다릅니다.")


def validate_runtime_binding(binding: Any, *, require_day: bool) -> None:
    if not isinstance(binding, Mapping) or set(binding) != {
        "binding_id",
        "capability_sha256",
        "schema_version",
        "snapshot_sha256",
        "state_revision",
        "value",
    }:
        raise Mix2KV4ContractError("runtime binding field 집합이 다릅니다.")
    value = binding.get("value")
    if (
        binding.get("binding_id") != RUNTIME_BINDING_ID
        or binding.get("schema_version") != RUNTIME_BINDING_SCHEMA
        or FULL_SHA256.fullmatch(str(binding.get("capability_sha256", ""))) is None
        or FULL_SHA256.fullmatch(str(binding.get("snapshot_sha256", ""))) is None
        or isinstance(binding.get("state_revision"), bool)
        or not isinstance(binding.get("state_revision"), int)
        or binding["state_revision"] < 1
        or not isinstance(value, Mapping)
        or set(value) != {"chart", "period"}
        or not isinstance(value.get("chart"), Mapping)
    ):
        raise Mix2KV4ContractError("runtime binding identity가 다릅니다.")
    canonical = canonical_json_bytes(value)
    if sha256_bytes(canonical) != binding["snapshot_sha256"]:
        raise Mix2KV4ContractError("runtime snapshot SHA-256이 다릅니다.")
    chart = value["chart"]
    if chart.get("status") != "ok" or chart.get("fact_authority") != "HARD_GT":
        raise Mix2KV4ContractError("runtime chart가 exact HARD_GT가 아닙니다.")
    period = value.get("period")
    if require_day:
        facts = period.get("hard_facts") if isinstance(period, Mapping) else None
        point = facts.get("period") if isinstance(facts, Mapping) else None
        if (
            not isinstance(period, Mapping)
            or period.get("status") != "ok"
            or period.get("fact_authority") != "HARD_GT"
            or not isinstance(point, Mapping)
            or point.get("period_type") != "day"
            or point.get("timezone") != "Asia/Seoul"
            or point.get("evaluation_local_time") != "12:00"
        ):
            raise Mix2KV4ContractError("runtime 단일 일진 계약이 다릅니다.")
    elif period is not None:
        raise Mix2KV4ContractError("chart-only spec에는 period가 없어야 합니다.")


def validate_spec(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != SPEC_FIELDS:
        raise Mix2KV4ContractError("spec field 집합이 다릅니다.")
    axis = row.get("task_axis")
    if (
        row.get("schema_version") != RECORD_SCHEMA_VERSION
        or row.get("dataset_version") != DATASET_VERSION
        or axis not in EXPECTED_AXES
        or not isinstance(row.get("id"), str)
        or not row["id"].startswith("m2v4_")
        or not isinstance(row.get("conversation_id"), str)
        or not row["conversation_id"].startswith("m2v4c_")
        or not isinstance(row.get("template_family"), str)
        or row.get("substantive") is not (axis in SUBSTANTIVE_AXES)
        or row.get("multiturn") is not (axis in MULTITURN_AXES)
        or row.get("drafter") not in {"claude", "codex"}
        or row.get("reviewer") not in {"claude", "codex"}
        or row["drafter"] == row["reviewer"]
        or row.get("restricted_local_only") is not False
    ):
        raise Mix2KV4ContractError("spec identity·역할 계약이 다릅니다.")
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or len(prompt) < 2:
        raise Mix2KV4ContractError("spec prompt가 비었습니다.")
    for message in prompt:
        _validate_message(message, assistant_allowed=True)
    if prompt[0]["role"] != "system" or prompt[-1]["role"] != "user":
        raise Mix2KV4ContractError("spec prompt 시작·종료 role이 다릅니다.")
    expected_roles = ["system", *(
        "user" if index % 2 == 0 else "assistant"
        for index in range(len(prompt) - 1)
    )]
    if [message["role"] for message in prompt] != expected_roles:
        raise Mix2KV4ContractError("spec prompt role이 교대로 배치되지 않았습니다.")
    if row["multiturn"] and not any(message["role"] == "assistant" for message in prompt[1:-1]):
        raise Mix2KV4ContractError("multiturn spec에 이전 assistant가 없습니다.")
    binding = row.get("runtime_binding")
    if axis in RUNTIME_AXES:
        validate_runtime_binding(binding, require_day=axis in DAY_AXES)
    elif binding is not None:
        if axis != "uncertainty_blocked_boundary":
            raise Mix2KV4ContractError("runtime 비대상 axis에 binding이 있습니다.")
        validate_runtime_binding(binding, require_day=True)
    allowed_paths = row.get("allowed_fact_paths")
    allowed_values = row.get("allowed_fact_values")
    if not isinstance(allowed_paths, list) or not isinstance(allowed_values, list):
        raise Mix2KV4ContractError("fact allowlist가 list가 아닙니다.")
    if any(not isinstance(item, str) or not item for item in [*allowed_paths, *allowed_values]):
        raise Mix2KV4ContractError("fact allowlist 값이 잘못됐습니다.")
    if len(allowed_paths) != len(allowed_values):
        raise Mix2KV4ContractError("fact allowlist path/value 길이가 다릅니다.")
    if binding is not None:
        flattened = flatten_runtime_facts(binding["value"])
        if allowed_paths != [path for path, _ in flattened] or allowed_values != [
            value for _, value in flattened
        ]:
            raise Mix2KV4ContractError(
                "fact allowlist가 full runtime snapshot과 다릅니다."
            )
    contract = row.get("response_contract")
    if not isinstance(contract, Mapping) or set(contract) != {
        "hard_max_completion_tokens",
        "minimum_nonempty_lines",
        "minimum_sentences",
        "natural_length_no_preferred_maximum",
    }:
        raise Mix2KV4ContractError("response contract field 집합이 다릅니다.")
    expected_minimum = 3 if row["substantive"] else 1
    if contract != {
        "hard_max_completion_tokens": MAX_COMPLETION_TOKENS,
        "minimum_nonempty_lines": expected_minimum,
        "minimum_sentences": expected_minimum,
        "natural_length_no_preferred_maximum": True,
    }:
        raise Mix2KV4ContractError("response contract 값이 다릅니다.")
    serialized = json.dumps(row, ensure_ascii=False, sort_keys=True)
    if RESTRICTED_MARKERS.search(serialized):
        raise Mix2KV4ContractError("외부 전송 금지 marker가 spec에 있습니다.")
    return row


def validate_draft(spec: Mapping[str, Any], draft: Any) -> dict[str, Any]:
    if not isinstance(draft, dict) or set(draft) != DRAFT_FIELDS:
        raise Mix2KV4ContractError("teacher draft field 집합이 다릅니다.")
    answer = draft.get("answer")
    if (
        draft.get("record_id") != spec["id"]
        or not isinstance(answer, str)
        or not answer.strip()
        or not isinstance(draft.get("used_fact_paths"), list)
        or not isinstance(draft.get("used_fact_values"), list)
        or type(draft.get("soft_interpretation_used")) is not bool
        or not isinstance(draft.get("limitations"), list)
        or draft.get("self_check") != "PASS"
    ):
        raise Mix2KV4ContractError("teacher draft identity·self-check가 다릅니다.")
    if any(not isinstance(item, str) for item in [*draft["used_fact_paths"], *draft["used_fact_values"], *draft["limitations"]]):
        raise Mix2KV4ContractError("teacher draft provenance가 문자열 list가 아닙니다.")
    if not set(draft["used_fact_paths"]).issubset(spec["allowed_fact_paths"]):
        raise Mix2KV4ContractError("teacher가 허용되지 않은 fact path를 사용했습니다.")
    if not set(draft["used_fact_values"]).issubset(spec["allowed_fact_values"]):
        raise Mix2KV4ContractError("teacher가 허용되지 않은 fact value를 사용했습니다.")
    contract = spec["response_contract"]
    if (
        len(nonempty_lines(answer)) < contract["minimum_nonempty_lines"]
        or sentence_count(answer) < contract["minimum_sentences"]
    ):
        raise Mix2KV4ContractError(
            "teacher 답변이 spec의 최소 줄·문장 계약을 충족하지 않습니다."
        )
    if INTERNAL_LANGUAGE.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 내부 계약 용어를 노출합니다.")
    if RESTRICTED_MARKERS.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 외부 반출 금지 marker를 포함합니다.")
    if FORBIDDEN_PREDICTION.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 확정적 사건 예측을 포함합니다.")
    errors = structural_claim_errors(spec, answer)
    errors.extend(required_fact_errors(spec, answer))
    if errors:
        raise Mix2KV4ContractError("teacher 구조 사실 claim 오류: " + ",".join(errors))
    claimed_values = {
        *GANYI.findall(answer),
        *ISO_DATE.findall(answer),
        *(value for value in TEN_GODS if value in answer),
    }
    if not claimed_values.issubset(draft["used_fact_values"]):
        missing = sorted(claimed_values - set(draft["used_fact_values"]))
        raise Mix2KV4ContractError(
            "teacher used_fact_values에 명시 claim이 빠졌습니다: "
            + ",".join(missing)
        )
    return draft


def validate_review(spec: Mapping[str, Any], review: Any) -> dict[str, Any]:
    if not isinstance(review, dict) or set(review) != REVIEW_FIELDS:
        raise Mix2KV4ContractError("peer review field 집합이 다릅니다.")
    if (
        review.get("record_id") != spec["id"]
        or review.get("decision") not in {"PASS", "FAIL"}
        or not all(isinstance(review.get(key), list) for key in ("failure_codes", "fact_errors", "style_notes"))
        or not isinstance(review.get("rewrite_instructions"), str)
        or any(not isinstance(item, str) for key in ("failure_codes", "fact_errors", "style_notes") for item in review[key])
    ):
        raise Mix2KV4ContractError("peer review identity·형식이 다릅니다.")
    if review["decision"] == "PASS" and any(
        (review["failure_codes"], review["fact_errors"], review["rewrite_instructions"].strip())
    ):
        raise Mix2KV4ContractError("PASS review에 실패 정보가 남았습니다.")
    if review["decision"] == "FAIL" and not review["failure_codes"]:
        raise Mix2KV4ContractError("FAIL review에 실패 코드가 없습니다.")
    return review


def validate_specs(rows: Sequence[dict[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    for row in rows:
        validate_spec(row)
    axes = Counter(row["task_axis"] for row in rows)
    drafters = Counter(row["drafter"] for row in rows)
    templates = Counter(row["template_family"] for row in rows)
    runtime_snapshots = {
        row["runtime_binding"]["snapshot_sha256"]
        for row in rows
        if row["runtime_binding"] is not None
    }
    target_dates = {
        row["runtime_binding"]["value"]["period"]["hard_facts"]["period"][
            "target_date"
        ]
        for row in rows
        if row["runtime_binding"] is not None
    }
    if len(rows) != EXPECTED_ROWS or len({row["id"] for row in rows}) != EXPECTED_ROWS:
        raise Mix2KV4ContractError("spec은 고유 ID 2,000행이어야 합니다.")
    if dict(axes) != EXPECTED_AXES:
        raise Mix2KV4ContractError(f"axis 수량이 다릅니다: {dict(axes)}")
    if drafters != {"claude": 1_000, "codex": 1_000}:
        raise Mix2KV4ContractError(f"teacher 역할 수량이 다릅니다: {dict(drafters)}")
    maximum = int(config["diversity"]["template_family_rows_maximum"])
    if not templates or max(templates.values()) > maximum:
        raise Mix2KV4ContractError("template family가 최대 20행을 넘습니다.")
    if sum(row["multiturn"] for row in rows) < int(config["diversity"]["minimum_multiturn_rows"]):
        raise Mix2KV4ContractError("multiturn 행이 300건 미만입니다.")
    if len(runtime_snapshots) < int(
        config["diversity"]["minimum_unique_runtime_snapshots"]
    ):
        raise Mix2KV4ContractError("runtime snapshot이 600건 미만입니다.")
    if len(target_dates) < int(config["diversity"]["minimum_unique_target_dates"]):
        raise Mix2KV4ContractError("단일 일진 날짜가 300건 미만입니다.")
    return {
        "rows": len(rows),
        "axes": dict(sorted(axes.items())),
        "drafters": dict(sorted(drafters.items())),
        "templates": len(templates),
        "multiturn_rows": sum(row["multiturn"] for row in rows),
        "unique_runtime_snapshots": len(runtime_snapshots),
        "unique_target_dates": len(target_dates),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Mix2KV4ContractError(f"JSONL이 없거나 symlink입니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Mix2KV4ContractError(f"JSONL 빈 행: {number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Mix2KV4ContractError(f"JSONL object 오류: {number}")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Mix2KV4ContractError):
            raise
        raise Mix2KV4ContractError(f"JSONL을 읽지 못했습니다: {path}") from exc
    return rows


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
        for row in rows
    )


__all__ = [
    "DATASET_VERSION",
    "DAY_AXES",
    "DRAFT_FIELDS",
    "EXPECTED_AXES",
    "EXPECTED_ROWS",
    "MAX_COMPLETION_TOKENS",
    "MAX_LENGTH",
    "MAX_PROMPT_TOKENS",
    "MULTITURN_AXES",
    "PRIVATE_DIR_MODE",
    "PRIVATE_FILE_MODE",
    "RECORD_SCHEMA_VERSION",
    "REVIEW_FIELDS",
    "RUNTIME_AXES",
    "RUNTIME_BINDING_ID",
    "RUNTIME_BINDING_SCHEMA",
    "RUNTIME_RELEASE_ID",
    "SPEC_FIELDS",
    "SUBSTANTIVE_AXES",
    "Mix2KV4ContractError",
    "flatten_runtime_facts",
    "jsonl_bytes",
    "nonempty_lines",
    "normalize_answer",
    "read_jsonl",
    "required_fact_errors",
    "sentence_count",
    "sha256_bytes",
    "sha256_file",
    "structural_claim_errors",
    "validate_draft",
    "validate_review",
    "validate_runtime_binding",
    "validate_spec",
    "validate_specs",
]
