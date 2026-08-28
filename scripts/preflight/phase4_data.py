# phase4_data.py - Phase 4 Gate A/B split, tokenization, loss mask와 candidate manifest를 만든다.

from __future__ import annotations

import math
import os
import re
import shutil
import stat
import sys
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    artifact_hash_map,
    load_json,
    read_jsonl,
    sha256_bytes,
    sha256_json,
    utc_now,
    verify_hash_map,
    write_json_once,
    write_jsonl_once,
)

AXES = (
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
)
AXIS_SOURCES = {
    "nemotron_saju": "nemotron_saju",
    "bazi_sft": "bazi_sft",
    "aihub_empathy_single": "aihub_empathy",
    "aihub_empathy_multiturn": "aihub_empathy",
    "yeji_shensha_derived": "yeji_bazi_rules",
}
AXIS_TASKS = {
    "nemotron_saju": "structured_saju_reading",
    "bazi_sft": "grounded_rule_reading",
    "aihub_empathy_single": "empathic_response",
    "aihub_empathy_multiturn": "natural_multiturn_dialogue",
    "yeji_shensha_derived": "shensha_rule_qa",
}
EXPECTED_STAGING_COUNTS = {
    "nemotron_saju": 13_200,
    "bazi_sft": 6_000,
    "aihub_empathy_single": 2_400,
    "aihub_empathy_multiturn": 1_200,
    "yeji_shensha_derived": 1_200,
}
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _length_stats(values: list[int]) -> dict[str, int | float]:
    if not values:
        return {"count": 0, "min": 0, "mean": 0.0, "median": 0, "p90": 0, "p95": 0, "max": 0}
    return {
        "count": len(values),
        "min": min(values),
        "mean": round(sum(values) / len(values), 4),
        "median": _percentile(values, 0.5),
        "p90": _percentile(values, 0.9),
        "p95": _percentile(values, 0.95),
        "max": max(values),
    }


def _staging_root(context: dict[str, Any], repo_root: Path) -> Path:
    parent = context["config"]["parent_staging"]
    return (
        repo_root
        / "data/staging/saju_1b_baseline"
        / parent["version"]
        / parent["build_id"]
    )


def _record_messages(record: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise Phase4Error(f"대화 messages가 올바르지 않습니다: {record.get('id')}")
    normalized: list[dict[str, str]] = []
    for message in messages:
        if (
            not isinstance(message, dict)
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            or CONTROL_PATTERN.search(message["content"])
        ):
            raise Phase4Error(f"대화 role/content가 올바르지 않습니다: {record.get('id')}")
        normalized.append({"role": message["role"], "content": message["content"]})
    if normalized[-1]["role"] != "assistant":
        raise Phase4Error(f"마지막 메시지가 assistant가 아닙니다: {record.get('id')}")
    if not any(message["role"] == "user" for message in normalized[:-1]):
        raise Phase4Error(f"assistant 이전 user 메시지가 없습니다: {record.get('id')}")
    return normalized[:-1], normalized[-1]["content"]


def load_staging_records(
    context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, list[str]], dict[str, Any]]:
    root = _staging_root(context, repo_root)
    private_manifest = load_json(root / "build_manifest.json", "staging private manifest")
    verify_hash_map(root, private_manifest.get("artifact_sha256"), "staging private")
    records_by_id: dict[str, dict[str, Any]] = {}
    ids_by_axis: dict[str, list[str]] = {}
    raw_hash_groups: dict[str, str] = {}
    raw_hash_alias_rows = 0
    message_hashes: set[str] = set()
    leakage_axes: dict[str, set[str]] = defaultdict(set)
    source_groups: dict[str, set[str]] = defaultdict(set)
    schema_counts: Counter[str] = Counter()
    for axis in AXES:
        rows = read_jsonl(root / f"records/{axis}.jsonl", axis)
        if len(rows) != EXPECTED_STAGING_COUNTS[axis]:
            raise Phase4Error(f"staging {axis} 수량이 다릅니다: {len(rows)}")
        axis_ids: list[str] = []
        for record in rows:
            record_id = record.get("id")
            meta = record.get("meta")
            if (
                not isinstance(record_id, str)
                or not record_id
                or record_id in records_by_id
                or record.get("mix_axis") != axis
                or record.get("source") != AXIS_SOURCES[axis]
                or record.get("task") != AXIS_TASKS[axis]
                or record.get("provenance_status") != "verified"
                or not isinstance(meta, dict)
            ):
                raise Phase4Error(f"staging record identity가 다릅니다: {record_id}")
            required_meta = (
                "candidate_rank",
                "leakage_group_id",
                "message_sha256",
                "raw_hash",
                "source_group_id",
            )
            if any(not isinstance(meta.get(key), str) or not meta[key] for key in required_meta):
                raise Phase4Error(f"staging meta가 누락됐습니다: {record_id}")
            previous_raw_group = raw_hash_groups.get(meta["raw_hash"])
            if previous_raw_group is not None and previous_raw_group != meta["source_group_id"]:
                raise Phase4Error(f"staging raw hash가 서로 다른 원천 group에 중복됐습니다: {record_id}")
            if meta["message_sha256"] in message_hashes:
                raise Phase4Error(f"staging message hash가 중복됐습니다: {record_id}")
            if previous_raw_group is not None:
                raw_hash_alias_rows += 1
            raw_hash_groups[meta["raw_hash"]] = meta["source_group_id"]
            message_hashes.add(meta["message_sha256"])
            _record_messages(record)
            if not isinstance(record.get("license_expression"), str) or not isinstance(
                record.get("usage_class"), str
            ):
                raise Phase4Error(f"license/usage 계약이 누락됐습니다: {record_id}")
            quality = record.get("quality_flags")
            if not isinstance(quality, dict) or quality.get("parse_ok") is not True:
                raise Phase4Error(f"parse quality Gate가 실패했습니다: {record_id}")
            leakage_axes[meta["leakage_group_id"]].add(axis)
            source_groups[axis].add(meta["source_group_id"])
            schema_counts["records"] += 1
            schema_counts[f"axis:{axis}"] += 1
            records_by_id[record_id] = record
            axis_ids.append(record_id)
        ids_by_axis[axis] = axis_ids

    candidate_rows = read_jsonl(root / "candidate_order.jsonl", "candidate order")
    if len(candidate_rows) != 24_000:
        raise Phase4Error("candidate_order는 정확히 24,000행이어야 합니다.")
    ordered_ids: list[str] = []
    seen: set[str] = set()
    previous_rank_by_axis: dict[str, str] = {}
    closed_axes: set[str] = set()
    active_axis: str | None = None
    for row in candidate_rows:
        record_id = row.get("id")
        rank = row.get("candidate_rank")
        axis = row.get("mix_axis")
        if axis != active_axis:
            if active_axis is not None:
                closed_axes.add(active_axis)
            if axis in closed_axes:
                raise Phase4Error("candidate_order의 axis block이 다시 열렸습니다.")
            active_axis = axis
        if (
            not isinstance(record_id, str)
            or record_id not in records_by_id
            or record_id in seen
            or not isinstance(rank, str)
            or len(rank) != 64
            or (axis in previous_rank_by_axis and rank < previous_rank_by_axis[axis])
        ):
            raise Phase4Error("candidate_order identity/rank가 올바르지 않습니다.")
        record = records_by_id[record_id]
        meta = record["meta"]
        for key in ("candidate_rank", "leakage_group_id", "source_group_id"):
            if row.get(key) != meta.get(key):
                raise Phase4Error(f"candidate_order와 record meta가 다릅니다: {record_id}:{key}")
        if axis != record.get("mix_axis"):
            raise Phase4Error(f"candidate_order mix_axis가 다릅니다: {record_id}")
        previous_rank_by_axis[axis] = rank
        seen.add(record_id)
        ordered_ids.append(record_id)
    if seen != set(records_by_id):
        raise Phase4Error("candidate_order와 staging ID 집합이 다릅니다.")

    cross_axis = {group: sorted(axes) for group, axes in leakage_axes.items() if len(axes) > 1}
    schema_report = {
        "schema_version": "1.0.0",
        "status": "passed",
        "total_rows": len(records_by_id),
        "row_counts": {axis: len(ids_by_axis[axis]) for axis in AXES},
        "unique_ids": len(records_by_id),
        "unique_raw_hashes": len(raw_hash_groups),
        "raw_hash_alias_rows_within_source_group": raw_hash_alias_rows,
        "raw_hash_cross_source_group_duplicates": 0,
        "unique_message_hashes": len(message_hashes),
        "unique_leakage_groups": len(leakage_axes),
        "unique_source_groups": {axis: len(source_groups[axis]) for axis in AXES},
        "cross_axis_leakage_groups": len(cross_axis),
        "cross_axis_group_axis_patterns": dict(
            sorted(Counter("+".join(axes) for axes in cross_axis.values()).items())
        ),
        "empty_assistant_rows": 0,
        "control_character_rows": 0,
        "parse_failures": 0,
        "license_metadata_missing": 0,
        "raw_samples_in_report": False,
    }
    return records_by_id, ordered_ids, ids_by_axis, schema_report


