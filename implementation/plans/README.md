# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본
- `mix20k_v3_repair_plan.md`: 외부 MIX20K-v3 후보의 감사·자동 보정·학습 차단 정본
- `saju_runtime_calculator_adoption.md`: 한국 만세력 계산 core·공식 conformance·v3.1 이관을 결정하는 runtime 정본

종합 가이드와 정본이 충돌하면 해당 workstream 정본을 따른다. 학습 Phase·현재 모델 상태는 `saju_1b_10k_20k_baseline/README.md`, v3 후보는 `mix20k_v3_repair_plan.md`, 계산기·공식 근거·release 경계는 `saju_runtime_calculator_adoption.md`가 우선한다.

## 현재 정본 상태

| workstream | 현재 상태 | 승인 경계 |
|---|---|---|
| 10K/20K baseline | Phase 0~6 완료, `eval-e8630962cab2` 단회 자동 평가 완료 | `AUTOMATED_REPAIR_REQUIRED`, production 금지 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` 기술 후보·비학습 preflight 완료 | canonical 3,800행·다양성·state/grounding·serving 자동 blocker, v3.1·학습 금지 |
| 만세력 runtime | Skyfield v1.3 candidate와 conformance v8 `build-8bd88d6db03a` 통과 | strict Gate·release·앱 연결 차단, 결과는 `HARD_CANDIDATE` |

루트 [`PROJECT_STATUS.html`](../../PROJECT_STATUS.html)은 위 세 workstream의 공개 가능한 현재 집계를 보여준다. 과거 진행 기록과 versioned config/report는 당시 판단의 불변 이력이며 현재 포인터로 읽지 않는다.

## 현재 평가 기본값

품질 Gate와 baseline 결정은 정본 계약에 고정된 자동 기술지표만 사용한다. 계약 밖 의미 품질은 `not_measured`이며 별도 사용자 작업이나 Phase 완료 blocker로 바꾸지 않는다. 과거 versioned config·report·화면 자산에 남은 필드와 workflow는 당시 이력 보존용이고 현재 포인터·Gate·후속 지시가 아니다.

Phase 6은 이미 단회 소비됐으므로 재실행하지 않고 다음 명령으로 완료 상태만 검증한다.

```bash
.venv/bin/python -m scripts.evaluation.phase6_completed_verify
```

AI Hub 원문·내부 ID·private 결과·checkpoint는 계속 Git과 공개 보고서에서 제외한다. 공개 가능한 현재 결과는 `data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-e8630962cab2/`의 집계 3파일만 사용한다.
