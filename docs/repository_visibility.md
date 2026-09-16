<!-- repository_visibility.md - 핵심 구현을 비공개로 보존하면서 인증된 ChatGPT 코드 검토를 유지하는 절차다. -->

# 저장소 비공개와 ChatGPT 접근

`sgim49697-ops/saju_diary_assistant`는 비공개 개발 저장소다. 계산 코드·정책·테스트·설계·검증 이력은 기존 경로와 `master`에서 계속 관리한다. GitHub 저장소 접근 권한으로 일반 공개를 제한하며 코드 삭제·난독화·추적 해제·과거 커밋 재작성은 하지 않는다.

## ChatGPT 채팅에서 코드 확인

GitHub 계정을 연결한 ChatGPT 채팅은 그 연결에 허용된 저장소를 읽는 방식으로 사용한다. 공개 웹 검색이나 비로그인 GitHub URL 접근과 구분한다.

1. ChatGPT에서 사용 중인 GitHub 앱·플러그인의 연결 계정을 확인한다.
2. 해당 GitHub 연결의 저장소 접근 설정에 이 저장소가 포함돼 있는지 확인한다. 조직 저장소라면 필요한 조직 승인도 별도로 확인한다.
3. 연결 설정을 바꿨다면 새 채팅에서 GitHub 앱을 명시해 아래처럼 요청한다.

   > GitHub 연결을 사용해서 sgim49697-ops/saju_diary_assistant의 master에 있는 AGENTS.md를 읽고, 핵심 구현 비공개 규칙을 요약해 줘. 공개 웹 검색 대신 연결된 저장소에서 확인해 줘.

4. 접근되지 않으면 계정·저장소 선택·연결 권한을 점검한다. 해결을 위해 저장소를 다시 공개하거나 토큰을 채팅·문서에 붙여 넣지 않는다.

2026-09-16 비공개 전환 직후 이 작업 세션의 GitHub 연결에서 저장소 `visibility=private`와 계산 엔진 파일 읽기 성공을 확인했다. 사용자의 다른 ChatGPT 채팅 세션 자체는 여기서 실행하지 않았으므로 위 요청으로 그 세션의 연결 상태를 확인한다.

일반 ChatGPT 웹 채팅에서 연결 도구가 사용하는 인증·접근 권한은 [OpenAI 공식 플러그인 안내](https://learn.chatgpt.com/docs/plugins#how-permissions-and-data-sharing-work)를 따른다.

## 공개·배포 기준

- 핵심 구현과 내부 자료는 이 비공개 저장소에 보존한다. 별도 공개 소개가 필요하면 사용자가 지정한 범위의 기능 설명·화면·선별 집계만 내보낸다.
- `public` 응답 또는 “공개 보고서”라는 이름만으로 외부 공개를 허용하지 않는다. 개인정보가 없는 집계라도 구현 노하우·정책·내부 경로가 포함될 수 있다.
- GitHub Pages, 공개 미러, 패키지, 컨테이너, 릴리스 첨부물에도 같은 기준을 적용한다.
- 이전 공개 기간의 외부 복사본은 이번 변경으로 회수되지 않는다. 과거 Git 이력은 보존하고 이후의 일반 공개를 제한한다.

## 원격 확인

push 전에 의도한 원격과 비공개 상태를 확인한다.

```bash
git remote get-url origin
gh api repos/sgim49697-ops/saju_diary_assistant --jq .private
git ls-remote origin HEAD refs/heads/master
```

현재 원격은 `https://github.com/sgim49697-ops/saju_diary_assistant.git`이고 API 결과는 `true`여야 한다. 조회 실패·예상 밖 원격·공개 상태에서는 push를 보류한다. 토큰이나 비밀번호를 출력하지 않는다.

비로그인 접근 차단을 확인할 때는 인증 헤더 없는 요청의 상태 코드만 확인한다.

```bash
curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}\n' https://api.github.com/repos/sgim49697-ops/saju_diary_assistant
```

전환 직후 결과는 `404`였다. `404`만으로 비공개 전환 성공을 단정하지 않고, 인증된 API의 `private=true`와 파일 읽기 성공을 함께 확인한다.

GitHub 공개 범위 변경의 효과와 한계는 [GitHub 공식 안내](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)를 따른다.
