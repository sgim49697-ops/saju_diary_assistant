# dashboard_grounding_v2.py - 원국·단일 날짜 요청 범위와 역할별 사실을 결정론적으로 진단한다.

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

SCORER_VERSION = "saju-bound-chart-grounding-v2.0.0"
DATE_REBIND = "RUNTIME_DATE_REBIND_REQUIRED"
SCOPE_UNSUPPORTED = "RUNTIME_PERIOD_SCOPE_UNSUPPORTED"
DATE_AMBIGUOUS = "RUNTIME_DATE_SELECTION_REQUIRED"
_DATE = re.compile(r"(?<!\d)(\d{4})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})(?:일)?(?!\d)")
_RELATIVE = re.compile(
    r"(?<![가-힣])(오늘|내일|모레|어제)(?![간가-힣])|(?:오늘|내일|모레|어제)(?=은|는|의|운세|사주|일진)"
)
_RANGE = re.compile(
    r"(?:이번|다음|지난)\s*(?:주|달|월|해)|주간|월간\s*운세|연간\s*운세|올해|내년|년\s*운세|월\s*운세|일\s*(?:부터|까지)|\d\s*[~∼～]"
)
_STEMS = "甲乙丙丁戊己庚辛壬癸"
_BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
_STEM_KO = "갑을병정무기경신임계"
_BRANCH_KO = "자축인묘진사오미신유술해"
_ALIASES = {
    _STEM_KO[i % 10] + _BRANCH_KO[i % 12]: _STEMS[i % 10] + _BRANCHES[i % 12]
    for i in range(60)
}
_VALUES = "|".join([*[re.escape(v) for v in _ALIASES.values()], *_ALIASES])
_PAIR = re.compile(rf"(?P<value>{_VALUES})(?:[가-힣]?주)?")
_LABEL = r"연주|년주|월주|일주|시주|일간|연간지|년간지|월간지|일진|세운"
_CLAIM = re.compile(
    rf"(?P<label>{_LABEL})(?:\s|[:：=·*]|은|는|이|가|의|간지|값|바로|입니다|이고|이고요|라고|서버|기준|에서|[()])*?(?P<value>{_VALUES}|[甲乙丙丁戊己庚辛壬癸]|갑목|을목|병화|정화|무토|기토|경금|신금|임수|계수)"
)
_REVERSE = re.compile(
    rf"(?P<value>{_VALUES}|갑목|을목|병화|정화|무토|기토|경금|신금|임수|계수)(?:\s|[()*,：:]|이|가|은|는|인|당신의|원국의|사주의)*(?P<label>{_LABEL})"
)


