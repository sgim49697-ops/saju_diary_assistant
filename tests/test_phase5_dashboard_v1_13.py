# test_phase5_dashboard_v1_13.py - 단일 날짜 relation 카드·model binding·재시작을 검증한다.

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.evaluation.saju_runtime.relation_conformance_v1 import (
    TEST_PERIOD_KEY,
    TEST_RELATION_KEY,
    _chart_fixture,
    _period_fixture,
)
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.chart_day_adapter import public_chart
from scripts.runtime.period_v1.contracts_v1_1 import RELEASE_PATH as PERIOD_RELEASE_PATH
from scripts.runtime.period_v1.engine import public_daily_label_result
from scripts.runtime.period_v1.security import PeriodIdSigner
from scripts.runtime.relation_dashboard_binding import RelationDashboardBinding
from scripts.runtime.relation_v1.contracts import RELEASE_PATH as RELATION_RELEASE_PATH
from scripts.runtime.relation_v1.engine import (
    calculate_relation_candidate,
    public_relation_result,
)
from scripts.runtime.relation_v1.security import RelationIdSigner
from scripts.training.phase5_dashboard_v1_13 import (
    V113_ASSET_ROOT,
    _messages_for_engine,
    _runtime_model_context_from_binding,
    evaluate_bound_output,
    period_runtime_status_payload,
    validate_config,
)

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


def _binding(*, day_count: int = 1) -> dict[str, object]:
    chart_internal, authorization = _chart_fixture()
    period_signer = PeriodIdSigner.for_test(TEST_PERIOD_KEY)
    period_internal = _period_fixture(
        authorization, period_signer, day_count=day_count
    )
    chart = public_chart(chart_internal)
    period = public_daily_label_result(period_internal)
    relation = None
    if day_count == 1:
        relation = public_relation_result(
            calculate_relation_candidate(
                chart_snapshot=chart_internal,
                period_snapshot=period_internal,
                period_signer=period_signer,
                relation_signer=RelationIdSigner.for_test(TEST_RELATION_KEY),
                authority_release_id="candidate-dashboard-test",
            )
        )
    value = {"chart": chart, "period": period, "relation": relation}
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "schema_version": "1.3.0",
        "binding_id": "saju-relation-dashboard-binding-v1.3.0",
        "capability_sha256": "e" * 64,
        "snapshot_sha256": hashlib.sha256(encoded).hexdigest(),
        "state_revision": 8,
        "value": value,
    }


