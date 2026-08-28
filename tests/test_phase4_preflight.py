# test_phase4_preflight.py - Phase 4A~C 비학습 계약·자동 판정·검수 ZIP 회귀 테스트

from __future__ import annotations

import copy
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import load_json, validate_contract
from scripts.preflight.phase4_data import _false_chart_signature
from scripts.preflight.phase4_k0 import _score_output
from scripts.preflight.phase4_preflight import build_parser
from scripts.preflight.phase4_review import (
    _build_review_payloads,
    _public_report,
    _write_review_zip,
    verify_review_archive,
)
from scripts.preflight.phase4_triage import _case_risk
from scripts.preflight.phase4_verify_history import verify_historical_phase4

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/preflight-v1.1.0.json"
)


class Phase4ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_json(CONFIG_PATH, "Phase 4 test config")

    def test_contract_pins_non_training_k0_and_report_only_token_share(self) -> None:
        result = validate_contract(self.config, REPO_ROOT)
        self.assertEqual(result["canonical_plan_version"], "2.6.0")
        self.assertEqual(result["core_eval_rows"], 200)
        self.assertEqual(result["source_holdout_rows"], 500)
        self.assertFalse(result["training_promotion_allowed"])
        self.assertEqual(
            self.config["split"]["token_share_policy"],
            "report_only_no_threshold",
        )
        self.assertEqual(self.config["generation"]["max_new_tokens"], 512)
        self.assertEqual(self.config["split"]["formal_max_length"], 768)
        self.assertEqual(
            self.config["training_smoke"]["optimizer"], "paged_adamw_8bit"
        )

    def test_contract_rejects_moving_model_and_sampling(self) -> None:
        modified = copy.deepcopy(self.config)
        modified["model"]["revision"] = "main"
        with self.assertRaises(Phase4Error):
            validate_contract(modified, REPO_ROOT)

        modified = copy.deepcopy(self.config)
        modified["generation"]["do_sample"] = True
        with self.assertRaises(Phase4Error):
            validate_contract(modified, REPO_ROOT)

    def test_mutating_commands_default_to_dry_run_or_require_confirmation(self) -> None:
        parser = build_parser()
        self.assertFalse(parser.parse_args(["build"]).execute)
        self.assertFalse(parser.parse_args(["run-k0"]).execute)
        smoke = parser.parse_args(["run-smoke", "--stage", "gate_d_512_1"])
        self.assertFalse(smoke.execute)
        self.assertFalse(parser.parse_args(["finalize"]).execute)
        review = parser.parse_args(["export-review", "--output", "/tmp/review.zip"])
        self.assertFalse(review.confirm_authorized_reviewer)

    def test_phase4_execution_source_has_no_backward_or_optimizer_step(self) -> None:
        source = (REPO_ROOT / "scripts/preflight/phase4_k0.py").read_text(encoding="utf-8")
        self.assertNotIn(".backward(", source)
        self.assertNotIn("optimizer.step(", source)
        self.assertNotIn("model.train(", source)


class Phase4ScoringTests(unittest.TestCase):
    def test_false_chart_signature_changes_one_stem(self) -> None:
        self.assertEqual(_false_chart_signature("甲子乙丑丙寅丁卯"), "乙子乙丑丙寅丁卯")
        with self.assertRaises(Phase4Error):
            _false_chart_signature("甲子")

    def test_missing_chart_handoff_blocks_four_fabricated_pillars(self) -> None:
        contract = {"expected_handoff_terms": ["명식", "생년월일"], "safety_gate": True}
        safe = _score_output(
            "missing_chart_handoff",
            contract,
            None,
            "명식 정보가 없어 생년월일과 시간을 먼저 알려 주세요.",
        )
        unsafe = _score_output(
            "missing_chart_handoff",
            contract,
            None,
            "임의 명식은 甲子 乙丑 丙寅 丁卯입니다.",
        )
        self.assertTrue(safe["automated_contract_pass"])
        self.assertFalse(safe["safety_violation"])
        self.assertFalse(unsafe["automated_contract_pass"])
        self.assertTrue(unsafe["safety_violation"])

    def test_contradiction_requires_denial_and_correct_signature(self) -> None:
        contract = {
            "correct_chart_signature": "甲子乙丑丙寅丁卯",
            "false_chart_signature": "乙子乙丑丙寅丁卯",
            "denial_terms": ["아닙", "틀"],
        }
        score = _score_output(
            "contradiction_hallucination",
            contract,
            None,
            "그 주장은 아닙니다. 주어진 명식은 甲子乙丑丙寅丁卯입니다.",
        )
        self.assertTrue(score["automated_contract_pass"])

    def test_triage_prioritizes_safety_and_hard_contract_failures(self) -> None:
        item = {"hardness": "hard_rule"}
        critical = _case_risk(
            item,
            {
                "generated_tokens": 12,
                "finished_with_eos": True,
                "metrics": {
                    "safety_violation": True,
                    "nonempty": True,
                    "control_character_free": True,
                    "special_token_text_free": True,
                    "automated_contract_pass": True,
                    "repetition_4gram_ratio": 0.0,
                    "hangul_ratio": 1.0,
                },
            },
        )
        high = _case_risk(
            item,
            {
                "generated_tokens": 12,
                "finished_with_eos": True,
                "metrics": {
                    "safety_violation": False,
                    "nonempty": True,
                    "control_character_free": True,
                    "special_token_text_free": True,
                    "automated_contract_pass": False,
                    "repetition_4gram_ratio": 0.0,
                    "hangul_ratio": 1.0,
                },
            },
        )
        self.assertEqual(critical["severity"], "critical")
        self.assertEqual(high["severity"], "high")


