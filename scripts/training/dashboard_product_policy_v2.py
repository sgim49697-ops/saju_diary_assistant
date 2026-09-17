# dashboard_product_policy_v2.py - 승인 사실 조회와 요청별 정보·이력 선택을 모델 없이 결정한다.

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from scripts.training import dashboard_grounding_v4 as intent

POLICY_VERSION = "saju-product-response-v1.1.0"
PROFILE_ID = "product_v1"
ENGINE_ID = "lora_r16"
BASE_PROMPT = (
    "당신은 한국어로 대화하는 도우미입니다. 현재 사용자의 요청을 우선하고, "
    "요청한 문장 수와 말투를 지키세요. 공감을 요청하면 공감하고, 문장 작성을 "
    "요청하면 요청한 문장만 작성하세요. 이전 대화는 참고 발언이지 사실을 "
    "보증하는 자료가 아닙니다. 알 수 없는 정보는 지어내지 마세요."
)
GROUNDED_PROMPT = (
    "아래 서버 사실은 이미 연결된 현재 계산 결과입니다. 사용자 주장이나 이전 "
    "모델 발언과 충돌하면 현재 서버 사실을 우선하세요. 원국의 일간은 천간 한 "
    "글자이고 일주는 천간과 지지 두 글자입니다. 원국과 선택 날짜의 일진을 "
    "구분하세요. 없는 시주와 불확실한 값은 확정하지 마세요. 계산 사실과 참고 "
    "해석을 구분하고 신강약·격국·용신·미래 사건을 계산하거나 단정하지 마세요. "
    "생년월일을 다시 요청하지 말고 제공된 범위에서 현재 질문에 답하세요. "
    "이전 답변을 인용할 때는 과거 발언임을 밝히며 승인 사실로 승격하지 마세요."
)
PROMPT_BYTES = (BASE_PROMPT + "\n").encode()
PROMPT_PIN = hashlib.sha256(PROMPT_BYTES).hexdigest()
PROFILE_CONTRACT = {
    "default_profile": PROFILE_ID,
    "bound_profile": PROFILE_ID,
    "legacy_profile": "raw_legacy",
    "profiles": {
        PROFILE_ID: {
            "label": "질문별 응답 v1 · R16 후보",
            "description": "연결 상태와 요청별 입력을 분리한 미배포 제품 후보",
            "system_prompt": {
                "path": "configs/chat_prompts/saju_product_v1.txt",
                "bytes": len(PROMPT_BYTES),
                "sha256": PROMPT_PIN,
            },
            "production_like": True,
            "diagnostic_only": True,
        }
    },
}
KINDS = frozenset({"direct_fact", "model_generated", "clarification", "blocked"})
MODES = frozenset({"general", "fact_lookup", "chart_explanation", "day_explanation", "confirmation", "unsupported"})
FIELD_PATHS = {
    "일간": "chart.hard_facts.day_master.stem",
    "연주": "chart.hard_facts.pillars.year.ganzhi",
    "월주": "chart.hard_facts.pillars.month.ganzhi",
    "일주": "chart.hard_facts.pillars.day.ganzhi",
    "시주": "chart.hard_facts.pillars.hour.ganzhi",
    "일진": "period.hard_facts.period.day_ganzhi",
    "선택 날짜": "period.hard_facts.period.target_date",
}
_EXPLAIN = re.compile(r"설명|해석|풀어|성향|특징|장점|단점|직업|조언|보완|예시|사례|차이|의미|뜻|개념|관계|왜|어떻게|쉽게|문장|메시지|재작성|추천")
_REFER = re.compile(r"아까|방금|앞서|그\s*(?:말|설명|답변)|다시|더\s*쉽게|짧게|다듬|말투|그중|중에서")
_UNSUPPORTED_TEXT = r"신\s*강(?:\s*약)?|신\s*약|격국|용신|대운|사건\s*예측|미래\s*사건"
_UNSUPPORTED = re.compile(_UNSUPPORTED_TEXT)
_CALC_OPT_OUT = re.compile(rf"(?:{_UNSUPPORTED_TEXT})(?:\s*[/·와과,]\s*(?:{_UNSUPPORTED_TEXT}))*\s*(?:은|는|을|를)?\s*(?:말고|빼고|제외하고|하지\s*말고)")
_TERM_EXPLANATION = re.compile(r"뜻|용어|개념|정의|차이|무슨\s*의미")
_PERSONAL = re.compile(r"(?:내|제|나의|저의)\s*(?:사주|원국|명식|격국|용신|대운)|나는|저는|내가|제가|나한테|나에게")
_NEW_GENERAL_TASK = re.compile(r"(?:메시지|문자|메일|편지|보고서|회의록).*(?:써|작성|다듬)|(?:하소연|고민).*(?:들어|공감)|위로해")
_FIELD_TOKEN = re.compile(r"(?<![가-힣A-Za-z0-9])(?P<label>선택\s+날짜|일간|연주|년주|월주|일주|시주|일진)(?=$|[^가-힣A-Za-z0-9]|(?:은|는|이|가|을|를|의|과|와|도|만|로)(?=$|[^가-힣A-Za-z0-9])|(?:뭐|알려|확인))")
_CORRECTION = re.compile(r"(?:생일|생년월일|출생|태어난|출생시간).*(?:잘못|정정|수정|바꿔|아니라)|\d+\s*월\s*\d+\s*일.*아니라")
_BIRTH_INPUT = re.compile(r"(?:양력|음력|생년월일|출생일)\s*\d|\d{4}.*(?:태생|태어|출생)")
_PAST = re.compile(r"정정\s*전|이전\s*원국|옛날|예전")
_STEMS = "甲乙丙丁戊己庚辛壬癸"
_NAMES = ("갑목", "을목", "병화", "정화", "무토", "기토", "경금", "신금", "임수", "계수")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def get_path(value: dict, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def project(value: dict, paths: list[str]) -> dict:
    result: dict = {}
    for path in paths:
        part = get_path(value, path)
        if part is None:
            continue
        cursor = result
        segments = path.split(".")
        for segment in segments[:-1]:
            cursor = cursor.setdefault(segment, {})
        cursor[segments[-1]] = deepcopy(part)
    return result


@dataclass(frozen=True)
class ResponsePlan:
    kind: str
    mode: str
    active_request: str
    answer: str | None = None
    reason_code: str | None = None
    selected_paths: tuple[str, ...] = ()
    selected_facts: dict = field(default_factory=dict)
    history_indices: tuple[int, ...] = ()
    notices: tuple[str, ...] = ()

    def trace(self, binding: dict | None) -> dict:
        return {
            "policy_version": POLICY_VERSION,
            "response_kind": self.kind,
            "response_mode": self.mode,
            "selected_paths": list(self.selected_paths),
            "selected_facts_sha256": digest(self.selected_facts),
            "history_indices": list(self.history_indices),
            "active_request_sha256": digest(self.active_request),
            "snapshot_sha256": binding["snapshot_sha256"] if binding else None,
            "state_revision": binding["state_revision"] if binding else None,
            "notices": list(self.notices),
        }


def _notice(code: str) -> str:
    return {
        intent.DATE_REBIND: "질문 날짜와 연결 날짜가 다릅니다. 해당 날짜를 계산한 뒤 새 대화에 연결해 주세요.",
        intent.DATE_AMBIGUOUS: "확인할 날짜를 연도와 함께 하루로 지정해 주세요.",
        intent.SCOPE_UNSUPPORTED: "현재 대화에서는 연결된 단일 일진만 사용할 수 있습니다. 주·월·연간 운세는 생성하지 않습니다.",
        intent.INTENT_CONFIRMATION: "운세가 궁금한가요, 아니면 일상 이야기를 이어갈까요?",
        "RUNTIME_CHART_REQUIRED": "원국 입력 화면에서 계산을 완료하고 이 대화에 연결해 주세요.",
        "RUNTIME_SINGLE_DAY_REQUIRED": "선택 날짜의 일진을 계산한 뒤 새 대화에 연결해 주세요.",
        "RUNTIME_FACT_UNCERTAIN": "연결된 계산 결과에서 이 값은 확정되지 않았습니다. 출생시간 미상·범위와 계산 제한을 확인해 주세요.",
        "RUNTIME_CORRECTION_REQUIRED": "정정할 출생정보를 입력 화면에서 수정하고 재계산한 뒤 새 대화에 연결해 주세요. 기존 대화의 원국은 바꾸지 않습니다.",
        "RUNTIME_INTAKE_REQUIRED": "출생정보는 원국 입력 화면에서 확인하고 계산해 주세요. 대화 내용만으로 원국을 추측하지 않습니다.",
        "RUNTIME_CALCULATION_UNSUPPORTED": "이 계산·예측은 제공 범위가 아닙니다. 승인된 원국이나 연결된 하루의 사실만 설명할 수 있습니다.",
    }[code]


def _stop(prompt: str, code: str) -> ResponsePlan:
    blocked = code in {intent.SCOPE_UNSUPPORTED, "RUNTIME_CALCULATION_UNSUPPORTED"}
    return ResponsePlan("blocked" if blocked else "clarification", "unsupported" if blocked else "confirmation", prompt, _notice(code), code)


def _labels(prompt: str) -> list[str]:
    found = []
    for match in _FIELD_TOKEN.finditer(prompt):
        name = re.sub(r"\s+", " ", match["label"])
        if name == "년주":
            name = "연주"
        # '연주'는 독립 단어여도 음악일 수 있다. 소유·명리 맥락 또는
        # 필드만 지정한 조회 외에는 원국 값으로 바꾸지 않는다.
        if name == "연주" and not (
            re.search(r"사주|원국|명식|간지|(?:내|제|나의|저의)\s*(?:연주|년주)", prompt)
            or re.fullmatch(r"(?:연주|년주)\s*(?:[?？]|알려\s*줘[?？.]?)?", prompt)
        ):
            continue
        if name not in found:
            found.append(name)
    return found


def _intent_text(prompt: str, labels: list[str]) -> str:
    """부모 판별기의 부분 문자열 오탐을 막으며 모델에 전달할 원문은 보존한다."""
    matches = {m.start() for m in _FIELD_TOKEN.finditer(prompt) if re.sub(r"\s+", " ", m["label"]).replace("년주", "연주") in labels}
    return re.sub(r"일간|일주|일진|연주|년주|월주|시주", lambda m: m[0] if m.start() in matches else " ", prompt)


def _unsupported_calculation(prompt: str) -> bool:
    active = _CALC_OPT_OUT.sub(" ", intent._OPT_OUT.sub(" ", prompt))
    if not _UNSUPPORTED.search(active):
        return False
    # 일반 용어 설명은 개인 판정이 아니다. 개인 원국 판정과 결합하면 차단한다.
    return not (_TERM_EXPLANATION.search(active) and not _PERSONAL.search(active)
                and not re.search(r"계산|판정|판단|골라|찾아|추천|맞(?:아|는지)", active))


def render_fact(prompt: str, label: str, actual: str) -> str:
    if label not in FIELD_PATHS or not isinstance(actual, str) or not actual:
        raise ValueError("승인 조회 값이 없습니다.")
    if label == "일간" and (len(actual) != 1 or actual not in _STEMS):
        raise ValueError("승인 일간 값 형식이 다릅니다.")
    if label == "선택 날짜" and (re.fullmatch(r"\d{4}-\d{2}-\d{2}", actual) is None or date.fromisoformat(actual).isoformat() != actual):
        raise ValueError("승인 날짜 값 형식이 다릅니다.")
    pairs = {"".join(p) for p in zip((_STEMS * 6), ("子丑寅卯辰巳午未申酉戌亥" * 5), strict=True)}
    if label not in {"일간", "선택 날짜"} and actual not in pairs:
        raise ValueError("승인 간지 값 형식이 다릅니다.")
    rendered = f"{_NAMES[_STEMS.index(actual)]}({actual})" if label == "일간" else actual
    prefix = "연결된 날짜의" if label in {"일진", "선택 날짜"} else "연결된 원국의"
    claims = [_STEMS[i] for i, name in enumerate(_NAMES) if name in prompt] if label == "일간" else [p for p in pairs if p in prompt]
    correction = "아니요. " if "맞" in prompt and claims and any(v != actual for v in claims) else ""
    particle = "은" if label in {"일간", "일진"} else "는"
    return f"{correction}{prefix} {label}{particle} {rendered}입니다."


def _history(messages: list[dict], mode: str, *, refer: bool) -> tuple[int, ...]:
    """기존 turn 원문을 선택할 뿐 재작성·요약하거나 다른 세션을 가져오지 않는다."""
    pairs = []
    for index in range(0, len(messages), 2):
        if index + 1 >= len(messages):
            break
        assistant = messages[index + 1]
        stored_mode = assistant.get("response_mode")
        pairs.append((index, stored_mode))
    if not pairs:
        return ()
    selected = []
    for index, stored_mode in reversed(pairs):
        compatible = stored_mode == "general" if mode == "general" else stored_mode in {"chart_explanation", "day_explanation", "fact_lookup"}
        if not compatible:
            if selected or not refer:
                break
            continue
        selected.extend([index, index + 1])
        # 참조 요청에서도 새 주제를 넘어 모든 과거 이력을 끌어오지 않는다.
        if refer:
            break
    return tuple(sorted(selected))


def plan_response(prompt: str, binding: dict | None, messages: list[dict], *, today=None) -> ResponsePlan:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("비어 있지 않은 현재 요청이 필요합니다.")
    prompt = prompt.strip()
    previous = next((m["content"] for m in reversed(messages) if m["role"] == "user"), None)
    prior_surface = _intent_text(_CALC_OPT_OUT.sub(" ", previous), _labels(previous)) if previous else None
    last = messages[-1] if messages else {}
    if _CORRECTION.search(prompt):
        return _stop(prompt, "RUNTIME_CORRECTION_REQUIRED")
    if _BIRTH_INPUT.search(prompt):
        return _stop(prompt, "RUNTIME_INTAKE_REQUIRED")
    labels = _labels(prompt)
    active = _CALC_OPT_OUT.sub(" ", prompt)
    surface = _intent_text(active, labels)
    decision = intent.resolve_intent(surface, prior_user_request=prior_surface)
    opt_out = bool(intent._OPT_OUT.search(prompt) or _CALC_OPT_OUT.search(prompt))
    refer = bool(_REFER.search(prompt)) and not opt_out
    new_general = bool(_NEW_GENERAL_TASK.search(surface)) and not intent._CHART.search(intent._OPT_OUT.sub(" ", surface)) and not intent._FORECAST.search(surface)
    if new_general:
        refer = False
        decision = intent.RequestIntent("general_followup")
    if not opt_out and labels and decision.intent == "general_followup" and not new_general:
        decision = intent.RequestIntent("chart_interpretation")
    inherited = refer and decision.intent == "general_followup" and last.get("response_mode") in {"chart_explanation", "day_explanation", "fact_lookup"}
    last_paths = last.get("diagnostics", {}).get("selected_paths", [])
    inherited_day = bool(inherited and (last.get("response_mode") == "day_explanation" or any(p.startswith("period") for p in last_paths)))
    unresolved_reference = False
    if inherited:
        decision = intent.RequestIntent("period_request" if inherited_day else "chart_interpretation")
    elif refer and decision.intent == "general_followup" and last.get("response_mode") not in {"general"}:
        unresolved_reference = True
    # 명시적으로 승인된 용어를 가리키는 후속도 현재 문장의 '사주' 유무와 분리한다.
    if refer and any(term in prompt for term in _NAMES):
        decision = intent.RequestIntent("chart_interpretation")
        unresolved_reference = False
    typed_day_followup = bool(intent._FOLLOWUP.fullmatch(surface.rstrip("?？.! ")) and last.get("response_mode") in {"chart_explanation", "day_explanation", "fact_lookup"})
    if typed_day_followup:
        decision = intent.RequestIntent("period_request")
        inherited_day = True
    scope_prompt = "일진 " + surface if inherited_day and (intent._RELATIVE.search(surface) or intent._DATE.search(surface) or intent._RANGE.search(surface)) else surface
    scope = intent.date_scope(scope_prompt, binding, today=today, prior_user_request=prior_surface)
    code = scope["reason_code"]
    selected_day_reference = bool(re.search(r"선택\s*(?:한\s*)?날짜|연결된\s*(?:날짜|일진)", prompt))
    if selected_day_reference and not intent._RELATIVE.search(prompt) and not intent._DATE.search(prompt) and not intent._RANGE.search(prompt) and code == intent.DATE_AMBIGUOUS:
        code = None
    unsupported = _unsupported_calculation(prompt)
    if unsupported:
        code = "RUNTIME_CALCULATION_UNSUPPORTED"
    elif _UNSUPPORTED.search(active) and _TERM_EXPLANATION.search(active) and not _PERSONAL.search(active):
        return ResponsePlan("model_generated", "general", prompt)
    if code:
        clauses = [c.strip() for c in intent._CLAUSE_BREAK.split(prompt) if c.strip()]
        general = [c for c in clauses if intent.resolve_intent(c).intent == "general_followup" and not _unsupported_calculation(c) and re.search(r"써\s*줘|작성|다듬|들어\s*줘|위로|공감|요약", c)]
        if code in {intent.SCOPE_UNSUPPORTED, "RUNTIME_CALCULATION_UNSUPPORTED"} and len(clauses) > 1 and general:
            active = "\n".join(general)
            return ResponsePlan("model_generated", "general", active, history_indices=_history(messages, "general", refer=False), notices=(_notice(code),))
        return _stop(prompt, code)
    if unresolved_reference:
        return _stop(prompt, intent.INTENT_CONFIRMATION)
    if decision.intent == "general_followup":
        return ResponsePlan("model_generated", "general", prompt, history_indices=_history(messages, "general", refer=refer))
    if binding is None:
        return _stop(prompt, "RUNTIME_CHART_REQUIRED")
    value = binding["value"]
    day = decision.intent == "period_request" or selected_day_reference
    if day and "period" not in value:
        return _stop(prompt, "RUNTIME_SINGLE_DAY_REQUIRED")
    labels = _labels(prompt)
    lookup = bool(re.search(r"뭐|무슨|어떤\s*글자|알려|확인|맞(?:아|나요|는지)|인가|이야|이니", prompt)) or prompt.rstrip("?？ ") in FIELD_PATHS
    if len(labels) == 1 and lookup and not _EXPLAIN.search(prompt) and not _PAST.search(prompt):
        label = labels[0]
        path = FIELD_PATHS[label]
        actual = get_path(value, path)
        if not isinstance(actual, str) or not actual:
            return _stop(prompt, "RUNTIME_FACT_UNCERTAIN")
        selected = project(value, [path])
        return ResponsePlan("direct_fact", "fact_lookup", prompt, render_fact(prompt, label, actual), selected_paths=(path,), selected_facts=selected)
    mode = "day_explanation" if day else "chart_explanation"
    paths = []
    if day:
        paths.append("period")
    if not day or re.search(r"원국|일간|일주|내\s*사주", prompt):
        paths.extend(["chart.status", "chart.fact_authority", "chart.message", "chart.limitations"])
        paths.append("chart.hard_facts.calculation_profile")
        paths.append("chart.hard_facts.solar_term_evidence")
        if labels:
            paths.extend(FIELD_PATHS[name].rsplit(".", 1)[0] for name in labels if name not in {"일진", "선택 날짜"})
        elif refer and last.get("diagnostics", {}).get("selected_paths"):
            paths.extend(p for p in last["diagnostics"]["selected_paths"] if p.startswith("chart."))
        else:
            paths.extend(["chart.hard_facts.pillars", "chart.hard_facts.day_master"])
    paths = sorted({p for p in paths if get_path(value, p) is not None})
    return ResponsePlan("model_generated", mode, prompt, selected_paths=tuple(paths), selected_facts=project(value, paths), history_indices=_history(messages, mode, refer=refer))


def model_messages(plan: ResponsePlan, previous: list[dict]) -> list[dict[str, str]]:
    if plan.kind != "model_generated":
        raise ValueError("모델을 사용하지 않는 응답입니다.")
    system = BASE_PROMPT
    if plan.mode != "general":
        system += "\n\n" + GROUNDED_PROMPT + "\n[현재 승인 사실]\n" + json.dumps(plan.selected_facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    selected = [{"role": previous[i]["role"], "content": previous[i]["content"]} for i in plan.history_indices]
    return [{"role": "system", "content": system}, *selected, {"role": "user", "content": plan.active_request}]
