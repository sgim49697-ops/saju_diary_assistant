# preprocess_tools.py - MIX20K용 24K staging build의 생성·검증·승인 경계를 관리한다.

from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.data.audit_tools import verify_source_bundle
from scripts.data.errors import Phase2AuditError
from scripts.data.preprocess_adapters import (
    QUESTION_TYPES,
    build_aihub_records,
    build_bazi_records,
    build_nemotron_records,
    build_yeji_records,
    sha256_json,
    stable_rank,
)
from scripts.data.source_tools import (
    load_config,
    resolve_repo_path,
    sha256_file,
    source_root,
    verify_sources,
    write_json_atomic,
)

STAGING_SCHEMA_VERSION = "1.0.0"
EXPECTED_AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
)
EXPECTED_SOURCES = {
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy",
    "yeji_bazi_rules",
}
REQUIRED_RECORD_KEYS = {
    "id",
    "source",
    "mix_axis",
    "source_variant",
    "source_revision",
    "license_expression",
    "usage_class",
    "provenance_status",
    "attribution_ids",
    "transformation_chain",
    "domain",
    "task",
    "messages",
    "label",
    "quality_flags",
    "meta",
}
PRIVATE_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
PRIVATE_DIR_MODE = stat.S_IRWXU
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z]{2,}")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _assert_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256_file(path) != expected:
        raise Phase2AuditError(f"{label} SHA-256이 고정값과 다릅니다: {path}")


def load_staging_config(path: Path) -> dict[str, Any]:
    config = _load_json(path, "staging 설정")
    if config.get("schema_version") != STAGING_SCHEMA_VERSION:
        raise Phase2AuditError("지원하지 않는 staging schema_version입니다.")
    if config.get("dataset_name") != "saju_1b_baseline":
        raise Phase2AuditError("staging dataset_name이 정본과 다릅니다.")
    if config.get("staging_version") != "v0.1.0" or config.get("seed") != 42:
        raise Phase2AuditError("첫 MIX20K staging은 v0.1.0/seed 42로 고정합니다.")
    if config.get("approval_status") != "staging_unapproved":
        raise Phase2AuditError("검토 전 staging은 staging_unapproved여야 합니다.")
    axes = config.get("axes")
    if not isinstance(axes, dict) or tuple(axes) != EXPECTED_AXES:
        raise Phase2AuditError("staging mix_axis 순서 또는 구성이 정본과 다릅니다.")
    expected = {
        "nemotron_saju": 13200,
        "bazi_sft": 6000,
        "aihub_empathy_single": 2400,
        "aihub_empathy_multiturn": 1200,
        "yeji_shensha_derived": 1200,
    }
    if {axis: int(value["staging_rows"]) for axis, value in axes.items()} != expected:
        raise Phase2AuditError("24K staging 수량 계약이 정본과 다릅니다.")
    if sum(expected.values()) != 24000:
        raise Phase2AuditError("staging 합계는 24,000행이어야 합니다.")
    variants = axes["nemotron_saju"].get("variants")
    if variants != {"v6": 2640, "v7": 10560}:
        raise Phase2AuditError("Nemotron v6/v7 staging 비율이 20:80이 아닙니다.")
    return config


def _validate_language_bank(
    bank: dict[str, Any], raw_rules: list[dict[str, Any]]
) -> dict[str, Any]:
    if (
        bank.get("schema_version") != STAGING_SCHEMA_VERSION
        or bank.get("bank_version") != "v1.0.0"
        or bank.get("review_status") != "accepted_after_deterministic_validation"
    ):
        raise Phase2AuditError("한국어 문구 은행 identity가 다릅니다.")
    content = bank.get("content")
    if not isinstance(content, dict) or set(content) != {"bazi", "yeji"}:
        raise Phase2AuditError("한국어 문구 은행 content 구조가 다릅니다.")
    bazi = content["bazi"]
    if set(bazi) != {
        "system_prompt",
        "safety_disclaimer",
        "questions",
        "rule_explanations",
    }:
        raise Phase2AuditError("BaZi 문구 은행 필드가 다릅니다.")
    if set(bazi["questions"]) != set(QUESTION_TYPES):
        raise Phase2AuditError("BaZi 질문 유형이 네 개와 다릅니다.")
    if set(bazi["rule_explanations"]) != {
        "day_master_strong",
        "day_master_weak",
        "dm_supported",
        "dominant_element",
        "missing_elements",
    }:
        raise Phase2AuditError("BaZi 규칙 문구 ID가 다릅니다.")
    yeji = content["yeji"]
    if not isinstance(yeji, list) or len(yeji) != 51:
        raise Phase2AuditError("YEJI 문구는 정확히 51개여야 합니다.")
    actual = [(item.get("rule_id"), item.get("name_ko")) for item in yeji]
    expected = [(int(item["id"]), item["name_ko"]) for item in raw_rules]
    if actual != expected:
        raise Phase2AuditError("YEJI 문구 ID·name_ko가 원천과 다릅니다.")
    banned = ("반드시", "틀림없이", "무조건", "수익 보장", "진단합니다", "투자하세요")
    all_text = json.dumps(content, ensure_ascii=False)
    if any(token in all_text for token in banned):
        raise Phase2AuditError("한국어 문구 은행에 금지한 단정 표현이 있습니다.")
    return content


