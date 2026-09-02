# security.py - relation snapshot 내부 ID를 release별 HMAC domain으로 만든다.

from __future__ import annotations

import hmac
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.chart_only_security import SecretKey

from .errors import RelationRuntimeError

KEY_BYTES = 32
MESSAGE_PREFIX = b"saju-natal-day-relation-id-v1\x00"


class RelationIdSigner:
    """Relation ID 전용 HMAC signer. 공개 결과에는 ID를 포함하지 않는다."""

    def __init__(self, key: bytes, *, production_key: bool) -> None:
        if not isinstance(key, bytes) or len(key) != KEY_BYTES:
            raise RelationRuntimeError(
                "RELATION_ID_KEY_INVALID", "relation ID key는 정확히 32바이트여야 합니다."
            )
        self._key = key
        self.production_key = bool(production_key)

    @classmethod
    def for_test(cls, key: bytes) -> RelationIdSigner:
        return cls(key, production_key=False)

    @classmethod
    def from_runtime_secret(cls, secret: SecretKey) -> RelationIdSigner:
        if not isinstance(secret, SecretKey) or secret.purpose != "runtime-hmac":
            raise RelationRuntimeError(
                "RELATION_ID_KEY_INVALID", "검증된 runtime-hmac key가 필요합니다."
            )
        return cls(secret.material, production_key=True)

    def relation_id(self, release_id: str, value: Any) -> str:
        if not isinstance(release_id, str) or not release_id:
            raise RelationRuntimeError(
                "RELATION_RELEASE_ID_INVALID", "relation release ID가 필요합니다."
            )
        message = (
            MESSAGE_PREFIX
            + release_id.encode("ascii")
            + b"\x00"
            + canonical_json_bytes(value)
        )
        return "sr1_" + hmac.digest(self._key, message, "sha256").hex()
