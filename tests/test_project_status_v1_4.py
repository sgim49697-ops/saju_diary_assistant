# test_project_status_v1_4.py - 제한 Runtime 운영선을 반영한 v1.4 현황 계약을 검증한다.

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from scripts.status.project_status import render_html, verify_status
from scripts.status.project_status_v1_4 import (
    _parser,
    prepare_context,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/project-status-v1.4.0.json"
)


class ProjectStatusV14Tests(unittest.TestCase):
    def test_contract_and_fingerprint_are_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(context["build_id"], "build-faf55ff6886d")

    def test_registry_points_to_verified_v14_build(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        result = verify_status(context, require_registry=True)
        self.assertTrue(result["registry_verified"])
        self.assertEqual(result["build_id"], "build-faf55ff6886d")

    def test_html_separates_limited_runtime_from_strict_and_training(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        text = render_html(context).decode("utf-8")
        self.assertIn("build-46185262164f", text)
        self.assertIn("Dashboard v1.11", text)
        self.assertIn("8,522/8,522 mismatch 0", text)
        self.assertIn("strict/full Runtime", text)
        self.assertIn("full_runtime_gate_passed=false", text)
        self.assertIn("not_measured", text)
        self.assertIsNone(
            re.search(
                r"사람\s*(?:평가|검수|심사)|독립\s*(?:평가|검수|심사)|"
                r"전문가|수동\s*(?:평가|검수|심사)",
                text,
                re.IGNORECASE,
            )
        )

    def test_render_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["render"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