def prepare_staging(
    repo_root: Path, config_path: Path, *, verify_raw: bool
) -> dict[str, Any]:
    config = load_staging_config(config_path)
    parents = config["parents"]
    source_config_path = resolve_repo_path(repo_root, parents["source_config"])
    source_config = load_config(source_config_path)
    source_bundle_path = resolve_repo_path(repo_root, parents["source_bundle"])
    bundle, _ = verify_source_bundle(repo_root, source_bundle_path)
    if bundle["source_build_sha256"] != parents["source_build_sha256"]:
        raise Phase2AuditError("staging parent source build SHA-256이 다릅니다.")
    audit_manifest_path = resolve_repo_path(repo_root, parents["audit_build_manifest"])
    audit_manifest = _load_json(audit_manifest_path, "audit build manifest")
    if audit_manifest.get("build_sha256") != parents["audit_build_sha256"]:
        raise Phase2AuditError("staging parent audit build SHA-256이 다릅니다.")
    audit_gate_path = resolve_repo_path(repo_root, parents["audit_gate"])
    audit_gate = _load_json(audit_gate_path, "audit gate")
    if audit_gate.get("blocking_finding_codes"):
        raise Phase2AuditError("차단 finding이 남은 audit build에서 staging을 만들 수 없습니다.")
    approval_path = audit_manifest_path.parent / "APPROVAL.json"
    audit_approval = _load_json(approval_path, "audit approval") if approval_path.is_file() else None
    audit_approved = bool(
        audit_approval
        and audit_approval.get("build_sha256") == parents["audit_build_sha256"]
        and audit_approval.get("approval_basis") == "explicit_user_instruction"
    )
    audit_policy_path = resolve_repo_path(repo_root, parents["audit_policy"])
    _assert_hash(
        audit_policy_path, parents["audit_policy_sha256"], "audit policy"
    )
    audit_policy = _load_json(audit_policy_path, "audit policy")
    correction_path = resolve_repo_path(repo_root, parents["correction_manifest"])
    _assert_hash(
        correction_path, parents["correction_manifest_sha256"], "correction manifest"
    )
    correction_manifest = _load_json(correction_path, "correction manifest")
    language_path = resolve_repo_path(repo_root, parents["language_bank"])
    _assert_hash(language_path, parents["language_bank_sha256"], "language bank")
    language_bank = _load_json(language_path, "language bank")
    yeji_root = source_root(source_config, repo_root, "yeji_bazi_rules")
    yeji_document = _load_json(
        yeji_root / "rules/shensha_51.json", "YEJI source rules"
    )
    language_content = _validate_language_bank(
        language_bank, yeji_document["shensha_list"]
    )
    if verify_raw:
        verify_sources(source_config, repo_root)

    code_paths = [
        Path(__file__),
        Path(__file__).with_name("preprocess_adapters.py"),
        Path(__file__).with_name("phase2b_preprocess.py"),
        Path(__file__).with_name("phase2b_review_web.py"),
        Path(__file__).with_name("source_tools.py"),
        Path(__file__).with_name("audit_tools.py"),
        Path(__file__).with_name("errors.py"),
        Path(__file__).with_name("preprocess_review_assets") / "index.html",
        Path(__file__).with_name("preprocess_review_assets") / "review.css",
        Path(__file__).with_name("preprocess_review_assets") / "review.js",
        repo_root / "requirements-data.txt",
    ]
    code_files = [
        {"path": path.relative_to(repo_root).as_posix(), "sha256": sha256_file(path)}
        for path in sorted(code_paths)
    ]
    inputs = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "dataset_name": config["dataset_name"],
        "staging_version": config["staging_version"],
        "seed": config["seed"],
        "config_sha256": sha256_file(config_path),
        "source_config_sha256": sha256_file(source_config_path),
        "source_build_sha256": bundle["source_build_sha256"],
        "audit_build_sha256": audit_manifest["build_sha256"],
        "audit_policy_sha256": sha256_file(audit_policy_path),
        "correction_manifest_sha256": sha256_file(correction_path),
        "language_bank_sha256": sha256_file(language_path),
        "code_sha256": sha256_json(code_files),
    }
    build_sha = sha256_json(inputs)
    build_id = f"build-{build_sha[:12]}"
    substitutions = {
        "dataset_name": config["dataset_name"],
        "staging_version": config["staging_version"],
    }
    private_root = resolve_repo_path(
        repo_root, str(config["outputs"]["private_root"]).format(**substitutions)
    )
    public_root = resolve_repo_path(
        repo_root, str(config["outputs"]["public_root"]).format(**substitutions)
    )
    return {
        "config": config,
        "config_path": config_path,
        "source_config": source_config,
        "source_config_path": source_config_path,
        "audit_manifest": audit_manifest,
        "audit_gate": audit_gate,
        "audit_approval": audit_approval,
        "audit_approved": audit_approved,
        "audit_policy": audit_policy,
        "correction_manifest": correction_manifest,
        "language_content": language_content,
        "identity": {**inputs, "build_sha256": build_sha, "build_id": build_id},
        "private_path": private_root / build_id,
        "public_path": public_root / build_id,
    }


