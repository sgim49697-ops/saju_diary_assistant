# backends.py - local-only 모델 생성과 candidate runtime projection backend를 제공한다.

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .errors import GroundedDialogueError


@dataclass(frozen=True)
class GeneratedText:
    text: str
    input_tokens: int
    new_tokens: int
    max_token_hit: bool


class ModelRunner(Protocol):
    def count_input_tokens(self, messages: Sequence[Mapping[str, str]]) -> int:
        ...

    def generate(
        self, messages: Sequence[Mapping[str, str]], *, max_new_tokens: int
    ) -> str:
        ...

    def generate_many(
        self,
        message_batches: Sequence[Sequence[Mapping[str, str]]],
        *,
        max_new_tokens: int,
    ) -> list[GeneratedText]:
        ...


class CalculatorRunner(Protocol):
    def calculate_chart(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        ...


class TransformersModelRunner:
    """Phase 6와 같은 local-only bf16·SDPA·greedy 생성 계약을 사용한다."""

    def __init__(self, model_root: Path, generation: Mapping[str, Any]) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except Exception as exc:
            raise GroundedDialogueError("모델 평가 runtime을 import하지 못했습니다.") from exc
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise GroundedDialogueError("진단 실행에는 단일 CUDA GPU가 필요합니다.")
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=True,
            fix_mistral_regex=generation["fix_mistral_regex"],
        )
        if self._tokenizer.pad_token_id is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        self._tokenizer.padding_side = "left"
        self._model = AutoModelForCausalLM.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=True,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
        ).to("cuda:0")
        self._model.eval()
        self._model.config.use_cache = True

    def close(self) -> None:
        model = self._model
        del self._model
        del model
        self._torch.cuda.empty_cache()
        self._torch.cuda.synchronize()

    def _prompt(self, messages: Sequence[Mapping[str, str]]) -> str:
        return self._tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=True
        )

    def count_input_tokens(self, messages: Sequence[Mapping[str, str]]) -> int:
        prompt = self._prompt(messages)
        return len(self._tokenizer(prompt, add_special_tokens=False)["input_ids"])

    def generate(
        self, messages: Sequence[Mapping[str, str]], *, max_new_tokens: int
    ) -> str:
        return self.generate_many([messages], max_new_tokens=max_new_tokens)[0].text

    def generate_many(
        self,
        message_batches: Sequence[Sequence[Mapping[str, str]]],
        *,
        max_new_tokens: int,
    ) -> list[GeneratedText]:
        if not message_batches:
            return []
        prompts = [self._prompt(messages) for messages in message_batches]
        input_lengths = [
            len(self._tokenizer(prompt, add_special_tokens=False)["input_ids"])
            for prompt in prompts
        ]
        encoded = self._tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            add_special_tokens=False,
        ).to("cuda:0")
        with self._torch.inference_mode():
            tokens = self._model.generate(
                **encoded,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                pad_token_id=self._tokenizer.pad_token_id,
                eos_token_id=self._tokenizer.eos_token_id,
            )
        prompt_width = encoded["input_ids"].shape[1]
        generated = tokens[:, prompt_width:]
        outputs = self._tokenizer.batch_decode(generated, skip_special_tokens=True)
        values: list[GeneratedText] = []
        for output, input_tokens, token_row in zip(
            outputs, input_lengths, generated, strict=True
        ):
            token_values = token_row.tolist()
            eos_seen = self._tokenizer.eos_token_id in token_values
            new_tokens = (
                token_values.index(self._tokenizer.eos_token_id) + 1
                if eos_seen
                else len(token_values)
            )
            values.append(
                GeneratedText(
                    text=output.strip(),
                    input_tokens=input_tokens,
                    new_tokens=new_tokens,
                    max_token_hit=not eos_seen and new_tokens >= max_new_tokens,
                )
            )
        return values


class CandidateCalculator:
    """v1.3 결과의 model-visible projection만 반환하고 app FSM에는 삽입하지 않는다."""

    def __init__(self, ephemeris_path: Path) -> None:
        from scripts.runtime.calculation.engine_v1_3 import SajuRuntimeEngineV13
        from scripts.runtime.calculation.id_signer import RuntimeIdSigner

        key = hashlib.sha256(b"grounded-dialogue-eval-v1").digest()
        self._engine = SajuRuntimeEngineV13(
            signer=RuntimeIdSigner.for_test(key),
            enable_candidate_runtime=True,
            ephemeris_path=ephemeris_path,
        )
        self._cache: dict[bytes, dict[str, Any]] = {}

    def close(self) -> None:
        self._engine.close()

    def calculate_chart(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        from scripts.runtime.calculation.engine_v1_3 import (
            execute_candidate_runtime_tool_v1_3,
        )

        key = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if key in self._cache:
            return deepcopy(self._cache[key])
        _, visible = execute_candidate_runtime_tool_v1_3(
            self._engine, "calculate_saju_chart", deepcopy(dict(arguments))
        )
        if visible["status"] in {"ok", "partial"} and (
            visible["status"] != "partial"
            or visible.get("fact_authority") != "HARD_CANDIDATE"
        ):
            raise GroundedDialogueError("candidate 결과 상태·권위가 고정 계약과 다릅니다.")
        self._cache[key] = deepcopy(visible)
        return visible
