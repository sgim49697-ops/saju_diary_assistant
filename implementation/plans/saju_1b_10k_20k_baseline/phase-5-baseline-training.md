# Phase 5. 10K·20K Baseline 학습

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 4 완료 |
| 입력 | 선택 길이의 canonical MIX10·MIX20, Instruct snapshot, 승인 config |
| 출력 | KI10·KI20 checkpoint, trainer state, 환경·학습 보고서 |
| 완료 Gate | KI10 재현·재로딩·자동 품질 Gate 완료, 통과한 경우에만 독립 KI20까지 종료 |
| 웹 확인일 | 2026-08-29 |

## 목적

같은 Instruct checkpoint와 동일한 학습 설정에서 데이터량만 10K에서 20K로 늘렸을 때의 효과를 비교한다. Phase 4에서 검증한 길이·template·환경·manifest를 변경하지 않는다.

## 실행 순서

```text
고정 Instruct ──> KI10-MIX-v2, 1 epoch ──> 1,000case 자동 품질 Gate
                                                   ├─ 모든 기준 통과 ─> 고정 Instruct ─> KI20-MIX-v2
                                                   └─ 하나라도 실패 ─> KI20 금지·원인 분석
```

KI20은 KI10 checkpoint에서 시작하지 않는다. KI10의 기술 안정성뿐 아니라 고정된 자동 품질 기준을 모두 통과해야만 실행한다. 실패 기준을 학습 결과에 맞춰 사후 완화하지 않으며 데이터·정책·hyperparameter 변경이 필요하면 새 버전으로 돌아간다.

## 고정 학습 설정

`max_length`는 Phase 4가 선택·승인한 `768`로 고정한다.

```yaml
model_name_or_path: <local-instruct-snapshot>
model_revision: bf4786aa2a1908adce942d53976270132732f720
trust_remote_code: true
model_init_kwargs:
  dtype: bfloat16

bf16: true
fp16: false
tf32: false
per_device_train_batch_size: 1
per_device_eval_batch_size: 1
gradient_accumulation_steps: 8
eval_accumulation_steps: 1
num_train_epochs: 1
max_length: 768
pad_to_multiple_of: 8

optim: paged_adamw_8bit
learning_rate: 8.0e-6
warmup_ratio: 0.03
lr_scheduler_type: cosine
weight_decay: 0.01
max_grad_norm: 1.0

gradient_checkpointing: true
gradient_checkpointing_kwargs:
  use_reentrant: false
use_cache: false
assistant_only_loss: true
packing: false
padding_free: false
loss_type: chunked_nll
shuffle_dataset: true

logging_strategy: steps
logging_steps: 10
logging_first_step: true
logging_nan_inf_filter: false
eval_strategy: steps
eval_steps: 250
save_strategy: steps
save_steps: 250
save_total_limit: 2
save_only_model: false
save_safetensors: true
dataloader_num_workers: 0

torch_compile: false
push_to_hub: false
report_to: none
seed: 42
data_seed: 42
```

`peft_config`와 quantized model loading을 전달하지 않는다. 8-bit는 optimizer state에만 적용하며 모델 전체 파라미터는 BF16 학습 대상이다.

## Phase 5 실행 전 의미 감사·평가·readiness Gate

실제 학습 전에 의미·출처 감사 `v1.0`, 평가 확장 `v1.1`, readiness `v1.2`를 차례로 고정한다. 기존 평가 v1.0의 membership과 봉인 blind bytes는 바꾸거나 읽지 않는다. 이 Gate들은 학습·optimizer·backward를 수행하지 않고 다음만 불변 산출물로 고정한다.

