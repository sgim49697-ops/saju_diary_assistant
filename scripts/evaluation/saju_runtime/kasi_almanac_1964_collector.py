# kasi_almanac_1964_collector.py - KASI 1964년 역서의 백로 원문을 해시 고정 수집한다.

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT

COLLECTOR_VERSION = "kasi-almanac-1964-collector-v1.0.0"
CONFIRMATION = "COLLECT_KASI_ALMANAC_1964_V1"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi/v1.2.0"
SOURCE_PAGE = "https://astro.kasi.re.kr/kor/almanac/pageView/26"
DATA_ENDPOINT = "https://astro.kasi.re.kr/kor/almanac/list"
IMAGE_URL = (
    "https://astro.kasi.re.kr/file/astro_img/KASI_A188_Z_001/00020.jpg"
)
ARCHIVE_ID = "KASI_A188_Z_001"
ARCHIVE_CATEGORY_ID = "SYSCD20170290"
PAGE_SEQUENCE = 20
MAX_LANDING_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PRIVATE_BYTES = 8 * 1024 * 1024

RESPONSE_FILENAME = "kasi_almanac_1964_pages_20_21.json"
IMAGE_FILENAME = "kasi_almanac_1964_page_20.jpg"
SNAPSHOT_FILENAME = "kasi_almanac_1964_baengno.json"
MANIFEST_FILENAME = "kasi_almanac_1964_manifest.json"


class KasiAlmanac1964CollectorError(RuntimeError):
    """KASI 1964년 역서 수집·해석·provenance 계약 위반."""


