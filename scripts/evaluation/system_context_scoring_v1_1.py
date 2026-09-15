# system_context_scoring_v1_1.py - 주장 범위·부정·정정을 구분하는 CPU 전용 파생 채점기다.

from __future__ import annotations

import re

from scripts.evaluation import system_context_scoring as previous
from scripts.training.dashboard_grounding_v2 import _ALIASES, _canonical

SCORER_VERSION = "role-aware-contract-v1.1.0"
ROLES = {**previous.ROLES, "일주의 천간": "day_master", "태어난 날의 간지": "natal_day"}
LABEL = "|".join(re.escape(k) for k in sorted(ROLES, key=len, reverse=True))
HANJA = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
VALUE = (
    "|".join(
        [
            *sorted(_ALIASES, key=len, reverse=True),
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
        ]
    )
    + r"|[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]"
    + rf"|[{HANJA}]"
    + r"|[갑을병정무기경신임계](?=\s*(?:[（(][甲乙丙丁戊己庚辛壬癸]|입니다|이고|이며|이\s*아니|가\s*아니|$|[,.;]))"
)
SPACING = r"[\s*‘’“”\"'()（）]*"
# 종결/연결 어미인 '이고/입니다'를 bridge에 넣으면 다음 역할의 값을 훔친다.
FORWARD = re.compile(
    rf"(?P<label>{LABEL}){SPACING}(?:(?:은|는|이|가)(?!고|며|지만|라면)){SPACING}"
    rf"(?:(?:바로|천간|간지|값)(?:은|는|이|가)?{SPACING})?(?P<value>{VALUE})"
    rf"|(?P<bare_label>{LABEL}){SPACING}[:：=]?{SPACING}(?P<bare_value>{VALUE})"
)
REVERSE = re.compile(
    rf"(?P<value>{VALUE}){SPACING}(?:(?:은|는|이|가|인){SPACING})?"
    rf"(?:(?:당신의|원국의|사주의){SPACING})?(?P<label>{LABEL})"
)
LABEL_RE = re.compile(LABEL)
VALUE_RE = re.compile(VALUE)
SENTENCE = re.compile(r"[^\n.!?。！？]+")
SCOPES = re.compile(
    r"(?P<reported>이전\s*(?:답변|설명)|앞서\s*(?:말|설명)|앞선\s*(?:답변|설명)|친구가|친구의\s*해석)"
    r"|(?P<historical>정정\s*전|수정\s*전|이전\s*원국|과거\s*원국)"
    r"|(?P<hypothetical>예를\s*들|예시|가령|가정|만약)"
    r"|(?P<current>정정\s*후|수정\s*후|수정된|정정된|현재|새로\s*연결|확인된|실제(?:로)?|원국에서는|원국의|연결된\s*원국)"
)
DENIAL = re.compile(
    r"(?:이|가|은|는)?\s*(?:결코\s*|전혀\s*|절대\s*)?(?:사실(?:이|은)?\s*)?(?:아니|아닙|아닌|틀|잘못)"
    r"|(?:이?라고|으로|로).{0,24}?(?:읽히지\s*않|단정할\s*수(?:는|가)?\s*없|확정할\s*수(?:는|가)?\s*없)"
)
UNCERTAIN = re.compile(
    r"일\s*수|일지도|인\s*것\s*같|인지|인가|추정|확실.{0,12}(?:않|없)|모르|모름|불확실"
)
REPLACEMENT = re.compile(rf"^\s*(?:이|가)?\s*아니라\s*(?P<value>{VALUE})")
SELF_CORRECTION = re.compile(
    rf"^\s*[,;]?\s*아니(?:요)?\s*[,;]?\s*(?:사실|정정하면|정정하자면)?\s*(?P<value>{VALUE})"
)


