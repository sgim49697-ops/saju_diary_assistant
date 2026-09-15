# test_system_context_diagnosis.py - 합성 진단의 계약·projection·검사기·보안·실행 분리를 검증한다.

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import system_context_contracts as contracts
from scripts.evaluation import system_context_diagnosis as runner
from scripts.evaluation.system_context_cases import (
    STRATA,
    cases,
    digest,
    runtime_fixtures,
)
from scripts.evaluation.system_context_projection import (
    AUTHORITY_PATHS,
    get_path,
    project,
    render,
    resolve_case,
)
from scripts.evaluation.system_context_scoring import (
    aggregate,
    claims,
    score,
    sentence_count,
)
from scripts.training import phase5_dashboard_v1_15 as dashboard


def score_case(task="fact", *, expected=None, roles=None):
    return {
        "task": task,
        "expected": expected
        or {
            "day_master": "丁",
            "natal_day": "丁亥",
            "natal_hour": None,
            "period_day": "壬辰",
            "target_date": "2026-09-15",
        },
        "expected_roles": roles or ["day_master", "natal_day"],
        "stratum": "facts",
        "apology_once": False,
    }


class ContractTests(unittest.TestCase):
    def test_fixed_contract_and_budget(self):
        config = contracts.validate_contract()
        requests = runner.schedule(config)
        self.assertEqual(len(requests), 342)
        self.assertEqual(len({r["request_id"] for r in requests}), 342)
        self.assertEqual(sum(r["stage"] == "preflight" for r in requests), 6)
        self.assertEqual(sum(r["stage"] == "primary" for r in requests), 288)
        self.assertEqual(sum(r["stage"] == "auxiliary" for r in requests), 48)
        self.assertEqual(
            {r["engine"] for r in requests if r["stage"] == "auxiliary"}, {"lora_r16"}
        )

    def test_48_new_synthetic_cases_strata_and_control_ids(self):
        suite = cases()
        self.assertEqual(len({case["family_id"] for case in suite}), 48)
        for stratum in STRATA:
            self.assertEqual(sum(c["stratum"] == stratum for c in suite), 6)
        self.assertEqual(sum(c["control"] == "C_NONE" for c in suite), 4)
        self.assertEqual(suite[0]["prompt"], suite[1]["prompt"])
        self.assertEqual(suite[0]["prompt"], suite[2]["prompt"])

    def test_contract_rejects_unknown_field_and_boolean_number_alias(self):
        original = contracts.validate_contract()
        for mutate in (
            lambda c: c.update(extra=True),
            lambda c: c.update(preflight_requests=6.0),
            lambda c: c.update(maximum_requests=680),
        ):
            changed = deepcopy(original)
            mutate(changed)
            with (
                patch.object(contracts, "read_json", return_value=changed),
                self.assertRaises(ValueError),
            ):
                contracts.validate_contract()

    def test_plan_and_contract_have_no_model_or_artifact_access(self):
        with (
            patch.object(runner, "prepare") as prepare,
            patch.object(runner, "execute") as execute,
        ):
            self.assertEqual(runner.main(["plan"]), 0)
            self.assertEqual(runner.main(["validate-contract"]), 0)
        prepare.assert_not_called()
        execute.assert_not_called()

    def test_unapproved_execution_is_rejected_before_writes(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(runner, "private_directory") as mkdir,
            self.assertRaises(ValueError),
        ):
            runner.execute({})
        mkdir.assert_not_called()

    def test_safe_paths_reject_traversal_alias_and_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            link = root / "alias"
            link.symlink_to(root, target_is_directory=True)
            for path in (Path("relative"), root / ".." / "bad", link / "child"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    contracts.safe_path(path)
        for build in (
            "../build-123456abcdef",
            "build-X",
            "build-123456abcdef/extra",
            None,
        ):
            with self.assertRaises(ValueError):
                contracts.build_path(build)

    def test_exclusive_atomic_write_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "value.json"
            contracts.write_new(path, {"x": 1})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                contracts.write_new(path, {"x": 2})
            self.assertEqual(contracts.read_json(path, private=True), {"x": 1})
            self.assertEqual([p.name for p in root.iterdir()], ["value.json"])

    def test_private_permissions_duplicate_keys_and_nonfinite_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.json"
            for text in ('{"x":1,"x":2}', '{"x":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    contracts.read_json(path)
            path.write_text("{}")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                contracts.read_json(path, private=True)

    def test_public_payload_rejects_nested_raw_fields_and_private_paths(self):
        for value in (
            {"child": [{"messages": []}]},
            {"x": "/home/user/private"},
            {"x": "sc2_" + "a" * 64},
            {"x": {"birth_date": "synthetic"}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contracts.validate_public(value)
        contracts.validate_public({"case_count": 48, "sha256": "a" * 64})

    def test_started_without_response_never_invokes_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contracts.write_new(root / "request-001.started.json", {"started": True})
            with (
                patch("subprocess.Popen") as worker,
                self.assertRaisesRegex(ValueError, "재생성"),
            ):
                runner._start_worker(root, {"request_id": "request-001"})
            worker.assert_not_called()

    def test_response_hash_and_expected_block_validation(self):
        request = {"case": {"expected_block": "DATE_BLOCK"}}
        response = {
            "request_sha256": digest(request),
            "status": "preblocked",
            "reason": "DATE_BLOCK",
        }
        runner.validate_response(request, response)
        for field, value in (
            ("request_sha256", "x"),
            ("status", "generated"),
            ("reason", "OTHER"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runner.validate_response(request, {**response, field: value})

    def test_same_build_resume_reuses_verified_blocks_without_gpu(self):
        request = {
            "request_id": "request-001",
            "stage": "primary",
            "engine": "k0_instruct",
            "arm": "C_FULL",
            "case_id": "date-1",
            "parent_sha256": "p",
            "case": {"expected_block": "DATE_BLOCK", "stratum": "date"},
            "render": {"input_tokens": 1},
        }
        prepared = {
            "build_id": "build-123456abcdef",
            "identity": {"fixed": 1},
            "requests": [request],
            "config": {"preflight_requests": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory) / "raw"
            root = raw / prepared["build_id"]
            with (
                patch.dict(os.environ, {"SYSTEM_CONTEXT_DIAGNOSIS": "S0_S1_S2_V1"}),
                patch.object(runner, "RAW_ROOT", raw),
                patch.object(runner, "build_path", return_value=root),
                patch.object(
                    runner, "service_observation", return_value={"stable": True}
                ),
                patch.object(runner, "_start_worker") as worker,
                patch.object(runner, "publish") as publish,
                patch.object(runner, "verify", return_value={"status": "verified"}),
            ):
                runner.execute(deepcopy(prepared))
                runner.execute(deepcopy(prepared), resume=True)
                self.assertEqual(publish.call_args.kwargs["reused"], 1)
                worker.assert_not_called()
                with self.assertRaises(ValueError):
                    runner.execute(deepcopy(prepared))
                changed = deepcopy(prepared)
                changed["identity"]["fixed"] = 2
                with self.assertRaises(ValueError):
                    runner.execute(changed, resume=True)
                path = root / "request-001.response.json"
                value = contracts.read_json(path, private=True)
                value["reason"] = "tampered"
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.execute(deepcopy(prepared), resume=True)

    def test_cpu_output_consumer_keeps_warning_output_unchanged(self):
        from scripts.evaluation.system_context_backend import replay_output_consumers
        from tests.test_dashboard_grounding_v2 import binding_fixture
        from tests.test_phase5_dashboard_v1_15 import context_fixture, generated_fixture

        with tempfile.TemporaryDirectory() as directory:
            context = context_fixture(Path(directory))
            case = {"prompt": "내 일간을 알려줘.", "binding": binding_fixture()}
            generated = generated_fixture(
                "일간은 갑목입니다. 틀린 응답도 그대로 보존합니다."
            )
            result = replay_output_consumers(context, case, "lora_r16", generated)
            self.assertFalse(result["changed"])
            self.assertEqual(result["raw_sha256"], result["stored_sha256"])

    def test_worker_without_parent_lock_cannot_start_model(self):
        from scripts.evaluation import system_context_backend as backend

        with (
            patch.dict(
                os.environ, {"SYSTEM_CONTEXT_DIAGNOSIS": "S0_S1_S2_V1"}, clear=True
            ),
            patch.object(backend, "header_check"),
            patch.object(dashboard, "_load_engine_model") as load,
            self.assertRaises(ValueError),
        ):
            backend.worker(
                contracts.RAW_ROOT / "build-123456abcdef/request-001.input.json",
                contracts.RAW_ROOT / "build-123456abcdef/request-001.response.json",
            )
        load.assert_not_called()

    def test_gpu_gate_requires_no_compute_process_and_12gib_free(self):
        with (
            patch.object(
                dashboard,
                "_gpu_snapshot",
                return_value={"available": True, "total_mib": 16303, "used_mib": 690},
            ),
            patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=""),
            ) as probe,
        ):
            self.assertTrue(runner.gpu_ready())
            probe.return_value.stdout = "1234\n"
            self.assertFalse(runner.gpu_ready())
        with (
            patch.object(
                dashboard,
                "_gpu_snapshot",
                return_value={"available": True, "total_mib": 16303, "used_mib": 7000},
            ),
            patch.object(
                runner.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=""),
            ),
        ):
            self.assertFalse(runner.gpu_ready())

    def test_publication_manifest_is_fully_recomputed_and_tamper_rejected(self):
        identity = {"fixed": "synthetic"}
        build = "build-" + digest(identity)[:12]
        request = {
            "request_id": "request-001",
            "stage": "primary",
            "engine": "k0_instruct",
            "arm": "C_FULL",
            "case_id": "date-1",
            "parent_sha256": "p",
            "case": {
                "expected_block": "DATE_BLOCK",
                "stratum": "date",
                "binding": None,
            },
            "render": {"input_tokens": 1},
        }
        prepared = {
            "build_id": build,
            "identity": identity,
            "config": {"governance": {"synthetic_only": True}},
            "requests": [request],
            "started_at_utc": "2026-09-15T00:00:00Z",
            "code_commit_at_start": "a" * 40,
            "service_before": {"stable": True},
            "model_registry": {},
            "training_inventory": {"rows": 2000},
            "maximum_input_tokens": 1,
            "expected_blocks": 1,
        }
        response = {
            "request_sha256": digest(request),
            "status": "preblocked",
            "reason": "DATE_BLOCK",
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw, public = base / "raw" / build, base / "public" / build
            raw.mkdir(parents=True, mode=0o700)
            for name, value in (
                ("prepared.json", prepared),
                ("request-001.input.json", request),
                ("request-001.response.json", response),
            ):
                contracts.write_new(raw / name, value)
            with (
                patch.object(
                    runner,
                    "build_path",
                    side_effect=lambda _, public=False: (
                        base / ("public" if public else "raw") / build
                    ),
                ),
                patch.object(runner, "PUBLIC_ROOT", base / "public"),
                patch.object(runner, "REPO_ROOT", base),
                patch.object(
                    runner, "service_observation", return_value={"stable": True}
                ),
                patch.object(runner, "prepare", return_value=prepared),
                patch.object(
                    runner.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, stdout=""),
                ),
            ):
                runner.publish(prepared, [runner._entry(request, response)], reused=0)
                self.assertEqual(runner.verify(build)["status"], "verified")
                manifest_path = public / "build_manifest.json"
                original = manifest_path.read_text()
                for key, value in (
                    ("training_inventory", {}),
                    ("service_before", {}),
                    ("model_registry", {"fake": 1}),
                    ("maximum_input_tokens", 4000),
                    ("history_omissions", 1),
                    ("unknown_field", True),
                ):
                    mutated = json.loads(original)
                    mutated[key] = value
                    manifest_path.write_text(json.dumps(mutated))
                    with (
                        self.subTest(key=key),
                        self.assertRaisesRegex(ValueError, "manifest 전체"),
                    ):
                        runner.verify(build)
                manifest_path.write_text(original)
                self.assertEqual(runner.verify(build)["status"], "verified")


class ScoringTests(unittest.TestCase):
    def test_role_correct_values_pass(self):
        result = score(score_case(), "일간은 丁입니다. 일주는 丁亥입니다.")
        self.assertEqual(result["metrics"]["required_fact_use"], "PASS")

    def test_value_in_wrong_role_fails_even_if_correct_value_appears(self):
        result = score(score_case(), "일간은 丁亥입니다. 일주는 丁입니다.")
        self.assertEqual(result["metrics"]["fact_contradiction_absent"], "FAIL")
        result = score(
            score_case(), "일간은 丁입니다. 일간은 甲입니다. 일주는 丁亥입니다."
        )
        self.assertEqual(result["metrics"]["required_fact_use"], "FAIL")

    def test_quoted_and_negated_wrong_premise_not_positive_claim(self):
        result = score(
            score_case("premise", roles=["day_master"]),
            '"일간은 갑목"이라는 말은 틀렸습니다. 일간은 丁입니다.',
        )
        self.assertEqual(result["metrics"]["false_premise_corrected"], "PASS")
        result = score(
            score_case("premise", roles=["day_master"]),
            "일간은 갑목이 아닙니다. 일간은 丁입니다.",
        )
        self.assertEqual(result["metrics"]["false_premise_corrected"], "PASS")

    def test_concept_example_not_user_fact(self):
        example = "예를 들면 일간은 甲이고 일주는 甲子인 경우도 있습니다."
        self.assertEqual(claims(example, score_case()["expected"]), [])
        self.assertEqual(
            score(score_case(), example)["metrics"]["required_fact_use"], "UNSCORABLE"
        )
        self.assertEqual(
            score(score_case(), "일간은 일주의 천간을 뜻합니다.")["metrics"][
                "required_fact_use"
            ],
            "FAIL",
        )

    def test_unsupported_syntax_is_not_counted_as_pass(self):
        metrics = score(score_case(), "丁 / 丁亥")["metrics"]
        self.assertEqual(metrics["required_fact_use"], "UNSCORABLE")

    def test_known_hour_cannot_be_invented_for_partial_chart(self):
        self.assertEqual(
            score(
                score_case("uncertainty"),
                "출생시간을 몰라서 시주를 확정할 수 없습니다.",
            )["metrics"]["uncertainty_preserved"],
            "PASS",
        )
        self.assertEqual(
            score(score_case("uncertainty"), "시주는 甲子입니다.")["metrics"][
                "uncertainty_preserved"
            ],
            "FAIL",
        )

    def test_general_refusal_mention_is_not_saju_interpretation(self):
        self.assertEqual(
            score(score_case("general"), "사주 얘기는 쉬어도 돼. 힘들었겠다.")[
                "metrics"
            ]["unsolicited_saju_absent"],
            "PASS",
        )
        self.assertEqual(
            score(score_case("general"), "일간은 丁이므로 그런 성향이 있어요.")[
                "metrics"
            ]["unsolicited_saju_absent"],
            "FAIL",
        )

    def test_sentence_count_lists_and_rewrite_contract(self):
        for output, expected in (
            ("하나입니다. 둘입니다.", 2),
            ("1. 하나입니다.\n2. 둘입니다.", 2),
            ("셋\n넷\n다섯", 1),
            ("한 문장.", 1),
        ):
            self.assertEqual(sentence_count(output), expected)
        case = {**score_case("two_sentences"), "apology_once": True}
        self.assertEqual(
            score(case, "죄송합니다. 표를 수정해 보내겠습니다.")["metrics"][
                "apology_once"
            ],
            "PASS",
        )
        self.assertEqual(
            score(case, "죄송합니다. 다시 죄송합니다.")["metrics"]["apology_once"],
            "FAIL",
        )

    def test_privacy_and_token_limit_fail_separately(self):
        result = score(
            score_case(), "birth_input_id: internal", stop_reason="max_tokens"
        )
        self.assertEqual(result["metrics"]["private_fields_absent"], "FAIL")
        self.assertEqual(result["metrics"]["max_token_hit_absent"], "FAIL")

    def test_aggregate_pairs_keep_unscorable_and_blocked_denominators(self):
        rows = []
        for arm, verdict in (("C_FULL", "FAIL"), ("C_MIN", "PASS")):
            rows.append(
                {
                    "stage": "primary",
                    "engine": "k0_instruct",
                    "case_id": "facts-1",
                    "arm": arm,
                    "stratum": "facts",
                    "parent_sha256": "p",
                    "status": "generated",
                    "input_tokens": 12,
                    "scoring": {
                        "metrics": {
                            "required_fact_use": verdict,
                            "fact_contradiction_absent": "UNSCORABLE",
                        }
                    },
                    "telemetry": {"elapsed_seconds": 1, "peak_allocated_bytes": 2},
                }
            )
        rows.append(
            {**rows[0], "case_id": "date-1", "status": "preblocked", "scoring": {}}
        )
        report = aggregate(rows)
        metric = next(
            m
            for m in report["paired_full_to_min"]
            if m["metric"] == "required_fact_use" and m["stratum"] == "all"
        )
        self.assertEqual(metric["improved"], 1)
        self.assertEqual(report["statuses"], {"generated": 2, "preblocked": 1})
        contracts.validate_public(report)
        rows[1]["parent_sha256"] = "different"
        with self.assertRaises(ValueError):
            aggregate(rows)


@unittest.skipUnless(
    (
        contracts.REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
    ).is_file(),
    "승인된 로컬 천체력이 필요합니다.",
)
class RuntimeProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = runtime_fixtures()
        cls.resolved = {
            case["case_id"]: resolve_case(case, cls.fixtures) for case in cases()
        }

    def test_registered_natal_and_day_labels(self):
        golden = ("丁亥", "戊子", "丁亥", "戊子", "丙寅", "壬辰")
        for index, expected in enumerate(golden):
            self.assertEqual(
                self.fixtures[f"chart-{index}"]["binding"]["value"]["chart"][
                    "hard_facts"
                ]["pillars"]["day"]["ganzhi"],
                expected,
            )
        self.assertEqual(self.resolved["date-1"]["expected"]["period_day"], "壬辰")

    def test_correction_invalidates_old_result_before_new_binding(self):
        for i in range(6):
            fixture = self.fixtures[f"corrected-{i}"]
            self.assertTrue(fixture["invalidated_before_recalculation"])
            self.assertGreater(
                fixture["binding"]["state_revision"], fixture["parent_revision"]
            )
            self.assertNotEqual(
                fixture["binding"]["snapshot_sha256"], fixture["parent_snapshot_sha256"]
            )

    def test_projection_retains_values_and_all_authority_fields(self):
        for case in self.resolved.values():
            if not case["binding"]:
                continue
            original = deepcopy(case["binding"])
            minimum = project(original["value"], case["required_paths"])
            for path in (*AUTHORITY_PATHS, *case["required_paths"]):
                self.assertEqual(
                    get_path(minimum, path), get_path(original["value"], path)
                )
            self.assertEqual(case["binding"], original)
            dashboard._runtime_model_context_from_binding(case["binding"])

    def test_general_projection_omits_natal_facts_without_unbinding(self):
        case = self.resolved["general-1"]
        minimum = project(case["binding"]["value"], case["required_paths"])
        self.assertNotIn("pillars", minimum["chart"]["hard_facts"])
        self.assertNotIn("day_master", minimum["chart"]["hard_facts"])
        self.assertIn("pillars", case["binding"]["value"]["chart"]["hard_facts"])

    def test_required_missing_path_rejected_and_partial_hour_stays_partial(self):
        with self.assertRaises(ValueError):
            project(self.fixtures["chart-0"]["binding"]["value"], ["nonexistent.value"])
        for case_id in ("uncertainty-1", "uncertainty-2"):
            case = self.resolved[case_id]
            minimum = project(case["binding"]["value"], case["required_paths"])
            self.assertEqual(minimum["chart"]["status"], "partial")
            self.assertNotIn(
                "ganzhi", minimum["chart"]["hard_facts"]["pillars"]["hour"] or {}
            )

    def test_scope_blocks_are_registered_not_model_failures(self):
        blocked = [case for case in self.resolved.values() if case["expected_block"]]
        self.assertEqual(len(blocked), 5)
        self.assertEqual(
            self.resolved["general-2"]["observed_intent"], "period_request"
        )
        self.assertIsNone(self.resolved["general-6"]["binding"])
        self.assertTrue(self.resolved["general-6"]["date_scope"]["allowed"])

    @unittest.skipUnless(
        importlib.util.find_spec("transformers"), "ML 환경 tokenizer 재구성 검사"
    )
    def test_full_renderer_byte_token_parity_and_padding_positions(self):
        from scripts.training.dashboard_tokenizer_v1 import (
            K0_RELATIVE,
            load_canonical_tokenizer,
        )

        context = contracts.prepare_context()
        tokenizer = load_canonical_tokenizer(contracts.REPO_ROOT / K0_RELATIVE)
        case = self.resolved["facts-1"]
        full = render(case, "C_FULL", context, tokenizer)
        profile = dashboard._prompt_profile(
            context, context["prompt_profiles"]["bound_profile"]
        )
        original = dashboard._messages_for_engine(
            case["history"],
            "k0_instruct",
            case["prompt"],
            profile["system_prompt_text"],
            dashboard._runtime_model_context_from_binding(case["binding"])[0],
        )
        self.assertEqual(full["messages"], original)
        self.assertEqual(
            full["input_token_ids"],
            tokenizer.apply_chat_template(
                original, tokenize=True, add_generation_prompt=True
            ),
        )
        minimum = render(case, "C_MIN", context, tokenizer)
        self.assertLess(minimum["input_tokens"], full["input_tokens"])
        fillers = set()
        for arm in ("C_PAD", "C_POS_FRONT", "C_POS_MIDDLE", "C_POS_END"):
            control = render(case, arm, context, tokenizer)
            self.assertLessEqual(
                abs(control["input_tokens"] - full["input_tokens"])
                / full["input_tokens"],
                0.01,
            )
            fillers.add(control["padding_sha256"])
            self.assertEqual(control["messages"][-1], full["messages"][-1])
        self.assertEqual(len(fillers), 1)
        invariant = render(self.resolved["facts-3"], "C_MIN", context, tokenizer)
        positive = render(self.resolved["facts-2"], "C_MIN", context, tokenizer)
        self.assertEqual(minimum["input_token_ids"], invariant["input_token_ids"])
        self.assertNotEqual(minimum["input_token_ids"], positive["input_token_ids"])


if __name__ == "__main__":
    unittest.main()
