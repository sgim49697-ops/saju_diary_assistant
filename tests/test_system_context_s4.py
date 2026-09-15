# test_system_context_s4.py - S4 모델 등록·2×2 입력·CPU 실행·재개·예산·공개 집계 안전성을 검증한다.

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.evaluation import system_context_s4 as runner
from scripts.evaluation import system_context_s4_backend as backend
from scripts.evaluation import system_context_s4_contracts as contracts
from scripts.evaluation import system_context_s4_models as models
from scripts.evaluation.system_context_cases import cases, digest
from scripts.evaluation.system_context_contracts import (
    read_json,
    validate_public,
    write_new,
)
from scripts.evaluation.system_context_s4_projection import compare_inputs, retokenize
from scripts.evaluation.system_context_s4_scoring import aggregate


def fixture():
    requests = []
    for spec in cases():
        for arm in ("C_FULL", "C_MIN"):
            for engine in models.ENGINES:
                requests.append({
                    "request_id": f"request-{len(requests) + 1:03d}", "case_id": spec["case_id"],
                    "stage": "primary", "engine": engine, "arm": arm,
                    "case": {"history": [], "expected_block": spec["expected_block"], "stratum": spec["stratum"], "task": "general", "expected": {}, "apology_once": False},
                    "render": {"messages": [{"role": "user", "content": "합성 테스트입니다."}], "input_tokens": 10, "input_token_ids_sha256": "a" * 64, "tokenizer_backend_sha256": "b" * 64},
                    "parent_sha256": digest([]),
                })
    identity = {"input_sha256": digest(requests)}
    return {"build_id": "build-" + digest(identity)[:12], "identity": identity,
            "config": contracts.validate_contract(), "requests": requests, "expected_blocks": 20,
            "maximum_input_tokens": 10, "training_inventory": {}}


def response_fixture(request):
    output = "차근차근 이야기해 주세요. 함께 정리하겠습니다."
    return {
        "request_sha256": digest(request), "status": "generated", "output": output,
        "generated": {"input_token_ids_sha256": "a" * 64, "tokenizer_backend_sha256": "b" * 64, "omitted_messages": 0, "output": output},
        "consumers": {
            **{k: digest(output) for k in ("raw_sha256", "api_display_sha256", "stored_sha256")},
            "consumer_path": "v1.15_k0_slot_cpu_replay_not_3b_app_integration", "model_engine": request["engine"],
            "storage_engine_slot": "k0_instruct", "browser_executed": False,
        },
        "telemetry": {"elapsed_seconds": 1, "stop_reason": "eos", "output_token_ids": [1], "output_tokens": 1,
                      "retry_count": 0, "seed": 20260915, "precision": "bfloat16", "native_context_tokens": 32768,
                      "peak_allocated_bytes": 1024, "peak_reserved_bytes": 2048, "gpu_total_memory_used_mib": 1024,
                      "cache_scope": "fresh_generate_no_past_key_values", "engine": request["engine"],
                      "adapter_used": False, "cpu_offload": False, "effective_backend_sha256": "b" * 64,
                      "generation_defaults_policy": "fresh_common_GenerationConfig_explicit_kwargs",
                      "actual_generation_kwargs": backend.generation_kwargs()},
    }


class FakeTokenizer:
    def __init__(self, shift=0):
        self.shift = shift
        self.backend_tokenizer = Mock(to_str=lambda: f"fake-backend-{shift}")

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        text = "\n".join(m["content"] for m in messages)
        return [ord(c) + self.shift for c in text] if tokenize else text

    def __call__(self, text, **kwargs):
        return {"input_ids": [ord(c) + self.shift for c in text], "offset_mapping": [(i, i + 1) for i in range(len(text))]}


