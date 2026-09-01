# test_historical_candidate_gate.py - 120건 후보 Gate의 계약·공개 보고서·재현성을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluation.saju_runtime.historical_candidate_gate import (
    CONFIG_PATH,
    REPORT_ROOT,
    _parser,
    evaluate,
    validate_contract,
    verify_report,
    write_report,
)
from scripts.runtime.calculation.contracts import REPO_ROOT

BUILD_ID = "build-5b80bfb2b7b9"
BUILD_ROOT = REPORT_ROOT / BUILD_ID
EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


class HistoricalCandidateGateTests(unittest.TestCase):
    def test_contract_and_plan_are_fixed_to_twelve_by_ten(self) -> None:
        config = validate_contract(CONFIG_PATH)
        self.assertEqual(
            config["dashboard_id"], "saju-historical-candidate-dashboard-v1.0.0"
        )
        args = _parser().parse_args(["plan"])
        self.assertEqual(args.command, "plan")
        gate = json.loads(
            (
                REPO_ROOT / "configs/runtime/historical_candidate_gate-v1.0.0.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(len(gate["strata"]), 12)
        self.assertEqual(sum(gate["strata"].values()), 120)

    def test_committed_report_verifies_as_diagnostic_only(self) -> None:
        result = verify_report(BUILD_ROOT, CONFIG_PATH)
        self.assertEqual(result["build_id"], BUILD_ID)
        self.assertTrue(result["diagnostic_target_met"])
        self.assertFalse(result["runtime_release_approved"])
        self.assertFalse(result["production_application_binding"])

    def test_public_report_contains_no_case_input_or_internal_ids(self) -> None:
        aggregate_path = BUILD_ROOT / "aggregate.json"
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
        encoded = aggregate_path.read_text(encoding="utf-8")
        self.assertEqual((aggregate["cases"], aggregate["passed"]), (120, 120))
        self.assertTrue(all(aggregate["gate_checks"].values()))
        self.assertTrue(aggregate["governance"]["diagnostic_dashboard_binding"])
        for field in (
            "production_application_binding",
            "runtime_release_approved",
            "context_window_changed",
            "mix20k_v3_1_generated",
            "additional_training_performed",
            "model_promotion_performed",
            "sealed_blind_accessed",
        ):
            self.assertFalse(aggregate["governance"][field])
        for forbidden in (
            '"birth_date":',
            '"birth_time":',
            '"normalized_input":',
            '"internal_trace":',
            '"chart_id":',
            '"chart_set_id":',
        ):
            self.assertNotIn(forbidden, encoded)

    def test_write_is_reproducible_from_committed_aggregate_core(self) -> None:
        aggregate = json.loads(
            (BUILD_ROOT / "aggregate.json").read_text(encoding="utf-8")
        )
        aggregate.pop("build_id")
        with tempfile.TemporaryDirectory() as temporary:
            output = write_report(aggregate, Path(temporary))
            self.assertEqual(output.name, BUILD_ID)
            self.assertEqual(
                (output / "aggregate.json").read_bytes(),
                (BUILD_ROOT / "aggregate.json").read_bytes(),
            )

    @unittest.skipUnless(EPHEMERIS.is_file(), "로컬 Git 제외 DE440s가 필요합니다.")
    def test_real_de440s_reproduces_committed_aggregate(self) -> None:
        report = evaluate(EPHEMERIS, CONFIG_PATH)
        with tempfile.TemporaryDirectory() as temporary:
            output = write_report(report, Path(temporary))
            self.assertEqual(output.name, BUILD_ID)
            self.assertEqual(
                (output / "aggregate.json").read_bytes(),
                (BUILD_ROOT / "aggregate.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
