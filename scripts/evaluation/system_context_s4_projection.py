# system_context_s4_projection.py - 동결 P0/FULL·MIN 메시지를 각 모델의 공식 tokenizer로 독립 렌더링한다.

import hashlib
import json
from copy import deepcopy

from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_projection import (
    _runtime_parts,
    canonical,
    project,
)
from scripts.training.dashboard_tokenizer_v1 import backend_sha256


def input_identity(tokenizer, messages, ids, *, engine, expected_backend):
    backend = backend_sha256(tokenizer)
    if backend != expected_backend:
        raise ValueError("등록한 모델 자체 tokenizer backend 불일치")
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return {
        "tokenizer_revision": f"s4-{engine}-official-v1.0.0",
        "tokenizer_backend_sha256": backend,
        "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "input_token_ids_sha256": hashlib.sha256(json.dumps(ids, separators=(",", ":")).encode()).hexdigest(),
    }


def runtime_sections(case, arm):
    if arm not in {"C_FULL", "C_MIN"}:
        raise ValueError("S4에 등록되지 않은 정보 조건입니다.")
    if case["binding"] is None:
        return None
    prefix, full_data = _runtime_parts(case["binding"])
    data = full_data if arm == "C_FULL" else canonical(project(case["binding"]["value"], case["required_paths"]))
    return {"runtime": prefix + data, "selected_data": data}


def retokenize(original, tokenizer, *, engine, expected_backend, full_input_tokens=None, sections=None):
    result = deepcopy(original)
    messages = result["messages"]
    ids = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
    if len(ids) > 4096 or not ids:
        raise ValueError("S4 무삭제 입력 상한 위반")
    rendered = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    encoded = tokenizer(rendered, add_special_tokens=False, return_offsets_mapping=True)
    if encoded["input_ids"] != ids:
        raise ValueError("S4 token/문자 위치 추적 불일치")
    def span(start, end):
        covered = [i for i, (left, right) in enumerate(encoded["offset_mapping"]) if left < end and right > start]
        return {
            "chars_start": start, "chars_end": end,
            "token_start": min(covered) if covered else None,
            "token_end_exclusive": max(covered) + 1 if covered else None,
            "prefix_tokens": len(tokenizer(rendered[:start], add_special_tokens=False)["input_ids"]),
            "content_tokens": len(tokenizer(rendered[start:end], add_special_tokens=False)["input_ids"]),
        }

    segments, cursor = [], 0
    for index, message in enumerate(messages):
        start = rendered.find(message["content"], cursor)
        if start < 0:
            raise ValueError("S4 모델별 template의 메시지 위치 누락")
        end = start + len(message["content"])
        segments.append({
            "index": index, "role": message["role"],
            "segment": "system" if index == 0 else "current_user" if index == len(messages) - 1 else "frozen_history",
            **span(start, end),
        })
        cursor = end
    detail_names = {s["segment"] for s in original.get("segments", [])} & {"p0", "runtime", "selected_data"}
    if detail_names and sections is None:
        raise ValueError("S4 재토큰화에서 지시문·계산 facts 위치 추적을 생략할 수 없습니다.")
    if sections is not None:
        runtime, data = sections["runtime"], sections["selected_data"]
        if (
            messages[0]["role"] != "system" or not runtime or not data
            or not messages[0]["content"].endswith(runtime) or not runtime.endswith(data)
        ):
            raise ValueError("S4 동결 메시지와 원국/일진 직렬화 구간이 다릅니다.")
        start, end = segments[0]["chars_start"], segments[0]["chars_end"]
        runtime_start, data_start = end - len(runtime), end - len(data)
        segments.extend([
            {"segment": "p0", **span(start, runtime_start)},
            {"segment": "runtime", **span(runtime_start, end)},
            {"segment": "selected_data", **span(data_start, end)},
        ])
    result.update(
        input_token_ids=ids, input_tokens=len(ids), full_input_tokens=full_input_tokens or len(ids),
        segments=segments, omitted_messages=0,
        **input_identity(tokenizer, messages, ids, engine=engine, expected_backend=expected_backend),
    )
    if result["messages"] != original["messages"] or result["selected_paths"] != original["selected_paths"]:
        raise ValueError("S4 모델 교체가 메시지·정보 선택을 바꿨습니다.")
    return result


def compare_inputs(left, right, *, case_id, arm):
    for key in ("messages", "selected_paths", "excluded_paths", "p0_sha256", "projection_schema"):
        if left[key] != right[key]:
            raise ValueError("S4 두 모델의 비모델 입력이 다릅니다.")
    return {
        "case_id": case_id, "arm": arm, "messages_sha256": digest(left["messages"]),
        "selected_paths_sha256": digest(left["selected_paths"]), "p0_sha256": left["p0_sha256"],
        "k0_input_tokens": left["input_tokens"], "kanana3b_input_tokens": right["input_tokens"],
        "token_ids_equal_observed": left["input_token_ids"] == right["input_token_ids"],
        "token_ids_equal_required": False, "messages_equal": True,
    }
