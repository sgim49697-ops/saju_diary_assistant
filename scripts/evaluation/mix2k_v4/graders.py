# graders.py - dev 응답의 구조 사실·누락·후속 일관성·회귀 blocker를 채점한다.

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from scripts.data.mix2k_v4_contracts import (
    GANYI,
    nonempty_lines,
    parallel_pillar_field_claims,
    pillar_position_detail_claims,
    sentence_count,
    structural_claim_errors,
    surface_element_claims,
)

from .contracts import REGRESSION_ID, spec_for_structural_validator

CONTROL_PATTERN = re.compile(r"<\|[^>]+\|>|</?think>", re.IGNORECASE)
REASK_PATTERNS = tuple(
    re.compile(value)
    for value in (
        r"생년월일.{0,18}(?:알려|입력|필요|말씀)",
        r"출생(?:시간|시각|정보|지).{0,18}(?:알려|입력|필요|말씀)",
        r"태어난\s*(?:날짜|시간|곳).{0,18}(?:알려|입력|필요|말씀)",
        r"사주(?:정보|명식).{0,18}(?:알려|입력|필요|말씀)",
        r"알려주시면.{0,24}(?:사주|원국|분석|봐드)",
    )
)
REASK_NEGATION = re.compile(
    r"(?:다시\s*)?(?:묻|요청|입력|제공|알려)[^\n.!?]{0,12}(?:않|마|필요\s*없)|"
    r"(?:재입력|다시\s*입력)[^\n.!?]{0,8}(?:불필요|필요\s*없)"
)
UNAMBIGUOUS_SAJU_INJECTION = re.compile(
    r"사주|원국|명식|팔자|년주|월주|시주|천간|지지|지장간|"
    r"십신|비견|겁재|식신|편재|정재|편관|정관|편인|정인|신강|신약|"
    r"격국|용신|대운|세운|합충|오행|일진|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]"
)
SCHEMA_CLAIM_PATTERNS = tuple(
    re.compile(value, re.IGNORECASE)
    for value in (
        rf"(?:연주|년주|월주|일주|시주)[^\n.!?]{{0,24}}{GANYI.pattern}",
        rf"(?:연간지|월간지|일진|일간지|해의\s*간지|달의\s*간지|날의\s*간지)[^\n.!?]{{0,24}}{GANYI.pattern}",
        r"일간[^\n.!?]{0,18}[甲乙丙丁戊己庚辛壬癸목화토금수]",
        r"(?:천간|지지|지장간|hidden\s*stems?|stem[_ -]?ten[_ -]?god|branch[_ -]?ten[_ -]?god)[^\n.!?]{0,30}",
        r"[목화토금수](?:은|는|이|가|:|=|\s)*[0-9]+\s*개",
    )
)
LABEL_ERROR_MARKERS = (
    "_confusion:",
    "natal_day_called_full_chart",
    "period_year_called_day_ganzhi",
    "period_day_called_seun",
)


def _question(case: Mapping[str, Any], turn_index: int) -> str:
    if turn_index == 0:
        return str(case["messages"][-1]["content"])
    return str(case["followup_turns"][turn_index - 1])


def _contains_labeled_value(text: str, labels: Sequence[str], value: str) -> bool:
    for label in labels:
        if re.search(
            rf"{re.escape(label)}[^\n.!?]{{0,28}}{re.escape(value)}|"
            rf"{re.escape(value)}[^\n.!?]{{0,20}}{re.escape(label)}",
            text,
        ):
            return True
    return False


def _contains_day_master(text: str, expected: str) -> bool:
    for match in re.finditer(
        r"일간(?:의\s*천간)?(?:은|는|이|가|:|=|\s){0,8}"
        r"([甲乙丙丁戊己庚辛壬癸])",
        text,
    ):
        if match.group(1) == expected:
            return True
    return False


def _runtime_details(case: Mapping[str, Any]) -> dict[str, Any]:
    binding = case.get("runtime_binding")
    if not isinstance(binding, Mapping):
        return {}
    return dict(binding["value"]["chart"]["hard_facts"])


