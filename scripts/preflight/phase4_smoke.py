# phase4_smoke.py - Full FT Gate D/E를 단계별 새 프로세스로 실행하고 checkpoint 복구를 검증한다.

from __future__ import annotations

import gc
import math
import os
import shutil
import stat
import time
from pathlib import Path
from statistics import median
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    load_json,
    read_jsonl,
    runtime_environment,
    sha256_bytes,
    sha256_file,
    sha256_json,
    utc_now,
    verify_hash_map,
    write_json_once,
    write_jsonl_once,
)
from scripts.preflight.phase4_data import load_staging_records, verify_private_build
from scripts.preflight.phase4_review import verify_preflight

TRAINING_STAGES = (
    "gate_d_512_1",
    "smoke_512_20",
    "diagnostic_1024_1",
    "main_768_100",
    "resume_768_200",
)
RELOAD_STAGE = "reload_768_generate5"
ALL_STAGES = (*TRAINING_STAGES, RELOAD_STAGE)


class _SmokeProbeCallback:
    """Trainer callback 구현은 런타임 import 뒤 동적으로 등록한다."""

    def __init__(self, torch: Any, *, stop_at_step: int | None = None) -> None:
        self.torch = torch
        self.stop_at_step = stop_at_step
        self.train_begin_global_step: int | None = None
        self.first_pre_optimizer_global_step: int | None = None
        self.gradient_probe: dict[str, Any] | None = None
        self.log_history: list[dict[str, Any]] = []

    def on_train_begin(self, args: Any, state: Any, control: Any, **kwargs: Any) -> None:
        del args, control, kwargs
        self.train_begin_global_step = int(state.global_step)

    def on_pre_optimizer_step(
        self, args: Any, state: Any, control: Any, **kwargs: Any
    ) -> None:
        del args, control
        if self.gradient_probe is not None:
            return
        model = kwargs["model"]
        finite = True
        nonzero = False
        tensors = 0
        elements = 0
        for parameter in model.parameters():
            gradient = parameter.grad
            if gradient is None:
                continue
            tensors += 1
            elements += gradient.numel()
            finite = finite and bool(self.torch.isfinite(gradient).all().item())
            nonzero = nonzero or bool(self.torch.count_nonzero(gradient).item())
        self.first_pre_optimizer_global_step = int(state.global_step)
        self.gradient_probe = {
            "finite": finite,
            "nonzero": nonzero,
            "tensor_count": tensors,
            "element_count": elements,
            "observed_after_gradient_clip": True,
        }

    def on_log(
        self, args: Any, state: Any, control: Any, logs: dict[str, Any], **kwargs: Any
    ) -> None:
        del args, state, control, kwargs
        clean: dict[str, Any] = {}
        for key, value in logs.items():
            if isinstance(value, (bool, int, float, str)) or value is None:
                clean[key] = value
        self.log_history.append(clean)

    def on_step_end(self, args: Any, state: Any, control: Any, **kwargs: Any) -> Any:
        del args, kwargs
        if self.stop_at_step is not None and int(state.global_step) >= self.stop_at_step:
            control.should_save = True
            control.should_training_stop = True
        return control


def _stage_root(context: dict[str, Any], stage: str) -> Path:
    return context["smoke_root"] / "stages" / stage


def _checkpoint_root(context: dict[str, Any]) -> Path:
    return context["smoke_root"] / "training-768"


def _checkpoint_path(context: dict[str, Any], step: int) -> Path:
    return _checkpoint_root(context) / f"checkpoint-{step}"


def _smoke_run_config(context: dict[str, Any]) -> dict[str, Any]:
    config = context["config"]
    return {
        "schema_version": "1.0.0",
        "report_type": "phase4_de_full_ft_smoke",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "model": {
            key: config["model"][key]
            for key in (
                "repo_id",
                "revision",
                "phase3_build_id",
                "snapshot_manifest_sha256",
                "dtype",
                "attention_backend",
                "expected_parameter_count",
            )
        },
        "chat_template_sha256": config["chat_template"]["sha256"],
        "training_smoke": config["training_smoke"],
        "parent_preflight_build_id": context["build_id"],
        "parent_staging": config["parent_staging"],
        "phase5_training_performed": False,
    }


