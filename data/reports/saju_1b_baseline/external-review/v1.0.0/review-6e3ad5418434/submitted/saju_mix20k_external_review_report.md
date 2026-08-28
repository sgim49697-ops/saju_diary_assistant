# 사주 MIX20K 학습 데이터·GitHub 외부 검수 보고서

## 1. 판정

**최종 권고: `fix_then_recheck`**

현재 데이터는 다음 두 용도를 구분해야 한다.

- **학습 파이프라인과 1.3B Full FT가 실제로 도는지 확인하는 소규모 raw diagnostic:** 사용 가능
- **사주 해석 품질을 비교하는 공식 10K·20K baseline:** 핵심 오류를 고친 뒤 사용 권장
- **전문 사주 assistant 또는 광고 앱 production candidate:** 현 상태 그대로는 부적합

즉 데이터가 전부 쓸모없는 것은 아니다. 구조·무결성·재현성은 상당히 좋지만, 현재 정답이 가르치는 행동은 **전문 명리 추론보다 persona 확인, 표면 오행 재서술, 신살 판정, 반복 disclaimer** 쪽에 더 가깝다.

## 2. 실제 검수 범위

- 첨부 ZIP SHA-256과 별도 `.sha256` 일치 확인
- ZIP 내부 `SHA256SUMS.txt` 전 파일 일치 확인
- 외부 공개 가능 본문 **17,000행 전수 기계 검사**
- source별 token decile 100행씩 **총 300행 의미 표본**
- AI Hub 공감대화 **3,000행은 본문 미제공**이므로 집계와 GitHub 파이프라인만 검토
- GitHub `sgim49697-ops/saju_diary_assistant`의 데이터 계약, 소스 고정, 전처리, preflight, Phase 5 계획 검토
- 명리학자에 의한 학파별 전문 판정은 수행하지 않음

외부 candidate 17K의 `messages`와 trainer 직전 `training_external_17k.jsonl`은 **17,000행 모두 완전히 일치**했다. JSONL 파싱, role 순서, 빈 content, NFC, 제어문자도 전부 정상이다.

## 3. 가장 중요한 발견: 행 비율과 실제 학습 비율이 다르다

Assistant-only loss이므로 모델 행동에 직접 작용하는 것은 행 수보다 **assistant 토큰 수**다.

| 소스 | 행 수 | 행 비중 | 총 토큰 | Assistant 토큰 | Assistant loss 비중 |
|---|---:|---:|---:|---:|---:|
| nemotron_saju | 11,000 | 55.00% | 5,868,413 | 2,648,397 | 71.80% |
| bazi_sft | 5,000 | 25.00% | 1,630,774 | 930,794 | 25.23% |
| yeji_bazi_rules | 1,000 | 5.00% | 160,523 | 67,786 | 1.84% |
| aihub_empathy | 3,000 | 15.00% | 232,884 | 41,625 | 1.13% |

공감대화는 행 기준 15%지만 loss 토큰 기준 **약 1.13%**다. 반대로 Nemotron은 행 55%인데 assistant loss의 **약 71.80%**를 차지한다. 현재 recipe로 학습하면 자연스러운 공감대화보다 Nemotron의 장문 4단 구성, persona 확인, 오행 보완 조언을 훨씬 강하게 학습할 가능성이 높다.

## 4. 소스별 평가

### 4.1 Nemotron Saju 11K

**장점**

- 11,000개 명식이 모두 고유하고, 4주·일간·표면 오행 수치가 내부적으로 일치한다.
- 구조화된 사주 정보와 한국어 장문 풀이를 연결하는 초기 SFT 재료로는 유용하다.
- 입력 길이와 출력 형식이 1.3B 모델에 현실적이다.

**핵심 문제**

1. **정답에만 존재하는 이름**
   - 11,000행 중 **9,254행(84.13%)**에서 assistant가 입력에 없는 실명을 생성한다.
   - v7은 8,800행 중 8,714행으로 거의 전부다.
   - 전처리 chain에는 `synthetic_identity_minimized`가 적혀 있지만 입력만 이름을 지우고 정답은 남긴 것으로 보인다.
   - 이 상태로 학습하면 실제 앱에서 사용자가 주지 않은 이름을 환각할 수 있다.

2. **persona를 정답으로 다시 확인하는 지름길**
   - 직업·성격·생활사가 user에 이미 상세히 주어지고, assistant는 이를 “사주와 일치한다”고 재서술한다.
   - persona 확인 표현이 최소 5,282행, 직업 일치·부합 표현이 최소 4,614행에서 검출됐다.
   - 이는 명식을 보고 성향을 추론하는 데이터가 아니라 **주어진 소개문을 사주 언어로 합리화하는 데이터**가 되기 쉽다.