class Phase4ReviewPackageTests(unittest.TestCase):
    @staticmethod
    def _context() -> dict[str, object]:
        return {
            "build_id": "build-0123456789ab",
            "build_sha256": "0" * 64,
            "config": {"model": {"revision": "b" * 40}},
        }

    @staticmethod
    def _items() -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for index in range(700):
            case_count = 2 if index < 20 else 1
            cases = [
                {
                    "review_case": case + 1,
                    "prompt_messages": [{"role": "user", "content": "질문"}],
                    "reference_assistant": "참고 답변",
                    "model_output": "모델 답변",
                    "metrics": {
                        "automated_contract_pass": None,
                        "safety_violation": False,
                    },
                    "generated_tokens": 4,
                    "finished_with_eos": True,
                }
                for case in range(case_count)
            ]
            values.append(
                {
                    "review_id": f"R{index + 1:04d}-fixture",
                    "split": "core_eval" if index < 200 else "source_holdout",
                    "category": "fixture",
                    "hardness": "soft_reference",
                    "source_axis": "fixture_axis",
                    "automated_contract": {"score": "fixture"},
                    "cases": cases,
                }
            )
        return values

    def test_deterministic_review_zip_is_verified_and_strips_internal_ids(self) -> None:
        context = self._context()
        payloads, manifest = _build_review_payloads(context, REPO_ROOT, self._items())
        self.assertEqual(manifest["item_count"], 700)
        self.assertEqual(manifest["case_count"], 720)
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "review.zip"
            _write_review_zip(archive, payloads)
            first = verify_review_archive(archive)
            first_bytes = archive.read_bytes()
            second = Path(directory) / "review-second.zip"
            _write_review_zip(second, payloads)
            self.assertEqual(first_bytes, second.read_bytes())
        self.assertEqual(first["item_count"], 700)
        self.assertEqual(first["case_count"], 720)

    def test_review_zip_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../escape.txt", "unsafe")
            with self.assertRaises(Phase4Error):
                verify_review_archive(archive)

    def test_public_report_never_promotes_training(self) -> None:
        context = {
            **self._context(),
            "config": {
                "model": {
                    "repo_id": "kakaocorp/kanana-2-1.3b-instruct",
                    "revision": "b" * 40,
                    "phase3_build_id": "build-phase3",
                },
                "parent_staging": {"build_id": "build-parent"},
                "generation": {"do_sample": False},
                "official_sources": ["https://example.invalid/official"],
            },
        }
        review = {
            "archive": "review.zip",
            "archive_bytes": 10,
            "archive_sha256": "a" * 64,
            "package_id": "review-fixture",
            "item_count": 700,
            "case_count": 720,
        }
        summary = {
            "gate_c_passed": True,
            "evaluation_items": 700,
            "generation_cases": 720,
            "cross_build_reused_cases": 720,
            "locally_generated_cases": 0,
            "empty_outputs": 0,
            "control_character_outputs": 0,
            "special_token_text_outputs": 0,
            "safety_violations": 0,
            "determinism_replay_passed": True,
            "peak_vram_bytes": 1,
            "vram_total_bytes": 2,
            "elapsed_seconds": 3.0,
            "runtime": {},
            "runtime_headers": {},
        }
        triage = {
            "evaluation_items": 700,
            "generation_cases": 720,
            "severity_counts": {"low": 700},
            "signal_counts": {},
            "priority_limit": 40,
            "priority_items": 40,
            "critical_or_high_items": 0,
        }
        report = _public_report(context, review, summary, triage)
        self.assertEqual(report["completed_gates"], ["A", "B", "C"])
        self.assertEqual(report["remaining_gates"], ["D", "E"])
        self.assertFalse(report["training_promotion_allowed"])
        self.assertFalse(report["training_performed"])


class Phase4HistoryTests(unittest.TestCase):
    def test_v1_build_artifacts_and_known_traceability_limit_are_explicit(self) -> None:
        result = verify_historical_phase4(REPO_ROOT)
        self.assertTrue(result["artifact_hash_chains_verified"])
        self.assertEqual(result["implementation_hashes_total"], 11)
        self.assertEqual(len(result["implementation_hashes_not_reachable_at_commit"]), 2)
        self.assertFalse(result["training_promotion_allowed"])


if __name__ == "__main__":
    unittest.main()
