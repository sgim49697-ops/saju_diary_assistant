# dashboard_product_session_v3.py - 제품 후보의 응답 출처·입력 신원과 원자적 세션 저장을 검증한다.

from __future__ import annotations

import fcntl
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone

from scripts.training import dashboard_product_policy_v3 as policy

SESSION_SCHEMA = "1.9.0"
_HASH = re.compile(r"^[0-9a-f]{64}$")


def request_state(d, context, prompt, session_id, binding, *, expected=None):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > context["config"]["server"]["max_prompt_chars"] or d.CONTROL_PATTERN.search(prompt):
        raise d.Phase5DashboardError("현재 요청의 길이·문자 계약이 다릅니다.")
    if binding is not None:
        if context.get("chart_only_runtime_active") is not True:
            raise d.DashboardRequestError(409, "원국 연결 기능이 비활성입니다.", reason_code="RUNTIME_FEATURE_DISABLED")
        d._runtime_model_context_from_binding(binding)
    current = d.manual_session_payload(context, session_id) if session_id is not None else None
    if current is not None:
        if current["schema_version"] != SESSION_SCHEMA:
            raise d.DashboardRequestError(409, "기존 대화는 보존합니다. 후보에서 새 대화를 시작해 주세요.", reason_code="LEGACY_BOUND_SESSION_READ_ONLY")
        if (
            current.get("runtime_binding_sha256") != (binding["capability_sha256"] if binding else None)
            or current.get("runtime_snapshot_sha256") != (binding["snapshot_sha256"] if binding else None)
            or current.get("runtime_state_revision") != (binding["state_revision"] if binding else None)
        ):
            raise d.DashboardRequestError(409, "원국·날짜·revision이 바뀌었습니다. 새 연결 대화를 시작해 주세요.", reason_code="RUNTIME_SESSION_BINDING_MISMATCH")
    token = policy.digest({"session": current, "binding": binding})
    if expected is not None and (not isinstance(expected, str) or not _HASH.fullmatch(expected) or token != expected):
        raise d.DashboardRequestError(409, "요청 이후 대화 상태가 변경됐습니다.", reason_code="RUNTIME_REQUEST_STATE_CHANGED")
    plan = policy.plan_response(prompt, binding, current["messages"] if current else [])
    return plan, current, token


