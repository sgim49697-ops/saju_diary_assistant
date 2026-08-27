# test_phase1_sources.py - Phase 1 계약, 비밀값, archive 안전성 회귀 테스트

from __future__ import annotations

import copy
import io
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.data.source_tools import (
    Phase1Error,
    aihub_request_headers,
    json_document_inventory,
    load_config,
    merge_zip_parts,
    plan_aihub_download,
    read_aihub_key,
    safe_extract_tar,
    validate_config,
    validate_zip_paths,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs/data_sources.v1.json"


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_contract_has_four_sources_and_five_axes(self) -> None:
        result = validate_config(self.config, REPO_ROOT)
        self.assertEqual(result["raw_source_count"], 4)
        self.assertEqual(result["mix_axis_count"], 5)
        self.assertEqual(result["mix_totals"], {"mix1k": 1000, "mix10": 10000, "mix20": 20000})

    def test_aihub_271_cannot_become_active(self) -> None:
        modified = copy.deepcopy(self.config)
        modified["sources"]["aihub_empathy"]["dataset_id"] = "271"
        with self.assertRaises(Phase1Error):
            validate_config(modified, REPO_ROOT)

    def test_aihub_dry_run_does_not_read_secret(self) -> None:
        modified = copy.deepcopy(self.config)
        modified["paths"]["aihub_key_file"] = "/definitely/missing/aihub.env"
        plan = plan_aihub_download(modified)
        self.assertEqual(plan["dataset_id"], "86")
        self.assertEqual(plan["request_count"], 4)
        self.assertNotIn("apikey", json.dumps(plan).lower())

    def test_aihub_key_is_not_forwarded_to_redirect_host(self) -> None:
        official = aihub_request_headers("https://api.aihub.or.kr/down/file", "fake-key")
        redirected = aihub_request_headers("https://download.example.net/signed", "fake-key")
        self.assertEqual(official["apikey"], "fake-key")
        self.assertNotIn("apikey", redirected)


class SecretFileTests(unittest.TestCase):
    def test_reads_strict_secret_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "config"
            parent.mkdir(mode=0o700)
            os.chmod(parent, 0o700)
            key_file = parent / "aihub.env"
            key_file.write_text("AIHUB_APIKEY=test-secret-value\n", encoding="utf-8")
            os.chmod(key_file, 0o600)
            self.assertEqual(read_aihub_key(key_file), "test-secret-value")

    def test_rejects_empty_or_loosely_permissioned_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "config"
            parent.mkdir(mode=0o700)
            os.chmod(parent, 0o700)
            key_file = parent / "aihub.env"
            key_file.write_text("AIHUB_APIKEY=\n", encoding="utf-8")
            os.chmod(key_file, 0o600)
            with self.assertRaises(Phase1Error):
                read_aihub_key(key_file)
            key_file.write_text("AIHUB_APIKEY=hidden\n", encoding="utf-8")
            os.chmod(key_file, 0o644)
            with self.assertRaises(Phase1Error):
                read_aihub_key(key_file)


class ArchiveSafetyTests(unittest.TestCase):
    def test_rejects_tar_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe.tar"
            with tarfile.open(archive, "w") as stream:
                info = tarfile.TarInfo("../escape.txt")
                payload = b"forbidden"
                info.size = len(payload)
                stream.addfile(info, io.BytesIO(payload))
            with self.assertRaises(Phase1Error):
                safe_extract_tar(archive, root / "output")
            self.assertFalse((root / "escape.txt").exists())

    def test_rejects_tar_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unsafe-link.tar"
            with tarfile.open(archive, "w") as stream:
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/target"
                stream.addfile(info)
            with self.assertRaises(Phase1Error):
                safe_extract_tar(archive, root / "output")

    def test_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../escape.json", "{}")
            with self.assertRaises(Phase1Error):
                validate_zip_paths(archive)

    def test_merges_parts_in_numeric_order_and_retains_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as stream:
                stream.writestr("safe/data.json", "{}")
            payload = buffer.getvalue()
            split = len(payload) // 2
            part1 = root / "dataset.zip.part1"
            part2 = root / "dataset.zip.part2"
            part1.write_bytes(payload[:split])
            part2.write_bytes(payload[split:])
            merged = merge_zip_parts(root)
            self.assertEqual(merged, ["dataset.zip"])
            self.assertTrue(part1.exists())
            self.assertTrue(part2.exists())
            self.assertEqual((root / "dataset.zip").read_bytes(), payload)


class InventoryStructureTests(unittest.TestCase):
    def test_counts_multiturn_groups_without_exposing_values(self) -> None:
        raw_identifier = "private-conversation-1"
        raw_sentence = "외부에 출력하면 안 되는 문장"
        document = [
            {
                "talk": {
                    "id": {"talk-id": raw_identifier},
                    "content": {
                        "HS01": raw_sentence,
                        "SS01": "응답 1",
                        "HS02": "발화 2",
                        "SS02": "응답 2",
                    },
                }
            }
        ]
        result = json_document_inventory(document)
        self.assertEqual(result["records_with_two_or_more_turn_pairs"], 1)
        self.assertEqual(result["turn_pair_count_distribution"], {"2": 1})
        self.assertEqual(len(result["_eligible_group_hashes"]), 1)
        rendered = json.dumps(result, ensure_ascii=False, default=list)
        self.assertNotIn(raw_identifier, rendered)
        self.assertNotIn(raw_sentence, rendered)


if __name__ == "__main__":
    unittest.main()