3. **runtime 정책과 지지 십신 충돌 가능성**
   - Nemotron은 지지 자체의 오행·음양으로 지지 십신을 만든다.
   - 앞서 검토한 `ssaju`는 지장간 정기를 기준으로 지지 십신을 만든다.
   - ssaju를 앱 runtime으로 쓰면 11,000행 중 **8,563행**, 총 **13,808개 필드**가 달라진다. 주로 子·巳·午·亥다.
   - 어느 쪽이 절대 정답이라는 문제가 아니라, **한 모델 안에서 정책이 둘이면 안 된다.**

4. **오행 보완 조언의 논리적 결함**
   - `부족 오행: 없음`인 3,330행 중 3,085행에도 보완·색상·방향 권고가 들어간다.
   - 최소 193행은 우세 오행 자체를 다시 “보완”하라고 말한다.
   - 누락 오행이 있는데도 “균형 잡힘”이라고 표현한 사례도 최소 152행이다.

5. **중국어·번역 잔재**
   - `녹색系的인`, `표현欲`, `火氣` 등 허용된 명리 한자 외 잔재가 **775행**에서 검출됐다.
   - 하지만 현재 `translation_residue` flag는 모두 false다.

**판정:** 데이터 전체 폐기보다는 **자동 수리 후 핵심 소스로 사용**하는 편이 낫다. 다만 11K 그대로 55%를 주는 것은 비추천이다.

### 4.2 bazi-sft 파생 5K

**검증 결과**

- 4주, 일간, 음양, 표면 오행 수치는 5,000행 모두 일치했다.
- 그러나 실제 다양성은 **1,250개 고유 명식 × 동일한 4개 질문**이다.
- 질문은 career / element balance / general / relationships 각각 1,250개다.
- 답변은 upstream 영어 응답을 사용하지 않고 자체 고정 한국어 template로 다시 만든 데이터다.

**학습 가치**

이 데이터가 잘 가르치는 것:

- 입력 사실을 복사·확인하기
- 표면 오행 개수 세기
- 간단한 규칙 조건을 빠뜨리지 않기
- 단정하지 않는 안전 문체

이 데이터가 거의 가르치지 못하는 것:

- 월령·계절성
- 통근·투간
- 지장간
- 십신 간 상호작용
- 합충형파해의 우선순위
- 격국·용신 정책
- 대운·세운 종합
- career와 relationship을 실제로 다르게 해석하는 과정

또한 답변 문자량의 약 **20.28%**가 모든 행에 똑같이 붙는 disclaimer다. 따라서 `전문 지식 데이터 5K`가 아니라 **사실 grounding과 안전 template 데이터 1,250 chart/4 task**로 보는 것이 정확하다.

**판정:** 10K raw baseline에 일부 유지 가능하지만, 현재 25% loss 비중은 높다. 1K~2.5K 정도를 유지하고 나머지는 근거형 QA로 교체하는 편이 낫다.

### 4.3 YEJI 신살 파생 1K

**장점**

- 51종 신살 조건을 deterministic evaluator로 판정한다.
- positive, negative, 판정 교정 문제를 섞어 단순 암기보다 조건 확인을 유도한다.
- upstream 오류를 별도 correction manifest로 관리한다.

**문제**

- 1,000행의 assistant 정답은 exact 기준 **221종**뿐이다.
- `검증기 기준 판정은…` 문구를 과도하게 학습할 수 있다.
- user 문장에 `도화이`, `천의이`, `금여이` 등 조사 오류가 **102건** 있다.
- 신살은 사주 전체 해석의 보조 축인데, 이 데이터만 보면 모델이 신살을 지나치게 중요한 전문 지식으로 오인할 수 있다.
- 사고·출혈·질병·상실 같은 표현은 사용자 앱에서는 더 중립적으로 렌더링할 필요가 있다.

**판정:** 규칙 QA로는 유지하되, 사용자용 상담 출력과 validator 출력은 분리해야 한다.

### 4.4 AI Hub 공감대화 3K

본문이 없으므로 다음은 확인하지 못했다.

- 실제 대화 자연스러움
- 문맥상 적절한 공감
- PII·위기·자해 문구 누락
- single-turn과 multiturn의 대화 연결 품질
- 정답이 지나치게 짧거나 상투적인지

확인 가능한 장점은 다음이다.

