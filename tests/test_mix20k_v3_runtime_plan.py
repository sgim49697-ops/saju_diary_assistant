# test_mix20k_v3_runtime_plan.py - v3.1 읽기 전용 이관 보고서의 고정 수량·차단 상태를 검증한다.

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.data.mix20k_v3_runtime_plan import (
    CONFORMANCE_ROOT,
    RuntimeMigrationPlanError,
    _runtime_gate,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT = (
    REPO_ROOT
    / "data/reports/saju_runtime_migration/v1.0.0/build-94eb7b543490/analysis.json"
)
GATE_REPORT = next(CONFORMANCE_ROOT.glob("build-*/aggregate.json"))


class Mix20kV3RuntimePlanTests(unittest.TestCase):
    def test_committed_runtime_gate_has_current_identity_and_stays_blocked(
        self,
    ) -> None:
        gate = _runtime_gate(GATE_REPORT)
        self.assertTrue(gate["provided"])
        self.assertFalse(gate["passed"])

    def test_runtime_gate_requires_matching_build_manifest(self) -> None:
        with tempfile.TemporaryDirectory(dir=CONFORMANCE_ROOT) as temporary:
            root = Path(temporary)
            aggregate = root / "aggregate.json"
            aggregate.write_bytes(GATE_REPORT.read_bytes())
            (root / "build_manifest.json").write_text(
                json.dumps(
                    {
                        "report_type": "saju_runtime_conformance_v2",
                        "build_id": root.name,
                        "aggregate_sha256": "0" * 64,
                        "runtime_gate_passed": False,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeMigrationPlanError, "identity"):
                _runtime_gate(aggregate)

    def test_committed_analysis_requires_all_tool_calls_and_preserves_20k(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        implementation = report["inputs"]["implementation"]
        self.assertEqual(
            implementation["sha256"],
            hashlib.sha256(
                (REPO_ROOT / implementation["path"]).read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            report["observed"]["tool_call_counts"],
            {
                "calculate_saju_chart": 4350,
                "calculate_saju_period": 900,
            },
        )
        self.assertEqual(report["observed"]["foreign_chart_rows"], 200)
        self.assertEqual(report["observed"]["hard_candidate_rows"], 3800)
        migration = report["migration_contract"]
        self.assertEqual(migration["foreign_replace_with_kr"], 180)
        self.assertEqual(migration["foreign_convert_to_unsupported_region"], 20)
        self.assertEqual(migration["preserve_total_rows"], 20_000)
        self.assertEqual(migration["regenerate_chart_tool_calls"], 4350)
        self.assertEqual(migration["regenerate_period_tool_calls"], 900)

    def test_runtime_gate_blocks_regeneration_and_training(self) -> None:
        report = json.loads(REPORT.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "blocked_runtime_gate_pending")
        self.assertFalse(report["runtime_gate"]["passed"])
        self.assertFalse(report["regeneration_allowed"])
        self.assertFalse(report["training_promotion_allowed"])
        self.assertFalse(report["phase5_training_performed"])
        self.assertFalse(report["sealed_blind_accessed"])
        self.assertFalse(report["raw_rows_in_report"])


if __name__ == "__main__":
    unittest.main()
