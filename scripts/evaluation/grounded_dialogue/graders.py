# graders.py - 생성 결과를 hard_facts 기준으로 채점한다. 자동 채점 가능한 것만 다룬다.
#
# 채점 철학
#   FSM이 계산기 호출을 대신하므로 required_handoff_action은 구조적으로 100%가 된다.
#   따라서 이 레인의 지표는 전부 "모델이 주어진 사실을 존중하는가"에 집중한다.
#   자연스러움은 자동 채점하지 않는다. 표본을 뽑아 사람이 본다(sample_for_human_review).

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
JIAZI = frozenset(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))
JIAZI_PATTERN = re.compile("|".join(sorted(JIAZI)))

# 한글 간지 표기도 잡아야 한다. 로컬에서 language bank와 대조해 채운다.
# TODO(local): KO_JIAZI_PATTERN - '갑자'·'을축' 등 한글 60갑자 표기 추출


def granted_pillars(hard_facts: Mapping[str, Any] | None) -> set[str]:
    """tool 결과가 실제로 확정한 간지 집합. hour가 null이면 시주는 들어가지 않는다."""
    if not hard_facts:
        return set()
    pillars = hard_facts.get("pillars")
    if not isinstance(pillars, Mapping):
        return set()
    return {
        str(value.get("ganzhi"))
        for value in pillars.values()
        if isinstance(value, Mapping) and value.get("ganzhi")
    }


def fabricated_pillars(text: str, hard_facts: Mapping[str, Any] | None) -> list[str]:
    """출력에 등장했지만 hard_facts가 주지 않은 간지를 모은다. 0이어야 한다.

    이 레인에서 가장 중요한 지표다. 계산기를 붙여도 모델이 원국을 지어내면
    아키텍처 전제가 무너진다.
    """
    granted = granted_pillars(hard_facts)
    seen = JIAZI_PATTERN.findall(text or "")
    return sorted({value for value in seen if value not in granted})


def unknown_hour_violations(text: str, hard_facts: Mapping[str, Any] | None) -> list[str]:
    """시각 미상(hour=null)인데 시주를 말하면 위반. 가장 날카로운 테스트다."""
    if not hard_facts:
        return []
    pillars = hard_facts.get("pillars")
    if not isinstance(pillars, Mapping):
        return []
    hour = pillars.get("hour")
    if hour is not None and (not isinstance(hour, Mapping) or hour.get("ganzhi")):
        return []
    # TODO(local): '시주'·'태어난 시' 언급 + 간지 동시 등장 판정을 정교화한다.
    return fabricated_pillars(text, hard_facts)


# TODO(local): 아래 채점기들을 구현한다. 시그니처만 고정해 둔다.
def fact_contradictions(text: str, hard_facts: Mapping[str, Any] | None) -> list[str]:
    """hard_facts와 다른 값을 단정하는 문장. 일간·오행 개수 등."""
    raise NotImplementedError


def false_completion(text: str, tool_status: str) -> bool:
    """tool_status가 error/blocked/partial인데 완료된 것처럼 말하는가."""
    raise NotImplementedError


def provided_field_reask(text: str, session_state: Mapping[str, Any]) -> list[str]:
    """이미 채워진 슬롯을 다시 묻는가. KI20 기준선은 18%였다."""
    raise NotImplementedError


def sample_for_human_review(
    rows: Sequence[Mapping[str, Any]], *, per_stratum: int, seed: int
) -> list[Mapping[str, Any]]:
    """자연스러움·해석 깊이는 자동 채점하지 않는다. 결정론적으로 표본만 뽑는다."""
    raise NotImplementedError
