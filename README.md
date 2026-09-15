<!-- README.md - 사주 일기 도우미 baseline의 정본 문서와 현재 Gate를 안내한다. -->

# 사주 일기 도우미

Kanana 2 1.3B 기반 한국어 사주·공감 대화 baseline 프로젝트다. `KI10-MIX-v2`와 독립 `KI20-MIX-v2/run-1f5d732cae67`의 1 epoch Full FT·최종 새 프로세스 재로딩을 완료했다. Phase 6 sealed blind 자동 기술평가는 단회 완료됐고 현재 baseline 결정은 `AUTOMATED_REPAIR_REQUIRED`다. sealed blind 재실행과 품질 인증·production 승격은 금지 상태다.

계산기는 과거 공식 원국 v1.4, 단일 일진 v1.5, 일별 기간·단일 날짜 관계 release까지 구현·검증됐다. K0 기반 R8·R16·R32 LoRA 학습도 완료됐다. 2026-09-14 PR #28로 v1.15·20문장 진단·최신 계획을 `master`에 병합했고 원본 프로젝트 폴더도 동기화했다. 운영은 dashboard v1.14를 유지하며 v1.15는 병합 완료·운영 미배포 후보다. 60요청·54생성·6차단 진단 이후에는 원인 분리를 먼저 수행하고 데이터 보정·추가 학습을 조건부로 판단한다. strict/full·미래 물리 절입·모델 승격은 열지 않으며 feature는 기본 off다. 현재 상태의 상세와 확인 시점은 아래 기준선 정본을 따른다.

2026-09-15 전체 경로 진단 S0/S1과 S2 342요청(312생성·30사전 차단)을 완료했고 전체 테스트 879건을 통과했다. 검사기 오탐도 확인했으므로 자동 점수로 모델 우열을 단정하지 않는다. 다음 최소 제안은 검사기 새 버전 보강과 기존 합성 응답의 파생 재집계다. [실행 결과·한계](implementation/history/2026-09-15-system-context-diagnosis.md)를 따르며 추가 학습·큰 모델 실행·서비스 전환은 하지 않았다.

- [후속 실행 순서 정본](implementation/plans/saju_product_roadmap/README.md)
- [현재 기준선·운영과 후보 구분](implementation/plans/saju_product_roadmap/00-current-baseline.md)
- [다음 작업: 원인 분리 진단](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)
- [전체 흐름·컨텍스트 진단 실행 계획](implementation/plans/saju_system_context_diagnosis.md)
- [프로젝트 현황판 — versioned 과거 집계](PROJECT_STATUS.html)
- [10K/20K 정본 계획](implementation/plans/saju_1b_10k_20k_baseline/README.md)
- [Phase 5 학습 계약](implementation/plans/saju_1b_10k_20k_baseline/phase-5-baseline-training.md)
- [Phase 6 평가 계약](implementation/plans/saju_1b_10k_20k_baseline/phase-6-evaluation-v2-decision.md)
- [MIX20K-v3 보정 계획](implementation/plans/mix20k_v3_repair_plan.md)
- [K0 기반 MIX2K v4 교정·LoRA 계획](implementation/plans/mix2k_v4_chart_day_lora.md)
- [만세력 Runtime 정본](implementation/plans/saju_runtime_calculator_adoption.md)
- [chart-only 운영 준비 계약](docs/runtime/chart_only_operations.md)

모델·checkpoint·AI Hub 원문과 파생 private 평가 payload는 Git에 넣지 않는다. `PROJECT_STATUS.html`은 공개 가능한 집계·버전·해시·Gate만 담는 결정적 생성물이다.
