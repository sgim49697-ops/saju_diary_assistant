# security.py - 기간 결과 내부 ID를 release별 domain-separated HMAC으로 만든다.

from __future__ import annotations

import hmac
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.chart_only_security import SecretKey

from .errors import PeriodRuntimeError

KEY_BYTES = 32
MESSAGE_PREFIX = b"saju-period-daily-label-id-v1\x00"


class PeriodIdSigner:
    """기간 ID 전용 HMAC signer. 결과 ID는 내부 상태에서만 사용한다."""

    def __init__(self, key: bytes, *, production_key: bool) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise PeriodRuntimeError(
                "PERIOD_ID_KEY_INVALID", "기간 ID key는 정확히 32바이트여야 합니다."
            )
        self._key = key
        self.production_key = bool(production_key)

    @classmethod
    def for_test(cls, key: bytes) -> PeriodIdSigner:
        return cls(key, production_key=False)

    @classmethod
    def from_runtime_secret(cls, secret: SecretKey) -> PeriodIdSigner:
        if not isinstance(secret, SecretKey) or secret.purpose != "runtime-hmac":
            raise PeriodRuntimeError(
                "PERIOD_ID_KEY_INVALID", "검증된 runtime-hmac key가 필요합니다."
            )
        return cls(secret.material, production_key=True)

    def period_id(self, release_id: str, value: Any) -> str:
        if not isinstance(release_id, str) or not release_id:
            raise PeriodRuntimeError(
                "PERIOD_RELEASE_ID_INVALID", "기간 release ID가 필요합니다."
            )
        message = (
            MESSAGE_PREFIX
            + release_id.encode("ascii")
            + b"\x00"
            + canonical_json_bytes(value)
        )
        return "spd1_" + hmac.digest(self._key, message, "sha256").hex()
