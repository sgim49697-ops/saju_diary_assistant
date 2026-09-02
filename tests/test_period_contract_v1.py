# test_period_contract_v1.py - 기간 요청·상대 날짜·원국 재복원 계약을 검증한다.

from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path

from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT
from scripts.runtime.calculation.contracts_v1_2 import ID_CONTRACT_VERSION_V2
from scripts.runtime.calculation.contracts_v1_5 import (
    APPROVED_SCOPE_V15,
    ENGINE_VERSION_V15,
    OUTPUT_SCHEMA_VERSION_V15,
    RELEASE_V15_PATH,
)
from scripts.runtime.calculation.engine_v1_5 import ApprovedSajuRuntimeEngineV15
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.chart_day_adapter import empty_session_state
from scripts.runtime.period_v1.contracts import (
    PARENT_RELEASE_ID,
    load_public_period_event,
    validate_contract_registry,
    validate_public_period_event,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.rehydration import rehydrate_exact_chart
from scripts.runtime.period_v1.resolver import resolve_period_scope

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


def _event(
    expression: str,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, object]:
    return {
        "type": "request_period",
        "request": {
            "schema_version": "saju-period-request-v2",
            "date_expression": expression,
            "start_date": start,
            "end_date": end,
        },
    }


def _birth_slots(*, precision: str = "exact") -> dict[str, object]:
    return {
        "calendar": "solar",
        "birth_date": "1990-01-01",
        "leap_month": None,
        "time_precision": precision,
        "birth_time": "12:00" if precision == "exact" else None,
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": "서울",
            "timezone": "Asia/Seoul",
        },
    }


