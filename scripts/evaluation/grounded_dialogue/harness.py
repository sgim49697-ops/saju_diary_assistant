# harness.py - 슬롯 추출, 현재 FSM, 후보 사실, prompt, 응답 생성을 case 단위로 연결한다.

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from scripts.runtime.saju_contract import project_model_visible_tool_result

from .backends import CalculatorRunner, GeneratedText, ModelRunner
from .cases import (
    ExtractionResult,
    apply_extractions,
    extract_structured_chart,
    oracle_extractions,
    slot_state_projection,
)
from .errors import GroundedDialogueError, PromptBudgetError
from .extractors import (
    ModelNarrowSlotExtractor,
    OracleSlotExtractor,
    RuleSlotExtractor,
)
from .graders import grade_response, slot_state_score


@dataclass(frozen=True)
class ArmConfig:
    arm_id: str
    model_id: str
    slot_extractor_id: str
    max_input_tokens: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ArmConfig:
        return cls(
            arm_id=str(value["arm_id"]),
            model_id=str(value["model_id"]),
            slot_extractor_id=str(value["slot_extractor_id"]),
            max_input_tokens=int(value["max_input_tokens"]),
        )


def _system_message(
    prompt: str,
    *,
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
    route: str,
    tool_result: Mapping[str, Any],
) -> str:
    slots = {
        key: deepcopy(value)
        for key, value in state["birth_slots"].items()
        if key != "gender_for_daeun" and value is not None
    }
    context = {
        "diagnostic": {
            "diagnostic_only": True,
            "route": route,
            "fsm_decision_before_executor": (
                None if route == "provided_structured_chart" else decision["action"]
            ),
            "provided_structured_chart_bypasses_intake": (
                route == "provided_structured_chart"
            ),
            "simulated_app_preconditions_ready": True,
            "candidate_result_inserted_into_app_fsm": False,
        },
        "session": {
            "birth_slots": slots,
            "confirmed_fields": sorted(state["confirmed_fields"]),
            "explicit_unknown_fields": sorted(state["explicit_unknown_fields"]),
        },
        "tool_result": deepcopy(dict(tool_result)),
    }
    compact = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prompt.strip()}\n<runtime_context>{compact}</runtime_context>"


def truncate_messages(
    system_content: str,
    case_messages: Sequence[Mapping[str, str]],
    *,
    max_input_tokens: int,
    token_counter: Any,
) -> tuple[list[dict[str, str]], int, int]:
    """runtime context·현재 user는 보존하고 오래된 완전한 user+assistant 쌍만 버린다."""

    if not case_messages or case_messages[-1].get("role") != "user":
        raise GroundedDialogueError("현재 user turn이 없는 prompt는 만들 수 없습니다.")
    history = [dict(message) for message in case_messages[:-1]]
    current = dict(case_messages[-1])
    dropped_pairs = 0
    while True:
        messages = [{"role": "system", "content": system_content}, *history, current]
        input_tokens = int(token_counter(messages))
        if input_tokens <= max_input_tokens:
            return messages, input_tokens, dropped_pairs
        if len(history) >= 2 and history[0].get("role") == "user" and history[1].get(
            "role"
        ) == "assistant":
            history = history[2:]
            dropped_pairs += 1
            continue
        raise PromptBudgetError(input_tokens, max_input_tokens, dropped_pairs)


def _incomplete_tool_result(decision: Mapping[str, Any]) -> dict[str, Any]:
    reason = str(decision.get("reason_code") or decision["action"])
    return project_model_visible_tool_result(
        {
            "status": "blocked",
            "code": "INPUT_INCOMPLETE",
            "message": f"현재 intake 상태에서는 계산을 실행하지 않습니다: {reason}",
            "limitations": ["확인되지 않은 출생 슬롯을 추측하지 않습니다."],
        }
    )


def _extractor_for_case(
    arm: ArmConfig,
    case: Mapping[str, Any],
    model: ModelRunner,
    config: Mapping[str, Any],
) -> Any:
    if arm.slot_extractor_id == "oracle":
        return OracleSlotExtractor(oracle_extractions(case))
    if arm.slot_extractor_id == "rule":
        return RuleSlotExtractor()
    if arm.slot_extractor_id == "model_narrow":
        narrow = config["slot_extraction"]["model_narrow"]
        if arm.model_id != narrow["model_id"]:
            raise GroundedDialogueError("model_narrow는 KI20 arm에서만 실행할 수 있습니다.")
        return ModelNarrowSlotExtractor(model, max_new_tokens=narrow["max_new_tokens"])
    raise GroundedDialogueError(f"알 수 없는 extractor입니다: {arm.slot_extractor_id}")


