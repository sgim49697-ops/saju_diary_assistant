# test_system_context_s4_consumers.py - 저장 함수를 모의하지 않고 S4 별칭·실패 예산·사전 검증을 회귀한다.

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import system_context_s4 as runner
from scripts.evaluation import system_context_s4_consumers as consumers
from scripts.evaluation import system_context_s4_contracts as contracts
from scripts.evaluation import system_context_s4_models as models
from scripts.evaluation.system_context_backend import replay_output_consumers
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_contracts import file_sha
from scripts.evaluation.system_context_s4_projection import input_identity
from scripts.training.dashboard_tokenizer_v1 import BACKEND_SHA256, TOKENIZER_FILES
from scripts.training.phase5_dashboard_v1_15 import Phase5DashboardError
from tests.test_dashboard_grounding_v2 import binding_fixture
from tests.test_phase5_dashboard_v1_15 import context_fixture
from tests.test_system_context_s4 import fixture, response_fixture


class S4RealConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokenizers = {engine: models.load_tokenizer(engine) for engine in models.ENGINES}
        cls.files = {
            engine: {name: file_sha(root / name) for name in TOKENIZER_FILES}
            for engine, root in (("k0_instruct", models.REPO_ROOT / models.K0_RELATIVE),
                                 ("kanana3b_instruct", models.model_root()))
        }

    def request(self, engine, *, bound=False):
        prompt = "내 일간을 알려줘." if bound else "실수해서 속상해. 이야기 좀 들어줘."
        messages = [{"role": "user", "content": prompt}]
        tokenizer = self.tokenizers[engine]
        ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        identity = input_identity(tokenizer, messages, ids, engine=engine, expected_backend=BACKEND_SHA256)
        return {
            "request_id": "synthetic-request", "engine": engine,
            "case": {"prompt": prompt, "binding": binding_fixture() if bound else None, "expected_block": None},
            "render": {**identity, "messages": messages, "input_token_ids": ids, "input_tokens": len(ids)},
        }

    @staticmethod
    def generated(request):
        return {
            **{k: request["render"][k] for k in consumers.IDENTITY_KEYS},
            "input_tokens": request["render"]["input_tokens"], "omitted_messages": 0,
            "output": "일간은 갑목입니다. 잘못된 합성 응답도 고치지 않고 보존합니다.",
            "peak_allocated_bytes": 1024, "gpu_total_memory_used_mib": 1024,
        }

    def replay(self, context, request, generated=None, files=None):
        return consumers.replay_consumers(
            context, request, generated or self.generated(request),
            self.tokenizers[request["engine"]], self.tokenizers["k0_instruct"], files or self.files,
        )

    def test_actual_storage_rejects_old_s4_namespace_and_accepts_proven_copy_for_both_models(self):
        with tempfile.TemporaryDirectory() as directory:
            context = context_fixture(Path(directory))
            for engine in models.ENGINES:
                for bound in (False, True):
                    with self.subTest(engine=engine, bound=bound):
                        request = self.request(engine, bound=bound)
                        generated = self.generated(request)
                        before = deepcopy(generated)
                        with self.assertRaises(Phase5DashboardError):
                            replay_output_consumers(context, request["case"], "k0_instruct", generated)
                        result = self.replay(context, request, generated)
                        self.assertEqual(generated, before)
                        self.assertFalse(result["changed"])
                        self.assertFalse(result["browser_executed"])
                        self.assertEqual(result["model_engine"], engine)
                        self.assertEqual(result["tokenizer_alias"], consumers.alias_receipt(request))
                        for key in ("raw_sha256", "stored_sha256", "api_display_sha256"):
                            self.assertEqual(result[key], digest(generated["output"]))

    def test_alias_rejects_file_template_token_backend_and_identity_changes_before_storage(self):
        for mutation in ("file", "template", "tokens", "backend", "revision", "rendered", "generated"):
            with self.subTest(mutation=mutation):
                request = self.request("kanana3b_instruct")
                generated, files = self.generated(request), deepcopy(self.files)
                if mutation == "file":
                    files["kanana3b_instruct"]["chat_template.jinja"] = "f" * 64
                elif mutation == "tokens":
                    request["render"]["input_token_ids"] = [1]
                elif mutation in {"backend", "revision", "rendered"}:
                    key = {"backend": "tokenizer_backend_sha256", "revision": "tokenizer_revision", "rendered": "rendered_prompt_sha256"}[mutation]
                    request["render"][key] = "forged"
                elif mutation == "generated":
                    generated["input_token_ids_sha256"] = "f" * 64
                with patch.object(consumers, "replay_output_consumers") as store:
                    if mutation == "template":
                        with patch.object(self.tokenizers["kanana3b_instruct"], "chat_template", "changed"), self.assertRaises(ValueError):
                            self.replay({}, request, generated, files)
                    else:
                        with self.assertRaises(ValueError):
                            self.replay({}, request, generated, files)
                    store.assert_not_called()

    def test_all_172_preflight_replays_use_real_storage_with_zero_model_calls(self):
        requests = [self.request(models.ENGINES[i % 2], bound=i % 3 == 0) for i in range(172)]
        requests += [{"case": {"expected_block": "synthetic_block"}} for _ in range(20)]
        for i, request in enumerate(requests):
            request["request_id"] = f"synthetic-{i}"
        with tempfile.TemporaryDirectory() as directory, patch("scripts.evaluation.system_context_s4_backend.load_model") as load:
            result = consumers.preflight(context_fixture(Path(directory)), requests, self.tokenizers, self.files)
            self.assertEqual(result["actual_storage_api_replays"], 172)
            self.assertEqual(result["model_calls"], 0)
            self.assertFalse(result["gpu_used"])
            load.assert_not_called()
            with self.assertRaisesRegex(ValueError, "분모"):
                consumers.preflight({}, [], self.tokenizers, self.files)

    def test_consumer_failure_is_not_suppressed(self):
        request = self.request("k0_instruct")
        with patch.object(consumers, "replay_output_consumers", side_effect=ValueError("synthetic failure")), self.assertRaisesRegex(ValueError, "synthetic failure"):
            self.replay({}, request)


