# test_saju_intake_handoff_candidates.py - 결정적 intake 후보의 분포·누수·불변 build를 검증한다.

from __future__ import annotations

import copy
import json
import stat
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from scripts.data.saju_intake_handoff_candidates import (
    DEFAULT_CONFIG,
    FULL_BIRTH_DATE_PATTERN,
    GANZHI_PAIR_PATTERN,
    INTAKE_CHART_TERM_PATTERN,
    REPO_ROOT,
    SCENARIO_FAMILIES,
    STRATA,
    STRUCTURED_INTAKE_FACT_PATTERN,
    IntakeHandoffCandidateError,
    _jsonl_bytes,
    _load_json,
    _load_system_prompt,
    _parser,
    _system_message_content,
    _validate_assistant_safety,
    _validate_message_safety,
    _validate_row_shape,
    build_candidates,
    generate_candidates,
    prepare_context,
    validate_candidate_rows,
    validate_contract,
    validate_dev_suite_separation,
    verify_build,
)

CONFIG_PATH = REPO_ROOT / DEFAULT_CONFIG


class SajuIntakeHandoffCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_json(CONFIG_PATH, "intake handoff 후보 config")
        cls.rows = generate_candidates(cls.config, REPO_ROOT)
        cls.validation = validate_candidate_rows(cls.rows, cls.config, REPO_ROOT)
        cls.system_content = _system_message_content(
            cls.config, _load_system_prompt(cls.config, REPO_ROOT)
        )

    def test_committed_contract_and_final_prompt_identity_are_valid(self) -> None:
        result = validate_contract(self.config, REPO_ROOT)
        self.assertEqual(result["status"], "valid")
        self.assertEqual(
            result["system_prompt_sha256"],
            "d2aa55a54bfab253669a56570ceca63e02b8d688d3699e40c9258ac6f7c18232",
        )
        self.assertEqual(self.config["system_prompt"]["bytes"], 1805)
        self.assertEqual(
            self.config["system_prompt"]["profile_id"], "guided_diagnostic_v1"
        )

    def test_generation_is_byte_deterministic_and_balanced(self) -> None:
        again = generate_candidates(self.config, REPO_ROOT)
        self.assertEqual(_jsonl_bytes(self.rows), _jsonl_bytes(again))
        self.assertEqual(len(self.rows), 2_000)
        self.assertEqual(
            Counter(row["stratum"] for row in self.rows),
            Counter({stratum: 200 for stratum in STRATA}),
        )
        for stratum in STRATA:
            families = Counter(
                row["provenance"]["scenario_family"]
                for row in self.rows
                if row["stratum"] == stratum
            )
            self.assertEqual(
                families, Counter({family: 10 for family in SCENARIO_FAMILIES})
            )
        self.assertEqual(self.validation["normalized_duplicate_rows"], 0)
        self.assertEqual(self.validation["leakage_components"], 200)

    def test_rows_have_versioned_system_and_candidate_only_contract(self) -> None:
        prompt = _load_system_prompt(self.config, REPO_ROOT)
        for row in self.rows:
            messages = row["messages"]
            self.assertEqual(messages[0]["content"], prompt)
            self.assertNotIn("sha256=", messages[0]["content"])
            self.assertIn(len(messages), {3, 5, 7})
            self.assertEqual(messages[-1]["role"], "assistant")
            self.assertFalse(row["birth_to_pillars_training_target"])
            self.assertEqual(row["promotion_status"], "candidate_only")
            self.assertFalse(row["sharing"]["rendered_row_shared"])
            self.assertTrue(row["sharing"]["tracked_template_fragments_public"])
            self.assertFalse(row["privacy"]["contains_aihub_source_text"])
            self.assertFalse(row["privacy"]["contains_manual_session_text"])
            self.assertFalse(row["privacy"]["contains_real_person_source_text"])

    def test_intake_and_structured_transcripts_are_bidirectionally_separated(self) -> None:
        for row in self.rows:
            transcript = "\n".join(
                message["content"]
                for message in row["messages"]
                if message["role"] != "system"
            )
            if row["stratum"] == "structured_chart_ready":
                self.assertIsNone(STRUCTURED_INTAKE_FACT_PATTERN.search(transcript))
            else:
                self.assertIsNone(INTAKE_CHART_TERM_PATTERN.search(transcript))
                self.assertIsNone(GANZHI_PAIR_PATTERN.search(transcript))
            self.assertFalse(
                FULL_BIRTH_DATE_PATTERN.search(transcript)
                and GANZHI_PAIR_PATTERN.search(transcript)
            )

    def test_stateful_dev_100_has_no_exact_or_near_duplicate(self) -> None:
        result = validate_dev_suite_separation(self.rows, self.config, REPO_ROOT)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["dev_cases"], 100)
        self.assertEqual(result["normalized_exact_overlaps"], 0)
        self.assertEqual(result["near_duplicate_pairs"], 0)
        self.assertLess(result["maximum_similarity"], 0.85)
        self.assertTrue(result["candidate_component_namespace_distinct"])
        self.assertFalse(result["individual_pair_ids_or_hashes_in_report"])

    def test_leap_month_is_conditional_on_calendar_type(self) -> None:
        no_birth = next(
            row for row in self.rows if row["stratum"] == "no_birth_information"
        )
        self.assertNotIn("leap_month", no_birth["expected_action"]["request_slots"])
        self.assertNotIn("timezone", no_birth["expected_action"]["request_slots"])
        date_only = next(
            row for row in self.rows if row["stratum"] == "date_only_no_time"
        )
        self.assertEqual(
            date_only["expected_action"]["request_slots"],
            ["calendar_type", "birth_time", "birth_location"],
        )
        for row in self.rows:
            after = row["slot_state_transition"]["after"]
            assistant_text = "\n".join(
                message["content"]
                for message in row["messages"]
                if message["role"] == "assistant"
            )
            if after["calendar_type"] == "solar":
                self.assertEqual(after["leap_month"], "not_applicable")
                self.assertNotIn("윤달", assistant_text)
            elif after["calendar_type"] == "ambiguous":
                self.assertEqual(after["leap_month"], "unknown")
                self.assertIn("음력이라면 윤달", row["messages"][-1]["content"])
            elif after["calendar_type"] == "lunar":
                self.assertEqual(after["leap_month"], "unknown")
                self.assertEqual(row["expected_action"]["request_slots"], ["leap_month"])
                self.assertIn("평달인지 윤달인지", row["messages"][-1]["content"])

        solar_reask = copy.deepcopy(
            next(
                row
                for row in self.rows
                if row["stratum"] == "complete_input_runtime_handoff"
            )
        )
        solar_reask["messages"][-1]["content"] += " 윤달 여부도 알려 주세요."
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_assistant_safety(solar_reask)

    def test_safety_and_schema_mutations_are_rejected(self) -> None:
        intake = copy.deepcopy(
            next(row for row in self.rows if row["stratum"] == "date_only_no_time")
        )
        intake["messages"][1]["content"] += " 연락처는 010-1234-5678입니다."
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_message_safety(intake, self.config)

        chart_leak = copy.deepcopy(
            next(row for row in self.rows if row["stratum"] == "ambiguous_time")
        )
        chart_leak["messages"][-1]["content"] += " 원국은 갑 자입니다."
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_assistant_safety(chart_leak)

        structured_leak = copy.deepcopy(
            next(row for row in self.rows if row["stratum"] == "structured_chart_ready")
        )
        structured_leak["messages"][1]["content"] += " 생년월일도 함께 사용해 주세요."
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_assistant_safety(structured_leak)

        fabricated = copy.deepcopy(
            next(row for row in self.rows if row["stratum"] == "no_birth_information")
        )
        fabricated["messages"][-1]["content"] += (
            " 년주는 갑 자, 월주는 을 축, 일주는 병 인, 시주는 정 묘입니다."
        )
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_assistant_safety(fabricated)

        wrong_role = copy.deepcopy(self.rows[0])
        wrong_role["messages"][1]["role"] = "assistant"
        with self.assertRaises(IntakeHandoffCandidateError):
            _validate_row_shape(wrong_role, self.config, self.system_content)

    def test_build_defaults_to_dry_run_and_requires_identity_confirmation(self) -> None:
        args = _parser().parse_args(["build"])
        self.assertFalse(args.execute)
        self.assertIsNone(args.confirm_build_id)
        execute = _parser().parse_args(
            ["build", "--execute", "--confirm-build-id", "build-0123456789ab"]
        )
        self.assertTrue(execute.execute)
        self.assertEqual(execute.confirm_build_id, "build-0123456789ab")

    def test_private_build_is_immutable_and_public_report_is_aggregate_only(self) -> None:
        context = prepare_context(REPO_ROOT, CONFIG_PATH)
        derived_root = REPO_ROOT / "data/derived"
        derived_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="intake-handoff-test-", dir=derived_root
        ) as temporary:
            base = Path(temporary)
            context["private_root"] = base / "private"
            context["public_root"] = base / "public"
            built = build_candidates(context, REPO_ROOT)
            verified = verify_build(context, REPO_ROOT)
            self.assertEqual(built["mode"], "built")
            self.assertEqual(verified["total_rows"], 2_000)
            self.assertEqual(
                stat.S_IMODE((context["private_root"] / "candidates.jsonl").stat().st_mode),
                0o600,
            )
            aggregate_path = context["public_root"] / "aggregate.json"
            aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
            self.assertFalse(aggregate["rendered_candidate_rows_included"])
            self.assertTrue(aggregate["tracked_template_fragments_public"])
            self.assertFalse(aggregate["candidate_ids_included"])
            encoded = aggregate_path.read_text(encoding="utf-8")
            self.assertNotIn(self.rows[0]["candidate_id"], encoded)
            self.assertNotIn(self.rows[0]["messages"][1]["content"], encoded)

            candidate_path = context["private_root"] / "candidates.jsonl"
            candidate_path.write_bytes(candidate_path.read_bytes() + b"{}\n")
            with self.assertRaises(IntakeHandoffCandidateError):
                build_candidates(context, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
