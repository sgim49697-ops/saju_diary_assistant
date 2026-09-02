# 03. 모델 실제 효과 검증·MIX2K-v4 LoRA 교정·후속 데이터 계획

- 저장소: `sgim49697-ops/saju_diary_assistant`
- 기준 브랜치: `master`
- 기준 커밋: `9be784e4fb7f937865cdff66b95d725f61081b25`
- 기준 시각: 2026-09-02 22:34 KST
- 문서 최초 작성: 2026-09-02 11:36 KST
- 보존 기준선: `KI20-MIX-v2/run-1f5d732cae67`, `saju-runtime-release-v1.4.0-63dc8d398e90`
- 공통 상태: 기존 KI10·KI20·평가 split·sealed blind·v1.4 chart-only release는 불변 보존

## 문서 역할

이 문서는 `K0-INSTRUCT`, `KI10-MIX-v2`, `KI20-MIX-v2`의 실제 차이를 검증하고, production-like full runtime snapshot으로 `MIX2K-v4` K0 LoRA 교정을 먼저 수행한 뒤 후속 대규모 데이터를 판단하는 계획이다.

현재 정본은 [`implementation/plans/mix2k_v4_chart_day_lora.md`](implementation/plans/mix2k_v4_chart_day_lora.md)와 versioned JSON 계약이다. KI20은 삭제하지 않고 실패·비교 baseline으로 보존하며 첫 학습 후보는 K0-INSTRUCT 고정 revision의 LoRA다.

- 전체 제품 목표와 release 순서는 [`01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md`](01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md)를 따른다.
- chart/period/relation의 사실 정본은 [`02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md`](02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md)만 소유한다.
- 이 문서는 계산기를 다시 구현하지 않고, 승인 snapshot을 소비하는 모델 평가·데이터·학습만 담당한다.

---

## 1. 현재 모델 상태 판정

### 1.1 학습 자체는 정상 완료

`KI20-MIX-v2/run-1f5d732cae67`은:

- 20,000행
- Kanana 2 1.3B Instruct
- BF16 Full Fine-Tuning
- 전체 1,291,478,272 parameter 학습
- 1 epoch
- 2,500 optimizer step
- final reload 통과
- RTX 5070 Ti 16GiB
- peak VRAM 약 6.92GB
- 약 4,002초

로 완료됐다. 즉 “학습이 실패해서 아무 효과가 없다”는 상태는 아니다.

### 1.2 그러나 production 품질은 미승인

Phase 6 결정은 `AUTOMATED_REPAIR_REQUIRED`다. K0·KI10·KI20 모두 deterministic/rule/handoff/zero-tolerance Gate를 완전히 통과하지 못했고, 사주 해석의 의미 품질은 `not_measured`였다.

### 1.3 그래도 KI20의 구조적 효과는 이미 일부 보임

동일한 oracle Runtime context를 사용한 grounded dialogue 비교에서:

| 2048-token arm | KI20 | K0 |
|---|---:|---:|
| 기제공 필드 재질문 | 2/100 | 24/100 |
| 임의 네 기둥 생성 | 0/100 | 3/100 |
| prompt budget failure | 0/100 | 0/100 |
| non-empty | 100/100 | 100/100 |
| max-token hit | 0/100 | 10/100 |

이 결과는 KI20이 적어도 **사주 intake·handoff 문맥에서 일반 K0보다 훨씬 안정적으로 행동할 가능성**을 보여준다. 다만 이는 전문 통변의 의미 정확성을 증명하지 않는다.

### 1.4 긴 context 진단

KI20 oracle arm은 2,048·3,584 context 진단에서 자동 목표를 모두 통과했다. 3,584 arm은 max-token hit를 더 줄이는 이점이 있었지만, 이는 inference context 진단이다. 기존 Full FT의 `max_length=768`과 혼동하면 안 된다.

---

## 2. 기존 MIX20K-v2가 모델에 실제로 가르친 것

| 축 | 행 | assistant-token 비중 |
|---|---:|---:|
| Nemotron 사주 해석 | 6,800 | 58.080955% |
| bazi-sft 규칙 파생 | 4,000 | 25.218343% |
| AI Hub 단일턴 | 1,500 | 0.861964% |
| AI Hub 멀티턴 | 1,500 | 0.906623% |
| YEJI 신살 | 1,000 | 2.890489% |
| deterministic QA | 2,000 | 3.589891% |
| 사주 일기 브리지 | 3,200 | 8.451735% |

