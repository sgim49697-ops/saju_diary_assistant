<!-- 2026-09-16-phase8-intent-s3.md - Phase 8A 후보와 독립 S3 비교의 실제 실행·검증 경계를 기록한다. -->

# Phase 8A 오차단 후보·8B S3 진행 기록

## 실행 범위

사용자의 구현 승인에 따라 원본 `master`의 `1b6344b`에서 시작했다. 8A 후보 구현·CPU 검증과 8B의 R16/P0·P1/C_FULL 최대 96요청 비교만 대상이다. Phase 9 이후, 다운로드·학습·데이터 보정·운영 교체·계산 release·새 24문항·소비된 sealed blind는 대상이 아니다. 원본 ZIP과 다른 worktree·세션·서비스를 보존한다.

<a id="phase8a"></a>
## Phase 8A: 후보 구현·CPU 확인 완료

- v1.16과 grounding v3를 보존하고 v1.17·grounding v4·의도 v2 후보를 별도 config/asset/private 경로에 만들었다. 기본 KI20·feature off·P0·생성 설정은 유지한다.
- 날짜 단어만으로 운세를 확정하지 않는다. 능동 운세 요청 절만 날짜 검사에 넣고 짧은 후속은 같은 세션 직전 완료 turn의 **사용자 발화만 읽기 전용**으로 참조한다. 이전 모델의 잘못된 사주 유도는 의도로 사용하지 않는다.
- 사주 맥락의 “그럼 내일은 어때?”는 날짜 검사, 일반 맥락은 일반 대화, 맥락 없음·모호함은 `409 RUNTIME_INTENT_CONFIRMATION_REQUIRED`다. 모델 호출·이력 저장 없이 질문을 확인하며 기존 연결을 유지한다.
- 공개 요청의 history/intent/today 주입은 거부한다. HTTP·직접 호출·worker·저장에 동일 판별을 사용하며 binding 무결성·동일 세션·검사 중 상태 변경을 재검증한다. 이력·snapshot·모델 입력은 재구성하지 않는다.
- 부모 25개 + 새 정책·경로 15개 + 보고서 안전성 5개 = **45개 통과**, 새 프로세스에서도 HTTP·직접 호출을 재검사했다. 브라우저는 합성 API와 실제 candidate asset으로 desktop/mobile 각각 확인·날짜·실패 안내를 검사해 **6개 통과**했다. 모델 가용성만 합성한 표시 경로 검사이며 운영 앱 통합이나 모델 품질 시험이 아니다.
- 초기 브라우저 fixture가 모델 상태 API를 생략해 패널을 숨긴 채 timeout한 것은 fixture 설정 오류였다. 표시 상태를 명시한 뒤 실제 폼 제출·질문 유지·연결 불변을 재검증했다. 초기 보고서에 빠진 부모 테스트 source pin도 보완해 최종 build를 재발행했다.
- 최종 [aggregate](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0/build-49b9aed70565/aggregate.json)·[manifest](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0/build-49b9aed70565/build_manifest.json)·[verification](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0/build-49b9aed70565/verification.json)를 추적한다. 공개 출력에 원문 세션·키·출생정보를 넣지 않았다.
- 유한 규칙이므로 임의 자연어의 완전한 의도 이해를 주장하지 않는다. 사실 주장 검사 문법은 부모 그대로이며 새 진단 scorer와 다르다. 후보 검증 완료는 서비스 적용 승인이 아니다.

## 검증 명령

```bash
.venv-data/bin/python -B -m unittest tests.test_dashboard_grounding_v3 tests.test_phase5_dashboard_v1_16 tests.test_dashboard_intent_canary tests.test_dashboard_intent_v2 tests.test_dashboard_intent_canary_v2 -q -b
# SAJU_PLAYWRIGHT_MODULE·SAJU_CHROMIUM_EXECUTABLE에는 기존 로컬 설치 경로를 지정한다. 다운로드하지 않는다.
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary_v2 execute --execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary_v2 verify --build build-49b9aed70565
uvx ruff check scripts tests
.venv/bin/python -B -m unittest discover -s tests -q -b
git diff --check
```

