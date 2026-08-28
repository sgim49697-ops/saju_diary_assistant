# phase4_review.py - K0 공개 집계 보고서와 제한 데이터 오프라인 검수 ZIP을 만든다.

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    canonical_json_bytes,
    load_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    sha256_json,
    utc_now,
    verify_hash_map,
    write_bytes_once,
    write_json_once,
)
from scripts.preflight.phase4_data import verify_private_build
from scripts.preflight.phase4_k0 import verify_k0_run
from scripts.preflight.phase4_triage import verify_triage

PUBLIC_FILE_MODE = 0o644
ZIP_TIMESTAMP = (2026, 8, 28, 0, 0, 0)
REVIEW_FILES = (
    "START_HERE.html",
    "review.css",
    "review.js",
    "review-data.js",
    "REVIEW_GUIDE.md",
    "DATA_USAGE_NOTICE.md",
    "PACKAGE_MANIFEST.json",
    "SHA256SUMS.txt",
)
PUBLIC_ARTIFACTS = (
    "schema_validation.json",
    "split_leakage_report.json",
    "tokenization_report.json",
    "manifest_report.json",
    "k0_summary.json",
    "triage_summary.json",
    "preflight_report.json",
)
SHA_LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")


def _asset_root(repo_root: Path) -> Path:
    return repo_root / "scripts/preflight/review_assets"


def _review_identity(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "package_type": "phase4_k0_offline_review",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "model_revision": context["config"]["model"]["revision"],
        "core_eval_items": 200,
        "source_holdout_items": 500,
        "generation_cases": 720,
    }


def _load_review_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    private_root: Path = context["private_root"]
    k0_root: Path = context["k0_root"]
    eval_items = [
        *read_jsonl(private_root / "eval/core_eval_200.jsonl", "Core Eval"),
        *read_jsonl(private_root / "eval/source_holdout_500.jsonl", "source holdout"),
    ]
    results = read_jsonl(k0_root / "results.jsonl", "K0 results")
    result_by_case: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        item = load_json(k0_root / result["item_path"], "K0 raw item")
        key = (str(item.get("eval_id")), str(item.get("case_id")))
        if key in result_by_case:
            raise Phase4Error("K0 검수 투영 case가 중복됐습니다.")
        result_by_case[key] = item

    identity = _review_identity(context)
    package_id = f"review-{sha256_json(identity)[:16]}"
    projected: list[dict[str, Any]] = []
    seen_case_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(eval_items, 1):
        review_cases: list[dict[str, Any]] = []
        for case_index, case in enumerate(item["cases"], 1):
            key = (item["eval_id"], case["case_id"])
            raw = result_by_case.get(key)
            if raw is None:
                raise Phase4Error("K0 결과와 eval case가 일치하지 않습니다.")
            seen_case_keys.add(key)
            review_cases.append(
                {
                    "review_case": case_index,
                    "prompt_messages": raw["prompt_messages"],
                    "reference_assistant": raw.get("reference_assistant"),
                    "model_output": raw["output"],
                    "metrics": raw["metrics"],
                    "generated_tokens": raw["generated_tokens"],
                    "finished_with_eos": raw["finished_with_eos"],
                }
            )
        projected.append(
            {
                "review_id": f"R{index:04d}-{sha256_json({'package_id': package_id, 'eval_id': item['eval_id']})[:10]}",
                "split": "source_holdout" if item["category"] == "source_holdout" else "core_eval",
                "category": item["category"],
                "hardness": item["hardness"],
                "source_axis": item.get("source_axis"),
                "automated_contract": item["automated_contract"],
                "cases": review_cases,
            }
        )
    if len(projected) != 700 or len(seen_case_keys) != 720 or len(result_by_case) != 720:
        raise Phase4Error("검수 패키지 투영 수량이 700항목/720case가 아닙니다.")
    return projected


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o100600 & 0xFFFF) << 16
    return info