Nemotron+bazi가 supervised assistant token의 약 83.3%를 차지했다. 따라서 KI20은 가장 강하게 다음을 학습했을 가능성이 높다.

- 사주스러운 한국어 장문
- 오행·십신·신살 용어
- 구조화 명식을 설명하는 문체
- 상담형 말투

반대로 덜 학습한 것은:

- missing-only intake
- tool call
- session correction
- 상대 날짜
- chart/period snapshot grounding
- tool 실패·허위 완료 처리

이다.

새 데이터는 “사주 지식 문장을 더 많이 넣기”보다 **Runtime facts를 정확히 사용하고 workflow를 완결하는 능력**에 더 큰 비중을 둬야 한다.

---

## 3. 현재 MIX20K-v3.0.1 후보의 상태

현재 후보는 20,000행·15축으로 재구성되어 있고 strict tool trajectory 5,250행, model-visible result 2,200행, 5-turn trajectory 1,000행을 포함한다. 하지만 학습 후보로 바로 사용할 수 없다.

| 위험 | 현재 수치 |
|---|---:|
| exact duplicate 참여 행 | 2,035 |
| canonical engine recheck 대기 | 3,800 |
| domain review 대기 | 3,453 |
| expert review 대기 | 1,547 |
| empathy review 대기 | 700 |
| policy review 대기 | 375 |
| soft interpretation review 대기 | 4,400 |
| 현재 training candidate | 10,125/20,000 |
| diversity Gate | 미완료 |
| expert review | 미완료 |

특히 `verified_period_handling` 1,000행은 현재 period release가 blocked이므로, 문장만 고치는 것이 아니라 문서 02의 새 Runtime으로 tool result를 다시 생성해야 한다.

---

## 4. 모델 평가의 질문을 분리

하나의 “정확도”로 평가하지 않는다.

### Q1. KI20이 K0보다 사주 문맥을 잘 이해하는가?

- 용어 사용
- 질문 관련 fact 선택
- 일반론 남발 감소
- 사주 상담 문체

### Q2. Runtime facts를 더 잘 지키는가?

- 미제공 간지 생성
- 원국 모순
- 생시 미상 위반
- relation hallucination

### Q3. 앱 workflow를 잘 수행하는가?

- 빠진 항목만 질문
- 이미 받은 항목 재질문 방지
- tool call 완결
- 실패·blocked 결과 처리
- 후속 질문에서 chart/period 재사용

### Q4. 일반 대화 능력을 보존하는가?

- 감정 대화에서 불필요한 사주 요구
- 일반 한국어 지시 수행
- 짧고 자연스러운 응답

### Q5. 실제 전문 해석 품질이 개선됐는가?

- 전문가 framework 내부 일관성
- 근거가 명확한가
- 지나친 오행 단순화가 없는가
- 사용자 질문에 실제 도움이 되는가

Q5는 자동 regex만으로 판정하지 않고 blind review를 포함한다.

---

## 5. 평가 arm

| Arm | 모델 | Runtime | 목적 |
|---|---|---|---|
| `A` | K0 untouched | production-like full snapshot | 원본 자연성과 grounding 기준선 |
| `B` | KI20 | 동일 Runtime | 기존 실패·비교 baseline |
| `C` | K0 + LoRA r8 | 동일 Runtime | 저용량 교정 효과 |
| `D` | K0 + LoRA r16 | 동일 Runtime | primary 교정 후보 |
| `E` | K0 + LoRA r32 | 동일 Runtime | rank 증가 효과 |

핵심 비교:

```text
A ↔ B = K0 자연성과 기존 KI20 회귀 비교
A·B ↔ C·D·E = 같은 facts에서 LoRA rank별 grounding·자연성 변화
```

모든 paired arm은 동일 prompt, 동일 chat template, 동일 decoding, 동일 context snapshot을 사용한다.

---

## 6. 동결 평가 suite

Training target 생성 전에 production-like dev 200건을 별도 불변 build로 고정하고 teacher에게 target을 공개하지 않는다.

| 축 | 건수 |
|---|---:|
| schema literacy | 40 |
| natal explanation | 30 |
| natal + today | 50 |
| follow-up | 40 |
| state/tool | 20 |
| general/empathy | 20 |
| **합계** | **200** |

