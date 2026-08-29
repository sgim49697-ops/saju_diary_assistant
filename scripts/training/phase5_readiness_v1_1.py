# phase5_readiness_v1_1.py - 봉인 평가 split을 Phase 5 비학습 실행 계약에 연결한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.phase5_split import (
    EvaluationSplitError,
    verify_split,
)
from scripts.evaluation.phase5_split import (
    prepare_context as prepare_evaluation_context,
)
from scripts.training.phase5_readiness import (
    Phase5ReadinessError,
    _git_clean,
    _load_json,
    _safe_repo_path,
    canonical_json_bytes,
    sha256_file,
)
from scripts.training.phase5_readiness import (
    _build_payloads as build_base_payloads,
)
from scripts.training.phase5_readiness import (
    prepare_context as prepare_base_context,
)
from scripts.training.phase5_readiness import (
    validate_contract as validate_base_contract,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.1.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")


class Phase5ReadinessV11Error(RuntimeError):
    """봉인 평가 split을 포함한 Phase 5 readiness 계약 위반."""


def _json_payload(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_PATTERN.fullmatch(value) is None:
        raise Phase5ReadinessV11Error(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("canonical_plan_version") != "3.1.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("readiness_version") != "v1.1.0"
    ):
        raise Phase5ReadinessV11Error("Phase 5 readiness v1.1 identity가 다릅니다.")

    base = config.get("base_readiness")
    if (
        not isinstance(base, dict)
        or base.get("version") != "v1.0.0"
        or base.get("config")
        != "configs/data_versions/saju_1b_baseline/phase5-readiness-v1.0.0.json"
        or base.get("config_sha256")
        != "6a3765affe49976059991cfdae70374ca3a4d6e46d0a976ae56fa63f95426bb7"
        or base.get("canonical_training_fingerprint_changed") is not False
    ):
        raise Phase5ReadinessV11Error("기존 readiness 부모 계약이 다릅니다.")
    base_path = _safe_repo_path(repo_root, base["config"])
    if sha256_file(base_path) != base["config_sha256"]:
        raise Phase5ReadinessV11Error("기존 readiness config hash가 다릅니다.")
    base_config = _load_json(base_path, "base readiness config")
    validate_base_contract(base_config, repo_root)

    evaluation = config.get("evaluation_split")
    if not isinstance(evaluation, dict):
        raise Phase5ReadinessV11Error("평가 split 부모 계약이 없습니다.")
    if (
        evaluation.get("version") != "v1.0.0"
        or evaluation.get("config")
        != "configs/data_versions/saju_1b_baseline/evaluation-split-v1.0.0.json"
        or BUILD_ID_PATTERN.fullmatch(str(evaluation.get("build_id", ""))) is None
        or evaluation.get("status")
        != "sealed_blind_ready_for_post_training_evaluation"
        or evaluation.get("canonical_training_fingerprint_changed") is not False
        or evaluation.get("phase4_smoke_reexecution_required") is not False
        or evaluation.get("phase5_training_performed") is not False
    ):
        raise Phase5ReadinessV11Error("평가 split 부모 상태가 다릅니다.")
    for key in (
        "config_sha256",
        "build_sha256",
        "private_manifest_sha256",
        "public_manifest_sha256",
        "split_summary_sha256",
        "external_conformance_report_sha256",
    ):
        _exact_sha(evaluation.get(key), f"evaluation_split.{key}")
    evaluation_path = _safe_repo_path(repo_root, evaluation["config"])
    if sha256_file(evaluation_path) != evaluation["config_sha256"]:
        raise Phase5ReadinessV11Error("평가 split config hash가 다릅니다.")

    roles = config.get("evaluation_roles")
    if roles != {
        "dev_monitor": {
            "rows": 70,
            "training_loop_access": True,
            "checkpoint_selection_allowed": False,
            "final_claim_allowed": False,
        },
        "dev_diagnostic": {
            "rows": 930,
            "training_loop_access": False,
            "final_claim_allowed": False,
        },
        "blind_source_test": {
            "components": 350,
            "rows": 500,
            "training_loop_access": False,
            "sealed": True,
            "aggregation": "component_then_axis_macro",
        },
        "external_conformance": {
            "rows": 220,
            "training_loop_access": False,
            "score_separately": True,
        },
    }:
        raise Phase5ReadinessV11Error("Phase 5 평가 역할 계약이 다릅니다.")

    final_gate = config.get("final_evaluation_gate")
    if final_gate != {
        "required_frozen_checkpoints": [
            "K0-INSTRUCT",
            "KI10-MIX-v2",
            "KI20-MIX-v2",
        ],
        "blind_evaluation_runs": 1,
        "blind_spent_after_output_inspection": True,
        "post_inspection_change_requires_new_split_version": True,
    }:
        raise Phase5ReadinessV11Error("최종 blind 평가 Gate가 다릅니다.")

    outputs = config.get("outputs")
    if outputs != {
        "private_root": "data/derived/saju_1b_baseline/phase5-readiness/v1.1.0/{build_id}",
        "public_root": "data/reports/saju_1b_baseline/phase5-readiness/v1.1.0/{build_id}",
    }:
        raise Phase5ReadinessV11Error("readiness v1.1 출력 경로가 다릅니다.")
    for value in outputs.values():
        _safe_repo_path(repo_root, value.format(build_id="build-000000000000"))

    governance = config.get("governance")
    if governance != {
        "training_promotion_allowed": True,
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }:
        raise Phase5ReadinessV11Error("readiness v1.1 거버넌스 계약이 다릅니다.")
    files = config.get("implementation_files")
    if files != [
        "scripts/training/phase5_readiness.py",
        "scripts/training/phase5_readiness_v1_1.py",
        "scripts/evaluation/external_conformance.py",
        "scripts/evaluation/phase5_split.py",
    ]:
        raise Phase5ReadinessV11Error("readiness v1.1 구현 fingerprint가 다릅니다.")
    return {
        "status": "valid",
        "readiness_version": "v1.1.0",
        "evaluation_split_build_id": evaluation["build_id"],
        "phase5_training_performed": False,
    }


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = _load_json(config_path, "Phase 5 readiness v1.1 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes: dict[str, str] = {}
    for relative in [*config["implementation_files"], relative_config]:
        path = _safe_repo_path(repo_root, relative)
        if not path.is_file():
            raise Phase5ReadinessV11Error(
                f"readiness 구현 fingerprint 파일이 없습니다: {relative}"
            )
        implementation_hashes[relative] = sha256_file(path)
    build_inputs = {
        "canonical_plan_version": config["canonical_plan_version"],
        "readiness_version": config["readiness_version"],
        "base_readiness": config["base_readiness"],
        "evaluation_split": config["evaluation_split"],
        "evaluation_roles_sha256": _sha256_json(config["evaluation_roles"]),
        "final_evaluation_gate_sha256": _sha256_json(
            config["final_evaluation_gate"]
        ),
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = _sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    return {
        "build_id": build_id,
        "build_sha256": build_sha256,
        "build_inputs": build_inputs,
        "config": config,
        "config_path": config_path,
        "private_root": _safe_repo_path(
            repo_root, config["outputs"]["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_repo_path(
            repo_root, config["outputs"]["public_root"].format(build_id=build_id)
        ),
    }


def _evaluation_context(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    contract = context["config"]["evaluation_split"]
    value = prepare_evaluation_context(
        repo_root, _safe_repo_path(repo_root, contract["config"])
    )
    if (
        value["build_id"] != contract["build_id"]
        or value["build_sha256"] != contract["build_sha256"]
    ):
        raise Phase5ReadinessV11Error("평가 split fingerprint가 다릅니다.")
    return value


def _build_payloads(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    config = context["config"]
    evaluation_contract = config["evaluation_split"]
    evaluation_context = _evaluation_context(context, repo_root)
    try:
        verified = verify_split(evaluation_context, repo_root)
    except EvaluationSplitError as exc:
        raise Phase5ReadinessV11Error("봉인 평가 split 재검증이 실패했습니다.") from exc
    if (
        verified.get("build_id") != evaluation_contract["build_id"]
        or verified.get("blind_components") != 350
        or verified.get("blind_rows") != 500
        or verified.get("canonical_training_fingerprint_changed") is not False
    ):
        raise Phase5ReadinessV11Error("봉인 평가 split 검증값이 다릅니다.")
    evaluation_private = evaluation_context["private_root"]
    evaluation_public = evaluation_context["public_root"]
    if (
        sha256_file(evaluation_private / "build_manifest.json")
        != evaluation_contract["private_manifest_sha256"]
        or sha256_file(evaluation_public / "build_manifest.json")
        != evaluation_contract["public_manifest_sha256"]
        or sha256_file(evaluation_public / "split_summary.json")
        != evaluation_contract["split_summary_sha256"]
        or sha256_file(evaluation_public / "external_conformance_report.json")
        != evaluation_contract["external_conformance_report_sha256"]
    ):
        raise Phase5ReadinessV11Error("평가 split public/private hash가 다릅니다.")

    base_context = prepare_base_context(
        repo_root,
        _safe_repo_path(repo_root, config["base_readiness"]["config"]),
    )
    try:
        base_private, base_public = build_base_payloads(base_context, repo_root)
    except Phase5ReadinessError as exc:
        raise Phase5ReadinessV11Error("기존 Phase 5 readiness 재검증이 실패했습니다.") from exc
    monitor = (evaluation_private / "eval/dev_monitor_70.jsonl").read_bytes()
    if monitor != base_private["eval/phase5_eval70.jsonl"]:
        raise Phase5ReadinessV11Error("기존 eval70과 dev_monitor byte가 다릅니다.")

    private_values: dict[str, bytes] = {"eval/dev_monitor_70.jsonl": monitor}
    for key in ("ki10", "ki20"):
        run_input = json.loads(base_private[f"run_inputs/{key}.json"])
        run_input["schema_version"] = "1.1.0"
        run_input["evaluation"] = {
            "split_role": "dev_monitor",
            "relative_path": "eval/dev_monitor_70.jsonl",
            "rows": 70,
            "sha256": hashlib.sha256(monitor).hexdigest(),
            "checkpoint_selection_allowed": False,
            "final_claim_allowed": False,
            "evaluation_split_build_id": evaluation_contract["build_id"],
            "evaluation_split_build_sha256": evaluation_contract["build_sha256"],
        }
        run_input["development_diagnostic"] = {
            "rows": 930,
            "training_loop_access": False,
            "evaluation_split_build_id": evaluation_contract["build_id"],
        }
        run_input["blind_source_test"] = {
            "components": 350,
            "rows": 500,
            "sealed": True,
            "training_loop_access": False,
            "aggregation": "component_then_axis_macro",
            "evaluation_split_build_id": evaluation_contract["build_id"],
        }
        run_input["external_conformance"] = {
            "rows": 220,
            "training_loop_access": False,
            "score_separately": True,
            "evaluation_split_build_id": evaluation_contract["build_id"],
        }
        private_values[f"run_inputs/{key}.json"] = _json_payload(run_input)

    base_summary = json.loads(base_public["readiness_summary.json"])
    base_summary.update(
        {
            "schema_version": "1.1.0",
            "readiness_version": "v1.1.0",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "status": "ready_for_explicit_phase5_execution_with_sealed_blind",
            "evaluation_split": evaluation_contract,
            "evaluation_roles": config["evaluation_roles"],
            "final_evaluation_gate": config["final_evaluation_gate"],
            "canonical_training_fingerprint_changed": False,
            "phase4_smoke_reexecution_required": False,
            "blind_raw_or_ids_in_report": False,
            "phase5_training_performed": False,
        }
    )
    base_summary["evaluation"] = {
        "split_role": "dev_monitor",
        "rows": 70,
        "sha256": hashlib.sha256(monitor).hexdigest(),
        "train_component_overlap": 0,
        "checkpoint_selection_allowed": False,
        "final_claim_allowed": False,
        "contains_restricted_text": True,
        "stored_in_git_ignored_private_root": True,
    }
    public_values = {"readiness_summary.json": _json_payload(base_summary)}
    return dict(sorted(private_values.items())), public_values


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
            raise Phase5ReadinessV11Error(
                f"기존 readiness 불변 파일을 덮어쓸 수 없습니다: {path}"
            )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifest_payload(
    context: dict[str, Any], root: Path, artifacts: dict[str, bytes], *, public: bool
) -> bytes:
    return _json_payload(
        {
            "schema_version": "1.1.0",
            "report_type": (
                "phase5_readiness_v1_1_public_manifest"
                if public
                else "phase5_readiness_v1_1_private_manifest"
            ),
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": {
                relative: sha256_file(root / relative)
                for relative in sorted(artifacts)
            },
            "status": "ready_for_explicit_phase5_execution_with_sealed_blind",
            "training_promotion_allowed": True,
            "canonical_training_fingerprint_changed": False,
            "phase4_smoke_reexecution_required": False,
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "phase5_training_performed": False,
        }
    )


def build_readiness(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if private_root.exists() or public_root.exists():
        if not private_root.exists() or not public_root.exists():
            raise Phase5ReadinessV11Error(
                "readiness v1.1 private/public 중 한쪽만 있습니다."
            )
        return {**verify_readiness(context, repo_root), "mode": "reused"}
    if not _git_clean(repo_root):
        raise Phase5ReadinessV11Error(
            "readiness v1.1 생성 전 working tree가 깨끗해야 합니다."
        )
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
        for path in [private_temp, *[p for p in private_temp.rglob("*") if p.is_dir()]]:
            path.chmod(PRIVATE_DIR_MODE)
        _atomic_bytes(
            private_temp / "build_manifest.json",
            _manifest_payload(context, private_temp, private_values, public=False),
            mode=PRIVATE_FILE_MODE,
        )
        for relative, payload in public_values.items():
            _atomic_bytes(public_temp / relative, payload, mode=PUBLIC_FILE_MODE)
        _atomic_bytes(
            public_temp / "build_manifest.json",
            _manifest_payload(context, public_temp, public_values, public=True),
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


def _verify_artifacts(
    root: Path, manifest: dict[str, Any], expected: dict[str, bytes], label: str
) -> None:
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(expected):
        raise Phase5ReadinessV11Error(f"{label} artifact 목록이 다릅니다.")
    for relative, payload in expected.items():
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != hashes[relative]
            or path.read_bytes() != payload
        ):
            raise Phase5ReadinessV11Error(
                f"{label} artifact가 재현되지 않습니다: {relative}"
            )


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
        raise Phase5ReadinessV11Error("readiness v1.1 경로·권한이 다릅니다.")
    private = _load_json(private_root / "build_manifest.json", "private readiness v1.1")
    public = _load_json(public_root / "build_manifest.json", "public readiness v1.1")
    for manifest in (private, public):
        if (
            manifest.get("build_id") != context["build_id"]
            or manifest.get("build_sha256") != context["build_sha256"]
            or manifest.get("build_inputs") != context["build_inputs"]
            or manifest.get("status")
            != "ready_for_explicit_phase5_execution_with_sealed_blind"
            or manifest.get("canonical_training_fingerprint_changed") is not False
            or manifest.get("phase4_smoke_reexecution_required") is not False
            or manifest.get("phase5_training_performed") is not False
        ):
            raise Phase5ReadinessV11Error("readiness v1.1 manifest identity가 다릅니다.")
    expected_private, expected_public = _build_payloads(context, repo_root)
    _verify_artifacts(private_root, private, expected_private, "private readiness v1.1")
    _verify_artifacts(public_root, public, expected_public, "public readiness v1.1")
    for path in private_root.rglob("*"):
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase5ReadinessV11Error(
                f"private readiness 파일 권한이 다릅니다: {path}"
            )
    for path in public_root.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE
        ):
            raise Phase5ReadinessV11Error(
                f"public readiness 파일 형식·권한이 다릅니다: {path}"
            )
    return {
        "status": "verified_ready_with_sealed_blind",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "evaluation_split_build_id": context["config"]["evaluation_split"]["build_id"],
        "manifest_rows": {"ki10": 10_000, "ki20": 20_000},
        "dev_monitor_rows": 70,
        "blind_rows": 500,
        "canonical_training_fingerprint_changed": False,
        "phase4_smoke_reexecution_required": False,
        "phase5_training_performed": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="봉인 평가 split을 포함한 Phase 5 readiness v1.1 Gate"
    )
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
    config_path = args.config
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                _load_json(config_path, "Phase 5 readiness v1.1 config"), REPO_ROOT
            )
        elif args.command == "plan":
            context = prepare_context(REPO_ROOT, config_path)
            result = {
                "status": "planned",
                "build_id": context["build_id"],
                "build_sha256": context["build_sha256"],
                "private_root": context["private_root"].relative_to(REPO_ROOT).as_posix(),
                "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
                "phase5_training_performed": False,
                "writes_performed": False,
            }
        elif args.command == "prepare":
            context = prepare_context(REPO_ROOT, config_path)
            result = (
                build_readiness(context, REPO_ROOT)
                if args.execute
                else {
                    "status": "dry_run",
                    "build_id": context["build_id"],
                    "phase5_training_performed": False,
                    "writes_performed": False,
                }
            )
        else:
            context = prepare_context(REPO_ROOT, config_path)
            result = verify_readiness(context, REPO_ROOT)
    except (Phase5ReadinessV11Error, Phase5ReadinessError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
