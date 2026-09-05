# test_phase5_dashboard_v1_15.py - v1.15 사전 차단·원출력 보존·세션 identity 격리를 검증한다.

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_grounding_v2 import SCORER_VERSION
from scripts.training.dashboard_tokenizer_v1 import BACKEND_SHA256, TOKENIZER_REVISION
from tests.test_dashboard_grounding_v2 import binding_fixture

ROOT = Path(__file__).resolve().parents[1]


def context_fixture(root):
    config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
    run = root / "runs/KI20-MIX-v2/v1.2.0/run-123456abcdef"
    run.mkdir(parents=True)
    return {
        "repo_root": root,
        "run_root": run,
        "config": config,
        "manifest": {
            "run_id": "KI20-MIX-v2",
            "run_build_id": run.name,
            "run_sha256": "a" * 64,
        },
        "inference_engines": config["inference_engines"],
        "prompt_profiles": dashboard._load_prompt_profiles(ROOT, config),
        "runtime_canary_active": False,
        "chart_only_runtime_active": True,
    }


def generated_fixture(output):
    return {
        "output": output,
        "input_tokens": 120,
        "omitted_messages": 0,
        "peak_allocated_bytes": 1,
        "gpu_total_memory_used_mib": 100,
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_backend_sha256": BACKEND_SHA256,
        "rendered_prompt_sha256": "a" * 64,
        "input_token_ids_sha256": "b" * 64,
    }


class DashboardV115Tests(unittest.TestCase):
    def test_http_date_block_happens_before_runner_and_rejects_client_clock(self):
        with tempfile.TemporaryDirectory() as temp:
            context = context_fixture(Path(temp))
            binding = Mock()
            binding.public_snapshot.return_value = binding_fixture()
            runner = Mock()
            server = dashboard.DashboardHTTPServer(
                ("127.0.0.1", 0),
                context,
                dashboard.V115_ASSET_ROOT,
                "test-csrf",
                chart_only_binding=binding,
                chart_only_runtime_requested=True,
                generation_runner=runner,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            payload = {
                "prompt": "내일 운세",
                "session_id": None,
                "runtime_session_id": "a" * 24,
            }
            try:
                with (
                    patch(
                        "scripts.training.dashboard_grounding_v2.kst_today",
                        return_value=date(2026, 9, 5),
                    ),
                    patch.object(
                        dashboard, "_generation_gate", return_value={"allowed": True}
                    ),
                ):
                    for extra, code in [
                        ({}, "RUNTIME_DATE_REBIND_REQUIRED"),
                        ({"today": "2026-09-04"}, "HTTP_400"),
                    ]:
                        request = urllib.request.Request(
                            base + "/api/generate",
                            data=json.dumps({**payload, **extra}).encode(),
                            headers={
                                "Origin": base,
                                "X-CSRF-Token": "test-csrf",
                                "Content-Type": "application/json",
                            },
                        )
                        with self.assertRaises(urllib.error.HTTPError) as raised:
                            urllib.request.urlopen(request, timeout=5)
                        response = json.loads(raised.exception.read())
                        self.assertEqual(response["code"], code)
                    runner.assert_not_called()
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()

    def test_candidate_config_preserves_defaults_and_old_version(self):
        config = json.loads((ROOT / dashboard.DEFAULT_CONFIG).read_text())
        dashboard.validate_config(config)
        self.assertEqual(config["server"]["port"], 8768)
        self.assertEqual(config["inference_engines"]["default_selection"], "ki20_final")
        self.assertFalse(config["chart_only_runtime"]["enabled_by_default"])
        self.assertFalse(config["model_check"]["generation"]["fix_mistral_regex"])
        self.assertFalse(config["governance"]["production_promotion_allowed"])
        from scripts.training import phase5_dashboard_v1_14 as previous

        self.assertNotEqual(dashboard.GROUNDING_GATE_ID, previous.GROUNDING_GATE_ID)
        previous.validate_config(
            json.loads((ROOT / previous.DEFAULT_CONFIG).read_text())
        )

    def test_date_block_never_acquires_gpu_or_generates(self):
        with (
            patch(
                "scripts.training.dashboard_grounding_v2.kst_today",
                return_value=date(2026, 9, 5),
            ),
            patch("scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock") as lock,
            patch.object(dashboard, "_generate_engine_conversation") as generate,
        ):
            for prompt, code in [
                ("내일 운세", "RUNTIME_DATE_REBIND_REQUIRED"),
                ("이번 주 운세", "RUNTIME_PERIOD_SCOPE_UNSUPPORTED"),
                ("9월 7일 운세", "RUNTIME_DATE_SELECTION_REQUIRED"),
            ]:
                with self.assertRaises(dashboard.DashboardRequestError) as raised:
                    dashboard.execute_manual_generation(
                        {}, prompt, runtime_binding=binding_fixture()
                    )
                self.assertEqual(raised.exception.reason_code, code)
            lock.assert_not_called()
            generate.assert_not_called()

    def test_raw_warning_saved_once_and_identity_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            context = context_fixture(Path(temp))
            raw = "원국 일주 병인, 오늘 일진 병오입니다."
            with (
                patch(
                    "scripts.training.dashboard_grounding_v2.kst_today",
                    return_value=date(2026, 9, 5),
                ),
                patch.object(
                    dashboard, "_generation_gate", return_value={"allowed": True}
                ),
                patch.object(
                    dashboard, "_engine_availability", return_value={"available": True}
                ),
                patch.object(
                    dashboard,
                    "_generate_engine_conversation",
                    return_value=generated_fixture(raw),
                ) as generate,
            ):
                result = dashboard.execute_manual_generation(
                    context,
                    "오늘 사주",
                    engine_selection="lora_r16",
                    runtime_binding=binding_fixture(),
                )
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(result["output"], raw)
                self.assertEqual(result["session"]["schema_version"], "1.7.0")
                self.assertFalse(
                    result["grounding_gate"]["passed_by_engine"]["lora_r16"]
                )
                session = dashboard.manual_session_payload(
                    context, result["session_id"]
                )
                diagnostic = session["messages"][-1]["diagnostics"]
                self.assertEqual(diagnostic["scorer_version"], SCORER_VERSION)
                self.assertTrue(diagnostic["raw_output_preserved"])
                self.assertEqual(
                    diagnostic["date_scope"]["snapshot_date"], "2026-09-05"
                )
                session["messages"][-1]["diagnostics"]["tokenizer_backend_sha256"] = (
                    "c" * 64
                )
                with self.assertRaises(dashboard.Phase5DashboardError):
                    dashboard._validate_manual_session(
                        context, session, result["session_id"]
                    )
                self.assertFalse((context["run_root"] / "dashboard/v1.14.0").exists())

    def test_each_engine_uses_own_history_and_first_turn_matches(self):
        first = [
            dashboard._messages_for_engine([], engine, "질문", "system", "runtime")
            for engine in ("k0_instruct", "ki20_final", "lora_r16")
        ]
        self.assertEqual(first[0], first[1])
        self.assertEqual(first[1], first[2])
        history = [
            {"role": "user", "content": "질문"},
            {"role": "assistant", "engine_id": "k0_instruct", "content": "K0 답"},
            {"role": "assistant", "engine_id": "lora_r16", "content": "R16 답"},
        ]
        messages = dashboard._messages_for_engine(history, "lora_r16", "후속", "system")
        self.assertEqual(
            [m["content"] for m in messages], ["system", "질문", "R16 답", "후속"]
        )