class S4ContractTests(unittest.TestCase):
    def test_2x2_schedule_budget_and_unchanged_permissions(self):
        config = contracts.validate_contract()
        scheduled = runner.schedule(config)
        self.assertEqual(len(scheduled), 192)
        self.assertEqual(len({(r["engine"], r["case_id"], r["arm"]) for r in scheduled}), 192)
        self.assertEqual(config["maximum_generations"], 172)
        self.assertEqual(config["preflight_requests"], 0)
        self.assertEqual(config["instruction_arm"], "P0")
        self.assertFalse(config["governance"]["application_model_registry_changed"])

    def test_contract_rejects_budget_model_prompt_scorer_or_unknown_field(self):
        config = contracts.validate_contract()
        for key, value in (("maximum_requests", 194), ("preflight_requests", False), ("instruction_arm", "P1"), ("engines", ["lora_r16", "kanana3b_instruct"]), ("scoring", "old"), ("extra", True)):
            with patch.object(contracts.parent, "read_json", return_value={**config, key: value}), self.assertRaises(ValueError):
                contracts.validate_contract()

    def test_longest_per_model_comes_from_main_budget_and_not_blocked(self):
        eligible = next(c for c in cases() if not c["expected_block"])
        blocked = next(c for c in cases() if c["expected_block"])
        rendered = {(c["case_id"], a, e): {"input_tokens": 10} for c in cases() for a in ("C_FULL", "C_MIN") for e in models.ENGINES}
        for engine in models.ENGINES:
            rendered[(eligible["case_id"], "C_MIN", engine)]["input_tokens"] = 100
            rendered[(blocked["case_id"], "C_FULL", engine)]["input_tokens"] = 1000
        result = runner.schedule(contracts.validate_contract(), rendered)
        self.assertEqual([(r["case_id"], r["arm"]) for r in result[:2]], [(eligible["case_id"], "C_MIN")] * 2)
        self.assertEqual({r["engine"] for r in result[:2]}, set(models.ENGINES))
        self.assertEqual(len(result), 192)

    def test_plan_dry_run_download_default_do_not_generate(self):
        with patch.object(runner, "prepare", return_value=fixture()), patch.object(runner, "execute") as execute, patch.object(models, "private_directory") as mkdir, redirect_stdout(io.StringIO()):
            for args in (["validate-contract"], ["plan"], ["execute"], ["download"]):
                self.assertEqual(runner.main(args), 0)
        execute.assert_not_called()
        mkdir.assert_not_called()

    def test_missing_model_is_a_block_not_success_or_download(self):
        with patch.object(runner, "prepare", side_effect=ValueError("등록 모델 없음")), patch.object(runner, "download") as download, redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["execute"]), 1)
        download.assert_not_called()

    def test_download_and_gpu_require_distinct_explicit_flags(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(models, "private_directory") as mkdir, self.assertRaises(ValueError):
            models.download(execute=True)
        mkdir.assert_not_called()
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, "private_directory") as mkdir, self.assertRaises(ValueError):
            runner.execute({})
        mkdir.assert_not_called()

    def test_frozen_identity_rejects_case_history_and_cross_model_message_tamper(self):
        runner.validate_prepared(fixture())
        for mutate in (lambda p: p["requests"].pop(), lambda p: p["requests"][1]["case"].update(history=["changed"]), lambda p: p["requests"][1]["render"].update(messages=[])):
            value = fixture()
            mutate(value)
            value["identity"]["input_sha256"] = digest(value["requests"])
            value["build_id"] = "build-" + digest(value["identity"])[:12]
            with self.assertRaises(ValueError):
                runner.validate_prepared(value)

    def test_other_build_cannot_reset_192_request_ledger(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_new(root / "active-build.json", {"build_id": "different", "maximum_requests": 192})
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "예산"):
                runner.execute(prepared)
            worker.assert_not_called()

    def test_complete_cpu_execution_and_resume_do_not_retry(self):
        prepared, original = fixture(), fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def generate(folder, request):
                response = response_fixture(request)
                write_new(folder / f"{request['request_id']}.response.json", response)
                return response

            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "service_observation", return_value={"MainPID": 0}), patch.object(runner, "_start_worker", side_effect=generate) as worker, patch.object(runner, "publish") as publish, patch.object(runner, "verify", return_value={"status": "synthetic_verified"}), redirect_stdout(io.StringIO()):
                runner.execute(prepared)
                self.assertEqual(worker.call_count, 172)
                worker.reset_mock()
                runner.execute(original, resume=True)
                worker.assert_not_called()
                self.assertEqual(publish.call_args.kwargs["reused"], 192)
                path = root / prepared["build_id"] / "request-001.response.json"
                response = read_json(path, private=True)
                response["request_sha256"] = "f" * 64
                path.write_text(json.dumps(response))
                with self.assertRaises(ValueError):
                    runner.execute(original, resume=True)
                worker.assert_not_called()

    def test_started_request_does_not_call_worker_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_new(root / "request-001.started.json", {"synthetic": True})
            with patch.object(runner.subprocess, "Popen") as process, self.assertRaisesRegex(ValueError, "재생성"):
                runner._start_worker(root, {"request_id": "request-001"})
            process.assert_not_called()

    def test_failed_worker_stops_experiment_and_is_not_retried_on_resume(self):
        prepared, original = fixture(), fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fail(folder, request):
                response = {"request_sha256": digest(request), "status": "error", "reason": "worker_failed"}
                write_new(folder / f"{request['request_id']}.response.json", response)
                return response

            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "service_observation", return_value={"MainPID": 0}), patch.object(runner, "_start_worker", side_effect=fail) as worker, patch.object(runner, "publish") as publish, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "실행 중단"):
                    runner.execute(prepared)
                self.assertEqual(worker.call_count, 1)
                worker.reset_mock()
                with self.assertRaisesRegex(ValueError, "실행 중단"):
                    runner.execute(original, resume=True)
                worker.assert_not_called()
                publish.assert_not_called()

    def test_cpu_publication_recomputes_manifest_and_rejects_tampering(self):
        prepared = fixture()
        prepared.update(input_comparisons=[], model_registry={"synthetic": True})
        rebuilt = {**prepared}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, public = root / "raw", root / "public"

            def paths(build, *, public=False):
                return (root / ("public" if public else "raw")) / build

            def generate(folder, request):
                response = response_fixture(request)
                write_new(folder / f"{request['request_id']}.response.json", response)
                return response

            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}), patch.object(runner, "REPO_ROOT", root), patch.object(runner, "RAW_ROOT", raw), patch.object(runner, "PUBLIC_ROOT", public), patch.object(runner, "build_path", side_effect=paths), patch.object(runner, "service_observation", return_value={"MainPID": 0}), patch.object(runner, "_start_worker", side_effect=generate), patch.object(runner, "prepare", return_value=rebuilt), patch.object(runner.subprocess, "run", return_value=Mock(stdout="")), redirect_stdout(io.StringIO()):
                result = runner.execute(prepared)
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["requests"], 192)
                folder = public / prepared["build_id"]
                self.assertEqual({p.name for p in folder.iterdir()}, {"aggregate.json", "build_manifest.json", "verification.json"})
                manifest_path = folder / "build_manifest.json"
                original = manifest_path.read_bytes()
                manifest = read_json(manifest_path)
                manifest["instruction_arm"] = "P1"
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "전체 필드"):
                    runner.verify(prepared["build_id"])
                manifest_path.write_bytes(original)
                input_path = raw / prepared["build_id"] / "request-001.input.json"
                request = read_json(input_path, private=True)
                request["case"]["history"] = ["tampered"]
                input_path.write_text(json.dumps(request))
                with self.assertRaisesRegex(ValueError, "파일 변조"):
                    runner.verify(prepared["build_id"])

    def test_generated_response_rejects_loader_decoding_and_consumer_forgery(self):
        request = fixture()["requests"][0]
        runner.validate_response(request, response_fixture(request))
        for section, key, value in (("telemetry", "engine", "other"), ("telemetry", "adapter_used", True), ("telemetry", "cpu_offload", True), ("telemetry", "effective_backend_sha256", "wrong"), ("telemetry", "actual_generation_kwargs", {"do_sample": False, "max_new_tokens": 4096}), ("consumers", "browser_executed", True), ("generated", "omitted_messages", 2)):
            response = response_fixture(request)
            response[section][key] = value
            with self.assertRaises(ValueError):
                runner.validate_response(request, response)

    def test_gpu_must_be_idle_and_have_12gib_free(self):
        with patch("scripts.training.phase5_dashboard_v1_15._gpu_snapshot", return_value={"available": True, "total_mib": 16384, "used_mib": 1000}), patch.object(runner.subprocess, "run", return_value=Mock(stdout="123\n")):
            self.assertFalse(runner.gpu_ready())
        with patch("scripts.training.phase5_dashboard_v1_15._gpu_snapshot", return_value={"available": True, "total_mib": 16384, "used_mib": 5000}), patch.object(runner.subprocess, "run", return_value=Mock(stdout="")):
            self.assertFalse(runner.gpu_ready())

    def test_worker_cannot_start_without_inherited_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4": "K0_KANANA3B_P0_V1"}, clear=True), patch.object(backend, "RAW_ROOT", root), patch.object(backend, "header_check"), patch.object(backend, "load_model") as loader, self.assertRaises(ValueError):
                backend.worker(root / "request-001.input.json", root / "request-001.response.json")
            loader.assert_not_called()


