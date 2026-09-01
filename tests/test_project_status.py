# test_project_status.py - 단일 파일 프로젝트 현황판의 계약·결정적 렌더링을 검증한다.

from __future__ import annotations

import json
import re
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
    / "configs/data_versions/saju_1b_baseline/project-status-v1.2.0.json"
)


class ProjectStatusTests(unittest.TestCase):
    def test_committed_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-84cf0ec3010d")

    def test_historical_v1_contract_remains_recognized(self) -> None:
        historical = REPO_ROOT / (
            "configs/data_versions/saju_1b_baseline/project-status-v1.0.0.json"
        )
        result = validate_contract(
            json.loads(historical.read_text(encoding="utf-8")), REPO_ROOT
        )
        self.assertEqual(result["status_version"], "v1.0.0")
        historical_v11 = REPO_ROOT / (
            "configs/data_versions/saju_1b_baseline/project-status-v1.1.0.json"
        )
        result_v11 = validate_contract(
            json.loads(historical_v11.read_text(encoding="utf-8")), REPO_ROOT
        )
        self.assertEqual(result_v11["status_version"], "v1.1.0")

    def test_html_is_self_contained_and_carries_governance(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        payload = render_html(context)
        text = payload.decode("utf-8")
        self.assertIn("PHASE 6 COMPLETE · AUTOMATED REPAIR", text)
        self.assertIn("AUTOMATED_REPAIR_REQUIRED", text)
        self.assertIn("eval-e8630962cab2", text)
        self.assertIn("not_measured", text)
        self.assertIn("결정론 56%", text)
        self.assertIn("임의 네 기둥 47건", text)
        self.assertIn("run-1f5d732cae67", text)
        self.assertIn("2,500 optimizer step", text)
        self.assertIn("build-8bd88d6db03a", text)
        self.assertIn("22/1,560 mismatch", text)
        self.assertIn("MIX20K-v3.1·추가 학습", text)
        self.assertIn("runtime_approved=false", text)
        self.assertIn("sealed blind", text)
        self.assertIsNone(
            re.search(
                r"사람\s*(?:평가|검수|심사)|독립\s*(?:평가|검수|심사)|"
                r"전문가|수동\s*(?:평가|검수|심사)",
                text,
                re.IGNORECASE,
            )
        )
        self.assertNotIn("<script", text)
        self.assertNotIn("src=\"http", text)
        self.assertEqual(payload, render_html(context))

    def test_render_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["render"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