def _chart_arguments() -> dict[str, object]:
    return {
        "birth_date": "1990-01-01",
        "calendar": "solar",
        "leap_month": None,
        "birth_time": "12:00",
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


def _signed_chart(signer: RuntimeIdSigner) -> dict[str, object]:
    normalized = {
        "calendar": "solar",
        "local_birth_date": "1990-01-01",
        "lunar_leap_month": None,
        "birth_time_precision": "exact",
        "local_birth_time": "12:00",
        "birth_time_range": None,
        "country_code": "KR",
        "city": "서울",
        "iana_time_zone": "Asia/Seoul",
        "policy_id": POLICY_ID,
    }
    hard_facts = {
        "pillars": {"year": "기사", "month": "병자", "day": "병인", "hour": "갑오"},
        "day_master": "병",
        "surface_five_elements": {"wood": 2, "fire": 3, "earth": 1, "metal": 0, "water": 2},
        "calculation_profile": {"profile_id": POLICY_ID},
        "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
    }
    source_versions = {"runtime_release": PARENT_RELEASE_ID, "fixture": "v1"}
    identity = {
        "normalized_birth_input": normalized,
        "policy_id": POLICY_ID,
        "engine_version": ENGINE_VERSION_V15,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "runtime_scope": APPROVED_SCOPE_V15,
        "source_versions": source_versions,
    }
    chart_id = signer.chart_id({**identity, "facts": hard_facts})
    return {
        "status": "ok",
        "code": None,
        "message": "원국 계산 완료",
        "normalized_input": normalized,
        "hard_facts": hard_facts,
        "stable_facts": hard_facts,
        "alternative_charts": [{"hard_facts": hard_facts, "chart_id": chart_id}],
        "fact_authority": "HARD_GT",
        "birth_input_id": signer.birth_input_id(normalized),
        "chart_id": chart_id,
        "chart_set_id": None,
        "calculation_run_id": signer.calculation_run_id(
            {**identity, "chart_id": chart_id, "chart_set_id": None}
        ),
        "engine_version": ENGINE_VERSION_V15,
        "calculation_schema_version": OUTPUT_SCHEMA_VERSION_V15,
        "id_contract_version": ID_CONTRACT_VERSION_V2,
        "policy_id": POLICY_ID,
        "runtime_scope": APPROVED_SCOPE_V15,
        "source_versions": source_versions,
        "limitations": [],
    }


def _state(chart: dict[str, object], *, revision: int = 7) -> dict[str, object]:
    state = empty_session_state()
    state.update(
        {
            "state_revision": revision,
            "saju_opt_in": True,
            "current_intent": "period",
            "birth_slots": _birth_slots(),
            "chart": deepcopy(chart),
        }
    )
    return state


def _fake_engine(result: dict[str, object]) -> ApprovedSajuRuntimeEngineV15:
    engine = object.__new__(ApprovedSajuRuntimeEngineV15)
    engine.release = {"release_id": PARENT_RELEASE_ID}
    engine.calculate_chart = lambda arguments: deepcopy(result)  # type: ignore[method-assign]
    return engine


class PeriodContractV1Tests(unittest.TestCase):
    def test_registry_and_public_request_contract(self) -> None:
        registry = validate_contract_registry()
        self.assertEqual(registry["registry_id"], "saju-period-contract-registry-v1.0.0")
        self.assertEqual(
            validate_public_period_event(_event("today"))["request"]["start_date"],
            None,
        )
        explicit = validate_public_period_event(
            _event("explicit", "2026-09-02", "2026-09-30")
        )
        self.assertEqual(explicit["request"]["end_date"], "2026-09-30")

    def test_duplicate_key_and_server_owned_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodRuntimeError, "중복 key"):
            load_public_period_event(
                '{"type":"request_period","type":"request_period","request":{}}'
            )
        forbidden_fields = (
            "chart_id",
            "timezone",
            "reference_date",
            "policy_id",
            "release_id",
            "state_revision",
        )
        for field in forbidden_fields:
            with self.subTest(field=field), self.assertRaises(PeriodRuntimeError):
                validate_public_period_event({**_event("today"), field: "forged"})

    def test_cross_fields_and_unsupported_expression_are_rejected(self) -> None:
        with self.assertRaisesRegex(PeriodRuntimeError, "지정할 수 없습니다"):
            validate_public_period_event(_event("today", "2026-09-02", "2026-09-02"))
        with self.assertRaisesRegex(PeriodRuntimeError, "지원하지 않는"):
            validate_public_period_event(_event("year"))
        with self.assertRaises(PeriodRuntimeError):
            validate_public_period_event(_event("explicit", "2026-9-2", "2026-09-03"))

    def test_relative_date_resolution_is_deterministic(self) -> None:
        reference = date(2026, 9, 2)
        expected = {
            "today": ("2026-09-02", "2026-09-02", 1),
            "tomorrow": ("2026-09-03", "2026-09-03", 1),
            "this_weekend": ("2026-09-05", "2026-09-06", 2),
            "this_week": ("2026-09-02", "2026-09-06", 5),
            "this_month": ("2026-09-02", "2026-09-30", 29),
        }
        for expression, values in expected.items():
            with self.subTest(expression=expression):
                scope = resolve_period_scope(_event(expression), reference_date=reference)
                self.assertEqual(
                    (scope["start_date"], scope["end_date"], scope["day_count"]),
                    values,
                )
                self.assertFalse(scope["intraday_segments_supported"])
                self.assertFalse(scope["future_physical_instant_claimed"])

    def test_weekend_saturday_and_sunday_do_not_roll_forward(self) -> None:
        saturday = resolve_period_scope(
            _event("this_weekend"), reference_date=date(2026, 9, 5)
        )
        sunday = resolve_period_scope(
            _event("this_weekend"), reference_date=date(2026, 9, 6)
        )
        self.assertEqual((saturday["start_date"], saturday["end_date"]), ("2026-09-05", "2026-09-06"))
        self.assertEqual((sunday["start_date"], sunday["end_date"]), ("2026-09-06", "2026-09-06"))

    def test_explicit_range_enforces_forward_31_day_and_release_bounds(self) -> None:
        accepted = resolve_period_scope(
            _event("explicit", "2026-09-02", "2026-10-02"),
            reference_date=date(2026, 9, 2),
        )
        self.assertEqual(accepted["day_count"], 31)
        cases = [
            _event("explicit", "2026-09-01", "2026-09-02"),
            _event("explicit", "2026-09-02", "2026-10-03"),
            _event("explicit", "2050-01-01", "2050-01-01"),
            _event("explicit", "2026-09-03", "2026-09-02"),
        ]
        for event in cases:
            with self.subTest(event=event), self.assertRaises(PeriodRuntimeError):
                resolve_period_scope(event, reference_date=date(2026, 9, 2))

    def test_release_floor_is_not_silently_shifted(self) -> None:
        with self.assertRaisesRegex(PeriodRuntimeError, "2026-09-02"):
            resolve_period_scope(_event("today"), reference_date=date(2026, 9, 1))
        explicit = resolve_period_scope(
            _event("explicit", "2026-09-02", "2026-09-02"),
            reference_date=date(2026, 9, 1),
        )
        self.assertEqual(explicit["start_date"], "2026-09-02")

    def test_chart_rehydration_matches_three_fingerprints(self) -> None:
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        chart = _signed_chart(signer)
        authorization = rehydrate_exact_chart(
            _state(chart),
            expected_revision=7,
            engine=_fake_engine(chart),
            signer=signer,
        )
        self.assertEqual(authorization["chart_id"], chart["chart_id"])
        self.assertEqual(authorization["state_revision"], 7)
        self.assertFalse(authorization["publicly_exposable"])

    def test_rehydration_rejects_stale_tampered_unknown_and_wrong_signer(self) -> None:
        signer = RuntimeIdSigner.for_test(bytes(range(32)))
        chart = _signed_chart(signer)
        with self.assertRaisesRegex(PeriodRuntimeError, "revision"):
            rehydrate_exact_chart(
                _state(chart),
                expected_revision=6,
                engine=_fake_engine(chart),
                signer=signer,
            )
        tampered = _state(chart)
        tampered["chart"]["hard_facts"]["day_master"] = "정"  # type: ignore[index]
        with self.assertRaisesRegex(PeriodRuntimeError, "일치하지 않습니다"):
            rehydrate_exact_chart(
                tampered,
                expected_revision=7,
                engine=_fake_engine(chart),
                signer=signer,
            )
        unknown = _state(chart)
        unknown["birth_slots"] = _birth_slots(precision="unknown")
        with self.assertRaisesRegex(PeriodRuntimeError, "exact"):
            rehydrate_exact_chart(
                unknown,
                expected_revision=7,
                engine=_fake_engine(chart),
                signer=signer,
            )
        with self.assertRaisesRegex(PeriodRuntimeError, "재계산 검증"):
            rehydrate_exact_chart(
                _state(chart),
                expected_revision=7,
                engine=_fake_engine(chart),
                signer=RuntimeIdSigner.for_test(bytes(reversed(range(32)))),
            )


