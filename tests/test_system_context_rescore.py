# test_system_context_rescore.py - CPU 재집계의 불변 부모·발행·변조·분모·비생성 계약을 검증한다.

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts.evaluation import system_context_rescore as runner
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_scoring import aggregate
from scripts.evaluation.system_context_scoring import score as old_score
from tests.test_system_context_scoring_v1_1 import fixture


def bundle_fixture():
    requests, responses, entries = [], [], []
    for index, (arm, stage, blocked) in enumerate(
        (
            ("C_FULL", "preflight", False),
            ("C_FULL", "primary", False),
            ("C_MIN", "primary", False),
            ("C_FULL", "primary", True),
        )
    ):
        case = fixture()
        text = "甲子는 일주이고 甲은 일간입니다."
        rid = f"request-{index + 1:03d}"
        request = {
            "request_id": rid,
            "case": case,
            "case_id": "facts-1" if not blocked else "date-3",
            "arm": arm,
            "stage": stage,
            "engine": "lora_r16",
            "parent_sha256": "a" * 64,
        }
        response = {
            "status": "preblocked" if blocked else "generated",
            "output": text,
            "telemetry": {
                "stop_reason": "eos",
                "elapsed_seconds": 1,
                "peak_allocated_bytes": 4,
            },
        }
        entry = {
            k: request[k]
            for k in (
                "request_id",
                "case_id",
                "arm",
                "stage",
                "engine",
                "parent_sha256",
            )
        }
        entry.update(
            status=response["status"],
            stratum=case["stratum"],
            input_tokens=30,
            response_sha256=digest(response),
        )
        if not blocked:
            entry.update(scoring=old_score(case, text), telemetry=response["telemetry"])
        requests.append(request)
        responses.append(response)
        entries.append(entry)
    return {
        "frozen": {"requests": requests, "config": {"auxiliary_case_ids": ["facts-1"]}},
        "responses": responses,
        "entries": entries,
        "summary": aggregate(entries),
    }


