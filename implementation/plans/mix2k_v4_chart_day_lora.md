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

Teacher 절반은 Claude 초안→Codex grounding 판정, 나머지 절반은 Codex 초안→Claude 자연성·상담 품질 판정으로 생성한다. 반대 teacher의 `PASS`와 deterministic validator `PASS`를 모두 받은 행만 학습 후보가 된다. 생성 prompt는 `[RAW RUNTIME FACTS]`, `[ALLOWED EVIDENCE]`, `[FORBIDDEN INFERENCE]`, `[TASK]`를 분리한다.

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
- 남은 작업: subscription teacher pilot→전체 생성·교차 판정→완성 target token audit→LoRA 3개 rank 학습→5-arm dev 평가·release blocker 판정.

### 2026-09-02 - subscription teacher 실행기

- 작업 요약: 20행 shard, 초안→deterministic validator→반대 teacher review→최대 2회 재작성, 중복 답변 재작성, 파일 lock·원자적 state·resume를 구현했다. teacher에는 생성된 공개 synthetic runtime fact만 전달하며 dev target 경로는 읽지 않는다.
- 보안: 자식 프로세스에서 provider API key·cloud credential 환경을 제거하고 Claude는 safe mode·tool 없음, Codex는 ephemeral·read-only·rule 없음으로 실행한다. raw provider envelope와 식별정보는 저장하지 않는다.
- 당시 검증: Codex ChatGPT subscription structured-output smoke는 통과했다. 당시 Claude CLI auth가 만료되어 초안 0건에서 fail-closed로 종료했고, 단일 teacher 결과를 Gold로 승격하지 않았다.
- 후속 상태: Claude subscription 복구 후 아래 v1.0.1 양방향 pilot에서 해소했다. full 2,000행은 별도 장시간 실행으로 남아 있다.

### 2026-09-02 - token audit·LoRA 실행 Gate

- 작업 요약: 교차 판정이 완료된 candidate 2,000행을 pinned K0 tokenizer·chat template로 재렌더하고 rendered/prompt/supervised token, assistant mask, 마지막 EOS, user·system loss leakage, truncation을 전수 검사하는 finalizer를 구현했다.
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
- 양방향 pilot: 새 build에서 Claude 초안→Codex grounding 판정과 Codex 초안→Claude 자연성 판정을 각각 1건 실행해 2/2 accepted를 확인했다. candidate SHA는 `074bd0d3faad743b0abdf67af09e2951e5432bb521a05299c2e96025efd79dc9`이며 두 답변 모두 deterministic PASS·peer PASS·최소 3줄/3문장을 만족했다. API key·dev target·sealed data 접근과 training 실행은 모두 false다.
- 검증: `uvx ruff check ...`, 관련 unittest 85건 실행(환경상 DE440s private fixture 2건 skip), `git diff --check`, 새 spec의 artifact SHA 재계산, teacher spec current-pass/stale-reject, LoRA `validate-contract`, evaluation `validate-contract`를 통과했다. Orca read-only red-team과 Claude Code의 좁은 no-tool 검토 결과를 반영한 뒤 추가 P0/P1이 없음을 재확인했다.
- 남은 작업: 실제 2,000행 dual-teacher 생성·교차 판정, assistant target까지 포함한 전수 token/mask/EOS/leakage audit, rank별 GPU preflight·1 epoch 학습, 5-arm 추론·이중 품질 판정은 아직 실행하지 않았다. `bound_chart_v2`의 versioned serving 통합과 regression 통과 전에는 production 승격을 허용하지 않는다.

### 2026-09-02 - full 교차 teacher 첫 checkpoint

- 작업 요약: 불변 spec `build-59d68bc841a0`에서 full 2,000행 실행을 시작하고 20행 shard의 Claude 초안→Codex 판정과 Codex 초안을 실제 subscription CLI로 처리했다.
- checkpoint: private target `full-build-59d68bc841a0-97b7404b-117d55cb`에 provider call 5회가 원자 저장됐다. 현재 accepted 16행, Claude 판정 대기 20행, 초안 대기 1,964행, permanent failed 0행이다. Claude 초안에서 걸린 구조 사실 누락·period label 혼동·금지 추론은 Gold에 들어가지 않고 재작성 상태로 남았다.
- 중단 원인: Codex 초안 20행은 deterministic validator를 20/20 통과했으나 다음 Claude 판정에서 Pro session limit에 도달해 CLI가 exit 1로 종료됐다. 인증은 유효하며 2026-09-03 02:00 KST reset을 안내했다. 실패한 provider call은 state에 반영되지 않아 같은 명령으로 재개할 수 있다.
- 보안·범위: API key, AI Hub, 개인정보, sealed blind, dev target과 training은 접근하거나 실행하지 않았다. raw teacher 출력과 private state는 Git에 넣지 않는다.
- 남은 작업: Claude 사용량 복구 후 동일 target을 재개해 2,000행 전수를 양방향 교차 판정해야 한다. 단일 teacher 결과를 Gold로 강등하지 않는다.

### 2026-09-03 - full schema-literacy prompt 교정

- 재개 결과: Claude 판정 대기 20행은 20/20 PASS해 누적 accepted 36행이 됐다. 뒤이은 Claude 재작성에서 기존 2회 실패 9행이 구조 필드 누락을 반복해 permanent failed가 되었고, runner는 provider call 7회에서 자동 중단됐다. 해당 target은 candidate manifest를 만들지 않은 비후보 이력으로 보존한다.
- 원인: 실패 9행은 schema-literacy의 전 기둥 오행·음양, 전 기둥 stem/branch ten-god, 원국 전체·일주 구분 질문에 집중됐다. teacher가 긴 RAW·ALLOWED 목록에서 질문이 요구한 위치별 값을 일부 생략하거나 일주 `stem_ten_god=일간` literal을 다른 표현으로 바꾼 실제 출력 오류이며 validator 완화 사유가 아니다.
- 교정: immutable spec과 validator는 변경하지 않고 draft·review prompt에 질문별 `[MANDATORY ANSWER CHECKLIST]`를 추가했다. 원국 네 기둥, 일간, period 세 label, 위치별 천간·지지·오행·음양·십신·지장간·표면 오행을 해당 질문에 맞춰 literal 값으로 제시하고 질문 밖 기간·관계·대운을 덧붙이지 않게 했다.
- 검증: Ruff, `git diff --check`, checklist 회귀를 포함한 `tests.test_mix2k_v4` 44건을 통과했다. 새 runner hash의 full target에서 초기 schema-literacy batch를 다시 검증한 뒤 전체 생성을 재개한다.

### 2026-09-03 - teacher 답변 줄바꿈 정규화

- 재검증 결과: checklist 적용 runner의 첫 schema-literacy 20건은 각 답변을 5~6개 완결 문장으로 작성하면서도 한 줄에 직렬화해 앞단에서 모두 최소 3줄 계약에 걸렸다. layout 정규화 후 재검사에서는 12건이 즉시 통과했고, 8건은 올바른 `천간 庚(오행 금, 음양 양)` 형식과 일주 천간 십신 literal `일간` 표현을 parser가 놓친 사실 누락 오탐으로 확인됐다.
- 교정: validator의 사실·문장·최소 줄 기준은 완화하지 않았다. 최소 문장 수를 이미 충족하면서 최소 줄 수만 부족한 답변에 한해 문장 사이 수평 공백을 실제 줄바꿈으로 바꾸며, 숫자로 끝나는 날짜 표기(`2026. 9. 2.` 등)는 분리하지 않는 layout-only 정규화를 teacher runner에 추가했다. 문장이 부족하거나 이미 여러 줄인 답변, 1줄 계약 행은 손대지 않는다. code fence·inline code·Markdown link·URL·목록이 있는 답변과 구두점·약어만으로 줄 수를 만드는 답변도 자동 보정하지 않고 재작성 경로에 남긴다. 구조 parser에는 괄호 안의 명시적 `오행 …, 음양 …` 쌍과 `천간 자리가 … '일간'으로 표기`된 십신 literal만 좁게 추가하고, 잘못된 위치 값은 계속 차단한다. private state에는 provider 원본 draft와 정규화 버전을 남기고 최종 teacher manifest에는 정규화 행 수를 집계한다.
- 실행 이력: 기존 `full-build-59d68bc841a0-64253f1d-117d55cb` checkpoint는 첫 provider call만 담은 비후보 불변 이력으로 보존한다. 새 runner hash target에서 같은 초기 20건을 다시 통과시킨 뒤 전체 교차 생성을 재개한다.
- 계약 재동결: validator source hash가 spec identity에 포함되므로 개선된 계약을 private `build-de0fdf57f45a`(build SHA `de0fdf57f45aa2e4f4705f8c94f9136dd8c05891bce58eedd31787b29cdec1c2`)로 새로 동결했다. dev SHA `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`, training spec SHA `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, projection SHA `c889ccd27fc161144a0e3a8739020aa697c27c7d1615b5547e1c7488acd99458`는 이전 build와 동일해 데이터·분할·prompt 내용은 바뀌지 않았다. 중간 build와 과거 teacher checkpoint는 삭제하지 않고 비후보 이력으로 보존한다.
- downstream pin: LoRA config SHA는 `06245aa665e268c712bf94c82b8ad22b2e58cddf57c86e6d76216a765933bb85`, 5-arm 평가 config SHA는 `9f58037b12797fcb3ec835c0863cf798a8081179f50b658a3f833902c0d33159`로 갱신했다.
- 검증: historical Claude 초안 20건을 새 layout/parser 계약으로 재생해 20/20 deterministic PASS를 확인했다. `tests.test_mix2k_v4` 45건과 관련 3개 모듈 68건, Ruff, `git diff --check`, JSON parse, teacher spec identity, LoRA `validate-contract`, 5-arm evaluation `validate-contract`를 통과했고 Orca read-only red-team에서 추가 P0/P1이 없었다. 모호한 숫자 종결·생략부호는 자동 분리하지 않고 teacher rewrite로 넘긴다.

### 2026-09-03 - full teacher 구조 claim 계약 최종 재동결

- checkpoint 분석: `build-de0fdf57f45a` 기반 private target은 provider call 6회, accepted 45행, permanent failed 0행까지 진행됐다. 초안 이력이 있는 56행을 보강 validator로 재생해 56/56 PASS를 확인했다. 이 target과 과거 build는 삭제하지 않지만 validator source identity가 바뀌었으므로 candidate로 재사용하지 않는다.
- validator 보강: 자연스러운 `천간/지지 + 오행/음양` 연속 표기, 부정 뒤 최종 교정값, 일주 천간의 `일간 자리` literal, period 연간지와 일진이 우연히 같은 값인 경우를 구조 claim으로 정확히 처리한다. sibling field의 부정이 이웃 field로 번지는 오탐, role literal 뒤의 부정 우회, `천간 A와 지지 B` ordered pair 오인, `같은 순서로` 후속 field carry 누락을 대칭 회귀로 차단했다.
- immutable 산출물: validator SHA `155bac7ec9b1b32a62414eefb734bb0e79ebf19760ddbb287eec9444f3816011`을 포함한 private `build-d6982e11bbbd`, build SHA `d6982e11bbbd0bc3d13d2101cd14c563d5d99eb3e513c2624208c46277c745f5`로 재동결했다. dev SHA `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`, training spec SHA `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, projection SHA `c889ccd27fc161144a0e3a8739020aa697c27c7d1615b5547e1c7488acd99458`는 동일해 데이터·분할·prompt는 바뀌지 않았다.
- downstream pin: LoRA config SHA는 `19039bbb734a399d5db96ee41189da7fbf36c04cfd89266587e25ef58bd1964e`, 5-arm 평가 config SHA는 `d9915af1d1c8995d6b240135b2b474271bb37fe275e7e00f7677eb01b77b5b3b`로 갱신했다.
- 검증: 관련 3개 unittest 모듈 69건, Ruff, `git diff --check`, 기존 최신 초안 56행 재생, teacher spec identity, LoRA `validate-contract`, 5-arm evaluation `validate-contract`가 모두 통과했다. 병렬 read-only red-team의 P0는 없었고 보고된 P1은 모두 회귀 테스트와 함께 닫았다.
- 남은 작업: 새 immutable target에서 full 2,000행 Claude↔Codex 교차 teacher 생성·판정을 재개한다. 양쪽 teacher와 deterministic validator를 모두 통과하기 전에는 candidate finalization·학습으로 진행하지 않는다.

