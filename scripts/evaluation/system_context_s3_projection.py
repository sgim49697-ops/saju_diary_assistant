# system_context_s3_projection.py - S2 동결 원문에서 시스템 지시 두 구간만 바꾸고 나머지 byte·token을 검사한다.

from copy import deepcopy

from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_contracts import REPO_ROOT, safe_path
from scripts.evaluation.system_context_projection import (
    _runtime_parts,
    canonical,
    render,
)
from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_tokenizer_v1 import input_identity


def instruction_bundle(config):
    return {
        "profile": safe_path(REPO_ROOT / config["p1_profile"]).read_text(encoding="utf-8").strip(),
        "runtime_prefix": safe_path(REPO_ROOT / config["p1_runtime_prefix"]).read_text(encoding="utf-8").strip() + "\n",
    }


def render_pair(parent_request, context, tokenizer, bundle):
    case = parent_request["case"]
    original = render(case, "C_FULL", context, tokenizer)
    if digest(original) != digest(parent_request["render"]):
        raise ValueError("S3 P0 입력이 S2 동결 원문·token과 다릅니다.")
    p1 = deepcopy(original)
    prefix, data = _runtime_parts(case["binding"])
    if case["binding"]:
        expected_data = canonical(case["binding"]["value"])
        if data != expected_data or not original["messages"][0]["content"].endswith(prefix + data):
            raise ValueError("P0 지시/JSON 구간 복원 실패")
        messages = dashboard._messages_for_engine(
            case["history"], "k0_instruct", case["prompt"], bundle["profile"],
            bundle["runtime_prefix"] + data,
        )
        if messages[1:] != original["messages"][1:] or not messages[0]["content"].endswith(data):
            raise ValueError("P1 변경이 지시문 밖으로 확장됐습니다.")
        if messages[0]["role"] != "system":
            raise ValueError("P1 역할 변경")
        ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
        if len(ids) > 4096:
            raise ValueError("P1 무삭제 입력 상한 초과")
        rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        encoded = tokenizer(rendered, add_special_tokens=False, return_offsets_mapping=True)
        if encoded["input_ids"] != ids:
            raise ValueError("P1 최종 token 추적 불일치")
        segments, cursor = [], 0
        for index, message in enumerate(messages):
            start = rendered.find(message["content"], cursor)
            if start < 0:
                raise ValueError("P1 message 위치 누락")
            end = start + len(message["content"])
            covered = [i for i, (left, right) in enumerate(encoded["offset_mapping"]) if left < end and right > start]
            segments.append({
                "index": index, "role": message["role"],
                "segment": "system" if index == 0 else "current_user" if index == len(messages) - 1 else "frozen_history",
                "chars_start": start, "chars_end": end,
                "token_start": min(covered) if covered else None,
                "token_end_exclusive": max(covered) + 1 if covered else None,
            })
            cursor = end
        p1.update(
            messages=messages, input_token_ids=ids, input_tokens=len(ids),
            segments=segments, **input_identity(tokenizer, messages, ids),
        )
        # p0_sha256는 변경 전 부모 지시문 pin이다. P1은 묶음 identity로 별도 기록한다.
    if p1["messages"][1:] != original["messages"][1:] or p1["selected_paths"] != original["selected_paths"]:
        raise ValueError("P1 비지시 입력 변경")
    comparison = {
        "case_id": case["case_id"],
        "stratum": case["stratum"],
        "p0_input_tokens": original["input_tokens"],
        "p1_input_tokens": p1["input_tokens"],
        "input_token_delta": p1["input_tokens"] - original["input_tokens"],
        "instruction_changed": bool(case["binding"]),
        "changed_segments": ["bound_profile", "runtime_prefix"] if case["binding"] else [],
        "fact_json_sha256": digest(data),
        "non_system_messages_sha256": digest(original["messages"][1:]),
        "p0_system_sha256": digest(original["messages"][0]),
        "p1_system_sha256": digest(p1["messages"][0]),
        "p1_bundle_sha256": digest(bundle),
        "fact_json_unchanged": True,
        "frozen_history_unchanged": True,
    }
    return {"P0": original, "P1": p1}, comparison
