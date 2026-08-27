# Phase 2. 데이터 전처리·Split·혼합

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 1 완료 |
| 입력 | 고정 원본, source inventory, license manifest |
| 출력 | unified v1, holdout/core eval, 소스별 후보 순서, 감사 보고서 |
| 완료 Gate | 스키마·누수·중복·후보 여유분 검사 통과 |
| 웹 확인일 | 2026-08-27 |

## 목적

소스별 원문을 재현 가능한 공통 대화 스키마로 변환하고, 평가 누수를 먼저 차단한 뒤 1K·10K·20K baseline manifest를 만든다. 외부 원문을 곧바로 섞지 않고 허용된 변환 사슬과 안전성 Gate를 통과한 행만 학습 후보로 만든다.

## 공통 레코드 계약

```json
{
  "id": "source:original_id",
  "source": "nemotron_saju",
  "mix_axis": "nemotron_saju",
  "source_variant": "v6",
  "source_revision": "commit-or-version",
  "license_expression": "CC-BY-4.0",
  "usage_class": "train_allow",
  "provenance_status": "verified",
  "attribution_ids": ["nvidia-nemotron-korea", "rayraykim-nemotron-saju"],
  "transformation_chain": ["source_output", "nfc_normalized", "safety_filtered"],
  "domain": "saju",
  "task": "structured_saju_reading",
  "messages": [
    {"role": "system", "content": "주어진 태스크와 입력에 맞게 한국어로 답하세요."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "label": {
    "stage": "R0+A1",
    "kind": "auto_validated_synthetic",
    "origin": "source_output",
    "human_review": "not_reviewed"
  },
  "quality_flags": {
    "parse_ok": true,
    "language_ok": true,
    "exact_duplicate": false,
    "translation_residue": false,
    "over_length": false
  },
  "meta": {
    "raw_hash": "sha256...",
    "group_id": "...",
    "conversation_id": null,
    "chart_signature": null,
    "input_tokens": 0,
    "assistant_tokens": 0,
    "total_tokens": 0
  }
}
```

`id`, `source`, `mix_axis`, `source_revision`, `license_expression`, `usage_class`, `provenance_status`, `attribution_ids`, `transformation_chain`, `task`, `raw_hash`, `group_id`는 null을 허용하지 않는다. 원천을 나타내는 `source` enum은 `nemotron_saju`, `bazi_sft`, `aihub_empathy`, `yeji_bazi_rules` 네 값이다. 학습 할당을 나타내는 `mix_axis` enum은 `nemotron_saju`, `bazi_sft`, `aihub_empathy_single`, `aihub_empathy_multiturn`, `yeji_shensha_derived` 다섯 값이다. 원본 ID가 없으면 source 파일 SHA-256, 행 번호, 정규화 전 원문 SHA-256으로 결정론적 ID를 만든다.

## 태스크와 라벨 값

| task | 목적 |
|---|---|
| `structured_saju_reading` | 구조화 원국 기반 풀이 |
| `grounded_rule_reading` | 입력 사실과 명시 규칙에 근거한 풀이 |
| `shensha_rule_qa` | 검증된 신살 조건의 정의·판정·교정 |
| `empathic_response` | 단일 턴 공감 응답 |
| `natural_multiturn_dialogue` | 대화 문맥의 다음 발화 |

| 소스 | `label.kind` |
|---|---|
| Nemotron | `auto_validated_synthetic` |
| `bazi-sft` 파생본 | `validated_rule_soft_gt` |
| AI Hub #86 단일턴 파생 | `human_reference` |
| AI Hub #86 멀티턴 파생 | `human_next_turn` |
| YEJI 신살 규칙 파생본 | `validated_rule_derived` |

`label.stage`는 `R0+A1`, `R0+A2`, `H1`, `H2`, `D`만 쓴다. v1 학습 후보는 `R0+A1` 또는 규칙 검증을 통과한 `R0+A2`이며 가중치를 차등 적용하지 않는다.

## 공통 최소 전처리

1. 원문과 변환 후 원문의 SHA-256을 기록한다.
2. Unicode NFC 정규화, 비정상 제어문자 제거, 연속 공백 정리만 수행한다.
3. 필수 필드 누락, 빈 assistant, 파싱 실패, 심한 문자 깨짐을 제외한다.
4. source 내부 exact duplicate를 제거한 뒤 source 간 exact duplicate를 제거한다.
5. 한국어 정답이 완전히 중국어인 행을 제외한다.
6. 문자 길이와 원문 구조는 기록하되 모델 token 길이 필터는 Phase 4까지 미룬다.
7. 제외된 행은 같은 source·task·문자 길이 구간에서 보충 후보를 남긴다.
8. 변환 이유와 필터 코드를 행별 audit log에 남긴다.
9. 건강 진단·치료 지시, 투자 종목·수익 보장, 죽음·사고·이혼 단정, 차별적 성역할, 자해·위기 상황의 부적절한 조언을 제외한다.
10. 남은 해석은 전통문화·오락적 참고라는 불확실성 표현을 유지하고 입력에 없는 사실을 추가하지 않는다.

