# test_phase5_v3_dataset.py - v3 projection과 Kanana tool XML parser 경계를 검증한다.

from __future__ import annotations

import unittest

from scripts.training.phase5_v3_dataset import (
    Phase5V3DatasetError,
    _validate_projection_row,
    parse_kanana_tool_output,
)


class Phase5V3DatasetTests(unittest.TestCase):
    def _call(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "calculate_saju_period",
                "arguments": {
                    "chart_id": "chart-fixture",
                    "period_type": "day",
                    "start_date": "2026-08-31",
                    "end_date": None,
                    "timezone": "Asia/Seoul",
                },
            },
        }

    def test_tool_xml_roundtrip_restores_typed_arguments(self) -> None:
        output = (
            "<tool_call>\n"
            "<function=calculate_saju_period>\n"
            "<parameter=chart_id>\nchart-fixture\n</parameter>\n"
            "<parameter=period_type>\nday\n</parameter>\n"
            "<parameter=start_date>\n2026-08-31\n</parameter>\n"
            "<parameter=end_date>\nnull\n</parameter>\n"
            "<parameter=timezone>\nAsia/Seoul\n</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        self.assertEqual(
            parse_kanana_tool_output(output, [self._call()]),
            [self._call()],
        )

    def test_tool_xml_rejects_suffix_text(self) -> None:
        output = (
            "<tool_call>\n"
            "<function=calculate_saju_period>\n"
            "<parameter=chart_id>\nchart-fixture\n</parameter>\n"
            "<parameter=period_type>\nday\n</parameter>\n"
            "<parameter=start_date>\n2026-08-31\n</parameter>\n"
            "<parameter=end_date>\nnull\n</parameter>\n"
            "<parameter=timezone>\nAsia/Seoul\n</parameter>\n"
            "</function>\n"
            "</tool_call>\n결과입니다."
        )
        with self.assertRaisesRegex(Phase5V3DatasetError, "suffix"):
            parse_kanana_tool_output(output, [self._call()])

    def test_projection_requires_tools_and_last_target(self) -> None:
        row = {
            "schema_version": "3.0.1",
            "id": "fixture",
            "conversation_id": "conversation",
            "task_axis": "intent_routing",
            "source": "synthetic_v3",
            "fact_authority": "NONE",
            "promotion_status": "candidate_auto_pass",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
                {"role": "assistant", "content": "answer"},
            ],
            "tools": [],
            "target_assistant_message_index": 2,
            "assistant_target_policy": "last_user_suffix",
            "train_candidate": True,
            "training_blockers": [],
            "restricted_local_only": False,
        }
        _validate_projection_row(row, 1)
        row["target_assistant_message_index"] = 1
        with self.assertRaisesRegex(Phase5V3DatasetError, "target"):
            _validate_projection_row(row, 1)


if __name__ == "__main__":
    unittest.main()