- registry가 품질 보정 Phase 4 v2 canonical을 가리키고 A~E·768·`training_promotion_allowed=true` hash chain이 전부 재검증됨
- `mix10k_v2.jsonl` 10,000행과 `mix20k_v2.jsonl` 20,000행이 7축 고정 수량·strict subset·동일 record hash를 만족함
- 기존 eval70 byte hash를 `dev_monitor_70`으로 보존하고 KI20 leakage component와 겹치지 않음
- 개발 진단 930행, 미사용 reserve의 7축 blind 350 component/500행, 외부 conformance 220행은 학습 loop 입력에서 제외됨
- Python 3.10.12, uv 0.9.26, torch 2.13.0+cu130, Transformers 4.57.6, TRL 1.12.0, bitsandbytes 0.50.2와 `requirements-phase3.lock.txt` SHA-256이 일치함
- 가용 disk가 최소 64GiB이고 KI10·KI20이 모두 고정 Instruct snapshot에서 독립 시작하며, checkpoint가 model·optimizer·scheduler·trainer state를 저장함
- readiness 공개 보고서에는 제한 원문을 넣지 않고 `phase5_training_performed=false`를 유지함
- 20K 전수 의미 감사에서 hard blocker 0, critical/high 0, assistant mask 0행·언어/제어/민감 entity 신규 위반 0을 확인함
- dev reference 중 train assistant와 같은 답변은 별도 집계하되, 반복성이 높은 축에서 reference similarity를 최종 성능 주장으로 쓰지 않음
- Nemotron 페르소나 연결 문구는 별도 50case 비인과 guard로 검사하며 광범위 연결 문구 비율 자체를 인과 오류율로 해석하지 않음

학습 중 loss-only eval은 이 eval70으로 250 optimizer step마다 수행한다. 생성 품질 평가는 Phase 6에서 별도 고정 계약으로 실행하며, eval 결과를 근거로 같은 Run의 hyperparameter나 데이터 비율을 중간 변경하지 않는다.

```bash
.venv/bin/python scripts/training/pretraining_audit.py validate-contract
.venv/bin/python scripts/training/pretraining_audit.py run --execute
.venv/bin/python scripts/evaluation/phase5_split_v1_1.py prepare --execute
.venv/bin/python scripts/training/phase5_readiness_v1_2.py prepare --execute
.venv/bin/python scripts/training/phase5_readiness_v1_2.py verify
```

각 `prepare`/`run`은 기본 dry-run이다. `--execute`가 있어도 평가·KI10/KI20 입력 계약과 공개 요약만 만들며, 실제 학습은 별도 runner와 정확한 `PHASE5_TRAINING=<run-id>` 확인값을 요구한다.

```bash
.venv/bin/python scripts/training/phase5_train.py validate-contract
.venv/bin/python scripts/training/phase5_train.py plan --run-id KI10-MIX-v2
.venv/bin/python scripts/training/phase5_train.py preflight-run --run-id KI10-MIX-v2 --execute
PHASE5_TRAINING=KI10-MIX-v2 .venv/bin/python scripts/training/phase5_train.py train --run-id KI10-MIX-v2 --execute
.venv/bin/python scripts/training/phase5_train.py evaluate-ki10 --execute
```

KI20 명령은 `evaluate-ki10`이 모든 기준을 통과해 `ki20_promotion_allowed=true`를 고정한 뒤에만 실행한다.

## Run 시작 전 동등성 검사

각 Run은 시작 직전 다음을 `run_manifest.json`에 기록한다.

- Git commit SHA와 working tree clean 여부
- Instruct·tokenizer·template SHA-256
- manifest SHA-256, 행 수, selected max length
- Python·torch·CUDA·Transformers·TRL·bitsandbytes 버전
- GPU, driver, 시작 시 가용 VRAM, RAM, disk
- 모든 hyperparameter와 seed
- 부모 checkpoint가 고정 Instruct snapshot인지 여부

KI10과 KI20에서 model·template·hyperparameter·seed가 다르면 시작하지 않는다. 허용되는 차이는 manifest와 output directory뿐이다.

## 학습 중 관찰

TRL이 제공하는 다음 지표를 10 optimizer step마다 저장한다.

- `global_step`, `epoch`, `num_tokens`
- `loss`, `entropy`, `mean_token_accuracy`
- `learning_rate`, `grad_norm`
- GPU memory allocated/reserved와 `nvidia-smi` peak
- system RAM·swap, step time, tokens/sec

다음 상황에서는 즉시 중단하고 마지막 정상 checkpoint를 보존한다.

```text
loss 또는 grad_norm NaN/Inf
CUDA OOM 반복
assistant loss token 수 0
dataset/manifest hash 불일치
예상하지 않은 model parameter freeze
checkpoint 저장 실패 또는 disk 부족
```

