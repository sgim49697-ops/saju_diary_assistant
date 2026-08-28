# ssaju_policy_review.py - 고정 ssaju 구현과 canonical 학습데이터 정책을 결정론적으로 비교한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "configs/saju_calculation_policy.json"
CANONICAL_ROOT = REPO_ROOT / "data/derived/saju_1b_baseline/v1.1.0/build-a1a34616dd72"
CANONICAL_MANIFEST = CANONICAL_ROOT / "manifests/mix20k_v1.jsonl"
STAGING_ROOT = REPO_ROOT / "data/staging/saju_1b_baseline/v0.2.0/build-847088ee804d"
NEMOTRON_RECORDS = STAGING_ROOT / "records/nemotron_saju.jsonl"
BAZI_RECORDS = STAGING_ROOT / "records/bazi_sft.jsonl"
NEMOTRON_RAW_ROOT = (
    REPO_ROOT
    / "data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702-full-1m"
)
REPORT_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/ssaju-policy-review/v1.0.0"
PROBE_PATH = REPO_ROOT / "scripts/data/ssaju_runtime_probe.mjs"

EXTERNAL_REPOSITORY = "https://github.com/golbin/ssaju.git"
EXTERNAL_REVISION = "07b608a778be6dac8669e04b9ab794c441959208"
CANONICAL_BUILD_ID = "build-a1a34616dd72"
CANONICAL_MIX_SHA256 = (
    "a61c16dc65ad24805b293ad50404d519c68d1ae844419b18c9e1538ea7a5bc3a"
)
STAGING_BUILD_ID = "build-847088ee804d"
STAGING_ARTIFACT_SHA256 = {
    "records/nemotron_saju.jsonl": (
        "0242bad3b408e9143813bb94fc84ad2911146f8262f93a7a924f20c06d32d132"
    ),
    "records/bazi_sft.jsonl": (
        "80654a812d41dffeea80ea72a02c3101d24bc5ee694e4229639fcd12960f2965"
    ),
}
EXPECTED_SELECTED_COUNTS = {"nemotron_saju": 11_000, "bazi_sft": 5_000}
EXPECTED_EXTERNAL_FILE_SHA256 = {
    "LICENSE": "237d62618b9d436054ad0dfdd53a93b6cdea1a3d4b62abb313b9f455c8c7e48a",
    "README.md": "ad2e0f348fdc7100172ba63b09354e2762271106755208ec271c7c25163fe83d",
    "package-lock.json": (
        "e5a5aca7a2360db0365364e24fe279f27351569e7eb0fd843c003c2ca5aa5de8"
    ),
    "package.json": (
        "a2450e41149c2e1f558181f4a42f13b86794319d1006db8248c66bd0d1f4d206"
    ),
    "src/analyze.ts": (
        "5238c3e2dfbf2358406d32cc0e36bd83de618e27f49e8cd6938cd64c128fc7c3"
    ),
    "src/constants.ts": (
        "4402529a5efe252d89377782ef92a2fd7d62d162c33e73eb2c38e679aaac60ad"
    ),
    "src/format.ts": (
        "df0cb2b40284d3e794debf1a235200fc8206fb9a29061787bd100f40b4bd761d"
    ),
    "src/manse.ts": (
        "ba1c32fb9f3ea02aac80e6fe7de79b4c9439fe01639562fae2ad0f1b65b6bfec"
    ),
    "tests/calculateSaju.test.ts": (
        "22c3e90e19027a82e75d21f4ee3f2b92fd70e5ddd363c28d509d93ac31df3971"
    ),
}

