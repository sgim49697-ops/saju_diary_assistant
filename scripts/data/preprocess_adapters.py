# preprocess_adapters.py - MIX20K 예비 20% 후보를 원천별로 검증·한국어 렌더링한다.

from __future__ import annotations

import hashlib
import heapq
import json
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts.data.audit_tools import (
    BRANCH_PINYIN,
    BRANCHES,
    ELEMENTS,
    PILLAR_ORDER,
    STEM_PINYIN,
    STEMS,
    apply_yeji_corrections,
    canonical_chart_from_bazi,
    canonical_chart_from_nemotron,
    leakage_group_id,
)
from scripts.data.errors import Phase2AuditError
from scripts.data.source_tools import source_root

ADAPTER_SCHEMA_VERSION = "1.1.0"
ELEMENT_KO = {
    "Wood": "목",
    "Fire": "화",
    "Earth": "토",
    "Metal": "금",
    "Water": "수",
}
POLARITY_KO = {"yin": "음", "yang": "양"}
SUPPORTING_ELEMENT = {
    "Wood": "Water",
    "Fire": "Wood",
    "Earth": "Fire",
    "Metal": "Earth",
    "Water": "Metal",
}
QUESTION_TYPES = ("career", "element_balance", "general_natal", "relationships")
RULE_IDS = (
    "day_master_strong",
    "day_master_weak",
    "dm_supported",
    "dominant_element",
    "missing_elements",
)
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
KOREAN_PATTERN = re.compile(r"[가-힣]")
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z]{2,}")
NAME_PATTERN = re.compile(r"(?<![가-힣])([가-힣]{2,4})\s*씨(?:는|가|의|에게|께서는)?")
PII_PATTERNS = (
    re.compile(r"\b01[016789][ -]?\d{3,4}[ -]?\d{4}\b"),
    re.compile(r"\b\d{6}[ -]?[1-4]\d{6}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
)
ADDITIONAL_UNSAFE_PATTERNS = (
    re.compile(r"(?:주식|코인|투자).{0,12}(?:사라|매수|매도|수익|보장)"),
    re.compile(r"(?:법률|소송).{0,12}(?:확실|보장|무조건)"),
    re.compile(r"(?:혐오|죽여|패버려|성폭력)"),
    re.compile(r"(?:발작|간질|질환|증상|통증|복용|처방|환자|아프(?:다|고|다는|다는 것을))"),
)
NARRATIVE_ORDER = (
    ("saju_summary", "사주 요약"),
    ("personality_reading", "성향 해석"),
    ("career_reading", "일·진로 해석"),
    ("lacking_element_advice", "오행 균형 참고"),
)
JIAZI = tuple(STEMS[index % 10] + BRANCHES[index % 12] for index in range(60))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_rank(seed: int, *values: Any) -> str:
    normalized = "|".join(str(value) for value in (seed, *values))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = CONTROL_PATTERN.sub("", text).replace("\ufffd", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line).strip()
    return text.replace("OO님", "사용자님").replace("OO 님", "사용자님")


def _duckdb_module() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise Phase2AuditError(
            "duckdb가 없습니다. uv pip으로 requirements-data.txt를 설치하세요."
        ) from exc
    return duckdb


def _iter_rows(cursor: Any, batch_size: int = 2_000) -> Iterator[tuple[Any, ...]]:
    while rows := cursor.fetchmany(batch_size):
        yield from rows


def _compile_patterns(values: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(value, re.IGNORECASE) for value in values)


def _contains_any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) is not None for pattern in patterns)


def _record_policy_exclusion(
    counters: Counter[str], matches: Sequence[str]
) -> None:
    """겹칠 수 있는 정책 일치와 상호배타적인 주 제외 사유를 함께 기록한다."""
    if not matches:
        return
    counters["excluded_policy_union"] += 1
    counters[f"excluded_primary_{matches[0]}"] += 1
    for reason in matches:
        counters[f"matched_{reason}"] += 1


def calendar_relations_valid(chart: Sequence[str]) -> bool:
    """연-월 오호둔과 일-시 오서둔의 필수 천간 관계를 검사한다."""
    if len(chart) != 4 or any(pillar not in JIAZI for pillar in chart):
        return False
    year, month, day_pillar, hour = chart
    month_offset = (BRANCHES.index(month[1]) - BRANCHES.index("寅")) % 12
    expected_month_stem = (
        (STEMS.index(year[0]) % 5) * 2 + STEMS.index("丙") + month_offset
    ) % 10
    expected_hour_stem = (
        (STEMS.index(day_pillar[0]) % 5) * 2 + BRANCHES.index(hour[1])
    ) % 10
    return (
        STEMS.index(month[0]) == expected_month_stem
        and STEMS.index(hour[0]) == expected_hour_stem
    )


def _push_smallest(
    heap: list[tuple[int, str, dict[str, Any]]],
    limit: int,
    rank: str,
    item: dict[str, Any],
) -> None:
    numeric = int(rank, 16)
    entry = (-numeric, rank, item)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif numeric < -heap[0][0]:
        heapq.heapreplace(heap, entry)


