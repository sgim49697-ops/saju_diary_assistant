# audit_tools.py - Phase 2A 원천 감사를 읽기 전용으로 수행하고 검토·승인 Gate를 관리한다.

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from scripts.data.errors import Phase2AuditError
from scripts.data.source_tools import (
    load_config,
    parquet_collection_inventory,
    resolve_repo_path,
    sha256_file,
    source_root,
    verify_sources,
    write_json_atomic,
)

AUDIT_SCHEMA_VERSION = "1.0.0"
DECISION_SCHEMA_VERSION = "1.1.0"
EXPECTED_SOURCES = {
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy",
    "yeji_bazi_rules",
}
VERSION_PATTERN = re.compile(r"^v\d+\.\d+\.\d+$")
PILLAR_ORDER = ("year", "month", "day", "hour")
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
ELEMENTS = ("Wood", "Fire", "Earth", "Metal", "Water")
STEM_PINYIN = {
    "jia": "甲",
    "yi": "乙",
    "bing": "丙",
    "ding": "丁",
    "wu": "戊",
    "ji": "己",
    "geng": "庚",
    "xin": "辛",
    "ren": "壬",
    "gui": "癸",
}
BRANCH_PINYIN = {
    "zi": "子",
    "chou": "丑",
    "yin": "寅",
    "mao": "卯",
    "chen": "辰",
    "si": "巳",
    "wu": "午",
    "wei": "未",
    "shen": "申",
    "you": "酉",
    "xu": "戌",
    "hai": "亥",
}
PUBLIC_FORBIDDEN_KEYS = {
    "raw_text",
    "text",
    "content",
    "messages",
    "response",
    "user_question",
    "talk_id",
    "uuid",
    "synthetic_id",
    "record_id",
    "source_id",
    "locator",
    "locators",
    "private_note",
    "group_hash",
    "chart_signature",
}
YEJI_PROVENANCE_NAME_ALIASES = {"福星贵人": "福星"}
CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
KOREAN_PATTERN = re.compile(r"[가-힣]")
CJK_PATTERN = re.compile(r"[㐀-鿿]")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 JSON object여야 합니다.")
    return value


def _validate_version(value: str) -> None:
    if VERSION_PATTERN.fullmatch(value) is None:
        raise Phase2AuditError(
            f"감사 버전은 vMAJOR.MINOR.PATCH 형식이어야 합니다: {value}"
        )


def load_audit_policy(path: Path, audit_version: str) -> dict[str, Any]:
    _validate_version(audit_version)
    policy = _load_json_object(path, "감사 정책")
    if policy.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise Phase2AuditError("지원하지 않는 감사 정책 schema_version입니다.")
    if policy.get("audit_version") != audit_version:
        raise Phase2AuditError("CLI 감사 버전과 정책 파일 버전이 다릅니다.")
    if policy.get("dataset_name") != "saju_1b_baseline":
        raise Phase2AuditError("감사 정책 dataset_name이 정본과 다릅니다.")
    if policy.get("seed") != 42:
        raise Phase2AuditError("Phase 2A 결정론적 seed는 42여야 합니다.")
    if sum(policy.get("required_review", {}).values()) != 150:
        raise Phase2AuditError("필수 검토 할당 합계는 150이어야 합니다.")
    reference_total = sum(policy.get("reference_review", {}).values())
    if reference_total not in {150, 151}:
        raise Phase2AuditError("참고 검토 할당 합계는 150 또는 기존 v1.1의 151이어야 합니다.")
    decision_schema = policy.get("decision_schema_version", AUDIT_SCHEMA_VERSION)
    if decision_schema not in {AUDIT_SCHEMA_VERSION, DECISION_SCHEMA_VERSION}:
        raise Phase2AuditError("지원하지 않는 검토 결정 schema_version입니다.")
    return policy


def load_yeji_corrections(
    repo_root: Path, policy: dict[str, Any]
) -> tuple[dict[str, Any] | None, Path | None]:
    relative = policy.get("correction_manifest")
    if relative is None:
        return None, None
    if not isinstance(relative, str):
        raise Phase2AuditError("correction_manifest 경로가 올바르지 않습니다.")
    path = resolve_repo_path(repo_root, relative)
    manifest = _load_json_object(path, "YEJI correction manifest")
    if (
        manifest.get("schema_version") != AUDIT_SCHEMA_VERSION
        or manifest.get("audit_version") != policy["audit_version"]
        or manifest.get("dataset_name") != policy["dataset_name"]
        or manifest.get("source") != "yeji_bazi_rules"
    ):
        raise Phase2AuditError("YEJI correction manifest identity가 다릅니다.")
    corrections = manifest.get("corrections")
    if not isinstance(corrections, list) or not corrections:
        raise Phase2AuditError("YEJI correction 목록이 비어 있습니다.")
    identifiers = [item.get("correction_id") for item in corrections]
    if len(identifiers) != len(set(identifiers)):
        raise Phase2AuditError("YEJI correction_id가 중복됐습니다.")
    return manifest, path


def _source_fingerprint_payload(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_name": bundle.get("dataset_name"),
        "schema_version": bundle.get("schema_version"),
        "sources": bundle.get("sources"),
        "version": bundle.get("version"),
    }


def verify_source_bundle(
    repo_root: Path, bundle_path: Path
) -> tuple[dict[str, Any], dict[str, str]]:
    bundle = _load_json_object(bundle_path, "source bundle")
    if bundle.get("schema_version") != AUDIT_SCHEMA_VERSION:
        raise Phase2AuditError("source bundle schema_version이 올바르지 않습니다.")
    if bundle.get("dataset_name") != "saju_1b_baseline":
        raise Phase2AuditError("source bundle dataset_name이 정본과 다릅니다.")
    _validate_version(str(bundle.get("version", "")))
    expected_build = sha256_json(_source_fingerprint_payload(bundle))
    if bundle.get("source_build_sha256") != expected_build:
        raise Phase2AuditError("source bundle fingerprint가 입력과 일치하지 않습니다.")

    sources = bundle.get("sources")
    if (
        not isinstance(sources, list)
        or {item.get("source") for item in sources} != EXPECTED_SOURCES
    ):
        raise Phase2AuditError(
            "source bundle은 활성 원천 네 개를 정확히 포함해야 합니다."
        )
    current_hashes: dict[str, str] = {}
    for item in sources:
        relative = item.get("manifest_path")
        if not isinstance(relative, str):
            raise Phase2AuditError("source bundle manifest_path가 없습니다.")
        manifest_path = resolve_repo_path(repo_root, relative)
        if not manifest_path.is_file():
            raise Phase2AuditError(f"source manifest가 없습니다: {item.get('source')}")
        actual_hash = sha256_file(manifest_path)
        if actual_hash != item.get("manifest_sha256"):
            raise Phase2AuditError(
                f"source manifest hash가 다릅니다: {item.get('source')}"
            )
        manifest = _load_json_object(manifest_path, "source manifest")
        if manifest.get("source") != item.get("source") or manifest.get(
            "revision"
        ) != item.get("revision"):
            raise Phase2AuditError(
                f"source manifest identity가 다릅니다: {item.get('source')}"
            )
        current_hashes[str(item["source"])] = actual_hash
    return bundle, current_hashes


def compute_code_sha256(code_paths: Sequence[Path]) -> str:
    entries = []
    for path in sorted(code_paths, key=lambda item: item.name):
        if not path.is_file():
            raise Phase2AuditError(f"감사 코드 파일이 없습니다: {path.name}")
        entries.append({"name": path.name, "sha256": sha256_file(path)})
    return sha256_json(entries)


def compute_build_identity(
    policy: dict[str, Any],
    policy_path: Path,
    bundle: dict[str, Any],
    code_paths: Sequence[Path],
    *,
    correction_sha256: str | None = None,
) -> dict[str, Any]:
    inputs = {
        "audit_version": policy["audit_version"],
        "code_sha256": compute_code_sha256(code_paths),
        "dataset_name": policy["dataset_name"],
        "policy_sha256": sha256_file(policy_path),
        "schema_version": AUDIT_SCHEMA_VERSION,
        "seed": policy["seed"],
        "source_build_sha256": bundle["source_build_sha256"],
    }
    if correction_sha256 is not None:
        inputs["correction_sha256"] = correction_sha256
    full_hash = sha256_json(inputs)
    return {**inputs, "build_sha256": full_hash, "build_id": f"build-{full_hash[:12]}"}


def audit_paths(
    repo_root: Path, policy: dict[str, Any], build_id: str
) -> dict[str, Path]:
    substitutions = {
        "dataset_name": policy["dataset_name"],
        "audit_version": policy["audit_version"],
    }
    private_root = str(policy["paths"]["private_root"]).format(**substitutions)
    public_root = str(policy["paths"]["public_root"]).format(**substitutions)
    return {
        "private": resolve_repo_path(repo_root, f"{private_root}/{build_id}"),
        "public": resolve_repo_path(repo_root, f"{public_root}/{build_id}"),
    }


def prepare_audit(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
    *,
    verify_raw: bool,
) -> dict[str, Any]:
    source_config = load_config(source_config_path)
    policy = load_audit_policy(policy_path, audit_version)
    correction_manifest, correction_path = load_yeji_corrections(repo_root, policy)
    bundle_path = resolve_repo_path(repo_root, policy["source_bundle"])
    bundle, manifest_hashes = verify_source_bundle(repo_root, bundle_path)
    if verify_raw:
        try:
            verify_sources(source_config, repo_root)
        except Exception as exc:
            raise Phase2AuditError("Phase 1 원본 재검증에 실패했습니다.") from exc
    code_paths = [
        Path(__file__),
        Path(__file__).with_name("phase2_audit.py"),
        Path(__file__).with_name("source_tools.py"),
        Path(__file__).with_name("archive_safety.py"),
        Path(__file__).with_name("errors.py"),
        repo_root / "requirements-data.txt",
        source_config_path,
    ]
    identity = compute_build_identity(
        policy,
        policy_path,
        bundle,
        code_paths,
        correction_sha256=(
            sha256_file(correction_path) if correction_path is not None else None
        ),
    )
    return {
        "source_config": source_config,
        "policy": policy,
        "bundle": bundle,
        "bundle_path": bundle_path,
        "manifest_hashes": manifest_hashes,
        "identity": identity,
        "correction_manifest": correction_manifest,
        "correction_path": correction_path,
        "paths": audit_paths(repo_root, policy, identity["build_id"]),
    }


def audit_plan(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=True
    )
    return {
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "mode": "plan",
        "private_path": context["paths"]["private"].relative_to(repo_root).as_posix(),
        "public_path": context["paths"]["public"].relative_to(repo_root).as_posix(),
        "required_review_count": sum(context["policy"]["required_review"].values()),
        "reference_review_count": sum(context["policy"]["reference_review"].values()),
        "source_build_sha256": context["bundle"]["source_build_sha256"],
        "writes_performed": False,
    }


def _duckdb_module() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise Phase2AuditError(
            "duckdb가 없습니다. uv pip으로 requirements-data.txt를 설치하세요."
        ) from exc
    return duckdb


