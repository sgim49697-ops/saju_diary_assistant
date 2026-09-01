<!-- phase-6-evaluation-v2-decision.md - K0·KI10·KI20의 단회 자동 기술평가와 baseline 결정을 고정한다. -->

# Phase 6. 사후 자동 기술평가·baseline 결정

| 항목 | 값 |
|---|---|
| 실행 상태 | 완료 · `eval-e8630962cab2` · `AUTOMATED_REPAIR_REQUIRED` |
| 선행 Phase | Phase 5 완료 |
| 입력 | K0-INSTRUCT·KI10-MIX-v2·KI20-MIX-v2, 공개 진단 suite, sealed blind 500행 |
| 출력 | 자동 기술지표, 단회 blind 집계, baseline 결정, private repair 순위 |
| 완료 Gate | 동일 fingerprint 단회 실행·검증과 자동 결정 기록 완료 |
| 정본 계약 | `phase6-technical-evaluation-v1.0.0.json` |

## 기본 평가 정책

모든 품질 Gate와 baseline 결정은 저장소 내부에서 재현 가능한 자동 기술지표로만 수행한다. 이 계약 밖의 별도 평가 작업은 현재 또는 미래의 승인 조건이나 사용자 후속 작업으로 만들지 않는다.

고정 계약으로 판정할 수 없는 해석 의미·취향·학파 의존 품질은 `not_measured`로 기록한다. `not_measured`는 자동 통과가 아니고 해당 품질을 주장하지 않는다는 뜻이며, Phase 완료나 baseline 기술 비교를 차단하지 않는다. 모델 출력과 reference의 문자열 유사도는 품질 점수로 사용하지 않는다.

이 정책은 Phase 0~6, MIX20K 후속 계획과 현재 프로젝트 상태의 기본값이다. 과거 versioned 산출물에 남은 당시 필드는 불변 이력일 뿐 현재 Gate나 작업 지시가 아니다.

## 목적과 범위

K0·KI10·KI20을 같은 입력·chat template·greedy 설정으로 비교해 학습 전후 기술적 회귀와 실패 유형을 분리한다. Phase 6 결과는 baseline 비교를 닫지만 다음 작업을 승인하지 않는다.

- release 승인
- 앱·runtime 연결
- MIX20K-v3.1 생성
- 추가 학습·checkpoint 생성
- production 승격 또는 의미 정확성 주장

## 평가 대상과 고정 설정

| 모델 | 역할 | checkpoint |
|---|---|---|
| `K0-INSTRUCT` | 비교 기준 | Kanana 고정 Instruct snapshot |
| `KI10-MIX-v2` | 유지 후보 | `run-e6b712f0d45e/final` |
| `KI20-MIX-v2` | 선택 후보 | `run-1f5d732cae67/final` |

세 모델의 `model.safetensors`, config, tokenizer와 chat template SHA-256을 계약에 고정한다. 학습 당시 token fingerprint와 같은 비교를 위해 `fix_mistral_regex=false`를 명시적으로 고정하고, 권장 보정은 별도 tokenizer·data·run version에서만 검증한다. 모델은 한 번에 하나만 GPU에 올리고 순서는 K0→KI10→KI20이다.

```yaml
do_sample: false
num_beams: 1
nonsealed_max_new_tokens: 128
stateful_max_new_tokens: 256
blind_max_new_tokens: 512
likelihood_batch_size: 8
blind_generation_batch_size: 1
dtype: bfloat16
attention_backend: sdpa
local_files_only: true
```

## blind 이전 자동 진단

blind 결과에 맞춰 scorer를 바꾸지 않도록 먼저 공개·비봉인 suite에서 구현을 검증한다.

1. Gate v2 1,045case를 K0·KI10·KI20에 같은 설정으로 채점한다.
2. 공개 합성 stateful 100case를 같은 안내 prompt와 설정으로 채점한다.
3. scorer reference fixture와 deliberate mutation이 각각 100% 통과·거부되는지 확인한다.
4. 모델·입력·코드 hash, CUDA runtime, native JIT header와 단일 GPU 상태를 검증한다.

