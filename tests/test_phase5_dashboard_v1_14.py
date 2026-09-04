# test_phase5_dashboard_v1_14.py - R16 adapter·2K 데이터·raw 진단 경계를 검증한다.

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.training.mix2k_v4_lora import Mix2KV4LoRAError
from scripts.training.phase5_dashboard_v1_14 import (
    DEFAULT_CONFIG,
    GPU_BUSY_CODE,
    V114_ASSET_ROOT,
    DashboardRequestError,
    Phase5DashboardError,
    _direct_training_candidates,
    _load_engine_model,
    _validate_lora_adapter_artifacts,
    audit_bound_output,
    ensure_dataset_sample_access,
    execute_manual_generation,
    validate_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _binding() -> dict[str, object]:
    value = {
        "chart": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "pillars": {
                    "year": {"ganzhi": "戊辰"},
                    "month": {"ganzhi": "甲子"},
                    "day": {"ganzhi": "乙丑"},
                    "hour": {"ganzhi": "壬午"},
                },
                "day_master": {"stem": "乙", "element": "목", "polarity": "음"},
            },
            "message": "원국 계산 완료",
            "limitations": [],
        },
        "period": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "period": {
                    "period_type": "day",
                    "target_date": "2026-09-02",
                    "start_date": "2026-09-02",
                    "end_date": "2026-09-02",
                    "timezone": "Asia/Seoul",
                    "evaluation_local_time": "12:00",
                },
                "pillars": {
                    "year": {"ganzhi": "丙午"},
                    "month": {"ganzhi": "丙申"},
                    "day": {"ganzhi": "己卯"},
                },
            },
            "message": "단일 일진 계산 완료",
            "limitations": [],
        },
    }
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return {
        "schema_version": "1.1.0",
        "binding_id": "saju-chart-day-dashboard-binding-v1.1.0",
        "capability_sha256": "e" * 64,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 1,
        "value": value,
    }