def staging_plan(repo_root: Path, config_path: Path) -> dict[str, Any]:
    context = prepare_staging(repo_root, config_path, verify_raw=False)
    return {
        "mode": "plan",
        "dataset_name": context["config"]["dataset_name"],
        "staging_version": context["config"]["staging_version"],
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "approval_status": context["config"]["approval_status"],
        "audit_status": "approved" if context["audit_approved"] else context["audit_gate"]["status"],
        "promotion_allowed": False,
        "axis_targets": {
            axis: value["staging_rows"] for axis, value in context["config"]["axes"].items()
        },
        "total_target_rows": 24000,
        "private_path": context["private_path"].relative_to(repo_root).as_posix(),
        "public_path": context["public_path"].relative_to(repo_root).as_posix(),
        "writes_performed": False,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(PRIVATE_FILE_MODE)


def _length_stats(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0, "p90": 0, "p95": 0, "max": 0}
    ordered = sorted(values)

    def percentile(value: float) -> int:
        return ordered[round((len(ordered) - 1) * value)]

    return {
        "count": len(values),
        "mean": round(sum(values) / len(values), 6),
        "p50": percentile(0.5),
        "p90": percentile(0.9),
        "p95": percentile(0.95),
        "max": ordered[-1],
    }


def _validate_records(
    records_by_axis: dict[str, list[dict[str, Any]]], config: dict[str, Any]
) -> dict[str, Any]:
    expected = {
        axis: int(config["axes"][axis]["staging_rows"]) for axis in EXPECTED_AXES
    }
    actual = {axis: len(records_by_axis[axis]) for axis in EXPECTED_AXES}
    if actual != expected:
        raise Phase2AuditError(f"staging 행 수가 계약과 다릅니다: {actual}")
    ids: set[str] = set()
    messages: set[str] = set()
    axis_groups: dict[str, set[str]] = defaultdict(set)
    role_patterns: Counter[str] = Counter()
    lengths: dict[str, dict[str, Any]] = {}
    for axis, records in records_by_axis.items():
        input_lengths: list[int] = []
        assistant_lengths: list[int] = []
        for record in records:
            if set(record) != REQUIRED_RECORD_KEYS:
                raise Phase2AuditError(f"공통 레코드 키가 다릅니다: {axis}")
            if record["mix_axis"] != axis or record["source"] not in EXPECTED_SOURCES:
                raise Phase2AuditError(f"source/mix_axis identity가 다릅니다: {axis}")
            if not record["id"] or record["id"] in ids:
                raise Phase2AuditError("staging record id가 비었거나 중복됐습니다.")
            ids.add(record["id"])
            message_hash = record["meta"].get("message_sha256")
            if not message_hash or message_hash in messages:
                raise Phase2AuditError("staging messages exact duplicate가 있습니다.")
            messages.add(message_hash)
            source_group = record["meta"].get("source_group_id")
            leakage_group = record["meta"].get("leakage_group_id")
            if not source_group or not leakage_group or not record["meta"].get("raw_hash"):
                raise Phase2AuditError("staging 필수 provenance/group 필드가 없습니다.")
            axis_groups[axis].add(source_group)
            roles = ",".join(item.get("role", "") for item in record["messages"])
            if roles not in {"system,user,assistant", "system,user,assistant,user,assistant"}:
                raise Phase2AuditError(f"지원하지 않는 role 순서입니다: {roles}")
            role_patterns[roles] += 1
            if not record["quality_flags"].get("language_ok"):
                raise Phase2AuditError("한국어 검증에 실패한 staging record가 있습니다.")
            if any(
                ASCII_WORD_PATTERN.search(message["content"])
                for message in record["messages"]
            ):
                raise Phase2AuditError(
                    f"학습 messages에 영문 단어 잔여가 있습니다: {record['id']}"
                )
            input_lengths.append(int(record["meta"]["input_chars"]))
            assistant_lengths.append(int(record["meta"]["assistant_chars"]))
        lengths[axis] = {
            "input_chars": _length_stats(input_lengths),
            "assistant_chars": _length_stats(assistant_lengths),
        }
    aihub_overlap = axis_groups["aihub_empathy_single"] & axis_groups[
        "aihub_empathy_multiturn"
    ]
    if aihub_overlap:
        raise Phase2AuditError("AI Hub 단일턴·멀티턴 group이 겹칩니다.")
    bazi_group_sizes = Counter(
        record["meta"]["source_group_id"] for record in records_by_axis["bazi_sft"]
    )
    if set(bazi_group_sizes.values()) != {4}:
        raise Phase2AuditError("BaZi 후보는 synthetic group마다 네 질문이어야 합니다.")
    return {
        "row_counts": actual,
        "total_rows": sum(actual.values()),
        "unique_record_ids": len(ids),
        "unique_message_hashes": len(messages),
        "role_patterns": dict(sorted(role_patterns.items())),
        "aihub_cross_axis_group_overlap": 0,
        "bazi_complete_group_count": len(bazi_group_sizes),
        "length_stats": lengths,
    }


def _artifact_hashes(root: Path, relative_paths: list[str]) -> dict[str, str]:
    return {relative: sha256_file(root / relative) for relative in sorted(relative_paths)}


def _copy_reviewer_assets(source_root_path: Path, target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=False)
    names = ("index.html", "review.css", "review.js")
    for name in names:
        shutil.copyfile(source_root_path / name, target / name)
    return [f"reviewer-v1.0.0/{name}" for name in names]


def _select_review_ids(
    records_by_axis: dict[str, list[dict[str, Any]]], seed: int
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for axis, count in (("bazi_sft", 150), ("yeji_shensha_derived", 150)):
        ordered = sorted(
            records_by_axis[axis],
            key=lambda item: stable_rank(seed, "staging-review", axis, item["id"]),
        )
        result[axis] = [item["id"] for item in ordered[:count]]
    return result


def _build_into(
    repo_root: Path,
    context: dict[str, Any],
    private_tmp: Path,
    public_tmp: Path,
) -> dict[str, Any]:
    config = context["config"]
    seed = int(config["seed"])
    source_config = context["source_config"]
    adapter_reports: dict[str, Any] = {}

    nemotron, adapter_reports["nemotron_saju"] = build_nemotron_records(
        source_config=source_config,
        repo_root=repo_root,
        audit_policy=context["audit_policy"],
        target_by_variant={
            key: int(value)
            for key, value in config["axes"]["nemotron_saju"]["variants"].items()
        },
        seed=seed,
    )
    bazi, adapter_reports["bazi_sft"] = build_bazi_records(
        source_config=source_config,
        repo_root=repo_root,
        language_bank=context["language_content"]["bazi"],
        target_rows=int(config["axes"]["bazi_sft"]["staging_rows"]),
        seed=seed,
    )
    aihub, aihub_report = build_aihub_records(
        source_config=source_config,
        repo_root=repo_root,
        audit_policy=context["audit_policy"],
        single_target=int(config["axes"]["aihub_empathy_single"]["staging_rows"]),
        multiturn_target=int(
            config["axes"]["aihub_empathy_multiturn"]["staging_rows"]
        ),
        seed=seed,
    )
    adapter_reports["aihub_empathy"] = aihub_report
    yeji, adapter_reports["yeji_shensha_derived"] = build_yeji_records(
        source_config=source_config,
        repo_root=repo_root,
        correction_manifest=context["correction_manifest"],
        language_bank=context["language_content"]["yeji"],
        target_rows=int(config["axes"]["yeji_shensha_derived"]["staging_rows"]),
        seed=seed,
    )
    records_by_axis = {
        "nemotron_saju": nemotron,
        "bazi_sft": bazi,
        "aihub_empathy_single": [
            item for item in aihub if item["mix_axis"] == "aihub_empathy_single"
        ],
        "aihub_empathy_multiturn": [
            item
            for item in aihub
            if item["mix_axis"] == "aihub_empathy_multiturn"
        ],
        "yeji_shensha_derived": yeji,
    }
    validation = _validate_records(records_by_axis, config)
    review_ids = _select_review_ids(records_by_axis, seed)

    private_tmp.chmod(PRIVATE_DIR_MODE)
    private_artifacts: list[str] = []
    for axis in EXPECTED_AXES:
        relative = f"records/{axis}.jsonl"
        _write_jsonl(private_tmp / relative, records_by_axis[axis])
        private_artifacts.append(relative)
    candidate_rows = [
        {
            "id": record["id"],
            "mix_axis": axis,
            "candidate_rank": record["meta"]["candidate_rank"],
            "source_group_id": record["meta"]["source_group_id"],
            "leakage_group_id": record["meta"]["leakage_group_id"],
            "approval_status": "staging_unapproved",
        }
        for axis in EXPECTED_AXES
        for record in records_by_axis[axis]
    ]
    candidate_rows.sort(key=lambda item: (item["mix_axis"], item["candidate_rank"]))
    _write_jsonl(private_tmp / "candidate_order.jsonl", candidate_rows)
    private_artifacts.append("candidate_order.jsonl")
    review_payload = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "dataset_name": config["dataset_name"],
        "staging_version": config["staging_version"],
        "build_id": context["identity"]["build_id"],
        "reviewer_version": config["outputs"]["reviewer_version"],
        "counts": {key: len(value) for key, value in review_ids.items()},
        "record_ids": review_ids,
    }
    write_json_atomic(private_tmp / "review_selection.json", review_payload)
    (private_tmp / "review_selection.json").chmod(PRIVATE_FILE_MODE)
    private_artifacts.append("review_selection.json")
    private_hashes = _artifact_hashes(private_tmp, private_artifacts)
    private_manifest = {
        **context["identity"],
        "generated_at": utc_now(),
        "approval_status": "staging_unapproved",
        "promotion_allowed": False,
        "audit_approved": context["audit_approved"],
        "artifact_sha256": private_hashes,
        "row_counts": validation["row_counts"],
        "total_rows": validation["total_rows"],
        "review_selection_sha256": private_hashes["review_selection.json"],
    }
    write_json_atomic(private_tmp / "build_manifest.json", private_manifest)
    (private_tmp / "build_manifest.json").chmod(PRIVATE_FILE_MODE)

    public_tmp.mkdir(parents=True, exist_ok=False)
    aggregate = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "dataset_name": config["dataset_name"],
        "staging_version": config["staging_version"],
        "build_id": context["identity"]["build_id"],
        "approval_status": "staging_unapproved",
        "scope": config["scope"],
        "validation": validation,
        "adapter_reports": adapter_reports,
        "contains_raw_samples": False,
    }
    write_json_atomic(public_tmp / "aggregate.json", aggregate)
    gate = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "status": "human_review_required",
        "staging_ready": True,
        "approval_status": "staging_unapproved",
        "audit_status": "approved" if context["audit_approved"] else context["audit_gate"]["status"],
        "audit_approved": context["audit_approved"],
        "promotion_allowed": False,
        "promotion_blockers": (
            ([] if context["audit_approved"] else ["audit_not_approved"])
            + [
                "staging_human_review_not_completed",
                "phase4_tokenizer_validation_not_completed",
            ]
        ),
        "review_required": {"bazi_sft": 150, "yeji_shensha_derived": 150},
        "contains_raw_samples": False,
    }
    write_json_atomic(public_tmp / "gate.staging.json", gate)
    review_manifest = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "dataset_name": config["dataset_name"],
        "staging_version": config["staging_version"],
        "build_id": context["identity"]["build_id"],
        "reviewer_version": config["outputs"]["reviewer_version"],
        "review_counts": {key: len(value) for key, value in review_ids.items()},
        "data_delivery": "loopback_api_from_gitignored_staging",
        "contains_samples": False,
    }
    write_json_atomic(public_tmp / "review_manifest.json", review_manifest)
    reviewer_assets = _copy_reviewer_assets(
        repo_root / "scripts/data/preprocess_review_assets",
        public_tmp / config["outputs"]["reviewer_version"],
    )
    public_artifacts = [
        "aggregate.json",
        "gate.staging.json",
        "review_manifest.json",
        *reviewer_assets,
    ]
    public_hashes = _artifact_hashes(public_tmp, public_artifacts)
    public_manifest = {
        **context["identity"],
        "generated_at": private_manifest["generated_at"],
        "approval_status": "staging_unapproved",
        "promotion_allowed": False,
        "contains_raw_samples": False,
        "private_manifest_sha256": sha256_file(private_tmp / "build_manifest.json"),
        "artifact_sha256": public_hashes,
        "row_counts": validation["row_counts"],
        "total_rows": validation["total_rows"],
    }
    write_json_atomic(public_tmp / "build_manifest.json", public_manifest)
    return {
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "row_counts": validation["row_counts"],
        "total_rows": validation["total_rows"],
        "review_counts": review_manifest["review_counts"],
        "approval_status": "staging_unapproved",
        "promotion_allowed": False,
    }