def _build_review_payloads(
    context: dict[str, Any], repo_root: Path, items: list[dict[str, Any]]
) -> tuple[dict[str, bytes], dict[str, Any]]:
    identity = _review_identity(context)
    package_id = f"review-{sha256_json(identity)[:16]}"
    data = {
        **identity,
        "package_id": package_id,
        "human_domain_review_performed": False,
        "items": items,
    }
    asset_root = _asset_root(repo_root)
    payloads: dict[str, bytes] = {}
    for name in ("START_HERE.html", "review.css", "review.js"):
        path = asset_root / name
        if path.is_symlink() or not path.is_file():
            raise Phase4Error(f"오프라인 검수 asset이 없습니다: {name}")
        payloads[name] = path.read_bytes()
    payloads["review-data.js"] = (
        b"window.PHASE4_REVIEW_DATA = " + canonical_json_bytes(data) + b";\n"
    )
    payloads["REVIEW_GUIDE.md"] = (
        "# Phase 4 K0 오프라인 검수 안내\n\n"
        "1. ZIP을 승인된 로컬 장치에서 풀고 `START_HERE.html`을 엽니다.\n"
        "2. 자동 판정은 참고값입니다. 출력의 사실 근거, 안전 문구, 한국어 자연스러움을 직접 확인합니다.\n"
        "3. 각 항목을 `통과`, `수정 필요`, `차단` 중 하나로 판정하고 메모를 남깁니다.\n"
        "4. 중간에는 checkpoint JSON, 완료 시 final JSON을 내려받아 별도 보관합니다.\n"
        "5. 이 검수 결과는 자동으로 학습 승인을 바꾸지 않습니다. Phase 4D/E 이후 별도 승격이 필요합니다.\n"
    ).encode()
    payloads["DATA_USAGE_NOTICE.md"] = (
        "# 제한 데이터 이용 고지\n\n"
        "이 패키지는 AI Hub 승인 범위 데이터에서 파생한 문장과 비공개 평가 출력을 포함할 수 있습니다. "
        "승인된 검수자만 로컬에서 사용하고, 원문·출력·검수 파일을 공개 저장소나 미승인 제3자에게 제공하지 마세요. "
        "Git에 추가하거나 외부 웹 서비스에 업로드하지 말고, 검수가 끝나면 조직의 보존·삭제 정책을 따르세요.\n"
    ).encode()
    content_hashes = {name: sha256_bytes(payload) for name, payload in sorted(payloads.items())}
    manifest = {
        **identity,
        "package_id": package_id,
        "item_count": len(items),
        "case_count": sum(len(item["cases"]) for item in items),
        "category_counts": dict(sorted(Counter(item["category"] for item in items).items())),
        "content_sha256": content_hashes,
        "contains_restricted_source_text": True,
        "authorized_local_review_only": True,
        "identifiers_and_source_locators_removed": True,
        "human_domain_review_performed": False,
    }
    payloads["PACKAGE_MANIFEST.json"] = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    checksums = "".join(
        f"{sha256_bytes(payloads[name])}  {name}\n" for name in sorted(payloads)
    )
    payloads["SHA256SUMS.txt"] = checksums.encode("utf-8")
    if tuple(sorted(payloads)) != tuple(sorted(REVIEW_FILES)):
        raise Phase4Error("검수 ZIP 고정 파일 집합이 다릅니다.")
    return payloads, manifest


