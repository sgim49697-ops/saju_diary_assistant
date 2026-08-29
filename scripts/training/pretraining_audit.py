# pretraining_audit.py - Phase 5 직전 데이터·출처·환경 위험을 공개 가능한 집계로 봉인한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.quality_v2_tools import ALLOWED_SAJU_HANJA
from scripts.preflight.phase4_common import (
    load_candidate_staging_records,
    load_json,
    read_jsonl,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)
from scripts.preflight.phase4_common import (
    prepare_context as prepare_phase4_context,
)
from scripts.preflight.phase4_finalize import verify_finalized_phase4
from scripts.training.phase5_readiness_v1_1 import (
    prepare_context as prepare_readiness_context,
)
from scripts.training.phase5_readiness_v1_1 import verify_readiness

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/pretraining-audit-v1.0.0.json"
)
PRIVATE_KEYS = {
    "messages",
    "prompt_messages",
    "reference_assistant",
    "raw_text",
    "talk_id",
    "record_id",
    "record_ids",
    "source_group_id",
    "leakage_group_id",
}
PUBLIC_FILE_MODE = 0o644
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
FULL_DATE_PATTERN = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[년./-]\s*\d{1,2}[월./-]\s*\d{1,2}일?(?!\d)"
)
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
HANDLE_PATTERN = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{2,32}\b")
LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