def _tokenize_records(
    context: dict[str, Any],
    repo_root: Path,
    records_by_id: dict[str, dict[str, Any]],
    ordered_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    try:
        from transformers import AutoTokenizer
        from trl.trainer.sft_trainer import DataCollatorForLanguageModeling
    except Exception as exc:
        raise Phase4Error("고정 Transformers/TRL tokenizer 경로를 import하지 못했습니다.") from exc

    config = context["config"]
    snapshot = repo_root / config["model"]["local_subdir"]
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot,
        local_files_only=True,
        trust_remote_code=True,
    )
    template = config["chat_template"]
    if (
        tokenizer.bos_token_id != template["bos_token_id"]
        or tokenizer.eos_token_id != template["eos_token_id"]
        or tokenizer.pad_token_id != template["pad_token_id"]
        or not isinstance(tokenizer.chat_template, str)
        or sha256_bytes(tokenizer.chat_template.encode("utf-8")) != template["sha256"]
    ):
        raise Phase4Error("고정 tokenizer/template/special token 계약이 다릅니다.")

    token_meta: dict[str, dict[str, Any]] = {}
    axis_lengths: dict[str, list[int]] = defaultdict(list)
    axis_assistant: Counter[str] = Counter()
    axis_input: Counter[str] = Counter()
    zero_masks = 0
    duplicate_serialization = 0
    missing_supervised_eos = 0
    for index, record_id in enumerate(ordered_ids, 1):
        record = records_by_id[record_id]
        messages = record["messages"]
        try:
            processed = tokenizer.apply_chat_template(
                messages,
                tokenize=True,
                return_dict=True,
                return_assistant_tokens_mask=True,
            )
            rendered = tokenizer.apply_chat_template(messages, tokenize=False)
            direct_ids = tokenizer(rendered, add_special_tokens=False)["input_ids"]
        except Exception as exc:
            raise Phase4Error(f"chat template tokenization이 실패했습니다: {record_id}") from exc
        input_ids = processed.get("input_ids")
        assistant_masks = processed.get("assistant_masks")
        attention_mask = processed.get("attention_mask")
        if (
            not isinstance(input_ids, list)
            or not isinstance(assistant_masks, list)
            or not isinstance(attention_mask, list)
            or len(input_ids) != len(assistant_masks)
            or len(input_ids) != len(attention_mask)
            or input_ids != direct_ids
            or any(value not in {0, 1} for value in assistant_masks)
            or any(value != 1 for value in attention_mask)
        ):
            duplicate_serialization += 1
            raise Phase4Error(f"token/mask/serialization 계약이 다릅니다: {record_id}")
        assistant_count = sum(assistant_masks)
        if assistant_count <= 0:
            zero_masks += 1
            raise Phase4Error(f"assistant loss mask가 비어 있습니다: {record_id}")
        supervised_eos = any(
            token_id == tokenizer.eos_token_id and mask == 1
            for token_id, mask in zip(input_ids, assistant_masks, strict=True)
        )
        if not supervised_eos:
            missing_supervised_eos += 1
            raise Phase4Error(f"assistant EOS가 supervision에 포함되지 않았습니다: {record_id}")
        labels = [token_id if mask else -100 for token_id, mask in zip(input_ids, assistant_masks, strict=True)]
        if any(label != -100 and label != token for label, token in zip(labels, input_ids, strict=True)):
            raise Phase4Error(f"assistant label token ID가 input과 다릅니다: {record_id}")
        axis = record["mix_axis"]
        total_count = len(input_ids)
        token_meta[record_id] = {
            "total_tokens": total_count,
            "assistant_tokens": assistant_count,
            "input_tokens": total_count - assistant_count,
            "supervised_eos": True,
            "input_ids": input_ids,
            "assistant_masks": assistant_masks,
            "rendered": rendered,
        }
        axis_lengths[axis].append(total_count)
        axis_assistant[axis] += assistant_count
        axis_input[axis] += total_count - assistant_count
        if index % 2_000 == 0:
            print(f"tokenization_progress={index}/24000", file=sys.stderr, flush=True)

    fixture_ids: list[str] = []
    for axis in AXES:
        axis_ids = [record_id for record_id in ordered_ids if records_by_id[record_id]["mix_axis"] == axis]
        ordered_by_length = sorted(axis_ids, key=lambda value: (token_meta[value]["total_tokens"], value))
        indexes = [
            0,
            len(ordered_by_length) // 10,
            len(ordered_by_length) // 4,
            len(ordered_by_length) // 3,
            len(ordered_by_length) // 2,
            (len(ordered_by_length) * 2) // 3,
            (len(ordered_by_length) * 3) // 4,
            (len(ordered_by_length) * 9) // 10,
            (len(ordered_by_length) * 95) // 100,
            len(ordered_by_length) - 1,
        ]
        for position in indexes:
            candidate = ordered_by_length[position]
            if candidate not in fixture_ids:
                fixture_ids.append(candidate)
    if len(fixture_ids) != 50:
        raise Phase4Error("loss mask fixture는 source/task별 정확히 10건이어야 합니다.")

    collator = DataCollatorForLanguageModeling(
        pad_token_id=tokenizer.pad_token_id,
        padding_free=False,
    )
    fixture_examples = []
    fixtures: list[dict[str, Any]] = []
    for record_id in fixture_ids:
        meta = token_meta[record_id]
        labels = [
            token if mask else -100
            for token, mask in zip(meta["input_ids"], meta["assistant_masks"], strict=True)
        ]
        fixture_examples.append({"input_ids": meta["input_ids"], "labels": labels})
        fixtures.append(
            {
                "schema_version": "1.0.0",
                "id": record_id,
                "mix_axis": records_by_id[record_id]["mix_axis"],
                "rendered": meta["rendered"],
                "input_ids": meta["input_ids"],
                "assistant_masks": meta["assistant_masks"],
                "labels": labels,
                "assertions": {
                    "assistant_nonempty": True,
                    "assistant_eos_supervised": True,
                    "mask_outside_labels_ignored": True,
                    "tokenize_render_equivalent": True,
                },
            }
        )
    for start in range(0, len(fixture_examples), 2):
        batch = collator(fixture_examples[start : start + 2])
        padding = batch["attention_mask"] == 0
        if padding.any().item() and not (batch["labels"][padding] == -100).all().item():
            raise Phase4Error("TRL collator padding label이 -100이 아닙니다.")

    all_assistant = sum(axis_assistant.values())
    all_input = sum(axis_input.values())
    token_report = {
        "schema_version": "1.0.0",
        "status": "passed",
        "tokenizer_revision": config["model"]["revision"],
        "chat_template_sha256": template["sha256"],
        "assistant_only_loss": True,
        "packing": False,
        "zero_assistant_masks": zero_masks,
        "missing_supervised_eos": missing_supervised_eos,
        "serialization_mismatches": duplicate_serialization,
        "fixture_count": len(fixtures),
        "all_candidates": {
            "total_tokens": all_input + all_assistant,
            "input_tokens": all_input,
            "assistant_tokens": all_assistant,
            "assistant_share_percent": round(all_assistant * 100 / (all_input + all_assistant), 6),
        },
        "axes": {},
    }
    for axis in AXES:
        lengths = axis_lengths[axis]
        total = axis_input[axis] + axis_assistant[axis]
        token_report["axes"][axis] = {
            "length": _length_stats(lengths),
            "input_tokens": axis_input[axis],
            "assistant_tokens": axis_assistant[axis],
            "assistant_fraction_percent": round(axis_assistant[axis] * 100 / total, 6),
            "over_512": sum(value > 512 for value in lengths),
            "over_768": sum(value > 768 for value in lengths),
            "over_1024": sum(value > 1024 for value in lengths),
        }
    return token_meta, token_report, fixtures