def _case_slot_results(
    case: Mapping[str, Any], extractor: Any
) -> tuple[list[ExtractionResult], dict[str, Any], dict[str, Any]]:
    results: list[ExtractionResult] = []
    state, decision, _ = apply_extractions(results)
    for message in case["messages"]:
        if message["role"] != "user":
            continue
        result = extractor.extract(message["content"], state)
        results.append(result)
        state, decision, _ = apply_extractions(results)
    if isinstance(extractor, OracleSlotExtractor):
        extractor.assert_consumed()
    return results, state, decision


def _correction_required(case: Mapping[str, Any]) -> bool:
    if case.get("stratum") != "accumulated_context_no_reask":
        return False
    index = int(str(case["case_id"]).rsplit("-", 1)[1])
    return 3 <= index < 6


def prepare_case(
    case: Mapping[str, Any],
    *,
    arm: ArmConfig,
    model: ModelRunner,
    calculator: CalculatorRunner,
    config: Mapping[str, Any],
    system_prompt: str,
) -> tuple[dict[str, Any], list[dict[str, str]] | None]:
    extractor = _extractor_for_case(arm, case, model, config)
    results, state, decision = _case_slot_results(case, extractor)
    expected_state, _, expected_events = apply_extractions(oracle_extractions(case))
    actual_projection = slot_state_projection(state)
    expected_projection = slot_state_projection(expected_state)
    extraction_valid = all(result.valid for result in results)
    score = slot_state_score(
        actual_projection, expected_projection, extraction_valid=extraction_valid
    )
    correction_required = _correction_required(case)
    score["correction_required"] = correction_required
    score["correction_pass"] = (
        not correction_required
        or (
            score["exact_state"]
            and any(event["type"] == "correct_slot" for event in expected_events)
        )
    )

    structured = extract_structured_chart(case)
    if structured is not None:
        tool_result = project_model_visible_tool_result(
            {
                "status": "ok",
                "hard_facts": structured,
                "fact_authority": "HARD_GT",
            }
        )
        route = "provided_structured_chart"
    elif decision["action"] == "call_chart":
        if state["chart"]["chart_valid"] is not False or state["chart"]["hard_facts"] is not None:
            raise GroundedDialogueError("candidate 호출 전에 app FSM chart가 비어 있지 않습니다.")
        tool_result = calculator.calculate_chart(decision["arguments"])
        if state["chart"]["chart_valid"] is not False or state["chart"]["hard_facts"] is not None:
            raise GroundedDialogueError("candidate 결과가 app FSM chart 상태에 삽입됐습니다.")
        route = (
            "candidate_grounded_reply"
            if isinstance(tool_result.get("hard_facts"), Mapping)
            else "candidate_limited_reply"
        )
    else:
        tool_result = _incomplete_tool_result(decision)
        route = "input_limited_reply"

    system_content = _system_message(
        system_prompt,
        state=state,
        decision=decision,
        route=route,
        tool_result=tool_result,
    )
    prompt_error: str | None = None
    try:
        messages, input_tokens, dropped_pairs = truncate_messages(
            system_content,
            case["messages"],
            max_input_tokens=arm.max_input_tokens,
            token_counter=model.count_input_tokens,
        )
    except PromptBudgetError as exc:
        messages = None
        input_tokens = exc.input_tokens
        dropped_pairs = exc.dropped_pairs
        prompt_error = "MINIMAL_PROMPT_OVER_BUDGET"
    if (
        messages is not None
        and input_tokens + config["generation"]["max_new_tokens"]
        > config["generation"]["native_context_tokens"]
    ):
        raise GroundedDialogueError("prompt와 출력 예산이 native context를 넘습니다.")
    extraction_rows = [
        {
            "valid": result.valid,
            "error_code": result.error_code,
            "updates": deepcopy(dict(result.updates)),
            "explicit_unknown_fields": list(result.explicit_unknown_fields),
            **(
                {"raw_output": result.raw_output}
                if result.raw_output is not None
                else {}
            ),
        }
        for result in results
    ]
    record = {
        "schema_version": "0.1.0",
        "case_id": case["case_id"],
        "stratum": case["stratum"],
        "arm_id": arm.arm_id,
        "model_id": arm.model_id,
        "slot_extractor_id": arm.slot_extractor_id,
        "extraction": {"turns": extraction_rows, "score": score},
        "fsm": {
            "decision_action": decision["action"],
            "simulated_app_preconditions_ready": True,
            "chart_valid_after_candidate": state["chart"]["chart_valid"],
            "candidate_result_inserted_into_app_fsm": False,
        },
        "tool_result": deepcopy(tool_result),
        "route": route,
        "prompt_metadata": {
            "input_tokens": input_tokens,
            "max_input_tokens": arm.max_input_tokens,
            "dropped_complete_pairs": dropped_pairs,
            "system_prompt_messages": 1,
            "error_code": prompt_error,
        },
        "grading_session_state": {
            "confirmed_fields": sorted(state["confirmed_fields"]),
            "explicit_unknown_fields": sorted(state["explicit_unknown_fields"]),
        },
    }
    return record, messages