### 2026-09-03 - 첫 live batch 피드백 반영

- 실행 결과: `build-d6982e11bbbd` 기반 새 target에서 Claude 초안 20행과 Codex 교차 판정을 각 1회 실행했다. 초기 deterministic PASS는 6행이었고 Codex는 질문 밖 제한사항 나열을 제거하도록 6행 모두 재작성으로 돌렸다. provider call 2회의 target은 삭제하지 않고 비후보 진단 이력으로 보존한다.
- 원인 분리: 정답인 “일주는 원국 네 기둥 중 하나” 문장을 단일 원국 주장으로 오인한 경우, `일간 자체라/그 자체이므로 '일간'으로 표기`한 문형을 놓친 경우, `포함하지 않습니다/포함되어 있지 않습니다`라는 명시적 부정을 unsupported 주장으로 오인한 경우를 확인했다. 보강 계약으로 같은 20개 초안을 재생한 결과 19/20이 PASS했고, 남은 1개는 answer에서 사용하지 않은 값을 `used_fact_values`에 넣은 실제 provenance 오류였다.
- prompt 교정: FORBIDDEN 목록은 답변에 반복할 문구가 아니라 생성 금지 기준임을 명시하고, 한계 질문이 아니면 요청 항목만 답하도록 했다. `limitations`와 `used_fact_*`는 audit metadata임을 양쪽 teacher에 명시해, 정확한 metadata가 비어 있지 않다는 이유만으로 답변 범위 이탈 판정을 내리지 않게 했다.
- immutable 산출물: 계약 SHA `3caa6bfe77df0bd95b31e4335ee247ff90be5e523e515b5893a5e80286c043df`를 포함한 private `build-4187ef6753d8`, build SHA `4187ef6753d895db91b9001fb44b8c188b4358a6a378ec6f649c84899f7c46db`로 재동결했다. dev·training spec·projection SHA는 앞선 build와 동일하다.
- downstream pin: LoRA config SHA는 `a21066cafa713fe6fc0949be783db1c9b31540242dd8a148504545ab71edad05`, 5-arm 평가 config SHA는 `97e58da46597d90ec3bf23a16baf2916dbb89648e13031791404b3a0718edf6d`다.
- 검증: 관련 3개 unittest 모듈 69건, Ruff, `git diff --check`, teacher spec identity, LoRA `validate-contract`, 5-arm evaluation `validate-contract`가 모두 통과했다. 새 immutable target의 첫 양방향 batch를 확인한 뒤 full 생성을 계속한다.

### 2026-09-03 - live batch 계약 red-team 종료

- 추가 차단: unsupported 사실을 먼저 단정한 뒤 쉼표 뒤의 다른 부정문으로 숨기는 우회를 막기 위해 부정 면제를 해당 용어부터 다음 쉼표 전까지로 제한했다. `乙丑은 원국 전체다/예요/이야` 같은 서술·구어 종결형도 단일 기둥→원국 전체 오칭으로 차단했다.
- immutable 산출물: 최종 계약 SHA `28844d3ec8563c2aff657f35498d5c26aa3b191164b439bb505c9cb40edde6f5`를 포함한 private `build-f0152c6533f4`, build SHA `f0152c6533f463d70f478230a6e242dc97af88cc7bc253e5ef536c4787d75d60`로 재동결했다. dev SHA `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`, training spec SHA `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, projection SHA `c889ccd27fc161144a0e3a8739020aa697c27c7d1615b5547e1c7488acd99458`는 그대로다.
- downstream pin: LoRA config SHA는 `09267a1bbb6ee6f67ee1d94e005b1c190b48ce9b5d8835c905641a00a477517e`, 5-arm 평가 config SHA는 `e81566047d137ecbe9f137dc88f631fb2f98f08046dbadb507c3bb2f6a7c4162`다.
- 재생 결과: 첫 live batch의 기존 20개 Claude 초안은 19/20 deterministic PASS이며, 유일한 탈락은 쓰지 않은 값을 provenance에 추가한 실제 오류다. 병렬 자동 red-team의 최종 P0는 없고 보고된 P1 두 건은 회귀 3종과 직접 재현으로 닫았다.
- provider 가용성: 최종 target 시작 시 Claude Pro가 session limit에 도달해 09:00 KST 재설정을 반환했다. `--provider-only` 실행 경로를 추가해 사용 가능한 provider의 담당 draft/review만 처리하고, 반대 provider pending은 그대로 보존한다. 이 경로는 단일 teacher 결과를 candidate로 강등하지 않으며 최종 peer PASS 조건도 바꾸지 않는다. Claude 복구 전에는 Codex 담당 초안을 먼저 누적한다.

### 2026-09-03 - Codex 선행 batch와 표면 오행 계수 교정

- 실행 결과: `build-f0152c6533f4` 기반 provider별 target에서 Codex 초안 40행을 2회 호출로 생성했다. 첫 판정에서는 38행이 다음 teacher 대기 상태였고, 2행은 답변의 `금도 2개`를 표면 오행 계수로 읽지 못해 deterministic DROP됐다. 이 target은 삭제하지 않고 비후보 이력으로 보존한다.
- 계약 교정: `목은 2개이고 ... 금도 0개`처럼 병렬 조사 `도`를 사용한 구조적 계수만 좁게 인식하도록 parser와 대칭 회귀를 보강했다. 수정 계약으로 40개 초안을 전수 재생해 40/40 deterministic PASS를 확인했으며, 잘못된 `금도 1개`는 계속 confusion으로 차단한다.
- immutable 산출물: 계약 SHA `b64933b3a2d54c270df043a0650c7df8f7f8c1aa83e26747a03ed962fcc70558`를 포함한 private `build-d9468dfae98e`, build SHA `d9468dfae98e3700b4f67764ec7b5eb5a7c69e701e97f3a762dc80677803c61d`로 새로 동결했다. dev SHA `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`, training spec SHA `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, projection SHA `c889ccd27fc161144a0e3a8739020aa697c27c7d1615b5547e1c7488acd99458`는 동일하다.
- downstream pin: LoRA config SHA는 `68345cfc248ad00b2a150c34b6437c1d58ad51ee5e3379938ef610133aee6573`, 5-arm 평가 config SHA는 `1783d6acd87048f83413287cc2797c62fd1dc4e2cc916a8f341f8516dd5856f3`로 갱신했다.
- 검증: 새 spec identity 2,000행, 관련 unittest 72건, Ruff, JSON parse, `git diff --check`, LoRA `validate-contract`, 5-arm evaluation `validate-contract`를 통과했다. `.venv-data`에는 PyTorch·Transformers가 없어 학습 계약 CLI를 실행할 수 없었고, 고정 의존성이 설치된 `.venv`에서 같은 계약을 정상 확인했다.
- 다음 단계: `build-d9468dfae98e` 전용 target에서 Codex 담당 초안을 다시 누적하고 Claude 복구 뒤 반대편 판정과 Claude 담당 초안 생성을 이어간다. deterministic PASS와 양쪽 teacher PASS를 모두 충족하기 전에는 finalization·학습으로 진행하지 않는다.

### 2026-09-03 - 선택 날짜 QA의 원국 동시 근거 고정

