# test_phase5_ki20_training.py - KI20 1 epoch 실행 승인과 시작 증거를 검증한다.

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.preflight.phase4_common import load_json
from scripts.training.phase5_ki20_train import (
    DEFAULT_CONFIG,
    REPO_ROOT,
    _initial_manifest,
    _KI20TrainingProbe,
    _operational_precheck,
    main,
    prepare_context,
    validate_contract,
)


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return False


class _FakeTorch:
    cuda = _FakeCuda()


class Phase5KI20TrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config_path = REPO_ROOT / DEFAULT_CONFIG
        cls.config = load_json(cls.config_path, "KI20 v1.2 training config")
        cls.context = prepare_context(REPO_ROOT, cls.config_path)

    def test_committed_execution_contract_is_valid(self) -> None:
        result = validate_contract(self.config, self.config_path, REPO_ROOT)
        self.assertEqual(
            result["status"], "validated_ki20_one_epoch_execution_contract"
        )
        training = self.config["training"]
        self.assertEqual(training["num_train_epochs"], 1)
        self.assertEqual(training["expected_optimizer_steps"], 2500)
        self.assertEqual(training["per_device_train_batch_size"], 4)
        self.assertEqual(training["gradient_accumulation_steps"], 2)
        self.assertEqual(training["effective_batch_size"], 8)
        self.assertEqual(training["preserved_milestone_steps"], [1250, 2500])
        self.assertTrue(
            self.config["governance"]["explicit_user_confirmation_received"]
        )
        self.assertTrue(self.config["governance"]["full_training_execution_enabled"])
        self.assertFalse(
            self.config["governance"]["production_promotion_allowed"]
        )

    def test_historical_training_contract_bytes_are_unchanged(self) -> None:
        expected = {
            "scripts/training/phase5_train.py": "aff05d86ffb2d483bf4817721797ad1aa410c019b7b77501071849c2dca1eb3f",
            "configs/model_versions/saju_1b_baseline/phase5-training-v1.0.0.json": "752f792ae6f6b44ea8a25b8a9fc3228082ef707d768ffe2205c1a2252907a016",
            "configs/model_versions/saju_1b_baseline/phase5-training-v1.1.0.json": "ee0f80eac83ae8cf4f72476bc0f69d002e9b41c21c11162f4b40e43f6d34db2f",
        }
        for relative, digest in expected.items():
            self.assertEqual(
                hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest(), digest
            )

    def test_training_commands_default_to_dry_run(self) -> None:
        self.assertEqual(main(["train"]), 0)
        self.assertEqual(
            main(["resume", "--checkpoint", "runs/not-used/checkpoint-250"]), 0
        )
        self.assertFalse(self.context["run_root"].exists())

    def test_initial_manifest_does_not_claim_training_before_first_step(self) -> None:
        manifest = _initial_manifest(
            self.context,
            REPO_ROOT,
            self.config["data"]["manifest_sha256"],
            {
                "gpu": {},
                "compute_processes": [],
                "system_ram": {},
                "disk_available_bytes": 1,
            },
        )
        self.assertEqual(manifest["status"], "initializing")
        self.assertIsNone(manifest["first_optimizer_step"])
        self.assertFalse(manifest["phase5_training_performed"])

    def test_start_marker_requires_first_finite_step_and_gradient(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = {
                **self.context,
                "run_root": root,
                "run_sha256": "a" * 64,
                "run_build_id": "run-test",
            }
            manifest = {
                "status": "initializing",
                "run_sha256": context["run_sha256"],
                "phase5_training_performed": False,
            }
            (root / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            probe = _KI20TrainingProbe(
                _FakeTorch(), root / "metrics.jsonl", context, REPO_ROOT
            )
            probe.gradient_probe = {
                "finite": True,
                "nonzero": True,
                "tensor_count": 1,
                "element_count": 1,
                "observed_after_gradient_clip": True,
            }
            gpu = {
                "index": 0,
                "name": "test",
                "uuid": "GPU-test",
                "total_mib": 16303,
                "used_mib": 10000,
                "free_mib": 6303,
                "driver_version": "test",
            }
            with patch(
                "scripts.training.phase5_ki20_train._gpu_snapshot",
                return_value=gpu,
            ):
                probe.on_log(
                    None,
                    SimpleNamespace(global_step=0, epoch=0.0),
                    None,
                    {"loss": 2.0, "grad_norm": 3.0},
                )
                self.assertFalse((root / "training_started.json").exists())
                probe.on_log(
                    None,
                    SimpleNamespace(global_step=1, epoch=0.0004),
                    None,
                    {"loss": 2.0, "grad_norm": 3.0},
                )
            marker = load_json(root / "training_started.json", "test marker")
            updated = load_json(root / "run_manifest.json", "test manifest")
            self.assertEqual(marker["global_step"], 1)
            self.assertTrue(marker["goal_completion_criterion_met"])
            self.assertEqual(updated["status"], "running")
            self.assertTrue(updated["phase5_training_performed"])
            self.assertEqual(updated["first_optimizer_step"], marker)

    def test_operational_precheck_rejects_another_compute_process(self) -> None:
        gpu = {
            "index": 0,
            "name": "test",
            "uuid": "GPU-test",
            "total_mib": 16303,
            "used_mib": 1000,
            "free_mib": 15000,
            "driver_version": "test",
        }
        with (
            patch(
                "scripts.training.phase5_ki20_train._gpu_snapshot",
                return_value=gpu,
            ),
            patch(
                "scripts.training.phase5_ki20_train._compute_processes",
                return_value=[
                    {
                        "pid": 99,
                        "process_name": "other",
                        "used_gpu_memory_mib": 100,
                    }
                ],
            ),
            self.assertRaises(RuntimeError),
        ):
            _operational_precheck(self.context, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
