# project_audit.py - sealed blind를 열지 않고 현재 프로젝트 정본과 산출물을 통합 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/project-audit-v1.0.0.json"
)
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_PATTERN = re.compile(r"^(?:build|eval|intake)-[0-9a-f]{12}$")
EXPECTED_ARTIFACTS = (
    "aggregate.json",
    "build_manifest.json",
    "runtime_provider_records.jsonl",
)


class ProjectAuditError(RuntimeError):
    """통합 정본 검증 계약 위반."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectAuditError(f"JSON duplicate key를 허용하지 않습니다: {key}")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectAuditError(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise ProjectAuditError(f"{label} 최상위는 JSON object여야 합니다.")
    return value


def _safe_path(repo_root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ProjectAuditError(f"{label} 경로가 안전하지 않습니다.")
    current = repo_root
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise ProjectAuditError(f"{label} 경로에 symlink가 있습니다.")
    resolved = (repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ProjectAuditError(f"{label} 경로가 저장소를 벗어납니다.") from exc
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_config(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = _load_json(config_path, "project audit config")
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("audit_id") != "saju-project-audit-v1.0.0"
        or set(config)
        != {
            "_description",
            "schema_version",
            "audit_id",
            "phase6",
            "grounded_dialogue",
            "mix20k_v3",
            "project_status",
            "runtime",
            "governance",
        }
        or config.get("governance")
        != {
            "sealed_blind_payload_open_allowed": False,
            "gpu_execution_allowed": False,
            "training_execution_allowed": False,
            "tracked_writes_allowed": False,
        }
    ):
        raise ProjectAuditError("project audit identity·governance가 다릅니다.")
    phase6 = config.get("phase6")
    grounded = config.get("grounded_dialogue")
    mix = config.get("mix20k_v3")
    status = config.get("project_status")
    runtime = config.get("runtime")
    if (
        not isinstance(phase6, Mapping)
        or set(phase6) != {"config", "registry", "evaluation_build_id", "public_root"}
        or phase6.get("evaluation_build_id") != "eval-e8630962cab2"
        or not isinstance(grounded, Mapping)
        or set(grounded) != {"base_config", "followup_config", "postscore_config"}
        or not isinstance(mix, Mapping)
        or set(mix) != {"private_build", "public_build"}
        or not isinstance(status, Mapping)
        or set(status) != {"config", "build_id"}
        or status.get("build_id") != "build-38b9ca77ce45"
        or not isinstance(runtime, Mapping)
        or set(runtime)
        != {
            "engine_version",
            "conformance_build_id",
            "tracked_report",
            "snapshots",
            "ephemeris_sha256",
        }
        or runtime.get("engine_version") != "1.3"
        or runtime.get("conformance_build_id") != "build-8bd88d6db03a"
        or FULL_SHA_PATTERN.fullmatch(str(runtime.get("ephemeris_sha256", "")))
        is None
    ):
        raise ProjectAuditError("project audit component 계약이 다릅니다.")
    paths: list[tuple[str, str]] = [
        (phase6["config"], "Phase 6 config"),
        (phase6["registry"], "Phase 6 registry"),
        (phase6["public_root"], "Phase 6 public root"),
        *[(value, f"grounded dialogue {key}") for key, value in grounded.items()],
        (mix["private_build"], "MIX20K-v3 private build"),
        (mix["public_build"], "MIX20K-v3 public build"),
        (status["config"], "project status config"),
        (runtime["tracked_report"], "runtime tracked report"),
    ]
    snapshots = runtime.get("snapshots")
    if not isinstance(snapshots, Mapping) or set(snapshots) != {
        "kasi_lunar",
        "kasi_solar_term",
        "kasi_official_solar_term",
        "kasi_minute",
        "kasi_almanac_1964",
        "iers",
    }:
        raise ProjectAuditError("runtime snapshot 계약이 다릅니다.")
    paths.extend((value, f"runtime snapshot {key}") for key, value in snapshots.items())
    for relative, label in paths:
        if not isinstance(relative, str):
            raise ProjectAuditError(f"{label} 경로 형식이 다릅니다.")
        _safe_path(repo_root, relative, label)
    return config


def _run_json(repo_root: Path, arguments: Sequence[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1:] or ["unknown error"]
        raise ProjectAuditError(f"{label} 검증에 실패했습니다: {detail[0]}")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ProjectAuditError(f"{label} 출력이 JSON이 아닙니다.") from exc
    if not isinstance(value, dict):
        raise ProjectAuditError(f"{label} 출력 계약이 다릅니다.")
    return value


def _verify_phase6_without_payload(
    repo_root: Path, config: Mapping[str, Any]
) -> dict[str, Any]:
    contract = _load_json(
        _safe_path(repo_root, config["config"], "Phase 6 config"),
        "Phase 6 config",
    )
    registry = _load_json(
        _safe_path(repo_root, config["registry"], "Phase 6 registry"),
        "Phase 6 registry",
    )
    approved = registry.get("approved_phase6_technical_evaluation")
    if not isinstance(approved, Mapping):
        raise ProjectAuditError("Phase 6 승인 registry 항목이 없습니다.")
    build_id = config["evaluation_build_id"]
    public_root = _safe_path(repo_root, config["public_root"], "Phase 6 public root")
    aggregate_path = public_root / "aggregate.json"
    manifest_path = public_root / "build_manifest.json"
    aggregate = _load_json(aggregate_path, "Phase 6 aggregate")
    manifest = _load_json(manifest_path, "Phase 6 public manifest")
    if (
        approved.get("build_id") != build_id
        or approved.get("blind_status") != "spent_completed"
        or approved.get("blind_consumption_runs") != 1
        or approved.get("decision_inputs")
        != "repository_local_automatic_metrics_only"
        or approved.get("baseline_decision") != "AUTOMATED_REPAIR_REQUIRED"
        or approved.get("release_approved") is not False
        or approved.get("application_binding_performed") is not False
        or approved.get("mix20k_v3_1_generated") is not False
        or approved.get("additional_training_performed") is not False
        or _sha256_file(aggregate_path) != approved.get("aggregate_sha256")
        or _sha256_file(manifest_path) != approved.get("build_manifest_sha256")
        or aggregate.get("evaluation_build_id") != build_id
        or aggregate.get("phase6_completed") is not True
        or aggregate.get("blind_usage", {}).get("status") != "spent_completed"
        or aggregate.get("policy", {}).get("domain_semantics") != "not_measured"
        or any(aggregate.get("promotion", {}).values())
        or manifest.get("evaluation_build_id") != build_id
        or contract.get("blind_source", {}).get("maximum_evaluation_runs") != 1
    ):
        raise ProjectAuditError("Phase 6 공개·registry 완료 계약이 다릅니다.")
    return {
        "status": "verified_without_payload_open",
        "evaluation_build_id": build_id,
        "baseline_decision": "AUTOMATED_REPAIR_REQUIRED",
        "sealed_blind_payload_opened": False,
    }


def _verify_runtime_reproduction(
    repo_root: Path, runtime: Mapping[str, Any], ephemeris: Path
) -> dict[str, Any]:
    if not ephemeris.is_absolute() or ephemeris.is_symlink() or not ephemeris.is_file():
        raise ProjectAuditError("DE440s는 symlink가 아닌 절대경로 일반 파일이어야 합니다.")
    if _sha256_file(ephemeris) != runtime["ephemeris_sha256"]:
        raise ProjectAuditError("DE440s SHA-256이 audit 계약과 다릅니다.")
    snapshots = runtime["snapshots"]
    with tempfile.TemporaryDirectory(prefix="saju-project-audit-") as directory:
        output_base = Path(directory)
        result = _run_json(
            repo_root,
            [
                "-m",
                "scripts.evaluation.saju_runtime.conformance_v8",
                "run",
                "--kasi-lunar-snapshot",
                str(_safe_path(repo_root, snapshots["kasi_lunar"], "KASI lunar")),
                "--kasi-solar-term-snapshot",
                str(
                    _safe_path(
                        repo_root, snapshots["kasi_solar_term"], "KASI solar term"
                    )
                ),
                "--kasi-official-solar-term-snapshot",
                str(
                    _safe_path(
                        repo_root,
                        snapshots["kasi_official_solar_term"],
                        "KASI official solar term",
                    )
                ),
                "--kasi-minute-snapshot",
                str(_safe_path(repo_root, snapshots["kasi_minute"], "KASI minute")),
                "--kasi-almanac-1964-snapshot",
                str(
                    _safe_path(
                        repo_root,
                        snapshots["kasi_almanac_1964"],
                        "KASI 1964 almanac",
                    )
                ),
                "--iers-snapshot",
                str(_safe_path(repo_root, snapshots["iers"], "IERS snapshot")),
                "--ephemeris",
                str(ephemeris),
                "--output-base",
                str(output_base),
            ],
            "runtime conformance v8",
        )
        if (
            result.get("status")
            != "candidate_runtime_conformance_passed_release_blocked"
            or result.get("candidate_runtime_conformance_passed") is not True
            or result.get("runtime_gate_passed") is not False
            or result.get("strict_runtime_provider_gate_passed") is not False
        ):
            raise ProjectAuditError("runtime v8 재현 상태가 다릅니다.")
        generated = output_base / runtime["conformance_build_id"]
        tracked = _safe_path(repo_root, runtime["tracked_report"], "runtime report")
        artifact_hashes: dict[str, str] = {}
        for filename in EXPECTED_ARTIFACTS:
            generated_path = generated / filename
            tracked_path = tracked / filename
            if (
                not generated_path.is_file()
                or not tracked_path.is_file()
                or _sha256_file(generated_path) != _sha256_file(tracked_path)
            ):
                raise ProjectAuditError(
                    f"runtime v8 재현 artifact가 다릅니다: {filename}"
                )
            artifact_hashes[filename] = _sha256_file(generated_path)
    return {
        "status": "byte_identical",
        "build_id": runtime["conformance_build_id"],
        "artifact_sha256": artifact_hashes,
        "runtime_release_approved": False,
    }


def verify_project(
    repo_root: Path,
    config_path: Path,
    *,
    full: bool,
    ephemeris: Path | None,
) -> dict[str, Any]:
    config = _validated_config(repo_root, config_path)
    if full and ephemeris is None:
        raise ProjectAuditError("full audit에는 --ephemeris 절대경로가 필요합니다.")
    results: dict[str, Any] = {
        "phase6": _verify_phase6_without_payload(repo_root, config["phase6"]),
        "runtime_contract": _run_json(
            repo_root,
            [
                "-m",
                "scripts.runtime.saju_runtime",
                "verify-contract",
                "--engine-version",
                "1.3",
            ],
            "runtime v1.3 contract",
        ),
        "grounded_dialogue": _run_json(
            repo_root,
            [
                "-m",
                "scripts.evaluation.grounded_dialogue",
                "--config",
                config["grounded_dialogue"]["base_config"],
                "verify",
            ],
            "grounded dialogue base",
        ),
        "grounded_dialogue_followup": _run_json(
            repo_root,
            [
                "-m",
                "scripts.evaluation.grounded_dialogue_followup",
                "--config",
                config["grounded_dialogue"]["followup_config"],
                "verify",
            ],
            "grounded dialogue followup",
        ),
        "grounded_dialogue_postscore": _run_json(
            repo_root,
            [
                "-m",
                "scripts.evaluation.grounded_dialogue_postscore",
                "--config",
                config["grounded_dialogue"]["postscore_config"],
                "verify",
            ],
            "grounded dialogue postscore",
        ),
        "mix20k_v3": _run_json(
            repo_root,
            [
                "-m",
                "scripts.data.mix20k_v3_repair",
                "verify",
                "--private-build",
                config["mix20k_v3"]["private_build"],
                "--public-build",
                config["mix20k_v3"]["public_build"],
            ],
            "MIX20K-v3",
        ),
        "project_status": _run_json(
            repo_root,
            [
                "-m",
                "scripts.status.project_status_v1_3",
                "--config",
                config["project_status"]["config"],
                "verify",
                "--require-registry",
            ],
            "project status",
        ),
    }
    if results["project_status"].get("build_id") != config["project_status"]["build_id"]:
        raise ProjectAuditError("project status build ID가 audit 계약과 다릅니다.")
    if full:
        assert ephemeris is not None
        results["phase1_sources"] = _run_json(
            repo_root,
            [str(repo_root / "scripts/data/phase1_sources.py"), "verify"],
            "Phase 1 sources",
        )
        results["runtime_conformance"] = _verify_runtime_reproduction(
            repo_root, config["runtime"], ephemeris
        )
    return {
        "status": "verified",
        "audit_id": config["audit_id"],
        "mode": "full" if full else "quick",
        "sealed_blind_payload_opened": False,
        "gpu_execution_performed": False,
        "training_execution_performed": False,
        "tracked_writes_performed": False,
        "results": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="사주 일기 도우미 통합 정본 검증")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    verify = commands.add_parser("verify")
    verify.add_argument("--full", action="store_true")
    verify.add_argument("--ephemeris", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            config = _validated_config(REPO_ROOT, config_path)
            result = {"status": "valid", "audit_id": config["audit_id"]}
        else:
            result = verify_project(
                REPO_ROOT,
                config_path,
                full=args.full,
                ephemeris=args.ephemeris,
            )
    except (OSError, ValueError, ProjectAuditError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
