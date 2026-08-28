# phase2b_verify_history.py - 승인된 과거 Phase 2B staging을 당시 Git 코드와 재검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.archive_safety import validate_relative_archive_path
from scripts.data.errors import Phase1Error, Phase2AuditError
from scripts.data.source_tools import sha256_file

DATASET_NAME = "saju_1b_baseline"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{7,40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z]{2,}")
EXPECTED_AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
)
AXIS_SOURCES = {
    "nemotron_saju": "nemotron_saju",
    "bazi_sft": "bazi_sft",
    "aihub_empathy_single": "aihub_empathy",
    "aihub_empathy_multiturn": "aihub_empathy",
    "yeji_shensha_derived": "yeji_bazi_rules",
}
AXIS_TASKS = {
    "nemotron_saju": "structured_saju_reading",
    "bazi_sft": "grounded_rule_reading",
    "aihub_empathy_single": "empathic_response",
    "aihub_empathy_multiturn": "natural_multiturn_dialogue",
    "yeji_shensha_derived": "shensha_rule_qa",
}
HISTORICAL_CODE_PATHS = (
    "scripts/data/preprocess_tools.py",
    "scripts/data/preprocess_adapters.py",
    "scripts/data/phase2b_preprocess.py",
    "scripts/data/phase2b_review_web.py",
    "scripts/data/source_tools.py",
    "scripts/data/audit_tools.py",
    "scripts/data/errors.py",
    "scripts/data/preprocess_review_assets/index.html",
    "scripts/data/preprocess_review_assets/review.css",
    "scripts/data/preprocess_review_assets/review.js",
    "requirements-data.txt",
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase2AuditError(
                        f"{label}에 빈 JSONL 행이 있습니다: {line_number}"
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError(f"line {line_number}")
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase2AuditError(f"{label} JSONL을 읽을 수 없습니다: {path}") from exc
    return values


def _git_blob(repo_root: Path, commit: str, relative: str) -> bytes:
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise Phase2AuditError("과거 구현 commit은 7~40자 16진 Git SHA여야 합니다.")
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase2AuditError(f"과거 구현 파일을 Git에서 읽을 수 없습니다: {relative}") from exc
    return result.stdout


def _json_blob(repo_root: Path, commit: str, relative: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_git_blob(repo_root, commit, relative))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"과거 {label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"과거 {label} 최상위 값은 object여야 합니다.")
    return value


def _historical_code_sha256(repo_root: Path, commit: str) -> str:
    entries = []
    for relative in sorted(HISTORICAL_CODE_PATHS):
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(
                    _git_blob(repo_root, commit, relative)
                ).hexdigest(),
            }
        )
    return _sha256_json(entries)


def _verify_hash_map(root: Path, values: Any, label: str) -> None:
    if not isinstance(values, dict) or not values:
        raise Phase2AuditError(f"{label} artifact hash map이 비어 있습니다.")
    for relative, expected in values.items():
        if not isinstance(relative, str) or SHA256_PATTERN.fullmatch(str(expected)) is None:
            raise Phase2AuditError(f"{label} artifact hash metadata가 올바르지 않습니다.")
        try:
            validated = validate_relative_archive_path(relative)
        except Phase1Error as exc:
            raise Phase2AuditError(f"{label} artifact 경로가 올바르지 않습니다.") from exc
        path = root.joinpath(*validated.parts)
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or sha256_file(path) != expected
        ):
            raise Phase2AuditError(f"{label} artifact SHA-256이 다릅니다: {relative}")


