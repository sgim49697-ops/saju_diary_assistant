<!-- 2026-09-16-private-repository-access.md - 핵심 구현의 일반 공개를 제한하고 인증된 GitHub 연결 접근을 검증한 기록이다. -->

# 핵심 구현 비공개와 ChatGPT 접근 유지

## 진행 기록

- 날짜: 2026-09-16.
- 요청: 사주 계산 핵심 구현의 향후 공개를 제한하되 GitHub 계정을 연결한 일반 ChatGPT 웹 채팅에서 코드 검토를 유지한다.
- 판단: 계산 모듈을 직접 import하는 외부 Python 파일이 119개이고 승인 release가 구현 hash를 결합하므로, 코드 분리·추적 해제 대신 기존 저장소의 접근 범위를 비공개로 변경했다. 기존 코드·경로·이력·승인 산출물은 보존했다.
- 원격 변경: `sgim49697-ops/saju_diary_assistant`의 `private=true`, `visibility=private`를 확인했다. 기본 브랜치는 `master`다. 전환 전 GitHub Pages는 비활성이고 fork 수는 0이었다. 외부 복사본 유무를 증명하는 수치는 아니다.
- 파일 변경: `AGENTS.md`에 비공개 유지·인증된 ChatGPT 검토·외부 공개 범위·push 전 비공개 확인 규칙을 추가하고 `docs/repository_visibility.md`에 운영 절차를 기록했다.
- 검증: 인증된 GitHub 연결의 저장소 조회에서 `visibility=private`를 확인하고 `scripts/runtime/calculation/engine_v1_5.py` 읽기에 성공했다. 비로그인 저장소 API와 해당 raw 파일 요청은 각각 HTTP `404`였다.
- 검증: `git ls-remote origin HEAD refs/heads/master`로 인증된 Git 읽기와 전환 직후 기존 커밋 `1ef7f9dd8005f56cd8b5efc499b1732202ee4b65` 보존을 확인했다.
- 검증: `.venv-data/bin/python -m unittest discover -s tests -p 'test_repository_workflow_policy.py' -v`의 기존 정책 테스트 4개를 통과했다. `git diff --check`, 문서 3개의 공백·마지막 개행과 로컬 링크 3개 존재 여부를 통과했고 연결에서 읽은 엔진 blob이 로컬 파일과 일치했다. 계산 코드 변경이 없어 ML·전체 runtime 테스트는 실행하지 않았다.
- 작업 경계: 다른 세션의 Phase 10·dashboard v1.18 후보 코드·설정과 로컬 계획 ZIP은 수정·stage하지 않는다. 실행 중인 서비스·작업도 변경하지 않는다.
- 남은 확인: 이 세션의 GitHub 연결 읽기는 검증했지만 사용자의 다른 ChatGPT 웹 채팅을 직접 실행하지는 않았다. 해당 채팅에서 GitHub 앱을 지정해 `AGENTS.md`를 읽으면 별도 연결 상태를 확인할 수 있다. 과거 외부 복사본은 회수하지 않았다.
