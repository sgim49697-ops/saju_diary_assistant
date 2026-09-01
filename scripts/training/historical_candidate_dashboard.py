# historical_candidate_dashboard.py - 과거 공식 근거 runtime 후보를 별도 loopback 화면에서 진단한다.

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scripts.runtime.calculation.engine_v1_3 import SajuRuntimeEngineV13
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.calculation.id_signer import RuntimeIdSigner
from scripts.runtime.calculation.skyfield_solar_terms import (
    DE440S_SHA256,
    OFFICIAL_SNAPSHOT_COLLECTED_AT,
)
from scripts.runtime.intake_contracts_v1_2 import validate_intake_registry_v1_2
from scripts.runtime.intake_fsm import IntakeFsmError
from scripts.runtime.intake_fsm_v1_2 import (
    CANDIDATE_SCOPE,
    advance_intake,
    assert_public_event,
    empty_intake_state,
)

CANDIDATE_ASSET_ROOT = (
    Path(__file__).with_name("phase5_dashboard_candidate_assets") / "v1.0.0"
)
CANDIDATE_STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/candidate.css": ("candidate.css", "text/css; charset=utf-8"),
    "/candidate.js": ("candidate.js", "text/javascript; charset=utf-8"),
}
CANDIDATE_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
CANDIDATE_SESSION_PATH = re.compile(
    r"^/api/runtime/historical-candidate/sessions/([0-9a-f]{24})/events$"
)
CANDIDATE_MAX_SESSIONS = 100
CANDIDATE_IDLE_SECONDS = 30 * 60
CANDIDATE_MAX_REQUEST_BYTES = 16 * 1024
CANDIDATE_RUNTIME_STATUS = {
    "candidate_runtime_enabled": True,
    "candidate_id_key_ready": True,
    "candidate_fsm_gate_passed": True,
    "ephemeris_ready": True,
    "loopback_only": True,
    "ephemeral_session_store": True,
}


class CandidateDashboardError(RuntimeError):
    """후보 진단 서버의 계약·환경 오류."""


