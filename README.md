<!-- README.md - 사주 일기 도우미 baseline의 정본 문서와 현재 Gate를 안내한다. -->

# 사주 일기 도우미

Kanana 2 1.3B 기반 한국어 사주·공감 대화 baseline 프로젝트다. `KI10-MIX-v2`와 독립 `KI20-MIX-v2/run-1f5d732cae67`의 1 epoch Full FT·최종 새 프로세스 재로딩을 완료했다. Phase 6 sealed blind 자동 기술평가는 단회 완료됐고 현재 baseline 결정은 `AUTOMATED_REPAIR_REQUIRED`다. sealed blind 재실행과 품질 인증·production 승격은 금지 상태다.

계산기는 과거 공식 원국 v1.4, 단일 일진 v1.5, 일별 기간·단일 날짜 관계 release까지 구현·검증됐다. K0 기반 R8·R16·R32 LoRA 학습도 완료됐다. 2026-09-14 PR #28로 v1.15·20문장 진단·최신 계획을 `master`에 병합했고 원본 프로젝트 폴더도 동기화했다. 운영은 dashboard v1.14를 유지하며 v1.15는 병합 완료·운영 미배포 후보다. 60요청·54생성·6차단 진단 이후에는 원인 분리를 먼저 수행하고 데이터 보정·추가 학습을 조건부로 판단한다. strict/full·미래 물리 절입·모델 승격은 열지 않으며 feature는 기본 off다. 현재 상태의 상세와 확인 시점은 아래 기준선 정본을 따른다.

2026-09-15 S0/S1·S2 342요청에 이어 [312응답 CPU 재채점](implementation/history/2026-09-15-system-context-rescore.md)과 [앱 v1.16 의도 정책 후보·CPU canary](implementation/history/2026-09-15-dashboard-v116-intent.md)를 검증했다. 원래 응답과 차단 30건은 보존했고 새 모델 응답은 생성하지 않았다. 유한 검사기의 판단 불가가 남아 자동 점수로 모델 우열을 단정하지 않는다. 운영 v1.14와 미배포 v1.16 후보를 구분한다.

새 ZIP 계획 3개는 [Phase 7 정본화](implementation/plans/saju_product_roadmap/phases/phase-07.md)에서 원문 보존·요구사항 연결 후 Phase 7~14로 통합·검증했다. **Phase 8A v1.17 오차단 후보·8B S3 96요청과 Phase 9 S4 v1.1 192요청의 검증을 완료**했다. [S3 기록](implementation/history/2026-09-16-phase8-intent-s3.md)의 P1은 미채택이고, [S4 복구·비교 기록](implementation/history/2026-09-16-phase9-s4-recovery.md#phase9)은 첫 저장 오류 1건을 보존한 뒤 172생성·20차단·새 실행 오류 0을 확인했다. 3B의 일부 사실/형식 개선과 일반 대화 회귀·반복 출력이 함께 있어 후보를 채택하지 않았다. [Phase 10 R16/v1.18 후보](implementation/history/2026-09-16-phase10-product-candidate.md#phase10)는 직접 사실 응답·설명 분리·필요 정보/이력 선택·정정 후 새 연결을 구현하고 CPU 46개·합성 화면 16개를 통과했다. 후속 [v1.19 버그 보완](implementation/history/2026-09-17-phase10-product-fixes.md#phase10-fixes)은 CPU 56개·합성 화면 32개로 응답 경쟁과 분류 오류를 보완했다. 최신 [v1.20 Claude Code 코드 검토·보완](implementation/history/2026-09-17-claude-product-review.md#claude-review)은 일상 어휘·정의 요청 오차단과 복합 요청 누락을 보완하고 부모 증거를 보존한다. [Phase 11 S5 분석](implementation/history/2026-09-17-phase11-data-hypotheses.md#phase11)은 2K 전수 대조·7축 가설·조건부 명세를 완료하고 400건 보정을 보류했다. 새 모델 생성은 0이며 다음은 Phase 12의 전체 후보 구성 동결·새 질문 실제 확인이다. 보정·학습·운영 전환은 그 결과에 따라 별도 판단하고 현재 운영 v1.14는 그대로다.

- [후속 실행 순서 정본](implementation/plans/saju_product_roadmap/README.md)
- [현재 기준선·운영과 후보 구분](implementation/plans/saju_product_roadmap/00-current-baseline.md)
- [완료한 후보: Phase 10 응답 모드·정정 흐름](implementation/plans/saju_product_roadmap/phases/phase-10.md)
- [완료한 분석: Phase 11 데이터·학습 필요성 가설](implementation/plans/saju_product_roadmap/phases/phase-11.md)
- [다음 작업: Phase 12 새 질문·실제 후보 앱 확인](implementation/plans/saju_product_roadmap/phases/phase-12.md)
- [ZIP 원문·요구사항 반영표](implementation/plans/saju_product_roadmap/source-20260915.md)
- [모델 진단의 기존 작업 계약](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)
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