관련 45개·브라우저 6개·문서 33개·Ruff·diff 검사 통과. 전체 unittest는 최종 문서 상태로 **977개/85.794초, 실패·오류·건너뜀 0**이다. 중간 문서 검사에서 과거의 “Phase 8 미실행” 고정 기대와 최신 후보 표기가 충돌한 3건을 발견했다. 과거 v1.16 build 참조를 복구하고, 8A 완료에 공개 검증 hash·45개·6개·배포 없음 증거를 요구하도록 검사했으며 9~14의 미실행/조건부 검사는 유지했다. 검사를 완화하거나 실패를 제외하지 않았다.

<a id="phase8b"></a>
## Phase 8B: S3 실행·재구성 검증 완료

S3 harness·P1을 먼저 동결하고 계약·CPU·dry-run을 검증한 뒤 `2d951b4`에서 실행했다. 동결된 기존 48개/부모 이력에서 P0·P1 각각 새로 실행해 **96요청 = 86생성 + 10사전 차단**을 완료했다. arm마다 43생성·5차단이며 오류·재시도·출력 재사용·미실행은 0이다. 8A 라우팅을 S3에 섞지 않았고 동결 scorer `role-aware-contract-v1.1.0`을 양쪽에 동일 적용했다. 본 비교의 최장 적격 pair `date-2`부터 실행해 적격성 잔여 2요청은 사용하지 않았다. 결과 확인 후 같은 질문으로 P2/P3를 추가하지 않는다.

### 동결된 구성과 실행 명령

