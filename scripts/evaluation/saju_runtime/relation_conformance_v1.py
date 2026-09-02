# relation_conformance_v1.py - 단일 날짜 관계표와 부모 snapshot Gate를 전수 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.facts import BRANCHES, STEMS, ten_god
from scripts.runtime.period_v1.contracts import (
    CHART_AUTHORIZATION_VERSION,
    PARENT_RELEASE_ID,
    RESOLVED_SCOPE_VERSION,
    sha256_value,
)
from scripts.runtime.period_v1.security import PeriodIdSigner
from scripts.runtime.relation_v1.contracts import (
    CHART_RELEASE_ID,
    CONFORMANCE_IMPLEMENTATIONS,
    PERIOD_RELEASE_ID,
    POLICY_ID,
    REGISTRY_PATH,
    REPORT_ROOT,
    SUITE_VERSION,
    TABLE_VERSION,
    TEN_GOD_TABLE_VERSION,
    validate_contract_registry,
)
from scripts.runtime.relation_v1.engine import (
    ApprovedSingleDateRelationEngine,
    branch_relations,
    calculate_relation_candidate,
    period_ten_gods,
    public_relation_result,
    validate_public_relation_result,
)
from scripts.runtime.relation_v1.errors import RelationRuntimeError
from scripts.runtime.relation_v1.security import RelationIdSigner

SCHEMA_VERSION = "1.0.0"
CANDIDATE_RELEASE_ID = "saju-natal-day-relation-candidate-v1.0.0"
TEST_PERIOD_KEY = bytes.fromhex(
    "04c658651f278ca104844b0dc60df449840c84c99a25cb1407a5e423c2b31f53"
)
TEST_RELATION_KEY = bytes.fromhex(
    "7cab8607230d9788ac57c7fbe7ba81ecd208b7fa08dc27a273fd5eca187c0294"
)

