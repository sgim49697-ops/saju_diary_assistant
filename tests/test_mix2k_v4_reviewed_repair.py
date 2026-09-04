# test_mix2k_v4_reviewed_repair.py - 외부 검토안의 선별 정본화와 비활성 serving 계약을 검증한다.

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import scripts.data.mix2k_v4_reviewed_repair as repair_module
from scripts.data.mix2k_v4_contracts import jsonl_bytes, sha256_bytes
from scripts.data.mix2k_v4_reviewed_repair import (
    AMBIGUITY_AXES,
    AMBIGUITY_COUNTS,
    AMBIGUITY_PROMPTS,
    DEFAULT_CONFIG,
    Mix2KV4RepairError,
    _ambiguity_answer_error,
    _inherited_answer_reservations,
    _json_bytes,
    _load_repair_config,
    _load_review_package_snapshot,
    _load_static_payloads,
    _load_teacher_completion,
    _mark_duplicate_repairs,
    _new_pipeline_state,
    _normalize_draft_answer_layout,
    _normalize_draft_answer_particles,
    _projection,
    _projection_evidence,
    _repair_answer_error,
    _repair_draft_prompt,
    _repair_provider_call,
    _repair_review_prompt,
    _safe_member_name,
    _select_teacher_batch,
    _source_dependency_hashes,
    _validate_parent_inputs,
    _validate_pipeline_state,
    status,
)
from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.training.phase5_dashboard_v1_14_candidate import (
    DEFAULT_CONFIG as DASHBOARD_CANDIDATE_CONFIG,
)
from scripts.training.phase5_dashboard_v1_14_candidate import validate_candidate


def _draft_state_fixture() -> tuple[list[dict], list[dict], dict, list[str]]:
    projection = {
        "next_decision": {"action": "request_slots"},
        "missing_fields": ["birthplace"],
    }
    specs = [
        {
            "id": f"m2v4_ledger_{index:03d}",
            "task_axis": "intake_state_correction",
            "drafter": "codex",
            "reviewer": "claude",
            "runtime_binding": None,
            "allowed_fact_paths": [],
            "allowed_fact_values": [],
            "response_contract": {
                "hard_max_completion_tokens": 4096,
                "minimum_nonempty_lines": 1,
                "minimum_sentences": 1,
                "natural_length_no_preferred_maximum": True,
            },
            "prompt": [
                {
                    "role": "system",
                    "content": "안내\n[앱의 구조화 입력 상태]\n"
                    + json.dumps(projection, ensure_ascii=False),
                },
                {"role": "user", "content": "이제 무엇을 알려 줘야 해?"},
            ],
        }
        for index in range(400)
    ]
    seeds = [
        {
            "record_id": spec["id"],
            "external_answer": "출생지를 입력하세요.",
            "ambiguity_resolution": None,
        }
        for spec in specs
    ]
    state = _new_pipeline_state(
        target_id="repair-ledger-test",
        identity={"test": True},
        specs=specs,
    )
    provider, kind, record_ids = _select_teacher_batch(state, ["codex"])
    assert provider == "codex"
    assert kind == "draft"
    started = "2026-09-04T00:00:00Z"
    elapsed = 1.25
    output_rows = []
    specs_by_id = {spec["id"]: spec for spec in specs}
    seeds_by_id = {seed["record_id"]: seed for seed in seeds}
    for record_id in record_ids:
        raw = {
            "record_id": record_id,
            "answer": "태어난 도시나 국가를 알려 주세요.",
            "used_fact_paths": [],
            "used_fact_values": [],
            "soft_interpretation_used": False,
            "limitations": [],
            "self_check": "PASS",
        }
        normalized, layout = _normalize_draft_answer_layout(specs_by_id[record_id], raw)
        normalized, particle = _normalize_draft_answer_particles(normalized)
        attempt = {
            "provider_call_sequence": 1,
            "provider": "codex",
            "started_at_utc": started,
            "elapsed_seconds": elapsed,
            "provider_draft": deepcopy(raw),
            "provider_draft_sha256": sha256_bytes(canonical_json_bytes(raw)),
            "normalized_draft_sha256": sha256_bytes(canonical_json_bytes(normalized)),
            "draft": deepcopy(normalized),
            "layout_normalized": layout,
            "particle_normalized": particle,
            "deterministic_pass": True,
            "validation_error": None,
        }
        record = state["records"][record_id]
        record["draft_attempts"].append(attempt)
        record["status"] = "needs_review"
        record["current_draft"] = deepcopy(normalized)
        record["current_draft_provider"] = "codex"
        output_rows.append(deepcopy(raw))
    prompt = _repair_draft_prompt(
        [specs_by_id[record_id] for record_id in record_ids],
        {record_id: "" for record_id in record_ids},
        seeds_by_id,
    )
    provider_output = {"drafts": output_rows}
    state["provider_calls"] = 1
    state["provider_call_log"].append(
        {
            "provider_call_sequence": 1,
            "provider_scope": ["codex"],
            "provider": "codex",
            "kind": "draft",
            "record_ids": list(record_ids),
            "started_at_utc": started,
            "elapsed_seconds": elapsed,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "provider_output": provider_output,
            "provider_output_sha256": sha256_bytes(
                canonical_json_bytes(provider_output)
            ),
        }
    )
    return specs, seeds, state, record_ids