def _adapter_fixture(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    adapter_root = root / "trainer/final_adapter"
    adapter_root.mkdir(parents=True)
    adapter_config = {
        "base_model_name_or_path": (
            "/any/root/models/saju_1b_baseline/kanana-2-1.3b-instruct/"
            "bf4786aa2a1908adce942d53976270132732f720"
        ),
        "bias": "none",
        "inference_mode": True,
        "lora_alpha": 32,
        "lora_dropout": 0.05,
        "modules_to_save": None,
        "peft_type": "LORA",
        "r": 16,
        "target_modules": [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        "task_type": "CAUSAL_LM",
        "use_rslora": True,
    }
    _write_json(adapter_root / "adapter_config.json", adapter_config)
    (adapter_root / "adapter_model.safetensors").write_bytes(b"adapter-only")
    completed = "2026-09-04T07:22:27.718479Z"
    engine: dict[str, object] = {
        "label": "K0 + LoRA r16",
        "kind": "lora_adapter",
        "revision": "train-f340a82c76d3",
        "rank": 16,
        "base_engine_id": "k0_instruct",
        "resolved_path": adapter_root,
        "data_build_id": "build-54836f556b4f",
        "data_build_sha256": (
            "54836f556b4f5eab0b82c5e21659b3ba23ff591d715a99677e4378c73eb370f3"
        ),
        "model_sha256": _sha256(adapter_root / "adapter_model.safetensors"),
        "required_file_sha256": {
            "adapter_config.json": _sha256(adapter_root / "adapter_config.json"),
            "adapter_model.safetensors": _sha256(
                adapter_root / "adapter_model.safetensors"
            ),
        },
    }
    manifest = {
        "adapter_only": True,
        "base_weights_unchanged": True,
        "completed": True,
        "completed_at_utc": completed,
        "data_build_id": engine["data_build_id"],
        "full_fine_tuning_performed": False,
        "identity": {
            "config_sha256": (
                "d7c5db056be927319617ac4b932acb9e37d9f9a2e6478598d20f2b7ce12fa728"
            ),
            "data_build_sha256": engine["data_build_sha256"],
            "mode": "train",
            "rank": 16,
            "script_sha256": (
                "7274abaa3f4750ab1fc2d266980fcf18ff80e02c5d14c45688bd7bb33bfc6a81"
            ),
        },
        "ki20_training_performed": False,
        "max_length": 2048,
        "metrics": {
            "adapter_config_sha256": engine["required_file_sha256"][
                "adapter_config.json"
            ],
            "adapter_model_sha256": engine["model_sha256"],
            "adapter_reload_match": True,
            "adapter_reload_rank": 16,
            "global_step": 250,
            "target_linear_modules": 224,
            "trainable_parameters": 18_677_760,
        },
        "num_train_epochs": 1,
        "production_promotion_allowed": False,
        "rank": 16,
        "rows": 2000,
        "run_id": engine["revision"],
        "schema_version": "1.0.0",
        "sealed_blind_accessed": False,
        "status": "training_completed",
    }
    manifest_path = root / "training_manifest.json"
    state_path = root / "training_state.json"
    _write_json(manifest_path, manifest)
    _write_json(
        state_path,
        {
            "completed_at_utc": completed,
            "rank": 16,
            "run_id": engine["revision"],
            "schema_version": "1.0.0",
            "status": "completed",
        },
    )
    engine["training_manifest_path"] = manifest_path
    engine["training_state_path"] = state_path
    engine["training_manifest"] = {"path": "unused", "sha256": _sha256(manifest_path)}
    engine["training_state"] = {"path": "unused", "sha256": _sha256(state_path)}
    base = {
        "kind": "fixed_snapshot",
        "revision": "bf4786aa2a1908adce942d53976270132732f720",
        "model_sha256": "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942",
        "resolved_path": root / "base",
        "required_file_sha256": {},
    }
    context = {
        "inference_engines": {
            "engines": {"k0_instruct": base, "lora_r16": engine}
        }
    }
    return context, engine


class DashboardV114Tests(unittest.TestCase):
    def test_committed_config_and_assets_are_r16_diagnostic_only(self) -> None:
        config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
        validate_config(config)
        self.assertEqual(config["server"]["port"], 8767)
        self.assertEqual(config["model_check"]["generation"]["max_input_tokens"], 4096)
        self.assertEqual(config["model_check"]["generation"]["max_new_tokens"], 4096)
        self.assertFalse(config["governance"]["production_promotion_allowed"])
        html = (V114_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V114_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn("LoRA r16 진단 대시보드", html)
        self.assertIn('value="k0_vs_lora_r16"', html)
        self.assertIn("mix2k_v4_train", javascript)
        self.assertIn("RUNTIME_GROUNDING_WARNING", javascript)

    def test_adapter_manifest_and_weights_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, engine = _adapter_fixture(Path(temporary))
            _validate_lora_adapter_artifacts(context, engine)
            engine["resolved_path"].joinpath("adapter_model.safetensors").write_bytes(
                b"tampered"
            )
            with self.assertRaisesRegex(Phase5DashboardError, "SHA-256"):
                _validate_lora_adapter_artifacts(context, engine)

    def test_lora_loader_wraps_verified_k0_without_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            context, engine = _adapter_fixture(Path(temporary))
            base_model = Mock()
            adapter_model = Mock()
            peft_model = Mock()
            peft_model.from_pretrained.return_value = adapter_model
            fake_module = types.SimpleNamespace(PeftModel=peft_model)
            with (
                patch(
                    "scripts.training.phase5_dashboard_v1_14._load_model",
                    return_value=(Mock(), Mock(), base_model),
                ) as load_model,
                patch.dict(sys.modules, {"peft": fake_module}),
            ):
                _, _, loaded = _load_engine_model(context, "lora_r16")
            self.assertIs(loaded, adapter_model)
            load_model.assert_called_once()
            peft_model.from_pretrained.assert_called_once_with(
                base_model,
                engine["resolved_path"],
                is_trainable=False,
                local_files_only=True,
            )

    def test_direct_training_loader_validates_rows_without_phase4_join(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows_path = root / "training/train.jsonl"
            rows_path.parent.mkdir(parents=True)
            rows = [
                {
                    "assistant_only_loss": True,
                    "dataset_version": "mix2k-v4-chart-day-8k",
                    "id": f"m2v4_{index:024x}",
                    "messages": [
                        {"role": "system", "content": "근거만 사용하세요."},
                        {"role": "user", "content": "질문"},
                        {"role": "assistant", "content": "답변"},
                    ],
                    "restricted_local_only": False,
                    "runtime_snapshot_sha256": None,
                    "schema_version": "1.0.0",
                    "task_axis": "hard_fact_short_qa",
                }
                for index in range(10)
            ]
            rows_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            manifest_path = root / "build_manifest.json"
            manifest = {
                "artifact_sha256": {"training/train_2000.jsonl": _sha256(rows_path)},
                "assistant_only_loss": True,
                "build_id": "build-54836f556b4f",
                "build_sha256": (
                    "54836f556b4f5eab0b82c5e21659b3ba23ff591d715a99677e4378c73eb370f3"
                ),
                "compact_projection_used_for_training": False,
                "cross_provider_teacher_contract_met": False,
                "dataset_version": "mix2k-v4-chart-day-8k",
                "full_runtime_snapshot_used": True,
                "lora_experimental_training_allowed": True,
                "production_promotion_allowed": False,
                "rows": 10,
                "schema_version": "1.1.0",
                "sealed_blind_accessed": False,
                "teacher_contract_mode": "authorized_codex_only_fallback",
                "truncation": False,
            }
            _write_json(manifest_path, manifest)
            split = {
                "label": "fixture",
                "path": "training/train.jsonl",
                "sha256": _sha256(rows_path),
                "manifest_path": "build_manifest.json",
                "manifest_sha256": _sha256(manifest_path),
                "dataset_version": "mix2k-v4-chart-day-8k",
                "record_schema_version": "1.0.0",
                "rows": 10,
                "axes": {"hard_fact_short_qa": 10},
            }
            candidates = _direct_training_candidates({"repo_root": root}, split)
            self.assertEqual(len(candidates), 10)
            self.assertNotIn("runtime_snapshot_sha256", candidates[0])

    def test_regression_audit_keeps_label_confusion_as_warning(self) -> None:
        result = audit_bound_output(
            "오늘의 흐름을 원국과 함께 이야기해줘",
            "乙丑 원국과 오늘 일진 丙午를 기준으로 봅니다.",
            _binding(),
        )
        self.assertFalse(result["passed"])
        self.assertIn("natal_pillars_omitted", result["reasons"])
        self.assertIn("period_year_labeled_as_day", result["reasons"])
        self.assertEqual(result["warning_code"], "RUNTIME_GROUNDING_WARNING")

    def test_failed_grounding_is_persisted_raw_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_root = root / "runs/KI20-MIX-v2/v1.2.0/run-123456abcdef"
            run_root.mkdir(parents=True)
            config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
            engines = json.loads(json.dumps(config["inference_engines"]))
            context = {
                "repo_root": root,
                "run_root": run_root,
                "config": config,
                "manifest": {
                    "run_id": "KI20-MIX-v2",
                    "run_build_id": "run-123456abcdef",
                    "run_sha256": "a" * 64,
                },
                "inference_engines": engines,
                "prompt_profiles": {
                    "default_profile": "guided_runtime_v2",
                    "bound_profile": "bound_chart_v2",
                    "legacy_profile": "raw_legacy",
                    "profiles": {
                        "guided_runtime_v2": {
                            "label": "구조화 입력 진단 v2",
                            "description": "진단",
                            "system_prompt_text": "근거만 사용하세요.",
                            "system_prompt_sha256": "c" * 64,
                            "production_like": False,
                            "diagnostic_only": True,
                        },
                        "bound_chart_v2": {
                            "label": "승인 원국·단일 일진 연결 v2",
                            "description": "연결",
                            "system_prompt_text": "승인 사실만 사용하세요.",
                            "system_prompt_sha256": "d" * 64,
                            "production_like": True,
                            "diagnostic_only": False,
                        },
                        "raw_no_system": {
                            "label": "무지시 원출력",
                            "description": "진단",
                            "system_prompt_text": None,
                            "system_prompt_sha256": None,
                            "production_like": False,
                            "diagnostic_only": True,
                        },
                    },
                },
                "runtime_canary_active": False,
                "chart_only_runtime_active": True,
            }
            raw = "乙丑 원국과 오늘 일진 丙午를 기준으로 봅니다."
            generated = {
                "output": raw,
                "input_tokens": 120,
                "omitted_messages": 0,
                "peak_allocated_bytes": 1,
                "gpu_total_memory_used_mib": 100,
            }
            with (
                patch(
                    "scripts.training.phase5_dashboard_v1_14._generation_gate",
                    return_value={"allowed": True, "reasons": []},
                ),
                patch(
                    "scripts.training.phase5_dashboard_v1_14._engine_availability",
                    return_value={"available": True, "reasons": []},
                ),
                patch(
                    "scripts.training.phase5_dashboard_v1_14._generate_engine_conversation",
                    return_value=generated,
                ) as generation,
            ):
                result = execute_manual_generation(
                    context,
                    "오늘의 흐름을 원국과 함께 이야기해줘",
                    engine_selection="lora_r16",
                    runtime_binding=_binding(),
                )
            self.assertEqual(generation.call_count, 1)
            self.assertEqual(result["output"], raw)
            self.assertEqual(result["session"]["schema_version"], "1.6.0")
            self.assertFalse(result["grounding_gate"]["passed_by_engine"]["lora_r16"])
            self.assertIn(raw, json.dumps(result["session"], ensure_ascii=False))
            self.assertTrue(
                result["session"]["messages"][-1]["diagnostics"][
                    "raw_output_preserved"
                ]
            )

    def test_remote_share_blocks_only_splits_with_restricted_axes(self) -> None:
        context = {
            "config": {
                "dataset_browser": {
                    "restricted_axes": ["aihub_empathy_single"],
                    "splits": {
                        "restricted": {
                            "axes": {"aihub_empathy_single": 10, "safe": 10}
                        },
                        "safe": {"axes": {"safe": 10}},
                    },
                }
            }
        }
        with self.assertRaises(DashboardRequestError) as caught:
            ensure_dataset_sample_access(
                context,
                remote_share_active=True,
                split_id="restricted",
                axis="all",
            )
        self.assertEqual(caught.exception.reason_code, "RESTRICTED_DATASET_LOCAL_ONLY")
        ensure_dataset_sample_access(
            context, remote_share_active=True, split_id="safe", axis="all"
        )

    def test_gpu_lock_conflict_is_reported_without_generation(self) -> None:
        context = {"repo_root": REPO_ROOT}
        with patch(
            "scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock",
            side_effect=Mix2KV4LoRAError("busy"),
        ), self.assertRaisesRegex(Phase5DashboardError, GPU_BUSY_CODE):
            execute_manual_generation(context, "질문")


if __name__ == "__main__":
    unittest.main()
