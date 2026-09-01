# test_phase6_technical.py - Phase 6 단회 실행·봉인·공개 경계 계약을 검증한다.

from __future__ import annotations

import inspect
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import phase6_technical as technical

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = (
    REPO_ROOT
    / "configs/model_versions/saju_1b_baseline/phase6-technical-evaluation-v1.0.0.json"
)


class Phase6TechnicalTests(unittest.TestCase):
    def test_canonical_docs_forbid_person_dependent_gates(self) -> None:
        roots = (
            REPO_ROOT / "implementation/contracts",
            REPO_ROOT / "implementation/plans",
            REPO_ROOT / "docs",
        )
        paths = sorted(
            path
            for root in roots
            for path in root.rglob("*")
            if path.suffix in {".md", ".html"}
            and "archive" not in path.parts
            and "history" not in path.parts
        )
        forbidden = re.compile(
            r"검수|사람|전문가|팀원|reviewer|human_domain|domain_item_review|"
            r"주관|수동|독립\s*(?:평가|검수)|KEEP/EDIT/DROP|선호\s*평가|expert",
            re.IGNORECASE,
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(forbidden.search(text), path.as_posix())
        top_level = (
            REPO_ROOT / "implementation/plans/saju_1b_10k_20k_baseline/README.md"
        ).read_text(encoding="utf-8")
        phase_zero = (
            REPO_ROOT
            / "implementation/plans/saju_1b_10k_20k_baseline/phase-0-governance.md"
        ).read_text(encoding="utf-8")
        phase_six = (
            REPO_ROOT
            / "implementation/plans/saju_1b_10k_20k_baseline/phase-6-evaluation-v2-decision.md"
        ).read_text(encoding="utf-8")
        for text in (top_level, phase_zero, phase_six):
            self.assertIn("not_measured", text)
            self.assertIn("자동 기술", text)

    def test_mix20k_v3_uses_automatic_gate_overlay(self) -> None:
        path = (
            REPO_ROOT
            / "configs/data_versions/saju_1b_baseline/mix20k-v3-automatic-gates-v1.0.0.json"
        )
        value = technical._load_json(path, "MIX20K-v3 automatic gate")
        parent = value["parent_candidate"]
        self.assertEqual(
            technical._sha256_file(REPO_ROOT / parent["config"]),
            parent["config_sha256"],
        )
        self.assertEqual(
            value["decision_policy"]["inputs"],
            "repository_local_automatic_metrics_only",
        )
        self.assertEqual(
            value["decision_policy"]["unavailable_semantics"], "not_measured"
        )
        self.assertFalse(value["promotion"]["training_promotion_allowed"])

    def test_repository_contract_is_valid(self) -> None:
        config = technical._load_json(CONFIG, "Phase 6 config")
        result = technical.validate_contract(config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["blind_runs"], 1)
        self.assertEqual(
            result["decision_inputs"], "repository_local_automatic_metrics_only"
        )
        self.assertFalse(config["generation"]["fix_mistral_regex"])

    def test_strict_json_rejects_duplicate_keys(self) -> None:
        with self.assertRaises(technical.Phase6TechnicalError):
            technical._strict_loads('{"a":1,"a":2}', "duplicate")

    def test_safe_path_rejects_escape_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real").mkdir()
            (root / "link").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(technical.Phase6TechnicalError):
                technical._safe_path(root, "../outside")
            with self.assertRaises(technical.Phase6TechnicalError):
                technical._safe_path(root, "link/file.json")

    def test_write_once_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marker.json"
            technical._write_once(path, b"one\n", mode=0o600)
            technical._write_once(path, b"one\n", mode=0o600)
            with self.assertRaises(technical.Phase6TechnicalError):
                technical._write_once(path, b"two\n", mode=0o600)

    def test_preflight_source_does_not_open_blind_payload(self) -> None:
        source = inspect.getsource(technical.preflight)
        self.assertNotIn("_read_blind_rows", source)
        self.assertNotIn("_sha256_file(blind_path)", source)

    def test_git_worktree_allows_only_same_fingerprint_public_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_root = root / "private"
            public_root = root / "reports" / "eval-111111111111"
            marker = root / "seal" / "consumption.json"
            identity = {
                "schema_version": "1.0.0",
                "evaluation_build_id": "eval-111111111111",
            }
            started = {**identity, "status": "spent_in_progress"}
            technical._write_once(
                marker, technical._json_bytes(started), mode=0o600
            )
            technical._write_once(
                private_root / "blind_access_started.json",
                technical._json_bytes(started),
                mode=0o600,
            )
            context = {
                "config": {
                    "blind_source": {"consumption_marker": "seal/consumption.json"}
                },
                "private_root": private_root,
                "public_root": public_root,
            }
            clean_resume = (
                "?? reports/eval-111111111111/aggregate.json\n"
                "?? reports/eval-111111111111/decision.md"
            )
            with (
                patch.object(technical, "_git_output", return_value=clean_resume),
                patch.object(
                    technical, "_consumption_identity", return_value=identity
                ),
            ):
                self.assertEqual(
                    technical._validate_git_worktree(
                        context, root, allow_resume=True
                    ),
                    "same_fingerprint_resume",
                )
            with (
                patch.object(
                    technical,
                    "_git_output",
                    return_value="?? implementation/unrelated.md",
                ),
                self.assertRaises(technical.Phase6TechnicalError),
            ):
                technical._validate_git_worktree(context, root, allow_resume=True)

    def test_consumption_marker_allows_only_same_fingerprint_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "private").mkdir(mode=0o700)
            context = {
                "config": {
                    "blind_source": {"consumption_marker": "seal/consumption.json"}
                },
                "private_root": root / "private",
            }
            first = {
                "schema_version": "1.0.0",
                "evaluation_id": "phase6-technical-evaluation",
                "evaluation_version": "v1.0.0",
                "evaluation_build_id": "eval-111111111111",
                "build_sha256": "1" * 64,
                "config_sha256": "2" * 64,
                "implementation_hashes": {"a": "3" * 64},
                "model_file_hashes": {"K0": {"model": "4" * 64}},
                "blind_source_sha256": "5" * 64,
                "git_commit": "6" * 40,
            }
            with patch.object(technical, "_consumption_identity", return_value=first):
                technical._begin_blind_consumption(context, root)
                technical._begin_blind_consumption(context, root)
            changed = {**first, "evaluation_build_id": "eval-222222222222"}
            with (
                patch.object(technical, "_consumption_identity", return_value=changed),
                self.assertRaises(technical.Phase6TechnicalError),
            ):
                technical._begin_blind_consumption(context, root)

    def _blind_row(
        self, *, axis: str, index: int, component: str
    ) -> dict[str, object]:
        return {
            "schema_version": "2.0.0",
            "split_role": "blind_source_test",
            "sealed": True,
            "source_axis": axis,
            "eval_id": f"eval-{axis}-{index}",
            "cases": [
                {
                    "case_id": f"case-{axis}-{index}",
                    "prompt_messages": [
                        {"role": "user", "content": "고정 입력입니다."}
                    ],
                    "reference_assistant": "고정 응답입니다.",
                }
            ],
            "parents": [
                {
                    "leakage_component_id": component,
                    "mix_axis": axis,
                }
            ],
        }

    def test_blind_identity_and_component_contract(self) -> None:
        expected = {axis: 1 for axis in technical.AXES}
        expected["bazi_sft"] = 4
        rows = []
        for axis in technical.AXES:
            count = expected[axis]
            for index in range(count):
                component = "bazi-component" if axis == "bazi_sft" else f"{axis}-component"
                rows.append(self._blind_row(axis=axis, index=index, component=component))
        context = {
            "evaluation_build_id": "eval-111111111111",
            "config": {
                "blind_source": {
                    "rows": len(rows),
                    "expected_rows_by_axis": expected,
                    "components_per_axis": 1,
                    "bazi_rows_per_component": 4,
                }
            },
        }
        cases = technical._validate_blind_rows(rows, context)
        self.assertEqual(len(cases), 10)
        self.assertEqual(len({case["case_key"] for case in cases}), 10)
        self.assertEqual(len({case["component_key"] for case in cases}), 7)

    def test_duplicate_blind_case_fails_closed(self) -> None:
        row = self._blind_row(axis="nemotron_saju", index=0, component="component")
        context = {
            "evaluation_build_id": "eval-111111111111",
            "config": {
                "blind_source": {
                    "rows": 2,
                    "expected_rows_by_axis": {"nemotron_saju": 2},
                    "components_per_axis": 1,
                    "bazi_rows_per_component": 4,
                }
            },
        }
        with self.assertRaises(technical.Phase6TechnicalError):
            technical._validate_blind_rows([row, row], context)

    def test_public_leak_scan_rejects_raw_fields_and_private_paths(self) -> None:
        with self.assertRaises(technical.Phase6TechnicalError):
            technical._public_leak_scan({"prompt_messages": []})
        with self.assertRaises(technical.Phase6TechnicalError):
            technical._public_leak_scan({"path": "data/derived/private.jsonl"})
        technical._public_leak_scan(
            {"status": "completed", "domain_semantics": "not_measured"}
        )

    def test_dry_run_does_not_change_consumption_markers(self) -> None:
        context = technical.prepare_context(REPO_ROOT, CONFIG)
        markers = (
            technical._safe_path(
                REPO_ROOT, context["config"]["blind_source"]["consumption_marker"]
            ),
            context["private_root"] / "blind_access_started.json",
            context["private_root"] / "blind_access_completed.json",
        )
        before = {
            path: (
                path.read_bytes() if path.exists() else None,
                path.stat().st_mtime_ns if path.exists() else None,
            )
            for path in markers
        }
        with patch.object(
            technical,
            "_read_blind_rows",
            side_effect=AssertionError("dry-run에서 blind payload를 읽었습니다."),
        ):
            self.assertEqual(
                technical.main(["--config", str(CONFIG), "execute"]),
                0,
            )
        after = {
            path: (
                path.read_bytes() if path.exists() else None,
                path.stat().st_mtime_ns if path.exists() else None,
            )
            for path in markers
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
