# system_context_diagnosis.py - S0/S1 CPU 동결과 최대 344요청 S2 진단·안전 재개·공개 검증 CLI다.

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from scripts.evaluation.system_context_cases import cases, digest, runtime_fixtures
from scripts.evaluation.system_context_contracts import (
    CONFIG,
    PUBLIC_NAMES,
    PUBLIC_ROOT,
    RAW_ROOT,
    REPO_ROOT,
    build_path,
    code_fingerprint,
    environment_identity,
    file_sha,
    prepare_context,
    private_directory,
    read_json,
    service_observation,
    training_inventory,
    validate_contract,
    validate_public,
    verify_model_artifacts,
    write_new,
)
from scripts.evaluation.system_context_projection import render, resolve_case
from scripts.evaluation.system_context_scoring import aggregate, score
from scripts.training.dashboard_tokenizer_v1 import (
    K0_RELATIVE,
    load_canonical_tokenizer,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def schedule(config):
    entries = []
    for engine in config["engines"]:
        for case_id in config["preflight_case_ids"]:
            entries.append(
                {
                    "stage": "preflight",
                    "engine": engine,
                    "case_id": case_id,
                    "arm": "C_FULL",
                }
            )
    for engine in config["engines"]:
        for case in cases():
            for arm in config["primary_arms"]:
                entries.append(
                    {
                        "stage": "primary",
                        "engine": engine,
                        "case_id": case["case_id"],
                        "arm": arm,
                    }
                )
        if engine == "lora_r16":
            for case_id in config["auxiliary_case_ids"]:
                for arm in config["auxiliary_arms"]:
                    entries.append(
                        {
                            "stage": "auxiliary",
                            "engine": engine,
                            "case_id": case_id,
                            "arm": arm,
                        }
                    )
    for index, entry in enumerate(entries):
        entry["request_id"] = f"request-{index + 1:03d}"
    if len(entries) != 342 or len(entries) > config["maximum_requests"]:
        raise ValueError("요청 예산 계약 위반")
    return entries


def _stable_case(case):
    value = deepcopy(case)
    if value["binding"]:
        del value["binding"]["capability_sha256"]
    return value


def prepare():
    config = validate_contract()
    context = prepare_context()
    model_files = verify_model_artifacts(context)
    fixtures = runtime_fixtures()
    resolved = {spec["case_id"]: resolve_case(spec, fixtures) for spec in cases()}
    try:
        tokenizer = load_canonical_tokenizer(REPO_ROOT / K0_RELATIVE)
    except ImportError as exc:
        raise ValueError(
            "CPU token 재구성에는 기존 .venv/bin/python 환경을 사용하세요."
        ) from exc
    inputs = {}
    requests = []
    for entry in schedule(config):
        key = (entry["case_id"], entry["arm"])
        case = resolved[entry["case_id"]]
        if key not in inputs:
            inputs[key] = render(case, entry["arm"], context, tokenizer)
        requests.append(
            {
                **entry,
                "case": case,
                "render": inputs[key],
                "parent_sha256": digest(case["history"]),
            }
        )
    registry = deepcopy(context["config"]["inference_engines"])
    identity = {
        "config_sha256": file_sha(CONFIG),
        "code_sha256": code_fingerprint(),
        "cases_sha256": digest([_stable_case(case) for case in resolved.values()]),
        "input_sha256": digest(
            [
                {
                    **entry,
                    "tokens": inputs[(entry["case_id"], entry["arm"])][
                        "input_token_ids_sha256"
                    ],
                }
                for entry in schedule(config)
            ]
        ),
        "model_registry_sha256": digest(registry),
        "verified_model_files": model_files,
        "generation": context["config"]["model_check"]["generation"],
        "environment": environment_identity(),
        "frozen_server_kst_date": config["frozen_server_kst_date"],
    }
    build = "build-" + digest(identity)[:12]
    inventory = training_inventory(context, list(resolved.values()))
    return {
        "build_id": build,
        "identity": identity,
        "config": config,
        "requests": requests,
        "training_inventory": inventory,
        "model_registry": registry,
        "expected_blocks": sum(
            bool(request["case"]["expected_block"]) for request in requests
        ),
        "maximum_input_tokens": max(
            request["render"]["input_tokens"] for request in requests
        ),
    }


def gpu_ready():
    from scripts.training.phase5_dashboard_v1_15 import _gpu_snapshot

    gpu = _gpu_snapshot()
    if not gpu.get("available"):
        raise ValueError("GPU 상태 조회 실패")
    compute = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return not compute.stdout.strip() and gpu["total_mib"] - gpu["used_mib"] >= 12288


def _entry(request, response):
    result = {
        key: request[key]
        for key in ("stage", "engine", "case_id", "arm", "request_id", "parent_sha256")
    }
    result.update(
        {
            "stratum": request["case"]["stratum"],
            "input_tokens": request["render"]["input_tokens"],
            "status": response["status"],
            "request_sha256": digest(request),
            "response_sha256": digest(response),
        }
    )
    if response["status"] == "generated":
        result["scoring"] = score(
            request["case"],
            response["output"],
            stop_reason=response["telemetry"]["stop_reason"],
        )
        result["telemetry"] = response["telemetry"]
        result["consumers"] = response["consumers"]
    else:
        result["reason"] = response.get("reason")
    return result


def validate_response(request, response):
    if response.get("request_sha256") != digest(request):
        raise ValueError("응답/요청 fingerprint 불일치")
    status = response.get("status")
    if status not in {"generated", "preblocked", "error", "indeterminate"}:
        raise ValueError("알 수 없는 실행 상태")
    if request["case"]["expected_block"]:
        if (
            status != "preblocked"
            or response.get("reason") != request["case"]["expected_block"]
        ):
            raise ValueError("예상 차단 결과가 다릅니다.")
    elif status == "preblocked":
        raise ValueError("예상하지 않은 사전 차단")
    if status == "generated":
        if (
            response["generated"]["input_token_ids_sha256"]
            != request["render"]["input_token_ids_sha256"]
            or response["generated"]["omitted_messages"]
        ):
            raise ValueError("완료 응답의 입력 identity 불일치")
        if (
            response["output"] != response["generated"]["output"]
            or not response["output"]
        ):
            raise ValueError("완료 응답 원문 불일치")
        consumers = response["consumers"]
        if any(
            consumers[key] != digest(response["output"])
            for key in ("raw_sha256", "api_display_sha256", "stored_sha256")
        ):
            raise ValueError("완료 응답 저장/표시 hash 불일치")


def _start_worker(root, request):
    request_id = request["request_id"]
    input_path, response_path = (
        root / f"{request_id}.input.json",
        root / f"{request_id}.response.json",
    )
    start_path = root / f"{request_id}.started.json"
    if start_path.exists():
        raise ValueError("시작됐으나 미완료인 요청은 자동 재생성하지 않습니다.")
    from scripts.evaluation.dashboard_v115_replay import header_check
    from scripts.training.mix2k_v4_lora import (
        Mix2KV4LoRAError,
        acquire_mix2k_v4_gpu_lock,
    )

    header_check()
    while True:
        descriptor = None
        try:
            descriptor = acquire_mix2k_v4_gpu_lock(REPO_ROOT)
            if gpu_ready():
                break
        except Mix2KV4LoRAError:
            pass
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        if descriptor is not None:
            os.close(descriptor)
        print(
            json.dumps({"status": "waiting_for_idle_gpu", "request_id": request_id}),
            flush=True,
        )
        time.sleep(20)
    try:
        write_new(
            start_path,
            {
                "request_sha256": digest(request),
                "started_at_utc": utc_now(),
                "parent_pid": os.getpid(),
                "retry": 0,
            },
        )
        log_path = root / f"{request_id}.worker.log"
        log_fd = os.open(
            log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        process = None
        try:
            with os.fdopen(log_fd, "wb") as logfile:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-m",
                        "scripts.evaluation.system_context_diagnosis",
                        "worker",
                        "--input",
                        str(input_path),
                        "--output",
                        str(response_path),
                    ],
                    cwd=REPO_ROOT,
                    stdout=logfile,
                    stderr=logfile,
                    start_new_session=True,
                    pass_fds=(descriptor,),
                    env={**os.environ, "SYSTEM_CONTEXT_GPU_LOCK_FD": str(descriptor)},
                )
                try:
                    return_code = process.wait(timeout=300)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    return_code = -9
        except BaseException:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            raise
        if not response_path.exists():
            write_new(
                response_path,
                {
                    "request_sha256": digest(request),
                    "status": "error",
                    "reason": "timeout" if return_code == -9 else "worker_failed",
                    "return_code": return_code,
                    "worker_log_sha256": file_sha(log_path),
                },
            )
        response = read_json(response_path, private=True)
        if return_code != 0 and response["status"] == "generated":
            raise ValueError(
                "worker 실패 뒤 완료 파일이 남았습니다. 점검이 필요합니다."
            )
        return response
    finally:
        os.close(descriptor)


