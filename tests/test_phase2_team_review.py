# test_phase2_team_review.py - 팀원용 핵심 검수 ZIP의 투영·무결성·의견 계약을 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from scripts.data.errors import Phase2AuditError
from scripts.data.phase2_export_team_review import (
    ASSET_ROOT,
    EXPECTED_REQUIRED_RECORDS,
    EXPECTED_REQUIRED_UNITS,
    STATIC_ASSETS,
    _archive_document,
    _zip_directory,
    build_package_document,
    build_projected_items,
    materialize_package,
    verify_archive,
    verify_feedback,
)


def _fixture_queue() -> list[dict[str, Any]]:
    pair_counts = {
        "aihub_empathy": 10,
        "bazi_sft": 10,
        "nemotron_saju": 10,
        "yeji_bazi_rules": 0,
    }
    queue: list[dict[str, Any]] = []
    review_index = 0
    record_index = 0
    for source, unit_count in EXPECTED_REQUIRED_UNITS.items():
        for source_index in range(unit_count):
            locator_count = 2 if source_index < pair_counts[source] else 1
            locators = []
            for offset in range(locator_count):
                if source == "yeji_bazi_rules":
                    value = source_index + 1
                else:
                    value = record_index + offset
                locators.append({"source": source, "record": value})
            queue.append(
                {
                    "queue": "required",
                    "review_id": f"{review_index:024x}",
                    "source": source,
                    "stratum": f"fixture_{source_index % 3}",
                    "unit_type": "pair" if locator_count == 2 else "single",
                    "flags": ["fixture"] if source_index == 0 else [],
                    "locators": locators,
                }
            )
            review_index += 1
            record_index += locator_count
    return queue


def _fixture_raw(locator: dict[str, Any]) -> dict[str, Any]:
    source = locator["source"]
    value = int(locator["record"])
    if source == "aihub_empathy":
        return {
            "profile": {
                "profile-id": f"private-{value}",
                "persona": {
                    "persona-id": f"persona-{value}",
                    "human": "청년",
                    "computer": "상담자",
                },
                "emotion": {"type": "불안", "situation": "진로 고민"},
            },
            "talk": {
                "talk-id": f"talk-{value}",
                "content": {"HS01": "요즘 불안해요.", "SS01": "많이 힘드셨겠어요."},
            },
        }
    if source == "nemotron_saju":
        return {
            "uuid": f"private-{value}",
            "birth_year": 1990,
            "birth_longitude": 127.0,
            "persona": "가상의 직장인",
            "sex": "female",
            "age": 35,
            "occupation": "engineer",
            "saju_pillars": {
                "year": {"stem_hanja": "甲", "branch_hanja": "子"},
                "month": {"stem_hanja": "乙", "branch_hanja": "丑"},
                "day": {"stem_hanja": "丙", "branch_hanja": "寅"},
                "hour": {"stem_hanja": "丁", "branch_hanja": "卯"},
            },
            "saju_day_master": "丙",
            "saju_elements": {"wood": 3, "fire": 2},
            "saju_narrative": {"summary": "균형을 살펴보는 예시 해석"},
            "saju_narrative_error": None,
        }
    if source == "bazi_sft":
        return {
            "example_id": f"example-{value}",
            "synthetic_id": f"synthetic-{value}",
            "birth_input": {"date": "1990-01-01", "location": "private"},
            "facts": {
                "bazi_year": 2026,
                "pillars": {
                    "year": {"stem": "Jia", "branch": "Zi"},
                    "month": {"stem": "Yi", "branch": "Chou"},
                    "day": {"stem": "Bing", "branch": "Yin"},
                    "hour": {"stem": "Ding", "branch": "Mao"},
                },
                "day_master": "Bing",
                "element_counts": {"wood": 3, "fire": 2},
                "birth_input": {"date": "1990-01-01"},
            },
            "retrieved_rules": [
                {
                    "id": "rule-1",
                    "name": "균형",
                    "citation": "fixture",
                    "effect": "균형을 점검한다.",
                }
            ],
            "question_type": "career",
            "user_question": "진로 흐름을 알려주세요.",
            "response": "명식과 규칙을 바탕으로 가능성을 설명합니다.",
            "citations": ["fixture"],
        }
    if source == "yeji_bazi_rules":
        category = "흉살류" if value == 19 else "학술류"
        pillar = "壬卯" if value == 11 else "甲子"
        return {
            "id": value,
            "name_cn": f"规则{value}",
            "name_ko": f"규칙 {value}",
            "type": "길신",
            "category": category,
            "condition": {"rule": "조건", "mapping": {"金": {"간지": pillar}}},
            "meaning": "검수용 규칙 의미",
        }
    raise AssertionError(f"unknown fixture source: {source}")


