# 한국형 사주 1.3B 10K·20K Baseline 정본

이 문서는 `kakaocorp/kanana-2-1.3b-instruct`에 상업 활용 후보로 선별한 사주·한국어 대화 데이터를 Full Fine-tuning하는 초기 실험의 정본 인덱스다. 실제 학습에는 각 라이선스·원천·품질 Gate를 모두 통과한 행만 들어간다. 세부 실행은 반드시 Phase 순서와 Gate를 따르며, archive 원본과 `kanana_saju_dataset_guide.html`은 참고 자료일 뿐 실행 기준이 아니다.

| 항목 | 값 |
|---|---|
| 문서 버전 | `3.4.0` |
| 정본화 기준일 | 2026-08-29 |
| 주 장비 | NVIDIA GeForce RTX 5070 Ti 16GiB, WSL2 |
| 주 모델 | `kakaocorp/kanana-2-1.3b-instruct@bf4786aa2a1908adce942d53976270132732f720` |
| 실험 범위 | 1K smoke, 독립 10K·20K Full FT, 사후 평가와 v2 Lite 결정 |
| 배포 성격 | 광고형 자체 서비스 후보. 원문·checkpoint 공개와 원격 모델 접근 판매는 별도 Gate |

## 고정 실험 계약

- 모델 학습 방식은 BF16 전체 파라미터 Full Fine-tuning이다. LoRA/QLoRA로 자동 전환하지 않는다.
- 10K를 먼저 1 epoch 학습한다. Gate v2 기술·안전 hard gate는 추가 baseline 실험 허용 여부를, 품질 목표는 배포·품질 승격 여부를 판정한다. 20K는 같은 Instruct revision에서 독립 시작하며, 2026-08-29에 받은 별도 명시 확인은 v1.2 실행 계약에만 적용한다.
- `MIX1K-v2 ⊂ MIX10-v2 ⊂ MIX20-v2`이어야 한다.
- 데이터 행 비율은 Nemotron 34%, 검산·한국어화한 `bazi-sft` 20%, AI Hub #86 단일턴 7.5%, 멀티턴 7.5%, 검증된 YEJI 신살 규칙 5%, deterministic 사주 QA 10%, 사주-일기 앱 브리지 16%로 고정한다.
- Nemotron 내부는 v6 20%·v7 80%로 고정한다.
- 평가셋과 group holdout은 학습 manifest보다 먼저 고정한다.
- 기존 Core Eval 300·source holdout 700은 개발·진단용으로만 사용하고, 최종 성능 주장은 미사용 reserve에서 component 단위로 새로 봉인한 7축 blind test를 사용한다.
- KASI와 외부 구현의 공개 fixture는 `external_conformance`로 분리하며 학습·source blind에 섞지 않는다.
- 모든 데이터 산출물은 `vMAJOR.MINOR.PATCH/build-<fingerprint>` 경로에 보관하고 기존 build를 덮어쓰지 않는다.
- source 내부 묶음과 split 누수 경계를 분리하며, Nemotron·`bazi-sft`·향후 YEJI 파생본의 동일 8자 명식은 원천이 달라도 하나의 전역 leakage group으로 묶는다.
- 해석 답변은 soft/reference label이며, 계산 가능한 구조만 검산 후 hard label로 승격한다.
- 모델 출력은 사람 또는 규칙 검증 없이 학습 정답으로 재사용하지 않는다.
- 모델은 생년월일에서 사주 원국을 계산하지 않는다. 런타임 계산기가 제공한 구조화 명식을 근거로 해석한다.

## 단계 지도

| Phase | 문서 | 실행 상태 | 핵심 Gate |
|---:|---|---|---|
| 0 | [거버넌스·실험 계약](phase-0-governance.md) | 완료 | 라이선스·범위·재현성 승인 |
| 1 | [데이터 수집](phase-1-data-collection.md) | 완료 | 네 원천의 revision·해시·이용조건과 #86 구조 Gate 통과 |
| 2 | [데이터 전처리](phase-2-data-preprocessing.md) | 완료 | 품질 보정 7축 24K와 로컬 전용 AI Hub 후보 10K 완결, 학습 승격은 Phase 4 Gate로 분리 |
| 3 | [학습 모델·환경 준비](phase-3-model-preparation.md) | 완료 | 고정 PyTorch cu130 환경에서 모델·tokenizer·template 전체 BF16 오프라인 로드 성공 |
| 4 | [학습 전 데이터·모델 검증](phase-4-preflight-validation.md) | 완료 | 품질 보정 staging의 A~E·1,020case·Full FT 200-step resume와 768 canonical 승격 통과 |
| 5 | [Baseline 학습](phase-5-baseline-training.md) | 진행 중 | KI10·Gate v2·KI20 preflight 완료, `run-1f5d732cae67` 1 epoch 본학습 실행 중 |
| 6 | [평가·검수·v2 결정](phase-6-evaluation-v2-decision.md) | 미시작 | 고정 평가 후 50K 또는 v2 Lite 경로 결정 |

Phase 상태 값은 `미시작`, `부분 진행`, `진행 중`, `차단`, `완료`만 사용한다. 앞 Phase가 `완료`가 아니면 뒤 Phase의 공식 산출물을 만들지 않는다.

```text
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6
                         데이터 Gate ─┘       └─ 모델 Gate
```

## 예정 산출물 경로

아래는 Phase별 계약 경로다. 원본은 upstream revision, 각 후속 단계는 데이터 SemVer와 입력 fingerprint를 함께 사용한다. `latest` 링크를 만들지 않으며 registry의 명시적 승인 포인터만 다음 단계가 읽는다.

```text
data/
├── raw/<source>/<revision>/
├── audit/saju_1b_baseline/v1.2.0/build-ca756f3eb89f/  # 비공개 locator·결정·seal
├── staging/saju_1b_baseline/v1.0.0/build-a5a9e76d6a8c/ # Git 제외·현재 품질 보정 24K
├── derived/saju_1b_baseline/v2.0.0/build-6f32d52c2868/
│   ├── unified/
│   ├── manifests/
│   └── eval/
├── derived/saju_1b_baseline/evaluation-split/v1.0.0/build-a5a04ab96594/
├── derived/saju_1b_baseline/evaluation-split/v1.1.0/build-d2f9e1623e96/ # v1.0 봉인 유지·dev 진단 확장
├── derived/saju_1b_baseline/evaluation-split/v1.2.0/build-e885b47cae74/ # Gate v2 typed contract·handoff 50
├── derived/saju_1b_baseline/phase5-readiness/v1.0.0/build-f6c8171f454f/ # 과거 readiness
├── derived/saju_1b_baseline/phase5-readiness/v1.1.0/build-201010b37e40/
├── derived/saju_1b_baseline/phase5-readiness/v1.2.0/build-e325f16096dd/ # 현재 KI10 자동 Gate 계약
└── reports/
    └── saju_1b_baseline/
        ├── audit/v1.2.0/build-ca756f3eb89f/
        ├── audit-review/v1.2.0/build-ca756f3eb89f/reviewer-v1.1.0/
        ├── preprocessing-staging/v1.0.0/build-a5a9e76d6a8c/
        ├── model-preparation/v1.0.0/build-32e2c84af3d3/
        ├── preflight/v1.0.0/build-a6813ba3b778/       # 과거 A~C build
        ├── preflight/v1.1.0/build-a1a34616dd72/      # 과거 A~E 완료 build
        ├── preflight/v2.0.0/build-6f32d52c2868/      # 현재 품질 보정 canonical
        ├── evaluation-split/v1.0.0/build-a5a04ab96594/ # 공개 split·누수 요약
        ├── evaluation-split/v1.1.0/build-d2f9e1623e96/ # reference overlap·persona guard 공개 요약
        ├── evaluation-split/v1.2.0/build-e885b47cae74/ # typed scorer·handoff 확장
        ├── pretraining-audit/v1.0.0/build-c38926f86a3d/ # 20K 의미·출처 전수 감사
        ├── phase5-readiness/v1.0.0/build-f6c8171f454f/ # 과거 비학습 계약
        ├── phase5-readiness/v1.1.0/build-201010b37e40/ # 봉인 평가 연결 계약
        ├── phase5-readiness/v1.2.0/build-e325f16096dd/ # 감사·평가·runner 연결 계약
        ├── phase5-gate/v2.0.0/KI10-MIX-v2/gate-df26e962e145/ # 기술/안전·품질 목표 분리
        ├── phase5-preflight/v1.1.0/preflight-b47fe12f03a4/ # KI20 비학습 처리량 검증
        ├── phase5-readiness/v1.3.0/build-7eb4c34364cc/ # KI20 preflight readiness
        ├── project-status/v1.0.0/build-e23e3501a200/ # KI20 preflight 대기 현황
        └── phase-verification/v1.0.0/review-20260828/

configs/data_versions/saju_1b_baseline/
├── source-bundle-v1.1.0.json
├── audit-policy-v1.0.0.json                 # 과거 build 검증용
├── audit-policy-v1.2.0.json                 # 승인된 전체 원천 감사
├── yeji-rule-corrections-v1.2.0.json
├── preprocessing-staging-v0.1.0.json
├── preprocessing-staging-v0.2.0.json
├── language-bank-v1.0.0.json
├── license-review-v1.0.0.json
├── preflight-v1.0.0.json
├── preflight-v1.1.0.json
├── preflight-v2.0.0.json
├── phase5-readiness-v1.0.0.json
├── evaluation-split-v1.0.0.json
├── phase5-readiness-v1.1.0.json
├── pretraining-audit-v1.0.0.json
├── evaluation-split-v1.1.0.json
├── phase5-readiness-v1.2.0.json
├── evaluation-split-v1.2.0.json
├── phase5-readiness-v1.3.0.json
├── project-status-v1.0.0.json
└── registry.json

configs/model_versions/saju_1b_baseline/
├── model-preparation-v1.0.0.json
├── phase5-quality-gate-v2.0.0.json
├── phase5-training-v1.1.0.json
└── phase5-training-v1.2.0.json
configs/chat_templates/kanana2_sft.jinja
requirements.txt
requirements-phase3.lock.txt
PROJECT_STATUS.html

runs/
├── K0-INSTRUCT/v2.0.0/build-2feaee353252/
├── KI1K-SMOKE-v2/v2.0.0/build-2feaee353252/
├── KI10-MIX-v2/
├── KI20-MIX-v2/v1.2.0/run-1f5d732cae67/
└── KI20-MIX-v2-LITE/  # Phase 6 결정 시에만 생성
```