def execute(prepared, *, resume=False):
    if os.environ.get("SYSTEM_CONTEXT_DIAGNOSIS") != "S0_S1_S2_V1":
        raise ValueError("SYSTEM_CONTEXT_DIAGNOSIS=S0_S1_S2_V1 확인이 필요합니다.")
    root = build_path(prepared["build_id"])
    if root.exists() and not resume:
        raise ValueError("기존 build는 --resume으로만 검증 후 재개합니다.")
    private_directory(RAW_ROOT)
    private_directory(root)
    lock_fd = os.open(root / ".run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen_path = root / "prepared.json"
        if frozen_path.exists():
            frozen = read_json(frozen_path, private=True, max_bytes=128 * 1024 * 1024)
            if (
                frozen["identity"] != prepared["identity"]
                or frozen["build_id"] != prepared["build_id"]
            ):
                raise ValueError("재개 입력·코드·환경 identity가 변경됐습니다.")
            prepared = frozen
        else:
            if resume:
                raise ValueError("동결 manifest 없는 build는 재개할 수 없습니다.")
            prepared["service_before"] = service_observation()
            prepared["started_at_utc"] = utc_now()
            prepared["code_commit_at_start"] = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            write_new(frozen_path, prepared)
        reused = 0
        entries = []
        for request in prepared["requests"]:
            request_id = request["request_id"]
            input_path, response_path = (
                root / f"{request_id}.input.json",
                root / f"{request_id}.response.json",
            )
            if input_path.exists():
                if read_json(input_path, private=True) != request:
                    raise ValueError("기존 요청 파일 변조")
            else:
                write_new(input_path, request)
            if response_path.exists():
                response = read_json(response_path, private=True)
                reused += 1
            elif request["case"]["expected_block"]:
                response = {
                    "request_sha256": digest(request),
                    "status": "preblocked",
                    "reason": request["case"]["expected_block"],
                }
                write_new(response_path, response)
            elif (root / f"{request_id}.started.json").exists():
                response = {
                    "request_sha256": digest(request),
                    "status": "indeterminate",
                    "reason": "started_without_verified_result_no_retry",
                }
                write_new(response_path, response)
            else:
                response = _start_worker(root, request)
            validate_response(request, response)
            entries.append(_entry(request, response))
            print(
                json.dumps(
                    {
                        "build_id": prepared["build_id"],
                        "completed": len(entries),
                        "total": len(prepared["requests"]),
                        "stage": request["stage"],
                        "engine": request["engine"],
                        "arm": request["arm"],
                        "status": response["status"],
                        "seconds": response.get("telemetry", {}).get("elapsed_seconds"),
                    }
                ),
                flush=True,
            )
            if response["status"] in {"error", "indeterminate"} and not resume:
                raise ValueError(
                    f"{request_id} 실행 중단: 원인을 확인하고 같은 build를 재개하세요. 재생성은 금지됩니다."
                )
            if len(entries) == prepared["config"]["preflight_requests"]:
                if any(r["status"] != "generated" for r in entries):
                    raise ValueError(
                        "적격성 확인이 미완료라 본 비교를 시작하지 않습니다."
                    )
                elapsed = [r["telemetry"]["elapsed_seconds"] for r in entries]
                print(
                    json.dumps(
                        {
                            "status": "preflight_completed",
                            "estimated_remaining_minutes": round(
                                sum(elapsed)
                                / len(elapsed)
                                * (
                                    len(prepared["requests"])
                                    - len(entries)
                                    - prepared["expected_blocks"]
                                )
                                / 60,
                                1,
                            ),
                        }
                    ),
                    flush=True,
                )
        if service_observation() != prepared["service_before"]:
            raise ValueError("실험 도중 운영 서비스 상태가 변경됐습니다.")
        publish(prepared, entries, reused=reused)
        return verify(prepared["build_id"])
    finally:
        os.close(lock_fd)


def public_manifest(prepared, entries, summary, publication):
    build = prepared["build_id"]
    root = build_path(build)
    return {
        "schema_version": "1.0.0",
        "build_id": build,
        "identity": prepared["identity"],
        "requests": len(entries),
        "started_at_utc": prepared["started_at_utc"],
        "code_commit_at_start": prepared["code_commit_at_start"],
        "service_before": prepared["service_before"],
        "service_after": publication["service_after"],
        "model_registry": prepared["model_registry"],
        "training_inventory": prepared["training_inventory"],
        "maximum_input_tokens": prepared["maximum_input_tokens"],
        "history_omissions": 0,
        "completed_reused_at_publication": publication["reused"],
        "request_hashes": {
            r["request_id"]: {
                "input_sha256": file_sha(root / f"{r['request_id']}.input.json"),
                "response_sha256": file_sha(root / f"{r['request_id']}.response.json"),
            }
            for r in entries
        },
        "aggregate_sha256": digest(summary),
        "governance": prepared["config"]["governance"],
    }


def publish(prepared, entries, *, reused):
    build = prepared["build_id"]
    root, public = build_path(build), build_path(build, public=True)
    summary = {
        "schema_version": "1.0.0",
        "build_id": build,
        **aggregate(entries),
        "governance": prepared["config"]["governance"],
    }
    publication_path = root / "publication.json"
    if publication_path.exists():
        publication = read_json(publication_path, private=True)
    else:
        publication = {"reused": reused, "service_after": service_observation()}
        write_new(publication_path, publication)
    manifest = public_manifest(prepared, entries, summary, publication)
    for value in (summary, manifest):
        validate_public(value)
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    public.mkdir(exist_ok=True)
    for name, value in (("aggregate.json", summary), ("build_manifest.json", manifest)):
        path = public / name
        if path.exists():
            # 완료 후 재개는 기존 publication을 덮어쓰지 않는다.
            if read_json(path) != value:
                raise ValueError("불변 공개 결과가 이미 다릅니다.")
        else:
            write_new(path, value, private=False)


def verify(build):
    root, public = build_path(build), build_path(build, public=True)
    frozen = read_json(
        root / "prepared.json", private=True, max_bytes=128 * 1024 * 1024
    )
    manifest = read_json(public / "build_manifest.json")
    summary = read_json(public / "aggregate.json")
    reconstructed = prepare()
    if (
        frozen["identity"] != reconstructed["identity"]
        or frozen["config"] != reconstructed["config"]
        or len(frozen["requests"]) != len(reconstructed["requests"])
    ):
        raise ValueError(
            "실제 코드/모델/계약으로 입력 identity를 재구성할 수 없습니다."
        )
    for previous, rebuilt in zip(
        frozen["requests"], reconstructed["requests"], strict=True
    ):
        left, right = deepcopy(previous), deepcopy(rebuilt)
        left["case"], right["case"] = (
            _stable_case(left["case"]),
            _stable_case(right["case"]),
        )
        if left != right:
            raise ValueError("동결 요청의 상태/역할/최종 토큰 재구성 불일치")
    for key in (
        "model_registry",
        "training_inventory",
        "maximum_input_tokens",
        "expected_blocks",
    ):
        if frozen[key] != reconstructed[key]:
            raise ValueError("동결 기준선 metadata 재검증 불일치")
    if (
        {p.name for p in public.iterdir()} - PUBLIC_NAMES
        or frozen["identity"] != manifest["identity"]
        or "build-" + digest(frozen["identity"])[:12] != build
    ):
        raise ValueError("공개 manifest identity/파일 범위 위반")
    entries = []
    for request in frozen["requests"]:
        rid = request["request_id"]
        path = root / f"{rid}.response.json"
        if (
            file_sha(path) != manifest["request_hashes"][rid]["response_sha256"]
            or file_sha(root / f"{rid}.input.json")
            != manifest["request_hashes"][rid]["input_sha256"]
        ):
            raise ValueError("private 입력/출력 파일 변조")
        if read_json(root / f"{rid}.input.json", private=True) != request:
            raise ValueError("동결 요청과 입력 파일 불일치")
        response = read_json(path, private=True)
        validate_response(request, response)
        entries.append(_entry(request, response))
    recomputed = {
        "schema_version": "1.0.0",
        "build_id": build,
        **aggregate(entries),
        "governance": frozen["config"]["governance"],
    }
    if recomputed != summary or digest(summary) != manifest["aggregate_sha256"]:
        raise ValueError("공개 집계 재계산 불일치")
    publication = read_json(root / "publication.json", private=True)
    if (
        publication["service_after"] != frozen["service_before"]
        or not isinstance(publication["reused"], int)
        or not 0 <= publication["reused"] <= len(entries)
    ):
        raise ValueError("발행 시 서비스/재사용 기록 불일치")
    if public_manifest(frozen, entries, recomputed, publication) != manifest:
        raise ValueError("공개 manifest 전체 필드 재계산 불일치")
    for item in (manifest, summary):
        validate_public(item)
    result = {
        "schema_version": "1.0.0",
        "build_id": build,
        "status": "verified",
        "requests": len(entries),
        "aggregate_file_sha256": file_sha(public / "aggregate.json"),
        "manifest_file_sha256": file_sha(public / "build_manifest.json"),
        "raw_files_git_tracked": False,
        "governance": frozen["config"]["governance"],
    }
    tracked = subprocess.run(
        ["git", "ls-files", str(root.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if tracked.strip():
        raise ValueError("원시 진단 파일이 Git 추적 중입니다.")
    path = public / "verification.json"
    if path.exists():
        if read_json(path) != result:
            raise ValueError("기존 검증 파일이 다릅니다.")
    else:
        write_new(path, result, private=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="S0/S1/S2 동결 진단: dry-run 기본, 기존 서비스 변경 없음"
    )
    parser.add_argument(
        "command", choices=("validate-contract", "plan", "execute", "verify", "worker")
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--build")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "worker":
            from scripts.evaluation.system_context_backend import worker

            if args.input is None or args.output is None:
                raise ValueError("격리 worker 입출력 경로가 필요합니다.")
            worker(args.input, args.output)
            return 0
        config = validate_contract()
        if args.command == "validate-contract":
            result = {
                "status": "valid",
                "gpu_used": False,
                "artifact_writes": False,
                "maximum_requests": config["maximum_requests"],
            }
        elif args.command == "plan":
            result = {
                "status": "planned",
                "requests": len(schedule(config)),
                "primary": 288,
                "auxiliary": 48,
                "preflight": 6,
                "maximum_requests": 344,
                "gpu_used": False,
                "artifact_writes": False,
                "stages": config["stages"],
                "governance": config["governance"],
            }
        elif args.command == "verify":
            result = verify(args.build)
        else:
            prepared = prepare()
            if args.build and args.build != prepared["build_id"]:
                raise ValueError("요청한 build와 현재 입력 identity가 다릅니다.")
            if args.execute:
                result = execute(prepared, resume=args.resume)
            else:
                result = {
                    "status": "dry_run",
                    "build_id": prepared["build_id"],
                    "requests": len(prepared["requests"]),
                    "expected_preblocks": prepared["expected_blocks"],
                    "maximum_input_tokens": prepared["maximum_input_tokens"],
                    "training_inventory": prepared["training_inventory"],
                    "gpu_used": False,
                    "artifact_writes": False,
                }
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
