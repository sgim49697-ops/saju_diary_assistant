# test_grounded_dialogue_eval.py - grounded dialogue 계약·추출·FSM·채점·출력 경계를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

from scripts.evaluation.grounded_dialogue.backends import GeneratedText
from scripts.evaluation.grounded_dialogue.cases import (
    apply_extractions,
    load_cases,
    oracle_extractions,
)
from scripts.evaluation.grounded_dialogue.contracts import (
    EXPECTED_ARMS,
    PRIVATE_FILE_MODE,
    PUBLIC_FILE_MODE,
    jsonl_bytes,
    load_json,
    prepare_context,
    public_leak_scan,
    validate_contract,
    write_once,
)
from scripts.evaluation.grounded_dialogue.errors import (
    ArtifactError,
    GroundedDialogueError,
)
from scripts.evaluation.grounded_dialogue.extractors import (
    ModelNarrowSlotExtractor,
    parse_model_narrow_output,
)
from scripts.evaluation.grounded_dialogue.graders import (
    fabricated_pillars,
    fact_contradictions,
    false_completion,
    provided_field_reask,
    severe_safety,
    unknown_hour_violations,
)
from scripts.evaluation.grounded_dialogue.harness import (
    ArmConfig,
    prepare_case,
    rule_harness_gate,
    run_arm,
    truncate_messages,
)
from scripts.evaluation.grounded_dialogue.reporting import (
    aggregate_arm,
    build_aggregate,
    verify,
    write_reports,
)
from scripts.evaluation.grounded_dialogue.runner import confirmation

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs/evaluation/grounded_dialogue_eval-v0.1.0.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
SOURCE_PATH = REPO_ROOT / CONFIG["source_suite"]["path"]


class FakeModel:
    def __init__(self, narrow_output: str = '{"updates":{},"explicit_unknown_fields":[]}') -> None:
        self.narrow_output = narrow_output
        self.generate_calls = 0
        self.batch_calls = 0

    def count_input_tokens(self, messages: object) -> int:
        return len(list(messages)) * 10

    def generate(self, messages: object, *, max_new_tokens: int) -> str:
        del messages, max_new_tokens
        self.generate_calls += 1
        return self.narrow_output

    def generate_many(self, message_batches: object, *, max_new_tokens: int) -> list[GeneratedText]:
        del max_new_tokens
        batches = list(message_batches)
        self.batch_calls += 1
        return [
            GeneratedText(
                text="후보 결과의 제한을 지키며 확인된 범위만 안내합니다.",
                input_tokens=self.count_input_tokens(messages),
                new_tokens=14,
                max_token_hit=False,
            )
            for messages in batches
        ]


class FakeCalculator:
    def __init__(self) -> None:
        self.calls = 0

    def calculate_chart(self, arguments: object) -> dict[str, object]:
        self.calls += 1
        values = dict(arguments)
        hour = None if values["time_precision"] == "unknown" else {"ganzhi": "丁卯"}
        return {
            "status": "partial",
            "code": "RUNTIME_RELEASE_PENDING",
            "message": "후보 계산 완료, release 승인 보류",
            "hard_facts": {
                "pillars": {
                    "year": {"ganzhi": "甲子"},
                    "month": {"ganzhi": "乙丑"},
                    "day": {"ganzhi": "丙寅"},
                    "hour": hour,
                },
                "day_master": {"stem": "丙", "element": "화"},
                "surface_five_elements": {"목": 2, "화": 2, "토": 1, "금": 1, "수": 2},
            },
            "fact_authority": "HARD_CANDIDATE",
            "limitations": ["release 승인 전 후보입니다."],
        }


class OversizeModel(FakeModel):
    def count_input_tokens(self, messages: object) -> int:
        del messages
        return 4097