다음은 v1에서 수행하지 않는다.

- 자유 생성 LLM 답변 재작성 또는 모델 judge 단독 필터
- 한국식 용어·학파 통일
- 생년월일에서 원국을 생성하는 계산 데이터 제작
- 의미 기반 dedup
- 답변 길이 강제 통일
- 사람 Gold oversampling

## 소스 adapter 계약

### Nemotron

- 사용자 배경과 `saju_pillars`, 일간, 오행, 십신을 고정 템플릿으로 직렬화한다.
- assistant는 `saju_narrative`의 네 키를 `saju_summary`, `personality_reading`, `career_reading`, `lacking_element_advice` 순서로 직렬화한다.
- 네 키와 값, `saju_narrative_error == null`을 요구한다.
- v6/v7을 `source_variant`로 보존하고 문체 통계는 별도로 낸다.
- `birth_datetime_synth`는 provenance에만 보존하고 학습 prompt에 넣지 않는다.
- 건강·재물·직업의 확정적 예측이나 입력 사실과 맞지 않는 문장을 제외한다.
- `group_id = uuid + chart_signature`로 만든다.

### `bazi-sft`

- `facts.pillars`, `day_master`, `element_counts`, `retrieved_rules`, `question_type`만 파생 입력으로 사용하고 `birth_input`은 학습 prompt에서 제거한다.
- 네 기둥에서 일간·오행 수를 다시 계산하고, 각 `retrieved_rules` 조건이 실제 입력에 성립하는지 검증한다.
- 검증된 구조와 Apache 2.0 effect를 승인된 한국어 용어표·고정 template로 재렌더한다. 원본 English `response`의 자유 번역은 사용하지 않는다.
- 같은 `synthetic_id`의 네 question type은 하나의 group으로 묶고 train/eval을 넘지 않게 한다.
- calendar anchor 정확도가 필요한 주장, 날짜·지역 기반 계산, 검증되지 않은 규칙은 제외한다.

### YEJI 신살 규칙 파생본

- SHA-256이 고정된 `rules/shensha_51.json` 외의 YEJI 파일을 읽으면 실패한다.
- 51개 condition을 원천 `chxb/shensha.js`와 대조하고 허용 stem·branch·60갑자 집합, mapping 내부 일관성을 검사한다.
- `词馆`의 `壬卯`는 자동 교정하지 않고 독립 기준으로 `壬申`을 확인한 교정 기록과 attribution을 남긴 뒤 사용한다.
- 검증된 rule마다 정의, 조건 판정, 반례, 잘못된 판정 교정 태스크를 구조화 명식 조합으로 생성한다. 날짜·실존 인물은 사용하지 않는다.
- `meaning`은 soft reference로만 쓰며 죽음·질병·재난을 확정하는 문구는 중립적인 전통 해석 설명으로 제한한다.
- `group_id = rule_id + chart_signature`로 만들고 같은 rule/chart 조합은 split을 넘지 않는다.

### AI Hub #86 단일턴 파생

- 사용자 발화와 대응 시스템 응답을 한 예시로 만든다.
- 전화번호, 주민번호, 이메일 등 개인정보 패턴이 있으면 마스킹보다 행 제외를 우선한다.
- 감정 label은 meta에만 두고 입력에 정답 감정을 노출하지 않는다.
- 자해·자살·우울증 위기, 임상 진단·치료, 미성년자 민감정보가 포함된 행은 일반 공감 학습 후보에서 제외한다.
- 원본 상황/대화 ID를 `group_id`로 사용한다.

### AI Hub #86 멀티턴 파생

- #86의 동일 대화/상황 그룹 안에서 순서가 확인된 앞 1~4턴을 문맥으로 주고 다음 시스템 응답을 assistant로 만든다.
- 한 세션당 최대 2개 예시만 추출한다.
- 개인정보, 외부 저작물 장문, 비속어·혐오, 의료·법률·금융 조언을 제외한다.
- 세션 ID 단위로만 split하고 전체 세션 ID를 `group_id`로 사용한다.

## 평가셋 우선 분리

### Source holdout 500

다섯 `mix_axis`에서 group 단위로 100개씩 먼저 고정한다. 동일 group의 다른 행은 학습 후보에서 모두 제외하며, #86 단일턴과 멀티턴 축 사이에도 group ID가 겹치면 안 된다.

### Core Eval 200

| 영역 | 수량 |
|---|---:|
| 구조화 원국 풀이 | 45 |
| 근거 규칙 기반 풀이 | 35 |
| 입력 사실 모순·환각 | 35 |
| 신살 규칙 판정·교정 | 20 |
| 동일 명식 반복 질문 일관성 | 20 |
| 공감 응답 | 20 |
| 멀티턴 자연 대화 | 15 |
| 명식 누락 시 계산기 handoff | 5 |
| 일반 한국어 instruction 보존 | 5 |

