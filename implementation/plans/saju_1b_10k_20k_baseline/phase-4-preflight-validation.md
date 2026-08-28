# Phase 4. 학습 전 데이터·모델 검증

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 2·3 완료 |
| 입력 | unified v1·후보 순서·혼합 계약, 고정 Instruct 환경, eval set |
| 출력 | `preflight_report.json`, 선택된 `max_length`, K0 결과, smoke checkpoint |
| 완료 Gate | 데이터 Gate와 모델·메모리 Gate 동시 통과 |
| 웹 확인일 | 2026-08-27 |

## 목적

정식 10K·20K Run 전에 데이터 계약, chat serialization, assistant loss mask, Instruct 원본 성능, Full FT 메모리와 checkpoint 복구를 실제로 검증한다. smoke는 검증용 학습이며 공식 모델 성능 비교에 사용하지 않는다.

## Gate A. 데이터 검증

먼저 unified 후보와 고정 split을 검사한다. 최종 MIX1K·MIX10·MIX20 수량 검사는 Gate B가 길이별 manifest를 만든 뒤 다시 수행한다.

### 스키마·수량

- 공통 필수 필드와 enum 위반 0건
- ID·raw hash duplicate 0건
- source별 reserve pool이 MIX20 목표 수량보다 최소 20% 큼

### 누수

- train과 source holdout 500 사이 group 교집합 0
- train과 core eval 200 사이 group·정규화 문장 hash 교집합 0
- AI Hub session, Nemotron chart signature, `bazi-sft` synthetic group, 신살 rule/chart group이 split을 넘지 않음

### 언어·품질

- 빈 assistant, JSON 파싱 실패, 비정상 제어문자 0건
- 중국어 잔재 flag와 제외 이유 집계 존재
- source·task·`license_expression`·`usage_class`·`provenance_status`·revision·raw hash 누락 0건
- 길이 초과 제외와 replacement mapping 수량 일치

## Gate B. Tokenization·loss mask·길이별 manifest

TRL 1.12.0의 conversational dataset과 `assistant_only_loss=True`를 사용한다. 공식 문서상 이 옵션은 template가 `{% generation %}`와 `{% endgeneration %}`로 assistant 영역을 반환해야 한다.

각 source/task에서 짧은 행, 중앙 길이, p95 길이, 멀티턴 행을 포함해 최소 10개씩 검사한다.

### 필수 assertion

```text
system token labels == -100
user token labels == -100
padding token labels == -100
assistant 본문 token 중 non--100 token 존재
assistant 본문 token id와 label token id 일치
assistant 종료 token이 생성 종료 정책과 일치
mask 밖의 non--100 token == 0
tokenize=True 경로에서 special token 중복 == 0
```

render된 텍스트, token ID, role boundary, label mask를 사람이 읽을 수 있는 fixture 보고서로 저장한다. assertion 하나라도 실패하면 optimizer를 만들지 않는다.

고정 tokenizer·template로 전체 후보를 tokenization해 다음을 보고한다.

- source/task별 input·assistant·total token 합계
- 평균, 중앙값, p90, p95와 512/768/1024 초과율
- source별 assistant loss token 비율
- special token 중복과 zero-assistant-mask 수

각 길이에서 후보 순서대로 MIX20을 채우고 source별 앞 절반으로 MIX10, MIX10의 앞 10%로 MIX1K를 만든다. 행 혼합비는 token 통계에 맞춰 바꾸지 않는다. 단일 source가 전체 assistant loss token의 70%를 넘거나 `bazi-sft`·신살 파생본의 동일 template 문장이 과도하게 반복되면 Phase 2로 돌아가 후보 다양성과 adapter를 수정한다. 원문을 자르거나 제외 소스를 보충재로 넣지 않는다.

각 길이별 manifest는 다음을 만족해야 한다.

- MIX1K 1,000행, MIX10 10,000행, MIX20 20,000행
- source 수량이 고정표와 정확히 일치
- `MIX1K IDs ⊂ MIX10 IDs ⊂ MIX20 IDs`
- 하위 manifest 레코드 hash가 상위 manifest의 동일 ID와 일치
- 길이 초과 행 0건, 제외·보충 mapping 완비

## Gate C. 원본 모델 Baseline

학습 전에 시작 checkpoint인 `K0-INSTRUCT`를 고정 평가한다.

```text
do_sample=false
num_beams=1
max_new_tokens=512
같은 prompt serialization과 EOS 정책
```

평가 대상은 source holdout 500과 core eval 200이다. Instruct 결과는 학습 전 시작점이자 데이터·template·generation 파이프라인의 sanity reference로 고정한다. `명식 누락 시 계산기 handoff` 문항에서 모델이 임의의 네 기둥을 만들면 Gate 실패로 기록한다.

