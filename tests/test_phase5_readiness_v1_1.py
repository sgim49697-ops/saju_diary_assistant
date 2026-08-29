# test_phase5_readiness_v1_1.py - 봉인 평가 split readiness 어댑터의 비학습 계약을 검증한다.

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts.training.phase5_readiness_v1_1 import (
    _json_payload,
    _parser,
    _stable_base_summary,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase5ReadinessV11Tests(unittest.TestCase):
    def test_dynamic_runtime_values_are_not_part_of_immutable_summary(self) -> None:
        first = _stable_base_summary(
            _json_payload(
                {
                    "available_disk_bytes": 100,
                    "workspace_commit": "a" * 40,
                    "runtime_versions": {"torch": "2.13.0"},
                }
            )
        )
        second = _stable_base_summary(
            _json_payload(
                {
                    "available_disk_bytes": 200,
                    "workspace_commit": "b" * 40,
                    "runtime_versions": {"torch": "2.13.0"},
                }
            )
        )
        self.assertEqual(first, second)
        self.assertTrue(first["available_disk_threshold_passed"])

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

    def test_registry_preserves_public_readiness_build(self) -> None:
        registry = json.loads(
            (
                REPO_ROOT / "configs/data_versions/saju_1b_baseline/registry.json"
            ).read_text(encoding="utf-8")
        )
        approved = next(
            value
            for value in registry["phase5_readiness_builds"]
            if value["version"] == "v1.1.0"
        )
        self.assertEqual(approved["build_id"], "build-201010b37e40")
        self.assertFalse(approved["phase5_training_performed"])
        public_root = (
            REPO_ROOT
            / "data/reports/saju_1b_baseline/phase5-readiness/v1.1.0"
            / approved["build_id"]
        )
        self.assertEqual(
            hashlib.sha256((public_root / "build_manifest.json").read_bytes())
            .hexdigest(),
            approved["public_manifest_sha256"],
        )
        self.assertEqual(
            hashlib.sha256((public_root / "readiness_summary.json").read_bytes())
            .hexdigest(),
            approved["readiness_summary_sha256"],
        )

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
