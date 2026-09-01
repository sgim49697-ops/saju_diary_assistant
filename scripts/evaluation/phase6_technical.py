# phase6_technical.py - Phase 6 자동 기술평가를 단회·재개 가능·fail-closed로 실행한다.

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.phase6_scoring import (
    AXES,
    EXPECTED_ROWS_BY_AXIS,
    Phase6ScoringError,
    aggregate_model_records,
    model_gate,
    no_regression,
    score_generation,
    select_baseline,
)
from scripts.training.phase5_quality_v2 import score_gate_v2
from scripts.training.phase5_stateful_chat_gate import score_case as score_stateful_case

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase6-technical-evaluation-v1.0.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^eval-[0-9a-f]{12}$")
MODEL_SLUGS = {
    "K0-INSTRUCT": "k0-instruct",
    "KI10-MIX-v2": "ki10-mix-v2",
    "KI20-MIX-v2": "ki20-mix-v2",
}
PHASE5_THRESHOLDS = {
    "expected_generation_cases": 1045,
    "expected_denominators": {
        "deterministic.stem_branch_identity": 12,
        "deterministic.yin_yang_elements_and_surface_counts": 12,
        "deterministic.hidden_stems": 12,
        "deterministic.stem_ten_gods": 12,
        "deterministic.branch_ten_gods": 12,
        "branch_policy": 40,
        "branch_policy.main_hidden_stem_application": 40,
        "branch_policy.surface_policy_rejection": 40,
        "shensha": 25,
        "handoff_action": 50,
        "handoff_no_fabrication": 50,
        "empathy_no_task_confusion": 20,
        "persona_no_causalization": 50,
    },
    "hard": {"generation_clean_min_percent": 98.0},
    "quality": {
        "typed_deterministic_min_percent": 90.0,
        "branch_policy_min_percent": 90.0,
        "shensha_min_percent": 90.0,
        "handoff_action_min_percent": 95.0,
        "foreign_sentence_max_percent": 3.0,
        "empathy_confusion_max_percent": 5.0,
        "persona_confusion_max_percent": 5.0,
    },
}
PUBLIC_FORBIDDEN_KEYS = {
    "eval_id",
    "case_id",
    "case_key",
    "component_id",
    "component_key",
    "leakage_component_id",
    "record_id",
    "prompt",
    "prompt_messages",
    "reference",
    "reference_assistant",
    "output",
    "raw_generations",
}
PUBLIC_FORBIDDEN_STRING_MARKERS = (
    "data/derived/",
    "data/staging/",
    "runs/KI",
    "blind_source_test_500.jsonl",
)


class Phase6TechnicalError(RuntimeError):
    """Phase 6 계약·입력·실행·산출물 위반."""


def _now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Phase6TechnicalError(f"JSON 중복 key를 허용하지 않습니다: {key}")
        result[key] = value
    return result


