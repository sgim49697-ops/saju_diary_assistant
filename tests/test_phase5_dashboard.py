# test_phase5_dashboard.py - KI20 로컬 대시보드의 live-read·보안·추론 차단 계약을 검증한다.

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from scripts.training.phase5_dashboard import (
    ASSET_ROOT,
    DEFAULT_CONFIG,
    DashboardHTTPServer,
    DashboardRequestError,
    Phase5DashboardError,
    _generate_loaded,
    _load_model,
    _parser,
    _select_probes,
    _service_snapshot,
    checkpoints_payload,
    dataset_samples_payload,
    dataset_splits_payload,
    execute_manual_generation,
    manual_session_payload,
    manual_sessions_payload,
    metrics_payload,
    prepare_context,
    read_live_metrics,
    status_payload,
    validate_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class DashboardFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
        self.config_path = (
            root
            / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.4.0.json"
        )
        self.config_path.parent.mkdir(parents=True)
        prompt_source = REPO_ROOT / self.config["prompt_profiles"]["profiles"][
            "guided_diagnostic_v1"
        ]["system_prompt"]["path"]
        prompt_target = root / prompt_source.relative_to(REPO_ROOT)
        prompt_target.parent.mkdir(parents=True)
        prompt_target.write_bytes(prompt_source.read_bytes())
        self.run_root = root / "runs/KI20-MIX-v2/v1.2.0/run-123456abcdef"
        self.run_root.mkdir(parents=True)
        model_roots = [
            self.run_root / "final",
            root / self.config["inference_engines"]["engines"]["k0_instruct"]["path"],
        ]
        for model_root in model_roots:
            model_root.mkdir(parents=True)
            for name in (
                "chat_template.jinja",
                "config.json",
                "configuration_kanana2_tiny.py",
                "model.safetensors",
                "modeling_kanana2_tiny.py",
                "tokenizer.json",
                "tokenizer_config.json",
            ):
                (model_root / name).write_bytes(b"fixture")
        self._write_json(self.run_root / "reload_summary.json", {"status": "passed"})
        self._write_json(
            self.run_root / "run_manifest.json",
            {
                "run_id": "KI20-MIX-v2",
                "run_build_id": "run-123456abcdef",
                "run_sha256": "a" * 64,
                "workspace_commit": "b" * 40,
                "status": "running",
                "production_promotion_allowed": False,
                "blind_source_test_inspected": False,
            },
        )
        self._write_json(
            self.run_root / "config.resolved.json",
            {
                "training": {
                    "logging_steps": 10,
                    "eval_steps": 250,
                    "save_steps": 250,
                    "expected_optimizer_steps": 2500,
                    "preserved_milestone_steps": [1250, 2500],
                },
                "operational_limits": {"max_total_gpu_memory_used_mib": 16384},
            },
        )
        self._write_json(
            self.run_root / "training_started.json",
            {
                "service_unit": "saju-ki20-test.service",
                "started_at_utc": "2026-08-29T12:00:00Z",
            },
        )
        self.metrics_path = self.run_root / "metrics.jsonl"
        self.metrics_path.write_text(
            "\n".join(
                json.dumps(value)
                for value in (
                    {
                        "global_step": 1,
                        "epoch": 0.0004,
                        "loss": 2.9,
                        "grad_norm": 21.0,
                        "gpu_total_memory_used_mib": 9900,
                    },
                    {
                        "global_step": 10,
                        "epoch": 0.004,
                        "loss": 2.7,
                        "grad_norm": 18.0,
                        "gpu_total_memory_used_mib": 9200,
                    },
                    {
                        "global_step": 250,
                        "epoch": 0.1,
                        "eval_loss": 0.7,
                        "eval_mean_token_accuracy": 0.84,
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        self._save_config()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def _save_config(self) -> None:
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def context(self) -> dict[str, object]:
        self._save_config()
        return prepare_context(self.root, self.config_path, self.run_root)


class Phase5DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DashboardFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_committed_config_and_cli_defaults_are_valid(self) -> None:
        config = json.loads((REPO_ROOT / DEFAULT_CONFIG).read_text(encoding="utf-8"))
        validate_config(config)
        args = _parser().parse_args(
            ["--run-root", "runs/KI20/x/run-123456abcdef", "probe"]
        )
        self.assertFalse(args.execute)
        invalid = json.loads(json.dumps(config))
        invalid["model_check"]["category_counts"]["empathy"] = "1"
        with self.assertRaisesRegex(Phase5DashboardError, "probe 범주"):
            validate_config(invalid)
        invalid_dataset = json.loads(json.dumps(config))
        invalid_dataset["dataset_browser"]["sealed_blind"][
            "sample_access_allowed"
        ] = True
        with self.assertRaisesRegex(Phase5DashboardError, "dataset browser"):
            validate_config(invalid_dataset)
        invalid_engine = json.loads(json.dumps(config))
        invalid_engine["inference_engines"]["engines"]["k0_instruct"][
            "model_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(Phase5DashboardError, "engine identity"):
            validate_config(invalid_engine)
        self.assertTrue(DashboardHTTPServer.allow_reuse_address)

    def test_prompt_hash_drift_is_rejected(self) -> None:
        prompt = self.fixture.root / "configs/chat_prompts/saju_intake_handoff_v1.txt"
        prompt.write_text("변조된 prompt\n", encoding="utf-8")
        with self.assertRaisesRegex(Phase5DashboardError, "system prompt 파일 계약"):
            self.fixture.context()

    def test_model_file_hash_drift_is_rejected_before_load(self) -> None:
        context = self.fixture.context()
        k0_path = context["inference_engines"]["engines"]["k0_instruct"][
            "resolved_path"
        ]
        with self.assertRaisesRegex(Phase5DashboardError, "model.safetensors SHA-256"):
            _load_model(k0_path, "0" * 64)
        with self.assertRaisesRegex(Phase5DashboardError, "tokenizer.json SHA-256"):
            _load_model(k0_path, required_file_sha256={"tokenizer.json": "0" * 64})

    def test_context_trimming_preserves_single_leading_system_message(self) -> None:
        class FakeTensor:
            def __init__(self, length: int) -> None:
                self.shape = (1, length)

            def to(self, _device: str) -> FakeTensor:
                return self

        class FakeGenerated:
            def __getitem__(self, _key: object) -> list[int]:
                return [1]

        class FakeTokenizer:
            pad_token_id = 0
            eos_token_id = 1

            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def apply_chat_template(
                self, messages: object, **_kwargs: object
            ) -> FakeTensor:
                copied = [dict(message) for message in messages]
                self.calls.append(copied)
                return FakeTensor(500 if len(copied) > 4 else 100)

            def decode(self, _tokens: object, **_kwargs: object) -> str:
                return "보존 답변"

        class FakeModel:
            def generate(self, _input: object, **_kwargs: object) -> FakeGenerated:
                return FakeGenerated()

        tokenizer = FakeTokenizer()
        result = _generate_loaded(
            object(),
            tokenizer,
            FakeModel(),
            [
                {"role": "system", "content": "고정 지침"},
                {"role": "user", "content": "오래된 질문"},
                {"role": "assistant", "content": "오래된 답변"},
                {"role": "user", "content": "중간 질문"},
                {"role": "assistant", "content": "중간 답변"},
                {"role": "user", "content": "최근 질문"},
            ],
            {"do_sample": False, "num_beams": 1, "max_new_tokens": 32},
            max_input_tokens=200,
        )
        self.assertEqual(result["omitted_messages"], 2)
        self.assertEqual(
            [message["role"] for message in tokenizer.calls[-1]],
            ["system", "user", "assistant", "user"],
        )
        self.assertEqual(
            sum(message["role"] == "system" for message in tokenizer.calls[-1]), 1
        )

    def test_prepare_context_rejects_symlink_and_outside_run(self) -> None:
        context = self.fixture.context()
        self.assertEqual(context["manifest"]["run_build_id"], "run-123456abcdef")
        outside = self.fixture.root / "outside/run-123456abcdef"
        outside.mkdir(parents=True)
        with self.assertRaisesRegex(Phase5DashboardError, "runs/ 아래"):
            prepare_context(
                self.fixture.root, self.fixture.config_path, outside
            )
        link = self.fixture.root / "runs/KI20-MIX-v2/v1.2.0/run-fedcba654321"
        link.symlink_to(self.fixture.run_root, target_is_directory=True)
        with self.assertRaisesRegex(Phase5DashboardError, "symlink"):
            prepare_context(self.fixture.root, self.fixture.config_path, link)

    def test_live_jsonl_ignores_only_incomplete_trailing_line(self) -> None:
        self.fixture.metrics_path.write_bytes(
            b'{"global_step":1,"loss":2.0}\n{"global_step":2'
        )
        rows, ignored = read_live_metrics(self.fixture.metrics_path)
        self.assertEqual([row["global_step"] for row in rows], [1])
        self.assertTrue(ignored)
        self.fixture.metrics_path.write_bytes(
            b'{"global_step":1}\nnot-json\n{"global_step":2}\n'
        )
        with self.assertRaisesRegex(Phase5DashboardError, "2행"):
            read_live_metrics(self.fixture.metrics_path)

    def test_metrics_split_train_eval_and_report_nonfinite(self) -> None:
        self.fixture.metrics_path.write_text(
            '{"global_step":1,"loss":NaN}\n'
            '{"global_step":250,"eval_loss":0.7}\n',
            encoding="utf-8",
        )
        payload = metrics_payload(self.fixture.context())
        self.assertIsNone(payload["train"][0]["loss"])
        self.assertEqual(payload["evaluation"][0]["global_step"], 250)
        self.assertEqual(payload["nonfinite"], [{"global_step": 1, "field": "loss"}])

    def test_checkpoint_saving_and_incomplete_are_distinguished(self) -> None:
        checkpoint = self.fixture.run_root / "checkpoint-250"
        checkpoint.mkdir()
        (checkpoint / "config.json").write_text("{}", encoding="utf-8")
        context = self.fixture.context()
        recent = checkpoints_payload(context, now=checkpoint.stat().st_mtime + 10)
        stable = checkpoints_payload(context, now=checkpoint.stat().st_mtime + 300)
        self.assertEqual(recent["items"][0]["status"], "saving")
        self.assertEqual(stable["items"][0]["status"], "incomplete")

    @patch("scripts.training.phase5_dashboard.subprocess.run")
    def test_systemd_properties_are_parsed_by_name_not_output_order(
        self, run: object
    ) -> None:
        run.return_value = CompletedProcess(
            args=[],
            returncode=0,
            stdout="MainPID=826832\nActiveState=active\nSubState=running\n",
            stderr="",
        )
        result = _service_snapshot("saju-ki20-test.service")
        self.assertTrue(result["active"])
        self.assertEqual(result["main_pid"], 826832)
        self.assertEqual(result["sub_state"], "running")

    @patch("scripts.training.phase5_dashboard._gpu_snapshot")
    @patch("scripts.training.phase5_dashboard._service_snapshot")
    def test_status_is_diagnostic_and_preserves_governance(
        self, service: object, gpu: object
    ) -> None:
        service.return_value = {
            "unit": "saju-ki20-test.service",
            "active": True,
            "active_state": "active",
            "sub_state": "running",
            "main_pid": 123,
        }
        gpu.return_value = {
            "available": True,
            "name": "test GPU",
            "used_mib": 9000,
            "total_mib": 16303,
        }
        payload = status_payload(
            self.fixture.context(),
            now=datetime(2026, 8, 29, 12, 10, tzinfo=timezone.utc),
        )
        self.assertEqual(payload["run"]["lifecycle"], "running")
        self.assertFalse(payload["run"]["production_promotion_allowed"])
        self.assertFalse(payload["run"]["blind_source_test_inspected"])
        self.assertFalse(payload["quality_gate_evaluated"])

    def test_probe_selection_is_fixed_to_twenty_without_blind_data(self) -> None:
        rows = []
        for category, count in self.fixture.config["model_check"][
            "category_counts"
        ].items():
            for index in range(count + 1):
                rows.append(
                    {
                        "eval_id": f"{len(rows) + 1:024x}"[-24:],
                        "case_id": f"case-{index}",
                        "category": category,
                        "source_axis": "synthetic_public",
                        "automated_contract": {"score": "qualitative"},
                        "prompt_messages": [{"role": "user", "content": "질문"}],
                        "output": "답변",
                    }
                )
        source = self.fixture.root / "runs/KI10/source.jsonl"
        source.parent.mkdir(parents=True)
        payload = "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in rows
        )
        source.write_text(payload, encoding="utf-8")
        self.fixture.config["model_check"]["probe_source"] = {
            "path": "runs/KI10/source.jsonl",
            "sha256": __import__("hashlib").sha256(payload.encode()).hexdigest(),
        }
        context = self.fixture.context()
        first = _select_probes(context)
        second = _select_probes(context)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 20)
        self.assertNotIn("blind", json.dumps(first))

    def test_dataset_catalog_matches_current_splits_and_keeps_blind_sealed(self) -> None:
        payload = dataset_splits_payload(self.fixture.context())
        counts = {split["split_id"]: split["rows"] for split in payload["splits"]}
        self.assertEqual(
            counts,
            {
                "ki10_train": 10_000,
                "ki20_train": 20_000,
                "dev_monitor": 70,
                "dev_diagnostic": 930,
                "persona_guard": 50,
                "external_conformance": 220,
            },
        )
        ki20 = next(
            split for split in payload["splits"] if split["split_id"] == "ki20_train"
        )
        self.assertEqual(sum(axis["rows"] for axis in ki20["axes"]), 20_000)
        self.assertFalse(payload["sealed_blind"]["sample_access_allowed"])
        self.assertFalse(payload["blind_source_test_inspected"])
        with self.assertRaisesRegex(DashboardRequestError, "허용되지 않은"):
            dataset_samples_payload(self.fixture.context(), "blind_source_test", "all")

    @patch("scripts.training.phase5_dashboard._dataset_candidates")
    def test_dataset_samples_are_deterministic_minimal_and_mark_restricted(
        self, candidates: object
    ) -> None:
        candidates.return_value = [
            {
                "identity": f"private-record-{index}",
                "axis": "aihub_empathy_single",
                "task": "empathic_response",
                "format": "messages",
                "messages": [
                    {"role": "user", "content": f"질문 {index}"},
                    {"role": "assistant", "content": f"답변 {index}"},
                ],
                "restricted_local_only": True,
            }
            for index in range(5)
        ]
        first = dataset_samples_payload(
            self.fixture.context(), "ki20_train", "aihub_empathy_single", {}
        )
        second = dataset_samples_payload(
            self.fixture.context(), "ki20_train", "aihub_empathy_single", {}
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first["items"]), 3)
        self.assertTrue(first["restricted_content_included"])
        self.assertTrue(first["local_only"])
        self.assertFalse(first["sealed_blind_accessed"])
        encoded = json.dumps(first, ensure_ascii=False)
        self.assertNotIn("private-record", encoded)
        self.assertNotIn("record_sha256", encoded)
        self.assertNotIn("leakage", encoded)

    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_manual_generation_persists_and_reuses_session_context(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.side_effect = [
            {"output": "첫 답변", "input_tokens": 20, "omitted_messages": 0},
            {"output": "후속 답변", "input_tokens": 45, "omitted_messages": 0},
        ]
        result = execute_manual_generation(self.fixture.context(), "질문입니다")
        self.assertEqual(result["output"], "첫 답변")
        self.assertTrue(result["persisted"])
        self.assertTrue(result["local_only"])
        self.assertEqual(
            result["prompt_profile"]["profile_id"], "guided_diagnostic_v1"
        )
        session_id = result["session_id"]
        followup = execute_manual_generation(
            self.fixture.context(), "앞 답변을 요약해줘", session_id
        )
        self.assertEqual(followup["session"]["turn_count"], 2)
        self.assertEqual(
            generate.call_args_list[1].args[1],
            [
                {
                    "role": "system",
                    "content": (
                        REPO_ROOT
                        / "configs/chat_prompts/saju_intake_handoff_v1.txt"
                    )
                    .read_text(encoding="utf-8")
                    .strip(),
                },
                {"role": "user", "content": "질문입니다"},
                {"role": "assistant", "content": "첫 답변"},
                {"role": "user", "content": "앞 답변을 요약해줘"},
            ],
        )
        stored = manual_session_payload(self.fixture.context(), session_id)
        self.assertEqual(stored["messages"][-1]["content"], "후속 답변")
        self.assertEqual(stored["prompt_profile"], "guided_diagnostic_v1")
        self.assertEqual(manual_sessions_payload(self.fixture.context())["items"][0]["turn_count"], 2)
        self.assertFalse(result["production_promotion_allowed"])

    @patch("scripts.training.phase5_dashboard._gpu_snapshot")
    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_paired_generation_keeps_engine_contexts_independent_and_locked(
        self, gate: object, generate: object, gpu: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        gpu.return_value = {"available": True, "used_mib": 1000}
        generate.side_effect = [
            {"output": "K0 첫 답변", "input_tokens": 20, "omitted_messages": 0},
            {"output": "KI20 첫 답변", "input_tokens": 20, "omitted_messages": 0},
            {"output": "K0 후속 답변", "input_tokens": 40, "omitted_messages": 0},
            {"output": "KI20 후속 답변", "input_tokens": 41, "omitted_messages": 0},
        ]
        context = self.fixture.context()
        first = execute_manual_generation(
            context,
            "같은 질문",
            profile="raw_no_system",
            engine_selection="k0_vs_ki20",
        )
        self.assertNotIn("output", first)
        self.assertEqual(
            first["outputs"],
            {"k0_instruct": "K0 첫 답변", "ki20_final": "KI20 첫 답변"},
        )
        self.assertEqual(first["session"]["engine_mode"], "paired")
        self.assertEqual(
            [message.get("engine_id") for message in first["session"]["messages"]],
            [None, "k0_instruct", "ki20_final"],
        )
        self.assertTrue(
            str(generate.call_args_list[0].args[0]).endswith(
                "kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720"
            )
        )
        self.assertTrue(str(generate.call_args_list[1].args[0]).endswith("/final"))
        followup = execute_manual_generation(
            context, "후속 질문", first["session_id"], engine_selection="k0_vs_ki20"
        )
        self.assertEqual(followup["session"]["turn_count"], 2)
        self.assertEqual(
            generate.call_args_list[2].args[1],
            [
                {"role": "user", "content": "같은 질문"},
                {"role": "assistant", "content": "K0 첫 답변"},
                {"role": "user", "content": "후속 질문"},
            ],
        )
        self.assertEqual(
            generate.call_args_list[3].args[1],
            [
                {"role": "user", "content": "같은 질문"},
                {"role": "assistant", "content": "KI20 첫 답변"},
                {"role": "user", "content": "후속 질문"},
            ],
        )
        with self.assertRaisesRegex(Phase5DashboardError, "변경할 수 없습니다"):
            execute_manual_generation(
                context,
                "엔진 변경",
                first["session_id"],
                engine_selection="ki20_final",
            )

    @patch("scripts.training.phase5_dashboard._gpu_snapshot")
    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_paired_failure_does_not_persist_partial_turn(
        self, gate: object, generate: object, gpu: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        gpu.return_value = {"available": True, "used_mib": 1000}
        generate.side_effect = [
            {"output": "K0 임시 답변", "input_tokens": 10, "omitted_messages": 0},
            Phase5DashboardError("KI20 생성 실패"),
        ]
        context = self.fixture.context()
        with self.assertRaisesRegex(Phase5DashboardError, "KI20 생성 실패"):
            execute_manual_generation(
                context,
                "원자 저장 질문",
                engine_selection="k0_vs_ki20",
            )
        self.assertEqual(manual_sessions_payload(context)["items"], [])

    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_k0_single_generation_uses_fixed_snapshot_identity(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.return_value = {
            "output": "K0 답변",
            "input_tokens": 12,
            "omitted_messages": 0,
        }
        result = execute_manual_generation(
            self.fixture.context(), "원본 질문", engine_selection="k0_instruct"
        )
        self.assertEqual(result["output"], "K0 답변")
        self.assertEqual(result["session"]["engine_selection"], "k0_instruct")
        self.assertEqual(
            generate.call_args.args[4],
            self.fixture.config["inference_engines"]["engines"]["k0_instruct"][
                "model_sha256"
            ],
        )
        catalog = manual_sessions_payload(self.fixture.context())["inference_engines"]
        self.assertEqual(len(catalog["selections"]), 3)
        self.assertTrue(all(engine["available"] for engine in catalog["items"]))

    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_raw_profile_is_explicit_and_locked_per_session(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.side_effect = [
            {"output": "원출력", "input_tokens": 10, "omitted_messages": 0},
            {"output": "후속", "input_tokens": 20, "omitted_messages": 0},
        ]
        context = self.fixture.context()
        result = execute_manual_generation(
            context, "원질문", profile="raw_no_system"
        )
        self.assertEqual(
            generate.call_args_list[0].args[1],
            [{"role": "user", "content": "원질문"}],
        )
        self.assertEqual(result["session"]["prompt_profile"], "raw_no_system")
        with self.assertRaisesRegex(Phase5DashboardError, "변경할 수 없습니다"):
            execute_manual_generation(
                context,
                "운영으로 바꿔줘",
                result["session_id"],
                "guided_diagnostic_v1",
            )

    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_legacy_session_continues_without_system_and_migrates_profile(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.return_value = {
            "output": "이어진 답변",
            "input_tokens": 30,
            "omitted_messages": 0,
        }
        context = self.fixture.context()
        session_id = "b" * 24
        root = (
            self.fixture.run_root
            / self.fixture.config["manual_session"]["private_output_relative"]
        )
        self.fixture._write_json(
            root / f"{session_id}.json",
            {
                "schema_version": "1.0.0",
                "session_id": session_id,
                "run_id": "KI20-MIX-v2",
                "run_build_id": "run-123456abcdef",
                "run_sha256": "a" * 64,
                "title": "기존 질문",
                "created_at_utc": "2026-08-29T10:00:00+00:00",
                "updated_at_utc": "2026-08-29T10:00:01+00:00",
                "turn_count": 1,
                "messages": [
                    {
                        "role": "user",
                        "content": "기존 질문",
                        "created_at_utc": "2026-08-29T10:00:00+00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "기존 답변",
                        "created_at_utc": "2026-08-29T10:00:01+00:00",
                    },
                ],
                "quality_gate_evaluated": False,
                "production_promotion_allowed": False,
            },
        )
        result = execute_manual_generation(
            context, "후속 질문", session_id, "raw_legacy"
        )
        self.assertEqual(
            generate.call_args.args[1],
            [
                {"role": "user", "content": "기존 질문"},
                {"role": "assistant", "content": "기존 답변"},
                {"role": "user", "content": "후속 질문"},
            ],
        )
        self.assertEqual(result["session"]["prompt_profile"], "raw_no_system")
        self.assertEqual(
            manual_session_payload(context, session_id)["prompt_profile"],
            "raw_no_system",
        )
        with self.assertRaisesRegex(Phase5DashboardError, "허용되지 않습니다"):
            execute_manual_generation(context, "새 질문", profile="raw_legacy")
        with self.assertRaisesRegex(Phase5DashboardError, "허용되지 않습니다"):
            execute_manual_generation(context, "새 질문", profile="unknown_profile")

    def test_manual_session_rejects_invalid_id_and_symlink(self) -> None:
        with self.assertRaisesRegex(Phase5DashboardError, "session_id"):
            manual_session_payload(self.fixture.context(), "../outside")
        root = (
            self.fixture.run_root
            / self.fixture.config["manual_session"]["private_output_relative"]
        )
        root.mkdir(parents=True)
        target = root / ("a" * 24 + ".json")
        target.symlink_to(self.fixture.run_root / "run_manifest.json")
        with self.assertRaisesRegex(Phase5DashboardError, "예상 밖"):
            manual_sessions_payload(self.fixture.context())

    def test_static_assets_are_self_hosted_and_contain_no_training_controls(self) -> None:
        html = (ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        script = (ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        style = (ASSET_ROOT / "dashboard.css").read_text(encoding="utf-8")
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("stop-training", html + script)
        self.assertNotIn("resume-training", html + script)
        self.assertEqual(html.count("__CSRF_TOKEN__"), 1)
        self.assertIn('data-tab="dataset"', html)
        self.assertIn('id="engine-selection-select"', html)
        self.assertIn("k0_vs_ki20", html + script)
        self.assertIn("assistant-response-grid", style)
        self.assertLess(html.index('id="manual-panel"'), html.index('id="comparison-panel"'))
        self.assertIn('<details id="comparison-panel"', html)
        self.assertNotIn('<details id="comparison-panel" open', html)
        self.assertIn('id="prompt-profile-select"', html)
        self.assertIn("guided_diagnostic_v1", html + script)
        self.assertIn("raw_no_system", html + script)
        self.assertIn("/api/dataset-samples/", script)
        self.assertIn("--bg: #f4efe4", style)
        dashboard_source = (REPO_ROOT / "scripts/training/phase5_dashboard.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("blind_source_test_500.jsonl", dashboard_source)


class Phase5DashboardHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DashboardFixture(Path(self.temporary.name))
        self.context = self.fixture.context()
        self.server = DashboardHTTPServer(
            ("127.0.0.1", 0), self.context, ASSET_ROOT, "a" * 48
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        host: str | None = None,
        token: bool = False,
        origin: bool = False,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-CSRF-Token"] = "a" * 48
        if origin:
            headers["Origin"] = f"http://127.0.0.1:{self.port}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, payload

    def test_security_headers_host_and_csrf(self) -> None:
        status, headers, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertNotIn(b"__CSRF_TOKEN__", payload)
        status, _, _ = self.request(
            "GET", "/", host=f"example.com:{self.port}"
        )
        self.assertEqual(status, 421)
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 403)

    @patch("scripts.training.phase5_dashboard._manual_generation_subprocess")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_generation_api_forwards_explicit_prompt_profile(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.return_value = {
            "status": "generated",
            "persisted": True,
            "local_only": True,
            "session_id": "c" * 24,
        }
        body = json.dumps(
            {
                "prompt": "진단 질문",
                "session_id": None,
                "profile": "guided_diagnostic_v1",
                "engine_selection": "k0_vs_ki20",
            }
        ).encode()
        status, _, _ = self.request(
            "POST",
            "/api/generate",
            token=True,
            origin=True,
            body=body,
        )
        self.assertEqual(status, 200)
        generate.assert_called_once_with(
            self.context,
            "진단 질문",
            None,
            "guided_diagnostic_v1",
            "k0_vs_ki20",
        )

    @patch("scripts.training.phase5_dashboard._generate_conversation")
    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_session_list_and_detail_are_loopback_api(
        self, gate: object, generate: object
    ) -> None:
        gate.return_value = {"allowed": True, "reasons": []}
        generate.return_value = {
            "output": "저장 답변",
            "input_tokens": 18,
            "omitted_messages": 0,
        }
        created = execute_manual_generation(self.context, "저장 질문")
        status, _, payload = self.request(
            "GET", "/api/sessions", token=True
        )
        self.assertEqual(status, 200)
        sessions = json.loads(payload)
        self.assertEqual(sessions["items"][0]["session_id"], created["session_id"])
        status, _, payload = self.request(
            "GET", f"/api/sessions/{created['session_id']}", token=True
        )
        self.assertEqual(status, 200)
        session = json.loads(payload)
        self.assertEqual(session["messages"][0]["content"], "저장 질문")

    @patch("scripts.training.phase5_dashboard._dataset_candidates")
    def test_dataset_api_is_csrf_protected_and_returns_minimal_samples(
        self, candidates: object
    ) -> None:
        status, _, payload = self.request(
            "GET", "/api/dataset-splits", token=True
        )
        self.assertEqual(status, 200)
        catalog = json.loads(payload)
        self.assertEqual(catalog["sealed_blind"]["rows"], 500)
        candidates.return_value = [
            {
                "identity": f"hidden-{index}",
                "axis": "nemotron_saju",
                "task": "structured_saju_reading",
                "format": "messages",
                "messages": [
                    {"role": "user", "content": "질문"},
                    {"role": "assistant", "content": "답변"},
                ],
                "restricted_local_only": False,
            }
            for index in range(3)
        ]
        status, _, payload = self.request(
            "GET",
            "/api/dataset-samples/ki20_train/nemotron_saju",
            token=True,
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)
        self.assertEqual(len(result["items"]), 3)
        self.assertNotIn("hidden-", payload.decode())
        status, _, _ = self.request(
            "GET", "/api/dataset-samples/blind_source_test/all", token=True
        )
        self.assertEqual(status, 404)

    @patch("scripts.training.phase5_dashboard._generation_gate")
    def test_generation_is_fail_closed_while_training(self, gate: object) -> None:
        gate.return_value = {
            "allowed": False,
            "reasons": ["training_service_active"],
        }
        body = json.dumps({"prompt": "질문"}).encode()
        status, _, payload = self.request(
            "POST",
            "/api/generate",
            token=True,
            origin=True,
            body=body,
        )
        self.assertEqual(status, 409)
        self.assertNotIn("질문", payload.decode())
        status, _, _ = self.request(
            "POST",
            "/api/probe",
            token=True,
            origin=True,
            body=b"{}",
        )
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