## 모델·데이터 조사 결론

### 모델

`kakaocorp/kanana-2-1.3b-instruct`의 고정 revision은 `bf4786aa2a1908adce942d53976270132732f720`이다. 공식 문서명은 `KANANA OPEN LICENSE AGREEMENT`이다. 고정 revision의 API license tag는 `other`, 2026-08-28 현재 모델 카드 tag는 `kanana-open-license`로 조회되므로 tag 대신 고정 revision의 LICENSE 원문을 판정 기준으로 삼는다. Section 4.2에 따라 Section 4.1의 별도 허가 대상이 아닌 자체 서비스 개발·운영은 별도 상업 라이선스 없이 가능하다. API·클라우드 원격 접근 판매, SI/on-premise 판매, on-device 판매는 Section 4.1의 별도 상업 라이선스 대상이다. 서비스·배포 시 라이선스 사본, 수정 표시, Notice, `Powered by Kanana` 표시를 제공하고, Kanana로 학습·향상한 모델을 배포할 때는 이름에 `Kanana` prefix를 적용한다. 과거 보고서의 `Kanana Open License 4.2`는 버전명이 아닌 조항을 버전처럼 적은 표현이므로 [현재 라이선스 재검토](../../../configs/data_versions/saju_1b_baseline/license-review-v1.0.0.json)가 대체한다. 과거 파일은 build hash 보존을 위해 수정하지 않았다. 이 결론은 법률 자문을 대체하지 않으며 서비스 형태가 바뀌면 다시 검토한다.

### 활성 학습 소스

| 소스 | 고정 revision/버전 | 라이선스·정책 | 10K | 20K | 사용 결정 |
|---|---|---|---:|---:|---|
| Nemotron Saju | `ffb934248746a2dea64ef771c0d86e1743d25702` | CC BY 4.0 | 3,400 | 6,800 | 출처·변경 표시 후 허용 |
| `AmareshHebbar/bazi-sft` | `fad87063b317612e4164dfb0e0e08572c3831df4` | Apache 2.0 | 2,000 | 4,000 | 구조 검산·한국어 재렌더 후 허용 |
| AI Hub 감성대화 #86 단일턴 축 | 승인 배포본 | AI Hub 일반정책 | 750 | 1,500 | 승인·비공개·출처 표시 조건부 허용 |
| AI Hub 감성대화 #86 멀티턴 파생 축 | 위와 동일 | 위와 동일 | 750 | 1,500 | 동일 대화 그룹의 연속 발화만 사용, 단일턴 축과 group 분리 |
| YEJI 신살 규칙 파생본 | `84583ca54e8fce257d3d5efd015bca1263a1cfe9` | MIT + 원천 MIT | 500 | 1,000 | 단일 파일 선별·교정·자체 QA 생성 후 허용 |
| deterministic 사주 QA | [`configs/saju_calculation_policy.json`](../../../configs/saju_calculation_policy.json) | 자체 생성·원천별 고지 | 1,000 | 2,000 | deterministic fact와 policy-bound fact만 허용 |
| 사주-일기 앱 브리지 | 품질 보정 staging `v1.0.0` | 자체 생성·부모 원천별 고지 | 1,600 | 3,200 | 구조화 명식을 입력으로 받은 공감·기록 연결만 허용 |

Nemotron 1K 몫은 v6 68행·v7 272행, 10K 몫은 v6 680행·v7 2,720행, 20K 몫은 v6 1,360행·v7 5,440행이다. `bazi-sft`는 제공된 네 기둥을 입력 사실로 삼아 일간·오행·규칙 조건을 다시 계산하며, 원문의 날짜·지역에서 네 기둥을 학습시키지 않는다. AI Hub 데이터는 원문을 외부에 공개하지 않고 개인정보·자해·의료 진단·과도한 훈계 행을 제외한다.

AI Hub #86은 영리·비영리 연구개발이 허용되지만 AI 모델 학습 목적과 신청·승인 범위로 한정한다. 사업 결과물에 NIA·AI Hub 출처를 표시하고, 미승인 제3자에게 열람·제공·양도·대여·판매하지 않으며, 개인정보를 발견하면 AI Hub에 신고하고 다운로드한 해당 데이터를 삭제한다. 데이터셋 자체 판매는 사전 협의 없이 허용하지 않는다.

YEJI Rules에서는 `rules/shensha_51.json`만 사용한다. 파일 SHA-256은 `9a11e1502983969407c43f82c65de6736b344da1a623e7a6557ad8b20cda939e`, 원천은 MIT의 `chxb/shensha@5b90110e55feb92303ef7853ecacdb6f9ed59eac`이다. 감사 v1.2는 원본을 바꾸지 않고 `词馆`의 金 간지, `五鬼` category, 덕수귀인 조건·매핑, 동자살 OR 조건을 고정 manifest overlay 다섯 항목으로 교정한다. 51규칙 evaluator 결과만 자체 한국어 QA로 사용한다.

### 제외·격리 소스

| 소스 | 결정 | 이유 |
|---|---|---|
| YEJI Fortune-Telling KO v9 | 학습 제외 | CC BY-NC 4.0으로 광고형 서비스와 양립하지 않음 |
| YEJI Processed | 학습 제외 | MIT 표기와 별개로 원천 권리 사슬이 불명확하고, 실제 샘플에 외부 웹 문구와 품질 오류가 섞임 |
| YEJI BaZi Interpretations | 학습 제외 | 데이터 카드가 비어 있고 명시 라이선스가 없음 |
| YEJI Rules의 고전·분석 파일 | 학습 제외 | `mymmsc/books`의 무라이선스 인터넷 수집본에서 파생된 파일이 포함됨 |
| YEJI BaZi Translated KO | 학습 제외·감사 참고 전용 | 상업 이용은 조건부 가능하지만 해석 지식이 없고 번역·질문·라벨 품질 Gate 실패 |
| AI Hub #271 한국어 감정 정보 연속 대화 | 학습·다운로드 제외 | 일반 AI Hub 데이터가 아니라 KETI 데이터로 분류되며, KETI 정책상 상업 이용은 별도 협의 대상 |

### YEJI BaZi Translated KO 상세 감사

- 번역본 revision `b494353378ea18a54f3502066e8075902049ec2f`의 원천은 `czuo03/bazi-calculate-rlvr@990b5a5f67398af0584e5e5427747b7742fc09e1`이다.
- 원천은 CC BY 4.0이고 번역 기여분이 MIT로 표시돼 있으므로 결합 데이터는 MIT 단독이 아니다. 사용 시 원천 저작자·CC BY 링크·번역자·변경 사실을 함께 표시해야 한다.
- 실제 번역 모델은 meta commit 기준 `Qwen3-4B-Instruct-2507`이며 데이터 카드의 Qwen2.5 설명과 다르다.
- 전체 262,980행은 fake `test_0` 1행과 원천 train의 두 번째 행부터 마지막 행까지 대응한다. 원천 test 2,000행은 포함되지 않았다.
- 답변은 항상 네 개의 한국어 간지 쌍이며 십신·오행·용신·대운·세운·격국·재물·직업·건강·연애 해석을 포함하지 않는다.
- 한국어 질문 33,371행(12.6896%)에 중국어가 남아 있고, 3,101행(1.1792%)에는 정답과 다른 완성형 네 기둥이 질문에 삽입돼 있다.
- 시각 없는 동일 질문의 상충 label은 2,380개 그룹·5,148행이고, 개행·장문 번역 이상은 3,062행이다.
- 원천 결과는 `sxtwl==2.0.7` 기본값과 262,980/262,980행 일치했다. `lunar-python==1.4.8`의 즉시 절입 기준과는 4,265행(1.621796%)이 달랐으며 모두 월주 또는 연주+월주 차이다.
- 23시대는 `sxtwl` 기본 야자시 관행을 따르고 시간대·장소·경도·진태양시 계약이 없다.
- 최종 판정은 `상업권 조건부 통과 / 원본 SFT 품질 실패 / 해석 지식 가치 없음 / 계산 감사 참고용`이다.

