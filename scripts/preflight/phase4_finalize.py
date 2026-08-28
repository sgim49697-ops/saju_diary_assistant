# phase4_finalize.py - Phase 4A~E 통과 산출물을 canonical manifest와 공개 기술 보고서로 승격한다.

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    load_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    utc_now,
    verify_hash_map,
    write_json_once,
)
from scripts.preflight.phase4_review import verify_preflight
from scripts.preflight.phase4_smoke import ALL_STAGES, verify_all_smoke

PUBLIC_FILE_MODE = 0o644
PUBLIC_FILES = (
    "canonical_manifest_report.json",
    "phase4_completion_report.json",
    "smoke_summary.json",
)


def _artifact_names(context: dict[str, Any]) -> dict[str, str]:
    return {
        "core_eval": "core_eval_200.jsonl",
        "source_holdout": "source_holdout_500.jsonl",
        "mix1k_candidate": "mix1k_candidate_v1.jsonl",
        "mix10k_candidate": "mix10k_candidate_v1.jsonl",
        "mix20k_candidate": "mix20k_candidate_v1.jsonl",
        "canonical_mix1k": "mix1k_smoke_v1.jsonl",
        "canonical_mix10k": "mix10k_v1.jsonl",
        "canonical_mix20k": "mix20k_v1.jsonl",
        **context["config"].get("artifacts", {}),
    }


def _canonical_files(context: dict[str, Any]) -> tuple[str, ...]:
    names = _artifact_names(context)
    return (
        f"eval/{names['core_eval']}",
        f"eval/{names['source_holdout']}",
        f"manifests/{names['canonical_mix1k']}",
        f"manifests/{names['canonical_mix10k']}",
        f"manifests/{names['canonical_mix20k']}",
    )


