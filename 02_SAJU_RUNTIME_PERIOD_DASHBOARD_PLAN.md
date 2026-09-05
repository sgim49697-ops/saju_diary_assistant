<!-- 02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md - 완료된 계산·기간·관계와 운영/후보 앱의 경계를 요약한다. -->

# 02. 원국·기간 Runtime과 대시보드 요약

## 문서 역할

이 문서는 Runtime·앱의 **요약 문서**다. 실행 순서는 [로드맵 README](implementation/plans/saju_product_roadmap/README.md), 현재 상태는 [00 기준선](implementation/plans/saju_product_roadmap/00-current-baseline.md), 진단 상세는 [50 자동 모델 평가](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)를 따른다.

계산 권위·source·conformance·release는 [Runtime 정본](implementation/plans/saju_runtime_calculator_adoption.md)과 versioned registry가 소유한다. 이 문서의 제안 schema나 자연어 설명으로 권한을 바꾸지 않는다.

## 완료된 계산 범위

| 범위 | 완료 내용 | 경계 |
|---|---|---|
| v1.4 원국 | 정규화 양력 1920-01-07~2026-08-31, KR profile, exact/range/unknown | 절입 불확실성과 미지원 입력을 확정값으로 축소하지 않음 |
| v1.5 단일 일진 | 2026-09-02~2049-12-31, 공식 label 8,522/8,522 대조 | KST 정오 기준 날짜 label, 미래 분 단위 물리 시각 승인 아님 |
| 기간 일별 배열 | 1~31일 연속 window 263,717개 전수 검증 | 과거·연간·지원 종료 이후는 차단 |
| 단일 날짜 관계 | 십신·직접 관계 존재 전수 검증 | 범위 관계 배열·길흉·대운·신강약·용신은 포함하지 않음 |

[10 계약·복원](implementation/plans/saju_product_roadmap/10-period-contract-and-restore.md), [20 기간 Runtime](implementation/plans/saju_product_roadmap/20-daily-range-runtime.md), [30 기간 앱](implementation/plans/saju_product_roadmap/30-period-dashboard.md), [40 관계 Runtime](implementation/plans/saju_product_roadmap/40-day-relation-runtime.md)은 완료 이력으로 보존한다. 다시 구현하거나 과거 v1.3 후보의 범위를 현재 release로 잘못 사용하지 않는다.

## 앱 구현과 실제 운영 분리

- dashboard v1.11은 명시적 원국·단일 날짜 연결의 부모 구현이다. v1.12 기간·v1.13 관계 후보도 자동 canary를 완료했다.
- 2026-09-05 기준 실제 운영은 dashboard v1.14다. 기본 `ki20_final`과 선택 가능한 R16을 유지하며 이번 변경으로 서비스를 교체하지 않는다.
- [v1.15 계획](implementation/plans/dashboard_v1_15_grounding.md)의 tokenizer 동결·날짜 사전 차단·역할별 사실 검사는 구현·진단을 완료한 **미병합·미배포 후보**다. 자유문장에서 새 기간을 계산하거나 연결 snapshot을 자동 교체하지 않는다.
- v1.15의 20문장 비교는 60요청·54생성·6사전 차단이다. 내일/주간 사주 요청의 사전 차단과 일반 메시지 작성은 구분한다. 이는 별도 period Runtime의 승인 범위가 사라졌다는 뜻이 아니라 해당 대화 후보의 연결 범위 제한이다.
- 날짜·원국 변경은 새 계산·명시적 연결을 거친다. feature 기본 off, 암호화 상태·권한·rate limit·로그 비노출은 유지한다. 운영 키는 공개하지 않는다.

## 실제 구현 입구

| 영역 | 파일 |
|---|---|
| 승인 원국·단일 일진 | [engine_v1_4.py](scripts/runtime/calculation/engine_v1_4.py), [engine_v1_5.py](scripts/runtime/calculation/engine_v1_5.py) |
| 기간 해석·복원·실행 | [period_v1](scripts/runtime/period_v1/) |
| 단일 날짜 관계 | [relation_v1](scripts/runtime/relation_v1/) |
| 운영 계열·진단 후보 | [v1.14](scripts/training/phase5_dashboard_v1_14.py), [v1.15](scripts/training/phase5_dashboard_v1_15.py) |
| tokenizer·사실 검사 | [dashboard_tokenizer_v1.py](scripts/training/dashboard_tokenizer_v1.py), [dashboard_grounding_v2.py](scripts/training/dashboard_grounding_v2.py) |
| 원국 운영 보안 | [운영 계약](docs/runtime/chart_only_operations.md) |

## 후속 경계와 진행 기록

### 2026-09-05 — 오래된 구현 지시를 완료 범위와 코드 지도로 통합

- 미구현으로 남아 있던 기간·관계·앱 단계를 실제 완료 이력에 연결했다. 오래된 제안 schema·별도 R0~R9 실행 지시는 현재 정본으로 대체하고 원문은 Git 이력에 보존한다.
- 다음은 계산 기능 확대가 아니라 50의 원인 분리다. 입력 전달 결함이 입증된 경우에만 별도 수정 범위를 정한다. `not_measured`를 새 Gate로 만들지 않으며 Phase 6·release·학습·서비스 상태를 변경하지 않는다.
- 검증 명령·결과는 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다.
