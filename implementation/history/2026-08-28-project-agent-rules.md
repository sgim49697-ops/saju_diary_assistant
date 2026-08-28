<!-- 2026-08-28-project-agent-rules.md - 프로젝트 에이전트 협업·Git 체크포인트 규칙 도입 기록 -->

# 프로젝트 에이전트 규칙 도입 진행 기록

## 진행 기록

- 날짜: 2026-08-28
- 작업 요약: 다른 AI 세션과 working tree를 공유할 때 변경 소유권을 먼저 판별하고, 검증된 중요 구현 지점에서 자동으로 분리 커밋·push하도록 저장소 루트 규칙을 추가했다.
- 변경 범위: `AGENTS.md`에 정본·Phase Gate, 데이터·비밀값 안전, unstaged·staged·unpushed 상태 처리, 별도 worktree 격리, 검증, 진행 기록, 한국어 커밋과 자동 push 기준을 명문화했다.
- 세션 조정: 동일 저장소의 다른 Codex 세션이 Phase 0~2 재검수 작업을 수정·검증 중임을 process tree와 Git 변경 시각으로 확인했다. 해당 working tree와 index는 건드리지 않고 임시 worktree 및 전용 브랜치에서 이 변경을 작성했다.
- 검증 명령/결과:
  - `git diff --check`: 공백 오류 없이 통과했다.
  - `git diff --cached --name-status` 및 `git diff --cached --check`: 이 기록과 `AGENTS.md` 두 파일만 포함됨을 확인했고 공백 오류 없이 통과했다.
- 남은 이슈/후속 작업: 코드 동작 변경은 없다. 실제 운영에서 새 충돌 유형이 확인되면 동일한 안전·소유권 원칙 안에서 규칙을 구체화한다.
