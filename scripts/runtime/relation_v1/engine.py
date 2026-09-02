# engine.py - 승인 원국과 단일 날짜 label 사이의 결정론적 관계 존재만 계산한다.

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.contracts import POLICY_ID as CHART_POLICY_ID
from scripts.runtime.calculation.contracts_v1_5 import ENGINE_VERSION_V15
from scripts.runtime.calculation.facts import BRANCHES, STEMS, tables, ten_god
from scripts.runtime.chart_day_adapter import ChartDayAdapterError, public_chart
from scripts.runtime.period_v1.contracts import (
    PARENT_RELEASE_ID,
    RESOLVED_SCOPE_VERSION,
    sha256_value,
    validate_chart_authorization,
)
from scripts.runtime.period_v1.engine import (
    PERIOD_ID_PATTERN,
    public_daily_label_result,
)
from scripts.runtime.period_v1.errors import PeriodRuntimeError
from scripts.runtime.period_v1.security import PeriodIdSigner

from .contracts import (
    CHART_RELEASE_ID,
    PERIOD_RELEASE_ID,
    POLICY_ID,
    TABLE_VERSION,
    TEN_GOD_TABLE_VERSION,
    load_relation_policy,
    validate_contract_registry,
    validate_release_registry,
)
from .errors import RelationRuntimeError
from .security import RelationIdSigner

RELATION_ID_PATTERN = re.compile(r"^sr1_[0-9a-f]{64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PILLAR_ORDER = ("year", "month", "day", "hour")
PERIOD_PART_ORDER = ("year", "month", "day")
COMPONENT_ORDER = ("stem", "branch")
TEN_GOD_LABELS = frozenset(
    {"비견", "겁재", "식신", "상관", "편재", "정재", "편관", "정관", "편인", "정인"}
)
PUBLIC_RESULT_FIELDS = {
    "status",
    "fact_authority",
    "selected_date",
    "day_master",
    "period_ten_gods",
    "direct_relations",
    "repetitions",
    "provenance",
    "interpretation_not_included",
    "limitations",
}
PUBLIC_FORBIDDEN_KEYS = {
    "chart_id",
    "period_id",
    "relation_snapshot_id",
    "birth_input_id",
    "normalized_input",
    "chart_authorization",
    "interpretation",
    "interpretations",
    "relation_priority",
    "priority",
    "score",
    "transformation",
    "event_prediction",
}


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in PUBLIC_FORBIDDEN_KEYS or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        return any(
            pattern.fullmatch(value) is not None
            for pattern in (RELATION_ID_PATTERN, PERIOD_ID_PATTERN)
        ) or re.fullmatch(r"(?:sc2_|sbi2_|scr2_|scs2_)[0-9a-f]{64}", value) is not None
    return False


def _validated_ganzhi(value: Any, *, label: str) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or len(value) != 2
        or value[0] not in STEMS
        or value[1] not in BRANCHES
    ):
        raise RelationRuntimeError(
            "RELATION_GANZHI_INVALID", f"{label} 간지가 고정 표에 없습니다."
        )
    return value[0], value[1]


def _pair_key(left: str, right: str) -> frozenset[str]:
    return frozenset((left, right))


def _pair_tables() -> dict[str, set[frozenset[str]]]:
    policy = load_relation_policy()
    return {
        name: {_pair_key(str(pair[0]), str(pair[1])) for pair in pairs}
        for name, pairs in policy["pair_relations"].items()
    }


def branch_relations(natal_branch: str, period_branch: str) -> list[tuple[str, str]]:
    """두 지지의 정책상 관계 존재와 명시적 대칭 규칙을 반환한다."""
    if natal_branch not in BRANCHES or period_branch not in BRANCHES:
        raise RelationRuntimeError(
            "RELATION_BRANCH_INVALID", "관계 계산 지지가 고정 표에 없습니다."
        )
    policy = load_relation_policy()
    pair_tables = _pair_tables()
    punishment = policy["punishment"]
    values: dict[str, str] = {}
    for relation in ("합", "충", "파", "해"):
        if natal_branch != period_branch and _pair_key(
            natal_branch, period_branch
        ) in pair_tables[relation]:
            values[relation] = "symmetric_pair"
    if natal_branch == period_branch:
        if natal_branch in punishment["self_branches"]:
            values["형"] = "symmetric_self"
    elif any(
        natal_branch in group and period_branch in group
        for group in punishment["distinct_groups"]
    ):
        values["형"] = "symmetric_group_pair"
    return [
        (relation, values[relation])
        for relation in policy["relation_order"]
        if relation in values
    ]


