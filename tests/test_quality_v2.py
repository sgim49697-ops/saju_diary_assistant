# test_quality_v2.py - 품질 보정 staging v1의 계산·스키마·공개 경계를 검증한다.

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.data import quality_v2_tools as quality
from scripts.data.preprocess_adapters import sha256_json

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/preprocessing-staging-v1.0.0.json"
)
POLICY_PATH = REPO_ROOT / "configs/saju_calculation_policy-v1.0.0.json"
POLICY_SHA256 = "d6a20582dd52d3928674b3e6a65a586d6f71c1e22e2ab8b925ca431debf3286c"


def _messages(assistant: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "검증된 사실만 답하세요."},
        {"role": "user", "content": "구조화된 입력을 확인해 주세요."},
        {"role": "assistant", "content": assistant},
    ]


def _source_record(*, axis: str, source: str, assistant: str) -> dict[str, object]:
    messages = _messages(assistant)
    return {
        "id": f"{axis}:fixture",
        "source": source,
        "mix_axis": axis,
        "source_variant": "fixture",
        "source_revision": "fixture-revision",
        "license_expression": "PROJECT-GENERATED",
        "usage_class": "train_allow",
        "provenance_status": "verified",
        "attribution_ids": [],
        "transformation_chain": ["fixed_disclaimer_appended"],
        "domain": "saju",
        "task": "fixture",
        "messages": messages,
        "label": {"stage": "D", "kind": "fixture", "origin": "fixture"},
        "quality_flags": {},
        "meta": {
            "raw_hash": "raw-fixture",
            "source_group_id": "fixture-group",
            "leakage_group_id": "fixture-group",
            "candidate_rank": "0" * 64,
            "message_sha256": sha256_json(messages),
        },
    }