def _correction_manifest() -> dict[str, Any]:
    return {
        "corrections": [
            {
                "correction_id": "ciguan",
                "rule_id": 11,
                "field_path": ["condition", "mapping", "金", "간지"],
                "expected_original": "壬卯",
                "replacement": "壬申",
                "resolves": ["YEJI_CIGUAN_CONFLICT"],
                "basis": "교차 검증 근거",
            },
            {
                "correction_id": "wugui",
                "rule_id": 19,
                "field_path": ["category"],
                "expected_original": "흉살류",
                "replacement": "재앙류",
                "resolves": ["YEJI_STRUCTURE_FAILURE"],
                "basis": "구조 검증 근거",
            },
        ]
    }


def _fixture_package() -> tuple[dict[str, Any], dict[str, Any]]:
    projected = build_projected_items(
        _fixture_queue(), _fixture_raw, _correction_manifest()
    )
    context = {
        "identity": {
            "build_id": "build-fixture000001",
            "build_sha256": "b" * 64,
        },
        "policy": {
            "dataset_name": "saju_1b_baseline",
            "audit_version": "v1.1.0",
            "decision_values": [
                "accept",
                "exclude_candidate",
                "rule_fix_required",
                "source_block",
                "uncertain",
                "skip",
            ],
            "reason_codes": [
                "safety_overclaim",
                "unsafe_advice",
                "pii",
                "schema_error",
                "rule_conflict",
                "mistranslation",
                "factual_inconsistency",
                "low_quality",
                "other",
            ],
        },
    }
    values = {"queue_manifest": {"queue_sha256": "c" * 64}}
    return build_package_document(context, values, projected)


def _write_archive(root: Path) -> tuple[Path, dict[str, Any]]:
    document, manifest_base = _fixture_package()
    package_root = root / "saju-review-fixture"
    materialize_package(package_root, document, manifest_base)
    archive_path = root / "saju-review-fixture.zip"
    _zip_directory(package_root, archive_path)
    return archive_path, document


