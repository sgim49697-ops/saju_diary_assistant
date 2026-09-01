# test_saju_runtime_conformance_v8.py - 고정 conformance v8 report와 비승인 상태를 검증한다.

from __future__ import annotations

import hashlib
import json
import unittest
from collections import Counter
from pathlib import Path

from scripts.evaluation.saju_runtime.conformance_v8 import IMPLEMENTATION_PATHS
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_3 import (
    REGISTRY_V13_PATH,
    REGISTRY_V13_SHA256,
    validate_contract_registry_v1_3,
)
from scripts.runtime.calculation.solar_term_types import (
    FORECAST_DIAGNOSTIC_NONAPPROVAL,
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
)

REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_conformance/v1.6.0"


def _successful_build() -> tuple[dict, Path]:
    current_implementation = {
        path: sha256_file(REPO_ROOT / path) for path in sorted(IMPLEMENTATION_PATHS)
    }
    candidates: list[tuple[dict, Path]] = []
    for path in sorted(REPORT_ROOT.glob("build-*/aggregate.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            report.get("candidate_runtime_conformance_passed") is True
            and report.get("inputs", {}).get("implementation_sha256")
            == current_implementation
        ):
            candidates.append((report, path.parent))
    if len(candidates) != 1:
        raise AssertionError(
            "현재 구현과 일치하는 conformance v8 통과 build가 "
            f"하나가 아닙니다: {len(candidates)}"
        )
    return candidates[0]


class ConformanceV8ContractTests(unittest.TestCase):
    def test_registry_and_gate_state_are_valid(self) -> None:
        registry = validate_contract_registry_v1_3()
        self.assertEqual(sha256_file(REGISTRY_V13_PATH), REGISTRY_V13_SHA256)
        self.assertEqual(registry["schema_version"], "1.3.0")

    def test_committed_report_passes_candidate_but_not_strict_or_release(self) -> None:
        report, _ = _successful_build()
        self.assertEqual(
            report["status"], "candidate_runtime_conformance_passed_release_blocked"
        )
        for key in (
            "candidate_runtime_provider_bound",
            "candidate_runtime_conformance_passed",
            "past_authority_gate_passed",
            "future_authority_separation_gate_passed",
        ):
            self.assertTrue(report[key])
        for key in (
            "strict_runtime_provider_gate_passed",
            "runtime_gate_passed",
            "runtime_approved",
            "release_approval_performed",
            "release_registry_creation_allowed",
            "production_runtime_provider_changed",
            "runtime_feature_flag_default",
            "app_binding_performed",
            "mix20k_v3_1_generated",
            "training_promotion_allowed",
            "phase5_training_performed",
            "sealed_blind_accessed",
        ):
            self.assertFalse(report[key])
        runtime = report["runtime_provider_conformance"]
        self.assertEqual(runtime["rows"], 1_800)
        self.assertEqual(runtime["boundary_cases"], 5_400)
        self.assertEqual(runtime["boundary_failures"], 0)
        self.assertEqual(runtime["tt_delta_microseconds"]["maximum_absolute"], 0.0)
        self.assertEqual(
            runtime["authority_counts"],
            {
                FORECAST_DIAGNOSTIC_NONAPPROVAL: 280,
                PAST_OFFICIAL_CORROBORATED: 1_280,
                PROFILE_DETERMINISTIC: 240,
            },
        )
        preferred = report["baseline_v7_recalculation"][
            "preferred_provider_evidence"
        ]
        self.assertEqual(preferred["official_current_minute_label_mismatches"], 22)
        self.assertEqual(preferred["past_uncertainty_failures"], 0)

    def test_manifest_records_and_implementation_hashes_are_self_consistent(self) -> None:
        report, directory = _successful_build()
        manifest_path = directory / "build_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            directory.name,
            "build-" + hashlib.sha256(canonical_json_bytes(report)).hexdigest()[:12],
        )
        self.assertEqual(manifest["build_id"], directory.name)
        for filename, identity in manifest["artifacts"].items():
            payload = (directory / filename).read_bytes()
            self.assertEqual(identity["bytes"], len(payload))
            self.assertEqual(identity["sha256"], hashlib.sha256(payload).hexdigest())
        for relative, expected in report["inputs"]["implementation_sha256"].items():
            self.assertEqual(sha256_file(REPO_ROOT / relative), expected)
        records_path = directory / "runtime_provider_records.jsonl"
        records = [
            json.loads(line)
            for line in records_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(records), 1_800)
        self.assertEqual(
            Counter(record["authority_class"] for record in records),
            Counter(
                {
                    PROFILE_DETERMINISTIC: 240,
                    PAST_OFFICIAL_CORROBORATED: 1_280,
                    FORECAST_DIAGNOSTIC_NONAPPROVAL: 280,
                }
            ),
        )
        self.assertTrue(all(record["utc_exact_match"] for record in records))
        self.assertTrue(
            all(record["official_display_minute_match"] for record in records)
        )


if __name__ == "__main__":
    unittest.main()
