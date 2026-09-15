<!-- 2026-09-15-system-context-rescore.md - S2 원응답의 새 검사 버전 집계·분모·한계를 기록한다. -->

# S2 CPU 파생 재집계 완료

- 실행 정본: [전체 흐름 진단 계획](../plans/saju_system_context_diagnosis.md).
- 동결 구현: `53c251a`, 검사기 `role-aware-contract-v1.1.0`.
- 부모: `build-c39b4bce5089`; 파생: `build-8547c487c858`.
- 342요청 = 312응답 재채점 + 기존 사전 차단 30. 적격성 6응답은 비교에서 제외했다. 실제 새 모델 생성·GPU 실행·학습은 0이다.
- [공개 집계](../../data/reports/saju_1b_baseline/system-context-rescore/v1.0.0/build-8547c487c858/aggregate.json), [manifest](../../data/reports/saju_1b_baseline/system-context-rescore/v1.0.0/build-8547c487c858/build_manifest.json), [검증](../../data/reports/saju_1b_baseline/system-context-rescore/v1.0.0/build-8547c487c858/verification.json).

## 분모와 결과

본 비교의 각 모델/정보 조건은 48요청·43생성·5차단이다. 아래는 필수 사실 지표가 적용되는 **24개**의 PASS/FAIL/UNSCORABLE 수다. 판단 불가를 분모에서 빼서 정확도로 표현하지 않는다.

| 모델 | 전체 정보 C_FULL | 최소 정보 C_MIN | 의미 |
|---|---|---|---|
| K0 | 3 / 20 / 1 | 2 / 20 / 2 | 최소 정보만으로 해결되지 않음 |
| R16 | 11 / 6 / 7 | 6 / 10 / 8 | 판단 불가가 많아 단순 통과 수로 우열 확정 불가 |
| KI20 | 0 / 19 / 5 | 1 / 19 / 4 | 이전 응답의 실제 오류가 남음 |

동일 사례 쌍의 FULL→MIN 판정은 다음과 같다. 이것은 새로운 성능 향상이 아니라 같은 응답에 새 검사 문법을 적용한 비교다.

| 모델 | FAIL→PASS | PASS→FAIL | 양쪽 PASS | 양쪽 FAIL | 판단 불가 포함 |
|---|---:|---:|---:|---:|---:|
| K0 | 1 | 2 | 1 | 17 | 3 |
| R16 | 1 | 3 | 3 | 5 | 12 |
| KI20 | 1 | 0 | 0 | 15 | 8 |

R16의 이전 자동 회귀 8개에서 확인한 검사 오탐 6개를 별도로 대조했다.

- `premise-4`, `history-1`의 MIN: 기존 FAIL → 새 PASS.
- `facts-5`, `premise-6`, `correction-4`, `history-2`의 MIN: 기존 FAIL → 새 UNSCORABLE. 잘못된 부정·과거 인용을 현재의 틀린 사실로 세지 않지만 지원 문법 밖의 표현까지 확정하지 않는다.
- 실제 MIN 오류로 확인했던 `facts-1`, `correction-3`은 계속 FAIL이다. `facts-1`의 FULL은 새 문법에서 UNSCORABLE이므로 과거 회귀 8개와 새 회귀 3개를 일대일 개선 건수로 읽지 않는다.
- 새 R16 FULL→MIN PASS→FAIL 쌍은 `facts-3`, `premise-2`, `correction-3`이다. 모든 판정 전이는 공개 aggregate에 보존했다.

## 검사 버전 변경과 독립 지표

- R16 FULL의 필수 사실 지표: FAIL→PASS 2, FAIL→UNSCORABLE 2, PASS→UNSCORABLE 4. 나머지 16개는 동일하다.
- R16 MIN: FAIL→PASS 3, FAIL→UNSCORABLE 5, PASS→UNSCORABLE 3. 나머지 13개는 동일하다. PASS 수만 보면 6→6이지만 같은 여섯 응답이 아니다.
- 모든 엔진·정보 조건에서 문장 수·사과 횟수·목표 날짜 사용·비공개 필드·출력 종료의 독립 지표 변경은 **0**이다. 전체 지표의 비대각 판정 전이는 비교 분모에서 85개이며 응답 85개라는 뜻은 아니다.
- 두 문장 지표는 적용 7개 중 K0 FULL/MIN 2/2, R16 4/3, KI20 6/5 PASS다. 잘못된 일간 전제 교정은 적용 6개 중 K0 0/0, R16 2/1, KI20 0/0 PASS다.
- 자연스러움·해석 의미는 `not_measured`다. 이를 별도의 완료 요건으로 만들지 않는다.

## 같은 12개 보조 질문의 비교

모든 조건의 질문 집합을 `facts-1..6`, `premise-1..6`으로 맞췄다. 주 비교 24개와 보조 12개의 통과 수를 섞지 않는다.

| R16 조건 | PASS / FAIL / UNSCORABLE |
|---|---|
| C_FULL | 5 / 3 / 4 |
| C_MIN | 2 / 7 / 3 |
| C_PAD | 5 / 6 / 1 |
| C_POS_FRONT | 5 / 6 / 1 |
| C_POS_MIDDLE | 3 / 9 / 0 |
| C_POS_END | 3 / 8 / 1 |

위치·패딩 조건의 차이는 관측됐지만 표본이 작고 의미·구분자·지원 문법 차이가 남는다. attention 내부 원리나 모델 크기의 인과 증거로 단정하지 않는다. 세 모델은 모두 1.3B이며 S3 지시문·S4 크기 비교는 미실행이다.

## 검증과 보존

```bash
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore validate-contract
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore plan
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore verify-parent
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore execute
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore execute --execute
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore verify --build build-8547c487c858
```

- 실제 실행·별도 verify·동일 명령 재실행 모두 같은 build/hash로 통과했다. 모델 파일·tokenizer 재로딩 없이 기존 460 source와 입력/응답 684파일을 재검증했다.
- 부모 공개 3파일·기존 source·입력·응답은 변경하지 않았다. 새 trace는 Git 제외 private 디렉터리 0700·파일 0600에만 보존한다. 공개 파일은 집계·manifest·verification뿐이다.
- 구현 동결 전 진단 27개 회귀, 관련 표적 54개와 전체 925개 회귀가 통과했다. 전체 최종 앱 통합 검증은 앱 후속 기록에 별도로 남긴다.
- 운영 v1.14 PID `3144071`·재시작 0 유지. Phase 6·Runtime release·기본 모델·feature off·학습·승격을 변경하지 않았다.

## 후속

앱 의도 오탐 후보는 이 재집계와 별도이며 부모 사전 차단 30건을 다시 생성하지 않는다. 다음 모델 실험은 고정 R16/C_FULL에서 P0/P1 지시문만 비교하는 S3다. 새 실험은 지원 문법 밖의 응답을 판단 불가로 보고하며, 결과를 좋게 만들기 위해 현재 검사 버전을 사후 조정하지 않는다.