class S4ProjectionTests(unittest.TestCase):
    def test_own_tokenizers_can_produce_different_ids_without_changing_messages(self):
        original = {"messages": [{"role": "system", "content": "같은 안내"}, {"role": "user", "content": "같은 질문"}], "selected_paths": [], "excluded_paths": [], "p0_sha256": "a", "projection_schema": "app-original"}
        pair = []
        for i, engine in enumerate(models.ENGINES):
            tokenizer = FakeTokenizer(i)
            expected = hashlib.sha256(tokenizer.backend_tokenizer.to_str().encode()).hexdigest()
            pair.append(retokenize(original, tokenizer, engine=engine, expected_backend=expected))
        observed = compare_inputs(*pair, case_id="synthetic", arm="C_FULL")
        self.assertFalse(observed["token_ids_equal_observed"])
        self.assertFalse(observed["token_ids_equal_required"])
        self.assertTrue(observed["messages_equal"])
        self.assertEqual(pair[0]["messages"], original["messages"])
        self.assertEqual(pair[1]["omitted_messages"], 0)

    def test_backend_mismatch_or_message_intervention_is_rejected(self):
        tokenizer = FakeTokenizer()
        original = {"messages": [{"role": "user", "content": "합성"}], "selected_paths": []}
        with self.assertRaises(ValueError):
            retokenize(original, tokenizer, engine="k0_instruct", expected_backend="wrong")
        with self.assertRaises(ValueError):
            compare_inputs(original, {**original, "messages": []}, case_id="synthetic", arm="C_MIN")


