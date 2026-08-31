# kasi_minute_collector_v1_1.py - KASI 달력자료의 표시 분 단위 절입 근거를 원문 해시와 함께 수집한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import JIE_TO_MONTH, SOLAR_TERM_NAMES

SOURCE_PAGE = "https://astro.kasi.re.kr/kor/life/post/calendarData"
SOURCE_TIER = "institutional_minute_display_reference_not_formal_almanac"
COLLECTOR_VERSION = "kasi-minute-reference-collector-v1.1.0"
CONFIRMATION = "COLLECT_KASI_MINUTE_REFERENCES_V1_1"
YEARS = tuple(range(2021, 2028))
EXPECTED_ROWS = len(YEARS) * len(JIE_TO_MONTH)
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi/v1.1.0"
DEFAULT_OUTPUT = ALLOWED_ROOT / "minute-references"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
KST = timezone(timedelta(hours=9))


class KasiMinuteCollectorError(RuntimeError):
    """KASI 표시 분 단위 근거의 수집·provenance 계약 위반."""


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        raise KasiMinuteCollectorError("KASI 달력자료 redirect는 허용하지 않습니다.")


KASI_OPENER = urllib.request.build_opener(_RejectRedirect())


class _TableRows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del attrs
        if tag.lower() == "tr":
            self._row = []
        elif tag.lower() in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"td", "th"} and self._cell is not None:
            assert self._row is not None
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif normalized == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def source_url(year: int) -> str:
    if year not in YEARS:
        raise KasiMinuteCollectorError("KASI 표시 분 단위 수집 연도가 다릅니다.")
    query = urllib.parse.urlencode(
        {"search_year": str(year), "bbs_uniq_id": "calendarData"}
    )
    return f"{SOURCE_PAGE}?{query}"


