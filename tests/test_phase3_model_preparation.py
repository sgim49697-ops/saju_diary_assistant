# test_phase3_model_preparation.py - Phase 3 모델·환경 계약과 snapshot 안전성 회귀 테스트

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.model.errors import Phase3Error
from scripts.model.phase3_prepare import build_parser
from scripts.model.phase3_tools import (
    load_config,
    review_remote_code,
    sha256_file,
    validate_contract,
    validate_relative_path,
    verify_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/model-preparation-v1.0.0.json"
)


class Phase3ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_contract_pins_blackwell_environment_and_model_revision(self) -> None:
        result = validate_contract(self.config, REPO_ROOT, require_lock=True)
        self.assertEqual(result["canonical_plan_version"], "2.4.0")
        self.assertEqual(result["direct_package_count"], 8)
        self.assertEqual(result["model_file_count"], 14)
        self.assertEqual(
            result["model_revision"],
            "bf4786aa2a1908adce942d53976270132732f720",
        )
        self.assertTrue(result["lock_present"])

    def test_contract_rejects_moving_revision_and_cuda_wheel(self) -> None:
        mutations = (
            ("revision", "main"),
            ("pytorch_index_url", "https://download.pytorch.org/whl/cu128"),
        )
        for field, value in mutations:
            modified = copy.deepcopy(self.config)
            if field == "revision":
                modified["model"][field] = value
            else:
                modified["environment"][field] = value
            with self.subTest(field=field), self.assertRaises(Phase3Error):
                validate_contract(modified, REPO_ROOT)

    def test_tracked_chat_template_is_exact_upstream_payload(self) -> None:
        template = self.config["chat_template"]
        template_path = REPO_ROOT / template["tracked_path"]
        self.assertEqual(template_path.stat().st_size, 10_725)
        self.assertEqual(sha256_file(template_path), template["sha256"])
        text = template_path.read_text(encoding="utf-8")
        for fragment in template["required_fragments"]:
            self.assertIn(fragment, text)

    def test_download_model_defaults_to_dry_run(self) -> None:
        arguments = build_parser().parse_args(["download-model"])
        self.assertFalse(arguments.execute)
        self.assertFalse(arguments.dry_run)

    def test_unsafe_relative_paths_are_rejected(self) -> None:
        for value in ("../model", "/tmp/model", "models//snapshot", "models/./snapshot"):
            with self.subTest(value=value), self.assertRaises(Phase3Error):
                validate_relative_path(value)


class RemoteCodeReviewTests(unittest.TestCase):
    def test_safe_remote_code_is_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modeling.py"
            path.write_text("import torch\nvalue = torch.ones(1)\n", encoding="utf-8")
            result = review_remote_code(path, {"socket"}, {"exec", "open"})
        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(result["imports"], ["torch"])
        self.assertEqual(result["network_subprocess_or_delete_calls"], 0)

    def test_network_import_and_delete_call_are_rejected(self) -> None:
        payloads = (
            "import socket\n",
            "from pathlib import Path\nPath('x').unlink()\n",
        )
        for payload in payloads:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "unsafe.py"
                path.write_text(payload, encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(Phase3Error):
                    review_remote_code(path, {"socket"}, {"exec", "open"})


class SnapshotVerificationTests(unittest.TestCase):
    @staticmethod
    def _fake_config(root: Path) -> dict[str, object]:
        for name, payload in (
            ("configuration.py", b"class Config:\n    pass\n"),
            ("modeling.py", b"import torch\nVALUE = torch.float32\n"),
        ):
            (root / name).write_bytes(payload)
        files = [
            {
                "bytes": path.stat().st_size,
                "path": path.name,
                "sha256": sha256_file(path),
            }
            for path in sorted(root.iterdir())
        ]
        return {
            "model": {
                "files": files,
                "remote_code": {
                    "banned_calls": ["exec", "open"],
                    "banned_import_roots": ["socket", "subprocess"],
                    "paths": ["configuration.py", "modeling.py"],
                },
                "repo_id": "example/model",
                "revision": "a" * 40,
            }
        }

    def test_snapshot_hashes_are_verified_and_tampering_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._fake_config(root)
            result = verify_snapshot(config, REPO_ROOT, root)
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["status"], "verified")

            (root / "modeling.py").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(Phase3Error):
                verify_snapshot(config, REPO_ROOT, root)

    def test_snapshot_rejects_extra_payload_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._fake_config(root)
            (root / "extra.txt").write_text("extra\n", encoding="utf-8")
            with self.assertRaises(Phase3Error):
                verify_snapshot(config, REPO_ROOT, root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._fake_config(root)
            (root / "linked").symlink_to(root / "modeling.py")
            with self.assertRaises(Phase3Error):
                verify_snapshot(config, REPO_ROOT, root)


if __name__ == "__main__":
    unittest.main()
