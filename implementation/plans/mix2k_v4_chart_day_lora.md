<!-- mix2k_v4_chart_day_lora.md - K0 기반 full-runtime 2K correction dataset과 LoRA 비교 실험 계획 -->

# K0 기반 2K correction dataset·LoRA 실험

## 목적

새 학습은 사주 지식을 임의로 늘리는 작업이 아니다. Dashboard v1.11이 전달하는 원국 네 기둥·일간·위치별 세부 필드·단일 날짜의 연간지/월간지/일진을 정확히 구분하고, 필요한 사실만 자연스러운 한국어로 풀어내는 행동을 K0에 교정한다.

- 출발 모델: `kakaocorp/kanana-2-1.3b-instruct` revision `bf4786aa2a1908adce942d53976270132732f720`
- 비교군: K0 무수정, LoRA r=8, LoRA r=16(주 실험), LoRA r=32, 기존 KI20
- 학습 형식: Dashboard v1.11과 동일한 v1.5 full runtime snapshot
- 금지: KI20 이어학습, Full FT, partial freeze, training만의 compact JSON, 제공되지 않은 relation·신강약·격국·용신·대운·사건 예측 생성

## 고정 데이터 계약

Training 2,000행은 schema literacy 300, 원국 자연어 설명 300, 원국+단일 일진 450, 후속 질문 300, intake/tool/state/correction 250, 일반 한국어·공감 250, 불확실성·blocked 100, HARD fact 짧은 QA 50으로 고정한다. Training target 생성 전에 별도 production-like dev 200건을 동결하고 teacher에게 target을 공개하지 않는다.

Teacher 절반은 Claude 초안→Codex grounding 검수, 나머지 절반은 Codex 초안→Claude 자연성·상담 품질 검수로 생성한다. 반대 teacher의 `PASS`와 deterministic validator `PASS`를 모두 받은 행만 학습 후보가 된다. 생성 prompt는 `[RAW RUNTIME FACTS]`, `[ALLOWED EVIDENCE]`, `[FORBIDDEN INFERENCE]`, `[TASK]`를 분리한다.

실질 답변은 최소 3개의 완결 문장과 3개의 의미 있는 줄을 요구하되, 선호 최대 길이를 두지 않는다. 입력·출력의 안전 상한은 각각 4,096 token이며, 학습 `max_length`는 전수 token audit 후 `[2048, 3584, 4096, 8192]` 중 truncation 없이 수용하는 최소값을 선택한다.

## 필수 release blocker

원국 `戊辰·甲子·乙丑·壬午`, 일간 `乙木`, 2026-09-02의 연간지 `丙午`, 월간지 `丙申`, 일진 `己卯`를 각각 올바른 label로 구분해야 한다. `乙丑`을 원국 전체로 부르거나 `丙午`를 일진으로 부르는 행동, `己卯` 누락, 제공되지 않은 통근·뿌리·relation 판단, 의미 없는 입력 재진술은 하나라도 발생하면 release를 차단한다. 후속 질문에서도 같은 evidence를 유지해야 한다.

## LoRA·평가 계약

- 공통: `target_modules="all-linear"`, `use_rslora=true`, `bias="none"`, `lora_dropout=0.05`, LR `5e-5`, 1 epoch, `assistant_only_loss=true`
- rank: r=8, r=16, r=32의 서로 독립된 adapter
- 학습 전 전수 audit: rendered token, supervised assistant token, truncation, assistant mask, EOS, user/system loss leakage
- 평가: schema field accuracy, natal/period label confusion, unsupported fact rate, provided fact omission, 자연스러운 설명 선호, follow-up evidence consistency, 일반 대화 보존, 반복·template 비율, false Saju injection, re-ask rate

## 진행 기록

### 2026-09-02 - full-runtime spec·dev 고정