def kst_today() -> date:
    """HTTP 입력이 아닌 서버 시계를 사용한다."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def prompt_intent(prompt: str) -> str:
    normalized = re.sub(r"\s+", " ", prompt.casefold()).strip()
    if re.search(
        r"사주\s*(?:말고|빼고)|그냥\s*(?:얘기|이야기)|메시지|문자|메일|두\s*문장.*써",
        normalized,
    ):
        return "general_followup"
    temporal = bool(
        _RELATIVE.search(normalized)
        or _DATE.search(normalized)
        or _RANGE.search(normalized)
    )
    if re.search(r"일간|원국|명식|팔자|오행", normalized) and not re.search(
        r"운세|일진|흐름", normalized
    ):
        return "chart_interpretation"
    if re.search(
        r"쉬운\s*말|쉽게|한자.*어려|풀어\s*(?:말|설명)", normalized
    ) and not re.search(r"내일|모레|이번\s*주|다음", normalized):
        return "general_followup"
    if temporal or re.search(r"운세|일진|기간|\d{1,2}\s*월\s*\d{1,2}\s*일", normalized):
        return "period_request"
    if re.search(r"사주|간지|천간|지지|성향|해석", normalized):
        return "chart_interpretation"
    return "general_followup"


def period_facts(binding: dict[str, Any]) -> dict[str, Any]:
    value = binding.get("value", {}).get("period")
    if not isinstance(value, dict):
        return {}
    facts = value.get("hard_facts", {}).get("period", {})
    return facts if isinstance(facts, dict) else {}


def date_scope(
    prompt: str, binding: dict[str, Any] | None, *, today: date | None = None
) -> dict[str, Any]:
    """자유문장에서 계산하지 않고 기존 snapshot과의 호환성만 확인한다."""
    reference = today if today is not None else kst_today()
    result: dict[str, Any] = {
        "policy_version": "day-request-scope-v1.0.0",
        "server_kst_date": reference.isoformat(),
        "allowed": True,
        "reason_code": None,
        "requested_dates": [],
        "snapshot_date": period_facts(binding).get("target_date") if binding else None,
    }
    if binding is None or prompt_intent(prompt) != "period_request":
        return result
    code = None
    requested: set[str] = set()
    if _RANGE.search(prompt):
        code = SCOPE_UNSUPPORTED
    else:
        try:
            for match in _DATE.finditer(prompt):
                requested.add(date(*(int(v) for v in match.groups())).isoformat())
        except ValueError:
            code = DATE_AMBIGUOUS
        for match in _RELATIVE.finditer(prompt):
            word = re.match(r"오늘|내일|모레|어제", match.group()).group()
            requested.add(
                (
                    reference
                    + timedelta(
                        days={"오늘": 0, "내일": 1, "모레": 2, "어제": -1}[word]
                    )
                ).isoformat()
            )
        if re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일", prompt) and not _DATE.search(
            prompt
        ):
            code = DATE_AMBIGUOUS
        if not requested:
            code = code or DATE_AMBIGUOUS
        elif len(requested) > 1:
            code = SCOPE_UNSUPPORTED
        elif requested != {result["snapshot_date"]}:
            code = DATE_REBIND
        if period_facts(binding).get("period_type") != "day":
            code = DATE_REBIND
    result.update(
        allowed=code is None, reason_code=code, requested_dates=sorted(requested)
    )
    if code:
        result["message"] = {
            SCOPE_UNSUPPORTED: "현재 연결은 하루 일진만 지원합니다. 주·월·연간 범위는 생성하지 않습니다. 날짜 선택에서 하루를 계산한 뒤 새 연결 대화를 시작하세요.",
            DATE_REBIND: "질문 날짜가 연결된 일진 날짜와 다릅니다. 날짜 선택에서 해당 하루를 계산한 뒤 새 연결 대화를 시작하세요.",
            DATE_AMBIGUOUS: "질문 날짜를 하나로 확인할 수 없습니다. 날짜 선택에서 연도와 날짜를 지정하고 새 연결 대화를 시작하세요.",
        }[code]
    return result


def _canonical(value: str) -> str:
    if value in _ALIASES:
        return _ALIASES[value]
    if value in {
        "갑목",
        "을목",
        "병화",
        "정화",
        "무토",
        "기토",
        "경금",
        "신금",
        "임수",
        "계수",
    }:
        return _STEMS[_STEM_KO.index(value[0])]
    return value


def _asserted_text(output: str) -> str:
    # 인용한 잘못된 전제 뒤 명시적 부정·교정이 있는 경우에만 인용을 제외한다.
    output = re.sub(
        r"[\"'‘“]([^\"'’”\n]+)[\"'’”](?=.{0,24}(?:아니|틀|잘못|오류))", "", output
    )
    return re.sub(r"[\"'‘’“”]", "", output)


def audit_output(prompt: str, output: str, binding: dict[str, Any]) -> dict[str, Any]:
    """유한 label 문법의 사실 검사이며 자연스러움·해석 의미 점수가 아니다."""
    intent = prompt_intent(prompt)
    chart = binding["value"]["chart"]["hard_facts"]
    pillars = chart.get("pillars", {})
    expected = {
        f"natal_{key}": item.get("ganzhi")
        for key, item in pillars.items()
        if isinstance(item, dict)
    }
    master = chart.get("day_master", {})
    expected["day_master"] = master.get("stem") if isinstance(master, dict) else master
    period = period_facts(binding)
    expected.update(
        {
            f"period_{key}": period.get(f"{key}_ganzhi")
            for key in ("year", "month", "day")
        }
    )
    label_roles = {
        "연주": "natal_year",
        "년주": "natal_year",
        "월주": "natal_month",
        "일주": "natal_day",
        "시주": "natal_hour",
        "일간": "day_master",
        "연간지": "period_year",
        "년간지": "period_year",
        "세운": "period_year",
        "월간지": "period_month",
        "일진": "period_day",
    }
    reasons: list[str] = []
    claims: list[dict[str, Any]] = []
    text = _asserted_text(output)
    for clause in re.split(r"[\n,;。!?]|(?<!\d)\.(?!\d)", text):
        found = list(_CLAIM.finditer(clause)) + list(_REVERSE.finditer(clause))
        for match in found:
            suffix = clause[match.end() :]
            if re.match(
                r"\s*[)）]?\s*(?:이|가|은|는)?\s*(?:아니|아닙|아닌|아니다|틀|잘못)",
                suffix,
            ):
                continue
            value = _canonical(match["value"])
            role = label_roles[match["label"]]
            if role.startswith("natal_") and re.search(
                r"(?:오늘|선택\s*날짜|해당\s*날짜|내일)의?\s*$", clause[: match.start()]
            ):
                role = role.replace("natal_", "period_")
            correct = expected.get(role) == value
            claims.append({"role": role, "value": value, "correct": correct})
            if not correct:
                reasons.append(f"{role}_value_mismatch")
    valid_roles = {claim["role"] for claim in claims if claim["correct"]}
    compact = re.sub(r"\s+", "", output)
    if re.search(
        r"(?:생년월일|출생시간|출생정보).{0,16}(?:알려|입력해|필요합니다)", compact
    ):
        reasons.append("birth_input_reasked")
    if re.search(r"(?:원국|명식).{0,12}(?:없습니다|제공되지|연결되지)", compact):
        reasons.append("bound_chart_denied")
    if re.search(
        r"snapshot|capability|system\s*prompt|내부검증|해시|hash", output, re.IGNORECASE
    ):
        reasons.append("internal_contract_exposed")
    if intent == "chart_interpretation":
        if "일간" in prompt:
            if "day_master" not in valid_roles:
                reasons.append("day_master_fact_missing")
        elif not any(role.startswith("natal_") for role in valid_roles):
            natal_values = {
                value
                for key, value in expected.items()
                if key.startswith("natal_") and value
            }
            if not any(
                _canonical(match["value"]) in natal_values
                for match in _PAIR.finditer(text)
            ):
                reasons.append("chart_fact_missing")
        if re.search(
            r"(?:네|4|네\s*개)\s*기둥|(?:원국|사주).*(?:전부|모두|나열)|연주.*월주.*일주.*시주",
            prompt,
        ) and any(
            expected.get(f"natal_{key}") and f"natal_{key}" not in valid_roles
            for key in ("year", "month", "day", "hour")
        ):
            reasons.append("natal_pillars_omitted")
    if intent == "period_request" and "period_day" not in valid_roles:
        reasons.append("period_day_fact_missing")
    if intent == "period_request":
        for match in _DATE.finditer(text):
            try:
                rendered_date = date(*(int(v) for v in match.groups())).isoformat()
            except ValueError:
                rendered_date = "invalid"
            if rendered_date != period.get("target_date"):
                reasons.append("period_target_date_mismatch")
    reasons = list(dict.fromkeys(reasons))
    return {
        "gate_id": SCORER_VERSION,
        "scorer_version": SCORER_VERSION,
        "intent": intent,
        "passed": not reasons,
        "reasons": reasons,
        "claims": claims,
        "warning_code": None if not reasons else "RUNTIME_GROUNDING_WARNING",
        "chart_markers": [
            value
            for key, value in expected.items()
            if key.startswith("natal_") and value
        ],
        "period_markers": [
            value
            for key, value in expected.items()
            if key.startswith("period_") and value
        ],
        "semantics": "not_measured",
        "naturalness": "not_measured",
    }
