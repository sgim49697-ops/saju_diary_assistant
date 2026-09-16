# system_context_s4_models.py - S4의 고정 공식 3B snapshot 수집·검증과 독립 tokenizer 등록을 담당한다.

from __future__ import annotations

import fcntl
import os
import shutil
import stat
from pathlib import Path

from scripts.evaluation.system_context_contracts import (
    REPO_ROOT,
    file_sha,
    private_directory,
    read_json,
    safe_path,
)
from scripts.training.dashboard_tokenizer_v1 import (
    BACKEND_SHA256,
    K0_RELATIVE,
    TOKENIZER_FILES,
    backend_sha256,
    load_canonical_tokenizer,
)

REGISTRY = REPO_ROOT / "configs/model_versions/saju_1b_baseline/kanana-2-3b-s4-v1.0.0.json"
REGISTRY_SHA256 = "f20ef62aa8f83b288f669d12d20622bcb6a6eed11db356960db778c81c73a9c9"
ENGINES = ("k0_instruct", "kanana3b_instruct")


def registration():
    if file_sha(REGISTRY) != REGISTRY_SHA256:
        raise ValueError("S4 공식 모델 등록 pin 변경")
    value = read_json(REGISTRY)
    for name in value["files"]:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("등록 파일 경로 이탈")
    if value["trust_remote_code"] or value["adapter"] or value["quantization"] or value["cpu_offload"]:
        raise ValueError("3B loader 권한 위반")
    return value


def model_root():
    return safe_path(REPO_ROOT / registration()["local_path"])


def verify_snapshot_layout(root, files, *, auxiliary_dirs=()):
    """등록 shard보다 우선 로딩될 가중치·adapter·tokenizer 덮어씌우기를 거부한다."""
    root = safe_path(root)
    if not root.is_dir():
        raise ValueError("S4 등록 snapshot 디렉터리가 없습니다.")
    for path in root.iterdir():
        safe_path(path)
        if path.name in {*files, ".download.lock"}:
            if not path.is_file():
                raise ValueError("등록 snapshot 파일이 일반 파일이 아닙니다.")
        elif path.name in {".cache", *auxiliary_dirs} and path.is_dir():
            # Hub cache와 지정한 보조 자료는 loader 입력이 아니다. 링크·특수 파일은 금지한다.
            for cached in path.rglob("*"):
                safe_path(cached)
                mode = cached.stat().st_mode
                if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                    raise ValueError("모델 보조 디렉터리의 특수 파일은 허용하지 않습니다.")
        else:
            raise ValueError(f"S4 모델 snapshot 미등록 파일/폴더: {path.name}")


def verify_3b(*, metadata_only=False):
    value, root = registration(), model_root()
    verify_snapshot_layout(root, value["files"])
    pins = {}
    for name, pin in value["files"].items():
        if metadata_only and name.endswith(".safetensors"):
            continue
        path = safe_path(root / name)
        if not path.is_file() or path.stat().st_size != pin["bytes"] or file_sha(path) != pin["sha256"]:
            raise ValueError(f"S4 3B 등록 파일 누락/크기/hash 불일치: {name}")
        pins[name] = pin["sha256"]
    config = read_json(root / "config.json")
    if (
        config.get("architectures") != [value["architecture"]]
        or config.get("model_type") != value["model_type"]
        or config.get("auto_map")
        or config.get("quantization_config")
        or config.get("max_position_embeddings") != value["native_context_tokens"]
        or config.get("layer_types") != ["full_attention"] * 32
        or config.get("use_sliding_window") is not False
    ):
        raise ValueError("S4 3B 실제 구조·native context·remote code 불일치")
    index = read_json(root / "model.safetensors.index.json")
    shards = {name for name in value["files"] if name.endswith(".safetensors")}
    if set(index["weight_map"].values()) != shards or index["metadata"]["total_size"] != value["parameter_count"] * 2:
        raise ValueError("3B safetensors index/분할/정밀도 불일치")
    return pins


