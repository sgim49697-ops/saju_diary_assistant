# test_phase5_dashboard_v1_12.py - 일별 기간 dashboard 계약·복원·model binding을 검증한다.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.period_dashboard_binding import PeriodDashboardBinding
from scripts.runtime.period_v1.contracts_v1_1 import RELEASE_PATH as PERIOD_RELEASE_PATH
from scripts.training.phase5_dashboard_v1_12 import (
    V112_ASSET_ROOT,
    _messages_for_engine,
    _runtime_model_context_from_binding,
    evaluate_bound_output,
    validate_config,
)

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


def _period() -> dict[str, object]:
    return {
        "status": "ok",
        "fact_authority": "HARD_GT",
        "period_scope": {
            "date_expression": "explicit",
            "start_date": "2026-09-02",
            "end_date": "2026-09-04",
            "day_count": 3,
            "timezone": "Asia/Seoul",
            "evaluation_local_time": "12:00",
        },
        "days": [
            {
                "date": f"2026-09-0{day}",
                "year_ganzhi": "병오",
                "month_ganzhi": "병신",
                "day_ganzhi": label,
                "authority": "SOURCE_HARD_FACT",
            }
            for day, label in ((2, "기묘"), (3, "경진"), (4, "신사"))
        ],
        "boundary_capability": {
            "intraday_segments_supported": False,
            "future_physical_instant_claimed": False,
        },
        "message": "합성 일별 기간 계산 완료",
        "limitations": ["날짜 label만 제공합니다."],
    }


def _binding() -> dict[str, object]:
    value = {
        "chart": {
            "status": "ok",
            "fact_authority": "HARD_GT",
            "hard_facts": {
                "pillars": {
                    "year": {"ganzhi": "庚午"},
                    "month": {"ganzhi": "辛巳"},
                    "day": {"ganzhi": "壬辰"},
                    "hour": {"ganzhi": "丁未"},
                },
                "day_master": {"stem": "壬"},
                "surface_five_elements": {
                    "목": 0,
                    "화": 3,
                    "토": 2,
                    "금": 2,
                    "수": 1,
                },
                "calculation_profile": "KR_CIVIL_MIDNIGHT_V1",
                "solar_term_evidence": {"authority": "SOURCE_HARD_FACT"},
            },
            "message": "합성 원국 계산 완료",
            "limitations": [],
        },
        "period": _period(),
    }
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": "1.2.0",
        "binding_id": "saju-period-dashboard-binding-v1.2.0",
        "capability_sha256": "e" * 64,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 8,
        "value": value,
    }


