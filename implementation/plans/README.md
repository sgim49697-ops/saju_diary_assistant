# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: Phase 0~6 완료 실험의 이력·고정 계약·기존 모델 판정 정본
- `mix20k_v3_repair_plan.md`: 외부 MIX20K-v3 후보의 감사·자동 보정·학습 차단 정본
- `saju_runtime_calculator_adoption.md`: 한국 만세력 계산 core·공식 conformance·v3.1 이관을 결정하는 runtime 정본
- `grounded_dialogue_eval_plan.md`: 계산기 연결 대화의 자동 사실·상태·장문 진단 정본
- `saju_product_roadmap/README.md`: 전체 실행 순서 정본, 완료 Phase 0~6 계약을 참조하고 Phase 7~14로 연결
- `saju_product_roadmap/source-20260915.md`: 새 ZIP 문서 3개의 원문·841행·80절·588개 비공백 행 반영표
- `saju_product_roadmap/00-current-baseline.md`: 원격 master·v1.14 운영·v1.15/v1.16 부모·v1.17 CPU 검증 후보를 구분하는 현재 상태 정본
- `saju_product_roadmap/50-automatic-model-evaluation.md`: 전체 경로·컨텍스트/지시문·큰 기본 모델·데이터/학습 점검의 진단 상세 정본
- `saju_system_context_diagnosis.md`: 50-A~D 안의 S0~S6 실험 설계·통제 변수·파일 구현 순서 정본
- `mix2k_v4_chart_day_lora.md`: 완료된 K0 기반 R8·R16·R32 학습과 별도 미완료 400건 보정 이력
- `dashboard_v1_15_grounding.md`: 완료된 v1.15 후보 구현·비봉인 대화 진단 이력

종합 가이드와 정본이 충돌하면 해당 workstream 정본을 따른다. 학습 Phase·현재 모델 상태는 `saju_1b_10k_20k_baseline/README.md`, v3 후보는 `mix20k_v3_repair_plan.md`, 계산기·공식 근거·release 경계는 `saju_runtime_calculator_adoption.md`, 계산기 연결 대화 진단은 `grounded_dialogue_eval_plan.md`가 우선한다.

전체 실행 순서는 `saju_product_roadmap/README.md`가 소유하며 초기 인덱스는 Phase 0~6 완료 이력·계약을 소유한다. 이 로드맵은 앞선 정본의 사실·release·Gate를 덮어쓰지 않는다. Phase 7의 문서 반영 완료와 Phase 8 이후 구현·실행 완료는 다르다.

## 현재 정본 상태

