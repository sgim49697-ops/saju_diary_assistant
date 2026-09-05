<!-- dashboard_v1_15_grounding.md - 공통 tokenizer·날짜 사전 차단·사실 검사 후보와 대화 재진단 실행 정본이다. -->

# Dashboard v1.15 연결 대화 진단 후보

## 범위와 실행 순서

기준은 `9270a46`의 실제 대화 진단이며, 기존 v1.14 서비스와 기본 선택 `ki20_final`은 유지한다. v1.15는 별도 명령·loopback 후보이며 Runtime feature 기본값은 off다. Phase 6, release, 모델 승격, 추가 학습, 봉인 데이터 상태는 변경하지 않는다.

1. `scripts/training/dashboard_tokenizer_v1.py`: 원본 K0 tokenizer 파일·effective backend SHA와 chat template 검증. 세 모델 모두 `fix_mistral_regex=False`. 생성 직전 렌더링 SHA·최종 token ID SHA·입력 길이를 기록한다.
2. `scripts/training/dashboard_grounding_v2.py`: 공백 경계 intent, 서버 KST 날짜와 연결 일진 대조, 실제 `period.hard_facts.period`의 역할별 간지 검사. 날짜 비교는 지원 범위 확인일 뿐 자유문장 계산·재결합이 아니다.
3. `scripts/training/phase5_dashboard_v1_15.py`와 `phase5-dashboard-v1.15.0-grounding-candidate.json`: 기존 버전을 수정하지 않는 독립 진단 진입점. 새 세션은 `dashboard/v1.15.0/manual_sessions`, 연결 schema `1.7.0`, 비연결 schema `1.8.0`으로 저장한다. 후보에서 구버전 세션을 수정하지 않는다.
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