단순히 loss 감소가 느리거나 자동평가 품질이 낮다는 이유로 같은 Run의 learning rate·길이·비율을 중간 변경하지 않는다.

## Checkpoint와 재시작

- 250 optimizer step마다 모델·optimizer·scheduler·trainer state를 저장한다.
- 최근 2개 step checkpoint와 final checkpoint를 유지한다.
- 재시작은 Instruct snapshot, manifest, config, package lock, world size가 완전히 같을 때만 허용한다.
- 설정이 다르면 기존 Run을 덮어쓰지 않고 새 Run ID를 만든다.
- final 저장 후 새 process에서 모델·tokenizer를 로드해 deterministic fixture 5개를 생성한다.

## KI10 자동 품질 Gate

KI10 종료 후 Phase 6 봉인 평가를 열기 전에 개발 진단 930case와 별도 페르소나 비인과 guard 50case를 합친 1,000case를 greedy 생성한다. train과 같은 reference 답변은 측정값으로 공개하지만 해당 reference를 그대로 재현했는지는 승격 점수로 쓰지 않는다.

```text
비어 있지 않고 파싱 가능한 출력       >= 98%
special/control token 노출             == 0건
의료·투자·운명 중대 단정               == 0건
외국어 문장 혼입                        <= 3%
target-only 민감 entity                 == 0건
hard fact·지지 정기 정책 충족           >= 90%
신살 조건·긍부정 판정 충족              >= 90%
공감↔사주 태스크 혼동                   <= 5%
명식 누락 handoff                       == 5/5
입력 사실 위반                          <= 10%
페르소나 인과 단정                      <= 10%
```

한 항목이라도 실패하면 KI20을 실행하지 않는다. 이 검사는 전문가 품질 인증이나 최종 일반화 점수가 아니며, KI20에 더 많은 계산량을 투입할 최소 기술·행동 품질 Gate다.

## 완료 Gate

- [ ] KI10이 고정 Instruct snapshot에서 시작해 NaN/Inf/OOM 없이 1 epoch를 끝냈다.
- [ ] KI10 final 모델과 trainer state를 새 process에서 재로딩했다.
- [ ] KI10 run manifest, package lock, log, checkpoint hash를 저장했다.
- [ ] KI10 1,000case 자동 품질 Gate를 고정 기준으로 판정했다.
- [ ] Gate 통과 시에만 KI20을 같은 Instruct snapshot에서 독립 시작했다.
- [ ] 실행된 KI20은 KI10과 manifest·output 경로 외 설정이 같고 재로딩 가능하다.
- [ ] 모델·checkpoint를 공개 저장소나 Hub에 올리지 않았다.

## 산출물

```text
data/derived/saju_1b_baseline/phase5-readiness/v1.2.0/build-<fingerprint>/
├── eval/dev_monitor_70.jsonl
└── run_inputs/

data/reports/saju_1b_baseline/phase5-readiness/v1.2.0/build-<fingerprint>/
├── build_manifest.json
└── readiness_summary.json

runs/KI10-MIX-v2/v1.0.0/run-<fingerprint>/
├── run_manifest.json
├── config.resolved.json
├── trainer_state.json
├── metrics.jsonl
├── checkpoint-*/
├── final/
├── reload_fixtures.jsonl
└── ki10_diagnostic_generations.jsonl

runs/KI20-MIX-v2/v1.0.0/run-<fingerprint>/
└── <동일 구조>
```

## 공식 자료

- [TRL 1.12.0 SFTTrainer](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)
- [Transformers 4.57.6 TrainingArguments source](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/training_args.py)
- [bitsandbytes 0.50.2 8-bit optimizer](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/optimizers)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | TRL SFT loss·logged metrics | masked token NLL과 loss/entropy/token accuracy/grad norm 기록 항목 확인 |
| 2026-08-27 | TRL config 필드 | save/logging/seed/full FT 관련 현재 필드 확인 |
| 2026-08-27 | bitsandbytes 8-bit optimizer | parameter state memory 절감과 activation memory 비절감 한계 확인 |

## 진행 기록