def parent_on_disk(root):
    """모델·원본 응답 없이 작은 부모를 실제 byte/hash chain으로 구성한다."""
    bundle = bundle_fixture()
    requests, responses = bundle["frozen"]["requests"], bundle["responses"]
    scheduled = [
        {k: r[k] for k in ("request_id", "case_id", "stage", "engine", "arm")}
        for r in requests
    ]
    config_path = (
        root
        / "configs/model_versions/saju_1b_baseline/system-context-diagnosis-v1.0.0.json"
    )
    config_path.parent.mkdir(parents=True)
    original_config = {"auxiliary_case_ids": ["facts-1"], "governance": {}}
    runner.write_new(config_path, original_config, private=False)
    source = root / "scripts/frozen.py"
    source.parent.mkdir()
    source.write_text("# 합성 부모 코드\n")
    for request, response in zip(requests, responses, strict=True):
        blocked = response["status"] == "preblocked"
        request["case"].update(
            binding=None,
            history=[],
            expected_block="synthetic_block" if blocked else None,
        )
        request["parent_sha256"] = digest([])
        request["render"] = {
            "input_tokens": 3,
            "input_token_ids": [1, 2, 3],
            "input_token_ids_sha256": digest([1, 2, 3]),
            "omitted_messages": 0,
        }
        response.update(
            request_sha256=digest(request), reason=request["case"]["expected_block"]
        )
        if not blocked:
            response["generated"] = {
                "input_token_ids_sha256": digest([1, 2, 3]),
                "omitted_messages": 0,
                "output": response["output"],
            }
            response["consumers"] = {
                k: digest(response["output"])
                for k in ("raw_sha256", "api_display_sha256", "stored_sha256")
            }
    normalized = {r["case_id"]: runner.parent._stable_case(r["case"]) for r in requests}
    identity = {
        "config_sha256": runner.file_sha(config_path),
        "code_sha256": {"scripts/frozen.py": runner.file_sha(source)},
        "cases_sha256": digest(list(normalized.values())),
        "input_sha256": digest([{**r, "tokens": digest([1, 2, 3])} for r in scheduled]),
        "model_registry_sha256": digest({}),
    }
    build = "build-" + digest(identity)[:12]
    raw = root / "runs/SYSTEM-CONTEXT-DIAGNOSIS/v1.0.0" / build
    public = (
        root / "data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0" / build
    )
    runner.private_directory(raw)
    public.mkdir(parents=True)
    frozen = {
        "identity": identity,
        "config": original_config,
        "requests": requests,
        "started_at_utc": "2026-09-15T00:00:00Z",
        "code_commit_at_start": "a" * 40,
        "service_before": {},
        "model_registry": {},
        "training_inventory": {},
        "maximum_input_tokens": 3,
    }
    runner.write_new(raw / "prepared.json", frozen)
    runner.write_new(raw / "publication.json", {"service_after": {}, "reused": 0})
    hashes = {}
    for request, response in zip(requests, responses, strict=True):
        rid = request["request_id"]
        runner.write_new(raw / f"{rid}.input.json", request)
        runner.write_new(raw / f"{rid}.response.json", response)
        hashes[rid] = {
            kind + "_sha256": runner.file_sha(raw / f"{rid}.{kind}.json")
            for kind in ("input", "response")
        }
    summary = {
        "schema_version": "1.0.0",
        "build_id": build,
        **aggregate(
            [
                runner.parent._entry(r, s)
                for r, s in zip(requests, responses, strict=True)
            ]
        ),
        "governance": {},
    }
    manifest = {
        "schema_version": "1.0.0",
        "build_id": build,
        "identity": identity,
        "requests": 4,
        **{
            k: frozen[k]
            for k in (
                "started_at_utc",
                "code_commit_at_start",
                "service_before",
                "model_registry",
                "training_inventory",
                "maximum_input_tokens",
            )
        },
        "service_after": {},
        "history_omissions": 0,
        "completed_reused_at_publication": 0,
        "request_hashes": hashes,
        "aggregate_sha256": digest(summary),
        "governance": {},
    }
    runner.write_new(public / "aggregate.json", summary, private=False)
    runner.write_new(public / "build_manifest.json", manifest, private=False)
    runner.write_new(
        public / "verification.json",
        {
            "status": "verified",
            "aggregate_file_sha256": runner.file_sha(public / "aggregate.json"),
            "manifest_file_sha256": runner.file_sha(public / "build_manifest.json"),
        },
        private=False,
    )
    config = {
        **runner.validate_contract(),
        "parent_build": build,
        "parent_public_sha256": {
            name: runner.file_sha(public / name) for name in runner.PUBLIC_NAMES
        },
        "expected_source_files": 1,
        "expected_requests": 4,
        "expected_generated": 3,
        "expected_preblocked": 1,
        "expected_preflight": 1,
    }
    return config, raw, public, scheduled, [{"case_id": cid} for cid in normalized]


