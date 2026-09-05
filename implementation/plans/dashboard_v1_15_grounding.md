<!-- dashboard_v1_15_grounding.md - 공통 tokenizer·날짜 사전 차단·사실 검사 후보와 대화 재진단 실행 정본이다. -->

# Dashboard v1.15 연결 대화 진단 후보

## 범위와 실행 순서

기준은 `9270a46`의 실제 대화 진단이며, 기존 v1.14 서비스와 기본 선택 `ki20_final`은 유지한다. v1.15는 별도 명령·loopback 후보이며 Runtime feature 기본값은 off다. Phase 6, release, 모델 승격, 추가 학습, 봉인 데이터 상태는 변경하지 않는다.

1. `scripts/training/dashboard_tokenizer_v1.py`: 원본 K0 tokenizer 파일·effective backend SHA와 chat template 검증. 세 모델 모두 `fix_mistral_regex=False`. 생성 직전 렌더링 SHA·최종 token ID SHA·입력 길이를 기록한다.
2. `scripts/training/dashboard_grounding_v2.py`: 공백 경계 intent, 서버 KST 날짜와 연결 일진 대조, 실제 `period.hard_facts.period`의 역할별 간지 검사. 날짜 비교는 지원 범위 확인일 뿐 자유문장 계산·재결합이 아니다.
3. `scripts/training/phase5_dashboard_v1_15.py`와 `phase5-dashboard-v1.15.0-grounding-candidate.json`: 기존 버전을 수정하지 않는 독립 진단 진입점. 현재 새 세션은 `dashboard/v1.15.0/grounding-v2.0.1/manual_sessions`, 연결 schema `1.7.0`, 비연결 schema `1.8.0`으로 저장한다. 초기 `grounding-v2.0.0` 진단 세션은 기존 `dashboard/v1.15.0/manual_sessions`에 불변 보존하며 현재 후보에서 수정하지 않는다.
4. 관련 CPU 회귀 테스트 후 동일 합성 10질문을 K0·R16·KI20으로 재생한다. 30개 모델 요청 중 27개 생성과 내일·주간 후속 3개 사전 차단을 기대한다. 서버 시계는 진단 harness에서만 2026-09-05로 고정하며 HTTP client의 날짜 override 필드는 추가하지 않는다.
5. 별도 현재 KST 날짜 HTTP canary에서 합성 원국·일진 계산 및 연결을 검증한다. 원출력은 Git 제외 경로에만 보존하고 공개 집계·build manifest와 진행 기록을 남긴다.

## 계약과 완료 기준

- 서로 다른 날짜는 `RUNTIME_DATE_REBIND_REQUIRED`, 여러 날·주·월·연 범위는 `RUNTIME_PERIOD_SCOPE_UNSUPPORTED`, 불명확한 날짜는 `RUNTIME_DATE_SELECTION_REQUIRED`로 GPU 호출 전에 닫는다. 새 날짜 선택→계산→새 연결 대화를 안내한다.
- “내 일간”은 원국 질문이다. 일반 대화·문장 작성·쉬운 풀이에는 날짜·네 기둥을 강제하지 않는다. 전체 기둥은 명시적인 전체 나열 요청에서만 요구한다.
- 값 존재와 원국/기간/연/월/일 역할을 구분한다. 한자·한국어 독음·명시적 부정과 교정에 대한 회귀 사례를 둔다. 유한 문법의 자동 진단이므로 자연스러움·해석 의미 품질은 `not_measured`이며 통과율을 모델 정확도로 부르지 않는다.
- 지원 범위의 모델 원출력은 경고 여부와 무관하게 보존하고 재작성·retry하지 않는다. 첫 turn 입력 identity는 세 모델 동일해야 한다. 후속 turn은 각 모델 자신의 이전 답변을 사용한다.
- `--artifact-root`는 기존 모델·run을 복사하지 않고 검증해 읽기 위한 명시적 입력 root다. 새 v1.15 세션만 새 하위 경로에 기록한다. 기존 모델·학습·대화 파일은 변경하지 않는다.
- R8/R32 확대 비교, v1.1 보정 잔여 162건, 학습, production 전환, service 교체는 이번 범위 밖이다.

