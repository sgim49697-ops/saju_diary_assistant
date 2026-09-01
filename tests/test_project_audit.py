# test_project_audit.py - 통합 audit의 정본 선택·sealed 비접근·full 재현 경계를 검증한다.

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.status import project_audit

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / (
    "configs/data_versions/saju_1b_baseline/project-audit-v1.0.0.json"
)


class ProjectAuditTests(unittest.TestCase):
    def test_historical_narratives_have_no_person_dependent_evaluation_gate(self) -> None:
        targets = [
            REPO_ROOT
            / "implementation/plans/saju_1b_10k_20k_baseline/archive/"
            "saju_1b_10k_20k_baseline_plan.original.md",
            *sorted(
                (
                    REPO_ROOT
                    / "configs/data_versions/saju_1b_baseline"
                ).glob("project-status-v*.json")
            ),
        ]
        forbidden = re.compile(
            r"사람\s*(?:평가|검수|심사|판정|선호)|"
            r"인간\s*(?:평가|검수|심사)|"
            r"전문가[^\n]{0,30}(?:평가|검수|검증|승인|판독|Gold)|"
            r"수동\s*(?:평가|검수|심사)|"
            r"(?:human|expert)[_ -]?(?:evaluation|review|gate)|"
            r"자동·사람|사람·sealed|검수 후보",
            re.IGNORECASE,
        )
        for path in targets:
            self.assertIsNone(
                forbidden.search(path.read_text(encoding="utf-8")), path.as_posix()
            )

    def test_contract_is_valid_and_defaults_do_not_execute_full_audit(self) -> None:
        config = project_audit._validated_config(REPO_ROOT, CONFIG)
        self.assertEqual(config["audit_id"], "saju-project-audit-v1.0.0")
        args = project_audit._parser().parse_args(["verify"])
        self.assertFalse(args.full)
        self.assertIsNone(args.ephemeris)

    def test_runtime_cli_can_verify_v13_without_opening_ephemeris(self) -> None:
        result = project_audit._run_json(
            REPO_ROOT,
            [
                "-m",
                "scripts.runtime.saju_runtime",
                "verify-contract",
                "--engine-version",
                "1.3",
            ],
            "runtime v1.3",
        )
        self.assertEqual(result["engine_version"], "1.3")
        self.assertEqual(
            result["registry"], "saju-runtime-calculation-registry-v1.3.0"
        )

    def test_phase6_audit_does_not_resolve_or_read_blind_payload(self) -> None:
        config = project_audit._validated_config(REPO_ROOT, CONFIG)
        phase6_config = json.loads(
            (REPO_ROOT / config["phase6"]["config"]).read_text(encoding="utf-8")
        )
        blind_name = Path(phase6_config["blind_source"]["path"]).name
        original = Path.read_text

        def guarded(path: Path, *args, **kwargs):
            if path.name == blind_name:
                raise AssertionError("sealed blind payload를 읽었습니다.")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            result = project_audit._verify_phase6_without_payload(
                REPO_ROOT, config["phase6"]
            )
        self.assertFalse(result["sealed_blind_payload_opened"])

    def test_quick_audit_uses_final_postscore_and_never_runs_full_steps(self) -> None:
        calls: list[list[str]] = []

        def fake_run(repo_root: Path, arguments, label: str):
            del repo_root, label
            calls.append(list(arguments))
            if any("project_status_v1_3" in item for item in arguments):
                return {"status": "verified", "build_id": "build-38b9ca77ce45"}
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
            patch.object(project_audit, "_verify_runtime_reproduction") as full_runtime,
        ):
            result = project_audit.verify_project(
                REPO_ROOT, CONFIG, full=False, ephemeris=None
            )
        full_runtime.assert_not_called()
        flattened = [item for call in calls for item in call]
        self.assertIn("scripts.evaluation.grounded_dialogue_postscore", flattened)
        self.assertNotIn("scripts.data.phase1_sources", flattened)
        self.assertFalse(result["sealed_blind_payload_opened"])

    def test_full_audit_requires_absolute_ephemeris(self) -> None:
        with self.assertRaisesRegex(project_audit.ProjectAuditError, "ephemeris"):
            project_audit.verify_project(REPO_ROOT, CONFIG, full=True, ephemeris=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "de440s.bsp"
            path.write_bytes(b"not-de440s")
            config = project_audit._validated_config(REPO_ROOT, CONFIG)
            with self.assertRaisesRegex(project_audit.ProjectAuditError, "SHA-256"):
                project_audit._verify_runtime_reproduction(
                    REPO_ROOT, config["runtime"], path
                )


if __name__ == "__main__":
    unittest.main()