- 2,000 single-turn + 1,000 multiturn
- parser/language/length/exact duplicate 내부 flag 통과
- GitHub는 `talk-id`로 upstream split을 다시 묶어 group-first 분리하도록 설계했다.
- 원문을 Git에 올리지 않고 접근키·원본을 보호한다.

하지만 3K 전체 assistant 토큰이 **41,625개**, 평균 약 **13.9토큰**뿐이다. 이 상태에서 1 epoch 혼합 SFT를 하면 공감 행동은 사실상 미세한 regularizer 수준이다.

**권고**

- 로컬에서 single 100 + multiturn 100 이상을 사람이 직접 검수
- source-balanced sampling 또는 별도 style stage 적용
- 단순 oversampling만 10배 하면 3K를 반복 암기할 수 있으므로, 사주 사실 + 사용자 감정 + 짧은 공감 답변을 연결한 **app bridge 1K~3K**를 추가

## 5. 정답 라벨을 어떻게 고쳐야 하는가

### 반드시 지켜야 할 원칙

1. **정답에 쓰인 모든 사실은 입력에도 존재하거나 deterministic engine으로 검증 가능해야 한다.**
2. 정답에만 이름·생년월일·직업 세부정보가 들어가면 안 된다.
3. `facts`, `applied_rules`, `final_answer`를 내부 스키마에서 분리한다.
4. hard fact와 해석 soft label을 같은 신뢰도로 취급하지 않는다.
5. 학파·계산 정책을 metadata로 고정한다.

권장 label tier:

| tier | 의미 | 예 |
|---|---|---|
| `HARD_GT` | 코드로 검증되는 사실 | 4주, 일간, 오행, 십신, 합충 |
| `RULE_GT` | 고정한 학파·정책에 따른 규칙 적용 | 특정 신살, 선택한 신강약 policy |
| `SOFT_INTERPRETATION` | 합성·번역된 해석문 | Nemotron, GPT 재작성 |
| `HUMAN_GOLD` | 사람이 검수·수정 | 앱 핵심 답변 |
| `STYLE_REFERENCE` | 자연스러운 공감 말투 | AI Hub |

학습용 messages에는 최종 답만 넣더라도 내부 원본은 다음 구조를 유지하는 편이 좋다.

```json
{
  "facts": { "...": "..." },
  "policy": {
    "branch_tengod": "hidden_stem_main",
    "strength_method": "..."
  },
  "applied_rules": ["..."],
  "answer": "...",
  "label_tier": "SOFT_INTERPRETATION"
}
```

## 6. 가장 먼저 적용할 자동 수정

### P0: 10K를 돌리기 전에 반드시

1. Nemotron assistant에서 target-only 이름·정확한 생년월일 제거
2. `ssaju`를 쓸지 포함해 지지 십신 policy 하나로 통일
3. 중국어 잔재 775행 교정 또는 제외
4. YEJI 조사 오류 102행 교정
5. source별 **assistant token 비율** 리포트와 Gate 추가
6. 공감대화 로컬 표본 검수

### P1: 20K 전에

1. Nemotron persona-aware와 chart-only task 분리
2. `부족 없음`·우세 오행 보완 충돌 수정
3. 동일 disclaimer를 system/UI로 이동
4. bazi template 5K 중 일부를 실제 규칙형 해석으로 재작성
5. ssaju의 hard fields만 이용한 deterministic QA 1K~2K 추가
6. 사주+감정+일기 입력을 연결한 app bridge 1K~3K 추가

### P2: 이후 50K·100K

- 월령·통근·지장간·십신·합충 우선순위
- 대운·세운 조합
- 학파·정책별 분리
- 반례와 잘못된 풀이 교정
- 한국어 사용자 선호 pairwise 데이터
- 실제 앱 feedback은 RAG 우선, 충분히 누적된 뒤 adapter 학습

## 7. 권장 10K·20K 재구성

### `MIX10K-v1.1` 권장안

| 축 | 수량 | 비고 |
|---|---:|---|
| Nemotron 정제 chart/context | 4,000 | 이름·날짜 제거, persona shortcut 축 분리 |
| bazi grounding 개선본 | 2,000 | 500 unique chart × 4 task 또는 더 다양한 질문 |
| AI Hub 공감대화 | 2,000 | token-balanced sampler 적용 |
| YEJI 신살 QA 교정본 | 500 | 사용자용/validator용 분리 |
| ssaju hard-GT QA | 500 | 4주·십신·관계 등 검증 가능한 것만 |
| app bridge | 1,000 | 사주 facts + 감정/일기 + 짧은 답변 |
| **합계** | **10,000** | |

