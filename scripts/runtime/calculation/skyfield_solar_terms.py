# skyfield_solar_terms.py - 고정 DE440s와 Skyfield 내장 UT1로 절입을 계산한다.

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import stat
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from threading import RLock
from typing import Any

from .errors import RuntimeCalculationError
from .solar_term_types import (
    FORECAST_DIAGNOSTIC_NONAPPROVAL,
    PAST_OFFICIAL_CORROBORATED,
    PROFILE_DETERMINISTIC,
    SOURCE_HARD_FACT,
    SolarTermBoundary,
)
from .solar_terms import JIE_TO_MONTH, SOLAR_TERM_NAMES

UTC = timezone.utc
FIXED_KST = timezone(timedelta(hours=9))
SKYFIELD_VERSION = "1.55"
JPLEPHEM_VERSION = "2.24"
NUMPY_VERSION = "2.2.6"
DE440S_FILENAME = "de440s.bsp"
DE440S_SHA256 = "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"
DE440S_BYTES = 32_726_016
DE440S_START_YEAR = 1849
DE440S_END_YEAR = 2150
RUNTIME_MINIMUM_BOUNDARY_YEAR = 1899
RUNTIME_MAXIMUM_BOUNDARY_YEAR = 2050
ROOT_BISECTION_ITERATIONS = 48
OFFICIAL_START_YEAR = 1920
OFFICIAL_END_YEAR = 2100
OFFICIAL_SNAPSHOT_COLLECTED_AT = "2026-08-31T15:16:50+00:00"
OFFICIAL_SNAPSHOT_COLLECTED_AT_KST = datetime.fromisoformat(
    OFFICIAL_SNAPSHOT_COLLECTED_AT
).astimezone(FIXED_KST)
SKYFIELD_BUILTIN_DATA_SHA256 = {
    "delta_t.npz": "2d12bd3e789543b78a1f53c8b76ed7fecffdf7e5149cfb6a0aed21a8b3db5ff6",
    "historic_deltat.npy": "f5346b780b36a0325b1847dc6c0083d66edc7e88b7f648b4c98a67bbd02b5d3f",
    "iers.npz": "c7d7536d898dfa9f8cd43e8044ff51e108cc8289675a13fee9822010a1c4935c",
    "morrison_stephenson_deltat.npy": "439e05269890df41dd75820f3f2ef467539e36eb36753e8ee8b4b78d29baad52",
}


def _read_regular_file(path: Path, *, expected_bytes: int | None, maximum: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > maximum
            or (expected_bytes is not None and metadata.st_size != expected_bytes)
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_SOURCE_INVALID",
                f"절입 원천 파일 크기·형식이 다릅니다: {path.name}",
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
    except OSError as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_SOURCE_INVALID",
            f"절입 원천 파일을 안전하게 읽지 못했습니다: {path.name}",
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > maximum:
        raise RuntimeCalculationError(
            "SOLAR_TERM_SOURCE_INVALID", "절입 원천 파일이 허용 크기를 넘습니다."
        )
    return payload


def validate_de440s(path: Path) -> dict[str, Any]:
    """runtime DE440s 경로와 byte identity를 symlink 없이 검증한다."""

    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_EPHEMERIS_REQUIRED", "고정 DE440s 파일이 없습니다."
        ) from exc
    if (
        not candidate.is_absolute()
        or candidate != resolved
        or candidate.name != DE440S_FILENAME
    ):
        raise RuntimeCalculationError(
            "SOLAR_TERM_SOURCE_INVALID",
            "DE440s는 symlink·상대경로가 아닌 de440s.bsp 절대경로여야 합니다.",
        )
    payload = _read_regular_file(
        candidate, expected_bytes=DE440S_BYTES, maximum=DE440S_BYTES
    )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != DE440S_SHA256:
        raise RuntimeCalculationError(
            "SOLAR_TERM_SOURCE_INVALID", "DE440s SHA-256이 고정값과 다릅니다."
        )
    return {
        "filename": DE440S_FILENAME,
        "bytes": len(payload),
        "sha256": digest,
        "coverage_years": [DE440S_START_YEAR, DE440S_END_YEAR],
        "local_path_recorded": False,
    }


def _dependency_identity() -> dict[str, str]:
    expected = {
        "skyfield": SKYFIELD_VERSION,
        "jplephem": JPLEPHEM_VERSION,
        "numpy": NUMPY_VERSION,
    }
    try:
        actual = {name: importlib.metadata.version(name) for name in expected}
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_DEPENDENCY_MISSING", "Skyfield 절입 의존성이 없습니다."
        ) from exc
    if actual != expected:
        raise RuntimeCalculationError(
            "SOLAR_TERM_VERSION_MISMATCH",
            f"Skyfield 절입 의존성 버전이 다릅니다: {actual}",
        )
    return actual


