# test_chart_only_dashboard_canary.py - dashboard v1.9 계약과 aggregate-only report를 회귀 검증한다.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.evaluation.saju_runtime.chart_only_dashboard_canary import (
    verify_report,
    write_report,
)
from scripts.runtime.chart_only_dashboard_contracts import (
    EXPECTED_CHECKS,
    EXPECTED_STRATA,
    validate_dashboard_operations_registry,
)


class ChartOnlyDashboardCanaryTests(unittest.TestCase):
    def test_registry_and_public_report_round_trip(self) -> None:
        registry = validate_dashboard_operations_registry(require_dependencies=True)
        self.assertEqual(
            registry["status"],
            "production_binding_implementation_ready_canary_required",
        )
        report = {
            "schema_version": "1.0.0",
            "gate_id": "saju-chart-only-dashboard-canary-v1.0.0",
            "status": "passed_limited_public_chart_only_canary",
            "diagnostic_target_met": True,
            "http_cases": 100,
            "http_passed": 100,
            "http_failed": 0,
            "failure_counts": {},
            "strata": {
                name: {"cases": count, "passed": count, "failed": 0}
                for name, count in EXPECTED_STRATA.items()
            },
            "gate_checks": {name: True for name in EXPECTED_CHECKS},
            "gpu_pair": {
                "executed": True,
                "engine_pair": "k0_instruct+ki20_final",
                "both_outputs_nonempty": True,
                "same_runtime_snapshot": True,
                "semantic_scoring_performed": False,
                "raw_outputs_tracked": False,
            },
            "runtime": {
                "dashboard_schema_version": "1.9.0",
                "binding_id": "saju-chart-only-dashboard-binding-v1.0.0",
                "release_id": "saju-runtime-release-v1.4.0-63dc8d398e90",
                "scope": "limited_public_chart_only",
                "feature_default": False,
                "period_runtime_allowed": False,
            },
            "output_policy": {
                "aggregate_only": True,
                "raw_case_output_tracked": False,
                "raw_model_output_tracked": False,
                "birth_input_recorded": False,
                "runtime_identifier_recorded": False,
                "public_url_recorded": False,
                "private_path_recorded": False,
            },
            "governance": {
                "sealed_blind_accessed": False,
                "mix20k_v3_1_generated": False,
                "training_execution_performed": False,
                "model_promotion_performed": False,
                "phase6_status_auto_changed": False,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            report_root = write_report(report, Path(directory))
            verified = verify_report(report_root.resolve())
            self.assertEqual(verified["http_cases"], 100)
            self.assertEqual(verified["gpu_pairs"], 1)
            aggregate = (report_root / "aggregate.json").read_text(encoding="utf-8")
            self.assertNotIn("session_id", aggregate)
            self.assertNotIn('"outputs":', aggregate)


if __name__ == "__main__":
    unittest.main()