## 웹 재검증 규칙

각 Phase 실행자는 시작 시점과 Gate 판정 직전에 해당 문서의 공식 자료를 다시 연다.

1. 모델·데이터 API에서 revision, 공개 여부, gated 여부, 라이선스를 확인한다.
2. 라이브러리는 고정 버전 문서를 우선하고, 실제 배포 메타데이터와 의존 조건을 교차 확인한다.
3. 확인 결과를 문서의 `웹 확인 기록`에 날짜, URL, 확인값, 결정 영향과 함께 남긴다.
4. 현재 정본과 충돌하면 실행을 멈추고 정본 버전을 올린 뒤 변경 이유를 기록한다.
5. 기술 정보는 공식 문서, 공식 저장소, 모델·데이터 카드, 공식 배포 메타데이터만 근거로 사용한다.

## 데이터 버전 규칙

- major는 공통 스키마·태스크 계약 변경, minor는 원천·필터·template·split·구성 변경, patch는 동일 레코드의 보고서·검토 메타 변경에 사용한다.
- source build는 네 `SOURCE_MANIFEST.json`, audit build는 source build와 감사 정책·seed·감사 코드 hash를 부모 입력으로 기록한다. 향후 derived build는 승인된 audit seal을 추가 부모로 삼는다.
- build hash 입력에는 절대경로와 실행 시각을 넣지 않는다. 전체 SHA-256을 manifest에 저장하고 디렉터리에는 앞 12자리를 사용한다.
- 동일 build 재실행은 검증만 수행한다. 사람 판정 수정은 같은 build의 append-only revision으로 남기고, 원천·정책·교정 계약·감사 코드가 달라지면 새 버전·build를 만든다.

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

- Kanana Instruct와 데이터의 라이선스 검토를 데이터 다운로드보다 앞선 Gate로 유지한다.
- Instruct 저장소의 native chat template를 고정하되 assistant loss mask 검증 전에는 학습을 허용하지 않는다.
- Base 학습·비교와 `NC` Run을 제거하고 광고형 자체 서비스 후보용 allowlist 혼합으로 교체했다.
- 명식 계산을 모델 학습에서 분리하고, 구조화 명식에 근거한 해석·규칙 적용만 학습한다.
- TRL 1.12.0 기준으로 `max_seq_length`를 `max_length`, `micro_batch_size`를 `per_device_train_batch_size`, 개념형 `precision`을 `bf16=True`로 교정했다.
- RTX 5070 Ti Blackwell 환경은 최신 안정 PyTorch 2.13.0·torchvision 0.28.0·TorchAudio 2.11.0의 cu130 wheel로 고정하고 native `sm_120`·BF16·bitsandbytes CUDA backend를 실제 검증했다.
- 16GiB GPU에서 Full FT가 된다고 가정하지 않는다. 512 기능 검사, 후보 상한 밖 1024 진단, 후보 전체를 수용하는 768의 100→200-step checkpoint resume를 통과 조건으로 둔다.
- 20행 블록은 정확한 소스 수량 산출에만 쓰고, 실제 학습 manifest는 seed 42로 최종 shuffle해 주기적 순서 편향을 막는다.
- 512 smoke 실패 시 DeepSpeed·CPU offload·LoRA로 자동 우회하지 않고 Phase 4를 `차단`한다.
- KI10 Gate v1은 불변 이력으로 보존하고 v2부터 기술·안전 hard gate와 배포 품질 목표를 분리한다. hard gate 통과는 추가 실험만 허용하며 품질 인증을 뜻하지 않는다.
- KI20 목적함수는 uniform assistant-token `chunked_nll`로 유지한다. weighted sampler·DFT는 baseline 목표 분포를 바꾸므로 사용하지 않고 축별 macro 지표로 편중을 공개한다.
- 16GiB hard cap 안에서 실측 비교한 KI20 runtime은 train `4×accumulation 2`, worker 0, eval batch 8이다. RAM·swap은 진단값이며 GPU 전체 사용량만 hard gate다.
- tokenizer regex와 YaRN 경고는 현 model·tokenizer fingerprint를 바꾸지 않는다. 교정은 별도 model/data/run version으로 격리한다.

## 원본 보관

- [초기 통합 플랜 원본](archive/saju_1b_10k_20k_baseline_plan.original.md)
- 원본 SHA-256: `11dde66505aa3ca90834488a877a0f4db42512d9cb377880d935f71bc71d3724`

## 진행 기록

- 2026-08-31
  - 작업 요약: 외부 `saju-mix20k-v3-review-ready`를 원본 불변으로 감사하고 `v3.0.1-repaired` private 보정 build와 public 집계 intake, 고정 Kanana tokenizer 비학습 preflight를 생성했다. 실제 학습은 실행하지 않았다.
  - 변경 범위: strict chart/period tool, model-facing result allowlist, session/상대 날짜, provenance·권위·restricted 계약과 4K 검수 큐를 추가했다. 기간 hard fact 유실을 고치고 cached fixture까지 포함한 3,800행을 canonical 재검산 전 `HARD_CANDIDATE`로 차단했다. 기존 v2·KI20·checkpoint·blind payload는 변경하거나 읽지 않았다.
  - 검증: 최종 `build-94eb7b543490`, `intake-99c0b48231d6`, `preflight-aea1c001126e`에서 20K 최대 767/768, 초과·마지막 사용자 이전 mask·최종 EOS·serialization 오류 0, tool call 5,250/5,250 round-trip, 기간 grounding 오류 0, blind hash overlap 0을 확인했다.
  - 남은 이슈·후속 작업: exact target 반복, canonical 3,800행, 전체 state/grounding, 내부 4K와 expert 1.5K 검수, 실제 serving parser 검증이 남았다. `training_promotion_allowed=false`, `production_promotion_allowed=false`를 유지하며 2K/10K/20K 재학습은 금지한다. 상세는 [MIX20K-v3 검수 후보 인수·보정 계획](../mix20k_v3_repair_plan.md)을 따른다.
- 2026-08-30
  - 작업 요약: dashboard `v1.7.0`에 사용자가 명시적으로 선택한 무인증 원격 공유 모드를 추가하고, 기존 Cloudflare Quick Tunnel URL을 유지한 채 `https://scholars-greatest-biography-presidential.trycloudflare.com`을 로그인 없이 공개했다.
  - 변경 범위: 무인증 공유는 `--allow-unauthenticated-remote`와 wildcard·경로·port가 없는 exact HTTPS Origin을 함께 지정한 경우에만 시작한다. 기본 loopback과 기존 Basic 인증 방식은 계속 지원하고 Host·CSRF·POST Origin 검사를 유지한다. 사용자가 공개 위험을 확인한 뒤 요청한 범위에 따라 AI Hub 제한 샘플과 기존 private 세션이 포함된 전체 dashboard를 URL 접근자에게 열었으며, 학습 데이터·checkpoint·sealed blind·Phase 6·승격 상태는 변경하지 않았다.
  - 검증: dashboard 34건과 저장소 전체 267건 unittest, Ruff, JavaScript syntax, config JSON과 diff 검증을 통과했다. 실제 공개 URL에서 인증 헤더 없이 root `200`, KI20 생성 POST `200`, `status=generated`, 비어 있지 않은 답변을 확인했고 서비스 restart 횟수는 0이다.
  - 남은 이슈·후속 작업: 이 URL은 인증·사용자별 접근 제어·rate limit 없이 전체 dashboard와 GPU 추론을 공개하므로 링크를 아는 누구나 접근할 수 있다. Quick Tunnel은 uptime·고정 URL을 보장하지 않으며, 공유 종료 시 tunnel과 dashboard 서비스를 중지해야 한다. 이전 런타임 비밀번호 파일은 서비스 참조 해제 뒤 삭제했으며 `production_promotion_allowed=false`를 유지한다.
- 2026-08-30
  - 작업 요약: dashboard `v1.6.0`에 기본 비활성인 인증 원격 공유 계약을 추가하고 현재 Cloudflare Quick Tunnel의 정확한 HTTPS Origin에서 KI20 답변 생성을 연결했다.
  - 변경 범위: 원격 공유는 exact Origin·Basic 사용자·현재 사용자 소유 `0600` 비밀번호 파일이 모두 있어야 시작하며 정적 화면과 전체 API를 인증으로 보호한다. 기본 loopback 모드와 Host·CSRF·동일 Origin 검사는 유지했다. AI Hub 제한 샘플과 기존 private 세션을 포함한 전체 화면은 사용자가 접근 권한을 확인한 내부 팀원에게만 공유하며 비밀번호는 Git·문서·프로세스 인자에 기록하지 않는다. 학습 데이터·checkpoint·sealed blind·Phase 6·승격 상태는 변경하지 않았다.
  - 검증: dashboard 33건과 저장소 전체 266건 unittest, Ruff, JavaScript syntax, JSON·diff 검증을 통과했다. 실제 `trycloudflare.com` URL에서 무인증 local/external `401`, 인증 root·session API `200`, K0/KI20 `available=true`를 확인했고, 외부 Origin의 합성 KI20 질문은 `200`, `status=generated`, 비어 있지 않은 1 turn 답변을 반환했다.
  - 남은 이슈·후속 작업: Quick Tunnel은 uptime과 고정 URL을 보장하지 않는다. 공유 종료 시 tunnel과 인증 dashboard 서비스를 중지하고 런타임 비밀번호 파일을 제거해야 하며 `production_promotion_allowed=false`를 유지한다.