## Gate D. 단일 배치 기능 검사

512 token MIX1K batch에서 다음을 한 번 수행한다.

1. BF16 전체 파라미터 모델 로드
2. gradient checkpointing 활성화, `use_cache=False`
3. `paged_adamw_8bit` optimizer 초기화
4. forward, loss finite 확인
5. backward, gradient finite·nonzero 확인
6. gradient clip과 optimizer step
7. peak VRAM과 system RAM 기록

NaN/Inf, zero assistant token, CUDA kernel 오류, remote code 오류가 있으면 즉시 차단한다.

## Gate E. 길이·메모리 smoke

### 순서

1. 512에서 20 optimizer step 기능 smoke
2. 1024에서 200 optimizer step memory smoke
3. 1024가 OOM 또는 headroom 부족이면 768에서 새 process로 200 step
4. 768도 실패하면 512에서 새 process로 200 step

각 시험은 동일 source 비율의 길이별 MIX1K manifest, micro batch 1, gradient accumulation 8을 사용한다. 이전 OOM process를 재사용하지 않는다.

### 통과 조건

- 200 optimizer step 완료
- CUDA OOM·NaN·Inf·무한 반복 없음
- loss가 유한하고 초기 구간보다 감소 경향
- 마지막 측정에서 최소 1,024MiB VRAM headroom 확보
- checkpoint 저장 후 새 process에서 optimizer·scheduler 포함 resume 성공
- checkpoint 모델로 다섯 task가 빈 문자열 없이 생성

가장 긴 통과 길이를 formal `max_length`로 선택한다. 512도 실패하면 Phase 4 상태를 `차단`으로 기록한다. CPU offload, DeepSpeed, LoRA, packing, `torch.compile`, FlashAttention 교체는 자동 적용하지 않는다.

## 선택된 길이와 최종 manifest

Phase 4는 512/768/1024 길이별 deterministic manifest를 만들고, 통과한 길이의 manifest만 다음 canonical 이름으로 승격한다.

```text
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/manifests/mix1k_smoke_v1.jsonl
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/manifests/mix10k_v1.jsonl
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/manifests/mix20k_v1.jsonl
```

선택되지 않은 길이 manifest는 audit용으로 보존하되 Phase 5가 읽지 못하도록 별도 후보 경로에 둔다.

## Preflight 설정 계약

```yaml
model_revision: bf4786aa2a1908adce942d53976270132732f720
trust_remote_code: true
bf16: true
per_device_train_batch_size: 1
gradient_accumulation_steps: 8
gradient_checkpointing: true
use_cache: false
optim: paged_adamw_8bit
assistant_only_loss: true
packing: false
loss_type: chunked_nll
seed: 42
data_seed: 42
```

`max_length`는 Gate E 결과만 기록하며 미리 확정하지 않는다.

## 완료 Gate

- [ ] Gate A의 스키마·후보·누수·언어 검사가 전부 통과했다.
- [ ] 모든 source/task의 assistant loss mask assertion이 통과했다.
- [ ] 길이별 token 감사와 MIX1K⊂MIX10⊂MIX20 manifest 검사가 통과했다.
- [ ] K0-INSTRUCT 결과·설정·revision을 저장했다.
- [ ] BF16 full-parameter forward/backward와 8-bit optimizer step이 성공했다.
- [ ] 선택한 길이에서 200-step smoke와 resume가 성공했다.
- [ ] canonical MIX1K·10K·20K가 선택 길이 manifest를 가리킨다.
- [ ] `preflight_report.json`에 장비, peak VRAM/RAM, 버전, 실패 이력을 기록했다.

## 산출물

```text
data/reports/schema_validation.json
data/reports/split_leakage_report.json
data/reports/loss_mask_fixtures.jsonl
runs/K0-INSTRUCT/
runs/KI1K-SMOKE-v1/
runs/preflight_report.json
```

## 공식 자료

- [TRL 1.12.0 SFTTrainer](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)
- [Transformers 4.57.1 chat template](https://huggingface.co/docs/transformers/v4.57.1/en/chat_templating)
- [Transformers 4.57.6 optimizer enum](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/training_args.py)
- [bitsandbytes 0.50.2 8-bit optimizer](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/optimizers)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | TRL assistant-only loss | conversational data와 generation mask 필요 확인 |
| 2026-08-27 | TRL 1.12.0 config | `max_length`, `per_device_train_batch_size`, `packing`, `loss_type` 필드 확인 |
| 2026-08-27 | Transformers 4.57.6 source | `paged_adamw_8bit` optimizer 이름 확인 |
| 2026-08-27 | bitsandbytes optimizer 문서 | 8-bit state는 parameter memory를 줄이며 activation OOM은 별도임을 확인 |