- 2026-08-29
  - 작업 요약: 감사·평가·runner 구현을 한 hash chain으로 묶은 readiness `v1.2.0/build-bffd53a2abb3`을 생성하고 별도 명령으로 재검증했다. 실제 KI10·KI20 학습은 아직 시작하지 않았다.
  - 변경 범위: KI10 baseline만 허용하고 KI20은 자동 품질 Gate 전까지 금지한 registry 포인터, private run input, 공개 readiness summary와 `PROJECT_STATUS.html` 현황판을 고정했다.
  - 검증: readiness private/public manifest `02d5ecc…b551c`/`514bcc5…fe62`, dev monitor byte hash `aa61d2a…bcb31`을 확인했다. 현황 HTML은 snapshot과 byte-identical하고 AI Hub 원문·ID·checkpoint·외부 script를 포함하지 않는다.
  - 남은 이슈·후속 작업: clean commit 뒤 `phase5_train.py preflight-run --run-id KI10-MIX-v2 --execute`로 forward-only loss·GPU/VRAM을 검증한다. 그 결과가 통과하기 전에는 `PHASE5_TRAINING` 실행을 시작하지 않는다.
- 2026-08-29
  - 작업 요약: 고정 구현 commit `1e76232c0c094e6d3c3ed47b253c60f0e1af63b1`에서 평가 v1.1과 20K 의미 감사를 실제 불변 build로 생성·독립 verify했다. 모델 학습·backward·optimizer step은 수행하지 않았다.
  - 변경 범위: `evaluation-split/v1.1.0/build-d2f9e1623e96`은 parent membership과 봉인 bytes를 바꾸지 않고 persona guard 50case·reference overlap 집계만 추가했다. `pretraining-audit/v1.0.0/build-c38926f86a3d`은 canonical MIX20 전체를 공개 집계로만 검사했다.
  - 검증: 기술 hard blocker 0, 데이터 수정 필요 없음, KI10 baseline 허용, 전문가·production 품질 주장 금지, KI20 사전 금지를 재확인했다. sealed blind는 읽지 않았고 private/public manifest bytes를 독립 재생성해 대조했다.
  - 남은 이슈·후속 작업: 새 registry 포인터를 커밋한 clean tree에서 readiness v1.2를 생성한 뒤에만 Phase 5 runner가 KI10을 계획할 수 있다.
- 2026-08-29
  - 작업 요약: 실제 학습 전 의미 감사·평가 확장·readiness v1.2와 KI10/조건부 KI20 Full FT runner를 구현했다. 현재는 구현 체크포인트이며 실제 backward·optimizer step은 수행하지 않았다.
  - 변경 범위: 768·BF16·batch 1·accumulation 8·evaluation batch 1·padding multiple 8·`logging_nan_inf_filter=false`를 고정했다. KI10 1 epoch 1,250 step과 새 process reload, 개발 1,000case 자동 품질 Gate, KI20 base 독립 2,500 step·사전 금지를 코드와 config에 묶었다.
  - 검증: 20K 전수 의미 감사에서 기술 hard blocker 0을 확인했고, 평가 v1.0 membership·blind bytes 비변경과 blind 미열람을 유지했다. 전체 175개 단위 테스트, Ruff, compile, diff check와 부모 Phase 4 계약 검증을 통과했다.
  - 남은 이슈·후속 작업: 학습 전 감사·평가 v1.1·readiness v1.2 build를 clean commit에서 생성·승인한 뒤 KI10 forward-only preflight를 먼저 실행한다. KI20은 KI10 자동 Gate 결과 전까지 `ki20_promotion_allowed=false`다.
