# phase4_export_external_review.py - 승인 MIX20K에서 외부 AI용 비제한 검수 패키지를 만든다.

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data.preprocess_adapters import PII_PATTERNS
from scripts.preflight.errors import Phase4Error
from scripts.preflight.phase4_common import (
    PRIVATE_DIR_MODE,
    PRIVATE_FILE_MODE,
    canonical_json_bytes,
    load_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    sha256_json,
    write_bytes_once,
)
from scripts.preflight.phase4_data import load_staging_records
from scripts.preflight.phase4_finalize import (
    canonical_identity,
    verify_finalized_phase4,
)
from scripts.preflight.phase4_preflight import DEFAULT_CONFIG

PACKAGE_SCHEMA_VERSION = "1.0.0"
EXPORT_VERSION = "v1.0.0"
PACKAGE_TYPE = "phase4_mix20k_external_ai_safe_review"
PACKAGE_PREFIX = "external-review"
MODEL_REPO_ID = "kakaocorp/kanana-2-1.3b-instruct"
MODEL_REVISION = "bf4786aa2a1908adce942d53976270132732f720"
CHAT_TEMPLATE_SHA256 = (
    "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3"
)
ZIP_TIMESTAMP = (2026, 8, 28, 0, 0, 0)
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SHA_LINE_PATTERN = re.compile(r"^([0-9a-f]{64})  ([^\r\n]+)$")

EXPECTED_AXIS_COUNTS = {
    "nemotron_saju": 11_000,
    "bazi_sft": 5_000,
    "aihub_empathy_single": 2_000,
    "aihub_empathy_multiturn": 1_000,
    "yeji_shensha_derived": 1_000,
}
EXTERNAL_SAFE_AXES = {
    "nemotron_saju",
    "bazi_sft",
    "yeji_shensha_derived",
}
RESTRICTED_AXES = {
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
}
EXPECTED_EXTERNAL_ROWS = sum(EXPECTED_AXIS_COUNTS[axis] for axis in EXTERNAL_SAFE_AXES)
EXPECTED_RESTRICTED_ROWS = sum(EXPECTED_AXIS_COUNTS[axis] for axis in RESTRICTED_AXES)

