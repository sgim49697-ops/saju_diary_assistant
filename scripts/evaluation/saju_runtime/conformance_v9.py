# conformance_v9.py - 과거 공식 완전 일자의 chart-only release Gate를 전수 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Iterable, Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.conformance_v8 import (
    RuntimeConformanceV8Error,
)
from scripts.evaluation.saju_runtime.conformance_v8 import (
    run_conformance as run_conformance_v8,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_4 import (
    APPROVED_END,
    APPROVED_SCOPE_V14,
    APPROVED_START,
    CONFORMANCE_V9_IMPLEMENTATIONS,
    ENGINE_VERSION_V14,
    GATE_V17_PATH,
    OUTPUT_SCHEMA_VERSION_V14,
    REGISTRY_V14_PATH,
    REPORT_V17_ROOT,
    SOURCE_REGISTRY_V17_PATH,
    SUITE_VERSION_V9,
    derive_gate_checks_v1_4,
    validate_contract_registry_v1_4,
)
from scripts.runtime.calculation.engine_v1_3 import SajuRuntimeEngineV13
from scripts.runtime.calculation.engine_v1_4 import (
    ApprovedSajuRuntimeEngineV14,
    boundary_uncertainty_hits,
    is_approved_solar_date,
    uncertain_result_is_stable,
    validate_chart_only_candidate,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.normalize import normalize_tool_birth_input
from scripts.runtime.calculation.skyfield_solar_terms import (
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_term_types import PAST_OFFICIAL_CORROBORATED
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH

SCHEMA_VERSION = "1.7.0"
TEST_SIGNER_KEY = bytes(range(32))
ZONE = ZoneInfo("Asia/Seoul")
SAFE_TIMES = ("12:00", "06:00", "18:00", "00:00", "03:00", "21:00")


class RuntimeConformanceV9Error(RuntimeError):
    """chart-only v9 입력·실행·산출물 계약 위반."""


def _arguments(
    *,
    birth_date: str,
    birth_time: str | None = "12:00",
    precision: str = "exact",
    time_range: dict[str, str] | None = None,
    calendar: str = "solar",
    leap_month: bool | None = None,
) -> dict[str, Any]:
    return {
        "birth_date": birth_date,
        "calendar": calendar,
        "leap_month": leap_month,
        "birth_time": birth_time if precision == "exact" else None,
        "time_precision": precision,
        "time_range": time_range if precision == "range" else None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _lunar_arguments(row: dict[str, Any], *, birth_time: str) -> dict[str, Any]:
    lunar = row["lunar_date"]
    return _arguments(
        birth_date=(
            f"{int(lunar['year']):04d}-{int(lunar['month']):02d}-{int(lunar['day']):02d}"
        ),
        birth_time=birth_time,
        calendar="lunar",
        leap_month=bool(row["leap_month"]),
    )


def _read_lunar_rows(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConformanceV9Error("KASI 음양력 snapshot이 없거나 symlink입니다.")
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for number, line in enumerate(stream, start=1):
                value = json.loads(line)
                if (
                    not isinstance(value, dict)
                    or not isinstance(value.get("solar_date"), str)
                    or not isinstance(value.get("lunar_date"), dict)
                    or type(value.get("leap_month")) is not bool
                ):
                    raise RuntimeConformanceV9Error(
                        f"KASI 음양력 snapshot {number}행 형식이 다릅니다."
                    )
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceV9Error(
            "KASI 음양력 snapshot을 읽지 못했습니다."
        ) from exc
    if len(rows) != 54_787:
        raise RuntimeConformanceV9Error("KASI 음양력 snapshot 행 수가 다릅니다.")
    return rows


def _scope_matrix(rows: list[dict[str, Any]]) -> dict[str, int]:
    allowed = 0
    blocked = 0
    failures = 0
    expected_date = date(1900, 1, 1)
    for row in rows:
        try:
            solar_date = date.fromisoformat(str(row["solar_date"]))
        except ValueError:
            failures += 6
            continue
        if solar_date != expected_date:
            failures += 6
        expected_date += timedelta(days=1)
        if is_approved_solar_date(solar_date):
            allowed += 6
        else:
            blocked += 6
    return {
        "cases": len(rows) * 2 * 3,
        "allowed": allowed,
        "blocked": blocked,
        "failures": failures,
    }


def _safe_exact_time(
    solar_date: date,
    calendar: KoreanLunarCalendarProvider,
    provider: SkyfieldSolarTermProvider,
) -> str:
    for label in SAFE_TIMES:
        arguments = _arguments(birth_date=solar_date.isoformat(), birth_time=label)
        normalized = normalize_tool_birth_input(arguments, calendar)
        if not boundary_uncertainty_hits(normalized, provider):
            return label
    raise RuntimeConformanceV9Error(
        f"안전한 exact 검증 시각을 찾지 못했습니다: {solar_date.isoformat()}"
    )


def _clear_candidate_cache(engine: SajuRuntimeEngineV13) -> None:
    engine._chart_to_candidate.clear()
    candidate = getattr(engine, "_candidate", None)
    cache = getattr(candidate, "_chart_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def _validate_exact_result(
    engine: SajuRuntimeEngineV13,
    calendar: KoreanLunarCalendarProvider,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    normalized = normalize_tool_birth_input(arguments, calendar)
    result = engine.calculate_chart(arguments)
    validate_chart_only_candidate(result, normalized=normalized)
    if (
        normalized["birth_time_precision"] != "exact"
        or result.get("chart_id") is None
        or len(result.get("alternative_charts", [])) != 1
    ):
        raise RuntimeConformanceV9Error("exact 원국 후보 결과가 단일 원국이 아닙니다.")
    _clear_candidate_cache(engine)
    return result


def _exact_chart_calculations(
    rows: list[dict[str, Any]],
    *,
    engine: SajuRuntimeEngineV13,
    calendar: KoreanLunarCalendarProvider,
    provider: SkyfieldSolarTermProvider,
) -> tuple[dict[str, int], dict[str, Any] | None]:
    cases = 0
    failures = 0
    sample: dict[str, Any] | None = None
    for row in rows:
        solar_date = date.fromisoformat(row["solar_date"])
        if not is_approved_solar_date(solar_date):
            continue
        label = _safe_exact_time(solar_date, calendar, provider)
        for arguments in (
            _arguments(birth_date=solar_date.isoformat(), birth_time=label),
            _lunar_arguments(row, birth_time=label),
        ):
            cases += 1
            try:
                result = _validate_exact_result(engine, calendar, arguments)
                if sample is None:
                    sample = deepcopy(result)
            except (RuntimeCalculationError, RuntimeConformanceV9Error):
                failures += 1
    return {"cases": cases, "failures": failures}, sample


def _past_boundaries(provider: SkyfieldSolarTermProvider) -> list[Any]:
    values = []
    for year in range(1920, 2027):
        for term_index in sorted(JIE_TO_MONTH):
            boundary = provider.boundary(year, term_index)
            local_date = boundary.instant_utc.astimezone(ZONE).date()
            if (
                boundary.authority_class == PAST_OFFICIAL_CORROBORATED
                and is_approved_solar_date(local_date)
            ):
                values.append(boundary)
    return values


def _boundary_minute_gate(
    *,
    engine: SajuRuntimeEngineV13,
    calendar: KoreanLunarCalendarProvider,
    provider: SkyfieldSolarTermProvider,
    parent_report: dict[str, Any],
) -> tuple[dict[str, int], list[tuple[Any, str]]]:
    boundaries = _past_boundaries(provider)
    probe_cases = 0
    probe_failures = 0
    unclassified = 0
    quarantined: dict[tuple[int, int, str], tuple[Any, str]] = {}
    for boundary in boundaries:
        root = boundary.instant_utc.astimezone(ZONE)
        floor_value = root.replace(second=0, microsecond=0)
        for value in (floor_value, floor_value + timedelta(minutes=1)):
            if not is_approved_solar_date(value.date()):
                continue
            probe_cases += 1
            arguments = _arguments(
                birth_date=value.date().isoformat(),
                birth_time=value.strftime("%H:%M"),
            )
            try:
                _validate_exact_result(engine, calendar, arguments)
                normalized = normalize_tool_birth_input(arguments, calendar)
                hits = boundary_uncertainty_hits(normalized, provider)
                for hit in hits:
                    key = (hit["year"], hit["term_index"], hit["local_minute"])
                    quarantined[key] = (boundary, value.strftime("%H:%M"))
            except (RuntimeCalculationError, RuntimeConformanceV9Error):
                probe_failures += 1
    preferred = parent_report["baseline_v7_recalculation"][
        "preferred_provider_evidence"
    ]
    past_mismatches = sum(
        row.get("temporal_class") == "past"
        for row in preferred["official_current_minute_mismatch_rows"]
    )
    if len(boundaries) != 1_279 or len(quarantined) != 50:
        unclassified += 1
    return (
        {
            "past_jie_rows": len(boundaries),
            "probe_cases": probe_cases,
            "probe_failures": probe_failures,
            "past_raw_minute_mismatches": past_mismatches,
            "quarantined_minutes": len(quarantined),
            "unclassified_failures": unclassified,
        },
        [quarantined[key] for key in sorted(quarantined)],
    )


def _month_dates() -> Iterable[date]:
    year, month = 1920, 1
    while (year, month) <= (2026, 8):
        value = date(year, month, 15)
        value = max(value, APPROVED_START)
        value = min(value, APPROVED_END)
        yield value
        month += 1
        if month == 13:
            year += 1
            month = 1


def _uncertain_time_gate(
    *,
    engine: SajuRuntimeEngineV13,
    signer: RuntimeIdSigner,
    calendar: KoreanLunarCalendarProvider,
    provider: SkyfieldSolarTermProvider,
    quarantined: list[tuple[Any, str]],
) -> dict[str, int]:
    cases = 0
    failures = 0
    stable_boundary_unknown = 0
    blocked_boundary_ranges = 0
    for value in _month_dates():
        for arguments in (
            _arguments(
                birth_date=value.isoformat(),
                birth_time=None,
                precision="range",
                time_range={"start": "06:00", "end": "18:00"},
            ),
            _arguments(
                birth_date=value.isoformat(), birth_time=None, precision="unknown"
            ),
        ):
            cases += 1
            try:
                normalized = normalize_tool_birth_input(arguments, calendar)
                result = engine.calculate_chart(arguments)
                validate_chart_only_candidate(result, normalized=normalized)
                if result.get("chart_id") is not None:
                    failures += 1
                _clear_candidate_cache(engine)
            except RuntimeCalculationError:
                failures += 1
    for boundary, label in quarantined:
        local_date = boundary.instant_utc.astimezone(ZONE).date()
        range_arguments = _arguments(
            birth_date=local_date.isoformat(),
            birth_time=None,
            precision="range",
            time_range={"start": label, "end": label},
        )
        unknown_arguments = _arguments(
            birth_date=local_date.isoformat(), birth_time=None, precision="unknown"
        )
        for arguments, expected_stable in (
            (range_arguments, False),
            (unknown_arguments, None),
        ):
            cases += 1
            try:
                normalized = normalize_tool_birth_input(arguments, calendar)
                result = engine.calculate_chart(arguments)
                validate_chart_only_candidate(result, normalized=normalized)
                stable = uncertain_result_is_stable(
                    arguments,
                    base_result=result,
                    signer=signer,
                    calendar_provider=calendar,
                    solar_term_provider=provider,
                )
                if expected_stable is False and stable:
                    failures += 1
                if expected_stable is False and not stable:
                    blocked_boundary_ranges += 1
                if expected_stable is None and stable:
                    stable_boundary_unknown += 1
                _clear_candidate_cache(engine)
            except RuntimeCalculationError:
                failures += 1
    return {
        "cases": cases,
        "failures": failures,
        "blocked_boundary_ranges": blocked_boundary_ranges,
        "stable_boundary_unknown": stable_boundary_unknown,
    }


def _negative_and_governance_gate(sample: dict[str, Any] | None) -> dict[str, Any]:
    date_guard_failures = sum(
        (
            is_approved_solar_date(date(1920, 1, 6)),
            not is_approved_solar_date(date(1920, 1, 7)),
            not is_approved_solar_date(date(2026, 8, 31)),
            is_approved_solar_date(date(2026, 9, 1)),
        )
    )
    dummy = object.__new__(ApprovedSajuRuntimeEngineV14)
    dummy.source_versions = {}
    period = dummy.calculate_period({})
    period_failures = int(
        period.get("status") != "blocked"
        or period.get("code") != "CHART_ONLY_PERIOD_OUT_OF_SCOPE"
    )
    tamper_failures = 0
    if sample is None:
        tamper_failures = 1
    else:
        tampered = deepcopy(sample)
        tampered["hard_facts"]["solar_term_evidence"]["authority_classes"] = [
            "PROFILE_DETERMINISTIC"
        ]
        try:
            validate_chart_only_candidate(
                tampered, normalized=tampered["normalized_input"]
            )
        except RuntimeCalculationError:
            pass
        else:
            tamper_failures = 1
    return {
        "date_guard_failures": date_guard_failures,
        "period_block_failures": period_failures,
        "authority_tamper_failures": tamper_failures,
        "production_application_binding": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }


def _implementation_hashes() -> dict[str, str]:
    values: dict[str, str] = {}
    for relative in sorted(CONFORMANCE_V9_IMPLEMENTATIONS):
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RuntimeConformanceV9Error(f"v9 구현 파일이 없습니다: {relative}")
        values[relative] = sha256_file(path)
    return values


def _parent_identity(directory: Path) -> dict[str, Any]:
    aggregate = directory / "aggregate.json"
    manifest = directory / "build_manifest.json"
    return {
        "build_id": directory.name,
        "aggregate_sha256": sha256_file(aggregate),
        "manifest_sha256": sha256_file(manifest),
        "verified": True,
    }


def run_conformance(
    *,
    lunar_snapshot: Path,
    openapi_solar_term_snapshot: Path,
    official_solar_term_snapshot: Path,
    minute_snapshot: Path,
    almanac_snapshot: Path,
    iers_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_V17_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_4()
    parent_report, parent_directory = run_conformance_v8(
        lunar_snapshot=lunar_snapshot,
        openapi_solar_term_snapshot=openapi_solar_term_snapshot,
        official_solar_term_snapshot=official_solar_term_snapshot,
        minute_snapshot=minute_snapshot,
        almanac_snapshot=almanac_snapshot,
        iers_snapshot=iers_snapshot,
        ephemeris=ephemeris,
        output_base=REPO_ROOT / "data/reports/saju_runtime_conformance/v1.6.0",
    )
    parent_verified = (
        parent_report.get("candidate_runtime_conformance_passed") is True
        and parent_report.get("past_authority_gate_passed") is True
        and parent_report.get("future_authority_separation_gate_passed") is True
        and parent_report.get("strict_runtime_provider_gate_passed") is False
        and parent_report.get("runtime_gate_passed") is False
    )
    if not parent_verified:
        raise RuntimeConformanceV9Error("부모 conformance v8 상태가 다릅니다.")
    rows = _read_lunar_rows(lunar_snapshot)
    signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    calendar = KoreanLunarCalendarProvider()
    provider = SkyfieldSolarTermProvider(ephemeris)
    engine = SajuRuntimeEngineV13(
        signer=signer,
        enable_candidate_runtime=True,
        calendar_provider=calendar,
        solar_term_provider=provider,
    )
    try:
        scope = _scope_matrix(rows)
        exact, sample = _exact_chart_calculations(
            rows,
            engine=engine,
            calendar=calendar,
            provider=provider,
        )
        boundaries, quarantined = _boundary_minute_gate(
            engine=engine,
            calendar=calendar,
            provider=provider,
            parent_report=parent_report,
        )
        uncertain = _uncertain_time_gate(
            engine=engine,
            signer=signer,
            calendar=calendar,
            provider=provider,
            quarantined=quarantined,
        )
    finally:
        engine.close()
        provider.close()
    negative = _negative_and_governance_gate(sample)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION_V9,
        "engine_version": ENGINE_VERSION_V14,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V14,
        "profile_id": POLICY_ID,
        "approval_scope": APPROVED_SCOPE_V14,
        "status": "pending_gate_derivation",
        "parent_v8": _parent_identity(parent_directory),
        "scope_matrix": scope,
        "exact_chart_calculations": exact,
        "boundary_minute_gate": boundaries,
        "uncertain_time_gate": uncertain,
        "negative_and_governance_gate": negative,
        "inputs": {
            "runtime_registry_sha256": sha256_file(REGISTRY_V14_PATH),
            "source_registry_sha256": sha256_file(SOURCE_REGISTRY_V17_PATH),
            "gate_sha256": sha256_file(GATE_V17_PATH),
            "implementation_sha256": _implementation_hashes(),
            "official_snapshots": deepcopy(
                parent_report.get("inputs", {}).get("official_snapshots", {})
            ),
            "private_paths_recorded": False,
            "test_signer": "fixed_nonproduction_key_not_recorded",
        },
        "runtime_feature_flag_default": False,
        "strict_runtime_provider_gate_passed": False,
        "full_runtime_gate_passed": False,
        "production_application_binding": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "sealed_blind_accessed": False,
    }
    checks = derive_gate_checks_v1_4(report)
    passed = all(checks.values())
    report.update(
        {
            "status": (
                "passed_chart_only_release_allowed"
                if passed
                else "failed_chart_only_release_blocked"
            ),
            "gate_checks": checks,
            "chart_only_gate_passed": passed,
            "chart_release_registry_creation_allowed": passed,
            "blocking_reasons": []
            if passed
            else sorted(key for key, value in checks.items() if not value),
        }
    )
    directory = write_report(report, output_base)
    return report, directory


def _safe_output_base(path: Path) -> Path:
    if path in {Path("/"), Path.home()} or path.is_symlink():
        raise RuntimeConformanceV9Error("v9 report 출력 경로가 안전하지 않습니다.")
    resolved = path.resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise RuntimeConformanceV9Error("v9 report 출력 경로가 너무 넓습니다.")
    return resolved


def write_report(report: dict[str, Any], output_base: Path = REPORT_V17_ROOT) -> Path:
    core = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(core).hexdigest()[:12]
    aggregate = {**report, "build_id": build_id}
    aggregate_bytes = canonical_json_bytes(aggregate) + b"\n"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v9",
        "artifacts": {
            "aggregate.json": {
                "bytes": len(aggregate_bytes),
                "sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
            }
        },
        "chart_only_gate_passed": report["chart_only_gate_passed"],
        "chart_release_registry_creation_allowed": report[
            "chart_release_registry_creation_allowed"
        ],
        "strict_runtime_provider_gate_passed": False,
        "full_runtime_gate_passed": False,
        "runtime_feature_flag_default": False,
        "production_application_binding": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "raw_case_output_tracked": False,
        "sealed_blind_accessed": False,
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    directory = _safe_output_base(output_base) / build_id
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        expected_names = {"aggregate.json", "build_manifest.json"}
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or {item.name for item in directory.iterdir()} != expected_names
            or any((directory / name).is_symlink() for name in expected_names)
            or (directory / "aggregate.json").read_bytes() != aggregate_bytes
            or (directory / "build_manifest.json").read_bytes() != manifest_bytes
        ):
            raise RuntimeConformanceV9Error("기존 v9 build ID artifact가 다릅니다.")
        return directory
    directory.mkdir(mode=0o755)
    try:
        for filename, payload in (
            ("aggregate.json", aggregate_bytes),
            ("build_manifest.json", manifest_bytes),
        ):
            descriptor = os.open(
                directory / filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o644,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    except Exception:
        for filename in ("aggregate.json", "build_manifest.json"):
            target = directory / filename
            if target.is_file() and not target.is_symlink():
                target.unlink()
        directory.rmdir()
        raise
    return directory


def verify_report(report_root: Path) -> dict[str, Any]:
    from scripts.runtime.calculation.contracts_v1_4 import (  # local import avoids cycle
        _validate_report_identity_v1_4,
    )

    identity = {
        "path": str((report_root / "aggregate.json").resolve().relative_to(REPO_ROOT)),
        "sha256": sha256_file(report_root / "aggregate.json"),
        "manifest_path": str(
            (report_root / "build_manifest.json").resolve().relative_to(REPO_ROOT)
        ),
        "manifest_sha256": sha256_file(report_root / "build_manifest.json"),
        "build_id": report_root.name,
    }
    report, _manifest = _validate_report_identity_v1_4(identity)
    return {
        "status": "verified_chart_only_release_allowed",
        "build_id": report["build_id"],
        "chart_only_gate_passed": True,
        "chart_release_registry_creation_allowed": True,
        "strict_runtime_provider_gate_passed": False,
        "full_runtime_gate_passed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="한국 만세력 chart-only conformance v9"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--kasi-lunar-snapshot", type=Path, required=True)
    run.add_argument("--kasi-solar-term-snapshot", type=Path, required=True)
    run.add_argument("--kasi-official-solar-term-snapshot", type=Path, required=True)
    run.add_argument("--kasi-minute-snapshot", type=Path, required=True)
    run.add_argument("--kasi-almanac-1964-snapshot", type=Path, required=True)
    run.add_argument("--iers-snapshot", type=Path, required=True)
    run.add_argument("--ephemeris", type=Path, required=True)
    run.add_argument("--output-base", type=Path, default=REPORT_V17_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            result = {
                "status": "valid",
                "registry": validate_contract_registry_v1_4()["registry_id"],
            }
        elif args.command == "plan":
            validate_contract_registry_v1_4()
            result = {
                "status": "planned",
                "scope_matrix_cases": 328_722,
                "exact_chart_cases": 77_908,
                "past_jie_rows": 1_279,
                "expected_quarantined_minutes": 50,
                "evaluation_mode": "automatic_gate",
            }
        elif args.command == "verify":
            result = verify_report(args.report_root)
        else:
            report, directory = run_conformance(
                lunar_snapshot=args.kasi_lunar_snapshot,
                openapi_solar_term_snapshot=args.kasi_solar_term_snapshot,
                official_solar_term_snapshot=args.kasi_official_solar_term_snapshot,
                minute_snapshot=args.kasi_minute_snapshot,
                almanac_snapshot=args.kasi_almanac_1964_snapshot,
                iers_snapshot=args.iers_snapshot,
                ephemeris=args.ephemeris,
                output_base=args.output_base,
            )
            result = {
                "status": report["status"],
                "build": str(directory),
                "chart_only_gate_passed": report["chart_only_gate_passed"],
                "chart_release_registry_creation_allowed": report[
                    "chart_release_registry_creation_allowed"
                ],
                "strict_runtime_provider_gate_passed": False,
                "full_runtime_gate_passed": False,
            }
    except (
        OSError,
        RuntimeCalculationError,
        RuntimeConformanceV8Error,
        RuntimeConformanceV9Error,
        ValueError,
    ) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
