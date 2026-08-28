# test_phase2b_preprocess.py - 24K staging 수량·한국어 렌더링·YEJI evaluator Gate를 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.data.audit_tools import apply_yeji_corrections
from scripts.data.phase2b_review_web import ReviewState
from scripts.data.phase2b_verify_history import verify_historical_staging
from scripts.data.preprocess_adapters import (
    evaluate_yeji_rule,
    expected_bazi_rules,
)
from scripts.data.preprocess_tools import (
    _validate_language_bank,
    load_staging_config,
    staging_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_ROOT = REPO_ROOT / "configs/data_versions/saju_1b_baseline"
CONFIG_PATH = VERSION_ROOT / "preprocessing-staging-v0.1.0.json"
BUILD_ID = "build-109815ee6879"


class StagingContractTests(unittest.TestCase):
    def test_mix20k_plus_twenty_percent_contract_is_exact(self) -> None:
        config = load_staging_config(CONFIG_PATH)
        self.assertEqual(config["scope"]["final_train_rows"], 20_000)
        self.assertEqual(config["scope"]["staging_rows"], 24_000)
        self.assertFalse(config["scope"]["full_source_conversion"])
        self.assertEqual(
            {
                axis: value["staging_rows"]
                for axis, value in config["axes"].items()
            },
            {
                "nemotron_saju": 13_200,
                "bazi_sft": 6_000,
                "aihub_empathy_single": 2_400,
                "aihub_empathy_multiturn": 1_200,
                "yeji_shensha_derived": 1_200,
            },
        )
        self.assertEqual(
            config["axes"]["nemotron_saju"]["variants"],
            {"v6": 2_640, "v7": 10_560},
        )

    def test_plan_tracks_current_inputs_without_reusing_historical_build_id(self) -> None:
        plan = staging_plan(REPO_ROOT, CONFIG_PATH)
        self.assertRegex(plan["build_id"], r"^build-[0-9a-f]{12}$")
        self.assertNotEqual(plan["build_id"], BUILD_ID)
        self.assertEqual(plan["audit_status"], "approved")
        self.assertEqual(plan["total_target_rows"], 24_000)
        self.assertFalse(plan["promotion_allowed"])

    def test_existing_build_is_owner_risk_accepted_but_not_train_promoted(self) -> None:
        private_path = (
            REPO_ROOT
            / "data/staging/saju_1b_baseline/v0.1.0"
            / BUILD_ID
        )
        if not private_path.is_dir():
            self.skipTest("Git 제외 staging build가 없는 환경입니다.")
        result = verify_historical_staging(
            REPO_ROOT,
            staging_version="v0.1.0",
            build_id=BUILD_ID,
            implementation_commit=None,
        )
        self.assertEqual(result["total_rows"], 24_000)
        self.assertEqual(result["approval_status"], "owner_risk_accepted")
        self.assertTrue(result["owner_risk_accepted"])
        self.assertFalse(result["promotion_allowed"])

        state = ReviewState(result, "127.0.0.1", 8765)
        bootstrap = state.bootstrap()
        self.assertEqual(bootstrap["approval_status"], "owner_risk_accepted")
        self.assertTrue(bootstrap["read_only"])
        self.assertEqual(len(bootstrap["items"]), 300)
        with self.assertRaisesRegex(ValueError, "승인이 완료된"):
            state.save_decision(
                {
                    "id": next(iter(state.allowed_ids)),
                    "decision": "exclude_candidate",
                    "note": "승인 후 변경 시도",
                }
            )


class BaziRuleTests(unittest.TestCase):
    def test_rule_recalculation_matches_five_fixed_conditions(self) -> None:
        facts = {
            "day_master": {"element": "Water"},
            "element_counts": {
                "Wood": 0,
                "Fire": 0,
                "Earth": 2,
                "Metal": 3,
                "Water": 3,
            },
        }
        self.assertEqual(
            expected_bazi_rules(facts),
            {
                "day_master_strong",
                "dm_supported",
                "missing_elements",
            },
        )

    def test_weak_dominant_and_missing_conditions_are_independent(self) -> None:
        facts = {
            "day_master": {"element": "Wood"},
            "element_counts": {
                "Wood": 1,
                "Fire": 4,
                "Earth": 2,
                "Metal": 1,
                "Water": 0,
            },
        }
        self.assertEqual(
            expected_bazi_rules(facts),
            {"day_master_weak", "dominant_element", "missing_elements"},
        )


class LanguageBankTests(unittest.TestCase):
    def test_language_bank_has_exact_yeji_identity(self) -> None:
        bank = json.loads(
            (VERSION_ROOT / "language-bank-v1.0.0.json").read_text(encoding="utf-8")
        )
        source = json.loads(
            (
                REPO_ROOT
                / "data/raw/yeji_bazi_rules/84583ca54e8fce257d3d5efd015bca1263a1cfe9"
                / "rules/shensha_51.json"
            ).read_text(encoding="utf-8")
        )
        content = _validate_language_bank(bank, source["shensha_list"])
        self.assertEqual(len(content["yeji"]), 51)
        self.assertEqual(content["yeji"][10]["name_ko"], "사관")


class YejiEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = json.loads(
            (
                REPO_ROOT
                / "data/raw/yeji_bazi_rules/84583ca54e8fce257d3d5efd015bca1263a1cfe9"
                / "rules/shensha_51.json"
            ).read_text(encoding="utf-8")
        )
        correction = json.loads(
            (VERSION_ROOT / "yeji-rule-corrections-v1.2.0.json").read_text(
                encoding="utf-8"
            )
        )
        cls.rules = apply_yeji_corrections(raw, correction)[0]["shensha_list"]

    def test_valid_pillar_positive_and_negative(self) -> None:
        rule = self.rules[11]
        self.assertTrue(
            evaluate_yeji_rule(
                rule, ("甲子", "乙丑", "壬辰", "丁卯"), sex="남성"
            )
        )
        self.assertFalse(
            evaluate_yeji_rule(
                rule, ("甲子", "乙丑", "丙寅", "丁卯"), sex="남성"
            )
        )

    def test_ciguan_overlay_uses_ren_shen_not_ren_mao(self) -> None:
        rule = self.rules[10]
        self.assertEqual(rule["condition"]["mapping"]["金"]["간지"], "壬申")
        self.assertTrue(
            evaluate_yeji_rule(
                rule, ("甲子", "壬申", "丙寅", "丁卯"), sex="여성"
            )
        )
        self.assertFalse(
            evaluate_yeji_rule(
                rule, ("甲子", "癸酉", "丙寅", "丁卯"), sex="여성"
            )
        )

    def test_tongzi_corrected_or_semantics(self) -> None:
        rule = self.rules[37]
        self.assertTrue(
            evaluate_yeji_rule(
                rule, ("甲子", "丙寅", "戊子", "庚午"), sex="남성"
            )
        )
        self.assertFalse(
            evaluate_yeji_rule(
                rule, ("甲子", "丙寅", "庚申", "壬申"), sex="남성"
            )
        )


if __name__ == "__main__":
    unittest.main()
