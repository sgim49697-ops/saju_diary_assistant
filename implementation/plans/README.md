# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본
- `mix20k_v3_repair_plan.md`: 외부 MIX20K-v3 후보의 감사·자동 보정·학습 차단 정본
- `saju_runtime_calculator_adoption.md`: 한국 만세력 계산 core·공식 conformance·v3.1 이관을 결정하는 runtime 정본
- `grounded_dialogue_eval_plan.md`: 계산기 연결 대화의 자동 사실·상태·장문 진단 정본
- `saju_product_roadmap/README.md`: 완료된 Runtime·LoRA·20문장 진단 이후 원인 분리와 조건부 데이터/학습의 실행순서 정본
- `saju_product_roadmap/00-current-baseline.md`: 원격 master·v1.14 운영·v1.15 검증 후보를 구분하는 현재 상태 정본
- `saju_product_roadmap/50-automatic-model-evaluation.md`: 오류 분리·지시문 비교·큰 기본 모델 비교·데이터/입력 점검의 진단 상세 정본
- `mix2k_v4_chart_day_lora.md`: 완료된 K0 기반 R8·R16·R32 학습과 별도 미완료 400건 보정 이력
- `dashboard_v1_15_grounding.md`: 완료된 v1.15 후보 구현·비봉인 대화 진단 이력

종합 가이드와 정본이 충돌하면 해당 workstream 정본을 따른다. 학습 Phase·현재 모델 상태는 `saju_1b_10k_20k_baseline/README.md`, v3 후보는 `mix20k_v3_repair_plan.md`, 계산기·공식 근거·release 경계는 `saju_runtime_calculator_adoption.md`, 계산기 연결 대화 진단은 `grounded_dialogue_eval_plan.md`가 우선한다.

완료된 이력 다음의 신규 구현 순서는 `saju_product_roadmap/README.md`가 소유한다. 이 로드맵은 앞선 정본의 사실·release·Gate를 덮어쓰지 않는다.

## 현재 정본 상태

| workstream | 현재 상태 | 승인 경계 |
|---|---|---|
| 10K/20K baseline | Phase 0~6 완료, `eval-e8630962cab2` 단회 자동 평가 완료 | `AUTOMATED_REPAIR_REQUIRED`, production 금지 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` 기술 후보·비학습 preflight 완료 | canonical 3,800행·다양성·state/grounding·serving 자동 blocker, v3.1·학습 금지 |
| 만세력 runtime | v1.4 원국·v1.5 단일 일진·일별 기간 release·단일 날짜 관계 release 완료 | 미래 물리 절입·미승인 범위 차단, 기능 기본 off·운영 승격 별도 |
| 계산기 연결 대화 | 기존 500건을 `eval-562c07d0e2e6`으로 재채점하고 2,048↔3,584 장문 200건 `eval-56d1357560d5` 완료 | 두 장문 arm 자동 목표 통과·3,584 상한 유지 후보, 전체 baseline·release·학습 권한 불변 |
| K0 기반 LoRA | R8·R16·R32 학습 완료, 별도 400건 보정은 현재 R16에 미반영 | 새 학습·보정 재개는 원인 분리 후 별도 판단 |
| 최신 대화 진단 | 20문장×3모델 60요청·54생성·6사전 차단 완료 | 50-A~D는 후속, 구조 통과 수를 정확도로 사용하지 않음 |
| 앱 | dashboard v1.14 운영 / v1.15 검증 후보·미병합·미배포 | 현재 service·기본 모델 유지, 후보 검증만으로 교체하지 않음 |

루트 [`PROJECT_STATUS.html`](../../PROJECT_STATUS.html)은 Phase 6·대화 진단까지의 공개 집계를 `project-status/v1.3.0/build-38b9ca77ce45`로 보여준다. 이후 완료한 runtime release와 앱 통합은 모델·승격 상태를 바꾸지 않으므로 runtime 정본과 각 versioned 보고서에 별도로 고정한다. 현재 계산기 권위는 부모 v9 `data/reports/saju_runtime_conformance/v1.7.0/build-9f1784e74a4e/`와 단일 일진 v10 `data/reports/saju_runtime_conformance/v1.8.0/build-46185262164f/`를 함께 따른다.

v1.11의 원국·단일 날짜 명시 연결은 완료된 부모 구현이다. AES-GCM state·공개 allowlist·snapshot hash·자동 Grounding Gate를 적용하며 날짜 변경은 기존 대화에 덮어쓰지 않는다. 최신 운영·후보 commit과 검증 범위는 [현재 기준선](saju_product_roadmap/00-current-baseline.md)을 따른다. 이 통합은 strict/full runtime, Phase 6, v3.1, 추가 학습과 모델 승격을 승인하지 않는다.

다음 실행은 [50 진단](saju_product_roadmap/50-automatic-model-evaluation.md)의 A→B→C→D다. 큰 동일 계열 Instruct 기본 모델 비교를 필수로 포함하며, 결과에 따라 60/70을 별도 결정한다. 최상단 01·02·03은 요약·연결 문서이고 별도 실행 정본이 아니다.

## 현재 평가 기본값

품질 Gate와 baseline 결정은 정본 계약에 고정된 자동 기술지표만 사용한다. 계약 밖 의미 품질은 `not_measured`이며 별도 사용자 작업이나 Phase 완료 blocker로 바꾸지 않는다. 과거 versioned config·report·화면 자산에 남은 필드와 workflow는 당시 이력 보존용이고 현재 포인터·Gate·후속 지시가 아니다.

Phase 6은 이미 단회 소비됐으므로 재실행하지 않고 다음 명령으로 완료 상태만 검증한다.

```bash
.venv/bin/python -m scripts.evaluation.phase6_completed_verify
```

AI Hub 원문·내부 ID·private 결과·checkpoint는 계속 Git과 공개 보고서에서 제외한다. Phase 6의 공개 근거는 `data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2/`의 집계 3파일만 사용한다. 이후 대화 진단은 각 정본에 연결된 별도 공개 aggregate·manifest를 따른다.

## 진행 기록

### 2026-09-05 — 후속 정본 연결 갱신

- 완료된 모델·Runtime·진단과 현재 서비스/후보를 분리하고 원인 분리 우선 순서를 반영했다. Phase 6·생성된 현황판·불변 report는 변경하지 않았다.
- 검증 명령·결과와 다음 50-A 작업은 [재정렬 기록](../history/2026-09-05-model-cause-roadmap.md)을 따른다.
