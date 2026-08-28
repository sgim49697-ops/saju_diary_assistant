# test_phase2_review_web.py - Phase 2A HTML 검수기의 API·보안·판정 revision을 검증한다.

from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from scripts.data.phase2_review_web import (
    REPO_ROOT,
    ReviewIdentity,
    ReviewState,
    create_server,
)


class ReviewWebTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text(
            '<meta name="csrf-token" content="__CSRF_TOKEN__">', encoding="utf-8"
        )
        (self.assets / "review.css").write_text("body{}", encoding="utf-8")
        (self.assets / "review.js").write_text("'use strict';", encoding="utf-8")
        (self.assets / "review_manifest.json").write_text("{}", encoding="utf-8")
        self.decisions = self.root / "decisions.jsonl"
        self.decisions.write_bytes(b"")
        self.sealed = self.root / "SEALED.json"
        self.queue = [
            {
                "schema_version": "1.0.0",
                "review_id": "a" * 24,
                "queue": "required",
                "source": "aihub_empathy",
                "stratum": "safety_sample",
                "unit_type": "single",
                "flags": ["self_harm"],
                "locators": [{"secret-row": 7}],
            },
            {
                "schema_version": "1.0.0",
                "review_id": "b" * 24,
                "queue": "reference",
                "source": "yeji_bazi_rules",
                "stratum": "remaining_rules",
                "unit_type": "single",
                "flags": ["correction_applied"],
                "locators": [{"rule": 11}],
            },
        ]
        self.policy = {
            "decision_schema_version": "1.1.0",
            "decision_values": [
                "accept",
                "exclude_candidate",
                "rule_fix_required",
                "source_block",
                "uncertain",
                "skip",
            ],
            "reason_codes": ["low_quality", "rule_conflict", "other"],
        }
        identity = ReviewIdentity(
            dataset_name="test_dataset",
            audit_version="v1.1.0",
            build_id="build-0123456789ab",
            build_sha256="1" * 64,
            reviewer_version="reviewer-v1.0.0",
            review_tool_sha256="2" * 64,
        )

        def raw_loader(locator: dict[str, Any]) -> dict[str, Any]:
            if "rule" in locator:
                return {
                    "id": 11,
                    "name_cn": "词馆",
                    "name_ko": "사관",
                    "type": "길신",
                    "category": "학술류",
                    "condition": {"mapping": {"金": {"간지": "壬卯"}}},
                    "meaning": "문학적 명성",
                }
            return {"profile": {"private": "sample"}, "talk": {"content": {}}}

        correction_manifest = {
            "corrections": [
                {
                    "correction_id": "YEJI_CIGUAN_GOLD_PILLAR",
                    "rule_id": 11,
                    "field_path": ["condition", "mapping", "金", "간지"],
                    "expected_original": "壬卯",
                    "replacement": "壬申",
                    "resolves": ["YEJI_CIGUAN_CONFLICT"],
                    "basis": "검증 근거",
                }
            ]
        }
        state = ReviewState(
            identity=identity,
            queue=self.queue,
            policy=self.policy,
            decision_path=self.decisions,
            sealed_path=self.sealed,
            raw_loader=raw_loader,
            correction_manifest=correction_manifest,
            blocking_findings=[],
            resolved_findings=["YEJI_CIGUAN_CONFLICT"],
        )
        self.token = "test-csrf-token"
        self.server = create_server(
            state, self.assets, port=0, csrf_token=self.token
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        host, port = self.server.server_address
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, {key.lower(): value for key, value in response.getheaders()}, payload
        connection.close()
        return result

    def api_headers(self, *, origin: bool = False) -> dict[str, str]:
        headers = {"X-CSRF-Token": self.token}
        if origin:
            headers["Origin"] = self.server.expected_origin
            headers["Content-Type"] = "application/json"
        return headers

    def test_static_entrypoint_injects_token_and_security_headers(self) -> None:
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.token.encode(), body)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn("default-src 'self'", headers["content-security-policy"])
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual(headers["x-frame-options"], "DENY")

    def test_api_requires_csrf_and_never_exposes_locators_in_bootstrap(self) -> None:
        status, _, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 403)
        status, _, body = self.request(
            "GET", "/api/bootstrap", headers=self.api_headers()
        )
        self.assertEqual(status, 200)
        rendered = body.decode("utf-8")
        self.assertNotIn("locators", rendered)
        self.assertNotIn("secret-row", rendered)
        payload = json.loads(body)
        self.assertEqual(payload["status"]["required_total"], 1)
        self.assertEqual(payload["status"]["reference_total"], 1)

    def test_item_returns_raw_and_verified_yeji_overlay(self) -> None:
        status, _, body = self.request(
            "GET", f"/api/items/{'b' * 24}", headers=self.api_headers()
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(
            payload["raw_records"][0]["condition"]["mapping"]["金"]["간지"],
            "壬卯",
        )
        self.assertEqual(
            payload["records"][0]["condition"]["mapping"]["金"]["간지"],
            "壬申",
        )
        self.assertEqual(payload["corrections"][0]["basis"], "검증 근거")
        self.assertNotIn("locators", payload)

    def test_decision_requires_origin_and_appends_linked_revisions(self) -> None:
        first = {
            "review_id": "a" * 24,
            "decision": "accept",
            "reason_code": None,
            "private_note": None,
        }
        body = json.dumps(first).encode()
        status, _, _ = self.request(
            "POST",
            "/api/decisions",
            body=body,
            headers={
                "X-CSRF-Token": self.token,
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 403)
        status, _, response = self.request(
            "POST",
            "/api/decisions",
            body=body,
            headers=self.api_headers(origin=True),
        )
        self.assertEqual(status, 201)
        saved_first = json.loads(response)["saved"]
        self.assertEqual(saved_first["revision"], 1)

        second = {
            **first,
            "decision": "exclude_candidate",
            "reason_code": "low_quality",
            "private_note": "응답 품질 확인",
        }
        status, _, response = self.request(
            "POST",
            "/api/decisions",
            body=json.dumps(second, ensure_ascii=False).encode("utf-8"),
            headers=self.api_headers(origin=True),
        )
        self.assertEqual(status, 201)
        payload = json.loads(response)
        self.assertEqual(payload["saved"]["revision"], 2)
        self.assertEqual(
            payload["saved"]["supersedes_decision_id"],
            saved_first["decision_id"],
        )
        self.assertEqual(payload["status"]["required_completed"], 1)
        self.assertEqual(payload["status"]["decision_history_entries"], 2)
        self.assertEqual(len(self.decisions.read_text().splitlines()), 2)

    def test_request_contract_path_and_seal_are_enforced(self) -> None:
        status, _, _ = self.request("GET", "/%2e%2e/private")
        self.assertEqual(status, 400)
        invalid = {
            "review_id": "a" * 24,
            "decision": "accept",
            "reason_code": None,
            "private_note": None,
            "unexpected": True,
        }
        status, _, _ = self.request(
            "POST",
            "/api/decisions",
            body=json.dumps(invalid).encode(),
            headers=self.api_headers(origin=True),
        )
        self.assertEqual(status, 400)
        self.sealed.write_text("{}", encoding="utf-8")
        valid = {key: invalid[key] for key in ("review_id", "decision", "reason_code", "private_note")}
        status, _, _ = self.request(
            "POST",
            "/api/decisions",
            body=json.dumps(valid).encode(),
            headers=self.api_headers(origin=True),
        )
        self.assertEqual(status, 423)

    def test_wrong_host_and_options_are_rejected_without_cors(self) -> None:
        status, _, _ = self.request("GET", "/", headers={"Host": "example.test"})
        self.assertEqual(status, 421)
        status, headers, _ = self.request("OPTIONS", "/api/decisions")
        self.assertEqual(status, 405)
        self.assertNotIn("access-control-allow-origin", headers)


class ReviewerAssetTests(unittest.TestCase):
    def test_committed_assets_do_not_persist_raw_data_or_load_external_code(self) -> None:
        root = (
            REPO_ROOT
            / "data/reports/saju_1b_baseline/audit-review/v1.1.0"
            / "build-e162d9b2b7dc/reviewer-v1.0.0"
        )
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "review.js").read_text(encoding="utf-8")
        css = (root / "review.css").read_text(encoding="utf-8")
        combined = f"{html}\n{javascript}\n{css}"
        self.assertEqual(html.count("__CSRF_TOKEN__"), 1)
        self.assertNotIn("innerHTML", javascript)
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)
        self.assertNotIn("http://", combined)
        self.assertNotIn("https://", combined)
        self.assertNotIn(".style.", javascript)


if __name__ == "__main__":
    unittest.main()
