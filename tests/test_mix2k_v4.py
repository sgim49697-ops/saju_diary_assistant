# test_mix2k_v4.py - MIX2K v4 full-runtime spec과 구조 사실 validator를 검증한다.

from __future__ import annotations

import unittest
from collections import Counter
from copy import deepcopy
from unittest.mock import patch

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    MAX_COMPLETION_TOKENS,
    RECORD_SCHEMA_VERSION,
    RUNTIME_BINDING_ID,
    RUNTIME_BINDING_SCHEMA,
    Mix2KV4ContractError,
    flatten_runtime_facts,
    sentence_count,
    sha256_bytes,
    structural_claim_errors,
    validate_draft,
    validate_spec,
)
from scripts.data.mix2k_v4_finalize import select_training_max_length
from scripts.data.mix2k_v4_teachers import (
    _selection,
    draft_prompt,
    review_prompt,
    subscription_environment,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.training.mix2k_v4_lora import DEFAULT_CONFIG as LORA_CONFIG
from scripts.training.mix2k_v4_lora import _validate_config as validate_lora_config


def _binding() -> dict[str, object]:
    value = {
        "chart": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "pillars": {
                    "year": {
                        "ganzhi": "戊辰",
                        "stem": "戊",
                        "branch": "辰",
                        "stem_ten_god": "정재",
                        "branch_ten_god": "편재",
                        "hidden_stems": ["戊", "乙", "癸"],
                    },
                    "month": {
                        "ganzhi": "甲子",
                        "stem": "甲",
                        "branch": "子",
                        "stem_ten_god": "겁재",
                        "branch_ten_god": "편인",
                        "hidden_stems": ["癸"],
                    },
                    "day": {
                        "ganzhi": "乙丑",
                        "stem": "乙",
                        "branch": "丑",
                        "stem_ten_god": "비견",
                        "branch_ten_god": "편재",
                        "hidden_stems": ["己", "癸", "辛"],
                    },
                    "hour": {
                        "ganzhi": "壬午",
                        "stem": "壬",
                        "branch": "午",
                        "stem_ten_god": "정인",
                        "branch_ten_god": "식신",
                        "hidden_stems": ["丁", "己"],
                    },
                },
                "day_master": {
                    "stem": "乙",
                    "five_element": "목",
                    "yin_yang": "음",
                },
                "surface_five_elements": {
                    "목": 2,
                    "화": 1,
                    "토": 3,
                    "금": 0,
                    "수": 2,
                },
                "calculation_profile": "KR_CIVIL_MIDNIGHT_V1",
                "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
            },
            "message": "합성 원국 계산 완료",
            "limitations": [],
        },
        "period": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "period": {
                    "period_type": "day",
                    "target_date": "2026-09-02",
                    "timezone": "Asia/Seoul",
                    "evaluation_local_time": "12:00",
                    "year_ganzhi": "丙午",
                    "month_ganzhi": "丙申",
                    "day_ganzhi": "己卯",
                },
                "day_assignment_evidence": {
                    "authority": "SOURCE_HARD_FACT",
                    "future_physical_instant_claimed": False,
                },
            },
            "message": "합성 단일 일진 계산 완료",
            "limitations": ["단일 날짜 12:00 기준"],
        },
    }
    return {
        "schema_version": RUNTIME_BINDING_SCHEMA,
        "binding_id": RUNTIME_BINDING_ID,
        "capability_sha256": "e" * 64,
        "snapshot_sha256": sha256_bytes(canonical_json_bytes(value)),
        "state_revision": 1,
        "value": value,
    }


def _spec() -> dict[str, object]:
    binding = _binding()
    flattened = flatten_runtime_facts(binding["value"])
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "id": "m2v4_" + "a" * 24,
        "conversation_id": "m2v4c_" + "b" * 24,
        "task_axis": "chart_day_today_flow",
        "template_family": "chart_day_today_flow-f000",
        "substantive": True,
        "multiturn": False,
        "drafter": "claude",
        "reviewer": "codex",
        "prompt": [
            {"role": "system", "content": "원국과 날짜 JSON을 근거로 사용하세요."},
            {"role": "user", "content": "오늘의 흐름을 원국과 함께 이야기해줘."},
        ],
        "runtime_binding": binding,
        "allowed_fact_paths": [path for path, _ in flattened],
        "allowed_fact_values": [value for _, value in flattened],
        "response_contract": {
            "hard_max_completion_tokens": MAX_COMPLETION_TOKENS,
            "minimum_nonempty_lines": 3,
            "minimum_sentences": 3,
            "natural_length_no_preferred_maximum": True,
        },
        "restricted_local_only": False,
    }