def execute_staging_build(repo_root: Path, config_path: Path) -> dict[str, Any]:
    context = prepare_staging(repo_root, config_path, verify_raw=True)
    private_path = context["private_path"]
    public_path = context["public_path"]
    if private_path.exists() or public_path.exists():
        if private_path.is_dir() and public_path.is_dir():
            result = verify_staging(repo_root, config_path, context["identity"]["build_id"])
            return {**result, "mode": "build_reused", "writes_performed": False}
        raise Phase2AuditError("staging private/public build가 한쪽에만 존재합니다.")
    private_path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_tmp_path = Path(
        tempfile.mkdtemp(prefix=f".{private_path.name}-", dir=private_path.parent)
    )
    public_tmp_path = Path(
        tempfile.mkdtemp(prefix=f".{public_path.name}-", dir=public_path.parent)
    )
    public_tmp_path.rmdir()
    try:
        result = _build_into(repo_root, context, private_tmp_path, public_tmp_path)
        private_tmp_path.rename(private_path)
        try:
            public_tmp_path.rename(public_path)
        except Exception:
            private_path.rename(private_tmp_path)
            raise
    except Exception:
        shutil.rmtree(private_tmp_path, ignore_errors=True)
        shutil.rmtree(public_tmp_path, ignore_errors=True)
        raise
    verified = verify_staging(repo_root, config_path, result["build_id"])
    return {**verified, "mode": "build", "writes_performed": True}