def _ordered_heap(heap: list[tuple[int, str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [item for _, _, item in sorted(heap, key=lambda entry: entry[1])]


def _message_lengths(messages: Sequence[dict[str, str]]) -> tuple[int, int, int]:
    input_chars = sum(
        len(message["content"])
        for message in messages
        if message["role"] != "assistant"
    )
    assistant_chars = sum(
        len(message["content"])
        for message in messages
        if message["role"] == "assistant"
    )
    return input_chars, assistant_chars, input_chars + assistant_chars


def make_record(
    *,
    record_id: str,
    source: str,
    mix_axis: str,
    source_variant: str,
    source_revision: str,
    license_expression: str,
    usage_class: str,
    attribution_ids: Sequence[str],
    transformation_chain: Sequence[str],
    task: str,
    messages: list[dict[str, str]],
    stage: str,
    kind: str,
    origin: str,
    raw_hash: str,
    source_group_id: str,
    leakage_group: str,
    extra_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_chars, assistant_chars, total_chars = _message_lengths(messages)
    message_text = "\n".join(item["content"] for item in messages)
    meta = {
        "raw_hash": raw_hash,
        "source_group_id": source_group_id,
        "leakage_group_id": leakage_group,
        "message_sha256": sha256_json(messages),
        "input_chars": input_chars,
        "assistant_chars": assistant_chars,
        "total_chars": total_chars,
    }
    if extra_meta:
        meta.update(extra_meta)
    return {
        "id": record_id,
        "source": source,
        "mix_axis": mix_axis,
        "source_variant": source_variant,
        "source_revision": source_revision,
        "license_expression": license_expression,
        "usage_class": usage_class,
        "provenance_status": "verified",
        "attribution_ids": list(attribution_ids),
        "transformation_chain": list(transformation_chain),
        "domain": "saju" if source != "aihub_empathy" else "general_dialogue",
        "task": task,
        "messages": messages,
        "label": {
            "stage": stage,
            "kind": kind,
            "origin": origin,
            "human_review": "not_reviewed",
        },
        "quality_flags": {
            "parse_ok": True,
            "language_ok": KOREAN_PATTERN.search(message_text) is not None,
            "exact_duplicate": False,
            "translation_residue": False,
            "over_length": False,
        },
        "meta": meta,
    }


def _source_parquets(
    source_config: dict[str, Any], repo_root: Path, source: str
) -> list[tuple[Path, dict[str, Any]]]:
    root = source_root(source_config, repo_root, source)
    manifest = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    return [
        (root / item["path"], item)
        for item in manifest.get("files", [])
        if str(item.get("path", "")).endswith(".parquet")
    ]


def _sanitize_persona(text: Any, district: Any, province: Any) -> str:
    value = NAME_PATTERN.sub("이 사람은", normalize_text(text))
    for location in (normalize_text(district), normalize_text(province)):
        if location:
            value = value.replace(location, "거주 지역")
    return value


def build_nemotron_records(
    *,
    source_config: dict[str, Any],
    repo_root: Path,
    audit_policy: dict[str, Any],
    target_by_variant: dict[str, int],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = source_config["sources"]["nemotron_saju"]
    pattern_groups = {
        key.removeprefix("nemotron_"): _compile_patterns(
            audit_policy["safety_patterns"][key]
        )
        for key in (
            "nemotron_health",
            "nemotron_death_accident",
            "nemotron_certainty",
            "nemotron_financial_guarantee",
        )
    }
    heaps: dict[str, list[tuple[int, str, dict[str, Any]]]] = {
        variant: [] for variant in target_by_variant
    }
    counters: Counter[str] = Counter(
        {
            "excluded_invalid_age": 0,
            "excluded_policy_union": 0,
            "excluded_primary_replacement_character": 0,
            "excluded_primary_health": 0,
            "excluded_primary_death_accident": 0,
            "excluded_primary_certainty": 0,
            "excluded_primary_financial_guarantee": 0,
            "excluded_primary_ascii_word": 0,
            "matched_replacement_character": 0,
            "matched_health": 0,
            "matched_death_accident": 0,
            "matched_certainty": 0,
            "matched_financial_guarantee": 0,
            "matched_ascii_word": 0,
        }
    )
    connection = _duckdb_module().connect(database=":memory:")
    try:
        for path, manifest_item in _source_parquets(
            source_config, repo_root, "nemotron_saju"
        ):
            variant = str(manifest_item.get("source_variant", "unknown"))
            if variant not in heaps:
                continue
            cursor = connection.execute(
                "SELECT uuid, persona, professional_persona, occupation, age, "
                "education_level, marital_status, district, province, saju_pillars, "
                "saju_day_master, saju_elements, saju_elements_dominant, "
                "saju_elements_lacking, saju_sipsin, saju_narrative, "
                "saju_narrative_error FROM read_parquet(?)",
                [str(path)],
            )
            for row in _iter_rows(cursor):
                counters["rows_scanned"] += 1
                (
                    uuid,
                    persona,
                    professional,
                    occupation,
                    age,
                    education,
                    marital,
                    district,
                    province,
                    pillars_raw,
                    day_master,
                    elements_raw,
                    dominant,
                    lacking,
                    sipsin_raw,
                    narrative_raw,
                    narrative_error,
                ) = row
                if narrative_error is not None or not isinstance(uuid, str) or not uuid:
                    counters["excluded_structure"] += 1
                    continue
                try:
                    chart = canonical_chart_from_nemotron(pillars_raw)
                    pillars = json.loads(pillars_raw)
                    elements = json.loads(elements_raw)
                    sipsin = json.loads(sipsin_raw)
                    narrative = json.loads(narrative_raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    counters["excluded_structure"] += 1
                    continue
                if not isinstance(narrative, dict) or any(
                    not normalize_text(narrative.get(key)) for key, _ in NARRATIVE_ORDER
                ):
                    counters["excluded_structure"] += 1
                    continue
                narrative_text = "\n".join(
                    normalize_text(narrative[key]) for key, _ in NARRATIVE_ORDER
                )
                if isinstance(age, bool) or not isinstance(age, int) or not 19 <= age <= 99:
                    counters["excluded_invalid_age"] += 1
                    continue
                persona_text = _sanitize_persona(persona, district, province)
                professional_text = _sanitize_persona(professional, district, province)
                prompt_projection = {
                    "persona": persona_text,
                    "professional": professional_text,
                    "occupation": normalize_text(occupation),
                    "age_band": f"{age // 10 * 10}대",
                    "education": normalize_text(education),
                    "marital": normalize_text(marital),
                    "pillars": pillars,
                    "day_master": normalize_text(day_master),
                    "elements": elements,
                    "dominant": normalize_text(dominant),
                    "lacking": list(lacking or []),
                    "sipsin": sipsin,
                }
                rendered_input_values = "\n".join(
                    (
                        persona_text,
                        professional_text,
                        normalize_text(occupation),
                        normalize_text(education),
                        normalize_text(marital),
                        normalize_text(day_master),
                        normalize_text(dominant),
                        *(normalize_text(item) for item in (lacking or [])),
                        *(
                            normalize_text(value)
                            for pillar in sipsin.values()
                            for value in pillar.values()
                        ),
                    )
                )
                policy_text = f"{rendered_input_values}\n{narrative_text}"
                matches: list[str] = []
                if "\ufffd" in str(narrative_raw):
                    matches.append("replacement_character")
                matches.extend(
                    reason
                    for reason, patterns in pattern_groups.items()
                    if _contains_any(patterns, policy_text)
                )
                if ASCII_WORD_PATTERN.search(policy_text):
                    matches.append("ascii_word")
                if matches:
                    _record_policy_exclusion(counters, matches)
                    continue
                raw_projection = {
                    "uuid": uuid,
                    "prompt": prompt_projection,
                    "narrative": narrative,
                }
                rank = stable_rank(seed, "nemotron_saju", variant, uuid)
                _push_smallest(
                    heaps[variant],
                    target_by_variant[variant] * 2,
                    rank,
                    {
                        "uuid": uuid,
                        "variant": variant,
                        "chart": chart,
                        "prompt": prompt_projection,
                        "narrative": narrative,
                        "raw_hash": sha256_json(raw_projection),
                        "rank": rank,
                    },
                )
                counters["eligible_scanned"] += 1
    finally:
        connection.close()

    records: list[dict[str, Any]] = []
    used_charts: set[str] = set()
    used_messages: set[str] = set()
    selected_variants: Counter[str] = Counter()
    for variant in ("v6", "v7"):
        target = target_by_variant[variant]
        for candidate in _ordered_heap(heaps[variant]):
            if candidate["chart"] in used_charts:
                counters["excluded_duplicate_chart"] += 1
                continue
            prompt = candidate["prompt"]
            p = prompt["pillars"]
            pillar_text = " ".join(
                f"{name_ko}주 {p[name]['stem_hanja']}{p[name]['branch_hanja']}"
                for name, name_ko in zip(PILLAR_ORDER, ("년", "월", "일", "시"), strict=True)
            )
            user_text = (
                "다음 구조화 정보만 바탕으로 전통 명리 관점의 참고 해석을 해주세요.\n"
                f"인물 배경: {prompt['persona']}\n"
                f"직업·활동: {prompt['professional']}\n"
                f"기본 정보: {prompt['age_band']}, {prompt['occupation']}, "
                f"{prompt['education']}, {prompt['marital']}\n"
                f"사주 원국: {pillar_text}\n"
                f"일간: {prompt['day_master']}\n"
                f"오행 분포: {json.dumps(prompt['elements'], ensure_ascii=False, sort_keys=True)}\n"
                f"우세 오행: {prompt['dominant']}; 부족 오행: {', '.join(prompt['lacking']) or '없음'}\n"
                "십신: "
                + "; ".join(
                    f"{name_ko}주 천간 {prompt['sipsin'][name]['stem']}, "
                    f"지지 {prompt['sipsin'][name]['branch']}"
                    for name, name_ko in zip(
                        PILLAR_ORDER, ("년", "월", "일", "시"), strict=True
                    )
                )
            )
            sections = [
                f"{label}: {normalize_text(candidate['narrative'][key])}"
                for key, label in NARRATIVE_ORDER
            ]
            assistant_text = (
                "\n".join(sections)
                + "\n이 내용은 전통 명리의 문화·오락적 참고 해석이며 실제 성향, "
                "진로, 건강 또는 재정 결과를 확정하지 않습니다."
            )
            messages = [
                {
                    "role": "system",
                    "content": "제공된 구조화 정보에만 근거해 한국어로 설명하고, 전통 명리 해석을 사실이나 예측처럼 단정하지 마세요.",
                },
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ]
            message_hash = sha256_json(messages)
            if message_hash in used_messages:
                counters["excluded_exact_duplicate"] += 1
                continue
            used_messages.add(message_hash)
            used_charts.add(candidate["chart"])
            group_id = leakage_group_id("nemotron-uuid", candidate["uuid"])
            records.append(
                make_record(
                    record_id=f"nemotron_saju:{hashlib.sha256(candidate['uuid'].encode()).hexdigest()}",
                    source="nemotron_saju",
                    mix_axis="nemotron_saju",
                    source_variant=variant,
                    source_revision=source["revision"],
                    license_expression=source["license_expression"],
                    usage_class=source["usage_class"],
                    attribution_ids=(
                        "nvidia-nemotron-personas-korea",
                        "rayraykim-nemotron-personas-korea-saju",
                    ),
                    transformation_chain=(
                        "source_output",
                        "nfc_normalized",
                        "synthetic_identity_minimized",
                        "safety_filtered",
                        "fixed_disclaimer_appended",
                    ),
                    task="structured_saju_reading",
                    messages=messages,
                    stage="R0+A1",
                    kind="auto_validated_synthetic",
                    origin="source_output",
                    raw_hash=candidate["raw_hash"],
                    source_group_id=group_id,
                    leakage_group=leakage_group_id("chart", candidate["chart"]),
                    extra_meta={
                        "candidate_rank": candidate["rank"],
                        "chart_signature": candidate["chart"],
                    },
                )
            )
            selected_variants[variant] += 1
            if selected_variants[variant] == target:
                break
        if selected_variants[variant] != target:
            raise Phase2AuditError(
                f"Nemotron {variant} 적격 후보가 부족합니다: "
                f"{selected_variants[variant]}/{target}"
            )
    records.sort(key=lambda item: item["meta"]["candidate_rank"])
    return records, {
        "adapter_version": ADAPTER_SCHEMA_VERSION,
        "source_rows_scanned": counters["rows_scanned"],
        "eligible_rows_scanned": counters["eligible_scanned"],
        "selected_rows": len(records),
        "selected_variants": dict(sorted(selected_variants.items())),
        "filter_counts": dict(sorted(counters.items())),
    }


def expected_bazi_rules(facts: dict[str, Any]) -> set[str]:
    counts = facts["element_counts"]
    day_element = str(facts["day_master"]["element"])
    expected: set[str] = set()
    if int(counts[day_element]) >= 3:
        expected.add("day_master_strong")
    if int(counts[day_element]) <= 1:
        expected.add("day_master_weak")
    if int(counts[SUPPORTING_ELEMENT[day_element]]) >= 1:
        expected.add("dm_supported")
    if max(int(counts[element]) for element in ELEMENTS) >= 4:
        expected.add("dominant_element")
    if any(int(counts[element]) == 0 for element in ELEMENTS):
        expected.add("missing_elements")
    return expected


def _validate_bazi_facts(facts: Any) -> tuple[str, set[str]]:
    if not isinstance(facts, dict):
        raise TypeError("facts가 object가 아닙니다.")
    chart = canonical_chart_from_bazi(facts)
    pillars = facts.get("pillars")
    day_master = facts.get("day_master")
    counts = facts.get("element_counts")
    if not isinstance(pillars, dict) or not isinstance(day_master, dict):
        raise TypeError("pillars 또는 day_master가 없습니다.")
    if not isinstance(counts, dict) or set(counts) != set(ELEMENTS):
        raise ValueError("element_counts 구조가 다릅니다.")
    day = pillars.get("day")
    if not isinstance(day, dict):
        raise TypeError("day pillar가 없습니다.")
    if normalize_text(day.get("stem")).lower() != normalize_text(
        day_master.get("stem")
    ).lower() or normalize_text(day.get("stem_element")) != normalize_text(
        day_master.get("element")
    ):
        raise ValueError("day master가 일주와 다릅니다.")
    actual: Counter[str] = Counter()
    for pillar_name in PILLAR_ORDER:
        pillar = pillars.get(pillar_name)
        if not isinstance(pillar, dict):
            raise TypeError("pillar 구조가 다릅니다.")
        actual[normalize_text(pillar.get("stem_element"))] += 1
        actual[normalize_text(pillar.get("branch_element"))] += 1
    if sum(actual.values()) != 8 or any(
        actual[element] != int(counts[element]) for element in ELEMENTS
    ):
        raise ValueError("element_counts 재계산이 원천과 다릅니다.")
    return chart, expected_bazi_rules(facts)


def _bazi_pillars_ko(facts: dict[str, Any]) -> str:
    pillars = facts["pillars"]
    labels = ("년", "월", "일", "시")
    values: list[str] = []
    for pillar_name, label in zip(PILLAR_ORDER, labels, strict=True):
        pillar = pillars[pillar_name]
        stem = STEM_PINYIN[normalize_text(pillar["stem"]).lower()]
        branch = BRANCH_PINYIN[normalize_text(pillar["branch"]).lower()]
        values.append(f"{label}주 {stem}{branch}")
    return " ".join(values)


def _render_bazi_messages(
    facts: dict[str, Any],
    question_type: str,
    rule_ids: Sequence[str],
    language_bank: dict[str, Any],
) -> list[dict[str, str]]:
    counts = facts["element_counts"]
    counts_text = ", ".join(
        f"{ELEMENT_KO[element]} {int(counts[element])}" for element in ELEMENTS
    )
    day = facts["day_master"]
    day_stem = STEM_PINYIN[normalize_text(day["stem"]).lower()]
    polarity = POLARITY_KO.get(normalize_text(day["polarity"]).lower())
    if polarity is None:
        raise ValueError("지원하지 않는 BaZi 음양 극성입니다.")
    day_text = f"{day_stem}({ELEMENT_KO[str(day['element'])]}·{polarity})"
    user_text = (
        f"사주 원국: {_bazi_pillars_ko(facts)}\n"
        f"일간: {day_text}\n"
        f"오행 수치: {counts_text}\n"
        f"질문: {language_bank['questions'][question_type]}"
    )
    explanations = [language_bank["rule_explanations"][rule_id] for rule_id in rule_ids]
    missing = [ELEMENT_KO[element] for element in ELEMENTS if int(counts[element]) == 0]
    dominant = [
        ELEMENT_KO[element]
        for element in ELEMENTS
        if int(counts[element]) == max(int(counts[item]) for item in ELEMENTS)
    ]
    facts_summary = (
        f"입력에서 확인한 원국은 {_bazi_pillars_ko(facts)}이고, 일간은 {day_text}입니다. "
        f"오행 분포는 {counts_text}입니다."
    )
    if "dominant_element" in rule_ids:
        facts_summary += f" 가장 많이 나타난 오행은 {', '.join(dominant)}입니다."
    if "missing_elements" in rule_ids:
        facts_summary += f" 표면에 나타나지 않은 오행은 {', '.join(missing)}입니다."
    focus = {
        "career": "일과 진로는 단일 요소로 결정되지 않으므로, 이 구조는 선호와 경향을 살피는 참고 자료로만 볼 수 있습니다.",
        "element_balance": "오행의 수치 차이는 전통 체계 안의 분포 설명이며, 좋고 나쁨이나 보완 행동의 필요성을 곧바로 확정하지 않습니다.",
        "general_natal": "이 조합은 전통 체계 안에서 성향을 살피는 한 가지 관점일 뿐, 실제 성격이나 미래를 확정하지 않습니다.",
        "relationships": "대인관계와 연애 결과는 사주만으로 정해지지 않으므로, 관계의 성향을 살피는 참고로만 해석합니다.",
    }[question_type]
    rule_text = " ".join(f"규칙 {index + 1}: {text}" for index, text in enumerate(explanations))
    assistant_text = (
        f"{facts_summary} {rule_text} {focus} {language_bank['safety_disclaimer']}"
    )
    return [
        {"role": "system", "content": language_bank["system_prompt"]},
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]


def build_bazi_records(
    *,
    source_config: dict[str, Any],
    repo_root: Path,
    language_bank: dict[str, Any],
    target_rows: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if target_rows % len(QUESTION_TYPES):
        raise Phase2AuditError("BaZi staging_rows는 질문 유형 네 개의 배수여야 합니다.")
    target_groups = target_rows // len(QUESTION_TYPES)
    source = source_config["sources"]["bazi_sft"]
    paths = [str(path) for path, _ in _source_parquets(source_config, repo_root, "bazi_sft")]
    connection = _duckdb_module().connect(database=":memory:")
    heap: list[tuple[int, str, dict[str, Any]]] = []
    counters: Counter[str] = Counter()
    query = (
        "SELECT synthetic_id, list(example_id ORDER BY question_type), "
        "list(question_type ORDER BY question_type), list(facts ORDER BY question_type), "
        "list(retrieved_rules ORDER BY question_type), list(filename ORDER BY question_type) "
        "FROM read_parquet(?, filename=true) GROUP BY synthetic_id"
    )
    try:
        cursor = connection.execute(query, [paths])
        for synthetic_id, example_ids, qtypes, facts_list, rules_list, filenames in _iter_rows(cursor):
            counters["source_groups_scanned"] += 1
            counters["source_rows_scanned"] += len(qtypes or [])
            try:
                if sorted(qtypes) != sorted(QUESTION_TYPES) or len(set(qtypes)) != 4:
                    raise ValueError("질문 유형 네 개가 완전하지 않습니다.")
                charts_and_rules = [_validate_bazi_facts(facts) for facts in facts_list]
                charts = {item[0] for item in charts_and_rules}
                expected_sets = [item[1] for item in charts_and_rules]
                if len(charts) != 1 or any(facts != facts_list[0] for facts in facts_list):
                    raise ValueError("그룹 facts가 일치하지 않습니다.")
                got_sets = [
                    {str(rule["id"]) for rule in rules if isinstance(rule, dict)}
                    for rules in rules_list
                ]
                if any(got != expected for got, expected in zip(got_sets, expected_sets, strict=True)):
                    raise ValueError("retrieved_rules 조건이 재계산 결과와 다릅니다.")
                if any(set_ != got_sets[0] for set_ in got_sets):
                    raise ValueError("그룹별 retrieved_rules가 다릅니다.")
            except (KeyError, TypeError, ValueError):
                counters["excluded_validation"] += 1
                continue
            rank = stable_rank(seed, "bazi_sft", synthetic_id)
            _push_smallest(
                heap,
                target_groups * 2,
                rank,
                {
                    "synthetic_id": str(synthetic_id),
                    "example_ids": list(example_ids),
                    "qtypes": list(qtypes),
                    "facts": facts_list[0],
                    "rules": sorted(got_sets[0]),
                    "chart": next(iter(charts)),
                    "splits": sorted(
                        {
                            "validation"
                            if "validation" in str(filename).lower()
                            else "test"
                            if "test" in str(filename).lower()
                            else "train"
                            for filename in filenames
                        }
                    ),
                    "raw_hash": sha256_json(
                        {
                            "synthetic_id": synthetic_id,
                            "example_ids": example_ids,
                            "qtypes": qtypes,
                            "facts": facts_list,
                            "rules": rules_list,
                        }
                    ),
                    "rank": rank,
                },
            )
            counters["eligible_groups_scanned"] += 1
    finally:
        connection.close()

    selected_groups: list[dict[str, Any]] = []
    seen_charts: set[str] = set()
    for candidate in _ordered_heap(heap):
        if candidate["chart"] in seen_charts:
            counters["excluded_duplicate_chart"] += 1
            continue
        seen_charts.add(candidate["chart"])
        selected_groups.append(candidate)
        if len(selected_groups) == target_groups:
            break
    if len(selected_groups) != target_groups:
        raise Phase2AuditError(
            f"BaZi 적격 그룹이 부족합니다: {len(selected_groups)}/{target_groups}"
        )

    records: list[dict[str, Any]] = []
    seen_messages: set[str] = set()
    for candidate in selected_groups:
        source_group = leakage_group_id("bazi-synthetic", candidate["synthetic_id"])
        chart_group = leakage_group_id("chart", candidate["chart"])
        for question_type, example_id in zip(
            candidate["qtypes"], candidate["example_ids"], strict=True
        ):
            messages = _render_bazi_messages(
                candidate["facts"], question_type, candidate["rules"], language_bank
            )
            message_hash = sha256_json(messages)
            if message_hash in seen_messages:
                raise Phase2AuditError("BaZi 렌더링 결과에 exact duplicate가 있습니다.")
            seen_messages.add(message_hash)
            records.append(
                make_record(
                    record_id=f"bazi_sft:{hashlib.sha256(str(example_id).encode()).hexdigest()}",
                    source="bazi_sft",
                    mix_axis="bazi_sft",
                    source_variant="rule_matched",
                    source_revision=source["revision"],
                    license_expression=source["license_expression"],
                    usage_class=source["usage_class"],
                    attribution_ids=("amareshhebbar-bazi-sft",),
                    transformation_chain=(
                        "facts_only_projection",
                        "full_rule_revalidation",
                        "fixed_korean_template",
                        "source_english_response_excluded",
                    ),
                    task="grounded_rule_reading",
                    messages=messages,
                    stage="R0+A2",
                    kind="validated_rule_soft_gt",
                    origin="deterministic_rule_render",
                    raw_hash=candidate["raw_hash"],
                    source_group_id=source_group,
                    leakage_group=chart_group,
                    extra_meta={
                        "candidate_rank": candidate["rank"],
                        "chart_signature": candidate["chart"],
                        "question_type": question_type,
                        "validated_rule_ids": candidate["rules"],
                        "upstream_splits": candidate["splits"],
                    },
                )
            )
    records.sort(
        key=lambda item: (item["meta"]["candidate_rank"], item["meta"]["question_type"])
    )
    return records, {
        "adapter_version": ADAPTER_SCHEMA_VERSION,
        "full_rule_validation": True,
        "source_rows_scanned": counters["source_rows_scanned"],
        "source_groups_scanned": counters["source_groups_scanned"],
        "eligible_groups_scanned": counters["eligible_groups_scanned"],
        "selected_groups": len(selected_groups),
        "selected_rows": len(records),
        "question_type_counts": dict(sorted(Counter(item["meta"]["question_type"] for item in records).items())),
        "filter_counts": dict(sorted(counters.items())),
    }


def _zip_json_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    if isinstance(value, dict):
        lists = [item for item in value.values() if isinstance(item, list)]
        if len(lists) == 1 and all(isinstance(item, dict) for item in lists[0]):
            return lists[0]
    raise Phase2AuditError("AI Hub labeling JSON의 레코드 구조가 예상과 다릅니다.")


def _turn_texts(content: Any, prefix: str) -> list[str]:
    if not isinstance(content, dict):
        return []
    entries: list[tuple[int, str]] = []
    pattern = re.compile(rf"^{re.escape(prefix)}0*([1-9]\d*)$", re.IGNORECASE)
    for key, value in content.items():
        match = pattern.fullmatch(str(key))
        text = normalize_text(value)
        if match and text:
            entries.append((int(match.group(1)), text))
    return [value for _, value in sorted(entries)]


def _aihub_label_zips(
    source_config: dict[str, Any], repo_root: Path
) -> list[Path]:
    root = source_root(source_config, repo_root, "aihub_empathy")
    manifest = json.loads((root / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
    result = [
        root / item["path"]
        for item in manifest.get("files", [])
        if str(item.get("path", "")).lower().endswith(".zip")
        and "라벨링데이터" in str(item.get("path", ""))
    ]
    if len(result) != 2:
        raise Phase2AuditError("AI Hub labeling ZIP 두 개를 찾지 못했습니다.")
    return sorted(result)


def build_aihub_records(
    *,
    source_config: dict[str, Any],
    repo_root: Path,
    audit_policy: dict[str, Any],
    single_target: int,
    multiturn_target: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = source_config["sources"]["aihub_empathy"]
    pattern_groups = {
        "self_harm": _compile_patterns(
            audit_policy["safety_patterns"]["aihub_self_harm"]
        ),
        "clinical": _compile_patterns(
            audit_policy["safety_patterns"]["aihub_clinical"]
        ),
        "pii": PII_PATTERNS,
        "financial": (ADDITIONAL_UNSAFE_PATTERNS[0],),
        "legal": (ADDITIONAL_UNSAFE_PATTERNS[1],),
        "hate_or_violence": (ADDITIONAL_UNSAFE_PATTERNS[2],),
        "medical": (ADDITIONAL_UNSAFE_PATTERNS[3],),
    }
    best_by_group: dict[str, dict[str, Any]] = {}
    group_splits: dict[str, set[str]] = defaultdict(set)
    counters: Counter[str] = Counter(
        {
            "excluded_policy_union": 0,
            **{
                f"excluded_primary_{reason}": 0
                for reason in (*pattern_groups, "ascii_word")
            },
            **{
                f"matched_{reason}": 0
                for reason in (*pattern_groups, "ascii_word")
            },
        }
    )
    for path in _aihub_label_zips(source_config, repo_root):
        split = "validation" if "validation" in path.as_posix().lower() else "train"
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise Phase2AuditError("AI Hub labeling ZIP을 열 수 없습니다.") from exc
        with archive:
            members = [
                item
                for item in archive.infolist()
                if not item.is_dir() and item.filename.lower().endswith(".json")
            ]
            if len(members) != 1:
                raise Phase2AuditError("AI Hub labeling ZIP 내부 JSON 수가 다릅니다.")
            with archive.open(members[0]) as stream:
                document = json.load(stream)
            for row_index, record in enumerate(_zip_json_records(document)):
                counters["source_rows_scanned"] += 1
                talk = record.get("talk")
                profile = record.get("profile")
                if not isinstance(talk, dict) or not isinstance(profile, dict):
                    counters["excluded_structure"] += 1
                    continue
                talk_id = talk.get("id", {}).get("talk-id")
                if not isinstance(talk_id, (str, int)) or not str(talk_id).strip():
                    counters["excluded_structure"] += 1
                    continue
                human = _turn_texts(talk.get("content"), "HS")
                system = _turn_texts(talk.get("content"), "SS")
                pair_count = min(len(human), len(system))
                if pair_count < 2:
                    counters["excluded_structure"] += 1
                    continue
                counters[f"source_pair_count_{pair_count}"] += 1
                human = human[:pair_count]
                system = system[:pair_count]
                combined = "\n".join([*human, *system])
                matches = [
                    reason
                    for reason, patterns in pattern_groups.items()
                    if _contains_any(patterns, combined)
                ]
                if ASCII_WORD_PATTERN.search(combined):
                    matches.append("ascii_word")
                if matches:
                    _record_policy_exclusion(counters, matches)
                    continue
                if KOREAN_PATTERN.search(combined) is None:
                    counters["excluded_language"] += 1
                    continue
                talk_value = str(talk_id).strip()
                group_hash = hashlib.sha256(talk_value.encode("utf-8")).hexdigest()
                group_id = f"aihub-talk:{group_hash}"
                group_splits[group_id].add(split)
                record_rank = stable_rank(seed, "aihub-record", group_id, split, row_index)
                candidate = {
                    "group_id": group_id,
                    "human": human,
                    "system": system,
                    "emotion": normalize_text(
                        profile.get("emotion", {}).get("type")
                        if isinstance(profile.get("emotion"), dict)
                        else ""
                    ),
                    "raw_hash": sha256_json(record),
                    "record_rank": record_rank,
                    "source_pair_count": pair_count,
                }
                existing = best_by_group.get(group_id)
                if existing is None or record_rank < existing["record_rank"]:
                    best_by_group[group_id] = candidate
                counters["eligible_rows_scanned"] += 1

    ordered_groups = sorted(
        best_by_group.values(),
        key=lambda item: stable_rank(seed, "aihub-group", item["group_id"]),
    )
    required = single_target + multiturn_target
    if len(ordered_groups) < required:
        raise Phase2AuditError(
            f"AI Hub 적격 talk group이 부족합니다: {len(ordered_groups)}/{required}"
        )
    single_groups = ordered_groups[:single_target]
    multi_groups = ordered_groups[single_target:required]
    if {item["group_id"] for item in single_groups} & {
        item["group_id"] for item in multi_groups
    }:
        raise Phase2AuditError("AI Hub 단일턴·멀티턴 group이 겹칩니다.")

    records: list[dict[str, Any]] = []
    seen_messages: set[str] = set()
    for axis, selected in (
        ("aihub_empathy_single", single_groups),
        ("aihub_empathy_multiturn", multi_groups),
    ):
        for candidate in selected:
            if axis == "aihub_empathy_single":
                messages = [
                    {
                        "role": "system",
                        "content": "사용자의 말을 평가하거나 성급히 해결하려 하지 말고, 자연스러운 한국어로 공감하며 응답하세요.",
                    },
                    {"role": "user", "content": candidate["human"][0]},
                    {"role": "assistant", "content": candidate["system"][0]},
                ]
                stage = "H1"
                kind = "human_reference"
                task = "empathic_response"
            else:
                messages = [
                    {
                        "role": "system",
                        "content": "앞선 대화의 맥락을 유지하며 자연스럽고 공감적인 한국어로 다음 응답을 이어가세요.",
                    },
                    {"role": "user", "content": candidate["human"][0]},
                    {"role": "assistant", "content": candidate["system"][0]},
                    {"role": "user", "content": candidate["human"][1]},
                    {"role": "assistant", "content": candidate["system"][1]},
                ]
                stage = "H2"
                kind = "human_next_turn"
                task = "natural_multiturn_dialogue"
            message_hash = sha256_json(messages)
            if message_hash in seen_messages:
                counters["excluded_exact_duplicate"] += 1
                raise Phase2AuditError("AI Hub 렌더링 결과에 exact duplicate가 있습니다.")
            seen_messages.add(message_hash)
            rank = stable_rank(seed, axis, candidate["group_id"])
            records.append(
                make_record(
                    record_id=f"{axis}:{candidate['group_id'].split(':', 1)[1]}",
                    source="aihub_empathy",
                    mix_axis=axis,
                    source_variant="upstream_dialogue",
                    source_revision=source["release"],
                    license_expression=source["license_expression"],
                    usage_class=source["usage_class"],
                    attribution_ids=("aihub-dataset-86",),
                    transformation_chain=(
                        "grouped_across_upstream_splits",
                        "nfc_normalized",
                        "pii_and_crisis_excluded",
                        "turn_projection",
                    ),
                    task=task,
                    messages=messages,
                    stage=stage,
                    kind=kind,
                    origin="source_dialogue",
                    raw_hash=candidate["raw_hash"],
                    source_group_id=candidate["group_id"],
                    leakage_group=candidate["group_id"],
                    extra_meta={
                        "candidate_rank": rank,
                        "emotion_type": candidate["emotion"],
                        "source_pair_count": candidate["source_pair_count"],
                        "turn_projection": (
                            "first_pair"
                            if axis == "aihub_empathy_single"
                            else "first_two_pairs"
                        ),
                        "upstream_splits": sorted(group_splits[candidate["group_id"]]),
                    },
                )
            )
    records.sort(key=lambda item: (item["mix_axis"], item["meta"]["candidate_rank"]))
    return records, {
        "adapter_version": ADAPTER_SCHEMA_VERSION,
        "source_rows_scanned": counters["source_rows_scanned"],
        "eligible_rows_scanned": counters["eligible_rows_scanned"],
        "eligible_talk_groups": len(best_by_group),
        "selected_rows": len(records),
        "selected_groups": {
            "aihub_empathy_single": len(single_groups),
            "aihub_empathy_multiturn": len(multi_groups),
        },
        "cross_axis_group_overlap": 0,
        "selected_source_pair_count_distribution": {
            axis: dict(
                sorted(
                    Counter(
                        item["meta"]["source_pair_count"]
                        for item in records
                        if item["mix_axis"] == axis
                    ).items()
                )
            )
            for axis in ("aihub_empathy_single", "aihub_empathy_multiturn")
        },
        "filter_counts": dict(sorted(counters.items())),
    }


NAYIN_ELEMENTS = (
    "金", "火", "木", "土", "金",
    "火", "水", "土", "金", "木",
    "水", "土", "火", "木", "水",
    "金", "火", "木", "土", "金",
    "火", "水", "土", "金", "木",
    "水", "土", "火", "木", "水",
)
NAYIN_BY_JIAZI = {
    JIAZI[pair_index * 2 + offset]: element
    for pair_index, element in enumerate(NAYIN_ELEMENTS)
    for offset in (0, 1)
}
TRIAD_GROUPS = ("申子辰", "寅午戌", "巳酉丑", "亥卯未")
SEASON_GROUPS = {
    "春": "寅卯辰",
    "夏": "巳午未",
    "秋": "申酉戌",
    "冬": "亥子丑",
}


def _chart_tokens(chart: Sequence[str]) -> tuple[list[str], list[str]]:
    return [pillar[0] for pillar in chart], [pillar[1] for pillar in chart]


def _group_for(token: str, groups: Iterable[str]) -> str | None:
    return next((group for group in groups if token in group), None)


def _season_mapping_value(mapping: dict[str, Any], month_branch: str) -> Any:
    for key, value in mapping.items():
        for season, branches in SEASON_GROUPS.items():
            if key.startswith(season) and month_branch in branches:
                return value
    return None


def _token_present(expected: Any, chart: Sequence[str]) -> bool:
    stems, branches = _chart_tokens(chart)
    if isinstance(expected, list):
        return any(_token_present(item, chart) for item in expected)
    token = normalize_text(expected)
    if len(token) == 2 and token in JIAZI:
        return token in chart
    if token in STEMS:
        return token in stems
    if token in BRANCHES:
        return token in branches
    return False


def evaluate_yeji_rule(
    rule: dict[str, Any], chart: Sequence[str], *, sex: str
) -> bool:
    if len(chart) != 4 or any(pillar not in JIAZI for pillar in chart):
        raise ValueError("YEJI evaluator 입력은 유효한 60갑자 네 기둥이어야 합니다.")
    rule_id = int(rule["id"])
    condition = rule["condition"]
    stems, branches = _chart_tokens(chart)
    year_stem, _month_stem, day_stem, _ = stems
    year_branch, month_branch, day_branch, _ = branches

    if "valid_pillars" in condition:
        valid = set(condition["valid_pillars"])
        positions = (chart[2], chart[3]) if rule_id == 18 else (chart[2],)
        return any(pillar in valid for pillar in positions)
    if "valid_combos" in condition:
        expected = condition["valid_combos"].get(day_stem)
        return expected in branches
    if rule_id == 5:
        mapping = condition["mapping"]
        group = _group_for(month_branch, mapping)
        if group is None:
            return False
        specification = mapping[group]
        has_de = any(token in stems for token in specification["덕_천간"])
        has_pair = any(
            all(token in stems for token in pair)
            for pair in specification["수_천간_조합"]
        )
        return has_de and has_pair
    if rule_id in {10, 11}:
        expected = condition["mapping"][NAYIN_BY_JIAZI[chart[0]]]["간지"]
        return expected in chart
    if rule_id in {27, 28}:
        pair = {"戌", "亥"} if rule_id == 27 else {"辰", "巳"}
        return pair.issubset(set(branches))
    if rule_id == 36:
        mapping_key = "mapping_male" if sex == "남성" else "mapping_female"
        return condition[mapping_key][year_branch] in branches
    if rule_id == 37:
        day_index = JIAZI.index(chart[2])
        xun_start = (day_index // 10) * 10
        start_branch_index = BRANCHES.index(JIAZI[xun_start][1])
        void = {
            BRANCHES[(start_branch_index + 10) % 12],
            BRANCHES[(start_branch_index + 11) % 12],
        }
        return any(branch in void for index, branch in enumerate(branches) if index != 2)
    if rule_id == 38:
        if month_branch in "寅卯辰申酉戌":
            seasonal_targets = {"寅", "子"}
        else:
            seasonal_targets = {"卯", "未", "辰"}
        nayin_targets = {
            "金": {"午", "卯"},
            "木": {"午", "卯"},
            "水": {"酉", "戌"},
            "火": {"酉", "戌"},
            "土": {"辰", "巳"},
        }[NAYIN_BY_JIAZI[chart[0]]]
        targets = {day_branch, branches[3]}
        return bool(targets & seasonal_targets) or bool(targets & nayin_targets)

    mapping = condition.get("mapping")
    if not isinstance(mapping, dict):
        raise TypeError(f"지원하지 않는 YEJI condition입니다: {rule_id}")
    if rule_id in {1, 2, 8, 9, 13, 17, 39}:
        expected = [mapping[stem] for stem in {year_stem, day_stem}]
        return any(_token_present(item, chart) for item in expected)
    if rule_id in {3, 6, 19, 20, 31}:
        return _token_present(mapping[month_branch], chart)
    if rule_id in {4, 7}:
        group = _group_for(month_branch, mapping)
        return group is not None and _token_present(mapping[group], chart)
    if rule_id in {14, 15, 16, 34, 42, 44}:
        expected = []
        for branch in {year_branch, day_branch}:
            group = _group_for(branch, mapping)
            if group is not None:
                expected.append(mapping[group])
        return any(_token_present(item, chart) for item in expected)
    if rule_id in {21, 25, 29, 30}:
        return _token_present(mapping[day_stem], chart)
    if rule_id in {22, 47}:
        expected = _season_mapping_value(mapping, month_branch)
        return expected is not None and _token_present(expected, chart)
    if rule_id in {23, 24, 48, 49, 50}:
        return _token_present(mapping[year_branch], chart)
    if rule_id == 35:
        group = _group_for(year_branch, mapping)
        return group is not None and _token_present(mapping[group], chart)
    if rule_id in {40, 41}:
        group = _group_for(year_branch, mapping)
        return group is not None and _token_present(mapping[group], chart)
    raise ValueError(f"지원하지 않는 YEJI rule_id입니다: {rule_id}")


def _condition_summary(rule: dict[str, Any]) -> str:
    rule_id = int(rule["id"])
    if "valid_pillars" in rule["condition"]:
        return "원천에 열거된 유효 일주 또는 시주 목록과 정확히 대조합니다."
    if rule_id in {10, 11}:
        return "년주의 납음 오행을 계산한 뒤 원천에 지정된 간지와 정확히 대조합니다."
    if rule_id == 36:
        return "가정한 성별과 년지를 기준으로 원천의 지지 매핑을 대조합니다."
    if rule_id == 37:
        return "일주가 속한 60갑자 순의 공망 지지 두 개를 계산해 다른 지지와 대조합니다."
    if rule_id == 38:
        return "월지 계절 조건과 년주 납음 조건을 각각 계산하고 일지·시지와 대조합니다."
    if rule_id in {27, 28}:
        return "원천에 지정된 두 지지가 네 기둥에 함께 있는지 대조합니다."
    return "원천의 구조화 매핑에서 기준 천간·지지와 대상 간지의 일치 여부를 대조합니다."


def _calendar_unique_chart(
    *,
    seed: int,
    sequence: int,
    used: set[str],
    rule: dict[str, Any],
    case_type: str,
    desired: bool | None,
    calendar_backend: dict[str, Any],
) -> tuple[tuple[str, str, str, str], str, dict[str, Any], int]:
    try:
        from lunar_python import Solar
    except ImportError as exc:
        raise Phase2AuditError(
            "lunar-python이 없습니다. uv pip으로 requirements-data.txt를 설치하세요."
        ) from exc
    start = date.fromisoformat(calendar_backend["anchor_start"])
    end = date.fromisoformat(calendar_backend["anchor_end"])
    day_count = (end - start).days + 1
    hours = tuple(int(value) for value in calendar_backend["anchor_hours"])
    max_attempts = int(calendar_backend["max_attempts_per_case"])
    for attempt in range(1, max_attempts + 1):
        digest = hashlib.sha256(
            (
                f"{seed}|{calendar_backend['algorithm']}|{sequence}|"
                f"{rule['id']}|{case_type}|{attempt}"
            ).encode()
        ).digest()
        anchor_date = start + timedelta(
            days=int.from_bytes(digest[:8], "big") % day_count
        )
        anchor_hour = hours[digest[8] % len(hours)]
        sex = ("남성", "여성")[digest[9] & 1]
        eight_char = (
            Solar.fromYmdHms(
                anchor_date.year,
                anchor_date.month,
                anchor_date.day,
                anchor_hour,
                0,
                0,
            )
            .getLunar()
            .getEightChar()
        )
        chart = (
            eight_char.getYear(),
            eight_char.getMonth(),
            eight_char.getDay(),
            eight_char.getTime(),
        )
        if not calendar_relations_valid(chart):
            raise Phase2AuditError("달력 backend가 내부 정합성이 없는 명식을 반환했습니다.")
        signature = "".join(chart)
        if signature in used:
            continue
        if desired is None or evaluate_yeji_rule(rule, chart, sex=sex) is desired:
            used.add(signature)
            return (
                chart,
                sex,
                {
                    "date": anchor_date.isoformat(),
                    "hour": anchor_hour,
                    "minute": 0,
                    "second": 0,
                },
                attempt,
            )
    raise Phase2AuditError(f"YEJI rule {rule['id']} 검증 사례 생성에 실패했습니다.")


def _chart_ko(chart: Sequence[str], sex: str) -> str:
    labels = ("년주", "월주", "일주", "시주")
    return ", ".join(
        [*(f"{label} {pillar}" for label, pillar in zip(labels, chart, strict=True)), f"성별 가정 {sex}"]
    )


def _render_yeji_messages(
    rule: dict[str, Any],
    safe_meaning: str,
    chart: Sequence[str],
    sex: str,
    case_type: str,
    outcome: bool,
) -> list[dict[str, str]]:
    name = rule["name_ko"]
    chart_text = _chart_ko(chart, sex)
    condition = _condition_summary(rule)
    system = (
        "제공된 명식과 고정 규칙만으로 신살 조건을 판정하고, 전통 명리의 상징을 "
        "실제 사건이나 건강·재정·관계의 확정적 예측처럼 말하지 마세요."
    )
    if case_type == "definition":
        user = f"{chart_text}\n{name}의 전통 명리상 의미와 판단 조건을 구분해 설명해 주세요."
        assistant = (
            f"의미: {safe_meaning} 판단 조건: {condition} "
            "이 문항은 정의 확인용이며, 제시된 명식의 성립 여부나 실제 사건을 확정하지 않습니다."
        )
    elif case_type in {"positive", "negative"}:
        user = f"{chart_text}\n이 명식에서 {name} 조건이 성립하는지 고정 규칙으로 판정해 주세요."
        result = "성립합니다" if outcome else "성립하지 않습니다"
        assistant = (
            f"검증기 기준 판정은 ‘{result}’입니다. {condition} "
            f"{safe_meaning} 다만 이 판정은 전통 체계의 형식 조건일 뿐 실제 결과를 예측하지 않습니다."
        )
    else:
        wrong = "성립하지 않는다" if outcome else "성립한다"
        correct = "성립합니다" if outcome else "성립하지 않습니다"
        user = f"{chart_text}\n‘이 명식에는 {name}이 {wrong}’는 판정을 검토하고 틀렸다면 바로잡아 주세요."
        assistant = (
            f"제시된 판정은 틀렸습니다. 검증기 기준으로는 {name} 조건이 {correct}. "
            f"{condition} 이 형식 판정은 실제 사건이나 삶의 결과를 확정하지 않습니다."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]


def build_yeji_records(
    *,
    source_config: dict[str, Any],
    repo_root: Path,
    correction_manifest: dict[str, Any],
    language_bank: Sequence[dict[str, Any]],
    target_rows: int,
    seed: int,
    calendar_backend: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = source_config["sources"]["yeji_bazi_rules"]
    root = source_root(source_config, repo_root, "yeji_bazi_rules")
    rules_path = root / "rules/shensha_51.json"
    document = json.loads(rules_path.read_text(encoding="utf-8"))
    corrected, applied = apply_yeji_corrections(document, correction_manifest)
    rules = corrected.get("shensha_list")
    if not isinstance(rules, list) or len(rules) != 51:
        raise Phase2AuditError("YEJI 교정 후 규칙 수가 51개가 아닙니다.")
    meanings = {int(item["rule_id"]): item for item in language_bank}
    if [(int(rule["id"]), rule["name_ko"]) for rule in rules] != [
        (rule_id, meanings[rule_id]["name_ko"]) for rule_id in range(1, 52)
    ]:
        raise Phase2AuditError("YEJI 문구 은행 ID·규칙명이 원천과 다릅니다.")
    if target_rows < 51:
        raise Phase2AuditError("YEJI staging_rows는 규칙별 정의 51건보다 작을 수 없습니다.")
    corrected_ids = {int(item["rule_id"]) for item in applied}
    corrections_by_rule: dict[int, list[str]] = defaultdict(list)
    for item in applied:
        corrections_by_rule[int(item["rule_id"])].append(item["correction_id"])

    specs: list[tuple[int, str, bool | None]] = [
        (rule_id, "definition", None) for rule_id in range(1, 52)
    ]
    remaining = target_rows - len(specs)
    task_counts = {
        "positive": remaining // 3,
        "negative": remaining // 3,
        "correction": remaining // 3,
    }
    for task in ("positive", "negative", "correction")[: remaining % 3]:
        task_counts[task] += 1
    for case_type in ("positive", "negative", "correction"):
        for index in range(task_counts[case_type]):
            desired = True if case_type == "positive" else False if case_type == "negative" else index % 2 == 0
            specs.append((index % 51 + 1, case_type, desired))

    used_charts: set[str] = set()
    records: list[dict[str, Any]] = []
    evaluator_checks: Counter[str] = Counter()
    generation_attempts: list[int] = []
    for sequence, (rule_id, case_type, desired) in enumerate(specs):
        rule = rules[rule_id - 1]
        chart, sex, calendar_anchor, attempts = _calendar_unique_chart(
            seed=seed,
            sequence=sequence,
            used=used_charts,
            rule=rule,
            case_type=case_type,
            desired=desired,
            calendar_backend=calendar_backend,
        )
        generation_attempts.append(attempts)
        outcome = evaluate_yeji_rule(rule, chart, sex=sex)
        if desired is not None and outcome is not desired:
            raise Phase2AuditError("YEJI 생성 사례가 evaluator 목표와 다릅니다.")
        messages = _render_yeji_messages(
            rule,
            meanings[rule_id]["safe_meaning_ko"],
            chart,
            sex,
            case_type,
            outcome,
        )
        signature = "".join(chart)
        rank = stable_rank(seed, "yeji", rule_id, case_type, signature)
        status = "corrected_overlay" if rule_id in corrected_ids else "verified_exact"
        records.append(
            make_record(
                record_id=f"yeji_shensha_derived:{hashlib.sha256(f'{rule_id}|{case_type}|{signature}|{sequence}'.encode()).hexdigest()}",
                source="yeji_bazi_rules",
                mix_axis="yeji_shensha_derived",
                source_variant="evaluator-calendar-v1.1.0",
                source_revision=source["revision"],
                license_expression=source["license_expression"],
                usage_class=source["usage_class"],
                attribution_ids=("tellang-yeji-bazi-rules", "chxb-shensha"),
                transformation_chain=(
                    "source_rule_loaded",
                    *(f"correction:{item}" for item in corrections_by_rule[rule_id]),
                    "deterministic_calendar_anchor_generation",
                    "deterministic_rule_evaluation",
                    "fixed_korean_template",
                ),
                task="shensha_rule_qa",
                messages=messages,
                stage="D",
                kind="validated_rule_derived",
                origin="rule_evaluator",
                raw_hash=sha256_json(
                    {
                        "rule": rule,
                        "chart": chart,
                        "sex": sex,
                        "case_type": case_type,
                        "calendar_anchor": calendar_anchor,
                        "calendar_backend": calendar_backend["distribution"],
                        "calendar_backend_version": calendar_backend["version"],
                    }
                ),
                source_group_id=leakage_group_id(
                    "yeji-rule-chart", f"{rule_id}|{signature}"
                ),
                leakage_group=leakage_group_id("chart", signature),
                extra_meta={
                    "candidate_rank": rank,
                    "chart_signature": signature,
                    "rule_id": rule_id,
                    "rule_name_ko": rule["name_ko"],
                    "case_type": case_type,
                    "evaluator_outcome": outcome,
                    "evaluator_status": status,
                    "correction_ids": sorted(corrections_by_rule[rule_id]),
                    "calendar_anchor": calendar_anchor,
                    "calendar_backend": calendar_backend["distribution"],
                    "calendar_backend_version": calendar_backend["version"],
                    "calendar_generation_attempts": attempts,
                },
            )
        )
        evaluator_checks[status] += 1
    records.sort(key=lambda item: item["meta"]["candidate_rank"])
    return records, {
        "adapter_version": ADAPTER_SCHEMA_VERSION,
        "evaluator_version": "v1.0.0",
        "source_rule_count": len(rules),
        "selected_rows": len(records),
        "unique_chart_count": len(used_charts),
        "calendar_relation_valid_rows": sum(
            calendar_relations_valid(
                tuple(
                    item["meta"]["chart_signature"][index : index + 2]
                    for index in range(0, 8, 2)
                )
            )
            for item in records
        ),
        "calendar_backend": {
            key: calendar_backend[key]
            for key in (
                "distribution",
                "version",
                "artifact_sha256",
                "algorithm",
                "anchor_start",
                "anchor_end",
                "anchor_hours",
            )
        },
        "generation_attempts": {
            "max": max(generation_attempts),
            "mean": round(sum(generation_attempts) / len(generation_attempts), 6),
        },
        "case_type_counts": dict(sorted(Counter(item["meta"]["case_type"] for item in records).items())),
        "rule_coverage": len({item["meta"]["rule_id"] for item in records}),
        "evaluator_status_counts": dict(sorted(evaluator_checks.items())),
        "applied_correction_ids": sorted(item["correction_id"] for item in applied),
        "unsupported_rule_ids": [],
    }
