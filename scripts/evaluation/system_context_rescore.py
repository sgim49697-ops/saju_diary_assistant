# system_context_rescore.py - 고정 S2 원문을 재생성 없이 검증·재채점하는 CPU 전용 CLI다.

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from scripts.evaluation import system_context_diagnosis as parent
from scripts.evaluation.system_context_cases import cases, digest
from scripts.evaluation.system_context_contracts import (
    REPO_ROOT,
    file_sha,
    private_directory,
    read_json,
    safe_path,
    validate_public,
    write_new,
)
from scripts.evaluation.system_context_scoring import aggregate
from scripts.evaluation.system_context_scoring_v1_1 import SCORER_VERSION, score

CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/system-context-rescore-v1.0.0.json"
)
PARENT_BUILD = "build-c39b4bce5089"
PARENT_PINS = {
    "aggregate.json": "d97979399747b4f0aae857860142ce47bf6cf5a67042955dc6392c95cadeb1ce",
    "build_manifest.json": "af74b65d2551843dcfd2d7c96b5a292da4e4e2b89c15d0894975c60b4f97ad10",
    "verification.json": "115e0dd8efa830d1b01d633b5e06090f9fe3b667d42cb7239f0332c34a6f6504",
}
GOVERNANCE = {
    "cpu_only": True,
    "new_generations": 0,
    "parent_outputs_modified": False,
    "sealed_blind_accessed": False,
    "training_performed": False,
    "service_changed": False,
    "production_promotion_allowed": False,
    "runtime_release_changed": False,
}
CODE = (
    "scripts/evaluation/system_context_rescore.py",
    "scripts/evaluation/system_context_scoring_v1_1.py",
)
PUBLIC_NAMES = {"aggregate.json", "build_manifest.json", "verification.json"}


def _same(left, right):
    # bool == int 같은 JSON type alias도 거부한다.
    return digest(left) == digest(right)


def validate_contract():
    value = read_json(REPO_ROOT / CONFIG)
    expected = {
        "schema_version": "1.0.0",
        "analysis_id": "system-context-rescore-v1.0.0",
        "scorer_version": SCORER_VERSION,
        "parent_build": PARENT_BUILD,
        "parent_public_sha256": PARENT_PINS,
        "expected_requests": 342,
        "expected_generated": 312,
        "expected_preblocked": 30,
        "expected_preflight": 6,
        "expected_source_files": 460,
        "raw_root": "runs/SYSTEM-CONTEXT-RESCORE/v1.0.0",
        "public_root": "data/reports/saju_1b_baseline/system-context-rescore/v1.0.0",
        "governance": GOVERNANCE,
    }
    if not _same(value, expected):
        raise ValueError("파생 재집계 계약/부모 pin/type이 다릅니다.")
    return value


def _relative(root, value):
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
        or Path(value).as_posix() != value
        or value == "."
    ):
        raise ValueError("상대 경로 계약 위반")
    return safe_path(root / value)


def _private_read_directory(path):
    info = safe_path(path).stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise ValueError("private 디렉터리 권한/소유권 위반")