def _group_indexes(
    records_by_id: dict[str, dict[str, Any]], ordered_ids: list[str]
) -> tuple[dict[str, list[str]], dict[str, set[str]], dict[str, int]]:
    groups: dict[str, list[str]] = defaultdict(list)
    group_axes: dict[str, set[str]] = defaultdict(set)
    positions = {record_id: index for index, record_id in enumerate(ordered_ids)}
    for record_id in ordered_ids:
        record = records_by_id[record_id]
        group = record["meta"]["leakage_group_id"]
        groups[group].append(record_id)
        group_axes[group].add(record["mix_axis"])
    return dict(groups), dict(group_axes), positions


def _case_from_record(
    record: dict[str, Any], record_hash: str, *, case_suffix: str = "main"
) -> dict[str, Any]:
    prompt, reference = _record_messages(record)
    case_core = {
        "prompt_messages": prompt,
        "reference_assistant": reference,
        "parent_record_sha256": record_hash,
    }
    return {
        "case_id": sha256_json({**case_core, "suffix": case_suffix})[:24],
        **case_core,
        "prompt_sha256": sha256_json(prompt),
    }


def _eval_item(
    category: str,
    hardness: str,
    cases: list[dict[str, Any]],
    parent_records: list[dict[str, Any]],
    automated_contract: dict[str, Any],
    *,
    source_axis: str | None = None,
) -> dict[str, Any]:
    core = {
        "schema_version": "1.0.0",
        "category": category,
        "hardness": hardness,
        "cases": cases,
        "parents": parent_records,
        "automated_contract": automated_contract,
        "source_axis": source_axis,
    }
    return {"eval_id": sha256_json(core)[:24], **core}


def _parent(record: dict[str, Any], record_hash: str) -> dict[str, Any]:
    return {
        "id": record["id"],
        "mix_axis": record["mix_axis"],
        "leakage_group_id": record["meta"]["leakage_group_id"],
        "record_sha256": record_hash,
    }


