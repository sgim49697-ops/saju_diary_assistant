# Phase 4. 학습 전 데이터·모델 검증

| 항목 | 값 |
|---|---|
| 실행 상태 | 진행 중 |
| 선행 Phase | Phase 2·3 완료 |
| 입력 | 승인 staging `v0.2.0/build-847088ee804d`, 고정 Instruct 환경, eval set |
| 출력 | A~C candidate manifest·eval·K0 보고서, D~E의 선택된 `max_length`·smoke checkpoint |
| 완료 Gate | 데이터 Gate와 모델·메모리 Gate 동시 통과 |
| 웹 확인일 | 2026-08-28 |

## 목적

정식 10K·20K Run 전에 데이터 계약, chat serialization, assistant loss mask, Instruct 원본 성능, Full FT 메모리와 checkpoint 복구를 실제로 검증한다. smoke는 검증용 학습이며 공식 모델 성능 비교에 사용하지 않는다.

## Phase 4A~C 비학습 실행 경계

정본 v2.6에서 A~C와 D~E를 분리한다. A~C는 승인된 24K staging의 불변 부모를 검증하고, holdout·Core Eval과 학습 후보 manifest를 고정하며, 원본 모델 K0를 inference-only로 평가한다. 이 구간에서는 optimizer·gradient·backward·checkpoint를 만들지 않는다. A~C 통과는 D/E 학습 smoke를 시작할 수 있다는 뜻일 뿐 `training_promotion_allowed=true`나 canonical 학습 manifest 승격을 뜻하지 않는다.

- source holdout은 축별 100건씩 500건, Core Eval은 9범주 200항목으로 고정한다. 동일 명식 consistency 20항목은 두 case씩이어서 K0 총 생성 수는 720case다.
- Core Eval과 source holdout의 모든 전역 leakage group을 정식 후보에서 먼저 제외한다. 36개 cross-axis 동일 명식 중 20개는 consistency 평가에 함께 고정한다.
- K0는 BF16·SDPA·batch 1·greedy·`max_new_tokens=512`로 실행하고, 임의 네 기둥 생성·빈 출력·제어문자/special-token 노출·결정성 재생 실패만 Gate C 차단 조건으로 삼는다. reference overlap과 범주별 자동 계약 점수는 기준선 진단값이며 임의 합격 임계값을 만들지 않는다.
- 700항목 오프라인 검수 ZIP은 제한 원문이 있을 수 있으므로 저장소 밖에만 만들고 내부 ID·원천 locator를 제거한다. 이 패키지의 생성은 전문 사람 검수를 수행했다는 뜻이 아니며 `human_domain_review_performed=false`를 유지한다.
- K0 700항목은 안전·무결성, hard 계약 실패, 생성 상한, 반복, 한국어 비율 순서로 자동 위험 분류한다. 전체 결과는 Git 제외 경로에 두고 공개 보고서에는 심각도·신호 집계만, 검토용으로는 상위 40항목만 별도 고정한다. 이는 사람 전문 판독이나 품질 인증을 대신하지 않는다.
- `v1.0.0` K0는 모델·template·generation·runtime·prompt SHA-256이 모두 같은 case만 재사용하고, 새 eval 계약으로 지표를 다시 계산한다. 하나라도 다르면 새로 생성한다.

## Gate A. 데이터 검증

먼저 unified 후보와 고정 split을 검사한다. 최종 MIX1K·MIX10·MIX20 수량 검사는 Gate B가 길이별 manifest를 만든 뒤 다시 수행한다.

### 스키마·수량

- 공통 필수 필드와 enum 위반 0건
- ID·정규화 message hash duplicate 0건
- 동일 원천의 여러 질문 파생은 같은 `source_group_id` 안에서만 raw hash alias를 허용하고, 서로 다른 source group 사이 raw hash duplicate는 0건
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

후보 순서대로 MIX20을 채우고 source별 앞 절반으로 MIX10, MIX10의 앞 10%로 MIX1K를 만든다. 행 혼합비는 token 통계에 맞춰 바꾸지 않는다. source별 assistant loss token share와 `bazi-sft`·신살 파생본의 template 반복은 반드시 보고하되, A~C에는 임의 비율 임계값을 두거나 source를 재가중하지 않는다. 분포 영향은 K0와 후속 학습·평가 비교로 판정한다. 원문을 자르거나 제외 소스를 보충재로 넣지 않는다.

