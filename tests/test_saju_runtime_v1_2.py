# test_saju_runtime_v1_2.py - 천문 v4 Gate·HMAC ID·구조화 intake FSM 회귀를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts.evaluation.saju_runtime.conformance_v4 import (
    _adjudicate_engine_local_dates,
    _minute_checks_v1_2,
)
from scripts.evaluation.saju_runtime.intake_fsm_gate import evaluate
from scripts.evaluation.saju_runtime.jie_crosscheck_v1_2 import (
    compare_jie_boundaries_v1_2,
    display_minute_label,
    nearest_display_minute,
)
from scripts.runtime.calculation.contracts_v1_2 import validate_contract_registry_v1_2
from scripts.runtime.calculation.engine_v1_2 import (
    ApprovedSajuRuntimeEngineV12,
    SajuRuntimeEngineV12,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.intake_fsm import (
    IntakeFsmError,
    advance_intake,
    empty_intake_state,
)
from scripts.training.phase5_v3_1_preflight import (
    Phase5V31PreflightError,
    _verify_projection_release,
)

KST = ZoneInfo("Asia/Seoul")
READY = {
    "runtime_release_ready": True,
    "feature_enabled": True,
    "production_id_key_ready": True,
    "fsm_gate_passed": True,
    "encrypted_persistence_ready": True,
    "retention_policy_ready": True,
}


def _chart_arguments() -> dict[str, object]:
    return {
        "birth_date": "1989-01-05",
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "13:00",
        "time_precision": "exact",
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


class RuntimeIdSignerV2Tests(unittest.TestCase):
    def test_domain_separation_reproducibility_and_rotation(self) -> None:
        payload = {"birth_date": "1989-01-05", "city": "서울"}
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        repeated = RuntimeIdSigner.for_test(bytes(range(32)))
        rotated = RuntimeIdSigner.for_test(bytes(reversed(range(32))))
        values = {
            signer.birth_input_id(payload),
            signer.chart_id(payload),
            signer.chart_set_id(payload),
            signer.calculation_run_id(payload),
            signer.chart_input_fingerprint(payload),
        }
        self.assertEqual(len(values), 5)
        self.assertEqual(signer.chart_id(payload), repeated.chart_id(dict(payload)))
        self.assertNotEqual(signer.chart_id(payload), rotated.chart_id(payload))
        self.assertFalse(signer.production_key)

    def test_production_key_requires_absolute_owned_0600_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "id-key"
            path.write_bytes(bytes(range(32)))
            path.chmod(0o600)
            signer = RuntimeIdSigner.from_key_file(path, environ={})
            self.assertTrue(signer.production_key)
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeCalculationError, "0600"):
                RuntimeIdSigner.from_key_file(path, environ={})
            path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeCalculationError, "절대 경로"):
                RuntimeIdSigner.from_key_file(Path("relative-key"), environ={})

    def test_key_source_is_unambiguous_and_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "id-key"
            path.write_bytes(b"x" * 32)
            path.chmod(0o600)
            with self.assertRaisesRegex(RuntimeCalculationError, "하나로만"):
                RuntimeIdSigner.from_key_file(
                    path, environ={"SAJU_RUNTIME_ID_KEY_FILE": str(path)}
                )
            link = root / "id-key-link"
            link.symlink_to(path)
            with self.assertRaises(RuntimeCalculationError):
                RuntimeIdSigner.from_key_file(link, environ={})

    def test_production_signer_cannot_be_forged_with_public_constructor(self) -> None:
        with self.assertRaisesRegex(RuntimeCalculationError, "loader"):
            RuntimeIdSigner(bytes(range(32)), production_key=True)


