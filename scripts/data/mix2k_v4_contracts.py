# mix2k_v4_contracts.py - 8K MIX2K v4 행·teacher·토큰 계약을 fail-closed로 검증한다.

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes

DATASET_VERSION = "mix2k-v4-chart-day-8k"
RECORD_SCHEMA_VERSION = "4.0.0"
RUNTIME_RELEASE_ID = "saju-runtime-release-v1.5.0-8b1d6ea2d46e"
RUNTIME_BINDING_ID = "saju-chart-day-dashboard-binding-v1.1.0"
RUNTIME_BINDING_SCHEMA = "1.1.0"
EXPECTED_ROWS = 2_000
MAX_PROMPT_TOKENS = 4_096
MAX_COMPLETION_TOKENS = 4_096
MAX_LENGTH = 8_192
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

EXPECTED_AXES = {
    "structured_fact_schema_literacy": 300,
    "chart_facts_natural_explanation": 300,
    "chart_day_today_flow": 450,
    "followup_explain_grounding": 300,
    "intake_state_correction": 250,
    "general_korean_empathy": 250,
    "uncertainty_blocked_boundary": 100,
    "hard_fact_short_qa": 50,
}
SUBSTANTIVE_AXES = frozenset(
    {
        "structured_fact_schema_literacy",
        "chart_facts_natural_explanation",
        "chart_day_today_flow",
        "followup_explain_grounding",
        "general_korean_empathy",
        "uncertainty_blocked_boundary",
    }
)
RUNTIME_AXES = frozenset(
    {
        "structured_fact_schema_literacy",
        "chart_facts_natural_explanation",
        "chart_day_today_flow",
        "followup_explain_grounding",
    }
)
DAY_AXES = RUNTIME_AXES
MULTITURN_AXES = frozenset({"followup_explain_grounding"})

FULL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GANYI = re.compile(r"[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]")
NUMERIC_DATE = re.compile(
    r"(?<!\d)(20[0-4][0-9])\s*[-./]\s*(0?[1-9]|1[0-2])\s*[-./]\s*"
    r"(0?[1-9]|[12][0-9]|3[01])(?!\d)"
)
KOREAN_DATE = re.compile(
    r"(?<!\d)(20[0-4][0-9])\s*년\s*(0?[1-9]|1[0-2])\s*월\s*"
    r"(0?[1-9]|[12][0-9]|3[01])\s*일"
)
SENTENCE_END = re.compile(r"(?:[.!?]|[。！？])(?:[\"'”’)]*)?(?=\s|$)")
NEGATED_STRUCTURAL_CLAIM = re.compile(
    r"(?:제공되지|주어지지|확인되지|계산하지|계산되지|판단하지|"
    r"단정하지|포함되지|다루지|알\s*수\s*없|근거가\s*없|범위가\s*아니|"
    r"범위(?:에|에는)\s*(?:(?:들어\s*)?있지\s*않|없)|범위가\s*아닙)"
)
UNSUPPORTED_ACTION_NEGATION = re.compile(
    r"(?:통근|득령|신강약|신강|신약|격국|용신|대운|세운|삼합|육합|방합|"
    r"암합|천간합|지지합|합충(?:형파해)?|상충|형살|파살|해살)"
    r"[^\n.!?。！？;；]{0,56}?"
    r"(?:(?:만들어\s*)?(?:말할|계산할|정할|판단할|도출할|확정할)\s*수\s*없|"
    r"(?:계산|판단|도출|확정|정)해서는\s*안)"
)
LOCAL_CLAIM_NEGATION = re.compile(
    r"(?:은|는|이|가|을|를)?\s*"
    r"(?:아닙|아닌|아니(?:라|다|라고|며|고|었|입|어서|므로))"
)
CORRECTION_CONNECTOR = (
    r"(?:(?:아니(?:라|고|어서|므로)|아닌(?:데)?|"
    r"(?:이라고|라고)?\s*볼\s*수(?:는|가)?\s*없(?:고|어서)?)"
    r"|(?:(?:이라고|라고|로)\s*)?(?:보면|읽으면)\s*안\s*"
    r"(?:되고|되며|되어서|돼서))"
)
CLAUSE_BOUNDARY = re.compile(
    r"[\n.!?。！？;；]|하지만|그러나|그래도|반면(?:에)?|지만|"
    r"(?:않|없|아니)(?:았|었|했)?(?:고|으며|는데|으나)|"
    r"(?:으므로|므로|더라도|음에도|는데도|고도)"
)
INTERNAL_LANGUAGE = re.compile(
    r"(?:canonical|runtime|snapshot|capability|fact[_ -]?authority|내부\s*해시|승인된\s*사실)",
    re.IGNORECASE,
)
FORBIDDEN_PREDICTION = re.compile(
    r"(?:반드시|확실히|틀림없이).{0,24}(?:생긴다|일어난다|된다|성공|이별|결혼|합격|부자)",
)
TEN_GODS = frozenset(
    {
        "비견",
        "겁재",
        "식신",
        "상관",
        "편재",
        "정재",
        "편관",
        "정관",
        "편인",
        "정인",
    }
)
STEM_ENTITY = r"[甲乙丙丁戊己庚辛壬癸]"
BRANCH_ENTITY = r"[子丑寅卯辰巳午未申酉戌亥]"
TEN_GOD_ENTITY = (
    "(?:" + "|".join(sorted(TEN_GODS | {"일간"}, key=len, reverse=True)) + ")"
)
SURFACE_ELEMENT_COUNT = re.compile(
    r"(?<![가-힣])([목화토금수])(?:\s*(?:수치|개수))?"
    r"(?:은|는|이|가|:|=|\s)*([0-9]+)(?:\s*개)?"
)
UNSUPPORTED_STRUCTURAL_PATTERNS = (
    (
        "rooting",
        re.compile(
            r"(?:일간|[甲乙丙丁戊己庚辛壬癸](?:목|화|토|금|수)?)"
            r"[^\n.!?]{0,24}(?:지지|[子丑寅卯辰巳午未申酉戌亥])[^\n.!?]{0,12}"
            r"뿌리(?:를|가|는)?\s*(?:두|내리|있)"
        ),
    ),
    ("rooting", re.compile(r"통근(?:하|했|되|된|이|을|은|는)")),
    ("seasonal_strength", re.compile(r"득령(?:하|했|되|된|이|을|은|는)")),
    (
        "strength_pattern_yongshin",
        re.compile(
            r"(?:신강약|신강|신약|격국|용신)"
            r"(?:은|는|이|가|을|를|의|으로|이다|입니다|에|하|합|했)"
        ),
    ),
    (
        "relation",
        re.compile(
            r"(?:삼합|육합|방합|암합|천간합|지지합|합충(?:형파해)?|상충|형살|파살|해살|"
            r"충돌?\s*관계)"
        ),
    ),
    (
        "relation",
        re.compile(
            r"(?:[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]|서로|둘이|두\s*기운)"
            r"[^\n.!?]{0,16}(?:합|충|형|파|해)(?:을|이|으로)?\s*"
            r"(?:이루|이룬|이룹|성립|작용|한다|합니다|됩니다|이다|입니다)"
        ),
    ),
    (
        "fortune_cycle",
        re.compile(r"(?:대운|세운)(?:은|는|이|가|을|를|의|에서|으로|이다|입니다)"),
    ),
)
PILLAR_LABELS = {
    "연주": "year",
    "년주": "year",
    "월주": "month",
    "일주": "day",
    "시주": "hour",
}
PERIOD_LABELS = {
    "연간지": "year_ganzhi",
    "해의 간지": "year_ganzhi",
    "월간지": "month_ganzhi",
    "달의 간지": "month_ganzhi",
    "일진": "day_ganzhi",
    "일간지": "day_ganzhi",
    "날의 간지": "day_ganzhi",
    "하루 간지": "day_ganzhi",
    "하루의 간지": "day_ganzhi",
}
PILLAR_CLAIM_SEPARATORS = (
    r"(?:에서는|에는|에서|으로|이고|이며|입니다|은|는|이|가|도|의|로|"
    r":|=|\(|\)|\s|[-·,/])*"
)
HIDDEN_STEM_SEPARATOR = r"(?:[\s,，·/]+|\s*(?:과|와|그리고)\s*)"
RESTRICTED_MARKERS = re.compile(
    r"(?:AI\s*Hub|aihub|개인정보|주민등록|전화번호|restricted_local_only\s*[=:]\s*true)",
    re.IGNORECASE,
)
UNAMBIGUOUS_SAJU_INJECTION = re.compile(
    r"사주|원국|명식|팔자|년주|월주|시주|천간|지지|지장간|십신|"
    r"비견|겁재|식신|편재|정재|편관|정관|편인|정인|신강|신약|격국|"
    r"용신|대운|세운|합충|오행|일진|"
    rf"{GANYI.pattern}"
)

SPEC_FIELDS = {
    "schema_version",
    "dataset_version",
    "id",
    "conversation_id",
    "task_axis",
    "template_family",
    "substantive",
    "multiturn",
    "drafter",
    "reviewer",
    "prompt",
    "runtime_binding",
    "allowed_fact_paths",
    "allowed_fact_values",
    "response_contract",
    "restricted_local_only",
}
DRAFT_FIELDS = {
    "record_id",
    "answer",
    "used_fact_paths",
    "used_fact_values",
    "soft_interpretation_used",
    "limitations",
    "self_check",
}
REVIEW_FIELDS = {
    "record_id",
    "decision",
    "failure_codes",
    "fact_errors",
    "style_notes",
    "rewrite_instructions",
}


