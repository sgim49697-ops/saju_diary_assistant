# Phase 5. 10K·20K Baseline 학습

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 4 완료 |
| 입력 | 선택 길이의 canonical MIX10·MIX20, Base snapshot, 승인 config |
| 출력 | K10·K20 checkpoint, trainer state, 환경·학습 보고서 |
| 완료 Gate | 두 독립 Run이 재현·재로딩 가능한 상태로 종료 |
| 웹 확인일 | 2026-08-27 |

## 목적

같은 Base와 동일한 학습 설정에서 데이터량만 10K에서 20K로 늘렸을 때의 효과를 비교한다. Phase 4에서 검증한 길이·template·환경·manifest를 변경하지 않는다.

## 실행 순서

```text
고정 Base ──> K10-MIX-v0-RAW-NC, 1 epoch ──> 중간 안정성 검사
고정 Base ──> K20-MIX-v0-RAW-NC, 1 epoch ──> 공식 비교 대상
```

K20은 K10 checkpoint에서 시작하지 않는다. K10 후 심각한 파이프라인 오류가 발견되면 K20을 실행하지 않고 Phase 2~4로 돌아간다.

## 고정 학습 설정

`max_length`는 Phase 4가 고른 1024/768/512 중 하나를 그대로 사용한다.

```yaml
model_name_or_path: <local-base-snapshot>
model_revision: e9ffedf7b713530ae6a0c94ea32538d75e8524e1
trust_remote_code: true
model_init_kwargs:
  dtype: bfloat16

bf16: true
fp16: false
tf32: false
per_device_train_batch_size: 1
gradient_accumulation_steps: 8
num_train_epochs: 1

optim: paged_adamw_8bit
learning_rate: 8.0e-6
warmup_ratio: 0.03
lr_scheduler_type: cosine
weight_decay: 0.01
max_grad_norm: 1.0

gradient_checkpointing: true
use_cache: false
assistant_only_loss: true
packing: false
loss_type: chunked_nll

logging_strategy: steps
logging_steps: 10
logging_first_step: true
eval_strategy: "no"
save_strategy: steps
save_steps: 250
save_total_limit: 2
save_only_model: false

torch_compile: false
push_to_hub: false
report_to: none
seed: 42
data_seed: 42
```

`peft_config`와 quantized model loading을 전달하지 않는다. 8-bit는 optimizer state에만 적용하며 모델 전체 파라미터는 BF16 학습 대상이다.

## Run 시작 전 동등성 검사

각 Run은 시작 직전 다음을 `run_manifest.json`에 기록한다.

- Git commit SHA와 working tree clean 여부
- Base·tokenizer·template SHA-256
- manifest SHA-256, 행 수, selected max length
- Python·torch·CUDA·Transformers·TRL·bitsandbytes 버전
- GPU, driver, 시작 시 가용 VRAM, RAM, disk
- 모든 hyperparameter와 seed
- 부모 checkpoint가 Base snapshot인지 여부

K10과 K20에서 model·template·hyperparameter·seed가 다르면 시작하지 않는다. 허용되는 차이는 manifest와 output directory뿐이다.

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
- 재시작은 Base, manifest, config, package lock, world size가 완전히 같을 때만 허용한다.
- 설정이 다르면 기존 Run을 덮어쓰지 않고 새 Run ID를 만든다.
- final 저장 후 새 process에서 모델·tokenizer를 로드해 deterministic fixture 5개를 생성한다.

## K10 중간 안정성 검사

K10 종료 후 Phase 6 전체 평가 전에 core eval의 소규모 고정 subset으로 다음만 확인한다.

- 정상 생성·종료
- JSON 태스크 파싱
- 공감 질문에 사주를 꺼내는 태스크 혼동
- 중국어 문장 혼입
- 입력 사실을 무시한 명백한 환각

모델이 생성 불능이거나 pipeline 오류가 있으면 K20을 중단한다. 모델 품질이 낮지만 파이프라인이 정상이라면 데이터량 효과 측정을 위해 K20은 진행한다.

## 완료 Gate

- [ ] K10과 K20 모두 같은 Base에서 독립적으로 시작했다.
- [ ] 두 Run의 config 차이는 manifest·output 경로뿐이다.
- [ ] 1 epoch가 NaN/Inf/OOM 없이 끝났다.
- [ ] final 모델과 trainer state를 새 process에서 재로딩했다.
- [ ] run manifest, package lock, log, checkpoint hash를 저장했다.
- [ ] K10 중간 안정성 검사가 K20 진행을 허용했다.
- [ ] 모델·checkpoint를 공개 저장소나 Hub에 올리지 않았다.

## 산출물

```text
runs/K10-MIX-v0-RAW-NC/
├── run_manifest.json
├── config.resolved.yaml
├── trainer_state.json
├── metrics.jsonl
├── checkpoint-*/
├── final/
└── reload_fixtures.jsonl

runs/K20-MIX-v0-RAW-NC/
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
