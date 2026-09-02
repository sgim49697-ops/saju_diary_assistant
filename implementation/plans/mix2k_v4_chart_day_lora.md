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
- 당시 검증: Codex ChatGPT subscription structured-output smoke는 통과했다. 당시 Claude CLI auth가 만료되어 초안 0건에서 fail-closed로 종료했고, 단일 teacher 결과를 Gold로 승격하지 않았다.
- 후속 상태: Claude subscription 복구 후 아래 v1.0.1 양방향 pilot에서 해소했다. full 2,000행은 별도 장시간 실행으로 남아 있다.

### 2026-09-02 - token audit·LoRA 실행 Gate

- 작업 요약: 교차 검수가 완료된 candidate 2,000행을 pinned K0 tokenizer·chat template로 재렌더하고 rendered/prompt/supervised token, assistant mask, 마지막 EOS, user·system loss leakage, truncation을 전수 검사하는 finalizer를 구현했다.
- 길이 판정: 입력·출력 각 4,096 token을 넘으면 즉시 차단한다. 2,048을 넘는 행이 20건 또는 1%보다 많으면 `max_length`를 자동 상향하지 않고 full-runtime·projection 검토 상태로 학습을 차단한다.
- LoRA 계약: K0에서만 r=8/16/32, `all-linear`, rsLoRA, dropout 0.05, bias 없음, LR 5e-5, 1 epoch, assistant-only loss를 고정했다. 공통 `lora_alpha=32`로 rank 이외 scaling 인자를 고정했다.
- package: 기존 PyTorch 2.13.0+cu130 환경을 변경하지 않고 `uv pip --no-deps`로 PEFT 0.20.0 overlay만 추가했다. 새 overlay lock은 base training lock과 분리했다.
- 검증: CPU에서 K0에 `all-linear` r=16을 실제 적용해 target linear 224개, trainable 18,677,760개, base trainable 0개를 확인했다. r=8과 r=32의 고정 기대값은 각각 9,338,880개와 37,355,520개다. 저장한 safetensors와 다시 로드한 PEFT state를 tensor 단위로 대조한다. K0 runtime·LoRA config 검증이 통과했고, single-turn·follow-up 샘플의 assistant mask leakage 0과 supervised EOS를 확인했다.
- 실행 순서: 완성 data manifest 통과 후 각 rank의 longest 8행 1-step forward/backward/optimizer·adapter reload preflight를 순차 실행한다. 그 다음에만 각 rank 250 optimizer step을 순차 실행하며 K0 base hash를 전·후 대조한다.

### 2026-09-02 - v1.0.1 교정 계약·평가 관문 고정

