# test_mix2k_v4_lora.py - LoRA checkpoint 재개 lineage와 완료 경계를 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from scripts.training.mix2k_v4_lora import (
    REQUIRED_RESUME_CHECKPOINT_FILES,
    Mix2KV4LoRAError,
    _longest_rows,
    _remove_fresh_trainer_metadata,
    _runtime_hash_matches_axis,
    _training_row_matches_spec,
    _valid_token_audit_row,
    _validate_adapter_metrics,
    _validated_resume_checkpoint,
)


class Mix2KV4LoRAResumeTests(unittest.TestCase):
    def test_fresh_trainer_metadata_is_removed_without_weakening_reuse_gate(
        self,
    ) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        adapter = Path(temporary.name) / "final_adapter"
        adapter.mkdir()
        metadata = adapter / "training_args.bin"
        metadata.write_bytes(b"trusted trainer metadata")

        _remove_fresh_trainer_metadata(adapter)

        self.assertFalse(metadata.exists())
        full_weight = adapter / "pytorch_model.bin"
        full_weight.write_bytes(b"full weights")
        _remove_fresh_trainer_metadata(adapter)
        self.assertTrue(full_weight.is_file())

    def test_fresh_trainer_metadata_rejects_non_regular_path(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        adapter = Path(temporary.name) / "final_adapter"
        adapter.mkdir()
        (adapter / "training_args.bin").mkdir()

        with self.assertRaisesRegex(Mix2KV4LoRAError, "일반 파일"):
            _remove_fresh_trainer_metadata(adapter)

    def test_runtime_hash_contract_allows_optional_uncertainty_binding(self) -> None:
        digest = "a" * 64
        self.assertTrue(_runtime_hash_matches_axis("chart_day_today_flow", digest))
        self.assertFalse(_runtime_hash_matches_axis("chart_day_today_flow", None))
        self.assertTrue(
            _runtime_hash_matches_axis("uncertainty_blocked_boundary", digest)
        )
        self.assertTrue(
            _runtime_hash_matches_axis("uncertainty_blocked_boundary", None)
        )
        self.assertFalse(_runtime_hash_matches_axis("general_korean_empathy", digest))

    def test_training_row_is_bound_to_frozen_prompt_axis_and_runtime_hash(self) -> None:
        prompt = [
            {"role": "system", "content": "고정 system"},
            {"role": "user", "content": "고정 질문"},
        ]
        digest = "a" * 64
        spec = {
            "task_axis": "chart_day_today_flow",
            "prompt": prompt,
            "runtime_binding": {"snapshot_sha256": digest},
        }
        row = {
            "task_axis": "chart_day_today_flow",
            "messages": [*prompt, {"role": "assistant", "content": "답변"}],
            "runtime_snapshot_sha256": digest,
        }
        self.assertTrue(_training_row_matches_spec(row, spec))
        changed_hash = {**row, "runtime_snapshot_sha256": "b" * 64}
        self.assertFalse(_training_row_matches_spec(changed_hash, spec))
        changed_prompt = {
            **row,
            "messages": [
                {"role": "system", "content": "변조 system"},
                *row["messages"][1:],
            ],
        }
        self.assertFalse(_training_row_matches_spec(changed_prompt, spec))

    def test_token_audit_type_corruption_is_rejected_without_exception(self) -> None:
        row = {
            "id": "m2v4_test",
            "task_axis": "chart_day_today_flow",
            "rendered_tokens": 120,
            "prompt_tokens": 100,
            "supervised_assistant_tokens": 20,
            "truncated": False,
            "assistant_mask_nonzero": True,
            "assistant_mask_sha256": "a" * 64,
            "final_eos_supervised": True,
            "user_system_loss_leakage_tokens": 0,
        }
        self.assertTrue(_valid_token_audit_row(row, 2048))
        for value in (None, "120", True):
            with self.subTest(value=value):
                damaged = {**row, "rendered_tokens": value}
                self.assertFalse(_valid_token_audit_row(damaged, 2048))

    def test_reused_training_metrics_require_intact_adapter_artifacts(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name).resolve() / "run"
        adapter = target / "trainer/final_adapter"
        adapter.mkdir(parents=True)
        model_bytes = b"adapter weights"
        config_value = {
            "peft_type": "LORA",
            "r": 16,
            "use_rslora": True,
            "bias": "none",
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "task_type": "CAUSAL_LM",
        }
        config_bytes = json.dumps(config_value).encode()
        model_path = adapter / "adapter_model.safetensors"
        adapter_config_path = adapter / "adapter_config.json"
        model_path.write_bytes(model_bytes)
        adapter_config_path.write_bytes(config_bytes)
        trainable = 18_677_760
        total = trainable + 1_291_478_272
        config = {
            "lora": {
                "expected_trainable_parameters": {"16": trainable},
                "expected_target_linear_modules": 224,
                "bias": "none",
                "lora_alpha": 32,
                "lora_dropout": 0.05,
                "task_type": "CAUSAL_LM",
            },
            "operational_limits": {"max_total_gpu_memory_used_mib": 16_384},
        }
        metrics = {
            "global_step": 250,
            "training_loss": 1.25,
            "elapsed_seconds": 10.0,
            "trainable_parameters": trainable,
            "target_linear_modules": 224,
            "total_parameters_with_adapter": total,
            "trainable_ratio": trainable / total,
            "gradient": {
                "called": True,
                "lora_gradient_finite": True,
                "lora_gradient_nonzero": True,
                "base_gradient_absent": True,
            },
            "peak_allocated_bytes": 1_000,
            "peak_reserved_bytes": 2_000,
            "adapter_model_sha256": sha256(model_bytes).hexdigest(),
            "adapter_config_sha256": sha256(config_bytes).hexdigest(),
            "adapter_reload_rank": 16,
            "adapter_reload_match": True,
        }
        _validate_adapter_metrics(
            metrics=metrics,
            config=config,
            target=target,
            rank=16,
            expected_steps=250,
        )
        full_weight = adapter / "pytorch_model.bin"
        full_weight.write_bytes(b"full weights")
        with self.assertRaisesRegex(Mix2KV4LoRAError, "full weight"):
            _validate_adapter_metrics(
                metrics=metrics,
                config=config,
                target=target,
                rank=16,
                expected_steps=250,
            )
        full_weight.unlink()
        model_path.unlink()
        with self.assertRaisesRegex(Mix2KV4LoRAError, "adapter model"):
            _validate_adapter_metrics(
                metrics=metrics,
                config=config,
                target=target,
                rank=16,
                expected_steps=250,
            )

    def test_longest_rows_rejects_missing_or_duplicate_token_ids(self) -> None:
        rows = [
            {"id": "a", "messages": []},
            {"id": "b", "messages": []},
        ]
        valid = [
            {"id": "a", "rendered_tokens": 10},
            {"id": "b", "rendered_tokens": 20},
        ]
        self.assertEqual(_longest_rows(rows, valid, 1)[0]["id"], "b")
        with self.assertRaisesRegex(Mix2KV4LoRAError, "ID 계약"):
            _longest_rows(rows, [valid[0], valid[0]], 1)
        with self.assertRaisesRegex(Mix2KV4LoRAError, "ID 계약"):
            _longest_rows(rows, [{"id": "missing", "rendered_tokens": 20}], 1)

    def _fixture(
        self, *, step: int = 50, rank: int = 16
    ) -> tuple[Path, Path, dict[str, str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name).resolve() / "run"
        trainer = target / "trainer"
        checkpoint = trainer / f"checkpoint-{step}"
        checkpoint.mkdir(parents=True)
        identity = {"build_sha256": "a" * 64}
        (target / "training_state.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "running",
                    "rank": rank,
                    "run_id": "run-test",
                    "identity": identity,
                    "resume_from_checkpoint": None,
                    "started_at_utc": "2026-09-02T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        for name in REQUIRED_RESUME_CHECKPOINT_FILES:
            payload = b"checkpoint"
            if name == "trainer_state.json":
                payload = json.dumps({"global_step": step, "max_steps": 250}).encode()
            elif name == "adapter_config.json":
                payload = json.dumps(
                    {"peft_type": "LORA", "r": rank, "use_rslora": True}
                ).encode()
            (checkpoint / name).write_bytes(payload)
        return target, trainer, identity

    def test_valid_incomplete_checkpoint_is_selected(self) -> None:
        target, trainer, identity = self._fixture()
        selected = _validated_resume_checkpoint(
            target=target,
            trainer_root=trainer,
            identity=identity,
            rank=16,
            run_id="run-test",
            expected_steps=250,
        )
        self.assertEqual(selected, str(trainer / "checkpoint-50"))

    def test_checkpoint_requires_bound_running_state(self) -> None:
        target, trainer, identity = self._fixture()
        (target / "training_state.json").unlink()
        with self.assertRaisesRegex(Mix2KV4LoRAError, "lineage"):
            _validated_resume_checkpoint(
                target=target,
                trainer_root=trainer,
                identity=identity,
                rank=16,
                run_id="run-test",
                expected_steps=250,
            )

    def test_checkpoint_rank_and_trainer_step_are_verified(self) -> None:
        target, trainer, identity = self._fixture()
        (trainer / "checkpoint-50" / "adapter_config.json").write_text(
            json.dumps({"peft_type": "LORA", "r": 8, "use_rslora": True}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(Mix2KV4LoRAError, "rank"):
            _validated_resume_checkpoint(
                target=target,
                trainer_root=trainer,
                identity=identity,
                rank=16,
                run_id="run-test",
                expected_steps=250,
            )

    def test_completed_step_checkpoint_needs_separate_recovery_audit(self) -> None:
        target, trainer, identity = self._fixture(step=250)
        with self.assertRaisesRegex(Mix2KV4LoRAError, "gradient audit"):
            _validated_resume_checkpoint(
                target=target,
                trainer_root=trainer,
                identity=identity,
                rank=16,
                run_id="run-test",
                expected_steps=250,
            )


if __name__ == "__main__":
    unittest.main()