- 실행 진단: `build-d9468dfae98e` target은 provider call 12회, Codex 초안 시도 240회, deterministic PASS 236행, 영구 실패 0행까지 진행했다. 이 가운데 direct period schema QA 60행이 날짜의 연간지·월간지·일진만 답해 `bound_chart_v2`의 날짜 사실+원국 사실 동시 사용 규칙과 어긋났다. dashboard v1.11 Gate 재생에서는 47행이 `chart_fact_missing`으로 차단됐고 13행은 period와 natal 간지의 우연한 문자열 일치 때문에 의미상 잘못 통과했다. 진행 중이던 다음 호출은 state 기록 전에 중단했으며 기존 target은 비후보 진단 이력으로 보존한다.
- 계약 교정: 두 direct period schema 질문에 날짜 세 label과 함께 명시적 원국 근거 하나를 요구한다. 권장 anchor는 `원국 일주=<정확한 일주>`이며, 정확한 pillar label·일간 label·정순서 원국 네 기둥도 허용한다. 단순 문자열 일치, 부정된 claim, 잘못된 위치는 근거로 세지 않으며 answer의 claim에 대응하는 `used_fact_paths`도 필수다. teacher 전역 지침은 `[MANDATORY ANSWER CHECKLIST]`가 동시 사용을 요구하는 record에만 적용해 natal snapshot이 없는 HARD QA로 번지지 않게 했다.
- smoke 재생: `build-60f73934fd43` target의 첫 Codex 시도 40건은 보강 계약으로 40/40 PASS했다. direct period QA 16건은 날짜 세 한국어 label, 원국 일주 anchor, period 3경로+natal 1경로를 모두 포함했다. 저장 당시 1건은 일진과 원국 일주가 같은 값일 때 positive coverage가 일진을 놓친 오탐이었고, 정확한 label 직결 fallback과 부정·순서 회귀로 닫았다. 이어진 `build-bd9de5ccef4d` target에서는 `연간지는 그 날짜가 속한 해의 간지인 A` 동격 문형 4건을 놓친 사실을 확인해 같은 field alias와 정확한 값만 허용하는 fallback으로 4/4 재생 PASS했다. wrong owner·wrong value·부정·natal owner 삽입은 계속 차단한다. 조사 오류 3건과 schema 문형 반복은 Claude 자연성 판정의 repair 대상으로 남긴다.
- immutable 산출물: 최종 계약 SHA `05e89acc53f980c461ed11e214601f3c22eafec5a81df5965812a6be1c505756`을 포함한 private `build-67cbcf3317b4`, build SHA `67cbcf3317b4416aa400ab34960376c46a0e2c42119cc90da766e7af6f31d4ae`로 새로 동결했다. dev SHA `2614d5e3578340969e03b2779b26c365bf774729bbc3838ff35998ec22faaf86`, training spec SHA `7cf01c8146190da0e77b717d72a687dce44056de542da8aae15b71ce43fbc229`, projection SHA `c889ccd27fc161144a0e3a8739020aa697c27c7d1615b5547e1c7488acd99458`는 동일하다. 중간 `build-5f1eb11a58e2`, `build-60f73934fd43`, `build-bd9de5ccef4d`도 삭제하지 않고 비후보 이력으로 보존한다.
- downstream pin: LoRA config SHA는 `9c243b1ecfe0ff4cbceb4afb0e3ff7ccab03e876f5327025d9efbe018bd59a94`, 5-arm 평가 config SHA는 `b36e9bc55a7135bc83928df3c29c89ff7dc1feb1313bfcb7abc622ad81468449`로 갱신했다.
- 검증: 부정·우연 일치·올바른 correction·일간·원국 네 기둥·provenance·HARD QA 범위 회귀를 포함한 관련 unittest 73건, Ruff, JSON parse, `git diff --check`, 새 spec identity 2,000행, LoRA `validate-contract`, 5-arm evaluation `validate-contract`를 통과했다.
- 다음 단계: `build-67cbcf3317b4` 전용 target에서 Codex 담당 초안을 다시 생성한다. Claude 가용 시 반대편 판정을 먼저 붙이고 조사·내부 field 표현·자연스러운 풀이 품질까지 확인한 뒤 나머지 축으로 확대한다.

### 2026-09-03 - 질문 선택형 날짜 근거 계약 재동결

- 실행 진단: `build-67cbcf3317b4` target에서 provider call 14회로 Codex 현재 초안 276행을 확보했다. 독립 감사에서 구조 사실 오배치와 입력 밖 간지는 0건이었지만, 원국 설명 표본 46행 중 39행이 질문하지 않은 날짜 문단을 추가했고 14행에는 `甲寅로`, `己丑는` 같은 조사 오류가 있었다. `bound_chart_v2`의 광의 Rule 7과 teacher의 질문 선택 지시가 한 spec 안에서 충돌한 것이 원인이었다. 기존 target과 276행은 삭제하지 않고 비후보 이력으로 보존한다.
- 계약 교정: 날짜와 오늘 흐름을 묻는 경우에만 날짜 사실과 원국 사실을 함께 쓰고, 원국-only 질문에는 연결된 period를 덧붙이지 않도록 `bound_chart_v2`와 teacher checklist를 일치시켰다. 원국 설명 300행과 schema 원국-only 210행의 명시적 날짜 주입을 deterministic DROP하며, 표면 구성·오행 분포 질문은 목·화·토·금·수 계수를 모두 요구한다. `일간 丁`은 유효한 원국 근거로 인정하고, `볼 수 있는`의 보조명사 `수`를 水 오행으로 읽던 오탐도 제거했다. 조사 오류는 독음에 맞는 교정 예시와 함께 상대 teacher의 자연성 FAIL 기준으로 명시했다.
- 재개 안전성: `--seed-target`은 같은 private output root의 과거 현재 초안을 stable record ID로 가져온 뒤 새 spec의 deterministic validator를 다시 통과한 행만 상대 teacher 판정 대기로 이관한다. 과거 상대 teacher PASS와 rewrite 예산은 재사용하지 않으며, 새 계약에 어긋난 행은 새로 작성한다. source state SHA와 이관·제외 수만 새 private state에 기록한다. 실제 276행 메모리 재생에서는 177행이 통과했고, 99행은 모두 질문 밖 period 주입으로 제외됐으며 이 가운데 표면 오행 누락도 함께 있던 행은 6행이었다.
- immutable 산출물: prompt SHA `55bdcec6bdf7fa6a91fb68b03cd4a296c705ab9bac0e77abb067190519cc8f90`, 계약 SHA `64d5f5074dab819042ef3d038b0d638dbee3d31e0993a5ef94b825fa73a70b64`를 포함한 private `build-e4f88ecc9b46`, build SHA `e4f88ecc9b4607eceb79a98633c30a925ef1bb7c7e888901cd848d5094706653`로 재동결했다. dev SHA는 `7ff700be25c3eaa27401be89afb7eeda6bba4a9c27ef3451d7853a9fd8d8a629`, training spec SHA는 `d6286e8a00a5ee9f1bfbc660ac92727ed6f5410ec0cb8dce2a95c1ada7f2168e`, projection SHA는 `3bace953ab5c4a06d967408f30d568eec651c2c30087994f17619a1ec6f7675d`다.
- downstream pin: LoRA config SHA는 `d79648754096cd6df6be14a944a3acc1dbcd40f534258790d3db4b9b85f2446a`, 5-arm 평가 config SHA는 `0a1ea3e225f0929de5405ed1090046897cecbf87d21f03b84d4bfb1a30c98bcd`로 갱신했다.
- 검증: teacher·LoRA·평가·Dashboard 관련 unittest 84건, Ruff, `git diff --check`, LoRA `validate-contract`, 5-arm evaluation `validate-contract`를 모두 통과했다. `.venv-data`에는 Transformers가 없어 builder가 환경 오류로 중단됐고, 고정 의존성이 있는 `.venv`에서 같은 builder를 정상 완료했다.
- 다음 단계: 과거 276행을 새 target에 재검증 이관하고 제외 사유를 집계한 뒤 Codex 담당 초안을 1,000행까지 누적한다. Claude가 사용 가능해지면 Codex 초안의 상대 판정과 Claude 담당 초안 생성을 우선하며, 최종 2,000행 모두 deterministic PASS와 반대 teacher PASS를 충족해야 한다.

### 2026-09-03 - 부정문 오탐 교정과 teacher checkpoint 승계

- 실행 결과: `build-e4f88ecc9b46` target에서 과거 초안 177행을 재검증 이관한 뒤 Codex 신규 초안 205행을 더 확보했다. provider call 11회 시점 상태는 상대 teacher 판정 대기 382행, 초안 대기 1,617행, 실패 1행이다. 독립 감사 기준 신규 초안 188행은 natal-only 날짜 주입 0건, 표면 오행 계수 누락 0건, 구조 사실 오류 0건, 전체 답변 중복 0건이었다.
- 실패 원인: 유일한 실패 record `m2v4_8e87db048ecd683a9fe1aacd`의 세 시도는 확정 예측을 부정하는 안전 문장을 `FORBIDDEN_PREDICTION`이 정방향 예측으로 오인하거나, 쉼표 뒤 다른 주어의 부정을 앞선 원국 claim에 잘못 전파해 원국 근거 누락으로 판정한 경우였다. 새 계약으로 세 시도를 재생해 3/3 PASS를 확인했다.
- 계약 교정: 확정 예측 표현은 해당 claim이 실제로 부정되지 않은 경우만 차단하고, claim 뒤 쉼표의 후속 절이 인용 연결어가 아니면 부정 범위를 그 쉼표에서 끝내도록 했다. 정방향 예측은 계속 차단하며, 쉼표 뒤 독립 주어의 부정으로 잘못된 앞 claim을 숨기는 우회도 기존대로 거부한다.
- immutable 산출물: 계약 SHA `638422550f44cc481ba14482aecdfa8706cab2d94fcb87ba388421b33a1679e5`를 포함한 private `build-8ba27d3b5bb0`, build SHA `8ba27d3b5bb0b8fdb0e4bd4030a87c03c7daab1542e390db638bbc70532069ac`로 재동결했다. prompt SHA `55bdcec6bdf7fa6a91fb68b03cd4a296c705ab9bac0e77abb067190519cc8f90`, dev SHA `7ff700be25c3eaa27401be89afb7eeda6bba4a9c27ef3451d7853a9fd8d8a629`, training spec SHA `d6286e8a00a5ee9f1bfbc660ac92727ed6f5410ec0cb8dce2a95c1ada7f2168e`, projection SHA `3bace953ab5c4a06d967408f30d568eec651c2c30087994f17619a1ec6f7675d`는 유지됐다.
- downstream pin: LoRA config SHA는 `7e6a45df3d7a698eee5af4211ba0edbca0c0f15554d3524878b65b5279e3e2fe`, 5-arm 평가 config SHA는 `d9543b835acb1c28ad1351294c264e7efc77992daa1120a0da198fafbc245a37`로 갱신했다.
- 다음 단계: 이전 `build-e4f88ecc9b46` target은 불변 이력으로 보존하고, 382개 현재 초안을 새 target에 재검증 이관한다. 조사 오류가 있는 9개 초안은 상대 teacher 자연성 판정에서 반려·교정하며, 양쪽 teacher와 deterministic validator를 모두 통과한 2,000행만 최종 후보로 확정한다.

### 2026-09-03 - 간지 조사 자동 교정과 자기모순 차단

