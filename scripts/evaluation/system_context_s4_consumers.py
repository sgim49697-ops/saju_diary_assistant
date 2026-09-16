# system_context_s4_consumers.py - 동일 tokenizer의 증명된 별칭만 사용해 실제 저장/API 경로를 CPU 검증한다.

from copy import deepcopy

from scripts.evaluation.system_context_backend import replay_output_consumers
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_s4_models import ENGINES
from scripts.evaluation.system_context_s4_projection import input_identity
from scripts.training.dashboard_tokenizer_v1 import (
    BACKEND_SHA256,
    TOKENIZER_FILES,
    TOKENIZER_REVISION,
)
from scripts.training.dashboard_tokenizer_v1 import (
    input_identity as canonical_identity,
)

IDENTITY_KEYS = (
    "tokenizer_revision", "tokenizer_backend_sha256",
    "rendered_prompt_sha256", "input_token_ids_sha256",
)


def alias_receipt(request):
    render = request["render"]
    if (
        request["engine"] not in ENGINES
        or render["tokenizer_revision"] != f"s4-{request['engine']}-official-v1.0.0"
        or render["tokenizer_backend_sha256"] != BACKEND_SHA256
    ):
        raise ValueError("S4 저장 별칭으로 증명되지 않은 tokenizer입니다.")
    return {
        "policy": "identical_files_backend_template_and_request_tokens_only_v1.1.0",
        "source_revision": render["tokenizer_revision"],
        "storage_revision": TOKENIZER_REVISION,
        "tokenizer_files_sha256": digest(TOKENIZER_FILES),
        **{key: render[key] for key in IDENTITY_KEYS if key != "tokenizer_revision"},
    }


def replay_consumers(context, request, generated, tokenizer, canonical, verified_files):
    """모델 identity는 보존하고 검증된 사본의 저장 별칭만 변환한다."""
    receipt = alias_receipt(request)
    engine, render = request["engine"], request["render"]
    for key in {"k0_instruct", engine}:
        if {name: verified_files[key].get(name) for name in TOKENIZER_FILES} != TOKENIZER_FILES:
            raise ValueError("S4 저장 별칭의 tokenizer 파일 증명이 다릅니다.")
    if tokenizer.chat_template != canonical.chat_template:
        raise ValueError("S4 저장 별칭의 실제 template이 다릅니다.")
    messages, ids = render["messages"], render["input_token_ids"]
    for current in (tokenizer, canonical):
        if current.apply_chat_template(messages, tokenize=True, add_generation_prompt=True) != ids:
            raise ValueError("S4 저장 별칭의 실제 입력 token이 다릅니다.")
    actual = input_identity(tokenizer, messages, ids, engine=engine, expected_backend=BACKEND_SHA256)
    storage = canonical_identity(canonical, messages, ids)
    if (
        any(actual[key] != render[key] or actual[key] != generated[key] for key in IDENTITY_KEYS)
        or any(actual[key] != storage[key] for key in IDENTITY_KEYS if key != "tokenizer_revision")
        or generated["input_tokens"] != len(ids)
        or generated["omitted_messages"] != 0
    ):
        raise ValueError("S4 생성·동결·저장 입력 identity가 다릅니다.")
    local = deepcopy(generated)
    local["tokenizer_revision"] = storage["tokenizer_revision"]
    result = replay_output_consumers(context, request["case"], "k0_instruct", local)
    return {
        **result, "consumer_path": "v1.15_k0_slot_cpu_replay_not_3b_app_integration",
        "model_engine": engine, "storage_engine_slot": "k0_instruct",
        "tokenizer_alias": receipt,
    }


def preflight(context, requests, tokenizers, verified_files):
    """생성 예정 전 건의 실제 저장·API 코드를 합성 응답으로 실행한다. 모델 호출은 0이다."""
    receipts = []
    for request in requests:
        if request["case"]["expected_block"]:
            continue
        render = request["render"]
        generated = {
            **{key: render[key] for key in IDENTITY_KEYS},
            "input_tokens": render["input_tokens"], "omitted_messages": 0,
            "output": "합성 응답으로 저장과 표시의 일치 여부를 확인합니다.",
            "peak_allocated_bytes": 1024, "gpu_total_memory_used_mib": 1024,
        }
        receipt = replay_consumers(
            context, request, generated, tokenizers[request["engine"]],
            tokenizers["k0_instruct"], verified_files,
        )
        receipts.append({"request_id": request["request_id"], "receipt": receipt})
    if len(receipts) != 172:
        raise ValueError("S4 실제 CPU 소비 사전 검증 분모가 다릅니다.")
    return {
        "status": "passed", "actual_storage_api_replays": len(receipts),
        "model_calls": 0, "gpu_used": False, "browser_executed": False,
        "synthetic_output_only": True, "receipts_sha256": digest(receipts),
    }
