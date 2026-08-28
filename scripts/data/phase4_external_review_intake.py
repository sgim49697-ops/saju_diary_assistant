# phase4_external_review_intake.py - 외부 MIX20K 검수 제출본을 검증하고 불변 보고서로 수용한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.phase4_export_external_review import (
    PACKAGE_FILES,
    verify_archive,
)
from scripts.data.preprocess_adapters import NAME_PATTERN, PII_PATTERNS
from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_bytes_once,
)

SCHEMA_VERSION = "1.0.0"
REVIEW_VERSION = "1.0.0"
INTAKE_VERSION = "v1.0.0"
REVIEW_PATH_VERSION = "v1.0.0"
REPORT_TYPE = "phase4_external_review_intake"
REPORT_RELATIVE_ROOT = (
    Path("data/reports/saju_1b_baseline/external-review") / REVIEW_PATH_VERSION
)
SAMPLE_SALT = "saju-mix20k-gpt-review-v1|"
MAX_SUBMITTED_FILE_BYTES = 2 * 1024 * 1024
MAX_SUBMITTED_TOTAL_BYTES = 8 * 1024 * 1024
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
REVIEW_ID_PATTERN = re.compile(r"^MIX20K-\d{5}$")
FINDING_ID_PATTERN = re.compile(r"^AGGREGATE-[A-Z0-9-]+$")
SHA_LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")
FULL_BIRTHDATE_PATTERN = re.compile(r"(?:19|20)\d{2}년\s*\d{1,2}월\s*\d{1,2}일")
FORBIDDEN_SUBMISSION_PATTERNS = (
    re.compile(r"aihub-talk:[0-9a-f]{16,}"),
    re.compile(
        r'"(?:raw_hash|record_sha256|source_group_id|leakage_group_id|locator)"\s*:'
    ),
    re.compile(r"(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{20,}"),
)
ALLOWED_SAJU_HANJA = set(
    "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥木火土金水陰陽比肩劫財食神傷官偏正印殺"
)
SAJU_HANJA_PATTERN = re.compile(r"[\u3400-\u9fff]")

SUBMITTED_FILES = (
    "saju_mix20k_external_review_report.md",
    "saju_mix20k_external_review_summary.json",
    "saju_mix20k_external_findings.jsonl",
    "saju_mix20k_reviewed_ids.jsonl",
)
SUMMARY_FIELDS = {
    "review_version",
    "reviewed_at",
    "package_build_id",
    "outer_zip_sha256",
    "machine_scanned_rows",
    "withheld_aihub_rows",
    "semantic_reviewed_rows",
    "human_domain_expert_review_performed",
    "candidate_training_projection_mismatches",
    "internal_checksum_failures",
    "recommendation",
    "suitability",
    "source_rows",
    "assistant_token_share_percent",
    "key_counts",
    "finding_severity_counts",
    "finding_category_counts",
    "limitations",
}
FINDING_FIELDS = {
    "review_id",
    "severity",
    "category",
    "evidence",
    "reason",
    "recommended_action",
}
REVIEWED_ID_FIELDS = {
    "review_id",
    "source",
    "token_decile",
    "selection_reason",
}
ALLOWED_SEVERITIES = {"high", "medium", "low"}
ALLOWED_CATEGORIES = {
    "contamination",
    "training_fit",
    "factual_saju",
    "naturalness",
    "duplication",
    "schema",
}
EXTERNAL_SOURCES = ("bazi_sft", "nemotron_saju", "yeji_bazi_rules")

EXPECTED_PACKAGE = {
    "package_id": "external-review-72fb212dc90369be",
    "canonical_build_id": "build-a1a34616dd72",
    "archive_sha256": (
        "64d174b5a5c427439ee4ae15797e78eaffb6feb0cca184943a468ca81c968abd"
    ),
    "external_content_rows": 17_000,
    "withheld_aihub_rows": 3_000,
    "full_index_rows": 20_000,
    "exporter_source_sha256": (
        "58e79f1fc501529c0d354183225f2a28b8740477c46d5762848e6c749294aa4f"
    ),
}
EXPECTED_SUBMITTED_SHA256 = {
    "saju_mix20k_external_findings.jsonl": (
        "21d0883bfc1f57c905e4182a510d52af6a7ae3ca2bed6aa3283376dd301efa39"
    ),
    "saju_mix20k_external_review_report.md": (
        "fad6b120ae643925013db56f85d6d46a01aeb691ed2c834936f309756b399904"
    ),
    "saju_mix20k_external_review_summary.json": (
        "f2c81d43ba068b6278c2fc21584597b18339b6b5ded12529b05aa69fef3e6a8a"
    ),
    "saju_mix20k_reviewed_ids.jsonl": (
        "8f70f6130f19ecda1c47733317ba0abfd1614a22be68a3aa74782eed3f6df53b"
    ),
}
EXPECTED_REVIEW_SHA256 = (
    "6e3ad54184347d671435665502cec73f36eb51cc7661ca2bf355be3f14e8f3c3"
)
EXPECTED_REVIEW_ID = f"review-{EXPECTED_REVIEW_SHA256[:12]}"
EXPECTED_SOURCE_TOKEN_TOTALS = {
    "aihub_empathy": {"total_tokens": 232_884, "assistant_tokens": 41_625},
    "bazi_sft": {"total_tokens": 1_630_774, "assistant_tokens": 930_794},
    "nemotron_saju": {
        "total_tokens": 5_868_413,
        "assistant_tokens": 2_648_397,
    },
    "yeji_bazi_rules": {"total_tokens": 160_523, "assistant_tokens": 67_786},
}
AIHUB_CAPACITY_REPORT = Path(
    "data/reports/saju_1b_baseline/preprocessing-staging/v0.2.0/"
    "build-847088ee804d/aggregate.json"
)

