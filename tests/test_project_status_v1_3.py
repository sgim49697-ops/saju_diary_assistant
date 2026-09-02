# test_project_status_v1_3.py - 대화 자동 진단을 포함한 v1.3 현황과 v1.2 보존을 검증한다.

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.status.project_status import (
    prepare_context as prepare_v12_context,
)
from scripts.status.project_status import (
    render_html,
)
from scripts.status.project_status_v1_3 import (
    _parser,
    prepare_context,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/project-status-v1.3.0.json"
)
V12_CONFIG_PATH = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/project-status-v1.2.0.json"
)


class ProjectStatusV13Tests(unittest.TestCase):
    def test_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-38b9ca77ce45")

    def test_v12_renderer_and_build_identity_are_unchanged(self) -> None:
        historical = prepare_v12_context(REPO_ROOT, V12_CONFIG_PATH)
        self.assertEqual(historical["build_id"], "build-84cf0ec3010d")

    def test_historical_v13_build_remains_reproducible(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(context["build_id"], "build-38b9ca77ce45")
        self.assertEqual(
            (context["snapshot_root"] / "index.html").read_bytes(),
            render_html(context),
        )

    def test_html_contains_dialogue_results_without_changing_authority(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        text = render_html(context).decode("utf-8")
        self.assertIn("eval-562c07d0e2e6", text)
        self.assertIn("eval-56d1357560d5", text)
        self.assertIn("재질문 7%→2%", text)
        self.assertIn("max-token hit 2→0", text)
        self.assertIn("AUTOMATED_REPAIR_REQUIRED", text)
        self.assertIn("runtime_approved=false", text)
        self.assertIn("MIX20K-v3.1·추가 학습", text)
        self.assertIsNone(
            re.search(
                r"사람\s*(?:평가|검수|심사)|독립\s*(?:평가|검수|심사)|"
                r"전문가|수동\s*(?:평가|검수|심사)",
                text,
                re.IGNORECASE,
            )
        )
        self.assertNotIn("<script", text)
        self.assertNotIn('src="http', text)

    def test_render_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["render"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
