# test_dashboard_intent_canary_report.py - CPU canary 공개 집계의 실행 경계·불변 발행·재검증을 검사한다.

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_intent_canary as runner

FROZEN = {"canary_id": "synthetic", "source_sha256": {"candidate.py": "a" * 64}}


class CanaryReportTests(unittest.TestCase):
    def test_dry_run_never_launches_checks_or_publishes(self):
        with (
            patch.object(runner, "identity", return_value=FROZEN),
            patch.object(runner, "run_checks", side_effect=AssertionError("execution")),
            patch.object(runner, "write_new", side_effect=AssertionError("write")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(runner.main(["execute"]), 0)

    def test_test_runner_requires_all_25_without_skips_and_disables_gpu(self):
        for code, text, allowed in (
            (0, "\nRan 25 tests in 1.0s\n\nOK\n", True),
            (0, "\nRan 25 tests in 1.0s\n\nOK (skipped=1)\n", False),
            (0, "\nRan 0 tests in 0s\n\nOK\n", False),
            (1, "\nRan 25 tests in 1.0s\n\nFAILED\n", False),
        ):
            with patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], code, stdout="", stderr=text
                ),
            ) as run:
                if allowed:
                    self.assertEqual(runner.run_checks(), 25)
                else:
                    with self.assertRaises(ValueError):
                        runner.run_checks()
                self.assertEqual(
                    run.call_args.kwargs["env"]["CUDA_VISIBLE_DEVICES"], ""
                )
                self.assertEqual(run.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")

    def test_identity_change_during_checks_rejected(self):
        with (
            patch.object(runner, "identity", side_effect=[FROZEN, {"changed": True}]),
            patch.object(runner, "run_checks", return_value=25),
            self.assertRaises(ValueError),
        ):
            runner.evaluate()

    def test_publication_reuse_tamper_and_coverage_checks(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(runner, "PUBLIC_ROOT", Path(temp)),
            patch.object(runner, "identity", return_value=FROZEN),
            patch.object(runner, "run_checks", return_value=25),
        ):
            published = runner.execute()
            self.assertEqual(runner.execute(), published)
            self.assertEqual(runner.verify(published["build_id"]), published)
            root = runner.build_path(published["build_id"])
            for name in runner.PUBLIC_NAMES:
                path = root / name
                original = path.read_bytes()
                value = json.loads(original)
                value["unexpected"] = True
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.verify(published["build_id"])
                with self.assertRaises(ValueError):
                    runner.execute()
                path.write_bytes(original)
            (root / "unexpected.json").write_text("{}")
            with self.assertRaises(ValueError):
                runner.verify(published["build_id"])
            for build in (None, "../x", "build-123", "build-" + "A" * 12):
                with self.assertRaises(ValueError):
                    runner.build_path(build)


if __name__ == "__main__":
    unittest.main()
