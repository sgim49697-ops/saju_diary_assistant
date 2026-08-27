# Phase 6. 사후 평가·사람 검수·v1 결정

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 5 완료 |
| 입력 | K0·K10·K20 모델, 고정 eval, Run log |
| 출력 | 자동·사람 평가, 400건 검수, 다음 단계 결정 기록 |
| 완료 Gate | K10·K20 비교와 50K/v1 Lite 결정 승인 |
| 웹 확인일 | 2026-08-27 |

## 목적

K10과 K20을 동일 조건에서 평가해 데이터량 증가의 효과와 실패 유형을 분리한다. 모델 출력은 새 정답으로 자동 승격하지 않고, 사람 검수와 규칙 검증을 거쳐 다음 데이터 버전을 결정한다.

## 평가 대상과 생성 설정

| 모델 | 역할 |
|---|---|
| `K0-BASE` | 학습 전 시작점 |
| `K0-INSTRUCT` | 공식 Instruct 비교 기준 |
| `K10-MIX-v0-RAW-NC` | 10K baseline |
| `K20-MIX-v0-RAW-NC` | 20K baseline |

모든 모델에 동일 prompt, chat template, EOS와 아래 greedy 설정을 적용한다.

```yaml
do_sample: false
num_beams: 1
max_new_tokens: 512
```

`do_sample=false`일 때 temperature/top-p로 차이를 만들지 않는다. 입력 순서와 모델 표시명은 사람 평가용 export에서 무작위로 가린다.

## 자동 평가

| 지표 | 대상·계산 |
|---|---|
| 정상 생성률 | 비어 있지 않고 decode 오류·무한 반복 없이 EOS/상한에서 종료한 비율 |
| 한국어 출력률 | 문자 언어 비율과 완전 중국어·영어 문장 수 |
| 태스크 혼동률 | 공감 질문에 사주 풀이, 계산 질문에 장문 상담 등 rule 위반 비율 |
| JSON 파싱률 | 구조화 JSON을 요구한 문항 중 schema parse 성공 비율 |
| 입력 사실 위반률 | 입력에 없는 일간·천간·지지·십신을 확정 언급한 비율 |
| 계산 정확도 | 고정 계산 문항의 네 기둥 exact match; 한국식 검산 전에는 참고값 |
| 길이 준수 | task별 설정한 출력 길이 구간 안에 든 비율 |
| 반복률 | n-gram·문장 내부 반복과 서로 다른 질문 간 동일 응답 비율 |
| 일반 능력 보존 | 고정 한국어 instruction 10문항의 성공률 |

초기 필수 Gate는 다음과 같다.

```text
정상 생성률             >= 98%
공감↔사주 태스크 혼동    <= 5%
구조화 JSON 파싱률       >= 90%
명백한 입력 사실 위반    <= 10%
중국어 문장 혼입         <= 3%
```

평가 스크립트 버전, prompt hash, generation config와 raw 출력은 모델별로 보존한다.

## 사람 평가 100문항

Core Eval에서 영역 비율을 유지한 100문항을 고정하고 K10·K20 출력 순서를 문항별로 무작위화한다. 평가자는 모델 이름을 보지 않는다.

### 평가 항목

- 질문 적합성
- 입력 정보 활용
- 사주 용어의 근거 있는 사용
- 한국어 자연스러움
- 공감의 과장·훈계 여부
- 반복·템플릿성
- 동일 명식 응답의 앞뒤 일관성
- 전체 선호: A / B / 동률 / 둘 다 실패

K20의 사람 선호율은 `K20 승 / (K10 승 + K20 승)`으로 계산한다. 동률과 둘 다 실패는 분모에서 제외하되 별도로 보고한다.

## 400건 사람 검수

K10·K20 오류를 확인한 뒤 학습 원본에서 다음 방식으로 400개를 고정한다.

| 추출 방식 | 수량 |
|---|---:|
| 소스별 무작위 | 180 |
| 모델이 크게 틀린 예시 | 120 |
| 높은 training loss 예시 | 50 |
| 유사 질문·상충 답변 | 50 |

각 행에 다음 label을 기록한다.