def _pillar_blocks(output: str) -> dict[str, str]:
    labels = {
        "연주": "year",
        "년주": "year",
        "월주": "month",
        "일주": "day",
        "시주": "hour",
    }
    pattern = re.compile("|".join(sorted(labels, key=len, reverse=True)))
    matches = list(pattern.finditer(output))
    blocks: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(output)
        block = output[match.end() : end]
        for owner_boundary in re.finditer(
            r"[\n.!?。！？;；]|(?:과|와)\s*달리|일간(?!지)|연간지|월간지|일진|일간지|날의\s*간지",
            block,
        ):
            if owner_boundary.group(0) == "일간" and re.search(
                r"(?:천간(?:의)?\s*십신|stem[_ -]?ten[_ -]?god)"
                r"[^\n.!?。！？;；]{0,16}$",
                block[: owner_boundary.start()],
                re.IGNORECASE,
            ):
                continue
            block = block[: owner_boundary.start()]
            break
        blocks.setdefault(labels[match.group(0)], []).append(block[:240])
    return {name: "\n".join(values) for name, values in blocks.items()}


def _field_value_present(block: str, field: str, value: str) -> bool:
    aliases = {
        "stem": r"천간|stem",
        "branch": r"지지|branch",
        "stem_ten_god": r"천간(?:의)?\s*십신|stem[_ -]?ten[_ -]?god",
        "branch_ten_god": r"지지(?:의)?\s*십신|branch[_ -]?ten[_ -]?god",
        "hidden_stems": r"지장간|hidden\s*stems?",
    }[field]
    return (
        re.search(
            rf"(?:{aliases})[^\n.!?]{{0,32}}{re.escape(value)}|"
            rf"{re.escape(value)}[^\n.!?]{{0,20}}(?:{aliases})",
            block,
            re.IGNORECASE,
        )
        is not None
    )


def _pillar_detail_present(
    block: str, *, position: str, detail: str, entity: str, value: str
) -> bool:
    if (entity, detail, value) in pillar_position_detail_claims(block, position):
        return True
    korean_position = "천간" if position == "stem" else "지지"
    korean_detail = "오행" if detail == "element" else "음양"
    separators = r"(?:은|는|이|가|의|에서|에는|:|=|\(|\)|\s|[-·,/])*"
    patterns = (
        (
            rf"(?:{korean_position}|{position}){separators}{re.escape(entity)}"
            rf"[^\n.!?,，;；]{{0,16}}?(?:{korean_detail}|{position}[_ -]?{detail})"
            rf"{separators}{re.escape(value)}"
        ),
        (
            rf"(?:{korean_position}|{position}){separators}{re.escape(entity)}\s*"
            rf"[\(\[]\s*[목화토금수]\s*[·,/\s]+\s*(?:음|양)\s*[\)\]]"
        ),
        (
            rf"(?:{korean_position}|{position})(?:의)?\s*{korean_detail}"
            rf"{separators}{re.escape(value)}"
        ),
    )
    for pattern in patterns:
        match = re.search(pattern, block, re.IGNORECASE)
        if match is None:
            continue
        if detail == "element" and value in match.group(0):
            return True
        if detail == "yin_yang" and value in match.group(0):
            return True
    return False


def _day_master_detail_present(
    output: str, field: str, expected: str, stem: str
) -> bool:
    parts = re.split(r"[\n.!?。！？]+", output)
    segments: list[str] = []
    for index, value in enumerate(parts):
        if "일간" not in value or stem not in value:
            continue
        segments.append(value)
        if index + 1 < len(parts) and stem in parts[index + 1]:
            segments.append(f"{value} {parts[index + 1]}")
    for segment in segments:
        if field == "element":
            explicit = re.search(r"오행(?:은|는|이|가|:|=|\s)*([목화토금수])", segment)
            hanja = {"목": "木", "화": "火", "토": "土", "금": "金", "수": "水"}[
                expected
            ]
            if (
                (explicit is not None and explicit.group(1) == expected)
                or f"{stem}{hanja}" in segment
                or re.search(
                    rf"(?:음|양)의\s*{re.escape(expected)}"
                    rf"(?:\s|기운|오행|[,)]|$)",
                    segment,
                )
            ):
                return True
        elif field == "yin_yang":
            explicit = re.search(r"음양(?:은|는|이|가|:|=|\s)*(음|양)", segment)
            if (explicit is not None and explicit.group(1) == expected) or re.search(
                rf"(?<![가-힣]){re.escape(expected)}"
                rf"(?:의|인|이고|이며|입니다|이다|\s|[,)]|$)",
                segment,
            ):
                return True
        else:
            raise ValueError(f"지원하지 않는 일간 detail field입니다: {field}")
    return False


