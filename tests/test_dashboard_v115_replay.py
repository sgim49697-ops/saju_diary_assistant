# test_dashboard_v115_replay.py - 합성 진단의 재개·변조 차단·원출력 집계를 검증한다.

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_v115_replay as replay
from scripts.training import phase5_dashboard_v1_15 as dashboard
from tests.test_dashboard_grounding_v2 import binding_fixture
from tests.test_phase5_dashboard_v1_15 import context_fixture, generated_fixture


class ReplayTests(unittest.TestCase):
    def test_30_requests_27_generations_resume_without_duplicate(self):
        suite = {
            "scenarios": [
                ("today", True, ["오늘 사주", "쉬운 말로 설명", "내일과 이번 주 운세"]),
                ("natal", True, ["원국 장점"]),
                ("intake", False, ["사주 봐줘", "시간 몰라"]),
                ("unknown", False, ["시간 몰라"]),
                ("everyday", False, ["위로해줘", "문자 써줘"]),
                ("master", True, ["내 일간 확인"]),
            ]
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = context_fixture(root)
            out = root / "diagnostic"
            with (
                patch.object(replay, "header_check"),
                patch.object(replay, "_compute_processes", return_value=[]),
                patch.object(replay, "_gpu_snapshot", return_value={"free_mib": 14000}),
                patch.object(
                    dashboard, "_generation_gate", return_value={"allowed": True}
                ),
                patch.object(
                    dashboard, "_engine_availability", return_value={"available": True}
                ),
                patch.object(
                    dashboard,
                    "_generate_engine_conversation",
                    return_value=generated_fixture("일간 병화, 일주 병인, 일진 임오"),
                ) as generate,
            ):
                aggregate = replay.execute(
                    suite, binding_fixture(), {"fingerprint": "fixture"}, context, out
                )
                self.assertEqual(generate.call_count, 27)
                self.assertTrue(aggregate["execution_contract_met"])
                self.assertTrue(aggregate["first_turn_token_parity"])
                repeated = replay.execute(
                    suite, binding_fixture(), {"fingerprint": "fixture"}, context, out
                )
                self.assertEqual(repeated, aggregate)
                self.assertEqual(generate.call_count, 27)
                with self.assertRaisesRegex(ValueError, "fingerprint"):
                    replay.execute(
                        suite,
                        binding_fixture(),
                        {"fingerprint": "different"},
                        context,
                        out,
                    )
                record = out / "today.k0_instruct.1.json"
                value = json.loads(record.read_text())
                value["prompt"] = "tampered"
                record.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "SHA"):
                    replay.execute(
                        suite,
                        binding_fixture(),
                        {"fingerprint": "fixture"},
                        context,
                        out,
                    )
                self.assertEqual(generate.call_count, 27)

    def test_wrong_source_root_is_rejected_before_model_access(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            replay.prepare(Path(temp) / "sealed", Path(temp), Path(temp) / "out")