## 진행 기록

### 2026-09-05 — 구현·CPU 검증 진행

- 분리 worktree에서 공통 tokenizer, 날짜 사전 차단, 역할별 사실 검사, 독립 v1.15 코드·자산·config를 구현했다.
- 설치된 환경에서 원본 tokenizer backend SHA `4f1fb83a437cc9c2f262ef579cfd635d355776a5eaf0fffafd9bf2b03487507f`와 effective chat template 일치를 확인했다. 기존 artifact root로 후보 context와 release 메타데이터 준비가 성공했다.
- 초기 회귀에서 인용부호 안의 잘못된 간지를 놓치는 사례를 찾아 교정했다. 최종 테스트·GPU 재진단 결과는 아래에 후속 기록한다.
- 관련 회귀·HTTP 사전 차단·tokenizer 변조·기존 v1.14·LoRA·문서 정책 41건 통과. 전체 scripts/tests Ruff와 JavaScript 구문 검사를 수행한다.
- 전체 `.venv-data/bin/python -m unittest discover -s tests -q` 첫 실행은 795건 중 실패 4·오류 37·skip 37이었다. 수정 전 `9270a46`의 별도 clean worktree에서 782건을 재실행해 같은 실패 4·오류 37·skip 37을 재현했다. 비공개 artifact 미배치, Torch 없는 data 환경, 기존 LoRA core hash 및 recovery target 계약이 원인이다. 기존 문제를 이번 후보 회귀로 오인하거나 전체 green으로 기록하지 않는다.
- 현재 Python 3.10 개발 헤더가 없어 공식 Ubuntu `libpython3.10-dev`의 지정 버전과 SHA-256을 검증하고 Git 제외 진단 경로에 추출했다. 시스템 패키지·Torch는 변경하지 않았으며 native JIT는 활성 상태를 유지한다.

### 2026-09-05 — 합성 재생기 준비

- `scripts/evaluation/dashboard_v115_replay.py`는 기존 합성 suite·snapshot의 고정 SHA를 검사한다. 기본 실행은 무기록 dry-run이다. 실제 실행은 `DASHBOARD_V115_DIAGNOSTIC=SYNTHETIC_30_V1`과 `--execute`를 함께 요구한다.
- 재개 시 코드·입력 fingerprint와 개별 응답 SHA를 검사한다. 시작 기록만 있고 완료 응답이 없는 요청은 자동 재실행하지 않는다. 집계·manifest·응답을 덮어쓰지 않는다.
- 재생기 회귀 2건으로 30요청→27생성+3차단, 재개 시 추가 생성 0회, 응답 변조·다른 fingerprint·허용 경로 밖 입력 거부를 확인했다. 실제 입력 dry-run과 전체 Ruff도 통과했다.

### 2026-09-05 — GPU 초기 재진단 후 추가 교정

- 초기 실제 GPU 재생은 30요청·27생성·3사전 차단, 첫 turn 6개 그룹의 세 모델 token identity 일치, 제외 turn 0으로 완료했다.
- 원출력 점검에서 KI20의 한국어 한 글자 천간을 일주로 부르는 주장을 검사기가 놓친 사례를 발견했다. 한 글자 천간의 역할 검사, 지시문 재출력 경고, 일반적인 감정 대화 분류 회귀를 추가했다.
- scorer를 `saju-bound-chart-grounding-v2.0.1`로 올리고 세션 경로도 분리했다. 초기 원출력·집계·manifest는 덮어쓰지 않고, 동일 입력을 별도 새 build에서 다시 생성·검증한다. 초기 집계는 최종 품질 지표로 사용하지 않는다.

### 2026-09-05 — HTTP canary·진단 격리 보강

