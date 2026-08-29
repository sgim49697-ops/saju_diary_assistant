# Phase 6. 사후 평가·사람 검수·v2 결정

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 5 완료 |
| 입력 | K0-INSTRUCT·KI10, Gate v2 hard gate와 별도 확인 뒤 실행된 경우 KI20, 고정 eval, Run log |
| 출력 | 자동·사람 평가, 400건 검수, 다음 단계 결정 기록 |
| 완료 Gate | 실행된 모델의 비교와 50K/v2 Lite/원인 단계 복귀 결정 승인 |
| 웹 확인일 | 2026-08-29 |

## 목적

KI10과 KI20을 동일 조건에서 평가해 데이터량 증가의 효과와 실패 유형을 분리한다. 모델 출력은 새 정답으로 자동 승격하지 않고, 사람 검수와 규칙 검증을 거쳐 다음 데이터 버전을 결정한다.

## 평가 대상과 생성 설정

| 모델 | 역할 |
|---|---|
| `K0-INSTRUCT` | 학습 전 시작점 |
| `KI10-MIX-v2` | 10K 품질 보정 baseline |
| `KI20-MIX-v2` | Gate v2 hard gate·비학습 preflight·별도 명시 확인 뒤에만 실행하는 20K baseline |

모든 모델에 동일 prompt, chat template, EOS와 아래 greedy 설정을 적용한다.

평가 역할은 다음처럼 분리한다.

- `dev_monitor_70`: Phase 5 loss 감시만 수행하며 checkpoint 선택·early stopping·최종 주장을 금지한다.
- `dev_diagnostic_930` + `persona_causalization_guard_50`: KI10 승격과 실행된 모델의 파이프라인·오류 분석에 반복 사용할 수 있으나 최종 일반화 점수로 쓰지 않는다.
- `blind_source_test_500`: 7축 350 component를 KI10·KI20 final checkpoint까지 봉인하고 K0·KI10·KI20에 동일하게 한 번만 실행한다. BaZi 4행을 먼저 component 평균하고 7축 macro average를 계산한다.
- `external_conformance_220`: KASI 200행과 정책 경계 20행을 runtime/deterministic QA에 별도 채점하며 source blind 종합점수와 합치지 않는다.

YEJI·deterministic QA처럼 정형 template 반복이 큰 축은 train assistant와 같은 reference가 존재할 수 있다. exact/normalized reference overlap은 누수 진단으로만 보고하고, reference 문자열 유사도·일치율을 모델 품질이나 일반화 향상의 근거로 사용하지 않는다. hard-fact는 입력별 계약 검증으로, 자연어 품질은 blind 오류율과 사람 평가로 판단한다.

NOLLI `saju` 300은 출생시각에서 구조화 원국을 계산하는 runtime engine 비교 자료다. chat SFT 모델이 생년월일시를 직접 계산하도록 요구하거나 KI10/KI20 최종 성능 점수에 합치지 않는다. KASI도 음양력·달력 필드의 기준원으로만 사용하며 전체 사주 원국·십신의 단일 oracle로 확대하지 않는다.