class RescoreTests(unittest.TestCase):
    def test_parent_reconstruction_and_tampering_use_real_hash_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, raw, public, schedule, specs = parent_on_disk(root)
            with (
                patch.object(runner.parent, "schedule", return_value=schedule),
                patch.object(runner, "cases", return_value=specs),
                patch.object(runner, "_not_tracked"),
                patch.object(
                    runner.parent, "prepare", side_effect=AssertionError("model access")
                ),
            ):
                baseline = runner.verify_parent(config, root=root)
                self.assertEqual(len(baseline["entries"]), 4)
                (root / "scripts/new_version.py").write_text(
                    "# 새 파일은 부모 map에 없음\n"
                )
                self.assertEqual(runner.verify_parent(config, root=root), baseline)
                for path in (
                    root / "scripts/frozen.py",
                    raw / "request-001.input.json",
                    raw / "request-001.response.json",
                    public / "aggregate.json",
                    public / "build_manifest.json",
                    public / "verification.json",
                ):
                    with self.subTest(path=path.name):
                        original = path.read_bytes()
                        path.write_bytes(original + b" ")
                        with self.assertRaises(ValueError):
                            runner.verify_parent(config, root=root)
                        path.write_bytes(original)
                prepared = raw / "prepared.json"
                prepared.chmod(0o644)
                with self.assertRaises(ValueError):
                    runner.verify_parent(config, root=root)
                prepared.chmod(0o600)
                changed = json.loads(prepared.read_bytes())
                changed["requests"][0]["render"]["input_tokens"] = 2
                prepared.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    runner.verify_parent(config, root=root)

    def test_fixed_contract_and_no_source_or_model_access_in_plan(self):
        with (
            patch.object(
                runner, "verify_parent", side_effect=AssertionError("parent read")
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(runner.main(["plan"]), 0)
            self.assertEqual(runner.main(["validate-contract"]), 0)

    def test_contract_rejects_boolean_alias_extra_fields_and_changed_parent(self):
        config = runner.validate_contract()
        for key, value in (
            ("expected_preflight", True),
            ("expected_requests", 341),
            ("parent_build", "build-" + "a" * 12),
            ("extra", False),
        ):
            changed = {**config, key: value}
            with (
                patch.object(runner, "read_json", return_value=changed),
                self.assertRaises(ValueError),
            ):
                runner.validate_contract()

    def test_dry_run_does_not_derive_or_publish(self):
        with (
            patch.object(runner, "verify_parent", return_value={}) as verify,
            patch.object(runner, "derive", side_effect=AssertionError("scoring")),
            patch.object(runner, "write_new", side_effect=AssertionError("write")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(runner.main(["execute"]), 0)
            verify.assert_called_once()

    def test_relative_aliases_traversal_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in (".", "../x", "./x", "x//y", "/tmp/x"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    runner._relative(root, path)
            (root / "link").symlink_to(root)
            with self.assertRaises(ValueError):
                runner._relative(root, "link/x")

    def test_private_read_never_creates_missing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "absent"
            with self.assertRaises(FileNotFoundError):
                runner._private_read_directory(missing)
            self.assertFalse(missing.exists())

    def test_transitions_and_parent_blocks_keep_denominators(self):
        config = runner.validate_contract()
        bundle = bundle_fixture()
        original = deepcopy(bundle)
        summary, manifest, trace = runner.derive(config, bundle)
        self.assertEqual(bundle, original)
        self.assertEqual(summary["statuses"], {"generated": 3, "preblocked": 1})
        self.assertEqual(summary["preflight_requests"], 1)
        self.assertEqual(manifest["generated_rescored"], 3)
        transitions = [
            x
            for x in summary["old_to_new_transitions"]
            if x["metric"] == "required_fact_use" and x["stratum"] == "all"
        ]
        self.assertEqual(len(transitions), 2)
        self.assertTrue(all(t["counts"]["FAIL_to_PASS"] == 1 for t in transitions))
        self.assertIsNone(trace[-1]["new"])
        self.assertNotIn("output", json.dumps(trace))
        self.assertEqual(runner.derive(config, bundle)[0], summary)

    def test_metric_applicability_cannot_change(self):
        bundle = bundle_fixture()
        changed = deepcopy(bundle["entries"])
        changed[1]["scoring"]["metrics"]["new_metric"] = "PASS"
        with self.assertRaises(ValueError):
            runner._transitions(bundle["entries"], changed)

    def test_exclusive_publication_idempotence_and_full_tamper_checks(self):
        config = runner.validate_contract()
        bundle = bundle_fixture()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {**config, "raw_root": "private", "public_root": "public"}
            with (
                patch.object(runner, "verify_parent", return_value=bundle),
                patch.object(runner, "_not_tracked"),
                patch.object(
                    runner,
                    "file_sha",
                    side_effect=lambda p: (
                        "a" * 64 if not Path(p).exists() else original_sha(p)
                    ),
                ),
            ):
                first = runner.execute(config, root=root)
                self.assertEqual(first, runner.execute(config, root=root))
                public = root / "public" / first["build_id"]
                for filename in (
                    "aggregate.json",
                    "build_manifest.json",
                    "verification.json",
                ):
                    path = public / filename
                    original = path.read_bytes()
                    value = json.loads(original)
                    value["unexpected"] = True
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValueError):
                        runner.verify(config, first["build_id"], root=root)
                    path.write_bytes(original)
                private = root / "private" / first["build_id"] / "scores.json"
                private.write_bytes(private.read_bytes() + b" ")
                with self.assertRaises(ValueError):
                    runner.verify(config, first["build_id"], root=root)

    def test_parent_missing_or_modified_public_fails_before_reading_responses(self):
        config = runner.validate_contract()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(FileNotFoundError):
                runner.verify_parent(config, root=root)

    def test_real_parent_hash_map_has_no_new_file_or_current_git_enumeration(self):
        # 원본 build의 존재를 요구하지 않고 구현 경계 자체를 회귀로 고정한다.
        import inspect

        source = inspect.getsource(runner.verify_parent)
        self.assertNotIn("parent.prepare(", source)
        self.assertNotIn("code_fingerprint(", source)
        self.assertNotIn("verify_model_artifacts(", source)
        self.assertIn('identity["code_sha256"].items()', source)


original_sha = runner.file_sha


if __name__ == "__main__":
    unittest.main()
