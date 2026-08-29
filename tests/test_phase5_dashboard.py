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
    Phase5DashboardError,
    _parser,
    _select_probes,
    _service_snapshot,
    checkpoints_payload,
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
            / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.1.0.json"
        )
        self.config_path.parent.mkdir(parents=True)
        self.run_root = root / "runs/KI20-MIX-v2/v1.2.0/run-123456abcdef"
        self.run_root.mkdir(parents=True)
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
        self.assertTrue(DashboardHTTPServer.allow_reuse_address)

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
        session_id = result["session_id"]
        followup = execute_manual_generation(
            self.fixture.context(), "앞 답변을 요약해줘", session_id
        )
        self.assertEqual(followup["session"]["turn_count"], 2)
        self.assertEqual(
            generate.call_args_list[1].args[1],
            [
                {"role": "user", "content": "질문입니다"},
                {"role": "assistant", "content": "첫 답변"},
                {"role": "user", "content": "앞 답변을 요약해줘"},
            ],
        )
        stored = manual_session_payload(self.fixture.context(), session_id)
        self.assertEqual(stored["messages"][-1]["content"], "후속 답변")
        self.assertEqual(manual_sessions_payload(self.fixture.context())["items"][0]["turn_count"], 2)
        self.assertFalse(result["production_promotion_allowed"])

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
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("stop-training", html + script)
        self.assertNotIn("resume-training", html + script)
        self.assertEqual(html.count("__CSRF_TOKEN__"), 1)


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
