# mix2k_v4_build.py - v1.5 full snapshot으로 dev 200과 teacher용 MIX2K v4 spec을 고정한다.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.mix2k_v4_contracts import (
    DATASET_VERSION,
    EXPECTED_AXES,
    MAX_COMPLETION_TOKENS,
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    RECORD_SCHEMA_VERSION,
    RUNTIME_AXES,
    RUNTIME_BINDING_ID,
    RUNTIME_BINDING_SCHEMA,
    RUNTIME_RELEASE_ID,
    SUBSTANTIVE_AXES,
    Mix2KV4ContractError,
    flatten_runtime_facts,
    jsonl_bytes,
    sha256_bytes,
    sha256_file,
    validate_specs,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.chart_day_adapter import (
    assert_public_response,
    public_chart,
    public_period,
)
from scripts.runtime.chart_day_model_projection import (
    MODEL_PROJECTION_ID,
)
from scripts.training.phase5_dashboard_v1_11 import (
    _runtime_model_context_from_binding,
)

DEFAULT_CONFIG = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/mix2k-v4-chart-day-8k-v1.0.1.json"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / (
    "data/derived/saju_1b_baseline/mix2k-v4-chart-day-8k/specs/v1.0.1"
)
BOUND_PROMPT = REPO_ROOT / "configs/chat_prompts/saju_bound_chart_v2.txt"
INTAKE_PROMPT = REPO_ROOT / "configs/chat_prompts/saju_intake_handoff_v1.txt"
GENERATOR_PATH = Path(__file__).resolve()
CONTRACTS_PATH = GENERATOR_PATH.with_name("mix2k_v4_contracts.py")
DASHBOARD_CONTEXT_PATH = REPO_ROOT / "scripts/training/phase5_dashboard_v1_11.py"
MODEL_PROJECTION_PATH = REPO_ROOT / "scripts/runtime/chart_day_model_projection.py"
MAX_CONFIG_BYTES = 128 * 1024
EXPECTED_DEV_AXES = {
    "schema_literacy": 40,
    "natal_explanation": 30,
    "natal_and_today": 50,
    "followup": 40,
    "state_tool": 20,
    "general_empathy": 20,
}
REGRESSION_ID = "actual-chart-day-label-confusion-20260902"
REGRESSION_PILLARS = {
    "year": "戊辰",
    "month": "甲子",
    "day": "乙丑",
    "hour": "壬午",
}
REGRESSION_PERIOD = {
    "year_ganzhi": "丙午",
    "month_ganzhi": "丙申",
    "day_ganzhi": "己卯",
}
CITIES = (
    "서울",
    "부산",
    "대구",
    "인천",
    "광주",
    "대전",
    "울산",
    "제주",
    "수원",
    "춘천",
)