진단 품질 결과는 blind 실행 여부를 조건부로 바꾸지 않는다. hash·membership·scorer·runtime 무결성 실패만 실행을 차단한다.

## sealed blind 단회 계약

고정 입력은 `evaluation-split/v1.0.0/build-a5a04ab96594`의 500행·350 component다.

| 축 | 행 | component |
|---|---:|---:|
| Nemotron | 50 | 50 |
| BaZi | 200 | 50 |
| AI Hub 단일턴 | 50 | 50 |
| AI Hub 멀티턴 | 50 | 50 |
| YEJI | 50 | 50 |
| 결정론 QA | 50 | 50 |
| 사주 일기 bridge | 50 | 50 |

- `validate-contract`, dry-run과 `preflight`는 payload를 열지 않는다.
- 실행에는 `PHASE6_TECHNICAL_BLIND=K0_KI10_KI20_V1`이 필요하다.
- source build 내부와 run root에 시작 marker를 payload 접근 전에 `O_EXCL`로 기록한다.
- 시작 marker가 있으면 blind는 사용된 것이다. 중단 후에는 같은 config·scorer·모델 hash·commit에서만 append-only로 재개한다.
- 다른 fingerprint, 두 번째 평가 run, 기존 결과 덮어쓰기는 fail-closed로 차단한다.
- private 경로는 `0700` directory와 `0600` file을 유지한다.
- 공개 보고서에는 prompt·reference·출력·record/component 식별자·private 경로를 포함하지 않는다.

## 자동 기술지표

### Teacher-forced likelihood

reference assistant token만 대상으로 NLL과 token accuracy를 계산한다. 행 평균→component 평균→축 평균→7축 macro 순서로 집계하고 token-weighted 수치는 진단용으로 별도 표시한다. 이 값은 held-out reference 적합도이며 사실·의미 진실성 점수가 아니다.

### Greedy 생성 계약

| 지표 | 판정 |
|---|---|
| 정상 생성 | 비어 있지 않고 special/control token이 없으며 512-token 상한 강제 종료가 아님 |
| 외국어 문장 | 가나·장문 영어·허용 목록 밖 한자 문장 탐지 |
| 태스크 혼동 | 공감 축에서 사주 용어·간지를 새로 생성했는지 |
| 입력 사실 위반 | prompt에 없는 간지·기둥 값을 출력했는지 |
| 정형 사실 | 결정론 QA의 기둥·오행·지장간·십신 구조 일치 |
| 규칙 판정 | YEJI 규칙명과 긍정/부정 극성 일치 |
| 중대 오류 | 안전 위반, 민감 entity, 근거 없는 전체 날짜·네 기둥 생성 |
| 반복·길이 | 문장/4-gram 반복, 문자·token 수와 상한 도달 |
| 의미 품질 | `not_measured` |

정형 계약이 없는 분모는 `not_applicable`로 표시하며 성공이나 실패로 바꾸지 않는다. reference exact/normalized overlap은 측정하지 않는다.

## 자동 baseline Gate

```text
정상 생성률                 >= 98%
공감↔사주 태스크 혼동        <= 5%
입력 사실 위반              <= 10%
외국어 문장 혼입             <= 3%
결정론 사실 계약             >= 90%
YEJI 규칙 계약              >= 90%
비봉인 handoff              >= 95%
special/control             == 0건
중대 안전 위반               == 0건
민감 entity                 == 0건
근거 없는 전체 날짜          == 0건
임의 네 기둥                 == 0건
```

KI20은 위 Gate와 함께 KI10 대비 다음 no-regression을 만족해야 한다.

- 높은 값이 좋은 비율: KI20 ≥ KI10 − 2%p
- 낮은 값이 좋은 비율: KI20 ≤ KI10 + 2%p
- zero-tolerance 지표: KI20도 정확히 0건
- NLL: 보고만 하고 Gate로 사용하지 않음

결정은 다음 세 값 중 하나로 자동 확정한다.

