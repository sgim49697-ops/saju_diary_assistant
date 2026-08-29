# test_project_status.py - 단일 파일 프로젝트 현황판의 계약·결정적 렌더링을 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.status.project_status import (
    _parser,
    prepare_context,
    render_html,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/project-status-v1.0.0.json"
)


class ProjectStatusTests(unittest.TestCase):
    def test_committed_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-3f8a7f1df86a")

    def test_html_is_self_contained_and_carries_governance(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        payload = render_html(context)
        text = payload.decode("utf-8")
        self.assertIn("KI10만 허용", text)
        self.assertIn("sealed blind", text)
        self.assertNotIn("<script", text)
        self.assertNotIn("src=\"http", text)
        self.assertEqual(payload, render_html(context))

    def test_render_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["render"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
