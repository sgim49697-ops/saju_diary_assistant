# cases.py - 고정 100건 suite를 검증하고 case ID에서 독립 슬롯 oracle을 재구성한다.

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.intake_fsm import advance_intake, empty_intake_state

from .contracts import PRIVATE_FILE_MODE, read_jsonl, safe_path, sha256_file
from .errors import ArtifactError, GroundedDialogueError

CASE_ID_PATTERN = re.compile(r"^stateful-gate-dev-v1-(\d{2})-(\d{2})$")
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
CITIES = ("서울", "부산", "대전", "광주", "인천", "수원", "춘천", "전주", "대구", "제주")
TIMES = (
    "00:20",
    "02:10",
    "05:40",
    "08:15",
    "10:50",
    "13:05",
    "15:30",
    "18:45",
    "21:10",
    "23:35",
)
DIAGNOSTIC_RUNTIME_STATUS = {
    "runtime_release_ready": True,
    "feature_enabled": True,
    "production_id_key_ready": True,
    "fsm_gate_passed": True,
    "encrypted_persistence_ready": True,
    "retention_policy_ready": True,
}
_ORACLE_SIGNER = RuntimeIdSigner.for_test(
    hashlib.sha256(b"grounded-dialogue-oracle-v1").digest()
)


@dataclass(frozen=True)
class ExtractionResult:
    """한 사용자 발화에서 명시된 값만 담는 추출 결과다."""

    updates: Mapping[str, Any] = field(default_factory=dict)
    explicit_unknown_fields: tuple[str, ...] = ()
    valid: bool = True
    error_code: str | None = None
    raw_output: str | None = None


def _case_identity(case: Mapping[str, Any]) -> tuple[str, int]:
    case_id = case.get("case_id")
    match = CASE_ID_PATTERN.fullmatch(str(case_id))
    if match is None:
        raise GroundedDialogueError("고정 suite case_id 형식이 다릅니다.")
    stratum_index, item_index = (int(value) for value in match.groups())
    if stratum_index >= len(STRATA) or item_index >= 10:
        raise GroundedDialogueError("고정 suite case_id 범위가 다릅니다.")
    stratum = STRATA[stratum_index]
    if case.get("stratum") != stratum:
        raise GroundedDialogueError("case_id와 stratum이 다릅니다.")
    return stratum, item_index


