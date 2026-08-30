# test_phase5_ki20_preflight.py - KI20 처리량 선택·16 GiB 한계·비학습 경계를 검증한다.

from __future__ import annotations

import json
import unittest

from scripts.training.phase5_ki20_preflight import (
    REPO_ROOT,
    _memory_safe,
    _parser,
    select_batch_candidate,
    select_eval_batch,
    select_worker_result,
    validate_contract,
)

CONFIG_PATH = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/phase5-training-v1.1.0.json"
)


def _batch_result(batch: int, rate: float, peak: int) -> dict[str, object]:
    return {
        "status": "passed",
        "per_device_train_batch_size": batch,
        "gradient_accumulation_steps": 8 // batch,
        "effective_batch_size": 8,
        "median_supervised_tokens_per_second": rate,
        "peak_total_gpu_memory_used_mib": peak,
        "memory_safe": True,
        "finite_loss_and_gradient": True,
    }


class Phase5KI20PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.policy = self.config["benchmark"]["limits"]

    def test_committed_contract_is_valid_and_standard_nll(self) -> None:
        result = validate_contract(self.config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(self.config["objective"]["name"], "assistant_only_token_nll")
        self.assertEqual(self.config["objective"]["trainer_loss_type"], "chunked_nll")
        self.assertFalse(self.config["objective"]["weighted_sampler"])
        self.assertFalse(self.config["objective"]["dft"])

    def test_training_contract_keeps_random_order_and_effective_batch_eight(self) -> None:
        fixed = self.config["fixed_training"]
        self.assertEqual(fixed["effective_batch_size"], 8)
        self.assertEqual(fixed["expected_optimizer_steps"], 2500)
        self.assertTrue(fixed["shuffle_dataset"])
        self.assertFalse(fixed["group_by_length"])
        self.assertFalse(fixed["packing"])
        self.assertFalse(fixed["padding_free"])
        for candidate in self.config["benchmark"]["train_batch_candidates"]:
            self.assertEqual(
                candidate["per_device_train_batch_size"]
                * candidate["gradient_accumulation_steps"],
                8,
            )

    def test_batch_selection_requires_five_percent_then_uses_tie_break(self) -> None:
        baseline = _batch_result(1, 100.0, 8000)
        not_enough = _batch_result(2, 104.9, 9000)
        selected = select_batch_candidate([baseline, not_enough], self.policy)
        self.assertEqual(selected["per_device_train_batch_size"], 1)
        batch_two = _batch_result(2, 110.0, 10000)
        batch_four = _batch_result(4, 111.0, 9000)
        selected = select_batch_candidate(
            [baseline, batch_two, batch_four], self.policy
        )
        self.assertEqual(selected["per_device_train_batch_size"], 4)

    def test_worker_two_requires_five_percent_and_safety(self) -> None:
        worker_zero = {
            "status": "passed",
            "dataloader_num_workers": 0,
            "median_supervised_tokens_per_second": 100.0,
            "memory_safe": True,
            "gradient_finite": True,
            "gradient_nonzero": True,
        }
        worker_two = {
            **worker_zero,
            "dataloader_num_workers": 2,
            "median_supervised_tokens_per_second": 105.0,
        }
        self.assertEqual(
            select_worker_result(worker_zero, worker_two, self.policy)[
                "dataloader_num_workers"
            ],
            2,
        )
        worker_two["memory_safe"] = False
        self.assertEqual(
            select_worker_result(worker_zero, worker_two, self.policy)[
                "dataloader_num_workers"
            ],
            0,
        )

    def test_eval_selection_requires_loss_equivalence(self) -> None:
        results = [
            {
                "status": "passed",
                "memory_safe": True,
                "per_device_eval_batch_size": 1,
                "loss": 1.0,
                "supervised_tokens_per_second": 100.0,
            },
            {
                "status": "passed",
                "memory_safe": True,
                "per_device_eval_batch_size": 4,
                "loss": 1.00005,
                "supervised_tokens_per_second": 180.0,
            },
            {
                "status": "passed",
                "memory_safe": True,
                "per_device_eval_batch_size": 8,
                "loss": 1.0002,
                "supervised_tokens_per_second": 220.0,
            },
        ]
        selected = select_eval_batch(results, 0.0001)
        self.assertEqual(selected["per_device_eval_batch_size"], 4)

    def test_gpu_limit_uses_physical_total_under_sixteen_gib(self) -> None:
        memory = {
            "monitor_error": None,
            "peak_total_gpu_memory_used_mib": 16200,
            "gpu_total_mib": 16303,
            "minimum_system_ram_available_bytes": 5 * 1024**3,
            "swap_growth_bytes": 0,
        }
        self.assertTrue(_memory_safe(memory, self.policy))
        memory["peak_total_gpu_memory_used_mib"] = 16304
        self.assertFalse(_memory_safe(memory, self.policy))
        self.assertEqual(self.policy["required_free_gpu_memory_mib"], 0)
        self.assertFalse(self.policy["ram_swap_hard_gate"])

    def test_only_benchmark_command_can_execute_optimizer_steps(self) -> None:
        args = _parser().parse_args(["benchmark"])
        self.assertFalse(args.execute)
        source = (
            REPO_ROOT / "scripts/training/phase5_ki20_preflight.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("PHASE5_TRAINING", source)
        self.assertFalse(self.config["governance"]["full_training_command_exposed"])
        self.assertFalse(self.config["governance"]["ki20_full_training_performed"])


if __name__ == "__main__":
    unittest.main()
