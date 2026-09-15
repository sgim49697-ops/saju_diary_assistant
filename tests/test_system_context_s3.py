# test_system_context_s3.py - S3 동결·지시문 단독 변경·예산·재개·CPU 재집계를 검증한다.

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.evaluation import system_context_rescore as rescore
from scripts.evaluation import system_context_s3 as runner
from scripts.evaluation import system_context_s3_backend as backend
from scripts.evaluation import system_context_s3_contracts as contracts
from scripts.evaluation.system_context_cases import cases, digest
from scripts.evaluation.system_context_contracts import (
    read_json,
    validate_public,
    write_new,
)
from scripts.evaluation.system_context_s3_projection import (
    instruction_bundle,
    render_pair,
)
from scripts.evaluation.system_context_s3_scoring import aggregate
from scripts.training.dashboard_tokenizer_v1 import (
    K0_RELATIVE,
    load_canonical_tokenizer,
)


def fixture():
    requests = []
    for i in range(48):
        for arm in ("P0", "P1"):
            requests.append({
                "request_id": f"request-{len(requests) + 1:03d}", "case_id": f"synthetic-{i}",
                "stage": "primary", "engine": "lora_r16", "arm": arm,
                "case": {"history": [], "expected_block": "synthetic_block" if i >= 43 else None, "stratum": "facts", "task": "general", "expected": {}, "apology_once": False},
                "render": {"input_tokens": 10, "input_token_ids_sha256": "a" * 64}, "parent_sha256": digest([]),
            })
    identity = {"input_sha256": digest(requests)}
    return {"build_id": "build-" + digest(identity)[:12], "identity": identity,
            "config": contracts.validate_contract(), "requests": requests, "expected_blocks": 10}


def response_fixture(request):
    output = "차근차근 이야기해 주세요."
    return {
        "request_sha256": digest(request), "status": "generated", "output": output,
        "generated": {"input_token_ids_sha256": "a" * 64, "omitted_messages": 0, "output": output},
        "consumers": {key: digest(output) for key in ("raw_sha256", "api_display_sha256", "stored_sha256")},
        "telemetry": {"elapsed_seconds": 1, "stop_reason": "eos", "output_token_ids": [1], "output_tokens": 1,
                      "retry_count": 0, "seed": 20260915, "precision": "bfloat16", "native_context_tokens": 8192,
                      "peak_allocated_bytes": 1024, "peak_reserved_bytes": 2048, "gpu_total_memory_used_mib": 1024,
                      "cache_scope": "fresh_generate_no_past_key_values",
                      "actual_generation_kwargs": {"do_sample": False, "max_new_tokens": 4096}},
    }