- 2026-08-30
  - 작업 요약: dashboard `v1.5.0`의 데이터 스플릿 탐색 화면을 분할 카드와 축 행 자체를 선택하는 단일 흐름으로 정리하고, 선택한 후보 풀에서 접힌 샘플 카드 10건을 한 번에 표시하도록 확장했다. `다른 10개 보기`는 클릭할 때마다 새 난수 표본을 요청한다.
  - 변경 범위: 각 요청은 OS 보안 난수로 후보 10건을 비복원 추출해 묶음 내부 중복을 금지하되, 요청 사이 재등장은 허용한다. `전체 혼합`은 축별 균등화 없이 실제 전체 후보 풀의 비율을 그대로 반영한다. 무작위 요청은 loopback·CSRF·Origin 보호 POST이며 표본을 cache·세션·파일에 저장하지 않는다. 학습 데이터 membership·모델·checkpoint·고정 20건·sealed blind는 수정하거나 열람하지 않았고 AI Hub 축의 로컬 제한 경고와 최소 투영을 유지했다.
  - 검증: dashboard 31건과 저장소 전체 264건 unittest, Ruff, JavaScript syntax, JSON·diff 검증을 통과했다. 실제 서비스에서 연속 두 표본이 각각 10건·묶음 내 고유 10건이며 재추첨 결과가 바뀌는 것을 확인했다. Windows Chrome에서 기본 전체 접힘, 상세 열기, 분할·축 전환, 10초 상태 갱신 중 표본 유지, desktop·390px mobile 가로 overflow 0을 확인했고 blind sample 요청은 404로 닫혔다.
  - 남은 이슈·후속 작업: 무작위 표본은 사람이 데이터 분포를 둘러보기 위한 로컬 진단 기능이며 평가 표본·품질 Gate·학습 재구성 근거가 아니다. 난수 특성상 연속 묶음 일부 또는 전부가 다시 나올 수 있다.
- 2026-08-30
  - 작업 요약: 수동 모델 검사와 고정 20건 진단 사이에 기본 접힘 상태의 실사용 사주 질문 20선(7개 분야·같은 세션 후속 4개)을 추가했다. 예시 선택은 합성 명식이 포함된 prompt 또는 자연어 후속 질문을 입력창에만 채우며 자동 생성을 하지 않는다.
  - 변경 범위: 두 공개 합성 명식은 승인 계산 정책과 고정 `lunar-python==1.4.8`로 재현하고, 2026년 시기 간지는 자문용·runtime 미승인·전문가 미검수로 명시했다. 실제 사용자·AI Hub 원문, 학습 20K, checkpoint, 고정 진단 결과, sealed blind와 Phase 6은 건드리지 않았다.
  - 검증: 20개·24 turn·4,000자 상한과 명식·십신·시기 간지를 자동 대조했고 dashboard 28건·전체 261건, Ruff, JavaScript·JSON·diff 검증을 통과했다. live Chrome에서 분야 필터, 입력 채우기, 생성 POST 0건, desktop/mobile overflow 0을 확인했다.
  - 남은 이슈·후속 작업: 예시와 모델 출력은 진단 전용이며 계산 engine 또는 품질 Gate가 아니다. 답변 개선용 재학습은 새 데이터·평가 계약 승인 뒤 별도로 결정한다.
- 2026-08-30
  - 작업 요약: dashboard `v1.4.0`에 실제 SFT 시작점인 고정 Kanana Instruct K0를 추가해 `KI20 단독`, `K0 단독`, `K0 ↔ KI20 동시 비교` 수동 세션을 지원한다. 동시 비교는 같은 질문을 두 모델의 독립 문맥에 순차 입력한다.
  - 변경 범위: 두 모델의 weight·tokenizer·chat template·custom code SHA-256을 로드 직전에 검증하고 16GiB GPU에서 하나씩 로드·해제한다. 기존 수동 세션은 KI20 단독으로 읽으며 고정 20건, 데이터, checkpoint, sealed blind와 재학습 상태는 바꾸지 않았다.
  - 검증: strict 고정 파일 경로의 실제 BF16 비교에서 같은 337-token 입력의 출력 `2/2`, peak allocated 각각 `2,677,079,040 bytes`, 전체 GPU 최대 `4,283MiB`, 종료 후 `1,242MiB` 복귀를 확인했다. 전체 259 tests, desktop 좌우·mobile 단일열과 live HTTP를 통과했다.
  - 남은 이슈·후속 작업: K0↔KI20 비교는 SFT 회귀 진단용이며 계산 engine이나 품질 Gate가 아니다. 상태형 동일 평가와 deterministic runtime bridge는 별도 후속 범위다.
- 2026-08-30
  - 작업 요약: KI20 수동 대화 실패를 context 손실이 아닌 무지시 행동 제어 문제로 진단하고 dashboard `v1.3.0`, 안내 보정 prompt, 공개 합성 상태형 dev 100건, 보강 후보 2,000건을 구현했다. 실제 재학습과 Phase 6은 수행하지 않았다.
  - 변경 범위: 최종 후보 `build-0f80acfeed13`은 기존 20K를 덮어쓰지 않은 `candidate_only` build이며 실제 사용자·AI Hub 원문과 생년월일↔명식 연결을 포함하지 않는다. dashboard의 raw profile·기존 세션·고정 20건은 비교 이력으로 보존하고 새 안내 profile도 운영 품질로 표시하지 않는다.
  - 검증: 상태형 Gate `stateful-gate-f5b76dde1921`은 reference/mutation 자체검증 각 `100/100` 뒤 KI20 실제 생성 100건을 완료했으나 필수 행동 `14%`, 무조작 `84%`, 재질문 18건, 허위 완료 1건, 미지원 날짜·기간 5건으로 `guided_diagnostic_not_met`였다. 후보는 10층×200건, 중복·PII·chart/DOB 혼합 0건과 dev100 근접중복 0건을 통과했다.
  - 남은 이슈·후속 작업: system prompt만으로는 production 준비가 되지 않는다. 2,000건 template 후보를 자연스러운 다회전 문장으로 다양화·소수 검수하고 새 데이터·split·평가 fingerprint를 승인한 뒤에만 별도 재학습을 검토한다. 앱 runtime의 deterministic slot state와 승인 계산 engine, 새 상태형 blind는 별도 구현 대상이며 기존 sealed blind는 계속 미열람이다.
- 2026-08-29
  - 작업 요약: clean commit `9ad00a283ce64ff222a54c41f743ae378ce12fe4`에서 KI20 1 epoch run `run-1f5d732cae67`을 독립 시작했다. goal 완료 기준인 첫 유한 optimizer step과 활성 process를 확인했다.
  - 변경 범위: model·data·과거 run은 수정하지 않고 Git 제외 `runs/KI20-MIX-v2/v1.2.0/run-1f5d732cae67`에 private 실행 상태를 생성했다. systemd user service가 학습을 계속 수행한다.
  - 검증: step 1 loss `2.9315`, grad norm `21.375`, gradient finite/nonzero, PID `826832` active를 확인했다. WSL2가 compute-app PID를 노출하지 않아 고정 runner PID·active service·초기 대비 GPU `8,781MiB` 증가를 교차 검증했으며 총 GPU 사용량은 `9,879/16,303MiB`였다.
  - 남은 이슈·후속 작업: 2,500 step 완료·milestone/final 저장·새 process reload·Phase 6 평가는 별도 후속 확인 대상이다. `production_promotion_allowed=false`와 blind 미열람을 유지한다.
- 2026-08-29
  - 작업 요약: 사용자의 별도 명시 확인을 받아 KI20 1 epoch Full FT 실행 계약 `v1.2.0`을 승인했다. 실제 시작 판정은 첫 optimizer step의 유한 loss·gradient와 활성 process 확인으로 분리했다.
  - 변경 범위: 기존 training v1.0·preflight v1.1·readiness v1.3은 불변으로 보존하고 새 config, 전용 runner, registry 실행 승인 포인터를 추가했다. 품질 목표 미달과 `production_promotion_allowed=false`는 유지한다.
  - 검증: 고정 모델·20K manifest·Gate v2·preflight·readiness hash chain, 1 epoch 2,500 step, `4×2`, eval 8, 16GiB 운영 상한을 실행 전 재검증하도록 고정했다.
  - 남은 이슈·후속 작업: 실행 계약을 커밋·푸시한 clean HEAD에서 백그라운드 학습을 시작하고 첫 정상 step 증거를 확인해야 한다. 이 항목 작성 시점에는 본학습을 시작하지 않았다.