def period_ten_gods(
    day_master: str, period_labels: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """기간 연·월·일 간과 지지 본기의 십신을 계산한다."""
    if day_master not in STEMS:
        raise RelationRuntimeError(
            "RELATION_DAY_MASTER_INVALID", "원국 일간이 고정 표에 없습니다."
        )
    if not isinstance(period_labels, Mapping) or set(period_labels) != set(
        PERIOD_PART_ORDER
    ):
        raise RelationRuntimeError(
            "RELATION_PERIOD_LABELS_INVALID", "기간 연·월·일 label 집합이 다릅니다."
        )
    hidden = tables()["hidden_stems_main_first"]
    result: dict[str, dict[str, str]] = {}
    for part in PERIOD_PART_ORDER:
        ganzhi = period_labels[part]
        stem, branch = _validated_ganzhi(ganzhi, label=part)
        main_hidden = hidden[branch][0]
        result[part] = {
            "ganzhi": ganzhi,
            "stem": stem,
            "stem_ten_god": ten_god(day_master, stem),
            "branch": branch,
            "branch_main_hidden_stem": main_hidden,
            "branch_ten_god": ten_god(day_master, main_hidden),
        }
    return result


def direct_relations(
    natal_pillars: Mapping[str, Any], period_day_branch: str
) -> list[dict[str, str]]:
    """기간 일지와 원국 네 지지의 모든 겹치는 관계를 보존한다."""
    if not isinstance(natal_pillars, Mapping) or set(natal_pillars) != set(
        PILLAR_ORDER
    ):
        raise RelationRuntimeError(
            "RELATION_NATAL_PILLARS_INVALID", "원국 네 기둥 집합이 다릅니다."
        )
    values: list[dict[str, str]] = []
    for pillar_name in PILLAR_ORDER:
        pillar = natal_pillars[pillar_name]
        if not isinstance(pillar, Mapping):
            raise RelationRuntimeError(
                "RELATION_NATAL_PILLARS_INVALID", "원국 기둥 형식이 다릅니다."
            )
        natal_branch = pillar.get("branch")
        if natal_branch not in BRANCHES:
            raise RelationRuntimeError(
                "RELATION_NATAL_PILLARS_INVALID", "원국 지지가 고정 표에 없습니다."
            )
        for relation, direction in branch_relations(natal_branch, period_day_branch):
            values.append(
                {
                    "period_part": "day_branch",
                    "period_branch": period_day_branch,
                    "natal_pillar": pillar_name,
                    "natal_branch": natal_branch,
                    "relation": relation,
                    "direction_rule": direction,
                    "authority": "PROFILE_DETERMINISTIC",
                    "table_version": TABLE_VERSION,
                }
            )
    return values


def exact_repetitions(
    natal_pillars: Mapping[str, Any], period_values: Mapping[str, Mapping[str, str]]
) -> list[dict[str, str]]:
    """기간 세 기둥과 원국 네 기둥의 동일 간·지만 기록한다."""
    if not isinstance(natal_pillars, Mapping) or set(natal_pillars) != set(
        PILLAR_ORDER
    ):
        raise RelationRuntimeError(
            "RELATION_NATAL_PILLARS_INVALID", "원국 네 기둥 집합이 다릅니다."
        )
    if not isinstance(period_values, Mapping) or set(period_values) != set(
        PERIOD_PART_ORDER
    ):
        raise RelationRuntimeError(
            "RELATION_PERIOD_LABELS_INVALID", "기간 세 기둥 집합이 다릅니다."
        )
    values: list[dict[str, str]] = []
    for period_part in PERIOD_PART_ORDER:
        period_pillar = period_values[period_part]
        for natal_name in PILLAR_ORDER:
            natal_pillar = natal_pillars[natal_name]
            if not isinstance(natal_pillar, Mapping):
                raise RelationRuntimeError(
                    "RELATION_NATAL_PILLARS_INVALID", "원국 기둥 형식이 다릅니다."
                )
            for component in COMPONENT_ORDER:
                period_value = period_pillar.get(component)
                natal_value = natal_pillar.get(component)
                allowed = STEMS if component == "stem" else BRANCHES
                if period_value not in allowed or natal_value not in allowed:
                    raise RelationRuntimeError(
                        "RELATION_PILLAR_COMPONENT_INVALID",
                        "원국·기간 간지 성분이 고정 표에 없습니다.",
                    )
                if period_value == natal_value:
                    values.append(
                        {
                            "period_part": period_part,
                            "natal_pillar": natal_name,
                            "component": component,
                            "value": period_value,
                            "authority": "PROFILE_DETERMINISTIC",
                            "table_version": TABLE_VERSION,
                        }
                    )
    return values


def _validate_chart_parent(
    chart_snapshot: Any, authorization_value: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(chart_snapshot, Mapping):
        raise RelationRuntimeError(
            "RELATION_CHART_PARENT_INVALID", "승인 원국 snapshot이 필요합니다."
        )
    try:
        public = public_chart(chart_snapshot)
        authorization = validate_chart_authorization(authorization_value)
    except (KeyError, TypeError, ChartDayAdapterError, PeriodRuntimeError) as exc:
        raise RelationRuntimeError(
            "RELATION_CHART_PARENT_INVALID", "원국 부모 snapshot을 검증할 수 없습니다."
        ) from exc
    source_versions = chart_snapshot.get("source_versions")
    hard_facts = public.get("hard_facts")
    if (
        public.get("status") != "ok"
        or public.get("fact_authority") != "HARD_GT"
        or chart_snapshot.get("chart_id") != authorization["chart_id"]
        or chart_snapshot.get("engine_version") != ENGINE_VERSION_V15
        or chart_snapshot.get("policy_id") != CHART_POLICY_ID
        or not isinstance(source_versions, Mapping)
        or source_versions.get("runtime_release") != CHART_RELEASE_ID
        or not isinstance(hard_facts, Mapping)
        or authorization.get("release_id") != PARENT_RELEASE_ID
        or authorization.get("normalized_input_sha256")
        != sha256_value(chart_snapshot.get("normalized_input"))
        or authorization.get("public_hard_facts_sha256") != sha256_value(hard_facts)
        or authorization.get("source_versions_sha256") != sha256_value(source_versions)
    ):
        raise RelationRuntimeError(
            "RELATION_CHART_PARENT_INVALID", "원국 부모 release·hash가 다릅니다."
        )
    pillars = hard_facts.get("pillars")
    day_master = hard_facts.get("day_master")
    if (
        not isinstance(pillars, Mapping)
        or set(pillars) != set(PILLAR_ORDER)
        or not isinstance(day_master, Mapping)
        or day_master.get("stem") not in STEMS
    ):
        raise RelationRuntimeError(
            "RELATION_CHART_PARENT_INVALID", "원국 relation 입력 사실이 없습니다."
        )
    for name in PILLAR_ORDER:
        pillar = pillars[name]
        if not isinstance(pillar, Mapping):
            raise RelationRuntimeError(
                "RELATION_CHART_PARENT_INVALID", "원국 기둥 형식이 다릅니다."
            )
        stem, branch = pillar.get("stem"), pillar.get("branch")
        if stem not in STEMS or branch not in BRANCHES or pillar.get("ganzhi") != stem + branch:
            raise RelationRuntimeError(
                "RELATION_CHART_PARENT_INVALID", "원국 간지 identity가 다릅니다."
            )
    return deepcopy(public), deepcopy(authorization)


def _validate_period_parent(
    period_snapshot: Any, period_signer: PeriodIdSigner
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_fields = {
        "schema_version",
        "status",
        "fact_authority",
        "period_scope",
        "days",
        "boundary_capability",
        "message",
        "limitations",
        "period_id",
        "chart_authorization",
        "authority_release_id",
        "reference_date",
    }
    if (
        not isinstance(period_snapshot, Mapping)
        or set(period_snapshot) != expected_fields
        or not isinstance(period_signer, PeriodIdSigner)
    ):
        raise RelationRuntimeError(
            "RELATION_PERIOD_PARENT_INVALID", "승인 단일 날짜 snapshot이 필요합니다."
        )
    try:
        public = public_daily_label_result(period_snapshot)
        authorization = validate_chart_authorization(
            period_snapshot.get("chart_authorization")
        )
    except (KeyError, TypeError, PeriodRuntimeError) as exc:
        raise RelationRuntimeError(
            "RELATION_PERIOD_PARENT_INVALID", "기간 부모 snapshot을 검증할 수 없습니다."
        ) from exc
    scope = public["period_scope"]
    if (
        public.get("status") != "ok"
        or public.get("fact_authority") != "HARD_GT"
        or scope.get("day_count") != 1
        or scope.get("start_date") != scope.get("end_date")
        or len(public.get("days", [])) != 1
        or period_snapshot.get("authority_release_id") != PERIOD_RELEASE_ID
        or PERIOD_ID_PATTERN.fullmatch(str(period_snapshot.get("period_id", "")))
        is None
    ):
        raise RelationRuntimeError(
            "RELATION_SINGLE_DATE_REQUIRED", "relation은 승인된 단일 날짜만 허용합니다."
        )
    resolved_scope = {
        "schema_version": RESOLVED_SCOPE_VERSION,
        **scope,
        "reference_date": period_snapshot["reference_date"],
        "intraday_segments_supported": False,
        "future_physical_instant_claimed": False,
    }
    preimage = {
        "authority": authorization,
        "period_scope": resolved_scope,
        "days": public["days"],
        "authority_release_id": PERIOD_RELEASE_ID,
    }
    if period_snapshot.get("period_id") != period_signer.period_id(
        PERIOD_RELEASE_ID, preimage
    ):
        raise RelationRuntimeError(
            "RELATION_PERIOD_PARENT_TAMPERED", "기간 부모 snapshot HMAC이 다릅니다."
        )
    return deepcopy(public), deepcopy(authorization)


def _expected_relation_records(value: Mapping[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item in value.get("direct_relations", []):
        if not isinstance(item, Mapping):
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "직접 관계 record 형식이 다릅니다."
            )
        expected = branch_relations(
            str(item.get("natal_branch")), str(item.get("period_branch"))
        )
        matches = [
            direction
            for relation, direction in expected
            if relation == item.get("relation")
        ]
        if matches != [item.get("direction_rule")]:
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "존재하지 않는 관계가 포함됐습니다."
            )
        records.append(dict(item))
    return records


def validate_public_relation_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != PUBLIC_RESULT_FIELDS:
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "relation 공개 결과 field 집합이 다릅니다."
        )
    period_values = value.get("period_ten_gods")
    provenance = value.get("provenance")
    direct = value.get("direct_relations")
    repetitions = value.get("repetitions")
    limitations = value.get("limitations")
    try:
        selected = date.fromisoformat(str(value.get("selected_date")))
    except ValueError as exc:
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "선택 날짜가 올바르지 않습니다."
        ) from exc
    if (
        value.get("status") != "ok"
        or value.get("fact_authority") != "PROFILE_DETERMINISTIC"
        or selected.isoformat() != value.get("selected_date")
        or value.get("day_master") not in STEMS
        or not isinstance(period_values, Mapping)
        or set(period_values) != set(PERIOD_PART_ORDER)
        or not isinstance(direct, list)
        or not isinstance(repetitions, list)
        or not isinstance(provenance, Mapping)
        or set(provenance)
        != {
            "chart_snapshot_sha256",
            "period_snapshot_sha256",
            "chart_runtime_release_id",
            "period_runtime_release_id",
            "relation_policy_id",
            "relation_table_version",
            "ten_god_table_version",
        }
        or any(
            SHA256_PATTERN.fullmatch(str(provenance.get(field, ""))) is None
            for field in ("chart_snapshot_sha256", "period_snapshot_sha256")
        )
        or provenance.get("chart_runtime_release_id") != CHART_RELEASE_ID
        or provenance.get("period_runtime_release_id") != PERIOD_RELEASE_ID
        or provenance.get("relation_policy_id") != POLICY_ID
        or provenance.get("relation_table_version") != TABLE_VERSION
        or provenance.get("ten_god_table_version") != TEN_GOD_TABLE_VERSION
        or value.get("interpretation_not_included") is not True
        or not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "relation 공개 결과 값이 다릅니다."
        )
    normalized_labels: dict[str, str] = {}
    for part in PERIOD_PART_ORDER:
        item = period_values[part]
        if not isinstance(item, Mapping) or set(item) != {
            "ganzhi",
            "stem",
            "stem_ten_god",
            "branch",
            "branch_main_hidden_stem",
            "branch_ten_god",
        }:
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "기간 십신 record 형식이 다릅니다."
            )
        stem, branch = _validated_ganzhi(item.get("ganzhi"), label=part)
        main_hidden = tables()["hidden_stems_main_first"][branch][0]
        if (
            item.get("stem") != stem
            or item.get("branch") != branch
            or item.get("branch_main_hidden_stem") != main_hidden
            or item.get("stem_ten_god") != ten_god(value["day_master"], stem)
            or item.get("branch_ten_god")
            != ten_god(value["day_master"], main_hidden)
            or item.get("stem_ten_god") not in TEN_GOD_LABELS
            or item.get("branch_ten_god") not in TEN_GOD_LABELS
        ):
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "기간 십신 값이 고정 표와 다릅니다."
            )
        normalized_labels[part] = item["ganzhi"]
    relation_records = _expected_relation_records(value)
    relation_keys: list[tuple[str, str, str]] = []
    for item in relation_records:
        if set(item) != {
            "period_part",
            "period_branch",
            "natal_pillar",
            "natal_branch",
            "relation",
            "direction_rule",
            "authority",
            "table_version",
        } or (
            item.get("period_part") != "day_branch"
            or item.get("period_branch") != period_values["day"]["branch"]
            or item.get("natal_pillar") not in PILLAR_ORDER
            or item.get("natal_branch") not in BRANCHES
            or item.get("authority") != "PROFILE_DETERMINISTIC"
            or item.get("table_version") != TABLE_VERSION
        ):
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "직접 관계 identity가 다릅니다."
            )
        relation_keys.append(
            (item["natal_pillar"], item["relation"], item["natal_branch"])
        )
    if len(relation_keys) != len(set(relation_keys)):
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "직접 관계가 중복됐습니다."
        )
    repetition_keys: list[tuple[str, str, str]] = []
    for item in repetitions:
        if not isinstance(item, Mapping) or set(item) != {
            "period_part",
            "natal_pillar",
            "component",
            "value",
            "authority",
            "table_version",
        }:
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "반복 record 형식이 다릅니다."
            )
        component = item.get("component")
        allowed = STEMS if component == "stem" else BRANCHES
        if (
            item.get("period_part") not in PERIOD_PART_ORDER
            or item.get("natal_pillar") not in PILLAR_ORDER
            or component not in COMPONENT_ORDER
            or item.get("value") not in allowed
            or item.get("value")
            != period_values[str(item.get("period_part"))][str(component)]
            or item.get("authority") != "PROFILE_DETERMINISTIC"
            or item.get("table_version") != TABLE_VERSION
        ):
            raise RelationRuntimeError(
                "RELATION_OUTPUT_INVALID", "반복 identity가 다릅니다."
            )
        repetition_keys.append(
            (str(item["period_part"]), str(item["natal_pillar"]), str(component))
        )
    if len(repetition_keys) != len(set(repetition_keys)):
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "반복 record가 중복됐습니다."
        )
    if _contains_forbidden(value):
        raise RelationRuntimeError(
            "RELATION_OUTPUT_PRIVATE_OR_INTERPRETIVE",
            "relation 공개 결과에 내부 ID 또는 해석 field가 있습니다.",
        )
    return deepcopy(dict(value))


