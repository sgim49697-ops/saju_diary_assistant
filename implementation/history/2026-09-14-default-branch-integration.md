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
- 운영 v1.14는 PID 3144071·재시작 0 상태를 유지한 채 통합을 진행했다.

### 2026-09-14 — PR 병합·원본 폴더 통합·안전 정리 완료

- 정책 체크포인트 `f9173e5`를 포함한 [PR #28](https://github.com/sgim49697-ops/saju_diary_assistant/pull/28)을 정확한 head SHA로 병합했다. 병합 커밋은 `3bb0ce2affa50e395ef21b473f1e27c5ff5fdb38`이며 원본 `/home/user/projects/saju_diary_assistant`를 `master`로 전환하고 `origin/master`에 fast-forward했다. 새 브랜치·worktree는 만들지 않았다.
- 기존 질문 파일은 이동 백업 후 동일 SHA의 tracked 파일로 수용했다. 압축파일의 크기·수정 시각은 그대로이며 기존 `.gitignore` 규칙으로 제외된다. 서비스·모델·출생정보·미완료 데이터는 이동하거나 Git에 추가하지 않았다.
- 삭제 직전 ref SHA·병합/patch 동등성·백업 포함 여부와 worktree의 HEAD·미저장 변경·ignored 자료·프로세스 cwd를 다시 확인했다. 자료 없이 코드와 재생성 가능한 캐시만 있던 worktree 11개, 로컬 브랜치 24개, 원격 브랜치 19개를 정리했다. 원격 삭제는 SHA 재확인 후 atomic push로 실행했고 force push는 쓰지 않았다.
- 최종 상태는 로컬 브랜치 8개·원격 브랜치 6개·worktree 8개다. 위 보존 대상 7개는 HEAD·미저장 변경 fingerprint·자료 목록 수가 동일하며, 실행 서비스의 PID 3144071·재시작 0·기존 작업 경로도 그대로다. 남은 브랜치는 새 작업용 분기가 아니라 자료/서비스 보존용이다.
- 복구용 bundle 두 개와 삭제 전 ref/worktree 목록·정리 audit·설정 원본은 Git 밖의 `/home/user/.local/state/saju-workspace-backups/2026-09-14-integration.MXh6UA`에 보관했다. 디렉터리는 0700, 백업·로그는 0600이며 병합 후 bundle의 완전한 이력도 재검증했다. 삭제한 브랜치/추적 파일은 이 bundle에서 복구할 수 있고 캐시는 재생성한다.
- 최상위 안내·로드맵·기준선·v1.15/컨텍스트 계획과 정합성 테스트를 병합 완료·운영 미배포 상태로 갱신했다. 과거 승인 산출물과 진행 기록의 당시 상태는 덮어쓰지 않았다.

### 2026-09-14 — 병합 후 원본 폴더 재검증

- 기본 브랜치/문서/자동 평가 경계, v1.14/v1.15, tokenizer·grounding·20문장·HTTP canary·replay 표적 unittest **58건 전부 통과**. `uvx ruff check scripts tests`, `node --check scripts/training/phase5_dashboard_assets/v1.15.0/dashboard.js`, `git diff --check`도 통과했다.
- `umask 022; .venv-data/bin/python -B -m unittest discover -s tests -v`는 **841건 실행, 실패 5·오류 17**이다. 원본 폴더의 로컬 artifact 조건이 통합 전 격리 환경과 달라 실행 수·오류 수를 단순 비교하지 않는다. 전체 성공이 아니다.
- 잔여 22개 실패/오류 기록은 공유 LoRA core hash 계약 13개, 기존 teacher recovery 대상/fixture 5개, 실제 서버 날짜와 2026-09-02 고정 fixture의 충돌 4개다. 해당 코드·테스트는 통합 전 head `f9173e5`와 동일하며 이번 정책/문서 변경 대상이 아니다. 불변 학습 계약이나 운영 날짜 차단을 통합 작업의 편의로 수정하지 않았다.
- 처음 실행한 `umask 077` 환경에서는 공개 aggregate 파일 권한을 검사하는 fixture 오류가 1개 더 있었다. 백업 파일 권한과 테스트 프로세스의 umask를 분리해 위 정식 환경으로 재실행했고 이 환경성 오류는 재현되지 않았다. 첫 로그도 성공으로 바꾸지 않고 보존했다.
- 전역 지침 네 파일의 byte 일치·TOML 세 파일 파싱·설정 구조 비교·지침 길이 검증을 다시 통과했다. 현재 Git 작업 폴더와 운영 서비스 경로를 구분하며 새 전역 지침은 새 세션/실행에서 읽힌다.
- 후속 작업은 별도 S0/S1 점검에서 잔여 계약/fixture 오류부터 다루는 것이다. S0~S6 실험·실제 GPU 생성·학습·서비스 재시작·v1.15 배포·Phase 6/Runtime release/모델 승격·sealed blind 열기/재사용은 실행하지 않았다.
