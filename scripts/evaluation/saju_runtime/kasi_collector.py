# kasi_collector.py - KASI 음양력 OpenAPI를 자격 증명 비노출·resume 방식으로 수집한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT

SOURCE_PAGE = "https://www.data.go.kr/data/15012679/openapi.do"
ENDPOINT = (
    "https://apis.data.go.kr/B090041/openapi/service/LrsrCldInfoService/getLunCalInfo"
)
SERVICE_KEY_ENV = "KASI_LUNISOLAR_SERVICE_KEY"
CONFIRMATION = "COLLECT_KASI_OFFICIAL_SNAPSHOT"
COLLECTOR_VERSION = "kasi-lunisolar-collector-v1.0.0"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi_lunisolar"
STEMS_KO = ("갑", "을", "병", "정", "무", "기", "경", "신", "임", "계")
STEMS_HANJA = ("甲", "乙", "丙", "丁", "戊", "己", "庚", "辛", "壬", "癸")
BRANCHES_KO = ("자", "축", "인", "묘", "진", "사", "오", "미", "신", "유", "술", "해")
BRANCHES_HANJA = (
    "子",
    "丑",
    "寅",
    "卯",
    "辰",
    "巳",
    "午",
    "未",
    "申",
    "酉",
    "戌",
    "亥",
)


class KasiCollectorError(RuntimeError):
    """공식 snapshot 수집 계약을 위반했을 때 발생한다."""


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise KasiCollectorError(
            "KASI 응답 redirect는 인증키 전달 위험 때문에 허용하지 않습니다."
        )


KASI_OPENER = urllib.request.build_opener(_RejectRedirect())


def _days(start: date, end: date) -> int:
    if end < start:
        raise KasiCollectorError("종료일이 시작일보다 빠릅니다.")
    return (end - start).days + 1


def collection_plan(start: date, end: date) -> dict[str, Any]:
    total = _days(start, end)
    return {
        "status": "network_not_started",
        "source": SOURCE_PAGE,
        "operation": "getLunCalInfo",
        "collector_version": COLLECTOR_VERSION,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "expected_rows": total,
        "development_quota_batches_at_10000": (total + 9999) // 10000,
        "credential_env": SERVICE_KEY_ENV,
        "credential_value_exposed": False,
    }


def _safe_output(directory: Path) -> Path:
    resolved = directory.resolve()
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve())
    except ValueError as exc:
        raise KasiCollectorError(f"출력은 {ALLOWED_ROOT} 아래여야 합니다.") from exc
    if directory.is_symlink():
        raise KasiCollectorError("출력 디렉터리는 symlink일 수 없습니다.")
    return resolved


def _text(item: ET.Element, name: str, *, required: bool = True) -> str:
    node = item.find(name)
    value = "" if node is None or node.text is None else node.text.strip()
    if required and not value:
        raise KasiCollectorError(f"KASI 응답 필드가 비었습니다: {name}")
    return value


def _integer(item: ET.Element, name: str) -> int:
    try:
        return int(_text(item, name))
    except ValueError as exc:
        raise KasiCollectorError(f"KASI 정수 필드가 올바르지 않습니다: {name}") from exc


def _hanja_ganzhi(value: str) -> str:
    cleaned = value.strip().replace("년", "").replace("월", "").replace("일", "")
    if len(cleaned) != 2 or cleaned[0] not in STEMS_KO or cleaned[1] not in BRANCHES_KO:
        raise KasiCollectorError(f"KASI 간지 형식을 해석할 수 없습니다: {value!r}")
    return (
        STEMS_HANJA[STEMS_KO.index(cleaned[0])]
        + BRANCHES_HANJA[BRANCHES_KO.index(cleaned[1])]
    )


