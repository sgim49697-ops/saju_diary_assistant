# test_saju_product_roadmap.py - 후속 로드맵의 파일 순서·링크·현재 버전·자동 Gate 경계를 검증한다.

import hashlib
import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP_ROOT = REPO_ROOT / "implementation/plans/saju_product_roadmap"
ROOT_SUMMARY_FILES = (
    "01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md",
    "02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md",
    "03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md",
)
HANDOFF_FILES = (
    "README.md",
    "implementation/plans/README.md",
    "implementation/plans/saju_1b_10k_20k_baseline/README.md",
    "implementation/plans/mix2k_v4_chart_day_lora.md",
    "implementation/plans/dashboard_v1_15_grounding.md",
    "implementation/history/2026-09-05-model-cause-roadmap.md",
)
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
        self.assertIn("saju-product-roadmap-v1.1.0", index)
        self.assertIn("26462137f9a4ef34adb2d3db0dd6eaff6282b309", index)
        self.assertIn("saju-runtime-release-v1.5.0-8b1d6ea2d46e", index)
        self.assertIn("dashboard v1.14 운영 / v1.15 검증 후보·미병합·미배포", index)
        offsets = [index.index(name) for name in ORDERED_FILES]
        self.assertEqual(offsets, sorted(offsets))

    def test_local_markdown_links_resolve(self) -> None:
        paths = (
            *ROADMAP_ROOT.glob("*.md"),
            *(REPO_ROOT / name for name in (*ROOT_SUMMARY_FILES, *HANDOFF_FILES)),
        )
        for path in paths:
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

    def test_summary_docs_delegate_authority_to_the_same_owners(self) -> None:
        for name in ROOT_SUMMARY_FILES:
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(path=name):
                self.assertIn("요약 문서", text)
                for target in (
                    "README.md",
                    "00-current-baseline.md",
                    "50-automatic-model-evaluation.md",
                ):
                    self.assertIn(f"implementation/plans/saju_product_roadmap/{target}", text)
        index = (ROADMAP_ROOT / "README.md").read_text(encoding="utf-8")
        for owner in ("후속 실행 순서", "현재 상태", "원인 분리 진단 상세"):
            self.assertIn(f"**{owner}**", index)

    def test_baseline_separates_remote_candidate_and_running_service(self) -> None:
        text = (ROADMAP_ROOT / "00-current-baseline.md").read_text(encoding="utf-8")
        for marker in (
            "b78f8e630261db7a1561c649d5fadac91e321d58",
            "26462137f9a4ef34adb2d3db0dd6eaff6282b309",
            "0e77621846c4e9894cb40d801e84d59ad57cb0de",
            "미병합·미배포",
            "saju-mix2k-r16-dashboard-v1-14.service",
            "기본 `ki20_final`",
            "R8·R16·R32",
            "accepted 238/400",
            "현재 R16에 반영되지 않았다",
            "과거 범위의 audit",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_recent_diagnostic_counts_match_public_artifact(self) -> None:
        report = REPO_ROOT / (
            "data/reports/saju_1b_baseline/dashboard-prompt20/"
            "v1.0.0/build-9ab2958c83dc/aggregate.json"
        )
        aggregate = json.loads(report.read_text(encoding="utf-8"))
        models = aggregate["models"]
        requests = aggregate["requests"]
        generated = sum(value["generated"] for value in models.values())
        blocked = sum(value["pre_generation_blocks"] for value in models.values())
        summary = (ROADMAP_ROOT / "50-automatic-model-evaluation.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"{requests}요청·{generated}생성·{blocked}사전 차단", summary)
        for key, label in (
            ("k0_instruct", "K0"), ("lora_r16", "R16"), ("ki20_final", "KI20")
        ):
            value = models[key]
            self.assertIn(
                f"{label} {value['bound_diagnostic_pass']}/{value['bound_generated']}",
                summary,
            )
        self.assertIn("정확도가 아니다", summary)
        self.assertFalse(aggregate["model_quality_approval"])

    def test_cause_diagnosis_order_and_completion_are_explicit(self) -> None:
        text = (ROADMAP_ROOT / "50-automatic-model-evaluation.md").read_text(
            encoding="utf-8"
        )
        stages = re.findall(r"^## (50-[A-D])\.", text, re.MULTILINE)
        self.assertEqual(stages, ["50-A", "50-B", "50-C", "50-D"])
        for marker in (
            "실제 모델 오류, 검사기 누락·오탐",
            "개선 후보 **하나**",
            "시스템 지시문만 바꾼다",
            "동결 부모 이력",
            "의미상 같은 예제가 없다는 증명은 아니다",
            "입력 전달 오류는 앱/상태 수정으로",
            "진단 완료는 품질 승인과 다르다",
            "실행하지 못한 필수 비교는 미실행",
            "이번 재정렬은 문서와 정합성 테스트만 변경한다",
        ):
            self.assertIn(marker, text)

    def test_larger_base_comparison_is_required_and_controls_confounders(self) -> None:
        text = (ROADMAP_ROOT / "50-automatic-model-evaluation.md").read_text(
            encoding="utf-8"
        ).split("## 50-C.", 1)[1].split("## 50-D.", 1)[0]
        for marker in (
            "필수 진단",
            "K0 1.3B 기본 모델 ↔ 큰 동일 계열 Instruct 기본 모델",
            "정확한 revision", "공식 라이선스", "정밀도", "VRAM",
            "다른 계열이나 양자화 모델로 자동 대체하지 않는다",
            "서로 다른 모델의 token ID 동일성은 요구하지 않는다",
            "크기만의 순수 인과 효과를 증명한 것은 아니다",
            "등록·다운로드하지 않았다",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_data_and_training_stay_conditional(self) -> None:
        for name in ("60-mix20k-v3-1-build.md", "70-training-and-promotion.md"):
            text = (ROADMAP_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(path=name):
                self.assertIn("상태: 조건부 보류", text)
                self.assertIn("자동 진행하지 않는다", text)
                self.assertIn("50-automatic-model-evaluation.md", text)
        training = (ROADMAP_ROOT / "70-training-and-promotion.md").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Full FT diagnostic을 먼저 실행한다", training)
        self.assertIn("별도 결정", training)
        self.assertIn("not_measured", training)
        data = (ROADMAP_ROOT / "60-mix20k-v3-1-build.md").read_text(encoding="utf-8")
        self.assertIn("training_promotion_allowed=false", data)

    def test_frozen_prompt_document_is_not_changed_by_policy_cleanup(self) -> None:
        from tests.test_phase6_technical import ROOT_SUMMARY_DOCS

        self.assertEqual(ROOT_SUMMARY_DOCS, ROOT_SUMMARY_FILES)
        self.assertNotIn("SAJU_CHAT_TEST_PROMPTS.md", ROOT_SUMMARY_DOCS)
        self.assertEqual(
            hashlib.sha256((REPO_ROOT / "SAJU_CHAT_TEST_PROMPTS.md").read_bytes()).hexdigest(),
            "e28a1a9defdbaf279b24896f663b581e0a49f76f07077230d06c3060a61eec18",
        )


if __name__ == "__main__":
    unittest.main()
