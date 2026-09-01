# test_saju_historical_candidate_intake.py - 과거 공식 근거 후보 FSM의 권한·HMAC·cutoff를 검증한다.

from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from scripts.runtime.calculation.contracts import POLICY_ID
from scripts.runtime.calculation.contracts_v1_2 import ID_CONTRACT_VERSION_V2
from scripts.runtime.calculation.contracts_v1_3 import (
    ENGINE_VERSION_V13,
    OUTPUT_SCHEMA_VERSION_V13,
)
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
    SkyfieldSolarTermProvider,
)
from scripts.runtime.calculation.solar_term_types import (
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
    SOURCE_HARD_FACT,
)
from scripts.runtime.intake_contracts_v1_2 import validate_intake_registry_v1_2
from scripts.runtime.intake_fsm import IntakeFsmError
from scripts.runtime.intake_fsm_v1_2 import (
    CANDIDATE_SCOPE,
    PUBLIC_EVENT_TYPES,
    advance_intake,
    assert_public_event,
    empty_intake_state,
)

READY = {
    "candidate_runtime_enabled": True,
    "candidate_id_key_ready": True,
    "candidate_fsm_gate_passed": True,
    "ephemeris_ready": True,
    "loopback_only": True,
    "ephemeral_session_store": True,
}
SEOUL = {
    "country_code": "KR",
    "city": "서울",
    "timezone": "Asia/Seoul",
    "longitude": None,
    "latitude": None,
}


def _evidence(
    *,
    authority: str = PAST_OFFICIAL_CORROBORATED,
    instant_utc: str = "1964-09-07T14:59:30Z",
) -> dict[str, object]:
    official_class = SOURCE_HARD_FACT if authority != PROFILE_DETERMINISTIC else None
    return {
        "schema_version": "1.0.0",
        "provider_id": SkyfieldSolarTermProvider.provider_id,
        "root_time_scale": "TT",
        "boundary_comparison_time_scale": "TT",
        "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
        "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
        "provider_generated_value_is_official": False,
        "authority_classes": [authority],
        "overall_authority": authority,
        "contains_future_nonapproval": False,
        "boundaries": [
            {
                "roles": ["month"],
                "year": 1964,
                "term_index": 16,
                "term_name": "백로",
                "instant_tt_jd": "2438646.125000000000",
                "instant_utc": instant_utc,
                "official_display_minute_fixed_kst": "1964-09-07T23:59+09:00",
                "authority_class": authority,
                "official_source_evidence_class": official_class,
                "provider_generated_value_is_official": False,
            }
        ],
    }


def _drive_to_call(
    signer: RuntimeIdSigner,
    *,
    birth_date: str = "1964-09-08",
    precision: str = "exact",
    birth_time: str = "00:01",
    time_range: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    state = empty_intake_state()
    events = [
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "birth_date", "value": birth_date},
        {"type": "set_slot", "field": "calendar", "value": "solar"},
    ]
    if precision == "unknown":
        events.append({"type": "set_time_unknown"})
    else:
        events.append(
            {"type": "set_slot", "field": "time_precision", "value": precision}
        )
        events.append(
            {
                "type": "set_slot",
                "field": "birth_time" if precision == "exact" else "time_range",
                "value": birth_time if precision == "exact" else time_range,
            }
        )
    events.append({"type": "set_slot", "field": "birthplace", "value": SEOUL})
    transition: dict = {}
    for event in events:
        transition = advance_intake(state, event, signer, READY)
        state = transition["session_state"]
    return state, transition["decision"]


