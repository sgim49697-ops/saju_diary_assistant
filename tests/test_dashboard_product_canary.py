# test_dashboard_product_canary.py - 제품 CPU 증거의 전량 집계·불변 발행·실패 처리를 검증한다.

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_product_canary as canary


class ProductCanaryTests(unittest.TestCase):
    def test_fixed_kinds_budget_and_cpu_scope(self):
        rows = canary.policy_cases()
        self.assertEqual(len(rows), 12)
        self.assertEqual({r["response_kind"] for r in rows}, {"direct_fact", "model_generated", "clarification", "blocked"})
        self.assertEqual(canary.GOVERNANCE["new_model_generations"], 0)
        self.assertFalse(canary.GOVERNANCE["service_changed"])

    def test_failed_missing_or_skipped_tests_are_not_success(self):
        for code, output in ((1, "Ran 46 tests in 1s\nOK\n"), (0, "OK\n"), (0, "Ran 45 tests in 1s\nOK\n"), (0, "Ran 46 tests in 1s\nOK (skipped=1)\n")):
            with patch.object(canary.subprocess, "run", return_value=subprocess.CompletedProcess([], code, "", output)), self.assertRaises(ValueError):
                canary.run_checks()

    def test_missing_failed_or_live_browser_is_not_success(self):
        for result in (subprocess.CompletedProcess([], 1, "", ""), subprocess.CompletedProcess([], 0, json.dumps({"passed": True, "cases_passed": 15}), ""), subprocess.CompletedProcess([], 0, json.dumps({"passed": True, "cases_passed": 16, "model_generations": 1, "production_service_accessed": False}), "")):
            with patch.object(canary.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                canary.run_browser()

    def test_unsafe_build_paths_are_rejected(self):
        for build in (None, "../outside", "build-" + "a" * 13):
            with self.assertRaises(ValueError):
                canary.build_path(build)

    def test_publication_reuse_and_tamper_rejection(self):
        aggregate = {"build_id": "build-" + "a" * 12}
        manifest = {"identity": "synthetic"}
        with tempfile.TemporaryDirectory() as directory, patch.object(canary, "PUBLIC_ROOT", Path(directory)), patch.object(canary, "evaluate", return_value=(aggregate, manifest)):
            first = canary.execute()
            self.assertEqual(first, canary.execute())
            self.assertEqual(first, canary.verify(aggregate["build_id"]))
            (Path(directory) / aggregate["build_id"] / "aggregate.json").write_text("{}")
            with self.assertRaises(ValueError):
                canary.verify(aggregate["build_id"])


if __name__ == "__main__":
    unittest.main()