def _false_saju_injection(output: str) -> bool:
    if UNAMBIGUOUS_SAJU_INJECTION.search(output):
        return True
    if re.search(
        r"(?:연주|일주)[^\n.!?]{0,16}"
        r"(?:기둥|간지|천간|지지|[甲乙丙丁戊己庚辛壬癸])",
        output,
    ):
        return True
    if re.search(r"일간[^\n.!?]{0,16}(?:오행|음양|[甲乙丙丁戊己庚辛壬癸])", output):
        return True
    return (
        re.search(
            r"(?:사주|원국|십신|일간|오행)[^\n.!?]{0,20}상관|"
            r"상관[^\n.!?]{0,20}(?:사주|원국|십신|일간|오행|격)",
            output,
        )
        is not None
    )


def required_fact_omissions(
    case: Mapping[str, Any], output: str, turn_index: int
) -> list[str]:
    expected = case.get("expected_structural_facts")
    if not isinstance(expected, Mapping):
        return []
    question = _question(case, turn_index)
    pillars = expected["natal_pillars"]
    omissions: list[str] = []

    def require_value(label: str, value: str) -> None:
        if value not in output:
            omissions.append(label)

    positional_detail_requested = any(
        marker in question
        for marker in (
            "천간과 지지",
            "천간·지지",
            "stem ten-god",
            "branch ten-god",
            "십신",
        )
    )
    all_pillars_requested = any(
        marker in question
        for marker in (
            "네 기둥",
            "원국 전체",
            "연주·월주·일주·시주",
            "연주부터 시주",
        )
    ) or ("각 기둥" in question and not positional_detail_requested)
    if all_pillars_requested:
        for name in ("year", "month", "day", "hour"):
            require_value(f"natal_pillars.{name}", pillars[name])
    elif case["axis"] in {
        "natal_explanation",
        "natal_and_today",
        "followup",
    } and not any(value in output for value in pillars.values()):
        omissions.append("natal_pillars.any")
    if "일주" in question:
        require_value("natal_pillars.day", pillars["day"])
    if "일간" in question:
        require_value("day_master", expected["day_master"])

    period_labels = {
        "연간지": "period_year_ganzhi",
        "월간지": "period_month_ganzhi",
        "일진": "period_day_ganzhi",
    }
    for marker, field in period_labels.items():
        if marker in question:
            require_value(field, expected[field])
    if any(
        marker in question
        for marker in ("선택 날짜 세 간지", "year/month/day ganzhi", "연·월·일 간지")
    ):
        for field in (
            "period_year_ganzhi",
            "period_month_ganzhi",
            "period_day_ganzhi",
        ):
            require_value(field, expected[field])
    if case["axis"] in {"natal_and_today", "followup"}:
        require_value("period_day_ganzhi", expected["period_day_ganzhi"])

    details = _runtime_details(case)
    day_master = details.get("day_master", {})
    expected_day_master = str(expected["day_master"])
    if "일간" in question and "오행" in question:
        element = day_master.get("element", day_master.get("five_element"))
        if isinstance(element, str) and not _day_master_detail_present(
            output, "element", element, expected_day_master
        ):
            omissions.append("day_master.element")
    if "일간" in question and "음양" in question:
        yin_yang = day_master.get("yin_yang")
        if isinstance(yin_yang, str) and not _day_master_detail_present(
            output, "yin_yang", yin_yang, expected_day_master
        ):
            omissions.append("day_master.yin_yang")
    pillar_details = details.get("pillars", {})
    blocks = _pillar_blocks(output)
    parallel = set(parallel_pillar_field_claims(output))
    if "각 기둥" in question and ("천간과 지지" in question or "천간·지지" in question):
        for name in ("year", "month", "day", "hour"):
            block = blocks.get(name, "")
            for field in ("stem", "branch"):
                value = pillar_details.get(name, {}).get(field)
                if (
                    isinstance(value, str)
                    and not _field_value_present(block, field, value)
                    and (name, field, value) not in parallel
                ):
                    omissions.append(f"natal_pillars.{name}.{field}")
            if "오행" in question and "음양" in question:
                for position in ("stem", "branch"):
                    entity = pillar_details.get(name, {}).get(position)
                    for detail in ("element", "yin_yang"):
                        value = pillar_details.get(name, {}).get(f"{position}_{detail}")
                        if (
                            isinstance(entity, str)
                            and isinstance(value, str)
                            and not _pillar_detail_present(
                                block,
                                position=position,
                                detail=detail,
                                entity=entity,
                                value=value,
                            )
                        ):
                            omissions.append(
                                f"natal_pillars.{name}.{position}_{detail}"
                            )
    if "각 기둥" in question and "ten-god" in question:
        for name in ("year", "month", "day", "hour"):
            block = blocks.get(name, "")
            for field in ("stem_ten_god", "branch_ten_god"):
                value = pillar_details.get(name, {}).get(field)
                if (
                    isinstance(value, str)
                    and not _field_value_present(block, field, value)
                    and (name, field, value) not in parallel
                ):
                    omissions.append(f"natal_pillars.{name}.{field}")
    if "일주" in question and "지장간" in question:
        day = pillar_details.get("day", {})
        block = blocks.get("day", "")
        for field in ("stem", "branch"):
            value = day.get(field)
            if isinstance(value, str) and not _field_value_present(block, field, value):
                omissions.append(f"natal_pillars.day.{field}")
        for value in day.get("hidden_stems", []):
            if not _field_value_present(block, "hidden_stems", value):
                omissions.append(f"natal_pillars.day.hidden_stems.{value}")
    if "표면 오행" in question and "누락 없이" in question:
        counts = details.get("surface_five_elements", {})
        observed_counts = dict(surface_element_claims(output))
        for element, count in counts.items():
            if observed_counts.get(str(element)) != int(count):
                omissions.append(f"surface_five_elements.{element}")
    return list(dict.fromkeys(omissions))