class S4RegistrationTests(unittest.TestCase):
    def synthetic_snapshot(self, root, *, config_change=None, index_change=None):
        value = models.registration()
        config = {"architectures": [value["architecture"]], "model_type": value["model_type"], "max_position_embeddings": value["native_context_tokens"], "layer_types": ["full_attention"] * 32, "use_sliding_window": False}
        config.update(config_change or {})
        index = {"metadata": {"total_size": value["parameter_count"] * 2}, "weight_map": {"a": "one.safetensors", "b": "two.safetensors"}}
        index.update(index_change or {})
        contents = {"config.json": json.dumps(config).encode(), "model.safetensors.index.json": json.dumps(index).encode(), "LICENSE": b"synthetic fixture", "one.safetensors": b"one", "two.safetensors": b"two"}
        for name, payload in contents.items():
            (root / name).write_bytes(payload)
        value["files"] = {name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()} for name, payload in contents.items()}
        return value

    def test_registry_pins_revision_shards_license_and_native_loader(self):
        value = models.registration()
        self.assertEqual(value["revision"], "6a5d7889964c4c590299d16e309eabab1f73f8a9")
        self.assertEqual(value["architecture"], "Qwen3ForCausalLM")
        self.assertFalse(value["trust_remote_code"])
        self.assertEqual(len([n for n in value["files"] if n.endswith(".safetensors")]), 2)
        self.assertIn("LICENSE", value["files"])
        self.assertEqual(value["parameter_count"], 3508972032)
        validate_public(value)

    def test_changed_registry_is_not_auto_accepted(self):
        with patch.object(models, "file_sha", return_value="0" * 64), self.assertRaises(ValueError):
            models.registration()

    def test_missing_or_symlink_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(models, "model_root", return_value=root), self.assertRaises(ValueError):
                models.verify_3b()
            (root / "LICENSE").symlink_to(root / "absent")
            with patch.object(models, "model_root", return_value=root), self.assertRaises(ValueError):
                models.verify_3b(metadata_only=True)

    def test_download_never_overwrites_wrong_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("invalid existing file")
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4_DOWNLOAD": "KANANA3B_PINNED_V1"}), patch.object(models, "model_root", return_value=root), self.assertRaisesRegex(ValueError, "덮어쓰기"):
                models.download(execute=True)
            self.assertEqual((root / "LICENSE").read_text(), "invalid existing file")

    def test_snapshot_checks_architecture_precision_shards_and_remote_code(self):
        changes = (
            ({}, {}), ({"auto_map": {"AutoModel": "remote.Custom"}}, {}),
            ({"quantization_config": {"bits": 4}}, {}), ({"use_sliding_window": True}, {}),
            ({"max_position_embeddings": 4096}, {}), ({"layer_types": ["full_attention"] * 31}, {}),
            ({}, {"weight_map": {"a": "../outside.safetensors"}}),
            ({}, {"metadata": {"total_size": 10}}),
        )
        for i, (config, index) in enumerate(changes):
            with self.subTest(config=config, index=index), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                value = self.synthetic_snapshot(root, config_change=config, index_change=index)
                with patch.object(models, "model_root", return_value=root), patch.object(models, "registration", return_value=value):
                    if i == 0:
                        self.assertEqual(set(models.verify_3b()), set(value["files"]))
                        (root / "one.safetensors").unlink()
                        self.assertNotIn("one.safetensors", models.verify_3b(metadata_only=True))
                        with self.assertRaises(ValueError):
                            models.verify_3b()
                    else:
                        with self.assertRaises(ValueError):
                            models.verify_3b()

    def test_3b_loader_uses_own_snapshot_and_native_no_offload_path(self):
        model = type("Qwen3ForCausalLM", (), {})()
        model.to, model.eval = Mock(return_value=model), Mock()
        model.generation_config = "directory-specific defaults"
        loader = Mock(return_value=model)
        common_defaults = object()
        transformers = SimpleNamespace(GenerationConfig=Mock(return_value=common_defaults), AutoModelForCausalLM=SimpleNamespace(from_pretrained=loader))
        torch = SimpleNamespace(bfloat16="mock-bfloat16", cuda=SimpleNamespace(init=Mock(), reset_peak_memory_stats=Mock()))
        tokenizer = object()
        with patch.dict(sys.modules, {"transformers": transformers, "torch": torch}), patch.object(backend, "load_tokenizer", return_value=tokenizer) as load_tokenizer, patch.object(backend, "model_root", return_value=Path("/synthetic/pinned-3b")), patch.object(backend.dashboard, "_load_engine_model") as legacy:
            actual_torch, actual_tokenizer, actual_model = backend.load_model({}, "kanana3b_instruct")
        self.assertIs(actual_torch, torch)
        self.assertIs(actual_tokenizer, tokenizer)
        self.assertIs(actual_model, model)
        self.assertIs(model.generation_config, common_defaults)
        load_tokenizer.assert_called_once_with("kanana3b_instruct")
        loader.assert_called_once_with(Path("/synthetic/pinned-3b"), local_files_only=True, trust_remote_code=False, dtype="mock-bfloat16", attn_implementation="sdpa", low_cpu_mem_usage=True)
        model.to.assert_called_once_with("cuda:0")
        model.eval.assert_called_once_with()
        legacy.assert_not_called()

    def test_download_checks_pinned_local_result_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = self.synthetic_snapshot(root)
            (root / "one.safetensors").unlink()

            def fetch(**kwargs):
                self.assertEqual(kwargs["repo_id"], value["model_id"])
                self.assertEqual(kwargs["revision"], value["revision"])
                self.assertEqual(kwargs["filename"], "one.safetensors")
                self.assertFalse(kwargs["token"])
                self.assertFalse(kwargs["force_download"])
                destination = kwargs["local_dir"] / kwargs["filename"]
                destination.write_bytes(b"one")
                return destination

            hub = SimpleNamespace(hf_hub_download=Mock(side_effect=fetch))
            utils = SimpleNamespace(disable_progress_bars=Mock())
            request_errors = SimpleNamespace(RequestException=type("SyntheticRequestError", (Exception,), {}))
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S4_DOWNLOAD": "KANANA3B_PINNED_V1"}), patch.dict(sys.modules, {"huggingface_hub": hub, "huggingface_hub.utils": utils, "requests.exceptions": request_errors}), patch.object(models, "model_root", return_value=root), patch.object(models, "registration", return_value=value):
                result = models.download(execute=True)
                self.assertEqual(result["status"], "download_verified")
                self.assertEqual(set(result["files"]), set(value["files"]))
                hub.hf_hub_download.assert_called_once()


