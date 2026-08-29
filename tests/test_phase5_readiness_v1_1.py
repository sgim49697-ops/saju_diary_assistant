# test_phase5_readiness_v1_1.py - 봉인 평가 split readiness 어댑터의 비학습 계약을 검증한다.

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.training.phase5_readiness_v1_1 import _parser

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase5ReadinessV11Tests(unittest.TestCase):
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