전체 24K의 최대 길이가 768 이하이면 768 candidate는 1024에도 그대로 적격이므로 중복 manifest를 만들지 않는다. 512는 정식 MIX20을 채울 수 없는 경우 Gate D 기능 smoke용 1K 부분집합만 별도로 만든다. 512 smoke의 Nemotron v6:v7 분포는 보고값이며, 정식 MIX1K·10K·20K의 고정 20:80 계약만 승격 조건으로 강제한다.

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

case별 prompt·reference·출력은 Git 제외 `runs/K0-INSTRUCT/`에 0600 권한으로 보존하고, 공개 보고서에는 집계만 투영한다. 첫 case를 같은 process에서 한 번 더 생성해 token ID 결정성을 검증한다. K0가 통과해도 사람 전문 검수는 별도이며, 오프라인 ZIP의 final feedback을 다시 검증·승인하기 전에는 품질 인증을 주장하지 않는다.

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
2. 실제 후보 상한 밖인 1024에서 1 optimizer step 진단
3. 정식 후보 전체를 수용하는 768에서 100 optimizer step 후 checkpoint 저장
4. 새 process에서 optimizer·scheduler를 포함해 step 100부터 resume하고 총 200 step 완료
5. 새 process에서 step 200 checkpoint를 다시 로드해 서로 다른 다섯 task 생성

각 시험은 동일 source 비율의 길이별 MIX1K manifest, micro batch 1, gradient accumulation 8을 사용한다. 1024는 실데이터를 더 수용하지 않는 padding-only 진단이므로 실패해도 768 정식 Gate를 대신 차단하지 않는다. 각 단계는 새 process에서 실행하고 이전 OOM process를 재사용하지 않는다.

### 통과 조건

- 200 optimizer step 완료
- CUDA OOM·NaN·Inf·무한 반복 없음
- loss가 유한하고 초기 구간보다 감소 경향
- 마지막 측정에서 최소 1,024MiB VRAM headroom 확보
- checkpoint 저장 후 새 process에서 optimizer·scheduler 포함 resume 성공
- checkpoint 모델로 다섯 task가 빈 문자열 없이 생성

전체 정식 후보를 무손실 수용하는 768을 formal `max_length`로 선택하되, 768의 200-step·resume·1GiB headroom 조건이 실패하면 Phase 4 상태를 `차단`으로 기록한다. CPU offload, DeepSpeed, LoRA, packing, `torch.compile`, FlashAttention 교체는 자동 적용하지 않는다.

## 선택된 길이와 최종 manifest

Phase 4A~C는 768/1024 공용 candidate와 512 기능-smoke 부분집합을 만들 뿐 canonical 이름으로 승격하지 않는다. Gate D/E가 가장 긴 통과 길이를 선택한 뒤 해당 candidate만 다음 canonical 이름으로 승격한다.

```text
data/derived/saju_1b_baseline/v1.1.0/build-<derived-hash>/manifests/mix1k_smoke_v1.jsonl
data/derived/saju_1b_baseline/v1.1.0/build-<derived-hash>/manifests/mix10k_v1.jsonl
data/derived/saju_1b_baseline/v1.1.0/build-<derived-hash>/manifests/mix20k_v1.jsonl
```

선택되지 않은 길이 manifest는 audit용으로 보존하되 Phase 5가 읽지 못하도록 별도 후보 경로에 둔다.

## Preflight 설정 계약

A~E 재검증 계약은 `configs/data_versions/saju_1b_baseline/preflight-v1.1.0.json`에 고정한다. 아래 학습 설정은 D/E에서만 사용한다.

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

정식 `max_length=768`, 진단 `max_length=1024`, 기능 검사 `max_length=512`를 서로 다른 역할로 고정한다.

## 완료 Gate

