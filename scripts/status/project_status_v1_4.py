# project_status_v1_4.py - 현재 제한 Runtime·dashboard 운영선을 반영한 v1.4 현황을 발행한다.

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.preflight.phase4_common import load_json, sha256_file, sha256_json
from scripts.status.project_status import (
    BUILD_PATTERN,
    ProjectStatusError,
    _safe_path,
    build_status,
    verify_status,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/project-status-v1.4.0.json"
)


def validate_contract(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    """v1.4 상태 문서의 현재 사실과 권한 분리를 검증한다."""
    if (
        config.get("schema_version") != "1.4.0"
        or config.get("status_version") != "v1.4.0"
        or config.get("canonical_plan_version") != "4.0.5"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("as_of") != "2026-09-02"
        or config.get("stage") != "phase6_completed_automatic_repair_required"
    ):
        raise ProjectStatusError("project status v1.4 identity가 다릅니다.")

    components = config.get("components")
    if not isinstance(components, list) or len(components) < 16:
        raise ProjectStatusError("project status v1.4 component가 부족합니다.")
    names: set[str] = set()
    for value in components:
        if (
            not isinstance(value, Mapping)
            or not isinstance(value.get("name"), str)
            or value["name"] in names
            or not isinstance(value.get("version"), str)
            or BUILD_PATTERN.fullmatch(str(value.get("build_id", ""))) is None
            or not isinstance(value.get("status"), str)
            or not isinstance(value.get("sha256"), str)
            or len(value["sha256"]) != 64
        ):
            raise ProjectStatusError("project status v1.4 component 형식이 다릅니다.")
        names.add(value["name"])
    required_components = {
        "Phase 6 자동 기술평가",
        "원국·단일 일진 Runtime",
        "Dashboard v1.11",
        "Grounded dialogue 재채점",
        "Grounded dialogue 장문 진단",
    }
    if not required_components <= names:
        raise ProjectStatusError("project status v1.4 필수 component가 없습니다.")

    phases = config.get("phases")
    if (
        not isinstance(phases, list)
        or [value.get("phase") for value in phases] != list(range(7))
        or any(
            value.get("status") not in {"완료", "진행 중", "미시작", "차단"}
            for value in phases
        )
    ):
        raise ProjectStatusError("project status v1.4 Phase 타임라인이 다릅니다.")
    axes = config.get("dataset_axes")
    if not isinstance(axes, list) or sum(
        int(value.get("rows", 0)) for value in axes
    ) != 20_000:
        raise ProjectStatusError("project status v1.4 데이터 축 수량이 다릅니다.")
    if (
        abs(
            sum(float(value.get("assistant_token_share_percent", 0)) for value in axes)
            - 100.0
        )
        > 0.001
    ):
        raise ProjectStatusError("project status v1.4 token 비율 합계가 다릅니다.")

    decision = config.get("decision")
    expected_decision_keys = {
        "stage_label",
        "stage_status",
        "signal",
        "signal_tone",
        "headline",
        "headline_accent",
        "summary",
        "ki10_baseline",
        "ki20_promotion",
        "domain_semantics",
        "sealed_blind",
        "phase4_rerun",
        "runtime_release",
        "strict_full_runtime",
        "dashboard_binding",
        "mix20k_v3_training",
    }
    if (
        not isinstance(decision, Mapping)
        or set(decision) != expected_decision_keys
        or any(not isinstance(decision[key], str) or not decision[key] for key in decision)
        or decision["signal_tone"] not in {"ok", "stop", "wait"}
        or decision["domain_semantics"] != "not_measured"
        or decision["ki20_promotion"] != "차단"
        or decision["runtime_release"] != "허용"
        or decision["strict_full_runtime"] != "차단"
        or decision["dashboard_binding"] != "허용"
        or decision["mix20k_v3_training"] != "금지"
    ):
        raise ProjectStatusError("project status v1.4 결정 형식이 다릅니다.")

    for key in (
        "evidence_tiers",
        "gates",
        "known_risks",
        "web_sources",
        "validation_summary",
    ):
        if not isinstance(config.get(key), list) or not config[key]:
            raise ProjectStatusError(f"project status v1.4 {key}가 없습니다.")
    gate_by_name = {
        value.get("name"): value for value in config["gates"] if isinstance(value, Mapping)
    }
    expected_gate_status = {
        "원국·단일 일진 제한 release": "통과",
        "Dashboard v1.11 제한 연결": "통과",
        "strict/full Runtime": "차단",
        "MIX20K-v3.1·추가 학습": "금지",
    }
    if any(
        gate_by_name.get(name, {}).get("status") != status
        for name, status in expected_gate_status.items()
    ):
        raise ProjectStatusError("project status v1.4 권한 Gate가 다릅니다.")

    outputs = config.get("outputs")
    if outputs != {
        "root_html": "PROJECT_STATUS.html",
        "snapshot_root": (
            "data/reports/saju_1b_baseline/project-status/v1.4.0/{build_id}"
        ),
    }:
        raise ProjectStatusError("project status v1.4 출력 경로가 다릅니다.")
    _safe_path(repo_root, outputs["root_html"])
    _safe_path(
        repo_root, outputs["snapshot_root"].format(build_id="build-000000000000")
    )
    if config.get("implementation_files") != [
        "scripts/status/project_status.py",
        "scripts/status/project_status_v1_4.py",
    ]:
        raise ProjectStatusError("project status v1.4 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "status_version": "v1.4.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "project status v1.4 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "status_version": config["status_version"],
        "as_of": config["as_of"],
        "stage": config["stage"],
        "decision_sha256": sha256_json(config["decision"]),
        "components_sha256": sha256_json(config["components"]),
        "phases_sha256": sha256_json(config["phases"]),
        "dataset_axes_sha256": sha256_json(config["dataset_axes"]),
        "evidence_sha256": sha256_json(config["evidence_tiers"]),
        "gates_sha256": sha256_json(config["gates"]),
        "risks_sha256": sha256_json(config["known_risks"]),
        "web_sources_sha256": sha256_json(config["web_sources"]),
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
        "root_html": _safe_path(repo_root, config["outputs"]["root_html"]),
        "snapshot_root": _safe_path(
            repo_root,
            config["outputs"]["snapshot_root"].format(build_id=build_id),
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="사주 일기 도우미 v1.4 현황 HTML")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    render = commands.add_parser("render")
    render.add_argument("--execute", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--require-registry", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                load_json(config_path, "project status v1.4 config"), REPO_ROOT
            )
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "build_id": context["build_id"],
                    "build_sha256": context["build_sha256"],
                    "snapshot_root": context["snapshot_root"]
                    .relative_to(REPO_ROOT)
                    .as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "render":
                result = (
                    build_status(context)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "build_id": context["build_id"],
                        "writes_performed": False,
                    }
                )
            else:
                result = verify_status(
                    context, require_registry=args.require_registry
                )
    except (ProjectStatusError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