TEN_GOD_ORACLE = {
    "甲": ["비견", "겁재", "식신", "상관", "편재", "정재", "편관", "정관", "편인", "정인"],
    "乙": ["겁재", "비견", "상관", "식신", "정재", "편재", "정관", "편관", "정인", "편인"],
    "丙": ["편인", "정인", "비견", "겁재", "식신", "상관", "편재", "정재", "편관", "정관"],
    "丁": ["정인", "편인", "겁재", "비견", "상관", "식신", "정재", "편재", "정관", "편관"],
    "戊": ["편관", "정관", "편인", "정인", "비견", "겁재", "식신", "상관", "편재", "정재"],
    "己": ["정관", "편관", "정인", "편인", "겁재", "비견", "상관", "식신", "정재", "편재"],
    "庚": ["편재", "정재", "편관", "정관", "편인", "정인", "비견", "겁재", "식신", "상관"],
    "辛": ["정재", "편재", "정관", "편관", "정인", "편인", "겁재", "비견", "상관", "식신"],
    "壬": ["식신", "상관", "편재", "정재", "편관", "정관", "편인", "정인", "비견", "겁재"],
    "癸": ["상관", "식신", "정재", "편재", "정관", "편관", "정인", "편인", "겁재", "비견"],
}
MAIN_HIDDEN_ORACLE = {
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
PAIR_ORACLE = {
    "합": (("子", "丑"), ("寅", "亥"), ("卯", "戌"), ("辰", "酉"), ("巳", "申"), ("午", "未")),
    "충": (("子", "午"), ("丑", "未"), ("寅", "申"), ("卯", "酉"), ("辰", "戌"), ("巳", "亥")),
    "파": (("子", "酉"), ("丑", "辰"), ("寅", "亥"), ("卯", "午"), ("巳", "申"), ("未", "戌")),
    "해": (("子", "未"), ("丑", "午"), ("寅", "巳"), ("卯", "辰"), ("申", "亥"), ("酉", "戌")),
}
PUNISHMENT_GROUP_ORACLE = (("寅", "巳", "申"), ("丑", "戌", "未"), ("子", "卯"))
SELF_PUNISHMENT_ORACLE = ("辰", "午", "酉", "亥")


class RelationConformanceError(RuntimeError):
    """Relation conformance 공개 산출물·Gate 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _strict_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RelationConformanceError(f"산출물 파일이 없거나 symlink입니다: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RelationConformanceError(f"산출물 JSON을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise RelationConformanceError("산출물 최상위는 object여야 합니다.")
    return value


def _implementation_hashes() -> dict[str, str]:
    values: dict[str, str] = {}
    for relative in sorted(CONFORMANCE_IMPLEMENTATIONS):
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise RelationConformanceError(f"relation 구현 파일이 없습니다: {relative}")
        values[relative] = sha256_file(path)
    return values


def _oracle_ten_god(day_stem: str, target_stem: str) -> str:
    return TEN_GOD_ORACLE[day_stem][STEMS.index(target_stem)]


def _oracle_relations(left: str, right: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    pair = frozenset((left, right))
    if left != right:
        for relation, pairs in PAIR_ORACLE.items():
            if any(pair == frozenset(value) for value in pairs):
                found[relation] = "symmetric_pair"
        if any(left in group and right in group for group in PUNISHMENT_GROUP_ORACLE):
            found["형"] = "symmetric_group_pair"
    elif left in SELF_PUNISHMENT_ORACLE:
        found["형"] = "symmetric_self"
    return [
        (relation, found[relation])
        for relation in ("합", "충", "형", "파", "해")
        if relation in found
    ]


def _stem_matrix() -> dict[str, int]:
    mismatches = 0
    cases = 0
    for day_stem in STEMS:
        for target_stem in STEMS:
            cases += 1
            mismatches += int(
                ten_god(day_stem, target_stem)
                != _oracle_ten_god(day_stem, target_stem)
            )
    return {"cases": cases, "expected_cases": 100, "mismatches": mismatches}


def _branch_matrix() -> dict[str, int]:
    mismatches = 0
    cases = 0
    for day_stem in STEMS:
        labels = {"year": "甲子", "month": "甲子", "day": "甲子"}
        for branch in BRANCHES:
            cases += 1
            labels["day"] = "甲" + branch
            actual = period_ten_gods(day_stem, labels)["day"]
            expected_stem = MAIN_HIDDEN_ORACLE[branch]
            mismatches += int(
                actual["branch_main_hidden_stem"] != expected_stem
                or actual["branch_ten_god"]
                != _oracle_ten_god(day_stem, expected_stem)
            )
    return {"cases": cases, "expected_cases": 120, "mismatches": mismatches}


def _relation_matrix() -> dict[str, Any]:
    missing = 0
    unexpected = 0
    occurrence_counts = {name: 0 for name in ("합", "충", "형", "파", "해")}
    cases = 0
    for left in BRANCHES:
        for right in BRANCHES:
            cases += 1
            expected = set(_oracle_relations(left, right))
            actual = set(branch_relations(left, right))
            missing += len(expected - actual)
            unexpected += len(actual - expected)
            for relation, _direction in actual:
                occurrence_counts[relation] += 1
    return {
        "cases": cases,
        "expected_cases": 144,
        "missing_relations": missing,
        "unexpected_relations": unexpected,
        "occurrence_counts": occurrence_counts,
    }


def _symmetry_matrix() -> dict[str, Any]:
    pair_mismatches = 0
    punishment_mismatches = 0
    for left in BRANCHES:
        for right in BRANCHES:
            forward = set(branch_relations(left, right))
            reverse = set(branch_relations(right, left))
            pair_mismatches += int(forward != reverse)
    for group in PUNISHMENT_GROUP_ORACLE:
        for left in group:
            for right in group:
                if left != right:
                    punishment_mismatches += int(
                        ("형", "symmetric_group_pair")
                        not in branch_relations(left, right)
                    )
    for branch in BRANCHES:
        expected = branch in SELF_PUNISHMENT_ORACLE
        punishment_mismatches += int(
            (("형", "symmetric_self") in branch_relations(branch, branch))
            != expected
        )
    return {
        "pair_types": len(PAIR_ORACLE),
        "pair_symmetry_mismatches": pair_mismatches,
        "distinct_groups": len(PUNISHMENT_GROUP_ORACLE),
        "self_branches": len(SELF_PUNISHMENT_ORACLE),
        "punishment_direction_mismatches": punishment_mismatches,
    }


def _chart_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = {
        "calendar": "solar",
        "local_birth_date": "1990-01-01",
        "lunar_leap_month": None,
        "birth_time_precision": "exact",
        "local_birth_time": "12:00",
        "birth_time_range": None,
        "country_code": "KR",
        "city": "서울",
        "iana_time_zone": "Asia/Seoul",
        "policy_id": "KR_CIVIL_MIDNIGHT_V1",
    }
    natal = {
        "year": {"stem": "甲", "branch": "子", "ganzhi": "甲子"},
        "month": {"stem": "乙", "branch": "丑", "ganzhi": "乙丑"},
        "day": {"stem": "丙", "branch": "寅", "ganzhi": "丙寅"},
        "hour": {"stem": "丁", "branch": "卯", "ganzhi": "丁卯"},
    }
    hard_facts = {
        "pillars": natal,
        "day_master": {"stem": "丙", "element": "화", "yin_yang": "양"},
        "surface_five_elements": {"목": 4, "화": 2, "토": 1, "금": 0, "수": 1},
        "calculation_profile": {"profile_id": "KR_CIVIL_MIDNIGHT_V1"},
        "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
    }
    source_versions = {"runtime_release": CHART_RELEASE_ID, "fixture": "relation-v1"}
    chart_id = "sc2_" + "4" * 64
    chart = {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "message": "원국 fixture",
        "limitations": [],
        "normalized_input": normalized,
        "hard_facts": hard_facts,
        "chart_id": chart_id,
        "engine_version": "saju-runtime-python-v1.5.0",
        "policy_id": "KR_CIVIL_MIDNIGHT_V1",
        "source_versions": source_versions,
    }
    authorization = {
        "authorization_version": CHART_AUTHORIZATION_VERSION,
        "chart_id": chart_id,
        "state_revision": 1,
        "normalized_input_sha256": sha256_value(normalized),
        "public_hard_facts_sha256": sha256_value(hard_facts),
        "source_versions_sha256": sha256_value(source_versions),
        "release_id": PARENT_RELEASE_ID,
        "engine_version": "saju-runtime-python-v1.5.0",
        "policy_id": "KR_CIVIL_MIDNIGHT_V1",
        "fact_authority": "HARD_GT",
        "publicly_exposable": False,
    }
    return chart, authorization


def _period_fixture(
    authorization: Mapping[str, Any], signer: PeriodIdSigner, *, day_count: int = 1
) -> dict[str, Any]:
    if day_count not in {1, 2}:
        raise ValueError("fixture day_count")
    days = [
        {
            "date": "2026-09-02",
            "year_ganzhi": "丙午",
            "month_ganzhi": "丙申",
            "day_ganzhi": "己卯",
            "authority": "SOURCE_HARD_FACT",
        }
    ]
    if day_count == 2:
        days.append(
            {
                "date": "2026-09-03",
                "year_ganzhi": "丙午",
                "month_ganzhi": "丙申",
                "day_ganzhi": "庚辰",
                "authority": "SOURCE_HARD_FACT",
            }
        )
    scope = {
        "schema_version": RESOLVED_SCOPE_VERSION,
        "date_expression": "explicit",
        "start_date": "2026-09-02",
        "end_date": days[-1]["date"],
        "day_count": day_count,
        "timezone": "Asia/Seoul",
        "evaluation_local_time": "12:00",
        "reference_date": "2026-09-02",
        "intraday_segments_supported": False,
        "future_physical_instant_claimed": False,
    }
    public_scope = {
        key: scope[key]
        for key in (
            "date_expression",
            "start_date",
            "end_date",
            "day_count",
            "timezone",
            "evaluation_local_time",
        )
    }
    preimage = {
        "authority": dict(authorization),
        "period_scope": scope,
        "days": days,
        "authority_release_id": PERIOD_RELEASE_ID,
    }
    return {
        "schema_version": "saju-period-daily-label-internal-v1",
        "status": "ok",
        "fact_authority": "HARD_GT",
        "period_scope": public_scope,
        "days": days,
        "boundary_capability": {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        },
        "message": "공식 날짜 label fixture",
        "limitations": ["날짜 label만 제공합니다."],
        "period_id": signer.period_id(PERIOD_RELEASE_ID, preimage),
        "chart_authorization": dict(authorization),
        "authority_release_id": PERIOD_RELEASE_ID,
        "reference_date": "2026-09-02",
    }


def _runtime_matrix() -> dict[str, int]:
    period_signer = PeriodIdSigner.for_test(TEST_PERIOD_KEY)
    relation_signer = RelationIdSigner.for_test(TEST_RELATION_KEY)
    chart, authorization = _chart_fixture()
    single = _period_fixture(authorization, period_signer)
    internal = calculate_relation_candidate(
        chart_snapshot=chart,
        period_snapshot=single,
        period_signer=period_signer,
        relation_signer=relation_signer,
        authority_release_id=CANDIDATE_RELEASE_ID,
    )
    public = public_relation_result(internal)
    single_passed = int(
        public["selected_date"] == "2026-09-02"
        and public["fact_authority"] == "PROFILE_DETERMINISTIC"
        and public["interpretation_not_included"] is True
    )

    range_accepted = 0
    try:
        calculate_relation_candidate(
            chart_snapshot=chart,
            period_snapshot=_period_fixture(
                authorization, period_signer, day_count=2
            ),
            period_signer=period_signer,
            relation_signer=relation_signer,
            authority_release_id=CANDIDATE_RELEASE_ID,
        )
        range_accepted += 1
    except RelationRuntimeError:
        pass

    tampered_parent_accepted = 0
    tampered = deepcopy(single)
    tampered["days"][0]["day_ganzhi"] = "庚辰"
    try:
        calculate_relation_candidate(
            chart_snapshot=chart,
            period_snapshot=tampered,
            period_signer=period_signer,
            relation_signer=relation_signer,
            authority_release_id=CANDIDATE_RELEASE_ID,
        )
        tampered_parent_accepted += 1
    except RelationRuntimeError:
        pass
    tampered_chart = deepcopy(chart)
    tampered_chart["hard_facts"]["pillars"]["year"]["branch"] = "午"
    try:
        calculate_relation_candidate(
            chart_snapshot=tampered_chart,
            period_snapshot=single,
            period_signer=period_signer,
            relation_signer=relation_signer,
            authority_release_id=CANDIDATE_RELEASE_ID,
        )
        tampered_parent_accepted += 1
    except RelationRuntimeError:
        pass

    missing_release_accepted = 0
    disabled = ApprovedSingleDateRelationEngine(
        period_signer=period_signer,
        relation_signer=relation_signer,
    )
    try:
        disabled.calculate(chart_snapshot=chart, period_snapshot=single)
        missing_release_accepted += 1
    except RelationRuntimeError:
        pass

    interpretation_fields = 0
    injected = {**public, "interpretation": "금지된 해석"}
    try:
        validate_public_relation_result(injected)
        interpretation_fields += 1
    except RelationRuntimeError:
        pass
    serialized = json.dumps(public, ensure_ascii=False)
    interpretation_fields += sum(
        token in serialized
        for token in ('"interpretation":', '"score":', '"priority":')
    )
    return {
        "single_date_parent_cases": single_passed,
        "range_parent_accepted": range_accepted,
        "tampered_parent_accepted": tampered_parent_accepted,
        "missing_release_accepted": missing_release_accepted,
        "interpretation_fields": interpretation_fields,
        "private_runtime_ids_in_public": int(
            any(token in serialized for token in ("sc2_", "spd1_", "sr1_"))
        ),
    }


def build_aggregate() -> dict[str, Any]:
    validate_contract_registry()
    stem = _stem_matrix()
    branch = _branch_matrix()
    relations = _relation_matrix()
    symmetry = _symmetry_matrix()
    runtime = _runtime_matrix()
    gate_passed = (
        stem == {"cases": 100, "expected_cases": 100, "mismatches": 0}
        and branch == {"cases": 120, "expected_cases": 120, "mismatches": 0}
        and relations["cases"] == 144
        and relations["expected_cases"] == 144
        and relations["missing_relations"] == 0
        and relations["unexpected_relations"] == 0
        and symmetry
        == {
            "pair_types": 4,
            "pair_symmetry_mismatches": 0,
            "distinct_groups": 3,
            "self_branches": 4,
            "punishment_direction_mismatches": 0,
        }
        and runtime
        == {
            "single_date_parent_cases": 1,
            "range_parent_accepted": 0,
            "tampered_parent_accepted": 0,
            "missing_release_accepted": 0,
            "interpretation_fields": 0,
            "private_runtime_ids_in_public": 0,
        }
    )
    core = {
        "schema_version": SCHEMA_VERSION,
        "suite_version": SUITE_VERSION,
        "status": "passed" if gate_passed else "failed",
        "diagnostic_target_met": gate_passed,
        "contract_registry_sha256": sha256_file(REGISTRY_PATH),
        "policy": {
            "policy_id": POLICY_ID,
            "table_version": TABLE_VERSION,
            "ten_god_table_version": TEN_GOD_TABLE_VERSION,
            "authority": "PROFILE_DETERMINISTIC",
        },
        "parent_releases": {
            "chart": CHART_RELEASE_ID,
            "period": PERIOD_RELEASE_ID,
        },
        "matrices": {
            "stem_ten_gods": stem,
            "branch_ten_gods": branch,
            "branch_relations": relations,
            "symmetry_and_direction": symmetry,
        },
        "runtime": runtime,
        "governance": {
            "feature_flag_default": False,
            "single_date_only": True,
            "range_relation_arrays_supported": False,
            "interpretation_included": False,
            "dashboard_v1_13_activated": False,
            "strict_full_runtime_approved": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generation_allowed": False,
            "training_execution_performed": False,
            "model_promotion_performed": False,
        },
        "implementation_sha256": _implementation_hashes(),
        "raw_rows_in_report": False,
        "private_paths_in_report": False,
    }
    build_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    return {"build_id": build_id, **core}


def _output_directory(build_id: str) -> Path:
    return REPORT_ROOT / build_id


def write_report(aggregate: Mapping[str, Any]) -> dict[str, Any]:
    if aggregate.get("status") != "passed":
        raise RelationConformanceError("실패한 relation Gate는 기록할 수 없습니다.")
    directory = _output_directory(str(aggregate["build_id"]))
    aggregate_bytes = _json_bytes(aggregate)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "build_id": aggregate["build_id"],
        "suite_version": SUITE_VERSION,
        "files": {"aggregate.json": hashlib.sha256(aggregate_bytes).hexdigest()},
        "immutable": True,
        "public_summary_only": True,
    }
    manifest_bytes = _json_bytes(manifest)
    if directory.exists() or directory.is_symlink():
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or (directory / "aggregate.json").read_bytes() != aggregate_bytes
            or (directory / "build_manifest.json").read_bytes() != manifest_bytes
        ):
            raise RelationConformanceError("기존 relation build를 덮어쓸 수 없습니다.")
        return {"status": "reused", "build_id": aggregate["build_id"]}
    directory.mkdir(parents=True, exist_ok=False)
    try:
        for name, payload in (
            ("aggregate.json", aggregate_bytes),
            ("build_manifest.json", manifest_bytes),
        ):
            path = directory / name
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o644,
            )
            try:
                written = 0
                while written < len(payload):
                    count = os.write(descriptor, payload[written:])
                    if count <= 0:
                        raise OSError("report write returned zero bytes")
                    written += count
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except Exception:
        for name in ("aggregate.json", "build_manifest.json"):
            try:
                (directory / name).unlink()
            except OSError:
                pass
        try:
            directory.rmdir()
        except OSError:
            pass
        raise
    verify_report(directory)
    return {"status": "created", "build_id": aggregate["build_id"]}


def verify_report(report_root: Path) -> dict[str, Any]:
    validate_contract_registry()
    directory = report_root if report_root.is_absolute() else REPO_ROOT / report_root
    if directory.is_symlink() or not directory.is_dir():
        raise RelationConformanceError("relation report 경로가 없거나 symlink입니다.")
    resolved = directory.resolve()
    try:
        resolved.relative_to(REPORT_ROOT.resolve())
    except ValueError as exc:
        raise RelationConformanceError("relation report 경로가 고정 root를 벗어납니다.") from exc
    aggregate_path = resolved / "aggregate.json"
    manifest_path = resolved / "build_manifest.json"
    aggregate = _strict_json(aggregate_path)
    manifest = _strict_json(manifest_path)
    core = dict(aggregate)
    build_id = core.pop("build_id", None)
    expected_id = "build-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:12]
    expected = build_aggregate()
    if (
        build_id != expected_id
        or resolved.name != build_id
        or aggregate != expected
        or aggregate.get("status") != "passed"
        or aggregate.get("diagnostic_target_met") is not True
        or aggregate.get("raw_rows_in_report") is not False
        or aggregate.get("private_paths_in_report") is not False
        or manifest
        != {
            "schema_version": SCHEMA_VERSION,
            "build_id": build_id,
            "suite_version": SUITE_VERSION,
            "files": {"aggregate.json": sha256_file(aggregate_path)},
            "immutable": True,
            "public_summary_only": True,
        }
    ):
        raise RelationConformanceError("relation report 내용·hash·Gate가 다릅니다.")
    return {
        "status": "verified",
        "build_id": build_id,
        "stem_ten_god_cases": aggregate["matrices"]["stem_ten_gods"]["cases"],
        "branch_ten_god_cases": aggregate["matrices"]["branch_ten_gods"]["cases"],
        "branch_relation_cases": aggregate["matrices"]["branch_relations"]["cases"],
        "missing_relations": aggregate["matrices"]["branch_relations"]["missing_relations"],
        "unexpected_relations": aggregate["matrices"]["branch_relations"]["unexpected_relations"],
        "diagnostic_target_met": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="single-date relation conformance v1")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    execute = commands.add_parser("execute")
    execute.add_argument("--execute", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_contract_registry()
            result = {"status": "verified", "registry_id": registry["registry_id"]}
        elif args.command in {"plan", "execute"}:
            aggregate = build_aggregate()
            if args.command == "execute" and args.execute:
                result = write_report(aggregate)
            else:
                result = {
                    "status": "planned" if args.command == "plan" else "dry_run",
                    "build_id": aggregate["build_id"],
                    "gate_passed": aggregate["diagnostic_target_met"],
                    "output": _output_directory(aggregate["build_id"])
                    .relative_to(REPO_ROOT)
                    .as_posix(),
                    "writes_performed": False,
                }
        else:
            target = args.report_root
            if target is None:
                candidates = sorted(
                    path
                    for path in REPORT_ROOT.glob("build-*")
                    if path.is_dir() and not path.is_symlink()
                )
                if len(candidates) != 1:
                    raise RelationConformanceError(
                        "verify에는 유일한 relation build 또는 --report-root가 필요합니다."
                    )
                target = candidates[0]
            result = verify_report(target)
    except (OSError, ValueError, RelationConformanceError, RelationRuntimeError) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": getattr(exc, "code", "RELATION_CONFORMANCE_ERROR"),
                    "message": getattr(exc, "message", str(exc)),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
