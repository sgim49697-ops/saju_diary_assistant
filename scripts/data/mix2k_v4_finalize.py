# mix2k_v4_finalize.py - 교차 검수 2K candidate를 전수 token audit해 학습 build로 고정한다.

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_build import (
    DEFAULT_CONFIG,
    _load_config,
    _validate_model_snapshot,
)
from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    EXPECTED_ROWS,
    MAX_COMPLETION_TOKENS,
    MAX_PROMPT_TOKENS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    Mix2KV4ContractError,
    jsonl_bytes,
    normalize_answer,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    validate_draft,
)
from scripts.data.mix2k_v4_teachers import _validate_spec_build
from scripts.runtime.calculation.canonical import canonical_json_bytes

DEFAULT_OUTPUT_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/final/v1.0.0"
)
FINALIZER_PATH = Path(__file__).resolve()
CONTRACTS_PATH = FINALIZER_PATH.with_name("mix2k_v4_contracts.py")
MAX_JSON_BYTES = 64 * 1024 * 1024
CANDIDATE_FIELDS = {
    "schema_version",
    "dataset_version",
    "id",
    "conversation_id",
    "task_axis",
    "template_family",
    "substantive",
    "multiturn",
    "prompt",
    "assistant",
    "runtime_snapshot_sha256",
    "teacher",
    "restricted_local_only",
}
TEACHER_FIELDS = {
    "drafter",
    "reviewer",
    "peer_review",
    "deterministic_validation",
    "rewrites_used",
    "used_fact_paths",
    "used_fact_values",
    "soft_interpretation_used",
    "limitations",
}


