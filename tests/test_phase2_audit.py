# test_phase2_audit.py - Phase 2A 버전 fingerprint, 누수 그룹, 검토·승인 Gate 회귀 테스트

from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.data.audit_tools import (
    ReviewQueueBuilder,
    _decision_map,
    _validate_decisions,
    append_review_decision,
    apply_yeji_corrections,
    assert_public_report_safe,
    audit_status_from_values,
    canonical_chart_from_bazi,
    canonical_chart_from_nemotron,
    compute_build_identity,
    evaluate_gate,
    leakage_group_id,
    review_summary,
    sha256_json,
    verify_source_bundle,
    write_json_exclusive,
    write_jsonl_exclusive,
)
from scripts.data.errors import Phase2AuditError


def _candidate(index: int) -> dict[str, object]:
    return {
        "source": "fixture",
        "stable_key": f"stable-{index}",
        "locator": {"kind": "json_list", "path": "private.json", "row_index": index},
        "flags": [],
    }


class ChartContractTests(unittest.TestCase):
    def test_nemotron_and_bazi_share_global_leakage_group(self) -> None:
        hanja = {
            "year": {"stem_hanja": "甲", "branch_hanja": "子"},
            "month": {"stem_hanja": "乙", "branch_hanja": "丑"},
            "day": {"stem_hanja": "丙", "branch_hanja": "寅"},
            "hour": {"stem_hanja": "丁", "branch_hanja": "卯"},
        }
        pinyin = {
            "pillars": {
                "year": {"stem": "Jia", "branch": "Zi"},
                "month": {"stem": "Yi", "branch": "Chou"},
                "day": {"stem": "Bing", "branch": "Yin"},
                "hour": {"stem": "Ding", "branch": "Mao"},
            }
        }
        left = canonical_chart_from_nemotron(json.dumps(hanja, ensure_ascii=False))
        right = canonical_chart_from_bazi(pinyin)
        self.assertEqual(left, "甲子乙丑丙寅丁卯")
        self.assertEqual(left, right)
        self.assertEqual(
            leakage_group_id("chart", left), leakage_group_id("chart", right)
        )

    def test_invalid_stem_fails_closed(self) -> None:
        invalid = {
            name: {"stem_hanja": "X", "branch_hanja": "子"}
            for name in ("year", "month", "day", "hour")
        }
        with self.assertRaises(ValueError):
            canonical_chart_from_nemotron(invalid)


class FingerprintTests(unittest.TestCase):
    def test_build_fingerprint_does_not_depend_on_absolute_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = []
            bundle = {"source_build_sha256": "a" * 64}
            policy = {
                "audit_version": "v1.0.0",
                "dataset_name": "saju_1b_baseline",
                "seed": 42,
            }
            for name in ("left", "right"):
                current = root / name
                current.mkdir()
                policy_path = current / "policy.json"
                policy_path.write_text('{"same":true}\n', encoding="utf-8")
                code_paths = []
                for filename in ("audit_tools.py", "phase2_audit.py"):
                    path = current / filename
                    path.write_text("# identical\n", encoding="utf-8")
                    code_paths.append(path)
                results.append(
                    compute_build_identity(policy, policy_path, bundle, code_paths)
                )
            self.assertEqual(results[0], results[1])

    def test_source_bundle_detects_manifest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            names = (
                "nemotron_saju",
                "bazi_sft",
                "aihub_empathy",
                "yeji_bazi_rules",
            )
            for index, name in enumerate(names):
                manifest = root / "data" / "raw" / name / "rev" / "SOURCE_MANIFEST.json"
                manifest.parent.mkdir(parents=True)
                payload = {
                    "source": name,
                    "revision": f"rev-{index}",
                    "files": [{"path": "x"}],
                }
                manifest.write_text(json.dumps(payload), encoding="utf-8")
                sources.append(
                    {
                        "source": name,
                        "revision": f"rev-{index}",
                        "manifest_path": manifest.relative_to(root).as_posix(),
                        "manifest_sha256": hashlib.sha256(
                            manifest.read_bytes()
                        ).hexdigest(),
                    }
                )
            bundle = {
                "dataset_name": "saju_1b_baseline",
                "schema_version": "1.0.0",
                "sources": sources,
                "version": "v1.0.0",
            }
            bundle["source_build_sha256"] = sha256_json(bundle)
            bundle_path = root / "bundle.json"
            bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
            verified, _ = verify_source_bundle(root, bundle_path)
            self.assertEqual(
                verified["source_build_sha256"], bundle["source_build_sha256"]
            )
            first_manifest = root / sources[0]["manifest_path"]
            first_manifest.write_text("{}", encoding="utf-8")
            with self.assertRaises(Phase2AuditError):
                verify_source_bundle(root, bundle_path)


class ReviewQueueTests(unittest.TestCase):
    def test_queue_is_deterministic_and_locator_disjoint(self) -> None:
        left = ReviewQueueBuilder(seed=42)
        right = ReviewQueueBuilder(seed=42)
        values = [_candidate(index) for index in range(5)]
        left.add_singles("required", "fixture", "random", values, 3)
        right.add_singles("required", "fixture", "random", list(reversed(values)), 3)
        self.assertEqual(left.items, right.items)
        with self.assertRaises(Phase2AuditError):
            left.add_singles("reference", "fixture", "too_many", values, 3)

    def test_public_summary_never_contains_locator(self) -> None:
        builder = ReviewQueueBuilder(seed=42)
        builder.add_singles("required", "fixture", "sample", [_candidate(1)], 1)
        summary = review_summary(builder.items)
        assert_public_report_safe(summary)
        rendered = json.dumps(summary)
        self.assertNotIn("private.json", rendered)
        with self.assertRaises(Phase2AuditError):
            assert_public_report_safe({"content": "private sentence"})


class YejiCorrectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = {
            "shensha_list": [
                {
                    "id": 11,
                    "condition": {"mapping": {"金": {"간지": "壬卯"}}},
                },
                {"id": 19, "category": "흉살류"},
            ]
        }
        self.manifest = {
            "corrections": [
                {
                    "correction_id": "ciguan",
                    "rule_id": 11,
                    "field_path": ["condition", "mapping", "金", "간지"],
                    "expected_original": "壬卯",
                    "replacement": "壬申",
                    "resolves": ["YEJI_CIGUAN_CONFLICT"],
                },
                {
                    "correction_id": "wugui",
                    "rule_id": 19,
                    "field_path": ["category"],
                    "expected_original": "흉살류",
                    "replacement": "재앙류",
                    "resolves": ["YEJI_STRUCTURE_FAILURE"],
                },
            ]
        }

    def test_overlay_preserves_raw_and_applies_exact_values(self) -> None:
        corrected, applied = apply_yeji_corrections(self.document, self.manifest)
        self.assertEqual(
            corrected["shensha_list"][0]["condition"]["mapping"]["金"]["간지"],
            "壬申",
        )
        self.assertEqual(corrected["shensha_list"][1]["category"], "재앙류")
        self.assertEqual(
            self.document["shensha_list"][0]["condition"]["mapping"]["金"]["간지"],
            "壬卯",
        )
        self.assertEqual(len(applied), 2)

    def test_overlay_fails_when_expected_original_drifted(self) -> None:
        self.document["shensha_list"][1]["category"] = "다른값"
        with self.assertRaises(Phase2AuditError):
            apply_yeji_corrections(self.document, self.manifest)


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = [
            {"review_id": "one", "queue": "required"},
            {"review_id": "two", "queue": "required"},
            {"review_id": "ref", "queue": "reference"},
        ]

    @staticmethod
    def decision(review_id: str, value: str) -> dict[str, str]:
        return {"review_id": review_id, "decision": value}

    def test_incomplete_and_uncertain_reviews_block(self) -> None:
        incomplete = evaluate_gate(self.queue, [self.decision("one", "accept")], [])
        self.assertEqual(incomplete["gate_status"], "human_review_incomplete")
        uncertain = evaluate_gate(
            self.queue,
            [self.decision("one", "accept"), self.decision("two", "uncertain")],
            [],
        )
        self.assertEqual(uncertain["gate_status"], "human_review_unresolved")

    def test_findings_and_exclusions_have_distinct_gate_states(self) -> None:
        accepted = [self.decision("one", "accept"), self.decision("two", "accept")]
        self.assertEqual(
            evaluate_gate(self.queue, accepted, ["RULE_CONFLICT"])["gate_status"],
            "blocked",
        )
        excluded = [
            self.decision("one", "accept"),
            self.decision("two", "exclude_candidate"),
        ]
        self.assertEqual(
            evaluate_gate(self.queue, excluded, [])["gate_status"],
            "ready_for_approval_with_exclusions",
        )


class DecisionRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = [{"review_id": "one", "queue": "required"}]
        self.policy = {
            "decision_schema_version": "1.1.0",
            "decision_values": ["accept", "exclude_candidate"],
            "reason_codes": ["low_quality", "other"],
        }

    def test_latest_revision_drives_status_without_overwriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            path.write_bytes(b"")
            first = append_review_decision(
                path,
                self.queue,
                self.policy,
                review_id="one",
                decision="accept",
                reason_code=None,
                private_note=None,
                reviewer_version="reviewer-v1.0.0",
                review_tool_sha256="a" * 64,
            )
            second = append_review_decision(
                path,
                self.queue,
                self.policy,
                review_id="one",
                decision="exclude_candidate",
                reason_code="low_quality",
                private_note=None,
                reviewer_version="reviewer-v1.0.0",
                review_tool_sha256="a" * 64,
            )
            decisions = [json.loads(line) for line in path.read_text().splitlines()]
            _validate_decisions(decisions, self.policy)
            self.assertEqual(second["revision"], 2)
            self.assertEqual(second["supersedes_decision_id"], first["decision_id"])
            self.assertEqual(_decision_map(decisions)["one"], second)
            status = audit_status_from_values(self.queue, decisions)
            self.assertEqual(status["required_completed"], 1)
            self.assertEqual(status["decision_history_entries"], 2)
            self.assertEqual(status["decisions"], {"exclude_candidate": 1})

    def test_tampered_decision_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "decisions.jsonl"
            path.write_bytes(b"")
            decision = append_review_decision(
                path,
                self.queue,
                self.policy,
                review_id="one",
                decision="accept",
                reason_code=None,
                private_note=None,
                reviewer_version="reviewer-v1.0.0",
                review_tool_sha256="b" * 64,
            )
            decision["decision_id"] = "0" * 24
            with self.assertRaises(Phase2AuditError):
                _validate_decisions([decision], self.policy)


class PrivateFileTests(unittest.TestCase):
    def test_exclusive_writes_are_private_and_never_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "manifest.json"
            jsonl_path = root / "queue.jsonl"
            write_json_exclusive(json_path, {"ok": True}, 0o600)
            write_jsonl_exclusive(jsonl_path, [{"id": 1}], 0o600)
            self.assertEqual(stat.S_IMODE(json_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(jsonl_path.stat().st_mode), 0o600)
            with self.assertRaises(Phase2AuditError):
                write_json_exclusive(json_path, {"ok": False}, 0o600)


if __name__ == "__main__":
    unittest.main()