def _valid_hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def validate_session(d, context, value, session_id):
    expected = {
        "schema_version", "session_id", "run_id", "run_build_id", "run_sha256", "title",
        "created_at_utc", "updated_at_utc", "turn_count", "prompt_profile", "system_prompt_sha256",
        "engine_selection", "engine_snapshots", "runtime_binding_sha256", "runtime_snapshot_sha256",
        "runtime_state_revision", "policy_version", "messages", "quality_gate_evaluated",
        "production_promotion_allowed", "binding_snapshot",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise d.Phase5DashboardError("제품 세션 필드 집합이 다릅니다.")
    messages = value["messages"]
    if (
        value["schema_version"] != SESSION_SCHEMA or value["session_id"] != session_id
        or not isinstance(session_id, str) or not d.SESSION_ID_PATTERN.fullmatch(session_id)
        or any(value[key] != context["manifest"][key] for key in ("run_id", "run_build_id", "run_sha256"))
        or value["policy_version"] != policy.POLICY_VERSION
        or value["prompt_profile"] != policy.PROFILE_ID or value["system_prompt_sha256"] != policy.PROMPT_PIN
        or value["engine_selection"] != policy.ENGINE_ID
        or value["engine_snapshots"] != d._selection_snapshots(context, d._inference_selection(context, policy.ENGINE_ID))
        or value["quality_gate_evaluated"] is not False or value["production_promotion_allowed"] is not False
        or type(value["turn_count"]) is not int or not 1 <= value["turn_count"] <= d._manual_session_contract(context)["max_turns_per_session"]
        or not isinstance(messages, list) or len(messages) != value["turn_count"] * 2
        or any(not isinstance(value[k], str) or not value[k] for k in ("title", "created_at_utc", "updated_at_utc"))
    ):
        raise d.Phase5DashboardError("제품 세션 identity·권한 계약이 다릅니다.")
    bound = value["runtime_binding_sha256"] is not None
    if bound:
        if not _valid_hash(value["runtime_binding_sha256"]) or not _valid_hash(value["runtime_snapshot_sha256"]) or type(value["runtime_state_revision"]) is not int or value["runtime_state_revision"] < 1:
            raise d.Phase5DashboardError("제품 세션 binding identity가 다릅니다.")
        snapshot = value["binding_snapshot"]
        d._runtime_model_context_from_binding(snapshot)
        if any(snapshot[k] != value[v] for k, v in (("snapshot_sha256", "runtime_snapshot_sha256"), ("capability_sha256", "runtime_binding_sha256"), ("state_revision", "runtime_state_revision"))):
            raise d.Phase5DashboardError("보존된 원국 snapshot과 대화 연결이 다릅니다.")
    elif value["runtime_snapshot_sha256"] is not None or value["runtime_state_revision"] is not None:
        raise d.Phase5DashboardError("미연결 제품 세션에 원국 identity가 있습니다.")
    elif value["binding_snapshot"] is not None:
        raise d.Phase5DashboardError("미연결 제품 세션에 원국 snapshot이 있습니다.")
    trace_fields = {"policy_version", "response_kind", "response_mode", "selected_paths", "selected_facts_sha256", "history_indices", "active_request_sha256", "snapshot_sha256", "state_revision", "notices"}
    model_fields = {"tokenizer_revision", "tokenizer_backend_sha256", "rendered_prompt_sha256", "input_token_ids_sha256", "model_messages_sha256", "input_tokens", "omitted_turns", "elapsed_seconds", "peak_allocated_bytes", "gpu_total_memory_used_mib", "output_tokens", "stop_reason", "grounding_warnings", "raw_output_preserved"}
    for index in range(0, len(messages), 2):
        user, assistant = messages[index:index + 2]
        if not isinstance(user, dict) or set(user) != {"role", "content", "created_at_utc"} or user["role"] != "user" or not isinstance(user["content"], str) or not user["content"]:
            raise d.Phase5DashboardError("제품 세션 사용자 turn 계약이 다릅니다.")
        if not isinstance(assistant, dict) or set(assistant) != {"role", "content", "created_at_utc", "engine_id", "response_kind", "response_mode", "diagnostics"} or assistant["role"] != "assistant" or not isinstance(assistant["content"], str) or not assistant["content"]:
            raise d.Phase5DashboardError("제품 세션 응답 turn 계약이 다릅니다.")
        kind, mode, trace = assistant["response_kind"], assistant["response_mode"], assistant["diagnostics"]
        model = kind == "model_generated"
        if (
            kind not in policy.KINDS or mode not in policy.MODES
            or (kind == "direct_fact" and mode != "fact_lookup")
            or (kind == "clarification" and mode != "confirmation")
            or (kind == "blocked" and mode != "unsupported")
            or (model and mode not in {"general", "chart_explanation", "day_explanation"})
            or assistant["engine_id"] != (policy.ENGINE_ID if model else None)
            or not isinstance(trace, dict) or set(trace) != trace_fields | (model_fields if model else set())
            or trace["policy_version"] != policy.POLICY_VERSION or trace["response_kind"] != kind or trace["response_mode"] != mode
            or trace["snapshot_sha256"] != value["runtime_snapshot_sha256"] or trace["state_revision"] != value["runtime_state_revision"]
            or not all(_valid_hash(trace[k]) for k in ("selected_facts_sha256", "active_request_sha256"))
            or not isinstance(trace["selected_paths"], list) or any(not isinstance(p, str) or not p.startswith(("chart.", "period")) for p in trace["selected_paths"])
            or not isinstance(trace["notices"], list) or any(not isinstance(n, str) for n in trace["notices"])
            or not isinstance(trace["history_indices"], list)
            or any(type(n) is not int or not 0 <= n < index for n in trace["history_indices"])
            or trace["history_indices"] != sorted(set(trace["history_indices"]))
            or len(trace["history_indices"]) % 2 != 0
            or any(not isinstance(m["created_at_utc"], str) or not m["created_at_utc"] for m in (user, assistant))
            or (mode == "general" and (trace["selected_paths"] or trace["selected_facts_sha256"] != policy.digest({})))
        ):
            raise d.Phase5DashboardError("제품 응답 출처·선택 trace가 다릅니다.")
        source = value["binding_snapshot"]["value"] if bound else {}
        if trace["selected_facts_sha256"] != policy.digest(policy.project(source, trace["selected_paths"])):
            raise d.Phase5DashboardError("보존된 원국과 선택 facts trace가 다릅니다.")
        if kind == "direct_fact":
            labels = [label for label, path in policy.FIELD_PATHS.items() if trace["selected_paths"] == [path]]
            if len(labels) != 1 or assistant["content"] != policy.render_fact(user["content"], labels[0], policy.get_path(source, policy.FIELD_PATHS[labels[0]])):
                raise d.Phase5DashboardError("직접 응답 원문과 승인 필드 값이 다릅니다.")
        if model and (
            trace["tokenizer_revision"] != d.TOKENIZER_REVISION or trace["tokenizer_backend_sha256"] != d.BACKEND_SHA256
            or not all(_valid_hash(trace[k]) for k in ("rendered_prompt_sha256", "input_token_ids_sha256", "model_messages_sha256"))
            or trace["raw_output_preserved"] is not True
            or trace["stop_reason"] not in {"eos", "max_tokens"}
            or any(type(trace[k]) is not int or trace[k] < 0 for k in ("input_tokens", "output_tokens", "omitted_turns", "peak_allocated_bytes"))
            or not 1 <= trace["input_tokens"] <= 4096 or not 1 <= trace["output_tokens"] <= 4096
            or (trace["gpu_total_memory_used_mib"] is not None and (type(trace["gpu_total_memory_used_mib"]) not in {int, float} or not math.isfinite(trace["gpu_total_memory_used_mib"]) or trace["gpu_total_memory_used_mib"] < 0))
            or not isinstance(trace["elapsed_seconds"], (int, float)) or isinstance(trace["elapsed_seconds"], bool) or not math.isfinite(trace["elapsed_seconds"]) or trace["elapsed_seconds"] < 0
            or not isinstance(trace["grounding_warnings"], list) or any(not isinstance(n, str) for n in trace["grounding_warnings"])
        ):
            raise d.Phase5DashboardError("제품 모델 응답 실행 identity가 다릅니다.")
    return value


@contextmanager
def write_lock(d, context):
    root = d._manual_session_root(context, create=True)
    descriptor = os.open(root / ".product-write.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise d.Phase5DashboardError("제품 세션 잠금 파일이 안전하지 않습니다.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise d.DashboardRequestError(409, "다른 응답을 저장 중입니다.", reason_code="RUNTIME_REQUEST_STATE_CHANGED") from exc
        yield
    finally:
        os.close(descriptor)


def execute(d, context, prompt, session_id=None, profile=None, engine_selection=None, runtime_session_id=None, runtime_binding=None, *, expected_request_state_sha256=None, defer_persistence=False):
    if runtime_session_id is not None or profile not in {None, policy.PROFILE_ID} or engine_selection not in {None, policy.ENGINE_ID} or type(defer_persistence) is not bool:
        raise d.Phase5DashboardError("제품 후보는 현재 검증 binding·고정 profile·R16 단독만 허용합니다.")
    plan, previous, state = request_state(d, context, prompt, session_id, runtime_binding, expected=expected_request_state_sha256)
    if previous and previous["turn_count"] >= d._manual_session_contract(context)["max_turns_per_session"]:
        raise d.Phase5DashboardError("최대 대화 turn에 도달했습니다. 새 대화를 시작해 주세요.")
    old_messages = deepcopy(previous["messages"] if previous else [])
    trace = plan.trace(runtime_binding)
    now = datetime.now(timezone.utc).isoformat()
    answer = plan.answer
    if plan.kind == "model_generated":
        gate = d._generation_gate(context)
        if not gate["allowed"]:
            raise d.DashboardRequestError(409, "모델 생성 준비가 되지 않았습니다. 직접 사실 조회는 사용할 수 있습니다.", reason_code=d.GPU_BUSY_CODE)
        availability = d._engine_availability(context, policy.ENGINE_ID)
        if not availability["available"]:
            raise d.Phase5DashboardError("고정 R16 후보를 사용할 수 없습니다.")
        from scripts.training.mix2k_v4_lora import (
            Mix2KV4LoRAError,
            acquire_mix2k_v4_gpu_lock,
        )

        try:
            descriptor = acquire_mix2k_v4_gpu_lock(context["repo_root"])
        except Mix2KV4LoRAError as exc:
            raise d.Phase5DashboardError(f"{d.GPU_BUSY_CODE}: 후보 생성 잠금을 얻지 못했습니다.") from exc
        sent = policy.model_messages(plan, old_messages)
        started = time.monotonic()
        try:
            generated = d._generate_engine_conversation(context, policy.ENGINE_ID, sent)
        finally:
            os.close(descriptor)
        elapsed = round(time.monotonic() - started, 3)
        omitted = generated.get("omitted_messages")
        if type(omitted) is not int or omitted < 0 or omitted % 2 or omitted > len(plan.history_indices):
            raise d.Phase5DashboardError("모델 입력의 제외 이력 수가 다릅니다.")
        actual_messages = [sent[0], *sent[1 + omitted:]]
        if generated.get("model_messages_sha256") != policy.digest(actual_messages):
            raise d.Phase5DashboardError("실제 모델 입력과 제품 선택 입력이 다릅니다.")
        answer = generated["output"]
        warnings = []
        if runtime_binding is not None:
            audited = d.audit_bound_output(plan.active_request, answer, runtime_binding, prior_user_request=next((m["content"] for m in reversed(old_messages) if m["role"] == "user"), None))
            warnings = audited["reasons"]
        if plan.mode == "general" and re.search(r"사주|원국|일간|일주|운세|일진|용신", answer):
            warnings = [*warnings, "unsolicited_saju"]
        if generated["stop_reason"] == "max_tokens":
            warnings = [*warnings, "output_limit_reached"]
        trace.update({k: generated[k] for k in ("tokenizer_revision", "tokenizer_backend_sha256", "rendered_prompt_sha256", "input_token_ids_sha256", "model_messages_sha256", "input_tokens", "output_tokens", "stop_reason")})
        trace.update(omitted_turns=omitted // 2, elapsed_seconds=elapsed, peak_allocated_bytes=generated.get("peak_allocated_bytes", 0), gpu_total_memory_used_mib=generated.get("gpu_total_memory_used_mib"), grounding_warnings=warnings, raw_output_preserved=True)
    completed = datetime.now(timezone.utc).isoformat()
    new_id = session_id or secrets.token_hex(12)
    session = {
        "schema_version": SESSION_SCHEMA, "session_id": new_id,
        **{key: context["manifest"][key] for key in ("run_id", "run_build_id", "run_sha256")},
        "title": previous["title"] if previous else prompt.strip().splitlines()[0][:d._manual_session_contract(context)["title_max_chars"]],
        "created_at_utc": previous["created_at_utc"] if previous else now,
        "updated_at_utc": completed, "turn_count": (previous["turn_count"] if previous else 0) + 1,
        "prompt_profile": policy.PROFILE_ID, "system_prompt_sha256": policy.PROMPT_PIN,
        "engine_selection": policy.ENGINE_ID,
        "engine_snapshots": d._selection_snapshots(context, d._inference_selection(context, policy.ENGINE_ID)),
        "runtime_binding_sha256": runtime_binding["capability_sha256"] if runtime_binding else None,
        "runtime_snapshot_sha256": runtime_binding["snapshot_sha256"] if runtime_binding else None,
        "runtime_state_revision": runtime_binding["state_revision"] if runtime_binding else None,
        "binding_snapshot": deepcopy(runtime_binding),
        "policy_version": policy.POLICY_VERSION,
        "messages": old_messages + [
            {"role": "user", "content": prompt.strip(), "created_at_utc": now},
            {"role": "assistant", "content": answer, "created_at_utc": completed, "engine_id": policy.ENGINE_ID if plan.kind == "model_generated" else None, "response_kind": plan.kind, "response_mode": plan.mode, "diagnostics": trace},
        ],
        "quality_gate_evaluated": False, "production_promotion_allowed": False,
    }
    validate_session(d, context, session, new_id)
    result = {
        "status": "generated" if plan.kind == "model_generated" else "responded",
        "response_kind": plan.kind, "response_mode": plan.mode, "reason_code": plan.reason_code,
        "output": answer, "outputs": {policy.ENGINE_ID: answer} if plan.kind == "model_generated" else {},
        "contexts": {policy.ENGINE_ID: trace} if plan.kind == "model_generated" else {},
        "notices": list(plan.notices), "session_id": new_id, "session": session,
        "runtime_binding_applied": runtime_binding is not None,
        "runtime_snapshot_sha256": runtime_binding["snapshot_sha256"] if runtime_binding else None,
        "request_state_sha256": state, "persisted": False, "local_only": True,
        "model_calls": int(plan.kind == "model_generated"), "production_promotion_allowed": False,
    }
    if not defer_persistence:
        return commit_result(d, context, result, prompt, session_id, runtime_binding, expected=state)
    return result


def commit_result(d, context, result, prompt, session_id, binding, *, expected):
    with write_lock(d, context):
        plan, previous, state = request_state(d, context, prompt, session_id, binding, expected=expected)
        if not isinstance(result, dict) or result.get("request_state_sha256") != state or result.get("persisted") is not False or result.get("local_only") is not True:
            raise d.Phase5DashboardError("제품 worker 결과의 요청 identity가 다릅니다.")
        session = result.get("session")
        validate_session(d, context, session, result.get("session_id"))
        prefix = previous["messages"] if previous else []
        assistant = session["messages"][-1]
        trace = assistant["diagnostics"]
        if (
            session["messages"][:-2] != prefix or session["messages"][-2]["content"] != prompt.strip()
            or (session_id is not None and session["session_id"] != session_id)
            or session["turn_count"] != (previous["turn_count"] if previous else 0) + 1
            or any(trace[k] != v for k, v in plan.trace(binding).items())
            or result.get("output") != assistant["content"] or result.get("response_kind") != plan.kind or result.get("response_mode") != plan.mode
            or (plan.kind != "model_generated" and result["output"] != plan.answer)
            or type(result.get("model_calls")) is not int or result["model_calls"] != int(plan.kind == "model_generated")
            or result.get("runtime_binding_applied") is not (binding is not None)
            or result.get("runtime_snapshot_sha256") != (binding["snapshot_sha256"] if binding else None)
            or result.get("notices") != list(plan.notices)
            or result.get("reason_code") != plan.reason_code
            or result.get("outputs") != ({policy.ENGINE_ID: assistant["content"]} if plan.kind == "model_generated" else {})
            or result.get("contexts") != ({policy.ENGINE_ID: trace} if plan.kind == "model_generated" else {})
            or result.get("production_promotion_allowed") is not False
        ):
            raise d.Phase5DashboardError("제품 worker 결과의 입력·출처·원문 대조에 실패했습니다.")
        if previous is None and (d._manual_session_path(context, session["session_id"]).exists() or len(d.manual_sessions_payload(context)["items"]) >= d._manual_session_contract(context)["max_sessions"]):
            raise d.Phase5DashboardError("새 제품 세션 경로 충돌 또는 세션 한도 초과입니다.")
        d._write_manual_session(context, session)
        return {**result, "session": d.manual_session_payload(context, session["session_id"]), "persisted": True}


def http_generate(handler, d, payload):
    server, context = handler.server, handler.server.context
    allowed = {"prompt", "session_id", "profile", "engine_selection", "runtime_session_id", "runtime_binding_scope"}
    if not isinstance(payload, dict) or not {"prompt", "session_id"}.issubset(payload) or not set(payload) <= allowed or not isinstance(payload["prompt"], str) or any(payload.get(k) is not None and not isinstance(payload[k], str) for k in allowed - {"prompt"}):
        raise d.DashboardRequestError(400, "제품 요청 형식이 다릅니다.", reason_code="PRODUCT_REQUEST_INVALID")
    if payload.get("profile") not in {None, policy.PROFILE_ID} or payload.get("engine_selection") not in {None, policy.ENGINE_ID}:
        raise d.DashboardRequestError(400, "R16 제품 후보의 구성은 고정되어 있습니다.", reason_code="PRODUCT_CANDIDATE_FIXED")
    scope = payload.get("runtime_binding_scope") or "chart_day"
    if scope not in {"chart", "chart_day"}:
        raise d.DashboardRequestError(400, "원국 연결 범위가 다릅니다.", reason_code="PRODUCT_BINDING_SCOPE_INVALID")
    handler._rate_limit("model_generation")
    runtime_id = payload.get("runtime_session_id")
    owner = handler._chart_only_binding() if runtime_id else None
    binding = handler._binding_request(owner.public_snapshot, runtime_id, scope=scope) if owner else None
    plan, _, state = request_state(d, context, payload["prompt"], payload["session_id"], binding)
    args = (context, payload["prompt"], payload["session_id"], payload.get("profile"), payload.get("engine_selection"), None, binding)
    if plan.kind == "model_generated":
        runner = server.generation_runner or d._manual_generation_subprocess
        result = runner(*args, expected_request_state_sha256=state, defer_persistence=True)
    else:
        result = execute(d, *args, expected_request_state_sha256=state, defer_persistence=True)
    if owner is None:
        return commit_result(d, context, result, payload["prompt"], payload["session_id"], binding, expected=state)
    # 현재 권한 원본을 다시 읽고 binding operation lock 안에서 최종 저장한다.
    def finish():
        with owner.guard_snapshot(runtime_id, binding, scope=scope):
            return commit_result(d, context, result, payload["prompt"], payload["session_id"], binding, expected=state)
    return handler._binding_request(finish)
