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
    "AGENTS.md",
    "README.md",
    "implementation/plans/README.md",
    "implementation/plans/saju_1b_10k_20k_baseline/README.md",
    "implementation/plans/mix2k_v4_chart_day_lora.md",
    "implementation/plans/dashboard_v1_15_grounding.md",
    "implementation/plans/saju_system_context_diagnosis.md",
    "implementation/history/2026-09-05-model-cause-roadmap.md",
    "implementation/history/2026-09-14-default-branch-integration.md",
    "implementation/history/2026-09-15-system-context-rescore.md",
    "implementation/history/2026-09-15-dashboard-v116-intent.md",
    "implementation/history/2026-09-15-phase7-canonicalization.md",
    "implementation/history/2026-09-16-phase8-intent-s3.md",
    "implementation/history/2026-09-16-phase9-s4.md",
    "implementation/history/2026-09-16-phase9-s4-audit.md",
    "implementation/history/2026-09-16-phase9-s4-execution.md",
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


def active_roadmap_documents(root: Path = ROADMAP_ROOT) -> tuple[Path, ...]:
    """활성 하위 문서는 재귀 수집하고 원본 archive·과거 history는 제외한다."""
    return tuple(sorted(
        path for path in root.rglob("*.md")
        if not {"archive", "history"}.intersection(path.relative_to(root).parts)
    ))


