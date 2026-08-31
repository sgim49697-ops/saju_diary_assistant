# iers_finals_collector.py - IERS finals2000A 원문을 재현 가능한 비공개 snapshot으로 수집한다.

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.evaluation.saju_runtime.strict_json import (
    DuplicateJsonKeyError,
    loads_without_duplicate_keys,
)
from scripts.runtime.calculation.contracts import REPO_ROOT

COLLECTOR_VERSION = "iers-finals2000a-collector-v1.0.0"
CONFIRMATION = "COLLECT_IERS_FINALS2000A_V1_0_0"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/iers/v1.0.0"
DOWNLOAD_ENDPOINT = (
    "https://datacenter.iers.org/products/eop/rapid/standard/finals2000A.all"
)
RESPONSE_FILENAME = "finals2000A.all"
MANIFEST_FILENAME = "iers_finals2000a_manifest.json"
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MIN_PARSED_ROWS = 19_000
MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)


class IersFinalsCollectorError(RuntimeError):
    """IERS snapshot 수집·검증·provenance 계약 위반."""


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        resolved = urllib.parse.urljoin(request.full_url, new_url)
        before = urllib.parse.urlsplit(request.full_url)
        after = urllib.parse.urlsplit(resolved)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise IersFinalsCollectorError(
                "IERS finals2000A의 다른 origin redirect를 허용하지 않습니다."
            )
        return super().redirect_request(
            request, file_pointer, code, message, headers, resolved
        )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise IersFinalsCollectorError("hash 대상이 일반 파일이 아닙니다.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise IersFinalsCollectorError("hash 대상을 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _safe_output(directory: Path) -> Path:
    unresolved = directory.absolute()
    root = ALLOWED_ROOT.absolute()
    try:
        relative = unresolved.relative_to(root)
    except ValueError as exc:
        raise IersFinalsCollectorError(
            f"출력은 {ALLOWED_ROOT} 아래여야 합니다."
        ) from exc
    current = REPO_ROOT.absolute()
    for part in root.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise IersFinalsCollectorError("IERS raw root에 symlink가 있습니다.")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise IersFinalsCollectorError("IERS 출력 경로에 symlink가 있습니다.")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise IersFinalsCollectorError(
            "IERS 출력 경로가 raw root를 벗어납니다."
        ) from exc
    return resolved


def _private_bytes(path: Path, maximum: int = MAX_DOWNLOAD_BYTES) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= maximum
        ):
            raise IersFinalsCollectorError(
                "IERS snapshot의 소유자·권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise IersFinalsCollectorError("IERS snapshot을 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise IersFinalsCollectorError("IERS snapshot 크기가 제한을 넘습니다.")
    return payload


def _write_exclusive(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise IersFinalsCollectorError(f"기존 IERS 파일이 있습니다: {path.name}")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def parse_snapshot(payload: bytes) -> dict[str, Any]:
    if not 1 <= len(payload) <= MAX_DOWNLOAD_BYTES or b"\x00" in payload:
        raise IersFinalsCollectorError("IERS 원문의 크기·내용이 올바르지 않습니다.")
    try:
        from skyfield.data import iers

        mjd, dut1 = iers.parse_dut1_from_finals_all(io.BytesIO(payload))
    except (ImportError, OSError, ValueError) as exc:
        raise IersFinalsCollectorError(
            "IERS finals2000A를 파싱하지 못했습니다."
        ) from exc
    if (
        len(mjd) != len(dut1)
        or len(mjd) < MIN_PARSED_ROWS
        or any(not math.isfinite(float(value)) for value in mjd)
        or any(not math.isfinite(float(value)) for value in dut1)
        or any(
            float(current) >= float(following) for current, following in pairwise(mjd)
        )
        or float(mjd[0]) > 41_684.0
        or float(mjd[-1]) < 61_000.0
    ):
        raise IersFinalsCollectorError("IERS finals2000A coverage·순서가 다릅니다.")
    start = MJD_EPOCH + timedelta(days=float(mjd[0]))
    end = MJD_EPOCH + timedelta(days=float(mjd[-1]))
    return {
        "rows": len(mjd),
        "mjd_start": float(mjd[0]),
        "mjd_end": float(mjd[-1]),
        "utc_date_start": start.date().isoformat(),
        "utc_date_end": end.date().isoformat(),
        "dut1_start_seconds": round(float(dut1[0]), 7),
        "dut1_end_seconds": round(float(dut1[-1]), 7),
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = loads_without_duplicate_keys(_private_bytes(path, 1024 * 1024))
    except (UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise IersFinalsCollectorError("IERS manifest를 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise IersFinalsCollectorError("IERS manifest 최상위가 object가 아닙니다.")
    return value


def _existing_manifest(directory: Path) -> dict[str, Any] | None:
    response_path = directory / RESPONSE_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    present = [
        path.exists() or path.is_symlink() for path in (response_path, manifest_path)
    ]
    if not any(present):
        return None
    if not all(present):
        raise IersFinalsCollectorError("IERS 산출물이 일부만 존재합니다.")
    payload = _private_bytes(response_path)
    manifest = _load_manifest(manifest_path)
    parsed = parse_snapshot(payload)
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "complete"
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != _sha256_file(Path(__file__))
        or manifest.get("download_endpoint") != DOWNLOAD_ENDPOINT
        or manifest.get("artifact")
        != {
            "filename": RESPONSE_FILENAME,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        }
        or manifest.get("parsed") != parsed
        or manifest.get("credential_used") is not False
        or manifest.get("automatic_fallback_used") is not False
    ):
        raise IersFinalsCollectorError("기존 IERS provenance가 다릅니다.")
    try:
        collected_at = datetime.fromisoformat(manifest["collected_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise IersFinalsCollectorError("IERS 수집 시각이 다릅니다.") from exc
    if collected_at.tzinfo != timezone.utc:
        raise IersFinalsCollectorError("IERS 수집 시각이 UTC가 아닙니다.")
    return manifest


def _download(timeout: float) -> bytes:
    opener = urllib.request.build_opener(_SameOriginRedirect())
    request = urllib.request.Request(
        DOWNLOAD_ENDPOINT,
        headers={
            "Accept": "text/plain,application/octet-stream;q=0.9",
            "User-Agent": "saju-runtime-iers-finals2000a/1.0",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if (
                response.status != 200
                or (declared is not None and int(declared) > MAX_DOWNLOAD_BYTES)
                or response.geturl() != DOWNLOAD_ENDPOINT
            ):
                raise IersFinalsCollectorError(
                    "IERS 응답 status·크기·최종 URL이 다릅니다."
                )
            payload = response.read(MAX_DOWNLOAD_BYTES + 1)
    except IersFinalsCollectorError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise IersFinalsCollectorError("IERS 다운로드에 실패했습니다.") from exc
    if not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise IersFinalsCollectorError("IERS 응답 크기가 올바르지 않습니다.")
    return payload


def collect(*, output: Path, timeout: float, confirmation: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise IersFinalsCollectorError(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    if not 0 < timeout <= 300:
        raise IersFinalsCollectorError("timeout이 올바르지 않습니다.")
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    existing = _existing_manifest(directory)
    if existing is not None:
        return existing
    payload = _download(timeout)
    parsed = parse_snapshot(payload)
    manifest = {
        "schema_version": "1.0.0",
        "status": "complete",
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _sha256_file(Path(__file__)),
        "download_endpoint": DOWNLOAD_ENDPOINT,
        "artifact": {
            "filename": RESPONSE_FILENAME,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        },
        "parsed": parsed,
        "credential_used": False,
        "automatic_fallback_used": False,
        "collected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_exclusive(directory / RESPONSE_FILENAME, payload)
    _write_exclusive(directory / MANIFEST_FILENAME, manifest_payload)
    return manifest


def collection_plan() -> dict[str, Any]:
    return {
        "status": "network_not_started",
        "collector_version": COLLECTOR_VERSION,
        "download_endpoint": DOWNLOAD_ENDPOINT,
        "minimum_parsed_rows": MIN_PARSED_ROWS,
        "credential_required": False,
        "automatic_fallback_used": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IERS finals2000A snapshot 수집기")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--confirm-network", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            collection_plan()
            if args.command == "plan"
            else collect(
                output=args.output,
                timeout=args.timeout,
                confirmation=args.confirm_network,
            )
        )
    except (IersFinalsCollectorError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
