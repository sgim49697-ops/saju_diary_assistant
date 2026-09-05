<!-- 03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md - 모델·학습 데이터 상태를 요약하고 원인 분리 후 조건부 보정으로 연결한다. -->

# 03. 모델 평가·데이터·학습 요약

## 문서 역할

이 문서는 모델·데이터의 **요약 문서**다. 실행 순서는 [로드맵 README](implementation/plans/saju_product_roadmap/README.md), 현재 상태는 [00 기준선](implementation/plans/saju_product_roadmap/00-current-baseline.md), 진단 상세는 [50 자동 모델 평가](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)가 소유한다. 여기서 별도 평가 수량·임계값·학습 순서를 만들지 않는다.

## 완료와 미완료

| 항목 | 상태 | 해석 |
|---|---|---|
| K0·KI10·KI20 | 고정 K0, KI10·KI20 Full FT 완료 | 학습 실패가 아니라 실제 응답의 원인 분리가 필요 |
| Phase 6 | 단회 완료, `AUTOMATED_REPAIR_REQUIRED` | 기존 판정 불변, 소비된 sealed blind 재열람·재사용 금지 |
| MIX2K-v4 LoRA | R8·R16·R32 각각 2,000행·250 step 완료 | 정식 5-arm 생성 비교 완료와는 별개 |
| 현재 R16 입력 | `v1.0.1/build-54836f556b4f` | [LoRA 계획](implementation/plans/mix2k_v4_chart_day_lora.md)의 고정 학습 이력 |
| 별도 v1.1 보정 | 마지막 checkpoint accepted 238/400 | 현재 R16에 미반영, 자동 재개·재학습하지 않음 |
| 최신 개발 진단 | 20문장·3모델·60요청·54생성·6차단 완료 | 지시문 A/B·큰 기본 모델 비교는 미실행 |
| MIX20K-v3.0.1 | 보정·비학습 후보 이력 | 현재 2K 학습 데이터와 별도, v3.1 생성 승인 아님 |

## 최근 진단이 말해 주는 것

[20문장 완료 기록](implementation/history/2026-09-05-dashboard-prompt20.md)에서 연결 구조 검사 통과는 K0 8/13·R16 10/13·KI20 8/13이었다. R16의 일간·일주/일진 구분 개선이 있지만 세 모델 모두 틀린 일간 전제를 수용했다. 시간 범위, 개념 설명, 일반 대화 전환, 요청한 형식에서도 오류가 관찰됐다.

입력 token identity·부모 이력은 검증됐고 최대 입력 2,024 token·제외 대화 0이었다. 무조건 context 확대나 재학습을 시작할 근거는 없다. 유한 검사기의 오탐·누락 때문에 위 통과 수를 모델 정확도나 해석 품질로 부르지 않는다.

현재 R16 데이터에서 일반 공감 250행은 모두 원국 미연결이고 2,000행 중 1,751행이 3줄 이상 답변이었다. 이는 연결 중 일반 대화 전환과 짧은 형식 요청의 커버리지를 점검할 단서이지 원인 확정이 아니다. 특정 문자열 0건만으로 의미상 예제 부재를 단정하지 않는다. 세부 집계 조건은 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다.

## 다음 판단의 원칙

- 모델 오류·검사기 오류·미측정 품질을 분리하고, R16 현재 지시문 대 개선 후보 하나를 비교한다.
- 큰 동일 계열 Instruct 기본 모델 비교는 필수다. K0 1.3B 기본 모델을 기준으로 크기 가설을 점검하며 R16과 큰 기본 모델만의 차이를 크기 효과로 부르지 않는다.
- 모델별 공식 tokenizer/template·revision·정밀도·VRAM 조건을 실행 전에 등록한다. 다른 모델의 token ID 동일성을 요구하거나 메모리 부족 때 다른 계열·양자화로 자동 대체하지 않는다.
- 남은 오류에 대해서만 데이터 커버리지와 serving 입력 상태를 점검한다. 별도 400건 보정의 범위가 이번 오류를 해결하는지도 이때 판단한다.
- [60 데이터 build](implementation/plans/saju_product_roadmap/60-mix20k-v3-1-build.md)와 [70 학습·승격](implementation/plans/saju_product_roadmap/70-training-and-promotion.md)은 조건부 후속이다. 진단 완료만으로 자동 진행하지 않으며 모델 크기·학습 방식·규모는 별도 결정이다.

## 평가·데이터 보존 원칙

측정 가능한 자동 계약만 사용하고 자연스러움·의미 품질은 계약이 없으면 `not_measured`로 남긴다. 계약 밖 평가를 완료 조건으로 추가하지 않는다. 진단 완료는 모든 응답의 정답이나 품질 승인을 뜻하지 않는다.

실제 모델 입력과 generation 조건을 고정하고, 새 검사/데이터/학습에는 새 version·build를 사용한다. 이미 노출된 20문장은 개발 진단이며 새로운 봉인 성능으로 주장하지 않는다. 과거 자동 보고서를 유리하게 덮어쓰거나 원출력·제한 데이터·checkpoint를 Git에 넣지 않는다.

[기존 Phase 정본](implementation/plans/saju_1b_10k_20k_baseline/README.md), [v3 후보 보정 정본](implementation/plans/mix20k_v3_repair_plan.md), LoRA의 versioned 계약은 당시 실행 범위를 보존한다. 과거 768 길이·최소 3문장/3줄·Full FT 지시를 새 실험의 자동 기본값으로 복사하지 않는다.

## 진행 기록

### 2026-09-05 — 보정·학습보다 원인 분리를 선행

- 완료된 3-rank 학습과 최신 20문장 진단을 반영했다. 비자동 평가 절차·필수 표본 작업·승격 조건과 중복 실행 지시를 제거하고 50~70 정본에 연결했다.
- 검증 명령·결과는 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다. 모델 등록·다운로드·GPU 생성·400건 재개·추가 학습·기존 Gate 변경은 미실행이다.
