# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본
- `mix20k_v3_repair_plan.md`: 외부 MIX20K-v3 후보의 감사·자동 보정·학습 차단 정본
- `saju_runtime_calculator_adoption.md`: 한국 만세력 계산 core·공식 conformance·v3.1 이관을 결정하는 runtime 정본
- `grounded_dialogue_eval_plan.md`: 계산기 연결 대화의 자동 사실·상태·장문 진단 정본

종합 가이드와 정본이 충돌하면 해당 workstream 정본을 따른다. 학습 Phase·현재 모델 상태는 `saju_1b_10k_20k_baseline/README.md`, v3 후보는 `mix20k_v3_repair_plan.md`, 계산기·공식 근거·release 경계는 `saju_runtime_calculator_adoption.md`, 계산기 연결 대화 진단은 `grounded_dialogue_eval_plan.md`가 우선한다.

## 현재 정본 상태

| workstream | 현재 상태 | 승인 경계 |
|---|---|---|
| 10K/20K baseline | Phase 0~6 완료, `eval-e8630962cab2` 단회 자동 평가 완료 | `AUTOMATED_REPAIR_REQUIRED`, production 금지 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` 기술 후보·비학습 preflight 완료 | canonical 3,800행·다양성·state/grounding·serving 자동 blocker, v3.1·학습 금지 |
| 만세력 runtime | v1.3 후보를 보존하고 과거 공식 원국 전용 v1.4·conformance v9 `build-9f1784e74a4e`·release `saju-runtime-release-v1.4.0-63dc8d398e90` 검증 완료 | chart만 제한 승인·feature 기본 off, strict/full Gate·period·production 앱 연결·v3.1 차단 |
| 계산기 연결 대화 | 기존 500건을 `eval-562c07d0e2e6`으로 재채점하고 2,048↔3,584 장문 200건 `eval-56d1357560d5` 완료 | 두 장문 arm 자동 목표 통과·3,584 상한 유지 후보, 전체 baseline·release·학습 권한 불변 |

루트 [`PROJECT_STATUS.html`](../../PROJECT_STATUS.html)은 Phase 6·대화 진단까지의 공개 집계를 `project-status/v1.3.0/build-38b9ca77ce45`로 보여준다. 이후 완료한 후보 화면 진단과 chart-only v1.4 release는 모델·승격 상태를 바꾸지 않으므로 runtime 정본과 각 versioned 보고서에 별도로 고정한다. 계산기 최신 상태는 `data/reports/saju_runtime_conformance/v1.7.0/build-9f1784e74a4e/`를 따른다.

과거 공식 근거 전용 후보 화면은 기존 dashboard·모델 context와 분리된 로컬 진단 도구다. 이후 생성한 v1.4 release도 앱에 연결하지 않았으며 production 앱, context 증설, v3.1, 추가 학습과 모델 승격을 승인하지 않는다.

## 현재 평가 기본값

품질 Gate와 baseline 결정은 정본 계약에 고정된 자동 기술지표만 사용한다. 계약 밖 의미 품질은 `not_measured`이며 별도 사용자 작업이나 Phase 완료 blocker로 바꾸지 않는다. 과거 versioned config·report·화면 자산에 남은 필드와 workflow는 당시 이력 보존용이고 현재 포인터·Gate·후속 지시가 아니다.

Phase 6은 이미 단회 소비됐으므로 재실행하지 않고 다음 명령으로 완료 상태만 검증한다.

```bash
.venv/bin/python -m scripts.evaluation.phase6_completed_verify
```

AI Hub 원문·내부 ID·private 결과·checkpoint는 계속 Git과 공개 보고서에서 제외한다. 공개 가능한 현재 결과는 `data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2/`의 집계 3파일만 사용한다.