```text
KI20 통과 + no-regression  → KI20_TECHNICAL_BASELINE_SELECTED
그 외 KI10 통과           → KI10_TECHNICAL_BASELINE_RETAINED
둘 다 실패                → AUTOMATED_REPAIR_REQUIRED
```

K0는 비교 기준이며 선택 후보가 아니다. Phase 6은 평가·집계·결정 산출물이 검증되면 결과의 합격 여부와 무관하게 완료된다.

## 실행 결과

봉인 입력은 실행 commit `8819b1753dfcf24367a902e3fa6b5fc0a94fbc0b`에서 세 모델에 한 번만 소비됐고 marker는 `spent_completed`다. 500행·350 component, 공개 진단 1,145case, KI20의 MIX20K-v2 20,000행 private NLL 순위를 모두 완료했다.

| 모델 | NLL | token accuracy | 정상 생성 | 결정론 | 규칙 | 비봉인 handoff | 임의 네 기둥 |
|---|---:|---:|---:|---:|---:|---:|---:|
| K0 | 3.563615 | 42.3471% | 66.2857% | 20% | 10% | 86% | 19건 |
| KI10 | 0.846031 | 80.4583% | 100% | 40% | 36% | 14% | 43건 |
| KI20 | 0.779496 | 81.3505% | 100% | 56% | 38% | 50% | 47건 |

KI20은 KI10보다 NLL, 결정론, 규칙, handoff가 개선됐지만 결정론 90%, 규칙 90%, handoff 95%, 임의 네 기둥 0건 Gate에는 미달했다. 따라서 자동 결정은 `AUTOMATED_REPAIR_REQUIRED`다. `phase6_completed=true`이며 결과가 모델 승격을 승인하지는 않는다.

## 자동 repair 순위와 runtime 문맥

- KI20로 현재 MIX20K-v2 20,000행의 assistant-token NLL을 계산한다.
- private 경로에는 record별 순위를 두고 공개 경로에는 축별 p50/p90/p95/p99/max만 둔다.
- blind 500행과 그 식별자는 repair 입력에 사용하지 않는다.
- runtime conformance v8 `build-8bd88d6db03a`의 통과·strict release 차단 상태를 별도 문맥으로 연결한다. runtime 결과를 모델 점수에 합치지 않는다.

## 실행과 검증

```bash
.venv/bin/python -m scripts.evaluation.phase6_completed_verify
```

봉인 입력은 이미 단회 소비됐으므로 `execute --execute`를 다시 실행하지 않는다. 사후 검증기는 marker가 고정한 실행 commit의 코드·테스트 blob 전체, 현재 실행 코드 hash, public/private manifest, source mode·hash와 미승인 경계를 함께 확인한다. 완료 상태용 테스트 변경은 실행 commit blob 검증을 유지한 채 허용하지만 현재 실행 코드 변경은 거부한다.

GPU 실행 동안 일시 중지했던 dashboard service는 같은 unit·run·port·origin 옵션으로 복구했다. local endpoint와 기존 Quick Tunnel endpoint의 HTTP 200을 확인했다.

검증 범위는 strict JSON 중복 key, 경로 이탈·symlink·mode, hash·모델 membership, marker 재개, 축/component 분모, 0분모, 공개 누출, scorer mutation, 전체 unittest·Ruff·Phase 1 source verify·runtime conformance v8·`git diff --check`다.

## 산출물

```text
runs/PHASE6-TECHNICAL/v1.0.0/eval-<fingerprint>/
data/reports/saju_1b_baseline/phase6-technical/v1.0.0/eval-<fingerprint>/
  aggregate.json
  decision.md
  build_manifest.json
```

## 완료 Gate

- [x] 자동 평가 기본 정책과 scorer를 blind 접근 전에 고정했다.
- [x] 단회 marker·동일 fingerprint 재개·공개 누출 차단 테스트를 추가했다.
- [x] 세 모델의 비봉인 1,045case와 stateful 100case를 완료했다.
- [x] sealed blind 500행을 세 모델에 단 한 번 실행했다.
- [x] component→axis macro와 자동 baseline 결정을 검증했다.
- [x] MIX20K-v2 20,000행 private repair 순위를 생성했다.
- [x] 현재 상태·registry·진행 기록을 결과에 맞춰 갱신했다.
- [x] release·앱 연결·v3.1·추가 학습 미승인 상태를 유지했다.

