# harness.py - 고정 KI20 tokenizer·FSM·계산기로 장문 paired arm을 준비하고 실행한다.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.evaluation.grounded_dialogue.backends import (
    CalculatorRunner,
    GeneratedText,
    ModelRunner,
)
from scripts.evaluation.grounded_dialogue.errors import (
    GroundedDialogueError,
    PromptBudgetError,
)
from scripts.evaluation.grounded_dialogue.harness import (
    ArmConfig,
    prepare_case,
    truncate_messages,
)

from .context_cases import ContextCase, build_context_cases
from .graders import grade_response


class TokenizerOnlyRunner:
    """모델 weight를 열지 않고 production chat template token 수만 계산한다."""

    def __init__(self, model_root: Path, generation: Mapping[str, Any]) -> None:
        try:
            from transformers import AutoTokenizer
        except Exception as exc:
            raise GroundedDialogueError("KI20 tokenizer를 import하지 못했습니다.") from exc
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=True,
            fix_mistral_regex=generation["fix_mistral_regex"],
        )

    def _prompt(self, messages: Sequence[Mapping[str, str]]) -> str:
        return self._tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=True
        )

    def count_input_tokens(self, messages: Sequence[Mapping[str, str]]) -> int:
        return len(
            self._tokenizer(
                self._prompt(messages), add_special_tokens=False
            )["input_ids"]
        )

    def generate(
        self, messages: Sequence[Mapping[str, str]], *, max_new_tokens: int
    ) -> str:
        del messages, max_new_tokens
        raise GroundedDialogueError("tokenizer-only runner는 응답을 생성하지 않습니다.")

    def generate_many(
        self,
        message_batches: Sequence[Sequence[Mapping[str, str]]],
        *,
        max_new_tokens: int,
    ) -> list[GeneratedText]:
        del message_batches, max_new_tokens
        raise GroundedDialogueError("tokenizer-only runner는 응답을 생성하지 않습니다.")


def prepare_context_suite(
    base_cases: Sequence[Mapping[str, Any]],
    *,
    model: ModelRunner,
    calculator: CalculatorRunner,
    parent_config: Mapping[str, Any],
    followup_config: Mapping[str, Any],
    system_prompt: str,
) -> tuple[list[ContextCase], dict[str, dict[str, Any]], dict[str, str]]:
    template_arm = ArmConfig(
        arm_id="C1_KI20_ORACLE_3584",
        model_id="KI20",
        slot_extractor_id="oracle",
        max_input_tokens=3584,
    )
    records: dict[str, dict[str, Any]] = {}
    system_contents: dict[str, str] = {}
    for case in base_cases:
        record, messages = prepare_case(
            case,
            arm=template_arm,
            model=model,
            calculator=calculator,
            config=parent_config,
            system_prompt=system_prompt,
        )
        if messages is None or messages[0].get("role") != "system":
            raise GroundedDialogueError("장문 base prompt를 준비하지 못했습니다.")
        source_id = str(case["case_id"])
        records[source_id] = record
        system_contents[source_id] = messages[0]["content"]
    context = followup_config["context_diagnostic"]
    cases = build_context_cases(
        base_cases,
        system_contents,
        bands=context["bands"],
        token_counter=model.count_input_tokens,
        denylist=context["history_policy"]["lexical_denylist"],
    )
    return cases, records, system_contents


def _generate_batch(
    model: ModelRunner,
    prompts: Sequence[Sequence[Mapping[str, str]]],
    *,
    max_new_tokens: int,
) -> list[GeneratedText]:
    method = getattr(model, "generate_many", None)
    if callable(method):
        return method(prompts, max_new_tokens=max_new_tokens)
    return [
        GeneratedText(
            text=model.generate(messages, max_new_tokens=max_new_tokens),
            input_tokens=model.count_input_tokens(messages),
            new_tokens=0,
            max_token_hit=False,
        )
        for messages in prompts
    ]