def _iter_query_rows(cursor: Any, batch_size: int = 2_000) -> Iterator[tuple[Any, ...]]:
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield from rows


def _normalize_token(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def canonical_chart_from_nemotron(pillars: Any) -> str:
    if isinstance(pillars, str):
        try:
            pillars = json.loads(pillars)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid pillars JSON") from exc
    if not isinstance(pillars, dict):
        raise TypeError("pillars must be an object")
    values: list[str] = []
    for pillar_name in PILLAR_ORDER:
        pillar = pillars.get(pillar_name)
        if not isinstance(pillar, dict):
            raise TypeError("missing pillar")
        stem = _normalize_token(pillar.get("stem_hanja"))
        branch = _normalize_token(pillar.get("branch_hanja"))
        if stem not in STEMS or branch not in BRANCHES:
            raise ValueError("invalid stem or branch")
        values.extend((stem, branch))
    return "".join(values)


def canonical_chart_from_bazi(facts: Any) -> str:
    if not isinstance(facts, dict) or not isinstance(facts.get("pillars"), dict):
        raise TypeError("missing facts.pillars")
    values: list[str] = []
    for pillar_name in PILLAR_ORDER:
        pillar = facts["pillars"].get(pillar_name)
        if not isinstance(pillar, dict):
            raise TypeError("missing pillar")
        stem = STEM_PINYIN.get(_normalize_token(pillar.get("stem")).lower())
        branch = BRANCH_PINYIN.get(_normalize_token(pillar.get("branch")).lower())
        if stem is None or branch is None:
            raise ValueError("invalid pinyin stem or branch")
        values.extend((stem, branch))
    return "".join(values)


def leakage_group_id(kind: str, value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return f"{kind}:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _compile_patterns(policy: dict[str, Any], name: str) -> list[re.Pattern[str]]:
    return [
        re.compile(pattern, re.IGNORECASE)
        for pattern in policy["safety_patterns"][name]
    ]


def _matches_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def _text_quality_flags(text: str) -> set[str]:
    flags: set[str] = set()
    if CONTROL_CHARACTER_PATTERN.search(text):
        flags.add("control_character")
    if "\ufffd" in text:
        flags.add("replacement_character")
    if KOREAN_PATTERN.search(text):
        flags.add("contains_korean")
    elif CJK_PATTERN.search(text):
        flags.add("cjk_without_korean")
    return flags


def _stable_key(*values: Any) -> str:
    return hashlib.sha256(
        "|".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()


def _locator_token(locator: dict[str, Any]) -> str:
    return sha256_json(locator)


def _length_stats(values: Sequence[int]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0, "p90": 0, "p95": 0, "max": 0}
    ordered = sorted(values)

    def percentile(fraction: float) -> int:
        return int(ordered[round((len(ordered) - 1) * fraction)])

    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 6),
        "p50": percentile(0.50),
        "p90": percentile(0.90),
        "p95": percentile(0.95),
        "max": int(ordered[-1]),
    }


def _candidate(
    source: str,
    stable_value: Any,
    locator: dict[str, Any],
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "source": source,
        "stable_key": _stable_key(source, stable_value),
        "locator": locator,
        **metadata,
    }


def _source_parquet_paths(
    config: dict[str, Any], repo_root: Path, source_name: str
) -> list[tuple[Path, dict[str, Any]]]:
    root = source_root(config, repo_root, source_name)
    manifest = _load_json_object(root / "SOURCE_MANIFEST.json", "source manifest")
    result = []
    for item in manifest.get("files", []):
        if str(item.get("path", "")).lower().endswith(".parquet"):
            result.append((root / item["path"], item))
    return result


def scan_nemotron(
    config: dict[str, Any], repo_root: Path, policy: dict[str, Any]
) -> dict[str, Any]:
    duckdb = _duckdb_module()
    parquet_entries = _source_parquet_paths(config, repo_root, "nemotron_saju")
    connection = duckdb.connect(database=":memory:")
    candidates: list[dict[str, Any]] = []
    charts_by_variant: dict[str, Counter[str]] = defaultdict(Counter)
    narrative_lengths: list[int] = []
    failures: Counter[str] = Counter()
    safety_counts: Counter[str] = Counter()
    projection_hashes: set[str] = set()
    uuids: set[str] = set()
    duplicate_uuid_count = 0
    text_quality: Counter[str] = Counter()
    patterns = {
        name: _compile_patterns(policy, f"nemotron_{name}")
        for name in ("health", "death_accident", "certainty", "financial_guarantee")
    }
    required_keys = set(policy["required_nemotron_narrative_keys"])
    row_count = 0
    try:
        for path, manifest_item in parquet_entries:
            variant = str(manifest_item.get("source_variant", "unknown"))
            cursor = connection.execute(
                "SELECT uuid, saju_pillars, saju_narrative, saju_narrative_error "
                "FROM read_parquet(?)",
                [str(path)],
            )
            for row_index, row in enumerate(_iter_query_rows(cursor)):
                row_count += 1
                uuid, pillars_raw, narrative_raw, narrative_error = row
                flags: list[str] = []
                if not isinstance(uuid, str) or not uuid.strip():
                    failures["missing_uuid"] += 1
                elif uuid in uuids:
                    duplicate_uuid_count += 1
                else:
                    uuids.add(uuid)
                try:
                    chart = canonical_chart_from_nemotron(pillars_raw)
                    charts_by_variant[variant][chart] += 1
                except (ValueError, TypeError):
                    chart = ""
                    failures["invalid_chart"] += 1

                try:
                    narrative = (
                        json.loads(narrative_raw)
                        if isinstance(narrative_raw, str)
                        else None
                    )
                except json.JSONDecodeError:
                    narrative = None
                if not isinstance(narrative, dict):
                    failures["invalid_narrative_json"] += 1
                    narrative_text = ""
                else:
                    missing = required_keys - set(narrative)
                    if missing:
                        failures["missing_narrative_keys"] += 1
                    empty = [
                        key
                        for key in required_keys
                        if not str(narrative.get(key, "")).strip()
                    ]
                    if empty:
                        failures["empty_narrative_values"] += 1
                    narrative_text = "\n".join(
                        str(narrative.get(key, "")) for key in sorted(required_keys)
                    )
                if narrative_error is not None:
                    failures["narrative_error_not_null"] += 1
                narrative_length = len(narrative_text)
                narrative_lengths.append(narrative_length)
                text_quality.update(_text_quality_flags(narrative_text))
                for flag_name, regexes in patterns.items():
                    if _matches_any(regexes, narrative_text):
                        flags.append(flag_name)
                        safety_counts[flag_name] += 1

                projection_hash = _stable_key(uuid, pillars_raw, narrative_raw)
                projection_hashes.add(projection_hash)
                relative = path.relative_to(repo_root).as_posix()
                candidates.append(
                    _candidate(
                        "nemotron_saju",
                        uuid or f"{relative}:{row_index}",
                        {"kind": "parquet", "path": relative, "row_index": row_index},
                        variant=variant,
                        chart=chart,
                        length=narrative_length,
                        flags=sorted(flags),
                    )
                )
    finally:
        connection.close()

    aggregate = parquet_collection_inventory([path for path, _ in parquet_entries])
    all_charts = set().union(*(set(counter) for counter in charts_by_variant.values()))
    v6_charts = set(charts_by_variant.get("v6", {}))
    v7_charts = set(charts_by_variant.get("v7", {}))
    return {
        "candidates": candidates,
        "charts": all_charts,
        "public": {
            "row_count": row_count,
            "variant_rows": {
                variant: sum(counter.values())
                for variant, counter in sorted(charts_by_variant.items())
            },
            "variant_unique_charts": {
                variant: len(counter)
                for variant, counter in sorted(charts_by_variant.items())
            },
            "variant_duplicate_chart_rows": {
                variant: sum(counter.values()) - len(counter)
                for variant, counter in sorted(charts_by_variant.items())
            },
            "cross_variant_chart_overlap_count": len(v6_charts & v7_charts),
            "canonical_chart_count": len(all_charts),
            "invalid_chart_count": failures["invalid_chart"],
            "narrative_length": _length_stats(narrative_lengths),
            "required_field_failures": dict(sorted(failures.items())),
            "safety_flag_counts": {
                name: safety_counts[name]
                for name in (
                    "health",
                    "death_accident",
                    "certainty",
                    "financial_guarantee",
                )
            },
            "text_quality_flag_counts": dict(sorted(text_quality.items())),
            "duplicate_uuid_count": duplicate_uuid_count,
            "audit_projection_exact_duplicate_count": row_count
            - len(projection_hashes),
            "full_row_duplicate_estimate": (
                aggregate["row_hash_duplicate_estimate"] if aggregate else None
            ),
        },
        "blocking_findings": [
            code
            for code, count in (
                ("NEMOTRON_INVALID_CHART", failures["invalid_chart"]),
                ("NEMOTRON_INVALID_NARRATIVE", failures["invalid_narrative_json"]),
                ("NEMOTRON_REQUIRED_FIELD_FAILURE", sum(failures.values())),
            )
            if count
        ],
    }


def _validate_bazi_structure(facts: Any) -> tuple[bool, bool]:
    if not isinstance(facts, dict):
        return False, False
    pillars = facts.get("pillars")
    day_master = facts.get("day_master")
    expected_counts = facts.get("element_counts")
    if (
        not isinstance(pillars, dict)
        or not isinstance(day_master, dict)
        or not isinstance(expected_counts, dict)
    ):
        return False, False
    day = pillars.get("day")
    day_ok = isinstance(day, dict) and (
        _normalize_token(day.get("stem")).lower()
        == _normalize_token(day_master.get("stem")).lower()
        and _normalize_token(day.get("stem_element"))
        == _normalize_token(day_master.get("element"))
    )
    actual: Counter[str] = Counter()
    for pillar_name in PILLAR_ORDER:
        pillar = pillars.get(pillar_name)
        if not isinstance(pillar, dict):
            return day_ok, False
        actual[_normalize_token(pillar.get("stem_element"))] += 1
        actual[_normalize_token(pillar.get("branch_element"))] += 1
    count_ok = sum(actual.values()) == 8 and all(
        actual[element] == int(expected_counts.get(element, -1)) for element in ELEMENTS
    )
    return day_ok, count_ok


def scan_bazi(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    duckdb = _duckdb_module()
    parquet_entries = _source_parquet_paths(config, repo_root, "bazi_sft")
    connection = duckdb.connect(database=":memory:")
    candidates: list[dict[str, Any]] = []
    charts: Counter[str] = Counter()
    chart_splits: dict[str, set[str]] = defaultdict(set)
    synthetic_splits: dict[str, set[str]] = defaultdict(set)
    question_types: Counter[str] = Counter()
    rule_counts: Counter[int] = Counter()
    response_lengths: list[int] = []
    failures: Counter[str] = Counter()
    projection_hashes: set[str] = set()
    example_ids: set[str] = set()
    duplicate_example_id_count = 0
    chart_synthetic_keys: dict[str, set[str]] = defaultdict(set)
    text_quality: Counter[str] = Counter()
    row_count = 0
    try:
        for path, item in parquet_entries:
            split = str(item.get("split", "unknown"))
            cursor = connection.execute(
                "SELECT example_id, synthetic_id, facts, retrieved_rules, question_type, response "
                "FROM read_parquet(?)",
                [str(path)],
            )
            for row_index, row in enumerate(_iter_query_rows(cursor)):
                row_count += 1
                example_id, synthetic_id, facts, rules, question_type, response = row
                try:
                    chart = canonical_chart_from_bazi(facts)
                    charts[chart] += 1
                    chart_splits[chart].add(split)
                except (ValueError, TypeError):
                    chart = ""
                    failures["invalid_chart"] += 1
                day_ok, counts_ok = _validate_bazi_structure(facts)
                if not day_ok:
                    failures["day_master_mismatch"] += 1
                if not counts_ok:
                    failures["element_count_mismatch"] += 1
                if not isinstance(rules, list) or not rules:
                    failures["missing_retrieved_rules"] += 1
                    rules = []
                elif any(
                    not isinstance(rule, dict)
                    or any(
                        not str(rule.get(key, "")).strip()
                        for key in ("id", "name", "citation", "effect")
                    )
                    for rule in rules
                ):
                    failures["invalid_retrieved_rule_structure"] += 1
                qtype = _normalize_token(question_type)
                if not qtype:
                    failures["missing_question_type"] += 1
                question_types[qtype] += 1
                rule_count = len(rules)
                rule_counts[rule_count] += 1
                response_length = len(str(response or ""))
                response_lengths.append(response_length)
                text_quality.update(_text_quality_flags(str(response or "")))
                if not isinstance(example_id, str) or not example_id:
                    failures["missing_example_id"] += 1
                elif example_id in example_ids:
                    duplicate_example_id_count += 1
                else:
                    example_ids.add(example_id)
                if not isinstance(synthetic_id, str) or not synthetic_id:
                    failures["missing_synthetic_id"] += 1
                    synthetic_key = f"{path.name}:{row_index}"
                else:
                    synthetic_key = synthetic_id
                    synthetic_splits[synthetic_id].add(split)
                if chart:
                    chart_synthetic_keys[chart].add(
                        _stable_key("synthetic", synthetic_key)
                    )
                projection_hashes.add(
                    _stable_key(example_id, synthetic_id, facts, rules, qtype, response)
                )
                relative = path.relative_to(repo_root).as_posix()
                candidates.append(
                    _candidate(
                        "bazi_sft",
                        example_id or f"{relative}:{row_index}",
                        {"kind": "parquet", "path": relative, "row_index": row_index},
                        chart=chart,
                        question_type=qtype,
                        retrieved_rule_count=rule_count,
                        response_length=response_length,
                        split=split,
                        synthetic_key=_stable_key("synthetic", synthetic_key),
                    )
                )
    finally:
        connection.close()

    aggregate = parquet_collection_inventory([path for path, _ in parquet_entries])
    split_names = sorted(
        {split for values in chart_splits.values() for split in values}
    )
    chart_overlap: dict[str, int] = {}
    for left_index, left in enumerate(split_names):
        for right in split_names[left_index + 1 :]:
            chart_overlap[f"{left}__{right}"] = sum(
                1
                for values in chart_splits.values()
                if left in values and right in values
            )
    return {
        "candidates": candidates,
        "charts": set(charts),
        "public": {
            "row_count": row_count,
            "synthetic_group_count": len(synthetic_splits),
            "synthetic_groups_crossing_upstream_splits": sum(
                1 for values in synthetic_splits.values() if len(values) > 1
            ),
            "canonical_chart_count": len(charts),
            "duplicate_chart_rows": row_count - len(charts),
            "charts_reused_across_synthetic_groups": sum(
                1 for values in chart_synthetic_keys.values() if len(values) > 1
            ),
            "chart_overlap_by_upstream_split": chart_overlap,
            "question_type_counts": dict(sorted(question_types.items())),
            "retrieved_rule_count_distribution": {
                str(key): value for key, value in sorted(rule_counts.items())
            },
            "response_length": _length_stats(response_lengths),
            "structure_failures": dict(sorted(failures.items())),
            "text_quality_flag_counts": dict(sorted(text_quality.items())),
            "duplicate_example_id_count": duplicate_example_id_count,
            "audit_projection_exact_duplicate_count": row_count
            - len(projection_hashes),
            "full_row_duplicate_estimate": (
                aggregate["row_hash_duplicate_estimate"] if aggregate else None
            ),
        },
        "blocking_findings": [
            code
            for code, count in (
                ("BAZI_INVALID_CHART", failures["invalid_chart"]),
                ("BAZI_DAY_MASTER_MISMATCH", failures["day_master_mismatch"]),
                ("BAZI_ELEMENT_COUNT_MISMATCH", failures["element_count_mismatch"]),
                (
                    "BAZI_RULE_STRUCTURE_FAILURE",
                    failures["invalid_retrieved_rule_structure"],
                ),
            )
            if count
        ],
    }


def _zip_json_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        lists = [item for item in value.values() if isinstance(item, list)]
        if len(lists) == 1 and all(isinstance(item, dict) for item in lists[0]):
            return lists[0]
    raise Phase2AuditError("AI Hub labeling JSON의 레코드 구조가 예상과 다릅니다.")


def _turn_texts(content: Any, prefix: str) -> list[str]:
    if not isinstance(content, dict):
        return []
    entries: list[tuple[int, str]] = []
    pattern = re.compile(rf"^{re.escape(prefix)}0*([1-9]\d*)$", re.IGNORECASE)
    for key, value in content.items():
        match = pattern.fullmatch(str(key))
        if match and isinstance(value, str) and value.strip():
            entries.append((int(match.group(1)), value.strip()))
    return [value for _, value in sorted(entries)]


def scan_aihub(
    config: dict[str, Any], repo_root: Path, policy: dict[str, Any]
) -> dict[str, Any]:
    root = source_root(config, repo_root, "aihub_empathy")
    manifest = _load_json_object(root / "SOURCE_MANIFEST.json", "source manifest")
    label_zips = [
        root / item["path"]
        for item in manifest.get("files", [])
        if str(item.get("path", "")).lower().endswith(".zip")
        and "라벨링데이터" in str(item.get("path", ""))
    ]
    if len(label_zips) != 2:
        raise Phase2AuditError(
            "AI Hub train/validation labeling zip 두 개를 찾지 못했습니다."
        )

    self_harm_patterns = _compile_patterns(policy, "aihub_self_harm")
    clinical_patterns = _compile_patterns(policy, "aihub_clinical")
    pii_patterns = [
        re.compile(r"\b01[016789][ -]?\d{3,4}[ -]?\d{4}\b"),
        re.compile(r"\b\d{6}[ -]?[1-4]\d{6}\b"),
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ]
    candidates: list[dict[str, Any]] = []
    emotions: Counter[str] = Counter()
    pair_counts: Counter[int] = Counter()
    situation_sizes: Counter[int] = Counter()
    human_lengths: list[int] = []
    system_lengths: list[int] = []
    safety_counts: Counter[str] = Counter()
    group_splits: dict[str, set[str]] = defaultdict(set)
    group_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    record_hashes_by_split: dict[str, set[str]] = defaultdict(set)
    all_record_hashes: set[str] = set()
    failures: Counter[str] = Counter()
    text_quality: Counter[str] = Counter()
    row_count = 0
    for path in sorted(label_zips):
        normalized = path.as_posix().lower()
        split = "validation" if "validation" in normalized else "train"
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise Phase2AuditError("AI Hub labeling zip을 열 수 없습니다.") from exc
        with archive:
            members = [
                item
                for item in archive.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".json")
            ]
            if len(members) != 1:
                raise Phase2AuditError(
                    "AI Hub labeling zip에는 JSON 하나만 있어야 합니다."
                )
            member = members[0]
            try:
                with archive.open(member) as stream:
                    document = json.load(stream)
            except (json.JSONDecodeError, UnicodeError, OSError) as exc:
                raise Phase2AuditError(
                    "AI Hub labeling JSON 파싱에 실패했습니다."
                ) from exc
            records = _zip_json_records(document)
            for row_index, record in enumerate(records):
                row_count += 1
                talk = record.get("talk") if isinstance(record, dict) else None
                profile = record.get("profile") if isinstance(record, dict) else None
                talk_id = (
                    talk.get("id", {}).get("talk-id")
                    if isinstance(talk, dict)
                    else None
                )
                content = talk.get("content") if isinstance(talk, dict) else None
                emotion = profile.get("emotion") if isinstance(profile, dict) else None
                emotion_label = (
                    _normalize_token(emotion.get("type"))
                    if isinstance(emotion, dict)
                    else ""
                )
                situations = (
                    emotion.get("situation") if isinstance(emotion, dict) else None
                )
                if not isinstance(talk_id, (str, int)) or not str(talk_id).strip():
                    failures["missing_talk_id"] += 1
                    talk_key = f"{path.name}:{row_index}"
                else:
                    talk_key = str(talk_id)
                if not emotion_label:
                    failures["missing_emotion_type"] += 1
                emotions[emotion_label] += 1
                situation_sizes[
                    len(situations) if isinstance(situations, list) else -1
                ] += 1
                human = _turn_texts(content, "HS")
                system = _turn_texts(content, "SS")
                pair_count = min(len(human), len(system))
                pair_counts[pair_count] += 1
                if pair_count < 2:
                    failures["insufficient_turn_pairs"] += 1
                human_lengths.extend(len(value) for value in human)
                system_lengths.extend(len(value) for value in system)
                combined = "\n".join([*human, *system])
                text_quality.update(_text_quality_flags(combined))
                flags: list[str] = []
                if _matches_any(self_harm_patterns, combined):
                    flags.append("self_harm")
                    safety_counts["self_harm"] += 1
                if _matches_any(clinical_patterns, combined):
                    flags.append("clinical")
                    safety_counts["clinical"] += 1
                if _matches_any(pii_patterns, combined):
                    flags.append("pii")
                    safety_counts["pii"] += 1
                group_key = _stable_key("aihub-talk", talk_key)
                group_splits[group_key].add(split)
                record_hash = sha256_json(record)
                all_record_hashes.add(record_hash)
                record_hashes_by_split[split].add(record_hash)
                relative = path.relative_to(repo_root).as_posix()
                item = _candidate(
                    "aihub_empathy",
                    f"{talk_key}:{split}:{row_index}",
                    {
                        "kind": "zip_json",
                        "path": relative,
                        "member": member.filename,
                        "row_index": row_index,
                    },
                    emotion=emotion_label,
                    flags=sorted(flags),
                    group_key=group_key,
                    split=split,
                    system_length=sum(len(value) for value in system),
                )
                candidates.append(item)
                group_candidates[group_key].append(item)

    split_names = sorted(record_hashes_by_split)
    record_overlap = 0
    if len(split_names) == 2:
        record_overlap = len(
            record_hashes_by_split[split_names[0]]
            & record_hashes_by_split[split_names[1]]
        )
    return {
        "candidates": candidates,
        "group_candidates": group_candidates,
        "public": {
            "row_count": row_count,
            "emotion_type_count": len([key for key in emotions if key]),
            "emotion_counts": dict(sorted(emotions.items())),
            "situation_size_distribution": {
                str(key): value for key, value in sorted(situation_sizes.items())
            },
            "turn_pair_count_distribution": {
                str(key): value for key, value in sorted(pair_counts.items())
            },
            "unique_talk_group_count": len(group_splits),
            "upstream_cross_split_talk_overlap_count": sum(
                1 for values in group_splits.values() if len(values) > 1
            ),
            "upstream_cross_split_exact_record_overlap_count": record_overlap,
            "exact_record_duplicate_count": row_count - len(all_record_hashes),
            "human_utterance_length": _length_stats(human_lengths),
            "system_response_length": _length_stats(system_lengths),
            "safety_flag_counts": {
                name: safety_counts[name] for name in ("self_harm", "clinical", "pii")
            },
            "text_quality_flag_counts": dict(sorted(text_quality.items())),
            "structure_failures": dict(sorted(failures.items())),
        },
        "blocking_findings": [
            code
            for code, count in (
                ("AIHUB_MISSING_TALK_ID", failures["missing_talk_id"]),
                ("AIHUB_INSUFFICIENT_TURNS", failures["insufficient_turn_pairs"]),
            )
            if count
        ],
    }


def _leaf_count(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_leaf_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(_leaf_count(item) for item in value)
    return 1


def _mapping_tokens_valid(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_mapping_tokens_valid(item) for item in value.values())
    if isinstance(value, list):
        return all(_mapping_tokens_valid(item) for item in value)
    if not isinstance(value, str):
        return True
    token = _normalize_token(value)
    if not token:
        return False
    relevant = "".join(
        character for character in token if character in STEMS + BRANCHES
    )
    if not relevant:
        return True
    return all(character in STEMS + BRANCHES for character in relevant)


def _nested_value(value: dict[str, Any], field_path: Sequence[str]) -> Any:
    current: Any = value
    for key in field_path:
        if not isinstance(current, dict) or key not in current:
            raise Phase2AuditError("YEJI correction field_path가 원본에 없습니다.")
        current = current[key]
    return current


def _replace_nested_value(
    value: dict[str, Any], field_path: Sequence[str], replacement: Any
) -> None:
    if not field_path:
        raise Phase2AuditError("YEJI correction field_path가 비어 있습니다.")
    parent: Any = value
    for key in field_path[:-1]:
        if not isinstance(parent, dict) or key not in parent:
            raise Phase2AuditError("YEJI correction field_path가 원본에 없습니다.")
        parent = parent[key]
    final = field_path[-1]
    if not isinstance(parent, dict) or final not in parent:
        raise Phase2AuditError("YEJI correction field_path가 원본에 없습니다.")
    parent[final] = replacement


def apply_yeji_corrections(
    document: dict[str, Any], correction_manifest: dict[str, Any] | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    corrected = deepcopy(document)
    if correction_manifest is None:
        return corrected, []
    rules = corrected.get("shensha_list")
    if not isinstance(rules, list):
        raise Phase2AuditError("YEJI shensha_list가 배열이 아닙니다.")
    by_id = {
        int(rule["id"]): rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("id"), int)
    }
    applied: list[dict[str, Any]] = []
    for correction in correction_manifest["corrections"]:
        try:
            rule_id = int(correction["rule_id"])
            field_path = correction["field_path"]
            expected = correction["expected_original"]
            replacement = correction["replacement"]
            correction_id = str(correction["correction_id"])
            resolves = correction["resolves"]
        except (KeyError, TypeError, ValueError) as exc:
            raise Phase2AuditError("YEJI correction 항목이 올바르지 않습니다.") from exc
        if (
            not isinstance(field_path, list)
            or not field_path
            or not all(isinstance(item, str) and item for item in field_path)
            or not isinstance(resolves, list)
            or not all(isinstance(item, str) and item for item in resolves)
        ):
            raise Phase2AuditError("YEJI correction 경로 또는 resolves가 올바르지 않습니다.")
        rule = by_id.get(rule_id)
        if rule is None:
            raise Phase2AuditError("YEJI correction 대상 rule_id가 없습니다.")
        if _nested_value(rule, field_path) != expected:
            raise Phase2AuditError(
                f"YEJI correction 예상 원본값이 다릅니다: {correction_id}"
            )
        _replace_nested_value(rule, field_path, replacement)
        applied.append(
            {
                "correction_id": correction_id,
                "field_path": field_path,
                "original": expected,
                "replacement": replacement,
                "resolves": sorted(resolves),
                "rule_id": rule_id,
            }
        )
    return corrected, applied


def scan_yeji(
    config: dict[str, Any],
    repo_root: Path,
    correction_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = source_root(config, repo_root, "yeji_bazi_rules")
    rules_path = root / "rules/shensha_51.json"
    source_revision = config["sources"]["yeji_bazi_rules"]["provenance"]["revision"]
    javascript_path = root / "provenance" / source_revision / "shensha.js"
    document = _load_json_object(rules_path, "YEJI 규칙")
    if correction_manifest is not None:
        source = config["sources"]["yeji_bazi_rules"]
        if (
            correction_manifest.get("source_revision") != source["revision"]
            or correction_manifest.get("source_file") != "rules/shensha_51.json"
            or correction_manifest.get("source_file_sha256")
            != sha256_file(rules_path)
        ):
            raise Phase2AuditError("YEJI correction 원본 identity가 다릅니다.")
    corrected_document, applied = apply_yeji_corrections(
        document, correction_manifest
    )
    try:
        javascript = javascript_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Phase2AuditError(
            "YEJI 고정 provenance JavaScript를 읽을 수 없습니다."
        ) from exc
    raw_rules = document.get("shensha_list")
    rules = corrected_document.get("shensha_list")
    if not isinstance(raw_rules, list) or not isinstance(rules, list):
        raise Phase2AuditError("YEJI shensha_list가 배열이 아닙니다.")
    categories = set(corrected_document.get("categories", {}))
    valid_types = set(corrected_document.get("type_summary", {}))
    failures: Counter[str] = Counter()
    observed_failures: Counter[str] = Counter()
    candidates: list[dict[str, Any]] = []
    ids: set[int] = set()
    known_conflict = False
    dexiu_conflict = False
    tongzi_conflict = False
    text_quality: Counter[str] = Counter()
    applied_by_rule: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for correction in applied:
        applied_by_rule[int(correction["rule_id"])].append(correction)
    for row_index, (raw_rule, rule) in enumerate(zip(raw_rules, rules, strict=True)):
        flags: list[str] = []
        if not isinstance(raw_rule, dict) or not isinstance(rule, dict):
            failures["invalid_rule_object"] += 1
            observed_failures["invalid_rule_object"] += 1
            continue
        required = (
            "id",
            "name_cn",
            "name_ko",
            "type",
            "category",
            "condition",
            "meaning",
        )
        if any(key not in rule or rule[key] in (None, "", {}) for key in required):
            failures["missing_required_field"] += 1
            flags.append("structural_anomaly")
        try:
            rule_id = int(rule.get("id"))
            if rule_id in ids:
                failures["duplicate_rule_id"] += 1
                flags.append("structural_anomaly")
            ids.add(rule_id)
        except (TypeError, ValueError):
            rule_id = -(row_index + 1)
            failures["invalid_rule_id"] += 1
            flags.append("structural_anomaly")
        if raw_rule.get("category") not in categories:
            observed_failures["invalid_category"] += 1
            flags.append("structural_anomaly")
        if rule.get("category") not in categories:
            failures["invalid_category"] += 1
            flags.append("structural_anomaly")
        if rule.get("type") not in valid_types:
            failures["invalid_type"] += 1
            flags.append("structural_anomaly")
        name_cn = str(rule.get("name_cn", ""))
        source_name = YEJI_PROVENANCE_NAME_ALIASES.get(name_cn, name_cn)
        if source_name not in javascript:
            failures["name_missing_in_provenance"] += 1
            flags.append("structural_anomaly")
        condition = rule.get("condition")
        mapping = condition.get("mapping") if isinstance(condition, dict) else None
        if mapping is not None and not _mapping_tokens_valid(mapping):
            failures["invalid_mapping_token"] += 1
            flags.append("structural_anomaly")
        if raw_rule.get("name_cn") == "词馆":
            raw_condition = raw_rule.get("condition")
            raw_mapping = (
                raw_condition.get("mapping", {})
                if isinstance(raw_condition, dict)
                else {}
            )
            gold = raw_mapping.get("金", {}) if isinstance(raw_mapping, dict) else {}
            known_conflict = (
                isinstance(gold, dict)
                and gold.get("간지") == "壬卯"
                and '"金": ["申", ["壬", "卯"]]' in javascript
                and "壬申" in javascript
            )
            flags.append("known_conflict")
        if rule_id == 5:
            raw_condition = raw_rule.get("condition", {})
            raw_mapping = (
                raw_condition.get("mapping", {})
                if isinstance(raw_condition, dict)
                else {}
            )
            dexiu_conflict = (
                raw_mapping.get("春(寅卯辰)") == {"德": "丙", "秀": "丁"}
                and 'hasTianganCombination("戊", "癸")' in javascript
                and 'hasTianganCombination("丙", "辛")' in javascript
                and 'hasTianganCombination("甲", "己")' in javascript
            )
            flags.append("condition_conflict")
        if rule_id == 38:
            raw_condition = raw_rule.get("condition", {})
            raw_complex = (
                raw_condition.get("complex_rule", "")
                if isinstance(raw_condition, dict)
                else ""
            )
            tongzi_conflict = (
                raw_complex
                == "봄秋(木火) 납음이면 일시지에 卯未巳 확인, 여름겨울(金水) 납음이면 일시지에 寅戌午 확인"
                and '"寅": ["寅", "子"]' in javascript
                and '"金": ["午", "卯"]' in javascript
                and '"土": ["辰", "巳"]' in javascript
            )
            flags.append("condition_conflict")
        if applied_by_rule.get(rule_id):
            flags.append("correction_applied")
        text_quality.update(_text_quality_flags(str(rule.get("meaning", ""))))
        relative = rules_path.relative_to(repo_root).as_posix()
        candidates.append(
            _candidate(
                "yeji_bazi_rules",
                rule_id,
                {
                    "kind": "json_list",
                    "path": relative,
                    "list_key": "shensha_list",
                    "row_index": row_index,
                },
                complexity=_leaf_count(condition),
                flags=sorted(set(flags)),
            )
        )
    if ids != set(range(1, 52)):
        failures["non_contiguous_rule_ids"] += 1
    if len(rules) != 51:
        failures["unexpected_rule_count"] += 1
    if not known_conflict:
        failures["known_conflict_signature_missing"] += 1
    resolved = {
        code for correction in applied for code in correction.get("resolves", [])
    }
    if failures:
        resolved.discard("YEJI_STRUCTURE_FAILURE")
    blockers = []
    if failures:
        blockers.append("YEJI_STRUCTURE_FAILURE")
    if known_conflict and "YEJI_CIGUAN_CONFLICT" not in resolved:
        blockers.append("YEJI_CIGUAN_CONFLICT")
    if dexiu_conflict and "YEJI_DEXIU_CONDITION_CONFLICT" not in resolved:
        blockers.append("YEJI_DEXIU_CONDITION_CONFLICT")
    if tongzi_conflict and "YEJI_TONGZI_CONDITION_CONFLICT" not in resolved:
        blockers.append("YEJI_TONGZI_CONDITION_CONFLICT")
    observed_codes = []
    if observed_failures:
        observed_codes.append("YEJI_STRUCTURE_FAILURE")
    if known_conflict:
        observed_codes.append("YEJI_CIGUAN_CONFLICT")
    if dexiu_conflict:
        observed_codes.append("YEJI_DEXIU_CONDITION_CONFLICT")
    if tongzi_conflict:
        observed_codes.append("YEJI_TONGZI_CONDITION_CONFLICT")
    return {
        "candidates": candidates,
        "corrections": applied,
        "public": {
            "rule_count": len(rules),
            "category_count": len(categories),
            "type_count": len(valid_types),
            "rule_ids_complete": ids == set(range(1, 52)),
            "provenance_name_match_count": len(rules)
            - failures["name_missing_in_provenance"],
            "observed_structural_failures": dict(sorted(observed_failures.items())),
            "structural_failures": dict(sorted(failures.items())),
            "text_quality_flag_counts": dict(sorted(text_quality.items())),
            "known_issue_codes": sorted(observed_codes),
            "resolved_issue_codes": sorted(resolved & set(observed_codes)),
            "correction_count": len(applied),
        },
        "blocking_findings": sorted(blockers),
        "observed_findings": sorted(observed_codes),
        "resolved_findings": sorted(resolved & set(observed_codes)),
    }


class ReviewQueueBuilder:
    """원문을 저장하지 않고 locator 단위의 결정론적 검토 큐를 만든다."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.items: list[dict[str, Any]] = []
        self.used_locators: set[str] = set()

    def _rank(self, queue: str, source: str, stratum: str, value: Any) -> str:
        return _stable_key(self.seed, queue, source, stratum, value)

    def _add_item(
        self,
        queue: str,
        source: str,
        stratum: str,
        candidates: Sequence[dict[str, Any]],
    ) -> None:
        locators = [candidate["locator"] for candidate in candidates]
        tokens = [_locator_token(locator) for locator in locators]
        if len(tokens) != len(set(tokens)) or any(
            token in self.used_locators for token in tokens
        ):
            raise Phase2AuditError("검토 큐 locator가 중복됐습니다.")
        review_payload = {
            "queue": queue,
            "source": source,
            "stratum": stratum,
            "locator_tokens": tokens,
        }
        item = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "review_id": sha256_json(review_payload)[:24],
            "queue": queue,
            "source": source,
            "stratum": stratum,
            "unit_type": "pair" if len(locators) == 2 else "single",
            "locators": locators,
            "flags": sorted(
                {
                    flag
                    for candidate in candidates
                    for flag in candidate.get("flags", [])
                }
            ),
        }
        self.items.append(item)
        self.used_locators.update(tokens)

    def add_singles(
        self,
        queue: str,
        source: str,
        stratum: str,
        candidates: Iterable[dict[str, Any]],
        quota: int,
        *,
        order_key: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        values = list(candidates)
        if order_key is None:
            values.sort(
                key=lambda candidate: self._rank(
                    queue, source, stratum, candidate["stable_key"]
                )
            )
        else:
            values.sort(
                key=lambda candidate: (
                    order_key(candidate),
                    self._rank(queue, source, stratum, candidate["stable_key"]),
                )
            )
        selected = 0
        for candidate in values:
            if _locator_token(candidate["locator"]) in self.used_locators:
                continue
            self._add_item(queue, source, stratum, [candidate])
            selected += 1
            if selected == quota:
                return
        raise Phase2AuditError(
            f"{source}/{stratum} 검토 표본 {quota}건을 채우지 못했습니다."
        )

    def add_group_pairs(
        self,
        queue: str,
        source: str,
        stratum: str,
        groups: dict[str, Any],
        quota: int,
        selector: Callable[[Any, set[str]], Sequence[dict[str, Any]] | None],
    ) -> None:
        selected = 0
        group_keys = sorted(
            groups,
            key=lambda key: self._rank(queue, source, stratum, key),
        )
        for group_key in group_keys:
            pair = selector(groups[group_key], self.used_locators)
            if pair is None or len(pair) != 2:
                continue
            self._add_item(queue, source, stratum, pair)
            selected += 1
            if selected == quota:
                return
        raise Phase2AuditError(
            f"{source}/{stratum} pair 표본 {quota}건을 채우지 못했습니다."
        )


def _first_unused(
    candidates: Iterable[dict[str, Any]], used: set[str]
) -> dict[str, Any] | None:
    for candidate in sorted(candidates, key=lambda item: item["stable_key"]):
        if _locator_token(candidate["locator"]) not in used:
            return candidate
    return None


def _two_unused(
    candidates: Iterable[dict[str, Any]], used: set[str]
) -> list[dict[str, Any]] | None:
    available = [
        candidate
        for candidate in sorted(candidates, key=lambda item: item["stable_key"])
        if _locator_token(candidate["locator"]) not in used
    ]
    return available[:2] if len(available) >= 2 else None


def build_review_queue(
    scan_results: dict[str, dict[str, Any]], policy: dict[str, Any]
) -> list[dict[str, Any]]:
    builder = ReviewQueueBuilder(int(policy["seed"]))

    aihub = scan_results["aihub_empathy"]
    aihub_candidates = aihub["candidates"]
    builder.add_singles(
        "required",
        "aihub_empathy",
        "self_harm_flag",
        (item for item in aihub_candidates if "self_harm" in item["flags"]),
        15,
    )
    builder.add_singles(
        "required",
        "aihub_empathy",
        "clinical_flag_nonoverlap",
        (
            item
            for item in aihub_candidates
            if "clinical" in item["flags"] and "self_harm" not in item["flags"]
        ),
        10,
    )
    builder.add_singles(
        "required",
        "aihub_empathy",
        "response_length_extreme",
        aihub_candidates,
        5,
        order_key=lambda item: -int(item["system_length"]),
    )
    cross_split_groups = {
        key: values
        for key, values in aihub["group_candidates"].items()
        if len({item["split"] for item in values}) > 1
    }

    def select_cross_split(
        values: Any, used: set[str]
    ) -> Sequence[dict[str, Any]] | None:
        by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in values:
            by_split[item["split"]].append(item)
        if "train" not in by_split or "validation" not in by_split:
            return None
        left = _first_unused(by_split["train"], used)
        right = _first_unused(by_split["validation"], used)
        return [left, right] if left is not None and right is not None else None

    builder.add_group_pairs(
        "required",
        "aihub_empathy",
        "upstream_cross_split_talk_pair",
        cross_split_groups,
        10,
        select_cross_split,
    )
    emotion_counts = Counter(
        item["emotion"] for item in aihub_candidates if item["emotion"]
    )
    if len(emotion_counts) != 60:
        raise Phase2AuditError(
            "AI Hub 감정 type이 60종이 아니어서 검토 층을 고정할 수 없습니다."
        )
    ordered_emotions = sorted(
        emotion_counts, key=lambda value: (emotion_counts[value], value)
    )
    for emotion in ordered_emotions[:30]:
        builder.add_singles(
            "required",
            "aihub_empathy",
            "rare_emotion_coverage",
            (item for item in aihub_candidates if item["emotion"] == emotion),
            1,
        )
    for emotion in ordered_emotions[30:]:
        builder.add_singles(
            "reference",
            "aihub_empathy",
            "remaining_emotion_coverage",
            (item for item in aihub_candidates if item["emotion"] == emotion),
            1,
        )

    nemotron = scan_results["nemotron_saju"]
    nemotron_candidates = nemotron["candidates"]
    for flag, quota in (("health", 8), ("death_accident", 6), ("certainty", 6)):
        builder.add_singles(
            "required",
            "nemotron_saju",
            f"{flag}_flag",
            (item for item in nemotron_candidates if flag in item["flags"]),
            quota,
        )
    cross_variant: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for item in nemotron_candidates:
        if item["chart"]:
            cross_variant[item["chart"]][item["variant"]].append(item)
    cross_variant = {
        key: values
        for key, values in cross_variant.items()
        if values.get("v6") and values.get("v7")
    }

    def select_variant_pair(
        values: Any, used: set[str]
    ) -> Sequence[dict[str, Any]] | None:
        left = _first_unused(values["v6"], used)
        right = _first_unused(values["v7"], used)
        return [left, right] if left is not None and right is not None else None

    builder.add_group_pairs(
        "required",
        "nemotron_saju",
        "cross_variant_same_chart_pair",
        cross_variant,
        10,
        select_variant_pair,
    )
    builder.add_singles(
        "required",
        "nemotron_saju",
        "shortest_narrative",
        nemotron_candidates,
        5,
        order_key=lambda item: int(item["length"]),
    )
    builder.add_singles(
        "required",
        "nemotron_saju",
        "longest_narrative",
        nemotron_candidates,
        5,
        order_key=lambda item: -int(item["length"]),
    )
    for variant in ("v6", "v7"):
        builder.add_singles(
            "reference",
            "nemotron_saju",
            f"deterministic_random_{variant}",
            (item for item in nemotron_candidates if item["variant"] == variant),
            20,
        )
    within_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in nemotron_candidates:
        if item["chart"]:
            within_variant[f"{item['variant']}:{item['chart']}"].append(item)
    within_variant = {
        key: values for key, values in within_variant.items() if len(values) >= 2
    }
    builder.add_group_pairs(
        "reference",
        "nemotron_saju",
        "within_variant_same_chart_pair",
        within_variant,
        10,
        _two_unused,
    )

    bazi = scan_results["bazi_sft"]
    bazi_candidates = bazi["candidates"]
    bazi_by_chart: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in bazi_candidates:
        if item["chart"]:
            bazi_by_chart[item["chart"]].append(item)
    reused_charts = {
        key: values
        for key, values in bazi_by_chart.items()
        if len({item["synthetic_key"] for item in values}) >= 2
    }

    def select_bazi_pair(
        values: Any, used: set[str]
    ) -> Sequence[dict[str, Any]] | None:
        available = [
            item
            for item in sorted(values, key=lambda candidate: candidate["stable_key"])
            if _locator_token(item["locator"]) not in used
        ]
        for left, right in combinations(available, 2):
            if left["synthetic_key"] != right["synthetic_key"]:
                return [left, right]
        return None

    builder.add_group_pairs(
        "required",
        "bazi_sft",
        "reused_chart_different_synthetic_pair",
        reused_charts,
        10,
        select_bazi_pair,
    )
    question_types = sorted(
        {item["question_type"] for item in bazi_candidates if item["question_type"]}
    )
    if len(question_types) != 4:
        raise Phase2AuditError(
            "bazi-sft question_type이 4종이 아니어서 검토 층을 고정할 수 없습니다."
        )
    for question_type in question_types:
        builder.add_singles(
            "required",
            "bazi_sft",
            "question_type_coverage",
            (
                item
                for item in bazi_candidates
                if item["question_type"] == question_type
            ),
            2,
        )
    for rule_count in (1, 4):
        builder.add_singles(
            "required",
            "bazi_sft",
            f"retrieved_rule_count_{rule_count}",
            (
                item
                for item in bazi_candidates
                if item["retrieved_rule_count"] == rule_count
            ),
            1,
        )
    for question_type in question_types:
        builder.add_singles(
            "reference",
            "bazi_sft",
            "question_type_reference",
            (
                item
                for item in bazi_candidates
                if item["question_type"] == question_type
            ),
            10,
        )

    yeji_candidates = scan_results["yeji_bazi_rules"]["candidates"]
    builder.add_singles(
        "required",
        "yeji_bazi_rules",
        "known_conflict",
        (item for item in yeji_candidates if "known_conflict" in item["flags"]),
        1,
    )
    builder.add_singles(
        "required",
        "yeji_bazi_rules",
        "structure_then_complexity",
        yeji_candidates,
        19,
        order_key=lambda item: (
            0 if "structural_anomaly" in item["flags"] else 1,
            -int(item["complexity"]),
        ),
    )
    builder.add_singles(
        "reference",
        "yeji_bazi_rules",
        "remaining_rules",
        yeji_candidates,
        int(policy["reference_review"]["yeji_bazi_rules"]),
        order_key=lambda item: -int(item["complexity"]),
    )

    ids = [item["review_id"] for item in builder.items]
    if len(ids) != len(set(ids)):
        raise Phase2AuditError("review_id가 충돌했습니다.")
    required_counts = Counter(
        item["source"] for item in builder.items if item["queue"] == "required"
    )
    reference_counts = Counter(
        item["source"] for item in builder.items if item["queue"] == "reference"
    )
    if dict(required_counts) != policy["required_review"]:
        raise Phase2AuditError("필수 검토 source 할당이 정책과 다릅니다.")
    if dict(reference_counts) != policy["reference_review"]:
        raise Phase2AuditError("참고 검토 source 할당이 정책과 다릅니다.")
    return builder.items


def review_summary(queue: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_queue = Counter(item["queue"] for item in queue)
    by_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_stratum: dict[str, int] = Counter()
    for item in queue:
        by_source[item["source"]][item["queue"]] += 1
        by_stratum[f"{item['queue']}:{item['source']}:{item['stratum']}"] += 1
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "contains_raw_samples": False,
        "total_review_units": len(queue),
        "required_review_units": by_queue["required"],
        "reference_review_units": by_queue["reference"],
        "by_source": {
            source: dict(sorted(values.items()))
            for source, values in sorted(by_source.items())
        },
        "by_stratum": dict(sorted(by_stratum.items())),
        "pair_unit_count": sum(item["unit_type"] == "pair" for item in queue),
    }


def assert_public_report_safe(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = PUBLIC_FORBIDDEN_KEYS & set(value)
        if forbidden:
            raise Phase2AuditError(
                f"공개 보고서 금지 필드가 있습니다: {sorted(forbidden)}"
            )
        for child in value.values():
            assert_public_report_safe(child)
    elif isinstance(value, list):
        for child in value:
            assert_public_report_safe(child)


def _write_bytes_exclusive(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise Phase2AuditError(
            f"기존 build 파일을 덮어쓸 수 없습니다: {path.name}"
        ) from exc
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if path.exists():
            path.unlink()
        raise


def write_json_exclusive(path: Path, payload: Any, mode: int) -> None:
    _write_bytes_exclusive(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n",
        mode,
    )


def write_jsonl_exclusive(path: Path, values: Iterable[Any], mode: int) -> None:
    rendered = b"".join(canonical_json_bytes(value) + b"\n" for value in values)
    _write_bytes_exclusive(path, rendered, mode)


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        for line_number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not raw_line.strip():
                continue
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                raise TypeError("not an object")
            values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase2AuditError(f"{label} JSONL을 읽을 수 없습니다.") from exc
    return values


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(path, 0o700)


def _ensure_private_parents(repo_root: Path, target_parent: Path) -> None:
    anchor = resolve_repo_path(repo_root, "data/audit")
    anchor.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(anchor, 0o700)
    if not target_parent.resolve().is_relative_to(anchor.resolve()):
        raise Phase2AuditError("비공개 감사 경로가 data/audit 밖입니다.")
    current = anchor
    for part in target_parent.relative_to(anchor).parts:
        current = current / part
        current.mkdir(exist_ok=True, mode=0o700)
        os.chmod(current, 0o700)


def _scan_all(
    config: dict[str, Any],
    repo_root: Path,
    policy: dict[str, Any],
    correction_manifest: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    return {
        "nemotron_saju": scan_nemotron(config, repo_root, policy),
        "bazi_sft": scan_bazi(config, repo_root),
        "aihub_empathy": scan_aihub(config, repo_root, policy),
        "yeji_bazi_rules": scan_yeji(config, repo_root, correction_manifest),
    }


def execute_scan(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=True
    )
    private_root = context["paths"]["private"]
    public_root = context["paths"]["public"]
    if private_root.exists() or public_root.exists():
        if private_root.exists() and public_root.exists():
            verified = verify_audit(
                repo_root,
                source_config_path,
                policy_path,
                audit_version,
                verify_raw=False,
            )
            return {
                **verified,
                "mode": "existing_verified",
                "raw_verified": True,
                "writes_performed": False,
            }
        raise Phase2AuditError(
            "감사 build가 부분 생성돼 있습니다. 같은 버전을 덮어쓸 수 없습니다."
        )

    manifest_hashes_before = dict(context["manifest_hashes"])
    results = _scan_all(
        context["source_config"],
        repo_root,
        context["policy"],
        context["correction_manifest"],
    )
    queue = build_review_queue(results, context["policy"])
    try:
        verify_sources(context["source_config"], repo_root)
    except Exception as exc:
        raise Phase2AuditError("스캔 후 Phase 1 원본 재검증에 실패했습니다.") from exc
    _, manifest_hashes_after = verify_source_bundle(repo_root, context["bundle_path"])
    if manifest_hashes_before != manifest_hashes_after:
        raise Phase2AuditError("감사 도중 source manifest가 변경됐습니다.")

    blockers = sorted(
        {
            code
            for result in results.values()
            for code in result.get("blocking_findings", [])
        }
    )
    observed_findings = sorted(
        {
            code
            for result in results.values()
            for code in result.get("observed_findings", [])
        }
    )
    resolved_findings = sorted(
        {
            code
            for result in results.values()
            for code in result.get("resolved_findings", [])
        }
    )
    aggregate = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "contains_raw_samples": False,
        "source_build_sha256": context["bundle"]["source_build_sha256"],
        "sources": {source: results[source]["public"] for source in sorted(results)},
        "cross_source": {
            "nemotron_bazi_chart_overlap_count": len(
                results["nemotron_saju"]["charts"] & results["bazi_sft"]["charts"]
            ),
            "combined_unique_chart_count": len(
                results["nemotron_saju"]["charts"] | results["bazi_sft"]["charts"]
            ),
            "leakage_group_contract": "global_canonical_eight_hanja_chart_hash",
        },
        "reserve_headroom": {
            "nemotron_saju": {
                "required_for_mix20_with_20_percent_reserve": 13200,
                "structurally_available": results["nemotron_saju"]["public"][
                    "row_count"
                ],
                "status": "passed",
            },
            "bazi_sft": {
                "required_for_mix20_with_20_percent_reserve": 6000,
                "structurally_available": results["bazi_sft"]["public"]["row_count"],
                "status": "passed",
            },
            "aihub_empathy_single": {
                "required_for_mix20_with_20_percent_reserve": 2400,
                "structurally_available": results["aihub_empathy"]["public"][
                    "row_count"
                ],
                "status": "passed",
            },
            "aihub_empathy_multiturn": {
                "required_for_mix20_with_20_percent_reserve": 1200,
                "structurally_available_groups": results["aihub_empathy"]["public"][
                    "unique_talk_group_count"
                ],
                "status": "passed",
            },
            "yeji_shensha_derived": {
                "required_for_mix20_with_20_percent_reserve": 1200,
                "source_rule_count": results["yeji_bazi_rules"]["public"]["rule_count"],
                "status": "requires_phase2b_derivation",
            },
        },
        "blocking_finding_codes": blockers,
        "observed_finding_codes": observed_findings,
        "resolved_finding_codes": resolved_findings,
        "correction_manifest_sha256": context["identity"].get(
            "correction_sha256"
        ),
        "raw_manifest_unchanged": True,
    }
    summary = review_summary(queue)
    required_review_units = sum(context["policy"]["required_review"].values())
    reference_review_units = sum(context["policy"]["reference_review"].values())
    gate_scan = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "contains_raw_samples": False,
        "status": "human_review_required",
        "required_review_remaining": required_review_units,
        "reference_review_optional": reference_review_units,
        "blocking_finding_codes": blockers,
        "resolved_finding_codes": resolved_findings,
        "approval_created": False,
    }
    for report in (aggregate, summary, gate_scan):
        assert_public_report_safe(report)

    _ensure_private_parents(repo_root, private_root.parent)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    _mkdir_private(private_root)
    public_root.mkdir(mode=0o755)
    os.chmod(public_root, 0o755)
    try:
        queue_path = private_root / "review_queue.jsonl"
        decisions_path = private_root / "decisions.jsonl"
        write_jsonl_exclusive(queue_path, queue, 0o600)
        _write_bytes_exclusive(decisions_path, b"", 0o600)
        queue_manifest = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "build_sha256": context["identity"]["build_sha256"],
            "queue_sha256": sha256_file(queue_path),
            "required_review_units": required_review_units,
            "reference_review_units": reference_review_units,
            "source_manifest_sha256": manifest_hashes_before,
            "correction_manifest_sha256": context["identity"].get(
                "correction_sha256"
            ),
        }
        write_json_exclusive(
            private_root / "queue_manifest.json", queue_manifest, 0o600
        )

        write_json_exclusive(public_root / "aggregate.json", aggregate, 0o644)
        write_json_exclusive(public_root / "review_summary.json", summary, 0o644)
        write_json_exclusive(public_root / "gate.scan.json", gate_scan, 0o644)
        build_manifest = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "contains_raw_samples": False,
            "dataset_name": context["policy"]["dataset_name"],
            "audit_version": audit_version,
            "build_id": context["identity"]["build_id"],
            "build_sha256": context["identity"]["build_sha256"],
            "source_build_sha256": context["bundle"]["source_build_sha256"],
            "policy_sha256": context["identity"]["policy_sha256"],
            "code_sha256": context["identity"]["code_sha256"],
            "correction_manifest_sha256": context["identity"].get(
                "correction_sha256"
            ),
            "seed": context["identity"]["seed"],
            "generated_at": utc_now(),
            "artifact_sha256": {
                "aggregate.json": sha256_file(public_root / "aggregate.json"),
                "gate.scan.json": sha256_file(public_root / "gate.scan.json"),
                "review_summary.json": sha256_file(public_root / "review_summary.json"),
            },
        }
        assert_public_report_safe(build_manifest)
        write_json_exclusive(public_root / "build_manifest.json", build_manifest, 0o644)
    except Exception:
        if private_root.exists():
            shutil.rmtree(private_root)
        if public_root.exists():
            shutil.rmtree(public_root)
        raise
    return {
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        "mode": "scan",
        "status": "human_review_required",
        "required_review_remaining": required_review_units,
        "reference_review_optional": reference_review_units,
        "blocking_finding_codes": blockers,
        "resolved_finding_codes": resolved_findings,
        "writes_performed": True,
    }


def _load_build_files(context: dict[str, Any]) -> dict[str, Any]:
    private_root = context["paths"]["private"]
    public_root = context["paths"]["public"]
    if not private_root.is_dir() or not public_root.is_dir():
        raise Phase2AuditError("감사 build 경로가 없습니다.")
    queue = _read_jsonl(private_root / "review_queue.jsonl", "검토 큐")
    decisions = _read_jsonl(private_root / "decisions.jsonl", "검토 결정")
    queue_manifest = _load_json_object(
        private_root / "queue_manifest.json", "queue manifest"
    )
    aggregate = _load_json_object(public_root / "aggregate.json", "감사 aggregate")
    summary = _load_json_object(public_root / "review_summary.json", "검토 summary")
    gate_scan = _load_json_object(public_root / "gate.scan.json", "scan Gate")
    build_manifest = _load_json_object(
        public_root / "build_manifest.json", "build manifest"
    )
    return {
        "queue": queue,
        "decisions": decisions,
        "queue_manifest": queue_manifest,
        "aggregate": aggregate,
        "summary": summary,
        "gate_scan": gate_scan,
        "build_manifest": build_manifest,
    }


def _decision_map(decisions: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        review_id = decision.get("review_id")
        if not isinstance(review_id, str) or not review_id:
            raise Phase2AuditError("검토 결정에 review_id가 없습니다.")
        previous = result.get(review_id)
        if "revision" not in decision:
            if previous is not None:
                raise Phase2AuditError("legacy 검토 결정에 중복 review_id가 있습니다.")
            result[review_id] = decision
            continue
        revision = decision.get("revision")
        if not isinstance(revision, int) or revision <= 0:
            raise Phase2AuditError("검토 결정 revision이 올바르지 않습니다.")
        if previous is None:
            if revision != 1 or decision.get("supersedes_decision_id") is not None:
                raise Phase2AuditError("첫 검토 결정 revision 연결이 올바르지 않습니다.")
        else:
            previous_revision = previous.get("revision")
            previous_id = previous.get("decision_id")
            if (
                not isinstance(previous_revision, int)
                or revision != previous_revision + 1
                or decision.get("supersedes_decision_id") != previous_id
            ):
                raise Phase2AuditError("검토 결정 supersedes 연결이 올바르지 않습니다.")
        result[review_id] = decision
    return result


def _validate_decisions(
    decisions: Sequence[dict[str, Any]], policy: dict[str, Any]
) -> None:
    allowed_decisions = set(policy["decision_values"])
    allowed_reasons = set(policy["reason_codes"])
    expected_schema = policy.get("decision_schema_version", AUDIT_SCHEMA_VERSION)
    _decision_map(decisions)
    for decision in decisions:
        value = decision.get("decision")
        reason = decision.get("reason_code")
        note = decision.get("private_note")
        if decision.get("schema_version") != expected_schema:
            raise Phase2AuditError("검토 결정 schema_version이 올바르지 않습니다.")
        if expected_schema == DECISION_SCHEMA_VERSION:
            decision_id = decision.get("decision_id")
            tool_hash = decision.get("review_tool_sha256")
            if (
                not isinstance(decision_id, str)
                or len(decision_id) != 24
                or not isinstance(decision.get("revision"), int)
                or not isinstance(decision.get("reviewer_version"), str)
                or not isinstance(tool_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", tool_hash) is None
            ):
                raise Phase2AuditError("검토 결정 v1.1 provenance가 올바르지 않습니다.")
            identity = {key: value for key, value in decision.items() if key != "decision_id"}
            if sha256_json(identity)[:24] != decision_id:
                raise Phase2AuditError("검토 결정 decision_id hash가 다릅니다.")
        if value not in allowed_decisions:
            raise Phase2AuditError("검토 결정 값이 정책 allowlist 밖입니다.")
        if value == "accept":
            if reason is not None or note is not None:
                raise Phase2AuditError(
                    "accept 결정에는 사유나 메모를 저장하지 않습니다."
                )
        else:
            if reason not in allowed_reasons:
                raise Phase2AuditError(
                    "비수락 결정에는 유효한 reason code가 필요합니다."
                )
            if reason == "other" and (
                not isinstance(note, str) or not note.strip()
            ):
                raise Phase2AuditError("other 결정에는 비공개 메모가 필요합니다.")
        if decision.get("reviewer") != "user" or not isinstance(
            decision.get("reviewed_at"), str
        ):
            raise Phase2AuditError("검토자 또는 검토 시각이 올바르지 않습니다.")


def audit_status_from_values(
    queue: Sequence[dict[str, Any]], decisions: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    mapped = _decision_map(decisions)
    queue_ids = {item["review_id"] for item in queue}
    if not set(mapped).issubset(queue_ids):
        raise Phase2AuditError("검토 결정에 큐 밖 review_id가 있습니다.")
    required = {item["review_id"] for item in queue if item["queue"] == "required"}
    reference = {item["review_id"] for item in queue if item["queue"] == "reference"}
    decisions_by_value = Counter(item.get("decision") for item in mapped.values())
    return {
        "required_total": len(required),
        "required_completed": len(required & set(mapped)),
        "required_remaining": len(required - set(mapped)),
        "reference_total": len(reference),
        "reference_completed": len(reference & set(mapped)),
        "decision_history_entries": len(decisions),
        "decisions": {
            str(key): value
            for key, value in sorted(
                decisions_by_value.items(), key=lambda item: str(item[0])
            )
        },
    }


def audit_status(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=False
    )
    values = _load_build_files(context)
    _validate_decisions(values["decisions"], context["policy"])
    return {
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        **audit_status_from_values(values["queue"], values["decisions"]),
        "sealed": (context["paths"]["private"] / "SEALED.json").exists(),
        "approved": (context["paths"]["public"] / "APPROVAL.json").exists(),
    }


def _load_raw_record(repo_root: Path, locator: dict[str, Any]) -> Any:
    try:
        path = resolve_repo_path(repo_root, str(locator["path"]))
        row_index = int(locator["row_index"])
        if row_index < 0:
            raise ValueError("negative row")
        if locator["kind"] == "parquet":
            duckdb = _duckdb_module()
            connection = duckdb.connect(database=":memory:")
            try:
                cursor = connection.execute(
                    "SELECT * FROM read_parquet(?) LIMIT 1 OFFSET ?",
                    [str(path), row_index],
                )
                row = cursor.fetchone()
                if row is None:
                    raise Phase2AuditError("Parquet locator 행이 없습니다.")
                columns = [item[0] for item in cursor.description]
                return dict(zip(columns, row, strict=True))
            finally:
                connection.close()
        if locator["kind"] == "zip_json":
            with (
                zipfile.ZipFile(path) as archive,
                archive.open(str(locator["member"])) as stream,
            ):
                records = _zip_json_records(json.load(stream))
            return records[row_index]
        if locator["kind"] == "json_list":
            document = _load_json_object(path, "검토 원문")
            values = document.get(str(locator["list_key"]))
            if not isinstance(values, list):
                raise Phase2AuditError("JSON list locator가 올바르지 않습니다.")
            return values[row_index]
    except (
        KeyError,
        IndexError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        raise Phase2AuditError("비공개 locator에서 원문을 읽을 수 없습니다.") from exc
    raise Phase2AuditError("지원하지 않는 locator 종류입니다.")


def _jsonl_from_bytes(payload: bytes, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        for raw_line in payload.decode("utf-8").splitlines():
            if not raw_line.strip():
                continue
            value = json.loads(raw_line)
            if not isinstance(value, dict):
                raise TypeError("not an object")
            values.append(value)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase2AuditError(f"{label} JSONL을 읽을 수 없습니다.") from exc
    return values


def append_review_decision(
    path: Path,
    queue: Sequence[dict[str, Any]],
    policy: dict[str, Any],
    *,
    review_id: str,
    decision: str,
    reason_code: str | None,
    private_note: str | None,
    reviewer_version: str,
    review_tool_sha256: str,
) -> dict[str, Any]:
    queue_ids = {item.get("review_id") for item in queue}
    if review_id not in queue_ids:
        raise Phase2AuditError("검토 큐 밖 review_id에는 판정할 수 없습니다.")
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND)
    with os.fdopen(descriptor, "a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.seek(0)
        decisions = _jsonl_from_bytes(stream.read(), "검토 결정")
        _validate_decisions(decisions, policy)
        previous = _decision_map(decisions).get(review_id)
        revision = 1 if previous is None else int(previous["revision"]) + 1
        value: dict[str, Any] = {
            "schema_version": policy.get(
                "decision_schema_version", AUDIT_SCHEMA_VERSION
            ),
            "review_id": review_id,
            "revision": revision,
            "supersedes_decision_id": (
                None if previous is None else previous.get("decision_id")
            ),
            "decision": decision,
            "reason_code": reason_code,
            "private_note": private_note,
            "reviewed_at": utc_now(),
            "reviewer": "user",
            "reviewer_version": reviewer_version,
            "review_tool_sha256": review_tool_sha256,
        }
        value["decision_id"] = sha256_json(value)[:24]
        _validate_decisions([*decisions, value], policy)
        stream.seek(0, os.SEEK_END)
        stream.write(canonical_json_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return value


def run_review(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
    *,
    source: str | None,
    required_only: bool,
    limit: int | None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=False
    )
    private_root = context["paths"]["private"]
    if (private_root / "SEALED.json").exists():
        raise Phase2AuditError("seal된 감사 build에는 결정을 추가할 수 없습니다.")
    values = _load_build_files(context)
    _validate_decisions(values["decisions"], context["policy"])
    mapped = _decision_map(values["decisions"])
    decision_values = set(context["policy"]["decision_values"])
    reason_codes = set(context["policy"]["reason_codes"])
    choices = {
        "a": "accept",
        "e": "exclude_candidate",
        "f": "rule_fix_required",
        "b": "source_block",
        "u": "uncertain",
        "s": "skip",
    }
    pending = [
        item
        for item in values["queue"]
        if item["review_id"] not in mapped
        and (source is None or item["source"] == source)
        and (not required_only or item["queue"] == "required")
    ]
    reviewed = 0
    for item in pending:
        if limit is not None and reviewed >= limit:
            break
        output_fn(
            f"\n[{item['queue']}] {item['source']} / {item['stratum']} "
            f"({item['unit_type']}, flags={','.join(item['flags']) or '-'})"
        )
        for position, locator in enumerate(item["locators"], 1):
            raw = _load_raw_record(repo_root, locator)
            output_fn(f"\n--- 원문 {position} ---")
            output_fn(json.dumps(raw, ensure_ascii=False, indent=2, default=str))
        while True:
            raw_choice = (
                input_fn(
                    "\n결정 [a=accept/e=exclude/f=rule-fix/b=block/u=uncertain/s=skip/q=종료]: "
                )
                .strip()
                .lower()
            )
            if raw_choice == "q":
                return {
                    "status": "stopped",
                    "reviewed_this_run": reviewed,
                    **audit_status_from_values(
                        values["queue"],
                        _read_jsonl(private_root / "decisions.jsonl", "검토 결정"),
                    ),
                }
            decision = choices.get(raw_choice)
            if decision in decision_values:
                break
            output_fn("지원하지 않는 결정입니다.")
        reason_code: str | None = None
        private_note: str | None = None
        if decision != "accept":
            while True:
                reason_code = input_fn(
                    f"reason code ({', '.join(sorted(reason_codes))}): "
                ).strip()
                if reason_code in reason_codes:
                    break
                output_fn("정의된 reason code를 입력하세요.")
            if reason_code == "other":
                while not private_note:
                    private_note = input_fn("비공개 사유 메모: ").strip()
        value = append_review_decision(
            private_root / "decisions.jsonl",
            values["queue"],
            context["policy"],
            review_id=item["review_id"],
            decision=decision,
            reason_code=reason_code,
            private_note=private_note,
            reviewer_version="terminal-v1.1.0",
            review_tool_sha256=sha256_file(Path(__file__)),
        )
        values["decisions"].append(value)
        reviewed += 1
    return {
        "status": "queue_exhausted" if reviewed == len(pending) else "limit_reached",
        "reviewed_this_run": reviewed,
        **audit_status_from_values(values["queue"], values["decisions"]),
    }


def evaluate_gate(
    queue: Sequence[dict[str, Any]],
    decisions: Sequence[dict[str, Any]],
    blocking_findings: Sequence[str],
) -> dict[str, Any]:
    status = audit_status_from_values(queue, decisions)
    if status["required_remaining"]:
        return {**status, "gate_status": "human_review_incomplete"}
    mapped = _decision_map(decisions)
    required_ids = {item["review_id"] for item in queue if item["queue"] == "required"}
    required_decisions = [mapped[review_id]["decision"] for review_id in required_ids]
    if any(value in {"uncertain", "skip"} for value in required_decisions):
        return {**status, "gate_status": "human_review_unresolved"}
    all_decisions = [item["decision"] for item in mapped.values()]
    if blocking_findings or any(
        value in {"rule_fix_required", "source_block"} for value in all_decisions
    ):
        return {**status, "gate_status": "blocked"}
    if any(value == "exclude_candidate" for value in all_decisions):
        return {**status, "gate_status": "ready_for_approval_with_exclusions"}
    return {**status, "gate_status": "ready_for_approval"}


def finalize_audit(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=True
    )
    private_root = context["paths"]["private"]
    public_root = context["paths"]["public"]
    if (private_root / "SEALED.json").exists():
        raise Phase2AuditError("감사 build가 이미 seal됐습니다.")
    values = _load_build_files(context)
    _validate_decisions(values["decisions"], context["policy"])
    gate = evaluate_gate(
        values["queue"],
        values["decisions"],
        values["aggregate"].get("blocking_finding_codes", []),
    )
    if gate["gate_status"] in {"human_review_incomplete", "human_review_unresolved"}:
        raise Phase2AuditError(
            "필수 150건 검토가 완료·해결되지 않아 finalize할 수 없습니다."
        )
    public_gate = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "contains_raw_samples": False,
        "status": gate["gate_status"],
        "required_review_total": gate["required_total"],
        "required_review_completed": gate["required_completed"],
        "reference_review_completed": gate["reference_completed"],
        "decision_counts": gate["decisions"],
        "blocking_finding_codes": values["aggregate"].get("blocking_finding_codes", []),
        "approval_created": False,
        "finalized_at": utc_now(),
    }
    assert_public_report_safe(public_gate)
    write_json_exclusive(public_root / "gate.final.json", public_gate, 0o644)
    seal = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "build_sha256": context["identity"]["build_sha256"],
        "queue_sha256": sha256_file(private_root / "review_queue.jsonl"),
        "decisions_sha256": sha256_file(private_root / "decisions.jsonl"),
        "public_gate_sha256": sha256_file(public_root / "gate.final.json"),
        "public_build_manifest_sha256": sha256_file(
            public_root / "build_manifest.json"
        ),
        "sealed_at": utc_now(),
    }
    write_json_exclusive(private_root / "SEALED.json", seal, 0o400)
    for path in private_root.iterdir():
        if path.is_file():
            os.chmod(path, 0o400)
    os.chmod(private_root, 0o500)
    return {
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        "status": gate["gate_status"],
        "sealed": True,
        "approved": False,
    }


def approve_audit(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
    *,
    confirm_user_approval: bool,
) -> dict[str, Any]:
    if not confirm_user_approval:
        raise Phase2AuditError("승인에는 --confirm-user-approval가 필요합니다.")
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=True
    )
    private_root = context["paths"]["private"]
    public_root = context["paths"]["public"]
    if not (private_root / "SEALED.json").is_file():
        raise Phase2AuditError("seal되지 않은 감사 build는 승인할 수 없습니다.")
    final_gate = _load_json_object(public_root / "gate.final.json", "final Gate")
    if final_gate.get("status") not in {
        "ready_for_approval",
        "ready_for_approval_with_exclusions",
    }:
        raise Phase2AuditError("차단 상태의 감사 build는 승인할 수 없습니다.")
    approval = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "contains_raw_samples": False,
        "dataset_name": context["policy"]["dataset_name"],
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "gate_status": final_gate["status"],
        "approved_at": utc_now(),
        "approval_basis": "explicit_user_instruction",
    }
    assert_public_report_safe(approval)
    write_json_exclusive(public_root / "APPROVAL.json", approval, 0o644)

    registry_path = policy_path.parent / "registry.json"
    registry = _load_json_object(registry_path, "data version registry")
    build_entry = {
        "version": audit_version,
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "status": "approved",
    }
    audit_builds = registry.setdefault("audit_builds", [])
    for index, item in enumerate(audit_builds):
        if item.get("build_sha256") == build_entry["build_sha256"]:
            audit_builds[index] = build_entry
            break
    else:
        audit_builds.append(build_entry)
    registry["approved_audit"] = build_entry
    write_json_atomic(registry_path, registry)
    return {**build_entry, "approved": True}


def verify_audit(
    repo_root: Path,
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
    *,
    verify_raw: bool = True,
) -> dict[str, Any]:
    context = prepare_audit(
        repo_root, source_config_path, policy_path, audit_version, verify_raw=verify_raw
    )
    values = _load_build_files(context)
    _validate_decisions(values["decisions"], context["policy"])
    private_root = context["paths"]["private"]
    public_root = context["paths"]["public"]
    build_manifest = values["build_manifest"]
    if build_manifest.get("build_sha256") != context["identity"]["build_sha256"]:
        raise Phase2AuditError("감사 build fingerprint가 현재 코드·정책과 다릅니다.")
    if values["queue_manifest"].get("queue_sha256") != sha256_file(
        private_root / "review_queue.jsonl"
    ):
        raise Phase2AuditError("비공개 review queue hash가 다릅니다.")
    expected_queue_units = sum(context["policy"]["required_review"].values()) + sum(
        context["policy"]["reference_review"].values()
    )
    if len(values["queue"]) != expected_queue_units:
        raise Phase2AuditError(
            f"review queue가 정책 합계 {expected_queue_units}건이 아닙니다."
        )
    summary = review_summary(values["queue"])
    if summary != values["summary"]:
        raise Phase2AuditError("공개 review summary가 비공개 큐 집계와 다릅니다.")
    for name, expected_hash in build_manifest.get("artifact_sha256", {}).items():
        path = public_root / name
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise Phase2AuditError(f"공개 감사 산출물 hash가 다릅니다: {name}")
    for value in (
        values["aggregate"],
        values["summary"],
        values["gate_scan"],
        build_manifest,
    ):
        assert_public_report_safe(value)
    if stat.S_IMODE(private_root.stat().st_mode) & 0o077:
        raise Phase2AuditError("비공개 감사 디렉터리 권한이 0700보다 넓습니다.")
    sealed_path = private_root / "SEALED.json"
    sealed = sealed_path.exists()
    if sealed:
        seal = _load_json_object(sealed_path, "audit seal")
        if seal.get("queue_sha256") != sha256_file(private_root / "review_queue.jsonl"):
            raise Phase2AuditError("seal의 queue hash가 다릅니다.")
        if seal.get("decisions_sha256") != sha256_file(
            private_root / "decisions.jsonl"
        ):
            raise Phase2AuditError("seal의 decisions hash가 다릅니다.")
        final_path = public_root / "gate.final.json"
        if not final_path.is_file() or seal.get("public_gate_sha256") != sha256_file(
            final_path
        ):
            raise Phase2AuditError("seal의 final Gate hash가 다릅니다.")
        if seal.get("public_build_manifest_sha256") != sha256_file(
            public_root / "build_manifest.json"
        ):
            raise Phase2AuditError("seal의 public build manifest hash가 다릅니다.")
        assert_public_report_safe(_load_json_object(final_path, "final Gate"))
    expected_private_mode = 0o400 if sealed else 0o600
    for name in ("review_queue.jsonl", "decisions.jsonl", "queue_manifest.json"):
        if stat.S_IMODE((private_root / name).stat().st_mode) != expected_private_mode:
            raise Phase2AuditError(f"비공개 감사 파일 권한이 올바르지 않습니다: {name}")
    approval_path = public_root / "APPROVAL.json"
    approved = approval_path.exists()
    if approved:
        if not sealed:
            raise Phase2AuditError("seal 없이 APPROVAL.json이 존재합니다.")
        approval = _load_json_object(approval_path, "audit approval")
        assert_public_report_safe(approval)
        if (
            approval.get("build_sha256") != context["identity"]["build_sha256"]
            or approval.get("approval_basis") != "explicit_user_instruction"
        ):
            raise Phase2AuditError("audit approval identity가 현재 build와 다릅니다.")
        registry = _load_json_object(
            policy_path.parent / "registry.json", "data version registry"
        )
        if (
            registry.get("approved_audit", {}).get("build_sha256")
            != context["identity"]["build_sha256"]
        ):
            raise Phase2AuditError(
                "registry approved_audit 포인터가 현재 build와 다릅니다."
            )
    status = audit_status_from_values(values["queue"], values["decisions"])
    return {
        "audit_version": audit_version,
        "build_id": context["identity"]["build_id"],
        "status": "verified",
        "required_review_remaining": status["required_remaining"],
        "reference_review_completed": status["reference_completed"],
        "sealed": sealed,
        "approved": approved,
        "public_report_safe": True,
        "raw_verified": verify_raw,
    }