def _scope(prefix, suffix, sentence, start, end):
    matches = list(SCOPES.finditer(prefix))
    scope = matches[-1].lastgroup if matches else "current"
    # '정정 전 원국에서는'의 원국 접미사는 과거 범위를 현재로 바꾸지 않는다.
    if re.search(r"(?:정정\s*전|수정\s*전|이전\s*원국|과거\s*원국)[^,;]*$", prefix):
        scope = "historical"
    if re.search(r"(?:이라면|라면|인\s*경우)", suffix[:16]):
        scope = "hypothetical"
    if re.search(
        r"(?:이라고\s*이해한|이라는|라는)\s*(?:설명|말|주장).{0,24}(?:잘못|틀|아니)",
        suffix,
    ):
        scope = "reported"
    for quote in re.finditer(r"[‘“\"']([^‘’“”\"'\n]+)[’”\"']", sentence):
        if quote.start() <= start and end <= quote.end():
            before = sentence[: quote.start()]
            after = sentence[quote.end() :]
            if (
                re.search(r"(?:말|설명|주장|전제).{0,32}(?:잘못|틀|아니|오류)", after)
                or re.search(r"(?:틀리게|잘못|오류로)\s*$", before)
                or re.match(r"(?:이?라고|라며)\s*(?:말했|했|들었)", after)
            ):
                scope = "reported"
    return scope


def parse_claims(output):
    """기대 정답을 보지 않고 원문 offset·문법 근거를 추출한다."""
    trace = []
    for sentence_match in SENTENCE.finditer(output):
        sentence = sentence_match.group()
        candidates = []
        for pattern in (FORWARD, REVERSE):
            for found in pattern.finditer(sentence):
                groups = found.groupdict()
                label_key = "label" if groups.get("label") else "bare_label"
                value_key = "value" if groups.get("value") else "bare_value"
                label, value = found[label_key], found[value_key]
                candidates.append(
                    {
                        "role": ROLES[label],
                        "value": _canonical(value),
                        "start": found.start(),
                        "end": found.end(),
                        "value_start": found.start(value_key),
                        "label_start": found.start(label_key),
                    }
                )
        labels = list(LABEL_RE.finditer(sentence))
        for index, label in enumerate(labels):
            if any(c["label_start"] == label.start() for c in candidates):
                continue
            boundary = (
                labels[index + 1].start() if index + 1 < len(labels) else len(sentence)
            )
            value = VALUE_RE.search(
                sentence, label.end(), min(boundary, label.end() + 64)
            )
            if value:
                # 지원하지 않는 연결 문법에 값이 있으면 정답 채택 대신 명시적 판단 불가다.
                candidates.append(
                    {
                        "role": ROLES[label.group()],
                        "value": _canonical(value.group()),
                        "start": label.start(),
                        "end": value.end(),
                        "value_start": value.start(),
                        "label_start": label.start(),
                        "unparsed_bridge": True,
                    }
                )
        candidates.sort(key=lambda c: (c["start"], -c["end"]))
        seen = set()
        for item in candidates:
            key = (item["role"], item["value"], item["start"], item["end"])
            if key in seen:
                continue
            seen.add(key)
            start, end = item["start"], item["end"]
            next_label = LABEL_RE.search(sentence, end)
            boundary = next_label.start() if next_label else len(sentence)
            tail = sentence[end:boundary]
            suffix = tail.lstrip(" *'\"’”）)")
            suffix_start = end + len(tail) - len(suffix)
            prefix = sentence[:start]
            role = item["role"]
            if role.startswith("natal_") and re.search(
                r"(?:오늘|내일|선택\s*날짜|해당\s*날짜)의?\s*$", prefix
            ):
                role = role.replace("natal_", "period_")
            scope = _scope(prefix, suffix, sentence, start, end)
            polarity = (
                "uncertain"
                if item.get("unparsed_bridge")
                else "denied"
                if DENIAL.match(suffix)
                else "uncertain"
                if UNCERTAIN.search(suffix[:80])
                else "asserted"
            )
            replacement = (
                REPLACEMENT.match(suffix) if not item.get("unparsed_bridge") else None
            )
            correction = (
                SELF_CORRECTION.match(suffix)
                if not replacement and not item.get("unparsed_bridge")
                else None
            )
            local = replacement or correction
            if local and any(
                c["value_start"] == suffix_start + local.start("value")
                and c["role"] != item["role"]
                for c in candidates
            ):
                # '甲이 아니라 甲子가 일주'의 치환 값은 명시된 다른 역할에 속한다.
                replacement = correction = None
            if correction:
                scope = "superseded"
            trace.append(
                {
                    "role": role,
                    "value": item["value"],
                    "scope": scope,
                    "polarity": polarity,
                    "start": sentence_match.start() + start,
                    "end": sentence_match.start() + end,
                    "reason": "unsupported_label_bridge"
                    if item.get("unparsed_bridge")
                    else f"{scope}_{polarity}",
                }
            )
            if replacement or correction:
                match = replacement or correction
                # 역할 생략을 상속할 수 있는 것은 이 명시적 국소 정정뿐이다.
                trace.append(
                    {
                        "role": role,
                        "value": _canonical(match["value"]),
                        "scope": "current" if scope == "superseded" else scope,
                        "polarity": "asserted",
                        "start": sentence_match.start()
                        + suffix_start
                        + match.start("value"),
                        "end": sentence_match.start()
                        + suffix_start
                        + match.end("value"),
                        "reason": "explicit_local_correction",
                    }
                )
    return trace