def _candidate_result(
    signer: RuntimeIdSigner,
    decision: dict,
    *,
    authority: str = PAST_OFFICIAL_CORROBORATED,
    alternative_authorities: tuple[str, ...] | None = None,
) -> dict:
    arguments = decision["arguments"]
    precision = arguments["time_precision"]
    normalized = {
        "calendar": arguments["calendar"],
        "local_birth_date": arguments["birth_date"],
        "solar_birth_date": arguments["birth_date"],
        "lunar_birth_date": {
            "year": 1964,
            "month": 8,
            "day": 2,
            "leap_month": False,
        },
        "lunar_leap_month": None,
        "birth_time_precision": precision,
        "local_birth_time": arguments["birth_time"],
        "birth_time_range": arguments["time_range"],
        "country_code": "KR",
        "city": "서울",
        "iana_time_zone": "Asia/Seoul",
        "fold": None,
        "policy_id": POLICY_ID,
    }
    source_versions = {
        "solar_term_provider": SkyfieldSolarTermProvider.provider_id,
        "source_registry": "saju-runtime-sources-v1.6.0",
    }
    identity = {
        "normalized_birth_input": normalized,
        "policy_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V13,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "source_versions": source_versions,
    }
    authorities = alternative_authorities or (authority,)
    alternatives = []
    for index, alternative_authority in enumerate(authorities):
        facts = {
            "pillars": {"marker": index},
            "solar_term_evidence": _evidence(authority=alternative_authority),
        }
        alternatives.append(
            {
                "chart_id": signer.chart_id({**identity, "facts": facts}),
                "hard_facts": facts,
                "local_time_first": "1964-09-08T00:01+0900",
                "local_time_last": "1964-09-08T00:01+0900",
                "sample_count": 1,
                "folds": [0],
            }
        )
    alternatives.sort(key=lambda item: item["chart_id"])
    candidate_ids = [item["chart_id"] for item in alternatives]
    exact_unique = precision == "exact" and len(alternatives) == 1
    chart_id = candidate_ids[0] if exact_unique else None
    chart_set_id = (
        None
        if exact_unique
        else signer.chart_set_id(
            {**identity, "candidate_chart_ids": candidate_ids}
        )
    )
    hard_facts = (
        alternatives[0]["hard_facts"]
        if exact_unique
        else {
            "pillars": {"hour": None},
            "solar_term_evidence": _evidence(authority=authority),
        }
    )
    return {
        "status": "partial",
        "code": "RUNTIME_RELEASE_PENDING",
        "message": "candidate",
        "normalized_input": normalized,
        "hard_facts": hard_facts,
        "stable_facts": deepcopy(hard_facts),
        "alternative_charts": alternatives,
        "uncertainty": {
            "birth_time_precision": precision,
            "candidate_count": len(alternatives),
            "instant_candidate_count": len(alternatives),
            "hour_pillar_confirmed": exact_unique,
        },
        "fact_authority": "HARD_CANDIDATE",
        "birth_input_id": signer.birth_input_id(normalized),
        "chart_id": chart_id,
        "chart_set_id": chart_set_id,
        "calculation_run_id": signer.calculation_run_id(
            {**identity, "chart_id": chart_id, "chart_set_id": chart_set_id}
        ),
        "engine_version": ENGINE_VERSION_V13,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V13,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "source_versions": source_versions,
        "warnings": [],
        "limitations": [],
        "internal_trace": {"not_persisted": True},
        "call_id": decision["call_id"],
    }


class HistoricalCandidateIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = RuntimeIdSigner.for_test(bytes(range(32)))

    def test_versioned_registry_preserves_all_production_blocks(self) -> None:
        registry = validate_intake_registry_v1_2()
        self.assertEqual(
            registry["registry_id"], "saju-runtime-intake-registry-v1.2.0"
        )
        for field in (
            "production_application_binding_allowed",
            "runtime_release_approved",
            "context_window_change_allowed",
            "mix20k_v3_1_generation_allowed",
            "additional_training_allowed",
            "model_promotion_allowed",
            "sealed_blind_access_allowed",
        ):
            self.assertFalse(registry[field])

    def test_1964_baengno_exact_candidate_is_rendered_without_release_claim(self) -> None:
        state, decision = _drive_to_call(self.signer)
        self.assertEqual(decision["action"], "call_candidate_chart")
        transition = advance_intake(
            state,
            {"type": "chart_result", "result": _candidate_result(self.signer, decision)},
            self.signer,
            READY,
        )
        rendered = transition["decision"]
        self.assertEqual(rendered["action"], "render_candidate")
        self.assertEqual(rendered["candidate_scope"], CANDIDATE_SCOPE)
        self.assertFalse(rendered["release_approved"])
        self.assertEqual(
            rendered["payload"]["hard_facts"]["solar_term_evidence"]["boundaries"][0][
                "official_display_minute_fixed_kst"
            ],
            "1964-09-07T23:59+09:00",
        )
        self.assertEqual(
            transition["session_state"]["chart"]["fact_authority"],
            "HARD_CANDIDATE",
        )

    def test_profile_year_is_blocked_instead_of_being_stored(self) -> None:
        state, decision = _drive_to_call(self.signer, birth_date="1900-06-01")
        result = _candidate_result(
            self.signer, decision, authority=PROFILE_DETERMINISTIC
        )
        transition = advance_intake(
            state, {"type": "chart_result", "result": result}, self.signer, READY
        )
        self.assertEqual(
            transition["decision"]["reason_code"],
            "CANDIDATE_OFFICIAL_EVIDENCE_REQUIRED",
        )
        self.assertFalse(transition["session_state"]["chart"]["chart_valid"])

    def test_birth_after_snapshot_is_blocked_even_with_past_boundary_evidence(self) -> None:
        state, decision = _drive_to_call(
            self.signer, birth_date="2026-09-01", birth_time="00:17"
        )
        transition = advance_intake(
            state,
            {"type": "chart_result", "result": _candidate_result(self.signer, decision)},
            self.signer,
            READY,
        )
        self.assertEqual(
            transition["decision"]["reason_code"],
            "CANDIDATE_AFTER_OFFICIAL_SNAPSHOT",
        )

    def test_cutoff_minute_and_range_chart_set_are_accepted(self) -> None:
        state, decision = _drive_to_call(
            self.signer,
            birth_date="2026-09-01",
            precision="range",
            time_range={"start": "00:00", "end": "00:16"},
        )
        result = _candidate_result(
            self.signer,
            decision,
            alternative_authorities=(
                PAST_OFFICIAL_CORROBORATED,
                PAST_OFFICIAL_CORROBORATED,
            ),
        )
        transition = advance_intake(
            state, {"type": "chart_result", "result": result}, self.signer, READY
        )
        self.assertEqual(transition["decision"]["action"], "render_candidate")
        self.assertIsNone(transition["session_state"]["chart"]["chart_id"])
        self.assertIsNotNone(transition["session_state"]["chart"]["chart_set_id"])

    def test_every_alternative_must_have_past_official_evidence(self) -> None:
        state, decision = _drive_to_call(
            self.signer,
            precision="range",
            time_range={"start": "00:00", "end": "01:00"},
        )
        result = _candidate_result(
            self.signer,
            decision,
            alternative_authorities=(
                PAST_OFFICIAL_CORROBORATED,
                PROFILE_DETERMINISTIC,
            ),
        )
        transition = advance_intake(
            state, {"type": "chart_result", "result": result}, self.signer, READY
        )
        self.assertEqual(
            transition["decision"]["reason_code"],
            "CANDIDATE_OFFICIAL_EVIDENCE_REQUIRED",
        )

    def test_tampered_hmac_and_stale_call_are_rejected(self) -> None:
        state, decision = _drive_to_call(self.signer)
        result = _candidate_result(self.signer, decision)
        result["call_id"] = self.signer.calculation_run_id({"stale": True})
        with self.assertRaisesRegex(IntakeFsmError, "호출·버전"):
            advance_intake(
                state, {"type": "chart_result", "result": result}, self.signer, READY
            )
        result = _candidate_result(self.signer, decision)
        result["chart_id"] = "sc2_" + "0" * 64
        with self.assertRaisesRegex(IntakeFsmError, "HMAC ID 집합"):
            advance_intake(
                state, {"type": "chart_result", "result": result}, self.signer, READY
            )

    def test_period_is_always_out_of_scope_and_never_persisted(self) -> None:
        state, _ = _drive_to_call(self.signer)
        transition = advance_intake(
            state, {"type": "request_period"}, self.signer, READY
        )
        self.assertEqual(
            transition["decision"]["reason_code"],
            "CANDIDATE_PERIOD_OUT_OF_SCOPE",
        )
        self.assertEqual(
            transition["session_state"]["period"], {"request": None, "result": None}
        )

    def test_public_boundary_rejects_internal_result_event(self) -> None:
        self.assertNotIn("chart_result", PUBLIC_EVENT_TYPES)
        with self.assertRaisesRegex(IntakeFsmError, "공개 API"):
            assert_public_event({"type": "chart_result", "result": {}})
        assert_public_event({"type": "request_chart"})

    def test_correction_invalidates_candidate_and_changes_call_id(self) -> None:
        state, decision = _drive_to_call(self.signer)
        accepted = advance_intake(
            state,
            {"type": "chart_result", "result": _candidate_result(self.signer, decision)},
            self.signer,
            READY,
        )
        corrected = advance_intake(
            accepted["session_state"],
            {"type": "correct_slot", "field": "birth_time", "value": "00:02"},
            self.signer,
            READY,
        )
        self.assertFalse(corrected["session_state"]["chart"]["chart_valid"])
        self.assertNotEqual(corrected["decision"]["call_id"], decision["call_id"])

    def test_cutoff_contract_is_the_recorded_utc_instant(self) -> None:
        cutoff = datetime.fromisoformat(OFFICIAL_SNAPSHOT_COLLECTED_AT)
        self.assertEqual(
            cutoff.astimezone(timezone(timedelta(hours=9))).isoformat(),
            "2026-09-01T00:16:50+09:00",
        )


if __name__ == "__main__":
    unittest.main()
