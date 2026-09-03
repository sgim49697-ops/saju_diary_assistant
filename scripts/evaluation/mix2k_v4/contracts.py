# contracts.py - MIX2K v4 dev200·5-arm·출력 artifact 계약을 검증한다.

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    flatten_runtime_facts,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    validate_runtime_binding,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_ROOT / "configs/evaluation/mix2k-v4-lora-eval-v1.0.0.json"
DEFAULT_SPEC_BUILD = Path(
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/specs/"
    "v1.0.1/build-da9014c5f24a"
)
EXPECTED_DEV_AXES = {
    "schema_literacy": 40,
    "natal_explanation": 30,
    "natal_and_today": 50,
    "followup": 40,
    "state_tool": 20,
    "general_empathy": 20,
}
EXPECTED_ARMS = (
    ("K0", "fixed_base", None),
    ("LORA_R8", "lora_adapter", 8),
    ("LORA_R16", "lora_adapter", 16),
    ("LORA_R32", "lora_adapter", 32),
    ("KI20", "fixed_comparator", None),
)
EXPECTED_METRICS = [
    "schema_field_accuracy",
    "natal_period_label_confusion",
    "unsupported_fact_rate",
    "provided_fact_omission_rate",
    "natural_explanation_preference",
    "followup_evidence_consistency",
    "general_conversation_retention",
    "repetitive_template_response_rate",
    "false_saju_injection",
    "reask_rate",
]
EXPECTED_DEPENDENCY_CONTRACTS = {
    "dataset_config": {
        "path": "configs/data_versions/saju_1b_baseline/mix2k-v4-chart-day-8k-v1.0.1.json",
        "sha256": "c8267ec438e1bebe46670553a846fa81db371d23c5004a3a3c2aeecafe440f1c",
    },
    "lora_config": {
        "path": "configs/model_versions/saju_1b_baseline/mix2k-v4-lora-v1.0.1.json",
        "sha256": "d7c5db056be927319617ac4b932acb9e37d9f9a2e6478598d20f2b7ce12fa728",
    },
    "bound_prompt": {
        "path": "configs/chat_prompts/saju_bound_chart_v2.txt",
        "sha256": "55bdcec6bdf7fa6a91fb68b03cd4a296c705ab9bac0e77abb067190519cc8f90",
    },
    "model_projection": {
        "path": "scripts/runtime/chart_day_model_projection.py",
        "sha256": "0c080e76ba7afff5b8d54bce41dc207f5701c235bb7863308bc565576afe9011",
    },
}
KANANA_REMOTE_CODE_HASHES = {
    "configuration_kanana2_tiny.py": (
        "191fb6fbfd63968cc24b3beeb8190aaa88868d4cf1695f8c5a379fb0a077d79d"
    ),
    "modeling_kanana2_tiny.py": (
        "e47cd8cc99e71fc69eea9bf5ba1221526fb8c6d4fc8677177e82de997b766500"
    ),
}
EXPECTED_MODEL_CONTRACTS = {
    "k0": {
        "repository": "kakaocorp/kanana-2-1.3b-instruct",
        "revision": "bf4786aa2a1908adce942d53976270132732f720",
        "relative_path": (
            "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
            "bf4786aa2a1908adce942d53976270132732f720"
        ),
        "required_file_sha256": {
            "chat_template.jinja": (
                "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3"
            ),
            "config.json": (
                "fe14b20b4b616d62ca0682312c2fcd2b90d9a836d14a1ff6448db3f533fd15a1"
            ),
            "model.safetensors": (
                "49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942"
            ),
            **KANANA_REMOTE_CODE_HASHES,
            "tokenizer.json": (
                "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab"
            ),
            "tokenizer_config.json": (
                "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5"
            ),
        },
    },
    "ki20": {
        "run_id": "KI20-MIX-v2",
        "revision": "run-1f5d732cae67",
        "relative_path": "runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67/final",
        "required_file_sha256": {
            "chat_template.jinja": (
                "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3"
            ),
            "config.json": (
                "e02848aa87656f5b33faee0d7354b26c1ff579a60042aa37c7ba204445cf76b7"
            ),
            **KANANA_REMOTE_CODE_HASHES,
            "model.safetensors": (
                "2fae23e28471c07d7db0c338bc6370493191722180ecc502de7e1e1d5fe5872d"
            ),
            "special_tokens_map.json": (
                "5931724161899da68f7ba2f903b4f30b1423e96726a73f9b7274271a27a0afb4"
            ),
            "tokenizer.json": (
                "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab"
            ),
            "tokenizer_config.json": (
                "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5"
            ),
        },
    },
}
EXPECTED_GENERATION = {
    "do_sample": False,
    "num_beams": 1,
    "num_beam_groups": 1,
    "num_return_sequences": 1,
    "max_input_tokens": 4096,
    "max_new_tokens": 4096,
    "min_new_tokens": 0,
    "native_context_tokens_minimum": 8192,
    "batch_size": 1,
    "use_cache": True,
    "bos_token_id": 128000,
    "eos_token_id": [128010],
    "pad_token_id": 128001,
    "return_dict_in_generate": False,
    "output_scores": False,
    "renormalize_logits": False,
    "remove_invalid_values": False,
    "fix_mistral_regex": True,
    "retry_or_grounding_rewrite_allowed": False,
    "same_runtime_snapshot": True,
    "same_system_prompt": True,
    "same_generation_config": True,
    "system_prompt_profile": "bound_chart_v2",
    "system_prompt_sha256": (
        "55bdcec6bdf7fa6a91fb68b03cd4a296c705ab9bac0e77abb067190519cc8f90"
    ),
    "model_projection_id": "saju-chart-day-model-projection-v1.0.0",
    "ignore_model_directory_generation_config": True,
}
EXPECTED_QUALITY_REVIEW = {
    "required": True,
    "blind_arm_labels": True,
    "providers": ["claude", "codex"],
    "minimum_reviews_per_case": 2,
    "score_minimum": 1,
    "score_maximum": 5,
    "cases_per_call": 2,
    "timeout_seconds": 600,
    "provider_contracts": {
        "claude": {
            "cli": "claude",
            "cli_version": "2.1.259 (Claude Code)",
            "model": "claude-sonnet-5",
            "auth": "claude.ai_subscription",
        },
        "codex": {
            "cli": "codex",
            "cli_version": "codex-cli 0.150.1",
            "model": "gpt-5.6-sol",
            "auth": "chatgpt_subscription",
        },
    },
    "external_transmission": {
        "candidate_outputs_transmitted": True,
        "public_synthetic_inputs_only": True,
        "pii_and_restricted_preflight_required": True,
        "raw_provider_envelopes_persisted": False,
        "explicit_operator_approval_required": True,
        "approval_not_persisted_in_config": True,
        "heuristic_scan_cannot_exclude_memorization": True,
        "provider_tool_access_disabled": True,
    },
    "dimensions": [
        "natural_explanation",
        "task_fulfillment",
        "followup_quality",
        "general_conversation_retention",
    ],
}
EXPECTED_RELEASE = {
    "primary_arm": "LORA_R16",
    "loss_only_selection_forbidden": True,
    "actual_regression_is_release_blocker": True,
    "all_regression_turns_must_pass": True,
    "regression_review_score_minimum": 4,
    "metric_thresholds": {
        "schema_field_accuracy_minimum": 0.98,
        "natal_period_label_confusion_maximum": 0.0,
        "unsupported_fact_rate_maximum": 0.01,
        "provided_fact_omission_rate_maximum": 0.05,
        "natural_explanation_mean_score_minimum": 3.5,
        "task_fulfillment_mean_score_minimum": 3.5,
        "task_fulfillment_minimum_score_minimum": 3.0,
        "followup_evidence_consistency_minimum": 0.95,
        "followup_review_mean_minimum": 3.5,
        "general_conversation_retention_minimum": 0.95,
        "general_conversation_review_mean_minimum": 3.5,
        "repetitive_template_response_rate_maximum": 0.05,
        "false_saju_injection_maximum": 0.0,
        "reask_rate_maximum": 0.0,
    },
    "k0_noninferiority": {
        "deterministic_turn_pass_rate_margin": 0.02,
        "natural_explanation_mean_score_margin": 0.25,
        "general_conversation_retention_margin": 0.02,
        "general_conversation_review_mean_margin": 0.25,
        "followup_review_mean_margin": 0.25,
    },
    "serving_contract": {
        "required_prompt_profile": "bound_chart_v2",
        "current_dashboard_prompt_profile": "bound_chart_v1",
        "prompt_upgrade_completed": False,
        "must_pass_before_production_release": True,
    },
    "automatic_production_promotion_allowed": False,
}
EXPECTED_OUTPUTS = {
    "private_root": (
        "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/evaluation/v1.0.0"
    ),
    "public_root": ("data/reports/saju_1b_baseline/mix2k-v4-lora-evaluation/v1.0.0"),
}
REGRESSION_ID = "actual-chart-day-label-confusion-20260902"
DEV_FIELDS = {
    "schema_version",
    "case_id",
    "axis",
    "messages",
    "followup_turns",
    "runtime_binding",
    "expected_structural_facts",
    "forbidden_claims",
    "minimum_substantive_sentences",
    "minimum_substantive_nonempty_lines",
    "regression_release_blocker",
    "teacher_target_access_allowed",
    "training_eligible",
    "provenance",
}
MESSAGE_FIELDS = {"role", "content"}
MAX_JSON_BYTES = 64 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")


