# test_dashboard_product_http.py - 제품 후보의 실제 HTTP·worker·binding 경계와 CPU 실행을 검증한다.

import base64
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
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.runtime.chart_day_dashboard_binding import ChartDayDashboardBindingError
from scripts.runtime.product_dashboard_binding_v1 import ProductDashboardBinding
from scripts.training import dashboard_product_policy_v1 as policy
from scripts.training import phase5_dashboard_v1_18 as dashboard
from tests.test_dashboard_grounding_v3 import binding_fixture
from tests.test_dashboard_product_v1 import ROOT, context_fixture, synthetic_app

RUNTIME_ID = "c" * 24


def binding_owner():
    source = binding_fixture()
    state = {**deepcopy(source["value"]), "state_revision": source["state_revision"]}
    owner = ProductDashboardBinding.__new__(ProductDashboardBinding)
    owner._closed = False
    owner._operation_lock = threading.Lock()
    owner.adapter = SimpleNamespace(store=SimpleNamespace(read=lambda _sid: deepcopy(state)))
    owner.close = lambda: None
    return owner, state


@contextmanager
def http_app(context, *, owner=None, runner=None):
    context["config"]["chart_only_runtime"]["rate_limits_per_minute"]["model_generation"] = 100
    server = dashboard.DashboardHTTPServer(("127.0.0.1", 0), context, dashboard.V118_ASSET_ROOT, "test-csrf", chart_only_binding=owner, chart_only_runtime_requested=owner is not None, generation_runner=runner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield server, base
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def post(base, prompt="내 일간이 뭐야?", *, session_id=None, bound=True, **extra):
    payload = {"prompt": prompt, "session_id": session_id, "engine_selection": "lora_r16", "profile": "product_v1", **extra}
    if bound:
        payload["runtime_session_id"] = RUNTIME_ID
        payload.setdefault("runtime_binding_scope", "chart")
    request = urllib.request.Request(base + "/api/generate", data=json.dumps(payload).encode(), headers={"Origin": base, "X-CSRF-Token": "test-csrf", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


class ProductHTTPTests(unittest.TestCase):
    def test_internal_policy_error_is_redacted_and_not_persisted(self):
        with synthetic_app() as (context, generate, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (_, base), patch.object(policy, "plan_response", side_effect=ValueError("synthetic-private-payload")):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(base)
                self.assertEqual(raised.exception.code, 500)
                result = json.loads(raised.exception.read())
                self.assertEqual(result["code"], "INTERNAL_ERROR")
                self.assertEqual(result["response_kind"], "blocked")
                self.assertNotIn("synthetic-private-payload", json.dumps(result))
                self.assertEqual(dashboard.manual_sessions_payload(context)["items"], [])
            generate.assert_not_called()

    def test_direct_response_still_requires_basic_auth(self):
        with synthetic_app() as (context, generate, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (server, base):
                server.basic_auth = ("synthetic", "test-only-password")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(base)
                self.assertEqual(raised.exception.code, 401)
                payload = {"prompt": "내 일간 뭐야?", "session_id": None, "runtime_session_id": RUNTIME_ID, "runtime_binding_scope": "chart"}
                auth = base64.b64encode(b"synthetic:test-only-password").decode()
                request = urllib.request.Request(base + "/api/generate", data=json.dumps(payload).encode(), headers={"Origin": base, "X-CSRF-Token": "test-csrf", "Content-Type": "application/json", "Authorization": "Basic " + auth})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(json.loads(response.read())["response_kind"], "direct_fact")
            generate.assert_not_called()

    def test_authenticated_candidate_startup_and_separate_default_port(self):
        with synthetic_app() as (context, generate, _), patch.object(dashboard, "DashboardHTTPServer") as factory, patch.object(dashboard, "_remote_access_settings", return_value=("https://synthetic.invalid", ("synthetic", "test-only-password"))), redirect_stdout(io.StringIO()):
            context["chart_only_runtime"] = {"asset_root": dashboard.V118_ASSET_ROOT}
            factory.return_value.server_address = ("127.0.0.1", 8770)
            dashboard.serve(context, "127.0.0.1", 8770)
            factory.return_value.serve_forever.assert_called_once()
            factory.return_value.server_close.assert_called_once()
            self.assertEqual(factory.call_args.args[5], ("synthetic", "test-only-password"))
            self.assertFalse(factory.call_args.args[-1])
            self.assertEqual(dashboard._parser().parse_args(["serve"]).port, 8770)
            generate.assert_not_called()

    def test_direct_real_http_never_checks_gpu_or_launches_worker(self):
        with synthetic_app() as (context, generate, lock):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (_, base), patch.object(dashboard, "_generation_gate", side_effect=AssertionError("직접 조회 GPU 금지")), patch.object(dashboard, "_manual_generation_subprocess", side_effect=AssertionError("직접 조회 worker 금지")):
                result = post(base)
                self.assertEqual(result["response_kind"], "direct_fact")
                self.assertTrue(result["persisted"])
                stored = dashboard.manual_session_payload(context, result["session_id"])
                self.assertEqual(stored["messages"][-1]["content"], result["output"])
                self.assertEqual(stored["binding_snapshot"], owner.public_snapshot(RUNTIME_ID, scope="chart"))
            generate.assert_not_called()
            lock.assert_not_called()

    def test_http_generated_storage_and_general_switch(self):
        with synthetic_app() as (context, generate, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner, runner=dashboard.execute_manual_generation) as (_, base):
                first = post(base)
                second = post(base, "사주 말고 하소연을 들어줘", session_id=first["session_id"])
                self.assertEqual(second["response_kind"], "model_generated")
                self.assertEqual(second["output"], second["session"]["messages"][-1]["content"])
                self.assertEqual(generate.call_args.args[-1][0]["content"], policy.BASE_PROMPT)
                self.assertEqual(second["session"]["turn_count"], 2)

    def test_source_revision_changed_during_worker_is_never_saved(self):
        with synthetic_app() as (context, _, _):
            owner, state = binding_owner()
            def race(*args, **kwargs):
                result = dashboard.execute_manual_generation(*args, **kwargs)
                state["state_revision"] += 1
                return result
            with http_app(context, owner=owner, runner=race) as (_, base):
                first = post(base)
                path = dashboard._manual_session_path(context, first["session_id"])
                before = path.read_bytes()
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(base, "사주 말고 하소연을 들어줘", session_id=first["session_id"])
                self.assertEqual(raised.exception.code, 409)
                self.assertEqual(json.loads(raised.exception.read())["code"], "RUNTIME_REQUEST_STATE_CHANGED")
                self.assertEqual(path.read_bytes(), before)

    def test_client_cannot_supply_plan_history_clock_or_binding(self):
        with synthetic_app() as (context, generate, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (_, base):
                for extra in ({"history": []}, {"today": "2026-09-05"}, {"runtime_binding": binding_fixture()}, {"response_kind": "direct_fact"}, {"defer_persistence": True}, {"runtime_binding_scope": "unsafe"}):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        post(base, **extra)
                    self.assertEqual(raised.exception.code, 400)
            generate.assert_not_called()

    def test_feature_off_and_csrf_are_not_bypassed_by_direct_fact(self):
        with synthetic_app() as (context, generate, _), http_app(context) as (_, base):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                post(base)
            self.assertEqual(json.loads(raised.exception.read())["code"], "RUNTIME_FEATURE_DISABLED")
            request = urllib.request.Request(base + "/api/generate", data=b"{}", headers={"Origin": base, "Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 403)
            generate.assert_not_called()

    def test_rate_limit_applies_to_direct_facts(self):
        with synthetic_app() as (context, _, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (server, base):
                server.rate_limiters["model_generation"] = dashboard.SlidingWindowRateLimiter(1)
                post(base)
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(base)
                self.assertEqual(raised.exception.code, 429)

    def test_day_and_chart_snapshot_scopes_cannot_silently_switch(self):
        with synthetic_app() as (context, _, _):
            owner, _ = binding_owner()
            with http_app(context, owner=owner) as (_, base):
                first = post(base, runtime_binding_scope="chart_day")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    post(base, session_id=first["session_id"], runtime_binding_scope="chart")
                self.assertEqual(raised.exception.code, 409)

    def test_real_cli_worker_direct_answer_with_no_model_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="saju-product-cli-") as directory:
            root = Path(directory)
            context = context_fixture(root)
            run = context["run_root"]
            (run / "run_manifest.json").write_text(json.dumps({**context["manifest"], "production_promotion_allowed": False, "blind_source_test_inspected": False}))
            training = context["config"]["training_contract"]
            resolved = {"training": {k: training[k] for k in ("logging_steps", "eval_steps", "save_steps", "expected_optimizer_steps", "preserved_milestone_steps")}, "operational_limits": {"max_total_gpu_memory_used_mib": training["gpu_hard_cap_mib"]}}
            (run / "config.resolved.json").write_text(json.dumps(resolved))
            payload = {"prompt": "내 일간은 뭐야?", "session_id": None, "runtime_binding": binding_fixture(), "defer_persistence": True}
            command = [sys.executable, "-B", "-m", "scripts.training.phase5_dashboard_v1_18", "--artifact-root", str(root), "--run-root", str(run), "generate", "--execute", "--enable-chart-only-runtime-binding"]
            result = subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=30, cwd=ROOT, env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["response_kind"], "direct_fact")
            self.assertEqual(value["model_calls"], 0)
            self.assertFalse(value["persisted"])
            self.assertFalse(dashboard._manual_session_root(context, create=False).exists())


class ProductBindingTests(unittest.TestCase):
    def test_partial_chart_does_not_weaken_parent_day_contract(self):
        owner, state = binding_owner()
        state["chart"].update(status="partial", fact_authority="POLICY_BOUND_RULE")
        state["chart"]["hard_facts"]["pillars"]["hour"] = None
        chart = owner.public_snapshot(RUNTIME_ID, scope="chart")
        self.assertEqual(chart["schema_version"], "1.0.0")
        self.assertEqual(chart["value"]["chart"]["status"], "partial")
        with self.assertRaises(ChartDayDashboardBindingError):
            owner.public_snapshot(RUNTIME_ID, scope="chart_day")

    def test_guard_is_atomic_and_releases_lock_on_mismatch(self):
        owner, state = binding_owner()
        before = owner.public_snapshot(RUNTIME_ID, scope="chart")
        with owner.guard_snapshot(RUNTIME_ID, before, scope="chart"):
            self.assertTrue(owner._operation_lock.locked())
            with self.assertRaises(ChartDayDashboardBindingError):
                owner.public_snapshot(RUNTIME_ID, scope="chart")
        state["state_revision"] += 1
        with self.assertRaises(ChartDayDashboardBindingError), owner.guard_snapshot(RUNTIME_ID, before, scope="chart"):
            self.fail("오래된 binding을 승인했습니다.")
        self.assertFalse(owner._operation_lock.locked())


if __name__ == "__main__":
    unittest.main()
