# test_system_context_s4_audit.py - S4의 파일 혼입·실패 재개·실행 인자·종료 증거를 음성 회귀로 검증한다.

import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, call, patch

from scripts.evaluation import system_context_s4 as runner
from scripts.evaluation import system_context_s4_models as models
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_contracts import read_json, write_new
from scripts.evaluation.system_context_s4_projection import retokenize
from scripts.training.dashboard_tokenizer_v1 import backend_sha256
from tests import test_system_context_s4 as fixtures
from tests.test_system_context_s4 import fixture, response_fixture


class S4AuditTests(unittest.TestCase):
    def test_k0_snapshot_rejects_unregistered_adapter_before_model_hashing(self):
        for name in ("adapter_config.json", "added_tokens.json", "unexpected.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                repo = Path(directory)
                root = repo / models.K0_RELATIVE
                root.mkdir(parents=True)
                for auxiliary in ("assets", "sglang"):
                    (root / auxiliary).mkdir()
                    (root / auxiliary / "synthetic.txt").write_text("non-loader metadata")
                (root / "README.md").write_text("synthetic metadata")
                (root / name).write_text("{}")
                context = {"inference_engines": {"engines": {"k0_instruct": {
                    "resolved_path": str(root), "required_file_sha256": {}, "model_sha256": "synthetic",
                }}}}
                with patch.object(models, "REPO_ROOT", repo), patch.object(models, "file_sha") as hasher, self.assertRaisesRegex(ValueError, "미등록"):
                    models.verify_models(context)
                hasher.assert_not_called()

    def test_unregistered_loading_files_are_rejected(self):
        for name in ("model.safetensors", "adapter_config.json", "added_tokens.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = fixtures.S4RegistrationTests().synthetic_snapshot(root)
                (root / name).write_bytes(b"unregistered synthetic file")
                with patch.object(models, "model_root", return_value=root), patch.object(models, "registration", return_value=value):
                    with self.assertRaisesRegex(ValueError, "미등록"):
                        models.verify_3b()
                    with self.assertRaisesRegex(ValueError, "미등록"):
                        models.verify_3b(metadata_only=True)

    def test_written_response_after_failed_worker_is_never_reused(self):
        prepared, original = fixture(), fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def failed_after_output(folder, request):
                write_new(folder / f"{request['request_id']}.response.json", response_fixture(request))
                raise ValueError("synthetic failed worker after output")

            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "assert_raw_untracked"), patch.object(runner, "service_observation", return_value={"MainPID": 0}), patch.object(runner, "_start_worker", side_effect=failed_after_output) as worker, patch.object(runner, "publish") as publish, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "failed worker"):
                    runner.execute(prepared)
                worker.reset_mock()
                with self.assertRaisesRegex(ValueError, "정상 종료"):
                    runner.execute(original, resume=True)
                worker.assert_not_called()
                publish.assert_not_called()

    def test_inconsistent_stop_tokens_and_nonfinite_cost_are_rejected(self):
        request = fixture()["requests"][0]
        for key, value in (("output_token_ids", [123]), ("output_token_ids", [True]), ("elapsed_seconds", float("nan")), ("elapsed_seconds", float("inf")), ("stop_reason", "max_tokens")):
            with self.subTest(key=key, value=value):
                response = response_fixture(request)
                response["telemetry"][key] = value
                with self.assertRaises(ValueError):
                    runner.validate_response(request, response)

    def test_invalid_cli_combinations_fail_before_prepare_or_side_effects(self):
        commands = (
            ["execute", "--execute"],
            ["execute", "--resume"],
            ["download", "--execute", "--resume"],
            ["plan", "--execute"],
            ["verify"],
            ["execute", "--input", "/synthetic/input.json"],
        )
        for args in commands:
            with self.subTest(args=args), patch.object(runner, "prepare", return_value=fixture()) as prepare, patch.object(runner, "execute", return_value={}) as execute, patch.object(runner, "download", return_value={}) as download, patch.object(runner, "verify", return_value={}) as verify, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(runner.main(args), 1)
                prepare.assert_not_called()
                execute.assert_not_called()
                download.assert_not_called()
                verify.assert_not_called()

    def test_failure_exit_receipt_is_persisted_before_error_and_blocks_reuse(self):
        request = fixture()["requests"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fd = os.open(root / "synthetic-gpu.lock", os.O_CREAT | os.O_RDWR, 0o600)
            process = Mock(pid=999999, wait=Mock(return_value=7), poll=Mock(return_value=7))

            def spawn(*args, **kwargs):
                write_new(root / "request-001.response.json", response_fixture(request))
                return process

            with patch("scripts.evaluation.dashboard_v115_replay.header_check"), patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock", return_value=fd), patch.object(runner, "gpu_ready", return_value=True), patch.object(runner.subprocess, "Popen", side_effect=spawn), self.assertRaisesRegex(ValueError, "worker 실패"):
                runner._start_worker(root, request)
            receipt = read_json(root / "request-001.exit.json", private=True)
            self.assertEqual(receipt["return_code"], 7)
            with self.assertRaisesRegex(ValueError, "정상 종료"):
                runner.completion_hashes(root, request, response_fixture(request))
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_completion_receipt_rejects_mutation_or_missing_private_evidence(self):
        request = fixture()["requests"][0]
        for mutation in ("exit", "start", "log", "missing", "mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                response = fixtures.completed_response_fixture(root, request)
                self.assertEqual(set(runner.completion_hashes(root, request, response)), {"started_sha256", "exit_sha256", "worker_log_sha256"})
                if mutation == "missing":
                    (root / "request-001.exit.json").unlink()
                elif mutation == "mode":
                    (root / "request-001.worker.log").chmod(0o644)
                else:
                    suffix = {"exit": "exit.json", "start": "started.json", "log": "worker.log"}[mutation]
                    path = root / f"request-001.{suffix}"
                    value = read_json(path)
                    value["mutation"] = True
                    path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.completion_hashes(root, request, response)

    def test_cancellation_escalates_and_waits_for_own_worker(self):
        process = Mock(pid=999999, poll=Mock(return_value=None))
        process.wait.side_effect = [subprocess.TimeoutExpired("synthetic", 5), -9]
        with patch.object(runner.os, "killpg") as kill:
            runner.stop_worker(process)
        self.assertEqual(kill.call_args_list, [call(999999, signal.SIGTERM), call(999999, signal.SIGKILL)])
        self.assertEqual(process.wait.call_args_list, [call(timeout=5), call(timeout=5)])

    def test_timeout_with_late_success_output_is_not_normal_completion(self):
        request = fixture()["requests"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fd = os.open(root / "synthetic-gpu.lock", os.O_CREAT | os.O_RDWR, 0o600)
            process = Mock(pid=999999, returncode=0, poll=Mock(return_value=0))
            process.wait.side_effect = subprocess.TimeoutExpired("synthetic", 300)

            def spawn(*args, **kwargs):
                write_new(root / "request-001.response.json", response_fixture(request))
                return process

            with patch("scripts.evaluation.dashboard_v115_replay.header_check"), patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock", return_value=fd), patch.object(runner, "gpu_ready", return_value=True), patch.object(runner, "stop_worker"), patch.object(runner.subprocess, "Popen", side_effect=spawn), self.assertRaisesRegex(ValueError, "worker 실패"):
                runner._start_worker(root, request)
            receipt = read_json(root / "request-001.exit.json", private=True)
            self.assertEqual(receipt["return_code"], 0)
            self.assertTrue(receipt["timed_out"])
            with self.assertRaisesRegex(ValueError, "정상 종료"):
                runner.completion_hashes(root, request, response_fixture(request))

    def test_partial_worker_traces_cannot_trigger_another_generation(self):
        for suffix in ("exit.json", "worker.log"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                write_new(root / f"request-001.{suffix}", {"synthetic": True})
                with patch.object(runner.subprocess, "Popen") as process, self.assertRaisesRegex(ValueError, "재생성"):
                    runner._start_worker(root, {"request_id": "request-001"})
                process.assert_not_called()

    def test_lost_ledger_cannot_reset_existing_build_budget(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build-aaaaaaaaaaaa").mkdir(mode=0o700)
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "assert_raw_untracked"), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "예산 원장"):
                runner.execute(prepared)
            worker.assert_not_called()
            self.assertFalse((root / "active-build.json").exists())
            self.assertFalse((root / prepared["build_id"]).exists())

    def test_invalid_resume_does_not_create_an_orphan_build(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), self.assertRaisesRegex(ValueError, "동결 manifest"):
                runner.execute(prepared, resume=True)
            self.assertFalse(root.exists())

    def test_raw_git_tracking_blocks_before_artifact_creation(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "REPO_ROOT", root), patch.object(runner, "RAW_ROOT", raw), patch.object(runner, "build_path", return_value=raw / prepared["build_id"]), patch.object(runner.subprocess, "run", return_value=Mock(stdout="raw/response.json\n")), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "Git 추적"):
                runner.execute(prepared)
            worker.assert_not_called()
            self.assertFalse(raw.exists())

    def test_service_change_stops_before_consuming_model_request(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "assert_raw_untracked"), patch.object(runner, "service_observation", side_effect=[{"MainPID": 1}, {"MainPID": 2}]), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "운영 서비스"):
                runner.execute(prepared)
            worker.assert_not_called()

    def test_resume_accounting_separates_generation_from_preblocks(self):
        entries = [{"request_id": str(i), "status": status} for i, status in enumerate(("generated", "generated", "preblocked", "preblocked"))]
        self.assertEqual(runner.publication_counts(entries, ["0", "2"]), {"new_generations": 1, "reused_generations": 1, "new_preblocks": 1, "reused_preblocks": 1})
        for ids in (["0", "0"], ["unknown"]):
            with self.assertRaises(ValueError):
                runner.publication_counts(entries, ids)

    def test_partial_resume_only_generates_unstarted_requests(self):
        prepared, original = fixture(), fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempted = 0

            def generate(folder, request):
                nonlocal attempted
                attempted += 1
                if attempted == 3:
                    raise KeyboardInterrupt
                return fixtures.completed_response_fixture(folder, request)

            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "assert_raw_untracked"), patch.object(runner, "service_observation", return_value={"MainPID": 0}), patch.object(runner, "_start_worker", side_effect=generate) as worker, patch.object(runner, "publish") as publish, patch.object(runner, "verify", return_value={"status": "synthetic_verified"}), redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    runner.execute(prepared)
                worker.reset_mock()
                runner.execute(original, resume=True)
                self.assertEqual(worker.call_count, 170)
                self.assertEqual(publish.call_args.kwargs["reused"], 2)
                counts = runner.publication_counts(publish.call_args.args[1], publish.call_args.kwargs["reused_request_ids"])
                self.assertEqual(counts, {"new_generations": 170, "reused_generations": 2, "new_preblocks": 20, "reused_preblocks": 0})

    def test_hub_cache_is_allowed_but_symlinks_are_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = fixtures.S4RegistrationTests().synthetic_snapshot(root)
            cache = root / ".cache/huggingface"
            cache.mkdir(parents=True)
            (cache / "download.metadata").write_text("synthetic cache")
            with patch.object(models, "model_root", return_value=root), patch.object(models, "registration", return_value=value):
                self.assertEqual(set(models.verify_3b()), set(value["files"]))
                (cache / "unsafe").symlink_to(root / "LICENSE")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    models.verify_3b(metadata_only=True)

    def test_retokenization_keeps_fact_and_instruction_positions(self):
        data = '{"example":"합성"}'
        sections = {"runtime": "계산 안내\n" + data, "selected_data": data}
        original = {
            "messages": [{"role": "system", "content": "공통 안내\n" + sections["runtime"]}, {"role": "user", "content": "질문"}],
            "selected_paths": ["example"], "segments": [{"segment": name} for name in ("p0", "runtime", "selected_data")],
        }
        before = deepcopy(original)
        tokenizer = fixtures.FakeTokenizer(10)
        kwargs = {"engine": "kanana3b_instruct", "expected_backend": backend_sha256(tokenizer)}
        with self.assertRaisesRegex(ValueError, "생략"):
            retokenize(original, tokenizer, **kwargs)
        result = retokenize(original, tokenizer, sections=sections, **kwargs)
        rendered = tokenizer.apply_chat_template(original["messages"], tokenize=False, add_generation_prompt=True)
        for name in ("runtime", "selected_data"):
            segment = next(s for s in result["segments"] if s["segment"] == name)
            self.assertEqual(rendered[segment["chars_start"]:segment["chars_end"]], sections[name])
            self.assertEqual(segment["token_end_exclusive"] - segment["token_start"], len(sections[name]))
        self.assertEqual(original, before)
        with self.assertRaisesRegex(ValueError, "직렬화"):
            retokenize(original, tokenizer, sections={**sections, "selected_data": "다른 값"}, **kwargs)

    def test_prepare_constructs_all_192_requests_with_independent_tokenizers(self):
        config = runner.validate_contract()
        tokenizers = {e: fixtures.FakeTokenizer(i) for i, e in enumerate(models.ENGINES)}
        registry = {e: {"tokenizer_backend_sha256": backend_sha256(t)} for e, t in tokenizers.items()}
        parent_rows = []
        for spec in runner.cases():
            case = {**spec, "binding": None, "history": []}
            for arm in ("C_FULL", "C_MIN"):
                rendered = retokenize({"messages": [{"role": "system", "content": "공통 P0"}, {"role": "user", "content": spec["case_id"] + arm}], "selected_paths": [], "excluded_paths": [], "p0_sha256": digest("공통 P0"), "projection_schema": arm}, tokenizers["k0_instruct"], engine="k0_instruct", expected_backend=registry["k0_instruct"]["tokenizer_backend_sha256"])
                parent_rows.append({"case_id": spec["case_id"], "case": case, "stage": "primary", "engine": "k0_instruct", "arm": arm, "render": rendered, "parent_sha256": digest([])})
        parent = {"requests": parent_rows, "identity": {"generation": {"do_sample": False}}, "training_inventory": {}}
        context = {"config": {"model_check": {"generation": parent["identity"]["generation"]}}}

        def rebuild(case, arm, context, tokenizer):
            return deepcopy(next(r["render"] for r in parent_rows if r["case_id"] == case["case_id"] and r["arm"] == arm))

        # 여기서는 서로 다른 합성 tokenizer의 projection만 검사한다.
        # 실제 저장 호환성은 test_system_context_s4_consumers가 모의 없이 검사한다.
        with patch.object(runner.rescore, "validate_contract", return_value={}), patch.object(runner.rescore, "verify_parent", return_value={"frozen": parent}), patch.object(runner, "prepare_context", return_value=context), patch.object(runner, "verify_models", return_value={}), patch.object(runner, "registry_summary", return_value=registry), patch.object(runner, "load_tokenizer", side_effect=tokenizers.__getitem__), patch.object(runner, "render", side_effect=rebuild), patch.object(runner, "code_fingerprint", return_value={"synthetic": "hash"}), patch.object(runner, "environment_identity", return_value={}), patch.object(runner, "preflight", return_value=fixture()["consumer_preflight"]) as preflight:
            prepared = runner.prepare()
        self.assertEqual(preflight.call_count, 1)
        self.assertEqual(len(preflight.call_args.args[1]), 192)
        runner.validate_prepared(prepared)
        self.assertEqual(prepared["config"], config)
        self.assertEqual(len(prepared["requests"]), 192)
        self.assertEqual(prepared["expected_blocks"], 20)
        self.assertEqual(len(prepared["input_comparisons"]), 96)
        self.assertTrue(all(not r["token_ids_equal_observed"] and r["messages_equal"] for r in prepared["input_comparisons"]))


if __name__ == "__main__":
    unittest.main()