- 전용 `system_context_s3` CLI·backend·projection·집계·config를 추가했다. 기존 S0~S2·재집계·계산·P0 파일은 수정하지 않았다. P1은 [profile](../../configs/chat_prompts/saju_s3_p1_v1.txt)과 [formatter 안내](../../configs/chat_prompts/saju_s3_p1_runtime_v1.txt)의 **묶음 하나**다.
- 모든 48개에서 P0 최종 입력이 부모 S2 byte/token과 같고 P1의 사실 JSON·이력·현재 질문은 같다. 비연결 intake는 P0/P1이 동일하다. 날짜 질문의 원국·날짜 각각 한 사실 요구도 유지한다. 지시문 길이와 우선순위 모두 바뀌므로 개별 문장 효과라고 하지 않는다.
- source·모델 파일·tokenizer/template·생성 설정·환경·원시 입력·부모 이력·묶음 hash를 결합한 build를 사용한다. 최종 사전 dry-run과 실제 실행은 모두 `build-ffd985905b51`, 최대 입력 1,692 token·96요청·10사전 차단이다. 실행 전 checkpoint에는 raw build·출력이 없었고 이후 고정 build만 생성했다.
- 전역 GPU lock·VRAM 12GiB·compute process 없음·native JIT/header 검사를 통과해야 worker를 연다. 요청별 300초, 이미 시작된 요청 재시도 없음, 응답 없는 시작 marker는 미결로 중단한다. 같은 build의 검증된 완료만 재사용하며 다른 build ID로 예산을 초기화할 수 없다. 원시 파일 0600·폴더 0700·공개 파일 allowlist를 검사한다.
- 공개 집계는 arm/stratum별 PASS·FAIL·UNSCORABLE, 대응쌍 변화, 토큰·종료·시간·VRAM을 분리한다. 시간은 model load를 포함하며 P0→P1 순차 측정이다. 순수 kernel 성능이나 운영 KI20 대비 개선으로 해석하지 않는다.
- 계약·plan·dry-run 및 S3 CPU 18개 통과. 초기 합성 resume fixture의 누락 필드와 binding 예외 타입 기대를 고친 뒤 재검증했다. 실제 생성 오류를 숨기거나 scoring 조건을 완화한 것은 아니다. GPU 사전 조회는 RTX 5070 Ti, free 15,119MiB, compute process 없음이었고 기존 local Python header로 native JIT 검사가 통과했다. 실제 시작 시 재확인하고 native JIT·오프라인 모드로 실행했다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.system_context_s3 validate-contract
.venv-data/bin/python -B -m scripts.evaluation.system_context_s3 plan
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s3 execute
# 기존 local sysroot의 Python 3.10 include 두 경로를 CPATH에 지정하고 native JIT는 켠다.
SYSTEM_CONTEXT_S3=R16_P0_P1_V1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -B -m scripts.evaluation.system_context_s3 execute --execute --build build-ffd985905b51
# 중단 뒤에는 같은 코드·입력·환경과 build에 --resume을 추가한다. 시작된 요청을 다시 만들지 않는다.
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s3 verify --build build-ffd985905b51
```

실행 전 전체 unittest **995개/96.957초, 실패·오류·건너뜀 0**, 문서 33개·Ruff·diff 검증을 통과해 harness/P1을 먼저 체크포인트로 저장·푸시했다. S3 결과에 따른 Phase 9 이후 실행·학습·운영 전환은 이번 범위가 아니다.

### 공개 결과와 입력·출력 검증

- [aggregate](../../data/reports/saju_1b_baseline/system-context-s3/v1.0.0/build-ffd985905b51/aggregate.json)·[manifest](../../data/reports/saju_1b_baseline/system-context-s3/v1.0.0/build-ffd985905b51/build_manifest.json)·[verification](../../data/reports/saju_1b_baseline/system-context-s3/v1.0.0/build-ffd985905b51/verification.json)를 발행했다. 실행 종료 내장 검증과 별도 `verify`가 같은 hash로 통과했다.
- aggregate SHA-256: `46e83c1e1b787ad1a244b723f3982efa275d042fe04e4cc1ed6d2590368bf3a2`, manifest SHA-256: `c1e83a8423da5e74a5f9c0eed5de0b7eeca6c851fde7893f4eca3d0122c8e5a0`.
- P0 새 출력 43/43개가 S2의 같은 조건 출력과 정확히 같았다. 이는 결정론적 재현 결과이며 출력 재사용은 아니다. 원시 입력·응답은 각각 96개, 실제 시작 marker·worker log는 각각 86개다.
- P1에서 연결된 44개만 지시문 묶음이 바뀌었다. 비연결 4개는 입력·출력 모두 P0/P1 동일했다. 모든 비지시 영역·JSON·이력은 동결 부모와 일치하며 이력 삭제는 없다.
- 원시 파일은 `runs/SYSTEM-CONTEXT-S3/v1.0.0/build-ffd985905b51/`에 0600으로 보관하고 Git 제외를 확인했다. 기존 460 source·684 부모 입력/응답·공개 부모 hash와 학습 inventory를 보존했다.

### 결과 해석 — 지시문만으로 일괄 개선되지 않음

아래는 **동결 자동 검사기의 PASS / FAIL / UNSCORABLE**이다. 분모는 지표 적용 대상이며 전체 모델 정확도가 아니다. 차단 5개/arm은 생성 품질 분모에서 제외한다.

| 항목 | 적용 수/arm | P0 | P1 |
|---|---:|---|---|
| 필수 사실 사용 | 24 | 11 / 6 / 7 | 12 / 8 / 4 |
| 사실 모순 없음 | 43 | 14 / 8 / 21 | 14 / 12 / 17 |
| 정정 상태 사용 | 6 | 3 / 2 / 1 | 4 / 2 / 0 |
| 잘못된 전제 교정 | 6 | 2 / 3 / 1 | 2 / 4 / 0 |
| 요청 날짜 표기 | 2 | 2 / 0 / 0 | 1 / 1 / 0 |
| 두 문장 준수 | 7 | 4 / 3 / 0 | 5 / 2 / 0 |
| 시간 불확실성 보존 | 5 | 2 / 2 / 1 | 2 / 3 / 0 |
| 불필요한 사주 언급 없음 | 13 | 11 / 2 / 0 | 10 / 3 / 0 |

필수 사실은 양쪽 모두 판정 가능한 15쌍에서 개선 2·회귀 2·양쪽 통과 7·양쪽 실패 4이며 나머지 9쌍에는 판정 불가가 있다. 사실 모순은 공통 판정 가능 19쌍에서 개선 2·회귀 3·양쪽 통과 8·양쪽 실패 6, 나머지 24쌍은 판정 불가를 포함한다. 총 PASS 증가만으로 개선을 선언하지 않는다. 각 층별 분모와 9종 판정 전이는 공개 aggregate에 그대로 남겼다.

층별로는 정정 사용 1쌍·이력 층 두 문장 1쌍이 FAIL→PASS였지만, 날짜 표기 1쌍은 PASS→FAIL이었다. 일반 대화 층의 생성 5개는 양쪽 모두 불필요 사주 언급 검사 PASS이며 기존 오차단 1개는 그대로다. 형식 층의 두 문장 4/6 PASS도 양쪽 동일하다. 따라서 전체 두 문장 4→5 개선을 모든 형식 질문의 개선으로 일반화하지 않는다.

### 원응답 대조에서 확인한 검사기 한계와 실제 오류

허용된 S3 합성 진단의 관련 원응답·claim trace만 읽기 전용으로 대조했다. 자동 점수나 scorer를 사후 수정하지 않았으며 원응답을 문서·fixture·Git에 복사하지 않는다.

- P1에서 새로 사실 모순 FAIL로 잡힌 6건 중 `facts-6`·`premise-2` **2건은 부정 표현 오탐**이다. 올바른 사실을 제시하고 잘못된 개념/전제를 부정했으나, 부정 범위 밖으로 추출된 claim이 `current_asserted`로 처리됐다. 이를 실제 사실 오류 2건으로 세지 않는다.
- 나머지 `facts-5`·`uncertainty-3`·`correction-2`·`format-6`에는 개념 설명 모순, 미상 시주 확정, 최신 원국과 날짜 사실 혼동, 연결되지 않은 일진 삽입 같은 실제 문제가 남았다. 자동 판정 실패 전부가 오탐인 것은 아니다.
- P0 `format-2`·`format-6`도 일반 메시지 작성에 불필요한 원국 내용을 넣었지만 사주 언급 검사에서는 PASS였다. P1의 두 FAIL 전이를 모두 새로운 의미 회귀라고 단정할 수 없는 **검사 누락 2건**이다.
- `date-1`의 P1은 요청 날짜를 **누락**하고 원국과 일진을 혼동했다. 틀린 달력 날짜를 명시한 오류라고 바꾸어 보고하지 않는다. 출력 한도 도달이 원인은 아니다.
- 이 대조는 전체 의미 품질 측정이 아니다. `naturalness/semantics=not_measured`를 유지하며 새 완료 조건으로 삼지 않는다. Phase 9에는 부정/개념 설명 오탐·일반 사주 언급 누락을 검사 범위의 한계로 넘기고, 검사 변경이 필요하면 실행 전에 새 버전을 양쪽에 동결한다. S3를 유리하게 다시 채점하거나 같은 질문으로 새 후보를 고르지 않는다.

### 비용·보존·다음 작업

| 비용 | P0 | P1 |
|---|---:|---:|
| 입력 token 평균 / 최대 | 1,344.979 / 1,692 | 1,358.958 / 1,678 |
| 출력 token 평균 / 최대 | 50.860 / 97 | 50.953 / 105 |
| 요청 지연 평균 / p95, 초 | 11.072 / 12.968 | 11.002 / 13.358 |
| 최대 할당 / 예약 VRAM, MiB | 2,953.814 / 3,276 | 2,949.319 / 3,238 |

입력 통계는 각 arm의 48요청, 출력·지연은 43생성 기준이다. 86개 모두 EOS 종료, 출력 한도 도달 0, 비공개 필드 노출 검사 FAIL 0이다. 지연은 매 요청 모델 로딩을 포함한 순차 단회 측정이므로 유의한 속도 개선이나 warm service 비용으로 주장하지 않는다.

P1의 일괄 개선·교체 근거는 확보되지 않아 `candidate_selected=false`로 종료한다. P0와 현재 기본 모델 KI20을 유지한다. S3 하나로 모델 크기나 데이터가 단일 원인이라고 결론 내리지 않는다. 다음 별도 착수 대상은 [Phase 9/S4](../plans/saju_product_roadmap/phases/phase-09.md)의 K0·3B 공통 P0/FULL·MIN 비교이며 이번에는 등록·다운로드·GPU 실행하지 않았다.

누적 요청은 438 = 부모 342 + S3 96이며 계획상 잔여는 **242 = S4 192 + S6 48 + 적격성 2**다. 이 산식은 후속 실행 승인이 아니다. 추가 호출·학습 후 평가는 별도 등록한다.

실행 전후 운영 service는 active, PID 3144071·재시작 0·코드 `0e77621846c4e9894cb40d801e84d59ad57cb0de`로 같았다. 완료 후 GPU compute process는 없다. 현재 feature off·Phase 6·계산 release·데이터·checkpoint·sealed blind를 변경하지 않았다. 8A 화면 검증은 CPU 표시 fixture, S3 저장/API 대조는 CPU 소비 재생이며 운영 앱 end-to-end 검증은 아니다.

## 진행 기록

- 2026-09-16 최종 검증: `.venv-data/bin/python -B -m unittest tests.test_saju_phase_plans tests.test_saju_product_roadmap tests.test_saju_system_context_plan -q -b` **42개 통과**. ML 의존성을 갖춘 `.venv/bin/python -B -m unittest discover -s tests -q -b` **996개/94.322초, 실패·오류·건너뜀 0**, `uvx ruff check scripts tests`·`git diff --check` 통과. 8A·S3를 다시 verify해 공개 hash 동일을 확인했다. 완료 상태는 실제 8A/8B 증거·고정 공개 hash·96요청 분모를 요구하고 9 이후 허위 완료·증거 누락을 계속 거부하도록 문서 테스트를 보강했다.
- 2026-09-16 보존 확인: 원문 ZIP SHA `85fe0f0716347b5fdbe83fe9b1aeccca67efd6917b30de2dfb248592b8bab47c`·3개 archive·588행 대응 유지. 원본 master/원격 일치와 기존 서비스·다른 worktree 보존을 확인했다. 공개 S3 3파일·관련 문서·정합성 테스트만 저장 대상으로 하며 원시 응답·사용자 ZIP은 제외한다. 남은 제한은 P1 미채택·검사 문법 한계·운영 미배포·Phase 9 이후 미실행이다.

- 2026-09-16 S3 완료: `2d951b4`를 `origin/master`에 푸시한 뒤 고정 build 96요청을 끝냈고 실행 내장·별도 재구성 검증을 통과했다. 항목별 변화와 검사 오탐/누락·실제 오류를 분리해 기록했다. 동결 실행 코드·scorer·P1은 결과를 본 뒤 수정하지 않았다.

- 2026-09-16 실행 전 checkpoint: 8A `c1f090f`를 `origin/master`에 푸시했다. S3 사전 구현과 18개 CPU·전체 995개 검증을 완료했다. 당시 요청 예산 소모는 0이며 다음 단계에서 고정 build만 실행하기로 했다.

- 2026-09-16 8A checkpoint: 8A CPU 후보와 공개 검증을 완료했다. 운영 서비스 PID 3144071·재시작 0·active와 부모 코드/원문 무변경을 재확인했다. 당시 8B 실제 생성은 0개였으며 완료로 표시하지 않았다. 검증된 8A를 한국어 체크포인트로 저장하고 8B를 이어갔다.