class DashboardV113ContractTests(unittest.TestCase):
    def test_disabled_runtime_status_keeps_v113_identity(self) -> None:
        class StubServer:
            def __init__(self) -> None:
                self.context = {"period_runtime": None}
                self.period_binding = None
                self.period_runtime_requested = False
                self.remote_unauthenticated = False

        status = period_runtime_status_payload(StubServer())
        self.assertEqual(status["schema_version"], "1.3.0")
        self.assertFalse(status["enabled"])
        self.assertEqual(status["code"], "RUNTIME_NOT_CONFIGURED")

    def test_config_assets_and_relation_card_are_versioned(self) -> None:
        config = json.loads(
            (
                REPO_ROOT
                / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.13.0.json"
            ).read_text(encoding="utf-8")
        )
        validate_config(config)
        html = (V113_ASSET_ROOT / "index.html").read_text(encoding="utf-8")
        javascript = (V113_ASSET_ROOT / "dashboard.js").read_text(encoding="utf-8")
        self.assertIn('id="runtime-relation-card"', html)
        self.assertIn('id="runtime-relation-ten-gods"', html)
        self.assertIn('id="runtime-direct-relations"', html)
        self.assertIn('id="runtime-repetitions"', html)
        self.assertIn("relation.period_ten_gods", javascript)
        self.assertIn("범위 관계 배열은 생성하지 않았습니다", javascript)
        self.assertNotIn('id="runtime-target-date"', html)

    def test_single_date_relation_is_hash_bound_and_same_for_both_engines(self) -> None:
        binding = _binding()
        prompt, digest, capability = _runtime_model_context_from_binding(binding)
        self.assertIn("원국·일별 기간·단일 날짜 관계", prompt)
        self.assertIn("direct_relations", prompt)
        self.assertIn("interpretation_not_included", prompt)
        self.assertEqual(digest, binding["snapshot_sha256"])
        self.assertEqual(capability, binding["capability_sha256"])
        k0 = _messages_for_engine([], "k0_instruct", "오늘을 봐줘", "system", prompt)
        ki20 = _messages_for_engine([], "ki20_final", "오늘을 봐줘", "system", prompt)
        self.assertEqual(k0, ki20)

    def test_range_keeps_daily_labels_and_requires_null_relation(self) -> None:
        binding = _binding(day_count=2)
        prompt, _, _ = _runtime_model_context_from_binding(binding)
        self.assertIn('"relation":null', prompt)
        changed = json.loads(json.dumps(binding, ensure_ascii=False))
        changed["value"]["relation"] = _binding()["value"]["relation"]
        canonical = json.dumps(
            changed["value"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        changed["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
        with self.assertRaisesRegex(Exception, "relation 배열"):
            _runtime_model_context_from_binding(changed)

    def test_relation_parent_hash_and_private_fields_are_rejected(self) -> None:
        tampered = json.loads(json.dumps(_binding(), ensure_ascii=False))
        tampered["value"]["relation"]["provenance"][
            "period_snapshot_sha256"
        ] = "0" * 64
        canonical = json.dumps(
            tampered["value"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        tampered["snapshot_sha256"] = hashlib.sha256(canonical).hexdigest()
        with self.assertRaisesRegex(Exception, "hash"):
            _runtime_model_context_from_binding(tampered)

        private = json.loads(json.dumps(_binding(), ensure_ascii=False))
        private["value"]["relation"]["relation_snapshot_id"] = "sr1_" + "a" * 64
        with self.assertRaisesRegex(Exception, "relation model binding"):
            _runtime_model_context_from_binding(private)

    def test_grounding_gate_keeps_chart_and_period_markers(self) -> None:
        result = evaluate_bound_output(
            "오늘 사주 흐름을 알려줘",
            "연결된 丙寅 원국과 2026-09-02의 己卯 일진부터 살펴보겠습니다.",
            _binding(),
        )
        self.assertTrue(result["passed"])


@unittest.skipUnless(EPHEMERIS.is_file(), "private DE440s fixture가 없습니다.")
class RelationDashboardRestartIntegrationTests(unittest.TestCase):
    def test_single_relation_survives_restart_and_range_drops_relation(self) -> None:
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
            lease = root / "relation-runtime.lease"

            def open_binding() -> RelationDashboardBinding:
                return RelationDashboardBinding(
                    parent_release_registry=RELEASE_V15_PATH,
                    period_release_registry=PERIOD_RELEASE_PATH,
                    relation_release_registry=RELATION_RELEASE_PATH,
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
                        "end_date": "2026-09-02",
                    },
                },
            ]
            for event in events:
                response = binding.handle_event(
                    session_id, expected_revision=revision, event=event
                )
                revision = response["state_revision"]
            first = binding.public_snapshot(session_id)
            self.assertIsNotNone(first["value"]["relation"])
            self.assertEqual(
                first["value"]["relation"]["selected_date"], "2026-09-02"
            )
            binding.close()

            restarted = open_binding()
            try:
                second = restarted.public_snapshot(session_id)
                self.assertEqual(first, second)
                ranged = restarted.handle_event(
                    session_id,
                    expected_revision=revision,
                    event={
                        "type": "request_period",
                        "request": {
                            "schema_version": "saju-period-request-v2",
                            "date_expression": "explicit",
                            "start_date": "2026-09-02",
                            "end_date": "2026-09-04",
                        },
                    },
                )
                self.assertIsNone(ranged["result"]["relation"])
                self.assertIsNone(restarted.public_snapshot(session_id)["value"]["relation"])
                encoded = json.dumps(second, ensure_ascii=False)
                self.assertNotIn(session_id, encoded)
                self.assertNotIn("period_id", encoded)
                self.assertNotIn("relation_snapshot_id", encoded)
                self.assertNotIn("birth_date", encoded)
            finally:
                restarted.close()


if __name__ == "__main__":
    unittest.main()
