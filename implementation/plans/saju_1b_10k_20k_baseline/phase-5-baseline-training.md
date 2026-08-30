# Phase 5. 10K·20K Baseline 학습

| 항목 | 값 |
|---|---|
| 실행 상태 | 진행 중 — KI20 `run-1f5d732cae67` 1 epoch 본학습 실행 중 |
| 선행 Phase | Phase 4 완료 |
| 입력 | 선택 길이의 canonical MIX10·MIX20, Instruct snapshot, 승인 config |
| 출력 | KI10·KI20 checkpoint, trainer state, 환경·학습 보고서 |
| 완료 Gate | KI10 재현·재로딩, Gate v2, 명시 승인된 경우 독립 KI20과 재로딩까지 종료 |
| 웹 확인일 | 2026-08-29 |

## 목적

같은 Instruct checkpoint·길이·목적함수·유효 batch에서 데이터량을 10K에서 20K로 늘렸을 때의 효과를 비교한다. KI20 v1.1은 16GiB 장비의 처리량을 위해 microbatch만 바꾸므로 KI10과의 비교는 엄밀한 단일 변수 인과 실험으로 주장하지 않고, 데이터량 baseline의 진단 비교로 제한한다.

## 실행 순서

```text
고정 Instruct ──> KI10-MIX-v2, 1 epoch ──> Gate v1 역사 보존
                                      └─ Gate v2
                                          ├─ hard gate 실패 ─> 실험 중단
                                          └─ hard gate 통과 ─> KI20 비학습 preflight
                                                               ├─ 품질 목표 미달 ─> 배포 승격 금지
                                                               └─ 별도 명시 확인 ─> v1.2 실행 계약 ─> 고정 Instruct에서 KI20
```

KI20은 KI10 checkpoint에서 시작하지 않는다. Gate v2는 계산 자원을 더 투입해도 되는 기술·안전 hard gate와 배포 품질 목표를 분리한다. hard gate 통과는 실험 지속만 허용하며 품질 인증·배포 승격을 뜻하지 않는다. 품질 목표는 사후 완화하지 않고 그대로 보고한다. KI20 본학습은 preflight 통과만으로 자동 실행되지 않으며 사용자의 새 명시 확인을 요구한다.

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
per_device_train_batch_size: 4  # KI20 v1.1 실측 선택; KI10 이력은 1
per_device_eval_batch_size: 8   # batch 1 대비 loss 동등성 통과 후보
gradient_accumulation_steps: 2  # 유효 batch 8 유지; KI10 이력은 8
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
save_total_limit: 6  # 0.5 epoch(1,250)와 final step(2,500) milestone 보존
save_only_model: false
save_safetensors: true
dataloader_num_workers: 0       # worker 2가 5% 처리량 개선 기준 미달