PACKAGE_FILES = (
    "START_HERE.md",
    "GPT_PRO_REVIEW_PROMPT.md",
    "MODEL_AND_TRAINING_CONTEXT.md",
    "DATA_SCOPE_AND_LICENSE.md",
    "candidate_20k_index.jsonl",
    "candidate_external_17k.jsonl",
    "training_external_17k.jsonl",
    "training_line_map.jsonl",
    "aihub_3k_aggregate.json",
    "candidate_vs_training_contract.json",
    "PACKAGE_MANIFEST.json",
    "SHA256SUMS.txt",
)
CONTENT_FILES = tuple(
    name
    for name in PACKAGE_FILES
    if name not in {"PACKAGE_MANIFEST.json", "SHA256SUMS.txt"}
)
FORBIDDEN_PROJECTED_KEYS = {
    "id",
    "record_sha256",
    "candidate_rank",
    "parent_staging_build_id",
    "raw_hash",
    "message_sha256",
    "source_group_id",
    "leakage_group_id",
    "calendar_anchor",
    "source_locator",
    "locator",
}
PROJECTED_TEXT_FIELDS = (
    "source",
    "source_revision",
    "source_variant",
    "mix_axis",
    "task",
    "domain",
    "license_expression",
    "usage_class",
    "provenance_status",
)
INDEX_FIELDS = {
    "schema_version",
    "review_id",
    "canonical_position",
    "source",
    "mix_axis",
    "task",
    "license_expression",
    "usage_class",
    "provenance_status",
    "total_tokens",
    "assistant_tokens",
    "content_status",
    "external_training_line",
}
CANDIDATE_FIELDS = {
    "schema_version",
    "review_id",
    "canonical_position",
    "external_training_line",
    *PROJECTED_TEXT_FIELDS,
    "quality_flags",
    "transformation_chain",
    "total_tokens",
    "assistant_tokens",
    "messages",
}
LINE_MAP_FIELDS = {
    "schema_version",
    "review_id",
    "canonical_position",
    "content_status",
    "external_training_line",
}
RESTRICTED_AGGREGATE_FIELDS = {
    "schema_version",
    "source_text_included",
    "row_ids_included",
    "record_hashes_included",
    "restricted_reason",
    "row_count",
    "source_counts",
    "axis_counts",
    "task_counts",
    "license_expression_counts",
    "transformation_counts",
    "quality_flag_counts",
    "token_totals",
    "partition_commitment_sha256",
}


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _jsonl_bytes(values: Sequence[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(value) + b"\n" for value in values)


def _read_json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise Phase4Error(f"{label} JSON을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise Phase4Error(f"{label} 최상위 값은 object여야 합니다.")
    return value


def _read_jsonl_payload(payload: bytes, label: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        text = payload.decode("utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line:
                raise Phase4Error(f"{label}에 빈 JSONL 행이 있습니다: {line_number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise Phase4Error(f"{label} 행이 object가 아닙니다: {line_number}")
            values.append(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4Error(f"{label} JSONL을 읽을 수 없습니다.") from exc
    if not payload.endswith(b"\n"):
        raise Phase4Error(f"{label} JSONL 끝에 newline이 없습니다.")
    return values


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _validated_safe_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or CONTROL_PATTERN.search(value)
        or any(pattern.search(value) for pattern in PII_PATTERNS)
    ):
        raise Phase4Error(f"{label} 외부 문자열이 안전하지 않습니다.")
    return value


def _validated_external_metadata(value: dict[str, Any], label: str) -> dict[str, Any]:
    metadata = {
        field: _validated_safe_text(value.get(field), f"{label} {field}")
        for field in PROJECTED_TEXT_FIELDS
    }
    quality_flags = value.get("quality_flags")
    if (
        not isinstance(quality_flags, dict)
        or not quality_flags
        or any(
            _validated_safe_text(flag, f"{label} quality flag") != flag
            or not isinstance(enabled, bool)
            for flag, enabled in quality_flags.items()
        )
    ):
        raise Phase4Error(f"{label} 품질 flag가 올바르지 않습니다.")
    transformations = value.get("transformation_chain")
    if (
        not isinstance(transformations, list)
        or not transformations
        or any(
            _validated_safe_text(item, f"{label} transformation") != item
            for item in transformations
        )
        or len(transformations) != len(set(transformations))
    ):
        raise Phase4Error(f"{label} 변환 이력이 올바르지 않습니다.")
    return {
        **metadata,
        "quality_flags": dict(quality_flags),
        "transformation_chain": list(transformations),
    }


def _validated_messages(
    value: Any, label: str, *, scan_pii: bool
) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) < 3:
        raise Phase4Error(f"{label} messages가 올바르지 않습니다.")
    messages: list[dict[str, str]] = []
    for index, message in enumerate(value, 1):
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message.get("role") not in {"system", "user", "assistant"}
            or not isinstance(message.get("content"), str)
            or not message["content"].strip()
            or CONTROL_PATTERN.search(message["content"])
        ):
            raise Phase4Error(f"{label} message가 올바르지 않습니다: {index}")
        if scan_pii and any(
            pattern.search(message["content"]) for pattern in PII_PATTERNS
        ):
            raise Phase4Error(f"{label} 외부 본문에서 개인정보 패턴을 탐지했습니다.")
        messages.append({"role": message["role"], "content": message["content"]})
    if messages[0]["role"] != "system" or messages[-1]["role"] != "assistant":
        raise Phase4Error(f"{label} role 경계가 올바르지 않습니다.")
    if not any(message["role"] == "user" for message in messages[:-1]):
        raise Phase4Error(f"{label} assistant 이전 user 메시지가 없습니다.")
    return messages


def _review_id(position: int) -> str:
    return f"MIX20K-{position:05d}"


def _project_records(
    manifest_rows: list[dict[str, Any]],
    records_by_id: dict[str, dict[str, Any]],
    *,
    expected_axis_counts: dict[str, int],
) -> dict[str, Any]:
    expected_total = sum(expected_axis_counts.values())
    expected_external = sum(
        expected_axis_counts.get(axis, 0) for axis in EXTERNAL_SAFE_AXES
    )
    expected_restricted = sum(
        expected_axis_counts.get(axis, 0) for axis in RESTRICTED_AXES
    )
    if len(manifest_rows) != expected_total:
        raise Phase4Error(f"MIX20K manifest 수량이 다릅니다: {len(manifest_rows)}")
    axis_counts = Counter(str(row.get("mix_axis")) for row in manifest_rows)
    if axis_counts != Counter(expected_axis_counts):
        raise Phase4Error(f"MIX20K axis 수량이 다릅니다: {dict(axis_counts)}")

    candidate_index: list[dict[str, Any]] = []
    candidate_external: list[dict[str, Any]] = []
    training_external: list[dict[str, Any]] = []
    line_map: list[dict[str, Any]] = []
    restricted_commitment: list[dict[str, Any]] = []
    restricted_sources: Counter[str] = Counter()
    restricted_tasks: Counter[str] = Counter()
    restricted_licenses: Counter[str] = Counter()
    restricted_transformations: Counter[str] = Counter()
    restricted_quality_true: Counter[str] = Counter()
    restricted_quality_false: Counter[str] = Counter()
    restricted_total_tokens = 0
    restricted_assistant_tokens = 0
    seen_ids: set[str] = set()
    external_line = 0

    for position, manifest in enumerate(manifest_rows, 1):
        record_id = manifest.get("id")
        axis = manifest.get("mix_axis")
        if (
            not isinstance(record_id, str)
            or not record_id
            or record_id in seen_ids
            or axis not in expected_axis_counts
        ):
            raise Phase4Error(
                f"MIX20K manifest identity가 올바르지 않습니다: {position}"
            )
        seen_ids.add(record_id)
        record = records_by_id.get(record_id)
        if (
            record is None
            or record.get("mix_axis") != axis
            or sha256_json(record) != manifest.get("record_sha256")
            or manifest.get("parent_staging_build_id") != "build-847088ee804d"
            or not isinstance(manifest.get("total_tokens"), int)
            or manifest["total_tokens"] <= 0
            or not isinstance(manifest.get("assistant_tokens"), int)
            or manifest["assistant_tokens"] <= 0
        ):
            raise Phase4Error(f"MIX20K/staging hash 계약이 다릅니다: {position}")

        source = _validated_safe_text(record.get("source"), f"{position} source")
        task = _validated_safe_text(record.get("task"), f"{position} task")
        license_expression = _validated_safe_text(
            record.get("license_expression"), f"{position} license"
        )
        usage_class = _validated_safe_text(
            record.get("usage_class"), f"{position} usage class"
        )
        provenance_status = _validated_safe_text(
            record.get("provenance_status"), f"{position} provenance"
        )
        restricted = axis in RESTRICTED_AXES
        if restricted != (source == "aihub_empathy"):
            raise Phase4Error(f"외부 반출 source 분류가 다릅니다: {position}")
        if not restricted and axis not in EXTERNAL_SAFE_AXES:
            raise Phase4Error(f"허용되지 않은 외부 반출 axis입니다: {axis}")

        review_id = _review_id(position)
        if not restricted:
            external_line += 1
        content_status = (
            "withheld_aihub_policy" if restricted else "included_external_safe"
        )
        external_training_line = None if restricted else external_line
        candidate_index.append(
            {
                "schema_version": PACKAGE_SCHEMA_VERSION,
                "review_id": review_id,
                "canonical_position": position,
                "source": source,
                "mix_axis": axis,
                "task": task,
                "license_expression": license_expression,
                "usage_class": usage_class,
                "provenance_status": provenance_status,
                "total_tokens": manifest["total_tokens"],
                "assistant_tokens": manifest["assistant_tokens"],
                "content_status": content_status,
                "external_training_line": external_training_line,
            }
        )
        line_map.append(
            {
                "schema_version": PACKAGE_SCHEMA_VERSION,
                "review_id": review_id,
                "canonical_position": position,
                "content_status": content_status,
                "external_training_line": external_training_line,
            }
        )

        if restricted:
            restricted_commitment.append(
                {
                    "canonical_position": position,
                    "id": record_id,
                    "record_sha256": manifest["record_sha256"],
                }
            )
            restricted_sources[str(source)] += 1
            restricted_tasks[task] += 1
            restricted_licenses[license_expression] += 1
            restricted_total_tokens += manifest["total_tokens"]
            restricted_assistant_tokens += manifest["assistant_tokens"]
            transformations = record.get("transformation_chain")
            if not isinstance(transformations, list) or not transformations:
                raise Phase4Error(f"AI Hub 변환 이력이 올바르지 않습니다: {position}")
            for transformation in transformations:
                restricted_transformations[
                    _validated_safe_text(
                        transformation, f"AI Hub {position} transformation"
                    )
                ] += 1
            quality_flags = record.get("quality_flags")
            if (
                not isinstance(quality_flags, dict)
                or not quality_flags
                or any(
                    _validated_safe_text(flag, f"AI Hub {position} quality flag")
                    != flag
                    or not isinstance(value, bool)
                    for flag, value in quality_flags.items()
                )
            ):
                raise Phase4Error(f"AI Hub 품질 flag가 올바르지 않습니다: {position}")
            for flag, value in quality_flags.items():
                (restricted_quality_true if value else restricted_quality_false)[
                    str(flag)
                ] += 1
            continue

        messages = _validated_messages(record.get("messages"), review_id, scan_pii=True)
        metadata = _validated_external_metadata(record, review_id)
        projected = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "review_id": review_id,
            "canonical_position": position,
            "external_training_line": external_line,
            **metadata,
            "total_tokens": manifest["total_tokens"],
            "assistant_tokens": manifest["assistant_tokens"],
            "messages": messages,
        }
        if _walk_keys(projected) & FORBIDDEN_PROJECTED_KEYS:
            raise Phase4Error(f"외부 후보 투영에 내부 필드가 남았습니다: {review_id}")
        candidate_external.append(projected)
        training_external.append({"messages": messages})

    if (
        external_line != expected_external
        or len(restricted_commitment) != expected_restricted
    ):
        raise Phase4Error("외부 17K/제한 3K 분할 수량이 다릅니다.")
    return {
        "candidate_index": candidate_index,
        "candidate_external": candidate_external,
        "training_external": training_external,
        "line_map": line_map,
        "restricted_aggregate": {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "source_text_included": False,
            "row_ids_included": False,
            "record_hashes_included": False,
            "restricted_reason": "AI Hub 승인 없는 제3자 열람 및 국외 반출 금지",
            "row_count": len(restricted_commitment),
            "source_counts": dict(sorted(restricted_sources.items())),
            "axis_counts": {
                axis: axis_counts[axis] for axis in sorted(RESTRICTED_AXES)
            },
            "task_counts": dict(sorted(restricted_tasks.items())),
            "license_expression_counts": dict(sorted(restricted_licenses.items())),
            "transformation_counts": dict(sorted(restricted_transformations.items())),
            "quality_flag_counts": {
                flag: {
                    "true": restricted_quality_true[flag],
                    "false": restricted_quality_false[flag],
                }
                for flag in sorted(
                    set(restricted_quality_true) | set(restricted_quality_false)
                )
            },
            "token_totals": {
                "total_tokens": restricted_total_tokens,
                "assistant_tokens": restricted_assistant_tokens,
            },
            "partition_commitment_sha256": sha256_json(restricted_commitment),
        },
    }


def _start_here(identity: dict[str, Any]) -> bytes:
    return f"""# MIX20K 외부 AI 안전 검수 패키지

이 패키지는 사주 1.3B baseline의 승인된 canonical MIX20K를 외부 GPT Pro에서 검수하기 위한 최소 투영본입니다.

## 먼저 알아야 할 범위

- 전체 계약 인덱스: 20,000건
- 실제 본문 포함: 17,000건 (Nemotron 11,000 + bazi 파생 5,000 + YEJI 파생 1,000)
- 본문 제외: AI Hub 감성대화 3,000건 (집계와 partition commitment만 포함)
- canonical build: `{identity["canonical_build_id"]}`
- candidate/canonical manifest byte 동일: `true`
- Phase 5 실제 학습: 수행하지 않음

AI Hub 행은 가공됐더라도 원천 대화 표현을 유지하므로 개인 GPT Pro에 업로드할 수 없습니다. 이 패키지를 전체 20K 본문 검수본이나 학습 데이터 대체물로 주장하지 마세요.

## 사용 순서

1. 가능하면 ZIP을 로컬에 풀고 `sha256sum -c SHA256SUMS.txt`로 내부 무결성을 확인합니다.
2. GPT Pro가 ZIP을 직접 읽지 못하면 압축을 풀어 이 문서, `MODEL_AND_TRAINING_CONTEXT.md`, `GPT_PRO_REVIEW_PROMPT.md`, JSONL 파일을 함께 첨부합니다.
3. `GPT_PRO_REVIEW_PROMPT.md`의 내용을 새 대화 첫 지시로 붙여 넣습니다.
4. GPT가 만든 네 결과 파일을 내려받아 원본 ZIP과 별도로 보관합니다.

OpenAI 공식 파일 작업 안내: https://learn.chatgpt.com/docs/artifacts-viewer
AI Hub 공식 이용정책: https://aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105
""".encode()


def _gpt_review_prompt() -> bytes:
    return """# GPT Pro 검수 지시

첨부된 ZIP은 MIX20K 외부 안전 검수 패키지다. 먼저 `PACKAGE_MANIFEST.json`, `SHA256SUMS.txt`, `MODEL_AND_TRAINING_CONTEXT.md`를 확인하고, Python으로 JSONL을 스트리밍 처리하라. 파일 전체를 대화 컨텍스트에 그대로 출력하지 마라.

## 절대 경계

- `aihub_3k_aggregate.json`의 3,000건에는 본문이 없다. 이를 추정·복원하거나 20K 전체 본문을 검수했다고 주장하지 마라.
- `training_external_17k.jsonl`은 실제 trainer의 `messages` 투영 형식이지만 AI Hub 행이 빠진 외부 검수용 부분집합이다. 실제 MIX20K 학습 데이터 대체물로 사용하지 마라.
- 원본을 수정하거나 재배포하지 말고, 검수 결과만 별도 파일로 생성하라.

## 전수 기계 검사

17,000건 전부에 대해 다음을 검사하라.

1. JSONL 파싱, messages role 순서, 빈 content, 제어문자, 비정상 Unicode
2. exact/near duplicate, 고정 문구 과반복, 응답 길이 이상치, source/task 혼합비
3. 중국어·영어 잔재, 부자연스러운 번역체, 모델 지시문·메타 문구 오염
4. 개인정보처럼 보이는 문자열, 자해·의료·법률·금융 위험 조언, 단정적 운명 표현
5. candidate와 training projection의 messages·행 순서 일치
6. 1.3B 모델의 BF16 Full FT, max length 768, assistant-only loss 계약에 비해 source·token·응답 난이도와 반복성이 적절한지

## 의미 검수 표본

각 외부 source에서 token 길이 decile별로 `sha256("saju-mix20k-gpt-review-v1|" + review_id)`가 가장 작은 10건을 선택해 source당 100건, 총 300건을 직접 읽어라. 전수 검사에서 발견된 이상 후보는 최대 200건을 추가로 검토하라.

- Nemotron: 명식과 해석의 내부 일관성, 근거 없는 단정, 반복 disclaimer
- bazi 파생: 네 기둥·일간·오행·규칙 조건과 답변의 일치
- YEJI 파생: 신살 조건·해당 지지·설명과 판정의 일치
- 공통: 한국어 자연스러움, 공감·안전성, SFT 학습 가치

## 결과 파일

다음 네 파일을 생성하라.

1. `external_review_report.md`: 방법, 실제 검수 범위, source별 결과, 주요 문제, 권고
2. `external_review_summary.json`: `machine_scanned_rows`, `semantic_reviewed_rows`, severity/category 집계, `recommendation` (`proceed`, `fix_then_recheck`, `block` 중 하나)
3. `external_findings.jsonl`: 각 행에 `review_id`, `severity` (`critical|high|medium|low`), `category` (`schema|factual_saju|naturalness|safety|duplication|contamination|training_fit`), `evidence`, `reason`, `recommended_action`
4. `reviewed_ids.jsonl`: 직접 의미 검수한 `review_id`, 선택 사유, source, token decile

근거가 불충분한 명리 판단은 오류로 단정하지 말고 `needs_domain_review`라고 보고서에 분리하라. 외부 AI 검수 결과는 기술 승격이나 품질 인증을 자동 변경하지 않는다.
""".encode()


def _model_training_context(identity: dict[str, Any]) -> bytes:
    model = identity["model_contract"]
    training = identity["training_contract"]
    preflight = identity["technical_preflight"]
    runtime = preflight["runtime"]
    return f"""# 모델·학습 계약 설명

## 대상 모델

- 모델: `{model["repo_id"]}`
- 고정 revision: `{model["revision"]}`
- 전체 parameter: {model["parameter_count"]:,}
- dtype / attention: `{model["dtype"]}` / `{model["attention_backend"]}`
- chat template SHA-256: `{model["chat_template_sha256"]}`
- BOS / EOS / PAD token ID: {model["bos_token_id"]} / {model["eos_token_id"]} / {model["pad_token_id"]}
- 고정 원본: https://huggingface.co/{model["repo_id"]}/tree/{model["revision"]}

모델 파일과 tokenizer 자체는 이 검수 ZIP에 넣지 않았다.

## Phase 5 baseline 계약

- 방식: BF16 전체 parameter SFT (`{training["method"]}`), LoRA/QLoRA 아님
- 정식 max length: {training["formal_max_length"]}; 실제 MIX20K 최대 token 길이: {training["observed_max_tokens"]}
- micro batch / gradient accumulation: {training["per_device_train_batch_size"]} / {training["gradient_accumulation_steps"]}
- gradient checkpointing / `use_cache`: {str(training["gradient_checkpointing"]).lower()} / {str(training["use_cache"]).lower()}
- optimizer: `{training["optimizer"]}`, learning rate {training["learning_rate"]}, cosine schedule, warmup ratio {training["warmup_ratio"]}
- assistant-only loss: {str(training["assistant_only_loss"]).lower()}, packing: {str(training["packing"]).lower()}, loss: `{training["loss_type"]}`
- seed / data seed: {training["seed"]} / {training["data_seed"]}

`training_external_17k.jsonl`은 trainer 직전의 정확한 최상위 `{{"messages": [...]}}` 투영이지만 AI Hub 3K가 빠진 외부 검수용 부분집합이다. 실제 Phase 5는 승인된 내부 MIX20K를 사용해야 한다.

## 완료된 기술 preflight

- Gate D BF16 forward/backward·8-bit optimizer step: `{preflight["gate_d_status"]}`
- 1024 padding-only 진단: `{preflight["diagnostic_1024_status"]}`
- 768에서 100→200 optimizer step resume: `{preflight["resume_200_status"]}`
- 첫 20 / 마지막 20 loss 중앙값: {preflight["first_20_median_loss"]} → {preflight["last_20_median_loss"]}
- 200-step peak VRAM / 종료 여유: {preflight["peak_vram_bytes"]:,} / {preflight["finish_vram_free_bytes"]:,} bytes
- checkpoint 재로드 후 5-task 생성: `{preflight["checkpoint_reload_status"]}`
- 실장비: `{runtime["gpu_name"]}`, PyTorch `{runtime["torch"]}`, CUDA `{runtime["torch_cuda"]}`, Transformers `{runtime["transformers"]}`, TRL `{runtime["trl"]}`, bitsandbytes `{runtime["bitsandbytes"]}`

이는 짧은 기술 smoke 통과 기록이지 최종 품질 인증이 아니다. Phase 5 전체 학습, 사람 명리 검수, 최종 모델 평가는 아직 수행하지 않았다.
""".encode()


def _license_notice() -> bytes:
    return """# 데이터 범위와 라이선스 고지

## 본문을 포함한 외부 검수 범위

- `rayraykim/Nemotron-Personas-Korea-Saju`, revision `ffb934248746a2dea64ef771c0d86e1743d25702`, CC BY 4.0. 안전 필터·선별·고정 disclaimer 추가 사실을 표시한다. https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju
- `AmareshHebbar/bazi-sft`, revision `fad87063b317612e4164dfb0e0e08572c3831df4`, Apache-2.0. 원문 응답은 제외하고 구조 검산과 한국어 재렌더를 거친 파생 행만 포함한다. https://huggingface.co/datasets/AmareshHebbar/bazi-sft
- `tellang/yeji-bazi-rules`, revision `84583ca54e8fce257d3d5efd015bca1263a1cfe9`, MIT AND MIT. 허용 규칙을 교정·재평가해 만든 QA 파생 행만 포함한다. https://huggingface.co/datasets/tellang/yeji-bazi-rules

## 본문을 제외한 범위

- AI Hub 감성대화 말뭉치 #86의 3,000건은 `AIHUB-GENERAL-POLICY` 적용 대상이다.
- 정규화, turn projection, 개인정보·위기 행 제외를 거쳤어도 원천 표현을 유지하므로 본문·원천 ID·개별 record hash를 포함하지 않는다.
- AI Hub 공식 정책은 승인 없는 제3자 열람과 국외 반출을 제한한다: https://aihub.or.kr/intrcn/guid/usagepolicy.do?currMenu=151&topMenu=105

이 문서는 기술적 이용조건 기록이며 법률 자문이 아니다. 패키지의 비제한 본문도 검수 목적에만 사용하고 공개 링크나 저장소에 재배포하지 않는다.
""".encode()


def _build_payloads(
    identity: dict[str, Any],
    projected: dict[str, Any],
    *,
    expected_axis_counts: dict[str, int],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    expected_total = sum(expected_axis_counts.values())
    expected_external = sum(
        expected_axis_counts.get(axis, 0) for axis in EXTERNAL_SAFE_AXES
    )
    expected_restricted = sum(
        expected_axis_counts.get(axis, 0) for axis in RESTRICTED_AXES
    )
    package_id = f"{PACKAGE_PREFIX}-{sha256_json(identity)[:16]}"
    contract = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "candidate_manifest_rows": expected_total,
        "canonical_manifest_rows": expected_total,
        "candidate_manifest_sha256": identity["candidate_manifest_sha256"],
        "canonical_manifest_sha256": identity["canonical_manifest_sha256"],
        "manifests_byte_identical": True,
        "row_membership_identical": True,
        "canonical_name_is_technical_gate_promotion": True,
        "trainer_projection": {"top_level_fields": ["messages"]},
        "model_contract": identity["model_contract"],
        "training_contract": identity["training_contract"],
        "external_content_rows": expected_external,
        "withheld_aihub_rows": expected_restricted,
        "external_package_is_full_training_dataset": False,
        "phase5_training_performed": False,
    }
    payloads = {
        "START_HERE.md": _start_here(identity),
        "GPT_PRO_REVIEW_PROMPT.md": _gpt_review_prompt(),
        "MODEL_AND_TRAINING_CONTEXT.md": _model_training_context(identity),
        "DATA_SCOPE_AND_LICENSE.md": _license_notice(),
        "candidate_20k_index.jsonl": _jsonl_bytes(projected["candidate_index"]),
        "candidate_external_17k.jsonl": _jsonl_bytes(projected["candidate_external"]),
        "training_external_17k.jsonl": _jsonl_bytes(projected["training_external"]),
        "training_line_map.jsonl": _jsonl_bytes(projected["line_map"]),
        "aihub_3k_aggregate.json": _json_bytes(projected["restricted_aggregate"]),
        "candidate_vs_training_contract.json": _json_bytes(contract),
    }
    content_sha256 = {
        name: sha256_bytes(payloads[name]) for name in sorted(CONTENT_FILES)
    }
    content_bytes = {name: len(payloads[name]) for name in sorted(CONTENT_FILES)}
    manifest = {
        **identity,
        "package_id": package_id,
        "full_index_rows": expected_total,
        "external_content_rows": expected_external,
        "withheld_aihub_rows": expected_restricted,
        "axis_counts": dict(sorted(expected_axis_counts.items())),
        "contains_aihub_source_text": False,
        "contains_internal_record_ids": False,
        "contains_external_safe_training_text": True,
        "not_full_mix20k_text_export": True,
        "quality_certification_claimed": False,
        "human_domain_review_performed": False,
        "phase5_training_performed": False,
        "content_sha256": content_sha256,
        "content_bytes": content_bytes,
    }
    payloads["PACKAGE_MANIFEST.json"] = _json_bytes(manifest)
    payloads["SHA256SUMS.txt"] = "".join(
        f"{sha256_bytes(payloads[name])}  {name}\n"
        for name in sorted(set(PACKAGE_FILES) - {"SHA256SUMS.txt"})
    ).encode("utf-8")
    if set(payloads) != set(PACKAGE_FILES):
        raise Phase4Error("외부 검수 ZIP 파일 집합이 고정 계약과 다릅니다.")
    return payloads, manifest


def _load_expected_package(
    config_path: Path,
    repo_root: Path,
    *,
    expected_axis_counts: dict[str, int] = EXPECTED_AXIS_COUNTS,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    from scripts.preflight.phase4_common import prepare_context

    context = prepare_context(repo_root, config_path)
    final = verify_finalized_phase4(context, repo_root)
    if (
        final.get("build_id") != "build-a1a34616dd72"
        or final.get("training_promotion_allowed") is not True
        or final.get("phase5_training_performed") is not False
    ):
        raise Phase4Error("승인된 Phase 4 canonical build 경계가 다릅니다.")
    canonical = canonical_identity(context, repo_root)
    canonical_root = repo_root / final["canonical_root"]
    completion_path = repo_root / final["public_root"] / "phase4_completion_report.json"
    tokenization_path = context["public_root"] / "tokenization_report.json"
    if (
        completion_path.is_symlink()
        or tokenization_path.is_symlink()
        or not completion_path.is_file()
        or not tokenization_path.is_file()
    ):
        raise Phase4Error("Phase 4 설명 입력이 regular file이 아닙니다.")
    completion = load_json(completion_path, "Phase 4 completion report")
    tokenization = load_json(tokenization_path, "Phase 4 tokenization report")
    try:
        observed_max_tokens = max(
            axis["length"]["max"] for axis in tokenization["axes"].values()
        )
        gate_d = completion["gate_d"]
        resume = completion["gate_e"]["resume_768_200"]
        reload_result = completion["gate_e"]["reload_768_generate5"]
        runtime = resume["runtime"]
    except (KeyError, TypeError, ValueError) as exc:
        raise Phase4Error("Phase 4 설명 입력 구조가 올바르지 않습니다.") from exc
    if (
        observed_max_tokens != 716
        or gate_d.get("status") != "passed"
        or completion.get("diagnostic_1024_status") != "passed"
        or resume.get("status") != "passed"
        or reload_result.get("status") != "passed"
        or completion.get("technical_full_ft_preflight_passed") is not True
        or completion.get("phase5_training_performed") is not False
    ):
        raise Phase4Error("Phase 4 기술 설명 Gate가 다릅니다.")
    candidate_path = context["private_root"] / "manifests/mix20k_candidate_v1.jsonl"
    canonical_path = canonical_root / "manifests/mix20k_v1.jsonl"
    if (
        candidate_path.is_symlink()
        or canonical_path.is_symlink()
        or not candidate_path.is_file()
        or not canonical_path.is_file()
    ):
        raise Phase4Error(
            "candidate/canonical MIX20K manifest가 regular file이 아닙니다."
        )
    candidate_bytes = candidate_path.read_bytes()
    canonical_bytes = canonical_path.read_bytes()
    if candidate_bytes != canonical_bytes:
        raise Phase4Error("candidate와 canonical MIX20K manifest byte가 다릅니다.")
    candidate_rows = read_jsonl(candidate_path, "MIX20K candidate")
    canonical_rows = read_jsonl(canonical_path, "MIX20K canonical")
    if candidate_rows != canonical_rows:
        raise Phase4Error("candidate와 canonical MIX20K 행이 다릅니다.")
    records_by_id, _, _, _ = load_staging_records(context, repo_root)
    projected = _project_records(
        canonical_rows,
        records_by_id,
        expected_axis_counts=expected_axis_counts,
    )
    config = context["config"]
    model = config["model"]
    template = config["chat_template"]
    split = config["split"]
    training = config["training_smoke"]
    if (
        model.get("repo_id") != MODEL_REPO_ID
        or model.get("revision") != MODEL_REVISION
        or template.get("sha256") != CHAT_TEMPLATE_SHA256
    ):
        raise Phase4Error("외부 검수 모델·template 계약이 다릅니다.")
    identity = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_type": PACKAGE_TYPE,
        "export_version": EXPORT_VERSION,
        "canonical_build_id": final["build_id"],
        "canonical_build_sha256": final["build_sha256"],
        "parent_preflight_build_id": context["build_id"],
        "parent_preflight_build_sha256": context["build_sha256"],
        "parent_staging_build_id": context["config"]["parent_staging"]["build_id"],
        "parent_staging_build_sha256": context["config"]["parent_staging"][
            "build_sha256"
        ],
        "candidate_manifest_sha256": sha256_bytes(candidate_bytes),
        "canonical_manifest_sha256": sha256_bytes(canonical_bytes),
        "restricted_partition_commitment_sha256": projected["restricted_aggregate"][
            "partition_commitment_sha256"
        ],
        "exporter_source_sha256": sha256_file(Path(__file__)),
        "external_scope": "non_aihub_text_plus_aihub_aggregate_only",
        "official_sources_checked_at": "2026-08-28",
        "selected_max_length": final["selected_max_length"],
        "model_contract": {
            "repo_id": model["repo_id"],
            "revision": model["revision"],
            "parameter_count": model["expected_parameter_count"],
            "dtype": model["dtype"],
            "attention_backend": model["attention_backend"],
            "chat_template_sha256": template["sha256"],
            "bos_token_id": template["bos_token_id"],
            "eos_token_id": template["eos_token_id"],
            "pad_token_id": template["pad_token_id"],
        },
        "training_contract": {
            "method": "full_parameter_sft",
            "formal_max_length": split["formal_max_length"],
            "observed_max_tokens": observed_max_tokens,
            "per_device_train_batch_size": training["per_device_train_batch_size"],
            "gradient_accumulation_steps": training["gradient_accumulation_steps"],
            "gradient_checkpointing": training["gradient_checkpointing"],
            "use_cache": training["use_cache"],
            "optimizer": training["optimizer"],
            "assistant_only_loss": training["assistant_only_loss"],
            "packing": training["packing"],
            "loss_type": training["loss_type"],
            "learning_rate": training["learning_rate"],
            "warmup_ratio": training["warmup_ratio"],
            "lr_scheduler_type": training["lr_scheduler_type"],
            "weight_decay": training["weight_decay"],
            "max_grad_norm": training["max_grad_norm"],
            "seed": training["seed"],
            "data_seed": training["data_seed"],
        },
        "technical_preflight": {
            "gate_d_status": gate_d["status"],
            "diagnostic_1024_status": completion["diagnostic_1024_status"],
            "resume_200_status": resume["status"],
            "checkpoint_reload_status": reload_result["status"],
            "first_20_median_loss": resume["loss_report"]["first_window_median_loss"],
            "last_20_median_loss": resume["loss_report"]["last_window_median_loss"],
            "peak_vram_bytes": resume["peak_vram_bytes"],
            "finish_vram_free_bytes": resume["vram_free_bytes_at_finish"],
            "runtime": {
                key: runtime[key]
                for key in (
                    "gpu_name",
                    "torch",
                    "torch_cuda",
                    "transformers",
                    "trl",
                    "bitsandbytes",
                )
            },
        },
        "training_promotion_allowed": True,
        "phase5_training_performed": False,
    }
    if canonical["build_id"] != identity["canonical_build_id"]:
        raise Phase4Error("계산된 canonical identity가 승인 build와 다릅니다.")
    return _build_payloads(
        identity,
        projected,
        expected_axis_counts=expected_axis_counts,
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | PRIVATE_FILE_MODE) << 16
    return info


def _write_zip(output: Path, payloads: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{output.name}.", dir=output.parent
        )
        os.close(descriptor)
        temporary = Path(name)
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for member in sorted(payloads):
                archive.writestr(_zip_info(member), payloads[member])
        temporary.chmod(PRIVATE_FILE_MODE)
        if output.exists():
            raise Phase4Error(f"외부 검수 ZIP을 덮어쓸 수 없습니다: {output}")
        os.replace(temporary, output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _safe_archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = archive.infolist()
    names: list[str] = []
    total = 0
    for info in infos:
        path = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (
            info.filename != path.as_posix()
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or info.is_dir()
            or info.flag_bits & 0x1
            or not stat.S_ISREG(mode)
            or stat.S_IMODE(mode) != PRIVATE_FILE_MODE
            or info.date_time != ZIP_TIMESTAMP
            or info.file_size > MAX_MEMBER_BYTES
            or info.compress_size > MAX_MEMBER_BYTES
        ):
            raise Phase4Error(
                f"외부 검수 ZIP member가 안전하지 않습니다: {info.filename}"
            )
        names.append(info.filename)
        total += info.file_size
    if (
        len(names) != len(set(names))
        or set(names) != set(PACKAGE_FILES)
        or total > MAX_ARCHIVE_UNCOMPRESSED_BYTES
    ):
        raise Phase4Error("외부 검수 ZIP 파일 집합·크기·중복 계약이 다릅니다.")
    return infos


def _verify_checksum_payloads(payloads: dict[str, bytes]) -> None:
    try:
        lines = payloads["SHA256SUMS.txt"].decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise Phase4Error("SHA256SUMS.txt가 UTF-8이 아닙니다.") from exc
    expected: dict[str, str] = {}
    for line in lines:
        match = SHA_LINE_PATTERN.fullmatch(line)
        if match is None or match.group(2) in expected:
            raise Phase4Error("SHA256SUMS 형식 또는 중복 entry가 올바르지 않습니다.")
        expected[match.group(2)] = match.group(1)
    targets = set(PACKAGE_FILES) - {"SHA256SUMS.txt"}
    if set(expected) != targets:
        raise Phase4Error("SHA256SUMS 대상 파일 집합이 다릅니다.")
    for name, digest in expected.items():
        if sha256_bytes(payloads[name]) != digest:
            raise Phase4Error(f"외부 검수 ZIP 내부 SHA-256이 다릅니다: {name}")


def _verify_payload_contract(
    payloads: dict[str, bytes],
    *,
    expected_axis_counts: dict[str, int] = EXPECTED_AXIS_COUNTS,
) -> dict[str, Any]:
    manifest = _read_json_object(payloads["PACKAGE_MANIFEST.json"], "package manifest")
    index_rows = _read_jsonl_payload(
        payloads["candidate_20k_index.jsonl"], "candidate index"
    )
    candidate_rows = _read_jsonl_payload(
        payloads["candidate_external_17k.jsonl"], "external candidate"
    )
    training_rows = _read_jsonl_payload(
        payloads["training_external_17k.jsonl"], "external training"
    )
    line_map = _read_jsonl_payload(payloads["training_line_map.jsonl"], "line map")
    restricted = _read_json_object(
        payloads["aihub_3k_aggregate.json"], "AI Hub aggregate"
    )
    contract = _read_json_object(
        payloads["candidate_vs_training_contract.json"], "candidate/training contract"
    )
    expected_total = sum(expected_axis_counts.values())
    expected_external = sum(
        expected_axis_counts.get(axis, 0) for axis in EXTERNAL_SAFE_AXES
    )
    expected_restricted = sum(
        expected_axis_counts.get(axis, 0) for axis in RESTRICTED_AXES
    )
    model_contract = manifest.get("model_contract")
    training_contract = manifest.get("training_contract")
    technical_preflight = manifest.get("technical_preflight")
    if (
        manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION
        or manifest.get("package_type") != PACKAGE_TYPE
        or manifest.get("export_version") != EXPORT_VERSION
        or manifest.get("package_id")
        != f"{PACKAGE_PREFIX}-{sha256_json({key: value for key, value in manifest.items() if key not in {'package_id', 'full_index_rows', 'external_content_rows', 'withheld_aihub_rows', 'axis_counts', 'contains_aihub_source_text', 'contains_internal_record_ids', 'contains_external_safe_training_text', 'not_full_mix20k_text_export', 'quality_certification_claimed', 'human_domain_review_performed', 'content_sha256', 'content_bytes'}})[:16]}"
        or manifest.get("axis_counts") != dict(sorted(expected_axis_counts.items()))
        or manifest.get("full_index_rows") != expected_total
        or manifest.get("external_content_rows") != expected_external
        or manifest.get("withheld_aihub_rows") != expected_restricted
        or manifest.get("contains_aihub_source_text") is not False
        or manifest.get("contains_internal_record_ids") is not False
        or manifest.get("contains_external_safe_training_text") is not True
        or manifest.get("not_full_mix20k_text_export") is not True
        or manifest.get("training_promotion_allowed") is not True
        or manifest.get("human_domain_review_performed") is not False
        or manifest.get("quality_certification_claimed") is not False
        or manifest.get("phase5_training_performed") is not False
        or not isinstance(model_contract, dict)
        or model_contract.get("repo_id") != MODEL_REPO_ID
        or model_contract.get("revision") != MODEL_REVISION
        or model_contract.get("chat_template_sha256") != CHAT_TEMPLATE_SHA256
        or model_contract.get("parameter_count") != 1_291_478_272
        or model_contract.get("dtype") != "bfloat16"
        or model_contract.get("attention_backend") != "sdpa"
        or not isinstance(training_contract, dict)
        or training_contract.get("method") != "full_parameter_sft"
        or training_contract.get("formal_max_length") != 768
        or training_contract.get("observed_max_tokens") != 716
        or training_contract.get("per_device_train_batch_size") != 1
        or training_contract.get("gradient_accumulation_steps") != 8
        or training_contract.get("gradient_checkpointing") is not True
        or training_contract.get("use_cache") is not False
        or training_contract.get("optimizer") != "paged_adamw_8bit"
        or training_contract.get("assistant_only_loss") is not True
        or training_contract.get("packing") is not False
        or training_contract.get("loss_type") != "chunked_nll"
        or not isinstance(technical_preflight, dict)
        or technical_preflight.get("gate_d_status") != "passed"
        or technical_preflight.get("diagnostic_1024_status") != "passed"
        or technical_preflight.get("resume_200_status") != "passed"
        or technical_preflight.get("checkpoint_reload_status") != "passed"
    ):
        raise Phase4Error("외부 검수 package identity/경계가 다릅니다.")
    if (
        len(index_rows) != expected_total
        or len(line_map) != expected_total
        or len(candidate_rows) != expected_external
        or len(training_rows) != expected_external
        or restricted.get("row_count") != expected_restricted
        or restricted.get("source_text_included") is not False
        or restricted.get("row_ids_included") is not False
        or restricted.get("record_hashes_included") is not False
        or restricted.get("axis_counts")
        != {axis: expected_axis_counts[axis] for axis in sorted(RESTRICTED_AXES)}
        or restricted.get("partition_commitment_sha256")
        != manifest.get("restricted_partition_commitment_sha256")
        or set(restricted) != RESTRICTED_AGGREGATE_FIELDS
        or _walk_keys(restricted) & FORBIDDEN_PROJECTED_KEYS
        or "messages" in _walk_keys(restricted)
    ):
        raise Phase4Error("외부 검수 20K/17K/3K 수량 계약이 다릅니다.")
    if (
        contract.get("manifests_byte_identical") is not True
        or contract.get("row_membership_identical") is not True
        or contract.get("external_package_is_full_training_dataset") is not False
        or contract.get("phase5_training_performed") is not False
        or contract.get("package_id") != manifest.get("package_id")
        or contract.get("candidate_manifest_sha256")
        != manifest.get("candidate_manifest_sha256")
        or contract.get("canonical_manifest_sha256")
        != manifest.get("canonical_manifest_sha256")
        or contract.get("model_contract") != model_contract
        or contract.get("training_contract") != training_contract
    ):
        raise Phase4Error("candidate/training 설명 계약이 다릅니다.")

    axis_counts: Counter[str] = Counter()
    expected_external_ids: list[str] = []
    previous_external_line = 0
    for position, (index, mapping) in enumerate(
        zip(index_rows, line_map, strict=True), 1
    ):
        review_id = _review_id(position)
        if (
            index.get("review_id") != review_id
            or index.get("canonical_position") != position
            or mapping.get("review_id") != review_id
            or mapping.get("canonical_position") != position
            or mapping.get("content_status") != index.get("content_status")
            or mapping.get("external_training_line")
            != index.get("external_training_line")
            or set(index) != INDEX_FIELDS
            or set(mapping) != LINE_MAP_FIELDS
            or _walk_keys(index) & FORBIDDEN_PROJECTED_KEYS
            or _walk_keys(mapping) & FORBIDDEN_PROJECTED_KEYS
        ):
            raise Phase4Error(f"외부 검수 index/line map이 다릅니다: {position}")
        axis = str(index.get("mix_axis"))
        axis_counts[axis] += 1
        if axis in RESTRICTED_AXES:
            if (
                index.get("source") != "aihub_empathy"
                or index.get("content_status") != "withheld_aihub_policy"
                or index.get("external_training_line") is not None
            ):
                raise Phase4Error(f"AI Hub index 경계가 다릅니다: {position}")
        else:
            previous_external_line += 1
            if (
                axis not in EXTERNAL_SAFE_AXES
                or index.get("content_status") != "included_external_safe"
                or index.get("external_training_line") != previous_external_line
            ):
                raise Phase4Error(f"외부 허용 index 경계가 다릅니다: {position}")
            expected_external_ids.append(review_id)
    if axis_counts != Counter(expected_axis_counts):
        raise Phase4Error("외부 검수 index axis 수량이 다릅니다.")

    external_index = [
        row for row in index_rows if row["mix_axis"] in EXTERNAL_SAFE_AXES
    ]
    external_axis_counts: Counter[str] = Counter()
    for external_line, (candidate, training, review_id, index) in enumerate(
        zip(
            candidate_rows,
            training_rows,
            expected_external_ids,
            external_index,
            strict=True,
        ),
        1,
    ):
        if (
            candidate.get("review_id") != review_id
            or candidate.get("canonical_position") != index.get("canonical_position")
            or candidate.get("external_training_line") != external_line
            or candidate.get("source") != index.get("source")
            or candidate.get("mix_axis") != index.get("mix_axis")
            or candidate.get("task") != index.get("task")
            or candidate.get("license_expression") != index.get("license_expression")
            or candidate.get("usage_class") != index.get("usage_class")
            or candidate.get("provenance_status") != index.get("provenance_status")
            or candidate.get("total_tokens") != index.get("total_tokens")
            or candidate.get("assistant_tokens") != index.get("assistant_tokens")
            or candidate.get("mix_axis") not in EXTERNAL_SAFE_AXES
            or candidate.get("source") == "aihub_empathy"
            or set(candidate) != CANDIDATE_FIELDS
            or set(training) != {"messages"}
            or _walk_keys(candidate) & FORBIDDEN_PROJECTED_KEYS
            or _walk_keys(training) & FORBIDDEN_PROJECTED_KEYS
        ):
            raise Phase4Error(f"외부 17K 투영 경계가 다릅니다: {external_line}")
        messages = _validated_messages(
            candidate.get("messages"), review_id, scan_pii=True
        )
        _validated_external_metadata(candidate, review_id)
        if training.get("messages") != messages:
            raise Phase4Error(f"candidate/training messages가 다릅니다: {review_id}")
        external_axis_counts[str(candidate["mix_axis"])] += 1
    if external_axis_counts != Counter(
        {axis: expected_axis_counts[axis] for axis in EXTERNAL_SAFE_AXES}
    ):
        raise Phase4Error("외부 17K source/axis 수량이 다릅니다.")

    content_hashes = manifest.get("content_sha256")
    content_bytes = manifest.get("content_bytes")
    if not isinstance(content_hashes, dict) or not isinstance(content_bytes, dict):
        raise Phase4Error("package content hash/size manifest가 없습니다.")
    if set(content_hashes) != set(CONTENT_FILES) or set(content_bytes) != set(
        CONTENT_FILES
    ):
        raise Phase4Error("package content hash/size 대상이 다릅니다.")
    for name in CONTENT_FILES:
        if content_hashes.get(name) != sha256_bytes(
            payloads[name]
        ) or content_bytes.get(name) != len(payloads[name]):
            raise Phase4Error(f"package content hash/size가 다릅니다: {name}")
    return manifest


def verify_archive(
    archive_path: Path,
    *,
    expected_axis_counts: dict[str, int] = EXPECTED_AXIS_COUNTS,
) -> dict[str, Any]:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise Phase4Error("외부 검수 ZIP이 regular file이 아닙니다.")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            _safe_archive_members(archive)
            payloads = {name: archive.read(name) for name in PACKAGE_FILES}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise Phase4Error("외부 검수 ZIP을 안전하게 읽지 못했습니다.") from exc
    _verify_checksum_payloads(payloads)
    manifest = _verify_payload_contract(
        payloads, expected_axis_counts=expected_axis_counts
    )
    return {
        "status": "verified_external_safe_review_package",
        "archive": archive_path.name,
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": sha256_file(archive_path),
        "package_id": manifest["package_id"],
        "canonical_build_id": manifest["canonical_build_id"],
        "full_index_rows": manifest["full_index_rows"],
        "external_content_rows": manifest["external_content_rows"],
        "withheld_aihub_rows": manifest["withheld_aihub_rows"],
        "contains_aihub_source_text": False,
        "phase5_training_performed": False,
        "content_sha256": manifest["content_sha256"],
    }


def _normalized_output(output: Path, repo_root: Path) -> Path:
    candidate = output.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if candidate.suffix.lower() != ".zip" or CONTROL_PATTERN.search(candidate.name):
        raise Phase4Error("외부 검수 출력은 명시적 .zip 경로여야 합니다.")
    if candidate.is_symlink():
        raise Phase4Error("외부 검수 ZIP 출력은 symlink일 수 없습니다.")
    resolved = candidate.parent.resolve(strict=False) / candidate.name
    if resolved.is_relative_to(repo_root.resolve()):
        raise Phase4Error("외부 검수 ZIP은 저장소 밖에만 만들 수 있습니다.")
    return resolved


def _verify_sidecar(archive: Path, digest: str) -> Path:
    sidecar = archive.with_name(f"{archive.name}.sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise Phase4Error("외부 검수 ZIP SHA-256 sidecar가 없습니다.")
    expected = f"{digest}  {archive.name}\n".encode()
    if sidecar.read_bytes() != expected:
        raise Phase4Error("외부 검수 ZIP SHA-256 sidecar가 다릅니다.")
    return sidecar


def _compare_expected(
    result: dict[str, Any], expected_manifest: dict[str, Any]
) -> None:
    if (
        result.get("package_id") != expected_manifest.get("package_id")
        or result.get("canonical_build_id")
        != expected_manifest.get("canonical_build_id")
        or result.get("content_sha256") != expected_manifest.get("content_sha256")
    ):
        raise Phase4Error("외부 검수 ZIP이 현재 승인 canonical 입력과 다릅니다.")


def export_package(
    config_path: Path,
    repo_root: Path,
    output: Path,
    *,
    confirm_external_safe_scope: bool,
) -> dict[str, Any]:
    if not confirm_external_safe_scope:
        raise Phase4Error("외부 안전 반출 범위 확인 옵션이 필요합니다.")
    output = _normalized_output(output, repo_root)
    sidecar = output.with_name(f"{output.name}.sha256")
    if not output.exists() and (sidecar.exists() or sidecar.is_symlink()):
        raise Phase4Error("ZIP 없이 기존 SHA-256 sidecar만 남아 있습니다.")
    payloads, expected_manifest = _load_expected_package(config_path, repo_root)
    if output.exists():
        result = verify_archive(output)
        _compare_expected(result, expected_manifest)
        sidecar = _verify_sidecar(output, result["archive_sha256"])
        return {
            **result,
            "mode": "reused",
            "sidecar": sidecar.name,
            "writes_performed": False,
        }
    _write_zip(output, payloads)
    digest = sha256_file(output)
    write_bytes_once(
        sidecar,
        f"{digest}  {output.name}\n".encode(),
        mode=PRIVATE_FILE_MODE,
    )
    result = verify_archive(output)
    _compare_expected(result, expected_manifest)
    _verify_sidecar(output, result["archive_sha256"])
    return {
        **result,
        "mode": "built",
        "output": str(output),
        "sidecar": str(sidecar),
        "writes_performed": True,
    }


def verify_package(
    config_path: Path,
    repo_root: Path,
    archive: Path,
) -> dict[str, Any]:
    archive = _normalized_output(archive, repo_root)
    _, expected_manifest = _load_expected_package(config_path, repo_root)
    result = verify_archive(archive)
    _compare_expected(result, expected_manifest)
    sidecar = _verify_sidecar(archive, result["archive_sha256"])
    return {**result, "sidecar": str(sidecar), "matches_current_canonical": True}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="승인 MIX20K의 외부 GPT Pro 안전 검수 패키지를 관리한다."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--confirm-external-safe-scope", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--archive", type=Path, required=True)
    return parser


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    config_path = arguments.config.expanduser().resolve()
    load_json(config_path, "Phase 4 설정")
    if arguments.command == "export":
        return export_package(
            config_path,
            REPO_ROOT,
            arguments.output,
            confirm_external_safe_scope=arguments.confirm_external_safe_scope,
        )
    if arguments.command == "verify":
        return verify_package(config_path, REPO_ROOT, arguments.archive)
    raise Phase4Error(f"지원하지 않는 명령입니다: {arguments.command}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        print(json.dumps(run(arguments), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Phase4Error as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: 사용자 요청으로 중단했습니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