def load_cases(config: Mapping[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    source = config["source_suite"]
    path = safe_path(repo_root, source["path"])
    if (
        path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != source["sha256"]
        or stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE
    ):
        raise ArtifactError("고정 비봉인 suite의 hash·mode가 다릅니다.")
    rows = read_jsonl(path, "grounded dialogue source suite")
    if len(rows) != source["rows"]:
        raise GroundedDialogueError("고정 suite row 수가 다릅니다.")
    ids: list[str] = []
    counts: Counter[str] = Counter()
    user_turns = 0
    for case in rows:
        stratum, _ = _case_identity(case)
        provenance = case.get("provenance")
        if provenance != {
            "kind": "public_synthetic",
            "contains_restricted_source": False,
            "contains_personal_data": False,
            "training_eligible": False,
        }:
            raise GroundedDialogueError("suite provenance가 공개 합성 계약과 다릅니다.")
        messages = case.get("messages")
        if (
            not isinstance(messages, list)
            or not messages
            or messages[-1].get("role") != "user"
            or any(
                not isinstance(message, Mapping)
                or set(message) != {"role", "content"}
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
                or not message["content"].strip()
                for message in messages
            )
        ):
            raise GroundedDialogueError("suite message 구조가 다릅니다.")
        if not isinstance(case.get("contract"), Mapping):
            raise GroundedDialogueError("suite case contract가 없습니다.")
        ids.append(str(case["case_id"]))
        counts[stratum] += 1
        user_turns += sum(message["role"] == "user" for message in messages)
    if len(ids) != len(set(ids)) or counts != Counter({name: 10 for name in STRATA}):
        raise GroundedDialogueError("suite identity·stratum 분포가 다릅니다.")
    if user_turns != source["user_turns"]:
        raise GroundedDialogueError("suite 사용자 turn 수가 다릅니다.")
    return rows


def _birthplace(index: int) -> dict[str, Any]:
    return {
        "country_code": "KR",
        "city": CITIES[index],
        "timezone": "Asia/Seoul",
        "longitude": None,
        "latitude": None,
    }


def _calendar_updates(index: int) -> dict[str, Any]:
    if index % 2 == 0:
        return {"calendar": "solar"}
    return {"calendar": "lunar", "leap_month": index % 4 != 1}


def _complete_updates(index: int, *, year: int, day: int) -> dict[str, Any]:
    return {
        "birth_date": f"{year:04d}-{index + 1:02d}-{day:02d}",
        **_calendar_updates(index),
        "birth_time": TIMES[index],
        "birthplace": _birthplace(index),
    }


def oracle_extractions(case: Mapping[str, Any]) -> list[ExtractionResult]:
    """원문 파서와 독립적으로 case ID·고정 템플릿 인덱스에서 Gold를 만든다."""

    stratum, index = _case_identity(case)
    empty = ExtractionResult()
    if stratum == "no_birth_information":
        values = [empty]
    elif stratum == "date_only_no_time":
        values = [
            ExtractionResult(
                {"birth_date": f"{1980 + index:04d}-{index + 1:02d}-{index + 2:02d}"}
            )
        ]
    elif stratum == "ambiguous_time":
        updates = {
            "birth_date": f"{1990 + index:04d}-{index + 1:02d}-{index + 3:02d}",
            **_calendar_updates(index),
            "birthplace": _birthplace(index),
        }
        if index == 2:
            updates["time_range"] = {"start": "07:00", "end": "09:00"}
        elif index == 8:
            updates["time_range"] = {"start": "18:00", "end": "20:00"}
        values = [ExtractionResult(updates)]
    elif stratum == "calendar_ambiguity":
        updates = {
            "birth_date": f"{1985 + index:04d}-{index + 1:02d}-{index + 4:02d}",
            "birth_time": TIMES[index],
            "birthplace": _birthplace(index),
        }
        if index % 2 == 1:
            updates["calendar"] = "lunar"
        values = [ExtractionResult(updates)]
    elif stratum == "timezone_location_ambiguity":
        values = [
            ExtractionResult(
                {
                    "birth_date": f"{1975 + index:04d}-{index + 1:02d}-{index + 5:02d}",
                    **_calendar_updates(index),
                    "birth_time": TIMES[index],
                }
            )
        ]
    elif stratum == "accumulated_context_no_reask":
        initial_date = f"{1980 + index:04d}-{index + 1:02d}-{index + 6:02d}"
        final_updates = {**_calendar_updates(index), "birth_time": TIMES[index]}
        if 3 <= index < 6:
            final_updates["birth_date"] = (
                f"{1980 + index:04d}-{index + 1:02d}-{index + 7:02d}"
            )
        values = [
            ExtractionResult(
                {"birth_date": initial_date, "birthplace": _birthplace(index)}
            ),
            ExtractionResult(final_updates),
        ]
    elif stratum == "time_unknown_partial_limit":
        values = [
            ExtractionResult(
                {
                    "birth_date": f"{1995 + index:04d}-{index + 1:02d}-{index + 7:02d}",
                    **_calendar_updates(index),
                    "birthplace": _birthplace(index),
                },
                explicit_unknown_fields=("birth_time",),
            )
        ]
    elif stratum == "complete_input_runtime_handoff":
        values = [
            ExtractionResult(
                _complete_updates(index, year=1965 + index, day=index + 8)
            )
        ]
    elif stratum == "false_ui_or_completion":
        values = [
            ExtractionResult(
                _complete_updates(index, year=1970 + index, day=index + 9)
            ),
            empty,
        ]
    elif stratum == "structured_chart_ready":
        values = [empty]
    else:  # pragma: no cover - _case_identity가 먼저 닫는다.
        raise GroundedDialogueError(f"지원하지 않는 stratum입니다: {stratum}")
    expected_user_turns = sum(
        message["role"] == "user" for message in case.get("messages", [])
    )
    if len(values) != expected_user_turns:
        raise GroundedDialogueError("oracle 결과 수와 사용자 turn 수가 다릅니다.")
    return values


def _event_for_update(state: Mapping[str, Any], field: str, value: Any) -> dict[str, Any] | None:
    current = state["birth_slots"][field]
    if current == value:
        return None
    return {
        "type": "correct_slot" if current is not None else "set_slot",
        "field": field,
        "value": deepcopy(value),
    }


def apply_extractions(
    results: Sequence[ExtractionResult],
    *,
    signer: RuntimeIdSigner = _ORACLE_SIGNER,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """추출값을 현재 v2.1 구조화 event로만 변환해 intake FSM에 적용한다."""

    state = empty_intake_state()
    transition = advance_intake(
        state,
        {"type": "opt_in", "accepted": True},
        signer,
        DIAGNOSTIC_RUNTIME_STATUS,
    )
    state = transition["session_state"]
    applied: list[dict[str, Any]] = [{"type": "opt_in", "accepted": True}]
    for result in results:
        if not result.valid:
            continue
        unknown = set(result.explicit_unknown_fields)
        if not unknown <= {"birth_time"}:
            raise GroundedDialogueError("허용되지 않은 명시적 미상 field입니다.")
        updates = dict(result.updates)
        allowed = {
            "birth_date",
            "calendar",
            "leap_month",
            "birth_time",
            "time_range",
            "birthplace",
        }
        if not set(updates) <= allowed:
            raise GroundedDialogueError("추출 결과가 FSM slot allowlist를 벗어납니다.")
        ordered: list[tuple[str, Any]] = []
        for slot_name in ("birth_date", "calendar", "leap_month"):
            if slot_name in updates:
                ordered.append((slot_name, updates[slot_name]))
        if "birth_time" in updates and "time_range" in updates:
            raise GroundedDialogueError("정확 시각과 범위를 함께 적용할 수 없습니다.")
        if "birth_time" in updates:
            ordered.extend((("time_precision", "exact"), ("birth_time", updates["birth_time"])))
        elif "time_range" in updates:
            ordered.extend((("time_precision", "range"), ("time_range", updates["time_range"])))
        if "birthplace" in updates:
            ordered.append(("birthplace", updates["birthplace"]))
        for slot_name, value in ordered:
            event = _event_for_update(state, slot_name, value)
            if event is None:
                continue
            transition = advance_intake(
                state, event, signer, DIAGNOSTIC_RUNTIME_STATUS
            )
            state = transition["session_state"]
            applied.append(event)
        if "birth_time" in unknown:
            event = {"type": "set_time_unknown"}
            transition = advance_intake(
                state, event, signer, DIAGNOSTIC_RUNTIME_STATUS
            )
            state = transition["session_state"]
            applied.append(event)
    return state, transition["decision"], applied


def slot_state_projection(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "birth_slots": deepcopy(dict(state["birth_slots"])),
        "confirmed_fields": sorted(state["confirmed_fields"]),
        "explicit_unknown_fields": sorted(state["explicit_unknown_fields"]),
    }


def extract_structured_chart(case: Mapping[str, Any]) -> dict[str, Any] | None:
    if case.get("stratum") != "structured_chart_ready":
        return None
    content = str(case["messages"][-1]["content"])
    marker = "검증된 runtime 구조화 명식입니다:"
    start = content.find(marker)
    if start < 0:
        raise GroundedDialogueError("structured chart marker가 없습니다.")
    payload = content[start + len(marker) :].lstrip()
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise GroundedDialogueError("structured chart에 중복 key가 있습니다.")
            value[key] = item
        return value

    try:
        chart, consumed = json.JSONDecoder(
            object_pairs_hook=reject_duplicates
        ).raw_decode(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise GroundedDialogueError("structured chart JSON을 읽지 못했습니다.") from exc
    if not payload[consumed:].lstrip().startswith("이 사실만 바탕으로"):
        raise GroundedDialogueError("structured chart 뒤 문장 경계가 다릅니다.")
    stems = "甲乙丙丁戊己庚辛壬癸"
    branches = "子丑寅卯辰巳午未申酉戌亥"
    jiazi = {stems[index % 10] + branches[index % 12] for index in range(60)}
    if (
        not isinstance(chart, dict)
        or set(chart) != {"schema_version", "pillars", "calculation_status"}
        or chart.get("schema_version") != "fact-only-v1"
        or chart.get("calculation_status") != "verified_runtime"
        or not isinstance(chart.get("pillars"), dict)
        or set(chart["pillars"]) != {"year", "month", "day", "hour"}
        or any(value not in jiazi for value in chart["pillars"].values())
    ):
        raise GroundedDialogueError("structured chart allowlist 계약이 다릅니다.")
    return chart
