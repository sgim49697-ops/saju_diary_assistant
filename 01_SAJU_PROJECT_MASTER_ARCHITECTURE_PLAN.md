<!-- 01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md - 제품 목표와 책임 분리를 요약하고 후속 실행 정본에 연결한다. -->

# 01. 사주 일기 Assistant 제품·구조 요약

## 문서 역할

이 문서는 제품 목표와 책임 분리의 **요약 문서**다. 독립 실행 정본이 아니다. 후속 실행 순서는 [로드맵 README](implementation/plans/saju_product_roadmap/README.md), 현재 상태는 [00 기준선](implementation/plans/saju_product_roadmap/00-current-baseline.md), 원인 분리 상세는 [50 자동 모델 평가](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)가 소유한다.

2026-09-05 정합화 기준은 완료 진단 commit `2646213`이다. 원격 master·운영 service·후보 branch가 같은 코드라고 가정하지 않는다. 과거 Phase 0~6·Runtime release·학습 build는 각각의 정본과 불변 산출물을 유지한다.

## 제품 목표

한국어 사주·일기 도우미로서 출생정보를 한 번 입력하고 승인된 원국·날짜 facts를 참고하며 후속 대화를 이어가는 것이 목표다. 실제 미래 사건을 맞힌다는 품질 주장을 하지 않는다.

- 모델은 계산 사실을 만들어내지 않고 질문에 필요한 근거를 골라 쉽게 설명한다.
- 이미 제공된 정보를 다시 묻지 않고 날짜 정정·시간 범위·미상을 보존한다.
- 원국이 연결돼 있어도 일반 공감·문장 작성 요청에는 사주를 강요하지 않는다.
- 해석은 참고 의견으로 구분하며 직업·재물·관계의 결과나 사건을 단정하지 않는다.
- 일기 저장·개인화·대운은 장기 제품 방향일 뿐 이번 실행 범위나 완료 기능이 아니다. 대운·미승인 계산은 별도 정책·자동 검증·release 전까지 노출하지 않는다.

## 계층별 책임

| 계층 | 담당 | 경계 |
|---|---|---|
| UI·Session | 입력·정정·연결 선택·snapshot revision·암호화 저장 | 원국 계산이나 임의 snapshot 교체를 하지 않음 |
| 원국 Runtime | 승인 profile·입력에 따른 원국과 불확실성 | 상담 문장·사건 예측을 만들지 않음 |
| 기간 Runtime | 승인 날짜의 label과 1~31일 일별 배열 | 미래 분 단위 절입·연간 범위를 승인하지 않음 |
| Relation Runtime | 단일 날짜의 십신·직접 관계 존재 | 길흉·신강약·용신으로 확대하지 않음 |
| Context Builder | 승인 allowlist·출처·상태·대화 이력 전달 | 내부 ID·키·불필요한 출생 원문을 모델/로그에 노출하지 않음 |
| LLM | 질문 이해·근거 선택·설명·공감·형식 준수 | 원국·기간·관계를 자체 계산하지 않음 |
| Validator | 측정 가능한 사실·상태·허위 완료 오류 검사 | 유한 규칙 통과를 의미 품질 인증으로 사용하지 않음 |

사실 권위의 `SOURCE_HARD_FACT`, `PROFILE_DETERMINISTIC` 등 등급은 [Runtime 정본](implementation/plans/saju_runtime_calculator_adoption.md)을 따른다. 이 요약에서 새 authority를 발급하지 않는다. 제품의 사실 입력과 설명 의견을 분리하는 목적은 유지한다.

## 현재 위치와 다음 방향

Runtime 10~40 구현과 R8·R16·R32 학습은 완료됐다. dashboard v1.14가 운영 중이며 v1.15와 20문장 비교는 검증 후보·미병합·미배포다. 검사기 한계·모델 오류·미측정 품질을 분리한 뒤 지시문, 큰 동일 계열 기본 모델, 데이터/입력 원인을 비교한다. 구현 상세나 실행 명령을 이 문서에 중복 정의하지 않는다.

[02 Runtime·앱 요약](02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md)과 [03 모델·데이터 요약](03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md)은 탐색 입구다. 데이터 생성·학습·서비스 전환은 진단 뒤 별도 결정이며 자동 진행하지 않는다.

## 변경하지 않는 상태

Phase 6은 `AUTOMATED_REPAIR_REQUIRED`로 완료됐다. 자동 계약 밖 자연스러움·의미 품질은 `not_measured`이고 계약 밖 평가를 완료 조건으로 추가하지 않는다. 소비된 sealed blind는 열거나 재사용하지 않는다. 모델 승격·Runtime release·기능 기본 off·기존 불변 보고서는 이번 문서 변경으로 바뀌지 않는다.

## 진행 기록

### 2026-09-05 — 제품 요약과 실행 정본 분리

- 중복 Phase·향후 모듈 설계·비자동 평가 절차를 제거하고 제품 목표·계층별 책임·정본 링크로 통합했다. 과거 설계의 상세 원문은 Git 이력에 남기며 현재 실행 지시로 사용하지 않는다.
- 검증 명령·결과는 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다. 다음 실행은 로드맵의 50-A이며 이 문서는 별도 승인 Gate를 만들지 않는다.