- 별도 loopback HTTP에서 현재 KST 날짜의 실제 원국·일진 연결과 R16 생성 1회, 다음 날짜·주간 범위 차단 2회를 확인했다. 화면 메타데이터 6개 경로도 정상 응답했다. 임시 서버를 종료하고 합성 Runtime session만 삭제했다. 기존 v1.14 서비스는 변경하지 않았다.
- 첫 HTTP 준비는 세션 생성 `201 Created`를 진단 코드가 `200`으로만 기대해 중단됐다. 기대 status와 실패 경로 cleanup을 교정했으며 해당 시도는 GPU 생성 0회다.
- 전체 테스트에서 진단 코드의 process `umask`가 뒤쪽 공개 artifact 권한 검사에 영향을 줄 수 있음을 확인했다. 성공·예외 모두 원래 `umask`를 복구하도록 보강했다. 개인정보 가능 응답 파일은 계속 0600을 유지한다.
- 위 보강도 별도 build에서 마지막 재생·HTTP 검증으로 확인한다. 이전 GPU·HTTP 산출물은 보존하고 최종 공개 집계에서 실행 차수를 구분한다.

### 2026-09-05 — 최종 검증 완료

- 실행 코드 `b0a6189`에서 최종 30요청·27생성·3사전 차단, 첫 turn 6개 그룹의 세 모델 token identity 일치를 확인했다. 실제 현재 날짜 HTTP에서는 R16 1회 생성·후속 2회 사전 차단을 완료하고 임시 server를 종료했다.
- 연결 응답 구조 검사: K0 1/4, R16 4/4, KI20 0/4. 설명·입력 이해·문장 작성 품질 승인을 뜻하지 않는다. 자연스러움·의미 품질은 `not_measured`, 모든 기존 승인·학습 상태는 유지한다.
- 재개 시 추가 생성 0회·private manifest 불변, 최종 원출력 27개가 앞선 두 재생과 byte 일치, 실제 HTTP R16 입력·출력이 고정 재생과 일치함을 검증했다.
- 관련 47건·Ruff·JavaScript 구문·공백 검사가 통과했다. 최종 전체 805건은 기존 실패 4·오류 37·skip 37이며, 이번 변경으로 추가된 실패는 없다. 전체 green으로 기록하지 않는다.
- 공개 build는 `data/reports/saju_1b_baseline/dashboard-v115-realistic-chat/v1.0.0/build-9e7b7d39475b`다. 자세한 해석·실행 차수·한계는 [완료 기록](../history/2026-09-05-dashboard-v115-grounding.md)에 정리했다. 원출력은 Git 제외 `runs/REALISTIC-CHAT`에만 보존했다.
- 계획의 구현·진단 범위는 완료했다. 기존 서비스 전환·브랜치 병합·추가 학습·모델 승격은 수행하지 않았다.

### 2026-09-05 — 후속 대화 예시 20문장 기록

- 사용자와 대화에서 확정한 합성 질문 20개를 최상단 [대화 테스트 예시](../../SAJU_CHAT_TEST_PROMPTS.md)에 기록했다. 원국 연결, 오늘 일진 연결, 비연결 입력, 일반 대화·문장 작성의 네 그룹과 후속·분기 순서를 보존했다.
- 이번 범위는 문서 저장뿐이다. 이 20문장으로 모델 생성·프롬프트 비교를 실행하지 않았으며 기존 진단 결과와 구분한다.
- 공유 작업 폴더에는 동일 문서만 추가하고, 기존 전용 worktree와 브랜치에서 문서·진행 기록을 커밋한다. 공유 브랜치·index, 기존 ZIP, 대시보드 서비스와 다른 세션 변경은 건드리지 않는다.
- 검증: 20문장 원문·1~20 연속 번호·네 그룹·연속 대화 3개·분기 2개의 일치, 두 위치 파일 byte 일치와 문서 링크 2개를 확인했다. `.venv-data/bin/python -m unittest tests.test_saju_product_roadmap tests.test_phase6_technical.Phase6TechnicalTests.test_canonical_docs_forbid_person_dependent_gates -q` 5건과 `git diff --check`가 통과했다. Python은 공유 프로젝트의 절대 경로로 실행했다.