- 실행 결과: `build-8ba27d3b5bb0` target에 기존 382행을 전부 재검증 이관하고 Codex 현재 초안을 236행 추가해 상대 teacher 판정 대기 618행을 확보했다. 진행 중 호출은 checkpoint 저장 전 안전하게 중단했고 완료 state는 그대로 보존했다.
- 독립 감사: 신규 196행 snapshot에서 구조 사실·금지 추론 오류와 전체 답변 중복은 0건이었다. 다만 14개 record에 `辛丑는`, `乙丑로`, `丁未이라는` 같은 간지 독음-조사 불일치가 있었고, 1개 record는 `辛丑은 원국의 일주가 아니라, 원국의 일주는 辛丑`이라는 자기모순을 포함했다.
- 교정: 12지 독음의 받침과 `ㄹ` 예외를 기준으로 은/는·이/가·을/를·과/와·으로/로·이라는/라는·이라고/라고만 결정적으로 고친다. 서술격 표현 `辛亥이더라도`는 건드리지 않으며 provider 원문과 교정본·버전을 private attempt 이력에 함께 남긴다. seed 이관에도 같은 교정을 적용한다. 제공된 올바른 원국·날짜 label 값을 바로 뒤에서 `아니다/아니라`로 부정하는 claim은 자기모순으로 DROP한다.
- 전수 재생: checkpoint의 현재 초안 618행에 새 교정을 적용했을 때 조사 표현 48건이 정정됐고, 617행은 새 계약 PASS, 실제 자기모순 1행만 DROP됐다. 정상적인 `날짜 일진은 원국 일주가 아니라 별도 정보` 문형과 다음 문장의 한계 부정은 통과한다.
- immutable 산출물: 계약 SHA `dfd004bbe48ba84e009070ec30b25a805410dfa1127e5ef06776ab1312714fbb`를 포함한 private `build-45d72dbfca76`, build SHA `45d72dbfca76535d5290e55d478a5fca81f33d269a0fd895e34aea09625eb465`로 재동결했다. training spec·dev·projection·prompt SHA는 직전 build와 동일하다.
- downstream pin: LoRA config SHA는 `b1a3c67bc17f64854f2df6cd6884e2bb6d8f3a1baeab2e128095e3421c55deec`, 5-arm 평가 config SHA는 `83b6aa65f855cc0e4ab241373e76fff40bdb5056c74769ffac0e117932656b47`로 갱신했다.
- 다음 단계: `build-8ba27d3b5bb0`의 617개 통과 초안을 새 target으로 이관하고 자기모순 1개만 다시 작성한다. 이후 Codex 담당 1,000행을 채우고 Claude 상대 판정 및 반대 방향 생성을 순차적으로 진행한다.

### 2026-09-03 - runtime 없는 HARD QA 경계 교정

- 실행 진단: `build-45d72dbfca76` target은 Codex 현재 초안 975행까지 저장됐다. HARD QA는 runtime 원국 없이 `schema_rule` 하나만 허용하는데, 기존 공통 검사가 모든 HARD QA에 구체적인 원국 일주를 요구해 18개의 올바른 짧은 답변을 누락으로 오판했고, 원국·일주 두 단어가 모두 있는 다음 batch에서는 `None` 포함 검사 예외로 fail-closed 중단됐다. 미저장 batch 외의 state는 손상되지 않았다.
- 계약 교정: `원국 전체와 일주는 같은 말이야?` 질문에만 원국 네 기둥과 일주 한 기둥의 구분을 요구하고, 나머지 HARD QA는 각 `schema_rule`과 기존 구조·금지 추론 계약으로 판정한다. runtime이 없는 질문에서 구체적인 natal value를 요구하지 않는다.
- 안전 승계: current draft가 없고 상대 teacher 판정 이력도 없으며, deterministic 단계에서만 탈락한 최신 attempt에 한해 새 계약으로 재검증 이관한다. 기존 18개 HARD QA 시도를 재생해 18/18 PASS를 확인했으며, 상대 teacher 반려 결과나 과거 rewrite 예산은 재사용하지 않는다.
- immutable 산출물: 계약 SHA `bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a`를 포함한 private `build-da9014c5f24a`, build SHA `da9014c5f24a6ffc239cd8bf1ec64d2ba50855caff6ec90438d5a41a4fefd980`로 재동결했다. training spec·dev·projection·prompt SHA는 직전 build와 동일하다.
- downstream pin: LoRA config SHA는 `d7c5db056be927319617ac4b932acb9e37d9f9a2e6478598d20f2b7ce12fa728`, 5-arm 평가 config SHA는 `6e9eef929cd2d579636c3ffec5e8c93505ab2df9f9aa6de4b4da2e5b1f56eb20`으로 갱신했다.
- 다음 단계: `build-45d72dbfca76` target의 현재 초안 975행과 복구 가능한 HARD QA 18행을 새 target에 재검증 이관한 뒤, 남은 Codex 7행을 작성하고 Claude 방향으로 전환한다.

### 2026-09-03 - Codex 절반 완료와 relation 안내 기준 보강

- checkpoint: final `build-da9014c5f24a` target에 Codex 담당 초안 1,000행을 모두 확보했다. 전수 재생은 deterministic 1,000/1,000 PASS, 구조 claim·필수 사실·잔여 조사·질문 밖 period·실제 자기모순 오류 0건이다. 실질 응답 850행은 모두 3줄 이상이며 1줄 150행은 계약상 예외인 intake 125행과 HARD QA 25행이다.
- 독립 감사 후속: 9행이 현재는 relation·신강약·용신 판단을 거절하면서도 raw 원국이나 간지만 더 받으면 나중에 계산할 수 있다고 암시했다. 양쪽 teacher 지침에 raw fact만으로는 충분하지 않고 deterministic 또는 POLICY_BOUND 계산 결과가 제공된 경우에만 다룰 수 있음을 명시했다. Claude 상대 판정에서 이 안내를 어긴 기존 초안은 FAIL·재작성한다.
- 다양성 상태: 상대 판정 전 초안의 exact 중복 excess는 130행이고 normalized excess는 138행이다. 아직 Gold가 아니며, 2,000행 상대 판정 완료 뒤 기존 diversity 계약의 duplicate repair를 실행해 exact 0과 normalized multiplicity 2 이하를 충족해야 한다.
- 도구 버전: final Claude 호출 전에 실제 Claude Code `2.1.259`와 Codex CLI `0.150.1`을 확인했다. 5-arm 평가의 Claude CLI pin을 `2.1.259`로 맞췄고 평가 config SHA는 `8f9dc776fe81a24d39c1d642a0f1a67a4e0d811eceb744415066d68d96001d5c`다.
- 다음 단계: 강화된 runner hash의 새 target으로 Codex 1,000행을 재검증 이관한 뒤 Claude가 Codex 초안을 먼저 상대 판정하고, 이어 Claude 담당 1,000행을 작성한다.

### 2026-09-03 - 중복 재작성 예산 분리

- 위험 진단: Codex 1,000행의 final diversity 예상 repair 대상은 133행이며 intake 91행, uncertainty 22행, HARD QA 15행에 집중됐다. 기존 runner는 사실 오류·상대 teacher FAIL과 중복 repair가 같은 2회 예산을 공유해, 정확한 답변도 마지막 중복 단계에서 즉시 terminal failed가 될 수 있었다.
- 교정: 사실·grounding 재작성 예산은 기존 2회를 유지하고, 중복 전용 예산 3회를 별도 `duplicate_rewrites_used`로 관리한다. 중복 feedback에는 exact/normalized 원인과 피해야 할 직전 답변을 포함해 첫 문장·설명 순서·문장 구조·예시를 함께 바꾸도록 한다. final manifest에는 중복 재작성 행·시도 수와 최대 라운드를 기록한다.
- 검증: 일반 재작성 예산을 이미 2회 사용한 accepted 행도 중복 때문에 즉시 실패하지 않고 별도 재작성 상태로 전환되며, 기존 답변과 구체적인 회피 지침이 feedback에 포함되는 회귀를 통과했다. finalizer의 exact 중복 0·normalized multiplicity 2 이하 fail-closed 기준은 그대로 유지한다.
- 다음 단계: 새 runner target에 Codex 1,000행을 재검증 이관하고 Claude 교차 작업을 시작한다. 양방향 상대 판정 이후 duplicate repair는 provider를 교대해 전용 예산 안에서 수렴시킨다.

### 2026-09-03 - 승인된 Codex-only 실험 fallback 계약

- 선행 교차 판정: Claude Code가 Codex 초안 200건을 별도 호출로 판정했다. 독립 감사에서 드러난 구조·금지 추론·문체 문제 26건은 원본 이력을 보존한 채 Codex로 재작성했으며, 답변이 바뀐 5건의 과거 Claude PASS는 승계하지 않았다. 따라서 현재 초안과 정확히 일치하는 유효 교차 provider PASS는 195건이다.
- 교정본 감사: 현재 Codex 초안 1,000건을 현 validator로 전수 재생해 1,000/1,000 PASS를 확인했다. relation·통근·신강약·용신을 raw fact만으로 계산할 수 있다고 암시하던 19건과 weekly relation 결과 없이 해석을 약속하던 2건은 제공된 계산 결과가 필요하다는 경계로 교정했다.
- 부분 token 감사: pinned Kanana tokenizer와 현재 chat template로 1,000건을 렌더링한 결과 최대 prompt 1,802, assistant 141, 전체 1,927 tokens였고 2,048 초과·truncation·assistant mask 0·EOS 누락·user/system loss leakage는 모두 0건이었다. 이 수치는 중간 checkpoint이며 최종 2,000건에서 다시 전수 검사한다.
- 중간 다양성: 교정 후 1,000개 초안은 exact unique 881개, exact excess 119개이고 normalized multiplicity 2 초과분은 77개다. 최종 Gold가 아니므로 전체 판정 뒤 duplicate repair를 수행한다.
- 작업 요약: Claude Code 사용량 만료 시 남은 작업을 Codex만으로 진행한다는 사용자 지시를 별도 hash-bound execution policy로 고정했다. 원래 Claude/Codex 배정은 보존하고 실제 실행 provider를 별도 기록하며, 동일 Codex의 초안·분리된 2차 판정은 `same_provider_separate_pass`로 표시해 교차 provider PASS로 오인하지 않는다.
- 안전 승계: 기존 불변 target `full-build-da9014c5f24a-a32df727-117d55cb`와 state SHA `1824fb7ddedce9d3d125f42636bbb98a3cf85f59269bfbf0856d8875f1530f63`를 policy에 고정하고, 그 state bytes를 새 target provenance에 복제해 SHA를 target/state identity에 포함한다. 현재 초안과 accepted 초안·review attempt가 정확히 같고 현 validator를 다시 통과한 실제 교차 provider PASS만 승계하며, 이후 초안이 바뀐 review는 재사용하지 않는다. 기존 state 메모리 재생 결과는 초안 1,000건 이관, 교차 PASS 195건 승계, 805건 재판정 대기, 1,000건 신규 초안 대기였다. source state 파일 SHA는 재생 전후 동일했다.
- 실행 신원: fallback 새 target은 실제 `codex --version` 결과, config의 `configured_subscription_default` model selector, `chatgpt_subscription` auth type, execution policy SHA를 state와 manifest에 기록하고 재개 시 일치를 검증한다. raw provider envelope와 session ID는 저장하지 않는다. 확인한 Codex CLI는 `0.150.1`이다.
- finalizer Gate: fallback candidate는 assigned/actual provider, cross/same-provider review mode, 최신 draft와 그 이후 별도 review 호출 순서, review schema, state의 current/accepted draft, 고정 seed의 행별 교차 PASS를 전수 대조한다. manifest의 provider·normalization·duplicate·호출 수 집계도 state에서 다시 계산한다. 이 경로는 LoRA 실험 학습만 허용하고 `cross_provider_teacher_contract_met=false`, `production_promotion_allowed=false`를 고정한다. execution policy·현재 teacher runner·seed snapshot·pipeline state hash가 하나라도 다르면 fail-closed로 중단한다.
- 학습 소비 경계: final build의 teacher governance 전체를 build identity에 포함했다. LoRA loader는 strict 교차 계약과 승인 fallback 계약을 명시적으로 구분하며, fallback에서는 policy/state SHA와 실험 전용·production 금지 조합을 강제한다. training·token audit 파일은 동일한 no-follow file descriptor에서 크기 상한 안의 bytes를 한 번만 읽어 그 bytes를 hash·parse·소비한다.
- 검증: provider 생성 호출 전 `tests.test_mix2k_v4` 65건, 관련 Ruff, Python compile, `git diff --check`, 실제 기존 state의 읽기 전용 2,000행 seed 재생을 통과했다. 전체 709건 중 705건이 통과했고, 나머지 4건은 현재 KST 날짜가 2026-09-03으로 바뀌었는데 2026-09-02를 고정한 기존 dashboard fixture 오류다. 과거 판정 선행 재사용, current draft 불일치, review schema 위조, seed 승계 위조, manifest count 변조, 과대 호출 수, governance 변조, artifact 검증-소비 race 회귀를 포함한다. 기존 config/spec build/private teacher target은 수정하지 않았다.
- 남은 작업: 새 fallback target에서 명시적 policy와 seed target을 지정해 Codex-only 초안·분리된 2차 판정을 실행한다. 2,000행 완료 뒤 duplicate Gate와 finalizer token/mask/EOS audit를 통과한 build만 LoRA 실험 입력으로 사용하며 production 승격은 별도 교차-provider 계약 회복 없이는 금지한다.