class Mix2KV4ContractTests(unittest.TestCase):
    def test_full_runtime_spec_and_exact_regression_answer_pass(self) -> None:
        spec = validate_spec(_spec())
        answer = (
            "원국 전체는 연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午이고 일간은 乙木입니다.\n"
            "2026-09-02의 연간지는 丙午, 월간지는 丙申, 그날의 일진은 己卯입니다.\n"
            "원국과 날짜 사이의 관계 계산이 제공되지 않았으므로 합충이나 신강약은 새로 단정하지 않겠습니다."
        )
        self.assertEqual(structural_claim_errors(spec, answer), [])
        self.assertEqual(sentence_count(answer), 3)
        draft = {
            "record_id": spec["id"],
            "answer": answer,
            "used_fact_paths": [],
            "used_fact_values": [
                "2026-09-02",
                "戊辰",
                "甲子",
                "乙丑",
                "壬午",
                "丙午",
                "丙申",
                "己卯",
            ],
            "soft_interpretation_used": False,
            "limitations": ["원국×기간 relation이 제공되지 않음"],
            "self_check": "PASS",
        }
        self.assertEqual(validate_draft(spec, draft), draft)

    def test_actual_failure_modes_are_rejected(self) -> None:
        spec = _spec()
        cases = {
            "원국은 乙丑입니다.": "natal_day_called_full_chart",
            "오늘 일진은 丙午입니다.": "period_year_called_day_ganzhi",
            "己卯는 세운입니다.": "period_day_called_seun",
            "제공된 원국은 庚子입니다.": "unprovided_ganzhi:庚子",
            "일주의 십신은 정관입니다.": "unprovided_ten_god:정관",
            "乙木이 丑에 뿌리를 두고 있습니다.": "unsupported_structural_claim:rooting",
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(spec, answer))

    def test_validator_does_not_block_ordinary_korean_substrings(self) -> None:
        answer = (
            "내용을 종합해서 설명할게요.\n"
            "이해하기 쉽게 예를 나눠 볼 수 있어요.\n"
            "필요한 부분부터 하나씩 확인해도 괜찮아요."
        )
        self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_today_flow_requires_natal_and_period_day_evidence(self) -> None:
        spec = _spec()
        draft = {
            "record_id": spec["id"],
            "answer": (
                "원국은 네 기둥으로 구성됩니다.\n"
                "선택한 날짜는 별도의 정보입니다.\n"
                "두 자료의 위치를 구분해서 보면 됩니다."
            ),
            "used_fact_paths": [],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        with self.assertRaisesRegex(
            Mix2KV4ContractError, "provided_period_day_fact_omitted"
        ):
            validate_draft(spec, draft)

    def test_role_order_and_three_line_contract_fail_closed(self) -> None:
        malformed = _spec()
        malformed["prompt"].insert(1, {"role": "assistant", "content": "잘못된 순서"})
        with self.assertRaisesRegex(Mix2KV4ContractError, "role"):
            validate_spec(malformed)

        spec = _spec()
        short = {
            "record_id": spec["id"],
            "answer": "원국과 날짜를 구분해서 보겠습니다.",
            "used_fact_paths": [],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        with self.assertRaisesRegex(Mix2KV4ContractError, "최소 줄·문장"):
            validate_draft(spec, short)

    def test_teacher_prompt_has_four_explicit_evidence_sections(self) -> None:
        spec = _spec()
        prompt = draft_prompt([spec], {})
        self.assertEqual(prompt.count("[RAW RUNTIME FACTS]"), 1)
        self.assertEqual(prompt.count("[ALLOWED EVIDENCE]"), 1)
        self.assertEqual(prompt.count("[FORBIDDEN INFERENCE]"), 1)
        self.assertEqual(prompt.count("[TASK]"), 1)
        self.assertNotIn("출생일", prompt)

        draft = {
            "record_id": spec["id"],
            "answer": "검수 대상 답변",
            "used_fact_paths": [],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        review = review_prompt([spec], {spec["id"]: draft})
        self.assertEqual(review.count("[DRAFT TO REVIEW]"), 1)

    def test_pilot_selection_is_balanced_and_api_keys_are_scrubbed(self) -> None:
        specs = []
        for index in range(8):
            spec = deepcopy(_spec())
            spec["id"] = f"m2v4_{index:024d}"
            spec["drafter"] = "claude" if index % 2 == 0 else "codex"
            spec["reviewer"] = "codex" if index % 2 == 0 else "claude"
            specs.append(spec)
        selected = _selection(specs, "pilot", 2)
        self.assertEqual(Counter(row["drafter"] for row in selected), {"claude": 2, "codex": 2})

        with patch.dict(
            "os.environ",
            {
                "EXAMPLE_API_KEY": "secret",
                "GITHUB_TOKEN": "secret",
                "SAFE_VALUE": "kept",
            },
            clear=True,
        ):
            environment = subscription_environment()
        self.assertNotIn("EXAMPLE_API_KEY", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertEqual(environment["SAFE_VALUE"], "kept")

    def test_training_max_length_uses_smallest_non_truncating_ladder_value(self) -> None:
        ladder = [2048, 3584, 4096, 8192]
        self.assertEqual(select_training_max_length(2048, ladder), 2048)
        self.assertEqual(select_training_max_length(2049, ladder), 3584)
        self.assertEqual(select_training_max_length(4096, ladder), 4096)
        self.assertIsNone(select_training_max_length(8193, ladder))

    def test_lora_contract_pins_k0_three_ranks_and_assistant_only_loss(self) -> None:
        config = validate_lora_config(LORA_CONFIG)
        self.assertEqual(config["lora"]["ranks"], [8, 16, 32])
        self.assertEqual(config["lora"]["primary_rank"], 16)
        self.assertEqual(config["lora"]["target_modules"], "all-linear")
        self.assertTrue(config["lora"]["use_rslora"])
        self.assertEqual(config["training"]["learning_rate"], 5e-5)
        self.assertEqual(config["training"]["num_train_epochs"], 1)
        self.assertTrue(config["training"]["assistant_only_loss"])
        self.assertFalse(config["governance"]["full_fine_tuning_allowed"])
        self.assertFalse(config["governance"]["ki20_training_allowed"])


if __name__ == "__main__":
    unittest.main()
