<!-- 2026-09-16-phase10-product-candidate.md - R16 제품 후보의 CPU 구현·검증과 미실행 경계를 기록한다. -->

# Phase 10 R16 최소 제품 후보

<a id="phase10"></a>
## 실행 범위와 결과

2026-09-16 사용자 계획 승인에 따라 원본 `master`에서 v1.18 후보 하나를 구현했다. 시작 부모는 `1ef7f9d`; 병행 세션의 `536394f`·`d3ba850`과 커밋 직전 확인한 `5608db9` 저장소 정책 커밋은 그대로 보존했다. 마지막 변경은 `.gitignore`·`AGENTS.md`·별도 진행 기록뿐이며 구현 경로와 겹치지 않음을 확인했다. 새 브랜치·worktree를 만들지 않았고 사용자 ZIP을 수정·추적하지 않았다.

**Phase 10 구현·CPU/합성 화면 검증 완료, 실제 모델 품질·운영 채택은 미측정/미실행**이다. 모델은 사용자 선택인 R16 단독이다. 운영 v1.14의 기본 KI20이나 S4 3B와 개선 비교를 했다는 뜻이 아니다. 지시문·정보 선택·이력·응답 계약을 함께 바꾼 통합 후보이므로 단일 변경의 품질 효과도 주장하지 않는다.

## 구현 변경

| 위치 | 변경·보호 경계 |
|---|---|
| `phase5_dashboard_v1_18.py`·전용 config/assets | 부모 v1.17 보존, 포트 8770·독립 세션·R16 고정·feature 기본 off·고정 20문장 실행 거부 |
| `dashboard_product_policy_v1.py`·`saju_product_v1.txt` | 직접 조회/모델 생성/확인/차단 구분, 요청별 facts·이력 선택, 일반 요청에 사주 입력 강제하지 않음 |
| `dashboard_product_session_v1.py` | schema 1.9.0, 승인 snapshot 보존, 출처·입력 hash, 모델 원문/경고 분리, CAS·0600 잠금·원자 저장 |
| `product_dashboard_binding_v1.py` | 기존 암호화 상태에서 chart-only와 exact chart+day 분리, 최종 commit 중 revision 재검증 |
| `dashboard_product_canary.py`·합성 UI 검사 | CPU·loopback HTTP·새 CLI process·2개 viewport의 자동 검사, 공개 집계와 source hash |

직접 답하는 필드는 일간·연주·월주·일주·시주·연결 일진·선택 날짜다. 미상 값은 확정하지 않으며 두 필드 비교·설명은 생성 경로다. 단순 조회의 틀린 일간 전제는 승인 필드로 교정한다. 출생정보 자유문장·정정은 구조화 입력으로 안내하고 모델이 새 원국을 추측하지 않는다.

일반 대화에는 중립 지시문과 관련 일반 turn만 보내되 연결 상태는 유지한다. 사주 설명으로 복귀하면 현재 binding의 승인 facts를 사용한다. 날짜만 있는 후속은 이전 사주/일반/맥락 없음의 세 조건을 구분한다. 미지원 계산과 명확히 구분된 일반 요청에는 제한 안내를 별도로 붙인다. 이 규칙의 CPU 통과를 자유문장 전체 이해의 증명으로 확대하지 않는다.

정정·날짜 변경은 새 대화로 연결한다. 이전 대화의 원문·snapshot은 덮어쓰지 않고 다른 세션 이력도 자동 이전하지 않는다. 재계산 실패가 기존 runtime session을 삭제하지 않게 했다. partial/unknown은 chart-only만 허용하고 exact day 계약은 완화하지 않는다. worker 완료 후 원본 revision이 바뀌면 초안을 폐기하고 409로 응답한다.

생성 원출력·API·저장·화면은 같게 유지한다. 자동 검사에서 틀린 사실을 찾아도 정답으로 재작성하거나 다시 생성하지 않고 경고로 분리한다. 직접 조회/확인/차단에는 R16 이름·가짜 token/cost를 붙이지 않는다. 공개 집계의 `model_generated` 수는 **CPU 분기 검사 수**이며 실제 모델 생성 수가 아니다.

## 검증과 디버깅