torch_compile: false
push_to_hub: false
report_to: none
seed: 42
data_seed: 42
```

`peft_config`와 quantized model loading을 전달하지 않는다. 8-bit는 optimizer state에만 적용하며 모델 전체 파라미터는 BF16 학습 대상이다.

### 목적함수와 표본 가중

- 목적함수는 TRL `assistant_only_loss=true`, `loss_type=chunked_nll`의 assistant token NLL이다. `chunked_nll`은 표준 NLL과 수학적으로 같고 ignored label의 projection을 생략해 peak activation을 줄인다.
- supervised assistant token은 모두 동일 가중치다. weighted sampler와 Dynamic Fine-Tuning(`dft`)은 사용하지 않는다.
- Nemotron+BaZi가 supervised assistant token의 83.299298%를 차지하지만, 이번 baseline에서 loss 가중을 바꾸면 고정 데이터 혼합의 목표 분포와 KI10 비교가 함께 바뀐다. 불균형은 축별 macro 지표로 공개하고 다음 데이터·run version에서 다룬다.
- 과거 Trainer eval loss와 이번 전체 supervised-token micro NLL은 reduction 단위가 다르므로 절대값을 직접 비교하지 않는다. loss는 유한성·회귀 진단에만 쓰고 자동 지식·안전 품질 Gate로 사용하지 않는다.

## Phase 5 실행 전 의미 감사·평가·readiness Gate

KI10 뒤의 조건을 다시 판정하기 위해 의미·출처 감사 `v1.0`, 평가 계약 `v1.2`, Gate `v2.0`, KI20 비학습 preflight `v1.1`, readiness `v1.3`을 hash chain으로 고정한다. 기존 평가 membership과 봉인 blind bytes는 바꾸거나 읽지 않는다. preflight의 짧은 임시 optimizer step은 후보 비교용이며 full KI20 1 epoch가 아니다.

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
- Gate v2 scorer의 reference 175/175 통과와 의도적 mutation 175/175 거부를 먼저 검증함
- GPU hard gate는 `nvidia-smi` 전체 사용량이 `min(16,384MiB, 장치 총량)` 미만인지로 판정하고 RAM·swap은 진단값으로만 남김
- 본학습 실행 명령을 노출하지 않고 `full_training_execution_enabled=false`를 readiness에 고정함

학습 중 loss-only eval은 이 eval70으로 250 optimizer step마다 수행한다. 생성 품질 평가는 Phase 6에서 별도 고정 계약으로 실행하며, eval 결과를 근거로 같은 Run의 hyperparameter나 데이터 비율을 중간 변경하지 않는다.

```bash
.venv/bin/python scripts/training/pretraining_audit.py validate-contract
.venv/bin/python scripts/training/pretraining_audit.py run --execute
.venv-data/bin/python scripts/evaluation/phase5_split_v1_2.py verify
.venv-data/bin/python scripts/training/phase5_gate_v2.py verify
.venv-data/bin/python scripts/training/phase5_ki20_preflight.py verify
.venv-data/bin/python scripts/training/phase5_readiness_v1_3.py verify --require-registry
```

각 `prepare`/`run`은 기본 dry-run이다. KI20 preflight v1.1은 후보별 임시 forward/backward/optimizer만 실행하고 임시 model·optimizer state를 삭제한다. 이 계약 자체는 full KI20 명령을 제공하지 않으며, 실제 1 epoch 학습은 아래 별도 v1.2 실행 계약으로만 시작한다.

```bash
.venv/bin/python scripts/training/phase5_train.py validate-contract
.venv/bin/python scripts/training/phase5_train.py plan --run-id KI10-MIX-v2
.venv/bin/python scripts/training/phase5_train.py preflight-run --run-id KI10-MIX-v2 --execute
PHASE5_TRAINING=KI10-MIX-v2 .venv/bin/python scripts/training/phase5_train.py train --run-id KI10-MIX-v2 --execute
.venv/bin/python scripts/training/phase5_train.py evaluate-ki10 --execute
```

위 KI10 명령은 실행 이력 재현용이며 다시 실행하지 않는다. KI20은 Gate v2 hard gate와 preflight가 통과했어도 자동 실행하지 않는다. 현재 readiness는 `experiment_continuation_allowed=true`, `quality_target_status=not_met`, `full_training_execution_enabled=false`, `production_promotion_allowed=false`다.

2026-08-29 사용자가 KI20 1 epoch 본학습을 별도로 명시 확인했다. readiness v1.3의 당시 상태는 소급 변경하지 않고 `phase5-training-v1.2.0.json`과 registry 실행 승인 포인터로 새 이력을 만든다. 실행은 기본 dry-run과 환경변수 이중 확인을 유지한다.

```bash
.venv/bin/python scripts/training/phase5_ki20_train.py validate-contract
.venv/bin/python scripts/training/phase5_ki20_train.py plan
PHASE5_TRAINING=KI20-MIX-v2 .venv/bin/python scripts/training/phase5_ki20_train.py train --execute
.venv/bin/python scripts/training/phase5_ki20_start_status.py
```

시작 상태 검증은 정확히 첫 optimizer step에서 loss·grad norm·전체 gradient가 유한하고 gradient가 nonzero이며, 기록된 PID와 systemd service가 같은 runner로 계속 실행 중일 때만 통과한다. WSL2의 `nvidia-smi --query-compute-apps`가 빈 목록을 반환하는 환경에서는 WSL2 여부와 시작 전 대비 4,096MiB 이상의 GPU 메모리 증가를 함께 요구한다. 이 판정은 실험 시작 확인일 뿐 학습 완료·품질 인증·배포 승격이 아니다.

## Run 시작 전 동등성 검사

각 Run은 시작 직전 다음을 `run_manifest.json`에 기록한다.

- Git commit SHA와 working tree clean 여부
- Instruct·tokenizer·template SHA-256
- manifest SHA-256, 행 수, selected max length
- Python·torch·CUDA·Transformers·TRL·bitsandbytes 버전
- GPU, driver, 시작 시 가용 VRAM, RAM, disk
- 모든 hyperparameter와 seed
- 부모 checkpoint가 고정 Instruct snapshot인지 여부

KI10과 KI20은 model·tokenizer·template·길이·목적함수·optimizer·learning rate·유효 batch 8·seed를 유지한다. KI20 v1.1에서 허용되는 실행 차이는 검증된 microbatch `4×accumulation 2`, eval batch 8, worker 0과 manifest·output directory다. 이 차이를 run manifest에 기록하고 엄밀한 단일 변수 인과 효과로 해석하지 않는다.

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
- v1.2 KI20은 마지막 6개 step checkpoint를 유지해 1,250·2,500 step milestone과 final checkpoint를 보존한다. 기존 KI10 이력은 최근 2개 설정 그대로다.
- 재시작은 Instruct snapshot, manifest, config, package lock, world size가 완전히 같을 때만 허용한다.
- 설정이 다르면 기존 Run을 덮어쓰지 않고 새 Run ID를 만든다.
- final 저장 후 새 process에서 모델·tokenizer를 로드해 deterministic fixture 5개를 생성한다.

## KI10 자동 품질 Gate v2

Gate v1의 코드·보고서 bytes는 당시 결정 이력으로 보존한다. v2는 scorer reference 175건과 의도적 오류 mutation 175건으로 계약 검출력을 먼저 증명하고, 1,000개 기존 출력과 45개 추가 handoff 출력만 사용한다. sealed blind는 사용하지 않는다.

기술·안전 hard gate는 다음과 같다.

```text
artifact identity·hash / 정확히 1,250 optimizer step / finite loss·gradient
새 process checkpoint reload / 정상 생성 1,045/1,045 / special·control 0
중대 안전 위반 0 / 민감 entity 0 / 명식 없는 입력의 허위 원국 0/50
scorer reference 175/175 통과·mutation 175/175 거부
```

hard gate는 모두 통과했다. 따라서 추가 baseline 실험은 가능하지만 아래 품질 목표는 배포·품질 승격을 별도로 차단한다.

```text
원국 글자 12/12                         통과
음양·오행·표면 수 4/12                 미달
지장간 7/12                            미달
천간 십신 0/12                         미달
지지 십신 1/12                         미달
지지 정기 적용 0/40·표면 정책 거부 20/40  미달
신살 9/25                              미달
handoff 행동 7/50                      미달
명식 미제공 허위 원국 0/50             통과
외국어 문장 14/1,045·입력 사실 위반 0   통과
```

결론은 `experiment_continuation_allowed=true`, `quality_target_status=not_met`, `production_promotion_allowed=false`다. KI20 preflight는 허용되지만 본학습은 별도 명시 확인 전까지 비활성이다.

## 완료 Gate

- [x] KI10이 고정 Instruct snapshot에서 시작해 NaN/Inf/OOM 없이 1 epoch를 끝냈다.
- [x] KI10 final 모델과 trainer state를 새 process에서 재로딩했다.
- [x] KI10 run manifest, package lock, log, checkpoint hash를 저장했다.
- [x] Gate v1 bytes를 보존하고 Gate v2 hard gate·품질 목표를 분리 판정했다.
- [x] KI20의 forward/backward/optimizer·batch/worker/eval 비학습 preflight를 완료했다.
- [x] 사용자의 별도 명시 확인을 v1.2 실행 계약과 registry 승인 포인터로 고정했다.
- [x] 별도 명시 확인 뒤 KI20을 같은 Instruct snapshot에서 독립 시작하고 첫 정상 step을 확인했다.
- [ ] 실행된 KI20은 KI10과 manifest·output 경로 외 설정이 같고 재로딩 가능하다.
- [x] 모델·checkpoint를 공개 저장소나 Hub에 올리지 않았다.

## 산출물

```text
data/reports/saju_1b_baseline/evaluation-split/v1.2.0/build-e885b47cae74/
data/reports/saju_1b_baseline/phase5-gate/v2.0.0/KI10-MIX-v2/gate-df26e962e145/
data/reports/saju_1b_baseline/phase5-preflight/v1.1.0/preflight-b47fe12f03a4/
data/reports/saju_1b_baseline/phase5-readiness/v1.3.0/build-7eb4c34364cc/
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

runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67/  # 1 epoch 본학습 실행 중
└── <동일 구조>
```

## 공식 자료

- [TRL 1.12.0 SFTTrainer](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)
- [Transformers 4.57.6 TrainingArguments source](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/training_args.py)
- [bitsandbytes 0.50.2 8-bit optimizer](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/optimizers)
- [PyTorch CrossEntropyLoss](https://docs.pytorch.org/docs/stable/generated/torch.nn.CrossEntropyLoss.html)
- [Transformers tokenizer backend](https://github.com/huggingface/transformers/blob/main/src/transformers/tokenization_utils_tokenizers.py)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | TRL SFT loss·logged metrics | masked token NLL과 loss/entropy/token accuracy/grad norm 기록 항목 확인 |
| 2026-08-27 | TRL config 필드 | save/logging/seed/full FT 관련 현재 필드 확인 |
| 2026-08-27 | bitsandbytes 8-bit optimizer | parameter state memory 절감과 activation memory 비절감 한계 확인 |
| 2026-08-29 | TRL 1.12 SFT objective | `chunked_nll`은 표준 NLL과 같은 수학이며 assistant-only mask를 지원함을 재확인 |
| 2026-08-29 | PyTorch cross entropy reduction | ignored target을 제외한 token mean과 축별 macro를 분리 보고하기로 결정 |
| 2026-08-29 | Transformers Mistral regex 처리 | base·KI10 tokenizer bytes는 동일하므로 현 fingerprint를 유지하고 별도 version migration으로 격리 |

## 진행 기록

- 2026-08-30
  - 작업 요약: dashboard `v1.4.0`에 실제 Full FT 시작점인 고정 `kanana-2-1.3b-instruct@bf4786aa…` K0 원본 축을 추가했다. 새 세션은 `KI20 단독`, `K0 원본 단독`, `K0 ↔ KI20 동시 비교` 중 하나를 고르며, 비교 모드는 같은 prompt profile·greedy 설정을 적용하되 각 모델이 상대 답변을 보지 않는 독립 문맥으로 동작한다.
  - 변경 범위: K0 `model.safetensors` SHA-256 `49aa6c…c942`, KI20 final `2fae23…872d`와 각 tokenizer·chat template·검토된 custom model code의 고정 SHA-256을 로드 직전에 확인하고 모델을 순차 로드·해제한다. 세션 `1.2.0`은 선택 축·모델 fingerprint·엔진별 응답 진단을 Git 제외 private JSON에 원자 저장하며 기존 `1.0/1.1` 세션은 KI20 단독으로 읽고 실제 후속 입력이 있을 때만 지연 변환한다. 기존 고정 20건 KI10↔KI20 산출물, 학습데이터, checkpoint, sealed blind는 수정하지 않았다.
  - 검증: 최종 고정 파일 검증 경로에서 공개 합성 한 문장을 두 모델에 같은 337-token 입력으로 실행해 non-empty `2/2`, K0/KI20 소요 `9.833/3.007초`, peak allocated 각각 `2,677,079,040 bytes`, 전체 GPU 사용 `4,272/4,283MiB`를 확인했고 종료 후 `1,242MiB`로 복귀했다. dashboard targeted 26건과 저장소 전체 259건, Ruff, JavaScript syntax, JSON·diff 검증, desktop `1440×1200` 좌우 비교·mobile `390×844` 단일열 렌더링을 통과했으며 가로 overflow는 없었다. 합성 세션은 사용자 세션 목록에서 분리한 Git 제외 `dashboard/v1.4.0/smoke_sessions`에 보존했다.
  - 남은 이슈·후속 작업: 이 비교축은 SFT 전후 행동 차이를 직접 보는 진단 기능일 뿐 품질 Gate가 아니며 두 모델 모두 사주 계산 engine이 연결되지 않았다. 기존 YaRN·tokenizer regex 경고의 고정 fingerprint 판단을 유지하고 `fix_mistral_regex`를 임의 적용하지 않는다. K0↔KI20 동일 상태형 평가와 계산 engine bridge는 별도 승인 범위다.
- 2026-08-30
  - 작업 요약: 실제 11턴 수동 대화는 22개 메시지 전체가 `553/3,584` token으로 보존되고 system message가 없었으므로, 실패 원인을 context 손실이 아닌 입력 수집·계산 경계 행동 제어 부재로 확정했다. dashboard `v1.3.0`은 새 세션 기본값을 `guided_diagnostic_v1`로 바꾸고 `raw_no_system`은 명시적 진단 선택으로만 남겼다. 두 profile 모두 `diagnostic_only=true`, `production_like=false`다.
  - 변경 범위: 안내 보정 prompt는 대화 첫 위치에 정확히 한 번 삽입하고 오래된 완결 turn을 줄여도 보존한다. v1.0 기존 세션은 `raw_legacy`로 읽어 원래 의미를 바꾸지 않는다. 공개 합성 상태형 dev 100건과 10개 층×200건인 보강 후보 2,000건 `build-0f80acfeed13`을 새로 만들었지만 후보는 `candidate_only`, `training_promotion_allowed=false`이며 기존 20K·checkpoint·학습 설정을 수정하거나 재학습하지 않았다.
  - 검증: 후보 JSONL SHA-256은 `fec7403e…6917b`, dev100과 정규화 exact·문자 5-gram Jaccard 0.85 이상 중복은 각각 0건이고 최대 유사도는 `0.065217`이다. intake transcript의 명식·간지와 structured transcript의 출생일·시각·장소 혼합, PII, 실제 사용자 세션·AI Hub 원문, 허위 4주, 정규화 중복은 모두 0건이다. 실제 KI20 상태형 Gate `stateful-gate-f5b76dde1921`은 100건을 105.577초에 생성했으며 non-empty `100/100`, 중대 안전·직전 답변 반복 0건은 통과했지만 필수 행동 `14/100`, 임의 간지 없는 출력 `84/100`, 이미 받은 필드 재질문 18건, 허위 UI·완료 1건, 미지원 날짜·기간 사실 5건으로 `guided_diagnostic_not_met`였다. 봉인 blind는 열지 않았다.
  - 남은 이슈·후속 작업: system prompt만으로는 보정되지 않았으며 sanitized 1턴 smoke도 네 필수 범주 중 생년월일·달력 구분만 요청했다. 후보 2,000건은 내부 hash 표식 없는 실제 system prompt와 조건부 `calendar_type`·`leap_month`를 사용하지만 결정적 template 후보이므로 그대로 학습 Gold로 승격하지 않는다. 자연스러운 다회전 표현 다양화, 실패 사례 소수 검토, 새 train/dev 평가 계약·data fingerprint 승인 뒤에만 별도 재학습을 결정한다. 실제 앱은 독립 slot state와 승인된 생년월일시→명식 계산 engine이 필요하다.
- 2026-08-29
  - 작업 요약: dashboard `v1.2.0`에 현재 KI10·KI20 학습, dev monitor·diagnostic, persona guard, 외부 정합성의 수량·축별 분포와 고정 샘플을 보는 `데이터 스플릿` 탭을 추가했다. 화면은 밝은 한지 바탕과 저채도 오행 색으로 바꾸고, 세션 챗봇을 먼저 배치한 뒤 고정 20건 진단 비교를 기본 닫힘 토글로 이동했다.
  - 변경 범위: 샘플 API는 고정 SHA-256을 확인한 로컬 비봉인 데이터만 읽고 message 또는 구조화 input/expected 최소 투영만 반환한다. AI Hub 두 축은 `로컬 제한·외부 공유 금지`를 표시하며 내부 record·leakage·locator·hash는 반환하지 않는다. `blind_source_test_500`은 수량·봉인 상태만 표시하고 payload path를 구현에 포함하거나 읽지 않았다. 학습 데이터·split membership·checkpoint·기존 dashboard 세션과 fixed probe 결과는 수정하지 않았다.
  - 검증: dashboard 단위·HTTP 보안·dataset 회귀 17건, Ruff, JavaScript syntax, JSON parse와 diff check가 통과했다. 실제 `run-1f5d732cae67`에서 split 10,000/20,000/70/930/50/220과 blind 500을 확인하고 KI20 전체 7축 샘플, 제한 표시, `sealed_blind_accessed=false`를 검증했다. Windows headless Chrome의 1440×1200·390×844 렌더링에서 한지 테마와 반응형 배치를 확인했고 렌더 DOM에서 고정 20건 외부 토글이 기본 닫힘이었다.
  - 남은 이슈·후속 작업: 데이터 샘플은 loopback 진단 편의이며 정식 평가나 품질 주장에 사용하지 않는다. AI Hub 샘플은 사용자의 로컬 즉시 표시 선택에 따라 보이므로 화면 캡처·복사·외부 공유를 금지한다. Phase 6 blind 단회 평가는 이 변경으로 시작되지 않았다.
- 2026-08-29
  - 작업 요약: 완료된 KI20 final을 수동 검사할 때 대화를 단발성으로 버리던 dashboard를 `v1.1.0` 세션 방식으로 확장했다. 새 세션·기존 세션 선택, turn별 질문/답변 조회와 이전 대화를 포함한 후속 생성을 지원한다.
  - 변경 범위: 대화 원문은 `runs/.../dashboard/v1.1.0/manual_sessions`의 Git 제외 private JSON에만 0600으로 원자 저장한다. 최대 100세션·세션당 50 turn을 보존하고 모델 입력은 오래된 완결 turn부터 제외해 3,584 token으로 제한한다. fixed probe `v1.0.0`, 학습 run·checkpoint, sealed blind와 품질 Gate는 수정하지 않았다.
  - 검증: dashboard 단위·HTTP 보안·세션 회귀 14건, Ruff, JavaScript syntax, JSON parse와 diff check가 통과했다. 실제 `run-1f5d732cae67` dashboard를 재기동해 `127.0.0.1:8765` active, CSRF 보호 `/api/sessions` 200, 빈 초기 세션 목록과 generation gate open을 확인했다. 재기동 중 확인한 TCP 재바인딩 지연은 loopback server의 안전한 address reuse로 보완했다.
  - 남은 이슈·후속 작업: 저장된 수동 대화는 사용자 진단 편의 기능이며 정식 평가나 모델 품질 근거로 합치지 않는다. 3,584 token을 넘는 장기 대화는 전체 기록은 남지만 오래된 turn이 모델 입력에서 제외되고 화면에 제외 turn 수를 표시한다.
- 2026-08-29
  - 작업 요약: `KI20-MIX-v2/run-1f5d732cae67` Full FT 1 epoch 2,500 step이 중단·resume 없이 완료됐고 final checkpoint 새 프로세스 reload 5/5를 통과했다. 대시보드의 비봉인 고정 20건 KI10↔KI20 진단도 완료했다.
  - 변경 범위: 20,000행 BF16 Full FT의 private run·checkpoint와 Git 제외 dashboard 진단만 읽어 집계했다. sealed blind·Phase 6·production 승격은 실행하지 않았고, 학습 종료 시 생성된 untracked 공개 run 보고서는 이번 기록 커밋에 포함하지 않는다.
  - 검증: 전체 training loss `0.687154`, final logged loss `0.5895`, eval loss는 step 250 `0.708870`에서 step 2250 최저 `0.533743`, final `0.535032`였고 final token accuracy는 `86.860620%`다. loss/grad norm은 전부 유한하고 peak allocated VRAM은 `6,918,075,904` bytes, 총 학습 시간은 `4,002.182초`다. 고정 20건에서 KI20은 non-empty 20/20·중대 안전 0·입력 간지 위반 0/16이었지만 hard fact/branch `2/7`, 신살 `1/3`, 정보부족 handoff `1/2`로 KI10 `3/7·1/3·1/2`보다 개선되지 않았다.
  - 남은 이슈·후속 작업: 이 20건은 정식 Gate가 아닌 소표본 진단이며 `production_promotion_allowed=false`를 유지한다. sealed blind 전에 동일한 비봉인 전체 Gate v2 범위로 KI20을 평가하고, 새 공개 run 보고서·checkpoint inventory hash를 별도 검증한다. 현재 run은 checkpoint 6개와 final을 합쳐 약 38GiB이므로 삭제 전 보존·재현 계약을 새 retention 기록으로 고정한다.
- 2026-08-29
  - 작업 요약: 실행 중인 KI20 Full FT와 분리된 loopback 전용 학습·모델 검사 대시보드 `v1.0.0`을 구현했다. 학습 중에는 `metrics.jsonl`·manifest·checkpoint만 읽고, final reload와 GPU idle이 확인된 뒤에만 모델 추론을 허용한다.
  - 변경 범위: 기존 `phase5_ki20_train.py`와 실행 config·run fingerprint는 수정하지 않았다. 별도 config·보안 HTTP 서버·자체 호스팅 HTML/CSS/JS·테스트를 추가했으며, 대시보드에는 학습 중단·재개·삭제 기능이 없다. KI10 비봉인 진단 결과에서 10개 범주 20건을 해시 기반으로 고정해 KI20 final과 비교하되 정식 Gate·Phase 6·sealed blind와 분리했다.
  - 검증: dashboard 단위·HTTP 보안 12건과 저장소 전체 220개 unittest, Ruff, JavaScript syntax, diff check가 통과했다. 실제 run HTTP smoke는 step 1,420에서 HTML/API 200, runtime alert 0, train/eval metric 143/5행, checkpoint 5개 complete, 학습 중 generation 409 차단을 확인했다. 격리 worktree에서 Git 제외 산출물 부재로 끝난 기존 테스트는 원 작업 트리 재실행에서 모두 통과했다.
  - 남은 이슈·후속 작업: `saju-ki20-dashboard-run-1f5d732cae67.service`가 `127.0.0.1:8765`에서 실행 중이며 Windows/WSL의 `http://localhost:8765`로 확인한다. 고정 20건·수동 질문은 학습 완료 전까지 fail-closed로 유지하고, 완료 뒤 화면에서 고정 검사를 한 번 실행하되 결과는 Git 제외 private run에만 둔다. 대시보드 종료는 `systemctl --user stop saju-ki20-dashboard-run-1f5d732cae67.service`를 사용한다.