class ContractTest(unittest.TestCase):
    def test_contract_and_single_variable_arms(self) -> None:
        result = validate_contract(load_json(CONFIG_PATH, "test config"), REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        actual = tuple(
            (
                item["arm_id"],
                item["model_id"],
                item["slot_extractor_id"],
                item["max_input_tokens"],
            )
            for item in CONFIG["arms"]
        )
        self.assertEqual(actual, EXPECTED_ARMS)
        self.assertEqual(CONFIG["governance"]["semantics"], "not_measured")
        self.assertFalse(CONFIG["governance"]["human_gate"])

    def test_duplicate_dialogue_fsm_was_removed(self) -> None:
        self.assertFalse(any((REPO_ROOT / "scripts/runtime/dialogue").glob("*.py")))
        self.assertFalse(
            (REPO_ROOT / "configs/runtime/dialogue/fsm_policy-v0.1.0.json").exists()
        )

    def test_prepare_context_is_stable_without_opening_private_suite(self) -> None:
        first = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        second = prepare_context(REPO_ROOT, CONFIG_PATH, require_local_artifacts=False)
        self.assertEqual(first["evaluation_build_id"], second["evaluation_build_id"])
        self.assertEqual(first["artifact_validation"]["status"], "not_checked")


@unittest.skipUnless(SOURCE_PATH.is_file(), "고정 비봉인 공개합성 suite가 로컬에 없음")
class FixedSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases(CONFIG, REPO_ROOT)

    def _case(self, stratum_index: int, item_index: int) -> dict[str, object]:
        identity = f"stateful-gate-dev-v1-{stratum_index:02d}-{item_index:02d}"
        return next(case for case in self.cases if case["case_id"] == identity)

    def test_rule_extractor_is_exact_on_all_100_cases(self) -> None:
        gate = rule_harness_gate(self.cases)
        self.assertEqual(gate["exact_state_percent"], 100.0)
        self.assertEqual(gate["time_semantics_passed"], 100)
        self.assertEqual(gate["corrections_passed"], 3)

    def test_oracle_covers_range_vague_unknown_overseas_and_correction(self) -> None:
        safe_range, _, _ = apply_extractions(oracle_extractions(self._case(2, 2)))
        vague, _, _ = apply_extractions(oracle_extractions(self._case(2, 0)))
        unknown, _, _ = apply_extractions(oracle_extractions(self._case(6, 0)))
        overseas, _, _ = apply_extractions(oracle_extractions(self._case(4, 0)))
        corrected, _, events = apply_extractions(oracle_extractions(self._case(5, 4)))
        self.assertEqual(safe_range["birth_slots"]["time_precision"], "range")
        self.assertIsNone(vague["birth_slots"]["time_precision"])
        self.assertEqual(unknown["explicit_unknown_fields"], ["birth_time"])
        self.assertIsNone(overseas["birth_slots"]["birthplace"])
        self.assertEqual(corrected["birth_slots"]["birth_date"], "1984-05-11")
        self.assertTrue(any(event["type"] == "correct_slot" for event in events))

    def test_candidate_result_is_not_inserted_into_app_fsm(self) -> None:
        case = self._case(7, 0)
        model = FakeModel()
        calculator = FakeCalculator()
        record, _ = prepare_case(
            case,
            arm=ArmConfig.from_mapping(CONFIG["arms"][3]),
            model=model,
            calculator=calculator,
            config=CONFIG,
            system_prompt="고정 prompt",
        )
        self.assertEqual(record["tool_result"]["fact_authority"], "HARD_CANDIDATE")
        self.assertEqual(record["tool_result"]["status"], "partial")
        self.assertFalse(record["fsm"]["chart_valid_after_candidate"])
        self.assertFalse(record["fsm"]["candidate_result_inserted_into_app_fsm"])

    def test_minimal_prompt_budget_failure_is_recorded_per_case(self) -> None:
        record, messages = prepare_case(
            self._case(0, 0),
            arm=ArmConfig.from_mapping(CONFIG["arms"][0]),
            model=OversizeModel(),
            calculator=FakeCalculator(),
            config=CONFIG,
            system_prompt="고정 prompt",
        )
        self.assertIsNone(messages)
        self.assertEqual(
            record["prompt_metadata"]["error_code"],
            "MINIMAL_PROMPT_OVER_BUDGET",
        )

    def test_mock_rule_arm_runs_all_cases_and_aggregates(self) -> None:
        model = FakeModel()
        calculator = FakeCalculator()
        rows = run_arm(
            ArmConfig.from_mapping(CONFIG["arms"][3]),
            self.cases,
            model=model,
            calculator=calculator,
            config=CONFIG,
            system_prompt="고정 prompt",
        )
        metrics = aggregate_arm(rows)
        self.assertEqual(len(rows), 100)
        self.assertEqual(metrics["slot_extraction"]["exact_state_percent"], 100.0)
        self.assertEqual(metrics["slot_extraction"]["invalid_cases"], 0)
        self.assertEqual(model.batch_calls, 25)
        self.assertEqual(calculator.calls, 42)

    def test_report_is_public_safe_immutable_and_restart_verifiable(self) -> None:
        model = FakeModel()
        rows = run_arm(
            ArmConfig.from_mapping(CONFIG["arms"][3]),
            self.cases,
            model=model,
            calculator=FakeCalculator(),
            config=CONFIG,
            system_prompt="고정 prompt",
        )
        rows_by_arm = {}
        for arm in CONFIG["arms"]:
            copied = deepcopy(rows)
            for row in copied:
                row["arm_id"] = arm["arm_id"]
                row["model_id"] = arm["model_id"]
                row["slot_extractor_id"] = arm["slot_extractor_id"]
            rows_by_arm[arm["arm_id"]] = copied
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {
                "config": CONFIG,
                "evaluation_build_id": "eval-aaaaaaaaaaaa",
                "build_sha256": "a" * 64,
                "build_inputs": {"implementation_hashes": {"test": "b" * 64}},
                "private_root": root / "private",
                "public_root": root / "public",
            }
            write_once(
                context["private_root"] / "run_metadata.json",
                b"{}\n",
                mode=PRIVATE_FILE_MODE,
            )
            write_once(
                context["private_root"] / "execution.lock",
                b"",
                mode=PRIVATE_FILE_MODE,
            )
            for arm_id, arm_rows in rows_by_arm.items():
                write_once(
                    context["private_root"] / "arms" / f"{arm_id}.jsonl",
                    jsonl_bytes(arm_rows),
                    mode=PRIVATE_FILE_MODE,
                )
            gate = rule_harness_gate(self.cases)
            aggregate = build_aggregate(context, rows_by_arm, gate)
            write_reports(context, aggregate, rows_by_arm)
            first = verify(context)
            write_reports(context, aggregate, rows_by_arm)
            second = verify(context)
            self.assertEqual(first, second)
            public_text = (context["public_root"] / "aggregate.json").read_text()
            self.assertNotIn("case_id", public_text)
            self.assertNotIn("runs/", public_text)


class ModelNarrowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state, _, _ = apply_extractions([])
        self.utterance = "1995년 4월 19일 양력, 서울에서 08시 15분에 태어났습니다."

    def test_valid_strict_json_is_normalized(self) -> None:
        result = parse_model_narrow_output(
            json.dumps(
                {
                    "updates": {
                        "birth_date": "1995-04-19",
                        "calendar": "solar",
                        "birth_time": "08:15",
                        "birthplace_city": "서울",
                    },
                    "explicit_unknown_fields": [],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            utterance=self.utterance,
            state=self.state,
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.updates["birthplace"]["timezone"], "Asia/Seoul")

    def test_markdown_extra_action_duplicate_and_hallucination_fail_closed(self) -> None:
        values = (
            '```json\n{"updates":{},"explicit_unknown_fields":[]}\n```',
            '{"updates":{},"explicit_unknown_fields":[],"action":"call_chart"}',
            '{"updates":{"action":"call_chart"},"explicit_unknown_fields":[]}',
            '{"updates":{"birth_date":"1990-01-01"},"explicit_unknown_fields":[]}',
            '{"updates":{},"updates":{},"explicit_unknown_fields":[]}',
        )
        for value in values:
            with self.subTest(value=value):
                result = parse_model_narrow_output(
                    value, utterance=self.utterance, state=self.state
                )
                self.assertFalse(result.valid)

    def test_invalid_output_is_not_retried(self) -> None:
        model = FakeModel("not-json")
        extractor = ModelNarrowSlotExtractor(model)
        result = extractor.extract(self.utterance, self.state)
        self.assertFalse(result.valid)
        self.assertEqual(model.generate_calls, 1)


class PromptAndGraderTest(unittest.TestCase):
    def test_truncation_drops_only_oldest_complete_pair(self) -> None:
        system = "SYSTEM-RUNTIME-CONTEXT"
        messages = [
            {"role": "user", "content": "old-user"},
            {"role": "assistant", "content": "old-assistant"},
            {"role": "user", "content": "current-user"},
        ]
        trimmed, tokens, dropped = truncate_messages(
            system,
            messages,
            max_input_tokens=200,
            token_counter=lambda value: len(value) * 100,
        )
        self.assertEqual(tokens, 200)
        self.assertEqual(dropped, 1)
        self.assertEqual(trimmed[0]["content"], system)
        self.assertEqual(trimmed[-1]["content"], "current-user")

    def test_minimal_prompt_over_budget_fails(self) -> None:
        with self.assertRaises(GroundedDialogueError):
            truncate_messages(
                "system",
                [{"role": "user", "content": "current"}],
                max_input_tokens=10,
                token_counter=lambda value: len(value) * 10,
            )

    def test_fact_graders_catch_deliberate_mutations(self) -> None:
        facts = {
            "pillars": {
                "year": {"ganzhi": "甲子"},
                "month": {"ganzhi": "乙丑"},
                "day": {"ganzhi": "丙寅"},
                "hour": None,
            },
            "day_master": {"stem": "丙", "element": "화"},
            "surface_five_elements": {"목": 2, "화": 2, "토": 1, "금": 1, "수": 2},
        }
        self.assertEqual(fabricated_pillars("시주는 정묘입니다.", facts), ["정묘"])
        self.assertEqual(unknown_hour_violations("시주는 정묘입니다.", facts), ["정묘"])
        contradictions = fact_contradictions(
            "일주는 丁卯이고 일간은 갑, 목은 7개입니다.", facts
        )
        self.assertIn("pillar:day", contradictions)
        self.assertIn("day_master:stem", contradictions)
        self.assertIn("surface_five_elements:목", contradictions)

    def test_partial_completion_requires_a_limit_qualifier(self) -> None:
        self.assertTrue(false_completion("검증된 명식 계산이 완료되었습니다.", "partial"))
        self.assertFalse(
            false_completion("후보 계산은 완료됐지만 release 승인은 보류 중입니다.", "partial")
        )

    def test_reask_and_safety(self) -> None:
        state = {
            "confirmed_fields": ["birth_date", "birthplace"],
            "explicit_unknown_fields": [],
        }
        self.assertEqual(
            provided_field_reask("생년월일과 출생지를 다시 알려 주세요.", state),
            ["birth_date", "birthplace"],
        )
        self.assertTrue(severe_safety("무조건 투자하면 확실히 수익을 냅니다."))


class OutputSafetyTest(unittest.TestCase):
    def test_write_once_is_immutable_and_preserves_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "private" / "value.json"
            public = Path(directory) / "public" / "value.json"
            write_once(private, b"{}\n", mode=PRIVATE_FILE_MODE)
            write_once(private, b"{}\n", mode=PRIVATE_FILE_MODE)
            write_once(public, b"{}\n", mode=PUBLIC_FILE_MODE)
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)
            self.assertEqual(public.stat().st_mode & 0o777, 0o644)
            with self.assertRaises(ArtifactError):
                write_once(private, b'{"changed":true}\n', mode=PRIVATE_FILE_MODE)

    def test_public_leak_scan_rejects_raw_and_private_paths(self) -> None:
        public_leak_scan({"metric": 1})
        with self.assertRaises(ArtifactError):
            public_leak_scan({"output": "raw"})
        with self.assertRaises(ArtifactError):
            public_leak_scan({"note": "runs/private/value.jsonl"})

    def test_execution_requires_explicit_confirmation(self) -> None:
        variable = CONFIG["generation"]["confirmation_variable"]
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            self.assertRaises(GroundedDialogueError),
        ):
            confirmation(CONFIG)
        with mock.patch.dict(
            os.environ,
            {variable: CONFIG["generation"]["confirmation_value"]},
            clear=True,
        ):
            confirmation(CONFIG)

    def test_config_has_no_sealed_payload_path(self) -> None:
        serialized = json.dumps(CONFIG, ensure_ascii=False)
        self.assertNotIn("blind_source_test", serialized)
        self.assertFalse(CONFIG["governance"]["sealed_blind_access"])


if __name__ == "__main__":
    unittest.main()
