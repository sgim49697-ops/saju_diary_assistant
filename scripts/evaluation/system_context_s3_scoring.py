# system_context_s3_scoring.py - 동결 scorer v1.1의 P0→P1 대응쌍·분모·비용을 공개 집계한다.

from collections import Counter, defaultdict
from copy import deepcopy

from scripts.evaluation.system_context_scoring import aggregate as parent_aggregate
from scripts.evaluation.system_context_scoring import summary_numbers
from scripts.evaluation.system_context_scoring_v1_1 import SCORER_VERSION


def aggregate(entries):
    translated = deepcopy(entries)
    for row in translated:
        row["arm"] = {"P0": "C_FULL", "P1": "C_MIN"}[row["arm"]]
    result = parent_aggregate(translated)
    for group in result["summaries"]:
        group["arm"] = {"C_FULL": "P0", "C_MIN": "P1"}[group["arm"]]
        rows = [r for r in entries if r["arm"] == group["arm"] and (group["stratum"] == "all" or r["stratum"] == group["stratum"])]
        generated = [r for r in rows if r["status"] == "generated"]
        group["output_tokens"] = summary_numbers([r["telemetry"]["output_tokens"] for r in generated])
        group["stop_reasons"] = dict(Counter(r["telemetry"]["stop_reason"] for r in generated))
        group["peak_reserved_vram_mib"] = max((r["telemetry"]["peak_reserved_bytes"] / 1048576 for r in generated), default=0)
    result["paired_p0_to_p1"] = result.pop("paired_full_to_min")
    pairs = defaultdict(dict)
    transitions = defaultdict(Counter)
    for entry in entries:
        pairs[entry["case_id"]][entry["arm"]] = entry
    for pair in pairs.values():
        if set(pair) != {"P0", "P1"}:
            raise ValueError("S3 대응쌍 누락")
        left, right = pair["P0"], pair["P1"]
        if left["status"] != right["status"] or left["parent_sha256"] != right["parent_sha256"]:
            raise ValueError("S3 대응쌍 상태/부모 불일치")
        before = left.get("scoring", {}).get("metrics", {})
        after = right.get("scoring", {}).get("metrics", {})
        if set(before) != set(after):
            raise ValueError("S3 대응쌍 적용 지표 불일치")
        for metric in before:
            for stratum in ("all", left["stratum"]):
                transitions[(stratum, metric)][f"{before[metric]}_to_{after[metric]}"] += 1
    result["paired_verdict_transitions"] = [
        {"stratum": stratum, "metric": metric, "counts": {f"{a}_to_{b}": values[f"{a}_to_{b}"] for a in ("PASS", "FAIL", "UNSCORABLE") for b in ("PASS", "FAIL", "UNSCORABLE")}}
        for (stratum, metric), values in sorted(transitions.items())
    ]
    result["scorer_version"] = SCORER_VERSION
    result["context_arm"] = "C_FULL"
    result["new_generations"] = sum(r["status"] == "generated" for r in entries)
    result["fresh_both_arms"] = True
    result["candidate_selected"] = False
    result["limitations"] = [
        "동일 R16·C_FULL·동결 부모 이력의 지시문 묶음 비교이며 개별 문장 효과가 아니다.",
        "P1은 bound profile과 formatter 안내 두 구간의 묶음 하나다. 비연결 intake 입력은 동일하다.",
        "v1.15의 기존 오차단도 동결한다. 8A 라우팅·현재 운영 KI20과의 비교가 아니다.",
        "날짜 질문의 원국·일진 각각 한 사실 요구와 단순 조회의 충돌은 사후 완화하지 않았다.",
        "유한 자동 계약의 UNSCORABLE을 실패나 성공으로 치환하지 않는다. 자연스러움·의미 품질은 미측정이다.",
        "48개는 소비된 개발 진단이다. S4 크기 비교·새 질문 확인·학습·배포를 대신하지 않는다.",
        "출력 소비는 실제 저장/API 함수의 CPU 재생이다. 운영 브라우저 실행은 아니다.",
    ]
    return result
