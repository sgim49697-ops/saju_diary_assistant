# mix20k_v3_runtime_build.py - 유효한 v1.2 runtime release와 HMAC key로 MIX20K-v3.1을 새 build에만 재생성한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT, sha256_file
from scripts.runtime.calculation.contracts_v1_2 import (
    ID_CONTRACT_VERSION_V2,
    validate_release_registry_v1_2,
)
from scripts.runtime.calculation.engine_v1_2 import (
    ApprovedSajuRuntimeEngineV12,
    execute_approved_runtime_tool_v1_2,
)
from scripts.runtime.calculation.errors import RuntimeCalculationError

SOURCE_BUILD_ID = "build-94eb7b543490"
SOURCE_BUILD_SHA256 = (
    "94eb7b5434907539d7041fc81846169dc2e80f332e99b53d710722dcd5564454"
)
SOURCE_MANIFEST_SHA256 = (
    "eca6a9b53f8e29501aab700e9c984071a9e800348d757c1294ea5f80e7937948"
)
SOURCE_TRAINING = "training/training_mix20k_v3.0.1_candidate.jsonl"
TARGET_VERSION = "mix20k-v3.1-runtime-grounded"
TARGET_SCHEMA = "3.1.0"
TARGET_TRAINING = "training/training_mix20k_v3.1_runtime_grounded.jsonl"
EXPECTED_ROWS = 20_000
EXPECTED_CHART_CALLS = 4_350
EXPECTED_PERIOD_CALLS = 900
EXPECTED_TOOL_RESULT_ROWS = 2_200
EXPECTED_CALL_ONLY_ROWS = 3_050
MAX_SOURCE_MANIFEST_BYTES = 64 * 1024
MAX_SOURCE_TRAINING_BYTES = 64 * 1024 * 1024
FOREIGN_SELECTION_SEED = (
    "mix20k-v3.1-runtime-grounded|KR_CIVIL_MIDNIGHT_V1|20260831"
)
OUTPUT_ROOT = REPO_ROOT / f"data/derived/saju_1b_baseline/{TARGET_VERSION}"
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
REMOVABLE_RUNTIME_BLOCKERS = {
    "canonical_engine_recheck_pending",
    "soft_interpretation_review_pending",
}
KOREAN_CITIES = ("서울", "부산", "대구", "인천", "광주", "대전", "울산", "제주")


class Mix20KV31BuildError(RuntimeError):
    """v3.1 runtime 재생성 입력·출력·전수 검증 위반."""


def _safe_child(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise Mix20KV31BuildError(f"{label} 경로가 안전하지 않습니다.")
    cursor = root
    for part in candidate.parts:
        cursor /= part
        if cursor.is_symlink():
            raise Mix20KV31BuildError(f"{label} 경로에 symlink가 있습니다.")
    resolved = (root / candidate).resolve(strict=False)
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise Mix20KV31BuildError(f"{label} 경로가 source build를 벗어납니다.") from exc
    return resolved


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()
        + b"\n"
    )


def _jsonl_bytes(values: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
        + b"\n"
        for value in values
    )


