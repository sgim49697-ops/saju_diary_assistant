# phase5_dashboard_v1_14_candidate.py - 비활성 v1.14 serving 후보 계약을 검증한다.

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import sha256_file

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.14.0-candidate.json"
)
DEFAULT_MODEL = REPO_ROOT / (
    "models/saju_1b_baseline/kanana-2-1.3b-instruct/"
    "bf4786aa2a1908adce942d53976270132732f720"
)
MAX_JSON_BYTES = 256 * 1024


class Phase5DashboardV114CandidateError(RuntimeError):
    """비활성 serving 후보·부모 dashboard·K0 context 계약 위반."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_JSON_BYTES
    ):
        raise Phase5DashboardV114CandidateError(
            f"{label}이 없거나 안전하지 않습니다: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase5DashboardV114CandidateError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Phase5DashboardV114CandidateError(f"{label} 최상위는 object여야 합니다.")
    return value


def validate_candidate(config_path: Path, model_path: Path | None) -> dict[str, Any]:
    config = _load_json(config_path, "v1.14 candidate config")
    parent = config.get("parent")
    feature = config.get("feature_gate")
    generation = config.get("generation")
    prompts = config.get("prompt_profiles")
    runtime = config.get("runtime_contract")
    experiment = config.get("experiment")
    governance = config.get("governance")
    if not all(
        isinstance(value, Mapping)
        for value in (
            parent,
            feature,
            generation,
            prompts,
            runtime,
            experiment,
            governance,
        )
    ):
        raise Phase5DashboardV114CandidateError("v1.14 candidate section이 없습니다.")
    parent_path = REPO_ROOT / str(parent.get("path", ""))
    data_config_path = REPO_ROOT / str(experiment.get("data_config_path", ""))
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("candidate_dashboard_version") != "v1.14.0"
        or config.get("status") != "INACTIVE_EXPERIMENT_CANDIDATE"
        or sha256_file(parent_path) != parent.get("sha256")
        or feature
        != {
            "enabled_by_default": False,
            "active_dashboard_config_changed": False,
            "explicit_candidate_command_required": True,
        }
        or generation
        != {
            "do_sample": False,
            "num_beams": 1,
            "max_input_tokens": 4096,
            "max_new_tokens": 4096,
            "native_context_tokens_minimum": 8192,
            "truncate_current_user_message": False,
        }
        or prompts.get("default_profile") != "guided_runtime_v2"
        or prompts.get("bound_profile") != "bound_chart_v2"
        or runtime
        != {
            "training_serving_prompt_parity_required": True,
            "full_runtime_snapshot_preserved": True,
            "compact_projection_used_for_bound_chart": False,
            "intake_projection_is_app_owned": True,
            "relation_training_scope_expanded": False,
            "period_year_month_day_labels_required": True,
        }
        or sha256_file(data_config_path) != experiment.get("data_config_sha256")
        or experiment.get("primary_arm") != "LORA_R16"
        or experiment.get("comparators") != ["K0", "KI20"]
        or experiment.get("adapter_available") is not False
        or governance
        != {
            "diagnostic_only": True,
            "production_promotion_allowed": False,
            "active_v1_13_unchanged": True,
            "aihub_content_allowed": False,
            "personal_data_allowed": False,
        }
    ):
        raise Phase5DashboardV114CandidateError("v1.14 candidate 고정 계약이 다릅니다.")
    parent_config = _load_json(parent_path, "active v1.13 config")
    if (
        parent_config.get("schema_version") != "1.13.0"
        or parent_config.get("model_check", {})
        .get("generation", {})
        .get("max_new_tokens")
        != 256
        or parent_config.get("manual_session", {}).get("max_context_tokens") != 3584
        or parent_config.get("prompt_profiles", {}).get("bound_profile")
        != "bound_chart_v1"
    ):
        raise Phase5DashboardV114CandidateError(
            "active v1.13 보존 기준이 달라졌습니다. 별도 승격 검토가 필요합니다."
        )
    profile_map = prompts.get("profiles")
    if not isinstance(profile_map, Mapping) or set(profile_map) != {
        "guided_runtime_v2",
        "bound_chart_v2",
    }:
        raise Phase5DashboardV114CandidateError("v1.14 prompt profile 집합이 다릅니다.")
    prompt_hashes: dict[str, str] = {}
    for profile_id, profile in profile_map.items():
        if not isinstance(profile, Mapping):
            raise Phase5DashboardV114CandidateError("prompt profile 형식이 다릅니다.")
        path = REPO_ROOT / str(profile.get("path", ""))
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != profile.get("bytes")
            or sha256_file(path) != profile.get("sha256")
        ):
            raise Phase5DashboardV114CandidateError(
                f"{profile_id} prompt identity가 다릅니다."
            )
        prompt_hashes[str(profile_id)] = str(profile["sha256"])
    native_context = None
    if model_path is not None:
        model_config = _load_json(model_path / "config.json", "K0 model config")
        native_context = model_config.get("max_position_embeddings")
        if (
            isinstance(native_context, bool)
            or not isinstance(native_context, int)
            or native_context < generation["native_context_tokens_minimum"]
            or generation["max_input_tokens"] + generation["max_new_tokens"]
            > native_context
        ):
            raise Phase5DashboardV114CandidateError(
                "K0 native context가 input 4K + output 4K 후보를 수용하지 못합니다."
            )
    return {
        "status": "valid_inactive_candidate",
        "candidate_dashboard_version": "v1.14.0",
        "active_dashboard_version": "v1.13.0",
        "active_dashboard_changed": False,
        "feature_enabled_by_default": False,
        "max_input_tokens": 4096,
        "max_new_tokens": 4096,
        "native_context_tokens": native_context,
        "prompt_sha256": prompt_hashes,
        "adapter_available": False,
        "production_promotion_allowed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dashboard v1.14 비활성 후보 검증")
    parser.add_argument("command", choices=("validate-contract",))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = validate_candidate(
            _absolute(args.config),
            _absolute(args.model),
        )
    except (OSError, ValueError, Phase5DashboardV114CandidateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
