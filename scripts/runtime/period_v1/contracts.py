# contracts.py - 기간 요청 v2와 내부 복원 권한의 fail-closed 계약을 검증한다.

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import POLICY_ID, REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import load_strict_json_object_v1_2
from scripts.runtime.calculation.contracts_v1_5 import (
    ENGINE_VERSION_V15,
    RELEASE_V15_PATH,
    validate_release_registry_v1_5,
)

from .errors import PeriodRuntimeError

CONFIG_ROOT = REPO_ROOT / "configs/runtime/period"
REGISTRY_PATH = CONFIG_ROOT / "registry-v1.0.0.json"
REQUEST_SCHEMA_PATH = CONFIG_ROOT / "request_schema-v2.0.0.json"
RESOLVED_SCOPE_SCHEMA_PATH = CONFIG_ROOT / "resolved_scope_schema-v1.0.0.json"
CHART_AUTHORITY_SCHEMA_PATH = CONFIG_ROOT / "chart_authority_schema-v1.0.0.json"
RELEASE_SCHEMA_PATH = CONFIG_ROOT / "release_registry_schema-v1.0.0.json"

REGISTRY_SHA256 = "1b05d27363e0ae8a7f0c3ec763a89fe63f89a567ec6f7084460bf0ae128948ef"
REGISTRY_ID = "saju-period-contract-registry-v1.0.0"
PUBLIC_REQUEST_VERSION = "saju-period-request-v2"
RESOLVED_SCOPE_VERSION = "saju-period-resolved-scope-v1"
CHART_AUTHORIZATION_VERSION = "saju-period-chart-authorization-v1"
PARENT_RELEASE_ID = "saju-runtime-release-v1.5.0-8b1d6ea2d46e"
DATE_EXPRESSIONS = frozenset(
    {"today", "tomorrow", "this_weekend", "this_week", "this_month", "explicit"}
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CHART_ID_PATTERN = re.compile(r"^sc2_[0-9a-f]{64}$")
EXPECTED_ARTIFACTS = {
    "configs/runtime/period/request_schema-v2.0.0.json": (
        "862f80836b0b868868a807d5d5e705a02c733b041f51fc496b6779fba372ba90"
    ),
    "configs/runtime/period/resolved_scope_schema-v1.0.0.json": (
        "3c2b74cece80c16c288d7af3b1bd3676dfedfe3f47060d9d9ffc53b7d50f26a8"
    ),
    "configs/runtime/period/chart_authority_schema-v1.0.0.json": (
        "4458dcd39afe1074f04b29573b4af0aa1e0378246ed231fb2aa2cf765340f370"
    ),
    "configs/runtime/period/release_registry_schema-v1.0.0.json": (
        "c10d860124ad5a6c46dbbac87d83f004f213837cf90d38b2cf12a05b07f9047b"
    ),
}


def _safe_repo_path(relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_PATH_INVALID", "기간 계약 경로가 안전하지 않습니다."
        )
    current = REPO_ROOT
    for part in candidate.parts:
        current /= part
        if current.is_symlink():
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_PATH_INVALID", "기간 계약 경로에 symlink가 있습니다."
            )
    resolved = (REPO_ROOT / candidate).resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_PATH_INVALID", "기간 계약 경로가 저장소를 벗어납니다."
        ) from exc
    return resolved


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PeriodRuntimeError(
                "PERIOD_REQUEST_DUPLICATE_KEY",
                f"기간 요청 JSON에 중복 key가 있습니다: {key}",
            )
        value[key] = item
    return value


def load_public_period_event(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text, object_pairs_hook=_strict_object)
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_JSON_INVALID", "기간 요청 JSON을 읽을 수 없습니다."
        ) from exc
    return validate_public_period_event(value)


