# canonical.py - runtime 입력·결과의 Unicode canonical JSON과 안정 ID를 만든다.

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import RuntimeCalculationError


def _normalize(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise RuntimeCalculationError(
            "NON_CANONICAL_NUMBER",
            "NaN과 Infinity는 canonical JSON에 넣을 수 없습니다.",
        )
    return value


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _normalize(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuntimeCalculationError(
            "NON_CANONICAL_VALUE", "canonical JSON으로 직렬화할 수 없는 값입니다."
        ) from exc


def stable_id(prefix: str, value: Any) -> str:
    if not prefix or not prefix.endswith("_"):
        raise RuntimeCalculationError(
            "INVALID_ID_PREFIX", "ID prefix가 올바르지 않습니다."
        )
    return prefix + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
