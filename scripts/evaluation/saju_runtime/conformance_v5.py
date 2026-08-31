# conformance_v5.py - KASI 실제 coverage와 두 절입 provider 후보를 fail-closed 집계한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.evaluation.external_conformance import sha256_file
from scripts.evaluation.saju_runtime.conformance import (
    KASI_FIXTURE,
    POLICY_FIXTURE,
    _host_invariance,
    _kasi_checks,
    _policy_checks,
    _synthetic_invariant_checks,
)
from scripts.evaluation.saju_runtime.conformance_v3 import (
    RuntimeConformanceV3Error,
    _load_lunar_snapshot,
    _load_minute_snapshot,
)
from scripts.evaluation.saju_runtime.conformance_v4 import (
    TEST_SIGNER_KEY,
    _hmac_invariants,
    _internal_boundary_checks,
)
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import (
    artifact_hash,
    render_delta_by_year_svg,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    DATA_ENDPOINT as ALMANAC_DATA_ENDPOINT,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    IMAGE_FILENAME as ALMANAC_IMAGE_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    IMAGE_URL as ALMANAC_IMAGE_URL,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    MANIFEST_FILENAME as ALMANAC_MANIFEST_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    RESPONSE_FILENAME as ALMANAC_RESPONSE_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    SNAPSHOT_FILENAME as ALMANAC_SNAPSHOT_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    SOURCE_PAGE as ALMANAC_SOURCE_PAGE,
)
from scripts.evaluation.saju_runtime.kasi_almanac_1964_collector import (
    KasiAlmanac1964CollectorError,
    _parse_response,
)
from scripts.evaluation.saju_runtime.kasi_collector_v1_1 import (
    SOLAR_TERM_ENDPOINT,
    SOLAR_TERM_SOURCE_PAGE,
    KasiCollectorV11Error,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    EXPECTED_YEARS as EXPECTED_TERM_SCAN_YEARS,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    MANIFEST_FILENAME as TERM_MANIFEST_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    SCAN_FILENAME as TERM_SCAN_FILENAME,
)
from scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 import (
    KasiTermCoverageCollectorError,
    _canonical_jsonl,
    _coverage_ranges,
    _validated_scan,
)
from scripts.evaluation.saju_runtime.solar_term_provider_comparison_v1 import (
    COMPARISON_VERSION,
    EXPECTED_JIE_ROWS,
    SolarTermProviderComparisonError,
    compare_providers,
    records_jsonl,
)
from scripts.evaluation.saju_runtime.strict_json import (
    DuplicateJsonKeyError,
    loads_without_duplicate_keys,
)
from scripts.runtime.calculation.calendar_provider import KoreanLunarCalendarProvider
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import (
    ENGINE_VERSION_V12,
    ID_CONTRACT_VERSION_V2,
    runtime_source_versions_v1_2,
    validate_contract_registry_v1_2,
)
from scripts.runtime.calculation.engine_v1_2 import SajuRuntimeEngineV12
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner

