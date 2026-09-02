# test_project_audit_v1_1.py - 현재 Runtime·dashboard 통합 audit의 권한 분리를 검증한다.

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.status import project_audit

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/project-audit-v1.1.0.json"
)


class ProjectAuditV11Tests(unittest.TestCase):
    def test_contract_selects_current_runtime_without_changing_governance(self) -> None:
        config = project_audit._validated_config(REPO_ROOT, CONFIG)
        self.assertEqual(config["runtime"]["engine_version"], "1.5")
        self.assertFalse(config["governance"]["strict_full_runtime_allowed"])
        self.assertFalse(config["governance"]["mix20k_v3_1_generation_allowed"])
        self.assertFalse(config["governance"]["model_promotion_allowed"])

    def test_dashboard_static_contract_and_assets_are_verified(self) -> None:
        config = project_audit._validated_config(REPO_ROOT, CONFIG)
        result = project_audit._verify_dashboard_v1_11(
            REPO_ROOT, config["dashboard"], config["runtime"]
        )
        self.assertEqual(result["schema_version"], "1.11.0")
        self.assertFalse(result["runtime_feature_flag_default"])
        self.assertFalse(result["strict_full_runtime_enabled"])

    def test_quick_audit_uses_v15_v10_and_v14_status(self) -> None:
        calls: list[list[str]] = []

        def fake_run(repo_root: Path, arguments, label: str):
            del repo_root, label
            call = list(arguments)
            calls.append(call)
            if "verify-release" in call:
                return {
                    "status": "verified_chart_and_single_day",
                    "release_id": "saju-runtime-release-v1.5.0-8b1d6ea2d46e",
                }
            if "scripts.evaluation.saju_runtime.conformance_v10" in call:
                return {"status": "verified", "build_id": "build-46185262164f"}
            if "scripts.status.project_status_v1_4" in call:
                return {"status": "verified", "build_id": "build-faf55ff6886d"}
            return {"status": "verified"}

        with (
            patch.object(project_audit, "_run_json", side_effect=fake_run),
            patch.object(
                project_audit,
                "_verify_phase6_without_payload",
                return_value={
                    "status": "verified_without_payload_open",
                    "sealed_blind_payload_opened": False,
                },
            ),
            patch.object(
                project_audit,
                "_verify_dashboard_v1_11",
                return_value={"status": "verified_limited_chart_and_single_day_binding"},
            ),
            patch.object(
                project_audit, "_verify_runtime_reproduction_v1_1"
            ) as reproduction,
        ):
            result = project_audit.verify_project(
                REPO_ROOT, CONFIG, full=False, ephemeris=None
            )
        reproduction.assert_not_called()
        flattened = [item for call in calls for item in call]
        self.assertIn("scripts.runtime.saju_runtime_v1_5", flattened)
        self.assertIn("scripts.evaluation.saju_runtime.conformance_v10", flattened)
        self.assertIn("scripts.status.project_status_v1_4", flattened)
        self.assertFalse(result["strict_full_runtime_approved"])
        self.assertFalse(result["mix20k_v3_1_generated"])
        self.assertFalse(result["model_promotion_performed"])

    def test_full_audit_requires_ephemeris(self) -> None:
        with self.assertRaisesRegex(project_audit.ProjectAuditError, "ephemeris"):
            project_audit.verify_project(
                REPO_ROOT, CONFIG, full=True, ephemeris=None
            )


if __name__ == "__main__":
    unittest.main()