def _write_review_zip(output: Path, payloads: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
        os.close(descriptor)
        temporary = Path(name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for member in sorted(payloads):
                archive.writestr(_zip_info(member), payloads[member])
        temporary.chmod(PRIVATE_FILE_MODE)
        if output.exists():
            raise Phase4Error(f"검수 ZIP을 덮어쓸 수 없습니다: {output}")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    names: list[str] = []
    total = 0
    for info in infos:
        path = PurePosixPath(info.filename)
        if (
            info.filename != path.as_posix()
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or info.is_dir()
            or stat.S_ISLNK(info.external_attr >> 16)
            or info.file_size > 100 * 1024 * 1024
            or info.compress_size > 100 * 1024 * 1024
        ):
            raise Phase4Error(f"검수 ZIP member가 안전하지 않습니다: {info.filename}")
        total += info.file_size
        names.append(info.filename)
    if total > 200 * 1024 * 1024 or len(names) != len(set(names)):
        raise Phase4Error("검수 ZIP 크기 또는 중복 member가 안전하지 않습니다.")
    if tuple(sorted(names)) != tuple(sorted(REVIEW_FILES)):
        raise Phase4Error("검수 ZIP 파일 집합이 고정 계약과 다릅니다.")
    return infos


def verify_review_archive(archive_path: Path) -> dict[str, Any]:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise Phase4Error("검수 ZIP이 regular file이 아닙니다.")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_archive_members(archive)
            payloads = {name: archive.read(name) for name in REVIEW_FILES}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise Phase4Error("검수 ZIP을 안전하게 읽지 못했습니다.") from exc
    checksum_lines = payloads["SHA256SUMS.txt"].decode("utf-8").splitlines()
    expected: dict[str, str] = {}
    for line in checksum_lines:
        match = SHA_LINE_PATTERN.fullmatch(line)
        if match is None or match.group(2) in expected:
            raise Phase4Error("SHA256SUMS 형식 또는 중복 entry가 올바르지 않습니다.")
        expected[match.group(2)] = match.group(1)
    checksum_targets = set(REVIEW_FILES) - {"SHA256SUMS.txt"}
    if set(expected) != checksum_targets:
        raise Phase4Error("SHA256SUMS 대상 파일 집합이 다릅니다.")
    for name, digest in expected.items():
        if sha256_bytes(payloads[name]) != digest:
            raise Phase4Error(f"검수 ZIP 내부 SHA-256이 다릅니다: {name}")
    try:
        manifest = json.loads(payloads["PACKAGE_MANIFEST.json"])
        prefix = b"window.PHASE4_REVIEW_DATA = "
        if not payloads["review-data.js"].startswith(prefix) or not payloads["review-data.js"].endswith(b";\n"):
            raise Phase4Error("review-data.js wrapper가 올바르지 않습니다.")
        data = json.loads(payloads["review-data.js"][len(prefix) : -2])
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase4Error("검수 ZIP manifest/data JSON이 올바르지 않습니다.") from exc
    if (
        not isinstance(manifest, dict)
        or not isinstance(data, dict)
        or manifest.get("package_id") != data.get("package_id")
        or manifest.get("item_count") != 700
        or manifest.get("case_count") != 720
        or len(data.get("items", [])) != 700
        or data.get("human_domain_review_performed") is not False
        or manifest.get("authorized_local_review_only") is not True
    ):
        raise Phase4Error("검수 ZIP identity 또는 수량 계약이 다릅니다.")
    forbidden = ("eval_id", "case_id", "parent_record", "raw_hash", "source_group_id", "leakage_group_id")
    serialized = canonical_json_bytes(data)
    if any(f'"{key}"'.encode() in serialized for key in forbidden):
        raise Phase4Error("검수 투영에 내부 ID/locator 필드가 남았습니다.")
    return {
        "status": "verified",
        "archive": archive_path.name,
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": sha256_file(archive_path),
        "package_id": manifest["package_id"],
        "item_count": manifest["item_count"],
        "case_count": manifest["case_count"],
        "contains_restricted_source_text": True,
        "human_domain_review_performed": False,
    }


def _public_report(
    context: dict[str, Any],
    review: dict[str, Any],
    k0_summary: dict[str, Any],
    triage_summary: dict[str, Any],
) -> dict[str, Any]:
    gate_c_passed = k0_summary.get("gate_c_passed") is True
    return {
        "schema_version": "1.0.0",
        "report_type": "phase4_abc_non_training_preflight",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "generated_at": utc_now(),
        "phase_status": "부분 진행" if gate_c_passed else "차단",
        "status": "gates_a_b_c_passed" if gate_c_passed else "gate_c_blocked",
        "completed_gates": ["A", "B", "C"] if gate_c_passed else ["A", "B"],
        "evaluated_gates": ["A", "B", "C"],
        "remaining_gates": ["D", "E"],
        "parent_staging": context["config"]["parent_staging"],
        "model": {
            "repo_id": context["config"]["model"]["repo_id"],
            "revision": context["config"]["model"]["revision"],
            "phase3_build_id": context["config"]["model"]["phase3_build_id"],
            "dtype": "bfloat16",
            "attention_backend": "sdpa",
        },
        "runtime": k0_summary["runtime"],
        "runtime_headers": k0_summary["runtime_headers"],
        "generation_contract": context["config"]["generation"],
        "k0": {
            key: k0_summary[key]
            for key in (
                "gate_c_passed",
                "evaluation_items",
                "generation_cases",
                "cross_build_reused_cases",
                "locally_generated_cases",
                "empty_outputs",
                "control_character_outputs",
                "special_token_text_outputs",
                "safety_violations",
                "determinism_replay_passed",
                "peak_vram_bytes",
                "vram_total_bytes",
                "elapsed_seconds",
            )
        },
        "automated_risk_triage": {
            "evaluation_items": triage_summary["evaluation_items"],
            "generation_cases": triage_summary["generation_cases"],
            "severity_counts": triage_summary["severity_counts"],
            "signal_counts": triage_summary["signal_counts"],
            "priority_limit": triage_summary["priority_limit"],
            "priority_items": triage_summary["priority_items"],
            "critical_or_high_items": triage_summary["critical_or_high_items"],
            "automated_second_pass_performed": True,
            "human_domain_review_performed": False,
        },
        "review_package": {
            "archive_name": review["archive"],
            "archive_bytes": review["archive_bytes"],
            "archive_sha256": review["archive_sha256"],
            "package_id": review["package_id"],
            "item_count": review["item_count"],
            "case_count": review["case_count"],
            "verified_at_export": True,
            "contains_restricted_source_text": True,
            "stored_outside_repository": True,
        },
        "human_domain_review_performed": False,
        "canonical_promotion_performed": False,
        "training_performed": False,
        "optimizer_created": False,
        "backward_performed": False,
        "training_promotion_allowed": False,
        "phase4_training_smoke_allowed": gate_c_passed,
        "official_sources": context["config"]["official_sources"],
        "notes": [
            "출처별 assistant token share는 임계값 없이 보고만 하며 가중치를 자동 변경하지 않았다.",
            "512 manifest는 기능 smoke 전용이고, 1024는 진단, 768은 정식 Gate E 후보로 검증한다.",
            "K0 품질 지표는 missing-chart 안전 Gate와 파이프라인 무결성 외에는 진단값이며 학습 승격 판정이 아니다.",
            "Upstream YaRN factor 40 설정과 implicit ratio 8 경고를 수정하지 않았다.",
        ],
    }


def _finalize_public_report(
    context: dict[str, Any], repo_root: Path, review: dict[str, Any]
) -> dict[str, Any]:
    private_root: Path = context["private_root"]
    k0_root: Path = context["k0_root"]
    public_root: Path = context["public_root"]
    verify_triage(context, repo_root)
    if public_root.exists():
        result = _verify_public(context)
        report = load_json(public_root / "preflight_report.json", "preflight report")
        if report.get("review_package", {}).get("archive_sha256") != review["archive_sha256"]:
            raise Phase4Error("기존 공개 보고서와 검수 ZIP SHA-256이 다릅니다.")
        return {**result, "mode": "reused", "writes_performed": False}
    public_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{public_root.name}-", dir=public_root.parent))
    promoted = False
    try:
        source_reports = {
            "schema_validation.json": private_root / "reports/schema_validation.json",
            "split_leakage_report.json": private_root / "reports/split_leakage_report.json",
            "tokenization_report.json": private_root / "reports/tokenization_report.json",
            "manifest_report.json": private_root / "reports/manifest_report.json",
        }
        for name, source in source_reports.items():
            value = load_json(source, name)
            if value.get("raw_samples_in_report") not in {False, None}:
                raise Phase4Error(f"공개 보고서에 raw sample 표기가 있습니다: {name}")
            write_json_once(temporary / name, value, mode=PUBLIC_FILE_MODE)
        k0_summary = load_json(k0_root / "summary.json", "K0 summary")
        if k0_summary.get("raw_samples_in_summary") is not False:
            raise Phase4Error("K0 공개 summary에 raw sample이 포함됐습니다.")
        write_json_once(temporary / "k0_summary.json", k0_summary, mode=PUBLIC_FILE_MODE)
        triage_summary = load_json(k0_root / "triage_summary.json", "K0 triage summary")
        if triage_summary.get("raw_samples_in_summary") is not False:
            raise Phase4Error("K0 triage 공개 summary에 raw sample이 포함됐습니다.")
        write_json_once(
            temporary / "triage_summary.json",
            triage_summary,
            mode=PUBLIC_FILE_MODE,
        )
        report = _public_report(context, review, k0_summary, triage_summary)
        write_json_once(temporary / "preflight_report.json", report, mode=PUBLIC_FILE_MODE)
        artifacts = artifact_hash_map(temporary, list(PUBLIC_ARTIFACTS))
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "phase4_abc_public_manifest",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": artifacts,
            "status": report["status"],
            "completed_gates": report["completed_gates"],
            "evaluated_gates": ["A", "B", "C"],
            "human_domain_review_performed": False,
            "training_promotion_allowed": False,
        }
        write_json_once(temporary / "build_manifest.json", manifest, mode=PUBLIC_FILE_MODE)
        if public_root.exists():
            raise Phase4Error("공개 보고서 생성 중 최종 경로가 생겼습니다.")
        os.replace(temporary, public_root)
        promoted = True
    finally:
        if not promoted:
            shutil.rmtree(temporary, ignore_errors=True)
    return {**_verify_public(context), "mode": "built", "writes_performed": True}


def _verify_public(context: dict[str, Any]) -> dict[str, Any]:
    root: Path = context["public_root"]
    if root.is_symlink() or not root.is_dir():
        raise Phase4Error("Phase 4 공개 보고서 build가 없습니다.")
    manifest = load_json(root / "build_manifest.json", "Phase 4 public manifest")
    report = load_json(root / "preflight_report.json", "Phase 4 preflight report")
    if (
        manifest.get("build_id") != context["build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or manifest.get("build_inputs") != context["build_inputs"]
        or report.get("build_id") != context["build_id"]
        or manifest.get("training_promotion_allowed") is not False
        or report.get("training_promotion_allowed") is not False
        or report.get("human_domain_review_performed") is not False
        or report.get("remaining_gates") != ["D", "E"]
    ):
        raise Phase4Error("Phase 4 공개 보고서 identity/Gate가 다릅니다.")
    verify_hash_map(root, manifest.get("artifact_sha256"), "Phase 4 public")
    return {
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": report["status"],
        "completed_gates": report["completed_gates"],
        "remaining_gates": report["remaining_gates"],
        "review_archive_sha256": report["review_package"]["archive_sha256"],
        "training_promotion_allowed": False,
        "human_domain_review_performed": False,
    }


def export_review_package(
    context: dict[str, Any],
    repo_root: Path,
    output: Path,
    *,
    confirm_authorized_reviewer: bool,
) -> dict[str, Any]:
    if not confirm_authorized_reviewer:
        raise Phase4Error("제한 데이터 검수자 확인 옵션이 필요합니다.")
    repo = repo_root.resolve()
    output = output.resolve(strict=False)
    if output.is_relative_to(repo) or output.suffix.lower() != ".zip":
        raise Phase4Error("검수 ZIP은 저장소 밖의 명시적 .zip 경로에만 만들 수 있습니다.")
    verify_private_build(context, repo_root)
    verify_k0_run(context, repo_root)
    verify_triage(context, repo_root)
    if output.exists():
        review = verify_review_archive(output)
        if review["package_id"] != f"review-{sha256_json(_review_identity(context))[:16]}":
            raise Phase4Error("기존 검수 ZIP은 현재 build의 패키지가 아닙니다.")
        public = _finalize_public_report(context, repo_root, review)
        return {**review, "mode": "reused", "public_report": public, "writes_performed": False}
    items = _load_review_items(context)
    payloads, _ = _build_review_payloads(context, repo_root, items)
    _write_review_zip(output, payloads)
    review = verify_review_archive(output)
    sidecar = output.with_name(f"{output.name}.sha256")
    sidecar_payload = f"{review['archive_sha256']}  {output.name}\n".encode()
    write_bytes_once(sidecar, sidecar_payload, mode=PRIVATE_FILE_MODE)
    public = _finalize_public_report(context, repo_root, review)
    return {
        **review,
        "mode": "built",
        "sidecar": sidecar.name,
        "public_report": public,
        "writes_performed": True,
    }


def verify_preflight(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    private = verify_private_build(context, repo_root)
    k0 = verify_k0_run(context, repo_root)
    triage = verify_triage(context, repo_root)
    public = _verify_public(context)
    if (k0["gate_c_passed"] is True) != (public["status"] == "gates_a_b_c_passed"):
        raise Phase4Error("K0와 공개 Phase 4 Gate C 판정이 다릅니다.")
    return {
        "status": "verified",
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "private": private,
        "k0": k0,
        "triage": triage,
        "public": public,
        "training_promotion_allowed": False,
        "human_domain_review_performed": False,
    }