SCHEMA_VERSION = "1.3.1"
SUITE_VERSION = "saju-runtime-conformance-v5.0.1"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.3.1"
GATE_PATH = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.3.1.json"
SOURCE_REGISTRY_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.3.1.json"
)
GATE_SHA256 = "c8fbd682e54a5c0a1e9590029d0ad2da0587e239783aaac0d0c9a7eac8ec9278"
SOURCE_REGISTRY_SHA256 = (
    "cfcb9ced0830a1817fab13f0450eefb3492cab8f964ca82b07f06efa52ba69cd"
)
GATE_PARENT_PATH = REPO_ROOT / "configs/runtime/calculation/conformance_gate-v1.3.0.json"
GATE_PARENT_SHA256 = (
    "07e656ad99a7396506623869ab3dd3d1f3f1c2ac4bde25d5bfa25aba32877a03"
)
SOURCE_REGISTRY_PARENT_PATH = (
    REPO_ROOT / "configs/runtime/calculation/source_registry-v1.3.0.json"
)
SOURCE_REGISTRY_PARENT_SHA256 = (
    "32d36a9606d8b9809e1310d92a5734dd1a101953194a033c08d04fff67dccd58"
)
EXPECTED_TERM_ROWS = 3_600
MAX_PUBLIC_JSON_BYTES = 8 * 1024 * 1024
MAX_PRIVATE_JSON_BYTES = 8 * 1024 * 1024
MAX_PRIVATE_JSONL_BYTES = 256 * 1024 * 1024
TERM_SNAPSHOT_COLLECTOR_VERSION = "kasi-term-coverage-collector-v1.2.0"
TERM_SNAPSHOT_COLLECTOR_SHA256 = (
    "c7ce32d04a5498a2d42dccda5d2408980d49ee158e59eda615b82d7b70347865"
)
TERM_PATCH_COLLECTOR_VERSION = "kasi-term-coverage-collector-v1.2.1"
TERM_PATCH_COLLECTOR_SHA256 = (
    "8cf4e36903025c530b22d19cdf370f8da3b082feb267fa8b008860aa70a7bda1"
)
TERM_ACCEPTED_SNAPSHOT_ORIGINS = frozenset(
    {
        (TERM_SNAPSHOT_COLLECTOR_VERSION, TERM_SNAPSHOT_COLLECTOR_SHA256),
        (TERM_PATCH_COLLECTOR_VERSION, TERM_PATCH_COLLECTOR_SHA256),
    }
)
TERM_TRANSPORT_COLLECTOR_SHA256 = (
    "819b909acf4933f567ee930405aa8c09001f852e6c51751805290eb816a1b3f2"
)
ALMANAC_SNAPSHOT_COLLECTOR_VERSION = "kasi-almanac-1964-collector-v1.0.0"
ALMANAC_SNAPSHOT_COLLECTOR_SHA256 = (
    "b1002a4ebeba07a6f283d623220b57e431be4e23ae6a7eba1372de6c24182efb"
)
ALMANAC_PATCH_COLLECTOR_VERSION = "kasi-almanac-1964-collector-v1.0.1"
ALMANAC_PATCH_COLLECTOR_SHA256 = (
    "cb11263e13f204f39fbd6404232995a84ed10a69724aef174acf2191c8103d5c"
)
ALMANAC_ACCEPTED_SNAPSHOT_ORIGINS = frozenset(
    {
        (ALMANAC_SNAPSHOT_COLLECTOR_VERSION, ALMANAC_SNAPSHOT_COLLECTOR_SHA256),
        (ALMANAC_PATCH_COLLECTOR_VERSION, ALMANAC_PATCH_COLLECTOR_SHA256),
    }
)

IMPLEMENTATION_PATHS = frozenset(
    {
        "configs/runtime/calculation/conformance_gate-v1.3.1.json",
        "configs/runtime/calculation/source_registry-v1.3.1.json",
        "scripts/evaluation/saju_runtime/conformance_v5.py",
        "scripts/evaluation/saju_runtime/kasi_almanac_1964_collector.py",
        "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/kasi_minute_collector_v1_1.py",
        "scripts/evaluation/saju_runtime/kasi_term_coverage_collector_v1_2.py",
        "scripts/evaluation/saju_runtime/solar_term_provider_comparison_v1.py",
        "scripts/evaluation/saju_runtime/strict_json.py",
        "scripts/runtime/calculation/engine.py",
        "scripts/runtime/calculation/engine_v1_2.py",
        "scripts/runtime/calculation/solar_terms.py",
    }
)


class RuntimeConformanceV5Error(RuntimeError):
    """conformance v5 입력·Gate·산출물 계약 위반."""