- 2026-08-29
  - 작업 요약: 실행 계약 commit `9ad00a283ce64ff222a54c41f743ae378ce12fe4`에서 KI20 `run-1f5d732cae67`을 systemd user service로 시작하고 첫 정상 optimizer step을 검증했다.
  - 변경 범위: Git 제외 private run 경로에 initializing manifest를 만든 뒤 step 1에서만 `phase5_training_performed=true`와 start marker를 원자적으로 기록했다. 모델·20K manifest·기존 KI10/v1.1 산출물은 수정하지 않았다.
  - 검증: loss `2.9315`, grad norm `21.375`, gradient finite/nonzero, PID `826832`와 service active를 확인했다. WSL2 compute-app 목록 미노출은 고정 runner PID와 초기 대비 GPU `8,781MiB` 증가로 교차 검증했으며 총 사용량 `9,879MiB`는 16GiB 상한 미만이었다.
  - 남은 이슈·후속 작업: 학습은 2,500 step까지 백그라운드에서 계속된다. 완료 후 checkpoint-1250·2500/final, 새 process reload, 공개 집계 보고서를 검증하고 그다음 Phase 6 평가를 별도 진행한다.
- 2026-08-29
  - 작업 요약: 사용자의 KI20 1 epoch 실행 확인을 받았고, 첫 정상 optimizer step을 시작 완료 기준으로 하는 training `v1.2.0` 계약을 추가했다.
  - 변경 범위: 기존 v1.0 runner와 v1.1 preflight를 수정하지 않고 전용 runner·config·registry 승인 이력을 분리했다. 1 epoch 2,500 step, `4×2`, eval 8, assistant-only `chunked_nll`, BF16 Full FT를 유지하며 1,250·2,500 step checkpoint를 보존한다.
  - 검증: 시작 전 모델·data·Gate·preflight·readiness hash, Git clean, GPU compute process·VRAM, RAM, disk를 fail-closed로 확인하고 첫 step 전에는 `phase5_training_performed=false`를 유지하도록 구현했다.
  - 남은 이슈·후속 작업: clean implementation commit을 push한 뒤 systemd user service에서 실행하고 첫 step marker·활성 PID·CUDA process를 확인해야 한다. 이 기록 시점에는 본학습을 시작하지 않았다.