### 2026-09-03 - intake 최소 길이 판정 충돌 교정

- 진단 실행: fallback target `full-build-da9014c5f24a-a8b8f17a-117d55cb`에서 seed 1,000건과 유효 교차 PASS 195건을 정확히 승계했다. 25번째 provider 호출까지 state SHA `f7ff67e7d0a993d629b62fd77596bfa5e45ed831a9075ecd629583eb082b0bb3`, accepted 661건, review 대기 305건, draft 대기 1,034건, 영구 실패 0건을 원자 저장했다.
- 중단 사유: 한 shard의 `intake_state_correction` 20건은 각 spec이 최소 1줄·1문장을 허용하고 실제 답변도 이를 충족했지만, 2차 판정 공통 문구가 모든 답변에 3줄·3문장을 요구해 20/20을 `MINIMUM_LENGTH_VIOLATION`으로 잘못 판정했다. 다음 호출을 중단했고 이 target은 삭제하지 않은 비후보 진단 이력으로 보존한다.
- 교정: 2차 판정은 각 `[TASK].response_contract`의 최소 줄·문장 수를 그대로 적용하고, intake·HARD QA의 1줄·1문장을 그 이유만으로 FAIL하지 않도록 명시했다. provider 응답이 충족된 contract에 길이 위반을 붙이면 어떤 record도 state에 반영하기 전에 호출 전체를 fail-closed로 거부한다.
- 검증: 관련 회귀를 포함한 `tests.test_mix2k_v4` 65건과 문서 자동 Gate 1건, Ruff, Python compile, `git diff --check`를 통과했다. 수정된 runner hash의 새 target은 원래 고정 seed에서 다시 시작하며 진단 target의 잘못된 판정이나 같은-provider PASS를 승계하지 않는다.

### 2026-09-03 - 위치별 동일 십신 축약 checkpoint 복구

