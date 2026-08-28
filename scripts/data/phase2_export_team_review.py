# phase2_export_team_review.py - 승인된 팀원용 핵심 검수 오프라인 ZIP을 생성·검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.audit_tools import (
    _load_build_files,
    _load_raw_record,
    apply_yeji_corrections,
    audit_paths,
    prepare_audit,
    sha256_file,
    sha256_json,
    verify_audit,
)
from scripts.data.errors import Phase1Error, Phase2AuditError
from scripts.data.phase2_verify_history import verify_historical_build

DEFAULT_SOURCE_CONFIG = REPO_ROOT / "configs/data_sources.v1.1.json"
DEFAULT_POLICY = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/audit-policy-v1.2.0.json"
)
ASSET_ROOT = Path(__file__).with_name("team_review_assets")
PACKAGE_SCHEMA_VERSION = "1.0.0"
PACKAGE_VERSION = "share-v1.1.0"
PROJECTION_VERSION = "minimal-v1.0.0"
TEAM_REVIEWER_VERSION = "team-reviewer-v1.1.0"
AUTHORIZATION_BASIS = "explicit_aihub_dataset_86_reviewer_access_authorization"
LEGACY_AUTHORIZATION_BASIS = "explicit_user_confirmation_same_approval_scope"
STATIC_ASSETS = ("START_HERE.html", "team-review.css", "team-review.js")
PACKAGE_ARTIFACTS = (
    *STATIC_ASSETS,
    "review-data.js",
    "TEAM_REVIEW_GUIDE.md",
    "DATA_USAGE_NOTICE.md",
)
PACKAGE_FILES = (*PACKAGE_ARTIFACTS, "PACKAGE_MANIFEST.json", "SHA256SUMS.txt")
CHECKSUM_FILES = (*PACKAGE_ARTIFACTS, "PACKAGE_MANIFEST.json")
EXPECTED_ALL_UNITS = {
    "aihub_empathy": 100,
    "bazi_sft": 60,
    "nemotron_saju": 90,
    "yeji_bazi_rules": 50,
}
EXPECTED_ALL_RECORDS = {
    "aihub_empathy": 110,
    "bazi_sft": 70,
    "nemotron_saju": 110,
    "yeji_bazi_rules": 50,
}
FORBIDDEN_KEYS = {
    "locator",
    "locators",
    "path",
    "member",
    "row_index",
    "uuid",
    "example_id",
    "synthetic_id",
    "profile-id",
    "talk-id",
    "persona-id",
    "private_note",
    "decision_id",
    "source_group_id",
    "leakage_group_id",
}
DATA_JS_PREFIX = (
    "// review-data.js - 고정 audit build에서 만든 팀 검수 최소 투영 데이터다.\n"
    "globalThis.TEAM_REVIEW_PACKAGE = "
)
DATA_JS_SUFFIX = ";\n"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FEEDBACK_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _copy_selected(value: dict[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {key: deepcopy(value[key]) for key in keys if key in value}


def project_aihub(record: dict[str, Any]) -> dict[str, Any]:
    profile = record.get("profile")
    talk = record.get("talk")
    if not isinstance(profile, dict) or not isinstance(talk, dict):
        raise Phase2AuditError("AI Hub 검수 표본 구조가 올바르지 않습니다.")
    persona = profile.get("persona")
    emotion = profile.get("emotion")
    content = talk.get("content")
    if (
        not isinstance(persona, dict)
        or not isinstance(emotion, dict)
        or not isinstance(content, dict)
    ):
        raise Phase2AuditError("AI Hub 검수 최소 투영 필드가 없습니다.")
    return {
        "profile": {
            "persona": _copy_selected(persona, ("human", "computer")),
            "emotion": _copy_selected(emotion, ("type", "situation")),
        },
        "talk": {"content": deepcopy(content)},
    }


def project_nemotron(record: dict[str, Any]) -> dict[str, Any]:
    context_keys = (
        "professional_persona",
        "sports_persona",
        "arts_persona",
        "travel_persona",
        "culinary_persona",
        "family_persona",
        "persona",
        "cultural_background",
        "skills_and_expertise",
        "skills_and_expertise_list",
        "hobbies_and_interests",
        "hobbies_and_interests_list",
        "career_goals_and_ambitions",
        "sex",
        "age",
        "marital_status",
        "military_status",
        "family_type",
        "housing_type",
        "education_level",
        "bachelors_field",
        "occupation",
        "district",
        "province",
        "country",
    )
    chart_keys = (
        "saju_pillars",
        "saju_day_master",
        "saju_elements",
        "saju_elements_dominant",
        "saju_elements_lacking",
        "saju_sipsin",
    )
    narrative_keys = ("saju_narrative", "saju_narrative_error")
    projected = {
        "persona_context": _copy_selected(record, context_keys),
        "chart": _copy_selected(record, chart_keys),
        "narrative": _copy_selected(record, narrative_keys),
    }
    if not projected["chart"].get("saju_pillars"):
        raise Phase2AuditError("Nemotron 검수 최소 투영에 명식이 없습니다.")
    return projected


def project_bazi(record: dict[str, Any]) -> dict[str, Any]:
    facts = record.get("facts")
    if not isinstance(facts, dict):
        raise Phase2AuditError("BaZi 검수 표본 facts가 올바르지 않습니다.")
    projected_facts = _copy_selected(
        facts, ("bazi_year", "pillars", "day_master", "element_counts")
    )
    if not projected_facts.get("pillars"):
        raise Phase2AuditError("BaZi 검수 최소 투영에 명식이 없습니다.")
    return {
        "facts": projected_facts,
        **_copy_selected(
            record,
            (
                "retrieved_rules",
                "question_type",
                "user_question",
                "response",
                "citations",
            ),
        ),
    }


def project_yeji(record: dict[str, Any]) -> dict[str, Any]:
    required = (
        "id",
        "name_cn",
        "name_ko",
        "type",
        "category",
        "condition",
        "meaning",
    )
    if any(key not in record for key in required):
        raise Phase2AuditError("YEJI 검수 최소 투영 필드가 없습니다.")
    return _copy_selected(record, required)


def project_record(source: str, record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise Phase2AuditError("팀 검수 표본은 JSON object여야 합니다.")
    if source == "aihub_empathy":
        return project_aihub(record)
    if source == "nemotron_saju":
        return project_nemotron(record)
    if source == "bazi_sft":
        return project_bazi(record)
    if source == "yeji_bazi_rules":
        return project_yeji(record)
    raise Phase2AuditError(f"지원하지 않는 팀 검수 원천입니다: {source}")


def _yeji_effective_records(
    records: list[dict[str, Any]], correction_manifest: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if correction_manifest is None:
        return deepcopy(records), []
    rule_ids = {
        int(record["id"])
        for record in records
        if isinstance(record.get("id"), int)
    }
    selected = [
        correction
        for correction in correction_manifest["corrections"]
        if int(correction["rule_id"]) in rule_ids
    ]
    if not selected:
        return deepcopy(records), []
    partial_manifest = {**correction_manifest, "corrections": selected}
    effective, applied = apply_yeji_corrections(
        {"shensha_list": records}, partial_manifest
    )
    basis = {
        value["correction_id"]: value.get("basis") for value in selected
    }
    return effective["shensha_list"], [
        {**value, "basis": basis.get(value["correction_id"])} for value in applied
    ]


def _assert_no_forbidden_keys(value: Any, location: str = "root") -> None:
    if isinstance(value, dict):
        forbidden = FORBIDDEN_KEYS & set(value)
        if forbidden:
            raise Phase2AuditError(
                f"팀 검수 투영에 금지 필드가 있습니다: {location}/{sorted(forbidden)}"
            )
        for key, child in value.items():
            _assert_no_forbidden_keys(child, f"{location}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_keys(child, f"{location}/{index}")


def build_projected_items(
    queue: Sequence[dict[str, Any]],
    raw_loader: Callable[[dict[str, Any]], Any],
    correction_manifest: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    selected = [
        item for item in queue if item.get("queue") in {"required", "reference"}
    ]
    queue_counts = Counter(item.get("queue") for item in selected)
    if queue_counts != {"required": 150, "reference": 150}:
        raise Phase2AuditError("핵심·참조 검수 큐 수량이 각각 150건이 아닙니다.")
    source_counts = Counter(item.get("source") for item in selected)
    if dict(source_counts) != EXPECTED_ALL_UNITS:
        raise Phase2AuditError("전체 검수 큐 source 할당이 정본과 다릅니다.")
    projected: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        source = str(item["source"])
        raw_records = [raw_loader(locator) for locator in item["locators"]]
        effective_records = raw_records
        corrections: list[dict[str, Any]] = []
        if source == "yeji_bazi_rules":
            if not all(isinstance(record, dict) for record in raw_records):
                raise Phase2AuditError("YEJI 검수 원문 구조가 올바르지 않습니다.")
            effective_records, corrections = _yeji_effective_records(
                raw_records, correction_manifest
            )
        value: dict[str, Any] = {
            "index": index,
            "review_id": item["review_id"],
            "queue": item["queue"],
            "source": source,
            "stratum": item["stratum"],
            "unit_type": item["unit_type"],
            "flags": deepcopy(item.get("flags", [])),
            "records": [
                project_record(source, record) for record in effective_records
            ],
            "corrections": corrections,
        }
        if corrections:
            value["original_records"] = [
                project_record(source, record) for record in raw_records
            ]
        projected.append(value)
    record_counts = Counter()
    for item in projected:
        record_counts[item["source"]] += len(item["records"])
    if dict(record_counts) != EXPECTED_ALL_RECORDS:
        raise Phase2AuditError("전체 검수 큐 record 할당이 정본과 다릅니다.")
    _assert_no_forbidden_keys(projected)
    return projected


def _asset_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in STATIC_ASSETS:
        path = ASSET_ROOT / name
        if not path.is_file():
            raise Phase2AuditError(f"팀 검수 정적 자산이 없습니다: {name}")
        hashes[name] = sha256_file(path)
    return hashes


def build_package_document(
    context: dict[str, Any],
    values: dict[str, Any],
    projected_items: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_units = Counter(item["source"] for item in projected_items)
    source_records = Counter()
    for item in projected_items:
        source_records[item["source"]] += len(item["records"])
    projection_sha256 = sha256_json(projected_items)
    asset_hashes = _asset_hashes()
    fingerprint_inputs = {
        "audit_build_sha256": context["identity"]["build_sha256"],
        "asset_sha256": asset_hashes,
        "package_version": PACKAGE_VERSION,
        "projection_sha256": projection_sha256,
        "projection_version": PROJECTION_VERSION,
        "queue_sha256": values["queue_manifest"]["queue_sha256"],
        "scope": "required_and_reference",
        "team_reviewer_version": TEAM_REVIEWER_VERSION,
    }
    fingerprint = sha256_json(fingerprint_inputs)
    package_id = f"team-review-{fingerprint[:12]}"
    document = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "feedback_type": "advisory_team_review",
        "package_id": package_id,
        "package_version": PACKAGE_VERSION,
        "projection_version": PROJECTION_VERSION,
        "team_reviewer_version": TEAM_REVIEWER_VERSION,
        "dataset_name": context["policy"]["dataset_name"],
        "audit_version": context["policy"]["audit_version"],
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "scope": "required_and_reference",
        "unit_count": len(projected_items),
        "record_count": sum(source_records.values()),
        "source_unit_counts": dict(sorted(source_units.items())),
        "source_record_counts": dict(sorted(source_records.items())),
        "contains_aihub_controlled_data": True,
        "main_decision_ledger_included": False,
        "advisory_feedback_only": True,
        "decision_values": list(context["policy"]["decision_values"]),
        "reason_codes": list(context["policy"]["reason_codes"]),
        "items": projected_items,
    }
    manifest_base = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "package_version": PACKAGE_VERSION,
        "projection_version": PROJECTION_VERSION,
        "team_reviewer_version": TEAM_REVIEWER_VERSION,
        "dataset_name": context["policy"]["dataset_name"],
        "audit_version": context["policy"]["audit_version"],
        "build_id": context["identity"]["build_id"],
        "build_sha256": context["identity"]["build_sha256"],
        "queue_sha256": values["queue_manifest"]["queue_sha256"],
        "projection_sha256": projection_sha256,
        "package_fingerprint": fingerprint,
        "scope": "required_and_reference",
        "unit_count": len(projected_items),
        "record_count": sum(source_records.values()),
        "source_unit_counts": dict(sorted(source_units.items())),
        "source_record_counts": dict(sorted(source_records.items())),
        "contains_aihub_controlled_data": True,
        "archive_encryption": "none_user_selected",
        "authorization_basis": AUTHORIZATION_BASIS,
        "main_decision_ledger_included": False,
        "advisory_feedback_only": True,
        "fingerprint_inputs": fingerprint_inputs,
    }
    _assert_no_forbidden_keys(document)
    return document, manifest_base


def _render_data_javascript(document: dict[str, Any]) -> bytes:
    rendered = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    rendered = rendered.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return (DATA_JS_PREFIX + rendered + DATA_JS_SUFFIX).encode("utf-8")


def _team_guide(manifest: dict[str, Any]) -> str:
    counts = manifest["source_unit_counts"]
    return f"""# 팀원 검수 안내

이 패키지는 사주 1.3B baseline Phase 2A `{manifest['audit_version']}/{manifest['build_id']}`의 핵심 150단위와 참고 150단위, 총 300단위에 대한 독립적인 2차 의견을 받기 위한 자료입니다.

## 시작 방법

1. ZIP 전체를 승인된 로컬 폴더에 압축 해제합니다. ZIP 내부에서 바로 열지 않습니다.
2. Chrome 또는 Edge에서 `START_HERE.html`을 엽니다.
3. 검수자 표기를 입력하고 각 항목의 제안 판정과 필요 시 사유·메모를 저장합니다.
4. 중간에는 `진행 JSON 저장`으로 checkpoint를 남깁니다. 이어서 할 때 `진행 불러오기`를 사용합니다.
5. 완료 후 `최종 JSON 내보내기`와 `CSV 내보내기`를 눌러 두 파일만 원 담당자에게 돌려줍니다.

## 범위

- AI Hub 공감대화: {counts['aihub_empathy']}단위
- Nemotron 사주: {counts['nemotron_saju']}단위
- BaZi SFT: {counts['bazi_sft']}단위
- YEJI 규칙: {counts['yeji_bazi_rules']}단위

이 수량은 위험·품질 검사를 위한 과대표집이며 실제 학습 혼합비 55%/25%/15%/5%를 나타내지 않습니다.

## 판정 의미

- `accept`: 구조·내용·안전성에 문제가 없습니다.
- `exclude_candidate`: 이 표본만 제외하는 것이 적절합니다.
- `rule_fix_required`: 공통 변환·교정 규칙을 수정해야 합니다.
- `source_block`: 원천 전체에 반복되는 중대한 문제가 보입니다.
- `uncertain`: 다른 사람 판단이 더 필요합니다.
- `skip`: 이번 검토에서 임시로 건너뜁니다.

## 원천별 확인 기준

- AI Hub: 자해·임상 상황에서 안전한 대응인지, 개인정보·진단·과도한 훈계가 없는지 확인합니다.
- Nemotron: 명식·오행과 서술이 일치하는지, 건강·사고·재물 등을 확정적으로 단정하지 않는지 확인합니다.
- BaZi: 제공된 명식·일간·오행·검색 규칙과 답변이 일치하는지, pair 간 모순이 없는지 확인합니다.
- YEJI: 원본과 correction overlay의 전후 값, 조건 mapping·category·meaning을 확인합니다.

팀원 의견은 advisory 자료입니다. 이 패키지는 본 판정 ledger를 포함하거나 수정하지 않으며, 최종 판정은 원 담당자가 별도 검수기에서 확정합니다.
"""


def _usage_notice(manifest: dict[str, Any]) -> str:
    return f"""# 통제 데이터 취급 안내

이 일반 ZIP은 암호화되지 않았으며 AI Hub #86의 최소 검수 투영을 포함합니다.

- 패키지 ID: `{manifest['package_id']}`
- Audit: `{manifest['audit_version']}/{manifest['build_id']}`
- 승인 근거: AI Hub #86의 동일 신청에 포함됐거나 별도 열람 권한을 명시적으로 확인함

AI Hub 공식 이용정책은 승인받지 않은 다른 법인·단체·개인에게 데이터를 열람시키거나 제공·양도·대여·판매하지 못하도록 규정합니다.

https://aihub.or.kr/intrcn/guid/usagepolicy.do

다음 조건을 지켜야 합니다.

1. 승인된 내부 전송 수단으로만 전달합니다.
2. Git, 공개 저장소, 공개 링크, 공용 드라이브, 공개 메신저 방에 올리지 않습니다.
3. 다른 사람에게 재전달하지 않습니다.
4. 검수 결과 JSON·CSV만 원 담당자에게 반환합니다.
5. 검수가 끝나면 ZIP과 압축 해제 폴더를 삭제합니다.
6. 승인 범위가 불확실해지면 즉시 열람을 멈추고 원 담당자에게 알립니다.
"""


def _write_payload(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_zoned_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def materialize_package(
    package_root: Path,
    document: dict[str, Any],
    manifest_base: dict[str, Any],
) -> dict[str, Any]:
    package_root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name in STATIC_ASSETS:
        _write_payload(package_root / name, (ASSET_ROOT / name).read_bytes())
    _write_payload(package_root / "review-data.js", _render_data_javascript(document))
    _write_payload(
        package_root / "TEAM_REVIEW_GUIDE.md",
        _team_guide(manifest_base).encode("utf-8"),
    )
    _write_payload(
        package_root / "DATA_USAGE_NOTICE.md",
        _usage_notice(manifest_base).encode("utf-8"),
    )
    artifact_hashes = {
        name: sha256_file(package_root / name) for name in PACKAGE_ARTIFACTS
    }
    manifest = {**manifest_base, "artifact_sha256": artifact_hashes}
    _write_payload(
        package_root / "PACKAGE_MANIFEST.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode(
            "utf-8"
        )
        + b"\n",
    )
    sums = "".join(
        f"{sha256_file(package_root / name)}  {name}\n" for name in CHECKSUM_FILES
    )
    _write_payload(package_root / "SHA256SUMS.txt", sums.encode("utf-8"))
    return manifest


def _zip_directory(package_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(package_root.iterdir(), key=lambda value: value.name):
            if not path.is_file() or path.is_symlink():
                raise Phase2AuditError("팀 검수 패키지에 일반 파일 외 항목이 있습니다.")
            info = zipfile.ZipInfo(
                f"{package_root.name}/{path.name}",
                date_time=(2026, 8, 27, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    os.chmod(destination, 0o600)


def _parse_data_javascript(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
        if not text.startswith(DATA_JS_PREFIX) or not text.endswith(DATA_JS_SUFFIX):
            raise ValueError("wrapper")
        value = json.loads(text[len(DATA_JS_PREFIX) : -len(DATA_JS_SUFFIX)])
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Phase2AuditError("review-data.js를 검증할 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError("review-data.js 최상위 값이 object가 아닙니다.")
    return value


def verify_archive(archive_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise Phase2AuditError("공유 ZIP CRC 검증에 실패했습니다.")
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if not names:
                raise Phase2AuditError("공유 ZIP이 비어 있습니다.")
            roots = {PurePosixPath(name).parts[0] for name in names}
            if len(roots) != 1:
                raise Phase2AuditError("공유 ZIP 최상위 디렉터리가 하나가 아닙니다.")
            for name in names:
                pure = PurePosixPath(name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or len(pure.parts) != 2
                    or "\\" in name
                    or ":" in name
                    or FEEDBACK_CONTROL_PATTERN.search(name)
                ):
                    raise Phase2AuditError("공유 ZIP 경로가 안전하지 않습니다.")
            if len(names) != len(set(names)):
                raise Phase2AuditError("공유 ZIP에 중복 경로가 있습니다.")
            if any(entry.is_dir() for entry in entries):
                raise Phase2AuditError("공유 ZIP에 예상하지 않은 디렉터리 엔트리가 있습니다.")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise Phase2AuditError("공유 ZIP 엔트리 암호화 상태가 계약과 다릅니다.")
            if any(
                stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG
                for entry in entries
            ):
                raise Phase2AuditError("공유 ZIP에 일반 파일 외 엔트리가 있습니다.")
            if any((entry.external_attr >> 16) & 0o777 != 0o600 for entry in entries):
                raise Phase2AuditError("공유 ZIP 파일 권한이 0600이 아닙니다.")
            if any(entry.file_size > 64 * 1024 * 1024 for entry in entries) or sum(
                entry.file_size for entry in entries
            ) > 128 * 1024 * 1024:
                raise Phase2AuditError("공유 ZIP 압축 해제 크기가 제한을 넘습니다.")
            root = next(iter(roots))
            expected_files = set(PACKAGE_FILES)
            actual_files = {PurePosixPath(name).name for name in names}
            if actual_files != expected_files or len(names) != len(expected_files):
                raise Phase2AuditError("공유 ZIP 파일 목록이 계약과 다릅니다.")

            def read(name: str) -> bytes:
                return archive.read(f"{root}/{name}")

            manifest = json.loads(read("PACKAGE_MANIFEST.json"))
            if not isinstance(manifest, dict):
                raise Phase2AuditError("공유 package manifest가 object가 아닙니다.")
            artifact_hashes = manifest.get("artifact_sha256")
            if not isinstance(artifact_hashes, dict) or set(artifact_hashes) != set(
                PACKAGE_ARTIFACTS
            ):
                raise Phase2AuditError("공유 artifact hash 목록이 계약과 다릅니다.")
            for name, expected_hash in artifact_hashes.items():
                if (
                    not isinstance(expected_hash, str)
                    or not SHA256_PATTERN.fullmatch(expected_hash)
                    or _sha256_bytes(read(name)) != expected_hash
                ):
                    raise Phase2AuditError(f"공유 artifact hash가 다릅니다: {name}")
            sums: dict[str, str] = {}
            for raw_line in read("SHA256SUMS.txt").decode("utf-8").splitlines():
                value, separator, name = raw_line.partition("  ")
                if not separator or not SHA256_PATTERN.fullmatch(value):
                    raise Phase2AuditError("SHA256SUMS 형식이 올바르지 않습니다.")
                if name in sums:
                    raise Phase2AuditError("SHA256SUMS에 중복 파일이 있습니다.")
                sums[name] = value
            if set(sums) != set(CHECKSUM_FILES):
                raise Phase2AuditError("SHA256SUMS 파일 목록이 계약과 다릅니다.")
            for name, expected_hash in sums.items():
                if _sha256_bytes(read(name)) != expected_hash:
                    raise Phase2AuditError(f"SHA256SUMS 값이 다릅니다: {name}")
            document = _parse_data_javascript(read("review-data.js"))
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError("공유 ZIP을 검증할 수 없습니다.") from exc
    _assert_no_forbidden_keys(document)
    asset_hashes = {
        name: manifest["artifact_sha256"][name] for name in STATIC_ASSETS
    }
    expected_fingerprint_inputs = {
        "audit_build_sha256": manifest.get("build_sha256"),
        "asset_sha256": asset_hashes,
        "package_version": manifest.get("package_version"),
        "projection_sha256": manifest.get("projection_sha256"),
        "projection_version": manifest.get("projection_version"),
        "queue_sha256": manifest.get("queue_sha256"),
        "scope": "required_and_reference",
        "team_reviewer_version": manifest.get("team_reviewer_version"),
    }
    expected_fingerprint = sha256_json(expected_fingerprint_inputs)
    package_id = f"team-review-{expected_fingerprint[:12]}"
    document_identity_fields = (
        "schema_version",
        "package_id",
        "package_version",
        "projection_version",
        "team_reviewer_version",
        "dataset_name",
        "audit_version",
        "build_id",
        "build_sha256",
        "scope",
        "unit_count",
        "record_count",
        "source_unit_counts",
        "source_record_counts",
        "contains_aihub_controlled_data",
        "main_decision_ledger_included",
        "advisory_feedback_only",
    )
    decision_values = document.get("decision_values")
    reason_codes = document.get("reason_codes")
    items = document.get("items")
    valid_decisions = (
        isinstance(decision_values, list)
        and all(isinstance(value, str) and value for value in decision_values)
        and len(decision_values) == len(set(decision_values))
    )
    valid_reasons = (
        isinstance(reason_codes, list)
        and all(isinstance(value, str) and value for value in reason_codes)
        and len(reason_codes) == len(set(reason_codes))
    )
    valid_items = (
        isinstance(items, list)
        and len(items) == 300
        and all(isinstance(item, dict) for item in items)
    )
    review_ids = (
        [item.get("review_id") for item in items] if valid_items else []
    )
    if (
        manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION
        or any(document.get(field) != manifest.get(field) for field in document_identity_fields)
        or manifest.get("archive_encryption") != "none_user_selected"
        or manifest.get("authorization_basis")
        not in {AUTHORIZATION_BASIS, LEGACY_AUTHORIZATION_BASIS}
        or manifest.get("fingerprint_inputs") != expected_fingerprint_inputs
        or manifest.get("package_fingerprint") != expected_fingerprint
        or manifest.get("package_id") != package_id
        or not SHA256_PATTERN.fullmatch(str(manifest.get("build_sha256", "")))
        or not SHA256_PATTERN.fullmatch(str(manifest.get("queue_sha256", "")))
        or document.get("feedback_type") != "advisory_team_review"
        or not valid_decisions
        or not valid_reasons
        or document.get("unit_count") != 300
        or document.get("record_count") != 340
        or document.get("source_unit_counts") != EXPECTED_ALL_UNITS
        or document.get("source_record_counts") != EXPECTED_ALL_RECORDS
        or not valid_items
        or any(
            not isinstance(review_id, str) or not re.fullmatch(r"[0-9a-f]{24}", review_id)
            for review_id in review_ids
        )
        or len(set(review_ids)) != 300
        or sha256_json(items) != manifest.get("projection_sha256")
    ):
        raise Phase2AuditError("공유 ZIP identity 또는 수량 계약이 다릅니다.")
    return {
        "status": "verified",
        "archive": archive_path.name,
        "package_id": manifest["package_id"],
        "audit_version": manifest["audit_version"],
        "build_id": manifest["build_id"],
        "unit_count": document["unit_count"],
        "record_count": document["record_count"],
        "contains_aihub_controlled_data": True,
        "archive_encryption": manifest["archive_encryption"],
    }


def _archive_document(archive_path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            roots = {PurePosixPath(name).parts[0] for name in names}
            if len(roots) != 1:
                raise Phase2AuditError(
                    "공유 ZIP 최상위 디렉터리가 하나가 아닙니다."
                )
            root = next(iter(roots))
            return _parse_data_javascript(archive.read(f"{root}/review-data.js"))
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise Phase2AuditError("공유 ZIP data를 읽을 수 없습니다.") from exc


def verify_feedback(archive_path: Path, feedback_path: Path) -> dict[str, Any]:
    verify_archive(archive_path)
    document = _archive_document(archive_path)
    try:
        if feedback_path.stat().st_size > 2 * 1024 * 1024:
            raise Phase2AuditError("팀원 의견 JSON이 2MiB를 넘습니다.")
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError("팀원 의견 JSON을 읽을 수 없습니다.") from exc
    if not isinstance(feedback, dict):
        raise Phase2AuditError("팀원 의견 최상위 값은 object여야 합니다.")
    expected_top = {
        "schema_version",
        "feedback_type",
        "export_kind",
        "package_id",
        "audit_version",
        "build_id",
        "reviewer_label",
        "exported_at",
        "completed_units",
        "total_units",
        "suggestions",
    }
    if set(feedback) != expected_top:
        raise Phase2AuditError("팀원 의견 최상위 필드가 계약과 다릅니다.")
    if (
        feedback.get("schema_version") != PACKAGE_SCHEMA_VERSION
        or feedback.get("feedback_type") != "advisory_team_review"
        or feedback.get("export_kind") not in {"checkpoint", "final"}
        or feedback.get("package_id") != document["package_id"]
        or feedback.get("audit_version") != document["audit_version"]
        or feedback.get("build_id") != document["build_id"]
        or feedback.get("total_units") != document["unit_count"]
    ):
        raise Phase2AuditError("팀원 의견 identity가 공유 package와 다릅니다.")
    reviewer_label = feedback.get("reviewer_label")
    if (
        not isinstance(reviewer_label, str)
        or not reviewer_label.strip()
        or len(reviewer_label) > 80
        or FEEDBACK_CONTROL_PATTERN.search(reviewer_label)
    ):
        raise Phase2AuditError("팀원 의견 reviewer label이 올바르지 않습니다.")
    if not _is_zoned_iso_timestamp(feedback.get("exported_at")):
        raise Phase2AuditError("팀원 의견 exported_at이 올바르지 않습니다.")
    suggestions = feedback.get("suggestions")
    if not isinstance(suggestions, list):
        raise Phase2AuditError("팀원 의견 suggestions가 배열이 아닙니다.")
    completed_units = feedback.get("completed_units")
    if (
        not isinstance(completed_units, int)
        or isinstance(completed_units, bool)
        or completed_units != len(suggestions)
    ):
        raise Phase2AuditError("팀원 의견 completed count가 다릅니다.")
    allowed_ids = {item["review_id"] for item in document["items"]}
    allowed_decisions = set(document["decision_values"])
    allowed_reasons = set(document["reason_codes"])
    seen: set[str] = set()
    expected_suggestion = {
        "review_id",
        "suggested_decision",
        "reason_code",
        "comment",
        "reviewed_at",
    }
    decisions: Counter[str] = Counter()
    for suggestion in suggestions:
        if not isinstance(suggestion, dict) or set(suggestion) != expected_suggestion:
            raise Phase2AuditError("팀원 의견 suggestion 필드가 계약과 다릅니다.")
        review_id = suggestion.get("review_id")
        decision = suggestion.get("suggested_decision")
        reason = suggestion.get("reason_code")
        comment = suggestion.get("comment")
        if (
            not isinstance(review_id, str)
            or review_id not in allowed_ids
            or review_id in seen
        ):
            raise Phase2AuditError("팀원 의견 review_id가 큐 밖이거나 중복됐습니다.")
        if not isinstance(decision, str) or decision not in allowed_decisions:
            raise Phase2AuditError("팀원 의견 판정값이 올바르지 않습니다.")
        if decision == "accept":
            if reason is not None:
                raise Phase2AuditError("accept 팀원 의견에 reason code가 있습니다.")
        elif not isinstance(reason, str) or reason not in allowed_reasons:
            raise Phase2AuditError("팀원 의견 reason code가 올바르지 않습니다.")
        if (
            not isinstance(comment, str)
            or len(comment) > 2_000
            or FEEDBACK_CONTROL_PATTERN.search(comment)
            or (reason == "other" and not comment.strip())
        ):
            raise Phase2AuditError("팀원 의견 메모가 올바르지 않습니다.")
        if not _is_zoned_iso_timestamp(suggestion.get("reviewed_at")):
            raise Phase2AuditError("팀원 의견 reviewed_at이 올바르지 않습니다.")
        seen.add(review_id)
        decisions[str(decision)] += 1
    return {
        "status": "verified",
        "feedback": feedback_path.name,
        "package_id": document["package_id"],
        "build_id": document["build_id"],
        "reviewer_label": reviewer_label,
        "completed_units": len(suggestions),
        "total_units": document["unit_count"],
        "decision_counts": dict(sorted(decisions.items())),
        "advisory_only": True,
    }


def default_output(audit_version: str, build_id: str) -> Path:
    return REPO_ROOT.parent / (
        f"saju-review-share-{audit_version}-{build_id}-core150-ref150.zip"
    )


def build_archive(arguments: argparse.Namespace) -> dict[str, Any]:
    source_config = arguments.source_config.expanduser().resolve()
    policy = arguments.policy.expanduser().resolve()
    context = prepare_audit(
        REPO_ROOT,
        source_config,
        policy,
        arguments.audit_version,
        verify_raw=True,
    )
    if arguments.build != context["identity"]["build_id"]:
        historical = verify_historical_build(
            REPO_ROOT,
            audit_version=arguments.audit_version,
            build_id=arguments.build,
            implementation_commit=None,
        )
        if not historical["approved"]:
            raise Phase2AuditError("공유본은 승인된 과거 audit에서만 만들 수 있습니다.")
        context["paths"] = audit_paths(
            REPO_ROOT, context["policy"], arguments.build
        )
        build_manifest = _load_build_files(context)["build_manifest"]
        if sha256_file(policy) != build_manifest.get("policy_sha256"):
            raise Phase2AuditError("과거 audit policy와 현재 버전 파일이 다릅니다.")
        correction_path = context.get("correction_path")
        if correction_path is not None and sha256_file(
            correction_path
        ) != build_manifest.get("correction_manifest_sha256"):
            raise Phase2AuditError("과거 audit correction과 현재 버전 파일이 다릅니다.")
        context["identity"] = {
            **context["identity"],
            "build_id": arguments.build,
            "build_sha256": build_manifest["build_sha256"],
        }
    else:
        verify_audit(
            REPO_ROOT,
            source_config,
            policy,
            arguments.audit_version,
            verify_raw=False,
        )
    values = _load_build_files(context)
    if any(
        item.get("source") == "aihub_empathy" for item in values["queue"]
    ) and not arguments.confirm_aihub_authorized_reviewer:
        raise Phase2AuditError(
            "AI Hub 표본 포함에는 --confirm-aihub-authorized-reviewer가 필요합니다."
        )
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output is not None
        else default_output(arguments.audit_version, arguments.build).resolve()
    )
    if output.suffix.lower() != ".zip":
        raise Phase2AuditError("공유 산출물은 .zip 경로여야 합니다.")
    if output.is_relative_to(REPO_ROOT):
        raise Phase2AuditError("통제 데이터 공유 ZIP은 저장소 밖에 생성해야 합니다.")
    if not output.parent.is_dir():
        raise Phase2AuditError("공유 ZIP 상위 디렉터리가 없습니다.")
    sidecar = output.with_suffix(output.suffix + ".sha256")
    if output.exists() or sidecar.exists():
        raise Phase2AuditError("기존 공유 ZIP 또는 sidecar를 덮어쓸 수 없습니다.")
    projected = build_projected_items(
        values["queue"],
        lambda locator: _load_raw_record(REPO_ROOT, locator),
        context["correction_manifest"],
    )
    document, manifest_base = build_package_document(
        context, values, projected
    )
    previous_umask = os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".saju-review-share-", dir=output.parent
        ) as temporary:
            temporary_root = Path(temporary)
            package_name = output.stem
            package_root = temporary_root / package_name
            manifest = materialize_package(package_root, document, manifest_base)
            temporary_zip = temporary_root / output.name
            _zip_directory(package_root, temporary_zip)
            verified = verify_archive(temporary_zip)
            digest = sha256_file(temporary_zip)
            temporary_sidecar = temporary_root / sidecar.name
            _write_payload(
                temporary_sidecar, f"{digest}  {output.name}\n".encode("ascii")
            )
            os.replace(temporary_zip, output)
            os.replace(temporary_sidecar, sidecar)
            os.chmod(output, 0o600)
            os.chmod(sidecar, 0o600)
    except Exception:
        if output.exists() and not sidecar.exists():
            output.unlink()
        raise
    finally:
        os.umask(previous_umask)
    return {
        **verified,
        "status": "created_and_verified",
        "archive_path": output.as_posix(),
        "archive_sha256": digest,
        "sidecar_path": sidecar.as_posix(),
        "package_fingerprint": manifest["package_fingerprint"],
        "main_decision_ledger_modified": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2A 핵심 150건·참조 150건 팀원용 오프라인 검수 ZIP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="새 공유 ZIP을 생성한다.")
    build.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    build.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    build.add_argument("--audit-version", default="v1.2.0")
    build.add_argument("--build", required=True)
    build.add_argument("--output", type=Path)
    build.add_argument(
        "--confirm-aihub-authorized-reviewer",
        action="store_true",
        help="검수자의 AI Hub #86 열람 권한을 명시적으로 확인한다.",
    )
    verify = subparsers.add_parser("verify", help="기존 공유 ZIP을 검증한다.")
    verify.add_argument("--archive", type=Path, required=True)
    feedback = subparsers.add_parser(
        "verify-feedback", help="팀원이 반환한 advisory JSON을 검증한다."
    )
    feedback.add_argument("--archive", type=Path, required=True)
    feedback.add_argument("--feedback", type=Path, required=True)
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.command == "build":
        return build_archive(arguments)
    if arguments.command == "verify":
        return verify_archive(arguments.archive.expanduser().resolve())
    if arguments.command == "verify-feedback":
        return verify_feedback(
            arguments.archive.expanduser().resolve(),
            arguments.feedback.expanduser().resolve(),
        )
    raise Phase2AuditError(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        print(json.dumps(run(arguments), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (Phase1Error, Phase2AuditError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