def _not_tracked(root, path):
    tracked = subprocess.run(
        ["git", "ls-files", "--", str(path.relative_to(root))],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if tracked.strip():
        raise ValueError("원시 파생 파일이 Git 추적 중입니다.")


def verify_parent(config, *, root=REPO_ROOT):
    """부모의 선택된 source map·frozen 입력만 읽는다. 모델/현재 전체 fingerprint를 재생성하지 않는다."""
    public = (
        root
        / "data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0"
        / config["parent_build"]
    )
    raw = root / "runs/SYSTEM-CONTEXT-DIAGNOSIS/v1.0.0" / config["parent_build"]
    safe_path(public)
    _private_read_directory(raw)
    if {p.name for p in public.iterdir()} != PUBLIC_NAMES:
        raise ValueError("부모 공개 파일 범위 위반")
    for name, pin in config["parent_public_sha256"].items():
        if file_sha(public / name) != pin:
            raise ValueError("부모 공개 파일 hash 불일치")
    manifest = read_json(public / "build_manifest.json")
    summary = read_json(public / "aggregate.json")
    verification = read_json(public / "verification.json")
    for value in (manifest, summary, verification):
        validate_public(value)
    frozen = read_json(raw / "prepared.json", private=True, max_bytes=128 * 1024 * 1024)
    identity = manifest["identity"]
    if (
        "build-" + digest(identity)[:12] != config["parent_build"]
        or not _same(frozen["identity"], identity)
        or len(identity["code_sha256"]) != config["expected_source_files"]
        or verification["status"] != "verified"
        or verification["aggregate_file_sha256"] != file_sha(public / "aggregate.json")
        or verification["manifest_file_sha256"]
        != file_sha(public / "build_manifest.json")
    ):
        raise ValueError("부모 identity/검증 기록 불일치")
    for relative, pin in identity["code_sha256"].items():
        if (
            Path(relative).parts[0] not in {"scripts", "configs"}
            or file_sha(_relative(root, relative)) != pin
        ):
            raise ValueError("부모에 기록된 source 파일 hash 불일치")
    original_config = read_json(
        root
        / "configs/model_versions/saju_1b_baseline/system-context-diagnosis-v1.0.0.json"
    )
    if (
        not _same(frozen["config"], original_config)
        or file_sha(
            root
            / "configs/model_versions/saju_1b_baseline/system-context-diagnosis-v1.0.0.json"
        )
        != identity["config_sha256"]
    ):
        raise ValueError("부모 config 불일치")
    requests = frozen["requests"]
    scheduled = parent.schedule(original_config)
    if len(requests) != config["expected_requests"] or len(requests) != len(scheduled):
        raise ValueError("부모 요청 수 불일치")
    if set(manifest["request_hashes"]) != {r["request_id"] for r in scheduled}:
        raise ValueError("부모 요청 ID 누락/추가")
    entries, responses, actual_hashes, seen_cases, input_identity = [], [], {}, {}, []
    for request, registered in zip(requests, scheduled, strict=True):
        if any(request.get(k) != v for k, v in registered.items()):
            raise ValueError("부모 요청 순서/등록 조건 불일치")
        rid = registered["request_id"]
        pins = manifest["request_hashes"][rid]
        hashes = {
            "input_sha256": file_sha(raw / f"{rid}.input.json"),
            "response_sha256": file_sha(raw / f"{rid}.response.json"),
        }
        if hashes != pins or not _same(
            read_json(raw / f"{rid}.input.json", private=True), request
        ):
            raise ValueError("부모 입력/응답 hash 또는 frozen 입력 불일치")
        response = read_json(raw / f"{rid}.response.json", private=True)
        parent.validate_response(request, response)
        if response["status"] not in {"generated", "preblocked"}:
            raise ValueError("미완료/오류 부모는 재채점할 수 없습니다.")
        render = request["render"]
        if (
            render["input_tokens"] != len(render["input_token_ids"])
            or digest(render["input_token_ids"]) != render["input_token_ids_sha256"]
            or render["omitted_messages"] != 0
            or request["parent_sha256"] != digest(request["case"]["history"])
        ):
            raise ValueError("부모 최종 token/이력 hash 불일치")
        normalized = parent._stable_case(request["case"])
        prior = seen_cases.setdefault(request["case_id"], normalized)
        if not _same(prior, normalized):
            raise ValueError("같은 사례의 동결 상태가 다릅니다.")
        input_identity.append(
            {**registered, "tokens": render["input_token_ids_sha256"]}
        )
        actual_hashes[rid] = hashes
        entries.append(parent._entry(request, response))
        responses.append(response)
    if (
        digest(input_identity) != identity["input_sha256"]
        or digest([seen_cases[c["case_id"]] for c in cases()])
        != identity["cases_sha256"]
    ):
        raise ValueError("부모 사례/최종 입력 identity 불일치")
    recomputed = {
        "schema_version": "1.0.0",
        "build_id": config["parent_build"],
        **aggregate(entries),
        "governance": original_config["governance"],
    }
    if (
        not _same(recomputed, summary)
        or digest(summary) != manifest["aggregate_sha256"]
    ):
        raise ValueError("부모 v1.0 점수 재계산 불일치")
    publication = read_json(raw / "publication.json", private=True)
    expected_manifest = {
        "schema_version": "1.0.0",
        "build_id": config["parent_build"],
        "identity": identity,
        "requests": len(requests),
        "started_at_utc": frozen["started_at_utc"],
        "code_commit_at_start": frozen["code_commit_at_start"],
        "service_before": frozen["service_before"],
        "service_after": publication["service_after"],
        "model_registry": frozen["model_registry"],
        "training_inventory": frozen["training_inventory"],
        "maximum_input_tokens": frozen["maximum_input_tokens"],
        "history_omissions": 0,
        "completed_reused_at_publication": publication["reused"],
        "request_hashes": actual_hashes,
        "aggregate_sha256": digest(summary),
        "governance": original_config["governance"],
    }
    if (
        not _same(expected_manifest, manifest)
        or digest(frozen["model_registry"]) != identity["model_registry_sha256"]
    ):
        raise ValueError("부모 manifest metadata 불일치")
    if (
        summary["statuses"]
        != {
            "generated": config["expected_generated"],
            "preblocked": config["expected_preblocked"],
        }
        or summary["preflight_requests"] != config["expected_preflight"]
    ):
        raise ValueError("부모 생성/차단 분모 불일치")
    _not_tracked(root, raw)
    return {
        "frozen": frozen,
        "responses": responses,
        "entries": entries,
        "summary": summary,
    }


def _transitions(before, after):
    counts = defaultdict(Counter)
    for left, right in zip(before, after, strict=True):
        if left["stage"] == "preflight" or left["status"] != "generated":
            continue
        a, b = left["scoring"]["metrics"], right["scoring"]["metrics"]
        if set(a) != set(b):
            raise ValueError("지표 적용 분모가 달라졌습니다.")
        for metric in sorted(a):
            for stratum in ("all", left["stratum"]):
                counts[(left["engine"], left["arm"], stratum, metric)][
                    a[metric], b[metric]
                ] += 1
    return [
        {
            "engine": e,
            "arm": a,
            "stratum": s,
            "metric": m,
            "counts": {
                f"{x}_to_{y}": count[x, y]
                for x in ("PASS", "FAIL", "UNSCORABLE")
                for y in ("PASS", "FAIL", "UNSCORABLE")
            },
        }
        for (e, a, s, m), count in sorted(counts.items())
    ]


def derive(config, bundle, *, root=REPO_ROOT):
    fingerprint = {name: file_sha(root / name) for name in (*CODE, str(CONFIG))}
    identity = {
        "analysis_id": config["analysis_id"],
        "parent_build": config["parent_build"],
        "parent_public_sha256": config["parent_public_sha256"],
        "scorer_version": SCORER_VERSION,
        "code_sha256": fingerprint,
        "python": list(sys.version_info[:3]),
    }
    build = "build-" + digest(identity)[:12]
    entries, traces = [], []
    for request, response, old_entry in zip(
        bundle["frozen"]["requests"],
        bundle["responses"],
        bundle["entries"],
        strict=True,
    ):
        entry = deepcopy(old_entry)
        if response["status"] == "generated":
            entry["scoring"] = score(
                request["case"],
                response["output"],
                stop_reason=response["telemetry"]["stop_reason"],
            )
        entries.append(entry)
        traces.append(
            {
                "request_id": request["request_id"],
                "parent_response_sha256": old_entry["response_sha256"],
                "status": response["status"],
                "old": old_entry.get("scoring"),
                "new": entry.get("scoring"),
            }
        )
    summary = {
        "schema_version": "1.0.0",
        "build_id": build,
        "parent_build": config["parent_build"],
        "scorer_version": SCORER_VERSION,
        **aggregate(entries),
        "old_to_new_transitions": _transitions(bundle["entries"], entries),
        "governance": config["governance"],
    }
    summary["limitations"] += [
        "같은 원응답의 검사 버전 차이이며 모델 성능 개선이나 새 생성 결과가 아니다.",
        "간접 표현·복잡한 인용의 의미 전체를 판정하는 검사기가 아니다.",
        "부모의 사전 차단 30개는 유지한다. 새 앱 정책의 허용 여부와 섞지 않는다.",
    ]
    subset = set(bundle["frozen"]["config"]["auxiliary_case_ids"])
    matched = [
        r
        for r in entries
        if r["engine"] == "lora_r16"
        and r["case_id"] in subset
        and r["stage"] != "preflight"
    ]
    summary["matched_auxiliary_subset"] = aggregate(matched)
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build,
        "identity": identity,
        "requests": len(entries),
        "generated_rescored": sum(r["status"] == "generated" for r in entries),
        "preflight_excluded_from_comparisons": config["expected_preflight"],
        "aggregate_sha256": digest(summary),
        "private_trace_sha256": digest(traces),
        "governance": config["governance"],
    }
    private_bytes = (
        json.dumps(
            {"identity": identity, "requests": traces},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    manifest["private_file_sha256"] = hashlib.sha256(private_bytes).hexdigest()
    validate_public(summary)
    validate_public(manifest)
    return summary, manifest, traces


def _build_path(root, relative, build):
    if not isinstance(build, str) or re.fullmatch(r"build-[0-9a-f]{12}", build) is None:
        raise ValueError("build ID 계약 위반")
    return _relative(root, relative) / build


def execute(config, *, root=REPO_ROOT):
    bundle = verify_parent(config, root=root)
    summary, manifest, traces = derive(config, bundle, root=root)
    build = summary["build_id"]
    raw = _build_path(root, config["raw_root"], build)
    public = _build_path(root, config["public_root"], build)
    private_directory(raw)
    lock = os.open(raw / ".publish.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private_value = {"identity": manifest["identity"], "requests": traces}
        private_path = raw / "scores.json"
        if private_path.exists():
            if not _same(read_json(private_path, private=True), private_value):
                raise ValueError("동일 build의 private 결과가 다릅니다.")
        else:
            write_new(private_path, private_value)
        safe_path(public).mkdir(parents=True, exist_ok=True)
        if {p.name for p in public.iterdir()} - PUBLIC_NAMES:
            raise ValueError("파생 공개 파일 범위 위반")
        for name, value in (
            ("aggregate.json", summary),
            ("build_manifest.json", manifest),
        ):
            path = public / name
            if path.exists():
                if not _same(read_json(path), value):
                    raise ValueError("동일 build의 공개 결과가 다릅니다.")
            else:
                write_new(path, value, private=False)
        return verify(config, build, root=root)
    finally:
        os.close(lock)


def verify(config, build, *, root=REPO_ROOT):
    summary, manifest, traces = derive(
        config, verify_parent(config, root=root), root=root
    )
    if build != summary["build_id"]:
        raise ValueError("파생 build identity 불일치")
    public = _build_path(root, config["public_root"], build)
    raw = _build_path(root, config["raw_root"], build)
    _private_read_directory(raw)
    _not_tracked(root, raw)
    if {p.name for p in public.iterdir()} - PUBLIC_NAMES:
        raise ValueError("파생 공개 파일 범위 위반")
    for name, value in (("aggregate.json", summary), ("build_manifest.json", manifest)):
        if not _same(read_json(public / name), value):
            raise ValueError("파생 공개 결과 재계산 불일치")
    expected_private = {"identity": manifest["identity"], "requests": traces}
    if file_sha(raw / "scores.json") != manifest["private_file_sha256"] or not _same(
        read_json(raw / "scores.json", private=True), expected_private
    ):
        raise ValueError("private 파생 trace 재계산 불일치")
    result = {
        "schema_version": "1.0.0",
        "build_id": build,
        "parent_build": config["parent_build"],
        "status": "verified",
        "requests": len(traces),
        "aggregate_file_sha256": file_sha(public / "aggregate.json"),
        "manifest_file_sha256": file_sha(public / "build_manifest.json"),
        "raw_files_git_tracked": False,
        "governance": config["governance"],
    }
    path = public / "verification.json"
    if path.exists():
        if not _same(read_json(path), result):
            raise ValueError("파생 verification 불일치")
    else:
        write_new(path, result, private=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="S2 원문 CPU 재집계: 기본 dry-run, 모델/GPU 호출 없음"
    )
    parser.add_argument(
        "command",
        choices=("validate-contract", "plan", "verify-parent", "execute", "verify"),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--build")
    args = parser.parse_args(argv)
    try:
        config = validate_contract()
        if args.command in {"validate-contract", "plan"}:
            result = {
                "status": "valid" if args.command == "validate-contract" else "planned",
                "requests": 342,
                "generated_to_rescore": 312,
                "preblocked_unchanged": 30,
                "artifact_writes": False,
                "governance": GOVERNANCE,
            }
        elif args.command == "verify-parent":
            verify_parent(config)
            result = {
                "status": "verified",
                "parent_build": PARENT_BUILD,
                "source_files": 460,
                "input_response_files": 684,
                "artifact_writes": False,
                "governance": GOVERNANCE,
            }
        elif args.command == "verify":
            result = verify(config, args.build)
        elif args.execute:
            result = execute(config)
        else:
            # 실제 새 점수는 발행 단계까지 보지 않는다. 부모 재계산만 수행한다.
            verify_parent(config)
            result = {
                "status": "dry_run",
                "requests": 342,
                "generated_to_rescore": 312,
                "artifact_writes": False,
                "governance": GOVERNANCE,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(exc).__name__},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
