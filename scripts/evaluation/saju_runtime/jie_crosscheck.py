# jie_crosscheck.py - Astronomy Engine 절입을 Skyfield/JPL DE440s와 독립 교차검증한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.solar_terms import (
    JIE_TO_MONTH,
    SOLAR_TERM_NAMES,
    solar_term_instant,
)

CROSSCHECK_VERSION = "saju-jie-crosscheck-v1.1.0"
SKYFIELD_VERSION = "1.55"
JPLEPHEM_VERSION = "2.24"
NUMPY_VERSION = "2.2.6"
DE440S_URL = "https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440s.bsp"
DE440S_SHA256 = "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"
DE440S_BYTES = 32_726_016
DE440S_START_YEAR = 1849
DE440S_END_YEAR = 2150
FETCH_CONFIRMATION = "FETCH_JPL_DE440S_V1_1"
ALLOWED_EPHEMERIS_ROOT = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0"
UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")


class JieCrosscheckError(RuntimeError):
    """절입 독립 교차검증 계약 위반."""


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        del request, file_pointer, code, message, headers, new_url
        raise JieCrosscheckError("DE440s 다운로드 redirect를 허용하지 않습니다.")


EPHEMERIS_OPENER = urllib.request.build_opener(_RejectRedirect())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise JieCrosscheckError("ephemeris 파일을 읽지 못했습니다.") from exc
    return digest.hexdigest()


