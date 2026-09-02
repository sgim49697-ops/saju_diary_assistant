# conformance_v11.py - 1~31일 daily-label Runtime을 공식 8,522일·전 window로 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.evaluation.saju_runtime.conformance_v3 import _load_lunar_snapshot
from scripts.evaluation.saju_runtime.conformance_v6 import _load_official_terms
from scripts.evaluation.saju_runtime.conformance_v10 import (
    DEFAULT_LUNAR,
    DEFAULT_OFFICIAL_TERMS,
    _single_day_matrix,
)
from scripts.evaluation.saju_runtime.conformance_v10 import (
    verify_report as verify_parent_report,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import load_strict_json_object_v1_2
from scripts.runtime.calculation.contracts_v1_5 import (
    RELEASE_V15_PATH,
    REPORT_V18_ROOT,
    SINGLE_DAY_END,
    SINGLE_DAY_END_DATE,
    SINGLE_DAY_START,
    SINGLE_DAY_START_DATE,
    validate_release_registry_v1_5,
)
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.chart_day_adapter import empty_session_state
from scripts.runtime.period_v1.contracts_v1_1 import (
    CONFORMANCE_IMPLEMENTATIONS,
    EXPECTED_WINDOWS,
    GATE_PATH,
    MAXIMUM_DAYS,
    OFFICIAL_DATES,
    REGISTRY_V11_PATH,
    REPORT_ROOT,
    SUITE_VERSION,
    validate_contract_registry_v1_1,
)
from scripts.runtime.period_v1.engine import (
    calculate_authorized_daily_labels,
    public_daily_label_result,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.rehydration import rehydrate_exact_chart
from scripts.runtime.period_v1.resolver import resolve_period_scope
from scripts.runtime.period_v1.security import PeriodIdSigner

SCHEMA_VERSION = "1.0.0"
CANDIDATE_RELEASE_ID = "saju-period-daily-label-candidate-v1.0.0"
DEFAULT_EPHEMERIS = (
    REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
)
TEST_RUNTIME_KEY = bytes.fromhex(
    "946633c457468889d4c2fd96de347093bf3f5954524162c5678db5e93bfd99cd"
)
TEST_PERIOD_KEY = bytes.fromhex(
    "04c658651f278ca104844b0dc60df449840c84c99a25cb1407a5e423c2b31f53"
)


class PeriodConformanceV11Error(RuntimeError):
    """daily-label conformance 입력·집계·공개 산출물 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _implementation_hashes() -> dict[str, str]:
    values: dict[str, str] = {}
    for relative in sorted(CONFORMANCE_IMPLEMENTATIONS):
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise PeriodConformanceV11Error(
                f"conformance v11 구현 파일이 없습니다: {relative}"
            )
        values[relative] = sha256_file(path)
    return values


def _chart_arguments() -> dict[str, Any]:
    return {
        "birth_date": "1990-01-01",
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "12:00",
        "time_precision": "exact",
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _session_state(chart: Mapping[str, Any]) -> dict[str, Any]:
    state = empty_session_state()
    state.update(
        {
            "state_revision": 1,
            "saju_opt_in": True,
            "current_intent": "period",
            "birth_slots": {
                "calendar": "solar",
                "birth_date": "1990-01-01",
                "leap_month": None,
                "time_precision": "exact",
                "birth_time": "12:00",
                "time_range": None,
                "birthplace": {
                    "country_code": "KR",
                    "city": "서울",
                    "timezone": "Asia/Seoul",
                },
            },
            "chart": deepcopy(dict(chart)),
        }
    )
    return state


def _write_test_key(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        written = os.write(descriptor, TEST_RUNTIME_KEY)
        if written != len(TEST_RUNTIME_KEY):
            raise OSError("test key short write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _daily_rows(
    *,
    engine: ApprovedSajuRuntimeEngineV15,
    runtime_signer: RuntimeIdSigner,
    authorization: Mapping[str, Any],
) -> tuple[list[dict[str, str]], int]:
    period_signer = PeriodIdSigner.for_test(TEST_PERIOD_KEY)
    rows: list[dict[str, str]] = []
    requests = 0
    current = SINGLE_DAY_START
    while current <= SINGLE_DAY_END:
        end = min(current + timedelta(days=MAXIMUM_DAYS - 1), SINGLE_DAY_END)
        scope = resolve_period_scope(
            {
                "type": "request_period",
                "request": {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": "explicit",
                    "start_date": current.isoformat(),
                    "end_date": end.isoformat(),
                },
            },
            reference_date=SINGLE_DAY_START,
        )
        internal = calculate_authorized_daily_labels(
            parent_engine=engine,
            runtime_signer=runtime_signer,
            period_signer=period_signer,
            authorization=authorization,
            resolved_scope=scope,
            authority_release_id=CANDIDATE_RELEASE_ID,
        )
        public = public_daily_label_result(internal)
        rows.extend(deepcopy(public["days"]))
        requests += 1
        current = end + timedelta(days=1)
    return rows, requests


def _daily_matrix(
    rows: list[dict[str, str]], official_by_date: Mapping[date, Mapping[str, str]]
) -> dict[str, Any]:
    values = [SINGLE_DAY_START + timedelta(days=index) for index in range(OFFICIAL_DATES)]
    order_mismatches = 0
    label_mismatches = 0
    authority_mismatches = 0
    for expected_date, row in zip(values, rows, strict=False):
        order_mismatches += int(row.get("date") != expected_date.isoformat())
        labels = {
            key: row.get(key)
            for key in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        }
        label_mismatches += int(labels != official_by_date.get(expected_date))
        authority_mismatches += int(row.get("authority") != "SOURCE_HARD_FACT")
    return {
        "cases": len(rows),
        "date_range": [SINGLE_DAY_START_DATE, SINGLE_DAY_END_DATE],
        "expected_cases": OFFICIAL_DATES,
        "order_mismatches": order_mismatches + abs(len(rows) - len(values)),
        "label_mismatches": label_mismatches + abs(len(rows) - len(values)),
        "authority_mismatches": authority_mismatches + abs(len(rows) - len(values)),
        "duplicate_dates": len(rows) - len({row.get("date") for row in rows}),
        "provider_values_written_to_official_snapshot": False,
        "raw_rows_in_report": False,
    }


def _window_matrix(
    rows: list[dict[str, str]], official_by_date: Mapping[date, Mapping[str, str]]
) -> dict[str, Any]:
    windows = 0
    order_mismatches = 0
    duplicate_or_missing = 0
    label_mismatches = 0
    authority_mismatches = 0
    for length in range(1, MAXIMUM_DAYS + 1):
        for start_index in range(len(rows) - length + 1):
            window = rows[start_index : start_index + length]
            windows += 1
            dates = [item.get("date") for item in window]
            duplicate_or_missing += int(len(set(dates)) != length)
            for offset, item in enumerate(window):
                expected_date = SINGLE_DAY_START + timedelta(
                    days=start_index + offset
                )
                order_mismatches += int(item.get("date") != expected_date.isoformat())
                labels = {
                    key: item.get(key)
                    for key in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
                }
                label_mismatches += int(labels != official_by_date.get(expected_date))
                authority_mismatches += int(
                    item.get("authority") != "SOURCE_HARD_FACT"
                )
    return {
        "windows": windows,
        "expected_windows": EXPECTED_WINDOWS,
        "window_lengths": [1, MAXIMUM_DAYS],
        "order_mismatches": order_mismatches,
        "duplicate_or_missing_days": duplicate_or_missing,
        "label_mismatches": label_mismatches,
        "authority_mismatches": authority_mismatches,
        "all_windows_materialized": True,
    }


def _resolver_fixtures() -> dict[str, Any]:
    cases: list[tuple[str, bool]] = []

    def resolved(
        expression: str, reference: date, start: str | None = None, end: str | None = None
    ) -> dict[str, Any]:
        return resolve_period_scope(
            {
                "type": "request_period",
                "request": {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": expression,
                    "start_date": start,
                    "end_date": end,
                },
            },
            reference_date=reference,
        )

    month_end = resolved("this_month", date(2030, 1, 31))
    cases.append(("month_end", month_end["day_count"] == 1))
    year_end = resolved(
        "explicit", date(2030, 12, 31), "2030-12-31", "2031-01-01"
    )
    cases.append(("year_end", year_end["day_count"] == 2))
    leap_day = resolved(
        "explicit", date(2028, 2, 28), "2028-02-28", "2028-02-29"
    )
    cases.append(("leap_day", leap_day["end_date"] == "2028-02-29"))
    saturday = resolved("this_weekend", date(2026, 9, 5))
    cases.append(
        ("saturday", saturday["start_date"] == "2026-09-05" and saturday["day_count"] == 2)
    )
    sunday = resolved("this_weekend", date(2026, 9, 6))
    cases.append(
        ("sunday", sunday["start_date"] == "2026-09-06" and sunday["day_count"] == 1)
    )
    floor_passed = False
    try:
        resolved("today", date(2026, 9, 1))
    except PeriodRuntimeError as exc:
        floor_passed = exc.code == "PERIOD_PAST_NOT_ALLOWED"
    cases.append(("dynamic_today_floor", floor_passed))
    return {
        "cases": len(cases),
        "failures": sum(not passed for _name, passed in cases),
        "fixture_names": [name for name, _passed in cases],
    }


def _negative_contract() -> dict[str, Any]:
    failures = 0
    cases = 0

    def expect_error(event: dict[str, Any], reference: date, expected_code: str) -> None:
        nonlocal cases, failures
        cases += 1
        try:
            resolve_period_scope(event, reference_date=reference)
        except PeriodRuntimeError as exc:
            failures += int(exc.code != expected_code)
        else:
            failures += 1

    expect_error(
        {
            "type": "request_period",
            "request": {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-02",
                "end_date": "2026-10-03",
            },
        },
        SINGLE_DAY_START,
        "PERIOD_RANGE_TOO_LONG",
    )
    expect_error(
        {
            "type": "request_period",
            "request": {
                "schema_version": "saju-period-request-v2",
                "date_expression": "explicit",
                "start_date": "2026-09-01",
                "end_date": "2026-09-01",
            },
        },
        SINGLE_DAY_START,
        "PERIOD_PAST_NOT_ALLOWED",
    )
    for field in ("chart_id", "timezone", "reference_date", "release_id"):
        expect_error(
            {
                "type": "request_period",
                "request": {
                    "schema_version": "saju-period-request-v2",
                    "date_expression": "today",
                    "start_date": None,
                    "end_date": None,
                },
                field: "forged",
            },
            SINGLE_DAY_START,
            "PERIOD_REQUEST_FIELDS_INVALID",
        )
    return {
        "cases": cases,
        "failures": failures,
        "free_text_date_parser_used": False,
        "model_generated_runtime_ids_allowed": False,
        "intraday_segments_supported": False,
    }


def derive_gate_checks(report: Mapping[str, Any]) -> dict[str, bool]:
    daily = report.get("daily_label_matrix", {})
    windows = report.get("window_matrix", {})
    fixtures = report.get("resolver_fixtures", {})
    negative = report.get("negative_contract", {})
    governance = report.get("governance", {})
    expected_governance = {
        "feature_flag_default": False,
        "strict_full_runtime_approved": False,
        "future_physical_instant_claimed": False,
        "provider_values_written_to_official_snapshot": False,
        "production_application_binding": False,
        "sealed_blind_accessed": False,
        "mix20k_v3_1_generated": False,
        "training_promotion_allowed": False,
    }
    return {
        "parent_v10_verified": report.get("parent")
        == {
            "verified": True,
            "release_id": "saju-runtime-release-v1.5.0-8b1d6ea2d46e",
            "release_registry_sha256": (
                "db3553529be851a33c3d16c0ec8898de5182c611160d194209ccfd82639f3560"
            ),
            "conformance_build_id": "build-46185262164f",
        },
        "official_dates_complete": daily.get("cases") == OFFICIAL_DATES,
        "official_date_range_exact": daily.get("date_range")
        == [SINGLE_DAY_START_DATE, SINGLE_DAY_END_DATE],
        "engine_request_partition_complete": daily.get("engine_requests") == 275,
        "daily_order_mismatches_zero": daily.get("order_mismatches") == 0,
        "daily_label_mismatches_zero": daily.get("label_mismatches") == 0,
        "daily_authority_mismatches_zero": daily.get("authority_mismatches") == 0,
        "daily_duplicates_zero": daily.get("duplicate_dates") == 0,
        "windows_complete": windows.get("windows") == EXPECTED_WINDOWS,
        "window_lengths_exact": windows.get("window_lengths") == [1, MAXIMUM_DAYS],
        "all_windows_materialized": windows.get("all_windows_materialized") is True,
        "window_order_mismatches_zero": windows.get("order_mismatches") == 0,
        "window_missing_or_duplicate_zero": windows.get("duplicate_or_missing_days") == 0,
        "window_label_mismatches_zero": windows.get("label_mismatches") == 0,
        "window_authority_mismatches_zero": windows.get("authority_mismatches") == 0,
        "resolver_fixtures_complete": fixtures.get("cases") == 6,
        "resolver_fixture_names_exact": fixtures.get("fixture_names")
        == [
            "month_end",
            "year_end",
            "leap_day",
            "saturday",
            "sunday",
            "dynamic_today_floor",
        ],
        "resolver_fixture_failures_zero": fixtures.get("failures") == 0,
        "negative_contract_complete": negative.get("cases") == 6,
        "negative_contract_failures_zero": negative.get("failures") == 0,
        "negative_scope_closed": (
            negative.get("free_text_date_parser_used") is False
            and negative.get("model_generated_runtime_ids_allowed") is False
            and negative.get("intraday_segments_supported") is False
        ),
        "official_snapshot_unmodified": daily.get(
            "provider_values_written_to_official_snapshot"
        )
        is False,
        "governance_closed": governance == expected_governance,
    }


def run_conformance(
    *,
    lunar_snapshot: Path,
    official_solar_term_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry_v1_1()
    lunar_snapshot = (
        lunar_snapshot
        if lunar_snapshot.is_absolute()
        else (REPO_ROOT / lunar_snapshot).resolve()
    )
    official_solar_term_snapshot = (
        official_solar_term_snapshot
        if official_solar_term_snapshot.is_absolute()
        else (REPO_ROOT / official_solar_term_snapshot).resolve()
    )
    ephemeris = (
        ephemeris if ephemeris.is_absolute() else (REPO_ROOT / ephemeris).resolve()
    )
    parent_release = validate_release_registry_v1_5(RELEASE_V15_PATH)
    parent_report = verify_parent_report(
        REPORT_V18_ROOT / parent_release["conformance_report"]["build_id"]
    )
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    official_rows, official_identity = _load_official_terms(official_solar_term_snapshot)
    if (
        ephemeris.is_symlink()
        or not ephemeris.is_file()
        or sha256_file(ephemeris)
        != "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"
    ):
        raise PeriodConformanceV11Error("고정 DE440s identity가 다릅니다.")

    with tempfile.TemporaryDirectory(prefix="saju-period-v11-") as directory:
        key_path = Path(directory) / "runtime-hmac.key"
        _write_test_key(key_path)
        runtime_signer = RuntimeIdSigner.from_key_file(key_path)
        with ApprovedSajuRuntimeEngineV15(
            release_registry=RELEASE_V15_PATH,
            enable_approved_runtime=True,
            ephemeris_path=ephemeris,
            id_key_file=key_path,
            today_provider=lambda: SINGLE_DAY_START,
        ) as engine:
            provider = engine._provider
            if provider is None:
                raise PeriodConformanceV11Error("v1.5 Skyfield provider가 없습니다.")
            parent_matrix, official_by_date = _single_day_matrix(
                lunar_rows=lunar_rows,
                official_rows=official_rows,
                provider=provider,
            )
            chart = engine.calculate_chart(_chart_arguments())
            if chart.get("status") != "ok":
                raise PeriodConformanceV11Error("conformance exact 원국 계산이 실패했습니다.")
            authorization = rehydrate_exact_chart(
                _session_state(chart),
                expected_revision=1,
                engine=engine,
                signer=runtime_signer,
            )
            rows, engine_requests = _daily_rows(
                engine=engine,
                runtime_signer=runtime_signer,
                authorization=authorization,
            )

    daily = _daily_matrix(rows, official_by_date)
    windows = _window_matrix(rows, official_by_date)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION,
        "status": "pending_gate_derivation",
        "parent": {
            "verified": parent_report.get("chart_and_single_day_gate_passed") is True,
            "release_id": parent_release["release_id"],
            "release_registry_sha256": parent_release["release_registry_sha256"],
            "conformance_build_id": parent_report["build_id"],
        },
        "daily_label_matrix": {**daily, "engine_requests": engine_requests},
        "window_matrix": windows,
        "resolver_fixtures": _resolver_fixtures(),
        "negative_contract": _negative_contract(),
        "inputs": {
            "contract_registry_sha256": sha256_file(REGISTRY_V11_PATH),
            "gate_sha256": sha256_file(GATE_PATH),
            "implementation_sha256": _implementation_hashes(),
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_official_current_solar_terms": official_identity,
            },
            "ephemeris": {
                "sha256": sha256_file(ephemeris),
                "bytes": ephemeris.stat().st_size,
                "private_path_recorded": False,
            },
            "parent_v10_matrix": parent_matrix,
            "daily_rows_recorded": False,
            "private_paths_recorded": False,
            "test_key_recorded": False,
        },
        "governance": {
            "feature_flag_default": False,
            "strict_full_runtime_approved": False,
            "future_physical_instant_claimed": False,
            "provider_values_written_to_official_snapshot": False,
            "production_application_binding": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generated": False,
            "training_promotion_allowed": False,
        },
    }
    checks = derive_gate_checks(report)
    passed = all(checks.values())
    report.update(
        {
            "status": (
                "passed_daily_labels_release_allowed"
                if passed
                else "failed_daily_labels_release_blocked"
            ),
            "gate_checks": checks,
            "daily_labels_gate_passed": passed,
            "release_registry_creation_allowed": passed,
            "blocking_reasons": []
            if passed
            else sorted(key for key, value in checks.items() if not value),
        }
    )
    core_sha256 = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
    report["build_id"] = f"build-{core_sha256[:12]}"
    directory = write_report(report, output_base)
    return report, directory


def _validated_output_base(path: Path) -> Path:
    target = path if path.is_absolute() else REPO_ROOT / path
    resolved = target.resolve(strict=False)
    if resolved in {Path("/"), Path.home().resolve(), REPO_ROOT.resolve()}:
        raise PeriodConformanceV11Error("conformance output 경로가 너무 넓습니다.")
    cursor = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise PeriodConformanceV11Error("conformance output 경로에 symlink가 있습니다.")
    return resolved


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("report write returned zero bytes")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_report(report: dict[str, Any], output_base: Path) -> Path:
    base = _validated_output_base(output_base)
    aggregate = _json_bytes(report)
    manifest = _json_bytes(
        {
            "schema_version": "1.0.0",
            "suite_version": SUITE_VERSION,
            "build_id": report["build_id"],
            "artifacts": {
                "aggregate.json": {
                    "bytes": len(aggregate),
                    "sha256": hashlib.sha256(aggregate).hexdigest(),
                }
            },
            "raw_daily_rows_tracked": False,
            "raw_case_output_tracked": False,
            "private_paths_recorded": False,
            "sealed_blind_accessed": False,
        }
    )
    base.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory = base / report["build_id"]
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise PeriodConformanceV11Error("기존 conformance build 경로가 안전하지 않습니다.")
        if (
            (directory / "aggregate.json").read_bytes() != aggregate
            or (directory / "build_manifest.json").read_bytes() != manifest
        ):
            raise PeriodConformanceV11Error("기존 conformance build를 덮어쓸 수 없습니다.")
        verify_report(directory)
        return directory
    directory.mkdir(mode=0o755)
    try:
        _write_exclusive(directory / "aggregate.json", aggregate)
        _write_exclusive(directory / "build_manifest.json", manifest)
    except Exception:
        for name in ("aggregate.json", "build_manifest.json"):
            try:
                (directory / name).unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise
    verify_report(directory)
    return directory


def verify_report(directory: Path) -> dict[str, Any]:
    if directory.is_symlink() or not directory.is_dir():
        raise PeriodConformanceV11Error("conformance v11 build 경로가 없습니다.")
    aggregate_path = directory / "aggregate.json"
    manifest_path = directory / "build_manifest.json"
    report = load_strict_json_object_v1_2(aggregate_path)
    manifest = load_strict_json_object_v1_2(manifest_path)
    core = dict(report)
    build_id = core.pop("build_id", None)
    expected_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    artifact = manifest.get("artifacts", {}).get("aggregate.json", {})
    inputs = report.get("inputs", {})
    if (
        build_id != expected_id
        or directory.name != expected_id
        or manifest.get("build_id") != expected_id
        or report.get("suite_version") != SUITE_VERSION
        or report.get("status") != "passed_daily_labels_release_allowed"
        or report.get("daily_labels_gate_passed") is not True
        or report.get("release_registry_creation_allowed") is not True
        or not all(derive_gate_checks(report).values())
        or inputs.get("contract_registry_sha256") != sha256_file(REGISTRY_V11_PATH)
        or inputs.get("gate_sha256") != sha256_file(GATE_PATH)
        or inputs.get("implementation_sha256") != _implementation_hashes()
        or inputs.get("daily_rows_recorded") is not False
        or inputs.get("private_paths_recorded") is not False
        or inputs.get("test_key_recorded") is not False
        or artifact.get("sha256") != sha256_file(aggregate_path)
        or artifact.get("bytes") != aggregate_path.stat().st_size
        or manifest.get("raw_daily_rows_tracked") is not False
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("private_paths_recorded") is not False
        or manifest.get("sealed_blind_accessed") is not False
    ):
        raise PeriodConformanceV11Error("conformance v11 공개 report 검증에 실패했습니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "official_dates": report["daily_label_matrix"]["cases"],
        "windows": report["window_matrix"]["windows"],
        "label_mismatches": report["window_matrix"]["label_mismatches"],
        "authority_mismatches": report["window_matrix"]["authority_mismatches"],
        "strict_full_runtime_approved": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="daily-label period conformance v11")
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
    run.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_contract_registry_v1_1()
            result = {"status": "verified", "registry_id": registry["registry_id"]}
        elif args.command == "plan":
            validate_contract_registry_v1_1()
            result = {
                "status": "planned",
                "suite_version": SUITE_VERSION,
                "official_dates": OFFICIAL_DATES,
                "window_lengths": [1, MAXIMUM_DAYS],
                "windows": EXPECTED_WINDOWS,
                "feature_flag_default": False,
                "strict_full_runtime_approved": False,
                "writes_performed": False,
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
                "official_dates": report["daily_label_matrix"]["cases"],
                "windows": report["window_matrix"]["windows"],
            }
        else:
            path = args.report_root
            directory = path if path.is_absolute() else REPO_ROOT / path
            result = verify_report(directory)
    except (OSError, ValueError, PeriodRuntimeError, PeriodConformanceV11Error) as exc:
        print(
            json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False)
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