def _schema_claim_counts(output: str, errors: Sequence[str]) -> tuple[int, int]:
    spans: set[tuple[int, int]] = set()
    for pattern in SCHEMA_CLAIM_PATTERNS:
        spans.update(match.span() for match in pattern.finditer(output))
    incorrect = sum(
        any(marker in error for marker in LABEL_ERROR_MARKERS) for error in errors
    )
    return max(len(spans), incorrect), incorrect


def _reasked_bound_input(output: str, *, runtime_bound: bool) -> bool:
    if not runtime_bound:
        return False
    for sentence in re.split(r"[\n.!?。！？]+", output):
        if REASK_NEGATION.search(sentence):
            continue
        if any(pattern.search(sentence) for pattern in REASK_PATTERNS):
            return True
    return False


def repeated_ngram_ratio(output: str, n: int = 6) -> float:
    tokens = re.findall(r"[0-9A-Za-z가-힣甲-龥]+|[^\s]", output.casefold())
    if len(tokens) < n:
        return 0.0
    ngrams = [tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]
    counts = Counter(ngrams)
    repeated = sum(count for count in counts.values() if count > 1)
    return repeated / len(ngrams)


def _regression_checks(
    case: Mapping[str, Any], output: str, turn_index: int, base_pass: bool
) -> dict[str, bool] | None:
    if case.get("case_id") != REGRESSION_ID:
        return None
    expected = case["expected_structural_facts"]
    pillars = expected["natal_pillars"]
    if turn_index == 0:
        checks = {
            "natal_year_labeled": _contains_labeled_value(
                output, ("연주", "년주"), pillars["year"]
            ),
            "natal_month_labeled": _contains_labeled_value(
                output, ("월주",), pillars["month"]
            ),
            "natal_day_labeled": _contains_labeled_value(
                output, ("일주",), pillars["day"]
            ),
            "natal_hour_labeled": _contains_labeled_value(
                output, ("시주",), pillars["hour"]
            ),
            "day_master_labeled": _contains_day_master(output, expected["day_master"]),
            "period_year_labeled": _contains_labeled_value(
                output, ("연간지", "해의 간지"), expected["period_year_ganzhi"]
            ),
            "period_month_labeled": _contains_labeled_value(
                output, ("월간지", "달의 간지"), expected["period_month_ganzhi"]
            ),
            "period_day_labeled": _contains_labeled_value(
                output, ("일진", "일간지", "날의 간지"), expected["period_day_ganzhi"]
            ),
        }
    else:
        checks = {
            "followup_natal_evidence": any(
                value in output for value in pillars.values()
            ),
            "followup_period_day_evidence": expected["period_day_ganzhi"] in output,
        }
    checks["turn_contract_pass"] = base_pass
    return checks


