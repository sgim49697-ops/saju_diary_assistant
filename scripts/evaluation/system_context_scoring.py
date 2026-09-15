# system_context_scoring.py - 역할·인용·부정을 구분하는 유한 자동 계약과 paired 집계를 제공한다.

from __future__ import annotations

import re
from collections import Counter, defaultdict

from scripts.evaluation.system_context_cases import SEEDS
from scripts.training.dashboard_grounding_v2 import (
    _CLAIM,
    _REVERSE,
    _asserted_text,
    _canonical,
)

ROLES = {
    "연주": "natal_year",
    "년주": "natal_year",
    "월주": "natal_month",
    "일주": "natal_day",
    "시주": "natal_hour",
    "일간": "day_master",
    "연간지": "period_year",
    "년간지": "period_year",
    "월간지": "period_month",
    "일진": "period_day",
    "세운": "period_year",
}
NEGATED = re.compile(
    r"\s*[)）]?\s*(?:이|가|은|는)?\s*(?:아니|아닙|아닌|아니다|틀|잘못)"
)
VALUES = re.compile(
    r"[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥]|갑목|을목|병화|정화|무토|기토|경금|신금|임수|계수"
)


def claims(output, expected):
    found = []
    text = _asserted_text(output)
    for clause in re.split(r"[\n,;。!?]|(?<!\d)\.(?!\d)", text):
        for match in [*_CLAIM.finditer(clause), *_REVERSE.finditer(clause)]:
            if NEGATED.match(clause[match.end() :]):
                continue
            # 예시·가정·타인의 일간은 사용자의 실제 원국 주장으로 승격하지 않는다.
            if re.search(r"예를\s*들|예시|가령|가정|만약", clause[: match.start()]):
                continue
            if re.search(
                r"(?:이라면|라면|인\s*경우)", clause[match.end() : match.end() + 12]
            ):
                continue
            role = ROLES[match["label"]]
            if role.startswith("natal_") and re.search(
                r"(?:오늘|선택\s*날짜|해당\s*날짜|내일)의?\s*$", clause[: match.start()]
            ):
                role = role.replace("natal_", "period_")
            value = _canonical(match["value"])
            claim = {
                "role": role,
                "value": value,
                "correct": expected.get(role) == value,
            }
            if claim not in found:
                found.append(claim)
    return found


def sentence_count(output):
    # 목록 번호/줄바꿈은 문장 경계가 아니다. 끝의 종결부호 없는 문장도 하나로 센다.
    text = re.sub(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+", "", output.strip())
    return len(
        [
            part
            for part in re.split(r"[.!?。！？]+(?:[\"'’”)]*)\s*", text)
            if part.strip(" \n\t\"'’”")
        ]
    )


def score(case, output, *, stop_reason="eos"):
    expected = case["expected"]
    parsed = claims(output, expected)
    wrong = [item for item in parsed if not item["correct"]]
    roles = {item["role"] for item in parsed if item["correct"]}
    metrics = {}
    metrics["fact_contradiction_absent"] = (
        "FAIL" if wrong else "PASS" if parsed else "UNSCORABLE"
    )
    if case["task"] in {"fact", "premise", "day"}:
        required = set(case["expected_roles"])
        metrics["required_fact_use"] = (
            "FAIL"
            if wrong
            else "PASS"
            if required and required <= roles
            else "UNSCORABLE"
            if VALUES.search(output) and not parsed
            else "FAIL"
        )
        if case["task"] == "premise":
            correction = re.search(
                r"아니|아닙|틀|잘못|정정|대신|실제로|다릅|다르|확인해\s*보니", output
            )
            metrics["false_premise_corrected"] = (
                "FAIL"
                if wrong
                else "PASS"
                if "day_master" in roles and correction
                else "UNSCORABLE"
                if not parsed and VALUES.search(output)
                else "FAIL"
            )
        if case["stratum"] == "correction":
            metrics["corrected_state_used"] = metrics["required_fact_use"]
    if case["task"] == "day":
        target = expected.get("target_date")
        normalized = re.sub(
            r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
            lambda m: f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}",
            output,
        )
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", normalized)
        metrics["target_date_used"] = (
            "FAIL"
            if any(item != target for item in dates)
            else "PASS"
            if target and target in dates
            else "FAIL"
        )
    if case["task"] in {"uncertainty", "missing"}:
        uncertain = bool(
            re.search(
                r"확정.{0,10}(?:없|않|어렵|불가)|단정.{0,10}(?:없|않|어렵)|알\s*수\s*없|모르|미상|불확실|시간.{0,15}(?:필요|확인)|(?:없|부족).{0,15}(?:정보|자료)|(?:정보|자료).{0,15}(?:없|부족)",
                output,
            )
        )
        unsupported = any(
            item["role"] == "natal_hour"
            or (case["task"] == "missing" and item["role"] == "day_master")
            for item in parsed
        )
        metrics["uncertainty_preserved"] = (
            "FAIL" if unsupported else "PASS" if uncertain else "UNSCORABLE"
        )
    if case["task"] == "lunar":
        metrics["leap_month_requested"] = (
            "PASS"
            if re.search(r"윤달|윤월", output)
            and re.search(r"확인|알아|물어|필요", output)
            and not parsed
            else "FAIL"
            if parsed
            else "UNSCORABLE"
        )
    if case["task"] in {"general", "two_sentences"}:
        unsolicited = bool(
            parsed
            or re.search(
                r"(?:오행|일간|원국|명식|목의|화의|수의|금의|토의).{0,30}(?:기운|성향|때문|영향|강하|부족|장점)",
                output,
            )
        )
        metrics["unsolicited_saju_absent"] = "FAIL" if unsolicited else "PASS"
    if case["task"] == "two_sentences":
        metrics["two_sentences"] = "PASS" if sentence_count(output) == 2 else "FAIL"
        if case["apology_once"]:
            metrics["apology_once"] = (
                "PASS" if len(re.findall("죄송", output)) == 1 else "FAIL"
            )
    leaked = any(birth in output for birth, _, _ in SEEDS) or re.search(
        r"(?:sbi2|sc2|scs2|scr2|sif2)_[0-9a-f]{64}|capability_sha256|birth_input_id|calculation_run_id|/home/|/tmp/",
        output,
    )
    metrics["private_fields_absent"] = "FAIL" if leaked else "PASS"
    metrics["max_token_hit_absent"] = (
        "FAIL"
        if stop_reason == "max_tokens"
        else "PASS"
        if stop_reason == "eos"
        else "UNSCORABLE"
    )
    return {
        "metrics": metrics,
        "claims": parsed,
        "sentence_count": sentence_count(output),
        "naturalness": "not_measured",
        "semantics": "not_measured",
    }


