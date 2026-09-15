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
IMPLEMENTATION_TASK_IDS = {"O-0171"}
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
            if phase != 8 or entry["id"] not in IMPLEMENTATION_TASK_IDS:
                raise ValueError("future phase incorrectly completed")
            evidence, separator, anchor = entry.get("execution_evidence", "").partition("#")
            if evidence != "../../history/2026-09-16-phase8-intent-s3.md" or not separator:
                raise ValueError("implementation completion without evidence")
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
        self.assertEqual(self.manifest["schema_version"], "1.2.0")
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
                    self.assertIn("8A 후보 구현·CPU 확인 완료 / 8B 실행 준비", text)
                    self.assertIn("2026-09-16-phase8-intent-s3.md#phase8a", text)
                elif 9 <= phase <= 12:
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
            ("M-0292", "execution_task", "not_executed"),
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
            ("M-0292", "execution_status", "completed"),
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
        for marker in (
            "실행 승인이나 성공 생성 수가 아니다", "목적·범위·예산을 사전에 별도 등록",
            "CPU 모의 테스트는 실제 모델 호출이 아니다", "Phase 13 학습 후 평가는",
        ):
            self.assertTrue(marker in budget, marker)
        self.assertFalse(self.manifest["governance"]["gpu_execution_allowed"])
        phase13 = (ROADMAP / "phases/phase-13.md").read_text(encoding="utf-8")
        self.assertIn("질문은 학습 자료나 새 모델 확인에 재사용하지 않는다", phase13)
        self.assertIn("S6 잔여 요청과 별도의", phase13)

    def test_s4_does_not_wait_for_app_and_s6_keeps_two_paths(self) -> None:
        phase8 = (ROADMAP / "phases/phase-08.md").read_text(encoding="utf-8")
        phase9 = (ROADMAP / "phases/phase-09.md").read_text(encoding="utf-8")
        phase12 = (ROADMAP / "phases/phase-12.md").read_text(encoding="utf-8")
        self.assertIn("8A나 전체 모드 구현 때문에 S4를 미루지 않는다", phase8)
        self.assertIn("Phase 8B", phase9)
        self.assertIn("8A", phase9)
        self.assertIn("R16/P0/C_FULL과 선택 후보 하나의 24×2=48요청", phase12)
        for marker in (
            "모델 생성, 프로그램 직접 응답, 확인 요청, 예상 차단",
            "예상 밖 차단/실행 오류, 미실행", "검증된 재사용",
        ):
            self.assertIn(marker, phase12)


if __name__ == "__main__":
    unittest.main()
