# test_evaluation_split_v1_1.py - dev 중복·페르소나 확장과 blind 비접근을 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.evaluation.phase5_split_v1_1 import (
    _overlap_role,
    _parser,
    _persona_guard,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/evaluation-split-v1.1.0.json"
)


def _item(index: int) -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "eval_id": f"eval-{index:03d}",
        "source_axis": "nemotron_saju",
        "category": "source_holdout",
        "hardness": "reference_anchor",
        "automated_contract": {"score": "reference_overlap_and_nonempty"},
        "parents": [],
        "cases": [
            {
                "case_id": f"case-{index:03d}",
                "parent_record_sha256": f"{index:064x}"[-64:],
                "prompt_sha256": f"{index + 1:064x}"[-64:],
                "prompt_messages": [{"role": "user", "content": "질문"}],
                "reference_assistant": "같은 답변" if index == 0 else "다른 답변",
            }
        ],
    }


class EvaluationSplitV11Tests(unittest.TestCase):
    def test_committed_contract_is_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertFalse(result["parent_membership_modified"])

    def test_persona_guard_is_deterministic_and_noncausal(self) -> None:
        values = [_item(index) for index in range(60)]
        first = _persona_guard(values, seed=42, namespace="guard", count=50)
        second = _persona_guard(list(reversed(values)), seed=42, namespace="guard", count=50)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 50)
        self.assertTrue(
            all(
                "비인과적으로" in item["cases"][0]["prompt_messages"][-1]["content"]
                for item in first
            )
        )

    def test_reference_overlap_is_aggregate_only(self) -> None:
        result = _overlap_role([_item(0), _item(1)], {"같은 답변"}, {"같은 답변"})
        self.assertEqual(result["exact_reference_overlap_cases"], 1)
        self.assertEqual(result["normalized_reference_overlap_cases"], 1)
        self.assertNotIn("reference_assistant", result)

    def test_prepare_defaults_to_dry_run_and_source_does_not_read_blind(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)
        source = (
            REPO_ROOT / "scripts/evaluation/phase5_split_v1_1.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('read_jsonl(parent_private / "eval/blind', source)


if __name__ == "__main__":
    unittest.main()
