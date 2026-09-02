# backends.py - K0·LoRA adapter·KI20을 동일한 local-only 생성 설정으로 실행한다.

from __future__ import annotations

import gc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from .contracts import Mix2KV4EvaluationError

MODEL_GENERATION_KEYS = (
    "do_sample",
    "num_beams",
    "num_beam_groups",
    "num_return_sequences",
    "max_new_tokens",
    "min_new_tokens",
    "use_cache",
    "bos_token_id",
    "eos_token_id",
    "pad_token_id",
    "return_dict_in_generate",
    "output_scores",
    "renormalize_logits",
    "remove_invalid_values",
)


def effective_generation_payload(generation: Mapping[str, Any]) -> dict[str, Any]:
    """모델 디렉터리 설정을 상속하지 않는 실제 generate 인자만 고정한다."""

    return {key: generation[key] for key in MODEL_GENERATION_KEYS}


@dataclass(frozen=True)
class GeneratedTurn:
    text: str
    input_tokens: int
    new_tokens: int
    max_token_hit: bool
    input_over_budget: bool


class LocalArmRunner:
    """한 arm만 GPU에 올리고 correction retry 없이 raw greedy 출력을 반환한다."""

    def __init__(
        self,
        *,
        model_root: Path,
        generation: Mapping[str, Any],
        adapter_root: Path | None = None,
    ) -> None:
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                GenerationConfig,
            )
        except Exception as exc:
            raise Mix2KV4EvaluationError(
                "평가 model runtime import가 실패했습니다."
            ) from exc
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise Mix2KV4EvaluationError("평가 실행에는 단일 CUDA GPU가 필요합니다.")
        self._torch = torch
        self._generation = dict(generation)
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=True,
            fix_mistral_regex=generation["fix_mistral_regex"],
        )
        if self._tokenizer.pad_token_id is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token
        self._tokenizer.padding_side = "left"
        expected_token_ids = {
            "bos_token_id": generation["bos_token_id"],
            "eos_token_id": generation["eos_token_id"][0],
            "pad_token_id": generation["pad_token_id"],
        }
        observed_token_ids = {
            key: getattr(self._tokenizer, key) for key in expected_token_ids
        }
        if observed_token_ids != expected_token_ids:
            raise Mix2KV4EvaluationError(
                "tokenizer special token ID가 고정 생성 계약과 다릅니다."
            )
        model = AutoModelForCausalLM.from_pretrained(
            model_root,
            local_files_only=True,
            trust_remote_code=True,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            low_cpu_mem_usage=True,
        )
        if adapter_root is not None:
            try:
                from peft import PeftModel
            except Exception as exc:
                raise Mix2KV4EvaluationError(
                    "PEFT adapter runtime import가 실패했습니다."
                ) from exc
            model = PeftModel.from_pretrained(
                model, adapter_root, is_trainable=False, local_files_only=True
            )
        self._model = model.to("cuda:0")
        self._model.eval()
        self._model.config.use_cache = generation["use_cache"]
        self._effective_generation = effective_generation_payload(generation)
        self._generation_config = GenerationConfig(**self._effective_generation)
        self._model.generation_config = GenerationConfig(**self._effective_generation)
        native_context = int(getattr(self._model.config, "max_position_embeddings", 0))
        if native_context < int(generation["native_context_tokens_minimum"]):
            self.close()
            raise Mix2KV4EvaluationError(
                "모델 native context가 평가 8K 계약보다 작습니다."
            )

    def close(self) -> None:
        model = getattr(self, "_model", None)
        if model is None:
            return
        del self._model
        del model
        gc.collect()
        self._torch.cuda.empty_cache()
        self._torch.cuda.synchronize()

    def __enter__(self) -> LocalArmRunner:  # noqa: PYI034
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def _prompt(self, messages: Sequence[Mapping[str, str]]) -> str:
        return self._tokenizer.apply_chat_template(
            list(messages), tokenize=False, add_generation_prompt=True
        )

    def count_input_tokens(self, messages: Sequence[Mapping[str, str]]) -> int:
        prompt = self._prompt(messages)
        return len(self._tokenizer(prompt, add_special_tokens=False)["input_ids"])

    def generate(self, messages: Sequence[Mapping[str, str]]) -> GeneratedTurn:
        prompt = self._prompt(messages)
        input_tokens = len(
            self._tokenizer(prompt, add_special_tokens=False)["input_ids"]
        )
        if input_tokens > int(self._generation["max_input_tokens"]):
            return GeneratedTurn(
                text="",
                input_tokens=input_tokens,
                new_tokens=0,
                max_token_hit=False,
                input_over_budget=True,
            )
        encoded = self._tokenizer(
            prompt,
            return_tensors="pt",
            add_special_tokens=False,
        ).to("cuda:0")
        with self._torch.inference_mode():
            tokens = self._model.generate(
                **encoded,
                generation_config=self._generation_config,
            )
        generated = tokens[0, encoded["input_ids"].shape[1] :]
        token_values = generated.tolist()
        eos_ids = set(self._effective_generation["eos_token_id"])
        eos_positions = [
            index for index, token_id in enumerate(token_values) if token_id in eos_ids
        ]
        eos_seen = bool(eos_positions)
        new_tokens = eos_positions[0] + 1 if eos_positions else len(token_values)
        text = self._tokenizer.decode(generated, skip_special_tokens=True).strip()
        del tokens, generated, encoded
        return GeneratedTurn(
            text=text,
            input_tokens=input_tokens,
            new_tokens=new_tokens,
            max_token_hit=(
                not eos_seen and new_tokens >= int(self._generation["max_new_tokens"])
            ),
            input_over_budget=False,
        )


__all__ = ["GeneratedTurn", "LocalArmRunner", "effective_generation_payload"]