def _canonical_date(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_DATE_INVALID", f"{field}는 ISO 날짜 문자열이어야 합니다."
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_DATE_INVALID", f"{field}가 유효한 날짜가 아닙니다."
        ) from exc
    if parsed.isoformat() != value:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_DATE_INVALID", f"{field}는 YYYY-MM-DD 형식이어야 합니다."
        )
    return value


def validate_public_period_event(value: Any) -> dict[str, Any]:
    """모델·브라우저가 opaque ID나 서버 정책을 주입하지 못하게 제한한다."""
    if not isinstance(value, Mapping) or set(value) != {"type", "request"}:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_FIELDS_INVALID", "기간 event field 집합이 다릅니다."
        )
    request = value.get("request")
    if value.get("type") != "request_period" or not isinstance(request, Mapping):
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_TYPE_INVALID", "request_period 구조화 event가 필요합니다."
        )
    if set(request) != {
        "schema_version",
        "date_expression",
        "start_date",
        "end_date",
    }:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_FIELDS_INVALID", "기간 request field 집합이 다릅니다."
        )
    expression = request.get("date_expression")
    if (
        request.get("schema_version") != PUBLIC_REQUEST_VERSION
        or not isinstance(expression, str)
        or expression not in DATE_EXPRESSIONS
    ):
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_EXPRESSION_INVALID", "지원하지 않는 날짜 표현입니다."
        )
    start = request.get("start_date")
    end = request.get("end_date")
    if expression == "explicit":
        start = _canonical_date(start, field="start_date")
        end = _canonical_date(end, field="end_date")
    elif start is not None or end is not None:
        raise PeriodRuntimeError(
            "PERIOD_REQUEST_CROSS_FIELD_INVALID",
            "상대 날짜 표현에는 start_date·end_date를 지정할 수 없습니다.",
        )
    return {
        "type": "request_period",
        "request": {
            "schema_version": PUBLIC_REQUEST_VERSION,
            "date_expression": expression,
            "start_date": start,
            "end_date": end,
        },
    }