- 중단 지점: authoritative fallback target `full-build-da9014c5f24a-6e5149a5-117d55cb`은 provider call 60 뒤 accepted 1,091건, review 대기 16건, draft 대기 892건, failed 1건에서 안전 정지했다. 실패 행 `m2v4_67ad3171b4f72afa5168ecc6`과 최대 rewrite 직전 행 `m2v4_437e3b58e25808533390db66`은 값 자체는 정확했지만, 천간·지지 십신이 같은 경우 `천간과 지지의 십신이 모두 정재/편재`로 묶어 써 위치별 완전한 claim을 요구하는 현 고정 validator를 통과하지 못했다.
- 복구 원칙: 현재 runner·contracts·spec·policy를 바꾸면 hash-bound target이 달라져 완료된 1,091건을 그대로 이어갈 수 없으므로 이 revision의 parser는 고정했다. 대신 `.pipeline.lock`, target명, provider_calls 60, runner SHA `77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835`, contracts SHA `bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a`, pre-state SHA를 모두 대조하는 1회성 operator recovery를 추가했다. provider 호출 없는 답변 교정·승인·attempt 삭제·rewrite counter 감소는 금지하고, 실패 행의 status와 두 행의 구체적 재생성 feedback만 바꿨다.
- 감사 이력: pre-state SHA `156b92ddb824634379045e2d50b9036875b4d632dcfb8022665804ec01d6c14f`, 복구 직후 state SHA `8c227e0c7071b9325a01b5a93224580e83972b04731d8235beae2105a93361b2`, private recovery manifest SHA `24c5fc5be8db1d18e83182f487278febb0ad3208960a539f23a40c0ab0fed4c0`를 pre/post snapshot과 함께 보존했다. finalizer는 snapshot exact diff와 과거 attempt prefix를 재생하고, 두 행 모두 복구 뒤 실제 Codex draft 및 별도 review PASS가 추가되지 않으면 final build를 거부한다. recovery 감사 결과는 token audit summary hash에도 포함한다.
- 검증: recovery 변경 범위·idempotency·후속 provider PASS 필수 회귀를 포함해 `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 68건, Ruff, Python compile, `git diff --check`를 통과했다. 독립 감사에서도 기존 target 복구에는 parser 변경과 새 seed epoch보다 제한적 state 복구가 더 안전하다는 결론을 받았다.
- 다음 단계: review 대기 16건을 먼저 판정한 뒤 두 복구 행을 포함한 다음 draft shard를 실제 Codex로 생성하고 별도 review한다. 이후 2,000건 승인, duplicate Gate, pinned Kanana tokenizer 전수 audit 순서로 진행한다.

### 2026-09-03 - call 148 구조적 제한 문구 충돌 복구

- 중단 지점: authoritative fallback target `full-build-da9014c5f24a-6e5149a5-117d55cb`은 provider call 148 뒤 accepted 1,911건, review 대기 16건, draft 대기 70건, failed 3건에서 fail-closed 정지했다. 실패 행 `m2v4_04a03609c2525768fe53777e`, `m2v4_9a689b213b285e1bda18f24f`, `m2v4_27ca49a80737af0a331ab558`과 최대 rewrite 직전 행 `m2v4_99b224076cafeba65e696b29`은 2차 판정 모델이 관계·신강약·용신을 직접 계산하지 말고 별도 검증 결과가 필요하다고 올바르게 안내했지만, deterministic 정규식이 그 제한 문구를 실제 구조 사실 claim으로 오인했다.
- 복구 원칙: runner SHA `77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835`, contracts SHA `bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a`, 1차 recovery script SHA `51a7379889a5898400c6e37a9cbedba8ccf492261cd5d687cfa823adfc34c37e`를 유지했다. 2차 recovery script SHA `ba9699292da045305cc83426282515071976c0cdfc3b3ca9c35a001338cd834c`는 target·lock·provider call 148·pre-state를 고정하고, 세 failed status와 네 행의 재생성 feedback만 변경한다. provider 호출 없는 답변 교정·승인·attempt 삭제·rewrite counter 감소·provider call 증가는 하지 않았다.
- 감사 이력: pre-state SHA `61b9efc0cbfe95cc72cce8e259ec42b2de6e52611dacf9e34448fb78cb85a80c`, 복구 직후 state SHA `5e0add17e841b2f6fdc2ccdb70e2080de4815da6b2dbfca7e7a50000fb58c25c`, private recovery manifest SHA `1ccb44fbb9faf261df942711f1d7c6e1062e8f40a959a8b90e32bd1cea8df9ed`를 보존했다. 복구 직후 상태는 accepted 1,911건, review 대기 16건, draft 대기 73건, failed 0건이다. finalizer는 1·2차 sidecar와 state event를 양방향으로 대조하고, 허용된 네 행의 attempt overflow만 인정하며, 네 행 모두 복구 뒤 실제 Codex draft와 분리된 review PASS가 추가되지 않으면 final build를 거부한다.
- 검증: 복구 적용 전 관련 unittest 71건, Ruff, Python compile, `git diff --check`를 통과했다. 독립 감사에서도 복구 이력 우회나 provenance 손상을 일으키는 P0/P1 문제는 없었으며, 복구 적용 후 동일 검증을 다시 실행한다.
- 다음 단계: 기존 review 대기 16건과 복구 4건을 소량 호출로 먼저 처리해 실제 draft·별도 review PASS를 확인한다. 이후 2,000건 승인, duplicate repair, pinned Kanana tokenizer 전수 audit 순서로 진행한다.

### 2026-09-03 - call 149 세 문장 intake 재시도 복구

- 중단 지점: call 149는 기존 review 대기 16건 가운데 11건을 승인해 accepted 1,922건까지 진행한 뒤 `m2v4_17f19223c200ce872e34b2d8`, `m2v4_91b95e86fca05f83012bc87f`, `m2v4_b98b22d7d5bbb0186e289392`을 terminal failed로 두고 정지했다. 세 답변은 3줄이지만 마지막 줄에 두 문장을 써 `uncertainty_blocked_boundary-f001`의 1~3문장 production instruction과 최소 3문장 response contract를 합친 정확히 3문장 조건을 어겼다. 이는 validator 오탐이 아닌 실제 instruction violation이다.
- 복구 원칙: pre-state SHA `225dc4910f760480c9961371011eb1d9cca7b689360e35bc52fbf3069b25b770`, provider call 149, 세 ID를 고정한 3차 recovery를 적용했다. 답변·attempt·rewrite counter·accepted·provider_calls는 보존하고 status만 `failed`에서 `needs_draft`로 바꾸며, 기존 review JSON 앞에 정확히 3개 완결 문장·3줄과 입력·도구 제한을 세 번째 문장에 결합하라는 지시만 추가했다. recovery script SHA는 `515518f66eff1de379ec5c13a855d6adbbdaaa3a1e2ba0979019e4fcc6292e31`이다.
- 감사 이력: 복구 직후 state SHA `d8f1cd21874bf3607bd6e8d23e97dfe402a001ccb2dce3343c9648407e39726a`, private recovery manifest SHA `1d1cb13eb0dc8ca31550c9d45bfb2c7244f208161730019c00e2ce2ae92d1670`를 보존했다. finalizer는 1·2·3차 event와 sidecar를 양방향 대조하고, 각 복구 행에 정확히 새 draft 1회와 더 늦은 별도 review 1회만 허용하며, non-empty current/latest/accepted draft 일치와 exact overflow allowlist를 강제한다. sidecar 작성 중 중단된 경우 기존 파일을 삭제하지 않고 exact bytes를 확인해 이어 쓴다.
- 검증: 관련 unittest 76건, Ruff, Python compile, `git diff --check`를 통과했다. Claude Code `2.1.259` 읽기 전용 감사와 별도 Codex sub-agent 재감사에서 null draft, 부분 sidecar crash, attempt 무제한 증가를 보강한 최신 bytes 기준 잔여 P0/P1이 없음을 확인했다. 이전 recovery가 진행 중인 상태에서 다음 event를 기록하는 것은 pipeline의 global failed stop을 풀기 위해 허용하되, 최종화는 `require_completed_provider_passes=true`로 모든 복구 완료를 강제한다.
- 다음 단계: 변경을 커밋·푸시해 recovery tool SHA를 고정한 뒤, 소량 provider 호출로 2·3차 복구 7건을 먼저 작성·판정하고 정확한 후속 이력을 확인한다.

### 2026-09-03 - call 154 relation 제한 문구 오탐 복구

- 진행 결과: call 150 초안과 call 151 분리 판정으로 2·3차 복구 7건을 모두 PASS시켜 `all_recoveries_completed=true`를 확인했다. 이어 call 152~153까지 승인 1,957건을 확보했고, call 154의 20개 초안 중 14건은 deterministic PASS, 3건은 terminal failed, 1건은 rewrite 한도 직전 `needs_draft`가 됐다.
- 오탐 진단: `m2v4_0f1fc1adf220e2bdf6e6fa85`, `m2v4_72b78ba78948ce9852dad13c`, `m2v4_7de74a3e13e9240d3f406fac`, `m2v4_daeffba875b39af3062f99fb`은 실제 합·충 관계를 주장하지 않고 별도 검증 결과가 필요하다는 제한을 설명했지만, 고정 정규식이 `합·충 관계 결과`를 구조적 relation claim으로 읽었다. `이 자료가 있어도 그 관계를 제가 새로 계산하지 않으며, 별도로 검증되어 입력된 관계만 설명할 수 있습니다.` 문구로 현재 spec과 validator를 재생해 4/4 PASS를 확인했다.
- 복구 이력: pre-state SHA `53a6709281505bdb81077a0ac610ff4274d56d7a03174edbb5b183fe95f41b52`, provider call 154에 고정된 4차 recovery를 적용했다. failed 3건만 `needs_draft`로 전환하고 임계 1건은 status를 유지한 채 네 feedback만 강화했다. 복구 직후 state SHA는 `c0018e4361a6dfef76776ee622dc44cb3086a519c454da60b2025275ab007299`, private manifest SHA는 `c3e0e715cb3d8912d6ed22b0f3a38e529e711f96214aa98fffc1ff70072085d4`, recovery script SHA는 `1441792a68a8fd402b33f22e5ed37950c74d0ef985d6cc705876e2d1adad5e2b`다. 답변·attempt·counter·accepted·provider_calls는 수정하지 않았다.
- 감사 계약: 4차 wrapper는 기존 1~3차 chain의 완료를 먼저 강제하고, 네 행의 exact status·feedback patch, 추가 draft와 더 늦은 review 정확히 한 쌍, non-empty draft 결속, overflow allowlist를 검증한다. finalizer는 recovery가 없는 빌드와 기존 1~3차만 있는 빌드를 이전 validator로 위임하고, 4차 event와 sidecar가 한쪽에만 있으면 거부한다. fresh·부분·prepared bundle은 live state를 쓰기 전에 메모리 state와 sidecar chain을 검증한다.
- 검증: 관련 unittest 78건, Ruff, Python compile, `git diff --check`, 실제 live pre-state dry replay를 통과했다. 독립 sub-agent 감사 기준 잔여 P0/P1은 없고 call154 전용 crash 세부 fixture 확대만 P2로 남았다.
- 다음 단계: recovery tool SHA를 커밋·푸시로 고정하고, 4차 네 행을 먼저 실제 Codex 초안과 별도 review로 완료한 뒤 review 대기 14건과 남은 25건을 이어간다.

### 2026-09-03 - call 174 duplicate 제한 문구 재시도 복구 준비

- 중단 지점: call 159에서 최초 2,000건 승인을 완료한 뒤 186행 duplicate repair를 예약했다. call 174 종료 상태는 accepted 1,944건, `needs_draft` 36건, `needs_review` 19건, failed 1건이며 live state SHA는 `d54270a3d8d0e8d1824d3f2296f0aaa098725d8241aedcf1f09747aa1ea3f0e4`다. failed 행 `m2v4_99b224076cafeba65e696b29`은 제공되지 않은 신강약·용신을 만들지 않는다는 올바른 제한이 고정 정규식에 구조 claim으로 오인되어 duplicate D4가 deterministic FAIL된 경우다. relation 두 행은 동일한 유형의 오탐을 피하도록 재작성 대기 상태의 feedback만 구체화한다.
- 복구 계약: target·lock·provider call 174·runner SHA `77f695128842eb91317f43b44aab5f7cd5cb9bd4f54e8f608d84cf0c875d5835`·contracts SHA `bdb6acb3c2211cd52a46f3f33b27ff103c07a40d2c9206922fd3eadc85e2761a`·pre-state SHA를 모두 고정한 5차 recovery를 준비했다. provider 호출 없는 답변 편집·승인·attempt 삭제·counter 감소는 금지하고, `99b224` 행의 status와 세 행의 feedback, recovery event만 변경한다. 복구 도구 SHA는 `744d2f44d30d8c6e3ee34eff54f559b818b9de9a997986625f950d6613ff3fbc`, 메모리에서 계산한 복구 후 state SHA는 `a237d16e31d4d6a63834318b11cc5707aa59abc5466119694f96079049ed87a0`다.
- 감사 계약: 기존 1~4차 recovery는 고정 ordinal·provider call의 실제 PASS pair로만 구성한 audit-only deep-copy projection으로 기존 validator를 재생한다. live duplicate suffix는 별도로 검증해 `99b224`·`0f1fc`·`7de74`의 새 D5/R3, `04a036`의 call 174 D5 후 R3을 강제하며, 후속 duplicate 2·3차도 counter 증가와 새 draft→더 늦은 별도 PASS review가 정확히 맞을 때만 허용한다. fresh·partial·prepared 경로는 sidecar와 메모리 chain 검증이 완료된 뒤에만 live state를 원자적으로 교체한다.
- 검증: `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 86건, Ruff, Python compile, `git diff --check`, finalizer CLI import, 실제 live pre-state 읽기 전용 재생을 통과했다. 별도 Codex 감사에서 fixed recovery projection·stale PASS·counter laundering·duplicate 2·3차 상태 전이를 교차 확인했다. 또한 모든 후속 attempt를 고정 spec으로 재검증해 중간 payload 필드 누락·assigned provider/fallback 변조·정규화 불일치를 거부한다. 실제 target을 임시 복사한 end-to-end 실행에서 5개 chain, 예상 post-state SHA, 멱등 재실행을 확인했고 원본 state SHA는 변하지 않았다.
- 다음 단계: recovery 도구와 finalizer 연결을 먼저 커밋·푸시해 tool SHA를 고정한 뒤 live recovery를 적용한다. 소량 provider 호출로 필수 초안·review pair를 완료하고, 나머지 duplicate repair와 finalizer token/mask/EOS/leakage 전수 감사를 이어간다.

### 2026-09-03 - call 177 정확히 3문장 후속 복구 적용

