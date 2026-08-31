# kasi_collector_v1_1.py - KASI 음양력·24절기 snapshot을 월/연 단위로 안전하게 수집한다.

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import SOLAR_TERM_NAMES

LUNAR_SOURCE_PAGE = "https://www.data.go.kr/data/15012679/openapi.do"
SOLAR_TERM_SOURCE_PAGE = "https://www.data.go.kr/data/15012690/openapi.do"
LUNAR_ENDPOINT = (
    "https://apis.data.go.kr/B090041/openapi/service/LrsrCldInfoService/"
    "getLunCalInfo"
)
SOLAR_TERM_ENDPOINT = (
    "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/"
    "get24DivisionsInfo"
)
SERVICE_KEY_ENV = "KASI_SERVICE_KEY"
SERVICE_KEY_FILENAME = "saju-kasi-service-key"
CONFIRMATION = "COLLECT_KASI_RUNTIME_V1_1"
COLLECTOR_VERSION = "kasi-runtime-collector-v1.1.0"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi/v1.1.0"
SUPPORTED_START_YEAR = 1900
SUPPORTED_END_YEAR = 2049
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_KEY_BYTES = 4096
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
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
GANZHI_PATTERN = re.compile(
    rf"^([{''.join(STEMS_KO)}][{''.join(BRANCHES_KO)}])"
    rf"(?:\(([{''.join(STEMS_HANJA)}][{''.join(BRANCHES_HANJA)}])\))?$"
)


class KasiCollectorV11Error(RuntimeError):
    """KASI v1.1 수집·provenance 계약 위반."""


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        raise KasiCollectorV11Error(
            "KASI 응답 redirect는 인증키 전달 위험 때문에 허용하지 않습니다."
        )


KASI_OPENER = urllib.request.build_opener(_RejectRedirect())


def default_service_key_path() -> Path:
    runtime_root = os.environ.get("XDG_RUNTIME_DIR")
    expected = Path(f"/run/user/{os.getuid()}")
    if runtime_root is not None and Path(runtime_root) != expected:
        raise KasiCollectorV11Error(
            "XDG_RUNTIME_DIR이 현재 사용자의 고정 runtime 경로와 다릅니다."
        )
    return expected / SERVICE_KEY_FILENAME