실제 regression `actual-chart-day-label-confusion-20260902`를 release blocker로 포함한다. 원국 전체 `戊辰·甲子·乙丑·壬午`, 일주 `乙丑`, 일간 `乙木`, period 연간지 `丙午`, 월간지 `丙申`, 일진 `己卯`를 구분해야 하며 제공되지 않은 통근·신강약·용신·합충을 만들면 실패다. 후속 “무슨 말인지 모르겠어 좀 풀어서 설명해줘”에도 같은 evidence를 유지하고 자연스럽게 설명해야 한다.

### 6.1 Final sealed set

새 final candidate가 생긴 뒤 한 번만 사용한다.

- 기존 sealed blind를 재사용·재열람하지 않음
- 새 version·새 fingerprint
- 모델·Runtime·prompt 모두 동결한 뒤 실행
- raw payload 비공개

---

## 7. 자동 평가 지표와 권장 Gate

| 지표 | Diagnostic 목표 | 최종 후보 목표 |
|---|---:|---:|
| schema field accuracy | 측정 | 개선·회귀 없음 |
| natal/period label confusion | 0 | 0 |
| unsupported fact rate | 0 | 0 |
| provided fact omission rate | 측정 | K0·KI20보다 개선 |
| natural explanation preference | 측정 | K0 대비 비열세 |
| follow-up evidence consistency | 100% | 100% |
| general conversation retention | 회귀 ≤5%p | 회귀 ≤3%p |
| repetitive/template response rate | 측정 | K0 대비 비열세 |
| false Saju injection | 0 | 0 |
| re-ask rate | ≤5% | ≤3% |

### 의미 평가 Gate

- KI20 또는 새 모델이 K0보다 blind pairwise에서 최소 60% 이상 선호
- “근거가 명확함” 평균 4/5 이상
- “아무 명식에나 붙는 일반론” 15% 이하
- 전문가가 명백한 개념 오류로 표시한 응답 5% 이하
- 계산 fact와 soft interpretation의 경계 위반 0

이 임계값은 첫 dev 결과를 본 뒤 바꾸지 않는다. 변경이 필요하면 새 evaluation version을 만든다.

---

## 8. 사람 Blind 평가

### 8.1 평가자 역할

| 역할 | 중점 |
|---|---|
| 명리 검수자 | 선택한 정책 framework 안에서 개념·근거 |
| 한국어/상담 검수자 | 자연스러움·공감·과장 |
| 제품 사용자 | 이해 가능성·재사용 의향·재미 |

### 8.2 표본

- chart pair 150
- period pair 100
- general dialogue pair 50
- 총 300 pair
- 모델 이름·순서 무작위
- chart/period facts는 별도 reference panel에 표시

### 8.3 점수

```text
근거 적합성
질문 적합성
한국어 자연스러움
과도한 단정 없음
일반론이 아닌 개인화
실제 앱에서의 유용성
```

실제 미래 사건이 맞았는지를 이 평가에서 주장하지 않는다.

---

## 9. MIX2K-v4 Diagnostic 정확한 구성

문서 02의 Day/Period/Relation Gate 통과 후 생성한다.

| 축 | 행 | 주된 목표 |
|---|---:|---|
| Structured fact/schema literacy | 300 | 원국 전체·일주와 period year/month/day 구분 |
| 원국 → 자연스러운 설명 | 300 | K0의 설명력 보존 |
| 원국 + 단일 일진 → 오늘 흐름 | 450 | full runtime fact 선택·grounding |
| 후속 질문 | 300 | 이전 evidence 유지·쉽게 설명 |
| intake/tool/state/correction | 250 | 상태·정정·도구 진실성 |
| 일반 한국어·공감 replay | 250 | 일반 대화 보존 |
| 불확실성/blocked/unsupported | 100 | 근거 밖 추론 차단 |
| HARD fact 짧은 QA | 50 | 구조 사실 정밀도 |
| **합계** | **2,000** |  |

### 2K 생성 원칙