### `MIX20K-v1.1` 권장안

| 축 | 수량 | 비고 |
|---|---:|---|
| Nemotron 정제본 | 7,000 | 장문 편향 억제 |
| bazi grounding/규칙형 | 4,000 | 단순 template 비중 축소 |
| AI Hub 공감대화 | 3,000 | 별도 style stage 또는 가중 sampler |
| YEJI 신살 QA | 1,000 | 문법 교정 |
| ssaju hard-GT QA | 2,000 | 약한 격국·용신 휴리스틱은 제외 |
| app bridge | 3,000 | 실제 서비스 입력·출력 형태 |
| **합계** | **20,000** | |

행 수를 이렇게 잡더라도 최종 Gate는 assistant token 비율로 둬야 한다. 예를 들어 공감+app bridge를 합쳐 최소 10~15%의 assistant loss exposure를 목표로 두는 편이 낫다.

## 8. 학습 파이프라인 권고

### 빠른 baseline

- 현재 raw 데이터로 전체 10K·20K를 바로 소모하기보다, P0 자동 수정 후 1K/2K diagnostic
- 문제가 없으면 독립 10K run
- 10K 평가 후 20K를 결정
- 10K와 20K는 같은 Instruct snapshot에서 독립 시작하는 현재 계약 유지

### 권장 설정 변경

현재 BF16 Full FT, 768, micro 1, grad accumulation 8, 8-bit optimizer는 기술적으로 통과했다. 이 부분은 유지해도 된다.

추가 권장:

- small held-out eval을 250 step마다 실행
- source별 loss/token count 로깅
- source-balanced batch 또는 deterministic interleaving
- task tag를 실제 message에 넣기
- disclaimer repetition rate, target-only entity rate, runtime fact contradiction rate를 checkpoint 평가에 포함
- raw manifest가 source별로 묶여 있으므로 trainer shuffle이 실제 활성인지 run manifest에서 확인

## 9. GitHub 레포 평가

### 잘한 부분

- source revision·파일 크기·SHA-256 고정
- AI Hub 원본·키·checkpoint Git 제외
- upstream AI Hub split 중복을 발견하고 group-first 재분리
- candidate와 trainer projection 계약 분리
- 5070 Ti에서 BF16 full parameter, 8-bit optimizer, checkpoint resume·reload까지 실제 검증
- 모델·데이터 라이선스와 서비스 배포 경계를 문서화

### 보완할 부분

1. 현재 `canonical`은 **기술 Gate 통과**이지 **데이터 품질 인증**이 아니다.
2. Phase 5 실제 10K·20K 학습과 사람 명리 검수는 아직 수행되지 않았다.
3. semantic lint가 부족해 이름 leakage, 중국어 잔재, 정책 충돌을 놓쳤다.
4. 공개 레포인데 GitHub metadata상 루트 license가 지정되지 않았다.
5. 루트 README, `THIRD_PARTY_NOTICES.md`, `DATA_CARD.md`, `KNOWN_LIMITATIONS.md`를 추가하는 편이 좋다.
6. `configs/saju_policy.json`을 만들어 다음을 하나의 계약으로 고정해야 한다.
   - 지지 십신 기준
   - 절입·야자시 기준
   - 지장간 정책
   - 신강약·격국·용신 사용 여부
   - 신살 사용 범위
7. preflight를 `technical_gate`와 `semantic_gate`로 분리한다.

권장 신규 검사:

```text
target_only_entity_leakage
foreign_cjk_residue
runtime_policy_consistency
source_assistant_token_share
template_repetition
persona_shortcut
particle_generation
disclaimer_share
app_task_coverage
```

## 10. 최종 결론

이 데이터는 **잘 관리된 실험 패키지**이지만, 아직 **잘 만들어진 사주 전문 학습 데이터**라고 보기는 어렵다.

가장 큰 문제는 데이터 수량이 아니라 다음 세 가지다.

1. Nemotron이 실제 loss의 대부분을 차지하면서 persona·직업을 사주와 맞다고 확인하는 행동을 가르친다.
2. 공감대화 3K는 행 수와 달리 assistant loss 비중이 1.13%뿐이다.
3. runtime 계산 정책, 특히 지지 십신을 통일하지 않으면 모델 입력과 앱 계산 결과가 충돌한다.

P0 수정은 대부분 자동화 가능하고, raw 철학을 훼손하는 대규모 수동 정제가 아니다. 따라서 **P0만 반영한 뒤 10K baseline을 돌리고, 결과를 보고 20K v1.1로 확장**하는 것이 가장 비용 대비 효과가 좋다.