def _verify_parent_inputs(
    repo_root: Path,
    commit: str,
    config: dict[str, Any],
    private_manifest: dict[str, Any],
) -> None:
    parents = config["parents"]
    bundle = _json_blob(repo_root, commit, parents["source_bundle"], "source bundle")
    source_payload = {
        "dataset_name": bundle.get("dataset_name"),
        "schema_version": bundle.get("schema_version"),
        "sources": bundle.get("sources"),
        "version": bundle.get("version"),
    }
    if (
        _sha256_json(source_payload) != bundle.get("source_build_sha256")
        or bundle.get("source_build_sha256") != private_manifest.get("source_build_sha256")
    ):
        raise Phase2AuditError("과거 staging source bundle fingerprint가 다릅니다.")
    for source in bundle["sources"]:
        manifest_path = repo_root / source["manifest_path"]
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or not manifest_path.resolve().is_relative_to(repo_root.resolve())
            or sha256_file(manifest_path) != source["manifest_sha256"]
        ):
            raise Phase2AuditError(
                f"현재 source manifest가 staging 부모와 다릅니다: {source['source']}"
            )

    audit_manifest_path = repo_root / parents["audit_build_manifest"]
    if (
        audit_manifest_path.is_symlink()
        or not audit_manifest_path.is_file()
        or not audit_manifest_path.resolve().is_relative_to(repo_root.resolve())
    ):
        raise Phase2AuditError("staging audit 부모 manifest 경로가 올바르지 않습니다.")
    audit_manifest = _load_json(audit_manifest_path, "audit build manifest")
    if audit_manifest.get("build_sha256") != private_manifest.get("audit_build_sha256"):
        raise Phase2AuditError("staging audit 부모 fingerprint가 다릅니다.")
    for path_key, manifest_key in (
        ("audit_policy", "audit_policy_sha256"),
        ("correction_manifest", "correction_manifest_sha256"),
        ("language_bank", "language_bank_sha256"),
    ):
        path = repo_root / parents[path_key]
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(repo_root.resolve())
            or sha256_file(path) != private_manifest.get(manifest_key)
        ):
            raise Phase2AuditError(f"staging 부모 {path_key} SHA-256이 다릅니다.")


