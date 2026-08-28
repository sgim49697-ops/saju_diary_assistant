# phase2b_review_web.py - 24K staging의 BaZi·YEJI 300건을 loopback에서 검수한다.

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.errors import Phase2AuditError
from scripts.data.phase2b_verify_history import verify_historical_staging
from scripts.data.preprocess_tools import verify_staging

DEFAULT_CONFIG = (
    REPO_ROOT
    / "configs/data_versions/saju_1b_baseline/preprocessing-staging-v0.1.0.json"
)
ALLOWED_DECISIONS = {"accept", "exclude_candidate", "needs_fix", "uncertain"}
MAX_BODY_BYTES = 16 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Phase2AuditError(f"JSON object가 아닙니다: {path}")
    return value


def _load_latest_decisions(path: Path, allowed_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise Phase2AuditError(f"빈 decision 행이 있습니다: {line_number}")
            item = json.loads(line)
            if not isinstance(item, dict) or item.get("id") not in allowed_ids:
                raise Phase2AuditError("staging decision ledger에 알 수 없는 ID가 있습니다.")
            result[item["id"]] = item
    return result


def _load_selected_records(
    private_path: Path, selection: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    selected_ids = {
        item for values in selection["record_ids"].values() for item in values
    }
    records: dict[str, dict[str, Any]] = {}
    for axis in ("bazi_sft", "yeji_shensha_derived"):
        with (private_path / f"records/{axis}.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                item = json.loads(line)
                if item["id"] in selected_ids:
                    records[item["id"]] = item
    if set(records) != selected_ids:
        raise Phase2AuditError("검수 selection에 대응하는 staging record가 없습니다.")
    return records


class ReviewState:
    def __init__(self, verified: dict[str, Any], host: str, port: int) -> None:
        self.verified = verified
        self.private_path = REPO_ROOT / verified["private_path"]
        self.public_path = REPO_ROOT / verified["public_path"]
        self.static_path = self.public_path / "reviewer-v1.0.0"
        self.selection = _load_json(self.private_path / "review_selection.json")
        self.records = _load_selected_records(self.private_path, self.selection)
        self.allowed_ids = set(self.records)
        self.decisions_path = self.private_path / "review_decisions.jsonl"
        self.decisions = _load_latest_decisions(self.decisions_path, self.allowed_ids)
        self.csrf_token = secrets.token_urlsafe(32)
        self.allowed_hosts = {f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def bootstrap(self) -> dict[str, Any]:
        items = []
        for axis in ("bazi_sft", "yeji_shensha_derived"):
            for record_id in self.selection["record_ids"][axis]:
                record = self.records[record_id]
                title = (
                    f"{record['meta']['question_type']} · {record_id[-8:]}"
                    if axis == "bazi_sft"
                    else f"{record['meta']['rule_id']:02d} {record['meta']['rule_name_ko']} · {record['meta']['case_type']}"
                )
                items.append(
                    {
                        "id": record_id,
                        "axis": axis,
                        "title": title,
                        "decision": self.decisions.get(record_id, {}).get("decision"),
                    }
                )
        return {
            "schema_version": "1.0.0",
            "staging_version": self.verified["staging_version"],
            "build_id": self.verified["build_id"],
            "approval_status": self.verified["approval_status"],
            "read_only": self.verified["approval_status"] != "staging_unapproved",
            "csrf_token": self.csrf_token,
            "items": items,
        }

    def projected_record(self, record_id: str) -> dict[str, Any]:
        record = self.records[record_id]
        meta = record["meta"]
        if record["mix_axis"] == "bazi_sft":
            review_title = f"BaZi · {meta['question_type']}"
            review_meta = {
                "질문 유형": meta["question_type"],
                "검증 규칙": meta["validated_rule_ids"],
                "원천 split": meta["upstream_splits"],
                "명식 식별": meta["chart_signature"],
            }
        else:
            review_title = f"YEJI {meta['rule_id']:02d} · {meta['rule_name_ko']}"
            review_meta = {
                "사례 유형": meta["case_type"],
                "검증 결과": meta["evaluator_outcome"],
                "검증 상태": meta["evaluator_status"],
                "교정 ID": meta["correction_ids"] or ["없음"],
                "명식 식별": meta["chart_signature"],
            }
        return {
            "id": record_id,
            "mix_axis": record["mix_axis"],
            "review_title": review_title,
            "messages": record["messages"],
            "review_meta": review_meta,
            "current_decision": self.decisions.get(record_id),
        }

    def save_decision(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.verified["approval_status"] != "staging_unapproved":
            raise ValueError("승인이 완료된 staging build는 판정을 수정할 수 없습니다.")
        record_id = value.get("id")
        decision = value.get("decision")
        note = value.get("note", "")
        if record_id not in self.allowed_ids:
            raise ValueError("검수 대상 ID가 아닙니다.")
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("지원하지 않는 판정입니다.")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("메모는 500자 이하여야 합니다.")
        previous = self.decisions.get(record_id)
        revision = int(previous.get("revision", 0)) + 1 if previous else 1
        item = {
            "schema_version": "1.0.0",
            "decision_id": hashlib.sha256(
                f"{self.verified['build_id']}|{record_id}|{revision}|{decision}|{note}".encode()
            ).hexdigest(),
            "id": record_id,
            "decision": decision,
            "note": note.strip(),
            "revision": revision,
            "previous_decision_id": previous.get("decision_id") if previous else None,
            "recorded_at": _utc_now(),
        }
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(
            self.decisions_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8", closefd=True) as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        self.decisions[record_id] = item
        return item


def make_handler(state: ReviewState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "SajuStagingReview/1.0"

        def _security_headers(self, content_type: str) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'none'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in state.allowed_hosts

        def _json(self, status: HTTPStatus, value: Any) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._security_headers("application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json(status, {"error": message})

        def do_GET(self) -> None:
            if not self._host_ok():
                self._error(HTTPStatus.BAD_REQUEST, "허용하지 않는 Host입니다.")
                return
            parsed = urlparse(self.path)
            if parsed.path == "/api/bootstrap":
                self._json(HTTPStatus.OK, state.bootstrap())
                return
            if parsed.path == "/api/record":
                record_id = parse_qs(parsed.query).get("id", [""])[0]
                if record_id not in state.allowed_ids:
                    self._error(HTTPStatus.NOT_FOUND, "검수 항목이 없습니다.")
                    return
                self._json(HTTPStatus.OK, state.projected_record(record_id))
                return
            static_name = "index.html" if parsed.path in {"/", "/index.html"} else parsed.path.lstrip("/")
            if static_name not in {"index.html", "review.css", "review.js"}:
                self._error(HTTPStatus.NOT_FOUND, "파일이 없습니다.")
                return
            path = state.static_path / static_name
            mime = "text/html; charset=utf-8" if static_name.endswith(".html") else "text/css; charset=utf-8" if static_name.endswith(".css") else "text/javascript; charset=utf-8"
            payload = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self._security_headers(mime)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            if not self._host_ok() or self.path != "/api/decision":
                self._error(HTTPStatus.BAD_REQUEST, "허용하지 않는 요청입니다.")
                return
            origin = self.headers.get("Origin")
            if origin not in state.allowed_origins:
                self._error(HTTPStatus.FORBIDDEN, "Origin이 올바르지 않습니다.")
                return
            if self.headers.get("X-CSRF-Token") != state.csrf_token:
                self._error(HTTPStatus.FORBIDDEN, "CSRF token이 올바르지 않습니다.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "Content-Length가 올바르지 않습니다.")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "요청 본문 크기가 올바르지 않습니다.")
                return
            try:
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise TypeError("JSON object가 필요합니다.")
                saved = state.save_decision(value)
            except (json.JSONDecodeError, TypeError, UnicodeError, ValueError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json(HTTPStatus.OK, saved)

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write(f"[staging-review] {format % args}\n")

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="MIX20K staging 검수 웹 서버")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--build", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    arguments = parser.parse_args()
    if arguments.host != "127.0.0.1":
        parser.error("검수 서버는 127.0.0.1에만 bind할 수 있습니다.")
    try:
        config_path = arguments.config.expanduser().resolve()
        try:
            verified = verify_staging(REPO_ROOT, config_path, arguments.build)
        except Phase2AuditError as current_error:
            staging_version = _load_json(config_path).get("staging_version")
            if not isinstance(staging_version, str) or not staging_version:
                raise
            try:
                verified = verify_historical_staging(
                    REPO_ROOT,
                    staging_version=staging_version,
                    build_id=arguments.build,
                    implementation_commit=None,
                )
            except Phase2AuditError:
                raise current_error
        state = ReviewState(verified, arguments.host, arguments.port)
    except (OSError, UnicodeError, json.JSONDecodeError, Phase2AuditError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer(
        (arguments.host, arguments.port), make_handler(state)
    )
    print(
        json.dumps(
            {
                "url": f"http://{arguments.host}:{arguments.port}/",
                "build_id": arguments.build,
                "review_count": len(state.records),
                "decision_path": state.decisions_path.relative_to(REPO_ROOT).as_posix(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
