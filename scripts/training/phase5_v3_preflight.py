# phase5_v3_preflight.py - v3 20K의 Kanana token/mask·tool XML round-trip을 비학습으로 검증한다.

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix20k_v3_repair import (
    Mix20KV3Error,
    verify_private_build,
)
from scripts.training.phase5_v3_dataset import (
    Phase5V3DatasetError,
    read_training_projection,
    tokenize_training_row,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/mix20k-v3-repair-v1.0.0.json"
)
MODEL_PREPARATION_CONFIG = Path(
    "configs/model_versions/saju_1b_baseline/model-preparation-v1.0.0.json"
)
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644


class Phase5V3PreflightError(RuntimeError):
    """v3 tokenizer/parser 비학습 preflight 계약 위반."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(4 * 1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise Phase5V3PreflightError(f"SHA-256을 계산할 수 없습니다: {path}") from exc
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5V3PreflightError(f"{label}을 읽을 수 없습니다: {path}") from exc
    if not isinstance(value, dict):
        raise Phase5V3PreflightError(f"{label} 최상위는 object여야 합니다.")
    return value


def _write(path: Path, payload: bytes, mode: int) -> None:
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
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json(path: Path, value: Any, mode: int) -> None:
    _write(path, _json_bytes(value), mode)


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]], mode: int) -> None:
    payload = b"".join(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
        + b"\n"
        for value in values
    )
    _write(path, payload, mode)


def _safe_directory(path: Path, label: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    try:
        value = candidate.lstat()
    except OSError as exc:
        raise Phase5V3PreflightError(f"{label}이 없습니다: {candidate}") from exc
    if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise Phase5V3PreflightError(f"{label}은 symlink가 아닌 directory여야 합니다.")
    return candidate.resolve()


def _artifact_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Phase5V3PreflightError("preflight 산출물에 symlink가 있습니다.")
        if path.is_file() and path.name != "build_manifest.json":
            result[path.relative_to(root).as_posix()] = _sha256_file(path)
    return result


def _verify_existing(root: Path, preflight_id: str) -> dict[str, Any]:
    directory = _safe_directory(root, "preflight build")
    manifest = _load_json(directory / "build_manifest.json", "preflight manifest")
    if manifest.get("preflight_id") != preflight_id:
        raise Phase5V3PreflightError("기존 preflight identity가 다릅니다.")
    if manifest.get("artifact_sha256") != _artifact_hashes(directory):
        raise Phase5V3PreflightError("기존 preflight artifact hash가 다릅니다.")
    return manifest


def _percentile(values: Sequence[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def _length_stats(values: Sequence[int]) -> dict[str, int | float]:
    if not values:
        return {
            "count": 0,
            "min": 0,
            "mean": 0.0,
            "median": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
            "max": 0,
        }
    return {
        "count": len(values),
        "min": min(values),
        "mean": round(sum(values) / len(values), 4),
        "median": _percentile(values, 0.5),
        "p90": _percentile(values, 0.9),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values),
    }


def _verify_model_snapshot(snapshot: Path) -> dict[str, Any]:
    root = _safe_directory(snapshot, "Kanana snapshot")
    preparation = _load_json(
        REPO_ROOT / MODEL_PREPARATION_CONFIG, "model preparation config"
    )
    model = preparation["model"]
    expected = {
        item["path"]: item["sha256"]
        for item in model["files"]
        if item["path"]
        in {
            "chat_template.jinja",
            "config.json",
            "configuration_kanana2_tiny.py",
            "modeling_kanana2_tiny.py",
            "tokenizer.json",
            "tokenizer_config.json",
        }
    }
    for relative, expected_hash in expected.items():
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != expected_hash
        ):
            raise Phase5V3PreflightError(
                f"Kanana 고정 파일 hash가 다릅니다: {relative}"
            )
    if (
        model["revision"] != "bf4786aa2a1908adce942d53976270132732f720"
        or preparation["chat_template"]["sha256"] != expected["chat_template.jinja"]
    ):
        raise Phase5V3PreflightError("Kanana revision/template 계약이 다릅니다.")
    return {
        "repo_id": model["repo_id"],
        "revision": model["revision"],
        "verified_file_sha256": expected,
        "model_weights_loaded": False,
    }


def analyze(
    build_root: Path,
    tokenizer_path: Path,
    config_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    private = verify_private_build(build_root)
    config = _load_json(config_path, "v3 repair config")
    if (
        config.get("governance", {}).get("full_training_execution_enabled") is not False
        or config.get("model", {}).get("max_length") != 768
    ):
        raise Phase5V3PreflightError("비학습/max_length 계약이 다릅니다.")
    model_report = _verify_model_snapshot(tokenizer_path)
    try:
        import transformers
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise Phase5V3PreflightError(
            "고정 Transformers 환경에서 실행해야 합니다."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path,
        local_files_only=True,
        trust_remote_code=True,
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
        raise Phase5V3PreflightError(
            "tokenizer/template/special token 계약이 다릅니다."
        )
    rows = read_training_projection(build_root)
    token_rows: list[dict[str, Any]] = []
    axis_lengths: dict[str, list[int]] = defaultdict(list)
    axis_over: Counter[str] = Counter()
    axis_eligible_over: Counter[str] = Counter()
    tool_calls = 0
    for index, row in enumerate(rows, 1):
        value = tokenize_training_row(
            tokenizer,
            row,
            max_length=config["model"]["max_length"],
        )
        token_rows.append(value)
        tool_calls += value["tool_calls_roundtripped"]
        axis = value["task_axis"]
        axis_lengths[axis].append(value["total_tokens"])
        if value["over_max_length"]:
            axis_over[axis] += 1
            if value["train_candidate"]:
                axis_eligible_over[axis] += 1
        if index % 2000 == 0:
            print(
                f"v3_tokenization_progress={index}/20000", file=sys.stderr, flush=True
            )
    lengths = [row["total_tokens"] for row in token_rows]
    assistant = [row["assistant_tokens"] for row in token_rows]
    eligible = [row for row in token_rows if row["train_candidate"]]
    over = [row for row in token_rows if row["over_max_length"]]
    eligible_over = [row for row in eligible if row["over_max_length"]]
    report = {
        "schema_version": "1.0.0",
        "status": "passed" if not over else "blocked_over_max_length",
        "build_id": private["build_id"],
        "rows": len(rows),
        "max_length": config["model"]["max_length"],
        "length": _length_stats(lengths),
        "assistant_tokens": _length_stats(assistant),
        "axes": {
            axis: {
                "length": _length_stats(values),
                "over_max_length_rows": axis_over[axis],
                "eligible_over_max_length_rows": axis_eligible_over[axis],
            }
            for axis, values in sorted(axis_lengths.items())
        },
        "over_max_length_rows": len(over),
        "eligible_rows": len(eligible),
        "eligible_over_max_length_rows": len(eligible_over),
        "zero_assistant_mask_rows": 0,
        "pre_last_user_supervised_rows": 0,
        "missing_supervised_eos_rows": 0,
        "missing_final_assistant_eos_rows": 0,
        "serialization_mismatch_rows": 0,
        "tool_response_supervised_rows": 0,
        "tool_call_outside_assistant_mask_rows": 0,
        "assistant_target_policy": "last_user_suffix",
        "chat_template_sha256": config["model"]["chat_template_sha256"],
        "tokenizer_revision": config["model"]["revision"],
        "transformers_version": transformers.__version__,
        "model_snapshot": model_report,
        "raw_samples_in_report": False,
        "training_promotion_allowed": False,
    }
    parser_report = {
        "schema_version": "1.0.0",
        "status": "passed_internal_kanana_xml_roundtrip",
        "structured_tool_calls_roundtripped": tool_calls,
        "function_name_errors": 0,
        "required_argument_errors": 0,
        "json_value_errors": 0,
        "duplicate_tool_tag_errors": 0,
        "suffix_text_errors": 0,
        "primary_serving_engine": config["serving"]["primary_engine"],
        "primary_serving_version": config["serving"]["primary_version"],
        "primary_parser": config["serving"]["primary_tool_call_parser"],
        "primary_runtime_installed_in_training_env": (
            importlib.util.find_spec("sglang") is not None
        ),
        "secondary_parser": config["serving"]["secondary_tool_call_parser"],
        "secondary_is_promotion_gate": False,
        "raw_samples_in_report": False,
    }
    return report, token_rows, parser_report


def _identity(
    build_root: Path,
    tokenizer_path: Path,
    config_path: Path,
) -> dict[str, Any]:
    inputs = {
        "private_build_manifest_sha256": _sha256_file(
            build_root / "build_manifest.json"
        ),
        "config_sha256": _sha256_file(config_path),
        "tokenizer_json_sha256": _sha256_file(tokenizer_path / "tokenizer.json"),
        "tokenizer_config_sha256": _sha256_file(
            tokenizer_path / "tokenizer_config.json"
        ),
        "chat_template_sha256": _sha256_file(tokenizer_path / "chat_template.jinja"),
        "dataset_loader_sha256": _sha256_file(
            REPO_ROOT / "scripts/training/phase5_v3_dataset.py"
        ),
        "preflight_implementation_sha256": _sha256_file(Path(__file__)),
    }
    digest = _sha256_json(inputs)
    return {
        "preflight_inputs": inputs,
        "preflight_sha256": digest,
        "preflight_id": f"preflight-{digest[:12]}",
    }


def _output_base(path: Path | None, default: Path, expected: str, mode: int) -> Path:
    candidate = (path or default).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    if candidate.name != expected:
        raise Phase5V3PreflightError(
            f"preflight output base 이름은 {expected!r}이어야 합니다."
        )
    if not candidate.exists():
        candidate.mkdir(parents=True, mode=mode)
    return _safe_directory(candidate, "preflight output base")


def _build_report(
    destination: Path,
    *,
    identity: Mapping[str, Any],
    token_report: Mapping[str, Any],
    parser_report: Mapping[str, Any],
    token_rows: Sequence[Mapping[str, Any]] | None,
    public: bool,
) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        return _verify_existing(destination, str(identity["preflight_id"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    mode = PUBLIC_FILE_MODE if public else PRIVATE_FILE_MODE
    dir_mode = PUBLIC_DIR_MODE if public else PRIVATE_DIR_MODE
    temporary.chmod(dir_mode)
    try:
        _write_json(temporary / "tokenization_report.json", token_report, mode)
        _write_json(temporary / "tool_roundtrip_report.json", parser_report, mode)
        if token_rows is not None:
            _write_jsonl(temporary / "token_rows.jsonl", token_rows, mode)
        artifacts = _artifact_hashes(temporary)
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "mix20k_v3_non_training_preflight",
            "preflight_id": identity["preflight_id"],
            "preflight_sha256": identity["preflight_sha256"],
            "generated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "preflight_inputs": identity["preflight_inputs"],
            "artifact_sha256": artifacts,
            "status": token_report["status"],
            "public_aggregate_only": public,
            "training_method_called": False,
            "backward_performed": False,
            "optimizer_step_performed": False,
            "phase5_training_performed": False,
            "training_promotion_allowed": False,
        }
        _write_json(temporary / "build_manifest.json", manifest, mode)
        for directory in [temporary, *temporary.rglob("*")]:
            if directory.is_dir():
                directory.chmod(dir_mode)
        os.replace(temporary, destination)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def run(
    build_root: Path,
    tokenizer_path: Path,
    config_path: Path,
    *,
    private_base: Path | None,
    public_base: Path | None,
) -> dict[str, Any]:
    build = _safe_directory(build_root, "private repaired build")
    tokenizer = _safe_directory(tokenizer_path, "Kanana tokenizer snapshot")
    token_report, token_rows, parser_report = analyze(build, tokenizer, config_path)
    identity = _identity(build, tokenizer, config_path)
    private_default = (
        REPO_ROOT / "data/derived/saju_1b_baseline/mix20k-v3.0.1-preflight/v1.0.0"
    )
    public_default = (
        REPO_ROOT / "data/reports/saju_1b_baseline/mix20k-v3-preflight/v1.0.0"
    )
    private_root = _output_base(
        private_base, private_default, "v1.0.0", PRIVATE_DIR_MODE
    ) / str(identity["preflight_id"])
    public_root = _output_base(
        public_base, public_default, "v1.0.0", PUBLIC_DIR_MODE
    ) / str(identity["preflight_id"])
    private_manifest = _build_report(
        private_root,
        identity=identity,
        token_report=token_report,
        parser_report=parser_report,
        token_rows=token_rows,
        public=False,
    )
    public_manifest = _build_report(
        public_root,
        identity=identity,
        token_report=token_report,
        parser_report=parser_report,
        token_rows=None,
        public=True,
    )
    return {
        "schema_version": "1.0.0",
        "status": token_report["status"],
        "preflight_id": identity["preflight_id"],
        "private_root": str(private_root),
        "public_root": str(public_root),
        "private_manifest_sha256": _sha256_file(private_root / "build_manifest.json"),
        "public_manifest_sha256": _sha256_file(public_root / "build_manifest.json"),
        "private_artifacts": len(private_manifest["artifact_sha256"]),
        "public_artifacts": len(public_manifest["artifact_sha256"]),
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX20K-v3.0.1을 학습 없이 정확한 Kanana tokenizer로 검증한다."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    analyze_command = commands.add_parser("analyze")
    run_command = commands.add_parser("run")
    for command in (analyze_command, run_command):
        command.add_argument("--build-root", type=Path, required=True)
        command.add_argument("--tokenizer-path", type=Path, required=True)
    run_command.add_argument("--private-base", type=Path)
    run_command.add_argument("--public-base", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config_path = arguments.config
    if not config_path.is_absolute():
        config_path = (REPO_ROOT / config_path).resolve()
    try:
        if arguments.command == "analyze":
            token_report, _, parser_report = analyze(
                arguments.build_root,
                arguments.tokenizer_path,
                config_path,
            )
            result = {
                "tokenization": token_report,
                "tool_roundtrip": parser_report,
                "training_promotion_allowed": False,
                "phase5_training_performed": False,
            }
        else:
            result = run(
                arguments.build_root,
                arguments.tokenizer_path,
                config_path,
                private_base=arguments.private_base,
                public_base=arguments.public_base,
            )
    except (Mix20KV3Error, Phase5V3DatasetError, Phase5V3PreflightError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