class S3ContractTests(unittest.TestCase):
    def test_contract_schedule_and_budget(self):
        config = contracts.validate_contract()
        schedule = runner.schedule(config)
        self.assertEqual(len(schedule), 96)
        self.assertEqual(len({r["request_id"] for r in schedule}), 96)
        self.assertEqual({r["engine"] for r in schedule}, {"lora_r16"})
        self.assertEqual(config["preflight_requests"], 0)
        self.assertEqual(config["maximum_generations"], 86)
        self.assertFalse(config["governance"]["phase8a_routing_used"])

    def test_contract_rejects_unknown_type_budget_and_intervention(self):
        base = contracts.validate_contract()
        for key, value in (("maximum_requests", 98), ("preflight_requests", False), ("engine", "ki20_final"), ("extra", True), ("instruction_arms", ["P0", "P1", "P2"])):
            with patch.object(contracts.parent, "read_json", return_value={**base, key: value}), self.assertRaises(ValueError):
                contracts.validate_contract()

    def test_longest_eligible_pair_uses_main_budget(self):
        config = contracts.validate_contract()
        rendered = {c["case_id"]: {arm: {"input_tokens": 10} for arm in ("P0", "P1")} for c in cases()}
        eligible = next(c for c in cases() if not c["expected_block"])
        blocked = next(c for c in cases() if c["expected_block"])
        rendered[eligible["case_id"]]["P1"]["input_tokens"] = 100
        rendered[blocked["case_id"]]["P0"]["input_tokens"] = 200
        scheduled = runner.schedule(config, rendered)
        self.assertEqual([r["case_id"] for r in scheduled[:2]], [eligible["case_id"]] * 2)
        self.assertEqual([r["arm"] for r in scheduled[:2]], ["P0", "P1"])

    def test_plan_contract_and_dry_run_cannot_execute(self):
        prepared = {**fixture(), "maximum_input_tokens": 10, "training_inventory": {}}
        with patch.object(runner, "prepare", return_value=prepared), patch.object(runner, "execute") as execute, redirect_stdout(io.StringIO()):
            for args in (["plan"], ["validate-contract"], ["execute"]):
                self.assertEqual(runner.main(args), 0)
        execute.assert_not_called()

    def test_execution_requires_explicit_flag_before_any_writes(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(runner, "private_directory") as mkdir, self.assertRaises(ValueError):
            runner.execute({})
        mkdir.assert_not_called()

    def test_frozen_payload_tamper_or_budget_is_rejected(self):
        original = fixture()
        runner.validate_prepared(original)
        for change in (lambda p: p["requests"].pop(), lambda p: p["requests"][0]["case"].update(history=["changed"]), lambda p: p.update(build_id="build-" + "a" * 12)):
            changed = deepcopy(original)
            change(changed)
            with self.assertRaises(ValueError):
                runner.validate_prepared(changed)

    def test_new_build_cannot_reset_existing_experiment_budget(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_new(root / "active-build.json", {"build_id": "different", "maximum_requests": 96})
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S3": "R16_P0_P1_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "예산"):
                runner.execute(prepared)
            worker.assert_not_called()

    def test_started_request_cannot_be_generated_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_new(root / "request-001.started.json", {"synthetic": True})
            with patch.object(runner.subprocess, "Popen") as process, self.assertRaisesRegex(ValueError, "재생성"):
                runner._start_worker(root, {"request_id": "request-001"})
            process.assert_not_called()

    def test_completed_resume_reuses_only_verified_outputs(self):
        prepared = fixture()
        original = deepcopy(prepared)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def generate(folder, request):
                value = response_fixture(request)
                write_new(folder / f"{request['request_id']}.response.json", value)
                return value

            with (
                patch.dict(os.environ, {"SYSTEM_CONTEXT_S3": "R16_P0_P1_V1"}),
                patch.object(runner, "RAW_ROOT", root),
                patch.object(runner, "build_path", return_value=root / prepared["build_id"]),
                patch.object(runner, "service_observation", return_value={"MainPID": 0}),
                patch.object(runner, "_start_worker", side_effect=generate) as worker,
                patch.object(runner, "publish") as publish,
                patch.object(runner, "verify", return_value={"status": "synthetic_verified"}),
                redirect_stdout(io.StringIO()),
            ):
                runner.execute(prepared)
                self.assertEqual(worker.call_count, 86)
                worker.reset_mock()
                runner.execute(original, resume=True)
                worker.assert_not_called()
                self.assertEqual(publish.call_args.kwargs["reused"], 96)
                path = root / prepared["build_id"] / "request-001.response.json"
                value = read_json(path, private=True)
                value["request_sha256"] = "f" * 64
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.execute(original, resume=True)
                worker.assert_not_called()

    def test_resume_rejects_changed_prepared_payload_before_worker(self):
        prepared = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = deepcopy(prepared)
            frozen["requests"][0]["case"]["history"] = ["변조"]
            write_new(root / prepared["build_id"] / "prepared.json", frozen)
            with patch.dict(os.environ, {"SYSTEM_CONTEXT_S3": "R16_P0_P1_V1"}), patch.object(runner, "RAW_ROOT", root), patch.object(runner, "build_path", return_value=root / prepared["build_id"]), patch.object(runner, "_start_worker") as worker, self.assertRaisesRegex(ValueError, "identity"):
                runner.execute(prepared, resume=True)
            worker.assert_not_called()

    def test_idle_gpu_lock_and_headers_precede_worker(self):
        request = fixture()["requests"][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = Mock()

            def finish(**_):
                write_new(root / "request-001.response.json", response_fixture(request))
                return 0

            process.wait.side_effect = finish
            with (
                patch("scripts.evaluation.dashboard_v115_replay.header_check") as headers,
                patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock", side_effect=lambda _: os.open(os.devnull, os.O_RDONLY)) as lock,
                patch.object(runner, "gpu_ready", side_effect=[False, True]),
                patch.object(runner.time, "sleep") as sleep,
                patch.object(runner.subprocess, "Popen", return_value=process) as launch,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(runner._start_worker(root, request)["status"], "generated")
                headers.assert_called_once()
                self.assertEqual(lock.call_count, 2)
                sleep.assert_called_once_with(20)
                self.assertEqual(launch.call_count, 1)
                self.assertIn("scripts.evaluation.system_context_s3", launch.call_args.args[0])
                self.assertEqual(launch.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")

    def test_worker_requires_new_flag_without_loading_model(self):
        with patch.dict(os.environ, {"SYSTEM_CONTEXT_DIAGNOSIS": "S0_S1_S2_V1"}, clear=True), patch.object(backend.dashboard, "_load_engine_model") as load, self.assertRaises(ValueError):
            backend.worker(Path("input"), Path("output"))
        load.assert_not_called()

    def test_response_identity_cost_retry_and_raw_tamper_are_rejected(self):
        request = fixture()["requests"][0]
        original = response_fixture(request)
        runner.validate_response(request, original)
        for mutate in (
            lambda r: r.update(request_sha256="f" * 64),
            lambda r: r.update(output="변조"),
            lambda r: r["telemetry"].update(retry_count=1),
            lambda r: r["telemetry"].update(output_tokens=2),
            lambda r: r["telemetry"].update(precision="float32"),
            lambda r: r["telemetry"].update(gpu_total_memory_used_mib=20000),
        ):
            changed = deepcopy(original)
            mutate(changed)
            with self.assertRaises(ValueError):
                runner.validate_response(request, changed)

    def test_path_traversal_and_public_raw_are_rejected(self):
        for name in (None, "../build-a", "build-" + "a" * 13):
            with self.assertRaises(ValueError):
                contracts.build_path(name)
        for payload in ({"messages": []}, {"child": {"output": "synthetic"}}, {"path": "/home/user/private"}):
            with self.assertRaises(ValueError):
                validate_public(payload)


class S3ScoringTests(unittest.TestCase):
    def test_paired_verdicts_cost_and_denominators(self):
        entries = []
        for arm, verdict in (("P0", "FAIL"), ("P1", "PASS")):
            entries.append({
                "stage": "primary", "engine": "lora_r16", "case_id": "facts-1", "arm": arm,
                "stratum": "facts", "parent_sha256": "same", "status": "generated", "input_tokens": 10,
                "scoring": {"metrics": {"required_fact_use": verdict, "fact_contradiction_absent": "UNSCORABLE"}},
                "telemetry": {"elapsed_seconds": 1, "peak_allocated_bytes": 2, "peak_reserved_bytes": 3, "output_tokens": 4, "stop_reason": "eos"},
            })
        report = aggregate(entries)
        metric = next(r for r in report["paired_p0_to_p1"] if r["stratum"] == "all" and r["metric"] == "required_fact_use")
        self.assertEqual(metric["improved"], 1)
        self.assertEqual(report["new_generations"], 2)
        self.assertEqual(report["scorer_version"], "role-aware-contract-v1.1.0")
        self.assertFalse(report["candidate_selected"])
        self.assertEqual(report["summaries"][0]["stop_reasons"], {"eos": 1})
        validate_public(report)
        with self.assertRaises(ValueError):
            aggregate(entries[:1])
        entries[1]["parent_sha256"] = "different"
        with self.assertRaises(ValueError):
            aggregate(entries)


class S3PublicationTests(unittest.TestCase):
    def test_public_reconstruction_and_private_or_public_tamper(self):
        prepared = fixture()
        prepared.update(model_registry={}, training_inventory={}, instruction_comparisons=[], maximum_input_tokens=10)
        reconstructed = deepcopy(prepared)
        prepared.update(service_before={"MainPID": 0}, started_at_utc="synthetic", code_commit_at_start="0" * 40)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, public = root / "private" / prepared["build_id"], root / "public" / prepared["build_id"]
            write_new(raw / "prepared.json", prepared)
            entries = []
            for request in prepared["requests"]:
                response = response_fixture(request) if not request["case"]["expected_block"] else {"request_sha256": digest(request), "status": "preblocked", "reason": "synthetic_block"}
                write_new(raw / f"{request['request_id']}.input.json", request)
                write_new(raw / f"{request['request_id']}.response.json", response)
                entries.append(runner._entry(request, response))
            with (
                patch.object(runner, "REPO_ROOT", root),
                patch.object(runner, "PUBLIC_ROOT", public.parent),
                patch.object(runner, "build_path", side_effect=lambda _, public=False: (root / "public" if public else root / "private") / prepared["build_id"]),
                patch.object(runner, "prepare", return_value=reconstructed),
                patch.object(runner, "service_observation", return_value={"MainPID": 0}),
                patch.object(runner.subprocess, "run", return_value=Mock(stdout="")),
            ):
                runner.publish(prepared, entries, reused=0)
                self.assertEqual(runner.verify(prepared["build_id"])["requests"], 96)
                path = raw / "request-001.response.json"
                old = path.read_bytes()
                path.write_text("{}")
                with self.assertRaises(ValueError):
                    runner.verify(prepared["build_id"])
                path.write_bytes(old)
                write_new(public / "unexpected.json", {}, private=False)
                with self.assertRaises(ValueError):
                    runner.verify(prepared["build_id"])


class S3ProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = rescore.verify_parent(rescore.validate_contract())["frozen"]
        cls.parents = [r for r in cls.frozen["requests"] if r["stage"] == "primary" and r["engine"] == "lora_r16" and r["arm"] == "C_FULL"]
        cls.context = runner.prepare_context()
        cls.tokenizer = load_canonical_tokenizer(contracts.REPO_ROOT / K0_RELATIVE)
        cls.bundle = instruction_bundle(contracts.validate_contract())

    def test_all_48_parent_requests_facts_history_and_p0_are_unchanged(self):
        changed, identical = 0, 0
        for request in self.parents:
            original = deepcopy(request)
            pair, difference = render_pair(request, self.context, self.tokenizer, self.bundle)
            self.assertEqual(request, original)
            self.assertEqual(pair["P0"], request["render"])
            self.assertEqual(pair["P0"]["messages"][1:], pair["P1"]["messages"][1:])
            self.assertEqual(pair["P1"]["omitted_messages"], 0)
            self.assertLessEqual(pair["P1"]["input_tokens"], 4096)
            changed += difference["instruction_changed"]
            identical += pair["P0"] == pair["P1"]
        self.assertEqual(changed + identical, 48)
        self.assertGreater(changed, 0)
        self.assertGreater(identical, 0)

    def test_wrong_parent_tokens_or_binding_are_rejected(self):
        request = deepcopy(self.parents[0])
        request["render"]["input_token_ids"][0] += 1
        with self.assertRaises(ValueError):
            render_pair(request, self.context, self.tokenizer, self.bundle)
        request = deepcopy(self.parents[0])
        request["case"]["binding"]["snapshot_sha256"] = "f" * 64
        with self.assertRaises(backend.dashboard.Phase5DashboardError):
            render_pair(request, self.context, self.tokenizer, self.bundle)


if __name__ == "__main__":
    unittest.main()