class AstronomyGateV12Tests(unittest.TestCase):
    def test_nearest_minute_is_half_up_in_kst(self) -> None:
        base = datetime(2026, 8, 31, 3, 4, 29, 999999, tzinfo=timezone.utc)
        self.assertEqual(
            nearest_display_minute(base).isoformat(timespec="minutes"),
            "2026-08-31T12:04+09:00",
        )
        self.assertEqual(
            display_minute_label(base + timedelta(microseconds=1)),
            "2026-08-31T12:05+09:00",
        )

    def test_exact_display_minute_rejects_value_old_abs_tolerance_accepted(self) -> None:
        reference = datetime(2026, 8, 31, 12, 0, tzinfo=KST)
        runtime = reference - timedelta(seconds=31)
        comparator = reference + timedelta(seconds=29)
        rows = [
            {
                "year": 2026,
                "term_index": 0,
                "reference_local_minute": reference.isoformat(timespec="minutes"),
            }
        ]
        independent = [
            {
                "year": 2026,
                "term_index": 0,
                "skyfield_instant_utc": comparator.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
        ]
        with mock.patch(
            "scripts.runtime.calculation.solar_terms.solar_term_instant",
            return_value=runtime.astimezone(timezone.utc),
        ):
            result = _minute_checks_v1_2(rows, independent)
        self.assertEqual(result["runtime_display_minute_mismatches"], 1)
        self.assertEqual(result["independent_display_minute_mismatches"], 0)
        self.assertEqual(result["minimum_runtime_signed_delta_seconds"], -31.0)
        self.assertEqual(result["signed_delta_role"], "diagnostic_only_not_gate_threshold")

    def test_engine_date_disagreement_without_kasi_is_blocking_unresolved(self) -> None:
        record = {
            "year": 1964,
            "term_index": 16,
            "term_name": "백로",
            "astronomy_local_date": "1964-09-08",
            "skyfield_local_date": "1964-09-07",
        }
        missing = _adjudicate_engine_local_dates([record] * 1800, [])
        self.assertEqual(missing["status"], "blocked_unresolved_official_adjudication")
        self.assertEqual(missing["unresolved_disagreements"], 1800)
        official = [{"year": 1964, "term_index": 16, "local_date": "1964-09-08"}]
        resolved = _adjudicate_engine_local_dates([record], official)
        self.assertEqual(resolved["unresolved_disagreements"], 0)
        self.assertEqual(resolved["runtime_official_mismatches_on_disagreements"], 0)
        self.assertEqual(resolved["independent_official_mismatches_on_disagreements"], 1)

    def test_crosscheck_summary_does_not_claim_official_adjudication(self) -> None:
        base = {
            "schema_version": "1.1.0",
            "crosscheck_version": "old",
            "status": "passed",
            "rows": 1,
            "expected_rows": 1,
            "threshold_failures": 0,
            "term_identity_failures": 0,
            "chronological_order_failures": 0,
            "local_date_mismatches": 1,
            "local_date_adjudicator": "incorrect",
            "records_sha256": "0" * 64,
            "records_in_report": True,
            "records": [{"placeholder": True}],
        }
        enriched = [
            {
                "year": 1964,
                "term_index": 16,
                "term_name": "백로",
                "astronomy_local_date": "1964-09-08",
                "skyfield_local_date": "1964-09-07",
                "delta_seconds": 40.0,
                "display_minute_match": True,
            }
        ]
        diagnostic = {
            "status": "delta_t_not_primary",
            "raw_engine_delta": {},
            "delta_t_aligned_residual": {},
        }
        with (
            mock.patch(
                "scripts.evaluation.saju_runtime.jie_crosscheck_v1_2.compare_jie_boundaries",
                return_value=base,
            ),
            mock.patch(
                "scripts.evaluation.saju_runtime.jie_crosscheck_v1_2._enrich_records",
                return_value=(enriched, diagnostic),
            ),
        ):
            result = compare_jie_boundaries_v1_2(Path("unused"), start_year=1964, end_year=1964)
        self.assertFalse(result["official_local_date_adjudication_performed"])
        self.assertEqual(result["local_date_comparison_scope"], "astronomy_engine_vs_skyfield_only")
        self.assertNotIn("local_date_adjudicator", result)


class RuntimeEngineV12Tests(unittest.TestCase):
    def test_contract_and_candidate_engine_emit_only_hmac_v2_ids(self) -> None:
        self.assertEqual(
            validate_contract_registry_v1_2()["registry_id"],
            "saju-runtime-calculation-registry-v1.2.0",
        )
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        engine = SajuRuntimeEngineV12(
            signer=signer, enable_candidate_runtime=True
        )
        result = engine.calculate_chart(_chart_arguments())
        self.assertTrue(result["birth_input_id"].startswith("sbi2_"))
        self.assertTrue(result["chart_id"].startswith("sc2_"))
        self.assertTrue(result["calculation_run_id"].startswith("scr2_"))
        self.assertEqual(result["id_contract_version"], "saju-runtime-id-hmac-v2.0.0")
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("sbi1_", serialized)
        self.assertNotIn("sc1_", serialized)
        period = engine.calculate_period(
            {
                "chart_id": result["chart_id"],
                "period_type": "day",
                "start_date": "2026-08-31",
                "end_date": None,
                "timezone": "Asia/Seoul",
            }
        )
        self.assertTrue(period["calculation_run_id"].startswith("scr2_"))
        self.assertEqual(period["internal_trace"]["chart_id"], result["chart_id"])

    def test_approved_engine_stays_release_blocked_without_loading_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            engine = ApprovedSajuRuntimeEngineV12(enable_approved_runtime=True)
        result = engine.calculate_chart(_chart_arguments())
        self.assertEqual(result["code"], "RUNTIME_RELEASE_REQUIRED")
        self.assertIsNone(result["fact_authority"])

    def test_future_v31_projection_rejects_legacy_runtime_fact_source(self) -> None:
        with self.assertRaisesRegex(Phase5V31PreflightError, "fact source"):
            _verify_projection_release(
                [
                    {
                        "runtime_release_id": "saju-runtime-release-v1.2.0-000000000000",
                        "runtime_fact_source": "approved_saju_runtime_v1_1",
                        "messages": [
                            {
                                "role": "assistant",
                                "tool_calls": [{"function": {"name": "calculate_saju_chart"}}],
                            }
                        ],
                    }
                ],
                "saju-runtime-release-v1.2.0-000000000000",
            )


class IntakeFsmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RuntimeIdSigner.for_test(bytes(range(32)))

    def _advance(self, state: dict[str, object], event: dict[str, object]):
        return advance_intake(state, event, self.signer, READY)

    def test_unknown_time_skips_exact_time_and_never_asks_name_or_gender(self) -> None:
        state = empty_intake_state()
        value = self._advance(state, {"type": "opt_in", "accepted": True})
        state = value["session_state"]
        for field, slot_value in (
            ("birth_date", "1989-01-05"),
            ("calendar", "solar"),
        ):
            value = self._advance(
                state, {"type": "set_slot", "field": field, "value": slot_value}
            )
            state = value["session_state"]
        value = self._advance(state, {"type": "set_time_unknown"})
        self.assertEqual(value["decision"]["action"], "ask_birthplace")
        self.assertEqual(
            value["session_state"]["birth_slots"]["gender_for_daeun"], "unspecified"
        )
        self.assertNotIn("성함", value["decision"]["message"])

    def test_chart_result_renders_and_correction_invalidates(self) -> None:
        state = empty_intake_state()
        events = [
            {"type": "opt_in", "accepted": True},
            {"type": "set_slot", "field": "birth_date", "value": "1989-01-05"},
            {"type": "set_slot", "field": "calendar", "value": "solar"},
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            {"type": "set_slot", "field": "birth_time", "value": "13:00"},
            {
                "type": "set_slot",
                "field": "birthplace",
                "value": _chart_arguments()["birthplace"],
            },
        ]
        for event in events:
            value = self._advance(state, event)
            state = value["session_state"]
        self.assertEqual(value["decision"]["action"], "call_chart")
        value = self._advance(
            state,
            {
                "type": "chart_result",
                "result": {
                    "status": "ok",
                    "hard_facts": {"pillars": {"year": {"ganzhi": "戊辰"}}},
                    "fact_authority": "HARD_GT",
                    "chart_id": self.signer.chart_id({"fixture": 1}),
                    "chart_set_id": None,
                },
            },
        )
        state = value["session_state"]
        self.assertEqual(value["decision"]["action"], "render_result")
        self.assertTrue(state["chart"]["chart_input_fingerprint"].startswith("sif2_"))
        value = self._advance(
            state,
            {"type": "correct_slot", "field": "birth_date", "value": "1990-01-05"},
        )
        self.assertFalse(value["session_state"]["chart"]["chart_valid"])
        self.assertEqual(value["decision"]["action"], "call_chart")

    def test_unstructured_or_extra_event_fields_are_rejected(self) -> None:
        with self.assertRaises(IntakeFsmError):
            self._advance(empty_intake_state(), {"type": "free_text", "text": "내 사주"})
        with self.assertRaisesRegex(IntakeFsmError, "field 집합"):
            self._advance(
                empty_intake_state(),
                {"type": "opt_in", "accepted": True, "name": "김슬기"},
            )

    def test_tool_result_cannot_be_injected_before_matching_call_action(self) -> None:
        event = {
            "type": "chart_result",
            "result": {
                "status": "ok",
                "hard_facts": {"pillars": {}},
                "fact_authority": "HARD_GT",
                "chart_id": self.signer.chart_id({"fixture": 2}),
                "chart_set_id": None,
            },
        }
        with self.assertRaisesRegex(IntakeFsmError, "call_chart"):
            self._advance(empty_intake_state(), event)
        with self.assertRaisesRegex(IntakeFsmError, "call_period"):
            self._advance(
                empty_intake_state(),
                {
                    "type": "period_result",
                    "result": {
                        "status": "ok",
                        "hard_facts": {"period": {}},
                        "fact_authority": "HARD_GT",
                    },
                },
            )

    def test_unknown_marker_is_cleared_when_exact_time_is_supplied(self) -> None:
        state = empty_intake_state()
        for event in (
            {"type": "opt_in", "accepted": True},
            {"type": "set_time_unknown"},
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            {"type": "set_slot", "field": "birth_time", "value": "13:00"},
        ):
            value = self._advance(state, event)
            state = value["session_state"]
        self.assertEqual(state["explicit_unknown_fields"], [])
        self.assertEqual(state["birth_slots"]["birth_time"], "13:00")

    def test_period_result_requires_a_new_request_after_render(self) -> None:
        state = empty_intake_state()
        for event in (
            {"type": "opt_in", "accepted": True},
            {"type": "set_slot", "field": "birth_date", "value": "1989-01-05"},
            {"type": "set_slot", "field": "calendar", "value": "solar"},
            {"type": "set_slot", "field": "time_precision", "value": "exact"},
            {"type": "set_slot", "field": "birth_time", "value": "13:00"},
            {"type": "set_slot", "field": "birthplace", "value": _chart_arguments()["birthplace"]},
        ):
            value = self._advance(state, event)
            state = value["session_state"]
        value = self._advance(
            state,
            {
                "type": "chart_result",
                "result": {
                    "status": "ok",
                    "hard_facts": {"pillars": {}},
                    "fact_authority": "HARD_GT",
                    "chart_id": self.signer.chart_id({"fixture": "period"}),
                    "chart_set_id": None,
                },
            },
        )
        state = value["session_state"]
        value = self._advance(
            state,
            {
                "type": "request_period",
                "request": {
                    "period_type": "day",
                    "start_date": "2026-08-31",
                    "end_date": None,
                    "timezone": "Asia/Seoul",
                },
            },
        )
        state = value["session_state"]
        period_result = {
            "type": "period_result",
            "result": {
                "status": "ok",
                "hard_facts": {"period": {}},
                "fact_authority": "HARD_GT",
            },
        }
        value = self._advance(state, period_result)
        self.assertEqual(value["decision"]["action"], "render_result")
        with self.assertRaisesRegex(IntakeFsmError, "call_period"):
            self._advance(value["session_state"], period_result)

    def test_synthetic_app_gate_is_100_of_100_but_model_14_remains(self) -> None:
        report = evaluate()
        self.assertEqual((report["passed"], report["cases"]), (100, 100))
        self.assertTrue(report["app_fsm_gate_passed"])
        self.assertFalse(report["app_integration_allowed"])
        self.assertEqual(
            (
                report["model_gate_comparison"]["required_handoff_action_passed"],
                report["model_gate_comparison"]["required_handoff_action_total"],
            ),
            (14, 100),
        )


if __name__ == "__main__":
    unittest.main()