- 작업 요약: K0 snapshot을 해시로 고정하고, v1.5 계산 엔진의 공개 synthetic 원국·단일 일진으로 dev 200건과 teacher spec 2,000건을 생성했다. AI Hub 원문·개인정보·sealed blind는 접근하지 않았다.
- 변경 범위: `mix2k-v4-chart-day-8k-v1.0.0.json`, `mix2k_v4_contracts.py`, `mix2k_v4_build.py`, 표적 회귀 테스트.
- 산출물: Git 제외 private 경로의 `build-3518debb78c5`; dev 200행과 training spec 2,000행. training runtime snapshot 600건과 날짜 300건을 확인했다. build identity에 generator·validator·Dashboard context·prompt·runtime release·ephemeris·K0 파일 해시를 포함했다.
- token 결과: full runtime prompt 최대 1,706, p99 1,700, 2,048 초과 0건. audit-only provenance projection의 평균 절감은 458.298 token이지만 training 형식으로는 사용하지 않았다.
- 검증: `uvx ruff check ...` 통과, `python -m unittest tests.test_mix2k_v4 -v` 7건 통과, 실제 builder 완료, 외부 반출 금지 key·marker scan 통과.
- 남은 작업: subscription teacher pilot→전체 생성·교차 검수→완성 target token audit→LoRA 3개 rank 학습→5-arm dev 평가·release blocker 판정.

### 2026-09-02 - subscription teacher 실행기

- 작업 요약: 20행 shard, 초안→deterministic validator→반대 teacher review→최대 2회 재작성, 중복 답변 재작성, 파일 lock·원자적 state·resume를 구현했다. teacher에는 생성된 공개 synthetic runtime fact만 전달하며 dev target 경로는 읽지 않는다.
- 보안: 자식 프로세스에서 provider API key·cloud credential 환경을 제거하고 Claude는 safe mode·tool 없음, Codex는 ephemeral·read-only·rule 없음으로 실행한다. raw provider envelope와 식별정보는 저장하지 않는다.
- 검증: Codex ChatGPT subscription structured-output smoke는 통과했다. Claude CLI는 auth 상태가 만료되어 초안 0건에서 fail-closed로 종료했고, 단일 teacher 결과를 Gold로 승격하지 않았다.
- 후속: `claude` 재로그인 후 같은 pilot state를 재실행하고 4건의 초안·교차 검수를 모두 통과시킨 뒤 full 2,000행을 시작한다.

### 2026-09-02 - token audit·LoRA 실행 Gate

- 작업 요약: 교차 검수가 완료된 candidate 2,000행을 pinned K0 tokenizer·chat template로 재렌더하고 rendered/prompt/supervised token, assistant mask, 마지막 EOS, user·system loss leakage, truncation을 전수 검사하는 finalizer를 구현했다.
- 길이 판정: 입력·출력 각 4,096 token을 넘으면 즉시 차단한다. 2,048을 넘는 행이 20건 또는 1%보다 많으면 `max_length`를 자동 상향하지 않고 full-runtime·projection 검토 상태로 학습을 차단한다.
- LoRA 계약: K0에서만 r=8/16/32, `all-linear`, rsLoRA, dropout 0.05, bias 없음, LR 5e-5, 1 epoch, assistant-only loss를 고정했다. 공통 `lora_alpha=32`로 rank 이외 scaling 인자를 고정했다.
- package: 기존 PyTorch 2.13.0+cu130 환경을 변경하지 않고 `uv pip --no-deps`로 PEFT 0.20.0 overlay만 추가했다. 새 overlay lock은 base training lock과 분리했다.
- 검증: CPU에서 K0에 `all-linear` r=16을 실제 적용해 target linear 224개, trainable 18,677,760개, base trainable 0개를 확인했다. r=8과 r=32의 고정 기대값은 각각 9,338,880개와 37,355,520개다. 저장한 safetensors와 다시 로드한 PEFT state를 tensor 단위로 대조한다. K0 runtime·LoRA config 검증이 통과했고, single-turn·follow-up 샘플의 assistant mask leakage 0과 supervised EOS를 확인했다.
- 실행 순서: 완성 data manifest 통과 후 각 rank의 longest 8행 1-step forward/backward/optimizer·adapter reload preflight를 순차 실행한다. 그 다음에만 각 rank 250 optimizer step을 순차 실행하며 K0 base hash를 전·후 대조한다.
