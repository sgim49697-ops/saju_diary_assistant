# kasi_term_coverage_collector_v1_2.py - KASI 24절기 API의 실제 연도별 제공 범위를 보존한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.evaluation.saju_runtime.kasi_collector_v1_1 import (
    SOLAR_TERM_ENDPOINT,
    SOLAR_TERM_SOURCE_PAGE,
    _request,
    _response_items,
    default_service_key_path,
    load_service_key,
    parse_solar_term_year,
)
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import SOLAR_TERM_NAMES

COLLECTOR_VERSION = "kasi-term-coverage-collector-v1.2.0"
CONFIRMATION = "COLLECT_KASI_TERM_COVERAGE_V1_2"
ALLOWED_ROOT = REPO_ROOT / "data/raw/saju_runtime/kasi/v1.2.0"
START_YEAR = 1900
END_YEAR = 2049
EXPECTED_YEARS = END_YEAR - START_YEAR + 1
CONTRACT_EXPECTED_ROWS = EXPECTED_YEARS * 24
MAX_PRIVATE_FILE_BYTES = 128 * 1024 * 1024

SCAN_FILENAME = "kasi_solar_term_scan.jsonl"
SNAPSHOT_FILENAME = "kasi_solar_terms.jsonl"
MANIFEST_FILENAME = "kasi_solar_terms_manifest.json"