def _strict_loads(payload: str, label: str) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, Phase6TechnicalError):
            raise
        raise Phase6TechnicalError(f"{label} JSON을 읽지 못했습니다.") from exc


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(repo_root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise Phase6TechnicalError(f"저장소 상대경로가 올바르지 않습니다: {relative}")
    root = repo_root.resolve()
    resolved = (repo_root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise Phase6TechnicalError(f"경로가 저장소를 벗어납니다: {relative}") from exc
    current = repo_root
    for part in value.parts:
        current /= part
        if current.is_symlink():
            raise Phase6TechnicalError(f"symlink 경로를 허용하지 않습니다: {relative}")
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase6TechnicalError(f"{label} 파일이 없습니다: {path}")
    try:
        value = _strict_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError) as exc:
        raise Phase6TechnicalError(f"{label} 파일을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Phase6TechnicalError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Phase6TechnicalError(f"{label} 파일이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n") or not line.strip():
                    raise Phase6TechnicalError(
                        f"{label} {line_number}행이 비었거나 newline으로 끝나지 않습니다."
                    )
                value = _strict_loads(line, f"{label} {line_number}행")
                if not isinstance(value, dict):
                    raise Phase6TechnicalError(f"{label} {line_number}행은 object여야 합니다.")
                rows.append(value)
    except (OSError, UnicodeError) as exc:
        raise Phase6TechnicalError(f"{label} JSONL을 읽지 못했습니다.") from exc
    return rows


def _atomic_write(path: Path, payload: bytes, *, mode: int, exclusive: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else PUBLIC_DIR_MODE)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if exclusive and path.exists():
            raise Phase6TechnicalError(f"불변 파일을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_once(path: Path, payload: bytes, *, mode: int) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise Phase6TechnicalError(f"기존 불변 파일의 내용이 다릅니다: {path}")
        return
    _atomic_write(path, payload, mode=mode, exclusive=True)


def _append_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if path.is_symlink():
        raise Phase6TechnicalError(f"append 대상이 symlink입니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    path.parent.chmod(PRIVATE_DIR_MODE)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
        PRIVATE_FILE_MODE,
    )
    try:
        with os.fdopen(descriptor, "ab", closefd=True) as stream:
            for row in rows:
                stream.write(_canonical_json_bytes(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if path.exists():
            path.chmod(PRIVATE_FILE_MODE)


def _exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_PATTERN.fullmatch(value) is None:
        raise Phase6TechnicalError(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def _assert_hashed_file(repo_root: Path, value: Mapping[str, Any], label: str) -> Path:
    path = _safe_path(repo_root, str(value.get("path", "")))
    expected = _exact_sha(value.get("sha256"), f"{label}.sha256")
    if not path.is_file() or _sha256_file(path) != expected:
        raise Phase6TechnicalError(f"{label} 파일 hash가 다릅니다.")
    return path


def validate_contract(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("canonical_plan_version") != "4.0.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("evaluation_id") != "phase6-technical-evaluation"
        or config.get("evaluation_version") != "v1.0.0"
        or config.get("seed") != 42
    ):
        raise Phase6TechnicalError("Phase 6 정본·버전·seed 계약이 다릅니다.")
    blind = config.get("blind_source")
    if not isinstance(blind, Mapping):
        raise Phase6TechnicalError("blind source 계약이 없습니다.")
    if (
        blind.get("rows") != 500
        or blind.get("components") != 350
        or blind.get("file_mode") != "0600"
        or blind.get("directory_mode") != "0700"
        or blind.get("expected_rows_by_axis") != EXPECTED_ROWS_BY_AXIS
        or blind.get("components_per_axis") != 50
        or blind.get("bazi_rows_per_component") != 4
        or blind.get("aggregation") != "record_then_leakage_component_then_axis_macro"
        or blind.get("maximum_evaluation_runs") != 1
    ):
        raise Phase6TechnicalError("blind 수량·집계·단회 계약이 다릅니다.")
    _exact_sha(blind.get("sha256"), "blind_source.sha256")
    _safe_path(repo_root, str(blind.get("path", "")))
    _safe_path(repo_root, str(blind.get("consumption_marker", "")))
    models = config.get("models")
    if not isinstance(models, Mapping) or set(models) != set(MODEL_SLUGS):
        raise Phase6TechnicalError("평가 모델 집합이 다릅니다.")
    for name, model in models.items():
        if not isinstance(model, Mapping) or model.get("role") not in {
            "comparator_only",
            "retention_candidate",
            "selection_candidate",
        }:
            raise Phase6TechnicalError(f"모델 역할이 다릅니다: {name}")
        root = _safe_path(repo_root, str(model.get("path", "")))
        if not root.is_dir() or root.is_symlink():
            raise Phase6TechnicalError(f"모델 경로가 없습니다: {name}")
        required = model.get("required_files")
        if not isinstance(required, Mapping) or set(required) != {
            "model.safetensors",
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "chat_template.jinja",
        }:
            raise Phase6TechnicalError(f"모델 파일 계약이 다릅니다: {name}")
        for value in required.values():
            _exact_sha(value, f"models.{name}.required_files")
    generation = config.get("generation")
    if not isinstance(generation, Mapping) or generation != {
        "confirmation_variable": "PHASE6_TECHNICAL_BLIND",
        "confirmation_value": "K0_KI10_KI20_V1",
        "model_order": ["K0-INSTRUCT", "KI10-MIX-v2", "KI20-MIX-v2"],
        "expected_gpu_count": 1,
        "require_no_other_compute_processes": True,
        "local_files_only": True,
        "trust_remote_code": True,
        "fix_mistral_regex": False,
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "do_sample": False,
        "num_beams": 1,
        "nonsealed_batch_size": 4,
        "nonsealed_max_new_tokens": 128,
        "stateful_batch_size": 4,
        "stateful_max_new_tokens": 256,
        "blind_batch_size": 1,
        "blind_max_new_tokens": 512,
        "likelihood_batch_size": 8,
        "formal_max_length": 768,
    }:
        raise Phase6TechnicalError("생성·likelihood 계약이 다릅니다.")
    thresholds = config.get("thresholds")
    if thresholds != {
        "generation_clean_min_percent": 98.0,
        "task_confusion_max_percent": 5.0,
        "input_fact_violation_max_percent": 10.0,
        "foreign_sentence_max_percent": 3.0,
        "deterministic_min_percent": 90.0,
        "rule_min_percent": 90.0,
        "handoff_min_percent": 95.0,
        "no_regression_tolerance_percent_points": 2.0,
        "zero_tolerance_count": 0,
    }:
        raise Phase6TechnicalError("자동 baseline threshold가 다릅니다.")
    governance = config.get("governance")
    if governance != {
        "decision_inputs": "repository_local_automatic_metrics_only",
        "unavailable_semantics": "not_measured",
        "unavailable_semantics_blocks_completion": False,
        "reference_similarity_is_quality_metric": False,
        "blind_result_may_change_scorer": False,
        "training_execution_allowed": False,
        "release_approval_allowed": False,
        "application_binding_allowed": False,
        "mix20k_v3_1_generation_allowed": False,
        "public_raw_output_allowed": False,
    }:
        raise Phase6TechnicalError("자동 평가 권한·금지 경계가 다릅니다.")
    outputs = config.get("outputs")
    if outputs != {
        "private_root": "runs/PHASE6-TECHNICAL/v1.0.0/{evaluation_build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase6-technical/v1.0.0/{evaluation_build_id}",
    }:
        raise Phase6TechnicalError("Phase 6 출력 경로가 다릅니다.")
    expected_files = [
        "scripts/evaluation/phase6_scoring.py",
        "scripts/evaluation/phase6_technical.py",
        "scripts/training/phase5_quality_v2.py",
        "scripts/training/phase5_stateful_chat_gate.py",
        "scripts/preflight/phase4_data_v2.py",
        "tests/test_phase6_scoring.py",
        "tests/test_phase6_technical.py",
    ]
    if config.get("implementation_files") != expected_files:
        raise Phase6TechnicalError("구현 fingerprint 파일 목록이 다릅니다.")
    for relative in expected_files:
        if not _safe_path(repo_root, relative).is_file():
            raise Phase6TechnicalError(f"구현 파일이 없습니다: {relative}")
    return {
        "status": "valid",
        "evaluation_version": "v1.0.0",
        "models": list(generation["model_order"]),
        "blind_rows": 500,
        "blind_runs": 1,
        "decision_inputs": "repository_local_automatic_metrics_only",
    }


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = _load_json(config_path, "Phase 6 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: _sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "schema_version": config["schema_version"],
        "evaluation_id": config["evaluation_id"],
        "evaluation_version": config["evaluation_version"],
        "seed": config["seed"],
        "blind_source": config["blind_source"],
        "models": config["models"],
        "nonsealed_suite": config["nonsealed_suite"],
        "stateful_suite": config["stateful_suite"],
        "repair_ranking": config["repair_ranking"],
        "runtime_conformance": config["runtime_conformance"],
        "generation": config["generation"],
        "runtime": config["runtime"],
        "thresholds": config["thresholds"],
        "governance": config["governance"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = _sha256_json(build_inputs)
    evaluation_build_id = f"eval-{build_sha256[:12]}"
    if BUILD_ID_PATTERN.fullmatch(evaluation_build_id) is None:
        raise Phase6TechnicalError("evaluation build ID가 올바르지 않습니다.")
    return {
        "config": config,
        "config_path": config_path,
        "config_sha256": implementation_hashes[relative_config],
        "implementation_hashes": implementation_hashes,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "evaluation_build_id": evaluation_build_id,
        "private_root": _safe_path(
            repo_root,
            config["outputs"]["private_root"].format(
                evaluation_build_id=evaluation_build_id
            ),
        ),
        "public_root": _safe_path(
            repo_root,
            config["outputs"]["public_root"].format(
                evaluation_build_id=evaluation_build_id
            ),
        ),
    }


def _git_output(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise Phase6TechnicalError(f"git {' '.join(arguments)} 실행이 실패했습니다.")
    return completed.stdout.strip()


def _validate_model_hashes(context: Mapping[str, Any], repo_root: Path) -> None:
    for name, model in context["config"]["models"].items():
        for relative, expected in model["required_files"].items():
            path = _safe_path(repo_root, f"{model['path']}/{relative}")
            if not path.is_file() or _sha256_file(path) != expected:
                raise Phase6TechnicalError(f"모델 파일 hash가 다릅니다: {name}/{relative}")


def _validate_runtime_environment(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    try:
        import torch
        import transformers
    except Exception as exc:
        raise Phase6TechnicalError("평가 runtime을 import하지 못했습니다.") from exc
    contract = context["config"]["runtime"]
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if major_minor != contract["python_major_minor"]:
        raise Phase6TechnicalError(f"Python 버전이 다릅니다: {major_minor}")
    if torch.__version__.split("+")[0] != contract["torch"]:
        raise Phase6TechnicalError(f"torch 버전이 다릅니다: {torch.__version__}")
    if transformers.__version__ != contract["transformers"]:
        raise Phase6TechnicalError(
            f"transformers 버전이 다릅니다: {transformers.__version__}"
        )
    if torch.version.cuda != contract["torch_cuda"]:
        raise Phase6TechnicalError(f"torch CUDA 버전이 다릅니다: {torch.version.cuda}")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise Phase6TechnicalError("Phase 6에는 단일 CUDA GPU가 필요합니다.")
    architectures = torch.cuda.get_arch_list()
    if contract["required_cuda_arch"] not in architectures:
        raise Phase6TechnicalError(f"CUDA arch가 없습니다: {architectures}")
    for key in ("python_header", "pyconfig_header"):
        path = _safe_path(repo_root, contract[key])
        if not path.is_file():
            raise Phase6TechnicalError(f"Python native header가 없습니다: {contract[key]}")
    free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    return {
        "python": major_minor,
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "transformers": transformers.__version__,
        "cuda_architectures": architectures,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_total_memory_mib": round(total_bytes / 1024**2),
        "gpu_free_memory_mib": round(free_bytes / 1024**2),
        "native_jit_headers_available": True,
    }


def _validate_git_worktree(
    context: Mapping[str, Any], repo_root: Path, *, allow_resume: bool
) -> str:
    status = _git_output(
        repo_root, "status", "--porcelain=v1", "--untracked-files=all"
    )
    if not status:
        return "clean"
    if not allow_resume:
        raise Phase6TechnicalError("정식 실행 전 Git working tree가 깨끗해야 합니다.")

    public_root = context["public_root"].relative_to(repo_root).as_posix()
    allowed = {
        f"{public_root}/aggregate.json",
        f"{public_root}/decision.md",
        f"{public_root}/build_manifest.json",
    }
    paths: set[str] = set()
    for line in status.splitlines():
        if len(line) < 4 or line[:2] != "??" or line[3:] not in allowed:
            raise Phase6TechnicalError(
                "봉인 평가 재개 중 허용되지 않은 Git 변경이 있습니다."
            )
        paths.add(line[3:])
    if not paths:
        raise Phase6TechnicalError("Git working tree 상태를 해석하지 못했습니다.")

    expected_identity = _consumption_identity(context, repo_root)
    marker_paths = (
        _safe_path(
            repo_root, context["config"]["blind_source"]["consumption_marker"]
        ),
        context["private_root"] / "blind_access_started.json",
    )
    for marker_path in marker_paths:
        marker = _load_json(marker_path, "blind consumption marker")
        _assert_same_consumption_identity(marker, expected_identity)
        if marker.get("status") != "spent_in_progress":
            raise Phase6TechnicalError("blind 시작 marker 상태가 다릅니다.")
    return "same_fingerprint_resume"


def preflight(
    context: Mapping[str, Any], repo_root: Path, *, allow_resume: bool = False
) -> dict[str, Any]:
    """blind payload를 열지 않고 계약·모델·공개 증거·runtime을 검증한다."""

    config = context["config"]
    blind = config["blind_source"]
    blind_path = _safe_path(repo_root, blind["path"])
    if not blind_path.is_file() or blind_path.is_symlink():
        raise Phase6TechnicalError("봉인 blind 파일이 없습니다.")
    blind_stat = blind_path.stat()
    parent_stat = blind_path.parents[1].stat()
    if stat.S_IMODE(blind_stat.st_mode) != PRIVATE_FILE_MODE:
        raise Phase6TechnicalError("봉인 blind 파일 mode가 0600이 아닙니다.")
    if stat.S_IMODE(parent_stat.st_mode) != PRIVATE_DIR_MODE:
        raise Phase6TechnicalError("봉인 blind build directory mode가 0700이 아닙니다.")
    public_contract_path = _assert_hashed_file(
        repo_root, blind["public_contract"], "blind public contract"
    )
    public_contract = _load_json(public_contract_path, "blind public contract")
    roles = public_contract.get("roles", {})
    private_hashes = public_contract.get("private_artifact_sha256", {})
    if (
        roles.get("blind_source_test_rows") != 500
        or roles.get("blind_source_test_components") != 350
        or private_hashes.get("eval/blind_source_test_500.jsonl") != blind["sha256"]
        or public_contract.get("blind_raw_or_ids_in_public_report") is not False
    ):
        raise Phase6TechnicalError("blind 공개 seal 계약이 다릅니다.")
    for section in ("nonsealed_suite", "stateful_suite"):
        values = config[section]
        for key, value in values.items():
            if isinstance(value, Mapping) and "path" in value and "sha256" in value:
                _assert_hashed_file(repo_root, value, f"{section}.{key}")
            elif isinstance(value, Mapping):
                for nested_key, nested in value.items():
                    if isinstance(nested, Mapping) and "path" in nested:
                        _assert_hashed_file(
                            repo_root, nested, f"{section}.{key}.{nested_key}"
                        )
    _assert_hashed_file(
        repo_root, config["repair_ranking"]["manifest"], "repair manifest"
    )
    runtime_report_path = _assert_hashed_file(
        repo_root, config["runtime_conformance"], "runtime conformance"
    )
    runtime_report = _load_json(runtime_report_path, "runtime conformance")
    if (
        runtime_report.get("status")
        != config["runtime_conformance"]["expected_status"]
        or runtime_report.get("release_registry_creation_allowed") is not False
        or runtime_report.get("sealed_blind_accessed") is not False
    ):
        raise Phase6TechnicalError("runtime conformance v8 상태가 다릅니다.")
    _validate_model_hashes(context, repo_root)
    runtime = _validate_runtime_environment(context, repo_root)
    worktree_mode = _validate_git_worktree(
        context, repo_root, allow_resume=allow_resume
    )
    branch = _git_output(repo_root, "branch", "--show-current")
    commit = _git_output(repo_root, "rev-parse", "HEAD")
    return {
        "status": "ready",
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "git_branch": branch,
        "git_commit": commit,
        "git_worktree_mode": worktree_mode,
        "blind_payload_opened": False,
        "blind_rows_expected": blind["rows"],
        "models_verified": list(config["generation"]["model_order"]),
        "runtime": runtime,
        "runtime_conformance_status": runtime_report["status"],
        "writes_performed": False,
    }


def _confirmation(config: Mapping[str, Any]) -> None:
    generation = config["generation"]
    if os.environ.get(generation["confirmation_variable"]) != generation["confirmation_value"]:
        raise Phase6TechnicalError(
            f"실행에는 {generation['confirmation_variable']}="
            f"{generation['confirmation_value']} 확인값이 필요합니다."
        )


def _assert_no_other_compute_processes() -> None:
    completed = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise Phase6TechnicalError("GPU compute process를 확인할 수 없습니다.")
    try:
        active = {
            int(line.strip())
            for line in completed.stdout.splitlines()
            if line.strip() and line.strip() not in {"N/A", "[N/A]"}
        }
    except ValueError as exc:
        raise Phase6TechnicalError("GPU compute process PID 형식이 다릅니다.") from exc
    others = sorted(active - {os.getpid()})
    if others:
        raise Phase6TechnicalError(
            f"다른 GPU compute process가 있어 평가를 시작하지 않습니다: {others}"
        )


def _hashed_rows(repo_root: Path, value: Mapping[str, Any], label: str) -> list[dict[str, Any]]:
    return _read_jsonl(_assert_hashed_file(repo_root, value, label), label)


def _case_key(eval_id: str, case_id: str) -> str:
    return hashlib.sha256(f"{eval_id}|{case_id}".encode()).hexdigest()


def _load_nonsealed_cases(
    context: Mapping[str, Any], repo_root: Path
) -> list[dict[str, Any]]:
    suite = context["config"]["nonsealed_suite"]
    dev = _hashed_rows(repo_root, suite["dev_diagnostic"], "nonsealed dev")
    persona = _hashed_rows(repo_root, suite["persona_guard"], "nonsealed persona")
    overlays = _hashed_rows(repo_root, suite["contract_overlay"], "nonsealed overlay")
    handoff = _hashed_rows(repo_root, suite["expanded_handoff"], "nonsealed handoff")
    overlay_by_id = {
        (row.get("eval_id"), row.get("case_id")): row.get("automated_contract_v2")
        for row in overlays
    }
    if len(overlay_by_id) != 130 or any(value is None for value in overlay_by_id.values()):
        raise Phase6TechnicalError("nonsealed overlay 130건 계약이 다릅니다.")
    cases: list[dict[str, Any]] = []
    overlay_hits = 0
    for item in [*dev, *persona]:
        for case in item.get("cases", []):
            identity = (item.get("eval_id"), case.get("case_id"))
            contract = overlay_by_id.get(identity, item.get("automated_contract"))
            overlay_hits += int(identity in overlay_by_id)
            cases.append(
                {
                    "case_key": _case_key(str(identity[0]), str(identity[1])),
                    "eval_id": identity[0],
                    "case_id": identity[1],
                    "category": item.get("category"),
                    "source_axis": item.get("source_axis"),
                    "prompt_messages": case.get("prompt_messages"),
                    "automated_contract_v2": contract,
                }
            )
    additions = [row for row in handoff if row.get("origin") == "v1.2_addition"]
    if len(cases) != 1000 or overlay_hits != 130 or len(additions) != 45:
        raise Phase6TechnicalError("nonsealed 기본·overlay·handoff 수량이 다릅니다.")
    for row in additions:
        cases.append(
            {
                "case_key": _case_key(str(row.get("eval_id")), str(row.get("case_id"))),
                "eval_id": row.get("eval_id"),
                "case_id": row.get("case_id"),
                "category": row.get("category"),
                "source_axis": row.get("source_axis"),
                "prompt_messages": row.get("prompt_messages"),
                "automated_contract_v2": row.get("automated_contract_v2"),
            }
        )
    identities = {row["case_key"] for row in cases}
    if len(cases) != suite["cases"] or len(identities) != len(cases):
        raise Phase6TechnicalError("nonsealed case 수·identity가 다릅니다.")
    if any(
        not isinstance(row["prompt_messages"], list)
        or not isinstance(row["automated_contract_v2"], Mapping)
        for row in cases
    ):
        raise Phase6TechnicalError("nonsealed prompt·contract 형식이 다릅니다.")
    return cases


def _load_stateful_cases(
    context: Mapping[str, Any], repo_root: Path
) -> tuple[list[dict[str, Any]], str]:
    suite = context["config"]["stateful_suite"]
    cases = _hashed_rows(repo_root, suite["cases"], "stateful cases")
    if len(cases) != suite["cases"]["rows"] or len(
        {row.get("case_id") for row in cases}
    ) != len(cases):
        raise Phase6TechnicalError("stateful case 수·identity가 다릅니다.")
    prompt_path = _assert_hashed_file(repo_root, suite["system_prompt"], "stateful prompt")
    system_prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not system_prompt:
        raise Phase6TechnicalError("stateful system prompt가 비어 있습니다.")
    return cases, system_prompt


def _load_model(
    context: Mapping[str, Any], repo_root: Path, model_name: str
) -> tuple[Any, Any, Any, Any]:
    if context["config"]["generation"]["require_no_other_compute_processes"]:
        _assert_no_other_compute_processes()
    try:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase6TechnicalError("모델 평가 runtime을 import하지 못했습니다.") from exc
    model_contract = context["config"]["models"][model_name]
    model_root = _safe_path(repo_root, model_contract["path"])
    tokenizer = AutoTokenizer.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=context["config"]["generation"]["fix_mistral_regex"],
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(
        model_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda:0")
    model.eval()
    model.config.use_cache = True
    return torch, transformers, tokenizer, model


def _unload_model(torch: Any, model: Any) -> None:
    del model
    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def _resume_rows(
    path: Path, expected_keys: set[str], label: str
) -> tuple[list[dict[str, Any]], set[str]]:
    if not path.exists():
        return [], set()
    rows = _read_jsonl(path, label)
    keys = [row.get("case_key") for row in rows]
    if (
        len(keys) != len(set(keys))
        or any(not isinstance(key, str) for key in keys)
        or not set(keys) <= expected_keys
    ):
        raise Phase6TechnicalError(f"{label} 재개 identity가 다릅니다.")
    return rows, set(keys)


def _generate_cases(
    *,
    torch: Any,
    tokenizer: Any,
    model: Any,
    cases: Sequence[Mapping[str, Any]],
    output_path: Path,
    batch_size: int,
    max_new_tokens: int,
    message_key: str,
    progress_event: str,
) -> list[dict[str, Any]]:
    expected_keys = {str(case["case_key"]) for case in cases}
    rows, completed = _resume_rows(output_path, expected_keys, progress_event)
    case_by_key = {str(case["case_key"]): case for case in cases}
    pending = [case for case in cases if str(case["case_key"]) not in completed]
    started = time.monotonic()
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        prompts = [
            tokenizer.apply_chat_template(
                case[message_key], tokenize=False, add_generation_prompt=True
            )
            for case in batch
        ]
        input_lengths = [
            len(tokenizer(prompt, add_special_tokens=False)["input_ids"])
            for prompt in prompts
        ]
        encoded = tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            add_special_tokens=False,
        ).to("cuda:0")
        with torch.inference_mode():
            tokens = model.generate(
                **encoded,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        prompt_width = encoded["input_ids"].shape[1]
        generated_tokens = tokens[:, prompt_width:]
        outputs = tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)
        additions: list[dict[str, Any]] = []
        for case, output, input_tokens, token_row in zip(
            batch, outputs, input_lengths, generated_tokens, strict=True
        ):
            values = token_row.tolist()
            eos_seen = tokenizer.eos_token_id in values
            if eos_seen:
                new_tokens = values.index(tokenizer.eos_token_id) + 1
            else:
                new_tokens = len(values)
            additions.append(
                {
                    "schema_version": "1.0.0",
                    "case_key": case["case_key"],
                    "input_tokens": input_tokens,
                    "new_tokens": new_tokens,
                    "eos_seen": eos_seen,
                    "max_token_hit": not eos_seen and new_tokens >= max_new_tokens,
                    "output": output.strip(),
                }
            )
        _append_jsonl(output_path, additions)
        rows.extend(additions)
        completed.update(row["case_key"] for row in additions)
        if len(rows) % max(10, batch_size * 10) < batch_size or len(rows) == len(cases):
            print(
                json.dumps(
                    {
                        "event": progress_event,
                        "completed": len(rows),
                        "total": len(cases),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    if len(rows) != len(cases) or {row["case_key"] for row in rows} != expected_keys:
        raise Phase6TechnicalError(f"{progress_event} 생성 결과가 불완전합니다.")
    rows.sort(key=lambda row: list(case_by_key).index(row["case_key"]))
    return rows


def _tokenize_likelihood_case(
    tokenizer: Any, messages: Sequence[Mapping[str, str]], max_length: int
) -> dict[str, list[int]]:
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_assistant_tokens_mask=True,
        add_generation_prompt=False,
    )
    input_ids = list(encoded["input_ids"])
    attention = list(encoded["attention_mask"])
    masks = list(encoded["assistant_masks"])
    if len(input_ids) > max_length:
        raise Phase6TechnicalError(
            f"likelihood 입력이 고정 {max_length} token 상한을 넘습니다: {len(input_ids)}"
        )
    labels = [token if mask else -100 for token, mask in zip(input_ids, masks, strict=True)]
    if sum(label != -100 for label in labels[1:]) <= 0:
        raise Phase6TechnicalError("assistant likelihood label이 0개입니다.")
    return {"input_ids": input_ids, "attention_mask": attention, "labels": labels}


def _evaluate_likelihood(
    *,
    torch: Any,
    tokenizer: Any,
    model: Any,
    cases: Sequence[Mapping[str, Any]],
    output_path: Path,
    batch_size: int,
    max_length: int,
    progress_event: str,
) -> list[dict[str, Any]]:
    from torch.nn import functional

    expected_keys = {str(case["case_key"]) for case in cases}
    rows, completed = _resume_rows(output_path, expected_keys, progress_event)
    pending = [case for case in cases if str(case["case_key"]) not in completed]
    started = time.monotonic()
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        tokenized = [
            _tokenize_likelihood_case(tokenizer, case["full_messages"], max_length)
            for case in batch
        ]
        width = max(len(row["input_ids"]) for row in tokenized)
        width = min(max_length, ((width + 7) // 8) * 8)
        input_ids: list[list[int]] = []
        attention: list[list[int]] = []
        labels: list[list[int]] = []
        for row in tokenized:
            padding = width - len(row["input_ids"])
            input_ids.append(row["input_ids"] + [tokenizer.pad_token_id] * padding)
            attention.append(row["attention_mask"] + [0] * padding)
            labels.append(row["labels"] + [-100] * padding)
        input_tensor = torch.tensor(input_ids, dtype=torch.long, device="cuda:0")
        attention_tensor = torch.tensor(attention, dtype=torch.long, device="cuda:0")
        label_tensor = torch.tensor(labels, dtype=torch.long, device="cuda:0")
        with torch.inference_mode():
            logits = model(input_ids=input_tensor, attention_mask=attention_tensor).logits
        shift_logits = logits[:, :-1, :].float()
        shift_labels = label_tensor[:, 1:]
        additions: list[dict[str, Any]] = []
        for index, case in enumerate(batch):
            active = shift_labels[index] != -100
            token_count = int(active.sum().item())
            nll_sum = float(
                functional.cross_entropy(
                    shift_logits[index][active],
                    shift_labels[index][active],
                    reduction="sum",
                ).item()
            )
            correct = int(
                (shift_logits[index][active].argmax(dim=-1) == shift_labels[index][active])
                .sum()
                .item()
            )
            row = {
                "schema_version": "1.0.0",
                "case_key": case["case_key"],
                "axis": case["axis"],
                "component_key": case["component_key"],
                "tokens": token_count,
                "nll_sum": nll_sum,
                "correct": correct,
                "nll": round(nll_sum / token_count, 9),
                "token_accuracy": round(correct / token_count, 9),
            }
            if "private_identity" in case:
                row["private_identity"] = case["private_identity"]
            additions.append(row)
        _append_jsonl(output_path, additions)
        rows.extend(additions)
        completed.update(row["case_key"] for row in additions)
        del input_tensor, attention_tensor, label_tensor, logits, shift_logits, shift_labels
        if len(rows) % max(100, batch_size * 25) < batch_size or len(rows) == len(cases):
            print(
                json.dumps(
                    {
                        "event": progress_event,
                        "completed": len(rows),
                        "total": len(cases),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    if len(rows) != len(cases) or {row["case_key"] for row in rows} != expected_keys:
        raise Phase6TechnicalError(f"{progress_event} 결과가 불완전합니다.")
    return rows


def _reuse_ki10_nonsealed(
    context: Mapping[str, Any], repo_root: Path, cases: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    reuse = context["config"]["nonsealed_suite"]["reusable_KI10"]
    existing = _hashed_rows(repo_root, reuse["existing"], "KI10 nonsealed existing")
    additions = _hashed_rows(
        repo_root, reuse["expanded_handoff"], "KI10 nonsealed handoff"
    )
    output_by_identity = {
        (row.get("eval_id"), row.get("case_id")): row.get("output")
        for row in [*existing, *additions]
    }
    if len(existing) != 1000 or len(additions) != 45 or len(output_by_identity) != 1045:
        raise Phase6TechnicalError("재사용 KI10 nonsealed 결과 수량이 다릅니다.")
    result = []
    for case in cases:
        output = output_by_identity.get((case["eval_id"], case["case_id"]))
        if not isinstance(output, str):
            raise Phase6TechnicalError("재사용 KI10 nonsealed identity가 다릅니다.")
        result.append(
            {
                "case_key": case["case_key"],
                "output": output,
                "max_token_hit": False,
                "reused": True,
            }
        )
    return result


def _score_nonsealed(
    cases: Sequence[Mapping[str, Any]], generations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    output_by_key = {row.get("case_key"): row for row in generations}
    joined = []
    for case in cases:
        generated = output_by_key.get(case["case_key"])
        if generated is None:
            raise Phase6TechnicalError("nonsealed generation이 누락됐습니다.")
        joined.append(
            {
                "eval_id": case["eval_id"],
                "case_id": case["case_id"],
                "category": case["category"],
                "source_axis": case["source_axis"],
                "prompt_messages": case["prompt_messages"],
                "automated_contract_v2": case["automated_contract_v2"],
                "output": generated["output"],
            }
        )
    try:
        report = score_gate_v2(
            joined,
            thresholds=PHASE5_THRESHOLDS,
            technical={
                "artifact_identity_and_hashes": True,
                "scorer_reference_and_mutation_validation": True,
                "finite_loss_and_gradient": True,
                "exact_optimizer_steps": True,
                "checkpoint_reload": True,
            },
        )
    except Exception as exc:
        raise Phase6TechnicalError("nonsealed Gate v2 scorer 실행이 실패했습니다.") from exc
    return {
        "cases": len(joined),
        "quality_target_status": report["quality_target_status"],
        "metrics": report["metrics"],
        "quality_targets": report["quality_targets"],
        "failed_quality_targets": report["failed_quality_targets"],
        "handoff_action_percent": report["metrics"]["handoff_action"]["percent"],
        "blind_accessed": False,
    }


def _reuse_ki20_stateful(
    context: Mapping[str, Any], repo_root: Path, cases: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows = _hashed_rows(
        repo_root,
        context["config"]["stateful_suite"]["reusable_KI20"],
        "KI20 stateful reuse",
    )
    output_by_id = {row.get("case_id"): row.get("output") for row in rows}
    if len(rows) != 100 or len(output_by_id) != 100:
        raise Phase6TechnicalError("재사용 KI20 stateful 결과 수량이 다릅니다.")
    return [
        {
            "case_key": hashlib.sha256(str(case["case_id"]).encode()).hexdigest(),
            "output": output_by_id[case["case_id"]],
            "max_token_hit": False,
            "reused": True,
        }
        for case in cases
    ]


def _score_stateful(
    cases: Sequence[Mapping[str, Any]], generations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    output_by_key = {row.get("case_key"): row.get("output") for row in generations}
    scored = []
    for case in cases:
        key = hashlib.sha256(str(case["case_id"]).encode()).hexdigest()
        output = output_by_key.get(key)
        if not isinstance(output, str):
            raise Phase6TechnicalError("stateful generation identity가 다릅니다.")
        scored.append(score_stateful_case(dict(case), output))
    names = (
        "required_action_pass",
        "no_fabricated_four_pillars",
        "provided_field_reask",
        "false_ui_or_completion",
        "unsupported_date_or_period_fact",
        "severe_safety",
        "consecutive_normalized_duplicate",
        "nonempty_output",
        "passed",
    )
    metrics = {
        name: {
            "count": sum(bool(row[name]) for row in scored),
            "percent": round(sum(bool(row[name]) for row in scored) * 100 / len(scored), 6),
        }
        for name in names
    }
    strata: dict[str, dict[str, Any]] = {}
    for stratum in sorted({row["stratum"] for row in scored}):
        values = [row for row in scored if row["stratum"] == stratum]
        strata[stratum] = {
            "cases": len(values),
            "required_action_percent": round(
                sum(bool(row["required_action_pass"]) for row in values) * 100 / len(values),
                6,
            ),
            "passed_percent": round(
                sum(bool(row["passed"]) for row in values) * 100 / len(values), 6
            ),
        }
    return {
        "cases": len(scored),
        "metrics": metrics,
        "strata": strata,
        "role": "public_synthetic_diagnostic_not_blind_gate",
        "blind_accessed": False,
    }


def _execute_preblind(
    context: Mapping[str, Any], repo_root: Path
) -> dict[str, dict[str, Any]]:
    cases = _load_nonsealed_cases(context, repo_root)
    stateful_cases, system_prompt = _load_stateful_cases(context, repo_root)
    model_stateful_cases = [
        {
            **case,
            "case_key": hashlib.sha256(str(case["case_id"]).encode()).hexdigest(),
            "model_messages": [
                {"role": "system", "content": system_prompt},
                *case["messages"],
            ],
        }
        for case in stateful_cases
    ]
    results: dict[str, dict[str, Any]] = {}
    for model_name in context["config"]["generation"]["model_order"]:
        model_root = context["private_root"] / "preblind" / MODEL_SLUGS[model_name]
        summary_path = model_root / "summary.json"
        if summary_path.exists():
            summary = _load_json(summary_path, f"{model_name} preblind summary")
            if (
                summary.get("model") != model_name
                or summary.get("evaluation_build_id") != context["evaluation_build_id"]
                or summary.get("build_sha256") != context["build_sha256"]
                or summary.get("blind_accessed") is not False
            ):
                raise Phase6TechnicalError(f"{model_name} 기존 preblind summary가 다릅니다.")
            results[model_name] = summary
            continue
        torch = transformers = tokenizer = model = None
        try:
            if model_name == "KI10-MIX-v2":
                nonsealed_generations = _reuse_ki10_nonsealed(context, repo_root, cases)
            else:
                torch, transformers, tokenizer, model = _load_model(
                    context, repo_root, model_name
                )
                nonsealed_generations = _generate_cases(
                    torch=torch,
                    tokenizer=tokenizer,
                    model=model,
                    cases=cases,
                    output_path=model_root / "nonsealed_generations.jsonl",
                    batch_size=context["config"]["generation"]["nonsealed_batch_size"],
                    max_new_tokens=context["config"]["generation"][
                        "nonsealed_max_new_tokens"
                    ],
                    message_key="prompt_messages",
                    progress_event=f"phase6_preblind_nonsealed_{MODEL_SLUGS[model_name]}",
                )
            nonsealed_summary = _score_nonsealed(cases, nonsealed_generations)
            if model_name == "KI20-MIX-v2":
                stateful_generations = _reuse_ki20_stateful(
                    context, repo_root, stateful_cases
                )
            else:
                if model is None:
                    torch, transformers, tokenizer, model = _load_model(
                        context, repo_root, model_name
                    )
                stateful_generations = _generate_cases(
                    torch=torch,
                    tokenizer=tokenizer,
                    model=model,
                    cases=model_stateful_cases,
                    output_path=model_root / "stateful_generations.jsonl",
                    batch_size=context["config"]["generation"]["stateful_batch_size"],
                    max_new_tokens=context["config"]["generation"][
                        "stateful_max_new_tokens"
                    ],
                    message_key="model_messages",
                    progress_event=f"phase6_preblind_stateful_{MODEL_SLUGS[model_name]}",
                )
            stateful_summary = _score_stateful(stateful_cases, stateful_generations)
            summary = {
                "schema_version": "1.0.0",
                "model": model_name,
                "evaluation_build_id": context["evaluation_build_id"],
                "build_sha256": context["build_sha256"],
                "nonsealed_gate_v2": nonsealed_summary,
                "stateful_public_synthetic": stateful_summary,
                "runtime": {
                    "torch": getattr(torch, "__version__", "reused") if torch else "reused",
                    "transformers": getattr(transformers, "__version__", "reused")
                    if transformers
                    else "reused",
                },
                "blind_accessed": False,
                "quality_result_blocks_blind_execution": False,
            }
            _write_once(summary_path, _json_bytes(summary), mode=PRIVATE_FILE_MODE)
            results[model_name] = summary
        finally:
            if model is not None and torch is not None:
                _unload_model(torch, model)
    if set(results) != set(MODEL_SLUGS):
        raise Phase6TechnicalError("세 모델의 preblind 결과가 완성되지 않았습니다.")
    return results


@contextmanager
def _execution_lock(private_root: Path) -> Iterator[None]:
    private_root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    private_root.chmod(PRIVATE_DIR_MODE)
    path = private_root / "execution.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, PRIVATE_FILE_MODE)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Phase6TechnicalError("다른 Phase 6 실행이 진행 중입니다.") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _consumption_identity(
    context: Mapping[str, Any], repo_root: Path
) -> dict[str, Any]:
    preblind_hashes: dict[str, str] = {}
    for model_name, slug in MODEL_SLUGS.items():
        path = context["private_root"] / "preblind" / slug / "summary.json"
        if not path.is_file() or path.is_symlink():
            raise Phase6TechnicalError(
                f"blind 시작 전 {model_name} preblind summary가 없습니다."
            )
        preblind_hashes[model_name] = _sha256_file(path)
    return {
        "schema_version": "1.0.0",
        "evaluation_id": context["config"]["evaluation_id"],
        "evaluation_version": context["config"]["evaluation_version"],
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "config_sha256": context["config_sha256"],
        "implementation_hashes": context["implementation_hashes"],
        "model_file_hashes": {
            name: model["required_files"]
            for name, model in context["config"]["models"].items()
        },
        "blind_source_sha256": context["config"]["blind_source"]["sha256"],
        "preblind_summary_hashes": preblind_hashes,
        "git_commit": _git_output(repo_root, "rev-parse", "HEAD"),
    }


def _assert_same_consumption_identity(
    existing: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    for key, value in expected.items():
        if existing.get(key) != value:
            raise Phase6TechnicalError(
                f"봉인 평가 재개 fingerprint가 다릅니다: {key}"
            )


def _begin_blind_consumption(
    context: Mapping[str, Any], repo_root: Path
) -> dict[str, Any]:
    identity = _consumption_identity(context, repo_root)
    payload = {
        **identity,
        "status": "spent_in_progress",
        "started_at_utc": _now_utc(),
        "maximum_evaluation_runs": 1,
    }
    global_path = _safe_path(
        repo_root, context["config"]["blind_source"]["consumption_marker"]
    )
    local_path = context["private_root"] / "blind_access_started.json"
    for path in (global_path, local_path):
        if path.exists():
            existing = _load_json(path, "blind consumption marker")
            _assert_same_consumption_identity(existing, identity)
            if existing.get("status") != "spent_in_progress":
                raise Phase6TechnicalError("blind 시작 marker 상태가 다릅니다.")
        else:
            _write_once(path, _json_bytes(payload), mode=PRIVATE_FILE_MODE)
    return payload


def _read_blind_rows(context: Mapping[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    blind = context["config"]["blind_source"]
    path = _safe_path(repo_root, blind["path"])
    if _sha256_file(path) != blind["sha256"]:
        raise Phase6TechnicalError("봉인 blind payload hash가 다릅니다.")
    return _read_jsonl(path, "sealed blind 500")


def _validate_blind_rows(
    rows: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> list[dict[str, Any]]:
    blind = context["config"]["blind_source"]
    if len(rows) != blind["rows"]:
        raise Phase6TechnicalError("봉인 blind 행 수가 500이 아닙니다.")
    cases: list[dict[str, Any]] = []
    raw_components: dict[str, set[str]] = defaultdict(set)
    axis_counts: Counter[str] = Counter()
    seen_case_keys: set[str] = set()
    for row in rows:
        if (
            row.get("schema_version") != "2.0.0"
            or row.get("split_role") != "blind_source_test"
            or row.get("sealed") is not True
            or row.get("source_axis") not in AXES
            or not isinstance(row.get("eval_id"), str)
        ):
            raise Phase6TechnicalError("봉인 blind row envelope이 다릅니다.")
        item_cases = row.get("cases")
        parents = row.get("parents")
        if not isinstance(item_cases, list) or len(item_cases) != 1:
            raise Phase6TechnicalError("봉인 blind row당 case는 정확히 1개여야 합니다.")
        if not isinstance(parents, list) or not parents:
            raise Phase6TechnicalError("봉인 blind parent가 없습니다.")
        component_values = {parent.get("leakage_component_id") for parent in parents}
        if len(component_values) != 1 or None in component_values:
            raise Phase6TechnicalError("봉인 blind component parent가 일치하지 않습니다.")
        component_id = str(next(iter(component_values)))
        if any(parent.get("mix_axis") != row["source_axis"] for parent in parents):
            raise Phase6TechnicalError("봉인 blind parent 축이 다릅니다.")
        case = item_cases[0]
        prompt_messages = case.get("prompt_messages")
        reference = case.get("reference_assistant")
        case_id = case.get("case_id")
        if (
            not isinstance(case_id, str)
            or not isinstance(prompt_messages, list)
            or not prompt_messages
            or not isinstance(reference, str)
            or not reference.strip()
            or any(
                not isinstance(message, Mapping)
                or message.get("role") not in {"system", "user", "assistant"}
                or not isinstance(message.get("content"), str)
                for message in prompt_messages
            )
        ):
            raise Phase6TechnicalError("봉인 blind case 형식이 다릅니다.")
        key = _case_key(row["eval_id"], case_id)
        if key in seen_case_keys:
            raise Phase6TechnicalError("봉인 blind case identity가 중복됐습니다.")
        seen_case_keys.add(key)
        component_key = hashlib.sha256(
            f"{context['evaluation_build_id']}|{component_id}".encode()
        ).hexdigest()
        axis = str(row["source_axis"])
        axis_counts[axis] += 1
        raw_components[axis].add(component_id)
        cases.append(
            {
                "case_key": key,
                "component_key": component_key,
                "axis": axis,
                "prompt_messages": prompt_messages,
                "reference_assistant": reference,
                "full_messages": [
                    *prompt_messages,
                    {"role": "assistant", "content": reference},
                ],
            }
        )
    if dict(axis_counts) != blind["expected_rows_by_axis"]:
        raise Phase6TechnicalError(f"봉인 blind 축별 행 수가 다릅니다: {dict(axis_counts)}")
    if any(len(values) != blind["components_per_axis"] for values in raw_components.values()):
        raise Phase6TechnicalError("봉인 blind 축별 component 수가 다릅니다.")
    bazi_component_rows = Counter(
        case["component_key"] for case in cases if case["axis"] == "bazi_sft"
    )
    if set(bazi_component_rows.values()) != {blind["bazi_rows_per_component"]}:
        raise Phase6TechnicalError("BaZi blind component당 4행 계약이 다릅니다.")
    for axis in set(AXES) - {"bazi_sft"}:
        values = Counter(
            case["component_key"] for case in cases if case["axis"] == axis
        )
        if set(values.values()) != {1}:
            raise Phase6TechnicalError(f"{axis} blind component당 행 수가 다릅니다.")
    return cases


def _score_blind_model(
    *,
    cases: Sequence[Mapping[str, Any]],
    generations: Sequence[Mapping[str, Any]],
    likelihoods: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    generation_by_key = {row.get("case_key"): row for row in generations}
    likelihood_by_key = {row.get("case_key"): row for row in likelihoods}
    if len(generation_by_key) != len(cases) or len(likelihood_by_key) != len(cases):
        raise Phase6TechnicalError("blind generation·likelihood identity 수가 다릅니다.")
    scored: list[dict[str, Any]] = []
    for case in cases:
        generated = generation_by_key.get(case["case_key"])
        likelihood = likelihood_by_key.get(case["case_key"])
        if generated is None or likelihood is None:
            raise Phase6TechnicalError("blind generation 또는 likelihood가 누락됐습니다.")
        try:
            scoring = score_generation(
                source_axis=case["axis"],
                prompt_messages=case["prompt_messages"],
                reference_assistant=case["reference_assistant"],
                output=generated["output"],
                max_token_hit=generated["max_token_hit"],
            )
        except Phase6ScoringError as exc:
            raise Phase6TechnicalError("blind 자동 scorer가 실패했습니다.") from exc
        scored.append(
            {
                "schema_version": "1.0.0",
                "case_key": case["case_key"],
                "component_key": case["component_key"],
                "axis": case["axis"],
                "generation": {
                    "input_tokens": generated["input_tokens"],
                    "new_tokens": generated["new_tokens"],
                    "eos_seen": generated["eos_seen"],
                    "max_token_hit": generated["max_token_hit"],
                    "output": generated["output"],
                },
                "likelihood": {
                    "tokens": likelihood["tokens"],
                    "nll_sum": likelihood["nll_sum"],
                    "correct": likelihood["correct"],
                    "nll": likelihood["nll"],
                    "token_accuracy": likelihood["token_accuracy"],
                },
                "scoring": scoring,
            }
        )
    try:
        aggregate = aggregate_model_records(scored)
    except Phase6ScoringError as exc:
        raise Phase6TechnicalError("blind model 집계가 실패했습니다.") from exc
    return scored, aggregate


def _load_repair_cases(
    context: Mapping[str, Any], repo_root: Path
) -> list[dict[str, Any]]:
    contract = context["config"]["repair_ranking"]
    manifest = _hashed_rows(repo_root, contract["manifest"], "repair manifest")
    if len(manifest) != contract["manifest"]["rows"]:
        raise Phase6TechnicalError("repair manifest 행 수가 다릅니다.")
    wanted = {row.get("id"): row for row in manifest}
    if len(wanted) != len(manifest) or None in wanted:
        raise Phase6TechnicalError("repair manifest ID가 비었거나 중복됐습니다.")
    staging_root = _safe_path(repo_root, contract["staging_root"])
    record_root = staging_root / "records"
    if record_root.is_symlink() or not record_root.is_dir():
        raise Phase6TechnicalError("repair staging record 경로가 없습니다.")
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(record_root.glob("*.jsonl")):
        if path.is_symlink():
            raise Phase6TechnicalError("repair staging record에 symlink가 있습니다.")
        for row in _read_jsonl(path, f"repair staging {path.name}"):
            record_id = row.get("id")
            if record_id in wanted:
                records[record_id] = row
    if set(records) != set(wanted):
        raise Phase6TechnicalError("repair manifest와 staging record membership이 다릅니다.")
    cases: list[dict[str, Any]] = []
    for manifest_row in manifest:
        record_id = manifest_row["id"]
        record = records[record_id]
        messages = record.get("messages")
        if (
            record.get("mix_axis") != manifest_row.get("mix_axis")
            or not isinstance(messages, list)
            or not messages
            or messages[-1].get("role") != "assistant"
        ):
            raise Phase6TechnicalError("repair staging record 계약이 다릅니다.")
        component = str(manifest_row.get("leakage_component_id"))
        cases.append(
            {
                "case_key": hashlib.sha256(
                    f"repair|{context['evaluation_build_id']}|{record_id}".encode()
                ).hexdigest(),
                "component_key": hashlib.sha256(
                    f"repair|{context['evaluation_build_id']}|{component}".encode()
                ).hexdigest(),
                "axis": manifest_row["mix_axis"],
                "full_messages": messages,
                "private_identity": record_id,
            }
        )
    return cases


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise Phase6TechnicalError("빈 percentile 입력입니다.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 9)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 9)


def _repair_summary(
    rows: Sequence[Mapping[str, Any]], private_root: Path
) -> dict[str, Any]:
    if len(rows) != 20_000:
        raise Phase6TechnicalError("repair likelihood 결과가 20,000행이 아닙니다.")
    ranking = sorted(rows, key=lambda row: float(row["nll"]), reverse=True)
    ranking_rows = [
        {
            "rank": index,
            "record_id": row["private_identity"],
            "axis": row["axis"],
            "nll": row["nll"],
            "token_accuracy": row["token_accuracy"],
            "tokens": row["tokens"],
        }
        for index, row in enumerate(ranking, 1)
    ]
    ranking_path = private_root / "repair_ranking_20000.jsonl"
    if ranking_path.exists():
        existing = _read_jsonl(ranking_path, "repair ranking")
        if existing != ranking_rows:
            raise Phase6TechnicalError("기존 repair ranking이 재현되지 않습니다.")
    else:
        _append_jsonl(ranking_path, ranking_rows)
    axes: dict[str, dict[str, Any]] = {}
    for axis in AXES:
        values = [float(row["nll"]) for row in rows if row["axis"] == axis]
        axes[axis] = {
            "rows": len(values),
            "mean_nll": round(sum(values) / len(values), 9),
            "p50_nll": _percentile(values, 0.50),
            "p90_nll": _percentile(values, 0.90),
            "p95_nll": _percentile(values, 0.95),
            "p99_nll": _percentile(values, 0.99),
            "max_nll": round(max(values), 9),
        }
    return {
        "model": "KI20-MIX-v2",
        "rows": len(rows),
        "purpose": "automatic_private_repair_priority",
        "blind_rows_included": False,
        "public_record_identifiers": False,
        "axis_percentiles": axes,
    }


def _execute_blind_models(
    context: Mapping[str, Any], repo_root: Path, cases: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    repair: dict[str, Any] | None = None
    generation = context["config"]["generation"]
    for model_name in generation["model_order"]:
        model_root = context["private_root"] / "blind" / MODEL_SLUGS[model_name]
        aggregate_path = model_root / "aggregate.json"
        torch = tokenizer = model = None
        try:
            torch, _transformers, tokenizer, model = _load_model(
                context, repo_root, model_name
            )
            likelihoods = _evaluate_likelihood(
                torch=torch,
                tokenizer=tokenizer,
                model=model,
                cases=cases,
                output_path=model_root / "likelihood.jsonl",
                batch_size=generation["likelihood_batch_size"],
                max_length=generation["formal_max_length"],
                progress_event=f"phase6_blind_likelihood_{MODEL_SLUGS[model_name]}",
            )
            generations = _generate_cases(
                torch=torch,
                tokenizer=tokenizer,
                model=model,
                cases=cases,
                output_path=model_root / "generations.jsonl",
                batch_size=generation["blind_batch_size"],
                max_new_tokens=generation["blind_max_new_tokens"],
                message_key="prompt_messages",
                progress_event=f"phase6_blind_generation_{MODEL_SLUGS[model_name]}",
            )
            scored, aggregate = _score_blind_model(
                cases=cases, generations=generations, likelihoods=likelihoods
            )
            scored_path = model_root / "scored_records.jsonl"
            if scored_path.exists():
                if _read_jsonl(scored_path, f"{model_name} scored") != scored:
                    raise Phase6TechnicalError(f"{model_name} scored 결과가 재현되지 않습니다.")
            else:
                _append_jsonl(scored_path, scored)
            envelope = {
                "schema_version": "1.0.0",
                "model": model_name,
                "results": aggregate,
                "blind_status": "spent_completed_for_model",
            }
            if aggregate_path.exists():
                if _load_json(aggregate_path, f"{model_name} blind aggregate") != envelope:
                    raise Phase6TechnicalError(
                        f"{model_name} 기존 blind aggregate가 재현되지 않습니다."
                    )
            else:
                _write_once(
                    aggregate_path, _json_bytes(envelope), mode=PRIVATE_FILE_MODE
                )
            aggregates[model_name] = aggregate
            if model_name == context["config"]["repair_ranking"]["model"]:
                repair_cases = _load_repair_cases(context, repo_root)
                repair_root = context["private_root"] / "repair"
                repair_rows = _evaluate_likelihood(
                    torch=torch,
                    tokenizer=tokenizer,
                    model=model,
                    cases=repair_cases,
                    output_path=repair_root / "likelihood_20000.jsonl",
                    batch_size=generation["likelihood_batch_size"],
                    max_length=generation["formal_max_length"],
                    progress_event="phase6_repair_likelihood_ki20",
                )
                repair = _repair_summary(repair_rows, repair_root)
                _write_once(
                    repair_root / "summary.json",
                    _json_bytes(repair),
                    mode=PRIVATE_FILE_MODE,
                )
        finally:
            if model is not None and torch is not None:
                _unload_model(torch, model)
    if set(aggregates) != set(MODEL_SLUGS) or repair is None:
        raise Phase6TechnicalError("blind 세 모델 또는 repair 집계가 완성되지 않았습니다.")
    return aggregates, repair


def _public_leak_scan(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in PUBLIC_FORBIDDEN_KEYS:
                raise Phase6TechnicalError(f"공개 report 금지 key가 있습니다: {path}.{key}")
            _public_leak_scan(nested, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _public_leak_scan(nested, f"{path}[{index}]")
        return
    if isinstance(value, str) and any(marker in value for marker in PUBLIC_FORBIDDEN_STRING_MARKERS):
        raise Phase6TechnicalError(f"공개 report에 private 경로가 있습니다: {path}")


def _public_preblind(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "nonsealed_gate_v2": summary["nonsealed_gate_v2"],
        "stateful_public_synthetic": summary["stateful_public_synthetic"],
        "quality_result_blocks_blind_execution": False,
    }


def _decision_markdown(aggregate: Mapping[str, Any]) -> str:
    decision = aggregate["baseline_decision"]
    lines = [
        "# Phase 6 자동 기술평가 결정",
        "",
        f"- 평가 build: `{aggregate['evaluation_build_id']}`",
        f"- 상태: `{aggregate['status']}`",
        f"- baseline 결정: `{decision['decision']}`",
        "- 결정 입력: 저장소 내부 자동 기술 지표만 사용",
        "- 의미 품질: `not_measured`이며 성능 주장에 사용하지 않음",
        "- release·앱 연결·MIX20K-v3.1 생성·추가 학습: 모두 미승인",
        "",
        "## 모델별 자동 Gate",
        "",
    ]
    for name in ("K0-INSTRUCT", "KI10-MIX-v2", "KI20-MIX-v2"):
        gate = decision["model_gates"][name]
        lines.append(
            f"- `{name}`: `{gate['status']}`"
            + (f" · 실패 {', '.join(gate['failed_gates'])}" if gate["failed_gates"] else "")
        )
    lines.extend(
        [
            "",
            "## 경계",
            "",
            "이 결정은 baseline 기술 비교를 닫는 결과다. production 적격성, 사주 해석의 의미 정확성, runtime release 승인을 뜻하지 않는다.",
            "",
        ]
    )
    return "\n".join(lines)


def _build_public_outputs(
    context: Mapping[str, Any],
    preblind: Mapping[str, Mapping[str, Any]],
    blind: Mapping[str, Mapping[str, Any]],
    repair: Mapping[str, Any],
) -> dict[str, Any]:
    thresholds = context["config"]["thresholds"]
    gates: dict[str, dict[str, Any]] = {}
    for model_name in context["config"]["generation"]["model_order"]:
        handoff = float(
            preblind[model_name]["nonsealed_gate_v2"]["handoff_action_percent"]
        )
        try:
            gates[model_name] = model_gate(
                blind[model_name], handoff_percent=handoff, thresholds=thresholds
            )
        except Phase6ScoringError as exc:
            raise Phase6TechnicalError("모델 자동 Gate 판정이 실패했습니다.") from exc
    try:
        regression = no_regression(
            blind["KI20-MIX-v2"],
            blind["KI10-MIX-v2"],
            candidate_handoff_percent=float(
                preblind["KI20-MIX-v2"]["nonsealed_gate_v2"][
                    "handoff_action_percent"
                ]
            ),
            baseline_handoff_percent=float(
                preblind["KI10-MIX-v2"]["nonsealed_gate_v2"][
                    "handoff_action_percent"
                ]
            ),
            tolerance_percent_points=float(
                thresholds["no_regression_tolerance_percent_points"]
            ),
        )
        decision = select_baseline(
            ki10_gate=gates["KI10-MIX-v2"],
            ki20_gate=gates["KI20-MIX-v2"],
            ki20_no_regression=regression,
        )
    except Phase6ScoringError as exc:
        raise Phase6TechnicalError("baseline 자동 결정이 실패했습니다.") from exc
    aggregate = {
        "schema_version": "1.0.0",
        "report_type": "phase6_automatic_technical_evaluation",
        "evaluation_version": context["config"]["evaluation_version"],
        "evaluation_build_id": context["evaluation_build_id"],
        "status": "completed",
        "phase6_completed": True,
        "blind_usage": {
            "status": "spent_completed",
            "runs": 1,
            "rows": 500,
            "components": 350,
            "aggregation": "record_then_leakage_component_then_axis_macro",
        },
        "policy": {
            "decision_inputs": "repository_local_automatic_metrics_only",
            "domain_semantics": "not_measured",
            "reference_similarity_used_as_quality_metric": False,
            "unavailable_semantics_blocks_completion": False,
        },
        "preblind": {
            name: _public_preblind(summary) for name, summary in preblind.items()
        },
        "blind_model_results": dict(blind),
        "baseline_decision": {
            "decision": decision,
            "model_gates": gates,
            "ki20_no_regression_vs_ki10": regression,
            "k0_role": "comparator_only",
        },
        "repair_ranking": dict(repair),
        "runtime_conformance": {
            "status": context["config"]["runtime_conformance"]["expected_status"],
            "mixed_into_model_score": False,
        },
        "promotion": {
            "release_approved": False,
            "application_binding_performed": False,
            "mix20k_v3_1_generated": False,
            "additional_training_performed": False,
            "production_promotion_allowed": False,
        },
    }
    _public_leak_scan(aggregate)
    public_root = context["public_root"]
    aggregate_payload = _json_bytes(aggregate)
    decision_payload = _decision_markdown(aggregate).encode("utf-8")
    if not decision_payload.endswith(b"\n"):
        decision_payload += b"\n"
    manifest = {
        "schema_version": "1.0.0",
        "evaluation_build_id": context["evaluation_build_id"],
        "public_files": {
            "aggregate.json": {
                "sha256": hashlib.sha256(aggregate_payload).hexdigest(),
                "bytes": len(aggregate_payload),
            },
            "decision.md": {
                "sha256": hashlib.sha256(decision_payload).hexdigest(),
                "bytes": len(decision_payload),
            },
        },
        "private_content_included": False,
        "raw_outputs_included": False,
    }
    _public_leak_scan(manifest)
    _write_once(public_root / "aggregate.json", aggregate_payload, mode=PUBLIC_FILE_MODE)
    _write_once(public_root / "decision.md", decision_payload, mode=PUBLIC_FILE_MODE)
    _write_once(
        public_root / "build_manifest.json",
        _json_bytes(manifest),
        mode=PUBLIC_FILE_MODE,
    )
    return aggregate


def _private_manifest(context: Mapping[str, Any]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(context["private_root"].rglob("*")):
        if not path.is_file() or path.name in {"execution.lock", "private_manifest.json"}:
            continue
        if path.is_symlink():
            raise Phase6TechnicalError("private output에 symlink가 있습니다.")
        relative = path.relative_to(context["private_root"]).as_posix()
        files[relative] = {"sha256": _sha256_file(path), "bytes": path.stat().st_size}
    return {
        "schema_version": "1.0.0",
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "files": files,
        "blind_status": "spent_completed",
        "public_raw_output_allowed": False,
    }


def execute(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    _confirmation(context["config"])
    with _execution_lock(context["private_root"]):
        completion_path = context["private_root"] / "blind_access_completed.json"
        if completion_path.exists():
            return verify(context, repo_root)
        preflight_result = preflight(context, repo_root, allow_resume=True)
        minimum = context["config"]["runtime"]["minimum_free_gpu_memory_mib"]
        if preflight_result["runtime"]["gpu_free_memory_mib"] < minimum:
            raise Phase6TechnicalError(
                f"평가 전 GPU free memory가 부족합니다: "
                f"{preflight_result['runtime']['gpu_free_memory_mib']} < {minimum} MiB"
            )
        preblind = _execute_preblind(context, repo_root)
        _begin_blind_consumption(context, repo_root)
        blind_rows = _read_blind_rows(context, repo_root)
        blind_cases = _validate_blind_rows(blind_rows, context)
        blind, repair = _execute_blind_models(context, repo_root, blind_cases)
        aggregate = _build_public_outputs(context, preblind, blind, repair)
        private_manifest = _private_manifest(context)
        _write_once(
            context["private_root"] / "private_manifest.json",
            _json_bytes(private_manifest),
            mode=PRIVATE_FILE_MODE,
        )
        completion = {
            **_consumption_identity(context, repo_root),
            "status": "spent_completed",
            "completed_at_utc": _now_utc(),
            "rows": 500,
            "components": 350,
            "models": list(context["config"]["generation"]["model_order"]),
            "baseline_decision": aggregate["baseline_decision"]["decision"],
        }
        _write_once(
            context["private_root"] / "blind_access_completed.json",
            _json_bytes(completion),
            mode=PRIVATE_FILE_MODE,
        )
        return verify(context, repo_root)


def verify(context: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    public_root = context["public_root"]
    aggregate = _load_json(public_root / "aggregate.json", "Phase 6 aggregate")
    manifest = _load_json(public_root / "build_manifest.json", "Phase 6 public manifest")
    _public_leak_scan(aggregate)
    _public_leak_scan(manifest)
    if (
        aggregate.get("evaluation_build_id") != context["evaluation_build_id"]
        or aggregate.get("status") != "completed"
        or aggregate.get("phase6_completed") is not True
        or aggregate.get("blind_usage", {}).get("status") != "spent_completed"
    ):
        raise Phase6TechnicalError("Phase 6 공개 aggregate 상태가 다릅니다.")
    for relative, meta in manifest.get("public_files", {}).items():
        path = public_root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != meta.get("sha256")
            or path.stat().st_size != meta.get("bytes")
        ):
            raise Phase6TechnicalError(f"Phase 6 공개 manifest 검증 실패: {relative}")
    completion = _load_json(
        context["private_root"] / "blind_access_completed.json",
        "blind completion marker",
    )
    _assert_same_consumption_identity(
        completion, _consumption_identity(context, repo_root)
    )
    if completion.get("status") != "spent_completed":
        raise Phase6TechnicalError("blind completion marker가 완료 상태가 아닙니다.")
    private_manifest = _load_json(
        context["private_root"] / "private_manifest.json", "Phase 6 private manifest"
    )
    for relative, meta in private_manifest.get("files", {}).items():
        path = context["private_root"] / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != meta.get("sha256")
            or path.stat().st_size != meta.get("bytes")
        ):
            raise Phase6TechnicalError(f"Phase 6 private manifest 검증 실패: {relative}")
    return {
        "status": "verified",
        "evaluation_version": context["config"]["evaluation_version"],
        "evaluation_build_id": context["evaluation_build_id"],
        "build_sha256": context["build_sha256"],
        "blind_status": "spent_completed",
        "baseline_decision": aggregate["baseline_decision"]["decision"],
        "phase6_completed": True,
        "release_approved": False,
        "application_binding_performed": False,
        "mix20k_v3_1_generated": False,
        "additional_training_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 6 자동 기술평가")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("preflight")
    run = commands.add_parser("execute")
    run.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(_load_json(config_path, "Phase 6 config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "preflight":
                result = preflight(context, REPO_ROOT)
            elif args.command == "execute":
                result = (
                    execute(context, REPO_ROOT)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "evaluation_build_id": context["evaluation_build_id"],
                        "blind_payload_opened": False,
                        "writes_performed": False,
                    }
                )
            else:
                result = verify(context, REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계에서 구조화 오류를 반환한다.
        print(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
