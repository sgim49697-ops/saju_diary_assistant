# phase4_common.py - Phase 4 계약, fingerprint, 원자 저장과 런타임 sysroot를 관리한다.

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.data.phase2b_verify_history import verify_historical_staging
from scripts.model.phase3_tools import (
    load_config as load_phase3_config,
)
from scripts.model.phase3_tools import (
    verify_report as verify_phase3_report,
)
from scripts.preflight.errors import Phase4Error

HASH_CHUNK_BYTES = 4 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
FULL_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError as exc:
        raise Phase4Error(f"파일 SHA-256을 계산할 수 없습니다: {path}") from exc
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4Error(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase4Error(f"{label} 최상위 값은 object여야 합니다.")
    return value


def read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase4Error(f"{label}에 빈 JSONL 행이 있습니다: {line_number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase4Error(
                        f"{label} JSONL 행이 object가 아닙니다: {line_number}"
                    )
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Phase4Error):
            raise
        raise Phase4Error(f"{label} JSONL을 읽을 수 없습니다: {path}") from exc
    return values


def validate_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Phase4Error("빈 상대경로는 허용하지 않습니다.")
    path = Path(value)
    if path.is_absolute() or value != path.as_posix() or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise Phase4Error(f"안전하지 않거나 정규화되지 않은 상대경로입니다: {value}")
    if any(any(ord(character) < 32 for character in part) for part in path.parts):
        raise Phase4Error(f"제어문자가 있는 상대경로입니다: {value}")
    return path


def resolve_repo_path(repo_root: Path, value: str) -> Path:
    relative = validate_relative_path(value)
    root = repo_root.resolve()
    result = (root / relative).resolve(strict=False)
    if not result.is_relative_to(root):
        raise Phase4Error(f"저장소 밖 상대경로는 허용하지 않습니다: {value}")
    return result


