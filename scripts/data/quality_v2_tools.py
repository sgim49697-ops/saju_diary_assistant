# quality_v2_tools.py - 품질 보정 staging v1의 생성·검증·불변 산출물을 관리한다.

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.data.errors import Phase2AuditError
from scripts.data.phase2b_verify_history import verify_historical_staging
from scripts.data.preprocess_adapters import (
    NAME_PATTERN,
    PII_PATTERNS,
    build_aihub_records,
    build_bazi_records,
    build_nemotron_records,
    build_yeji_records,
    calendar_relations_valid,
    sha256_json,
    stable_rank,
)
from scripts.data.source_tools import load_config as load_source_config
from scripts.data.source_tools import sha256_file
from scripts.data.ssaju_policy_review import (
    CHART_PATTERN,
    hidden_stem_branch_ten_god,
    stem_ten_god,
)

RECORD_SCHEMA_VERSION = "2.0.0"
CONFIG_SCHEMA_VERSION = "1.0.0"
STAGING_VERSION = "v1.0.0"
DATASET_NAME = "saju_1b_baseline"
SEED = 42
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
    "deterministic_saju_qa",
    "saju_diary_bridge",
)
LABEL_TIERS = {
    "HARD_GT",
    "RULE_DERIVED",
    "STYLE_REFERENCE",
    "SOFT_INTERPRETATION",
}
QA_CATEGORIES = (
    "stem_branch_identity",
    "yin_yang_elements_and_surface_counts",
    "hidden_stems",
    "stem_ten_gods",
    "branch_ten_gods",
)
TEN_GOD_LINE_PATTERN = re.compile(r"^십신: .+$", re.MULTILINE)
FULL_BIRTHDATE_LOCAL_PATTERN = re.compile(
    r"(?:19|20)\d{2}[년./-]\s*\d{1,2}[월./-]\s*\d{1,2}일?"
)
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
HANDLE_PATTERN = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{2,32}\b")
LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
ACCOUNT_PATTERN = re.compile(
    r"(?:계좌|카드|주민(?:등록)?번호|운전면허|여권).{0,12}\d{4,}", re.IGNORECASE
)
ADDRESS_NUMBER_PATTERN = re.compile(r"(?:로|길|동|읍|면|리)\s*\d{1,4}(?:-\d{1,4})?\b")
SENSITIVE_PATTERN = re.compile(
    r"(?:자살|자해|극단적 선택|죽고 싶|목숨을 끊|진단|처방|복용|투자 보장|수익 보장|"
    r"법률 보장|혐오|성폭력|죽여|패버려)",
    re.IGNORECASE,
)
NEMOTRON_DISCLAIMER = (
    "이 내용은 전통 명리의 문화·오락적 참고 해석이며 실제 성향, 진로, "
    "건강 또는 재정 결과를 확정하지 않습니다."
)
BAZI_DISCLAIMER = (
    "이 해석은 전통 명리 관점의 문화·오락적 참고이며, 미래의 사건이나 "
    "건강·재정·관계 결과를 확정하지 않고 의료 진단이나 투자 조언을 대신하지 않습니다."
)
SOURCE_NAME_PATTERN = re.compile(
    r"(?<![가-힣])(?P<name>[가-힣]{2,4})\s*(?P<title>씨|님)"
    r"(?P<particle>께서는|에게는|에게|께|의|는|은|가|이|를|을|와|과)?"
)
NAME_REPLACEMENTS = {
    "께서는": "이 사람은",
    "에게는": "이 사람에게는",
    "에게": "이 사람에게",
    "께": "이 사람에게",
    "의": "이 사람의",
    "는": "이 사람은",
    "은": "이 사람은",
    "가": "이 사람이",
    "이": "이 사람이",
    "를": "이 사람을",
    "을": "이 사람을",
    "와": "이 사람과",
    "과": "이 사람과",
    "": "이 사람",
}
PILLAR_NAMES = ("year", "month", "day", "hour")
PILLAR_KO = ("년주", "월주", "일주", "시주")
LUNAR_TEN_GOD_KO = {
    "比肩": "비견",
    "劫财": "겁재",
    "食神": "식신",
    "伤官": "상관",
    "偏财": "편재",
    "正财": "정재",
    "七杀": "편관",
    "正官": "정관",
    "偏印": "편인",
    "正印": "정인",
}
ALLOWED_SAJU_HANJA = set(
    "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥木火土金水陰陽比肩劫財财食神傷伤官偏正印殺杀"
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _git_head(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Phase2AuditError("현재 Git HEAD를 확인할 수 없습니다.") from exc
    return result.stdout.strip()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError(f"{label} JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase2AuditError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _safe_repo_path(repo_root: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise Phase2AuditError(f"저장소 상대경로가 올바르지 않습니다: {value}")
    root = repo_root.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise Phase2AuditError(f"저장소 밖 경로를 허용하지 않습니다: {value}")
    return resolved


def _assert_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
        raise Phase2AuditError(f"{label} SHA-256이 고정 계약과 다릅니다.")


def _write_bytes_once(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else PUBLIC_DIR_MODE,
    )
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise Phase2AuditError(f"기존 불변 파일과 내용이 다릅니다: {path}")
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(mode)


def _write_json_once(path: Path, value: Any, *, mode: int) -> None:
    _write_bytes_once(path, _json_bytes(value), mode=mode)


def _write_jsonl_once(
    path: Path, values: Iterable[dict[str, Any]], *, mode: int
) -> None:
    payload = b"".join(_canonical_json_bytes(value) + b"\n" for value in values)
    _write_bytes_once(path, payload, mode=mode)


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise ValueError(f"empty line {line_number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TypeError(f"line {line_number}")
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise Phase2AuditError(f"{label} JSONL을 읽을 수 없습니다: {path}") from exc
    return values


def _artifact_hashes(root: Path, relative_paths: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for relative in relative_paths:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise Phase2AuditError(f"산출물이 일반 파일이 아닙니다: {relative}")
        values[relative] = sha256_file(path)
    return values


def _verify_hashes(root: Path, values: Any, label: str) -> None:
    if not isinstance(values, dict) or not values:
        raise Phase2AuditError(f"{label} artifact hash map이 없습니다.")
    for relative, expected in values.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise Phase2AuditError(f"{label} artifact hash map이 올바르지 않습니다.")
        _assert_hash(root / relative, expected, f"{label}:{relative}")


def load_quality_config(path: Path, repo_root: Path) -> dict[str, Any]:
    config = _load_json(path, "품질 보정 staging 설정")
    if (
        config.get("schema_version") != CONFIG_SCHEMA_VERSION
        or config.get("record_schema_version") != RECORD_SCHEMA_VERSION
        or config.get("dataset_name") != DATASET_NAME
        or config.get("staging_version") != STAGING_VERSION
        or config.get("seed") != SEED
    ):
        raise Phase2AuditError(
            "품질 보정 staging의 schema/version/seed 계약이 다릅니다."
        )
    axes = config.get("axes")
    if not isinstance(axes, dict) or tuple(axes) != AXES:
        raise Phase2AuditError("품질 보정 staging axis 순서가 고정 계약과 다릅니다.")
    if sum(int(value.get("staging_rows", -1)) for value in axes.values()) != 24_000:
        raise Phase2AuditError("품질 보정 staging 수량 합계가 24,000이 아닙니다.")
    for key, total in (("mix20k", 20_000), ("mix10k", 10_000), ("mix1k", 1_000)):
        if sum(int(value.get(key, -1)) for value in axes.values()) != total:
            raise Phase2AuditError(f"품질 보정 {key} 수량 합계가 {total:,}이 아닙니다.")
    for axis, values in axes.items():
        if (
            values["mix10k"] * 2 != values["mix20k"]
            or values["mix1k"] * 10 != values["mix10k"]
        ):
            raise Phase2AuditError(f"{axis} MIX1K/10K/20K 정확한 중첩 비율이 다릅니다.")
        if values["staging_rows"] * 5 != values["mix20k"] * 6:
            raise Phase2AuditError(f"{axis} staging 20% reserve 계약이 다릅니다.")
    scope = config.get("scope")
    if (
        not isinstance(scope, dict)
        or scope.get("overwrite_existing_builds") is not False
        or scope.get("human_domain_review_performed") is not False
        or scope.get("quality_certification_claimed") is not False
        or scope.get("phase5_training_performed") is not False
    ):
        raise Phase2AuditError("품질 보정 staging의 거버넌스 flag가 다릅니다.")
    parents = config.get("parents")
    if not isinstance(parents, dict):
        raise Phase2AuditError("품질 보정 staging 부모 계약이 없습니다.")
    for path_key, hash_key in (
        ("audit_policy", "audit_policy_sha256"),
        ("correction_manifest", "correction_manifest_sha256"),
        ("language_bank", "language_bank_sha256"),
        ("calculation_policy", "calculation_policy_sha256"),
    ):
        _assert_hash(
            _safe_repo_path(repo_root, str(parents[path_key])),
            str(parents[hash_key]),
            path_key,
        )
    old = parents.get("immutable_staging")
    if not isinstance(old, dict) or old.get("build_id") != "build-847088ee804d":
        raise Phase2AuditError("불변 staging 제외 부모가 다릅니다.")
    _assert_hash(
        repo_root
        / "data/staging"
        / DATASET_NAME
        / str(old["version"])
        / str(old["build_id"])
        / "build_manifest.json",
        str(old["manifest_sha256"]),
        "불변 staging private manifest",
    )
    review = parents.get("external_review")
    if not isinstance(review, dict):
        raise Phase2AuditError("외부 검수 부모가 없습니다.")
    _assert_hash(
        repo_root
        / "data/reports/saju_1b_baseline/external-review/v1.0.0"
        / str(review["review_id"])
        / "verification_report.json",
        str(review["verification_sha256"]),
        "외부 검수 보고서",
    )
    quality = config.get("quality_contract")
    if (
        not isinstance(quality, dict)
        or quality.get("critical_or_high_allowed") != 0
        or quality.get("full_scan_required") is not True
        or quality.get("human_domain_review_performed") is not False
        or quality.get("quality_certification_claimed") is not False
    ):
        raise Phase2AuditError("자동 품질 Gate 계약이 fail-closed가 아닙니다.")
    calendar = config.get("calendar_backend")
    if (
        not isinstance(calendar, dict)
        or calendar.get("distribution") != "lunar-python"
        or calendar.get("import_name") != "lunar_python"
        or calendar.get("version") != "1.4.8"
        or calendar.get("artifact_sha256")
        != "3aa11cc73c25e70ddf0ba5bdac7398c03acc9491a3aa512a91c9642973b669d6"
        or calendar.get("algorithm") != "sha256-counter-v2"
        or calendar.get("timezone_assumption") != "Asia/Seoul"
        or calendar.get("max_attempts_per_case") != 200_000
    ):
        raise Phase2AuditError("달력 backend 버전·artifact·탐색 계약이 다릅니다.")
    try:
        installed_calendar_version = importlib.metadata.version(
            str(calendar["distribution"])
        )
    except importlib.metadata.PackageNotFoundError as exc:
        raise Phase2AuditError("lunar-python 1.4.8이 데이터 환경에 없습니다.") from exc
    if installed_calendar_version != calendar["version"]:
        raise Phase2AuditError(
            "설치된 lunar-python 버전이 고정 계약과 다릅니다: "
            f"{installed_calendar_version}"
        )
    return config


def validate_calculation_policy(
    config: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    policy_path = _safe_repo_path(repo_root, config["parents"]["calculation_policy"])
    policy = _load_json(policy_path, "사주 계산 정책")
    if (
        policy.get("policy_version") != "v1.0.0"
        or policy.get("status") != "approved_project_policy_not_expert_certified"
        or policy.get("contracts", {}).get("validator_mode") != "enforce"
        or policy.get("contracts", {}).get("weak_fields_must_never_be_gold") is not True
        or policy.get("scope", {}).get("quality_certification_claimed") is not False
    ):
        raise Phase2AuditError("승인된 계산 정책의 Gate 상태가 다릅니다.")
    tables = policy.get("tables")
    if not isinstance(tables, dict):
        raise Phase2AuditError("계산 정책 표가 없습니다.")
    try:
        from lunar_python.util import LunarUtil
    except ImportError as exc:
        raise Phase2AuditError("lunar-python 1.4.8이 데이터 환경에 없습니다.") from exc
    if LunarUtil.ZHI_HIDE_GAN != tables["hidden_stems_main_first"]:
        raise Phase2AuditError("지장간 표가 lunar-python 1.4.8과 다릅니다.")
    if tables.get("ten_god_label_normalization") != LUNAR_TEN_GOD_KO:
        raise Phase2AuditError("십신 표기 정규화 계약이 다릅니다.")
    checks = 0
    for day_stem in tables["stems"]:
        for branch in tables["branches"]:
            main_stem = tables["hidden_stems_main_first"][branch][0]
            lunar_value = LUNAR_TEN_GOD_KO[LunarUtil.SHI_SHEN[day_stem + main_stem]]
            project_value = hidden_stem_branch_ten_god(day_stem, branch)
            if lunar_value != project_value:
                raise Phase2AuditError(
                    f"지지 십신 120조합 전수 비교가 실패했습니다: {day_stem}/{branch}"
                )
            checks += 1
    return {
        "policy_id": policy["policy_id"],
        "policy_sha256": sha256_file(policy_path),
        "hidden_stem_branch_ten_god_checks": checks,
        "hidden_stem_table_checks": len(tables["branches"]),
        "independent_oracle": "lunar-python==1.4.8",
        "external_reference_revision": policy["evidence"]["external_reference"][
            "revision"
        ],
        "expert_certification": False,
    }


def prepare_quality_context(
    repo_root: Path,
    config_path: Path,
    *,
    verify_parent: bool = True,
) -> dict[str, Any]:
    config = load_quality_config(config_path, repo_root)
    policy_validation = validate_calculation_policy(config, repo_root)
    parent_verification: dict[str, Any] | None = None
    if verify_parent:
        old = config["parents"]["immutable_staging"]
        parent_verification = verify_historical_staging(
            repo_root,
            staging_version=old["version"],
            build_id=old["build_id"],
            implementation_commit=None,
        )
        if (
            parent_verification.get("owner_risk_accepted") is not True
            or parent_verification.get("record_validation", {}).get("total_rows")
            != 24_000
        ):
            raise Phase2AuditError("과거 staging 제외 부모 재검증이 실패했습니다.")
    implementation_hashes: dict[str, str] = {}
    for relative in [
        *config["implementation_files"],
        config_path.relative_to(repo_root).as_posix(),
    ]:
        path = _safe_repo_path(repo_root, relative)
        if not path.is_file():
            raise Phase2AuditError(f"품질 보정 구현 파일이 없습니다: {relative}")
        implementation_hashes[relative] = sha256_file(path)
    build_inputs = {
        "record_schema_version": config["record_schema_version"],
        "staging_version": config["staging_version"],
        "seed": config["seed"],
        "axes_sha256": _sha256_json(config["axes"]),
        "aihub_reservoir_sha256": _sha256_json(config["aihub_reservoir"]),
        "quality_contract_sha256": _sha256_json(config["quality_contract"]),
        "calendar_backend_sha256": _sha256_json(config["calendar_backend"]),
        "source_build_sha256": config["parents"]["source_build_sha256"],
        "immutable_parent": config["parents"]["immutable_staging"],
        "calculation_policy_sha256": policy_validation["policy_sha256"],
        "external_review_id": config["parents"]["external_review"]["review_id"],
        "ssaju_review": config["parents"]["ssaju_review"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = _sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    outputs = config["outputs"]
    private_root = (
        _safe_repo_path(
            repo_root,
            outputs["private_root"].format(
                dataset_name=config["dataset_name"],
                staging_version=config["staging_version"],
            ),
        )
        / build_id
    )
    public_root = (
        _safe_repo_path(
            repo_root,
            outputs["public_root"].format(
                dataset_name=config["dataset_name"],
                staging_version=config["staging_version"],
            ),
        )
        / build_id
    )
    return {
        "build_id": build_id,
        "build_sha256": build_sha256,
        "build_inputs": build_inputs,
        "config": config,
        "config_path": config_path,
        "policy_validation": policy_validation,
        "parent_verification": parent_verification,
        "private_root": private_root,
        "public_root": public_root,
        "workspace_base_commit": _git_head(repo_root),
    }


def _message_lengths(messages: Sequence[dict[str, str]]) -> tuple[int, int, int]:
    input_chars = sum(
        len(message["content"])
        for message in messages
        if message["role"] != "assistant"
    )
    assistant_chars = sum(
        len(message["content"])
        for message in messages
        if message["role"] == "assistant"
    )
    return input_chars, assistant_chars, input_chars + assistant_chars


def _upgrade_record(
    record: dict[str, Any],
    *,
    policy_sha256: str,
    tier: str,
    task: str | None = None,
    mix_axis: str | None = None,
    record_id: str | None = None,
    messages: list[dict[str, str]] | None = None,
    leakage_group_ids: Sequence[str] | None = None,
    transformation_steps: Sequence[str] = (),
    extra_meta: dict[str, Any] | None = None,
    label_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if tier not in LABEL_TIERS:
        raise Phase2AuditError(f"지원하지 않는 label tier입니다: {tier}")
    value = copy.deepcopy(record)
    value["schema_version"] = RECORD_SCHEMA_VERSION
    if task is not None:
        value["task"] = task
    if mix_axis is not None:
        value["mix_axis"] = mix_axis
    if record_id is not None:
        value["id"] = record_id
    if messages is not None:
        value["messages"] = messages
    label = dict(value.get("label") or {})
    label["tier"] = tier
    label["human_review"] = "not_performed"
    label["quality_certification"] = False
    if label_updates:
        label.update(label_updates)
    value["label"] = label
    chain = [
        step
        for step in value.get("transformation_chain", [])
        if step not in {"fixed_disclaimer_appended", "fixed_korean_template"}
    ]
    for step in transformation_steps:
        if step not in chain:
            chain.append(step)
    value["transformation_chain"] = chain
    meta = dict(value.get("meta") or {})
    primary = str(meta.get("leakage_group_id", ""))
    groups = list(leakage_group_ids or meta.get("leakage_group_ids") or [primary])
    groups = sorted({group for group in groups if isinstance(group, str) and group})
    if not groups:
        raise Phase2AuditError(f"leakage group이 없습니다: {value.get('id')}")
    meta["leakage_group_id"] = primary if primary in groups else groups[0]
    meta["leakage_group_ids"] = groups
    meta["calculation_policy_id"] = "saju-calculation-policy-v1.0.0"
    meta["calculation_policy_sha256"] = policy_sha256
    input_chars, assistant_chars, total_chars = _message_lengths(value["messages"])
    meta.update(
        {
            "message_sha256": sha256_json(value["messages"]),
            "input_chars": input_chars,
            "assistant_chars": assistant_chars,
            "total_chars": total_chars,
        }
    )
    if extra_meta:
        meta.update(extra_meta)
    value["meta"] = meta
    text = "\n".join(message["content"] for message in value["messages"])
    quality = dict(value.get("quality_flags") or {})
    quality.update(
        {
            "parse_ok": True,
            "language_ok": re.search(r"[가-힣]", text) is not None,
            "exact_duplicate": False,
            "translation_residue": False,
            "over_length": False,
            "automated_quality_gate": "passed",
        }
    )
    value["quality_flags"] = quality
    return value


def _assistant_text(record: dict[str, Any]) -> str:
    return "\n".join(
        message["content"]
        for message in record["messages"]
        if message["role"] == "assistant"
    )


def _input_text(record: dict[str, Any]) -> str:
    return "\n".join(
        message["content"]
        for message in record["messages"]
        if message["role"] != "assistant"
    )


def _replace_source_names(text: str) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return NAME_REPLACEMENTS[match.group("particle") or ""]

    return SOURCE_NAME_PATTERN.sub(replace, text), count


def _nemotron_chart(record: dict[str, Any]) -> dict[str, str]:
    user = "\n".join(
        message["content"]
        for message in record["messages"]
        if message["role"] == "user"
    )
    match = CHART_PATTERN.search(user)
    if match is None:
        raise Phase2AuditError(
            f"Nemotron 명식을 파싱할 수 없습니다: {record.get('id')}"
        )
    return match.groupdict()


def _correct_ten_god_line(user: str, chart: dict[str, str]) -> tuple[str, int]:
    day_stem = chart["day"][0]
    parts: list[str] = []
    changed = 0
    existing = TEN_GOD_LINE_PATTERN.search(user)
    if existing is None:
        raise Phase2AuditError("Nemotron user에 십신 행이 없습니다.")
    old_line = existing.group(0)
    for name, label in zip(PILLAR_NAMES, PILLAR_KO, strict=True):
        stem = chart[name][0]
        branch = chart[name][1]
        stem_label = "본원(일간)" if name == "day" else stem_ten_god(day_stem, stem)
        branch_label = hidden_stem_branch_ten_god(day_stem, branch)
        parts.append(f"{label} 천간 {stem_label}, 지지 {branch_label}")
    new_line = "십신: " + "; ".join(parts)
    if old_line != new_line:
        changed = 1
    return TEN_GOD_LINE_PATTERN.sub(new_line, user, count=1), changed


def _neutral_balance_line(user: str) -> str:
    match = re.search(r"부족 오행:\s*(?P<value>[^\n]+)", user)
    if match is None:
        raise Phase2AuditError("Nemotron 부족 오행 값을 파싱할 수 없습니다.")
    lacking = match.group("value").strip().rstrip(".;")
    if lacking == "없음":
        return (
            "오행 균형 참고: 표면 오행 분포에서 빠진 오행은 없습니다. "
            "색상·방향·소품 같은 보완 행동을 이 정보만으로 권하지 않습니다."
        )
    return (
        f"오행 균형 참고: 표면 오행 분포에서 나타나지 않은 오행은 {lacking}입니다. "
        "이는 구조 설명이며 색상·방향·소품 같은 행동 처방을 뜻하지 않습니다."
    )


def _foreign_cjk(text: str) -> set[str]:
    return {
        character
        for character in CJK_PATTERN.findall(text)
        if character not in ALLOWED_SAJU_HANJA
    }


def _transform_nemotron(
    candidates: list[dict[str, Any]],
    *,
    target_by_variant: dict[str, int],
    policy_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    counters: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    variant_counts: Counter[str] = Counter()
    for source_record in sorted(
        candidates,
        key=lambda value: (value["meta"]["candidate_rank"], value["id"]),
    ):
        variant = str(source_record["source_variant"])
        if variant_counts[variant] >= target_by_variant[variant]:
            continue
        messages = copy.deepcopy(source_record["messages"])
        input_text = _input_text(source_record)
        assistant_before = _assistant_text(source_record)
        target_dates = set(
            FULL_BIRTHDATE_LOCAL_PATTERN.findall(assistant_before)
        ) - set(FULL_BIRTHDATE_LOCAL_PATTERN.findall(input_text))
        if target_dates:
            counters["excluded_target_only_full_birthdate"] += 1
            continue
        assistant, replacements = _replace_source_names(assistant_before)
        counters["source_name_replacements"] += replacements
        assistant = assistant.removesuffix("\n" + NEMOTRON_DISCLAIMER).removesuffix(
            NEMOTRON_DISCLAIMER
        )
        if assistant != assistant_before:
            counters["fixed_disclaimer_removed"] += 1
        chart = _nemotron_chart(source_record)
        for message in messages:
            if message["role"] == "user":
                message["content"], changed = _correct_ten_god_line(
                    message["content"], chart
                )
                counters["branch_ten_god_rows_migrated"] += int(changed > 0)
            elif message["role"] == "assistant":
                lines = assistant.splitlines()
                replaced = False
                for index, line in enumerate(lines):
                    if line.startswith("오행 균형 참고:"):
                        lines[index] = _neutral_balance_line(
                            next(
                                item["content"]
                                for item in messages
                                if item["role"] == "user"
                            )
                        )
                        replaced = True
                if not replaced:
                    raise Phase2AuditError("Nemotron 오행 균형 section이 없습니다.")
                message["content"] = "\n".join(lines)
                counters["balance_advice_replaced"] += 1
        assistant_after = "\n".join(
            message["content"] for message in messages if message["role"] == "assistant"
        )
        if NAME_PATTERN.search(assistant_after):
            counters["excluded_residual_target_name"] += 1
            continue
        residue = _foreign_cjk(assistant_after)
        if residue:
            counters["excluded_foreign_cjk_residue"] += 1
            continue
        if FULL_BIRTHDATE_LOCAL_PATTERN.search(assistant_after):
            counters["excluded_residual_full_birthdate"] += 1
            continue
        record = _upgrade_record(
            source_record,
            policy_sha256=policy_sha256,
            tier="SOFT_INTERPRETATION",
            messages=messages,
            transformation_steps=(
                "target_only_name_contextual_replacement_v1",
                "target_only_full_birthdate_excluded_v1",
                "foreign_cjk_allowlist_v1",
                "branch_main_hidden_stem_ten_god_migration_v1",
                "neutral_surface_element_balance_v1",
                "assistant_disclaimer_moved_to_system_v1",
            ),
            extra_meta={
                "label_policy": "persona_aware_contextualization",
                "remediation": {
                    "source_name_replacements": replacements,
                    "full_birthdate_exclusion_checked": True,
                    "foreign_cjk_allowlist_checked": True,
                    "branch_ten_god_policy": "branch_main_hidden_stem_v1",
                },
            },
        )
        selected.append(record)
        variant_counts[variant] += 1
    if variant_counts != Counter(target_by_variant):
        raise Phase2AuditError(
            f"품질 보정 Nemotron 후보가 부족합니다: {dict(variant_counts)}"
        )
    selected.sort(key=lambda value: (value["meta"]["candidate_rank"], value["id"]))
    return selected, {
        "selected_rows": len(selected),
        "selected_variants": dict(sorted(variant_counts.items())),
        "remediation_counts": dict(sorted(counters.items())),
    }


def _transform_bazi(
    records: list[dict[str, Any]], *, policy_sha256: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result: list[dict[str, Any]] = []
    removed = 0
    for source_record in records:
        messages = copy.deepcopy(source_record["messages"])
        for message in messages:
            if message["role"] == "assistant":
                updated = (
                    message["content"]
                    .removesuffix(" " + BAZI_DISCLAIMER)
                    .removesuffix(BAZI_DISCLAIMER)
                )
                removed += int(updated != message["content"])
                message["content"] = updated
        result.append(
            _upgrade_record(
                source_record,
                policy_sha256=policy_sha256,
                tier="RULE_DERIVED",
                messages=messages,
                transformation_steps=(
                    "assistant_disclaimer_moved_to_system_v1",
                    "rule_grounding_preserved_v1",
                ),
                extra_meta={"advanced_expert_interpretation_claimed": False},
            )
        )
    if removed != len(records):
        raise Phase2AuditError(
            "BaZi 고정 assistant disclaimer를 전부 제거하지 못했습니다."
        )
    return result, {"selected_rows": len(result), "fixed_disclaimer_removed": removed}


def _has_final_consonant(value: str) -> bool:
    for character in reversed(value):
        code = ord(character)
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 != 0
    raise Phase2AuditError(f"조사를 선택할 한글 이름이 없습니다: {value!r}")


def _fix_yeji_particle(text: str, name: str) -> tuple[str, int]:
    subject = "이" if _has_final_consonant(name) else "가"
    old = f"{name}이"
    new = f"{name}{subject}"
    count = text.count(old) if old != new else 0
    return text.replace(old, new), count


def _transform_yeji(
    records: list[dict[str, Any]], *, policy_sha256: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result: list[dict[str, Any]] = []
    particle_fixes = 0
    task_counts: Counter[str] = Counter()
    ordered = sorted(
        records, key=lambda value: (value["meta"]["candidate_rank"], value["id"])
    )
    for index, source_record in enumerate(ordered):
        messages = copy.deepcopy(source_record["messages"])
        name = str(source_record["meta"]["rule_name_ko"])
        for message in messages:
            message["content"], count = _fix_yeji_particle(message["content"], name)
            particle_fixes += count
        task = (
            "shensha_rule_validation"
            if index % 2 == 0
            else "shensha_neutral_explanation"
        )
        task_counts[task] += 1
        result.append(
            _upgrade_record(
                source_record,
                policy_sha256=policy_sha256,
                tier="RULE_DERIVED",
                task=task,
                messages=messages,
                transformation_steps=(
                    "korean_subject_particle_resolver_v1",
                    "validator_and_user_explanation_task_split_v1",
                ),
                extra_meta={"task_presentation": task},
            )
        )
    expected = Counter(
        {"shensha_rule_validation": 600, "shensha_neutral_explanation": 600}
    )
    if task_counts != expected:
        raise Phase2AuditError(f"YEJI task 50:50 계약이 다릅니다: {dict(task_counts)}")
    return result, {
        "selected_rows": len(result),
        "particle_fixes": particle_fixes,
        "task_counts": dict(sorted(task_counts.items())),
    }


def _sensitive_findings(text: str) -> list[str]:
    findings: list[str] = []
    if any(pattern.search(text) for pattern in PII_PATTERNS):
        findings.append("pii_core_pattern")
    for name, pattern in (
        ("url", URL_PATTERN),
        ("handle", HANDLE_PATTERN),
        ("long_number", LONG_NUMBER_PATTERN),
        ("account_or_card", ACCOUNT_PATTERN),
        ("address_number", ADDRESS_NUMBER_PATTERN),
        ("sensitive_topic", SENSITIVE_PATTERN),
        ("control_character", CONTROL_PATTERN),
    ):
        if pattern.search(text):
            findings.append(name)
    return findings


def _old_aihub_groups(repo_root: Path, config: dict[str, Any]) -> set[str]:
    old = config["parents"]["immutable_staging"]
    root = (
        repo_root
        / "data/staging"
        / DATASET_NAME
        / old["version"]
        / old["build_id"]
        / "records"
    )
    groups: set[str] = set()
    for axis in ("aihub_empathy_single", "aihub_empathy_multiturn"):
        for record in _read_jsonl(root / f"{axis}.jsonl", f"과거 {axis}"):
            group = record.get("meta", {}).get("leakage_group_id")
            if not isinstance(group, str) or not group.startswith("aihub-talk:"):
                raise Phase2AuditError("과거 AI Hub leakage group이 올바르지 않습니다.")
            groups.add(group)
    if len(groups) != 3_600:
        raise Phase2AuditError(
            f"과거 AI Hub 제외 group이 3,600개가 아닙니다: {len(groups)}"
        )
    return groups


def _build_aihub_reservoir(
    *,
    repo_root: Path,
    config: dict[str, Any],
    source_config: dict[str, Any],
    audit_policy: dict[str, Any],
    policy_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reservoir = config["aihub_reservoir"]
    candidates, upstream_report = build_aihub_records(
        source_config=source_config,
        repo_root=repo_root,
        audit_policy=audit_policy,
        single_target=int(reservoir["scanner_candidate_targets"]["single"]),
        multiturn_target=int(reservoir["scanner_candidate_targets"]["multiturn"]),
        seed=int(config["seed"]),
    )
    excluded_groups = _old_aihub_groups(repo_root, config)
    candidates_by_axis: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counters: Counter[str] = Counter()
    for source_record in candidates:
        group = source_record["meta"]["leakage_group_id"]
        if group in excluded_groups:
            counters["excluded_immutable_staging_group"] += 1
            continue
        text = "\n".join(message["content"] for message in source_record["messages"])
        findings = _sensitive_findings(text)
        if findings:
            counters["excluded_enhanced_safety"] += 1
            for finding in findings:
                counters[f"matched_{finding}"] += 1
            continue
        upgraded = _upgrade_record(
            source_record,
            policy_sha256=policy_sha256,
            tier="STYLE_REFERENCE",
            transformation_steps=(
                "immutable_v0_2_group_exclusion_v1",
                "enhanced_local_pii_and_safety_scan_v1",
                "aihub_style_reservoir_v1",
            ),
            extra_meta={
                "local_only": True,
                "external_sharing_allowed": False,
                "reservoir_contract_id": reservoir["contract_id"],
            },
        )
        candidates_by_axis[upgraded["mix_axis"]].append(upgraded)
    selected: list[dict[str, Any]] = []
    expected = {
        "aihub_empathy_single": int(reservoir["single_rows"]),
        "aihub_empathy_multiturn": int(reservoir["multiturn_rows"]),
    }
    for axis, count in expected.items():
        values = sorted(
            candidates_by_axis[axis],
            key=lambda value: (value["meta"]["candidate_rank"], value["id"]),
        )
        if len(values) < count:
            raise Phase2AuditError(
                f"AI Hub 로컬 reservoir 후보가 부족합니다: {axis}={len(values)}/{count}"
            )
        selected.extend(values[:count])
    groups = [record["meta"]["leakage_group_id"] for record in selected]
    if (
        len(groups) != 10_000
        or len(set(groups)) != 10_000
        or set(groups) & excluded_groups
    ):
        raise Phase2AuditError(
            "AI Hub 10K reservoir group 불변·고유 계약이 실패했습니다."
        )
    selected.sort(
        key=lambda value: (value["mix_axis"], value["meta"]["candidate_rank"])
    )
    return selected, {
        "contract_id": reservoir["contract_id"],
        "source_rows_scanned": upstream_report["source_rows_scanned"],
        "eligible_talk_groups": upstream_report["eligible_talk_groups"],
        "selected_rows": len(selected),
        "selected_axis_counts": dict(
            sorted(Counter(record["mix_axis"] for record in selected).items())
        ),
        "excluded_old_group_count": len(excluded_groups),
        "filter_counts": dict(sorted(counters.items())),
        "source_text_in_public_report": False,
        "individual_ids_in_public_report": False,
        "external_sharing_allowed": False,
    }


def _generated_chart(
    *,
    namespace: str,
    sequence: int,
    used: set[str],
    calendar: dict[str, Any],
) -> tuple[tuple[str, str, str, str], dict[str, Any], int]:
    try:
        from lunar_python import Solar
    except ImportError as exc:
        raise Phase2AuditError("lunar-python 1.4.8이 데이터 환경에 없습니다.") from exc
    start = date.fromisoformat(calendar["anchor_start"])
    end = date.fromisoformat(calendar["anchor_end"])
    day_count = (end - start).days + 1
    hours = tuple(int(value) for value in calendar["anchor_hours"])
    max_attempts = int(calendar["max_attempts_per_case"])
    for attempt in range(1, max_attempts + 1):
        digest = hashlib.sha256(
            f"{SEED}|{calendar['algorithm']}|{namespace}|{sequence}|{attempt}".encode()
        ).digest()
        anchor_date = start + timedelta(
            days=int.from_bytes(digest[:8], "big") % day_count
        )
        anchor_hour = hours[digest[8] % len(hours)]
        eight_char = (
            Solar.fromYmdHms(
                anchor_date.year,
                anchor_date.month,
                anchor_date.day,
                anchor_hour,
                0,
                0,
            )
            .getLunar()
            .getEightChar()
        )
        chart = (
            eight_char.getYear(),
            eight_char.getMonth(),
            eight_char.getDay(),
            eight_char.getTime(),
        )
        if not calendar_relations_valid(chart):
            raise Phase2AuditError(
                "달력 backend가 내부 정합성이 없는 명식을 반환했습니다."
            )
        signature = "".join(chart)
        if signature in used:
            continue
        used.add(signature)
        return (
            chart,
            {
                "date": anchor_date.isoformat(),
                "hour": anchor_hour,
                "minute": 0,
                "second": 0,
            },
            attempt,
        )
    raise Phase2AuditError(f"고유 명식 생성에 실패했습니다: {namespace}/{sequence}")


def _chart_text(chart: Sequence[str]) -> str:
    return " ".join(
        f"{label} {pillar}" for label, pillar in zip(PILLAR_KO, chart, strict=True)
    )


def _chart_group(signature: str) -> str:
    return f"chart:{hashlib.sha256(signature.encode()).hexdigest()}"


def _surface_element_counts(
    chart: Sequence[str], policy: dict[str, Any]
) -> dict[str, int]:
    tables = policy["tables"]
    counts: Counter[str] = Counter()
    for pillar in chart:
        counts[tables["stem_elements"][pillar[0]]] += 1
        counts[tables["branch_elements"][pillar[1]]] += 1
    return {element: counts[element] for element in ("목", "화", "토", "금", "수")}


def _base_generated_record(
    *,
    record_id: str,
    source: str,
    mix_axis: str,
    source_variant: str,
    source_revision: str,
    license_expression: str,
    usage_class: str,
    attribution_ids: Sequence[str],
    transformation_chain: Sequence[str],
    task: str,
    messages: list[dict[str, str]],
    tier: str,
    raw_hash: str,
    source_group_id: str,
    leakage_group_ids: Sequence[str],
    candidate_rank: str,
    policy_sha256: str,
    extra_meta: dict[str, Any],
    label_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary = min(leakage_group_ids)
    template = {
        "id": record_id,
        "source": source,
        "mix_axis": mix_axis,
        "source_variant": source_variant,
        "source_revision": source_revision,
        "license_expression": license_expression,
        "usage_class": usage_class,
        "provenance_status": "verified",
        "attribution_ids": list(attribution_ids),
        "transformation_chain": list(transformation_chain),
        "domain": "saju",
        "task": task,
        "messages": messages,
        "label": {
            "stage": "D",
            "kind": "deterministic_project_label",
            "origin": "project_policy",
            "human_review": "not_performed",
        },
        "quality_flags": {},
        "meta": {
            "raw_hash": raw_hash,
            "source_group_id": source_group_id,
            "leakage_group_id": primary,
            "candidate_rank": candidate_rank,
            **extra_meta,
        },
    }
    return _upgrade_record(
        template,
        policy_sha256=policy_sha256,
        tier=tier,
        leakage_group_ids=leakage_group_ids,
        label_updates=label_updates,
    )


def _qa_messages(
    chart: tuple[str, str, str, str],
    category: str,
    policy: dict[str, Any],
) -> tuple[list[dict[str, str]], list[str]]:
    tables = policy["tables"]
    chart_value = _chart_text(chart)
    day_stem = chart[2][0]
    system = (
        "제공된 구조화 명식과 고정된 프로젝트 계산표만 사용해 답하세요. "
        "생년월일을 추측하거나 신강약·격국·용신·미래 사건을 덧붙이지 마세요."
    )
    if category == "stem_branch_identity":
        user = f"구조화 명식: {chart_value}\n네 기둥을 년주부터 시주 순서로 정확히 적어 주세요."
        answer = (
            f"년주 {chart[0]}, 월주 {chart[1]}, 일주 {chart[2]}, 시주 {chart[3]}입니다."
        )
        rule_ids = ["pillar_order_identity_v1"]
    elif category == "yin_yang_elements_and_surface_counts":
        counts = _surface_element_counts(chart, policy)
        tokens = []
        for pillar in chart:
            tokens.extend(
                [
                    f"{pillar[0]}={tables['stem_yin_yang'][pillar[0]]}·{tables['stem_elements'][pillar[0]]}",
                    f"{pillar[1]}={tables['branch_yin_yang'][pillar[1]]}·{tables['branch_elements'][pillar[1]]}",
                ]
            )
        user = f"구조화 명식: {chart_value}\n각 글자의 음양·오행과 표면 오행 수를 계산해 주세요."
        answer = (
            "; ".join(tokens)
            + ". 표면 오행 수는 "
            + ", ".join(f"{key} {value}" for key, value in counts.items())
            + "입니다."
        )
        rule_ids = [
            "stem_branch_yinyang_element_v1",
            "surface_element_count_8_chars_v1",
        ]
    elif category == "hidden_stems":
        user = f"구조화 명식: {chart_value}\n각 지지의 지장간을 정기 우선 순서로 적어 주세요."
        answer = (
            "; ".join(
                f"{label} {pillar[1]}={','.join(tables['hidden_stems_main_first'][pillar[1]])}"
                for label, pillar in zip(PILLAR_KO, chart, strict=True)
            )
            + "입니다."
        )
        rule_ids = ["hidden_stems_main_first_v1"]
    elif category == "stem_ten_gods":
        user = f"구조화 명식: {chart_value}\n일간 {day_stem}을 기준으로 각 천간의 십신을 적어 주세요."
        answer = (
            "; ".join(
                f"{label} {stem_ten_god(day_stem, pillar[0])}"
                for label, pillar in zip(PILLAR_KO, chart, strict=True)
            )
            + "입니다."
        )
        rule_ids = ["stem_ten_god_v1"]
    elif category == "branch_ten_gods":
        user = (
            f"구조화 명식: {chart_value}\n일간 {day_stem}과 각 지지의 정기를 기준으로 "
            "지지 십신을 적어 주세요."
        )
        answer = (
            "; ".join(
                f"{label} {pillar[1]}(정기 {tables['hidden_stems_main_first'][pillar[1]][0]})="
                f"{hidden_stem_branch_ten_god(day_stem, pillar[1])}"
                for label, pillar in zip(PILLAR_KO, chart, strict=True)
            )
            + "입니다."
        )
        rule_ids = ["branch_main_hidden_stem_ten_god_v1"]
    else:
        raise Phase2AuditError(
            f"지원하지 않는 deterministic QA category입니다: {category}"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": answer},
    ], rule_ids


def _build_deterministic_qa(
    *,
    count_per_category: int,
    used_charts: set[str],
    calendar: dict[str, Any],
    policy: dict[str, Any],
    policy_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    attempts: list[int] = []
    for category_index, category in enumerate(QA_CATEGORIES):
        for index in range(count_per_category):
            sequence = category_index * count_per_category + index
            chart, anchor, attempt = _generated_chart(
                namespace="deterministic-qa",
                sequence=sequence,
                used=used_charts,
                calendar=calendar,
            )
            attempts.append(attempt)
            signature = "".join(chart)
            messages, rule_ids = _qa_messages(chart, category, policy)
            identity = f"{category}|{signature}|{sequence}"
            group = _chart_group(signature)
            records.append(
                _base_generated_record(
                    record_id=f"deterministic_saju_qa:{hashlib.sha256(identity.encode()).hexdigest()}",
                    source="project_deterministic_saju",
                    mix_axis="deterministic_saju_qa",
                    source_variant=category,
                    source_revision="saju-calculation-policy-v1.0.0",
                    license_expression="PROJECT-GENERATED",
                    usage_class="train_allow",
                    attribution_ids=("saju-calculation-policy-v1.0.0",),
                    transformation_chain=(
                        "private_calendar_anchor_generation",
                        "independently_verified_calculation_table",
                        "deterministic_korean_qa_render",
                    ),
                    task=f"deterministic_{category}",
                    messages=messages,
                    tier="HARD_GT",
                    raw_hash=_sha256_json(
                        {
                            "chart": chart,
                            "category": category,
                            "policy_sha256": policy_sha256,
                            "anchor": anchor,
                        }
                    ),
                    source_group_id=f"qa-case:{hashlib.sha256(identity.encode()).hexdigest()}",
                    leakage_group_ids=(group,),
                    candidate_rank=stable_rank(
                        SEED, "deterministic_saju_qa", category, signature
                    ),
                    policy_sha256=policy_sha256,
                    extra_meta={
                        "chart_signature": signature,
                        "qa_category": category,
                        "applied_rule_ids": rule_ids,
                        "hard_claims_verified": True,
                        "calendar_anchor": anchor,
                        "calendar_backend": "lunar-python",
                        "calendar_backend_version": "1.4.8",
                        "calendar_generation_attempts": attempt,
                        "birth_to_pillars_training_target": False,
                    },
                    label_updates={
                        "kind": "deterministic_hard_ground_truth",
                        "origin": "project_policy_and_independent_oracle",
                        "applied_rule_ids": rule_ids,
                    },
                )
            )
    records.sort(key=lambda value: (value["meta"]["candidate_rank"], value["id"]))
    counts = Counter(record["meta"]["qa_category"] for record in records)
    expected = Counter({category: count_per_category for category in QA_CATEGORIES})
    if counts != expected:
        raise Phase2AuditError(f"deterministic QA 범주 수량이 다릅니다: {dict(counts)}")
    return records, {
        "selected_rows": len(records),
        "category_counts": dict(sorted(counts.items())),
        "unique_charts": len({record["meta"]["chart_signature"] for record in records}),
        "calendar_generation_attempts": {
            "max": max(attempts),
            "mean": round(sum(attempts) / len(attempts), 6),
        },
        "hard_gt_fields_only": True,
        "weak_heuristic_fields_included": False,
    }


def _build_bridge(
    *,
    source_records: list[dict[str, Any]],
    used_charts: set[str],
    calendar: dict[str, Any],
    policy: dict[str, Any],
    policy_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tables = policy["tables"]
    prompts = (
        "오늘 기록에서 가장 크게 남은 감정을 한 단어로 적어보는 건 어떨까요?",
        "지금 스스로에게 해주고 싶은 현실적인 말을 한 문장 적어보시겠어요?",
        "오늘 바꿀 수 있는 아주 작은 행동 하나를 골라보는 건 어떨까요?",
        "이 감정이 조금 가벼워졌던 순간이 있었는지 떠올려보시겠어요?",
    )
    records: list[dict[str, Any]] = []
    attempts: list[int] = []
    origin_counts: Counter[str] = Counter()
    for sequence, source_record in enumerate(source_records):
        origin = (
            "single"
            if source_record["mix_axis"] == "aihub_empathy_single"
            else "multiturn"
        )
        origin_counts[origin] += 1
        chart, anchor, attempt = _generated_chart(
            namespace="saju-diary-bridge",
            sequence=sequence,
            used=used_charts,
            calendar=calendar,
        )
        attempts.append(attempt)
        signature = "".join(chart)
        day_stem = chart[2][0]
        day_branch = chart[2][1]
        day_element = tables["stem_elements"][day_stem]
        day_yinyang = tables["stem_yin_yang"][day_stem]
        main_hidden = tables["hidden_stems_main_first"][day_branch][0]
        branch_god = hidden_stem_branch_ten_god(day_stem, day_branch)
        source_messages = source_record["messages"]
        original_system = source_messages[0]["content"]
        dialogue = [copy.deepcopy(message) for message in source_messages[1:]]
        if not dialogue or dialogue[-1]["role"] != "assistant":
            raise Phase2AuditError(
                "앱 브리지 AI Hub 부모 대화가 assistant로 끝나지 않습니다."
            )
        original_final = dialogue.pop()["content"]
        structured = (
            f"구조화 명식: {_chart_text(chart)}\n"
            f"검증 사실: 일간 {day_stem}은 {day_yinyang}·{day_element}이고, "
            f"일지 {day_branch}의 정기 {main_hidden}은 일간 기준 {branch_god}입니다.\n"
            "아래 일기·감정 표현에 공감하되, 명식을 감정의 원인이나 미래의 결정 요인으로 말하지 마세요."
        )
        first_user = next(
            (
                index
                for index, message in enumerate(dialogue)
                if message["role"] == "user"
            ),
            None,
        )
        if first_user is None:
            raise Phase2AuditError("앱 브리지 부모에 user 메시지가 없습니다.")
        dialogue[first_user]["content"] = (
            structured + "\n일기·감정: " + dialogue[first_user]["content"]
        )
        reflection = prompts[
            int(source_record["meta"]["candidate_rank"][:2], 16) % len(prompts)
        ]
        final = (
            f"{original_final}\n참고로 제공된 구조화 명식에서 일간은 {day_stem}({day_yinyang}·{day_element})이고 "
            f"일지 정기 기준 십신은 {branch_god}입니다. 이 구조가 지금 감정의 원인이나 미래를 "
            f"결정한다는 뜻은 아닙니다. {reflection}"
        )
        messages = [
            {
                "role": "system",
                "content": (
                    original_system
                    + " 구조화 사주 사실은 검증된 범위에서만 짧게 연결하고, 감정의 원인·진단·예언으로 사용하지 마세요."
                ),
            },
            *dialogue,
            {"role": "assistant", "content": final},
        ]
        aihub_group = source_record["meta"]["leakage_group_id"]
        chart_group = _chart_group(signature)
        identity = f"{aihub_group}|{signature}|{sequence}"
        value = _base_generated_record(
            record_id=f"saju_diary_bridge:{hashlib.sha256(identity.encode()).hexdigest()}",
            source="aihub_empathy",
            mix_axis="saju_diary_bridge",
            source_variant=f"aihub_{origin}_with_verified_chart",
            source_revision=source_record["source_revision"],
            license_expression=source_record["license_expression"],
            usage_class=source_record["usage_class"],
            attribution_ids=(
                *source_record["attribution_ids"],
                "saju-calculation-policy-v1.0.0",
            ),
            transformation_chain=(
                *source_record["transformation_chain"],
                "unique_aihub_chart_pairing_v1",
                "verified_hard_fact_bridge_v1",
                "noncausal_reflection_prompt_v1",
            ),
            task="saju_diary_empathy_bridge",
            messages=messages,
            tier="STYLE_REFERENCE",
            raw_hash=_sha256_json(
                {
                    "parent_raw_hash": source_record["meta"]["raw_hash"],
                    "chart": chart,
                    "anchor": anchor,
                    "policy_sha256": policy_sha256,
                }
            ),
            source_group_id=aihub_group,
            leakage_group_ids=(aihub_group, chart_group),
            candidate_rank=stable_rank(SEED, "saju_diary_bridge", origin, aihub_group),
            policy_sha256=policy_sha256,
            extra_meta={
                "chart_signature": signature,
                "aihub_origin": origin,
                "emotion_type": source_record["meta"].get("emotion_type", ""),
                "applied_rule_ids": [
                    "stem_yinyang_element_v1",
                    "branch_main_hidden_stem_ten_god_v1",
                    "noncausal_bridge_v1",
                ],
                "hard_claims_verified": True,
                "calendar_anchor": anchor,
                "calendar_backend": "lunar-python",
                "calendar_backend_version": "1.4.8",
                "calendar_generation_attempts": attempt,
                "birth_to_pillars_training_target": False,
                "external_sharing_allowed": False,
            },
            label_updates={
                "kind": "restricted_style_reference_with_verified_hard_claim",
                "origin": "aihub_response_and_project_policy",
                "hard_claim_tier": "HARD_GT",
            },
        )
        value["domain"] = "saju_diary"
        records.append(value)
    records.sort(key=lambda value: (value["meta"]["candidate_rank"], value["id"]))
    if origin_counts != Counter({"single": 1800, "multiturn": 1800}):
        raise Phase2AuditError(f"앱 브리지 원천 균형이 다릅니다: {dict(origin_counts)}")
    return records, {
        "selected_rows": len(records),
        "origin_counts": dict(sorted(origin_counts.items())),
        "unique_aihub_groups": len(
            {record["meta"]["source_group_id"] for record in records}
        ),
        "unique_charts": len({record["meta"]["chart_signature"] for record in records}),
        "calendar_generation_attempts": {
            "max": max(attempts),
            "mean": round(sum(attempts) / len(attempts), 6),
        },
        "weak_heuristic_fields_included": False,
        "external_sharing_allowed": False,
    }


def _record_text(record: dict[str, Any]) -> str:
    return "\n".join(message["content"] for message in record.get("messages", []))


def _validate_record(
    record: dict[str, Any],
    *,
    axis: str,
    expected_source: str,
    policy_sha256: str,
) -> list[str]:
    required = {
        "schema_version",
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
    signals: list[str] = []
    if set(record) != required:
        signals.append("record_schema_keys")
        return signals
    if (
        record["schema_version"] != RECORD_SCHEMA_VERSION
        or record["mix_axis"] != axis
        or record["source"] != expected_source
        or record["provenance_status"] != "verified"
        or not isinstance(record["id"], str)
        or not record["id"]
    ):
        signals.append("record_identity")
    label = record.get("label")
    if (
        not isinstance(label, dict)
        or label.get("tier") not in LABEL_TIERS
        or label.get("human_review") != "not_performed"
        or label.get("quality_certification") is not False
    ):
        signals.append("label_tier_contract")
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 3:
        signals.append("messages_structure")
        return signals
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            or unicodedata.normalize("NFC", message["content"]) != message["content"]
            or CONTROL_PATTERN.search(message["content"])
        ):
            signals.append("messages_content")
            break
    if messages[-1].get("role") != "assistant" or not any(
        message.get("role") == "user" for message in messages[:-1]
    ):
        signals.append("messages_roles")
    meta = record.get("meta")
    if not isinstance(meta, dict):
        signals.append("meta_structure")
        return signals
    groups = meta.get("leakage_group_ids")
    if (
        not isinstance(groups, list)
        or not groups
        or groups != sorted(set(groups))
        or meta.get("leakage_group_id") not in groups
    ):
        signals.append("leakage_group_ids")
    for key in ("raw_hash", "message_sha256", "candidate_rank", "source_group_id"):
        value = meta.get(key)
        if not isinstance(value, str) or not value:
            signals.append(f"meta_{key}")
    if meta.get("message_sha256") != sha256_json(messages):
        signals.append("message_sha256")
    if meta.get("calculation_policy_sha256") != policy_sha256:
        signals.append("calculation_policy_sha256")
    text = _record_text(record)
    if any(pattern.search(text) for pattern in PII_PATTERNS):
        signals.append("pii_core_pattern")
    if (
        URL_PATTERN.search(text)
        or HANDLE_PATTERN.search(text)
        or LONG_NUMBER_PATTERN.search(text)
    ):
        signals.append("extended_pii_pattern")
    if NEMOTRON_DISCLAIMER in _assistant_text(
        record
    ) or BAZI_DISCLAIMER in _assistant_text(record):
        signals.append("fixed_assistant_disclaimer")
    if axis == "nemotron_saju":
        assistant = _assistant_text(record)
        if NAME_PATTERN.search(assistant):
            signals.append("target_only_name")
        if FULL_BIRTHDATE_LOCAL_PATTERN.search(assistant):
            signals.append("target_only_full_birthdate")
        if _foreign_cjk(assistant):
            signals.append("foreign_cjk_residue")
        chart = _nemotron_chart(record)
        user = next(
            message["content"] for message in messages if message["role"] == "user"
        )
        corrected, _ = _correct_ten_god_line(user, chart)
        if corrected != user:
            signals.append("branch_ten_god_mismatch")
        if "부족 오행: 없음" in user and re.search(
            r"(?:보완|채우).{0,20}(?:색|방향|소품|착용)", assistant
        ):
            signals.append("remedy_contradiction")
    if axis == "yeji_shensha_derived":
        name = str(meta.get("rule_name_ko", ""))
        if name and not _has_final_consonant(name) and f"{name}이" in text:
            signals.append("yeji_particle_error")
    if axis == "deterministic_saju_qa":
        if (
            label.get("tier") != "HARD_GT"
            or meta.get("hard_claims_verified") is not True
        ):
            signals.append("hard_fact_contract")
        if meta.get("qa_category") not in QA_CATEGORIES:
            signals.append("qa_category")
    if axis == "saju_diary_bridge":
        if (
            len(groups) != 2
            or not any(group.startswith("aihub-talk:") for group in groups)
            or not any(group.startswith("chart:") for group in groups)
        ):
            signals.append("bridge_dual_leakage")
        if (
            label.get("tier") != "STYLE_REFERENCE"
            or label.get("hard_claim_tier") != "HARD_GT"
        ):
            signals.append("bridge_label_contract")
        if meta.get("external_sharing_allowed") is not False:
            signals.append("restricted_sharing_flag")
    return sorted(set(signals))


def _validate_and_classify(
    *,
    records_by_axis: dict[str, list[dict[str, Any]]],
    reservoir: list[dict[str, Any]],
    config: dict[str, Any],
    policy_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_sources = {axis: str(config["axes"][axis]["source"]) for axis in AXES}
    seen_ids: set[str] = set()
    seen_messages: set[str] = set()
    raw_groups: dict[str, str] = {}
    risk_items: list[dict[str, Any]] = []
    severity_counts: Counter[str] = Counter()
    signal_counts: Counter[str] = Counter()
    axis_severity: dict[str, Counter[str]] = defaultdict(Counter)

    def classify(record: dict[str, Any], scope: str) -> None:
        axis = str(record.get("mix_axis"))
        signals = _validate_record(
            record,
            axis=axis,
            expected_source=expected_sources[axis],
            policy_sha256=policy_sha256,
        )
        critical_signals = {
            "pii_core_pattern",
            "extended_pii_pattern",
            "record_schema_keys",
            "record_identity",
            "messages_structure",
            "messages_content",
            "messages_roles",
            "leakage_group_ids",
            "message_sha256",
        }
        high_signals = {
            "target_only_name",
            "target_only_full_birthdate",
            "foreign_cjk_residue",
            "fixed_assistant_disclaimer",
            "branch_ten_god_mismatch",
            "remedy_contradiction",
            "yeji_particle_error",
            "hard_fact_contract",
            "qa_category",
            "bridge_dual_leakage",
            "bridge_label_contract",
            "restricted_sharing_flag",
            "calculation_policy_sha256",
            "label_tier_contract",
        }
        if any(
            signal in critical_signals or signal.startswith("meta_")
            for signal in signals
        ):
            severity = "critical"
        elif any(signal in high_signals for signal in signals):
            severity = "high"
        elif record.get("label", {}).get("tier") in {
            "SOFT_INTERPRETATION",
            "STYLE_REFERENCE",
        }:
            severity = "medium"
            signals = [*signals, "reference_label_requires_runtime_grounding"]
        else:
            severity = "low"
            signals = [*signals, "deterministic_or_rule_derived_label"]
        signals = sorted(set(signals))
        severity_counts[severity] += 1
        axis_severity[axis][severity] += 1
        signal_counts.update(signals)
        risk_items.append(
            {
                "schema_version": RECORD_SCHEMA_VERSION,
                "record_id": record["id"],
                "mix_axis": axis,
                "scope": scope,
                "severity": severity,
                "signals": signals,
                "message_sha256": record["meta"]["message_sha256"],
                "human_domain_review_performed": False,
            }
        )

    for axis in AXES:
        rows = records_by_axis[axis]
        expected = int(config["axes"][axis]["staging_rows"])
        if len(rows) != expected:
            raise Phase2AuditError(
                f"{axis} staging 수량이 다릅니다: {len(rows)}/{expected}"
            )
        for record in rows:
            record_id = record["id"]
            message_hash = record["meta"]["message_sha256"]
            if record_id in seen_ids:
                raise Phase2AuditError(f"staging ID가 중복됐습니다: {record_id}")
            if message_hash in seen_messages:
                raise Phase2AuditError(f"staging message가 중복됐습니다: {record_id}")
            raw_hash = record["meta"]["raw_hash"]
            source_group = record["meta"]["source_group_id"]
            previous_group = raw_groups.get(raw_hash)
            if previous_group is not None and previous_group != source_group:
                raise Phase2AuditError(
                    "서로 다른 source group 사이에 raw hash가 중복됐습니다."
                )
            seen_ids.add(record_id)
            seen_messages.add(message_hash)
            raw_groups[raw_hash] = source_group
            classify(record, "staging_24k")
    reservoir_groups: set[str] = set()
    for record in reservoir:
        group = record["meta"]["leakage_group_id"]
        if group in reservoir_groups:
            raise Phase2AuditError("AI Hub reservoir leakage group이 중복됐습니다.")
        reservoir_groups.add(group)
        classify(record, "aihub_local_reservoir_10k")
    if len(reservoir) != 10_000 or len(reservoir_groups) != 10_000:
        raise Phase2AuditError(
            "AI Hub reservoir는 정확히 10,000개 고유 group이어야 합니다."
        )
    critical_high = severity_counts["critical"] + severity_counts["high"]
    if critical_high != int(config["quality_contract"]["critical_or_high_allowed"]):
        raise Phase2AuditError(
            f"자동 위험 Gate에 critical/high가 남았습니다: {dict(severity_counts)}"
        )
    return risk_items, {
        "status": "passed",
        "scanned_rows": len(risk_items),
        "staging_rows": 24_000,
        "aihub_local_reservoir_rows": 10_000,
        "severity_counts": dict(sorted(severity_counts.items())),
        "signal_counts": dict(sorted(signal_counts.items())),
        "axis_severity_counts": {
            axis: dict(sorted(counts.items()))
            for axis, counts in sorted(axis_severity.items())
        },
        "critical_or_high_rows": critical_high,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "raw_samples_in_report": False,
    }


def _private_artifacts() -> list[str]:
    return [
        *(f"records/{axis}.jsonl" for axis in AXES),
        "reservoirs/aihub_style_10k.jsonl",
        "candidate_order.jsonl",
        "risk/quality_risk_items.jsonl",
        "reports/adapter_report.json",
        "reports/quality_report.json",
    ]


def _public_artifacts() -> list[str]:
    return ["aggregate.json", "quality_gate.json", "TECHNICAL_ACCEPTANCE.json"]


def _candidate_order(
    records_by_axis: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for axis in AXES:
        for record in sorted(
            records_by_axis[axis],
            key=lambda value: (value["meta"]["candidate_rank"], value["id"]),
        ):
            rows.append(
                {
                    "schema_version": RECORD_SCHEMA_VERSION,
                    "id": record["id"],
                    "mix_axis": axis,
                    "candidate_rank": record["meta"]["candidate_rank"],
                    "leakage_group_id": record["meta"]["leakage_group_id"],
                    "leakage_group_ids": record["meta"]["leakage_group_ids"],
                    "source_group_id": record["meta"]["source_group_id"],
                }
            )
    if len(rows) != 24_000:
        raise Phase2AuditError("candidate_order 수량이 24,000이 아닙니다.")
    return rows


def _materialize_records(
    context: dict[str, Any], repo_root: Path
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
]:
    config = context["config"]
    parents = config["parents"]
    source_config = load_source_config(
        _safe_repo_path(repo_root, parents["source_config"])
    )
    audit_policy = _load_json(
        _safe_repo_path(repo_root, parents["audit_policy"]), "audit policy"
    )
    correction = _load_json(
        _safe_repo_path(repo_root, parents["correction_manifest"]),
        "YEJI correction manifest",
    )
    language_bank = _load_json(
        _safe_repo_path(repo_root, parents["language_bank"]), "language bank"
    )["content"]
    policy = _load_json(
        _safe_repo_path(repo_root, parents["calculation_policy"]),
        "사주 계산 정책",
    )
    policy_sha256 = context["policy_validation"]["policy_sha256"]
    adapter_reports: dict[str, Any] = {}

    nemotron_candidates, upstream_nemotron = build_nemotron_records(
        source_config=source_config,
        repo_root=repo_root,
        audit_policy=audit_policy,
        target_by_variant=config["axes"]["nemotron_saju"]["raw_candidate_pool"],
        seed=config["seed"],
    )
    nemotron, adapter_reports["nemotron_saju"] = _transform_nemotron(
        nemotron_candidates,
        target_by_variant=config["axes"]["nemotron_saju"]["variants"]["staging"],
        policy_sha256=policy_sha256,
    )
    adapter_reports["nemotron_saju"]["upstream_adapter"] = upstream_nemotron

    bazi_source, upstream_bazi = build_bazi_records(
        source_config=source_config,
        repo_root=repo_root,
        language_bank=language_bank["bazi"],
        target_rows=config["axes"]["bazi_sft"]["staging_rows"],
        seed=config["seed"],
    )
    bazi, adapter_reports["bazi_sft"] = _transform_bazi(
        bazi_source, policy_sha256=policy_sha256
    )
    adapter_reports["bazi_sft"]["upstream_adapter"] = upstream_bazi

    reservoir, adapter_reports["aihub_reservoir"] = _build_aihub_reservoir(
        repo_root=repo_root,
        config=config,
        source_config=source_config,
        audit_policy=audit_policy,
        policy_sha256=policy_sha256,
    )
    reservoir_by_axis: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in reservoir:
        reservoir_by_axis[record["mix_axis"]].append(record)
    for values in reservoir_by_axis.values():
        values.sort(key=lambda value: (value["meta"]["candidate_rank"], value["id"]))
    aihub_single = reservoir_by_axis["aihub_empathy_single"][:1_800]
    aihub_multi = reservoir_by_axis["aihub_empathy_multiturn"][:1_800]
    bridge_parents = [
        *reservoir_by_axis["aihub_empathy_single"][1_800:3_600],
        *reservoir_by_axis["aihub_empathy_multiturn"][1_800:3_600],
    ]
    if (
        len(aihub_single) != 1_800
        or len(aihub_multi) != 1_800
        or len(bridge_parents) != 3_600
    ):
        raise Phase2AuditError("AI Hub reservoir의 pure/bridge 할당 수량이 다릅니다.")

    yeji_source, upstream_yeji = build_yeji_records(
        source_config=source_config,
        repo_root=repo_root,
        correction_manifest=correction,
        language_bank=language_bank["yeji"],
        target_rows=config["axes"]["yeji_shensha_derived"]["staging_rows"],
        seed=config["seed"],
        calendar_backend=config["calendar_backend"],
    )
    yeji, adapter_reports["yeji_shensha_derived"] = _transform_yeji(
        yeji_source, policy_sha256=policy_sha256
    )
    adapter_reports["yeji_shensha_derived"]["upstream_adapter"] = upstream_yeji

    used_charts = {
        str(record["meta"]["chart_signature"])
        for record in [*nemotron, *bazi, *yeji]
        if isinstance(record["meta"].get("chart_signature"), str)
    }
    deterministic_qa, adapter_reports["deterministic_saju_qa"] = (
        _build_deterministic_qa(
            count_per_category=config["axes"]["deterministic_saju_qa"]["category_rows"],
            used_charts=used_charts,
            calendar=config["calendar_backend"],
            policy=policy,
            policy_sha256=policy_sha256,
        )
    )
    bridge, adapter_reports["saju_diary_bridge"] = _build_bridge(
        source_records=bridge_parents,
        used_charts=used_charts,
        calendar=config["calendar_backend"],
        policy=policy,
        policy_sha256=policy_sha256,
    )
    records_by_axis = {
        "nemotron_saju": nemotron,
        "bazi_sft": bazi,
        "aihub_empathy_single": aihub_single,
        "aihub_empathy_multiturn": aihub_multi,
        "yeji_shensha_derived": yeji,
        "deterministic_saju_qa": deterministic_qa,
        "saju_diary_bridge": bridge,
    }
    risk_items, quality_report = _validate_and_classify(
        records_by_axis=records_by_axis,
        reservoir=reservoir,
        config=config,
        policy_sha256=policy_sha256,
    )
    return records_by_axis, reservoir, adapter_reports, risk_items, quality_report


def execute_quality_build(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if private_root.exists() or public_root.exists():
        if private_root.exists() and public_root.exists():
            return {
                **verify_quality_build(context, repo_root),
                "mode": "reused",
                "writes_performed": False,
            }
        raise Phase2AuditError(
            "품질 보정 private/public build가 부분적으로 존재합니다."
        )
    private_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_root.parent.mkdir(parents=True, exist_ok=True, mode=PUBLIC_DIR_MODE)
    private_tmp = Path(
        tempfile.mkdtemp(prefix=f".{private_root.name}-", dir=private_root.parent)
    )
    public_tmp = Path(
        tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent)
    )
    private_promoted = False
    public_promoted = False
    try:
        records_by_axis, reservoir, adapters, risk_items, quality = (
            _materialize_records(context, repo_root)
        )
        order = _candidate_order(records_by_axis)
        for axis in AXES:
            _write_jsonl_once(
                private_tmp / f"records/{axis}.jsonl",
                records_by_axis[axis],
                mode=PRIVATE_FILE_MODE,
            )
        _write_jsonl_once(
            private_tmp / "reservoirs/aihub_style_10k.jsonl",
            reservoir,
            mode=PRIVATE_FILE_MODE,
        )
        _write_jsonl_once(
            private_tmp / "candidate_order.jsonl", order, mode=PRIVATE_FILE_MODE
        )
        _write_jsonl_once(
            private_tmp / "risk/quality_risk_items.jsonl",
            risk_items,
            mode=PRIVATE_FILE_MODE,
        )
        _write_json_once(
            private_tmp / "reports/adapter_report.json",
            {
                "schema_version": RECORD_SCHEMA_VERSION,
                "report_type": "quality_v2_adapter_report",
                "adapters": adapters,
                "raw_samples_in_report": False,
            },
            mode=PRIVATE_FILE_MODE,
        )
        _write_json_once(
            private_tmp / "reports/quality_report.json",
            {
                "schema_version": RECORD_SCHEMA_VERSION,
                "report_type": "quality_v2_full_risk_report",
                **quality,
            },
            mode=PRIVATE_FILE_MODE,
        )
        private_artifacts = _artifact_hashes(private_tmp, _private_artifacts())
        private_manifest = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "report_type": "quality_corrected_staging_private_build",
            "dataset_name": DATASET_NAME,
            "staging_version": STAGING_VERSION,
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": private_artifacts,
            "row_counts": {axis: len(records_by_axis[axis]) for axis in AXES},
            "aihub_local_reservoir_rows": len(reservoir),
            "status": "automated_technical_acceptance_for_phase4",
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
            "workspace_base_commit": context["workspace_base_commit"],
            "generated_on": "2026-08-29",
        }
        _write_json_once(
            private_tmp / "build_manifest.json",
            private_manifest,
            mode=PRIVATE_FILE_MODE,
        )
        private_manifest_sha256 = sha256_file(private_tmp / "build_manifest.json")

        aggregate = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "report_type": "quality_corrected_staging_public_aggregate",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "row_counts": private_manifest["row_counts"],
            "total_rows": 24_000,
            "aihub_local_reservoir": {
                "rows": 10_000,
                "single_rows": 5_000,
                "multiturn_rows": 5_000,
                "unique_groups": 10_000,
                "local_only": True,
                "source_text_shared": False,
                "individual_ids_shared": False,
                "individual_hashes_shared": False,
            },
            "label_tier_counts": dict(
                sorted(
                    Counter(
                        record["label"]["tier"]
                        for rows in records_by_axis.values()
                        for record in rows
                    ).items()
                )
            ),
            "calculation_policy_validation": context["policy_validation"],
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "phase5_training_performed": False,
            "raw_samples_in_report": False,
        }
        quality_gate = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "report_type": "quality_corrected_staging_automated_gate",
            "build_id": context["build_id"],
            "status": "passed",
            **quality,
            "training_promotion_allowed": False,
        }
        acceptance = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "report_type": "quality_corrected_staging_technical_acceptance",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "private_manifest_sha256": private_manifest_sha256,
            "acceptance_basis": "explicit_owner_automated_technical_acceptance",
            "status": "accepted_for_phase4_preflight",
            "full_automated_scan_performed": True,
            "critical_or_high_rows": quality["critical_or_high_rows"],
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
        }
        _write_json_once(
            public_tmp / "aggregate.json", aggregate, mode=PUBLIC_FILE_MODE
        )
        _write_json_once(
            public_tmp / "quality_gate.json", quality_gate, mode=PUBLIC_FILE_MODE
        )
        _write_json_once(
            public_tmp / "TECHNICAL_ACCEPTANCE.json",
            acceptance,
            mode=PUBLIC_FILE_MODE,
        )
        public_manifest = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "report_type": "quality_corrected_staging_public_manifest",
            "dataset_name": DATASET_NAME,
            "staging_version": STAGING_VERSION,
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "private_manifest_sha256": private_manifest_sha256,
            "artifact_sha256": _artifact_hashes(public_tmp, _public_artifacts()),
            "status": "accepted_for_phase4_preflight",
            "contains_aihub_source_text": False,
            "contains_individual_aihub_ids_or_hashes": False,
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
            "generated_on": "2026-08-29",
        }
        _write_json_once(
            public_tmp / "build_manifest.json",
            public_manifest,
            mode=PUBLIC_FILE_MODE,
        )
        for directory in [
            private_tmp,
            *[path for path in private_tmp.rglob("*") if path.is_dir()],
        ]:
            directory.chmod(PRIVATE_DIR_MODE)
        for directory in [
            public_tmp,
            *[path for path in public_tmp.rglob("*") if path.is_dir()],
        ]:
            directory.chmod(PUBLIC_DIR_MODE)
        os.replace(private_tmp, private_root)
        private_promoted = True
        os.replace(public_tmp, public_root)
        public_promoted = True
    finally:
        if not private_promoted:
            shutil.rmtree(private_tmp, ignore_errors=True)
        if not public_promoted:
            shutil.rmtree(public_tmp, ignore_errors=True)
        if private_promoted and not public_promoted:
            raise Phase2AuditError(
                "private build만 승격되고 public build 승격에 실패했습니다. 수동 삭제 없이 중단합니다."
            )
    return {
        **verify_quality_build(context, repo_root),
        "mode": "built",
        "writes_performed": True,
    }


def verify_quality_build(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if (
        private_root.is_symlink()
        or public_root.is_symlink()
        or not private_root.is_dir()
        or not public_root.is_dir()
    ):
        raise Phase2AuditError("품질 보정 private/public build 경로가 없습니다.")
    if stat.S_IMODE(private_root.stat().st_mode) & 0o077:
        raise Phase2AuditError("품질 보정 private build 권한이 너무 넓습니다.")
    private = _load_json(private_root / "build_manifest.json", "private build manifest")
    public = _load_json(public_root / "build_manifest.json", "public build manifest")
    if (
        private.get("build_id") != context["build_id"]
        or private.get("build_sha256") != context["build_sha256"]
        or private.get("build_inputs") != context["build_inputs"]
        or private.get("status") != "automated_technical_acceptance_for_phase4"
        or private.get("training_promotion_allowed") is not False
        or public.get("build_id") != context["build_id"]
        or public.get("private_manifest_sha256")
        != sha256_file(private_root / "build_manifest.json")
        or public.get("contains_aihub_source_text") is not False
        or public.get("contains_individual_aihub_ids_or_hashes") is not False
    ):
        raise Phase2AuditError("품질 보정 build identity·공개 경계가 다릅니다.")
    _verify_hashes(private_root, private.get("artifact_sha256"), "private build")
    _verify_hashes(public_root, public.get("artifact_sha256"), "public build")
    record_counts: dict[str, int] = {}
    all_ids: set[str] = set()
    all_messages: set[str] = set()
    for axis in AXES:
        rows = _read_jsonl(private_root / f"records/{axis}.jsonl", axis)
        record_counts[axis] = len(rows)
        for record in rows:
            signals = _validate_record(
                record,
                axis=axis,
                expected_source=context["config"]["axes"][axis]["source"],
                policy_sha256=context["policy_validation"]["policy_sha256"],
            )
            if signals:
                raise Phase2AuditError(
                    f"품질 보정 record 재검증이 실패했습니다: {record.get('id')}:{signals}"
                )
            if (
                record["id"] in all_ids
                or record["meta"]["message_sha256"] in all_messages
            ):
                raise Phase2AuditError("품질 보정 staging ID/message가 중복됐습니다.")
            all_ids.add(record["id"])
            all_messages.add(record["meta"]["message_sha256"])
    expected_counts = {
        axis: context["config"]["axes"][axis]["staging_rows"] for axis in AXES
    }
    if record_counts != expected_counts or len(all_ids) != 24_000:
        raise Phase2AuditError(f"품질 보정 staging 수량이 다릅니다: {record_counts}")
    order = _read_jsonl(private_root / "candidate_order.jsonl", "candidate order")
    if len(order) != 24_000 or {row.get("id") for row in order} != all_ids:
        raise Phase2AuditError("candidate_order와 record ID 집합이 다릅니다.")
    reservoir = _read_jsonl(
        private_root / "reservoirs/aihub_style_10k.jsonl", "AI Hub reservoir"
    )
    if (
        len(reservoir) != 10_000
        or len({row["meta"]["leakage_group_id"] for row in reservoir}) != 10_000
    ):
        raise Phase2AuditError("AI Hub local reservoir 10K 계약이 다릅니다.")
    risk = _read_jsonl(
        private_root / "risk/quality_risk_items.jsonl", "quality risk items"
    )
    if len(risk) != 34_000 or any(
        row.get("severity") in {"critical", "high"} for row in risk
    ):
        raise Phase2AuditError(
            "자동 위험 분류 수량 또는 critical/high Gate가 다릅니다."
        )
    for relative in (
        private_root / item for item in ["build_manifest.json", *_private_artifacts()]
    ):
        if stat.S_IMODE(relative.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase2AuditError(
                f"private 파일 권한이 0600이 아닙니다: {relative.name}"
            )
    for path in public_root.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE
        ):
            raise Phase2AuditError(f"public 산출물 형식·권한이 다릅니다: {path.name}")
        text = path.read_text(encoding="utf-8")
        if "aihub-talk:" in text or re.search(
            r'"(?:raw_hash|source_group_id)"\s*:', text
        ):
            raise Phase2AuditError(
                "public 보고서에 AI Hub 비공개 식별 정보가 있습니다."
            )
    acceptance = _load_json(
        public_root / "TECHNICAL_ACCEPTANCE.json", "technical acceptance"
    )
    if (
        acceptance.get("status") != "accepted_for_phase4_preflight"
        or acceptance.get("critical_or_high_rows") != 0
        or acceptance.get("human_domain_review_performed") is not False
        or acceptance.get("quality_certification_claimed") is not False
        or acceptance.get("training_promotion_allowed") is not False
    ):
        raise Phase2AuditError("자동 기술 승인 상태가 다릅니다.")
    return {
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "verified_accepted_for_phase4_preflight",
        "record_counts": record_counts,
        "total_rows": len(all_ids),
        "aihub_local_reservoir_rows": len(reservoir),
        "automated_risk_rows": len(risk),
        "critical_or_high_rows": 0,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }
