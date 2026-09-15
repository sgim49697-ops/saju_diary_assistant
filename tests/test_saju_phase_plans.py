# test_saju_phase_plans.py - ZIP 원문 보존·행별 전량 대응·Phase 순서와 실행 권한을 검증한다.

import copy
import hashlib
import json
import re
import unittest
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP = REPO_ROOT / "implementation/plans/saju_product_roadmap"
MANIFEST = ROADMAP / "requirements-20260915.json"
ARCHIVE_HASH = "85fe0f0716347b5fdbe83fe9b1aeccca67efd6917b30de2dfb248592b8bab47c"
BASELINE_COMMIT = "390ca88f4b65f6c3b87f0f36ca26cbc492251a06"
SOURCE_PINS = (
    ("C", "01_SAJU_CALCULATOR_SCOPE_PLAN.md",
     "597a654de38f12d7250d319d8eb7c8aeac5ab5f7b3bc0d1028534753edd5c993",
     18171, 203, 19, 9),
    ("M", "02_SAJU_MODEL_DIAGNOSIS_TRAINING_PLAN.md",
     "103ef7597f37dc2b3fbd5d6cfc2d5acc6522848372aeac43aabc8348a705d0bb",
     28555, 339, 33, 13),
    ("O", "03_SAJU_OVERALL_EXECUTION_PLAN.md",
     "7398dcbe2ed97888f4e11fdfc1d17bad7d2a33af087753425a18b31bb50d0c30",
     25845, 299, 28, 10),
)
LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def source_units(text: str) -> dict[int, str]:
    units = {}
    fenced = False
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```"):
            kind = "code"
            fenced = not fenced
        elif fenced:
            kind = "code"
        elif re.match(r"^#{1,6} ", line):
            kind = "heading"
        elif re.fullmatch(r"\|[\s|:\-]+\|", line):
            kind = "structure"
        elif line.startswith("|"):
            kind = "table_row"
        elif re.match(r"^(?:[-*] |\d+\. )", line):
            kind = "list_item"
        else:
            kind = "paragraph"
        units[line_number] = kind
    return units


def validate_mapping(manifest: dict) -> None:
    expected = {}
    for source in manifest["sources"]:
        text = (ROADMAP / source["path"]).read_text(encoding="utf-8")
        for line, kind in source_units(text).items():
            expected[(source["id"], line)] = kind
    seen = set()
    for entry in manifest["entries"]:
        identity = (entry["source"], entry["line"])
        if identity in seen or identity not in expected:
            raise ValueError(f"duplicate or unknown source line: {identity}")
        seen.add(identity)
        if entry["id"] != f"{identity[0]}-{identity[1]:04d}":
            raise ValueError("unstable source ID")
        if entry["kind"] != expected[identity]:
            raise ValueError("incorrect source kind")
        phase = entry["phase"]
        if type(phase) is not int or phase not in range(7, 15):
            raise ValueError("unsupported phase")
        target, separator, anchor = entry["target"].partition("#")
        if target != f"phases/phase-{phase:02d}.md" or not separator or not anchor:
            raise ValueError("invalid target")
        destination = (ROADMAP / target).read_text(encoding="utf-8")
        if f'<a id="{anchor}"></a>' not in destination:
            raise ValueError("missing target anchor")
        if entry["disposition"] not in {
            "preserved", "adopted", "planned", "conditional", "adapted",
        }:
            raise ValueError("unknown disposition")
        if entry["disposition"] == "adapted" and not entry.get("reason", "").strip():
            raise ValueError("adaptation without reason")
    if seen != set(expected):
        raise ValueError("unmapped source lines")


class SajuPhasePlansTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(
            MANIFEST.read_text(encoding="utf-8"), object_pairs_hook=unique_json_object,
        )

    def test_source_bytes_and_provenance_match_received_archive(self) -> None:
        self.assertEqual(self.manifest["archive_name"], "saju_plans_20260915.zip")
        self.assertEqual(self.manifest["archive_sha256"], ARCHIVE_HASH)
        self.assertEqual(self.manifest["baseline_commit"], BASELINE_COMMIT)
        self.assertEqual(self.manifest["schema_version"], "1.0.0")
        self.assertEqual(len(self.manifest["sources"]), 3)
        for source, pin in zip(self.manifest["sources"], SOURCE_PINS, strict=True):
            source_id, name, digest, size, lines, sections, links = pin
            with self.subTest(source=source_id):
                expected_path = f"archive/2026-09-15/{name}"
                self.assertEqual(source, {
                    "id": source_id, "path": expected_path, "sha256": digest,
                    "bytes": size, "lines": lines, "sections": sections, "links": links,
                })
                path = ROADMAP / expected_path
                self.assertFalse(path.is_symlink())
                raw = path.read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
                self.assertEqual(len(raw), size)
                text = raw.decode("utf-8")
                self.assertEqual(len(text.splitlines()), lines)
                self.assertEqual(len(re.findall(r"^#{2,3} ", text, re.MULTILINE)), sections)
                self.assertEqual(len(LINK_PATTERN.findall(text)), links)

    def test_every_nonempty_source_line_maps_exactly_once(self) -> None:
        validate_mapping(self.manifest)
        self.assertEqual(self.manifest["totals"], {
            "documents": 3, "lines": 841, "sections": 80, "links": 32,
            "mapped_nonempty_lines": 588,
        })
        entries = self.manifest["entries"]
        self.assertEqual(len(entries), 588)
        self.assertEqual(Counter(entry["phase"] for entry in entries), {
            7: 295, 8: 67, 9: 35, 10: 84, 11: 25, 12: 42, 13: 23, 14: 17,
        })

    def test_coverage_rejects_omissions_duplicates_and_unknown_lines(self) -> None:
        for mutation in ("missing", "duplicate", "unknown"):
            manifest = copy.deepcopy(self.manifest)
            if mutation == "missing":
                manifest["entries"].pop()
            elif mutation == "duplicate":
                manifest["entries"].append(copy.deepcopy(manifest["entries"][0]))
            else:
                manifest["entries"][0]["line"] = 9999
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_mapping(manifest)

    def test_coverage_rejects_wrong_phases_paths_anchors_and_kinds(self) -> None:
        for field, value in (
            ("phase", 6), ("phase", True), ("id", "C-1"), ("kind", "omitted"),
            ("target", "../README.md#source-governance"),
            ("target", "phases/phase-07.md#missing-anchor"),
            ("disposition", "ignored"),
        ):
            manifest = copy.deepcopy(self.manifest)
            manifest["entries"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_mapping(manifest)

    def test_adaptations_record_user_numbering_and_save_authority(self) -> None:
        adaptations = {
            entry["id"]: entry for entry in self.manifest["entries"]
            if entry["disposition"] == "adapted"
        }
        self.assertEqual(set(adaptations), {"C-0166", "O-0166", "O-0271"})
        self.assertIn("Phase 7", self.manifest["numbering_decision"])
        for entry in adaptations.values():
            self.assertTrue(entry["reason"].strip())
        manifest = copy.deepcopy(self.manifest)
        adapted = next(e for e in manifest["entries"] if e["disposition"] == "adapted")
        adapted.pop("reason")
        with self.assertRaises(ValueError):
            validate_mapping(manifest)

    def test_manifest_rejects_duplicate_json_keys(self) -> None:
        with self.assertRaises(ValueError):
            json.loads('{"phase": 7, "phase": 8}', object_pairs_hook=unique_json_object)

    def test_section_index_matches_all_source_headings(self) -> None:
        receipt = (ROADMAP / "source-20260915.md").read_text(encoding="utf-8")
        rows = re.findall(
            r"^\| ([CMO]-\d{4}) \| (.*?) \| (\d+) \| \[반영 절\]\(([^)]+)\) \|$",
            receipt, re.MULTILINE,
        )
        expected = []
        entries = {entry["id"]: entry for entry in self.manifest["entries"]}
        for source in self.manifest["sources"]:
            text = (ROADMAP / source["path"]).read_text(encoding="utf-8")
            for line, content in enumerate(text.splitlines(), 1):
                heading = re.match(r"^#{2,3} (.+)$", content)
                if heading:
                    source_id = f"{source['id']}-{line:04d}"
                    entry = entries[source_id]
                    expected.append((source_id, heading[1], str(entry["phase"]), entry["target"]))
        self.assertEqual(rows, expected)
        self.assertEqual(len(rows), 80)

    def test_phase_files_have_actionable_sections_and_unique_anchors(self) -> None:
        for phase in range(7, 15):
            path = ROADMAP / f"phases/phase-{phase:02d}.md"
            text = path.read_text(encoding="utf-8")
            with self.subTest(phase=phase):
                for section in (
                    "목적과 현재 상태", "진입 조건", "작업 순서", "입력·산출물",
                    "검증", "종료·중단과 다음 단계", "원문 대응", "진행 기록",
                ):
                    self.assertIn(f"## {section}\n\n", text)
                anchors = re.findall(r'<a id="([^"]+)"></a>', text)
                self.assertEqual(len(anchors), len(set(anchors)))
                self.assertIn("../source-20260915.md", text)
                if phase == 7:
                    self.assertIn("상태: 완료", text)
                elif 8 <= phase <= 12:
                    self.assertIn("상태: 미실행", text)
                elif phase >= 13:
                    self.assertIn("상태: 조건부 보류", text)

    def test_local_links_and_explicit_phase_anchors_resolve(self) -> None:
        paths = [*ROADMAP.rglob("*.md")]
        paths.extend(REPO_ROOT / name for name in (
            "README.md", "01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md",
            "02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md",
            "03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md",
            "implementation/plans/README.md",
            "implementation/plans/saju_system_context_diagnosis.md",
            "implementation/plans/saju_runtime_calculator_adoption.md",
            "implementation/plans/saju_1b_10k_20k_baseline/README.md",
            "implementation/plans/mix2k_v4_chart_day_lora.md",
            "implementation/history/2026-09-15-phase7-canonicalization.md",
        ))
        for path in paths:
            for target in LINK_PATTERN.findall(path.read_text(encoding="utf-8")):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc:
                    continue
                destination = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
                with self.subTest(path=path.relative_to(REPO_ROOT), target=target):
                    self.assertTrue(destination.is_relative_to(REPO_ROOT))
                    self.assertTrue(destination.exists())
                    if parsed.fragment and destination.parent == ROADMAP / "phases":
                        self.assertIn(
                            f'<a id="{parsed.fragment}"></a>',
                            destination.read_text(encoding="utf-8"),
                        )

    def test_source_code_references_point_to_existing_baseline_paths(self) -> None:
        prefix = f"https://github.com/sgim49697-ops/saju_diary_assistant/blob/{BASELINE_COMMIT}/"
        count = 0
        for source in self.manifest["sources"]:
            text = (ROADMAP / source["path"]).read_text(encoding="utf-8")
            for target in LINK_PATTERN.findall(text):
                if target.startswith("https://github.com/"):
                    self.assertTrue(target.startswith(prefix), target)
                    self.assertTrue((REPO_ROOT / target.removeprefix(prefix)).is_file(), target)
                    count += 1
        self.assertEqual(count, 24)

    def test_documentation_does_not_authorize_execution(self) -> None:
        self.assertEqual(self.manifest["governance"], {
            "documentation_only": True, "gpu_execution_allowed": False,
            "training_allowed": False, "service_change_allowed": False,
            "runtime_release_change_allowed": False, "new_approval_gate_created": False,
        })
        phase7 = (ROADMAP / "phases/phase-07.md").read_text(encoding="utf-8")
        for marker in (
            "기존 Phase 0~6", "Phase 8~14", "GPU·teacher·학습·서비스 변경은 실행하지 않는다",
            "680", "336", "적격성 잔여는 최대 2", "계약 밖 평가를 완료 조건으로 추가하지 않는다",
        ):
            self.assertIn(marker, phase7)
        index = (ROADMAP / "README.md").read_text(encoding="utf-8")
        for phase in range(7, 15):
            self.assertIn(f"phases/phase-{phase:02d}.md", index)
        self.assertIn("8B → 9", index)
        self.assertIn("S3 반복·R8/R32 추가 비교·20K 학습", index)


if __name__ == "__main__":
    unittest.main()
