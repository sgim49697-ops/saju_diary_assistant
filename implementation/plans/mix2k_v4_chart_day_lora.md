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