def _verify_records(
    private_root: Path,
    config: dict[str, Any],
    source_contracts: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    expected_counts = {
        axis: int(config["axes"][axis]["staging_rows"]) for axis in EXPECTED_AXES
    }
    records_by_axis = {
        axis: _read_jsonl(private_root / f"records/{axis}.jsonl", axis)
        for axis in EXPECTED_AXES
    }
    actual_counts = {axis: len(records) for axis, records in records_by_axis.items()}
    if actual_counts != expected_counts:
        raise Phase2AuditError(f"과거 staging 행 수가 계약과 다릅니다: {actual_counts}")

    ids: set[str] = set()
    message_hashes: set[str] = set()
    axis_source_groups: dict[str, set[str]] = defaultdict(set)
    leakage_axes: dict[str, set[str]] = defaultdict(set)
    bazi_groups: Counter[str] = Counter()
    ranks_by_axis: dict[str, Counter[str]] = defaultdict(Counter)
    roles: Counter[str] = Counter()
    for axis, records in records_by_axis.items():
        for record in records:
            record_id = record.get("id")
            meta = record.get("meta")
            messages = record.get("messages")
            if (
                not isinstance(record_id, str)
                or not record_id
                or record_id in ids
                or record.get("mix_axis") != axis
                or record.get("source") != AXIS_SOURCES[axis]
                or not isinstance(meta, dict)
                or not isinstance(messages, list)
                or not messages
            ):
                raise Phase2AuditError(f"과거 staging record identity가 다릅니다: {axis}")
            source_contract = source_contracts[AXIS_SOURCES[axis]]
            attribution_ids = record.get("attribution_ids")
            transformation_chain = record.get("transformation_chain")
            if (
                record.get("source_revision")
                != source_contract.get("revision", source_contract.get("release"))
                or record.get("license_expression")
                != source_contract.get("license_expression")
                or record.get("usage_class") != source_contract.get("usage_class")
                or record.get("provenance_status") != "verified"
                or record.get("task") != AXIS_TASKS[axis]
                or not isinstance(attribution_ids, list)
                or not attribution_ids
                or any(
                    not isinstance(value, str) or not value
                    for value in attribution_ids
                )
                or not isinstance(transformation_chain, list)
                or not transformation_chain
                or any(
                    not isinstance(value, str) or not value
                    for value in transformation_chain
                )
            ):
                raise Phase2AuditError("과거 staging 라이선스·provenance 계약이 다릅니다.")
            ids.add(record_id)
            message_hash = meta.get("message_sha256")
            if (
                not isinstance(message_hash, str)
                or message_hash != _sha256_json(messages)
                or message_hash in message_hashes
            ):
                raise Phase2AuditError("과거 staging message hash가 비었거나 중복·변조됐습니다.")
            message_hashes.add(message_hash)
            rank = meta.get("candidate_rank")
            source_group = meta.get("source_group_id")
            leakage_group = meta.get("leakage_group_id")
            if (
                SHA256_PATTERN.fullmatch(str(rank)) is None
                or not isinstance(source_group, str)
                or not source_group
                or not isinstance(leakage_group, str)
                or not leakage_group
                or SHA256_PATTERN.fullmatch(str(meta.get("raw_hash"))) is None
            ):
                raise Phase2AuditError("과거 staging provenance·group·rank가 올바르지 않습니다.")
            ranks_by_axis[axis][rank] += 1
            axis_source_groups[axis].add(source_group)
            leakage_axes[leakage_group].add(axis)
            if axis == "bazi_sft":
                bazi_groups[source_group] += 1
            role_pattern = ",".join(str(message.get("role")) for message in messages)
            if role_pattern not in {
                "system,user,assistant",
                "system,user,assistant,user,assistant",
            }:
                raise Phase2AuditError(f"과거 staging role 순서가 잘못됐습니다: {role_pattern}")
            roles[role_pattern] += 1
            for message in messages:
                content = message.get("content")
                if not isinstance(content, str) or not content:
                    raise Phase2AuditError("과거 staging message content가 비어 있습니다.")
                if ASCII_WORD_PATTERN.search(content):
                    raise Phase2AuditError("과거 staging message에 영문 단어가 남아 있습니다.")
    if axis_source_groups["aihub_empathy_single"] & axis_source_groups[
        "aihub_empathy_multiturn"
    ]:
        raise Phase2AuditError("과거 staging AI Hub 축 간 talk group이 겹칩니다.")
    if set(bazi_groups.values()) != {4}:
        raise Phase2AuditError("과거 staging BaZi group이 질문 4종으로 완결되지 않았습니다.")
    if set(ranks_by_axis["bazi_sft"].values()) != {4} or any(
        set(ranks_by_axis[axis].values()) != {1}
        for axis in EXPECTED_AXES
        if axis != "bazi_sft"
    ):
        raise Phase2AuditError("과거 staging candidate rank 묶음 계약이 다릅니다.")
    return records_by_axis, {
        "row_counts": actual_counts,
        "total_rows": len(ids),
        "unique_record_ids": len(ids),
        "unique_message_hashes": len(message_hashes),
        "role_patterns": dict(sorted(roles.items())),
        "cross_axis_leakage_group_count": sum(
            len(axes) > 1 for axes in leakage_axes.values()
        ),
        "aihub_cross_axis_group_overlap": 0,
        "bazi_complete_group_count": len(bazi_groups),
    }


def verify_historical_staging(
    repo_root: Path,
    *,
    staging_version: str,
    build_id: str,
    implementation_commit: str | None,
) -> dict[str, Any]:
    registry_path = repo_root / f"configs/data_versions/{DATASET_NAME}/registry.json"
    registry = _load_json(registry_path, "registry")
    entry = next(
        (
            item
            for item in registry.get("staging_builds", [])
            if item.get("version") == staging_version and item.get("build_id") == build_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise Phase2AuditError("registry에 요청한 staging build가 없습니다.")
    commit = implementation_commit or entry.get("implementation_commit")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        raise Phase2AuditError("staging implementation_commit이 없거나 올바르지 않습니다.")

    private_root = repo_root / "data/staging" / DATASET_NAME / staging_version / build_id
    public_root = (
        repo_root
        / "data/reports"
        / DATASET_NAME
        / "preprocessing-staging"
        / staging_version
        / build_id
    )
    if (
        private_root.is_symlink()
        or public_root.is_symlink()
        or not private_root.is_dir()
        or not public_root.is_dir()
    ):
        raise Phase2AuditError("과거 staging private/public build 경로가 없습니다.")
    private_manifest_path = private_root / "build_manifest.json"
    public_manifest_path = public_root / "build_manifest.json"
    if private_manifest_path.is_symlink() or public_manifest_path.is_symlink():
        raise Phase2AuditError("과거 staging manifest에 symlink를 허용하지 않습니다.")
    private_manifest = _load_json(private_manifest_path, "private manifest")
    public_manifest = _load_json(public_manifest_path, "public manifest")

    config_relative = (
        f"configs/data_versions/{DATASET_NAME}/preprocessing-staging-{staging_version}.json"
    )
    config_blob = _git_blob(repo_root, commit, config_relative)
    config = json.loads(config_blob)
    source_config_relative = config["parents"]["source_config"]
    source_config_blob = _git_blob(repo_root, commit, source_config_relative)
    source_config = json.loads(source_config_blob)
    if not isinstance(source_config, dict) or not isinstance(
        source_config.get("sources"), dict
    ):
        raise Phase2AuditError("과거 staging source config가 올바르지 않습니다.")
    code_sha256 = _historical_code_sha256(repo_root, commit)
    identity = {
        "schema_version": private_manifest.get("schema_version"),
        "dataset_name": DATASET_NAME,
        "staging_version": staging_version,
        "seed": config.get("seed"),
        "config_sha256": hashlib.sha256(config_blob).hexdigest(),
        "source_config_sha256": hashlib.sha256(source_config_blob).hexdigest(),
        "source_build_sha256": private_manifest.get("source_build_sha256"),
        "audit_build_sha256": private_manifest.get("audit_build_sha256"),
        "audit_policy_sha256": private_manifest.get("audit_policy_sha256"),
        "correction_manifest_sha256": private_manifest.get(
            "correction_manifest_sha256"
        ),
        "language_bank_sha256": private_manifest.get("language_bank_sha256"),
        "code_sha256": code_sha256,
    }
    expected_build_sha256 = _sha256_json(identity)
    if (
        expected_build_sha256 != private_manifest.get("build_sha256")
        or expected_build_sha256 != public_manifest.get("build_sha256")
        or build_id != f"build-{expected_build_sha256[:12]}"
        or code_sha256 != private_manifest.get("code_sha256")
        or any(
            manifest.get("dataset_name") != DATASET_NAME
            or manifest.get("staging_version") != staging_version
            or manifest.get("build_id") != build_id
            or manifest.get("promotion_allowed") is not False
            for manifest in (private_manifest, public_manifest)
        )
    ):
        raise Phase2AuditError("과거 staging build identity가 다릅니다.")

    _verify_parent_inputs(repo_root, commit, config, private_manifest)
    _verify_hash_map(private_root, private_manifest.get("artifact_sha256"), "private")
    _verify_hash_map(public_root, public_manifest.get("artifact_sha256"), "public")
    if sha256_file(private_root / "build_manifest.json") != public_manifest.get(
        "private_manifest_sha256"
    ):
        raise Phase2AuditError("public manifest의 private manifest hash가 다릅니다.")

    records_by_axis, record_validation = _verify_records(
        private_root, config, source_config["sources"]
    )
    candidate_rows = _read_jsonl(private_root / "candidate_order.jsonl", "candidate order")
    records_by_id = {
        record["id"]: record
        for records in records_by_axis.values()
        for record in records
    }
    if (
        len(candidate_rows) != record_validation["total_rows"]
        or {item.get("id") for item in candidate_rows} != set(records_by_id)
        or candidate_rows
        != sorted(
            candidate_rows,
            key=lambda item: (item.get("mix_axis"), item.get("candidate_rank")),
        )
        or any(
            item.get("mix_axis") != records_by_id[item["id"]]["mix_axis"]
            or item.get("candidate_rank")
            != records_by_id[item["id"]]["meta"]["candidate_rank"]
            or item.get("source_group_id")
            != records_by_id[item["id"]]["meta"]["source_group_id"]
            or item.get("leakage_group_id")
            != records_by_id[item["id"]]["meta"]["leakage_group_id"]
            or item.get("approval_status") != "staging_unapproved"
            for item in candidate_rows
        )
    ):
        raise Phase2AuditError("과거 candidate order가 staging record와 다릅니다.")

    selection = _load_json(private_root / "review_selection.json", "review selection")
    selected = selection.get("record_ids")
    if not isinstance(selected, dict) or selection.get("counts") != {
        "bazi_sft": 150,
        "yeji_shensha_derived": 150,
    }:
        raise Phase2AuditError("과거 staging review selection 수량이 다릅니다.")
    selected_ids = [
        record_id
        for axis in ("bazi_sft", "yeji_shensha_derived")
        for record_id in selected.get(axis, [])
    ]
    if (
        len(selected_ids) != 300
        or len(set(selected_ids)) != 300
        or any(record_id not in records_by_id for record_id in selected_ids)
    ):
        raise Phase2AuditError("과거 staging review selection ID가 다릅니다.")

    decisions_path = private_root / "review_decisions.jsonl"
    decisions = _read_jsonl(decisions_path, "review decisions")
    if (
        len(decisions) != 300
        or {item.get("id") for item in decisions} != set(selected_ids)
        or any(
            item.get("decision") != "accept"
            or item.get("review_mode") != "owner_blanket_risk_acceptance"
            or item.get("domain_item_review_performed") is not False
            or item.get("decision_id")
            != _sha256_json(
                {key: value for key, value in item.items() if key != "decision_id"}
            )[:24]
            for item in decisions
        )
    ):
        raise Phase2AuditError("과거 staging review decision 원장이 다릅니다.")

    approval_path = public_root / "APPROVAL.json"
    approval = _load_json(approval_path, "approval")
    acceptance = _load_json(public_root / "REVIEW_ACCEPTANCE.json", "review acceptance")
    accepted_gate = _load_json(public_root / "gate.accepted.json", "accepted gate")
    if (
        approval != acceptance
        or approval.get("build_sha256") != expected_build_sha256
        or approval.get("approval_basis")
        != "explicit_owner_blanket_risk_acceptance"
        or approval.get("accepted_review_units") != 300
        or approval.get("domain_item_review_performed") is not False
        or approval.get("quality_certification_claimed") is not False
        or approval.get("training_promotion_allowed") is not False
        or accepted_gate.get("promotion_allowed") is not False
    ):
        raise Phase2AuditError("과거 staging approval·Gate 계약이 다릅니다.")

    pinned_hashes = {
        "approval_manifest_sha256": approval_path,
        "review_acceptance_sha256": public_root / "REVIEW_ACCEPTANCE.json",
        "accepted_gate_sha256": public_root / "gate.accepted.json",
        "review_decisions_sha256": decisions_path,
        "private_manifest_sha256": private_root / "build_manifest.json",
        "public_manifest_sha256": public_root / "build_manifest.json",
    }
    for key, path in pinned_hashes.items():
        if path.is_symlink() or not path.is_file() or entry.get(key) != sha256_file(path):
            raise Phase2AuditError(f"registry staging {key}가 다릅니다.")
    approved_staging = registry.get("approved_staging")
    if not isinstance(approved_staging, dict) or any(
        approved_staging.get(key) != entry.get(key)
        for key in (
            "version",
            "build_id",
            "build_sha256",
            "implementation_commit",
            *pinned_hashes,
        )
    ):
        raise Phase2AuditError("registry approved_staging 포인터가 다릅니다.")

    if stat.S_IMODE(private_root.stat().st_mode) & 0o077:
        raise Phase2AuditError("과거 staging 비공개 디렉터리 권한이 너무 넓습니다.")
    for path in (
        private_root / "build_manifest.json",
        private_root / "candidate_order.jsonl",
        private_root / "review_selection.json",
        decisions_path,
        *(private_root / "records" / f"{axis}.jsonl" for axis in EXPECTED_AXES),
    ):
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise Phase2AuditError(f"과거 staging 비공개 파일 권한이 0600이 아닙니다: {path.name}")

    return {
        "dataset_name": DATASET_NAME,
        "staging_version": staging_version,
        "build_id": build_id,
        "build_sha256": expected_build_sha256,
        "implementation_commit": commit,
        "status": "historical_verified_owner_risk_accepted",
        "approval_status": "owner_risk_accepted",
        "owner_risk_accepted": True,
        "promotion_allowed": False,
        "domain_item_review_performed": False,
        "training_promotion_allowed": False,
        "row_counts": record_validation["row_counts"],
        "total_rows": record_validation["total_rows"],
        "review_counts": {"bazi_sft": 150, "yeji_shensha_derived": 150},
        "record_validation": record_validation,
        "review_decision_count": len(decisions),
        "private_path": private_root.relative_to(repo_root).as_posix(),
        "public_path": public_root.relative_to(repo_root).as_posix(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="과거 Phase 2B staging build와 당시 Git 구현을 검증한다."
    )
    parser.add_argument("--staging-version", required=True)
    parser.add_argument("--build", required=True)
    parser.add_argument("--implementation-commit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        result = verify_historical_staging(
            REPO_ROOT,
            staging_version=arguments.staging_version,
            build_id=arguments.build,
            implementation_commit=arguments.implementation_commit,
        )
    except Phase2AuditError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