@unittest.skipUnless(EPHEMERIS.is_file(), "private DE440s fixture가 없습니다.")
class PeriodRehydrationIntegrationTests(unittest.TestCase):
    def test_exact_chart_is_rehydrated_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "runtime-hmac.key"
            key_path.write_bytes(bytes(range(32)))
            os.chmod(key_path, 0o600)
            with ApprovedSajuRuntimeEngineV15(
                release_registry=RELEASE_V15_PATH,
                enable_approved_runtime=True,
                ephemeris_path=EPHEMERIS,
                id_key_file=key_path,
                today_provider=lambda: date(2026, 9, 2),
            ) as first:
                chart = first.calculate_chart(_chart_arguments())
            state = _state(chart)

            signer = RuntimeIdSigner.from_key_file(key_path)
            with ApprovedSajuRuntimeEngineV15(
                release_registry=RELEASE_V15_PATH,
                enable_approved_runtime=True,
                ephemeris_path=EPHEMERIS,
                id_key_file=key_path,
                today_provider=lambda: date(2026, 9, 2),
            ) as restarted:
                authorization = rehydrate_exact_chart(
                    state,
                    expected_revision=7,
                    engine=restarted,
                    signer=signer,
                )
                period = restarted.calculate_period(
                    {
                        "chart_id": authorization["chart_id"],
                        "period_type": "day",
                        "start_date": "2026-09-02",
                        "end_date": "2026-09-02",
                        "timezone": "Asia/Seoul",
                    }
                )
            self.assertEqual(period["status"], "ok")
            self.assertEqual(period["fact_authority"], "HARD_GT")


if __name__ == "__main__":
    unittest.main()
