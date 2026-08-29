# phase5_readiness_v1_2.py - 의미 감사·평가 v1.1·실제 runner를 KI10 실행 계약에 묶는다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.phase5_split_v1_1 import (
    prepare_context as prepare_evaluation_context,
)
from scripts.evaluation.phase5_split_v1_1 import verify_split_v1_1
from scripts.preflight.phase4_common import (
    load_json,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)
from scripts.training.phase5_readiness_v1_1 import (
    prepare_context as prepare_parent_context,
)
from scripts.training.phase5_readiness_v1_1 import verify_readiness as verify_parent
from scripts.training.pretraining_audit import prepare_context as prepare_audit_context
from scripts.training.pretraining_audit import verify_audit

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.2.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")


class Phase5ReadinessV12Error(RuntimeError):
    """KI10 자동 승격 Gate를 포함한 Phase 5 readiness v1.2 계약 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise Phase5ReadinessV12Error(f"안전하지 않은 readiness 경로입니다: {relative}") from exc


def _assert_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise Phase5ReadinessV12Error(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.2.0"
        or config.get("canonical_plan_version") != "3.2.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("readiness_version") != "v1.2.0"
    ):
        raise Phase5ReadinessV12Error("Phase 5 readiness v1.2 identity가 다릅니다.")
    parent = config.get("parent_readiness")
    if (
        not isinstance(parent, dict)
        or parent.get("version") != "v1.1.0"
        or parent.get("build_id") != "build-201010b37e40"
        or parent.get("status") != "ready_for_explicit_phase5_execution_with_sealed_blind"
        or parent.get("phase5_training_performed") is not False
    ):
        raise Phase5ReadinessV12Error("readiness v1.1 부모 계약이 다릅니다.")
    for key in ("config_sha256", "build_sha256", "private_manifest_sha256", "public_manifest_sha256"):
        _assert_sha(parent.get(key), f"parent_readiness.{key}")
    parent_path = _safe_path(repo_root, str(parent.get("config", "")))
    if sha256_file(parent_path) != parent["config_sha256"]:
        raise Phase5ReadinessV12Error("readiness v1.1 config hash가 다릅니다.")

    for key, version in (("pretraining_audit", "v1.0.0"), ("evaluation_split", "v1.1.0")):
        value = config.get(key)
        if (
            not isinstance(value, dict)
            or value.get("version") != version
            or BUILD_ID_PATTERN.fullmatch(str(value.get("build_id", ""))) is None
        ):
            raise Phase5ReadinessV12Error(f"{key} 부모 identity가 다릅니다.")
        for sha_key in ("config_sha256", "build_sha256", "public_manifest_sha256"):
            _assert_sha(value.get(sha_key), f"{key}.{sha_key}")
        path = _safe_path(repo_root, str(value.get("config", "")))
        if sha256_file(path) != value["config_sha256"]:
            raise Phase5ReadinessV12Error(f"{key} config hash가 다릅니다.")
    audit = config["pretraining_audit"]
    if (
        audit.get("baseline_training_allowed") is not True
        or audit.get("dataset_mutation_required_before_ki10") is not False
        or audit.get("production_quality_claim_allowed") is not False
        or audit.get("ki20_promotion_allowed") is not False
    ):
        raise Phase5ReadinessV12Error("학습 전 감사 governance 계약이 다릅니다.")
    evaluation = config["evaluation_split"]
    if (
        evaluation.get("parent_membership_modified") is not False
        or evaluation.get("persona_guard_rows") != 50
        or evaluation.get("blind_source_test_inspected") is not False
    ):
        raise Phase5ReadinessV12Error("평가 split v1.1 governance 계약이 다릅니다.")

    runner = config.get("training_runner")
    if (
        not isinstance(runner, dict)
        or runner.get("version") != "v1.0.0"
        or runner.get("confirmation_variable") != "PHASE5_TRAINING"
        or runner.get("ki10_confirmation") != "KI10-MIX-v2"
        or runner.get("ki20_requires_ki10_gate") is not True
        or runner.get("ki20_initializes_from_base") is not True
    ):
        raise Phase5ReadinessV12Error("Phase 5 runner 계약이 다릅니다.")
    _assert_sha(runner.get("config_sha256"), "training_runner.config_sha256")
    runner_path = _safe_path(repo_root, str(runner.get("config", "")))
    if sha256_file(runner_path) != runner["config_sha256"]:
        raise Phase5ReadinessV12Error("Phase 5 runner config hash가 다릅니다.")
    implementation_sha256 = runner.get("implementation_sha256")
    expected_runner_files = {
        "scripts/training/phase5_quality.py",
        "scripts/training/phase5_train.py",
    }
    if (
        not isinstance(implementation_sha256, dict)
        or set(implementation_sha256) != expected_runner_files
    ):
        raise Phase5ReadinessV12Error("Phase 5 runner 구현 hash 목록이 다릅니다.")
    for relative, digest in implementation_sha256.items():
        _assert_sha(digest, f"training_runner.{relative}")
        if sha256_file(_safe_path(repo_root, relative)) != digest:
            raise Phase5ReadinessV12Error(
                f"Phase 5 runner 구현 hash가 다릅니다: {relative}"
            )

    outputs = config.get("outputs")
    if outputs != {
        "private_root": "data/derived/saju_1b_baseline/phase5-readiness/v1.2.0/{build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-readiness/v1.2.0/{build_id}",
    }:
        raise Phase5ReadinessV12Error("readiness v1.2 출력 경로가 다릅니다.")
    for value in outputs.values():
        _safe_path(repo_root, value.format(build_id="build-000000000000"))
    if config.get("governance") != {
        "training_promotion_allowed": True,
        "baseline_training_allowed": True,
        "dataset_mutation_required_before_ki10": False,
        "production_quality_claim_allowed": False,
        "ki20_promotion_allowed": False,
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
        "blind_source_test_inspected": False,
    }:
        raise Phase5ReadinessV12Error("readiness v1.2 governance가 다릅니다.")
    if config.get("implementation_files") != [
        "scripts/training/phase5_readiness_v1_2.py"
    ]:
        raise Phase5ReadinessV12Error("readiness v1.2 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "readiness_version": "v1.2.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "Phase 5 readiness v1.2 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "readiness_version": config["readiness_version"],
        "parent_readiness": config["parent_readiness"],
        "pretraining_audit": config["pretraining_audit"],
        "evaluation_split": config["evaluation_split"],
        "training_runner": config["training_runner"],
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
        "private_root": _safe_path(
            repo_root, config["outputs"]["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_path(
            repo_root, config["outputs"]["public_root"].format(build_id=build_id)
        ),
    }


def _verify_dependencies(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    config = context["config"]
    parent_contract = config["parent_readiness"]
    parent_context = prepare_parent_context(
        repo_root, _safe_path(repo_root, parent_contract["config"])
    )
    parent = verify_parent(parent_context, repo_root)
    if (
        parent.get("build_id") != parent_contract["build_id"]
        or parent.get("build_sha256") != parent_contract["build_sha256"]
    ):
        raise Phase5ReadinessV12Error("readiness v1.1 재검증이 실패했습니다.")
    audit_contract = config["pretraining_audit"]
    audit_context = prepare_audit_context(
        repo_root, _safe_path(repo_root, audit_contract["config"])
    )
    audit = verify_audit(audit_context, repo_root)
    if (
        audit.get("build_id") != audit_contract["build_id"]
        or audit.get("build_sha256") != audit_contract["build_sha256"]
        or audit.get("baseline_training_allowed") is not True
        or audit.get("dataset_mutation_required_before_ki10") is not False
    ):
        raise Phase5ReadinessV12Error("학습 전 감사 재검증이 실패했습니다.")
    evaluation_contract = config["evaluation_split"]
    evaluation_context = prepare_evaluation_context(
        repo_root, _safe_path(repo_root, evaluation_contract["config"])
    )
    evaluation = verify_split_v1_1(evaluation_context, repo_root)
    if (
        evaluation.get("build_id") != evaluation_contract["build_id"]
        or evaluation.get("build_sha256") != evaluation_contract["build_sha256"]
        or evaluation.get("parent_membership_modified") is not False
        or evaluation.get("blind_source_test_inspected") is not False
    ):
        raise Phase5ReadinessV12Error("평가 split v1.1 재검증이 실패했습니다.")
    from scripts.training.phase5_train import validate_contract as validate_runner

    runner_path = _safe_path(repo_root, config["training_runner"]["config"])
    validate_runner(load_json(runner_path, "Phase 5 runner config"), repo_root)
    return {
        "parent_context": parent_context,
        "parent": parent,
        "audit": audit,
        "evaluation_context": evaluation_context,
        "evaluation": evaluation,
    }


def _payloads(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    dependencies = _verify_dependencies(context, repo_root)
    config = context["config"]
    parent_root: Path = dependencies["parent_context"]["private_root"]
    monitor = (parent_root / "eval/dev_monitor_70.jsonl").read_bytes()
    if hashlib.sha256(monitor).hexdigest() != "aa61d2a763e3194e3a25561a3030c74bebb002c702ef8469c27a1bc22a2bcb31":
        raise Phase5ReadinessV12Error("dev monitor byte hash가 다릅니다.")
    private_values: dict[str, bytes] = {"eval/dev_monitor_70.jsonl": monitor}
    for key in ("ki10", "ki20"):
        parent_input = load_json(parent_root / f"run_inputs/{key}.json", f"{key} run input")
        run_id = parent_input["run_name"]
        parent_input.update(
            {
                "schema_version": "1.2.0",
                "pretraining_audit": config["pretraining_audit"],
                "evaluation_split_v1_1": config["evaluation_split"],
                "training_runner": config["training_runner"],
                "training": load_json(
                    _safe_path(repo_root, config["training_runner"]["config"]),
                    "runner config",
                )["training"],
                "promotion_gate": {
                    "ki10_automated_gate_required": True,
                    "ki20_allowed_initially": False,
                    "ki20_requires_all_ki10_thresholds": True,
                    "ki20_initial_checkpoint": "fixed_instruct_snapshot",
                },
                "run_name": run_id,
                "phase5_training_performed": False,
            }
        )
        private_values[f"run_inputs/{key}.json"] = _json_bytes(parent_input)
    summary = {
        "schema_version": "1.2.0",
        "readiness_version": "v1.2.0",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "ready_for_ki10_execution_with_automated_promotion_gate",
        "parent_readiness_build_id": config["parent_readiness"]["build_id"],
        "pretraining_audit_build_id": config["pretraining_audit"]["build_id"],
        "evaluation_split_build_id": config["evaluation_split"]["build_id"],
        "training_runner_version": config["training_runner"]["version"],
        "manifest_rows": {"ki10": 10_000, "ki20": 20_000},
        "dev_monitor_rows": 70,
        "dev_diagnostic_rows": 930,
        "persona_guard_rows": 50,
        "blind_components": 350,
        "blind_rows": 500,
        "baseline_training_allowed": True,
        "dataset_mutation_required_before_ki10": False,
        "production_quality_claim_allowed": False,
        "ki20_promotion_allowed": False,
        "ki20_requires_ki10_automated_gate": True,
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
        "blind_source_test_inspected": False,
        "raw_or_restricted_samples_in_report": False,
    }
    return dict(sorted(private_values.items())), {"readiness_summary.json": _json_bytes(summary)}


def _manifest(
    context: dict[str, Any], artifacts: dict[str, bytes], *, public: bool
) -> bytes:
    return _json_bytes(
        {
            "schema_version": "1.2.0",
            "report_type": (
                "phase5_readiness_v1_2_public_manifest"
                if public
                else "phase5_readiness_v1_2_private_manifest"
            ),
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in sorted(artifacts.items())
            },
            "status": "ready_for_ki10_execution_with_automated_promotion_gate",
            "training_promotion_allowed": True,
            "baseline_training_allowed": True,
            "production_quality_claim_allowed": False,
            "ki20_promotion_allowed": False,
            "phase5_training_performed": False,
            "blind_source_test_inspected": False,
        }
    )


def _atomic_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if path.exists():
            raise Phase5ReadinessV12Error(f"기존 readiness v1.2 산출물을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _git_clean(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and not result.stdout.strip()


def build_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if private_root.exists() or public_root.exists():
        if not private_root.exists() or not public_root.exists():
            raise Phase5ReadinessV12Error("readiness v1.2 private/public 중 한쪽만 있습니다.")
        return {**verify_readiness(context, repo_root), "mode": "reused"}
    if not _git_clean(repo_root):
        raise Phase5ReadinessV12Error("readiness v1.2 생성 전 working tree가 깨끗해야 합니다.")
    private_values, public_values = _payloads(context, repo_root)
    private_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    private_temp = Path(tempfile.mkdtemp(prefix=f".{private_root.name}-", dir=private_root.parent))
    public_temp = Path(tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent))
    private_done = False
    public_done = False
    try:
        for relative, payload in private_values.items():
            _atomic_bytes(private_temp / relative, payload, mode=PRIVATE_FILE_MODE)
        _atomic_bytes(
            private_temp / "build_manifest.json",
            _manifest(context, private_values, public=False),
            mode=PRIVATE_FILE_MODE,
        )
        for path in [private_temp, *[p for p in private_temp.rglob("*") if p.is_dir()]]:
            path.chmod(PRIVATE_DIR_MODE)
        for relative, payload in public_values.items():
            _atomic_bytes(public_temp / relative, payload, mode=PUBLIC_FILE_MODE)
        _atomic_bytes(
            public_temp / "build_manifest.json",
            _manifest(context, public_values, public=True),
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(private_temp, private_root)
        private_done = True
        os.replace(public_temp, public_root)
        public_done = True
    finally:
        if not private_done:
            shutil.rmtree(private_temp, ignore_errors=True)
        if not public_done:
            shutil.rmtree(public_temp, ignore_errors=True)
    return {**verify_readiness(context, repo_root), "mode": "built"}


def verify_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if (
        private_root.is_symlink()
        or public_root.is_symlink()
        or not private_root.is_dir()
        or not public_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) & 0o077
    ):
        raise Phase5ReadinessV12Error("readiness v1.2 경로·권한이 다릅니다.")
    private_values, public_values = _payloads(context, repo_root)
    for root, values, public in (
        (private_root, private_values, False),
        (public_root, public_values, True),
    ):
        manifest_path = root / "build_manifest.json"
        if manifest_path.read_bytes() != _manifest(context, values, public=public):
            raise Phase5ReadinessV12Error("readiness v1.2 manifest가 재현되지 않습니다.")
        for relative, payload in values.items():
            path = root / relative
            mode = PUBLIC_FILE_MODE if public else PRIVATE_FILE_MODE
            if (
                path.is_symlink()
                or not path.is_file()
                or path.read_bytes() != payload
                or stat.S_IMODE(path.stat().st_mode) != mode
            ):
                raise Phase5ReadinessV12Error(f"readiness v1.2 artifact가 다릅니다: {relative}")
    return {
        "status": "verified_ready_for_ki10_with_automated_gate",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "baseline_training_allowed": True,
        "dataset_mutation_required_before_ki10": False,
        "production_quality_claim_allowed": False,
        "ki20_promotion_allowed": False,
        "phase5_training_performed": False,
        "blind_source_test_inspected": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 readiness v1.2 Gate")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "readiness v1.2 config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "build_id": context["build_id"],
                    "build_sha256": context["build_sha256"],
                    "private_root": context["private_root"].relative_to(REPO_ROOT).as_posix(),
                    "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "prepare":
                result = (
                    build_readiness(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "build_id": context["build_id"], "writes_performed": False}
                )
            else:
                result = verify_readiness(context, REPO_ROOT)
    except (Phase5ReadinessV12Error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