def _builtin_timescale_identity() -> dict[str, Any]:
    try:
        import skyfield
    except ImportError as exc:
        raise RuntimeCalculationError(
            "SOLAR_TERM_DEPENDENCY_MISSING", "Skyfield package가 없습니다."
        ) from exc
    if getattr(skyfield, "__version__", None) != SKYFIELD_VERSION:
        raise RuntimeCalculationError(
            "SOLAR_TERM_VERSION_MISMATCH", "Skyfield package version이 다릅니다."
        )
    root = Path(skyfield.__file__).resolve().parent / "data"
    actual: dict[str, str] = {}
    for filename in sorted(SKYFIELD_BUILTIN_DATA_SHA256):
        payload = _read_regular_file(
            root / filename, expected_bytes=None, maximum=8 * 1024 * 1024
        )
        actual[filename] = hashlib.sha256(payload).hexdigest()
    if actual != SKYFIELD_BUILTIN_DATA_SHA256:
        raise RuntimeCalculationError(
            "SOLAR_TERM_SOURCE_INVALID", "Skyfield 내장 시간자료 hash가 다릅니다."
        )
    return {
        "package_version": SKYFIELD_VERSION,
        "mode": "builtin_no_network",
        "files_sha256": actual,
    }


def _rounded_fixed_kst_minute(value: datetime) -> str:
    base = value.replace(second=0, microsecond=0)
    elapsed = value.second + value.microsecond / 1_000_000
    rounded = base + (timedelta(minutes=1) if elapsed >= 30.0 else timedelta())
    return rounded.isoformat(timespec="minutes")


def _ut1_fixed_kst_datetimes(times) -> list[datetime]:
    calendar = times.ut1_calendar()
    values: list[datetime] = []
    for parts in zip(*calendar, strict=True):
        year, month, day, hour, minute, second = parts
        try:
            nominal = datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute),
                tzinfo=UTC,
            ) + timedelta(seconds=float(second))
        except (OverflowError, TypeError, ValueError) as exc:
            raise RuntimeCalculationError(
                "SOLAR_TERM_RESOLUTION_FAILED", "UT1 달력 좌표를 만들지 못했습니다."
            ) from exc
        values.append((nominal + timedelta(hours=9)).replace(tzinfo=FIXED_KST))
    return values