def _write_bytes_atomic(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_bytes_once(path: Path, payload: bytes, *, mode: int | None = None) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise Phase4Error(f"기존 불변 산출물과 내용이 다릅니다: {path}")
        return
    _write_bytes_atomic(path, payload, mode=mode)


def write_json_once(
    path: Path, value: Any, *, mode: int | None = None, generated_at_key: str | None = None
) -> dict[str, Any]:
    candidate = value
    if path.exists() and generated_at_key and isinstance(value, dict):
        existing = load_json(path, path.name)
        candidate = dict(value)
        candidate[generated_at_key] = existing.get(generated_at_key)
        if candidate != existing:
            raise Phase4Error(f"기존 불변 JSON과 내용이 다릅니다: {path}")
        return existing
    payload = json.dumps(
        candidate, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    write_bytes_once(path, payload, mode=mode)
    if not isinstance(candidate, dict):
        raise Phase4Error("write_json_once 반환값은 object여야 합니다.")
    return candidate


def write_jsonl_once(path: Path, values: list[dict[str, Any]], *, mode: int = PRIVATE_FILE_MODE) -> None:
    payload = b"".join(canonical_json_bytes(value) + b"\n" for value in values)
    write_bytes_once(path, payload, mode=mode)


def git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise Phase4Error("현재 Git HEAD를 확인할 수 없습니다.")
    return completed.stdout.strip()


def _implementation_paths(config: dict[str, Any]) -> list[str]:
    return [
        "scripts/preflight/errors.py",
        "scripts/preflight/phase4_common.py",
        "scripts/preflight/phase4_data.py",
        "scripts/preflight/phase4_k0.py",
        "scripts/preflight/phase4_triage.py",
        "scripts/preflight/phase4_smoke.py",
        "scripts/preflight/phase4_finalize.py",
        "scripts/preflight/phase4_verify_history.py",
        "scripts/preflight/phase4_review.py",
        "scripts/preflight/phase4_preflight.py",
        "scripts/preflight/review_assets/START_HERE.html",
        "scripts/preflight/review_assets/review.css",
        "scripts/preflight/review_assets/review.js",
        config["chat_template"]["path"],
    ]


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if config.get("schema_version") != "1.0.0":
        raise Phase4Error("Phase 4 설정 schema_version은 1.0.0이어야 합니다.")
    if config.get("canonical_plan_version") != "2.6.0":
        raise Phase4Error("Phase 4 정본 버전은 2.6.0이어야 합니다.")
    if config.get("dataset_name") != "saju_1b_baseline":
        raise Phase4Error("Phase 4 dataset_name이 다릅니다.")
    if config.get("preflight_version") != "v1.1.0" or config.get("seed") != 42:
        raise Phase4Error("Phase 4 version 또는 seed가 다릅니다.")

    parent = config.get("parent_staging")
    if not isinstance(parent, dict) or parent != {
        "version": "v0.2.0",
        "build_id": "build-847088ee804d",
        "build_sha256": "847088ee804d8bc8933c0d83767a2251fc820ae3cb4235965a9a27a3f0f34801",
        "implementation_commit": "21705fe72fefe5bd9933a9ac9cc2cf30aad08ce7",
        "approval_sha256": "3be8fa8c2d9948bcbe3c8a8367026ac814ad86e2087e6c2d32701a88e4de7c52",
    }:
        raise Phase4Error("승인된 24K staging 부모 계약이 다릅니다.")

    model = config.get("model")
    if not isinstance(model, dict):
        raise Phase4Error("Phase 4 모델 계약이 없습니다.")
    if (
        model.get("repo_id") != "kakaocorp/kanana-2-1.3b-instruct"
        or FULL_REVISION_PATTERN.fullmatch(str(model.get("revision", ""))) is None
        or model.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or model.get("phase3_build_id") != "build-32e2c84af3d3"
        or model.get("dtype") != "bfloat16"
        or model.get("attention_backend") != "sdpa"
    ):
        raise Phase4Error("Phase 4 고정 Kanana 모델 계약이 다릅니다.")
    snapshot = resolve_repo_path(repo_root, str(model.get("local_subdir", "")))
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise Phase4Error("Phase 3 고정 모델 snapshot이 없습니다.")

    template = config.get("chat_template")
    if not isinstance(template, dict):
        raise Phase4Error("Phase 4 chat template 계약이 없습니다.")
    template_path = resolve_repo_path(repo_root, str(template.get("path", "")))
    if (
        not template_path.is_file()
        or template_path.stat().st_size != template.get("bytes")
        or sha256_file(template_path) != template.get("sha256")
    ):
        raise Phase4Error("Phase 4 고정 chat template가 다릅니다.")

    runtime = config.get("runtime_headers")
    if not isinstance(runtime, dict):
        raise Phase4Error("Phase 4 runtime header 계약이 없습니다.")
    if (
        runtime.get("package") != "libpython3.10-dev"
        or runtime.get("version") != "3.10.12-1~22.04.16"
        or runtime.get("bytes") != 4_765_572
        or runtime.get("sha256")
        != "7ec59ebb7ecea34f416f37f74797712a7d477cc344f1bca1755528b154eaa04a"
        or not str(runtime.get("url", "")).startswith("https://security.ubuntu.com/")
    ):
        raise Phase4Error("Phase 4 Python header package 계약이 다릅니다.")
    resolve_repo_path(repo_root, str(runtime.get("local_root", "")))

    split = config.get("split")
    expected_axes = {
        "nemotron_saju": {"mix1k": 550, "mix10k": 5500, "mix20k": 11000, "holdout": 100},
        "bazi_sft": {"mix1k": 250, "mix10k": 2500, "mix20k": 5000, "holdout": 100},
        "aihub_empathy_single": {"mix1k": 100, "mix10k": 1000, "mix20k": 2000, "holdout": 100},
        "aihub_empathy_multiturn": {"mix1k": 50, "mix10k": 500, "mix20k": 1000, "holdout": 100},
        "yeji_shensha_derived": {"mix1k": 50, "mix10k": 500, "mix20k": 1000, "holdout": 100},
    }
    if not isinstance(split, dict) or split.get("axes") != expected_axes:
        raise Phase4Error("Phase 4 split 수량 계약이 다릅니다.")
    if split.get("token_share_policy") != "report_only_no_threshold":
        raise Phase4Error("출처별 token share는 report-only여야 합니다.")
    if (
        split.get("formal_max_length") != 768
        or split.get("diagnostic_max_length") != 1024
        or split.get("smoke_only_max_length") != 512
    ):
        raise Phase4Error("Phase 4 길이별 기술 검증 계약이 다릅니다.")

    core_eval = split.get("core_eval")
    expected_core_eval = {
        "structured_natal_reading": 45,
        "grounded_rule_reading": 35,
        "contradiction_hallucination": 35,
        "shensha_rule_qa": 20,
        "same_chart_consistency": 20,
        "empathy": 20,
        "multiturn": 15,
        "missing_chart_handoff": 5,
        "general_korean_instruction": 5,
    }
    if core_eval != expected_core_eval or sum(core_eval.values()) != 200:
        raise Phase4Error("Core Eval 200 수량 계약이 다릅니다.")

    generation = config.get("generation")
    if not isinstance(generation, dict) or generation != {
        "do_sample": False,
        "max_new_tokens": 512,
        "num_beams": 1,
        "use_cache": True,
        "batch_size": 1,
    }:
        raise Phase4Error("K0 generation 계약이 다릅니다.")

    reuse = config.get("k0_reuse")
    if (
        not isinstance(reuse, dict)
        or reuse.get("source_build_id") != "build-9cf4fdb83bbd"
        or reuse.get("source_run_manifest_sha256")
        != "d06b44548d76b311e2f8b2decf64dc253a147e8d581907101bf2cfea44f7c65f"
        or reuse.get("source_run_config_sha256")
        != "63bf2dd5053037e4c4d273f2107267dbe7e551ac20a2038033a119f16e926243"
        or reuse.get("reuse_key")
        != "model-template-generation-prompt-sha256"
        or reuse.get("recompute_metrics") is not True
    ):
        raise Phase4Error("K0 교차 build 재사용 계약이 다릅니다.")
    resolve_repo_path(repo_root, str(reuse.get("source_root", "")))

    triage = config.get("triage")
    if not isinstance(triage, dict) or triage != {
        "evaluation_items": 700,
        "generation_cases": 720,
        "priority_limit": 40,
        "severity_order": ["critical", "high", "medium", "low"],
        "human_domain_review_performed": False,
    }:
        raise Phase4Error("K0 자동 위험 분류 계약이 다릅니다.")

    smoke = config.get("training_smoke")
    expected_smoke = {
        "full_parameter_count": 1_291_478_272,
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "gradient_checkpointing": True,
        "use_cache": False,
        "optimizer": "paged_adamw_8bit",
        "assistant_only_loss": True,
        "packing": False,
        "loss_type": "chunked_nll",
        "learning_rate": 8.0e-6,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
        "seed": 42,
        "data_seed": 42,
        "minimum_vram_headroom_bytes": 1_073_741_824,
    }
    if not isinstance(smoke, dict) or any(
        smoke.get(key) != value for key, value in expected_smoke.items()
    ):
        raise Phase4Error("Phase 4D/E 학습 smoke 계약이 다릅니다.")
    stages = smoke.get("stages")
    expected_stages = {
        "gate_d_512_1": {
            "max_length": 512,
            "optimizer_steps": 1,
            "manifest": "mix1k_smoke_512_v1.jsonl",
        },
        "smoke_512_20": {
            "max_length": 512,
            "optimizer_steps": 20,
            "manifest": "mix1k_smoke_512_v1.jsonl",
        },
        "diagnostic_1024_1": {
            "max_length": 1024,
            "optimizer_steps": 1,
            "manifest": "mix1k_candidate_v1.jsonl",
        },
        "main_768_100": {
            "max_length": 768,
            "optimizer_steps": 100,
            "total_optimizer_steps": 200,
            "checkpoint_step": 100,
            "manifest": "mix1k_candidate_v1.jsonl",
        },
        "resume_768_200": {
            "max_length": 768,
            "optimizer_steps": 200,
            "resume_step": 100,
            "checkpoint_step": 200,
            "manifest": "mix1k_candidate_v1.jsonl",
        },
        "reload_768_generate5": {
            "max_length": 768,
            "checkpoint_step": 200,
            "task_count": 5,
            "max_new_tokens": 64,
        },
    }
    if not isinstance(stages, dict) or stages != expected_stages:
        raise Phase4Error("Phase 4D/E stage 구성이 다릅니다.")
    if smoke.get("forbidden_automatic_changes") != [
        "cpu_offload",
        "deepspeed",
        "lora",
        "packing",
        "torch_compile",
        "flash_attention",
    ]:
        raise Phase4Error("Phase 4D/E 자동 변경 금지 계약이 다릅니다.")

    outputs = config.get("outputs")
    if not isinstance(outputs, dict):
        raise Phase4Error("Phase 4 출력 경로 계약이 없습니다.")
    for key in ("private_root", "public_root", "k0_root", "smoke_root"):
        resolve_repo_path(repo_root, str(outputs.get(key, "")).format(build_id="build-000000000000"))
    for key in ("canonical_root", "canonical_public_root"):
        resolve_repo_path(repo_root, str(outputs.get(key, "")))

    ignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    for fragment in ("/data/derived/", "/data/eval/", "/runs/", "/.venv/"):
        if fragment not in ignore:
            raise Phase4Error(f"필수 Git 제외 규칙이 없습니다: {fragment}")
    official_sources = config.get("official_sources")
    if not isinstance(official_sources, list) or any(
        not isinstance(url, str) or not url.startswith("https://")
        for url in official_sources
    ):
        raise Phase4Error("Phase 4 공식 출처 URL 계약이 올바르지 않습니다.")
    return {
        "status": "valid",
        "canonical_plan_version": config["canonical_plan_version"],
        "model_revision": model["revision"],
        "core_eval_rows": 200,
        "source_holdout_rows": 500,
        "formal_max_length": 768,
        "training_promotion_allowed": False,
    }


def _implementation_hashes(repo_root: Path, config_path: Path, config: dict[str, Any]) -> dict[str, str]:
    paths = [*_implementation_paths(config)]
    hashes: dict[str, str] = {}
    for relative in paths:
        path = resolve_repo_path(repo_root, relative)
        if not path.is_file():
            raise Phase4Error(f"Phase 4 구현 파일이 없습니다: {relative}")
        hashes[relative] = sha256_file(path)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    hashes[relative_config] = sha256_file(config_path)
    return hashes


def prepare_context(
    repo_root: Path,
    config_path: Path,
    *,
    verify_parents: bool = True,
) -> dict[str, Any]:
    config = load_json(config_path, "Phase 4 설정")
    validate_contract(config, repo_root)
    parent_result: dict[str, Any] | None = None
    phase3_result: dict[str, Any] | None = None
    if verify_parents:
        try:
            parent_result = verify_historical_staging(
                repo_root,
                staging_version=config["parent_staging"]["version"],
                build_id=config["parent_staging"]["build_id"],
                implementation_commit=config["parent_staging"]["implementation_commit"],
            )
        except Exception as exc:
            raise Phase4Error("승인된 과거 24K staging 재검증이 실패했습니다.") from exc
        if (
            parent_result.get("owner_risk_accepted") is not True
            or parent_result.get("training_promotion_allowed") is not False
            or parent_result.get("record_validation", {}).get("total_rows") != 24_000
        ):
            raise Phase4Error("24K staging은 Phase 4 입력 승인 상태여야 합니다.")
        phase3_config_path = resolve_repo_path(repo_root, config["model"]["phase3_config"])
        phase3_config = load_phase3_config(phase3_config_path)
        try:
            phase3_result = verify_phase3_report(
                phase3_config,
                repo_root,
                resolve_repo_path(repo_root, config["model"]["phase3_report"]),
            )
        except Exception as exc:
            raise Phase4Error("Phase 3 모델 준비 보고서 재검증이 실패했습니다.") from exc

    implementation_hashes = _implementation_hashes(repo_root, config_path, config)
    build_inputs = {
        "canonical_plan_version": config["canonical_plan_version"],
        "implementation_hashes": implementation_hashes,
        "model": {
            "phase3_build_id": config["model"]["phase3_build_id"],
            "revision": config["model"]["revision"],
            "snapshot_manifest_sha256": config["model"]["snapshot_manifest_sha256"],
        },
        "parent_staging": config["parent_staging"],
        "preflight_version": config["preflight_version"],
        "runtime_headers": {
            key: config["runtime_headers"][key]
            for key in ("package", "version", "bytes", "sha256", "url")
        },
        "seed": config["seed"],
        "split_contract_sha256": sha256_json(config["split"]),
        "generation_contract_sha256": sha256_json(config["generation"]),
        "k0_reuse_contract_sha256": sha256_json(config["k0_reuse"]),
        "triage_contract_sha256": sha256_json(config["triage"]),
        "training_smoke_contract_sha256": sha256_json(config["training_smoke"]),
    }
    build_sha256 = sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    outputs = config["outputs"]
    private_root = resolve_repo_path(
        repo_root, outputs["private_root"].format(build_id=build_id)
    )
    public_root = resolve_repo_path(
        repo_root, outputs["public_root"].format(build_id=build_id)
    )
    k0_root = resolve_repo_path(repo_root, outputs["k0_root"].format(build_id=build_id))
    smoke_root = resolve_repo_path(
        repo_root, outputs["smoke_root"].format(build_id=build_id)
    )
    return {
        "build_id": build_id,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "config": config,
        "config_path": config_path,
        "k0_root": k0_root,
        "smoke_root": smoke_root,
        "canonical_root": resolve_repo_path(repo_root, outputs["canonical_root"]),
        "canonical_public_root": resolve_repo_path(
            repo_root, outputs["canonical_public_root"]
        ),
        "parent_verification": parent_result,
        "phase3_verification": phase3_result,
        "private_root": private_root,
        "public_root": public_root,
        "workspace_base_commit": git_head(repo_root),
    }


def _tree_manifest(root: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise Phase4Error(f"runtime sysroot symlink는 허용하지 않습니다: {relative}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise Phase4Error(f"runtime sysroot special file은 허용하지 않습니다: {relative}")
        values.append({"bytes": info.st_size, "path": relative, "sha256": sha256_file(path)})
    return values


def verify_runtime_headers(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    runtime = config["runtime_headers"]
    root = resolve_repo_path(repo_root, runtime["local_root"])
    manifest_path = root / "RUNTIME_MANIFEST.json"
    if root.is_symlink() or not root.is_dir() or not manifest_path.is_file():
        raise Phase4Error("Phase 4 runtime header sysroot가 준비되지 않았습니다.")
    manifest = load_json(manifest_path, "runtime header manifest")
    expected_identity = {
        "package": runtime["package"],
        "version": runtime["version"],
        "package_bytes": runtime["bytes"],
        "package_sha256": runtime["sha256"],
    }
    if manifest.get("identity") != expected_identity:
        raise Phase4Error("runtime header package identity가 다릅니다.")
    actual_files = _tree_manifest(root)
    actual_without_manifest = [
        value for value in actual_files if value["path"] != "RUNTIME_MANIFEST.json"
    ]
    if actual_without_manifest != manifest.get("files"):
        raise Phase4Error("runtime header sysroot 파일 hash가 다릅니다.")
    required = [root / relative for relative in runtime["required_headers"]]
    if any(not path.is_file() for path in required):
        raise Phase4Error("runtime sysroot에 Python.h 또는 pyconfig.h가 없습니다.")
    return {
        "status": "verified",
        "file_count": len(actual_without_manifest),
        "manifest_sha256": sha256_file(manifest_path),
        "root": root.relative_to(repo_root).as_posix(),
    }


def prepare_runtime_headers(
    config: dict[str, Any], repo_root: Path, *, execute: bool
) -> dict[str, Any]:
    runtime = config["runtime_headers"]
    root = resolve_repo_path(repo_root, runtime["local_root"])
    if root.exists():
        return {**verify_runtime_headers(config, repo_root), "mode": "reused"}
    if not execute:
        return {
            "mode": "dry_run",
            "package": runtime["package"],
            "version": runtime["version"],
            "bytes": runtime["bytes"],
            "sha256": runtime["sha256"],
            "target": root.relative_to(repo_root).as_posix(),
            "writes_performed": False,
        }
    root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=".phase4-runtime-", dir=root.parent))
    download_path = temporary / "package.deb"
    extracted = temporary / "extracted"
    promoted = False
    try:
        try:
            with urllib.request.urlopen(runtime["url"], timeout=120) as response:
                payload = response.read(runtime["bytes"] + 1)
        except (OSError, urllib.error.URLError) as exc:
            raise Phase4Error("고정 Ubuntu Python header package 다운로드가 실패했습니다.") from exc
        if len(payload) != runtime["bytes"] or sha256_bytes(payload) != runtime["sha256"]:
            raise Phase4Error("Python header package bytes 또는 SHA-256이 다릅니다.")
        _write_bytes_atomic(download_path, payload, mode=PRIVATE_FILE_MODE)
        extracted.mkdir(mode=PRIVATE_DIR_MODE)
        completed = subprocess.run(
            ["dpkg-deb", "-x", str(download_path), str(extracted)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise Phase4Error("고정 Python header package를 추출하지 못했습니다.")
        destination = temporary / "sysroot"
        destination.mkdir(mode=PRIVATE_DIR_MODE)
        for relative in ("usr/include/python3.10", "usr/include/x86_64-linux-gnu/python3.10"):
            source = extracted / relative
            if not source.is_dir() or source.is_symlink():
                raise Phase4Error(f"Python header package 필수 디렉터리가 없습니다: {relative}")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, symlinks=False)
        files = _tree_manifest(destination)
        manifest = {
            "schema_version": "1.0.0",
            "identity": {
                "package": runtime["package"],
                "version": runtime["version"],
                "package_bytes": runtime["bytes"],
                "package_sha256": runtime["sha256"],
            },
            "files": files,
        }
        write_json_once(
            destination / "RUNTIME_MANIFEST.json", manifest, mode=PRIVATE_FILE_MODE
        )
        if root.exists():
            raise Phase4Error("runtime sysroot 준비 중 최종 경로가 생성됐습니다.")
        os.replace(destination, root)
        promoted = True
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    if not promoted:
        raise Phase4Error("runtime sysroot를 승격하지 못했습니다.")
    return {**verify_runtime_headers(config, repo_root), "mode": "prepared", "writes_performed": True}


def runtime_environment(config: dict[str, Any], repo_root: Path) -> dict[str, str]:
    verify_runtime_headers(config, repo_root)
    root = resolve_repo_path(repo_root, config["runtime_headers"]["local_root"])
    includes = [root / relative for relative in config["runtime_headers"]["include_roots"]]
    existing = os.environ.get("CPATH")
    cpath_values = [str(path) for path in includes]
    if existing:
        cpath_values.append(existing)
    environment = dict(os.environ)
    environment["CPATH"] = os.pathsep.join(cpath_values)
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    environment["HF_DATASETS_OFFLINE"] = "1"
    return environment


def artifact_hash_map(root: Path, relative_paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in sorted(relative_paths):
        path = root / validate_relative_path(relative)
        if path.is_symlink() or not path.is_file():
            raise Phase4Error(f"artifact가 regular file이 아닙니다: {relative}")
        result[relative] = sha256_file(path)
    return result


def verify_hash_map(root: Path, values: Any, label: str) -> None:
    if not isinstance(values, dict) or not values:
        raise Phase4Error(f"{label} artifact hash map이 비어 있습니다.")
    for relative, expected in values.items():
        if not isinstance(relative, str) or SHA256_PATTERN.fullmatch(str(expected)) is None:
            raise Phase4Error(f"{label} artifact hash metadata가 올바르지 않습니다.")
        path = root / validate_relative_path(relative)
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise Phase4Error(f"{label} artifact SHA-256이 다릅니다: {relative}")
