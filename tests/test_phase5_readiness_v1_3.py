# test_phase5_readiness_v1_3.py - Gate v2·KI20 preflight readiness의 불변 계약을 검증한다.

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.training.phase5_readiness_v1_3 import (
    Phase5ReadinessV13Error,
    _parser,
    prepare_context,
    validate_contract,
    verify_readiness,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.3.0.json"
)


class Phase5ReadinessV13Tests(unittest.TestCase):
    def test_committed_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-7eb4c34364cc")
        self.assertTrue(config["governance"]["ki20_preflight_ready"])
        self.assertFalse(config["governance"]["full_training_execution_enabled"])
        self.assertFalse(config["governance"]["production_promotion_allowed"])

    def test_governance_cannot_enable_full_training(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(config)
        mutated["governance"]["full_training_execution_enabled"] = True
        with self.assertRaises(Phase5ReadinessV13Error):
            validate_contract(mutated, REPO_ROOT)

    def test_prepare_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)

    def test_registry_points_to_verified_public_build(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        result = verify_readiness(context, REPO_ROOT, require_registry=True)
        self.assertTrue(result["registry_verified"])
        self.assertTrue(result["ki20_preflight_ready"])
        self.assertFalse(result["full_training_execution_enabled"])


if __name__ == "__main__":
    unittest.main()