def _add_pass_review(
    specs: list[dict], seeds: list[dict], state: dict, record_ids: list[str]
) -> None:
    provider, kind, selected_ids = _select_teacher_batch(state, ["claude"])
    assert provider == "claude"
    assert kind == "review"
    assert selected_ids == record_ids
    started = "2026-09-04T00:01:00Z"
    elapsed = 1.5
    specs_by_id = {spec["id"]: spec for spec in specs}
    draft_map = {
        record_id: state["records"][record_id]["current_draft"]
        for record_id in selected_ids
    }
    output_rows = []
    for record_id in selected_ids:
        review = {
            "record_id": record_id,
            "decision": "PASS",
            "failure_codes": [],
            "fact_errors": [],
            "style_notes": [],
            "rewrite_instructions": "",
        }
        attempt = {
            "provider_call_sequence": 2,
            "provider": "claude",
            "started_at_utc": started,
            "elapsed_seconds": elapsed,
            "review": deepcopy(review),
            "review_sha256": sha256_bytes(canonical_json_bytes(review)),
            "reviewed_draft_sha256": sha256_bytes(
                canonical_json_bytes(draft_map[record_id])
            ),
        }
        record = state["records"][record_id]
        record["review_attempts"].append(attempt)
        record["status"] = "accepted"
        record["accepted"] = {
            "draft_provider": "codex",
            "review_provider": "claude",
            "draft": deepcopy(record["current_draft"]),
            "review": deepcopy(review),
        }
        output_rows.append(review)
    prompt = _repair_review_prompt(
        [specs_by_id[record_id] for record_id in selected_ids], draft_map
    )
    provider_output = {"reviews": output_rows}
    state["provider_calls"] = 2
    state["provider_call_log"].append(
        {
            "provider_call_sequence": 2,
            "provider_scope": ["claude"],
            "provider": "claude",
            "kind": "review",
            "record_ids": list(selected_ids),
            "started_at_utc": started,
            "elapsed_seconds": elapsed,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "provider_output": provider_output,
            "provider_output_sha256": sha256_bytes(
                canonical_json_bytes(provider_output)
            ),
        }
    )
    _validate_pipeline_state(specs=specs, seeds=seeds, state=state)


def _synthetic_review_package(marker: str) -> tuple[bytes, dict]:
    root = "review-package"
    parent_train_sha256 = "a" * 64
    declared_upload_sha256 = "b" * 64
    review_payload = jsonl_bytes([{"marker": marker}])
    training_payload = jsonl_bytes([{"marker": f"training-{marker}"}])
    manifest_payload = _json_bytes(
        {
            "parent": {
                "canonical_v1_0_1_train_sha256": parent_train_sha256,
                "uploaded_zip_sha256": declared_upload_sha256,
            },
            "output": {
                "review_sha256": sha256_bytes(review_payload),
                "training_sha256": sha256_bytes(training_payload),
            },
        }
    )
    members = {
        repair_module.PACKAGE_MANIFEST: manifest_payload,
        repair_module.PACKAGE_REVIEW: review_payload,
        repair_module.PACKAGE_TRAIN: training_payload,
        **{
            f"supplement/filler_{index:02d}.txt": f"{marker}-{index:02d}\n".encode()
            for index in range(37)
        },
    }
    sums_payload = "".join(
        f"{sha256_bytes(payload)}  {relative}\n"
        for relative, payload in sorted(members.items())
    ).encode()
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer, "w", compression=zipfile.ZIP_STORED
    ) as archive:
        for relative, payload in members.items():
            archive.writestr(f"{root}/{relative}", payload)
        archive.writestr(f"{root}/{repair_module.PACKAGE_SUMS}", sums_payload)
    package_payload = archive_buffer.getvalue()
    config = {
        "parent": {"train_sha256": parent_train_sha256},
        "review_package": {
            "expected_filename": "package.zip",
            "bytes": len(package_payload),
            "sha256": sha256_bytes(package_payload),
            "root": root,
            "members": len(members) + 1,
            "uncompressed_bytes": sum(len(value) for value in members.values())
            + len(sums_payload),
            "declared_parent_upload_sha256": declared_upload_sha256,
            "review_rows_sha256": sha256_bytes(review_payload),
            "candidate_train_sha256": sha256_bytes(training_payload),
        },
    }
    return package_payload, config


