# test_dashboard_v115_http_canary.py - 진단 성공·실패 시 process 권한 설정의 복구를 검증한다.

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_v115_http_canary as canary


class HttpCanaryTests(unittest.TestCase):
    def setUp(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)

    def test_private_creation_mask_is_restored_on_success(self):
        with patch.object(canary, "_run_private", return_value={"ok": True}):
            self.assertEqual(canary.run(Path("/unused"), Path("/unused")), {"ok": True})
        self.assertEqual(os.umask(0o022), 0o022)

    def test_private_creation_mask_is_restored_on_failure(self):
        with (
            patch.object(canary, "_run_private", side_effect=ValueError("diagnostic")),
            self.assertRaises(ValueError),
        ):
            canary.run(Path("/unused"), Path("/unused"))
        self.assertEqual(os.umask(0o022), 0o022)