def public_relation_result(internal: Mapping[str, Any]) -> dict[str, Any]:
    try:
        projected = {field: deepcopy(internal[field]) for field in PUBLIC_RESULT_FIELDS}
    except (KeyError, TypeError) as exc:
        raise RelationRuntimeError(
            "RELATION_OUTPUT_INVALID", "relation 내부 결과를 공개할 수 없습니다."
        ) from exc
    return validate_public_relation_result(projected)


def calculate_relation_candidate(
    *,
    chart_snapshot: Any,
    period_snapshot: Any,
    period_signer: PeriodIdSigner,
    relation_signer: RelationIdSigner,
    authority_release_id: str,
) -> dict[str, Any]:
    """승인 부모 snapshot에 결합된 단일 날짜 relation 후보를 만든다."""
    validate_contract_registry()
    if not isinstance(relation_signer, RelationIdSigner):
        raise RelationRuntimeError(
            "RELATION_ID_KEY_INVALID", "relation signer가 필요합니다."
        )
    public_period, authorization = _validate_period_parent(
        period_snapshot, period_signer
    )
    public_natal, chart_authorization = _validate_chart_parent(
        chart_snapshot, authorization
    )
    if chart_authorization != authorization:
        raise RelationRuntimeError(
            "RELATION_PARENT_LINK_MISMATCH", "원국과 기간 부모 snapshot 연결이 다릅니다."
        )
    hard_facts = public_natal["hard_facts"]
    natal_pillars = hard_facts["pillars"]
    day_master = hard_facts["day_master"]["stem"]
    day_row = public_period["days"][0]
    labels = {
        "year": day_row["year_ganzhi"],
        "month": day_row["month_ganzhi"],
        "day": day_row["day_ganzhi"],
    }
    ten_gods = period_ten_gods(day_master, labels)
    facts = {
        "status": "ok",
        "fact_authority": "PROFILE_DETERMINISTIC",
        "selected_date": day_row["date"],
        "day_master": day_master,
        "period_ten_gods": ten_gods,
        "direct_relations": direct_relations(
            natal_pillars, ten_gods["day"]["branch"]
        ),
        "repetitions": exact_repetitions(natal_pillars, ten_gods),
        "provenance": {
            "chart_snapshot_sha256": authorization["public_hard_facts_sha256"],
            "period_snapshot_sha256": sha256_value(public_period),
            "chart_runtime_release_id": CHART_RELEASE_ID,
            "period_runtime_release_id": PERIOD_RELEASE_ID,
            "relation_policy_id": POLICY_ID,
            "relation_table_version": TABLE_VERSION,
            "ten_god_table_version": TEN_GOD_TABLE_VERSION,
        },
        "interpretation_not_included": True,
        "limitations": [
            "관계 존재와 동일 간지만 제공하며 우선순위·합화 성립·길흉 해석은 포함하지 않습니다.",
            "승인된 단일 날짜만 지원하며 2~31일 relation 배열은 제공하지 않습니다.",
        ],
    }
    preimage = {
        "facts": facts,
        "chart_id": chart_snapshot["chart_id"],
        "period_id": period_snapshot["period_id"],
        "authority_release_id": authority_release_id,
    }
    internal = {
        "schema_version": "saju-natal-day-relation-internal-v1",
        **facts,
        "relation_snapshot_id": relation_signer.relation_id(
            authority_release_id, preimage
        ),
        "parent_chart_id": chart_snapshot["chart_id"],
        "parent_period_id": period_snapshot["period_id"],
        "authority_release_id": authority_release_id,
    }
    if RELATION_ID_PATTERN.fullmatch(internal["relation_snapshot_id"]) is None:
        raise RelationRuntimeError(
            "RELATION_ID_INVALID", "relation 내부 ID 생성에 실패했습니다."
        )
    public_relation_result(internal)
    return internal


