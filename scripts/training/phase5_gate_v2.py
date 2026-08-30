# phase5_gate_v2.py - 기존 KI10 출력을 Gate v2로 재채점하고 handoff 45건만 추가 생성한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.phase5_split_v1_2 import (
    prepare_context as prepare_split_context,
)
from scripts.evaluation.phase5_split_v1_2 import verify_split
from scripts.preflight.phase4_common import (
    load_json,
    read_jsonl,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)
from scripts.training.phase5_quality_v2 import score_gate_v2

DEFAULT_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/phase5-quality-gate-v2.0.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644


class Phase5GateV2Error(RuntimeError):
    """Gate v2의 입력·생성·출력 계약 위반."""


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
        raise Phase5GateV2Error(f"안전하지 않은 Gate v2 경로입니다: {relative}") from exc


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
            raise Phase5GateV2Error(f"기존 불변 Gate v2 파일과 다릅니다: {path}")
        return
    _atomic_replace(path, payload, mode=mode)


def _assert_file(value: Any, repo_root: Path, label: str) -> Path:
    if not isinstance(value, dict):
        raise Phase5GateV2Error(f"{label} 입력 계약이 없습니다.")
    path = _safe_path(repo_root, str(value.get("path", "")))
    digest = value.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or sha256_file(path) != digest:
        raise Phase5GateV2Error(f"{label} SHA-256이 다릅니다.")
    return path


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "2.0.0"
        or config.get("gate_version") != "v2.0.0"
        or config.get("canonical_plan_version") != "3.2.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("run_id") != "KI10-MIX-v2"
        or config.get("run_build_id") != "run-e6b712f0d45e"
    ):
        raise Phase5GateV2Error("Gate v2 identity가 다릅니다.")
    split = config.get("evaluation_split")
    if (
        not isinstance(split, dict)
        or split.get("version") != "v1.2.0"
        or not isinstance(split.get("build_id"), str)
        or not str(split["build_id"]).startswith("build-")
        or split.get("parent_membership_modified") is not False
        or split.get("blind_source_test_inspected") is not False
    ):
        raise Phase5GateV2Error("Gate v2 evaluation split 계약이 다릅니다.")
    split_config = _safe_path(repo_root, str(split.get("config", "")))
    if sha256_file(split_config) != split.get("config_sha256"):
        raise Phase5GateV2Error("evaluation split v1.2 config hash가 다릅니다.")
    split_context = prepare_split_context(repo_root, split_config)
    if (
        split_context["build_id"] != split["build_id"]
        or split_context["build_sha256"] != split.get("build_sha256")
    ):
        raise Phase5GateV2Error("evaluation split v1.2 build identity가 다릅니다.")
    expected_inputs = {
        "existing_generations",
        "training_summary",
        "reload_summary",
        "historical_gate_v1",
        "model_checkpoint",
    }
    if set(config.get("inputs", {})) != expected_inputs:
        raise Phase5GateV2Error("Gate v2 입력 목록이 다릅니다.")
    for label in expected_inputs - {"model_checkpoint"}:
        _assert_file(config["inputs"][label], repo_root, f"inputs.{label}")
    checkpoint = config["inputs"]["model_checkpoint"]
    checkpoint_path = _safe_path(repo_root, checkpoint["path"])
    if not checkpoint_path.is_dir() or sha256_file(checkpoint_path / "model.safetensors") != checkpoint["sha256"]:
        raise Phase5GateV2Error("KI10 final checkpoint hash가 다릅니다.")
    generation = config.get("generation")
    if generation != {
        "confirmation_variable": "PHASE5_GATE_V2",
        "confirmation_value": "KI10-MIX-v2",
        "new_handoff_cases": 45,
        "batch_size": 4,
        "max_new_tokens": 128,
        "do_sample": False,
        "blind_source_test_access_allowed": False,
    }:
        raise Phase5GateV2Error("Gate v2 생성 계약이 다릅니다.")
    thresholds = config.get("thresholds")
    if (
        not isinstance(thresholds, dict)
        or thresholds.get("expected_generation_cases") != 1045
        or thresholds.get("expected_denominators")
        != {
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
        }
        or thresholds.get("hard")
        != {"generation_clean_min_percent": 98.0}
        or thresholds.get("quality")
        != {
            "typed_deterministic_min_percent": 90.0,
            "branch_policy_min_percent": 90.0,
            "shensha_min_percent": 90.0,
            "handoff_action_min_percent": 95.0,
            "foreign_sentence_max_percent": 3.0,
            "empathy_confusion_max_percent": 5.0,
            "persona_confusion_max_percent": 5.0,
        }
    ):
        raise Phase5GateV2Error("Gate v2 threshold 계약이 다릅니다.")
    if config.get("governance") != {
        "gate_v1_preserved_as_history": True,
        "experiment_continuation_and_quality_split": True,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "full_ki20_training_allowed_by_this_command": False,
    }:
        raise Phase5GateV2Error("Gate v2 governance가 다릅니다.")
    if config.get("outputs") != {
        "private_root": "runs/KI10-MIX-v2/gate-v2.0.0/{gate_build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-gate/v2.0.0/KI10-MIX-v2/{gate_build_id}",
    }:
        raise Phase5GateV2Error("Gate v2 출력 경로가 다릅니다.")
    if config.get("implementation_files") != [
        "scripts/training/phase5_quality_v2.py",
        "scripts/training/phase5_gate_v2.py",
    ]:
        raise Phase5GateV2Error("Gate v2 구현 fingerprint 목록이 다릅니다.")
    return {"status": "valid", "gate_version": "v2.0.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "Phase 5 Gate v2 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "gate_version": config["gate_version"],
        "run_id": config["run_id"],
        "run_build_id": config["run_build_id"],
        "evaluation_split": config["evaluation_split"],
        "inputs": config["inputs"],
        "generation": config["generation"],
        "thresholds": config["thresholds"],
        "governance": config["governance"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    gate_build_id = f"gate-{build_sha256[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "gate_build_id": gate_build_id,
        "private_root": _safe_path(
            repo_root, config["outputs"]["private_root"].format(gate_build_id=gate_build_id)
        ),
        "public_root": _safe_path(
            repo_root, config["outputs"]["public_root"].format(gate_build_id=gate_build_id)
        ),
    }


def _split_dependency(context: dict[str, Any], repo_root: Path) -> tuple[dict[str, Any], Path]:
    split_contract = context["config"]["evaluation_split"]
    split_context = prepare_split_context(
        repo_root, _safe_path(repo_root, split_contract["config"])
    )
    result = verify_split(split_context, repo_root)
    if (
        result["build_id"] != split_contract["build_id"]
        or result["build_sha256"] != split_contract["build_sha256"]
        or result["scorer_validation"]["reference_pass_percent"] != 100.0
        or result["scorer_validation"]["mutation_reject_percent"] != 100.0
    ):
        raise Phase5GateV2Error("evaluation split v1.2 재검증이 실패했습니다.")
    return result, split_context["private_root"]


def _confirmation(config: dict[str, Any]) -> None:
    generation = config["generation"]
    if os.environ.get(generation["confirmation_variable"]) != generation["confirmation_value"]:
        raise Phase5GateV2Error(
            f"실행에는 {generation['confirmation_variable']}={generation['confirmation_value']} 확인값이 필요합니다."
        )


def generate_handoff(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    _confirmation(context["config"])
    _split_result, split_root = _split_dependency(context, repo_root)
    handoff = read_jsonl(split_root / "eval/missing_chart_handoff_50.jsonl", "handoff 50")
    additions = [row for row in handoff if row.get("origin") == "v1.2_addition"]
    if len(additions) != 45:
        raise Phase5GateV2Error("추가 생성할 handoff가 45건이 아닙니다.")
    output_path = context["private_root"] / "additional_handoff_generations.jsonl"
    metadata_path = context["private_root"] / "generation_metadata.json"
    if output_path.exists() or metadata_path.exists():
        if not output_path.is_file() or not metadata_path.is_file():
            raise Phase5GateV2Error("Gate v2 기존 생성 경로가 일반 파일이 아닙니다.")
        rows = read_jsonl(output_path, "existing handoff generations")
        metadata = load_json(metadata_path, "existing handoff generation metadata")
        if len(rows) != 45 or metadata.get("output_sha256") != sha256_file(output_path):
            raise Phase5GateV2Error("기존 handoff 생성 산출물이 불완전합니다.")
        return {**metadata, "status": "already_generated"}

    try:
        import torch
        import transformers
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as exc:
        raise Phase5GateV2Error("Gate v2 생성 runtime을 import하지 못했습니다.") from exc
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise Phase5GateV2Error("Gate v2 생성에는 단일 CUDA GPU가 필요합니다.")
    checkpoint = _safe_path(repo_root, context["config"]["inputs"]["model_checkpoint"]["path"])
    tokenizer = AutoTokenizer.from_pretrained(
        checkpoint, local_files_only=True, trust_remote_code=True
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"
    model = AutoModelForCausalLM.from_pretrained(
        checkpoint,
        local_files_only=True,
        trust_remote_code=True,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).to("cuda")
    model.eval()
    model.config.use_cache = True
    batch_size = context["config"]["generation"]["batch_size"]
    max_new_tokens = context["config"]["generation"]["max_new_tokens"]
    generated: list[dict[str, Any]] = []
    started = time.monotonic()
    for start in range(0, len(additions), batch_size):
        batch = additions[start : start + batch_size]
        prompts = [
            tokenizer.apply_chat_template(
                row["prompt_messages"], tokenize=False, add_generation_prompt=True
            )
            for row in batch
        ]
        encoded = tokenizer(prompts, return_tensors="pt", padding=True).to("cuda")
        with torch.inference_mode():
            tokens = model.generate(
                **encoded,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        prompt_width = encoded["input_ids"].shape[1]
        outputs = tokenizer.batch_decode(tokens[:, prompt_width:], skip_special_tokens=True)
        for row, output in zip(batch, outputs, strict=True):
            generated.append(
                {
                    "schema_version": "2.0.0",
                    "eval_id": row["eval_id"],
                    "case_id": row["case_id"],
                    "category": row["category"],
                    "source_axis": row["source_axis"],
                    "prompt_messages": row["prompt_messages"],
                    "automated_contract_v2": row["automated_contract_v2"],
                    "output": output.strip(),
                }
            )
        print(
            json.dumps(
                {"event": "handoff_generation_progress", "completed": len(generated), "total": 45},
                ensure_ascii=False,
            ),
            flush=True,
        )
    elapsed = round(time.monotonic() - started, 3)
    payload = _jsonl_bytes(generated)
    metadata = {
        "schema_version": "2.0.0",
        "status": "generated",
        "cases": len(generated),
        "elapsed_seconds": elapsed,
        "batch_size": batch_size,
        "max_new_tokens": max_new_tokens,
        "output_sha256": hashlib.sha256(payload).hexdigest(),
        "checkpoint_model_sha256": context["config"]["inputs"]["model_checkpoint"]["sha256"],
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "gpu_name": torch.cuda.get_device_name(0),
        "blind_source_test_accessed": False,
    }
    context["private_root"].mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    context["private_root"].chmod(PRIVATE_DIR_MODE)
    _write_once(output_path, payload, mode=PRIVATE_FILE_MODE)
    _write_once(metadata_path, _json_bytes(metadata), mode=PRIVATE_FILE_MODE)
    return metadata


def _scoring_rows(context: dict[str, Any], repo_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    split_result, split_root = _split_dependency(context, repo_root)
    overlays = read_jsonl(split_root / "eval/contract_overlay_v2.jsonl", "Gate v2 overlay")
    overlay_by_id = {(row["eval_id"], row["case_id"]): row for row in overlays}
    existing_path = _assert_file(
        context["config"]["inputs"]["existing_generations"], repo_root, "existing_generations"
    )
    existing = read_jsonl(existing_path, "KI10 existing generations")
    if len(existing) != 1000:
        raise Phase5GateV2Error("기존 KI10 generation이 1,000건이 아닙니다.")
    joined: list[dict[str, Any]] = []
    overlay_hits = 0
    for row in existing:
        overlay = overlay_by_id.get((row.get("eval_id"), row.get("case_id")))
        contract = row.get("automated_contract")
        if overlay is not None:
            contract = overlay["automated_contract_v2"]
            overlay_hits += 1
        if not isinstance(contract, dict):
            raise Phase5GateV2Error("기존 generation 계약이 없습니다.")
        joined.append({**row, "automated_contract_v2": contract})
    if overlay_hits != 130:
        raise Phase5GateV2Error(f"Gate v2 overlay 결합 수가 다릅니다: {overlay_hits}")
    additions_path = context["private_root"] / "additional_handoff_generations.jsonl"
    additions = read_jsonl(additions_path, "additional handoff generations")
    if len(additions) != 45:
        raise Phase5GateV2Error("추가 handoff generation이 45건이 아닙니다.")
    return [*joined, *additions], split_result


def _technical(context: dict[str, Any], repo_root: Path, split_result: dict[str, Any]) -> dict[str, bool]:
    training = load_json(
        _assert_file(context["config"]["inputs"]["training_summary"], repo_root, "training_summary"),
        "KI10 training summary",
    )
    reload = load_json(
        _assert_file(context["config"]["inputs"]["reload_summary"], repo_root, "reload_summary"),
        "KI10 reload summary",
    )
    gradient = training.get("gradient_probe", {})
    loss = training.get("loss_summary", {})
    return {
        "artifact_identity_and_hashes": True,
        "scorer_reference_and_mutation_validation": split_result["scorer_validation"]
        == {
            "reference_cases": 175,
            "reference_passed": 175,
            "deliberate_mutations": 175,
            "deliberate_mutations_failed": 175,
            "reference_pass_percent": 100.0,
            "mutation_reject_percent": 100.0,
        },
        "finite_loss_and_gradient": bool(
            gradient.get("finite")
            and gradient.get("nonzero")
            and loss.get("losses_finite")
            and loss.get("grad_norms_finite")
        ),
        "exact_optimizer_steps": training.get("optimizer_steps")
        == training.get("expected_optimizer_steps")
        == 1250,
        "checkpoint_reload": bool(
            training.get("final_reload_passed")
            and reload.get("status") == "passed"
            and reload.get("new_process") is True
            and reload.get("nonempty_outputs") == reload.get("task_count") == 5
        ),
    }


def _report(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    rows, split_result = _scoring_rows(context, repo_root)
    technical = _technical(context, repo_root, split_result)
    result = score_gate_v2(
        rows, thresholds=context["config"]["thresholds"], technical=technical
    )
    generation_metadata = load_json(
        context["private_root"] / "generation_metadata.json", "Gate v2 generation metadata"
    )
    historical = load_json(
        _assert_file(context["config"]["inputs"]["historical_gate_v1"], repo_root, "historical_gate_v1"),
        "historical Gate v1",
    )
    result.update(
        {
            "gate_build_id": context["gate_build_id"],
            "gate_build_sha256": context["build_sha256"],
            "run_id": context["config"]["run_id"],
            "run_build_id": context["config"]["run_build_id"],
            "evaluation_split_build_id": split_result["build_id"],
            "existing_generation_cases": 1000,
            "new_handoff_generation_cases": 45,
            "generation_elapsed_seconds": generation_metadata["elapsed_seconds"],
            "existing_generations_sha256": context["config"]["inputs"]["existing_generations"]["sha256"],
            "additional_generations_sha256": generation_metadata["output_sha256"],
            "historical_gate_v1": {
                "status": historical["status"],
                "ki20_promotion_allowed": historical["ki20_promotion_allowed"],
                "preserved_as_history": True,
            },
            "blind_source_test_accessed": False,
            "human_domain_review_performed": False,
            "raw_outputs_in_public_report": False,
        }
    )
    return result


def build_gate(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    report = _report(context, repo_root)
    report_payload = _json_bytes(report)
    public_summary = {**report}
    public_payload = _json_bytes(public_summary)
    manifest = {
        "schema_version": "2.0.0",
        "gate_build_id": context["gate_build_id"],
        "gate_build_sha256": context["build_sha256"],
        "private_files": {
            "gate_v2_detailed.json": {
                "sha256": hashlib.sha256(report_payload).hexdigest(),
                "bytes": len(report_payload),
            },
            "additional_handoff_generations.jsonl": {
                "sha256": sha256_file(context["private_root"] / "additional_handoff_generations.jsonl"),
                "bytes": (context["private_root"] / "additional_handoff_generations.jsonl").stat().st_size,
            },
            "generation_metadata.json": {
                "sha256": sha256_file(context["private_root"] / "generation_metadata.json"),
                "bytes": (context["private_root"] / "generation_metadata.json").stat().st_size,
            },
        },
        "public_files": {
            "gate_v2_summary.json": {
                "sha256": hashlib.sha256(public_payload).hexdigest(),
                "bytes": len(public_payload),
            }
        },
    }
    manifest_payload = _json_bytes(manifest)
    context["private_root"].mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    context["public_root"].mkdir(parents=True, exist_ok=True)
    context["private_root"].chmod(PRIVATE_DIR_MODE)
    context["public_root"].chmod(0o755)
    _write_once(context["private_root"] / "gate_v2_detailed.json", report_payload, mode=PRIVATE_FILE_MODE)
    _write_once(context["private_root"] / "build_manifest.json", manifest_payload, mode=PRIVATE_FILE_MODE)
    _write_once(context["public_root"] / "gate_v2_summary.json", public_payload, mode=PUBLIC_FILE_MODE)
    _write_once(context["public_root"] / "build_manifest.json", manifest_payload, mode=PUBLIC_FILE_MODE)
    return verify_gate(context, repo_root)


def verify_gate(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    expected = _report(context, repo_root)
    public_path = context["public_root"] / "gate_v2_summary.json"
    if public_path.is_symlink() or load_json(public_path, "Gate v2 public summary") != expected:
        raise Phase5GateV2Error("Gate v2 공개 요약 재검증이 실패했습니다.")
    manifest = load_json(context["public_root"] / "build_manifest.json", "Gate v2 manifest")
    for relative, meta in manifest["public_files"].items():
        path = context["public_root"] / relative
        if sha256_file(path) != meta["sha256"] or path.stat().st_size != meta["bytes"]:
            raise Phase5GateV2Error(f"Gate v2 공개 manifest 검증 실패: {relative}")
    return {
        "status": expected["status"],
        "gate_version": "v2.0.0",
        "gate_build_id": context["gate_build_id"],
        "gate_build_sha256": context["build_sha256"],
        "experiment_continuation_allowed": expected["experiment_continuation_allowed"],
        "quality_target_status": expected["quality_target_status"],
        "production_promotion_allowed": False,
        "blind_source_test_accessed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 Gate v2")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    generate = commands.add_parser("generate-handoff")
    generate.add_argument("--execute", action="store_true")
    build = commands.add_parser("build-gate")
    build.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "Phase 5 Gate v2 config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "generate-handoff":
                result = (
                    generate_handoff(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "gate_build_id": context["gate_build_id"], "writes_performed": False}
                )
            elif args.command == "build-gate":
                result = (
                    build_gate(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "gate_build_id": context["gate_build_id"], "writes_performed": False}
                )
            else:
                result = verify_gate(context, REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계에서 구조화 실패를 반환한다.
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
