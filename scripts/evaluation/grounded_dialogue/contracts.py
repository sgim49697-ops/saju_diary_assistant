# contracts.py - grounded dialogue 진단의 고정 입력, build ID, 불변 출력 계약을 검증한다.

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .errors import ArtifactError, GroundedDialogueError

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = Path("configs/evaluation/grounded_dialogue_eval-v0.1.0.json")
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
PUBLIC_DIR_MODE = 0o755
PUBLIC_FILE_MODE = 0o644
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
BUILD_ID_PATTERN = re.compile(r"^eval-[0-9a-f]{12}$")

EXPECTED_ARMS = (
    ("R0_KI20_ORACLE_768", "KI20", "oracle", 768),
    ("R1_KI20_ORACLE_2048", "KI20", "oracle", 2048),
    ("R2_K0_ORACLE_2048", "K0", "oracle", 2048),
    ("R3_KI20_RULE_2048", "KI20", "rule", 2048),
    ("R4_KI20_MODEL_NARROW_2048", "KI20", "model_narrow", 2048),
)
EXPECTED_CONTRASTS = {
    "input_budget_effect": ["R0_KI20_ORACLE_768", "R1_KI20_ORACLE_2048"],
    "finetuning_effect": ["R2_K0_ORACLE_2048", "R1_KI20_ORACLE_2048"],
    "rule_end_to_end_penalty": [
        "R1_KI20_ORACLE_2048",
        "R3_KI20_RULE_2048",
    ],
    "model_narrow_end_to_end_penalty": [
        "R1_KI20_ORACLE_2048",
        "R4_KI20_MODEL_NARROW_2048",
    ],
    "extractor_comparison": [
        "R3_KI20_RULE_2048",
        "R4_KI20_MODEL_NARROW_2048",
    ],
}
EXPECTED_STRATA = [
    "no_birth_information",
    "date_only_no_time",
    "ambiguous_time",
    "calendar_ambiguity",
    "timezone_location_ambiguity",
    "accumulated_context_no_reask",
    "time_unknown_partial_limit",
    "complete_input_runtime_handoff",
    "false_ui_or_completion",
    "structured_chart_ready",
]
EXPECTED_IMPLEMENTATION_FILES = [
    "scripts/evaluation/grounded_dialogue/__init__.py",
    "scripts/evaluation/grounded_dialogue/__main__.py",
    "scripts/evaluation/grounded_dialogue/backends.py",
    "scripts/evaluation/grounded_dialogue/cases.py",
    "scripts/evaluation/grounded_dialogue/contracts.py",
    "scripts/evaluation/grounded_dialogue/errors.py",
    "scripts/evaluation/grounded_dialogue/extractors.py",
    "scripts/evaluation/grounded_dialogue/graders.py",
    "scripts/evaluation/grounded_dialogue/harness.py",
    "scripts/evaluation/grounded_dialogue/reporting.py",
    "scripts/evaluation/grounded_dialogue/runner.py",
    "tests/test_grounded_dialogue_eval.py",
]
PUBLIC_FORBIDDEN_KEYS = {
    "case_id",
    "messages",
    "output",
    "raw_output",
    "private_root",
    "prompt",
    "source_path",
    "tool_result",
}
PUBLIC_FORBIDDEN_MARKERS = (
    "runs/",
    "data/raw/",
    "dev_cases.jsonl",
    "model.safetensors",
    "<runtime_context>",
)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GroundedDialogueError(f"JSON 중복 key를 허용하지 않습니다: {key}")
        result[key] = value
    return result


def strict_loads(payload: str, label: str) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, GroundedDialogueError):
            raise
        raise GroundedDialogueError(f"{label} JSON을 읽지 못했습니다.") from exc


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactError(f"파일 hash를 계산하지 못했습니다: {path}") from exc
    return digest.hexdigest()


def exact_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or FULL_SHA_PATTERN.fullmatch(value) is None:
        raise GroundedDialogueError(f"{label} SHA-256이 올바르지 않습니다.")
    return value


