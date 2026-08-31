# id_signer.py - 출생 입력에서 파생되는 runtime ID를 domain-separated HMAC으로 만든다.

from __future__ import annotations

import hmac
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .errors import RuntimeCalculationError

ID_CONTRACT_VERSION = "saju-runtime-id-hmac-v2.0.0"
KEY_FILE_ENV = "SAJU_RUNTIME_ID_KEY_FILE"
KEY_BYTES = 32
_MESSAGE_PREFIX = b"saju-runtime-id-hmac-v2\x00"
_DOMAINS = {
    "birth_input_id": ("sbi2_", b"birth-input"),
    "chart_id": ("sc2_", b"chart"),
    "chart_set_id": ("scs2_", b"chart-set"),
    "calculation_run_id": ("scr2_", b"calculation-run"),
    "chart_input_fingerprint": ("sif2_", b"chart-input-fingerprint"),
}
_PRODUCTION_FACTORY = object()


class RuntimeIdSigner:
    """고정 canonical JSON과 용도별 domain으로 HMAC-SHA256 ID를 발급한다."""

    def __init__(
        self,
        key: bytes,
        *,
        production_key: bool,
        _factory_token: object | None = None,
    ) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_INVALID",
                "runtime ID key는 정확히 32바이트여야 합니다.",
            )
        if production_key and _factory_token is not _PRODUCTION_FACTORY:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_INVALID",
                "production signer는 검증된 key 파일 loader로만 만들 수 있습니다.",
            )
        self._key = key
        self.production_key = bool(production_key)

    @classmethod
    def for_test(cls, key: bytes) -> RuntimeIdSigner:
        """테스트·conformance에만 쓰는 명시적 비-production signer다."""

        return cls(key, production_key=False)

    @classmethod
    def from_key_file(
        cls,
        path: Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> RuntimeIdSigner:
        environment = os.environ if environ is None else environ
        configured = environment.get(KEY_FILE_ENV)
        if path is not None and configured is not None:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_SOURCE_AMBIGUOUS",
                "runtime ID key 파일 경로는 인자와 환경변수 중 하나로만 지정해야 합니다.",
            )
        raw_path = str(path) if path is not None else configured
        if not raw_path:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_REQUIRED",
                f"production runtime에는 {KEY_FILE_ENV} 또는 명시적 key 파일 경로가 필요합니다.",
            )
        key_path = Path(raw_path)
        if not key_path.is_absolute():
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_PATH_INVALID",
                "runtime ID key 파일 경로는 절대 경로여야 합니다.",
            )
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(key_path, flags)
        except OSError as exc:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_INVALID",
                "runtime ID key 파일을 안전하게 열지 못했습니다.",
            ) from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise RuntimeCalculationError(
                    "RUNTIME_ID_KEY_INVALID",
                    "runtime ID key는 현재 사용자 소유의 0600 일반 파일이어야 합니다.",
                )
            chunks: list[bytes] = []
            remaining = KEY_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            key = b"".join(chunks)
        except OSError as exc:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_INVALID",
                "runtime ID key 파일을 읽지 못했습니다.",
            ) from exc
        finally:
            os.close(descriptor)
        if len(key) != KEY_BYTES:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KEY_INVALID",
                "runtime ID key 파일은 줄바꿈 없이 정확히 32바이트여야 합니다.",
            )
        return cls(key, production_key=True, _factory_token=_PRODUCTION_FACTORY)

    def sign(self, kind: str, value: Any) -> str:
        try:
            prefix, domain = _DOMAINS[kind]
        except KeyError as exc:
            raise RuntimeCalculationError(
                "RUNTIME_ID_KIND_INVALID", "허용되지 않은 runtime ID 종류입니다."
            ) from exc
        message = _MESSAGE_PREFIX + domain + b"\x00" + canonical_json_bytes(value)
        return prefix + hmac.digest(self._key, message, "sha256").hex()

    def birth_input_id(self, value: Any) -> str:
        return self.sign("birth_input_id", value)

    def chart_id(self, value: Any) -> str:
        return self.sign("chart_id", value)

    def chart_set_id(self, value: Any) -> str:
        return self.sign("chart_set_id", value)

    def calculation_run_id(self, value: Any) -> str:
        return self.sign("calculation_run_id", value)

    def chart_input_fingerprint(self, value: Any) -> str:
        return self.sign("chart_input_fingerprint", value)