def _finalization_inputs(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    public_root: Path = context["public_root"]
    k0_root: Path = context["k0_root"]
    smoke_root: Path = context["smoke_root"]
    names = _artifact_names(context)
    candidate_names = tuple(
        names[key]
        for key in ("mix1k_candidate", "mix10k_candidate", "mix20k_candidate")
    )
    return {
        "schema_version": "1.0.0",
        "canonical_plan_version": context["config"]["canonical_plan_version"],
        "preflight_version": context["config"]["preflight_version"],
        "parent_preflight_build_id": context["build_id"],
        "parent_preflight_build_sha256": context["build_sha256"],
        "parent_private_manifest_sha256": sha256_file(
            private_root / "build_manifest.json"
        ),
        "parent_public_manifest_sha256": sha256_file(
            public_root / "build_manifest.json"
        ),
        "k0_manifest_sha256": sha256_file(k0_root / "run_manifest.json"),
        "triage_manifest_sha256": sha256_file(k0_root / "triage_manifest.json"),
        "candidate_manifest_sha256": {
            name: sha256_file(private_root / "manifests" / name)
            for name in candidate_names
        },
        "smoke_stage_manifest_sha256": {
            stage: sha256_file(smoke_root / "stages" / stage / "stage_manifest.json")
            for stage in ALL_STAGES
        },
        "selected_max_length": 768,
        "promotion_basis": "explicit_user_authorization_after_automated_risk_triage_and_phase4_de",
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "phase5_training_performed": False,
    }


def canonical_identity(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    inputs = _finalization_inputs(context, repo_root)
    build_sha256 = sha256_json(inputs)
    return {
        "build_id": f"build-{build_sha256[:12]}",
        "build_sha256": build_sha256,
        "build_inputs": inputs,
    }


def _canonical_paths(
    context: dict[str, Any], identity: dict[str, Any]
) -> tuple[Path, Path]:
    return (
        context["canonical_root"] / identity["build_id"],
        context["canonical_public_root"] / identity["build_id"],
    )


def _copy_regular_file(source: Path, destination: Path, *, mode: int) -> None:
    if source.is_symlink() or not source.is_file():
        raise Phase4Error(f"승격 원본이 regular file이 아닙니다: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def _canonical_manifest_report(root: Path, context: dict[str, Any]) -> dict[str, Any]:
    artifacts = _artifact_names(context)
    names = {
        "mix1k": f"manifests/{artifacts['canonical_mix1k']}",
        "mix10k": f"manifests/{artifacts['canonical_mix10k']}",
        "mix20k": f"manifests/{artifacts['canonical_mix20k']}",
    }
    rows = {
        key: read_jsonl(root / relative, f"canonical {key}")
        for key, relative in names.items()
    }
    expected = {"mix1k": 1_000, "mix10k": 10_000, "mix20k": 20_000}
    counts = {key: len(value) for key, value in rows.items()}
    if counts != expected:
        raise Phase4Error(f"canonical manifest 수량이 다릅니다: {counts}")
    ids = {key: {value.get("id") for value in values} for key, values in rows.items()}
    if not ids["mix1k"] < ids["mix10k"] or not ids["mix10k"] < ids["mix20k"]:
        raise Phase4Error("canonical MIX1K⊂MIX10K⊂MIX20K가 성립하지 않습니다.")
    by_id = {
        key: {value["id"]: value["record_sha256"] for value in values}
        for key, values in rows.items()
    }
    for child, parent in (("mix1k", "mix10k"), ("mix10k", "mix20k")):
        if any(
            by_id[parent].get(key) != digest for key, digest in by_id[child].items()
        ):
            raise Phase4Error(
                "canonical subset의 record hash가 상위 manifest와 다릅니다."
            )
    return {
        "schema_version": "1.0.0",
        "status": "verified",
        "selected_max_length": 768,
        "manifest_counts": counts,
        "mix1k_subset_mix10k": True,
        "mix10k_subset_mix20k": True,
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
    }


def _safe_smoke_summary(context: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for stage in ALL_STAGES:
        summary = load_json(
            context["smoke_root"] / "stages" / stage / "summary.json",
            f"smoke {stage} summary",
        )
        values[stage] = {
            key: summary.get(key)
            for key in (
                "status",
                "max_length",
                "optimizer_steps",
                "train_begin_global_step",
                "first_pre_optimizer_global_step",
                "resumed_from_checkpoint",
                "training_loss",
                "loss_report",
                "gradient_probe",
                "optimizer_probe",
                "runtime",
                "peak_vram_bytes",
                "vram_total_bytes",
                "vram_free_bytes_at_finish",
                "vram_headroom_requirement_bytes",
                "system_ram",
                "elapsed_seconds",
                "failure_class",
                "task_count",
                "nonempty_outputs",
            )
            if key in summary
        }
    return {
        "schema_version": "1.0.0",
        "report_type": "phase4_de_public_smoke_summary",
        "selected_max_length": 768,
        "stages": values,
        "raw_samples_in_summary": False,
        "phase5_training_performed": False,
    }


def _completion_report(
    context: dict[str, Any],
    identity: dict[str, Any],
    canonical_report: dict[str, Any],
    smoke_summary: dict[str, Any],
) -> dict[str, Any]:
    triage = load_json(context["k0_root"] / "triage_summary.json", "K0 triage")
    k0 = load_json(context["k0_root"] / "summary.json", "K0 summary")
    return {
        "schema_version": "1.0.0",
        "report_type": "phase4_abcde_completion",
        "build_id": identity["build_id"],
        "build_sha256": identity["build_sha256"],
        "generated_at": utc_now(),
        "phase_status": "완료",
        "status": "gates_a_b_c_d_e_passed",
        "completed_gates": ["A", "B", "C", "D", "E"],
        "parent_preflight_build_id": context["build_id"],
        "parent_staging": context["config"]["parent_staging"],
        "model": {
            key: context["config"]["model"][key]
            for key in (
                "repo_id",
                "revision",
                "phase3_build_id",
                "dtype",
                "attention_backend",
            )
        },
        "selected_max_length": 768,
        "diagnostic_max_length": 1024,
        "diagnostic_1024_status": smoke_summary["stages"]["diagnostic_1024_1"][
            "status"
        ],
        "canonical_manifests": canonical_report,
        "k0": {
            "gate_c_passed": k0["gate_c_passed"],
            "generation_cases": k0["generation_cases"],
            "cross_build_reused_cases": k0["cross_build_reused_cases"],
            "locally_generated_cases": k0["locally_generated_cases"],
            "safety_violations": k0["safety_violations"],
            "determinism_replay_passed": k0["determinism_replay_passed"],
        },
        "automated_risk_triage": {
            "evaluation_items": triage["evaluation_items"],
            "severity_counts": triage["severity_counts"],
            "signal_counts": triage["signal_counts"],
            "priority_items": triage["priority_items"],
            "critical_or_high_items": triage["critical_or_high_items"],
            "automated_second_pass_performed": True,
        },
        "gate_d": smoke_summary["stages"]["gate_d_512_1"],
        "gate_e": {
            "smoke_512_20": smoke_summary["stages"]["smoke_512_20"],
            "main_768_100": smoke_summary["stages"]["main_768_100"],
            "resume_768_200": smoke_summary["stages"]["resume_768_200"],
            "reload_768_generate5": smoke_summary["stages"]["reload_768_generate5"],
        },
        "promotion_basis": identity["build_inputs"]["promotion_basis"],
        "technical_full_ft_preflight_passed": True,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "canonical_promotion_performed": True,
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
        "official_sources": context["config"]["official_sources"],
        "notes": [
            "자동 위험 분류는 사람 전문 판독이나 품질 인증을 뜻하지 않는다.",
            "K0의 비안전 품질 지표는 진단값이며 Gate C 차단 임계값으로 소급 사용하지 않았다.",
            "1024는 실제 후보 최대 길이보다 긴 패딩 진단이고, 정식 Full FT 길이는 768로 선택했다.",
            "Phase 5의 10K·20K 실제 학습은 실행하지 않았다.",
        ],
    }


def finalize_phase4(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_preflight(context, repo_root)
    verify_all_smoke(context, repo_root)
    identity = canonical_identity(context, repo_root)
    canonical_root, public_root = _canonical_paths(context, identity)
    if canonical_root.exists() or public_root.exists():
        if not canonical_root.exists() or not public_root.exists():
            raise Phase4Error("canonical private/public 중 한쪽만 존재합니다.")
        return {
            **verify_finalized_phase4(context, repo_root),
            "mode": "reused",
            "writes_performed": False,
        }

    canonical_root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    canonical_temp = Path(
        tempfile.mkdtemp(prefix=f".{identity['build_id']}-", dir=canonical_root.parent)
    )
    public_root.parent.mkdir(parents=True, exist_ok=True)
    public_temp = Path(
        tempfile.mkdtemp(prefix=f".{identity['build_id']}-", dir=public_root.parent)
    )
    canonical_promoted = False
    public_promoted = False
    try:
        names = _artifact_names(context)
        copies = {
            f"eval/{names['core_eval']}": f"eval/{names['core_eval']}",
            f"eval/{names['source_holdout']}": f"eval/{names['source_holdout']}",
            f"manifests/{names['mix1k_candidate']}": f"manifests/{names['canonical_mix1k']}",
            f"manifests/{names['mix10k_candidate']}": f"manifests/{names['canonical_mix10k']}",
            f"manifests/{names['mix20k_candidate']}": f"manifests/{names['canonical_mix20k']}",
        }
        for source, destination in copies.items():
            _copy_regular_file(
                context["private_root"] / source,
                canonical_temp / destination,
                mode=PRIVATE_FILE_MODE,
            )
        for directory in [
            canonical_temp,
            *[path for path in canonical_temp.rglob("*") if path.is_dir()],
        ]:
            directory.chmod(PRIVATE_DIR_MODE)
        canonical_report = _canonical_manifest_report(canonical_temp, context)
        canonical_manifest = {
            "schema_version": "1.0.0",
            "report_type": "phase4_canonical_derived_manifest",
            **identity,
            "artifact_sha256": artifact_hash_map(
                canonical_temp, list(_canonical_files(context))
            ),
            "status": "approved_for_phase5_training",
            "selected_max_length": 768,
            "technical_full_ft_preflight_passed": True,
            "human_domain_review_performed": False,
            "quality_certification_claimed": False,
            "training_promotion_allowed": True,
            "phase5_training_performed": False,
        }
        write_json_once(
            canonical_temp / "build_manifest.json",
            canonical_manifest,
            mode=PRIVATE_FILE_MODE,
        )

        smoke_summary = _safe_smoke_summary(context)
        completion = _completion_report(
            context, identity, canonical_report, smoke_summary
        )
        write_json_once(
            public_temp / "canonical_manifest_report.json",
            canonical_report,
            mode=PUBLIC_FILE_MODE,
        )
        write_json_once(
            public_temp / "smoke_summary.json",
            smoke_summary,
            mode=PUBLIC_FILE_MODE,
        )
        write_json_once(
            public_temp / "phase4_completion_report.json",
            completion,
            mode=PUBLIC_FILE_MODE,
        )
        public_manifest = {
            "schema_version": "1.0.0",
            "report_type": "phase4_completion_public_manifest",
            **identity,
            "artifact_sha256": artifact_hash_map(public_temp, list(PUBLIC_FILES)),
            "status": completion["status"],
            "training_promotion_allowed": True,
            "phase5_training_performed": False,
        }
        write_json_once(
            public_temp / "build_manifest.json",
            public_manifest,
            mode=PUBLIC_FILE_MODE,
        )
        os.replace(canonical_temp, canonical_root)
        canonical_promoted = True
        os.replace(public_temp, public_root)
        public_promoted = True
    finally:
        if not canonical_promoted:
            shutil.rmtree(canonical_temp, ignore_errors=True)
        if not public_promoted:
            shutil.rmtree(public_temp, ignore_errors=True)
    return {
        **verify_finalized_phase4(context, repo_root),
        "mode": "promoted",
        "writes_performed": True,
    }


def verify_finalized_phase4(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verify_preflight(context, repo_root)
    verify_all_smoke(context, repo_root)
    identity = canonical_identity(context, repo_root)
    canonical_root, public_root = _canonical_paths(context, identity)
    if (
        canonical_root.is_symlink()
        or not canonical_root.is_dir()
        or public_root.is_symlink()
        or not public_root.is_dir()
    ):
        raise Phase4Error("Phase 4 canonical private/public build가 없습니다.")
    canonical = load_json(
        canonical_root / "build_manifest.json", "canonical build manifest"
    )
    public = load_json(
        public_root / "build_manifest.json", "completion public manifest"
    )
    completion = load_json(
        public_root / "phase4_completion_report.json", "Phase 4 completion report"
    )
    if (
        canonical.get("build_id") != identity["build_id"]
        or canonical.get("build_sha256") != identity["build_sha256"]
        or canonical.get("build_inputs") != identity["build_inputs"]
        or public.get("build_inputs") != identity["build_inputs"]
        or completion.get("completed_gates") != ["A", "B", "C", "D", "E"]
        or canonical.get("training_promotion_allowed") is not True
        or public.get("training_promotion_allowed") is not True
        or completion.get("training_promotion_allowed") is not True
        or completion.get("phase5_training_performed") is not False
        or completion.get("human_domain_review_performed") is not False
        or completion.get("quality_certification_claimed") is not False
    ):
        raise Phase4Error("Phase 4 canonical identity/Gate 계약이 다릅니다.")
    verify_hash_map(canonical_root, canonical.get("artifact_sha256"), "canonical")
    verify_hash_map(public_root, public.get("artifact_sha256"), "completion public")
    report = _canonical_manifest_report(canonical_root, context)
    for relative in (*_canonical_files(context), "build_manifest.json"):
        path = canonical_root / relative
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase4Error(
                f"canonical private 파일 권한이 0600이 아닙니다: {relative}"
            )
    return {
        "status": "verified_phase4_complete",
        "build_id": identity["build_id"],
        "build_sha256": identity["build_sha256"],
        "selected_max_length": report["selected_max_length"],
        "canonical_root": canonical_root.relative_to(repo_root).as_posix(),
        "public_root": public_root.relative_to(repo_root).as_posix(),
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
        "human_domain_review_performed": False,
    }


def registry_entry(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    verified = verify_finalized_phase4(context, repo_root)
    identity = canonical_identity(context, repo_root)
    canonical_root, public_root = _canonical_paths(context, identity)
    return {
        "version": context["config"]["preflight_version"],
        "build_id": identity["build_id"],
        "build_sha256": identity["build_sha256"],
        "parent_preflight_build_id": context["build_id"],
        "parent_staging_build_id": context["config"]["parent_staging"]["build_id"],
        "private_manifest_sha256": sha256_file(canonical_root / "build_manifest.json"),
        "public_manifest_sha256": sha256_file(public_root / "build_manifest.json"),
        "selected_max_length": verified["selected_max_length"],
        "technical_full_ft_preflight_passed": True,
        "human_domain_review_performed": False,
        "quality_certification_claimed": False,
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
        "status": "approved_for_phase5_training",
    }