class Mix2KV4ContractError(RuntimeError):
    """MIX2K v4의 데이터·외부 전송·학습 후보 계약 위반."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z가-힣甲-龥]+", "", normalized)


def nonempty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def sentence_count(value: str) -> int:
    return len(SENTENCE_END.findall(value.strip()))


def normalized_dates(value: str) -> list[str]:
    """한국어·점·슬래시 날짜 표기를 비교 가능한 ISO 값으로 정규화한다."""

    matches = [
        (match.start(), *match.groups())
        for pattern in (NUMERIC_DATE, KOREAN_DATE)
        for match in pattern.finditer(value)
    ]
    return [
        f"{year}-{int(month):02d}-{int(day):02d}"
        for _, year, month, day in sorted(matches)
    ]


def _contextual_partial_date_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """일진 claim에 붙은 월·일/일 단독 표기를 target date와 대조한다."""

    target = _path_value(spec, "period.hard_facts.period.target_date")
    target_match = (
        re.fullmatch(r"(20[0-4][0-9])-([01][0-9])-([0-3][0-9])", target)
        if target is not None
        else None
    )
    if target_match is None:
        return []
    target_year, target_month, target_day = map(int, target_match.groups())
    context = re.compile(
        rf"(?:일진|일간지|오늘|이날|그날|선택\s*날짜|해당\s*날짜|{GANYI.pattern})"
    )
    month_day = re.compile(
        r"(?<![0-9./-])(0?[1-9]|1[0-2])\s*(?:월\s*|[/.]\s*)"
        r"(0?[1-9]|[12][0-9]|3[01])(?!\d)\s*(?:일)?"
    )
    day_only = re.compile(r"(?<![0-9./-])([1-9]|[12][0-9]|3[01])\s*일")

    def duration_use(match: re.Match[str], clause: str) -> bool:
        return (
            re.match(
                r"\s*(?:치|분|동안|간(?:격)?|정도|연속|뒤|후|전|마다|째(?:에|부터)?)",
                clause[match.end() :],
            )
            is not None
        )

    errors: list[str] = []
    for clause in re.split(r"[\n!?。！？;；]+", answer):
        if context.search(clause) is None:
            continue
        year_match = re.search(r"(?<!\d)(20[0-4][0-9])\s*년", clause)
        claim_year = int(year_match.group(1)) if year_match else target_year
        occupied: list[tuple[int, int]] = []
        for match in month_day.finditer(clause):
            occupied.append(match.span())
            month, day = map(int, match.groups())
            if (claim_year, month, day) != (target_year, target_month, target_day):
                errors.append(f"unprovided_date:{claim_year:04d}-{month:02d}-{day:02d}")
        for match in day_only.finditer(clause):
            if any(
                start <= match.start() and match.end() <= end for start, end in occupied
            ) or duration_use(match, clause):
                continue
            day = int(match.group(1))
            if (claim_year, target_month, day) != (
                target_year,
                target_month,
                target_day,
            ):
                errors.append(
                    f"unprovided_date:{claim_year:04d}-{target_month:02d}-{day:02d}"
                )
    return errors


def flatten_runtime_facts(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        for key in sorted(value):
            child = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(flatten_runtime_facts(value[key], child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            flattened.extend(flatten_runtime_facts(item, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        flattened.append((prefix, value))
    elif isinstance(value, (int, float, bool)) or value is None:
        flattened.append((prefix, json.dumps(value, ensure_ascii=False)))
    return flattened


def _path_value(spec: Mapping[str, Any], suffix: str) -> str | None:
    for path, value in zip(
        spec["allowed_fact_paths"], spec["allowed_fact_values"], strict=True
    ):
        if path.endswith(suffix):
            return value
    return None


def _claim_clause(answer: str, start: int, end: int) -> str:
    left = 0
    for match in CLAUSE_BOUNDARY.finditer(answer, 0, start):
        left = match.end()
    right_match = CLAUSE_BOUNDARY.search(answer, end)
    right = right_match.start() if right_match is not None else len(answer)
    return answer[left:right]


def _explicit_claim_is_negated(answer: str, start: int, end: int) -> bool:
    """잘못된 label 바로 뒤의 `아니다/아니라`만 해당 claim의 부정으로 본다."""

    local_tail = answer[start : min(len(answer), end + 32)]
    return (
        LOCAL_CLAIM_NEGATION.search(local_tail) is not None
        or re.search(
            r"(?:되지|하지)(?:\s*않|는|고|만|도|라|$)|"
            r"(?:되어서는|해서는|되면|하면)\s*안",
            local_tail,
        )
        is not None
        or re.search(
            r"(?:이라고|라고)?\s*(?:볼|말할|부를)\s*수(?:는|가)?\s*없",
            local_tail,
        )
        is not None
        or re.search(
            r"(?:라는|이라는|이라고\s*하는)\s*(?:뜻|말|의미)(?:은|는|이|가)?\s*"
            r"(?:아닙|아니(?:다|라고|며|고|었|입))",
            local_tail,
        )
        is not None
        or re.match(
            rf"\s*(?:은|는|이|가)?\s*{CORRECTION_CONNECTOR}",
            answer[end : min(len(answer), end + 48)],
        )
        is not None
    )


def _unsupported_claim_is_negated(answer: str, start: int, end: int) -> bool:
    """unsupported 용어와 직접 맞닿은 근거 부재 표현만 면제한다."""

    clause = _claim_clause(answer, start, end)
    return (
        NEGATED_STRUCTURAL_CLAIM.search(clause) is not None
        or UNSUPPORTED_ACTION_NEGATION.search(clause) is not None
        or LOCAL_CLAIM_NEGATION.search(clause) is not None
    )


def _ordinary_sanggwan_use(answer: str, end: int) -> bool:
    """십신이 아니라 `상관없이/상관없는/상관관계`인 일반어 사용을 구분한다."""

    return (
        re.match(r"\s*(?:은|는|이|가|도)?\s*(?:없|없이|없는|관계)", answer[end:])
        is not None
    )


def _mentioned_ten_gods(answer: str) -> set[str]:
    """일반어 `상관`을 제외하고 답변이 실제로 명시한 십신만 반환한다."""

    return {
        value
        for value in TEN_GODS
        if any(
            not (value == "상관" and _ordinary_sanggwan_use(answer, match.end()))
            for match in re.finditer(re.escape(value), answer)
        )
    }


def _label_closes_prior_claim(
    answer: str, label: str, start: int, value_start: int
) -> bool:
    """`丙午는 … 아니라 연간지이고, 己卯…`의 연간지를 후행 값과 엮지 않는다."""

    between = answer[start + len(label) : value_start]
    return re.match(r"\s*(?:이고|이며|이고요|입니다|이다|라고)", between) is not None


def _label_completes_reverse_claim(answer: str, label_start: int) -> bool:
    clause_start = max(
        (answer.rfind(boundary, 0, label_start) for boundary in "\n.!?。！？;；"),
        default=-1,
    )
    prefix = answer[clause_start + 1 : label_start]
    return (
        re.search(
            rf"{GANYI.pattern}\s*(?:은|는|이|가)\s*"
            r"(?:(?:이|해당)\s*)?(?:(?:원국|날짜|오늘)(?:의)?\s*)?$",
            prefix,
        )
        is not None
    )


def _label_pattern(labels: Mapping[str, str]) -> str:
    values = []
    for label in sorted(labels, key=len, reverse=True):
        values.append(r"일간(?!지)" if label == "일간" else re.escape(label))
    return "(?:" + "|".join(values) + ")"


def _labeled_entity_claims(
    answer: str,
    labels: Mapping[str, str],
    entity_pattern: str,
    *,
    maximum_gap: int = 24,
) -> list[tuple[str, str, int, int]]:
    """괄호·조사·구두점 및 `값은 … label` 역방향 구조 claim을 추출한다."""

    label_source = _label_pattern(labels)
    label_finder = re.compile(label_source)
    all_label_finder = re.compile(
        _label_pattern({**PILLAR_LABELS, **PERIOD_LABELS, "일간": "stem"})
    )
    direct_gap = re.compile(
        r"^\s*(?:\([^()\n]{0,18}\)\s*)?(?:"
        r"(?:(?:(?:의\s*)?(?:기둥|항목)|(?:라는|이란)\s*(?:기둥|값|간지|항목))\s*)?"
        r"(?::|：|=)?\s*(?:의\s*)?"
        r"(?:(?:간지(?:\s*두\s*글자)?|값|사실)\s*)?"
        r"(?:은|는|이|가|인|에|에는|을|를|으로|로|으로는|로는|도|:|：|=|,|，)?"
        r"\s*(?:우연히\s*)?[\(\[\{]?|"
        r"값으로\s*등록된\s*것(?:은|는|이|가)?\s*|"
        r"에\s*해당하는\s*(?:간지|값|글자)(?:은|는|이|가|:|：|=)?\s*|"
        r"(?:의\s*)?자리(?:에|에는)\s*|"
        r"(?:으)?로\s*(?:확인|표시|등록)되는\s*"
        r"(?:간지|값|글자)?(?:은|는|이|가|:|：|=)?\s*)$"
    )
    reverse_gap = re.compile(
        r"^\s*(?:(?:이|해당)\s*)?(?:(?:원국|날짜|오늘)(?:의)?\s*)?"
        r"(?:(?:간지|값|사실|항목|기둥)\s*(?:은|는|이|가|인)?\s*)?$"
    )
    claims: list[tuple[str, str, int, int]] = []
    forward = re.compile(
        rf"(?P<label>{label_source})(?P<gap>[^\n.!?。！？]{{0,{maximum_gap}}}?)"
        rf"(?P<value>{entity_pattern})"
    )
    for match in forward.finditer(answer):
        gap = match.group("gap")
        if (
            "각각" in gap
            or label_finder.search(gap) is not None
            or all_label_finder.search(gap) is not None
            or direct_gap.fullmatch(gap) is None
            or _label_completes_reverse_claim(answer, match.start("label"))
            or _label_closes_prior_claim(
                answer,
                match.group("label"),
                match.start("label"),
                match.start("value"),
            )
            or _explicit_claim_is_negated(answer, match.start(), match.end())
        ):
            continue
        claims.append(
            (
                match.group("label"),
                match.group("value"),
                match.start(),
                match.end(),
            )
        )
    correction = re.compile(
        rf"(?P<label>{label_source})(?P<prefix>[^\n.!?。！？]{{0,{maximum_gap}}}?)"
        rf"(?P<negated>{entity_pattern})\s*(?:은|는|이|가)?\s*"
        rf"{CORRECTION_CONNECTOR}\s*"
        rf"(?P<gap>[^\n.!?。！？]{{0,{maximum_gap}}}?)"
        rf"(?P<value>{entity_pattern})"
    )
    for match in correction.finditer(answer):
        following_text = answer[match.end("value") :]
        following_label = re.match(
            rf"\s*(?:은|는|이|가)\s*(?P<label>{all_label_finder.pattern})",
            following_text,
        )
        following_subject_clause = re.match(
            rf"\s*(?:은|는|이|가)\s*[^\n.!?。！？]{{1,40}}?"
            rf"(?:이고|이며|인데|이고요)[,，\s]*{entity_pattern}\s*"
            rf"(?:은|는|이|가)\s*[^\n.!?。！？]{{0,12}}?"
            rf"{re.escape(match.group('label'))}",
            following_text,
        )
        if (
            "각각" in match.group("prefix")
            or all_label_finder.search(match.group("prefix")) is not None
            or all_label_finder.search(match.group("gap")) is not None
            or (
                following_label is not None
                and following_label.group("label") != match.group("label")
            )
            or following_subject_clause is not None
        ):
            continue
        claims.append(
            (
                match.group("label"),
                match.group("value"),
                match.start(),
                match.end(),
            )
        )
    reverse = re.compile(
        rf"(?P<value>{entity_pattern})\s*(?:은|는|이|가)?\s*"
        rf"(?P<gap>[^\n.!?。！？]{{0,{maximum_gap}}}?)(?P<label>{label_source})"
    )
    for match in reverse.finditer(answer):
        gap = match.group("gap")
        if (
            "각각" in gap
            or label_finder.search(gap) is not None
            or all_label_finder.search(gap) is not None
            or reverse_gap.fullmatch(gap) is None
            or _explicit_claim_is_negated(answer, match.start(), match.end())
            or re.match(
                rf"\s*(?:{entity_pattern})?\s*(?:과|와)\s*"
                r"(?:다른|다르|구분|별개)",
                answer[match.end() :],
            )
        ):
            continue
        claims.append(
            (
                match.group("label"),
                match.group("value"),
                match.start(),
                match.end(),
            )
        )
    return claims


def _corrected_owner_claims(
    answer: str, labels: Mapping[str, str], entity_pattern: str
) -> list[tuple[str, str]]:
    """`X는 A가 아니라 B`에서 최종 owner B만 구조 claim으로 읽는다."""

    label_source = _label_pattern(labels)
    pattern = re.compile(
        rf"(?P<value>{entity_pattern})\s*(?:은|는|이|가)\s*"
        rf"(?P<negated>{label_source})\s*(?:은|는|이|가)?\s*"
        rf"아니(?:라|고)\s*(?P<label>{label_source})"
    )
    return [
        (match.group("label"), match.group("value"))
        for match in pattern.finditer(answer)
        if match.group("label") != match.group("negated")
    ]


def _parallel_ganzhi_label_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """`연주·월주…는 (각각/순서대로) A·B…` 위치 대응을 검증한다."""

    errors: list[str] = []
    label_pattern = re.compile(
        "|".join(sorted([*PILLAR_LABELS, *PERIOD_LABELS], key=len, reverse=True))
    )
    for clause in re.split(r"[\n.!?。！？;；]+", answer):
        groups: list[tuple[list[str], list[str]]] = []
        if "각각" in clause:
            markers = list(re.finditer("각각", clause))
            previous_end = 0
            for index, marker in enumerate(markers):
                following_start = (
                    markers[index + 1].start()
                    if index + 1 < len(markers)
                    else len(clause)
                )
                before = clause[previous_end : marker.start()]
                after = clause[marker.end() : following_start]
                labels = [
                    label_match.group(0)
                    for label_match in label_pattern.finditer(before)
                ]
                groups.append((labels, GANYI.findall(after)))
                previous_end = marker.end()
        else:
            labels = [match.group(0) for match in label_pattern.finditer(clause)]
            values = GANYI.findall(clause)
            if len(labels) < 2 or len(labels) != len(values):
                continue
            if re.search(r"(?:차이|구분|다르|비교)", clause):
                continue
            grouped_labels = (
                rf"(?:{label_pattern.pattern})(?:\s*(?:와|과|,|，|·|/)\s*"
                rf"(?:{label_pattern.pattern}))+\s*(?:은|는|:|：)"
            )
            if (
                re.search(r"(?:순서|순으로|순:|순：)", clause) is None
                and re.search(grouped_labels, clause) is None
            ):
                continue
            groups.append((labels, values))
        for labels, values in groups:
            if not labels or len(values) < len(labels):
                continue
            natal_only = all(label in PILLAR_LABELS for label in labels)
            period_only = all(label in PERIOD_LABELS for label in labels)
            if not (natal_only or period_only):
                continue
            for label, value in zip(labels, values, strict=False):
                if natal_only:
                    field = PILLAR_LABELS[label]
                    expected = _path_value(
                        spec, f"chart.hard_facts.pillars.{field}.ganzhi"
                    )
                    prefix = f"natal_{field}_label_confusion"
                else:
                    field = PERIOD_LABELS[label]
                    expected = _path_value(spec, f"period.hard_facts.period.{field}")
                    prefix = f"period_{field}_label_confusion"
                if expected is not None and value != expected:
                    errors.append(f"{prefix}:{value}")
    implicit_sequences = (
        (
            re.compile(
                rf"(?:원국(?:의)?\s*)?(?:네|4)\s*기둥"
                rf"[^\n.!?。！？;；]{{0,20}}?(?:순서대로|순으로|순\s*[:：])\s*"
                rf"(?P<values>{GANYI.pattern}(?:[\s,，·/]+{GANYI.pattern}){{3}})"
            ),
            ("year", "month", "day", "hour"),
            "natal",
        ),
        (
            re.compile(
                rf"(?:선택(?:한)?\s*날짜(?:의)?\s*)?(?:세|3)\s*간지"
                rf"[^\n.!?。！？;；]{{0,20}}?(?:순서대로|순으로|순\s*[:：])\s*"
                rf"(?P<values>{GANYI.pattern}(?:[\s,，·/]+{GANYI.pattern}){{2}})"
            ),
            ("year_ganzhi", "month_ganzhi", "day_ganzhi"),
            "period",
        ),
    )
    for pattern, fields, owner in implicit_sequences:
        for match in pattern.finditer(answer):
            values = GANYI.findall(match.group("values"))
            for field, value in zip(fields, values, strict=True):
                path = (
                    f"chart.hard_facts.pillars.{field}.ganzhi"
                    if owner == "natal"
                    else f"period.hard_facts.period.{field}"
                )
                expected = _path_value(spec, path)
                if expected is not None and value != expected:
                    errors.append(f"{owner}_{field}_label_confusion:{value}")
    return errors


def parallel_pillar_field_claims(answer: str) -> list[tuple[str, str, str]]:
    """pillar×field 병렬·행렬 claim을 row-major 위치로 추출한다."""

    label_pattern = re.compile("|".join(sorted(PILLAR_LABELS, key=len, reverse=True)))
    field_pattern = re.compile(
        r"(?P<stem_ten_god>stem[_ -]?ten[_ -]?god|천간(?:의)?\s*십신)|"
        r"(?P<branch_ten_god>branch[_ -]?ten[_ -]?god|지지(?:의)?\s*십신)|"
        r"(?P<stem_element>stem[_ -]?element|천간(?:의)?\s*오행)|"
        r"(?P<branch_element>branch[_ -]?element|지지(?:의)?\s*오행)|"
        r"(?P<stem_yin_yang>stem[_ -]?yin[_ -]?yang|천간(?:의)?\s*음양)|"
        r"(?P<branch_yin_yang>branch[_ -]?yin[_ -]?yang|지지(?:의)?\s*음양)|"
        r"(?P<stem>천간|\bstem\b)|(?P<branch>지지|\bbranch\b)",
        re.IGNORECASE,
    )
    claims: list[tuple[str, str, str]] = []
    last_labels: list[str] = []
    last_fields: list[str] = []
    last_positions: list[tuple[str, str]] = []
    previous_end = 0
    markers = list(re.finditer("각각", answer))
    for marker_index, marker in enumerate(markers):
        prefix = answer[previous_end : marker.start()]
        boundary = max(
            (
                prefix.rfind(value)
                for value in ("\n", ".", "!", "?", "。", "！", "？", ";", "；")
            ),
            default=-1,
        )
        if boundary >= 0:
            prefix = prefix[boundary + 1 :]
            if (
                "같은 순서" not in prefix
                and re.match(r"\s*(?:천간|지지|stem\b|branch\b)", prefix, re.IGNORECASE)
                is None
            ):
                last_labels = []
        label_matches = list(label_pattern.finditer(prefix))
        explicit = [match.group(0) for match in label_matches]
        if explicit:
            last_labels = explicit
        if not last_labels:
            previous_end = marker.end()
            continue
        field_matches = list(field_pattern.finditer(prefix))
        explicit_fields = [
            next(name for name, value in match.groupdict().items() if value is not None)
            for match in field_matches
        ]
        if explicit_fields:
            last_fields = explicit_fields
        if not last_fields:
            previous_end = marker.end()
            continue
        pairwise_positions: list[tuple[str, str]] = []
        if len(label_matches) == len(field_matches) and len(label_matches) > 1:
            for index, label_match in enumerate(label_matches):
                interval_end = (
                    label_matches[index + 1].start()
                    if index + 1 < len(label_matches)
                    else len(prefix)
                )
                interval_fields = [
                    field_match
                    for field_match in field_matches
                    if label_match.end() <= field_match.start() < interval_end
                ]
                if len(interval_fields) != 1:
                    pairwise_positions = []
                    break
                field = next(
                    name
                    for name, value in interval_fields[0].groupdict().items()
                    if value is not None
                )
                pairwise_positions.append((PILLAR_LABELS[label_match.group(0)], field))
        if pairwise_positions:
            last_positions = pairwise_positions
        elif explicit or explicit_fields or not last_positions:
            last_positions = [
                (PILLAR_LABELS[label], field)
                for label in last_labels
                for field in last_fields
            ]
        active_positions = list(last_positions)
        detail_match = re.search(
            r"(?P<detail>오행|음양)(?:은|는|이|가|:|：)?\s*"
            r"(?:같은\s*순서(?:로)?\s*)?$",
            prefix,
        )
        if detail_match is not None:
            suffix_name = (
                "element" if detail_match.group("detail") == "오행" else "yin_yang"
            )
            mapped_positions: list[tuple[str, str]] = []
            for pillar, field in last_positions:
                base_field = re.sub(r"_(?:element|yin_yang)$", "", field)
                if base_field not in {"stem", "branch"}:
                    mapped_positions = []
                    break
                mapped_positions.append((pillar, f"{base_field}_{suffix_name}"))
            if mapped_positions:
                active_positions = mapped_positions
        following_end = (
            markers[marker_index + 1].start()
            if marker_index + 1 < len(markers)
            else len(answer)
        )
        suffix = answer[marker.end() : following_end]
        boundary = re.search(r"[\n.!?。！？;；]", suffix)
        if boundary is not None:
            suffix = suffix[: boundary.start()]
        field_set = {field for _, field in active_positions}
        if field_set <= {"stem", "branch"}:
            entity_pattern = re.compile(rf"(?:{STEM_ENTITY}|{BRANCH_ENTITY})")
        elif field_set <= {"stem_ten_god", "branch_ten_god"}:
            entity_pattern = re.compile(TEN_GOD_ENTITY)
        elif field_set <= {"stem_element", "branch_element"}:
            entity_pattern = re.compile(r"[목화토금수]")
        elif field_set <= {"stem_yin_yang", "branch_yin_yang"}:
            entity_pattern = re.compile(r"(?:음|양)")
        else:
            previous_end = marker.end()
            continue
        values = entity_pattern.findall(suffix)
        for (pillar, field), value in zip(active_positions, values, strict=False):
            claims.append((pillar, field, value))

        base_fields = {
            re.sub(r"_(?:element|yin_yang)$", "", field)
            for _, field in last_positions
        }
        if len(base_fields) == 1 and base_fields <= {"stem", "branch"}:
            base_field = next(iter(base_fields))
            same_order_detail = re.compile(
                r"(?P<detail>오행|음양)(?:은|는|이|가|:|：)?\s*"
                r"같은\s*순서(?:로)?\s*(?:각각\s*)?"
                r"(?P<values>(?:[목화토금수]|음|양)"
                r"(?:\s*(?:와|과|,|，|·|/)\s*(?:[목화토금수]|음|양))+)",
            )
            for detail_match in same_order_detail.finditer(suffix):
                detail = detail_match.group("detail")
                value_source = r"[목화토금수]" if detail == "오행" else r"(?:음|양)"
                detail_values = re.findall(
                    value_source,
                    detail_match.group("values"),
                )
                if len(detail_values) < len(last_positions):
                    continue
                suffix_name = "element" if detail == "오행" else "yin_yang"
                for (pillar, _), value in zip(
                    last_positions,
                    detail_values,
                    strict=False,
                ):
                    claims.append((pillar, f"{base_field}_{suffix_name}", value))
        previous_end = marker.end()
    return list(dict.fromkeys(claims))


def _paired_position_detail_claims(
    text: str,
) -> tuple[list[tuple[str, str, str, str]], list[tuple[int, int]]]:
    """`천간 A와 지지 B ... X와 Y`의 ordered field claim을 추출한다."""

    pair_anchor = re.compile(
        rf"(?:천간|stem){PILLAR_CLAIM_SEPARATORS}(?P<stem>{STEM_ENTITY})\s*"
        rf"(?:와|과|,|，)\s*(?:지지|branch){PILLAR_CLAIM_SEPARATORS}"
        rf"(?P<branch>{BRANCH_ENTITY})\s*(?:은|는|이|가|:|：)?",
        re.IGNORECASE,
    )
    anchors = list(pair_anchor.finditer(text))
    claims: list[tuple[str, str, str, str]] = []
    detail_spans: list[tuple[int, int]] = []
    for index, anchor_match in enumerate(anchors):
        body_end = anchors[index + 1].start() if index + 1 < len(anchors) else len(text)
        boundary = re.search(
            r"[\n.!?。！？;；]",
            text[anchor_match.end() : body_end],
        )
        if boundary is not None:
            body_end = anchor_match.end() + boundary.start()
        body = text[anchor_match.end() : body_end]
        for detail, value_source in (
            ("element", r"[목화토금수]"),
            ("yin_yang", r"(?:음|양)"),
        ):
            korean_detail = "오행" if detail == "element" else "음양"
            detail_pattern = re.compile(
                rf"(?P<label>{korean_detail})(?:은|는|이|가|:|：)?\s*"
                rf"(?:같은\s*순서(?:로)?\s*)?(?:각각\s*)?"
                rf"(?P<stem_value>{value_source})\s*(?:와|과|,|，|·|/)\s*"
                rf"(?P<branch_value>{value_source})",
            )
            for detail_match in detail_pattern.finditer(body):
                detail_start = anchor_match.end() + detail_match.start()
                detail_end = anchor_match.end() + detail_match.end()
                local_end = min(body_end, detail_end + 32)
                next_field = re.search(
                    r"[,，]?\s*(?:오행|음양)",
                    text[detail_end:local_end],
                )
                if next_field is not None:
                    local_end = detail_end + next_field.start()
                local_claim = text[detail_start:local_end]
                if _explicit_claim_is_negated(
                    local_claim,
                    0,
                    detail_end - detail_start,
                ):
                    continue
                claims.extend(
                    (
                        (
                            "stem",
                            anchor_match.group("stem"),
                            detail,
                            detail_match.group("stem_value"),
                        ),
                        (
                            "branch",
                            anchor_match.group("branch"),
                            detail,
                            detail_match.group("branch_value"),
                        ),
                    )
                )
                detail_spans.append((detail_start, detail_end))
    return list(dict.fromkeys(claims)), list(dict.fromkeys(detail_spans))


def pillar_position_detail_claims(
    text: str, position: str
) -> list[tuple[str, str, str]]:
    """천간·지지 entity에 붙은 오행·음양의 자연어 claim을 추출한다."""

    if position not in {"stem", "branch"}:
        raise ValueError(f"지원하지 않는 pillar position입니다: {position}")
    korean_position = "천간" if position == "stem" else "지지"
    entity_source = STEM_ENTITY if position == "stem" else BRANCH_ENTITY
    anchor = (
        rf"(?:{korean_position}|{position}){PILLAR_CLAIM_SEPARATORS}"
        rf"(?P<entity>{entity_source})"
    )
    paired_claims, paired_detail_spans = _paired_position_detail_claims(text)

    def detail_claim_is_negated(match: re.Match[str]) -> bool:
        """다음 구조 field의 부정 표현이 현재 field로 번지지 않게 제한한다."""

        detail_start = match.start("detail_label")
        value_end = match.end("value")
        local_end = min(len(text), value_end + 48)
        next_field = re.search(
            r"[,，]?\s*(?:오행|음양|천간|지지|stem\b|branch\b)",
            text[value_end:local_end],
            re.IGNORECASE,
        )
        if next_field is not None:
            local_end = value_end + next_field.start()
        local_claim = text[detail_start:local_end]
        return _explicit_claim_is_negated(
            local_claim,
            0,
            value_end - detail_start,
        )

    claims: list[tuple[str, str, str]] = []
    for detail, korean_detail, value_source in (
        ("element", "오행", r"[목화토금수]"),
        ("yin_yang", "음양", r"(?:음|양)"),
    ):
        detail_prefix = (
            anchor
            + rf"(?:(?!(?:천간|지지|\bstem\b|\bbranch\b))"
            rf"[^\n.!?;；]){{0,40}}?"
            rf"(?P<detail_label>{korean_detail}(?:상)?|"
            rf"{position}[_ -]?{detail})"
            rf"{PILLAR_CLAIM_SEPARATORS}"
            rf"(?:(?:보면|읽으면|분류하면|구분하면)\s*)?"
        )
        pattern = re.compile(
            detail_prefix + rf"(?P<value>{value_source})",
            re.IGNORECASE,
        )
        claims.extend(
            (match.group("entity"), detail, match.group("value"))
            for match in pattern.finditer(text)
            if not any(
                start <= match.start("detail_label") < end
                for start, end in paired_detail_spans
            )
            and not detail_claim_is_negated(match)
        )
        correction = re.compile(
            detail_prefix
            + rf"(?P<negated>{value_source})\s*(?:은|는|이|가)?\s*"
            rf"{CORRECTION_CONNECTOR}[^\n.!?。！？;；]{{0,24}}?"
            rf"(?P<corrected>{value_source})",
            re.IGNORECASE,
        )
        claims.extend(
            (match.group("entity"), detail, match.group("corrected"))
            for match in correction.finditer(text)
            if not any(
                start <= match.start("detail_label") < end
                for start, end in paired_detail_spans
            )
        )

    claims.extend(
        (entity, detail, value)
        for claim_position, entity, detail, value in paired_claims
        if claim_position == position
    )

    natural_pair = re.compile(
        anchor
        + r"\s*(?:은|는|이|가|도)?\s*"
        r"(?P<yin_yang>음|양)(?:의\s*)?(?P<element>[목화토금수])",
        re.IGNORECASE,
    )
    compact_patterns = (
        re.compile(
            anchor
            + r"\s*[\(\[]\s*(?P<element>[목화토금수])\s*[·,/\s]+\s*"
            r"(?P<yin_yang>음|양)\s*[\)\]]",
            re.IGNORECASE,
        ),
        re.compile(
            anchor
            + r"\s*[\(\[]\s*(?P<yin_yang>음|양)\s*[·,/\s]+\s*"
            r"(?P<element>[목화토금수])\s*[\)\]]",
            re.IGNORECASE,
        ),
        re.compile(
            anchor
            + r"\s*[\(\[]\s*오행\s*(?P<element>[목화토금수])\s*[,，·/]\s*"
            r"음양\s*(?P<yin_yang>음|양)\s*[\)\]]",
            re.IGNORECASE,
        ),
        re.compile(
            anchor
            + r"\s*[\(\[]\s*음양\s*(?P<yin_yang>음|양)\s*[,，·/]\s*"
            r"오행\s*(?P<element>[목화토금수])\s*[\)\]]",
            re.IGNORECASE,
        ),
    )
    trailing_element = re.compile(
        anchor
        + r"\s*(?:은|는|이|가|도)?\s*"
        + r"(?P<element>[목화토금수])\s*(?:오행|기운)",
        re.IGNORECASE,
    )
    trailing_yin_yang = re.compile(
        anchor
        + r"(?:(?!(?:천간|지지|\bstem\b|\bbranch\b))[^\n.!?,，;；]){0,40}?"
        + r"(?P<yin_yang>음|양)(?:의\s*)?(?:성질|기운)",
        re.IGNORECASE,
    )
    for pattern in (natural_pair, *compact_patterns):
        for match in pattern.finditer(text):
            if _explicit_claim_is_negated(text, match.start(), match.end()):
                continue
            claims.extend(
                (
                    (match.group("entity"), "element", match.group("element")),
                    (match.group("entity"), "yin_yang", match.group("yin_yang")),
                )
            )
    for match in trailing_element.finditer(text):
        if _explicit_claim_is_negated(text, match.start(), match.end()):
            continue
        claims.append((match.group("entity"), "element", match.group("element")))
    for match in trailing_yin_yang.finditer(text):
        if _explicit_claim_is_negated(text, match.start(), match.end()):
            continue
        claims.append((match.group("entity"), "yin_yang", match.group("yin_yang")))
    reverse_natural = re.compile(
        rf"(?:{korean_position}|{position}){PILLAR_CLAIM_SEPARATORS}"
        r"(?P<yin_yang>음|양)(?:의\s*)?(?P<element>[목화토금수])"
        r"(?:\s*기운)?(?:을|를)?\s*(?:가진|띠는|인)?"
        rf"{PILLAR_CLAIM_SEPARATORS}(?P<entity>{entity_source})",
        re.IGNORECASE,
    )
    reverse_element = re.compile(
        rf"(?:{korean_position}|{position}){PILLAR_CLAIM_SEPARATORS}"
        r"(?P<element>[목화토금수])\s*(?:오행|기운)(?:의|을|를)?\s*"
        r"(?:가진|띠는)?\s*"
        rf"{PILLAR_CLAIM_SEPARATORS}(?P<entity>{entity_source})",
        re.IGNORECASE,
    )
    for pattern in (reverse_natural, reverse_element):
        for match in pattern.finditer(text):
            if _explicit_claim_is_negated(text, match.start(), match.end()):
                continue
            claims.append((match.group("entity"), "element", match.group("element")))
            if "yin_yang" in match.groupdict() and match.group("yin_yang") is not None:
                claims.append(
                    (match.group("entity"), "yin_yang", match.group("yin_yang"))
                )
    return list(dict.fromkeys(claims))


def pillar_stem_role_claims(text: str) -> list[str]:
    """`천간이 일간 자리 그 자체` 형태의 최종 십신 literal을 추출한다."""

    prefix = r"(?:천간|stem)\s*(?:은|는|이|가|:|=)?\s*"
    role = rf"(?P<value>{TEN_GOD_ENTITY})\s*자리(?:\s*그\s*자체)?"
    affirmative = (
        r"(?=\s*(?:입니다|이다|이고|이며|이어서|이라고|인\s*셈|"
        r"(?:으)?로\s*(?:표기|표시|쓰이|사용)|[.!?。！？]|$))"
    )
    normal = re.compile(prefix + role + affirmative, re.IGNORECASE)
    claims = [
        match.group("value")
        for match in normal.finditer(text)
        if not _explicit_claim_is_negated(
            text, match.start("value"), match.end()
        )
    ]
    correction = re.compile(
        prefix
        + rf"(?P<negated>{TEN_GOD_ENTITY})\s*자리(?:\s*그\s*자체)?\s*"
        + rf"(?:은|는|이|가)?\s*{CORRECTION_CONNECTOR}"
        + rf"[^\n.!?。！？;；]{{0,24}}?(?P<corrected>{TEN_GOD_ENTITY})"
        + r"\s*자리(?:\s*그\s*자체)?"
        + affirmative,
        re.IGNORECASE,
    )
    claims.extend(match.group("corrected") for match in correction.finditer(text))
    return list(dict.fromkeys(claims))


def surface_element_claims(answer: str) -> list[tuple[str, int]]:
    """명시적인 표면 오행 문맥 또는 둘 이상의 오행 count 목록만 추출한다."""

    matches = list(SURFACE_ELEMENT_COUNT.finditer(answer))
    distinct = {match.group(1) for match in matches}
    claims: list[tuple[str, int]] = []
    for match in matches:
        clause = _claim_clause(answer, match.start(), match.end())
        if len(distinct) >= 2 or re.search(r"(?:표면\s*)?오행|분포", clause):
            claims.append((match.group(1), int(match.group(2))))
    return claims


def _dated_period_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """선택 날짜의 숫자 year/month/day label과 간지의 위치 대응을 검사한다."""

    target_date = _path_value(spec, "period.hard_facts.period.target_date")
    if target_date is None:
        return []
    match = re.fullmatch(r"(20[0-4][0-9])-([01][0-9])-([0-3][0-9])", target_date)
    if match is None:
        return []
    year, month, day = match.groups()
    month_number = str(int(month))
    day_number = str(int(day))
    claims = (
        (
            "year_ganzhi",
            rf"{year}년(?:의)?\s*(?:간지|연간지)",
        ),
        (
            "month_ganzhi",
            rf"(?:{month_number}|{month})월(?:의)?\s*(?:간지|월간지)",
        ),
        (
            "day_ganzhi",
            rf"(?:{day_number}|{day})일(?:의)?\s*(?:간지|일진|일간지)?",
        ),
    )
    errors: list[str] = []
    for field, label_pattern in claims:
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        if expected is None:
            continue
        for entity_match in re.finditer(
            rf"(?:{label_pattern})(?:은|는|이|가|:|=|\s)+"
            rf"[^\n.!?]{{0,12}}?({GANYI.pattern})",
            answer,
        ):
            between = answer[entity_match.start() : entity_match.start(1)]
            if "각각" in between or _explicit_claim_is_negated(
                answer, entity_match.start(), entity_match.end()
            ):
                continue
            if entity_match.group(1) != expected:
                errors.append(f"period_{field}_label_confusion:{entity_match.group(1)}")
    return errors


def _relative_period_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """상대 날짜 표현에 붙은 간지가 period year/month/day와 맞는지 검사한다."""

    labels = (
        ("year_ganzhi", r"(?:올해|이번\s*해|해당\s*연도|그해)"),
        ("month_ganzhi", r"(?:이번\s*달|해당\s*월|그달)"),
        ("day_ganzhi", r"(?:오늘|이날|그날|해당\s*날짜|이\s*날짜)"),
    )
    errors: list[str] = []
    for clause in re.split(r"[\n.!?。！？;；]+", answer):
        if "각각" not in clause:
            continue
        before, after = clause.split("각각", 1)
        ordered_labels: list[tuple[int, str]] = []
        for field, label in labels:
            ordered_labels.extend(
                (match.start(), field) for match in re.finditer(label, before)
            )
        ordered_labels.sort()
        values = GANYI.findall(after)
        if not ordered_labels or len(values) < len(ordered_labels):
            continue
        for (_, field), value in zip(ordered_labels, values, strict=False):
            expected = _path_value(spec, f"period.hard_facts.period.{field}")
            if expected is not None and value != expected:
                errors.append(f"period_{field}_relative_label_confusion:{value}")
    for field, label in labels:
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        if expected is None:
            continue
        patterns = (
            re.compile(
                rf"{label}\s*(?:의\s*)?(?:(?:간지|값)\s*)?"
                rf"(?:은|는|이|가|:|=)?\s*({GANYI.pattern})"
            ),
            re.compile(
                rf"({GANYI.pattern})\s*(?:은|는|이|가)\s*"
                rf"{label}\s*(?:의\s*)?(?:(?:간지|값))?"
            ),
        )
        for pattern in patterns:
            for match in re.finditer(pattern, answer):
                clause = _claim_clause(answer, match.start(), match.end())
                if "각각" in clause or _explicit_claim_is_negated(
                    answer, match.start(), match.end()
                ):
                    continue
                value = match.group(1)
                if value != expected:
                    errors.append(f"period_{field}_relative_label_confusion:{value}")
    return errors


def _pillar_blocks(answer: str) -> list[tuple[str, str]]:
    """기둥명이 명시된 구간만 잘라 위치별 구조 claim을 보수적으로 검사한다."""

    label_pattern = re.compile(r"연주|년주|월주|일주|시주")
    matches = list(label_pattern.finditer(answer))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(answer)
        block = answer[match.end() : end]
        for owner_boundary in re.finditer(
            r"[\n.!?。！？;；]|(?:과|와)\s*달리|일간(?!지)|연간지|월간지|일진|일간지|날의\s*간지",
            block,
        ):
            if owner_boundary.group(0) == "일간" and re.search(
                r"(?:천간(?:의)?\s*십신|stem[_ -]?ten[_ -]?god)"
                r"[^\n.!?。！？;；]{0,16}$",
                block[: owner_boundary.start()],
                re.IGNORECASE,
            ):
                continue
            if owner_boundary.group(0) == "일간" and (
                re.search(
                    r"(?:천간|stem)[^\n.!?。！？;；]{0,40}$",
                    block[: owner_boundary.start()],
                    re.IGNORECASE,
                )
                and re.match(
                    r"[\"'“”‘’]*\s*(?:으로\s*)?"
                    r"(?:표기|표시|쓰이|사용)",
                    block[owner_boundary.end() :],
                )
            ):
                continue
            if owner_boundary.group(0) == "일간" and (
                re.search(
                    r"(?:천간|stem)[^\n.!?。！？;；]{0,48}$",
                    block[: owner_boundary.start()],
                    re.IGNORECASE,
                )
                and re.match(
                    r"\s*자리(?:\s*그\s*자체)?",
                    block[owner_boundary.end() :],
                )
            ):
                continue
            block = block[: owner_boundary.start()]
            break
        # 다음 소유자명이 없더라도 장문의 해석까지 구조 claim으로 오인하지 않는다.
        blocks.append((PILLAR_LABELS[match.group(0)], block[:240]))
    return blocks


def _add_hidden_sequence_errors(
    errors: list[str], pillar: str, expected: Sequence[str], observed: list[str]
) -> None:
    if observed != list(expected):
        errors.append(
            f"natal_{pillar}_hidden_stem_sequence_confusion:" + "·".join(observed)
        )
    for index, entity in enumerate(observed):
        if index >= len(expected) or entity != expected[index]:
            errors.append(f"natal_{pillar}_hidden_stem_confusion:{entity}")


def _positioned_pillar_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    errors: list[str] = []
    separators = PILLAR_CLAIM_SEPARATORS
    field_patterns = {
        "stem": (
            rf"(?:천간|stem)(?:\s*(?:자리|값))?{separators}({STEM_ENTITY})",
            "stem",
            STEM_ENTITY,
        ),
        "branch": (
            rf"(?:지지|branch)(?:\s*(?:자리|값))?{separators}({BRANCH_ENTITY})",
            "branch",
            BRANCH_ENTITY,
        ),
        "stem_element": (
            (
                rf"(?:천간(?:의)?\s*오행|stem[_ -]?element){separators}"
                rf"(?:(?:보면|읽으면|분류하면|구분하면)\s*)?([목화토금수])"
            ),
            "stem_element",
            r"[목화토금수]",
        ),
        "branch_element": (
            (
                rf"(?:지지(?:의)?\s*오행|branch[_ -]?element){separators}"
                rf"(?:(?:보면|읽으면|분류하면|구분하면)\s*)?([목화토금수])"
            ),
            "branch_element",
            r"[목화토금수]",
        ),
        "stem_yin_yang": (
            rf"(?:천간(?:의)?\s*음양|stem[_ -]?yin[_ -]?yang){separators}(음|양)",
            "stem_yin_yang",
            r"(?:음|양)",
        ),
        "branch_yin_yang": (
            rf"(?:지지(?:의)?\s*음양|branch[_ -]?yin[_ -]?yang){separators}(음|양)",
            "branch_yin_yang",
            r"(?:음|양)",
        ),
        "stem_ten_god": (
            (
                rf"(?:천간(?:에)?\s*(?:(?:배정된|해당하는)\s*)?십신|천간(?:의)?\s*십신|"
                rf"천간\s*(?:쪽\s*)?역할|stem[_ -]?ten[_ -]?god)"
                rf"(?:\s*(?:자리(?:에|에는)?|역할))?{separators}({TEN_GOD_ENTITY})"
            ),
            "stem_ten_god",
            TEN_GOD_ENTITY,
        ),
        "branch_ten_god": (
            (
                rf"(?:지지(?:에)?\s*(?:(?:배정된|해당하는)\s*)?십신|지지(?:의)?\s*십신|"
                rf"지지\s*(?:쪽\s*)?역할|branch[_ -]?ten[_ -]?god)"
                rf"(?:\s*(?:자리(?:에|에는)?|역할))?{separators}({TEN_GOD_ENTITY})"
            ),
            "branch_ten_god",
            TEN_GOD_ENTITY,
        ),
    }
    for pillar, block in _pillar_blocks(answer):
        _, paired_detail_spans = _paired_position_detail_claims(block)
        for error_label, (pattern, suffix, entity_source) in field_patterns.items():
            expected = _path_value(spec, f"chart.hard_facts.pillars.{pillar}.{suffix}")
            if expected is None:
                continue
            for match in re.finditer(pattern, block, re.IGNORECASE):
                if "각각" in _claim_clause(
                    block, match.start(), match.end()
                ) or _explicit_claim_is_negated(block, match.start(), match.end()):
                    continue
                if match.group(1) != expected:
                    errors.append(
                        f"natal_{pillar}_{error_label}_confusion:{match.group(1)}"
                    )
            correction = re.compile(
                rf"{pattern}\s*(?:은|는|이|가)?\s*{CORRECTION_CONNECTOR}"
                rf"[^\n.!?。！？]{{0,24}}?(?P<corrected>{entity_source})",
                re.IGNORECASE,
            )
            for match in correction.finditer(block):
                if match.group("corrected") != expected:
                    errors.append(
                        f"natal_{pillar}_{error_label}_confusion:"
                        f"{match.group('corrected')}"
                    )
        expected_stem_role = _path_value(
            spec, f"chart.hard_facts.pillars.{pillar}.stem_ten_god"
        )
        for value in pillar_stem_role_claims(block):
            if expected_stem_role is not None and value != expected_stem_role:
                errors.append(f"natal_{pillar}_stem_ten_god_confusion:{value}")
        for error_label, entity_source, suffix, field_source in (
            ("stem", STEM_ENTITY, "stem", r"(?:천간|stem)"),
            ("branch", BRANCH_ENTITY, "branch", r"(?:지지|branch)"),
        ):
            expected = _path_value(spec, f"chart.hard_facts.pillars.{pillar}.{suffix}")
            if expected is None:
                continue
            placed_in_block = re.compile(
                rf"{field_source}\s*(?:"
                r"(?:(?:쪽)?에)\s*(?:(?:놓인|있는|배치된|자리한)\s*)?|"
                r"(?:으로|로)\s*(?:(?:쓰이는|사용되는|표시되는)\s*)"
                r")?(?:글자|값)?\s*"
                rf"(?:은|는|이|가|:|=)?\s*(?P<value>{entity_source})",
                re.IGNORECASE,
            )
            for match in placed_in_block.finditer(block):
                if _explicit_claim_is_negated(block, match.start(), match.end()):
                    continue
                if match.group("value") != expected:
                    errors.append(
                        f"natal_{pillar}_{error_label}_confusion:"
                        f"{match.group('value')}"
                    )
            reverse_in_block = re.compile(
                rf"(?P<value>{entity_source})\s*(?:은|는|이|가)\s*"
                rf"{field_source}(?:\s*자리)?",
                re.IGNORECASE,
            )
            for match in reverse_in_block.finditer(block):
                if match.group("value") != expected:
                    errors.append(
                        f"natal_{pillar}_{error_label}_confusion:{match.group('value')}"
                    )

        expected_hidden = [
            value
            for path, value in zip(
                spec["allowed_fact_paths"],
                spec["allowed_fact_values"],
                strict=True,
            )
            if path.startswith(f"chart.hard_facts.pillars.{pillar}.hidden_stems[")
        ]
        if expected_hidden:
            hidden_values_source = (
                rf"{STEM_ENTITY}(?:{HIDDEN_STEM_SEPARATOR}{STEM_ENTITY})*"
            )
            hidden_pattern = re.compile(
                rf"(?:지장간(?:\s*(?:목록|구성))?|hidden\s*stems?){separators}"
                rf"(?P<values>{hidden_values_source})",
                re.IGNORECASE,
            )
            for match in hidden_pattern.finditer(block):
                if _explicit_claim_is_negated(block, match.start(), match.end()):
                    continue
                _add_hidden_sequence_errors(
                    errors,
                    pillar,
                    expected_hidden,
                    re.findall(STEM_ENTITY, match.group("values")),
                )

            hidden_correction = re.compile(
                rf"(?:지장간|hidden\s*stems?){separators}"
                rf"(?P<negated>{hidden_values_source})\s*"
                rf"(?:은|는|이|가)?\s*{CORRECTION_CONNECTOR}"
                rf"[^\n.!?。！？]{{0,24}}?"
                rf"(?P<corrected>{hidden_values_source})",
                re.IGNORECASE,
            )
            for match in hidden_correction.finditer(block):
                _add_hidden_sequence_errors(
                    errors,
                    pillar,
                    expected_hidden,
                    re.findall(STEM_ENTITY, match.group("corrected")),
                )

            branch_hidden = re.compile(
                rf"(?P<branch>{BRANCH_ENTITY})\s*(?:속|안)(?:에는|에|은|는)?\s*"
                rf"(?P<values>{hidden_values_source})",
                re.IGNORECASE,
            )
            expected_branch = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.branch"
            )
            for match in branch_hidden.finditer(block):
                if _explicit_claim_is_negated(block, match.start(), match.end()):
                    continue
                if (
                    expected_branch is not None
                    and match.group("branch") != expected_branch
                ):
                    errors.append(
                        f"natal_{pillar}_branch_confusion:{match.group('branch')}"
                    )
                _add_hidden_sequence_errors(
                    errors,
                    pillar,
                    expected_hidden,
                    re.findall(STEM_ENTITY, match.group("values")),
                )

            branch_hidden_correction = re.compile(
                rf"(?P<branch>{BRANCH_ENTITY})\s*(?:속|안)(?:에는|에|은|는)?\s*"
                rf"(?P<negated>{hidden_values_source})\s*"
                rf"(?:은|는|이|가)?\s*{CORRECTION_CONNECTOR}"
                rf"[^\n.!?。！？]{{0,24}}?"
                rf"(?P<corrected>{hidden_values_source})",
                re.IGNORECASE,
            )
            for match in branch_hidden_correction.finditer(block):
                if (
                    expected_branch is not None
                    and match.group("branch") != expected_branch
                ):
                    errors.append(
                        f"natal_{pillar}_branch_confusion:{match.group('branch')}"
                    )
                _add_hidden_sequence_errors(
                    errors,
                    pillar,
                    expected_hidden,
                    re.findall(STEM_ENTITY, match.group("corrected")),
                )

        entity_ten_god = re.compile(
            rf"(?P<entity>{STEM_ENTITY}|{BRANCH_ENTITY})\s*"
            r"(?:에는|에|은|는|이|가|의)\s*"
            rf"(?:십신\s*(?:은|는|이|가|:|=)?\s*)?(?P<ten_god>{TEN_GOD_ENTITY})",
            re.IGNORECASE,
        )
        for match in entity_ten_god.finditer(block):
            if _explicit_claim_is_negated(block, match.start(), match.end()) or (
                match.group("ten_god") == "상관"
                and _ordinary_sanggwan_use(block, match.end("ten_god"))
            ):
                continue
            entity = match.group("entity")
            if re.fullmatch(STEM_ENTITY, entity):
                entity_field = "stem"
                ten_god_field = "stem_ten_god"
            else:
                entity_field = "branch"
                ten_god_field = "branch_ten_god"
            expected_entity = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{entity_field}"
            )
            expected_ten_god = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{ten_god_field}"
            )
            if expected_entity is not None and entity != expected_entity:
                errors.append(f"natal_{pillar}_{entity_field}_confusion:{entity}")
            if (
                expected_ten_god is not None
                and match.group("ten_god") != expected_ten_god
            ):
                errors.append(
                    f"natal_{pillar}_{ten_god_field}_confusion:{match.group('ten_god')}"
                )
        entity_ten_god_correction = re.compile(
            rf"(?P<entity>{STEM_ENTITY}|{BRANCH_ENTITY})\s*"
            r"(?:에는|에|은|는|이|가|의)\s*"
            rf"(?:십신\s*(?:은|는|이|가|:|=)?\s*)?"
            rf"(?P<negated>{TEN_GOD_ENTITY})\s*(?:은|는|이|가)?\s*"
            rf"{CORRECTION_CONNECTOR}[^\n.!?。！？]{{0,24}}?"
            rf"(?P<corrected>{TEN_GOD_ENTITY})",
            re.IGNORECASE,
        )
        for match in entity_ten_god_correction.finditer(block):
            entity = match.group("entity")
            if re.fullmatch(STEM_ENTITY, entity):
                entity_field = "stem"
                ten_god_field = "stem_ten_god"
            else:
                entity_field = "branch"
                ten_god_field = "branch_ten_god"
            expected_entity = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{entity_field}"
            )
            expected_ten_god = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{ten_god_field}"
            )
            if expected_entity is not None and entity != expected_entity:
                errors.append(f"natal_{pillar}_{entity_field}_confusion:{entity}")
            if (
                expected_ten_god is not None
                and match.group("corrected") != expected_ten_god
            ):
                errors.append(
                    f"natal_{pillar}_{ten_god_field}_confusion:"
                    f"{match.group('corrected')}"
                )
        for position in ("stem", "branch"):
            korean_position = "천간" if position == "stem" else "지지"
            compact_position_detail = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                r"(?P<element>[목화토금수])\s*[·,/\s]+\s*"
                r"(?P<yin_yang>음|양)",
                re.IGNORECASE,
            )
            for match in compact_position_detail.finditer(block):
                expected_element = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_element"
                )
                expected_yin_yang = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_yin_yang"
                )
                if (
                    expected_element is not None
                    and match.group("element") != expected_element
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_element_confusion:"
                        f"{match.group('element')}"
                    )
                if (
                    expected_yin_yang is not None
                    and match.group("yin_yang") != expected_yin_yang
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_yin_yang_confusion:"
                        f"{match.group('yin_yang')}"
                    )
        for position, entity_source in (
            ("stem", STEM_ENTITY),
            ("branch", BRANCH_ENTITY),
        ):
            korean_position = "천간" if position == "stem" else "지지"
            expected_entity = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{position}"
            )
            for entity, detail, value in pillar_position_detail_claims(
                block, position
            ):
                expected_detail = _path_value(
                    spec,
                    f"chart.hard_facts.pillars.{pillar}.{position}_{detail}",
                )
                if expected_entity is not None and entity != expected_entity:
                    errors.append(f"natal_{pillar}_{position}_confusion:{entity}")
                if expected_detail is not None and value != expected_detail:
                    errors.append(
                        f"natal_{pillar}_{position}_{detail}_confusion:{value}"
                    )
            natural_pair = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                rf"(?P<entity>{entity_source})\s*(?:은|는|이|가)?\s*"
                r"(?P<yin_yang>음|양)(?:의\s*)?(?P<element>[목화토금수])",
                re.IGNORECASE,
            )
            for match in natural_pair.finditer(block):
                expected_element = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_element"
                )
                expected_yin_yang = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_yin_yang"
                )
                if expected_entity is not None and match.group("entity") != expected_entity:
                    errors.append(
                        f"natal_{pillar}_{position}_confusion:{match.group('entity')}"
                    )
                if (
                    expected_element is not None
                    and match.group("element") != expected_element
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_element_confusion:"
                        f"{match.group('element')}"
                    )
                if (
                    expected_yin_yang is not None
                    and match.group("yin_yang") != expected_yin_yang
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_yin_yang_confusion:"
                        f"{match.group('yin_yang')}"
                    )
            for detail, detail_source, korean_detail in (
                ("element", r"[목화토금수]", "오행"),
                ("yin_yang", r"(?:음|양)", "음양"),
            ):
                expected_detail = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_{detail}"
                )
                detail_pattern = re.compile(
                    rf"(?:{korean_position}|{position}){separators}"
                    rf"(?P<entity>{entity_source})[^\n.!?,，;；]{{0,16}}?"
                    rf"(?P<detail_label>{korean_detail}|"
                    rf"{position}[_ -]?{detail}){separators}"
                    rf"(?P<value>{detail_source})",
                    re.IGNORECASE,
                )
                for match in detail_pattern.finditer(block):
                    if any(
                        start <= match.start("detail_label") < end
                        for start, end in paired_detail_spans
                    ):
                        continue
                    if _explicit_claim_is_negated(block, match.start(), match.end()):
                        continue
                    if (
                        expected_entity is not None
                        and match.group("entity") != expected_entity
                    ):
                        errors.append(
                            f"natal_{pillar}_{position}_confusion:"
                            f"{match.group('entity')}"
                        )
                    if (
                        expected_detail is not None
                        and match.group("value") != expected_detail
                    ):
                        errors.append(
                            f"natal_{pillar}_{position}_{detail}_confusion:"
                            f"{match.group('value')}"
                        )

            compact_detail = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                rf"(?P<entity>{entity_source})\s*[\(\[]\s*"
                r"(?P<element>[목화토금수])\s*[·,/\s]+\s*"
                r"(?P<yin_yang>음|양)\s*[\)\]]",
                re.IGNORECASE,
            )
            for match in compact_detail.finditer(block):
                expected_element = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_element"
                )
                expected_yin_yang = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{position}_yin_yang"
                )
                if (
                    expected_entity is not None
                    and match.group("entity") != expected_entity
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_confusion:{match.group('entity')}"
                    )
                if (
                    expected_element is not None
                    and match.group("element") != expected_element
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_element_confusion:"
                        f"{match.group('element')}"
                    )
                if (
                    expected_yin_yang is not None
                    and match.group("yin_yang") != expected_yin_yang
                ):
                    errors.append(
                        f"natal_{pillar}_{position}_yin_yang_confusion:"
                        f"{match.group('yin_yang')}"
                    )
        for position, entity_pattern, ten_god_field in (
            ("stem", STEM_ENTITY, "stem_ten_god"),
            ("branch", BRANCH_ENTITY, "branch_ten_god"),
        ):
            korean_position = "천간" if position == "stem" else "지지"
            expected_entity = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{position}"
            )
            expected_ten_god = _path_value(
                spec, f"chart.hard_facts.pillars.{pillar}.{ten_god_field}"
            )
            if expected_entity is None or expected_ten_god is None:
                continue
            natural_claim = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                rf"({entity_pattern})[^\n.!?,，;；]{{0,12}}?({TEN_GOD_ENTITY})",
                re.IGNORECASE,
            )
            for claim in natural_claim.finditer(block):
                if "각각" in _claim_clause(
                    block, claim.start(), claim.end()
                ) or _explicit_claim_is_negated(block, claim.start(), claim.end()):
                    continue
                if claim.group(1) != expected_entity:
                    errors.append(
                        f"natal_{pillar}_{position}_confusion:{claim.group(1)}"
                    )
                if claim.group(2) != expected_ten_god:
                    errors.append(
                        f"natal_{pillar}_{ten_god_field}_confusion:{claim.group(2)}"
                    )
    for pillar, field, value in parallel_pillar_field_claims(answer):
        expected = _path_value(spec, f"chart.hard_facts.pillars.{pillar}.{field}")
        if expected is not None and value != expected:
            errors.append(f"natal_{pillar}_{field}_confusion:{value}")
    label_source = _label_pattern(PILLAR_LABELS)
    reverse_fields = (
        ("stem", STEM_ENTITY, r"(?:천간|stem)"),
        ("branch", BRANCH_ENTITY, r"(?:지지|branch)"),
        (
            "stem_ten_god",
            TEN_GOD_ENTITY,
            r"(?:천간(?:의)?\s*십신|stem[_ -]?ten[_ -]?god)",
        ),
        (
            "branch_ten_god",
            TEN_GOD_ENTITY,
            r"(?:지지(?:의)?\s*십신|branch[_ -]?ten[_ -]?god)",
        ),
    )
    for field, entity_source, field_source in reverse_fields:
        pattern = re.compile(
            rf"(?P<value>{entity_source})\s*(?:은|는|이|가)\s*"
            rf"(?P<label>{label_source})(?:의)?\s*{field_source}",
            re.IGNORECASE,
        )
        for match in pattern.finditer(answer):
            if _explicit_claim_is_negated(answer, match.start(), match.end()):
                continue
            pillar = PILLAR_LABELS[match.group("label")]
            expected = _path_value(spec, f"chart.hard_facts.pillars.{pillar}.{field}")
            if expected is not None and match.group("value") != expected:
                errors.append(
                    f"natal_{pillar}_{field}_confusion:{match.group('value')}"
                )
    hidden_reverse = re.compile(
        rf"(?P<values>{STEM_ENTITY}(?:{HIDDEN_STEM_SEPARATOR}{STEM_ENTITY})+)\s*"
        rf"(?:은|는|이|가)\s*(?P<label>{label_source})(?:의)?\s*"
        r"(?:지장간|hidden\s*stems?)",
        re.IGNORECASE,
    )
    for match in hidden_reverse.finditer(answer):
        if _explicit_claim_is_negated(answer, match.start(), match.end()):
            continue
        pillar = PILLAR_LABELS[match.group("label")]
        observed = re.findall(STEM_ENTITY, match.group("values"))
        expected = [
            value
            for path, value in zip(
                spec["allowed_fact_paths"],
                spec["allowed_fact_values"],
                strict=True,
            )
            if path.startswith(f"chart.hard_facts.pillars.{pillar}.hidden_stems[")
        ]
        for index, value in enumerate(observed):
            if index >= len(expected) or value != expected[index]:
                errors.append(f"natal_{pillar}_hidden_stem_confusion:{value}")
    return errors


def _day_master_detail_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    errors: list[str] = []
    expected_stem = _path_value(spec, "chart.hard_facts.day_master.stem")
    expected_element = _path_value(spec, "chart.hard_facts.day_master.element")
    if expected_element is None:
        expected_element = _path_value(spec, "chart.hard_facts.day_master.five_element")
    expected_yin_yang = _path_value(spec, "chart.hard_facts.day_master.yin_yang")
    for sentence in re.split(r"[\n.!?。！？]+", answer):
        if "일간" not in sentence:
            continue
        if expected_element is not None:
            for match in re.finditer(
                r"일간(?:(?!지지|branch)[^\n.!?,，;；]){0,32}?오행"
                r"(?:은|는|이|가|:|=|\s)*([목화토금수])",
                sentence,
            ):
                if match.group(1) != expected_element:
                    errors.append(f"day_master_element_confusion:{match.group(1)}")
        if expected_yin_yang is not None:
            for match in re.finditer(
                r"일간(?:(?!지지|branch)[^\n.!?,，;；]){0,32}?음양"
                r"(?:은|는|이|가|:|=|\s)*"
                r"(음|양)(?:의|인|이고|이며|입니다|이다|\s|$)",
                sentence,
            ):
                if match.group(1) != expected_yin_yang:
                    errors.append(f"day_master_yin_yang_confusion:{match.group(1)}")
    stop = re.compile(
        r"(?:연주|년주|월주|일주|시주|일지|지지|branch|연간지|월간지|일진)",
        re.IGNORECASE,
    )
    day_master_source = r"[甲乙丙丁戊己庚辛壬癸]"
    anchored = (
        re.compile(
            rf"일간(?!지)[^\n.!?。！？]{{0,12}}?(?P<stem>{day_master_source})"
            r"(?P<tail>[^,，;；\n.!?。！？]{0,24})"
        ),
        re.compile(
            rf"(?P<stem>{day_master_source})\s*(?:은|는|이|가)"
            r"[^\n.!?。！？]{0,16}?일간(?!지)"
            r"(?P<tail>[^,，;；\n.!?。！？]{0,24})"
        ),
    )
    for pattern in anchored:
        for match in pattern.finditer(answer):
            tail = stop.split(match.group("tail"), maxsplit=1)[0]
            element_match = re.search(
                r"([목화토금수])(?:의\s*기운|이고|이며|입니다|이다|으로|,|·|/|\s|$)",
                tail,
            )
            yin_yang_match = re.search(
                r"(음|양)(?:간)?(?:의|이고|이며|입니다|이다|으로|,|·|/|\s|$)",
                tail,
            )
            if (
                element_match is not None
                and expected_element is not None
                and element_match.group(1) != expected_element
            ):
                errors.append(f"day_master_element_confusion:{element_match.group(1)}")
            if (
                yin_yang_match is not None
                and expected_yin_yang is not None
                and yin_yang_match.group(1) != expected_yin_yang
            ):
                errors.append(
                    f"day_master_yin_yang_confusion:{yin_yang_match.group(1)}"
                )

    hanja_elements = {"木": "목", "火": "화", "土": "토", "金": "금", "水": "수"}

    def add_day_master_detail_errors(
        *, stem: str | None, element: str | None, yin_yang: str | None
    ) -> None:
        if stem is not None and expected_stem is not None and stem != expected_stem:
            errors.append(f"day_master_confusion:{stem}")
        if (
            element is not None
            and expected_element is not None
            and element != expected_element
        ):
            errors.append(f"day_master_element_confusion:{element}")
        if (
            yin_yang is not None
            and expected_yin_yang is not None
            and yin_yang != expected_yin_yang
        ):
            errors.append(f"day_master_yin_yang_confusion:{yin_yang}")

    for sentence in re.split(r"[\n.!?。！？]+", answer):
        direct_stem = re.compile(
            rf"일간(?!지)\s*(?:의\s*)?(?:천간\s*)?"
            rf"(?:은|는|이|가|:|=)?\s*(?P<stem>{STEM_ENTITY})"
            r"(?P<tail>[^,，;；\n.!?。！？]{0,28})"
        )
        for match in direct_stem.finditer(sentence):
            if _explicit_claim_is_negated(sentence, match.start(), match.start("tail")):
                continue
            tail = match.group("tail")
            pair = re.search(
                r"(?P<yin_yang>음|양)(?:의|\s)*(?P<element>[목화토금수])",
                tail,
            )
            yin_yang = (
                pair.group("yin_yang")
                if pair is not None
                else (
                    yin_match.group("yin_yang")
                    if (
                        yin_match := re.search(
                            r"(?P<yin_yang>음|양)(?:의|\s)*(?:성질|기운)", tail
                        )
                    )
                    else None
                )
            )
            add_day_master_detail_errors(
                stem=match.group("stem"),
                element=pair.group("element") if pair is not None else None,
                yin_yang=yin_yang,
            )

        leading_pair = re.compile(
            r"일간(?!지)\s*(?:은|는|이|가|:|=)?\s*"
            r"(?P<yin_yang>음|양)(?:의|\s)*(?P<element>[목화토금수])"
            rf"(?:\s*기운)?(?:인|이고|이며)?\s*(?P<stem>{STEM_ENTITY})"
        )
        for match in leading_pair.finditer(sentence):
            add_day_master_detail_errors(
                stem=match.group("stem"),
                element=match.group("element"),
                yin_yang=match.group("yin_yang"),
            )

        reverse_detail = re.compile(
            rf"(?P<stem>{STEM_ENTITY})(?P<element_hanja>[木火土金水])?\s*"
            r"(?:은|는|이|가)[^,，;；\n.!?。！？]{0,32}?"
            r"(?P<yin_yang>음|양)(?:의|\s+)"
            r"[^,，;；\n.!?。！？]{0,24}?일간(?!지)"
        )
        for match in reverse_detail.finditer(sentence):
            add_day_master_detail_errors(
                stem=match.group("stem"),
                element=hanja_elements.get(match.group("element_hanja")),
                yin_yang=match.group("yin_yang"),
            )
        reverse_korean_detail = re.compile(
            rf"(?P<stem>{STEM_ENTITY})\s*(?:은|는|이|가)\s*"
            r"(?P<element>[목화토금수])\s*(?:오행|기운)"
            r"(?:의|이고|이며|,|\s)*"
            r"(?P<yin_yang>음|양)(?:의\s*)?\s*(?:기운)?(?:인|이고|이며)?\s*"
            r"일간(?!지)"
        )
        for match in reverse_korean_detail.finditer(sentence):
            if _explicit_claim_is_negated(sentence, match.start(), match.end()):
                continue
            add_day_master_detail_errors(
                stem=match.group("stem"),
                element=match.group("element"),
                yin_yang=match.group("yin_yang"),
            )
    return errors


def _surface_element_count_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    errors: list[str] = []
    for element, count in surface_element_claims(answer):
        expected = _path_value(
            spec, f"chart.hard_facts.surface_five_elements.{element}"
        )
        if expected is not None and count != int(expected):
            errors.append(f"surface_five_elements_{element}_confusion:{count}")
    return errors


def _path_scalar_claims(
    answer: str, label_source: str, entity_source: str
) -> list[tuple[str, str]]:
    """dot-path의 `field=value`, `field(value)`, `value는 field` claim을 읽는다."""

    claims: list[tuple[str, str]] = []
    quote = r"[`\"'“”‘’]?"
    forward = re.compile(
        rf"{quote}(?P<label>{label_source}){quote}\s*"
        rf"(?:=|:|：|은|는|이|가|인|\(|\[)\s*{quote}"
        rf"(?P<value>{entity_source}){quote}",
        re.IGNORECASE,
    )
    reverse = re.compile(
        rf"{quote}(?P<value>{entity_source}){quote}\s*"
        rf"(?:=|:|：|은|는|이|가)\s*{quote}(?P<label>{label_source}){quote}",
        re.IGNORECASE,
    )
    for pattern in (forward, reverse):
        claims.extend(
            (match.group("label"), match.group("value"))
            for match in pattern.finditer(answer)
            if not _explicit_claim_is_negated(answer, match.start(), match.end())
        )
    correction = re.compile(
        rf"{quote}(?P<label>{label_source}){quote}\s*"
        rf"(?:=|:|：|은|는|이|가|인|\(|\[)\s*{quote}"
        rf"(?P<negated>{entity_source}){quote}\s*(?:은|는|이|가)?\s*"
        rf"아니(?:라|고)\s*{quote}(?P<value>{entity_source}){quote}",
        re.IGNORECASE,
    )
    claims.extend(
        (match.group("label"), match.group("value"))
        for match in correction.finditer(answer)
    )
    return claims


def _nested_json_objects(answer: str, key: str) -> list[dict[str, Any]]:
    """답변 속 `key: {...}`의 균형 잡힌 JSON object만 안전하게 읽는다."""

    quote = r"[`\"'“”‘’]?"
    opener = re.compile(
        rf"{quote}{re.escape(key)}{quote}\s*(?:=|:|：)\s*\{{",
        re.IGNORECASE,
    )
    objects: list[dict[str, Any]] = []
    for match in opener.finditer(answer):
        start = match.end() - 1
        depth = 0
        string_quote: str | None = None
        escaped = False
        for index in range(start, len(answer)):
            character = answer[index]
            if string_quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == string_quote:
                    string_quote = None
                continue
            if character in {'"', "'"}:
                string_quote = character
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(answer[start : index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        objects.append(value)
                    break
    return objects


def _schema_path_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """한국어 설명뿐 아니라 model이 직접 출력한 runtime dot-path도 검증한다."""

    errors: list[str] = []
    pillar_prefix = r"(?:natal\.pillars|chart(?:\.hard_facts)?\.pillars|pillars)\."
    for field, entity_source in (
        ("ganzhi", GANYI.pattern),
        ("stem", STEM_ENTITY),
        ("branch", BRANCH_ENTITY),
        ("stem_element", r"[목화토금수]"),
        ("branch_element", r"[목화토금수]"),
        ("stem_yin_yang", r"(?:음|양)"),
        ("branch_yin_yang", r"(?:음|양)"),
        ("stem_ten_god", TEN_GOD_ENTITY),
        ("branch_ten_god", TEN_GOD_ENTITY),
    ):
        label_source = rf"{pillar_prefix}(?P<pillar>year|month|day|hour)\.{field}"
        for label, value in _path_scalar_claims(answer, label_source, entity_source):
            pillar_match = re.search(r"\.(year|month|day|hour)\.", label)
            if pillar_match is None:
                continue
            pillar = pillar_match.group(1).lower()
            expected = _path_value(spec, f"chart.hard_facts.pillars.{pillar}.{field}")
            if expected is not None and value != expected:
                errors.append(f"natal_{pillar}_{field}_confusion:{value}")

    hidden_source = rf"{pillar_prefix}(?P<pillar>year|month|day|hour)\.hidden_stems"
    quote = r"[`\"'“”‘’]?"
    hidden_pattern = re.compile(
        rf"{quote}(?P<label>{hidden_source}){quote}\s*"
        r"(?:=|:|：|은|는|이|가|인)?\s*[\[(]\s*"
        rf"(?P<values>[甲乙丙丁戊己庚辛壬癸\s,，·/`'\"“”‘’]+)\s*[\])]",
        re.IGNORECASE,
    )
    for match in hidden_pattern.finditer(answer):
        pillar = match.group("pillar").lower()
        observed = re.findall(STEM_ENTITY, match.group("values"))
        expected = []
        index = 0
        while True:
            value = _path_value(
                spec,
                f"chart.hard_facts.pillars.{pillar}.hidden_stems[{index}]",
            )
            if value is None:
                break
            expected.append(value)
            index += 1
        if expected and observed != expected:
            for position, value in enumerate(observed):
                if position >= len(expected) or value != expected[position]:
                    errors.append(f"natal_{pillar}_hidden_stem_confusion:{value}")

    for pillars in _nested_json_objects(answer, "pillars"):
        for pillar in ("year", "month", "day", "hour"):
            claims = pillars.get(pillar)
            if not isinstance(claims, Mapping):
                continue
            for field in (
                "ganzhi",
                "stem",
                "branch",
                "stem_element",
                "branch_element",
                "stem_yin_yang",
                "branch_yin_yang",
                "stem_ten_god",
                "branch_ten_god",
            ):
                value = claims.get(field)
                expected = _path_value(
                    spec, f"chart.hard_facts.pillars.{pillar}.{field}"
                )
                if (
                    isinstance(value, str)
                    and expected is not None
                    and value != expected
                ):
                    errors.append(f"natal_{pillar}_{field}_confusion:{value}")
            hidden_stems = claims.get("hidden_stems")
            if isinstance(hidden_stems, list) and all(
                isinstance(value, str) for value in hidden_stems
            ):
                expected_hidden = [
                    value
                    for path, value in zip(
                        spec["allowed_fact_paths"],
                        spec["allowed_fact_values"],
                        strict=True,
                    )
                    if path.startswith(
                        f"chart.hard_facts.pillars.{pillar}.hidden_stems["
                    )
                ]
                _add_hidden_sequence_errors(
                    errors, pillar, expected_hidden, hidden_stems
                )

    period_prefix = r"(?:period(?:\.hard_facts(?:\.period)?)?\.)?"
    for field in ("year_ganzhi", "month_ganzhi", "day_ganzhi"):
        label_source = rf"{period_prefix}{field}"
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        for _, value in _path_scalar_claims(answer, label_source, GANYI.pattern):
            if expected is not None and value != expected:
                errors.append(f"period_{field}_label_confusion:{value}")

    day_master_prefix = (
        r"(?:(?:natal\.)?day_master|chart(?:\.hard_facts)?\.day_master)\."
    )
    day_master_fields = (
        ("stem", STEM_ENTITY, "stem", "day_master_confusion"),
        ("element", r"[목화토금수]", "element", "day_master_element_confusion"),
        (
            "five_element",
            r"[목화토금수]",
            "element",
            "day_master_element_confusion",
        ),
        ("yin_yang", r"(?:음|양)", "yin_yang", "day_master_yin_yang_confusion"),
    )
    for label_field, entity, fact_field, error_prefix in day_master_fields:
        expected = _path_value(spec, f"chart.hard_facts.day_master.{fact_field}")
        if expected is None and fact_field == "element":
            expected = _path_value(spec, "chart.hard_facts.day_master.five_element")
        for _, value in _path_scalar_claims(
            answer, rf"{day_master_prefix}{label_field}", entity
        ):
            if expected is not None and value != expected:
                errors.append(f"{error_prefix}:{value}")

    nested_day_master = re.compile(
        rf"{quote}(?:day_master|일간){quote}\s*(?:=|:|：)\s*"
        r"\{(?P<body>[^{}]{1,320})\}",
        re.IGNORECASE,
    )
    for block in nested_day_master.finditer(answer):
        body = block.group("body")
        for label_field, entity, fact_field, error_prefix in day_master_fields:
            expected = _path_value(spec, f"chart.hard_facts.day_master.{fact_field}")
            if expected is None and fact_field == "element":
                expected = _path_value(spec, "chart.hard_facts.day_master.five_element")
            nested_field = re.compile(
                rf"{quote}{label_field}{quote}\s*(?:=|:|：)\s*"
                rf"{quote}(?P<value>{entity}){quote}",
                re.IGNORECASE,
            )
            for match in nested_field.finditer(body):
                value = match.group("value")
                if expected is not None and value != expected:
                    errors.append(f"{error_prefix}:{value}")

    surface_source = (
        r"(?:chart(?:\.hard_facts)?\.)?surface_five_elements\."
        r"(?P<element>[목화토금수])"
    )
    for label, value in _path_scalar_claims(answer, surface_source, r"[0-9]+"):
        element_match = re.search(r"([목화토금수])$", label)
        if element_match is None:
            continue
        element = element_match.group(1)
        expected = _path_value(
            spec, f"chart.hard_facts.surface_five_elements.{element}"
        )
        if expected is not None and int(value) != int(expected):
            errors.append(f"surface_five_elements_{element}_confusion:{value}")

    nested_surface = re.compile(
        rf"{quote}surface_five_elements{quote}\s*(?:=|:|：)\s*"
        r"\{(?P<body>[^{}]{1,320})\}",
        re.IGNORECASE,
    )
    for block in nested_surface.finditer(answer):
        body = block.group("body")
        field = re.compile(
            rf"{quote}(?P<element>[목화토금수]){quote}\s*(?:=|:|：)\s*"
            rf"{quote}(?P<value>[0-9]+){quote}"
        )
        for match in field.finditer(body):
            element = match.group("element")
            value = match.group("value")
            expected = _path_value(
                spec, f"chart.hard_facts.surface_five_elements.{element}"
            )
            if expected is not None and int(value) != int(expected):
                errors.append(f"surface_five_elements_{element}_confusion:{value}")
    return errors


def structural_claim_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """일반 해석이 아니라 명시적인 schema label·entity claim만 강하게 검사한다."""

    errors: list[str] = []
    allowed = set(spec["allowed_fact_values"])
    for ganzhi in GANYI.findall(answer):
        if ganzhi not in allowed:
            errors.append(f"unprovided_ganzhi:{ganzhi}")
    for target_date in normalized_dates(answer):
        if target_date not in allowed:
            errors.append(f"unprovided_date:{target_date}")
    errors.extend(_contextual_partial_date_errors(spec, answer))
    errors.extend(_parallel_ganzhi_label_errors(spec, answer))
    errors.extend(_dated_period_claim_errors(spec, answer))
    errors.extend(_relative_period_claim_errors(spec, answer))
    errors.extend(_schema_path_claim_errors(spec, answer))
    for label, value, _, _ in _labeled_entity_claims(
        answer, PILLAR_LABELS, GANYI.pattern
    ):
        field = PILLAR_LABELS[label]
        expected = _path_value(spec, f"chart.hard_facts.pillars.{field}.ganzhi")
        if expected is not None and value != expected:
            errors.append(f"natal_{field}_label_confusion:{value}")
    for label, value in _corrected_owner_claims(
        answer, PILLAR_LABELS, GANYI.pattern
    ):
        field = PILLAR_LABELS[label]
        expected = _path_value(spec, f"chart.hard_facts.pillars.{field}.ganzhi")
        if expected is not None and value != expected:
            errors.append(f"natal_{field}_label_confusion:{value}")
    period_claims = _labeled_entity_claims(answer, PERIOD_LABELS, GANYI.pattern)
    for label, value, _, _ in period_claims:
        field = PERIOD_LABELS[label]
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        if expected is not None and value != expected:
            errors.append(f"period_{field}_label_confusion:{value}")
    for label, value in _corrected_owner_claims(
        answer, PERIOD_LABELS, GANYI.pattern
    ):
        field = PERIOD_LABELS[label]
        expected = _path_value(spec, f"period.hard_facts.period.{field}")
        if expected is not None and value != expected:
            errors.append(f"period_{field}_label_confusion:{value}")
    natal_day = _path_value(spec, "chart.hard_facts.pillars.day.ganzhi")
    natal_pillars = {
        value
        for field in ("year", "month", "day", "hour")
        if (
            value := _path_value(
                spec, f"chart.hard_facts.pillars.{field}.ganzhi"
            )
        )
        is not None
    }
    singleton_chart_patterns = (
        re.compile(
            rf"(?:연결된\s*승인\s*)?(?:사주\s*)?원국(?:\s*전체)?"
            r"(?:\s*(?:의\s*)?간지|\s*사실)?\s*"
            r"(?:은|는|이|가|에|에는|:|=)?\s*"
            r"(?:하나(?:의)?\s*기둥(?:인|으로)?\s*)?"
            rf"(?P<value>{GANYI.pattern})\s*"
            r"(?:하나(?:만)?|한\s*기둥(?:만)?|만|뿐)?\s*"
            r"(?:이다|입니다|이라고|이고|이며|을\s*뜻(?:합니다|한다)|"
            r"(?:으)?로\s*(?:이루어집니다|이루어진다|구성됩니다|구성된다|되어\s*있습니다)|"
            r"(?:이|가)?\s*있(?:습니다|다|어요|음))"
        ),
        re.compile(
            rf"(?P<value>{GANYI.pattern})\s*(?:하나만|한\s*기둥만)?\s*"
            r"(?:은|는|이|가)?\s*(?:바로\s*)?원국(?:\s*전체)?"
            r"(?:\s*(?:이다|입니다|이라고))?"
        ),
        re.compile(
            r"(?:사주\s*)?원국을\s*(?:한마디로\s*(?:하면|말하면)|"
            r"이루는\s*간지(?:는|가)?)\s*"
            rf"(?P<value>{GANYI.pattern})\s*(?:하나|만|뿐)?\s*"
            r"(?:이다|입니다|이라고|이고|이며|(?:으)?로\s*구성됩니다)"
        ),
    )
    singleton_matches = [
        match
        for pattern in singleton_chart_patterns
        for match in pattern.finditer(answer)
        if not _explicit_claim_is_negated(answer, match.start(), match.end())
    ]
    for match in singleton_matches:
        value = match.group("value")
        if value not in natal_pillars:
            continue
        if value == natal_day:
            errors.append("natal_day_called_full_chart")
        else:
            errors.append(f"natal_single_pillar_called_full_chart:{value}")
    period_year = _path_value(spec, "period.hard_facts.period.year_ganzhi")
    period_day = _path_value(spec, "period.hard_facts.period.day_ganzhi")
    if period_year and period_year != period_day and any(
        PERIOD_LABELS[label] == "day_ganzhi" and value == period_year
        for label, value, _, _ in period_claims
    ):
        errors.append("period_year_called_day_ganzhi")
    target_date = _path_value(spec, "period.hard_facts.period.target_date")
    if period_year and period_year != period_day:
        generic_day_patterns = [
            rf"(?:승인된\s*)?날짜\s*사실\s*(?:은|는|이|가|:|=)?\s*{period_year}",
            rf"(?:오늘|해당\s*날짜)(?:의)?\s*간지\s*(?:은|는|이|가|:|=)?\s*{period_year}",
            rf"오늘(?:은|는|이|가|:|=)\s*{period_year}(?:이다|입니다|이라고|\s|[,.:;!?]|$)",
        ]
        if target_date is not None:
            generic_day_patterns.append(
                rf"{re.escape(target_date)}(?:의)?\s*간지\s*"
                rf"(?:은|는|이|가|:|=)?\s*{period_year}"
            )
        if any(
            not _explicit_claim_is_negated(answer, match.start(), match.end())
            for pattern in generic_day_patterns
            for match in re.finditer(pattern, answer)
        ):
            errors.append("period_year_called_day_ganzhi")
    if period_day and any(
        not _explicit_claim_is_negated(answer, match.start(), match.end())
        for match in re.finditer(
            rf"{period_day}(?:은|는|이|가|\s)*[^\n.!?]{{0,8}}세운"
            rf"(?:이다|입니다|이라고)|세운(?:은|는|이|가|:|=|\s)+"
            rf"[^\n.!?]{{0,8}}{period_day}",
            answer,
        )
    ):
        errors.append("period_day_called_seun")
    period_values = {
        value
        for value in (
            period_year,
            _path_value(spec, "period.hard_facts.period.month_ganzhi"),
            period_day,
        )
        if value is not None
    }
    for match in re.finditer(
        rf"(?:오늘(?:\s*날짜)?|이날|그날|해당\s*날짜|이\s*날짜)(?:의|에|에는|은|는|\s)*"
        rf"원국[^\n.!?]{{0,18}}?({GANYI.pattern})",
        answer,
    ):
        if match.group(1) in period_values and not _explicit_claim_is_negated(
            answer, match.start(), match.end()
        ):
            errors.append(f"period_ganzhi_called_natal_chart:{match.group(1)}")
    day_master = _path_value(spec, "chart.hard_facts.day_master.stem")
    if day_master:
        for _, value, _, _ in _labeled_entity_claims(
            answer, {"일간": "stem"}, STEM_ENTITY, maximum_gap=16
        ):
            if value != day_master:
                errors.append(f"day_master_confusion:{value}")
    errors.extend(_positioned_pillar_claim_errors(spec, answer))
    errors.extend(_day_master_detail_errors(spec, answer))
    errors.extend(_surface_element_count_errors(spec, answer))
    for match in re.finditer(
        r"(?:천간|지지|지장간|hidden\s*stems?)[^\n.!?]{0,36}",
        answer,
        re.IGNORECASE,
    ):
        entities = re.findall(
            r"[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]", match.group(0)
        )
        for entity in entities:
            if entity not in allowed:
                errors.append(f"unprovided_stem_or_branch:{entity}")
    mentioned_ten_gods = _mentioned_ten_gods(answer)
    missing_ten_gods = mentioned_ten_gods - allowed
    errors.extend(f"unprovided_ten_god:{value}" for value in sorted(missing_ten_gods))
    for label, pattern in UNSUPPORTED_STRUCTURAL_PATTERNS:
        for match in pattern.finditer(answer):
            if not _unsupported_claim_is_negated(answer, match.start(), match.end()):
                errors.append(f"unsupported_structural_claim:{label}")
                break
    return list(dict.fromkeys(errors))


def _pillar_field_claim_coverage(answer: str) -> set[tuple[str, str, str]]:
    """schema 질문의 pillar/field/value가 실제 위치 claim으로 쓰였는지 센다."""

    coverage = set(parallel_pillar_field_claims(answer))
    separators = PILLAR_CLAIM_SEPARATORS
    field_specs = (
        (
            "stem_ten_god",
            (
                r"(?:천간(?:에)?\s*(?:(?:배정된|해당하는)\s*)?십신|천간(?:의)?\s*십신|"
                r"천간\s*(?:쪽\s*)?역할|stem[_ -]?ten[_ -]?god)"
                r"(?:\s*(?:자리(?:에|에는)?|역할))?"
            ),
            TEN_GOD_ENTITY,
        ),
        (
            "branch_ten_god",
            (
                r"(?:지지(?:에)?\s*(?:(?:배정된|해당하는)\s*)?십신|지지(?:의)?\s*십신|"
                r"지지\s*(?:쪽\s*)?역할|branch[_ -]?ten[_ -]?god)"
                r"(?:\s*(?:자리(?:에|에는)?|역할))?"
            ),
            TEN_GOD_ENTITY,
        ),
        (
            "stem_element",
            r"(?:천간(?:의)?\s*오행|stem[_ -]?element)",
            r"[목화토금수]",
        ),
        (
            "branch_element",
            r"(?:지지(?:의)?\s*오행|branch[_ -]?element)",
            r"[목화토금수]",
        ),
        (
            "stem_yin_yang",
            r"(?:천간(?:의)?\s*음양|stem[_ -]?yin[_ -]?yang)",
            r"(?:음|양)",
        ),
        (
            "branch_yin_yang",
            r"(?:지지(?:의)?\s*음양|branch[_ -]?yin[_ -]?yang)",
            r"(?:음|양)",
        ),
        ("stem", r"(?:천간|\bstem\b)(?:\s*(?:자리|값))?", STEM_ENTITY),
        ("branch", r"(?:지지|\bbranch\b)(?:\s*(?:자리|값))?", BRANCH_ENTITY),
    )
    for pillar, block in _pillar_blocks(answer):
        for field, label_source, entity_source in field_specs:
            pattern = re.compile(
                rf"{label_source}{separators}(?P<value>{entity_source})",
                re.IGNORECASE,
            )
            coverage.update(
                (pillar, field, match.group("value"))
                for match in pattern.finditer(block)
                if not _explicit_claim_is_negated(block, match.start(), match.end())
            )
        literal_stem_ten_god = re.compile(
            r"(?:천간|stem)[^\n.!?。！？;；]{0,40}?"
            rf"[\"'“”‘’]?(?P<value>{TEN_GOD_ENTITY})[\"'“”‘’]?\s*"
            r"(?:으로\s*)?(?:표기|표시|쓰이|사용)",
            re.IGNORECASE,
        )
        coverage.update(
            (pillar, "stem_ten_god", match.group("value"))
            for match in literal_stem_ten_god.finditer(block)
            if not _explicit_claim_is_negated(block, match.start(), match.end())
        )
        reference_literal_stem_ten_god = re.compile(
            r"(?:천간|stem)[^\n.!?。！？;；]{0,40}?기준(?:이|으로)\s*되는\s*"
            rf"[\"'“”‘’]?(?P<value>{TEN_GOD_ENTITY})[\"'“”‘’]?\s*"
            r"(?:으로\s*)?(?:표기|표시|쓰이|사용)",
            re.IGNORECASE,
        )
        coverage.update(
            (pillar, "stem_ten_god", match.group("value"))
            for match in reference_literal_stem_ten_god.finditer(block)
            if not _explicit_claim_is_negated(
                block, match.start("value"), match.end()
            )
            and re.match(
                r"\s*(?:되지|하지|하면\s*안|해서는\s*안|되어서는\s*안|"
                r"할\s*수\s*없)",
                block[match.end() : match.end() + 24],
            )
            is None
        )
        coverage.update(
            (pillar, "stem_ten_god", value)
            for value in pillar_stem_role_claims(block)
        )
        for position, entity_source in (
            ("stem", STEM_ENTITY),
            ("branch", BRANCH_ENTITY),
        ):
            korean_position = "천간" if position == "stem" else "지지"
            positioned = re.compile(
                rf"(?:{korean_position}|{position})\s*(?:"
                r"(?:(?:쪽)?에)\s*(?:(?:놓인|있는|배치된|자리한)\s*)?|"
                r"(?:으로|로)\s*(?:(?:쓰이는|사용되는|표시되는)\s*)"
                r")?(?:글자|값)?\s*(?:은|는|이|가|:|=)?\s*"
                rf"(?P<entity>{entity_source})",
                re.IGNORECASE,
            )
            coverage.update(
                (pillar, position, match.group("entity"))
                for match in positioned.finditer(block)
                if not _explicit_claim_is_negated(
                    block, match.start(), match.end()
                )
            )
            natural_pair = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                rf"(?P<entity>{entity_source})\s*(?:은|는|이|가)?\s*"
                r"(?P<yin_yang>음|양)(?:의\s*)?(?P<element>[목화토금수])",
                re.IGNORECASE,
            )
            for match in natural_pair.finditer(block):
                coverage.update(
                    {
                        (pillar, position, match.group("entity")),
                        (pillar, f"{position}_element", match.group("element")),
                        (pillar, f"{position}_yin_yang", match.group("yin_yang")),
                    }
                )
            compact = re.compile(
                rf"(?:{korean_position}|{position}){separators}"
                rf"(?P<entity>{entity_source})\s*[\(\[]\s*"
                r"(?P<element>[목화토금수])\s*[·,/\s]+\s*"
                r"(?P<yin_yang>음|양)\s*[\)\]]",
                re.IGNORECASE,
            )
            for match in compact.finditer(block):
                coverage.update(
                    {
                        (pillar, position, match.group("entity")),
                        (pillar, f"{position}_element", match.group("element")),
                        (pillar, f"{position}_yin_yang", match.group("yin_yang")),
                    }
                )
            for entity, detail, value in pillar_position_detail_claims(
                block, position
            ):
                coverage.update(
                    {
                        (pillar, position, entity),
                        (pillar, f"{position}_{detail}", value),
                    }
                )
    for pillars in _nested_json_objects(answer, "pillars"):
        for pillar in ("year", "month", "day", "hour"):
            values = pillars.get(pillar)
            if not isinstance(values, Mapping):
                continue
            coverage.update(
                (pillar, field, value)
                for field, value in values.items()
                if field
                in {
                    "ganzhi",
                    "stem",
                    "branch",
                    "stem_element",
                    "branch_element",
                    "stem_yin_yang",
                    "branch_yin_yang",
                    "stem_ten_god",
                    "branch_ten_god",
                }
                and isinstance(value, str)
            )
    return coverage


def required_fact_errors(spec: Mapping[str, Any], answer: str) -> list[str]:
    """질문 축이 요구하는 최소 natal·period evidence 누락만 검사한다."""

    errors: list[str] = []
    natal = [
        _path_value(spec, f"chart.hard_facts.pillars.{field}.ganzhi")
        for field in ("year", "month", "day", "hour")
    ]
    natal = [value for value in natal if value is not None]
    axis = spec["task_axis"]
    question = str(spec["prompt"][-1]["content"])

    def require_suffixes(suffixes: Sequence[str]) -> None:
        expected = [(suffix, _path_value(spec, suffix)) for suffix in suffixes]
        values = [value for _, value in expected if value is not None]
        required_counts = Counter(values)
        observed_counts = Counter(
            {value: answer.count(value) for value in required_counts}
        )
        for suffix, value in expected:
            if value is None:
                continue
            if observed_counts[value] > 0:
                observed_counts[value] -= 1
            else:
                errors.append(f"required_schema_fact_omitted:{suffix}")

    def require_positioned_pillar_fields(fields: Sequence[str]) -> None:
        coverage = _pillar_field_claim_coverage(answer)
        for pillar in ("year", "month", "day", "hour"):
            for field in fields:
                suffix = f"chart.hard_facts.pillars.{pillar}.{field}"
                value = _path_value(spec, suffix)
                if value is not None and (pillar, field, value) not in coverage:
                    errors.append(f"required_schema_fact_omitted:{suffix}")

    if axis == "structured_fact_schema_literacy":
        pillar_ganzhi = [
            f"chart.hard_facts.pillars.{pillar}.ganzhi"
            for pillar in ("year", "month", "day", "hour")
        ]
        period_ganzhi = [
            f"period.hard_facts.period.{field}"
            for field in ("year_ganzhi", "month_ganzhi", "day_ganzhi")
        ]
        if "원국 전체 네 기둥과 일주" in question or "연주·월주·일주·시주" in question:
            require_suffixes(pillar_ganzhi)
        elif "일간과 그 오행·음양" in question:
            stem = _path_value(spec, "chart.hard_facts.day_master.stem")
            element = _path_value(spec, "chart.hard_facts.day_master.element")
            if element is None:
                element = _path_value(spec, "chart.hard_facts.day_master.five_element")
            yin_yang = _path_value(spec, "chart.hard_facts.day_master.yin_yang")
            if stem is not None and stem not in answer:
                errors.append(
                    "required_schema_fact_omitted:chart.hard_facts.day_master.stem"
                )
            element_alias = {
                "목": "木",
                "화": "火",
                "토": "土",
                "금": "金",
                "수": "水",
            }.get(str(element), "")
            if (
                element is not None
                and element not in answer
                and element_alias not in answer
            ):
                errors.append(
                    "required_schema_fact_omitted:chart.hard_facts.day_master.element"
                )
            yin_yang_alias = {"음": "陰", "양": "陽"}.get(str(yin_yang), "")
            if (
                yin_yang is not None
                and yin_yang not in answer
                and yin_yang_alias not in answer
            ):
                errors.append(
                    "required_schema_fact_omitted:chart.hard_facts.day_master.yin_yang"
                )
        elif "선택 날짜의 연간지" in question or "year/month/day ganzhi" in question:
            require_suffixes(period_ganzhi)
        elif "각 기둥의 천간·지지" in question:
            require_positioned_pillar_fields(
                (
                    "stem",
                    "stem_element",
                    "stem_yin_yang",
                    "branch",
                    "branch_element",
                    "branch_yin_yang",
                )
            )
        elif "일주의 천간·지지·지장간" in question:
            suffixes = [
                "chart.hard_facts.pillars.day.stem",
                "chart.hard_facts.pillars.day.branch",
            ]
            suffixes.extend(
                path
                for path in spec["allowed_fact_paths"]
                if path.startswith("chart.hard_facts.pillars.day.hidden_stems[")
            )
            require_suffixes(suffixes)
        elif "각 기둥의 stem ten-god" in question:
            require_positioned_pillar_fields(("stem_ten_god", "branch_ten_god"))
        elif "표면 오행 개수를 누락 없이" in question:
            observed = dict(surface_element_claims(answer))
            for element in "목화토금수":
                expected = _path_value(
                    spec, f"chart.hard_facts.surface_five_elements.{element}"
                )
                if expected is not None and observed.get(element) != int(expected):
                    errors.append(
                        "required_schema_fact_omitted:"
                        f"chart.hard_facts.surface_five_elements.{element}"
                    )
        elif "원국 네 기둥과 선택 날짜 세 간지" in question:
            require_suffixes([*pillar_ganzhi, *period_ganzhi])

    if axis == "chart_facts_natural_explanation" and not any(
        value in answer for value in natal
    ):
        errors.append("provided_natal_fact_omitted")
    if axis in {"chart_day_today_flow", "followup_explain_grounding"}:
        period_day = _path_value(spec, "period.hard_facts.period.day_ganzhi")
        if period_day is not None and period_day not in answer:
            errors.append("provided_period_day_fact_omitted")
        if natal and not any(value in answer for value in natal):
            errors.append("provided_natal_fact_omitted")
    if axis == "hard_fact_short_qa":
        natal_day = _path_value(spec, "chart.hard_facts.pillars.day.ganzhi")
        if "원국" not in answer or "일주" not in answer or natal_day not in answer:
            errors.append("chart_and_day_pillar_distinction_omitted")
    if axis == "general_korean_empathy" and (
        UNAMBIGUOUS_SAJU_INJECTION.search(answer)
        or re.search(
            r"(?:연주|일주)[^\n.!?]{0,16}"
            r"(?:기둥|간지|천간|지지|[甲乙丙丁戊己庚辛壬癸])|"
            r"일간[^\n.!?]{0,16}(?:오행|음양|[甲乙丙丁戊己庚辛壬癸])",
            answer,
        )
    ):
        errors.append("false_saju_injection")
    return errors


def _validate_message(message: Any, *, assistant_allowed: bool) -> None:
    if (
        not isinstance(message, Mapping)
        or set(message) != {"role", "content"}
        or message.get("role")
        not in (
            {"system", "user", "assistant"} if assistant_allowed else {"system", "user"}
        )
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
    ):
        raise Mix2KV4ContractError("prompt message 계약이 다릅니다.")


def validate_runtime_binding(binding: Any, *, require_day: bool) -> None:
    if not isinstance(binding, Mapping) or set(binding) != {
        "binding_id",
        "capability_sha256",
        "schema_version",
        "snapshot_sha256",
        "state_revision",
        "value",
    }:
        raise Mix2KV4ContractError("runtime binding field 집합이 다릅니다.")
    value = binding.get("value")
    if (
        binding.get("binding_id") != RUNTIME_BINDING_ID
        or binding.get("schema_version") != RUNTIME_BINDING_SCHEMA
        or FULL_SHA256.fullmatch(str(binding.get("capability_sha256", ""))) is None
        or FULL_SHA256.fullmatch(str(binding.get("snapshot_sha256", ""))) is None
        or isinstance(binding.get("state_revision"), bool)
        or not isinstance(binding.get("state_revision"), int)
        or binding["state_revision"] < 1
        or not isinstance(value, Mapping)
        or set(value) != {"chart", "period"}
        or not isinstance(value.get("chart"), Mapping)
    ):
        raise Mix2KV4ContractError("runtime binding identity가 다릅니다.")
    if sha256_bytes(canonical_json_bytes(value)) != binding["snapshot_sha256"]:
        raise Mix2KV4ContractError("runtime snapshot SHA-256이 다릅니다.")
    chart = value["chart"]
    if chart.get("status") != "ok" or chart.get("fact_authority") != "HARD_GT":
        raise Mix2KV4ContractError("runtime chart가 exact HARD_GT가 아닙니다.")
    period = value.get("period")
    if require_day:
        facts = period.get("hard_facts") if isinstance(period, Mapping) else None
        point = facts.get("period") if isinstance(facts, Mapping) else None
        if (
            not isinstance(period, Mapping)
            or period.get("status") != "ok"
            or period.get("fact_authority") != "HARD_GT"
            or not isinstance(point, Mapping)
            or point.get("period_type") != "day"
            or point.get("timezone") != "Asia/Seoul"
            or point.get("evaluation_local_time") != "12:00"
        ):
            raise Mix2KV4ContractError("runtime 단일 일진 계약이 다릅니다.")
    elif period is not None:
        raise Mix2KV4ContractError("chart-only spec에는 period가 없어야 합니다.")


def validate_spec(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != SPEC_FIELDS:
        raise Mix2KV4ContractError("spec field 집합이 다릅니다.")
    axis = row.get("task_axis")
    if (
        row.get("schema_version") != RECORD_SCHEMA_VERSION
        or row.get("dataset_version") != DATASET_VERSION
        or axis not in EXPECTED_AXES
        or not isinstance(row.get("id"), str)
        or not row["id"].startswith("m2v4_")
        or not isinstance(row.get("conversation_id"), str)
        or not row["conversation_id"].startswith("m2v4c_")
        or not isinstance(row.get("template_family"), str)
        or row.get("substantive") is not (axis in SUBSTANTIVE_AXES)
        or row.get("multiturn") is not (axis in MULTITURN_AXES)
        or row.get("drafter") not in {"claude", "codex"}
        or row.get("reviewer") not in {"claude", "codex"}
        or row["drafter"] == row["reviewer"]
        or row.get("restricted_local_only") is not False
    ):
        raise Mix2KV4ContractError("spec identity·역할 계약이 다릅니다.")
    prompt = row.get("prompt")
    if not isinstance(prompt, list) or len(prompt) < 2:
        raise Mix2KV4ContractError("spec prompt가 비었습니다.")
    for message in prompt:
        _validate_message(message, assistant_allowed=True)
    if prompt[0]["role"] != "system" or prompt[-1]["role"] != "user":
        raise Mix2KV4ContractError("spec prompt 시작·종료 role이 다릅니다.")
    expected_roles = [
        "system",
        *(
            "user" if index % 2 == 0 else "assistant"
            for index in range(len(prompt) - 1)
        ),
    ]
    if [message["role"] for message in prompt] != expected_roles:
        raise Mix2KV4ContractError("spec prompt role이 교대로 배치되지 않았습니다.")
    if row["multiturn"] and not any(
        message["role"] == "assistant" for message in prompt[1:-1]
    ):
        raise Mix2KV4ContractError("multiturn spec에 이전 assistant가 없습니다.")
    binding = row.get("runtime_binding")
    if axis in RUNTIME_AXES:
        validate_runtime_binding(binding, require_day=axis in DAY_AXES)
    elif binding is not None:
        if axis != "uncertainty_blocked_boundary":
            raise Mix2KV4ContractError("runtime 비대상 axis에 binding이 있습니다.")
        validate_runtime_binding(binding, require_day=True)
    allowed_paths = row.get("allowed_fact_paths")
    allowed_values = row.get("allowed_fact_values")
    if not isinstance(allowed_paths, list) or not isinstance(allowed_values, list):
        raise Mix2KV4ContractError("fact allowlist가 list가 아닙니다.")
    if any(
        not isinstance(item, str) or not item
        for item in [*allowed_paths, *allowed_values]
    ):
        raise Mix2KV4ContractError("fact allowlist 값이 잘못됐습니다.")
    if len(allowed_paths) != len(allowed_values):
        raise Mix2KV4ContractError("fact allowlist path/value 길이가 다릅니다.")
    if binding is not None:
        flattened = flatten_runtime_facts(binding["value"])
        if allowed_paths != [path for path, _ in flattened] or allowed_values != [
            value for _, value in flattened
        ]:
            raise Mix2KV4ContractError(
                "fact allowlist가 full runtime snapshot과 다릅니다."
            )
    contract = row.get("response_contract")
    if not isinstance(contract, Mapping) or set(contract) != {
        "hard_max_completion_tokens",
        "minimum_nonempty_lines",
        "minimum_sentences",
        "natural_length_no_preferred_maximum",
    }:
        raise Mix2KV4ContractError("response contract field 집합이 다릅니다.")
    expected_minimum = 3 if row["substantive"] else 1
    if contract != {
        "hard_max_completion_tokens": MAX_COMPLETION_TOKENS,
        "minimum_nonempty_lines": expected_minimum,
        "minimum_sentences": expected_minimum,
        "natural_length_no_preferred_maximum": True,
    }:
        raise Mix2KV4ContractError("response contract 값이 다릅니다.")
    serialized = json.dumps(row, ensure_ascii=False, sort_keys=True)
    if RESTRICTED_MARKERS.search(serialized):
        raise Mix2KV4ContractError("외부 전송 금지 marker가 spec에 있습니다.")
    return row


def validate_draft(spec: Mapping[str, Any], draft: Any) -> dict[str, Any]:
    if not isinstance(draft, dict) or set(draft) != DRAFT_FIELDS:
        raise Mix2KV4ContractError("teacher draft field 집합이 다릅니다.")
    answer = draft.get("answer")
    if (
        draft.get("record_id") != spec["id"]
        or not isinstance(answer, str)
        or not answer.strip()
        or not isinstance(draft.get("used_fact_paths"), list)
        or not isinstance(draft.get("used_fact_values"), list)
        or type(draft.get("soft_interpretation_used")) is not bool
        or not isinstance(draft.get("limitations"), list)
        or draft.get("self_check") != "PASS"
    ):
        raise Mix2KV4ContractError("teacher draft identity·self-check가 다릅니다.")
    if any(
        not isinstance(item, str)
        for item in [
            *draft["used_fact_paths"],
            *draft["used_fact_values"],
            *draft["limitations"],
        ]
    ):
        raise Mix2KV4ContractError("teacher draft provenance가 문자열 list가 아닙니다.")
    if any(
        len(draft[field]) != len(set(draft[field]))
        for field in ("used_fact_paths", "used_fact_values")
    ):
        raise Mix2KV4ContractError("teacher draft provenance에 중복값이 있습니다.")
    if not set(draft["used_fact_paths"]).issubset(spec["allowed_fact_paths"]):
        raise Mix2KV4ContractError("teacher가 허용되지 않은 fact path를 사용했습니다.")
    if not set(draft["used_fact_values"]).issubset(spec["allowed_fact_values"]):
        raise Mix2KV4ContractError("teacher가 허용되지 않은 fact value를 사용했습니다.")
    contract = spec["response_contract"]
    if (
        len(nonempty_lines(answer)) < contract["minimum_nonempty_lines"]
        or sentence_count(answer) < contract["minimum_sentences"]
    ):
        raise Mix2KV4ContractError(
            "teacher 답변이 spec의 최소 줄·문장 계약을 충족하지 않습니다."
        )
    if INTERNAL_LANGUAGE.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 내부 계약 용어를 노출합니다.")
    if RESTRICTED_MARKERS.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 외부 반출 금지 marker를 포함합니다.")
    if FORBIDDEN_PREDICTION.search(answer):
        raise Mix2KV4ContractError("teacher 답변이 확정적 사건 예측을 포함합니다.")
    errors = structural_claim_errors(spec, answer)
    errors.extend(required_fact_errors(spec, answer))
    if errors:
        raise Mix2KV4ContractError("teacher 구조 사실 claim 오류: " + ",".join(errors))
    claimed_values = {
        *GANYI.findall(answer),
        *normalized_dates(answer),
        *_mentioned_ten_gods(answer),
    }
    if not claimed_values.issubset(draft["used_fact_values"]):
        missing = sorted(claimed_values - set(draft["used_fact_values"]))
        raise Mix2KV4ContractError(
            "teacher used_fact_values에 명시 claim이 빠졌습니다: " + ",".join(missing)
        )
    return draft


def validate_review(spec: Mapping[str, Any], review: Any) -> dict[str, Any]:
    if not isinstance(review, dict) or set(review) != REVIEW_FIELDS:
        raise Mix2KV4ContractError("peer review field 집합이 다릅니다.")
    if (
        review.get("record_id") != spec["id"]
        or review.get("decision") not in {"PASS", "FAIL"}
        or not all(
            isinstance(review.get(key), list)
            for key in ("failure_codes", "fact_errors", "style_notes")
        )
        or not isinstance(review.get("rewrite_instructions"), str)
        or any(
            not isinstance(item, str)
            for key in ("failure_codes", "fact_errors", "style_notes")
            for item in review[key]
        )
    ):
        raise Mix2KV4ContractError("peer review identity·형식이 다릅니다.")
    if review["decision"] == "PASS" and any(
        (
            review["failure_codes"],
            review["fact_errors"],
            review["rewrite_instructions"].strip(),
        )
    ):
        raise Mix2KV4ContractError("PASS review에 실패 정보가 남았습니다.")
    if review["decision"] == "FAIL" and not review["failure_codes"]:
        raise Mix2KV4ContractError("FAIL review에 실패 코드가 없습니다.")
    return review


def validate_specs(
    rows: Sequence[dict[str, Any]], config: Mapping[str, Any]
) -> dict[str, Any]:
    for row in rows:
        validate_spec(row)
    axes = Counter(row["task_axis"] for row in rows)
    drafters = Counter(row["drafter"] for row in rows)
    templates = Counter(row["template_family"] for row in rows)
    runtime_snapshots = {
        row["runtime_binding"]["snapshot_sha256"]
        for row in rows
        if row["runtime_binding"] is not None
    }
    target_dates = {
        row["runtime_binding"]["value"]["period"]["hard_facts"]["period"]["target_date"]
        for row in rows
        if row["runtime_binding"] is not None
    }
    if len(rows) != EXPECTED_ROWS or len({row["id"] for row in rows}) != EXPECTED_ROWS:
        raise Mix2KV4ContractError("spec은 고유 ID 2,000행이어야 합니다.")
    if dict(axes) != EXPECTED_AXES:
        raise Mix2KV4ContractError(f"axis 수량이 다릅니다: {dict(axes)}")
    if drafters != {"claude": 1_000, "codex": 1_000}:
        raise Mix2KV4ContractError(f"teacher 역할 수량이 다릅니다: {dict(drafters)}")
    maximum = int(config["diversity"]["template_family_rows_maximum"])
    if not templates or max(templates.values()) > maximum:
        raise Mix2KV4ContractError("template family가 최대 20행을 넘습니다.")
    if sum(row["multiturn"] for row in rows) < int(
        config["diversity"]["minimum_multiturn_rows"]
    ):
        raise Mix2KV4ContractError("multiturn 행이 300건 미만입니다.")
    if len(runtime_snapshots) < int(
        config["diversity"]["minimum_unique_runtime_snapshots"]
    ):
        raise Mix2KV4ContractError("runtime snapshot이 600건 미만입니다.")
    if len(target_dates) < int(config["diversity"]["minimum_unique_target_dates"]):
        raise Mix2KV4ContractError("단일 일진 날짜가 300건 미만입니다.")
    return {
        "rows": len(rows),
        "axes": dict(sorted(axes.items())),
        "drafters": dict(sorted(drafters.items())),
        "templates": len(templates),
        "multiturn_rows": sum(row["multiturn"] for row in rows),
        "unique_runtime_snapshots": len(runtime_snapshots),
        "unique_target_dates": len(target_dates),
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise Mix2KV4ContractError(f"JSONL이 없거나 symlink입니다: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Mix2KV4ContractError(f"JSONL 빈 행: {number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Mix2KV4ContractError(f"JSONL object 오류: {number}")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Mix2KV4ContractError):
            raise
        raise Mix2KV4ContractError(f"JSONL을 읽지 못했습니다: {path}") from exc
    return rows


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )


__all__ = [
    "DATASET_VERSION",
    "DAY_AXES",
    "DRAFT_FIELDS",
    "EXPECTED_AXES",
    "EXPECTED_ROWS",
    "MAX_COMPLETION_TOKENS",
    "MAX_LENGTH",
    "MAX_PROMPT_TOKENS",
    "MULTITURN_AXES",
    "PRIVATE_DIR_MODE",
    "PRIVATE_FILE_MODE",
    "RECORD_SCHEMA_VERSION",
    "REVIEW_FIELDS",
    "RUNTIME_AXES",
    "RUNTIME_BINDING_ID",
    "RUNTIME_BINDING_SCHEMA",
    "RUNTIME_RELEASE_ID",
    "SPEC_FIELDS",
    "SUBSTANTIVE_AXES",
    "Mix2KV4ContractError",
    "flatten_runtime_facts",
    "jsonl_bytes",
    "nonempty_lines",
    "normalize_answer",
    "read_jsonl",
    "required_fact_errors",
    "sentence_count",
    "sha256_bytes",
    "sha256_file",
    "structural_claim_errors",
    "validate_draft",
    "validate_review",
    "validate_runtime_binding",
    "validate_spec",
    "validate_specs",
]