## 진행 기록

- 2026-09-01
  - 작업 요약: `eval-e8630962cab2`로 Phase 6 자동 기술평가를 완료하고 `AUTOMATED_REPAIR_REQUIRED`를 확정했다.
  - 변경 범위: K0·KI10·KI20 비봉인 1,145case, sealed blind 500행·350 component 단회 실행, KI20 MIX20K-v2 20,000행 private NLL 순위와 집계 전용 공개 보고서를 생성했다. release·앱 연결·MIX20K-v3.1 생성·추가 학습은 수행하지 않았다.
  - 검증: 단회 marker `spent_completed`, 실행 commit·동결 구현·모델·입력 hash, record→component→axis macro, 공개 누출 차단과 public/private manifest를 사후 검증했다. runtime conformance v8 전수 재현은 같은 `build-8bd88d6db03a`를 반환했고 Ruff, 두 환경 package check, Phase 1 원천 전수 verify와 저장소 전체 unittest 454건을 통과했다. 공개 보고서는 원문·출력·식별자·private 경로를 포함하지 않는다.
  - 디버깅: 실행 전에는 marker 부재를 확인하던 dry-run 회귀를 완료 후에는 기존 marker byte·mtime 불변 확인으로 전환했다. 사후 검증기는 실행 commit의 코드·테스트 blob 전체를 계속 고정하고 현재 실행 코드는 동일 hash를 강제하되 완료 상태를 확인하는 테스트만 갱신할 수 있게 분리했다.
  - 결과: KI20은 NLL 0.779496, 결정론 56%, 규칙 38%, handoff 50%였고 임의 네 기둥 47건 때문에 자동 Gate를 통과하지 못했다. Phase 6 자체는 완료됐으며 다음 허용 작업은 자동 repair 설계뿐이다.

- 2026-09-01
  - 작업 요약: 기존 Phase 6의 비재현 판정·50K 분기 조건을 폐기하고 저장소 내부 자동 기술평가와 `not_measured` 정책으로 정본을 교체했다.
  - 변경 범위: K0/KI10/KI20 동일 설정, blind 500행 단회 marker, teacher-forced likelihood, greedy 기술지표, no-regression과 세 가지 baseline 결정을 계약으로 고정했다.
  - 검증: `uvx ruff check scripts tests`, 전체 `unittest` 449건, 두 Python 환경의 `uv pip check`, Phase 1 source `validate-contract`·`verify`, runtime conformance v8 3건과 `git diff --check`를 통과했다. scorer reference·mutation, component/axis 집계, 0분모, marker resume·다른 fingerprint 차단, dry-run 무접근과 공개 누출도 포함한다.
  - 디버깅: 첫 비봉인 실행 중 Transformers regex 경고를 재확인했다. 비봉인 1,145개 중 보정 시 token ID가 달라지는 320개를 확인하고, 기존 Phase 5 학습 계약과 같은 `fix_mistral_regex=false`를 Phase 6에도 명시적으로 pin했다. 봉인 소비 marker 생성 전 실행을 중단했으므로 blind 접근·실행 횟수는 0을 유지한다.
  - 당시 상태: 계약 체크포인트까지만 완료한 기록이며, 실행은 위 `eval-e8630962cab2` 완료 기록으로 대체됐다.

- 2026-08-30
  - 작업 요약: 비봉인 공개 합성 stateful 100건에서 입력 누적·재질문·runtime handoff 실패 유형을 분리했다. 이 결과는 source blind를 대체하지 않는다.
  - 검증: 10개 층 각 10건, fixture 통과·의도 변이 거부와 private/public 분리를 확인했다.

- 2026-08-29
  - 작업 요약: evaluation split v1.0~v1.2에서 개발·봉인·외부 conformance 역할과 350 component/500행 membership을 고정했다.
  - 검증: train/development/blind component·record·content hash overlap 0과 `blind_source_test_inspected=false`를 확인했다.
