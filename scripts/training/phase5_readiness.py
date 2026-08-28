# phase5_readiness.py - 승인된 Phase 4 v2 산출물을 실제 학습 전 불변 입력 계약으로 고정한다.

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
    "deterministic_saju_qa",
    "saju_diary_bridge",
)
DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.0.0.json"
)


class Phase5ReadinessError(RuntimeError):
    """Phase 5 실행 전 불변 계약 위반."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_repo_path(repo_root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise Phase5ReadinessError("빈 상대경로는 허용하지 않습니다.")
    path = Path(relative)
    if (
        path.is_absolute()
        or relative != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(any(ord(character) < 32 for character in part) for part in path.parts)
    ):
        raise Phase5ReadinessError(f"안전하지 않은 상대경로입니다: {relative}")
    resolved = (repo_root / path).resolve(strict=False)
    if not resolved.is_relative_to(repo_root.resolve()):
        raise Phase5ReadinessError(f"저장소 밖 경로는 허용하지 않습니다: {relative}")
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase5ReadinessError(f"{label} 파일이 없습니다: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5ReadinessError(f"{label} JSON을 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase5ReadinessError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Phase5ReadinessError(f"{label} 파일이 없습니다: {path}")
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase5ReadinessError(
                        f"{label}에 빈 JSONL 행이 있습니다: {line_number}"
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase5ReadinessError(
                        f"{label} JSONL 행은 object여야 합니다: {line_number}"
                    )
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5ReadinessError(f"{label} JSONL을 읽지 못했습니다.") from exc
    return values


def _atomic_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if path.exists():
            raise Phase5ReadinessError(f"기존 불변 파일을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any, *, mode: int) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()
    _atomic_bytes(path, payload + b"\n", mode=mode)


def _write_jsonl(path: Path, values: Sequence[dict[str, Any]]) -> None:
    payload = b"".join(canonical_json_bytes(value) + b"\n" for value in values)
    _atomic_bytes(path, payload, mode=PRIVATE_FILE_MODE)


def _hash_map(root: Path, relatives: Sequence[str]) -> dict[str, str]:
    return {relative: sha256_file(root / relative) for relative in relatives}


def _verify_hash_map(root: Path, values: Any, label: str) -> None:
    if not isinstance(values, dict) or not values:
        raise Phase5ReadinessError(f"{label} artifact hash map이 없습니다.")
    for relative, expected in values.items():
        path = _safe_repo_path(root, relative)
        if (
            not isinstance(expected, str)
            or FULL_SHA_PATTERN.fullmatch(expected) is None
            or path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != expected
        ):
            raise Phase5ReadinessError(f"{label} artifact hash가 다릅니다: {relative}")


def _exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_PATTERN.fullmatch(value) is None:
        raise Phase5ReadinessError(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("canonical_plan_version") != "3.0.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("readiness_version") != "v1.0.0"
        or config.get("seed") != 42
    ):
        raise Phase5ReadinessError("Phase 5 readiness 정본·버전·seed가 다릅니다.")

    parent = config.get("parent_phase4")
    if not isinstance(parent, dict):
        raise Phase5ReadinessError("Phase 4 canonical 부모 계약이 없습니다.")
    if (
        parent.get("version") != "v2.0.0"
        or parent.get("status") != "approved_for_phase5_training"
        or parent.get("selected_max_length") != 768
        or parent.get("training_promotion_allowed") is not True
        or parent.get("phase5_training_performed") is not False
        or BUILD_ID_PATTERN.fullmatch(str(parent.get("preflight_build_id", ""))) is None
        or BUILD_ID_PATTERN.fullmatch(str(parent.get("canonical_build_id", ""))) is None
    ):
        raise Phase5ReadinessError("Phase 4 canonical 부모 상태가 다릅니다.")
    for key in (
        "preflight_build_sha256",
        "canonical_build_sha256",
        "private_manifest_sha256",
        "public_manifest_sha256",
        "completion_report_sha256",
    ):
        _exact_sha(parent.get(key), f"parent_phase4.{key}")
    _safe_repo_path(repo_root, str(parent.get("preflight_config", "")))

    model = config.get("model")
    if model != {
        "repo_id": "kakaocorp/kanana-2-1.3b-instruct",
        "revision": "bf4786aa2a1908adce942d53976270132732f720",
        "local_subdir": "models/saju_1b_baseline/kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720",
        "phase3_build_id": "build-32e2c84af3d3",
        "snapshot_manifest_sha256": "5786d04831c93192d234651df0894a1912b974cfab96011ce0676563185cc93d",
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "trust_remote_code": True,
        "local_files_only": True,
        "expected_parameter_count": 1_291_478_272,
    }:
        raise Phase5ReadinessError("Phase 5 고정 Kanana 모델 계약이 다릅니다.")
    model_root = _safe_repo_path(repo_root, model["local_subdir"])
    if model_root.is_symlink() or not model_root.is_dir():
        raise Phase5ReadinessError("Phase 5 고정 모델 snapshot이 없습니다.")

    template = config.get("chat_template")
    if not isinstance(template, dict):
        raise Phase5ReadinessError("Phase 5 chat template 계약이 없습니다.")
    template_path = _safe_repo_path(repo_root, str(template.get("path", "")))
    if (
        not template_path.is_file()
        or template.get("sha256")
        != "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3"
        or template.get("bytes") != 10_725
        or template_path.stat().st_size != 10_725
        or sha256_file(template_path) != template["sha256"]
    ):
        raise Phase5ReadinessError("Phase 5 chat template hash가 다릅니다.")

    runtime = config.get("runtime")
    expected_runtime = {
        "python": "3.10.12",
        "uv": "0.9.26",
        "torch": "2.13.0",
        "torch_build": "2.13.0+cu130",
        "torch_cuda": "13.0",
        "torchvision": "0.28.0",
        "torchaudio": "2.11.0",
        "transformers": "4.57.6",
        "trl": "1.12.0",
        "bitsandbytes": "0.50.2",
        "datasets": "4.7.0",
        "accelerate": "1.14.0",
        "pytorch_index_url": "https://download.pytorch.org/whl/cu130",
        "gpu_name": "NVIDIA GeForce RTX 5070 Ti",
        "compute_capability": [12, 0],
    }
    if runtime != expected_runtime:
        raise Phase5ReadinessError("Phase 5 고정 runtime 계약이 다릅니다.")
    environment_lock = config.get("environment_lock")
    if environment_lock != {
        "path": "requirements-phase3.lock.txt",
        "bytes": 160_907,
        "sha256": "0301de92dea4a21eb8077abb01aae6eaf412590ac670e436bcd5d7b3717b8aed",
    }:
        raise Phase5ReadinessError("Phase 5 환경 lock 계약이 다릅니다.")
    lock_path = _safe_repo_path(repo_root, environment_lock["path"])
    if (
        not lock_path.is_file()
        or lock_path.stat().st_size != environment_lock["bytes"]
        or sha256_file(lock_path) != environment_lock["sha256"]
    ):
        raise Phase5ReadinessError("Phase 5 환경 lock 파일 hash가 다릅니다.")

    data = config.get("data")
    if not isinstance(data, dict) or data.get("selected_max_length") != 768:
        raise Phase5ReadinessError("Phase 5 데이터 길이 계약이 다릅니다.")
    manifests = data.get("manifests")
    if not isinstance(manifests, dict) or set(manifests) != {"ki10", "ki20"}:
        raise Phase5ReadinessError("Phase 5 KI10/KI20 manifest 계약이 없습니다.")
    expected_manifests = {
        "ki10": ("manifests/mix10k_v2.jsonl", 10_000),
        "ki20": ("manifests/mix20k_v2.jsonl", 20_000),
    }
    for key, (relative, rows) in expected_manifests.items():
        value = manifests.get(key)
        if (
            not isinstance(value, dict)
            or value.get("relative_path") != relative
            or value.get("rows") != rows
        ):
            raise Phase5ReadinessError(f"Phase 5 {key} manifest 계약이 다릅니다.")
        _exact_sha(value.get("sha256"), f"data.manifests.{key}.sha256")
    evaluation = data.get("evaluation")
    if evaluation != {
        "source_relative_path": "eval/source_holdout_700.jsonl",
        "source_rows": 700,
        "rows_per_axis": 10,
        "total_rows": 70,
        "selection_seed": 42,
        "output_name": "phase5_eval70.jsonl",
    }:
        raise Phase5ReadinessError("Phase 5 고정 eval70 계약이 다릅니다.")
    axis_counts = data.get("axis_counts")
    if axis_counts != {
        "ki10": {
            "nemotron_saju": 3_400,
            "bazi_sft": 2_000,
            "aihub_empathy_single": 750,
            "aihub_empathy_multiturn": 750,
            "yeji_shensha_derived": 500,
            "deterministic_saju_qa": 1_000,
            "saju_diary_bridge": 1_600,
        },
        "ki20": {
            "nemotron_saju": 6_800,
            "bazi_sft": 4_000,
            "aihub_empathy_single": 1_500,
            "aihub_empathy_multiturn": 1_500,
            "yeji_shensha_derived": 1_000,
            "deterministic_saju_qa": 2_000,
            "saju_diary_bridge": 3_200,
        },
    }:
        raise Phase5ReadinessError("Phase 5 7축 manifest 수량 계약이 다릅니다.")

    training = config.get("training")
    expected_training = {
        "num_train_epochs": 1,
        "per_device_train_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "bf16": True,
        "fp16": False,
        "tf32": False,
        "optim": "paged_adamw_8bit",
        "learning_rate": 8.0e-6,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
        "gradient_checkpointing": True,
        "gradient_checkpointing_kwargs": {"use_reentrant": False},
        "use_cache": False,
        "assistant_only_loss": True,
        "packing": False,
        "padding_free": False,
        "loss_type": "chunked_nll",
        "shuffle_dataset": True,
        "logging_strategy": "steps",
        "logging_steps": 10,
        "logging_first_step": True,
        "eval_strategy": "steps",
        "eval_steps": 250,
        "save_strategy": "steps",
        "save_steps": 250,
        "save_total_limit": 2,
        "save_only_model": False,
        "save_safetensors": True,
        "dataloader_num_workers": 0,
        "torch_compile": False,
        "push_to_hub": False,
        "report_to": [],
        "seed": 42,
        "data_seed": 42,
    }
    if training != expected_training:
        raise Phase5ReadinessError("Phase 5 Full FT hyperparameter 계약이 다릅니다.")

    runs = config.get("runs")
    if runs != {
        "independent_initialization_required": True,
        "ki10": {
            "run_name": "KI10-MIX-v2",
            "manifest_key": "ki10",
            "initial_checkpoint": "fixed_instruct_snapshot",
            "expected_optimizer_steps": 1_250,
        },
        "ki20": {
            "run_name": "KI20-MIX-v2",
            "manifest_key": "ki20",
            "initial_checkpoint": "fixed_instruct_snapshot",
            "expected_optimizer_steps": 2_500,
        },
        "ki20_must_not_resume_from_ki10": True,
    }:
        raise Phase5ReadinessError("Phase 5 독립 KI10/KI20 Run 계약이 다릅니다.")

    safety = config.get("safety")
    if safety != {
        "minimum_available_disk_bytes": 68_719_476_736,
        "require_clean_worktree": True,
        "require_single_cuda_gpu": True,
        "checkpoint_requires_optimizer_scheduler_state": True,
        "model_or_checkpoint_git_tracking_allowed": False,
        "hub_upload_allowed": False,
        "phase5_execute_confirmation": "PHASE5_TRAINING",
    }:
        raise Phase5ReadinessError("Phase 5 실행 안전 계약이 다릅니다.")

    outputs = config.get("outputs")
    if outputs != {
        "private_root": "data/derived/saju_1b_baseline/phase5-readiness/v1.0.0/{build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-readiness/v1.0.0/{build_id}",
        "ki10_run_root": "runs/KI10-MIX-v2",
        "ki20_run_root": "runs/KI20-MIX-v2",
    }:
        raise Phase5ReadinessError("Phase 5 readiness 출력 경로가 다릅니다.")
    for key in ("private_root", "public_root"):
        _safe_repo_path(
            repo_root,
            str(outputs.get(key, "")).format(build_id="build-000000000000"),
        )
    for key in ("ki10_run_root", "ki20_run_root"):
        _safe_repo_path(repo_root, str(outputs.get(key, "")))
    ignore_lines = {
        line.strip()
        for line in (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    if not {"/data/derived/", "/runs/", "/models/"} <= ignore_lines:
        raise Phase5ReadinessError("모델·학습·private 데이터 Git 제외 규칙이 없습니다.")
    governance = config.get("governance")
    if governance != {
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }:
        raise Phase5ReadinessError("Phase 5 readiness 거버넌스 flag가 다릅니다.")
    implementation_files = config.get("implementation_files")
    if not isinstance(implementation_files, list) or implementation_files != [
        "scripts/training/__init__.py",
        "scripts/training/phase5_readiness.py",
    ]:
        raise Phase5ReadinessError(
            "Phase 5 readiness 구현 fingerprint 목록이 다릅니다."
        )
    official = config.get("official_sources")
    if not isinstance(official, list) or any(
        not isinstance(url, str) or not url.startswith("https://") for url in official
    ):
        raise Phase5ReadinessError("Phase 5 공식 출처 URL 계약이 다릅니다.")
    return {
        "status": "valid",
        "canonical_plan_version": "3.0.0",
        "manifest_rows": {"ki10": 10_000, "ki20": 20_000},
        "evaluation_rows": 70,
        "selected_max_length": 768,
        "phase5_training_performed": False,
    }


def prepare_context(
    repo_root: Path, config_path: Path, *, verify_parent: bool = False
) -> dict[str, Any]:
    config = _load_json(config_path, "Phase 5 readiness config")
    validate_contract(config, repo_root)
    hashes: dict[str, str] = {}
    relatives = [
        *config["implementation_files"],
        config_path.resolve().relative_to(repo_root.resolve()).as_posix(),
    ]
    for relative in relatives:
        path = _safe_repo_path(repo_root, relative)
        if not path.is_file():
            raise Phase5ReadinessError(f"구현 fingerprint 파일이 없습니다: {relative}")
        hashes[relative] = sha256_file(path)
    build_inputs = {
        "canonical_plan_version": config["canonical_plan_version"],
        "readiness_version": config["readiness_version"],
        "seed": config["seed"],
        "parent_phase4": config["parent_phase4"],
        "model_sha256": sha256_json(config["model"]),
        "chat_template_sha256": config["chat_template"]["sha256"],
        "runtime_sha256": sha256_json(config["runtime"]),
        "environment_lock": config["environment_lock"],
        "data_sha256": sha256_json(config["data"]),
        "training_sha256": sha256_json(config["training"]),
        "runs_sha256": sha256_json(config["runs"]),
        "safety_sha256": sha256_json(config["safety"]),
        "implementation_hashes": hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    outputs = config["outputs"]
    context = {
        "build_id": build_id,
        "build_sha256": build_sha256,
        "build_inputs": build_inputs,
        "config": config,
        "config_path": config_path,
        "private_root": _safe_repo_path(
            repo_root, outputs["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_repo_path(
            repo_root, outputs["public_root"].format(build_id=build_id)
        ),
    }
    if verify_parent:
        context["parent_verification"] = verify_parent_phase4(context, repo_root)
    return context


def _canonical_roots(context: dict[str, Any], repo_root: Path) -> tuple[Path, Path]:
    parent = context["config"]["parent_phase4"]
    private = _safe_repo_path(
        repo_root,
        f"data/derived/saju_1b_baseline/{parent['version']}/{parent['canonical_build_id']}",
    )
    public = _safe_repo_path(
        repo_root,
        "data/reports/saju_1b_baseline/preflight/"
        f"{parent['version']}/{parent['canonical_build_id']}",
    )
    return private, public


def verify_parent_phase4(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    from scripts.preflight.phase4_common import prepare_context as prepare_phase4
    from scripts.preflight.phase4_finalize import verify_finalized_phase4

    parent = context["config"]["parent_phase4"]
    phase4_context = prepare_phase4(
        repo_root,
        _safe_repo_path(repo_root, parent["preflight_config"]),
    )
    if (
        phase4_context["build_id"] != parent["preflight_build_id"]
        or phase4_context["build_sha256"] != parent["preflight_build_sha256"]
    ):
        raise Phase5ReadinessError("Phase 4 preflight fingerprint가 다릅니다.")
    try:
        verified = verify_finalized_phase4(phase4_context, repo_root)
    except Exception as exc:
        raise Phase5ReadinessError(
            "Phase 4 A~E canonical 재검증이 실패했습니다."
        ) from exc
    private_root, public_root = _canonical_roots(context, repo_root)
    private_manifest = private_root / "build_manifest.json"
    public_manifest = public_root / "build_manifest.json"
    completion = public_root / "phase4_completion_report.json"
    if (
        verified.get("build_id") != parent["canonical_build_id"]
        or verified.get("build_sha256") != parent["canonical_build_sha256"]
        or verified.get("training_promotion_allowed") is not True
        or verified.get("phase5_training_performed") is not False
        or sha256_file(private_manifest) != parent["private_manifest_sha256"]
        or sha256_file(public_manifest) != parent["public_manifest_sha256"]
        or sha256_file(completion) != parent["completion_report_sha256"]
    ):
        raise Phase5ReadinessError("Phase 4 canonical hash·승격 상태가 다릅니다.")
    registry = _load_json(
        repo_root / "configs/data_versions/saju_1b_baseline/registry.json",
        "dataset registry",
    )
    approved = registry.get("approved_derived")
    required = {
        "version": parent["version"],
        "build_id": parent["canonical_build_id"],
        "build_sha256": parent["canonical_build_sha256"],
        "private_manifest_sha256": parent["private_manifest_sha256"],
        "public_manifest_sha256": parent["public_manifest_sha256"],
        "selected_max_length": 768,
        "technical_full_ft_preflight_passed": True,
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
        "status": "approved_for_phase5_training",
    }
    if not isinstance(approved, dict) or any(
        approved.get(key) != value for key, value in required.items()
    ):
        raise Phase5ReadinessError(
            "registry approved_derived가 v2 canonical을 가리키지 않습니다."
        )
    return {
        "status": "verified_phase4_v2_parent",
        "canonical_root": private_root.relative_to(repo_root).as_posix(),
        "public_root": public_root.relative_to(repo_root).as_posix(),
        "selected_max_length": 768,
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
    }


def _select_eval70(
    rows: list[dict[str, Any]], *, seed: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_axis: dict[str, list[dict[str, Any]]] = {axis: [] for axis in AXES}
    for row in rows:
        axis = row.get("source_axis")
        if axis not in by_axis or row.get("category") != "source_holdout":
            raise Phase5ReadinessError("source holdout 축·범주가 올바르지 않습니다.")
        by_axis[axis].append(row)
    if {axis: len(values) for axis, values in by_axis.items()} != {
        axis: 100 for axis in AXES
    }:
        raise Phase5ReadinessError("source holdout은 7축별 100항목이어야 합니다.")
    selected: list[dict[str, Any]] = []
    for axis in AXES:
        ordered = sorted(
            by_axis[axis],
            key=lambda row: hashlib.sha256(
                f"{seed}|phase5-eval70|{axis}|{row['eval_id']}".encode()
            ).hexdigest(),
        )
        selected.extend(ordered[:10])
    selected.sort(
        key=lambda row: hashlib.sha256(
            f"{seed}|phase5-eval70-order|{row['eval_id']}".encode()
        ).hexdigest()
    )
    return selected, dict(
        sorted(Counter(row["source_axis"] for row in selected).items())
    )


def _verify_manifest_rows(
    values: list[dict[str, Any]], expected_rows: int, expected_axes: dict[str, int]
) -> dict[str, str]:
    if len(values) != expected_rows:
        raise Phase5ReadinessError(f"학습 manifest 행 수가 다릅니다: {len(values)}")
    ids: dict[str, str] = {}
    axis_counts: Counter[str] = Counter()
    components: set[str] = set()
    for row in values:
        record_id = row.get("id")
        record_sha = row.get("record_sha256")
        component = row.get("leakage_component_id")
        axis = row.get("mix_axis")
        if (
            not isinstance(record_id, str)
            or record_id in ids
            or not isinstance(record_sha, str)
            or FULL_SHA_PATTERN.fullmatch(record_sha) is None
            or not isinstance(component, str)
            or not component
            or axis not in AXES
            or not isinstance(row.get("total_tokens"), int)
            or row["total_tokens"] > 768
            or not isinstance(row.get("assistant_tokens"), int)
            or row["assistant_tokens"] <= 0
        ):
            raise Phase5ReadinessError("학습 manifest record 계약이 다릅니다.")
        ids[record_id] = record_sha
        components.add(component)
        axis_counts[axis] += 1
    if dict(axis_counts) != expected_axes:
        raise Phase5ReadinessError(f"학습 manifest 7축 수량이 다릅니다: {axis_counts}")
    return ids


def _git_clean(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return not result.stdout.strip()


def _git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise Phase5ReadinessError("Git HEAD 형식이 올바르지 않습니다.")
    return value


def _runtime_versions() -> dict[str, str]:
    names = (
        "torch",
        "torchvision",
        "torchaudio",
        "transformers",
        "trl",
        "bitsandbytes",
        "datasets",
        "accelerate",
    )
    return {name: importlib.metadata.version(name) for name in names}


def _runtime_hardware(
    runtime: dict[str, Any],
    *,
    require_single_cuda_gpu: bool,
    torch_module: Any | None = None,
) -> dict[str, Any]:
    if torch_module is None:
        import torch as torch_module

    cuda = torch_module.cuda
    if (
        torch_module.__version__ != runtime["torch_build"]
        or getattr(torch_module.version, "cuda", None) != runtime["torch_cuda"]
        or not cuda.is_available()
    ):
        raise Phase5ReadinessError("Phase 5 PyTorch CUDA runtime이 다릅니다.")
    device_count = cuda.device_count()
    if require_single_cuda_gpu and device_count != 1:
        raise Phase5ReadinessError(
            f"Phase 5는 단일 CUDA GPU만 허용합니다: {device_count}"
        )
    if device_count < 1:
        raise Phase5ReadinessError("Phase 5 CUDA GPU를 찾지 못했습니다.")
    gpu_name = cuda.get_device_name(0)
    capability = list(cuda.get_device_capability(0))
    if (
        gpu_name != runtime["gpu_name"]
        or capability != runtime["compute_capability"]
        or not cuda.is_bf16_supported()
    ):
        raise Phase5ReadinessError("Phase 5 GPU·compute capability·BF16 계약이 다릅니다.")
    return {
        "torch_build": torch_module.__version__,
        "torch_cuda": torch_module.version.cuda,
        "device_count": device_count,
        "gpu_name": gpu_name,
        "compute_capability": capability,
        "bf16_supported": True,
        "vram_total_bytes": cuda.get_device_properties(0).total_memory,
    }


def _uv_version() -> str:
    try:
        result = subprocess.run(
            ["uv", "--version"], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase5ReadinessError("uv 버전을 확인하지 못했습니다.") from exc
    prefix = "uv "
    value = result.stdout.strip()
    if not value.startswith(prefix):
        raise Phase5ReadinessError("uv 버전 출력 형식이 올바르지 않습니다.")
    return value.removeprefix(prefix)


def _build_payloads(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    config = context["config"]
    parent = config["parent_phase4"]
    private_parent, _ = _canonical_roots(context, repo_root)
    manifest_rows: dict[str, list[dict[str, Any]]] = {}
    manifest_ids: dict[str, dict[str, str]] = {}
    for key in ("ki10", "ki20"):
        contract = config["data"]["manifests"][key]
        path = private_parent / contract["relative_path"]
        if sha256_file(path) != contract["sha256"]:
            raise Phase5ReadinessError(f"{key} canonical manifest hash가 다릅니다.")
        rows = _read_jsonl(path, f"{key} canonical manifest")
        manifest_rows[key] = rows
        manifest_ids[key] = _verify_manifest_rows(
            rows, contract["rows"], config["data"]["axis_counts"][key]
        )
    if not set(manifest_ids["ki10"]) < set(manifest_ids["ki20"]):
        raise Phase5ReadinessError("KI10 manifest가 KI20의 strict subset이 아닙니다.")
    if any(
        manifest_ids["ki20"].get(record_id) != digest
        for record_id, digest in manifest_ids["ki10"].items()
    ):
        raise Phase5ReadinessError("KI10/KI20 공통 record hash가 다릅니다.")

    evaluation = config["data"]["evaluation"]
    holdout = _read_jsonl(
        private_parent / evaluation["source_relative_path"], "Phase 5 holdout source"
    )
    if len(holdout) != evaluation["source_rows"]:
        raise Phase5ReadinessError("Phase 5 holdout source 수량이 다릅니다.")
    eval70, eval_axis_counts = _select_eval70(
        holdout, seed=evaluation["selection_seed"]
    )
    train_components = {row["leakage_component_id"] for row in manifest_rows["ki20"]}
    eval_components = {
        parent_row["leakage_component_id"]
        for item in eval70
        for parent_row in item["parents"]
    }
    if train_components & eval_components:
        raise Phase5ReadinessError(
            "Phase 5 eval70과 KI20 leakage component가 겹칩니다."
        )
    eval_payload = b"".join(canonical_json_bytes(row) + b"\n" for row in eval70)
    eval_sha = hashlib.sha256(eval_payload).hexdigest()

    run_inputs: dict[str, dict[str, Any]] = {}
    for key in ("ki10", "ki20"):
        run = config["runs"][key]
        manifest = config["data"]["manifests"][key]
        run_inputs[key] = {
            "schema_version": "1.0.0",
            "run_name": run["run_name"],
            "initial_checkpoint": config["model"],
            "parent_phase4": parent,
            "manifest": manifest,
            "evaluation": {
                "relative_path": f"eval/{evaluation['output_name']}",
                "rows": 70,
                "sha256": eval_sha,
                "axis_counts": eval_axis_counts,
            },
            "selected_max_length": 768,
            "training": config["training"],
            "independent_from_other_run": True,
            "expected_optimizer_steps": run["expected_optimizer_steps"],
            "phase5_training_performed": False,
        }
    private_values: dict[str, bytes] = {
        f"eval/{evaluation['output_name']}": eval_payload,
        "run_inputs/ki10.json": json.dumps(
            run_inputs["ki10"], ensure_ascii=False, indent=2, sort_keys=True
        ).encode()
        + b"\n",
        "run_inputs/ki20.json": json.dumps(
            run_inputs["ki20"], ensure_ascii=False, indent=2, sort_keys=True
        ).encode()
        + b"\n",
    }
    disk_free = shutil.disk_usage(repo_root).free
    if disk_free < config["safety"]["minimum_available_disk_bytes"]:
        raise Phase5ReadinessError("Phase 5 시작 전 가용 disk가 64GiB 미만입니다.")
    versions = _runtime_versions()
    expected_versions = {
        key: config["runtime"][key]
        for key in (
            "torch",
            "torchvision",
            "torchaudio",
            "transformers",
            "trl",
            "bitsandbytes",
            "datasets",
            "accelerate",
        )
    }
    if (
        versions != expected_versions
        or platform.python_version() != config["runtime"]["python"]
        or _uv_version() != config["runtime"]["uv"]
    ):
        raise Phase5ReadinessError("Phase 5 Python/package runtime 버전이 다릅니다.")
    hardware = _runtime_hardware(
        config["runtime"],
        require_single_cuda_gpu=config["safety"]["require_single_cuda_gpu"],
    )
    summary = {
        "schema_version": "1.0.0",
        "report_type": "phase5_readiness_summary",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "ready_for_explicit_phase5_execution",
        "parent_phase4": parent,
        "manifest_rows": {"ki10": 10_000, "ki20": 20_000},
        "manifest_axis_counts": config["data"]["axis_counts"],
        "nested_manifest_verified": True,
        "selected_max_length": 768,
        "evaluation": {
            "rows": 70,
            "axis_counts": eval_axis_counts,
            "sha256": eval_sha,
            "train_component_overlap": 0,
            "contains_restricted_text": True,
            "stored_in_git_ignored_private_root": True,
        },
        "runtime_versions": versions,
        "runtime_hardware": hardware,
        "python": platform.python_version(),
        "uv": _uv_version(),
        "environment_lock": config["environment_lock"],
        "available_disk_bytes": disk_free,
        "minimum_available_disk_bytes": config["safety"][
            "minimum_available_disk_bytes"
        ],
        "independent_run_initialization_verified": True,
        "expected_optimizer_steps": {"ki10": 1_250, "ki20": 2_500},
        "workspace_commit": _git_head(repo_root),
        "working_tree_clean_at_build_start": True,
        "checkpoint_state_required": [
            "model",
            "optimizer",
            "scheduler",
            "trainer_state",
        ],
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
        "training_execution_requires_separate_explicit_command": True,
        "raw_samples_in_report": False,
    }
    public_values = {
        "readiness_summary.json": json.dumps(
            summary, ensure_ascii=False, indent=2, sort_keys=True
        ).encode()
        + b"\n"
    }
    return private_values, public_values


def _manifest_payload(
    context: dict[str, Any], root: Path, artifacts: dict[str, bytes], *, public: bool
) -> bytes:
    hashes = {relative: sha256_file(root / relative) for relative in sorted(artifacts)}
    manifest = {
        "schema_version": "1.0.0",
        "report_type": (
            "phase5_readiness_public_manifest"
            if public
            else "phase5_readiness_private_manifest"
        ),
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "build_inputs": context["build_inputs"],
        "artifact_sha256": hashes,
        "status": "ready_for_explicit_phase5_execution",
        "training_promotion_allowed": True,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode()
        + b"\n"
    )


def build_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if private_root.exists() or public_root.exists():
        if not private_root.exists() or not public_root.exists():
            raise Phase5ReadinessError("readiness private/public 중 한쪽만 존재합니다.")
        return {**verify_readiness(context, repo_root), "mode": "reused"}
    if context["config"]["safety"]["require_clean_worktree"] and not _git_clean(
        repo_root
    ):
        raise Phase5ReadinessError(
            "Phase 5 readiness 생성 전 working tree가 깨끗해야 합니다."
        )
    verify_parent_phase4(context, repo_root)
    private_values, public_values = _build_payloads(context, repo_root)
    private_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    private_temp = Path(
        tempfile.mkdtemp(prefix=f".{private_root.name}-", dir=private_root.parent)
    )
    public_temp = Path(
        tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent)
    )
    private_promoted = False
    public_promoted = False
    try:
        for relative, payload in private_values.items():
            _atomic_bytes(private_temp / relative, payload, mode=PRIVATE_FILE_MODE)
        for directory in [
            private_temp,
            *[p for p in private_temp.rglob("*") if p.is_dir()],
        ]:
            directory.chmod(PRIVATE_DIR_MODE)
        private_manifest = _manifest_payload(
            context, private_temp, private_values, public=False
        )
        _atomic_bytes(
            private_temp / "build_manifest.json",
            private_manifest,
            mode=PRIVATE_FILE_MODE,
        )
        for relative, payload in public_values.items():
            _atomic_bytes(public_temp / relative, payload, mode=PUBLIC_FILE_MODE)
        public_manifest = _manifest_payload(
            context, public_temp, public_values, public=True
        )
        _atomic_bytes(
            public_temp / "build_manifest.json",
            public_manifest,
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(private_temp, private_root)
        private_promoted = True
        os.replace(public_temp, public_root)
        public_promoted = True
    finally:
        if not private_promoted:
            shutil.rmtree(private_temp, ignore_errors=True)
        if not public_promoted:
            shutil.rmtree(public_temp, ignore_errors=True)
    return {**verify_readiness(context, repo_root), "mode": "built"}


def verify_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_parent_phase4(context, repo_root)
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if (
        private_root.is_symlink()
        or public_root.is_symlink()
        or not private_root.is_dir()
        or not public_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) & 0o077
    ):
        raise Phase5ReadinessError("Phase 5 readiness 경로·권한이 다릅니다.")
    private = _load_json(private_root / "build_manifest.json", "private readiness")
    public = _load_json(public_root / "build_manifest.json", "public readiness")
    for manifest in (private, public):
        if (
            manifest.get("build_id") != context["build_id"]
            or manifest.get("build_sha256") != context["build_sha256"]
            or manifest.get("build_inputs") != context["build_inputs"]
            or manifest.get("status") != "ready_for_explicit_phase5_execution"
            or manifest.get("training_promotion_allowed") is not True
            or manifest.get("phase5_training_performed") is not False
        ):
            raise Phase5ReadinessError(
                "Phase 5 readiness manifest identity가 다릅니다."
            )
    _verify_hash_map(private_root, private.get("artifact_sha256"), "private readiness")
    _verify_hash_map(public_root, public.get("artifact_sha256"), "public readiness")
    eval_name = context["config"]["data"]["evaluation"]["output_name"]
    eval70 = _read_jsonl(private_root / f"eval/{eval_name}", "Phase 5 eval70")
    counts = Counter(row.get("source_axis") for row in eval70)
    if len(eval70) != 70 or counts != Counter({axis: 10 for axis in AXES}):
        raise Phase5ReadinessError("Phase 5 eval70 수량이 다릅니다.")
    for path in private_root.rglob("*"):
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase5ReadinessError(
                f"private readiness 파일 권한이 다릅니다: {path}"
            )
    for path in public_root.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE
        ):
            raise Phase5ReadinessError(
                f"public readiness 파일 형식·권한이 다릅니다: {path}"
            )
    summary = _load_json(public_root / "readiness_summary.json", "readiness summary")
    if (
        summary.get("phase5_training_performed") is not False
        or summary.get("training_execution_requires_separate_explicit_command")
        is not True
        or summary.get("evaluation", {}).get("train_component_overlap") != 0
    ):
        raise Phase5ReadinessError("Phase 5 readiness 공개 상태가 다릅니다.")
    return {
        "status": "verified_ready_for_explicit_phase5_execution",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "manifest_rows": {"ki10": 10_000, "ki20": 20_000},
        "evaluation_rows": 70,
        "selected_max_length": 768,
        "phase5_training_performed": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 실행 전 readiness Gate")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-contract")
    subparsers.add_parser("plan")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--execute", action="store_true")
    subparsers.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = REPO_ROOT
    config_path = args.config
    if not config_path.is_absolute():
        config_path = repo_root / config_path
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                _load_json(config_path, "Phase 5 readiness config"), repo_root
            )
        elif args.command == "plan":
            context = prepare_context(repo_root, config_path)
            result = {
                "status": "planned",
                "build_id": context["build_id"],
                "build_sha256": context["build_sha256"],
                "private_root": context["private_root"]
                .relative_to(repo_root)
                .as_posix(),
                "public_root": context["public_root"].relative_to(repo_root).as_posix(),
                "phase5_training_performed": False,
            }
        elif args.command == "prepare":
            context = prepare_context(repo_root, config_path)
            result = (
                build_readiness(context, repo_root)
                if args.execute
                else {
                    "status": "dry_run",
                    "build_id": context["build_id"],
                    "writes_performed": False,
                    "phase5_training_performed": False,
                }
            )
        else:
            context = prepare_context(repo_root, config_path, verify_parent=True)
            result = verify_readiness(context, repo_root)
    except Phase5ReadinessError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