class _TextTokens(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_data(self, data: str) -> None:
        value = re.sub(r"\s+", " ", data).strip()
        if value:
            self.tokens.append(value)


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del file_pointer, code, message, headers
        resolved = urllib.parse.urljoin(request.full_url, new_url)
        before = urllib.parse.urlsplit(request.full_url)
        after = urllib.parse.urlsplit(resolved)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise KasiAlmanac1964CollectorError(
                "KASI archive의 다른 origin redirect를 허용하지 않습니다."
            )
        return super().redirect_request(
            request, None, 302, "same-origin", {}, resolved
        )


def _opener() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _SameOriginRedirect()
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
            raise KasiAlmanac1964CollectorError("hash 대상이 일반 파일이 아닙니다.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise KasiAlmanac1964CollectorError("hash 대상을 읽지 못했습니다.") from exc
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
        raise KasiAlmanac1964CollectorError(
            f"출력은 {ALLOWED_ROOT} 아래여야 합니다."
        ) from exc
    current = REPO_ROOT.absolute()
    for part in root.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise KasiAlmanac1964CollectorError("KASI raw root에 symlink가 있습니다.")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise KasiAlmanac1964CollectorError("출력 경로에 symlink가 있습니다.")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise KasiAlmanac1964CollectorError("출력 경로가 raw root를 벗어납니다.") from exc
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
            raise KasiAlmanac1964CollectorError(
                "기존 archive 파일의 소유자·권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise KasiAlmanac1964CollectorError("기존 archive 파일을 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise KasiAlmanac1964CollectorError("기존 archive 파일 크기가 제한을 넘습니다.")
    return payload


def _write_exclusive(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise KasiAlmanac1964CollectorError(f"기존 archive 파일이 있습니다: {path.name}")
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


def _read_response(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    *,
    maximum: int,
    content_type_prefix: str,
) -> bytes:
    try:
        with opener.open(request, timeout=30.0) as response:
            if response.status != 200:
                raise KasiAlmanac1964CollectorError(
                    f"KASI archive HTTP status가 {response.status}입니다."
                )
            content_type = response.headers.get_content_type()
            if not content_type.startswith(content_type_prefix):
                raise KasiAlmanac1964CollectorError(
                    f"KASI archive Content-Type이 다릅니다: {content_type}"
                )
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > maximum:
                raise KasiAlmanac1964CollectorError(
                    "KASI archive Content-Length가 제한을 넘습니다."
                )
            payload = response.read(maximum + 1)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        if isinstance(exc, KasiAlmanac1964CollectorError):
            raise
        raise KasiAlmanac1964CollectorError("KASI archive 요청에 실패했습니다.") from exc
    if not payload or len(payload) > maximum:
        raise KasiAlmanac1964CollectorError("KASI archive 응답 크기가 올바르지 않습니다.")
    return payload


def _parse_response(payload: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise KasiAlmanac1964CollectorError("KASI archive JSON을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise KasiAlmanac1964CollectorError("KASI archive JSON 최상위가 object가 아닙니다.")
    metadata = value.get("map")
    pages = value.get("pages")
    if (
        not isinstance(metadata, dict)
        or metadata.get("ALMN_ID") != ARCHIVE_ID
        or metadata.get("KOR_NM") != "역서(曆書)1964년"
        or str(metadata.get("PBLS_YYYY")) != "1964"
        or metadata.get("WEB_SRVC_DVSN") != "이미지,텍스트"
        or not isinstance(pages, list)
    ):
        raise KasiAlmanac1964CollectorError("KASI 1964년 역서 metadata가 다릅니다.")
    by_sequence = {page.get("PAGE_SEQ"): page for page in pages if isinstance(page, dict)}
    if set(by_sequence) != {20, 21}:
        raise KasiAlmanac1964CollectorError("KASI 1964년 9월 page 범위가 다릅니다.")
    page = by_sequence[PAGE_SEQUENCE]
    if (
        page.get("ALMN_ID") != ARCHIVE_ID
        or page.get("ARTL_AFT_FILENM") != "00020.jpg"
        or not isinstance(page.get("PAGE_CONT"), str)
    ):
        raise KasiAlmanac1964CollectorError("KASI 1964년 9월 page identity가 다릅니다.")
    parser = _TextTokens()
    parser.feed(page["PAGE_CONT"])
    tokens = parser.tokens
    try:
        start = tokens.index("백로")
    except ValueError as exc:
        raise KasiAlmanac1964CollectorError("1964년 역서에서 백로를 찾지 못했습니다.") from exc
    if (
        "9 월 소" not in tokens[:start]
        or tokens[start : start + 4] != ["백로", "7일", "24시", "00분"]
    ):
        raise KasiAlmanac1964CollectorError("1964년 역서 백로 표기가 예상과 다릅니다.")
    normalized = {
        "schema_version": "1.0.0",
        "source_id": "kasi_digitized_almanac_1964",
        "source_tier": "formal_kasi_almanac_archive",
        "source_page": SOURCE_PAGE,
        "archive_id": ARCHIVE_ID,
        "page_sequence": PAGE_SEQUENCE,
        "page_filename": "00020.jpg",
        "year": 1964,
        "term_index": 16,
        "term_name": "백로",
        "printed_local_date": "1964-09-07",
        "printed_hour": 24,
        "printed_minute": 0,
        "printed_label": "1964-09-07 24:00 KST",
        "normalized_reference_local_minute": "1964-09-08T00:00+09:00",
        "reference_precision": "minute",
        "normalization": "end_of_day_24_00_to_next_day_00_00",
        "subminute_instant_claimed": False,
    }
    return value, normalized


def _existing_manifest(directory: Path) -> dict[str, Any] | None:
    paths = [
        directory / RESPONSE_FILENAME,
        directory / IMAGE_FILENAME,
        directory / SNAPSHOT_FILENAME,
        directory / MANIFEST_FILENAME,
    ]
    present = [path.exists() or path.is_symlink() for path in paths]
    if not any(present):
        return None
    if not all(present):
        raise KasiAlmanac1964CollectorError("KASI archive 산출물이 일부만 존재합니다.")
    try:
        manifest = json.loads(_private_bytes(paths[-1]))
        normalized = json.loads(_private_bytes(paths[-2]))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise KasiAlmanac1964CollectorError("기존 archive JSON을 읽지 못했습니다.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != _sha256_file(Path(__file__))
        or manifest.get("credential_used") is not False
        or manifest.get("artifacts", {}).get(RESPONSE_FILENAME, {}).get("sha256")
        != _sha256_file(paths[0])
        or manifest.get("artifacts", {}).get(IMAGE_FILENAME, {}).get("sha256")
        != _sha256_file(paths[1])
        or manifest.get("artifacts", {}).get(SNAPSHOT_FILENAME, {}).get("sha256")
        != _sha256_file(paths[2])
        or not isinstance(normalized, dict)
        or normalized.get("normalized_reference_local_minute")
        != "1964-09-08T00:00+09:00"
    ):
        raise KasiAlmanac1964CollectorError("기존 archive provenance가 다릅니다.")
    return manifest


def collect(*, output: Path, confirmation: str) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise KasiAlmanac1964CollectorError(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    existing = _existing_manifest(directory)
    if existing is not None:
        return existing

    opener = _opener()
    landing = _read_response(
        opener,
        urllib.request.Request(
            SOURCE_PAGE,
            headers={"Accept": "text/html", "User-Agent": "saju-runtime-kasi-almanac/1.0"},
        ),
        maximum=MAX_LANDING_BYTES,
        content_type_prefix="text/html",
    )
    if ARCHIVE_ID.encode() not in landing or "역서(曆書)1964년".encode() not in landing:
        raise KasiAlmanac1964CollectorError("KASI archive landing에서 1964년 역서를 찾지 못했습니다.")
    body = urllib.parse.urlencode(
        {
            "searchKey": ARCHIVE_CATEGORY_ID,
            "searchAlmn": ARCHIVE_ID,
            "start_seq": "20",
            "end_seq": "22",
        }
    ).encode()
    response_payload = _read_response(
        opener,
        urllib.request.Request(
            DATA_ENDPOINT,
            data=body,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": SOURCE_PAGE,
                "User-Agent": "saju-runtime-kasi-almanac/1.0",
                "X-Requested-With": "XMLHttpRequest",
            },
        ),
        maximum=MAX_JSON_BYTES,
        content_type_prefix="application/json",
    )
    response, normalized = _parse_response(response_payload)
    canonical_response = (
        json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")
    normalized_payload = (
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    image_payload = _read_response(
        opener,
        urllib.request.Request(
            IMAGE_URL,
            headers={"Accept": "image/jpeg", "Referer": SOURCE_PAGE, "User-Agent": "saju-runtime-kasi-almanac/1.0"},
        ),
        maximum=MAX_IMAGE_BYTES,
        content_type_prefix="image/jpeg",
    )
    if not image_payload.startswith(b"\xff\xd8\xff") or not image_payload.endswith(b"\xff\xd9"):
        raise KasiAlmanac1964CollectorError("KASI archive page가 완전한 JPEG가 아닙니다.")
    artifacts = {
        RESPONSE_FILENAME: {
            "bytes": len(canonical_response),
            "sha256": _sha256_bytes(canonical_response),
        },
        IMAGE_FILENAME: {
            "bytes": len(image_payload),
            "sha256": _sha256_bytes(image_payload),
        },
        SNAPSHOT_FILENAME: {
            "bytes": len(normalized_payload),
            "sha256": _sha256_bytes(normalized_payload),
        },
    }
    manifest = {
        "schema_version": "1.0.0",
        "status": "complete",
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _sha256_file(Path(__file__)),
        "source_page": SOURCE_PAGE,
        "data_endpoint": DATA_ENDPOINT,
        "image_url": IMAGE_URL,
        "archive_id": ARCHIVE_ID,
        "page_sequence": PAGE_SEQUENCE,
        "artifacts": artifacts,
        "credential_used": False,
        "private_path_recorded": False,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_exclusive(directory / RESPONSE_FILENAME, canonical_response)
    _write_exclusive(directory / IMAGE_FILENAME, image_payload)
    _write_exclusive(directory / SNAPSHOT_FILENAME, normalized_payload)
    _write_exclusive(directory / MANIFEST_FILENAME, manifest_payload)
    return manifest


def collection_plan() -> dict[str, Any]:
    return {
        "status": "network_not_started",
        "collector_version": COLLECTOR_VERSION,
        "source_page": SOURCE_PAGE,
        "archive_id": ARCHIVE_ID,
        "page_sequence": PAGE_SEQUENCE,
        "target": "1964년 백로 9월 7일 24시 00분 원문과 이미지",
        "credential_required": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KASI 1964년 역서 백로 수집기")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--confirm-network", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = (
            collection_plan()
            if args.command == "plan"
            else collect(output=args.output, confirmation=args.confirm_network)
        )
    except (KasiAlmanac1964CollectorError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