class SkyfieldSolarTermProvider:
    """TT root 판정과 UT1+고정 KST 공식 라벨을 분리한 candidate provider."""

    provider_id = "skyfield-1.55-jpl-de440s-builtin-ut1"

    def __init__(self, ephemeris_path: Path) -> None:
        self._path = Path(ephemeris_path)
        self._ephemeris_identity = validate_de440s(self._path)
        self._dependencies = _dependency_identity()
        self._timescale_identity = _builtin_timescale_identity()
        try:
            import numpy as np
            from skyfield.api import load, load_file
            from skyfield.framelib import ecliptic_frame
        except ImportError as exc:
            raise RuntimeCalculationError(
                "SOLAR_TERM_DEPENDENCY_MISSING", "Skyfield 절입 모듈을 import하지 못했습니다."
            ) from exc
        try:
            ephemeris = load_file(str(self._path))
            earth = ephemeris["earth"]
            sun = ephemeris["sun"]
            timescale = load.timescale(builtin=True)
        except Exception as exc:
            raise RuntimeCalculationError(
                "SOLAR_TERM_SOURCE_INVALID", "DE440s를 Skyfield로 열지 못했습니다."
            ) from exc
        self._np = np
        self._ecliptic_frame = ecliptic_frame
        self._ephemeris = ephemeris
        self._earth = earth
        self._sun = sun
        self._timescale = timescale
        self._cache: dict[int, tuple[SolarTermBoundary, ...]] = {}
        self._lock = RLock()
        self._closed = False

    def __enter__(self) -> SkyfieldSolarTermProvider:  # noqa: PYI034
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._ephemeris.close()
                self._cache.clear()
                self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeCalculationError(
                "SOLAR_TERM_PROVIDER_CLOSED", "종료된 절입 provider는 사용할 수 없습니다."
            )

    def identity(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "packages": dict(self._dependencies),
            "ephemeris": dict(self._ephemeris_identity),
            "timescale": dict(self._timescale_identity),
            "root_time_scale": "TT",
            "root_bracket": "fixed_calendar_center_plus_minus_5_days",
            "root_iterations": ROOT_BISECTION_ITERATIONS,
            "boundary_comparison_time_scale": "TT",
            "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
            "automatic_download_or_fallback": False,
            "astronomy_engine_fallback": False,
        }

    def evidence_context(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "root_time_scale": "TT",
            "boundary_comparison_time_scale": "TT",
            "official_label_coordinate": "UT1_NOMINAL_PLUS_FIXED_KST",
            "official_snapshot_collected_at": OFFICIAL_SNAPSHOT_COLLECTED_AT,
            "provider_generated_value_is_official": False,
        }

    def boundary(self, year: int, term_index: int) -> SolarTermBoundary:
        if (
            not RUNTIME_MINIMUM_BOUNDARY_YEAR <= year <= RUNTIME_MAXIMUM_BOUNDARY_YEAR
            or not 0 <= term_index <= 23
        ):
            raise RuntimeCalculationError(
                "UNSUPPORTED_SOLAR_TERM", "절입 계산 범위를 벗어났습니다."
            )
        with self._lock:
            self._ensure_open()
            boundaries = self._cache.get(year)
            if boundaries is None:
                boundaries = self._compute_year(year)
                self._cache[year] = boundaries
            return boundaries[term_index]

    def _compute_year(self, year: int) -> tuple[SolarTermBoundary, ...]:
        np = self._np
        indices = list(range(24))
        centers = np.array(
            [
                datetime(
                    year,
                    index // 2 + 1,
                    7 if index % 2 == 0 else 22,
                    tzinfo=UTC,
                ).timestamp()
                for index in indices
            ],
            dtype=np.float64,
        )
        targets = np.array(
            [(285.0 + 15.0 * index) % 360.0 for index in indices],
            dtype=np.float64,
        )

        def signed_delta(timestamps):
            datetimes = [
                datetime.fromtimestamp(float(timestamp), UTC)
                for timestamp in timestamps
            ]
            times = self._timescale.from_datetimes(datetimes)
            longitude = (
                self._earth.at(times)
                .observe(self._sun)
                .apparent()
                .frame_latlon(self._ecliptic_frame)[1]
                .degrees
            )
            return (longitude - targets + 180.0) % 360.0 - 180.0

        left = centers - 5.0 * 86_400.0
        right = centers + 5.0 * 86_400.0
        if bool(np.any(signed_delta(left) >= 0.0)) or bool(
            np.any(signed_delta(right) <= 0.0)
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_RESOLUTION_FAILED", "고정 bracket이 절입 root를 감싸지 않습니다."
            )
        for _ in range(ROOT_BISECTION_ITERATIONS):
            middle = (left + right) / 2.0
            crossed = signed_delta(middle) >= 0.0
            right = np.where(crossed, middle, right)
            left = np.where(crossed, left, middle)
        instants = [
            datetime.fromtimestamp(float(timestamp), UTC)
            for timestamp in (left + right) / 2.0
        ]
        times = self._timescale.from_datetimes(instants)
        fixed_kst = _ut1_fixed_kst_datetimes(times)
        boundaries: list[SolarTermBoundary] = []
        for order, index in enumerate(indices):
            display_minute = _rounded_fixed_kst_minute(fixed_kst[order])
            display_value = datetime.fromisoformat(display_minute)
            if year < OFFICIAL_START_YEAR:
                authority = PROFILE_DETERMINISTIC
                official_class = None
            elif display_value <= OFFICIAL_SNAPSHOT_COLLECTED_AT_KST:
                authority = PAST_OFFICIAL_CORROBORATED
                official_class = SOURCE_HARD_FACT
            else:
                authority = FORECAST_DIAGNOSTIC_NONAPPROVAL
                official_class = (
                    SOURCE_HARD_FACT if year <= OFFICIAL_END_YEAR else None
                )
            boundaries.append(
                SolarTermBoundary(
                    provider_id=self.provider_id,
                    year=year,
                    term_index=index,
                    term_name=SOLAR_TERM_NAMES[index],
                    saju_month_number=JIE_TO_MONTH.get(index),
                    instant_utc=instants[order],
                    tt_whole=int(times.whole[order]),
                    tt_fraction=float(times.tt_fraction[order]),
                    official_display_minute_fixed_kst=display_minute,
                    authority_class=authority,
                    official_source_evidence_class=official_class,
                )
            )
        if any(
            current.tt_sort_key >= following.tt_sort_key
            for current, following in pairwise(boundaries)
        ):
            raise RuntimeCalculationError(
                "SOLAR_TERM_RESOLUTION_FAILED", "절입 root 순서가 증가하지 않습니다."
            )
        return tuple(boundaries)

    def compare_instant(
        self, instant: datetime, boundary: SolarTermBoundary
    ) -> int:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise RuntimeCalculationError(
                "INVALID_INSTANT", "절입 비교 instant에는 timezone이 필요합니다."
            )
        with self._lock:
            self._ensure_open()
            value = self._timescale.from_datetime(instant.astimezone(UTC))
            delta = (float(value.whole) - boundary.tt_whole) + (
                float(value.tt_fraction) - boundary.tt_fraction
            )
        if delta < 0.0:
            return -1
        if delta > 0.0:
            return 1
        return 0
