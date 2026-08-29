# test_phase5_training.py - Phase 5 runner의 실행 확인·고정 설정·자동 Gate를 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.training.phase5_quality import score_generations
from scripts.training.phase5_train import RUN_IDS, _parser, validate_contract

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/phase5-training-v1.0.0.json"
)


def _thresholds(cases: int) -> dict[str, float | int]:
    return {
        "expected_generation_cases": cases,
        "parseable_nonempty_min_percent": 98.0,
        "special_or_control_outputs_max": 0,
        "severe_safety_outputs_max": 0,
        "foreign_sentence_max_percent": 3.0,
        "target_only_entity_outputs_max": 0,
        "hard_fact_and_branch_policy_min_percent": 90.0,
        "shensha_condition_and_polarity_min_percent": 90.0,
        "empathy_task_confusion_max_percent": 5.0,
        "missing_chart_handoff_required": 1,
        "input_fact_violation_max_percent": 10.0,
        "persona_causalization_max_percent": 10.0,
    }


def _row(index: int, category: str, output: str, contract: dict[str, object]) -> dict[str, object]:
    return {
        "eval_id": f"eval-{index}",
        "case_id": f"case-{index}",
        "category": category,
        "source_axis": "synthetic",
        "automated_contract": contract,
        "prompt_messages": [{"role": "user", "content": "명식은 甲子 乙丑 丙寅 丁卯입니다."}],
        "output": output,
    }


class Phase5TrainingTests(unittest.TestCase):
    def test_committed_runner_contract_is_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(config["training"]["pad_to_multiple_of"], 8)
        self.assertFalse(config["training"]["logging_nan_inf_filter"])

    def test_training_commands_default_to_dry_run(self) -> None:
        for command in ("preflight-run", "train"):
            args = _parser().parse_args([command, "--run-id", RUN_IDS[0]])
            self.assertFalse(args.execute)
        args = _parser().parse_args(["evaluate-ki10"])
        self.assertFalse(args.execute)

    def test_quality_gate_passes_only_when_every_contract_passes(self) -> None:
        rows = [
            _row(0, "deterministic_hard_fact", "甲子 乙丑 丙寅 丁卯와 비견입니다.", {"required_terms": ["비견"]}),
            _row(1, "shensha_rule_qa", "이 조건에는 해당합니다.", {"expected_outcome": True}),
            _row(2, "empathy", "그 마음이 많이 힘들었겠어요.", {}),
            _row(3, "missing_chart_handoff", "생년월일 정보나 계산기 명식을 알려주세요.", {"expected_handoff_terms": ["명식", "생년월일", "계산기", "정보"]}),
            _row(4, "persona_causalization_guard", "사주는 참고 가능한 해석이며 직업을 단정할 수 없습니다.", {}),
        ]
        result = score_generations(rows, _thresholds(len(rows)))
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["ki20_promotion_allowed"])
        rows[0]["output"] = "비어 있지 않지만 필수 용어가 없습니다."
        failed = score_generations(rows, _thresholds(len(rows)))
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["ki20_promotion_allowed"])

    def test_quality_gate_fails_when_required_category_disappears(self) -> None:
        rows = [
            _row(0, "deterministic_hard_fact", "비견입니다.", {"required_terms": ["비견"]}),
            _row(1, "shensha_rule_qa", "해당합니다.", {"expected_outcome": True}),
            _row(2, "missing_chart_handoff", "명식 정보를 알려주세요.", {"expected_handoff_terms": ["명식"]}),
        ]
        result = score_generations(rows, _thresholds(len(rows)))
        self.assertIn("empathy_task_confusion", result["failed_gates"])
        self.assertIn("persona_causalization", result["failed_gates"])

    def test_runner_never_names_sealed_blind_payload(self) -> None:
        source = (REPO_ROOT / "scripts/training/phase5_train.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("blind_source_test_500.jsonl", source)


if __name__ == "__main__":
    unittest.main()
