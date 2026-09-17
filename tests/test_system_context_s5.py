# test_system_context_s5.py - 생성 없는 데이터 대조·원본 보호·재계산·공개 경계를 검증한다.

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import system_context_s5 as s5


def fixture():
    data = {key: [] for key in ("training_rows", "specs", "candidates", "development")}
    data["teacher_state"] = {"records": {}}
    data["repair_state"] = {"provider_calls": 55, "records": {str(i): {"status": "accepted" if i < 238 else "needs_review" if i < 241 else "needs_draft", "task_axis": "intake_state_correction"} for i in range(400)}}
    for index, text in enumerate(("두 문장으로 써줘", "내 일간이 뭐야?")):
        key = "synthetic-" + str(index)
        prompt = [{"role": "system", "content": "합성 지시문"}, {"role": "user", "content": text}]
        answer = "합성 답변 하나.\n\n합성 답변 둘." if index == 0 else "합성 값"
        axis = "general_korean_empathy" if index == 0 else "hard_fact_short_qa"
        common = {"id": key, "task_axis": axis, "restricted_local_only": False}
        data["training_rows"].append({**common, "messages": [*prompt, {"role": "assistant", "content": answer}], "assistant_only_loss": True, "runtime_snapshot_sha256": None})
        data["specs"].append({**common, "prompt": prompt, "runtime_binding": None, "template_family": "family-" + str(index), "conversation_id": key, "multiturn": False, "response_contract": {"minimum_nonempty_lines": 1}})
        data["candidates"].append({**common, "prompt": prompt, "assistant": answer, "runtime_snapshot_sha256": None, "teacher": {"actual_drafter": "codex", "actual_reviewer": "codex", "review_mode": "same_provider_separate_pass"}})
        data["teacher_state"]["records"][key] = {"rewrites_used": 1, "draft_attempts": [{"deterministic_pass": False, "deterministic_error": "PRODUCTION_INSTRUCTION_LENGTH_VIOLATION: synthetic"}]}
    data["development"].append({"messages": data["specs"][0]["prompt"], "runtime_binding": None, "training_eligible": False})
    return data


def audit_row():
    return {"id": "synthetic", "truncated": False, "assistant_mask_nonzero": True, "final_eos_supervised": True, "user_system_loss_leakage_tokens": 0, "supervised_assistant_tokens": 2}