def _verify_hash_map(root: Path, values: dict[str, str], label: str) -> None:
    for relative, expected in values.items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise Phase2AuditError(f"{label} artifact SHA-256이 다릅니다: {relative}")


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise Phase2AuditError(f"빈 JSONL 행이 있습니다: {path}:{line_number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase2AuditError(f"JSONL 행이 object가 아닙니다: {path}:{line_number}")
                records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Phase2AuditError):
            raise
        raise Phase2AuditError(f"staging JSONL을 읽을 수 없습니다: {path}") from exc
    return records


def verify_staging(
    repo_root: Path, config_path: Path, requested_build: str
) -> dict[str, Any]:
    context = prepare_staging(repo_root, config_path, verify_raw=False)
    expected_build = context["identity"]["build_id"]
    if requested_build != expected_build:
        raise Phase2AuditError(
            f"요청 build가 현재 입력 fingerprint와 다릅니다: {requested_build} != {expected_build}"
        )
    private_path = context["private_path"]
    public_path = context["public_path"]
    if not private_path.is_dir() or not public_path.is_dir():
        raise Phase2AuditError("staging private/public build가 모두 존재해야 합니다.")
    private_manifest = _load_json(private_path / "build_manifest.json", "private manifest")
    public_manifest = _load_json(public_path / "build_manifest.json", "public manifest")
    for manifest in (private_manifest, public_manifest):
        if (
            manifest.get("build_id") != expected_build
            or manifest.get("build_sha256") != context["identity"]["build_sha256"]
            or manifest.get("approval_status") != "staging_unapproved"
            or manifest.get("promotion_allowed") is not False
        ):
            raise Phase2AuditError("staging build manifest identity/Gate가 다릅니다.")
    _verify_hash_map(private_path, private_manifest["artifact_sha256"], "private")
    _verify_hash_map(public_path, public_manifest["artifact_sha256"], "public")
    if sha256_file(private_path / "build_manifest.json") != public_manifest.get(
        "private_manifest_sha256"
    ):
        raise Phase2AuditError("public manifest의 private manifest hash가 다릅니다.")
    records_by_axis = {
        axis: _read_records(private_path / f"records/{axis}.jsonl")
        for axis in EXPECTED_AXES
    }
    validation = _validate_records(records_by_axis, context["config"])
    candidate_rows = _read_records(private_path / "candidate_order.jsonl")
    if len(candidate_rows) != validation["total_rows"]:
        raise Phase2AuditError("candidate_order 행 수가 staging 레코드 수와 다릅니다.")
    candidate_ids = {item["id"] for item in candidate_rows}
    record_ids = {
        item["id"] for records in records_by_axis.values() for item in records
    }
    if candidate_ids != record_ids:
        raise Phase2AuditError("candidate_order ID 집합이 staging 레코드와 다릅니다.")
    review = _load_json(private_path / "review_selection.json", "review selection")
    if review.get("counts") != {"bazi_sft": 150, "yeji_shensha_derived": 150}:
        raise Phase2AuditError("staging 검수 표본은 BaZi/YEJI 각 150건이어야 합니다.")
    selected = review.get("record_ids", {})
    for axis in ("bazi_sft", "yeji_shensha_derived"):
        axis_ids = {item["id"] for item in records_by_axis[axis]}
        values = selected.get(axis)
        if not isinstance(values, list) or len(values) != 150 or not set(values) <= axis_ids:
            raise Phase2AuditError(f"staging {axis} 검수 표본이 올바르지 않습니다.")
    public_aggregate = _load_json(public_path / "aggregate.json", "public aggregate")
    public_gate = _load_json(public_path / "gate.staging.json", "public gate")
    if public_aggregate.get("contains_raw_samples") is not False:
        raise Phase2AuditError("public aggregate에 raw sample 표시가 잘못됐습니다.")
    if public_gate.get("promotion_allowed") is not False:
        raise Phase2AuditError("검토 전 staging promotion Gate가 열려 있습니다.")
    approval_path = public_path / "APPROVAL.json"
    owner_accepted = approval_path.is_file()
    if owner_accepted:
        approval = _load_json(approval_path, "staging approval")
        if (
            approval.get("build_sha256") != context["identity"]["build_sha256"]
            or approval.get("approval_basis") != "explicit_owner_blanket_risk_acceptance"
            or approval.get("domain_item_review_performed") is not False
            or approval.get("accepted_review_units") != 300
        ):
            raise Phase2AuditError("staging owner risk acceptance identity가 다릅니다.")
        decisions_path = private_path / "review_decisions.jsonl"
        decisions = _read_records(decisions_path)
        if (
            len(decisions) != 300
            or {item.get("id") for item in decisions}
            != {item for values in selected.values() for item in values}
            or any(
                item.get("decision") != "accept"
                or item.get("review_mode") != "owner_blanket_risk_acceptance"
                or item.get("domain_item_review_performed") is not False
                for item in decisions
            )
        ):
            raise Phase2AuditError("staging 일괄 위험 수용 decision 원장이 다릅니다.")
    return {
        "dataset_name": context["config"]["dataset_name"],
        "staging_version": context["config"]["staging_version"],
        "build_id": expected_build,
        "build_sha256": context["identity"]["build_sha256"],
        "status": (
            "verified_owner_risk_accepted" if owner_accepted else "verified_staging_unapproved"
        ),
        "approval_status": (
            "owner_risk_accepted" if owner_accepted else "staging_unapproved"
        ),
        "owner_risk_accepted": owner_accepted,
        "promotion_allowed": False,
        "row_counts": validation["row_counts"],
        "total_rows": validation["total_rows"],
        "review_counts": review["counts"],
        "private_path": private_path.relative_to(repo_root).as_posix(),
        "public_path": public_path.relative_to(repo_root).as_posix(),
    }


