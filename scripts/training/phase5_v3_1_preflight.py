# phase5_v3_1_preflight.py - v3.1 leakage·token/mask·tool round-trip을 학습 없이 검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.data.mix20k_v3_runtime_build import (
    EXPECTED_CALL_ONLY_ROWS,
    EXPECTED_CHART_CALLS,
    EXPECTED_PERIOD_CALLS,
    EXPECTED_ROWS,
    EXPECTED_TOOL_RESULT_ROWS,
    SOURCE_BUILD_ID,
    SOURCE_BUILD_SHA256,
    SOURCE_MANIFEST_SHA256,
    TARGET_TRAINING,
    TARGET_VERSION,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import (
    ID_CONTRACT_VERSION_V2,
    validate_release_registry_v1_2,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.training.phase5_v3_1_dataset import (
    Phase5V31DatasetError,
    read_training_projection,
    tokenize_training_row,
)
from scripts.training.phase5_v3_preflight import (
    Phase5V3PreflightError,
    _length_stats,
    _verify_model_snapshot,
)

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/"
    "mix20k-v3.1-preflight-v1.0.0.json"
)
PRIVATE_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix20k-v3.1-preflight/v1.0.0"
)
PUBLIC_ROOT = REPO_ROOT / (
    "data/reports/saju_1b_baseline/mix20k-v3.1-preflight/v1.0.0"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
FULL_SHA = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID = re.compile(r"^build-[0-9a-f]{12}$")
GENERATOR_PATH = REPO_ROOT / "scripts/data/mix20k_v3_runtime_build.py"
EXPECTED_BUILD_ARTIFACTS = {
    "review/mix20k_v3.1_review.jsonl",
    TARGET_TRAINING,
    "catalog/trajectory_catalog.jsonl",
    "manifests/record_index.jsonl",
    "manifests/split_manifest.json",
    "diagnostic/diagnostic_2k.jsonl",
    "reports/leakage_report.json",
    "reports/runtime_regrounding_summary.json",
}


class Phase5V31PreflightError(RuntimeError):
    """v3.1 비학습 preflight identity·leakage·token 계약 위반."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Phase5V31PreflightError(f"{label}이 없거나 symlink입니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5V31PreflightError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Phase5V31PreflightError(f"{label} 최상위는 object여야 합니다.")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Phase5V31PreflightError(f"{label}이 없거나 symlink입니다.")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Phase5V31PreflightError(f"{label} 빈 행: {number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Phase5V31PreflightError(f"{label} object 오류: {number}")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Phase5V31PreflightError):
            raise
        raise Phase5V31PreflightError(f"{label}을 읽지 못했습니다.") from exc
    return rows


def _safe_repo_path(relative: str, expected_hash: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise Phase5V31PreflightError(f"{label} 경로가 안전하지 않습니다.")
    cursor = REPO_ROOT
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise Phase5V31PreflightError(f"{label} 경로에 symlink가 있습니다.")
    path = (REPO_ROOT / candidate).resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise Phase5V31PreflightError(f"{label} 경로가 저장소를 벗어납니다.") from exc
    if path.is_symlink() or not path.is_file() or sha256_file(path) != expected_hash:
        raise Phase5V31PreflightError(f"{label} SHA-256이 다릅니다.")
    return path


def _safe_build_artifact(build_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise Phase5V31PreflightError(f"v3.1 artifact 경로가 안전하지 않습니다: {relative}")
    cursor = build_root
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise Phase5V31PreflightError(
                f"v3.1 artifact 경로에 symlink가 있습니다: {relative}"
            )
    path = (build_root / candidate).resolve(strict=False)
    try:
        path.relative_to(build_root.resolve())
    except ValueError as exc:
        raise Phase5V31PreflightError(
            f"v3.1 artifact가 build를 벗어납니다: {relative}"
        ) from exc
    return path


def _verify_build(build_root: Path, release: dict[str, Any]) -> dict[str, Any]:
    if build_root.is_symlink() or not build_root.is_dir():
        raise Phase5V31PreflightError("v3.1 build가 없거나 symlink입니다.")
    manifest = _load_json(build_root / "build_manifest.json", "v3.1 manifest")
    identity = manifest.get("identity")
    artifacts = manifest.get("artifact_sha256")
    identity_fields = {
        "dataset_version",
        "source_build_id",
        "source_build_sha256",
        "source_manifest_sha256",
        "runtime_release_id",
        "runtime_release_registry_sha256",
        "runtime_id_contract_version",
        "generator_sha256",
        "artifact_content_sha256",
    }
    expected_build_sha256 = (
        hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
        if isinstance(identity, dict)
        else None
    )
    if (
        set(manifest)
        != {
            "schema_version",
            "dataset_version",
            "build_id",
            "build_sha256",
            "identity",
            "artifact_sha256",
            "rows",
            "runtime_gate_passed",
            "runtime_release_validated",
            "training_execution_allowed",
            "phase5_training_performed",
            "sealed_blind_payload_accessed",
            "source_build_mutated",
        }
        or manifest.get("schema_version") != "1.0.0"
        or manifest.get("dataset_version") != TARGET_VERSION
        or BUILD_ID.fullmatch(str(manifest.get("build_id", ""))) is None
        or manifest.get("build_id") != build_root.name
        or manifest.get("build_sha256") != expected_build_sha256
        or manifest.get("build_id")
        != f"build-{str(expected_build_sha256 or '')[:12]}"
        or not isinstance(identity, dict)
        or set(identity) != identity_fields
        or identity.get("dataset_version") != TARGET_VERSION
        or identity.get("source_build_id") != SOURCE_BUILD_ID
        or identity.get("source_build_sha256") != SOURCE_BUILD_SHA256
        or identity.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
        or identity.get("runtime_release_id") != release["release_id"]
        or identity.get("runtime_release_registry_sha256")
        != release["release_registry_sha256"]
        or identity.get("runtime_id_contract_version") != ID_CONTRACT_VERSION_V2
        or identity.get("generator_sha256") != sha256_file(GENERATOR_PATH)
        or manifest.get("rows")
        != {"review": EXPECTED_ROWS, "training": EXPECTED_ROWS, "diagnostic": 2000}
        or manifest.get("runtime_gate_passed") is not True
        or manifest.get("runtime_release_validated") is not True
        or manifest.get("training_execution_allowed") is not False
        or manifest.get("phase5_training_performed") is not False
        or manifest.get("sealed_blind_payload_accessed") is not False
        or manifest.get("source_build_mutated") is not False
    ):
        raise Phase5V31PreflightError("v3.1 build identity·governance가 다릅니다.")
    content_hashes = identity["artifact_content_sha256"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != EXPECTED_BUILD_ARTIFACTS
        or not isinstance(content_hashes, dict)
        or artifacts != content_hashes
        or any(
            not isinstance(value, str) or FULL_SHA.fullmatch(value) is None
            for value in artifacts.values()
        )
    ):
        raise Phase5V31PreflightError("v3.1 artifact manifest가 비었습니다.")
    for relative, expected_hash in artifacts.items():
        if not isinstance(relative, str):
            raise Phase5V31PreflightError("v3.1 artifact 경로가 문자열이 아닙니다.")
        path = _safe_build_artifact(build_root, relative)
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != expected_hash
        ):
            raise Phase5V31PreflightError(f"v3.1 artifact hash가 다릅니다: {relative}")
    actual_files = {
        path.relative_to(build_root).as_posix()
        for path in build_root.rglob("*")
        if path.is_file()
    }
    if actual_files != {"build_manifest.json", *EXPECTED_BUILD_ARTIFACTS}:
        raise Phase5V31PreflightError("v3.1 build에 미등록 파일 또는 누락이 있습니다.")
    return manifest


def _verify_projection_release(
    rows: Sequence[Mapping[str, Any]], release_id: str
) -> None:
    actual = {row.get("runtime_release_id") for row in rows}
    if actual != {release_id}:
        raise Phase5V31PreflightError(
            "v3.1 training projection의 runtime release가 build와 다릅니다."
        )
    invalid_fact_sources = 0
    for row in rows:
        messages = row.get("messages")
        has_tool_call = isinstance(messages, list) and any(
            isinstance(message, Mapping) and bool(message.get("tool_calls"))
            for message in messages
        )
        expected = "approved_saju_runtime_v1_2" if has_tool_call else None
        invalid_fact_sources += row.get("runtime_fact_source") != expected
    if invalid_fact_sources:
        raise Phase5V31PreflightError(
            "v3.1 training projection의 tool 사용 여부와 v1.2 runtime fact source가 다릅니다."
        )


def _training_prompt_hash(row: Mapping[str, Any]) -> str:
    messages = row["messages"]
    last_user = max(
        index for index, message in enumerate(messages) if message.get("role") == "user"
    )
    return hashlib.sha256(canonical_json_bytes(messages[: last_user + 1])).hexdigest()


def _training_content_hash(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "prompt_messages": row["messages"][:-1],
                "reference_assistant": row["messages"][-1]["content"],
            }
        )
    ).hexdigest()


def _leakage_report(
    rows: Sequence[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any]:
    training_prompts = {_training_prompt_hash(row) for row in rows}
    evaluation_prompts: set[str] = set()
    evaluation_rows = 0
    evaluation_cases = 0
    for name, identity in config["nonsealed_evaluation"].items():
        path = _safe_repo_path(identity["path"], identity["sha256"], name)
        values = _read_jsonl(path, name)
        if len(values) != identity["rows"]:
            raise Phase5V31PreflightError(f"{name} 행 수가 다릅니다.")
        evaluation_rows += len(values)
        for value in values:
            cases = value.get("cases")
            if not isinstance(cases, list):
                raise Phase5V31PreflightError(f"{name} cases가 list가 아닙니다.")
            for case in cases:
                prompt_hash = case.get("prompt_sha256") if isinstance(case, dict) else None
                if not isinstance(prompt_hash, str) or len(prompt_hash) != 64:
                    raise Phase5V31PreflightError(f"{name} prompt hash가 다릅니다.")
                evaluation_prompts.add(prompt_hash)
                evaluation_cases += 1
    blind_identity = config["sealed_blind_hash_only"]
    blind_path = _safe_repo_path(
        blind_identity["path"], blind_identity["sha256"], "sealed blind hash manifest"
    )
    blind_rows = _read_jsonl(blind_path, "sealed blind hash manifest")
    if len(blind_rows) != blind_identity["components"]:
        raise Phase5V31PreflightError("sealed blind component 수가 다릅니다.")
    blind_content: set[str] = set()
    allowed = {
        "assistant_tokens",
        "component_id",
        "content_sha256",
        "record_ids",
        "record_sha256",
        "schema_version",
        "selector_rank",
        "source_axis",
        "split_role",
        "total_tokens",
    }
    for value in blind_rows:
        if set(value) - allowed or value.get("split_role") != "blind_source_test":
            raise Phase5V31PreflightError("sealed blind hash-only field 계약이 다릅니다.")
        hashes = value.get("content_sha256")
        if not isinstance(hashes, list) or any(
            not isinstance(item, str) or len(item) != 64 for item in hashes
        ):
            raise Phase5V31PreflightError("sealed blind content hash 계약이 다릅니다.")
        blind_content.update(hashes)
    content_hashes = {_training_content_hash(row) for row in rows}
    return {
        "schema_version": "1.0.0",
        "status": "passed"
        if not (training_prompts & evaluation_prompts or content_hashes & blind_content)
        else "blocked_overlap",
        "training_rows": len(rows),
        "training_prompt_hashes": len(training_prompts),
        "nonsealed_evaluation_rows": evaluation_rows,
        "nonsealed_evaluation_cases": evaluation_cases,
        "nonsealed_eval_prompt_overlap": len(training_prompts & evaluation_prompts),
        "sealed_blind_components_checked": len(blind_rows),
        "sealed_blind_content_hash_overlap": len(content_hashes & blind_content),
        "sealed_blind_payload_read": False,
        "sealed_blind_prompts_or_references_read": False,
        "raw_samples_in_report": False,
    }


def analyze(
    *,
    build_root: Path,
    tokenizer_path: Path,
    release_registry: Path,
    config_path: Path = DEFAULT_CONFIG,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    try:
        release = validate_release_registry_v1_2(release_registry)
    except RuntimeCalculationError as exc:
        raise Phase5V31PreflightError(
            "유효한 runtime release 전에는 v3.1 preflight를 실행하지 않습니다: "
            + exc.message
        ) from exc
    manifest = _verify_build(build_root, release)
    config = _load_json(config_path, "v3.1 preflight config")
    if (
        config.get("dataset_version") != "mix20k-v3.1-runtime-grounded"
        or config.get("rows") != 20_000
        or config.get("model", {}).get("max_length") != 768
        or config.get("model", {}).get("assistant_only_loss") is not True
        or config.get("governance", {}).get("training_method_called") is not False
        or config.get("governance", {}).get("sealed_blind_payload_accessed") is not False
    ):
        raise Phase5V31PreflightError("v3.1 preflight config가 다릅니다.")
    model_report = _verify_model_snapshot(tokenizer_path)
    try:
        import transformers
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise Phase5V31PreflightError("고정 Transformers 환경이 필요합니다.") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path, local_files_only=True, trust_remote_code=True
    )
    template = tokenizer.chat_template
    if (
        not isinstance(template, str)
        or hashlib.sha256(template.encode()).hexdigest()
        != config["model"]["chat_template_sha256"]
        or tokenizer.bos_token_id != 128000
        or tokenizer.eos_token_id != 128010
        or tokenizer.pad_token_id != 128001
    ):
        raise Phase5V31PreflightError("tokenizer/template/special token 계약이 다릅니다.")
    rows = read_training_projection(build_root)
    _verify_projection_release(rows, release["release_id"])
    token_rows: list[dict[str, Any]] = []
    lengths: list[int] = []
    assistant_lengths: list[int] = []
    axis_lengths: dict[str, list[int]] = defaultdict(list)
    over_by_axis: Counter[str] = Counter()
    tool_calls = 0
    call_names: Counter[str] = Counter()
    stored_tool_result_rows = 0
    call_only_rows = 0
    for number, row in enumerate(rows, 1):
        tokenized = tokenize_training_row(
            tokenizer, row, max_length=config["model"]["max_length"]
        )
        token_rows.append(tokenized)
        lengths.append(tokenized["total_tokens"])
        assistant_lengths.append(tokenized["assistant_tokens"])
        axis_lengths[tokenized["task_axis"]].append(tokenized["total_tokens"])
        if tokenized["over_max_length"]:
            over_by_axis[tokenized["task_axis"]] += 1
        tool_calls += tokenized["tool_calls_roundtripped"]
        row_calls = [
            call["function"]["name"]
            for message in row["messages"]
            for call in message.get("tool_calls", [])
        ]
        call_names.update(row_calls)
        if row_calls:
            if any(message.get("role") == "tool" for message in row["messages"]):
                stored_tool_result_rows += 1
            else:
                call_only_rows += 1
        if number % 2000 == 0:
            print(f"v3_1_tokenization_progress={number}/20000", file=sys.stderr, flush=True)
    if (
        call_names
        != {
            "calculate_saju_chart": EXPECTED_CHART_CALLS,
            "calculate_saju_period": EXPECTED_PERIOD_CALLS,
        }
        or tool_calls != EXPECTED_CHART_CALLS + EXPECTED_PERIOD_CALLS
        or stored_tool_result_rows != EXPECTED_TOOL_RESULT_ROWS
        or call_only_rows != EXPECTED_CALL_ONLY_ROWS
    ):
        raise Phase5V31PreflightError(
            "v3.1 tool trajectory 전수 수량이 생성 계약과 다릅니다."
        )
    over = sum(over_by_axis.values())
    leakage = _leakage_report(rows, config)
    token_report = {
        "schema_version": "1.0.0",
        "status": "passed" if over == 0 else "blocked_over_max_length",
        "build_id": manifest["build_id"],
        "runtime_release_id": release["release_id"],
        "rows": len(rows),
        "max_length": config["model"]["max_length"],
        "length": _length_stats(lengths),
        "assistant_tokens": _length_stats(assistant_lengths),
        "axes": {
            axis: {
                "length": _length_stats(values),
                "over_max_length_rows": over_by_axis[axis],
            }
            for axis, values in sorted(axis_lengths.items())
        },
        "over_max_length_rows": over,
        "zero_assistant_mask_rows": 0,
        "pre_last_user_supervised_rows": 0,
        "missing_final_assistant_eos_rows": 0,
        "serialization_mismatch_rows": 0,
        "tool_response_supervised_rows": 0,
        "structured_tool_calls_roundtripped": tool_calls,
        "tool_call_names": dict(sorted(call_names.items())),
        "stored_tool_result_rows": stored_tool_result_rows,
        "call_only_rows": call_only_rows,
        "tool_roundtrip_errors": 0,
        "chat_template_sha256": config["model"]["chat_template_sha256"],
        "tokenizer_revision": config["model"]["revision"],
        "transformers_version": transformers.__version__,
        "model_snapshot": model_report,
        "model_weights_loaded": False,
        "training_method_called": False,
        "backward_performed": False,
        "optimizer_step_performed": False,
        "training_promotion_allowed": False,
        "raw_samples_in_report": False,
    }
    status = (
        "passed_non_training_preflight"
        if token_report["status"] == "passed" and leakage["status"] == "passed"
        else "blocked_preflight_failure"
    )
    aggregate = {
        "schema_version": "1.0.0",
        "status": status,
        "tokenization": token_report,
        "leakage": leakage,
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "sealed_blind_payload_accessed": False,
    }
    return aggregate, leakage, token_rows


def _write(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"


def _jsonl_bytes(values: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
        for value in values
    )


def _write_build(
    root: Path,
    preflight_id: str,
    aggregate: dict[str, Any],
    token_rows: Sequence[Mapping[str, Any]] | None,
    *,
    public: bool,
    identity: dict[str, Any],
) -> Path:
    destination = root / preflight_id
    if destination.exists() or destination.is_symlink():
        raise Phase5V31PreflightError("같은 preflight ID를 덮어쓰지 않습니다.")
    mode = PUBLIC_FILE_MODE if public else PRIVATE_FILE_MODE
    dir_mode = PUBLIC_DIR_MODE if public else PRIVATE_DIR_MODE
    root.mkdir(parents=True, mode=dir_mode, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{preflight_id}.", dir=root))
    temporary.chmod(dir_mode)
    try:
        _write(temporary / "aggregate.json", _json_bytes(aggregate), mode)
        if token_rows is not None:
            _write(temporary / "token_rows.jsonl", _jsonl_bytes(token_rows), mode)
        artifacts = {
            path.relative_to(temporary).as_posix(): sha256_file(path)
            for path in sorted(temporary.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "mix20k_v3_1_non_training_preflight",
            "preflight_id": preflight_id,
            "identity": identity,
            "artifact_sha256": artifacts,
            "status": aggregate["status"],
            "public_aggregate_only": public,
            "training_method_called": False,
            "backward_performed": False,
            "optimizer_step_performed": False,
            "training_promotion_allowed": False,
            "phase5_training_performed": False,
            "sealed_blind_payload_accessed": False,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        _write(temporary / "build_manifest.json", _json_bytes(manifest), mode)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def run(
    *,
    build_root: Path,
    tokenizer_path: Path,
    release_registry: Path,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    aggregate, _leakage, token_rows = analyze(
        build_root=build_root,
        tokenizer_path=tokenizer_path,
        release_registry=release_registry,
        config_path=config_path,
    )
    identity = {
        "dataset_build_manifest_sha256": sha256_file(build_root / "build_manifest.json"),
        "runtime_release_registry_sha256": sha256_file(release_registry),
        "config_sha256": sha256_file(config_path),
        "tokenizer_json_sha256": sha256_file(tokenizer_path / "tokenizer.json"),
        "implementation_sha256": sha256_file(Path(__file__)),
        "dataset_loader_sha256": sha256_file(
            REPO_ROOT / "scripts/training/phase5_v3_1_dataset.py"
        ),
    }
    digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    preflight_id = "preflight-" + digest[:12]
    private = _write_build(
        PRIVATE_ROOT,
        preflight_id,
        aggregate,
        token_rows,
        public=False,
        identity=identity,
    )
    public = _write_build(
        PUBLIC_ROOT,
        preflight_id,
        aggregate,
        None,
        public=True,
        identity=identity,
    )
    return {
        "status": aggregate["status"],
        "preflight_id": preflight_id,
        "private_root": str(private),
        "public_root": str(public),
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIX20K-v3.1 비학습 preflight")
    parser.add_argument("command", choices=["analyze", "run"])
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--tokenizer-path", type=Path, required=True)
    parser.add_argument("--release-registry", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "analyze":
            aggregate, _leakage, _tokens = analyze(
                build_root=args.build_root,
                tokenizer_path=args.tokenizer_path,
                release_registry=args.release_registry,
                config_path=args.config,
            )
            result = aggregate
        else:
            result = run(
                build_root=args.build_root,
                tokenizer_path=args.tokenizer_path,
                release_registry=args.release_registry,
                config_path=args.config,
            )
    except (
        OSError,
        Phase5V31DatasetError,
        Phase5V31PreflightError,
        Phase5V3PreflightError,
    ) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
