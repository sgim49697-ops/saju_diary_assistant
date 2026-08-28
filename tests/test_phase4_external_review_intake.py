# test_phase4_external_review_intake.py - 외부 검수 수용의 identity·표본·보안·불변성을 검증한다.

from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.data import phase4_external_review_intake as intake
from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import _implementation_paths, sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase4ExternalReviewIntakeTests(unittest.TestCase):
    def _submission_dir(self, root: Path) -> Path:
        bundle = root / "bundle"
        bundle.mkdir()
        values = {
            "saju_mix20k_external_review_report.md": b"# review\n",
            "saju_mix20k_external_review_summary.json": b"{}\n",
            "saju_mix20k_external_findings.jsonl": b"{}\n",
            "saju_mix20k_reviewed_ids.jsonl": b"{}\n",
        }
        for name, payload in values.items():
            (bundle / name).write_bytes(payload)
        return bundle

    def _verification_report(self, review_id: str, review_sha256: str) -> dict:
        submitted_sha256 = {name: "a" * 64 for name in intake.SUBMITTED_FILES}
        return {
            "review_id": review_id,
            "review_sha256": review_sha256,
            "reviewed_at": "2026-08-28",
            "review_identity_inputs": {
                "submitted_files_sha256": submitted_sha256,
            },
            "source_package": {
                "package_id": "fixture-package",
                "canonical_build_id": "build-fixture",
                "outer_zip_sha256": "b" * 64,
                "outer_zip_bytes": 100,
                "full_index_rows": 330,
                "external_content_rows": 300,
                "withheld_aihub_rows": 30,
                "contains_aihub_source_text": False,
            },
            "verification_tool": {
                "version": "v1.0.0",
                "source_path": "scripts/data/phase4_external_review_intake.py",
                "source_sha256": "d" * 64,
                "package_verifier_source_sha256": "e" * 64,
            },
            "independent_metrics": {
                "candidate_training_projection_mismatches": 0,
                "bazi": {
                    "unique_charts": 25,
                    "identical_disclaimer_rows": 100,
                    "disclaimer_assistant_char_share_percent": 20.28,
                },
                "nemotron": {
                    "target_only_full_birthdate_rows": 4,
                    "target_only_name_rows_local_pattern": 90,
                    "foreign_residue_rows_local_whitelist": 8,
                    "identical_disclaimer_rows": 100,
                    "disclaimer_assistant_char_share_percent": 10.68,
                },
                "yeji": {
                    "unique_assistant_outputs": 30,
                    "particle_error_rows": 10,
                },
            },
            "claim_reproduction": {
                "partially_reproduced": {
                    "nemotron_target_only_name_rows": {
                        "submitted": 88,
                        "independent_local_pattern": 90,
                    },
                    "nemotron_foreign_residue_rows": {
                        "submitted": 7,
                        "independent_local_whitelist": 8,
                    },
                }
            },
        }

    @staticmethod
    def _candidate_rows() -> list[dict]:
        rows: list[dict] = []
        source_offsets = {
            "bazi_sft": 1,
            "nemotron_saju": 101,
            "yeji_bazi_rules": 201,
        }
        for source, offset in source_offsets.items():
            for index in range(100):
                rows.append(
                    {
                        "review_id": f"MIX20K-{offset + index:05d}",
                        "source": source,
                        "total_tokens": index // 2 + 10,
                    }
                )
        return rows

    def test_parser_requires_explicit_ingest_confirmation(self) -> None:
        arguments = intake.build_parser().parse_args(
            [
                "ingest",
                "--bundle-dir",
                "/tmp/bundle",
                "--archive",
                "/tmp/package.zip",
            ]
        )
        self.assertFalse(arguments.confirm_advisory_intake)
        with self.assertRaisesRegex(Phase4Error, "확인 옵션"):
            intake.run(arguments, REPO_ROOT)

    def test_review_identity_matches_approved_submission(self) -> None:
        package = {
            "result": {
                "package_id": intake.EXPECTED_PACKAGE["package_id"],
                "archive_sha256": intake.EXPECTED_PACKAGE["archive_sha256"],
            }
        }
        identity = intake._review_identity(intake.EXPECTED_SUBMITTED_SHA256, package)
        self.assertEqual(identity["sha256"], intake.EXPECTED_REVIEW_SHA256)
        self.assertEqual(identity["review_id"], intake.EXPECTED_REVIEW_ID)

    def test_load_submission_accepts_exact_files_and_reports_missing_newline(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._submission_dir(Path(temporary))
            summary = bundle / "saju_mix20k_external_review_summary.json"
            summary.write_bytes(b"{}")
            result = intake.load_submission(bundle)
        self.assertEqual(set(result["payloads"]), set(intake.SUBMITTED_FILES))
        self.assertEqual(
            result["warnings"],
            ["missing_final_newline:saju_mix20k_external_review_summary.json"],
        )

    def test_load_submission_rejects_extra_file_symlink_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._submission_dir(Path(temporary))
            (bundle / "extra.txt").write_text("extra\n", encoding="utf-8")
            with self.assertRaisesRegex(Phase4Error, "정확히 네 파일"):
                intake.load_submission(bundle)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._submission_dir(root)
            target = root / "outside.json"
            target.write_text("{}\n", encoding="utf-8")
            summary = bundle / "saju_mix20k_external_review_summary.json"
            summary.unlink()
            summary.symlink_to(target)
            with self.assertRaisesRegex(Phase4Error, "symlink"):
                intake.load_submission(bundle)
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._submission_dir(Path(temporary))
            with (
                mock.patch.object(intake, "MAX_SUBMITTED_FILE_BYTES", 1),
                self.assertRaisesRegex(Phase4Error, "크기"),
            ):
                intake.load_submission(bundle)

    def test_load_submission_rejects_pii_control_and_restricted_identifier(
        self,
    ) -> None:
        unsafe_values = (
            "연락처 test@example.com\n",
            "금지\x00문자\n",
            "aihub-talk:0123456789abcdef\n",
        )
        for unsafe in unsafe_values:
            with (
                self.subTest(unsafe=repr(unsafe)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                bundle = self._submission_dir(Path(temporary))
                (bundle / "saju_mix20k_external_review_report.md").write_text(
                    unsafe, encoding="utf-8"
                )
                with self.assertRaises(Phase4Error):
                    intake.load_submission(bundle)

    def test_json_and_finding_count_mismatch_fail_closed(self) -> None:
        with self.assertRaisesRegex(Phase4Error, "JSON"):
            intake._read_json_object(b"{", "fixture")
        summary = {
            "finding_severity_counts": {"high": 1},
            "finding_category_counts": {"schema": 1},
        }
        finding = {
            "review_id": "AGGREGATE-FIXTURE",
            "severity": "low",
            "category": "schema",
            "evidence": {},
            "reason": "검증 이유",
            "recommended_action": "유지",
        }
        with self.assertRaisesRegex(Phase4Error, "severity"):
            intake._validate_findings([finding], summary)

    def test_deterministic_sample_has_exact_strata_and_rejects_duplicate(self) -> None:
        candidates = self._candidate_rows()
        first = intake.deterministic_review_sample(candidates)
        second = intake.deterministic_review_sample(list(reversed(candidates)))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 300)
        strata = {
            (source, decile): sum(
                item["source"] == source and item["token_decile"] == decile
                for item in first
            )
            for source in intake.EXTERNAL_SOURCES
            for decile in range(1, 11)
        }
        self.assertEqual(set(strata.values()), {10})
        duplicate = copy.deepcopy(candidates)
        duplicate[-1]["review_id"] = duplicate[0]["review_id"]
        with self.assertRaisesRegex(Phase4Error, "표본 후보"):
            intake.deterministic_review_sample(duplicate)

    def test_reviewed_rows_reject_duplicate_and_wrong_sequence(self) -> None:
        reviewed = intake.deterministic_review_sample(self._candidate_rows())
        summary = {"semantic_reviewed_rows": 300}
        intake._validate_reviewed_rows(reviewed, summary)
        duplicated = copy.deepcopy(reviewed)
        duplicated[-1]["review_id"] = duplicated[0]["review_id"]
        with self.assertRaisesRegex(Phase4Error, "형식"):
            intake._validate_reviewed_rows(duplicated, summary)

    def test_sidecar_must_match_archive_hash_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "package.zip"
            archive.write_bytes(b"fixture")
            digest = hashlib.sha256(b"fixture").hexdigest()
            sidecar = root / "package.zip.sha256"
            sidecar.write_text(f"{digest}  package.zip\n", encoding="utf-8")
            self.assertEqual(
                intake._verify_sidecar(archive, sidecar, digest), sidecar.resolve()
            )
            sidecar.write_text(f"{'0' * 64}  package.zip\n", encoding="utf-8")
            with self.assertRaisesRegex(Phase4Error, "다릅니다"):
                intake._verify_sidecar(archive, sidecar, digest)

    def test_package_archive_inside_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            archive = repo_root / "package.zip"
            archive.write_bytes(b"not-a-zip")
            with self.assertRaisesRegex(Phase4Error, "저장소 밖"):
                intake.load_package_context(archive, repo_root)

    def test_materialized_review_is_write_once_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            submission_dir = self._submission_dir(repo_root)
            submission = intake.load_submission(submission_dir)
            review_sha256 = "c" * 64
            review_id = f"review-{review_sha256[:12]}"
            report = self._verification_report(review_id, review_sha256)
            artifacts = intake.build_artifacts(submission, report)
            first = intake.materialize_review(repo_root, review_id, artifacts)
            second = intake.materialize_review(repo_root, review_id, artifacts)
            self.assertTrue(first["writes_performed"])
            self.assertFalse(second["writes_performed"])
            target = repo_root / intake.REPORT_RELATIVE_ROOT / review_id
            (target / "owner_assessment.md").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(Phase4Error, "변조"):
                intake.verify_materialized_review(target, artifacts)

    def test_importer_does_not_change_phase4_or_exporter_identity(self) -> None:
        config = {"chat_template": {"path": "configs/chat_templates/kanana.jinja"}}
        self.assertNotIn(
            "scripts/data/phase4_external_review_intake.py",
            _implementation_paths(config),
        )
        exporter = REPO_ROOT / "scripts/data/phase4_export_external_review.py"
        self.assertEqual(
            sha256_file(exporter), intake.EXPECTED_PACKAGE["exporter_source_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
