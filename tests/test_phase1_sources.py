# test_phase1_sources.py - Phase 1 계약, 비밀값, archive 안전성 회귀 테스트

from __future__ import annotations

import copy
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.data.phase1_sources import build_parser
from scripts.data.source_tools import (
    Phase1Error,
    _download_url,
    _verify_manifest,
    aihub_request_headers,
    json_document_inventory,
    load_config,
    merge_zip_parts,
    plan_aihub_download,
    read_aihub_key,
    safe_extract_tar,
    sha256_file,
    source_root,
    validate_config,
    validate_relative_archive_path,
    validate_zip_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs/data_sources.v1.1.json"


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_contract_has_four_sources_and_five_axes(self) -> None:
        result = validate_config(self.config, REPO_ROOT)
        self.assertEqual(result["raw_source_count"], 4)
        self.assertEqual(result["mix_axis_count"], 5)
        self.assertEqual(
            result["mix_totals"],
            {"mix1k": 1000, "mix10": 10000, "mix20": 20000},
        )

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
        official = aihub_request_headers(
            "https://api.aihub.or.kr/down/file", "fake-key"
        )
        redirected = aihub_request_headers(
            "https://download.example.net/signed", "fake-key"
        )
        self.assertEqual(official["apikey"], "fake-key")
        self.assertNotIn("apikey", redirected)

    def test_aihub_rejects_http_credentials_and_nonstandard_port(self) -> None:
        for url in (
            "http://api.aihub.or.kr/down/file",
            "https://user:password@api.aihub.or.kr/down/file",
            "https://api.aihub.or.kr:444/down/file",
        ):
            with self.subTest(url=url), self.assertRaises(Phase1Error):
                aihub_request_headers(url, "fake-key")

    def test_full_nemotron_contract_is_explicit_and_complete(self) -> None:
        config = load_config(CONFIG_PATH)
        result = validate_config(config, REPO_ROOT)
        source = config["sources"]["nemotron_saju"]
        variants = source["file_variants"]
        self.assertEqual(result["canonical_plan_version"], "2.3.1")
        self.assertEqual(len(source["allow_files"]), 22)
        self.assertEqual(sum(value == "v6" for value in variants.values()), 3)
        self.assertEqual(sum(value == "v7" for value in variants.values()), 17)
        self.assertEqual(source["expected_rows"]["total"], 1_000_000)

    def test_full_nemotron_requires_explicit_cli_source(self) -> None:
        parser = build_parser()
        explicit = parser.parse_args(
            ["download-hf", "--source", "nemotron_saju", "--dry-run"]
        )
        implicit = parser.parse_args(["download-hf", "--dry-run"])
        self.assertEqual(explicit.source, ["nemotron_saju"])
        self.assertIsNone(implicit.source)

    def test_rejects_expected_file_outside_allowlist(self) -> None:
        config = load_config(CONFIG_PATH)
        modified = copy.deepcopy(config)
        modified["sources"]["nemotron_saju"]["expected_files"][
            "adapters/forbidden.safetensors"
        ] = {"bytes": 1, "sha256": "0" * 64}
        with self.assertRaises(Phase1Error):
            validate_config(modified, REPO_ROOT)

    def test_yeji_provenance_requires_fixed_metadata_and_official_url(self) -> None:
        config = load_config(CONFIG_PATH)
        for mutation in ("missing_hashes", "unexpected_host"):
            modified = copy.deepcopy(config)
            provenance = modified["sources"]["yeji_bazi_rules"]["provenance"]
            if mutation == "missing_hashes":
                provenance.pop("expected_files")
            else:
                provenance["raw_url_template"] = (
                    "https://example.test/chxb/shensha/{revision}/{path}"
                )
            with self.subTest(mutation=mutation), self.assertRaises(Phase1Error):
                validate_config(modified, REPO_ROOT)

    def test_malformed_contract_fails_with_domain_error(self) -> None:
        modified = copy.deepcopy(self.config)
        modified["mix_contract"] = "invalid"
        with self.assertRaises(Phase1Error):
            validate_config(modified, REPO_ROOT)


class DownloadSafetyTests(unittest.TestCase):
    def test_generic_download_rejects_non_https_and_unexpected_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "payload"
            for url in (
                "file:///etc/passwd",
                "http://raw.githubusercontent.com/chxb/shensha/rev/LICENSE",
                "https://example.test/chxb/shensha/rev/LICENSE",
            ):
                with self.subTest(url=url), self.assertRaises(Phase1Error):
                    _download_url(
                        url,
                        target,
                        expected_bytes=1,
                        expected_sha256="0" * 64,
                    )
            self.assertFalse(target.exists())


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
    def test_rejects_archive_path_aliases_and_controls(self) -> None:
        for name in ("safe//data.json", "safe/./data.json", "safe\n/data.json"):
            with self.subTest(name=name), self.assertRaises(Phase1Error):
                validate_relative_archive_path(name)

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

    def test_existing_tar_extraction_is_revalidated_against_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "safe.tar"
            payload = b"expected"
            with tarfile.open(archive, "w") as stream:
                info = tarfile.TarInfo("safe/data.txt")
                info.size = len(payload)
                stream.addfile(info, io.BytesIO(payload))
            destination = root / "output"
            self.assertEqual(safe_extract_tar(archive, destination), ["safe/data.txt"])
            (destination / "safe/data.txt").write_bytes(b"tampered")
            with self.assertRaises(Phase1Error):
                safe_extract_tar(archive, destination)

    def test_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../escape.json", "{}")
            with self.assertRaises(Phase1Error):
                validate_zip_paths(archive)

    def test_rejects_duplicate_zip_member_alias(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "duplicate.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("safe/data.json", "{}")
                stream.writestr("safe\\data.json", "{}")
            with self.assertRaises(Phase1Error):
                validate_zip_paths(archive)

    def test_rejects_zip_special_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "special.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                entry = zipfile.ZipInfo("device")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFCHR | 0o600) << 16
                stream.writestr(entry, b"")
            with self.assertRaises(Phase1Error):
                validate_zip_paths(archive)

    def test_rejects_symlinked_zip_part_before_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_bytes(b"not-a-zip")
            (root / "dataset.zip.part1").symlink_to(outside)
            (root / "dataset.zip.part2").write_bytes(b"tail")
            with self.assertRaises(Phase1Error):
                merge_zip_parts(root)
            self.assertFalse((root / "dataset.zip").exists())

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

            (root / "dataset.zip").write_bytes(b"tampered")
            with self.assertRaises(Phase1Error):
                merge_zip_parts(root)


class ManifestSafetyTests(unittest.TestCase):
    def test_source_root_rejects_symlinked_path_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo_root = Path(directory)
            raw_root = repo_root / "data/raw"
            raw_root.mkdir(parents=True)
            (raw_root / "elsewhere").mkdir()
            (raw_root / "nemotron_saju").symlink_to(raw_root / "elsewhere")
            config = load_config(CONFIG_PATH)
            with self.assertRaises(Phase1Error):
                source_root(config, repo_root, "nemotron_saju")

    def test_manifest_rejects_symlink_and_unregistered_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "raw"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            linked = root / "payload.txt"
            linked.symlink_to(outside)
            manifest = {
                "source": "fixture",
                "revision": "rev",
                "license_expression": "MIT",
                "usage_class": "train_allow",
                "files": [
                    {
                        "path": "payload.txt",
                        "bytes": outside.stat().st_size,
                        "sha256": sha256_file(outside),
                    }
                ],
            }
            (root / "SOURCE_MANIFEST.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with self.assertRaises(Phase1Error):
                _verify_manifest(root, expected_source="fixture")

            linked.unlink()
            linked.write_text("inside", encoding="utf-8")
            manifest["files"][0].update(
                {"bytes": linked.stat().st_size, "sha256": sha256_file(linked)}
            )
            (root / "SOURCE_MANIFEST.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            (root / "unregistered.txt").write_text("extra", encoding="utf-8")
            with self.assertRaises(Phase1Error):
                _verify_manifest(root, expected_source="fixture")


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