def _parse_response(payload: bytes, expected: date) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise KasiCollectorError("KASI XML 응답을 해석하지 못했습니다.") from exc
    code = root.findtext(".//resultCode", default="").strip()
    message = root.findtext(".//resultMsg", default="").strip()
    if code not in {"00", "0000"}:
        raise KasiCollectorError(
            f"KASI API 오류: resultCode={code}, resultMsg={message}"
        )
    items = root.findall(".//item")
    if len(items) != 1:
        raise KasiCollectorError(f"KASI 단일 날짜 응답 item 수가 {len(items)}입니다.")
    item = items[0]
    try:
        solar = date(
            _integer(item, "solYear"),
            _integer(item, "solMonth"),
            _integer(item, "solDay"),
        )
    except ValueError as exc:
        raise KasiCollectorError("KASI 양력 날짜가 올바르지 않습니다.") from exc
    if solar != expected:
        raise KasiCollectorError("KASI 응답 양력일이 요청일과 다릅니다.")
    leap_raw = _text(item, "lunLeapmonth")
    if leap_raw in {"평", "평달", "false", "False"}:
        leap = False
    elif leap_raw in {"윤", "윤달", "true", "True"}:
        leap = True
    else:
        raise KasiCollectorError(f"KASI 윤달 값을 해석할 수 없습니다: {leap_raw!r}")
    day_ko = _text(item, "lunIljin")
    lunar_year = _integer(item, "lunYear")
    lunar_month = _integer(item, "lunMonth")
    lunar_day = _integer(item, "lunDay")
    if not (1 <= lunar_month <= 12 and 1 <= lunar_day <= 30):
        raise KasiCollectorError("KASI 음력 날짜 범위가 올바르지 않습니다.")
    return {
        "schema_version": "1.0.0",
        "solar_date": solar.isoformat(),
        "lunar_date": {
            "year": lunar_year,
            "month": lunar_month,
            "day": lunar_day,
        },
        "leap_month": leap,
        "calendar_year_ganzhi_ko": _text(item, "lunSecha"),
        "calendar_month_ganzhi_ko": _text(item, "lunWolgeon", required=False),
        "day_ganzhi_ko": day_ko,
        "day_ganzhi": _hanja_ganzhi(day_ko),
        "source_id": "kasi_lunisolar_openapi",
    }