```text
판정: KEEP / EDIT / DROP
입력 사실 반영: 0 / 1 / 2
사주 내부 논리: 0 / 1 / 2 / 판단불가
한국어 자연스러움: 1~5
질문 적합성: 1~5
공감·대화 자연스러움: 1~5
중국어·번역체 잔재: 없음 / 경미 / 심함
과도한 단정: 0 / 1
답변 반복·템플릿성: 0 / 1
학파·정책 의존: 없음 / 있음 / 불명
수정 정답: 선택 입력
검수 메모: 자유 입력
```

`KEEP→H1`, 사람이 수정한 `EDIT→H2`, `DROP→D`로 승격한다. 모델 출력은 비교·오류 mining에만 쓰며 검수 없이 정답으로 넣지 않는다.

## 다음 단계 결정 규칙

### 50K 확장 후보

다음을 모두 만족할 때만 50K 확장을 선택한다.

- K20이 자동평가 필수 Gate를 모두 통과
- K20이 K10 대비 핵심 자동 지표에서 중대한 회귀 없음
- 동률 제외 사람 선호율에서 K20이 60% 이상
- 400건 검수에서 특정 source의 `DROP`이 10%를 넘지 않음
- 라이선스·데이터 접근 범위가 50K 확장에도 유효

이 결정은 50K 학습 승인이 아니라 다음 정본 작성 승인이다.

### v1 Lite 우선

위 조건 중 하나라도 실패하거나 K10·K20 차이가 작으면 같은 20K의 정제 효과를 먼저 본다.

- `DROP` 제외와 `old_id → replacement_id` 기록
- `H2` 수정 답변 반영
- 명백한 중국어·번역 깨짐 제거
- exact·near-exact template 중복 축소
- 계산형 일부의 한국 기준 엔진 검산
- 입력에 없는 사주 사실 언급 제외
- 훈계·진단·과도한 조언 공감 답변 제외
- task별 답변 길이 조정

v1 비교는 `(a) 동일 ID 공통 subset`과 `(b) source/task를 맞춰 보충한 전체 20K` 양쪽 결과를 보고한다.

### 즉시 차단

다음은 50K와 v1 학습 모두를 멈추고 원인 단계로 돌아간다.

- eval/train 누수 발견
- 모델·데이터 revision 또는 manifest hash 불일치
- 정상 생성률 Gate 실패가 pipeline 문제에서 발생
- 라이선스·AI Hub 승인 범위 위반
- checkpoint 재로딩 불가 또는 평가 재현 실패

## 배포 전 별도 Gate

이번 Phase가 끝나도 checkpoint를 공개 배포하지 않는다. 공개·서비스화 전에는 KananaOpenLicense의 Notice, `Powered by Kanana`, 모델명 prefix, 상업 라이선스 조건과 YEJI v9/AI Hub 제한을 다시 검토한다.

## 완료 Gate

- [ ] 네 모델의 raw 출력과 자동평가를 같은 설정으로 저장했다.
- [ ] 100문항 K10·K20 블라인드 선호 평가를 완료했다.
- [ ] 400건 KEEP/EDIT/DROP 검수와 H1/H2/D 상태를 저장했다.
- [ ] 자동 Gate, 선호율, source별 DROP 비율을 계산했다.
- [ ] 50K 또는 v1 Lite 중 하나를 규칙에 따라 선택했다.
- [ ] 결정 근거, 반대 근거, 남은 위험을 `next_stage_decision.md`에 기록했다.
- [ ] 모델·데이터 라이선스를 배포 전 미승인 상태로 유지했다.

## 산출물

```text
data/eval/human_review_400.jsonl
data/reports/auto_eval_k0_k10_k20.json
data/reports/human_preference_100.json
data/reports/error_taxonomy.json
data/reports/review_400_summary.json
runs/next_stage_decision.md
```

## 공식 자료

- [Transformers 4.57.1 text generation API](https://huggingface.co/docs/transformers/v4.57.1/en/main_classes/text_generation)
- [TRL 1.12.0 SFTTrainer metrics](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)
- [Kanana Open License](https://huggingface.co/kakaocorp/kanana-2-1.3b-base/blob/e9ffedf7b713530ae6a0c94ea32538d75e8524e1/LICENSE)
- [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Transformers generation API | `max_new_tokens`, `do_sample`, greedy generation 의미 확인 |
| 2026-08-27 | TRL SFT logged metrics | loss, entropy, token accuracy, learning rate, grad norm 정의 확인 |
| 2026-08-27 | Kanana·AI Hub 이용조건 | checkpoint 배포와 원문 재배포를 별도 Gate로 유지 |
