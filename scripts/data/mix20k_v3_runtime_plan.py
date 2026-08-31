# mix20k_v3_runtime_plan.py - v3.0.1을 읽기 전용 분석해 runtime-grounded v3.1 이관 계약을 만든다.

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.evaluation.external_conformance import sha256_file
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import (
    CONFIG_ROOT,
    ENGINE_VERSION,
    POLICY_ID,
    REPO_ROOT,
)
from scripts.runtime.calculation.contracts import (
    sha256_file as runtime_sha256_file,
)

EXPECTED_BUILD_ID = "build-94eb7b543490"
EXPECTED_BUILD_SHA256 = (
    "94eb7b5434907539d7041fc81846169dc2e80f332e99b53d710722dcd5564454"
)
TRAINING_RELATIVE = "training/training_mix20k_v3.0.1_candidate.jsonl"
SELECTION_SEED = "mix20k-v3.1-runtime-grounded|KR_CIVIL_MIDNIGHT_V1|20260831"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_migration/v1.0.0"
CONFORMANCE_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.0.0"


class RuntimeMigrationPlanError(RuntimeError):
    """private v3 build나 이관 Gate가 고정 계약과 다를 때 발생한다."""


def _load_build(build: Path) -> tuple[dict[str, Any], Path]:
    root = build.resolve()
    if build.is_symlink() or not build.is_dir():
        raise RuntimeMigrationPlanError("v3 private build가 없거나 symlink입니다.")
    manifest_path = root / "build_manifest.json"
    training_path = root / TRAINING_RELATIVE
    if manifest_path.is_symlink() or training_path.is_symlink():
        raise RuntimeMigrationPlanError(
            "v3 manifest 또는 training 파일은 symlink일 수 없습니다."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeMigrationPlanError("v3 build manifest를 읽지 못했습니다.") from exc
    if (
        manifest.get("build_id") != EXPECTED_BUILD_ID
        or manifest.get("build_sha256") != EXPECTED_BUILD_SHA256
        or manifest.get("dataset_version") != "v3.0.1-repaired"
        or manifest.get("rows", {}).get("training_candidate_projection") != 20_000
        or manifest.get("governance", {}).get("training_promotion_allowed") is not False
        or manifest.get("governance", {}).get("sealed_blind_payload_read_allowed")
        is not False
    ):
        raise RuntimeMigrationPlanError(
            "v3 build identity·governance가 고정 계약과 다릅니다."
        )
    expected_hash = manifest.get("artifact_sha256", {}).get(TRAINING_RELATIVE)
    if (
        not isinstance(expected_hash, str)
        or sha256_file(training_path) != expected_hash
    ):
        raise RuntimeMigrationPlanError(
            "v3 training projection hash가 manifest와 다릅니다."
        )
    return manifest, training_path


def _iter_rows(path: Path):
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise RuntimeMigrationPlanError(
                        f"v3 training에 빈 행이 있습니다: {number}"
                    )
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise RuntimeMigrationPlanError(
                        f"v3 training 행이 object가 아닙니다: {number}"
                    )
                yield number, row
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeMigrationPlanError(
            "v3 training projection을 읽지 못했습니다."
        ) from exc


def _tool_calls(row: dict[str, Any]):
    for message in row.get("messages", []):
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls", []):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            if isinstance(function, dict):
                yield function.get("name"), function.get("arguments")


def _runtime_gate(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "passed": False, "sha256": None}
    try:
        path.resolve().relative_to(CONFORMANCE_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeMigrationPlanError(
            "runtime Gate 보고서가 고정 conformance 경로 밖에 있습니다."
        ) from exc
    if (
        path.name != "aggregate.json"
        or path.parent.is_symlink()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise RuntimeMigrationPlanError("runtime Gate 보고서가 없거나 symlink입니다.")
    manifest_path = path.parent / "build_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeMigrationPlanError("runtime Gate manifest가 없거나 symlink입니다.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeMigrationPlanError(
            "runtime Gate 보고서·manifest를 읽지 못했습니다."
        ) from exc
    report_hash = runtime_sha256_file(path)
    if (
        manifest.get("report_type") != "saju_runtime_conformance_v2"
        or manifest.get("build_id") != path.parent.name
        or manifest.get("aggregate_sha256") != report_hash
        or manifest.get("runtime_gate_passed")
        is not (report.get("runtime_gate_passed") is True)
        or report.get("suite_version") != "saju-runtime-conformance-v2.0.0"
        or report.get("profile_id") != POLICY_ID
        or report.get("engine_version") != ENGINE_VERSION
        or report.get("training_promotion_allowed") is not False
        or report.get("sealed_blind_accessed") is not False
    ):
        raise RuntimeMigrationPlanError("runtime Gate identity·governance가 다릅니다.")
    checks = report.get("gate_checks")
    passed = report.get("runtime_gate_passed") is True
    if (
        not isinstance(checks, dict)
        or not checks
        or any(not isinstance(value, bool) for value in checks.values())
        or passed != all(checks.values())
    ):
        raise RuntimeMigrationPlanError("runtime Gate check 집계가 다릅니다.")
    inputs = report.get("inputs")
    if not isinstance(inputs, dict) or inputs.get(
        "runtime_registry_sha256"
    ) != runtime_sha256_file(CONFIG_ROOT / "registry-v1.0.0.json"):
        raise RuntimeMigrationPlanError("runtime Gate registry hash가 다릅니다.")
    implementations = inputs.get("implementation_sha256")
    if not isinstance(implementations, dict) or not implementations:
        raise RuntimeMigrationPlanError("runtime Gate 구현 hash가 비었습니다.")
    for relative, expected_hash in implementations.items():
        candidate = Path(relative) if isinstance(relative, str) else Path("..")
        unresolved = REPO_ROOT / candidate
        implementation = unresolved.resolve()
        try:
            implementation.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise RuntimeMigrationPlanError(
                "runtime Gate 구현 경로가 저장소를 벗어납니다."
            ) from exc
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or unresolved.is_symlink()
            or not implementation.is_file()
            or not isinstance(expected_hash, str)
            or runtime_sha256_file(implementation) != expected_hash
        ):
            raise RuntimeMigrationPlanError(
                f"runtime Gate 구현 hash가 다릅니다: {relative}"
            )
    return {
        "provided": True,
        "passed": passed,
        "sha256": report_hash,
    }


def analyze(build: Path, *, runtime_gate_report: Path | None = None) -> dict[str, Any]:
    manifest, training_path = _load_build(build)
    rows = 0
    axes: Counter[str] = Counter()
    call_counts: Counter[str] = Counter()
    chart_calendars: Counter[str] = Counter()
    chart_precisions: Counter[str] = Counter()
    chart_years: list[int] = []
    foreign_by_axis: Counter[str] = Counter()
    foreign_ids: list[str] = []
    hard_candidates = 0
    for number, row in _iter_rows(training_path):
        rows += 1
        row_id = row.get("id")
        axis = row.get("task_axis")
        if not isinstance(row_id, str) or not row_id or not isinstance(axis, str):
            raise RuntimeMigrationPlanError(f"v3 row identity가 다릅니다: {number}")
        axes[axis] += 1
        hard_candidates += row.get("fact_authority") == "HARD_CANDIDATE"
        row_foreign = False
        for name, arguments in _tool_calls(row):
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise RuntimeMigrationPlanError(
                    f"tool call schema가 다릅니다: {number}"
                )
            call_counts[name] += 1
            if name != "calculate_saju_chart":
                continue
            birthplace = arguments.get("birthplace", {})
            if not isinstance(birthplace, dict):
                raise RuntimeMigrationPlanError(
                    f"birthplace schema가 다릅니다: {number}"
                )
            row_foreign = (
                birthplace.get("country_code") != "KR"
                or birthplace.get("timezone") != "Asia/Seoul"
            )
            chart_calendars[str(arguments.get("calendar"))] += 1
            chart_precisions[str(arguments.get("time_precision"))] += 1
            try:
                chart_years.append(
                    int(str(arguments.get("birth_date")).split("-", 1)[0])
                )
            except (TypeError, ValueError) as exc:
                raise RuntimeMigrationPlanError(
                    f"chart birth_date가 다릅니다: {number}"
                ) from exc
        if row_foreign:
            foreign_by_axis[axis] += 1
            foreign_ids.append(row_id)
    if rows != 20_000:
        raise RuntimeMigrationPlanError(
            f"v3 training 행 수가 20,000이 아닙니다: {rows}"
        )
    if call_counts != {"calculate_saju_chart": 4350, "calculate_saju_period": 900}:
        raise RuntimeMigrationPlanError(
            f"tool call 수가 고정 계약과 다릅니다: {dict(call_counts)}"
        )
    if foreign_by_axis != {"calendar_time_edge": 50, "chart_tool_call": 150}:
        raise RuntimeMigrationPlanError(
            f"해외 chart 행 수가 고정 계약과 다릅니다: {dict(foreign_by_axis)}"
        )
    ranked = sorted(
        foreign_ids,
        key=lambda value: hashlib.sha256(
            f"{SELECTION_SEED}|{value}".encode()
        ).hexdigest(),
    )
    unsupported = sorted(ranked[:20])
    replaced = sorted(ranked[20:])
    gate = _runtime_gate(runtime_gate_report)
    return {
        "schema_version": "1.0.0",
        "plan_version": "mix20k-v3.1-runtime-migration-v1.0.0",
        "inputs": {
            "implementation": {
                "path": "scripts/data/mix20k_v3_runtime_plan.py",
                "sha256": runtime_sha256_file(
                    REPO_ROOT / "scripts/data/mix20k_v3_runtime_plan.py"
                ),
            },
            "runtime_registry_sha256": runtime_sha256_file(
                CONFIG_ROOT / "registry-v1.0.0.json"
            ),
        },
        "status": "blocked_runtime_gate_pending"
        if not gate["passed"]
        else "runtime_gate_passed_ready_to_regenerate",
        "source_build": {
            "build_id": manifest["build_id"],
            "build_sha256": manifest["build_sha256"],
            "training_projection_sha256": manifest["artifact_sha256"][
                TRAINING_RELATIVE
            ],
            "rows": rows,
        },
        "observed": {
            "task_axis_counts": dict(sorted(axes.items())),
            "tool_call_counts": dict(sorted(call_counts.items())),
            "chart_calendar_counts": dict(sorted(chart_calendars.items())),
            "chart_time_precision_counts": dict(sorted(chart_precisions.items())),
            "chart_year_min": min(chart_years),
            "chart_year_max": max(chart_years),
            "foreign_chart_rows": len(foreign_ids),
            "foreign_by_axis": dict(sorted(foreign_by_axis.items())),
            "hard_candidate_rows": hard_candidates,
        },
        "migration_contract": {
            "target_version": "mix20k-v3.1-runtime-grounded",
            "preserve_total_rows": 20_000,
            "regenerate_chart_tool_calls": 4350,
            "regenerate_period_tool_calls": 900,
            "foreign_replace_with_kr": len(replaced),
            "foreign_convert_to_unsupported_region": len(unsupported),
            "replacement_selection_fingerprint": hashlib.sha256(
                canonical_json_bytes({"replace": replaced, "unsupported": unsupported})
            ).hexdigest(),
            "preserve_conversation_and_scenario_ids": True,
            "new_build_fingerprint_required": True,
            "new_split_leakage_preflight_required": True,
            "mutate_source_build": False,
            "training_execution_allowed": False,
        },
        "runtime_gate": gate,
        "regeneration_allowed": gate["passed"],
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_rows_in_report": False,
    }


def write_report(report: dict[str, Any], output: Path) -> Path:
    resolved = output.resolve()
    try:
        resolved.relative_to(REPORT_ROOT.resolve())
    except ValueError as exc:
        raise RuntimeMigrationPlanError(
            f"보고서는 {REPORT_ROOT} 아래에만 쓸 수 있습니다."
        ) from exc
    if output.is_symlink():
        raise RuntimeMigrationPlanError("보고서 경로는 symlink일 수 없습니다.")
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output.exists():
        if not output.is_file():
            raise RuntimeMigrationPlanError("기존 migration 보고서가 파일이 아닙니다.")
        try:
            existing = output.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeMigrationPlanError(
                "기존 migration 보고서를 읽지 못했습니다."
            ) from exc
        if existing != payload:
            raise RuntimeMigrationPlanError("기존 migration 보고서 내용이 다릅니다.")
        return output
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except OSError as exc:
        raise RuntimeMigrationPlanError(
            "migration 보고서를 배타적으로 기록하지 못했습니다."
        ) from exc
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX20K-v3.1 runtime 이관 읽기 전용 분석"
    )
    parser.add_argument("command", choices=["analyze"])
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--runtime-gate-report", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = analyze(args.build, runtime_gate_report=args.runtime_gate_report)
        output = write_report(report, args.output) if args.output else None
    except RuntimeMigrationPlanError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "regeneration_allowed": report["regeneration_allowed"],
                "output": None if output is None else str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