- 2026-08-29
  - 작업 요약: Gate v2 계약 탐색부터 KI20 비학습 preflight까지 완료해 readiness `v1.3.0/build-7eb4c34364cc`으로 묶었다. 실제 KI20 1 epoch 학습은 실행하지 않았다.
  - 변경 범위: 평가 계약 `v1.2.0/build-e885b47cae74`, Gate `v2.0.0/gate-df26e962e145`, preflight `v1.1.0/preflight-b47fe12f03a4`, 공개 현황 `build-e23e3501a200`을 새 불변 경로에 추가했다. Gate v1 코드·보고서, 학습 데이터, 모델 checkpoint, sealed blind는 수정·열람하지 않았다.
  - 검증: scorer reference/mutation `175/175`, Gate v2 hard gate `10/10`, train 후보 `1×8·2×4·4×2`, longest padded stress, worker `0/2`, eval batch `1/2/4/8`을 비교했다. `4×2`, worker 0, eval 8을 선택했고 train peak `10,634MiB`, eval peak `11,802MiB`로 모두 `16,384MiB` 미만이었다.
  - 남은 이슈·후속 작업: 품질 목표는 원국 글자 외 8개가 미달이며 `production_promotion_allowed=false`다. 본학습은 `full_training_execution_enabled=false`로 닫혀 있고 사용자 새 확인 뒤 별도 실행 계약으로만 시작한다. tokenizer regex·YaRN 경고는 현 fingerprint를 바꾸지 않고 후속 버전에서 검증한다.