def _regular_bytes(
    path: Path, *, maximum: int, private: bool, description: str
) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= maximum
            or (
                private
                and (
                    metadata.st_uid != os.getuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                )
            )
        ):
            raise RuntimeConformanceV5Error(
                f"{description}의 유형·소유자·권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
        if len(payload) != metadata.st_size:
            raise RuntimeConformanceV5Error(f"{description}이 읽는 중 변경됐습니다.")
        return payload
    except OSError as exc:
        raise RuntimeConformanceV5Error(f"{description}을 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _strict_json(path: Path) -> dict[str, Any]:
    try:
        value = loads_without_duplicate_keys(
            _regular_bytes(
                path,
                maximum=MAX_PUBLIC_JSON_BYTES,
                private=False,
                description="JSON 입력",
            )
        )
    except (UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise RuntimeConformanceV5Error(f"JSON 입력을 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeConformanceV5Error("JSON 입력 최상위가 object가 아닙니다.")
    return value


def _private_json(path: Path) -> dict[str, Any]:
    try:
        value = loads_without_duplicate_keys(
            _regular_bytes(
                path,
                maximum=MAX_PRIVATE_JSON_BYTES,
                private=True,
                description="공식 snapshot JSON",
            )
        )
    except (UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise RuntimeConformanceV5Error(
            "공식 snapshot JSON을 읽지 못했습니다."
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeConformanceV5Error(
            "공식 snapshot JSON 최상위가 object가 아닙니다."
        )
    return value


def _private_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        payload = _regular_bytes(
            path,
            maximum=MAX_PRIVATE_JSONL_BYTES,
            private=True,
            description="공식 snapshot JSONL",
        ).decode("utf-8")
        for number, line in enumerate(payload.splitlines(), 1):
            if not line:
                raise RuntimeConformanceV5Error(
                    f"공식 snapshot JSONL에 빈 행이 있습니다: {number}"
                )
            value = loads_without_duplicate_keys(line)
            if not isinstance(value, dict):
                raise RuntimeConformanceV5Error(
                    f"공식 snapshot JSONL 행이 object가 아닙니다: {number}"
                )
            rows.append(value)
    except (UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise RuntimeConformanceV5Error(
            "공식 snapshot JSONL을 읽지 못했습니다."
        ) from exc
    return rows


def _is_utc_timestamp(value: Any) -> bool:
    try:
        instant = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return instant.tzinfo == timezone.utc


def _validate_configs() -> tuple[dict[str, Any], dict[str, Any]]:
    gate = _strict_json(GATE_PATH)
    registry = _strict_json(SOURCE_REGISTRY_PATH)
    if (
        sha256_file(GATE_PATH) != GATE_SHA256
        or sha256_file(SOURCE_REGISTRY_PATH) != SOURCE_REGISTRY_SHA256
        or sha256_file(GATE_PARENT_PATH) != GATE_PARENT_SHA256
        or sha256_file(SOURCE_REGISTRY_PARENT_PATH)
        != SOURCE_REGISTRY_PARENT_SHA256
        or gate.get("parent")
        != {
            "path": "configs/runtime/calculation/conformance_gate-v1.3.0.json",
            "sha256": GATE_PARENT_SHA256,
        }
        or registry.get("parent")
        != {
            "path": "configs/runtime/calculation/source_registry-v1.3.0.json",
            "sha256": SOURCE_REGISTRY_PARENT_SHA256,
        }
    ):
        raise RuntimeConformanceV5Error("v1.3.1 설정 hash chain이 다릅니다.")
    official_sources = registry.get("official_sources", {})
    if not isinstance(official_sources, dict):
        raise RuntimeConformanceV5Error("v1.3.1 source registry 원천 목록이 다릅니다.")
    term_source = official_sources.get("kasi_24_divisions_openapi", {})
    almanac_source = official_sources.get("kasi_digitized_almanac_1964", {})
    if not isinstance(term_source, dict) or not isinstance(almanac_source, dict):
        raise RuntimeConformanceV5Error("v1.3.1 source registry 원천 항목이 다릅니다.")
    term_origin_rows = term_source.get("accepted_snapshot_origins", [])
    almanac_origin_rows = almanac_source.get("accepted_snapshot_origins", [])
    if not isinstance(term_origin_rows, list) or not isinstance(
        almanac_origin_rows, list
    ):
        raise RuntimeConformanceV5Error("v1.3.1 snapshot origin 목록이 다릅니다.")
    term_origins = {
        (row.get("collector_version"), row.get("collector_sha256"))
        for row in term_origin_rows
        if isinstance(row, dict)
    }
    almanac_origins = {
        (row.get("collector_version"), row.get("collector_sha256"))
        for row in almanac_origin_rows
        if isinstance(row, dict)
    }
    if (
        gate.get("schema_version") != SCHEMA_VERSION
        or gate.get("suite_version") != SUITE_VERSION
        or gate.get("profile_id") != POLICY_ID
        or gate.get("input_validation", {}).get("nested_duplicate_json_keys")
        != "reject"
        or gate.get("input_validation", {}).get("duplicate_almanac_page_sequence")
        != "reject"
        or gate.get("promotion_effects", {}).get("runtime_approved") is not False
        or gate.get("promotion_effects", {}).get("release_approval_performed")
        is not False
        or registry.get("schema_version") != SCHEMA_VERSION
        or registry.get("registry_version") != "saju-runtime-sources-v1.3.1"
        or len(term_origin_rows) != len(TERM_ACCEPTED_SNAPSHOT_ORIGINS)
        or not all(isinstance(row, dict) for row in term_origin_rows)
        or term_origins != TERM_ACCEPTED_SNAPSHOT_ORIGINS
        or term_source.get("origin_transport_collector_sha256")
        != TERM_TRANSPORT_COLLECTOR_SHA256
        or len(almanac_origin_rows) != len(ALMANAC_ACCEPTED_SNAPSHOT_ORIGINS)
        or not all(isinstance(row, dict) for row in almanac_origin_rows)
        or almanac_origins != ALMANAC_ACCEPTED_SNAPSHOT_ORIGINS
        or registry.get("validation", {}).get("nested_duplicate_json_keys") != "reject"
        or registry.get("validation", {}).get("stored_response_reparsed") is not True
    ):
        raise RuntimeConformanceV5Error("v1.3.1 Gate 또는 source registry가 다릅니다.")
    return gate, registry


def _load_term_coverage(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _private_jsonl(path)
    directory = path.parent
    manifest_path = directory / TERM_MANIFEST_FILENAME
    scan_path = directory / TERM_SCAN_FILENAME
    manifest = _private_json(manifest_path)
    scan_rows = _private_jsonl(scan_path)
    flattened = _validated_scan(scan_rows)
    supported_years = [row["year"] for row in scan_rows if row.get("item_count") == 24]
    supported_ranges = _coverage_ranges(supported_years)
    origin = (manifest.get("collector_version"), manifest.get("collector_sha256"))
    snapshot_payload = _regular_bytes(
        path,
        maximum=MAX_PRIVATE_JSONL_BYTES,
        private=True,
        description="KASI 24절기 snapshot",
    )
    scan_payload = _regular_bytes(
        scan_path,
        maximum=MAX_PRIVATE_JSONL_BYTES,
        private=True,
        description="KASI 24절기 scan",
    )
    requests_this_run = manifest.get("requests_this_run")
    if (
        path.name != "kasi_solar_terms.jsonl"
        or rows != flattened
        or snapshot_payload != _canonical_jsonl(rows)
        or scan_payload != _canonical_jsonl(scan_rows)
        or manifest.get("schema_version") != "1.2.0"
        or manifest.get("status") != "complete_api_range_scan"
        or manifest.get("source_kind") != "solar-terms-api-coverage-scan"
        or manifest.get("source_page") != SOLAR_TERM_SOURCE_PAGE
        or manifest.get("endpoint") != SOLAR_TERM_ENDPOINT
        or origin not in TERM_ACCEPTED_SNAPSHOT_ORIGINS
        or manifest.get("transport_collector_path")
        != "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py"
        or manifest.get("transport_collector_sha256") != TERM_TRANSPORT_COLLECTOR_SHA256
        or manifest.get("start_year") != 1900
        or manifest.get("end_year") != 2049
        or manifest.get("expected_periods") != EXPECTED_TERM_SCAN_YEARS
        or manifest.get("completed_periods") != EXPECTED_TERM_SCAN_YEARS
        or manifest.get("api_range_scan_complete") is not True
        or manifest.get("contract_expected_rows") != EXPECTED_TERM_ROWS
        or manifest.get("rows") != len(rows)
        or manifest.get("supported_years") != supported_years
        or manifest.get("supported_year_ranges") != supported_ranges
        or manifest.get("missing_years")
        != EXPECTED_TERM_SCAN_YEARS - len(supported_years)
        or not isinstance(manifest.get("contract_coverage_complete"), bool)
        or manifest.get("contract_coverage_complete")
        != (len(rows) == EXPECTED_TERM_ROWS)
        or manifest.get("scan_sha256") != sha256_file(scan_path)
        or manifest.get("snapshot_sha256") != sha256_file(path)
        or manifest.get("credential_source") not in {"environment", "private_file"}
        or manifest.get("credential_value_recorded") is not False
        or manifest.get("unsupported_years_filled_from_provider") is not False
        or isinstance(requests_this_run, bool)
        or not isinstance(requests_this_run, int)
        or not 0 <= requests_this_run <= EXPECTED_TERM_SCAN_YEARS
        or not _is_utc_timestamp(manifest.get("updated_at"))
    ):
        raise RuntimeConformanceV5Error(
            "KASI 24절기 coverage snapshot provenance가 다릅니다."
        )
    return rows, {
        "kind": "private_official_24_divisions_observed_api_coverage",
        "provided": True,
        "api_range_scan_complete": True,
        "contract_coverage_complete": len(rows) == EXPECTED_TERM_ROWS,
        "requested_years": [1900, 2049],
        "years_scanned": len(scan_rows),
        "supported_years": supported_years,
        "supported_year_ranges": supported_ranges,
        "rows": len(rows),
        "expected_rows": EXPECTED_TERM_ROWS,
        "sha256": sha256_file(path),
        "scan_sha256": sha256_file(scan_path),
        "manifest_sha256": sha256_file(manifest_path),
        "origin_collector_version": origin[0],
        "origin_collector_sha256": origin[1],
        "unsupported_years_filled_from_provider": False,
        "private_path_recorded": False,
    }


def _load_almanac(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if path.name != ALMANAC_SNAPSHOT_FILENAME:
        raise RuntimeConformanceV5Error("1964년 역서 snapshot 파일명이 다릅니다.")
    directory = path.parent
    manifest_path = path.parent / ALMANAC_MANIFEST_FILENAME
    response_path = directory / ALMANAC_RESPONSE_FILENAME
    image_path = directory / ALMANAC_IMAGE_FILENAME
    manifest = _private_json(manifest_path)
    row = _private_json(path)
    response_payload = _regular_bytes(
        response_path,
        maximum=MAX_PRIVATE_JSON_BYTES,
        private=True,
        description="1964년 역서 JSON 원문",
    )
    image_payload = _regular_bytes(
        image_path,
        maximum=MAX_PRIVATE_JSON_BYTES,
        private=True,
        description="1964년 역서 이미지",
    )
    snapshot_payload = _regular_bytes(
        path,
        maximum=MAX_PRIVATE_JSON_BYTES,
        private=True,
        description="1964년 역서 snapshot",
    )
    response, derived = _parse_response(response_payload)
    canonical_response = (
        json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    canonical_snapshot = (
        json.dumps(derived, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    actual_artifacts = {
        response_path.name: artifact_hash(response_payload),
        image_path.name: artifact_hash(image_payload),
        path.name: artifact_hash(snapshot_payload),
    }
    origin = (manifest.get("collector_version"), manifest.get("collector_sha256"))
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "complete"
        or origin not in ALMANAC_ACCEPTED_SNAPSHOT_ORIGINS
        or manifest.get("source_page") != ALMANAC_SOURCE_PAGE
        or manifest.get("data_endpoint") != ALMANAC_DATA_ENDPOINT
        or manifest.get("image_url") != ALMANAC_IMAGE_URL
        or manifest.get("archive_id") != "KASI_A188_Z_001"
        or manifest.get("page_sequence") != 20
        or manifest.get("artifacts") != actual_artifacts
        or manifest.get("credential_used") is not False
        or manifest.get("private_path_recorded") is not False
        or not _is_utc_timestamp(manifest.get("updated_at"))
        or response_payload != canonical_response
        or snapshot_payload != canonical_snapshot
        or row != derived
        or not image_payload.startswith(b"\xff\xd8\xff")
        or not image_payload.endswith(b"\xff\xd9")
        or row.get("source_id") != "kasi_digitized_almanac_1964"
        or row.get("year") != 1964
        or row.get("term_index") != 16
        or row.get("printed_label") != "1964-09-07 24:00 KST"
        or row.get("normalized_reference_local_minute") != "1964-09-08T00:00+09:00"
        or row.get("reference_precision") != "minute"
        or row.get("subminute_instant_claimed") is not False
    ):
        raise RuntimeConformanceV5Error("1964년 역서 snapshot provenance가 다릅니다.")
    return row, {
        "kind": "private_official_kasi_digitized_almanac",
        "provided": True,
        "complete": True,
        "archive_id": row["archive_id"],
        "page_sequence": row["page_sequence"],
        "sha256": sha256_file(path),
        "manifest_sha256": sha256_file(manifest_path),
        "image_sha256": actual_artifacts[ALMANAC_IMAGE_FILENAME]["sha256"],
        "origin_collector_version": origin[0],
        "origin_collector_sha256": origin[1],
        "private_path_recorded": False,
    }


def _safe_output_base(path: Path) -> Path:
    if path.is_symlink() or path.resolve(strict=False) != REPORT_ROOT.resolve(
        strict=False
    ):
        raise RuntimeConformanceV5Error(
            "conformance v5 output base는 고정 v1.3.1 경로여야 합니다."
        )
    return path.resolve(strict=False)


def _write_artifacts(
    report: dict[str, Any],
    records_payload: bytes,
    svg_payload: bytes,
    output_base: Path,
) -> Path:
    build_id = "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12]
    directory = _safe_output_base(output_base) / build_id
    aggregate_payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    artifact_identities = {
        "aggregate.json": artifact_hash(aggregate_payload),
        "provider_records.jsonl": artifact_hash(records_payload),
        "provider_delta_by_year.svg": artifact_hash(svg_payload),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": build_id,
        "report_type": "saju_runtime_conformance_v5",
        "artifacts": artifact_identities,
        "technical_gate_passed": report["technical_gate_passed"],
        "runtime_approved": False,
        "release_approval_performed": False,
        "release_registry_creation_allowed": False,
        "app_binding_performed": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
    payloads = {
        "aggregate.json": aggregate_payload,
        "provider_records.jsonl": records_payload,
        "provider_delta_by_year.svg": svg_payload,
        "build_manifest.json": (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
    }
    if directory.exists() or directory.is_symlink():
        if directory.is_symlink() or not directory.is_dir():
            raise RuntimeConformanceV5Error("기존 v5 build가 일반 디렉터리가 아닙니다.")
        for filename, payload in payloads.items():
            artifact = directory / filename
            if (
                artifact.is_symlink()
                or not artifact.is_file()
                or artifact.read_bytes() != payload
            ):
                raise RuntimeConformanceV5Error(
                    f"같은 v5 build ID의 artifact가 다릅니다: {filename}"
                )
        return directory
    directory.mkdir(parents=True, mode=0o755)
    try:
        for filename, payload in payloads.items():
            with (directory / filename).open("xb") as stream:
                stream.write(payload)
    except OSError as exc:
        raise RuntimeConformanceV5Error(
            "v5 build를 배타적으로 기록하지 못했습니다."
        ) from exc
    return directory


def run_conformance(
    *,
    lunar_snapshot: Path,
    solar_term_snapshot: Path,
    minute_snapshot: Path,
    almanac_snapshot: Path,
    ephemeris: Path,
    output_base: Path = REPORT_ROOT,
) -> tuple[dict[str, Any], Path]:
    gate, source_registry = _validate_configs()
    validate_contract_registry_v1_2()
    source_versions = runtime_source_versions_v1_2(
        require_runtime_dependencies=True,
        require_validator_dependencies=True,
    )
    _private_jsonl(lunar_snapshot)
    _private_json(lunar_snapshot.with_name("kasi_lunisolar_manifest.json"))
    _private_jsonl(minute_snapshot)
    _private_json(minute_snapshot.with_name("kasi_minute_references_manifest.json"))
    lunar_rows, lunar_identity = _load_lunar_snapshot(lunar_snapshot)
    term_rows, term_identity = _load_term_coverage(solar_term_snapshot)
    minute_rows, minute_identity = _load_minute_snapshot(minute_snapshot)
    almanac_row, almanac_identity = _load_almanac(almanac_snapshot)
    provider = KoreanLunarCalendarProvider()
    signer = RuntimeIdSigner.for_test(TEST_SIGNER_KEY)
    engine = SajuRuntimeEngineV12(
        signer=signer,
        enable_candidate_runtime=True,
        calendar_provider=provider,
    )
    lunar = _kasi_checks(provider, lunar_rows)
    comparison = compare_providers(
        ephemeris,
        official_term_rows=term_rows,
        minute_rows=minute_rows,
        almanac_row=almanac_row,
        include_records=True,
    )
    records = comparison.pop("records")
    comparison["records_in_report"] = False
    boundary = _internal_boundary_checks()
    selected_boundary = {
        "status": "not_run_no_selected_provider",
        "cases": 0,
        "expected_cases": gate["minimum_cases"]["internal_profile_boundary_assignment"],
        "mismatch_rows": None,
        "reason": "provider_selection_blocked",
    }
    policy = _policy_checks(engine)
    invariants = _synthetic_invariant_checks(engine)
    host = _host_invariance(engine)
    hmac_invariants = _hmac_invariants(signer)
    minimum = gate["minimum_cases"]
    astronomy_evidence = comparison["official_evidence"]["astronomy_engine"]
    skyfield_evidence = comparison["official_evidence"]["skyfield_de440s"]
    selection = comparison["selection"]
    shared_provider_checks = {
        "provider_rows_complete": comparison["rows"]
        == minimum["provider_jie_instants_each"],
        "provider_term_identity_failure_zero": comparison["term_identity_failures"]
        == 0,
        "provider_chronological_order_failure_zero": comparison[
            "astronomy_chronological_order_failures"
        ]
        == 0
        and comparison["skyfield_chronological_order_failures"] == 0,
        "provider_fixed_120_second_regression_guard": comparison[
            "fixed_regression_guard"
        ]["failures"]
        == 0,
    }
    checks = {
        "kasi_lunar_days_complete": lunar["rows"] == minimum["kasi_lunar_days"],
        "kasi_lunar_conversion_mismatch_zero": lunar["rows"]
        == minimum["kasi_lunar_days"]
        and lunar["solar_lunar_mismatches"] == 0,
        "kasi_day_ganzhi_mismatch_zero": lunar["rows"] == minimum["kasi_lunar_days"]
        and lunar["day_ganzhi_mismatches"] == 0,
        "kasi_term_api_year_scan_complete": term_identity["years_scanned"]
        == minimum["kasi_term_api_years_scanned"],
        "kasi_all_solar_term_dates_complete": term_identity["rows"]
        == minimum["kasi_all_solar_term_dates"],
        "kasi_jie_dates_complete": astronomy_evidence["openapi_jie_rows"] + 1
        == minimum["kasi_jie_dates"],
        "available_kasi_openapi_date_mismatch_zero_both_providers": astronomy_evidence[
            "openapi_date_mismatches"
        ]
        == 0
        and skyfield_evidence["openapi_date_mismatches"] == 0,
        "kasi_formal_1964_baengno_complete": almanac_identity["complete"]
        and minimum["kasi_formal_1964_baengno_rows"] == 1,
        "kasi_1964_baengno_civil_date_adjudicated": comparison[
            "adjudication_1964_baengno"
        ]["civil_date_policy_result"]
        == "astronomy_engine",
        "kasi_minute_references_complete": len(minute_rows)
        == minimum["kasi_jie_minute_references"],
        "at_least_one_provider_minute_label_mismatch_zero": min(
            astronomy_evidence["institutional_minute_label_mismatches"],
            skyfield_evidence["institutional_minute_label_mismatches"],
        )
        == 0,
        **shared_provider_checks,
        "eligible_provider_selected": selection["selected_provider"] is not None,
        "selected_provider_boundary_cases_complete": selected_boundary["cases"]
        == minimum["internal_profile_boundary_assignment"],
        "baseline_internal_boundary_cases_complete": boundary["cases"]
        == minimum["internal_profile_boundary_assignment"],
        "baseline_internal_boundary_mismatch_zero": boundary["mismatch_rows"] == 0,
        "policy_fixture_mismatch_zero": policy["mismatch_rows"] == 0,
        "unknown_range_complete": invariants["unknown_range_cases"]
        >= minimum["unknown_range"],
        "guessed_unknown_hour_zero": invariants["guessed_unknown_hour"] == 0,
        "hmac_id_vectors_complete": hmac_invariants["vectors"]
        >= minimum["hmac_id_vectors"],
        "hmac_id_mismatch_zero": all(
            hmac_invariants[key] == 0
            for key in (
                "reproducibility_mismatches",
                "prefix_failures",
                "domain_collisions",
                "key_separation_failures",
            )
        ),
        "unsupported_foreign_complete": invariants["unsupported_foreign_cases"]
        >= minimum["unsupported_foreign"],
        "unsupported_foreign_failure_zero": invariants["unsupported_foreign_failures"]
        == 0,
        "dst_gap_auto_shift_zero": invariants["dst_gap_auto_shift_failures"] == 0,
        "dst_fold_auto_pick_zero": invariants["dst_fold_auto_pick_failures"] == 0,
        "host_timezone_or_locale_drift_zero": host["byte_drift"] == 0,
        "heuristic_fact_leak_zero": invariants["heuristic_fact_leaks"] == 0,
        "source_version_id_failure_zero": invariants["source_version_id_failures"] == 0,
        "profile_id_failure_zero": invariants["profile_id_failures"] == 0,
    }
    blocking_reasons = sorted(key for key, value in checks.items() if not value)
    technical_gate_passed = not blocking_reasons
    if technical_gate_passed:
        status = "technical_gate_passed_release_not_performed"
    elif (
        selection["selected_provider"] is None
        and not term_identity["contract_coverage_complete"]
    ):
        status = "blocked_official_coverage_and_no_eligible_provider"
    elif selection["selected_provider"] is None:
        status = "blocked_no_eligible_provider"
    else:
        status = "blocked_conformance_failures"
    records_payload = records_jsonl(records)
    svg_payload = render_delta_by_year_svg(records)
    report = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION,
        "profile_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V12,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "comparison_version": COMPARISON_VERSION,
        "status": status,
        "source_versions": source_versions,
        "inputs": {
            "official_snapshots": {
                "kasi_lunisolar": lunar_identity,
                "kasi_24_divisions_observed_coverage": term_identity,
                "kasi_minute_reference": minute_identity,
                "kasi_1964_almanac": almanac_identity,
            },
            "public_fallback_fixture": {
                "path": str(KASI_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(KASI_FIXTURE),
            },
            "policy_fixture": {
                "path": str(POLICY_FIXTURE.relative_to(REPO_ROOT)),
                "sha256": sha256_file(POLICY_FIXTURE),
            },
            "gate_sha256": sha256_file(GATE_PATH),
            "source_registry_sha256": sha256_file(SOURCE_REGISTRY_PATH),
            "source_registry_version": source_registry["registry_version"],
            "implementation_sha256": {
                path: sha256_file(REPO_ROOT / path)
                for path in sorted(IMPLEMENTATION_PATHS)
            },
            "test_signer": {
                "kind": "fixed_non_production_injected",
                "key_value_recorded": False,
                "accepted_for_production": False,
            },
        },
        "official_kasi_lunisolar": lunar,
        "official_kasi_solar_term_coverage": {
            "years_scanned": term_identity["years_scanned"],
            "supported_year_ranges": term_identity["supported_year_ranges"],
            "all_term_rows": term_identity["rows"],
            "expected_all_term_rows": EXPECTED_TERM_ROWS,
            "jie_rows": astronomy_evidence["openapi_jie_rows"],
            "expected_jie_rows": EXPECTED_JIE_ROWS,
            "unsupported_years_filled_from_provider": False,
        },
        "official_kasi_1964_baengno": {
            **almanac_row,
            "raw_artifact_hashes_only": True,
        },
        "solar_term_provider_comparison": comparison,
        "selected_provider_boundary_assignment_checks": selected_boundary,
        "baseline_internal_boundary_assignment_checks": boundary,
        "policy_comparison": policy,
        "synthetic_invariants": {
            key: value for key, value in invariants.items() if key != "sample_outputs"
        },
        "hmac_id_invariants": hmac_invariants,
        "host_invariance": host,
        "public_diagnostics": {
            "provider_records": {
                "rows": len(records),
                **artifact_hash(records_payload),
                "contains_birth_or_session_data": False,
            },
            "provider_delta_by_year_svg": artifact_hash(svg_payload),
        },
        "gate_checks": checks,
        "blocking_reasons": blocking_reasons,
        "technical_gate_passed": technical_gate_passed,
        "runtime_gate_passed": technical_gate_passed,
        "runtime_approved": False,
        "release_approval_performed": False,
        "release_registry_creation_allowed": False,
        "runtime_provider_changed": False,
        "runtime_feature_flag_default": False,
        "app_binding_performed": False,
        "mix20k_v3_1_regeneration_allowed": False,
        "mix20k_v3_1_generated": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_accessed": False,
        "raw_restricted_samples_in_report": False,
    }
    if any(
        report[key] is not False
        for key in (
            "runtime_approved",
            "release_approval_performed",
            "release_registry_creation_allowed",
            "runtime_provider_changed",
            "app_binding_performed",
            "mix20k_v3_1_regeneration_allowed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
            "sealed_blind_accessed",
            "raw_restricted_samples_in_report",
        )
    ):
        raise RuntimeConformanceV5Error(
            "현재 R4~R5 범위를 넘는 상태 변경이 감지됐습니다."
        )
    directory = _write_artifacts(report, records_payload, svg_payload, output_base)
    return report, directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="한국 만세력 runtime conformance v5")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--kasi-lunar-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-solar-term-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-minute-snapshot", type=Path, required=True)
    parser.add_argument("--kasi-almanac-1964-snapshot", type=Path, required=True)
    parser.add_argument("--ephemeris", type=Path, required=True)
    parser.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, directory = run_conformance(
            lunar_snapshot=args.kasi_lunar_snapshot,
            solar_term_snapshot=args.kasi_solar_term_snapshot,
            minute_snapshot=args.kasi_minute_snapshot,
            almanac_snapshot=args.kasi_almanac_1964_snapshot,
            ephemeris=args.ephemeris,
            output_base=args.output_base,
        )
    except (
        KasiAlmanac1964CollectorError,
        KasiCollectorV11Error,
        KasiTermCoverageCollectorError,
        RuntimeCalculationError,
        RuntimeConformanceV3Error,
        RuntimeConformanceV5Error,
        SolarTermProviderComparisonError,
    ) as exc:
        message = exc.message if isinstance(exc, RuntimeCalculationError) else str(exc)
        print(json.dumps({"status": "error", "message": message}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "technical_gate_passed": report["technical_gate_passed"],
                "runtime_approved": report["runtime_approved"],
                "release_approval_performed": report["release_approval_performed"],
                "output": str(directory),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