def _prepare_smoke_root(context: dict[str, Any]) -> None:
    root: Path = context["smoke_root"]
    root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    root.chmod(PRIVATE_DIR_MODE)
    (root / "stages").mkdir(exist_ok=True, mode=PRIVATE_DIR_MODE)
    (root / "stages").chmod(PRIVATE_DIR_MODE)
    write_json_once(
        root / "run_config.json",
        _smoke_run_config(context),
        mode=PRIVATE_FILE_MODE,
    )


def _validate_smoke_prerequisites(
    context: dict[str, Any], repo_root: Path, stage: str
) -> None:
    preflight = verify_preflight(context, repo_root)
    if preflight["k0"]["gate_c_passed"] is not True:
        raise Phase4Error("Gate C 통과 전에는 Phase 4D/E를 시작할 수 없습니다.")
    predecessor = {
        "smoke_512_20": "gate_d_512_1",
        "diagnostic_1024_1": "smoke_512_20",
        "main_768_100": "diagnostic_1024_1",
        "resume_768_200": "main_768_100",
        "reload_768_generate5": "resume_768_200",
    }.get(stage)
    if predecessor is None:
        return
    result = verify_smoke_stage(context, repo_root, predecessor)
    allowed = {"passed"}
    if predecessor == "diagnostic_1024_1":
        allowed.add("diagnostic_failed")
    if result["status"] not in allowed:
        raise Phase4Error(f"선행 smoke stage가 통과하지 않았습니다: {predecessor}")


def _load_training_records(
    context: dict[str, Any], repo_root: Path, manifest_name: str
) -> tuple[list[dict[str, Any]], str]:
    records_by_id, _, _, _ = load_staging_records(context, repo_root)
    manifest_path = context["private_root"] / "manifests" / manifest_name
    manifest = read_jsonl(manifest_path, f"smoke manifest {manifest_name}")
    rows: list[dict[str, Any]] = []
    for value in manifest:
        record_id = value.get("id")
        record = records_by_id.get(record_id)
        if record is None or value.get("record_sha256") is None:
            raise Phase4Error("smoke manifest record identity가 없습니다.")
        if sha256_json(record) != value["record_sha256"]:
            raise Phase4Error(f"smoke manifest/staging record hash가 다릅니다: {record_id}")
        rows.append({"messages": record["messages"]})
    if len(rows) != 1_000:
        raise Phase4Error("Phase 4D/E는 정확히 MIX1K 1,000행을 사용해야 합니다.")
    return rows, sha256_file(manifest_path)