- 2026-08-29
  - 작업 요약: 구현 checkpoint `618ce4d9870e7a64681823f0cde3a38f9934fad1`에서 KI10 Gate 실패 현황판 `v1.0.0/build-a4014017c26c`를 발행하고 registry 최신 포인터를 갱신했다.
  - 변경 범위: 기존 pre-KI10 status build는 불변 이력으로 보존했다. root `PROJECT_STATUS.html`과 새 snapshot은 KI10 학습 완료, 4개 품질 Gate 미달, KI20·sealed blind 차단을 공개 집계로만 표시한다.
  - 검증: build SHA-256 `a401401…a5a8e4`, HTML `4d81927…1f732e`, manifest `f0f1d6a…a0d253`, config `73e5b1a…ee914b`이며 root/snapshot HTML byte가 같다. restricted content와 외부 실행 자산은 포함하지 않았다.
  - 남은 이슈·후속 작업: 현 계약의 조건부 학습 흐름은 KI20 미실행으로 종료한다. 후속은 현재 run을 수정하는 작업이 아니라 데이터·평가·run 새 version을 설계하고 다시 사전 승인하는 별도 Phase 5 재시도다.
- 2026-08-29
  - 작업 요약: KI10 Gate 실패 뒤에도 `PRE-KI10 / GO`로 남던 현황판을 config 기반 현재 결정으로 전환하고 `ki10_gate_failed / STOP` 계약을 구현했다. 이 항목은 렌더러 fingerprint 체크포인트이며 실제 새 HTML build 발행은 다음 clean commit에서 수행한다.
  - 변경 범위: 현황 config에 KI10 Full FT와 자동 Gate component, Phase 5 차단 상태, 실패 지표, KI20·sealed blind 금지를 추가했다. hero·현재 결정·Gate 제목은 하드코딩 대신 검증된 `decision` 객체에서 escape해 렌더링한다.
  - 검증: status `validate-contract`, dry-run `plan`, Ruff와 `git diff --check`가 통과했고 예상 snapshot은 `v1.0.0/build-a4014017c26c`다.
  - 남은 이슈·후속 작업: 구현 commit SHA를 registry provenance로 사용해 HTML·manifest를 생성하고 byte-identical·restricted-content 검증 뒤 최신 포인터를 갱신한다.
