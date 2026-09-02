# chart_day_model_projection.py - Dashboard v1.11의 full-runtime 모델 투영을 고정한다.

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes

MODEL_PROJECTION_ID = "saju-chart-day-model-projection-v1.0.0"


def normalize_model_period_projection(period: Mapping[str, Any]) -> dict[str, Any]:
    """현재 serving과 동일하게 공개 period를 내용 변경 없이 복제한다."""

    return deepcopy(dict(period))


def model_projection_digest(value: Mapping[str, Any]) -> str:
    """Dashboard v1.11과 같은 canonical snapshot digest를 계산한다."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
