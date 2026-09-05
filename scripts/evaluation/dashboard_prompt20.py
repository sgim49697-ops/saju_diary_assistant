# dashboard_prompt20.py - 확정된 합성 질문 20개를 세 모델의 독립 대화로 진단한다.

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_v115_replay as base
from scripts.training.dashboard_grounding_v2 import audit_output

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_SHA256 = "c78b08d30bd122bc56823eb731ed6e135a12be28a8652770a2e91d777fcdb72e"
PARENTS = {8: 7, 9: 8, 11: 7, 12: 7, 14: 13, 15: 14, 19: 18, 20: 19}
EXPECTED_BLOCKS = {
    11: "RUNTIME_DATE_REBIND_REQUIRED",
    12: "RUNTIME_PERIOD_SCOPE_UNSUPPORTED",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def prompts_from_document(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000:
        raise ValueError("질문 문서 경로·크기가 다릅니다.")
    rows = re.findall(r"^(\d+)\. “(.+)”$", path.read_text(encoding="utf-8"), re.MULTILINE)
    prompts = [prompt for _, prompt in rows]
    if [int(n) for n, _ in rows] != list(range(1, 21)) or digest(
        prompts
    ) != PROMPTS_SHA256:
        raise ValueError("확정된 20문장 원문·번호 SHA가 다릅니다.")
    return prompts


def chart_binding(day_binding):
    value = {"chart": copy.deepcopy(day_binding["value"]["chart"])}
    result = {
        **day_binding,
        "schema_version": "1.0.0",
        "binding_id": "saju-chart-only-dashboard-binding-v1.0.0",
        "capability_sha256": digest({"synthetic_prompt20_chart": value}),
        "snapshot_sha256": digest(value),
        "value": value,
    }
    base.dashboard._runtime_model_context_from_binding(result)
    return result


def binding_for(number, bindings):
    return None if 13 <= number <= 17 else bindings["chart" if number <= 6 else "day"]


def prepare(source, artifact_root, output):
    _, day, identity, context = base.prepare(source, artifact_root, output)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", output.name):
        raise ValueError("출력 build 이름이 안전하지 않습니다.")
    prompts = prompts_from_document(ROOT / "SAJU_CHAT_TEST_PROMPTS.md")
    bindings = {"day": day, "chart": chart_binding(day)}
    if day["value"]["chart"]["hard_facts"]["day_master"]["stem"] != "丙":
        raise ValueError("잘못된 전제 교정용 합성 원국 일간이 다릅니다.")
    context = copy.deepcopy(context)
    relative = f"dashboard/v1.15.0/prompt20-v1/{output.name}/sessions"
    context["config"]["manual_session"]["private_output_relative"] = relative
    identity.update(
        diagnostic_version="synthetic-prompt20-v1.0.0",
        prompt_document_sha256=base.sha(ROOT / "SAJU_CHAT_TEST_PROMPTS.md"),
        prompts_sha256=PROMPTS_SHA256,
        parent_graph={str(k): v for k, v in PARENTS.items()},
        chart_snapshot_sha256=bindings["chart"]["snapshot_sha256"],
        expected_requests=60,
        expected_generations=54,
        expected_pre_generation_blocks=6,
        expected_first_turn_groups=12,
        private_session_relative=relative,
        generation=context["config"]["model_check"]["generation"],
        system_prompt_changed=False,
        raw_output_rewrite_allowed=False,
    )
    identity["code_sha256"][str(Path(__file__).relative_to(ROOT))] = base.sha(
        Path(__file__)
    )
    return prompts, bindings, identity, context


def fork_session(context, parent, engine, number, identity):
    if parent["status"] != "generated":
        raise ValueError("부모 응답이 없어 후속 대화를 실행할 수 없습니다.")
    session = copy.deepcopy(parent["response"]["session"])
    session_id = digest([identity, engine, number, parent["entry_sha256"]])[:24]
    session["session_id"] = session_id
    base.dashboard._validate_manual_session(context, session, session_id)
    target = base.dashboard._manual_session_path(context, session_id)
    if target.exists():
        if base.load(target) != session:
            raise ValueError("분기 세션이 이미 변경되어 덮어쓰지 않습니다.")
    else:
        base.dashboard._write_manual_session(context, session)
    return session_id


def save_or_match(path, value):
    if path.is_symlink():
        raise ValueError("산출물 symlink를 허용하지 않습니다.")
    if path.exists():
        if base.load(path) != value:
            raise ValueError("기존 산출물 내용이 달라 덮어쓰지 않습니다.")
    else:
        base.write_new(path, value)


def execute(prompts, bindings, identity, context, output):
    previous_umask = os.umask(0o077)
    try:
        return _execute(prompts, bindings, identity, context, output)
    finally:
        os.umask(previous_umask)


def _execute(prompts, bindings, identity, context, output):
    base.header_check()
    if base._compute_processes() or base._gpu_snapshot()["free_mib"] < 12 * 1024:
        raise ValueError("GPU compute 유휴·12GiB 이상 여유 조건이 필요합니다.")
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("출력 경로 symlink를 허용하지 않습니다.")
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    output.chmod(0o700)
    manifest = output / "build_manifest.json"
    if not manifest.exists() and any(output.iterdir()):
        raise ValueError("manifest 없는 비어 있지 않은 경로입니다.")
    save_or_match(manifest, identity)
    entries = []
    with patch(
        "scripts.training.dashboard_grounding_v2.kst_today",
        return_value=base.FROZEN_DATE,
    ):
        for engine in base.ENGINES:
            completed = {}
            for number, prompt in enumerate(prompts, 1):
                parent_number = PARENTS.get(number)
                parent = completed.get(parent_number)
                common = {
                    "number": number,
                    "engine": engine,
                    "prompt": prompt,
                    "parent_number": parent_number,
                    "parent_entry_sha256": parent["entry_sha256"] if parent else None,
                }
                target = output / f"q{number:02}.{engine}.json"
                pending = output / f"q{number:02}.{engine}.started.json"
                if target.exists():
                    entry = base.load(target)
                    if entry.get("entry_sha256") != base.entry_sha(entry):
                        raise ValueError("재개 응답 SHA가 다릅니다.")
                    if any(entry.get(k) != v for k, v in common.items()):
                        raise ValueError("재개 응답·부모 identity가 다릅니다.")
                else:
                    if pending.exists() or pending.is_symlink():
                        raise ValueError(
                            "완료 기록 없는 시도는 자동 중복 생성하지 않습니다."
                        )
                    session_id = (
                        fork_session(context, parent, engine, number, identity)
                        if parent
                        else None
                    )
                    base.write_new(
                        pending, {k: v for k, v in common.items() if k != "prompt"}
                    )
                    entry = dict(common)
                    try:
                        response = base.dashboard.execute_manual_generation(
                            context,
                            prompt,
                            session_id,
                            engine_selection=engine,
                            runtime_binding=binding_for(number, bindings),
                        )
                    except base.dashboard.DashboardRequestError as exc:
                        if exc.reason_code not in {
                            *EXPECTED_BLOCKS.values(),
                            "RUNTIME_DATE_SELECTION_REQUIRED",
                        }:
                            raise
                        entry.update(
                            status="blocked", code=exc.reason_code, model_invoked=False
                        )
                    else:
                        expected_history = (
                            parent["response"]["session"]["messages"] if parent else []
                        )
                        if response["session"]["messages"][:-2] != expected_history:
                            raise ValueError("다른 대화의 응답이 혼입됐습니다.")
                        entry.update(status="generated", response=response)
                    entry["entry_sha256"] = base.entry_sha(entry)
                    base.write_new(target, entry)
                entries.append(entry)
                completed[number] = entry
                print(
                    json.dumps(
                        {"number": number, "engine": engine, "status": entry["status"]}
                    ),
                    flush=True,
                )
    aggregate = summarize(entries, bindings)
    save_or_match(output / "aggregate.json", aggregate)
    inventory = {
        p.name: base.sha(p)
        for p in sorted(output.glob("*.json"))
        if p.name != "private_manifest.json"
    }
    save_or_match(output / "private_manifest.json", inventory)
    return aggregate


def summarize(entries, bindings):
    models, first = {}, {}
    for engine in base.ENGINES:
        selected = [e for e in entries if e["engine"] == engine]
        generated = [e for e in selected if e["status"] == "generated"]
        blocked = [e for e in selected if e["status"] == "blocked"]
        warnings = Counter()
        bounded, passed, max_input, omitted = 0, 0, 0, 0
        for entry in generated:
            response = entry["response"]
            context = response["contexts"][engine]
            if context["tokenizer_backend_sha256"] != base.BACKEND_SHA256:
                raise ValueError("원본 tokenizer backend가 다릅니다.")
            max_input = max(max_input, context["input_tokens"])
            omitted += context["omitted_turns"]
            if entry["parent_number"] is None:
                first.setdefault(entry["number"], set()).add(
                    (
                        context["rendered_prompt_sha256"],
                        context["input_token_ids_sha256"],
                        context["input_tokens"],
                    )
                )
            binding = binding_for(entry["number"], bindings)
            if binding is not None:
                audit = audit_output(
                    entry["prompt"], response["outputs"][engine], binding
                )
                bounded += 1
                passed += audit["passed"]
                warnings.update(audit["reasons"])
        expected_blocks = all(
            e["status"]
            == ("blocked" if e["number"] in EXPECTED_BLOCKS else "generated")
            and (
                e["status"] != "blocked"
                or e["code"] == EXPECTED_BLOCKS.get(e["number"])
            )
            for e in selected
        )
        models[engine] = {
            "generated": len(generated),
            "pre_generation_blocks": len(blocked),
            "nonempty": sum(
                bool(e["response"]["outputs"][engine].strip()) for e in generated
            ),
            "expected_block_behavior": expected_blocks,
            "bound_generated": bounded,
            "bound_diagnostic_pass": passed,
            "warnings": dict(sorted(warnings.items())),
            "max_input_tokens": max_input,
            "omitted_turns": omitted,
            "block_codes": dict(Counter(e["code"] for e in blocked)),
        }
    parity = len(first) == 12 and all(len(values) == 1 for values in first.values())
    return {
        "schema_version": "1.0.0",
        "diagnostic_version": "synthetic-prompt20-v1.0.0",
        "requests": len(entries),
        "models": models,
        "first_turn_token_parity": parity,
        "first_turn_groups": len(first),
        "execution_contract_met": len(entries) == 60
        and parity
        and all(
            m["generated"] == m["nonempty"] == 18
            and m["pre_generation_blocks"] == 2
            and m["expected_block_behavior"]
            and m["omitted_turns"] == 0
            for m in models.values()
        ),
        "naturalness": "not_measured",
        "semantics": "not_measured",
        "model_quality_approval": False,
        "sealed_blind_accessed": False,
        "production_promotion_allowed": False,
        "runtime_release_changed": False,
        "phase6_changed": False,
        "training_performed": False,
        "system_prompt_changed": False,
        "raw_output_rewrite_count": 0,
    }


def main():
    parser = argparse.ArgumentParser(description="확정 합성 20문장 × 세 모델 진단")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    prompts, bindings, identity, context = prepare(
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
    if os.environ.get("DASHBOARD_PROMPT20_DIAGNOSTIC") != "SYNTHETIC_20_V1":
        raise ValueError(
            "DASHBOARD_PROMPT20_DIAGNOSTIC=SYNTHETIC_20_V1 명시가 필요합니다."
        )
    print(
        json.dumps(
            execute(prompts, bindings, identity, context, args.output),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
