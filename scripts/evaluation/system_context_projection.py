# system_context_projection.py - P0와 기존 전체 입력을 보존하며 별도 진단 projection을 검증한다.

from __future__ import annotations

import json
from copy import deepcopy

from scripts.evaluation.system_context_cases import FROZEN_DATE, digest
from scripts.training import phase5_dashboard_v1_15 as dashboard
from scripts.training.dashboard_grounding_v2 import date_scope, prompt_intent
from scripts.training.dashboard_tokenizer_v1 import input_identity

STEMS = "甲乙丙丁戊己庚辛壬癸"
NAMES = ("갑목", "을목", "병화", "정화", "무토", "기토", "경금", "신금", "임수", "계수")
AUTHORITY_PATHS = (
    "chart.status",
    "chart.fact_authority",
    "chart.message",
    "chart.limitations",
    "chart.hard_facts.calculation_profile",
    "chart.hard_facts.solar_term_evidence.authority_classes",
    "chart.hard_facts.solar_term_evidence.overall_authority",
    "chart.hard_facts.solar_term_evidence.contains_future_nonapproval",
    "chart.hard_facts.solar_term_evidence.provider_generated_value_is_official",
    "chart.hard_facts.solar_term_evidence.provider_id",
    "chart.hard_facts.solar_term_evidence.root_time_scale",
    "chart.hard_facts.solar_term_evidence.official_label_coordinate",
    "chart.hard_facts.solar_term_evidence.official_snapshot_collected_at",
    "chart.hard_facts.solar_term_evidence.schema_version",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def get_path(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"필수 경로 누락: {path}")
        value = value[part]
    return value


def leaf_paths(value, prefix=""):
    if isinstance(value, dict) and value:
        return [
            path
            for key, child in value.items()
            for path in leaf_paths(child, f"{prefix}.{key}" if prefix else key)
        ]
    return [prefix]


def required_paths(case):
    task = case["task"]
    if task in ("fact", "premise", "day"):
        paths = ["chart.hard_facts.day_master", "chart.hard_facts.pillars.day"]
    elif task == "uncertainty":
        paths = ["chart.hard_facts.pillars.hour"]
    else:
        paths = []
    if task == "day":
        paths.append("period")
    return paths


def project(value, required):
    selected = list(AUTHORITY_PATHS) + list(required)
    result = {}
    for path in selected:
        part = deepcopy(get_path(value, path))
        cursor = result
        segments = path.split(".")
        for segment in segments[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[segments[-1]] = part
    for path in leaf_paths(result):
        original, projected = get_path(value, path), get_path(result, path)
        if type(original) is not type(projected) or original != projected:
            raise ValueError("projection 필드 값/형식이 원본과 다릅니다.")
    return result


def expected_facts(binding):
    if binding is None:
        return {}
    facts = binding["value"]["chart"]["hard_facts"]
    expected = {
        f"natal_{key}": value.get("ganzhi") if isinstance(value, dict) else None
        for key, value in facts.get("pillars", {}).items()
    }
    expected["day_master"] = facts.get("day_master", {}).get("stem")
    period = binding["value"].get("period", {}).get("hard_facts", {}).get("period", {})
    expected.update(
        {
            "period_day": period.get("day_ganzhi"),
            "target_date": period.get("target_date"),
        }
    )
    return expected


def resolve_case(spec, fixtures):
    case = deepcopy(spec)
    fixture = fixtures.get(case["fixture"], {})
    binding = fixture.get("binding")
    expected = expected_facts(binding)
    master = expected.get("day_master", "丁")
    wrong = NAMES[(STEMS.index(master) + 3) % 10]
    case["prompt"] = case["prompt"].format(wrong=wrong)
    for turn in case["history"]:
        turn["content"] = turn["content"].format(wrong=wrong)
    if case["stratum"] == "correction":
        old = fixture["parent_value"]["chart"]["hard_facts"]
        case["history"] = [
            {"role": "user", "content": "정정하기 전 원국의 일간과 일주를 알려줘."},
            {
                "role": "assistant",
                "content": f"당시 원국의 일간은 {old['day_master']['stem']}이고 일주는 {old['pillars']['day']['ganzhi']}입니다.",
            },
        ]
    case["expected"] = expected
    case["required_paths"] = required_paths(case) if binding else []
    # 예상 사전 차단 입력은 없는 period를 만들어 비교하지 않는다.
    if case["expected_block"] and binding and "period" not in binding["value"]:
        case["required_paths"] = [p for p in case["required_paths"] if p != "period"]
    case["expected_roles"] = (["day_master"] if "일간" in case["prompt"] else []) + (
        ["natal_day"] if "일주" in case["prompt"] else []
    )
    if case["task"] == "day":
        case["expected_roles"] = ["natal_day", "period_day"]
    case["binding"] = binding
    case["state_trace"] = {
        key: value
        for key, value in fixture.items()
        if key not in {"binding", "parent_value"}
    }
    case["date_scope"] = date_scope(case["prompt"], binding, today=FROZEN_DATE)
    if case["date_scope"]["reason_code"] != case["expected_block"]:
        raise ValueError(
            f"등록한 사전 차단과 실제 앱 판정이 다릅니다: {case['case_id']}"
        )
    case["observed_intent"] = prompt_intent(case["prompt"])
    return case


def _runtime_parts(binding):
    if binding is None:
        return "", ""
    full, _, _ = dashboard._runtime_model_context_from_binding(binding)
    data = canonical(binding["value"])
    if not full.endswith(data):
        raise ValueError("v1.15 원본 직렬화가 변경됐습니다.")
    return full[: -len(data)], data


def _messages(case, context, runtime):
    profiles = context["prompt_profiles"]
    key = profiles["bound_profile"] if case["binding"] else profiles["default_profile"]
    prompt = dashboard._prompt_profile(context, key)["system_prompt_text"]
    return dashboard._messages_for_engine(
        case["history"], "k0_instruct", case["prompt"], prompt, runtime or None
    )


def render(case, arm, context, tokenizer):
    prefix, full_data = _runtime_parts(case["binding"])
    value = case["binding"]["value"] if case["binding"] else {}
    full_messages = _messages(case, context, prefix + full_data)
    full_ids = tokenizer.apply_chat_template(
        full_messages, tokenize=True, add_generation_prompt=True
    )
    minimal = project(value, case["required_paths"]) if value else {}
    minimum_data = canonical(minimal) if value else ""
    runtime = prefix + (full_data if arm == "C_FULL" else minimum_data)
    padding = []
    if arm in ("C_PAD", "C_POS_FRONT", "C_POS_MIDDLE", "C_POS_END"):
        if not value:
            raise ValueError("위치 대조에는 계산 사실이 필요합니다.")

        def pad_context(items, selected_arm):
            # facts 묶음과 filler byte를 그대로 이동한다. 권한/role/P0는 밖에 고정한다.
            chunk = "[DATA]\n" + minimum_data + "\n[/DATA]"
            position = (
                0
                if selected_arm in ("C_PAD", "C_POS_FRONT")
                else len(items) // 2
                if selected_arm == "C_POS_MIDDLE"
                else len(items)
            )
            return prefix + "\n".join([*items[:position], chunk, *items[position:]])

        target = len(full_ids)
        candidates = []
        for count in range(1, 501):
            items = [
                f'{{"reference_item":{index},"object":"도서관 보관함","color":"연두색","label":"합성 참고자료"}}'
                for index in range(count)
            ]
            trial = pad_context(items, "C_PAD")
            length = len(
                tokenizer.apply_chat_template(
                    _messages(case, context, trial),
                    tokenize=True,
                    add_generation_prompt=True,
                )
            )
            candidates.append((abs(length - target), items))
            if length >= target:
                break
        padding = min(candidates, key=lambda item: item[0])[1]
        # 작은 단위 filler로 1% 허용 범위에 맞춘다. 모든 위치 arm에서 같은 filler다.
        while True:
            current = len(
                tokenizer.apply_chat_template(
                    _messages(case, context, pad_context(padding, "C_PAD")),
                    tokenize=True,
                    add_generation_prompt=True,
                )
            )
            if current >= target - 2:
                break
            padding = [*padding, "참고"]
        runtime = pad_context(padding, arm)
    messages = _messages(case, context, runtime)
    ids = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True
    )
    if len(ids) > 4096:
        raise ValueError(f"무삭제 입력 상한 초과: {case['case_id']} {arm}")
    if padding and abs(len(ids) - len(full_ids)) / len(full_ids) > 0.01:
        raise ValueError("padding 길이 ±1% 계약 위반")
    selected = leaf_paths(value if arm == "C_FULL" else minimal) if value else []
    excluded = sorted(set(leaf_paths(value)) - set(selected)) if value else []
    rendered = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    encoded = tokenizer(rendered, add_special_tokens=False, return_offsets_mapping=True)
    if encoded["input_ids"] != ids:
        raise ValueError("토큰 위치 추적과 실제 입력이 다릅니다.")
    offsets = encoded["offset_mapping"]

    def token_span(start, end):
        covered = [
            i for i, (left, right) in enumerate(offsets) if left < end and right > start
        ]
        return (
            {"token_start": min(covered), "token_end_exclusive": max(covered) + 1}
            if covered
            else {"token_start": None, "token_end_exclusive": None}
        )

    segments = []
    cursor = 0
    for index, message in enumerate(messages):
        start = rendered.find(message["content"], cursor)
        if start < 0:
            raise ValueError("렌더링 메시지 구간 복원 실패")
        end = start + len(message["content"])
        segments.append(
            {
                "index": index,
                "role": message["role"],
                "segment": "system"
                if index == 0
                else "current_user"
                if index == len(messages) - 1
                else "frozen_history",
                "chars_start": start,
                "chars_end": end,
                **token_span(start, end),
                "prefix_tokens": len(
                    tokenizer.encode(rendered[:start], add_special_tokens=False)
                ),
                "content_tokens": len(
                    tokenizer.encode(message["content"], add_special_tokens=False)
                ),
            }
        )
        cursor = end
    if runtime:
        system_start = segments[0]["chars_start"]
        runtime_start = rendered.find(runtime, system_start)
        segments.extend(
            [
                {"segment": "p0", **token_span(system_start, runtime_start)},
                {
                    "segment": "runtime",
                    **token_span(runtime_start, runtime_start + len(runtime)),
                },
            ]
        )
        selected_data = full_data if arm == "C_FULL" else minimum_data
        data_start = rendered.find(selected_data, runtime_start)
        segments.append(
            {
                "segment": "selected_data",
                **token_span(data_start, data_start + len(selected_data)),
            }
        )
    return {
        "messages": messages,
        "input_token_ids": ids,
        "input_tokens": len(ids),
        "full_input_tokens": len(full_ids),
        **input_identity(tokenizer, messages, ids),
        "segments": segments,
        "selected_paths": sorted(selected),
        "excluded_paths": excluded,
        "exclusion_reason": "pre_registered_task_projection",
        "projection_schema": "app-original"
        if arm == "C_FULL"
        else "diagnosis-task-projection-v1.0.0",
        "padding_sha256": digest(padding) if padding else None,
        "omitted_messages": 0,
        "p0_sha256": digest(
            dashboard._prompt_profile(
                context,
                context["prompt_profiles"]["bound_profile"]
                if case["binding"]
                else context["prompt_profiles"]["default_profile"],
            )["system_prompt_text"]
        ),
        "retained_values_unchanged": True,
    }