- dashboard v1.11과 같은 full runtime snapshot을 사용하고 학습만 compact JSON으로 바꾸지 않음
- assistant 해석은 `[ALLOWED EVIDENCE]` 밖 구조 사실 0
- teacher 절반은 Claude 초안→Codex grounding 검수, 나머지는 Codex 초안→Claude 자연성 검수
- 반대 teacher `PASS`와 deterministic validator `PASS`를 모두 만족한 행만 채택
- 실질 답변은 최소 3개 완결 문장·3개 의미 있는 줄, 선호 최대 길이 제한 없음
- 동일 template family 최대 20행
- 동일 normalized target 최대 10행
- unique chart ≥1,000
- unique chart+period pair ≥500
- multi-turn conversation ≥600
- 5-turn trajectory ≥150
- 사람 검수 500행

---

## 10. MIX20K-v3.1 정확한 추천 구성

대운은 포함하지 않는다. 대운은 후속 `v3.2-daeun`에서 별도 구성한다.

| 축 | 행 | 행 비율 |
|---|---:|---:|
| intent_routing | 900 | 4.5% |
| birth_intake | 1,400 | 7.0% |
| calendar_time_edge | 700 | 3.5% |
| correction_conflict | 800 | 4.0% |
| chart_tool_call | 1,400 | 7.0% |
| chart_result_interpretation | 2,500 | 12.5% |
| day_period_request_resolution | 900 | 4.5% |
| period_tool_call_result | 1,200 | 6.0% |
| natal_period_relation_interpretation | 1,400 | 7.0% |
| domain_consultation | 2,000 | 10.0% |
| stateful_followup | 2,100 | 10.5% |
| non_saju_empathy | 1,200 | 6.0% |
| optin_saju_diary_bridge | 700 | 3.5% |
| completion_truthfulness | 500 | 2.5% |
| invalid_unverifiable_correction | 400 | 2.0% |
| hard_policy_qa | 900 | 4.5% |
| general_korean_replay | 1,000 | 5.0% |
| **합계** | **20,000** | **100%** |

### assistant-token 목표

행 비율이 아니라 supervised assistant token을 별도로 Gate한다.

| 그룹 | 목표 token 비중 |
|---|---:|
| routing·intake·correction·state | 22~26% |
| chart/period tool serialization | 15~19% |
| chart/period grounded interpretation | 28~33% |
| domain consultation soft interpretation | 11~15% |
| empathy·diary·general replay | 12~16% |
| policy·error·completion guards | 5~8% |

추가 제한:

- 단일 source가 assistant token의 20%를 초과하지 않음
- 단일 template cluster 2% 이하
- 장문 soft interpretation이 전체 token의 18%를 초과하지 않음
- tool/state trajectory가 전체 token의 최소 35%를 차지

---

## 11. 데이터 다양성·중복 Gate

현재 v3.0.1에서 확인된 multiplicity를 그대로 두지 않는다.

### 최소 기준

- unique normalized message signature ≥18,500
- unique chart input ≥8,000
- unique chart+period pair ≥3,000
- unique intent 표현 ≥300
- template family ≥250
- multi-turn conversation ≥5,000
- 5-turn trajectory ≥800
- exact duplicate 참여 행 ≤200
- 동일 assistant target 최대 20행
- fixed handoff처럼 의도된 문구는 별도 allowlist와 이유 필요

### split 원칙

- 동일 chart, persona seed, template family, dialogue parent를 train/dev/test에 나누지 않음
- period는 동일 chart+date family를 group split
- paraphrase cluster 단위 split
- sealed set과 content hash overlap 0

---

## 12. 사실·해석 라벨

| 라벨 | 자동 생성 | 사람 검수 | 학습 사용 |
|---|---:|---:|---:|
| `SOURCE_HARD_FACT` | 가능 | 표본 | 가능 |
| `PROFILE_DETERMINISTIC` | 가능 | 정책 표본 | 가능 |
| `FORECAST_PROFILE_DETERMINISTIC` | 가능 | 경계 표본 | 가능, warning 포함 |
| `RULE_GT` | 가능 | 조건·긍부정 표본 | 가능 |
| `DIALOGUE_REF` | 제한 | 필요 | 가능 |
| `GROUNDED_INTERPRETATION` | 초안 가능 | 필수 표본 | 가능 |
| `SOFT_INTERPRETATION` | 초안 가능 | 전문가/문체 검수 | 제한 |
| `HEURISTIC_ONLY` | 생성 가능 | 해당 없음 | Gold 금지 |

### 자동 Gold 금지

