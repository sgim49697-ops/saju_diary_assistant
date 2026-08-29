# test_phase5_readiness_v1_1.py - 봉인 평가 split readiness 어댑터의 비학습 계약을 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.training.phase5_readiness_v1_1 import _parser, validate_contract

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase5ReadinessV11Tests(unittest.TestCase):
    def test_committed_contract_is_valid(self) -> None:
        config_path = (
            REPO_ROOT
            / "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.1.0.json"
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["evaluation_split_build_id"], "build-a5a04ab96594")
        self.assertFalse(result["phase5_training_performed"])

    def test_prepare_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)

    def test_adapter_contains_no_training_execution(self) -> None:
        source = (
            REPO_ROOT / "scripts/training/phase5_readiness_v1_1.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(".train(", source)
        self.assertNotIn(".backward(", source)
        self.assertNotIn("optimizer.step(", source)


if __name__ == "__main__":
    unittest.main()