- 2026-08-29
  - 작업 요약: 정본을 `3.3.0`으로 올려 Gate v2와 KI20 비학습 preflight를 반영했다. readiness `v1.3.0/build-7eb4c34364cc`과 현황 `v1.0.0/build-e23e3501a200`을 registry 최신 포인터로 고정했으며 KI20 본학습은 실행하지 않았다.
  - 변경 범위: 평가 `v1.2.0/build-e885b47cae74`, Gate `v2.0.0/gate-df26e962e145`, preflight `v1.1.0/preflight-b47fe12f03a4`을 새 경로에 추가했다. 과거 Gate v1, canonical 10K/20K, KI10 checkpoint, blind bytes를 덮어쓰지 않았다.
  - 검증: Gate v2 hard gate 10개 전부 통과, scorer reference/mutation 각 175건 전부 기대대로 판정했다. KI20 후보 중 train `4×2`·worker 0·eval 8을 선택했고 train peak `10,634MiB`, eval peak `11,802MiB`로 모두 16GiB 상한을 통과했다. 품질 목표는 8개 미달이라 배포 승격은 금지다.
  - 남은 이슈·후속 작업: `full_training_execution_enabled=false`, `production_promotion_allowed=false`, `blind_source_test_inspected=false`를 유지한다. 실제 KI20 1 epoch는 사용자의 새 명시 확인과 별도 실행 checkpoint 뒤에만 시작한다.
- 2026-08-29
  - 작업 요약: 현황 렌더러 checkpoint `618ce4d9870e7a64681823f0cde3a38f9934fad1`에서 KI10 Gate 실패 snapshot `v1.0.0/build-a4014017c26c`를 생성하고 registry 승인 포인터와 root HTML을 연결했다.
  - 변경 범위: 과거 pre-KI10 현황 build는 덮어쓰지 않았다. 새 현황은 KI10 Full FT·reload 통과와 1,000case 4개 Gate 미달, `ki20_promotion_allowed=false`, sealed blind 미열람을 집계로만 표시한다.
  - 검증: status build/HTML/manifest/config SHA-256은 `a401401…a5a8e4`/`4d81927…1f732e`/`f0f1d6a…a0d253`/`73e5b1a…ee914b`이며 root와 snapshot HTML은 byte-identical하다.
  - 남은 이슈·후속 작업: 현재 Phase 5는 `차단`으로 종료하고 KI20을 실행하지 않는다. 다음 보강은 지식·branch-policy·신살·handoff용 새 데이터 fingerprint와 평가/run version을 먼저 설계해야 한다.
- 2026-08-29
  - 작업 요약: KI10 자동 Gate 실패를 단일 파일 현황판에 정확히 표시하도록 current decision을 config화하고 `ki10_gate_failed / STOP` 렌더링 계약을 구현했다. 새 HTML은 구현 commit을 고정한 다음 불변 build로 발행한다.
  - 변경 범위: KI10 Full FT·품질 Gate hash chain, Phase 5 차단, 네 가지 미달 지표, KI20·sealed blind 금지를 status config에 추가했다. 기존 pre-KI10 hero·결정 표 하드코딩을 제거했다.
  - 검증: `project_status.py validate-contract`와 `plan`이 통과했고 예상 build는 `build-a4014017c26c`다. 테스트는 실패 상태 문구와 지표가 self-contained HTML에 포함됨을 검사한다.
  - 남은 이슈·후속 작업: 이 구현 checkpoint를 커밋한 뒤 snapshot·root HTML을 생성하고 registry 최신 포인터와 정본 경로를 새 build로 연결한다.
- 2026-08-29
  - 작업 요약: `KI10-MIX-v2/run-e6b712f0d45e`로 개발용 1,000case 자동 품질 Gate를 완료했으나 hard fact·branch policy, 신살, missing-chart handoff, target-only entity 4개 Gate가 실패했다. `ki20_promotion_allowed=false`로 고정하고 KI20을 실행하지 않았다.
  - 변경 범위: 생성 1,000건은 Git 제외 private run에만 저장하고 공개 경로에는 원문 없는 Gate 집계와 manifest만 추가했다. sealed blind·Phase 6·학습 데이터는 변경하거나 열람하지 않았다.
  - 검증: 통과 항목은 parseable 100%, special/control·severe safety 0, foreign sentence 1.4%, input fact violation·empathy confusion·persona causalization 0%다. 실패 항목은 hard fact·branch policy `38/100`, 신살 `17/25`, handoff `3/5`, 입력에 없는 날짜 `1건`이다. 고정 reference의 동일 채점 결과 `60/60`·`40/40`·`25/25` 통과로 평가기 자체의 exact-term 계약은 유효함을 재확인했다.
  - 남은 이슈·후속 작업: Phase 5 상태를 `차단`으로 전환하고 실패 현황판을 새 불변 build로 발행한다. 다음 학습은 현재 임계값을 낮추는 방식이 아니라 지식·정책 축 보강과 새 fingerprint/version 승인부터 시작해야 한다.
- 2026-08-29
  - 작업 요약: 고정 Instruct snapshot에서 `KI10-MIX-v2/run-e6b712f0d45e`를 1 epoch·1,250 optimizer step Full FT하고 최종 checkpoint의 새 프로세스 재로딩과 5/5 비공개 generation smoke를 통과했다. Phase 5 상태를 `진행 중`으로 전환했다.
  - 변경 범위: 모델·optimizer·생성문은 Git 제외 `runs/`에만 보존하고 공개 경로에는 원문 없는 집계 summary와 manifest만 추가했다. canonical 10K·평가 split·봉인 blind·KI20은 변경하거나 열람·실행하지 않았다.
  - 검증: train loss `0.7679830020904541`, final dev loss `0.9442684650421143`, 유한·nonzero gradient, peak VRAM `6,757,645,824` bytes, 공개 summary SHA-256 `1cbec50…e80a53`을 확인했다.
  - 남은 이슈·후속 작업: 개발용 1,000case 자동 품질 Gate를 실행한다. 하나라도 실패하면 KI20은 fail-closed로 금지하며, 전부 통과할 때만 고정 base에서 독립 KI20을 실행한다.
- 2026-08-29
  - 작업 요약: 새 readiness의 KI10 forward-only preflight `run-e6b712f0d45e`를 실행해 BF16 model load와 dev monitor 70건의 assistant-only loss를 검증했다. 실제 `.train()`·backward·optimizer step은 수행하지 않았다.
  - 변경 범위: 비공개 run summary만 생성했고 학습 데이터·모델·tokenizer·checkpoint·평가 split은 변경하지 않았다. TRL의 stop-token 일반 경고는 Kanana가 마지막 응답만 supervision하는 template라 발생함을 10K 전수 mask 집계로 분리 확인했다.
  - 검증: eval loss `3.658891439437866`, peak VRAM `3,675,810,816` bytes, 종료 free VRAM `12,867,076,096` bytes, 최종 assistant EOS mask `10,000/10,000`, 최대 길이 `609/768`, mask·EOS 누락 0건이다. preflight summary SHA-256은 `cdd5ca7…b6a79`다.
  - 남은 이슈·후속 작업: 현황판에 preflight 통과를 반영해 커밋한 뒤 KI10 1 epoch Full FT를 실행한다. KI20은 KI10 reload와 1,000case 자동 Gate가 모두 통과할 때까지 실행하지 않는다.
- 2026-08-29
  - 작업 요약: TRL preflight 호출 수정 commit `a6a1eefa91fcd7fa34f37ffbea386a9a731c9ea6`에서 readiness `v1.2.0/build-e325f16096dd`을 새 경로에 생성·standalone verify하고 현황판 `v1.0.0/build-a89d078aabc0`을 재발행했다.
  - 변경 범위: 이전 `build-bffd…`와 `build-f3ae…`는 불변 이력으로 보존했다. registry 최신 포인터와 문서 경로만 새 runner fingerprint에 연결했으며 학습 데이터·평가 membership·봉인 blind는 변경하거나 열람하지 않았다.
  - 검증: readiness private/public manifest는 `fd7d2b0…67553`/`b0356b2…3b0e3`, summary는 `6ca7fb4…3d814`, dev monitor는 기존과 같은 `aa61d2a…bcb31`이다. 현황 HTML은 snapshot과 byte-identical하고 SHA-256 `df379e4…69f5b`다.
  - 남은 이슈·후속 작업: registry·현황판 checkpoint를 커밋한 clean tree에서 KI10 forward-only preflight를 재실행한다. 유한 loss·BF16·VRAM Gate가 통과할 때만 실제 KI10 Full FT를 시작하며 KI20은 계속 금지한다.
