# conformance_v10.py - KASI 공식 라벨과 v1.5 단일 일진을 전수 대조한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.conformance_v3 import _load_lunar_snapshot
from scripts.evaluation.saju_runtime.conformance_v6 import _load_official_terms
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_4 import RELEASE_V14_PATH
from scripts.runtime.calculation.contracts_v1_5 import (
    APPROVED_SCOPE_V15,
    CONFORMANCE_V10_IMPLEMENTATIONS,
    ENGINE_VERSION_V15,
    GATE_V18_PATH,
    OUTPUT_SCHEMA_VERSION_V15,
    REGISTRY_V15_PATH,
    REPORT_V18_ROOT,
    SINGLE_DAY_CASES,
    SINGLE_DAY_END,
    SINGLE_DAY_END_DATE,
    SINGLE_DAY_START,
    SINGLE_DAY_START_DATE,
    SOURCE_REGISTRY_V18_PATH,
    SUITE_VERSION_V10,
    derive_gate_checks_v1_5,
    validate_contract_registry_v1_5,
    validate_release_registry_v1_4,
)
from scripts.runtime.calculation.engine_v1_5 import (
    ApprovedSajuRuntimeEngineV15,
    effective_single_day_start,
)
from scripts.runtime.calculation.facts import month_pillar, year_pillar
from scripts.runtime.calculation.facts_v1_3 import period_point_facts_v1_3
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    DE440S_SHA256,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

SCHEMA_VERSION = "1.8.0"
KST = ZoneInfo("Asia/Seoul")
TEST_SIGNER_KEY = bytes.fromhex(
    "7d66bba0d228bb7bdf4f4d3bc2d7f72253baec179e4bdcf047c78970d21a880f"
)
DEFAULT_LUNAR = (
    REPO_ROOT
    / "data/raw/saju_runtime/kasi/v1.1.0/lunisolar/kasi_lunisolar.jsonl"
)
DEFAULT_OFFICIAL_TERMS = (
    REPO_ROOT
    / "data/raw/saju_runtime/kasi/v1.3.0/official-solar-terms/kasi_official_solar_terms.jsonl"
)
DEFAULT_EPHEMERIS = (
    REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
)


class RuntimeConformanceV10Error(RuntimeError):
    """conformance v10 입력·전수 대조·공개 산출물 계약 위반."""


def _dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _implementation_hashes() -> dict[str, str]:
    values: dict[str, str] = {}
    for relative in sorted(CONFORMANCE_V10_IMPLEMENTATIONS):
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeConformanceV10Error(f"v10 구현 파일이 없습니다: {relative}")
        values[relative] = sha256_file(path)
    return values


def _parent_identity() -> dict[str, Any]:
    release = validate_release_registry_v1_4(RELEASE_V14_PATH)
    report = release["conformance_report"]
    return {
        "verified": True,
        "release_id": release["release_id"],
        "release_path": str(RELEASE_V14_PATH.relative_to(REPO_ROOT)),
        "release_sha256": release["release_registry_sha256"],
        "report_build_id": report["build_id"],
        "report_sha256": report["sha256"],
        "manifest_sha256": report["manifest_sha256"],
    }


def _official_resolution(
    value: date,
    *,
    day_rows: dict[date, dict[str, Any]],
    jie_rows: list[tuple[datetime, dict[str, Any]]],
    lichun: dict[int, datetime],
) -> tuple[dict[str, str], bool]:
    instant = datetime.combine(value, time(12, 0), tzinfo=KST)
    day = day_rows.get(value)
    if day is None:
        raise RuntimeConformanceV10Error(
            f"KASI 음양력 snapshot에 단일 일진 날짜가 없습니다: {value}"
        )
    boundary = lichun.get(value.year)
    if boundary is None:
        raise RuntimeConformanceV10Error(f"KASI 입춘 행이 없습니다: {value.year}")
    saju_year = value.year if instant >= boundary else value.year - 1
    candidates = [item for item in jie_rows if item[0] <= instant]
    if not candidates:
        raise RuntimeConformanceV10Error(f"직전 KASI 절입을 찾지 못했습니다: {value}")
    previous_instant, previous = candidates[-1]
    del previous_instant
    month_number = JIE_TO_MONTH.get(previous["term_index"])
    if month_number is None:
        raise RuntimeConformanceV10Error("KASI 직전 행이 절입이 아닙니다.")
    quarantine = any(
        abs((official_instant - instant).total_seconds()) <= 30
        for official_instant, _row in jie_rows
    )
    try:
        day_ganzhi = day["cn"][2]
    except (IndexError, KeyError, TypeError) as exc:
        raise RuntimeConformanceV10Error(
            f"KASI 일진 라벨 형식이 다릅니다: {value}"
        ) from exc
    if not isinstance(day_ganzhi, str) or not day_ganzhi:
        raise RuntimeConformanceV10Error(f"KASI 일진 라벨이 비었습니다: {value}")
    return (
        {
            "year_ganzhi": year_pillar(saju_year)["ganzhi"],
            "month_ganzhi": month_pillar(saju_year, month_number)["ganzhi"],
            "day_ganzhi": day_ganzhi,
        },
        quarantine,
    )


