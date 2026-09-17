# test_saju_phase_plans.py - ZIP 원문 보존·행별 전량 대응·Phase 순서와 실행 권한을 검증한다.

import copy
import hashlib
import json
import re
import unittest
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit

from tests.test_saju_product_roadmap import (
    FORBIDDEN_REQUIRED_GATES,
    active_roadmap_documents,
)

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
DOCUMENTATION_TASK_IDS = {
    "C-0170", "C-0171", "C-0172", "C-0173",
    "C-0181", "C-0182", "C-0183", "C-0184",
    "M-0306", "M-0307", "M-0308", "M-0312", "O-0170",
    "O-0250", "O-0251", "O-0252", "O-0253",
    "O-0254", "O-0255", "O-0256", "O-0257",
}
NON_TASK_TYPES = {
    "current_fact", "policy", "development_example", "evidence_link", "structure",
}
S4_TASK_IDS = {"M-0293", "O-0173"}
PRODUCT_TASK_IDS = {"O-0144", "O-0174", *(f"O-{n:04}" for n in range(213, 221))}
S5_TASK_IDS = {"M-0294", "O-0175"}
IMPLEMENTATION_TASK_IDS = {"O-0171", "M-0292", "O-0172"} | S4_TASK_IDS | PRODUCT_TASK_IDS | S5_TASK_IDS
SEMANTIC_TYPES = NON_TASK_TYPES | {
    "execution_task", "conditional_proposal", "completed_history",
}


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
        if not {"requirement_type", "documentation_status", "execution_status"} <= entry.keys():
            raise ValueError("missing semantic status fields")
        if type(entry["line"]) is not int:
            raise ValueError("invalid source line")
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
        requirement_type = entry["requirement_type"]
        if requirement_type not in SEMANTIC_TYPES:
            raise ValueError("unknown requirement type")
        if entry["documentation_status"] not in {"pending", "verified"}:
            raise ValueError("unknown documentation status")
        if requirement_type in NON_TASK_TYPES:
            expected_status = "not_applicable"
        elif requirement_type == "completed_history":
            expected_status = "historical_completed"
        elif requirement_type == "conditional_proposal":
            expected_status = "conditional_not_executed"
        else:
            expected_status = "completed" if entry["id"] in DOCUMENTATION_TASK_IDS | IMPLEMENTATION_TASK_IDS else "not_executed"
        if entry["execution_status"] != expected_status:
            raise ValueError("requirement type/execution status contradiction")
        if entry["id"] in DOCUMENTATION_TASK_IDS and requirement_type != "execution_task":
            raise ValueError("documentation task misclassified")
        if entry["execution_status"] == "completed" and phase != 7:
            expected_phase = 11 if entry["id"] in S5_TASK_IDS else 10 if entry["id"] in PRODUCT_TASK_IDS else 9 if entry["id"] in S4_TASK_IDS else 8
            if phase != expected_phase or entry["id"] not in IMPLEMENTATION_TASK_IDS:
                raise ValueError("future phase incorrectly completed")
            evidence, separator, anchor = entry.get("execution_evidence", "").partition("#")
            expected_evidence = (
                "../../history/2026-09-17-phase11-data-hypotheses.md" if phase == 11
                else "../../history/2026-09-16-phase10-product-candidate.md" if phase == 10
                else "../../history/2026-09-16-phase9-s4-recovery.md" if phase == 9
                else "../../history/2026-09-16-phase8-intent-s3.md"
            )
            if evidence != expected_evidence or not separator:
                raise ValueError("implementation completion without evidence")
            expected_anchor = "phase11" if phase == 11 else "phase10" if phase == 10 else "phase9" if phase == 9 else "phase8a" if entry["id"] == "O-0171" else "phase8b"
            if anchor != expected_anchor:
                raise ValueError("implementation evidence refers to another task")
            if f'<a id="{anchor}"></a>' not in (ROADMAP / evidence).read_text(encoding="utf-8"):
                raise ValueError("implementation evidence section missing")
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
        self.assertEqual(self.manifest["schema_version"], "1.3.0")
        self.assertEqual(
            self.manifest["canonicalization_baseline_commit"],
            "d151548e2eed37e8d36dd2e5fd9328aec1fb7315",
        )
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
            7: 274, 8: 73, 9: 39, 10: 86, 11: 28, 12: 46, 13: 24, 14: 18,
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
        self.assertEqual(set(adaptations), {
            "C-0166", "O-0166", "O-0271", "M-0294", "M-0296", "O-0175", "O-0181",
        })
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
                    self.assertRegex(text, r"(?m)^상태: 완료\.")
                elif phase == 8:
                    self.assertIn("8A 후보 구현·CPU 확인 완료 / 8B S3 실행·검증 완료", text)
                    self.assertIn("2026-09-16-phase8-intent-s3.md#phase8a", text)
                    self.assertIn("2026-09-16-phase8-intent-s3.md#phase8b", text)
                elif phase == 9:
                    self.assertRegex(text, r"(?m)^상태: \*\*완료 — S4 v1.1")
                    self.assertIn("build-1f851d69a91f", text)
                    self.assertIn("2026-09-16-phase9-s4-recovery.md#phase9", text)
                    self.assertIn("build-296dffd1ef51", text)
                    self.assertIn("1오류·191미실행·정상 응답 0", text)
                    self.assertIn("2026-09-16-phase9-s4-execution.md#blocked-run", text)
                elif phase == 10:
                    self.assertIn("상태: **완료 — R16 단독 v1.20 코드 검토 보완·CPU/합성 화면 검증**", text)
                    self.assertIn("build-f13715ee1d91", text)
                    self.assertIn("2026-09-16-phase10-product-candidate.md#phase10", text)
                elif phase == 11:
                    self.assertIn("상태: **완료 — S5 기존 데이터 전수 대조·학습 가설·조건부 명세만 작성**", text)
                    self.assertIn("build-27a91a8e21a8", text)
                    self.assertIn("2026-09-17-phase11-data-hypotheses.md#phase11", text)
                elif phase == 12:
                    self.assertIn("상태: 미실행", text)
                elif phase >= 13:
                    self.assertIn("상태: 조건부 보류", text)

    def test_local_links_and_explicit_phase_anchors_resolve(self) -> None:
        paths = [*active_roadmap_documents()]
        paths.extend(REPO_ROOT / name for name in (
            "AGENTS.md", "README.md", "01_SAJU_PROJECT_MASTER_ARCHITECTURE_PLAN.md",
            "02_SAJU_RUNTIME_PERIOD_DASHBOARD_PLAN.md",
            "03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md",
            "implementation/plans/README.md",
            "implementation/plans/saju_system_context_diagnosis.md",
            "implementation/plans/saju_runtime_calculator_adoption.md",
            "implementation/plans/saju_1b_10k_20k_baseline/README.md",
            "implementation/plans/mix2k_v4_chart_day_lora.md",
            "implementation/history/2026-09-15-phase7-canonicalization.md",
            "implementation/history/2026-09-16-phase8-intent-s3.md",
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
                    if parsed.fragment and destination.suffix == ".md":
                        destination_text = destination.read_text(encoding="utf-8")
                        fragment = unquote(parsed.fragment)
                        explicit = f'<a id="{fragment}"></a>' in destination_text
                        heading_slugs = {
                            re.sub(r"[^\w\-\s]", "", heading.lower()).replace(" ", "-")
                            for heading in re.findall(r"^#{1,6} (.+)$", destination_text, re.MULTILINE)
                        }
                        self.assertTrue(explicit or fragment in heading_slugs, target)

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

    def test_documentation_completion_is_not_implementation_completion(self) -> None:
        entries = {entry["id"]: entry for entry in self.manifest["entries"]}
        self.assertEqual({e["documentation_status"] for e in entries.values()}, {"verified"})
        self.assertEqual({e["requirement_type"] for e in entries.values()}, SEMANTIC_TYPES)
        for source_id, requirement_type, status in (
            ("C-0170", "execution_task", "completed"),
            ("C-0035", "current_fact", "not_applicable"),
            ("M-0042", "completed_history", "historical_completed"),
            ("O-0068", "development_example", "not_applicable"),
            ("C-0197", "evidence_link", "not_applicable"),
            ("M-0292", "execution_task", "completed"),
            ("M-0293", "execution_task", "completed"),
            ("M-0296", "conditional_proposal", "conditional_not_executed"),
            ("C-0001", "structure", "not_applicable"),
        ):
            with self.subTest(source_id=source_id):
                self.assertEqual(entries[source_id]["requirement_type"], requirement_type)
                self.assertEqual(entries[source_id]["execution_status"], status)
        completed = {e["id"] for e in entries.values() if e["execution_status"] == "completed"}
        self.assertEqual(completed, DOCUMENTATION_TASK_IDS | IMPLEMENTATION_TASK_IDS)

    def test_status_validation_rejects_fictional_execution_and_missing_fields(self) -> None:
        for source_id, field, value in (
            ("O-0175", "execution_status", "not_executed"),
            ("M-0292", "execution_evidence", "../../history/2026-09-16-phase8-intent-s3.md#phase8a"),
            ("M-0296", "execution_status", "completed"),
            ("O-0068", "execution_status", "not_executed"),
            ("M-0042", "execution_status", "completed"),
            ("C-0035", "execution_status", "historical_completed"),
            ("C-0170", "requirement_type", "current_fact"),
            ("M-0292", "documentation_status", "executed"),
            ("M-0292", "requirement_type", "new_type"),
        ):
            manifest = copy.deepcopy(self.manifest)
            entry = next(e for e in manifest["entries"] if e["id"] == source_id)
            entry[field] = value
            with self.subTest(source_id=source_id, field=field), self.assertRaises(ValueError):
                validate_mapping(manifest)
        for field in ("requirement_type", "documentation_status", "execution_status"):
            manifest = copy.deepcopy(self.manifest)
            manifest["entries"][0].pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_mapping(manifest)

    def test_phase8a_completion_has_verified_cpu_evidence_not_deployment(self) -> None:
        entry = next(e for e in self.manifest["entries"] if e["id"] == "O-0171")
        self.assertEqual(entry["execution_status"], "completed")
        root = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-intent-canary/v2.0.0/build-49b9aed70565"
        summary = json.loads((root / "aggregate.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        self.assertEqual(summary["tests_passed"], 45)
        self.assertEqual(summary["browser"]["cases_passed"], 6)
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["aggregate_file_sha256"], hashlib.sha256((root / "aggregate.json").read_bytes()).hexdigest())
        self.assertEqual(verification["manifest_file_sha256"], hashlib.sha256((root / "build_manifest.json").read_bytes()).hexdigest())
        self.assertEqual(summary["governance"]["new_model_generations"], 0)
        self.assertFalse(summary["governance"]["service_changed"])
        self.assertFalse(summary["governance"]["production_promotion_allowed"])
        broken = copy.deepcopy(self.manifest)
        next(e for e in broken["entries"] if e["id"] == "O-0171").pop("execution_evidence")
        with self.assertRaises(ValueError):
            validate_mapping(broken)

    def test_phase10_completion_has_current_cpu_evidence_not_model_quality(self) -> None:
        entries = {e["id"]: e for e in self.manifest["entries"]}
        for source_id in PRODUCT_TASK_IDS:
            self.assertEqual(entries[source_id]["execution_status"], "completed")
            self.assertEqual(entries[source_id]["execution_evidence"], "../../history/2026-09-16-phase10-product-candidate.md#phase10")
            for field, value in (("execution_status", "not_executed"), ("execution_evidence", ""), ("execution_evidence", "../../history/2026-09-16-phase8-intent-s3.md#phase8a")):
                broken = copy.deepcopy(self.manifest)
                next(e for e in broken["entries"] if e["id"] == source_id)[field] = value
                with self.subTest(source_id=source_id, field=field), self.assertRaises(ValueError):
                    validate_mapping(broken)
        self.assertEqual(entries["M-0311"]["execution_status"], "conditional_not_executed")
        self.assertEqual(entries["O-0156"]["execution_status"], "conditional_not_executed")
        root = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91"
        summary = json.loads((root / "aggregate.json").read_bytes())
        manifest = json.loads((root / "build_manifest.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        self.assertEqual(summary["tests_passed"], 46)
        self.assertEqual(summary["synthetic_browser_cases_passed"], 16)
        self.assertEqual(summary["candidate_engine"], "lora_r16")
        self.assertFalse(summary["candidate_adopted_for_production"])
        self.assertEqual(summary["cpu_policy_response_kind_counts"], {"direct_fact": 3, "model_generated": 4, "clarification": 3, "blocked": 2})
        self.assertEqual(summary["quality_dimensions"], {"naturalness": "not_measured", "semantics": "not_measured"})
        self.assertEqual(summary["request_budget"], {"actual_model_requests_added": 0, "previous_total": 631, "current_total": 631, "remaining": 49})
        self.assertEqual(summary["governance"]["new_model_generations"], 0)
        for flag in ("service_changed", "production_service_accessed", "production_promotion_allowed", "runtime_release_changed", "training_performed", "sealed_blind_accessed", "raw_outputs_published", "phase12_executed"):
            self.assertIs(summary["governance"][flag], False)
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["aggregate_file_sha256"], hashlib.sha256((root / "aggregate.json").read_bytes()).hexdigest())
        self.assertEqual(verification["manifest_file_sha256"], hashlib.sha256((root / "build_manifest.json").read_bytes()).hexdigest())
        for path, expected in manifest["identity"]["source_sha256"].items():
            self.assertEqual(hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest(), expected, path)
        for path, expected in manifest["identity"]["parent_manifest_sha256"].items():
            self.assertEqual(hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest(), expected, path)

    def test_phase10_bugfix_keeps_parent_and_new_cpu_evidence(self) -> None:
        root = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-product-canary/v2.0.0/build-35dc83ce161d"
        summary = json.loads((root / "aggregate.json").read_bytes())
        manifest = json.loads((root / "build_manifest.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        self.assertEqual((summary["tests_passed"], summary["synthetic_browser_cases_passed"], summary["policy_case_count"]), (56, 32, 18))
        self.assertEqual(summary["request_budget"]["actual_model_requests_added"], 0)
        self.assertEqual(summary["quality_dimensions"], {"naturalness": "not_measured", "semantics": "not_measured"})
        self.assertFalse(summary["candidate_adopted_for_production"])
        self.assertIn("data/reports/saju_1b_baseline/dashboard-product-canary/v1.0.0/build-f13715ee1d91/build_manifest.json", manifest["identity"]["parent_manifest_sha256"])
        for group in ("source_sha256", "parent_manifest_sha256"):
            for path, expected in manifest["identity"][group].items():
                self.assertEqual(hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest(), expected, path)
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["aggregate_file_sha256"], hashlib.sha256((root / "aggregate.json").read_bytes()).hexdigest())
        self.assertEqual(verification["manifest_file_sha256"], hashlib.sha256((root / "build_manifest.json").read_bytes()).hexdigest())
        for flag in ("training_performed", "sealed_blind_accessed", "service_changed", "runtime_release_changed", "production_promotion_allowed", "phase12_executed"):
            self.assertIs(summary["governance"][flag], False)
        phase = (ROADMAP / "phases/phase-10.md").read_text()
        self.assertIn("2026-09-17-phase10-product-fixes.md#phase10-fixes", phase)
        self.assertIn("build-35dc83ce161d", phase)

    def test_claude_review_successor_preserves_evidence_and_execution_boundaries(self) -> None:
        root = REPO_ROOT / "data/reports/saju_1b_baseline/dashboard-product-canary/v3.0.0/build-bc8f19f91297"
        summary = json.loads((root / "aggregate.json").read_bytes())
        manifest = json.loads((root / "build_manifest.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        self.assertEqual((summary["tests_passed"], summary["synthetic_browser_cases_passed"], summary["policy_case_count"]), (71, 32, 36))
        self.assertEqual(manifest["identity"]["response_policy_version"], "saju-product-response-v1.2.0")
        for path in (
            "dashboard-product-canary/v2.0.0/build-35dc83ce161d/build_manifest.json",
            "system-context-s5/v1.0.0/build-27a91a8e21a8/aggregate.json",
            "system-context-s5/v1.0.0/build-27a91a8e21a8/build_manifest.json",
        ):
            self.assertIn("data/reports/saju_1b_baseline/" + path, manifest["identity"]["parent_manifest_sha256"])
        for group in ("source_sha256", "parent_manifest_sha256"):
            for path, expected in manifest["identity"][group].items():
                self.assertEqual(hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest(), expected, path)
        self.assertEqual(verification["status"], "verified")
        for name, field in (("aggregate.json", "aggregate_file_sha256"), ("build_manifest.json", "manifest_file_sha256")):
            self.assertEqual(verification[field], hashlib.sha256((root / name).read_bytes()).hexdigest())
        self.assertEqual(summary["request_budget"], {"actual_model_requests_added": 0, "previous_total": 631, "current_total": 631, "remaining": 49})
        self.assertEqual(summary["quality_dimensions"], {"naturalness": "not_measured", "semantics": "not_measured"})
        self.assertFalse(summary["candidate_adopted_for_production"])
        for flag in ("training_performed", "sealed_blind_accessed", "service_changed", "runtime_release_changed", "production_promotion_allowed", "phase12_executed", "production_service_accessed"):
            self.assertIs(summary["governance"][flag], False)
        phase = (ROADMAP / "phases/phase-10.md").read_text()
        self.assertIn("2026-09-17-claude-product-review.md#claude-review", phase)
        self.assertIn(root.name, phase)
        self.assertIn("기본 포트 8772", phase)
        self.assertIn("dashboard/v1.20.0/product-v1.2.0/manual_sessions", phase)
        self.assertIn("상태: 미실행", (ROADMAP / "phases/phase-12.md").read_text())

    def test_active_collection_recurses_and_keeps_archive_policy_separate(self) -> None:
        real_active = set(active_roadmap_documents())
        self.assertTrue({ROADMAP / f"phases/phase-{i:02d}.md" for i in range(7, 15)} <= real_active)
        archive_paths = {ROADMAP / source["path"] for source in self.manifest["sources"]}
        self.assertFalse(real_active & archive_paths)
        # 미래 하위 디렉터리도 검사하고 archive의 과거 표현은 현재 정책으로 바꾸지 않는다.
        with TemporaryDirectory(prefix="saju-plan-scope-") as directory:
            root = Path(directory)
            nested = root / "phases/nested/phase-example.md"
            archived = root / "archive/source.md"
            history = root / "history/past.md"
            for path in (nested, archived, history):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(FORBIDDEN_REQUIRED_GATES[0], encoding="utf-8")
            self.assertEqual(active_roadmap_documents(root), (nested,))
            scanned = "\n".join(p.read_text() for p in active_roadmap_documents(root))
            self.assertIn(FORBIDDEN_REQUIRED_GATES[0], scanned)

    def test_phase8b_completion_has_frozen_actual_evidence_not_promotion(self) -> None:
        for task_id in ("M-0292", "O-0172"):
            entry = next(e for e in self.manifest["entries"] if e["id"] == task_id)
            self.assertEqual(entry["execution_status"], "completed")
            self.assertTrue(entry["execution_evidence"].endswith("#phase8b"))
            broken = copy.deepcopy(self.manifest)
            next(e for e in broken["entries"] if e["id"] == task_id).pop("execution_evidence")
            with self.assertRaises(ValueError):
                validate_mapping(broken)
        root = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s3/v1.0.0/build-ffd985905b51"
        summary = json.loads((root / "aggregate.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        for name, key, digest in (
            ("aggregate.json", "aggregate_file_sha256", "46e83c1e1b787ad1a244b723f3982efa275d042fe04e4cc1ed6d2590368bf3a2"),
            ("build_manifest.json", "manifest_file_sha256", "c1e83a8423da5e74a5f9c0eed5de0b7eeca6c851fde7893f4eca3d0122c8e5a0"),
        ):
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
            self.assertEqual(verification[key], digest)
        self.assertEqual(verification["status"], "verified")
        self.assertFalse(verification["raw_files_git_tracked"])
        self.assertEqual(summary["requests"], 96)
        self.assertEqual(summary["statuses"], {"generated": 86, "preblocked": 10})
        self.assertEqual(summary["new_generations"], 86)
        self.assertEqual(summary["preflight_requests"], 0)
        self.assertTrue(summary["fresh_both_arms"])
        self.assertFalse(summary["candidate_selected"])
        self.assertEqual(summary["scorer_version"], "role-aware-contract-v1.1.0")
        arms = {r["arm"]: r for r in summary["summaries"] if r["stratum"] == "all"}
        self.assertEqual(set(arms), {"P0", "P1"})
        for arm in arms.values():
            self.assertEqual(arm["engine"], "lora_r16")
            self.assertEqual(arm["requests"], 48)
            self.assertEqual(arm["statuses"], {"generated": 43, "preblocked": 5})
            self.assertEqual(arm["stop_reasons"], {"eos": 43})
            for metric in arm["metrics"].values():
                self.assertEqual(metric["applicable"], sum(metric[k] for k in ("PASS", "FAIL", "UNSCORABLE")))
        for arm, counts in (("P0", (11, 6, 7)), ("P1", (12, 8, 4))):
            metric = arms[arm]["metrics"]["required_fact_use"]
            self.assertEqual(tuple(metric[k] for k in ("PASS", "FAIL", "UNSCORABLE")), counts)
        self.assertEqual(summary["quality_dimensions"], {"naturalness": "not_measured", "semantics": "not_measured"})
        self.assertEqual(summary["governance"], {
            "confirmation_24_used": False, "larger_model_downloaded": False,
            "phase8a_routing_used": False, "production_promotion_allowed": False,
            "runtime_release_changed": False, "sealed_blind_accessed": False,
            "service_changed": False, "synthetic_only": True, "training_performed": False,
        })

    def test_agents_and_indexes_share_authority_without_changing_safety(self) -> None:
        agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for marker in (
            "전체 실행 순서", "Phase 0~6의 완료 이력·계약", "Phase 7의 문서 반영 완료",
            "archive/", "phases/", "원본 바이트", "git add -- <명시적 파일 목록>",
            "원본 프로젝트 폴더의 `master`", "새 브랜치·worktree를 만들지 않는다",
            "AI Hub 제한 데이터", "토큰·키·로컬 설정값", "불변 산출물",
        ):
            self.assertTrue(marker in agents, marker)
        baseline = (REPO_ROOT / "implementation/plans/saju_1b_10k_20k_baseline/README.md").read_text()
        self.assertIn("Phase 0~6의 완료 이력·계약", baseline)
        self.assertEqual(re.findall(r"^\| ([0-6]) \| .*? \| 완료 \|", baseline, re.MULTILINE), list("0123456"))

    def test_training_rows_point_to_actual_decision_conditions(self) -> None:
        entries = {entry["id"]: entry for entry in self.manifest["entries"]}
        for source_id, target in {
            "M-0294": "phases/phase-11.md#failure-attribution",
            "M-0296": "phases/phase-13.md#training-entry",
            "O-0175": "phases/phase-11.md#failure-attribution",
            "O-0181": "phases/phase-12.md#training-decision",
        }.items():
            with self.subTest(source_id=source_id):
                self.assertEqual(entries[source_id]["target"], target)
                self.assertEqual(entries[source_id]["disposition"], "adapted")
                self.assertIn("사용자 보완 4", entries[source_id]["reason"])

    def test_content_mapping_uses_specific_existing_owners(self) -> None:
        entries = {entry["id"]: entry for entry in self.manifest["entries"]}
        checks = (
            ("C-0066", "phases/phase-07.md#calc-freeze", "유지보수까지 영구 금지"),
            ("C-0186", "phases/phase-07.md#reopen", "기존 기간/관계 재사용 가능성"),
            ("M-0292", "phases/phase-08.md#s3-controls", "상한은 48×2=96요청"),
            ("M-0293", "phases/phase-09.md#registration", "동일 정밀도·순차 실행"),
            ("M-0309", "phases/phase-08.md#instruction-bundle", "P0 파일은 보존한다"),
            ("M-0311", "phases/phase-10.md#context", "질문별 projection 변경"),
            ("M-0339", "phases/phase-09.md#comparison", "공개 benchmark를 이 프로젝트 성능으로 대체하지 않는다"),
            ("O-0171", "phases/phase-08.md#routing", "읽기 전용"),
            ("O-0174", "phases/phase-10.md#cpu-regression", "최소 앱 후보 하나"),
            ("O-0176", "phases/phase-12.md#confirmation", "24×2=48요청"),
            ("O-0177", "phases/phase-14.md#rollout-decision", "별도 승인된 전환"),
            ("O-0267", "phases/phase-11.md#failure-attribution", "먼저 앱/상태를 수정한다"),
            ("O-0270", "phases/phase-07.md#phase-order", "양자화·새 모델 계열·대형 라우터·장기 메모리·대운"),
        )
        for source_id, target, marker in checks:
            with self.subTest(source_id=source_id):
                self.assertEqual(entries[source_id]["target"], target)
                path, anchor = target.split("#")
                text = (ROADMAP / path).read_text(encoding="utf-8")
                section = text.split(f'<a id="{anchor}"></a>', 1)[1].split('\n<a id="', 1)[0]
                self.assertTrue(marker in section, target)

    def test_phase_boundaries_include_conditions_not_only_existing_links(self) -> None:
        checks = {
            (8, "routing"): (
                "이전 사주 대화", "이전 일반 대화", "맥락 없음·불명확",
                "읽기 전용", "이력 재작성·snapshot 전환·모델 컨텍스트 재구성은 하지 않는다",
            ),
            (10, "revision-history"): (
                "기존 구현이 새 대화를 요구하면", "이전 snapshot을 덮어쓰지 않는다",
                "대형 라우터·장기 메모리·자동 요약·세션 전면 개편",
                "선행 조건으로도 삼지 않는다", "향후 요약 오염 검사",
            ),
            (11, "failure-attribution"): (
                "학습 필요성 가설", "조건부 학습 명세", "실행 확정으로 표시하지 않는다",
                "Phase 11 완료만으로 Phase 13에 진입하지 않는다",
            ),
            (12, "confirmation"): (
                "전체 실행 구성 하나", "새 24문항을 사용하기 전에",
                "모델/adapter·지시문 묶음·정보 선택·라우팅·이력 정책·검사 기준·채택 규칙",
                "같은 확인 질문으로 재선발하지 않는다", "미시험 3B/P1", "첫 실제 확인",
            ),
            (12, "training-decision"): (
                "실제 제품 후보 결과와 대조", "정상 입력에서도 대상 모델 오류가 남고",
                "데이터 변경의 필요성이 확인", "학습을 건너뛴다", "실행 보류",
            ),
            (13, "training-entry"): (
                "Phase 12에서 확인", "데이터 변경 필요성", "학습을 건너뛴다",
                "Phase 11 완료만으로 실행하지 않는다",
            ),
            (14, "comparison-baselines"): (
                "R16/P0/C_FULL", "운영 기본 모델은 KI20", "작업 시 실제 운영",
                "현재 운영 대비 개선이라고 표현하지 않는다", "not_measured",
                "S6 비교군을 임의로 늘리지 않는다",
            ),
            (14, "rollback"): (
                "코드·모델/adapter·지시문 묶음·입력 정책·설정",
                "세션/binding", "schema·revision·snapshot 호환성",
                "호환성이 확인되지 않으면 전환하지 않으며",
            ),
        }
        for (phase, anchor), markers in checks.items():
            text = (ROADMAP / f"phases/phase-{phase:02d}.md").read_text(encoding="utf-8")
            section = text.split(f'<a id="{anchor}"></a>', 1)[1].split('\n<a id="', 1)[0]
            for marker in markers:
                with self.subTest(phase=phase, anchor=anchor, marker=marker):
                    self.assertTrue(marker in section, f"Phase {phase} #{anchor}: {marker}")

    def test_remaining_budget_is_arithmetic_not_permission(self) -> None:
        text = (ROADMAP / "phases/phase-07.md").read_text(encoding="utf-8")
        budget = text.split('<a id="budget"></a>', 1)[1].split("\n## 입력", 1)[0]
        counts = [int(n) for n in re.findall(r"^\| .*? \| (\d+) \|$", budget, re.MULTILINE)]
        self.assertEqual(counts, [680 - 342, 96 + 192 + 48, 8 - 6])
        self.assertEqual(counts[0], sum(counts[1:]))
        self.assertIn("Phase 7 등록 당시", budget)
        self.assertIn("242 = 680 - 438 = 본 비교 240(S4 192 + S6 48) + 적격성 2", budget)
        self.assertIn("241 = 680 - 439 = S4 미처리 191 + S6 48 + 적격성 2", budget)
        self.assertEqual(680 - 438 - 1, 191 + 48 + 2)
        self.assertIn("49 = 680 - 631 = S6 48 + 공유 여유 1", budget)
        self.assertEqual(680 - 438 - 1 - 192, 48 + 1)
        self.assertIn("여유분을 자동 전용하지 않는다", budget)
        self.assertEqual(680 - 342 - 96, 192 + 48 + 2)
        for marker in (
            "실행 승인이나 성공 생성 수가 아니다", "목적·범위·예산을 사전에 별도 등록",
            "CPU 모의 테스트는 실제 모델 호출이 아니다", "Phase 13 학습 후 평가는",
        ):
            self.assertTrue(marker in budget, marker)
        self.assertFalse(self.manifest["governance"]["gpu_execution_allowed"])
        phase13 = (ROADMAP / "phases/phase-13.md").read_text(encoding="utf-8")
        self.assertIn("질문은 학습 자료나 새 모델 확인에 재사용하지 않는다", phase13)
        self.assertIn("S6 잔여 요청과 별도의", phase13)

    def test_s5_completion_requires_immutable_analysis_evidence_not_training(self) -> None:
        root = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8"
        self.assertEqual({p.name for p in root.iterdir()}, {"aggregate.json", "build_manifest.json", "verification.json"})
        summary = json.loads((root / "aggregate.json").read_bytes())
        manifest = json.loads((root / "build_manifest.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        for name, key, pin in (
            ("aggregate.json", "aggregate_file_sha256", "bb34e74c7f0dda2b58b1d1c6b4fa868d1ac0c58a2cdca35bbe48312f19ed4150"),
            ("build_manifest.json", "manifest_file_sha256", "65ef8dd8b4647766d98b5adacf826e1647aae34ed32e9fa3b707510bb3929f7d"),
        ):
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), pin)
            self.assertEqual(verification[key], pin)
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(summary["status"], "completed_analysis_only")
        self.assertEqual(summary["decision"], "hold_repair_and_training_until_phase12")
        config_path = REPO_ROOT / "configs/model_versions/saju_1b_baseline/system-context-s5-v1.0.0.json"
        self.assertEqual(hashlib.sha256(config_path.read_bytes()).hexdigest(), manifest["identity"]["config_sha256"])
        for path, pin in manifest["identity"]["source_sha256"].items():
            with self.subTest(source=path):
                self.assertFalse(Path(path).is_absolute())
                self.assertNotIn("..", Path(path).parts)
                self.assertEqual(hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest(), pin)
        for item in (summary, manifest, verification):
            self.assertIs(item["governance"]["cpu_only"], True)
            self.assertEqual(item["governance"]["new_model_generations"], 0)
            self.assertEqual(item["governance"]["teacher_calls"], 0)
            for flag in ("training_performed", "data_changed", "service_changed", "production_promotion_allowed", "runtime_release_changed", "sealed_blind_accessed", "phase12_executed", "confirmation_24_created"):
                self.assertIs(item["governance"][flag], False)
        self.assertEqual(summary["request_budget"], {"actual_model_requests_added": 0, "remaining": 49, "total": 631})
        self.assertEqual(summary["candidate_canary"]["build_id"], "build-35dc83ce161d")
        entries = {entry["id"]: entry for entry in self.manifest["entries"]}
        for source_id in S5_TASK_IDS:
            self.assertEqual(entries[source_id]["execution_status"], "completed")
            self.assertEqual(entries[source_id]["execution_evidence"], "../../history/2026-09-17-phase11-data-hypotheses.md#phase11")
            for field, value in (("execution_status", "not_executed"), ("execution_evidence", ""), ("execution_evidence", "../../history/2026-09-16-phase10-product-candidate.md#phase10")):
                changed = copy.deepcopy(self.manifest)
                next(e for e in changed["entries"] if e["id"] == source_id)[field] = value
                with self.subTest(source_id=source_id, field=field), self.assertRaises(ValueError):
                    validate_mapping(changed)
        self.assertEqual(entries["O-0176"]["execution_status"], "not_executed")
        self.assertEqual(entries["M-0296"]["execution_status"], "conditional_not_executed")

    def test_s5_data_limits_and_full_token_audit_are_not_quality_approval(self) -> None:
        path = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8/aggregate.json"
        summary = json.loads(path.read_bytes())
        data, token = summary["data_distribution"], summary["token_audit"]
        self.assertEqual(data["rows"], 2000)
        self.assertEqual(data["final_three_or_more_nonempty_lines"], 1751)
        self.assertEqual(data["axes"]["general_korean_empathy"]["rows"], 250)
        self.assertEqual(data["axes"]["general_korean_empathy"]["bound"], 0)
        self.assertEqual(data["axes"]["general_korean_empathy"]["multiturn"], 0)
        self.assertEqual(data["teacher"]["actual_reviewers"], {"codex": 1809, "claude": 191})
        self.assertFalse(data["lexical_zero_proves_semantic_absence"])
        overlap = data["development_overlap"]
        self.assertEqual(overlap["rows"], 200)
        self.assertEqual(overlap["normalized_last_user_overlap_rows"], 200)
        self.assertEqual(overlap["exact_parent_dialogue_overlap_rows"], 0)
        self.assertEqual(overlap["chart_fingerprint_overlap_rows"], 0)
        self.assertEqual(overlap["semantic_family_overlap"], "not_measured")
        self.assertFalse(overlap["targets_used_for_training"])
        for key in ("rows_recomputed", "stored_audit_exact_matches", "same_serialized_dialogue_token_ids_equal_rows", "same_serialized_dialogue_masks_equal_rows"):
            self.assertEqual(token[key], 2000)
        for key in ("maximum_token_count_delta", "truncated_rows", "loss_leakage_rows", "unsupervised_final_eos_rows"):
            self.assertEqual(token[key], 0)
        self.assertEqual(token["maximum_rendered_tokens"], 1960)
        self.assertFalse(token["weights_loaded"])
        self.assertFalse(token["candidate_information_selection_matches_old_full_snapshot"])
        repair = data["repair"]
        self.assertEqual(repair["status_counts"], {"accepted": 238, "needs_review": 3, "needs_draft": 159})
        self.assertEqual((repair["inherited_rows_if_finalized"], repair["replaced_rows_if_finalized"], repair["total_rows_if_finalized"]), (1600, 400, 2000))
        self.assertFalse(repair["automatic_resume_allowed"])
        self.assertFalse(repair["applied_to_current_r16"])

    def test_s5_hypotheses_require_phase12_falsification_and_separate_execution(self) -> None:
        path = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8/aggregate.json"
        summary = json.loads(path.read_bytes())
        self.assertEqual(set(summary["behavior_hypotheses"]), {"bound_general", "topic_switch", "short_format", "false_premise", "correction_history", "fact_selection", "uncertainty"})
        for hypothesis in summary["behavior_hypotheses"].values():
            self.assertEqual(hypothesis["candidate_live_result"], "not_measured")
            self.assertEqual(hypothesis["falsification"], "phase12_target_behavior_succeeds_without_data_change")
        spec = summary["conditional_training_specification"]
        self.assertFalse(spec["execution_authorized"])
        self.assertFalse(spec["phase12_consumed_questions_reusable"])
        self.assertEqual(spec["method"], "fresh_lora_from_pinned_k0_not_r16_continuation")
        self.assertEqual((spec["rank"], spec["provisional_rows"], spec["preferred_max_length"]), (16, 2000, 2048))
        self.assertEqual(spec["training"]["learning_rate"], 5e-5)
        self.assertEqual(spec["training"]["num_train_epochs"], 1)
        self.assertEqual(spec["post_training_evaluation_budget"], "separately_register_before_model_calls")
        self.assertEqual(spec["skip_rule"], "skip_training_if_application_or_instructions_resolve_target_errors")
        phase = (ROADMAP / "phases/phase-11.md").read_text(encoding="utf-8")
        for marker in ("200/200행 겹쳤다", "전체 샘플 중복·정답 유출의 증명은 아니다", "현재 제안은 **보류**", "execution_authorized=false", "Phase 11 완료만으로 Phase 13에 진입하지 않는다", "새 24문항은 아직 만들거나 사용하지 않았다"):
            self.assertIn(marker, phase)

    def test_completed_s4_requires_new_evidence_and_cannot_use_failed_or_missing_execution(self) -> None:
        for source_id in S4_TASK_IDS:
            for field, value in (("execution_status", "blocked"), ("execution_status", "not_executed"), ("execution_evidence", ""), ("execution_evidence", "../../history/2026-09-16-phase9-s4-execution.md#blocked-run")):
                with self.subTest(source_id=source_id, field=field, value=value):
                    changed = copy.deepcopy(self.manifest)
                    row = next(entry for entry in changed["entries"] if entry["id"] == source_id)
                    row[field] = value
                    with self.assertRaises(ValueError):
                        validate_mapping(changed)

    def test_phase9_has_verified_full_comparison_and_preserves_failure_budget_and_limits(self) -> None:
        root = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s4/v1.1.0/build-1f851d69a91f"
        summary = json.loads((root / "aggregate.json").read_bytes())
        manifest = json.loads((root / "build_manifest.json").read_bytes())
        verification = json.loads((root / "verification.json").read_bytes())
        for name, key, pin in (
            ("aggregate.json", "aggregate_file_sha256", "dcdf9e33e14d3b1f70b80f986afbc384cab09e722e675268a1d14ab593dfb45f"),
            ("build_manifest.json", "manifest_file_sha256", "61122bd326762fb9acb98389ac3fab0876bef5dcbf300612c0f3e218a9a2e29a"),
        ):
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), pin)
            self.assertEqual(verification[key], pin)
        self.assertEqual(verification["status"], "verified")
        self.assertFalse(verification["raw_files_git_tracked"])
        self.assertEqual(summary["requests"], 192)
        self.assertEqual(summary["statuses"], {"generated": 172, "preblocked": 20})
        self.assertEqual(summary["new_generations"], 172)
        self.assertFalse(summary["candidate_selected"])
        self.assertTrue(summary["fresh_all_conditions"])
        self.assertEqual(summary["scorer_version"], "role-aware-contract-v1.2.0")
        self.assertEqual(summary["quality_dimensions"], {"naturalness": "not_measured", "semantics": "not_measured"})
        self.assertTrue(summary["governance"]["synthetic_only"])
        self.assertTrue(all(not value for key, value in summary["governance"].items() if key != "synthetic_only"))
        self.assertEqual(manifest["publication_counts"], {"new_generations": 172, "new_preblocks": 20, "reused_generations": 0, "reused_preblocks": 0})
        self.assertEqual(manifest["consumer_preflight"]["actual_storage_api_replays"], 172)
        self.assertEqual(manifest["consumer_preflight"]["model_calls"], 0)
        self.assertEqual(manifest["service_before"], manifest["service_after"])
        self.assertEqual(manifest["identity"]["previous_attempt"]["consumed_requests"], 1)
        accounting = manifest["request_accounting"]
        self.assertEqual(accounting["before_s4"] + accounting["preserved_failed_requests"] + accounting["new_comparison_requests"], 631)
        self.assertEqual(accounting["remaining_after_completion"], 49)
        arms = {(r["engine"], r["arm"]): r for r in summary["summaries"] if r["stratum"] == "all"}
        for key, counts in {
            ("k0_instruct", "C_FULL"): (3, 20, 1), ("k0_instruct", "C_MIN"): (2, 20, 2),
            ("kanana3b_instruct", "C_FULL"): (3, 15, 6), ("kanana3b_instruct", "C_MIN"): (8, 11, 5),
        }.items():
            row = arms[key]
            self.assertEqual(row["statuses"], {"generated": 43, "preblocked": 5})
            self.assertEqual(tuple(row["metrics"]["required_fact_use"][k] for k in ("PASS", "FAIL", "UNSCORABLE")), counts)
            self.assertEqual(row["metrics"]["false_premise_corrected"]["PASS"], 0)
        self.assertEqual(arms[("kanana3b_instruct", "C_MIN")]["stop_reasons"], {"eos": 42, "max_tokens": 1})
        quad = next(r for r in summary["interaction_common_scorable_quads"] if r["stratum"] == "all" and r["metric"] == "required_fact_use")
        self.assertEqual((quad["common_scorable_cases"], quad["with_unscorable_cases"], quad["full_model_pass_delta"], quad["minimum_model_pass_delta"]), (15, 9, 2, 5))

    def test_s4_does_not_wait_for_app_and_s6_keeps_two_paths(self) -> None:
        phase8 = (ROADMAP / "phases/phase-08.md").read_text(encoding="utf-8")
        phase9 = (ROADMAP / "phases/phase-09.md").read_text(encoding="utf-8")
        phase12 = (ROADMAP / "phases/phase-12.md").read_text(encoding="utf-8")
        self.assertIn("8A나 전체 모드 구현 때문에 S4를 미루지 않는다", phase8)
        self.assertIn("Phase 8B", phase9)
        self.assertIn("8A", phase9)
        self.assertIn("부정/개념 설명 오탐 2건과 일반 사주 언급 누락 2건", phase9)
        self.assertIn("동결 S3 scorer·집계의 사후 수정이나 S3 재튜닝은 하지 않는다", phase9)
        self.assertIn("R16/P0/C_FULL과 선택 후보 하나의 24×2=48요청", phase12)
        for marker in (
            "모델 생성, 프로그램 직접 응답, 확인 요청, 예상 차단",
            "예상 밖 차단/실행 오류, 미실행", "검증된 재사용",
        ):
            self.assertIn(marker, phase12)


if __name__ == "__main__":
    unittest.main()