- [ ] 교정 staging 기반 Gate A의 스키마·후보·누수·언어 검사가 전부 통과했다.
- [ ] 모든 source/task의 assistant loss mask assertion이 통과했다.
- [ ] 길이별 token 감사와 MIX1K⊂MIX10⊂MIX20 candidate manifest 검사가 통과했다.
- [ ] K0-INSTRUCT 결과·설정·revision과 자동 위험 분류를 저장했다.
- [ ] BF16 full-parameter forward/backward와 8-bit optimizer step이 성공했다.
- [ ] 선택한 길이에서 200-step smoke와 resume가 성공했다.
- [ ] canonical MIX1K·10K·20K가 선택 길이 manifest를 가리킨다.
- [ ] `preflight_report.json`에 장비, peak VRAM/RAM, 버전, 실패 이력을 기록했다.

## 산출물

```text
data/derived/saju_1b_baseline/v1.1.0/build-<fingerprint>/
data/reports/saju_1b_baseline/preflight/v1.1.0/build-<fingerprint>/
runs/K0-INSTRUCT/v1.1.0/build-<fingerprint>/
runs/KI1K-SMOKE-v1/v1.1.0/build-<fingerprint>/
<저장소 밖>/saju-phase4-k0-review-<build>.zip
```

## 공식 자료

