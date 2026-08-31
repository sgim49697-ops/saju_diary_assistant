# conformance.py - 공식·비교 fixture와 runtime 불변 조건을 집계해 fail-closed Gate를 발급한다.

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import time as time_module
from collections.abc import Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.evaluation.external_conformance import sha256_file
from scripts.evaluation.saju_runtime.kasi_collector import (
    COLLECTOR_VERSION,
)
from scripts.evaluation.saju_runtime.kasi_collector import (
    ENDPOINT as KASI_ENDPOINT,
)
from scripts.evaluation.saju_runtime.kasi_collector import (
    SOURCE_PAGE as KASI_SOURCE_PAGE,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes, stable_id
from scripts.runtime.calculation.contracts import (
    CONFIG_ROOT,
    REPO_ROOT,
    SOURCE_REGISTRY_PATH,
    load_json_object,
    runtime_source_versions,
    validate_contract_registry,
)
from scripts.runtime.calculation.engine import SajuRuntimeEngine
from scripts.runtime.calculation.facts import (
    BRANCHES,
    STEMS,
    day_pillar,
    tables,
    ten_god,
)

KASI_FIXTURE = (
    REPO_ROOT / "tests/fixtures/saju_external_conformance/kasi_lunar_200.json"
)
POLICY_FIXTURE = (
    REPO_ROOT / "tests/fixtures/saju_external_conformance/policy_cases_20.jsonl"
)
GATE_PATH = CONFIG_ROOT / "conformance_gate-v1.0.0.json"
REPORT_BASE = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.0.0"
KOREAN_STEMS = dict(
    zip(("갑", "을", "병", "정", "무", "기", "경", "신", "임", "계"), STEMS)
)
KOREAN_BRANCHES = dict(
    zip(
        ("자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해"),
        BRANCHES,
    )
)
HEURISTIC_FIELDS = {
    "daeun",
    "gongmang",
    "twelve_stages",
    "branch_relations",
    "day_strength",
    "geukguk",
    "yongsin",
    "automatic_interpretation",
    "future_event_prediction",
}
SEXAGENARY_CYCLE = {
    STEMS[index % len(STEMS)] + BRANCHES[index % len(BRANCHES)] for index in range(60)
}


class RuntimeConformanceError(RuntimeError):
    """conformance 입력·산출물 계약이 깨졌을 때 발생한다."""


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConformanceError(f"fixture가 없거나 symlink입니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise RuntimeConformanceError(
                        f"fixture에 빈 행이 있습니다: {number}"
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeConformanceError(
                        f"fixture 행은 object여야 합니다: {number}"
                    )
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceError(f"fixture를 읽지 못했습니다: {path}") from exc
    return rows


def _tool_args(
    value: date,
    *,
    birth_time: str | None,
    precision: str,
    time_range: dict[str, str] | None = None,
    country_code: str = "KR",
    timezone: str = "Asia/Seoul",
) -> dict[str, Any]:
    return {
        "birth_date": value.isoformat(),
        "calendar": "solar",
        "leap_month": None,
        "birth_time": birth_time,
        "time_precision": precision,
        "time_range": time_range,
        "birthplace": {
            "country_code": country_code,
            "city": "서울" if country_code == "KR" else "외부",
            "timezone": timezone,
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _hanja_ganzhi(korean: str) -> str:
    if len(korean) != 2:
        raise RuntimeConformanceError(f"한글 간지 형식이 다릅니다: {korean}")
    return KOREAN_STEMS[korean[0]] + KOREAN_BRANCHES[korean[1]]


def _load_kasi_rows(
    full_snapshot: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if full_snapshot is None:
        rows = json.loads(KASI_FIXTURE.read_text(encoding="utf-8"))
        supported = [row for row in rows if 1900 <= row["solar"][0] <= 2049]
        return supported, {
            "kind": "committed_stratified_snapshot",
            "path": str(KASI_FIXTURE.relative_to(REPO_ROOT)),
            "sha256": sha256_file(KASI_FIXTURE),
            "committed_rows": len(rows),
            "supported_rows": len(supported),
            "complete_supported_range": False,
        }
    if full_snapshot.is_symlink() or not full_snapshot.is_file():
        raise RuntimeConformanceError("KASI full snapshot이 없거나 symlink입니다.")
    rows = _load_jsonl(full_snapshot)
    expected_date = date(1900, 1, 1)
    normalized: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        required_fields = {
            "solar_date",
            "lunar_date",
            "leap_month",
            "day_ganzhi",
        }
        if not required_fields.issubset(row):
            raise RuntimeConformanceError(
                f"KASI full snapshot schema가 다릅니다: {number}"
            )
        try:
            actual_date = date.fromisoformat(row["solar_date"])
            lunar = row["lunar_date"]
            if (
                not isinstance(lunar, dict)
                or not {"year", "month", "day"}.issubset(lunar)
                or any(
                    isinstance(lunar[key], bool) or not isinstance(lunar[key], int)
                    for key in ("year", "month", "day")
                )
                or not isinstance(row["leap_month"], bool)
                or row["day_ganzhi"] not in SEXAGENARY_CYCLE
            ):
                raise ValueError("KASI field type or value")
            lunar_year = lunar["year"]
            lunar_month = lunar["month"]
            lunar_day = lunar["day"]
            if not (
                1899 <= lunar_year <= 2049
                and 1 <= lunar_month <= 12
                and 1 <= lunar_day <= 30
            ):
                raise ValueError("KASI lunar range")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeConformanceError(
                f"KASI full snapshot 값이 다릅니다: {number}"
            ) from exc
        if actual_date != expected_date:
            raise RuntimeConformanceError(
                f"KASI full snapshot 날짜 연속성이 깨졌습니다: {number}"
            )
        normalized.append(
            {
                "solar": [actual_date.year, actual_date.month, actual_date.day],
                "lunar": [lunar_year, lunar_month, lunar_day],
                "leap": row["leap_month"],
                "cn": ["", "", str(row["day_ganzhi"])],
            }
        )
        expected_date += timedelta(days=1)
    if len(normalized) != 54_787 or expected_date != date(2050, 1, 1):
        raise RuntimeConformanceError(
            "KASI full snapshot은 1900~2049의 54,787일이어야 합니다."
        )
    manifest_path = full_snapshot.with_name("collection_manifest.json")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeConformanceError("KASI full snapshot 수집 manifest가 없습니다.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeConformanceError(
            "KASI full snapshot 수집 manifest를 읽지 못했습니다."
        ) from exc
    source_registry = load_json_object(SOURCE_REGISTRY_PATH)
    expected_collector_hash = (
        source_registry.get("sources", {})
        .get("kasi_lunisolar_openapi", {})
        .get("collector_sha256")
    )
    if (
        manifest.get("status") != "complete"
        or manifest.get("source") != KASI_SOURCE_PAGE
        or manifest.get("endpoint") != KASI_ENDPOINT
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != expected_collector_hash
        or manifest.get("start_date") != "1900-01-01"
        or manifest.get("end_date") != "2049-12-31"
        or manifest.get("expected_rows") != 54_787
        or manifest.get("rows") != 54_787
        or manifest.get("next_date") is not None
        or manifest.get("snapshot_sha256") != sha256_file(full_snapshot)
        or manifest.get("credential_value_recorded") is not False
    ):
        raise RuntimeConformanceError(
            "KASI full snapshot 수집 provenance가 고정 계약과 다릅니다."
        )
    return normalized, {
        "kind": "private_official_full_snapshot",
        "path": "private_snapshot_redacted",
        "sha256": sha256_file(full_snapshot),
        "manifest_sha256": sha256_file(manifest_path),
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": expected_collector_hash,
        "committed_rows": 0,
        "supported_rows": len(normalized),
        "complete_supported_range": True,
    }


def _kasi_checks(
    provider: KoreanLunarCalendarProvider, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    conversion_mismatches = 0
    day_mismatches = 0
    for row in rows:
        solar = date(*row["solar"])
        actual_lunar = provider.solar_to_lunar(solar)
        expected_lunar = {
            "year": row["lunar"][0],
            "month": row["lunar"][1],
            "day": row["lunar"][2],
            "leap_month": row["leap"],
        }
        conversion_mismatches += actual_lunar != expected_lunar
        day_mismatches += day_pillar(solar)["ganzhi"] != row["cn"][2]
    return {
        "rows": len(rows),
        "solar_lunar_mismatches": conversion_mismatches,
        "day_ganzhi_mismatches": day_mismatches,
        "official_hard_mismatches": conversion_mismatches + day_mismatches,
    }


def _policy_checks(engine: SajuRuntimeEngine) -> dict[str, Any]:
    rows = _load_jsonl(POLICY_FIXTURE)
    passed = 0
    mismatches: list[dict[str, str]] = []
    excluded: list[str] = []
    for row in rows:
        case_type = row["case_type"]
        case_id = row["case_id"]
        if case_type == "four_pillars":
            item = row["input"]
            result = engine.calculate_chart(
                _tool_args(
                    date(item["year"], item["month"], item["day"]),
                    birth_time=f"{item['hour']:02d}:{item['minute']:02d}",
                    precision="exact",
                )
            )
            actual = {
                key: result["hard_facts"]["pillars"][key]["ganzhi"]
                for key in ("year", "month", "day", "hour")
            }
            expected = {
                key: _hanja_ganzhi(value) for key, value in row["expected"].items()
            }
        elif case_id == "day-boundary-midnight-20240310-2330":
            result = engine.calculate_chart(
                _tool_args(date(2024, 3, 10), birth_time="23:30", precision="exact")
            )
            actual = {
                "day": result["hard_facts"]["pillars"]["day"]["ganzhi"],
                "hour": result["hard_facts"]["pillars"]["hour"]["ganzhi"],
            }
            expected = {
                key: _hanja_ganzhi(value) for key, value in row["expected"].items()
            }
        elif case_type == "solar_term_boundary":
            item = row["input"]
            result = engine.calculate_chart(
                _tool_args(
                    date(item["year"], item["month"], item["day"]),
                    birth_time=f"{item['hour']:02d}:{item['minute']:02d}",
                    precision="exact",
                )
            )
            actual = {"year": result["hard_facts"]["pillars"]["year"]["ganzhi"]}
            expected = {"year": _hanja_ganzhi(row["expected"]["year"])}
        elif case_type == "stem_ten_gods":
            actual = {stem: ten_god("甲", stem) for stem in STEMS}
            expected = {
                KOREAN_STEMS[key]: value for key, value in row["expected"].items()
            }
        elif case_type == "branch_main_hidden_stem":
            actual = {
                branch: tables()["hidden_stems_main_first"][branch][0]
                for branch in BRANCHES
            }
            expected = {
                KOREAN_BRANCHES[key]: KOREAN_STEMS[value]
                for key, value in row["expected"].items()
            }
        else:
            excluded.append(case_id)
            continue
        if actual == expected:
            passed += 1
        else:
            mismatches.append(
                {
                    "case_id": case_id,
                    "category": "policy_profile_mismatch",
                }
            )
    return {
        "fixture_rows": len(rows),
        "comparable_rows": passed + len(mismatches),
        "passed_rows": passed,
        "mismatch_rows": len(mismatches),
        "mismatches": mismatches,
        "excluded_non_profile_cases": sorted(excluded),
    }


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_walk_keys(nested))
    return keys


def _synthetic_invariant_checks(engine: SajuRuntimeEngine) -> dict[str, Any]:
    outputs: list[dict[str, Any]] = []
    guessed_unknown_hour = 0
    for index in range(500):
        year = 1900 + index % 150
        month = index % 12 + 1
        day_value = index % 27 + 1
        if index < 250:
            arguments = _tool_args(
                date(year, month, day_value), birth_time=None, precision="unknown"
            )
        else:
            is_am = index % 2 == 0
            arguments = _tool_args(
                date(year, month, day_value),
                birth_time=None,
                precision="range",
                time_range={
                    "start": "00:00" if is_am else "12:00",
                    "end": "11:59" if is_am else "23:59",
                },
            )
        result = engine.calculate_chart(arguments)
        outputs.append(result)
        if (
            index < 250
            and result["hard_facts"].get("pillars", {}).get("hour") is not None
        ):
            guessed_unknown_hour += 1
    hash_mismatches = 0
    for index in range(200):
        payload = {
            "vector": index,
            "profile_id": "KR_CIVIL_MIDNIGHT_V1",
            "source_versions": engine.source_versions,
        }
        hash_mismatches += stable_id("sc1_", payload) != stable_id(
            "sc1_", deepcopy(payload)
        )
    foreign_failures = 0
    foreign_zones = [
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Asia/Hong_Kong",
        "Asia/Singapore",
        "Asia/Bangkok",
        "Asia/Kolkata",
        "Australia/Sydney",
        "Pacific/Auckland",
        "Pacific/Honolulu",
        "Africa/Cairo",
        "Africa/Johannesburg",
        "America/Sao_Paulo",
        "UTC",
    ]
    for zone in foreign_zones:
        result = engine.calculate_chart(
            _tool_args(
                date(2000, 1, 1),
                birth_time="12:00",
                precision="exact",
                country_code="US",
                timezone=zone,
            )
        )
        foreign_failures += not (
            result["status"] == "blocked" and result["code"] == "UNSUPPORTED_REGION"
        )
    heuristic_leaks = sum(
        bool(_walk_keys(output.get("hard_facts")) & HEURISTIC_FIELDS)
        for output in outputs
    )
    version_payload = {"input": "same", "source_versions": engine.source_versions}
    changed_payload = deepcopy(version_payload)
    changed_payload["source_versions"]["tzdb"] = "changed"
    version_id_failures = stable_id("sc1_", version_payload) == stable_id(
        "sc1_", changed_payload
    )
    profile_payload = {"input": "same", "profile_id": "KR_CIVIL_MIDNIGHT_V1"}
    changed_profile_payload = {"input": "same", "profile_id": "OTHER_PROFILE"}
    profile_id_failures = stable_id("sc1_", profile_payload) == stable_id(
        "sc1_", changed_profile_payload
    )
    gap = engine.calculate_chart(
        _tool_args(date(1987, 5, 10), birth_time="02:30", precision="exact")
    )
    fold = engine.calculate_chart(
        _tool_args(date(1987, 10, 11), birth_time="02:30", precision="exact")
    )
    return {
        "unknown_range_cases": len(outputs),
        "guessed_unknown_hour": guessed_unknown_hour,
        "canonical_hash_vectors": 200,
        "canonical_hash_mismatches": hash_mismatches,
        "unsupported_foreign_cases": len(foreign_zones),
        "unsupported_foreign_failures": foreign_failures,
        "heuristic_fact_leaks": heuristic_leaks,
        "source_version_id_failures": int(version_id_failures),
        "profile_id_failures": int(profile_id_failures),
        "dst_gap_auto_shift_failures": int(
            not (gap["status"] == "blocked" and gap["code"] == "NONEXISTENT_LOCAL_TIME")
        ),
        "dst_fold_auto_pick_failures": int(
            fold["chart_id"] is not None or fold["chart_set_id"] is None
        ),
        "sample_outputs": outputs[:2],
    }


def _host_invariance(engine: SajuRuntimeEngine) -> dict[str, Any]:
    arguments = _tool_args(date(1989, 1, 5), birth_time="13:00", precision="exact")
    old_tz = os.environ.get("TZ")
    old_locale = locale.setlocale(locale.LC_ALL)
    serialized: list[bytes] = []
    tested_locales: list[str] = []
    try:
        for timezone_name, locale_name in (
            ("UTC", "C"),
            ("Pacific/Honolulu", "C.UTF-8"),
        ):
            os.environ["TZ"] = timezone_name
            if hasattr(time_module, "tzset"):
                time_module.tzset()
            try:
                locale.setlocale(locale.LC_ALL, locale_name)
                tested_locales.append(locale_name)
            except locale.Error:
                locale.setlocale(locale.LC_ALL, "C")
                tested_locales.append("C")
            serialized.append(canonical_json_bytes(engine.calculate_chart(arguments)))
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        if hasattr(time_module, "tzset"):
            time_module.tzset()
        locale.setlocale(locale.LC_ALL, old_locale)
    return {
        "runs": len(serialized),
        "tested_locales": tested_locales,
        "byte_drift": int(len(set(serialized)) != 1),
    }


def _safe_output_base(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (REPO_ROOT / "data/reports/saju_runtime_conformance").resolve()
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise RuntimeConformanceError(
            "보고서 경로는 data/reports/saju_runtime_conformance 아래여야 합니다."
        ) from exc
    if path.is_symlink():
        raise RuntimeConformanceError("보고서 base는 symlink일 수 없습니다.")
    return resolved


def _write_artifacts(report: dict[str, Any], output_base: Path) -> Path:
    core_bytes = canonical_json_bytes(report)
    build_id = "build-" + hashlib.sha256(core_bytes).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    aggregate_hash = hashlib.sha256(aggregate.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v2",
        "aggregate_sha256": aggregate_hash,
        "runtime_gate_passed": report["runtime_gate_passed"],
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    manifest_text = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if directory.exists() or directory.is_symlink():
        aggregate_path = directory / "aggregate.json"
        manifest_path = directory / "build_manifest.json"
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or aggregate_path.is_symlink()
            or not aggregate_path.is_file()
            or manifest_path.is_symlink()
            or not manifest_path.is_file()
        ):
            raise RuntimeConformanceError(
                "기존 conformance build가 완전한 일반 파일 구성이 아닙니다."
            )
        try:
            existing_aggregate = aggregate_path.read_text(encoding="utf-8")
            existing_manifest = manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise RuntimeConformanceError(
                "기존 conformance build를 읽지 못했습니다."
            ) from exc
        if existing_aggregate != aggregate or existing_manifest != manifest_text:
            raise RuntimeConformanceError(
                "같은 build ID의 기존 보고서 내용이 다릅니다."
            )
        return directory
    try:
        directory.mkdir(parents=True, mode=0o755)
        with (directory / "aggregate.json").open("x", encoding="utf-8") as stream:
            stream.write(aggregate)
        with (directory / "build_manifest.json").open("x", encoding="utf-8") as stream:
            stream.write(manifest_text)
    except OSError as exc:
        raise RuntimeConformanceError(
            "conformance build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    full_kasi_snapshot: Path | None = None,
    output_base: Path = REPORT_BASE,
) -> tuple[dict[str, Any], Path]:
    validate_contract_registry()
    source_versions = runtime_source_versions(require_dependencies=True)
    provider = KoreanLunarCalendarProvider()
    engine = SajuRuntimeEngine(
        enable_candidate_runtime=True, calendar_provider=provider
    )
    kasi_rows, kasi_input = _load_kasi_rows(full_kasi_snapshot)
    kasi = _kasi_checks(provider, kasi_rows)
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    gate = load_json_object(GATE_PATH)
    required = gate["minimum_cases"]
    observed_jie_cases = sum(
        1
        for line in POLICY_FIXTURE.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["case_type"] == "solar_term_boundary"
    )
    implementation_paths = [
        "scripts/runtime/calculation/canonical.py",
        "scripts/runtime/calculation/contracts.py",
        "scripts/runtime/calculation/timezone_resolver.py",
        "scripts/runtime/calculation/calendar_provider.py",
        "scripts/runtime/calculation/normalize.py",
        "scripts/runtime/calculation/solar_terms.py",
        "scripts/runtime/calculation/facts.py",
        "scripts/runtime/calculation/engine.py",
        "scripts/evaluation/saju_runtime/conformance.py",
    ]
    checks = {
        "kasi_supported_solar_days": kasi["rows"]
        == required["kasi_supported_solar_days"],
        "jie_boundary_before_at_after": observed_jie_cases
        == required["jie_boundary_before_at_after"],
        "unknown_range": invariants["unknown_range_cases"] >= required["unknown_range"],
        "canonical_hash_vectors": invariants["canonical_hash_vectors"]
        >= required["canonical_hash_vectors"],
        "unsupported_foreign": invariants["unsupported_foreign_cases"]
        >= required["unsupported_foreign"],
        "official_hard_mismatch_zero": kasi["official_hard_mismatches"] == 0,
        "profile_boundary_mismatch_zero": policy["mismatch_rows"] == 0,
        "guessed_unknown_hour_zero": invariants["guessed_unknown_hour"] == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "unsupported_foreign_failure_zero": invariants["unsupported_foreign_failures"]
        == 0,
        "source_version_id_failure_zero": invariants["source_version_id_failures"] == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
        "unclassified_mismatch_zero": all(
            item.get("category") for item in policy["mismatches"]
        ),
        "runtime_profile_preapproved": False,
    }
    coverage = {
        "kasi_supported_solar_days": {
            "observed": kasi["rows"],
            "required": required["kasi_supported_solar_days"],
        },
        "jie_boundary_before_at_after": {
            "observed": observed_jie_cases,
            "required": required["jie_boundary_before_at_after"],
        },
    }
    blocking_reasons = sorted(key for key, passed in checks.items() if not passed)
    gate_passed = not blocking_reasons
    if (
        not checks["kasi_supported_solar_days"]
        or not checks["jie_boundary_before_at_after"]
    ):
        status = "blocked_missing_official_full_snapshots"
    elif not checks["runtime_profile_preapproved"]:
        status = "blocked_profile_approval_pending"
    elif not gate_passed:
        status = "blocked_conformance_failures"
    else:
        status = "passed"
    report = {
        "schema_version": "1.0.0",
        "suite_version": "saju-runtime-conformance-v2.0.0",
        "profile_id": "KR_CIVIL_MIDNIGHT_V1",
        "engine_version": "saju-runtime-python-v1.0.0",
        "status": status,
        "source_versions": source_versions,
        "inputs": {
            "kasi": kasi_input,
            "policy_fixture": {
                "path": str(POLICY_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(POLICY_FIXTURE),
            },
            "runtime_registry_sha256": sha256_file(
                CONFIG_ROOT / "registry-v1.0.0.json"
            ),
            "gate_sha256": sha256_file(GATE_PATH),
            "implementation_sha256": {
                relative: sha256_file(REPO_ROOT / relative)
                for relative in implementation_paths
            },
        },
        "official_kasi": kasi,
        "policy_comparison": policy,
        "synthetic_invariants": {
            key: value for key, value in invariants.items() if key != "sample_outputs"
        },
        "host_invariance": host,
        "coverage": coverage,
        "gate_checks": checks,
        "blocking_reasons": blocking_reasons,
        "runtime_gate_passed": gate_passed,
        "runtime_feature_flag_default": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
    }
    directory = _write_artifacts(report, output_base)
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v2")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--kasi-full-snapshot", type=Path)
    parser.add_argument("--output-base", type=Path, default=REPORT_BASE)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            full_kasi_snapshot=args.kasi_full_snapshot,
            output_base=args.output_base,
        )
    except RuntimeConformanceError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "runtime_gate_passed": report["runtime_gate_passed"],
                "output": str(directory),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
