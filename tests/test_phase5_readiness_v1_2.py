# test_phase5_readiness_v1_2.py - 의미 감사·평가·runner를 묶는 readiness v1.2 계약을 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.training.phase5_readiness_v1_2 import (
    _parser,
    prepare_context,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.2.0.json"
)


class Phase5ReadinessV12Tests(unittest.TestCase):
    def test_committed_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-bffd53a2abb3")
        self.assertFalse(config["governance"]["ki20_promotion_allowed"])
        self.assertFalse(config["governance"]["blind_source_test_inspected"])

    def test_prepare_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
