# phase5_split.py - Phase 5 전 개발·봉인 blind·외부 conformance 평가 계약을 생성한다.

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
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluation.external_conformance import (
    ExternalConformanceError,
    validate_external_conformance,
)
from scripts.preflight.phase4_common import (
    prepare_context as prepare_phase4_context,
)
from scripts.preflight.phase4_common import (
    read_jsonl,
    sha256_file,
)
from scripts.preflight.phase4_data_v2 import (
    _case_from_record,
    _component,
    _eval_item,
    _parent,
    _tokenize_one,
    load_staging_records,
)
from scripts.training.phase5_readiness import (
    _select_eval70,
    verify_parent_phase4,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/evaluation-split-v1.0.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_FILE_MODE = 0o644
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
    "deterministic_saju_qa",
    "saju_diary_bridge",
)


class EvaluationSplitError(RuntimeError):
    """평가 split 계약·입력·산출물이 올바르지 않을 때 발생한다."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _payload_sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_repo_path(repo_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise EvaluationSplitError(f"저장소 상대경로가 올바르지 않습니다: {relative}")
    resolved = (repo_root / candidate).resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise EvaluationSplitError(
            f"평가 경로가 저장소를 벗어납니다: {relative}"
        ) from exc
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise EvaluationSplitError(f"{label} 파일이 없습니다: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationSplitError(f"{label} JSON을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise EvaluationSplitError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _atomic_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if path.exists():
            raise EvaluationSplitError(f"기존 불변 파일을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_payload(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


def _jsonl_payload(values: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(value) + b"\n" for value in values)


def _exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_PATTERN.fullmatch(value) is None:
        raise EvaluationSplitError(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("canonical_plan_version") != "3.1.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("split_version") != "v1.0.0"
        or config.get("seed") != 42
    ):
        raise EvaluationSplitError("평가 split 정본·버전·seed가 다릅니다.")

    parent = config.get("parent_phase4")
    if not isinstance(parent, dict):
        raise EvaluationSplitError("Phase 4 canonical 부모가 없습니다.")
    if (
        parent.get("version") != "v2.0.0"
        or parent.get("preflight_build_id") != "build-2feaee353252"
        or parent.get("canonical_build_id") != "build-6f32d52c2868"
        or parent.get("status") != "approved_for_phase5_training"
        or parent.get("selected_max_length") != 768
        or parent.get("training_promotion_allowed") is not True
        or parent.get("phase5_training_performed") is not False
    ):
        raise EvaluationSplitError("Phase 4 canonical 부모 계약이 다릅니다.")
    for key in (
        "preflight_build_sha256",
        "canonical_build_sha256",
        "private_manifest_sha256",
        "public_manifest_sha256",
        "completion_report_sha256",
    ):
        _exact_sha(parent.get(key), f"parent_phase4.{key}")
    _safe_repo_path(repo_root, str(parent.get("preflight_config", "")))

    staging = config.get("parent_staging")
    if (
        not isinstance(staging, dict)
        or staging.get("version") != "v1.0.0"
        or staging.get("build_id") != "build-a5a9e76d6a8c"
    ):
        raise EvaluationSplitError("품질 보정 staging 부모가 다릅니다.")
    for key in ("build_sha256", "private_manifest_sha256"):
        _exact_sha(staging.get(key), f"parent_staging.{key}")

    inputs = config.get("canonical_inputs")
    expected_inputs = {
        "mix20": ("manifests/mix20k_v2.jsonl", 20_000),
        "core_eval": ("eval/core_eval_300.jsonl", 300),
        "source_holdout": ("eval/source_holdout_700.jsonl", 700),
    }
    if not isinstance(inputs, dict) or set(inputs) != set(expected_inputs):
        raise EvaluationSplitError("평가 split canonical 입력 계약이 다릅니다.")
    for key, (relative, rows) in expected_inputs.items():
        value = inputs[key]
        if (
            not isinstance(value, dict)
            or value.get("relative_path") != relative
            or value.get("rows") != rows
        ):
            raise EvaluationSplitError(f"canonical 입력 계약이 다릅니다: {key}")
        _exact_sha(value.get("sha256"), f"canonical_inputs.{key}.sha256")

    tokenization = config.get("tokenization")
    if tokenization != {
        "formal_max_length": 768,
        "model_revision": "bf4786aa2a1908adce942d53976270132732f720",
        "model_local_subdir": "models/saju_1b_baseline/kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720",
        "chat_template_sha256": "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3",
        "snapshot_manifest_sha256": "5786d04831c93192d234651df0894a1912b974cfab96011ce0676563185cc93d",
    }:
        raise EvaluationSplitError("blind tokenization 계약이 다릅니다.")
    model_root = _safe_repo_path(repo_root, tokenization["model_local_subdir"])
    if model_root.is_symlink() or not model_root.is_dir():
        raise EvaluationSplitError("고정 tokenizer snapshot이 없습니다.")

    roles = config.get("roles")
    if not isinstance(roles, dict) or set(roles) != {
        "dev_monitor",
        "dev_diagnostic",
        "blind_source_test",
        "external_conformance",
    }:
        raise EvaluationSplitError("평가 역할 계약이 다릅니다.")
    if roles["dev_monitor"] != {
        "rows": 70,
        "rows_per_axis": 10,
        "selection_seed": 42,
        "expected_sha256": "aa61d2a763e3194e3a25561a3030c74bebb002c702ef8469c27a1bc22a2bcb31",
        "training_use": "loss_monitor_only",
        "checkpoint_selection_allowed": False,
        "final_claim_allowed": False,
    }:
        raise EvaluationSplitError("dev_monitor 계약이 다릅니다.")
    if roles["dev_diagnostic"] != {
        "source_holdout_rows": 630,
        "core_eval_rows": 300,
        "total_rows": 930,
        "training_use": "pipeline_and_error_analysis",
        "final_claim_allowed": False,
    }:
        raise EvaluationSplitError("dev_diagnostic 계약이 다릅니다.")
    blind = roles["blind_source_test"]
    if (
        not isinstance(blind, dict)
        or blind.get("components_per_axis") != 50
        or blind.get("total_components") != 350
        or blind.get("total_rows") != 500
        or blind.get("selector") != "sha256_component_stratified_v1"
        or blind.get("selector_namespace") != "saju-blind-source-v1"
        or blind.get("sealed") is not True
        or blind.get("final_evaluation_runs") != 1
        or blind.get("aggregation") != "component_then_axis_macro"
        or blind.get("expected_rows_by_axis")
        != {
            "nemotron_saju": 50,
            "bazi_sft": 200,
            "aihub_empathy_single": 50,
            "aihub_empathy_multiturn": 50,
            "yeji_shensha_derived": 50,
            "deterministic_saju_qa": 50,
            "saju_diary_bridge": 50,
        }
    ):
        raise EvaluationSplitError("blind_source_test 계약이 다릅니다.")
    strata = blind.get("strata")
    if not isinstance(strata, dict) or set(strata) != set(AXES):
        raise EvaluationSplitError("blind 축별 층화 계약이 다릅니다.")
    if roles["external_conformance"] != {
        "suite_version": "v1.0.0",
        "training_data_inclusion_allowed": False,
        "blind_source_test_inclusion_allowed": False,
        "score_separately": True,
    }:
        raise EvaluationSplitError("external_conformance 역할 계약이 다릅니다.")

    try:
        validate_external_conformance(config["external_conformance"], repo_root)
    except (KeyError, ExternalConformanceError) as exc:
        raise EvaluationSplitError("외부 conformance 계약이 다릅니다.") from exc

    sealing = config.get("sealing")
    if sealing != {
        "private_file_mode": "0600",
        "private_directory_mode": "0700",
        "raw_or_ids_in_public_report": False,
        "requires_frozen_checkpoints": ["K0-INSTRUCT", "KI10-MIX-v2", "KI20-MIX-v2"],
        "blind_spent_after_output_inspection": True,
        "post_inspection_change_requires_new_version": True,
    }:
        raise EvaluationSplitError("blind 봉인 계약이 다릅니다.")

    outputs = config.get("outputs")
    if outputs != {
        "private_root": "data/derived/saju_1b_baseline/evaluation-split/v1.0.0/{build_id}",
        "public_root": "data/reports/saju_1b_baseline/evaluation-split/v1.0.0/{build_id}",
    }:
        raise EvaluationSplitError("평가 split 출력 경로가 다릅니다.")
    for value in outputs.values():
        _safe_repo_path(repo_root, value.format(build_id="build-000000000000"))
    ignore_lines = {
        line.strip()
        for line in (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    if "/data/derived/" not in ignore_lines:
        raise EvaluationSplitError("private 평가 데이터 Git 제외 규칙이 없습니다.")

    governance = config.get("governance")
    if governance != {
        "canonical_training_data_modified": False,
        "phase4_smoke_reexecution_required": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }:
        raise EvaluationSplitError("평가 split 거버넌스 계약이 다릅니다.")
    files = config.get("implementation_files")
    if not isinstance(files, list) or files != [
        "scripts/evaluation/__init__.py",
        "scripts/evaluation/external_conformance.py",
        "scripts/evaluation/phase5_split.py",
        "scripts/preflight/phase4_data_v2.py",
        "scripts/training/phase5_readiness.py",
    ]:
        raise EvaluationSplitError("평가 split 구현 fingerprint 목록이 다릅니다.")
    return {
        "status": "valid",
        "split_version": "v1.0.0",
        "blind_components": 350,
        "blind_rows": 500,
        "phase5_training_performed": False,
    }


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = _load_json(config_path, "evaluation split config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes: dict[str, str] = {}
    for relative in [*config["implementation_files"], relative_config]:
        path = _safe_repo_path(repo_root, relative)
        if not path.is_file():
            raise EvaluationSplitError(f"구현 fingerprint 파일이 없습니다: {relative}")
        implementation_hashes[relative] = sha256_file(path)
    build_inputs = {
        "canonical_plan_version": config["canonical_plan_version"],
        "split_version": config["split_version"],
        "seed": config["seed"],
        "parent_phase4": config["parent_phase4"],
        "parent_staging": config["parent_staging"],
        "canonical_inputs": config["canonical_inputs"],
        "tokenization_sha256": sha256_json(config["tokenization"]),
        "roles_sha256": sha256_json(config["roles"]),
        "external_conformance_sha256": sha256_json(config["external_conformance"]),
        "sealing_sha256": sha256_json(config["sealing"]),
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
        "private_root": _safe_repo_path(
            repo_root, config["outputs"]["private_root"].format(build_id=build_id)
        ),
        "public_root": _safe_repo_path(
            repo_root, config["outputs"]["public_root"].format(build_id=build_id)
        ),
    }


def _canonical_root(context: dict[str, Any], repo_root: Path) -> Path:
    parent = context["config"]["parent_phase4"]
    return _safe_repo_path(
        repo_root,
        f"data/derived/saju_1b_baseline/{parent['version']}/{parent['canonical_build_id']}",
    )


def _rank(seed: int, namespace: str, axis: str, component_id: str) -> str:
    return hashlib.sha256(
        f"{seed}|{namespace}|{axis}|{component_id}".encode()
    ).hexdigest()


def _select_quota(
    ordered: list[dict[str, Any]], field: str, quotas: dict[str, int]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for value, count in quotas.items():
        matches = [item for item in ordered if item.get(field) == value]
        if len(matches) < count:
            raise EvaluationSplitError(
                f"blind 층화 후보가 부족합니다: {field}={value} {len(matches)}<{count}"
            )
        selected.extend(matches[:count])
    return selected


def _select_distinct(
    ordered: list[dict[str, Any]], field: str, count: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unique_only in (True, False):
        for item in ordered:
            if item in selected:
                continue
            value = str(item.get(field))
            if unique_only and value in seen:
                continue
            selected.append(item)
            seen.add(value)
            if len(selected) == count:
                return selected
    raise EvaluationSplitError(f"blind 다양성 후보가 부족합니다: {field}")


def select_blind_components(
    candidates: dict[str, list[dict[str, Any]]],
    strata: dict[str, dict[str, Any]],
    *,
    seed: int,
    namespace: str,
    count_per_axis: int,
) -> dict[str, list[dict[str, Any]]]:
    """component 단위로 층화된 blind 후보를 결정론적으로 선택한다."""

    if set(candidates) != set(AXES) or set(strata) != set(AXES):
        raise EvaluationSplitError("blind 후보 축 집합이 다릅니다.")
    result: dict[str, list[dict[str, Any]]] = {}
    for axis in AXES:
        values = candidates[axis]
        if len({item.get("component_id") for item in values}) != len(values):
            raise EvaluationSplitError(f"blind component가 중복됐습니다: {axis}")
        ordered = sorted(
            values,
            key=lambda item: _rank(
                seed, namespace, axis, str(item.get("component_id"))
            ),
        )
        contract = strata[axis]
        if "quotas" in contract:
            selected = _select_quota(
                ordered, str(contract.get("field")), contract["quotas"]
            )
        elif "distinct_min" in contract:
            selected = _select_distinct(
                ordered, str(contract.get("field")), int(contract["distinct_min"])
            )
        else:
            selected = ordered[:count_per_axis]
        if len(selected) != count_per_axis:
            raise EvaluationSplitError(
                f"blind component 수량이 다릅니다: {axis}={len(selected)}"
            )
        if axis == "bazi_sft":
            required = set(contract.get("required_member_values", []))
            if any(
                item.get("row_count") != contract.get("component_rows")
                or set(item.get("question_types", [])) != required
                for item in selected
            ):
                raise EvaluationSplitError("BaZi blind component 4질문 계약이 다릅니다.")
        result[axis] = selected
    return result


def _content_hash_from_messages(messages: Sequence[dict[str, str]]) -> str:
    prompt = list(messages[:-1])
    reference = messages[-1]["content"]
    return sha256_json(
        {"prompt_messages": prompt, "reference_assistant": reference}
    )


def _case_content_hashes(items: Sequence[dict[str, Any]]) -> set[str]:
    return {
        sha256_json(
            {
                "prompt_messages": case["prompt_messages"],
                "reference_assistant": case.get("reference_assistant"),
            }
        )
        for item in items
        for case in item["cases"]
    }


def _parent_identity(items: Sequence[dict[str, Any]]) -> tuple[set[str], set[str]]:
    return (
        {parent["id"] for item in items for parent in item["parents"]},
        {
            parent["record_sha256"]
            for item in items
            for parent in item["parents"]
        },
    )


def _components(items: Sequence[dict[str, Any]]) -> set[str]:
    return {
        parent["leakage_component_id"]
        for item in items
        for parent in item["parents"]
    }


def _load_and_select(
    context: dict[str, Any], repo_root: Path
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    config = context["config"]
    canonical_root = _canonical_root(context, repo_root)
    loaded: dict[str, list[dict[str, Any]]] = {}
    for key, contract in config["canonical_inputs"].items():
        path = canonical_root / contract["relative_path"]
        if sha256_file(path) != contract["sha256"]:
            raise EvaluationSplitError(f"canonical 평가 입력 hash가 다릅니다: {key}")
        rows = read_jsonl(path, key)
        if len(rows) != contract["rows"]:
            raise EvaluationSplitError(f"canonical 평가 입력 수량이 다릅니다: {key}")
        loaded[key] = rows

    mix20 = loaded["mix20"]
    core = loaded["core_eval"]
    holdout = loaded["source_holdout"]
    dev_monitor, monitor_axes = _select_eval70(
        holdout, seed=config["roles"]["dev_monitor"]["selection_seed"]
    )
    monitor_payload = _jsonl_payload(dev_monitor)
    if _payload_sha(monitor_payload) != config["roles"]["dev_monitor"]["expected_sha256"]:
        raise EvaluationSplitError("기존 eval70 byte hash가 재현되지 않습니다.")
    monitor_ids = {item["eval_id"] for item in dev_monitor}
    remaining_holdout = [item for item in holdout if item["eval_id"] not in monitor_ids]
    dev_diagnostic = [*core, *remaining_holdout]
    if len(remaining_holdout) != 630 or len(dev_diagnostic) != 930:
        raise EvaluationSplitError("개발 진단 평가 수량이 다릅니다.")

    phase4_context = prepare_phase4_context(
        repo_root, _safe_repo_path(repo_root, config["parent_phase4"]["preflight_config"])
    )
    records_by_id, ordered_ids, _, _ = load_staging_records(phase4_context, repo_root)
    train_components = {row["leakage_component_id"] for row in mix20}
    development_components = _components([*core, *holdout])
    blocked = train_components | development_components
    groups: dict[str, list[str]] = defaultdict(list)
    group_axes: dict[str, set[str]] = defaultdict(set)
    for record_id in ordered_ids:
        record = records_by_id[record_id]
        component = _component(record)
        groups[component].append(record_id)
        group_axes[component].add(record["mix_axis"])

    try:
        from transformers import AutoTokenizer
    except Exception as exc:
        raise EvaluationSplitError("고정 tokenizer를 import하지 못했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        _safe_repo_path(repo_root, config["tokenization"]["model_local_subdir"]),
        local_files_only=True,
        trust_remote_code=True,
    )
    if (
        not isinstance(tokenizer.chat_template, str)
        or hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
        != config["tokenization"]["chat_template_sha256"]
    ):
        raise EvaluationSplitError("blind tokenizer chat template hash가 다릅니다.")

    token_meta: dict[str, dict[str, int]] = {}
    candidates: dict[str, list[dict[str, Any]]] = {axis: [] for axis in AXES}
    for component, record_ids in groups.items():
        axes = group_axes[component]
        if component in blocked or len(axes) != 1:
            continue
        axis = next(iter(axes))
        lengths: list[int] = []
        for record_id in record_ids:
            value = _tokenize_one(tokenizer, records_by_id[record_id])
            token_meta[record_id] = {
                "total_tokens": value["total_tokens"],
                "assistant_tokens": value["assistant_tokens"],
            }
            lengths.append(value["total_tokens"])
        if max(lengths) > config["tokenization"]["formal_max_length"]:
            continue
        first = records_by_id[record_ids[0]]
        candidates[axis].append(
            {
                "component_id": component,
                "record_ids": list(record_ids),
                "row_count": len(record_ids),
                "source_variant": first.get("source_variant"),
                "qa_category": first["meta"].get("qa_category"),
                "task_presentation": first["meta"].get("task_presentation"),
                "emotion_type": first["meta"].get("emotion_type"),
                "question_types": sorted(
                    {
                        str(records_by_id[record_id]["meta"].get("question_type"))
                        for record_id in record_ids
                    }
                ),
            }
        )

    blind_contract = config["roles"]["blind_source_test"]
    selected = select_blind_components(
        candidates,
        blind_contract["strata"],
        seed=config["seed"],
        namespace=blind_contract["selector_namespace"],
        count_per_axis=blind_contract["components_per_axis"],
    )
    blind_items: list[dict[str, Any]] = []
    blind_component_rows: list[dict[str, Any]] = []
    for axis in AXES:
        for component in selected[axis]:
            record_ids = component["record_ids"]
            blind_component_rows.append(
                {
                    "schema_version": "1.0.0",
                    "split_role": "blind_source_test",
                    "source_axis": axis,
                    "component_id": component["component_id"],
                    "record_ids": record_ids,
                    "record_sha256": [
                        records_by_id[record_id]["meta"]["phase4_parent_record_sha256"]
                        for record_id in record_ids
                    ],
                    "content_sha256": [
                        _content_hash_from_messages(records_by_id[record_id]["messages"])
                        for record_id in record_ids
                    ],
                    "total_tokens": [
                        token_meta[record_id]["total_tokens"] for record_id in record_ids
                    ],
                    "assistant_tokens": [
                        token_meta[record_id]["assistant_tokens"]
                        for record_id in record_ids
                    ],
                    "selector_rank": _rank(
                        config["seed"],
                        blind_contract["selector_namespace"],
                        axis,
                        component["component_id"],
                    ),
                }
            )
            for record_id in record_ids:
                record = records_by_id[record_id]
                item = _eval_item(
                    "blind_source_test",
                    "sealed_reference_anchor",
                    [_case_from_record(record)],
                    [_parent(records_by_id[value]) for value in record_ids],
                    {
                        "score": "reference_overlap_and_nonempty",
                        "aggregation_unit": "leakage_component_id",
                    },
                    source_axis=axis,
                )
                item["split_role"] = "blind_source_test"
                item["sealed"] = True
                blind_items.append(item)
    blind_items.sort(
        key=lambda item: hashlib.sha256(
            f"{config['seed']}|blind-output-order|{item['eval_id']}".encode()
        ).hexdigest()
    )
    blind_component_rows.sort(key=lambda item: item["selector_rank"])
    axis_rows = Counter(item["source_axis"] for item in blind_items)
    if (
        len(blind_component_rows) != blind_contract["total_components"]
        or len(blind_items) != blind_contract["total_rows"]
        or dict(axis_rows) != blind_contract["expected_rows_by_axis"]
    ):
        raise EvaluationSplitError(
            f"blind 최종 수량이 다릅니다: components={len(blind_component_rows)}, rows={dict(axis_rows)}"
        )

    train_ids = {row["id"] for row in mix20}
    train_record_hashes = {row["record_sha256"] for row in mix20}
    train_content_hashes = {
        _content_hash_from_messages(records_by_id[row["id"]]["messages"])
        for row in mix20
    }
    dev_ids, dev_record_hashes = _parent_identity([*core, *holdout])
    blind_ids, blind_record_hashes = _parent_identity(blind_items)
    dev_content_hashes = _case_content_hashes([*core, *holdout])
    blind_content_hashes = _case_content_hashes(blind_items)
    overlaps = {
        "train_development_component": len(train_components & development_components),
        "train_blind_component": len(train_components & _components(blind_items)),
        "development_blind_component": len(
            development_components & _components(blind_items)
        ),
        "train_development_record_id": len(train_ids & dev_ids),
        "train_blind_record_id": len(train_ids & blind_ids),
        "development_blind_record_id": len(dev_ids & blind_ids),
        "train_development_record_sha256": len(
            train_record_hashes & dev_record_hashes
        ),
        "train_blind_record_sha256": len(train_record_hashes & blind_record_hashes),
        "development_blind_record_sha256": len(
            dev_record_hashes & blind_record_hashes
        ),
        "train_development_content_sha256": len(
            train_content_hashes & dev_content_hashes
        ),
        "train_blind_content_sha256": len(train_content_hashes & blind_content_hashes),
        "development_blind_content_sha256": len(
            dev_content_hashes & blind_content_hashes
        ),
    }
    if any(overlaps.values()):
        raise EvaluationSplitError(f"평가 split 누수가 있습니다: {overlaps}")

    monitor_components = _components(dev_monitor)
    diagnostic_components = _components(dev_diagnostic)
    dev_internal_overlap = len(monitor_components & diagnostic_components)
    if dev_internal_overlap != 8:
        raise EvaluationSplitError(
            f"기존 dev 내부 BaZi component 겹침 진단값이 다릅니다: {dev_internal_overlap}"
        )
    remaining = {
        axis: len(candidates[axis]) - len(selected[axis]) for axis in AXES
    }
    selection_summary = {
        "candidate_components_by_axis": {
            axis: len(candidates[axis]) for axis in AXES
        },
        "selected_components_by_axis": {axis: 50 for axis in AXES},
        "selected_rows_by_axis": dict(axis_rows),
        "remaining_components_by_axis": remaining,
        "dev_monitor_axis_counts": monitor_axes,
        "development_internal_component_overlap": dev_internal_overlap,
        "development_internal_overlap_policy": "allowed_same_development_suite_not_final_test",
        "cross_axis_reserve_components_selected": 0,
    }
    leakage_report = {
        "schema_version": "1.0.0",
        "status": "passed",
        "overlaps": overlaps,
        "development_internal_component_overlap": dev_internal_overlap,
        "blind_component_grouping_preserved": True,
        "bazi_rows_per_component": 4,
        "normalized_content_hash_checked": True,
        "raw_samples_in_report": False,
    }
    return dev_monitor, dev_diagnostic, blind_items, blind_component_rows, {
        "selection": selection_summary,
        "leakage": leakage_report,
    }


def _build_payloads(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, bytes], dict[str, bytes]]:
    verify_parent_phase4(context, repo_root)
    dev_monitor, dev_diagnostic, blind, blind_components, reports = _load_and_select(
        context, repo_root
    )
    external = validate_external_conformance(
        context["config"]["external_conformance"], repo_root
    )
    payloads = {
        "eval/dev_monitor_70.jsonl": _jsonl_payload(dev_monitor),
        "eval/dev_diagnostic_930.jsonl": _jsonl_payload(dev_diagnostic),
        "eval/blind_source_test_500.jsonl": _jsonl_payload(blind),
        "manifests/blind_components_350.jsonl": _jsonl_payload(blind_components),
        "reports/leakage_report.json": _json_payload(reports["leakage"]),
    }
    role_manifest = {
        "schema_version": "1.0.0",
        "split_version": context["config"]["split_version"],
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "roles": {
            "train_10k": {
                "split_role": "train",
                "parent_relative_path": "manifests/mix10k_v2.jsonl",
                "rows": 10_000,
                "canonical_training_fingerprint_changed": False,
            },
            "train_20k": {
                "split_role": "train",
                "parent_relative_path": "manifests/mix20k_v2.jsonl",
                "rows": 20_000,
                "canonical_training_fingerprint_changed": False,
            },
            "dev_monitor": {
                "split_role": "dev_monitor",
                "relative_path": "eval/dev_monitor_70.jsonl",
                "rows": 70,
                "sha256": _payload_sha(payloads["eval/dev_monitor_70.jsonl"]),
                "sealed": False,
                "checkpoint_selection_allowed": False,
                "final_claim_allowed": False,
            },
            "dev_diagnostic": {
                "split_role": "dev_diagnostic",
                "relative_path": "eval/dev_diagnostic_930.jsonl",
                "rows": 930,
                "sha256": _payload_sha(payloads["eval/dev_diagnostic_930.jsonl"]),
                "sealed": False,
                "final_claim_allowed": False,
            },
            "blind_source_test": {
                "split_role": "blind_test",
                "relative_path": "eval/blind_source_test_500.jsonl",
                "rows": 500,
                "components": 350,
                "sha256": _payload_sha(payloads["eval/blind_source_test_500.jsonl"]),
                "sealed": True,
                "aggregation": "component_then_axis_macro",
                "final_evaluation_runs": 1,
            },
            "external_conformance": {
                "split_role": "external_conformance",
                "suite_version": "v1.0.0",
                "score_separately": True,
                "mixed_into_train_or_blind": False,
            },
        },
        "phase5_training_performed": False,
    }
    payloads["manifests/evaluation_roles.json"] = _json_payload(role_manifest)
    private_values = dict(sorted(payloads.items()))
    public_summary = {
        "schema_version": "1.0.0",
        "report_type": "phase5_evaluation_split_summary",
        "split_version": context["config"]["split_version"],
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "sealed_blind_ready_for_post_training_evaluation",
        "parent_phase4": context["config"]["parent_phase4"],
        "roles": {
            "train_10k_rows": 10_000,
            "train_20k_rows": 20_000,
            "dev_monitor_rows": 70,
            "dev_diagnostic_rows": 930,
            "blind_source_test_components": 350,
            "blind_source_test_rows": 500,
            "external_conformance_kasi_rows": 200,
            "external_conformance_policy_rows": 20,
        },
        "selection": reports["selection"],
        "leakage": reports["leakage"],
        "private_artifact_sha256": {
            relative: _payload_sha(payload)
            for relative, payload in private_values.items()
        },
        "canonical_training_fingerprint_changed": False,
        "phase4_smoke_reexecution_required": False,
        "blind_raw_or_ids_in_public_report": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }
    public_values = {
        "external_conformance_report.json": _json_payload(external),
        "split_summary.json": _json_payload(public_summary),
    }
    return private_values, public_values


def _manifest_payload(
    context: dict[str, Any], root: Path, artifacts: dict[str, bytes], *, public: bool
) -> bytes:
    manifest = {
        "schema_version": "1.0.0",
        "report_type": (
            "evaluation_split_public_manifest"
            if public
            else "evaluation_split_private_manifest"
        ),
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "build_inputs": context["build_inputs"],
        "artifact_sha256": {
            relative: sha256_file(root / relative) for relative in sorted(artifacts)
        },
        "status": "sealed_blind_ready_for_post_training_evaluation",
        "canonical_training_fingerprint_changed": False,
        "training_promotion_allowed": True,
        "phase4_smoke_reexecution_required": False,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }
    return _json_payload(manifest)


def _git_clean(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return not result.stdout.strip()


def build_split(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if private_root.exists() or public_root.exists():
        if not private_root.exists() or not public_root.exists():
            raise EvaluationSplitError("평가 split private/public 중 한쪽만 있습니다.")
        return {**verify_split(context, repo_root), "mode": "reused"}
    if not _git_clean(repo_root):
        raise EvaluationSplitError("평가 split 생성 전 working tree가 깨끗해야 합니다.")
    private_values, public_values = _build_payloads(context, repo_root)
    private_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    public_root.parent.mkdir(parents=True, exist_ok=True)
    private_temp = Path(
        tempfile.mkdtemp(prefix=f".{private_root.name}-", dir=private_root.parent)
    )
    public_temp = Path(
        tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent)
    )
    private_promoted = False
    public_promoted = False
    try:
        for relative, payload in private_values.items():
            _atomic_bytes(private_temp / relative, payload, mode=PRIVATE_FILE_MODE)
        for path in [private_temp, *[p for p in private_temp.rglob("*") if p.is_dir()]]:
            path.chmod(PRIVATE_DIR_MODE)
        _atomic_bytes(
            private_temp / "build_manifest.json",
            _manifest_payload(context, private_temp, private_values, public=False),
            mode=PRIVATE_FILE_MODE,
        )
        for relative, payload in public_values.items():
            _atomic_bytes(public_temp / relative, payload, mode=PUBLIC_FILE_MODE)
        _atomic_bytes(
            public_temp / "build_manifest.json",
            _manifest_payload(context, public_temp, public_values, public=True),
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(private_temp, private_root)
        private_promoted = True
        os.replace(public_temp, public_root)
        public_promoted = True
    finally:
        if not private_promoted:
            shutil.rmtree(private_temp, ignore_errors=True)
        if not public_promoted:
            shutil.rmtree(public_temp, ignore_errors=True)
    return {**verify_split(context, repo_root), "mode": "built"}


def _verify_artifacts(
    root: Path, manifest: dict[str, Any], expected: dict[str, bytes], label: str
) -> None:
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(expected):
        raise EvaluationSplitError(f"{label} artifact 목록이 다릅니다.")
    for relative, payload in expected.items():
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != hashes[relative]
            or path.read_bytes() != payload
        ):
            raise EvaluationSplitError(f"{label} artifact가 재현되지 않습니다: {relative}")


def verify_split(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    if (
        private_root.is_symlink()
        or public_root.is_symlink()
        or not private_root.is_dir()
        or not public_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) & 0o077
    ):
        raise EvaluationSplitError("평가 split 경로·private 권한이 다릅니다.")
    private = _load_json(private_root / "build_manifest.json", "private split manifest")
    public = _load_json(public_root / "build_manifest.json", "public split manifest")
    for manifest in (private, public):
        if (
            manifest.get("build_id") != context["build_id"]
            or manifest.get("build_sha256") != context["build_sha256"]
            or manifest.get("build_inputs") != context["build_inputs"]
            or manifest.get("status")
            != "sealed_blind_ready_for_post_training_evaluation"
            or manifest.get("canonical_training_fingerprint_changed") is not False
            or manifest.get("phase4_smoke_reexecution_required") is not False
            or manifest.get("phase5_training_performed") is not False
        ):
            raise EvaluationSplitError("평가 split manifest identity가 다릅니다.")
    expected_private, expected_public = _build_payloads(context, repo_root)
    _verify_artifacts(private_root, private, expected_private, "private split")
    _verify_artifacts(public_root, public, expected_public, "public split")
    for path in private_root.rglob("*"):
        if path.is_file() and stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise EvaluationSplitError(f"private 평가 파일 권한이 다릅니다: {path}")
    for path in public_root.iterdir():
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != PUBLIC_FILE_MODE
        ):
            raise EvaluationSplitError(f"public 평가 파일 형식·권한이 다릅니다: {path}")
    return {
        "status": "verified_sealed_blind_ready",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "dev_monitor_rows": 70,
        "dev_diagnostic_rows": 930,
        "blind_components": 350,
        "blind_rows": 500,
        "external_conformance_rows": 220,
        "canonical_training_fingerprint_changed": False,
        "phase4_smoke_reexecution_required": False,
        "phase5_training_performed": False,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 전 평가 split·봉인 blind Gate")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-contract")
    subparsers.add_parser("plan")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--execute", action="store_true")
    subparsers.add_parser("verify")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config
    if not config_path.is_absolute():
        config_path = REPO_ROOT / config_path
    try:
        if args.command == "validate-contract":
            result = validate_contract(
                _load_json(config_path, "evaluation split config"), REPO_ROOT
            )
        elif args.command == "plan":
            context = prepare_context(REPO_ROOT, config_path)
            result = {
                "status": "planned",
                "build_id": context["build_id"],
                "build_sha256": context["build_sha256"],
                "private_root": context["private_root"].relative_to(REPO_ROOT).as_posix(),
                "public_root": context["public_root"].relative_to(REPO_ROOT).as_posix(),
                "writes_performed": False,
                "phase5_training_performed": False,
            }
        elif args.command == "prepare":
            context = prepare_context(REPO_ROOT, config_path)
            result = (
                build_split(context, REPO_ROOT)
                if args.execute
                else {
                    "status": "dry_run",
                    "build_id": context["build_id"],
                    "writes_performed": False,
                    "phase5_training_performed": False,
                }
            )
        else:
            context = prepare_context(REPO_ROOT, config_path)
            result = verify_split(context, REPO_ROOT)
    except (EvaluationSplitError, ExternalConformanceError) as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