def _load_training_runtime(
    context: dict[str, Any], repo_root: Path
) -> tuple[Any, Any, Any, Any, Any, dict[str, Any]]:
    environment = runtime_environment(context["config"], repo_root)
    for key in ("CPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = environment[key]
    if os.environ.get("TORCH_DISABLE_NATIVE_JIT"):
        raise Phase4Error("정식 smoke에서는 TORCH_DISABLE_NATIVE_JIT를 사용할 수 없습니다.")
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
        raise Phase4Error("Phase 4D/E 고정 학습 runtime을 import하지 못했습니다.") from exc
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise Phase4Error("Phase 4D/E는 단일 CUDA GPU가 필요합니다.")
    if torch.cuda.current_device() != 0:
        raise Phase4Error("Phase 4D/E 장치는 cuda:0이어야 합니다.")
    config = context["config"]
    snapshot = repo_root / config["model"]["local_subdir"]
    torch.manual_seed(config["training_smoke"]["seed"])
    torch.cuda.manual_seed_all(config["training_smoke"]["seed"])
    torch.backends.cudnn.benchmark = False
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(0)
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot,
        local_files_only=True,
        trust_remote_code=True,
    )
    tokenizer.padding_side = "right"
    template = config["chat_template"]
    if (
        tokenizer.bos_token_id != template["bos_token_id"]
        or tokenizer.eos_token_id != template["eos_token_id"]
        or tokenizer.pad_token_id != template["pad_token_id"]
        or not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8"))
        != template["sha256"]
    ):
        raise Phase4Error("Full FT tokenizer/chat template 계약이 다릅니다.")
    model = AutoModelForCausalLM.from_pretrained(
        snapshot,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = False
    parameters = list(model.parameters())
    parameter_count = sum(parameter.numel() for parameter in parameters)
    trainable_count = sum(
        parameter.numel() for parameter in parameters if parameter.requires_grad
    )
    if parameter_count != config["training_smoke"]["full_parameter_count"]:
        raise Phase4Error("Full FT 모델 parameter 수가 고정 계약과 다릅니다.")
    if trainable_count != parameter_count:
        raise Phase4Error("Full FT인데 동결된 parameter가 있습니다.")
    if any(parameter.dtype != torch.bfloat16 for parameter in parameters):
        raise Phase4Error("Full FT 모델 parameter가 전부 BF16이 아닙니다.")
    if getattr(model.config, "_attn_implementation", None) != "sdpa":
        raise Phase4Error("Full FT attention backend가 SDPA가 아닙니다.")

    runtime = {
        "python": ".venv/bin/python",
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "transformers": transformers.__version__,
        "trl": trl.__version__,
        "bitsandbytes": bitsandbytes.__version__,
        "datasets": datasets.__version__,
        "gpu_name": torch.cuda.get_device_name(0),
        "gpu_capability": list(torch.cuda.get_device_capability(0)),
        "vram_total_bytes": int(torch.cuda.get_device_properties(0).total_memory),
        "parameter_count": parameter_count,
        "trainable_parameter_count": trainable_count,
        "parameter_dtype": "torch.bfloat16",
        "attention_backend": "sdpa",
        "device": "cuda:0",
    }
    return torch, Dataset, TrainerCallback, SFTConfig, SFTTrainer, {
        "tokenizer": tokenizer,
        "model": model,
        "runtime": runtime,
    }


def _unwrap_optimizer(optimizer: Any) -> Any:
    seen: set[int] = set()
    current = optimizer
    while hasattr(current, "optimizer") and id(current) not in seen:
        seen.add(id(current))
        current = current.optimizer
    return current


def _optimizer_probe(torch: Any, optimizer: Any) -> dict[str, Any]:
    raw = _unwrap_optimizer(optimizer)
    dtype_counts: dict[str, int] = {}
    tensor_count = 0
    element_count = 0
    for state in raw.state.values():
        for value in state.values():
            if not torch.is_tensor(value):
                continue
            tensor_count += 1
            element_count += value.numel()
            key = str(value.dtype)
            dtype_counts[key] = dtype_counts.get(key, 0) + 1
    return {
        "wrapper_class": type(optimizer).__name__,
        "optimizer_class": type(raw).__name__,
        "state_tensor_count": tensor_count,
        "state_element_count": element_count,
        "state_tensor_dtype_counts": dict(sorted(dtype_counts.items())),
        "uint8_state_present": dtype_counts.get("torch.uint8", 0) > 0,
    }


def _system_ram() -> dict[str, int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            name, raw = line.split(":", 1)
            if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
                values[name] = int(raw.strip().split()[0]) * 1024
    except (OSError, UnicodeError, ValueError):
        return {
            "total_bytes": None,
            "available_bytes": None,
            "swap_total_bytes": None,
            "swap_free_bytes": None,
        }
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
    }


def _loss_report(log_history: list[dict[str, Any]]) -> dict[str, Any]:
    losses = [
        float(value["loss"])
        for value in log_history
        if isinstance(value.get("loss"), (int, float))
    ]
    grad_norms = [
        float(value["grad_norm"])
        for value in log_history
        if isinstance(value.get("grad_norm"), (int, float))
    ]
    finite = bool(losses) and all(math.isfinite(value) for value in losses)
    finite_grad_norms = bool(grad_norms) and all(
        math.isfinite(value) for value in grad_norms
    )
    window = min(20, max(1, len(losses) // 2)) if losses else 0
    first = median(losses[:window]) if window else None
    last = median(losses[-window:]) if window else None
    return {
        "logged_optimizer_steps": len(losses),
        "losses_finite": finite,
        "grad_norms_finite": finite_grad_norms,
        "first_window_size": window,
        "first_window_median_loss": round(first, 8) if first is not None else None,
        "last_window_median_loss": round(last, 8) if last is not None else None,
        "loss_decrease_trend": bool(first is not None and last is not None and last <= first),
        "minimum_loss": round(min(losses), 8) if losses else None,
        "maximum_loss": round(max(losses), 8) if losses else None,
    }


def _checkpoint_inventory(checkpoint: Path, output: Path) -> dict[str, Any]:
    if checkpoint.is_symlink() or not checkpoint.is_dir():
        raise Phase4Error(f"checkpoint가 없습니다: {checkpoint}")
    checkpoint.chmod(PRIVATE_DIR_MODE)
    checkpoint.parent.chmod(PRIVATE_DIR_MODE)
    files: list[dict[str, Any]] = []
    for path in sorted(checkpoint.rglob("*")):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise Phase4Error("checkpoint에 link 또는 special file이 있습니다.")
        if stat.S_ISDIR(info.st_mode):
            path.chmod(PRIVATE_DIR_MODE)
            continue
        path.chmod(PRIVATE_FILE_MODE)
        files.append(
            {
                "bytes": info.st_size,
                "path": path.relative_to(checkpoint).as_posix(),
                "sha256": sha256_file(path),
            }
        )
    required = {"optimizer.pt", "scheduler.pt", "trainer_state.json"}
    if not required <= {value["path"] for value in files}:
        raise Phase4Error("resume 필수 optimizer/scheduler/trainer state가 없습니다.")
    result = {
        "schema_version": "1.0.0",
        "checkpoint": checkpoint.name,
        "file_count": len(files),
        "total_bytes": sum(value["bytes"] for value in files),
        "files": files,
    }
    write_json_once(output, result, mode=PRIVATE_FILE_MODE)
    return result


def _make_callback(base: _SmokeProbeCallback, trainer_callback: Any) -> Any:
    class SmokeProbeCallback(trainer_callback):
        def on_train_begin(self, *args: Any, **kwargs: Any) -> None:
            return base.on_train_begin(*args, **kwargs)

        def on_pre_optimizer_step(self, *args: Any, **kwargs: Any) -> None:
            return base.on_pre_optimizer_step(*args, **kwargs)

        def on_log(self, *args: Any, **kwargs: Any) -> None:
            return base.on_log(*args, **kwargs)

        def on_step_end(self, *args: Any, **kwargs: Any) -> Any:
            return base.on_step_end(*args, **kwargs)

    return SmokeProbeCallback()


def _training_arguments(
    context: dict[str, Any], stage: str, stage_config: dict[str, Any], sft_config: Any
) -> Any:
    smoke = context["config"]["training_smoke"]
    max_steps = int(stage_config.get("total_optimizer_steps", stage_config["optimizer_steps"]))
    output_dir = (
        _checkpoint_root(context)
        if stage in {"main_768_100", "resume_768_200"}
        else context["smoke_root"] / "temporary-training" / stage
    )
    return sft_config(
        output_dir=str(output_dir),
        overwrite_output_dir=False,
        do_train=True,
        per_device_train_batch_size=smoke["per_device_train_batch_size"],
        gradient_accumulation_steps=smoke["gradient_accumulation_steps"],
        learning_rate=smoke["learning_rate"],
        weight_decay=smoke["weight_decay"],
        max_grad_norm=smoke["max_grad_norm"],
        max_steps=max_steps,
        lr_scheduler_type=smoke["lr_scheduler_type"],
        warmup_ratio=smoke["warmup_ratio"],
        logging_strategy="steps",
        logging_steps=1,
        logging_first_step=True,
        logging_nan_inf_filter=False,
        save_strategy=(
            "steps" if stage in {"main_768_100", "resume_768_200"} else "no"
        ),
        save_steps=100,
        save_total_limit=None,
        save_only_model=False,
        save_safetensors=True,
        seed=smoke["seed"],
        data_seed=smoke["data_seed"],
        bf16=True,
        fp16=False,
        tf32=False,
        optim=smoke["optimizer"],
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        report_to=[],
        disable_tqdm=False,
        dataloader_num_workers=0,
        dataloader_pin_memory=True,
        remove_unused_columns=True,
        skip_memory_metrics=True,
        max_length=stage_config["max_length"],
        pad_to_multiple_of=stage_config["max_length"],
        assistant_only_loss=True,
        packing=False,
        padding_free=False,
        loss_type=smoke["loss_type"],
        shuffle_dataset=True,
        dataset_num_proc=1,
        trust_remote_code=True,
        torch_compile=False,
        use_liger_kernel=False,
        activation_offloading=False,
    )


def _write_stage(
    context: dict[str, Any],
    stage: str,
    summary: dict[str, Any],
    logs: list[dict[str, Any]],
    *,
    checkpoint_inventory: dict[str, Any] | None = None,
    generations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = _stage_root(context, stage)
    root.mkdir(parents=True, exist_ok=False, mode=PRIVATE_DIR_MODE)
    write_json_once(root / "summary.json", summary, mode=PRIVATE_FILE_MODE)
    write_json_once(
        root / "trainer_log.json",
        {"schema_version": "1.0.0", "logs": logs},
        mode=PRIVATE_FILE_MODE,
    )
    artifacts = ["summary.json", "trainer_log.json"]
    if checkpoint_inventory is not None:
        write_json_once(
            root / "checkpoint_inventory.json",
            checkpoint_inventory,
            mode=PRIVATE_FILE_MODE,
        )
        artifacts.append("checkpoint_inventory.json")
    if generations is not None:
        write_jsonl_once(root / "generations.jsonl", generations)
        artifacts.append("generations.jsonl")
    manifest = {
        "schema_version": "1.0.0",
        "report_type": "phase4_smoke_stage_manifest",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "stage": stage,
        "status": summary["status"],
        "artifact_sha256": artifact_hash_map(root, artifacts),
        "phase5_training_performed": False,
        "training_promotion_allowed": False,
    }
    write_json_once(root / "stage_manifest.json", manifest, mode=PRIVATE_FILE_MODE)
    return verify_smoke_stage(context, Path.cwd(), stage)


def _run_training_stage(
    context: dict[str, Any], repo_root: Path, stage: str
) -> dict[str, Any]:
    stage_config = context["config"]["training_smoke"]["stages"][stage]
    rows, manifest_sha256 = _load_training_records(
        context, repo_root, stage_config["manifest"]
    )
    (
        torch,
        dataset_class,
        trainer_callback,
        sft_config,
        sft_trainer,
        loaded,
    ) = _load_training_runtime(context, repo_root)
    model = loaded["model"]
    tokenizer = loaded["tokenizer"]
    trainer: Any | None = None
    started = time.monotonic()
    try:
        dataset = dataset_class.from_list(rows)
        stop_at = 100 if stage == "main_768_100" else None
        probe = _SmokeProbeCallback(torch, stop_at_step=stop_at)
        args = _training_arguments(context, stage, stage_config, sft_config)
        trainer = sft_trainer(
            model=model,
            args=args,
            train_dataset=dataset,
            processing_class=tokenizer,
            callbacks=[_make_callback(probe, trainer_callback)],
        )
        expected_gc = context["config"]["training_smoke"]["gradient_checkpointing"]
        if bool(model.is_gradient_checkpointing) is not expected_gc:
            raise Phase4Error("gradient checkpointing 활성 상태가 다릅니다.")
        resume_path: str | None = None
        if stage == "resume_768_200":
            checkpoint = _checkpoint_path(context, stage_config["resume_step"])
            verify_checkpoint_inventory(
                checkpoint,
                _stage_root(context, "main_768_100") / "checkpoint_inventory.json",
            )
            resume_path = str(checkpoint)
        train_output = trainer.train(resume_from_checkpoint=resume_path)
        global_step = int(trainer.state.global_step)
        expected_step = (
            100 if stage == "main_768_100" else int(stage_config["optimizer_steps"])
        )
        if global_step != expected_step:
            raise Phase4Error(
                f"smoke optimizer step 수가 다릅니다: {global_step} != {expected_step}"
            )
        loss = float(train_output.training_loss)
        if not math.isfinite(loss):
            raise Phase4Error("smoke training loss가 NaN/Inf입니다.")
        gradient = probe.gradient_probe
        if not gradient or gradient["finite"] is not True or gradient["nonzero"] is not True:
            raise Phase4Error("smoke gradient가 유한한 nonzero 값이 아닙니다.")
        optimizer = _optimizer_probe(torch, trainer.optimizer)
        if optimizer["uint8_state_present"] is not True:
            raise Phase4Error("paged AdamW optimizer에 실제 uint8 state가 없습니다.")
        free_vram, total_vram = torch.cuda.mem_get_info(0)
        peak_vram = int(torch.cuda.max_memory_allocated(0))
        all_logs = [
            {
                key: value
                for key, value in log.items()
                if isinstance(value, (bool, int, float, str)) or value is None
            }
            for log in trainer.state.log_history
        ]
        losses = _loss_report(all_logs)
        if losses["losses_finite"] is not True or losses["grad_norms_finite"] is not True:
            raise Phase4Error("step별 loss/gradient norm이 유한하지 않습니다.")
        if stage == "resume_768_200" and losses["loss_decrease_trend"] is not True:
            raise Phase4Error("200-step loss가 초기 구간 대비 감소 경향을 보이지 않습니다.")
        minimum_headroom = context["config"]["training_smoke"][
            "minimum_vram_headroom_bytes"
        ]
        if stage in {"main_768_100", "resume_768_200"} and free_vram < minimum_headroom:
            raise Phase4Error("768 formal smoke의 VRAM headroom이 1GiB 미만입니다.")
        checkpoint_inventory: dict[str, Any] | None = None
        checkpoint_step = stage_config.get("checkpoint_step")
        if checkpoint_step is not None:
            checkpoint = _checkpoint_path(context, int(checkpoint_step))
            temporary_inventory = _stage_root(context, stage).with_suffix(".inventory.json")
            checkpoint_inventory = _checkpoint_inventory(checkpoint, temporary_inventory)
            temporary_inventory.unlink()
        summary = {
            "schema_version": "1.0.0",
            "report_type": "phase4_full_ft_smoke_stage",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "stage": stage,
            "status": "passed",
            "generated_at": utc_now(),
            "manifest": stage_config["manifest"],
            "manifest_sha256": manifest_sha256,
            "max_length": stage_config["max_length"],
            "optimizer_steps": global_step,
            "train_begin_global_step": probe.train_begin_global_step,
            "first_pre_optimizer_global_step": probe.first_pre_optimizer_global_step,
            "resumed_from_checkpoint": resume_path is not None,
            "training_loss": loss,
            "loss_report": losses,
            "gradient_probe": gradient,
            "optimizer_probe": optimizer,
            "runtime": loaded["runtime"],
            "gradient_checkpointing": bool(model.is_gradient_checkpointing),
            "use_cache": bool(model.config.use_cache),
            "peak_vram_bytes": peak_vram,
            "vram_total_bytes": int(total_vram),
            "vram_free_bytes_at_finish": int(free_vram),
            "vram_headroom_requirement_bytes": minimum_headroom,
            "system_ram": _system_ram(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "phase5_training_performed": False,
            "training_promotion_allowed": False,
        }
        result = _write_stage(
            context,
            stage,
            summary,
            all_logs,
            checkpoint_inventory=checkpoint_inventory,
        )
        return {**result, "mode": "completed", "writes_performed": True}
    finally:
        del trainer
        del model
        gc.collect()
        torch.cuda.empty_cache()
        if stage not in {"main_768_100", "resume_768_200"}:
            shutil.rmtree(
                context["smoke_root"] / "temporary-training" / stage,
                ignore_errors=True,
            )


def _is_cuda_oom(exc: BaseException) -> bool:
    return "out of memory" in str(exc).lower() or "cuda error: out of memory" in str(exc).lower()


def _write_diagnostic_failure(
    context: dict[str, Any], stage: str, exc: BaseException
) -> dict[str, Any]:
    summary = {
        "schema_version": "1.0.0",
        "report_type": "phase4_full_ft_smoke_stage",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "stage": stage,
        "status": "diagnostic_failed",
        "failure_class": "cuda_oom" if _is_cuda_oom(exc) else "runtime_error",
        "failure_message": str(exc)[:500],
        "formal_length_blocked": False,
        "selected_formal_length": 768,
        "phase5_training_performed": False,
        "training_promotion_allowed": False,
    }
    result = _write_stage(context, stage, summary, [])
    return {**result, "mode": "diagnostic_failure_recorded", "writes_performed": True}


def _reload_and_generate(
    context: dict[str, Any], repo_root: Path, stage: str
) -> dict[str, Any]:
    config = context["config"]
    stage_config = config["training_smoke"]["stages"][stage]
    checkpoint = _checkpoint_path(context, stage_config["checkpoint_step"])
    verify_checkpoint_inventory(
        checkpoint,
        _stage_root(context, "resume_768_200") / "checkpoint_inventory.json",
    )
    environment = runtime_environment(config, repo_root)
    for key in ("CPATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ[key] = environment[key]
    try:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase4Error("checkpoint reload runtime import가 실패했습니다.") from exc
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(0)
    started = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(
        checkpoint,
        local_files_only=True,
        trust_remote_code=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        checkpoint,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda:0")
    model.eval()
    core = read_jsonl(context["private_root"] / "eval/core_eval_200.jsonl", "Core Eval")
    tasks: list[dict[str, Any]] = []
    seen_categories: set[str] = set()
    try:
        for item in core:
            category = item["category"]
            if category in seen_categories:
                continue
            seen_categories.add(category)
            messages = item["cases"][0]["prompt_messages"]
            encoded = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            ).to("cuda:0")
            input_length = int(encoded["input_ids"].shape[-1])
            with torch.inference_mode():
                output = model.generate(
                    **encoded,
                    do_sample=False,
                    num_beams=1,
                    max_new_tokens=stage_config["max_new_tokens"],
                    use_cache=True,
                    eos_token_id=tokenizer.eos_token_id,
                    pad_token_id=tokenizer.pad_token_id,
                )
            ids = output[0, input_length:].detach().cpu().tolist()
            text = tokenizer.decode(ids, skip_special_tokens=True).strip()
            tasks.append(
                {
                    "schema_version": "1.0.0",
                    "category": category,
                    "generated_tokens": len(ids),
                    "finished_with_eos": tokenizer.eos_token_id in ids,
                    "nonempty": bool(text),
                    "output": text,
                }
            )
            if len(tasks) == stage_config["task_count"]:
                break
        if len(tasks) != stage_config["task_count"] or not all(
            task["nonempty"] for task in tasks
        ):
            raise Phase4Error("checkpoint reload 5-task 생성에 빈 출력이 있습니다.")
        free_vram, total_vram = torch.cuda.mem_get_info(0)
        summary = {
            "schema_version": "1.0.0",
            "report_type": "phase4_checkpoint_reload_generation",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "stage": stage,
            "status": "passed",
            "generated_at": utc_now(),
            "checkpoint": checkpoint.name,
            "task_count": len(tasks),
            "nonempty_outputs": sum(task["nonempty"] for task in tasks),
            "runtime": {
                "torch": torch.__version__,
                "torch_cuda": torch.version.cuda,
                "transformers": transformers.__version__,
                "gpu_name": torch.cuda.get_device_name(0),
            },
            "peak_vram_bytes": int(torch.cuda.max_memory_allocated(0)),
            "vram_total_bytes": int(total_vram),
            "vram_free_bytes_at_finish": int(free_vram),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "phase5_training_performed": False,
            "training_promotion_allowed": False,
        }
        result = _write_stage(context, stage, summary, [], generations=tasks)
        return {**result, "mode": "completed", "writes_performed": True}
    finally:
        del model
        gc.collect()
        torch.cuda.empty_cache()


def run_smoke_stage(
    context: dict[str, Any], repo_root: Path, stage: str
) -> dict[str, Any]:
    if stage not in ALL_STAGES:
        raise Phase4Error(f"지원하지 않는 smoke stage입니다: {stage}")
    root = _stage_root(context, stage)
    if root.exists():
        return {
            **verify_smoke_stage(context, repo_root, stage),
            "mode": "reused",
            "writes_performed": False,
        }
    _validate_smoke_prerequisites(context, repo_root, stage)
    _prepare_smoke_root(context)
    if stage == RELOAD_STAGE:
        return _reload_and_generate(context, repo_root, stage)
    try:
        return _run_training_stage(context, repo_root, stage)
    except (RuntimeError, Phase4Error) as exc:
        if stage == "diagnostic_1024_1":
            return _write_diagnostic_failure(context, stage, exc)
        raise


def verify_checkpoint_inventory(checkpoint: Path, inventory_path: Path) -> dict[str, Any]:
    inventory = load_json(inventory_path, "checkpoint inventory")
    if checkpoint.name != inventory.get("checkpoint") or not isinstance(
        inventory.get("files"), list
    ):
        raise Phase4Error("checkpoint inventory identity가 다릅니다.")
    files = inventory["files"]
    if len(files) != inventory.get("file_count"):
        raise Phase4Error("checkpoint inventory 파일 수가 다릅니다.")
    for value in files:
        relative = value.get("path")
        path = checkpoint / str(relative)
        if (
            not isinstance(relative, str)
            or path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != value.get("bytes")
            or sha256_file(path) != value.get("sha256")
        ):
            raise Phase4Error(f"checkpoint 파일 hash가 다릅니다: {relative}")
    return {
        "status": "verified",
        "checkpoint": checkpoint.name,
        "file_count": len(files),
        "total_bytes": inventory.get("total_bytes"),
    }


def verify_smoke_stage(
    context: dict[str, Any], repo_root: Path, stage: str
) -> dict[str, Any]:
    del repo_root
    if stage not in ALL_STAGES:
        raise Phase4Error(f"지원하지 않는 smoke stage입니다: {stage}")
    root = _stage_root(context, stage)
    if root.is_symlink() or not root.is_dir():
        raise Phase4Error(f"smoke stage 산출물이 없습니다: {stage}")
    manifest = load_json(root / "stage_manifest.json", "smoke stage manifest")
    summary = load_json(root / "summary.json", "smoke stage summary")
    allowed_statuses = {"passed"}
    if stage == "diagnostic_1024_1":
        allowed_statuses.add("diagnostic_failed")
    if (
        manifest.get("build_id") != context["build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or manifest.get("stage") != stage
        or manifest.get("status") not in allowed_statuses
        or summary.get("status") != manifest.get("status")
        or summary.get("phase5_training_performed") is not False
        or manifest.get("training_promotion_allowed") is not False
    ):
        raise Phase4Error(f"smoke stage identity/status가 다릅니다: {stage}")
    verify_hash_map(root, manifest.get("artifact_sha256"), f"smoke {stage}")
    for path in root.iterdir():
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase4Error(f"smoke stage 파일 권한이 0600이 아닙니다: {path.name}")
    if stage in {"main_768_100", "resume_768_200"}:
        step = 100 if stage == "main_768_100" else 200
        verify_checkpoint_inventory(
            _checkpoint_path(context, step), root / "checkpoint_inventory.json"
        )
    return {
        "build_id": context["build_id"],
        "stage": stage,
        "status": summary["status"],
        "optimizer_steps": summary.get("optimizer_steps"),
        "peak_vram_bytes": summary.get("peak_vram_bytes"),
        "vram_free_bytes_at_finish": summary.get("vram_free_bytes_at_finish"),
        "phase5_training_performed": False,
        "training_promotion_allowed": False,
    }


def verify_all_smoke(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_private_build(context, repo_root)
    results = {
        stage: verify_smoke_stage(context, repo_root, stage) for stage in ALL_STAGES
    }
    if any(
        result["status"] != "passed"
        for stage, result in results.items()
        if stage != "diagnostic_1024_1"
    ):
        raise Phase4Error("필수 Phase 4D/E smoke stage가 모두 통과하지 않았습니다.")
    return {
        "status": "verified_gates_d_e_passed",
        "selected_max_length": 768,
        "stages": results,
        "phase5_training_performed": False,
        "training_promotion_allowed": False,
    }