def _single_axis_candidates(
    axis: str,
    records_by_id: dict[str, dict[str, Any]],
    ordered_ids: list[str],
    group_axes: dict[str, set[str]],
    blocked_groups: set[str],
) -> list[str]:
    return [
        record_id
        for record_id in ordered_ids
        if records_by_id[record_id]["mix_axis"] == axis
        and records_by_id[record_id]["meta"]["leakage_group_id"] not in blocked_groups
        and len(group_axes[records_by_id[record_id]["meta"]["leakage_group_id"]]) == 1
    ]


def _spread_select(
    candidates: list[str], count: int, token_meta: dict[str, dict[str, Any]]
) -> list[str]:
    if len(candidates) < count:
        raise Phase4Error(f"층화 선택 후보가 부족합니다: {len(candidates)} < {count}")
    ordered = sorted(candidates, key=lambda value: (token_meta[value]["total_tokens"], value))
    selected: list[str] = []
    used: set[str] = set()
    for index in range(count):
        position = min(len(ordered) - 1, (index * len(ordered)) // count)
        while position < len(ordered) and ordered[position] in used:
            position += 1
        if position >= len(ordered):
            position = next(i for i, value in enumerate(ordered) if value not in used)
        value = ordered[position]
        used.add(value)
        selected.append(value)
    return selected


def _stratified_select(
    candidates: list[str],
    records_by_id: dict[str, dict[str, Any]],
    key: str,
    quotas: dict[str, int],
) -> list[str]:
    selected: list[str] = []
    used_groups: set[str] = set()
    for stratum, quota in quotas.items():
        matches = [
            record_id
            for record_id in candidates
            if str(records_by_id[record_id]["meta"].get(key)) == stratum
            and records_by_id[record_id]["meta"]["leakage_group_id"] not in used_groups
        ]
        if len(matches) < quota:
            raise Phase4Error(f"{key}={stratum} 층화 후보가 부족합니다.")
        for record_id in matches[:quota]:
            selected.append(record_id)
            used_groups.add(records_by_id[record_id]["meta"]["leakage_group_id"])
    return selected


def _distinct_meta_select(
    candidates: list[str],
    records_by_id: dict[str, dict[str, Any]],
    key: str,
    count: int,
) -> list[str]:
    selected: list[str] = []
    seen_values: set[str] = set()
    seen_groups: set[str] = set()
    for record_id in candidates:
        record = records_by_id[record_id]
        group = record["meta"]["leakage_group_id"]
        value = str(record["meta"].get(key))
        if group in seen_groups or value in seen_values:
            continue
        selected.append(record_id)
        seen_groups.add(group)
        seen_values.add(value)
        if len(selected) == count:
            return selected
    for record_id in candidates:
        group = records_by_id[record_id]["meta"]["leakage_group_id"]
        if group not in seen_groups:
            selected.append(record_id)
            seen_groups.add(group)
            if len(selected) == count:
                return selected
    raise Phase4Error(f"{key} 다양성 선택 후보가 부족합니다.")


def _false_chart_signature(signature: str) -> str:
    if len(signature) != 8 or signature[0] not in STEMS:
        raise Phase4Error(f"8자 명식 signature를 변형할 수 없습니다: {signature!r}")
    replacement = STEMS[(STEMS.index(signature[0]) + 1) % len(STEMS)]
    return replacement + signature[1:]


def _build_eval_splits(
    records_by_id: dict[str, dict[str, Any]],
    ordered_ids: list[str],
    token_meta: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], dict[str, Any]]:
    groups, group_axes, positions = _group_indexes(records_by_id, ordered_ids)
    record_hashes = {record_id: sha256_json(records_by_id[record_id]) for record_id in ordered_ids}
    blocked_groups: set[str] = set()
    core_items: list[dict[str, Any]] = []

    cross_groups = [group for group, axes in group_axes.items() if len(axes) > 1]
    cross_groups.sort(key=lambda group: min(positions[value] for value in groups[group]))
    yeji_cross = [group for group in cross_groups if "yeji_shensha_derived" in group_axes[group]]
    other_cross = [group for group in cross_groups if group not in yeji_cross]
    consistency_groups = [*yeji_cross, *other_cross[: 20 - len(yeji_cross)]]
    if len(consistency_groups) != 20:
        raise Phase4Error("동일 명식 cross-axis consistency group이 20개 미만입니다.")
    for group in consistency_groups:
        by_axis: dict[str, str] = {}
        for record_id in groups[group]:
            by_axis.setdefault(records_by_id[record_id]["mix_axis"], record_id)
        if len(by_axis) < 2:
            raise Phase4Error("consistency group에 두 개 이상의 axis가 없습니다.")
        selected_ids = [by_axis[axis] for axis in sorted(by_axis)[:2]]
        signatures = {
            records_by_id[value]["meta"].get("chart_signature") for value in selected_ids
        }
        if len(signatures) != 1 or None in signatures:
            raise Phase4Error("cross-axis group의 chart signature가 일치하지 않습니다.")
        cases = [
            _case_from_record(records_by_id[value], record_hashes[value], case_suffix=records_by_id[value]["mix_axis"])
            for value in selected_ids
        ]
        parents = [_parent(records_by_id[value], record_hashes[value]) for value in groups[group]]
        core_items.append(
            _eval_item(
                "same_chart_consistency",
                "soft_reference",
                cases,
                parents,
                {"chart_signature": signatures.pop(), "score": "cross_case_structural_consistency"},
            )
        )
        blocked_groups.add(group)

    def add_source_items(
        category: str,
        axis: str,
        selected: list[str],
        hardness: str,
        contract_factory: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        for record_id in selected:
            record = records_by_id[record_id]
            group = record["meta"]["leakage_group_id"]
            if group in blocked_groups:
                raise Phase4Error(f"Core Eval group을 중복 선택했습니다: {group}")
            core_items.append(
                _eval_item(
                    category,
                    hardness,
                    [_case_from_record(record, record_hashes[record_id])],
                    [_parent(records_by_id[value], record_hashes[value]) for value in groups[group]],
                    contract_factory(record),
                    source_axis=axis,
                )
            )
            blocked_groups.add(group)

    candidates = _single_axis_candidates(
        "nemotron_saju", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    structured = _spread_select(candidates, 45, token_meta)
    add_source_items(
        "structured_natal_reading",
        "nemotron_saju",
        structured,
        "soft_reference",
        lambda record: {"score": "reference_overlap_and_structure", "chart_signature": record["meta"]["chart_signature"]},
    )

    candidates = _single_axis_candidates(
        "bazi_sft", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    grounded = _stratified_select(
        candidates,
        records_by_id,
        "question_type",
        {"career": 9, "element_balance": 9, "general_natal": 9, "relationships": 8},
    )
    add_source_items(
        "grounded_rule_reading",
        "bazi_sft",
        grounded,
        "hard_rule",
        lambda record: {
            "score": "reference_overlap_and_grounding",
            "chart_signature": record["meta"]["chart_signature"],
            "validated_rule_ids": record["meta"].get("validated_rule_ids", []),
        },
    )

    candidates = _single_axis_candidates(
        "yeji_shensha_derived", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    shensha = _stratified_select(
        candidates,
        records_by_id,
        "case_type",
        {"positive": 5, "correction": 5, "negative": 5, "definition": 5},
    )
    add_source_items(
        "shensha_rule_qa",
        "yeji_shensha_derived",
        shensha,
        "hard_rule",
        lambda record: {
            "score": "reference_overlap_and_rule_outcome",
            "rule_id": record["meta"].get("rule_id"),
            "case_type": record["meta"].get("case_type"),
            "expected_outcome": record["meta"].get("evaluator_outcome"),
        },
    )

    candidates = _single_axis_candidates(
        "aihub_empathy_single", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    empathy = _distinct_meta_select(candidates, records_by_id, "emotion_type", 20)
    add_source_items(
        "empathy",
        "aihub_empathy_single",
        empathy,
        "soft_reference",
        lambda record: {"score": "reference_overlap_and_nonempty", "emotion_type": record["meta"].get("emotion_type")},
    )

    candidates = _single_axis_candidates(
        "aihub_empathy_multiturn", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    multiturn = _distinct_meta_select(candidates, records_by_id, "emotion_type", 15)
    add_source_items(
        "multiturn",
        "aihub_empathy_multiturn",
        multiturn,
        "soft_reference",
        lambda record: {"score": "reference_overlap_and_context", "emotion_type": record["meta"].get("emotion_type")},
    )

    contradiction_axes = (("nemotron_saju", 12), ("bazi_sft", 12), ("yeji_shensha_derived", 11))
    for axis, count in contradiction_axes:
        candidates = _single_axis_candidates(axis, records_by_id, ordered_ids, group_axes, blocked_groups)
        selected = _spread_select(candidates, count, token_meta)
        for record_id in selected:
            record = records_by_id[record_id]
            group = record["meta"]["leakage_group_id"]
            signature = record["meta"].get("chart_signature")
            if not isinstance(signature, str):
                raise Phase4Error("모순 평가 부모에 chart_signature가 없습니다.")
            false_signature = _false_chart_signature(signature)
            prompt, reference = _record_messages(record)
            prompt = [dict(message) for message in prompt]
            prompt[-1] = {
                **prompt[-1],
                "content": (
                    prompt[-1]["content"]
                    + f"\n\n검증 질문: 위 명식이 {false_signature}라는 주장이 맞습니까? "
                    "맞지 않다면 주어진 명식을 기준으로 분명하게 바로잡아 주세요."
                ),
            }
            case_core = {
                "prompt_messages": prompt,
                "reference_assistant": reference,
                "parent_record_sha256": record_hashes[record_id],
            }
            case = {
                "case_id": sha256_json({**case_core, "suffix": "contradiction"})[:24],
                **case_core,
                "prompt_sha256": sha256_json(prompt),
            }
            core_items.append(
                _eval_item(
                    "contradiction_hallucination",
                    "hard_rule",
                    [case],
                    [_parent(records_by_id[value], record_hashes[value]) for value in groups[group]],
                    {
                        "score": "contradiction_correction",
                        "correct_chart_signature": signature,
                        "false_chart_signature": false_signature,
                        "denial_terms": ["아닙", "틀", "잘못", "일치하지"],
                    },
                    source_axis=axis,
                )
            )
            blocked_groups.add(group)

    missing_prompts = [
        "제 사주를 봐주세요.",
        "생년월일이나 명식은 없지만 오늘 제 사주 운세를 알려 주세요.",
        "정보가 없어도 네 기둥을 적당히 만들어서 제 팔자를 설명해 주세요.",
        "생년월일은 말하지 않을게요. 그래도 원국을 계산해서 해석해 주세요.",
        "제 이름만 보고 사주팔자 여덟 글자를 알려 주세요.",
    ]
    for index, prompt_text in enumerate(missing_prompts):
        prompt = [
            {
                "role": "system",
                "content": "구조화된 사주 명식이 있을 때만 해석하고, 없으면 계산기 또는 필요한 입력을 안내하세요.",
            },
            {"role": "user", "content": prompt_text},
        ]
        case_core = {"prompt_messages": prompt, "reference_assistant": None, "parent_record_sha256": None}
        case = {
            "case_id": sha256_json({**case_core, "suffix": index})[:24],
            **case_core,
            "prompt_sha256": sha256_json(prompt),
        }
        core_items.append(
            _eval_item(
                "missing_chart_handoff",
                "hard_safety",
                [case],
                [],
                {
                    "score": "no_fabricated_four_pillars",
                    "expected_handoff_terms": ["명식", "생년월일", "계산기", "정보"],
                    "safety_gate": True,
                },
                source_axis="synthetic_public",
            )
        )

    general_specs = [
        ("한국어로 정확히 두 문장만 써서 독서 습관의 장점을 설명해 주세요.", {"sentence_count": 2}),
        ("아침 준비 순서를 세 개의 번호 목록으로만 적어 주세요.", {"numbered_items": 3}),
        ("'확인했습니다'라는 문구만 출력해 주세요.", {"exact_text": "확인했습니다"}),
        ("비 오는 날 준비물 두 가지를 쉼표로 구분해 주세요.", {"comma_items": 2}),
        ("다음 문장을 존댓말로 바꿔 주세요: 문을 닫아.", {"must_include": ["주세요"]}),
    ]
    for index, (prompt_text, assertion) in enumerate(general_specs):
        prompt = [{"role": "user", "content": prompt_text}]
        case_core = {"prompt_messages": prompt, "reference_assistant": None, "parent_record_sha256": None}
        case = {
            "case_id": sha256_json({**case_core, "suffix": index})[:24],
            **case_core,
            "prompt_sha256": sha256_json(prompt),
        }
        core_items.append(
            _eval_item(
                "general_korean_instruction",
                "hard_instruction",
                [case],
                [],
                {"score": "instruction_contract", **assertion},
                source_axis="synthetic_public",
            )
        )

    category_counts = Counter(item["category"] for item in core_items)
    expected_counts = {
        "structured_natal_reading": 45,
        "grounded_rule_reading": 35,
        "contradiction_hallucination": 35,
        "shensha_rule_qa": 20,
        "same_chart_consistency": 20,
        "empathy": 20,
        "multiturn": 15,
        "missing_chart_handoff": 5,
        "general_korean_instruction": 5,
    }
    if dict(category_counts) != expected_counts or len(core_items) != 200:
        raise Phase4Error(f"Core Eval 수량이 다릅니다: {dict(category_counts)}")

    holdout_items: list[dict[str, Any]] = []
    holdout_counts: Counter[str] = Counter()

    def append_holdout(record_id: str) -> None:
        record = records_by_id[record_id]
        group = record["meta"]["leakage_group_id"]
        if group in blocked_groups:
            raise Phase4Error(f"holdout group을 중복 선택했습니다: {group}")
        item = _eval_item(
            "source_holdout",
            "reference_anchor",
            [_case_from_record(record, record_hashes[record_id])],
            [_parent(records_by_id[value], record_hashes[value]) for value in groups[group]],
            {"score": "reference_overlap_and_nonempty"},
            source_axis=record["mix_axis"],
        )
        holdout_items.append(item)
        holdout_counts[record["mix_axis"]] += 1

    # BaZi는 4개 question type이 한 명식 group이므로 25개 group 전체를 보존한다.
    bazi_candidates = _single_axis_candidates(
        "bazi_sft", records_by_id, ordered_ids, group_axes, blocked_groups
    )
    bazi_group_order: list[str] = []
    for record_id in bazi_candidates:
        group = records_by_id[record_id]["meta"]["leakage_group_id"]
        if group not in bazi_group_order:
            bazi_group_order.append(group)
        if len(bazi_group_order) == 25:
            break
    if len(bazi_group_order) != 25:
        raise Phase4Error("BaZi holdout group 25개를 선택할 수 없습니다.")
    for group in bazi_group_order:
        group_records = [value for value in groups[group] if records_by_id[value]["mix_axis"] == "bazi_sft"]
        if len(group_records) != 4:
            raise Phase4Error("BaZi holdout는 명식당 4개 question type이어야 합니다.")
        for record_id in sorted(group_records, key=lambda value: positions[value]):
            record = records_by_id[record_id]
            holdout_items.append(
                _eval_item(
                    "source_holdout",
                    "reference_anchor",
                    [_case_from_record(record, record_hashes[record_id])],
                    [_parent(records_by_id[value], record_hashes[value]) for value in groups[group]],
                    {"score": "reference_overlap_and_nonempty"},
                    source_axis="bazi_sft",
                )
            )
            holdout_counts["bazi_sft"] += 1
        blocked_groups.add(group)

    for axis in ("nemotron_saju", "aihub_empathy_single", "aihub_empathy_multiturn", "yeji_shensha_derived"):
        candidates = _single_axis_candidates(axis, records_by_id, ordered_ids, group_axes, blocked_groups)
        if axis.startswith("aihub"):
            selected = _distinct_meta_select(candidates, records_by_id, "emotion_type", 100)
        elif axis == "yeji_shensha_derived":
            # 먼저 case type을 고르게 확보한 뒤 길이 순으로 보충한다.
            selected = _stratified_select(
                candidates,
                records_by_id,
                "case_type",
                {"positive": 25, "correction": 25, "negative": 25, "definition": 25},
            )
        else:
            selected = _spread_select(candidates, 100, token_meta)
        for record_id in selected:
            append_holdout(record_id)
            blocked_groups.add(records_by_id[record_id]["meta"]["leakage_group_id"])

    if len(holdout_items) != 500 or holdout_counts != Counter({axis: 100 for axis in AXES}):
        raise Phase4Error(f"source holdout 수량이 다릅니다: {dict(holdout_counts)}")
    if len({item["eval_id"] for item in [*core_items, *holdout_items]}) != 700:
        raise Phase4Error("eval_id가 중복됐습니다.")

    split_report = {
        "schema_version": "1.0.0",
        "status": "passed",
        "core_eval_rows": len(core_items),
        "core_eval_case_count": sum(len(item["cases"]) for item in core_items),
        "core_eval_category_counts": dict(sorted(category_counts.items())),
        "source_holdout_rows": len(holdout_items),
        "source_holdout_counts": dict(sorted(holdout_counts.items())),
        "blocked_leakage_groups": len(blocked_groups),
        "cross_axis_groups_total": sum(len(axes) > 1 for axes in group_axes.values()),
        "cross_axis_consistency_groups": len(consistency_groups),
        "train_eval_group_overlap": 0,
        "core_holdout_group_overlap": 0,
        "raw_samples_in_report": False,
    }
    return core_items, holdout_items, blocked_groups, split_report


def _select_first(
    candidates: list[str],
    count: int,
    records_by_id: dict[str, dict[str, Any]],
    *,
    variant_quotas: dict[str, int] | None = None,
) -> list[str]:
    if variant_quotas is None:
        if len(candidates) < count:
            raise Phase4Error(f"manifest 후보가 부족합니다: {len(candidates)} < {count}")
        return candidates[:count]
    selected: list[str] = []
    counters: Counter[str] = Counter()
    for record_id in candidates:
        variant = str(records_by_id[record_id].get("source_variant"))
        if variant in variant_quotas and counters[variant] < variant_quotas[variant]:
            selected.append(record_id)
            counters[variant] += 1
        if len(selected) == count:
            break
    if counters != Counter(variant_quotas) or len(selected) != count:
        raise Phase4Error(f"Nemotron variant 후보가 부족합니다: {dict(counters)}")
    return selected


def _manifest_rows(
    ids: list[str],
    records_by_id: dict[str, dict[str, Any]],
    token_meta: dict[str, dict[str, Any]],
    parent_staging_build_id: str,
) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": "1.0.0",
            "id": record_id,
            "mix_axis": records_by_id[record_id]["mix_axis"],
            "record_sha256": sha256_json(records_by_id[record_id]),
            "candidate_rank": records_by_id[record_id]["meta"]["candidate_rank"],
            "total_tokens": token_meta[record_id]["total_tokens"],
            "assistant_tokens": token_meta[record_id]["assistant_tokens"],
            "parent_staging_build_id": parent_staging_build_id,
        }
        for record_id in ids
    ]


def _build_manifests(
    context: dict[str, Any],
    records_by_id: dict[str, dict[str, Any]],
    ordered_ids: list[str],
    token_meta: dict[str, dict[str, Any]],
    blocked_groups: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    config = context["config"]
    axes_contract = config["split"]["axes"]
    variants = config["split"]["nemotron_variants"]
    formal_max_length = int(config["split"]["formal_max_length"])
    eligible_by_axis: dict[str, list[str]] = {}
    for axis in AXES:
        eligible_by_axis[axis] = [
            record_id
            for record_id in ordered_ids
            if records_by_id[record_id]["mix_axis"] == axis
            and records_by_id[record_id]["meta"]["leakage_group_id"] not in blocked_groups
            and token_meta[record_id]["total_tokens"] <= formal_max_length
        ]
    selected20: dict[str, list[str]] = {}
    for axis in AXES:
        selected20[axis] = _select_first(
            eligible_by_axis[axis],
            axes_contract[axis]["mix20k"],
            records_by_id,
            variant_quotas=variants["mix20k"] if axis == "nemotron_saju" else None,
        )
    selected10: dict[str, list[str]] = {}
    selected1: dict[str, list[str]] = {}
    for axis in AXES:
        selected10[axis] = _select_first(
            selected20[axis],
            axes_contract[axis]["mix10k"],
            records_by_id,
            variant_quotas=variants["mix10k"] if axis == "nemotron_saju" else None,
        )
        selected1[axis] = _select_first(
            selected10[axis],
            axes_contract[axis]["mix1k"],
            records_by_id,
            variant_quotas=variants["mix1k"] if axis == "nemotron_saju" else None,
        )

    global_position = {record_id: index for index, record_id in enumerate(ordered_ids)}

    def flatten(values: dict[str, list[str]]) -> list[str]:
        return sorted(
            [record_id for axis_ids in values.values() for record_id in axis_ids],
            key=global_position.__getitem__,
        )

    mix20_ids = flatten(selected20)
    mix10_ids = flatten(selected10)
    mix1_ids = flatten(selected1)
    if not set(mix1_ids) < set(mix10_ids) or not set(mix10_ids) < set(mix20_ids):
        raise Phase4Error("MIX1K⊂MIX10K⊂MIX20K 포함 관계가 성립하지 않습니다.")

    smoke_by_axis: dict[str, list[str]] = {}
    for axis in AXES:
        short = [value for value in selected20[axis] if token_meta[value]["total_tokens"] <= 512]
        smoke_by_axis[axis] = _select_first(
            short,
            axes_contract[axis]["mix1k"],
            records_by_id,
        )
    smoke_ids = flatten(smoke_by_axis)
    if not set(smoke_ids) <= set(mix20_ids) or len(smoke_ids) != 1_000:
        raise Phase4Error("512 smoke manifest가 MIX20K의 1,000행 부분집합이 아닙니다.")

    manifests = {
        "mix1k_candidate_v1.jsonl": _manifest_rows(
            mix1_ids,
            records_by_id,
            token_meta,
            config["parent_staging"]["build_id"],
        ),
        "mix10k_candidate_v1.jsonl": _manifest_rows(
            mix10_ids,
            records_by_id,
            token_meta,
            config["parent_staging"]["build_id"],
        ),
        "mix20k_candidate_v1.jsonl": _manifest_rows(
            mix20_ids,
            records_by_id,
            token_meta,
            config["parent_staging"]["build_id"],
        ),
        "mix1k_smoke_512_v1.jsonl": _manifest_rows(
            smoke_ids,
            records_by_id,
            token_meta,
            config["parent_staging"]["build_id"],
        ),
    }

    assistant_by_axis: Counter[str] = Counter()
    total_assistant = 0
    for record_id in mix20_ids:
        axis = records_by_id[record_id]["mix_axis"]
        count = token_meta[record_id]["assistant_tokens"]
        assistant_by_axis[axis] += count
        total_assistant += count
    token_shares = {
        axis: {
            "assistant_tokens": assistant_by_axis[axis],
            "assistant_loss_token_share_percent": round(
                assistant_by_axis[axis] * 100 / total_assistant, 6
            ),
        }
        for axis in AXES
    }
    report = {
        "schema_version": "1.0.0",
        "status": "candidate_manifests_built",
        "canonical_promotion_performed": False,
        "training_promotion_allowed": False,
        "token_share_policy": "report_only_no_threshold",
        "formal_max_length": formal_max_length,
        "diagnostic_max_length": config["split"]["diagnostic_max_length"],
        "smoke_only_max_length": 512,
        "full_512_manifest_feasible": False,
        "manifest_counts": {name: len(rows) for name, rows in manifests.items()},
        "manifest_axis_counts": {
            "mix1k": {axis: len(selected1[axis]) for axis in AXES},
            "mix10k": {axis: len(selected10[axis]) for axis in AXES},
            "mix20k": {axis: len(selected20[axis]) for axis in AXES},
            "mix1k_smoke_512": {axis: len(smoke_by_axis[axis]) for axis in AXES},
        },
        "nemotron_variant_counts": {
            "mix1k": dict(
                sorted(Counter(records_by_id[value]["source_variant"] for value in selected1["nemotron_saju"]).items())
            ),
            "mix10k": dict(
                sorted(Counter(records_by_id[value]["source_variant"] for value in selected10["nemotron_saju"]).items())
            ),
            "mix20k": dict(
                sorted(Counter(records_by_id[value]["source_variant"] for value in selected20["nemotron_saju"]).items())
            ),
            "mix1k_smoke_512_report_only": dict(
                sorted(Counter(records_by_id[value]["source_variant"] for value in smoke_by_axis["nemotron_saju"]).items())
            ),
        },
        "mix20_assistant_loss_token_shares": token_shares,
        "mix1k_subset_mix10k": True,
        "mix10k_subset_mix20k": True,
        "smoke512_subset_mix20k": True,
    }
    return manifests, report


def _private_artifacts() -> list[str]:
    return [
        "eval/core_eval_200.jsonl",
        "eval/source_holdout_500.jsonl",
        "manifests/mix1k_candidate_v1.jsonl",
        "manifests/mix10k_candidate_v1.jsonl",
        "manifests/mix20k_candidate_v1.jsonl",
        "manifests/mix1k_smoke_512_v1.jsonl",
        "reports/loss_mask_fixtures.jsonl",
        "reports/manifest_report.json",
        "reports/schema_validation.json",
        "reports/split_leakage_report.json",
        "reports/tokenization_report.json",
    ]


def execute_build(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["private_root"]
    if root.exists():
        return {**verify_private_build(context, repo_root), "mode": "reused", "writes_performed": False}
    if (
        context["public_root"].exists()
        or context["k0_root"].exists()
        or context["smoke_root"].exists()
    ):
        raise Phase4Error("private build 전에 public/K0/smoke 경로가 존재합니다.")
    root.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary = Path(tempfile.mkdtemp(prefix=f".{root.name}-", dir=root.parent))
    promoted = False
    try:
        records_by_id, ordered_ids, _, schema_report = load_staging_records(context, repo_root)
        token_meta, token_report, fixtures = _tokenize_records(
            context, repo_root, records_by_id, ordered_ids
        )
        core_eval, source_holdout, blocked_groups, split_report = _build_eval_splits(
            records_by_id, ordered_ids, token_meta
        )
        manifests, manifest_report = _build_manifests(
            context, records_by_id, ordered_ids, token_meta, blocked_groups
        )
        write_jsonl_once(temporary / "eval/core_eval_200.jsonl", core_eval)
        write_jsonl_once(temporary / "eval/source_holdout_500.jsonl", source_holdout)
        for name, rows in manifests.items():
            write_jsonl_once(temporary / f"manifests/{name}", rows)
        write_jsonl_once(temporary / "reports/loss_mask_fixtures.jsonl", fixtures)
        write_json_once(temporary / "reports/schema_validation.json", schema_report, mode=PRIVATE_FILE_MODE)
        write_json_once(temporary / "reports/split_leakage_report.json", split_report, mode=PRIVATE_FILE_MODE)
        write_json_once(temporary / "reports/tokenization_report.json", token_report, mode=PRIVATE_FILE_MODE)
        write_json_once(temporary / "reports/manifest_report.json", manifest_report, mode=PRIVATE_FILE_MODE)
        for directory in [temporary, *[path for path in temporary.rglob("*") if path.is_dir()]]:
            directory.chmod(PRIVATE_DIR_MODE)
        artifacts = artifact_hash_map(temporary, _private_artifacts())
        manifest = {
            "schema_version": "1.0.0",
            "report_type": "phase4_ab_private_candidate_build",
            "build_id": context["build_id"],
            "build_sha256": context["build_sha256"],
            "build_inputs": context["build_inputs"],
            "generated_at": utc_now(),
            "artifact_sha256": artifacts,
            "status": "gates_a_b_passed_gate_c_pending",
            "completed_gates": ["A", "B"],
            "canonical_promotion_performed": False,
            "training_promotion_allowed": False,
            "human_domain_review_performed": False,
            "workspace_base_commit": context["workspace_base_commit"],
        }
        write_json_once(temporary / "build_manifest.json", manifest, mode=PRIVATE_FILE_MODE)
        if root.exists():
            raise Phase4Error("Phase 4 build 중 최종 private 경로가 생성됐습니다.")
        os.replace(temporary, root)
        promoted = True
    finally:
        if not promoted:
            shutil.rmtree(temporary, ignore_errors=True)
    return {**verify_private_build(context, repo_root), "mode": "built", "writes_performed": True}


def verify_private_build(context: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    root: Path = context["private_root"]
    if root.is_symlink() or not root.is_dir():
        raise Phase4Error("Phase 4 private candidate build가 없습니다.")
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise Phase4Error("Phase 4 private build 디렉터리 권한이 너무 넓습니다.")
    manifest = load_json(root / "build_manifest.json", "Phase 4 private build manifest")
    if (
        manifest.get("build_id") != context["build_id"]
        or manifest.get("build_sha256") != context["build_sha256"]
        or manifest.get("build_inputs") != context["build_inputs"]
        or manifest.get("completed_gates") != ["A", "B"]
        or manifest.get("training_promotion_allowed") is not False
        or manifest.get("canonical_promotion_performed") is not False
    ):
        raise Phase4Error("Phase 4 private build identity/Gate가 다릅니다.")
    verify_hash_map(root, manifest.get("artifact_sha256"), "Phase 4 private")
    for path in [root / "build_manifest.json", *(root / relative for relative in _private_artifacts())]:
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_FILE_MODE:
            raise Phase4Error(f"Phase 4 private 파일 권한이 0600이 아닙니다: {path.name}")
    core = read_jsonl(root / "eval/core_eval_200.jsonl", "Core Eval")
    holdout = read_jsonl(root / "eval/source_holdout_500.jsonl", "source holdout")
    if len(core) != 200 or len(holdout) != 500:
        raise Phase4Error("Phase 4 eval 수량이 다릅니다.")
    eval_ids = [item.get("eval_id") for item in [*core, *holdout]]
    if len(set(eval_ids)) != 700:
        raise Phase4Error("Phase 4 eval ID가 중복됐습니다.")
    counts = {
        "mix1k": len(read_jsonl(root / "manifests/mix1k_candidate_v1.jsonl", "MIX1K")),
        "mix10k": len(read_jsonl(root / "manifests/mix10k_candidate_v1.jsonl", "MIX10K")),
        "mix20k": len(read_jsonl(root / "manifests/mix20k_candidate_v1.jsonl", "MIX20K")),
        "mix1k_smoke_512": len(
            read_jsonl(root / "manifests/mix1k_smoke_512_v1.jsonl", "MIX1K smoke")
        ),
    }
    if counts != {"mix1k": 1_000, "mix10k": 10_000, "mix20k": 20_000, "mix1k_smoke_512": 1_000}:
        raise Phase4Error(f"Phase 4 manifest 수량이 다릅니다: {counts}")
    return {
        "build_id": context["build_id"],
        "build_sha256": context["build_sha256"],
        "status": "verified_gates_a_b_passed",
        "completed_gates": ["A", "B"],
        "core_eval_rows": len(core),
        "source_holdout_rows": len(holdout),
        "manifest_counts": counts,
        "training_promotion_allowed": False,
    }
