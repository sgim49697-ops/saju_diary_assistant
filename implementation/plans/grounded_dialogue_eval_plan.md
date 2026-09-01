<!-- grounded_dialogue_eval_plan.md - 계산기 연결 후 K0·KI20의 사실 충실도와 슬롯 추출 손실을 분리 측정하는 진단 계획. -->

# 계산기 연결 grounded dialogue 진단

- 문서·평가 버전: `grounded-dialogue-eval-v0.1.0`
- 구현 상태: **구현 완료, GPU 진단 미실행**
- 성격: 공개 합성 100건을 쓰는 비봉인·진단 전용 inference lane
- 권한: runtime release, 앱 연결, 학습, 모델 승격을 승인하지 않음

## 1. 질문과 결론 경계

기존 stateful Gate에서 KI20의 `required_handoff_action`은 14/100이었다. 이 수치는 모델이 계산기 handoff를 잘 요청하는지를 보여 주지만, 계산된 구조화 사실을 받은 뒤 그 사실을 얼마나 충실하게 사용하는지는 보여 주지 않는다.

이 진단은 다음 두 질문을 분리한다.

1. 동일한 구조화 사실과 production prompt를 줄 때 K0와 KI20의 사실 충실도가 다른가?
2. 완전한 oracle 슬롯 대신 규칙 또는 KI20 narrow 추출을 붙이면 end-to-end 품질이 얼마나 손실되는가?

완료 여부와 목표 달성 여부도 분리한다. 고정 실행이 정상 종료되면 `diagnostic_completed=true`이며, 자동 지표 임계를 모두 만족했는지는 별도 `diagnostic_target_met`으로 기록한다. 목표 미달이어도 결과를 숨기거나 실행 실패로 바꾸지 않는다.

자연스러움과 의미 품질은 이 버전에서 `not_measured`다. 저장소 내부 자동 기술 지표 외의 표본 Gate를 추가하지 않는다.

## 2. 현재 저장소 계약 재사용

브랜치 초안의 별도 `scripts/runtime/dialogue` FSM과 `configs/runtime/dialogue` 정책은 제거했다. 저장소에는 다음 정본이 이미 있으므로 진단도 이를 직접 사용한다.

- 구조화 intake FSM: `scripts/runtime/intake_fsm.py`
- FSM 계약: `configs/runtime/intake_fsm-v1.1.0.json`
- session schema: `configs/runtime/session_state_schema_v2.1.0.json`
- 후보 계산기: `scripts/runtime/calculation/engine_v1_3.py`
- model-visible allowlist: `scripts/runtime/saju_contract.py`의 `project_model_visible_tool_result`
- production prompt: `configs/runtime/production_system_prompt_v1.txt`

진단 흐름은 다음과 같다.

```text
공개 합성 user turn
  -> oracle | rule | KI20 model_narrow 슬롯 추출
  -> 현재 intake FSM의 구조화 event
  -> call_chart 결정이 난 경우에만 Skyfield v1.3 후보 계산
  -> model-visible tool result + 동일 production prompt
  -> K0 또는 KI20 greedy 응답
  -> 사실 충실도·재질문·안전 자동 채점
```

FSM이 `call_chart` 결정을 내릴 수 있도록 앱 사전조건은 진단 fixture에서만 모두 `true`로 둔다. 이 값은 실제 앱 readiness를 뜻하지 않는다.

Skyfield v1.3 결과는 계속 `status=partial`, `fact_authority=HARD_CANDIDATE`다. 모델에는 allowlist projection만 전달하며, 앱 FSM이 승인하는 `HARD_GT` 또는 `POLICY_BOUND_RULE` chart result로 위장하지 않는다. 후보 결과를 `session_state.chart`에 넣지 않는 것을 코드와 테스트에서 확인한다.

`structured_chart_ready` 10건은 suite가 이미 제공한 공개 합성 `verified_runtime` 사실을 `HARD_GT` 입력 fixture로 투영한다. 이 경우 출생 슬롯을 새로 만들거나 후보 계산기를 다시 부르지 않는다.