- 2026-08-29
  - 작업 요약: 승인된 감사·평가 build와 Phase 5 runner 구현 hash를 묶은 readiness `v1.2.0/build-bffd53a2abb3`을 생성·standalone verify하고, 공개 현황판 `v1.0.0/build-f3ae22a8860e`을 고정했다. 실제 학습은 아직 수행하지 않았다.
  - 변경 범위: registry의 최신 평가·감사·readiness·현황 포인터를 갱신했다. readiness는 KI10만 허용하고 KI20은 1,000case 자동 품질 Gate 전까지 금지한다. `PROJECT_STATUS.html`은 버전 chain·20K token 구성·근거 등급·Gate·알려진 위험만 포함한다.
  - 검증: readiness private/public manifest SHA-256은 `02d5ecc…b551c`/`514bcc5…fe62`, summary는 `e6c89a4…d0ce`다. 현황 HTML은 snapshot과 byte-identical하며 SHA-256 `dc01a04…e4696`, 외부 실행 자산·restricted content 0으로 registry verify를 통과했다.
  - 남은 이슈·후속 작업: Phase 5 상태는 backward·optimizer step 전이므로 계속 `미시작`이다. 이 체크포인트를 커밋한 clean tree에서 KI10 forward-only preflight를 실행한 뒤에만 실제 KI10을 시작한다.
- 2026-08-29
  - 작업 요약: 구현 commit `1e76232c0c094e6d3c3ed47b253c60f0e1af63b1`에서 평가 확장 `v1.1.0/build-d2f9e1623e96`과 학습 전 감사 `v1.0.0/build-c38926f86a3d`를 생성·독립 재검증하고 registry 승인 포인터 후보를 고정했다. 실제 학습은 수행하지 않았다.
  - 변경 범위: 평가 v1.0 membership·bytes를 그대로 보존하면서 dev reference overlap 집계와 Nemotron 비인과 guard 50case만 추가했다. 20K 감사는 공개 집계만 생성하고 AI Hub 원문·개별 ID·봉인 blind를 읽거나 공개하지 않았다.
  - 검증: 평가 private/public manifest는 `96b7912…47203`/`f491e71…82738`, 감사 public manifest는 `a488ab1…71e82`다. hard blocker·critical/high·mask·foreign CJK·target-only entity·중대 단정·control·revision drift는 모두 0이며 KI10 전 데이터 수정은 불필요하다.
  - 실행 기록·후속 작업: `.venv-data` 시도는 부모 Phase 3 package freeze 불일치로 artifact 생성 전 실패했고, 고정 학습 `.venv`에서 재실행해 통과했다. 이 산출물과 registry를 커밋한 뒤 clean tree에서 readiness v1.2를 생성한다. KI20은 계속 금지다.
- 2026-08-29
  - 작업 요약: 학습 직전 의미·출처 전수 감사, 평가 v1.0 불변 확장, readiness v1.2, 실제 BF16 Full FT runner와 공개 현황판 계약을 구현했다. 이 체크포인트에서는 모델 학습·backward·optimizer step과 봉인 blind 열람을 수행하지 않았다.
  - 변경 범위: canonical 계획을 `3.2.0`으로 올리고 20K assistant token 편중·정형 중복·개발 reference overlap·Nemotron 페르소나 연결 문구를 집계했다. KI10은 1,250 optimizer step, KI20은 KI10의 고정 1,000case 자동 Gate를 모두 통과할 때만 base snapshot에서 독립 2,500 step으로 실행하도록 fail-closed 계약을 추가했다.
  - 검증: 고정 학습 환경에서 20K 전수 재검증 결과 hard blocker·critical/high·assistant mask 오류·foreign CJK·target-only entity·중대 단정·control·revision drift가 모두 0이었다. 175개 단위 테스트, Ruff, Python compile, `git diff --check`, Phase 4 계약 검증을 통과했다.
  - 남은 이슈·후속 작업: Nemotron+bazi assistant token 비중 83.299298%, YEJI 정규화 중복 참여 95.5%, Nemotron 연결 표현 76.029412%를 알려진 위험으로 유지한다. 구현 commit을 고정한 뒤 평가·감사·readiness 불변 build와 현황 HTML을 생성하고, clean tree에서 KI10 forward preflight와 조건부 학습을 실행한다.
- 2026-08-29
  - 작업 요약: Phase 5 전 평가 역할을 train·dev monitor·dev diagnostic·sealed blind·external conformance로 분리한 `evaluation-split/v1.0.0/build-a5a04ab96594`와 비학습 `phase5-readiness/v1.1.0/build-201010b37e40`을 생성·독립 재검증했다. 실제 KI10·KI20 학습은 실행하지 않았다.
  - 변경 범위: 기존 eval70과 1,000건을 개발용으로 재분류하고, 품질 보정 24K의 미사용 component에서 축별 50개·총 350 component/500행 blind를 봉인했다. KASI 200행과 고정 revision 정책 경계 20행, 라이선스 고지를 별도 공개 fixture로 고정하고 readiness v1.1 부모 계약에 정확한 manifest hash를 연결했다.
  - 검증: reserve는 축별 최소 YEJI 61 component이며 50개 선택 후 11개가 남는다. train·개발·blind의 component/record/content hash 누수 0, BaZi 4행 component 보존, 고정 tokenizer 768 이하, Kanana 고정 revision·torch 2.13.0+cu130·RTX 5070 Ti·BF16·64GiB disk Gate를 통과했다. readiness private/public manifest는 `4d28b744…db907`/`1205f83a…d1321`이다.
  - 남은 이슈·후속 작업: Phase 5는 실제 학습을 시작하지 않아 계속 `미시작`이다. 다음 실행은 사용자가 별도로 승인할 때 KI10을 고정 Instruct에서 시작하는 것이며 `phase5_training_performed=false`를 유지한다.
- 2026-08-29
  - 작업 요약: Phase 4 v2 canonical을 부모로 실제 학습 없는 Phase 5 readiness `build-f6c8171f454f`을 완료했다. KI10·KI20 학습은 시작하지 않아 Phase 5 상태는 `미시작`이다.
  - 변경 범위: KI10 10K·KI20 20K, 축별 10건 eval70, 고정 Kanana·CUDA 13.0·BF16·package lock, 독립 Run과 checkpoint state 계약을 고정했다. AI Hub 원문이 포함된 eval70은 Git 제외 private 경로에만 저장했다.
  - 검증: readiness 생성·독립 verify, 7축 manifest strict subset과 eval/train 누수 0, 단일 RTX 5070 Ti와 754,540,773,376 bytes 가용 disk를 확인했다. public/private manifest SHA-256은 `9b71d2d3…8a176`/`6f72abe1…8c273`이다.
  - 남은 이슈·후속 작업: 실제 Phase 5 실행은 별도 승인 대상이며 `phase5_training_performed=false`다. KI10과 KI20은 같은 Instruct snapshot에서 각각 독립 시작한다.
- 2026-08-29
  - 작업 요약: 품질 보정 Phase 4 v2의 A~E를 완료하고 768 canonical `v2.0.0/build-6f32d52c2868`을 registry의 `approved_derived`로 승격했다. Phase 5 실제 학습은 실행하지 않았다.
  - 변경 범위: 7축 MIX1K/10K/20K와 1,000항목·1,020case K0, 자동 위험 분류, BF16 Full FT forward/backward·8-bit optimizer, 100→200-step resume와 새 process 재로드를 검증했다. 이전 v1 canonical은 이력으로 보존한다.
  - 검증: Phase 4 `verify-final`과 private/public manifest hash chain이 통과했다. canonical build SHA-256은 `6f32d52c…32ee9`, private/public manifest는 `66b21f08…bedbf`/`c670c2ad…f7fec`이며 안전 위반 0, 선택 길이 768이다.
  - 남은 이슈·후속 작업: 자동 위험도 high 97건은 사람이 판독해야 하는 승인 Gate로 넘기지 않고 상위 50건만 로컬 우선순위로 고정했다. 전문 품질 인증은 주장하지 않으며 다음 작업은 비학습 Phase 5 readiness다.
- 2026-08-29
  - 작업 요약: 정본을 v3.0으로 올리고 품질 보정 7축 24K를 부모로 하는 Phase 4 `v2.0.0` 계약을 구현했다. Core Eval 300·source holdout 700·K0 1,020case와 7축 MIX1K/10K/20K, deterministic QA·앱 브리지를 새 fingerprint에서 검증한다.
  - 변경 범위: 과거 v1 canonical을 덮어쓰지 않고 `v2.0.0` 경로를 사용하며, AI Hub+브리지 assistant loss token share 10% 최소 Gate와 전역 leakage component 분리를 추가했다. 실제 Phase 5 학습은 수행하지 않았다.
  - 검증: Ruff, Python compile, 전체 단위 테스트 139건, 품질 보정 부모 hash chain·Phase 3 보고서·Phase 4 v2 계약·dry-run을 통과했다.
  - 남은 이슈·후속 작업: 구현 checkpoint 이후 새 build의 A~E와 canonical 승격, Phase 5 실행 전 계약 검증이 남아 있다. 그전까지 Phase 4는 `진행 중`이다.
