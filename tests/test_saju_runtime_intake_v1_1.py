# test_saju_runtime_intake_v1_1.py - session v2.1·FSM v1.1 계약과 Gate 재계산을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from scripts.evaluation.saju_runtime.intake_fsm_gate import evaluate
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import (
    derive_gate_checks_v1_2,
    load_strict_json_object_v1_2,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.intake_contracts_v1_1 import (
    SESSION_SCHEMA,
    load_strict_json_object,
    validate_intake_registry_v1_1,
)
from scripts.runtime.intake_fsm import (
    IntakeFsmError,
    advance_intake,
    empty_intake_state,
)
from scripts.runtime.saju_runtime_v1_2 import _load_input

READY = {
    "runtime_release_ready": True,
    "feature_enabled": True,
    "production_id_key_ready": True,
    "fsm_gate_passed": True,
    "encrypted_persistence_ready": True,
    "retention_policy_ready": True,
}
CONFORMANCE_REPORT = REPO_ROOT / (
    "data/reports/saju_runtime_conformance/v1.2.0/"
    "build-ec510bc6922d/aggregate.json"
)


class IntakeContractV11Tests(unittest.TestCase):
    def test_registry_uses_duplicate_free_semantic_schema(self) -> None:
        registry = validate_intake_registry_v1_1()
        self.assertEqual(
            registry["registry_id"], "saju-runtime-intake-registry-v1.1.0"
        )
        schema = load_strict_json_object(SESSION_SCHEMA)
        period = schema["properties"]["period"]
        self.assertFalse(period["additionalProperties"])
        self.assertEqual(set(period["properties"]), {"request", "result"})

    def test_historical_v2_duplicate_key_is_detected_and_not_reused(self) -> None:
        historical = REPO_ROOT / "configs/runtime/session_state_schema_v2.json"
        with self.assertRaisesRegex(RuntimeCalculationError, "중복 key"):
            load_strict_json_object(historical)

    def test_strict_json_loaders_reject_nested_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"outer":{"value":1,"value":2}}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeCalculationError, "중복 key"):
                load_strict_json_object(path)
            with self.assertRaisesRegex(RuntimeCalculationError, "중복 key"):
                load_strict_json_object_v1_2(path)
            with self.assertRaisesRegex(RuntimeCalculationError, "중복 key"):
                _load_input(str(path))

    def test_tampered_slot_confirmation_state_is_rejected(self) -> None:
        state = empty_intake_state()
        state["saju_opt_in"] = True
        state["current_intent"] = "chart"
        state["birth_slots"]["birth_date"] = "1989-01-05"
        state["field_provenance"]["birth_date"] = "user_explicit"
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        with self.assertRaisesRegex(IntakeFsmError, "확인 field"):
            advance_intake(state, {"type": "request_chart"}, signer, READY)

    def test_malformed_json_types_fail_with_contract_error(self) -> None:
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        state = advance_intake(
            empty_intake_state(),
            {"type": "opt_in", "accepted": True},
            signer,
            READY,
        )["session_state"]
        malformed_states = []
        for field, value in (
            ("timezone", []),
            ("timezone", {}),
        ):
            malformed = deepcopy(state)
            malformed["birth_slots"][field] = value
            malformed_states.append(malformed)
        malformed = deepcopy(state)
        malformed["field_provenance"] = {"gender_for_daeun": []}
        malformed_states.append(malformed)
        malformed = deepcopy(state)
        malformed["chart"]["chart_valid"] = []
        malformed_states.append(malformed)
        for malformed_state in malformed_states:
            with self.subTest(state=malformed_state), self.assertRaises(
                IntakeFsmError
            ):
                advance_intake(
                    malformed_state,
                    {"type": "request_chart"},
                    signer,
                    READY,
                )
        for malformed_state in (None, 1, True):
            with self.subTest(top_level=malformed_state), self.assertRaises(
                IntakeFsmError
            ):
                advance_intake(
                    malformed_state,  # type: ignore[arg-type]
                    {"type": "request_chart"},
                    signer,
                    READY,
                )

    def test_cached_chart_fingerprint_must_match_current_slots(self) -> None:
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        state = empty_intake_state()
        events = (
            {"type": "opt_in", "accepted": True},
            {"type": "set_slot", "field": "birth_date", "value": "1989-01-05"},
            {"type": "set_slot", "field": "calendar", "value": "solar"},
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            {"type": "set_slot", "field": "birth_time", "value": "13:00"},
            {
                "type": "set_slot",
                "field": "birthplace",
                "value": {
                    "country_code": "KR",
                    "city": "서울",
                    "timezone": "Asia/Seoul",
                    "longitude": None,
                    "latitude": None,
                },
            },
        )
        for event in events:
            value = advance_intake(state, event, signer, READY)
            state = value["session_state"]
        value = advance_intake(
            state,
            {
                "type": "chart_result",
                "result": {
                    "status": "ok",
                    "hard_facts": {"pillars": {}},
                    "fact_authority": "HARD_GT",
                    "chart_id": signer.chart_id({"fixture": "fingerprint"}),
                    "chart_set_id": None,
                    "call_id": value["decision"]["call_id"],
                },
            },
            signer,
            READY,
        )
        tampered = deepcopy(value["session_state"])
        tampered["chart"]["chart_input_fingerprint"] = "sif2_" + "0" * 64
        with self.assertRaisesRegex(IntakeFsmError, "HMAC ID"):
            advance_intake(
                tampered,
                {"type": "request_chart"},
                signer,
                READY,
            )


