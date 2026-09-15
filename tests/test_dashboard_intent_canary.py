# test_dashboard_intent_canary.py - v1.16 의도 정책을 합성 HTTP·직접 호출·새 CPU 프로세스로 검증한다.

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.training import dashboard_grounding_v3 as policy
from scripts.training import phase5_dashboard_v1_16 as dashboard
from tests.test_dashboard_grounding_v3 import binding_fixture
from tests.test_phase5_dashboard_v1_16 import context_fixture, generated_fixture

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 6)
MATRIX = (
    ("오늘 힘들었어", None),
    ("오늘은 사주 얘기를 쉬고 싶어", None),
    ("운세 얘기는 하지 말고 내일 보낼 메시지만 다듬어줘", None),
    ("내일 날씨 알려줄래?", None),
    ("내 일간을 설명해줘", None),
    ("2026-09-05 일진", None),
    ("어제 운세", None),
    ("사주 말고 내일 운세 봐줘", policy.DATE_REBIND),
    ("그냥 내일 운세를 메시지로 써줘", policy.DATE_REBIND),
    ("그럼 내일은 어때?", policy.DATE_REBIND),
    ("오늘 힘들었어. 그럼 내일은 어때?", policy.SCOPE_UNSUPPORTED),
    ("그럼 내일이 어때?", policy.DATE_AMBIGUOUS),
    ("오늘 운세", policy.DATE_REBIND),
    ("이번 주 운세", policy.SCOPE_UNSUPPORTED),
    ("운세 좀", policy.DATE_AMBIGUOUS),
    ("9월 5일 운세", policy.DATE_AMBIGUOUS),
    ("2026-02-30 일진", policy.DATE_AMBIGUOUS),
    ("2026-09-05와 2026-09-06 운세", policy.SCOPE_UNSUPPORTED),
)


def fresh_process_probe():
    """새 interpreter에서 정책/HTTP/direct가 자기 모듈로 동작하는지 검사한다."""
    names = [
        __name__ + ".IntentCanaryTests.test_http_matrix_before_generation",
        __name__ + ".IntentCanaryTests.test_direct_generation_matrix_and_saved_policy",
    ]
    result = unittest.TextTestRunner(stream=io.StringIO()).run(
        unittest.defaultTestLoader.loadTestsFromNames(names)
    )
    gpu_loaded = bool({"torch", "transformers", "peft"} & set(sys.modules))
    passed = result.wasSuccessful() and not gpu_loaded and not result.skipped
    print(
        json.dumps(
            {
                "passed": passed,
                "tests": result.testsRun,
                "matrix_cases": len(MATRIX),
                "gpu_modules_loaded": gpu_loaded,
            }
        )
    )
    raise SystemExit(0 if passed else 1)


