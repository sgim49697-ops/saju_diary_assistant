# phase2_review_web.py - 버전 고정 Phase 2A 원문 검수기를 로컬 HTTP로 제공한다.

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.audit_tools import (
    _decision_map,
    _load_build_files,
    _load_raw_record,
    _read_jsonl,
    _validate_decisions,
    append_review_decision,
    apply_yeji_corrections,
    audit_status_from_values,
    prepare_audit,
    sha256_file,
    sha256_json,
    verify_audit,
)
from scripts.data.errors import Phase1Error, Phase2AuditError

DEFAULT_SOURCE_CONFIG = REPO_ROOT / "configs/data_sources.v1.json"
DEFAULT_POLICY = (
    REPO_ROOT / "configs/data_versions/saju_1b_baseline/audit-policy-v1.1.0.json"
)
REVIEWER_VERSION = "reviewer-v1.0.0"
MAX_REQUEST_BYTES = 16 * 1024
MAX_PRIVATE_NOTE_CHARS = 2_000
REVIEW_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
STATIC_TYPES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/review.css": ("review.css", "text/css; charset=utf-8"),
    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
    "/review_manifest.json": (
        "review_manifest.json",
        "application/json; charset=utf-8",
    ),
}


class ReviewRequestError(Exception):
    """브라우저 요청에 안전하게 반환할 상태와 메시지를 보관한다."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ReviewIdentity:
    dataset_name: str
    audit_version: str
    build_id: str
    build_sha256: str
    reviewer_version: str
    review_tool_sha256: str


class ReviewState:
    """검토 큐와 append-only 결정을 HTTP 계층에서 안전하게 연결한다."""

    def __init__(
        self,
        *,
        identity: ReviewIdentity,
        queue: list[dict[str, Any]],
        policy: dict[str, Any],
        decision_path: Path,
        sealed_path: Path,
        raw_loader: Callable[[dict[str, Any]], Any],
        correction_manifest: dict[str, Any] | None,
        blocking_findings: Sequence[str],
        resolved_findings: Sequence[str],
    ) -> None:
        self.identity = identity
        self.queue = queue
        self.policy = policy
        self.decision_path = decision_path
        self.sealed_path = sealed_path
        self.raw_loader = raw_loader
        self.correction_manifest = correction_manifest
        self.blocking_findings = sorted(set(blocking_findings))
        self.resolved_findings = sorted(set(resolved_findings))
        self._items = {item["review_id"]: item for item in queue}
        if len(self._items) != len(queue):
            raise Phase2AuditError("검토 큐 review_id가 중복됐습니다.")
        self._write_lock = threading.RLock()

    def _decisions(self) -> list[dict[str, Any]]:
        decisions = _read_jsonl(self.decision_path, "검토 결정")
        _validate_decisions(decisions, self.policy)
        return decisions

    @staticmethod
    def _decision_summary(value: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.get(key)
            for key in (
                "decision_id",
                "review_id",
                "revision",
                "supersedes_decision_id",
                "decision",
                "reason_code",
                "reviewed_at",
                "reviewer",
                "reviewer_version",
            )
        }

    @classmethod
    def _decision_detail(cls, value: dict[str, Any]) -> dict[str, Any]:
        return {**cls._decision_summary(value), "private_note": value.get("private_note")}

    def bootstrap(self) -> dict[str, Any]:
        decisions = self._decisions()
        latest = _decision_map(decisions)
        history_counts: dict[str, int] = {}
        for value in decisions:
            review_id = str(value["review_id"])
            history_counts[review_id] = history_counts.get(review_id, 0) + 1
        items = []
        for index, item in enumerate(self.queue, 1):
            current = latest.get(item["review_id"])
            items.append(
                {
                    "index": index,
                    "review_id": item["review_id"],
                    "queue": item["queue"],
                    "source": item["source"],
                    "stratum": item["stratum"],
                    "unit_type": item["unit_type"],
                    "flags": item["flags"],
                    "latest_decision": (
                        self._decision_summary(current) if current is not None else None
                    ),
                    "history_count": history_counts.get(item["review_id"], 0),
                }
            )
        return {
            "schema_version": "1.0.0",
            "identity": {
                "dataset_name": self.identity.dataset_name,
                "audit_version": self.identity.audit_version,
                "build_id": self.identity.build_id,
                "build_sha256": self.identity.build_sha256,
                "reviewer_version": self.identity.reviewer_version,
            },
            "items": items,
            "status": {
                **audit_status_from_values(self.queue, decisions),
                "sealed": self.sealed_path.exists(),
            },
            "decision_values": list(self.policy["decision_values"]),
            "reason_codes": list(self.policy["reason_codes"]),
            "blocking_finding_codes": self.blocking_findings,
            "resolved_finding_codes": self.resolved_findings,
        }

    def item(self, review_id: str) -> dict[str, Any]:
        item = self._items.get(review_id)
        if item is None:
            raise ReviewRequestError(404, "검토 항목을 찾을 수 없습니다.")
        records = [self.raw_loader(locator) for locator in item["locators"]]
        corrected_records = deepcopy(records)
        corrections: list[dict[str, Any]] = []
        if item["source"] == "yeji_bazi_rules" and self.correction_manifest:
            rule_ids = {
                int(record["id"])
                for record in records
                if isinstance(record, dict) and isinstance(record.get("id"), int)
            }
            selected = [
                value
                for value in self.correction_manifest["corrections"]
                if int(value["rule_id"]) in rule_ids
            ]
            if selected:
                partial_manifest = {
                    **self.correction_manifest,
                    "corrections": selected,
                }
                corrected_document, applied = apply_yeji_corrections(
                    {"shensha_list": records}, partial_manifest
                )
                corrected_records = corrected_document["shensha_list"]
                basis_by_id = {
                    value["correction_id"]: value.get("basis") for value in selected
                }
                corrections = [
                    {**value, "basis": basis_by_id.get(value["correction_id"])}
                    for value in applied
                ]
        decisions = self._decisions()
        history = [
            self._decision_detail(value)
            for value in decisions
            if value["review_id"] == review_id
        ]
        return {
            "review_id": review_id,
            "queue": item["queue"],
            "source": item["source"],
            "stratum": item["stratum"],
            "unit_type": item["unit_type"],
            "flags": item["flags"],
            "records": corrected_records,
            "raw_records": records,
            "corrections": corrections,
            "decision_history": history,
            "sealed": self.sealed_path.exists(),
        }

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        expected_fields = {"review_id", "decision", "reason_code", "private_note"}
        if set(payload) != expected_fields:
            raise ReviewRequestError(400, "요청 필드가 검수 계약과 다릅니다.")
        review_id = payload["review_id"]
        decision = payload["decision"]
        reason_code = payload["reason_code"]
        private_note = payload["private_note"]
        if not isinstance(review_id, str) or REVIEW_ID_PATTERN.fullmatch(review_id) is None:
            raise ReviewRequestError(400, "review_id 형식이 올바르지 않습니다.")
        if not isinstance(decision, str):
            raise ReviewRequestError(400, "decision 형식이 올바르지 않습니다.")
        if reason_code is not None and not isinstance(reason_code, str):
            raise ReviewRequestError(400, "reason_code 형식이 올바르지 않습니다.")
        if private_note is not None and not isinstance(private_note, str):
            raise ReviewRequestError(400, "private_note 형식이 올바르지 않습니다.")
        if isinstance(private_note, str):
            if len(private_note) > MAX_PRIVATE_NOTE_CHARS:
                raise ReviewRequestError(400, "비공개 메모가 2,000자를 넘습니다.")
            if CONTROL_PATTERN.search(private_note):
                raise ReviewRequestError(400, "비공개 메모에 제어 문자가 있습니다.")
            private_note = private_note.strip() or None
        with self._write_lock:
            if self.sealed_path.exists():
                raise ReviewRequestError(423, "봉인된 감사 build는 읽기 전용입니다.")
            try:
                saved = append_review_decision(
                    self.decision_path,
                    self.queue,
                    self.policy,
                    review_id=review_id,
                    decision=decision,
                    reason_code=reason_code,
                    private_note=private_note,
                    reviewer_version=self.identity.reviewer_version,
                    review_tool_sha256=self.identity.review_tool_sha256,
                )
            except Phase2AuditError as exc:
                raise ReviewRequestError(400, str(exc)) from exc
            decisions = self._decisions()
        return {
            "saved": self._decision_detail(saved),
            "status": {
                **audit_status_from_values(self.queue, decisions),
                "sealed": False,
            },
        }


class ReviewHTTPServer(ThreadingHTTPServer):
    """검수 상태와 정적 자산을 보관하는 loopback 전용 서버다."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        state: ReviewState,
        asset_root: Path,
        csrf_token: str,
    ) -> None:
        self.state = state
        self.asset_root = asset_root
        self.csrf_token = csrf_token
        super().__init__(server_address, ReviewRequestHandler)
        host, port = self.server_address
        self.expected_host = f"{host}:{port}"
        self.expected_origin = f"http://{host}:{port}"


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """외부 의존성 없이 정적 UI와 제한된 JSON API만 제공한다."""

    server: ReviewHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
        sys.stderr.write(f"review-web {self.command} {urlsplit(self.path).path}\n")

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        ))
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()

    def _send_bytes(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        *,
        head_only: bool = False,
    ) -> None:
        self._headers(status, content_type, len(payload))
        if not head_only:
            self.wfile.write(payload)

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self.close_connection = True
        self._send_json(status, {"error": message, "status": status})

    def _valid_host(self) -> bool:
        return self.headers.get("Host") == self.server.expected_host

    def _api_authorized(self) -> bool:
        provided = self.headers.get("X-CSRF-Token", "")
        return secrets.compare_digest(provided, self.server.csrf_token)

    def _request_path(self) -> str:
        parsed = urlsplit(self.path)
        if parsed.query or "%" in parsed.path:
            raise ReviewRequestError(400, "쿼리와 인코딩 경로는 지원하지 않습니다.")
        return parsed.path

    def _static_payload(self, path: str) -> tuple[bytes, str]:
        asset = STATIC_TYPES.get(path)
        if asset is None:
            raise ReviewRequestError(404, "요청한 자산을 찾을 수 없습니다.")
        filename, content_type = asset
        target = self.server.asset_root / filename
        try:
            payload = target.read_bytes()
        except OSError as exc:
            raise ReviewRequestError(500, "검수기 자산을 읽을 수 없습니다.") from exc
        if filename == "index.html":
            placeholder = b"__CSRF_TOKEN__"
            if payload.count(placeholder) != 1:
                raise ReviewRequestError(500, "CSRF placeholder가 올바르지 않습니다.")
            payload = payload.replace(placeholder, self.server.csrf_token.encode("ascii"))
        return payload, content_type

    def _guard(self) -> str:
        if not self._valid_host():
            raise ReviewRequestError(421, "허용되지 않은 Host입니다.")
        return self._request_path()

    def do_HEAD(self) -> None:
        try:
            path = self._guard()
            if path.startswith("/api/"):
                raise ReviewRequestError(405, "API HEAD 요청은 지원하지 않습니다.")
            payload, content_type = self._static_payload(path)
            self._send_bytes(200, payload, content_type, head_only=True)
        except ReviewRequestError as exc:
            self._error(exc.status, str(exc))

    def do_GET(self) -> None:
        try:
            path = self._guard()
            if path.startswith("/api/"):
                if not self._api_authorized():
                    raise ReviewRequestError(403, "CSRF 검증에 실패했습니다.")
                if path == "/api/bootstrap":
                    self._send_json(200, self.server.state.bootstrap())
                    return
                match = re.fullmatch(r"/api/items/([0-9a-f]{24})", path)
                if match:
                    self._send_json(200, self.server.state.item(match.group(1)))
                    return
                raise ReviewRequestError(404, "API 경로를 찾을 수 없습니다.")
            payload, content_type = self._static_payload(path)
            self._send_bytes(200, payload, content_type)
        except ReviewRequestError as exc:
            self._error(exc.status, str(exc))
        except Phase2AuditError:
            self._error(500, "감사 원문 또는 결정 무결성 검증에 실패했습니다.")

    def _json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip()
        if content_type != "application/json":
            raise ReviewRequestError(415, "application/json 요청만 허용됩니다.")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ReviewRequestError(411, "Content-Length가 필요합니다.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ReviewRequestError(400, "Content-Length가 올바르지 않습니다.") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ReviewRequestError(413, "요청 본문 크기가 허용 범위를 벗어났습니다.")
        try:
            value = json.loads(self.rfile.read(length))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ReviewRequestError(400, "JSON 요청 본문이 올바르지 않습니다.") from exc
        if not isinstance(value, dict):
            raise ReviewRequestError(400, "JSON object 요청만 허용됩니다.")
        return value

    def do_POST(self) -> None:
        try:
            path = self._guard()
            if path != "/api/decisions":
                raise ReviewRequestError(404, "API 경로를 찾을 수 없습니다.")
            if not self._api_authorized():
                raise ReviewRequestError(403, "CSRF 검증에 실패했습니다.")
            if self.headers.get("Origin") != self.server.expected_origin:
                raise ReviewRequestError(403, "Origin 검증에 실패했습니다.")
            result = self.server.state.append(self._json_body())
            self._send_json(201, result)
        except ReviewRequestError as exc:
            self._error(exc.status, str(exc))
        except Phase2AuditError:
            self._error(500, "검토 결정 무결성 검증에 실패했습니다.")

    def do_OPTIONS(self) -> None:
        self._error(405, "OPTIONS 요청은 지원하지 않습니다.")


def create_server(
    state: ReviewState,
    asset_root: Path,
    *,
    port: int,
    csrf_token: str | None = None,
) -> ReviewHTTPServer:
    if port < 0 or port > 65_535:
        raise Phase2AuditError("--port는 0~65535 범위여야 합니다.")
    return ReviewHTTPServer(
        ("127.0.0.1", port),
        state,
        asset_root.resolve(),
        csrf_token or secrets.token_urlsafe(32),
    )


def reviewer_root(context: dict[str, Any]) -> Path:
    policy = context["policy"]
    return (
        REPO_ROOT
        / "data"
        / "reports"
        / policy["dataset_name"]
        / "audit-review"
        / policy["audit_version"]
        / context["identity"]["build_id"]
        / REVIEWER_VERSION
    )


def _review_tool_hash(asset_root: Path) -> str:
    entries = [
        {
            "name": "scripts/data/phase2_review_web.py",
            "sha256": sha256_file(Path(__file__)),
        }
    ]
    for name in ("index.html", "review.css", "review.js"):
        entries.append({"name": name, "sha256": sha256_file(asset_root / name)})
    return sha256_json(entries)


def validate_reviewer_manifest(
    asset_root: Path, context: dict[str, Any]
) -> dict[str, Any]:
    manifest_path = asset_root / "review_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase2AuditError("reviewer manifest를 읽을 수 없습니다.") from exc
    identity = context["identity"]
    policy = context["policy"]
    expected_identity = {
        "schema_version": "1.0.0",
        "contains_raw_samples": False,
        "dataset_name": policy["dataset_name"],
        "audit_version": policy["audit_version"],
        "build_id": identity["build_id"],
        "build_sha256": identity["build_sha256"],
        "reviewer_version": REVIEWER_VERSION,
        "entrypoint": "index.html",
        "server_entrypoint": "scripts/data/phase2_review_web.py",
    }
    for key, expected in expected_identity.items():
        if manifest.get(key) != expected:
            raise Phase2AuditError(f"reviewer manifest identity가 다릅니다: {key}")
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != {
        "index.html",
        "review.css",
        "review.js",
    }:
        raise Phase2AuditError("reviewer artifact 목록이 올바르지 않습니다.")
    for name, expected in hashes.items():
        path = asset_root / name
        if not path.is_file() or sha256_file(path) != expected:
            raise Phase2AuditError(f"reviewer artifact hash가 다릅니다: {name}")
    if manifest.get("server_entrypoint_sha256") != sha256_file(Path(__file__)):
        raise Phase2AuditError("reviewer server hash가 다릅니다.")
    if manifest.get("review_tool_sha256") != _review_tool_hash(asset_root):
        raise Phase2AuditError("reviewer tool fingerprint가 다릅니다.")
    audit_manifest = context["paths"]["public"] / "build_manifest.json"
    if (
        not audit_manifest.is_file()
        or manifest.get("audit_build_manifest_sha256") != sha256_file(audit_manifest)
    ):
        raise Phase2AuditError("reviewer가 참조하는 audit manifest가 다릅니다.")
    return manifest


def build_state(
    source_config_path: Path,
    policy_path: Path,
    audit_version: str,
    requested_build: str,
) -> tuple[ReviewState, Path]:
    context = prepare_audit(
        REPO_ROOT,
        source_config_path,
        policy_path,
        audit_version,
        verify_raw=False,
    )
    if requested_build != context["identity"]["build_id"]:
        raise Phase2AuditError("요청한 --build가 현재 코드·정책 fingerprint와 다릅니다.")
    verify_audit(
        REPO_ROOT,
        source_config_path,
        policy_path,
        audit_version,
        verify_raw=False,
    )
    assets = reviewer_root(context)
    manifest = validate_reviewer_manifest(assets, context)
    values = _load_build_files(context)
    _validate_decisions(values["decisions"], context["policy"])
    identity = ReviewIdentity(
        dataset_name=context["policy"]["dataset_name"],
        audit_version=audit_version,
        build_id=context["identity"]["build_id"],
        build_sha256=context["identity"]["build_sha256"],
        reviewer_version=REVIEWER_VERSION,
        review_tool_sha256=manifest["review_tool_sha256"],
    )
    state = ReviewState(
        identity=identity,
        queue=values["queue"],
        policy=context["policy"],
        decision_path=context["paths"]["private"] / "decisions.jsonl",
        sealed_path=context["paths"]["private"] / "SEALED.json",
        raw_loader=lambda locator: _load_raw_record(REPO_ROOT, locator),
        correction_manifest=context["correction_manifest"],
        blocking_findings=values["aggregate"].get("blocking_finding_codes", []),
        resolved_findings=values["aggregate"].get("resolved_finding_codes", []),
    )
    return state, assets


def _open_browser(url: str) -> None:
    wslview = shutil.which("wslview")
    explorer = Path("/mnt/c/WINDOWS/explorer.exe")
    try:
        if wslview:
            subprocess.Popen(
                [wslview, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        if explorer.is_file():
            subprocess.Popen(
                [str(explorer), url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        webbrowser.open(url, new=1)
    except OSError:
        return


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 2A 감사 큐 301건을 검토하는 loopback 전용 HTML UI"
    )
    parser.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--audit-version", default="v1.1.0")
    parser.add_argument("--build", required=True, help="고정된 audit build ID")
    parser.add_argument("--port", type=int, default=0, help="0이면 빈 포트를 자동 선택")
    parser.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 생략")
    return parser


def run(arguments: argparse.Namespace) -> int:
    source_config = arguments.source_config.expanduser().resolve()
    policy = arguments.policy.expanduser().resolve()
    state, assets = build_state(
        source_config, policy, arguments.audit_version, arguments.build
    )
    server = create_server(state, assets, port=arguments.port)
    url = server.expected_origin + "/"
    print(
        json.dumps(
            {
                "audit_version": state.identity.audit_version,
                "build_id": state.identity.build_id,
                "reviewer_version": state.identity.reviewer_version,
                "url": url,
                "sealed": state.sealed_path.exists(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not arguments.no_open:
        _open_browser(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        return run(arguments)
    except (Phase1Error, Phase2AuditError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