class MinimalProjectionTests(unittest.TestCase):
    def test_projection_has_exact_allocation_and_strips_identifiers(self) -> None:
        items = build_projected_items(
            _fixture_queue(), _fixture_raw, _correction_manifest()
        )
        units = Counter(item["source"] for item in items)
        records: Counter[str] = Counter()
        for item in items:
            records[item["source"]] += len(item["records"])
        self.assertEqual(dict(units), EXPECTED_REQUIRED_UNITS)
        self.assertEqual(dict(records), EXPECTED_REQUIRED_RECORDS)

        rendered = json.dumps(items, ensure_ascii=False)
        for forbidden in (
            '"uuid"',
            '"profile-id"',
            '"persona-id"',
            '"talk-id"',
            '"example_id"',
            '"synthetic_id"',
            '"birth_input"',
            '"locator"',
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("많이 힘드셨겠어요", rendered)
        self.assertIn("명식과 규칙을 바탕으로", rendered)

    def test_yeji_overlay_shows_original_and_effective_values(self) -> None:
        items = build_projected_items(
            _fixture_queue(), _fixture_raw, _correction_manifest()
        )
        rule = next(
            item
            for item in items
            if item["source"] == "yeji_bazi_rules"
            and item["records"][0]["id"] == 11
        )
        self.assertEqual(
            rule["original_records"][0]["condition"]["mapping"]["金"]["간지"],
            "壬卯",
        )
        self.assertEqual(
            rule["records"][0]["condition"]["mapping"]["金"]["간지"],
            "壬申",
        )
        self.assertEqual(rule["corrections"][0]["basis"], "교차 검증 근거")


class TeamReviewArchiveTests(unittest.TestCase):
    def test_archive_round_trip_and_file_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path, document = _write_archive(Path(directory))
            result = verify_archive(archive_path)
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["unit_count"], 150)
            self.assertEqual(result["record_count"], 180)
            self.assertEqual(_archive_document(archive_path), document)
            self.assertEqual(os.stat(archive_path).st_mode & 0o777, 0o600)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertTrue(
                    all(
                        (entry.external_attr >> 16) & 0o777 == 0o600
                        for entry in archive.infolist()
                    )
                )

    def test_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "malicious.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                info = zipfile.ZipInfo("../escape.txt")
                info.external_attr = 0o100600 << 16
                archive.writestr(info, b"escape")
            with self.assertRaises(Phase2AuditError):
                verify_archive(archive_path)

    def test_archive_rejects_windows_style_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "malicious-windows.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                info = zipfile.ZipInfo(r"share\..\escape.txt")
                info.external_attr = 0o100600 << 16
                archive.writestr(info, b"escape")
            with self.assertRaises(Phase2AuditError):
                verify_archive(archive_path)

    def test_feedback_is_bound_to_package_and_advisory_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path, document = _write_archive(root)
            feedback_path = root / "feedback.json"
            feedback = {
                "schema_version": "1.0.0",
                "feedback_type": "advisory_team_review",
                "export_kind": "final",
                "package_id": document["package_id"],
                "audit_version": document["audit_version"],
                "build_id": document["build_id"],
                "reviewer_label": "데이터팀 검수자",
                "exported_at": "2026-08-27T12:00:00.000Z",
                "completed_units": 2,
                "total_units": 150,
                "suggestions": [
                    {
                        "review_id": document["items"][0]["review_id"],
                        "suggested_decision": "accept",
                        "reason_code": None,
                        "comment": "문제 없음",
                        "reviewed_at": "2026-08-27T11:59:00.000Z",
                    },
                    {
                        "review_id": document["items"][1]["review_id"],
                        "suggested_decision": "uncertain",
                        "reason_code": "other",
                        "comment": "원 담당자 재확인 필요",
                        "reviewed_at": "2026-08-27T11:59:30.000Z",
                    },
                ],
            }
            feedback_path.write_text(
                json.dumps(feedback, ensure_ascii=False), encoding="utf-8"
            )
            result = verify_feedback(archive_path, feedback_path)
            self.assertEqual(result["completed_units"], 2)
            self.assertTrue(result["advisory_only"])

            invalid_cases = {
                "other build": {"build_id": "build-other"},
                "unknown item": {
                    "suggestions": [
                        {
                            **feedback["suggestions"][0],
                            "review_id": "f" * 24,
                        }
                    ],
                    "completed_units": 1,
                },
                "accept reason": {
                    "suggestions": [
                        {**feedback["suggestions"][0], "reason_code": "other"}
                    ],
                    "completed_units": 1,
                },
                "invalid timestamp": {"exported_at": "not-a-timestamp"},
            }
            for label, changes in invalid_cases.items():
                with self.subTest(label=label):
                    invalid = {**deepcopy(feedback), **changes}
                    feedback_path.write_text(
                        json.dumps(invalid, ensure_ascii=False), encoding="utf-8"
                    )
                    with self.assertRaises(Phase2AuditError):
                        verify_feedback(archive_path, feedback_path)

    def test_static_assets_are_offline_and_avoid_unsafe_html_sinks(self) -> None:
        html = (ASSET_ROOT / "START_HERE.html").read_text(encoding="utf-8")
        javascript = (ASSET_ROOT / "team-review.js").read_text(encoding="utf-8")
        self.assertIn("default-src 'none'", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn("textContent", javascript)
        self.assertIn("sort(dialogueOrder)", javascript)
        self.assertIn(r"^\s*[=+\-@]", javascript)
        for forbidden in (
            "innerHTML",
            "fetch(",
            "XMLHttpRequest",
            "WebSocket",
            "localStorage",
            "sessionStorage",
        ):
            self.assertNotIn(forbidden, javascript)
        self.assertEqual(set(STATIC_ASSETS), {
            "START_HERE.html",
            "team-review.css",
            "team-review.js",
        })


if __name__ == "__main__":
    unittest.main()
