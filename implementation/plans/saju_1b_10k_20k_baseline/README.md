# 한국형 사주 1.3B 10K·20K Baseline 정본

이 문서는 `kakaocorp/kanana-2-1.3b-instruct`에 상업 활용 후보로 선별한 사주·한국어 대화 데이터를 Full Fine-tuning하는 초기 실험의 정본 인덱스다. 실제 학습에는 각 라이선스·원천·품질 Gate를 모두 통과한 행만 들어간다. 세부 실행은 반드시 Phase 순서와 Gate를 따르며, archive 원본과 `kanana_saju_dataset_guide.html`은 참고 자료일 뿐 실행 기준이 아니다.

| 항목 | 값 |
|---|---|
| 문서 버전 | `2.5.0` |
| 정본화 기준일 | 2026-08-28 |
| 주 장비 | NVIDIA GeForce RTX 5070 Ti 16GiB, WSL2 |
| 주 모델 | `kakaocorp/kanana-2-1.3b-instruct@bf4786aa2a1908adce942d53976270132732f720` |
| 실험 범위 | 1K smoke, 독립 10K·20K Full FT, 사후 평가와 v2 Lite 결정 |
| 배포 성격 | 광고형 자체 서비스 후보. 원문·checkpoint 공개와 원격 모델 접근 판매는 별도 Gate |

## 고정 실험 계약

- 모델 학습 방식은 BF16 전체 파라미터 Full Fine-tuning이다. LoRA/QLoRA로 자동 전환하지 않는다.
- 10K와 20K는 같은 Instruct revision에서 각각 독립적으로 1 epoch 학습한다.
- `MIX1K-v1 ⊂ MIX10-v1 ⊂ MIX20-v1`이어야 한다.
- 데이터 행 비율은 Nemotron 55%, 검산·한국어화한 `bazi-sft` 25%, AI Hub #86 단일턴 10%, 같은 #86의 대화 그룹에서 파생한 멀티턴 5%, 검증된 YEJI 신살 규칙 파생본 5%로 고정한다.
- Nemotron 내부는 v6 20%·v7 80%로 고정한다.
- 평가셋과 group holdout은 학습 manifest보다 먼저 고정한다.
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
| 2 | [데이터 전처리](phase-2-data-preprocessing.md) | 완료 | 승인 audit 부모의 MIX20K용 24K staging 완결, 학습 승격은 Phase 4 Gate로 분리 |
| 3 | [학습 모델·환경 준비](phase-3-model-preparation.md) | 완료 | 고정 PyTorch cu130 환경에서 모델·tokenizer·template 전체 BF16 오프라인 로드 성공 |
| 4 | [학습 전 데이터·모델 검증](phase-4-preflight-validation.md) | 부분 진행 | A~C 비학습 preflight 통과, D~E 학습 smoke 미실행 |
| 5 | [Baseline 학습](phase-5-baseline-training.md) | 미시작 | KI10·KI20 독립 Run과 산출물 완결 |
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
├── staging/saju_1b_baseline/v0.2.0/build-847088ee804d/ # Git 제외·현재 24K 후보
├── derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/
│   ├── unified/
│   ├── manifests/
│   └── eval/
└── reports/
    └── saju_1b_baseline/
        ├── audit/v1.2.0/build-ca756f3eb89f/
        ├── audit-review/v1.2.0/build-ca756f3eb89f/reviewer-v1.1.0/
        ├── preprocessing-staging/v0.2.0/build-847088ee804d/
        ├── model-preparation/v1.0.0/build-32e2c84af3d3/
        ├── preflight/v1.0.0/build-a6813ba3b778/
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
└── registry.json

configs/model_versions/saju_1b_baseline/model-preparation-v1.0.0.json
configs/chat_templates/kanana2_sft.jinja
requirements.txt
requirements-phase3.lock.txt

runs/
├── K0-INSTRUCT/
├── KI1K-SMOKE-v1/
├── KI10-MIX-v1/
├── KI20-MIX-v1/
└── KI20-MIX-v2-LITE/  # Phase 6 결정 시에만 생성
```

## 모델·데이터 조사 결론

### 모델

`kakaocorp/kanana-2-1.3b-instruct`의 고정 revision은 `bf4786aa2a1908adce942d53976270132732f720`이다. 공식 문서명은 `KANANA OPEN LICENSE AGREEMENT`이다. 고정 revision의 API license tag는 `other`, 2026-08-28 현재 모델 카드 tag는 `kanana-open-license`로 조회되므로 tag 대신 고정 revision의 LICENSE 원문을 판정 기준으로 삼는다. Section 4.2에 따라 Section 4.1의 별도 허가 대상이 아닌 자체 서비스 개발·운영은 별도 상업 라이선스 없이 가능하다. API·클라우드 원격 접근 판매, SI/on-premise 판매, on-device 판매는 Section 4.1의 별도 상업 라이선스 대상이다. 서비스·배포 시 라이선스 사본, 수정 표시, Notice, `Powered by Kanana` 표시를 제공하고, Kanana로 학습·향상한 모델을 배포할 때는 이름에 `Kanana` prefix를 적용한다. 과거 보고서의 `Kanana Open License 4.2`는 버전명이 아닌 조항을 버전처럼 적은 표현이므로 [현재 라이선스 재검토](../../../configs/data_versions/saju_1b_baseline/license-review-v1.0.0.json)가 대체한다. 과거 파일은 build hash 보존을 위해 수정하지 않았다. 이 결론은 법률 자문을 대체하지 않으며 서비스 형태가 바뀌면 다시 검토한다.

### 활성 학습 소스

| 소스 | 고정 revision/버전 | 라이선스·정책 | 10K | 20K | 사용 결정 |
|---|---|---|---:|---:|---|
| Nemotron Saju | `ffb934248746a2dea64ef771c0d86e1743d25702` | CC BY 4.0 | 5,500 | 11,000 | 출처·변경 표시 후 허용 |
| `AmareshHebbar/bazi-sft` | `fad87063b317612e4164dfb0e0e08572c3831df4` | Apache 2.0 | 2,500 | 5,000 | 구조 검산·한국어 재렌더 후 허용 |
| AI Hub 감성대화 #86 단일턴 축 | 승인 배포본 | AI Hub 일반정책 | 1,000 | 2,000 | 승인·비공개·출처 표시 조건부 허용 |
| AI Hub 감성대화 #86 멀티턴 파생 축 | 위와 동일 | 위와 동일 | 500 | 1,000 | 동일 대화 그룹의 연속 발화만 사용, 단일턴 축과 group 분리 |
| YEJI 신살 규칙 파생본 | `84583ca54e8fce257d3d5efd015bca1263a1cfe9` | MIT + 원천 MIT | 500 | 1,000 | 단일 파일 선별·교정·자체 QA 생성 후 허용 |

Nemotron 1K 몫은 v6 110행·v7 440행, 10K 몫은 v6 1,100행·v7 4,400행, 20K 몫은 v6 2,200행·v7 8,800행이다. `bazi-sft`는 제공된 네 기둥을 입력 사실로 삼아 일간·오행·규칙 조건을 다시 계산하며, 원문의 날짜·지역에서 네 기둥을 학습시키지 않는다. AI Hub 데이터는 원문을 외부에 공개하지 않고 개인정보·자해·의료 진단·과도한 훈계 행을 제외한다.

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