def record_owner_risk_acceptance(
    repo_root: Path,
    config_path: Path,
    requested_build: str,
    *,
    confirm_owner_risk_acceptance: bool,
) -> dict[str, Any]:
    if not confirm_owner_risk_acceptance:
        raise Phase2AuditError("일괄 위험 수용에는 명시적 확인 flag가 필요합니다.")
    verified = verify_staging(repo_root, config_path, requested_build)
    private_path = repo_root / verified["private_path"]
    public_path = repo_root / verified["public_path"]
    approval_path = public_path / "APPROVAL.json"
    if approval_path.is_file():
        return {
            **verify_staging(repo_root, config_path, requested_build),
            "mode": "owner_risk_acceptance_reused",
            "writes_performed": False,
        }
    selection = _load_json(private_path / "review_selection.json", "review selection")
    selected_ids = [
        item
        for axis in ("bazi_sft", "yeji_shensha_derived")
        for item in selection["record_ids"][axis]
    ]
    if len(selected_ids) != 300 or len(set(selected_ids)) != 300:
        raise Phase2AuditError("일괄 위험 수용 대상은 정확히 300개여야 합니다.")
    recorded_at = utc_now()
    decisions = []
    for record_id in selected_ids:
        value = {
            "schema_version": STAGING_SCHEMA_VERSION,
            "id": record_id,
            "decision": "accept",
            "review_mode": "owner_blanket_risk_acceptance",
            "domain_item_review_performed": False,
            "basis": "explicit_user_instruction_after_automated_validation",
            "recorded_at": recorded_at,
        }
        value["decision_id"] = sha256_json(value)[:24]
        decisions.append(value)
    decisions_path = private_path / "review_decisions.jsonl"
    if decisions_path.exists():
        raise Phase2AuditError("기존 staging review decision 원장이 있어 일괄 수용할 수 없습니다.")
    _write_jsonl(decisions_path, decisions)
    acceptance = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "dataset_name": verified["dataset_name"],
        "staging_version": verified["staging_version"],
        "build_id": verified["build_id"],
        "build_sha256": verified["build_sha256"],
        "approval_basis": "explicit_owner_blanket_risk_acceptance",
        "accepted_review_units": 300,
        "decision_counts": {"accept": 300},
        "domain_item_review_performed": False,
        "quality_certification_claimed": False,
        "automated_validation_passed": True,
        "approved_for": "phase4_preflight_only",
        "training_promotion_allowed": False,
        "accepted_at": recorded_at,
    }
    gate = {
        "schema_version": STAGING_SCHEMA_VERSION,
        "status": "owner_risk_accepted_for_phase4_preflight",
        "build_id": verified["build_id"],
        "content_review_method": "owner_blanket_risk_acceptance",
        "domain_item_review_performed": False,
        "accepted_review_units": 300,
        "promotion_allowed": False,
        "remaining_blockers": ["phase4_tokenizer_and_model_preflight_not_completed"],
        "contains_raw_samples": False,
    }
    for path, value in (
        (public_path / "REVIEW_ACCEPTANCE.json", acceptance),
        (public_path / "gate.accepted.json", gate),
        (approval_path, acceptance),
    ):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")

    registry_path = config_path.parent / "registry.json"
    registry = _load_json(registry_path, "data version registry")
    entry = {
        "version": verified["staging_version"],
        "build_id": verified["build_id"],
        "build_sha256": verified["build_sha256"],
        "status": "owner_risk_accepted_for_phase4_preflight",
        "approval_basis": "explicit_owner_blanket_risk_acceptance",
        "domain_item_review_performed": False,
    }
    builds = registry.setdefault("staging_builds", [])
    builds.append(entry)
    registry["approved_staging"] = entry
    write_json_atomic(registry_path, registry)
    return {
        **verify_staging(repo_root, config_path, requested_build),
        "mode": "owner_risk_acceptance",
        "writes_performed": True,
    }