STEMS = tuple("甲乙丙丁戊己庚辛壬癸")
BRANCHES = tuple("子丑寅卯辰巳午未申酉戌亥")
PILLARS = ("year", "month", "day", "hour")
PILLAR_KO = {"year": "년주", "month": "월주", "day": "일주", "hour": "시주"}
STEM_ELEMENT = {
    "甲": "목",
    "乙": "목",
    "丙": "화",
    "丁": "화",
    "戊": "토",
    "己": "토",
    "庚": "금",
    "辛": "금",
    "壬": "수",
    "癸": "수",
}
BRANCH_ELEMENT = {
    "子": "수",
    "丑": "토",
    "寅": "목",
    "卯": "목",
    "辰": "토",
    "巳": "화",
    "午": "화",
    "未": "토",
    "申": "금",
    "酉": "금",
    "戌": "토",
    "亥": "수",
}
STEM_YINYANG = dict(zip(STEMS, ("양", "음") * 5, strict=True))
BRANCH_YINYANG = dict(zip(BRANCHES, ("양", "음") * 6, strict=True))
MAIN_HIDDEN_STEM = {
    "子": "癸",
    "丑": "己",
    "寅": "甲",
    "卯": "乙",
    "辰": "戊",
    "巳": "丙",
    "午": "丁",
    "未": "己",
    "申": "庚",
    "酉": "辛",
    "戌": "戊",
    "亥": "壬",
}
ELEMENT_GENERATES = {"목": "화", "화": "토", "토": "금", "금": "수", "수": "목"}
ELEMENT_CONTROLS = {"목": "토", "화": "금", "토": "수", "금": "목", "수": "화"}
SUPPORT_ELEMENT = {"목": "수", "화": "목", "토": "화", "금": "토", "수": "금"}
ATTACK_ELEMENT = {"목": "금", "화": "수", "토": "목", "금": "화", "수": "토"}
TWELVE_STAGE_SEQUENCE = (
    "장생",
    "목욕",
    "관대",
    "건록",
    "제왕",
    "쇠",
    "병",
    "사",
    "묘",
    "절",
    "태",
    "양",
)
TWELVE_STAGE_START_BRANCH = {
    "甲": "亥",
    "乙": "午",
    "丙": "寅",
    "丁": "酉",
    "戊": "寅",
    "己": "酉",
    "庚": "巳",
    "辛": "子",
    "壬": "申",
    "癸": "卯",
}
ELEMENT_KEY_NORMALIZATION = {
    "木": "목",
    "火": "화",
    "土": "토",
    "金": "금",
    "水": "수",
    "목": "목",
    "화": "화",
    "토": "토",
    "금": "금",
    "수": "수",
}
CHART_PATTERN = re.compile(
    r"사주 원국: 년주 (?P<year>[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]) "
    r"월주 (?P<month>[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]) "
    r"일주 (?P<day>[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]) "
    r"시주 (?P<hour>[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥])"
)
TEN_GOD_PATTERN = re.compile(
    r"십신: 년주 천간 (?P<year_stem>[^,;\n]+), 지지 (?P<year_branch>[^;\n]+); "
    r"월주 천간 (?P<month_stem>[^,;\n]+), 지지 (?P<month_branch>[^;\n]+); "
    r"일주 천간 (?P<day_stem>[^,;\n]+), 지지 (?P<day_branch>[^;\n]+); "
    r"시주 천간 (?P<hour_stem>[^,;\n]+), 지지 (?P<hour_branch>[^;\n]+)"
)
ELEMENT_PATTERN = re.compile(r"오행 분포: (?P<value>\{[^\n]+\})")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class SsajuReviewError(RuntimeError):
    """ssaju 정책 비교의 입력·검증·불변성 오류."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def require_regular_file(path: Path, label: str) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise SsajuReviewError(f"{label} 파일을 읽을 수 없습니다: {path}") from exc
    if not stat.S_ISREG(mode):
        raise SsajuReviewError(
            f"{label}은 symlink가 아닌 일반 파일이어야 합니다: {path}"
        )


def require_sha256(path: Path, expected: str, label: str) -> str:
    require_regular_file(path, label)
    actual = sha256_file(path)
    if actual != expected:
        raise SsajuReviewError(
            f"{label} SHA-256 불일치: expected={expected}, actual={actual}"
        )
    return actual


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    require_regular_file(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SsajuReviewError(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise SsajuReviewError(f"{label} 최상위 값은 JSON object여야 합니다.")
    return value


def safe_child(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise SsajuReviewError(f"안전하지 않은 상대경로입니다: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / rel).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise SsajuReviewError(f"기준 경로 밖 파일입니다: {relative}")
    return resolved


def run_command(
    command: list[str],
    *,
    cwd: Path,
    allowed_returncodes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode not in allowed_returncodes:
        stderr = result.stderr.strip()[-2_000:]
        raise SsajuReviewError(
            f"명령 실패({result.returncode}): {' '.join(command)}\n{stderr}"
        )
    return result


def validate_policy() -> tuple[dict[str, Any], str]:
    policy = load_json_object(POLICY_PATH, "계산 정책")
    if (
        policy.get("schema_version") != "1.0.0"
        or policy.get("status") != "draft_not_runtime_approved"
        or policy.get("scope", {}).get("runtime_enabled") is not False
        or policy.get("scope", {}).get("training_data_mutation_allowed") is not False
    ):
        raise SsajuReviewError(
            "계산 정책의 draft·비변경 안전 계약이 올바르지 않습니다."
        )
    external = policy.get("external_reference")
    if not isinstance(external, dict) or external.get("revision") != EXTERNAL_REVISION:
        raise SsajuReviewError("계산 정책의 ssaju revision이 고정값과 다릅니다.")
    if external.get("focus_file_sha256") != EXPECTED_EXTERNAL_FILE_SHA256:
        raise SsajuReviewError("계산 정책의 ssaju focus file hash가 고정값과 다릅니다.")
    branch = policy.get("branch_ten_god_contract")
    if (
        not isinstance(branch, dict)
        or branch.get("main_hidden_stem") != MAIN_HIDDEN_STEM
        or branch.get("validator_mode") != "advisory"
        or branch.get("immutable_builds_must_not_change") is not True
    ):
        raise SsajuReviewError("지지 십신 draft 계약이 올바르지 않습니다.")
    fields = policy.get("fields")
    required_fields = {
        "pillars",
        "stem_branch_identity",
        "yin_yang_elements",
        "surface_five_elements",
        "hidden_stems",
        "stem_ten_gods",
        "branch_ten_gods",
        "gongmang",
        "twelve_stages",
        "branch_relations",
        "luck_cycle_ganzhi",
        "day_strength",
        "geukguk",
        "yongsin",
        "relation_priority",
        "automatic_interpretation",
        "remedy_advice",
    }
    if not isinstance(fields, dict) or set(fields) != required_fields:
        raise SsajuReviewError("계산 정책 field flag 집합이 고정 계약과 다릅니다.")
    for weak_field in (
        "day_strength",
        "geukguk",
        "yongsin",
        "relation_priority",
        "automatic_interpretation",
        "remedy_advice",
    ):
        if (
            fields[weak_field].get("qa_gold_candidate") is not False
            or fields[weak_field].get("validator_mode") != "disabled"
        ):
            raise SsajuReviewError(
                f"휴리스틱 field가 Gold에서 차단되지 않았습니다: {weak_field}"
            )
    return policy, sha256_file(POLICY_PATH)


def validate_canonical_inputs() -> dict[str, str]:
    canonical_build = load_json_object(
        CANONICAL_ROOT / "build_manifest.json", "canonical build manifest"
    )
    if (
        canonical_build.get("build_id") != CANONICAL_BUILD_ID
        or canonical_build.get("training_promotion_allowed") is not True
        or canonical_build.get("phase5_training_performed") is not False
    ):
        raise SsajuReviewError("canonical Phase 4 부모 계약이 예상과 다릅니다.")
    require_sha256(CANONICAL_MANIFEST, CANONICAL_MIX_SHA256, "canonical MIX20K")

    staging_build = load_json_object(
        STAGING_ROOT / "build_manifest.json", "staging build manifest"
    )
    if staging_build.get("build_id") != STAGING_BUILD_ID:
        raise SsajuReviewError("staging build ID가 고정값과 다릅니다.")
    for relative, expected in STAGING_ARTIFACT_SHA256.items():
        if staging_build.get("artifact_sha256", {}).get(relative) != expected:
            raise SsajuReviewError(
                f"staging manifest artifact hash가 다릅니다: {relative}"
            )
        require_sha256(STAGING_ROOT / relative, expected, relative)

    return {
        "canonical_build_manifest_sha256": sha256_file(
            CANONICAL_ROOT / "build_manifest.json"
        ),
        "canonical_mix20k_sha256": CANONICAL_MIX_SHA256,
        "staging_build_manifest_sha256": sha256_file(
            STAGING_ROOT / "build_manifest.json"
        ),
        "nemotron_records_sha256": STAGING_ARTIFACT_SHA256[
            "records/nemotron_saju.jsonl"
        ],
        "bazi_records_sha256": STAGING_ARTIFACT_SHA256["records/bazi_sft.jsonl"],
    }


def validate_raw_bundle() -> tuple[list[Path], dict[str, Any]]:
    manifest_path = NEMOTRON_RAW_ROOT / "SOURCE_MANIFEST.json"
    manifest = load_json_object(manifest_path, "Nemotron raw source manifest")
    if (
        manifest.get("source") != "nemotron_saju"
        or manifest.get("revision") != "ffb934248746a2dea64ef771c0d86e1743d25702"
        or manifest.get("access_scope") != "public"
    ):
        raise SsajuReviewError("Nemotron raw source manifest 계약이 예상과 다릅니다.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise SsajuReviewError("Nemotron raw source file 목록이 없습니다.")
    parquet_paths: list[Path] = []
    normalized_files: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise SsajuReviewError(
                "Nemotron raw source file entry가 object가 아닙니다."
            )
        relative = entry.get("path")
        expected = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or SHA256_PATTERN.fullmatch(expected) is None
        ):
            raise SsajuReviewError(
                "Nemotron raw source file metadata가 올바르지 않습니다."
            )
        path = safe_child(NEMOTRON_RAW_ROOT, relative)
        require_sha256(path, expected, f"Nemotron raw {relative}")
        normalized_files.append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": expected}
        )
        if relative.endswith(".parquet"):
            parquet_paths.append(path)
    if len(parquet_paths) != 20:
        raise SsajuReviewError(
            f"Nemotron raw parquet 수가 20개가 아닙니다: {len(parquet_paths)}"
        )
    return parquet_paths, {
        "source_manifest_sha256": sha256_file(manifest_path),
        "verified_file_count": len(normalized_files),
        "verified_parquet_count": len(parquet_paths),
        "file_set_sha256": sha256_bytes(canonical_json_bytes(normalized_files)),
    }


def validate_external_checkout(ssaju_root: Path, *, prepare: bool) -> dict[str, Any]:
    root = ssaju_root.resolve()
    if root == REPO_ROOT.resolve() or root.is_relative_to(REPO_ROOT.resolve()):
        raise SsajuReviewError(
            "ssaju checkout은 현재 프로젝트 밖 임시 경로여야 합니다."
        )
    if not (root / ".git").exists():
        raise SsajuReviewError("ssaju 경로가 Git checkout이 아닙니다.")
    head = run_command(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    if head != EXTERNAL_REVISION:
        raise SsajuReviewError(f"ssaju HEAD가 고정 revision과 다릅니다: {head}")
    remote = run_command(
        ["git", "remote", "get-url", "origin"], cwd=root
    ).stdout.strip()
    allowed_remotes = {
        EXTERNAL_REPOSITORY,
        "https://github.com/golbin/ssaju",
        "git@github.com:golbin/ssaju.git",
    }
    if remote not in allowed_remotes:
        raise SsajuReviewError(f"ssaju origin이 승인 저장소가 아닙니다: {remote}")
    status = run_command(["git", "status", "--porcelain"], cwd=root).stdout.strip()
    if status:
        raise SsajuReviewError("ssaju source checkout이 clean하지 않습니다.")
    for relative, expected in EXPECTED_EXTERNAL_FILE_SHA256.items():
        require_sha256(safe_child(root, relative), expected, f"ssaju {relative}")
    package = load_json_object(root / "package.json", "ssaju package.json")
    if (
        package.get("name") != "ssaju"
        or package.get("version") != "0.2.0"
        or package.get("license") != "MIT"
        or package.get("dependencies") not in (None, {})
    ):
        raise SsajuReviewError("ssaju package metadata가 검토 계약과 다릅니다.")

    checks: dict[str, Any] = {
        "npm_ci": "not_run",
        "tests": "not_run",
        "test_count": None,
        "typecheck": "not_run",
        "build": "not_run",
        "npm_audit": "not_run",
        "npm_audit_vulnerabilities": None,
    }
    if prepare:
        run_command(["npm", "ci"], cwd=root)
        checks["npm_ci"] = "passed"
        test_result = run_command(["npm", "test"], cwd=root)
        checks["tests"] = "passed"
        count_match = re.search(r"# tests (\d+)", test_result.stdout)
        checks["test_count"] = int(count_match.group(1)) if count_match else None
        if checks["test_count"] != 21:
            raise SsajuReviewError(
                f"ssaju test count가 검토 시점의 21건과 다릅니다: {checks['test_count']}"
            )
        run_command(["npm", "run", "typecheck"], cwd=root)
        checks["typecheck"] = "passed"
        run_command(["npm", "run", "build"], cwd=root)
        checks["build"] = "passed"
        audit_result = run_command(
            ["npm", "audit", "--json"],
            cwd=root,
            allowed_returncodes=frozenset({0, 1}),
        )
        try:
            audit = json.loads(audit_result.stdout)
            vulnerabilities = audit["metadata"]["vulnerabilities"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SsajuReviewError("npm audit JSON을 해석할 수 없습니다.") from exc
        checks["npm_audit"] = (
            "completed_with_findings" if audit_result.returncode else "passed"
        )
        checks["npm_audit_vulnerabilities"] = vulnerabilities

    dist = root / "dist/index.mjs"
    require_regular_file(dist, "ssaju dist/index.mjs")
    node_version = run_command(["node", "--version"], cwd=root).stdout.strip()
    npm_version = run_command(["npm", "--version"], cwd=root).stdout.strip()
    commit_date = run_command(
        ["git", "show", "-s", "--format=%cI", "HEAD"], cwd=root
    ).stdout.strip()
    return {
        "repository": EXTERNAL_REPOSITORY,
        "revision": head,
        "commit_date": commit_date,
        "package_version": "0.2.0",
        "runtime_dependencies": 0,
        "license": "MIT",
        "copyright_notice": "Copyright (c) 2026 Jin",
        "focus_file_sha256": EXPECTED_EXTERNAL_FILE_SHA256,
        "dist_index_mjs_sha256": sha256_file(dist),
        "dist_index_mjs": dist,
        "node_version": node_version,
        "npm_version": npm_version,
        "checks": checks,
    }


def load_selection() -> dict[str, dict[str, str]]:
    selected: dict[str, dict[str, str]] = {
        axis: {} for axis in EXPECTED_SELECTED_COUNTS
    }
    seen_ids: set[str] = set()
    with CANONICAL_MANIFEST.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SsajuReviewError(
                    f"canonical MIX20K JSONL 오류: line={line_number}"
                ) from exc
            record_id = value.get("id")
            axis = value.get("mix_axis")
            record_hash = value.get("record_sha256")
            if (
                not isinstance(record_id, str)
                or record_id in seen_ids
                or not isinstance(axis, str)
                or not isinstance(record_hash, str)
                or SHA256_PATTERN.fullmatch(record_hash) is None
            ):
                raise SsajuReviewError(
                    f"canonical MIX20K identity 오류: line={line_number}"
                )
            seen_ids.add(record_id)
            if axis in selected:
                selected[axis][record_id] = record_hash
    if len(seen_ids) != 20_000:
        raise SsajuReviewError(
            f"canonical MIX20K 고유 ID가 20,000개가 아닙니다: {len(seen_ids)}"
        )
    actual_counts = {axis: len(values) for axis, values in selected.items()}
    if actual_counts != EXPECTED_SELECTED_COUNTS:
        raise SsajuReviewError(
            f"canonical 비교축 수량이 고정 계약과 다릅니다: {actual_counts}"
        )
    return selected


def load_selected_records(
    path: Path, expected: dict[str, str], label: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    found: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SsajuReviewError(
                    f"{label} JSONL 오류: line={line_number}"
                ) from exc
            record_id = value.get("id")
            if record_id not in expected:
                continue
            if record_id in found:
                raise SsajuReviewError(f"{label} selected ID가 중복입니다: {record_id}")
            actual_hash = sha256_bytes(canonical_json_bytes(value))
            if actual_hash != expected[record_id]:
                raise SsajuReviewError(
                    f"{label} record hash가 canonical manifest와 다릅니다: {record_id}"
                )
            found.add(record_id)
            records.append(value)
    missing = set(expected) - found
    if missing:
        raise SsajuReviewError(f"{label} selected record가 {len(missing)}건 없습니다.")
    records.sort(key=lambda value: value["id"])
    return records


def ten_god_from_attributes(
    day_element: str, day_yinyang: str, other_element: str, other_yinyang: str
) -> str:
    same_polarity = day_yinyang == other_yinyang
    if day_element == other_element:
        return "비견" if same_polarity else "겁재"
    if ELEMENT_GENERATES[day_element] == other_element:
        return "식신" if same_polarity else "상관"
    if ELEMENT_CONTROLS[day_element] == other_element:
        return "편재" if same_polarity else "정재"
    if ELEMENT_CONTROLS[other_element] == day_element:
        return "편관" if same_polarity else "정관"
    if ELEMENT_GENERATES[other_element] == day_element:
        return "편인" if same_polarity else "정인"
    raise SsajuReviewError("오행 생극 관계를 판정할 수 없습니다.")


def stem_ten_god(day_stem: str, other_stem: str) -> str:
    return ten_god_from_attributes(
        STEM_ELEMENT[day_stem],
        STEM_YINYANG[day_stem],
        STEM_ELEMENT[other_stem],
        STEM_YINYANG[other_stem],
    )


def surface_branch_ten_god(day_stem: str, branch: str) -> str:
    return ten_god_from_attributes(
        STEM_ELEMENT[day_stem],
        STEM_YINYANG[day_stem],
        BRANCH_ELEMENT[branch],
        BRANCH_YINYANG[branch],
    )


def hidden_stem_branch_ten_god(day_stem: str, branch: str) -> str:
    return stem_ten_god(day_stem, MAIN_HIDDEN_STEM[branch])


def normalize_current_stem_label(value: str) -> str:
    return "비견" if value == "본원(일간)" else value


def parse_nemotron_record(
    record: dict[str, Any],
) -> tuple[dict[str, str], dict[str, dict[str, str]], dict[str, int]]:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise SsajuReviewError("Nemotron messages가 list가 아닙니다.")
    user_messages = [
        item.get("content")
        for item in messages
        if isinstance(item, dict) and item.get("role") == "user"
    ]
    if len(user_messages) != 1 or not isinstance(user_messages[0], str):
        raise SsajuReviewError("Nemotron user message가 정확히 1개가 아닙니다.")
    content = user_messages[0]
    chart_match = CHART_PATTERN.search(content)
    ten_god_match = TEN_GOD_PATTERN.search(content)
    element_match = ELEMENT_PATTERN.search(content)
    if chart_match is None or ten_god_match is None or element_match is None:
        raise SsajuReviewError("Nemotron 구조화 사주 필드를 파싱할 수 없습니다.")
    chart = chart_match.groupdict()
    meta = record.get("meta")
    if not isinstance(meta, dict) or meta.get("chart_signature") != "".join(
        chart[pillar] for pillar in PILLARS
    ):
        raise SsajuReviewError("Nemotron chart signature와 user message가 다릅니다.")
    ten_gods: dict[str, dict[str, str]] = {}
    for pillar in PILLARS:
        ten_gods[pillar] = {
            "stem": ten_god_match.group(f"{pillar}_stem").strip(),
            "branch": ten_god_match.group(f"{pillar}_branch").strip(),
        }
    try:
        raw_elements = json.loads(element_match.group("value"))
    except json.JSONDecodeError as exc:
        raise SsajuReviewError("Nemotron 오행 분포 JSON을 파싱할 수 없습니다.") from exc
    if not isinstance(raw_elements, dict):
        raise SsajuReviewError("Nemotron 오행 분포가 object가 아닙니다.")
    elements: dict[str, int] = {
        element: 0 for element in ("목", "화", "토", "금", "수")
    }
    for key, count in raw_elements.items():
        normalized = ELEMENT_KEY_NORMALIZATION.get(key)
        if normalized is None or not isinstance(count, int):
            raise SsajuReviewError("Nemotron 오행 분포 key/value가 올바르지 않습니다.")
        elements[normalized] = count
    return chart, ten_gods, elements


def chart_elements(chart: dict[str, str]) -> dict[str, int]:
    counts = {element: 0 for element in ("목", "화", "토", "금", "수")}
    for pillar in PILLARS:
        stem, branch = chart[pillar]
        counts[STEM_ELEMENT[stem]] += 1
        counts[BRANCH_ELEMENT[branch]] += 1
    return counts


def make_conflict_samples(
    candidates: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    selected_record_keys: set[str] = set()
    for anchor in ("子", "巳", "午", "亥"):
        ordered = sorted(
            candidates[anchor],
            key=lambda item: hashlib.sha256(
                f"ssaju-policy-review-v1|{anchor}|{item['_record_key']}".encode()
            ).hexdigest(),
        )
        taken = 0
        for item in ordered:
            record_key = item["_record_key"]
            if record_key in selected_record_keys:
                continue
            selected_record_keys.add(record_key)
            sample_id = (
                "ssaju-conflict-"
                + hashlib.sha256(f"public-sample-v1|{record_key}".encode()).hexdigest()[
                    :16
                ]
            )
            samples.append(
                {
                    "sample_id": sample_id,
                    "chart": item["chart"],
                    "day_stem": item["day_stem"],
                    "anchor_branch": anchor,
                    "conflicts": item["conflicts"],
                }
            )
            taken += 1
            if taken == 25:
                break
        if taken != 25:
            raise SsajuReviewError(
                f"{anchor} 충돌 고유 표본을 25건 선택할 수 없습니다."
            )
    if len(samples) != 100 or len({sample["sample_id"] for sample in samples}) != 100:
        raise SsajuReviewError("충돌 표본은 정확히 고유 100건이어야 합니다.")
    return samples


def compare_nemotron(
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, str]]]:
    stem_mismatches = 0
    current_branch_mismatches = 0
    element_row_mismatches = 0
    ssaju_field_conflicts = 0
    conflict_rows = 0
    conflict_by_branch: Counter[str] = Counter()
    conflict_by_pillar: Counter[str] = Counter()
    candidates: dict[str, list[dict[str, Any]]] = {
        branch: [] for branch in ("子", "巳", "午", "亥")
    }
    charts_by_source_hash: dict[str, dict[str, str]] = {}

    for record in records:
        chart, labels, elements = parse_nemotron_record(record)
        if elements != chart_elements(chart):
            element_row_mismatches += 1
        day_stem = chart["day"][0]
        conflicts: list[dict[str, Any]] = []
        for pillar in PILLARS:
            stem, branch = chart[pillar]
            expected_stem = stem_ten_god(day_stem, stem)
            if normalize_current_stem_label(labels[pillar]["stem"]) != expected_stem:
                stem_mismatches += 1
            current_branch = surface_branch_ten_god(day_stem, branch)
            if labels[pillar]["branch"] != current_branch:
                current_branch_mismatches += 1
            reference_branch = hidden_stem_branch_ten_god(day_stem, branch)
            if labels[pillar]["branch"] != reference_branch:
                ssaju_field_conflicts += 1
                conflict_by_branch[branch] += 1
                conflict_by_pillar[pillar] += 1
                conflicts.append(
                    {
                        "pillar": pillar,
                        "branch": branch,
                        "current_policy_id": "branch_surface_element_yinyang_v1",
                        "current_ten_god": labels[pillar]["branch"],
                        "ssaju_policy_id": "branch_main_hidden_stem_v1",
                        "ssaju_main_hidden_stem": MAIN_HIDDEN_STEM[branch],
                        "ssaju_ten_god": reference_branch,
                    }
                )
        if conflicts:
            conflict_rows += 1
            item = {
                "_record_key": record["id"],
                "chart": {pillar: chart[pillar] for pillar in PILLARS},
                "day_stem": day_stem,
                "conflicts": conflicts,
            }
            for branch in sorted({conflict["branch"] for conflict in conflicts}):
                if branch in candidates:
                    candidates[branch].append(item)
        source_hash = record["id"].split(":", 1)[-1]
        if SHA256_PATTERN.fullmatch(source_hash) is None:
            raise SsajuReviewError("Nemotron record ID suffix가 SHA-256이 아닙니다.")
        charts_by_source_hash[source_hash] = chart

    if (
        stem_mismatches != 0
        or current_branch_mismatches != 0
        or element_row_mismatches != 0
    ):
        raise SsajuReviewError(
            "현재 Nemotron deterministic label이 재현되지 않았습니다: "
            f"stem={stem_mismatches}, branch={current_branch_mismatches}, "
            f"elements={element_row_mismatches}"
        )
    samples = make_conflict_samples(candidates)
    result = {
        "selected_rows": len(records),
        "pillar_fields": len(records) * 4,
        "surface_five_element_row_mismatches": element_row_mismatches,
        "stem_ten_god_mismatches_against_ssaju_table": stem_mismatches,
        "branch_ten_god_mismatches_against_current_surface_policy": (
            current_branch_mismatches
        ),
        "branch_ten_god_conflict_rows_against_ssaju_main_hidden_stem": conflict_rows,
        "branch_ten_god_conflict_fields_against_ssaju_main_hidden_stem": (
            ssaju_field_conflicts
        ),
        "conflict_fields_by_branch": dict(sorted(conflict_by_branch.items())),
        "conflict_fields_by_pillar": {
            pillar: conflict_by_pillar[pillar] for pillar in PILLARS
        },
        "current_policy_inference_basis": (
            "44,000개 지지 label을 branch_surface_element_yinyang_v1으로 전수 재계산"
        ),
        "upstream_generator_source_available": False,
    }
    return result, samples, charts_by_source_hash


def twelve_stage(day_stem: str, branch: str) -> str:
    branch_index = BRANCHES.index(branch)
    start_index = BRANCHES.index(TWELVE_STAGE_START_BRANCH[day_stem])
    if STEM_YINYANG[day_stem] == "양":
        offset = (branch_index - start_index) % 12
    else:
        offset = (start_index - branch_index) % 12
    return TWELVE_STAGE_SEQUENCE[offset]


def ssaju_strength(chart_signature: str) -> tuple[str, int]:
    if len(chart_signature) != 8:
        raise SsajuReviewError(
            f"bazi chart signature 길이가 8이 아닙니다: {chart_signature}"
        )
    chart = {
        pillar: chart_signature[index * 2 : index * 2 + 2]
        for index, pillar in enumerate(PILLARS)
    }
    counts = chart_elements(chart)
    day_stem = chart["day"][0]
    day_element = STEM_ELEMENT[day_stem]
    month_branch = chart["month"][1]
    score = 50
    if BRANCH_ELEMENT[month_branch] == day_element:
        score += 20
    score += counts[day_element] * 10
    score += counts[SUPPORT_ELEMENT[day_element]] * 8
    score -= counts[ATTACK_ELEMENT[day_element]] * 8
    month_stage = twelve_stage(day_stem, month_branch)
    if month_stage in {"건록", "제왕"}:
        score += 15
    if month_stage in {"사", "절", "묘"}:
        score -= 15
    if score >= 70:
        return "strong", score
    if score <= 30:
        return "weak", score
    return "neutral", score


def bazi_current_strength(record: dict[str, Any]) -> str:
    meta = record.get("meta")
    if not isinstance(meta, dict):
        raise SsajuReviewError("bazi meta가 object가 아닙니다.")
    rule_ids = meta.get("validated_rule_ids")
    if not isinstance(rule_ids, list) or not all(
        isinstance(value, str) for value in rule_ids
    ):
        raise SsajuReviewError("bazi validated_rule_ids가 문자열 list가 아닙니다.")
    if "day_master_strong" in rule_ids and "day_master_weak" in rule_ids:
        raise SsajuReviewError("bazi 강·약 rule이 동시에 있습니다.")
    if "day_master_strong" in rule_ids:
        return "strong"
    if "day_master_weak" in rule_ids:
        return "weak"
    return "neutral"


def compare_bazi(records: list[dict[str, Any]]) -> dict[str, Any]:
    charts: dict[str, str] = {}
    repeats: Counter[str] = Counter()
    for record in records:
        meta = record.get("meta")
        if not isinstance(meta, dict) or not isinstance(
            meta.get("chart_signature"), str
        ):
            raise SsajuReviewError("bazi chart_signature가 없습니다.")
        chart = meta["chart_signature"]
        current = bazi_current_strength(record)
        if chart in charts and charts[chart] != current:
            raise SsajuReviewError("같은 bazi 명식의 현재 강약 class가 서로 다릅니다.")
        charts[chart] = current
        repeats[chart] += 1
    if len(charts) != 1_250 or set(repeats.values()) != {4}:
        raise SsajuReviewError(
            f"bazi canonical 반복 계약이 1,250명식×4가 아닙니다: {len(charts)}"
        )
    matrix = {
        current: {reference: 0 for reference in ("neutral", "strong", "weak")}
        for current in ("neutral", "strong", "weak")
    }
    conflicts = 0
    score_min: int | None = None
    score_max: int | None = None
    for chart, current in sorted(charts.items()):
        reference, score = ssaju_strength(chart)
        matrix[current][reference] += 1
        if current != reference:
            conflicts += 1
        score_min = score if score_min is None else min(score_min, score)
        score_max = score if score_max is None else max(score_max, score)
    return {
        "selected_rows": len(records),
        "unique_charts": len(charts),
        "rows_per_chart": 4,
        "comparison_matrix": matrix,
        "conflict_unique_charts": conflicts,
        "conflict_unique_chart_rate": round(conflicts / len(charts), 6),
        "conflict_repeated_rows": conflicts * 4,
        "ssaju_score_range": {"minimum": score_min, "maximum": score_max},
        "classification": "heuristic_vs_heuristic_not_correction_truth",
    }


def load_raw_runtime_records(
    parquet_paths: list[Path], charts_by_source_hash: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    try:
        import duckdb
    except ImportError as exc:
        raise SsajuReviewError(
            "raw 원국 진단에는 프로젝트 .venv-data의 duckdb가 필요합니다."
        ) from exc
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute("CREATE TABLE wanted (source_hash VARCHAR PRIMARY KEY)")
        connection.executemany(
            "INSERT INTO wanted VALUES (?)",
            [(value,) for value in sorted(charts_by_source_hash)],
        )
        rows = connection.execute(
            """
            SELECT sha256(raw.uuid) AS source_hash,
                   raw.birth_datetime_synth,
                   raw.birth_longitude_e,
                   raw.last_datetime,
                   raw.saju_pillars
            FROM read_parquet(?) AS raw
            INNER JOIN wanted ON sha256(raw.uuid) = wanted.source_hash
            ORDER BY source_hash
            """,
            [[str(path) for path in parquet_paths]],
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != len(charts_by_source_hash):
        raise SsajuReviewError(
            "Nemotron raw join 수량이 selected canonical 수량과 다릅니다: "
            f"{len(rows)} != {len(charts_by_source_hash)}"
        )
    output: list[dict[str, Any]] = []
    for source_hash, birth, longitude, last, raw_pillars in rows:
        try:
            parsed = json.loads(raw_pillars)
            pillars = {
                pillar: parsed[pillar]["stem_hanja"] + parsed[pillar]["branch_hanja"]
                for pillar in PILLARS
            }
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SsajuReviewError(
                "Nemotron raw saju_pillars를 파싱할 수 없습니다."
            ) from exc
        if pillars != charts_by_source_hash[source_hash]:
            raise SsajuReviewError(
                "Nemotron raw 원국과 canonical 구조화 원국이 다릅니다."
            )
        if (
            not isinstance(birth, str)
            or not isinstance(last, str)
            or not isinstance(longitude, (int, float))
        ):
            raise SsajuReviewError(
                "Nemotron raw runtime 입력 필드가 올바르지 않습니다."
            )
        output.append(
            {
                "birth_datetime_synth": birth,
                "birth_longitude_e": longitude,
                "last_datetime": last,
                "pillars": pillars,
            }
        )
    return output


def run_runtime_probe(
    dist_path: Path, raw_records: list[dict[str, Any]]
) -> dict[str, Any]:
    require_regular_file(PROBE_PATH, "ssaju runtime probe")
    result = subprocess.run(
        ["node", str(PROBE_PATH), dist_path.resolve().as_uri()],
        cwd=REPO_ROOT,
        input=json.dumps({"records": raw_records}, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise SsajuReviewError(
            f"ssaju runtime probe 실패({result.returncode}): {result.stderr.strip()[-2000:]}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SsajuReviewError("ssaju runtime probe JSON을 읽을 수 없습니다.") from exc
    if not isinstance(payload, dict):
        raise SsajuReviewError("ssaju runtime probe 결과가 object가 아닙니다.")
    return payload


def field_comparison_table(
    nemotron: dict[str, Any],
    bazi: dict[str, Any],
    runtime_probe: dict[str, Any],
) -> list[dict[str, Any]]:
    hybrid = runtime_probe["birth_diagnostics"]["documented_policy_hybrid"]
    return [
        {
            "field": "원국 4주",
            "current_dataset_policy": "Nemotron 원천 제공 원국; 생성기 소스 부재로 세부 역법은 미확정",
            "ssaju_policy": "근사 태양황경 절입·Asia/Seoul·선택적 경도 평균태양시",
            "conflict_rows": hybrid["row_conflicts"],
            "comparison_scope": "11,000행 documented-policy hybrid 진단; ssaju를 정답으로 간주하지 않음",
            "recommended_runtime_policy": "KASI·IANA 기반 독립 Gold fixture 확정 전 채택 보류",
            "existing_dataset_modification": "자동 수정 금지",
        },
        {
            "field": "천간·지지",
            "current_dataset_policy": "원국 문자열의 4주 8자",
            "ssaju_policy": "동일 10천간·12지지 상수",
            "conflict_rows": 0,
            "comparison_scope": "11,000개 구조화 원국의 문자 집합·60갑자 소속",
            "recommended_runtime_policy": "공통 상수 채택 후보",
            "existing_dataset_modification": "불필요",
        },
        {
            "field": "음양·오행(표면 8자)",
            "current_dataset_policy": "천간·지지 자체 오행을 각 1회 집계",
            "ssaju_policy": "천간·지지 자체 오행을 각 1회 집계",
            "conflict_rows": nemotron["surface_five_element_row_mismatches"],
            "comparison_scope": "Nemotron 11,000행",
            "recommended_runtime_policy": "표면 집계임을 명시해 채택 후보",
            "existing_dataset_modification": "불필요",
        },
        {
            "field": "지장간",
            "current_dataset_policy": "구조화 학습 label 없음",
            "ssaju_policy": "지지별 여기·중기·정기 고정표",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재",
            "recommended_runtime_policy": "전문가 표 교차검증 후 일부 참고 구현",
            "existing_dataset_modification": "새 버전 생성 시에만 추가",
        },
        {
            "field": "십신(천간)",
            "current_dataset_policy": "일간과 각 천간의 오행 생극·음양 비교",
            "ssaju_policy": "TEN_GODS 대응표",
            "conflict_rows": nemotron["stem_ten_god_mismatches_against_ssaju_table"],
            "comparison_scope": "44,000개 천간 field",
            "recommended_runtime_policy": "공통 표를 독립 fixture로 재검증 후 채택",
            "existing_dataset_modification": "불필요",
        },
        {
            "field": "십신(지지)",
            "current_dataset_policy": "지지 자체 오행·음양",
            "ssaju_policy": "지장간 정기(본기)를 일간과 비교",
            "conflict_rows": nemotron[
                "branch_ten_god_conflict_rows_against_ssaju_main_hidden_stem"
            ],
            "comparison_scope": (
                f"{nemotron['branch_ten_god_conflict_fields_against_ssaju_main_hidden_stem']:,}개 field"
            ),
            "recommended_runtime_policy": "정기 기준을 권장하되 전문가 Gold 전까지 advisory",
            "existing_dataset_modification": "기존 build 불변; 새 dataset version 이관 필요",
        },
        {
            "field": "공망",
            "current_dataset_policy": "구조화 학습 label 없음",
            "ssaju_policy": "일주 순공 기준 2지지",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재",
            "recommended_runtime_policy": "독립 fixture 후 validator 후보",
            "existing_dataset_modification": "새 버전 생성 시에만 추가",
        },
        {
            "field": "12운성",
            "current_dataset_policy": "구조화 학습 label 없음",
            "ssaju_policy": "봉법·거법을 함께 제공",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재",
            "recommended_runtime_policy": "봉법/거법 계약을 분리한 뒤 후보",
            "existing_dataset_modification": "새 버전 생성 시에만 추가",
        },
        {
            "field": "합충형파해",
            "current_dataset_policy": "구조화 학습 label 없음",
            "ssaju_policy": "상수 pair/group 탐지; 일부 관계표·반합 범위 검토 필요",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재 및 direct relation assertion 부족",
            "recommended_runtime_policy": "관계별 독립 표·fixture 후 validator 후보",
            "existing_dataset_modification": "새 버전 생성 시에만 추가",
        },
        {
            "field": "대운·세운·월운 간지",
            "current_dataset_policy": "구조화 학습 label 없음",
            "ssaju_policy": "절입·성별/년간 음양·현재시각 기반 계산",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재",
            "recommended_runtime_policy": "시간·절입 Gold 확정 뒤 간지만 후보",
            "existing_dataset_modification": "새 버전 생성 시에만 추가",
        },
        {
            "field": "신강약",
            "current_dataset_policy": "bazi rule ID 기반 strong/weak/neutral soft label",
            "ssaju_policy": "표면 오행 가중치·월지·12운성 임계값 점수",
            "conflict_rows": bazi["conflict_unique_charts"],
            "comparison_scope": (
                f"1,250개 고유 명식; 반복 행 환산 {bazi['conflict_repeated_rows']:,}행"
            ),
            "recommended_runtime_policy": "둘 다 heuristic_only로 격리",
            "existing_dataset_modification": "정답 교정 금지",
        },
        {
            "field": "격국·용신·자동 해석",
            "current_dataset_policy": "직접 비교 가능한 구조화 Gold label 없음",
            "ssaju_policy": "점수 임계값·월간 십신·정적 후보표·템플릿",
            "conflict_rows": "not_comparable",
            "comparison_scope": "필드 부재",
            "recommended_runtime_policy": "전문가 Gold로 사용 금지; 참고 해석에만 명시적 flag",
            "existing_dataset_modification": "불필요",
        },
    ]


def build_report(
    *,
    input_hashes: dict[str, str],
    raw_bundle: dict[str, Any],
    external: dict[str, Any],
    policy_sha256: str,
    nemotron: dict[str, Any],
    bazi: dict[str, Any],
    runtime_probe: dict[str, Any],
) -> dict[str, Any]:
    external_public = {
        key: value for key, value in external.items() if key != "dist_index_mjs"
    }
    return {
        "schema_version": "1.0.0",
        "report_type": "ssaju_policy_comparison",
        "review_version": "v1.0.0",
        "reviewed_at": "2026-08-29",
        "status": "completed_advisory_only",
        "scope_guards": {
            "training_data_modified": False,
            "evaluation_data_modified": False,
            "training_run_performed": False,
            "runtime_dependency_added": False,
            "submodule_added": False,
            "external_source_copied": False,
            "phase4_training_promotion_state_changed": False,
        },
        "inputs": {
            **input_hashes,
            "policy_sha256": policy_sha256,
            "analyzer_sha256": sha256_file(Path(__file__).resolve()),
            "runtime_probe_sha256": sha256_file(PROBE_PATH),
            "raw_bundle": raw_bundle,
        },
        "external_repository": external_public,
        "dataset_comparison": {
            "nemotron_saju": nemotron,
            "bazi_sft": bazi,
            "birth_to_pillars_diagnostic": runtime_probe["birth_diagnostics"],
            "field_comparison": field_comparison_table(nemotron, bazi, runtime_probe),
        },
        "external_engine_findings": {
            "constants": {
                "strengths": [
                    "천간·지지·음양·오행·십신·지장간 상수를 명시적으로 분리",
                    "지지 십신을 지장간 정기 기준으로 일관되게 계산",
                ],
                "limits": [
                    "한국 DST 표가 1960·1987·1988만 포함해 1948~1951·1955~1959를 누락",
                    "지장간·12운성·신살·용신 표는 학파 정책이므로 독립 검증 필요",
                ],
            },
            "manse": {
                "strengths": [
                    "양력·음력 변환, 절입시각, 연월일시주를 단일 deterministic API로 제공",
                    "timezone·longitude·local mean time 선택지를 노출",
                ],
                "limits": [
                    "절입은 근사 태양황경이며 공인 ephemeris Gold가 아님",
                    "DST가 day/hour 경로와 절입 경로에 일관되게 적용되지 않음",
                    "광고된 1900~2099 양력 73,049일 전수 역변환에서 실패가 확인됨",
                ],
                "solar_lunar_roundtrip": runtime_probe["lunar_roundtrip"],
            },
            "analyze": {
                "hard_fact_candidates": [
                    "십신 대응표",
                    "공망",
                    "봉법·거법 12운성",
                    "관계 탐지 구조",
                    "운 간지 생성 구조",
                ],
                "blocked_or_heuristic": {
                    "branch_relations": "self-punishment 누락·반합 범위·귀문 issue를 fixture로 재검증해야 함",
                    "day_strength": "50점 시작과 20/10/8/15 가중치 및 70/30 임계값",
                    "geukguk": "강약 점수와 월간 십신에 대한 단순 분기",
                    "yongsin": "일간별 정적 후보표",
                    "relation_priority": "수동 점수와 텍스트 우선순위",
                    "automatic_interpretation": "템플릿 기반 생성문",
                },
            },
            "format": {
                "assessment": (
                    "toCompact()/toMarkdown()는 deterministic fact와 휴리스틱·현재시각 기반 운세·"
                    "자동 해석을 한 문자열에 섞고 confidence/provenance flag를 직렬화하지 않음"
                ),
                "llm_use_recommendation": (
                    "field별 evidence_class를 보존하는 별도 JSON serializer를 먼저 설계"
                ),
            },
            "tests": {
                "result": external["checks"]["tests"],
                "count": external["checks"]["test_count"],
                "typecheck": external["checks"]["typecheck"],
                "covered": [
                    "단일 golden 원국",
                    "일부 음양력·시각 경계",
                    "12운성·신살·포맷 기본 동작",
                ],
                "gaps": [
                    "1900~2099 전수 음양력 역변환",
                    "공식 KASI fixture 대조",
                    "역사 한국 DST 전체",
                    "합충형파해 관계별 direct assertion",
                    "전문가 Gold 기반 십신·공망·12운성·대운 fixture",
                ],
            },
        },
        "usage_options": [
            {
                "option": "runtime 계산 엔진",
                "benefit": "작고 빠르며 단일 API·직렬화 제공",
                "risk": "역법 roundtrip·DST·관계표·휴리스틱이 runtime 정답에 혼재",
                "decision": "그대로 도입하지 않음",
            },
            {
                "option": "deterministic QA 생성기",
                "benefit": "정책을 고정하면 대량 구조화 문제 생성 가능",
                "risk": "생성기 오류가 train/eval에 함께 복제될 수 있음",
                "decision": "독립 Gold를 통과한 field만 일부 참고 구현",
            },
            {
                "option": "검증 validator",
                "benefit": "기존 label과 정책 차이를 빠르게 advisory 탐지",
                "risk": "단일 구현을 oracle로 쓰면 정책 차이를 오류로 오판",
                "decision": "현재는 advisory differential validator로만 사용",
            },
        ],
        "adoption_conclusion": {
            "decision": "일부 모듈만 참고 구현",
            "rejected_choices": ["그대로 도입", "계산 정책 전체 채택", "사용하지 않음"],
            "reason": (
                "상수·순수 계산 함수의 구조는 유용하지만 birth-to-pillars와 관계표는 Gold가 부족하고, "
                "신강약·격국·용신·해석은 명시적 휴리스틱이므로 모듈별 독립 검증이 필요함"
            ),
            "next_gate": [
                "KASI 음양력·절입 및 IANA 역사 시간대 기반 fixture 구축",
                "전문가가 승인한 지장간·십신·12운성·공망·관계 정책표 고정",
                "기존 immutable dataset을 건드리지 않는 새 schema/version migration 설계",
                "fact-only runtime serializer와 evidence_class flag 구현",
            ],
        },
        "third_party_notices_candidate": {
            "status": "candidate_only_not_yet_required",
            "trigger": "ssaju 코드 또는 실질적 파생 구현을 배포물에 포함할 때",
            "name": "ssaju",
            "repository": "https://github.com/golbin/ssaju",
            "revision": EXTERNAL_REVISION,
            "license": "MIT License",
            "copyright_notice": "Copyright (c) 2026 Jin",
            "preservation_requirement": (
                "저작권 고지와 MIT 허가·면책 문구를 소프트웨어의 모든 복제본 또는 "
                "중요 부분에 포함"
            ),
            "legal_advice": False,
        },
    }


def sample_jsonl_bytes(samples: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(sample) + b"\n" for sample in samples)


def build_identity(
    report: dict[str, Any], comparison_bytes: bytes, samples_bytes: bytes
) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "review_version": report["review_version"],
        "external_revision": report["external_repository"]["revision"],
        "external_dist_sha256": report["external_repository"]["dist_index_mjs_sha256"],
        "environment": {
            "python": sys.version.split()[0],
            "node": report["external_repository"]["node_version"],
            "npm": report["external_repository"]["npm_version"],
        },
        "input_sha256": report["inputs"],
        "external_checks": report["external_repository"]["checks"],
        "comparison_sha256": sha256_bytes(comparison_bytes),
        "samples_sha256": sha256_bytes(samples_bytes),
    }


def write_artifacts(report: dict[str, Any], samples: list[dict[str, Any]]) -> Path:
    comparison_bytes = pretty_json_bytes(report)
    samples_bytes = sample_jsonl_bytes(samples)
    identity = build_identity(report, comparison_bytes, samples_bytes)
    review_sha256 = sha256_bytes(canonical_json_bytes(identity))
    review_id = f"review-{review_sha256[:12]}"
    output = REPORT_ROOT / review_id
    manifest = {
        "schema_version": "1.0.0",
        "report_type": "ssaju_policy_review_build_manifest",
        "review_id": review_id,
        "review_sha256": review_sha256,
        "status": "completed_advisory_only",
        "identity": identity,
        "artifacts": {
            "comparison_report.json": {
                "bytes": len(comparison_bytes),
                "sha256": sha256_bytes(comparison_bytes),
            },
            "conflict_samples_100.jsonl": {
                "bytes": len(samples_bytes),
                "rows": 100,
                "sha256": sha256_bytes(samples_bytes),
                "contains_source_record_ids": False,
                "contains_birth_or_location_fields": False,
            },
        },
        "immutability": {
            "write_once": True,
            "existing_canonical_data_modified": False,
            "phase5_training_performed": False,
        },
    }
    manifest_bytes = pretty_json_bytes(manifest)
    checksums = {
        "comparison_report.json": sha256_bytes(comparison_bytes),
        "conflict_samples_100.jsonl": sha256_bytes(samples_bytes),
        "build_manifest.json": sha256_bytes(manifest_bytes),
    }
    checksum_bytes = "".join(
        f"{digest}  {name}\n" for name, digest in sorted(checksums.items())
    ).encode("utf-8")
    payloads = {
        "comparison_report.json": comparison_bytes,
        "conflict_samples_100.jsonl": samples_bytes,
        "build_manifest.json": manifest_bytes,
        "SHA256SUMS.txt": checksum_bytes,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        for name, expected in payloads.items():
            path = output / name
            require_regular_file(path, f"기존 {name}")
            if path.read_bytes() != expected:
                raise SsajuReviewError(
                    f"기존 불변 review와 재생성 결과가 다릅니다: {output}"
                )
        return output

    temporary = Path(tempfile.mkdtemp(prefix=f".{review_id}.", dir=output.parent))
    try:
        for name, payload in payloads.items():
            path = temporary / name
            path.write_bytes(payload)
            os.chmod(path, 0o644)
        os.chmod(temporary, 0o755)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return output


def verify_report(report_dir: Path) -> dict[str, Any]:
    root = report_dir.resolve()
    if root.parent != REPORT_ROOT.resolve() or not root.name.startswith("review-"):
        raise SsajuReviewError(
            "report 경로가 고정 ssaju review version 아래가 아닙니다."
        )
    manifest = load_json_object(root / "build_manifest.json", "review build manifest")
    if manifest.get("review_id") != root.name:
        raise SsajuReviewError("review directory와 manifest review_id가 다릅니다.")
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise SsajuReviewError("review manifest identity가 object가 아닙니다.")
    expected_review_sha256 = sha256_bytes(canonical_json_bytes(identity))
    if (
        manifest.get("review_sha256") != expected_review_sha256
        or root.name != f"review-{expected_review_sha256[:12]}"
    ):
        raise SsajuReviewError(
            "review identity fingerprint가 경로·manifest와 다릅니다."
        )
    expected_names = {
        "comparison_report.json",
        "conflict_samples_100.jsonl",
        "build_manifest.json",
        "SHA256SUMS.txt",
    }
    actual_names = {path.name for path in root.iterdir()}
    if actual_names != expected_names:
        raise SsajuReviewError(
            f"review artifact 집합이 고정값과 다릅니다: {actual_names}"
        )
    checksums: dict[str, str] = {}
    for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        try:
            digest, name = line.split("  ", 1)
        except ValueError as exc:
            raise SsajuReviewError("SHA256SUMS 형식이 올바르지 않습니다.") from exc
        if SHA256_PATTERN.fullmatch(digest) is None or name in checksums:
            raise SsajuReviewError("SHA256SUMS digest 또는 중복 이름 오류입니다.")
        checksums[name] = digest
    if set(checksums) != expected_names - {"SHA256SUMS.txt"}:
        raise SsajuReviewError("SHA256SUMS 대상 집합이 고정값과 다릅니다.")
    for name, expected in checksums.items():
        require_sha256(root / name, expected, f"review {name}")
    artifact_contract = manifest.get("artifacts")
    if not isinstance(artifact_contract, dict):
        raise SsajuReviewError("review manifest artifacts가 object가 아닙니다.")
    for name in ("comparison_report.json", "conflict_samples_100.jsonl"):
        metadata = artifact_contract.get(name)
        path = root / name
        if (
            not isinstance(metadata, dict)
            or metadata.get("sha256") != checksums[name]
            or metadata.get("bytes") != path.stat().st_size
        ):
            raise SsajuReviewError(
                f"review artifact metadata가 실제 파일과 다릅니다: {name}"
            )
    if checksums["build_manifest.json"] != sha256_file(root / "build_manifest.json"):
        raise SsajuReviewError("build manifest checksum이 실제 파일과 다릅니다.")
    report = load_json_object(root / "comparison_report.json", "comparison report")
    guards = report.get("scope_guards")
    if not isinstance(guards, dict) or any(guards.values()):
        raise SsajuReviewError(
            "comparison report의 비변경 scope guard가 유지되지 않았습니다."
        )
    if report.get("external_repository", {}).get("revision") != EXTERNAL_REVISION:
        raise SsajuReviewError(
            "comparison report의 ssaju revision이 고정값과 다릅니다."
        )
    samples = (
        (root / "conflict_samples_100.jsonl").read_text(encoding="utf-8").splitlines()
    )
    if len(samples) != 100:
        raise SsajuReviewError("충돌 표본이 100행이 아닙니다.")
    parsed_samples = [json.loads(line) for line in samples]
    if len({sample["sample_id"] for sample in parsed_samples}) != 100:
        raise SsajuReviewError("충돌 sample_id가 고유하지 않습니다.")
    anchors = Counter(sample.get("anchor_branch") for sample in parsed_samples)
    if anchors != Counter({"子": 25, "巳": 25, "午": 25, "亥": 25}):
        raise SsajuReviewError(f"충돌 anchor 균형이 고정 계약과 다릅니다: {anchors}")
    forbidden_keys = {
        "id",
        "uuid",
        "record_sha256",
        "raw_hash",
        "source_group_id",
        "leakage_group_id",
        "birth_datetime_synth",
        "last_datetime",
        "birth_longitude_e",
    }
    for sample in parsed_samples:
        serialized = canonical_json_bytes(sample).decode("utf-8")
        if any(f'"{key}":' in serialized for key in forbidden_keys):
            raise SsajuReviewError("충돌 표본에 금지 identity/raw field가 있습니다.")
    return {
        "review_id": root.name,
        "artifact_count": 4,
        "sample_rows": 100,
        "sample_anchor_counts": dict(sorted(anchors.items())),
        "status": "verified",
    }


def build_command(args: argparse.Namespace) -> int:
    _, policy_sha256 = validate_policy()
    input_hashes = validate_canonical_inputs()
    parquet_paths, raw_bundle = validate_raw_bundle()
    external = validate_external_checkout(
        args.ssaju_root, prepare=args.prepare_external
    )
    selection = load_selection()
    nemotron_records = load_selected_records(
        NEMOTRON_RECORDS, selection["nemotron_saju"], "Nemotron staging"
    )
    bazi_records = load_selected_records(
        BAZI_RECORDS, selection["bazi_sft"], "bazi staging"
    )
    nemotron, samples, charts_by_source_hash = compare_nemotron(nemotron_records)
    bazi = compare_bazi(bazi_records)
    raw_records = load_raw_runtime_records(parquet_paths, charts_by_source_hash)
    runtime_probe = run_runtime_probe(external["dist_index_mjs"], raw_records)
    report = build_report(
        input_hashes=input_hashes,
        raw_bundle=raw_bundle,
        external=external,
        policy_sha256=policy_sha256,
        nemotron=nemotron,
        bazi=bazi,
        runtime_probe=runtime_probe,
    )
    output = write_artifacts(report, samples)
    verification = verify_report(output)
    print(json.dumps({"output": str(output), **verification}, ensure_ascii=False))
    return 0


def verify_command(args: argparse.Namespace) -> int:
    validate_policy()
    validate_canonical_inputs()
    validate_external_checkout(args.ssaju_root, prepare=False)
    result = verify_report(args.report_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="고정 ssaju revision과 canonical MIX20K의 계산 정책 비교"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser(
        "build", help="전수 비교 보고서를 새 불변 경로에 생성"
    )
    build.add_argument("--ssaju-root", type=Path, required=True)
    build.add_argument(
        "--prepare-external",
        action="store_true",
        help="임시 checkout에서 npm ci/test/typecheck/build/audit까지 실행",
    )
    build.set_defaults(handler=build_command)

    verify = subparsers.add_parser(
        "verify", help="보고서 hash chain과 고정 입력을 검증"
    )
    verify.add_argument("--ssaju-root", type=Path, required=True)
    verify.add_argument("--report-dir", type=Path, required=True)
    verify.set_defaults(handler=verify_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except SsajuReviewError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