- 신강약 자동 점수
- 격국
- 용신
- 합충 관계 우선순위
- 색상·방향·소품 추천
- 미래 사건
- 대운 시작점 미승인 산식
- LLM이 출생정보에서 직접 만든 원국

---

## 13. 검수 4,000행 권장 배분

| 검수 큐 | 행 |
|---|---:|
| 명리 해석·grounding | 1,500 |
| workflow/tool/state | 900 |
| 기간·relation | 700 |
| 공감·일반 replay | 500 |
| 정책·오류·안전 | 400 |
| **합계** | **4,000** |

모든 tool result는 사람 표본과 무관하게 자동 canonical 검사를 전수 통과해야 한다.

---

## 14. 학습 arm

### T0. 학습 전 비교

- K0 vs KI20 semantic A/B
- 새 Runtime에서 B0/B1 비교
- 이 결과가 기존 FT의 실제 가치 기준선

### T1. MIX2K-v4 K0 LoRA

- 시작점: `kakaocorp/kanana-2-1.3b-instruct` 고정 revision `bf4786aa2a1908adce942d53976270132732f720`
- 1 epoch
- rank: r8, r16(primary), r32
- `target_modules=all-linear`, `use_rslora=true`, `bias=none`, `lora_dropout=0.05`
- 1차 learning rate `5e-5`
- 입력·출력 각각 4,096 token 안전 상한
- 학습 `max_length`는 768을 금지하고 pinned tokenizer 전수 audit 후 truncation 없는 최소 허용값 선택
- assistant-only loss
- 기존 chat template hash 유지
- 10K/20K 실행 금지 상태

### T2. Context·projection ablation

완성 target 전수 audit에서 2,048 token 초과 행이 20건 또는 1%보다 많으면 자동 상향하지 않는다. 먼저 audit-only provenance를 model-visible context에서 제외 가능한지, production serving에도 동일 projection을 적용할 수 있는지 별도 A/B로 검토한다. 학습과 serving 형식은 같은 실험 안에서 일치시킨다.

### T3. MIX10K-v3.1

- 시작점: K0
- 약 1,250 optimizer step
- T1 Gate를 모두 통과한 경우만 실행

### T4. MIX20K-v3.1

- 시작점: K0
- 2,500 optimizer step
- T3가 KI20과 K0를 모두 유의하게 이긴 경우만 실행

KI20에서 이어학습은 첫 production 후보가 아니라 별도 ablation으로만 고려한다.

---

## 15. Training preflight

`MIX2K-v4`의 pinned Kanana tokenizer·현재 chat template 전수 preflight를 적용한다.

필수 0건 조건:

- over max length
- zero assistant mask
- pre-last-user supervised token
- missing assistant EOS
- chat serialization mismatch
- tool round-trip error
- dev prompt overlap
- sealed hash overlap
- provisional tool result
- Runtime version mismatch
- unsupported relation fact
- period year/month/day label confusion
- input 4,096 token 또는 completion 4,096 token 초과

Tool-use SFT row는:

- conversation messages
- model tool call
- `tool` role result
- 사용 가능한 tools JSON schema

를 함께 가져야 한다.

---

## 16. Chat template·serving 원칙

- 기존 모델과 학습 checkpoint의 `chat_template.jinja` hash를 유지한다.
- training은 `add_generation_prompt=false`에 해당하는 형식으로 직렬화한다.
- inference는 generation prompt와 tool parser가 학습 형식과 일치해야 한다.
- SGLang primary parser와 vLLM secondary parser의 AST round-trip을 각각 확인한다.
- 현재 Kanana 2 1.3B 공식 model card의 긴 context 지원을 곧바로 학습 길이 승인으로 해석하지 않는다.
- 모델 architecture capability, 실제 Full FT memory, 데이터의 실제 문맥 길이는 별개의 Gate다.

---

## 17. 모델 승격 조건

### 2K → 10K

- 모든 zero-tolerance 0
- KI20보다 reask·tool completion 개선
- 일반 대화 회귀 ≤5%p
- semantic blind에서 KI20 대비 최소 동률

### 10K → 20K

- chart/period semantic 선호 ≥60%
- tool AST ≥97%
- relation hallucination 0
- non-saju unnecessary intake ≤2%
- 3개 seed 중 2개 이상 일관된 개선 또는 고정 seed 재현성 증명

### 20K → 제한 canary

