# phase5_train.py - 고정 계약으로 KI10·KI20 Full FT와 KI10 자동 승격 Gate를 실행한다.

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_candidate_staging_records,
    load_json,
    read_jsonl,
    resolve_repo_path,
    runtime_environment,
    sha256_bytes,
    sha256_file,
    sha256_json,
)
from scripts.preflight.phase4_common import (
    prepare_context as prepare_phase4_context,
)
from scripts.training.phase5_quality import (
    Phase5QualityError,
    flatten_items,
    public_summary,
    score_generations,
)

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-training-v1.0.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
RUN_IDS = ("KI10-MIX-v2", "KI20-MIX-v2")


class Phase5TrainingError(RuntimeError):
    """Phase 5 학습·재시작·품질 Gate 계약 위반."""


class _TrainingProbe:
    def __init__(self, torch: Any, metrics_path: Path) -> None:
        self.torch = torch
        self.metrics_path = metrics_path
        self.gradient_probe: dict[str, Any] | None = None
        self.nonfinite_seen = False

    def on_pre_optimizer_step(
        self, args: Any, state: Any, control: Any, **kwargs: Any
    ) -> None:
        del args, state, control
        if self.gradient_probe is not None:
            return
        finite = True
        nonzero = False
        tensors = 0
        elements = 0
        for parameter in kwargs["model"].parameters():
            gradient = parameter.grad
            if gradient is None:
                continue
            tensors += 1
            elements += gradient.numel()
            finite = finite and bool(self.torch.isfinite(gradient).all().item())
            nonzero = nonzero or bool(self.torch.count_nonzero(gradient).item())
        self.gradient_probe = {
            "finite": finite,
            "nonzero": nonzero,
            "tensor_count": tensors,
            "element_count": elements,
            "observed_after_gradient_clip": True,
        }
        if not finite:
            raise Phase5TrainingError("첫 gradient에 NaN/Inf가 있습니다.")

    def on_log(
        self,
        args: Any,
        state: Any,
        control: Any,
        logs: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        del args, control, kwargs
        clean = {
            key: value
            for key, value in logs.items()
            if isinstance(value, (bool, int, float, str)) or value is None
        }
        clean["global_step"] = int(state.global_step)
        for key in ("loss", "eval_loss", "grad_norm"):
            value = clean.get(key)
            if isinstance(value, (int, float)) and not math.isfinite(float(value)):
                self.nonfinite_seen = True
                raise Phase5TrainingError(f"학습 지표 {key}가 NaN/Inf입니다.")
        if self.torch.cuda.is_available():
            clean["gpu_memory_allocated_bytes"] = int(
                self.torch.cuda.memory_allocated(0)
            )
            clean["gpu_memory_reserved_bytes"] = int(
                self.torch.cuda.memory_reserved(0)
            )
            clean["gpu_peak_memory_allocated_bytes"] = int(
                self.torch.cuda.max_memory_allocated(0)
            )
        _append_jsonl(self.metrics_path, clean)


def _make_callback(probe: _TrainingProbe, callback_class: Any) -> Any:
    class TrainingProbeCallback(callback_class):
        def on_pre_optimizer_step(self, *args: Any, **kwargs: Any) -> None:
            return probe.on_pre_optimizer_step(*args, **kwargs)

        def on_log(self, *args: Any, **kwargs: Any) -> None:
            return probe.on_log(*args, **kwargs)

    return TrainingProbeCallback()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise Phase5TrainingError(f"안전하지 않은 Phase 5 경로입니다: {relative}") from exc


def _atomic_replace(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_once(path: Path, payload: bytes, *, mode: int) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise Phase5TrainingError(f"기존 불변 파일과 내용이 다릅니다: {path}")
        return
    _atomic_replace(path, payload, mode=mode)


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8") + b"\n"
    descriptor = os.open(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        PRIVATE_FILE_MODE,
    )
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git_clean(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _git_head(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or len(result.stdout.strip()) != 40:
        raise Phase5TrainingError("Git HEAD를 확인할 수 없습니다.")
    return result.stdout.strip()


def _system_ram() -> dict[str, int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, raw = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                values[name] = int(raw.strip().split()[0]) * 1024
    except (OSError, UnicodeError, ValueError):
        pass
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
    }


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("training_version") != "v1.0.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("canonical_plan_version") != "3.2.0"
    ):
        raise Phase5TrainingError("Phase 5 training identity가 다릅니다.")
    readiness = config.get("required_readiness")
    if readiness != {
        "version": "v1.2.0",
        "config": "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.2.0.json",
        "registry_key": "approved_phase5_readiness",
        "required_status": "ready_for_ki10_execution_with_automated_promotion_gate",
    }:
        raise Phase5TrainingError("Phase 5 readiness 요구 계약이 다릅니다.")
    model = config.get("model")
    if (
        not isinstance(model, dict)
        or model.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or model.get("dtype") != "bfloat16"
        or model.get("attention_backend") != "sdpa"
        or model.get("parameter_count") != 1_291_478_272
        or model.get("local_files_only") is not True
        or model.get("trust_remote_code") is not True
    ):
        raise Phase5TrainingError("Phase 5 모델 계약이 다릅니다.")
    for key in ("local_subdir", "chat_template_path", "package_lock"):
        path = _safe_path(repo_root, str(model.get(key, "")))
        if not path.exists():
            raise Phase5TrainingError(f"Phase 5 모델 입력이 없습니다: {key}")
    for key in ("snapshot_manifest_sha256", "chat_template_sha256", "package_lock_sha256"):
        value = model.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise Phase5TrainingError(f"Phase 5 모델 hash가 다릅니다: {key}")
    if sha256_file(_safe_path(repo_root, model["chat_template_path"])) != model["chat_template_sha256"]:
        raise Phase5TrainingError("Phase 5 chat template hash가 다릅니다.")
    if sha256_file(_safe_path(repo_root, model["package_lock"])) != model["package_lock_sha256"]:
        raise Phase5TrainingError("Phase 5 package lock hash가 다릅니다.")

    data = config.get("data")
    if not isinstance(data, dict) or data.get("canonical_build_id") != "build-6f32d52c2868":
        raise Phase5TrainingError("Phase 5 데이터 부모 계약이 다릅니다.")
    expected_runs = {
        "KI10-MIX-v2": {
            "manifest": "manifests/mix10k_v2.jsonl",
            "rows": 10_000,
            "sha256": "7863270e5a16fe9f92b52e82d45ae0b60f0b68ee3f0003955d662eb0392472e9",
            "expected_optimizer_steps": 1_250,
        },
        "KI20-MIX-v2": {
            "manifest": "manifests/mix20k_v2.jsonl",
            "rows": 20_000,
            "sha256": "731ace0ac5584fd97fc38f157a4ecdb1babedefd79e2ec5b2d755fa26e48a550",
            "expected_optimizer_steps": 2_500,
        },
    }
    if data.get("runs") != expected_runs:
        raise Phase5TrainingError("Phase 5 KI10/KI20 manifest 계약이 다릅니다.")
    _safe_path(repo_root, str(data.get("canonical_root", "")))
    _safe_path(repo_root, str(data.get("preflight_config", "")))

    training = config.get("training")
    expected_training = {
        "max_length": 768,
        "pad_to_multiple_of": 8,
        "per_device_train_batch_size": 1,
        "per_device_eval_batch_size": 1,
        "gradient_accumulation_steps": 8,
        "eval_accumulation_steps": 1,
        "num_train_epochs": 1,
        "optim": "paged_adamw_8bit",
        "learning_rate": 0.000008,
        "warmup_ratio": 0.03,
        "lr_scheduler_type": "cosine",
        "weight_decay": 0.01,
        "max_grad_norm": 1.0,
        "gradient_checkpointing": True,
        "gradient_checkpointing_use_reentrant": False,
        "assistant_only_loss": True,
        "packing": False,
        "padding_free": False,
        "loss_type": "chunked_nll",
        "bf16": True,
        "fp16": False,
        "tf32": False,
        "use_cache": False,
        "logging_steps": 10,
        "logging_nan_inf_filter": False,
        "eval_steps": 250,
        "save_steps": 250,
        "save_total_limit": 2,
        "save_only_model": False,
        "save_safetensors": True,
        "dataloader_num_workers": 0,
        "seed": 42,
        "data_seed": 42,
        "torch_compile": False,
        "report_to": [],
    }
    if training != expected_training:
        raise Phase5TrainingError("Phase 5 고정 하이퍼파라미터가 다릅니다.")
    evaluation = config.get("evaluation")
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("dev_monitor_rows") != 70
        or evaluation.get("diagnostic_cases") != 1_000
        or evaluation.get("generation_max_new_tokens") != 256
        or evaluation.get("blind_source_test_access_allowed") is not False
    ):
        raise Phase5TrainingError("Phase 5 평가 계약이 다릅니다.")
    thresholds = evaluation.get("ki10_promotion_thresholds")
    required_thresholds = {
        "expected_generation_cases": 1_000,
        "parseable_nonempty_min_percent": 98.0,
        "special_or_control_outputs_max": 0,
        "severe_safety_outputs_max": 0,
        "foreign_sentence_max_percent": 3.0,
        "target_only_entity_outputs_max": 0,
        "hard_fact_and_branch_policy_min_percent": 90.0,
        "shensha_condition_and_polarity_min_percent": 90.0,
        "empathy_task_confusion_max_percent": 5.0,
        "missing_chart_handoff_required": 5,
        "input_fact_violation_max_percent": 10.0,
        "persona_causalization_max_percent": 10.0,
    }
    if thresholds != required_thresholds:
        raise Phase5TrainingError("KI10 promotion threshold가 다릅니다.")
    outputs = config.get("outputs")
    if outputs != {
        "run_root": "runs/{run_id}/v1.0.0/{run_build_id}",
        "preflight_root": "runs/PHASE5-PREFLIGHT/v1.0.0/{run_build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-runs/v1.0.0/{run_id}/{run_build_id}",
    }:
        raise Phase5TrainingError("Phase 5 출력 경로 계약이 다릅니다.")
    for value in outputs.values():
        _safe_path(
            repo_root,
            value.format(run_id="KI10-MIX-v2", run_build_id="run-000000000000"),
        )
    if config.get("implementation_files") != [
        "scripts/training/phase5_quality.py",
        "scripts/training/phase5_train.py",
    ]:
        raise Phase5TrainingError("Phase 5 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "training_version": "v1.0.0"}


def _approved_readiness(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    registry = load_json(
        repo_root / "configs/data_versions/saju_1b_baseline/registry.json",
        "dataset registry",
    )
    requirement = config["required_readiness"]
    approved = registry.get(requirement["registry_key"])
    if (
        not isinstance(approved, dict)
        or approved.get("version") != requirement["version"]
        or approved.get("status") != requirement["required_status"]
        or approved.get("training_promotion_allowed") is not True
        or approved.get("phase5_training_performed") is not False
    ):
        raise Phase5TrainingError("registry가 승인된 readiness v1.2를 가리키지 않습니다.")
    from scripts.training.phase5_readiness_v1_2 import (
        prepare_context as prepare_readiness_context,
    )
    from scripts.training.phase5_readiness_v1_2 import verify_readiness

    readiness_context = prepare_readiness_context(
        repo_root, _safe_path(repo_root, requirement["config"])
    )
    result = verify_readiness(readiness_context, repo_root)
    if (
        result.get("build_id") != approved.get("build_id")
        or result.get("build_sha256") != approved.get("build_sha256")
        or result.get("baseline_training_allowed") is not True
        or result.get("ki20_promotion_allowed") is not False
    ):
        raise Phase5TrainingError("readiness v1.2 재검증이 실패했습니다.")
    return {"registry": approved, "context": readiness_context, "verified": result}


def prepare_context(
    repo_root: Path, config_path: Path, run_id: str
) -> dict[str, Any]:
    if run_id not in RUN_IDS:
        raise Phase5TrainingError(f"허용되지 않은 Run ID입니다: {run_id}")
    config = load_json(config_path, "Phase 5 training config")
    validate_contract(config, repo_root)
    readiness = _approved_readiness(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    run_contract = config["data"]["runs"][run_id]
    run_inputs = {
        "training_version": config["training_version"],
        "run_id": run_id,
        "model": config["model"],
        "data_parent": {
            key: config["data"][key]
            for key in ("canonical_version", "canonical_build_id", "canonical_build_sha256")
        },
        "manifest": run_contract,
        "training_sha256": sha256_json(config["training"]),
        "evaluation_sha256": sha256_json(config["evaluation"]),
        "readiness_build_id": readiness["registry"]["build_id"],
        "readiness_build_sha256": readiness["registry"]["build_sha256"],
        "implementation_hashes": implementation_hashes,
    }
    run_sha256 = sha256_json(run_inputs)
    run_build_id = f"run-{run_sha256[:12]}"
    outputs = config["outputs"]
    return {
        "config": config,
        "config_path": config_path,
        "run_id": run_id,
        "run_inputs": run_inputs,
        "run_sha256": run_sha256,
        "run_build_id": run_build_id,
        "run_contract": run_contract,
        "readiness": readiness,
        "run_root": _safe_path(
            repo_root, outputs["run_root"].format(run_id=run_id, run_build_id=run_build_id)
        ),
        "preflight_root": _safe_path(
            repo_root,
            outputs["preflight_root"].format(run_id=run_id, run_build_id=run_build_id),
        ),
        "public_root": _safe_path(
            repo_root, outputs["public_root"].format(run_id=run_id, run_build_id=run_build_id)
        ),
    }


def _phase4_context(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    return prepare_phase4_context(
        repo_root, _safe_path(repo_root, context["config"]["data"]["preflight_config"])
    )


def _training_rows(
    context: dict[str, Any], repo_root: Path
) -> tuple[list[dict[str, Any]], str]:
    phase4_context = _phase4_context(context, repo_root)
    records, _, _, _ = load_candidate_staging_records(phase4_context, repo_root)
    manifest_path = (
        _safe_path(repo_root, context["config"]["data"]["canonical_root"])
        / context["run_contract"]["manifest"]
    )
    if sha256_file(manifest_path) != context["run_contract"]["sha256"]:
        raise Phase5TrainingError("학습 manifest SHA-256이 다릅니다.")
    manifest = read_jsonl(manifest_path, context["run_id"])
    if len(manifest) != context["run_contract"]["rows"]:
        raise Phase5TrainingError("학습 manifest 행 수가 다릅니다.")
    rows: list[dict[str, Any]] = []
    for value in manifest:
        record = records.get(value["id"])
        if (
            record is None
            or record["meta"]["phase4_parent_record_sha256"]
            != value["record_sha256"]
        ):
            raise Phase5TrainingError("학습 manifest/staging record hash가 다릅니다.")
        rows.append({"messages": record["messages"]})
    return rows, sha256_file(manifest_path)


def _dev_monitor_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    readiness_root: Path = context["readiness"]["context"]["private_root"]
    path = readiness_root / "eval/dev_monitor_70.jsonl"
    values = read_jsonl(path, "dev monitor 70")
    rows: list[dict[str, Any]] = []
    for item in values:
        for case in item["cases"]:
            messages = [
                *case["prompt_messages"],
                {"role": "assistant", "content": case["reference_assistant"]},
            ]
            rows.append({"messages": messages})
    if len(rows) != context["config"]["evaluation"]["dev_monitor_rows"]:
        raise Phase5TrainingError("dev monitor 생성 case 수가 다릅니다.")
    return rows


def _load_runtime(
    context: dict[str, Any], repo_root: Path
) -> tuple[Any, Any, Any, Any, Any, Any, Any, dict[str, Any]]:
    phase4_config = _phase4_context(context, repo_root)["config"]
    environment = runtime_environment(phase4_config, repo_root)
    for key in ("CPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = environment[key]
    if os.environ.get("TORCH_DISABLE_NATIVE_JIT"):
        raise Phase5TrainingError("정식 Phase 5에서는 TORCH_DISABLE_NATIVE_JIT를 허용하지 않습니다.")
    try:
        import bitsandbytes
        import datasets
        import torch
        import transformers
        import trl
        from datasets import Dataset
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
        from trl import SFTConfig, SFTTrainer
    except Exception as exc:
        raise Phase5TrainingError("Phase 5 고정 runtime을 import하지 못했습니다.") from exc
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise Phase5TrainingError("Phase 5는 단일 CUDA GPU가 필요합니다.")
    expected_versions = context["config"]["runtime_versions"]
    actual_versions = {
        "torch": torch.__version__,
        "torch_cuda": str(torch.version.cuda),
        "transformers": transformers.__version__,
        "trl": trl.__version__,
        "bitsandbytes": bitsandbytes.__version__,
        "datasets": datasets.__version__,
    }
    if actual_versions != expected_versions:
        raise Phase5TrainingError(f"Phase 5 package runtime이 다릅니다: {actual_versions}")
    model_config = context["config"]["model"]
    snapshot = _safe_path(repo_root, model_config["local_subdir"])
    torch.manual_seed(context["config"]["training"]["seed"])
    torch.cuda.manual_seed_all(context["config"]["training"]["seed"])
    torch.backends.cudnn.benchmark = False
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(0)
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=True
    )
    tokenizer.padding_side = "right"
    if (
        not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode())
        != model_config["chat_template_sha256"]
    ):
        raise Phase5TrainingError("Phase 5 tokenizer/chat template 계약이 다릅니다.")
    model = AutoModelForCausalLM.from_pretrained(
        snapshot,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation=model_config["attention_backend"],
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = False
    parameters = list(model.parameters())
    total = sum(parameter.numel() for parameter in parameters)
    trainable = sum(parameter.numel() for parameter in parameters if parameter.requires_grad)
    if (
        total != model_config["parameter_count"]
        or trainable != total
        or any(parameter.dtype != torch.bfloat16 for parameter in parameters)
    ):
        raise Phase5TrainingError("Phase 5 Full BF16 parameter 계약이 다릅니다.")
    runtime = {
        **actual_versions,
        "python": str(Path(sys.executable).resolve()),
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_capability": list(torch.cuda.get_device_capability(0)),
        "vram_total_bytes": int(torch.cuda.get_device_properties(0).total_memory),
        "parameter_count": total,
        "trainable_parameter_count": trainable,
        "parameter_dtype": "torch.bfloat16",
        "attention_backend": model_config["attention_backend"],
    }
    return torch, Dataset, TrainerCallback, SFTConfig, SFTTrainer, model, tokenizer, runtime


def _sft_args(
    context: dict[str, Any], sft_config: Any, output_dir: Path, *, train: bool
) -> Any:
    values = context["config"]["training"]
    return sft_config(
        output_dir=str(output_dir),
        overwrite_output_dir=False,
        do_train=train,
        do_eval=True,
        per_device_train_batch_size=values["per_device_train_batch_size"],
        per_device_eval_batch_size=values["per_device_eval_batch_size"],
        gradient_accumulation_steps=values["gradient_accumulation_steps"],
        eval_accumulation_steps=values["eval_accumulation_steps"],
        num_train_epochs=values["num_train_epochs"],
        learning_rate=values["learning_rate"],
        weight_decay=values["weight_decay"],
        max_grad_norm=values["max_grad_norm"],
        warmup_ratio=values["warmup_ratio"],
        lr_scheduler_type=values["lr_scheduler_type"],
        optim=values["optim"],
        logging_strategy="steps" if train else "no",
        logging_steps=values["logging_steps"],
        logging_first_step=True,
        logging_nan_inf_filter=values["logging_nan_inf_filter"],
        eval_strategy="steps" if train else "no",
        eval_steps=values["eval_steps"],
        save_strategy="steps" if train else "no",
        save_steps=values["save_steps"],
        save_total_limit=values["save_total_limit"],
        save_only_model=values["save_only_model"],
        save_safetensors=values["save_safetensors"],
        seed=values["seed"],
        data_seed=values["data_seed"],
        bf16=values["bf16"],
        fp16=values["fp16"],
        tf32=values["tf32"],
        gradient_checkpointing=values["gradient_checkpointing"],
        gradient_checkpointing_kwargs={
            "use_reentrant": values["gradient_checkpointing_use_reentrant"]
        },
        report_to=values["report_to"],
        dataloader_num_workers=values["dataloader_num_workers"],
        dataloader_pin_memory=True,
        remove_unused_columns=True,
        skip_memory_metrics=True,
        max_length=values["max_length"],
        pad_to_multiple_of=values["pad_to_multiple_of"],
        assistant_only_loss=values["assistant_only_loss"],
        packing=values["packing"],
        padding_free=values["padding_free"],
        loss_type=values["loss_type"],
        shuffle_dataset=True,
        dataset_num_proc=1,
        trust_remote_code=True,
        torch_compile=values["torch_compile"],
        use_liger_kernel=False,
        activation_offloading=False,
        load_best_model_at_end=False,
    )


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=PRIVATE_DIR_MODE)
    path.chmod(PRIVATE_DIR_MODE)


def _preflight_manifest(context: dict[str, Any], summary: bytes) -> bytes:
    return _json_bytes(
        {
            "schema_version": "1.0.0",
            "report_type": "phase5_forward_preflight",
            "run_id": context["run_id"],
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "artifact_sha256": {"summary.json": hashlib.sha256(summary).hexdigest()},
            "status": "passed",
            "backward_performed": False,
            "optimizer_step_performed": False,
            "phase5_training_performed": False,
        }
    )


def preflight_run(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["preflight_root"]
    if root.exists():
        return {**verify_preflight(context), "mode": "reused"}
    if not _git_clean(repo_root):
        raise Phase5TrainingError("Phase 5 forward preflight 전 working tree가 깨끗해야 합니다.")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent)
    )
    temporary_root.chmod(PRIVATE_DIR_MODE)
    (
        torch,
        dataset_class,
        _,
        sft_config,
        sft_trainer,
        model,
        tokenizer,
        runtime,
    ) = _load_runtime(context, repo_root)
    started = time.monotonic()
    trainer: Any | None = None
    try:
        dataset = dataset_class.from_list(_dev_monitor_rows(context))
        args = _sft_args(
            context, sft_config, temporary_root / "trainer", train=False
        )
        trainer = sft_trainer(
            model=model,
            args=args,
            eval_dataset=dataset,
            processing_class=tokenizer,
        )
        metrics = trainer.evaluate()
        loss = float(metrics.get("eval_loss", math.nan))
        if not math.isfinite(loss):
            raise Phase5TrainingError("Phase 5 forward preflight eval_loss가 NaN/Inf입니다.")
        summary_value = {
            "schema_version": "1.0.0",
            "status": "passed",
            "run_id": context["run_id"],
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "dev_monitor_rows": 70,
            "eval_loss": loss,
            "runtime": runtime,
            "peak_vram_bytes": int(torch.cuda.max_memory_allocated(0)),
            "vram_free_bytes": int(torch.cuda.mem_get_info(0)[0]),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "assistant_only_loss": True,
            "max_length": 768,
            "backward_performed": False,
            "optimizer_step_performed": False,
            "phase5_training_performed": False,
        }
        summary = _json_bytes(summary_value)
        _write_once(
            temporary_root / "summary.json", summary, mode=PRIVATE_FILE_MODE
        )
        _write_once(
            temporary_root / "run_manifest.json",
            _preflight_manifest(context, summary),
            mode=PRIVATE_FILE_MODE,
        )
        shutil.rmtree(temporary_root / "trainer", ignore_errors=True)
        os.replace(temporary_root, root)
        return {**verify_preflight(context), "mode": "built"}
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        del trainer
        del model
        gc.collect()
        torch.cuda.empty_cache()


def verify_preflight(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["preflight_root"]
    if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise Phase5TrainingError("Phase 5 forward preflight가 없습니다.")
    summary_path = root / "summary.json"
    manifest_path = root / "run_manifest.json"
    summary = load_json(summary_path, "Phase 5 forward preflight summary")
    if (
        summary.get("status") != "passed"
        or summary.get("run_build_id") != context["run_build_id"]
        or summary.get("backward_performed") is not False
        or summary.get("optimizer_step_performed") is not False
        or not math.isfinite(float(summary.get("eval_loss", math.nan)))
    ):
        raise Phase5TrainingError("Phase 5 forward preflight 결과가 다릅니다.")
    if manifest_path.read_bytes() != _preflight_manifest(context, summary_path.read_bytes()):
        raise Phase5TrainingError("Phase 5 forward preflight manifest가 다릅니다.")
    return {
        "status": "verified_forward_preflight",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "eval_loss": summary["eval_loss"],
        "phase5_training_performed": False,
        "writes_performed": False,
    }


def _ki10_gate_summary(context: dict[str, Any]) -> dict[str, Any]:
    path = context["public_root"] / "ki10_quality_gate.json"
    if not path.is_file():
        raise Phase5TrainingError("KI20 시작 전 KI10 자동 품질 Gate가 없습니다.")
    value = load_json(path, "KI10 quality gate")
    if value.get("ki20_promotion_allowed") is not True or value.get("status") != "passed":
        raise Phase5TrainingError("KI10 자동 품질 Gate가 KI20을 허용하지 않았습니다.")
    return value


def _confirmation(context: dict[str, Any]) -> None:
    if os.environ.get("PHASE5_TRAINING") != context["run_id"]:
        raise Phase5TrainingError(
            f"PHASE5_TRAINING={context['run_id']} 확인값이 필요합니다."
        )


def _checkpoint_inventory(root: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise Phase5TrainingError("checkpoint에 symlink가 있습니다.")
        if stat.S_ISDIR(info.st_mode):
            path.chmod(PRIVATE_DIR_MODE)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise Phase5TrainingError("checkpoint에 special file이 있습니다.")
        path.chmod(PRIVATE_FILE_MODE)
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": info.st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "file_count": len(files),
        "total_bytes": sum(value["bytes"] for value in files),
        "files": files,
    }


def _loss_summary(logs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    losses = [float(value["loss"]) for value in logs if isinstance(value.get("loss"), (int, float))]
    gradients = [float(value["grad_norm"]) for value in logs if isinstance(value.get("grad_norm"), (int, float))]
    return {
        "logged_train_losses": len(losses),
        "losses_finite": bool(losses) and all(math.isfinite(value) for value in losses),
        "grad_norms_finite": bool(gradients) and all(math.isfinite(value) for value in gradients),
        "minimum_loss": min(losses) if losses else None,
        "maximum_loss": max(losses) if losses else None,
        "final_loss": losses[-1] if losses else None,
    }


def _read_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path, path.name) if path.is_file() else []


def _initial_manifest(context: dict[str, Any], repo_root: Path, manifest_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "report_type": "phase5_full_ft_run",
        "status": "running",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "run_inputs": context["run_inputs"],
        "workspace_commit": _git_head(repo_root),
        "working_tree_clean_at_start": True,
        "manifest_sha256": manifest_sha256,
        "initial_checkpoint_is_fixed_instruct": True,
        "independent_from_other_run": True,
        "phase5_training_performed": True,
    }


def _training_summary_public(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "schema_version",
            "status",
            "run_id",
            "run_build_id",
            "run_sha256",
            "workspace_commit",
            "manifest_sha256",
            "rows",
            "optimizer_steps",
            "expected_optimizer_steps",
            "training_loss",
            "loss_summary",
            "gradient_probe",
            "runtime",
            "peak_vram_bytes",
            "vram_free_bytes_at_finish",
            "system_ram",
            "elapsed_seconds",
            "resumed_from_checkpoint",
            "final_reload_passed",
            "phase5_training_performed",
            "raw_samples_in_report",
        )
    }


def _public_run_manifest(
    context: dict[str, Any], summary_payload: bytes
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "report_type": "phase5_full_ft_public_manifest",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "artifact_sha256": {
            "training_summary.json": hashlib.sha256(summary_payload).hexdigest()
        },
        "status": "trained_and_reloaded",
        "quality_gate_evaluated": False,
        "phase5_training_performed": True,
        "raw_samples_in_report": False,
    }


def _publish_run(context: dict[str, Any], summary: dict[str, Any]) -> None:
    root: Path = context["public_root"]
    if root.exists():
        raise Phase5TrainingError("기존 Phase 5 공개 run 보고서를 덮어쓸 수 없습니다.")
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{root.name}.", dir=root.parent)
    )
    public = _training_summary_public(summary)
    summary_payload = _json_bytes(public)
    try:
        _write_once(
            temporary / "training_summary.json",
            summary_payload,
            mode=PUBLIC_FILE_MODE,
        )
        _write_once(
            temporary / "build_manifest.json",
            _json_bytes(_public_run_manifest(context, summary_payload)),
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(temporary, root)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _train(
    context: dict[str, Any], repo_root: Path, *, resume_checkpoint: Path | None
) -> dict[str, Any]:
    _confirmation(context)
    verify_preflight(context)
    if context["run_id"] == "KI20-MIX-v2":
        ki10_context = prepare_context(
            repo_root, context["config_path"], "KI10-MIX-v2"
        )
        _ki10_gate_summary(ki10_context)
    root: Path = context["run_root"]
    if resume_checkpoint is None:
        if root.exists() or context["public_root"].exists():
            raise Phase5TrainingError("기존 Phase 5 Run을 덮어쓸 수 없습니다.")
        if not _git_clean(repo_root):
            raise Phase5TrainingError("Phase 5 학습 시작 전 working tree가 깨끗해야 합니다.")
        rows, manifest_sha256 = _training_rows(context, repo_root)
        _private_dir(root)
        _atomic_replace(
            root / "run_manifest.json",
            _json_bytes(_initial_manifest(context, repo_root, manifest_sha256)),
            mode=PRIVATE_FILE_MODE,
        )
        _write_once(
            root / "config.resolved.json",
            _json_bytes(context["config"]),
            mode=PRIVATE_FILE_MODE,
        )
    else:
        if root.is_symlink() or not root.is_dir():
            raise Phase5TrainingError("resume 대상 Run이 없습니다.")
        rows, manifest_sha256 = _training_rows(context, repo_root)
        manifest = load_json(root / "run_manifest.json", "resume run manifest")
        if (
            manifest.get("run_sha256") != context["run_sha256"]
            or manifest.get("manifest_sha256") != manifest_sha256
            or manifest.get("status") not in {"running", "interrupted", "failed"}
        ):
            raise Phase5TrainingError("resume Run identity가 다릅니다.")
        if (
            resume_checkpoint.is_symlink()
            or not resume_checkpoint.is_dir()
            or resume_checkpoint.parent != root
            or not (resume_checkpoint / "optimizer.pt").is_file()
            or not (resume_checkpoint / "scheduler.pt").is_file()
            or not (resume_checkpoint / "trainer_state.json").is_file()
        ):
            raise Phase5TrainingError("resume checkpoint state가 완전하지 않습니다.")

    (
        torch,
        dataset_class,
        callback_class,
        sft_config,
        sft_trainer,
        model,
        tokenizer,
        runtime,
    ) = _load_runtime(context, repo_root)
    trainer: Any | None = None
    started = time.monotonic()
    try:
        dataset = dataset_class.from_list(rows)
        eval_dataset = dataset_class.from_list(_dev_monitor_rows(context))
        probe = _TrainingProbe(torch, root / "metrics.jsonl")
        args = _sft_args(context, sft_config, root, train=True)
        trainer = sft_trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            eval_dataset=eval_dataset,
            processing_class=tokenizer,
            callbacks=[_make_callback(probe, callback_class)],
        )
        output = trainer.train(
            resume_from_checkpoint=(str(resume_checkpoint) if resume_checkpoint else None)
        )
        global_step = int(trainer.state.global_step)
        expected_steps = context["run_contract"]["expected_optimizer_steps"]
        if global_step != expected_steps:
            raise Phase5TrainingError(
                f"optimizer step 수가 다릅니다: {global_step} != {expected_steps}"
            )
        training_loss = float(output.training_loss)
        if not math.isfinite(training_loss) or probe.nonfinite_seen:
            raise Phase5TrainingError("Phase 5 training loss가 NaN/Inf입니다.")
        if not probe.gradient_probe or not probe.gradient_probe["finite"] or not probe.gradient_probe["nonzero"]:
            raise Phase5TrainingError("Phase 5 gradient가 유한한 nonzero가 아닙니다.")
        final_root = root / "final"
        trainer.save_model(str(final_root))
        tokenizer.save_pretrained(final_root)
        trainer.save_state()
        for path in [root, final_root, *[p for p in root.rglob("*") if p.is_dir()]]:
            path.chmod(PRIVATE_DIR_MODE)
        logs = _read_jsonl_if_exists(root / "metrics.jsonl")
        loss_summary = _loss_summary(logs)
        if not loss_summary["losses_finite"] or not loss_summary["grad_norms_finite"]:
            raise Phase5TrainingError("저장된 loss/grad_norm 로그가 유한하지 않습니다.")
        checkpoints = sorted(
            (path for path in root.glob("checkpoint-*") if path.is_dir()),
            key=lambda path: int(path.name.split("-")[-1]),
        )
        if not checkpoints:
            raise Phase5TrainingError("Phase 5 checkpoint가 저장되지 않았습니다.")
        inventories = {
            path.name: _checkpoint_inventory(path) for path in [*checkpoints, final_root]
        }
        _write_once(
            root / "checkpoint_inventory.json",
            _json_bytes(inventories),
            mode=PRIVATE_FILE_MODE,
        )
        del trainer
        trainer = None
        del model
        gc.collect()
        torch.cuda.empty_cache()
        reload_result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--config",
                str(context["config_path"]),
                "reload",
                "--run-id",
                context["run_id"],
            ],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PHASE5_RELOAD_CHILD": "1"},
        )
        if reload_result.returncode != 0:
            raise Phase5TrainingError(
                f"새 프로세스 final reload가 실패했습니다: {reload_result.stderr[-1000:]}"
            )
        reload_summary = load_json(root / "reload_summary.json", "reload summary")
        if reload_summary.get("status") != "passed" or reload_summary.get("nonempty_outputs") != 5:
            raise Phase5TrainingError("새 프로세스 reload/generation 결과가 다릅니다.")
        summary = {
            "schema_version": "1.0.0",
            "status": "trained_and_reloaded",
            "run_id": context["run_id"],
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "workspace_commit": _git_head(repo_root),
            "manifest_sha256": manifest_sha256,
            "rows": len(rows),
            "optimizer_steps": global_step,
            "expected_optimizer_steps": expected_steps,
            "training_loss": training_loss,
            "loss_summary": loss_summary,
            "gradient_probe": probe.gradient_probe,
            "runtime": runtime,
            "peak_vram_bytes": int(torch.cuda.max_memory_allocated(0)),
            "vram_free_bytes_at_finish": int(torch.cuda.mem_get_info(0)[0]),
            "system_ram": _system_ram(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "resumed_from_checkpoint": resume_checkpoint is not None,
            "final_reload_passed": True,
            "phase5_training_performed": True,
            "raw_samples_in_report": False,
        }
        _write_once(root / "training_summary.json", _json_bytes(summary), mode=PRIVATE_FILE_MODE)
        final_manifest = _initial_manifest(context, repo_root, manifest_sha256)
        final_manifest.update(
            {
                "status": "trained_and_reloaded",
                "artifact_sha256": {
                    "training_summary.json": sha256_file(root / "training_summary.json"),
                    "checkpoint_inventory.json": sha256_file(root / "checkpoint_inventory.json"),
                    "reload_summary.json": sha256_file(root / "reload_summary.json"),
                },
                "final_reload_passed": True,
            }
        )
        _atomic_replace(
            root / "run_manifest.json", _json_bytes(final_manifest), mode=PRIVATE_FILE_MODE
        )
        _publish_run(context, summary)
        return verify_run(context)
    except Exception as exc:
        if root.is_dir():
            status = {
                "schema_version": "1.0.0",
                "status": "failed",
                "run_id": context["run_id"],
                "run_build_id": context["run_build_id"],
                "error_type": type(exc).__name__,
                "error_message": str(exc)[:1000],
                "checkpoints_preserved": sorted(path.name for path in root.glob("checkpoint-*")),
            }
            _atomic_replace(root / "failure.json", _json_bytes(status), mode=PRIVATE_FILE_MODE)
            manifest_path = root / "run_manifest.json"
            if manifest_path.is_file():
                manifest = load_json(manifest_path, "failed run manifest")
                manifest["status"] = "failed"
                _atomic_replace(manifest_path, _json_bytes(manifest), mode=PRIVATE_FILE_MODE)
        raise
    finally:
        del trainer
        gc.collect()
        if "torch" in locals():
            torch.cuda.empty_cache()


def reload_final(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["run_root"]
    final_root = root / "final"
    if root.is_symlink() or final_root.is_symlink() or not final_root.is_dir():
        raise Phase5TrainingError("reload 대상 final checkpoint가 없습니다.")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5TrainingError("reload runtime import가 실패했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        final_root, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        final_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda:0")
    model.eval()
    monitor = _dev_monitor_rows(context)[:5]
    generations: list[dict[str, Any]] = []
    started = time.monotonic()
    with torch.inference_mode():
        for index, row in enumerate(monitor):
            prompt = row["messages"][:-1]
            input_ids = tokenizer.apply_chat_template(
                prompt,
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
            ).to("cuda:0")
            output = model.generate(
                input_ids,
                do_sample=False,
                num_beams=1,
                max_new_tokens=64,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            decoded = tokenizer.decode(output[0, input_ids.shape[-1] :], skip_special_tokens=True).strip()
            if not decoded:
                raise Phase5TrainingError("reload fixture 출력이 비었습니다.")
            generations.append(
                {
                    "fixture_index": index,
                    "prompt_sha256": sha256_json(prompt),
                    "output": decoded,
                }
            )
    _write_once(
        root / "reload_fixtures.jsonl",
        b"".join(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            + b"\n"
            for value in generations
        ),
        mode=PRIVATE_FILE_MODE,
    )
    summary = {
        "schema_version": "1.0.0",
        "status": "passed",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "task_count": 5,
        "nonempty_outputs": 5,
        "model_parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "model_dtype": "torch.bfloat16",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "new_process": os.environ.get("PHASE5_RELOAD_CHILD") == "1",
        "raw_outputs_in_summary": False,
    }
    _write_once(root / "reload_summary.json", _json_bytes(summary), mode=PRIVATE_FILE_MODE)
    return summary


def verify_run(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["run_root"]
    public_root: Path = context["public_root"]
    if (
        root.is_symlink()
        or public_root.is_symlink()
        or not root.is_dir()
        or not public_root.is_dir()
        or stat.S_IMODE(root.stat().st_mode) & 0o077
    ):
        raise Phase5TrainingError("완료된 Phase 5 private/public Run이 없습니다.")
    manifest = load_json(root / "run_manifest.json", "Phase 5 run manifest")
    summary = load_json(root / "training_summary.json", "Phase 5 training summary")
    reload_summary = load_json(root / "reload_summary.json", "Phase 5 reload summary")
    public_value = load_json(public_root / "training_summary.json", "public training summary")
    public_payload = _json_bytes(public_value)
    public_manifest = load_json(
        public_root / "build_manifest.json", "public run manifest"
    )
    if (
        manifest.get("status") != "trained_and_reloaded"
        or manifest.get("run_sha256") != context["run_sha256"]
        or summary.get("optimizer_steps") != context["run_contract"]["expected_optimizer_steps"]
        or summary.get("final_reload_passed") is not True
        or reload_summary.get("status") != "passed"
        or reload_summary.get("new_process") is not True
        or public_value != _training_summary_public(summary)
        or public_manifest != _public_run_manifest(context, public_payload)
    ):
        raise Phase5TrainingError("Phase 5 Run 완료 계약이 다릅니다.")
    for relative, digest in manifest.get("artifact_sha256", {}).items():
        if sha256_file(root / relative) != digest:
            raise Phase5TrainingError(f"Phase 5 Run artifact hash가 다릅니다: {relative}")
    return {
        "status": "verified_trained_and_reloaded",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "optimizer_steps": summary["optimizer_steps"],
        "training_loss": summary["training_loss"],
        "phase5_training_performed": True,
        "writes_performed": False,
    }


def _diagnostic_items(context: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    registry = load_json(
        repo_root / "configs/data_versions/saju_1b_baseline/registry.json",
        "dataset registry",
    )
    evaluation = registry.get("approved_evaluation_split")
    if not isinstance(evaluation, dict) or evaluation.get("version") != "v1.1.0":
        raise Phase5TrainingError("승인된 evaluation split v1.1이 없습니다.")
    parent_id = evaluation.get("parent_split_build_id")
    build_id = evaluation.get("build_id")
    if not isinstance(parent_id, str) or not isinstance(build_id, str):
        raise Phase5TrainingError("evaluation split registry identity가 없습니다.")
    diagnostic = read_jsonl(
        repo_root
        / f"data/derived/saju_1b_baseline/evaluation-split/v1.0.0/{parent_id}/eval/dev_diagnostic_930.jsonl",
        "dev diagnostic",
    )
    guard = read_jsonl(
        repo_root
        / f"data/derived/saju_1b_baseline/evaluation-split/v1.1.0/{build_id}/eval/persona_causalization_guard_50.jsonl",
        "persona causalization guard",
    )
    values = flatten_items([*diagnostic, *guard])
    if len(values) != context["config"]["evaluation"]["diagnostic_cases"]:
        raise Phase5TrainingError("KI10 diagnostic generation 수량이 다릅니다.")
    return values


def evaluate_ki10(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if context["run_id"] != "KI10-MIX-v2":
        raise Phase5TrainingError("자동 promotion Gate는 KI10에만 실행합니다.")
    verify_run(context)
    root: Path = context["run_root"]
    generations_path = root / "ki10_diagnostic_generations.jsonl"
    existing = _read_jsonl_if_exists(generations_path)
    completed = {(row["eval_id"], row["case_id"]) for row in existing}
    items = _diagnostic_items(context, repo_root)
    if completed - {(row["eval_id"], row["case_id"]) for row in items}:
        raise Phase5TrainingError("기존 KI10 diagnostic generation identity가 다릅니다.")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5TrainingError("KI10 평가 runtime import가 실패했습니다.") from exc
    final_root = root / "final"
    tokenizer = AutoTokenizer.from_pretrained(
        final_root, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        final_root,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda:0")
    model.eval()
    max_new_tokens = context["config"]["evaluation"]["generation_max_new_tokens"]
    started = time.monotonic()
    with torch.inference_mode():
        for index, item in enumerate(items, 1):
            identity = (item["eval_id"], item["case_id"])
            if identity in completed:
                continue
            input_ids = tokenizer.apply_chat_template(
                item["prompt_messages"],
                tokenize=True,
                add_generation_prompt=True,
                return_tensors="pt",
            ).to("cuda:0")
            output_ids = model.generate(
                input_ids,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            output = tokenizer.decode(
                output_ids[0, input_ids.shape[-1] :], skip_special_tokens=True
            ).strip()
            _append_jsonl(generations_path, {**item, "output": output})
            if index % 50 == 0:
                print(f"ki10_diagnostic_progress={index}/{len(items)}", file=sys.stderr, flush=True)
    del model
    gc.collect()
    torch.cuda.empty_cache()
    generations = read_jsonl(generations_path, "KI10 diagnostic generations")
    score = score_generations(
        generations,
        context["config"]["evaluation"]["ki10_promotion_thresholds"],
    )
    score.update(
        {
            "run_id": context["run_id"],
            "run_build_id": context["run_build_id"],
            "run_sha256": context["run_sha256"],
            "generation_max_new_tokens": max_new_tokens,
            "generation_elapsed_seconds": round(time.monotonic() - started, 3),
            "train_loss_and_gradient_finite": True,
            "checkpoint_reload_verified": True,
            "blind_source_test_accessed": False,
            "production_quality_claim_allowed": False,
        }
    )
    private_payload = _json_bytes(score)
    _write_once(root / "ki10_quality_gate.json", private_payload, mode=PRIVATE_FILE_MODE)
    public_value = public_summary(score)
    public_value["generation_elapsed_seconds"] = score["generation_elapsed_seconds"]
    public_payload = _json_bytes(public_value)
    _write_once(
        context["public_root"] / "ki10_quality_gate.json",
        public_payload,
        mode=PUBLIC_FILE_MODE,
    )
    gate_manifest = {
        "schema_version": "1.0.0",
        "report_type": "ki10_automated_promotion_gate",
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "run_sha256": context["run_sha256"],
        "artifact_sha256": {
            "ki10_quality_gate.json": hashlib.sha256(public_payload).hexdigest()
        },
        "status": score["status"],
        "ki20_promotion_allowed": score["ki20_promotion_allowed"],
        "blind_source_test_accessed": False,
        "human_row_review_required": False,
    }
    _write_once(
        context["public_root"] / "ki10_gate_manifest.json",
        _json_bytes(gate_manifest),
        mode=PUBLIC_FILE_MODE,
    )
    return {
        "status": score["status"],
        "run_id": context["run_id"],
        "run_build_id": context["run_build_id"],
        "failed_gates": score["failed_gates"],
        "ki20_promotion_allowed": score["ki20_promotion_allowed"],
        "blind_source_test_accessed": False,
        "writes_performed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 KI10·KI20 Full FT runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    plan = commands.add_parser("plan")
    plan.add_argument("--run-id", choices=RUN_IDS, required=True)
    preflight = commands.add_parser("preflight-run")
    preflight.add_argument("--run-id", choices=RUN_IDS, required=True)
    preflight.add_argument("--execute", action="store_true")
    train = commands.add_parser("train")
    train.add_argument("--run-id", choices=RUN_IDS, required=True)
    train.add_argument("--execute", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("--run-id", choices=RUN_IDS, required=True)
    resume.add_argument("--checkpoint", type=Path, required=True)
    resume.add_argument("--execute", action="store_true")
    reload_parser = commands.add_parser("reload")
    reload_parser.add_argument("--run-id", choices=RUN_IDS, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--run-id", choices=RUN_IDS, required=True)
    evaluate = commands.add_parser("evaluate-ki10")
    evaluate.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "training config"), REPO_ROOT)
        else:
            run_id = "KI10-MIX-v2" if args.command == "evaluate-ki10" else args.run_id
            context = prepare_context(REPO_ROOT, config_path, run_id)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "run_id": run_id,
                    "run_build_id": context["run_build_id"],
                    "run_sha256": context["run_sha256"],
                    "rows": context["run_contract"]["rows"],
                    "expected_optimizer_steps": context["run_contract"]["expected_optimizer_steps"],
                    "run_root": context["run_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "preflight-run":
                result = (
                    preflight_run(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "run_build_id": context["run_build_id"], "writes_performed": False}
                )
            elif args.command == "train":
                result = (
                    _train(context, REPO_ROOT, resume_checkpoint=None)
                    if args.execute
                    else {"status": "dry_run", "run_build_id": context["run_build_id"], "writes_performed": False}
                )
            elif args.command == "resume":
                checkpoint = args.checkpoint
                if not checkpoint.is_absolute():
                    checkpoint = (REPO_ROOT / checkpoint).resolve()
                result = (
                    _train(context, REPO_ROOT, resume_checkpoint=checkpoint)
                    if args.execute
                    else {"status": "dry_run", "run_build_id": context["run_build_id"], "writes_performed": False}
                )
            elif args.command == "reload":
                result = reload_final(context, REPO_ROOT)
            elif args.command == "verify":
                result = verify_run(context)
            else:
                result = (
                    evaluate_ki10(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "run_build_id": context["run_build_id"], "writes_performed": False}
                )
    except (Phase5TrainingError, Phase5QualityError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
