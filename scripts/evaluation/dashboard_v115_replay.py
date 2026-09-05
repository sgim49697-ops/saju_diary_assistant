# dashboard_v115_replay.py - 검증된 신규 합성 대화만 v1.15에서 재생하고 공개 집계를 분리한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import sysconfig
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_grounding_v2 import audit_output
from scripts.training.dashboard_tokenizer_v1 import BACKEND_SHA256
from scripts.training.mix2k_v4_lora import _compute_processes, _gpu_snapshot

SOURCE_HASHES = {
    "suite.json": "2691ee90456d47524917aef19334b52b6d590cfd77fc89df9f3d78672e3aea99",
    "today.runtime.json": "52902a615886e2dd2e7eb199301294d1fc58fbb3855c2609a15f2bfda69390ce",
}
FROZEN_DATE = date(2026, 9, 5)
ENGINES = ("k0_instruct", "lora_r16", "ki20_final")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("진단 입력 경로·크기가 안전하지 않습니다.")
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, value: object):
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    path.chmod(0o600)


def entry_sha(entry):
    return hashlib.sha256(
        json.dumps(
            {key: value for key, value in entry.items() if key != "entry_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def prepare(source: Path, artifact_root: Path, output: Path):
    artifact_root = artifact_root.resolve(strict=True)
    for path in (source, output):
        if not path.is_absolute() or any(
            parent.is_symlink() for parent in (path, *path.parents)
        ):
            raise ValueError("진단 경로는 symlink 없는 절대 경로여야 합니다.")
        path.resolve().relative_to(artifact_root / "runs/REALISTIC-CHAT")
    for name, expected in SOURCE_HASHES.items():
        load(source / name)
        if sha(source / name) != expected:
            raise ValueError("승인된 합성 진단 입력 SHA가 다릅니다.")
    suite = load(source / "suite.json")
    runtime = load(source / "today.runtime.json")
    if (
        suite.get("synthetic_only") is not True
        or suite.get("sealed_blind_accessed") is not False
    ):
        raise ValueError("합성 전용 입력 계약이 다릅니다.")
    value = runtime["result"]
    binding = {
        "schema_version": "1.1.0",
        "binding_id": "saju-chart-day-dashboard-binding-v1.1.0",
        "capability_sha256": hashlib.sha256(
            b"synthetic-offline-replay-v115"
        ).hexdigest(),
        "snapshot_sha256": hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        ).hexdigest(),
        "state_revision": runtime["state_revision"],
        "value": value,
    }
    dashboard._runtime_model_context_from_binding(binding)
    if (
        value["period"]["hard_facts"]["period"]["target_date"]
        != FROZEN_DATE.isoformat()
    ):
        raise ValueError("고정 snapshot 날짜가 다릅니다.")
    code_paths = [
        Path(__file__).relative_to(REPO_ROOT),
        dashboard.DEFAULT_CONFIG,
        Path("scripts/training/phase5_dashboard_v1_15.py"),
        Path("scripts/training/dashboard_grounding_v2.py"),
        Path("scripts/training/dashboard_tokenizer_v1.py"),
        Path("configs/chat_prompts/saju_bound_chart_v2.txt"),
        Path("configs/chat_prompts/saju_intake_runtime_v2.txt"),
    ]
    identity = {
        "schema_version": "1.0.0",
        "dashboard_version": "1.15.0",
        "frozen_server_kst_date": FROZEN_DATE.isoformat(),
        "snapshot_sha256": binding["snapshot_sha256"],
        "source_sha256": SOURCE_HASHES,
        "code_sha256": {str(p): sha(REPO_ROOT / p) for p in code_paths},
        "engines": list(ENGINES),
        "expected_requests": 30,
        "expected_generations": 27,
        "expected_pre_generation_blocks": 3,
        "sealed_blind_accessed": False,
        "production_promotion_allowed": False,
        "training_performed": False,
        "first_turn_token_identity_required": True,
        "followup_history": "own_engine_only",
    }
    context = dashboard.prepare_context(
        REPO_ROOT,
        dashboard.DEFAULT_CONFIG,
        artifact_root / "runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67",
        artifact_root=artifact_root,
    )
    context["chart_only_runtime_active"] = True
    return suite, binding, identity, context


def header_check():
    roots = [
        Path(sysconfig.get_path("include")),
        *[Path(p) for p in os.environ.get("CPATH", "").split(os.pathsep) if p],
    ]
    major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if not any(
        (root / "Python.h").is_file()
        and (root / "pyconfig.h").is_file()
        and (root / "patchlevel.h").is_file()
        and f'"{major_minor}.' in (root / "patchlevel.h").read_text()
        for root in roots
    ):
        raise ValueError("현재 Python과 일치하는 Python.h·pyconfig.h가 필요합니다.")
    if os.environ.get("TORCH_DISABLE_NATIVE_JIT") == "1":
        raise ValueError("진단은 native JIT를 비활성화할 수 없습니다.")


def execute(suite, binding, identity, context, output):
    previous_umask = os.umask(0o077)
    try:
        return _execute_private(suite, binding, identity, context, output)
    finally:
        os.umask(previous_umask)


def _execute_private(suite, binding, identity, context, output):
    header_check()
    if _compute_processes() or _gpu_snapshot()["free_mib"] < 12 * 1024:
        raise ValueError("GPU compute 유휴·12GiB 이상 여유 조건이 필요합니다.")
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.chmod(0o700)
    manifest = output / "build_manifest.json"
    if manifest.exists():
        if load(manifest) != identity:
            raise ValueError(
                "기존 build fingerprint가 달라 재개할 수 없습니다. 새 build를 사용하세요."
            )
    else:
        if any(output.iterdir()):
            raise ValueError("manifest 없는 비어 있지 않은 경로는 사용할 수 없습니다.")
        write_new(manifest, identity)
    entries = []
    with patch(
        "scripts.training.dashboard_grounding_v2.kst_today", return_value=FROZEN_DATE
    ):
        for case, bound, prompts in suite["scenarios"]:
            if re.fullmatch(r"[a-z_]+", case) is None:
                raise ValueError("case 식별자가 다릅니다.")
            for engine in ENGINES:
                session_id = None
                for turn, prompt in enumerate(prompts, 1):
                    target = output / f"{case}.{engine}.{turn}.json"
                    pending = output / f"{case}.{engine}.{turn}.started.json"
                    if target.exists():
                        entry = load(target)
                        if entry.get("entry_sha256") != entry_sha(entry):
                            raise ValueError("재개 응답 SHA가 다릅니다.")
                        if (
                            entry["prompt"] != prompt
                            or entry["engine"] != engine
                            or entry["case"] != case
                            or entry["turn"] != turn
                        ):
                            raise ValueError("재개 응답 identity가 다릅니다.")
                        if entry["status"] == "generated":
                            session_id = entry["response"]["session_id"]
                    else:
                        if pending.exists():
                            raise ValueError(
                                "완료 기록 없는 이전 생성 시도가 있습니다. 자동 중복 실행하지 않습니다."
                            )
                        write_new(
                            pending, {"case": case, "engine": engine, "turn": turn}
                        )
                        entry = {
                            "case": case,
                            "engine": engine,
                            "turn": turn,
                            "prompt": prompt,
                        }
                        try:
                            response = dashboard.execute_manual_generation(
                                context,
                                prompt,
                                session_id,
                                engine_selection=engine,
                                runtime_binding=binding if bound else None,
                            )
                        except dashboard.DashboardRequestError as exc:
                            if exc.reason_code not in {
                                "RUNTIME_DATE_REBIND_REQUIRED",
                                "RUNTIME_PERIOD_SCOPE_UNSUPPORTED",
                                "RUNTIME_DATE_SELECTION_REQUIRED",
                            }:
                                raise
                            entry.update(
                                status="blocked",
                                code=exc.reason_code,
                                model_invoked=False,
                            )
                        else:
                            session_id = response["session_id"]
                            entry.update(status="generated", response=response)
                        entry["entry_sha256"] = entry_sha(entry)
                        write_new(target, entry)
                    entries.append(entry)
                    print(
                        json.dumps(
                            {
                                "case": case,
                                "engine": engine,
                                "turn": turn,
                                "status": entry["status"],
                            }
                        ),
                        flush=True,
                    )
    aggregate = summarize(entries, binding)
    if not aggregate["execution_contract_met"]:
        raise ValueError("요청 수·차단 수·첫 turn token identity 계약이 다릅니다.")
    aggregate_path = output / "aggregate.json"
    if aggregate_path.exists():
        if load(aggregate_path) != aggregate:
            raise ValueError("기존 집계가 달라 덮어쓰지 않습니다.")
    else:
        write_new(aggregate_path, aggregate)
    inventory = {
        p.name: sha(p)
        for p in sorted(output.glob("*.json"))
        if p.name != "private_manifest.json"
    }
    private_manifest = output / "private_manifest.json"
    if private_manifest.exists():
        if load(private_manifest) != inventory:
            raise ValueError("비공개 artifact inventory가 다릅니다.")
    else:
        write_new(private_manifest, inventory)
    return aggregate


def summarize(entries, binding):
    models = {}
    first_ids = {}
    for engine in ENGINES:
        generated = [
            e for e in entries if e["engine"] == engine and e["status"] == "generated"
        ]
        blocked = [
            e for e in entries if e["engine"] == engine and e["status"] == "blocked"
        ]
        reasons = Counter()
        bounded, passed = 0, 0
        max_input, omitted = 0, 0
        for entry in generated:
            response = entry["response"]
            diagnostic = response["contexts"][engine]
            if diagnostic["tokenizer_backend_sha256"] != BACKEND_SHA256:
                raise ValueError("저장된 tokenizer backend가 다릅니다.")
            max_input = max(max_input, diagnostic["input_tokens"])
            omitted += diagnostic["omitted_turns"]
            if entry["turn"] == 1:
                first_ids.setdefault(entry["case"], set()).add(
                    (
                        diagnostic["rendered_prompt_sha256"],
                        diagnostic["input_token_ids_sha256"],
                        diagnostic["input_tokens"],
                    )
                )
            if response["runtime_binding_applied"]:
                audit = audit_output(
                    entry["prompt"], response["outputs"][engine], binding
                )
                bounded += 1
                passed += audit["passed"]
                reasons.update(audit["reasons"])
        models[engine] = {
            "generated": len(generated),
            "pre_generation_blocks": len(blocked),
            "nonempty": sum(
                bool(e["response"]["outputs"][engine].strip()) for e in generated
            ),
            "bound_generated": bounded,
            "bound_diagnostic_pass": passed,
            "warnings": dict(sorted(reasons.items())),
            "max_input_tokens": max_input,
            "omitted_turns": omitted,
            "block_codes": dict(Counter(e["code"] for e in blocked)),
        }
    parity = len(first_ids) == 6 and all(
        len(values) == 1 for values in first_ids.values()
    )
    return {
        "schema_version": "1.0.0",
        "dashboard_version": "1.15.0",
        "requests": len(entries),
        "models": models,
        "first_turn_token_parity": parity,
        "first_turn_groups": len(first_ids),
        "execution_contract_met": len(entries) == 30
        and parity
        and all(
            m["generated"] == 9
            and m["pre_generation_blocks"] == 1
            and m["nonempty"] == 9
            for m in models.values()
        ),
        "naturalness": "not_measured",
        "semantics": "not_measured",
        "model_quality_approval": False,
        "sealed_blind_accessed": False,
        "production_promotion_allowed": False,
        "runtime_release_changed": False,
        "phase6_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description="v1.15 합성 대화 30요청 재진단")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    suite, binding, identity, context = prepare(
        args.source, args.artifact_root, args.output
    )
    if not args.execute:
        print(
            json.dumps(
                {"status": "dry_run", "writes_performed": False, **identity},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if os.environ.get("DASHBOARD_V115_DIAGNOSTIC") != "SYNTHETIC_30_V1":
        raise ValueError("DASHBOARD_V115_DIAGNOSTIC=SYNTHETIC_30_V1 명시가 필요합니다.")
    print(
        json.dumps(
            execute(suite, binding, identity, context, args.output),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