| workstream | 현재 상태 | 승인 경계 |
|---|---|---|
| 10K/20K baseline | Phase 0~6 완료, `eval-e8630962cab2` 단회 자동 평가 완료 | `AUTOMATED_REPAIR_REQUIRED`, production 금지 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` 기술 후보·비학습 preflight 완료 | canonical 3,800행·다양성·state/grounding·serving 자동 blocker, v3.1·학습 금지 |
| 만세력 runtime | v1.4 원국·v1.5 단일 일진·일별 기간 release·단일 날짜 관계 release 완료 | 미래 물리 절입·미승인 범위 차단, 기능 기본 off·운영 승격 별도 |
| 계산기 연결 대화 | 기존 500건을 `eval-562c07d0e2e6`으로 재채점하고 2,048↔3,584 장문 200건 `eval-56d1357560d5` 완료 | 두 장문 arm 자동 목표 통과·3,584 상한 유지 후보, 전체 baseline·release·학습 권한 불변 |
| K0 기반 LoRA | R8·R16·R32 학습 완료, 별도 400건 보정은 현재 R16에 미반영 | 새 학습·보정 재개는 원인 분리 후 별도 판단 |
| 최신 대화 진단 | S0/S1·S2·CPU 재집계 보존, S3 `build-ffd985905b51` 96요청 검증 완료 | 86생성·10차단, P1 미채택·검사 한계 분리; S4~S6 미실행 |
| 앱 | dashboard v1.14 운영 / v1.15·v1.16 부모·v1.17 오차단 후보 CPU/합성 화면 검증 완료 | `build-49b9aed70565`, 현재 service·기본 모델·feature off 유지 |
| 후속 계획 | Phase 7 정본화·8A 후보 검증·8B S3 완료, 9~12 미실행·13~14 조건부 보류 | 기존 Phase·불변 산출물·release·후속 실행 권한 불변 |

루트 [`PROJECT_STATUS.html`](../../PROJECT_STATUS.html)은 Phase 6·대화 진단까지의 공개 집계를 `project-status/v1.3.0/build-38b9ca77ce45`로 보여준다. 이후 완료한 runtime release와 앱 통합은 모델·승격 상태를 바꾸지 않으므로 runtime 정본과 각 versioned 보고서에 별도로 고정한다. 현재 계산기 권위는 부모 v9 `data/reports/saju_runtime_conformance/v1.7.0/build-9f1784e74a4e/`와 단일 일진 v10 `data/reports/saju_runtime_conformance/v1.8.0/build-46185262164f/`를 함께 따른다.

v1.11의 원국·단일 날짜 명시 연결은 완료된 부모 구현이다. AES-GCM state·공개 allowlist·snapshot hash·자동 Grounding Gate를 적용하며 날짜 변경은 기존 대화에 덮어쓰지 않는다. 최신 운영·후보 commit과 검증 범위는 [현재 기준선](saju_product_roadmap/00-current-baseline.md)을 따른다. 이 통합은 strict/full runtime, Phase 6, v3.1, 추가 학습과 모델 승격을 승인하지 않는다.

실행 순서는 [Phase 7~14 로드맵](saju_product_roadmap/README.md)이다. [50 진단](saju_product_roadmap/50-automatic-model-evaluation.md)과 [전체 흐름·컨텍스트 진단 계획](saju_system_context_diagnosis.md)의 S0/S1·S2·CPU 후속과 Phase 8A·8B S3를 완료했다. 다음은 [Phase 9 모델 규모×정보](saju_product_roadmap/phases/phase-09.md)이며 Phase 10의 전체 앱 구현 완료를 기다릴 필요는 없다. Phase 10 응답 모드→11 데이터 원인→12 확인 평가 후 보정·학습·운영은 조건부로 결정한다. 문서 정본화와 남은 242요청은 후속 GPU 실행 승인이 아니다. 최상단 01·02·03은 요약·연결 문서이고 별도 실행 정본이 아니다.

## 현재 평가 기본값

품질 Gate와 baseline 결정은 정본 계약에 고정된 자동 기술지표만 사용한다. 계약 밖 의미 품질은 `not_measured`이며 별도 사용자 작업이나 Phase 완료 blocker로 바꾸지 않는다. 과거 versioned config·report·화면 자산에 남은 필드와 workflow는 당시 이력 보존용이고 현재 포인터·Gate·후속 지시가 아니다.

Phase 6은 이미 단회 소비됐으므로 재실행하지 않고 다음 명령으로 완료 상태만 검증한다.

```bash
.venv/bin/python -m scripts.evaluation.phase6_completed_verify
```

AI Hub 원문·내부 ID·private 결과·checkpoint는 계속 Git과 공개 보고서에서 제외한다. Phase 6의 공개 근거는 `data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2/`의 집계 3파일만 사용한다. 이후 대화 진단은 각 정본에 연결된 별도 공개 aggregate·manifest를 따른다.

## 진행 기록

### 2026-09-16 — Phase 8 완료·Phase 9 미실행 정합화

- 최신 앱 v1.17과 S3 96요청 검증·P1 미채택을 [실행 기록](../history/2026-09-16-phase8-intent-s3.md)에 연결했다. 운영·데이터·학습·기존 승인 상태는 보존하고 다음 별도 과제를 S4로 맞췄다.

### 2026-09-15 — Phase 7 보완 정본화 연결

- 저장소 AGENTS와 전체 순서/완료 계약의 소유권을 맞췄다. 기존 Phase와 실제 실행 상태는 보존하며 [보완 기록](../history/2026-09-15-phase7-canonicalization.md#supplement-20260915)에 검증을 남긴다.

### 2026-09-15 — ZIP 3개를 Phase 7~14 실행 순서에 연결

- 원문 SHA·전체 행별 대응표와 Phase별 계획을 연결하고 기존 초기 실험·계산기·모델 진단 정본의 권한을 유지했다. 검증과 변경 범위는 [정본화 기록](../history/2026-09-15-phase7-canonicalization.md)을 따른다.

### 2026-09-15 — CPU 재집계·앱 의도 후보 완료 포인터 갱신

- [재집계](../history/2026-09-15-system-context-rescore.md)·[v1.16 CPU canary](../history/2026-09-15-dashboard-v116-intent.md)를 연결했다. 부모 결과·현재 운영·미배포 후보를 분리했고 다음 별도 과제는 S3로 정리했다. 검증 결과는 실행 정본의 진행 기록을 따른다.

### 2026-09-15 — 전체 경로·S2 실행 결과 연결

- 342요청 완료·재검증과 검사기 오탐을 [완료 기록](../history/2026-09-15-system-context-diagnosis.md)에 연결했다. S2 자동 점수를 실제 정확도나 모델 승격 근거로 사용하지 않는다.
- 전체 ML 테스트 879건 통과, 기존 서비스·Phase·release·학습 상태 유지. 다음 제안은 새 검사 버전의 파생 재집계이며 추가 생성·학습은 수행하지 않았다.

### 2026-09-14 — 최신 코드·문서의 기본 브랜치 통합

- PR #28 `3bb0ce2`로 v1.15·20문장 진단·최신 정본과 기본 브랜치 작업 규칙을 통합하고 원본 폴더를 `master`에 맞췄다. 운영 service·모델·학습·기존 Gate는 유지한다.
- 검증 결과와 브랜치·자료 보존 경계는 [통합 기록](../history/2026-09-14-default-branch-integration.md)을 따른다.

### 2026-09-14 — 전체 흐름 실험 설계 정본 연결

- 컨텍스트 간섭을 입력 용량과 구분하는 [새 계획](saju_system_context_diagnosis.md)을 50의 상세 실행 설계로 연결했다. 문서·정합성 테스트만 변경했으며 검증 결과·미실행 범위는 새 계획의 진행 기록을 따른다.

### 2026-09-05 — 후속 정본 연결 갱신

- 완료된 모델·Runtime·진단과 현재 서비스/후보를 분리하고 원인 분리 우선 순서를 반영했다. Phase 6·생성된 현황판·불변 report는 변경하지 않았다.
- 검증 명령·결과와 다음 50-A 작업은 [재정렬 기록](../history/2026-09-05-model-cause-roadmap.md)을 따른다.
