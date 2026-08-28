# test_phase5_readiness.py - Phase 5 실행 전 불변 입력·비학습 Gate 회귀 테스트

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.training.phase5_readiness import (
    AXES,
    Phase5ReadinessError,
    _parser,
    _runtime_hardware,
    _select_eval70,
    _verify_manifest_rows,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase5ReadinessTests(unittest.TestCase):
    @staticmethod
    def _holdout_rows() -> list[dict[str, object]]:
        return [
            {
                "schema_version": "2.0.0",
                "eval_id": f"{axis}-{index:03d}",
                "category": "source_holdout",
                "source_axis": axis,
                "parents": [{"leakage_component_id": f"component-{axis}-{index:03d}"}],
            }
            for axis in AXES
            for index in range(100)
        ]

    def test_eval70_is_deterministic_and_balanced_by_seven_axes(self) -> None:
        rows = self._holdout_rows()
        first, counts = _select_eval70(rows, seed=42)
        second, second_counts = _select_eval70(list(reversed(rows)), seed=42)
        self.assertEqual(first, second)
        self.assertEqual(counts, second_counts)
        self.assertEqual(counts, {axis: 10 for axis in sorted(AXES)})
        self.assertEqual(len(first), 70)

    def test_manifest_validation_rejects_length_over_formal_limit(self) -> None:
        row = {
            "id": "fixture",
            "record_sha256": "a" * 64,
            "leakage_component_id": "component-fixture",
            "mix_axis": "nemotron_saju",
            "total_tokens": 769,
            "assistant_tokens": 10,
        }
        with self.assertRaises(Phase5ReadinessError):
            _verify_manifest_rows(
                [row], 1, {axis: int(axis == "nemotron_saju") for axis in AXES}
            )

    def test_prepare_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["prepare"])
        self.assertFalse(args.execute)

    def test_runtime_hardware_rejects_multiple_gpus(self) -> None:
        class FakeProperties:
            total_memory = 17_094_475_776

        class FakeCuda:
            @staticmethod
            def is_available() -> bool:
                return True

            @staticmethod
            def device_count() -> int:
                return 2

            @staticmethod
            def get_device_name(_index: int) -> str:
                return "NVIDIA GeForce RTX 5070 Ti"

            @staticmethod
            def get_device_capability(_index: int) -> tuple[int, int]:
                return (12, 0)

            @staticmethod
            def is_bf16_supported() -> bool:
                return True

            @staticmethod
            def get_device_properties(_index: int) -> FakeProperties:
                return FakeProperties()

        class FakeVersion:
            cuda = "13.0"

        class FakeTorch:
            __version__ = "2.13.0+cu130"
            version = FakeVersion()
            cuda = FakeCuda()

        runtime = {
            "torch_build": "2.13.0+cu130",
            "torch_cuda": "13.0",
            "gpu_name": "NVIDIA GeForce RTX 5070 Ti",
            "compute_capability": [12, 0],
        }
        with self.assertRaises(Phase5ReadinessError):
            _runtime_hardware(
                runtime, require_single_cuda_gpu=True, torch_module=FakeTorch()
            )

    def test_readiness_module_has_no_training_execution(self) -> None:
        source = (REPO_ROOT / "scripts/training/phase5_readiness.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn(".train(", source)
        self.assertNotIn(".backward(", source)
        self.assertNotIn("optimizer.step(", source)


if __name__ == "__main__":
    unittest.main()