- 2026-08-29
  - 작업 요약: KI10 최종 checkpoint로 고정 dev diagnostic 930건과 persona guard 50건의 1,000case 자동 품질 Gate를 전수 실행했다. 4개 Gate가 미달해 `ki20_promotion_allowed=false`로 판정했으며 KI20은 실행하지 않았다.
  - 변경 범위: deterministic greedy·`max_new_tokens=256`으로 1,000개 출력을 private run에만 저장하고 공개 경로에는 집계 Gate와 manifest만 추가했다. 봉인 blind·Phase 6·전문가 Gold는 열람하거나 사용하지 않았다.
  - 검증: parseable 100%, special/control 0, severe safety 0, foreign sentence 1.4%, input fact violation 0%, empathy confusion 0%, persona causalization 0%는 통과했다. 반면 hard fact·branch policy `38/100`, 신살 `17/25`, missing-chart handoff `3/5`, target-only date `1건`으로 고정 기준을 미달했다. 같은 채점기의 고정 reference는 hard fact `60/60`, branch policy `40/40`, 신살 `25/25`를 통과해 채점기 오탐보다 모델 지식·정책 회귀로 판정했다.
  - 남은 이슈·후속 작업: 현재 Phase 5는 `차단`이며 임계값을 완화하거나 같은 run을 덮어쓰지 않는다. deterministic·branch-policy·신살·handoff 보강안을 새 데이터/평가/version 계약으로 설계하기 전까지 KI20과 sealed blind를 실행하지 않는다.
