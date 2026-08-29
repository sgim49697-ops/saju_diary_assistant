# external_conformance.py - 공개 사주 계산 fixture의 출처·정책·비교 결과를 검증한다.

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GANJI_PATTERN = re.compile(r"^[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]$")
KOREAN_GANJI_PATTERN = re.compile(
    r"^(갑|을|병|정|무|기|경|신|임|계)(자|축|인|묘|진|사|오|미|신|유|술|해)$"
)
POLICY_CASE_TYPES = {
    "four_pillars",
    "day_boundary",
    "solar_term_boundary",
    "true_solar_time",
    "stem_ten_gods",
    "branch_main_hidden_stem",
    "void_branches",
}
EXCLUDED_HEURISTIC_FIELDS = (
    "automatic_interpretation",
    "day_strength",
    "geukguk",
    "luck_start_age",
    "relation_priority",
    "remedy_advice",
    "shensha_interpretation",
    "yongsin",
)


class ExternalConformanceError(RuntimeError):
    """외부 conformance 계약 또는 fixture가 올바르지 않을 때 발생한다."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(repo_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ExternalConformanceError(f"외부 fixture 경로가 안전하지 않습니다: {relative}")
    resolved = (repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise ExternalConformanceError(
            f"외부 fixture 경로가 저장소를 벗어납니다: {relative}"
        ) from exc
    return resolved


def _load_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ExternalConformanceError(f"{label} 파일이 없습니다: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalConformanceError(f"{label} JSON을 읽지 못했습니다.") from exc


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ExternalConformanceError(f"{label} 파일이 없습니다: {path}")
    values: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise ExternalConformanceError(
                        f"{label}에 빈 행이 있습니다: {line_number}"
                    )
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ExternalConformanceError(
                        f"{label} 행은 object여야 합니다: {line_number}"
                    )
                values.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExternalConformanceError(f"{label} JSONL을 읽지 못했습니다.") from exc
    return values


def _validate_kasi_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or len(rows) != 200:
        raise ExternalConformanceError("KASI fixture는 정확히 200행이어야 합니다.")
    seen: set[tuple[int, int, int]] = set()
    validated: list[dict[str, Any]] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != {
            "solar",
            "lunar",
            "leap",
            "ko",
            "cn",
        }:
            raise ExternalConformanceError(f"KASI fixture schema가 다릅니다: {index}")
        solar = row["solar"]
        lunar = row["lunar"]
        ko = row["ko"]
        cn = row["cn"]
        if (
            not isinstance(solar, list)
            or len(solar) != 3
            or not all(isinstance(value, int) for value in solar)
            or not isinstance(lunar, list)
            or len(lunar) != 3
            or not all(isinstance(value, int) for value in lunar)
            or not isinstance(row["leap"], bool)
            or not isinstance(ko, list)
            or len(ko) != 3
            or not all(isinstance(value, str) for value in ko)
            or KOREAN_GANJI_PATTERN.fullmatch(ko[0]) is None
            or (ko[1] != "" and KOREAN_GANJI_PATTERN.fullmatch(ko[1]) is None)
            or KOREAN_GANJI_PATTERN.fullmatch(ko[2]) is None
            or not isinstance(cn, list)
            or len(cn) != 3
            or not all(isinstance(value, str) for value in cn)
            or GANJI_PATTERN.fullmatch(cn[0]) is None
            or (cn[1] != "" and GANJI_PATTERN.fullmatch(cn[1]) is None)
            or GANJI_PATTERN.fullmatch(cn[2]) is None
        ):
            raise ExternalConformanceError(f"KASI fixture 값이 다릅니다: {index}")
        try:
            solar_date = date(*solar)
        except ValueError as exc:
            raise ExternalConformanceError(
                f"KASI fixture 양력 날짜가 올바르지 않습니다: {index}"
            ) from exc
        if solar_date.year < 1583 or tuple(solar) in seen:
            raise ExternalConformanceError(
                f"KASI fixture 범위 또는 중복이 올바르지 않습니다: {index}"
            )
        if not (1 <= lunar[1] <= 12 and 1 <= lunar[2] <= 30):
            raise ExternalConformanceError(
                f"KASI fixture 음력 날짜가 올바르지 않습니다: {index}"
            )
        seen.add(tuple(solar))
        validated.append(row)
    return validated


def _validate_policy_cases(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != 20:
        raise ExternalConformanceError("정책 경계 fixture는 정확히 20행이어야 합니다.")
    ids: set[str] = set()
    type_counts: Counter[str] = Counter()
    profile_counts: Counter[str] = Counter()
    for index, row in enumerate(rows, 1):
        case_id = row.get("case_id")
        case_type = row.get("case_type")
        profile = row.get("policy_profile")
        sources = row.get("sources")
        if (
            row.get("schema_version") != "1.0.0"
            or not isinstance(case_id, str)
            or not case_id
            or case_id in ids
            or case_type not in POLICY_CASE_TYPES
            or row.get("fact_class") != "policy_bound"
            or row.get("oracle_tier") != "comparative"
            or row.get("gold_eligible") is not False
            or not isinstance(profile, str)
            or not profile
            or not isinstance(row.get("input"), dict)
            or not isinstance(row.get("expected"), dict)
            or not isinstance(sources, list)
            or not sources
        ):
            raise ExternalConformanceError(
                f"정책 경계 fixture 계약이 다릅니다: {index}"
            )
        for source in sources:
            if (
                not isinstance(source, dict)
                or not isinstance(source.get("repository"), str)
                or not source["repository"].startswith("https://github.com/")
                or not isinstance(source.get("revision"), str)
                or re.fullmatch(r"[0-9a-f]{40}", source["revision"]) is None
                or not isinstance(source.get("path"), str)
                or not source["path"]
            ):
                raise ExternalConformanceError(
                    f"정책 경계 fixture 출처가 다릅니다: {case_id}"
                )
        if set(row) & set(EXCLUDED_HEURISTIC_FIELDS):
            raise ExternalConformanceError(
                f"휴리스틱 필드가 정책 fixture에 들어갔습니다: {case_id}"
            )
        ids.add(case_id)
        type_counts[case_type] += 1
        profile_counts[profile] += 1
    expected_types = {
        "branch_main_hidden_stem": 1,
        "day_boundary": 3,
        "four_pillars": 11,
        "solar_term_boundary": 2,
        "stem_ten_gods": 1,
        "true_solar_time": 1,
        "void_branches": 1,
    }
    if dict(type_counts) != expected_types:
        raise ExternalConformanceError(
            f"정책 경계 fixture 유형 수량이 다릅니다: {dict(type_counts)}"
        )
    return {
        "rows": len(rows),
        "case_type_counts": dict(sorted(type_counts.items())),
        "policy_profile_counts": dict(sorted(profile_counts.items())),
    }


def _compare_lunar_python(
    rows: Sequence[dict[str, Any]],
    *,
    repo_root: Path | None = None,
    allow_data_environment: bool = True,
) -> dict[str, Any]:
    try:
        import importlib.metadata

        from lunar_python import Solar
    except Exception as exc:
        if allow_data_environment and repo_root is not None:
            interpreter = repo_root / ".venv-data/bin/python"
            if not interpreter.exists():
                raise ExternalConformanceError(
                    "고정 lunar-python 데이터 환경이 없습니다."
                ) from exc
            child = subprocess.run(
                [
                    str(interpreter),
                    "-c",
                    (
                        "import json,sys; "
                        "from pathlib import Path; "
                        "from scripts.evaluation.external_conformance import "
                        "_compare_lunar_python; "
                        "print(json.dumps(_compare_lunar_python(json.load(sys.stdin), "
                        "repo_root=Path.cwd(), allow_data_environment=False), "
                        "ensure_ascii=False, sort_keys=True))"
                    ),
                ],
                cwd=repo_root,
                input=json.dumps(rows, ensure_ascii=False),
                check=False,
                capture_output=True,
                text=True,
            )
            if child.returncode == 0:
                try:
                    value = json.loads(child.stdout)
                except json.JSONDecodeError as child_exc:
                    raise ExternalConformanceError(
                        "lunar-python 비교기 출력이 올바르지 않습니다."
                    ) from child_exc
                if isinstance(value, dict):
                    return value
            raise ExternalConformanceError(
                "고정 lunar-python 데이터 환경 비교가 실패했습니다."
            ) from exc
        raise ExternalConformanceError(
            "고정 lunar-python 비교기를 import하지 못했습니다."
        ) from exc

    row_conflicts: Counter[str] = Counter()
    field_conflicts: Counter[str] = Counter()
    for row in rows:
        lunar = Solar.fromYmd(*row["solar"]).getLunar()
        actual = {
            "lunar_date": [lunar.getYear(), abs(lunar.getMonth()), lunar.getDay()],
            "leap_month": lunar.getMonth() < 0,
            "calendar_year_ganzhi": lunar.getYearInGanZhi(),
            "calendar_month_ganzhi": lunar.getMonthInGanZhi(),
            "day_ganzhi": lunar.getDayInGanZhi(),
        }
        expected = {
            "lunar_date": row["lunar"],
            "leap_month": row["leap"],
            "calendar_year_ganzhi": row["cn"][0],
            "calendar_month_ganzhi": row["cn"][1],
            "day_ganzhi": row["cn"][2],
        }
        differences = [
            field for field in expected if actual[field] != expected[field]
        ]
        if differences:
            row_conflicts["conflict"] += 1
            field_conflicts.update(differences)
        else:
            row_conflicts["match"] += 1
    return {
        "adapter": "lunar-python",
        "version": importlib.metadata.version("lunar-python"),
        "oracle_role": "advisory_consistency_only",
        "independent_oracle": False,
        "rows": len(rows),
        "fully_matching_rows": row_conflicts["match"],
        "rows_with_any_conflict": row_conflicts["conflict"],
        "field_conflicts": {
            field: field_conflicts[field]
            for field in (
                "lunar_date",
                "leap_month",
                "calendar_year_ganzhi",
                "calendar_month_ganzhi",
                "day_ganzhi",
            )
        },
        "semantic_guard": {
            "calendar_year_ganzhi_is_not_saju_year_pillar": True,
            "calendar_month_ganzhi_is_not_saju_month_pillar": True,
            "day_ganzhi_is_midnight_boundary_day_pillar_candidate": True,
        },
    }


def validate_external_conformance(
    contract: dict[str, Any], repo_root: Path
) -> dict[str, Any]:
    """고정 공개 fixture와 source catalog를 검증하고 공개 가능한 집계를 반환한다."""

    if (
        contract.get("schema_version") != "1.0.0"
        or contract.get("suite_version") != "v1.0.0"
        or contract.get("status") != "fixture_contract_ready_runtime_adapter_pending"
        or contract.get("training_data_inclusion_allowed") is not False
        or contract.get("blind_source_test_inclusion_allowed") is not False
    ):
        raise ExternalConformanceError("외부 conformance 상위 계약이 다릅니다.")

    policy_contract = contract.get("calculation_policy")
    if (
        not isinstance(policy_contract, dict)
        or policy_contract.get("path") != "configs/saju_calculation_policy.json"
        or not isinstance(policy_contract.get("sha256"), str)
        or FULL_SHA_PATTERN.fullmatch(policy_contract["sha256"]) is None
    ):
        raise ExternalConformanceError("외부 conformance 계산 정책 계약이 다릅니다.")
    policy_path = _safe_path(repo_root, policy_contract["path"])
    if sha256_file(policy_path) != policy_contract["sha256"]:
        raise ExternalConformanceError("외부 conformance 계산 정책 hash가 다릅니다.")
    policy = _load_json(policy_path, "saju calculation policy")
    policy_scope = policy.get("scope", {})
    policy_external = policy.get("external_conformance", {})
    if (
        policy.get("policy_version") != "0.2.0"
        or policy.get("status") != "draft_not_runtime_approved"
        or policy_scope.get("runtime_enabled") is not False
        or policy_scope.get("training_data_mutation_allowed") is not False
        or policy_scope.get("evaluation_gold_generation_allowed") is not False
        or policy_scope.get("external_conformance_fixture_ready") is not True
        or policy_external.get("suite_version") != "v1.0.0"
        or policy_external.get("runtime_engine_approved") is not False
        or policy_external.get("training_data_inclusion_allowed") is not False
        or policy_external.get("blind_source_test_inclusion_allowed") is not False
    ):
        raise ExternalConformanceError("외부 conformance 계산 정책 flag가 다릅니다.")

    source_catalog = contract.get("source_catalog")
    if not isinstance(source_catalog, list) or len(source_catalog) != 6:
        raise ExternalConformanceError("외부 conformance source catalog가 다릅니다.")
    revisions: dict[str, str | None] = {}
    for source in source_catalog:
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("source_id"), str)
            or not isinstance(source.get("url"), str)
            or not source["url"].startswith("https://")
            or source.get("role")
            not in {"primary", "comparative", "consistency_only"}
        ):
            raise ExternalConformanceError("외부 conformance source 항목이 다릅니다.")
        revision = source.get("revision")
        if revision is not None and (
            not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        ):
            raise ExternalConformanceError("외부 source revision이 올바르지 않습니다.")
        revisions[source["source_id"]] = revision

    fixtures = contract.get("fixtures")
    if not isinstance(fixtures, dict) or set(fixtures) != {
        "kasi_lunar_200",
        "policy_cases_20",
    }:
        raise ExternalConformanceError("외부 conformance fixture 계약이 다릅니다.")
    loaded: dict[str, Any] = {}
    for key, fixture in fixtures.items():
        if (
            not isinstance(fixture, dict)
            or not isinstance(fixture.get("path"), str)
            or not isinstance(fixture.get("sha256"), str)
            or FULL_SHA_PATTERN.fullmatch(fixture["sha256"]) is None
        ):
            raise ExternalConformanceError(f"외부 fixture identity가 다릅니다: {key}")
        path = _safe_path(repo_root, fixture["path"])
        if sha256_file(path) != fixture["sha256"]:
            raise ExternalConformanceError(f"외부 fixture hash가 다릅니다: {key}")
        loaded[key] = (
            _read_jsonl(path, key) if path.suffix == ".jsonl" else _load_json(path, key)
        )

    kasi_rows = _validate_kasi_rows(loaded["kasi_lunar_200"])
    policy_summary = _validate_policy_cases(loaded["policy_cases_20"])
    comparison = _compare_lunar_python(kasi_rows, repo_root=repo_root)
    return {
        "schema_version": "1.0.0",
        "report_type": "saju_external_conformance_summary",
        "suite_version": "v1.0.0",
        "status": "fixture_contract_ready_runtime_adapter_pending",
        "retrieved_at": contract.get("retrieved_at"),
        "calculation_policy": {
            "policy_id": policy["policy_id"],
            "policy_version": policy["policy_version"],
            "sha256": policy_contract["sha256"],
            "runtime_enabled": False,
        },
        "source_revisions": dict(sorted(revisions.items())),
        "kasi_primary_snapshot": {
            "rows": len(kasi_rows),
            "fact_class": "hard_fact",
            "oracle_tier": "primary_snapshot",
            "gold_eligible_fields": [
                "solar_lunar_conversion",
                "leap_month",
                "calendar_year_ganzhi",
                "calendar_month_ganzhi",
                "day_ganzhi",
            ],
            "explicitly_not_equivalent_fields": {
                "calendar_year_ganzhi": "saju_year_pillar",
                "calendar_month_ganzhi": "saju_month_pillar",
            },
        },
        "policy_cases": policy_summary,
        "lunar_python_comparison": comparison,
        "heuristic_excluded_fields": list(EXCLUDED_HEURISTIC_FIELDS),
        "runtime_engine_approved": False,
        "training_data_modified": False,
        "evaluation_gold_automatically_promoted": False,
        "phase5_training_performed": False,
        "raw_restricted_samples_in_report": False,
    }