def _generate_batch(
    model: ModelRunner,
    prompts: Sequence[Sequence[Mapping[str, str]]],
    *,
    max_new_tokens: int,
) -> list[GeneratedText]:
    method = getattr(model, "generate_many", None)
    if callable(method):
        return method(prompts, max_new_tokens=max_new_tokens)
    values: list[GeneratedText] = []
    for messages in prompts:
        output = model.generate(messages, max_new_tokens=max_new_tokens)
        values.append(
            GeneratedText(
                text=output,
                input_tokens=model.count_input_tokens(messages),
                new_tokens=0,
                max_token_hit=False,
            )
        )
    return values


def run_arm(
    arm: ArmConfig,
    cases: Sequence[Mapping[str, Any]],
    *,
    model: ModelRunner,
    calculator: CalculatorRunner,
    config: Mapping[str, Any],
    system_prompt: str,
) -> list[dict[str, Any]]:
    if len(cases) != 100:
        raise GroundedDialogueError("진단 arm은 고정 100건이어야 합니다.")
    prepared: list[dict[str, Any]] = []
    prompt_items: list[tuple[int, list[dict[str, str]]]] = []
    for case in cases:
        record, messages = prepare_case(
            case,
            arm=arm,
            model=model,
            calculator=calculator,
            config=config,
            system_prompt=system_prompt,
        )
        prepared.append(record)
        if messages is not None:
            prompt_items.append((len(prepared) - 1, messages))
    batch_size = int(config["generation"]["batch_size"])
    generated_by_index: dict[int, GeneratedText] = {}
    for start in range(0, len(prompt_items), batch_size):
        batch = prompt_items[start : start + batch_size]
        values = _generate_batch(
            model,
            [messages for _, messages in batch],
            max_new_tokens=config["generation"]["max_new_tokens"],
        )
        if len(values) != len(batch):
            raise GroundedDialogueError("모델 batch 생성 결과 수가 다릅니다.")
        generated_by_index.update(
            (index, value) for (index, _), value in zip(batch, values, strict=True)
        )
    if len(generated_by_index) != len(prompt_items):
        raise GroundedDialogueError("모델 생성 결과 수가 case 수와 다릅니다.")
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(prepared):
        expected_input = record["prompt_metadata"]["input_tokens"]
        generation = generated_by_index.get(index)
        attempted = generation is not None
        if generation is None:
            generation = GeneratedText(
                text="",
                input_tokens=expected_input,
                new_tokens=0,
                max_token_hit=False,
            )
        elif generation.input_tokens != expected_input:
            raise GroundedDialogueError("생성 시점 input token 수가 plan과 달라졌습니다.")
        tool_result = record["tool_result"]
        response_grade = grade_response(
            generation.text,
            hard_facts=tool_result.get("hard_facts"),
            tool_status=tool_result["status"],
            session_state=record["grading_session_state"],
            max_token_hit=generation.max_token_hit,
        )
        rows.append(
            {
                **record,
                "generation": {
                    "attempted": attempted,
                    "error_code": record["prompt_metadata"]["error_code"],
                    "output": generation.text,
                    "input_tokens": generation.input_tokens,
                    "new_tokens": generation.new_tokens,
                    "max_token_hit": generation.max_token_hit,
                },
                "response_grade": response_grade,
            }
        )
    return rows


def rule_harness_gate(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    exact = 0
    valid = 0
    time_semantics = 0
    corrections_required = 0
    corrections_passed = 0
    for case in cases:
        extractor = RuleSlotExtractor()
        results, state, _ = _case_slot_results(case, extractor)
        expected, _, expected_events = apply_extractions(oracle_extractions(case))
        score = slot_state_score(
            slot_state_projection(state),
            slot_state_projection(expected),
            extraction_valid=all(result.valid for result in results),
        )
        exact += score["exact_state"]
        valid += not score["invalid_extraction"]
        time_semantics += score["time_semantics_pass"]
        if _correction_required(case):
            corrections_required += 1
            corrections_passed += score["exact_state"] and any(
                event["type"] == "correct_slot" for event in expected_events
            )
    percent = round(exact * 100 / len(cases), 6)
    result = {
        "status": "passed" if percent == 100.0 else "failed",
        "cases": len(cases),
        "exact_state": exact,
        "exact_state_percent": percent,
        "valid_extractions": valid,
        "time_semantics_passed": time_semantics,
        "corrections_required": corrections_required,
        "corrections_passed": corrections_passed,
    }
    if result["status"] != "passed":
        raise GroundedDialogueError(f"rule extractor 고정 suite Gate 실패: {result}")
    return result