- 작업 요약: 실제 Dashboard v1.11 full runtime 형식을 유지한 v1.0.1 spec을 다시 생성하고, teacher·LoRA·5-arm 평가가 같은 immutable build를 참조하도록 ID와 SHA를 연쇄 고정했다. 기존 Dashboard v1.11 prompt와 runtime 파일은 변경하지 않았으며 `bound_chart_v2`는 별도 production 승격 전제인 candidate로만 유지한다.
- immutable 산출물: private 경로의 `build-59d68bc841a0`, build SHA `59d68bc841a02e366711045383ebea0f37be138244e0e213fe7eb15bfa109826`. training spec SHA는 `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, frozen dev SHA는 `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`이다. 이전 build는 최신 source hash와 달라 의도대로 거부된다.
- 분포 확인: training 2,000행은 300/300/450/300/250/250/100/50의 고정 축을 정확히 만족하고 Claude→Codex 1,000행, Codex→Claude 1,000행으로 배정됐다. dev 200건은 40/30/50/40/20/20이며 `actual-chart-day-label-confusion-20260902`를 release blocker로 포함한다. uncertainty 축은 runtime 연결 50건과 미연결 50건이다.
- token·projection 결과: pinned Kanana tokenizer와 현재 chat template 기준 full runtime prompt 최대 1,774, p99 1,768, 2,048 초과 0건이다. audit-only projection은 평균 458.298 token을 줄이지만 학습에는 사용하지 않았고, training은 raw full snapshot을 유지한다.
- validator 보강: 원국 전체/단일 기둥, 연주·월주·일주·시주, 연간지·월간지·일진, 천간·지지·오행·음양·십신·지장간의 명시 구조 claim을 위치별로 검사한다. 교정문·조사·wrapper·병렬 표현·날짜 기간 표현과 일반 한국어 오탐 회귀를 테스트로 고정했다. 제공되지 않은 통근·합충·신강약 등은 계속 차단한다.
- LoRA 실행 안전성: final training row의 ID·axis·prompt·runtime snapshot SHA를 frozen spec과 직접 대조한다. 손상된 token audit 타입은 예외 대신 fail-closed로 거부하며, preflight/training 재사용 시 manifest 전체 계약과 실제 adapter hash·config·rank·reload·adapter-only 상태를 다시 확인한다.
- 평가 계약: K0, LoRA r=8/16/32, KI20의 5개 arm과 10개 지표를 동일 runtime·prompt·generation 설정으로 고정했다. LoRA config SHA는 `cb156569841002495b2e6d87107cd30e6e7766342eee3a185fadc1d31805f9a1`, 평가 config SHA는 `20eabbc1e492d8b8c57553be586103c0c0f66a2b347b0e8a8cb85651b12598d4`이다.
- 양방향 pilot: 새 build에서 Claude 초안→Codex grounding 검수와 Codex 초안→Claude 자연성 검수를 각각 1건 실행해 2/2 accepted를 확인했다. candidate SHA는 `074bd0d3faad743b0abdf67af09e2951e5432bb521a05299c2e96025efd79dc9`이며 두 답변 모두 deterministic PASS·peer PASS·최소 3줄/3문장을 만족했다. API key·dev target·sealed data 접근과 training 실행은 모두 false다.
- 검증: `uvx ruff check ...`, 관련 unittest 85건 실행(환경상 DE440s private fixture 2건 skip), `git diff --check`, 새 spec의 artifact SHA 재계산, teacher spec current-pass/stale-reject, LoRA `validate-contract`, evaluation `validate-contract`를 통과했다. Orca read-only red-team과 Claude Code의 좁은 no-tool 검토 결과를 반영한 뒤 추가 P0/P1이 없음을 재확인했다.
- 남은 작업: 실제 2,000행 dual-teacher 생성·교차검수, assistant target까지 포함한 전수 token/mask/EOS/leakage audit, rank별 GPU preflight·1 epoch 학습, 5-arm 추론·이중 품질 검수는 아직 실행하지 않았다. `bound_chart_v2`의 versioned serving 통합과 regression 통과 전에는 production 승격을 허용하지 않는다.

### 2026-09-02 - full 교차 teacher 첫 checkpoint

- 작업 요약: 불변 spec `build-59d68bc841a0`에서 full 2,000행 실행을 시작하고 20행 shard의 Claude 초안→Codex 검수와 Codex 초안을 실제 subscription CLI로 처리했다.
- checkpoint: private target `full-build-59d68bc841a0-97b7404b-117d55cb`에 provider call 5회가 원자 저장됐다. 현재 accepted 16행, Claude 검수 대기 20행, 초안 대기 1,964행, permanent failed 0행이다. Claude 초안에서 걸린 구조 사실 누락·period label 혼동·금지 추론은 Gold에 들어가지 않고 재작성 상태로 남았다.
- 중단 원인: Codex 초안 20행은 deterministic validator를 20/20 통과했으나 다음 Claude 검수에서 Pro session limit에 도달해 CLI가 exit 1로 종료됐다. 인증은 유효하며 2026-09-03 02:00 KST reset을 안내했다. 실패한 provider call은 state에 반영되지 않아 같은 명령으로 재개할 수 있다.
- 보안·범위: API key, AI Hub, 개인정보, sealed blind, dev target과 training은 접근하거나 실행하지 않았다. raw teacher 출력과 private state는 Git에 넣지 않는다.
- 남은 작업: Claude 사용량 복구 후 동일 target을 재개해 2,000행 전수를 양방향 교차검수해야 한다. 단일 teacher 결과를 Gold로 강등하지 않는다.

### 2026-09-03 - full schema-literacy prompt 교정

- 재개 결과: Claude 검수 대기 20행은 20/20 PASS해 누적 accepted 36행이 됐다. 뒤이은 Claude 재작성에서 기존 2회 실패 9행이 구조 필드 누락을 반복해 permanent failed가 되었고, runner는 provider call 7회에서 자동 중단됐다. 해당 target은 candidate manifest를 만들지 않은 비후보 이력으로 보존한다.
- 원인: 실패 9행은 schema-literacy의 전 기둥 오행·음양, 전 기둥 stem/branch ten-god, 원국 전체·일주 구분 질문에 집중됐다. teacher가 긴 RAW·ALLOWED 목록에서 질문이 요구한 위치별 값을 일부 생략하거나 일주 `stem_ten_god=일간` literal을 다른 표현으로 바꾼 실제 출력 오류이며 validator 완화 사유가 아니다.
- 교정: immutable spec과 validator는 변경하지 않고 draft·review prompt에 질문별 `[MANDATORY ANSWER CHECKLIST]`를 추가했다. 원국 네 기둥, 일간, period 세 label, 위치별 천간·지지·오행·음양·십신·지장간·표면 오행을 해당 질문에 맞춰 literal 값으로 제시하고 질문 밖 기간·관계·대운을 덧붙이지 않게 했다.
- 검증: Ruff, `git diff --check`, checklist 회귀를 포함한 `tests.test_mix2k_v4` 44건을 통과했다. 새 runner hash의 full target에서 초기 schema-literacy batch를 다시 검증한 뒤 전체 생성을 재개한다.
