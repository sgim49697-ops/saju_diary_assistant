# strict_json.py - conformance 입력의 중첩 JSON 중복 key를 일관되게 거부한다.

from __future__ import annotations

import json
from typing import Any


class DuplicateJsonKeyError(ValueError):
    """같은 JSON object 안에 동일한 key가 둘 이상 있음."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKeyError(key)
        value[key] = item
    return value


def loads_without_duplicate_keys(payload: str | bytes) -> Any:
    """모든 중첩 object에서 중복 key를 거부하며 JSON을 읽는다."""

    return json.loads(payload, object_pairs_hook=_object_without_duplicates)