def download(*, execute=False):
    """명시 호출만 외부 수집한다. 고정 revision/파일 외 실행 코드는 받지 않는다."""
    value = registration()
    total = sum(p["bytes"] for p in value["files"].values())
    if not execute:
        return {"status": "download_plan", "model_id": value["model_id"], "revision": value["revision"], "bytes": total, "gpu_used": False, "artifact_writes": False}
    if os.environ.get("SYSTEM_CONTEXT_S4_DOWNLOAD") != "KANANA3B_PINNED_V1":
        raise ValueError("SYSTEM_CONTEXT_S4_DOWNLOAD=KANANA3B_PINNED_V1 확인이 필요합니다.")
    root = private_directory(model_root())
    descriptor = os.open(root / ".download.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    previous_umask = os.umask(0o077)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_snapshot_layout(root, value["files"])
        missing = []
        for name, pin in value["files"].items():
            path = safe_path(root / name)
            if path.exists():
                if path.stat().st_size != pin["bytes"] or file_sha(path) != pin["sha256"]:
                    raise ValueError("기존 모델 파일이 변조됐습니다. 자동 덮어쓰기 금지")
            else:
                missing.append(name)
        if shutil.disk_usage(root).free < sum(value["files"][n]["bytes"] for n in missing) + 1024**3:
            raise ValueError("고정 모델 수집 여유 공간 부족")
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import disable_progress_bars
        from requests.exceptions import RequestException

        disable_progress_bars()
        for name in missing:
            try:
                downloaded = Path(hf_hub_download(
                    repo_id=value["model_id"], revision=value["revision"], filename=name,
                    local_dir=root, token=False, force_download=False,
                ))
            except (OSError, ValueError, RuntimeError, RequestException) as exc:
                # 네트워크 예외의 signed URL/자격 증명을 공개 진단에 복사하지 않는다.
                raise ValueError(f"고정 모델 수집 실패: {name}, {type(exc).__name__}") from None
            if safe_path(downloaded).resolve() != root / name:
                raise ValueError("수집 도구의 최종 경로 불일치")
            pin = value["files"][name]
            if downloaded.stat().st_size != pin["bytes"] or file_sha(downloaded) != pin["sha256"]:
                raise ValueError("수집 파일 공식 hash/크기 불일치")
        pins = verify_3b()
        return {"status": "download_verified", "model_id": value["model_id"], "revision": value["revision"], "files": pins, "gpu_used": False, "service_changed": False}
    finally:
        os.umask(previous_umask)
        os.close(descriptor)


def verify_k0(context):
    base = context["inference_engines"]["engines"]["k0_instruct"]
    root = safe_path(base["resolved_path"])
    if root != REPO_ROOT / K0_RELATIVE:
        raise ValueError("S4 K0 고정 snapshot 경로 불일치")
    pins = dict(base["required_file_sha256"])
    pins["model.safetensors"] = base["model_sha256"]
    # generation_config도 실행 신원에 포함한다. 실제 생성 인자는 별도로 고정한다.
    generation = root / "generation_config.json"
    if generation.is_file():
        pins["generation_config.json"] = file_sha(generation)
    verify_snapshot_layout(
        root, {*pins, ".gitattributes", "LICENSE", "README.md"},
        auxiliary_dirs=("assets", "sglang"),
    )
    actual = {name: file_sha(root / name) for name in pins}
    if actual != pins:
        raise ValueError("S4 K0 코드/가중치/hash 불일치")
    return actual


def verify_models(context):
    return {"k0_instruct": verify_k0(context), "kanana3b_instruct": verify_3b()}


def load_tokenizer(engine):
    if engine == "k0_instruct":
        return load_canonical_tokenizer(REPO_ROOT / K0_RELATIVE)
    if engine != "kanana3b_instruct":
        raise ValueError("미등록 tokenizer")
    verify_3b(metadata_only=True)
    from transformers import AutoTokenizer

    root = model_root()
    tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False, fix_mistral_regex=False)
    if (
        backend_sha256(tokenizer) != registration()["tokenizer_backend_sha256"]
        or tokenizer.chat_template != (root / "chat_template.jinja").read_text(encoding="utf-8")
    ):
        raise ValueError("3B 자체 tokenizer/backend/template 불일치")
    return tokenizer


def registry_summary(context, verified):
    base = context["inference_engines"]["engines"]["k0_instruct"]
    large = registration()
    return {
        "k0_instruct": {
            "model_id": "kakaocorp/kanana-2-1.3b-instruct", "revision": base["revision"],
            "architecture": "Kanana2TinyForCausalLM", "trust_remote_code": True,
            "custom_code_sha256": {n: p for n, p in verified["k0_instruct"].items() if n.endswith(".py")},
            "tokenizer_backend_sha256": BACKEND_SHA256, "tokenizer_files": TOKENIZER_FILES,
            "verified_files": verified["k0_instruct"], "dtype": "bfloat16",
            "attention_layout": "24_sliding_attention_and_8_full_attention_layers",
        },
        "kanana3b_instruct": {**large, "verified_files": verified["kanana3b_instruct"]},
    }
