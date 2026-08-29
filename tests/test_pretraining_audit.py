# test_pretraining_audit.py - 학습 전 의미·출처 감사 계약과 공개 경계를 검증한다.

from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.training.pretraining_audit import (
    PretrainingAuditError,
    _duplicate_summary,
    _parser,
    _walk_public,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/pretraining-audit-v1.0.0.json"
)


class PretrainingAuditTests(unittest.TestCase):
    def test_committed_contract_is_valid(self) -> None:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        result = validate_contract(config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["audit_version"], "v1.0.0")

    def test_public_report_rejects_private_record_fields(self) -> None:
        _walk_public({"axis_counts": {"nemotron_saju": 1}})
        with self.assertRaises(PretrainingAuditError):
            _walk_public({"record_ids": ["private"]})

    def test_duplicate_summary_counts_participating_rows(self) -> None:
        result = _duplicate_summary(
            [
                ("a", "같은 답변"),
                ("a", " 같은   답변 "),
                ("b", "다른 답변"),
            ],
            Counter({"a": 2, "b": 1}),
        )
        self.assertEqual(result["participating_rows_by_axis"], {"a": 2})
        self.assertEqual(result["maximum_multiplicity"], 2)
        self.assertEqual(result["cross_axis_duplicate_groups"], 0)

    def test_run_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["run"])
        self.assertFalse(args.execute)


if __name__ == "__main__":
    unittest.main()
