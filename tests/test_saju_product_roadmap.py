# test_saju_product_roadmap.py - 후속 로드맵의 파일 순서·링크·현재 버전·자동 Gate 경계를 검증한다.

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP_ROOT = REPO_ROOT / "implementation/plans/saju_product_roadmap"
ORDERED_FILES = (
    "00-current-baseline.md",
    "10-period-contract-and-restore.md",
    "20-daily-range-runtime.md",
    "30-period-dashboard.md",
    "40-day-relation-runtime.md",
    "50-automatic-model-evaluation.md",
    "60-mix20k-v3-1-build.md",
    "70-training-and-promotion.md",
)
FORBIDDEN_REQUIRED_GATES = (
    "사람 Blind",
    "사람 blind",
    "사람 검수",
    "전문가 검수",
    "human blind",
    "human_gate: true",
)


class SajuProductRoadmapTests(unittest.TestCase):
    def test_index_uses_current_runtime_and_execution_order(self) -> None:
        index = (ROADMAP_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("2298573ec2a2a14e6ae003ca45bccdeda54ade37", index)
        self.assertIn("saju-runtime-release-v1.5.0-8b1d6ea2d46e", index)
        self.assertIn("dashboard v1.11", index)
        offsets = [index.index(name) for name in ORDERED_FILES]
        self.assertEqual(offsets, sorted(offsets))

    def test_local_markdown_links_resolve(self) -> None:
        for path in (ROADMAP_ROOT / "README.md", *ROADMAP_ROOT.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]+)?\)", text):
                if "://" in target or target.startswith("mailto:"):
                    continue
                self.assertTrue((path.parent / target).resolve().exists(), f"{path}: {target}")

    def test_current_roadmap_has_no_nonautomatic_required_gate(self) -> None:
        current = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ROADMAP_ROOT.glob("*.md"))
        )
        for phrase in FORBIDDEN_REQUIRED_GATES:
            self.assertNotIn(phrase, current)
        self.assertIn("not_measured", current)
        self.assertIn("계약 밖 평가를 완료 조건으로 추가하지 않는다", current)

    def test_period_scope_stays_daily_label_only(self) -> None:
        period = (ROADMAP_ROOT / "20-daily-range-runtime.md").read_text(encoding="utf-8")
        self.assertIn("263,717", period)
        self.assertIn('"intraday_segments_supported": false', period)
        self.assertIn('"future_physical_instant_claimed": false', period)
        self.assertIn("어제·과거·연간 범위는 차단", period)


if __name__ == "__main__":
    unittest.main()
