<!-- 2026-09-14-default-branch-integration.md - 기본 브랜치 통합·분기 정책 교정·안전한 정리의 근거와 검증을 기록한다. -->

# 기본 브랜치 통합과 작업 규칙 정리

## 요청과 경계

- 개인 프로젝트는 원본 폴더의 기본 브랜치에서 작업하고 요청하지 않은 브랜치·worktree를 만들지 않는다. 이 저장소의 기본 브랜치 이름은 `master`로 유지한다.
- 사용자는 운영 서비스와 데이터 보존을 선택했다. 대시보드 재시작·경로 이관·추가 모델 실행·학습·Phase 6·Runtime release·모델 승격은 이번 범위가 아니다.
- 시작 시점은 원격 `master=b78f8e6`, 원본 폴더 `8011b05`, 통합할 기존 브랜치 `codex/system-context-audit-plan=56b0ecb`다. 최신 미반영 작업은 이 브랜치에 연결된 11개 커밋이며 별도 통합 브랜치를 만들지 않았다.

## 전역·프로젝트 규칙

- Linux Codex·Windows Codex·Claude·현재 별도 `CODEX_HOME`의 전역 규칙 네 곳을 동일하게 갱신했다. 기본 브랜치 직접 작업, 새 분기 사전 확인, 동시 작업 충돌 시 대기, 요청된 병합의 동기화·정리까지 명시했다.
- 저장소 `AGENTS.md`의 자동 worktree 생성 문구를 교체하고 회귀 테스트 4건을 추가했다. 프로필의 다른 규칙은 유지했다.
- 전역·프로젝트 지침 합계가 구분자 포함 33,620 bytes이므로 Codex 설정 세 곳의 `project_doc_max_bytes`만 65536으로 올렸다. TOML 파싱과 이전 설정의 구조 비교에서 다른 설정 변경은 0이었다.
- 전역 규칙 네 파일의 SHA-256은 `c65ddb50736ecbed8a846a6c105f456a58ae937a80df039e7d8d73439f5c1203`으로 같다. 전역 파일·비밀값·실행 로그 자체를 Git에 넣지 않는다.
- 실제 적용 경로와 길이 한도는 [OpenAI Docs](https://learn.chatgpt.com/docs/agent-configuration/agents-md)에 따라 확인했다. 기존에 열린 세션의 지침이 즉시 재로딩됐다고 주장하지 않는다.

## 브랜치와 보존 대상

- 시작 상태는 로컬 브랜치 32개·원격 브랜치 25개·worktree 19개다. 정리 전 비공개 복구용 Git bundle과 ref 목록·설정 원본·질문 파일을 보관했고 bundle의 완전한 이력을 검증했다.
- 과거 squash 병합은 SHA ancestry만으로 미반영이라고 판단하지 않았다. Phase 5 split·조건부 학습·대화 통합 브랜치는 해당 PR 병합 커밋과 tree가 같고, 초기 학습 대시보드는 반영된 변경과 patch ID가 같다. `8011b05`와 runtime audit 추가 커밋도 patch 동등성을 확인했다.
- 보존 대상은 `codex/mix20k-v3-repair`, `codex/mix2k-v4-reviewed-repair-v1-1`, `codex/period-roadmap-v1`, `codex/model-training-presentation`, `codex/r16-dashboard-v1-14`, `codex/runtime-grounded-day-v1-11`, `codex/runtime-r4-r7`의 worktree다. 각각 고유/미완료 자료·미커밋 발표자료·실행 서비스·원천/환경 자료를 보유한다.
- 원본 폴더의 미추적 압축파일은 그대로 보존한다. 20문장 파일은 기존 untracked 파일과 통합할 tracked 파일의 SHA 일치를 확인한 뒤 복구 사본을 남기고 수용한다. 모델·raw/run·미완료 보정 state를 정리 목적으로 삭제하지 않는다.

## 진행 기록

### 2026-09-14 — 통합 전 검증

- 기본 브랜치 정책·문서·자동 평가 경계 24건, v1.14/v1.15·tokenizer·grounding·20문장/replay의 CPU 표적 검사 포함 58건을 통과했다. 검사 중 생성 표시는 mock fixture 실행이며 실제 GPU 생성이 아니다.
- `uvx ruff check scripts tests`, v1.15 JavaScript의 `node --check`, `git diff --check`를 통과했다. 전체 unittest는 `umask 022`와 기존 `.venv-data` interpreter로 827건 실행했다.
- 전체 결과는 실패 4·오류 37·건너뜀 37이다. 이전 823건과 실패/오류 이름 41개가 동일하고 새 정책 검사 4건의 신규 실패는 없다. 고정 로컬 snapshot/의존성 부재·기존 hash 계약/fixture 문제를 숨기거나 전체 성공으로 기록하지 않는다.
- PR 병합·원본 폴더 전환·삭제 수와 최종 검증 결과는 실제 실행 후 아래에 추가한다. 운영 v1.14는 PID 3144071·재시작 0 상태를 유지한다.
