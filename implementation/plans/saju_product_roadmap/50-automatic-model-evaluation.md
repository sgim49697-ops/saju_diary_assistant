<!-- 50-automatic-model-evaluation.md - 동일 Runtime context에서 K0와 KI20을 자동 계약으로만 비교한다. -->

# 50. 자동 모델 비교

## 질문

1. 동일 chart/period/relation fact를 K0와 KI20 중 어느 쪽이 더 잘 지키는가.
2. missing-only intake, correction, blocked 결과, 후속 snapshot 재사용이 정확한가.
3. 일반 감정 대화에서 불필요한 사주 입력을 요구하지 않는가.

## 자동 지표

- fabricated pillars/period/relation facts.
- input contradiction, unknown-hour violation, false completion.
- provided-field reask, tool AST, state transition, relative-date resolution.
- non-saju unnecessary intake, severe safety, max-token hit, general replay regression.

동일 prompt·chat template·decoding·context hash를 사용한다. 자동 계약이 없는 자연스러움과 의미 품질은 `not_measured`이며 목표 계산이나 승격 blocker에 포함하지 않는다.

기존 Phase 6와 grounded-dialogue 원시 출력은 재사용하지 않는다. 공개 보고서에는 aggregate·manifest·threshold만 기록하고 prompt·응답·case ID는 넣지 않는다.

## 진행 기록

### 2026-09-05 — 실제 대화와 비교 조건 사전 진단

- 활성 dashboard v1.14에서 신규 합성 질문 10개를 K0·R16·KI20에 전달해 응답 30개를 생성했다. R8·R16·R32의 2,000행·250 step 완료 artifact도 재검증했다. 이는 정식 5-arm 비교 완료가 아니다.
- 날짜 intent 오탐, 실제 기간 schema와 scorer 경로 불일치, 다음 날짜 요청에 현재 snapshot을 사용하는 오류의 통과를 재현했다. 기존 Gate 통과 수는 실제 정확도로 사용하지 않는다.
- 동일 파일 hash에도 KI20과 K0의 effective tokenizer가 달랐다. 동일 KI20·동일 렌더링 원문 두 질문의 tokenizer 통제 4응답에서 차이를 확인했으며 tokenizer 교체만으로 품질 문제가 해소되지는 않았다.
- 다음 구현은 tokenizer 동결·기간 schema/날짜 검사 교정 후 새 평가 계약을 확정하는 순서다. 기존 문서의 K0↔KI20 질문에 별도 LoRA 실험 비교축을 추가할 때도 이를 선행한다.
- dashboard·LoRA 표적 테스트 19/19와 artifact SHA 검증을 통과했다. 세부 분석·미완료 v1.1.0 보정 상태·진단 한계는 [실제 대화 진단 기록](../../history/2026-09-05-realistic-chat-audit.md)에 정리했다. sealed blind·학습·승격 상태는 변경하지 않았다.

### 2026-09-05 — v1.15 비교 조건·날짜 사전 차단 후보 완료

- [v1.15 계획](../dashboard_v1_15_grounding.md)에 따라 공통 tokenizer backend·최종 입력 identity, 서버 날짜 범위 사전 차단, 실제 기간 schema의 역할별 사실 검사를 구현했다. 기존 v1.14 서비스는 유지했다.
- 최종 합성 재생 30요청 중 27생성·3차단, 첫 turn 6개 그룹의 K0·R16·KI20 token identity 일치, 실제 현재 날짜 HTTP 연결·R16 생성·후속 차단을 확인했다. 정식 5-arm 평가나 R8/R32 생성 비교 완료는 아니다.
- 구조 사실 검사에서는 연결 응답 4개씩 중 K0 1개·R16 4개·KI20 0개가 통과했다. 원출력을 보존했으며 설명·시간 미상 이해·문장 작성 실패는 여전히 남는다. 품질 승인이나 새로운 승격 blocker를 만들지 않는다.
- 관련 47건을 통과했고 전체 805건에서는 변경 전에도 있던 실패 4·오류 37을 확인했다. 공개 build와 한계는 [v1.15 완료 기록](../../history/2026-09-05-dashboard-v115-grounding.md)에 정리했다. Phase 6·Runtime release·학습·승격 상태는 변경하지 않았다.