- 2026-08-28
  - 작업 요약: 구현 checkpoint `31fe13b08e04d4015d30ac670d92dd6427e6427d`에서 교정 staging 기반 Phase 4A~E를 완료하고 768 canonical `v1.1.0/build-a1a34616dd72`를 `approved_derived`로 승인했다. Phase 5 실제 10K·20K 학습은 실행하지 않았다.
  - 변경 범위: 부모 preflight `build-7d59833b8d59`에서 24K 전수 token/loss-mask, Core Eval 200·source holdout 500, K0 700항목/720case와 자동 위험 분류를 재검증했다. BF16 Full FT 512 1/20-step, 1024 1-step 진단, 768 100→200-step 별도 process resume와 checkpoint 재로드 5-task 생성을 수행했다.
  - 검증: A~E, canonical hash chain과 `verify-final`이 통과했다. K0 안전 위반 0, 위험도 high 48·medium 329·low 323, 200-step 손실 중앙값 2.3489→0.9452, peak VRAM 10,498,061,312 bytes, 종료 여유 3,005,186,048 bytes였다.
  - 남은 이슈·후속 작업: 사람 전문 판독과 품질 인증은 수행하지 않았으며 `quality_certification_claimed=false`다. Transformers의 checkpoint tokenizer 정규식 경고는 원본·저장본 `tokenizer.json` SHA-256 `1c4be9ec…f2b5ab` 일치와 표본 token ID 일치로 비-Mistral 오탐임을 확인했으므로 일반 Mistral 교정값을 적용하지 않는다. 다음 작업은 별도 승격된 Phase 5다.
- 2026-08-28
  - 작업 요약: 정본을 v2.6으로 올리고 교정 staging 기반 Phase 4 `v1.1.0` 재실행 계약과 K0 자동 위험 분류·Full FT smoke/resume·canonical 승격 실행기를 구현했다.
  - 변경 범위: K0는 고정 모델·template·prompt hash가 같은 과거 출력만 재사용하고 지표를 재계산한다. D/E는 512 단일/20-step, 1024 진단, 768 100-step 저장·새 process 200-step resume·5-task reload로 고정했다.
  - 검증: Ruff, compile, 단위 테스트, 계약·dry-run, 과거 v1.0 산출물 hash chain 재검증을 통과했다. v1.0의 11개 구현 hash 중 2개가 최종 커밋에서 같은 바이트로 도달 불가한 추적성 한계도 별도 고정했다.
  - 남은 이슈·후속 작업: 이 기록은 실행 전 구현 체크포인트다. A~C/K0·triage와 GPU D/E를 실행해 결과를 추가하기 전까지 Phase 4는 `진행 중`, `training_promotion_allowed=false`다.

- 2026-08-27
  - 작업 요약: 원본 0~17절을 인덱스와 7개 Phase로 정본화하고, 모델·데이터 revision, Kanana/AI Hub 라이선스, PyTorch cu128, TRL assistant loss, bitsandbytes GPU 지원을 공식 웹 자료로 재확인했다.
  - 변경 범위: 이 계획 디렉터리의 Markdown 문서만 추가했다. 코드·데이터·환경·모델은 변경하지 않았다.
  - 검증: 원본 SHA-256 `11dde66505aa3ca90834488a877a0f4db42512d9cb377880d935f71bc71d3724` 일치, Phase 7개와 내부 링크 8개 검사 통과, 정본 8개 `git diff --cached --check` 통과, 공식 PyTorch 참조 URL HTTP 200을 확인했다. 보관 원본의 기존 줄 끝 공백 4곳은 byte-for-byte 보존을 위해 유지했다.
  - 남은 이슈·후속 작업: 구현은 시작하지 않았다. Phase 0의 라이선스·실험 계약 승인 후 Phase 1부터 순서대로 실행한다.
- 2026-08-27
  - 작업 요약: 주 모델을 Kanana 2 1.3B Instruct로 교체하고, 모델·데이터의 상업 이용조건과 원천 계보·품질 감사를 바탕으로 광고형 자체 서비스 후보용 데이터 혼합을 다시 정본화했다.
  - 변경 범위: README와 Phase 0~6 문서만 수정했다. 코드·데이터·환경·모델·archive·미추적 HTML은 변경하지 않았다.
  - 검증: 고정 모델·데이터 revision API 조회, chat template SHA-256 `b8ee6b…e3e3`, 신살 JSON 29,754 bytes·SHA-256 `9a11e1…939e` 일치, 혼합 합계 1,000/10,000/20,000, Phase 문서 7개, 내부 Markdown 링크, 구 모델·Run·manifest 참조 0건, 공식 외부 URL 25개 HTTP 200, `git diff --check` 통과를 확인했다.
  - 남은 이슈·후속 작업: `bazi-sft`, AI Hub 2종, YEJI 신살 규칙은 조건부 Gate를 통과해야 하며 하나라도 실패하면 혼합비를 재배분하지 않고 Phase를 차단한다.
- 2026-08-27
  - 작업 요약: KETI 정책이 적용되는 AI Hub #271을 활성 원천에서 제외하고 같은 #86의 단일턴·멀티턴 파생 축으로 계약을 v2.1로 교정했다. Phase 0 계약을 승인 산출물로 고정하고 Phase 1 수집기·보안 검증·집계 inventory를 구현했다.
  - 변경 범위: 계약·라이선스·원천 설정, `uv` 데이터 환경 요구사항, 안전한 HF/AI Hub 수집 CLI, archive 방어와 단위 테스트, 집계 보고서를 추가했다. Nemotron 두 shard는 해시 확인 후 Git 제외 raw 경로로 이동했고 `bazi-sft`와 YEJI 허용 파일을 고정 revision에서 수집했다. Phase 2 전처리와 모델 다운로드·학습은 시작하지 않았다.
  - 검증: 단위 테스트 11건, Python compile, 혼합 합계 1,000/10,000/20,000, #271 활성화 거부, AI Hub 키 파일 권한·비노출·외부 redirect 전달 차단, tar/zip path traversal·link 거부, 공개 원천 manifest 재해시를 통과했다. inventory에서 Nemotron 116,666행·UUID/행 중복 0건, `bazi-sft` 100,000행·행 중복 0건·25,000 synthetic group, YEJI 51개 규칙과 고정 SHA-256을 확인했다.
  - 남은 이슈·후속 작업: AI Hub #86은 사용자의 데이터 이용 신청·승인과 실제 파일 수집이 남아 Phase 1을 `차단`으로 유지한다. 승인 후 네 file key를 수집해 멀티턴 적격 group 1,200개 이상 여부를 확인하며, 실패 시 혼합비를 재배분하지 않는다.
- 2026-08-27
  - 작업 요약: 사용자의 AI Hub #86 이용 승인을 확인하고 고정 file key 4개를 공식 API v0.6 계약으로 수집했다. tar·zip 안전 검사, 원본·part·병합본 manifest, 라벨 JSON 구조와 split 간 group 감사를 완료해 Phase 1을 `완료`로 판정했다.
  - 변경 범위: Git 제외 raw 경로에 AI Hub 원본을 추가하고, 공개 가능한 다운로드 해시 명세·집계 inventory·승인 메타데이터·Phase 2 누수 방지 계약만 갱신했다. 원문 샘플은 보고서와 Git에 포함하지 않았다.
  - 검증: file key `66046`~`66049`의 archive 21,350,912 bytes와 SHA-256, manifest 12파일 재해시, JSON 58,268건 파싱 실패 0건, 멀티턴 고유 group 51,886개를 확인했다. 최소 1,200-group Gate와 활성 원천 4개 전체 `verify`를 통과했다.
  - 남은 이슈·후속 작업: upstream train/validation의 group ID 교집합 6,379개와 exact record 교집합 0개를 확인했다. Phase 2에서는 upstream split을 출처 메타로만 쓰고 전체 `talk.id.talk-id`를 group-first로 다시 분리해야 한다.
- 2026-08-27
  - 작업 요약: Phase 2A 읽기 전용 원천 감사기와 데이터 SemVer/build fingerprint 경로를 구현하고, 네 원천 전체 스캔 및 사용자 필수 150건·참고 151건의 비공개 검토 큐를 생성했다.
  - 변경 범위: source 보고서를 `source/v1.0.0/build-b3890c552e38`로 이관하고, 감사 정책·bundle·registry, 감사 CLI·테스트, 공개 집계 보고서를 추가했다. 원본·unified·평가셋·학습 데이터는 변경하거나 생성하지 않았다.
  - 검증: 단위 테스트 20건, Python compile, Phase 1 원본 재해시, audit `verify`, 같은 build 무쓰기 재실행, 미검토 finalize·무확인 approve 차단을 통과했다. `build-336b8377063a`에서 Nemotron 116,666행, `bazi-sft` 100,000행, AI Hub 58,268건, YEJI 51규칙과 교차 원천 동일 명식 2,778개를 확인했다.
  - 남은 이슈·후속 작업: 필수 150건 사람 검토가 남았다. YEJI `词馆`의 `壬申` 주석·`壬卯` 코드 충돌과 `五鬼`의 미등록 `흉살류` category 때문에 Gate는 차단 상태이며, 교정 계약과 새 build 없이는 승인·Phase 2B 전처리를 시작할 수 없다.
