# system_context_contracts.py - 진단 계약·공개 경계·불변 파일·실행 identity를 fail-closed로 검증한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import stat
import subprocess
import uuid
from pathlib import Path

from scripts.evaluation.system_context_cases import FROZEN_DATE, STRATA, cases
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.training import phase5_dashboard_v1_15 as dashboard

CONFIG = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/system-context-diagnosis-v1.0.0.json"
)
RUN_ROOT = REPO_ROOT / "runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67"
SERVICE = "saju-mix2k-r16-dashboard-v1-14.service"
RAW_ROOT = REPO_ROOT / "runs/SYSTEM-CONTEXT-DIAGNOSIS/v1.0.0"
PUBLIC_ROOT = (
    REPO_ROOT / "data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0"
)
BUILD_RE = re.compile(r"build-[0-9a-f]{12}")
PUBLIC_NAMES = {"aggregate.json", "build_manifest.json", "verification.json"}


def safe_path(path):
    path = Path(path)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or any(part.is_symlink() for part in (path, *path.parents))
    ):
        raise ValueError("symlink/상대/경로 이탈을 허용하지 않습니다.")
    return path


def file_sha(path):
    path = safe_path(path)
    if not path.is_file():
        raise ValueError("검증 대상 일반 파일이 없습니다.")
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 중복 key")
        value[key] = item
    return value


def read_json(path, *, private=False, max_bytes=16 * 1024 * 1024):
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise ValueError("JSON 일반 파일/크기 계약 위반")
    if private and (stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()):
        raise ValueError("private 파일 권한/소유권 계약 위반")
    return json.loads(
        path.read_bytes(),
        object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("비유한 JSON")),
    )


def private_directory(path):
    path = safe_path(path)
    if not path.exists():
        private_directory(path.parent) if not path.parent.exists() else None
        path.mkdir(mode=0o700)
    if (
        not path.is_dir()
        or path.stat().st_uid != os.getuid()
        or stat.S_IMODE(path.stat().st_mode) != 0o700
    ):
        raise ValueError("private 디렉터리 권한/소유권 계약 위반")
    return path


def write_new(path, value, *, private=True):
    """임시 완성 파일을 배타적 link로 게시한다. 기존 결과는 절대 덮어쓰지 않는다."""
    path = safe_path(path)
    safe_path(path.parent)
    if private:
        private_directory(path.parent)
    elif not path.parent.is_dir():
        raise ValueError("공개 디렉터리를 먼저 만들어야 합니다.")
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode()
    temporary = path.parent / f".partial-{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600 if private else 0o644,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def validate_contract():
    config = read_json(CONFIG)
    expected = {
        "schema_version": "1.0.0",
        "experiment_id": "saju-system-context-diagnosis-v1.0.0",
        "frozen_server_kst_date": FROZEN_DATE.isoformat(),
        "seed": 20260915,
        "stages": ["S0", "S1", "S2"],
        "engines": ["k0_instruct", "lora_r16", "ki20_final"],
        "primary_arms": ["C_FULL", "C_MIN"],
        "auxiliary_arms": ["C_PAD", "C_POS_FRONT", "C_POS_MIDDLE", "C_POS_END"],
        "case_count": 48,
        "strata_count": 8,
        "maximum_requests": 344,
        "preflight_requests": 6,
        "minimum_free_vram_mib": 12288,
        "request_timeout_seconds": 300,
        "retry_allowed": False,
        "history_truncation_allowed": False,
        "context_limit": {"input": 4096, "output": 4096, "native_minimum": 8192},
        "projection_schema": "diagnosis-task-projection-v1.0.0",
        "padding_relative_tolerance": 0.01,
        "scoring": "role-aware-contract-v1.0.0",
        "candidate_selection": "no_selection_in_S2",
        "raw_root": str(RAW_ROOT.relative_to(REPO_ROOT)),
        "public_root": str(PUBLIC_ROOT.relative_to(REPO_ROOT)),
        "parent_dashboard_config": str(dashboard.DEFAULT_CONFIG),
        "auxiliary_case_ids": [
            f"{s}-{i}" for s in ("facts", "premise") for i in range(1, 7)
        ],
        "preflight_case_ids": ["facts-1", "format-1"],
        "governance": {
            "synthetic_only": True,
            "sealed_blind_accessed": False,
            "training_performed": False,
            "service_changed": False,
            "production_promotion_allowed": False,
            "runtime_release_changed": False,
            "confirmation_24_used": False,
            "larger_model_downloaded": False,
        },
    }
    if config != expected or canonical_types(config) != canonical_types(expected):
        raise ValueError("고정 진단 계약이 변경됐습니다. 새 버전이 필요합니다.")
    specs = cases()
    if (
        len(specs) != 48
        or len({c["case_id"] for c in specs}) != 48
        or any(sum(c["stratum"] == s for c in specs) != 6 for s in STRATA)
    ):
        raise ValueError("48개/8개 층/고유 ID 계약 위반")
    parent = read_json(REPO_ROOT / config["parent_dashboard_config"])
    dashboard.validate_config(parent)
    generation = parent["model_check"]["generation"]
    if (
        generation["do_sample"]
        or generation["max_input_tokens"] != 4096
        or generation["max_new_tokens"] != 4096
    ):
        raise ValueError("P0 생성 부모가 변경됐습니다.")
    return config


def canonical_types(value):
    if isinstance(value, dict):
        return {key: canonical_types(child) for key, child in value.items()}
    if isinstance(value, list):
        return [canonical_types(child) for child in value]
    return type(value).__name__


