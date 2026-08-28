# test_phase4_external_review.py - 외부 AI용 MIX20K 안전 패키지의 범위·결정론·ZIP 보안을 검증한다.

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import ClassVar

from scripts.data.phase4_export_external_review import (
    CHAT_TEMPLATE_SHA256,
    EXPORT_VERSION,
    MODEL_REPO_ID,
    MODEL_REVISION,
    PACKAGE_SCHEMA_VERSION,
    PACKAGE_TYPE,
    _build_payloads,
    _normalized_output,
    _project_records,
    _write_zip,
    build_parser,
    export_package,
    verify_archive,
)
from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import _implementation_paths, sha256_json

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase4ExternalReviewTests(unittest.TestCase):
    COUNTS: ClassVar[dict[str, int]] = {
        "nemotron_saju": 2,
        "bazi_sft": 1,
        "aihub_empathy_single": 1,
        "aihub_empathy_multiturn": 1,
        "yeji_shensha_derived": 1,
    }
    AXIS_SOURCE: ClassVar[dict[str, str]] = {
        "nemotron_saju": "nemotron_saju",
        "bazi_sft": "bazi_sft",
        "aihub_empathy_single": "aihub_empathy",
        "aihub_empathy_multiturn": "aihub_empathy",
        "yeji_shensha_derived": "yeji_bazi_rules",
    }
    AXIS_LICENSE: ClassVar[dict[str, str]] = {
        "nemotron_saju": "CC-BY-4.0",
        "bazi_sft": "Apache-2.0",
        "aihub_empathy_single": "AIHUB-GENERAL-POLICY",
        "aihub_empathy_multiturn": "AIHUB-GENERAL-POLICY",
        "yeji_shensha_derived": "MIT AND MIT",
    }

    @classmethod
    def _fixture(cls) -> tuple[list[dict[str, object]], dict[str, dict[str, object]]]:
        manifests: list[dict[str, object]] = []
        records: dict[str, dict[str, object]] = {}
        position = 0
        for axis, count in cls.COUNTS.items():
            for offset in range(count):
                position += 1
                record_id = f"fixture-{position:02d}"
                restricted = axis.startswith("aihub_")
                record: dict[str, object] = {
                    "id": record_id,
                    "source": cls.AXIS_SOURCE[axis],
                    "source_revision": f"revision-{axis}",
                    "source_variant": "fixture",
                    "mix_axis": axis,
                    "task": "empathic_response" if restricted else "saju_fixture",
                    "domain": "general_dialogue" if restricted else "saju",
                    "license_expression": cls.AXIS_LICENSE[axis],
                    "usage_class": (
                        "conditional_train_allow" if restricted else "train_allow"
                    ),
                    "provenance_status": "verified",
                    "quality_flags": {"parse_ok": True, "language_ok": True},
                    "transformation_chain": [
                        "pii_and_crisis_excluded"
                        if restricted
                        else "fixture_projection"
                    ],
                    "messages": [
                        {"role": "system", "content": "안전한 답변을 작성하세요."},
                        {
                            "role": "user",
                            "content": (
                                f"외부에 나가면 안 되는 질문 {position}"
                                if restricted
                                else f"검수 질문 {position}"
                            ),
                        },
                        {"role": "assistant", "content": f"검수 답변 {position}"},
                    ],
                    "meta": {"fixture": offset},
                }
                records[record_id] = record
                manifests.append(
                    {
                        "schema_version": "1.0.0",
                        "id": record_id,
                        "mix_axis": axis,
                        "record_sha256": sha256_json(record),
                        "candidate_rank": f"{position:064x}",
                        "total_tokens": 20 + position,
                        "assistant_tokens": 5 + position,
                        "parent_staging_build_id": "build-847088ee804d",
                    }
                )
        return manifests, records

    @classmethod
    def _payloads(cls) -> tuple[dict[str, bytes], dict[str, object]]:
        manifests, records = cls._fixture()
        projected = _project_records(
            manifests,
            records,
            expected_axis_counts=cls.COUNTS,
        )
        identity = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_type": PACKAGE_TYPE,
            "export_version": EXPORT_VERSION,
            "canonical_build_id": "build-a1a34616dd72",
            "canonical_build_sha256": "a" * 64,
            "parent_preflight_build_id": "build-7d59833b8d59",
            "parent_preflight_build_sha256": "7" * 64,
            "parent_staging_build_id": "build-847088ee804d",
            "parent_staging_build_sha256": "8" * 64,
            "candidate_manifest_sha256": "c" * 64,
            "canonical_manifest_sha256": "c" * 64,
            "restricted_partition_commitment_sha256": projected["restricted_aggregate"][
                "partition_commitment_sha256"
            ],
            "exporter_source_sha256": "e" * 64,
            "external_scope": "non_aihub_text_plus_aihub_aggregate_only",
            "official_sources_checked_at": "2026-08-28",
            "selected_max_length": 768,
            "model_contract": {
                "repo_id": MODEL_REPO_ID,
                "revision": MODEL_REVISION,
                "parameter_count": 1_291_478_272,
                "dtype": "bfloat16",
                "attention_backend": "sdpa",
                "chat_template_sha256": CHAT_TEMPLATE_SHA256,
                "bos_token_id": 128000,
                "eos_token_id": 128010,
                "pad_token_id": 128001,
            },
            "training_contract": {
                "method": "full_parameter_sft",
                "formal_max_length": 768,
                "observed_max_tokens": 716,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 8,
                "gradient_checkpointing": True,
                "use_cache": False,
                "optimizer": "paged_adamw_8bit",
                "assistant_only_loss": True,
                "packing": False,
                "loss_type": "chunked_nll",
                "learning_rate": 8.0e-6,
                "warmup_ratio": 0.03,
                "lr_scheduler_type": "cosine",
                "weight_decay": 0.01,
                "max_grad_norm": 1.0,
                "seed": 42,
                "data_seed": 42,
            },
            "technical_preflight": {
                "gate_d_status": "passed",
                "diagnostic_1024_status": "passed",
                "resume_200_status": "passed",
                "checkpoint_reload_status": "passed",
                "first_20_median_loss": 2.3489,
                "last_20_median_loss": 0.9452,
                "peak_vram_bytes": 10_498_061_312,
                "finish_vram_free_bytes": 3_005_186_048,
                "runtime": {
                    "gpu_name": "NVIDIA GeForce RTX 5070 Ti",
                    "torch": "2.13.0+cu130",
                    "torch_cuda": "13.0",
                    "transformers": "4.57.6",
                    "trl": "1.12.0",
                    "bitsandbytes": "0.50.2",
                },
            },
            "training_promotion_allowed": True,
            "phase5_training_performed": False,
        }
        return _build_payloads(
            identity,
            projected,
            expected_axis_counts=cls.COUNTS,
        )

    def test_export_cli_requires_explicit_external_safe_confirmation(self) -> None:
        arguments = build_parser().parse_args(["export", "--output", "/tmp/review.zip"])
        self.assertFalse(arguments.confirm_external_safe_scope)
        with self.assertRaisesRegex(Phase4Error, "확인 옵션"):
            export_package(
                Path("/missing/config.json"),
                REPO_ROOT,
                Path("/tmp/review.zip"),
                confirm_external_safe_scope=False,
            )

    def test_exporter_does_not_change_phase4_build_fingerprint(self) -> None:
        config = {"chat_template": {"path": "configs/chat_templates/kanana.jinja"}}
        self.assertNotIn(
            "scripts/data/phase4_export_external_review.py",
            _implementation_paths(config),
        )

    def test_deterministic_archive_excludes_aihub_text_and_verifies(self) -> None:
        payloads, _ = self._payloads()
        self.assertNotIn(
            "외부에 나가면 안 되는 질문".encode(),
            payloads["candidate_external_17k.jsonl"],
        )
        self.assertNotIn(
            "외부에 나가면 안 되는 질문".encode(),
            payloads["training_external_17k.jsonl"],
        )
        self.assertIn(MODEL_REPO_ID.encode(), payloads["MODEL_AND_TRAINING_CONTEXT.md"])
        self.assertIn(b"full_parameter_sft", payloads["MODEL_AND_TRAINING_CONTEXT.md"])
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.zip"
            second = Path(directory) / "second.zip"
            _write_zip(first, payloads)
            _write_zip(second, payloads)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            result = verify_archive(first, expected_axis_counts=self.COUNTS)
        self.assertEqual(result["full_index_rows"], 6)
        self.assertEqual(result["external_content_rows"], 4)
        self.assertEqual(result["withheld_aihub_rows"], 2)
        self.assertFalse(result["contains_aihub_source_text"])

    def test_projection_rejects_pii_and_forbidden_internal_keys(self) -> None:
        manifests, records = self._fixture()
        pii = copy.deepcopy(records)
        pii["fixture-01"]["messages"][1]["content"] = (
            "연락처는 test@example.com 입니다."
        )
        pii_manifests = copy.deepcopy(manifests)
        pii_manifests[0]["record_sha256"] = sha256_json(pii["fixture-01"])
        with self.assertRaisesRegex(Phase4Error, "개인정보 패턴"):
            _project_records(pii_manifests, pii, expected_axis_counts=self.COUNTS)

        forbidden = copy.deepcopy(records)
        forbidden["fixture-01"]["quality_flags"]["raw_hash"] = True
        forbidden_manifests = copy.deepcopy(manifests)
        forbidden_manifests[0]["record_sha256"] = sha256_json(forbidden["fixture-01"])
        with self.assertRaisesRegex(Phase4Error, "내부 필드"):
            _project_records(
                forbidden_manifests,
                forbidden,
                expected_axis_counts=self.COUNTS,
            )

        metadata_pii = copy.deepcopy(records)
        metadata_pii["fixture-01"]["transformation_chain"] = ["test@example.com"]
        metadata_manifests = copy.deepcopy(manifests)
        metadata_manifests[0]["record_sha256"] = sha256_json(metadata_pii["fixture-01"])
        with self.assertRaisesRegex(Phase4Error, "안전하지"):
            _project_records(
                metadata_manifests,
                metadata_pii,
                expected_axis_counts=self.COUNTS,
            )

    def test_archive_rejects_restricted_text_tampering_and_path_traversal(self) -> None:
        payloads, _ = self._payloads()
        tampered = dict(payloads)
        projected = [
            json_line
            for json_line in tampered["candidate_external_17k.jsonl"].splitlines()
        ]
        first = json.loads(projected[0])
        first["source"] = "aihub_empathy"
        projected[0] = json.dumps(
            first, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        tampered["candidate_external_17k.jsonl"] = b"\n".join(projected) + b"\n"
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "tampered.zip"
            _write_zip(archive, tampered)
            with self.assertRaises(Phase4Error):
                verify_archive(archive, expected_axis_counts=self.COUNTS)

            traversal = Path(directory) / "traversal.zip"
            with zipfile.ZipFile(traversal, "w") as stream:
                stream.writestr("../escape.txt", "unsafe")
            with self.assertRaises(Phase4Error):
                verify_archive(traversal, expected_axis_counts=self.COUNTS)

    def test_output_path_must_be_outside_repository(self) -> None:
        with self.assertRaisesRegex(Phase4Error, "저장소 밖"):
            _normalized_output(REPO_ROOT / "review.zip", REPO_ROOT)

    def test_export_rejects_orphan_sidecar_before_loading_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "review.zip"
            archive.with_name(f"{archive.name}.sha256").write_text(
                f"{'0' * 64}  {archive.name}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(Phase4Error, "sidecar"):
                export_package(
                    Path(directory) / "missing-config.json",
                    REPO_ROOT,
                    archive,
                    confirm_external_safe_scope=True,
                )


if __name__ == "__main__":
    unittest.main()