- [TRL SFTTrainer 최신 문서](https://huggingface.co/docs/trl/main/sft_trainer)
- [Transformers 4.57.1 chat template](https://huggingface.co/docs/transformers/v4.57.1/en/chat_templating)
- [Transformers 4.57.6 optimizer enum](https://github.com/huggingface/transformers/blob/v4.57.6/src/transformers/training_args.py)
- [bitsandbytes AdamW optimizer 최신 문서](https://huggingface.co/docs/bitsandbytes/reference/optim/adamw)
- [Transformers checkpoint resume](https://huggingface.co/docs/transformers/trainer_recipes)
- [PyTorch activation checkpoint](https://docs.pytorch.org/docs/stable/checkpoint)
- [PyTorch 2.13 release](https://pytorch.org/blog/pytorch-2-13-release-blog/)
- [PEFT LoRA](https://huggingface.co/docs/peft/main/package_reference/lora)
- [PEFT quantization](https://huggingface.co/docs/peft/developer_guides/quantization)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | TRL assistant-only loss | conversational data와 generation mask 필요 확인 |
| 2026-08-27 | TRL 1.12.0 config | `max_length`, `per_device_train_batch_size`, `packing`, `loss_type` 필드 확인 |
| 2026-08-27 | Transformers 4.57.6 source | `paged_adamw_8bit` optimizer 이름 확인 |
| 2026-08-27 | bitsandbytes optimizer 문서 | 8-bit state는 parameter memory를 줄이며 activation OOM은 별도임을 확인 |
| 2026-08-28 | Kanana 2 1.3B 고정 revision·로컬 snapshot | Instruct custom code·chat template·BF16 SDPA 고정 경로를 재확인 |
| 2026-08-28 | PyTorch 2.13 release | CUDA 13.0 기본 build와 Triton 3.7.1 pin을 확인하고 RTX 5070 Ti 실장비 cu130 환경을 유지 |
| 2026-08-28 | TRL 1.12.0 및 설치본 1.12.0 | conversational template generation mask와 assistant-only label 경로를 전체 24K에 직접 검증 |
| 2026-08-28 | PEFT LoRA·quantization | LoRA는 원 가중치 동결, QLoRA는 quantized base 위 adapter라는 별도 계약이므로 Full FT를 자동 변경하지 않기로 결정 |
| 2026-08-28 | TRL·Transformers·bitsandbytes·PyTorch 최신 공식 문서와 설치본 | `assistant_only_loss`, `chunked_nll`, `paged_adamw_8bit`, Trainer optimizer/scheduler resume, non-reentrant activation checkpoint 경로를 비교하고 Full FT 768 계약을 유지 |

## 진행 기록

- 2026-08-28
  - 작업 요약: 교정 staging을 부모로 하는 Phase 4 `v1.1.0` 계약, K0 exact-match 재사용·700항목 자동 위험 분류, 단계별 Full FT smoke/resume와 canonical 승격 실행기를 구현했다.
  - 변경 범위: A~C는 계속 비학습으로 격리하고, D/E만 BF16 전체 파라미터·SDPA·assistant-only `chunked_nll`·실제 `paged_adamw_8bit` state를 검사하도록 했다. 1024는 진단, 768은 100→200-step 정식 resume로 역할을 분리했다.
  - 검증: Ruff, Python compile, Phase 4 단위 테스트, `validate-contract`, dry-run, 과거 v1.0 artifact hash chain 재검증을 통과했다.
  - 남은 이슈·후속 작업: 이 항목은 실행 전 구현 체크포인트다. 새 build A~C/K0·triage와 D/E GPU 단계, canonical 승격을 순서대로 실행하며 그 전까지 `training_promotion_allowed=false`다.
- 2026-08-28
  - 작업 요약: 첫 v1.1 Gate A/B 실행에서 YEJI 포함 cross-axis group이 42개로 늘자 기존 선택식이 정확히 20개로 상한 처리하지 못하는 회귀를 발견해 수정했다.
  - 변경 범위: YEJI 포함 그룹을 결정론적으로 최대 20개까지만 우선 선택하고 부족분만 다른 교차 축 그룹으로 채우도록 했으며 42개 입력 회귀 테스트를 추가했다. 실패한 임시 build는 최종 경로로 승격되지 않았다.
  - 검증: 새 staging의 cross-axis 76개를 `YEJI 포함 42`, `그 외 34`로 직접 집계했고 targeted test·Ruff·`git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 수정 구현을 새 Git checkpoint로 고정한 뒤 새 fingerprint에서 A~E를 다시 시작한다.

- 2026-08-28
  - 작업 요약: 승인된 24K staging과 Phase 3 모델을 부모로 Phase 4A~C 비학습 preflight `v1.0.0/build-a6813ba3b778`을 구현·실행했다. Gate A/B/C는 통과했고 Phase 4는 `부분 진행`으로 판정했다.
  - 변경 범위: 고정 계약·Python header sysroot·24K schema/token/loss-mask 검사, group-first Core Eval 200·source holdout 500, 중첩 MIX candidate, BF16 SDPA K0 720case, 저장소 밖 오프라인 검수 ZIP과 원문 없는 공개 보고서·테스트를 추가했다. optimizer·gradient·backward·checkpoint·canonical 승격은 수행하지 않았다.
  - 데이터 결과: 24,000행·고유 message hash 24,000, raw hash 19,500과 동일 source group 내부 alias 4,500행, group 밖 raw duplicate 0, cross-axis group 36개를 확인했다. eval과 candidate leakage group 교집합은 0이다. 전체 최대 길이는 716이고 Nemotron 9,619행이 512를 넘지만 768은 전부 수용한다. MIX20 assistant-loss token share는 Nemotron 71.805712%이며 계약대로 재가중하지 않았다.
  - K0 결과: 700항목·720case, 빈 출력·제어문자·special-token 노출·missing-chart 임의 명식 각 0건, 결정성 재생 통과, peak VRAM 2,752,092,672 bytes, 7,426.566초를 기록했다. EOS 종료는 349/720이고 371case가 512-token 상한에 도달했다. strict 자동 계약은 missing-chart 5/5, 일반 instruction 4/5, 신살 8/20, 모순 exact-string 0/35로 후자의 품질 판단은 사람 검수에 남겼다.
  - 검증: `validate-contract`, runtime native-JIT probe, 24K `build`, K0 `run-k0`, 전체 `verify`, ZIP 700항목·720case/내부 checksum 검증, Windows Chrome `file://` 렌더링을 통과했다. private manifest SHA-256은 `2ed5c03c…b49a50`, K0 manifest는 `67d6ca3b…02ab1`, public manifest는 `7750f462…791b`, 검수 ZIP은 `517abea9…c3ea`다.
  - 남은 이슈·후속 작업: 사람 전문 검수는 아직 0/700이고 `approved_derived=null`, `training_promotion_allowed=false`다. 저장소 밖 `saju-phase4-k0-review-build-a6813ba3b778.zip`을 검수한 뒤 별도 승인하고, Phase 4D 단일 batch forward/backward·optimizer step과 Phase 4E 1024→768→512 200-step smoke/resume를 수행해야 한다. 그전에는 Phase 5 학습을 시작하지 않는다.