def _load_source(build: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if build.is_symlink() or not build.is_dir() or build.name != SOURCE_BUILD_ID:
        raise Mix20KV31BuildError("고정 v3.0.1 source build가 없거나 symlink입니다.")
    manifest_path = _safe_child(build, "build_manifest.json", "source manifest")
    training_path = _safe_child(build, SOURCE_TRAINING, "source training")
    if (
        manifest_path.is_symlink()
        or training_path.is_symlink()
        or not manifest_path.is_file()
        or not training_path.is_file()
        or not 1 <= manifest_path.stat().st_size <= MAX_SOURCE_MANIFEST_BYTES
        or not 1 <= training_path.stat().st_size <= MAX_SOURCE_TRAINING_BYTES
        or sha256_file(manifest_path) != SOURCE_MANIFEST_SHA256
    ):
        raise Mix20KV31BuildError(
            "v3.0.1 source manifest·training identity가 고정값과 다릅니다."
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Mix20KV31BuildError("v3.0.1 source manifest를 읽지 못했습니다.") from exc
    if (
        manifest.get("build_id") != SOURCE_BUILD_ID
        or manifest.get("build_sha256") != SOURCE_BUILD_SHA256
        or manifest.get("dataset_version") != "v3.0.1-repaired"
        or manifest.get("rows", {}).get("training_candidate_projection")
        != EXPECTED_ROWS
        or manifest.get("artifact_sha256", {}).get(SOURCE_TRAINING)
        != sha256_file(training_path)
        or manifest.get("governance", {}).get("sealed_blind_payload_read_allowed")
        is not False
    ):
        raise Mix20KV31BuildError("v3.0.1 source identity·governance가 다릅니다.")
    rows: list[dict[str, Any]] = []
    try:
        with training_path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    raise Mix20KV31BuildError(f"source training 빈 행: {number}")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise Mix20KV31BuildError(f"source training object 오류: {number}")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, Mix20KV31BuildError):
            raise
        raise Mix20KV31BuildError("v3.0.1 source training을 읽지 못했습니다.") from exc
    if len(rows) != EXPECTED_ROWS or len({row.get("id") for row in rows}) != EXPECTED_ROWS:
        raise Mix20KV31BuildError("v3.0.1 source는 고유 ID 20,000행이어야 합니다.")
    return manifest, rows


def _tool_call(row: dict[str, Any]) -> tuple[int, str, dict[str, Any]] | None:
    found: list[tuple[int, str, dict[str, Any]]] = []
    for message_index, message in enumerate(row.get("messages", [])):
        for call in message.get("tool_calls", []):
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = function.get("name") if isinstance(function, dict) else None
            arguments = function.get("arguments") if isinstance(function, dict) else None
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise Mix20KV31BuildError(f"tool call schema가 다릅니다: {row.get('id')}")
            found.append((message_index, name, arguments))
    if len(found) > 1:
        raise Mix20KV31BuildError(f"한 행에 tool call이 둘 이상입니다: {row.get('id')}")
    return found[0] if found else None


def _foreign_ids(rows: Sequence[dict[str, Any]]) -> tuple[set[str], set[str]]:
    foreign: list[str] = []
    for row in rows:
        call = _tool_call(row)
        if call is None or call[1] != "calculate_saju_chart":
            continue
        birthplace = call[2].get("birthplace")
        if isinstance(birthplace, dict) and (
            birthplace.get("country_code") != "KR"
            or birthplace.get("timezone") != "Asia/Seoul"
        ):
            foreign.append(str(row["id"]))
    if len(foreign) != 200:
        raise Mix20KV31BuildError(f"해외 chart 행이 200건이 아닙니다: {len(foreign)}")
    ranked = sorted(
        foreign,
        key=lambda value: hashlib.sha256(
            f"{FOREIGN_SELECTION_SEED}|{value}".encode()
        ).hexdigest(),
    )
    return set(ranked[:20]), set(ranked[20:])


def _replace_foreign(
    row: dict[str, Any], arguments: dict[str, Any], call_message_index: int
) -> dict[str, Any]:
    digest = hashlib.sha256(str(row["id"]).encode()).digest()
    city = KOREAN_CITIES[digest[0] % len(KOREAN_CITIES)]
    updated = deepcopy(arguments)
    updated["birthplace"] = {
        "country_code": "KR",
        "city": city,
        "timezone": "Asia/Seoul",
        "longitude": None,
        "latitude": None,
    }
    precision = updated["time_precision"]
    if precision == "exact":
        time_label = f"{updated['birth_time']} 출생"
    elif precision == "range":
        time_label = (
            f"{updated['time_range']['start']}~{updated['time_range']['end']} 범위 출생"
        )
    else:
        time_label = "출생시간 미상"
    calendar_label = "양력" if updated["calendar"] == "solar" else "음력"
    gender = {
        "male": "남성",
        "female": "여성",
        "unspecified": "성별 미지정",
    }[updated["gender_for_daeun"]]
    user_indexes = [
        index
        for index, message in enumerate(row["messages"][: call_message_index + 1])
        if message.get("role") == "user"
    ]
    if not user_indexes:
        raise Mix20KV31BuildError(
            f"해외 chart tool call 앞에 user 메시지가 없습니다: {row.get('id')}"
        )
    row["messages"][user_indexes[-1]]["content"] = (
        f"출생정보는 {updated['birth_date']} {calendar_label}, {time_label}, "
        f"{city} 출생, {gender}입니다. 이 정보로 원국을 계산해 주세요."
    )
    return updated


def _period_anchor(row_id: str) -> dict[str, Any]:
    digest = hashlib.sha256(f"period-anchor|{row_id}".encode()).digest()
    year = 1960 + digest[0] % 46
    month = digest[1] % 12 + 1
    day_value = digest[2] % 28 + 1
    hour = digest[3] % 24
    minute = (digest[4] % 12) * 5
    return {
        "birth_date": date(year, month, day_value).isoformat(),
        "calendar": "solar",
        "leap_month": None,
        "birth_time": f"{hour:02d}:{minute:02d}",
        "time_precision": "exact",
        "time_range": None,
        "birthplace": {
            "country_code": "KR",
            "city": KOREAN_CITIES[digest[5] % len(KOREAN_CITIES)],
            "timezone": "Asia/Seoul",
            "longitude": None,
            "latitude": None,
        },
        "gender_for_daeun": "unspecified",
    }


def _replace_chart_id(messages: list[dict[str, Any]], old: str, new: str) -> None:
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and old in content:
            message["content"] = content.replace(old, new)


def _final_answer(name: str, visible: dict[str, Any]) -> str:
    status = visible.get("status")
    if status == "blocked":
        code = visible.get("code")
        if code == "UNSUPPORTED_REGION":
            return (
                "현재 계산기는 대한민국 출생·Asia/Seoul만 지원해 이 입력으로는 계산하지 "
                "않았습니다. 지원되지 않는 지역을 한국 기준으로 바꾸어 추측하지 않습니다."
            )
        return f"계산을 완료하지 못했습니다. 확인 코드: {code or 'UNKNOWN'}"
    facts = visible.get("hard_facts")
    if not isinstance(facts, dict):
        raise Mix20KV31BuildError("성공 tool result에 hard_facts가 없습니다.")
    authority = visible.get("fact_authority")
    if name == "calculate_saju_chart":
        pillars = facts.get("pillars", {})
        labels = []
        for key, label in (("year", "연주"), ("month", "월주"), ("day", "일주"), ("hour", "시주")):
            pillar = pillars.get(key) if isinstance(pillars, dict) else None
            if isinstance(pillar, dict) and isinstance(pillar.get("ganzhi"), str):
                labels.append(f"{label} {pillar['ganzhi']}")
        day_master = facts.get("day_master", {})
        master = day_master.get("stem") if isinstance(day_master, dict) else None
        if authority == "HARD_GT":
            return (
                "승인된 계산 결과는 "
                + ", ".join(labels)
                + (f"이며 일간은 {master}입니다. " if master else "입니다. ")
                + "이는 명리 체계의 구조화 분류값이며 실제 사건이나 감정의 원인으로 단정하지 않습니다."
            )
        return (
            "출생시간을 임의로 정하지 않았습니다. 현재 범위에서 공통으로 확인되는 값은 "
            + (", ".join(labels) if labels else "제공된 공통 사실")
            + "입니다. 시주와 후보별 차이는 확정값으로 말하지 않습니다."
        )
    period = facts.get("period")
    if not isinstance(period, dict):
        raise Mix20KV31BuildError("기간 tool result 구조가 다릅니다.")
    start = period.get("start_ganzhi", {})
    end = period.get("end_ganzhi", {})
    start_text = start.get("day_ganzhi") if isinstance(start, dict) else None
    end_text = end.get("day_ganzhi") if isinstance(end, dict) else None
    return (
        f"계산된 기간은 {period.get('start_date')}~{period.get('end_date')}이고, "
        f"시작일 일진은 {start_text}, 종료일 일진은 {end_text}입니다. "
        "이 값만으로 사건이나 길흉을 보장하지 않으며 현실 일정과 함께 참고하세요."
    )


def _tool_result_index(messages: Sequence[dict[str, Any]], call_index: int) -> int | None:
    candidates = [
        index
        for index in range(call_index + 1, len(messages))
        if messages[index].get("role") == "tool"
    ]
    if len(candidates) > 1:
        raise Mix20KV31BuildError("한 tool call 뒤 result가 둘 이상입니다.")
    return candidates[0] if candidates else None


def _reground_rows(
    rows: Sequence[dict[str, Any]], engine: ApprovedSajuRuntimeEngineV12
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    unsupported_ids, replacement_ids = _foreign_ids(rows)
    review_rows: list[dict[str, Any]] = []
    training_rows: list[dict[str, Any]] = []
    calls: Counter[str] = Counter()
    stored_results = 0
    call_only = 0
    period_anchor_calls = 0
    authority_counts: Counter[str] = Counter()
    eligible_before = sum(bool(row.get("train_candidate")) for row in rows)
    for number, source in enumerate(rows, 1):
        row = deepcopy(source)
        row_id = str(row["id"])
        call = _tool_call(row)
        regrounding: dict[str, Any] = {
            "tool_executed": False,
            "tool_result_stored": False,
            "foreign_policy": "not_applicable",
        }
        if call is not None:
            message_index, name, source_arguments = call
            calls[name] += 1
            arguments = deepcopy(source_arguments)
            if row_id in replacement_ids:
                arguments = _replace_foreign(row, arguments, message_index)
                regrounding["foreign_policy"] = "replaced_with_deterministic_kr_case"
            elif row_id in unsupported_ids:
                regrounding["foreign_policy"] = "preserved_as_unsupported_region"
            if name == "calculate_saju_period":
                anchor = engine.calculate_chart(_period_anchor(row_id))
                if anchor.get("status") != "ok" or not isinstance(anchor.get("chart_id"), str):
                    raise Mix20KV31BuildError(f"기간 anchor chart 생성 실패: {row_id}")
                period_anchor_calls += 1
                old_chart_id = str(arguments["chart_id"])
                arguments["chart_id"] = anchor["chart_id"]
                _replace_chart_id(row["messages"], old_chart_id, anchor["chart_id"])
            row["messages"][message_index]["tool_calls"][0]["function"][
                "arguments"
            ] = arguments
            internal, visible = execute_approved_runtime_tool_v1_2(
                engine, name, arguments
            )
            regrounding.update(
                {
                    "tool_executed": True,
                    "tool_status": internal["status"],
                    "tool_code": internal.get("code"),
                    "fact_authority": internal.get("fact_authority"),
                }
            )
            if internal["status"] not in {"ok", "partial", "blocked"}:
                raise Mix20KV31BuildError(f"tool 실행이 안전하게 닫히지 않았습니다: {row_id}")
            if row_id in unsupported_ids and internal.get("code") != "UNSUPPORTED_REGION":
                raise Mix20KV31BuildError(f"해외 unsupported trajectory가 다릅니다: {row_id}")
            if row_id not in unsupported_ids and internal["status"] == "blocked":
                raise Mix20KV31BuildError(
                    f"지원 범위 tool trajectory가 차단됐습니다: {row_id}/{internal.get('code')}"
                )
            result_index = _tool_result_index(row["messages"], message_index)
            if result_index is None:
                call_only += 1
                row["fact_authority"] = "NONE"
            else:
                stored_results += 1
                row["messages"][result_index]["content"] = json.dumps(
                    visible, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                final_index = len(row["messages"]) - 1
                if (
                    final_index <= result_index
                    or row["messages"][final_index].get("role") != "assistant"
                ):
                    raise Mix20KV31BuildError(
                        f"tool result final assistant가 없습니다: {row_id}"
                    )
                row["messages"][final_index]["content"] = _final_answer(name, visible)
                row["fact_authority"] = visible.get("fact_authority") or "NONE"
                regrounding["tool_result_stored"] = True
                authority_counts[row["fact_authority"]] += 1
            blockers = [
                blocker
                for blocker in row.get("training_blockers", [])
                if blocker not in REMOVABLE_RUNTIME_BLOCKERS
            ]
            row["training_blockers"] = blockers
            row["train_candidate"] = not blockers
            row["promotion_status"] = (
                "runtime_grounded_auto_pass"
                if not blockers
                else "runtime_grounded_review_pending"
            )
        row["schema_version"] = TARGET_SCHEMA
        row["runtime_release_id"] = engine.release["release_id"]
        row["runtime_fact_source"] = (
            "approved_saju_runtime_v1_2" if call is not None else None
        )
        review = {**deepcopy(row), "runtime_regrounding": regrounding}
        review_rows.append(review)
        training_rows.append(row)
        if number % 2000 == 0:
            print(
                f"mix20k_v3_1_progress={number}/{EXPECTED_ROWS}",
                file=sys.stderr,
                flush=True,
            )
    if calls != {
        "calculate_saju_chart": EXPECTED_CHART_CALLS,
        "calculate_saju_period": EXPECTED_PERIOD_CALLS,
    }:
        raise Mix20KV31BuildError(f"전수 tool call 수가 다릅니다: {dict(calls)}")
    if stored_results != EXPECTED_TOOL_RESULT_ROWS or call_only != EXPECTED_CALL_ONLY_ROWS:
        raise Mix20KV31BuildError(
            f"stored/call-only 수가 다릅니다: {stored_results}/{call_only}"
        )
    return review_rows, training_rows, {
        "rows": len(rows),
        "tool_calls": dict(sorted(calls.items())),
        "dataset_tool_calls": sum(calls.values()),
        "period_anchor_chart_calls": period_anchor_calls,
        "stored_tool_result_rows": stored_results,
        "call_only_rows_executed_not_materialized": call_only,
        "foreign_replaced_with_kr": len(replacement_ids),
        "foreign_preserved_as_unsupported": len(unsupported_ids),
        "authority_counts_on_stored_results": dict(sorted(authority_counts.items())),
        "eligible_rows_before": eligible_before,
        "eligible_rows_after": sum(row["train_candidate"] for row in training_rows),
        "all_or_nothing_completed": True,
    }


def _normalized_signature(row: dict[str, Any]) -> str:
    return _sha256_bytes(
        canonical_json_bytes(
            {
                "messages": row["messages"],
                "tools": row["tools"],
                "target": row["target_assistant_message_index"],
            }
        )
    )


def _derived_artifacts(
    review_rows: Sequence[dict[str, Any]], training_rows: Sequence[dict[str, Any]]
) -> dict[str, bytes]:
    signatures = Counter(_normalized_signature(row) for row in training_rows)
    record_index = [
        {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "task_axis": row["task_axis"],
            "split": "train_20k",
            "content_sha256": _normalized_signature(row),
        }
        for row in training_rows
    ]
    ranked = sorted(
        training_rows,
        key=lambda row: hashlib.sha256(
            f"mix20k-v3.1-diagnostic-2k|{row['id']}".encode()
        ).hexdigest(),
    )[:2000]
    catalog = [
        {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "task_axis": row["task_axis"],
            "fact_authority": row["fact_authority"],
            "train_candidate": row["train_candidate"],
            "tool_names": sorted(
                {
                    call["function"]["name"]
                    for message in row["messages"]
                    for call in message.get("tool_calls", [])
                }
            ),
        }
        for row in training_rows
    ]
    split_manifest = {
        "schema_version": "1.0.0",
        "dataset_version": TARGET_VERSION,
        "training": {
            "split_id": "train_20k",
            "rows": EXPECTED_ROWS,
            "membership_sha256": _sha256_bytes(_jsonl_bytes(record_index)),
            "carved_holdout_rows": 0,
        },
        "evaluation_policy": {
            "reuse_existing_dev_monitor_and_diagnostic_by_hash": True,
            "sealed_blind_payload_accessed": False,
            "sealed_blind_membership_changed": False,
            "training_rows_reused_as_new_eval": False,
        },
        "training_execution_allowed": False,
    }
    leakage = {
        "schema_version": "1.0.0",
        "status": "new_training_membership_hashed_sealed_blind_untouched",
        "rows": EXPECTED_ROWS,
        "unique_normalized_signatures": len(signatures),
        "exact_duplicate_participating_rows": sum(
            count for count in signatures.values() if count > 1
        ),
        "max_exact_signature_multiplicity": max(signatures.values()),
        "sealed_blind_payload_read": False,
        "sealed_blind_accessed": False,
        "cross_split_payload_comparison_pending_preflight": True,
    }
    return {
        "review/mix20k_v3.1_review.jsonl": _jsonl_bytes(review_rows),
        TARGET_TRAINING: _jsonl_bytes(training_rows),
        "catalog/trajectory_catalog.jsonl": _jsonl_bytes(catalog),
        "manifests/record_index.jsonl": _jsonl_bytes(record_index),
        "manifests/split_manifest.json": _json_bytes(split_manifest),
        "diagnostic/diagnostic_2k.jsonl": _jsonl_bytes(ranked),
        "reports/leakage_report.json": _json_bytes(leakage),
    }


def _write_private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, mode=PRIVATE_DIR_MODE, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        PRIVATE_FILE_MODE,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def build(
    *,
    source_build: Path,
    release_registry: Path,
    id_key_file: Path | None = None,
    output_base: Path = OUTPUT_ROOT,
) -> dict[str, Any]:
    try:
        release = validate_release_registry_v1_2(release_registry)
        engine = ApprovedSajuRuntimeEngineV12(
            release_registry=release_registry,
            enable_approved_runtime=True,
            id_key_file=id_key_file,
        )
    except RuntimeCalculationError as exc:
        raise Mix20KV31BuildError(
            "유효한 v1.2 runtime release와 production HMAC key 전에는 "
            "source dataset을 읽거나 재생성하지 않습니다: "
            + exc.message
        ) from exc
    source_manifest, source_rows = _load_source(source_build)
    review_rows, training_rows, summary = _reground_rows(source_rows, engine)
    artifacts = _derived_artifacts(review_rows, training_rows)
    artifacts["reports/runtime_regrounding_summary.json"] = _json_bytes(summary)
    identity = {
        "dataset_version": TARGET_VERSION,
        "source_build_id": SOURCE_BUILD_ID,
        "source_build_sha256": SOURCE_BUILD_SHA256,
        "source_manifest_sha256": sha256_file(source_build / "build_manifest.json"),
        "runtime_release_id": release["release_id"],
        "runtime_release_registry_sha256": release["release_registry_sha256"],
        "runtime_id_contract_version": ID_CONTRACT_VERSION_V2,
        "generator_sha256": sha256_file(Path(__file__)),
        "artifact_content_sha256": {
            relative: _sha256_bytes(payload) for relative, payload in sorted(artifacts.items())
        },
    }
    build_sha256 = _sha256_bytes(canonical_json_bytes(identity))
    build_id = "build-" + build_sha256[:12]
    output_resolved = output_base.resolve(strict=False)
    if output_resolved != OUTPUT_ROOT.resolve(strict=False) or output_base.is_symlink():
        raise Mix20KV31BuildError(f"출력 base는 {OUTPUT_ROOT}로 고정됩니다.")
    destination = output_resolved / build_id
    if destination.exists() or destination.is_symlink():
        raise Mix20KV31BuildError("같은 v3.1 build ID를 덮어쓰지 않습니다.")
    output_resolved.mkdir(parents=True, mode=PRIVATE_DIR_MODE, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=output_resolved))
    temporary.chmod(PRIVATE_DIR_MODE)
    try:
        for relative, payload in artifacts.items():
            _write_private(temporary / relative, payload)
        artifact_hashes = {
            relative: sha256_file(temporary / relative) for relative in sorted(artifacts)
        }
        manifest = {
            "schema_version": "1.0.0",
            "dataset_version": TARGET_VERSION,
            "build_id": build_id,
            "build_sha256": build_sha256,
            "identity": identity,
            "artifact_sha256": artifact_hashes,
            "rows": {
                "review": EXPECTED_ROWS,
                "training": EXPECTED_ROWS,
                "diagnostic": 2000,
            },
            "runtime_gate_passed": True,
            "runtime_release_validated": True,
            "training_execution_allowed": False,
            "phase5_training_performed": False,
            "sealed_blind_payload_accessed": False,
            "source_build_mutated": False,
        }
        _write_private(temporary / "build_manifest.json", _json_bytes(manifest))
        for directory in [temporary, *temporary.rglob("*")]:
            if directory.is_dir():
                directory.chmod(PRIVATE_DIR_MODE)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "status": "built_not_training_promoted",
        "build_id": build_id,
        "build_sha256": build_sha256,
        "output": str(destination),
        "rows": EXPECTED_ROWS,
        "runtime_release_id": release["release_id"],
        "training_promotion_allowed": False,
        "phase5_training_performed": False,
        "source_build_mutated": False,
        "source_dataset_version": source_manifest["dataset_version"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MIX20K-v3.1 runtime-grounded 재생성")
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--source-build", type=Path, required=True)
    parser.add_argument("--release-registry", type=Path, required=True)
    parser.add_argument(
        "--id-key-file",
        type=Path,
        help="현재 사용자 소유 0600·32바이트 production HMAC key 파일",
    )
    parser.add_argument("--output-base", type=Path, default=OUTPUT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build(
            source_build=args.source_build,
            release_registry=args.release_registry,
            id_key_file=args.id_key_file,
            output_base=args.output_base,
        )
    except (OSError, Mix20KV31BuildError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