- 2026-08-29
  - 작업 요약: 기존 1,000건이 개발 중 노출된 점을 반영해 `evaluation-split/v1.0.0/build-a5a04ab96594`를 생성하고, 이를 포함한 비학습 readiness `v1.1.0/build-201010b37e40`을 생성·독립 재검증했다. 실제 학습은 실행하지 않았다.
  - 변경 범위: eval70은 loss monitor 전용이며 checkpoint 선택·early stopping·최종 성능 주장에 쓰지 않는다. 나머지 source holdout 630+Core Eval 300은 개발 진단, reserve 7축 350 component/500행은 Phase 6 단회 blind, 공개 KASI·정책 fixture 220행은 별도 conformance로 고정한다.
  - 검증: 고정 tokenizer 768 제한, 7축 층화, BaZi 4질문 component, component/record/content hash 누수 0을 통과했다. 외부 fixture는 runtime·학습 Gold를 자동 승인하지 않으며 평가 split public summary에는 raw·ID가 없다.
  - 재현성 보강: 최초 미승인 readiness 시도 `build-69b21cb079a7`은 가용 disk와 Git HEAD 순간값 때문에 작성 직후 byte 재현 검증이 실패해 private/public 경로를 제거했다. disk 임계값·runtime·GPU 검증은 계속 실행하되 순간값 자체는 불변 summary에서 제외하는 회귀 테스트를 추가했다.
  - readiness 검증: KI10 10,000행·KI20 20,000행, 기존 eval70 byte 보존, 단일 RTX 5070 Ti·CUDA 13.0·BF16, 64GiB disk 임계값을 통과했다. private/public manifest는 `4d28b744…db907`/`1205f83a…d1321`, summary는 `e8ed61e3…6c38d`다.
  - 남은 이슈·후속 작업: Phase 5는 실제 학습을 시작하지 않아 계속 `미시작`이다. 사용자가 별도로 승인하면 KI10부터 실행하며 KI20은 KI10 checkpoint를 재사용하지 않는다. `phase5_training_performed=false`다.
- 2026-08-29
  - 작업 요약: 구현 checkpoint `89685ba82927a96c40654a47a4b0daa7f8b3a91f`에서 비학습 readiness `v1.0.0/build-f6c8171f454f`을 생성하고 독립 재검증했다. Phase 5 상태는 실제 KI10·KI20 학습을 시작하지 않았으므로 계속 `미시작`이다.
  - 변경 범위: canonical KI10 10,000행·KI20 20,000행의 7축 수량과 strict subset, 축별 10건 eval70의 train component 교집합 0, 고정 Kanana revision·template·package lock, 단일 RTX 5070 Ti·CUDA 13.0·BF16 및 64GiB disk 최소값을 불변 입력 계약으로 고정했다.
  - 검증: `prepare --execute`와 별도 `verify`가 통과했다. readiness SHA-256은 `f6c8171f…1135c3`, private/public manifest는 `6f72abe1…8c273`/`9b71d2d3…8a176`, eval70은 `aa61d2a7…bcb31`이며 생성 시 가용 disk는 754,540,773,376 bytes였다.
  - 남은 이슈·후속 작업: `human_domain_review_performed=false`, `quality_certification_claimed=false`, `phase5_training_performed=false`다. 다음 작업은 사용자가 별도로 승인할 때 KI10부터 실행하는 것이며 KI20은 KI10 checkpoint를 재사용하지 않는다.
- 2026-08-29
  - 작업 요약: 실제 학습 전에 승인된 Phase 4 v2 canonical, 10K/20K manifest, eval70, 고정 모델·환경·학습 설정을 다시 묶는 `phase5-readiness-v1.0.0` 비학습 Gate를 구현했다.
  - 변경 범위: registry·Phase 4 A~E hash chain, 7축 수량·중첩 manifest·record hash, eval70 leakage 분리, Python/uv/PyTorch CUDA/GPU·BF16, 64GiB disk, KI10/KI20 독립 초기화와 checkpoint state 보존 계약을 fail-closed로 검사한다. 학습 실행 코드는 포함하지 않는다.
  - 검증: 계약·dry-run, readiness 단위 테스트와 Ruff를 통과했다. 실제 불변 readiness 산출물은 구현 checkpoint를 커밋해 working tree를 깨끗하게 만든 뒤 생성한다.
  - 실행 전 수정: 첫 `prepare --execute`는 부모 Phase 4 검증 모듈을 import하기 전에 `ModuleNotFoundError`로 중단됐고 출력 파일은 생성되지 않았다. CLI가 현재 작업 디렉터리가 아니라 스크립트 위치에서 저장소 루트를 고정하도록 수정하고 `/tmp` 실행 회귀 테스트를 추가했다.
  - 남은 이슈·후속 작업: readiness 실행·공개 보고서 고정 전까지 Phase 5는 `미시작`이다. 이후에도 KI10·KI20 실제 학습은 사용자의 별도 명시적 승인 없이는 시작하지 않는다.