## 3. 고정 suite와 슬롯 oracle

입력은 Phase 5의 공개 합성·비봉인 `dev_cases.jsonl` 100건을 hash로 고정해 재사용한다. 원문, case ID, 모델 출력은 공개 report에 넣지 않는다.

- 10 strata × 10건
- 총 사용자 turn 120개
- restricted source와 개인정보 없음
- 입력 파일 mode `0600`
- SHA-256 `a153801f4b81af1e78ae7608c30212d389c23f972a3c4c07630c1a6d64e5a763`

Gold 슬롯은 원문 parser의 결과를 답으로 재사용하지 않는다. `case_id`의 stratum·index와 고정 템플릿 의미에서 독립적으로 재구성한 뒤 현재 FSM event로 적용한다.

현재 v2.1 의미에 맞춘 핵심 처리는 다음과 같다.

- 양력은 `leap_month=null` 결정론적 기본값을 유지한다.
- 음력 평달·윤달은 각각 boolean으로 확정한다.
- `HH시 MM분`은 `HH:MM`, `time_precision=exact`로 정규화한다.
- 오전 7~9시, 저녁 6~8시처럼 같은 날짜 안에서 안전하게 표현되는 범위는 `time_precision=range`로 보존한다.
- 오전·오후가 불명확하거나 새벽·정오 전후처럼 안전한 경계를 만들 수 없는 표현은 슬롯을 추측하지 않는다.
- 명시적 시간 미상은 `set_time_unknown` event로 처리한다.
- 해외 출생이지만 도시·시간대가 없는 경우 대한민국 출생지로 추측하지 않는다.
- 누적 대화의 기존 날짜·장소를 보존하고 명시적 날짜 정정은 `correct_slot` event로 적용한다.

규칙 추출기는 이 고정 100건에서 최종 slot state, 미상 표식, 시간 의미가 oracle과 100% 일치해야 GPU 실행 전 harness 무결성 Gate를 통과한다.

## 4. KI20 model-narrow 추출 계약

`model_narrow`는 K0와 비교하지 않고 KI20만 사용한다. 도구 호출 시점이나 다음 행동을 모델에게 묻지 않고, 현재 user turn에 명시된 출생 슬롯만 JSON으로 한 번 추출한다.

- greedy 1회, retry 0회
- markdown fence나 설명 금지
- 최상위 key는 `updates`, `explicit_unknown_fields`만 허용
- 업데이트 허용값은 날짜, 양·음력, 윤달, 정확 시각, 안전한 시간 범위, 대한민국 출생 도시뿐
- action, tool status, ID, provenance, hard facts, 추측값은 금지
- normalize한 날짜·시간·도시는 현재 발화 surface로 다시 검증
- JSON 오류, 중복 key, 추가 key, 모순값은 수정하지 않고 해당 turn의 invalid extraction으로 기록
- 검증된 값만 구조화 FSM event로 변환하며, 다음 action은 FSM이 결정

model-narrow 슬롯 지표는 진단 결과다. 이 버전에는 합격 임계를 두지 않는다.

## 5. arm과 한 변수 대조

모든 arm은 동일한 production prompt, runtime context, candidate runtime, `max_new_tokens=256`, greedy 계약을 사용한다.

| arm | 응답 모델 | 슬롯 추출 | 최대 입력 token |
|---|---|---|---:|
| `R0_KI20_ORACLE_768` | KI20 | oracle | 768 |
| `R1_KI20_ORACLE_2048` | KI20 | oracle | 2,048 |
| `R2_K0_ORACLE_2048` | K0 | oracle | 2,048 |
| `R3_KI20_RULE_2048` | KI20 | rule | 2,048 |
| `R4_KI20_MODEL_NARROW_2048` | KI20 | KI20 model-narrow | 2,048 |