def safe_path(repo_root: Path, relative: str) -> Path:
    value = Path(relative)
    if value.is_absolute() or not value.parts or ".." in value.parts:
        raise GroundedDialogueError(f"저장소 상대경로가 올바르지 않습니다: {relative}")
    root = repo_root.resolve()
    resolved = (repo_root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GroundedDialogueError(f"경로가 저장소를 벗어납니다: {relative}") from exc
    current = repo_root
    for part in value.parts:
        current /= part
        if current.is_symlink():
            raise GroundedDialogueError(f"symlink 경로를 허용하지 않습니다: {relative}")
    return resolved


def load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ArtifactError(f"{label} 파일이 없습니다: {path}")
    try:
        value = strict_loads(path.read_text(encoding="utf-8"), label)
    except (OSError, UnicodeError) as exc:
        raise ArtifactError(f"{label} 파일을 읽지 못했습니다: {path}") from exc
    if not isinstance(value, dict):
        raise GroundedDialogueError(f"{label} 최상위 값은 object여야 합니다.")
    return value


def read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise ArtifactError(f"{label} 파일이 없습니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith("\n") or not line.strip():
                    raise GroundedDialogueError(
                        f"{label} {line_number}행이 비었거나 newline으로 끝나지 않습니다."
                    )
                value = strict_loads(line, f"{label} {line_number}행")
                if not isinstance(value, dict):
                    raise GroundedDialogueError(
                        f"{label} {line_number}행은 object여야 합니다."
                    )
                rows.append(value)
    except (OSError, UnicodeError) as exc:
        raise ArtifactError(f"{label} JSONL을 읽지 못했습니다: {path}") from exc
    return rows


def _assert_hashed_file(repo_root: Path, item: Mapping[str, Any], label: str) -> Path:
    path = safe_path(repo_root, str(item.get("path", "")))
    expected = exact_sha(item.get("sha256"), f"{label}.sha256")
    if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
        raise ArtifactError(f"{label} 파일 hash가 다릅니다: {path}")
    return path


def _validate_arms(config: Mapping[str, Any]) -> None:
    arms = config.get("arms")
    if not isinstance(arms, list) or len(arms) != len(EXPECTED_ARMS):
        raise GroundedDialogueError("진단 arm 수가 다릅니다.")
    actual = tuple(
        (
            arm.get("arm_id"),
            arm.get("model_id"),
            arm.get("slot_extractor_id"),
            arm.get("max_input_tokens"),
        )
        for arm in arms
        if isinstance(arm, Mapping)
    )
    if actual != EXPECTED_ARMS or len(actual) != len(arms):
        raise GroundedDialogueError("arm 축이 한 변수 대조 계약과 다릅니다.")
    if config.get("contrasts") != EXPECTED_CONTRASTS:
        raise GroundedDialogueError("arm contrast가 고정 대조 계약과 다릅니다.")


def validate_contract(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "0.1.0"
        or config.get("evaluation_id") != "grounded-dialogue-eval"
        or config.get("evaluation_version") != "v0.1.0"
        or config.get("status") != "implemented_not_executed"
        or config.get("seed") != 42
    ):
        raise GroundedDialogueError("진단 정본·버전·상태 계약이 다릅니다.")
    source = config.get("source_suite")
    if not isinstance(source, Mapping) or (
        source.get("rows") != 100
        or source.get("user_turns") != 120
        or source.get("strata") != EXPECTED_STRATA
        or source.get("provenance") != "public_synthetic_private_nonsealed"
        or source.get("file_mode") != "0600"
        or source.get("path")
        != "runs/KI20-MIX-v2/stateful-chat-gate/v1.0.0/"
        "stateful-gate-f5b76dde1921/dev_cases.jsonl"
    ):
        raise GroundedDialogueError("고정 공개합성 suite 계약이 다릅니다.")
    safe_path(repo_root, str(source.get("path", "")))
    exact_sha(source.get("sha256"), "source_suite.sha256")
    _validate_arms(config)

    runtime = config.get("runtime_inputs")
    if not isinstance(runtime, Mapping) or (
        runtime.get("simulated_ready_fixture") is not True
        or runtime.get("candidate_fact_authority") != "HARD_CANDIDATE"
        or runtime.get("candidate_result_inserted_into_app_fsm") is not False
    ):
        raise GroundedDialogueError("FSM·candidate runtime 경계 계약이 다릅니다.")
    for name in (
        "intake_fsm",
        "intake_gate",
        "session_schema",
        "candidate_registry",
        "candidate_conformance",
        "ephemeris",
    ):
        item = runtime.get(name)
        if not isinstance(item, Mapping):
            raise GroundedDialogueError(f"runtime_inputs.{name} 계약이 없습니다.")
        safe_path(repo_root, str(item.get("path", "")))
        exact_sha(item.get("sha256"), f"runtime_inputs.{name}.sha256")
    expected_runtime_paths = {
        "intake_fsm": "configs/runtime/intake_fsm-v1.1.0.json",
        "intake_gate": "configs/runtime/intake_fsm_gate-v1.1.0.json",
        "session_schema": "configs/runtime/session_state_schema_v2.1.0.json",
        "candidate_registry": "configs/runtime/calculation/registry-v1.3.0.json",
        "candidate_conformance": "data/reports/saju_runtime_conformance/v1.6.0/"
        "build-8bd88d6db03a/aggregate.json",
        "ephemeris": "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp",
    }
    if any(runtime[name].get("path") != path for name, path in expected_runtime_paths.items()):
        raise GroundedDialogueError("runtime 고정 입력 경로가 다릅니다.")

    prompt = config.get("system_prompt")
    if (
        not isinstance(prompt, Mapping)
        or prompt.get("same_for_every_arm") is not True
        or prompt.get("path") != "configs/runtime/production_system_prompt_v1.txt"
    ):
        raise GroundedDialogueError("모든 arm의 production prompt 고정 계약이 다릅니다.")
    safe_path(repo_root, str(prompt.get("path", "")))
    exact_sha(prompt.get("sha256"), "system_prompt.sha256")

    models = config.get("models")
    if not isinstance(models, Mapping) or set(models) != {"K0", "KI20"}:
        raise GroundedDialogueError("모델 집합은 K0·KI20이어야 합니다.")
    expected_model_files = {
        "model.safetensors",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
    }
    for name, model in models.items():
        if not isinstance(model, Mapping):
            raise GroundedDialogueError(f"models.{name} 계약이 object가 아닙니다.")
        safe_path(repo_root, str(model.get("path", "")))
        required = model.get("required_files")
        if not isinstance(required, Mapping) or set(required) != expected_model_files:
            raise GroundedDialogueError(f"models.{name} 필수 파일 집합이 다릅니다.")
        for filename, digest in required.items():
            if Path(filename).name != filename:
                raise GroundedDialogueError(f"models.{name} 파일명이 올바르지 않습니다.")
            exact_sha(digest, f"models.{name}.{filename}")
    if models["K0"].get("path") != (
        "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
        "bf4786aa2a1908adce942d53976270132732f720"
    ) or models["KI20"].get("path") != (
        "runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67/final"
    ):
        raise GroundedDialogueError("K0·KI20 고정 모델 경로가 다릅니다.")

    extraction = config.get("slot_extraction")
    narrow = extraction.get("model_narrow") if isinstance(extraction, Mapping) else None
    if not isinstance(narrow, Mapping) or (
        extraction.get("oracle_is_diagnostic_only") is not True
        or extraction.get("rule_fixed_suite_required_percent") != 100.0
        or narrow.get("model_id") != "KI20"
        or narrow.get("strict_json") is not True
        or narrow.get("attempts") != 1
        or narrow.get("retries") != 0
        or narrow.get("max_new_tokens") != 160
        or narrow.get("may_emit_actions_or_runtime_fields") is not False
        or narrow.get("metrics_are_diagnostic_only") is not True
    ):
        raise GroundedDialogueError("슬롯 추출 비교 계약이 다릅니다.")

    generation = config.get("generation")
    if not isinstance(generation, Mapping) or generation != {
        "confirmation_variable": "GROUNDED_DIALOGUE_EVAL",
        "confirmation_value": "K0_KI20_V1",
        "expected_gpu_count": 1,
        "require_no_other_compute_processes": True,
        "minimum_free_gpu_memory_mib": 12000,
        "local_files_only": True,
        "trust_remote_code": True,
        "fix_mistral_regex": False,
        "dtype": "bfloat16",
        "attention_backend": "sdpa",
        "do_sample": False,
        "num_beams": 1,
        "batch_size": 4,
        "max_new_tokens": 256,
        "native_context_tokens": 4096,
        "truncation": "drop_oldest_complete_user_assistant_pairs_only",
    }:
        raise GroundedDialogueError("greedy 생성·컨텍스트 계약이 다릅니다.")
    if max(arm[3] for arm in EXPECTED_ARMS) + generation["max_new_tokens"] > 4096:
        raise GroundedDialogueError("입력·출력 예산이 native context를 넘습니다.")

    if config.get("automatic_metrics") != {
        "fabricated_pillars_maximum": 0,
        "unknown_hour_violations_maximum": 0,
        "fact_contradictions_maximum": 0,
        "false_completion_maximum": 0,
        "provided_field_reask_percent_maximum": 5.0,
        "severe_safety_maximum": 0,
        "nonempty_percent_minimum": 100.0,
    }:
        raise GroundedDialogueError("자동 지표 목표 계약이 다릅니다.")
    governance = config.get("governance")
    if not isinstance(governance, Mapping) or governance != {
        "diagnostic_only": True,
        "completion_independent_of_target": True,
        "semantics": "not_measured",
        "naturalness": "not_measured",
        "human_gate": False,
        "sealed_blind_access": False,
        "training_execution_allowed": False,
        "promotion_allowed": False,
        "release_approval_allowed": False,
        "application_binding_allowed": False,
        "public_raw_output_allowed": False,
    }:
        raise GroundedDialogueError("진단 권한·금지 경계가 다릅니다.")
    outputs = config.get("outputs")
    if not isinstance(outputs, Mapping) or (
        outputs.get("private_root")
        != "runs/GROUNDED-DIALOGUE/v0.1.0/{evaluation_build_id}"
        or outputs.get("public_root")
        != "data/reports/saju_1b_baseline/grounded-dialogue/v0.1.0/{evaluation_build_id}"
        or outputs.get("private_directory_mode") != "0700"
        or outputs.get("private_file_mode") != "0600"
    ):
        raise GroundedDialogueError("진단 출력 경로·권한 계약이 다릅니다.")

    implementation = config.get("implementation_files")
    if implementation != EXPECTED_IMPLEMENTATION_FILES:
        raise GroundedDialogueError("구현 fingerprint 파일 목록이 다릅니다.")
    for relative in implementation:
        path = safe_path(repo_root, relative)
        if not path.is_file() or path.is_symlink():
            raise GroundedDialogueError(f"구현 fingerprint 파일이 없습니다: {relative}")
    return {
        "status": "valid",
        "evaluation_version": "v0.1.0",
        "arms": [item[0] for item in EXPECTED_ARMS],
        "cases": 100,
        "sealed_blind_accessed": False,
        "gpu_execution_performed": False,
    }


def validate_local_artifacts(config: Mapping[str, Any], repo_root: Path) -> dict[str, Any]:
    source_path = _assert_hashed_file(repo_root, config["source_suite"], "source_suite")
    if stat.S_IMODE(source_path.stat().st_mode) != PRIVATE_FILE_MODE:
        raise ArtifactError("source suite 파일 mode는 0600이어야 합니다.")
    for name, item in config["runtime_inputs"].items():
        if isinstance(item, Mapping) and "path" in item:
            _assert_hashed_file(repo_root, item, f"runtime_inputs.{name}")
    conformance = load_json(
        safe_path(repo_root, config["runtime_inputs"]["candidate_conformance"]["path"]),
        "candidate conformance",
    )
    if conformance.get("status") != config["runtime_inputs"]["candidate_conformance"][
        "expected_status"
    ]:
        raise ArtifactError("candidate runtime conformance 상태가 다릅니다.")
    _assert_hashed_file(repo_root, config["system_prompt"], "system_prompt")
    for model_name, model in config["models"].items():
        root = safe_path(repo_root, model["path"])
        if root.is_symlink() or not root.is_dir():
            raise ArtifactError(f"모델 디렉터리가 없습니다: {model_name}")
        for filename, expected in model["required_files"].items():
            path = root / filename
            if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
                raise ArtifactError(f"모델 파일 hash가 다릅니다: {model_name}/{filename}")
    return {
        "status": "ready",
        "source_rows": config["source_suite"]["rows"],
        "models": sorted(config["models"]),
        "candidate_runtime_release_approved": False,
    }


def prepare_context(
    repo_root: Path,
    config_path: Path,
    *,
    require_local_artifacts: bool,
) -> dict[str, Any]:
    config = load_json(config_path, "grounded dialogue config")
    validate_contract(config, repo_root)
    artifacts = (
        validate_local_artifacts(config, repo_root)
        if require_local_artifacts
        else {"status": "not_checked"}
    )
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "schema_version": config["schema_version"],
        "evaluation_id": config["evaluation_id"],
        "evaluation_version": config["evaluation_version"],
        "seed": config["seed"],
        "source_suite": config["source_suite"],
        "runtime_inputs": config["runtime_inputs"],
        "system_prompt": config["system_prompt"],
        "models": config["models"],
        "arms": config["arms"],
        "contrasts": config["contrasts"],
        "slot_extraction": config["slot_extraction"],
        "generation": config["generation"],
        "automatic_metrics": config["automatic_metrics"],
        "governance": config["governance"],
        "implementation_hashes": implementation_hashes,
    }
    build_sha256 = sha256_json(build_inputs)
    evaluation_build_id = f"eval-{build_sha256[:12]}"
    if BUILD_ID_PATTERN.fullmatch(evaluation_build_id) is None:
        raise GroundedDialogueError("evaluation build ID 형식이 다릅니다.")
    outputs = config["outputs"]
    return {
        "config": config,
        "config_path": config_path,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "evaluation_build_id": evaluation_build_id,
        "private_root": safe_path(
            repo_root,
            outputs["private_root"].format(evaluation_build_id=evaluation_build_id),
        ),
        "public_root": safe_path(
            repo_root,
            outputs["public_root"].format(evaluation_build_id=evaluation_build_id),
        ),
        "artifact_validation": artifacts,
    }


def atomic_write(path: Path, payload: bytes, *, mode: int, exclusive: bool) -> None:
    directory_mode = PRIVATE_DIR_MODE if mode == PRIVATE_FILE_MODE else PUBLIC_DIR_MODE
    path.parent.mkdir(parents=True, exist_ok=True, mode=directory_mode)
    path.parent.chmod(directory_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if exclusive and path.exists():
            raise ArtifactError(f"불변 파일을 덮어쓸 수 없습니다: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_once(path: Path, payload: bytes, *, mode: int) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ArtifactError(f"기존 불변 파일의 내용이 다릅니다: {path}")
        if stat.S_IMODE(path.stat().st_mode) != mode:
            raise ArtifactError(f"기존 불변 파일 mode가 다릅니다: {path}")
        return
    atomic_write(path, payload, mode=mode, exclusive=True)


def jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def public_leak_scan(value: Any) -> None:
    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            forbidden = PUBLIC_FORBIDDEN_KEYS & set(item)
            if forbidden:
                raise ArtifactError(f"공개 산출물 금지 key가 있습니다: {sorted(forbidden)}")
            for key, nested in item.items():
                walk(key)
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)
        elif isinstance(item, str) and any(
            marker in item for marker in PUBLIC_FORBIDDEN_MARKERS
        ):
            raise ArtifactError("공개 산출물에 private·원문 경로 문자열이 있습니다.")

    walk(value)
