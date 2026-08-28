<!-- AGENTS.md - 사주 일기 도우미 저장소에서 AI 에이전트가 안전하게 구현·검증·커밋·푸시하기 위한 프로젝트 규칙 -->

# 프로젝트 에이전트 작업 규칙

이 파일은 저장소 전체에 적용한다. 시스템·사용자·전역 `AGENTS.md` 지시가 이 파일보다 우선하며, 이 파일은 저장소에 필요한 협업·데이터 안전·Git 체크포인트 규칙을 보강한다.

## 프로젝트 정본과 기본 원칙

- 실행 정본은 `implementation/plans/saju_1b_10k_20k_baseline/README.md`와 그 문서가 연결한 Phase 문서다. 조사용 HTML이나 과거 archive와 충돌하면 현재 정본을 따른다.
- Phase 상태와 Gate를 임의로 앞당기지 않는다. 특히 `training_promotion_allowed=true`는 대응 검증 산출물과 정본의 조건이 실제로 충족된 경우에만 기록한다.
- 승인된 source, audit, preprocessing build와 report는 불변 산출물이다. 입력·정책·코드 fingerprint가 바뀌면 기존 경로를 덮어쓰지 말고 새 version 또는 build ID를 만든다.
- `data/raw`, `data/audit`, `data/staging`, 모델, run, checkpoint 같은 Git 제외 원본·파생물은 명시적 요청과 검증된 공개 범위 없이는 추적하지 않는다.
- AI Hub 제한 데이터, 개인정보 가능 원문, 토큰·키·로컬 설정값을 로그·테스트 fixture·문서·커밋에 노출하지 않는다. 비밀값은 환경변수나 Git 제외 로컬 설정에서만 읽는다.
- Python 환경과 패키지 관리는 전역 규칙대로 `uv`를 사용한다. 프로젝트 가상환경 실행이 필요하면 우선 `.venv-data/bin/python`을 사용한다.

## 작업 시작 시 Git·세션 점검

파일을 수정하기 전에 최소한 다음 상태를 확인한다.

```bash
git status --short --branch
git diff --name-status
git diff --cached --name-status
git log -5 --oneline --decorate
git worktree list --porcelain
```

working tree가 깨끗하지 않으면 다음 기준을 추가로 적용한다.

1. 현재 요청이 시작되기 전부터 있던 변경은 기본적으로 다른 세션 또는 사용자의 변경으로 간주한다.
2. `ps`, process tree, `/proc/<pid>/cwd`, 파일 수정 시각으로 같은 저장소에서 실행 중인 Codex·Claude·테스트·서버·데이터 작업을 확인한다. 로컬 세션 기록 확인이 꼭 필요하면 작업 소유권과 현재 요청만 최소 범위로 확인하고 비밀값이나 불필요한 대화 내용을 출력하지 않는다.
3. 활성 세션이 수정 중인 경로, 실행 중인 명령의 입력·출력, 기존 index를 건드리지 않는다. 관련 서버도 단순히 오래 실행 중이라는 이유로 종료하지 않는다.
4. 독립 작업은 겹치지 않는 파일에서 진행한다. 같은 working tree의 `HEAD`나 index 변경이 상대 세션을 방해할 수 있으면 `mktemp -d`로 만든 별도 worktree와 전용 브랜치에서 작업하고, 상대 세션이 안정된 체크포인트를 만든 뒤 통합한다.
5. 같은 파일을 고쳐야 하거나 소유권을 판별할 수 없으면 임의 병합·복원·커밋하지 말고, 안전한 읽기·검증을 계속한 뒤에도 불명확할 때 사용자에게 알린다.
6. 공유 working tree에서 `git stash`, `git reset`, `git checkout --`, 대량 포맷, 일괄 삭제를 사용하지 않는다. 다른 세션 변경까지 이동하거나 없앨 수 있다.

작업 도중에도 커밋 직전 상태를 다시 확인한다. 처음 본 snapshot과 경로·mtime·diff가 달라졌다면 다른 세션이 계속 작업 중인 것으로 보고 충돌 가능성을 다시 판단한다.

## 기존 unstaged·staged·unpushed 상태 처리

- Unstaged 변경: 현재 요청과 소유권이 명확히 연결되지 않으면 수정·stage·discard하지 않는다. 완료된 것으로 보이더라도 활성 소유 세션이 있으면 그 세션이 체크포인트를 만들도록 둔다.
- Staged 변경: staged라는 사실만으로 커밋 준비 완료라고 간주하지 않는다. `git diff --cached --name-status`와 필요한 diff·검증 결과·활성 세션을 확인한다. 다른 세션 파일은 unstage하거나 우리 커밋에 포함하지 않는다.
- 이미 커밋됐지만 push되지 않은 변경: `git status --short --branch`, upstream 차이, 커밋 내용과 검증 상태를 확인한다. 작업 소유 세션이 종료됐고 커밋이 완결됐으며 민감정보·불필요한 산출물·WIP가 없으면 현재 브랜치에 안전하게 push할 수 있다.
- 작업 출처가 섞였으면 한 커밋으로 뭉치지 않는다. 경로와 목적별로 소유권이 명확한 변경만 분리하고, 판단 근거를 최종 보고에 남긴다.