class CandidateRequestError(RuntimeError):
    """외부 요청에 노출 가능한 HTTP 오류."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class CandidateSessionStore:
    """raw 출생 state를 disk에 쓰지 않는 크기·유휴시간 제한 메모리 저장소."""

    def __init__(
        self,
        *,
        maximum: int = CANDIDATE_MAX_SESSIONS,
        idle_seconds: int = CANDIDATE_IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if maximum != CANDIDATE_MAX_SESSIONS or idle_seconds != CANDIDATE_IDLE_SECONDS:
            raise CandidateDashboardError("candidate session 제한은 100개·30분으로 고정됩니다.")
        self.maximum = maximum
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._sessions: dict[str, dict[str, Any]] = {}

    def _expire(self) -> None:
        now = self._clock()
        expired = [
            session_id
            for session_id, record in self._sessions.items()
            if now - record["last_access"] >= self.idle_seconds
        ]
        for session_id in expired:
            del self._sessions[session_id]

    def create(self) -> tuple[str, dict[str, Any]]:
        self._expire()
        if len(self._sessions) >= self.maximum:
            raise CandidateRequestError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "candidate 메모리 세션 한도에 도달했습니다.",
            )
        session_id = secrets.token_hex(12)
        while session_id in self._sessions:
            session_id = secrets.token_hex(12)
        now = self._clock()
        state = empty_intake_state()
        self._sessions[session_id] = {
            "state": deepcopy(state),
            "created": now,
            "last_access": now,
        }
        return session_id, state

    def read(self, session_id: str) -> dict[str, Any]:
        self._expire()
        if CANDIDATE_SESSION_ID_PATTERN.fullmatch(session_id) is None:
            raise CandidateRequestError(HTTPStatus.NOT_FOUND, "candidate 세션을 찾을 수 없습니다.")
        record = self._sessions.get(session_id)
        if record is None:
            raise CandidateRequestError(HTTPStatus.NOT_FOUND, "candidate 세션을 찾을 수 없습니다.")
        record["last_access"] = self._clock()
        return deepcopy(record["state"])

    def update(self, session_id: str, state: Mapping[str, Any]) -> None:
        record = self._sessions.get(session_id)
        if record is None:
            raise CandidateRequestError(HTTPStatus.NOT_FOUND, "candidate 세션을 찾을 수 없습니다.")
        record["state"] = deepcopy(dict(state))
        record["last_access"] = self._clock()

    def count(self) -> int:
        self._expire()
        return len(self._sessions)


def _public_hard_facts(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    allowed = {
        "pillars",
        "day_master",
        "surface_five_elements",
        "calculation_profile",
        "solar_term_evidence",
    }
    return {key: deepcopy(item) for key, item in value.items() if key in allowed}


def _public_transition(transition: Mapping[str, Any]) -> dict[str, Any]:
    state = transition.get("session_state")
    decision = transition.get("decision")
    if not isinstance(state, Mapping) or not isinstance(decision, Mapping):
        raise CandidateDashboardError("candidate FSM 전이 결과가 다릅니다.")
    action = decision.get("action")
    if action == "render_candidate":
        status = "candidate_ready"
    elif action == "explain_candidate_blocked":
        status = "blocked"
    else:
        status = "needs_input"
    public_decision = {
        key: deepcopy(decision[key])
        for key in ("action", "message", "reason_code", "candidate_scope")
        if key in decision and decision[key] is not None
    }
    result = None
    payload = decision.get("payload")
    if action == "render_candidate" and isinstance(payload, Mapping):
        facts = _public_hard_facts(payload.get("hard_facts"))
        if facts is None or payload.get("fact_authority") != "HARD_CANDIDATE":
            raise CandidateDashboardError("candidate 공개 결과 권한이 다릅니다.")
        result = {"fact_authority": "HARD_CANDIDATE", "hard_facts": facts}
    response = {
        "status": status,
        "state_revision": state.get("state_revision"),
        "decision": public_decision,
        "result": result,
        "governance": {
            "candidate_scope": CANDIDATE_SCOPE,
            "runtime_release_approved": False,
            "production_application_binding": False,
            "model_context_binding": False,
            "period_calculation_allowed": False,
            "disk_persistence": False,
        },
    }
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
    forbidden = (
        "normalized_input",
        "birth_input_id",
        "chart_id",
        "chart_set_id",
        "calculation_run_id",
        "internal_trace",
        "local_birth_time",
        "local_birth_date",
    )
    if any(item in encoded for item in forbidden):
        raise CandidateDashboardError("candidate 공개 응답에 내부·출생 식별 정보가 포함됐습니다.")
    return response


def candidate_status_payload(server: HistoricalCandidateDashboardServer) -> dict[str, Any]:
    with server.candidate_lock:
        active_sessions = server.candidate_store.count()
    return {
        "status": "available",
        "dashboard_version": "saju-historical-candidate-dashboard-v1.0.0",
        "fsm_version": "saju-intake-fsm-v1.2.0",
        "session_schema_version": "saju-session-state-v2.2",
        "engine_version": "saju-runtime-python-v1.3.0",
        "candidate_scope": CANDIDATE_SCOPE,
        "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
        "ephemeris_sha256": DE440S_SHA256,
        "active_sessions": active_sessions,
        "session_limit": CANDIDATE_MAX_SESSIONS,
        "idle_timeout_seconds": CANDIDATE_IDLE_SECONDS,
        "loopback_only": True,
        "disk_persistence": False,
        "model_context_binding": False,
        "period_calculation_allowed": False,
        "runtime_release_approved": False,
        "production_application_binding": False,
        "context_window_changed": False,
        "sealed_blind_accessed": False,
    }


def execute_candidate_event(
    server: HistoricalCandidateDashboardServer,
    session_id: str,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    assert_public_event(event)
    with server.candidate_lock:
        state = server.candidate_store.read(session_id)
        transition = advance_intake(
            state,
            event,
            server.signer,
            CANDIDATE_RUNTIME_STATUS,
        )
        decision = transition["decision"]
        if decision["action"] == "call_candidate_chart":
            internal = server.engine.calculate_chart(decision["arguments"])
            transition = advance_intake(
                transition["session_state"],
                {
                    "type": "chart_result",
                    "result": {**internal, "call_id": decision["call_id"]},
                },
                server.signer,
                CANDIDATE_RUNTIME_STATUS,
            )
        server.candidate_store.update(session_id, transition["session_state"])
        return _public_transition(transition)


class HistoricalCandidateDashboardServer(ThreadingHTTPServer):
    """모델·기존 dashboard context와 분리된 후보 runtime 서버."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        engine: Any,
        signer: RuntimeIdSigner,
        asset_root: Path = CANDIDATE_ASSET_ROOT,
        csrf_token: str | None = None,
        store: CandidateSessionStore | None = None,
    ) -> None:
        host = address[0]
        if host != "127.0.0.1":
            raise CandidateDashboardError("candidate dashboard는 127.0.0.1에만 열 수 있습니다.")
        if asset_root.is_symlink() or not asset_root.is_dir():
            raise CandidateDashboardError("candidate asset root가 없거나 symlink입니다.")
        self.engine = engine
        self.signer = signer
        self.asset_root = asset_root
        self.csrf_token = csrf_token or secrets.token_hex(24)
        self.candidate_store = store or CandidateSessionStore()
        self.candidate_lock = threading.Lock()
        super().__init__(address, HistoricalCandidateRequestHandler)
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{value}" for value in self.allowed_hosts}

    def server_close(self) -> None:
        try:
            close = getattr(self.engine, "close", None)
            if callable(close):
                close()
        finally:
            super().server_close()