class Mix2KV4ReviewedRepairTest(unittest.TestCase):
    def test_contract_and_selected_rewrite_inventory_are_fixed(self) -> None:
        config, parent, config_payload, prompt_texts = _load_repair_config(
            DEFAULT_CONFIG
        )

        self.assertEqual(config["artifact_revision"], "v1.1.0")
        self.assertEqual(config["repair"]["regenerated_assistant_rows"], 400)
        self.assertEqual(config["repair"]["inherited_assistant_rows"], 1600)
        self.assertFalse(config["repair"]["external_assistant_answers_are_gold"])
        self.assertFalse(config["repair"]["supplement_blueprints_included"])
        self.assertEqual(config["teacher"]["maximum_rewrite_rounds"], 4)
        self.assertEqual(parent["dataset_version"], config["dataset_version"])
        self.assertEqual(
            sha256_bytes(config_payload), sha256_bytes(DEFAULT_CONFIG.read_bytes())
        )
        self.assertEqual(set(prompt_texts), {"bound", "intake"})
        self.assertEqual(
            {key: len(value) for key, value in AMBIGUITY_PROMPTS.items()},
            AMBIGUITY_COUNTS,
        )
        self.assertEqual(
            AMBIGUITY_AXES,
            {
                "birth_date_correction": "intake_state_correction",
                "target_date_change": "intake_state_correction",
                "actual_birth_time_correction": "uncertainty_blocked_boundary",
                "hypothetical_unknown_time_policy": "uncertainty_blocked_boundary",
            },
        )
        prompts = [value for values in AMBIGUITY_PROMPTS.values() for value in values]
        self.assertEqual(len(prompts), 35)
        self.assertEqual(len(set(prompts)), 35)

    def test_repair_identity_binds_every_behavior_dependency(self) -> None:
        hashes = _source_dependency_hashes()

        self.assertEqual(
            set(hashes),
            {
                "canonical_json",
                "chart_day_adapter",
                "contracts",
                "parent_builder",
                "parent_finalizer",
                "teacher_runner",
            },
        )
        self.assertTrue(all(len(digest) == 64 for digest in hashes.values()))

    def test_static_dev_and_reports_are_hash_bound_and_symlink_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-static-") as directory:
            target = Path(directory).resolve()
            (target / "evaluation").mkdir()
            (target / "reports").mkdir()
            payloads = {
                "evaluation/dev_cases_200.jsonl": jsonl_bytes(
                    [{"id": f"dev-{index:03d}"} for index in range(200)]
                ),
                "reports/package_audit.json": _json_bytes({"passed": True}),
                "reports/lineage_summary.json": _json_bytes({"rows": 2000}),
            }
            for relative, payload in payloads.items():
                (target / relative).write_bytes(payload)
            identity = {
                "dev_sha256": sha256_bytes(payloads["evaluation/dev_cases_200.jsonl"]),
                "package_audit_sha256": sha256_bytes(
                    payloads["reports/package_audit.json"]
                ),
                "lineage_summary_sha256": sha256_bytes(
                    payloads["reports/lineage_summary.json"]
                ),
            }
            self.assertEqual(_load_static_payloads(target, identity), payloads)

            for relative, payload in payloads.items():
                path = target / relative
                with self.subTest(relative=relative):
                    path.write_bytes(payload + b" ")
                    with self.assertRaises(Mix2KV4RepairError):
                        _load_static_payloads(target, identity)
                    path.write_bytes(payload)

            dev = target / "evaluation/dev_cases_200.jsonl"
            backup = target / "evaluation/dev-backup.jsonl"
            backup.write_bytes(payloads["evaluation/dev_cases_200.jsonl"])
            dev.unlink()
            os.symlink(backup, dev)
            with self.assertRaises(Mix2KV4RepairError):
                _load_static_payloads(target, identity)

    def test_teacher_completion_uses_exact_snapshot_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-completion-") as directory:
            target = Path(directory)
            candidate_path = target / "accepted/repaired_candidates_400.jsonl"
            candidate_path.parent.mkdir()
            manifest_path = target / "teacher_manifest.json"
            expected_manifest = {"schema_version": "test", "rows": 1}
            expected_rows = [{"id": "row-1", "assistant": "답변"}]
            expected_payload = jsonl_bytes(expected_rows)
            manifest_path.write_bytes(_json_bytes(expected_manifest))
            candidate_path.write_bytes(expected_payload)
            with patch.object(
                repair_module,
                "_expected_teacher_completion",
                return_value=(expected_manifest, expected_rows, expected_payload),
            ):
                loaded = _load_teacher_completion(target, {}, [], [], {})
                self.assertEqual(loaded[0], expected_manifest)
                self.assertEqual(loaded[1], expected_rows)
                candidate_path.write_bytes(jsonl_bytes([{"id": "tampered"}]))
                with self.assertRaisesRegex(Mix2KV4RepairError, "state와 다릅니다"):
                    _load_teacher_completion(target, {}, [], [], {})
                candidate_path.unlink()
                backup = target / "candidate-backup.jsonl"
                backup.write_bytes(expected_payload)
                os.symlink(backup, candidate_path)
                with self.assertRaisesRegex(Mix2KV4RepairError, "안전하지"):
                    _load_teacher_completion(target, {}, [], [], {})

    def test_status_requires_both_valid_completion_artifacts(self) -> None:
        record_ids = [f"row-{index:03d}" for index in range(400)]
        state = {
            "provider_calls": 80,
            "selection_order": record_ids,
            "records": {record_id: {"status": "accepted"} for record_id in record_ids},
        }
        manifest = {"target_sha256": "a" * 64}
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-status-") as directory:
            target = Path(directory)
            with patch.object(
                repair_module,
                "_load_target",
                return_value=(manifest, [], [], state, {}),
            ):
                self.assertFalse(status(target)["teacher_generation_complete"])
                (target / "teacher_manifest.json").write_text("{}\n", encoding="utf-8")
                with (
                    patch.object(
                        repair_module,
                        "_load_teacher_completion",
                        side_effect=Mix2KV4RepairError("candidate가 없습니다"),
                    ),
                    self.assertRaisesRegex(Mix2KV4RepairError, "candidate가 없습니다"),
                ):
                    status(target)
                candidate_path = target / "accepted/repaired_candidates_400.jsonl"
                candidate_path.parent.mkdir()
                candidate_path.write_text("{}\n", encoding="utf-8")
                with patch.object(
                    repair_module,
                    "_load_teacher_completion",
                    return_value=({}, [], b"{}\n", b"{}\n"),
                ):
                    self.assertTrue(status(target)["teacher_generation_complete"])

    def test_parent_token_audit_is_bound_to_config_and_final_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-parent-") as directory:
            root = Path(directory).resolve()
            spec_root = root / "spec"
            final_root = root / "final"
            teacher_root = root / "teacher"
            for path in (
                spec_root / "training",
                spec_root / "evaluation",
                final_root / "training",
                final_root / "reports",
                teacher_root / "accepted",
            ):
                path.mkdir(parents=True)
            rows = [{"id": f"row-{index:04d}"} for index in range(2000)]
            dev_rows = [{"id": f"dev-{index:03d}"} for index in range(200)]
            artifact_payloads = {
                "specs": jsonl_bytes(rows),
                "dev": jsonl_bytes(dev_rows),
                "train": jsonl_bytes(rows),
                "token_audit": jsonl_bytes(
                    [
                        {"id": f"row-{index:04d}", "rendered_tokens": 100}
                        for index in range(2000)
                    ]
                ),
                "teacher_candidates": jsonl_bytes(rows),
            }
            (spec_root / "training/specs_2000.jsonl").write_bytes(
                artifact_payloads["specs"]
            )
            (spec_root / "evaluation/dev_cases_200.jsonl").write_bytes(
                artifact_payloads["dev"]
            )
            (final_root / "training/train_2000.jsonl").write_bytes(
                artifact_payloads["train"]
            )
            audit_path = final_root / "reports/token_audit_2000.jsonl"
            audit_path.write_bytes(artifact_payloads["token_audit"])
            (teacher_root / "accepted/training_candidates_2000.jsonl").write_bytes(
                artifact_payloads["teacher_candidates"]
            )
            artifact_hashes = {
                key: sha256_bytes(payload) for key, payload in artifact_payloads.items()
            }
            spec_manifest = {
                "build_id": "spec-build",
                "build_sha256": "1" * 64,
            }
            final_manifest = {
                "build_id": "final-build",
                "build_sha256": "2" * 64,
                "artifact_sha256": {
                    "training/train_2000.jsonl": artifact_hashes["train"],
                    "reports/token_audit_2000.jsonl": artifact_hashes["token_audit"],
                },
            }
            teacher_manifest = {
                "candidate_sha256": artifact_hashes["teacher_candidates"]
            }
            manifests = {
                "spec_manifest": _json_bytes(spec_manifest),
                "final_manifest": _json_bytes(final_manifest),
                "teacher_manifest": _json_bytes(teacher_manifest),
            }
            (spec_root / "build_manifest.json").write_bytes(manifests["spec_manifest"])
            (final_root / "build_manifest.json").write_bytes(
                manifests["final_manifest"]
            )
            (teacher_root / "teacher_manifest.json").write_bytes(
                manifests["teacher_manifest"]
            )
            config = {
                "parent": {
                    "spec_build_id": "spec-build",
                    "spec_build_sha256": "1" * 64,
                    "spec_manifest_sha256": sha256_bytes(manifests["spec_manifest"]),
                    "specs_sha256": artifact_hashes["specs"],
                    "dev_sha256": artifact_hashes["dev"],
                    "final_build_id": "final-build",
                    "final_build_sha256": "2" * 64,
                    "final_manifest_sha256": sha256_bytes(manifests["final_manifest"]),
                    "train_sha256": artifact_hashes["train"],
                    "token_audit_sha256": artifact_hashes["token_audit"],
                    "teacher_manifest_sha256": sha256_bytes(
                        manifests["teacher_manifest"]
                    ),
                    "teacher_candidates_sha256": artifact_hashes["teacher_candidates"],
                }
            }
            result = _validate_parent_inputs(
                config=config,
                parent_spec_build=spec_root,
                parent_final_build=final_root,
                parent_teacher_build=teacher_root,
            )
            self.assertEqual(len(result[3]), 2000)
            tampered = bytearray(artifact_payloads["token_audit"])
            index = tampered.index(b"100")
            tampered[index : index + 3] = b"101"
            audit_path.write_bytes(tampered)
            with self.assertRaisesRegex(Mix2KV4RepairError, "token_audit"):
                _validate_parent_inputs(
                    config=config,
                    parent_spec_build=spec_root,
                    parent_final_build=final_root,
                    parent_teacher_build=teacher_root,
                )

    def test_zip_member_path_rejects_traversal_and_foreign_root(self) -> None:
        root = "review-package"

        self.assertTrue(_safe_member_name("review-package/training/train.jsonl", root))
        self.assertFalse(_safe_member_name("review-package/../secret", root))
        self.assertFalse(_safe_member_name("other/train.jsonl", root))
        self.assertFalse(_safe_member_name("/review-package/train.jsonl", root))
        self.assertFalse(_safe_member_name("review-package\\train.jsonl", root))

    def test_review_package_audit_and_rows_share_one_snapshot(self) -> None:
        package_a, config = _synthetic_review_package("A")
        package_b, _other_config = _synthetic_review_package("B")
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-package-") as directory:
            package_path = Path(directory) / "package.zip"
            package_path.write_bytes(package_a)
            original_loader = repair_module._load_bytes_snapshot

            def snapshot_then_swap(path: Path, label: str, **kwargs: object) -> bytes:
                payload = original_loader(path, label, **kwargs)
                if path == package_path:
                    path.write_bytes(package_b)
                return payload

            with patch.object(
                repair_module,
                "_load_bytes_snapshot",
                side_effect=snapshot_then_swap,
            ) as loader:
                report, review, training = _load_review_package_snapshot(
                    package_path, config
                )

            self.assertEqual(loader.call_count, 1)
            self.assertEqual(report["package_sha256"], sha256_bytes(package_a))
            self.assertEqual(review, [{"marker": "A"}])
            self.assertEqual(training, [{"marker": "training-A"}])
            with self.assertRaisesRegex(Mix2KV4RepairError, "identity"):
                _load_review_package_snapshot(package_path, config)

    def test_config_and_prompt_text_use_the_same_snapshots(self) -> None:
        source_config = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="mix2k-v11-prompt-") as directory:
            root = Path(directory)
            config_path = root / "repair.json"
            parent_path = root / "parent.json"
            bound_path = root / "bound.txt"
            intake_path = root / "intake.txt"
            parent_payload = b'{"marker":"PARENT-A"}\n'
            parent_b = b'{"marker":"PARENT-B"}\n'
            bound_payload = b"BOUND-A\n"
            intake_a = b"INTAKE-A\n"
            intake_b = b"INTAKE-B\n"
            parent_path.write_bytes(parent_payload)
            bound_path.write_bytes(bound_payload)
            intake_path.write_bytes(intake_a)
            source_config["parent"]["config_path"] = "parent.json"
            source_config["parent"]["config_sha256"] = sha256_bytes(parent_payload)
            source_config["prompts"].update(
                {
                    "bound_path": "bound.txt",
                    "bound_sha256": sha256_bytes(bound_payload),
                    "intake_path": "intake.txt",
                    "intake_sha256": sha256_bytes(intake_a),
                }
            )
            config_payload = json.dumps(source_config, ensure_ascii=False).encode()
            config_path.write_bytes(config_payload)
            original_loader = repair_module._load_bytes_snapshot

            def snapshot_then_swap(path: Path, label: str, **kwargs: object) -> bytes:
                payload = original_loader(path, label, **kwargs)
                if path == parent_path:
                    path.write_bytes(parent_b)
                elif path == intake_path:
                    path.write_bytes(intake_b)
                return payload

            def parse_parent_snapshot(path: Path) -> dict:
                return json.loads(path.read_text(encoding="utf-8"))

            with (
                patch.object(repair_module, "REPO_ROOT", root),
                patch.object(
                    repair_module,
                    "_load_parent_config",
                    side_effect=parse_parent_snapshot,
                ),
                patch.object(
                    repair_module,
                    "_load_bytes_snapshot",
                    side_effect=snapshot_then_swap,
                ),
            ):
                _config, parent, loaded_payload, prompts = _load_repair_config(
                    config_path
                )

            self.assertEqual(loaded_payload, config_payload)
            self.assertEqual(parent, {"marker": "PARENT-A"})
            self.assertEqual(prompts["bound"], "BOUND-A")
            self.assertEqual(prompts["intake"], "INTAKE-A")
            parent_path.write_bytes(parent_payload)
            with (
                patch.object(repair_module, "REPO_ROOT", root),
                patch.object(
                    repair_module,
                    "_load_parent_config",
                    side_effect=parse_parent_snapshot,
                ),
                self.assertRaisesRegex(Mix2KV4RepairError, "intake prompt SHA-256"),
            ):
                _load_repair_config(config_path)

    def test_birth_date_and_target_date_corrections_invalidate_different_results(
        self,
    ) -> None:
        original = "날짜를 바꾸고 싶으면 기존 결과를 어떻게 처리해야 해?"
        birth = _projection(original, "birth_date_correction", 2)
        target = _projection(original, "target_date_change", 2)

        self.assertEqual(birth["next_decision"]["action"], "request_chart")
        self.assertEqual(birth["chart_status"], "invalidated")
        self.assertEqual(birth["period_status"], "invalidated")
        self.assertEqual(target["next_decision"]["action"], "request_period")
        self.assertEqual(target["chart_status"], "valid")
        self.assertEqual(target["period_status"], "invalidated")

    def test_unknown_time_projection_requests_only_remaining_required_slots(
        self,
    ) -> None:
        projection = _projection(
            "생년월일은 말했는데 출생시간은 몰라. 지금 무엇을 확인해야 해?",
            None,
            0,
        )

        self.assertEqual(projection["explicit_unknown_fields"], ["birth_time"])
        self.assertEqual(projection["missing_fields"], ["calendar", "birthplace"])
        self.assertEqual(projection["next_decision"]["action"], "request_slots")

    def test_projection_evidence_is_namespaced_and_complete(self) -> None:
        projection = _projection(
            "생년월일은 말했는데 출생시간은 몰라. 지금 무엇을 확인해야 해?",
            None,
            0,
        )
        paths, values = _projection_evidence(projection)

        self.assertEqual(len(paths), len(values))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn("intake_state.birth_slots.birth_date", paths)
        self.assertIn("intake_state.next_decision.action", paths)
        self.assertIn("1988-07-14", values)
        self.assertIn("request_slots", values)

    def test_repair_answer_validator_blocks_false_completion_and_internal_language(
        self,
    ) -> None:
        projection = {
            "next_decision": {"action": "request_slots"},
            "missing_fields": ["birthplace"],
        }
        spec = {
            "task_axis": "intake_state_correction",
            "prompt": [
                {
                    "role": "system",
                    "content": "안내\n[앱의 구조화 입력 상태]\n"
                    + json.dumps(projection, ensure_ascii=False),
                }
            ],
        }

        self.assertEqual(
            _repair_answer_error(spec, "출생지 수정을 완료했어요."),
            "false_state_completion",
        )
        self.assertEqual(
            _repair_answer_error(spec, "다음으로 현재 runtime을 확인해요."),
            "internal_or_false_authority_language",
        )
        self.assertIsNone(
            _repair_answer_error(spec, "계속하려면 태어난 도시나 국가를 알려 주세요.")
        )

        blocked_spec = {
            "task_axis": "intake_state_correction",
            "prompt": [
                {
                    "role": "system",
                    "content": "안내\n[앱의 구조화 입력 상태]\n"
                    + json.dumps(
                        {
                            "next_decision": {"action": "explain_blocked"},
                            "missing_fields": [],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }
        self.assertIsNone(
            _repair_answer_error(
                blocked_spec,
                "현재 계산이 완료된 것이 아니라 차단된 상태입니다.",
            )
        )

    def test_unknown_time_correction_and_hypothesis_have_different_semantics(
        self,
    ) -> None:
        correction = (
            "실제 출생시간은 시간 미상으로 정정해야 합니다.\n"
            "기존 시주는 확정값으로 사용하지 않고 해석 근거에서 제외합니다.\n"
            "원국은 시간 미상 조건으로 다시 계산한 뒤 확인된 범위만 설명해야 합니다."
        )
        hypothesis = (
            "현재 원국은 그대로 유지하고 일반적인 가정의 원칙만 설명하겠습니다.\n"
            "시간을 모를 때는 시주를 임의로 고르지 않습니다.\n"
            "계산기가 반환한 공통 사실과 후보 차이가 있을 때만 그 범위를 구분합니다."
        )

        self.assertIsNone(
            _ambiguity_answer_error("actual_birth_time_correction", correction)
        )
        self.assertIsNone(
            _ambiguity_answer_error("hypothetical_unknown_time_policy", hypothesis)
        )
        self.assertIsNone(
            _ambiguity_answer_error(
                "hypothetical_unknown_time_policy",
                "현재 원국은 그대로 유지하는 일반적인 가정입니다. "
                "임의의 대표 시각이나 시주를 대신 넣지도 않습니다.",
            )
        )
        self.assertEqual(
            _ambiguity_answer_error("actual_birth_time_correction", hypothesis),
            "actual_unknown_time_not_acknowledged",
        )
        self.assertEqual(
            _ambiguity_answer_error("hypothetical_unknown_time_policy", correction),
            "hypothetical_policy_scope_missing",
        )

    def test_pipeline_state_rejects_accepted_without_attempt_chain(self) -> None:
        specs = [
            {
                "id": f"m2v4_state_{index:03d}",
                "task_axis": "intake_state_correction",
                "drafter": "claude" if index % 2 == 0 else "codex",
                "reviewer": "codex" if index % 2 == 0 else "claude",
            }
            for index in range(400)
        ]
        seeds = [
            {"record_id": spec["id"], "external_answer": "외부 제안"} for spec in specs
        ]
        state = _new_pipeline_state(
            target_id="repair-test",
            identity={"test": True},
            specs=specs,
        )
        report = _validate_pipeline_state(specs=specs, seeds=seeds, state=state)
        self.assertTrue(report["attempt_linkage_valid"])

        record = state["records"][specs[0]["id"]]
        fake_draft = {"answer": "수동 답변"}
        record["status"] = "accepted"
        record["current_draft"] = fake_draft
        record["current_draft_provider"] = "claude"
        record["accepted"] = {
            "draft_provider": "claude",
            "review_provider": "codex",
            "draft": fake_draft,
            "review": {"decision": "PASS"},
        }
        with self.assertRaisesRegex(Mix2KV4RepairError, "최신 PASS attempt"):
            _validate_pipeline_state(specs=specs, seeds=seeds, state=state)

    def test_pipeline_state_rejects_raw_draft_unrelated_to_normalized_draft(
        self,
    ) -> None:
        specs, seeds, state, record_ids = _draft_state_fixture()
        _validate_pipeline_state(specs=specs, seeds=seeds, state=state)

        record_id = record_ids[0]
        attempt = state["records"][record_id]["draft_attempts"][0]
        attempt["provider_draft"]["answer"] = "출생지를 먼저 확인해 주세요."
        attempt["provider_draft_sha256"] = sha256_bytes(
            canonical_json_bytes(attempt["provider_draft"])
        )
        provider_output = state["provider_call_log"][0]["provider_output"]
        provider_output["drafts"][0] = deepcopy(attempt["provider_draft"])
        state["provider_call_log"][0]["provider_output_sha256"] = sha256_bytes(
            canonical_json_bytes(provider_output)
        )

        with self.assertRaisesRegex(Mix2KV4RepairError, "draft attempt가 손상"):
            _validate_pipeline_state(specs=specs, seeds=seeds, state=state)

    def test_pipeline_state_rejects_stale_review_after_draft_replacement(self) -> None:
        specs, seeds, state, record_ids = _draft_state_fixture()
        _add_pass_review(specs, seeds, state, record_ids)

        record_id = record_ids[0]
        replacement = deepcopy(state["records"][record_id]["current_draft"])
        replacement["answer"] = "계속하려면 태어난 도시나 국가를 알려 주세요."
        attempt = state["records"][record_id]["draft_attempts"][0]
        attempt["provider_draft"] = deepcopy(replacement)
        attempt["provider_draft_sha256"] = sha256_bytes(
            canonical_json_bytes(replacement)
        )
        attempt["draft"] = deepcopy(replacement)
        attempt["normalized_draft_sha256"] = sha256_bytes(
            canonical_json_bytes(replacement)
        )
        state["records"][record_id]["current_draft"] = deepcopy(replacement)
        state["records"][record_id]["accepted"]["draft"] = deepcopy(replacement)
        draft_call = state["provider_call_log"][0]
        draft_call["provider_output"]["drafts"][0] = deepcopy(replacement)
        draft_call["provider_output_sha256"] = sha256_bytes(
            canonical_json_bytes(draft_call["provider_output"])
        )
        specs_by_id = {spec["id"]: spec for spec in specs}
        review_drafts = {
            selected_id: state["records"][selected_id]["current_draft"]
            for selected_id in record_ids
        }
        review_prompt = _repair_review_prompt(
            [specs_by_id[selected_id] for selected_id in record_ids], review_drafts
        )
        state["provider_call_log"][1]["prompt_sha256"] = sha256_bytes(
            review_prompt.encode("utf-8")
        )

        with self.assertRaisesRegex(Mix2KV4RepairError, "review가 직전"):
            _validate_pipeline_state(specs=specs, seeds=seeds, state=state)

    def test_pipeline_state_rejects_call_that_skips_scheduler_batch(self) -> None:
        specs, seeds, state, record_ids = _draft_state_fixture()
        keep_id = record_ids[0]
        for record_id in record_ids[1:]:
            record = state["records"][record_id]
            record["status"] = "needs_draft"
            record["current_draft"] = None
            record["current_draft_provider"] = None
            record["draft_attempts"] = []
        call = state["provider_call_log"][0]
        call["record_ids"] = [keep_id]
        call["provider_output"]["drafts"] = call["provider_output"]["drafts"][:1]
        call["provider_output_sha256"] = sha256_bytes(
            canonical_json_bytes(call["provider_output"])
        )
        specs_by_id = {spec["id"]: spec for spec in specs}
        seeds_by_id = {seed["record_id"]: seed for seed in seeds}
        prompt = _repair_draft_prompt(
            [specs_by_id[keep_id]], {keep_id: ""}, seeds_by_id
        )
        call["prompt_sha256"] = sha256_bytes(prompt.encode("utf-8"))

        with self.assertRaisesRegex(Mix2KV4RepairError, "scheduler replay"):
            _validate_pipeline_state(specs=specs, seeds=seeds, state=state)

    def test_duplicate_rewrite_exhaustion_is_fail_atomic(self) -> None:
        _specs, _seeds, state, _record_ids = _draft_state_fixture()
        for record_id in state["selection_order"]:
            record = state["records"][record_id]
            draft = {
                "record_id": record_id,
                "answer": "같은 답변입니다.",
            }
            record["status"] = "accepted"
            record["current_draft"] = deepcopy(draft)
            record["current_draft_provider"] = "codex"
            record["accepted"] = {
                "draft_provider": "codex",
                "review_provider": "claude",
                "draft": deepcopy(draft),
                "review": {"decision": "PASS"},
            }
        exhausted_id = state["selection_order"][1]
        state["records"][exhausted_id]["duplicate_rewrites_used"] = 3
        before = deepcopy(state)

        for _ in range(2):
            with self.assertRaisesRegex(Mix2KV4RepairError, "세 차례"):
                _mark_duplicate_repairs(state)

        self.assertEqual(state, before)

    def test_duplicate_repair_reserves_inherited_exact_and_normalized_answers(
        self,
    ) -> None:
        repair_specs, _seeds, state, _record_ids = _draft_state_fixture()
        inherited_specs = [
            {"id": f"m2v4_inherited_{index:04d}", "task_axis": "general_empathy_replay"}
            for index in range(1600)
        ]
        inherited_answers = [
            f"상속 고유 답변 {index:04d}입니다." for index in range(1598)
        ] + ["예약 문장!", "예약 문장?"]
        parent_candidates = [
            {"id": spec["id"], "assistant": answer}
            for spec, answer in zip(inherited_specs, inherited_answers, strict=True)
        ]
        full_specs = [*inherited_specs, *repair_specs]
        reservations = _inherited_answer_reservations(
            specs=full_specs,
            parent_candidates=parent_candidates,
            parent_config={
                "diversity": {
                    "exact_duplicate_answers_maximum": 0,
                    "normalized_answer_multiplicity_maximum": 2,
                }
            },
        )
        state["identity"]["inherited_answer_reservations"] = reservations
        for index, record_id in enumerate(state["selection_order"]):
            answer = f"새 고유 답변 {index:04d}입니다."
            if index == 0:
                answer = inherited_answers[0]
            elif index == 1:
                answer = "예약 문장."
            draft = {"record_id": record_id, "answer": answer}
            record = state["records"][record_id]
            record["status"] = "accepted"
            record["current_draft"] = deepcopy(draft)
            record["accepted"] = {
                "draft_provider": "codex",
                "review_provider": "claude",
                "draft": deepcopy(draft),
                "review": {"decision": "PASS"},
            }

        self.assertEqual(_mark_duplicate_repairs(state, full_specs), 2)
        exact_id, normalized_id = state["selection_order"][:2]
        self.assertIn("exact", state["records"][exact_id]["feedback"])
        self.assertIn("normalized", state["records"][normalized_id]["feedback"])
        for index, record_id in enumerate((exact_id, normalized_id)):
            draft = {
                "record_id": record_id,
                "answer": f"재작성된 고유 답변 {index:04d}입니다.",
            }
            record = state["records"][record_id]
            record["status"] = "accepted"
            record["current_draft"] = deepcopy(draft)
            record["accepted"] = {
                "draft_provider": "codex",
                "review_provider": "claude",
                "draft": deepcopy(draft),
                "review": {"decision": "PASS"},
            }
        self.assertEqual(_mark_duplicate_repairs(state, full_specs), 0)

        tampered = deepcopy(state)
        first_digest = next(
            iter(
                tampered["identity"]["inherited_answer_reservations"][
                    "exact_sha256_counts"
                ]
            )
        )
        tampered["identity"]["inherited_answer_reservations"]["exact_sha256_counts"][
            first_digest
        ] = 2
        with self.assertRaisesRegex(Mix2KV4RepairError, "예약 분포"):
            _mark_duplicate_repairs(tampered, full_specs)

    def test_claude_call_has_one_json_schema_option(self) -> None:
        captured: list[str] = []

        def fake_run(command: list[str], **_: object) -> SimpleNamespace:
            captured.extend(command)
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"structured_output": {"drafts": []}}),
            )

        with patch(
            "scripts.data.mix2k_v4_reviewed_repair.subprocess.run",
            side_effect=fake_run,
        ):
            result = _repair_provider_call(
                provider="claude",
                prompt="검증",
                schema={"type": "object"},
                environment={},
                timeout_seconds=60,
                model="sonnet",
            )

        self.assertEqual(captured.count("--json-schema"), 1)
        self.assertEqual(result["structured"], {"drafts": []})

    def test_dashboard_candidate_is_inactive_and_keeps_active_v113_unchanged(
        self,
    ) -> None:
        report = validate_candidate(DASHBOARD_CANDIDATE_CONFIG, None)
        active = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.13.0.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(report["status"], "valid_inactive_candidate")
        self.assertFalse(report["feature_enabled_by_default"])
        self.assertFalse(report["active_dashboard_changed"])
        self.assertFalse(report["adapter_available"])
        self.assertEqual(active["model_check"]["generation"]["max_new_tokens"], 256)


if __name__ == "__main__":
    unittest.main()