def run_context_arm(
    arm: ArmConfig,
    cases: Sequence[ContextCase],
    *,
    base_records: Mapping[str, Mapping[str, Any]],
    system_contents: Mapping[str, str],
    model: ModelRunner,
    config: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if len(cases) != 100 or arm.arm_id not in {
        "C0_KI20_ORACLE_2048",
        "C1_KI20_ORACLE_3584",
    }:
        raise GroundedDialogueError("장문 arm identity·case 수가 다릅니다.")
    generation_config = config["context_diagnostic"]["generation"]
    prepared: list[dict[str, Any]] = []
    prompts: list[tuple[int, list[dict[str, str]]]] = []
    for context_case in cases:
        source_id = str(context_case.source_case["case_id"])
        base_record = deepcopy(dict(base_records[source_id]))
        system_content = system_contents[source_id]
        case_messages = [
            *deepcopy(list(context_case.history_messages)),
            *deepcopy(list(context_case.source_case["messages"])),
        ]
        error_code: str | None = None
        try:
            messages, input_tokens, dropped_pairs = truncate_messages(
                system_content,
                case_messages,
                max_input_tokens=arm.max_input_tokens,
                token_counter=model.count_input_tokens,
            )
        except PromptBudgetError as exc:
            messages = None
            input_tokens = exc.input_tokens
            dropped_pairs = exc.dropped_pairs
            error_code = "MINIMAL_PROMPT_OVER_BUDGET"
        if messages is not None and (
            input_tokens + generation_config["max_new_tokens"]
            > generation_config["native_context_tokens"]
        ):
            raise GroundedDialogueError("장문 prompt와 출력 예산이 native context를 넘습니다.")
        record = {
            "schema_version": "0.1.0",
            "context_case_id": context_case.context_case_id,
            "source_case_id": source_id,
            "stratum": context_case.source_case["stratum"],
            "band_id": context_case.band_id,
            "arm_id": arm.arm_id,
            "model_id": arm.model_id,
            "slot_extractor_id": arm.slot_extractor_id,
            "extraction": base_record["extraction"],
            "fsm": base_record["fsm"],
            "tool_result": base_record["tool_result"],
            "route": base_record["route"],
            "prompt_metadata": {
                "base_input_tokens": context_case.base_input_tokens,
                "original_input_tokens": context_case.original_input_tokens,
                "final_input_tokens": input_tokens,
                "max_input_tokens": arm.max_input_tokens,
                "dropped_complete_pairs": dropped_pairs,
                "error_code": error_code,
            },
            "grading_session_state": base_record["grading_session_state"],
        }
        prepared.append(record)
        if messages is not None:
            prompts.append((len(prepared) - 1, messages))

    generated: dict[int, GeneratedText] = {}
    batch_size = int(generation_config["batch_size"])
    for start in range(0, len(prompts), batch_size):
        batch = prompts[start : start + batch_size]
        values = _generate_batch(
            model,
            [messages for _, messages in batch],
            max_new_tokens=int(generation_config["max_new_tokens"]),
        )
        if len(values) != len(batch):
            raise GroundedDialogueError("장문 batch 생성 결과 수가 다릅니다.")
        generated.update(
            (index, value) for (index, _), value in zip(batch, values, strict=True)
        )

    rows: list[dict[str, Any]] = []
    for index, record in enumerate(prepared):
        expected_input = record["prompt_metadata"]["final_input_tokens"]
        output = generated.get(index)
        attempted = output is not None
        if output is None:
            output = GeneratedText(
                text="",
                input_tokens=expected_input,
                new_tokens=0,
                max_token_hit=False,
            )
        elif output.input_tokens != expected_input:
            raise GroundedDialogueError("장문 생성 시점 token 수가 plan과 다릅니다.")
        tool_result = record["tool_result"]
        response_grade = grade_response(
            output.text,
            hard_facts=tool_result.get("hard_facts"),
            tool_status=tool_result["status"],
            session_state=record["grading_session_state"],
            decision_action=record["fsm"]["decision_action"],
            max_token_hit=output.max_token_hit,
        )
        rows.append(
            {
                **record,
                "generation": {
                    "attempted": attempted,
                    "error_code": record["prompt_metadata"]["error_code"],
                    "output": output.text,
                    "input_tokens": output.input_tokens,
                    "new_tokens": output.new_tokens,
                    "max_token_hit": output.max_token_hit,
                },
                "response_grade": response_grade,
            }
        )
    return rows


__all__ = ["TokenizerOnlyRunner", "prepare_context_suite", "run_context_arm"]
