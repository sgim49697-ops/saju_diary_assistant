# phase6_scoring.py - Phase 6 봉인 평가의 재현 가능한 자동 기술 지표를 계산한다.

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.data.quality_v2_tools import ALLOWED_SAJU_HANJA
from scripts.training.phase5_quality_v2 import (
    CONTROL_PATTERN,
    FULL_DATE_PATTERN,
    GANZHI_PATTERN,
    HANDLE_PATTERN,
    LONG_NUMBER_PATTERN,
    SAJU_TERM_PATTERN,
    SEVERE_SAFETY_PATTERNS,
    SPECIAL_TOKEN_PATTERN,
    URL_PATTERN,
    Phase5QualityV2Error,
    build_typed_contract,
    score_shensha_contract,
    score_typed_contract,
)

AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
    "deterministic_saju_qa",
    "saju_diary_bridge",
)

EXPECTED_ROWS_BY_AXIS = {
    "nemotron_saju": 50,
    "bazi_sft": 200,
    "aihub_empathy_single": 50,
    "aihub_empathy_multiturn": 50,
    "yeji_shensha_derived": 50,
    "deterministic_saju_qa": 50,
    "saju_diary_bridge": 50,
}

KANA_PATTERN = re.compile(r"[\u3040-\u30ff]")
ENGLISH_SENTENCE_PATTERN = re.compile(r"(?:\b[A-Za-z]{2,}\b[\s,.;:!?-]*){4,}")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
THINK_TOKEN_PATTERN = re.compile(r"</?think>", re.IGNORECASE)
PILLAR_PATTERN = re.compile(
    r"([년월일시])(?:주|柱)\s*[:=]?\s*([甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])"
)
KOREAN_PILLAR_PATTERN = re.compile(
    r"[갑을병정무기경신임계]\s*[자축인묘진사오미신유술해]"
)
ELEMENT_COUNT_PATTERN = re.compile(r"(?:목|木)\s*[:=]?\s*(\d+)")
ELEMENT_PATTERNS = {
    "wood": re.compile(r"(?:목|木)\s*[:=]?\s*(\d+)"),
    "fire": re.compile(r"(?:화|火)\s*[:=]?\s*(\d+)"),
    "earth": re.compile(r"(?:토|土)\s*[:=]?\s*(\d+)"),
    "metal": re.compile(r"(?:금|金)\s*[:=]?\s*(\d+)"),
    "water": re.compile(r"(?:수|水)\s*[:=]?\s*(\d+)"),
}
CAUSAL_PATTERN = re.compile(
    r"(?:사주|명식|오행|일간).{0,30}(?:때문|이므로|따라서|결정|정해|필연|운명)"
)
NONCAUSAL_PATTERN = re.compile(r"단정할 수 없|참고|가능성|해석|경향|결정하지 않|뜻은 아닙")
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")


class Phase6ScoringError(RuntimeError):
    """Phase 6 scorer 입력·분모·축 계약이 다를 때 발생한다."""


def normalize_text(value: str) -> str:
    """비교에 영향을 주지 않는 유니코드·공백 차이만 정규화한다."""

    text = unicodedata.normalize("NFKC", value)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def prompt_text(messages: Sequence[Mapping[str, str]]) -> str:
    return "\n".join(str(message.get("content", "")) for message in messages)


def _introduced(pattern: re.Pattern[str], prompt: str, output: str) -> bool:
    return bool(set(map(str, pattern.findall(output))) - set(map(str, pattern.findall(prompt))))


def _foreign_sentence(text: str) -> bool:
    if KANA_PATTERN.search(text) or ENGLISH_SENTENCE_PATTERN.search(text):
        return True
    return any(character not in ALLOWED_SAJU_HANJA for character in CJK_PATTERN.findall(text))


def _pillar_map(text: str) -> dict[str, str]:
    labels = {"년": "year", "월": "month", "일": "day", "시": "hour"}
    return {labels[label]: value for label, value in PILLAR_PATTERN.findall(normalize_text(text))}


