# project_status.py - 정본 config에서 접근 가능한 단일 파일 프로젝트 현황판을 생성한다.

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.preflight.phase4_common import (
    load_json,
    resolve_repo_path,
    sha256_file,
    sha256_json,
)

DEFAULT_CONFIG = Path(
    "configs/data_versions/saju_1b_baseline/project-status-v1.0.0.json"
)
PUBLIC_FILE_MODE = 0o644
BUILD_PATTERN = re.compile(r"^(?:build|run|gate|preflight)-[0-9a-f]{12}$")


class ProjectStatusError(RuntimeError):
    """프로젝트 상태 config·HTML·registry 일관성 위반."""


def _safe_path(repo_root: Path, relative: str) -> Path:
    try:
        return resolve_repo_path(repo_root, relative)
    except Exception as exc:
        raise ProjectStatusError(f"안전하지 않은 상태 HTML 경로입니다: {relative}") from exc


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def validate_contract(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    if (
        config.get("schema_version") != "1.0.0"
        or config.get("canonical_plan_version") != "3.3.0"
        or config.get("dataset_name") != "saju_1b_baseline"
        or config.get("status_version") != "v1.0.0"
        or config.get("as_of") != "2026-08-29"
        or config.get("stage")
        not in {"pre_ki10", "ki10_gate_failed", "ki20_preflight_ready"}
    ):
        raise ProjectStatusError("project status identity가 다릅니다.")
    components = config.get("components")
    if not isinstance(components, list) or len(components) < 8:
        raise ProjectStatusError("project status component가 부족합니다.")
    names: set[str] = set()
    for value in components:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("name"), str)
            or value["name"] in names
            or not isinstance(value.get("version"), str)
            or BUILD_PATTERN.fullmatch(str(value.get("build_id", ""))) is None
            or not isinstance(value.get("status"), str)
        ):
            raise ProjectStatusError("project status component 형식이 다릅니다.")
        names.add(value["name"])
        digest = value.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ProjectStatusError("project status component hash가 다릅니다.")
    phases = config.get("phases")
    if (
        not isinstance(phases, list)
        or [value.get("phase") for value in phases] != list(range(7))
        or any(value.get("status") not in {"완료", "진행 중", "미시작", "차단"} for value in phases)
    ):
        raise ProjectStatusError("project status Phase 타임라인이 다릅니다.")
    axes = config.get("dataset_axes")
    if not isinstance(axes, list) or sum(int(value.get("rows", 0)) for value in axes) != 20_000:
        raise ProjectStatusError("project status 데이터 축 수량이 다릅니다.")
    token_sum = sum(float(value.get("assistant_token_share_percent", 0)) for value in axes)
    if abs(token_sum - 100.0) > 0.001:
        raise ProjectStatusError("project status assistant token 비율 합계가 다릅니다.")
    if not isinstance(config.get("gates"), list) or not config["gates"]:
        raise ProjectStatusError("project status Gate가 없습니다.")
    if not isinstance(config.get("known_risks"), list) or not config["known_risks"]:
        raise ProjectStatusError("project status 위험 목록이 없습니다.")
    decision = config.get("decision")
    decision_keys = {
        "stage_label",
        "stage_status",
        "signal",
        "signal_tone",
        "headline",
        "headline_accent",
        "summary",
        "ki10_baseline",
        "ki20_promotion",
        "expert_quality",
        "sealed_blind",
        "phase4_rerun",
    }
    if (
        not isinstance(decision, dict)
        or set(decision) != decision_keys
        or any(not isinstance(decision[key], str) or not decision[key] for key in decision_keys)
        or decision["signal_tone"] not in {"ok", "stop", "wait"}
        or any(
            decision[key] not in {"완료", "허용", "금지", "차단", "대기"}
            for key in (
                "stage_status",
                "ki10_baseline",
                "ki20_promotion",
                "expert_quality",
                "sealed_blind",
            )
        )
    ):
        raise ProjectStatusError("project status 현재 결정 형식이 다릅니다.")
    outputs = config.get("outputs")
    if outputs != {
        "root_html": "PROJECT_STATUS.html",
        "snapshot_root": "data/reports/saju_1b_baseline/project-status/v1.0.0/{build_id}",
    }:
        raise ProjectStatusError("project status 출력 경로가 다릅니다.")
    _safe_path(repo_root, outputs["root_html"])
    _safe_path(repo_root, outputs["snapshot_root"].format(build_id="build-000000000000"))
    if config.get("implementation_files") != [
        "scripts/status/project_status.py"
    ]:
        raise ProjectStatusError("project status 구현 fingerprint가 다릅니다.")
    return {"status": "valid", "status_version": "v1.0.0"}


