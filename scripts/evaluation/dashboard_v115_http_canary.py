# dashboard_v115_http_canary.py - 임시 loopback에서 현재 날짜 원국·일진 연결과 R16 생성 경계를 검증한다.

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.dashboard_v115_replay import header_check, sha, write_new
from scripts.runtime.chart_day_dashboard_binding import ChartDayDashboardBinding
from scripts.runtime.chart_only_security import create_secret_key
from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_grounding_v2 import kst_today
from scripts.training.mix2k_v4_lora import _compute_processes, _gpu_snapshot


def run(artifact_root: Path, output: Path):
    previous_umask = os.umask(0o077)
    try:
        return _run_private(artifact_root, output)
    finally:
        os.umask(previous_umask)


def _run_private(artifact_root: Path, output: Path):
    if not output.is_absolute() or any(
        p.is_symlink() for p in (output, *output.parents)
    ):
        raise ValueError("출력은 symlink 없는 절대 경로여야 합니다.")
    output.resolve().relative_to(artifact_root.resolve() / "runs/REALISTIC-CHAT")
    header_check()
    if _compute_processes() or _gpu_snapshot()["free_mib"] < 12 * 1024:
        raise ValueError("GPU compute 유휴·12GiB 이상 여유가 필요합니다.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    keys = output / "keys"
    keys.mkdir(mode=0o700)
    hmac = create_secret_key(keys / "hmac.key", purpose="runtime-hmac")
    encryption = create_secret_key(keys / "session.key", purpose="session-aead")
    store = output / "runtime_sessions"
    store.mkdir(mode=0o700)
    context = dashboard.prepare_context(
        REPO_ROOT,
        dashboard.DEFAULT_CONFIG,
        artifact_root / "runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67",
        artifact_root=artifact_root,
    )
    context["chart_only_runtime_active"] = True
    binding = ChartDayDashboardBinding(
        release_registry=REPO_ROOT
        / context["config"]["chart_only_runtime"]["release_registry"],
        ephemeris_path=artifact_root
        / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp",
        hmac_key_file=hmac.path,
        encryption_key_file=encryption.path,
        store_root=store,
        process_lease_file=output / "runtime.lease",
    )
    calls = []

    def generate(*args):
        calls.append(True)
        return dashboard._manual_generation_subprocess(*args)

    server = dashboard.DashboardHTTPServer(
        ("127.0.0.1", 0),
        context,
        dashboard.V115_ASSET_ROOT,
        secrets.token_hex(32),
        chart_only_binding=binding,
        chart_only_runtime_requested=True,
        generation_runner=generate,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    runtime_id = None
    report = {
        "schema_version": "1.0.0",
        "diagnostic": "dashboard-v115-current-day-http",
        "synthetic_only": True,
        "clock_override_used": False,
        "feature_default": False,
        "existing_service_changed": False,
        "sealed_blind_accessed": False,
        "production_promotion_allowed": False,
    }
    private = []
    try:
        with urllib.request.urlopen(base, timeout=10) as response:
            html = response.read().decode()
        token = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
        if "v1.15 날짜·사실 검사 후보" not in html:
            raise ValueError("후보 HTML 버전이 다릅니다.")

        def api(path, payload=None, method=None, expected_status=200):
            nonlocal runtime_id
            request = urllib.request.Request(
                base + path,
                data=json.dumps(payload, ensure_ascii=False).encode()
                if payload is not None
                else None,
                method=method,
                headers={
                    "Origin": base,
                    "X-CSRF-Token": token,
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=650) as response:
                    status, value = response.status, json.load(response)
            except urllib.error.HTTPError as exc:
                status, value = exc.code, json.load(exc)
            private.append({"status": status, "response": value})
            if path == "/api/runtime/sessions" and 200 <= status < 300:
                runtime_id = value.get("session_id")
            if status != expected_status:
                raise ValueError(
                    f"HTTP canary 상태 불일치: {status}, {value.get('code')}"
                )
            return value

        for route in (
            "/api/status",
            "/api/metrics",
            "/api/checkpoints",
            "/api/model-checks",
            "/api/sessions",
            "/api/dataset-splits",
        ):
            api(route)
        report["ui_metadata_routes_ok"] = True
        runtime = api("/api/runtime/sessions", {}, expected_status=201)
        runtime_id = runtime["session_id"]
        current_date = api("/api/runtime/status")["single_day_today_kst"]
        if current_date != kst_today().isoformat():
            raise ValueError("Runtime과 서버 KST 날짜가 다릅니다.")
        events = [
            {"type": "opt_in", "accepted": True},
            {"type": "set_slot", "field": "calendar", "value": "solar"},
            {"type": "set_slot", "field": "birth_date", "value": "1990-01-01"},
            {
                "type": "set_slot",
                "field": "birthplace",
                "value": {
                    "country_code": "KR",
                    "city": "서울",
                    "timezone": "Asia/Seoul",
                },
            },
            {"type": "set_slot", "field": "birth_time", "value": "12:00"},
            {"type": "request_chart"},
            {
                "type": "request_period",
                "request": {
                    "period_type": "day",
                    "start_date": current_date,
                    "end_date": current_date,
                    "timezone": "Asia/Seoul",
                },
            },
        ]
        for event in events:
            runtime = api(
                f"/api/runtime/sessions/{runtime_id}/events",
                {"expected_revision": runtime["state_revision"], "event": event},
            )
        if (
            runtime["status"] != "ready"
            or runtime["result"]["period"]["hard_facts"]["period"]["target_date"]
            != current_date
        ):
            raise ValueError("현재 날짜 일진 연결이 준비되지 않았습니다.")
        response = api(
            "/api/generate",
            {
                "prompt": "내 오늘 사주 봐줄래?",
                "session_id": None,
                "engine_selection": "lora_r16",
                "runtime_session_id": runtime_id,
            },
        )
        if (
            not response["runtime_binding_applied"]
            or not response["outputs"]["lora_r16"]
        ):
            raise ValueError("R16에 실제 연결 snapshot이 전달되지 않았습니다.")
        codes = []
        for prompt, code in [
            ("내일 운세도 봐줘", "RUNTIME_DATE_REBIND_REQUIRED"),
            ("이번 주 전체 흐름", "RUNTIME_PERIOD_SCOPE_UNSUPPORTED"),
        ]:
            blocked = api(
                "/api/generate",
                {
                    "prompt": prompt,
                    "session_id": response["session_id"],
                    "runtime_session_id": runtime_id,
                },
                expected_status=409,
            )
            if blocked["code"] != code:
                raise ValueError("날짜 사전 차단 code가 다릅니다.")
            codes.append(code)
        if len(calls) != 1:
            raise ValueError("차단 요청이 생성 process를 실행했습니다.")
        diagnostic = response["contexts"]["lora_r16"]
        report.update(
            server_kst_date=current_date,
            runtime_current_day_bound=True,
            generated=1,
            pre_generation_blocks=2,
            generation_runner_calls=1,
            block_codes=codes,
            tokenizer_backend_sha256=diagnostic["tokenizer_backend_sha256"],
            input_tokens=diagnostic["input_tokens"],
            scorer_version=diagnostic["scorer_version"],
            bound_diagnostic_pass=response["grounding_gate"]["passed_by_engine"][
                "lora_r16"
            ],
            raw_output_preserved=True,
            http_canary_passed=True,
        )
    finally:
        try:
            if runtime_id is not None:
                binding.delete_session(runtime_id)
                report["synthetic_runtime_session_deleted"] = True
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            report["temporary_server_stopped"] = not thread.is_alive()
            write_new(output / "private_responses.json", private)
    write_new(output / "aggregate.json", report)
    write_new(
        output / "build_manifest.json",
        {
            "script_sha256": sha(Path(__file__)),
            "config_sha256": sha(REPO_ROOT / dashboard.DEFAULT_CONFIG),
            "private_responses_sha256": sha(output / "private_responses.json"),
            "aggregate_sha256": sha(output / "aggregate.json"),
        },
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="현재 날짜 v1.15 HTTP canary")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "writes_performed": False,
                    "expected_generations": 1,
                    "expected_pre_generation_blocks": 2,
                }
            )
        )
        return
    if os.environ.get("DASHBOARD_V115_DIAGNOSTIC") != "SYNTHETIC_30_V1":
        raise ValueError("명시적인 진단 환경변수가 필요합니다.")
    print(
        json.dumps(run(args.artifact_root, args.output), ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
