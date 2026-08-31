# kasi_official_solar_terms_collector.py - KASI 공식 24기 입기 시각 원문과 정규화 snapshot을 수집한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.evaluation.saju_runtime.strict_json import (
    DuplicateJsonKeyError,
    loads_without_duplicate_keys,
)
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH, SOLAR_TERM_NAMES

COLLECTOR_VERSION = "kasi-official-solar-terms-collector-v1.0.0"
CONFIRMATION = "COLLECT_KASI_OFFICIAL_SOLAR_TERMS_V1_0_0"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi/v1.3.0"
SOURCE_PAGE = "https://astro.kasi.re.kr/kor/life/post/almanac"
DOWNLOAD_ENDPOINT = "https://astro.kasi.re.kr/kor/almanac/solarTerms/download"
START_YEAR = 1920
END_YEAR = 2100
EXPECTED_ROWS = (END_YEAR - START_YEAR + 1) * 24
EXPECTED_JIE_ROWS = (END_YEAR - START_YEAR + 1) * 12
KNOWN_UPSTREAM_OMISSIONS = frozenset({(2030, 2)})
MAX_DOWNLOAD_BYTES = 1024 * 1024
MAX_PRIVATE_BYTES = 8 * 1024 * 1024
KST = timezone(timedelta(hours=9))

RESPONSE_FILENAME = "kasi_official_solar_terms_1920_2100.txt"
SNAPSHOT_FILENAME = "kasi_official_solar_terms.jsonl"
MANIFEST_FILENAME = "kasi_official_solar_terms_manifest.json"


class KasiOfficialSolarTermsCollectorError(RuntimeError):
    """KASI 공식 24기 원문 수집·정규화·provenance 계약 위반."""


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        resolved = urllib.parse.urljoin(request.full_url, new_url)
        before = urllib.parse.urlsplit(request.full_url)
        after = urllib.parse.urlsplit(resolved)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise KasiOfficialSolarTermsCollectorError(
                "KASI 24기 다운로드의 다른 origin redirect를 허용하지 않습니다."
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
            raise KasiOfficialSolarTermsCollectorError(
                "hash 대상이 일반 파일이 아닙니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "hash 대상을 읽지 못했습니다."
        ) from exc
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
        raise KasiOfficialSolarTermsCollectorError(
            f"출력은 {ALLOWED_ROOT} 아래여야 합니다."
        ) from exc
    current = REPO_ROOT.absolute()
    for part in root.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise KasiOfficialSolarTermsCollectorError(
                "KASI raw root에 symlink가 있습니다."
            )
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise KasiOfficialSolarTermsCollectorError(
                "출력 경로에 symlink가 있습니다."
            )
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "출력 경로가 raw root를 벗어납니다."
        ) from exc
    return resolved


def _private_bytes(path: Path, maximum: int = MAX_PRIVATE_BYTES) -> bytes:
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
            raise KasiOfficialSolarTermsCollectorError(
                "기존 공식 24기 파일의 소유자·권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 파일을 읽지 못했습니다."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 파일 크기가 제한을 넘습니다."
        )
    return payload


def _write_exclusive(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise KasiOfficialSolarTermsCollectorError(
            f"기존 공식 24기 파일이 있습니다: {path.name}"
        )
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


def _canonical_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )


def parse_download(payload: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not 1 <= len(payload) <= MAX_DOWNLOAD_BYTES or b"\x00" in payload:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문 크기·내용이 올바르지 않습니다."
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문이 UTF-8이 아닙니다."
        ) from exc
    required_notices = (
        "[ 1920년-2100년 24기 입기 시각 ]",
        "반올림 결과 날짜가 바뀌는 경우 날짜 변동 없이 24시 0분으로 표기",
        "최신의 이론, 모델, 상수를 사용하여 계산",
        "과거 데이터의 불확도는 1초 이내",
        "현재 계산 결과: 1950년 대한, 1월 20일 24시 0분",
        "과거 역서 기록: 1950년 대한, 1월 21일 0시 0분",
    )
    if any(notice not in text for notice in required_notices):
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문의 범위·반올림·불확도 고지가 다릅니다."
        )
    pattern = re.compile(
        r"^\s*(\d{1,2})\s*,\s*(\d{4})\s*,\s*(\d{1,2})\s*,"
        r"\s*(\d{1,2})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*$"
    )
    parsed: list[tuple[int, int, int, int, int, int]] = []
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match:
            parsed.append(tuple(map(int, match.groups())))
    expected_identities = {
        (year, kind)
        for year in range(START_YEAR, END_YEAR + 1)
        for kind in range(1, 25)
    }
    identities = [(year, kind) for kind, year, *_ in parsed]
    identity_set = set(identities)
    missing = expected_identities - identity_set
    if (
        len(identity_set) != len(identities)
        or identity_set - expected_identities
        or missing != KNOWN_UPSTREAM_OMISSIONS
        or len(parsed) != EXPECTED_ROWS - len(KNOWN_UPSTREAM_OMISSIONS)
    ):
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문의 identity·알려진 누락이 다릅니다."
        )
    chronological_kinds = [23, 24, *range(1, 23)]
    expected_order = [
        (year, kind)
        for year in range(START_YEAR, END_YEAR + 1)
        for kind in chronological_kinds
        if (year, kind) not in KNOWN_UPSTREAM_OMISSIONS
    ]
    if identities != expected_order:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문의 행 순서가 다릅니다."
        )
    rows: list[dict[str, Any]] = []
    for source_order, (kind, year, month, day, hour, minute) in enumerate(parsed):
        term_index = (kind + 1) % 24
        try:
            printed_date = date(year, month, day)
        except ValueError as exc:
            raise KasiOfficialSolarTermsCollectorError(
                "KASI 공식 24기 원문에 올바르지 않은 날짜가 있습니다."
            ) from exc
        if (
            term_index // 2 + 1 != month
            or not 0 <= hour <= 24
            or not 0 <= minute <= 59
            or (hour == 24 and minute != 0)
        ):
            raise KasiOfficialSolarTermsCollectorError(
                "KASI 공식 24기 원문의 절기 월·시·분이 다릅니다."
            )
        reference = datetime.combine(printed_date, datetime.min.time(), KST) + timedelta(
            hours=hour, minutes=minute
        )
        rows.append(
            {
                "schema_version": "1.0.0",
                "source_id": "kasi_official_solar_terms_download",
                "evidence_class": "SOURCE_HARD_FACT",
                "provider_generated": False,
                "source_order": source_order,
                "year": year,
                "source_kind": kind,
                "term_index": term_index,
                "term_name": SOLAR_TERM_NAMES[term_index],
                "printed_local_date": printed_date.isoformat(),
                "printed_hour": hour,
                "printed_minute": minute,
                "reference_local_minute": reference.isoformat(timespec="minutes"),
                "reference_precision": "minute",
                "reference_timezone": "KST_UTC_PLUS_09_FIXED",
                "rounding_policy": "nearest_minute_half_up_preserve_date_as_24_00",
            }
        )
    jie_rows = [row for row in rows if row["term_index"] in JIE_TO_MONTH]
    if len(jie_rows) != EXPECTED_JIE_ROWS:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 원문의 절입 coverage가 완전하지 않습니다."
        )
    metadata = {
        "source_rows": len(rows),
        "expected_source_rows": EXPECTED_ROWS,
        "known_upstream_omissions": [
            {"year": year, "source_kind": kind}
            for year, kind in sorted(KNOWN_UPSTREAM_OMISSIONS)
        ],
        "jie_rows": len(jie_rows),
        "expected_jie_rows": EXPECTED_JIE_ROWS,
        "jie_coverage_complete": True,
        "source_range": [START_YEAR, END_YEAR],
    }
    return rows, metadata


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = loads_without_duplicate_keys(_private_bytes(path))
    except (UnicodeError, json.JSONDecodeError, DuplicateJsonKeyError) as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 manifest를 읽지 못했습니다."
        ) from exc
    if not isinstance(value, dict):
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 manifest 최상위가 object가 아닙니다."
        )
    return value