- 실행 결과: 5차 recovery를 적용해 state SHA `a237d16e31d4d6a63834318b11cc5707aa59abc5466119694f96079049ed87a0`, private manifest SHA `af3aa8013e6ebb7350cfe201249fa22d6dd70ced85d4ca0e1491cee46c93ef34`를 확인했다. call 175 review는 19건 중 18건을 승인했고, call 176의 20건 초안은 전부 deterministic PASS했다. call 177 review는 16건을 승인했지만 5차 복구 대상 세 행의 4문장 답변을 production instruction의 1~3문장 상한 위반으로 올바르게 FAIL 했다. 이어 6차 recovery를 적용해 세 행을 새 초안 대기 상태로 복구했으며, 현재 state는 accepted 1,978건, `needs_draft` 22건, failed 0건이고 SHA는 `8ca7eebc1e8912c28d3ea601306410294d01cb2283d9ddd481a2ea53d3d696e9`, private manifest SHA는 `d1bc8cb96403c20dcdaa0f5f95a703df51f963249cb8671978214924838cfa73`다.
- 복구 계약: provider call 177의 세 D5@176 deterministic PASS와 R3@177 `PRODUCTION_INSTRUCTION_LENGTH_VIOLATION` FAIL을 before/after prefix에 byte 단위로 보존한다. 6차 recovery는 provider 호출 없는 답변·승인·counter·attempt 변경 없이 세 status를 `needs_draft`로 복구하며, 정확히 3개 완결 문장·3개 비어 있지 않은 줄을 만족하는 새 D6와 더 늦은 별도 R4 PASS를 필수로 한다. pre-state SHA는 `74055a4ad1ee5f1ef1b80fecd66e7c0a22d38d0633ea76168dcc376149373f3e`, 복구 도구 SHA는 `4f7e627477864624cefe1ab9e8f012b6336518a2ab7fb81eee238d8dbf47aca1`, 메모리 예상 post-state SHA는 `8ca7eebc1e8912c28d3ea601306410294d01cb2283d9ddd481a2ea53d3d696e9`다.
- 감사 계약: call 174는 고정 before snapshot에서 세 R3 FAIL만 제거한 D5 review 대기 projection으로 `partial_superseded`를 보고하며 PASS로 바꾸지 않는다. 실제 current의 영향 세 행은 call 177 상태 머신으로, 비영향 prior recovery 행은 call 174 AFTER 기준으로 별도 검증한다. D6·D7·D8 전부에 정확히 3문장·3줄과 고정 spec payload/provider provenance를 강제하고, duplicate 2·3차는 counter와 새 draft→review pair가 맞을 때만 허용한다. 최종 완료에서는 overflow 11-ID exact 집합과 모든 prior pending 해소를 강제한다.
- 검증: `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 93건, Ruff, Python compile, `git diff --check`, finalizer CLI import, live incident pre-state 재생을 통과했다. 실제 target 임시 복사 end-to-end에서 6개 chain·`partial_superseded`·예상 post-state SHA·멱등 재실행·원본 state 미변경을 확인했다. 독립 Codex 공격 감사의 잔여 P0/P1은 없다. Claude Code Sonnet 읽기 전용 감사도 긴 fallback timeout으로 약 5분 18초 만에 완료됐고, 고정 R3 FAIL 보존부터 live-write 순서와 finalizer 연결까지 지정한 10개 경계에서 P0/P1 결함이 없음을 교차 확인했다.
- 다음 단계: 세 복구 행의 D6/R4를 별도 provider call로 먼저 완료한다. 이어 나머지 19건, duplicate repair, final token/mask/EOS/leakage 전수 감사를 순서대로 수행한다.

### 2026-09-03 - call 178 부정형 validator 오탐 복구 준비

- 실행 결과: call 178에서 대기 20행의 새 draft를 생성했고, deterministic PASS 18건은 `needs_review`로 이동했다. 일반 FAIL 한 건은 재시도 가능한 `needs_draft`, call 177의 strength 복구 행 `m2v4_99b224076cafeba65e696b29`은 rewrite 한도에서 terminal `failed`가 됐다. 현재 live state SHA는 `0d90068687419f473393cda2fcf2ac282ea7a21db62adb29e1b3a067fd8d0fba`이고 상태는 accepted 1,978건, `needs_review` 18건, `needs_draft` 3건, failed 1건이다.
- 원인: strength D6는 정확히 3문장·3줄이며 “새로 판정하지는 않으므로”라고 명시적으로 제한했지만, pinned validator의 부정형 인식에 `판정하지`가 없어 `unsupported_structural_claim:strength_pattern_yongshin`으로 오탐했다. 동일 call의 `m2v4_dc9ad501f2460c7db35d1e80`도 같은 패턴으로 1차 재시도 실패 상태라 terminal 재발 방지를 위한 feedback 보강이 필요하다. 현재 build의 contracts SHA와 기존 attempt 판정을 수정하지 않는다.
- 7차 복구 계약: `operator-recovery-provider-call-178-v1`은 99b의 status만 `failed→needs_draft`로 바꾸고, 99b와 dc9의 feedback만 안전한 재생성 지시로 교체한다. D6 FAIL, dc9 D2 FAIL, 모든 attempt·counter·current draft·accepted·provider call은 byte 단위로 보존한다. 이후 99b는 새 D7/R4 PASS와 모든 후속 draft의 정확히 3문장·3줄을, dc9는 새 D3/R2 PASS를 요구한다. call 177 audit projection에서는 99b만 call 177 checkpoint로 되돌리고 provider_calls 178과 나머지 call 178 이력은 유지한다.
- 고정값: 복구 전 SHA는 `0d90068687419f473393cda2fcf2ac282ea7a21db62adb29e1b3a067fd8d0fba`, 복구 도구 SHA는 `55e1cb480aab3b69f9ab360bef9af2be611cda7c81af66a9b5217cf751b6ec6f`, 메모리 예상 post-state SHA는 `f60520110c8b74af78e47c2fd6f121ce1873718d0bb96967f99029c3080f3266`다. finalizer SHA는 `5323a6568a3a8f81b9917218b85d819dc71261317bb8f3eeada3b80e97a4b6b6`이며 새 7차 chain validator를 import한다.
- 검증: call 178 전체 20행의 상태 전이·provider provenance·normalization·고정 spec replay를 두 독립 Codex 감사로 확인했다. `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 98건, Ruff, Python compile, `git diff --check`를 통과했다. 실제 target 임시 복사 end-to-end에서 7개 chain, `partial_superseded`, 예상 post-state SHA, 멱등 재실행, 원본 state 미변경을 확인했다. Claude Code Sonnet 읽기 전용 공격 감사도 약 6분 만에 완료됐고 지정한 10개 경계에 P0/P1 결함이 없었다.
- 다음 단계: 복구 도구·테스트·finalizer 연결을 커밋·푸시해 tool SHA를 고정한 뒤 live recovery를 적용한다. call 179에서 대기 review 18건을 먼저 판정하고, 다음 draft/review pair에서 99b·dc9와 남은 2건을 완료한다.

### 2026-09-03 - call 181 필수 intake·명시 label 재판정 복구 준비

