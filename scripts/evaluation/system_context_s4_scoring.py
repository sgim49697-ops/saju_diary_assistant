# system_context_s4_scoring.py - S4 정보·모델 대응쌍과 공통 판정 가능 2×2 변화·비용을 분리 집계한다.

from collections import Counter, defaultdict

from scripts.evaluation.system_context_s4_models import ENGINES
from scripts.evaluation.system_context_scoring import aggregate as parent_aggregate
from scripts.evaluation.system_context_scoring import summary_numbers
from scripts.evaluation.system_context_scoring_v1_2 import SCORER_VERSION

VERDICTS = ("PASS", "FAIL", "UNSCORABLE")


def aggregate(entries):
    result = parent_aggregate(entries)
    for group in result["summaries"]:
        rows = [r for r in entries if r["engine"] == group["engine"] and r["arm"] == group["arm"] and (group["stratum"] == "all" or r["stratum"] == group["stratum"])]
        generated = [r for r in rows if r["status"] == "generated"]
        group["output_tokens"] = summary_numbers([r["telemetry"]["output_tokens"] for r in generated])
        group["stop_reasons"] = dict(Counter(r["telemetry"]["stop_reason"] for r in generated))
        group["peak_reserved_vram_mib"] = max((r["telemetry"]["peak_reserved_bytes"] / 1048576 for r in generated), default=0)
    cases = defaultdict(dict)
    for row in entries:
        key = (row["engine"], row["arm"])
        if key in cases[row["case_id"]]:
            raise ValueError("S4 집계 중복 요청")
        cases[row["case_id"]][key] = row
    transitions = defaultdict(Counter)
    quads = defaultdict(Counter)
    for rows in cases.values():
        if set(rows) != {(e, a) for e in ENGINES for a in ("C_FULL", "C_MIN")}:
            raise ValueError("S4 집계 2×2 누락")
        reference = next(iter(rows.values()))
        if any(r["parent_sha256"] != reference["parent_sha256"] or r["status"] != reference["status"] or r["stratum"] != reference["stratum"] for r in rows.values()):
            raise ValueError("S4 집계 부모/상태/층 불일치")
        if reference["status"] != "generated":
            if reference["status"] != "preblocked":
                raise ValueError("오류·미결 결과를 완료 집계로 발행할 수 없습니다.")
            continue
        expected_metrics = set(reference["scoring"]["metrics"])
        if any(set(r["scoring"]["metrics"]) != expected_metrics for r in rows.values()):
            raise ValueError("S4 대응쌍 지표 분모 불일치")
        for stratum in ("all", reference["stratum"]):
            for metric in expected_metrics:
                verdicts = {k: r["scoring"]["metrics"][metric] for k, r in rows.items()}
                if any(v not in VERDICTS for v in verdicts.values()):
                    raise ValueError("S4 알 수 없는 판정")
                for arm in ("C_FULL", "C_MIN"):
                    a, b = verdicts[("k0_instruct", arm)], verdicts[("kanana3b_instruct", arm)]
                    transitions[("k0_to_3b", arm, stratum, metric)][f"{a}_to_{b}"] += 1
                for engine in ENGINES:
                    a, b = verdicts[(engine, "C_FULL")], verdicts[(engine, "C_MIN")]
                    transitions[("full_to_min", engine, stratum, metric)][f"{a}_to_{b}"] += 1
                counts = quads[(stratum, metric)]
                counts["applicable_cases"] += 1
                if "UNSCORABLE" in verdicts.values():
                    counts["with_unscorable_cases"] += 1
                else:
                    counts["common_scorable_cases"] += 1
                    for arm, label in (("C_FULL", "full_model_pass_delta"), ("C_MIN", "minimum_model_pass_delta")):
                        counts[label] += int(verdicts[("kanana3b_instruct", arm)] == "PASS") - int(verdicts[("k0_instruct", arm)] == "PASS")
    result.update({
        "scorer_version": SCORER_VERSION, "instruction_arm": "P0", "candidate_selected": False,
        "fresh_all_conditions": True, "new_generations": sum(r["status"] == "generated" for r in entries),
        "paired_verdict_transitions": [
            {"comparison": comparison, "condition": condition, "stratum": stratum, "metric": metric,
             "counts": {f"{a}_to_{b}": counts[f"{a}_to_{b}"] for a in VERDICTS for b in VERDICTS}}
            for (comparison, condition, stratum, metric), counts in sorted(transitions.items())
        ],
        "interaction_common_scorable_quads": [
            {"stratum": stratum, "metric": metric,
             **{k: counts[k] for k in ("applicable_cases", "common_scorable_cases", "with_unscorable_cases", "full_model_pass_delta", "minimum_model_pass_delta")},
             "full_minus_minimum_model_gain": counts["full_model_pass_delta"] - counts["minimum_model_pass_delta"]}
            for (stratum, metric), counts in sorted(quads.items())
        ],
        "limitations": [
            "K0와 3B에 공통 P0/FULL·MIN·동결 이력을 적용했다. R16이나 P1 조합 비교가 아니다.",
            "모델 규모 외 가지치기·증류·attention 구조·학습 차이가 남아 순수 크기 인과 효과가 아니다.",
            "유한 scorer v1.2의 판정이며 전체 정확도·자연스러움·해석 의미 품질이 아니다.",
            "2×2 차이는 네 조건 모두 판정 가능한 사례만의 서술 통계다. 제외된 판정 불가도 공개한다.",
            "각 요청을 새 process에서 실행한다. 지연은 모델 로딩 포함이며 warm 서비스 성능이 아니다.",
            "저장/API는 K0 슬롯의 CPU 문자열 소비 재생이다. 3B 앱 연결·운영 브라우저 검증이 아니다.",
            "기존 v1.15 오차단을 동결하며 8A 정책은 실험에 혼입하지 않았다.",
            "48개는 소비된 개발 진단이다. 새 24문항·학습·운영 전환을 대신하지 않는다.",
        ],
    })
    return result
