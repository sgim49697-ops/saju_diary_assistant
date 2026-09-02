# test_chart_only_canary.py - 공개 130-case canary의 hash·Gate·누출 경계를 검증한다.

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.evaluation.saju_runtime.chart_only_canary import (
    ChartOnlyCanaryError,
    verify_report,
)
from scripts.runtime.calculation.contracts import REPO_ROOT

REPORT_ROOT = (
    REPO_ROOT
    / "data/reports/saju_runtime_app_canary/v1.0.0/build-ddde6dce3d3c"
)


class ChartOnlyCanaryTest(unittest.TestCase):
    def test_committed_canary_is_verified_and_non_promoting(self) -> None:
        result = verify_report(REPORT_ROOT)
        self.assertEqual(result["build_id"], "build-ddde6dce3d3c")
        self.assertEqual(result["cases"], 130)
        self.assertTrue(result["canary_gate_passed"])
        self.assertFalse(result["production_application_binding"])

        aggregate = json.loads((REPORT_ROOT / "aggregate.json").read_text())
        self.assertEqual(aggregate["passed"], 130)
        self.assertEqual(aggregate["failed"], 0)
        self.assertTrue(all(aggregate["gate_checks"].values()))
        self.assertFalse(any(aggregate["governance"].values()))
        encoded = json.dumps(aggregate, ensure_ascii=False)
        for forbidden in (
            '"birth_date"',
            '"birth_time"',
            "sbi2_",
            "sc2_",
            "scs2_",
            "scr2_",
            "/home/",
            "/tmp/",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_report_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-canary-mutation-") as directory:
            copy = Path(directory) / REPORT_ROOT.name
            shutil.copytree(REPORT_ROOT, copy)
            aggregate_path = copy / "aggregate.json"
            aggregate = json.loads(aggregate_path.read_text())
            aggregate["passed"] = 129
            aggregate_path.write_text(
                json.dumps(aggregate, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            aggregate_path.chmod(0o644)
            with self.assertRaises(ChartOnlyCanaryError):
                verify_report(copy)
