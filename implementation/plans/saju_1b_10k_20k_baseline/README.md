# 한국형 사주 1.3B 10K·20K Baseline 정본

이 문서는 `kakaocorp/kanana-2-1.3b-base`에 다중 소스 한국어 사주·대화 데이터를 Full Fine-tuning하는 초기 실험의 정본 인덱스다. 세부 실행은 반드시 Phase 순서와 Gate를 따르며, archive 원본은 참고 자료일 뿐 실행 기준이 아니다.

| 항목 | 값 |
|---|---|
| 문서 버전 | `1.0.0` |
| 정본화 기준일 | 2026-08-27 |
| 주 장비 | NVIDIA GeForce RTX 5070 Ti 16GiB, WSL2 |
| 주 모델 | `kakaocorp/kanana-2-1.3b-base` |
| 실험 범위 | 1K smoke, 독립 10K·20K Full FT, 사후 평가와 v1 결정 |
| 배포 성격 | YEJI v9와 AI Hub 이용조건이 포함된 비상업 연구·취미 `NC` 실험 |

## 고정 실험 계약

- 모델 학습 방식은 BF16 전체 파라미터 Full Fine-tuning이다. LoRA/QLoRA로 자동 전환하지 않는다.
- 10K와 20K는 같은 Base revision에서 각각 독립적으로 1 epoch 학습한다.
- `MIX10-v0`는 `MIX20-v0`의 정확한 부분집합이어야 한다.
- 데이터 행 비율은 Nemotron 35%, YEJI v9 20%, YEJI Processed 25%, YEJI Translated 5%, 한국어·공감 대화 15%로 고정한다.
- 평가셋과 group holdout은 학습 manifest보다 먼저 고정한다.
- 해석 답변은 soft/reference label이며, 계산 가능한 구조만 검산 후 hard label로 승격한다.
- 모델 출력은 사람 또는 규칙 검증 없이 학습 정답으로 재사용하지 않는다.

## 단계 지도

| Phase | 문서 | 실행 상태 | 핵심 Gate |
|---:|---|---|---|
| 0 | [거버넌스·실험 계약](phase-0-governance.md) | 미시작 | 라이선스·범위·재현성 승인 |
| 1 | [데이터 수집](phase-1-data-collection.md) | 부분 진행 | 모든 원본 revision·해시·이용조건 고정 |
| 2 | [데이터 전처리](phase-2-data-preprocessing.md) | 미시작 | split·누수·혼합·토큰 감사 통과 |
| 3 | [학습 모델·환경 준비](phase-3-model-preparation.md) | 미시작 | 고정 환경에서 모델·template 로드 성공 |
| 4 | [학습 전 데이터·모델 검증](phase-4-preflight-validation.md) | 미시작 | 데이터 Gate와 200-step smoke 모두 통과 |
| 5 | [Baseline 학습](phase-5-baseline-training.md) | 미시작 | K10·K20 독립 Run과 산출물 완결 |
| 6 | [평가·검수·v1 결정](phase-6-evaluation-v1-decision.md) | 미시작 | 고정 평가 후 50K 또는 v1 Lite 경로 결정 |

Phase 상태 값은 `미시작`, `부분 진행`, `진행 중`, `차단`, `완료`만 사용한다. 앞 Phase가 `완료`가 아니면 뒤 Phase의 공식 산출물을 만들지 않는다.

```text
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
                         데이터 Gate ─┘       └─ 모델 Gate
```

## 예정 산출물 경로

아래는 이후 구현 Phase가 만들 계약 경로다. 정본화 작업에서는 생성하지 않는다.

```text
data/
├── raw/<source>/<revision>/
├── unified/v0_raw/
├── manifests/
│   ├── mix1k_smoke_v0_nc.jsonl
│   ├── mix10k_v0_raw_nc.jsonl
│   └── mix20k_v0_raw_nc.jsonl
├── eval/
│   ├── source_holdout_600.jsonl
│   ├── core_eval_200.jsonl
│   └── human_review_400.jsonl
└── reports/
    ├── source_inventory.json
    ├── token_stats_mix10.json
    ├── token_stats_mix20.json
    └── license_manifest.json

runs/
├── K0-BASE/
├── K0-INSTRUCT/
├── K1K-SMOKE-NC/
├── K10-MIX-v0-RAW-NC/
└── K20-MIX-v0-RAW-NC/
```

