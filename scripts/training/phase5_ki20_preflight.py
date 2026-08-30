# phase5_ki20_preflight.py - KI20의 batch·worker·eval 처리량과 Full FT 수치 안정성을 학습 전 검증한다.

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import multiprocessing
import os
import queue
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from typing_extensions import Self

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_candidate_staging_records,
    load_json,
    read_jsonl,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)
from scripts.preflight.phase4_common import (
    prepare_context as prepare_phase4_context,
)
from scripts.training.phase5_train import _load_runtime

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-training-v1.1.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
GIB = 1024**3
MIB = 1024**2


class Phase5KI20PreflightError(RuntimeError):
    """KI20 v1.1 benchmark·preflight 계약 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _jsonl_bytes(values: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
        for value in values
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise Phase5KI20PreflightError(f"안전하지 않은 KI20 preflight 경로입니다: {relative}") from exc


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
            raise Phase5KI20PreflightError(f"기존 불변 KI20 preflight 파일과 다릅니다: {path}")
        return
    _atomic_replace(path, payload, mode=mode)


def _assert_file(repo_root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, dict):
        raise Phase5KI20PreflightError(f"{label} 입력 계약이 없습니다.")
    path = _safe_path(repo_root, str(value.get("path", "")))
    if sha256_file(path) != value.get("sha256"):
        raise Phase5KI20PreflightError(f"{label} SHA-256이 다릅니다.")
    return path


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.1.0"
        or config.get("training_version") != "v1.1.0"
        or config.get("canonical_plan_version") != "3.2.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("run_id") != "KI20-MIX-v2-THROUGHPUT"
    ):
        raise Phase5KI20PreflightError("KI20 training v1.1 identity가 다릅니다.")
    gate = config.get("required_gate_v2")
    if (
        not isinstance(gate, dict)
        or gate.get("version") != "v2.0.0"
        or gate.get("experiment_continuation_allowed") is not True
        or gate.get("production_promotion_allowed") is not False
    ):
        raise Phase5KI20PreflightError("Gate v2 요구 계약이 다릅니다.")
    _assert_file(repo_root, gate, "required_gate_v2")
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
        raise Phase5KI20PreflightError("KI20 모델 계약이 다릅니다.")
    snapshot = _safe_path(repo_root, str(model.get("local_subdir", "")))
    if (
        not snapshot.is_dir()
        or sha256_file(snapshot / "model.safetensors") != model.get("model_sha256")
        or sha256_file(_safe_path(repo_root, model["chat_template_path"]))
        != model.get("chat_template_sha256")
        or sha256_file(_safe_path(repo_root, model["package_lock"]))
        != model.get("package_lock_sha256")
    ):
        raise Phase5KI20PreflightError("KI20 모델 snapshot·template·lock hash가 다릅니다.")
    if config.get("runtime_versions") != {
        "torch": "2.13.0+cu130",
        "torch_cuda": "13.0",
        "transformers": "4.57.6",
        "trl": "1.12.0",
        "bitsandbytes": "0.50.2",
        "datasets": "4.7.0",
    }:
        raise Phase5KI20PreflightError("KI20 runtime 버전 계약이 다릅니다.")
    data = config.get("data")
    if (
        not isinstance(data, dict)
        or data.get("canonical_build_id") != "build-6f32d52c2868"
        or data.get("rows") != 20_000
        or data.get("manifest") != "manifests/mix20k_v2.jsonl"
        or data.get("manifest_sha256")
        != "731ace0ac5584fd97fc38f157a4ecdb1babedefd79e2ec5b2d755fa26e48a550"
    ):
        raise Phase5KI20PreflightError("KI20 데이터 계약이 다릅니다.")
    manifest_path = _safe_path(repo_root, data["canonical_root"]) / data["manifest"]
    if sha256_file(manifest_path) != data["manifest_sha256"]:
        raise Phase5KI20PreflightError("KI20 manifest hash가 다릅니다.")
    dev = config.get("dev_monitor")
    if not isinstance(dev, dict) or dev.get("rows") != 70:
        raise Phase5KI20PreflightError("dev monitor 계약이 다릅니다.")
    _assert_file(repo_root, dev, "dev_monitor")
    historical = config.get("historical_ki10")
    if (
        not isinstance(historical, dict)
        or historical.get("comparison_role") != "historical_reference_not_strict_causal_control"
    ):
        raise Phase5KI20PreflightError("KI10 역사적 비교 계약이 다릅니다.")
    checkpoint = _safe_path(repo_root, str(historical.get("checkpoint", "")))
    if not checkpoint.is_dir() or sha256_file(checkpoint / "model.safetensors") != historical.get(
        "checkpoint_model_sha256"
    ):
        raise Phase5KI20PreflightError("KI10 historical checkpoint hash가 다릅니다.")
    objective = config.get("objective")
    if objective != {
        "name": "assistant_only_token_nll",
        "trainer_loss_type": "chunked_nll",
        "token_weighting": "uniform_over_supervised_assistant_tokens",
        "weighted_sampler": False,
        "dft": False,
        "absolute_loss_is_quality_gate": False,
    }:
        raise Phase5KI20PreflightError("KI20 loss objective 계약이 다릅니다.")
    training = config.get("fixed_training")
    required_training = {
        "max_length": 768,
        "pad_to_multiple_of": 8,
        "effective_batch_size": 8,
        "num_train_epochs": 1,
        "expected_optimizer_steps": 2500,
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
        "torch_compile": False,
        "group_by_length": False,
        "shuffle_dataset": True,
        "seed": 42,
        "data_seed": 42,
    }
    if training != required_training:
        raise Phase5KI20PreflightError("KI20 고정 학습 계약이 다릅니다.")
    benchmark = config.get("benchmark")
    if (
        not isinstance(benchmark, dict)
        or benchmark.get("confirmation_variable") != "PHASE5_BENCHMARK"
        or benchmark.get("confirmation_value") != "KI20-MIX-v2-THROUGHPUT"
        or benchmark.get("train_batch_candidates")
        != [
            {"per_device_train_batch_size": 1, "gradient_accumulation_steps": 8},
            {"per_device_train_batch_size": 2, "gradient_accumulation_steps": 4},
            {"per_device_train_batch_size": 4, "gradient_accumulation_steps": 2},
        ]
        or benchmark.get("worker_candidates") != [0, 2]
        or benchmark.get("eval_batch_candidates") != [1, 2, 4, 8]
        or benchmark.get("longest_observed_stress_steps") != 8
        or benchmark.get("warmup_optimizer_steps") != 10
        or benchmark.get("timed_optimizer_steps") != 50
        or benchmark.get("throughput_windows") != 5
        or benchmark.get("effective_batch_size") != 8
        or benchmark.get("representative_rows") != 480
        or benchmark.get("dataloader_prefetch_factor") != 2
        or benchmark.get("dataloader_pin_memory") is not True
        or benchmark.get("dataloader_persistent_workers") is not False
    ):
        raise Phase5KI20PreflightError("KI20 benchmark 후보 계약이 다릅니다.")
    limits = benchmark.get("limits")
    if limits != {
        "max_total_gpu_memory_used_mib": 16384,
        "required_free_gpu_memory_mib": 0,
        "min_system_ram_available_bytes": 4294967296,
        "max_swap_growth_bytes": 536870912,
        "ram_swap_hard_gate": False,
        "eval_loss_tolerance": 0.0001,
        "minimum_throughput_improvement_percent": 5.0,
        "throughput_tie_percent": 3.0,
    }:
        raise Phase5KI20PreflightError("KI20 benchmark 한계 계약이 다릅니다.")
    if config.get("tokenizer_compatibility") != {
        "preserve_phase4_and_ki10_tokenization": True,
        "fix_mistral_regex_applied": False,
        "disposition": "동일 tokenizer revision의 기존 Phase 4·KI10 byte semantics를 유지하고 별도 데이터·run version에서만 변경 검토",
    }:
        raise Phase5KI20PreflightError("KI20 tokenizer 호환성 계약이 다릅니다.")
    if config.get("governance") != {
        "ki10_rerun_allowed": False,
        "ki20_full_training_performed": False,
        "full_training_command_exposed": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "benchmark_optimizer_steps_are_temporary": True,
        "temporary_models_and_optimizer_states_retained": False,
    }:
        raise Phase5KI20PreflightError("KI20 preflight governance가 다릅니다.")
    if config.get("outputs") != {
        "private_root": "runs/KI20-MIX-v2-THROUGHPUT/v1.1.0/{preflight_build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-preflight/v1.1.0/{preflight_build_id}",
    }:
        raise Phase5KI20PreflightError("KI20 preflight 출력 경로가 다릅니다.")
    if config.get("implementation_files") != [
        "scripts/training/phase5_ki20_preflight.py"
    ]:
        raise Phase5KI20PreflightError("KI20 preflight 구현 fingerprint 목록이 다릅니다.")
    return {"status": "valid", "training_version": "v1.1.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "KI20 training v1.1 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "training_version": config["training_version"],
        "run_id": config["run_id"],
        "required_gate_v2": config["required_gate_v2"],
        "model": config["model"],
        "runtime_versions": config["runtime_versions"],
        "data": config["data"],
        "dev_monitor": config["dev_monitor"],
        "historical_ki10": config["historical_ki10"],
        "objective": config["objective"],
        "fixed_training": config["fixed_training"],
        "benchmark": config["benchmark"],
        "tokenizer_compatibility": config["tokenizer_compatibility"],
        "governance": config["governance"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    preflight_build_id = f"preflight-{build_sha256[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "preflight_build_id": preflight_build_id,
        "private_root": _safe_path(
            repo_root,
            config["outputs"]["private_root"].format(
                preflight_build_id=preflight_build_id
            ),
        ),
        "public_root": _safe_path(
            repo_root,
            config["outputs"]["public_root"].format(
                preflight_build_id=preflight_build_id
            ),
        ),
    }


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, raw = line.split(":", 1)
        if name in {"MemAvailable", "SwapTotal", "SwapFree"}:
            values[name] = int(raw.strip().split()[0]) * 1024
    return values


def _nvidia_memory() -> tuple[int, int]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Phase5KI20PreflightError("nvidia-smi GPU memory 조회가 실패했습니다.")
    first = result.stdout.strip().splitlines()[0]
    used, total = (int(value.strip()) for value in first.split(","))
    return used, total


class _MemoryMonitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.peak_gpu_used_mib = 0
        self.gpu_total_mib = 0
        self.minimum_ram_available_bytes = 2**63 - 1
        info = _meminfo()
        self.swap_used_at_start_bytes = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
        self.maximum_swap_used_bytes = self.swap_used_at_start_bytes
        self.error: str | None = None

    def _poll(self) -> None:
        while not self.stop_event.is_set():
            try:
                used, total = _nvidia_memory()
                info = _meminfo()
                self.peak_gpu_used_mib = max(self.peak_gpu_used_mib, used)
                self.gpu_total_mib = total
                self.minimum_ram_available_bytes = min(
                    self.minimum_ram_available_bytes, info.get("MemAvailable", 0)
                )
                swap_used = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
                self.maximum_swap_used_bytes = max(self.maximum_swap_used_bytes, swap_used)
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                self.error = str(exc)
            self.stop_event.wait(0.1)

    def __enter__(self) -> Self:
        self.thread = threading.Thread(target=self._poll, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.minimum_ram_available_bytes == 2**63 - 1:
            info = _meminfo()
            self.minimum_ram_available_bytes = info.get("MemAvailable", 0)
        if self.gpu_total_mib == 0:
            used, total = _nvidia_memory()
            self.peak_gpu_used_mib = max(self.peak_gpu_used_mib, used)
            self.gpu_total_mib = total

    def summary(self) -> dict[str, Any]:
        return {
            "peak_total_gpu_memory_used_mib": self.peak_gpu_used_mib,
            "gpu_total_mib": self.gpu_total_mib,
            "minimum_system_ram_available_bytes": self.minimum_ram_available_bytes,
            "swap_used_at_start_bytes": self.swap_used_at_start_bytes,
            "maximum_swap_used_bytes": self.maximum_swap_used_bytes,
            "swap_growth_bytes": max(
                0, self.maximum_swap_used_bytes - self.swap_used_at_start_bytes
            ),
            "monitor_error": self.error,
        }


def _memory_safe(memory: dict[str, Any], limits: dict[str, Any]) -> bool:
    physical_cap = min(
        int(limits["max_total_gpu_memory_used_mib"]), int(memory["gpu_total_mib"])
    )
    return bool(
        memory.get("monitor_error") is None
        and int(memory["peak_total_gpu_memory_used_mib"]) <= physical_cap
    )


def _load_training_rows(
    context: dict[str, Any], repo_root: Path
) -> list[dict[str, Any]]:
    config = context["config"]
    phase4_context = prepare_phase4_context(
        repo_root, _safe_path(repo_root, config["data"]["preflight_config"])
    )
    records, _, _, _ = load_candidate_staging_records(phase4_context, repo_root)
    manifest_path = (
        _safe_path(repo_root, config["data"]["canonical_root"])
        / config["data"]["manifest"]
    )
    manifest = read_jsonl(manifest_path, "KI20 v1.1 manifest")
    if len(manifest) != 20_000:
        raise Phase5KI20PreflightError("KI20 manifest가 20,000행이 아닙니다.")
    rows: list[dict[str, Any]] = []
    for item in manifest:
        record = records.get(item["id"])
        if (
            record is None
            or record["meta"]["phase4_parent_record_sha256"] != item["record_sha256"]
        ):
            raise Phase5KI20PreflightError("KI20 manifest/staging hash가 다릅니다.")
        rows.append(
            {
                "id": item["id"],
                "mix_axis": item["mix_axis"],
                "total_tokens": item["total_tokens"],
                "assistant_tokens": item["assistant_tokens"],
                "messages": record["messages"],
            }
        )
    return rows


def _stable_rank(namespace: str, identifier: str) -> str:
    return hashlib.sha256(f"{namespace}\0{identifier}".encode()).hexdigest()


def select_fixed_subsets(
    rows: Sequence[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    effective = config["benchmark"]["effective_batch_size"]
    stress_count = config["benchmark"]["longest_observed_stress_steps"] * effective
    stress = sorted(rows, key=lambda row: (-row["total_tokens"], row["id"]))[:stress_count]
    quotas = {
        "nemotron_saju": 163,
        "bazi_sft": 96,
        "aihub_empathy_single": 36,
        "aihub_empathy_multiturn": 36,
        "yeji_shensha_derived": 24,
        "deterministic_saju_qa": 48,
        "saju_diary_bridge": 77,
    }
    selected: list[dict[str, Any]] = []
    for axis, quota in quotas.items():
        candidates = [row for row in rows if row["mix_axis"] == axis]
        candidates.sort(key=lambda row: _stable_rank("ki20-throughput-v1.1", row["id"]))
        selected.extend(candidates[:quota])
    representative = sorted(
        selected, key=lambda row: _stable_rank("ki20-throughput-order-v1.1", row["id"])
    )
    if len(stress) != 64 or len(representative) != 480:
        raise Phase5KI20PreflightError("KI20 benchmark 고정 subset 크기가 다릅니다.")
    digest_payload = {
        "stress_ids": [row["id"] for row in stress],
        "representative_ids": [row["id"] for row in representative],
    }
    metadata = {
        "stress_rows": len(stress),
        "stress_min_total_tokens": min(row["total_tokens"] for row in stress),
        "stress_max_total_tokens": max(row["total_tokens"] for row in stress),
        "representative_rows": len(representative),
        "representative_axis_counts": dict(sorted(Counter(row["mix_axis"] for row in representative).items())),
        "selection_sha256": sha256_json(digest_payload),
    }
    return stress, representative, {"metadata": metadata, "private": digest_payload}


def _supervised_tokens(tokenizer: Any, rows: Sequence[dict[str, Any]], max_length: int) -> list[int]:
    values: list[int] = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["messages"],
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            truncation=True,
            max_length=max_length,
            add_generation_prompt=False,
        )
        count = int(sum(encoded["assistant_masks"]))
        if count <= 0:
            raise Phase5KI20PreflightError("benchmark assistant supervision token이 0입니다.")
        values.append(count)
    return values


def _benchmark_sft_args(
    sft_config: Any,
    output_dir: Path,
    config: dict[str, Any],
    *,
    batch_size: int,
    accumulation: int,
    workers: int,
    max_steps: int,
    pad_to_multiple_of: int,
) -> Any:
    fixed = config["fixed_training"]
    return sft_config(
        output_dir=str(output_dir),
        overwrite_output_dir=False,
        do_train=True,
        do_eval=False,
        per_device_train_batch_size=batch_size,
        gradient_accumulation_steps=accumulation,
        max_steps=max_steps,
        learning_rate=fixed["learning_rate"],
        weight_decay=fixed["weight_decay"],
        max_grad_norm=fixed["max_grad_norm"],
        warmup_ratio=0.0,
        lr_scheduler_type=fixed["lr_scheduler_type"],
        optim=fixed["optim"],
        logging_strategy="no",
        save_strategy="no",
        eval_strategy="no",
        seed=fixed["seed"],
        data_seed=fixed["data_seed"],
        bf16=fixed["bf16"],
        fp16=fixed["fp16"],
        tf32=fixed["tf32"],
        gradient_checkpointing=fixed["gradient_checkpointing"],
        gradient_checkpointing_kwargs={
            "use_reentrant": fixed["gradient_checkpointing_use_reentrant"]
        },
        report_to=[],
        dataloader_num_workers=workers,
        dataloader_pin_memory=config["benchmark"]["dataloader_pin_memory"],
        dataloader_persistent_workers=config["benchmark"][
            "dataloader_persistent_workers"
        ],
        dataloader_prefetch_factor=(
            config["benchmark"]["dataloader_prefetch_factor"] if workers else None
        ),
        remove_unused_columns=True,
        skip_memory_metrics=True,
        max_length=fixed["max_length"],
        pad_to_multiple_of=pad_to_multiple_of,
        assistant_only_loss=fixed["assistant_only_loss"],
        packing=fixed["packing"],
        padding_free=fixed["padding_free"],
        loss_type=fixed["loss_type"],
        shuffle_dataset=False,
        dataset_num_proc=1,
        trust_remote_code=True,
        torch_compile=fixed["torch_compile"],
        group_by_length=False,
        use_liger_kernel=False,
        activation_offloading=False,
        load_best_model_at_end=False,
    )


def _make_benchmark_callback(
    torch: Any,
    callback_class: Any,
    *,
    warmup_steps: int,
    total_steps: int,
    window_steps: int,
) -> tuple[Any, dict[str, Any]]:
    state_values: dict[str, Any] = {
        "timed_started": None,
        "window_started": None,
        "window_seconds": [],
        "gradient_finite": None,
        "gradient_nonzero": None,
    }

    class BenchmarkCallback(callback_class):
        def on_pre_optimizer_step(
            self, args: Any, state: Any, control: Any, **kwargs: Any
        ) -> None:
            del args, state, control
            if state_values["gradient_finite"] is not None:
                return
            finite = True
            nonzero = False
            for parameter in kwargs["model"].parameters():
                gradient = parameter.grad
                if gradient is None:
                    continue
                finite = finite and bool(torch.isfinite(gradient).all().item())
                nonzero = nonzero or bool(torch.count_nonzero(gradient).item())
            state_values["gradient_finite"] = finite
            state_values["gradient_nonzero"] = nonzero
            if not finite or not nonzero:
                raise Phase5KI20PreflightError("benchmark gradient가 유한·nonzero가 아닙니다.")

        def on_step_begin(
            self, args: Any, state: Any, control: Any, **kwargs: Any
        ) -> None:
            del args, control, kwargs
            if state.global_step == warmup_steps and state_values["timed_started"] is None:
                now = time.monotonic()
                state_values["timed_started"] = now
                state_values["window_started"] = now

        def on_step_end(
            self, args: Any, state: Any, control: Any, **kwargs: Any
        ) -> None:
            del args, control, kwargs
            if (
                state.global_step > warmup_steps
                and (state.global_step - warmup_steps) % window_steps == 0
            ):
                now = time.monotonic()
                state_values["window_seconds"].append(
                    now - state_values["window_started"]
                )
                state_values["window_started"] = now
            if state.global_step > total_steps:
                raise Phase5KI20PreflightError("benchmark optimizer step이 계약을 초과했습니다.")

    return BenchmarkCallback(), state_values


def _runtime_context(context: dict[str, Any]) -> dict[str, Any]:
    config = dict(context["config"])
    config["training"] = config["fixed_training"]
    return {"config": config}


def _release_runtime(torch: Any | None, trainer: Any, model: Any) -> None:
    del trainer, model
    gc.collect()
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()


def _run_training_phase(
    context: dict[str, Any],
    repo_root: Path,
    rows: Sequence[dict[str, Any]],
    *,
    batch_size: int,
    accumulation: int,
    workers: int,
    max_steps: int,
    pad_to_multiple_of: int,
    warmup_steps: int,
    supervised_tokens: Sequence[int] | None,
    label: str,
) -> dict[str, Any]:
    torch = trainer = model = None
    limits = context["config"]["benchmark"]["limits"]
    temporary_root = repo_root / "runs/PHASE5-BENCHMARK-TEMP"
    temporary_root.mkdir(parents=True, exist_ok=True)
    try:
        with _MemoryMonitor() as monitor, tempfile.TemporaryDirectory(
            prefix=f"{label}-", dir=temporary_root
        ) as temporary:
            (
                torch,
                Dataset,
                TrainerCallback,
                SFTConfig,
                SFTTrainer,
                model,
                tokenizer,
                runtime,
            ) = _load_runtime(_runtime_context(context), repo_root)
            torch.cuda.reset_peak_memory_stats(0)
            dataset = Dataset.from_list([{"messages": row["messages"]} for row in rows])
            total_steps = max_steps
            window_steps = (
                context["config"]["benchmark"]["timed_optimizer_steps"]
                // context["config"]["benchmark"]["throughput_windows"]
                if warmup_steps
                else max_steps
            )
            callback, probe = _make_benchmark_callback(
                torch,
                TrainerCallback,
                warmup_steps=warmup_steps,
                total_steps=total_steps,
                window_steps=window_steps,
            )
            args = _benchmark_sft_args(
                SFTConfig,
                Path(temporary),
                context["config"],
                batch_size=batch_size,
                accumulation=accumulation,
                workers=workers,
                max_steps=max_steps,
                pad_to_multiple_of=pad_to_multiple_of,
            )
            trainer = SFTTrainer(
                model=model,
                args=args,
                train_dataset=dataset,
                processing_class=tokenizer,
                callbacks=[callback],
            )
            started = time.monotonic()
            train_output = trainer.train()
            torch.cuda.synchronize()
            elapsed = time.monotonic() - started
            peak_torch = int(torch.cuda.max_memory_allocated(0))
            optimizer_steps = int(trainer.state.global_step)
            train_loss = float(train_output.training_loss)
        memory = monitor.summary()
        if not math.isfinite(train_loss):
            raise Phase5KI20PreflightError("benchmark training loss가 유한하지 않습니다.")
        if optimizer_steps != max_steps:
            raise Phase5KI20PreflightError(
                f"benchmark optimizer step이 다릅니다: {optimizer_steps} != {max_steps}"
            )
        window_rates: list[float] = []
        if warmup_steps:
            expected_windows = context["config"]["benchmark"]["throughput_windows"]
            if len(probe["window_seconds"]) != expected_windows or supervised_tokens is None:
                raise Phase5KI20PreflightError("benchmark timed window가 완결되지 않았습니다.")
            effective = batch_size * accumulation
            start_index = warmup_steps * effective
            window_examples = window_steps * effective
            for index, seconds in enumerate(probe["window_seconds"]):
                token_start = start_index + index * window_examples
                token_end = token_start + window_examples
                token_count = sum(supervised_tokens[token_start:token_end])
                window_rates.append(token_count / seconds)
        result = {
            "status": "passed",
            "label": label,
            "per_device_train_batch_size": batch_size,
            "gradient_accumulation_steps": accumulation,
            "effective_batch_size": batch_size * accumulation,
            "dataloader_num_workers": workers,
            "optimizer_steps": optimizer_steps,
            "elapsed_seconds": round(elapsed, 6),
            "training_loss": train_loss,
            "loss_finite": True,
            "gradient_finite": probe["gradient_finite"],
            "gradient_nonzero": probe["gradient_nonzero"],
            "torch_peak_memory_allocated_bytes": peak_torch,
            "memory": memory,
            "memory_safe": _memory_safe(memory, limits),
            "window_supervised_tokens_per_second": [round(value, 6) for value in window_rates],
            "median_supervised_tokens_per_second": (
                round(statistics.median(window_rates), 6) if window_rates else None
            ),
            "runtime": runtime,
            "temporary_output_retained": False,
        }
        if not result["memory_safe"]:
            result["status"] = "failed_memory_contract"
        return result
    finally:
        _release_runtime(torch, trainer, model)


def _run_candidate(
    context: dict[str, Any],
    repo_root: Path,
    stress_rows: Sequence[dict[str, Any]],
    representative_rows: Sequence[dict[str, Any]],
    representative_tokens: Sequence[int],
    candidate: dict[str, int],
) -> dict[str, Any]:
    batch_size = candidate["per_device_train_batch_size"]
    accumulation = candidate["gradient_accumulation_steps"]
    try:
        stress = _run_training_phase(
            context,
            repo_root,
            stress_rows,
            batch_size=batch_size,
            accumulation=accumulation,
            workers=0,
            max_steps=context["config"]["benchmark"]["longest_observed_stress_steps"],
            pad_to_multiple_of=768,
            warmup_steps=0,
            supervised_tokens=None,
            label=f"batch{batch_size}-stress",
        )
        if stress["status"] != "passed":
            return {"status": "failed", "stress": stress, **candidate}
        timed = _run_training_phase(
            context,
            repo_root,
            representative_rows,
            batch_size=batch_size,
            accumulation=accumulation,
            workers=0,
            max_steps=context["config"]["benchmark"]["warmup_optimizer_steps"]
            + context["config"]["benchmark"]["timed_optimizer_steps"],
            pad_to_multiple_of=8,
            warmup_steps=context["config"]["benchmark"]["warmup_optimizer_steps"],
            supervised_tokens=representative_tokens,
            label=f"batch{batch_size}-timed",
        )
        passed = timed["status"] == "passed"
        return {
            "status": "passed" if passed else "failed",
            **candidate,
            "effective_batch_size": batch_size * accumulation,
            "stress": stress,
            "timed": timed,
            "median_supervised_tokens_per_second": timed[
                "median_supervised_tokens_per_second"
            ],
            "peak_total_gpu_memory_used_mib": max(
                stress["memory"]["peak_total_gpu_memory_used_mib"],
                timed["memory"]["peak_total_gpu_memory_used_mib"],
            ),
            "memory_safe": stress["memory_safe"] and timed["memory_safe"],
            "finite_loss_and_gradient": bool(
                stress["loss_finite"]
                and stress["gradient_finite"]
                and stress["gradient_nonzero"]
                and timed["loss_finite"]
                and timed["gradient_finite"]
                and timed["gradient_nonzero"]
            ),
        }
    except Exception as exc:  # noqa: BLE001 - OOM 후보도 집계하고 다음 후보를 검증한다.
        return {
            "status": "failed",
            **candidate,
            "effective_batch_size": batch_size * accumulation,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "memory_safe": False,
            "finite_loss_and_gradient": False,
        }


def _candidate_process_target(
    result_queue: Any,
    context: dict[str, Any],
    repo_root: Path,
    stress_rows: Sequence[dict[str, Any]],
    representative_rows: Sequence[dict[str, Any]],
    representative_tokens: Sequence[int],
    candidate: dict[str, int],
) -> None:
    result_queue.put(
        _run_candidate(
            context,
            repo_root,
            stress_rows,
            representative_rows,
            representative_tokens,
            candidate,
        )
    )


def _training_phase_process_target(
    result_queue: Any,
    context: dict[str, Any],
    repo_root: Path,
    rows: Sequence[dict[str, Any]],
    kwargs: dict[str, Any],
) -> None:
    try:
        result_queue.put(_run_training_phase(context, repo_root, rows, **kwargs))
    except Exception as exc:  # noqa: BLE001 - 격리 child의 실패를 구조화한다.
        result_queue.put(
            {
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "memory_safe": False,
                "gradient_finite": False,
                "gradient_nonzero": False,
                "dataloader_num_workers": kwargs["workers"],
            }
        )


def _cleanup_temporary_prefixes(repo_root: Path, prefixes: Sequence[str]) -> None:
    temporary_root = repo_root / "runs/PHASE5-BENCHMARK-TEMP"
    if not temporary_root.is_dir():
        return
    for prefix in prefixes:
        for path in temporary_root.glob(f"{prefix}-*"):
            if path.is_dir() and path.parent == temporary_root:
                shutil.rmtree(path)


def _isolated_result(
    target: Any,
    args: tuple[Any, ...],
    *,
    repo_root: Path,
    temporary_prefixes: Sequence[str],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    process_context = multiprocessing.get_context("spawn")
    result_queue = process_context.Queue()
    process = process_context.Process(target=target, args=(result_queue, *args))
    process.start()
    process.join()
    _cleanup_temporary_prefixes(repo_root, temporary_prefixes)
    if process.exitcode != 0:
        result = {
            **fallback,
            "status": "failed",
            "error_type": "IsolatedProcessExit",
            "process_exit_code": process.exitcode,
            "memory_safe": False,
        }
    else:
        try:
            result = result_queue.get(timeout=2)
        except queue.Empty:
            result = {
                **fallback,
                "status": "failed",
                "error_type": "MissingIsolatedResult",
                "process_exit_code": process.exitcode,
                "memory_safe": False,
            }
    result_queue.close()
    result_queue.join_thread()
    return result


def _run_candidate_isolated(
    context: dict[str, Any],
    repo_root: Path,
    stress_rows: Sequence[dict[str, Any]],
    representative_rows: Sequence[dict[str, Any]],
    representative_tokens: Sequence[int],
    candidate: dict[str, int],
) -> dict[str, Any]:
    batch_size = candidate["per_device_train_batch_size"]
    return _isolated_result(
        _candidate_process_target,
        (
            context,
            repo_root,
            stress_rows,
            representative_rows,
            representative_tokens,
            candidate,
        ),
        repo_root=repo_root,
        temporary_prefixes=[f"batch{batch_size}-stress", f"batch{batch_size}-timed"],
        fallback={
            **candidate,
            "effective_batch_size": batch_size
            * candidate["gradient_accumulation_steps"],
            "finite_loss_and_gradient": False,
        },
    )


def _run_worker_two_isolated(
    context: dict[str, Any],
    repo_root: Path,
    representative_rows: Sequence[dict[str, Any]],
    representative_tokens: Sequence[int],
    selected_batch: dict[str, Any],
) -> dict[str, Any]:
    kwargs = {
        "batch_size": selected_batch["per_device_train_batch_size"],
        "accumulation": selected_batch["gradient_accumulation_steps"],
        "workers": 2,
        "max_steps": context["config"]["benchmark"]["warmup_optimizer_steps"]
        + context["config"]["benchmark"]["timed_optimizer_steps"],
        "pad_to_multiple_of": 8,
        "warmup_steps": context["config"]["benchmark"]["warmup_optimizer_steps"],
        "supervised_tokens": representative_tokens,
        "label": "workers2-timed",
    }
    return _isolated_result(
        _training_phase_process_target,
        (context, repo_root, representative_rows, kwargs),
        repo_root=repo_root,
        temporary_prefixes=["workers2-timed"],
        fallback={
            "dataloader_num_workers": 2,
            "gradient_finite": False,
            "gradient_nonzero": False,
        },
    )


def select_batch_candidate(
    results: Sequence[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, Any]:
    eligible = [
        result
        for result in results
        if result.get("status") == "passed"
        and result.get("memory_safe")
        and result.get("finite_loss_and_gradient")
        and result.get("effective_batch_size") == 8
    ]
    baseline = next(
        (result for result in eligible if result["per_device_train_batch_size"] == 1),
        None,
    )
    if baseline is None:
        raise Phase5KI20PreflightError("batch 1 baseline benchmark가 통과하지 못했습니다.")
    improvement = float(policy["minimum_throughput_improvement_percent"]) / 100
    challengers = [
        result
        for result in eligible
        if result["per_device_train_batch_size"] > 1
        and result["median_supervised_tokens_per_second"]
        >= baseline["median_supervised_tokens_per_second"] * (1 + improvement)
    ]
    if not challengers:
        return baseline
    fastest = max(result["median_supervised_tokens_per_second"] for result in challengers)
    tie = float(policy["throughput_tie_percent"]) / 100
    tied = [
        result
        for result in challengers
        if result["median_supervised_tokens_per_second"] >= fastest * (1 - tie)
    ]
    return min(
        tied,
        key=lambda result: (
            result["peak_total_gpu_memory_used_mib"],
            result["per_device_train_batch_size"],
        ),
    )


def select_worker_result(
    worker_zero: dict[str, Any], worker_two: dict[str, Any], policy: dict[str, Any]
) -> dict[str, Any]:
    improvement = float(policy["minimum_throughput_improvement_percent"]) / 100
    if (
        worker_two.get("status") == "passed"
        and worker_two.get("memory_safe")
        and worker_two.get("gradient_finite")
        and worker_two.get("gradient_nonzero")
        and worker_two["median_supervised_tokens_per_second"]
        >= worker_zero["median_supervised_tokens_per_second"] * (1 + improvement)
    ):
        return worker_two
    return worker_zero


def _dev_rows(context: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    items = read_jsonl(
        _assert_file(repo_root, context["config"]["dev_monitor"], "dev_monitor"),
        "dev monitor 70",
    )
    rows: list[dict[str, Any]] = []
    for item in items:
        for case in item["cases"]:
            rows.append(
                {
                    "axis": item["source_axis"],
                    "messages": [
                        *case["prompt_messages"],
                        {"role": "assistant", "content": case["reference_assistant"]},
                    ],
                }
            )
    if len(rows) != 70 or set(Counter(row["axis"] for row in rows).values()) != {10}:
        raise Phase5KI20PreflightError("dev monitor 70 축 분포가 다릅니다.")
    return rows


def _tokenize_eval_rows(
    tokenizer: Any, rows: Sequence[dict[str, Any]], max_length: int
) -> list[dict[str, Any]]:
    tokenized: list[dict[str, Any]] = []
    for row in rows:
        encoded = tokenizer.apply_chat_template(
            row["messages"],
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            truncation=True,
            max_length=max_length,
            add_generation_prompt=False,
        )
        labels = [
            token if mask else -100
            for token, mask in zip(
                encoded["input_ids"], encoded["assistant_masks"], strict=True
            )
        ]
        if sum(label != -100 for label in labels[1:]) <= 0:
            raise Phase5KI20PreflightError("dev monitor assistant label이 0입니다.")
        tokenized.append(
            {
                "axis": row["axis"],
                "input_ids": encoded["input_ids"],
                "attention_mask": encoded["attention_mask"],
                "labels": labels,
            }
        )
    return tokenized


def _evaluate_tokenized(
    torch: Any,
    model: Any,
    tokenizer: Any,
    rows: Sequence[dict[str, Any]],
    batch_size: int,
) -> dict[str, Any]:
    from torch.nn import functional

    totals: dict[str, dict[str, float | int]] = defaultdict(
        lambda: {"nll_sum": 0.0, "correct": 0, "tokens": 0}
    )
    started = time.monotonic()
    torch.cuda.reset_peak_memory_stats(0)
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        width = max(len(row["input_ids"]) for row in batch)
        width = min(768, ((width + 7) // 8) * 8)
        input_ids = []
        attention = []
        labels = []
        for row in batch:
            padding = width - len(row["input_ids"])
            input_ids.append(row["input_ids"] + [tokenizer.pad_token_id] * padding)
            attention.append(row["attention_mask"] + [0] * padding)
            labels.append(row["labels"] + [-100] * padding)
        input_tensor = torch.tensor(input_ids, dtype=torch.long, device="cuda")
        attention_tensor = torch.tensor(attention, dtype=torch.long, device="cuda")
        label_tensor = torch.tensor(labels, dtype=torch.long, device="cuda")
        with torch.inference_mode():
            logits = model(input_ids=input_tensor, attention_mask=attention_tensor).logits
        shift_logits = logits[:, :-1, :].float()
        shift_labels = label_tensor[:, 1:]
        for index, row in enumerate(batch):
            active = shift_labels[index] != -100
            tokens = int(active.sum().item())
            if tokens <= 0:
                raise Phase5KI20PreflightError("eval batch active token이 0입니다.")
            nll_sum = functional.cross_entropy(
                shift_logits[index][active], shift_labels[index][active], reduction="sum"
            )
            correct = int(
                (shift_logits[index][active].argmax(dim=-1) == shift_labels[index][active])
                .sum()
                .item()
            )
            axis = str(row["axis"])
            totals[axis]["nll_sum"] += float(nll_sum.item())
            totals[axis]["correct"] += correct
            totals[axis]["tokens"] += tokens
        del input_tensor, attention_tensor, label_tensor, logits, shift_logits, shift_labels
    torch.cuda.synchronize()
    elapsed = time.monotonic() - started
    axis_metrics: dict[str, Any] = {}
    micro_nll = 0.0
    micro_correct = 0
    micro_tokens = 0
    for axis, values in sorted(totals.items()):
        tokens = int(values["tokens"])
        nll = float(values["nll_sum"]) / tokens
        accuracy = int(values["correct"]) / tokens
        axis_metrics[axis] = {
            "nll": round(nll, 9),
            "token_accuracy": round(accuracy, 9),
            "active_tokens": tokens,
        }
        micro_nll += float(values["nll_sum"])
        micro_correct += int(values["correct"])
        micro_tokens += tokens
    return {
        "loss": round(micro_nll / micro_tokens, 9),
        "token_accuracy": round(micro_correct / micro_tokens, 9),
        "active_tokens": micro_tokens,
        "elapsed_seconds": round(elapsed, 6),
        "supervised_tokens_per_second": round(micro_tokens / elapsed, 6),
        "torch_peak_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(0)),
        "axis_metrics": axis_metrics,
        "macro": {
            "nll": round(
                sum(value["nll"] for value in axis_metrics.values()) / len(axis_metrics), 9
            ),
            "token_accuracy": round(
                sum(value["token_accuracy"] for value in axis_metrics.values())
                / len(axis_metrics),
                9,
            ),
        },
        "micro": {
            "nll": round(micro_nll / micro_tokens, 9),
            "token_accuracy": round(micro_correct / micro_tokens, 9),
            "active_tokens": micro_tokens,
        },
    }


def select_eval_batch(
    results: Sequence[dict[str, Any]], tolerance: float
) -> dict[str, Any]:
    baseline = next(
        (result for result in results if result["per_device_eval_batch_size"] == 1), None
    )
    if baseline is None or baseline.get("status") != "passed":
        raise Phase5KI20PreflightError(
            f"eval batch 1 baseline이 통과하지 못했습니다: {baseline}"
        )
    eligible = [
        result
        for result in results
        if result.get("status") == "passed"
        and result.get("memory_safe")
        and abs(result["loss"] - baseline["loss"]) <= tolerance
    ]
    if not eligible:
        raise Phase5KI20PreflightError("loss 동등성을 통과한 eval batch가 없습니다.")
    return max(
        eligible,
        key=lambda result: (
            result["supervised_tokens_per_second"],
            result["per_device_eval_batch_size"],
        ),
    )


def _evaluate_model(
    context: dict[str, Any],
    repo_root: Path,
    rows: Sequence[dict[str, Any]],
    model_path: Path,
    batch_sizes: Sequence[int],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    torch = model = None
    try:
        runtime_context = _runtime_context(context)
        runtime_context["config"] = dict(runtime_context["config"])
        runtime_context["config"]["model"] = dict(runtime_context["config"]["model"])
        runtime_context["config"]["model"]["local_subdir"] = model_path.relative_to(
            repo_root
        ).as_posix()
        (
            torch,
            _Dataset,
            _TrainerCallback,
            _SFTConfig,
            _SFTTrainer,
            model,
            tokenizer,
            runtime,
        ) = _load_runtime(runtime_context, repo_root)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "right"
        model.to("cuda")
        model.eval()
        model.config.use_cache = False
        tokenized = _tokenize_eval_rows(
            tokenizer, rows, context["config"]["fixed_training"]["max_length"]
        )
        results: list[dict[str, Any]] = []
        limits = context["config"]["benchmark"]["limits"]
        for batch_size in batch_sizes:
            try:
                with _MemoryMonitor() as monitor:
                    _evaluate_tokenized(
                        torch,
                        model,
                        tokenizer,
                        tokenized[: min(8, len(tokenized))],
                        batch_size,
                    )
                    measured = _evaluate_tokenized(
                        torch, model, tokenizer, tokenized, batch_size
                    )
                memory = monitor.summary()
                results.append(
                    {
                        "status": (
                            "passed"
                            if _memory_safe(memory, limits)
                            else "failed_memory_contract"
                        ),
                        "per_device_eval_batch_size": batch_size,
                        **{
                            key: measured[key]
                            for key in (
                                "loss",
                                "token_accuracy",
                                "active_tokens",
                                "elapsed_seconds",
                                "supervised_tokens_per_second",
                                "torch_peak_memory_allocated_bytes",
                            )
                        },
                        "memory": memory,
                        "memory_safe": _memory_safe(memory, limits),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - OOM 후보를 탈락 처리한다.
                results.append(
                    {
                        "status": "failed",
                        "per_device_eval_batch_size": batch_size,
                        "memory_safe": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                torch.cuda.empty_cache()
        selected = (
            select_eval_batch(results, float(limits["eval_loss_tolerance"]))
            if any(result["per_device_eval_batch_size"] == 1 for result in results)
            else results[0]
        )
        if selected.get("status") != "passed":
            raise Phase5KI20PreflightError("선택한 eval batch가 통과하지 못했습니다.")
        axis = _evaluate_tokenized(
            torch,
            model,
            tokenizer,
            tokenized,
            int(selected["per_device_eval_batch_size"]),
        )
        return results, axis, runtime
    finally:
        _release_runtime(torch, None, model)


def _evaluate_historical_ki10(
    context: dict[str, Any],
    repo_root: Path,
    rows: Sequence[dict[str, Any]],
    batch_size: int,
) -> dict[str, Any]:
    _results, axis, _runtime = _evaluate_model(
        context,
        repo_root,
        rows,
        _safe_path(repo_root, context["config"]["historical_ki10"]["checkpoint"]),
        [batch_size],
    )
    return axis


def _confirmation(config: dict[str, Any]) -> None:
    benchmark = config["benchmark"]
    if os.environ.get(benchmark["confirmation_variable"]) != benchmark["confirmation_value"]:
        raise Phase5KI20PreflightError(
            f"실행에는 {benchmark['confirmation_variable']}={benchmark['confirmation_value']} 확인값이 필요합니다."
        )


def _cached_result(
    path: Path, *, build_sha256: str, selection_sha256: str
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = load_json(path, "KI20 benchmark resume cache")
    if (
        value.get("preflight_build_sha256") != build_sha256
        or value.get("selection_sha256") != selection_sha256
        or not isinstance(value.get("result"), dict)
    ):
        raise Phase5KI20PreflightError(f"KI20 benchmark cache identity가 다릅니다: {path}")
    return value["result"]


def _store_cached_result(
    path: Path,
    result: dict[str, Any],
    *,
    build_sha256: str,
    selection_sha256: str,
) -> None:
    payload = _json_bytes(
        {
            "schema_version": "1.1.0",
            "preflight_build_sha256": build_sha256,
            "selection_sha256": selection_sha256,
            "result": result,
        }
    )
    _write_once(path, payload, mode=PRIVATE_FILE_MODE)


def run_benchmark(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    _confirmation(context["config"])
    if context["public_root"].exists() or context["private_root"].exists():
        raise Phase5KI20PreflightError("같은 KI20 preflight build 경로가 이미 존재합니다.")
    rows = _load_training_rows(context, repo_root)
    stress, representative, selection = select_fixed_subsets(rows, context["config"])
    del rows
    gc.collect()
    torch = tokenizer = None
    try:
        (
            torch,
            _Dataset,
            _TrainerCallback,
            _SFTConfig,
            _SFTTrainer,
            model,
            tokenizer,
            _runtime,
        ) = _load_runtime(_runtime_context(context), repo_root)
        representative_tokens = _supervised_tokens(
            tokenizer,
            representative,
            context["config"]["fixed_training"]["max_length"],
        )
    finally:
        _release_runtime(torch, None, locals().get("model"))
        if "model" in locals():
            model = None
        tokenizer = None
    cache_root = (
        context["private_root"].parent
        / f".{context['preflight_build_id']}.resume-cache"
    )
    cache_root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    cache_root.chmod(PRIVATE_DIR_MODE)
    selection_sha256 = selection["metadata"]["selection_sha256"]
    cache_reused: list[str] = []
    candidate_results: list[dict[str, Any]] = []
    for candidate in context["config"]["benchmark"]["train_batch_candidates"]:
        print(
            json.dumps(
                {"event": "batch_candidate_start", **candidate}, ensure_ascii=False
            ),
            flush=True,
        )
        cache_path = cache_root / (
            f"batch-{candidate['per_device_train_batch_size']}.json"
        )
        result = _cached_result(
            cache_path,
            build_sha256=context["build_sha256"],
            selection_sha256=selection_sha256,
        )
        if result is None:
            result = _run_candidate_isolated(
                context,
                repo_root,
                stress,
                representative,
                representative_tokens,
                candidate,
            )
            _store_cached_result(
                cache_path,
                result,
                build_sha256=context["build_sha256"],
                selection_sha256=selection_sha256,
            )
        else:
            cache_reused.append(cache_path.name)
        candidate_results.append(result)
        print(
            json.dumps(
                {
                    "event": "batch_candidate_complete",
                    "batch_size": candidate["per_device_train_batch_size"],
                    "status": result["status"],
                    "median_supervised_tokens_per_second": result.get(
                        "median_supervised_tokens_per_second"
                    ),
                    "peak_total_gpu_memory_used_mib": result.get(
                        "peak_total_gpu_memory_used_mib"
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    limits = context["config"]["benchmark"]["limits"]
    selected_batch = select_batch_candidate(candidate_results, limits)
    worker_zero = selected_batch["timed"]
    print(json.dumps({"event": "worker_candidate_start", "workers": 2}), flush=True)
    worker_cache = cache_root / "workers-2.json"
    worker_two = _cached_result(
        worker_cache,
        build_sha256=context["build_sha256"],
        selection_sha256=selection_sha256,
    )
    if worker_two is None:
        worker_two = _run_worker_two_isolated(
            context, repo_root, representative, representative_tokens, selected_batch
        )
        _store_cached_result(
            worker_cache,
            worker_two,
            build_sha256=context["build_sha256"],
            selection_sha256=selection_sha256,
        )
    else:
        cache_reused.append(worker_cache.name)
    selected_worker = select_worker_result(worker_zero, worker_two, limits)
    dev_rows = _dev_rows(context, repo_root)
    print(json.dumps({"event": "eval_batch_benchmark_start"}), flush=True)
    eval_results, base_axis, runtime = _evaluate_model(
        context,
        repo_root,
        dev_rows,
        _safe_path(repo_root, context["config"]["model"]["local_subdir"]),
        context["config"]["benchmark"]["eval_batch_candidates"],
    )
    selected_eval = select_eval_batch(eval_results, float(limits["eval_loss_tolerance"]))
    print(json.dumps({"event": "historical_ki10_axis_eval_start"}), flush=True)
    ki10_axis = _evaluate_historical_ki10(
        context,
        repo_root,
        dev_rows,
        int(selected_eval["per_device_eval_batch_size"]),
    )
    selected_training = {
        "per_device_train_batch_size": selected_batch["per_device_train_batch_size"],
        "gradient_accumulation_steps": selected_batch[
            "gradient_accumulation_steps"
        ],
        "effective_batch_size": 8,
        "dataloader_num_workers": selected_worker["dataloader_num_workers"],
        "dataloader_prefetch_factor": (
            context["config"]["benchmark"]["dataloader_prefetch_factor"]
            if selected_worker["dataloader_num_workers"]
            else None
        ),
        "dataloader_pin_memory": True,
        "dataloader_persistent_workers": False,
        "per_device_eval_batch_size": selected_eval["per_device_eval_batch_size"],
        "group_by_length": False,
        "packing": False,
        "padding_free": False,
        "expected_optimizer_steps": 2500,
    }
    resolved = {
        "schema_version": "1.1.0",
        "training_version": "v1.1.0",
        "run_id": context["config"]["run_id"],
        "preflight_build_id": context["preflight_build_id"],
        "preflight_build_sha256": context["build_sha256"],
        "base_config_sha256": sha256_file(context["config_path"]),
        "objective": context["config"]["objective"],
        "fixed_training": context["config"]["fixed_training"],
        "selected_runtime_training": selected_training,
        "selection_policy": {
            "random_sample_order": True,
            "group_by_length": False,
            "minimum_throughput_improvement_percent": limits[
                "minimum_throughput_improvement_percent"
            ],
            "throughput_tie_percent": limits["throughput_tie_percent"],
            "gpu_memory_cap_mib": limits["max_total_gpu_memory_used_mib"],
            "required_free_gpu_memory_mib": 0,
        },
        "ki20_full_training_performed": False,
        "full_training_execution_enabled": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
    }
    report = {
        "schema_version": "1.1.0",
        "status": "ki20_preflight_ready",
        "training_version": "v1.1.0",
        "run_id": context["config"]["run_id"],
        "preflight_build_id": context["preflight_build_id"],
        "preflight_build_sha256": context["build_sha256"],
        "gate_v2": context["config"]["required_gate_v2"],
        "subset": selection["metadata"],
        "resume_cache_reused": sorted(cache_reused),
        "batch_candidates": candidate_results,
        "selected_batch": {
            "per_device_train_batch_size": selected_batch[
                "per_device_train_batch_size"
            ],
            "gradient_accumulation_steps": selected_batch[
                "gradient_accumulation_steps"
            ],
            "median_supervised_tokens_per_second": selected_batch[
                "median_supervised_tokens_per_second"
            ],
            "peak_total_gpu_memory_used_mib": selected_batch[
                "peak_total_gpu_memory_used_mib"
            ],
        },
        "worker_candidates": [worker_zero, worker_two],
        "selected_workers": selected_worker["dataloader_num_workers"],
        "eval_batch_candidates": eval_results,
        "selected_eval_batch_size": selected_eval["per_device_eval_batch_size"],
        "forward_only_axis_metrics": {
            "base_snapshot": base_axis,
            "historical_ki10_final": ki10_axis,
            "comparison_role": "diagnostic_only_not_strict_causal_10k_vs_20k",
            "future_ki20_final": None,
        },
        "runtime": runtime,
        "selected_training": selected_training,
        "objective": context["config"]["objective"],
        "forward_backward_optimizer_preflight_passed": True,
        "temporary_models_and_optimizer_states_retained": False,
        "ki10_rerun_performed": False,
        "ki20_full_training_performed": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "raw_rows_or_ids_in_public_report": False,
    }
    private_values = {
        "fixed_subset_ids.json": _json_bytes(selection["private"]),
        "benchmark_detailed.json": _json_bytes(report),
        "config.resolved.json": _json_bytes(resolved),
    }
    public_values = {
        "preflight_report.json": _json_bytes(report),
        "config.resolved.json": _json_bytes(resolved),
    }
    manifest = {
        "schema_version": "1.1.0",
        "preflight_build_id": context["preflight_build_id"],
        "preflight_build_sha256": context["build_sha256"],
        "private_files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for name, payload in sorted(private_values.items())
        },
        "public_files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for name, payload in sorted(public_values.items())
        },
    }
    manifest_payload = _json_bytes(manifest)
    private_values["build_manifest.json"] = manifest_payload
    public_values["build_manifest.json"] = manifest_payload
    for root, values, mode in (
        (context["private_root"], private_values, PRIVATE_FILE_MODE),
        (context["public_root"], public_values, PUBLIC_FILE_MODE),
    ):
        root.mkdir(parents=True, exist_ok=False, mode=PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else 0o755)
        root.chmod(PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else 0o755)
        for relative, payload in values.items():
            _write_once(root / relative, payload, mode=mode)
    result = verify_preflight(context, repo_root)
    shutil.rmtree(cache_root)
    return result


def verify_preflight(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    del repo_root
    report_path = context["public_root"] / "preflight_report.json"
    resolved_path = context["public_root"] / "config.resolved.json"
    manifest_path = context["public_root"] / "build_manifest.json"
    report = load_json(report_path, "KI20 preflight report")
    resolved = load_json(resolved_path, "KI20 resolved config")
    manifest = load_json(manifest_path, "KI20 preflight manifest")
    for relative, meta in manifest["public_files"].items():
        path = context["public_root"] / relative
        if sha256_file(path) != meta["sha256"] or path.stat().st_size != meta["bytes"]:
            raise Phase5KI20PreflightError(f"KI20 public manifest 검증 실패: {relative}")
    selected = resolved["selected_runtime_training"]
    if (
        report.get("status") != "ki20_preflight_ready"
        or report.get("forward_backward_optimizer_preflight_passed") is not True
        or selected["effective_batch_size"] != 8
        or selected["expected_optimizer_steps"] != 2500
        or selected["group_by_length"] is not False
        or selected["packing"] is not False
        or selected["padding_free"] is not False
        or resolved.get("ki20_full_training_performed") is not False
        or resolved.get("full_training_execution_enabled") is not False
        or resolved.get("production_promotion_allowed") is not False
        or resolved.get("blind_source_test_inspected") is not False
    ):
        raise Phase5KI20PreflightError("KI20 resolved preflight governance가 다릅니다.")
    cap = context["config"]["benchmark"]["limits"][
        "max_total_gpu_memory_used_mib"
    ]
    for candidate in report["batch_candidates"]:
        if candidate.get("status") == "passed" and candidate[
            "peak_total_gpu_memory_used_mib"
        ] > cap:
            raise Phase5KI20PreflightError("통과한 batch 후보가 16 GiB GPU 상한을 넘었습니다.")
    return {
        "status": "ki20_preflight_ready",
        "training_version": "v1.1.0",
        "preflight_build_id": context["preflight_build_id"],
        "preflight_build_sha256": context["build_sha256"],
        "selected_runtime_training": selected,
        "ki20_full_training_performed": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KI20 Phase 5 v1.1 preflight")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "KI20 v1.1 config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "benchmark":
                result = (
                    run_benchmark(context, REPO_ROOT)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "preflight_build_id": context["preflight_build_id"],
                        "writes_performed": False,
                        "ki20_full_training_performed": False,
                    }
                )
            else:
                result = verify_preflight(context, REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계에서 구조화 실패를 반환한다.
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