class IntentCanaryTests(unittest.TestCase):
    def test_http_matrix_before_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            context = context_fixture(Path(temp))
            binding, runner = Mock(), Mock(return_value={"synthetic_canary": True})
            binding.public_snapshot.return_value = binding_fixture()
            # 합성 의도 행렬은 카운터 초기화 전에 별도 예산을 설정한다.
            context["config"]["chart_only_runtime"]["rate_limits_per_minute"][
                "model_generation"
            ] = 100
            server = dashboard.DashboardHTTPServer(
                ("127.0.0.1", 0),
                context,
                dashboard.V116_ASSET_ROOT,
                "canary-csrf",
                chart_only_binding=binding,
                chart_only_runtime_requested=True,
                generation_runner=runner,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with (
                    patch.object(policy, "kst_today", return_value=TODAY),
                    patch.object(
                        dashboard, "_generation_gate", return_value={"allowed": True}
                    ),
                ):
                    for prompt, code in MATRIX:
                        with self.subTest(prompt=prompt):
                            runner.reset_mock()
                            request = urllib.request.Request(
                                base + "/api/generate",
                                data=json.dumps(
                                    {
                                        "prompt": prompt,
                                        "session_id": None,
                                        "runtime_session_id": "a" * 24,
                                    }
                                ).encode(),
                                headers={
                                    "Origin": base,
                                    "X-CSRF-Token": "canary-csrf",
                                    "Content-Type": "application/json",
                                },
                            )
                            if code:
                                with self.assertRaises(
                                    urllib.error.HTTPError
                                ) as raised:
                                    urllib.request.urlopen(request, timeout=5)
                                self.assertEqual(raised.exception.code, 409)
                                self.assertEqual(
                                    json.loads(raised.exception.read())["code"], code
                                )
                                runner.assert_not_called()
                            else:
                                with urllib.request.urlopen(
                                    request, timeout=5
                                ) as response:
                                    self.assertEqual(response.status, 200)
                                    self.assertTrue(
                                        json.loads(response.read())["synthetic_canary"]
                                    )
                                runner.assert_called_once()
                                self.assertEqual(
                                    runner.call_args.args[-1], binding_fixture()
                                )
            finally:
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
            self.assertFalse(thread.is_alive())

    def test_direct_generation_matrix_and_saved_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            context = context_fixture(Path(temp))
            with (
                patch.object(policy, "kst_today", return_value=TODAY),
                patch(
                    "scripts.training.mix2k_v4_lora.acquire_mix2k_v4_gpu_lock",
                    side_effect=lambda _: os.open(os.devnull, os.O_RDONLY),
                ) as lock,
                patch.object(
                    dashboard, "_generation_gate", return_value={"allowed": True}
                ),
                patch.object(
                    dashboard, "_engine_availability", return_value={"available": True}
                ),
                patch.object(
                    dashboard,
                    "_generate_engine_conversation",
                    return_value=generated_fixture("잠시 쉬어도 괜찮아."),
                ) as generate,
            ):
                for prompt, code in MATRIX:
                    with self.subTest(prompt=prompt):
                        lock.reset_mock()
                        generate.reset_mock()
                        if code:
                            with self.assertRaises(
                                dashboard.DashboardRequestError
                            ) as raised:
                                dashboard.execute_manual_generation(
                                    context, prompt, runtime_binding=binding_fixture()
                                )
                            self.assertEqual(raised.exception.reason_code, code)
                            lock.assert_not_called()
                            generate.assert_not_called()
                        else:
                            result = dashboard.execute_manual_generation(
                                context,
                                prompt,
                                engine_selection="lora_r16",
                                runtime_binding=binding_fixture(),
                            )
                            generate.assert_called_once()
                            session = dashboard.manual_session_payload(
                                context, result["session_id"]
                            )
                            diagnostic = session["messages"][-1]["diagnostics"]
                            self.assertEqual(
                                diagnostic["date_scope"],
                                policy.date_scope(
                                    prompt, binding_fixture(), today=TODAY
                                ),
                            )
                            self.assertEqual(
                                diagnostic["scorer_version"], policy.SCORER_VERSION
                            )
                            self.assertEqual(
                                result["grounding_gate"]["intent"],
                                policy.prompt_intent(prompt),
                            )
                            self.assertEqual(result["output"], "잠시 쉬어도 괜찮아.")
                            self.assertEqual(
                                diagnostic["date_scope"]["intent_policy_version"],
                                policy.INTENT_POLICY_VERSION,
                            )

    def test_spawn_target_is_the_candidate_and_blocks_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            context = context_fixture(Path(temp))
            context["config_path"] = ROOT / dashboard.DEFAULT_CONFIG
            binding = binding_fixture()
            response = {
                "persisted": True,
                "local_only": True,
                "session_id": "a" * 24,
                "runtime_binding_applied": True,
                "runtime_snapshot_sha256": binding["snapshot_sha256"],
            }
            with (
                patch.object(policy, "kst_today", return_value=TODAY),
                patch.object(
                    dashboard.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, stdout=json.dumps(response), stderr=""
                    ),
                ) as spawn,
            ):
                for prompt, code in MATRIX:
                    spawn.reset_mock()
                    args = (context, prompt, None, None, "lora_r16", None, binding)
                    if code:
                        with self.assertRaises(dashboard.DashboardRequestError):
                            dashboard._manual_generation_subprocess(*args)
                        spawn.assert_not_called()
                    else:
                        dashboard._manual_generation_subprocess(*args)
                        command = spawn.call_args.args[0]
                        self.assertEqual(
                            command[1], str(Path(dashboard.__file__).resolve())
                        )
                        self.assertIn("phase5_dashboard_v1_16.py", command[1])
                        self.assertIn(str(context["config_path"]), command)

    def test_fresh_process_uses_no_gpu_or_parent_monkeypatch(self):
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from tests.test_dashboard_intent_canary import fresh_process_probe; fresh_process_probe()",
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": "",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "passed": True,
                "tests": 2,
                "matrix_cases": len(MATRIX),
                "gpu_modules_loaded": False,
            },
        )

    def test_candidate_snapshot_only_changes_version_identity_and_import(self):
        previous = (ROOT / "scripts/training/phase5_dashboard_v1_15.py").read_text()
        current = Path(dashboard.__file__).read_text()
        current = current.replace(
            "phase5-dashboard-v1.16.0-intent-candidate.json",
            "phase5-dashboard-v1.15.0-grounding-candidate.json",
        )
        for new, old in (
            ("phase5_dashboard_v1_16", "phase5_dashboard_v1_15"),
            ("dashboard_grounding_v3", "dashboard_grounding_v2"),
            ("1.16.0", "1.15.0"),
            ("v1.16", "v1.15"),
            ("V116", "V115"),
            ("8769", "8768"),
            ("grounding-v3.0.0", "grounding-v2.0.1"),
        ):
            current = current.replace(new, old)
        self.assertEqual(current.splitlines()[1:], previous.splitlines()[1:])
        current_config = (ROOT / dashboard.DEFAULT_CONFIG).read_text()
        for new, old in (
            ("1.16.0", "1.15.0"),
            ("8769", "8768"),
            ("grounding-v3.0.0", "grounding-v2.0.1"),
        ):
            current_config = current_config.replace(new, old)
        original_config = (
            ROOT
            / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.15.0-grounding-candidate.json"
        ).read_text()
        self.assertEqual(json.loads(current_config), json.loads(original_config))

    def test_tampered_binding_is_not_accepted_for_ordinary_chat(self):
        binding = deepcopy(binding_fixture())
        binding["value"]["chart"]["hard_facts"]["day_master"]["stem"] = "甲"
        with patch.object(dashboard, "_generate_engine_conversation") as generate:
            with self.assertRaises(dashboard.Phase5DashboardError):
                dashboard.execute_manual_generation(
                    {}, "오늘 힘들었어", runtime_binding=binding
                )
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
