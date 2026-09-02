# test_mix2k_v4.py - MIX2K v4 full-runtime spec과 구조 사실 validator를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    MAX_COMPLETION_TOKENS,
    RECORD_SCHEMA_VERSION,
    RUNTIME_BINDING_ID,
    RUNTIME_BINDING_SCHEMA,
    Mix2KV4ContractError,
    _pillar_field_claim_coverage,
    flatten_runtime_facts,
    required_fact_errors,
    sentence_count,
    structural_claim_errors,
    validate_draft,
    validate_spec,
)
from scripts.data.mix2k_v4_finalize import select_training_max_length
from scripts.data.mix2k_v4_teachers import (
    CODEX_DISABLED_FEATURES,
    _draft_schema,
    _import_seed_drafts,
    _mandatory_answer_checklist,
    _normalize_draft_answer_layout,
    _normalize_draft_answer_particles,
    _selection,
    draft_prompt,
    review_prompt,
    subscription_environment,
)
from scripts.data.mix2k_v4_teachers import _parser as teacher_parser
from scripts.runtime.chart_day_model_projection import (
    model_projection_digest,
    normalize_model_period_projection,
)
from scripts.training.mix2k_v4_lora import DEFAULT_CONFIG as LORA_CONFIG
from scripts.training.mix2k_v4_lora import (
    Mix2KV4LoRAError,
    acquire_mix2k_v4_gpu_lock,
)
from scripts.training.mix2k_v4_lora import (
    _validate_config as validate_lora_config,
)


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
                        "stem_element": "토",
                        "branch_element": "토",
                        "stem_yin_yang": "양",
                        "branch_yin_yang": "양",
                        "stem_ten_god": "정재",
                        "branch_ten_god": "편재",
                        "hidden_stems": ["戊", "乙", "癸"],
                    },
                    "month": {
                        "ganzhi": "甲子",
                        "stem": "甲",
                        "branch": "子",
                        "stem_element": "목",
                        "branch_element": "수",
                        "stem_yin_yang": "양",
                        "branch_yin_yang": "양",
                        "stem_ten_god": "겁재",
                        "branch_ten_god": "편인",
                        "hidden_stems": ["癸"],
                    },
                    "day": {
                        "ganzhi": "乙丑",
                        "stem": "乙",
                        "branch": "丑",
                        "stem_element": "목",
                        "branch_element": "토",
                        "stem_yin_yang": "음",
                        "branch_yin_yang": "음",
                        "stem_ten_god": "일간",
                        "branch_ten_god": "편재",
                        "hidden_stems": ["己", "癸", "辛"],
                    },
                    "hour": {
                        "ganzhi": "壬午",
                        "stem": "壬",
                        "branch": "午",
                        "stem_element": "수",
                        "branch_element": "화",
                        "stem_yin_yang": "양",
                        "branch_yin_yang": "양",
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
        "snapshot_sha256": model_projection_digest(value),
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
    def test_teacher_provider_only_preserves_explicit_provider_choice(self) -> None:
        arguments = teacher_parser().parse_args(
            [
                "full",
                "--spec-build",
                "/tmp/spec-build",
                "--provider-only",
                "codex",
                "--seed-target",
                "/tmp/old-teacher-target",
            ]
        )
        self.assertEqual(arguments.provider_only, "codex")
        self.assertEqual(arguments.seed_target, Path("/tmp/old-teacher-target"))

    def test_seed_import_revalidates_drafts_without_reusing_peer_pass(self) -> None:
        spec = _spec()
        answer = (
            "원국 전체는 연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午입니다.\n"
            "2026-09-02의 연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다.\n"
            "乙丑는 원국의 일주이고, 원국과 선택 날짜는 서로 다른 사실로 구분하면 됩니다."
        )
        draft = {
            "record_id": spec["id"],
            "answer": answer,
            "used_fact_paths": [],
            "used_fact_values": [
                "戊辰",
                "甲子",
                "乙丑",
                "壬午",
                "2026-09-02",
                "丙午",
                "丙申",
                "己卯",
            ],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        source_record = {
            "status": "accepted",
            "rewrites_used": 1,
            "current_draft": draft,
            "draft_attempts": [
                {
                    "provider": "claude",
                    "deterministic_pass": True,
                    "draft": draft,
                }
            ],
        }

        def empty_state() -> dict[str, object]:
            return {
                "mode": "full",
                "provider_calls": 0,
                "selection_order": [spec["id"]],
                "records": {
                    spec["id"]: {
                        "status": "needs_draft",
                        "rewrites_used": 0,
                        "feedback": "",
                        "draft_attempts": [],
                        "review_attempts": [],
                        "current_draft": None,
                        "accepted": None,
                    }
                },
            }

        seed_state = {
            "schema_version": "1.0.0",
            "dataset_version": DATASET_VERSION,
            "mode": "full",
            "selection_order": [spec["id"]],
            "records": {spec["id"]: source_record},
        }
        state = empty_state()
        report = _import_seed_drafts(
            state=state,
            seed_state=seed_state,
            seed_state_sha256="a" * 64,
            specs_by_id={spec["id"]: spec},
        )
        self.assertEqual(report["imported_current_drafts"], 1)
        self.assertFalse(report["peer_review_reused"])
        imported = state["records"][spec["id"]]
        self.assertEqual(imported["status"], "needs_review")
        self.assertIsNone(imported["accepted"])
        self.assertTrue(imported["draft_attempts"][0]["imported_from_seed"])
        self.assertTrue(imported["draft_attempts"][0]["particle_normalized"])
        self.assertIn("乙丑은 원국의 일주", imported["current_draft"]["answer"])

        chart_spec = deepcopy(spec)
        chart_spec["task_axis"] = "chart_facts_natural_explanation"
        rejected_state = empty_state()
        rejected = _import_seed_drafts(
            state=rejected_state,
            seed_state=seed_state,
            seed_state_sha256="b" * 64,
            specs_by_id={chart_spec["id"]: chart_spec},
        )
        self.assertEqual(rejected["imported_current_drafts"], 0)
        self.assertEqual(rejected["rejected_current_drafts"], 1)
        self.assertEqual(rejected["rejection_counts"]["unrequested_period_fact"], 1)
        self.assertEqual(
            rejected_state["records"][spec["id"]]["status"], "needs_draft"
        )

    def test_codex_teacher_disables_host_read_tools(self) -> None:
        self.assertTrue(
            {
                "apps",
                "browser_use",
                "code_mode_host",
                "computer_use",
                "shell_tool",
                "unified_exec",
                "view_image",
            }.issubset(CODEX_DISABLED_FEATURES)
        )

    def test_training_and_evaluation_share_one_gpu_lock(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v4-lock-") as directory:
            artifact_root = Path(directory)
            descriptor = acquire_mix2k_v4_gpu_lock(artifact_root)
            try:
                with self.assertRaises(Mix2KV4LoRAError):
                    acquire_mix2k_v4_gpu_lock(artifact_root)
            finally:
                os.close(descriptor)
            released = acquire_mix2k_v4_gpu_lock(artifact_root)
            os.close(released)

    def test_gpu_lock_is_shared_across_worktrees(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="mix2k-v4-lock-a-") as first,
            tempfile.TemporaryDirectory(prefix="mix2k-v4-lock-b-") as second,
        ):
            descriptor = acquire_mix2k_v4_gpu_lock(Path(first))
            try:
                with self.assertRaises(Mix2KV4LoRAError):
                    acquire_mix2k_v4_gpu_lock(Path(second))
            finally:
                os.close(descriptor)

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
                "일간",
            ],
            "soft_interpretation_used": False,
            "limitations": ["원국×기간 relation이 제공되지 않음"],
            "self_check": "PASS",
        }
        self.assertEqual(validate_draft(spec, draft), draft)

        negated_prediction = deepcopy(draft)
        negated_prediction["answer"] += (
            "\n어떤 일이 반드시 생긴다고 단정하지 않는 편이 좋습니다."
        )
        self.assertEqual(
            validate_draft(spec, negated_prediction),
            negated_prediction,
        )
        asserted_prediction = deepcopy(draft)
        asserted_prediction["answer"] += "\n어떤 일이 반드시 생긴다고 말합니다."
        with self.assertRaisesRegex(Mix2KV4ContractError, "확정적 사건 예측"):
            validate_draft(spec, asserted_prediction)

        contradictory = deepcopy(draft)
        contradictory["answer"] = contradictory["answer"].replace(
            "일주 乙丑",
            "乙丑은 원국의 일주가 아니라, 원국의 일주는 乙丑",
            1,
        )
        with self.assertRaisesRegex(
            Mix2KV4ContractError,
            "natal_day_expected_fact_negated",
        ):
            validate_draft(spec, contradictory)

    def test_training_period_projection_preserves_dashboard_v1_11_content(self) -> None:
        source = deepcopy(_binding()["value"]["period"])
        projected = normalize_model_period_projection(source)

        self.assertEqual(projected, source)
        self.assertIsNot(projected, source)
        projected["limitations"].append("변경 확인")
        self.assertNotEqual(projected, source)

    def test_teacher_provenance_ignores_ordinary_sanggwan_word(self) -> None:
        spec = validate_spec(_spec())
        answer = (
            "원국 전체는 戊辰·甲子·乙丑·壬午이며, 그중 乙丑은 일주입니다.\n"
            "그 사실과 상관없이 2026-09-02의 연간지는 丙午, 월간지는 丙申, 일진은 己卯로 구분합니다.\n"
            "원국과 날짜의 관계 계산은 제공되지 않아 합충이나 신강약은 단정하지 않습니다."
        )
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
            "연결된 승인 원국 사실 乙丑입니다.": "natal_day_called_full_chart",
            "원국은 乙丑을 뜻합니다.": "natal_day_called_full_chart",
            "乙丑이 바로 원국입니다.": "natal_day_called_full_chart",
            "乙丑은 원국 전체다.": "natal_day_called_full_chart",
            "乙丑이 원국 전체예요.": "natal_day_called_full_chart",
            "乙丑은 원국이야.": "natal_day_called_full_chart",
            "원국 전체가 乙丑으로 이루어집니다.": "natal_day_called_full_chart",
            "원국의 간지는 乙丑입니다.": "natal_day_called_full_chart",
            "원국 간지는 乙丑입니다.": "natal_day_called_full_chart",
            "오늘 일진은 丙午입니다.": "period_year_called_day_ganzhi",
            "승인된 날짜 사실 丙午입니다.": "period_year_called_day_ganzhi",
            "오늘은 丙午입니다.": "period_year_called_day_ganzhi",
            "오늘의 간지는 丙午입니다.": "period_year_called_day_ganzhi",
            "올해는 己卯입니다.": "period_year_ganzhi_relative_label_confusion:己卯",
            "이번 달 간지는 丙午입니다.": "period_month_ganzhi_relative_label_confusion:丙午",
            "오늘은 丙申입니다.": "period_day_ganzhi_relative_label_confusion:丙申",
            "이날은 丙申입니다.": "period_day_ganzhi_relative_label_confusion:丙申",
            "2026-09-02의 간지는 丙午입니다.": "period_year_called_day_ganzhi",
            "해당 날짜의 간지는 丙午입니다.": "period_year_called_day_ganzhi",
            "오늘 날짜의 원국에는 丙午가 있습니다.": "period_ganzhi_called_natal_chart:丙午",
            "원국에는 乙丑이 있고, 오늘 날짜의 원국에는 丙午가 있습니다.": "period_ganzhi_called_natal_chart:丙午",
            "원국은 乙丑 하나입니다.": "natal_day_called_full_chart",
            "사주 원국은 乙丑 하나로 구성됩니다.": "natal_day_called_full_chart",
            "원국을 한마디로 하면 乙丑입니다.": "natal_day_called_full_chart",
            "己卯는 세운입니다.": "period_day_called_seun",
            "제공된 원국은 庚子입니다.": "unprovided_ganzhi:庚子",
            "일주의 십신은 정관입니다.": "unprovided_ten_god:정관",
            "乙木이 丑에 뿌리를 두고 있습니다.": "unsupported_structural_claim:rooting",
            "원국의 연주(乙丑), 월주(甲子), 일주(戊辰), 시주(壬午)입니다.": (
                "natal_year_label_confusion:乙丑"
            ),
            "2026-09-02의 연간지(己卯), 월간지(丙申), 일진(丙午)입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "오늘(2026년 9월 2일)의 일진(丙午)입니다. 실제 확인값 己卯도 있습니다.": (
                "period_day_ganzhi_label_confusion:丙午"
            ),
            "원국의 연주인 乙丑입니다.": "natal_year_label_confusion:乙丑",
            "연주의 간지는 乙丑입니다.": "natal_year_label_confusion:乙丑",
            "乙丑은 이 원국의 연주입니다.": "natal_year_label_confusion:乙丑",
            "오늘 일진인 丙午입니다.": "period_day_ganzhi_label_confusion:丙午",
            "일진의 간지는 丙午입니다.": "period_day_ganzhi_label_confusion:丙午",
            "연간지인 己卯입니다.": "period_year_ganzhi_label_confusion:己卯",
            "己卯는 이 날짜의 연간지입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "월간지, 己卯입니다.": "period_month_ganzhi_label_confusion:己卯",
            "일진은 丙午가 아니라 丙申입니다.": (
                "period_day_ganzhi_label_confusion:丙申"
            ),
            "연간지는 己卯가 아니라 丙申입니다.": (
                "period_year_ganzhi_label_confusion:丙申"
            ),
            "연주는 乙丑이 아니라 甲子입니다.": "natal_year_label_confusion:甲子",
            "일간은 丙이 아니라 甲입니다.": "day_master_confusion:甲",
            "연주·월주·일주·시주는 甲子·戊辰·壬午·乙丑입니다.": (
                "natal_year_label_confusion:甲子"
            ),
            "연주와 월주는 甲子와 戊辰이고, 일주와 시주는 壬午와 乙丑입니다.": (
                "natal_year_label_confusion:甲子"
            ),
            "연주/월주/일주/시주 순: 甲子/戊辰/壬午/乙丑.": (
                "natal_year_label_confusion:甲子"
            ),
            "연간지와 월간지는 丙申과 丙午이고, 일진은 己卯입니다.": (
                "period_year_ganzhi_label_confusion:丙申"
            ),
            "오늘은 2026년 9월 3일이며 일진은 己卯입니다.": (
                "unprovided_date:2026-09-03"
            ),
            "오늘은 2026. 9. 3.이고 일진은 己卯입니다.": ("unprovided_date:2026-09-03"),
            "2026/09/03의 연간지는 丙午입니다.": ("unprovided_date:2026-09-03"),
            "9월 3일의 일진은 己卯입니다.": "unprovided_date:2026-09-03",
            "9/3의 일진은 己卯입니다.": "unprovided_date:2026-09-03",
            "09.03.의 일진은 己卯입니다.": "unprovided_date:2026-09-03",
            "3일의 일진은 己卯입니다.": "unprovided_date:2026-09-03",
            "올해 9월 3일의 일진은 己卯입니다.": "unprovided_date:2026-09-03",
            "2026년의 9월 3일은 己卯입니다.": "unprovided_date:2026-09-03",
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

    def test_validator_rejects_allowed_values_attached_to_wrong_schema_position(
        self,
    ) -> None:
        spec = _spec()
        cases = {
            "연주의 천간은 甲입니다.": "natal_year_stem_confusion:甲",
            "연주의 천간 십신은 겁재입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "연주의 천간 戊는 겁재입니다.": ("natal_year_stem_ten_god_confusion:겁재"),
            "일주의 지장간은 戊·癸입니다.": ("natal_day_hidden_stem_confusion:戊"),
            "일간은 乙이고 오행은 화입니다.": "day_master_element_confusion:화",
            "일간(丙)입니다.": "day_master_confusion:丙",
            "일간인 丙입니다.": "day_master_confusion:丙",
            "丙이 이 원국의 일간입니다.": "day_master_confusion:丙",
            "일간 乙은 양입니다.": "day_master_yin_yang_confusion:양",
            "표면 오행은 목 3개입니다.": "surface_five_elements_목_confusion:3",
            "2026년의 간지는 己卯이고 2일은 丙午입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "겁재는 연주의 천간 십신입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "편인은 연주의 지지 십신입니다.": (
                "natal_year_branch_ten_god_confusion:편인"
            ),
            "甲은 연주의 천간입니다.": "natal_year_stem_confusion:甲",
            "子는 연주의 지지입니다.": "natal_year_branch_confusion:子",
            "戊·乙·癸는 일주의 지장간입니다.": ("natal_day_hidden_stem_confusion:戊"),
            "식신은 일주의 branch ten-god입니다.": (
                "natal_day_branch_ten_god_confusion:식신"
            ),
            "비견은 시주의 stem ten-god입니다.": (
                "natal_hour_stem_ten_god_confusion:비견"
            ),
            "연주의 천간 값은 甲이고 지지 값은 子입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            (
                "연주의 천간에 배정된 십신은 겁재이고 "
                "지지에 배정된 십신은 편인입니다."
            ): "natal_year_stem_ten_god_confusion:겁재",
            "연주의 지장간 목록은 甲·乙·癸입니다.": (
                "natal_year_hidden_stem_confusion:甲"
            ),
            "연주의 천간은 목 기운의 甲이고 지지는 수 기운의 子입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            "연간지 값으로 등록된 것은 己卯입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "일진에 해당하는 값은 丙午입니다.": (
                "period_day_ganzhi_label_confusion:丙午"
            ),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(spec, answer))

    def test_validator_rejects_explicit_natural_position_claims(self) -> None:
        cases = {
            "연주의 천간에 놓인 글자는 甲입니다.": "natal_year_stem_confusion:甲",
            "연주를 보면 천간 쪽에 甲이 있습니다.": "natal_year_stem_confusion:甲",
            "해당 날짜에서 연간지로 확인되는 간지는 己卯입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "연주 기둥은 甲子입니다.": "natal_year_label_confusion:甲子",
            "연주라는 기둥은 甲子입니다.": "natal_year_label_confusion:甲子",
            "연주의 간지 두 글자는 甲子입니다.": (
                "natal_year_label_confusion:甲子"
            ),
            "甲子 기둥이 연주입니다.": "natal_year_label_confusion:甲子",
            "연간지 항목은 己卯입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "연간지라는 값은 己卯입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "己卯 값이 연간지입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "2026-09-02의 하루 간지는 丙午입니다.": (
                "period_day_ganzhi_label_confusion:丙午"
            ),
            "연주에서 천간으로 쓰이는 글자는 甲입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            "연주 천간 戊는 오행으로 보면 목입니다.": (
                "natal_year_stem_element_confusion:목"
            ),
            "연주 천간 戊는 오행상 목입니다.": (
                "natal_year_stem_element_confusion:목"
            ),
            "일주의 지장간 구성은 戊, 癸, 辛입니다.": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            "연주의 천간에 해당하는 십신은 겁재입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "연주의 천간 역할은 겁재입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "연주의 천간 戊은 목 기운입니다.": (
                "natal_year_stem_element_confusion:목"
            ),
            "연주의 천간 戊은 목 오행입니다.": (
                "natal_year_stem_element_confusion:목"
            ),
            "연주의 지지 辰은 수 기운입니다.": (
                "natal_year_branch_element_confusion:수"
            ),
            "乙은 화 오행의 음 기운인 일간입니다.": (
                "day_master_element_confusion:화"
            ),
            "연주 천간의 십신 자리에 겁재가 놓입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "연주에서 천간 쪽 역할은 겁재입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "戊辰은 연주가 아니라 월주입니다.": (
                "natal_month_label_confusion:戊辰"
            ),
            "己卯는 일진이 아니라 연간지입니다.": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "원국은 戊辰입니다.": "natal_single_pillar_called_full_chart:戊辰",
            "원국에는 戊辰만 있습니다.": (
                "natal_single_pillar_called_full_chart:戊辰"
            ),
            "원국을 이루는 간지는 戊辰뿐입니다.": (
                "natal_single_pillar_called_full_chart:戊辰"
            ),
            "원국은 하나의 기둥인 戊辰으로 되어 있습니다.": (
                "natal_single_pillar_called_full_chart:戊辰"
            ),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_validator_accepts_natural_boundaries_lists_and_durations(self) -> None:
        answers = (
            "연주의 지장간은 戊과 乙, 癸입니다.",
            "연주의 지장간은 戊, 乙과 癸입니다.",
            "연주의 지장간은 戊·乙 그리고 癸입니다.",
            "연주는 甲子로 보면 안 되고 戊辰으로 읽어야 합니다.",
            "甲子는 연주가 아니라 월주입니다.",
            "丙午는 일진이 아니라 연간지입니다.",
            "합충을 새로 만들어 말할 수 없습니다.",
            "합충은 제가 임의로 계산할 수 없습니다.",
            "합충을 직접 계산해서는 안 됩니다.",
            "신강약을 여기서 새로 정할 수 없습니다.",
            "용신은 이 자료만으로 정할 수 없습니다.",
            "오늘부터 3일 동안은 무리하지 마세요.",
            "오늘 흐름을 보고 3일 뒤 다시 점검해 보세요.",
            "오늘인 9월 2일부터 3일 동안 기록하세요.",
            "오늘부터 3일치 기록을 모아 보세요.",
            "오늘부터 3일분 기록을 모아 보세요.",
            "오늘부터 3일 정도 기록을 모아 보세요.",
            "오늘부터 3일 연속 기록을 모아 보세요.",
            "원국은 乙丑, 戊辰, 甲子, 壬午 네 기둥을 모두 포함합니다.",
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_validator_checks_case_particles_and_position_carry_forward(self) -> None:
        shuffled = (
            "연주에 甲子가 있고 월주에 乙丑이 있으며, "
            "일주에 戊辰, 시주에 壬午가 있습니다.\n"
            "일진에 丙午를 기록합니다.\n"
            "오늘 일진은 己卯이고 원국은 戊辰입니다."
        )
        errors = structural_claim_errors(_spec(), shuffled)
        for expected in (
            "natal_year_label_confusion:甲子",
            "natal_month_label_confusion:乙丑",
            "natal_day_label_confusion:戊辰",
            "period_day_ganzhi_label_confusion:丙午",
            "natal_single_pillar_called_full_chart:戊辰",
        ):
            self.assertIn(expected, errors)

        for answer in (
            "연주에는 甲子가 있습니다.",
            "연주에 해당하는 간지는 甲子입니다.",
            "연주로는 甲子를 씁니다.",
            "연주 자리에 甲子가 놓입니다.",
            "일진에는 丙午가 있습니다.",
            "일진으로는 丙午를 씁니다.",
            "일진 자리에 丙午가 놓입니다.",
            "오늘의 일진으로 丙午가 기록됐습니다.",
        ):
            with self.subTest(answer=answer):
                self.assertTrue(structural_claim_errors(_spec(), answer))

        spec = deepcopy(_spec())
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘."
        )
        complete = (
            "연주의 천간 戊과 지지 辰은 오행이 각각 토와 토이고 음양은 각각 양과 양입니다. "
            "월주의 천간 甲과 지지 子는 오행이 각각 목과 수이고 음양은 각각 양과 양입니다. "
            "일주의 천간 乙과 지지 丑은 오행이 각각 목과 토이고 음양은 각각 음과 음입니다. "
            "시주의 천간 壬과 지지 午는 오행이 각각 수와 화이고 음양은 각각 양과 양입니다."
        )
        self.assertEqual(structural_claim_errors(spec, complete), [])
        self.assertEqual(required_fact_errors(spec, complete), [])

        natural_parallel = complete.replace("각각 ", "")
        self.assertEqual(structural_claim_errors(spec, natural_parallel), [])
        self.assertEqual(required_fact_errors(spec, natural_parallel), [])

        swapped = complete.replace(
            "월주의 천간 甲과 지지 子는 오행이 각각 목과 수",
            "월주의 천간 甲과 지지 子는 오행이 각각 수와 목",
        )
        self.assertIn(
            "natal_month_stem_element_confusion:수",
            structural_claim_errors(spec, swapped),
        )
        natural_swapped = natural_parallel.replace(
            "월주의 천간 甲과 지지 子는 오행이 목과 수",
            "월주의 천간 甲과 지지 子는 오행이 수와 목",
        )
        self.assertIn(
            "natal_month_stem_element_confusion:수",
            structural_claim_errors(spec, natural_swapped),
        )
        cross_swapped = (
            "연주와 월주의 천간은 각각 戊와 甲이고, "
            "오행은 같은 순서로 각각 목과 토입니다."
        )
        self.assertIn(
            "natal_year_stem_element_confusion:목",
            structural_claim_errors(spec, cross_swapped),
        )
        carried_swapped = (
            "연주와 월주의 천간은 각각 戊와 甲이고, "
            "오행은 같은 순서로 목과 수이며, "
            "음양은 같은 순서로 음과 양입니다."
        )
        carried_errors = structural_claim_errors(spec, carried_swapped)
        self.assertIn("natal_year_stem_element_confusion:목", carried_errors)
        self.assertIn("natal_month_stem_element_confusion:수", carried_errors)
        self.assertIn("natal_year_stem_yin_yang_confusion:음", carried_errors)

        natural = (
            "연주의 천간 戊는 토 오행이며 양의 성질이고, "
            "지지 辰은 토 오행이며 양의 성질입니다."
        )
        coverage = _pillar_field_claim_coverage(natural)
        self.assertIn(("year", "stem_yin_yang", "양"), coverage)
        self.assertIn(("year", "branch_yin_yang", "양"), coverage)

    def test_runtime_dot_path_claims_are_position_checked(self) -> None:
        cases = {
            "period.year_ganzhi = 己卯": "period_year_ganzhi_label_confusion:己卯",
            "period.month_ganzhi(丙午)": "period_month_ganzhi_label_confusion:丙午",
            "丙午는 period.day_ganzhi": "period_day_ganzhi_label_confusion:丙午",
            "natal.pillars.year.ganzhi = 乙丑": "natal_year_ganzhi_confusion:乙丑",
            "chart.hard_facts.pillars.day.ganzhi(戊辰)": (
                "natal_day_ganzhi_confusion:戊辰"
            ),
            "natal.pillars.year.stem = 甲": "natal_year_stem_confusion:甲",
            "natal.pillars.year.branch = 子": "natal_year_branch_confusion:子",
            "chart.hard_facts.pillars.year.stem_element = 화": (
                "natal_year_stem_element_confusion:화"
            ),
            "chart.hard_facts.pillars.year.branch_yin_yang = 음": (
                "natal_year_branch_yin_yang_confusion:음"
            ),
            "natal.pillars.day.hidden_stems=[戊,乙,癸]": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            "natal.pillars.year.stem_ten_god=비견": (
                "natal_year_stem_ten_god_confusion:비견"
            ),
            "natal.pillars.day.branch_ten_god=식신": (
                "natal_day_branch_ten_god_confusion:식신"
            ),
            "day_master.stem = 丙": "day_master_confusion:丙",
            "day_master.five_element = 화": "day_master_element_confusion:화",
            "day_master.yin_yang = 양": "day_master_yin_yang_confusion:양",
            "surface_five_elements.목=3": "surface_five_elements_목_confusion:3",
            "period.day_ganzhi = 丙午가 아니라 丙申입니다.": (
                "period_day_ganzhi_label_confusion:丙申"
            ),
            '"year_ganzhi": "己卯"': "period_year_ganzhi_label_confusion:己卯",
            "`period.year_ganzhi` = `己卯`": (
                "period_year_ganzhi_label_confusion:己卯"
            ),
            "period.year_ganzhi: `己卯`": ("period_year_ganzhi_label_confusion:己卯"),
            "“day_master.stem”: “丙”": "day_master_confusion:丙",
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_natural_pillar_field_claims_are_position_checked(self) -> None:
        cases = {
            "연주에서는 甲이 천간이고 子가 지지입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            "연주의 甲은 천간, 子는 지지입니다.": "natal_year_stem_confusion:甲",
            "연주 천간 자리는 甲이고 지지 자리는 子입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            "연주의 戊에는 겁재가, 辰에는 편인이 배정됩니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "연주에서 戊의 십신은 겁재, 辰의 십신은 편인입니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "일주 丑 속에는 戊·癸·辛이 들어 있습니다.": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            "일주의 丑 안에는 戊, 癸, 辛이 숨어 있습니다.": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            "월주 子 속에는 甲이 들어 있습니다.": (
                "natal_month_hidden_stem_confusion:甲"
            ),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_pillar_element_and_yin_yang_claims_are_position_checked(self) -> None:
        invalid = {
            "연주의 천간 戊의 오행은 화이고 음양은 양입니다.": (
                "natal_year_stem_element_confusion:화"
            ),
            "연주의 지지 辰의 오행은 토이고 음양은 음입니다.": (
                "natal_year_branch_yin_yang_confusion:음"
            ),
            "연주의 천간은 토·음이고 지지는 토·양입니다.": (
                "natal_year_stem_yin_yang_confusion:음"
            ),
            "연주의 천간 戊는 음화이고 지지 辰은 양토입니다.": (
                "natal_year_stem_element_confusion:화"
            ),
        }
        for answer, expected in invalid.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

        for answer in (
            "일간 丁을 중심으로 함께 볼 수 있는 원국의 겉구성입니다.",
            "일간 丁을 중심으로 함께 살펴볼 수 있는 원국의 겉구성입니다.",
        ):
            with self.subTest(answer=answer):
                spec = deepcopy(_spec())
                stem_path = "chart.hard_facts.day_master.stem"
                spec["allowed_fact_values"][
                    spec["allowed_fact_paths"].index(stem_path)
                ] = "丁"
                self.assertNotIn(
                    "day_master_element_confusion:수",
                    structural_claim_errors(spec, answer),
                )

        for valid in (
            "연주의 천간 戊(토·양), 지지 辰(토·양)입니다.",
            "연주의 천간 戊(양·토), 지지 辰(양·토)입니다.",
            "연주의 천간 戊는 양토이고 지지 辰은 양의 토입니다.",
            "연주는 천간 戊가 양토이고 지지 辰도 양토입니다.",
            (
                "연주는 천간 戊의 오행은 토이고 음양은 양이며, "
                "지지 辰의 오행은 토이고 음양도 양입니다."
            ),
        ):
            with self.subTest(valid=valid):
                self.assertEqual(structural_claim_errors(_spec(), valid), [])

    def test_pillar_detail_coverage_accepts_common_natural_forms(self) -> None:
        expected = {
            ("year", "stem", "戊"),
            ("year", "stem_element", "토"),
            ("year", "stem_yin_yang", "양"),
            ("year", "branch", "辰"),
            ("year", "branch_element", "토"),
            ("year", "branch_yin_yang", "양"),
        }
        for answer in (
            "연주는 천간 戊가 양토이고 지지 辰도 양토입니다.",
            (
                "연주는 천간 戊의 오행은 토이고 음양은 양이며, "
                "지지 辰의 오행은 토이고 음양도 양입니다."
            ),
            "연주는 천간 戊(양·토), 지지 辰(토·양)입니다.",
            "연주는 천간 戊(오행 토, 음양 양)과 지지 辰(오행 토, 음양 양)입니다.",
            (
                "연주는 천간 戊로 오행은 토, 음양은 양이고, "
                "지지 辰으로 오행은 토, 음양은 양입니다."
            ),
            (
                "연주는 천간은 양의 토 기운을 가진 戊이고, "
                "지지는 양의 토 기운을 가진 辰입니다."
            ),
        ):
            with self.subTest(answer=answer):
                self.assertTrue(expected <= _pillar_field_claim_coverage(answer))

        wrong_labeled_compact = (
            "연주는 천간 戊(오행 토, 음양 음)과 "
            "지지 辰(오행 토, 음양 양)입니다."
        )
        self.assertIn(
            "natal_year_stem_yin_yang_confusion:음",
            structural_claim_errors(_spec(), wrong_labeled_compact),
        )
        wrong_labeled_sequence = (
            "연주는 천간 戊로 오행은 토, 음양은 음이고, "
            "지지 辰으로 오행은 토, 음양은 양입니다."
        )
        self.assertIn(
            "natal_year_stem_yin_yang_confusion:음",
            structural_claim_errors(_spec(), wrong_labeled_sequence),
        )
        corrected_sequence = (
            "연주는 천간 戊로 오행은 금이 아니라 토, "
            "음양은 음이 아니라 양이고, 지지 辰입니다."
        )
        coverage = _pillar_field_claim_coverage(corrected_sequence)
        self.assertIn(("year", "stem_element", "토"), coverage)
        self.assertIn(("year", "stem_yin_yang", "양"), coverage)
        corrected_element_wrong_yin_yang = (
            "연주는 천간 戊로 오행은 금이 아니라 토, 음양은 음입니다."
        )
        mixed_coverage = _pillar_field_claim_coverage(
            corrected_element_wrong_yin_yang
        )
        self.assertIn(("year", "stem_element", "토"), mixed_coverage)
        self.assertIn(("year", "stem_yin_yang", "음"), mixed_coverage)
        self.assertIn(
            "natal_year_stem_yin_yang_confusion:음",
            structural_claim_errors(_spec(), corrected_element_wrong_yin_yang),
        )
        for wrong, expected_error in (
            (
                "연주는 천간 戊로 오행은 토가 아니라 금, 음양은 양입니다.",
                "natal_year_stem_element_confusion:금",
            ),
            (
                "연주는 천간 戊로 오행은 토, 음양은 양이 아니고 음입니다.",
                "natal_year_stem_yin_yang_confusion:음",
            ),
        ):
            with self.subTest(wrong=wrong):
                self.assertIn(expected_error, structural_claim_errors(_spec(), wrong))

    def test_natural_pillar_corrections_use_the_final_value(self) -> None:
        valid = (
            "연주의 천간은 甲이 아니라 戊입니다.",
            "연주의 戊에는 겁재가 아니라 정재가 배정됩니다.",
            "일주의 지장간은 戊·癸·辛이 아니라 己·癸·辛입니다.",
            "일주 丑 속에는 戊·癸·辛이 아니라 己·癸·辛이 들어 있습니다.",
        )
        for answer in valid:
            with self.subTest(answer=answer, state="valid"):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

        invalid = {
            "연주의 천간은 戊가 아니라 甲입니다.": "natal_year_stem_confusion:甲",
            "연주의 戊에는 정재가 아니라 겁재가 배정됩니다.": (
                "natal_year_stem_ten_god_confusion:겁재"
            ),
            "일주의 지장간은 己·癸·辛이 아니라 戊·癸·辛입니다.": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            "일주 丑 속에는 己·癸·辛이 아니라 戊·癸·辛이 들어 있습니다.": (
                "natal_day_hidden_stem_confusion:戊"
            ),
        }
        for answer, expected in invalid.items():
            with self.subTest(answer=answer, state="invalid"):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_hidden_stem_full_list_rejects_extra_duplicate_or_reorder(self) -> None:
        for answer in (
            "일주의 지장간은 己·癸·辛·辛입니다.",
            "일주의 지장간은 己·辛·癸·己입니다.",
            "연주의 지장간은 戊·乙·癸·戊입니다.",
            "월주의 지장간은 癸·癸입니다.",
        ):
            with self.subTest(answer=answer):
                self.assertTrue(
                    any(
                        "hidden_stem" in error
                        for error in structural_claim_errors(_spec(), answer)
                    )
                )

    def test_entity_ten_god_parser_ignores_ordinary_sanggwan_words(self) -> None:
        for answer in (
            "연주의 천간 戊는 상관없이 일상을 단정하는 값이 아닙니다.",
            "연주에서 戊는 상관없는 정보라고 버리기보다 한 항목으로만 보세요.",
            "시주의 壬은 상관관계를 뜻하지 않습니다.",
            "연주의 천간 戊는 원국의 일간 乙과 다른 위치입니다.",
            "연주 戊辰과 달리, 甲이 천간인 기둥은 월주입니다.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_each_parallel_group_in_one_sentence_is_checked(self) -> None:
        cases = {
            (
                "연주와 일주는 각각 戊辰과 乙丑이고, "
                "연간지와 일진은 각각 己卯와 丙午입니다."
            ): "period_year_ganzhi_label_confusion:己卯",
            (
                "연주와 월주는 각각 戊辰과 甲子이며 "
                "일주와 시주는 각각 壬午와 乙丑입니다."
            ): "natal_day_label_confusion:壬午",
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_implicit_ordered_natal_and_period_sequences_are_checked(self) -> None:
        invalid = {
            "원국 네 기둥은 순서대로 甲子·戊辰·乙丑·壬午입니다.": (
                "natal_year_label_confusion:甲子"
            ),
            "선택 날짜의 세 간지는 순서대로 丙申·丙午·己卯입니다.": (
                "period_year_ganzhi_label_confusion:丙申"
            ),
        }
        for answer, expected in invalid.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

        for valid in (
            "원국 네 기둥은 순서대로 戊辰·甲子·乙丑·壬午입니다.",
            "선택 날짜의 세 간지는 순서대로 丙午·丙申·己卯입니다.",
        ):
            with self.subTest(valid=valid):
                self.assertEqual(structural_claim_errors(_spec(), valid), [])

    def test_parallel_pillar_field_vectors_and_matrices_are_checked(self) -> None:
        valid = (
            "연주의 천간 십신과 지지 십신은 각각 정재와 편재입니다.",
            "연주의 천간과 지지는 각각 戊와 辰입니다.",
            (
                "연주·월주·일주·시주의 천간 십신과 지지 십신은 각각 "
                "정재·편재, 겁재·편인, 일간·편재, 정인·식신입니다."
            ),
            (
                "연주·월주·일주·시주의 천간과 지지는 각각 "
                "戊·辰, 甲·子, 乙·丑, 壬·午입니다."
            ),
            "연주의 천간과 월주의 지지는 각각 戊와 子입니다.",
            "연주 천간과 월주 천간은 각각 戊와 甲입니다.",
            ("연주 천간, 월주 지지, 일주 천간, 시주 지지는 각각 戊, 子, 乙, 午입니다."),
            "연주 천간 십신과 월주 지지 십신은 각각 정재와 편인입니다.",
        )
        for answer in valid:
            with self.subTest(answer=answer, state="valid"):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

        invalid = {
            "연주의 천간과 지지는 각각 甲과 辰입니다.": (
                "natal_year_stem_confusion:甲"
            ),
            (
                "연주·월주·일주·시주의 천간과 지지는 각각 "
                "甲·辰, 戊·子, 壬·丑, 乙·午입니다."
            ): "natal_year_stem_confusion:甲",
        }
        for answer, expected in invalid.items():
            with self.subTest(answer=answer, state="invalid"):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

        detail_matrix = (
            "연주·월주·일주·시주의 천간은 각각 戊·甲·乙·壬입니다.\n"
            "같은 순서로 천간 오행은 각각 토·목·목·수이고 천간 음양은 각각 양·양·음·양입니다.\n"
            "같은 순서로 지지는 각각 辰·子·丑·午이고 지지 오행은 각각 토·수·토·화이며 지지 음양은 각각 양·양·음·양입니다."
        )
        spec = deepcopy(_spec())
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘."
        )
        self.assertEqual(structural_claim_errors(spec, detail_matrix), [])
        self.assertEqual(required_fact_errors(spec, detail_matrix), [])

    def test_validator_accepts_positioned_schema_facts(self) -> None:
        answer = (
            "연주는 戊辰이며 천간은 戊, 천간 십신은 정재입니다. "
            "지지는 辰이고 지지 십신은 편재이며 지장간은 戊·乙·癸입니다.\n"
            "일주는 乙丑이며 지장간은 己·癸·辛입니다.\n"
            "일간은 乙이고 오행은 목, 음양은 음입니다. 표면 오행은 목 2개입니다."
        )
        self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_validator_accepts_corrections_parallel_lists_and_ordinary_sanggwan(
        self,
    ) -> None:
        answers = (
            "丙午는 일진이 아니라 연간지이고, 己卯가 일진입니다.",
            "일진은 丙午가 아닌 己卯입니다.",
            "일진은 丙午가 아닌데, 정확히는 己卯입니다.",
            "일진은 丙午가 아니어서 己卯로 고쳐 읽어야 합니다.",
            "일진이 丙午라고 볼 수는 없고 己卯로 봐야 합니다.",
            "己卯는 세운이 아니라 선택 날짜의 일진입니다.",
            "乙丑은 원국 전체가 아니라 일주 한 자리입니다.",
            "연주, 월주, 일주, 시주는 각각 戊辰, 甲子, 乙丑, 壬午입니다.",
            "연간지, 월간지, 일진은 각각 丙午, 丙申, 己卯입니다.",
            "연간지·월간지·일진은 丙午·丙申·己卯입니다.",
            "연간지, 월간지, 일진 순서로 丙午, 丙申, 己卯입니다.",
            "그 사실과 상관없이 사용자의 실제 경험을 존중합니다.",
            "그 사실과 상관이 없어도 사용자의 실제 경험을 존중합니다.",
            "그 사실과 상관은 없어요. 경험을 먼저 보겠습니다.",
            "2026년 9월 2일의 일진은 己卯입니다.",
            "9월 2일의 일진은 己卯입니다.",
            "원국에서 일주는 乙丑입니다.",
            "연간지와 원국의 연주는 서로 다른 항목입니다. 戊辰은 이 원국의 연주입니다.",
            "일진을 설명한 뒤 원국의 일주는 乙丑입니다.",
            "일진과 연간지를 구분하면 丙午는 연간지, 己卯는 일진입니다.",
            "연간지와 일진은 서로 다르며, 앞의 丙午는 연간지이고 뒤의 己卯는 일진입니다.",
            "일진과 연간지의 차이는 丙午가 올해 값이고 己卯가 오늘 값이라는 점입니다.",
            "합충은 계산되지 않았습니다.",
            "통근이나 신강약 판정은 포함하지 않습니다.",
            "통근이나 신강약 같은 추가 판정은 포함되어 있지 않습니다.",
            "일진은 丙午가 아니라, 丙申은 월간지이고 己卯가 일진입니다.",
            "연간지는 己卯가 아니라, 己卯는 일진이고 丙午가 연간지입니다.",
            "연주는 乙丑이 아니라, 乙丑은 일주이고 戊辰이 연주입니다.",
            "일간은 丙이 아니라, 丙은 날짜의 연간 천간이고 乙이 일간입니다.",
            "연주의 천간 戊는 일간과 다른 위치입니다.",
            (
                "원국 전체는 戊辰·甲子·乙丑·壬午이며, "
                "乙丑 하나만 원국 전체라는 뜻은 아닙니다."
            ),
            (
                "일주 乙丑은 원국을 구성하는 네 기둥 중 하나이며, "
                "戊辰·甲子·壬午와 함께 원국 전체를 이룹니다."
            ),
            "연주는 戊辰입니다. 이 원국의 일간 천간은 乙입니다.",
            "연주는 戊辰입니다. 일간의 천간은 乙이고 오행은 목입니다.",
            (
                "연주 戊辰을 먼저 확인합니다. "
                "별도로 일간의 천간 乙을 기준으로 설명합니다."
            ),
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

        self.assertIn(
            "period_day_ganzhi_label_confusion:丙申",
            structural_claim_errors(_spec(), "일진은 丙午가 아닌 丙申입니다."),
        )
        for answer in (
            "일진은 己卯가 아니어서 丙午로 고쳐 읽어야 합니다.",
            "일진이 己卯라고 볼 수는 없고 丙午로 봐야 합니다.",
        ):
            with self.subTest(answer=answer):
                self.assertIn(
                    "period_day_ganzhi_label_confusion:丙午",
                    structural_claim_errors(_spec(), answer),
                )

    def test_unsupported_claim_negation_does_not_mask_later_affirmative_claim(
        self,
    ) -> None:
        answers = (
            "관계 계산은 제공되지 않았습니다. 하지만 乙과 己는 합을 이룹니다.",
            "관계 계산은 제공되지 않았지만 乙과 己는 합이 성립합니다.",
            "관계 계산은 제공되지 않았고 乙과 己는 합이 성립합니다.",
            "신강약은 제공되지 않았습니다. 그래도 이 원국은 신강입니다.",
            "대운은 확인되지 않았습니다. 현재 대운은 甲子입니다.",
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertTrue(
                    any(
                        error.startswith("unsupported_structural_claim:")
                        for error in structural_claim_errors(_spec(), answer)
                    )
                )

    def test_strength_claim_variants_are_blocked(self) -> None:
        for answer in (
            "이 원국은 신강합니다.",
            "전체적으로 신약하다고 봅니다.",
        ):
            with self.subTest(answer=answer):
                self.assertIn(
                    "unsupported_structural_claim:strength_pattern_yongshin",
                    structural_claim_errors(_spec(), answer),
                )

    def test_earlier_hedge_does_not_mask_later_wrong_label(self) -> None:
        for answer in (
            "근거가 없으므로 오늘 일진은 丙午입니다.",
            "계산하지 않았으므로 오늘 일진은 丙午입니다.",
            "확인되지 않았더라도 오늘 일진은 丙午입니다.",
            "제공되지 않았음에도 오늘 일진은 丙午입니다.",
            "단정하지는 않지만 오늘 일진은 丙午입니다.",
        ):
            with self.subTest(answer=answer):
                self.assertIn(
                    "period_year_called_day_ganzhi",
                    structural_claim_errors(_spec(), answer),
                )

    def test_day_master_detail_does_not_capture_other_pillar_yin_yang(self) -> None:
        answer = "일간은 乙입니다. 시주의 지지 午는 양입니다."
        self.assertEqual(structural_claim_errors(_spec(), answer), [])
        long_answer = (
            "일주 乙丑은 나 자신을 가리키는 기준 기둥이라는 점에서 구분되는데, "
            "천간 乙의 십신 라벨이 바로 일간이고 지지 丑의 오행은 토입니다. "
            "일간 乙의 오행은 목이며 음양은 음입니다."
        )
        self.assertEqual(structural_claim_errors(_spec(), long_answer), [])

    def test_day_master_natural_element_and_yin_yang_are_checked(self) -> None:
        invalid = {
            "일간 乙은 양목입니다.": "day_master_yin_yang_confusion:양",
            "일간은 양의 목 기운인 乙입니다.": "day_master_yin_yang_confusion:양",
            "일간은 음의 화 기운인 乙입니다.": "day_master_element_confusion:화",
            "乙木은 양의 성질을 가진 일간입니다.": ("day_master_yin_yang_confusion:양"),
            "乙은 양 기운의 일간입니다.": "day_master_yin_yang_confusion:양",
        }
        for answer, expected in invalid.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_unrelated_later_negation_does_not_mask_unsupported_claim(self) -> None:
        cases = {
            "일간 乙은 丑에 통근하며, 신강약 해석은 포함하지 않습니다.": (
                "unsupported_structural_claim:rooting"
            ),
            "용신은 화라고 판단하며, 통근 판정은 포함하지 않습니다.": (
                "unsupported_structural_claim:strength_pattern_yongshin"
            ),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_unsupported_term_absence_wording_is_not_a_claim(self) -> None:
        answer = "기둥 사이 합충과 대운·세운은 계산 범위에 들어 있지 않습니다."
        self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_relative_period_labels_accept_correct_parallel_prose(self) -> None:
        for answer in (
            "원국의 일주는 乙丑이고, 오늘은 己卯입니다.",
            "올해는 丙午이고 이번 달은 丙申이며 오늘은 己卯입니다.",
            "올해와 이번 달은 각각 丙午와 丙申이고, 오늘은 己卯입니다.",
            "올해, 이번 달, 오늘의 간지는 각각 丙午, 丙申, 己卯입니다.",
            "오늘 기준으로 올해는 丙午입니다.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])

    def test_surface_element_validator_requires_structural_context(self) -> None:
        for answer in (
            "오늘 확인할 항목 3개를 정리해드릴게요.",
            "첫 항목 3개부터 보겠습니다.",
            "금 3개 프로젝트를 정리했습니다.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(structural_claim_errors(_spec(), answer), [])
        self.assertIn(
            "surface_five_elements_목_confusion:3",
            structural_claim_errors(_spec(), "표면 오행은 목 3개입니다."),
        )
        for answer in (
            "표면 오행은 목 3, 화 1, 토 2, 금 0, 수 2입니다.",
            "표면 오행 분포: 목=3, 화=1, 토=2, 금=0, 수=2.",
            "목 3 / 화 1 / 토 2 / 금 0 / 수 2가 표면 오행입니다.",
            "표면 오행의 목 수치는 3입니다.",
        ):
            with self.subTest(answer=answer):
                self.assertIn(
                    "surface_five_elements_목_confusion:3",
                    structural_claim_errors(_spec(), answer),
                )
        correct_with_additive_particle = (
            "표면 오행 가운데 목은 2개이고 화는 1개입니다.\n"
            "토는 3개이고 금도 0개입니다.\n"
            "수는 2개이며, 이상이 다섯 오행의 표면 개수 전부입니다."
        )
        spec = deepcopy(_spec())
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "표면 오행 개수를 누락 없이 읽고, 계산되지 않은 판단은 덧붙이지 마."
        )
        self.assertEqual(required_fact_errors(spec, correct_with_additive_particle), [])
        self.assertIn(
            "surface_five_elements_금_confusion:1",
            structural_claim_errors(
                spec,
                correct_with_additive_particle.replace("금도 0개", "금도 1개"),
            ),
        )

    def test_nested_json_schema_claims_are_position_checked(self) -> None:
        cases = {
            '"chart.hard_facts.pillars.year.hidden_stems": ["甲", "乙", "癸"]': (
                "natal_year_hidden_stem_confusion:甲"
            ),
            "`chart.hard_facts.pillars.day.hidden_stems`: [`戊`, `癸`, `辛`]": (
                "natal_day_hidden_stem_confusion:戊"
            ),
            '"surface_five_elements": {"목": 3, "화": 1, "토": 3, "금": 0, "수": 2}': (
                "surface_five_elements_목_confusion:3"
            ),
            '"day_master": {"stem": "丙", "five_element": "화", "yin_yang": "양"}': (
                "day_master_confusion:丙"
            ),
            '"pillars": {"year": {"ganzhi": "乙丑", "stem": "乙", "branch": "丑", "stem_ten_god": "비견"}}': (
                "natal_year_ganzhi_confusion:乙丑"
            ),
            '"pillars": {"year": {"stem_element": "화", "branch_element": "수", "stem_yin_yang": "음"}}': (
                "natal_year_stem_element_confusion:화"
            ),
            '"natal": {"pillars": {"day": {"ganzhi": "戊辰", "stem": "戊", "branch": "辰"}}}': (
                "natal_day_ganzhi_confusion:戊辰"
            ),
            '"pillars": {"year": {"hidden_stems": ["甲", "乙", "癸"]}}': (
                "natal_year_hidden_stem_confusion:甲"
            ),
        }
        for answer, expected in cases.items():
            with self.subTest(answer=answer):
                self.assertIn(expected, structural_claim_errors(_spec(), answer))

    def test_schema_questions_require_their_requested_facts(self) -> None:
        cases = {
            "원국 전체 네 기둥과 일주를 서로 구분해서 설명해줘.": (
                "연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午입니다."
            ),
            "연주·월주·일주·시주가 각각 무엇인지 JSON에서 정확히 읽어줘.": (
                "연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午입니다."
            ),
            "이 원국의 일간과 그 오행·음양을 근거와 함께 풀어줘.": (
                "일간은 乙이고 오행은 목이며 음양은 음입니다."
            ),
            "선택 날짜의 연간지, 월간지, 일진을 서로 바꾸지 말고 알려줘.": (
                "연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다. "
                "함께 연결된 원국의 일주는 乙丑입니다."
            ),
            "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘.": (
                "연주는 천간 戊(토·양), 지지 辰(토·양), "
                "월주는 천간 甲(목·양), 지지 子(수·양), "
                "일주는 천간 乙(목·음), 지지 丑(토·음), "
                "시주는 천간 壬(수·양), 지지 午(화·양)입니다."
            ),
            "일주의 천간·지지·지장간을 JSON에 있는 값만 사용해서 알려줘.": (
                "일주의 천간은 乙, 지지는 丑, 지장간은 己·癸·辛입니다."
            ),
            "각 기둥의 stem ten-god와 branch ten-god를 위치별로 구분해줘.": (
                "연주는 천간 십신 정재·지지 십신 편재, "
                "월주는 천간 십신 겁재·지지 십신 편인, "
                "일주는 천간 십신 일간·지지 십신 편재, "
                "시주는 천간 십신 정인·지지 십신 식신입니다."
            ),
            "표면 오행 개수를 누락 없이 읽고, 계산되지 않은 판단은 덧붙이지 마.": (
                "표면 오행은 목 2, 화 1, 토 3, 금 0, 수 2입니다."
            ),
            "원국 네 기둥과 선택 날짜 세 간지가 어떻게 다른 자료인지 설명해줘.": (
                "원국은 戊辰·甲子·乙丑·壬午이고, 날짜는 丙午·丙申·己卯입니다."
            ),
            "날짜 JSON의 year/month/day ganzhi를 일반인이 혼동하지 않게 풀어줘.": (
                "연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다. "
                "함께 연결된 원국의 일주는 乙丑입니다."
            ),
        }
        for question, complete_answer in cases.items():
            spec = deepcopy(_spec())
            spec["task_axis"] = "structured_fact_schema_literacy"
            spec["prompt"][-1]["content"] = question
            with self.subTest(question=question, state="omitted"):
                self.assertTrue(
                    any(
                        error.startswith("required_schema_fact_omitted:")
                        for error in required_fact_errors(
                            spec, "확인된 값을 설명합니다."
                        )
                    )
                )
            with self.subTest(question=question, state="complete"):
                self.assertEqual(required_fact_errors(spec, complete_answer), [])

        natal_schema = deepcopy(_spec())
        natal_schema["task_axis"] = "structured_fact_schema_literacy"
        natal_schema["prompt"][-1]["content"] = (
            "원국 전체 네 기둥과 일주를 서로 구분해서 설명해줘."
        )
        natal_answer = "연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午입니다."
        self.assertEqual(required_fact_errors(natal_schema, natal_answer), [])
        self.assertIn(
            "unrequested_period_fact",
            required_fact_errors(
                natal_schema,
                natal_answer
                + " 선택 날짜의 연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다.",
            ),
        )

        period_spec = deepcopy(_spec())
        period_spec["task_axis"] = "structured_fact_schema_literacy"
        period_spec["prompt"][-1]["content"] = (
            "선택 날짜의 연간지, 월간지, 일진을 서로 바꾸지 말고 알려줘."
        )
        period_only = (
            "선택 날짜의 연간지는 丙午입니다. "
            "월간지는 丙申이고 일진은 己卯입니다."
        )
        self.assertIn(
            "explicit_natal_fact_omitted",
            required_fact_errors(period_spec, period_only),
        )
        unlabeled_period = (
            "첫 값은 丙午입니다. 둘째 값은 丙申입니다. "
            "마지막 값은 己卯이고 원국 일주는 乙丑입니다."
        )
        self.assertEqual(
            sum(
                error.startswith("required_schema_fact_omitted:period.")
                for error in required_fact_errors(period_spec, unlabeled_period)
            ),
            3,
        )
        for parallel_period in (
            "연간지·월간지·일진은 각각 丙午·丙申·己卯입니다.",
            "연간지, 월간지, 일진은 순서대로 丙午, 丙申, 己卯입니다.",
            "연간지/월간지/일진: 丙午/丙申/己卯입니다.",
        ):
            with self.subTest(parallel_period=parallel_period):
                self.assertEqual(
                    required_fact_errors(
                        period_spec,
                        parallel_period + " 원국 일주는 乙丑입니다.",
                    ),
                    [],
                )
        wrong_parallel = (
            "연간지·월간지·일진은 각각 丙午·己卯·丙申입니다. "
            "원국 일주는 乙丑입니다."
        )
        self.assertEqual(
            sum(
                error.startswith("required_schema_fact_omitted:period.")
                for error in required_fact_errors(period_spec, wrong_parallel)
            ),
            2,
        )
        negated_period = (
            "연간지가 丙午라는 근거는 없습니다. "
            "월간지는 丙申이고 일진은 己卯입니다. "
            "원국 일주는 乙丑입니다."
        )
        self.assertIn(
            "required_schema_fact_omitted:period.hard_facts.period.year_ganzhi",
            required_fact_errors(period_spec, negated_period),
        )
        appositive_period = (
            "연간지는 그 날짜가 속한 해의 간지인 丙午입니다.\n"
            "월간지는 그 날짜가 속한 달의 간지인 丙申입니다.\n"
            "그날 자체의 일진은 己卯이고 원국 일주는 乙丑입니다."
        )
        self.assertEqual(required_fact_errors(period_spec, appositive_period), [])
        wrong_appositive_owner = appositive_period.replace(
            "연간지는 그 날짜가 속한 해의 간지인 丙午",
            "연간지는 그 날짜가 속한 달의 간지인 丙午",
        )
        self.assertIn(
            "required_schema_fact_omitted:period.hard_facts.period.year_ganzhi",
            required_fact_errors(period_spec, wrong_appositive_owner),
        )
        equal_day_spec = deepcopy(_spec())
        equal_day_spec["task_axis"] = "structured_fact_schema_literacy"
        equal_day_spec["prompt"][-1]["content"] = (
            "날짜 JSON의 year/month/day ganzhi를 일반인이 혼동하지 않게 풀어줘."
        )
        period_day_path = "period.hard_facts.period.day_ganzhi"
        equal_day_spec["allowed_fact_values"][
            equal_day_spec["allowed_fact_paths"].index(period_day_path)
        ] = "乙丑"
        equal_period_and_natal_day = (
            "선택 날짜의 연간지는 丙午입니다.\n"
            "월간지는 丙申이며, 그날 자체의 일진은 乙丑입니다.\n"
            "원국의 일주도 乙丑이지만 날짜의 일진과는 구분해야 합니다."
        )
        self.assertEqual(
            required_fact_errors(equal_day_spec, equal_period_and_natal_day), []
        )
        year_path = "period.hard_facts.period.year_ganzhi"
        natal_day = "乙丑"
        period_spec["allowed_fact_values"][
            period_spec["allowed_fact_paths"].index(year_path)
        ] = natal_day
        same_literal_period_only = period_only.replace("丙午", natal_day)
        self.assertIn(
            "explicit_natal_fact_omitted",
            required_fact_errors(period_spec, same_literal_period_only),
        )

        for negated_anchor in (
            "원국의 일주는 乙丑일 수 없습니다.",
            "원국의 일주가 乙丑인지는 알 수 없습니다.",
            "원국의 일주는 乙丑과 무관합니다.",
            "원국의 일주가 乙丑이라는 근거는 없습니다.",
            "원국의 일주가 乙丑인지 불확실합니다.",
            "원국의 일주는 乙丑으로 보이지 않습니다.",
            "원국의 일주가 乙丑일 가능성이 없습니다.",
            "戊辰은 일주가 아니라 연주가 아닙니다.",
        ):
            with self.subTest(negated_anchor=negated_anchor):
                self.assertIn(
                    "explicit_natal_fact_omitted",
                    required_fact_errors(
                        period_spec,
                        same_literal_period_only + " " + negated_anchor,
                    ),
                )
        for valid_anchor in (
            "乙丑은 연주가 아니라 일주입니다.",
            "원국의 일간은 乙입니다.",
            "원국 네 기둥은 戊辰·甲子·乙丑·壬午입니다.",
            "원국 전체는 戊辰·甲子·乙丑·壬午입니다.",
        ):
            with self.subTest(valid_anchor=valid_anchor):
                self.assertNotIn(
                    "explicit_natal_fact_omitted",
                    required_fact_errors(
                        period_spec,
                        same_literal_period_only + " " + valid_anchor,
                    ),
                )

    def test_period_schema_natal_anchor_requires_matching_provenance_path(self) -> None:
        spec = deepcopy(_spec())
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "선택 날짜의 연간지, 월간지, 일진을 서로 바꾸지 말고 알려줘."
        )
        period_paths = [
            f"period.hard_facts.period.{field}"
            for field in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        ]
        natal_path = "chart.hard_facts.pillars.day.ganzhi"
        draft = {
            "record_id": spec["id"],
            "answer": (
                "선택 날짜의 연간지는 丙午입니다.\n"
                "월간지는 丙申이고 일진은 己卯입니다.\n"
                "이 날짜 정보는 원국의 일주 乙丑과 별개입니다."
            ),
            "used_fact_paths": period_paths,
            "used_fact_values": ["丙午", "丙申", "己卯", "乙丑"],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        with self.assertRaisesRegex(
            Mix2KV4ContractError, "명시 근거가 빠졌습니다"
        ):
            validate_draft(spec, draft)
        draft["used_fact_paths"].append(natal_path)
        self.assertEqual(validate_draft(spec, draft), draft)
        draft["used_fact_paths"] = [natal_path]
        with self.assertRaisesRegex(
            Mix2KV4ContractError, "명시 근거가 빠졌습니다"
        ):
            validate_draft(spec, draft)

    def test_general_replay_blocks_saju_injection_without_word_false_positives(
        self,
    ) -> None:
        spec = deepcopy(_spec())
        spec["task_axis"] = "general_korean_empathy"
        self.assertIn(
            "false_saju_injection",
            required_fact_errors(spec, "사주 원국의 오행부터 살펴보겠습니다."),
        )
        for answer in (
            "좋아하는 음악 연주를 떠올려 보세요.",
            "일주일 동안 한 번씩 기록해 보세요.",
            "일간 계획을 작게 나누면 부담이 줄어듭니다.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(required_fact_errors(spec, answer), [])

        empty_allowed = deepcopy(spec)
        empty_allowed["allowed_fact_paths"] = []
        empty_allowed["allowed_fact_values"] = []
        for answer in (
            "일간 계획을 작게 나누면 부담이 줄어듭니다.",
            "일간은 아직 계산되지 않았습니다.",
        ):
            with self.subTest(answer=answer, validator="structural"):
                self.assertEqual(structural_claim_errors(empty_allowed, answer), [])

    def test_ten_god_coverage_does_not_use_day_master_label_as_evidence(self) -> None:
        spec = deepcopy(_spec())
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "각 기둥의 stem ten-god와 branch ten-god를 위치별로 구분해줘."
        )
        answer = (
            "일간을 기준으로 십신을 읽습니다.\n"
            "연주는 천간 십신 정재·지지 십신 편재, 월주는 천간 십신 겁재·지지 십신 편인입니다.\n"
            "일주는 지지 십신 편재, 시주는 천간 십신 정인·지지 십신 식신입니다."
        )
        self.assertIn(
            "required_schema_fact_omitted:chart.hard_facts.pillars.day.stem_ten_god",
            required_fact_errors(spec, answer),
        )

        positioned_literal = (
            "연주는 천간 십신이 정재, 지지 십신이 편재입니다.\n"
            "월주는 천간 십신이 겁재, 지지 십신이 편인입니다.\n"
            "일주는 천간 자리가 십신이 아니라 기준이 되는 '일간'으로 표기되어 있고, "
            "지지 십신은 편재입니다.\n"
            "시주는 천간 십신이 정인, 지지 십신이 식신입니다."
        )
        self.assertEqual(required_fact_errors(spec, positioned_literal), [])

        role_literal = positioned_literal.replace(
            "천간 자리가 십신이 아니라 기준이 되는 '일간'으로 표기되어 있고",
            "천간이 일간 자리 그 자체이고",
        )
        self.assertEqual(required_fact_errors(spec, role_literal), [])
        self.assertIn(
            "natal_year_stem_ten_god_confusion:일간",
            structural_claim_errors(_spec(), "연주는 천간이 일간 자리 그 자체입니다."),
        )
        corrected_role = role_literal.replace(
            "일간 자리 그 자체이고",
            "정재 자리가 아니라 일간 자리이고",
        )
        self.assertEqual(required_fact_errors(spec, corrected_role), [])

        for natural_role in (
            "천간이 일간 자체라 '일간'으로 표기되고",
            "천간이 일간 그 자체이므로 십신 자리에 '일간'으로 표기되고",
        ):
            answer = positioned_literal.replace(
                "천간 자리가 십신이 아니라 기준이 되는 '일간'으로 표기되어 있고",
                natural_role,
            )
            with self.subTest(natural_role=natural_role):
                self.assertEqual(required_fact_errors(spec, answer), [])

        for negated_role in (
            "일주는 천간이 일간 자리 그 자체와 다릅니다.",
            "일주는 천간이 일간 자리 그 자체와 같지 않습니다.",
            "일주는 천간이 일간 자리 그 자체와 무관합니다.",
            "일주는 천간이 일간 자리 그 자체라고 하기 어렵습니다.",
            "일주는 천간이 일간 자리 그 자체일 리 없습니다.",
            "일주는 천간이 일간 자리 그 자체, 라고 보면 안 됩니다.",
            "일주는 천간이 일간 자리 그 자체, 라는 해석은 맞지 않습니다.",
            "일주는 천간이 일간 자리 그 자체, 라는 표현은 틀립니다.",
            "일주는 천간이 일간 자리 그 자체, 라고 단정하기 어렵습니다.",
            "일주는 천간이 일간 자리 그 자체, 라고 할 수 없습니다.",
        ):
            with self.subTest(negated_role=negated_role):
                self.assertNotIn(
                    ("day", "stem_ten_god", "일간"),
                    _pillar_field_claim_coverage(negated_role),
                )

        for negated in (
            positioned_literal.replace("표기되어", "표기되지 않고"),
            positioned_literal.replace("표기되어", "표기하면 안 되고"),
            positioned_literal.replace(
                "표기되어 있고", "표기되는 것은 아닙니다. 또한"
            ),
            positioned_literal.replace(
                "표기되어 있고", "표기된다는 뜻은 아닙니다. 또한"
            ),
            positioned_literal.replace(
                "표기되어 있고", "표기되는 게 아니라 다른 기준이며,"
            ),
            positioned_literal.replace("표기되어", "표기되어서는 안 되고"),
            positioned_literal.replace("십신이 아니라 ", "").replace(
                "표기되어 있고", "표기되어서는 안 됩니다. 또한"
            ),
            positioned_literal.replace("십신이 아니라 ", "").replace(
                "표기되어 있고", "표기되지 않고,"
            ),
        ):
            with self.subTest(negated=negated):
                self.assertIn(
                    "required_schema_fact_omitted:"
                    "chart.hard_facts.pillars.day.stem_ten_god",
                    required_fact_errors(spec, negated),
                )

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

        separated = (
            "선택 날짜의 연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다. "
            "한편 원국의 일주는 乙丑이므로, 위 날짜 표시는 원국 기둥이 아니라 "
            "선택 날짜에 붙는 정보입니다. 두 자료를 구분해서 보면 됩니다."
        )
        self.assertNotIn(
            "provided_natal_fact_omitted",
            required_fact_errors(spec, separated),
        )

    def test_chart_explanation_rejects_unrequested_period_facts(self) -> None:
        spec = deepcopy(_spec())
        spec["task_axis"] = "chart_facts_natural_explanation"
        chart_only = (
            "원국은 연주 戊辰, 월주 甲子, 일주 乙丑, 시주 壬午입니다. "
            "일간은 乙입니다. 네 기둥과 일간을 구분해서 보면 됩니다."
        )
        self.assertEqual(required_fact_errors(spec, chart_only), [])

        with_period = (
            chart_only
            + " 선택 날짜 2026-09-02의 연간지는 丙午, 월간지는 丙申, 일진은 己卯입니다."
        )
        self.assertIn(
            "unrequested_period_fact",
            required_fact_errors(spec, with_period),
        )

        day_master_only = (
            "원국의 일간은 乙입니다.\n"
            "일간은 일주 천간에서 읽는 중심값입니다.\n"
            "질문한 원국 범위 안에서 이 값을 먼저 구분하면 됩니다."
        )
        self.assertEqual(required_fact_errors(spec, day_master_only), [])

        spec["prompt"][-1]["content"] = (
            "일간을 중심으로 원국의 표면 구성을 설명해줘."
        )
        self.assertTrue(
            any(
                error.startswith("required_chart_fact_omitted:")
                for error in required_fact_errors(spec, day_master_only)
            )
        )
        with_surface = (
            day_master_only
            + "\n표면 오행은 목 2, 화 1, 토 3, 금 0, 수 2입니다."
        )
        self.assertEqual(required_fact_errors(spec, with_surface), [])

    def test_equal_period_year_and_day_ganzhi_are_not_label_confusion(self) -> None:
        spec = deepcopy(_spec())
        year_path = "period.hard_facts.period.year_ganzhi"
        day_path = "period.hard_facts.period.day_ganzhi"
        year_value = spec["allowed_fact_values"][
            spec["allowed_fact_paths"].index(year_path)
        ]
        day_index = spec["allowed_fact_paths"].index(day_path)
        spec["allowed_fact_values"][day_index] = year_value
        answer = (
            f"선택 날짜의 연간지는 {year_value}입니다. "
            f"같은 날짜의 일진도 우연히 {year_value}입니다."
        )
        self.assertNotIn(
            "period_year_called_day_ganzhi",
            structural_claim_errors(spec, answer),
        )

        unequal_errors = structural_claim_errors(_spec(), answer)
        self.assertIn(
            f"period_day_ganzhi_label_confusion:{year_value}",
            unequal_errors,
        )
        self.assertIn("period_year_called_day_ganzhi", unequal_errors)

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

    def test_teacher_layout_normalizer_only_splits_complete_sentences(self) -> None:
        spec = _spec()
        one_line = {
            "record_id": spec["id"],
            "answer": (
                "2026. 9. 2. 날짜를 먼저 확인합니다. "
                "원국과 날짜 정보는 서로 구분합니다. "
                "확인된 범위에서 차근차근 설명합니다."
            ),
            "used_fact_paths": [],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        normalized, changed = _normalize_draft_answer_layout(spec, one_line)
        self.assertTrue(changed)
        self.assertEqual(len(normalized["answer"].splitlines()), 3)
        self.assertIn("2026. 9. 2. 날짜", normalized["answer"])

        too_short = deepcopy(one_line)
        too_short["answer"] = "첫 문장입니다. 둘째 문장입니다."
        unchanged, changed = _normalize_draft_answer_layout(spec, too_short)
        self.assertFalse(changed)
        self.assertEqual(unchanged["answer"], too_short["answer"])

        already_multiline = deepcopy(one_line)
        already_multiline["answer"] = "첫 문장입니다.\n둘째 문장입니다.\n셋째 문장입니다."
        unchanged, changed = _normalize_draft_answer_layout(spec, already_multiline)
        self.assertFalse(changed)
        self.assertEqual(unchanged["answer"], already_multiline["answer"])

        for protected in (
            '`foo. bar. baz.`를 코드로 표시합니다.',
            "[참고. 예시. 항목.](https://example.com) 한 문장으로 안내합니다.",
            "- 첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.",
            "> 첫 문장입니다. 둘째 문장입니다. 셋째 문장입니다.",
            "설명은 두 문장입니다. . . 마지막 문장입니다.",
            "e.g. i.e. 실제 설명은 하나입니다.",
            "Dr. Kim. 실제 설명입니다.",
            "핵심: 감정. 상황. 차근차근 설명합니다.",
        ):
            with self.subTest(protected=protected):
                markup = deepcopy(one_line)
                markup["answer"] = protected
                unchanged, changed = _normalize_draft_answer_layout(spec, markup)
                self.assertFalse(changed)
                self.assertEqual(unchanged["answer"], protected)

    def test_teacher_particle_normalizer_uses_ganzhi_pronunciation(self) -> None:
        draft = {
            "answer": (
                "辛丑는 乙丑로 이어지고 癸丑와 구분합니다. "
                "丁未은 乙未을 뜻하며 丁未이라는 표현을 씁니다. "
                "壬戌으로 향하고 辛亥이더라도 그대로 둡니다."
            )
        }
        normalized, changed = _normalize_draft_answer_particles(draft)
        self.assertTrue(changed)
        self.assertEqual(
            normalized["answer"],
            (
                "辛丑은 乙丑으로 이어지고 癸丑과 구분합니다. "
                "丁未는 乙未를 뜻하며 丁未라는 표현을 씁니다. "
                "壬戌로 향하고 辛亥이더라도 그대로 둡니다."
            ),
        )
        self.assertEqual(draft["answer"], (
            "辛丑는 乙丑로 이어지고 癸丑와 구분합니다. "
            "丁未은 乙未을 뜻하며 丁未이라는 표현을 씁니다. "
            "壬戌으로 향하고 辛亥이더라도 그대로 둡니다."
        ))

    def test_teacher_prompt_has_four_explicit_evidence_sections(self) -> None:
        spec = _spec()
        prompt = draft_prompt([spec], {})
        self.assertEqual(prompt.count("[RAW RUNTIME FACTS]"), 1)
        self.assertEqual(prompt.count("[ALLOWED EVIDENCE]"), 1)
        self.assertEqual(prompt.count("[FORBIDDEN INFERENCE]"), 1)
        self.assertEqual(prompt.count("[TASK]"), 1)
        self.assertEqual(prompt.count("[MANDATORY ANSWER CHECKLIST]"), 1)
        self.assertNotIn("출생일", prompt)
        self.assertIn("FORBIDDEN 목록은 금지 기준", prompt)
        self.assertIn("limitations는 내부 audit metadata", prompt)
        self.assertIn("날짜 사실과 원국 사실을 각각 최소 하나", prompt)
        self.assertIn("`甲寅로`, `己丑는`처럼", prompt)
        self.assertIn("`甲寅으로`, `己丑은`", prompt)

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
        self.assertIn("최소 조건이며 최대 길이 제한이 아닙니다", review)
        self.assertIn("정확히 3줄로 줄이라고 요구하지 마세요", review)
        self.assertIn("audit metadata입니다", review)
        self.assertIn("answer의 내용과 metadata의 정확성을 구분", review)
        self.assertIn("날짜 사실과 원국 사실을 각각 최소 하나", review)
        self.assertIn("자연성 오류로 FAIL", review)
        self.assertIn("`甲寅으로`, `己丑은`", review)
        self.assertEqual(review.count("[MANDATORY ANSWER CHECKLIST]"), 1)

    def test_bound_chart_v2_dual_grounding_is_date_question_only(self) -> None:
        prompt = Path("configs/chat_prompts/saju_bound_chart_v2.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("사용자가 선택 날짜나 오늘의 흐름을 묻는다면", prompt)
        self.assertIn(
            "원국만 묻는 질문에는 `period`가 연결됐다는 이유만으로 날짜 사실을 덧붙이지 마세요",
            prompt,
        )
        self.assertNotIn(
            "현재 snapshot에 승인된 단일 날짜 `period`가 있다면 해당 날짜 사실과 원국 사실을 각각",
            prompt,
        )

    def test_schema_teacher_checklist_expands_every_requested_position(self) -> None:
        spec = _spec()
        spec["task_axis"] = "structured_fact_schema_literacy"
        spec["prompt"][-1]["content"] = (
            "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘."
        )
        checklist = "\n".join(_mandatory_answer_checklist(spec))
        for expected in (
            "연주: 천간=戊",
            "월주: 천간=甲",
            "일주: 천간=乙",
            "일주: 천간=乙, 천간 오행=목, 천간 음양=음, 지지=丑",
            "시주: 천간=壬",
        ):
            self.assertIn(expected, checklist)

        spec["prompt"][-1]["content"] = (
            "각 기둥의 stem ten-god와 branch ten-god를 위치별로 구분해줘."
        )
        checklist = "\n".join(_mandatory_answer_checklist(spec))
        self.assertIn("일주: stem ten-god=일간", checklist)
        self.assertIn("branch ten-god=편재", checklist)
        self.assertIn("runtime literal '일간'", checklist)

        spec["prompt"][-1]["content"] = (
            "선택 날짜의 연간지, 월간지, 일진을 서로 바꾸지 말고 알려줘."
        )
        checklist = "\n".join(_mandatory_answer_checklist(spec))
        self.assertIn("선택 날짜의 연간지=丙午", checklist)
        self.assertIn("선택 날짜의 월간지=丙申", checklist)
        self.assertIn("선택 날짜의 일진=己卯", checklist)
        self.assertIn("원국 일주=乙丑", checklist)

        hard_qa = deepcopy(_spec())
        hard_qa["task_axis"] = "hard_fact_short_qa"
        hard_qa["prompt"][-1]["content"] = "날짜의 year_ganzhi가 오늘 일진이야?"
        hard_checklist = "\n".join(_mandatory_answer_checklist(hard_qa))
        self.assertNotIn("원국 일주=", hard_checklist)
        self.assertNotIn("선택 날짜의 동시 근거", hard_checklist)

    def test_draft_schema_is_codex_compatible_and_validator_rejects_duplicates(
        self,
    ) -> None:
        schema = _draft_schema(["record-1"])
        self.assertNotIn("uniqueItems", json.dumps(schema, sort_keys=True))
        spec = _spec()
        duplicate = {
            "record_id": spec["id"],
            "answer": "첫 문장입니다.\n둘째 문장입니다.\n셋째 문장입니다.",
            "used_fact_paths": [
                spec["allowed_fact_paths"][0],
                spec["allowed_fact_paths"][0],
            ],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        with self.assertRaisesRegex(Mix2KV4ContractError, "중복값"):
            validate_draft(spec, duplicate)

    def test_pilot_selection_is_balanced_and_api_keys_are_scrubbed(self) -> None:
        specs = []
        for index in range(8):
            spec = deepcopy(_spec())
            spec["id"] = f"m2v4_{index:024d}"
            spec["drafter"] = "claude" if index % 2 == 0 else "codex"
            spec["reviewer"] = "codex" if index % 2 == 0 else "claude"
            specs.append(spec)
        selected = _selection(specs, "pilot", 2)
        self.assertEqual(
            Counter(row["drafter"] for row in selected), {"claude": 2, "codex": 2}
        )

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

    def test_training_max_length_uses_smallest_non_truncating_ladder_value(
        self,
    ) -> None:
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