- 실행 결과: 7차 recovery를 예상 SHA `f60520110c8b74af78e47c2fd6f121ce1873718d0bb96967f99029c3080f3266`로 적용했고 private manifest SHA는 `d19b462b54d4729f62b953fda39b0e27f7d09cfce52babdb27549d3e28c0983d`다. call 179 review 18건은 전부 PASS해 accepted 1,996건이 됐다. call 180의 남은 4개 draft도 전부 deterministic PASS했지만 call 181의 별도 review는 4건 모두 정당하게 FAIL했다. 현재 live state SHA는 `d8fd11472dbefb8deea0bfbd424b62733c7d11ad09e469a53ca3f688f2d4f047`, 상태는 accepted 1,996건, `needs_draft` 3건, failed 1건이다.
- 실패 판정: 99b D7은 정확히 3문장·3줄과 비추론은 지켰지만 필수 출생 입력 요청을 누락해 `MISSING_REQUIRED_INTAKE`가 정당하다. dc9·953은 질문의 `신강약` label을 생략했고, 5fd는 meta HARD QA의 `K0`·`Gold`·`현재 허용 evidence` label을 생략해 `MISSING_EXPLICIT_FIELD_LABEL`이 정당하다. 기존 D@180/R@181 판정을 오탐으로 바꾸거나 provider 호출 없는 승인으로 바꾸지 않는다.
- 8차 복구 계약: `operator-recovery-provider-call-181-v1`은 99b만 `failed→needs_draft`로 복구하고 네 행의 feedback만 pinned validator로 미리 검증한 구체적 지시로 교체한다. D7/R4·D3/R2·D2/R2, rewrite·duplicate counter, current draft, accepted, provider_calls는 그대로 보존한다. 새 provider pass는 99b D8/R5, dc9 D4/R3, 953·5fd D3/R3이며 모든 draft sequence는 181보다 크고 review는 각 draft보다 늦은 별도 PASS여야 한다.
- projection 계약: call 178 audit copy에서는 고정 incident의 99b D7과 dc9 D3를 유지하고 call 181의 실패 R4/R2만 제거한다. dc9의 review가 증가시킨 rewrites만 call 178 checkpoint 값 1로 복원하며, provider_calls 181과 953·5fd를 포함한 나머지 이력은 유지한다. 실제 current state에서는 모든 FAIL과 counter를 원형대로 별도 검증한다.
- strength deterministic gate: 99b의 모든 후속 draft에 정확히 3문장·3줄, 생년월일, 양력·음력, 출생시각 또는 시간 미상, 출생 도시·국가, 윤달, 계산 도구 미연결, 검증된 네 기둥, 용신·신강약 결정 불가, 두 판단 비추론을 강제한다. `정할 수 있다`·`판정 가능`·`도구 연결 가능` 같은 반대 주장은 차단하고 `연결되어 있지는 않아`라는 강조 부정과 `설명할 수 있다`는 허용한다.
- 고정값: 복구 도구 SHA는 `ac35dac8bb548ac68fb6d99e6ebeb89257712367c66088cc21717ce600f4612b`, 메모리 예상 post-state SHA는 `abc828cf31a21b1db3c7bfc202886acfee8e2821e37dcfb44c09823f8c24c4e3`다. finalizer SHA는 `909f89b7e29df236fd1742e5abcf5046424f3ad1c7716ad0a589e7fd81ab4752`이며 8차 chain validator를 import한다.
- 검증: call 181 전체 review provenance와 정당성을 두 독립 Codex 감사로 확인했다. projection·counter laundering·fresh pair·duplicate 2·3차·overflow 11-ID exact·이전 recovery 완료·sidecar/live-write 순서를 검증했다. `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 102건, Ruff, Python compile, `git diff --check`를 통과했다. Orca 공격 감사가 찾아낸 gate 우회 두 차례를 수정했고 정상·강조 부정·설명 가능 PASS 및 label 생략·도구 긍정·결정 긍정·판정 가능·연결 가능 BLOCK을 회귀로 고정했다. 최종 Claude Code Sonnet 읽기 전용 재감사도 지정 문장을 직접 추적해 P0/P1 결함이 없음을 확인했다.
- 다음 단계: 복구 도구·테스트·finalizer 연결을 커밋·푸시해 SHA를 고정한 뒤 live recovery를 적용한다. call 182에서 네 새 draft를 만들고 call 183에서 별도 review한 뒤, 2,000건 accepted 상태에서 duplicate scan과 final token/mask/EOS/leakage 전수 감사를 이어간다.

### 2026-09-03 - fallback teacher 2,000건 완결 및 최종 token audit

- teacher 완료: 8차 recovery 적용 뒤 call 182에서 마지막 4개 draft가 deterministic PASS했고, 실제 Claude Code Sonnet 별도 판정도 4/4 PASS했다. call 183의 정식 분리 판정으로 잠시 2,000건 승인에 도달한 뒤 전체 duplicate Gate가 정규화 중복 1건을 추가 재작성 대상으로 잡았다. call 184의 새 3문장·3줄 초안은 Claude 별도 판정과 deterministic validator를 모두 통과했고 call 185 정식 판정 뒤 최종 accepted 2,000건이 됐다.
- teacher 산출물: private target `full-build-da9014c5f24a-6e5149a5-117d55cb`, state SHA `422ae570f4ea42bd742e5964f4a062db2cfcdb81137d1701ae2fbcd6e31b2eed`, manifest SHA `2ff4a818ecd1d7b13545ce0a2ad723021c1fd70a75e0f2f7a2e766da9cb1541d`, candidate SHA `864e2dfa6ee914db08501b0b824054352fa2d3755f74f67887a810283f4f4937`이다. exact duplicate는 0개, normalized multiplicity 최대값은 허용 상한 2이고 duplicate rewrite는 186행·187회다. 8개 operator recovery는 모두 실제 후속 draft/review PASS로 완결됐다.
- provider 경계: assigned 역할은 Claude/Codex 각 1,000건이지만 승인 fallback에서 실제 초안은 Codex 2,000건이다. 최종 review는 실제 Claude 191건과 Codex 분리 판정 1,809건이므로 `cross_provider_teacher_contract_met=false`, `production_promotion_allowed=false`를 유지한다. 복구된 Claude가 마지막 5건을 별도로 PASS했어도 기존 hash-bound state의 provider provenance를 사후 변경하지 않았으며, 이 결과는 LoRA 실험 전용이다.
- finalizer 교정: seed import 시점의 교차 PASS 195건 중 4건이 duplicate rewrite로 교체되어 최종 생존 승계본이 191건인데 기존 finalizer가 두 수의 동일성을 잘못 강제했다. pinned `_import_seed_drafts`를 빈 메모리 projection에서 전수 재생해 역사적 195건과 `seed_import` 전체를 다시 고정하고, 2,000행별 imported draft/review의 exact 목록·필드 집합·index 0 prefix를 live state와 대조하도록 보강했다. 정상 195→191 감소는 허용하지만 later imported D2/R2, seed 부적격 행의 가짜 imported review, extra/null field, attempt 위치 이동과 state·manifest 동시 위조는 거부한다.
- 최종 학습 build: pinned K0 tokenizer와 현재 chat template를 사용한 private `build-54836f556b4f`, build SHA `54836f556b4f5eab0b82c5e21659b3ba23ff591d715a99677e4378c73eb370f3`를 생성했다. finalizer SHA는 `a5393b66cf93b987afade1d0604601e4381fefadf232a1ced4a9746fd4191f4c`, training rows SHA는 `a58904fa4968501f36be2e03e79d783ca6d4126164bd62fb859a924583e2293c`, token audit rows SHA는 `fa484c9169a233e06e30944a7f6ab59f0421d88ae76460d9e6b084f541a0621c`, summary SHA는 `42b32dcf05434f4e8960d7638f6aac247814e3ce85de1c887c43c6cce4702a12`다.
- token 결과: prompt는 최대 1,802·p99 1,796 token, supervised assistant는 최대 169·p99 150 token, 전체 rendered는 최대 1,960·p99 1,934 token이다. 2,048 초과, truncation, zero assistant mask, supervised EOS 누락, user/system loss leakage는 모두 0건이어서 선택 `max_length=2048`이다. 입력·출력 각 4,096 안전 상한은 그대로 유지하며 학습에는 compact projection이 아니라 production-like full runtime snapshot을 사용한다.
- 검증: `tests.test_mix2k_v4`와 `tests.test_mix2k_v4_teacher_recovery` 102건, 전체 `scripts tests` Ruff, Python compile, `git diff --check`, live 2,000행 finalizer를 통과했다. Orca가 초기 보강의 later imported marker와 부적격 imported review 우회를 실제 재현해 canonical replay로 수정했으며, 최신 diff를 Orca와 Claude Code Sonnet이 각각 재감사해 지정한 위조 경계에서 잔여 P0/P1이 없음을 확인했다.
- 다음 단계: 이 build는 K0 기반 LoRA r=8/16/32의 실험 입력으로 사용할 수 있다. production 후보 승격이 필요하면 별도 strict Claude↔Codex 교차-provider revision에서 `cross_provider_teacher_contract_met=true`를 먼저 충족해야 한다.

### 2026-09-04 - 외부 검토 package 정본화·v1.1.0 teacher checkpoint

- 작업 요약: 외부 검토 package SHA `fc01f6d119015928580c93689386fdf4b7b0724e4a7c8ea595e77c956709d17b`를 제안 전용 입력으로 감사하고, 기존 2,000행 중 1,600행은 그대로 상속하면서 intake 250·uncertainty 100·HARD QA 50만 다시 생성하는 v1.1.0 repair 파이프라인을 구현했다. 외부 assistant 답변은 Gold나 teacher로 세지 않으며, 선택된 사용자 발화 35건 외의 prompt·고정 dev 200건·production-like full runtime 형식은 보존한다.
- 변경 범위: `mix2k-v4-reviewed-repair-v1.1.0.json`, `saju_intake_runtime_v2.txt`, `mix2k_v4_reviewed_repair.py`, v1.14 비활성 dashboard candidate, K0 r=16 전용 LoRA 실행기·config와 관련 회귀 테스트를 추가했다. 활성 Dashboard v1.13과 K0 base는 변경하지 않았고 v1.14는 명시적 실행 전까지 비활성이다.
- 안전성 보강: config·prompt·부모 build·ZIP은 같은 file descriptor에서 읽은 snapshot bytes만 hash·parse·소비한다. pipeline state와 provider 원문·정규화본·review는 원자 저장하고 호출 로그에서 전수 재생한다. 관계 validator는 문장 앞의 부정이 뒤의 `성립·작용·관계·사실` 단정을 가리지 못하도록 답변 전체 hard veto와 relation 전용 fail-closed 분기를 적용했으며, `子午충` 같은 새 관계값을 차단한다. 반대로 직접 부정과 검증 결과가 제공된 범위만 설명하는 문장은 허용한다.
- 재현 검증: 과거 v1.1.0 진단 target 13개의 고유 draft 1,256건을 최신 validator로 재생해 과거 strength/relation 오탐 60건이 모두 해소되고, 기존 deterministic PASS에 새 오탐이 0건임을 확인했다. 공격 회귀에는 부정 뒤 후행 성립 주장, bare relation assertion, 구체 relation 값과 evidence guard 위장 문구를 포함했다. 일반 재작성은 최초안+최대 4회로 늘려 한 행 때문에 전체 immutable target을 반복 생성하는 낭비를 줄였고, terminal failure 시 중단·상대 provider PASS·deterministic PASS 조건은 유지한다.
- 현재 teacher 상태: immutable target `repair-23340fc31022`, target SHA `23340fc310223e5c27850f7d0f240105ac2fd799ff95703696e7c81eabf7ec5a`, provider call 55, state SHA `caca7a3bd47b9b23c6e985ccae37c4a8884ba08e7c77ff899e9d5d45f4309436`이다. 400행 중 accepted 238, Claude review 대기 3, Claude draft·rewrite 대기 159, permanent failed 0이다. Codex 배정 200행은 모두 deterministic PASS했고 Claude가 197행을 승인했으며, Claude 배정분은 60행을 작성해 Codex가 41행을 승인했다. 같은 값이 여러 fact path에 쓰이면 `used_fact_values`에는 한 번만 기록한다는 계약과 충돌하는 reviewer 지시는 승인으로 간주하지 않는다.
- 중단 사유: Claude Code가 2026-09-04 14:43 KST에 HTTP 429와 `18:00 KST reset`을 명시했다. 실패 호출은 state에 기록되지 않았다. Claude 결과를 Codex 단독 승인으로 대체하지 않고, quota 복구 전 가능한 Codex review와 재작성만 완료했다.
- 검증: `/home/user/projects/saju_diary_assistant/.venv/bin/python -m unittest tests.test_mix2k_v4 tests.test_mix2k_v4_reviewed_repair tests.test_mix2k_v4_lora_v1_1` 103건, Ruff format/check, `git diff --check`, 과거 target replay를 통과했다. 최신 source SHA는 contracts `2aedeace1f70427d03a30df812d89368bca0c4a2a053c6d60a1e992ff09fd8ba`, repair runner `6d826fc6707ade49eb28e56f7bb0f3f4b5ae6f81cd93e3ff70def56d9990f734`, data config `83cc6c36bc79e54614cdd015602bae1c2082c0bb4434343182fcf8a37ef167ae`다.
- 다음 단계: Claude quota 복구 뒤 같은 target에서 Claude review 3건과 draft·rewrite 159건을 재개한다. 이어 Codex가 Claude 최신 draft를 별도 호출로 판정하고, 400행 전부 양방향 교차 PASS·duplicate Gate를 통과하면 부모 1,600행과 결합해 pinned Kanana tokenizer로 2,000행 token/mask/EOS/leakage 전수 감사를 수행한다. 그 결과가 준비되기 전에는 v1.1.0 build를 입력으로 하는 r=16 학습이나 production 승격을 실행하지 않는다.

### 2026-09-04 - v1.0.1 LoRA 3개 rank 순차 실행 시작

- 실행 범위: 사용자 확인에 따라 이미 최종화·전수 token audit를 통과한 fallback teacher v1.0.1 `build-54836f556b4f`를 별도 비교 실험 입력으로 사용한다. 진행 중인 v1.1.0 repair를 대체하거나 그 결과를 production 후보로 간주하지 않으며, 세 rank 모두 K0 base·`max_length=2048`·1 epoch·250 optimizer step·assistant-only loss 조건을 공유한다.
- 저장 Gate 교정: Transformers `Trainer.save_model()`이 adapter와 함께 자동 생성한 비가중치 pickle metadata `training_args.bin` 때문에 엄격한 adapter-only 재사용 검사가 preflight 결과를 거부했다. 새 임시 저장 직후 정확한 해당 파일만 일반 파일 여부를 확인하고 제거하도록 수정했으며, 재사용 경로의 모든 `.bin`·`.pt`·`.pth`·`.gguf` full-weight 후보 차단은 유지했다. 회귀 테스트 11건과 Ruff check를 통과했고 수정은 PR #22, merge commit `a6e19017ef2d3c7d2011409eed32c87de6984d1c`로 master에 반영했다.
- r=16 preflight: `preflight-b165a8934069`가 RTX 5070 Ti에서 실제 1-step loss `2.476799726486206`, 유한·비영 gradient, base gradient 없음, adapter reload tensor 일치, base weight 불변, peak reserved `4,580,179,968` bytes로 통과했다. Kanana template를 직접 렌더링해 assistant 종료 `<|im_end|>` token id `128010`의 mask가 `1`임을 확인했으므로, 뒤의 supervised 개행 때문에 발생한 TRL의 마지막-token 경고는 실제 EOS 누락이 아니다.
- 백그라운드 실행: r=16 `train-f340a82c76d3`을 시작했고, user systemd transient unit `saju-mix2k-lora-all-ranks.service`가 r=16 완료를 기다린다. 기존 실행이 완료 manifest 없이 끝나면 r=16을 checkpoint에서 재개하고, 성공 후 r=8 preflight→1 epoch 학습→r=32 preflight→1 epoch 학습을 같은 GPU lock 아래 순차 실행한다. 앞 단계 실패 시 다음 단계로 진행하지 않는다.
- 남은 작업: 세 rank의 완료 manifest·adapter hash를 확인한 뒤 K0·KI20을 포함한 고정 dev 200건 5-arm 평가와 실제 회귀 release blocker 판정을 수행한다. 현재 실행 결과만으로 dashboard 또는 production 승격은 허용하지 않는다.