def _download(year: int) -> bytes:
    url = source_url(year)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "saju-runtime-conformance/1.1 (+local-validator)",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ko-KR,ko;q=0.9",
        },
    )
    try:
        with KASI_OPENER.open(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if response.status != 200 or response.geturl() != url:
                raise KasiMinuteCollectorError("KASI 달력자료 응답 대상이 다릅니다.")
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise KasiMinuteCollectorError("KASI 달력자료 응답 형식이 HTML이 아닙니다.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except KasiMinuteCollectorError:
        raise
    except OSError as exc:
        raise KasiMinuteCollectorError("KASI 달력자료를 내려받지 못했습니다.") from exc
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise KasiMinuteCollectorError("KASI 달력자료 응답 크기가 허용 범위를 벗어났습니다.")
    return payload


def parse_calendar_html(payload: bytes, year: int) -> list[dict[str, Any]]:
    if not payload or len(payload) > MAX_RESPONSE_BYTES or b"\x00" in payload:
        raise KasiMinuteCollectorError("KASI 달력자료 HTML 크기·내용이 다릅니다.")
    try:
        html = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise KasiMinuteCollectorError("KASI 달력자료 HTML은 UTF-8이어야 합니다.") from exc
    if (
        f"{year}년 달력자료" not in html
        or "24절기" not in html
        or "공식 발표 자료가 아닙니다" not in html
    ):
        raise KasiMinuteCollectorError("KASI 달력자료 제목·등급 고지가 다릅니다.")

    parser = _TableRows()
    parser.feed(html)
    parser.close()
    by_name = {name: index for index, name in enumerate(SOLAR_TERM_NAMES)}
    parsed: dict[int, tuple[int, int, int, int]] = {}
    for cells in parser.rows:
        if len(cells) != 5 or cells[0] not in by_name:
            continue
        index = by_name[cells[0]]
        try:
            values = tuple(int(value) for value in cells[1:])
        except ValueError as exc:
            raise KasiMinuteCollectorError("KASI 24절기 표에 정수가 아닌 값이 있습니다.") from exc
        if index in parsed:
            raise KasiMinuteCollectorError("KASI 24절기 표에 중복 절기가 있습니다.")
        parsed[index] = values
    if set(parsed) != set(range(24)):
        raise KasiMinuteCollectorError("KASI 24절기 표가 24개 절기를 모두 포함하지 않습니다.")

    rows: list[dict[str, Any]] = []
    for index in sorted(JIE_TO_MONTH):
        month, day, hour, minute = parsed[index]
        try:
            instant = datetime(year, month, day, hour, minute, tzinfo=KST)
        except ValueError as exc:
            raise KasiMinuteCollectorError("KASI 24절기 표의 날짜·시각이 유효하지 않습니다.") from exc
        rows.append(
            {
                "year": year,
                "term_index": index,
                "term_name": SOLAR_TERM_NAMES[index],
                "reference_local_minute": instant.isoformat(timespec="minutes"),
                "reference_precision": "displayed_minute",
                "source_page": SOURCE_PAGE,
                "source_tier": SOURCE_TIER,
                "generated_value": False,
            }
        )
    return rows


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _collector_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _safe_output(path: Path) -> Path:
    unresolved = path.absolute()
    allowed = ALLOWED_ROOT.absolute()
    try:
        relative = unresolved.relative_to(allowed)
    except ValueError as exc:
        raise KasiMinuteCollectorError(f"출력은 {ALLOWED_ROOT} 아래여야 합니다.") from exc
    current = REPO_ROOT.absolute()
    for part in allowed.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise KasiMinuteCollectorError("KASI raw root 경로에 symlink가 포함됐습니다.")
    current = allowed
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise KasiMinuteCollectorError("출력 경로에 symlink가 포함됐습니다.")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise KasiMinuteCollectorError("출력 경로가 KASI raw root를 벗어납니다.") from exc
    return resolved


def _exclusive_write(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise KasiMinuteCollectorError("KASI 근거 파일을 배타적으로 쓰지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ) + "\n"
    return text.encode("utf-8")


def collect(
    output: Path,
    *,
    confirmation: str,
    fetch: Callable[[int], bytes] = _download,
) -> tuple[Path, dict[str, Any]]:
    if confirmation != CONFIRMATION:
        raise KasiMinuteCollectorError("공식 페이지 수집 확인 문자열이 다릅니다.")
    target = _safe_output(output)
    artifacts: dict[int, bytes] = {}
    rows: list[dict[str, Any]] = []
    for year in YEARS:
        payload = fetch(year)
        artifacts[year] = payload
        artifact_hash = _sha256_bytes(payload)
        year_rows = parse_calendar_html(payload, year)
        for row in year_rows:
            row["source_artifact_sha256"] = artifact_hash
        rows.extend(year_rows)
    if len(rows) != EXPECTED_ROWS:
        raise KasiMinuteCollectorError("KASI 표시 분 단위 행 수가 다릅니다.")

    snapshot = b"".join(_json_bytes(row) for row in rows)
    manifest = {
        "schema_version": "1.1.0",
        "status": "complete",
        "source_page": SOURCE_PAGE,
        "source_tier": SOURCE_TIER,
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _collector_sha256(),
        "years": list(YEARS),
        "rows": EXPECTED_ROWS,
        "snapshot_sha256": _sha256_bytes(snapshot),
        "generated_values": False,
        "second_precision_available": False,
        "source_artifacts": {
            str(year): _sha256_bytes(artifacts[year]) for year in YEARS
        },
        "source_urls": {str(year): source_url(year) for year in YEARS},
    }
    outputs = {
        **{
            f"kasi_calendar_data_{year}.html": payload
            for year, payload in artifacts.items()
        },
        "kasi_minute_references.jsonl": snapshot,
        "kasi_minute_references_manifest.json": _json_bytes(manifest, pretty=True),
    }
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise KasiMinuteCollectorError("기존 출력 경로가 안전한 디렉터리가 아닙니다.")
        for name, expected in outputs.items():
            path = target / name
            if path.is_symlink() or not path.is_file() or path.read_bytes() != expected:
                raise KasiMinuteCollectorError("기존 KASI 표시 분 단위 snapshot이 다릅니다.")
        return target / "kasi_minute_references.jsonl", manifest

    try:
        target.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise KasiMinuteCollectorError("KASI 표시 분 단위 출력 경로를 만들지 못했습니다.") from exc
    for name, payload in outputs.items():
        _exclusive_write(target / name, payload)
    return target / "kasi_minute_references.jsonl", manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KASI 달력자료의 2021~2027 표시 분 단위 12절 근거 수집"
    )
    parser.add_argument("command", choices=["plan", "collect"])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--confirm-network")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "status": "planned",
                    "source_page": SOURCE_PAGE,
                    "years": list(YEARS),
                    "requests": len(YEARS),
                    "expected_jie_rows": EXPECTED_ROWS,
                    "source_tier": SOURCE_TIER,
                    "generated_substitution_allowed": False,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    try:
        snapshot, manifest = collect(
            args.output,
            confirmation=args.confirm_network or "",
        )
    except KasiMinuteCollectorError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "complete",
                "rows": manifest["rows"],
                "snapshot_sha256": manifest["snapshot_sha256"],
                "output": str(snapshot),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