class HistoricalCandidateRequestHandler(BaseHTTPRequestHandler):
    """후보 전용 정적 화면과 최소 session event API를 제공한다."""

    server: HistoricalCandidateDashboardServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        del format, args
        path = CANDIDATE_SESSION_PATH.sub(
            "/api/runtime/historical-candidate/sessions/<redacted>/events",
            urlsplit(self.path).path,
        )
        sys.stderr.write(f"historical-candidate {self.command} {path}\n")

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self._headers(status, content_type, len(payload))
        self.wfile.write(payload)

    def _send_json(self, status: int, value: Any) -> None:
        payload = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self.close_connection = True
        self._send_json(status, {"status": status, "error": message})

    def _guard(self, *, require_origin: bool = False) -> str:
        if self.headers.get("Host") not in self.server.allowed_hosts:
            raise CandidateRequestError(
                HTTPStatus.MISDIRECTED_REQUEST, "허용되지 않은 Host입니다."
            )
        parsed = urlsplit(self.path)
        if parsed.query or parsed.fragment or "%" in parsed.path:
            raise CandidateRequestError(
                HTTPStatus.BAD_REQUEST, "쿼리·인코딩 경로는 지원하지 않습니다."
            )
        if require_origin and self.headers.get("Origin") not in self.server.allowed_origins:
            raise CandidateRequestError(HTTPStatus.FORBIDDEN, "허용되지 않은 Origin입니다.")
        return parsed.path

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-CSRF-Token", ""), self.server.csrf_token
        )

    @staticmethod
    def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def _request_json(self) -> dict[str, Any]:
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise CandidateRequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON 요청만 허용됩니다."
            )
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError as exc:
            raise CandidateRequestError(
                HTTPStatus.BAD_REQUEST, "Content-Length가 잘못됐습니다."
            ) from exc
        if length < 2 or length > CANDIDATE_MAX_REQUEST_BYTES:
            raise CandidateRequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "요청 크기가 허용 범위를 벗어납니다.",
            )
        try:
            value = json.loads(
                self.rfile.read(length), object_pairs_hook=self._strict_object
            )
        except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise CandidateRequestError(
                HTTPStatus.BAD_REQUEST, "JSON 요청이 잘못됐습니다."
            ) from exc
        if not isinstance(value, dict):
            raise CandidateRequestError(HTTPStatus.BAD_REQUEST, "JSON object만 허용됩니다.")
        return value

    def _static(self, path: str) -> tuple[bytes, str]:
        asset = CANDIDATE_STATIC_ASSETS.get(path)
        if asset is None:
            raise CandidateRequestError(HTTPStatus.NOT_FOUND, "정적 자산을 찾을 수 없습니다.")
        filename, content_type = asset
        target = self.server.asset_root / filename
        if target.is_symlink() or not target.is_file():
            raise CandidateDashboardError("candidate 정적 자산이 없습니다.")
        payload = target.read_bytes()
        if filename == "index.html":
            placeholder = b"__CSRF_TOKEN__"
            if payload.count(placeholder) != 1:
                raise CandidateDashboardError("candidate CSRF placeholder가 잘못됐습니다.")
            payload = payload.replace(
                placeholder, self.server.csrf_token.encode("ascii")
            )
        return payload, content_type

    def do_GET(self) -> None:
        try:
            path = self._guard()
            if path.startswith("/api/"):
                if not self._authorized():
                    raise CandidateRequestError(HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다.")
                if path == "/api/runtime/historical-candidate/status":
                    self._send_json(HTTPStatus.OK, candidate_status_payload(self.server))
                    return
                raise CandidateRequestError(HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다.")
            payload, content_type = self._static(path)
            self._send_bytes(HTTPStatus.OK, payload, content_type)
        except CandidateRequestError as exc:
            self._error(exc.status, str(exc))
        except (OSError, CandidateDashboardError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            path = self._guard(require_origin=True)
            if not self._authorized():
                raise CandidateRequestError(HTTPStatus.FORBIDDEN, "CSRF 검증에 실패했습니다.")
            payload = self._request_json()
            if path == "/api/runtime/historical-candidate/sessions":
                if payload:
                    raise CandidateRequestError(
                        HTTPStatus.BAD_REQUEST, "candidate 세션 생성은 빈 object여야 합니다."
                    )
                with self.server.candidate_lock:
                    session_id, state = self.server.candidate_store.create()
                response = _public_transition(
                    advance_intake(
                        state,
                        {"type": "reset"},
                        self.server.signer,
                        CANDIDATE_RUNTIME_STATUS,
                    )
                )
                self._send_json(
                    HTTPStatus.CREATED, {"session_id": session_id, **response}
                )
                return
            match = CANDIDATE_SESSION_PATH.fullmatch(path)
            if match is None:
                raise CandidateRequestError(HTTPStatus.NOT_FOUND, "API 경로를 찾을 수 없습니다.")
            if set(payload) != {"event"} or not isinstance(payload["event"], Mapping):
                raise CandidateRequestError(
                    HTTPStatus.BAD_REQUEST, "candidate event object 하나만 허용됩니다."
                )
            result = execute_candidate_event(
                self.server, match.group(1), payload["event"]
            )
            self._send_json(HTTPStatus.OK, result)
        except CandidateRequestError as exc:
            self._error(exc.status, str(exc))
        except IntakeFsmError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except RuntimeCalculationError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "candidate runtime 계산이 실패했습니다.")
        except (OSError, CandidateDashboardError):
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "candidate 서버 검증이 실패했습니다.")


def prepare_candidate_engine(
    *, ephemeris_path: Path, id_key_file: Path
) -> tuple[SajuRuntimeEngineV13, RuntimeIdSigner]:
    validate_intake_registry_v1_2()
    if not ephemeris_path.is_absolute() or not id_key_file.is_absolute():
        raise CandidateDashboardError("DE440s와 ID key는 절대경로여야 합니다.")
    signer = RuntimeIdSigner.from_key_file(id_key_file)
    engine = SajuRuntimeEngineV13(
        signer=signer,
        enable_candidate_runtime=True,
        ephemeris_path=ephemeris_path,
    )
    return engine, signer


def serve_candidate(
    *,
    ephemeris_path: Path,
    id_key_file: Path,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    if host != "127.0.0.1" or not 1 <= port <= 65535:
        raise CandidateDashboardError(
            "candidate dashboard는 127.0.0.1의 유효한 port에만 열 수 있습니다."
        )
    engine, signer = prepare_candidate_engine(
        ephemeris_path=ephemeris_path,
        id_key_file=id_key_file,
    )
    try:
        server = HistoricalCandidateDashboardServer(
            (host, port), engine=engine, signer=signer
        )
    except Exception:
        engine.close()
        raise
    actual_port = server.server_address[1]
    print(
        f"Historical candidate dashboard: http://127.0.0.1:{actual_port}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