Core Eval의 원문·정답·채점 정책을 별도 해시로 잠그고, 학습용 adapter가 읽는 경로 밖에 둔다.

## 혼합 후보 순서와 수량 계약

소스별 후보는 seed 42로 한 번만 섞어 결정론적 후보 순서를 만든다. 각 source는 MIX20 목표 수량보다 최소 20% 많은 적격 후보를 확보한다. 20행 구성 단위의 수량 계약은 다음과 같다.

```text
Nemotron                11
bazi-sft                 5
AI Hub #86 단일턴        2
AI Hub #86 멀티턴        1
YEJI 신살 규칙 파생본     1
```

1. 각 source의 문자 길이·variant 층을 유지하며 후보 ID 순서와 reserve pool을 고정한다.
2. 아래 MIX1K·MIX10·MIX20 수량표와 `MIX1K ⊂ MIX10 ⊂ MIX20` 계약을 machine-readable spec으로 저장한다.
3. Phase 4가 고정 tokenizer로 512/768/1024 token 적격 행을 계산한다.
4. Phase 4는 각 길이에서 후보 순서대로 MIX20 수량을 채우고, source별 앞 절반을 MIX10으로 고정한 뒤 MIX10의 앞 10%를 MIX1K로 고정한다.
5. 수량·포함 관계 검증 후 Phase 4가 각 manifest 행 순서를 seed 42로 별도 shuffle한다.

| 소스 | MIX1K | MIX10 | MIX20 |
|---|---:|---:|---:|
| Nemotron | 550 | 5,500 | 11,000 |
| `bazi-sft` 파생본 | 250 | 2,500 | 5,000 |
| AI Hub #86 단일턴 | 100 | 1,000 | 2,000 |
| AI Hub #86 멀티턴 | 50 | 500 | 1,000 |
| YEJI 신살 규칙 파생본 | 50 | 500 | 1,000 |

세 manifest는 위 비율과 수량을 정확히 지키고 MIX1K를 MIX10의 정확한 부분집합으로 고정한다.

## Phase 4로 넘길 길이 감사 입력

Phase 2에서는 tokenizer를 사용하지 않고 다음 문자·구조 통계를 넘긴다.

- source/task별 행 수와 input·assistant 문자 수
- 문자 길이 평균, 중앙값, p90, p95, 최대
- 멀티턴 수와 messages role 분포
- source별 목표 수량 대비 reserve pool 비율
- Nemotron v6/v7, `bazi-sft` question type, 신살 rule category별 문자 길이 분포

token 수, 512/768/1024 초과율, assistant loss token 비율과 special token 중복은 Phase 3의 tokenizer·template가 준비된 뒤 Phase 4에서 감사한다.

## 완료 Gate

- [ ] 모든 unified 행이 공통 스키마와 enum을 만족한다.
- [ ] source 내부·간 exact duplicate가 0건이다.
- [ ] train과 holdout/core eval 사이 group 중복이 0건이다.
- [ ] 각 source가 MIX20 목표보다 최소 20% 많은 적격 후보를 가진다.
- [ ] 후보 순서와 MIX1K⊂MIX10⊂MIX20 수량 계약을 seed 42로 고정했다.
- [ ] AI Hub 동일 group이 train/eval 또는 #86 단일턴/멀티턴 축에 중복 배정되지 않았다.
- [ ] #86에 구조적으로 적격인 멀티턴 group이 reserve를 포함해 1,200개 이상이다.
- [ ] `bazi-sft` 구조·규칙 검산과 한국어 재렌더가 모두 통과했다.
- [ ] 신살 51개 원천 대조·known issue 교정과 허용 파일 검사가 통과했다.
- [ ] 의료·투자·운명 단정·자해 위기 안전 필터의 제외 내역이 있다.
- [ ] 모든 제외 행에 이유가 있고 보충 후보 순서가 고정됐다.
- [ ] messages role과 문자 길이 통계가 Phase 4에 전달된다.

## 산출물

```text
data/unified/v1/<source>.jsonl
data/manifests/candidates/source_candidate_order_v1.jsonl
data/manifests/candidates/mix_contract_v1.json
data/eval/source_holdout_500_v1.jsonl
data/eval/core_eval_200.jsonl
data/reports/source_text_length_stats.json
data/reports/split_leakage_report.json
data/reports/filter_audit.jsonl
```

## 공식 자료

- [Datasets 4.7.0 데이터 로딩](https://huggingface.co/docs/datasets/v4.7.0/en/loading)
- [Datasets 4.7.0 데이터 처리](https://huggingface.co/docs/datasets/v4.7.0/en/process)
- [TRL 1.12.0 dataset formats](https://huggingface.co/docs/trl/v1.12.0/en/dataset_formats)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Datasets 4.7.0 로딩 문서 | Hub revision, local Parquet/JSON, data_files 로딩 방식 확인 |
| 2026-08-27 | TRL conversational format | `messages` 역할·내용 구조와 assistant mask 연계 확인 |