class SajuProductRoadmapTests(unittest.TestCase):
    def test_index_uses_current_runtime_and_execution_order(self) -> None:
        index = (ROADMAP_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("saju-product-roadmap-v1.3.1", index)
        self.assertIn("26462137f9a4ef34adb2d3db0dd6eaff6282b309", index)
        self.assertIn("saju-runtime-release-v1.5.0-8b1d6ea2d46e", index)
        self.assertIn("dashboard v1.14 운영 / v1.15·v1.16 보존 / v1.17 오차단 후보 CPU 검증·운영 미배포", index)
        offsets = [index.index(name) for name in ORDERED_FILES]
        self.assertEqual(offsets, sorted(offsets))

    def test_local_markdown_links_resolve(self) -> None:
        paths = (
            *active_roadmap_documents(),
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
            for path in (*active_roadmap_documents(), REPO_ROOT / "AGENTS.md")
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
        for owner in ("전체 실행 순서", "현재 상태", "원인 분리 진단 상세"):
            self.assertIn(f"**{owner}**", index)

    def test_baseline_separates_remote_candidate_and_running_service(self) -> None:
        text = (ROADMAP_ROOT / "00-current-baseline.md").read_text(encoding="utf-8")
        for marker in (
            "3bb0ce2affa50e395ef21b473f1e27c5ff5fdb38",
            "26462137f9a4ef34adb2d3db0dd6eaff6282b309",
            "0e77621846c4e9894cb40d801e84d59ad57cb0de",
            "병합 완료·운영 미배포",
            "원본 프로젝트 폴더의 `master`",
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
            "A/S0·S1·B1/S2 342요청에 이어 새 검사 버전 CPU 재집계",
            "다음 별도 과제는 C/S4 모델 규모×정보 비교",
        ):
            self.assertIn(marker, text)

    def test_cpu_followup_reports_preserve_denominators_and_permissions(self) -> None:
        root = REPO_ROOT / "data/reports/saju_1b_baseline"
        rescore = json.loads((root / "system-context-rescore/v1.0.0/build-8547c487c858/aggregate.json").read_bytes())
        canary = json.loads((root / "dashboard-intent-canary/v1.0.0/build-641ac655f656/aggregate.json").read_bytes())
        self.assertEqual(rescore["requests"], 342)
        self.assertEqual(rescore["statuses"], {"generated": 312, "preblocked": 30})
        self.assertEqual(rescore["preflight_requests"], 6)
        self.assertEqual(rescore["governance"]["new_generations"], 0)
        self.assertEqual(canary["tests_passed"], 25)
        self.assertEqual(canary["matrix_allowed"] + canary["matrix_preblocked"], 18)
        self.assertTrue(canary["feature_default_off"])
        for value in (rescore, canary):
            self.assertFalse(value["governance"]["service_changed"])
            self.assertFalse(value["governance"]["production_promotion_allowed"])
            self.assertFalse(value["governance"]["training_performed"])
            self.assertFalse(value["governance"]["sealed_blind_accessed"])
            self.assertIn(value["build_id"], (ROADMAP_ROOT / "00-current-baseline.md").read_text())

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
            "10파일 수집·검증·실제 tokenizer dry-run을 완료",
            "전용 실행기·CPU 검증",
            "3B GPU 요청은 0",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_s4_registration_and_cpu_implementation_are_not_actual_comparison(self) -> None:
        phase = (ROADMAP_ROOT / "phases/phase-09.md").read_text(encoding="utf-8")
        history = (REPO_ROOT / "implementation/history/2026-09-16-phase9-s4.md").read_text(encoding="utf-8")
        registry_path = REPO_ROOT / "configs/model_versions/saju_1b_baseline/kanana-2-3b-s4-v1.0.0.json"
        config_path = REPO_ROOT / "configs/model_versions/saju_1b_baseline/system-context-s4-v1.0.0.json"
        registry = json.loads(registry_path.read_bytes())
        config = json.loads(config_path.read_bytes())
        self.assertIn(registry["revision"], phase)
        self.assertIn(str(sum(p["bytes"] for p in registry["files"].values())), phase.replace(",", ""))
        self.assertEqual(config["maximum_requests"], 192)
        self.assertEqual(config["maximum_generations"] + config["expected_preblocks"], 192)
        self.assertEqual(config["preflight_requests"], 0)
        self.assertIn("실행기 구현·CPU 검증 완료", phase)
        self.assertIn("실제 비교 미실행, GPU 생성 0", history)
        self.assertIn(config["scoring"], phase)
        for name in ("system_context_s4.py", "system_context_s4_models.py", "system_context_scoring_v1_2.py"):
            self.assertTrue((REPO_ROOT / "scripts/evaluation" / name).is_file())
        mapping = json.loads((ROADMAP_ROOT / "requirements-20260915.json").read_bytes())
        tasks = {r["id"]: r for r in mapping["entries"]}
        for task_id in ("M-0293", "O-0173"):
            self.assertEqual(tasks[task_id]["documentation_status"], "verified")
            self.assertEqual(tasks[task_id]["execution_status"], "blocked")

    def test_s4_audit_preserves_execution_and_scoring_boundaries(self) -> None:
        phase = (ROADMAP_ROOT / "phases/phase-09.md").read_text(encoding="utf-8")
        audit = (REPO_ROOT / "implementation/history/2026-09-16-phase9-s4-audit.md").read_text(encoding="utf-8")
        self.assertIn("2026-09-16-phase9-s4-audit.md", phase)
        for marker in (
            "실제 비교 미실행, GPU 생성 0", "242 = S4 192 + S6 48 + 공유 적격성 2",
            "정상 종료 증거", "96개", "264개", "기존 S3 결과를 재채점하지 않았다",
            "3B 앱 연결이 아니다", "사람 평가 Gate로 전환하지 않는다",
        ):
            self.assertIn(marker, audit)

    def test_s4_failed_execution_is_not_a_quality_result_or_budget_reset(self) -> None:
        history = (REPO_ROOT / "implementation/history/2026-09-16-phase9-s4-execution.md").read_text(encoding="utf-8")
        phase = (ROADMAP_ROOT / "phases/phase-09.md").read_text(encoding="utf-8")
        for marker in (
            "build-296dffd1ef51", "blocked", "191", "정상 응답 0",
        ):
            self.assertIn(marker, phase)
        for marker in (
            "7,028,155,860바이트", "1,692 token", "S4 K0 차단 / S4 3B 차단 / 기존 canonical 대조 통과",
            "공개 aggregate/build manifest/verification은 **미발행**",
            "241 = 680 - (438 + 1)", "실패 요청을 재생성하거나 새 build로 우회하지 않았다",
            "비교 미완료", "모델의 응답 품질·정확도·생성 속도 수치로 사용할 결과가 없다",
        ):
            self.assertIn(marker, history)

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
