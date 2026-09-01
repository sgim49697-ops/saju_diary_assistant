# test_grounded_dialogue_postscore.py - scope-aware 완료 후처리와 불변 부모 연결을 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluation.grounded_dialogue_followup.contracts import (
    prepare_context as prepare_followup_context,
)
from scripts.evaluation.grounded_dialogue_postscore import (
    _parser,
    build_postscore_aggregates,
    execute,
    false_completion_v3,
    prepare_context,
    validate_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs/evaluation/grounded_dialogue_postscore-v0.1.0.json"
FOLLOWUP_CONFIG_PATH = (
    REPO_ROOT / "configs/evaluation/grounded_dialogue_followup-v0.2.0.json"
)
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


class CompletionScopeTest(unittest.TestCase):
    def test_input_confirmation_is_not_analysis_completion(self) -> None:
        text = "출생일과 지역 정보만 확인되었습니다. 추가 요청은 없습니다."
        self.assertFalse(false_completion_v3(text, "blocked"))

    def test_domain_result_completion_remains_blocking(self) -> None:
        self.assertTrue(false_completion_v3("검증된 명식 계산이 완료되었습니다.", "partial"))
        self.assertTrue(false_completion_v3("분석 결과가 확인되었습니다.", "blocked"))
        self.assertFalse(
            false_completion_v3(
                "후보 계산은 완료됐지만 release 승인은 보류 중입니다.", "partial"
            )
        )


class PostscoreContractTest(unittest.TestCase):
    def test_contract_and_build_ids_are_stable(self) -> None:
        result = validate_contract(CONFIG, REPO_ROOT)
        first = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        second = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(first["rescore_build_id"], second["rescore_build_id"])
        self.assertEqual(
            first["context_rescore_build_id"], second["context_rescore_build_id"]
        )

    def test_parent_followup_builds_remain_reproducible(self) -> None:
        parent = prepare_followup_context(
            REPO_ROOT,
            FOLLOWUP_CONFIG_PATH,
            require_local_artifacts=True,
        )
        self.assertEqual(parent["rescore_build_id"], "eval-34d2c461b3c0")
        self.assertEqual(parent["context_build_id"], "eval-7f67d5200b31")

    def test_execute_defaults_to_dry_run(self) -> None:
        args = _parser().parse_args(["execute"])
        self.assertFalse(args.execute)

    def test_governance_forbids_state_changes(self) -> None:
        governance = CONFIG["governance"]
        self.assertFalse(governance["response_regeneration_allowed"])
        self.assertFalse(governance["runtime_configuration_change_allowed"])
        self.assertFalse(governance["sealed_blind_access"])
        self.assertFalse(governance["training_execution_allowed"])
        self.assertFalse(governance["promotion_allowed"])


@unittest.skipUnless(
    (REPO_ROOT / CONFIG["parent_context"]["private_manifest"]["path"]).is_file(),
    "완료된 비봉인 장문 진단이 로컬에 없음",
)
class ActualPostscoreTest(unittest.TestCase):
    def test_actual_rows_remove_only_scope_false_positives(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=True)
        baseline, long_context = build_postscore_aggregates(context, REPO_ROOT)
        self.assertEqual(
            baseline["arms"]["R2_K0_ORACLE_2048"]["response"][
                "false_completion_cases"
            ],
            0,
        )
        self.assertEqual(
            baseline["arms"]["R4_KI20_MODEL_NARROW_2048"]["response"][
                "false_completion_cases"
            ],
            1,
        )
        self.assertTrue(long_context["2048_target_met"])
        self.assertTrue(long_context["3584_target_met"])
        self.assertTrue(long_context["3584_strict_advantage"])
        self.assertEqual(
            long_context["capacity_recommendation"],
            "retain_3584_as_runtime_candidate_ceiling",
        )
        self.assertFalse(long_context["runtime_configuration_changed"])

    def test_public_reports_are_immutable_and_verifiable(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context["rescore_public_root"] = root / "rescore"
            context["context_public_root"] = root / "context"
            first = execute(context, REPO_ROOT)
            second = execute(context, REPO_ROOT)
            self.assertEqual(first, second)
            self.assertFalse(first["response_regenerated"])
            for report_root in (
                context["rescore_public_root"],
                context["context_public_root"],
            ):
                text = (report_root / "aggregate.json").read_text(encoding="utf-8")
                self.assertNotIn("case_id", text)
                self.assertNotIn('"output"', text)
                self.assertNotIn("runs/", text)


if __name__ == "__main__":
    unittest.main()