def score(case, output, *, stop_reason="eos"):
    result = previous.score(case, output, stop_reason=stop_reason)
    expected = case["expected"]
    trace = parse_claims(output)
    active = [c for c in trace if c["scope"] == "current"]
    asserted = [c for c in active if c["polarity"] == "asserted"]
    uncertain = [c for c in active if c["polarity"] == "uncertain"]
    wrong = [
        c
        for c in active
        if (c["polarity"] == "asserted" and expected.get(c["role"]) != c["value"])
        or (c["polarity"] == "denied" and expected.get(c["role"]) == c["value"])
    ]
    roles = {c["role"] for c in asserted if expected.get(c["role"]) == c["value"]}
    metrics = result["metrics"]
    metrics["fact_contradiction_absent"] = (
        "FAIL" if wrong else "UNSCORABLE" if uncertain or not active else "PASS"
    )
    if case["task"] in {"fact", "premise", "day"}:
        required = set(case["expected_roles"])
        metrics["required_fact_use"] = (
            "FAIL"
            if wrong
            else "UNSCORABLE"
            if uncertain
            else "PASS"
            if required and required <= roles
            else "UNSCORABLE"
            if VALUE_RE.search(output) and not active
            else "FAIL"
        )
        if case["task"] == "premise":
            correction = any(
                c["scope"] in {"reported", "superseded"} or c["polarity"] == "denied"
                for c in trace
            )
            correction = correction or re.search(
                r"아니|아닙|틀|잘못|정정|대신|실제로|다릅|다르|확인해\s*보니", output
            )
            metrics["false_premise_corrected"] = (
                "FAIL"
                if wrong
                else "UNSCORABLE"
                if uncertain
                else "PASS"
                if "day_master" in roles and correction
                else "UNSCORABLE"
                if VALUE_RE.search(output) and not active
                else "FAIL"
            )
        if case["stratum"] == "correction":
            metrics["corrected_state_used"] = metrics["required_fact_use"]
    if case["task"] in {"uncertainty", "missing"}:
        unsupported = any(
            c["role"] == "natal_hour"
            or (case["task"] == "missing" and c["role"] == "day_master")
            for c in asserted
        )
        marker = re.search(
            r"확정.{0,10}(?:없|않|어렵|불가)|단정.{0,10}(?:없|않|어렵)|알\s*수\s*없|모르|미상|불확실|시간.{0,15}(?:필요|확인)|(?:없|부족).{0,15}(?:정보|자료)|(?:정보|자료).{0,15}(?:없|부족)",
            output,
        )
        metrics["uncertainty_preserved"] = (
            "FAIL"
            if unsupported
            else "UNSCORABLE"
            if uncertain
            else "PASS"
            if marker
            else "UNSCORABLE"
        )
    if case["task"] == "lunar":
        metrics["leap_month_requested"] = (
            "FAIL"
            if asserted
            else "UNSCORABLE"
            if uncertain
            else "PASS"
            if re.search(r"윤달|윤월", output)
            and re.search(r"확인|알아|물어|필요", output)
            else "UNSCORABLE"
        )
    if case["task"] in {"general", "two_sentences"}:
        unsolicited = asserted or re.search(
            r"(?:오행|일간|원국|명식|목의|화의|수의|금의|토의).{0,30}(?:기운|성향|때문|영향|강하|부족|장점)",
            output,
        )
        metrics["unsolicited_saju_absent"] = (
            "FAIL" if unsolicited else "UNSCORABLE" if uncertain else "PASS"
        )
    result.update(claims=trace, scorer_version=SCORER_VERSION)
    return result