def _request_day(service_key: str, value: date, *, timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "solYear": f"{value.year:04d}",
            "solMonth": f"{value.month:02d}",
            "solDay": f"{value.day:02d}",
        }
    )
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={
            "Accept": "application/xml",
            "User-Agent": "saju-runtime-kasi-collector/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with KASI_OPENER.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise KasiCollectorError(
                        f"KASI HTTP status가 {response.status}입니다."
                    )
                return _parse_response(response.read(), value)
        except (urllib.error.URLError, TimeoutError, KasiCollectorError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise KasiCollectorError(
        f"KASI 요청이 3회 실패했습니다: {type(last_error).__name__}"
    )


def _load_existing(path: Path, start: date, end: date) -> tuple[int, date]:
    if not path.exists():
        return 0, start
    if path.is_symlink() or not path.is_file():
        raise KasiCollectorError("기존 snapshot이 일반 파일이 아닙니다.")
    expected = start
    rows = 0
    try:
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                row = json.loads(line)
                actual = date.fromisoformat(row["solar_date"])
                if actual != expected or actual > end:
                    raise KasiCollectorError(
                        f"기존 snapshot 연속성이 깨졌습니다: {number}"
                    )
                expected += timedelta(days=1)
                rows += 1
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise KasiCollectorError(
            "기존 snapshot을 안전하게 resume할 수 없습니다."
        ) from exc
    return rows, expected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_resume_manifest(
    manifest_path: Path,
    snapshot: Path,
    *,
    start: date,
    end: date,
) -> dict[str, Any] | None:
    if not snapshot.exists():
        if manifest_path.exists() or manifest_path.is_symlink():
            raise KasiCollectorError("snapshot 없이 기존 manifest만 남아 있습니다.")
        return None
    if (
        snapshot.is_symlink()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
    ):
        raise KasiCollectorError(
            "resume에는 일반 snapshot과 manifest가 모두 필요합니다."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KasiCollectorError("기존 collection manifest를 읽지 못했습니다.") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("source") != SOURCE_PAGE
        or manifest.get("endpoint") != ENDPOINT
        or manifest.get("collector_version") != COLLECTOR_VERSION
        or manifest.get("collector_sha256") != _sha256(Path(__file__))
        or manifest.get("start_date") != start.isoformat()
        or manifest.get("end_date") != end.isoformat()
        or manifest.get("snapshot_sha256") != _sha256(snapshot)
        or manifest.get("credential_value_recorded") is not False
    ):
        raise KasiCollectorError("기존 snapshot resume provenance가 다릅니다.")
    return manifest


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise KasiCollectorError("manifest 경로는 일반 파일이어야 합니다.")
    temporary = path.with_suffix(".tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if temporary.exists() or temporary.is_symlink():
        raise KasiCollectorError("이전 manifest 임시 파일이 남아 있습니다.")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise KasiCollectorError("manifest 임시 파일을 만들지 못했습니다.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)


def collect(
    *,
    start: date,
    end: date,
    output: Path,
    max_requests: int,
    request_interval: float,
    timeout: float,
    confirmation: str,
) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise KasiCollectorError(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    if not 1 <= max_requests <= 10_000:
        raise KasiCollectorError("max_requests는 1~10,000이어야 합니다.")
    if request_interval < 0 or timeout <= 0:
        raise KasiCollectorError("요청 간격·timeout이 올바르지 않습니다.")
    service_key = os.environ.get(SERVICE_KEY_ENV, "").strip()
    if not service_key:
        raise KasiCollectorError(f"{SERVICE_KEY_ENV} 환경변수가 없습니다.")
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    snapshot = directory / "kasi_lunisolar.jsonl"
    manifest_path = directory / "collection_manifest.json"
    resume_manifest = _load_resume_manifest(
        manifest_path,
        snapshot,
        start=start,
        end=end,
    )
    existing_rows, next_date = _load_existing(snapshot, start, end)
    if resume_manifest is not None and resume_manifest.get("rows") != existing_rows:
        raise KasiCollectorError("기존 manifest와 snapshot 행 수가 다릅니다.")
    requested = 0
    if next_date <= end:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(snapshot, flags, 0o600)
        except OSError as exc:
            raise KasiCollectorError(
                "snapshot 파일을 안전하게 열지 못했습니다."
            ) from exc
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            current = next_date
            while current <= end and requested < max_requests:
                row = _request_day(service_key, current, timeout=timeout)
                stream.write(
                    json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
                requested += 1
                current += timedelta(days=1)
                if current <= end and requested < max_requests and request_interval:
                    time.sleep(request_interval)
    total_rows, next_date = _load_existing(snapshot, start, end)
    expected_rows = _days(start, end)
    manifest = {
        "schema_version": "1.0.0",
        "status": "complete"
        if total_rows == expected_rows
        else "partial_resume_required",
        "source": SOURCE_PAGE,
        "endpoint": ENDPOINT,
        "operation": "getLunCalInfo",
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _sha256(Path(__file__)),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "expected_rows": expected_rows,
        "rows": total_rows,
        "requests_this_run": requested,
        "preexisting_rows": existing_rows,
        "next_date": None if total_rows == expected_rows else next_date.isoformat(),
        "snapshot_sha256": _sha256(snapshot),
        "credential_env": SERVICE_KEY_ENV,
        "credential_value_recorded": False,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    _write_manifest(manifest_path, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KASI 음양력 공식 snapshot 수집기")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    collect_parser = commands.add_parser("collect")
    for target in (plan, collect_parser):
        target.add_argument(
            "--start", type=date.fromisoformat, default=date(1900, 1, 1)
        )
        target.add_argument(
            "--end", type=date.fromisoformat, default=date(2049, 12, 31)
        )
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--max-requests", type=int, default=100)
    collect_parser.add_argument("--request-interval", type=float, default=0.15)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--confirm-network", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = collection_plan(args.start, args.end)
        else:
            result = collect(
                start=args.start,
                end=args.end,
                output=args.output,
                max_requests=args.max_requests,
                request_interval=args.request_interval,
                timeout=args.timeout,
                confirmation=args.confirm_network,
            )
    except KasiCollectorError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