def validate_ephemeris(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise JieCrosscheckError("DE440s가 없거나 symlink입니다.")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise JieCrosscheckError("DE440s metadata를 읽지 못했습니다.") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != DE440S_BYTES:
        raise JieCrosscheckError("DE440s 파일 크기가 고정값과 다릅니다.")
    digest = sha256_file(path)
    if digest != DE440S_SHA256:
        raise JieCrosscheckError("DE440s SHA-256이 고정값과 다릅니다.")
    return {
        "filename": "de440s.bsp",
        "bytes": metadata.st_size,
        "sha256": digest,
        "coverage_years": [DE440S_START_YEAR, DE440S_END_YEAR],
        "local_path_recorded": False,
    }


def _safe_fetch_target(path: Path) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(ALLOWED_EPHEMERIS_ROOT.resolve())
    except ValueError as exc:
        raise JieCrosscheckError(
            f"DE440s 출력은 {ALLOWED_EPHEMERIS_ROOT} 아래여야 합니다."
        ) from exc
    current = ALLOWED_EPHEMERIS_ROOT.resolve()
    for part in resolved.relative_to(current).parts:
        current /= part
        if current.is_symlink():
            raise JieCrosscheckError("DE440s 출력 경로에 symlink가 포함됐습니다.")
    if resolved.name != "de440s.bsp":
        raise JieCrosscheckError("DE440s 출력 파일명은 de440s.bsp여야 합니다.")
    return resolved


def fetch_ephemeris(path: Path, confirmation: str, timeout: float) -> dict[str, Any]:
    if confirmation != FETCH_CONFIRMATION:
        raise JieCrosscheckError(
            f"network 다운로드에는 --confirm-network {FETCH_CONFIRMATION}가 필요합니다."
        )
    if timeout <= 0:
        raise JieCrosscheckError("timeout이 올바르지 않습니다.")
    target = _safe_fetch_target(path)
    if target.exists() or target.is_symlink():
        return validate_ephemeris(target)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.parent.chmod(0o700)
    temporary = target.with_name(".de440s.bsp.part")
    if temporary.exists() or temporary.is_symlink():
        raise JieCrosscheckError("이전 DE440s 임시 파일이 남아 있습니다.")
    request = urllib.request.Request(
        DE440S_URL,
        headers={"User-Agent": "saju-runtime-jie-crosscheck/1.1"},
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with EPHEMERIS_OPENER.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise JieCrosscheckError(
                    f"DE440s HTTP status가 {response.status}입니다."
                )
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != DE440S_BYTES:
                raise JieCrosscheckError("DE440s Content-Length가 고정값과 다릅니다.")
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                remaining = DE440S_BYTES + 1
                while remaining:
                    chunk = response.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    stream.write(chunk)
                    remaining -= len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
        validate_ephemeris(temporary)
        os.replace(temporary, target)
        return validate_ephemeris(target)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise JieCrosscheckError("DE440s 다운로드에 실패했습니다.") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _dependency_versions() -> dict[str, str]:
    try:
        import importlib.metadata

        versions = {
            "skyfield": importlib.metadata.version("skyfield"),
            "jplephem": importlib.metadata.version("jplephem"),
            "numpy": importlib.metadata.version("numpy"),
        }
    except importlib.metadata.PackageNotFoundError as exc:
        raise JieCrosscheckError("Skyfield 교차검증 의존성이 없습니다.") from exc
    expected = {
        "skyfield": SKYFIELD_VERSION,
        "jplephem": JPLEPHEM_VERSION,
        "numpy": NUMPY_VERSION,
    }
    if versions != expected:
        raise JieCrosscheckError(
            f"Skyfield 교차검증 의존성 버전이 다릅니다: {versions}"
        )
    return versions


def _skyfield_jie_instants(
    ephemeris_path: Path,
    astronomy_rows: list[dict[str, Any]],
) -> list[datetime]:
    _dependency_versions()
    try:
        import numpy as np
        from skyfield.api import load, load_file
        from skyfield.framelib import ecliptic_frame
    except ImportError as exc:
        raise JieCrosscheckError("Skyfield 모듈을 import하지 못했습니다.") from exc
    ephemeris = load_file(str(ephemeris_path))
    try:
        earth = ephemeris["earth"]
        sun = ephemeris["sun"]
        timescale = load.timescale(builtin=True)
        centers = np.array(
            [row["astronomy_instant"].timestamp() for row in astronomy_rows],
            dtype=np.float64,
        )
        targets = np.array(
            [(285.0 + 15.0 * row["term_index"]) % 360.0 for row in astronomy_rows],
            dtype=np.float64,
        )

        def signed_delta(timestamps):
            datetimes = [datetime.fromtimestamp(float(value), UTC) for value in timestamps]
            times = timescale.from_datetimes(datetimes)
            apparent = earth.at(times).observe(sun).apparent()
            longitude = apparent.frame_latlon(ecliptic_frame)[1].degrees
            return (longitude - targets + 180.0) % 360.0 - 180.0

        left = centers - 86_400.0
        right = centers + 86_400.0
        left_delta = signed_delta(left)
        right_delta = signed_delta(right)
        if bool(np.any(left_delta >= 0.0)) or bool(np.any(right_delta <= 0.0)):
            raise JieCrosscheckError(
                "Skyfield 황경 root가 Astronomy Engine ±24시간 bracket에 없습니다."
            )
        for _ in range(48):
            middle = (left + right) / 2.0
            delta = signed_delta(middle)
            crossed = delta >= 0.0
            right = np.where(crossed, middle, right)
            left = np.where(crossed, left, middle)
        return [datetime.fromtimestamp(float(value), UTC) for value in (left + right) / 2.0]
    finally:
        ephemeris.close()


def compare_jie_boundaries(
    ephemeris_path: Path,
    *,
    start_year: int = 1900,
    end_year: int = 2049,
    maximum_delta_seconds: float = 120.0,
    include_records: bool = False,
) -> dict[str, Any]:
    if (
        start_year < 1900
        or end_year > 2049
        or end_year < start_year
        or maximum_delta_seconds <= 0
    ):
        raise JieCrosscheckError("절입 비교 범위·threshold가 올바르지 않습니다.")
    ephemeris = validate_ephemeris(ephemeris_path)
    astronomy_rows = [
        {
            "year": year,
            "term_index": index,
            "term_name": SOLAR_TERM_NAMES[index],
            "astronomy_instant": solar_term_instant(year, index),
        }
        for year in range(start_year, end_year + 1)
        for index in sorted(JIE_TO_MONTH)
    ]
    skyfield_instants = _skyfield_jie_instants(ephemeris_path, astronomy_rows)
    records: list[dict[str, Any]] = []
    for expected_order, (row, skyfield_instant) in enumerate(
        zip(astronomy_rows, skyfield_instants, strict=True)
    ):
        astronomy_instant = row["astronomy_instant"]
        records.append(
            {
                "order": expected_order,
                "year": row["year"],
                "term_index": row["term_index"],
                "term_name": row["term_name"],
                "astronomy_instant_utc": astronomy_instant.isoformat().replace(
                    "+00:00", "Z"
                ),
                "skyfield_instant_utc": skyfield_instant.isoformat().replace(
                    "+00:00", "Z"
                ),
                "delta_seconds": round(
                    (astronomy_instant - skyfield_instant).total_seconds(), 6
                ),
                "astronomy_local_date": astronomy_instant.astimezone(KST)
                .date()
                .isoformat(),
                "skyfield_local_date": skyfield_instant.astimezone(KST)
                .date()
                .isoformat(),
            }
        )
    absolute = [abs(row["delta_seconds"]) for row in records]
    sorted_absolute = sorted(absolute)
    ordering_failures = sum(
        current["skyfield_instant_utc"] >= following["skyfield_instant_utc"]
        for current, following in pairwise(records)
    )
    identity_failures = sum(
        row["term_index"] not in JIE_TO_MONTH
        or row["term_name"] != SOLAR_TERM_NAMES[row["term_index"]]
        for row in records
    )
    date_mismatches = sum(
        row["astronomy_local_date"] != row["skyfield_local_date"] for row in records
    )
    threshold_failures = sum(value > maximum_delta_seconds for value in absolute)
    result = {
        "schema_version": "1.1.0",
        "crosscheck_version": CROSSCHECK_VERSION,
        "status": "passed"
        if not (ordering_failures or identity_failures or threshold_failures)
        else "failed",
        "start_year": start_year,
        "end_year": end_year,
        "rows": len(records),
        "expected_rows": (end_year - start_year + 1) * 12,
        "maximum_allowed_delta_seconds": maximum_delta_seconds,
        "minimum_signed_delta_seconds": min(row["delta_seconds"] for row in records),
        "maximum_signed_delta_seconds": max(row["delta_seconds"] for row in records),
        "mean_absolute_delta_seconds": round(sum(absolute) / len(absolute), 6),
        "p99_absolute_delta_seconds": sorted_absolute[
            min(len(sorted_absolute) - 1, int(len(sorted_absolute) * 0.99))
        ],
        "threshold_failures": threshold_failures,
        "term_identity_failures": identity_failures,
        "chronological_order_failures": ordering_failures,
        "local_date_mismatches": date_mismatches,
        "local_date_adjudicator": "kasi_24_divisions_openapi",
        "dependency_versions": _dependency_versions(),
        "ephemeris": ephemeris,
        "records_sha256": hashlib.sha256(
            json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "records_in_report": include_records,
    }
    if include_records:
        result["records"] = records
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JPL DE440s 절입 독립 교차검증")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--output", type=Path, required=True)
    fetch.add_argument("--timeout", type=float, default=120.0)
    fetch.add_argument("--confirm-network", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--ephemeris", type=Path, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--ephemeris", type=Path, required=True)
    compare.add_argument("--start-year", type=int, default=1900)
    compare.add_argument("--end-year", type=int, default=2049)
    compare.add_argument("--maximum-delta-seconds", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fetch":
            result = fetch_ephemeris(args.output, args.confirm_network, args.timeout)
        elif args.command == "verify":
            result = validate_ephemeris(args.ephemeris)
        else:
            result = compare_jie_boundaries(
                args.ephemeris,
                start_year=args.start_year,
                end_year=args.end_year,
                maximum_delta_seconds=args.maximum_delta_seconds,
            )
    except JieCrosscheckError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