class Mix2KV4EvaluationError(RuntimeError):
    """동결 dev·모델·평가 artifact 계약 위반."""


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4EvaluationError(
                f"{label} 경로에 symlink component가 있습니다."
            )


def validate_directory(path: Path, label: str) -> None:
    reject_symlink_components(path, label)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise Mix2KV4EvaluationError(f"{label} 경로가 없거나 안전하지 않습니다.")


def ensure_directory(path: Path, label: str, *, mode: int = PRIVATE_DIR_MODE) -> None:
    if not path.is_absolute():
        raise Mix2KV4EvaluationError(f"{label}은 절대경로여야 합니다.")
    reject_symlink_components(path, label)
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise Mix2KV4EvaluationError(f"{label} 경로가 안전하지 않습니다.")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    reject_symlink_components(path, label)
    path.chmod(mode)


def load_json(path: Path, label: str) -> dict[str, Any]:
    reject_symlink_components(path, label)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_JSON_BYTES
    ):
        raise Mix2KV4EvaluationError(f"{label}이 없거나 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4EvaluationError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4EvaluationError(f"{label} 최상위는 object여야 합니다.")
    return value


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def atomic_write(path: Path, payload: bytes, mode: int = PRIVATE_FILE_MODE) -> None:
    if not path.is_absolute():
        raise Mix2KV4EvaluationError("평가 output file은 절대경로여야 합니다.")
    reject_symlink_components(path, "평가 output file")
    ensure_directory(path.parent, "평가 output parent")
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise Mix2KV4EvaluationError("기존 평가 output file이 안전하지 않습니다.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _assert_hashed_dependencies(config: Mapping[str, Any]) -> None:
    dependencies = config.get("dependency_contracts")
    if not isinstance(dependencies, Mapping) or set(dependencies) != {
        "dataset_config",
        "lora_config",
        "bound_prompt",
        "model_projection",
    }:
        raise Mix2KV4EvaluationError("평가 dependency 계약이 다릅니다.")
    for name, item in dependencies.items():
        if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
            raise Mix2KV4EvaluationError(f"평가 dependency 형식이 다릅니다: {name}")
        relative = Path(str(item["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise Mix2KV4EvaluationError(
                f"평가 dependency 경로가 안전하지 않습니다: {name}"
            )
        path = REPO_ROOT / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != item["sha256"]
        ):
            raise Mix2KV4EvaluationError(f"평가 dependency hash가 다릅니다: {name}")


def validate_config(config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "MIX2K v4 evaluation config")
    arms = config.get("arms")
    actual_arms = (
        tuple(
            (item.get("arm_id"), item.get("kind"), item.get("rank"))
            for item in arms
            if isinstance(item, Mapping)
        )
        if isinstance(arms, list)
        else ()
    )
    source = config.get("source_suite")
    generation = config.get("generation")
    review = config.get("quality_review")
    release = config.get("release")
    governance = config.get("governance")
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("evaluation_id") != "K0-MIX2K-V4-LORA-DEV200"
        or config.get("evaluation_version") != "v1.0.0"
        or config.get("dataset_version") != DATASET_VERSION
        or not isinstance(source, Mapping)
        or source.get("build_id") != "build-da9014c5f24a"
        or source.get("build_sha256")
        != "da9014c5f24a6ffc239cd8bf1ec64d2ba50855caff6ec90438d5a41a4fefd980"
        or source.get("sha256")
        != "7ff700be25c3eaa27401be89afb7eeda6bba4a9c27ef3451d7853a9fd8d8a629"
        or source.get("rows") != 200
        or source.get("axes") != EXPECTED_DEV_AXES
        or source.get("required_regression_id") != REGRESSION_ID
        or source.get("frozen_before_teacher_generation") is not True
        or source.get("teacher_targets_present") is not False
        or source.get("training_eligible") is not False
        or actual_arms != EXPECTED_ARMS
        or config.get("metrics") != EXPECTED_METRICS
        or config.get("dependency_contracts") != EXPECTED_DEPENDENCY_CONTRACTS
        or config.get("model_contracts") != EXPECTED_MODEL_CONTRACTS
        or generation != EXPECTED_GENERATION
        or review != EXPECTED_QUALITY_REVIEW
        or release != EXPECTED_RELEASE
        or not isinstance(governance, Mapping)
        or governance
        != {
            "public_synthetic_only": True,
            "aihub_content_accessed": False,
            "personal_data_accessed": False,
            "sealed_blind_accessed": False,
            "development_targets_accessed": False,
            "model_training_performed_by_evaluator": False,
            "production_promotion_allowed": False,
        }
    ):
        raise Mix2KV4EvaluationError("MIX2K v4 evaluation 고정 계약이 다릅니다.")
    repetition = config.get("repetition")
    if repetition != {
        "normalized_cross_case_multiplicity_maximum": 2,
        "minimum_ngram_size": 6,
        "within_response_repeated_ngram_ratio": 0.35,
    }:
        raise Mix2KV4EvaluationError("평가 repetition 계약이 다릅니다.")
    operational = config.get("operational_limits")
    if operational != {
        "expected_gpu_count": 1,
        "max_total_gpu_memory_used_mib": 16384,
        "min_free_gpu_memory_before_start_mib": 12000,
        "require_no_active_compute_process_before_start": True,
        "run_arms_sequentially": True,
    }:
        raise Mix2KV4EvaluationError("평가 GPU 운영 계약이 다릅니다.")
    outputs = config.get("outputs")
    if outputs != EXPECTED_OUTPUTS:
        raise Mix2KV4EvaluationError("평가 output 계약이 다릅니다.")
    _assert_hashed_dependencies(config)
    return config


def _expected_from_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    value = binding["value"]
    chart = value["chart"]["hard_facts"]
    period = value["period"]["hard_facts"]["period"]
    return {
        "natal_pillars": {
            name: chart["pillars"][name]["ganzhi"]
            for name in ("year", "month", "day", "hour")
        },
        "day_master": chart["day_master"]["stem"],
        "target_date": period["target_date"],
        "period_year_ganzhi": period["year_ganzhi"],
        "period_month_ganzhi": period["month_ganzhi"],
        "period_day_ganzhi": period["day_ganzhi"],
    }


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or len(messages) != 2:
        raise Mix2KV4EvaluationError(
            "dev case는 system+user 두 message로 시작해야 합니다."
        )
    if [item.get("role") for item in messages if isinstance(item, Mapping)] != [
        "system",
        "user",
    ]:
        raise Mix2KV4EvaluationError("dev case message role이 다릅니다.")
    for item in messages:
        if (
            not isinstance(item, Mapping)
            or set(item) != MESSAGE_FIELDS
            or not isinstance(item.get("content"), str)
            or not item["content"].strip()
        ):
            raise Mix2KV4EvaluationError("dev case message 형식이 다릅니다.")


def validate_dev_cases(
    spec_build: Path, config: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    reject_symlink_components(spec_build, "동결 spec build")
    if (
        not spec_build.is_absolute()
        or spec_build.is_symlink()
        or not spec_build.is_dir()
    ):
        raise Mix2KV4EvaluationError("동결 spec build가 없거나 안전하지 않습니다.")
    manifest = load_json(spec_build / "build_manifest.json", "동결 spec manifest")
    source = config["source_suite"]
    relative = source["path"]
    path = spec_build / relative
    if (
        manifest.get("build_id") != source["build_id"]
        or manifest.get("build_sha256") != source["build_sha256"]
        or manifest.get("development_frozen_before_teacher_generation") is not True
        or manifest.get("teacher_target_access_allowed") is not False
        or manifest.get("training_execution_allowed") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("artifact_sha256", {}).get(relative) != source["sha256"]
        or path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != source["sha256"]
    ):
        raise Mix2KV4EvaluationError("동결 dev build identity가 다릅니다.")
    rows = read_jsonl(path)
    seen: set[str] = set()
    axes: Counter[str] = Counter()
    regression_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != DEV_FIELDS:
            raise Mix2KV4EvaluationError("dev case field 집합이 다릅니다.")
        case_id = row.get("case_id")
        axis = row.get("axis")
        _validate_messages(row.get("messages"))
        followups = row.get("followup_turns")
        binding = row.get("runtime_binding")
        expected = row.get("expected_structural_facts")
        if (
            row.get("schema_version") != "1.0.0"
            or not isinstance(case_id, str)
            or CASE_ID_PATTERN.fullmatch(case_id) is None
            or case_id in seen
            or axis not in EXPECTED_DEV_AXES
            or not isinstance(followups, list)
            or any(
                not isinstance(value, str) or not value.strip() for value in followups
            )
            or row.get("minimum_substantive_sentences") != 3
            or row.get("minimum_substantive_nonempty_lines") != 3
            or row.get("teacher_target_access_allowed") is not False
            or row.get("training_eligible") is not False
            or row.get("provenance") != "public_synthetic_runtime_v1.5"
        ):
            raise Mix2KV4EvaluationError(f"dev case identity가 다릅니다: {case_id}")
        if axis == "followup" and len(followups) != 1:
            raise Mix2KV4EvaluationError(
                "followup dev는 후속 질문이 정확히 하나여야 합니다."
            )
        if axis != "followup" and case_id != REGRESSION_ID and followups:
            raise Mix2KV4EvaluationError(
                "비-followup dev에 예기치 않은 후속 질문이 있습니다."
            )
        if axis in {
            "schema_literacy",
            "natal_explanation",
            "natal_and_today",
            "followup",
        }:
            validate_runtime_binding(binding, require_day=True)
            if expected != _expected_from_binding(binding):
                raise Mix2KV4EvaluationError(
                    f"dev expected fact가 binding과 다릅니다: {case_id}"
                )
        elif binding is not None or expected is not None:
            raise Mix2KV4EvaluationError(
                "일반·state dev에는 runtime fact가 없어야 합니다."
            )
        if bool(row.get("regression_release_blocker")) != (case_id == REGRESSION_ID):
            raise Mix2KV4EvaluationError("필수 regression flag가 다릅니다.")
        if case_id == REGRESSION_ID:
            regression_count += 1
            if expected != {
                "natal_pillars": {
                    "year": "戊辰",
                    "month": "甲子",
                    "day": "乙丑",
                    "hour": "壬午",
                },
                "day_master": "乙",
                "target_date": "2026-09-02",
                "period_year_ganzhi": "丙午",
                "period_month_ganzhi": "丙申",
                "period_day_ganzhi": "己卯",
            }:
                raise Mix2KV4EvaluationError("실제 regression fact가 다릅니다.")
        seen.add(case_id)
        axes[axis] += 1
    if len(rows) != 200 or dict(axes) != EXPECTED_DEV_AXES or regression_count != 1:
        raise Mix2KV4EvaluationError("동결 dev 수량·axis·regression 계약이 다릅니다.")
    return manifest, rows


def spec_for_structural_validator(case: Mapping[str, Any]) -> dict[str, Any]:
    binding = case.get("runtime_binding")
    flattened = flatten_runtime_facts(binding["value"]) if binding is not None else []
    axis = {
        "schema_literacy": "structured_fact_schema_literacy",
        "natal_explanation": "chart_facts_natural_explanation",
        "natal_and_today": "chart_day_today_flow",
        "followup": "followup_explain_grounding",
        "state_tool": "intake_state_correction",
        "general_empathy": "general_korean_empathy",
    }[str(case["axis"])]
    return {
        "task_axis": axis,
        "allowed_fact_paths": [path for path, _ in flattened],
        "allowed_fact_values": [value for _, value in flattened],
    }


def validate_model_files(
    root: Path, contract: Mapping[str, Any], label: str
) -> dict[str, str]:
    reject_symlink_components(root, label)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise Mix2KV4EvaluationError(f"{label} 경로가 없거나 안전하지 않습니다.")
    expected = contract.get("required_file_sha256")
    if not isinstance(expected, Mapping) or not expected:
        raise Mix2KV4EvaluationError(f"{label} file hash 계약이 비었습니다.")
    observed: dict[str, str] = {}
    for name, digest in expected.items():
        path = root / name
        if (
            not isinstance(name, str)
            or SHA256_PATTERN.fullmatch(str(digest)) is None
            or path.is_symlink()
            or not path.is_file()
        ):
            raise Mix2KV4EvaluationError(
                f"{label} 파일이 없거나 안전하지 않습니다: {name}"
            )
        observed[name] = sha256_file(path)
        if observed[name] != digest:
            raise Mix2KV4EvaluationError(f"{label} file hash가 다릅니다: {name}")
    return observed


def case_set_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(
        json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_SPEC_BUILD",
    "EXPECTED_ARMS",
    "EXPECTED_DEV_AXES",
    "EXPECTED_METRICS",
    "REGRESSION_ID",
    "Mix2KV4EvaluationError",
    "absolute",
    "atomic_write",
    "case_set_sha256",
    "ensure_directory",
    "json_bytes",
    "load_json",
    "reject_symlink_components",
    "spec_for_structural_validator",
    "validate_config",
    "validate_dev_cases",
    "validate_directory",
    "validate_model_files",
]