| 대조 | 답하는 질문 |
|---|---|
| R0 → R1 | 입력 예산만 늘렸을 때의 효과 |
| R2 → R1 | 20K fine-tuning의 응답 효과 |
| R1 → R3 | rule 추출을 붙인 end-to-end 손실 |
| R1 → R4 | KI20 narrow 추출을 붙인 end-to-end 손실 |
| R3 → R4 | 두 실제 추출기의 차이 |

기존 초안의 A0와 A1은 입력 예산과 system prompt를 함께 바꿔 효과를 분리할 수 없었다. 현재 계약은 모든 대조에서 한 축만 바꾼다.

총 응답 생성은 5 arm × 100건 = 500건이다. model-narrow arm은 별도로 사용자 turn 120개를 추출한다.

## 6. prompt와 token 정책

모델 native context 4,096 안에서만 실행하며 YaRN 확장 구간은 사용하지 않는다.

- R0 입력 상한 768, 나머지 2,048
- 응답 상한 256
- `input + output <= 4,096`
- system prompt, 완전한 `<runtime_context>`, 현재 user turn은 항상 보존
- 초과 시 가장 오래된 완전한 `user + assistant` 쌍부터 제거
- runtime context 내부나 현재 user turn 일부를 자르지 않음
- 최소 prompt도 상한을 넘으면 해당 case를 조용히 축약하지 않고 실패 처리

768은 기존 단발 학습 분포 재현용이고 2,048은 대화·구조화 사실을 담는 진단 예산이다. 이 비교는 재학습을 수행하지 않는다.

## 7. 자동 지표

응답은 다음을 구조화 사실 기준으로 채점한다.

- `fabricated_pillars`: hard facts에 없는 유효 60갑자 표기. 한자와 문맥상 한글 표기를 모두 검사
- `unknown_hour_violations`: hour가 없거나 null인데 시주 간지를 단정
- `fact_contradictions`: 년·월·일·시주, 일간 stem·오행, 표면 오행 개수를 다른 값으로 단정
- `false_completion`: blocked/error/partial인데 제한 표현 없이 분석·검증·확정을 완료했다고 주장
- `provided_field_reask`: FSM confirmed 또는 explicit unknown 슬롯을 다시 요청
- `nonempty`, `max_token_hit`, `severe_safety`

슬롯 추출은 다음을 별도로 집계한다.

- oracle 최종 state exact match
- missing, hallucinated, mismatched slot
- invalid extraction
- exact/range/unknown 시간 의미
- 누적 대화 날짜 correction

자동 목표는 fabricated pillar, unknown hour, contradiction, false completion, severe safety 각 0건, 재질문 5% 이하, nonempty 100%다. 이 목표는 `diagnostic_target_met`만 결정하며 모델 승격이나 release Gate로 사용하지 않는다.

## 8. 실행·산출물·재개 계약

CLI는 다음 네 명령을 제공한다.

```bash
.venv-data/bin/python -m scripts.evaluation.grounded_dialogue validate-contract
.venv-data/bin/python -m scripts.evaluation.grounded_dialogue plan
.venv-data/bin/python -m scripts.evaluation.grounded_dialogue execute
GROUNDED_DIALOGUE_EVAL=K0_KI20_V1 \
  .venv-data/bin/python -m scripts.evaluation.grounded_dialogue execute --execute
.venv-data/bin/python -m scripts.evaluation.grounded_dialogue verify
```

실제 실행은 확인 환경변수, 단일 CUDA GPU, 다른 compute process 없음, 최소 free VRAM 12,000 MiB, 모든 입력·모델 hash 일치를 요구한다. `execute`만 호출하면 dry-run이며 생성과 쓰기를 하지 않는다.

원시 추출·응답은 다음 private root에 `0700/0600`으로 저장한다.

```text
runs/GROUNDED-DIALOGUE/v0.1.0/eval-<fingerprint>/
```

arm 결과는 완결된 100건 파일 단위로 불변 저장한다. 중단 후 같은 build를 재실행하면 완결된 arm은 hash·identity를 검증해 재사용하고, 미완료 arm만 다시 실행한다. 같은 build 동시 실행은 lock으로 거부한다.

