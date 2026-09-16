<!-- 2026-09-16-private-repository-access.md - 잘못 수행한 저장소 비공개 전환과 공개 상태 복구를 기록한다. -->

# 저장소 공개 범위 정정

현재 상태는 **공개**다. 아래 이전 비공개 전환은 사용자가 요청한 조치가 아니었으며 철회했다.

## 진행 기록

- 날짜: 2026-09-16.
- 원래 요청: 저장소 공개와 GitHub 연결을 이용한 ChatGPT 코드 검토를 유지하면서 일부 핵심 구현의 노출을 줄인다. 저장소 전체 비공개 전환은 요청하지 않았다.
- 잘못된 처리: 일부 핵심 구현 보호 요청을 전체 저장소 비공개 전환으로 확대 해석해 GitHub 설정을 바꾸고 `536394f`에 비공개 유지 규칙·안내를 추가했다. 당시 인증된 GitHub 연결 읽기는 성공했지만 이 검증이 공개 여부 변경에 대한 사용자 권한을 대신하지는 않는다.
- 정정: 사용자의 지적 직후 `sgim49697-ops/saju_diary_assistant`를 `private=false`, `visibility=public`으로 복구했다. 기본 브랜치는 `master`이며 비로그인 저장소 API의 HTTP `200`을 확인했다.
- 파일 변경: `AGENTS.md`의 비공개 강제·공개 원격 push 차단 규칙을 제거했다. 공개 여부 변경은 명시적 요청이 있을 때만 수행하도록 정정했고 잘못된 비공개 운영 안내 `docs/repository_visibility.md`를 삭제했다.
- 보존: 계산 코드·파일 경로·개발 이력·승인 산출물·hash chain은 변경하지 않았다. 다른 세션의 Phase 10·dashboard v1.18 후보 작업과 로컬 계획 ZIP도 수정·stage하지 않는다.
- 검증: `.venv-data/bin/python -m unittest discover -s tests -p 'test_repository_workflow_policy.py' -v`의 4개 테스트, `git diff --check`, 수정 문서 2개와 로컬 링크 2개 검사를 통과했다. 인증된 API의 `private=false`·`visibility=public`과 비로그인 계산 엔진 raw 파일 HTTP `200`을 재확인했다. 구현 변경이 없어 전체 ML·runtime 테스트는 실행하지 않았다.
- 후속 범위: 일부 핵심 구현을 공개 범위에서 분리하는 작업은 아직 적용하지 않았다. 공개 저장소를 유지하고 대상 코드·상세 자료 및 외부 검토에 미치는 영향을 구분해 진행해야 한다.

## 최소 변경 적용

- 날짜: 2026-09-16. 후속 요청에 따라 저장소 공개와 기존 코드 구조를 유지하면서 새 내부 자료의 실수 업로드를 줄이는 범위로 축소했다.
- 변경 범위: `.gitignore`에 `/private/`를 추가하고 `AGENTS.md`에 새 내부 계산 연구·상세 설계·실험 원문을 이 경로에 로컬 보관하는 규칙을 추가했다.
- 한계: 기존 공개 코드·문서는 숨겨지지 않는다. `private/`는 Git 백업 대상이 아니며 다른 경로로 복사하거나 강제 추가한 파일까지 막는 접근 제어 수단은 아니다.
- 보존: 기존 코드·계산 결과·저장소 공개 설정·다른 세션의 Phase 10 변경은 건드리지 않았다.
- 검증: `git check-ignore -v -- private/calculation-notes.md private/experiments/engine.py`로 하위 경로까지 제외됨을 확인했다. 기존 저장소 정책 unittest 4개, 변경 파일의 `git diff --check`, 문서 링크 2개 검사와 기존 계산 엔진 추적 유지 확인을 통과했다.