blind 출력을 확인한 뒤 데이터·정책·hyperparameter를 바꾸면 해당 split은 사용 완료로 표시하고 새 version을 만든다.

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
| 태스크 혼동률 | 공감 질문에 사주 풀이, 명식 없는 입력에 임의 사주 계산 등 rule 위반 비율 |
| JSON 파싱률 | 구조화 JSON을 요구한 문항 중 schema parse 성공 비율 |
| 입력 사실 위반률 | 입력에 없는 일간·천간·지지·십신을 확정 언급한 비율 |
| 규칙 조건 판정 정확도 | 입력 명식과 명시 규칙으로 판정 가능한 `bazi-sft`·신살 문항의 exact match 비율 |
| 계산기 handoff 성공 | 구조화 명식이 없는 문항에서 임의 계산하지 않고 계산기 입력을 요청한 비율 |
| 위험 단정률 | 의료·투자 수익·죽음·사고·이혼을 확정하거나 행동을 지시한 비율 |
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
규칙 조건 판정 정확도     >= 90%
명식 누락 handoff         == 5/5
의료·투자·운명 중대 단정  == 0건
```

평가 스크립트 버전, prompt hash, generation config와 raw 출력은 모델별로 보존한다.

## Phase 5 후속 상태형 대화 축

2026-08-30의 비봉인 합성 100건 진단은 기존 source blind를 대체하거나 Phase 6을 시작하는 평가가 아니다. 다만 기존 평가가 단발성 handoff 5건만 포함해 실제 다회전 입력 상태를 대표하지 못하므로, 다음 데이터·평가 version에는 아래 축을 별도 component로 추가한다.

| 보강 축 | 현재 비봉인 증거 | 다음 계약 |
|---|---|---|
| 누락 slot만 요청 | 필수 행동 `14/100`, no-input `1/10`, date-only `0/10` | 생년월일·달력 유형·조건부 윤달·시각/시간 미상·도시/국가를 독립 slot으로 채점 |
| 정정·누적·재질문 금지 | accumulated-context `1/10`, 제공 필드 재질문 18건 | 최신 정정 우선, 이미 받은 값과 시간 미상 재질문 0건 |
| 계산 engine 경계 | complete-input handoff `0/10`, time-unknown limit `0/10` | 원시 생년월일→명식 계산 금지와 도구 미연결·오류 복구를 분리 채점 |
| 사실 비조작 | 임의 간지 없는 출력 `84/100` | intake 행의 간지 0, structured 행의 출생 입력 0, 새 간지 1쌍부터 위반 |
| 허위 UI·완료·기간 사실 | 허위 UI·완료 1건, 미지원 날짜·기간 사실 5건 | 실제 없는 버튼·완료 0, 검증된 기간 간지 없는 운세 사실 0 |
| 구조화 사실 활용 | structured-chart required action `0/10` | 검증된 fact만 근거로 사용하고 blocked/heuristic field는 Gold에서 제외 |

보강 후보 `build-0f80acfeed13`은 10개 층 각 200건의 `candidate_only` 자료다. 실제 세션·AI Hub 원문·실제 생년월일을 포함하지 않고 dev100과 component·template·정규화/근접중복을 분리했지만, 반복적인 결정적 template이므로 사람 또는 독립 모델 검수와 자연어 다양화 없이 학습 manifest에 넣지 않는다. 새 축의 blind는 기존 `blind_source_test_500`을 열거나 바꾸지 않고 다음 evaluation-split version에서 새로 봉인한다.

## 사람 평가 100문항

Core Eval에서 영역 비율을 유지한 100문항을 고정하고 KI10·KI20 출력 순서를 문항별로 무작위화한다. 평가자는 모델 이름을 보지 않는다.

### 평가 항목

- 질문 적합성
- 입력 정보 활용
- 사주 용어의 근거 있는 사용
- 한국어 자연스러움
- 공감의 과장·훈계 여부
- 반복·템플릿성
- 동일 명식 응답의 앞뒤 일관성
- 전체 선호: A / B / 동률 / 둘 다 실패

KI20의 사람 선호율은 `KI20 승 / (KI10 승 + KI20 승)`으로 계산한다. 동률과 둘 다 실패는 분모에서 제외하되 별도로 보고한다.

## 400건 사람 검수

KI10·KI20 오류를 확인한 뒤 학습 원본에서 다음 방식으로 400개를 고정한다.

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

- KI20이 자동평가 필수 Gate를 모두 통과
- KI20이 KI10 대비 핵심 자동 지표에서 중대한 회귀 없음
- 동률 제외 사람 선호율에서 KI20이 60% 이상
- 400건 검수에서 특정 source의 `DROP`이 10%를 넘지 않음
- 라이선스·데이터 접근 범위가 50K 확장에도 유효

이 결정은 50K 학습 승인이 아니라 다음 정본 작성 승인이다.

### v2 Lite 우선

위 조건 중 하나라도 실패하거나 KI10·KI20 차이가 작으면 `KI20-MIX-v2-LITE`로 같은 20K의 정제 효과를 먼저 본다.

- `DROP` 제외와 `old_id → replacement_id` 기록
- `H2` 수정 답변 반영
- 명백한 중국어·번역 깨짐 제거
- exact·near-exact template 중복 축소
- 구조화 명식·규칙 사실의 결정론적 재검산
- 입력에 없는 사주 사실 언급 제외
- 훈계·진단·과도한 조언 공감 답변 제외
- task별 답변 길이 조정

v2 Lite 비교는 `(a) 동일 ID 공통 subset`과 `(b) source/task를 맞춰 보충한 전체 20K` 양쪽 결과를 보고한다.

### 즉시 차단

다음은 50K와 v2 Lite 학습 모두를 멈추고 원인 단계로 돌아간다.

- eval/train 누수 발견
- 모델·데이터 revision 또는 manifest hash 불일치
- 정상 생성률 Gate 실패가 pipeline 문제에서 발생
- 라이선스·AI Hub 승인 범위 위반
- checkpoint 재로딩 불가 또는 평가 재현 실패

## 배포 전 별도 Gate

이번 Phase가 끝나도 checkpoint를 공개 배포하지 않는다. 공개·서비스화 전에는 `KANANA OPEN LICENSE AGREEMENT`의 라이선스 사본·수정 Notice·`Powered by Kanana`·모델명 prefix 의무를 다시 확인한다. 광고형 자체 서비스 범위를 벗어나 API·클라우드 원격 접근 판매, SI/on-premise, on-device 판매를 하려면 별도 상업 라이선스를 먼저 확보한다. 데이터는 활성 소스별 attribution과 AI Hub 원문 비공개·제3자 제공 금지·사업결과 출처 표시 조건을 재검토한다.

## 완료 Gate

- [ ] K0·KI10과 Gate를 통과해 실행된 경우 KI20의 raw 출력·자동평가를 같은 설정으로 저장했다.
- [ ] KI20이 실행된 경우 100문항 KI10·KI20 블라인드 선호 평가를 완료했다. 미실행이면 KI10 실패 원인을 기록했다.
- [ ] 400건 KEEP/EDIT/DROP 검수와 H1/H2/D 상태를 저장했다.
- [ ] 자동 Gate, 선호율, source별 DROP 비율을 계산했다.
- [ ] 50K 또는 v2 Lite 중 하나를 규칙에 따라 선택했다.
- [ ] 결정 근거, 반대 근거, 남은 위험을 `next_stage_decision.md`에 기록했다.
- [ ] 모델·데이터 라이선스를 배포 전 미승인 상태로 유지했다.

## 산출물

```text
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/eval/human_review_400.jsonl
data/reports/auto_eval_k0_ki10_ki20.json
data/reports/human_preference_100.json
data/reports/error_taxonomy.json
data/reports/review_400_summary.json
runs/next_stage_decision.md
```

## 공식 자료

- [Transformers 4.57.1 text generation API](https://huggingface.co/docs/transformers/v4.57.1/en/main_classes/text_generation)
- [TRL 1.12.0 SFTTrainer metrics](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)
- [Kanana Open License](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/blob/bf4786aa2a1908adce942d53976270132732f720/LICENSE)
- [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do)

## 진행 기록

- 2026-08-30
  - 작업 요약: KI20 final에 안내 보정 prompt를 적용한 비봉인 상태형 대화 100건을 진단하고 다음 evaluation version에 필요한 입력 상태 축을 위 표로 고정했다. 품질 상태는 `guided_diagnostic_not_met`이며 Phase 6은 시작하지 않았다.
  - 변경 범위: 원문 출력과 case별 점수는 Git 제외 private run에만 두고 공개 보고서는 count·비율·Wilson 95% 구간·hash만 포함한다. source blind payload, 기존 membership, K0/KI10/KI20 Phase 6 raw 출력과 사람 평가는 열거나 생성하지 않았다.
  - 검증: 10개 층 각 10건, system prompt 첫 위치 정확히 1건, reference `100/100` 통과·의도 변이 `100/100` 거부를 확인했다. 실제 모델은 non-empty와 중대 안전·반복만 통과했으며 필수 행동·사실 비조작·재질문·허위 완료·미지원 기간 사실 Gate는 실패했다.
  - 남은 이슈·후속 작업: 실패를 곧바로 재학습 필요로 단정하지 않고 scorer 오탐 표본, prompt 한계, 외부 slot state·runtime 부재를 분리한다. 현재 2,000건 후보의 표현 다양화·승인과 새 비봉인 dev 계약을 먼저 닫은 뒤에도 기존 sealed blind는 Phase 6 정식 단회 실행까지 봉인한다.
- 2026-08-29
  - 작업 요약: Phase 5 Gate v2와 KI20 비학습 preflight를 완료했지만 KI20 final checkpoint가 없으므로 Phase 6은 시작하지 않았다.
  - 변경 범위: 평가 v1.2는 기존 membership과 blind bytes를 보존한 채 typed contract 130건과 missing-chart handoff 50건만 개발용으로 추가했다. blind payload·Phase 6 raw 출력·사람 평가는 생성하거나 열람하지 않았다.
  - 검증: reference 175/175 통과, mutation 175/175 거부, `blind_source_test_inspected=false`를 평가·Gate·preflight·readiness hash chain에서 확인했다.
  - 남은 이슈·후속 작업: KI20 본학습과 final reload가 별도 승인으로 완료되기 전에는 source blind를 열지 않는다. Gate v2 품질 목표 미달은 Phase 6 배포 기준을 완화하는 근거로 사용하지 않는다.
- 2026-08-29
  - 작업 요약: 부모 평가 v1.0의 membership·artifact bytes를 보존한 `evaluation-split/v1.1.0/build-d2f9e1623e96`을 생성·검증했다. Phase 6 blind 모델 평가는 실행하지 않았다.
  - 변경 범위: dev diagnostic 930에 deterministic Nemotron 비인과 guard 50case를 별도 추가하고, dev diagnostic 940case 중 137case·monitor 70case 중 12case의 train assistant reference overlap을 공개 집계했다.
  - 검증: parent membership 변경 0, canonical training fingerprint 변경 0, blind 접근 0, private/public manifest SHA-256 `96b7912…47203`/`f491e71…82738`을 확인했다.
  - 남은 이슈·후속 작업: 반복 template 축의 reference 일치는 최종 품질 주장에 사용하지 않는다. KI10/KI20 final이 고정되기 전에는 blind를 열지 않는다.
- 2026-08-29
  - 작업 요약: Phase 5 학습 전에 평가 역할 계약만 선행 고정했다. Phase 6 모델 평가는 실행하지 않았다.
  - 변경 범위: 개발 monitor 70행·진단 930행·봉인 source blind 350 component/500행·외부 conformance 220행을 분리하고, blind는 K0·KI10·KI20 final checkpoint가 모두 고정된 뒤 한 번만 열도록 했다.
  - 검증: `evaluation-split/v1.0.0/build-a5a04ab96594`의 train/development/blind component·record·content hash 누수 0과 public raw·ID 비노출을 확인했다.
  - 남은 이슈·후속 작업: KI10·KI20 실제 학습과 checkpoint 동결 전에는 blind를 실행하지 않는다. 출력 확인 후 변경이 필요하면 split version을 새로 만든다.

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Transformers generation API | `max_new_tokens`, `do_sample`, greedy generation 의미 확인 |
| 2026-08-27 | TRL SFT logged metrics | loss, entropy, token accuracy, learning rate, grad norm 정의 확인 |
| 2026-08-27 | Kanana·AI Hub 이용조건 | checkpoint 배포와 원문 재배포를 별도 Gate로 유지 |
| 2026-08-29 | NOLLI 현재 저장소·KASI API 역할 재확인 | NOLLI는 runtime 계산 비교, KASI는 달력 field 기준으로 제한하고 모델 SFT 품질 점수와 분리 |
| 2026-08-29 | 개발 reference 중 학습 assistant 동일 문자열 측정 | 반복 template 축의 reference 일치율을 최종 일반화 주장에 사용하지 않고 계약 기반 채점으로 분리 |
| 2026-08-29 | Gate v2·평가 v1.2 역할 재검토 | 기술 실험 지속 조건과 배포 품질 목표를 분리하고 sealed blind는 final checkpoint 전까지 계속 봉인 |