class S5AnalysisTests(unittest.TestCase):
    def test_exact_join_and_counts_do_not_mutate_inputs(self):
        data = fixture()
        before = copy.deepcopy(data)
        s5.validate_lineage(data, 2, 1)
        result, private = s5.analyze_data(data)
        self.assertEqual(data, before)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(len(private), 2)
        self.assertEqual(result["axes"]["general_korean_empathy"]["bound"], 0)
        self.assertEqual(result["final_three_or_more_nonempty_lines"], 0)
        self.assertEqual(result["lexical_last_request_markers"]["two_sentences"], 1)
        self.assertFalse(result["lexical_zero_proves_semantic_absence"])

    def test_wrong_count_duplicate_or_changed_answer_is_rejected(self):
        for change in ("count", "duplicate", "answer", "restricted", "mask", "development"):
            data = fixture()
            if change == "count":
                data["training_rows"].pop()
            elif change == "duplicate":
                data["training_rows"][1]["id"] = data["training_rows"][0]["id"]
            elif change == "answer":
                data["training_rows"][0]["messages"][-1]["content"] = "변조"
            elif change == "restricted":
                data["training_rows"][0]["restricted_local_only"] = True
            elif change == "mask":
                data["training_rows"][0]["assistant_only_loss"] = False
            else:
                data["development"][0]["training_eligible"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                s5.validate_lineage(data, 2, 1)

    def test_provider_fallback_is_actual_not_assigned(self):
        result, _ = s5.analyze_data(fixture())
        self.assertEqual(result["teacher"]["actual_drafters"], {"codex": 2})
        self.assertEqual(result["teacher"]["review_modes"], {"same_provider_separate_pass": 2})
        self.assertEqual(result["teacher"]["deterministic_failed_attempts_by_code"], {"PRODUCTION_INSTRUCTION_LENGTH_VIOLATION": 2})
        self.assertEqual(result["teacher"]["semantic_bias_causality"], "hypothesis_not_proven")

    def test_overlap_is_counted_not_hidden_as_pass(self):
        result, _ = s5.analyze_data(fixture())
        self.assertEqual(result["development_overlap"]["normalized_last_user_overlap_rows"], 1)
        self.assertEqual(result["development_overlap"]["semantic_family_overlap"], "not_measured")
        self.assertFalse(result["development_overlap"]["targets_used_for_training"])

    def test_repair_is_replacement_and_not_permission(self):
        result, _ = s5.analyze_data(fixture())
        repair = result["repair"]
        self.assertEqual(repair["status_counts"], {"accepted": 238, "needs_review": 3, "needs_draft": 159})
        self.assertEqual(repair["inherited_rows_if_finalized"] + repair["replaced_rows_if_finalized"], 2000)
        self.assertFalse(repair["applied_to_current_r16"])
        self.assertFalse(repair["automatic_resume_allowed"])
        self.assertEqual(repair["proposal"], "hold_pending_phase12")

    def test_repair_incomplete_inventory_fails(self):
        data = fixture()
        data["repair_state"]["records"].pop("0")
        with self.assertRaises(ValueError):
            s5.analyze_data(data)

    def test_public_report_contains_no_example_text_or_row_identifiers(self):
        report, _ = s5.analyze_data(fixture())
        text = json.dumps(report, ensure_ascii=False)
        for forbidden in ("합성 답변", "synthetic-0", "두 문장으로 써줘", "내 일간이 뭐야"):
            self.assertNotIn(forbidden, text)
        s5.validate_public(report)

    def test_all_seven_behavior_axes_are_required(self):
        self.assertEqual(set(s5.BEHAVIOR_AXES), {"bound_general", "topic_switch", "short_format", "false_premise", "correction_history", "fact_selection", "uncertainty"})

    def test_token_recalculation_checks_identity_and_eos_mask(self):
        row = audit_row()
        s5.check_token_rows([row], [copy.deepcopy(row)])
        changed = {**row, "supervised_assistant_tokens": 3}
        with self.assertRaises(ValueError):
            s5.check_token_rows([row], [changed])
        for key, bad in (("truncated", True), ("assistant_mask_nonzero", False), ("final_eos_supervised", False), ("user_system_loss_leakage_tokens", 1), ("supervised_assistant_tokens", 0)):
            broken = {**row, key: bad}
            with self.subTest(key=key), self.assertRaises(ValueError):
                s5.check_token_rows([broken], [broken])

    def test_json_duplicate_and_nonfinite_are_rejected(self):
        for data in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                s5.decode(data)

    def test_pinned_contract_and_external_target_cannot_be_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text("{}")
            with patch.object(s5, "CONFIG", config), self.assertRaises(ValueError):
                s5.validate_contract(Path(tmp))
        with self.assertRaises(ValueError):
            s5.validate_contract(Path("/tmp/wrong-target"))

    def test_conditional_spec_does_not_inherit_training_permission(self):
        old = json.loads((s5.REPO_ROOT / "configs/model_versions/saju_1b_baseline/mix2k-v4-lora-v1.0.1.json").read_bytes())
        spec = s5.training_proposal({"training_config": old})
        self.assertFalse(spec["execution_authorized"])
        self.assertFalse(spec["phase12_consumed_questions_reusable"])
        self.assertEqual(spec["method"], "fresh_lora_from_pinned_k0_not_r16_continuation")
        self.assertEqual((spec["rank"], spec["training"]["num_train_epochs"]), (16, 1))

    def test_review_failure_codes_are_aggregated_without_free_text(self):
        data = fixture()
        record = next(iter(data["teacher_state"]["records"].values()))
        record["review_attempts"] = [{"review": {"decision": "FAIL", "failure_codes": ["PRODUCTION_INSTRUCTION_LENGTH_VIOLATION"], "rewrite_instructions": "비공개 합성 지시문"}}]
        result, _ = s5.analyze_data(data)
        self.assertEqual(result["teacher"]["automatic_review_failure_codes"], {"PRODUCTION_INSTRUCTION_LENGTH_VIOLATION": 1})
        self.assertNotIn("비공개 합성 지시문", json.dumps(result, ensure_ascii=False))
        record["review_attempts"][0]["review"]["failure_codes"] = ["/tmp/private"]
        with self.assertRaises(ValueError):
            s5.analyze_data(data)

    def test_hash_size_and_symlink_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_bytes(b"{}")
            sha = hashlib.sha256(b"{}").hexdigest()
            self.assertEqual(s5.checked_bytes(path, sha), b"{}")
            for digest, size in (("0" * 64, 100), (sha, 1)):
                with self.assertRaises(ValueError):
                    s5.checked_bytes(path, digest, max_bytes=size)
            link = Path(tmp) / "link.json"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                s5.checked_bytes(link, sha)

    def test_unsafe_build_paths_fail(self):
        for build in (None, "../../elsewhere", "build-abc", "build-" + "a" * 13):
            with self.assertRaises(ValueError):
                s5.build_path(Path("/tmp"), build)

    def test_publication_reuse_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            aggregate = {"build_id": "build-" + "a" * 12, "governance": {"training_performed": False}}
            manifest, private = {"synthetic": True}, {"row_evidence": []}
            with patch.object(s5, "PUBLIC_ROOT", root / "public"), patch.object(s5, "PRIVATE_ROOT", root / "private"), patch.object(s5, "evaluate", return_value=(aggregate, manifest, private)):
                result = s5.publish(aggregate, manifest, private)
                self.assertEqual(s5.publish(aggregate, manifest, private), result)
                self.assertEqual(s5.verify(aggregate["build_id"], root), result)
                evidence = root / "private" / aggregate["build_id"] / "row_evidence.json"
                self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
                evidence.write_text("{}")
                with self.assertRaises(ValueError):
                    s5.verify(aggregate["build_id"], root)
                with self.assertRaises(ValueError):
                    s5.publish(aggregate, manifest, private)

    def test_unexpected_public_files_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(s5, "PUBLIC_ROOT", Path(tmp)):
            root = Path(tmp) / ("build-" + "a" * 12)
            root.mkdir()
            (root / "unexpected.txt").write_text("synthetic")
            with self.assertRaises(ValueError):
                s5.publish({"build_id": root.name}, {}, {})

    def test_cli_has_no_training_or_generation_entrypoint(self):
        for command in ("train", "generate", "resume-teacher"):
            with patch("sys.stderr"), self.assertRaises(SystemExit):
                s5.main([command, "--repair-root", "/tmp/synthetic"])


if __name__ == "__main__":
    unittest.main()
