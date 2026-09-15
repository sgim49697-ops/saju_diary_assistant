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
## Phase 8B: 다음 실행

S3 harness·P1을 먼저 동결하고 계약·CPU·dry-run을 검증한다. 동결된 기존 48개/부모 이력에서 P0·P1 각각 새로 실행한다. 96요청 중 기존 v1.15 guard에 따른 10사전 차단을 포함해 새 생성은 최대 86개다. 8A 라우팅을 S3에 섞지 않으며 새 scorer v1.1을 양쪽에 동일 적용한다. 본 비교의 최장 적격 pair부터 실행하고 적격성 잔여 2요청은 사용하지 않는다. 결과 확인 후 같은 질문으로 P2/P3를 추가하지 않는다.

## 진행 기록

- 2026-09-16: 8A CPU 후보와 공개 검증을 완료했다. 운영 서비스 PID 3144071·재시작 0·active와 부모 코드/원문 무변경을 재확인했다. 8B는 아직 실제 생성 0개이며 완료로 표시하지 않는다. 검증된 8A를 한국어 체크포인트로 저장하고 8B를 이어간다.