def _single_day_matrix(
    *,
    lunar_rows: list[dict[str, Any]],
    official_rows: list[dict[str, Any]],
    provider: SkyfieldSolarTermProvider,
) -> tuple[dict[str, Any], dict[date, dict[str, str]]]:
    day_rows: dict[date, dict[str, Any]] = {}
    for row in lunar_rows:
        try:
            solar = row["solar"]
            value = date(int(solar[0]), int(solar[1]), int(solar[2]))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV10Error("KASI 음양력 날짜가 잘못됐습니다.") from exc
        if value in day_rows:
            raise RuntimeConformanceV10Error("KASI 음양력 날짜가 중복됐습니다.")
        day_rows[value] = row
    raw_jie = [row for row in official_rows if row.get("term_index") in JIE_TO_MONTH]
    if len(raw_jie) != 2_172:
        raise RuntimeConformanceV10Error("KASI 공식 절입 행 수가 다릅니다.")
    jie_rows: list[tuple[datetime, dict[str, Any]]] = []
    lichun: dict[int, datetime] = {}
    for row in raw_jie:
        try:
            instant = datetime.fromisoformat(row["reference_local_minute"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceV10Error("KASI 공식 절입 시각이 잘못됐습니다.") from exc
        if instant.tzinfo is None or instant.utcoffset() != timedelta(hours=9):
            raise RuntimeConformanceV10Error("KASI 공식 절입 timezone이 다릅니다.")
        jie_rows.append((instant, row))
        if row["term_index"] == 2:
            if row["year"] in lichun:
                raise RuntimeConformanceV10Error("KASI 입춘 행이 중복됐습니다.")
            lichun[row["year"]] = instant
    jie_rows.sort(key=lambda item: item[0])

    mismatches = 0
    quarantined = 0
    official_by_date: dict[date, dict[str, str]] = {}
    values = _dates(SINGLE_DAY_START, SINGLE_DAY_END)
    if len(values) != SINGLE_DAY_CASES:
        raise RuntimeConformanceV10Error("단일 일진 계약 날짜 수가 다릅니다.")
    for value in values:
        official, quarantine = _official_resolution(
            value,
            day_rows=day_rows,
            jie_rows=jie_rows,
            lichun=lichun,
        )
        official_by_date[value] = official
        quarantined += int(quarantine)
        point = period_point_facts_v1_3(
            value,
            datetime.combine(value, time(12, 0), tzinfo=KST),
            solar_term_provider=provider,
        )
        provider_labels = {
            key: point[key]
            for key in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        }
        mismatches += int(provider_labels != official)
    return (
        {
            "cases": len(values),
            "date_range": [SINGLE_DAY_START_DATE, SINGLE_DAY_END_DATE],
            "official_day_rows": len(official_by_date),
            "official_jie_rows": len(raw_jie),
            "evaluation_timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
            "official_minute_uncertainty_seconds": 30,
            "provider_id": provider.provider_id,
            "provider_label_mismatches": mismatches,
            "noon_boundary_quarantine_dates": quarantined,
            "official_day_oracle": "kasi_lunisolar_openapi",
            "official_year_month_oracle": "kasi_official_solar_terms_download",
            "provider_values_written_to_official_snapshot": False,
            "raw_rows_in_report": False,
        },
        official_by_date,
    )


def _conformance_engine(
    provider: SkyfieldSolarTermProvider,
    *,
    enabled: bool = True,
) -> ApprovedSajuRuntimeEngineV15:
    engine = object.__new__(ApprovedSajuRuntimeEngineV15)
    engine.enable_approved_runtime = enabled
    engine.release = {
        "release_id": "saju-runtime-release-v1.5.0-000000000000"
    }
    engine._chart_engine = object()
    engine._provider = provider
    engine._signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    engine._today_provider = lambda: SINGLE_DAY_START
    engine._exact_chart_ids = {"sc2_" + "a" * 64}
    engine.source_versions = {
        "runtime_contract": "saju-runtime-contract-v1.5.0",
        "test_context": "conformance_nonproduction",
    }
    return engine


def _runtime_contract_gate(
    provider: SkyfieldSolarTermProvider,
    official_by_date: dict[date, dict[str, str]],
) -> dict[str, Any]:
    chart_id = "sc2_" + "a" * 64
    engine = _conformance_engine(provider)
    positives = [SINGLE_DAY_START, date(2035, 6, 1), SINGLE_DAY_END]
    positive_failures = 0
    for index, value in enumerate(positives):
        arguments = {
            "chart_id": chart_id,
            "period_type": "day",
            "start_date": value.isoformat(),
            "end_date": None if index == 0 else value.isoformat(),
            "timezone": "Asia/Seoul",
        }
        result = engine.calculate_period(arguments)
        facts = result.get("hard_facts", {}).get("period", {})
        labels = {
            key: facts.get(key)
            for key in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        }
        positive_failures += int(
            result.get("status") != "ok"
            or result.get("fact_authority") != "HARD_GT"
            or labels != official_by_date[value]
            or facts.get("evaluation_local_time") != "12:00"
            or result.get("hard_facts", {})
            .get("day_assignment_evidence", {})
            .get("future_physical_instant_claimed")
            is not False
        )

    base = {
        "chart_id": chart_id,
        "period_type": "day",
        "start_date": SINGLE_DAY_START_DATE,
        "end_date": SINGLE_DAY_START_DATE,
        "timezone": "Asia/Seoul",
    }
    negatives: list[tuple[ApprovedSajuRuntimeEngineV15, dict[str, Any], str]] = []
    for period_type in ("week", "month", "year"):
        negatives.append(
            (engine, {**base, "period_type": period_type}, "SINGLE_DAY_PERIOD_TYPE_REQUIRED")
        )
    negatives.extend(
        [
            (engine, {**base, "timezone": "UTC"}, "UNSUPPORTED_REGION"),
            (engine, {**base, "chart_id": "sc2_" + "b" * 64}, "EXACT_CHART_NOT_IN_PROCESS"),
            (engine, {**base, "end_date": "2026-09-03"}, "SINGLE_DAY_RANGE_REQUIRED"),
            (engine, {**base, "start_date": "2026-09-01", "end_date": "2026-09-01"}, "SINGLE_DAY_OUT_OF_APPROVED_RANGE"),
            (engine, {**base, "start_date": "2050-01-01", "end_date": "2050-01-01"}, "SINGLE_DAY_OUT_OF_APPROVED_RANGE"),
            (engine, {key: value for key, value in base.items() if key != "timezone"}, "INVALID_TOOL_ARGUMENTS"),
            (engine, {**base, "start_date": "2026-09-03", "end_date": "2026-09-02"}, "INVALID_TOOL_ARGUMENTS"),
            (engine, {**base, "start_date": "not-a-date"}, "INVALID_TOOL_ARGUMENTS"),
            (_conformance_engine(provider, enabled=False), base, "RUNTIME_FEATURE_DISABLED"),
        ]
    )
    negative_failures = sum(
        candidate.calculate_period(arguments).get("code") != expected
        for candidate, arguments, expected in negatives
    )
    return {
        "positive_cases": len(positives),
        "positive_failures": positive_failures,
        "negative_cases": len(negatives),
        "negative_failures": negative_failures,
        "only_exact_current_process_chart": True,
        "server_kst_today_floor_enforced": (
            effective_single_day_start(SINGLE_DAY_START) == SINGLE_DAY_START
            and effective_single_day_start(date(2030, 1, 1)) == date(2030, 1, 1)
        ),
        "free_text_date_parser_used": False,
        "supported_period_types": ["day"],
        "blocked_period_types": ["week", "month", "year"],
    }


def run_conformance(
    *,
    lunar_snapshot: Path,
    official_solar_term_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_V18_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_5()
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    official_rows, official_identity = _load_official_terms(
        official_solar_term_snapshot
    )
    if ephemeris.is_symlink() or not ephemeris.is_file():
        raise RuntimeConformanceV10Error("고정 DE440s ephemeris가 없습니다.")
    if ephemeris.stat().st_size != 32_726_016 or sha256_file(ephemeris) != DE440S_SHA256:
        raise RuntimeConformanceV10Error("고정 DE440s identity가 다릅니다.")
    provider = SkyfieldSolarTermProvider(ephemeris)
    try:
        matrix, official_by_date = _single_day_matrix(
            lunar_rows=lunar_rows,
            official_rows=official_rows,
            provider=provider,
        )
        runtime_gate = _runtime_contract_gate(provider, official_by_date)
        provider_identity = provider.identity()
    finally:
        provider.close()
    parent = _parent_identity()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION_V10,
        "engine_version": ENGINE_VERSION_V15,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
        "profile_id": POLICY_ID,
        "approval_scope": APPROVED_SCOPE_V15,
        "status": "pending_gate_derivation",
        "parent_v9": parent,
        "single_day_official_matrix": matrix,
        "runtime_contract_gate": runtime_gate,
        "inputs": {
            "runtime_registry_sha256": sha256_file(REGISTRY_V15_PATH),
            "source_registry_sha256": sha256_file(SOURCE_REGISTRY_V18_PATH),
            "gate_sha256": sha256_file(GATE_V18_PATH),
            "implementation_sha256": _implementation_hashes(),
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_official_current_solar_terms": official_identity,
            },
            "ephemeris": {
                **provider_identity,
                "bytes": ephemeris.stat().st_size,
                "sha256": sha256_file(ephemeris),
                "private_path_recorded": False,
            },
            "parent_v1_4_release": deepcopy(parent),
            "private_paths_recorded": False,
            "test_signer": "fixed_nonproduction_key_not_recorded",
        },
        "governance": {
            "runtime_feature_flag_default": False,
            "strict_runtime_provider_gate_passed": False,
            "full_runtime_gate_passed": False,
            "production_application_binding": False,
            "mix20k_v3_1_regeneration_allowed": False,
            "training_promotion_allowed": False,
            "sealed_blind_accessed": False,
        },
        "runtime_feature_flag_default": False,
        "strict_runtime_provider_gate_passed": False,
        "full_runtime_gate_passed": False,
        "production_application_binding": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }
    checks = derive_gate_checks_v1_5(report)
    passed = all(checks.values())
    report.update(
        {
            "status": (
                "passed_chart_and_single_day_release_allowed"
                if passed
                else "failed_chart_and_single_day_release_blocked"
            ),
            "gate_checks": checks,
            "chart_and_single_day_gate_passed": passed,
            "release_registry_creation_allowed": passed,
            "blocking_reasons": []
            if passed
            else sorted(key for key, value in checks.items() if not value),
        }
    )
    directory = write_report(report, output_base)
    return report, directory


def _safe_output_base(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if resolved != REPORT_V18_ROOT.resolve(strict=False) or path.is_symlink():
        raise RuntimeConformanceV10Error("v10 공개 report 경로는 고정돼 있습니다.")
    return resolved


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def write_report(report: dict[str, Any], output_base: Path = REPORT_V18_ROOT) -> Path:
    base = _safe_output_base(output_base)
    core = deepcopy(report)
    core.pop("build_id", None)
    build_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    final_report = {**core, "build_id": build_id}
    aggregate_payload = (
        json.dumps(final_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    manifest = {
        "schema_version": "1.0.0",
        "suite_version": SUITE_VERSION_V10,
        "build_id": build_id,
        "artifacts": {
            "aggregate.json": {
                "bytes": len(aggregate_payload),
                "sha256": hashlib.sha256(aggregate_payload).hexdigest(),
            }
        },
        "private_content_included": False,
        "raw_case_output_tracked": False,
        "private_paths_recorded": False,
        "sealed_blind_accessed": False,
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    directory = base / build_id
    if directory.exists() or directory.is_symlink():
        raise RuntimeConformanceV10Error("기존 v10 build를 덮어쓰지 않습니다.")
    base.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o755)
    try:
        _write_exclusive(directory / "aggregate.json", aggregate_payload)
        _write_exclusive(directory / "build_manifest.json", manifest_payload)
    except Exception:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
        raise
    report.clear()
    report.update(final_report)
    return directory


def verify_report(report_root: Path = REPORT_V18_ROOT) -> dict[str, Any]:
    root = report_root.resolve()
    if root.parent == REPORT_V18_ROOT.resolve() and root.name.startswith("build-"):
        directories = [root]
    elif root == REPORT_V18_ROOT.resolve():
        directories = sorted(
            path for path in root.glob("build-*") if path.is_dir() and not path.is_symlink()
        )
    else:
        raise RuntimeConformanceV10Error("v10 verify 경로가 고정 report root 밖입니다.")
    if len(directories) != 1:
        raise RuntimeConformanceV10Error("검증할 v10 build는 정확히 하나여야 합니다.")
    directory = directories[0]
    aggregate = directory / "aggregate.json"
    manifest_path = directory / "build_manifest.json"
    if (
        aggregate.is_symlink()
        or manifest_path.is_symlink()
        or not aggregate.is_file()
        or not manifest_path.is_file()
        or {path.name for path in directory.iterdir()}
        != {"aggregate.json", "build_manifest.json"}
    ):
        raise RuntimeConformanceV10Error("v10 공개 artifact 집합이 다릅니다.")
    report = json.loads(aggregate.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    core = dict(report)
    build_id = core.pop("build_id", None)
    expected = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    artifact = manifest.get("artifacts", {}).get("aggregate.json", {})
    if (
        build_id != expected
        or directory.name != expected
        or manifest.get("build_id") != expected
        or artifact.get("sha256") != sha256_file(aggregate)
        or artifact.get("bytes") != aggregate.stat().st_size
        or manifest.get("private_content_included") is not False
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or not all(derive_gate_checks_v1_5(report).values())
        or report.get("chart_and_single_day_gate_passed") is not True
    ):
        raise RuntimeConformanceV10Error("v10 공개 report 검증에 실패했습니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "chart_and_single_day_gate_passed": True,
        "single_day_cases": report["single_day_official_matrix"]["cases"],
        "provider_label_mismatches": report["single_day_official_matrix"][
            "provider_label_mismatches"
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="saju runtime conformance v10")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--kasi-lunar-snapshot", type=Path, default=DEFAULT_LUNAR)
    run.add_argument(
        "--kasi-official-solar-term-snapshot",
        type=Path,
        default=DEFAULT_OFFICIAL_TERMS,
    )
    run.add_argument("--ephemeris", type=Path, default=DEFAULT_EPHEMERIS)
    run.add_argument("--output-base", type=Path, default=REPORT_V18_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, default=REPORT_V18_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            result = {
                "status": "verified",
                "registry": validate_contract_registry_v1_5()["registry_id"],
            }
        elif args.command == "plan":
            validate_contract_registry_v1_5()
            result = {
                "status": "planned",
                "suite_version": SUITE_VERSION_V10,
                "single_day_date_range": [SINGLE_DAY_START_DATE, SINGLE_DAY_END_DATE],
                "single_day_cases": SINGLE_DAY_CASES,
                "evaluation_local_time": "12:00",
                "provider_label_mismatches_required": 0,
                "noon_boundary_quarantine_dates_required": 0,
                "runtime_feature_flag_default": False,
                "private_paths_recorded": False,
            }
        elif args.command == "run":
            report, directory = run_conformance(
                lunar_snapshot=args.kasi_lunar_snapshot,
                official_solar_term_snapshot=args.kasi_official_solar_term_snapshot,
                ephemeris=args.ephemeris,
                output_base=args.output_base,
            )
            result = {
                "status": report["status"],
                "build_id": report["build_id"],
                "output": str(directory),
                "provider_label_mismatches": report["single_day_official_matrix"][
                    "provider_label_mismatches"
                ],
            }
        else:
            result = verify_report(args.report_root)
    except Exception as exc:  # CLI boundary: 내부 오류 상세를 공개하지 않는다.
        if not isinstance(exc, (RuntimeConformanceV10Error, RuntimeError, OSError)):
            raise
        print(
            json.dumps(
                {"status": "error", "message": str(exc)},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
