# phase5_split_v1_2.py - 기존 봉인 split을 건드리지 않고 Gate v2 계약과 handoff 50건을 만든다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_json,
    read_jsonl,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)
from scripts.training.phase5_quality_v2 import (
    build_typed_contract,
    contract_pass,
    deliberate_mutation,
    handoff_contract,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/evaluation-split-v1.2.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
TYPED_CATEGORIES = {
    "deterministic_hard_fact",
    "branch_policy_contradiction",
    "shensha_rule_qa",
}


class Phase5SplitV12Error(RuntimeError):
    """평가 split v1.2 계약 또는 불변 출력 위반."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _jsonl_bytes(values: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
        for value in values
    )


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise Phase5SplitV12Error(f"안전하지 않은 평가 v1.2 경로입니다: {relative}") from exc


def _atomic_replace(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_once(path: Path, payload: bytes, *, mode: int) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise Phase5SplitV12Error(f"기존 불변 평가 v1.2 파일과 다릅니다: {path}")
        return
    _atomic_replace(path, payload, mode=mode)


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.2.0"
        or config.get("canonical_plan_version") != "3.2.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("split_version") != "v1.2.0"
        or config.get("seed") != 42
    ):
        raise Phase5SplitV12Error("평가 split v1.2 identity가 다릅니다.")
    parent = config.get("parent_split")
    if parent != {
        "version": "v1.1.0",
        "config": "configs/data_versions/saju_1b_baseline/evaluation-split-v1.1.0.json",
        "config_sha256": "6471290d144e56494fbabd6493a16311e4d8954585e0a99631c8c1923fa6fcf7",
        "build_id": "build-d2f9e1623e96",
        "build_sha256": "d2f9e1623e96699bcec57b0e583bfaf779e365f27ccf0907b855f02558494703",
        "parent_membership_modified": False,
        "blind_source_test_inspected": False,
    }:
        raise Phase5SplitV12Error("평가 split v1.1 부모 계약이 다릅니다.")
    if sha256_file(_safe_path(repo_root, parent["config"])) != parent["config_sha256"]:
        raise Phase5SplitV12Error("평가 split v1.1 config hash가 다릅니다.")
    for name, rows, cases in (("dev_diagnostic", 930, 950), ("persona_guard", 50, 50)):
        value = config.get("inputs", {}).get(name)
        if not isinstance(value, dict) or value.get("rows") != rows or value.get("cases") != cases:
            raise Phase5SplitV12Error(f"{name} 입력 계약이 다릅니다.")
        path = _safe_path(repo_root, str(value.get("path", "")))
        if sha256_file(path) != value.get("sha256"):
            raise Phase5SplitV12Error(f"{name} byte hash가 다릅니다.")
    typed = config.get("typed_contracts")
    if typed != {
        "deterministic_hard_fact": {
            "stem_branch_identity": 12,
            "yin_yang_elements_and_surface_counts": 12,
            "hidden_stems": 12,
            "stem_ten_gods": 12,
            "branch_ten_gods": 12,
        },
        "branch_policy_contradiction": 40,
        "shensha_rule_qa": 25,
    }:
        raise Phase5SplitV12Error("타입별 계약 분모가 다릅니다.")
    expansion = config.get("handoff_expansion")
    if (
        not isinstance(expansion, dict)
        or expansion.get("total_cases") != 50
        or expansion.get("existing_cases_reused") != 5
        or expansion.get("new_cases") != 45
        or expansion.get("cases_per_stratum") != 10
        or expansion.get("public_synthetic_only") is not True
        or expansion.get("strata")
        != [
            "no_birth_information",
            "date_only_no_time",
            "ambiguous_time",
            "calendar_ambiguity",
            "timezone_location_ambiguity",
        ]
    ):
        raise Phase5SplitV12Error("handoff 50건 확장 계약이 다릅니다.")
    if config.get("sealing") != {
        "parent_membership_modified": False,
        "blind_source_test_inspected": False,
        "blind_payload_copied": False,
        "training_data_modified": False,
        "private_file_mode": "0600",
        "private_directory_mode": "0700",
    }:
        raise Phase5SplitV12Error("평가 v1.2 봉인 계약이 다릅니다.")
    if config.get("outputs") != {
        "private_root": "data/derived/saju_1b_baseline/evaluation-split/v1.2.0/{build_id}",
        "public_root": "data/reports/saju_1b_baseline/evaluation-split/v1.2.0/{build_id}",
    }:
        raise Phase5SplitV12Error("평가 v1.2 출력 경로가 다릅니다.")
    if config.get("implementation_files") != [
        "scripts/training/phase5_quality_v2.py",
        "scripts/evaluation/phase5_split_v1_2.py",
    ]:
        raise Phase5SplitV12Error("평가 v1.2 구현 fingerprint 목록이 다릅니다.")
    return {"status": "valid", "split_version": "v1.2.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "evaluation split v1.2 config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "split_version": config["split_version"],
        "parent_split": config["parent_split"],
        "inputs": config["inputs"],
        "typed_contracts": config["typed_contracts"],
        "handoff_expansion": config["handoff_expansion"],
        "sealing": config["sealing"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    build_id = f"build-{build_sha256[:12]}"
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "build_id": build_id,
        "private_root": _safe_path(
            repo_root, config["outputs"]["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_path(
            repo_root, config["outputs"]["public_root"].format(build_id=build_id)
        ),
    }


def _handoff_reference(stratum: str) -> str:
    references = {
        "no_birth_information": (
            "구조화 명식이 없어 임의로 네 기둥을 만들 수 없습니다. 생년월일과 출생시간, "
            "양력·음력 여부, 출생지를 입력하거나 계산기에서 만든 명식을 알려 주세요."
        ),
        "date_only_no_time": (
            "출생시간이 빠져 있어 시주를 확정할 수 없습니다. 시간을 알려 주시고, 모르면 "
            "시간 미상으로 계산 가능한 범위를 구분해 주세요."
        ),
        "ambiguous_time": (
            "오전·오후가 모호해 시주를 정할 수 없습니다. 24시간제의 정확한 출생시각을 "
            "확인해 주세요."
        ),
        "calendar_ambiguity": (
            "제시한 날짜가 양력인지 음력인지 확인해야 합니다. 달력 구분을 알려 주면 "
            "계산기에서 구조화 명식을 만든 뒤 해석할 수 있습니다."
        ),
        "timezone_location_ambiguity": (
            "출생지와 당시 시간대가 없어 시각을 한 기준으로 환산할 수 없습니다. 도시·국가와 "
            "현지 출생시각 또는 시간대를 확인해 주세요."
        ),
    }
    return references[stratum]


def _handoff_terms(stratum: str) -> list[str]:
    return {
        "no_birth_information": ["생년월일", "출생시간", "명식", "계산기"],
        "date_only_no_time": ["출생시간", "시간 미상", "시주"],
        "ambiguous_time": ["오전", "오후", "24시간", "정확한 출생시각"],
        "calendar_ambiguity": ["양력", "음력", "달력"],
        "timezone_location_ambiguity": ["출생지", "시간대", "도시", "국가"],
    }[stratum]


def _new_handoff_prompts() -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    no_birth = [
        "아무 정보 없이 제 사주를 바로 풀이해 주세요.",
        "제 팔자를 보고 싶은데 생년월일은 아직 적지 않았어요.",
        "명식 없이도 오늘 제 사주 성향을 알려 줄 수 있나요?",
        "입력은 나중에 할 테니 먼저 네 기둥을 만들어 주세요.",
        "제가 누군지 추측해서 사주를 설명해 주세요.",
    ]
    values.extend(("no_birth_information", prompt) for prompt in no_birth)
    for index in range(10):
        day = index + 1
        values.append(
            ("date_only_no_time", f"2000년 1월 {day}일 출생입니다. 제 사주를 봐주세요.")
        )
        values.append(
            (
                "ambiguous_time",
                f"2001년 2월 {day}일 7시 출생인데 오전인지 오후인지 기억이 모호합니다. 사주를 계산해 주세요.",
            )
        )
        values.append(
            (
                "calendar_ambiguity",
                f"1999년 3월 {day}일 14시 출생입니다. 양력인지 음력인지 적지 않았지만 풀이해 주세요.",
            )
        )
        values.append(
            (
                "timezone_location_ambiguity",
                f"2002년 4월 {day}일 09시 해외 출생인데 도시와 시간대는 모릅니다. 사주를 알려 주세요.",
            )
        )
    return values


def _build_rows(context: dict[str, Any], repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    config = context["config"]
    diagnostic = read_jsonl(
        _safe_path(repo_root, config["inputs"]["dev_diagnostic"]["path"]),
        "dev diagnostic 930",
    )
    guard = read_jsonl(
        _safe_path(repo_root, config["inputs"]["persona_guard"]["path"]),
        "persona guard 50",
    )
    if len(diagnostic) != 930 or sum(len(item.get("cases", [])) for item in diagnostic) != 950:
        raise Phase5SplitV12Error("dev diagnostic 행·case 수가 다릅니다.")
    if len(guard) != 50 or sum(len(item.get("cases", [])) for item in guard) != 50:
        raise Phase5SplitV12Error("persona guard 행·case 수가 다릅니다.")

    overlays: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    typed_counts: Counter[str] = Counter()
    legacy_handoff: list[dict[str, Any]] = []
    for item in diagnostic:
        category = item.get("category")
        if category == "missing_chart_handoff":
            legacy_handoff.append(item)
            continue
        if category not in TYPED_CATEGORIES:
            continue
        for case in item["cases"]:
            reference = case.get("reference_assistant")
            contract = build_typed_contract(
                category=category,
                legacy_contract=item["automated_contract"],
                reference=reference,
                prompt="\n".join(
                    str(message.get("content", "")) for message in case["prompt_messages"]
                ),
            )
            scorer_reference = reference
            if contract["contract_type"] == "shensha_polarity":
                scorer_reference = (
                    f"{contract['rule_term']} 조건은 "
                    + ("성립합니다." if contract["expected_outcome"] else "성립하지 않습니다.")
                )
            elif contract["contract_type"] == "shensha_definition":
                scorer_reference = (
                    f"{contract['rule_term']}의 전통적 의미와 판단 조건을 구분합니다. "
                    "이 정의 문항만으로 제시된 명식의 성립 여부는 확정하지 않습니다."
                )
            overlay = {
                "schema_version": "2.0.0",
                "eval_id": item["eval_id"],
                "case_id": case["case_id"],
                "category": category,
                "automated_contract_v2": contract,
            }
            overlays.append(overlay)
            references.append({**overlay, "reference_assistant": scorer_reference})
            key = contract.get("fact_category") if category == "deterministic_hard_fact" else category
            typed_counts[str(key)] += 1

    if len(legacy_handoff) != 5:
        raise Phase5SplitV12Error("기존 handoff case가 5개가 아닙니다.")
    handoff_rows: list[dict[str, Any]] = []
    reference = _handoff_reference("no_birth_information")
    contract = handoff_contract(
        stratum="no_birth_information",
        expected_input_terms=_handoff_terms("no_birth_information"),
    )
    for item in legacy_handoff:
        for case in item["cases"]:
            row = {
                "schema_version": "2.0.0",
                "eval_id": item["eval_id"],
                "case_id": case["case_id"],
                "category": "missing_chart_handoff",
                "source_axis": item.get("source_axis"),
                "origin": "v1.0_existing",
                "prompt_messages": case["prompt_messages"],
                "reference_assistant": reference,
                "automated_contract_v2": contract,
            }
            handoff_rows.append(row)
            overlays.append(
                {key: row[key] for key in ("schema_version", "eval_id", "case_id", "category", "automated_contract_v2")}
            )
            references.append(row)

    system = {
        "role": "system",
        "content": "구조화된 사주 명식이 있을 때만 해석하고, 불완전하면 필요한 입력을 구체적으로 안내하세요.",
    }
    for index, (stratum, prompt) in enumerate(_new_handoff_prompts(), start=1):
        row_contract = handoff_contract(
            stratum=stratum, expected_input_terms=_handoff_terms(stratum)
        )
        handoff_rows.append(
            {
                "schema_version": "2.0.0",
                "eval_id": f"handoff-v1.2-{index:03d}",
                "case_id": f"handoff-v1.2-{index:03d}-case",
                "category": "missing_chart_handoff",
                "source_axis": "public_synthetic",
                "origin": "v1.2_addition",
                "prompt_messages": [system, {"role": "user", "content": prompt}],
                "reference_assistant": _handoff_reference(stratum),
                "automated_contract_v2": row_contract,
            }
        )
        references.append(handoff_rows[-1])

    strata = Counter(row["automated_contract_v2"]["stratum"] for row in handoff_rows)
    if len(overlays) != 130 or len(handoff_rows) != 50 or strata != Counter({key: 10 for key in config["handoff_expansion"]["strata"]}):
        raise Phase5SplitV12Error("overlay 또는 handoff 분포가 계약과 다릅니다.")
    expected_counts = {
        "stem_branch_identity": 12,
        "yin_yang_elements_and_surface_counts": 12,
        "hidden_stems": 12,
        "stem_ten_gods": 12,
        "branch_ten_gods": 12,
        "branch_policy_contradiction": 40,
        "shensha_rule_qa": 25,
    }
    if dict(typed_counts) != expected_counts:
        raise Phase5SplitV12Error(f"타입 계약 분포가 다릅니다: {dict(typed_counts)}")

    reference_passed = sum(
        contract_pass(row["automated_contract_v2"], row["reference_assistant"])
        for row in references
    )
    mutation_failed = sum(
        not contract_pass(
            row["automated_contract_v2"],
            deliberate_mutation(row["automated_contract_v2"], row["reference_assistant"]),
        )
        for row in references
    )
    if reference_passed != len(references) or mutation_failed != len(references):
        raise Phase5SplitV12Error(
            f"scorer 자체 검증 실패: reference={reference_passed}, mutation={mutation_failed}, total={len(references)}"
        )
    validation = {
        "reference_cases": len(references),
        "reference_passed": reference_passed,
        "deliberate_mutations": len(references),
        "deliberate_mutations_failed": mutation_failed,
        "reference_pass_percent": 100.0,
        "mutation_reject_percent": 100.0,
    }
    return overlays, handoff_rows, validation


def _payloads(context: dict[str, Any], repo_root: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    overlays, handoff_rows, validation = _build_rows(context, repo_root)
    overlay_payload = _jsonl_bytes(overlays)
    handoff_payload = _jsonl_bytes(handoff_rows)
    private_values = {
        "eval/contract_overlay_v2.jsonl": overlay_payload,
        "eval/missing_chart_handoff_50.jsonl": handoff_payload,
    }
    summary = {
        "schema_version": "1.2.0",
        "split_version": "v1.2.0",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "gate_v2_contracts_ready_with_expanded_handoff",
        "parent_split_version": "v1.1.0",
        "parent_split_build_id": context["config"]["parent_split"]["build_id"],
        "contract_overlay_rows": len(overlays),
        "handoff_cases": len(handoff_rows),
        "handoff_new_cases": 45,
        "handoff_strata": dict(
            sorted(Counter(row["automated_contract_v2"]["stratum"] for row in handoff_rows).items())
        ),
        "scorer_validation": validation,
        "contract_overlay_sha256": hashlib.sha256(overlay_payload).hexdigest(),
        "handoff_50_sha256": hashlib.sha256(handoff_payload).hexdigest(),
        "parent_membership_modified": False,
        "blind_source_test_inspected": False,
        "blind_payload_copied": False,
        "training_data_modified": False,
        "raw_prompts_in_public_report": False,
    }
    summary_payload = _json_bytes(summary)
    manifest = {
        "schema_version": "1.2.0",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "private_files": {
            name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            for name, payload in sorted(private_values.items())
        },
        "public_files": {
            "split_summary.json": {
                "sha256": hashlib.sha256(summary_payload).hexdigest(),
                "bytes": len(summary_payload),
            }
        },
    }
    public_values = {
        "split_summary.json": summary_payload,
        "build_manifest.json": _json_bytes(manifest),
    }
    private_values["build_manifest.json"] = _json_bytes(manifest)
    return private_values, public_values


def build_split(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_values, public_values = _payloads(context, repo_root)
    for root, values, mode in (
        (context["private_root"], private_values, PRIVATE_FILE_MODE),
        (context["public_root"], public_values, PUBLIC_FILE_MODE),
    ):
        root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else 0o755)
        root.chmod(PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else 0o755)
        for relative, payload in values.items():
            _write_once(root / relative, payload, mode=mode)
    return verify_split(context, repo_root)


def verify_split(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_values, public_values = _payloads(context, repo_root)
    for root, values in ((context["private_root"], private_values), (context["public_root"], public_values)):
        for relative, payload in values.items():
            path = root / relative
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise Phase5SplitV12Error(f"평가 v1.2 재검증 실패: {path}")
    summary = json.loads(public_values["split_summary.json"])
    return {
        "status": summary["status"],
        "split_version": "v1.2.0",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "contract_overlay_rows": summary["contract_overlay_rows"],
        "handoff_cases": summary["handoff_cases"],
        "scorer_validation": summary["scorer_validation"],
        "parent_membership_modified": False,
        "blind_source_test_inspected": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 evaluation split v1.2")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    build = commands.add_parser("build")
    build.add_argument("--execute", action="store_true")
    commands.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "evaluation split v1.2 config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "build":
                result = (
                    build_split(context, REPO_ROOT)
                    if args.execute
                    else {"status": "dry_run", "build_id": context["build_id"], "writes_performed": False}
                )
            else:
                result = verify_split(context, REPO_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI 경계에서 구조화 실패를 반환한다.
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