def _element_counts(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, pattern in ELEMENT_PATTERNS.items():
        match = pattern.search(normalize_text(text))
        if match is not None:
            result[key] = int(match.group(1))
    return result


def _input_fact_violation(prompt: str, output: str) -> bool:
    prompt_pairs = set(GANZHI_PATTERN.findall(prompt))
    output_pairs = set(GANZHI_PATTERN.findall(output))
    if output_pairs - prompt_pairs:
        return True
    prompt_pillars = _pillar_map(prompt)
    output_pillars = _pillar_map(output)
    return any(
        name in prompt_pillars and prompt_pillars[name] != value
        for name, value in output_pillars.items()
    )


def _fabricated_four_pillars(prompt: str, output: str) -> bool:
    prompt_pairs = set(GANZHI_PATTERN.findall(prompt))
    output_pairs = set(GANZHI_PATTERN.findall(output))
    introduced_chinese = output_pairs - prompt_pairs
    introduced_korean = set(KOREAN_PILLAR_PATTERN.findall(output)) - set(
        KOREAN_PILLAR_PATTERN.findall(prompt)
    )
    return len(introduced_chinese) >= 4 or len(introduced_korean) >= 4


def _repetition(output: str) -> bool:
    sentences = [
        re.sub(r"[^0-9a-z가-힣一-龥]", "", value.lower())
        for value in SENTENCE_SPLIT_PATTERN.split(output)
        if value.strip()
    ]
    sentences = [value for value in sentences if value]
    if len(sentences) != len(set(sentences)):
        return True
    words = re.findall(r"[0-9A-Za-z가-힣一-龥]+", normalize_text(output).lower())
    fourgrams = [tuple(words[index : index + 4]) for index in range(max(0, len(words) - 3))]
    return any(count >= 3 for count in Counter(fourgrams).values())


def _infer_deterministic_category(prompt: str) -> str:
    endings = {
        "네 기둥을 년주부터 시주 순서로 정확히 적어": "stem_branch_identity",
        "각 글자의 음양·오행과 표면 오행 수": "yin_yang_elements_and_surface_counts",
        "각 지지의 지장간을 정기 우선 순서": "hidden_stems",
        "각 천간의 십신": "stem_ten_gods",
        "지지 십신": "branch_ten_gods",
    }
    matches = [category for marker, category in endings.items() if marker in prompt]
    if len(matches) != 1:
        raise Phase6ScoringError("deterministic 문항 유형을 하나로 확정할 수 없습니다.")
    return matches[0]


def _deterministic_contract_pass(prompt: str, reference: str, output: str) -> bool:
    category = _infer_deterministic_category(prompt)
    try:
        contract = build_typed_contract(
            category="deterministic_hard_fact",
            legacy_contract={"qa_category": category},
            reference=reference,
            prompt=prompt,
        )
        return bool(score_typed_contract(contract, output)["passed"])
    except Phase5QualityV2Error as exc:
        raise Phase6ScoringError("deterministic 자동 계약을 구성하지 못했습니다.") from exc


def _rule_term(prompt: str) -> str:
    quoted = re.search(
        r"[‘'\"](?:이 명식에는 )?([가-힣A-Za-z0-9]+?)(?:가|이)?\s+성립",
        prompt,
    )
    if quoted is not None:
        return quoted.group(1)
    direct = re.search(r"(?:이 명식에서\s+)?([가-힣A-Za-z0-9]+)\s+(?:조건|의 전통)", prompt)
    if direct is not None:
        return direct.group(1)
    raise Phase6ScoringError("신살 규칙명을 prompt에서 확정할 수 없습니다.")


def _rule_contract_pass(prompt: str, reference: str, output: str) -> bool:
    term = _rule_term(prompt)
    if "의 전통 명리상 의미와 판단 조건" in prompt:
        contract = {
            "contract_type": "shensha_definition",
            "rule_term": term,
            "expected_outcome": None,
        }
    else:
        negative = "성립하지 않습니다" in reference
        positive = "성립합니다" in reference and not negative
        if negative == positive:
            raise Phase6ScoringError("신살 reference의 극성을 확정할 수 없습니다.")
        contract = {
            "contract_type": "shensha_polarity",
            "rule_term": term,
            "expected_outcome": positive,
        }
    try:
        return bool(score_shensha_contract(contract, output)["passed"])
    except Phase5QualityV2Error as exc:
        raise Phase6ScoringError("신살 자동 계약을 채점하지 못했습니다.") from exc


def _grounded_fact_contract_pass(prompt: str, output: str) -> bool | None:
    expected_pillars = _pillar_map(prompt)
    expected_elements = _element_counts(prompt)
    if not expected_pillars and not expected_elements:
        return None
    actual_pillars = _pillar_map(output)
    actual_elements = _element_counts(output)
    pillars_ok = not expected_pillars or all(
        actual_pillars.get(key) == value for key, value in expected_pillars.items()
    )
    elements_ok = not expected_elements or all(
        actual_elements.get(key) == value for key, value in expected_elements.items()
    )
    return pillars_ok and elements_ok


def score_generation(
    *,
    source_axis: str,
    prompt_messages: Sequence[Mapping[str, str]],
    reference_assistant: str,
    output: str,
    max_token_hit: bool,
) -> dict[str, Any]:
    """한 blind 출력을 reference 유사도 없이 자동 기술 계약으로 채점한다."""

    if source_axis not in AXES:
        raise Phase6ScoringError(f"알 수 없는 평가 축입니다: {source_axis}")
    if not isinstance(reference_assistant, str) or not reference_assistant.strip():
        raise Phase6ScoringError("봉인 reference가 비어 있습니다.")
    if not isinstance(output, str) or not isinstance(max_token_hit, bool):
        raise Phase6ScoringError("출력 또는 종료 정보 형식이 다릅니다.")
    prompt = prompt_text(prompt_messages)
    stripped = output.strip()
    special_or_control = bool(
        SPECIAL_TOKEN_PATTERN.search(output)
        or THINK_TOKEN_PATTERN.search(output)
        or CONTROL_PATTERN.search(output)
    )
    nonempty = bool(stripped)
    severe_safety = any(pattern.search(output) for pattern in SEVERE_SAFETY_PATTERNS)
    fact_applicable = bool(GANZHI_PATTERN.search(prompt) or _pillar_map(prompt))
    task_confusion: bool | None = None
    if source_axis in {"aihub_empathy_single", "aihub_empathy_multiturn"}:
        task_confusion = bool(SAJU_TERM_PATTERN.search(output) or GANZHI_PATTERN.search(output))
    deterministic_pass: bool | None = None
    if source_axis == "deterministic_saju_qa":
        deterministic_pass = _deterministic_contract_pass(prompt, reference_assistant, output)
    rule_pass: bool | None = None
    if source_axis == "yeji_shensha_derived":
        rule_pass = _rule_contract_pass(prompt, reference_assistant, output)
    noncausal: bool | None = None
    if source_axis in {"nemotron_saju", "saju_diary_bridge"}:
        noncausal = not bool(CAUSAL_PATTERN.search(output)) or bool(NONCAUSAL_PATTERN.search(output))
    return {
        "generation_clean": nonempty and not special_or_control and not max_token_hit,
        "nonempty": nonempty,
        "max_token_hit": max_token_hit,
        "special_or_control": special_or_control,
        "foreign_sentence": _foreign_sentence(output),
        "severe_safety": severe_safety,
        "sensitive_entity": any(
            _introduced(pattern, prompt, output)
            for pattern in (URL_PATTERN, HANDLE_PATTERN, LONG_NUMBER_PATTERN)
        ),
        "unsupported_full_date": _introduced(FULL_DATE_PATTERN, prompt, output),
        "input_fact_violation": _input_fact_violation(prompt, output)
        if fact_applicable
        else None,
        "fabricated_four_pillars": _fabricated_four_pillars(prompt, output),
        "task_confusion": task_confusion,
        "repetition": _repetition(output),
        "deterministic_contract_pass": deterministic_pass,
        "rule_contract_pass": rule_pass,
        "grounded_fact_contract_pass": _grounded_fact_contract_pass(prompt, output),
        "noncausal_contract_pass": noncausal,
        "structured_json_parse": None,
        "domain_semantics": "not_measured",
        "output_characters": len(stripped),
    }


def _round(value: float) -> float:
    return round(value, 9)


def _percent_metric(
    records: Sequence[Mapping[str, Any]], metric: str
) -> dict[str, Any]:
    components: dict[tuple[str, str], list[bool]] = defaultdict(list)
    record_count = 0
    for record in records:
        value = record["scoring"].get(metric)
        if value is None:
            continue
        if not isinstance(value, bool):
            raise Phase6ScoringError(f"{metric} 값은 bool 또는 null이어야 합니다.")
        components[(record["axis"], record["component_key"])].append(value)
        record_count += 1
    if not components:
        return {
            "status": "not_applicable",
            "records": 0,
            "components": 0,
            "axes": 0,
        }
    axis_values: dict[str, list[float]] = defaultdict(list)
    for (axis, _component), values in components.items():
        axis_values[axis].append(sum(values) / len(values))
    axis_percent = {
        axis: _round(sum(values) * 100 / len(values))
        for axis, values in sorted(axis_values.items())
    }
    macro = sum(axis_percent.values()) / len(axis_percent)
    return {
        "status": "measured",
        "records": record_count,
        "components": len(components),
        "axes": len(axis_percent),
        "axis_percent": axis_percent,
        "macro_percent": _round(macro),
    }


def _likelihood_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    components: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        likelihood = record.get("likelihood")
        if not isinstance(likelihood, Mapping):
            raise Phase6ScoringError("likelihood 결과가 없습니다.")
        tokens = likelihood.get("tokens")
        nll_sum = likelihood.get("nll_sum")
        correct = likelihood.get("correct")
        if (
            not isinstance(tokens, int)
            or isinstance(tokens, bool)
            or tokens <= 0
            or not isinstance(nll_sum, (int, float))
            or not math.isfinite(float(nll_sum))
            or not isinstance(correct, int)
            or isinstance(correct, bool)
            or not 0 <= correct <= tokens
        ):
            raise Phase6ScoringError("likelihood 수치가 올바르지 않습니다.")
        components[(record["axis"], record["component_key"])].append(likelihood)
    axis_case_nll: dict[str, list[float]] = defaultdict(list)
    axis_case_accuracy: dict[str, list[float]] = defaultdict(list)
    micro_nll = 0.0
    micro_correct = 0
    micro_tokens = 0
    for (axis, _component), values in components.items():
        axis_case_nll[axis].append(
            sum(float(value["nll_sum"]) / int(value["tokens"]) for value in values)
            / len(values)
        )
        axis_case_accuracy[axis].append(
            sum(int(value["correct"]) / int(value["tokens"]) for value in values)
            / len(values)
        )
        micro_nll += sum(float(value["nll_sum"]) for value in values)
        micro_correct += sum(int(value["correct"]) for value in values)
        micro_tokens += sum(int(value["tokens"]) for value in values)
    axis = {
        name: {
            "case_component_mean_nll": _round(sum(axis_case_nll[name]) / len(axis_case_nll[name])),
            "case_component_mean_token_accuracy": _round(
                sum(axis_case_accuracy[name]) / len(axis_case_accuracy[name])
            ),
        }
        for name in sorted(axis_case_nll)
    }
    return {
        "interpretation": "held_out_reference_fit_not_semantic_truth",
        "aggregation": "case_then_component_then_axis_macro",
        "axis": axis,
        "macro": {
            "nll": _round(
                sum(value["case_component_mean_nll"] for value in axis.values()) / len(axis)
            ),
            "token_accuracy": _round(
                sum(value["case_component_mean_token_accuracy"] for value in axis.values())
                / len(axis)
            ),
        },
        "token_weighted_diagnostic": {
            "nll": _round(micro_nll / micro_tokens),
            "token_accuracy": _round(micro_correct / micro_tokens),
            "tokens": micro_tokens,
        },
    }


def aggregate_model_records(
    records: Sequence[Mapping[str, Any]],
    *,
    expected_rows_by_axis: Mapping[str, int] = EXPECTED_ROWS_BY_AXIS,
    expected_components_per_axis: int = 50,
) -> dict[str, Any]:
    """행→component→axis 순서를 고정해 모델별 공개 집계를 만든다."""

    if len(records) != sum(expected_rows_by_axis.values()):
        raise Phase6ScoringError("봉인 평가 행 수가 계약과 다릅니다.")
    axis_counts = Counter(record.get("axis") for record in records)
    if dict(axis_counts) != dict(expected_rows_by_axis):
        raise Phase6ScoringError(f"축별 행 수가 계약과 다릅니다: {dict(axis_counts)}")
    identities = {record.get("case_key") for record in records}
    if len(identities) != len(records) or None in identities:
        raise Phase6ScoringError("case identity가 비었거나 중복됐습니다.")
    component_counts: Counter[str] = Counter()
    for axis in expected_rows_by_axis:
        components = {
            record.get("component_key") for record in records if record.get("axis") == axis
        }
        if None in components or len(components) != expected_components_per_axis:
            raise Phase6ScoringError(f"{axis} component 수가 계약과 다릅니다.")
        component_counts[axis] = len(components)
    metric_names = (
        "generation_clean",
        "nonempty",
        "max_token_hit",
        "special_or_control",
        "foreign_sentence",
        "severe_safety",
        "sensitive_entity",
        "unsupported_full_date",
        "input_fact_violation",
        "fabricated_four_pillars",
        "task_confusion",
        "repetition",
        "deterministic_contract_pass",
        "rule_contract_pass",
        "grounded_fact_contract_pass",
        "noncausal_contract_pass",
        "structured_json_parse",
    )
    metrics = {name: _percent_metric(records, name) for name in metric_names}
    zero_counts = {
        name: sum(bool(record["scoring"].get(name)) for record in records)
        for name in (
            "special_or_control",
            "severe_safety",
            "sensitive_entity",
            "unsupported_full_date",
            "fabricated_four_pillars",
        )
    }
    return {
        "rows": len(records),
        "components": sum(component_counts.values()),
        "rows_by_axis": dict(axis_counts),
        "components_by_axis": dict(component_counts),
        "aggregation": "record_then_leakage_component_then_axis_macro",
        "metrics": metrics,
        "zero_tolerance_counts": zero_counts,
        "likelihood": _likelihood_metrics(records),
        "domain_semantics": "not_measured",
    }


def model_gate(
    aggregate: Mapping[str, Any],
    *,
    handoff_percent: float,
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    """자동 기술 지표만으로 한 모델의 baseline 적격성을 판정한다."""

    metrics = aggregate["metrics"]

    def measured(name: str) -> float:
        value = metrics[name]
        if value.get("status") != "measured":
            raise Phase6ScoringError(f"필수 Gate 지표가 측정되지 않았습니다: {name}")
        return float(value["macro_percent"])

    gates = {
        "generation_clean": measured("generation_clean")
        >= float(thresholds["generation_clean_min_percent"]),
        "task_confusion": measured("task_confusion")
        <= float(thresholds["task_confusion_max_percent"]),
        "input_fact_violation": measured("input_fact_violation")
        <= float(thresholds["input_fact_violation_max_percent"]),
        "foreign_sentence": measured("foreign_sentence")
        <= float(thresholds["foreign_sentence_max_percent"]),
        "deterministic_contract": measured("deterministic_contract_pass")
        >= float(thresholds["deterministic_min_percent"]),
        "rule_contract": measured("rule_contract_pass")
        >= float(thresholds["rule_min_percent"]),
        "nonsealed_handoff": float(handoff_percent)
        >= float(thresholds["handoff_min_percent"]),
        "zero_tolerance": all(value == 0 for value in aggregate["zero_tolerance_counts"].values()),
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "passed": all(gates.values()),
        "gates": gates,
        "failed_gates": sorted(name for name, passed in gates.items() if not passed),
        "structured_json_parse": "not_applicable",
        "domain_semantics": "not_measured",
    }


def no_regression(
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    candidate_handoff_percent: float,
    baseline_handoff_percent: float,
    tolerance_percent_points: float,
) -> dict[str, Any]:
    """KI20이 KI10 대비 허용 범위를 벗어나지 않는지 판정한다."""

    higher = ("generation_clean", "deterministic_contract_pass", "rule_contract_pass")
    lower = ("task_confusion", "input_fact_violation", "foreign_sentence")
    checks: dict[str, bool] = {}
    deltas: dict[str, float] = {}
    for name in higher:
        current = float(candidate["metrics"][name]["macro_percent"])
        previous = float(baseline["metrics"][name]["macro_percent"])
        deltas[name] = _round(current - previous)
        checks[name] = current >= previous - tolerance_percent_points
    for name in lower:
        current = float(candidate["metrics"][name]["macro_percent"])
        previous = float(baseline["metrics"][name]["macro_percent"])
        deltas[name] = _round(current - previous)
        checks[name] = current <= previous + tolerance_percent_points
    deltas["nonsealed_handoff"] = _round(
        candidate_handoff_percent - baseline_handoff_percent
    )
    checks["nonsealed_handoff"] = (
        candidate_handoff_percent
        >= baseline_handoff_percent - tolerance_percent_points
    )
    checks["zero_tolerance"] = all(
        value == 0 for value in candidate["zero_tolerance_counts"].values()
    )
    return {
        "passed": all(checks.values()),
        "tolerance_percent_points": tolerance_percent_points,
        "checks": checks,
        "deltas_percent_points": deltas,
        "failed_checks": sorted(name for name, passed in checks.items() if not passed),
    }


def select_baseline(
    *,
    ki10_gate: Mapping[str, Any],
    ki20_gate: Mapping[str, Any],
    ki20_no_regression: Mapping[str, Any],
) -> str:
    if bool(ki20_gate.get("passed")) and bool(ki20_no_regression.get("passed")):
        return "KI20_TECHNICAL_BASELINE_SELECTED"
    if bool(ki10_gate.get("passed")):
        return "KI10_TECHNICAL_BASELINE_RETAINED"
    return "AUTOMATED_REPAIR_REQUIRED"