class Mix2KV4FinalizeError(RuntimeError):
    """2K candidate·token audit·immutable final build 계약 위반."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4FinalizeError(f"{label} 경로에 symlink component가 있습니다.")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_JSON_BYTES
    ):
        raise Mix2KV4FinalizeError(f"{label}이 없거나 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4FinalizeError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4FinalizeError(f"{label} 최상위는 object여야 합니다.")
    return value


def _atomic_build(
    root: Path, build_id: str, files: Mapping[str, bytes]
) -> tuple[Path, str]:
    target = root / build_id
    if target.exists():
        for relative, payload in files.items():
            path = target / relative
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise Mix2KV4FinalizeError("기존 final build가 동일 identity와 다릅니다.")
        return target, "reused"
    root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    root.chmod(PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=root))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for relative, payload in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
            path.parent.chmod(PRIVATE_DIR_MODE)
            path.write_bytes(payload)
            path.chmod(PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target, "created"


def select_training_max_length(
    maximum_tokens: int, ladder: Sequence[int]
) -> int | None:
    for value in ladder:
        if maximum_tokens <= value:
            return value
    return None


def _teacher_inputs(
    teacher_build: Path,
) -> tuple[dict[str, Any], Path, list[dict[str, Any]]]:
    if teacher_build.is_symlink() or not teacher_build.is_dir():
        raise Mix2KV4FinalizeError("teacher build가 없거나 symlink입니다.")
    manifest = _load_json(teacher_build / "teacher_manifest.json", "teacher manifest")
    relative = manifest.get("candidate_path")
    if (
        manifest.get("dataset_version") != DATASET_VERSION
        or manifest.get("mode") != "full"
        or manifest.get("rows") != EXPECTED_ROWS
        or relative != "accepted/training_candidates_2000.jsonl"
        or manifest.get("peer_review_passed") is not True
        or manifest.get("deterministic_validation_passed") is not True
        or manifest.get("contracts_sha256") != sha256_file(CONTRACTS_PATH)
        or manifest.get("teacher_roles") != {"claude": 1000, "codex": 1000}
        or manifest.get("review_roles") != {"claude": 1000, "codex": 1000}
        or manifest.get("development_targets_accessed") is not False
        or manifest.get("api_keys_used") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("training_execution_allowed") is not False
    ):
        raise Mix2KV4FinalizeError("teacher manifest 교차검수·격리 계약이 다릅니다.")
    candidate_path = teacher_build / relative
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise Mix2KV4FinalizeError("teacher candidate가 없거나 symlink입니다.")
    if sha256_file(candidate_path) != manifest.get("candidate_sha256"):
        raise Mix2KV4FinalizeError("teacher candidate hash가 다릅니다.")
    try:
        rows = read_jsonl(candidate_path)
    except Mix2KV4ContractError as exc:
        raise Mix2KV4FinalizeError(str(exc)) from exc
    return manifest, candidate_path, rows


def _validate_candidates(
    candidates: Sequence[dict[str, Any]],
    specs: Sequence[dict[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    if len(candidates) != EXPECTED_ROWS:
        raise Mix2KV4FinalizeError("teacher candidate가 2,000행이 아닙니다.")
    specs_by_id = {row["id"]: row for row in specs}
    seen: set[str] = set()
    answers: list[str] = []
    for row in candidates:
        if not isinstance(row, dict) or set(row) != CANDIDATE_FIELDS:
            raise Mix2KV4FinalizeError("teacher candidate field 집합이 다릅니다.")
        record_id = row.get("id")
        spec = specs_by_id.get(record_id)
        teacher = row.get("teacher")
        expected_snapshot = (
            spec["runtime_binding"]["snapshot_sha256"]
            if spec is not None and spec["runtime_binding"] is not None
            else None
        )
        if (
            spec is None
            or record_id in seen
            or row.get("schema_version") != "1.0.0"
            or row.get("dataset_version") != DATASET_VERSION
            or row.get("conversation_id") != spec["conversation_id"]
            or row.get("task_axis") != spec["task_axis"]
            or row.get("template_family") != spec["template_family"]
            or row.get("substantive") is not spec["substantive"]
            or row.get("multiturn") is not spec["multiturn"]
            or row.get("prompt") != spec["prompt"]
            or row.get("runtime_snapshot_sha256") != expected_snapshot
            or row.get("restricted_local_only") is not False
            or not isinstance(row.get("assistant"), str)
            or not row["assistant"].strip()
            or not isinstance(teacher, dict)
            or set(teacher) != TEACHER_FIELDS
            or teacher.get("drafter") != spec["drafter"]
            or teacher.get("reviewer") != spec["reviewer"]
            or teacher.get("peer_review") != "PASS"
            or teacher.get("deterministic_validation") != "PASS"
            or isinstance(teacher.get("rewrites_used"), bool)
            or not isinstance(teacher.get("rewrites_used"), int)
            or not 0 <= teacher["rewrites_used"] <= 2
        ):
            raise Mix2KV4FinalizeError(f"teacher candidate identity가 다릅니다: {record_id}")
        draft = {
            "record_id": record_id,
            "answer": row["assistant"],
            "used_fact_paths": teacher["used_fact_paths"],
            "used_fact_values": teacher["used_fact_values"],
            "soft_interpretation_used": teacher["soft_interpretation_used"],
            "limitations": teacher["limitations"],
            "self_check": "PASS",
        }
        try:
            validate_draft(spec, draft)
        except Mix2KV4ContractError as exc:
            raise Mix2KV4FinalizeError(str(exc)) from exc
        seen.add(record_id)
        answers.append(row["assistant"].strip())
    if seen != set(specs_by_id):
        raise Mix2KV4FinalizeError("teacher candidate ID 집합이 spec과 다릅니다.")
    exact = Counter(answers)
    normalized = Counter(normalize_answer(answer) for answer in answers)
    exact_duplicates = sum(count - 1 for count in exact.values() if count > 1)
    normalized_maximum = max(normalized.values(), default=0)
    if (
        exact_duplicates > int(config["diversity"]["exact_duplicate_answers_maximum"])
        or normalized_maximum
        > int(config["diversity"]["normalized_answer_multiplicity_maximum"])
    ):
        raise Mix2KV4FinalizeError("teacher candidate 답변 중복 계약을 넘었습니다.")
    axes = Counter(row["task_axis"] for row in candidates)
    drafters = Counter(row["teacher"]["drafter"] for row in candidates)
    reviewers = Counter(row["teacher"]["reviewer"] for row in candidates)
    if dict(axes) != EXPECTED_AXES or drafters != {"claude": 1000, "codex": 1000}:
        raise Mix2KV4FinalizeError("teacher candidate axis·drafter 비율이 다릅니다.")
    if reviewers != {"claude": 1000, "codex": 1000}:
        raise Mix2KV4FinalizeError("teacher candidate reviewer 비율이 다릅니다.")
    return {
        "rows": len(candidates),
        "axes": dict(sorted(axes.items())),
        "drafters": dict(sorted(drafters.items())),
        "reviewers": dict(sorted(reviewers.items())),
        "exact_duplicate_answers": exact_duplicates,
        "normalized_answer_multiplicity_maximum": normalized_maximum,
    }


def _stats(values: Sequence[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    if not ordered:
        raise Mix2KV4FinalizeError("token 통계 대상이 비었습니다.")
    return {
        "minimum": ordered[0],
        "median": ordered[len(ordered) // 2],
        "p90": ordered[math.ceil(len(ordered) * 0.9) - 1],
        "p99": ordered[math.ceil(len(ordered) * 0.99) - 1],
        "maximum": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 3),
    }


def _tokenize_row(tokenizer: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    messages = [*deepcopy(row["prompt"]), {"role": "assistant", "content": row["assistant"]}]
    try:
        processed = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            return_dict=True,
            return_assistant_tokens_mask=True,
            add_generation_prompt=False,
        )
        rendered = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False
        )
        direct_ids = tokenizer(rendered, add_special_tokens=False)["input_ids"]
        prefix_ids = tokenizer.apply_chat_template(
            row["prompt"], tokenize=True, add_generation_prompt=False
        )
    except Exception as exc:
        raise Mix2KV4FinalizeError(
            f"chat template tokenization이 실패했습니다: {row.get('id')}"
        ) from exc
    if not isinstance(processed, Mapping):
        raise Mix2KV4FinalizeError("tokenizer BatchEncoding이 Mapping이 아닙니다.")
    input_ids = processed.get("input_ids")
    assistant_masks = processed.get("assistant_masks")
    attention_mask = processed.get("attention_mask")
    if (
        not isinstance(input_ids, list)
        or not isinstance(assistant_masks, list)
        or not isinstance(attention_mask, list)
        or not isinstance(prefix_ids, list)
        or input_ids != direct_ids
        or len(input_ids) != len(assistant_masks)
        or len(input_ids) != len(attention_mask)
        or input_ids[: len(prefix_ids)] != prefix_ids
        or any(value not in {0, 1} for value in assistant_masks)
        or any(value != 1 for value in attention_mask)
    ):
        raise Mix2KV4FinalizeError(f"token·mask 계약이 다릅니다: {row.get('id')}")
    supervised = int(sum(assistant_masks))
    prompt_tokens = len(input_ids) - supervised
    eos_positions = [
        index
        for index, token_id in enumerate(input_ids)
        if token_id == tokenizer.eos_token_id
    ]
    leakage = int(sum(assistant_masks[: len(prefix_ids)]))
    if (
        supervised <= 0
        or leakage != 0
        or not eos_positions
        or assistant_masks[eos_positions[-1]] != 1
        or not any(
            token_id == tokenizer.eos_token_id and mask == 1
            for token_id, mask in zip(input_ids, assistant_masks, strict=True)
        )
    ):
        raise Mix2KV4FinalizeError(
            f"assistant-only loss·EOS 계약이 다릅니다: {row.get('id')}"
        )
    if prompt_tokens > MAX_PROMPT_TOKENS:
        raise Mix2KV4FinalizeError(f"입력 4K token 상한을 넘었습니다: {row.get('id')}")
    if supervised > MAX_COMPLETION_TOKENS:
        raise Mix2KV4FinalizeError(f"출력 4K token 상한을 넘었습니다: {row.get('id')}")
    return {
        "id": row["id"],
        "task_axis": row["task_axis"],
        "rendered_tokens": len(input_ids),
        "prompt_tokens": prompt_tokens,
        "supervised_assistant_tokens": supervised,
        "truncated": False,
        "assistant_mask_nonzero": True,
        "assistant_mask_sha256": sha256_bytes(bytes(assistant_masks)),
        "final_eos_supervised": True,
        "user_system_loss_leakage_tokens": leakage,
    }


def _token_audit(
    candidates: Sequence[dict[str, Any]], tokenizer_path: Path, config: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from transformers import AutoTokenizer
    except Exception as exc:
        raise Mix2KV4FinalizeError("Transformers tokenizer import가 실패했습니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    expected_template = config["base_model"]["files"]["chat_template.jinja"]
    if (
        not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8")) != expected_template
    ):
        raise Mix2KV4FinalizeError("pinned Kanana chat template hash가 다릅니다.")
    rows = [_tokenize_row(tokenizer, row) for row in candidates]
    rendered = [row["rendered_tokens"] for row in rows]
    prompt = [row["prompt_tokens"] for row in rows]
    supervised = [row["supervised_assistant_tokens"] for row in rows]
    ladder = config["token_budget"]["training_selection_ladder"]
    selected = select_training_max_length(max(rendered), ladder)
    over_2048 = sum(value > 2048 for value in rendered)
    many_over_2048 = over_2048 > max(20, math.floor(len(rows) * 0.01))
    if selected is None:
        raise Mix2KV4FinalizeError("8,192 token 합산 상한을 넘는 행이 있습니다.")
    return rows, {
        "schema_version": "1.0.0",
        "rows": len(rows),
        "rendered_tokens": _stats(rendered),
        "prompt_tokens": _stats(prompt),
        "supervised_assistant_tokens": _stats(supervised),
        "rows_over_2048": over_2048,
        "rows_over_3584": sum(value > 3584 for value in rendered),
        "rows_over_4096": sum(value > 4096 for value in rendered),
        "rows_over_8192": sum(value > 8192 for value in rendered),
        "truncated_rows": 0,
        "zero_assistant_mask_rows": 0,
        "missing_supervised_eos_rows": 0,
        "user_system_loss_leakage_rows": 0,
        "selected_max_length": None if many_over_2048 else selected,
        "provisional_ladder_value": selected,
        "many_rows_over_2048": many_over_2048,
        "runtime_projection_review_required": many_over_2048,
        "training_blocked_pending_projection_review": many_over_2048,
    }


def finalize(
    *,
    config_path: Path,
    spec_build: Path,
    teacher_build: Path,
    tokenizer_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    for path, label in (
        (config_path, "config"),
        (spec_build, "spec build"),
        (teacher_build, "teacher build"),
        (tokenizer_path, "K0 model snapshot"),
        (output_root, "private output"),
    ):
        _reject_symlink_components(path, label)
    config = _load_config(config_path)
    model_files = _validate_model_snapshot(tokenizer_path, config)
    _, spec_manifest, specs = _validate_spec_build(spec_build, config_path)
    teacher_manifest, candidate_path, candidates = _teacher_inputs(teacher_build)
    if (
        teacher_manifest.get("spec_build_id") != spec_manifest["build_id"]
        or teacher_manifest.get("spec_build_sha256") != spec_manifest["build_sha256"]
    ):
        raise Mix2KV4FinalizeError("teacher와 spec build identity가 다릅니다.")
    candidate_validation = _validate_candidates(candidates, specs, config)
    audits, audit_summary = _token_audit(candidates, tokenizer_path, config)
    projection_path = spec_build / "reports/full_runtime_projection_ab.json"
    projection = _load_json(projection_path, "full runtime projection report")
    if (
        sha256_file(projection_path)
        != spec_manifest.get("artifact_sha256", {}).get(
            "reports/full_runtime_projection_ab.json"
        )
        or projection.get("training_uses_full_runtime_snapshot") is not True
        or projection.get("compact_projection_used_for_training") is not False
    ):
        raise Mix2KV4FinalizeError("full runtime projection report 계약이 다릅니다.")
    training_rows = [
        {
            "schema_version": "1.0.0",
            "dataset_version": DATASET_VERSION,
            "id": row["id"],
            "task_axis": row["task_axis"],
            "messages": [
                *deepcopy(row["prompt"]),
                {"role": "assistant", "content": row["assistant"]},
            ],
            "assistant_only_loss": True,
            "runtime_snapshot_sha256": row["runtime_snapshot_sha256"],
            "restricted_local_only": False,
        }
        for row in candidates
    ]
    training_bytes = jsonl_bytes(training_rows)
    audits_bytes = jsonl_bytes(audits)
    summary_bytes = _json_bytes(
        {
            **audit_summary,
            "candidate_validation": candidate_validation,
            "full_runtime_vs_audit_projection": projection,
        }
    )
    identity = {
        "dataset_version": DATASET_VERSION,
        "config_sha256": sha256_file(config_path),
        "spec_build_sha256": spec_manifest["build_sha256"],
        "teacher_candidate_sha256": sha256_file(candidate_path),
        "teacher_manifest_sha256": sha256_file(
            teacher_build / "teacher_manifest.json"
        ),
        "finalizer_sha256": sha256_file(FINALIZER_PATH),
        "contracts_sha256": sha256_file(CONTRACTS_PATH),
        "base_model_files": model_files,
        "training_rows_sha256": sha256_bytes(training_bytes),
        "token_audit_rows_sha256": sha256_bytes(audits_bytes),
        "token_audit_summary_sha256": sha256_bytes(summary_bytes),
    }
    build_sha = sha256_bytes(canonical_json_bytes(identity))
    build_id = f"build-{build_sha[:12]}"
    manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "build_id": build_id,
        "build_sha256": build_sha,
        "identity": identity,
        "rows": EXPECTED_ROWS,
        "selected_max_length": audit_summary["selected_max_length"],
        "assistant_only_loss": True,
        "truncation": False,
        "full_runtime_snapshot_used": True,
        "compact_projection_used_for_training": False,
        "development_targets_accessed": False,
        "sealed_blind_accessed": False,
        "training_execution_allowed": not audit_summary[
            "training_blocked_pending_projection_review"
        ],
        "training_performed": False,
        "artifact_sha256": {
            "training/train_2000.jsonl": identity["training_rows_sha256"],
            "reports/token_audit_2000.jsonl": identity["token_audit_rows_sha256"],
            "reports/token_audit_summary.json": identity[
                "token_audit_summary_sha256"
            ],
        },
    }
    files = {
        "training/train_2000.jsonl": training_bytes,
        "reports/token_audit_2000.jsonl": audits_bytes,
        "reports/token_audit_summary.json": summary_bytes,
        "build_manifest.json": _json_bytes(manifest),
    }
    target, mode = _atomic_build(output_root, build_id, files)
    return {
        **manifest,
        "mode": mode,
        "path": str(target),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIX2K v4 final token audit builder")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--spec-build", type=Path, required=True)
    parser.add_argument("--teacher-build", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = finalize(
            config_path=_absolute(args.config),
            spec_build=_absolute(args.spec_build),
            teacher_build=_absolute(args.teacher_build),
            tokenizer_path=_absolute(args.tokenizer),
            output_root=_absolute(args.output_root),
        )
    except (Mix2KV4FinalizeError, Mix2KV4ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["training_execution_allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