class KasiTermCoverageCollectorError(RuntimeError):
    """KASI 24절기 범위 scan과 snapshot provenance 계약 위반."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise KasiTermCoverageCollectorError("hash 대상이 일반 파일이 아닙니다.")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise KasiTermCoverageCollectorError("hash 대상을 읽지 못했습니다.") from exc
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
        raise KasiTermCoverageCollectorError(
            f"출력은 {ALLOWED_ROOT} 아래여야 합니다."
        ) from exc
    current = REPO_ROOT.absolute()
    for part in root.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise KasiTermCoverageCollectorError("KASI raw root에 symlink가 있습니다.")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise KasiTermCoverageCollectorError("출력 경로에 symlink가 있습니다.")
    resolved = directory.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_ROOT.resolve(strict=False))
    except ValueError as exc:
        raise KasiTermCoverageCollectorError("출력 경로가 raw root를 벗어납니다.") from exc
    return resolved


def _private_bytes(path: Path, *, allow_empty: bool = False) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        minimum = 0 if allow_empty else 1
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not minimum <= metadata.st_size <= MAX_PRIVATE_FILE_BYTES
        ):
            raise KasiTermCoverageCollectorError(
                "기존 raw 파일의 소유자·권한·크기가 다릅니다."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read(MAX_PRIVATE_FILE_BYTES + 1)
    except OSError as exc:
        raise KasiTermCoverageCollectorError("기존 raw 파일을 읽지 못했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = _private_bytes(path, allow_empty=True)
    if len(payload) > MAX_PRIVATE_FILE_BYTES:
        raise KasiTermCoverageCollectorError("기존 scan 크기가 제한을 넘습니다.")
    rows: list[dict[str, Any]] = []
    try:
        for number, line in enumerate(payload.decode("utf-8").splitlines(), 1):
            if not line:
                raise KasiTermCoverageCollectorError(
                    f"기존 scan에 빈 행이 있습니다: {number}"
                )
            value = json.loads(line)
            if not isinstance(value, dict):
                raise KasiTermCoverageCollectorError(
                    f"기존 scan 행이 object가 아닙니다: {number}"
                )
            rows.append(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise KasiTermCoverageCollectorError("기존 scan JSONL을 읽지 못했습니다.") from exc
    return rows


def _canonical_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
        for row in rows
    )


def _append_scan(path: Path, row: dict[str, Any]) -> None:
    payload = _canonical_jsonl([row])
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise KasiTermCoverageCollectorError("scan은 현재 사용자 0600 파일이어야 합니다.")
        with os.fdopen(descriptor, "ab") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _replace_private(path: Path, payload: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise KasiTermCoverageCollectorError("출력 파일 경로가 일반 파일이 아닙니다.")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise KasiTermCoverageCollectorError("이전 임시 파일이 남아 있습니다.")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
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


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_private_bytes(path))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise KasiTermCoverageCollectorError("기존 manifest를 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise KasiTermCoverageCollectorError("기존 manifest 최상위가 object가 아닙니다.")
    return value


def _coverage_ranges(years: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for year in years:
        if not ranges or year != ranges[-1][1] + 1:
            ranges.append([year, year])
        else:
            ranges[-1][1] = year
    return ranges


def _validated_scan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    for offset, row in enumerate(rows):
        year = START_YEAR + offset
        row_terms = row.get("terms")
        if (
            row.get("schema_version") != "1.2.0"
            or row.get("year") != year
            or row.get("source_id") != "kasi_24_divisions_openapi"
            or row.get("item_count") not in {0, 24}
            or not isinstance(row.get("response_sha256"), str)
            or len(row["response_sha256"]) != 64
            or not isinstance(row_terms, list)
            or len(row_terms) != row["item_count"]
        ):
            raise KasiTermCoverageCollectorError(f"기존 scan 행이 다릅니다: {year}")
        if row_terms:
            expected = list(range(24))
            if (
                [item.get("term_index") for item in row_terms] != expected
                or any(
                    item.get("year") != year
                    or item.get("term_name") != SOLAR_TERM_NAMES[item["term_index"]]
                    or item.get("source_id") != "kasi_24_divisions_openapi"
                    for item in row_terms
                )
            ):
                raise KasiTermCoverageCollectorError(
                    f"기존 scan의 24절기 block이 다릅니다: {year}"
                )
            terms.extend(row_terms)
    if len(rows) > EXPECTED_YEARS:
        raise KasiTermCoverageCollectorError("기존 scan이 요청 범위를 넘습니다.")
    return terms


def _manifest(
    *,
    scan_path: Path,
    snapshot_path: Path,
    scan_rows: list[dict[str, Any]],
    terms: list[dict[str, Any]],
    requests_this_run: int,
) -> dict[str, Any]:
    complete = len(scan_rows) == EXPECTED_YEARS
    supported_years = [row["year"] for row in scan_rows if row["item_count"] == 24]
    return {
        "schema_version": "1.2.0",
        "status": "complete_api_range_scan" if complete else "partial_resume_required",
        "source_kind": "solar-terms-api-coverage-scan",
        "source_page": SOLAR_TERM_SOURCE_PAGE,
        "endpoint": SOLAR_TERM_ENDPOINT,
        "collector_version": COLLECTOR_VERSION,
        "collector_sha256": _sha256_file(Path(__file__)),
        "transport_collector_path": "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py",
        "transport_collector_sha256": _sha256_file(
            REPO_ROOT / "scripts/evaluation/saju_runtime/kasi_collector_v1_1.py"
        ),
        "start_year": START_YEAR,
        "end_year": END_YEAR,
        "expected_periods": EXPECTED_YEARS,
        "completed_periods": len(scan_rows),
        "contract_expected_rows": CONTRACT_EXPECTED_ROWS,
        "rows": len(terms),
        "supported_years": supported_years,
        "supported_year_ranges": _coverage_ranges(supported_years),
        "missing_years": len(scan_rows) - len(supported_years),
        "api_range_scan_complete": complete,
        "contract_coverage_complete": complete
        and len(terms) == CONTRACT_EXPECTED_ROWS,
        "scan_sha256": _sha256_file(scan_path),
        "snapshot_sha256": _sha256_file(snapshot_path)
        if complete and snapshot_path.exists()
        else None,
        "requests_this_run": requests_this_run,
        "credential_source": "private_file"
        if default_service_key_path().exists()
        else "environment",
        "credential_value_recorded": False,
        "unsupported_years_filled_from_provider": False,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def collect(
    *,
    output: Path,
    service_key_file: Path | None,
    max_requests: int,
    request_interval: float,
    timeout: float,
    confirmation: str,
) -> dict[str, Any]:
    if confirmation != CONFIRMATION:
        raise KasiTermCoverageCollectorError(
            f"network 수집에는 --confirm-network {CONFIRMATION}가 필요합니다."
        )
    if not 1 <= max_requests <= 10_000 or request_interval < 0 or timeout <= 0:
        raise KasiTermCoverageCollectorError("요청 수·간격·timeout이 올바르지 않습니다.")
    directory = _safe_output(output)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    scan_path = directory / SCAN_FILENAME
    snapshot_path = directory / SNAPSHOT_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    scan_rows = _jsonl_rows(scan_path)
    terms = _validated_scan(scan_rows)
    if manifest_path.exists():
        manifest = _load_manifest(manifest_path)
        if (
            manifest.get("collector_version") != COLLECTOR_VERSION
            or manifest.get("completed_periods") != len(scan_rows)
            or manifest.get("scan_sha256") != _sha256_file(scan_path)
            or manifest.get("credential_value_recorded") is not False
        ):
            raise KasiTermCoverageCollectorError("기존 scan resume provenance가 다릅니다.")
    elif scan_rows or snapshot_path.exists():
        raise KasiTermCoverageCollectorError("resume에는 scan과 manifest가 함께 필요합니다.")
    if len(scan_rows) == EXPECTED_YEARS:
        payload = _canonical_jsonl(terms)
        if not snapshot_path.exists():
            _replace_private(snapshot_path, payload)
        elif _private_bytes(snapshot_path, allow_empty=True) != payload:
            raise KasiTermCoverageCollectorError("기존 완성 snapshot이 scan과 다릅니다.")
        return _manifest(
            scan_path=scan_path,
            snapshot_path=snapshot_path,
            scan_rows=scan_rows,
            terms=terms,
            requests_this_run=0,
        )

    key = load_service_key(service_key_file)
    requests = 0
    while len(scan_rows) < EXPECTED_YEARS and requests < max_requests:
        year = START_YEAR + len(scan_rows)
        payload = _request(
            SOLAR_TERM_ENDPOINT,
            {"solYear": f"{year:04d}", "pageNo": "1", "numOfRows": "30"},
            key,
            timeout=timeout,
        )
        item_count = len(_response_items(payload))
        if item_count not in {0, 24}:
            raise KasiTermCoverageCollectorError(
                f"KASI {year}년 응답이 0건 또는 24건이 아닙니다: {item_count}"
            )
        row_terms = [] if item_count == 0 else parse_solar_term_year(payload, year)
        scan_row = {
            "schema_version": "1.2.0",
            "year": year,
            "item_count": item_count,
            "response_sha256": _sha256_bytes(payload),
            "terms": row_terms,
            "source_id": "kasi_24_divisions_openapi",
        }
        _append_scan(scan_path, scan_row)
        scan_rows.append(scan_row)
        terms.extend(row_terms)
        requests += 1
        complete = len(scan_rows) == EXPECTED_YEARS
        if complete:
            _replace_private(snapshot_path, _canonical_jsonl(terms))
        current = _manifest(
            scan_path=scan_path,
            snapshot_path=snapshot_path,
            scan_rows=scan_rows,
            terms=terms,
            requests_this_run=requests,
        )
        _replace_private(
            manifest_path,
            (json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        if not complete and requests < max_requests and request_interval:
            time.sleep(request_interval)
    return _load_manifest(manifest_path)


def collection_plan() -> dict[str, Any]:
    return {
        "status": "network_not_started",
        "collector_version": COLLECTOR_VERSION,
        "requested_years": [START_YEAR, END_YEAR],
        "requests": EXPECTED_YEARS,
        "contract_expected_rows": CONTRACT_EXPECTED_ROWS,
        "observed_coverage_not_assumed": True,
        "unsupported_years_filled_from_provider": False,
        "credential": {
            "preferred_path": str(default_service_key_path()),
            "value_exposed": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KASI 24절기 실제 제공 범위 수집기")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    collect_parser = commands.add_parser("collect")
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
        result = (
            collection_plan()
            if args.command == "plan"
            else collect(
                output=args.output,
                service_key_file=args.service_key_file,
                max_requests=args.max_requests,
                request_interval=args.request_interval,
                timeout=args.timeout,
                confirmation=args.confirm_network,
            )
        )
    except (KasiTermCoverageCollectorError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