class IntakeGateV11Tests(unittest.TestCase):
    def test_gate_requires_cases_and_computed_structural_checks(self) -> None:
        report = evaluate()
        self.assertEqual((report["passed"], report["cases"]), (100, 100))
        self.assertTrue(report["app_fsm_gate_passed"])
        self.assertTrue(all(report["gate_checks"].values()))
        self.assertEqual(report["failure_counts"], report["maximum_failures"])
        self.assertEqual(
            report["inputs"]["intake_registry"]["registry_id"],
            "saju-runtime-intake-registry-v1.1.0",
        )
        self.assertEqual(report["gate_version"], "saju-intake-fsm-gate-v1.1.0")

    def test_structural_failure_cannot_pass_with_100_case_score(self) -> None:
        with mock.patch(
            "scripts.evaluation.saju_runtime.intake_fsm_gate.SLOT_FIELDS",
            frozenset(
                {
                    "birth_date",
                    "calendar",
                    "leap_month",
                    "birth_time",
                    "time_precision",
                    "time_range",
                    "birthplace",
                    "name",
                }
            ),
        ):
            report = evaluate()
        self.assertEqual(report["passed"], 100)
        self.assertFalse(report["gate_checks"]["name_family_job_slots_absent"])
        self.assertFalse(report["app_fsm_gate_passed"])
        self.assertEqual(report["status"], "failed")


class RuntimeReleaseSemanticTests(unittest.TestCase):
    def test_committed_gate_booleans_recompute_from_aggregate_metrics(self) -> None:
        report = json.loads(CONFORMANCE_REPORT.read_text(encoding="utf-8"))
        self.assertEqual(report["gate_checks"], derive_gate_checks_v1_2(report))

    def test_tampered_metric_cannot_keep_original_gate_booleans(self) -> None:
        report = json.loads(CONFORMANCE_REPORT.read_text(encoding="utf-8"))
        tampered = deepcopy(report)
        tampered["independent_jie_crosscheck"]["threshold_failures"] = 1
        derived = derive_gate_checks_v1_2(tampered)
        self.assertFalse(
            derived["independent_jie_fixed_120_second_regression_guard"]
        )
        self.assertNotEqual(tampered["gate_checks"], derived)


if __name__ == "__main__":
    unittest.main()