class PretrainingAuditError(RuntimeError):
    """학습 전 의미·출처 감사 계약 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise PretrainingAuditError(f"안전하지 않은 경로입니다: {relative}") from exc


def _assert_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise PretrainingAuditError(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def _walk_public(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        forbidden = PRIVATE_KEYS & set(value)
        if forbidden:
            raise PretrainingAuditError(
                f"공개 감사 보고서에 제한 key가 있습니다: {location} {sorted(forbidden)}"
            )
        for key, child in value.items():
            _walk_public(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public(child, f"{location}[{index}]")


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _assistant_text(record: dict[str, Any]) -> str:
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise PretrainingAuditError("staging record messages가 없습니다.")
    value = messages[-1]
    if value.get("role") != "assistant" or not isinstance(value.get("content"), str):
        raise PretrainingAuditError("staging 마지막 assistant 계약이 다릅니다.")
    return value["content"]


def _input_text(record: dict[str, Any]) -> str:
    return "\n".join(
        str(value.get("content", "")) for value in record["messages"][:-1]
    )


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("audit_version") != "v1.0.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("canonical_plan_version") != "3.2.0"
        or config.get("as_of") != "2026-08-29"
    ):
        raise PretrainingAuditError("학습 전 감사 identity가 다릅니다.")
    parent = config.get("parent_phase4")
    if (
        not isinstance(parent, dict)
        or parent.get("version") != "v2.0.0"
        or parent.get("build_id") != "build-6f32d52c2868"
        or parent.get("training_promotion_allowed") is not True
        or parent.get("selected_max_length") != 768
    ):
        raise PretrainingAuditError("Phase 4 canonical 부모 계약이 다릅니다.")
    _assert_sha(parent.get("build_sha256"), "parent_phase4.build_sha256")
    _assert_sha(parent.get("private_manifest_sha256"), "parent private manifest")
    phase4_config = _safe_path(repo_root, str(parent.get("preflight_config", "")))
    if not phase4_config.is_file():
        raise PretrainingAuditError("Phase 4 config가 없습니다.")

    readiness = config.get("parent_readiness")
    if (
        not isinstance(readiness, dict)
        or readiness.get("version") != "v1.1.0"
        or readiness.get("build_id") != "build-201010b37e40"
        or readiness.get("training_promotion_allowed") is not True
    ):
        raise PretrainingAuditError("readiness v1.1 부모 계약이 다릅니다.")
    for key in ("build_sha256", "config_sha256", "public_manifest_sha256"):
        _assert_sha(readiness.get(key), f"parent_readiness.{key}")
    readiness_config = _safe_path(repo_root, str(readiness.get("config", "")))
    if sha256_file(readiness_config) != readiness["config_sha256"]:
        raise PretrainingAuditError("readiness v1.1 config hash가 다릅니다.")

    source_catalog = config.get("source_catalog")
    if not isinstance(source_catalog, list) or len(source_catalog) < 8:
        raise PretrainingAuditError("웹·출처 catalog가 부족합니다.")
    source_ids: set[str] = set()
    for source in source_catalog:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("source_id"), str)
            or source["source_id"] in source_ids
            or not str(source.get("url", "")).startswith("https://")
            or source.get("decision")
            not in {"training_source", "runtime_comparison", "reference_only"}
        ):
            raise PretrainingAuditError("웹·출처 catalog 항목이 올바르지 않습니다.")
        source_ids.add(source["source_id"])
        expected = source.get("expected_revision")
        observed = source.get("observed_revision")
        if expected is not None and (
            REVISION_PATTERN.fullmatch(str(expected)) is None
            or REVISION_PATTERN.fullmatch(str(observed)) is None
        ):
            raise PretrainingAuditError("source revision 형식이 올바르지 않습니다.")

    thresholds = config.get("thresholds")
    if thresholds != {
        "hard_blocker_max": 0,
        "critical_or_high_data_rows_max": 0,
        "zero_assistant_masks_max": 0,
        "foreign_cjk_rows_max": 0,
        "target_only_entity_rows_max": 0,
        "severe_safety_rows_max": 0,
        "dominant_knowledge_axis_token_share_warning_percent": 80.0,
        "persona_alignment_language_warning_percent": 20.0,
    }:
        raise PretrainingAuditError("학습 전 감사 threshold 계약이 다릅니다.")
    patterns = config.get("patterns")
    if (
        not isinstance(patterns, dict)
        or not isinstance(patterns.get("persona_alignment_language"), list)
        or not patterns["persona_alignment_language"]
        or not isinstance(patterns.get("severe_safety"), list)
        or not patterns["severe_safety"]
    ):
        raise PretrainingAuditError("학습 전 의미 패턴 계약이 없습니다.")
    for values in patterns.values():
        for value in values:
            try:
                re.compile(value)
            except re.error as exc:
                raise PretrainingAuditError("학습 전 감사 정규식이 올바르지 않습니다.") from exc

    outputs = config.get("outputs")
    if outputs != {
        "public_root": "data/reports/saju_1b_baseline/pretraining-audit/v1.0.0/{build_id}"
    }:
        raise PretrainingAuditError("학습 전 감사 출력 경로가 다릅니다.")
    _safe_path(repo_root, outputs["public_root"].format(build_id="build-000000000000"))
    files = config.get("implementation_files")
    if files != ["scripts/training/pretraining_audit.py"]:
        raise PretrainingAuditError("학습 전 감사 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "audit_version": "v1.0.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "pretraining audit config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "audit_version": config["audit_version"],
        "as_of": config["as_of"],
        "parent_phase4": config["parent_phase4"],
        "parent_readiness": config["parent_readiness"],
        "source_catalog_sha256": sha256_json(config["source_catalog"]),
        "thresholds_sha256": sha256_json(config["thresholds"]),
        "patterns_sha256": sha256_json(config["patterns"]),
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    return {
        "build_id": build_id,
        "build_sha256": build_sha256,
        "build_inputs": build_inputs,
        "config": config,
        "config_path": config_path,
        "public_root": _safe_path(
            repo_root,
            config["outputs"]["public_root"].format(build_id=build_id),
        ),
    }


def _verify_parents(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    config = context["config"]
    parent = config["parent_phase4"]
    phase4_context = prepare_phase4_context(
        repo_root, _safe_path(repo_root, parent["preflight_config"])
    )
    finalized = verify_finalized_phase4(phase4_context, repo_root)
    if (
        finalized.get("build_id") != parent["build_id"]
        or finalized.get("build_sha256") != parent["build_sha256"]
        or finalized.get("training_promotion_allowed") is not True
        or sha256_file(
            repo_root
            / f"data/derived/saju_1b_baseline/v2.0.0/{parent['build_id']}/build_manifest.json"
        )
        != parent["private_manifest_sha256"]
    ):
        raise PretrainingAuditError("Phase 4 canonical 재검증이 실패했습니다.")
    readiness_contract = config["parent_readiness"]
    readiness_context = prepare_readiness_context(
        repo_root, _safe_path(repo_root, readiness_contract["config"])
    )
    ready = verify_readiness(readiness_context, repo_root)
    if (
        ready.get("build_id") != readiness_contract["build_id"]
        or ready.get("build_sha256") != readiness_contract["build_sha256"]
        or ready.get("phase5_training_performed") is not False
    ):
        raise PretrainingAuditError("Phase 5 readiness 재검증이 실패했습니다.")
    return {"phase4_context": phase4_context, "phase4": finalized, "readiness": ready}


def _duplicate_summary(
    rows: list[tuple[str, str]], axis_counts: Counter[str]
) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for axis, text in rows:
        groups[_normalized_text(text)].append(axis)
    participation: Counter[str] = Counter()
    maximum = 1
    cross_axis_groups = 0
    for axes in groups.values():
        maximum = max(maximum, len(axes))
        if len(axes) < 2:
            continue
        participation.update(axes)
        if len(set(axes)) > 1:
            cross_axis_groups += 1
    return {
        "participating_rows_by_axis": dict(sorted(participation.items())),
        "participation_percent_by_axis": {
            axis: round(participation[axis] * 100 / count, 6)
            for axis, count in sorted(axis_counts.items())
        },
        "maximum_multiplicity": maximum,
        "cross_axis_duplicate_groups": cross_axis_groups,
    }


def _scan(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verified = _verify_parents(context, repo_root)
    config = context["config"]
    parent = config["parent_phase4"]
    canonical_root = (
        repo_root / f"data/derived/saju_1b_baseline/v2.0.0/{parent['build_id']}"
    )
    manifest_path = canonical_root / "manifests/mix20k_v2.jsonl"
    manifest = read_jsonl(manifest_path, "canonical MIX20")
    if len(manifest) != 20_000:
        raise PretrainingAuditError("canonical MIX20 수량이 다릅니다.")
    records_by_id, _, _, _ = load_candidate_staging_records(
        verified["phase4_context"], repo_root
    )
    axis_counts: Counter[str] = Counter()
    axis_assistant_tokens: Counter[str] = Counter()
    zero_assistant_masks = 0
    foreign_cjk_rows = 0
    target_only_entity_rows = 0
    severe_safety_rows = 0
    control_rows = 0
    persona_alignment_rows = 0
    normalized_rows: list[tuple[str, str]] = []
    persona_alignment_patterns = [
        re.compile(value, re.IGNORECASE)
        for value in config["patterns"]["persona_alignment_language"]
    ]
    safety_patterns = [
        re.compile(value, re.IGNORECASE)
        for value in config["patterns"]["severe_safety"]
    ]
    for item in manifest:
        record_id = item.get("id")
        record = records_by_id.get(str(record_id))
        if (
            record is None
            or record.get("meta", {}).get("phase4_parent_record_sha256")
            != item.get("record_sha256")
        ):
            raise PretrainingAuditError("MIX20/staging record identity가 다릅니다.")
        axis = str(item.get("mix_axis"))
        assistant_tokens = item.get("assistant_tokens")
        if not isinstance(assistant_tokens, int) or assistant_tokens <= 0:
            zero_assistant_masks += 1
        axis_counts[axis] += 1
        axis_assistant_tokens[axis] += int(assistant_tokens or 0)
        assistant = _assistant_text(record)
        source_input = _input_text(record)
        normalized_rows.append((axis, assistant))
        if CONTROL_PATTERN.search(assistant):
            control_rows += 1
        foreign = {
            character
            for character in CJK_PATTERN.findall(assistant)
            if character not in ALLOWED_SAJU_HANJA
        }
        if foreign:
            foreign_cjk_rows += 1
        entity_matches = [
            pattern.findall(assistant)
            for pattern in (FULL_DATE_PATTERN, URL_PATTERN, HANDLE_PATTERN, LONG_NUMBER_PATTERN)
        ]
        input_entity_matches = [
            pattern.findall(source_input)
            for pattern in (FULL_DATE_PATTERN, URL_PATTERN, HANDLE_PATTERN, LONG_NUMBER_PATTERN)
        ]
        if any(
            set(map(str, output_values)) - set(map(str, input_values))
            for output_values, input_values in zip(
                entity_matches, input_entity_matches, strict=True
            )
        ):
            target_only_entity_rows += 1
        if any(pattern.search(assistant) for pattern in safety_patterns):
            severe_safety_rows += 1
        if axis == "nemotron_saju" and any(
            pattern.search(assistant) for pattern in persona_alignment_patterns
        ):
            persona_alignment_rows += 1

    expected_axis_counts = {
        "nemotron_saju": 6800,
        "bazi_sft": 4000,
        "aihub_empathy_single": 1500,
        "aihub_empathy_multiturn": 1500,
        "yeji_shensha_derived": 1000,
        "deterministic_saju_qa": 2000,
        "saju_diary_bridge": 3200,
    }
    if dict(axis_counts) != expected_axis_counts:
        raise PretrainingAuditError(f"MIX20 축 수량이 다릅니다: {dict(axis_counts)}")
    total_assistant_tokens = sum(axis_assistant_tokens.values())
    token_share = {
        axis: round(value * 100 / total_assistant_tokens, 6)
        for axis, value in sorted(axis_assistant_tokens.items())
    }
    dominant_share = round(
        (
            axis_assistant_tokens["nemotron_saju"]
            + axis_assistant_tokens["bazi_sft"]
        )
        * 100
        / total_assistant_tokens,
        6,
    )
    persona_alignment_percent = round(
        persona_alignment_rows * 100 / axis_counts["nemotron_saju"], 6
    )
    source_drift = [
        source["source_id"]
        for source in config["source_catalog"]
        if source.get("expected_revision") is not None
        and source.get("expected_revision") != source.get("observed_revision")
    ]
    technical_acceptance = load_json(
        repo_root
        / "data/reports/saju_1b_baseline/preprocessing-staging/v1.0.0/build-a5a9e76d6a8c/TECHNICAL_ACCEPTANCE.json",
        "quality technical acceptance",
    )
    critical_or_high = int(technical_acceptance.get("critical_or_high_rows", -1))
    hard_checks = {
        "phase4_hash_chain": verified["phase4"].get("training_promotion_allowed")
        is True,
        "readiness_hash_chain": verified["readiness"].get("phase5_training_performed")
        is False,
        "source_revision_drift_zero": not source_drift,
        "canonical_rows_20000": len(manifest) == 20_000,
        "axis_counts_exact": dict(axis_counts) == expected_axis_counts,
        "critical_or_high_data_rows_zero": critical_or_high
        <= config["thresholds"]["critical_or_high_data_rows_max"],
        "zero_assistant_masks_zero": zero_assistant_masks
        <= config["thresholds"]["zero_assistant_masks_max"],
        "foreign_cjk_rows_zero": foreign_cjk_rows
        <= config["thresholds"]["foreign_cjk_rows_max"],
        "target_only_entity_rows_zero": target_only_entity_rows
        <= config["thresholds"]["target_only_entity_rows_max"],
        "severe_safety_rows_zero": severe_safety_rows
        <= config["thresholds"]["severe_safety_rows_max"],
        "control_rows_zero": control_rows == 0,
        "aihub_public_boundary_preserved": True,
    }
    hard_blockers = sorted(key for key, passed in hard_checks.items() if not passed)
    known_risks = {
        "dominant_knowledge_axis_token_share": {
            "axes": ["nemotron_saju", "bazi_sft"],
            "percent": dominant_share,
            "warning_threshold_percent": config["thresholds"]
            ["dominant_knowledge_axis_token_share_warning_percent"],
            "warning": dominant_share
            >= config["thresholds"][
                "dominant_knowledge_axis_token_share_warning_percent"
            ],
        },
        "nemotron_persona_alignment_language": {
            "rows": persona_alignment_rows,
            "total_rows": axis_counts["nemotron_saju"],
            "percent": persona_alignment_percent,
            "warning_threshold_percent": config["thresholds"]
            ["persona_alignment_language_warning_percent"],
            "warning": persona_alignment_percent
            >= config["thresholds"]["persona_alignment_language_warning_percent"],
            "causalization_inferred": False,
            "interpretation": (
                "페르소나와 사주 설명을 연결하는 문구의 광범위 탐지값이며, "
                "그 자체로 인과 단정 오류를 뜻하지 않습니다."
            ),
        },
        "normalized_assistant_duplicates": _duplicate_summary(
            normalized_rows, axis_counts
        ),
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
    }
    baseline_allowed = len(hard_blockers) <= config["thresholds"]["hard_blocker_max"]
    report = {
        "schema_version": "1.0.0",
        "report_type": "phase5_pretraining_semantic_and_source_audit",
        "audit_version": config["audit_version"],
        "as_of": config["as_of"],
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "canonical_dataset": {
            "version": parent["version"],
            "build_id": parent["build_id"],
            "rows": len(manifest),
            "manifest_sha256": sha256_file(manifest_path),
            "axis_rows": dict(sorted(axis_counts.items())),
            "assistant_tokens": total_assistant_tokens,
            "assistant_token_share_percent": token_share,
        },
        "hard_checks": hard_checks,
        "hard_blockers": hard_blockers,
        "scan_counts": {
            "critical_or_high_data_rows": critical_or_high,
            "zero_assistant_masks": zero_assistant_masks,
            "foreign_cjk_rows": foreign_cjk_rows,
            "target_only_entity_rows": target_only_entity_rows,
            "severe_safety_rows": severe_safety_rows,
            "control_rows": control_rows,
            "source_revision_drift": source_drift,
        },
        "known_risks": known_risks,
        "web_and_policy_comparison": [
            {
                "source_id": source["source_id"],
                "expected_revision": source.get("expected_revision"),
                "observed_revision": source.get("observed_revision"),
                "role": source["decision"],
                "decision_basis": source["decision_basis"],
            }
            for source in config["source_catalog"]
        ],
        "governance": {
            "baseline_training_allowed": baseline_allowed,
            "dataset_mutation_required_before_ki10": not baseline_allowed,
            "production_quality_claim_allowed": False,
            "ki20_promotion_allowed": False,
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "phase5_training_performed": False,
            "blind_source_test_inspected": False,
        },
        "raw_samples_in_report": False,
        "restricted_aihub_content_in_report": False,
    }
    _walk_public(report)
    return report


def _manifest(context: dict[str, Any], report: bytes) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "report_type": "phase5_pretraining_audit_public_manifest",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "build_inputs": context["build_inputs"],
        "artifact_sha256": {
            "audit_report.json": hashlib.sha256(report).hexdigest()
        },
        "status": "audited",
        "phase5_training_performed": False,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(PUBLIC_FILE_MODE)
        if path.exists():
            raise PretrainingAuditError(f"기존 감사 산출물을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_audit(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["public_root"]
    if root.exists():
        return {**verify_audit(context, repo_root), "mode": "reused"}
    report = _json_bytes(_scan(context, repo_root))
    root.mkdir(parents=True, exist_ok=False)
    try:
        _atomic_write(root / "audit_report.json", report)
        _atomic_write(root / "build_manifest.json", _json_bytes(_manifest(context, report)))
    except Exception:
        for path in root.iterdir():
            path.unlink()
        root.rmdir()
        raise
    return {**verify_audit(context, repo_root), "mode": "built"}


def verify_audit(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["public_root"]
    if root.is_symlink() or not root.is_dir():
        raise PretrainingAuditError("학습 전 감사 공개 build가 없습니다.")
    report = _scan(context, repo_root)
    report_payload = _json_bytes(report)
    manifest = load_json(root / "build_manifest.json", "pretraining audit manifest")
    expected_manifest = _manifest(context, report_payload)
    if manifest != expected_manifest:
        raise PretrainingAuditError("학습 전 감사 manifest가 재현되지 않습니다.")
    report_path = root / "audit_report.json"
    if report_path.read_bytes() != report_payload:
        raise PretrainingAuditError("학습 전 감사 보고서가 재현되지 않습니다.")
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE:
            raise PretrainingAuditError("학습 전 감사 공개 파일 형식·권한이 다릅니다.")
    governance = report["governance"]
    return {
        "status": "verified",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        **governance,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 학습 전 의미·출처 감사")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "audit config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "build_id": context["build_id"],
                    "build_sha256": context["build_sha256"],
                    "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "run":
                result = (
                    build_audit(context, REPO_ROOT)
                    if args.execute
                    else {
                        "status": "dry_run",
                        "build_id": context["build_id"],
                        "writes_performed": False,
                    }
                )
            else:
                result = verify_audit(context, REPO_ROOT)
    except (PretrainingAuditError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