def prepare_context():
    return dashboard.prepare_context(
        REPO_ROOT, dashboard.DEFAULT_CONFIG, RUN_ROOT, artifact_root=REPO_ROOT
    )


def verify_model_artifacts(context):
    verified = {}
    for engine_id, engine in context["inference_engines"]["engines"].items():
        if engine["kind"] == "lora_adapter":
            dashboard._validate_lora_adapter_artifacts(context, engine)
        required = dict(engine.get("required_file_sha256", {}))
        required[
            "adapter_model.safetensors"
            if engine["kind"] == "lora_adapter"
            else "model.safetensors"
        ] = engine["model_sha256"]
        actual = {name: file_sha(engine["resolved_path"] / name) for name in required}
        if actual != required:
            raise ValueError(f"동결 모델 artifact hash가 다릅니다: {engine_id}")
        verified[engine_id] = actual
    return verified


def code_fingerprint():
    # runner뿐 아니라 동적으로 로드될 계산·앱·데이터 계약도 결합한다.
    listed = subprocess.run(
        ["git", "ls-files", "scripts", "configs"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    paths = (
        set(listed)
        | {
            str(p.relative_to(REPO_ROOT))
            for p in (REPO_ROOT / "scripts/evaluation").glob("system_context_*.py")
        }
        | {str(CONFIG.relative_to(REPO_ROOT))}
    )
    return {
        relative: file_sha(REPO_ROOT / relative)
        for relative in sorted(paths)
        if (REPO_ROOT / relative).is_file()
    }


def service_observation():
    fields = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            SERVICE,
            "-p",
            "ActiveState",
            "-p",
            "MainPID",
            "-p",
            "NRestarts",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    result = dict(line.split("=", 1) for line in fields.splitlines() if "=" in line)
    pid = int(result["MainPID"])
    result["MainPID"] = pid
    result["NRestarts"] = int(result["NRestarts"])
    if pid:
        cwd = Path(f"/proc/{pid}/cwd").resolve(strict=True)
        result["code_commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        result["candidate_code_same"] = cwd == REPO_ROOT
    return result


def training_inventory(context, resolved_cases):
    split = context["config"]["dataset_browser"]["splits"]["mix2k_v4_train"]
    path = REPO_ROOT / split["path"]
    if (
        file_sha(path) != split["sha256"]
        or file_sha(REPO_ROOT / split["manifest_path"]) != split["manifest_sha256"]
    ):
        raise ValueError("승인 2K 학습 부모 해시 불일치")
    # 허용된 fallback 2K만 읽고 집계한다. KI20 제한 원문·과거 진단 raw는 열지 않는다.
    rows = [
        json.loads(line, object_pairs_hook=_unique)
        for line in path.read_text().splitlines()
    ]
    if len(rows) != 2000:
        raise ValueError("승인 학습 행 수 불일치")
    prompts = {
        re.sub(r"\s+", "", turn["content"])
        for row in rows
        for turn in row["messages"]
        if turn["role"] == "user"
    }
    duplicate = [
        case["case_id"]
        for case in resolved_cases
        if re.sub(r"\s+", "", case["prompt"]) in prompts
    ]
    exposed = (REPO_ROOT / "SAJU_CHAT_TEST_PROMPTS.md").read_text()
    old_duplicate = [
        case["case_id"] for case in resolved_cases if case["prompt"] in exposed
    ]
    if duplicate or old_duplicate:
        raise ValueError("기존 공개 질문/학습 질문의 정확 중복 발견")
    from collections import Counter

    axes = Counter(row["task_axis"] for row in rows)
    answers = [
        turn["content"]
        for row in rows
        for turn in row["messages"]
        if turn["role"] == "assistant"
    ]
    return {
        "source": "approved_fallback_2k_only",
        "sha256": split["sha256"],
        "rows": len(rows),
        "axes": dict(axes),
        "assistant_turns_including_frozen_parents": len(answers),
        "answers_three_or_more_lines": sum(
            len([line for line in text.splitlines() if line.strip()]) >= 3
            for text in answers
        ),
        "final_answers_three_or_more_lines": sum(
            len(
                [
                    line
                    for line in row["messages"][-1]["content"].splitlines()
                    if line.strip()
                ]
            )
            >= 3
            for row in rows
        ),
        "exact_training_prompt_overlap": len(duplicate),
        "exact_exposed20_prompt_overlap": len(old_duplicate),
        "semantic_overlap": "not_measured",
        "confirmation_families": "S6_reserved_not_created",
        "other_training_parents": "manifest_only_no_restricted_payload_access",
    }


def environment_identity():
    result = {}
    for name in (
        "torch",
        "transformers",
        "peft",
        "tokenizers",
        "skyfield",
        "safetensors",
    ):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def validate_public(value):
    serialized = json.dumps(value, ensure_ascii=False)
    forbidden = re.compile(r"/home/|/tmp/|(?:sbi2|sc2|scs2|scr2|sif2)_[0-9a-f]{64}")
    if forbidden.search(serialized):
        raise ValueError("공개 파일에 private 경로/식별자가 있습니다.")
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if {
                "messages",
                "output",
                "raw_output",
                "input_token_ids",
                "birth_date",
                "birth_time",
                "capability_sha256",
                "value",
                "binding",
            } & set(item):
                raise ValueError("공개 파일에 금지된 원시 필드가 있습니다.")
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and (
            item.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", item)
        ):
            raise ValueError("공개 파일에 절대 로컬 경로가 있습니다.")


def build_path(build, *, public=False):
    if not isinstance(build, str) or not BUILD_RE.fullmatch(build):
        raise ValueError("build ID가 올바르지 않습니다.")
    return safe_path((PUBLIC_ROOT if public else RAW_ROOT) / build)