- Runtime+Model end-to-end 300건 통과
- human blind review 통과
- 새로운 sealed evaluation 단회 통과
- 기존 KI20·K0 comparator 결과 보존
- production promotion은 별도 명시 승인

---

## 18. 보고서 구조

```text
data/reports/saju_model_semantic/
├── k0-vs-ki20-chart/v1.0.0/{eval_id}/
├── k0-vs-ki20-period/v1.0.0/{eval_id}/
├── mix2k-v3.1/v1.0.0/{eval_id}/
├── mix10k-v3.1/v1.0.0/{eval_id}/
└── mix20k-v3.1/v1.0.0/{eval_id}/
```

각 build:

```text
aggregate.json
build_manifest.json
thresholds.json
pairwise_summary.json
review_summary.json
```

원시 사용자 입력·출생정보·모델 출력은 private root에만 둔다.

---

## 19. Codex 실행 지시

1. 동결 dev 200건을 teacher 생성 전에 확정하고 K0·KI20·LoRA r8/r16/r32를 동일 Runtime·prompt·generation config로 비교한다.
2. 기존 Phase 6 sealed payload를 다시 열지 않는다.
3. 기존 KI20 checkpoint와 training summary를 수정하지 않는다.
4. Runtime이 제공하지 않은 통근·신강약·격국·용신·관계 계산을 Gold로 생성하지 않는다.
5. 모든 데이터 통계는 행 비율과 assistant-token 비율을 함께 낸다.
6. 동일 prompt·chat template·decoding을 model arm마다 유지한다.
7. 현재 v3.0.1의 2,035 duplicate 참여 행을 그대로 학습하지 않는다.
8. 2K LoRA diagnostic을 먼저 만들고, Gate 통과 전 Full FT와 10K·20K를 금지한다.
9. 사람 blind 평가 입력에서 모델 이름과 순서를 숨긴다.
10. 실제 실행 결과가 생길 때만 본 문서의 수치·상태를 갱신하고 작은 commit으로 남긴다.

---

## 진행 기록

### 2026-09-02 — MIX2K-v4 LoRA 실행 계약 반영

- 작업 요약: production-like full runtime 기반 2,000행 spec과 독립 dev 200건, 양방향 teacher 검수, K0 LoRA r8/r16/r32 및 5-arm 평가 계약을 반영했다.
- 변경 범위: KI20을 비교 baseline으로 보존하고 Full FT·768 우선 계획을 폐기했다. 입력·출력 각 4,096 안전 상한과 최소 3문장·3줄 목표를 명시했다.
- 검증: versioned spec·evaluation·LoRA 계약 검증, Ruff, 관련 unittest 85건(환경 의존 2건 skip), 양방향 teacher pilot 2/2를 통과했다.
- 남은 작업: 전체 2,000행 생성·교차검수, assistant target token/mask/EOS/leakage 전수 audit, GPU 1 epoch 학습과 5-arm 평가는 미실행이다.

---

## 20. 내부 근거 파일

- `data/reports/saju_1b_baseline/phase5-runs/v1.2.0/KI20-MIX-v2/run-1f5d732cae67/training_summary.json`
- `data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2/decision.md`
- `configs/model_versions/saju_1b_baseline/phase6-technical-evaluation-v1.0.0.json`
- `data/reports/saju_1b_baseline/grounded-dialogue-rescore/v0.1.2/eval-562c07d0e2e6/aggregate.json`
- `data/reports/saju_1b_baseline/grounded-dialogue-context/v0.1.1/eval-56d1357560d5/aggregate.json`
- `configs/data_versions/saju_1b_baseline/mix20k-v3-repair-v1.0.0.json`
- `data/reports/saju_1b_baseline/mix20k-v3-intake/v3.0.0/intake-99c0b48231d6/repair_summary.json`
- `configs/data_versions/saju_1b_baseline/mix20k-v3.1-preflight-v1.0.0.json`
- `configs/chat_templates/kanana2_sft.jinja`

## 21. 외부 참고

- Kanana 2 1.3B Instruct model card: https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct
- Kanana-2 공식 저장소: https://github.com/kakao/kanana-2
- Hugging Face TRL SFT Trainer: https://huggingface.co/docs/trl/sft_trainer
- Hugging Face chat templates: https://huggingface.co/docs/transformers/chat_templating
- Hugging Face TRL chat templates: https://huggingface.co/docs/trl/chat_templates