- 2026-08-27
  - 작업 요약: YEJI 두 known issue를 원본 불변 correction overlay로 고정한 감사 `v1.1.0/build-e162d9b2b7dc`를 생성하고, 핵심 150건과 참조 151건을 함께 확인하는 버전 고정 HTML 검수기를 구현했다.
  - 변경 범위: 감사 정책·교정 manifest·append-only 판정 revision·과거 v1.0 검증기, 공개 감사 보고서와 `reports/.../audit-review/.../reviewer-v1.0.0` 화면을 추가했다. 원본·unified·평가셋·학습 데이터는 변경하거나 생성하지 않았다.
  - 검증: Ruff, Python compile, 단위 테스트 20건, Node JavaScript 구문 검사, Phase 1 원본 재해시, v1.1 audit `verify`, v1.0 historical verify, 실제 loopback HTTP/API와 Chromium 1600×1000 렌더링을 통과했다. v1.1은 관측 코드 2건을 모두 해소해 blocking finding 0건이며 큐 review ID 301개는 v1.0과 동일하다.
  - 남은 이슈·후속 작업: 사용자 판정은 필수 0/150, 참조 0/151이고 build는 미봉인·미승인이다. 필수 판정을 모두 완료·해결한 뒤 별도 지시에 따라 seal과 명시 승인을 수행하며, 그 전에는 Phase 2B를 시작하지 않는다.
- 2026-08-27
  - 작업 요약: 동일 AI Hub 승인 범위 팀원용으로 핵심 150단위만 담은 오프라인 HTML 검수 ZIP과 advisory 피드백 검증 흐름을 추가했다.
  - 변경 범위: 원천별 최소 투영·식별자 제거, checkpoint/final JSON·CSV, ZIP manifest·SHA-256·권한 검증을 구현하고 저장소 밖에 현재 build 공유본을 생성했다. 참조 151단위와 본 판정 ledger는 공유하거나 변경하지 않았다.
  - 검증: 신규 테스트 7건과 전체 38건, 변경 파일 Ruff·compile·Node 검사, 실제 audit/raw verify, ZIP 150단위·180레코드 및 sidecar 검증, Chrome 오프라인 렌더링과 대화 턴 순서를 통과했다.
  - 남은 이슈·후속 작업: 팀원 의견은 본 판정에 자동 합치지 않는다. 원 담당자 검토가 여전히 필수 0/150이므로 Phase 2A는 미봉인·미승인이고 Phase 2B는 시작하지 않는다.
- 2026-08-27
  - 작업 요약: Nemotron 100만 행 전체 원천과 감사 v1.2를 고정하고, 사용자 지시에 따른 감사 300건 일괄 위험 수용·seal·승인을 완료했다. 첫 MIX20K에 필요한 최종 20K + 예비 20%만 정제한 24K staging `v0.1.0/build-109815ee6879`을 생성했다.
  - 변경 범위: BaZi·YEJI 한국어 고정 문구 은행, 다섯 축 adapter, 전체 규칙·안전·언어 검증, 결정론적 후보 순서, 버전별 공개 집계와 승인 후 읽기 전용인 BaZi/YEJI 300건 loopback 검수 화면을 추가했다. Git 제외 원본은 수정하지 않았다.
  - 검증: 감사 원본 재해시와 approval verify, staging 24,000행·고유 ID 24,000·고유 message 24,000, 영문 단어 잔여 0, AI Hub 축 간 group 겹침 0, BaZi 규칙 불일치 0, YEJI 51규칙 coverage와 테스트를 통과했다.
  - 남은 이슈·후속 작업: 검수 승인은 항목별 전문 판독이 아닌 `owner_blanket_risk_acceptance`이며 품질 인증을 주장하지 않는다. Phase 4에서 tokenizer 길이, assistant loss mask, holdout/core eval, 정확한 MIX20K manifest와 모델 preflight를 완료하기 전에는 학습 승격하지 않는다.
- 2026-08-28
  - 작업 요약: Phase 0~2의 계약·원천·감사·24K staging을 전체 재검수하고, 과거 승인 build를 당시 Git 구현과 해시로 재검증하는 경로를 복구했다. Phase 2는 24K staging 인계 경계에서 `완료`로 판정했다.
  - 변경 범위: archive 중복·별칭 경로, link/device, 기존 추출본·병합 ZIP 변조, 원천 symlink·미등록 파일, 비공식 URL·redirect·무해시 다운로드를 fail-closed로 보강했다. 승인 audit의 registry 상태·seal·approval 일치와 승인 staging의 실행 commit·승인·decision·manifest 해시를 강제하고, 현재 라이선스 재검토·과거 표현 정오표를 추가했다.
  - 검증: 현재 원천 4종 전체 manifest 재해시, 과거 audit v1.0·v1.1·승인 v1.2, 승인 staging 24,000행·검수 300건, loopback HTML read-only bootstrap, 전체 회귀 테스트 68건과 정적·보안 검사를 통과했다. 상세 결과는 `data/reports/saju_1b_baseline/phase-verification/v1.0.0/review-20260828/verification_report.json`에 고정한다.
  - 남은 이슈·후속 작업: 스테이징에 서로 다른 축의 동일 명식 `leakage_group_id` 36개가 있으므로 Phase 4 split에서 반드시 같은 쪽에 배치한다. holdout/core eval, 중첩 MIX manifest, tokenizer·assistant mask·모델 preflight와 `training_promotion_allowed=true` 판정은 Phase 4 전용 Gate로 남긴다.
- 2026-08-28
  - 작업 요약: 최신 공식 문서와 RTX 5070 Ti 실장비를 기준으로 Phase 3 환경을 PyTorch 2.13.0·CUDA 13.0 wheel로 고정하고, Kanana 2 1.3B Instruct의 고정 revision·tokenizer·chat template·전체 BF16 모델 load를 완료했다.
  - 변경 범위: Linux·Windows Codex와 Claude 전역 Markdown의 오래된 PyTorch 기본값을 같은 내용으로 갱신했다. 저장소에는 모델·환경 계약, 75개 전이 의존성 hash lock, 안전한 준비·검증 CLI와 테스트, `build-32e2c84af3d3` 공개 보고서를 추가했다. `.venv`와 모델 파일은 Git에서 제외했다.
  - 검증: `torch.version.cuda=13.0`, native `sm_120`, BF16 matmul, `libbitsandbytes_cuda130.so` Adam8bit state, 모델 14파일·2,593,309,962 bytes 전체 hash, 1,291,478,272개 BF16 CUDA parameter, load key 오류 0, 보고서 재검증과 전체 회귀 검사를 통과했다.
  - 남은 이슈·후속 작업: upstream YaRN factor 40과 암시적 비율 8 경고는 원본 불변 상태로 보고서에 남겼다. Phase 4 최종 데이터셋·assistant loss mask·200-step smoke와 실제 학습은 수행하지 않았고 `training_promotion_allowed=false`를 유지한다.
- 2026-08-28
  - 작업 요약: Phase 4A~C 비학습 preflight `v1.0.0/build-a6813ba3b778`을 완료해 24K schema/token/loss mask, Core Eval 200·source holdout 500, 중첩 MIX candidate와 원본 모델 K0 720case를 고정했다.
  - 변경 범위: 재현 가능한 preflight CLI·테스트·공개 집계 보고서와 저장소 밖 제한 데이터 오프라인 검수 ZIP을 추가했다. 전역 Codex·Claude 규칙에는 native Triton용 Python header 검증과 정식 실행의 JIT 우회 금지를 동기화했다. 실제 학습·optimizer·backward·checkpoint·canonical manifest 승격은 하지 않았다.
  - 검증: Gate A/B/C, BF16 SDPA native-JIT probe, K0 빈 출력·제어문자·special-token·missing-chart 안전 위반 0건, 결정성 재생, 700항목/720case ZIP checksum과 Windows Chrome 오프라인 렌더링을 통과했다.
  - 남은 이슈·후속 작업: K0 371/720case가 512-token 상한에 도달했고 일부 hard 자동 계약 점수가 낮다. 사람 전문 검수와 Phase 4D/E 200-step 학습 smoke가 남았으므로 Phase 4는 `부분 진행`, `approved_derived=null`, `training_promotion_allowed=false`이며 Phase 5를 시작하지 않는다.
- 2026-08-28
  - 작업 요약: 생성기 검수 6건을 원천·기존 staging과 대조하고, 유효 달력 명식을 사용하는 새 24K staging `v0.2.0/build-847088ee804d`를 승인된 Phase 4 입력으로 고정했다.
  - 변경 범위: 기존 `v0.1.0`은 불변 보존했다. YEJI 달력 backend·역법 관계 Gate, 필터 겹침 카운터, AI Hub projection provenance, Nemotron 나이 검증, 새 공개 보고서와 registry hash chain을 추가했다.
  - 검증: 24,000행 전수 schema·중복·수량 검사, Nemotron/BaZi/YEJI 역법 관계 20,400/20,400, AI Hub 원천 58,268건의 turn 수·정책 사유, YEJI 고유 명식 1,200건을 통과했다. 기존 YEJI는 1,151/1,200건이 역법 불일치이고 단순 천간 교정 시 evaluator 56건이 바뀜을 별도 확인했다.
  - 남은 이슈·후속 작업: 승인 방식은 자동 검증 기반 사용자 위험 수용이며 전문 항목 검수·품질 인증은 아니다. 새 staging 부모로 Phase 4A~E를 재실행하기 전까지 `training_promotion_allowed=false`를 유지한다.