## 구현과 데이터 안전

- 파일 I/O, archive 추출, 외부 다운로드, 경로 해석, hash/manifest, 승인 Gate는 fail-closed로 구현한다. 입력 검증·경로 이탈·symlink·중복 alias·부분 파일·redirect·재사용 변조를 고려한다.
- 계약·schema·manifest·registry를 바꾸면 소비 코드, 테스트, 정본 문서, version/hash chain을 함께 점검한다.
- 기존 파일 전체 재작성보다 필요한 부분만 수정하고 현재 코드 스타일을 유지한다.
- 생성 파일을 정본으로 추적해야 할 때는 원천·생성 명령·버전·SHA-256·공개 가능 여부를 검증한다. 과거 승인 산출물의 byte hash를 깨는 수정은 하지 않는다.
- 법률·라이선스 판단은 공식 원문과 고정 revision을 우선한다. 사실, 프로젝트 운영 판단, 법률 자문이 아님을 구분해 기록한다.

## 검증 기준

수정 중에는 관련 테스트를 먼저 실행하고, 의미 있는 Python 구현 체크포인트 전에는 가능한 범위에서 다음 기본 검증을 실행한다.

```bash
uvx ruff check scripts tests
.venv-data/bin/python -m unittest discover -s tests -v
git diff --check
```

원천 계약·수집 경로를 바꿨다면 다음 검증도 실행한다.

```bash
.venv-data/bin/python scripts/data/phase1_sources.py validate-contract
.venv-data/bin/python scripts/data/phase1_sources.py verify
```

- 명령 실패를 코드 결함, 환경 문제, 사용법 오류로 구분한다. 실패를 숨기거나 성공으로 기록하지 않는다.
- 대용량·장시간·외부 자격 증명이 필요한 검증은 dry-run·targeted test·manifest 재해시부터 실행하고, 실행하지 못한 범위와 이유를 남긴다.
- 문서만 바꿔도 `git diff --check`와 링크·명령·경로의 존재 여부를 확인한다.

## 중요 구현 지점 자동 커밋

다음 중 하나에 해당하고 관련 검증이 통과하면 사용자의 추가 지시를 기다리지 말고 즉시 체크포인트 커밋을 만든다.

- 하나의 기능·버그 수정·보안 보강이 독립적으로 완결된 시점
- 계약, schema, version, hash chain, Gate 또는 승인 상태 변경이 문서·테스트와 함께 닫힌 시점
- Phase 또는 하위 단계의 산출물과 검증 기록이 완결된 시점
- 위험한 리팩터링·대규모 생성 작업·브랜치 전환 전에 현재 상태가 재현 가능하고 green인 시점
- 긴 작업 중 검증된 복구 지점을 확보해야 하는 시점

커밋 절차는 다음을 지킨다.

1. 관련 plan 문서의 `## 진행 기록`을 갱신한다. 대응 plan이 없으면 `implementation/history/YYYY-MM-DD-<task-slug>.md`를 만든다.
2. `git status --short`로 다른 세션의 새 변경이 생기지 않았는지 다시 확인한다.
3. `git add -- <명시적 파일 목록>`만 사용한다. 공유 저장소에서 `git add .`, `git add -A`, 무분별한 glob을 사용하지 않는다.
4. `git diff --cached --name-status`와 `git diff --cached --check`로 commit 범위를 검증한다. 예상 밖 파일이 있으면 커밋하지 않는다.
5. 한 커밋에는 하나의 응집된 목적만 담고 제목과 본문을 한국어로 작성한다. 코드 식별자·경로·고정 API 명칭만 원문을 유지할 수 있다.
6. 실패 테스트, 임시 디버그, WIP, 비밀값, 불필요한 생성물은 커밋하지 않는다. 다른 세션 커밋을 amend하지 않는다.

사소한 탐색이나 내용 없는 변경에는 빈 체크포인트를 만들지 않는다. 반대로 여러 독립 기능을 작업 끝까지 한 번에 쌓아두지 않는다.

## 중요 체크포인트 자동 push

- 위 기준으로 만든 커밋은 민감정보·대용량 불필요 산출물·WIP가 없고 사용자가 push 금지나 보류를 지시하지 않았다면 즉시 upstream으로 push한다.
- push 전 `git status --short --branch`와 upstream 차이를 재확인한다. 원격이 앞섰으면 force push하지 말고, clean·격리 상태에서만 안전하게 통합한 뒤 다시 검증한다.
- upstream이 있으면 `git push`, 없으면 의도한 원격과 브랜치를 확인한 뒤 `git push -u origin HEAD`를 사용한다.
- `--force`, `--force-with-lease`는 사용자가 명시적으로 승인한 특별한 경우 외에는 사용하지 않는다.
- 인증·권한·브랜치 정책·원격 선행 커밋으로 push가 막히면 반복해서 덮어쓰지 말고 원인, 로컬 커밋 hash, 미push 상태를 즉시 보고한다.

## 완료 보고

최종 응답에는 다음을 짧게 포함한다.

- 변경 범위와 중요한 판단
- 실행한 검증과 결과
- 생성한 커밋 hash·한국어 제목과 push 대상
- 건드리지 않은 다른 세션 변경, 남은 위험 또는 후속 Gate