- 2026-08-29
  - 작업 요약: 고정 Instruct snapshot에서 `KI10-MIX-v2/run-e6b712f0d45e` Full FT 1 epoch를 실행하고 최종 checkpoint를 새 프로세스로 재로딩해 5개 smoke 출력이 모두 비어 있지 않음을 확인했다. Phase 5는 진행 중이며 KI20은 아직 실행하지 않았다.
  - 변경 범위: canonical 10,000행을 BF16·SDPA·8-bit optimizer로 정확히 1,250 optimizer step 학습했다. 비공개 모델·optimizer·생성문은 `runs/`에만 저장하고 공개 경로에는 집계 summary와 manifest만 기록했다. 데이터·평가 membership·봉인 blind는 변경하거나 열람하지 않았다.
  - 검증: train/eval loss `0.7679830020904541`/`0.9442684650421143`, 유한·nonzero gradient, peak VRAM `6,757,645,824` bytes, final reload 5/5를 확인했다. private training/reload/inventory SHA-256은 `1cbec50…e80a53`/`7235fa4…0f17c`/`1ebab21…cda747`, 공개 summary/manifest는 `1cbec50…e80a53`/`1b35d59…f7ca6`다.
  - 남은 이슈·후속 작업: 고정 dev diagnostic 930건과 persona guard 50건을 합친 1,000case 자동 품질 Gate를 실행한다. 모든 임계값이 통과한 경우에만 KI20을 base snapshot에서 독립 시작하며 sealed blind와 전문 품질 주장은 계속 금지한다.
- 2026-08-29
  - 작업 요약: KI10 forward-only preflight `run-e6b712f0d45e`가 BF16·SDPA·assistant-only evaluation을 통과했다. `train_method_called=false`, `backward_performed=false`, `optimizer_step_performed=false`를 manifest로 확인했다.
  - 변경 범위: dev monitor 70건만 forward했고 비공개 `runs/PHASE5-PREFLIGHT` 경로 외에는 모델·데이터·checkpoint를 쓰지 않았다. TRL stop-token 경고는 assistant 뒤 user를 붙이는 generic probe와 Kanana의 last-response-only template 의미가 달라 발생했다.
  - 검증: eval loss `3.658891439437866`, peak/free VRAM `3,675,810,816`/`12,867,076,096` bytes, summary/manifest SHA-256 `cdd5ca7…b6a79`/`01e5dfb…2cac5`다. KI10 10,000행 전수에서 final assistant EOS mask 10,000건, 누락 0건, 최대 609 token을 확인했다. 1,543개 멀티턴의 앞선 assistant는 원본 정책대로 context이고 마지막 응답만 supervision한다.
  - 남은 이슈·후속 작업: preflight 통과 현황을 새 공개 status build로 고정한 clean tree에서만 KI10 Full FT를 시작한다. KI20은 계속 `ki20_promotion_allowed=false`다.
- 2026-08-29
  - 작업 요약: 수정 runner를 포함한 readiness `v1.2.0/build-e325f16096dd`을 clean commit에서 새로 생성하고 별도 프로세스로 재검증했다. 이전 readiness는 덮어쓰지 않았다.
  - 변경 범위: registry 최신 승인 포인터와 공개 현황판을 새 build로 연결했다. dev monitor 70건과 canonical KI10/20K fingerprint는 불변이며 봉인 blind·학습·backward·optimizer step은 수행하지 않았다.
  - 검증: build SHA-256 `e325f160…fbf7775`, private/public manifest `fd7d2b0…67553`/`b0356b2…3b0e3`, summary `6ca7fb4…3d814`, dev monitor `aa61d2a…bcb31`을 확인했다. KI10 baseline 허용·KI20 금지·전문가 품질 주장 금지를 유지한다.
  - 남은 이슈·후속 작업: 이 승인 변경을 커밋·푸시하고 clean tree가 된 뒤 forward-only preflight를 재실행한다. 그 결과가 통과하기 전에는 `PHASE5_TRAINING`을 설정하지 않는다.
- 2026-08-29
  - 작업 요약: 첫 KI10 forward-only preflight는 모델 load 뒤 TRL 1.12.0 `SFTTrainer`가 `train_dataset`을 필수로 검사해 evaluation 전에 중단됐다. 임시 경로는 atomic cleanup되어 partial run·backward·optimizer step·학습 데이터 변경이 없었다.
  - 변경 범위: 동일한 dev monitor 70건을 TRL 전처리용 `train_dataset`과 실제 `eval_dataset`에 함께 전달하되 실행 메서드는 `evaluate()`만 허용했다. summary와 manifest에 `train_method_called=false` 및 전처리용 train dataset 제공 사실을 명시하고 회귀 테스트를 추가했다.
  - 검증: 설치된 TRL `1.12.0`의 생성자에서 `train_dataset is None`이면 `ValueError`가 발생함을 로컬 source로 확인했다. 수정 runner SHA-256은 `aff05d86…1eb3f`, 새 readiness 예상 build는 `v1.2.0/build-e325f16096dd`다.
  - 남은 이슈·후속 작업: 수정 checkpoint를 먼저 커밋한 뒤 새 readiness와 현황판을 불변 build로 재생성·재승인한다. 그 hash chain이 통과할 때만 forward-only preflight를 재시도하며, 실제 KI10 학습은 그 결과 전까지 시작하지 않는다.
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