def validate_contract_registry() -> dict[str, Any]:
    release = validate_release_registry_v1_5(RELEASE_V15_PATH)
    registry = load_strict_json_object_v1_2(REGISTRY_PATH)
    if (
        sha256_file(REGISTRY_PATH) != REGISTRY_SHA256
        or registry.get("schema_version") != "1.0.0"
        or registry.get("registry_id") != REGISTRY_ID
        or registry.get("status") != "contract_only_feature_default_off"
        or registry.get("parent_runtime_release")
        != {
            "path": str(RELEASE_V15_PATH.relative_to(REPO_ROOT)),
            "release_id": PARENT_RELEASE_ID,
            "sha256": sha256_file(RELEASE_V15_PATH),
        }
        or release.get("release_id") != PARENT_RELEASE_ID
        or registry.get("governance")
        != {
            "feature_flag_default": False,
            "public_request_may_supply_runtime_ids": False,
            "public_request_may_supply_reference_clock": False,
            "process_independent_chart_rehydration_required": True,
            "strict_full_runtime_approved": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generation_allowed": False,
            "training_promotion_allowed": False,
        }
    ):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 계약 registry가 다릅니다."
        )
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 계약 artifact 수가 다릅니다."
        )
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 artifact 형식이 다릅니다."
            )
        relative = artifact.get("path")
        expected = artifact.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in seen
            or EXPECTED_ARTIFACTS.get(relative) != expected
        ):
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 artifact identity가 다릅니다."
            )
        path = _safe_repo_path(relative)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise PeriodRuntimeError(
                "PERIOD_CONTRACT_HASH_MISMATCH", f"기간 artifact hash가 다릅니다: {relative}"
            )
        seen.add(relative)
    if seen != set(EXPECTED_ARTIFACTS):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_REGISTRY_INVALID", "기간 artifact 집합이 다릅니다."
        )

    request_schema = load_strict_json_object_v1_2(REQUEST_SCHEMA_PATH)
    scope_schema = load_strict_json_object_v1_2(RESOLVED_SCOPE_SCHEMA_PATH)
    authority_schema = load_strict_json_object_v1_2(CHART_AUTHORITY_SCHEMA_PATH)
    release_schema = load_strict_json_object_v1_2(RELEASE_SCHEMA_PATH)
    if (
        request_schema.get("$id") != "saju-period-request-v2.0.0"
        or request_schema.get("additionalProperties") is not False
        or scope_schema.get("$id") != "saju-period-resolved-scope-v1.0.0"
        or scope_schema.get("additionalProperties") is not False
        or authority_schema.get("$id") != "saju-period-chart-authority-v1.0.0"
        or authority_schema.get("additionalProperties") is not False
        or release_schema.get("$id")
        != "saju-period-daily-label-release-registry-v1.0.0"
        or release_schema.get("additionalProperties") is not False
    ):
        raise PeriodRuntimeError(
            "PERIOD_CONTRACT_SCHEMA_INVALID", "기간 schema identity가 다릅니다."
        )
    return deepcopy(registry)


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_resolved_scope(value: Any) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "date_expression",
        "start_date",
        "end_date",
        "day_count",
        "timezone",
        "evaluation_local_time",
        "reference_date",
        "intraday_segments_supported",
        "future_physical_instant_claimed",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise PeriodRuntimeError(
            "PERIOD_SCOPE_INVALID", "해석된 기간 field 집합이 다릅니다."
        )
    start = date.fromisoformat(_canonical_date(value.get("start_date"), field="start_date"))
    end = date.fromisoformat(_canonical_date(value.get("end_date"), field="end_date"))
    reference = date.fromisoformat(
        _canonical_date(value.get("reference_date"), field="reference_date")
    )
    count = value.get("day_count")
    if (
        value.get("schema_version") != RESOLVED_SCOPE_VERSION
        or value.get("date_expression") not in DATE_EXPRESSIONS
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count != (end - start).days + 1
        or not 1 <= count <= 31
        or value.get("timezone") != "Asia/Seoul"
        or value.get("evaluation_local_time") != "12:00"
        or value.get("intraday_segments_supported") is not False
        or value.get("future_physical_instant_claimed") is not False
        or isinstance(reference, datetime)
    ):
        raise PeriodRuntimeError("PERIOD_SCOPE_INVALID", "해석된 기간 값이 다릅니다.")
    return deepcopy(dict(value))


def validate_chart_authorization(value: Any) -> dict[str, Any]:
    expected_fields = {
        "authorization_version",
        "chart_id",
        "state_revision",
        "normalized_input_sha256",
        "public_hard_facts_sha256",
        "source_versions_sha256",
        "release_id",
        "engine_version",
        "policy_id",
        "fact_authority",
        "publicly_exposable",
    }
    if not isinstance(value, Mapping) or set(value) != expected_fields:
        raise PeriodRuntimeError(
            "PERIOD_CHART_AUTHORITY_INVALID", "원국 복원 권한 field 집합이 다릅니다."
        )
    revision = value.get("state_revision")
    if (
        value.get("authorization_version") != CHART_AUTHORIZATION_VERSION
        or CHART_ID_PATTERN.fullmatch(str(value.get("chart_id", ""))) is None
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or any(
            SHA256_PATTERN.fullmatch(str(value.get(field, ""))) is None
            for field in (
                "normalized_input_sha256",
                "public_hard_facts_sha256",
                "source_versions_sha256",
            )
        )
        or value.get("release_id") != PARENT_RELEASE_ID
        or value.get("engine_version") != ENGINE_VERSION_V15
        or value.get("policy_id") != POLICY_ID
        or value.get("fact_authority") != "HARD_GT"
        or value.get("publicly_exposable") is not False
    ):
        raise PeriodRuntimeError(
            "PERIOD_CHART_AUTHORITY_INVALID", "원국 복원 권한 값이 다릅니다."
        )
    return deepcopy(dict(value))