| 검증 | 결과 |
|---|---|
| `.venv-data/bin/python -B -m unittest tests.test_dashboard_product_v1 tests.test_dashboard_product_http tests.test_dashboard_product_canary -q -b` | 46개 통과; 모의 생성·합성 binding, 실제 GPU 0 |
| `dashboard_product_canary validate-contract`·`plan`·`execute`·`verify --build build-f13715ee1d91` | 계약·dry-run·발행·재계산 검증 모두 통과 |
| `dashboard_product_ui_canary.cjs` | desktop/mobile 8개씩 16개 통과; 실제 운영 service·외부 네트워크 접근 없음 |
| 부모 `dashboard_intent_canary_v2 verify --build build-49b9aed70565` | 45개 CPU·6개 합성 화면, 기존 aggregate/manifest hash 유지 |
| `system_context_s3 verify --build build-ffd985905b51` | 기존 96요청 재구성 검증; 새 생성 0 |
| `system_context_s4 verify --build build-1f851d69a91f` | 기존 192요청 재구성 검증; 새 생성 0 |
| `.venv-data/bin/python -B -m unittest tests.test_saju_phase_plans tests.test_saju_product_roadmap -q -b` | 40개 통과; 원문 해시·588행·정책·절/링크·완료 증거 검증 |
| `.venv/bin/python -B -m unittest discover -s tests -v` | 최종 전체 **1,121개 통과**, 실패·오류·건너뜀 0, 143.812초 |
| `uvx ruff check scripts tests`·`node --check`·`git diff --check` | 모두 통과; 후보 화면/검사 JS와 최종 staged diff 포함 |

문서·관련 CPU 검사는 `.venv-data`, ML 의존성을 포함한 전체 회귀는 기존 `.venv`에서 수행한다. 후자는 `CUDA_VISIBLE_DEVICES=''`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`로 실행했다. 새 패키지·모델은 설치하지 않았다. Playwright와 Chromium은 기존 로컬 설치를 명시하는 `SAJU_PLAYWRIGHT_MODULE`·`SAJU_CHROMIUM_EXECUTABLE`을 사용했고 MCP·운영 서버를 새로 켜지 않았다.

처음 HTTP 회귀의 1오류는 실제 CLI 성공 뒤 fixture의 미해결 engine catalog로 빈 세션 목록을 조회한 테스트 문제였다. 초안 실행에서 저장 디렉터리가 생성되지 않음을 직접 확인하도록 고쳤다. 합성 화면 초기 2회 실패는 빈 세션 목록 모의 응답·독립 사례의 미복원 화면 상태 때문이었다. 실제 목록/상세 fixture와 성공 상태를 복원해 재검증했으며 강제 클릭·assertion 제거로 통과시키지 않았다.

추가 점검에서 v1.18 인증 시작 allowlist와 별도 CLI 포트, 안전한 내부 오류 JSON을 보완했다. 직접 조회의 한 글자 일간·유효 ISO 날짜 검사와 잘못된 값 거부도 추가했다. 보강 전 초도 CPU 집계 `build-a14b46f96958`은 Git에 넣지 않고 임시 검증 디렉터리로 이동해 보존했으며 덮어쓰지 않았다. 최종 source는 아래 새 build로 고정한다.

## 공개 증거

- [aggregate](../../data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91/aggregate.json): `7219b9ca1026734bbd445962ce46533068c7926784a6b597cc42caa1290cd7c1`
- [build manifest](../../data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91/build_manifest.json): `8d7a0fae54c9af5cd4d9dc55a20364abf11d14797b3b4a83ff899c374223dbe3`
- [verification](../../data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91/verification.json): 재계산과 두 파일 hash 대조용.

46개 회귀·16개 합성 화면과 별도 정책 분기 12건(직접 3/모델 경로 4/확인 3/차단 2)만 집계했다. 생성 원문·출생 원문·내부 runtime ID·키·모델·checkpoint는 공개하지 않았다. 원문 archive 3개와 588행의 문서 반영 상태는 유지한다. 실행 과제 `O-0144`, `O-0174`, `O-0213`~`O-0220`에만 실제 완료 근거를 연결하고 조건부 제안·예시를 새 완료 과제로 바꾸지 않는다.

## 운영 보존과 다음 단계

- 운영 service는 `saju-mix2k-r16-dashboard-v1-14.service`, PID `3144071`, active/running·재시작 0을 재확인했다. 운영 코드 `0e77621`·기본 KI20·기존 세션은 유지한다.
- 실제 모델 호출 추가 0, 누적 **631/680**, 잔여 **49 = S6 48 + 공유 여유 1**이다. CPU 모의 호출을 실제 요청 예산으로 세지 않는다. config의 호출 미승인 표시는 이번 Phase 10 실행 범위이며 미래 실행 권한을 부여하지 않는다.
- 다운로드·GPU 생성·데이터 보정/학습·운영 전환·Runtime release·Phase 6/승격 변경·sealed blind 접근은 없다. 새 Phase 12 24문항은 작성·소비하지 않았다.
- 다음은 [Phase 11](../plans/saju_product_roadmap/phases/phase-11.md)의 기존 진단/데이터 대조·학습 가설·조건부 명세다. 실제 R16 통합 입력·생성·cold/warm 비용·모델 원문→API→저장→실제 화면의 확인은 [Phase 12](../plans/saju_product_roadmap/phases/phase-12.md)에 남긴다. 이 CPU 결과로 자연스러움·의미 품질 `not_measured`를 바꾸거나 자동 배포하지 않는다.
