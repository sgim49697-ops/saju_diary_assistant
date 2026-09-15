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
## Phase 8B: 동결 실행기·사전 검증

S3 harness·P1을 먼저 동결하고 계약·CPU·dry-run을 검증한다. 동결된 기존 48개/부모 이력에서 P0·P1 각각 새로 실행한다. 96요청 중 기존 v1.15 guard에 따른 10사전 차단을 포함해 새 생성은 최대 86개다. 8A 라우팅을 S3에 섞지 않으며 새 scorer v1.1을 양쪽에 동일 적용한다. 본 비교의 최장 적격 pair부터 실행하고 적격성 잔여 2요청은 사용하지 않는다. 결과 확인 후 같은 질문으로 P2/P3를 추가하지 않는다.

### 동결된 구성과 실행 명령

- 전용 `system_context_s3` CLI·backend·projection·집계·config를 추가했다. 기존 S0~S2·재집계·계산·P0 파일은 수정하지 않았다. P1은 [profile](../../configs/chat_prompts/saju_s3_p1_v1.txt)과 [formatter 안내](../../configs/chat_prompts/saju_s3_p1_runtime_v1.txt)의 **묶음 하나**다.
- 모든 48개에서 P0 최종 입력이 부모 S2 byte/token과 같고 P1의 사실 JSON·이력·현재 질문은 같다. 비연결 intake는 P0/P1이 동일하다. 날짜 질문의 원국·날짜 각각 한 사실 요구도 유지한다. 지시문 길이와 우선순위 모두 바뀌므로 개별 문장 효과라고 하지 않는다.
- source·모델 파일·tokenizer/template·생성 설정·환경·원시 입력·부모 이력·묶음 hash를 결합한 build를 사용한다. 최종 사전 dry-run은 `build-ffd985905b51`, 최대 입력 1,692 token·96요청·10사전 차단이다. 아직 raw build나 모델 출력은 생성하지 않았다.
- 전역 GPU lock·VRAM 12GiB·compute process 없음·native JIT/header 검사를 통과해야 worker를 연다. 요청별 300초, 이미 시작된 요청 재시도 없음, 응답 없는 시작 marker는 미결로 중단한다. 같은 build의 검증된 완료만 재사용하며 다른 build ID로 예산을 초기화할 수 없다. 원시 파일 0600·폴더 0700·공개 파일 allowlist를 검사한다.
- 공개 집계는 arm/stratum별 PASS·FAIL·UNSCORABLE, 대응쌍 변화, 토큰·종료·시간·VRAM을 분리한다. 시간은 model load를 포함하며 P0→P1 순차 측정이다. 순수 kernel 성능이나 운영 KI20 대비 개선으로 해석하지 않는다.
- 계약·plan·dry-run 및 S3 CPU 18개 통과. 초기 합성 resume fixture의 누락 필드와 binding 예외 타입 기대를 고친 뒤 재검증했다. 실제 생성 오류를 숨기거나 scoring 조건을 완화한 것은 아니다. GPU 사전 조회는 RTX 5070 Ti, free 15,119MiB, compute process 없음이었고 기존 local Python header로 native JIT 검사가 통과했다. 실제 시작 시 다시 확인한다.

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

전체 unittest **995개/96.957초, 실패·오류·건너뜀 0**, 문서 33개·Ruff·diff 검증을 통과해 harness/P1을 먼저 체크포인트로 저장한다. S3 결과에 따른 Phase 9 이후 실행·학습·운영 전환은 이번 범위가 아니다.

## 진행 기록

- 2026-09-16: 8A `c1f090f`를 `origin/master`에 푸시했다. S3 사전 구현과 18개 CPU·전체 995개 검증을 완료했다. 요청 예산 소모는 아직 0이며 다음 단계에서 고정 build만 실행한다.

- 2026-09-16: 8A CPU 후보와 공개 검증을 완료했다. 운영 서비스 PID 3144071·재시작 0·active와 부모 코드/원문 무변경을 재확인했다. 8B는 아직 실제 생성 0개이며 완료로 표시하지 않는다. 검증된 8A를 한국어 체크포인트로 저장하고 8B를 이어간다.