def _existing_manifest(directory: Path) -> dict[str, Any] | None:
    response_path = directory / RESPONSE_FILENAME
    snapshot_path = directory / SNAPSHOT_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    paths = (response_path, snapshot_path, manifest_path)
    present = [path.exists() or path.is_symlink() for path in paths]
    if not any(present):
        return None
    if not all(present):
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 산출물이 일부만 존재합니다."
        )
    response_payload = _private_bytes(response_path, MAX_DOWNLOAD_BYTES)
    snapshot_payload = _private_bytes(snapshot_path)
    manifest = _load_json(manifest_path)
    rows, parsed_metadata = parse_download(response_payload)
    expected_snapshot = _canonical_jsonl(rows)
    artifacts = {
        RESPONSE_FILENAME: {
            "bytes": len(response_payload),
            "sha256": _sha256_bytes(response_payload),
        },
        SNAPSHOT_FILENAME: {
            "bytes": len(snapshot_payload),
            "sha256": _sha256_bytes(snapshot_payload),
        },
    }
    if (
        manifest.get("schema_version") != "1.0.0"
        or manifest.get("status") != "complete_available_official_download"
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != _sha256_file(Path(__file__))
        or manifest.get("source_page") != SOURCE_PAGE
        or manifest.get("download_endpoint") != DOWNLOAD_ENDPOINT
        or manifest.get("artifacts") != artifacts
        or manifest.get("parsed") != parsed_metadata
        or manifest.get("credential_used") is not False
        or manifest.get("private_path_recorded") is not False
        or manifest.get("provider_values_used") is not False
        or snapshot_payload != expected_snapshot
    ):
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 provenance가 다릅니다."
        )
    try:
        collected_at = datetime.fromisoformat(manifest["collected_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 수집 시각이 다릅니다."
        ) from exc
    if collected_at.tzinfo != timezone.utc:
        raise KasiOfficialSolarTermsCollectorError(
            "기존 공식 24기 수집 시각이 UTC가 아닙니다."
        )
    return manifest


def _download(timeout: float) -> tuple[bytes, str]:
    opener = urllib.request.build_opener(_SameOriginRedirect())
    request = urllib.request.Request(
        DOWNLOAD_ENDPOINT,
        headers={
            "Accept": "application/octet-stream,text/plain;q=0.9",
            "Referer": SOURCE_PAGE,
            "User-Agent": "saju-runtime-kasi-official-solar-terms/1.0",
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            disposition = response.headers.get("Content-Disposition", "")
            declared = response.headers.get("Content-Length")
            if (
                response.status != 200
                or content_type != "application/octet-stream"
                or "attachment" not in disposition.lower()
                or "1920-2100" not in disposition
                or (declared is not None and int(declared) > MAX_DOWNLOAD_BYTES)
            ):
                raise KasiOfficialSolarTermsCollectorError(
                    "KASI 공식 24기 응답 status·header가 다릅니다."
                )
            payload = response.read(MAX_DOWNLOAD_BYTES + 1)
            final_url = response.geturl()
    except KasiOfficialSolarTermsCollectorError:
        raise
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 다운로드에 실패했습니다."
        ) from exc
    if not payload or len(payload) > MAX_DOWNLOAD_BYTES:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 응답 크기가 올바르지 않습니다."
        )
    if final_url != DOWNLOAD_ENDPOINT:
        raise KasiOfficialSolarTermsCollectorError(
            "KASI 공식 24기 최종 URL이 고정 endpoint와 다릅니다."
        )
    return payload, disposition


def collect(*, output: Path, timeout: float, confirmation: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise KasiOfficialSolarTermsCollectorError(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    if not 0 < timeout <= 300:
        raise KasiOfficialSolarTermsCollectorError("timeout이 올바르지 않습니다.")
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    existing = _existing_manifest(directory)
    if existing is not None:
        return existing
    response_payload, disposition = _download(timeout)
    rows, parsed_metadata = parse_download(response_payload)
    snapshot_payload = _canonical_jsonl(rows)
    artifacts = {
        RESPONSE_FILENAME: {
            "bytes": len(response_payload),
            "sha256": _sha256_bytes(response_payload),
        },
        SNAPSHOT_FILENAME: {
            "bytes": len(snapshot_payload),
            "sha256": _sha256_bytes(snapshot_payload),
        },
    }
    manifest = {
        "schema_version": "1.0.0",
        "status": "complete_available_official_download",
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _sha256_file(Path(__file__)),
        "source_page": SOURCE_PAGE,
        "download_endpoint": DOWNLOAD_ENDPOINT,
        "content_disposition": disposition,
        "artifacts": artifacts,
        "parsed": parsed_metadata,
        "credential_used": False,
        "private_path_recorded": False,
        "provider_values_used": False,
        "collected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_exclusive(directory / RESPONSE_FILENAME, response_payload)
    _write_exclusive(directory / SNAPSHOT_FILENAME, snapshot_payload)
    _write_exclusive(directory / MANIFEST_FILENAME, manifest_payload)
    return manifest


def collection_plan() -> dict[str, Any]:
    return {
        "status": "network_not_started",
        "collector_version": COLLECTOR_VERSION,
        "source_page": SOURCE_PAGE,
        "download_endpoint": DOWNLOAD_ENDPOINT,
        "published_range": [START_YEAR, END_YEAR],
        "expected_rows": EXPECTED_ROWS,
        "known_upstream_omissions": [
            {"year": year, "source_kind": kind}
            for year, kind in sorted(KNOWN_UPSTREAM_OMISSIONS)
        ],
        "expected_jie_rows": EXPECTED_JIE_ROWS,
        "credential_required": False,
        "provider_values_used": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KASI 공식 24기 입기 시각 수집기")
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
    except (KasiOfficialSolarTermsCollectorError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