공개 root에는 집계와 build manifest 두 파일만 둔다.

```text
data/reports/saju_1b_baseline/grounded-dialogue/v0.1.0/eval-<fingerprint>/
  aggregate.json
  build_manifest.json
```

공개 산출물에는 prompt, 응답, case ID, private path, 모델 파일 path를 넣지 않으며 재귀 leak scan을 통과해야 한다.

## 9. 범위 밖

- 실제 GPU 진단 실행과 결과 해석
- sealed blind 접근 또는 Phase 6 소비 상태 변경
- KI20 weight, checkpoint, run manifest 수정
- candidate runtime을 앱 FSM의 승인된 chart로 저장
- runtime release·feature flag·app binding 변경
- 추가 학습, v3.1 생성, 모델 promotion
- 추가 의미·자연스러움 Gate

## 진행 기록

### 2026-09-01 — 브랜치 초안

- 별도 dialogue FSM, 4개 혼합 arm, 미구현 하네스 시그니처를 추가했다.
- 로컬 통합 전 검토가 필요한 뼈대 상태였다.

### 2026-09-01 — 현재 저장소 기준 구현 확정

- 작업 요약: 중복 FSM을 제거하고 현재 intake FSM·session v2.1·candidate runtime v1.3에 맞춰 5-arm 진단을 완성했다. 규칙·oracle·KI20 narrow 슬롯 추출, prompt 보존 truncation, 구조화 사실 채점, private/public 불변 출력, CLI와 재개 검증을 구현했다.
- 변경 범위: 평가 계약 1건, 진단 패키지 11개 파일, 단위·통합 테스트 1건, 이 plan 문서. 기존 Phase 6와 runtime 구현 파일은 수정하지 않았다.
- 검증 결과:
  - `uv run python -m unittest tests.test_grounded_dialogue_eval -v`: 21건 통과
  - 고정 공개 합성 suite 규칙 추출: 100/100 exact, 시간 의미 100/100, 정정 3/3
  - `uvx ruff check scripts/evaluation/grounded_dialogue tests/test_grounded_dialogue_eval.py`: 통과
  - `uv run python -m scripts.evaluation.grounded_dialogue validate-contract`: 통과
- 실행하지 않은 항목: K0·KI20 GPU 생성 500건과 KI20 narrow 추출 120건. PR 전 구현 검증 범위에서는 의도적으로 실행하지 않는다.
- 남은 후속 작업: GPU가 유휴 상태일 때 확인 환경변수를 명시해 `execute --execute`를 1회 실행하고, 생성된 aggregate의 `diagnostic_target_met` 및 arm contrast를 해석한다.

### 2026-09-01 — Phase 6 정본 통합 및 전체 회귀

- 작업 요약: 최신 `master`의 Phase 6 자동 기술평가 완료 상태를 브랜치에 병합하고, 저장소 내부 자동 기술 지표만 사용하는 현재 문서 계약에 맞춰 진단 범위 표현을 정렬했다.
- 변경 범위: 이 plan 문서의 Gate 경계 표현과 진행 기록. 진단 코드·계약·runtime 동작은 바꾸지 않았다.
- 검증 결과:
  - `.venv-data/bin/python -m unittest tests.test_grounded_dialogue_eval -v`: 21건 통과
  - `.venv-data/bin/python -m scripts.evaluation.grounded_dialogue validate-contract`: `status=valid`, 고정 suite 100건 확인
  - `uvx ruff check scripts tests`: 통과
  - 실제 raw·derived·staging·모델·run 산출물을 연결한 `.venv-data/bin/python -m unittest discover -s tests -q`: 475건 통과
  - `git diff --check origin/master...HEAD`: 통과
- 남은 후속 작업: GPU 진단은 아직 실행하지 않았다. 결과 생성 전까지 runtime release, 앱 연결, 추가 학습, 모델 승격은 계속 미승인이다.
