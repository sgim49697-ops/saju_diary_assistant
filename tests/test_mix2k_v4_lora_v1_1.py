# test_mix2k_v4_lora_v1_1.py - reviewed-repair v1.1 R16 학습의 fail-closed 경계를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    jsonl_bytes,
    sha256_bytes,
    sha256_file,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.training import mix2k_v4_lora as core
from scripts.training.mix2k_v4_lora_v1_1 import (
    ARTIFACT_PATHS,
    CHECKPOINT_FINGERPRINT_FIELDS,
    DEFAULT_CONFIG,
    PINNED,
    RANK,
    Mix2KV4LoRAV11Error,
    _run_identity,
    _stats,
    _valid_checkpoint_fingerprint,
    _valid_training_row,
    _validate_closed_model_snapshot,
    _validate_config,
    _validate_data_build,
    _validate_final_adapter_artifact,
    _validate_token_summary,
    _validated_resume_checkpoint,
    run_preflight,
)


class Mix2KV4LoRAV11ContractTest(unittest.TestCase):
    def _config(self) -> dict[str, object]:
        return json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))

    def _write_config(self, value: dict[str, object]) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="mix2k-v11-lora-config-")
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name).resolve() / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _pinned_config(self) -> dict[str, object]:
        config = self._config()
        required = config["required_data"]
        assert isinstance(required, dict)
        required["pin_state"] = PINNED
        pins = required["pins"]
        assert isinstance(pins, dict)
        build_sha = "b" * 64
        pins.update(
            {
                "source_config_sha256": "a" * 64,
                "generator_sha256": "c" * 64,
                "final_build_id": f"build-{build_sha[:12]}",
                "final_build_sha256": build_sha,
                "final_manifest_sha256": "d" * 64,
                "artifact_sha256": {path: "e" * 64 for path in ARTIFACT_PATHS},
            }
        )
        return config

    def _checkpoint_fixture(
        self, *, step: int = 50
    ) -> tuple[Path, Path, dict[str, object], dict[str, object]]:
        import torch
        from safetensors.torch import save_file

        temporary = tempfile.TemporaryDirectory(prefix="mix2k-v11-resume-")
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name).resolve() / "run"
        checkpoint = target / f"trainer/checkpoint-{step}"
        checkpoint.mkdir(parents=True)
        config = self._config()
        identity = {"run": "test"}
        (target / "training_state.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.1.0",
                    "status": "running",
                    "rank": RANK,
                    "run_id": "train-test",
                    "identity": identity,
                    "resume_checkpoint": None,
                    "started_at_utc": "2026-09-04T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        modules = [
            "down_proj",
            "gate_proj",
            "k_proj",
            "o_proj",
            "q_proj",
            "up_proj",
            "v_proj",
        ]
        adapter = {
            "peft_type": "LORA",
            "task_type": "CAUSAL_LM",
            "r": RANK,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "bias": "none",
            "use_rslora": True,
            "modules_to_save": None,
            "target_modules": modules,
        }
        (checkpoint / "adapter_config.json").write_text(
            json.dumps(adapter), encoding="utf-8"
        )
        tensors = {}
        for index in range(32):
            for module in modules:
                prefix = f"base_model.model.layers.{index:03d}.{module}"
                tensors[f"{prefix}.lora_A.weight"] = torch.ones(
                    (RANK, 1), dtype=torch.float32
                )
                tensors[f"{prefix}.lora_B.weight"] = torch.ones(
                    (1, RANK), dtype=torch.float32
                )
        save_file(tensors, checkpoint / "adapter_model.safetensors")
        torch.save({"state": {}, "param_groups": []}, checkpoint / "optimizer.pt")
        torch.save({"last_epoch": step}, checkpoint / "scheduler.pt")
        torch.save({"cpu": torch.get_rng_state()}, checkpoint / "rng_state.pth")
        torch.save({"output_dir": "test"}, checkpoint / "training_args.bin")
        (checkpoint / "trainer_state.json").write_text(
            json.dumps({"global_step": step, "max_steps": 250}),
            encoding="utf-8",
        )
        (checkpoint / "README.md").write_text("adapter", encoding="utf-8")
        return target, checkpoint, config, identity

    def test_default_contract_is_explicitly_unpinned_and_r16_only(self) -> None:
        config = _validate_config(DEFAULT_CONFIG)

        self.assertEqual(
            config["required_data"]["pin_state"], "unpinned_pending_final_build"
        )
        self.assertEqual(config["lora"]["ranks"], [RANK])
        self.assertEqual(config["training"]["num_train_epochs"], 1)
        self.assertEqual(config["training"]["expected_optimizer_steps"], 250)
        self.assertFalse(config["governance"]["production_promotion_allowed"])
        self.assertTrue(
            all(
                value is None
                for value in config["required_data"]["pins"]["artifact_sha256"].values()
            )
        )

    def test_unpinned_contract_blocks_preflight_before_model_or_gpu_access(
        self,
    ) -> None:
        with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "unpinned"):
            run_preflight(
                config_path=DEFAULT_CONFIG,
                data_build=Path("/does/not/exist"),
                model_root=Path("/does/not/exist"),
                artifact_root=Path("/does/not/exist"),
                execute=False,
            )

    def test_partial_pin_is_rejected_but_complete_pin_shape_is_accepted(self) -> None:
        partial = self._config()
        required = partial["required_data"]
        assert isinstance(required, dict)
        pins = required["pins"]
        assert isinstance(pins, dict)
        pins["source_config_sha256"] = "a" * 64
        with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "부분 pin"):
            _validate_config(self._write_config(partial))

        pinned_path = self._write_config(self._pinned_config())
        self.assertEqual(
            _validate_config(pinned_path)["required_data"]["pin_state"], PINNED
        )

    def test_config_cannot_enable_other_rank_or_production(self) -> None:
        rank_changed = self._config()
        lora = rank_changed["lora"]
        assert isinstance(lora, dict)
        lora["ranks"] = [8, 16]
        with self.assertRaises(Mix2KV4LoRAV11Error):
            _validate_config(self._write_config(rank_changed))

        production = self._config()
        governance = production["governance"]
        assert isinstance(governance, dict)
        governance["production_promotion_allowed"] = True
        with self.assertRaises(Mix2KV4LoRAV11Error):
            _validate_config(self._write_config(production))

    def test_training_rows_require_v11_schema_and_axis_runtime_contract(self) -> None:
        runtime_row = {
            "schema_version": "1.1.0",
            "dataset_version": "mix2k-v4-chart-day-8k",
            "id": "row-1",
            "task_axis": "chart_day_today_flow",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
            "assistant_only_loss": True,
            "runtime_snapshot_sha256": "a" * 64,
            "restricted_local_only": False,
        }
        self.assertTrue(_valid_training_row(runtime_row))

        old_schema = {**runtime_row, "schema_version": "1.0.0"}
        self.assertFalse(_valid_training_row(old_schema))
        missing_runtime = {**runtime_row, "runtime_snapshot_sha256": None}
        self.assertFalse(_valid_training_row(missing_runtime))
        empathy = {
            **runtime_row,
            "task_axis": "general_korean_empathy",
            "runtime_snapshot_sha256": None,
        }
        self.assertTrue(_valid_training_row(empathy))

    def test_token_summary_is_recomputed_from_all_rows(self) -> None:
        token_rows = [
            {
                "rendered_tokens": 100 + index % 3,
                "prompt_tokens": 80,
                "supervised_assistant_tokens": 20 + index % 3,
            }
            for index in range(2000)
        ]
        rendered = [row["rendered_tokens"] for row in token_rows]
        prompt = [row["prompt_tokens"] for row in token_rows]
        supervised = [row["supervised_assistant_tokens"] for row in token_rows]
        summary = {
            "schema_version": "1.0.0",
            "rows": 2000,
            "rendered_tokens": _stats(rendered),
            "prompt_tokens": _stats(prompt),
            "supervised_assistant_tokens": _stats(supervised),
            "rows_over_2048": 0,
            "rows_over_3584": 0,
            "rows_over_4096": 0,
            "rows_over_8192": 0,
            "truncated_rows": 0,
            "zero_assistant_mask_rows": 0,
            "missing_supervised_eos_rows": 0,
            "user_system_loss_leakage_rows": 0,
            "selected_max_length": 2048,
            "provisional_ladder_value": 2048,
            "many_rows_over_2048": False,
            "runtime_projection_review_required": False,
            "training_blocked_pending_projection_review": False,
            "candidate_validation": {
                "rows": 2000,
                "axes": dict(sorted(EXPECTED_AXES.items())),
                "origins": {
                    "parent_v1.0.1": 1600,
                    "regenerated_v1.1.0": 400,
                },
                "repaired_cross_provider_pass_rows": 400,
                "all_2000_rows_cross_provider_contract_met": False,
            },
            "parent_comparison": {
                "schema_version": "1.0.0",
                "comparison": "repo_native_v1.1.0_minus_parent_v1.0.1",
                "rows": 2000,
                "audit_provenance_removed_from_model_context": False,
                "compact_projection_used": False,
                "production_like_format_preserved": True,
            },
        }
        _validate_token_summary(summary, token_rows)

        damaged = deepcopy(summary)
        damaged["rendered_tokens"]["maximum"] += 1
        with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "token audit summary"):
            _validate_token_summary(damaged, token_rows)

    def test_normal_finalizer_manifest_with_source_dependencies_is_accepted(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-final-") as directory:
            root = Path(directory).resolve()
            axes = [axis for axis, count in EXPECTED_AXES.items() for _ in range(count)]
            rows = []
            token_rows = []
            for index, axis in enumerate(axes):
                runtime_hash = (
                    "a" * 64
                    if axis
                    in {
                        "structured_fact_schema_literacy",
                        "chart_facts_natural_explanation",
                        "chart_day_today_flow",
                        "followup_explain_grounding",
                    }
                    else None
                )
                rows.append(
                    {
                        "schema_version": "1.1.0",
                        "dataset_version": DATASET_VERSION,
                        "id": f"row-{index:04d}",
                        "task_axis": axis,
                        "messages": [
                            {"role": "system", "content": "system"},
                            {"role": "user", "content": "user"},
                            {"role": "assistant", "content": "assistant"},
                        ],
                        "assistant_only_loss": True,
                        "runtime_snapshot_sha256": runtime_hash,
                        "restricted_local_only": False,
                    }
                )
                token_rows.append(
                    {
                        "id": f"row-{index:04d}",
                        "task_axis": axis,
                        "rendered_tokens": 100,
                        "prompt_tokens": 80,
                        "supervised_assistant_tokens": 20,
                        "truncated": False,
                        "assistant_mask_nonzero": True,
                        "assistant_mask_sha256": "b" * 64,
                        "final_eos_supervised": True,
                        "user_system_loss_leakage_tokens": 0,
                    }
                )
            stats_100 = _stats([100] * 2000)
            stats_80 = _stats([80] * 2000)
            stats_20 = _stats([20] * 2000)
            summary = {
                "schema_version": "1.0.0",
                "rows": 2000,
                "rendered_tokens": stats_100,
                "prompt_tokens": stats_80,
                "supervised_assistant_tokens": stats_20,
                "rows_over_2048": 0,
                "rows_over_3584": 0,
                "rows_over_4096": 0,
                "rows_over_8192": 0,
                "truncated_rows": 0,
                "zero_assistant_mask_rows": 0,
                "missing_supervised_eos_rows": 0,
                "user_system_loss_leakage_rows": 0,
                "selected_max_length": 2048,
                "provisional_ladder_value": 2048,
                "many_rows_over_2048": False,
                "runtime_projection_review_required": False,
                "training_blocked_pending_projection_review": False,
                "candidate_validation": {
                    "rows": 2000,
                    "axes": dict(sorted(EXPECTED_AXES.items())),
                    "origins": {
                        "parent_v1.0.1": 1600,
                        "regenerated_v1.1.0": 400,
                    },
                    "repaired_cross_provider_pass_rows": 400,
                    "all_2000_rows_cross_provider_contract_met": False,
                },
                "parent_comparison": {
                    "schema_version": "1.0.0",
                    "comparison": "repo_native_v1.1.0_minus_parent_v1.0.1",
                    "rows": 2000,
                    "audit_provenance_removed_from_model_context": False,
                    "compact_projection_used": False,
                    "production_like_format_preserved": True,
                },
            }
            artifacts = {
                "evaluation/dev_cases_200.jsonl": jsonl_bytes([{"id": "dev"}]),
                "provenance/combined_candidates_2000.jsonl": jsonl_bytes(
                    [{"id": "candidate"}]
                ),
                "provenance/row_lineage_2000.jsonl": jsonl_bytes([{"id": "lineage"}]),
                "reports/lineage_summary.json": core._json_bytes({"rows": 2000}),
                "reports/package_audit.json": core._json_bytes({"passed": True}),
                "reports/token_audit_2000.jsonl": jsonl_bytes(token_rows),
                "reports/token_audit_summary.json": core._json_bytes(summary),
                "training/train_2000.jsonl": jsonl_bytes(rows),
            }
            artifact_hashes = {
                relative: sha256_bytes(payload)
                for relative, payload in artifacts.items()
            }
            config = self._pinned_config()
            pins = config["required_data"]["pins"]
            source_config = Path(
                "configs/data_versions/saju_1b_baseline/"
                "mix2k-v4-reviewed-repair-v1.1.0.json"
            ).resolve()
            generator = Path("scripts/data/mix2k_v4_reviewed_repair.py").resolve()
            pins["source_config_sha256"] = sha256_file(source_config)
            pins["generator_sha256"] = sha256_file(generator)
            pins["artifact_sha256"] = artifact_hashes
            identity = {
                "dataset_version": DATASET_VERSION,
                "artifact_revision": "v1.1.0",
                "config_sha256": pins["source_config_sha256"],
                "generator_sha256": pins["generator_sha256"],
                "source_dependency_sha256": {"contracts": "c" * 64},
                "prepare_target_sha256": "d" * 64,
                "repair_teacher_manifest_sha256": "e" * 64,
                "repair_candidates_sha256": "f" * 64,
                "parent_final_build_sha256": "1" * 64,
                "parent_teacher_candidates_sha256": "2" * 64,
                "review_package_sha256": "3" * 64,
                "base_model_files": config["base_model"]["files"],
                "artifact_sha256": artifact_hashes,
            }
            build_sha = sha256_bytes(canonical_json_bytes(identity))
            build_id = f"build-{build_sha[:12]}"
            data_build = root / build_id
            for relative, payload in artifacts.items():
                path = data_build / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            manifest = {
                "schema_version": "1.1.0",
                "dataset_version": DATASET_VERSION,
                "artifact_revision": "v1.1.0",
                "build_id": build_id,
                "build_sha256": build_sha,
                "identity": identity,
                "rows": 2000,
                "axes": dict(sorted(EXPECTED_AXES.items())),
                "selected_max_length": 2048,
                "assistant_only_loss": True,
                "truncation": False,
                "full_runtime_snapshot_used": True,
                "compact_projection_used_for_training": False,
                "inherited_parent_rows": 1600,
                "regenerated_cross_provider_rows": 400,
                "all_2000_rows_cross_provider_contract_met": False,
                "development_targets_accessed": False,
                "sealed_blind_accessed": False,
                "training_execution_allowed": True,
                "lora_r16_experimental_training_allowed": True,
                "training_performed": False,
                "production_promotion_allowed": False,
                "artifact_sha256": artifact_hashes,
            }
            manifest_path = data_build / "build_manifest.json"
            manifest_path.write_bytes(core._json_bytes(manifest))
            pins["final_build_id"] = build_id
            pins["final_build_sha256"] = build_sha
            pins["final_manifest_sha256"] = sha256_file(manifest_path)

            loaded, _digest, loaded_rows, loaded_tokens = _validate_data_build(
                data_build, config
            )
            self.assertEqual(loaded["build_id"], build_id)
            self.assertEqual(len(loaded_rows), 2000)
            self.assertEqual(len(loaded_tokens), 2000)

    def test_run_identity_binds_new_runner_core_and_preflight(self) -> None:
        config_path = self._write_config(self._pinned_config())
        config = _validate_config(config_path)
        data_manifest = {"build_sha256": "f" * 64}
        preflight_id, preflight_identity = _run_identity(
            mode="preflight",
            config_path=config_path,
            config=config,
            data_manifest=data_manifest,
            data_manifest_sha256="1" * 64,
        )
        train_id, train_identity = _run_identity(
            mode="train",
            config_path=config_path,
            config=config,
            data_manifest=data_manifest,
            data_manifest_sha256="1" * 64,
            preflight_manifest_sha256="2" * 64,
        )

        self.assertTrue(preflight_id.startswith("preflight-"))
        self.assertTrue(train_id.startswith("train-"))
        self.assertEqual(preflight_identity["rank"], RANK)
        self.assertIn("runner_sha256", preflight_identity)
        self.assertIn("training_core_sha256", preflight_identity)
        self.assertEqual(train_identity["preflight_manifest_sha256"], "2" * 64)

    def test_resume_checkpoint_is_parsed_hashed_and_bound_to_state(self) -> None:
        target, checkpoint, config, identity = self._checkpoint_fixture()
        selected, fingerprint = _validated_resume_checkpoint(
            target=target,
            trainer_root=target / "trainer",
            identity=identity,
            config=config,
            run_id="train-test",
        )
        self.assertEqual(selected, str(checkpoint))
        self.assertIsNotNone(fingerprint)
        assert fingerprint is not None
        self.assertEqual(set(fingerprint), CHECKPOINT_FINGERPRINT_FIELDS)
        self.assertTrue(_valid_checkpoint_fingerprint(fingerprint))
        state = json.loads((target / "training_state.json").read_text(encoding="utf-8"))
        state["resume_checkpoint"] = fingerprint
        (target / "training_state.json").write_text(json.dumps(state), encoding="utf-8")
        (checkpoint / "README.md").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "fingerprint"):
            _validated_resume_checkpoint(
                target=target,
                trainer_root=target / "trainer",
                identity=identity,
                config=config,
                run_id="train-test",
            )

    def test_resume_checkpoint_rejects_missing_invalid_and_override_files(self) -> None:
        mutations = ("missing_rng", "invalid_adapter", "full_weight", "directory")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                target, checkpoint, config, identity = self._checkpoint_fixture()
                if mutation == "missing_rng":
                    (checkpoint / "rng_state.pth").unlink()
                elif mutation == "invalid_adapter":
                    (checkpoint / "adapter_model.safetensors").write_bytes(b"invalid")
                elif mutation == "full_weight":
                    (checkpoint / "model.safetensors").write_bytes(b"override")
                else:
                    (checkpoint / "unexpected").mkdir()
                with self.assertRaises(Mix2KV4LoRAV11Error):
                    _validated_resume_checkpoint(
                        target=target,
                        trainer_root=target / "trainer",
                        identity=identity,
                        config=config,
                        run_id="train-test",
                    )

    def test_resume_checkpoint_rejects_non_save_step_and_symlink(self) -> None:
        for step in (49, 51):
            with self.subTest(step=step):
                target, _checkpoint, config, identity = self._checkpoint_fixture(
                    step=step
                )
                with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "step"):
                    _validated_resume_checkpoint(
                        target=target,
                        trainer_root=target / "trainer",
                        identity=identity,
                        config=config,
                        run_id="train-test",
                    )
        target, checkpoint, config, identity = self._checkpoint_fixture()
        source = checkpoint / "README.md"
        backup = checkpoint / "README.backup"
        source.rename(backup)
        os.symlink(backup, source)
        with self.assertRaises(Mix2KV4LoRAV11Error):
            _validated_resume_checkpoint(
                target=target,
                trainer_root=target / "trainer",
                identity=identity,
                config=config,
                run_id="train-test",
            )

    def test_final_adapter_is_live_parsed_with_complete_config(self) -> None:
        target, checkpoint, config, _identity = self._checkpoint_fixture()
        final_adapter = target / "trainer/final_adapter"
        final_adapter.mkdir()
        (final_adapter / "adapter_model.safetensors").write_bytes(
            (checkpoint / "adapter_model.safetensors").read_bytes()
        )
        model_root = target / "model" / ("b" * 40)
        model_root.mkdir(parents=True)
        adapter = json.loads(
            (checkpoint / "adapter_config.json").read_text(encoding="utf-8")
        )
        adapter.update(
            {
                "inference_mode": True,
                "use_dora": False,
                "use_qalora": False,
                "rank_pattern": {},
                "alpha_pattern": {},
                "exclude_modules": None,
                "base_model_name_or_path": str(model_root),
            }
        )
        config_path = final_adapter / "adapter_config.json"
        config_path.write_text(json.dumps(adapter), encoding="utf-8")
        _validate_final_adapter_artifact(
            config=config,
            target=target,
            model_root=model_root,
        )

        adapter["modules_to_save"] = ["lm_head"]
        config_path.write_text(json.dumps(adapter), encoding="utf-8")
        with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "config"):
            _validate_final_adapter_artifact(
                config=config,
                target=target,
                model_root=model_root,
            )

    def test_closed_base_snapshot_rejects_loader_visible_extra_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-model-") as directory:
            model_root = Path(directory).resolve()
            model_file = model_root / "model.safetensors"
            model_file.write_bytes(b"model")
            digest = sha256_file(model_file)
            config = {
                "base_model": {
                    "files": {"model.safetensors": digest},
                    "snapshot_allowlist": {"model.safetensors": digest},
                }
            }
            with patch(
                "scripts.training.mix2k_v4_lora_v1_1.core._validate_model_snapshot"
            ):
                self.assertEqual(
                    _validate_closed_model_snapshot(config, model_root),
                    {"model.safetensors": digest},
                )
                (model_root / "generation_config.json").write_text(
                    "{}", encoding="utf-8"
                )
                with self.assertRaisesRegex(Mix2KV4LoRAV11Error, "allowlist"):
                    _validate_closed_model_snapshot(config, model_root)


if __name__ == "__main__":
    unittest.main()
