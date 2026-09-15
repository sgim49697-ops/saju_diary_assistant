# test_saju_system_context_plan.py - 전체 경로 진단 계획의 정본 관계·실험 통제·예산·실행 경계를 검사한다.

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN = REPO_ROOT / "implementation/plans/saju_system_context_diagnosis.md"
ROADMAP = REPO_ROOT / "implementation/plans/saju_product_roadmap"


class SajuSystemContextPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = PLAN.read_text(encoding="utf-8")

    def test_plan_is_delegated_by_existing_canonical_owners(self) -> None:
        self.assertIn("saju-system-context-diagnosis-v1.0.0", self.plan)
        self.assertIn("saju-system-context-diagnosis-v1.1.0", self.plan)
        self.assertIn("saju-system-context-diagnosis-v1.2.0", self.plan)
        self.assertIn("saju-system-context-diagnosis-v1.2.1", self.plan)
        self.assertIn("보존된 부모 실행 계약", self.plan)
        self.assertIn("50이 위임한", self.plan)
        self.assertIn("별도 Phase나 release Gate를 만들지 않는다", self.plan)
        for path in (
            ROADMAP / "README.md",
            ROADMAP / "00-current-baseline.md",
            ROADMAP / "50-automatic-model-evaluation.md",
            REPO_ROOT / "README.md",
            REPO_ROOT / "03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md",
            REPO_ROOT / "implementation/plans/README.md",
        ):
            with self.subTest(path=path):
                self.assertIn(PLAN.name, path.read_text(encoding="utf-8"))

    def test_system_stages_remain_inside_the_existing_four_phases(self) -> None:
        stages = re.findall(
            r"^\| (S[0-6]) \| (50-[A-D][12]?) \|", self.plan, re.MULTILINE
        )
        self.assertEqual(
            stages,
            [
                ("S0", "50-A"), ("S1", "50-A"),
                ("S2", "50-B1"), ("S3", "50-B2"),
                ("S4", "50-C"), ("S5", "50-D"), ("S6", "50-D"),
            ],
        )
        self.assertIn("S0/S1은 `validated`", self.plan)
        self.assertIn("구현된 실행 파일", self.plan)
        self.assertIn("S3는 `executed`·공개 build `verified`", self.plan)
        self.assertIn("S4는 실행기 `implemented`·CPU 검증 완료/실제 비교 `not_executed`", self.plan)
        self.assertIn("S5~S6은 `not_executed`", self.plan)
        self.assertIn("가중치 다운로드·최대 192요청의 실제 비교는 범위 확인 전까지 보류", self.plan)
        self.assertIn("system_context_s3 verify --build build-ffd985905b51", self.plan)
        self.assertIn("system_context_diagnosis execute --execute", self.plan)

    def test_hypotheses_cover_the_whole_system_without_assuming_a_cause(self) -> None:
        hypotheses = re.findall(r"^\| (H-[A-Z]+) \|", self.plan, re.MULTILINE)
        self.assertEqual(
            hypotheses,
            [
                "H-RUNTIME", "H-STATE", "H-ROUTE", "H-CONTEXT", "H-PROMPT",
                "H-HISTORY", "H-SIZE", "H-TRAIN", "H-SERVE", "H-SCORE", "H-DEPLOY",
            ],
        )
        self.assertIn("입력이 잘리지 않았다는 사실은 컨텍스트 간섭이 없다는 증거가 아니다", self.plan)
        self.assertIn("출력 비교만으로 attention 내부 원리를 확정하지 않는다", self.plan)
        self.assertIn("가설은 동시에 성립할 수 있다", self.plan)

    def test_information_controls_preserve_required_facts_and_authority(self) -> None:
        for marker in (
            "C_FULL", "C_MIN", "C_NONE", "C_PAD", "C_POS_FRONT/MIDDLE/END",
            "실행 전 task schema로 필수 경로 고정",
            "role·권한 우선순위와 마지막 사용자 질문은 유지",
            "양성 대조", "불변 대조",
            "저장된 원국·승인 여부·보안 정책은 그대로",
            "padding이 완전히 중립적인 의미를 갖는다고 가정하지 않음",
            "동일 R16·C_FULL·동결 부모 이력",
            "시스템 지시문만 바꾼다",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.plan)

    def test_model_size_and_history_comparisons_are_not_confounded_silently(self) -> None:
        for marker in (
            "K0 1.3B 기본 모델 ↔ 큰 동일 계열 Instruct 기본 모델",
            "두 모델에 P0를 공통 적용하고 각각 C_FULL/C_MIN",
            "크기×컨텍스트 상호작용",
            "서로 다른 모델의 token ID 동일성은 요구하지 않는다",
            "다른 계열·양자화로 자동 대체하지 않는다",
            "K0 tokenizer와 고정 token 설정",
            "공식 라이선스", "정확한 revision", "VRAM/KV cache",
            "같은 정밀도가 불가능하면 미실행",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.plan)
        self.assertIn("본 원인 비교의 예산은 고정 부모 재생만 포함", self.plan)
        self.assertIn("자체 이력 연속 생성은 필요성이 확인된 후 별도 예산·승인", self.plan)
        self.assertIn("사전학습 데이터·학습량·후처리는 다를 수", self.plan)

    def test_request_budget_is_bounded_and_reconciles(self) -> None:
        table = self.plan.split("| 비교 | 최대 요청 계산 | 최대 요청 |", 1)[1]
        table = table.split("\n\n", 1)[0]
        counts = [int(value) for value in re.findall(r"\| (\d+) \|$", table, re.MULTILINE)]
        self.assertEqual(counts, [48 * 2 * 3, 12 * 4, 48 * 2, 48 * 2 * 2, 24 * 2, 8, 680])
        self.assertEqual(sum(counts[:-1]), counts[-1])
        for marker in (
            "8개 층 × 6개 = 48개", "8개 층 × 3개 = 24개",
            "요청 상한이지 GPU 생성 완료 수가 아니다",
            "신규 생성 완료 + 검증된 재사용 + 예상 사전 차단 + 예상 밖 차단/오류 + 미실행",
            "부모 생성을 추가 GPU 요청으로 숨기지 않는다",
            "680 전체 예산은 승인하지 않았다",
        ):
            self.assertIn(marker, self.plan)

    def test_scoring_and_confirmation_do_not_turn_unmeasured_into_success(self) -> None:
        for marker in (
            "PASS / FAIL / UNSCORABLE",
            "통과로 계산하지 않는다",
            "공통으로 판정 가능한 쌍의 수",
            "조건별 `UNSCORABLE` 비율",
            "not_measured", "not_executed",
            "계약 밖 평가를 완료 조건으로 추가하지 않는다",
            "동일 부모 turn을 독립 표본으로 부풀리지 않는다",
            "24개 확인용 묶음은 P1·projection·검사기를 동결한 뒤 한 번 사용",
            "그 결과로 동일 묶음에 맞춰 다시 조정하지 않는다",
            "이후 학습 자료로 재사용하지 않는다",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.plan)

    def test_privacy_immutable_artifacts_and_runtime_boundaries_are_preserved(self) -> None:
        for marker in (
            "합성 입력만 사용한다",
            "단순 hash로 공개해도 안전하다고 간주하지 않는다",
            "원시 trace·질문에 결합된 계산 내용·모델 출력·token 배열은 Git 제외",
            "소비된 sealed blind는 열거나 재사용하지 않는다",
            "승인된 부모 파일을 덮어쓰지 않는다",
            "미승인 미래 물리 시각 차단",
            "Phase 6·Runtime release·production 허용·기본 모델·feature 기본 off는 자동 변경하지 않는다",
            "400건 재개·학습", "서비스 전환·브랜치 병합은 실행하지 않는다",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.plan)


if __name__ == "__main__":
    unittest.main()