class S4RecoveryContractTests(unittest.TestCase):
    def test_failed_attempt_is_preserved_and_budget_includes_one_additional_request(self):
        previous = contracts.verify_previous_attempt()
        accounting = contracts.validate_contract()["request_accounting"]
        self.assertEqual(previous["consumed_requests"], 1)
        self.assertEqual(accounting["before_s4"] + accounting["preserved_failed_requests"] + accounting["new_comparison_requests"], 631)
        self.assertEqual(accounting["overall_limit"] - 631, 49)
        self.assertEqual(accounting["reserved_s6"] + accounting["remaining_shared_reserve"], 49)
        self.assertNotEqual(contracts.PREVIOUS_ROOT, contracts.RAW_ROOT)

    def test_previous_attempt_missing_extra_and_tampered_files_are_rejected(self):
        for mutation in ("missing", "extra", "changed"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".experiment.lock").touch()
                path = root / "synthetic-evidence.json"
                path.write_text("{}")
                pins = {path.name: file_sha(path)}
                with patch.object(contracts, "PREVIOUS_ROOT", root), patch.object(contracts, "PREVIOUS_PINS", pins):
                    contracts.verify_previous_attempt()
                    if mutation == "missing":
                        path.unlink()
                    elif mutation == "extra":
                        (root / "request-002.started.json").write_text("{}")
                    else:
                        path.write_text('{"changed":true}')
                    with self.assertRaises(ValueError):
                        contracts.verify_previous_attempt()

    def test_missing_failed_cpu_preflight_or_forged_alias_cannot_pass_validation(self):
        for key, value in (("status", "failed"), ("actual_storage_api_replays", 171), ("model_calls", 1)):
            prepared = fixture()
            prepared["consumer_preflight"][key] = value
            with self.assertRaises(ValueError):
                runner.validate_prepared(prepared)
        request = fixture()["requests"][0]
        for section, field in (("generated", "tokenizer_revision"), ("generated", "rendered_prompt_sha256"), ("consumers", "tokenizer_alias")):
            response = response_fixture(request)
            response[section][field] = "forged"
            with self.assertRaises(ValueError):
                runner.validate_response(request, response)
