# test_grounded_dialogue_skeleton.py - 정책 전이표가 전역이고 계약이 서로 맞는지 확인한다.

from __future__ import annotations

import json
import unittest
from itertools import product
from pathlib import Path

from scripts.runtime.dialogue.policy import Decision, classify_state, decide
from scripts.runtime.dialogue.states import DialogueState, ExecutorAction

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_CONFIG = REPO_ROOT / "configs/runtime/dialogue/fsm_policy-v0.1.0.json"
EVAL_CONFIG = REPO_ROOT / "configs/evaluation/grounded_dialogue_eval-v0.1.0.json"


def _state(**slots: object) -> dict[str, object]:
    base = {"birth_date": None, "calendar": None, "birthplace": None}
    base.update(slots)
    return {"birth_slots": base, "hard_facts": None, "chart_invalidated": False}


class PolicyTotalityTest(unittest.TestCase):
    def test_decide_is_total(self) -> None:
        """모든 슬롯 조합·의도·도구 상태에서 결정이 나와야 한다."""
        values = (None, "x")
        statuses = (None, "ok", "partial", "error", "blocked")
        for date, cal, place, intent, status in product(
            values, values, values, (True, False), statuses
        ):
            with self.subTest(date=date, cal=cal, place=place, intent=intent, status=status):
                decision = decide(
                    _state(birth_date=date, calendar=cal, birthplace=place),
                    saju_intent=intent,
                    last_tool_status=status,
                )
                self.assertIsInstance(decision, Decision)
                self.assertIn(decision.action, set(ExecutorAction))

    def test_full_slots_route_to_calculator(self) -> None:
        decision = decide(
            _state(birth_date="1995-04-19", calendar="solar", birthplace="KR-SEOUL"),
            saju_intent=True,
        )
        self.assertIs(decision.action, ExecutorAction.CALL_CALCULATOR)

    def test_no_saju_intent_never_calls_calculator(self) -> None:
        decision = decide(
            _state(birth_date="1995-04-19", calendar="solar", birthplace="KR-SEOUL"),
            saju_intent=False,
        )
        self.assertIs(decision.action, ExecutorAction.MODEL_FREE_REPLY)

    def test_chart_ready_state(self) -> None:
        state = _state(birth_date="1995-04-19", calendar="solar", birthplace="KR-SEOUL")
        state["hard_facts"] = {"pillars": {}}
        self.assertIs(classify_state(state), DialogueState.CHART_READY)


class ContractConsistencyTest(unittest.TestCase):
    def test_config_vocabulary_matches_code(self) -> None:
        config = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(set(config["states"]), {item.value for item in DialogueState})
        self.assertEqual(set(config["actions"]), {item.value for item in ExecutorAction})

    def test_eval_arms_are_distinct_and_scoped(self) -> None:
        config = json.loads(EVAL_CONFIG.read_text(encoding="utf-8"))
        arm_ids = [arm["arm_id"] for arm in config["arms"]]
        self.assertEqual(len(arm_ids), len(set(arm_ids)))
        self.assertFalse(config["scope"]["modifies_ki20_artifacts"])
        self.assertFalse(config["scope"]["sealed_blind_access"])


if __name__ == "__main__":
    unittest.main()