def _read_key_file(path: Path) -> str:
    if not path.is_absolute():
        raise KasiCollectorV11Error("KASI key 파일은 절대 경로여야 합니다.")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or not 1 <= metadata.st_size <= MAX_KEY_BYTES
        ):
            raise KasiCollectorV11Error(
                "KASI key는 현재 사용자 소유의 0600 일반 파일이어야 합니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(MAX_KEY_BYTES + 1)
    except OSError as exc:
        raise KasiCollectorV11Error(
            "KASI key 파일을 안전하게 읽지 못했습니다."
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if payload.endswith(b"\n"):
        payload = payload[:-1]
        if payload.endswith(b"\r"):
            payload = payload[:-1]
    try:
        key = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise KasiCollectorV11Error("KASI key는 한 줄 ASCII여야 합니다.") from exc
    if not key or "%" in key or any(
        not 0x21 <= ord(character) <= 0x7E for character in key
    ):
        raise KasiCollectorV11Error(
            "KASI key는 percent-encoding 전의 공백 없는 한 줄 ASCII여야 합니다."
        )
    return key


def load_service_key(path: Path | None = None) -> str:
    environment_key = os.environ.get(SERVICE_KEY_ENV)
    key_path = default_service_key_path() if path is None else path
    file_exists = key_path.exists() or key_path.is_symlink()
    if environment_key is not None and file_exists:
        raise KasiCollectorV11Error(
            "KASI key 파일과 환경변수를 동시에 제공할 수 없습니다."
        )
    if file_exists:
        return _read_key_file(key_path)
    if environment_key is None:
        raise KasiCollectorV11Error(
            f"KASI key 파일이 없습니다: {key_path}. 채팅이나 명령행 인자로 key를 전달하지 마세요."
        )
    try:
        environment_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise KasiCollectorV11Error(f"{SERVICE_KEY_ENV}는 ASCII여야 합니다.") from exc
    if (
        not 1 <= len(environment_key) <= MAX_KEY_BYTES
        or environment_key != environment_key.strip()
        or "%" in environment_key
        or any(not 0x21 <= ord(character) <= 0x7E for character in environment_key)
    ):
        raise KasiCollectorV11Error(
            f"{SERVICE_KEY_ENV}는 percent-encoding 전의 공백 없는 한 줄 값이어야 합니다."
        )
    return environment_key


def _safe_output(directory: Path) -> Path:
    unresolved = directory.absolute()
    allowed_unresolved = ALLOWED_ROOT.absolute()
    try:
        relative = unresolved.relative_to(allowed_unresolved)
    except ValueError as exc:
        raise KasiCollectorV11Error(f"출력은 {ALLOWED_ROOT} 아래여야 합니다.") from exc
    current = REPO_ROOT.absolute()
    for part in allowed_unresolved.relative_to(REPO_ROOT.absolute()).parts:
        current /= part
        if current.is_symlink():
            raise KasiCollectorV11Error("KASI raw root 경로에 symlink가 포함됐습니다.")
    current = allowed_unresolved
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise KasiCollectorV11Error("출력 경로에 symlink가 포함됐습니다.")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise KasiCollectorV11Error("출력 경로가 KASI raw root를 벗어납니다.") from exc
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise KasiCollectorV11Error("SHA-256 대상이 일반 파일이 아닙니다.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise KasiCollectorV11Error("SHA-256 대상을 안전하게 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


def _read_private_bytes(path: Path, maximum: int, label: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.getuid()
            or not 1 <= metadata.st_size <= maximum
        ):
            raise KasiCollectorV11Error(f"{label} 권한·소유자·크기가 다릅니다.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise KasiCollectorV11Error(f"{label}을 안전하게 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise KasiCollectorV11Error(f"{label} 크기가 제한을 넘습니다.")
    return payload


def _load_private_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_private_bytes(path, MAX_MANIFEST_BYTES, "manifest"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise KasiCollectorV11Error("manifest JSON을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise KasiCollectorV11Error("manifest 최상위가 object가 아닙니다.")
    return value


def _text(item: ET.Element, name: str, *, required: bool = True) -> str:
    node = item.find(name)
    value = "" if node is None or node.text is None else node.text.strip()
    if required and not value:
        raise KasiCollectorV11Error(f"KASI 응답 필드가 비었습니다: {name}")
    return value


def _integer(item: ET.Element, name: str) -> int:
    try:
        return int(_text(item, name))
    except ValueError as exc:
        raise KasiCollectorV11Error(
            f"KASI 정수 필드가 올바르지 않습니다: {name}"
        ) from exc


def _response_items(payload: bytes) -> list[ET.Element]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise KasiCollectorV11Error("KASI XML 응답을 해석하지 못했습니다.") from exc
    code = root.findtext(".//resultCode", default="").strip()
    message = root.findtext(".//resultMsg", default="").strip()
    if code not in {"00", "0000"}:
        raise KasiCollectorV11Error(
            f"KASI API 오류: resultCode={code}, resultMsg={message[:80]}"
        )
    items = root.findall(".//item")
    total_text = root.findtext(".//totalCount")
    if total_text is not None:
        try:
            total = int(total_text.strip())
        except ValueError as exc:
            raise KasiCollectorV11Error("KASI totalCount가 정수가 아닙니다.") from exc
        if total != len(items):
            raise KasiCollectorV11Error(
                "KASI 응답이 pagination으로 잘렸거나 totalCount와 다릅니다."
            )
    return items


def _parse_ganzhi(value: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", "", value).replace("년", "").replace("월", "").replace("일", "")
    match = GANZHI_PATTERN.fullmatch(cleaned)
    if match is None:
        raise KasiCollectorV11Error(f"KASI 간지 형식을 해석할 수 없습니다: {value!r}")
    korean, supplied_hanja = match.groups()
    derived = (
        STEMS_HANJA[STEMS_KO.index(korean[0])]
        + BRANCHES_HANJA[BRANCHES_KO.index(korean[1])]
    )
    if supplied_hanja is not None and supplied_hanja != derived:
        raise KasiCollectorV11Error("KASI 한글·한자 간지가 서로 다릅니다.")
    return korean, derived


def parse_lunar_month(payload: bytes, year: int, month: int) -> list[dict[str, Any]]:
    items = _response_items(payload)
    expected_days = calendar.monthrange(year, month)[1]
    if len(items) != expected_days:
        raise KasiCollectorV11Error(
            f"KASI {year:04d}-{month:02d} 응답은 {expected_days}일이어야 합니다."
        )
    rows: list[dict[str, Any]] = []
    for item in items:
        try:
            solar = date(
                _integer(item, "solYear"),
                _integer(item, "solMonth"),
                _integer(item, "solDay"),
            )
            lunar = {
                "year": _integer(item, "lunYear"),
                "month": _integer(item, "lunMonth"),
                "day": _integer(item, "lunDay"),
            }
        except ValueError as exc:
            raise KasiCollectorV11Error("KASI 날짜 값이 올바르지 않습니다.") from exc
        if solar.year != year or solar.month != month:
            raise KasiCollectorV11Error("KASI 월 응답에 다른 양력 월이 포함됐습니다.")
        if not 1 <= lunar["month"] <= 12 or not 1 <= lunar["day"] <= 30:
            raise KasiCollectorV11Error("KASI 음력 날짜 범위가 올바르지 않습니다.")
        leap_raw = _text(item, "lunLeapmonth")
        if leap_raw in {"평", "평달", "false", "False"}:
            leap = False
        elif leap_raw in {"윤", "윤달", "true", "True"}:
            leap = True
        else:
            raise KasiCollectorV11Error(
                f"KASI 윤달 값을 해석할 수 없습니다: {leap_raw!r}"
            )
        day_ko, day_hanja = _parse_ganzhi(_text(item, "lunIljin"))
        rows.append(
            {
                "schema_version": "1.1.0",
                "solar_date": solar.isoformat(),
                "lunar_date": lunar,
                "leap_month": leap,
                "calendar_year_ganzhi": _text(item, "lunSecha", required=False),
                "calendar_month_ganzhi": _text(item, "lunWolgeon", required=False),
                "day_ganzhi_ko": day_ko,
                "day_ganzhi": day_hanja,
                "source_id": "kasi_lunisolar_openapi",
            }
        )
    rows.sort(key=lambda row: row["solar_date"])
    expected = date(year, month, 1)
    for row in rows:
        if date.fromisoformat(row["solar_date"]) != expected:
            raise KasiCollectorV11Error("KASI 월 응답 날짜 연속성이 깨졌습니다.")
        expected += timedelta(days=1)
    return rows


def parse_solar_term_year(payload: bytes, year: int) -> list[dict[str, Any]]:
    items = _response_items(payload)
    if len(items) != 24:
        raise KasiCollectorV11Error(f"KASI {year}년 24절기 응답이 24건이 아닙니다.")
    by_name: dict[str, date] = {}
    for item in items:
        name = re.sub(r"\s+", "", _text(item, "dateName"))
        if name not in SOLAR_TERM_NAMES or name in by_name:
            raise KasiCollectorV11Error(f"KASI 24절기 명칭이 다릅니다: {name!r}")
        raw_date = _text(item, "locdate")
        if re.fullmatch(r"\d{8}", raw_date) is None:
            raise KasiCollectorV11Error("KASI 24절기 날짜 형식이 YYYYMMDD가 아닙니다.")
        try:
            value = date(int(raw_date[0:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except ValueError as exc:
            raise KasiCollectorV11Error("KASI 24절기 날짜가 올바르지 않습니다.") from exc
        if value.year != year:
            raise KasiCollectorV11Error("KASI 24절기 응답 연도가 요청과 다릅니다.")
        by_name[name] = value
    if set(by_name) != set(SOLAR_TERM_NAMES):
        raise KasiCollectorV11Error("KASI 24절기 명칭 집합이 다릅니다.")
    return [
        {
            "schema_version": "1.1.0",
            "year": year,
            "term_index": index,
            "term_name": name,
            "local_date": by_name[name].isoformat(),
            "reference_precision": "date",
            "source_id": "kasi_24_divisions_openapi",
        }
        for index, name in enumerate(SOLAR_TERM_NAMES)
    ]


def _request(
    endpoint: str,
    parameters: dict[str, str],
    service_key: str,
    *,
    timeout: float,
) -> bytes:
    query = urllib.parse.urlencode({"serviceKey": service_key, **parameters})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={
            "Accept": "application/xml",
            "User-Agent": "saju-runtime-kasi-collector/1.1",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with KASI_OPENER.open(request, timeout=timeout) as response:
                if response.status != 200:
                    raise KasiCollectorV11Error(
                        f"KASI HTTP status가 {response.status}입니다."
                    )
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > MAX_RESPONSE_BYTES:
                    raise KasiCollectorV11Error("KASI 응답 크기가 제한을 넘습니다.")
                payload = response.read(MAX_RESPONSE_BYTES + 1)
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise KasiCollectorV11Error("KASI 응답 크기가 제한을 넘습니다.")
                return payload
        except (
            OSError,
            ValueError,
            urllib.error.URLError,
            TimeoutError,
            KasiCollectorV11Error,
        ) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2**attempt)
    raise KasiCollectorV11Error(
        f"KASI 요청이 3회 실패했습니다: {type(last_error).__name__}"
    )


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise KasiCollectorV11Error("manifest 경로는 일반 파일이어야 합니다.")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise KasiCollectorV11Error("이전 manifest 임시 파일이 남아 있습니다.")
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise KasiCollectorV11Error("snapshot 경로는 일반 파일이어야 합니다.")
    payload = b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
        for row in rows
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise KasiCollectorV11Error(
                "snapshot은 현재 사용자 소유의 0600 일반 파일이어야 합니다."
            )
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise KasiCollectorV11Error("기존 snapshot이 일반 파일이 아닙니다.")
    rows: list[dict[str, Any]] = []
    try:
        text = _read_private_bytes(path, MAX_SNAPSHOT_BYTES, "기존 snapshot").decode(
            "utf-8"
        )
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                raise KasiCollectorV11Error(
                    f"기존 snapshot에 빈 행이 있습니다: {number}"
                )
            value = json.loads(line)
            if not isinstance(value, dict):
                raise KasiCollectorV11Error(
                    f"기존 snapshot 행이 object가 아닙니다: {number}"
                )
            rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KasiCollectorV11Error("기존 snapshot을 읽지 못했습니다.") from exc
    return rows


def _validate_lunar_existing(rows: list[dict[str, Any]]) -> int:
    expected = date(SUPPORTED_START_YEAR, 1, 1)
    for number, row in enumerate(rows, 1):
        try:
            actual = date.fromisoformat(row["solar_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise KasiCollectorV11Error(
                f"기존 음양력 snapshot schema가 다릅니다: {number}"
            ) from exc
        if row.get("source_id") != "kasi_lunisolar_openapi" or actual != expected:
            raise KasiCollectorV11Error(
                f"기존 음양력 snapshot 연속성이 깨졌습니다: {number}"
            )
        expected += timedelta(days=1)
    if rows and expected.day != 1:
        raise KasiCollectorV11Error("기존 음양력 snapshot이 월 경계에서 끝나지 않았습니다.")
    if expected > date(SUPPORTED_END_YEAR + 1, 1, 1):
        raise KasiCollectorV11Error("기존 음양력 snapshot이 지원 범위를 넘습니다.")
    return (expected.year - SUPPORTED_START_YEAR) * 12 + expected.month - 1


def _validate_term_existing(rows: list[dict[str, Any]]) -> int:
    if len(rows) % 24:
        raise KasiCollectorV11Error("기존 24절기 snapshot이 연 경계에서 끝나지 않았습니다.")
    for offset in range(0, len(rows), 24):
        year = SUPPORTED_START_YEAR + offset // 24
        block = rows[offset : offset + 24]
        if [row.get("term_index") for row in block] != list(range(24)) or any(
            row.get("year") != year or row.get("source_id") != "kasi_24_divisions_openapi"
            for row in block
        ):
            raise KasiCollectorV11Error(f"기존 24절기 snapshot이 다릅니다: {year}")
    if len(rows) > (SUPPORTED_END_YEAR - SUPPORTED_START_YEAR + 1) * 24:
        raise KasiCollectorV11Error("기존 24절기 snapshot이 지원 범위를 넘습니다.")
    return len(rows) // 24


def _periods(
    source: str, service_key: str, timeout: float
) -> tuple[str, str, int, Callable[[int], list[dict[str, Any]]]]:
    if source == "lunar":
        total = (SUPPORTED_END_YEAR - SUPPORTED_START_YEAR + 1) * 12

        def request_period(offset: int) -> list[dict[str, Any]]:
            year = SUPPORTED_START_YEAR + offset // 12
            month = offset % 12 + 1
            payload = _request(
                LUNAR_ENDPOINT,
                {
                    "solYear": f"{year:04d}",
                    "solMonth": f"{month:02d}",
                    "pageNo": "1",
                    "numOfRows": "40",
                },
                service_key,
                timeout=timeout,
            )
            return parse_lunar_month(payload, year, month)

        return "kasi_lunisolar.jsonl", LUNAR_SOURCE_PAGE, total, request_period
    total = SUPPORTED_END_YEAR - SUPPORTED_START_YEAR + 1

    def request_period(offset: int) -> list[dict[str, Any]]:
        year = SUPPORTED_START_YEAR + offset
        payload = _request(
            SOLAR_TERM_ENDPOINT,
            {"solYear": f"{year:04d}", "pageNo": "1", "numOfRows": "30"},
            service_key,
            timeout=timeout,
        )
        return parse_solar_term_year(payload, year)

    return "kasi_solar_terms.jsonl", SOLAR_TERM_SOURCE_PAGE, total, request_period

def collection_plan() -> dict[str, Any]:
    lunar_requests = (SUPPORTED_END_YEAR - SUPPORTED_START_YEAR + 1) * 12
    term_requests = SUPPORTED_END_YEAR - SUPPORTED_START_YEAR + 1
    return {
        "status": "network_not_started",
        "collector_version": COLLECTOR_VERSION,
        "support": {
            "start_year": SUPPORTED_START_YEAR,
            "end_year": SUPPORTED_END_YEAR,
            "solar_days": 54_787,
        },
        "requests": {
            "lunar_months": lunar_requests,
            "solar_term_years": term_requests,
            "total": lunar_requests + term_requests,
            "development_quota": 10_000,
        },
        "credential": {
            "preferred_path": str(default_service_key_path()),
            "fallback_env": SERVICE_KEY_ENV,
            "value_exposed": False,
        },
        "sources": [LUNAR_SOURCE_PAGE, SOLAR_TERM_SOURCE_PAGE],
    }


def collect(
    *,
    source: str,
    output: Path,
    service_key_file: Path | None,
    max_requests: int,
    request_interval: float,
    timeout: float,
    confirmation: str,
) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise KasiCollectorV11Error(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    if source not in {"lunar", "solar-terms"}:
        raise KasiCollectorV11Error("지원하지 않는 KASI source입니다.")
    if not 1 <= max_requests <= 10_000 or request_interval < 0 or timeout <= 0:
        raise KasiCollectorV11Error("요청 수·간격·timeout이 올바르지 않습니다.")
    service_key = load_service_key(service_key_file)
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    filename, source_page, total_periods, request_period = _periods(
        source, service_key, timeout
    )
    snapshot = directory / filename
    manifest_path = directory / f"{Path(filename).stem}_manifest.json"
    rows = _load_jsonl(snapshot)
    completed = (
        _validate_lunar_existing(rows)
        if source == "lunar"
        else _validate_term_existing(rows)
    )
    if snapshot.exists() and manifest_path.exists():
        manifest = _load_private_manifest(manifest_path)
        if (
            not isinstance(manifest, dict)
            or manifest.get("collector_version") != COLLECTOR_VERSION
            or manifest.get("source_kind") != source
            or manifest.get("snapshot_sha256") != _sha256(snapshot)
            or manifest.get("completed_periods") != completed
            or manifest.get("credential_value_recorded") is not False
        ):
            raise KasiCollectorV11Error("기존 snapshot resume provenance가 다릅니다.")
    elif snapshot.exists() or manifest_path.exists():
        raise KasiCollectorV11Error("resume에는 snapshot과 manifest가 모두 필요합니다.")
    requested = 0
    while completed < total_periods and requested < max_requests:
        batch = request_period(completed)
        _append_rows(snapshot, batch)
        completed += 1
        requested += 1
        total_rows = len(rows) + sum(
            calendar.monthrange(
                SUPPORTED_START_YEAR + offset // 12, offset % 12 + 1
            )[1]
            for offset in range(
                completed - requested,
                completed,
            )
        ) if source == "lunar" else completed * 24
        manifest = {
            "schema_version": "1.1.0",
            "status": "complete" if completed == total_periods else "partial_resume_required",
            "source_kind": source,
            "source_page": source_page,
            "endpoint": LUNAR_ENDPOINT if source == "lunar" else SOLAR_TERM_ENDPOINT,
            "collector_version": COLLECTOR_VERSION,
            "collector_sha256": _sha256(Path(__file__)),
            "start_year": SUPPORTED_START_YEAR,
            "end_year": SUPPORTED_END_YEAR,
            "expected_periods": total_periods,
            "completed_periods": completed,
            "expected_rows": 54_787 if source == "lunar" else 3_600,
            "rows": total_rows,
            "requests_this_run": requested,
            "snapshot_sha256": _sha256(snapshot),
            "credential_source": "private_file" if service_key_file is not None or default_service_key_path().exists() else "environment",
            "credential_value_recorded": False,
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        _write_manifest(manifest_path, manifest)
        if completed < total_periods and requested < max_requests and request_interval:
            time.sleep(request_interval)
    return _load_private_manifest(manifest_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KASI runtime v1.1 공식 snapshot 수집기")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--source", choices=["lunar", "solar-terms"], required=True)
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--service-key-file", type=Path)
    collect_parser.add_argument("--max-requests", type=int, default=10_000)
    collect_parser.add_argument("--request-interval", type=float, default=0.15)
    collect_parser.add_argument("--timeout", type=float, default=30.0)
    collect_parser.add_argument("--confirm-network", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            result = collection_plan()
        else:
            result = collect(
                source=args.source,
                output=args.output,
                service_key_file=args.service_key_file,
                max_requests=args.max_requests,
                request_interval=args.request_interval,
                timeout=args.timeout,
                confirmation=args.confirm_network,
            )
    except (KasiCollectorV11Error, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