class ApprovedSingleDateRelationEngine:
    """유효 relation release와 명시 flag가 있을 때만 단일 날짜 관계를 연다."""

    def __init__(
        self,
        *,
        period_signer: PeriodIdSigner,
        relation_signer: RelationIdSigner,
        release_registry: Path | None = None,
        enable_approved_runtime: bool = False,
    ) -> None:
        validate_contract_registry()
        if not isinstance(period_signer, PeriodIdSigner) or not isinstance(
            relation_signer, RelationIdSigner
        ):
            raise RelationRuntimeError(
                "RELATION_ID_KEY_INVALID", "검증된 period·relation signer가 필요합니다."
            )
        self.period_signer = period_signer
        self.relation_signer = relation_signer
        self.release = (
            validate_release_registry(release_registry)
            if release_registry is not None
            else None
        )
        self.enable_approved_runtime = bool(enable_approved_runtime)
        if self.enable_approved_runtime:
            if self.release is None:
                raise RelationRuntimeError(
                    "RELATION_RELEASE_REQUIRED", "승인된 relation release가 필요합니다."
                )
            if not period_signer.production_key or not relation_signer.production_key:
                raise RelationRuntimeError(
                    "RELATION_ID_KEY_INVALID", "운영 relation Runtime에는 production signer가 필요합니다."
                )

    def calculate(self, *, chart_snapshot: Any, period_snapshot: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.release is None:
            raise RelationRuntimeError(
                "RELATION_RELEASE_REQUIRED", "승인된 relation release가 필요합니다."
            )
        if not self.enable_approved_runtime:
            raise RelationRuntimeError(
                "RELATION_FEATURE_DISABLED", "relation Runtime은 기본 off입니다."
            )
        internal = calculate_relation_candidate(
            chart_snapshot=chart_snapshot,
            period_snapshot=period_snapshot,
            period_signer=self.period_signer,
            relation_signer=self.relation_signer,
            authority_release_id=self.release["release_id"],
        )
        return internal, public_relation_result(internal)