def prepare_context(repo_root: Path, config_path: Path) -> dict[str, Any]:
    config = load_json(config_path, "project status config")
    validate_contract(config, repo_root)
    relative_config = config_path.resolve().relative_to(repo_root.resolve()).as_posix()
    implementation_hashes = {
        relative: sha256_file(_safe_path(repo_root, relative))
        for relative in [*config["implementation_files"], relative_config]
    }
    build_inputs = {
        "status_version": config["status_version"],
        "as_of": config["as_of"],
        "stage": config["stage"],
        "decision_sha256": sha256_json(config["decision"]),
        "components_sha256": sha256_json(config["components"]),
        "phases_sha256": sha256_json(config["phases"]),
        "dataset_axes_sha256": sha256_json(config["dataset_axes"]),
        "evidence_sha256": sha256_json(config["evidence_tiers"]),
        "gates_sha256": sha256_json(config["gates"]),
        "risks_sha256": sha256_json(config["known_risks"]),
        "web_sources_sha256": sha256_json(config["web_sources"]),
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
        "root_html": _safe_path(repo_root, config["outputs"]["root_html"]),
        "snapshot_root": _safe_path(
            repo_root,
            config["outputs"]["snapshot_root"].format(build_id=build_id),
        ),
    }


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _badge(value: str) -> str:
    tone = {
        "완료": "ok",
        "통과": "ok",
        "허용": "ok",
        "진행 중": "live",
        "대기": "wait",
        "미시작": "wait",
        "금지": "stop",
        "차단": "stop",
        "주의": "warn",
    }.get(value, "neutral")
    return f'<span class="badge {tone}">{_esc(value)}</span>'


def render_html(context: dict[str, Any]) -> bytes:
    config = context["config"]
    decision = config["decision"]
    phases = "".join(
        f"""
        <li class="phase {_esc(value['status'])}">
          <span class="phase-index">{value['phase']}</span>
          <div><strong>{_esc(value['title'])}</strong><small>{_esc(value['note'])}</small></div>
          {_badge(value['status'])}
        </li>"""
        for value in config["phases"]
    )
    axes = "".join(
        f"""
        <tr>
          <th scope="row">{_esc(value['label'])}</th>
          <td>{int(value['rows']):,}</td>
          <td><div class="bar" role="img" aria-label="{_esc(value['label'])} assistant token 비율 {value['assistant_token_share_percent']}%"><i style="width:{float(value['assistant_token_share_percent']):.6f}%"></i></div></td>
          <td>{float(value['assistant_token_share_percent']):.3f}%</td>
          <td>{_badge(value['evidence'])}</td>
        </tr>"""
        for value in config["dataset_axes"]
    )
    components = "".join(
        f"""
        <div class="node">
          <span>{_esc(value['name'])}</span>
          <strong>{_esc(value['version'])}</strong>
          <code>{_esc(value['build_id'])}</code>
          <small>{_esc(value['sha256'][:12])}…</small>
          {_badge(value['status'])}
        </div>"""
        for value in config["components"]
    )
    evidence = "".join(
        f"""
        <article class="evidence {_esc(value['tone'])}">
          <span>{_esc(value['code'])}</span><h3>{_esc(value['title'])}</h3>
          <p>{_esc(value['description'])}</p>
        </article>"""
        for value in config["evidence_tiers"]
    )
    gates = "".join(
        f"""
        <tr><th scope="row">{_esc(value['name'])}</th><td>{_badge(value['status'])}</td><td>{_esc(value['criterion'])}</td><td>{_esc(value['result'])}</td></tr>"""
        for value in config["gates"]
    )
    risks = "".join(
        f"""
        <article class="risk"><div>{_badge(value['severity'])}<code>{_esc(value['id'])}</code></div><h3>{_esc(value['title'])}</h3><p>{_esc(value['finding'])}</p><small>{_esc(value['disposition'])}</small></article>"""
        for value in config["known_risks"]
    )
    sources = "".join(
        f"""
        <tr><th scope="row"><a href="{_esc(value['url'])}">{_esc(value['name'])}</a></th><td><code>{_esc(value['revision'])}</code></td><td>{_esc(value['role'])}</td><td>{_esc(value['decision'])}</td></tr>"""
        for value in config["web_sources"]
    )
    checks = "".join(
        f'<li><span aria-hidden="true">✓</span>{_esc(value)}</li>'
        for value in config["validation_summary"]
    )
    html_value = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>사주 일기 도우미 · 학습·품질 현황</title>