BAZI_DISCLAIMER = (
    "이 해석은 전통 명리 관점의 문화·오락적 참고이며, 미래의 사건이나 "
    "건강·재정·관계 결과를 확정하지 않고 의료 진단이나 투자 조언을 대신하지 않습니다."
)
NEMOTRON_DISCLAIMER = (
    "이 내용은 전통 명리의 문화·오락적 참고 해석이며 실제 성향, 진로, "
    "건강 또는 재정 결과를 확정하지 않습니다."
)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _read_json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4Error(f"{label}을 UTF-8 JSON object로 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase4Error(f"{label} 최상위 값은 JSON object여야 합니다.")
    return value


def _read_jsonl(payload: bytes, label: str) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise Phase4Error(f"{label}이 UTF-8이 아닙니다.") from exc
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line:
            raise Phase4Error(f"{label} {line_number}행이 비었습니다.")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Phase4Error(
                f"{label} {line_number}행 JSON이 올바르지 않습니다."
            ) from exc
        if not isinstance(value, dict):
            raise Phase4Error(f"{label} {line_number}행은 JSON object여야 합니다.")
        values.append(value)
    return values


def _safe_directory(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.is_symlink() or not candidate.is_dir():
        raise Phase4Error(f"{label}은 symlink가 아닌 디렉터리여야 합니다.")
    resolved = candidate.resolve()
    if CONTROL_PATTERN.search(resolved.name):
        raise Phase4Error(f"{label} 이름에 제어문자가 있습니다.")
    return resolved


def _safe_regular_file(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        file_stat = candidate.lstat()
    except OSError as exc:
        raise Phase4Error(f"{label}을 찾을 수 없습니다.") from exc
    mode = file_stat.st_mode
    if not stat.S_ISREG(mode) or stat.S_ISLNK(mode):
        raise Phase4Error(f"{label}은 symlink가 아닌 일반 파일이어야 합니다.")
    if stat.S_IMODE(mode) & 0o133:
        raise Phase4Error(f"{label}에 실행 또는 제3자 쓰기 권한이 있습니다.")
    return candidate.resolve()


def _scan_submitted_text(name: str, payload: bytes) -> list[str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise Phase4Error(f"제출 파일이 UTF-8이 아닙니다: {name}") from exc
    if unicodedata.normalize("NFC", text) != text:
        raise Phase4Error(f"제출 파일이 NFC가 아닙니다: {name}")
    if CONTROL_PATTERN.search(text):
        raise Phase4Error(f"제출 파일에 금지 제어문자가 있습니다: {name}")
    if any(pattern.search(text) for pattern in PII_PATTERNS):
        raise Phase4Error(f"제출 파일에 개인정보 패턴이 있습니다: {name}")
    if any(pattern.search(text) for pattern in FORBIDDEN_SUBMISSION_PATTERNS):
        raise Phase4Error(f"제출 파일에 비공개 AI Hub 식별 정보가 있습니다: {name}")
    warnings: list[str] = []
    if not payload.endswith(b"\n"):
        warnings.append(f"missing_final_newline:{name}")
    return warnings


def load_submission(bundle_dir: Path) -> dict[str, Any]:
    root = _safe_directory(bundle_dir, "외부 검수 제출 폴더")
    entries = list(root.iterdir())
    if {entry.name for entry in entries} != set(SUBMITTED_FILES):
        raise Phase4Error("외부 검수 제출 파일 집합이 정확히 네 파일이 아닙니다.")
    payloads: dict[str, bytes] = {}
    warnings: list[str] = []
    total = 0
    for name in SUBMITTED_FILES:
        path = _safe_regular_file(root / name, f"제출 파일 {name}")
        size = path.stat().st_size
        if size <= 0 or size > MAX_SUBMITTED_FILE_BYTES:
            raise Phase4Error(f"제출 파일 크기가 허용 범위를 벗어납니다: {name}")
        total += size
        if total > MAX_SUBMITTED_TOTAL_BYTES:
            raise Phase4Error("외부 검수 제출 파일 총크기가 제한을 넘습니다.")
        payload = path.read_bytes()
        payloads[name] = payload
        warnings.extend(_scan_submitted_text(name, payload))
    return {
        "root": root,
        "payloads": payloads,
        "warnings": warnings,
        "sha256": {name: sha256_bytes(payloads[name]) for name in sorted(payloads)},
        "bytes": {name: len(payloads[name]) for name in sorted(payloads)},
    }


def _verify_sidecar(archive: Path, sidecar: Path | None, digest: str) -> Path:
    selected = sidecar or archive.with_name(f"{archive.name}.sha256")
    resolved = _safe_regular_file(selected, "외부 검수 ZIP sidecar")
    expected = f"{digest}  {archive.name}\n".encode()
    if resolved.read_bytes() != expected:
        raise Phase4Error("외부 검수 ZIP sidecar 내용이 실제 SHA-256과 다릅니다.")
    return resolved


def _read_archive_payloads(archive: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(archive) as opened:
            return {name: opened.read(name) for name in PACKAGE_FILES}
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise Phase4Error("검증된 외부 ZIP payload를 다시 읽지 못했습니다.") from exc


def load_package_context(
    archive_path: Path,
    repo_root: Path,
    *,
    sidecar_path: Path | None = None,
    enforce_production: bool = True,
) -> dict[str, Any]:
    archive = _safe_regular_file(archive_path, "외부 검수 ZIP")
    if archive.is_relative_to(repo_root.resolve()):
        raise Phase4Error("외부 검수 ZIP은 저장소 밖에 있어야 합니다.")
    result = verify_archive(archive)
    sidecar = _verify_sidecar(archive, sidecar_path, result["archive_sha256"])
    if sidecar.is_relative_to(repo_root.resolve()):
        raise Phase4Error("외부 검수 ZIP sidecar는 저장소 밖에 있어야 합니다.")
    payloads = _read_archive_payloads(archive)
    manifest = _read_json_object(payloads["PACKAGE_MANIFEST.json"], "package manifest")
    candidates = _read_jsonl(payloads["candidate_external_17k.jsonl"], "외부 candidate")
    training = _read_jsonl(
        payloads["training_external_17k.jsonl"], "외부 training projection"
    )
    index_rows = _read_jsonl(payloads["candidate_20k_index.jsonl"], "20K index")
    restricted = _read_json_object(
        payloads["aihub_3k_aggregate.json"], "AI Hub aggregate"
    )
    if enforce_production:
        for key, expected in EXPECTED_PACKAGE.items():
            actual = (
                manifest.get(key)
                if key == "exporter_source_sha256"
                else result.get(key)
            )
            if actual != expected:
                raise Phase4Error(f"외부 검수 package 고정 identity가 다릅니다: {key}")
        exporter_path = repo_root / "scripts/data/phase4_export_external_review.py"
        if sha256_file(exporter_path) != EXPECTED_PACKAGE["exporter_source_sha256"]:
            raise Phase4Error(
                "현재 exporter source가 제출 package identity와 다릅니다."
            )
    return {
        "archive": archive,
        "sidecar": sidecar,
        "result": result,
        "manifest": manifest,
        "candidates": candidates,
        "training": training,
        "index_rows": index_rows,
        "restricted": restricted,
    }


def _validate_summary(summary: dict[str, Any]) -> None:
    if set(summary) != SUMMARY_FIELDS:
        raise Phase4Error("외부 검수 summary 필드 집합이 다릅니다.")
    if (
        not isinstance(summary.get("review_version"), str)
        or not isinstance(summary.get("reviewed_at"), str)
        or not isinstance(summary.get("package_build_id"), str)
        or not isinstance(summary.get("outer_zip_sha256"), str)
        or summary.get("human_domain_expert_review_performed") is not False
        or not isinstance(summary.get("source_rows"), dict)
        or not isinstance(summary.get("assistant_token_share_percent"), dict)
        or not isinstance(summary.get("key_counts"), dict)
        or not isinstance(summary.get("finding_severity_counts"), dict)
        or not isinstance(summary.get("finding_category_counts"), dict)
        or not isinstance(summary.get("limitations"), list)
    ):
        raise Phase4Error("외부 검수 summary 값 형식이 올바르지 않습니다.")


def _validate_findings(
    findings: Sequence[dict[str, Any]], summary: dict[str, Any]
) -> None:
    ids: set[str] = set()
    severities: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    for position, finding in enumerate(findings, 1):
        review_id = finding.get("review_id")
        if (
            set(finding) != FINDING_FIELDS
            or not isinstance(review_id, str)
            or FINDING_ID_PATTERN.fullmatch(review_id) is None
            or review_id in ids
            or finding.get("severity") not in ALLOWED_SEVERITIES
            or finding.get("category") not in ALLOWED_CATEGORIES
            or not isinstance(finding.get("evidence"), dict)
            or not isinstance(finding.get("reason"), str)
            or not finding["reason"].strip()
            or not isinstance(finding.get("recommended_action"), str)
            or not finding["recommended_action"].strip()
        ):
            raise Phase4Error(f"외부 검수 finding {position} 형식이 올바르지 않습니다.")
        ids.add(review_id)
        severities[str(finding["severity"])] += 1
        categories[str(finding["category"])] += 1
    if dict(severities) != summary["finding_severity_counts"]:
        raise Phase4Error("finding severity 집계가 summary와 다릅니다.")
    if dict(categories) != summary["finding_category_counts"]:
        raise Phase4Error("finding category 집계가 summary와 다릅니다.")


def _validate_reviewed_rows(
    reviewed: Sequence[dict[str, Any]], summary: dict[str, Any]
) -> None:
    if len(reviewed) != summary.get("semantic_reviewed_rows"):
        raise Phase4Error("reviewed ID 수가 summary와 다릅니다.")
    ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    strata: Counter[tuple[str, int]] = Counter()
    for position, item in enumerate(reviewed, 1):
        review_id = item.get("review_id")
        source = item.get("source")
        decile = item.get("token_decile")
        if (
            set(item) != REVIEWED_ID_FIELDS
            or not isinstance(review_id, str)
            or REVIEW_ID_PATTERN.fullmatch(review_id) is None
            or review_id in ids
            or source not in EXTERNAL_SOURCES
            or isinstance(decile, bool)
            or not isinstance(decile, int)
            or not 1 <= decile <= 10
            or item.get("selection_reason") != "deterministic token-decile sample"
        ):
            raise Phase4Error(f"reviewed ID {position} 형식이 올바르지 않습니다.")
        ids.add(review_id)
        source_counts[str(source)] += 1
        strata[(str(source), decile)] += 1
    if len(reviewed) == 300 and (
        source_counts != Counter({source: 100 for source in EXTERNAL_SOURCES})
        or any(
            strata[(source, decile)] != 10
            for source in EXTERNAL_SOURCES
            for decile in range(1, 11)
        )
    ):
        raise Phase4Error("300건 표본의 source·token decile 층화 수량이 다릅니다.")


def deterministic_review_sample(
    candidates: Sequence[dict[str, Any]], *, per_decile: int = 10
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_ids: set[str] = set()
    for candidate in candidates:
        source = candidate.get("source")
        review_id = candidate.get("review_id")
        total_tokens = candidate.get("total_tokens")
        if (
            source not in EXTERNAL_SOURCES
            or not isinstance(review_id, str)
            or REVIEW_ID_PATTERN.fullmatch(review_id) is None
            or review_id in seen_ids
            or isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
        ):
            raise Phase4Error("결정적 표본 후보 필드가 올바르지 않습니다.")
        seen_ids.add(review_id)
        by_source[str(source)].append(candidate)
    if set(by_source) != set(EXTERNAL_SOURCES):
        raise Phase4Error("결정적 표본 후보 source 집합이 다릅니다.")
    for source in sorted(by_source):
        rows = sorted(
            by_source[source],
            key=lambda item: (item["total_tokens"], item["review_id"]),
        )
        if len(rows) % 10 or len(rows) // 10 < per_decile:
            raise Phase4Error(f"token decile 표본 후보가 부족합니다: {source}")
        bucket_size = len(rows) // 10
        for decile in range(1, 11):
            bucket = rows[(decile - 1) * bucket_size : decile * bucket_size]
            selected = sorted(
                bucket,
                key=lambda item: (
                    hashlib.sha256(
                        f"{SAMPLE_SALT}{item['review_id']}".encode()
                    ).hexdigest(),
                    item["review_id"],
                ),
            )[:per_decile]
            result.extend(
                {
                    "review_id": item["review_id"],
                    "source": source,
                    "token_decile": decile,
                    "selection_reason": "deterministic token-decile sample",
                }
                for item in selected
            )
    return result


def _message_text(row: Mapping[str, Any], role: str) -> str:
    messages = row.get("messages")
    if not isinstance(messages, list):
        raise Phase4Error("candidate messages가 list가 아닙니다.")
    values = [
        item.get("content")
        for item in messages
        if isinstance(item, dict) and item.get("role") == role
    ]
    if len(values) != 1 or not isinstance(values[0], str):
        raise Phase4Error(f"candidate에 {role} message가 정확히 하나가 아닙니다.")
    return values[0]


def _has_hangul_final_consonant(value: str) -> bool:
    if not value:
        return False
    codepoint = ord(value[-1])
    return 0xAC00 <= codepoint <= 0xD7A3 and (codepoint - 0xAC00) % 28 != 0


def _independent_metrics(package: dict[str, Any]) -> dict[str, Any]:
    candidates = package["candidates"]
    training = package["training"]
    index_rows = package["index_rows"]
    source_rows: Counter[str] = Counter()
    source_tokens: dict[str, Counter[str]] = defaultdict(Counter)
    for row in index_rows:
        source = str(row.get("source"))
        total_tokens = row.get("total_tokens")
        assistant_tokens = row.get("assistant_tokens")
        if (
            isinstance(total_tokens, bool)
            or not isinstance(total_tokens, int)
            or isinstance(assistant_tokens, bool)
            or not isinstance(assistant_tokens, int)
        ):
            raise Phase4Error("20K index token 집계 필드가 올바르지 않습니다.")
        source_rows[source] += 1
        source_tokens[source]["total_tokens"] += total_tokens
        source_tokens[source]["assistant_tokens"] += assistant_tokens
    assistant_total = sum(value["assistant_tokens"] for value in source_tokens.values())
    assistant_share = {
        source: round(value["assistant_tokens"] / assistant_total * 100, 3)
        for source, value in sorted(source_tokens.items())
    }
    projection_mismatches = sum(
        candidate.get("messages") != projected.get("messages")
        for candidate, projected in zip(candidates, training, strict=True)
    )

    bazi = [row for row in candidates if row.get("source") == "bazi_sft"]
    chart_counts: Counter[str] = Counter()
    for row in bazi:
        match = re.search(
            r"^사주 원국: (.+)$", _message_text(row, "user"), re.MULTILINE
        )
        if match is None:
            raise Phase4Error("bazi candidate에서 명식 문장을 찾지 못했습니다.")
        chart_counts[match.group(1)] += 1

    nemotron = [row for row in candidates if row.get("source") == "nemotron_saju"]
    target_only_name_rows = 0
    target_only_name_variants: Counter[str] = Counter()
    target_only_birthdate_rows = 0
    foreign_residue_rows = 0
    no_lacking_rows = 0
    for row in nemotron:
        user = _message_text(row, "user")
        assistant = _message_text(row, "assistant")
        user_names = {match.group(1) for match in NAME_PATTERN.finditer(user)}
        assistant_names = {match.group(1) for match in NAME_PATTERN.finditer(assistant)}
        if assistant_names - user_names:
            target_only_name_rows += 1
            target_only_name_variants[str(row.get("source_variant"))] += 1
        if FULL_BIRTHDATE_PATTERN.search(
            assistant
        ) and not FULL_BIRTHDATE_PATTERN.search(user):
            target_only_birthdate_rows += 1
        if any(
            character not in ALLOWED_SAJU_HANJA
            for character in SAJU_HANJA_PATTERN.findall(assistant)
        ):
            foreign_residue_rows += 1
        if "부족 오행: 없음" in user:
            no_lacking_rows += 1

    yeji = [row for row in candidates if row.get("source") == "yeji_bazi_rules"]
    yeji_assistant = [_message_text(row, "assistant") for row in yeji]
    yeji_particle_errors = 0
    for row in yeji:
        user = _message_text(row, "user")
        match = re.search(r"이 명식에는 (.+?)이 (?:성립|성립하지)", user)
        if match is not None and not _has_hangul_final_consonant(match.group(1)):
            yeji_particle_errors += 1

    bazi_assistant = [_message_text(row, "assistant") for row in bazi]
    nemotron_assistant = [_message_text(row, "assistant") for row in nemotron]
    return {
        "source_rows": dict(sorted(source_rows.items())),
        "source_token_totals": {
            source: dict(value) for source, value in sorted(source_tokens.items())
        },
        "assistant_token_share_percent": assistant_share,
        "candidate_training_projection_mismatches": projection_mismatches,
        "bazi": {
            "rows": len(bazi),
            "unique_charts": len(chart_counts),
            "rows_per_chart_distribution": dict(
                sorted(Counter(chart_counts.values()).items())
            ),
            "identical_disclaimer_rows": sum(
                value.endswith(BAZI_DISCLAIMER) for value in bazi_assistant
            ),
            "disclaimer_assistant_char_share_percent": round(
                len(BAZI_DISCLAIMER)
                * len(bazi_assistant)
                / sum(map(len, bazi_assistant))
                * 100,
                2,
            ),
        },
        "nemotron": {
            "rows": len(nemotron),
            "target_only_name_rows_local_pattern": target_only_name_rows,
            "target_only_name_variant_counts": dict(target_only_name_variants),
            "target_only_full_birthdate_rows": target_only_birthdate_rows,
            "foreign_residue_rows_local_whitelist": foreign_residue_rows,
            "no_lacking_rows": no_lacking_rows,
            "identical_disclaimer_rows": sum(
                value.endswith(NEMOTRON_DISCLAIMER) for value in nemotron_assistant
            ),
            "disclaimer_assistant_char_share_percent": round(
                len(NEMOTRON_DISCLAIMER)
                * len(nemotron_assistant)
                / sum(map(len, nemotron_assistant))
                * 100,
                2,
            ),
        },
        "yeji": {
            "rows": len(yeji),
            "unique_assistant_outputs": len(set(yeji_assistant)),
            "particle_error_rows": yeji_particle_errors,
        },
    }


def _finding_by_id(
    findings: Sequence[dict[str, Any]], review_id: str
) -> dict[str, Any]:
    matches = [item for item in findings if item.get("review_id") == review_id]
    if len(matches) != 1:
        raise Phase4Error(f"필수 외부 finding이 정확히 하나가 아닙니다: {review_id}")
    return matches[0]


def _validate_claims(
    summary: dict[str, Any],
    findings: Sequence[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    if metrics["source_rows"] != summary["source_rows"]:
        raise Phase4Error("독립 source 행 수가 외부 summary와 다릅니다.")
    if (
        metrics["assistant_token_share_percent"]
        != summary["assistant_token_share_percent"]
    ):
        raise Phase4Error("독립 assistant token 비율이 외부 summary와 다릅니다.")
    key_counts = summary["key_counts"]
    bazi_rows_per_chart = metrics["bazi"]["rows_per_chart_distribution"]
    reproduced_rows_per_chart = (
        next(iter(bazi_rows_per_chart)) if len(bazi_rows_per_chart) == 1 else None
    )
    strong_checks = {
        "candidate_training_projection_mismatches": (
            metrics["candidate_training_projection_mismatches"],
            summary["candidate_training_projection_mismatches"],
        ),
        "bazi_unique_charts": (
            metrics["bazi"]["unique_charts"],
            key_counts.get("bazi_unique_charts"),
        ),
        "bazi_rows_per_chart": (
            reproduced_rows_per_chart,
            key_counts.get("bazi_rows_per_chart"),
        ),
        "nemotron_target_only_date_rows": (
            metrics["nemotron"]["target_only_full_birthdate_rows"],
            key_counts.get("nemotron_target_only_date_rows"),
        ),
        "yeji_unique_assistant_outputs": (
            metrics["yeji"]["unique_assistant_outputs"],
            key_counts.get("yeji_unique_assistant_outputs"),
        ),
        "yeji_particle_errors": (
            metrics["yeji"]["particle_error_rows"],
            key_counts.get("yeji_particle_errors"),
        ),
    }
    mismatched = {
        name: {"independent": actual, "submitted": submitted}
        for name, (actual, submitted) in strong_checks.items()
        if actual != submitted
    }
    if mismatched:
        raise Phase4Error(f"독립 재현 필수 지표가 다릅니다: {sorted(mismatched)}")

    remedy = _finding_by_id(findings, "AGGREGATE-NEMOTRON-REMEDY-CONTRADICTION")
    disclaimer = _finding_by_id(findings, "AGGREGATE-DISCLAIMER-REPETITION")
    if (
        remedy["evidence"].get("no_lacking_rows")
        != metrics["nemotron"]["no_lacking_rows"]
    ):
        raise Phase4Error("부족 오행 없음 행 수를 재현하지 못했습니다.")
    if (
        disclaimer["evidence"].get("bazi_rows_with_same_disclaimer")
        != metrics["bazi"]["identical_disclaimer_rows"]
        or disclaimer["evidence"].get("bazi_assistant_char_share_percent")
        != metrics["bazi"]["disclaimer_assistant_char_share_percent"]
        or disclaimer["evidence"].get("nemotron_rows_with_same_disclaimer")
        != metrics["nemotron"]["identical_disclaimer_rows"]
        or disclaimer["evidence"].get("nemotron_assistant_char_share_percent")
        != metrics["nemotron"]["disclaimer_assistant_char_share_percent"]
    ):
        raise Phase4Error("반복 disclaimer 지표를 재현하지 못했습니다.")

    return {
        "accepted_verified": [
            "candidate_training_projection_integrity",
            "source_row_and_token_totals",
            "bazi_1250_charts_times_4",
            "target_only_full_birthdate_rows_441",
            "fixed_disclaimer_repetition",
            "yeji_unique_outputs_221_and_particle_errors_102",
        ],
        "partially_reproduced": {
            "nemotron_target_only_name_rows": {
                "submitted": key_counts.get("nemotron_target_only_name_rows"),
                "independent_local_pattern": metrics["nemotron"][
                    "target_only_name_rows_local_pattern"
                ],
                "reason": "외부 name matcher와 분석 코드가 제출되지 않았고 로컬 정규식은 일반명사 false positive 가능성이 있습니다.",
            },
            "nemotron_foreign_residue_rows": {
                "submitted": key_counts.get("nemotron_foreign_residue_rows"),
                "independent_local_whitelist": metrics["nemotron"][
                    "foreign_residue_rows_local_whitelist"
                ],
                "reason": "외부 한자 whitelist와 분석 코드가 제출되지 않아 정확한 집합을 동일하게 재현할 수 없습니다.",
            },
            "persona_job_and_remedy_subcounts": {
                "status": "claim_retained_not_independently_counted",
                "reason": "외부 의미 패턴 정의와 분석 코드가 제출되지 않았습니다.",
            },
        },
        "conditional_unverified": {
            "ssaju_runtime_tengod_conflict": {
                "submitted_rows": key_counts.get(
                    "nemotron_tengod_policy_conflict_rows_if_ssaju"
                ),
                "reason": "저장소에 ssaju runtime·버전·canonical policy가 없습니다.",
            }
        },
        "inference_not_gate": [
            "AI Hub 1.13% assistant-token exposure의 실제 품질 효과",
            "권장 혼합비와 production 적합성 결론",
        ],
    }


def _load_aihub_capacity(repo_root: Path) -> dict[str, Any]:
    path = repo_root / AIHUB_CAPACITY_REPORT
    document = _read_json_object(path.read_bytes(), "AI Hub staging 공개 집계")
    report = document.get("adapter_reports", {}).get("aihub_empathy")
    if not isinstance(report, dict):
        raise Phase4Error("AI Hub staging 공개 집계를 찾지 못했습니다.")
    result = {
        "source_rows_scanned": report.get("source_rows_scanned"),
        "eligible_rows_scanned": report.get("eligible_rows_scanned"),
        "eligible_talk_groups": report.get("eligible_talk_groups"),
        "staging_selected_groups": report.get("selected_groups"),
    }
    expected = {
        "source_rows_scanned": 58_268,
        "eligible_rows_scanned": 53_768,
        "eligible_talk_groups": 48_190,
        "staging_selected_groups": {
            "aihub_empathy_multiturn": 1_200,
            "aihub_empathy_single": 2_400,
        },
    }
    if result != expected:
        raise Phase4Error("AI Hub 증량 기반 공개 집계가 정본과 다릅니다.")
    return result


def _review_identity(
    submitted_sha256: Mapping[str, str], package: dict[str, Any]
) -> dict[str, Any]:
    identity = {
        "schema_version": SCHEMA_VERSION,
        "review_version": REVIEW_VERSION,
        "package_id": package["result"]["package_id"],
        "outer_zip_sha256": package["result"]["archive_sha256"],
        "submitted_files_sha256": dict(sorted(submitted_sha256.items())),
    }
    digest = sha256_bytes(canonical_json_bytes(identity))
    return {"inputs": identity, "sha256": digest, "review_id": f"review-{digest[:12]}"}


def verify_review_submission(
    submission: dict[str, Any],
    package: dict[str, Any],
    repo_root: Path,
    *,
    enforce_production: bool = True,
) -> dict[str, Any]:
    payloads = submission["payloads"]
    summary = _read_json_object(
        payloads["saju_mix20k_external_review_summary.json"], "외부 검수 summary"
    )
    findings = _read_jsonl(
        payloads["saju_mix20k_external_findings.jsonl"], "외부 검수 findings"
    )
    reviewed = _read_jsonl(
        payloads["saju_mix20k_reviewed_ids.jsonl"], "외부 검수 reviewed IDs"
    )
    _validate_summary(summary)
    _validate_findings(findings, summary)
    _validate_reviewed_rows(reviewed, summary)
    if (
        summary["outer_zip_sha256"] != package["result"]["archive_sha256"]
        or summary["package_build_id"] != package["result"]["canonical_build_id"]
        or summary["machine_scanned_rows"] != package["result"]["external_content_rows"]
        or summary["withheld_aihub_rows"] != package["result"]["withheld_aihub_rows"]
    ):
        raise Phase4Error("외부 검수 summary와 원본 package identity가 다릅니다.")

    candidate_by_id = {row.get("review_id"): row for row in package["candidates"]}
    if len(candidate_by_id) != len(package["candidates"]):
        raise Phase4Error("외부 candidate review ID가 중복됩니다.")
    for item in reviewed:
        candidate = candidate_by_id.get(item["review_id"])
        if candidate is None or candidate.get("source") != item["source"]:
            raise Phase4Error("reviewed ID가 candidate source와 일치하지 않습니다.")
    expected_reviewed = deterministic_review_sample(package["candidates"])
    if list(reviewed) != expected_reviewed:
        raise Phase4Error("reviewed ID가 결정적 token-decile 표본과 다릅니다.")

    representative_ids = [
        item["evidence"].get("representative_review_id")
        for item in findings
        if isinstance(item.get("evidence"), dict)
        and item["evidence"].get("representative_review_id") is not None
    ]
    if any(
        not isinstance(review_id, str) or review_id not in candidate_by_id
        for review_id in representative_ids
    ):
        raise Phase4Error("finding 대표 review ID가 외부 candidate에 없습니다.")
    reviewed_ids = {item["review_id"] for item in reviewed}
    representative_overlap = sorted(set(representative_ids) & reviewed_ids)

    metrics = _independent_metrics(package)
    claim_reproduction = _validate_claims(summary, findings, metrics)
    identity = _review_identity(submission["sha256"], package)
    capacity = _load_aihub_capacity(repo_root)
    warnings = [
        *submission["warnings"],
        "aggregate_finding_ids_instead_of_row_ids",
        "representative_examples_outside_semantic_sample",
        "semantic_review_has_no_per_row_verdicts",
        "external_method_code_model_prompt_hash_and_run_id_absent",
    ]
    if representative_overlap:
        warnings.remove("representative_examples_outside_semantic_sample")
    result = {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "status": "verified_advisory_intake",
        "review_id": identity["review_id"],
        "review_sha256": identity["sha256"],
        "review_identity_inputs": identity["inputs"],
        "reviewed_at": summary["reviewed_at"],
        "source_package": {
            "package_id": package["result"]["package_id"],
            "canonical_build_id": package["result"]["canonical_build_id"],
            "canonical_build_sha256": package["manifest"]["canonical_build_sha256"],
            "outer_zip_sha256": package["result"]["archive_sha256"],
            "outer_zip_bytes": package["result"]["archive_bytes"],
            "export_version": package["manifest"]["export_version"],
            "exporter_source_sha256": package["manifest"]["exporter_source_sha256"],
            "candidate_manifest_sha256": package["manifest"][
                "candidate_manifest_sha256"
            ],
            "canonical_manifest_sha256": package["manifest"][
                "canonical_manifest_sha256"
            ],
            "model_repo_id": package["manifest"]["model_contract"]["repo_id"],
            "model_revision": package["manifest"]["model_contract"]["revision"],
            "chat_template_sha256": package["manifest"]["model_contract"][
                "chat_template_sha256"
            ],
            "full_index_rows": package["result"]["full_index_rows"],
            "external_content_rows": package["result"]["external_content_rows"],
            "withheld_aihub_rows": package["result"]["withheld_aihub_rows"],
            "contains_aihub_source_text": False,
        },
        "verification_tool": {
            "version": INTAKE_VERSION,
            "source_path": "scripts/data/phase4_external_review_intake.py",
            "source_sha256": sha256_file(Path(__file__)),
            "package_verifier_source_sha256": package["manifest"][
                "exporter_source_sha256"
            ],
        },
        "submission_security": {
            "exact_file_set": True,
            "regular_files_only": True,
            "size_limits_passed": True,
            "utf8_nfc": True,
            "control_character_matches": 0,
            "pii_pattern_matches": 0,
            "restricted_aihub_identifier_matches": 0,
        },
        "submitted_files": {
            name: {
                "bytes": submission["bytes"][name],
                "sha256": submission["sha256"][name],
            }
            for name in sorted(SUBMITTED_FILES)
        },
        "finding_counts": {
            "total": len(findings),
            "severity": summary["finding_severity_counts"],
            "category": summary["finding_category_counts"],
        },
        "semantic_sample": {
            "rows": len(reviewed),
            "source_counts": dict(Counter(item["source"] for item in reviewed)),
            "per_source_per_decile": 10,
            "algorithm": "sort(total_tokens,review_id)/10 buckets/sha256 salt rank",
            "salt": SAMPLE_SALT,
            "exact_sequence_match": True,
            "representative_references": len(representative_ids),
            "representative_unique_ids": len(set(representative_ids)),
            "representative_reviewed_overlap": representative_overlap,
        },
        "independent_metrics": metrics,
        "claim_reproduction": claim_reproduction,
        "warnings": warnings,
        "governance": {
            "advisory_only": True,
            "automatic_gate_changes": False,
            "training_promotion_allowed_unchanged": True,
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "phase5_training_performed": False,
        },
        "aihub": {
            "external_withholding": {
                "status": "intentional_policy_boundary_not_missing_data",
                "source_text_shared": False,
                "row_ids_shared": False,
                "individual_record_hashes_shared": False,
            },
            "local_capacity_public_aggregate": capacity,
            "accepted_follow_up_contract": {
                "contract_id": "AIHUB-STYLE10K-v1",
                "status": "accepted_not_built",
                "additional_unique_rows": 10_000,
                "single_turn_rows": 5_000,
                "multiturn_rows": 5_000,
                "existing_mix20k_aihub_rows": 3_000,
                "disjoint_from_existing_mix20k_aihub": True,
                "disjoint_from_core_eval_and_source_holdout": True,
                "minimum_local_human_review": {
                    "single_turn": 100,
                    "multiturn": 100,
                },
                "local_human_review_performed": False,
                "git_or_external_source_text_allowed": False,
                "oversampling_allowed": False,
                "dataset_build_performed": False,
                "training_performed": False,
            },
        },
    }
    if enforce_production:
        if submission["sha256"] != EXPECTED_SUBMITTED_SHA256:
            raise Phase4Error("제출 파일 SHA-256이 승인된 외부 검수본과 다릅니다.")
        if (
            identity["sha256"] != EXPECTED_REVIEW_SHA256
            or identity["review_id"] != EXPECTED_REVIEW_ID
            or summary["review_version"] != REVIEW_VERSION
            or summary["reviewed_at"] != "2026-08-28"
            or summary["recommendation"] != "fix_then_recheck"
            or len(findings) != 13
            or summary["finding_severity_counts"] != {"high": 6, "medium": 6, "low": 1}
        ):
            raise Phase4Error("외부 검수 제출본의 고정 review identity가 다릅니다.")
        if metrics["source_token_totals"] != EXPECTED_SOURCE_TOKEN_TOTALS:
            raise Phase4Error("독립 source token 총계가 승인된 제출 분석과 다릅니다.")
    return result


def _owner_assessment(report: dict[str, Any]) -> str:
    metrics = report["independent_metrics"]
    partial = report["claim_reproduction"]["partially_reproduced"]
    return f"""# MIX20K 외부 검수 소유자 평가

## 결론

- 제출본 `{report["review_id"]}`은 패키지 `{report["source_package"]["package_id"]}`에 대한 자문 자료로 수용한다.
- 기술적 무결성과 여러 데이터 문제는 독립 재현했지만, 사람 명리 전문가 검수나 품질 인증으로 승격하지 않는다.
- 외부 권고는 canonical·Gate를 자동 변경하지 않으며 `advisory_only=true`로 유지한다.

## 독립 확인

- 공개 가능한 candidate 17,000건과 trainer projection 불일치: {metrics["candidate_training_projection_mismatches"]}건
- bazi: {metrics["bazi"]["unique_charts"]:,}개 고유 명식, 명식당 4행
- Nemotron target-only 전체 생년월일: {metrics["nemotron"]["target_only_full_birthdate_rows"]:,}행
- 반복 disclaimer: bazi {metrics["bazi"]["identical_disclaimer_rows"]:,}행/{metrics["bazi"]["disclaimer_assistant_char_share_percent"]:.2f}%, Nemotron {metrics["nemotron"]["identical_disclaimer_rows"]:,}행/{metrics["nemotron"]["disclaimer_assistant_char_share_percent"]:.2f}%
- YEJI: 고유 assistant 출력 {metrics["yeji"]["unique_assistant_outputs"]:,}종, 조사 오류 {metrics["yeji"]["particle_error_rows"]:,}행
- 의미 표본 300건은 소스별 100건·token decile별 10건인 결정적 추출 결과와 순서까지 일치한다.

## 부분 재현·미확인

- target-only 이름은 제출 {partial["nemotron_target_only_name_rows"]["submitted"]:,}행, 로컬 `NAME_PATTERN` {partial["nemotron_target_only_name_rows"]["independent_local_pattern"]:,}행이다. 외부 matcher가 없어 2행 차이를 확정하지 않는다.
- 번역 잔재는 제출 {partial["nemotron_foreign_residue_rows"]["submitted"]:,}행, 로컬 한자 whitelist {partial["nemotron_foreign_residue_rows"]["independent_local_whitelist"]:,}행이다. 외부 whitelist가 없어 정확한 집합을 확정하지 않는다.
- persona·직업·오행 보완 세부 건수는 외부 의미 패턴과 분석 코드가 없어 주장으로 보존한다.
- `ssaju` 십신 충돌 수치는 저장소에 runtime·버전·canonical policy가 없어 조건부 미확인으로 둔다.
- 대표 사례 참조 7개는 실제 candidate에 있으나 고유 ID는 4개이고 의미 표본 300건과 교집합이 없다.
- 행별 판정·메모, 외부 분석 코드, GPT 모델·버전, 실행 ID와 prompt hash가 없어 `semantic_reviewed_rows=300`은 제출자 자체 진술이다.

## AI Hub 경계와 후속 계약

- AI Hub 3,000건 본문 미제공은 누락이 아니라 승인되지 않은 제3자 공유를 막은 정상적인 정책 경계다.
- 공개 집계상 필터 통과 데이터는 53,768건·48,190개 대화 그룹이므로 로컬 증량 여력은 충분하다.
- 후속 `AIHUB-STYLE10K-v1`은 기존 MIX20K·평가군과 겹치지 않는 신규 그룹 10,000건(단일턴 5,000·멀티턴 5,000)으로 준비한다.
- 생성 전에 승인 범위 안에서 단일턴 100건·멀티턴 100건 이상을 로컬 검수한다. 원문·개별 ID·개별 hash는 Git이나 외부 검수 자료에 넣지 않는다.
- 이번 수용 작업은 계약만 기록하며 style 데이터 생성과 실제 학습은 수행하지 않는다.

## 유지하는 상태

- `training_promotion_allowed`: 기존 기술 상태 변경 없음
- `human_domain_review_performed=false`
- `quality_certification_claimed=false`
- `phase5_training_performed=false`
"""


def build_artifacts(
    submission: dict[str, Any], verification_report: dict[str, Any]
) -> dict[str, bytes]:
    artifacts = {
        f"submitted/{name}": submission["payloads"][name] for name in SUBMITTED_FILES
    }
    artifacts["verification_report.json"] = _json_bytes(verification_report)
    artifacts["owner_assessment.md"] = _owner_assessment(verification_report).encode(
        "utf-8"
    )
    checksums = {
        name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
    }
    checksum_payload = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(checksums.items())
    ).encode("utf-8")
    artifacts["SHA256SUMS.txt"] = checksum_payload
    build_manifest = {
        "schema_version": SCHEMA_VERSION,
        "report_type": REPORT_TYPE,
        "status": "verified_advisory_intake",
        "review_id": verification_report["review_id"],
        "review_sha256": verification_report["review_sha256"],
        "review_version": REVIEW_VERSION,
        "reviewed_at": verification_report["reviewed_at"],
        "source_package": verification_report["source_package"],
        "verification_tool": verification_report["verification_tool"],
        "submitted_files_sha256": verification_report["review_identity_inputs"][
            "submitted_files_sha256"
        ],
        "artifact_sha256": {
            name: sha256_bytes(payload) for name, payload in sorted(artifacts.items())
        },
        "advisory_only": True,
        "automatic_gate_changes": False,
        "training_promotion_allowed_unchanged": True,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
        "aihub_style10k_dataset_build_performed": False,
        "writes_are_immutable": True,
    }
    artifacts["build_manifest.json"] = _json_bytes(build_manifest)
    return artifacts


def _expected_output_entries() -> set[str]:
    return {
        "build_manifest.json",
        "verification_report.json",
        "owner_assessment.md",
        "SHA256SUMS.txt",
        "submitted",
        *(f"submitted/{name}" for name in SUBMITTED_FILES),
    }


def verify_materialized_review(
    review_root: Path, expected_artifacts: Mapping[str, bytes]
) -> dict[str, Any]:
    if review_root.is_symlink() or not review_root.is_dir():
        raise Phase4Error("수용된 review 경로가 일반 디렉터리가 아닙니다.")
    actual_entries: set[str] = set()
    for path in review_root.rglob("*"):
        relative = path.relative_to(review_root).as_posix()
        actual_entries.add(relative)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise Phase4Error(f"수용된 review에 symlink가 있습니다: {relative}")
        if path.is_dir():
            if stat.S_IMODE(mode) != PUBLIC_DIR_MODE:
                raise Phase4Error(f"수용된 review 디렉터리 권한이 다릅니다: {relative}")
        elif not stat.S_ISREG(mode) or stat.S_IMODE(mode) != PUBLIC_FILE_MODE:
            raise Phase4Error(f"수용된 review 파일 종류·권한이 다릅니다: {relative}")
    if actual_entries != _expected_output_entries():
        raise Phase4Error("수용된 review 파일 집합이 다릅니다.")
    if set(expected_artifacts) != _expected_output_entries() - {"submitted"}:
        raise Phase4Error("내부 수용 artifact 집합이 다릅니다.")
    for relative, expected in expected_artifacts.items():
        if (review_root / relative).read_bytes() != expected:
            raise Phase4Error(f"수용된 review artifact가 변조됐습니다: {relative}")

    manifest = _read_json_object(
        (review_root / "build_manifest.json").read_bytes(), "수용 build manifest"
    )
    if (
        manifest.get("status") != "verified_advisory_intake"
        or manifest.get("review_id") != review_root.name
        or manifest.get("advisory_only") is not True
        or manifest.get("automatic_gate_changes") is not False
        or manifest.get("training_promotion_allowed_unchanged") is not True
        or manifest.get("human_domain_review_performed") is not False
        or manifest.get("quality_certification_claimed") is not False
        or manifest.get("phase5_training_performed") is not False
        or manifest.get("aihub_style10k_dataset_build_performed") is not False
        or manifest.get("writes_are_immutable") is not True
    ):
        raise Phase4Error("수용 build manifest Gate 값이 다릅니다.")
    return {
        "status": "verified_materialized_external_review",
        "review_id": manifest["review_id"],
        "review_sha256": manifest["review_sha256"],
        "artifact_count": len(expected_artifacts),
    }


def _ensure_public_parent(path: Path, repo_root: Path) -> None:
    relative = path.relative_to(repo_root)
    current = repo_root
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise Phase4Error(
                    f"공개 report 부모 경로가 안전하지 않습니다: {current}"
                )
            continue
        current.mkdir(mode=PUBLIC_DIR_MODE)


def materialize_review(
    repo_root: Path,
    review_id: str,
    artifacts: Mapping[str, bytes],
) -> dict[str, Any]:
    version_root = repo_root / REPORT_RELATIVE_ROOT
    _ensure_public_parent(version_root, repo_root)
    target = version_root / review_id
    if target.exists() or target.is_symlink():
        result = verify_materialized_review(target, artifacts)
        return {**result, "mode": "reused", "writes_performed": False}
    temporary = Path(tempfile.mkdtemp(prefix=f".{review_id}.", dir=version_root))
    previous_umask = os.umask(0o022)
    try:
        for relative, payload in artifacts.items():
            destination = temporary / relative
            write_bytes_once(destination, payload, mode=PUBLIC_FILE_MODE)
        for directory in [temporary, temporary / "submitted"]:
            directory.chmod(PUBLIC_DIR_MODE)
        try:
            os.rename(temporary, target)
        except OSError as exc:
            if target.exists() and not temporary.exists():
                pass
            else:
                raise Phase4Error(
                    "불변 review 경로를 원자적으로 승격하지 못했습니다."
                ) from exc
    finally:
        os.umask(previous_umask)
        if temporary.exists():
            shutil.rmtree(temporary)
    result = verify_materialized_review(target, artifacts)
    return {**result, "mode": "built", "writes_performed": True}


def prepare_review(
    repo_root: Path,
    bundle_dir: Path,
    archive: Path,
    *,
    sidecar: Path | None = None,
    enforce_production: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    submission = load_submission(bundle_dir)
    package = load_package_context(
        archive,
        repo_root,
        sidecar_path=sidecar,
        enforce_production=enforce_production,
    )
    report = verify_review_submission(
        submission, package, repo_root, enforce_production=enforce_production
    )
    artifacts = build_artifacts(submission, report)
    return report, artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="외부 MIX20K 검수 제출본을 자문 자료로 검증·수용한다."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "ingest"):
        command = commands.add_parser(name)
        command.add_argument("--bundle-dir", type=Path, required=True)
        command.add_argument("--archive", type=Path, required=True)
        command.add_argument("--sidecar", type=Path)
        if name == "ingest":
            command.add_argument("--confirm-advisory-intake", action="store_true")
    return parser


def run(arguments: argparse.Namespace, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    if arguments.command == "ingest" and not arguments.confirm_advisory_intake:
        raise Phase4Error("자문 자료 수용 확인 옵션이 필요합니다.")
    report, artifacts = prepare_review(
        repo_root,
        arguments.bundle_dir,
        arguments.archive,
        sidecar=arguments.sidecar,
    )
    review_root = repo_root / REPORT_RELATIVE_ROOT / report["review_id"]
    if arguments.command == "verify":
        materialized = None
        if review_root.exists() or review_root.is_symlink():
            materialized = verify_materialized_review(review_root, artifacts)
        return {
            "status": "verified_external_review_submission",
            "review_id": report["review_id"],
            "review_sha256": report["review_sha256"],
            "output": review_root.relative_to(repo_root).as_posix(),
            "output_exists": materialized is not None,
            "warnings": report["warnings"],
            "advisory_only": True,
            "automatic_gate_changes": False,
            "phase5_training_performed": False,
        }
    if arguments.command == "ingest":
        result = materialize_review(repo_root, report["review_id"], artifacts)
        return {
            **result,
            "output": review_root.relative_to(repo_root).as_posix(),
            "advisory_only": True,
            "automatic_gate_changes": False,
            "phase5_training_performed": False,
        }
    raise Phase4Error(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        print(json.dumps(run(arguments), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Phase4Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
