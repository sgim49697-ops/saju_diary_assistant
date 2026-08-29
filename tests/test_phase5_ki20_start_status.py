# test_phase5_ki20_start_status.py - WSL2 KI20 시작 증거의 읽기 전용 판정을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.training.phase5_ki20_start_status import (
    Phase5KI20StartStatusError,
    verify_start_status,
)
from scripts.training.phase5_ki20_train import REPO_ROOT, prepare_context


class Phase5KI20StartStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base_context = prepare_context(REPO_ROOT)

    def _context(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        context = {
            **self.base_context,
            "run_root": root,
            "run_build_id": "run-test",
            "run_sha256": "a" * 64,
        }
        marker = {
            "status": "training_started",
            "run_id": "KI20-MIX-v2",
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "global_step": 1,
            "loss": 2.0,
            "grad_norm": 3.0,
            "gradient_probe": {"finite": True, "nonzero": True},
            "process_id": 42,
            "service_unit": "saju-test.service",
            "goal_completion_criterion_met": True,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
        }
        manifest = {
            "status": "running",
            "run_sha256": context["run_sha256"],
            "first_optimizer_step": marker,
            "phase5_training_performed": True,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
            "operational_precheck": {"gpu": {"used_mib": 1000}},
        }
        (root / "training_started.json").write_text(
            json.dumps(marker), encoding="utf-8"
        )
        (root / "run_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (root / "metrics.jsonl").write_text(
            json.dumps({"global_step": 1, "loss": 2.0, "grad_norm": 3.0}) + "\n",
            encoding="utf-8",
        )
        return context, marker

    def test_wsl2_fallback_requires_live_runner_service_and_gpu_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, _ = self._context(Path(temporary))
            with (
                patch(
                    "scripts.training.phase5_ki20_start_status._process_is_training",
                    return_value=True,
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._service_is_active",
                    return_value=True,
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._gpu_snapshot",
                    return_value={
                        "total_mib": 16303,
                        "used_mib": 9000,
                        "free_mib": 7303,
                    },
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._compute_processes",
                    return_value=[],
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._is_wsl2",
                    return_value=True,
                ),
            ):
                result = verify_start_status(context)
        self.assertEqual(result["status"], "verified_training_started")
        self.assertEqual(
            result["cuda_process_evidence"], "wsl2_runner_pid_and_gpu_growth"
        )
        self.assertEqual(result["gpu_memory_growth_from_precheck_mib"], 8000)
        self.assertTrue(result["goal_completion_criterion_met"])

    def test_empty_compute_list_is_not_accepted_outside_wsl2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, _ = self._context(Path(temporary))
            with (
                patch(
                    "scripts.training.phase5_ki20_start_status._process_is_training",
                    return_value=True,
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._service_is_active",
                    return_value=True,
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._gpu_snapshot",
                    return_value={
                        "total_mib": 16303,
                        "used_mib": 9000,
                        "free_mib": 7303,
                    },
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._compute_processes",
                    return_value=[],
                ),
                patch(
                    "scripts.training.phase5_ki20_start_status._is_wsl2",
                    return_value=False,
                ),
                self.assertRaises(Phase5KI20StartStatusError),
            ):
                verify_start_status(context)

    def test_status_verifier_contains_no_training_execution(self) -> None:
        source = (
            REPO_ROOT / "scripts/training/phase5_ki20_start_status.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("trainer.train(", source)
        self.assertNotIn("optimizer.step(", source)


if __name__ == "__main__":
    unittest.main()
