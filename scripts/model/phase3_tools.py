# phase3_tools.py - Phase 3 환경 계약, 모델 snapshot, GPU smoke와 보고서를 검증한다.

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.model.errors import Phase3Error

HASH_CHUNK_BYTES = 4 * 1024 * 1024
FULL_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PACKAGE_LINE_PATTERN = re.compile(r"([A-Za-z0-9_.-]+)==([^\s]+)")
REMOTE_CODE_FORBIDDEN_ATTRIBUTES = {
    "popen",
    "remove",
    "removedirs",
    "rmtree",
    "system",
    "unlink",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase3Error(f"Phase 3 설정을 읽을 수 없습니다: {path}") from exc
    if not isinstance(payload, dict):
        raise Phase3Error("Phase 3 설정 최상위 값은 JSON object여야 합니다.")
    return payload


def validate_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise Phase3Error("빈 상대경로는 허용하지 않습니다.")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise Phase3Error(f"안전하지 않은 상대경로입니다: {value}")
    if any(not part or any(ord(char) < 32 for char in part) for part in path.parts):
        raise Phase3Error(f"제어문자 또는 빈 component가 있는 경로입니다: {value}")
    if path.as_posix() != value:
        raise Phase3Error(f"정규화되지 않은 상대경로입니다: {value}")
    return path


def resolve_repo_path(repo_root: Path, value: str) -> Path:
    relative = validate_relative_path(value)
    root = repo_root.resolve()
    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise Phase3Error(f"저장소 밖 경로는 허용하지 않습니다: {value}")
    return resolved


def _version_base(version: str) -> str:
    return version.split("+", 1)[0]


def _parse_requirements(path: Path) -> tuple[list[str], dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise Phase3Error(f"requirements를 읽을 수 없습니다: {path}") from exc
    options = [line.strip() for line in lines if line.strip().startswith("--")]
    packages: dict[str, str] = {}
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("--"):
            continue
        match = PACKAGE_LINE_PATTERN.fullmatch(line)
        if match is None:
            raise Phase3Error(f"정확히 고정되지 않은 requirement입니다: {line}")
        name = match.group(1).lower().replace("_", "-")
        if name in packages:
            raise Phase3Error(f"중복 requirement입니다: {name}")
        packages[name] = match.group(2)
    return options, packages


def validate_contract(
    config: dict[str, Any], repo_root: Path, *, require_lock: bool = False
) -> dict[str, Any]:
    if config.get("schema_version") != "1.0.0":
        raise Phase3Error("Phase 3 schema_version은 1.0.0이어야 합니다.")
    if config.get("canonical_plan_version") != "2.4.0":
        raise Phase3Error("Phase 3 정본 버전은 2.4.0이어야 합니다.")

    environment = config.get("environment")
    expected_packages = {
        "torch": "2.13.0",
        "torchvision": "0.28.0",
        "torchaudio": "2.11.0",
        "transformers": "4.57.6",
        "trl": "1.12.0",
        "datasets": "4.7.0",
        "accelerate": "1.14.0",
        "bitsandbytes": "0.50.2",
    }
    if not isinstance(environment, dict):
        raise Phase3Error("환경 계약이 없습니다.")
    if environment.get("packages") != expected_packages:
        raise Phase3Error("Phase 3 직접 패키지 버전 계약이 다릅니다.")
    if (
        environment.get("pytorch_index_url")
        != "https://download.pytorch.org/whl/cu130"
        or environment.get("extra_index_url") != "https://pypi.org/simple"
        or environment.get("torch_cuda_version") != "13.0"
        or environment.get("python_version") != "3.10.12"
        or environment.get("uv_version") != "0.9.26"
        or environment.get("only_binary") is not True
    ):
        raise Phase3Error("Python·uv·CUDA wheel 계약이 다릅니다.")

    hardware = config.get("hardware")
    if not isinstance(hardware, dict) or hardware != {
        "compute_capability": [12, 0],
        "gpu_name": "NVIDIA GeForce RTX 5070 Ti",
        "minimum_driver_version": "580.88",
        "minimum_vram_mib": 16000,
        "required_arch": "sm_120",
    }:
        raise Phase3Error("RTX 5070 Ti 하드웨어 계약이 다릅니다.")

    model = config.get("model")
    if not isinstance(model, dict):
        raise Phase3Error("모델 계약이 없습니다.")
    if model.get("repo_id") != "kakaocorp/kanana-2-1.3b-instruct":
        raise Phase3Error("허용되지 않은 모델 저장소입니다.")
    revision = str(model.get("revision", ""))
    if FULL_REVISION_PATTERN.fullmatch(revision) is None:
        raise Phase3Error("모델 revision은 40자 commit SHA여야 합니다.")
    if (
        model.get("expected_class") != "Kanana2TinyForCausalLM"
        or model.get("expected_dtype") != "bfloat16"
        or model.get("expected_max_position_embeddings") != 32768
        or model.get("expected_parameter_count") != 1_291_478_272
    ):
        raise Phase3Error("Kanana 모델 구조 계약이 다릅니다.")
    if model.get("expected_rope_parameters") != {
        "full_attention": {
            "factor": 40.0,
            "original_max_position_embeddings": 4096,
            "rope_theta": 10000,
            "rope_type": "yarn",
        },
        "sliding_attention": {
            "rope_theta": 10000.0,
            "rope_type": "default",
        },
    }:
        raise Phase3Error("Kanana RoPE 구조 계약이 다릅니다.")
    local_subdir = str(model.get("local_subdir", ""))
    if not local_subdir.startswith("models/"):
        raise Phase3Error("모델 snapshot은 models/ 아래에 있어야 합니다.")
    resolve_repo_path(repo_root, local_subdir)

    files = model.get("files")
    if not isinstance(files, list) or len(files) != 14:
        raise Phase3Error("모델 payload는 정확히 14개 파일이어야 합니다.")
    file_paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise Phase3Error("모델 file manifest 항목이 올바르지 않습니다.")
        path = str(item.get("path", ""))
        validate_relative_path(path)
        if path in file_paths:
            raise Phase3Error(f"모델 file manifest 중복 경로입니다: {path}")
        file_paths.add(path)
        if (
            not isinstance(item.get("bytes"), int)
            or item["bytes"] <= 0
            or SHA256_PATTERN.fullmatch(str(item.get("sha256", ""))) is None
        ):
            raise Phase3Error(f"모델 file metadata가 올바르지 않습니다: {path}")
    required_model_files = {
        "LICENSE",
        "README.md",
        "chat_template.jinja",
        "config.json",
        "configuration_kanana2_tiny.py",
        "model.safetensors",
        "modeling_kanana2_tiny.py",
        "tokenizer.json",
        "tokenizer_config.json",
    }
    if not required_model_files.issubset(file_paths):
        raise Phase3Error("필수 모델·tokenizer·license 파일이 누락됐습니다.")

    remote_code = model.get("remote_code")
    if not isinstance(remote_code, dict) or remote_code.get("paths") != [
        "configuration_kanana2_tiny.py",
        "modeling_kanana2_tiny.py",
    ]:
        raise Phase3Error("remote code allowlist가 다릅니다.")
    if not isinstance(remote_code.get("banned_import_roots"), list) or not isinstance(
        remote_code.get("banned_calls"), list
    ):
        raise Phase3Error("remote code 금지 계약이 없습니다.")

    template = config.get("chat_template")
    if not isinstance(template, dict):
        raise Phase3Error("chat template 계약이 없습니다.")
    tracked_template = resolve_repo_path(repo_root, str(template.get("tracked_path", "")))
    if not tracked_template.is_file():
        raise Phase3Error("고정 chat template 파일이 없습니다.")
    if tracked_template.stat().st_size != template.get("bytes") or sha256_file(
        tracked_template
    ) != template.get("sha256"):
        raise Phase3Error("고정 chat template의 bytes 또는 SHA-256이 다릅니다.")
    template_text = tracked_template.read_text(encoding="utf-8")
    for fragment in template.get("required_fragments", []):
        if fragment not in template_text:
            raise Phase3Error(f"chat template 필수 marker가 없습니다: {fragment}")

    paths = config.get("paths")
    if not isinstance(paths, dict):
        raise Phase3Error("Phase 3 경로 계약이 없습니다.")
    requirements_path = resolve_repo_path(repo_root, str(paths.get("requirements", "")))
    lock_path = resolve_repo_path(repo_root, str(paths.get("lock", "")))
    report_root = resolve_repo_path(repo_root, str(paths.get("report_root", "")))
    if not report_root.is_relative_to((repo_root / "data/reports").resolve()):
        raise Phase3Error("Phase 3 보고서는 data/reports 아래에 있어야 합니다.")
    options, actual_packages = _parse_requirements(requirements_path)
    expected_options = [
        "--index-url https://download.pytorch.org/whl/cu130",
        "--extra-index-url https://pypi.org/simple",
        "--only-binary :all:",
    ]
    if options != expected_options or actual_packages != expected_packages:
        raise Phase3Error("requirements.txt가 환경 계약과 다릅니다.")
    if require_lock and not lock_path.is_file():
        raise Phase3Error("Phase 3 lock snapshot이 없습니다.")

    ignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")
    if "/models/" not in ignore_text or "/.venv/" not in ignore_text:
        raise Phase3Error("모델 또는 .venv Git 제외 규칙이 없습니다.")
    sources = config.get("official_sources")
    if not isinstance(sources, list) or any(
        not isinstance(url, str) or not url.startswith("https://") for url in sources
    ):
        raise Phase3Error("공식 출처 URL 계약이 올바르지 않습니다.")
    return {
        "canonical_plan_version": config["canonical_plan_version"],
        "direct_package_count": len(expected_packages),
        "lock_present": lock_path.is_file(),
        "model_file_count": len(files),
        "model_revision": revision,
        "status": "valid",
    }


def _ensure_regular_path(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise Phase3Error(f"snapshot 파일이 없습니다: {relative.as_posix()}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise Phase3Error(f"snapshot symlink는 허용하지 않습니다: {relative.as_posix()}")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise Phase3Error(f"snapshot payload는 regular file이어야 합니다: {relative.as_posix()}")
    return current


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def review_remote_code(path: Path, banned_imports: set[str], banned_calls: set[str]) -> dict[str, Any]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise Phase3Error(f"remote code를 안전하게 파싱할 수 없습니다: {path.name}") from exc
    imports: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            calls.add(name)
            root = name.split(".", 1)[0]
            leaf = name.rsplit(".", 1)[-1]
            if root in banned_calls or leaf in banned_calls or leaf in REMOTE_CODE_FORBIDDEN_ATTRIBUTES:
                raise Phase3Error(f"remote code 금지 call을 발견했습니다: {path.name}:{name}")
    blocked_imports = sorted(imports & banned_imports)
    if blocked_imports:
        raise Phase3Error(
            f"remote code 금지 import를 발견했습니다: {path.name}:{','.join(blocked_imports)}"
        )
    return {
        "call_count": len(calls),
        "imports": sorted(imports),
        "network_subprocess_or_delete_calls": 0,
        "path": path.name,
        "sha256": sha256_file(path),
        "status": "reviewed",
    }


def verify_snapshot(
    config: dict[str, Any], repo_root: Path, snapshot_root: Path | None = None
) -> dict[str, Any]:
    model = config["model"]
    root = snapshot_root or resolve_repo_path(repo_root, model["local_subdir"])
    if not root.is_dir() or root.is_symlink():
        raise Phase3Error("고정 모델 snapshot 디렉터리가 없습니다.")
    expected = {item["path"]: item for item in model["files"]}
    actual_payload: set[str] = set()
    for directory, directory_names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in list(directory_names):
            child = directory_path / name
            if child.is_symlink():
                raise Phase3Error(f"snapshot 디렉터리 symlink는 허용하지 않습니다: {name}")
        relative_directory = directory_path.relative_to(root)
        if relative_directory.parts[:1] == (".cache",):
            continue
        for filename in filenames:
            child = directory_path / filename
            relative = child.relative_to(root).as_posix()
            if child.is_symlink() or not child.is_file():
                raise Phase3Error(f"snapshot special file은 허용하지 않습니다: {relative}")
            actual_payload.add(relative)
    if actual_payload != set(expected):
        missing = sorted(set(expected) - actual_payload)
        extra = sorted(actual_payload - set(expected))
        raise Phase3Error(f"snapshot payload 목록이 다릅니다: missing={missing}, extra={extra}")

    verified_files: list[dict[str, Any]] = []
    for relative_name in sorted(expected):
        item = expected[relative_name]
        path = _ensure_regular_path(root, validate_relative_path(relative_name))
        size = path.stat().st_size
        digest = sha256_file(path)
        if size != item["bytes"] or digest != item["sha256"]:
            raise Phase3Error(f"snapshot bytes 또는 SHA-256이 다릅니다: {relative_name}")
        verified_files.append({"bytes": size, "path": relative_name, "sha256": digest})

    remote_contract = model["remote_code"]
    banned_imports = set(remote_contract["banned_import_roots"])
    banned_calls = set(remote_contract["banned_calls"])
    remote_reviews = [
        review_remote_code(root / name, banned_imports, banned_calls)
        for name in remote_contract["paths"]
    ]
    manifest_core = {
        "files": verified_files,
        "repo_id": model["repo_id"],
        "revision": model["revision"],
    }
    return {
        "file_count": len(verified_files),
        "files": verified_files,
        "manifest_sha256": sha256_bytes(canonical_json_bytes(manifest_core)),
        "remote_code_review": remote_reviews,
        "repo_id": model["repo_id"],
        "revision": model["revision"],
        "status": "verified",
        "total_bytes": sum(item["bytes"] for item in verified_files),
    }


def _run_checked(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    if completed.returncode != 0:
        raise Phase3Error(f"외부 검증 명령이 실패했습니다: {Path(command[0]).name}")
    return completed.stdout.strip()


def download_model(
    config: dict[str, Any], repo_root: Path, *, execute: bool
) -> dict[str, Any]:
    validate_contract(config, repo_root)
    model = config["model"]
    target = resolve_repo_path(repo_root, model["local_subdir"])
    if target.exists():
        result = verify_snapshot(config, repo_root, target)
        result["action"] = "reused_verified_snapshot"
        return result

    try:
        from huggingface_hub import HfApi, snapshot_download

        remote = HfApi().model_info(
            model["repo_id"],
            revision=model["revision"],
            files_metadata=True,
        )
    except Exception as exc:
        raise Phase3Error("고정 Hugging Face revision metadata를 확인하지 못했습니다.") from exc
    if remote.sha != model["revision"]:
        raise Phase3Error("Hugging Face가 요청한 고정 revision과 다른 commit을 반환했습니다.")
    expected = {item["path"]: item for item in model["files"]}
    remote_files = {item.rfilename: item for item in remote.siblings}
    if set(remote_files) != set(expected):
        raise Phase3Error("원격 모델 payload 목록이 고정 manifest와 다릅니다.")
    lfs_sha256_verified = 0
    for name, expected_item in expected.items():
        remote_item = remote_files[name]
        if remote_item.size != expected_item["bytes"]:
            raise Phase3Error(f"원격 모델 파일 크기가 고정 manifest와 다릅니다: {name}")
        remote_lfs_sha256 = (remote_item.lfs or {}).get("sha256")
        if remote_lfs_sha256 is not None:
            if remote_lfs_sha256 != expected_item["sha256"]:
                raise Phase3Error(f"원격 LFS SHA-256이 고정 manifest와 다릅니다: {name}")
            lfs_sha256_verified += 1
    if not execute:
        return {
            "action": "dry_run",
            "expected_bytes": sum(item["bytes"] for item in model["files"]),
            "expected_file_count": len(model["files"]),
            "remote_lfs_sha256_verified": lfs_sha256_verified,
            "repo_id": model["repo_id"],
            "revision": model["revision"],
            "writes_performed": False,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = tempfile.mkdtemp(prefix=".phase3-download-", dir=target.parent)
    temporary = Path(temporary_name)
    promoted = False
    try:
        try:
            snapshot_download(
                model["repo_id"],
                allow_patterns=sorted(expected),
                local_dir=temporary,
                max_workers=4,
                revision=model["revision"],
            )
        except Exception as exc:
            raise Phase3Error("고정 Hugging Face snapshot 다운로드가 실패했습니다.") from exc
        local_cache = temporary / ".cache"
        if local_cache.is_symlink():
            raise Phase3Error("Hugging Face local cache symlink는 허용하지 않습니다.")
        if local_cache.exists():
            shutil.rmtree(local_cache)
        result = verify_snapshot(config, repo_root, temporary)
        if target.exists():
            raise Phase3Error("다운로드 중 최종 snapshot 경로가 생성돼 승격을 중단했습니다.")
        os.replace(temporary, target)
        promoted = True
        result["action"] = "downloaded_and_promoted"
        return result
    finally:
        if not promoted and temporary.exists():
            shutil.rmtree(temporary)


def _version_tuple(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as exc:
        raise Phase3Error(f"버전 문자열을 비교할 수 없습니다: {value}") from exc


def _package_versions(expected: dict[str, str]) -> dict[str, str]:
    installed: dict[str, str] = {}
    for name, version in expected.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise Phase3Error(f"필수 패키지가 설치되지 않았습니다: {name}") from exc
        if _version_base(actual) != version:
            raise Phase3Error(f"패키지 버전이 다릅니다: {name}={actual}, expected={version}")
        installed[name] = actual
    return installed


def collect_environment_smoke(config: dict[str, Any]) -> dict[str, Any]:
    expected_environment = config["environment"]
    python_version = ".".join(str(part) for part in sys.version_info[:3])
    if python_version != expected_environment["python_version"]:
        raise Phase3Error(
            f"Python 버전이 다릅니다: {python_version}, expected={expected_environment['python_version']}"
        )
    uv_output = _run_checked(["uv", "--version"])
    uv_version = uv_output.removeprefix("uv ").strip()
    if uv_version != expected_environment["uv_version"]:
        raise Phase3Error(f"uv 버전이 다릅니다: {uv_version}")
    packages = _package_versions(expected_environment["packages"])

    nvidia_output = _run_checked(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,compute_cap,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    rows = [row.strip() for row in nvidia_output.splitlines() if row.strip()]
    if len(rows) != 1:
        raise Phase3Error("Phase 3는 정확히 한 개의 NVIDIA GPU를 요구합니다.")
    columns = [column.strip() for column in rows[0].split(",")]
    if len(columns) != 4:
        raise Phase3Error("nvidia-smi 장비 정보를 해석할 수 없습니다.")
    gpu_name, driver_version, compute_capability_text, vram_text = columns
    hardware = config["hardware"]
    if gpu_name != hardware["gpu_name"]:
        raise Phase3Error(f"GPU 이름이 다릅니다: {gpu_name}")
    if _version_tuple(driver_version) < _version_tuple(hardware["minimum_driver_version"]):
        raise Phase3Error("CUDA 13.0 wheel에 필요한 최소 NVIDIA driver를 충족하지 않습니다.")
    if compute_capability_text != "12.0" or int(vram_text) < hardware["minimum_vram_mib"]:
        raise Phase3Error("RTX 5070 Ti compute capability 또는 VRAM Gate를 충족하지 않습니다.")

    import torch

    if not torch.cuda.is_available():
        raise Phase3Error("torch CUDA backend를 사용할 수 없습니다.")
    if torch.version.cuda != expected_environment["torch_cuda_version"]:
        raise Phase3Error(f"torch CUDA wheel이 cu130이 아닙니다: {torch.version.cuda}")
    device_index = torch.cuda.current_device()
    capability = list(torch.cuda.get_device_capability(device_index))
    if capability != hardware["compute_capability"]:
        raise Phase3Error(f"torch compute capability가 다릅니다: {capability}")
    architecture_list = torch.cuda.get_arch_list()
    if hardware["required_arch"] not in architecture_list:
        raise Phase3Error("PyTorch wheel에 sm_120 native architecture가 없습니다.")
    if not torch.cuda.is_bf16_supported():
        raise Phase3Error("GPU가 BF16을 지원하지 않습니다.")

    torch.cuda.reset_peak_memory_stats(device_index)
    left = torch.ones((128, 128), device="cuda", dtype=torch.bfloat16)
    right = torch.ones((128, 128), device="cuda", dtype=torch.bfloat16)
    product = left @ right
    torch.cuda.synchronize(device_index)
    if product.dtype != torch.bfloat16 or not torch.isfinite(product).all().item():
        raise Phase3Error("BF16 CUDA matmul 결과가 유한한 BF16 tensor가 아닙니다.")
    if float(product[0, 0].item()) != 128.0:
        raise Phase3Error("BF16 CUDA matmul 결과가 예상값과 다릅니다.")
    bf16_peak = torch.cuda.max_memory_allocated(device_index)
    del product, right, left

    import bitsandbytes as bnb
    from bitsandbytes.cextension import lib as bnb_library

    if not bool(getattr(bnb_library, "compiled_with_cuda", False)):
        raise Phase3Error("bitsandbytes가 CUDA native backend를 로드하지 못했습니다.")
    native_object = getattr(bnb_library, "_lib", None)
    native_name = Path(str(getattr(native_object, "_name", "unknown"))).name
    parameter = torch.nn.Parameter(
        torch.zeros(4096, device="cuda", dtype=torch.bfloat16)
    )
    optimizer = bnb.optim.Adam8bit([parameter], min_8bit_size=4096)
    optimizer.init_state(optimizer.param_groups[0], parameter, 0, 0)
    state = optimizer.state[parameter]
    for state_name in ("state1", "state2"):
        tensor = state.get(state_name)
        if tensor is None or tensor.dtype != torch.uint8 or tensor.device.type != "cuda":
            raise Phase3Error(f"bitsandbytes 8-bit CUDA state 초기화 실패: {state_name}")
    bnb_state = {
        "backend_compiled_with_cuda": True,
        "native_library": native_name,
        "optimizer": "Adam8bit",
        "optimizer_step_executed": False,
        "state1_device": state["state1"].device.type,
        "state1_dtype": str(state["state1"].dtype).removeprefix("torch."),
        "state2_device": state["state2"].device.type,
        "state2_dtype": str(state["state2"].dtype).removeprefix("torch."),
    }
    del optimizer, parameter
    torch.cuda.empty_cache()

    properties = torch.cuda.get_device_properties(device_index)
    return {
        "bf16": {
            "matmul": "passed",
            "peak_allocated_bytes": bf16_peak,
            "supported": True,
        },
        "bitsandbytes": bnb_state,
        "cuda": {
            "arch_list": architecture_list,
            "available": True,
            "compute_capability": capability,
            "device_count": torch.cuda.device_count(),
            "device_name": torch.cuda.get_device_name(device_index),
            "torch_cuda_version": torch.version.cuda,
            "total_memory_bytes": properties.total_memory,
        },
        "driver": {
            "compute_capability": compute_capability_text,
            "gpu_name": gpu_name,
            "nvidia_driver": driver_version,
            "vram_mib": int(vram_text),
        },
        "packages": packages,
        "python_version": python_version,
        "uv_version": uv_version,
    }


def load_model_offline(
    config: dict[str, Any], repo_root: Path, snapshot_manifest: dict[str, Any]
) -> dict[str, Any]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"

    import torch
    from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

    model_contract = config["model"]
    template_contract = config["chat_template"]
    snapshot_root = resolve_repo_path(repo_root, model_contract["local_subdir"])
    model_config = AutoConfig.from_pretrained(
        snapshot_root,
        local_files_only=True,
        trust_remote_code=True,
    )
    if model_config.max_position_embeddings != model_contract["expected_max_position_embeddings"]:
        raise Phase3Error("오프라인 config의 max_position_embeddings가 다릅니다.")
    if model_config.rope_parameters != model_contract["expected_rope_parameters"]:
        raise Phase3Error("오프라인 config의 rope_parameters가 다릅니다.")
    yarn = model_config.rope_parameters["full_attention"]
    implicit_yarn_factor = (
        model_config.max_position_embeddings / yarn["original_max_position_embeddings"]
    )
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot_root,
        local_files_only=True,
        trust_remote_code=True,
    )
    token_contract = template_contract["special_tokens"]
    for name in ("bos_token", "eos_token", "pad_token"):
        if getattr(tokenizer, name) != token_contract[name]:
            raise Phase3Error(f"tokenizer special token이 다릅니다: {name}")
        if getattr(tokenizer, f"{name}_id") != token_contract[f"{name}_id"]:
            raise Phase3Error(f"tokenizer special token ID가 다릅니다: {name}")
    if not isinstance(tokenizer.chat_template, str) or sha256_bytes(
        tokenizer.chat_template.encode("utf-8")
    ) != template_contract["sha256"]:
        raise Phase3Error("오프라인 tokenizer가 고정 chat template를 로드하지 못했습니다.")

    device_index = torch.cuda.current_device()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats(device_index)
    free_before, total_memory = torch.cuda.mem_get_info(device_index)
    model: Any | None = None
    try:
        loaded = AutoModelForCausalLM.from_pretrained(
            snapshot_root,
            device_map={"": device_index},
            dtype=torch.bfloat16,
            local_files_only=True,
            low_cpu_mem_usage=True,
            output_loading_info=True,
            trust_remote_code=True,
        )
        model, loading_info = loaded
        torch.cuda.synchronize(device_index)
        for key in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"):
            if loading_info.get(key):
                raise Phase3Error(f"모델 load 정보에 {key}가 있습니다.")
        parameter_count = sum(parameter.numel() for parameter in model.parameters())
        if parameter_count != model_contract["expected_parameter_count"]:
            raise Phase3Error(f"모델 parameter 수가 다릅니다: {parameter_count}")
        if model.__class__.__name__ != model_contract["expected_class"]:
            raise Phase3Error(f"모델 class가 다릅니다: {model.__class__.__name__}")
        dtype_histogram: dict[str, int] = {}
        device_histogram: dict[str, int] = {}
        for parameter in model.parameters():
            dtype = str(parameter.dtype).removeprefix("torch.")
            device = parameter.device.type
            dtype_histogram[dtype] = dtype_histogram.get(dtype, 0) + parameter.numel()
            device_histogram[device] = device_histogram.get(device, 0) + parameter.numel()
        if dtype_histogram != {"bfloat16": parameter_count}:
            raise Phase3Error(f"모델 parameter dtype이 전부 BF16이 아닙니다: {dtype_histogram}")
        if device_histogram != {"cuda": parameter_count}:
            raise Phase3Error(f"모델 parameter가 전부 CUDA에 있지 않습니다: {device_histogram}")
        free_after, _ = torch.cuda.mem_get_info(device_index)
        return {
            "config_class": model_config.__class__.__name__,
            "device_histogram": device_histogram,
            "dtype_histogram": dtype_histogram,
            "forward_or_generation_executed": False,
            "free_vram_after_load_bytes": free_after,
            "free_vram_before_load_bytes": free_before,
            "loading_info": {
                "error_message_count": len(loading_info.get("error_msgs", [])),
                "mismatched_key_count": len(loading_info.get("mismatched_keys", [])),
                "missing_key_count": len(loading_info.get("missing_keys", [])),
                "unexpected_key_count": len(loading_info.get("unexpected_keys", [])),
            },
            "local_files_only": True,
            "max_position_embeddings": model_config.max_position_embeddings,
            "model_class": model.__class__.__name__,
            "offline_environment": True,
            "parameter_count": parameter_count,
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(device_index),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(device_index),
            "snapshot_manifest_sha256": snapshot_manifest["manifest_sha256"],
            "tokenizer_class": tokenizer.__class__.__name__,
            "total_vram_bytes": total_memory,
            "trust_remote_code": True,
            "upstream_rope_configuration": {
                "explicit_yarn_factor": yarn["factor"],
                "implicit_context_ratio": implicit_yarn_factor,
                "parameters": model_config.rope_parameters,
                "ratio_matches_explicit_factor": yarn["factor"] == implicit_yarn_factor,
                "snapshot_preserved_without_override": True,
            },
        }
    finally:
        if model is not None:
            del model
        del tokenizer, model_config
        torch.cuda.empty_cache()


def _freeze_environment() -> str:
    freeze = _run_checked(["uv", "pip", "freeze", "--python", sys.executable])
    return freeze.rstrip() + "\n"


def _git_head(repo_root: Path) -> str:
    return _run_checked(["git", "-C", str(repo_root), "rev-parse", "HEAD"])


def _implementation_hashes(repo_root: Path, config: dict[str, Any]) -> dict[str, str]:
    paths = [
        "scripts/model/errors.py",
        "scripts/model/phase3_tools.py",
        "scripts/model/phase3_prepare.py",
        "tests/test_phase3_model_preparation.py",
        config["paths"]["requirements"],
        config["paths"]["lock"],
        config["chat_template"]["tracked_path"],
        "configs/model_versions/saju_1b_baseline/model-preparation-v1.0.0.json",
    ]
    return {path: sha256_file(resolve_repo_path(repo_root, path)) for path in paths}


def _write_text_once(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise Phase3Error(f"기존 불변 산출물과 내용이 다릅니다: {path.name}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _write_report_once(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Phase3Error("기존 Phase 3 보고서를 읽을 수 없습니다.") from exc
        candidate = dict(payload)
        candidate["generated_at"] = existing.get("generated_at")
        if candidate != existing:
            raise Phase3Error("기존 Phase 3 build 보고서와 결과가 다릅니다.")
        return existing
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_once(path, text)
    return payload


def run_smoke(
    config: dict[str, Any], repo_root: Path, *, write_report: bool
) -> dict[str, Any]:
    validate_contract(config, repo_root, require_lock=True)
    snapshot = verify_snapshot(config, repo_root)
    environment = collect_environment_smoke(config)
    model_load = load_model_offline(config, repo_root, snapshot)
    freeze_text = _freeze_environment()
    implementation_hashes = _implementation_hashes(repo_root, config)
    snapshot_report = {
        "file_count": snapshot["file_count"],
        "files": snapshot["files"],
        "manifest_sha256": snapshot["manifest_sha256"],
        "remote_code_review": snapshot["remote_code_review"],
        "repo_id": snapshot["repo_id"],
        "revision": snapshot["revision"],
        "schema_version": "1.0.0",
        "total_bytes": snapshot["total_bytes"],
    }
    build_inputs = {
        "canonical_plan_version": config["canonical_plan_version"],
        "environment_identity": {
            "compute_capability": environment["cuda"]["compute_capability"],
            "driver": environment["driver"]["nvidia_driver"],
            "gpu_name": environment["driver"]["gpu_name"],
            "packages": environment["packages"],
            "python_version": environment["python_version"],
            "torch_cuda_version": environment["cuda"]["torch_cuda_version"],
            "total_memory_bytes": environment["cuda"]["total_memory_bytes"],
            "uv_version": environment["uv_version"],
        },
        "implementation_hashes": implementation_hashes,
        "package_freeze_sha256": sha256_bytes(freeze_text.encode("utf-8")),
        "snapshot_manifest_sha256": snapshot["manifest_sha256"],
    }
    build_sha256 = sha256_bytes(canonical_json_bytes(build_inputs))
    build_id = f"build-{build_sha256[:12]}"
    report = {
        "build_id": build_id,
        "build_inputs": build_inputs,
        "build_sha256": build_sha256,
        "canonical_plan_version": config["canonical_plan_version"],
        "contains_model_weights": False,
        "environment": environment,
        "generated_at": utc_now(),
        "model_load": model_load,
        "model_manifest_sha256": snapshot["manifest_sha256"],
        "optimizer_step_executed": False,
        "phase": 3,
        "phase_4_entry_allowed": True,
        "report_type": "phase_3_model_preparation_verification",
        "schema_version": "1.0.0",
        "status": "passed",
        "training_promotion_allowed": False,
        "workspace_base_commit": _git_head(repo_root),
    }
    if not write_report:
        report["writes_performed"] = False
        return report

    report_root = resolve_repo_path(repo_root, config["paths"]["report_root"])
    build_root = report_root / build_id
    _write_text_once(
        build_root / "model_manifest.json",
        json.dumps(snapshot_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _write_text_once(build_root / "package_freeze.txt", freeze_text)
    stored = _write_report_once(build_root / "verification_report.json", report)
    return {
        "build_id": build_id,
        "build_sha256": build_sha256,
        "report_path": (build_root / "verification_report.json")
        .relative_to(repo_root)
        .as_posix(),
        "status": stored["status"],
        "training_promotion_allowed": stored["training_promotion_allowed"],
        "writes_performed": True,
    }


def verify_report(
    config: dict[str, Any], repo_root: Path, report_path: Path | None = None
) -> dict[str, Any]:
    validate_contract(config, repo_root, require_lock=True)
    report_root = resolve_repo_path(repo_root, config["paths"]["report_root"])
    if report_path is None:
        candidates = sorted(report_root.glob("build-*/verification_report.json"))
        if len(candidates) != 1:
            raise Phase3Error("검증할 Phase 3 보고서는 정확히 하나여야 합니다.")
        report_path = candidates[0]
    resolved_report = report_path.expanduser().resolve()
    if not resolved_report.is_relative_to(report_root.resolve()):
        raise Phase3Error("Phase 3 report_root 밖의 보고서는 검증할 수 없습니다.")
    try:
        report = json.loads(resolved_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase3Error("Phase 3 보고서를 읽을 수 없습니다.") from exc
    if (
        report.get("status") != "passed"
        or report.get("training_promotion_allowed") is not False
        or report.get("phase_4_entry_allowed") is not True
    ):
        raise Phase3Error("Phase 3 보고서 Gate 값이 올바르지 않습니다.")
    expected_build_sha = sha256_bytes(canonical_json_bytes(report.get("build_inputs")))
    if report.get("build_sha256") != expected_build_sha:
        raise Phase3Error("Phase 3 report build SHA-256이 다릅니다.")
    expected_build_id = f"build-{expected_build_sha[:12]}"
    if report.get("build_id") != expected_build_id or resolved_report.parent.name != expected_build_id:
        raise Phase3Error("Phase 3 report build ID 또는 경로가 다릅니다.")
    current_hashes = _implementation_hashes(repo_root, config)
    if report["build_inputs"].get("implementation_hashes") != current_hashes:
        raise Phase3Error("Phase 3 구현·lock·template hash가 보고서와 다릅니다.")
    freeze_text = _freeze_environment()
    if sha256_bytes(freeze_text.encode("utf-8")) != report["build_inputs"].get(
        "package_freeze_sha256"
    ):
        raise Phase3Error("현재 package freeze가 Phase 3 보고서와 다릅니다.")
    snapshot = verify_snapshot(config, repo_root)
    if snapshot["manifest_sha256"] != report.get("model_manifest_sha256"):
        raise Phase3Error("현재 모델 snapshot이 Phase 3 보고서와 다릅니다.")
    manifest_path = resolved_report.parent / "model_manifest.json"
    freeze_path = resolved_report.parent / "package_freeze.txt"
    if not manifest_path.is_file() or not freeze_path.is_file():
        raise Phase3Error("Phase 3 보조 보고서가 누락됐습니다.")
    if freeze_path.read_text(encoding="utf-8") != freeze_text:
        raise Phase3Error("고정 package_freeze.txt가 현재 환경과 다릅니다.")
    return {
        "build_id": expected_build_id,
        "model_file_count": snapshot["file_count"],
        "status": "verified",
        "training_promotion_allowed": False,
    }