class QualityContractTests(unittest.TestCase):
    def test_contract_has_exact_nested_mix_and_fail_closed_flags(self) -> None:
        config = quality.load_quality_config(CONFIG_PATH, REPO_ROOT)
        axes = config["axes"]
        self.assertEqual(sum(value["staging_rows"] for value in axes.values()), 24_000)
        self.assertEqual(sum(value["mix20k"] for value in axes.values()), 20_000)
        self.assertEqual(sum(value["mix10k"] for value in axes.values()), 10_000)
        self.assertEqual(sum(value["mix1k"] for value in axes.values()), 1_000)
        for value in axes.values():
            self.assertEqual(value["mix20k"], value["mix10k"] * 2)
            self.assertEqual(value["mix10k"], value["mix1k"] * 10)
        self.assertFalse(config["scope"]["overwrite_existing_builds"])
        self.assertFalse(config["scope"]["phase5_training_performed"])
        self.assertEqual(config["quality_contract"]["critical_or_high_allowed"], 0)
        self.assertEqual(config["calendar_backend"]["version"], "1.4.8")
        self.assertEqual(config["calendar_backend"]["max_attempts_per_case"], 200_000)

    def test_calculation_policy_matches_independent_oracle(self) -> None:
        config = quality.load_quality_config(CONFIG_PATH, REPO_ROOT)
        report = quality.validate_calculation_policy(config, REPO_ROOT)
        self.assertEqual(report["hidden_stem_table_checks"], 12)
        self.assertEqual(report["hidden_stem_branch_ten_god_checks"], 120)
        self.assertFalse(report["expert_certification"])

    def test_policy_blocks_weak_heuristics_and_birth_conversion(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        blocked = set(policy["contracts"]["blocked_qa_fields"])
        self.assertTrue(
            {
                "birth_to_pillars",
                "day_strength",
                "geukguk",
                "yongsin",
                "automatic_interpretation",
                "remedy_advice",
            }
            <= blocked
        )
        self.assertFalse(
            policy["calendar_boundary"]["birth_to_pillars_training_allowed"]
        )
        self.assertFalse(policy["scope"]["quality_certification_claimed"])


class RemediationTests(unittest.TestCase):
    def test_message_lengths_count_each_message_once(self) -> None:
        messages = _messages("답변")
        expected_input = len(messages[0]["content"]) + len(messages[1]["content"])
        self.assertEqual(
            quality._message_lengths(messages),
            (expected_input, len("답변"), expected_input + len("답변")),
        )

    def test_contextual_name_replacement_preserves_particles(self) -> None:
        updated, count = quality._replace_source_names(
            "김민수님께서는 오늘 힘들었고, 이서연 씨에게 도움을 구했습니다."
        )
        self.assertEqual(count, 2)
        self.assertEqual(
            updated,
            "이 사람은 오늘 힘들었고, 이 사람에게 도움을 구했습니다.",
        )

    def test_branch_ten_gods_are_rewritten_from_main_hidden_stems(self) -> None:
        user = (
            "사주 원국: 년주 辛丑 월주 己亥 일주 甲寅 시주 庚午\n"
            "부족 오행: 없음\n"
            "십신: 년주 천간 정관, 지지 정재; 월주 천간 정재, 지지 정인; "
            "일주 천간 본원(일간), 지지 비견; 시주 천간 편관, 지지 식신"
        )
        chart = {"year": "辛丑", "month": "己亥", "day": "甲寅", "hour": "庚午"}
        updated, changed = quality._correct_ten_god_line(user, chart)
        self.assertEqual(changed, 1)
        self.assertIn("월주 천간 정재, 지지 편인", updated)
        self.assertIn("시주 천간 편관, 지지 상관", updated)
        self.assertEqual(quality._correct_ten_god_line(updated, chart), (updated, 0))

    def test_balance_advice_is_non_prescriptive(self) -> None:
        self.assertEqual(
            quality._neutral_balance_line("부족 오행: 없음"),
            "오행 균형 참고: 표면 오행 분포에서 빠진 오행은 없습니다. "
            "색상·방향·소품 같은 보완 행동을 이 정보만으로 권하지 않습니다.",
        )
        self.assertIn(
            "행동 처방을 뜻하지 않습니다",
            quality._neutral_balance_line("부족 오행: 금, 수"),
        )

    def test_bazi_fixed_disclaimer_is_removed_from_assistant(self) -> None:
        source = _source_record(
            axis="bazi_sft",
            source="bazi_sft",
            assistant="규칙 기반 답변 " + quality.BAZI_DISCLAIMER,
        )
        transformed, report = quality._transform_bazi(
            [source], policy_sha256=POLICY_SHA256
        )
        self.assertEqual(report["fixed_disclaimer_removed"], 1)
        self.assertEqual(quality._assistant_text(transformed[0]), "규칙 기반 답변")
        self.assertNotIn(
            "fixed_disclaimer_appended", transformed[0]["transformation_chain"]
        )

    def test_yeji_particle_resolver_changes_only_vowelless_name_error(self) -> None:
        self.assertEqual(
            quality._fix_yeji_particle("도화이 성립합니다.", "도화"),
            ("도화가 성립합니다.", 1),
        )
        self.assertEqual(
            quality._fix_yeji_particle("천덕귀인이 성립합니다.", "천덕귀인"),
            ("천덕귀인이 성립합니다.", 0),
        )


class GeneratedRecordTests(unittest.TestCase):
    def test_qa_categories_emit_only_enabled_hard_facts(self) -> None:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        chart = ("辛丑", "己亥", "甲寅", "庚午")
        for category in quality.QA_CATEGORIES:
            messages, rule_ids = quality._qa_messages(chart, category, policy)
            self.assertEqual(
                [message["role"] for message in messages],
                ["system", "user", "assistant"],
            )
            self.assertTrue(rule_ids)
            self.assertNotRegex(
                messages[-1]["content"],
                r"신강약|격국|용신|미래 사건|색상|방향|소품",
            )

    def test_bridge_record_keeps_both_leakage_groups_and_restricted_flag(self) -> None:
        messages = _messages("공감 답변과 검증 사실입니다.")
        record = quality._base_generated_record(
            record_id="saju_diary_bridge:fixture",
            source="aihub_empathy",
            mix_axis="saju_diary_bridge",
            source_variant="fixture",
            source_revision="fixture-revision",
            license_expression="AIHUB-LOCAL-ONLY",
            usage_class="train_allow_local_only",
            attribution_ids=("fixture",),
            transformation_chain=("verified_hard_fact_bridge_v1",),
            task="saju_diary_empathy_bridge",
            messages=messages,
            tier="STYLE_REFERENCE",
            raw_hash="raw-fixture",
            source_group_id="aihub-talk:fixture",
            leakage_group_ids=("chart:fixture", "aihub-talk:fixture"),
            candidate_rank="0" * 64,
            policy_sha256=POLICY_SHA256,
            extra_meta={"external_sharing_allowed": False},
            label_updates={"hard_claim_tier": "HARD_GT"},
        )
        self.assertEqual(
            record["meta"]["leakage_group_ids"],
            ["aihub-talk:fixture", "chart:fixture"],
        )
        self.assertEqual(
            quality._validate_record(
                record,
                axis="saju_diary_bridge",
                expected_source="aihub_empathy",
                policy_sha256=POLICY_SHA256,
            ),
            [],
        )

    def test_public_contract_forbids_aihub_text_and_identifiers(self) -> None:
        config = quality.load_quality_config(CONFIG_PATH, REPO_ROOT)
        reservoir = config["aihub_reservoir"]
        self.assertFalse(reservoir["git_or_external_source_text_allowed"])
        self.assertEqual(reservoir["file_mode"], "0600")
        self.assertFalse(config["scope"]["quality_certification_claimed"])


if __name__ == "__main__":
    unittest.main()