<style>
:root{{--ink:#edf5f0;--muted:#9ab0a7;--bg:#07110f;--panel:#0d1c18;--line:#1f3a32;--mint:#59e5b2;--cyan:#55c8e8;--amber:#f5bd5c;--red:#ff7c78;--violet:#aa91ff}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 12% 0,#12352b 0,transparent 33%),radial-gradient(circle at 88% 12%,#172b45 0,transparent 30%),var(--bg);color:var(--ink);font:15px/1.58 ui-sans-serif,system-ui,-apple-system,"Noto Sans KR",sans-serif}}a{{color:var(--cyan)}}code{{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;color:#bceee0}}main{{width:min(1180px,calc(100% - 32px));margin:auto;padding:44px 0 72px}}.hero{{display:grid;grid-template-columns:1.5fr .9fr;gap:22px;align-items:stretch}}.hero>div,.panel{{background:linear-gradient(145deg,rgba(16,38,32,.96),rgba(10,24,21,.92));border:1px solid var(--line);border-radius:22px;box-shadow:0 18px 60px #0006}}.intro{{padding:36px}}.eyebrow{{color:var(--mint);font-weight:800;letter-spacing:.13em;text-transform:uppercase}}h1{{font-size:clamp(34px,6vw,66px);line-height:1.02;margin:.25em 0}}h1 span{{display:block;color:transparent;background:linear-gradient(90deg,var(--mint),var(--cyan));background-clip:text}}.lede{{color:var(--muted);font-size:17px;max-width:70ch}}.decision{{padding:28px;display:flex;flex-direction:column;justify-content:space-between}}.decision .signal{{font-size:52px;font-weight:900}}.decision .signal.ok{{color:var(--mint)}}.decision .signal.stop{{color:var(--red)}}.decision .signal.wait{{color:var(--amber)}}.decision p{{color:var(--muted)}}.meta{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.meta div{{background:#071510;border:1px solid var(--line);border-radius:12px;padding:12px}}.meta small{{display:block;color:var(--muted)}}section{{margin-top:22px}}.panel{{padding:26px}}h2{{margin:0 0 18px;font-size:22px}}.grid-2{{display:grid;grid-template-columns:1fr 1fr;gap:22px}}.timeline{{list-style:none;padding:0;margin:0;display:grid;gap:9px}}.phase{{display:grid;grid-template-columns:36px 1fr auto;gap:12px;align-items:center;padding:11px;border:1px solid var(--line);border-radius:13px;background:#081713}}.phase-index{{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:#17382f;color:var(--mint);font-weight:900}}.phase small{{display:block;color:var(--muted)}}.badge{{display:inline-flex;align-items:center;padding:3px 9px;border-radius:999px;border:1px solid #ffffff1c;font-size:11px;font-weight:800;white-space:nowrap}}.badge.ok{{background:#163b2f;color:#7cf0c2}}.badge.live{{background:#14364c;color:#77d9f2}}.badge.warn{{background:#493815;color:#ffd382}}.badge.wait,.badge.neutral{{background:#242d2a;color:#b3c1bc}}.badge.stop{{background:#482321;color:#ffaaa6}}.chain{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}.node{{min-width:0;padding:15px;border:1px solid var(--line);border-radius:14px;background:#071510;position:relative}}.node:after{{content:"→";position:absolute;right:-12px;top:45%;color:var(--mint);z-index:2}}.node:nth-child(4n):after,.node:last-child:after{{display:none}}.node span,.node small,.node code{{display:block;overflow:hidden;text-overflow:ellipsis}}.node span,.node small{{color:var(--muted)}}.node strong{{font-size:17px}}table{{border-collapse:collapse;width:100%}}th,td{{border-bottom:1px solid var(--line);text-align:left;padding:11px 9px;vertical-align:middle}}th{{color:#dff8ee}}td{{color:var(--muted)}}.bar{{min-width:160px;height:9px;background:#06100d;border-radius:99px;overflow:hidden}}.bar i{{display:block;height:100%;background:linear-gradient(90deg,var(--mint),var(--cyan));border-radius:inherit}}.evidence-grid,.risk-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}.evidence,.risk{{border:1px solid var(--line);border-radius:15px;padding:16px;background:#081713}}.evidence span{{font:800 12px ui-monospace;color:var(--mint)}}.evidence h3,.risk h3{{margin:8px 0}}.evidence p,.risk p,.risk small{{color:var(--muted)}}.risk-grid{{grid-template-columns:repeat(2,1fr)}}.risk>div{{display:flex;justify-content:space-between;gap:8px}}.checks{{display:grid;grid-template-columns:repeat(2,1fr);list-style:none;padding:0;gap:8px}}.checks li{{padding:11px;border:1px solid var(--line);border-radius:12px;background:#081713}}.checks span{{color:var(--mint);margin-right:9px}}footer{{margin-top:28px;padding:22px;color:var(--muted);text-align:center}}@media(max-width:850px){{.hero,.grid-2{{grid-template-columns:1fr}}.chain{{grid-template-columns:repeat(2,1fr)}}.node:nth-child(2n):after{{display:none}}.evidence-grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:560px){{main{{width:min(100% - 20px,1180px);padding-top:20px}}.intro,.panel,.decision{{padding:18px}}.chain,.evidence-grid,.risk-grid,.checks{{grid-template-columns:1fr}}.node:after{{display:none}}.table-wrap{{overflow:auto}}}}
</style>
</head>
<body><main>
<header class="hero">
  <div class="intro"><div class="eyebrow">Saju Diary Assistant · {_esc(config['status_version'])}</div><h1>{_esc(decision['headline'])}<span>{_esc(decision['headline_accent'])}</span></h1><p class="lede">{_esc(decision['summary'])}</p></div>
  <div class="decision"><div><span class="badge {_esc(decision['signal_tone'])}">{_esc(decision['stage_label'])}</span><div class="signal {_esc(decision['signal_tone'])}">{_esc(decision['signal'])}</div><p>{_esc(decision['summary'])}</p></div><div class="meta"><div><small>기준일</small><strong>{_esc(config['as_of'])}</strong></div><div><small>현황 build</small><code>{_esc(context['build_id'])}</code></div><div><small>계획 버전</small><strong>{_esc(config['canonical_plan_version'])}</strong></div><div><small>현재 상태</small><strong>{_esc(decision['stage_status'])}</strong></div></div></div>
</header>
<section class="grid-2"><div class="panel"><h2>Phase 타임라인</h2><ol class="timeline">{phases}</ol></div><div class="panel"><h2>현재 결정</h2><table><tbody><tr><th>KI10 baseline</th><td>{_badge(decision['ki10_baseline'])}</td></tr><tr><th>KI20 promotion</th><td>{_badge(decision['ki20_promotion'])}</td></tr><tr><th>전문가 품질 인증</th><td>{_badge(decision['expert_quality'])}</td></tr><tr><th>sealed blind 접근</th><td>{_badge(decision['sealed_blind'])}</td></tr><tr><th>Phase 4 재실행</th><td>{_esc(decision['phase4_rerun'])}</td></tr></tbody></table></div></section>
<section class="panel"><h2>버전·해시 연결</h2><div class="chain" aria-label="모델과 데이터 버전 hash chain">{components}</div></section>
<section class="panel"><h2>20K 구성 — 행보다 supervised token을 함께 봅니다</h2><div class="table-wrap"><table><thead><tr><th>축</th><th>행</th><th>assistant token 분포</th><th>비율</th><th>근거</th></tr></thead><tbody>{axes}</tbody></table></div></section>
<section class="panel"><h2>근거 등급</h2><div class="evidence-grid">{evidence}</div></section>
<section class="panel"><h2>학습·품질 Gate</h2><div class="table-wrap"><table><thead><tr><th>Gate</th><th>상태</th><th>기준</th><th>결과</th></tr></thead><tbody>{gates}</tbody></table></div></section>
<section class="panel"><h2>남은 위험과 처리</h2><div class="risk-grid">{risks}</div></section>
<section class="panel"><h2>웹·외부 구현 비교</h2><div class="table-wrap"><table><thead><tr><th>근거</th><th>확인 revision</th><th>역할</th><th>판정</th></tr></thead><tbody>{sources}</tbody></table></div></section>
<section class="panel"><h2>재현 검증</h2><ul class="checks">{checks}</ul></section>
<footer>이 파일은 <code>{_esc(context['build_sha256'])}</code>에서 결정적으로 생성됐습니다. AI Hub 원문·개별 ID·checkpoint는 포함하지 않습니다.</footer>
</main></body></html>
"""
    return html_value.encode("utf-8")


def _manifest(context: dict[str, Any], payload: bytes) -> bytes:
    return _json_bytes(
        {
            "schema_version": "1.0.0",
            "report_type": "project_status_public_manifest",
            "status_version": context["config"]["status_version"],
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "artifact_sha256": {"index.html": hashlib.sha256(payload).hexdigest()},
            "root_html_byte_identical": True,
            "restricted_content_included": False,
        }
    )


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(PUBLIC_FILE_MODE)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_status(context: dict[str, Any]) -> dict[str, Any]:
    snapshot: Path = context["snapshot_root"]
    payload = render_html(context)
    manifest = _manifest(context, payload)
    if snapshot.exists():
        result = verify_status(context, require_registry=False)
        if context["root_html"].read_bytes() != payload:
            _atomic_replace(context["root_html"], payload)
        return {**result, "mode": "reused"}
    snapshot.mkdir(parents=True, exist_ok=False)
    _atomic_replace(snapshot / "index.html", payload)
    _atomic_replace(snapshot / "build_manifest.json", manifest)
    _atomic_replace(context["root_html"], payload)
    return {**verify_status(context, require_registry=False), "mode": "built"}


def verify_status(context: dict[str, Any], *, require_registry: bool) -> dict[str, Any]:
    payload = render_html(context)
    snapshot: Path = context["snapshot_root"]
    root_html: Path = context["root_html"]
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or root_html.is_symlink()
        or not root_html.is_file()
        or (snapshot / "index.html").read_bytes() != payload
        or root_html.read_bytes() != payload
        or (snapshot / "build_manifest.json").read_bytes() != _manifest(context, payload)
    ):
        raise ProjectStatusError("project status HTML/manifest가 재현되지 않습니다.")
    text = payload.decode("utf-8")
    for required in (
        "Phase 타임라인",
        "버전·해시 연결",
        "20K 구성",
        "근거 등급",
        "학습·품질 Gate",
        "남은 위험과 처리",
        "웹·외부 구현 비교",
        "재현 검증",
    ):
        if required not in text:
            raise ProjectStatusError(f"project status 필수 섹션이 없습니다: {required}")
    if "<script" in text or "<link" in text or "src=\"http" in text:
        raise ProjectStatusError("project status에 외부 실행 자산이 있습니다.")
    if require_registry:
        registry = load_json(
            REPO_ROOT / "configs/data_versions/saju_1b_baseline/registry.json",
            "dataset registry",
        )
        approved = registry.get("approved_project_status")
        if (
            not isinstance(approved, dict)
            or approved.get("version") != context["config"]["status_version"]
            or approved.get("build_id") != context["build_id"]
            or approved.get("build_sha256") != context["build_sha256"]
            or approved.get("html_sha256") != hashlib.sha256(payload).hexdigest()
        ):
            raise ProjectStatusError("registry project status 포인터가 다릅니다.")
    return {
        "status": "verified",
        "status_version": context["config"]["status_version"],
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "html_sha256": hashlib.sha256(payload).hexdigest(),
        "root_html_byte_identical": True,
        "registry_verified": require_registry,
        "writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="사주 일기 도우미 버전 현황 HTML")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    render = commands.add_parser("render")
    render.add_argument("--execute", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--require-registry", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = args.config if args.config.is_absolute() else REPO_ROOT / args.config
    try:
        if args.command == "validate-contract":
            result = validate_contract(load_json(config_path, "project status config"), REPO_ROOT)
        else:
            context = prepare_context(REPO_ROOT, config_path)
            if args.command == "plan":
                result = {
                    "status": "planned",
                    "build_id": context["build_id"],
                    "build_sha256": context["build_sha256"],
                    "snapshot_root": context["snapshot_root"].relative_to(REPO_ROOT).as_posix(),
                    "writes_performed": False,
                }
            elif args.command == "render":
                result = (
                    build_status(context)
                    if args.execute
                    else {"status": "dry_run", "build_id": context["build_id"], "writes_performed": False}
                )
            else:
                result = verify_status(context, require_registry=args.require_registry)
    except (ProjectStatusError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