## 웹 재검증 규칙

각 Phase 실행자는 시작 시점과 Gate 판정 직전에 해당 문서의 공식 자료를 다시 연다.

1. 모델·데이터 API에서 revision, 공개 여부, gated 여부, 라이선스를 확인한다.
2. 라이브러리는 고정 버전 문서를 우선하고, 실제 배포 메타데이터와 의존 조건을 교차 확인한다.
3. 확인 결과를 문서의 `웹 확인 기록`에 날짜, URL, 확인값, 결정 영향과 함께 남긴다.
4. 현재 정본과 충돌하면 실행을 멈추고 정본 버전을 올린 뒤 변경 이유를 기록한다.
5. 기술 정보는 공식 문서, 공식 저장소, 모델·데이터 카드, 공식 배포 메타데이터만 근거로 사용한다.

## 원본 내용 매핑

| archive 원본 절 | 정본 위치 |
|---|---|
| 0, 3, 16 | Phase 0, README |
| 1, 15-Step 1~2 | Phase 1 |
| 2, 4~8, 14, 15-Step 2~6 | Phase 2 |
| 11.1~11.2, 11.4 | Phase 3 |
| 3.2, 7, 11.3, 12 일부, 15-Step 7 | Phase 4 |
| 3, 11, 15-Step 8~10 | Phase 5 |
| 9~10, 12~13, 15-Step 11~12 | Phase 6 |
| 17 | 각 Phase의 공식 자료 |

## 정본화 변경 결정

- Kanana 파생 모델과 데이터의 라이선스 검토를 데이터 다운로드보다 앞선 Gate로 승격했다.
- Kanana Base에는 chat template가 없으므로 Instruct의 고정 template를 후보로 사용하되, assistant loss mask 검증 전에는 학습을 허용하지 않는다.
- TRL 1.12.0 기준으로 `max_seq_length`를 `max_length`, `micro_batch_size`를 `per_device_train_batch_size`, 개념형 `precision`을 `bf16=True`로 교정했다.
- 16GiB GPU에서 Full FT가 된다고 가정하지 않고, 512 기능 검사와 1024→768→512 memory smoke를 통과 조건으로 바꿨다.
- 20행 블록은 정확한 소스 수량 산출에만 쓰고, 실제 학습 manifest는 seed 42로 최종 shuffle해 주기적 순서 편향을 막는다.
- 512 smoke 실패 시 DeepSpeed·CPU offload·LoRA로 자동 우회하지 않고 Phase 4를 `차단`한다.

## 원본 보관

- [초기 통합 플랜 원본](archive/saju_1b_10k_20k_baseline_plan.original.md)
- 원본 SHA-256: `11dde66505aa3ca90834488a877a0f4db42512d9cb377880d935f71bc71d3724`

## 진행 기록

- 2026-08-27
  - 작업 요약: 원본 0~17절을 인덱스와 7개 Phase로 정본화하고, 모델·데이터 revision, Kanana/AI Hub 라이선스, PyTorch cu128, TRL assistant loss, bitsandbytes GPU 지원을 공식 웹 자료로 재확인했다.
  - 변경 범위: 이 계획 디렉터리의 Markdown 문서만 추가했다. 코드·데이터·환경·모델은 변경하지 않았다.
  - 검증: 원본 SHA-256 `11dde66505aa3ca90834488a877a0f4db42512d9cb377880d935f71bc71d3724` 일치, Phase 7개와 내부 링크 8개 검사 통과, 정본 8개 `git diff --cached --check` 통과, 공식 PyTorch 참조 URL HTTP 200을 확인했다. 보관 원본의 기존 줄 끝 공백 4곳은 byte-for-byte 보존을 위해 유지했다.
  - 남은 이슈·후속 작업: 구현은 시작하지 않았다. Phase 0의 라이선스·실험 계약 승인 후 Phase 1부터 순서대로 실행한다.
