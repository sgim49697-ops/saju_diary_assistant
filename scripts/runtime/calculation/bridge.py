# bridge.py - 기존 strict tool 호출을 runtime engine과 모델-visible allowlist에 연결한다.

from __future__ import annotations

from typing import Any

from scripts.runtime.saju_contract import project_model_visible_tool_result

from .engine import SajuRuntimeEngine


def execute_runtime_tool(
    engine: SajuRuntimeEngine,
    name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if name == "calculate_saju_chart":
        internal = engine.calculate_chart(arguments)
    elif name == "calculate_saju_period":
        internal = engine.calculate_period(arguments)
    else:
        internal = engine._blocked(
            "UNSUPPORTED_TOOL", f"지원하지 않는 tool입니다: {name}"
        )
    visible_input = {
        key: internal.get(key)
        for key in (
            "status",
            "hard_facts",
            "fact_authority",
            "code",
            "message",
            "limitations",
        )
        if internal.get(key) is not None
    }
    return internal, project_model_visible_tool_result(visible_input)
