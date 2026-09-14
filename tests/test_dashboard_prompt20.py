# test_dashboard_prompt20.py - 20문장 진단의 분기·입력 고정·재개와 중복 생성 차단을 검증한다.

import contextlib
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import dashboard_prompt20 as run
from tests.test_dashboard_grounding_v2 import binding_fixture
from tests.test_phase5_dashboard_v1_15 import context_fixture, generated_fixture


class Prompt20Tests(unittest.TestCase):
    def test_document_and_chart_only_context(self):
        prompts = run.prompts_from_document(run.ROOT / "SAJU_CHAT_TEST_PROMPTS.md")
        self.assertEqual(len(prompts), 20)
        chart = run.chart_binding(binding_fixture())
        self.assertEqual(set(chart["value"]), {"chart"})
        self.assertEqual(chart["snapshot_sha256"], run.digest(chart["value"]))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "questions.md"
            path.write_text("\n".join(f"{i}. “{p}”" for i, p in enumerate(prompts, 1)))
            self.assertEqual(run.prompts_from_document(path), prompts)
            path.write_text(path.read_text().replace("한자는", "한자도", 1))
            with self.assertRaisesRegex(ValueError, "SHA"):
                run.prompts_from_document(path)

    def run_fixture(self, root, stack):
        context = context_fixture(root)
        day = binding_fixture()
        bindings = {"day": day, "chart": run.chart_binding(day)}
        prompts = run.prompts_from_document(run.ROOT / "SAJU_CHAT_TEST_PROMPTS.md")
        stack.enter_context(patch.object(run.base, "header_check"))
        stack.enter_context(
            patch.object(run.base, "_compute_processes", return_value=[])
        )
        stack.enter_context(
            patch.object(run.base, "_gpu_snapshot", return_value={"free_mib": 15000})
        )
        stack.enter_context(
            patch.object(
                run.base.dashboard, "_generation_gate", return_value={"allowed": True}
            )
        )
        stack.enter_context(
            patch.object(
                run.base.dashboard,
                "_engine_availability",
                return_value={"available": True},
            )
        )
        generated = stack.enter_context(
            patch.object(
                run.base.dashboard,
                "_generate_engine_conversation",
                side_effect=lambda context, engine, messages: generated_fixture(
                    f"일간은 병화입니다. {engine}"
                ),
            )
        )
        return prompts, bindings, {"test": "synthetic20"}, context, generated

    def test_sixty_requests_history_forks_and_zero_generation_resume(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp)
            prompts, bindings, identity, context, generated = self.run_fixture(
                root, stack
            )
            output = root / "output"
            result = run.execute(prompts, bindings, identity, context, output)
            self.assertTrue(result["execution_contract_met"])
            self.assertEqual(generated.call_count, 54)
            self.assertEqual(os.umask(0o022), 0o022)
            for engine in run.base.ENGINES:
                for number, parent in run.PARENTS.items():
                    child = run.base.load(output / f"q{number:02}.{engine}.json")
                    ancestor = run.base.load(output / f"q{parent:02}.{engine}.json")
                    self.assertEqual(
                        child["parent_entry_sha256"], ancestor["entry_sha256"]
                    )
                    if child["status"] == "generated":
                        self.assertEqual(
                            child["response"]["session"]["messages"][:-2],
                            ancestor["response"]["session"]["messages"],
                        )
            inventory = (output / "private_manifest.json").read_bytes()
            self.assertEqual(
                run.execute(prompts, bindings, identity, context, output), result
            )
            self.assertEqual(generated.call_count, 54)
            self.assertEqual(inventory, (output / "private_manifest.json").read_bytes())
            for path in output.glob("*.json"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            changed = copy.deepcopy(identity)
            changed["test"] = "different"
            with self.assertRaisesRegex(ValueError, "덮어쓰지"):
                run.execute(prompts, bindings, changed, context, output)
            record = output / "q01.k0_instruct.json"
            value = run.base.load(record)
            value["prompt"] = "changed"
            record.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "SHA"):
                run.execute(prompts, bindings, identity, context, output)
            self.assertEqual(generated.call_count, 54)

    def test_incomplete_attempt_is_not_repeated_and_umask_restored(self):
        previous = os.umask(0o022)
        self.addCleanup(os.umask, previous)
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            root = Path(temp)
            prompts, bindings, identity, context, generated = self.run_fixture(
                root, stack
            )
            output = root / "output"
            output.mkdir()
            run.base.write_new(output / "build_manifest.json", identity)
            run.base.write_new(output / "q01.k0_instruct.started.json", {})
            with self.assertRaisesRegex(ValueError, "중복"):
                run.execute(prompts, bindings, identity, context, output)
            self.assertEqual(generated.call_count, 0)
            self.assertEqual(os.umask(0o022), 0o022)


if __name__ == "__main__":
    unittest.main()