class S4ScoringTests(unittest.TestCase):
    def test_pair_denominators_and_interaction_do_not_turn_unscorable_into_failure(self):
        entries = []
        for case_id, verdicts in (("one", ("FAIL", "PASS", "PASS", "PASS")), ("two", ("UNSCORABLE", "PASS", "FAIL", "PASS"))):
            for (engine, arm), verdict in zip((("k0_instruct", "C_FULL"), ("kanana3b_instruct", "C_FULL"), ("k0_instruct", "C_MIN"), ("kanana3b_instruct", "C_MIN")), verdicts, strict=True):
                entries.append({"case_id": case_id, "engine": engine, "arm": arm, "stage": "primary", "stratum": "facts", "parent_sha256": "parent", "input_tokens": 10, "status": "generated", "scoring": {"metrics": {"required_fact_use": verdict}}, "telemetry": {"elapsed_seconds": 1, "peak_allocated_bytes": 1024, "peak_reserved_bytes": 2048, "output_tokens": 3, "stop_reason": "eos"}})
        result = aggregate(entries)
        row = next(r for r in result["interaction_common_scorable_quads"] if r["stratum"] == "all")
        self.assertEqual(row["common_scorable_cases"], 1)
        self.assertEqual(row["with_unscorable_cases"], 1)
        self.assertEqual(row["full_minus_minimum_model_gain"], 1)
        self.assertFalse(result["candidate_selected"])
        self.assertEqual(result["quality_dimensions"]["semantics"], "not_measured")
        validate_public(result)
        with self.assertRaises(ValueError):
            aggregate(entries[:-1])
        with self.assertRaises(ValueError):
            aggregate([*entries, entries[0]])


if __name__ == "__main__":
    unittest.main()