def grade_turn(
    case: Mapping[str, Any],
    output: str,
    *,
    turn_index: int,
    prior_outputs: Sequence[str] = (),
    max_token_hit: bool = False,
    input_over_budget: bool = False,
    ngram_size: int = 6,
    repetition_threshold: float = 0.35,
) -> dict[str, Any]:
    spec = spec_for_structural_validator(case)
    errors = structural_claim_errors(spec, output)
    omissions = required_fact_omissions(case, output, turn_index)
    lines = len(nonempty_lines(output))
    sentences = sentence_count(output)
    nonempty = bool(CONTROL_PATTERN.sub("", output).strip())
    minimum_length_pass = lines >= int(
        case["minimum_substantive_nonempty_lines"]
    ) and sentences >= int(case["minimum_substantive_sentences"])
    reasked = _reasked_bound_input(
        output, runtime_bound=case.get("runtime_binding") is not None
    )
    unsupported_errors = [
        error
        for error in errors
        if error.startswith(("unprovided_", "unsupported_structural_claim:"))
    ]
    label_errors = [
        error
        for error in errors
        if any(marker in error for marker in LABEL_ERROR_MARKERS)
    ]
    schema_claims, schema_errors = _schema_claim_counts(output, errors)
    repetition_ratio = repeated_ngram_ratio(output, n=ngram_size)
    false_saju = bool(
        case["axis"] == "general_empathy" and _false_saju_injection(output)
    )
    followup_consistent: bool | None = None
    if turn_index > 0:
        expected = case.get("expected_structural_facts")
        prior_valid = all(
            not structural_claim_errors(spec, prior)
            and not required_fact_omissions(case, prior, prior_index)
            for prior_index, prior in enumerate(prior_outputs)
        )
        followup_consistent = bool(
            isinstance(expected, Mapping)
            and expected["period_day_ganzhi"] in output
            and any(value in output for value in expected["natal_pillars"].values())
            and not errors
            and not omissions
            and not reasked
            and prior_outputs
            and prior_valid
        )
    base_pass = bool(
        nonempty
        and minimum_length_pass
        and not max_token_hit
        and not input_over_budget
        and not errors
        and not omissions
        and not reasked
        and not false_saju
    )
    regression_checks = _regression_checks(case, output, turn_index, base_pass)
    regression_pass = (
        None if regression_checks is None else all(regression_checks.values())
    )
    general_retention = (
        bool(base_pass and not false_saju)
        if case["axis"] == "general_empathy"
        else None
    )
    return {
        "nonempty": nonempty,
        "nonempty_lines": lines,
        "sentences": sentences,
        "minimum_length_pass": minimum_length_pass,
        "max_token_hit": bool(max_token_hit),
        "input_over_budget": bool(input_over_budget),
        "structural_claim_errors": errors,
        "natal_period_label_confusion": bool(label_errors),
        "unsupported_fact": bool(unsupported_errors),
        "provided_fact_omissions": omissions,
        "schema_claims": schema_claims,
        "schema_claim_errors": schema_errors,
        "reasked_bound_input": reasked,
        "false_saju_injection": false_saju,
        "repeated_ngram_ratio": round(repetition_ratio, 6),
        "within_response_repetitive": repetition_ratio > repetition_threshold,
        "followup_evidence_consistent": followup_consistent,
        "general_conversation_retention_deterministic": general_retention,
        "regression_checks": regression_checks,
        "regression_turn_pass": regression_pass,
        "deterministic_turn_pass": base_pass,
    }


__all__ = [
    "grade_turn",
    "repeated_ngram_ratio",
    "required_fact_omissions",
]
