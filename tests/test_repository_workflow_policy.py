# test_repository_workflow_policy.py - 기본 브랜치 우선·분기 사전 확인·서비스와 자료 보존 규칙을 검증한다.

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class RepositoryWorkflowPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    def test_primary_workspace_uses_existing_default_branch(self) -> None:
        self.assertIn("기본 브랜치는 `master`", self.rules)
        self.assertIn("원본 프로젝트 폴더의 `master`", self.rules)
        self.assertIn("브랜치 이름을 `main`으로 바꾸지 않는다", self.rules)

    def test_isolation_is_never_an_automatic_fallback(self) -> None:
        self.assertIn("사전 확인 없이 새 브랜치·worktree를 만들지 않는다", self.rules)
        self.assertIn("다른 세션·dirty 상태를 이유로 자동 분기하지 않는다", self.rules)
        self.assertIn("상태를 알리고 대기한다", self.rules)
        self.assertNotIn("`mktemp -d`로 만든 별도 worktree와 전용 브랜치에서 작업", self.rules)

    def test_requested_merge_includes_integration_but_not_deployment(self) -> None:
        self.assertIn("검증·PR 병합·원본 폴더 동기화·안전한 정리", self.rules)
        self.assertIn("후보 코드의 병합은 운영 배포나 모델 승격이 아니다", self.rules)

    def test_cleanup_preserves_unique_and_live_work(self) -> None:
        self.assertIn("병합/변경 동등성·복구 근거", self.rules)
        self.assertIn("실행 서비스, 고유 데이터·모델·run, 미커밋 변경", self.rules)
        self.assertIn("worktree는 보존하고 예외를 보고한다", self.rules)
        self.assertIn("`git add -- <명시적 파일 목록>`만 사용", self.rules)


if __name__ == "__main__":
    unittest.main()
