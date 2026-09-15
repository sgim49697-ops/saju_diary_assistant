# test_dashboard_intent_canary_v2.py - 새 CPU canary의 불변 출력·누락 검사·안전 경계를 검증한다.

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_intent_canary_v2 as canary


class IntentCanaryV2ReportTests(unittest.TestCase):
    def test_fixed_suite_and_scope(self):
        self.assertEqual(canary.TEST_COUNT, 45)
        self.assertEqual(len(canary.TESTS), 5)
        self.assertEqual(canary.GOVERNANCE["new_model_generations"], 0)
        self.assertFalse(canary.GOVERNANCE["service_changed"])

    def test_failed_missing_and_skipped_tests_are_not_success(self):
        for returncode, output in ((1, "Ran 45 tests in 1s\nOK\n"), (0, "OK\n"), (0, "Ran 44 tests in 1s\nOK\n"), (0, "Ran 45 tests in 1s\nOK (skipped=1)\n")):
            with patch.object(canary.subprocess, "run", return_value=subprocess.CompletedProcess([], returncode, "", output)), self.assertRaises(ValueError):
                canary.run_checks()

    def test_browser_failure_or_missing_cases_is_not_success(self):
        for result in (subprocess.CompletedProcess([], 1, "", ""), subprocess.CompletedProcess([], 0, json.dumps({"passed": True, "cases_passed": 5}), "")):
            with patch.object(canary.subprocess, "run", return_value=result), self.assertRaises(ValueError):
                canary.run_browser()

    def test_unsafe_build_id_is_rejected(self):
        for build in (None, "../../outside", "build-" + "a" * 13):
            with self.assertRaises(ValueError):
                canary.build_path(build)

    def test_publication_reuse_verification_and_tamper_fail_closed(self):
        summary = {"build_id": "build-" + "a" * 12, "tests_passed": 45}
        manifest = {"identity": "synthetic"}
        with tempfile.TemporaryDirectory() as directory, patch.object(canary, "PUBLIC_ROOT", Path(directory)), patch.object(canary, "evaluate", return_value=(summary, manifest)):
            result = canary.execute()
            self.assertEqual(canary.execute(), result)
            self.assertEqual(canary.verify(summary["build_id"]), result)
            path = Path(directory) / summary["build_id"] / "aggregate.json"
            path.write_text("{}")
            with self.assertRaises(ValueError):
                canary.verify(summary["build_id"])


if __name__ == "__main__":
    unittest.main()