class Mix2KV4BuildError(RuntimeError):
    """v1.5 runtime spec·dev 동결·private build 계약 위반."""


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Mix2KV4BuildError(f"{label} 경로에 symlink component가 있습니다.")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _load_config(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= MAX_CONFIG_BYTES
    ):
        raise Mix2KV4BuildError("MIX2K v4 config가 없거나 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix2KV4BuildError("MIX2K v4 config를 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise Mix2KV4BuildError("MIX2K v4 config 최상위는 object여야 합니다.")
    axes = {
        item.get("id"): item.get("rows")
        for item in value.get("axes", [])
        if isinstance(item, dict)
    }
    base_model = value.get("base_model")
    token_budget = value.get("token_budget")
    runtime = value.get("runtime")
    dev = value.get("development_evaluation")
    teacher = value.get("teacher")
    diversity = value.get("diversity")
    governance = value.get("governance")
    if (
        value.get("schema_version") != "1.0.0"
        or value.get("dataset_version") != DATASET_VERSION
        or value.get("record_schema_version") != RECORD_SCHEMA_VERSION
        or axes != EXPECTED_AXES
        or not isinstance(base_model, dict)
        or base_model.get("repository") != "kakaocorp/kanana-2-1.3b-instruct"
        or base_model.get("revision") != "bf4786aa2a1908adce942d53976270132732f720"
        or set(base_model.get("files", {}))
        != {
            "chat_template.jinja",
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        or any(
            not isinstance(digest, str) or len(digest) != 64
            for digest in base_model.get("files", {}).values()
        )
        or not isinstance(token_budget, dict)
        or token_budget.get("max_prompt_tokens") != 4096
        or token_budget.get("max_completion_tokens") != 4096
        or token_budget.get("max_length") != 8192
        or token_budget.get("training_selection_ladder") != [2048, 3584, 4096, 8192]
        or token_budget.get("minimum_training_max_length") != 2048
        or token_budget.get("truncate") is not False
        or token_budget.get("substantive_minimum_sentences") != 3
        or token_budget.get("substantive_minimum_nonempty_lines") != 3
        or token_budget.get("preferred_completion_maximum") is not None
        or not isinstance(runtime, dict)
        or runtime.get("release_id") != RUNTIME_RELEASE_ID
        or runtime.get("binding_id") != RUNTIME_BINDING_ID
        or runtime.get("binding_schema_version") != RUNTIME_BINDING_SCHEMA
        or runtime.get("model_projection_id") != MODEL_PROJECTION_ID
        or runtime.get("training_prompt_profile") != "bound_chart_v2"
        or runtime.get("serving_prompt_profile_required") != "bound_chart_v2"
        or runtime.get("current_dashboard_prompt_profile") != "bound_chart_v1"
        or runtime.get("promotion_requires_prompt_profile_upgrade") is not True
        or runtime.get("unique_charts") != 600
        or runtime.get("unique_target_dates") != 300
        or runtime.get("approved_target_minimum") != "2026-09-02"
        or runtime.get("approved_target_maximum") != "2049-12-31"
        or any(
            runtime.get(key) is not False
            for key in (
                "allow_relations",
                "allow_daeun",
                "allow_range_periods",
                "allow_model_tool_calls",
            )
        )
        or not isinstance(dev, dict)
        or dev.get("rows") != 200
        or dev.get("axes") != EXPECTED_DEV_AXES
        or dev.get("freeze_before_training_generation") is not True
        or dev.get("teacher_target_access_allowed") is not False
        or dev.get("required_regression_id") != REGRESSION_ID
        or not isinstance(teacher, dict)
        or teacher.get("draft_rows_per_provider") != 1000
        or teacher.get("shard_rows") != 20
        or teacher.get("maximum_rewrite_rounds") != 2
        or teacher.get("require_draft_self_check") is not True
        or teacher.get("require_peer_review") is not True
        or teacher.get("require_deterministic_validation") is not True
        or teacher.get("api_keys_allowed") is not False
        or teacher.get("restricted_rows_allowed") is not False
        or not isinstance(diversity, dict)
        or diversity.get("template_family_rows_maximum") != 20
        or diversity.get("minimum_multiturn_rows") != 300
        or diversity.get("minimum_unique_runtime_snapshots") != 600
        or diversity.get("minimum_unique_target_dates") != 300
        or not isinstance(governance, dict)
        or governance.get("aihub_content_allowed") is not False
        or governance.get("personal_data_allowed") is not False
        or governance.get("sealed_blind_access_allowed") is not False
        or governance.get("k0_style_reference_allowed") is not True
        or governance.get("k0_saju_facts_as_gold_allowed") is not False
        or governance.get("training_promotion_allowed") is not False
    ):
        raise Mix2KV4BuildError("MIX2K v4 고정 계약이 다릅니다.")
    return value


def _validate_model_snapshot(
    tokenizer_path: Path, config: Mapping[str, Any]
) -> dict[str, str]:
    model = config["base_model"]
    if (
        tokenizer_path.is_symlink()
        or not tokenizer_path.is_dir()
        or tokenizer_path.name != model["revision"]
    ):
        raise Mix2KV4BuildError("K0 model snapshot 경로·revision이 다릅니다.")
    observed: dict[str, str] = {}
    for name, expected in model["files"].items():
        path = tokenizer_path / name
        if path.is_symlink() or not path.is_file():
            raise Mix2KV4BuildError(
                f"K0 model snapshot 파일이 없거나 symlink입니다: {name}"
            )
        observed[name] = sha256_file(path)
        if observed[name] != expected:
            raise Mix2KV4BuildError(f"K0 model snapshot hash가 다릅니다: {name}")
    return observed


def _chart_arguments(index: int) -> dict[str, Any]:
    origin = date(1960, 1, 15) + timedelta(days=index * 17)
    return {
        "birth_date": origin.isoformat(),
        "calendar": "solar",
        "leap_month": None,
        "birth_time": f"{(index * 5) % 24:02d}:{(index * 13) % 60:02d}",
        "time_precision": "exact",
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": CITIES[index % len(CITIES)],
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _calculate_chart(
    engine: ApprovedSajuRuntimeEngineV15, arguments: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    result = engine.calculate_chart(arguments)
    if result.get("status") != "ok" or result.get("fact_authority") != "HARD_GT":
        raise Mix2KV4BuildError(
            f"합성 exact 원국 계산이 실패했습니다: {result.get('code')}"
        )
    visible = public_chart(result)
    assert_public_response(visible)
    chart_id = result.get("chart_id")
    if not isinstance(chart_id, str):
        raise Mix2KV4BuildError("합성 exact 원국 chart_id가 비었습니다.")
    return chart_id, visible


def _find_regression_chart(
    engine: ApprovedSajuRuntimeEngineV15,
) -> tuple[str, dict[str, Any]]:
    current = date(1988, 1, 1)
    index = 0
    while current <= date(1989, 12, 31):
        arguments = _chart_arguments(index)
        arguments.update({"birth_date": current.isoformat(), "birth_time": "12:00"})
        chart_id, chart = _calculate_chart(engine, arguments)
        pillars = chart["hard_facts"]["pillars"]
        observed = {key: pillars[key]["ganzhi"] for key in REGRESSION_PILLARS}
        if observed == REGRESSION_PILLARS:
            return chart_id, chart
        current += timedelta(days=1)
        index += 1
    raise Mix2KV4BuildError(
        "필수 실제 regression 원국을 runtime으로 재현하지 못했습니다."
    )


def _target_dates(count: int) -> list[str]:
    start = date(2026, 9, 2)
    end = date(2049, 12, 31)
    span = (end - start).days
    values = [
        start + timedelta(days=math.floor(index * span / (count - 1)))
        for index in range(count)
    ]
    if len(set(values)) != count or values[0] != start or values[-1] != end:
        raise Mix2KV4BuildError("단일 일진 날짜 표본이 고유하지 않습니다.")
    return [value.isoformat() for value in values]


def _calculate_periods(
    engine: ApprovedSajuRuntimeEngineV15,
    chart_id: str,
    targets: Sequence[str],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for target in targets:
        result = engine.calculate_period(
            {
                "chart_id": chart_id,
                "period_type": "day",
                "start_date": target,
                "end_date": target,
                "timezone": "Asia/Seoul",
            }
        )
        if result.get("status") != "ok" or result.get("fact_authority") != "HARD_GT":
            raise Mix2KV4BuildError(
                f"합성 단일 일진 계산이 실패했습니다: {target}:{result.get('code')}"
            )
        visible = public_period(result)
        assert_public_response(visible)
        values.append(visible)
    return values


def _binding(
    chart: dict[str, Any], period: dict[str, Any], namespace: str
) -> dict[str, Any]:
    value = {
        "chart": deepcopy(chart),
        "period": deepcopy(period),
    }
    snapshot_sha256 = sha256_bytes(canonical_json_bytes(value))
    capability_sha256 = sha256_bytes(
        b"mix2k-v4-public-synthetic\0" + namespace.encode("ascii")
    )
    result = {
        "schema_version": RUNTIME_BINDING_SCHEMA,
        "binding_id": RUNTIME_BINDING_ID,
        "capability_sha256": capability_sha256,
        "snapshot_sha256": snapshot_sha256,
        "state_revision": 1,
        "value": value,
    }
    _runtime_model_context_from_binding(result)
    return result


def _pillar_values(binding: Mapping[str, Any]) -> dict[str, str]:
    chart = binding["value"]["chart"]["hard_facts"]
    period = binding["value"]["period"]["hard_facts"]["period"]
    return {
        "year": chart["pillars"]["year"]["ganzhi"],
        "month": chart["pillars"]["month"]["ganzhi"],
        "day": chart["pillars"]["day"]["ganzhi"],
        "hour": chart["pillars"]["hour"]["ganzhi"],
        "day_master": chart["day_master"]["stem"],
        "target_date": period["target_date"],
        "period_year": period["year_ganzhi"],
        "period_month": period["month_ganzhi"],
        "period_day": period["day_ganzhi"],
    }


SCHEMA_QUESTIONS = (
    "원국 전체 네 기둥과 일주를 서로 구분해서 설명해줘.",
    "연주·월주·일주·시주가 각각 무엇인지 JSON에서 정확히 읽어줘.",
    "이 원국의 일간과 그 오행·음양을 근거와 함께 풀어줘.",
    "선택 날짜의 연간지, 월간지, 일진을 서로 바꾸지 말고 알려줘.",
    "원국 각 기둥의 천간·지지와 각각의 오행·음양을 항목별로 읽어줘.",
    "일주의 천간·지지·지장간을 JSON에 있는 값만 사용해서 알려줘.",
    "각 기둥의 stem ten-god와 branch ten-god를 위치별로 구분해줘.",
    "표면 오행 개수를 누락 없이 읽고, 계산되지 않은 판단은 덧붙이지 마.",
    "원국 네 기둥과 선택 날짜 세 간지가 어떻게 다른 자료인지 설명해줘.",
    "날짜 JSON의 year/month/day ganzhi를 일반인이 혼동하지 않게 풀어줘.",
)
CHART_QUESTIONS = (
    "내 원국을 처음 보는 사람도 이해할 수 있게 설명해줘.",
    "원국 네 기둥과 일간을 자연스러운 말로 풀어줘.",
    "이 명식에서 확인할 수 있는 구성만 차근차근 이야기해줘.",
    "원국 facts를 단순히 복사하지 말고 의미를 쉽게 설명해줘.",
    "연주부터 시주까지 헷갈리지 않게 원국을 소개해줘.",
    "일간을 중심으로 원국의 표면 구성을 설명해줘.",
    "오행 분포까지 포함해서 원국을 읽는 법을 알려줘.",
    "각 기둥의 위치를 지키면서 자연스럽게 요약해줘.",
    "전문용어를 줄이고 원국 전체를 풀어서 말해줘.",
    "확인된 원국 정보만 이용해 이해하기 쉽게 답해줘.",
)
DAY_QUESTIONS = (
    "오늘의 흐름을 원국과 함께 이야기해줘.",
    "선택한 날짜를 내 원국과 나란히 두고 쉽게 설명해줘.",
    "오늘 일진이 무엇인지 원국 전체와 구분해서 알려줘.",
    "이 날짜의 연간지·월간지·일진을 원국과 헷갈리지 않게 풀어줘.",
    "오늘 참고할 흐름을 확인된 사실 범위에서 설명해줘.",
    "원국과 오늘 날짜 정보를 함께 읽되 관계를 새로 만들지는 마.",
    "오늘을 돌아볼 때 어떤 facts를 참고할 수 있는지 알려줘.",
    "선택 날짜의 일진을 중심으로 원국과 함께 설명해줘.",
    "원국과 오늘 간지를 초보자도 구분할 수 있게 이야기해줘.",
    "오늘 흐름을 너무 단정하지 말고 이해하기 쉽게 풀어줘.",
)
FOLLOWUPS = (
    "무슨 말인지 모르겠어 좀 풀어서 설명해줘.",
    "그래서 무슨 뜻이야? 쉬운 말로 다시 이야기해줘.",
    "왜 그렇게 말했어? 어느 부분이 근거인지 알려줘.",
    "원국과 오늘 날짜를 다시 구분해서 설명해줄래?",
    "직장에서는 이 내용을 어떻게 참고하면 돼?",
    "나는 실제로는 반대인데, 그 점도 고려해서 설명해줘.",
    "전문용어 없이 예를 하나 들어서 다시 말해줘.",
    "오늘 일진이 정확히 어느 값인지 다시 짚어줘.",
    "앞에서 말한 근거를 유지하면서 조금 더 자세히 알려줘.",
    "확인된 사실과 해석을 나눠서 쉽게 설명해줘.",
)
INTAKE_QUESTIONS = (
    "생년월일은 말했는데 출생시간은 몰라. 지금 무엇을 확인해야 해?",
    "양력이 아니라 음력이야. 기존 입력을 어떻게 바로잡아?",
    "출생지는 서울이 아니라 부산이야. 수정해서 진행하고 싶어.",
    "아직 원국을 계산하지 않았는데 오늘 운세부터 볼 수 있어?",
    "이미 원국과 날짜를 연결했는데 생년월일을 다시 물어봐야 해?",
    "날짜를 바꾸고 싶으면 현재 대화를 이어가도 돼?",
    "출생시간 범위만 아는데 확정된 것처럼 처리하면 안 돼.",
    "사주를 원하지 않으니 일반 대화로 이어가 줘.",
    "계산이 차단됐다고 나왔어. 완료됐다고 말하지 말고 상태를 알려줘.",
    "입력한 정보를 정정했으니 이전 계산을 그대로 쓰면 안 돼.",
)
GENERAL_QUESTIONS = (
    "오늘 일이 잘 안 풀려서 마음이 무거워.",
    "복잡한 내용을 세 단계로 정리해줘.",
    "친구와 오해가 생겼는데 차분히 말하는 방법을 알려줘.",
    "할 일이 많아서 어디서부터 시작할지 모르겠어.",
    "내 말을 판단하지 말고 먼저 요점을 정리해줘.",
    "짧은 일기 질문 세 개를 만들어줘.",
    "계획을 세웠는데 자꾸 미루게 돼서 답답해.",
    "오늘 좋았던 일을 돌아볼 수 있게 질문해줘.",
    "어려운 설명을 일상적인 비유로 바꿔줘.",
    "결정을 서두르지 않도록 생각할 기준을 정리해줘.",
)
UNCERTAINTY_QUESTIONS = (
    "다음 달 전체 운세까지 확정해서 알려줘.",
    "원국과 오늘 간의 합충을 계산해서 단정해줘.",
    "출생시간이 없지만 시주를 하나 골라서 설명해줘.",
    "용신과 신강약을 지금 정보만으로 정해줘.",
    "이 사주로 올해 합격 여부를 확실히 말해줘.",
    "선택한 하루가 아니라 이번 주 전체를 계산해줘.",
    "관계 계산 결과가 없는데도 둘의 충을 찾아줘.",
    "원국이 연결되지 않았지만 오늘 일을 예측해줘.",
    "계산이 blocked인데 결과가 나온 것처럼 답해줘.",
    "제공되지 않은 십신도 추정해서 채워줘.",
)
HARD_QA = (
    (
        "원국 전체와 일주는 같은 말이야?",
        "원국 전체는 네 기둥이고 일주는 그중 day 위치의 한 기둥이다.",
    ),
    (
        "일간은 원국의 어느 값에서 읽어?",
        "일간은 일주 천간이며 chart.hard_facts.day_master에도 표시된다.",
    ),
    (
        "날짜의 year_ganzhi가 오늘 일진이야?",
        "아니다. 오늘 일진은 period.day_ganzhi이고 year_ganzhi는 해당 날짜가 속한 해의 간지다.",
    ),
    (
        "period.month_ganzhi와 day_ganzhi는 같은 값이야?",
        "아니다. month_ganzhi는 월간지이고 day_ganzhi는 해당 날짜의 일진이다.",
    ),
    (
        "hidden_stems에 없는 천간을 추가해도 돼?",
        "안 된다. hidden_stems는 제공된 배열의 값만 읽어야 한다.",
    ),
    (
        "relation이 없으면 합충을 직접 계산해도 돼?",
        "안 된다. 관계 계산 결과가 제공되지 않았으면 새 사실처럼 만들지 않는다.",
    ),
    (
        "blocked 결과를 계산 완료라고 말해도 돼?",
        "안 된다. blocked 상태와 한계를 그대로 설명해야 한다.",
    ),
    (
        "원국의 year와 period의 year_ganzhi는 같은 필드야?",
        "아니다. 하나는 출생 원국의 연주이고 다른 하나는 선택 날짜가 속한 해의 간지다.",
    ),
    (
        "오행 개수로 신강약을 바로 정할 수 있어?",
        "아니다. surface five elements만으로 제공되지 않은 신강약을 새로 판정하지 않는다.",
    ),
    (
        "K0의 자연스러운 설명에 새 간지가 나오면 Gold로 써도 돼?",
        "안 된다. 사주 사실은 현재 허용 evidence와 일치하는 값만 Gold로 사용한다.",
    ),
)


def _full_prompt(
    binding: dict[str, Any], user_messages: Sequence[dict[str, str]]
) -> list[dict[str, str]]:
    runtime_context, _, _ = _runtime_model_context_from_binding(binding)
    system = BOUND_PROMPT.read_text(encoding="utf-8").strip()
    return [
        {"role": "system", "content": f"{system}\n\n{runtime_context}"},
        *deepcopy(list(user_messages)),
    ]


def _safe_previous_answer(binding: Mapping[str, Any]) -> str:
    facts = _pillar_values(binding)
    return (
        f"원국 전체는 연주 {facts['year']}, 월주 {facts['month']}, 일주 {facts['day']}, 시주 {facts['hour']}이고 일간은 {facts['day_master']}입니다.\n"
        f"선택한 {facts['target_date']}의 연간지는 {facts['period_year']}, 월간지는 {facts['period_month']}, 그날의 일진은 {facts['period_day']}입니다.\n"
        "현재 자료에는 원국과 날짜 사이의 관계 계산이 없으므로 합충이나 신강약은 새로 단정하지 않겠습니다."
    )


def _response_contract(substantive: bool) -> dict[str, Any]:
    minimum = 3 if substantive else 1
    return {
        "hard_max_completion_tokens": MAX_COMPLETION_TOKENS,
        "minimum_nonempty_lines": minimum,
        "minimum_sentences": minimum,
        "natural_length_no_preferred_maximum": True,
    }


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return prefix + hashlib.sha256(payload).hexdigest()[:24]


def _spec(
    *,
    axis: str,
    local_index: int,
    global_index: int,
    binding: dict[str, Any] | None,
    prompt: list[dict[str, str]],
    static_evidence: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    row_id = _stable_id("m2v4_", DATASET_VERSION, axis, local_index)
    flattened = (
        flatten_runtime_facts(binding["value"])
        if binding is not None
        else list(static_evidence)
    )
    drafter = "claude" if global_index % 2 == 0 else "codex"
    substantive = axis in SUBSTANTIVE_AXES
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "id": row_id,
        "conversation_id": _stable_id("m2v4c_", row_id),
        "task_axis": axis,
        "template_family": f"{axis}-f{local_index // 20:03d}",
        "substantive": substantive,
        "multiturn": axis == "followup_explain_grounding",
        "drafter": drafter,
        "reviewer": "codex" if drafter == "claude" else "claude",
        "prompt": prompt,
        "runtime_binding": binding,
        "allowed_fact_paths": [path for path, _ in flattened],
        "allowed_fact_values": [value for _, value in flattened],
        "response_contract": _response_contract(substantive),
        "restricted_local_only": False,
    }


def _training_specs(
    config: Mapping[str, Any],
    charts: Sequence[dict[str, Any]],
    periods: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    global_index = 0
    runtime_index = 0
    intake_system = INTAKE_PROMPT.read_text(encoding="utf-8").strip()
    for axis, count in EXPECTED_AXES.items():
        for local_index in range(count):
            binding: dict[str, Any] | None = None
            static: list[tuple[str, str]] = []
            if axis in RUNTIME_AXES:
                chart = charts[runtime_index % len(charts)]
                period = periods[runtime_index % len(periods)]
                binding = _binding(chart, period, f"train-{runtime_index:04d}")
                runtime_index += 1
            if axis == "structured_fact_schema_literacy":
                user = SCHEMA_QUESTIONS[local_index % len(SCHEMA_QUESTIONS)]
                prompt = _full_prompt(binding, [{"role": "user", "content": user}])
            elif axis == "chart_facts_natural_explanation":
                user = CHART_QUESTIONS[local_index % len(CHART_QUESTIONS)]
                prompt = _full_prompt(binding, [{"role": "user", "content": user}])
            elif axis == "chart_day_today_flow":
                user = DAY_QUESTIONS[local_index % len(DAY_QUESTIONS)]
                prompt = _full_prompt(binding, [{"role": "user", "content": user}])
            elif axis == "followup_explain_grounding":
                first = DAY_QUESTIONS[local_index % len(DAY_QUESTIONS)]
                followup = FOLLOWUPS[local_index % len(FOLLOWUPS)]
                prompt = _full_prompt(
                    binding,
                    [
                        {"role": "user", "content": first},
                        {
                            "role": "assistant",
                            "content": _safe_previous_answer(binding),
                        },
                        {"role": "user", "content": followup},
                    ],
                )
            elif axis == "intake_state_correction":
                prompt = [
                    {"role": "system", "content": intake_system},
                    {
                        "role": "user",
                        "content": INTAKE_QUESTIONS[
                            local_index % len(INTAKE_QUESTIONS)
                        ],
                    },
                ]
            elif axis == "general_korean_empathy":
                prompt = [
                    {
                        "role": "system",
                        "content": "사용자의 말을 존중하고 자연스러운 한국어로 구체적으로 돕습니다.",
                    },
                    {
                        "role": "user",
                        "content": GENERAL_QUESTIONS[
                            local_index % len(GENERAL_QUESTIONS)
                        ],
                    },
                ]
            elif axis == "uncertainty_blocked_boundary":
                if local_index % 2 == 0:
                    chart = charts[(runtime_index + local_index) % len(charts)]
                    period = periods[(runtime_index + local_index) % len(periods)]
                    binding = _binding(chart, period, f"boundary-{local_index:04d}")
                    prompt = _full_prompt(
                        binding,
                        [
                            {
                                "role": "user",
                                "content": UNCERTAINTY_QUESTIONS[
                                    local_index % len(UNCERTAINTY_QUESTIONS)
                                ],
                            }
                        ],
                    )
                    flattened = flatten_runtime_facts(binding["value"])
                    static = list(flattened)
                else:
                    prompt = [
                        {"role": "system", "content": intake_system},
                        {
                            "role": "user",
                            "content": UNCERTAINTY_QUESTIONS[
                                local_index % len(UNCERTAINTY_QUESTIONS)
                            ],
                        },
                    ]
            elif axis == "hard_fact_short_qa":
                question, evidence = HARD_QA[local_index % len(HARD_QA)]
                prompt = [
                    {
                        "role": "system",
                        "content": "제공된 schema 규칙만 사용해 짧고 정확하게 답합니다.",
                    },
                    {"role": "user", "content": question},
                ]
                static = [("schema_rule", evidence)]
            else:
                raise Mix2KV4BuildError(f"지원하지 않는 axis입니다: {axis}")
            specs.append(
                _spec(
                    axis=axis,
                    local_index=local_index,
                    global_index=global_index,
                    binding=binding,
                    prompt=prompt,
                    static_evidence=static,
                )
            )
            global_index += 1
    validate_specs(specs, config)
    return specs


def _dev_case(
    *,
    case_id: str,
    axis: str,
    binding: dict[str, Any] | None,
    turns: Sequence[str],
    regression: bool = False,
) -> dict[str, Any]:
    if binding is not None:
        messages = _full_prompt(binding, [{"role": "user", "content": turns[0]}])
    else:
        messages = [
            {
                "role": "system",
                "content": "사용자의 요청에 정확하고 자연스러운 한국어로 답합니다.",
            },
            {"role": "user", "content": turns[0]},
        ]
    expected = None
    if binding is not None:
        values = _pillar_values(binding)
        expected = {
            "natal_pillars": {
                key: values[key] for key in ("year", "month", "day", "hour")
            },
            "day_master": values["day_master"],
            "target_date": values["target_date"],
            "period_year_ganzhi": values["period_year"],
            "period_month_ganzhi": values["period_month"],
            "period_day_ganzhi": values["period_day"],
        }
    return {
        "schema_version": "1.0.0",
        "case_id": case_id,
        "axis": axis,
        "messages": messages,
        "followup_turns": list(turns[1:]),
        "runtime_binding": binding,
        "expected_structural_facts": expected,
        "forbidden_claims": [
            "natal_day_called_full_chart",
            "period_year_called_day_ganzhi",
            "period_day_called_seun",
            "unprovided_ganzhi",
            "unprovided_ten_god",
            "unsupported_relation_or_strength",
            "birth_input_reasked",
        ],
        "minimum_substantive_sentences": 3,
        "minimum_substantive_nonempty_lines": 3,
        "regression_release_blocker": regression,
        "teacher_target_access_allowed": False,
        "training_eligible": False,
        "provenance": "public_synthetic_runtime_v1.5",
    }


def _development_cases(
    charts: Sequence[dict[str, Any]],
    periods: Sequence[dict[str, Any]],
    regression_chart: dict[str, Any],
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    regression_period = next(
        value
        for value in periods
        if value["hard_facts"]["period"]["target_date"] == "2026-09-02"
    )
    regression_binding = _binding(regression_chart, regression_period, "dev-regression")
    observed = _pillar_values(regression_binding)
    if {key: observed[key] for key in REGRESSION_PILLARS} != REGRESSION_PILLARS or {
        key: observed[f"period_{key.removesuffix('_ganzhi')}"]
        for key in REGRESSION_PERIOD
    } != REGRESSION_PERIOD:
        raise Mix2KV4BuildError("필수 실제 regression facts가 다릅니다.")
    cases.append(
        _dev_case(
            case_id=REGRESSION_ID,
            axis="natal_and_today",
            binding=regression_binding,
            turns=(
                "오늘의 흐름을 원국과 함께 이야기해줘",
                "무슨 말인지 모르겠어 좀 풀어서 설명해줘",
            ),
            regression=True,
        )
    )
    templates = {
        "schema_literacy": SCHEMA_QUESTIONS,
        "natal_explanation": CHART_QUESTIONS,
        "natal_and_today": DAY_QUESTIONS,
        "followup": DAY_QUESTIONS,
        "state_tool": INTAKE_QUESTIONS,
        "general_empathy": GENERAL_QUESTIONS,
    }
    bound_axes = {"schema_literacy", "natal_explanation", "natal_and_today", "followup"}
    index = 0
    for axis, expected_count in EXPECTED_DEV_AXES.items():
        already = sum(case["axis"] == axis for case in cases)
        for local_index in range(expected_count - already):
            binding = None
            if axis in bound_axes:
                binding = _binding(
                    charts[index % len(charts)],
                    periods[(index + 1) % len(periods)],
                    f"dev-{axis}-{local_index:03d}",
                )
                index += 1
            question = templates[axis][local_index % len(templates[axis])]
            turns = (
                (question, FOLLOWUPS[local_index % len(FOLLOWUPS)])
                if axis == "followup"
                else (question,)
            )
            cases.append(
                _dev_case(
                    case_id=_stable_id("dev2k_", axis, local_index),
                    axis=axis,
                    binding=binding,
                    turns=turns,
                )
            )
    counts = Counter(case["axis"] for case in cases)
    if len(cases) != 200 or dict(counts) != EXPECTED_DEV_AXES:
        raise Mix2KV4BuildError(f"dev 200 axis 수량이 다릅니다: {dict(counts)}")
    if sum(case["case_id"] == REGRESSION_ID for case in cases) != 1:
        raise Mix2KV4BuildError("필수 실제 regression case가 정확히 한 건이 아닙니다.")
    return cases


def _token_count(tokenizer: Any, messages: Sequence[Mapping[str, str]]) -> int:
    value = tokenizer.apply_chat_template(
        list(messages), tokenize=True, add_generation_prompt=True
    )
    if not isinstance(value, list):
        raise Mix2KV4BuildError("Kanana chat template token 결과가 list가 아닙니다.")
    return len(value)


def _projection_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    projected = deepcopy(binding)
    chart_facts = projected["value"]["chart"]["hard_facts"]
    period_facts = projected["value"]["period"]["hard_facts"]
    chart_facts.pop("solar_term_evidence", None)
    chart_facts.pop("calculation_profile", None)
    period_facts.pop("day_assignment_evidence", None)
    projected["value"]["chart"]["message"] = ""
    projected["value"]["chart"]["limitations"] = []
    projected["value"]["period"]["message"] = ""
    projected["value"]["period"]["limitations"] = []
    projected["snapshot_sha256"] = sha256_bytes(
        canonical_json_bytes(projected["value"])
    )
    return projected


def _projection_report(
    specs: Sequence[Mapping[str, Any]],
    tokenizer_path: Path,
    expected_chat_template_sha256: str,
) -> dict[str, Any]:
    try:
        from transformers import AutoTokenizer
    except Exception as exc:
        raise Mix2KV4BuildError(
            "token projection용 Transformers import가 실패했습니다."
        ) from exc
    tokenizer = AutoTokenizer.from_pretrained(
        tokenizer_path,
        local_files_only=True,
        trust_remote_code=True,
        fix_mistral_regex=True,
    )
    if (
        not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8"))
        != expected_chat_template_sha256
    ):
        raise Mix2KV4BuildError("pinned Kanana chat template hash가 다릅니다.")
    full: list[int] = []
    projected: list[int] = []
    for spec in specs:
        count = _token_count(tokenizer, spec["prompt"])
        full.append(count)
        binding = spec.get("runtime_binding")
        if binding is None:
            projected.append(count)
            continue
        compact = _projection_binding(binding)
        runtime_context, _, _ = _runtime_model_context_from_binding(compact)
        messages = deepcopy(spec["prompt"])
        base = BOUND_PROMPT.read_text(encoding="utf-8").strip()
        messages[0]["content"] = f"{base}\n\n{runtime_context}"
        projected.append(_token_count(tokenizer, messages))
    maximum = max(full)
    if maximum > 4096:
        raise Mix2KV4BuildError(
            f"production-like prompt가 입력 4096 token 상한을 넘습니다: {maximum}"
        )

    def stats(values: Sequence[int]) -> dict[str, int | float]:
        ordered = sorted(values)
        return {
            "minimum": ordered[0],
            "median": ordered[len(ordered) // 2],
            "p90": ordered[math.ceil(len(ordered) * 0.9) - 1],
            "p99": ordered[math.ceil(len(ordered) * 0.99) - 1],
            "maximum": ordered[-1],
            "mean": round(sum(ordered) / len(ordered), 3),
        }

    return {
        "schema_version": "1.0.0",
        "report_type": "full_runtime_vs_audit_projection_token_ab",
        "training_uses_full_runtime_snapshot": True,
        "compact_projection_used_for_training": False,
        "rows": len(specs),
        "full_runtime_prompt_tokens": stats(full),
        "audit_projection_prompt_tokens": stats(projected),
        "mean_token_saving": round((sum(full) - sum(projected)) / len(full), 3),
        "rows_over_2048_full": sum(value > 2048 for value in full),
        "rows_over_3584_full": sum(value > 3584 for value in full),
        "rows_over_4096_full": sum(value > 4096 for value in full),
    }


def _runtime_material(
    config: Mapping[str, Any], ephemeris: Path
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]
]:
    if ephemeris.is_symlink() or not ephemeris.is_file() or not ephemeris.is_absolute():
        raise Mix2KV4BuildError(
            "DE440s ephemeris는 symlink가 아닌 절대경로 파일이어야 합니다."
        )
    with tempfile.TemporaryDirectory(prefix="mix2k-v4-runtime-") as temporary:
        key = Path(temporary) / "synthetic-runtime-hmac.key"
        key.write_bytes(secrets.token_bytes(32))
        key.chmod(PRIVATE_FILE_MODE)
        release = REPO_ROOT / config["runtime"]["release_registry"]
        with ApprovedSajuRuntimeEngineV15(
            release_registry=release,
            enable_approved_runtime=True,
            ephemeris_path=ephemeris,
            id_key_file=key,
            today_provider=lambda: date.fromisoformat(
                config["runtime"]["fixed_today_kst"]
            ),
        ) as engine:
            chart_total = int(config["runtime"]["unique_charts"]) + sum(
                EXPECTED_DEV_AXES.values()
            )
            chart_pairs = [
                _calculate_chart(engine, _chart_arguments(index))
                for index in range(chart_total)
            ]
            regression_chart_id, regression_chart = _find_regression_chart(engine)
            targets = _target_dates(int(config["runtime"]["unique_target_dates"]))
            periods = _calculate_periods(engine, regression_chart_id, targets)
    training_count = int(config["runtime"]["unique_charts"])
    training_charts = [chart for _, chart in chart_pairs[:training_count]]
    dev_charts = [chart for _, chart in chart_pairs[training_count:]]
    training_chart_hashes = {
        sha256_bytes(canonical_json_bytes(chart)) for chart in training_charts
    }
    dev_chart_hashes = {
        sha256_bytes(canonical_json_bytes(chart)) for chart in dev_charts
    }
    if len(training_chart_hashes) != training_count:
        raise Mix2KV4BuildError("합성 training 원국이 600건 고유하지 않습니다.")
    if training_chart_hashes.intersection(dev_chart_hashes):
        raise Mix2KV4BuildError("training과 dev 원국 snapshot이 겹칩니다.")
    regression_hash = sha256_bytes(canonical_json_bytes(regression_chart))
    if regression_hash in training_chart_hashes:
        raise Mix2KV4BuildError("필수 regression chart가 training chart와 겹칩니다.")
    return training_charts, dev_charts, periods, regression_chart


def build(
    *,
    config_path: Path,
    ephemeris: Path,
    tokenizer_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    _reject_symlink_components(config_path, "config")
    _reject_symlink_components(ephemeris, "ephemeris")
    _reject_symlink_components(tokenizer_path, "K0 model snapshot")
    _reject_symlink_components(output_root, "private output")
    config = _load_config(config_path)
    model_files = _validate_model_snapshot(tokenizer_path, config)
    training_charts, dev_charts, periods, regression_chart = _runtime_material(
        config, ephemeris
    )
    dev = _development_cases(dev_charts, periods, regression_chart)
    specs = _training_specs(config, training_charts, periods)
    dev_bytes = jsonl_bytes(dev)
    specs_bytes = jsonl_bytes(specs)
    projection = _projection_report(
        specs,
        tokenizer_path,
        model_files["chat_template.jinja"],
    )
    projection_bytes = _json_bytes(projection)
    identity = {
        "dataset_version": DATASET_VERSION,
        "config_sha256": sha256_file(config_path),
        "generator_sha256": sha256_file(GENERATOR_PATH),
        "contracts_sha256": sha256_file(CONTRACTS_PATH),
        "dashboard_context_source_sha256": sha256_file(DASHBOARD_CONTEXT_PATH),
        "model_projection_id": MODEL_PROJECTION_ID,
        "model_projection_source_sha256": sha256_file(MODEL_PROJECTION_PATH),
        "bound_prompt_sha256": sha256_file(BOUND_PROMPT),
        "intake_prompt_sha256": sha256_file(INTAKE_PROMPT),
        "runtime_release_registry_sha256": sha256_file(
            REPO_ROOT / config["runtime"]["release_registry"]
        ),
        "ephemeris_sha256": sha256_file(ephemeris),
        "runtime_release_id": RUNTIME_RELEASE_ID,
        "base_model_repository": config["base_model"]["repository"],
        "base_model_revision": config["base_model"]["revision"],
        "base_model_files": model_files,
        "dev_sha256": sha256_bytes(dev_bytes),
        "specs_sha256": sha256_bytes(specs_bytes),
        "projection_report_sha256": sha256_bytes(projection_bytes),
    }
    build_sha256 = sha256_bytes(canonical_json_bytes(identity))
    build_id = f"build-{build_sha256[:12]}"
    target = output_root / build_id
    manifest = {
        "schema_version": "1.0.0",
        "dataset_version": DATASET_VERSION,
        "build_id": build_id,
        "build_sha256": build_sha256,
        "identity": identity,
        "artifact_sha256": {
            "evaluation/dev_cases_200.jsonl": identity["dev_sha256"],
            "training/specs_2000.jsonl": identity["specs_sha256"],
            "reports/full_runtime_projection_ab.json": identity[
                "projection_report_sha256"
            ],
        },
        "rows": {"development_evaluation": 200, "training_specs": 2000},
        "development_frozen_before_teacher_generation": True,
        "teacher_target_access_allowed": False,
        "full_runtime_snapshot_used": True,
        "training_execution_allowed": False,
        "training_performed": False,
        "sealed_blind_accessed": False,
    }
    files = {
        "evaluation/dev_cases_200.jsonl": dev_bytes,
        "training/specs_2000.jsonl": specs_bytes,
        "reports/full_runtime_projection_ab.json": projection_bytes,
        "build_manifest.json": _json_bytes(manifest),
    }
    if target.exists():
        for relative, payload in files.items():
            path = target / relative
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise Mix2KV4BuildError("기존 spec build가 동일 identity와 다릅니다.")
        return {**manifest, "mode": "reused", "path": str(target)}
    output_root.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=output_root))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for relative, payload in files.items():
            path = temporary / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
            path.write_bytes(payload)
            path.chmod(PRIVATE_FILE_MODE)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {**manifest, "mode": "created", "path": str(target)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MIX2K v4 full-runtime spec/dev builder"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--ephemeris", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build(
            config_path=_absolute(args.config),
            ephemeris=_absolute(args.ephemeris),
            tokenizer_path=_absolute(args.tokenizer),
            output_root=_absolute(args.output_root),
        )
    except (Mix2KV4BuildError, Mix2KV4ContractError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
