# test_relation_runtime_v1.py - 단일 날짜 relation 정책·부모 연결·공개 경계를 검증한다.

from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.evaluation.saju_runtime.relation_conformance_v1 import (
    TEST_PERIOD_KEY,
    TEST_RELATION_KEY,
    _chart_fixture,
    _period_fixture,
    verify_report,
)
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.chart_only_security import load_secret_key
from scripts.runtime.period_v1.security import PeriodIdSigner
from scripts.runtime.relation_v1.contracts import (
    RELEASE_PATH,
    REPORT_ROOT,
    load_relation_policy,
    validate_contract_registry,
    validate_release_registry,
)
from scripts.runtime.relation_v1.engine import (
    ApprovedSingleDateRelationEngine,
    branch_relations,
    calculate_relation_candidate,
    period_ten_gods,
    public_relation_result,
    validate_public_relation_result,
)
from scripts.runtime.relation_v1.errors import RelationRuntimeError
from scripts.runtime.relation_v1.security import RelationIdSigner


class RelationPolicyV1Tests(unittest.TestCase):
    def test_contract_policy_report_and_release_are_self_consistent(self) -> None:
        registry = validate_contract_registry()
        policy = load_relation_policy()
        self.assertEqual(
            registry["registry_id"],
            "saju-natal-day-relation-contract-registry-v1.0.0",
        )
        self.assertEqual(policy["authority"], "PROFILE_DETERMINISTIC")
        if RELEASE_PATH.is_file():
            release = validate_release_registry(RELEASE_PATH)
            report = REPO_ROOT / release["conformance_report"]["path"]
            verified = verify_report(report.parent)
            self.assertEqual(verified["stem_ten_god_cases"], 100)
            self.assertEqual(verified["branch_ten_god_cases"], 120)
            self.assertEqual(verified["branch_relation_cases"], 144)
            self.assertEqual(verified["missing_relations"], 0)
            self.assertEqual(verified["unexpected_relations"], 0)
            self.assertFalse(release["feature_flag_default"])
            self.assertTrue(release["single_date_only"])
        else:
            self.assertFalse(REPORT_ROOT.exists())

    def test_overlapping_pairs_and_punishment_rules_are_explicit(self) -> None:
        self.assertEqual(
            branch_relations("寅", "亥"),
            [("합", "symmetric_pair"), ("파", "symmetric_pair")],
        )
        self.assertEqual(
            branch_relations("巳", "申"),
            [
                ("합", "symmetric_pair"),
                ("형", "symmetric_group_pair"),
                ("파", "symmetric_pair"),
            ],
        )
        self.assertEqual(
            branch_relations("申", "巳"), branch_relations("巳", "申")
        )
        self.assertEqual(branch_relations("辰", "辰"), [("형", "symmetric_self")])
        self.assertEqual(branch_relations("子", "子"), [])

    def test_ten_gods_use_natal_day_master_and_branch_main_hidden_stem(self) -> None:
        result = period_ten_gods(
            "甲", {"year": "丙午", "month": "庚申", "day": "癸亥"}
        )
        self.assertEqual(result["year"]["stem_ten_god"], "식신")
        self.assertEqual(result["year"]["branch_main_hidden_stem"], "丁")
        self.assertEqual(result["year"]["branch_ten_god"], "상관")
        self.assertEqual(result["month"]["branch_main_hidden_stem"], "庚")
        self.assertEqual(result["day"]["branch_main_hidden_stem"], "壬")


class RelationParentAndFirewallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.period_signer = PeriodIdSigner.for_test(TEST_PERIOD_KEY)
        self.relation_signer = RelationIdSigner.for_test(TEST_RELATION_KEY)
        self.chart, self.authorization = _chart_fixture()
        self.period = _period_fixture(self.authorization, self.period_signer)

    def _calculate(self, *, chart=None, period=None) -> dict[str, object]:
        return calculate_relation_candidate(
            chart_snapshot=self.chart if chart is None else chart,
            period_snapshot=self.period if period is None else period,
            period_signer=self.period_signer,
            relation_signer=self.relation_signer,
            authority_release_id="candidate-test",
        )

    def test_single_date_candidate_contains_only_fact_relations(self) -> None:
        internal = self._calculate()
        public = public_relation_result(internal)
        self.assertEqual(public["selected_date"], "2026-09-02")
        self.assertEqual(public["fact_authority"], "PROFILE_DETERMINISTIC")
        self.assertEqual(
            public["direct_relations"],
            [
                {
                    "period_part": "day_branch",
                    "period_branch": "卯",
                    "natal_pillar": "year",
                    "natal_branch": "子",
                    "relation": "형",
                    "direction_rule": "symmetric_group_pair",
                    "authority": "PROFILE_DETERMINISTIC",
                    "table_version": "branch-relations-v1.0.0",
                }
            ],
        )
        self.assertTrue(public["interpretation_not_included"])
        serialized = str(public)
        self.assertNotIn("sc2_", serialized)
        self.assertNotIn("spd1_", serialized)
        self.assertNotIn("sr1_", serialized)
        self.assertNotIn("relation_snapshot_id", public)

    def test_range_and_tampered_parents_fail_closed(self) -> None:
        ranged = _period_fixture(
            self.authorization, self.period_signer, day_count=2
        )
        with self.assertRaisesRegex(RelationRuntimeError, "단일 날짜"):
            self._calculate(period=ranged)

        tampered_period = deepcopy(self.period)
        tampered_period["days"][0]["day_ganzhi"] = "庚辰"
        with self.assertRaisesRegex(RelationRuntimeError, "HMAC"):
            self._calculate(period=tampered_period)

        tampered_chart = deepcopy(self.chart)
        tampered_chart["hard_facts"]["pillars"]["year"]["branch"] = "午"
        with self.assertRaises(RelationRuntimeError):
            self._calculate(chart=tampered_chart)

    def test_public_validator_rejects_interpretation_and_internal_ids(self) -> None:
        public = public_relation_result(self._calculate())
        with self.assertRaisesRegex(RelationRuntimeError, "field 집합"):
            validate_public_relation_result({**public, "interpretation": "사건 단정"})
        with self.assertRaisesRegex(RelationRuntimeError, "내부 ID"):
            changed = deepcopy(public)
            changed["limitations"] = ["sr1_" + "a" * 64]
            validate_public_relation_result(changed)

    def test_relation_id_is_release_domain_separated(self) -> None:
        first = self.relation_signer.relation_id("release-a", {"same": True})
        second = self.relation_signer.relation_id("release-b", {"same": True})
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("sr1_"))

    def test_approved_engine_requires_release_and_production_signers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "runtime-hmac.key"
            key_path.write_bytes(TEST_PERIOD_KEY)
            os.chmod(key_path, 0o600)
            secret = load_secret_key(key_path, purpose="runtime-hmac")
            period_signer = PeriodIdSigner.from_runtime_secret(secret)
            relation_signer = RelationIdSigner.from_runtime_secret(secret)
            period = _period_fixture(self.authorization, period_signer)
            engine = ApprovedSingleDateRelationEngine(
                period_signer=period_signer,
                relation_signer=relation_signer,
                release_registry=RELEASE_PATH,
                enable_approved_runtime=True,
            )
            internal, public = engine.calculate(
                chart_snapshot=self.chart,
                period_snapshot=period,
            )
        self.assertTrue(internal["relation_snapshot_id"].startswith("sr1_"))
        self.assertNotIn("relation_snapshot_id", public)
        self.assertFalse(engine.release["feature_flag_default"])


if __name__ == "__main__":
    unittest.main()
