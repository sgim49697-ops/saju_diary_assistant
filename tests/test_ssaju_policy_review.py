# test_ssaju_policy_review.py - ssaju 정책 비교기와 공개 충돌 표본 계약을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.data import ssaju_policy_review as review


class TenGodPolicyTests(unittest.TestCase):
    def test_stem_ten_god_relationships(self) -> None:
        expected = {
            "甲": "비견",
            "乙": "겁재",
            "丙": "식신",
            "丁": "상관",
            "戊": "편재",
            "己": "정재",
            "庚": "편관",
            "辛": "정관",
            "壬": "편인",
            "癸": "정인",
        }
        self.assertEqual(
            {stem: review.stem_ten_god("甲", stem) for stem in review.STEMS},
            expected,
        )

    def test_only_four_branches_change_under_main_hidden_stem_policy(self) -> None:
        changed = {
            branch
            for branch in review.BRANCHES
            if review.surface_branch_ten_god("甲", branch)
            != review.hidden_stem_branch_ten_god("甲", branch)
        }
        self.assertEqual(changed, {"子", "巳", "午", "亥"})

    def test_ssaju_strength_replays_external_golden_chart(self) -> None:
        strength, score = review.ssaju_strength("辛巳戊戌庚午癸未")
        self.assertEqual(strength, "strong")
        self.assertEqual(score, 78)


class NemotronParserTests(unittest.TestCase):
    def test_parse_structured_record(self) -> None:
        chart = "辛丑己亥甲寅庚午"
        user = (
            "사주 원국: 년주 辛丑 월주 己亥 일주 甲寅 시주 庚午\n"
            '오행 분포: {"土": 2, "木": 2, "水": 1, "火": 1, "金": 2}\n'
            "십신: 년주 천간 정관, 지지 정재; 월주 천간 정재, 지지 정인; "
            "일주 천간 본원(일간), 지지 비견; 시주 천간 편관, 지지 식신"
        )
        record = {
            "messages": [{"role": "user", "content": user}],
            "meta": {"chart_signature": chart},
        }
        parsed_chart, ten_gods, elements = review.parse_nemotron_record(record)
        self.assertEqual(
            "".join(parsed_chart[pillar] for pillar in review.PILLARS), chart
        )
        self.assertEqual(ten_gods["day"]["stem"], "본원(일간)")
        self.assertEqual(elements, {"목": 2, "화": 1, "토": 2, "금": 2, "수": 1})
        self.assertEqual(elements, review.chart_elements(parsed_chart))

    def test_rejects_mismatched_chart_signature(self) -> None:
        record = {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "사주 원국: 년주 辛丑 월주 己亥 일주 甲寅 시주 庚午\n"
                        '오행 분포: {"土": 2, "木": 2, "水": 1, "火": 1, "金": 2}\n'
                        "십신: 년주 천간 정관, 지지 정재; 월주 천간 정재, 지지 정인; "
                        "일주 천간 본원(일간), 지지 비견; 시주 천간 편관, 지지 식신"
                    ),
                }
            ],
            "meta": {"chart_signature": "甲子甲子甲子甲子"},
        }
        with self.assertRaises(review.SsajuReviewError):
            review.parse_nemotron_record(record)


class ConflictSampleTests(unittest.TestCase):
    @staticmethod
    def _candidate(branch: str, number: int) -> dict[str, object]:
        return {
            "_record_key": f"nemotron_saju:{branch}-{number:03d}",
            "chart": {"year": "甲子", "month": "甲寅", "day": "甲辰", "hour": "甲午"},
            "day_stem": "甲",
            "conflicts": [
                {
                    "pillar": "year",
                    "branch": branch,
                    "current_policy_id": "branch_surface_element_yinyang_v1",
                    "current_ten_god": "편인",
                    "ssaju_policy_id": "branch_main_hidden_stem_v1",
                    "ssaju_main_hidden_stem": review.MAIN_HIDDEN_STEM[branch],
                    "ssaju_ten_god": "정인",
                }
            ],
        }

    def test_balanced_samples_are_deterministic_and_hide_record_keys(self) -> None:
        candidates = {
            branch: [self._candidate(branch, number) for number in range(30)]
            for branch in ("子", "巳", "午", "亥")
        }
        first = review.make_conflict_samples(candidates)
        second = review.make_conflict_samples(candidates)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 100)
        self.assertEqual(
            {
                branch: sum(item["anchor_branch"] == branch for item in first)
                for branch in candidates
            },
            {"子": 25, "巳": 25, "午": 25, "亥": 25},
        )
        self.assertNotIn("_record_key", json.dumps(first, ensure_ascii=False))
        self.assertEqual(len({item["sample_id"] for item in first}), 100)


class ArtifactTests(unittest.TestCase):
    def test_write_once_artifacts_and_verify_hash_chain(self) -> None:
        report = {
            "review_version": "v1.0.0",
            "scope_guards": {
                "training_data_modified": False,
                "evaluation_data_modified": False,
                "training_run_performed": False,
            },
            "external_repository": {
                "revision": review.EXTERNAL_REVISION,
                "dist_index_mjs_sha256": "1" * 64,
                "node_version": "v22.0.0",
                "npm_version": "10.0.0",
                "checks": {"tests": "passed"},
            },
            "inputs": {"policy_sha256": "2" * 64},
        }
        samples = []
        for branch in ("子", "巳", "午", "亥"):
            for number in range(25):
                samples.append(
                    {
                        "sample_id": f"ssaju-conflict-{branch}-{number:02d}",
                        "anchor_branch": branch,
                        "chart": {"year": "甲子"},
                    }
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "v1.0.0"
            with mock.patch.object(review, "REPORT_ROOT", root):
                output = review.write_artifacts(report, samples)
                self.assertEqual(review.write_artifacts(report, samples), output)
                verified = review.verify_report(output)
        self.assertEqual(verified["sample_rows"], 100)
        self.assertEqual(verified["status"], "verified")


class PolicyContractTests(unittest.TestCase):
    def test_draft_policy_blocks_runtime_and_heuristic_gold(self) -> None:
        policy, digest = review.validate_policy()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertFalse(policy["scope"]["runtime_enabled"])
        for field in ("day_strength", "geukguk", "yongsin"):
            self.assertFalse(policy["fields"][field]["qa_gold_candidate"])
            self.assertEqual(policy["fields"][field]["validator_mode"], "disabled")


class CanonicalReportTests(unittest.TestCase):
    def test_committed_review_keeps_expected_advisory_metrics(self) -> None:
        root = review.REPORT_ROOT / "review-c1e129b1e602"
        verified = review.verify_report(root)
        report = json.loads(
            (root / "comparison_report.json").read_text(encoding="utf-8")
        )
        nemotron = report["dataset_comparison"]["nemotron_saju"]
        bazi = report["dataset_comparison"]["bazi_sft"]
        lunar = report["external_engine_findings"]["manse"]["solar_lunar_roundtrip"]
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(
            nemotron["branch_ten_god_conflict_rows_against_ssaju_main_hidden_stem"],
            8_563,
        )
        self.assertEqual(
            nemotron["branch_ten_god_conflict_fields_against_ssaju_main_hidden_stem"],
            13_808,
        )
        self.assertEqual(bazi["conflict_unique_charts"], 594)
        self.assertEqual(lunar["total_failures"], 2_502)
        self.assertFalse(any(report["scope_guards"].values()))


if __name__ == "__main__":
    unittest.main()
