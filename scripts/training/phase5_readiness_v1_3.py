# phase5_readiness_v1_3.py - Gate v2와 KI20 비학습 preflight를 불변 readiness로 묶는다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_json,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.3.0.json"
)
PUBLIC_FILE_MODE = 0o644
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_PATTERNS = {
    "parent_readiness": re.compile(r"^build-[0-9a-f]{12}$"),
    "evaluation_split": re.compile(r"^build-[0-9a-f]{12}$"),
    "quality_gate": re.compile(r"^gate-[0-9a-f]{12}$"),
    "ki20_preflight": re.compile(r"^preflight-[0-9a-f]{12}$"),
}


class Phase5ReadinessV13Error(RuntimeError):
    """Gate v2·KI20 비학습 preflight readiness 계약 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise Phase5ReadinessV13Error(
            f"안전하지 않은 readiness 경로입니다: {relative}"
        ) from exc


def _assert_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise Phase5ReadinessV13Error(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def _verify_file_reference(
    repo_root: Path, value: dict[str, Any], path_key: str, sha_key: str, label: str
) -> Path:
    expected = _assert_sha(value.get(sha_key), f"{label}.{sha_key}")
    path = _safe_path(repo_root, str(value.get(path_key, "")))
    if sha256_file(path) != expected:
        raise Phase5ReadinessV13Error(f"{label} {path_key} hash가 다릅니다.")
    return path


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.3.0"
        or config.get("canonical_plan_version") != "3.3.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("readiness_version") != "v1.3.0"
    ):
        raise Phase5ReadinessV13Error("Phase 5 readiness v1.3 identity가 다릅니다.")

    versions = {
        "parent_readiness": "v1.2.0",
        "evaluation_split": "v1.2.0",
        "quality_gate": "v2.0.0",
        "ki20_preflight": "v1.1.0",
    }
    for key, version in versions.items():
        value = config.get(key)
        if (
            not isinstance(value, dict)
            or value.get("version") != version
            or BUILD_PATTERNS[key].fullmatch(str(value.get("build_id", ""))) is None
        ):
            raise Phase5ReadinessV13Error(f"{key} identity가 다릅니다.")
        _assert_sha(value.get("build_sha256"), f"{key}.build_sha256")
        _verify_file_reference(repo_root, value, "config", "config_sha256", key)
        _verify_file_reference(
            repo_root, value, "public_manifest", "public_manifest_sha256", key
        )

    _verify_file_reference(
        repo_root,
        config["parent_readiness"],
        "readiness_summary",
        "readiness_summary_sha256",
        "parent_readiness",
    )
    _verify_file_reference(
        repo_root,
        config["evaluation_split"],
        "split_summary",
        "split_summary_sha256",
        "evaluation_split",
    )
    _verify_file_reference(
        repo_root,
        config["quality_gate"],
        "gate_summary",
        "gate_summary_sha256",
        "quality_gate",
    )
    _verify_file_reference(
        repo_root,
        config["ki20_preflight"],
        "preflight_report",
        "preflight_report_sha256",
        "ki20_preflight",
    )
    _verify_file_reference(
        repo_root,
        config["ki20_preflight"],
        "resolved_config",
        "resolved_config_sha256",
        "ki20_preflight",
    )

    if config.get("governance") != {
        "experiment_continuation_allowed": True,
        "quality_target_status": "not_met",
        "ki20_preflight_ready": True,
        "ki20_full_training_performed": False,
        "full_training_execution_enabled": False,
        "full_training_requires_explicit_new_confirmation": True,
        "production_promotion_allowed": False,
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "blind_source_test_inspected": False,
    }:
        raise Phase5ReadinessV13Error("readiness v1.3 governance가 다릅니다.")
    if config.get("outputs") != {
        "public_root": "data/reports/saju_1b_baseline/phase5-readiness/v1.3.0/{build_id}"
    }:
        raise Phase5ReadinessV13Error("readiness v1.3 출력 경로가 다릅니다.")
    _safe_path(
        repo_root,
        config["outputs"]["public_root"].format(build_id="build-000000000000"),
    )
    if config.get("implementation_files") != [
        "scripts/training/phase5_readiness_v1_3.py"
    ]:
        raise Phase5ReadinessV13Error("readiness v1.3 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "readiness_version": "v1.3.0"}


def _load_evidence(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    gate = load_json(
        _safe_path(repo_root, config["quality_gate"]["gate_summary"]),
        "Gate v2 summary",
    )
    preflight = load_json(
        _safe_path(repo_root, config["ki20_preflight"]["preflight_report"]),
        "KI20 preflight report",
    )
    resolved = load_json(
        _safe_path(repo_root, config["ki20_preflight"]["resolved_config"]),
        "KI20 resolved config",
    )
    split = load_json(
        _safe_path(repo_root, config["evaluation_split"]["split_summary"]),
        "evaluation split v1.2 summary",
    )
    if (
        gate.get("gate_build_id") != config["quality_gate"]["build_id"]
        or gate.get("gate_build_sha256") != config["quality_gate"]["build_sha256"]
        or gate.get("experiment_continuation_allowed") is not True
        or gate.get("quality_target_status") != "not_met"
        or gate.get("production_promotion_allowed") is not False
        or gate.get("blind_source_test_accessed") is not False
    ):
        raise Phase5ReadinessV13Error("Gate v2 결정이 readiness 계약과 다릅니다.")
    if (
        preflight.get("preflight_build_id") != config["ki20_preflight"]["build_id"]
        or preflight.get("preflight_build_sha256")
        != config["ki20_preflight"]["build_sha256"]
        or preflight.get("status") != "ki20_preflight_ready"
        or preflight.get("ki20_full_training_performed") is not False
        or preflight.get("production_promotion_allowed") is not False
        or preflight.get("blind_source_test_inspected") is not False
        or preflight.get("temporary_models_and_optimizer_states_retained") is not False
    ):
        raise Phase5ReadinessV13Error("KI20 preflight 결정이 readiness 계약과 다릅니다.")
    selected = preflight.get("selected_training")
    if (
        not isinstance(selected, dict)
        or selected.get("per_device_train_batch_size") != 4
        or selected.get("gradient_accumulation_steps") != 2
        or selected.get("effective_batch_size") != 8
        or selected.get("dataloader_num_workers") != 0
        or selected.get("per_device_eval_batch_size") != 8
        or selected.get("expected_optimizer_steps") != 2500
    ):
        raise Phase5ReadinessV13Error("KI20 선택 runtime이 고정 결과와 다릅니다.")
    selected_batch = preflight.get("selected_batch")
    if (
        not isinstance(selected_batch, dict)
        or int(selected_batch.get("peak_total_gpu_memory_used_mib", 0)) >= 16_384
    ):
        raise Phase5ReadinessV13Error("KI20 선택 runtime이 16 GiB 상한을 만족하지 않습니다.")
    if (
        resolved.get("full_training_execution_enabled") is not False
        or resolved.get("ki20_full_training_performed") is not False
    ):
        raise Phase5ReadinessV13Error("KI20 full training 실행이 비활성화되지 않았습니다.")
    if (
        split.get("build_id") != config["evaluation_split"]["build_id"]
        or split.get("build_sha256") != config["evaluation_split"]["build_sha256"]
        or split.get("blind_source_test_inspected") is not False
    ):
        raise Phase5ReadinessV13Error("평가 split v1.2 봉인 계약이 다릅니다.")
    return {"gate": gate, "preflight": preflight, "resolved": resolved, "split": split}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "Phase 5 readiness v1.3 config")
    validate_contract(config, repo_root)
    _load_evidence(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "readiness_version": config["readiness_version"],
        "parent_readiness": config["parent_readiness"],
        "evaluation_split": config["evaluation_split"],
        "quality_gate": config["quality_gate"],
        "ki20_preflight": config["ki20_preflight"],
        "governance_sha256": sha256_json(config["governance"]),
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "build_id": build_id,
        "public_root": _safe_path(
            repo_root,
            config["outputs"]["public_root"].format(build_id=build_id),
        ),
    }


def _payloads(context: dict[str, Any], repo_root: Path) -> dict[str, bytes]:
    evidence = _load_evidence(context["config"], repo_root)
    gate = evidence["gate"]
    preflight = evidence["preflight"]
    summary = {
        "schema_version": "1.3.0",
        "readiness_version": "v1.3.0",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "ki20_preflight_ready_quality_targets_not_met",
        "parent_readiness_build_id": context["config"]["parent_readiness"]["build_id"],
        "evaluation_split_build_id": context["config"]["evaluation_split"]["build_id"],
        "quality_gate_build_id": context["config"]["quality_gate"]["build_id"],
        "ki20_preflight_build_id": context["config"]["ki20_preflight"]["build_id"],
        "experiment_continuation_allowed": True,
        "quality_target_status": "not_met",
        "failed_quality_targets": gate["failed_quality_targets"],
        "ki20_preflight_ready": True,
        "selected_training": preflight["selected_training"],
        "selected_train_peak_total_gpu_memory_used_mib": preflight["selected_batch"][
            "peak_total_gpu_memory_used_mib"
        ],
        "gpu_hard_cap_mib": 16_384,
        "gpu_hard_cap_passed": True,
        "objective": preflight["objective"],
        "ki20_full_training_performed": False,
        "full_training_execution_enabled": False,
        "full_training_requires_explicit_new_confirmation": True,
        "production_promotion_allowed": False,
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "blind_source_test_inspected": False,
        "raw_rows_or_ids_in_public_report": False,
    }
    return {"readiness_summary.json": _json_bytes(summary)}


def _manifest(context: dict[str, Any], artifacts: dict[str, bytes]) -> bytes:
    return _json_bytes(
        {
            "schema_version": "1.3.0",
            "report_type": "phase5_readiness_v1_3_public_manifest",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in sorted(artifacts.items())
            },
            "status": "ki20_preflight_ready_quality_targets_not_met",
            "experiment_continuation_allowed": True,
            "ki20_preflight_ready": True,
            "ki20_full_training_performed": False,
            "full_training_execution_enabled": False,
            "production_promotion_allowed": False,
            "blind_source_test_inspected": False,
        }
    )


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(PUBLIC_FILE_MODE)
        if path.exists():
            raise Phase5ReadinessV13Error(
                f"기존 readiness v1.3 산출물을 덮어쓸 수 없습니다: {path}"
            )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    public_root: Path = context["public_root"]
    if public_root.exists():
        return {**verify_readiness(context, repo_root, require_registry=False), "mode": "reused"}
    artifacts = _payloads(context, repo_root)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent)
    )
    completed = False
    try:
        for relative, payload in artifacts.items():
            _atomic_bytes(temporary / relative, payload)
        _atomic_bytes(temporary / "build_manifest.json", _manifest(context, artifacts))
        os.replace(temporary, public_root)
        completed = True
    finally:
        if not completed and temporary.exists():
            shutil.rmtree(temporary)
    return {**verify_readiness(context, repo_root, require_registry=False), "mode": "built"}


def verify_readiness(
    context: dict[str, Any], repo_root: Path, *, require_registry: bool
) -> dict[str, Any]:
    public_root: Path = context["public_root"]
    artifacts = _payloads(context, repo_root)
    if public_root.is_symlink() or not public_root.is_dir():
        raise Phase5ReadinessV13Error("readiness v1.3 공개 경로가 다릅니다.")
    if (public_root / "build_manifest.json").read_bytes() != _manifest(
        context, artifacts
    ):
        raise Phase5ReadinessV13Error("readiness v1.3 manifest가 재현되지 않습니다.")
    for relative, payload in artifacts.items():
        path = public_root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or path.read_bytes() != payload
            or path.stat().st_mode & 0o777 != PUBLIC_FILE_MODE
        ):
            raise Phase5ReadinessV13Error(
                f"readiness v1.3 artifact가 다릅니다: {relative}"
            )
    if require_registry:
        registry = load_json(
            repo_root / "configs/data_versions/saju_1b_baseline/registry.json",
            "dataset registry",
        )
        approved = registry.get("approved_phase5_readiness")
        summary_sha = hashlib.sha256(artifacts["readiness_summary.json"]).hexdigest()
        manifest_sha = sha256_file(public_root / "build_manifest.json")
        if (
            not isinstance(approved, dict)
            or approved.get("version") != "v1.3.0"
            or approved.get("build_id") != context["build_id"]
            or approved.get("build_sha256") != context["build_sha256"]
            or approved.get("public_manifest_sha256") != manifest_sha
            or approved.get("readiness_summary_sha256") != summary_sha
        ):
            raise Phase5ReadinessV13Error("registry readiness v1.3 포인터가 다릅니다.")
    return {
        "status": "verified_ki20_preflight_ready_quality_targets_not_met",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "experiment_continuation_allowed": True,
        "ki20_preflight_ready": True,
        "ki20_full_training_performed": False,
        "full_training_execution_enabled": False,
        "production_promotion_allowed": False,
        "blind_source_test_inspected": False,
        "registry_verified": require_registry,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 readiness v1.3 Gate")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--execute", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--require-registry", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                load_json(config_path, "readiness v1.3 config"), REPO_ROOT
            )
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "build_id": context["build_id"],
                    "build_sha256": context["build_sha256"],
                    "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "prepare":
                result = (
                    build_readiness(context, REPO_ROOT)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "build_id": context["build_id"],
                        "writes_performed": False,
                    }
                )
            else:
                result = verify_readiness(
                    context, REPO_ROOT, require_registry=args.require_registry
                )
    except (Phase5ReadinessV13Error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