class DashboardV112ContractTests(unittest.TestCase):
    def test_config_assets_and_structured_period_controls_are_versioned(self) -> None:
        config = json.loads(
            (
                REPO_ROOT
                / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.12.0.json"
            ).read_text(encoding="utf-8")
        )
        validate_config(config)
        html = (V112_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V112_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('id="runtime-date-expression"', html)
        self.assertIn('id="runtime-start-date"', html)
        self.assertIn('id="runtime-period-table"', html)
        self.assertIn("이 원국·기간으로 새 대화 시작", html)
        self.assertIn('schema_version: "saju-period-request-v2"', javascript)
        self.assertIn("date_expression: dateExpression", javascript)
        self.assertNotIn("new Date().toISOString", javascript)
        self.assertNotIn('id="runtime-target-date"', html)

    def test_period_snapshot_is_hash_bound_and_same_for_both_engines(self) -> None:
        binding = _binding()
        prompt, digest, capability = _runtime_model_context_from_binding(binding)
        self.assertIn("원국·일별 기간", prompt)
        self.assertIn("2026-09-04", prompt)
        self.assertEqual(digest, binding["snapshot_sha256"])
        self.assertEqual(capability, binding["capability_sha256"])
        k0 = _messages_for_engine([], "k0_instruct", "이번 주를 봐줘", "system", prompt)
        ki20 = _messages_for_engine(
            [], "ki20_final", "이번 주를 봐줘", "system", prompt
        )
        self.assertEqual(k0, ki20)

    def test_snapshot_swap_and_private_fields_are_rejected(self) -> None:
        tampered = json.loads(json.dumps(_binding()))
        tampered["value"]["period"]["days"][0]["date"] = "2026-09-03"
        with self.assertRaisesRegex(Exception, "일별 기간"):
            _runtime_model_context_from_binding(tampered)

        private = json.loads(json.dumps(_binding()))
        private["value"]["chart"]["hard_facts"]["birth_date"] = "1990-01-01"
        with self.assertRaisesRegex(Exception, "금지된 내부 field"):
            _runtime_model_context_from_binding(private)

    def test_grounding_gate_uses_chart_and_period_markers(self) -> None:
        result = evaluate_bound_output(
            "이번 주 사주 흐름을 알려줘",
            "연결된 壬辰 원국과 2026-09-02의 기묘 일진부터 살펴보겠습니다.",
            _binding(),
        )
        self.assertTrue(result["passed"])
        missing = evaluate_bound_output(
            "이번 주 사주 흐름을 알려줘",
            "연결된 壬辰 원국을 살펴보겠습니다.",
            _binding(),
        )
        self.assertIn("period_fact_missing", missing["reasons"])


@unittest.skipUnless(EPHEMERIS.is_file(), "private DE440s fixture가 없습니다.")
class PeriodDashboardRestartIntegrationTests(unittest.TestCase):
    def test_encrypted_session_survives_new_engine_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "store"
            store.mkdir(mode=0o700)
            hmac_key = root / "runtime-hmac.key"
            encryption_key = root / "session-aead.key"
            hmac_key.write_bytes(bytes(range(32)))
            encryption_key.write_bytes(bytes(reversed(range(32))))
            os.chmod(hmac_key, 0o600)
            os.chmod(encryption_key, 0o600)
            lease = root / "period-runtime.lease"

            def open_binding() -> PeriodDashboardBinding:
                return PeriodDashboardBinding(
                    parent_release_registry=RELEASE_V15_PATH,
                    period_release_registry=PERIOD_RELEASE_PATH,
                    ephemeris_path=EPHEMERIS,
                    hmac_key_file=hmac_key,
                    encryption_key_file=encryption_key,
                    store_root=store,
                    process_lease_file=lease,
                )

            binding = open_binding()
            created = binding.create_session()
            session_id = created["session_id"]
            revision = 0
            events = [
                {"type": "opt_in", "accepted": True},
                {"type": "set_slot", "field": "calendar", "value": "solar"},
                {"type": "set_slot", "field": "birth_date", "value": "1990-01-01"},
                {
                    "type": "set_slot",
                    "field": "birthplace",
                    "value": {
                        "country_code": "KR",
                        "city": "서울",
                        "timezone": "Asia/Seoul",
                    },
                },
                {"type": "set_slot", "field": "birth_time", "value": "12:00"},
                {"type": "request_chart"},
                {
                    "type": "request_period",
                    "request": {
                        "schema_version": "saju-period-request-v2",
                        "date_expression": "explicit",
                        "start_date": "2026-09-02",
                        "end_date": "2026-09-04",
                    },
                },
            ]
            for event in events:
                response = binding.handle_event(
                    session_id, expected_revision=revision, event=event
                )
                revision = response["state_revision"]
            first = binding.public_snapshot(session_id)
            binding.close()

            restarted = open_binding()
            try:
                second = restarted.public_snapshot(session_id)
                self.assertEqual(first, second)
                self.assertEqual(len(second["value"]["period"]["days"]), 3)
                encoded = json.dumps(second, ensure_ascii=False)
                self.assertNotIn(session_id, encoded)
                self.assertNotIn("period_id", encoded)
                self.assertNotIn("birth_date", encoded)
            finally:
                restarted.close()


if __name__ == "__main__":
    unittest.main()
