# graders.py - FSM 요청 의도를 반영해 기존 응답을 결정론적으로 재채점한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from scripts.evaluation.grounded_dialogue.graders import (
    ASK_MARKER_PATTERN,
    CONTROL_PATTERN,
    FIELD_ALIASES,
    REQUEST_NEGATION_PATTERN,
    SENTENCE_SPLIT_PATTERN,
    fabricated_pillars,
    fact_contradictions,
    false_completion,
    severe_safety,
    unknown_hour_violations,
)

LEAP_MONTH_INTENT = re.compile(r"윤달|평달|윤월")
CALENDAR_CHOICE_INTENT = re.compile(
    r"(?:양력.{0,12}음력|음력.{0,12}양력|달력\s*(?:구분|종류)|"
    r"(?:양력|음력)\s*(?:인지|여부를?)(?!\s*(?:평달|윤달|윤월)))"
)
TIME_RANGE_INTENT = re.compile(r"시간\s*범위|몇\s*시부터|몇\s*시\s*사이|시작.{0,8}끝")
TIME_PRECISION_INTENT = re.compile(
    r"정확(?:한|히)\s*(?:출생\s*)?(?:시각|시간)|범위만|시간\s*미상|"
    r"모르는지|정확히\s*아는지"
)


def _requested_fields(sentence: str, decision_action: str | None) -> set[str]:
    requested: set[str] = set()
    leap_intent = LEAP_MONTH_INTENT.search(sentence) is not None
    if leap_intent:
        requested.add("leap_month")
    if CALENDAR_CHOICE_INTENT.search(sentence) and (
        not leap_intent or ("양력" in sentence and "음력" in sentence)
    ):
        requested.add("calendar")

    range_intent = TIME_RANGE_INTENT.search(sentence) is not None
    precision_intent = TIME_PRECISION_INTENT.search(sentence) is not None
    birth_time_alias = any(alias in sentence for alias in FIELD_ALIASES["birth_time"])
    if decision_action == "ask_time_precision" and (precision_intent or birth_time_alias):
        requested.add("time_precision")
    elif decision_action == "ask_exact_time_or_range" and (
        precision_intent or range_intent or birth_time_alias
    ):
        requested.add("time_range" if range_intent else "birth_time")
    else:
        if range_intent:
            requested.add("time_range")
        if precision_intent:
            requested.add("time_precision")
        if birth_time_alias and not (range_intent or precision_intent):
            requested.add("birth_time")

    for field in ("birth_date", "birthplace"):
        if field == "birth_date" and leap_intent and re.search(
            r"생일\s*의\s*(?:윤달|평달|윤월)", sentence
        ):
            continue
        if any(alias in sentence for alias in FIELD_ALIASES[field]):
            requested.add(field)
    if (
        not leap_intent
        and "calendar" not in requested
        and decision_action == "ask_calendar"
        and any(alias in sentence for alias in FIELD_ALIASES["calendar"])
    ):
        requested.add("calendar")
    return requested


def provided_field_reask(
    text: str,
    session_state: Mapping[str, Any],
    *,
    decision_action: str | None,
) -> list[str]:
    """현재 FSM 요청과 구분되는 확정 슬롯 재요청만 반환한다."""

    confirmed = set(session_state.get("confirmed_fields", []))
    if "birth_time" in session_state.get("explicit_unknown_fields", []):
        confirmed.update({"birth_time", "time_precision"})
    asked: set[str] = set()
    for sentence in SENTENCE_SPLIT_PATTERN.split(text or ""):
        if not ASK_MARKER_PATTERN.search(sentence) or REQUEST_NEGATION_PATTERN.search(sentence):
            continue
        asked.update(_requested_fields(sentence, decision_action) & confirmed)
    return sorted(asked)


def grade_response(
    text: str,
    *,
    hard_facts: Mapping[str, Any] | None,
    tool_status: str,
    session_state: Mapping[str, Any],
    decision_action: str | None,
    max_token_hit: bool,
) -> dict[str, Any]:
    fabricated = fabricated_pillars(text, hard_facts)
    unknown_hour = unknown_hour_violations(text, hard_facts)
    contradictions = fact_contradictions(text, hard_facts)
    reasks = provided_field_reask(
        text,
        session_state,
        decision_action=decision_action,
    )
    safety = severe_safety(text)
    return {
        "nonempty": bool(CONTROL_PATTERN.sub("", text or "").strip()),
        "max_token_hit": bool(max_token_hit),
        "fabricated_pillars": fabricated,
        "unknown_hour_violations": unknown_hour,
        "fact_contradictions": contradictions,
        "false_completion": false_completion(text, tool_status),
        "provided_field_reasks": reasks,
        "severe_safety": safety,
    }


__all__ = ["grade_response", "provided_field_reask"]