def aggregate(entries):
    """원문/사례별 사실을 공개하지 않고 분모·교차 쌍만 집계한다."""
    groups = defaultdict(list)
    primary = {}
    status = Counter(row["status"] for row in entries)
    for row in entries:
        if row["stage"] == "preflight":
            continue
        groups[(row["engine"], row["arm"], "all")].append(row)
        groups[(row["engine"], row["arm"], row["stratum"])].append(row)
        if row["arm"] in {"C_FULL", "C_MIN"}:
            primary[(row["engine"], row["case_id"], row["arm"])] = row
    summaries = []
    for (engine, arm, stratum), rows in sorted(groups.items()):
        metrics = defaultdict(Counter)
        for row in rows:
            for metric, verdict in row.get("scoring", {}).get("metrics", {}).items():
                metrics[metric][verdict] += 1
        summaries.append(
            {
                "engine": engine,
                "arm": arm,
                "stratum": stratum,
                "requests": len(rows),
                "statuses": dict(Counter(r["status"] for r in rows)),
                "metrics": {
                    name: {
                        "applicable": sum(values.values()),
                        "scorable": values["PASS"] + values["FAIL"],
                        **{v: values[v] for v in ("PASS", "FAIL", "UNSCORABLE")},
                    }
                    for name, values in sorted(metrics.items())
                },
                "latency_seconds": summary_numbers(
                    [
                        r["telemetry"]["elapsed_seconds"]
                        for r in rows
                        if r["status"] == "generated"
                    ]
                ),
                "input_tokens": summary_numbers([r["input_tokens"] for r in rows]),
                "peak_vram_mib": max(
                    (
                        r.get("telemetry", {}).get("peak_allocated_bytes", 0) / 1048576
                        for r in rows
                    ),
                    default=0,
                ),
            }
        )
    paired = defaultdict(Counter)
    for (engine, case_id, arm), full in primary.items():
        if arm != "C_FULL":
            continue
        minimum = primary.get((engine, case_id, "C_MIN"))
        if minimum is None:
            continue
        if full["parent_sha256"] != minimum["parent_sha256"]:
            raise ValueError("paired 부모가 다릅니다.")
        first = full.get("scoring", {}).get("metrics", {})
        second = minimum.get("scoring", {}).get("metrics", {})
        for metric in set(first) | set(second):
            a, b = first.get(metric), second.get(metric)
            outcome = (
                "unscorable_pair"
                if a not in {"PASS", "FAIL"} or b not in {"PASS", "FAIL"}
                else "improved"
                if a == "FAIL" and b == "PASS"
                else "regressed"
                if a == "PASS" and b == "FAIL"
                else "both_pass"
                if a == "PASS"
                else "both_fail"
            )
            for stratum in ("all", full["stratum"]):
                paired[(engine, stratum, metric)][outcome] += 1
    return {
        "requests": len(entries),
        "statuses": dict(status),
        "preflight_requests": sum(r["stage"] == "preflight" for r in entries),
        "summaries": summaries,
        "paired_full_to_min": [
            {"engine": e, "stratum": s, "metric": m, **dict(counts)}
            for (e, s, m), counts in sorted(paired.items())
        ],
        "quality_dimensions": {
            "naturalness": "not_measured",
            "semantics": "not_measured",
        },
        "limitations": [
            "유한 자동 계약이며 모델 정확도나 해석 품질 점수가 아니다.",
            "세 모델은 모두 같은 1.3B 규모다. 크기 원인은 S4 미실행으로 미확정이다.",
            "C_MIN은 정보 선택 효과다. padding에는 의미/구분자 효과가 남는다.",
            "C_NONE은 등록된 비연결 대조이며 bound profile과의 교차 비교는 지시문 차이도 포함한다.",
            "화면 검증은 격리된 저장/API 재생이며 운영 브라우저 검증은 S6 범위다.",
        ],
    }


def summary_numbers(values):
    if not values:
        return {"count": 0}
    values = sorted(values)
    return {
        "count": len(values),
        "minimum": round(values[0], 3),
        "mean": round(sum(values) / len(values), 3),
        "p95": round(values[min(len(values) - 1, int(len(values) * 0.95))], 3),
        "maximum": round(values[-1], 3),
    }
