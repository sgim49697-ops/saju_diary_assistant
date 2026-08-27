# source_tools.py - Phase 1 원천을 안전하게 수집하고 집계 inventory를 생성한다.

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from scripts.data.archive_safety import (
    merge_zip_parts,
    safe_extract_tar,
    validate_relative_archive_path,
    validate_zip_paths,
)
from scripts.data.errors import Phase1Error


EXPECTED_SOURCES = {
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy",
    "yeji_bazi_rules",
}
EXPECTED_AXES = {
    "nemotron_saju",
    "bazi_sft",
    "aihub_empathy_single",
    "aihub_empathy_multiturn",
    "yeji_shensha_derived",
}
EXPECTED_MIX_TOTALS = {"mix1k": 1_000, "mix10": 10_000, "mix20": 20_000}
AIHUB_ALLOWED_DATASET_ID = "86"
AIHUB_FORBIDDEN_DATASET_ID = "271"
AIHUB_ALLOWED_HOST = "api.aihub.or.kr"
MANIFEST_NAME = "SOURCE_MANIFEST.json"
HASH_CHUNK_BYTES = 4 * 1024 * 1024
MAX_JSON_MEMBER_BYTES = 2 * 1024 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_versioned_json_once(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """버전 경로의 보고서를 덮어쓰지 않고 동일 입력 재실행만 허용한다."""
    if not path.exists():
        write_json_atomic(path, payload)
        return payload
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase1Error(f"기존 버전 보고서를 읽을 수 없습니다: {path}") from exc
    candidate = dict(payload)
    if isinstance(existing, dict) and "generated_at" in existing:
        candidate["generated_at"] = existing["generated_at"]
    if candidate != existing:
        raise Phase1Error(
            "기존 source build 보고서와 결과가 다릅니다. 원천 bundle의 minor 버전을 올리세요."
        )
    return existing


def load_config(config_path: Path) -> dict[str, Any]:
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            config = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase1Error(f"설정 파일을 읽을 수 없습니다: {config_path}") from exc
    if not isinstance(config, dict):
        raise Phase1Error("설정 최상위 값은 JSON object여야 합니다.")
    return config


def resolve_repo_path(repo_root: Path, relative_path: str) -> Path:
    raw = Path(relative_path)
    if raw.is_absolute() or ".." in raw.parts:
        raise Phase1Error(f"저장소 상대경로가 안전하지 않습니다: {relative_path}")
    root = repo_root.resolve()
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise Phase1Error(f"저장소 밖 경로를 사용할 수 없습니다: {relative_path}")
    return resolved


def validate_config(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    sources = config.get("sources")
    if not isinstance(sources, dict) or set(sources) != EXPECTED_SOURCES:
        raise Phase1Error(
            f"활성 원천은 정확히 {sorted(EXPECTED_SOURCES)}여야 합니다."
        )

    axes = config.get("mix_contract", {}).get("axes")
    if not isinstance(axes, dict) or set(axes) != EXPECTED_AXES:
        raise Phase1Error(f"학습 혼합축은 정확히 {sorted(EXPECTED_AXES)}여야 합니다.")
    totals: dict[str, int] = {}
    for key, expected in EXPECTED_MIX_TOTALS.items():
        try:
            actual = sum(int(axis[key]) for axis in axes.values())
        except (KeyError, TypeError, ValueError) as exc:
            raise Phase1Error(f"혼합 계약의 {key} 수량이 올바르지 않습니다.") from exc
        if actual != expected:
            raise Phase1Error(f"{key} 합계가 {expected}가 아니라 {actual}입니다.")
        totals[key] = actual

    aihub = sources["aihub_empathy"]
    if str(aihub.get("dataset_id")) != AIHUB_ALLOWED_DATASET_ID:
        raise Phase1Error("활성 AI Hub 원천은 dataSetSn=86만 허용합니다.")
    active_serialized = json.dumps(sources, ensure_ascii=False, sort_keys=True)
    if f'"dataset_id": "{AIHUB_FORBIDDEN_DATASET_ID}"' in active_serialized:
        raise Phase1Error("AI Hub #271은 활성 원천에 등록할 수 없습니다.")
    excluded_271 = config.get("excluded_sources", {}).get("aihub_271_keti", {})
    if (
        str(excluded_271.get("dataset_id")) != AIHUB_FORBIDDEN_DATASET_ID
        or excluded_271.get("usage_class") != "contract_required"
    ):
        raise Phase1Error("AI Hub #271은 contract_required 제외 원천으로 고정해야 합니다.")

    endpoint = str(aihub.get("endpoint_template", ""))
    parsed = urllib.parse.urlparse(
        endpoint.replace("{dataset_id}", "86").replace("{file_key}", "0")
    )
    if parsed.scheme != "https" or parsed.hostname != AIHUB_ALLOWED_HOST:
        raise Phase1Error("AI Hub endpoint는 공식 HTTPS host만 허용합니다.")
    keys = aihub.get("file_keys")
    if keys != ["66046", "66047", "66048", "66049"]:
        raise Phase1Error("AI Hub #86 file key 목록이 승인된 네 값과 다릅니다.")

    local_subdirs: set[str] = set()
    for source_name, source in sources.items():
        local_subdir = source.get("local_subdir")
        if not isinstance(local_subdir, str):
            raise Phase1Error(f"{source_name} local_subdir가 없습니다.")
        resolve_repo_path(repo_root, f"data/raw/{local_subdir}")
        if local_subdir in local_subdirs:
            raise Phase1Error(f"중복 local_subdir입니다: {local_subdir}")
        local_subdirs.add(local_subdir)
        for filename in source.get("allow_files", []):
            validate_relative_archive_path(filename)

    key_file = Path(os.path.expanduser(config["paths"]["aihub_key_file"]))
    if key_file.resolve().is_relative_to(repo_root.resolve()):
        raise Phase1Error("AI Hub 키 파일은 Git 저장소 밖에 있어야 합니다.")

    return {
        "status": "ok",
        "schema_version": config.get("schema_version"),
        "canonical_plan_version": config.get("canonical_plan_version"),
        "raw_source_count": len(sources),
        "mix_axis_count": len(axes),
        "mix_totals": totals,
        "aihub_active_dataset": AIHUB_ALLOWED_DATASET_ID,
        "aihub_271_policy": "contract_required_excluded",
    }


def read_aihub_key(key_file: Path) -> str:
    expanded = key_file.expanduser()
    try:
        file_lstat = expanded.lstat()
        parent_stat = expanded.parent.stat()
    except OSError as exc:
        raise Phase1Error(
            "AI Hub 키 파일이 없습니다. ~/.config/saju_diary_assistant/aihub.env를 확인하세요."
        ) from exc
    if stat.S_ISLNK(file_lstat.st_mode) or not stat.S_ISREG(file_lstat.st_mode):
        raise Phase1Error("AI Hub 키 파일은 일반 파일이어야 하며 symlink는 허용하지 않습니다.")
    if file_lstat.st_uid != os.getuid():
        raise Phase1Error("AI Hub 키 파일 소유자가 현재 사용자와 다릅니다.")
    if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != os.getuid():
        raise Phase1Error("AI Hub 키 디렉터리는 현재 사용자 소유의 일반 디렉터리여야 합니다.")
    if stat.S_IMODE(file_lstat.st_mode) & 0o077:
        raise Phase1Error("AI Hub 키 파일 권한은 0600이어야 합니다.")
    if stat.S_IMODE(parent_stat.st_mode) & 0o077:
        raise Phase1Error("AI Hub 키 디렉터리 권한은 0700이어야 합니다.")

    values: list[str] = []
    try:
        for raw_line in expanded.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("AIHUB_APIKEY="):
                raise Phase1Error("AI Hub 키 파일에는 AIHUB_APIKEY 한 항목만 허용합니다.")
            values.append(line.split("=", 1)[1].strip())
    except UnicodeError as exc:
        raise Phase1Error("AI Hub 키 파일은 UTF-8 텍스트여야 합니다.") from exc
    if len(values) != 1 or not values[0]:
        raise Phase1Error("AIHUB_APIKEY 값이 비어 있거나 중복됐습니다.")
    if any(character.isspace() for character in values[0]):
        raise Phase1Error("AIHUB_APIKEY에는 공백을 넣을 수 없습니다.")
    return values[0]


def source_root(config: dict[str, Any], repo_root: Path, source_name: str) -> Path:
    raw_root = resolve_repo_path(repo_root, config["paths"]["raw_root"])
    local_subdir = config["sources"][source_name]["local_subdir"]
    resolved = (raw_root / local_subdir).resolve()
    if not resolved.is_relative_to(raw_root.resolve()):
        raise Phase1Error(f"{source_name} 원천 경로가 raw root 밖입니다.")
    return resolved


def _iter_manifest_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        if any(part in {".cache", "__pycache__"} for part in path.relative_to(root).parts):
            continue
        yield path


def build_source_manifest(
    config: dict[str, Any], repo_root: Path, source_name: str
) -> dict[str, Any]:
    source = config["sources"][source_name]
    root = source_root(config, repo_root, source_name)
    variant_by_path = {
        item["target"]: item["variant"]
        for item in source.get("legacy_files", [])
        if "target" in item and "variant" in item
    }
    files = []
    for path in _iter_manifest_files(root):
        relative = path.relative_to(root).as_posix()
        item: dict[str, Any] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if relative in variant_by_path:
            item["source_variant"] = variant_by_path[relative]
        if "/train" in f"/{relative}":
            item["split"] = "train"
        elif "/validation" in f"/{relative}":
            item["split"] = "validation"
        elif "/test" in f"/{relative}":
            item["split"] = "test"
        files.append(item)
    source_paths = list(_iter_manifest_files(root))
    retrieved_at = (
        datetime.fromtimestamp(
            min(path.stat().st_mtime for path in source_paths), timezone.utc
        )
        .replace(microsecond=0)
        .isoformat()
        if source_paths
        else utc_now()
    )
    manifest = {
        "schema_version": "1.0.0",
        "source": source_name,
        "repo_or_provider": source.get("repo_id", source.get("provider")),
        "revision": source.get("revision", source.get("release")),
        "retrieved_at": retrieved_at,
        "license_expression": source["license_expression"],
        "usage_class": source["usage_class"],
        "provenance_status": "verified" if files else "missing",
        "access_scope": source["access_scope"],
        "files": files,
    }
    if source.get("access_approved_at"):
        manifest["access_approved_at"] = source["access_approved_at"]
    write_json_atomic(root / MANIFEST_NAME, manifest, mode=0o600)
    return manifest


def migrate_nemotron(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    source = config["sources"]["nemotron_saju"]
    destination_root = source_root(config, repo_root, "nemotron_saju")
    destination_root.mkdir(parents=True, exist_ok=True)
    moved: list[dict[str, Any]] = []

    for expected in source["legacy_files"]:
        legacy = resolve_repo_path(repo_root, expected["path"])
        target = destination_root / expected["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if legacy.exists() and target.exists():
            raise Phase1Error(f"Nemotron 이전·신규 경로에 파일이 동시에 있습니다: {target.name}")
        current = target if target.exists() else legacy
        if not current.exists():
            raise Phase1Error(f"Nemotron 고정 shard를 찾을 수 없습니다: {expected['target']}")
        if current.stat().st_size != expected["bytes"] or sha256_file(current) != expected["sha256"]:
            raise Phase1Error(f"Nemotron 고정 shard 검증에 실패했습니다: {expected['target']}")
        action = "already_migrated"
        if current == legacy:
            os.replace(legacy, target)
            action = "moved"
        if target.stat().st_size != expected["bytes"] or sha256_file(target) != expected["sha256"]:
            raise Phase1Error(f"Nemotron 이동 후 검증에 실패했습니다: {expected['target']}")
        moved.append({"variant": expected["variant"], "target": expected["target"], "action": action})

    legacy_readme = resolve_repo_path(repo_root, "data/nemotron_saju/README.md")
    target_readme = destination_root / "README.huggingface.md"
    if legacy_readme.exists() and not target_readme.exists():
        os.replace(legacy_readme, target_readme)

    legacy_cache = resolve_repo_path(repo_root, "data/nemotron_saju/.cache")
    target_cache = destination_root / ".cache"
    if legacy_cache.exists() and not target_cache.exists():
        shutil.move(str(legacy_cache), str(target_cache))

    legacy_data_dir = resolve_repo_path(repo_root, "data/nemotron_saju/data")
    if legacy_data_dir.exists():
        try:
            legacy_data_dir.rmdir()
        except OSError as exc:
            raise Phase1Error("Nemotron legacy data 디렉터리에 예상하지 않은 파일이 남았습니다.") from exc

    manifest = build_source_manifest(config, repo_root, "nemotron_saju")
    return {"status": "ok", "source": "nemotron_saju", "files": moved, "manifest_files": len(manifest["files"])}


def _hf_remote_file_sizes(source: dict[str, Any]) -> dict[str, int | None]:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise Phase1Error("huggingface-hub가 없습니다. uv로 requirements-data.txt를 설치하세요.") from exc
    api = HfApi()
    try:
        entries = api.list_repo_tree(
            repo_id=source["repo_id"],
            repo_type="dataset",
            revision=source["revision"],
            recursive=True,
            expand=True,
        )
        sizes = {
            entry.path: getattr(entry, "size", None)
            for entry in entries
            if hasattr(entry, "path")
        }
    except Exception as exc:
        raise Phase1Error(f"Hugging Face 원격 목록 확인에 실패했습니다: {source['repo_id']}") from exc
    missing = [name for name in source["allow_files"] if name not in sizes]
    if missing:
        raise Phase1Error(f"고정 revision에서 허용 파일을 찾지 못했습니다: {missing}")
    return {name: sizes[name] for name in source["allow_files"]}


def plan_hf_downloads(
    config: dict[str, Any], source_names: Iterable[str]
) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    for source_name in source_names:
        if source_name not in {"bazi_sft", "yeji_bazi_rules"}:
            raise Phase1Error(f"HF 수집 대상이 아닙니다: {source_name}")
        source = config["sources"][source_name]
        sizes = _hf_remote_file_sizes(source)
        plans.append(
            {
                "source": source_name,
                "repo_id": source["repo_id"],
                "revision": source["revision"],
                "files": [{"path": path, "bytes": sizes[path]} for path in source["allow_files"]],
                "total_bytes": sum(size or 0 for size in sizes.values()),
                "local_subdir": source["local_subdir"],
            }
        )
    return {"mode": "dry-run", "plans": plans}


def _download_url(url: str, target: Path, headers: dict[str, str] | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.incomplete")
    if temporary.exists():
        temporary.unlink()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "saju-diary-phase1/1.0", **(headers or {})},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as stream:
            if response.status != 200:
                raise Phase1Error(f"다운로드 HTTP 상태가 200이 아닙니다: {response.status}")
            shutil.copyfileobj(response, stream, length=HASH_CHUNK_BYTES)
        os.replace(temporary, target)
    except urllib.error.HTTPError as exc:
        raise Phase1Error(f"다운로드 HTTP 오류: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise Phase1Error("다운로드 연결에 실패했습니다.") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def download_hf_sources(
    config: dict[str, Any], repo_root: Path, source_names: Iterable[str]
) -> dict[str, Any]:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise Phase1Error("huggingface-hub가 없습니다. uv로 requirements-data.txt를 설치하세요.") from exc

    results: list[dict[str, Any]] = []
    for source_name in source_names:
        if source_name not in {"bazi_sft", "yeji_bazi_rules"}:
            raise Phase1Error(f"HF 수집 대상이 아닙니다: {source_name}")
        source = config["sources"][source_name]
        destination = source_root(config, repo_root, source_name)
        destination.mkdir(parents=True, exist_ok=True)
        for filename in source["allow_files"]:
            try:
                hf_hub_download(
                    repo_id=source["repo_id"],
                    filename=filename,
                    repo_type="dataset",
                    revision=source["revision"],
                    local_dir=destination,
                )
            except Exception as exc:
                raise Phase1Error(f"Hugging Face 파일 수집에 실패했습니다: {source_name}/{filename}") from exc

        if source_name == "yeji_bazi_rules":
            provenance = source["provenance"]
            provenance_root = destination / "provenance" / provenance["revision"]
            for filename in provenance["allow_files"]:
                url = provenance["raw_url_template"].format(
                    revision=provenance["revision"], path=filename
                )
                target = provenance_root / filename
                if not target.exists():
                    _download_url(url, target)

        for filename, expected in source.get("expected_files", {}).items():
            path = destination / filename
            if (
                not path.is_file()
                or path.stat().st_size != expected["bytes"]
                or sha256_file(path) != expected["sha256"]
            ):
                raise Phase1Error(f"고정 파일 bytes/SHA-256 검증 실패: {source_name}/{filename}")
        manifest = build_source_manifest(config, repo_root, source_name)
        results.append(
            {
                "source": source_name,
                "revision": source["revision"],
                "file_count": len(manifest["files"]),
                "total_bytes": sum(item["bytes"] for item in manifest["files"]),
            }
        )
    return {"mode": "execute", "results": results}


def plan_aihub_download(config: dict[str, Any]) -> dict[str, Any]:
    source = config["sources"]["aihub_empathy"]
    return {
        "mode": "dry-run",
        "source": "aihub_empathy",
        "dataset_id": source["dataset_id"],
        "file_keys": list(source["file_keys"]),
        "request_count": len(source["file_keys"]),
        "host": AIHUB_ALLOWED_HOST,
        "local_subdir": source["local_subdir"],
        "key_source": config["paths"]["aihub_key_file"],
        "forbidden_dataset_ids": [AIHUB_FORBIDDEN_DATASET_ID],
    }


def aihub_request_headers(url: str, api_key: str) -> dict[str, str]:
    """공식 API host에만 인증 header를 붙여 redirect 시 비밀값 전달을 막는다."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise Phase1Error("AI Hub downloader가 안전하지 않은 URL을 거부했습니다.")
    headers = {"User-Agent": "saju-diary-phase1/1.0"}
    if parsed.hostname == AIHUB_ALLOWED_HOST:
        headers["apikey"] = api_key
    return headers


def _download_aihub_archive(url: str, api_key: str, target: Path, file_key: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.incomplete")
    if temporary.exists():
        temporary.unlink()
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request: Any, file_pointer: Any, code: int, message: str, headers: Any, new_url: str) -> None:
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        current_url = url
        response: Any = None
        for _ in range(6):
            headers = aihub_request_headers(current_url, api_key)
            request = urllib.request.Request(current_url, headers=headers, method="GET")
            try:
                response = opener.open(request, timeout=120)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    raise
                location = exc.headers.get("Location")
                if not location:
                    raise Phase1Error(f"AI Hub file key {file_key} redirect 위치가 없습니다.") from exc
                current_url = urllib.parse.urljoin(current_url, location)
        if response is None:
            raise Phase1Error(f"AI Hub file key {file_key} redirect 횟수가 한도를 넘었습니다.")
        with response, temporary.open("wb") as stream:
            if response.status != 200:
                raise Phase1Error(f"AI Hub file key {file_key} HTTP 상태가 200이 아닙니다.")
            shutil.copyfileobj(response, stream, length=HASH_CHUNK_BYTES)
        if temporary.stat().st_size == 0:
            raise Phase1Error(f"AI Hub file key {file_key} 응답이 비어 있습니다.")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except urllib.error.HTTPError as exc:
        raise Phase1Error(f"AI Hub file key {file_key} 다운로드 HTTP 오류: {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise Phase1Error(f"AI Hub file key {file_key} 다운로드 연결 실패") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def download_aihub(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    source = config["sources"]["aihub_empathy"]
    if source["dataset_id"] != AIHUB_ALLOWED_DATASET_ID:
        raise Phase1Error("AI Hub downloader는 #86 외의 데이터셋을 거부합니다.")
    key_file = Path(os.path.expanduser(config["paths"]["aihub_key_file"]))
    api_key = read_aihub_key(key_file)
    destination = source_root(config, repo_root, "aihub_empathy")
    archives = destination / "archives"
    extracted = destination / "extracted"
    archives.mkdir(parents=True, exist_ok=True, mode=0o700)
    extracted.mkdir(parents=True, exist_ok=True, mode=0o700)
    for private_directory in (destination, archives, extracted):
        os.chmod(private_directory, 0o700)
    results: list[dict[str, Any]] = []

    for file_key in source["file_keys"]:
        url = source["endpoint_template"].format(
            dataset_id=source["dataset_id"], file_key=file_key
        )
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != AIHUB_ALLOWED_HOST:
            raise Phase1Error("AI Hub 요청 대상이 공식 HTTPS host가 아닙니다.")
        archive_path = archives / f"filekey-{file_key}.tar"
        if not archive_path.exists():
            _download_aihub_archive(url, api_key, archive_path, file_key)
        extraction_root = extracted / f"filekey-{file_key}"
        members = safe_extract_tar(archive_path, extraction_root)
        zip_files = merge_zip_parts(extraction_root)
        results.append(
            {
                "file_key": file_key,
                "archive_bytes": archive_path.stat().st_size,
                "archive_sha256": sha256_file(archive_path),
                "member_count": len(members),
                "zip_count": len(zip_files),
            }
        )

    manifest = build_source_manifest(config, repo_root, "aihub_empathy")
    return {
        "mode": "execute",
        "source": "aihub_empathy",
        "dataset_id": AIHUB_ALLOWED_DATASET_ID,
        "files": results,
        "manifest_files": len(manifest["files"]),
    }


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def parquet_inventory(path: Path) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise Phase1Error("duckdb가 없습니다. uv로 requirements-data.txt를 설치하세요.") from exc
    connection = duckdb.connect(database=":memory:")
    literal = _sql_literal(str(path))
    try:
        description = connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet({literal})"
        ).fetchall()
        columns = [{"name": row[0], "type": row[1]} for row in description]
        row_count = int(
            connection.execute(f"SELECT count(*) FROM read_parquet({literal})").fetchone()[0]
        )
        null_expressions = [
            f"sum(CASE WHEN {_sql_identifier(column['name'])} IS NULL THEN 1 ELSE 0 END)"
            for column in columns
        ]
        null_values = connection.execute(
            f"SELECT {', '.join(null_expressions)} FROM read_parquet({literal})"
        ).fetchone()
        null_counts = {
            column["name"]: int(value or 0)
            for column, value in zip(columns, null_values, strict=True)
        }

        identifiers = [_sql_identifier(column["name"]) for column in columns]
        try:
            duplicate_estimate = int(
                connection.execute(
                    f"SELECT count(*) - count(DISTINCT hash({', '.join(identifiers)})) "
                    f"FROM read_parquet({literal})"
                ).fetchone()[0]
            )
        except Exception:
            duplicate_estimate = -1

        id_candidates = [
            column["name"]
            for column in columns
            if column["name"].lower() in {"id", "uuid", "synthetic_id", "conversation_id"}
        ]
        id_duplicates: dict[str, int] = {}
        for column in id_candidates:
            identifier = _sql_identifier(column)
            value = connection.execute(
                f"SELECT count({identifier}) - count(DISTINCT {identifier}) "
                f"FROM read_parquet({literal})"
            ).fetchone()[0]
            id_duplicates[column] = int(value or 0)

        categorical_counts: dict[str, list[dict[str, Any]]] = {}
        for column in columns:
            if column["name"].lower() not in {
                "question_type",
                "source",
                "domain",
                "split",
                "source_variant",
            }:
                continue
            identifier = _sql_identifier(column["name"])
            values = connection.execute(
                f"SELECT cast({identifier} AS VARCHAR), count(*) "
                f"FROM read_parquet({literal}) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 100"
            ).fetchall()
            categorical_counts[column["name"]] = [
                {"value": value, "count": int(count)} for value, count in values
            ]

        text_columns = [
            column["name"]
            for column in columns
            if any(token in column["type"].upper() for token in ("VARCHAR", "JSON"))
        ]
        text_stats: dict[str, Any] | None = None
        if text_columns:
            values = ", ".join(
                f"coalesce(cast({_sql_identifier(column)} AS VARCHAR), '')"
                for column in text_columns
            )
            row = connection.execute(
                "WITH source AS ("
                f"SELECT concat_ws('', {values}) AS text FROM read_parquet({literal})"
                ") SELECT "
                "sum(length(text)), "
                "sum(length(regexp_replace(text, '[^가-힣ㄱ-ㅎㅏ-ㅣ]', '', 'g'))), "
                "sum(length(regexp_replace(text, '[^A-Za-z]', '', 'g'))), "
                "sum(length(regexp_replace(text, '[^㐀-鿿]', '', 'g'))), "
                "avg(length(text)), quantile_cont(length(text), 0.5), "
                "quantile_cont(length(text), 0.9), quantile_cont(length(text), 0.95), "
                "max(length(text)) FROM source"
            ).fetchone()
            total_characters = int(row[0] or 0)
            text_stats = {
                "columns": text_columns,
                "total_characters": total_characters,
                "korean_character_ratio": (int(row[1] or 0) / total_characters if total_characters else 0.0),
                "english_character_ratio": (int(row[2] or 0) / total_characters if total_characters else 0.0),
                "cjk_character_ratio": (int(row[3] or 0) / total_characters if total_characters else 0.0),
                "row_character_length": {
                    "mean": float(row[4] or 0),
                    "p50": float(row[5] or 0),
                    "p90": float(row[6] or 0),
                    "p95": float(row[7] or 0),
                    "max": int(row[8] or 0),
                },
            }
        return {
            "rows": row_count,
            "columns": columns,
            "null_counts": null_counts,
            "row_hash_duplicate_estimate": duplicate_estimate,
            "id_duplicate_counts": id_duplicates,
            "categorical_counts": categorical_counts,
            "text_stats": text_stats,
        }
    except Exception as exc:
        if isinstance(exc, Phase1Error):
            raise
        raise Phase1Error(f"Parquet inventory에 실패했습니다: {path.name}") from exc
    finally:
        connection.close()


def parquet_collection_inventory(paths: list[Path]) -> dict[str, Any] | None:
    if not paths:
        return None
    try:
        import duckdb
    except ImportError as exc:
        raise Phase1Error("duckdb가 없습니다. uv로 requirements-data.txt를 설치하세요.") from exc
    connection = duckdb.connect(database=":memory:")
    literals = ", ".join(_sql_literal(str(path)) for path in paths)
    relation = f"read_parquet([{literals}], union_by_name=true)"
    try:
        individual_schemas = [
            [
                {"name": row[0], "type": row[1]}
                for row in connection.execute(
                    f"DESCRIBE SELECT * FROM read_parquet({_sql_literal(str(path))})"
                ).fetchall()
            ]
            for path in paths
        ]
        columns = [
            {"name": row[0], "type": row[1]}
            for row in connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
        ]
        identifiers = [_sql_identifier(column["name"]) for column in columns]
        row = connection.execute(
            f"SELECT count(*), count(*) - count(DISTINCT hash({', '.join(identifiers)})) "
            f"FROM {relation}"
        ).fetchone()
        id_duplicates: dict[str, int] = {}
        for column in columns:
            if column["name"].lower() not in {"id", "uuid", "synthetic_id", "conversation_id"}:
                continue
            identifier = _sql_identifier(column["name"])
            duplicate_count = connection.execute(
                f"SELECT count({identifier}) - count(DISTINCT {identifier}) FROM {relation}"
            ).fetchone()[0]
            id_duplicates[column["name"]] = int(duplicate_count or 0)
        schema_serialized = json.dumps(columns, ensure_ascii=False, sort_keys=True)
        return {
            "file_count": len(paths),
            "rows": int(row[0]),
            "row_hash_duplicate_estimate": int(row[1]),
            "id_duplicate_counts": id_duplicates,
            "schema_consistent_across_files": all(
                schema == individual_schemas[0] for schema in individual_schemas[1:]
            ),
            "unified_schema_sha256": hashlib.sha256(schema_serialized.encode("utf-8")).hexdigest(),
        }
    except Exception as exc:
        raise Phase1Error("Parquet 원천 전체 중복 inventory에 실패했습니다.") from exc
    finally:
        connection.close()


def _walk_dicts(value: Any, depth: int = 0) -> Iterator[tuple[dict[str, Any], int]]:
    if isinstance(value, dict):
        yield value, depth
        for child in value.values():
            yield from _walk_dicts(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child, depth + 1)


def _candidate_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, list):
        for child in value:
            if isinstance(child, dict):
                yield child
            elif isinstance(child, list):
                yield from _candidate_records(child)
        return
    if not isinstance(value, dict):
        return
    normalized_keys = {str(key).lower().replace("_", "-") for key in value}
    record_markers = {"talk", "profile", "content", "conversation", "dialogue", "utterances"}
    if normalized_keys & record_markers:
        yield value
        return
    list_values = [child for child in value.values() if isinstance(child, list)]
    if list_values:
        for child in list_values:
            yield from _candidate_records(child)
        return
    if value and all(isinstance(child, dict) for child in value.values()):
        yield from value.values()
        return
    yield value


def _flatten_leaf_items(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from _flatten_leaf_items(child)
            else:
                yield str(key), child
    elif isinstance(value, list):
        for child in value:
            yield from _flatten_leaf_items(child)


def _turn_pair_count(record: dict[str, Any]) -> int:
    human_turns: set[int] = set()
    system_turns: set[int] = set()
    human_pattern = re.compile(
        r"^(?:hs|human|user|사람문장|사용자문장|화자문장)[^0-9]*0*([1-9][0-9]*)$",
        re.IGNORECASE,
    )
    system_pattern = re.compile(
        r"^(?:ss|system|assistant|시스템응답|상담사문장|봇문장)[^0-9]*0*([1-9][0-9]*)$",
        re.IGNORECASE,
    )
    for key, value in _flatten_leaf_items(record):
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = re.sub(r"[\s_\-]", "", key)
        human = human_pattern.match(normalized)
        system = system_pattern.match(normalized)
        if human:
            human_turns.add(int(human.group(1)))
        if system:
            system_turns.add(int(system.group(1)))
    return len(human_turns & system_turns)


def _group_identifier_hash(record: dict[str, Any]) -> str | None:
    candidates = {
        "talkid",
        "conversationid",
        "dialogueid",
        "sessionid",
        "상황id",
        "대화id",
    }
    fallback: Any = None
    for key, value in _flatten_leaf_items(record):
        normalized = re.sub(r"[\s_\-]", "", key).lower()
        if normalized in candidates and isinstance(value, (str, int)):
            return hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        if normalized == "id" and isinstance(value, (str, int)):
            fallback = value
    if fallback is None:
        return None
    return hashlib.sha256(str(fallback).encode("utf-8")).hexdigest()


def json_document_inventory(value: Any) -> dict[str, Any]:
    field_counts: Counter[str] = Counter()
    object_count = 0
    max_depth = 0
    for mapping, depth in _walk_dicts(value):
        object_count += 1
        max_depth = max(max_depth, depth)
        field_counts.update(str(key) for key in mapping)

    records = list(_candidate_records(value))
    group_hashes: set[str] = set()
    record_hashes: set[str] = set()
    multiturn_without_id = 0
    records_with_group_id = 0
    records_with_two_pairs = 0
    turn_pair_counts: Counter[int] = Counter()
    for record in records:
        canonical_record = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        record_hashes.add(hashlib.sha256(canonical_record.encode("utf-8")).hexdigest())
        pair_count = _turn_pair_count(record)
        turn_pair_counts[pair_count] += 1
        group_hash = _group_identifier_hash(record)
        if group_hash is not None:
            records_with_group_id += 1
        if pair_count >= 2:
            records_with_two_pairs += 1
            if group_hash is not None:
                group_hashes.add(group_hash)
            else:
                multiturn_without_id += 1

    repeated_fields = [
        key for key, count in field_counts.most_common() if count >= 2
    ][:200]
    top_level_keys = list(value)[:100] if isinstance(value, dict) and len(value) <= 100 else []
    return {
        "top_level_type": type(value).__name__,
        "object_count": object_count,
        "record_count": len(records),
        "field_name_count": len(field_counts),
        "repeated_field_names": repeated_fields,
        "top_level_keys": top_level_keys,
        "max_depth": max_depth,
        "records_with_group_id": records_with_group_id,
        "records_with_two_or_more_turn_pairs": records_with_two_pairs,
        "turn_pair_count_distribution": {
            str(pair_count): count for pair_count, count in sorted(turn_pair_counts.items())
        },
        "multiturn_records_without_group_id": multiturn_without_id,
        "_eligible_group_hashes": group_hashes,
        "_record_hashes": record_hashes,
    }


def _read_json_stream(binary_stream: Any) -> Any:
    text_stream = io.TextIOWrapper(binary_stream, encoding="utf-8-sig")
    try:
        return json.load(text_stream)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise Phase1Error("JSON 문서를 파싱할 수 없습니다.") from exc
    finally:
        text_stream.detach()


def standalone_json_inventory(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            summary = json_document_inventory(_read_json_stream(stream))
    except OSError as exc:
        raise Phase1Error(f"JSON 파일을 읽을 수 없습니다: {path.name}") from exc
    return summary


def zip_json_inventory(path: Path) -> dict[str, Any]:
    validate_zip_paths(path)
    documents: list[dict[str, Any]] = []
    group_hashes: set[str] = set()
    record_hashes: set[str] = set()
    parse_failures = 0
    split_counts: Counter[str] = Counter()
    try:
        with zipfile.ZipFile(path) as archive:
            json_entries = [
                entry
                for entry in archive.infolist()
                if not entry.is_dir() and entry.filename.lower().endswith(".json")
            ]
            for entry in json_entries:
                if entry.file_size > MAX_JSON_MEMBER_BYTES:
                    raise Phase1Error("zip 내부 JSON이 안전한 처리 한도를 넘었습니다.")
                normalized = entry.filename.lower()
                if "train" in normalized or "training" in normalized or "학습" in normalized:
                    split_counts["train"] += 1
                elif "valid" in normalized or "validation" in normalized or "검증" in normalized:
                    split_counts["validation"] += 1
                else:
                    split_counts["unknown"] += 1
                try:
                    with archive.open(entry) as stream:
                        summary = json_document_inventory(_read_json_stream(stream))
                    group_hashes.update(summary.pop("_eligible_group_hashes"))
                    record_hashes.update(summary.pop("_record_hashes"))
                    documents.append(summary)
                except Phase1Error:
                    parse_failures += 1
    except (zipfile.BadZipFile, OSError) as exc:
        raise Phase1Error(f"zip inventory에 실패했습니다: {path.name}") from exc

    fields: set[str] = set()
    record_count = 0
    records_with_pairs = 0
    records_with_group_id = 0
    turn_pair_counts: Counter[int] = Counter()
    for document in documents:
        fields.update(document["repeated_field_names"])
        record_count += document["record_count"]
        records_with_pairs += document["records_with_two_or_more_turn_pairs"]
        records_with_group_id += document["records_with_group_id"]
        turn_pair_counts.update(
            {int(pair_count): count for pair_count, count in document["turn_pair_count_distribution"].items()}
        )
    return {
        "json_member_count": len(documents) + parse_failures,
        "json_parse_success_count": len(documents),
        "json_parse_failure_count": parse_failures,
        "split_json_member_counts": dict(sorted(split_counts.items())),
        "record_count": record_count,
        "records_with_group_id": records_with_group_id,
        "records_with_two_or_more_turn_pairs": records_with_pairs,
        "turn_pair_count_distribution": {
            str(pair_count): count for pair_count, count in sorted(turn_pair_counts.items())
        },
        "repeated_field_names": sorted(fields),
        "_eligible_group_hashes": group_hashes,
        "_record_hashes": record_hashes,
    }


def inventory_source(
    config: dict[str, Any], repo_root: Path, source_name: str
) -> dict[str, Any]:
    root = source_root(config, repo_root, source_name)
    if not root.exists():
        return {"source": source_name, "status": "missing", "file_count": 0, "total_bytes": 0}
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_file():
        _verify_manifest(root)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Phase1Error(f"SOURCE_MANIFEST.json을 읽을 수 없습니다: {source_name}") from exc
    else:
        manifest = build_source_manifest(config, repo_root, source_name)
    if not manifest["files"]:
        return {
            "source": source_name,
            "status": "missing",
            "revision": config["sources"][source_name].get(
                "revision", config["sources"][source_name].get("release")
            ),
            "file_count": 0,
            "total_bytes": 0,
            "access_scope": config["sources"][source_name]["access_scope"],
            "access_approved_at": config["sources"][source_name].get("access_approved_at"),
        }
    parquet_files: list[dict[str, Any]] = []
    json_files: list[dict[str, Any]] = []
    zip_files: list[dict[str, Any]] = []
    group_hashes: set[str] = set()
    group_hashes_by_split: dict[str, set[str]] = {"train": set(), "validation": set(), "unknown": set()}
    record_hashes_by_split: dict[str, set[str]] = {"train": set(), "validation": set(), "unknown": set()}
    parse_failures = 0

    for item in manifest["files"]:
        path = root / item["path"]
        suffix = path.suffix.lower()
        try:
            if suffix == ".parquet":
                parquet_files.append({"path": item["path"], **parquet_inventory(path)})
            elif suffix == ".json" and not path.name.endswith("MANIFEST.json"):
                summary = standalone_json_inventory(path)
                group_hashes.update(summary.pop("_eligible_group_hashes"))
                summary.pop("_record_hashes")
                json_files.append({"path": item["path"], **summary})
            elif suffix == ".zip":
                summary = zip_json_inventory(path)
                zip_groups = summary.pop("_eligible_group_hashes")
                zip_records = summary.pop("_record_hashes")
                normalized_path = item["path"].lower()
                if "training" in normalized_path or "/train" in normalized_path:
                    split = "train"
                elif "validation" in normalized_path or "/valid" in normalized_path:
                    split = "validation"
                else:
                    split = "unknown"
                group_hashes.update(zip_groups)
                group_hashes_by_split[split].update(zip_groups)
                record_hashes_by_split[split].update(zip_records)
                zip_files.append({"path": item["path"], "split": split, **summary})
        except Phase1Error:
            parse_failures += 1

    parquet_paths = [root / item["path"] for item in manifest["files"] if item["path"].lower().endswith(".parquet")]
    parquet_aggregate = parquet_collection_inventory(parquet_paths)

    rows = sum(item["rows"] for item in parquet_files)
    zip_records = sum(item["record_count"] for item in zip_files)
    status = "collected"
    structural_minimum = config["mix_contract"]["aihub_multiturn_min_structural_groups"]
    structural_groups = len(group_hashes)
    if source_name == "aihub_empathy" and structural_groups < structural_minimum:
        status = "blocked_insufficient_multiturn_groups"
    return {
        "source": source_name,
        "status": status,
        "revision": config["sources"][source_name].get(
            "revision", config["sources"][source_name].get("release")
        ),
        "license_expression": config["sources"][source_name]["license_expression"],
        "usage_class": config["sources"][source_name]["usage_class"],
        "access_scope": manifest["access_scope"],
        "access_approved_at": manifest.get("access_approved_at"),
        "retrieved_at": manifest["retrieved_at"],
        "file_count": len(manifest["files"]),
        "total_bytes": sum(item["bytes"] for item in manifest["files"]),
        "files": manifest["files"],
        "parquet": parquet_files,
        "parquet_aggregate": parquet_aggregate,
        "standalone_json": json_files,
        "zip_json": zip_files,
        "aggregate_rows": rows + zip_records,
        "parse_failure_count": parse_failures + sum(
            item["json_parse_failure_count"] for item in zip_files
        ),
        "aihub_multiturn_structural_group_count": (
            structural_groups if source_name == "aihub_empathy" else None
        ),
        "aihub_multiturn_structural_group_counts_by_split": (
            {split: len(values) for split, values in group_hashes_by_split.items()}
            if source_name == "aihub_empathy"
            else None
        ),
        "aihub_cross_split_group_overlap_count": (
            len(group_hashes_by_split["train"] & group_hashes_by_split["validation"])
            if source_name == "aihub_empathy"
            else None
        ),
        "aihub_exact_record_cross_split_overlap_count": (
            len(record_hashes_by_split["train"] & record_hashes_by_split["validation"])
            if source_name == "aihub_empathy"
            else None
        ),
    }


def inventory_all(config: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    validation = validate_config(config, repo_root)
    sources = [inventory_source(config, repo_root, name) for name in sorted(EXPECTED_SOURCES)]
    aihub = next(source for source in sources if source["source"] == "aihub_empathy")
    if aihub["status"] == "missing":
        gate_status = "blocked_missing_aihub_86"
    elif aihub["status"] != "collected":
        gate_status = aihub["status"]
    elif any(source["status"] != "collected" for source in sources):
        gate_status = "in_progress_missing_public_source"
    else:
        gate_status = "passed"
    report = {
        "schema_version": "1.0.0",
        "generated_at": utc_now(),
        "canonical_plan_version": config["canonical_plan_version"],
        "contract_validation": validation,
        "phase1_gate_status": gate_status,
        "contains_raw_samples": False,
        "sources": sources,
    }
    report_path = resolve_repo_path(repo_root, config["paths"]["inventory_report"])
    return write_versioned_json_once(report_path, report)


def _verify_manifest(root: Path) -> list[str]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise Phase1Error(f"SOURCE_MANIFEST.json이 없습니다: {root.name}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase1Error(f"SOURCE_MANIFEST.json을 읽을 수 없습니다: {root.name}") from exc
    verified: list[str] = []
    for item in manifest.get("files", []):
        relative = validate_relative_archive_path(item["path"])
        path = root.joinpath(*relative.parts)
        if not path.is_file():
            raise Phase1Error(f"manifest 파일이 없습니다: {root.name}/{item['path']}")
        if path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise Phase1Error(f"manifest bytes/SHA-256 불일치: {root.name}/{item['path']}")
        verified.append(item["path"])
    if not verified:
        raise Phase1Error(f"manifest에 원천 파일이 없습니다: {root.name}")
    return verified


def verify_sources(
    config: dict[str, Any], repo_root: Path, allow_missing_aihub: bool = False
) -> dict[str, Any]:
    validation = validate_config(config, repo_root)
    results: list[dict[str, Any]] = []
    manifest_files: dict[str, list[str]] = {}
    for source_name in sorted(EXPECTED_SOURCES):
        root = source_root(config, repo_root, source_name)
        has_source_files = root.exists() and any(_iter_manifest_files(root))
        if not has_source_files:
            if source_name == "aihub_empathy" and allow_missing_aihub:
                results.append({"source": source_name, "status": "blocked_missing_aihub_86"})
                continue
            raise Phase1Error(f"활성 원천 경로가 없습니다: {source_name}")
        files = _verify_manifest(root)
        manifest_files[source_name] = files
        results.append({"source": source_name, "status": "verified", "file_count": len(files)})

    raw_root = resolve_repo_path(repo_root, config["paths"]["raw_root"])
    for forbidden in ("aihub_271", "aihub_continuous", "271_keti"):
        if (raw_root / forbidden).exists():
            raise Phase1Error(f"금지된 AI Hub #271 원천 경로가 존재합니다: {forbidden}")

    if "bazi_sft" in manifest_files:
        allowed = set(config["sources"]["bazi_sft"]["allow_files"])
        if set(manifest_files["bazi_sft"]) != allowed:
            raise Phase1Error("bazi_sft raw 경로에 allowlist 밖 파일이 있습니다.")
    if "yeji_bazi_rules" in manifest_files:
        source = config["sources"]["yeji_bazi_rules"]
        allowed = set(source["allow_files"])
        allowed.update(
            f"provenance/{source['provenance']['revision']}/{name}"
            for name in source["provenance"]["allow_files"]
        )
        if set(manifest_files["yeji_bazi_rules"]) != allowed:
            raise Phase1Error("YEJI raw 경로에 allowlist 밖 파일이 있습니다.")
        for filename, expected in source["expected_files"].items():
            path = source_root(config, repo_root, "yeji_bazi_rules") / filename
            if path.stat().st_size != expected["bytes"] or sha256_file(path) != expected["sha256"]:
                raise Phase1Error("YEJI 신살 고정 파일 bytes/SHA-256이 다릅니다.")
    if "aihub_empathy" in manifest_files:
        source = config["sources"]["aihub_empathy"]
        expected_keys = set(source["file_keys"])
        files = set(manifest_files["aihub_empathy"])
        for file_key in expected_keys:
            archive = f"archives/filekey-{file_key}.tar"
            if archive not in files:
                raise Phase1Error(f"AI Hub 고정 archive가 없습니다: file key {file_key}")
            zip_prefix = f"extracted/filekey-{file_key}/"
            if not any(path.startswith(zip_prefix) and path.lower().endswith(".zip") for path in files):
                raise Phase1Error(f"AI Hub 추출 zip이 없습니다: file key {file_key}")
        for path in files:
            if path.startswith("archives/filekey-"):
                match = re.fullmatch(r"archives/filekey-(\d+)\.tar", path)
                if match is None or match.group(1) not in expected_keys:
                    raise Phase1Error("AI Hub allowlist 밖 archive가 있습니다.")
            elif path.startswith("extracted/filekey-"):
                match = re.match(r"extracted/filekey-(\d+)/", path)
                if match is None or match.group(1) not in expected_keys:
                    raise Phase1Error("AI Hub allowlist 밖 추출 파일이 있습니다.")
            else:
                raise Phase1Error("AI Hub raw 경로에 허용되지 않은 파일이 있습니다.")
        private_root = source_root(config, repo_root, "aihub_empathy")
        if stat.S_IMODE(private_root.stat().st_mode) & 0o077:
            raise Phase1Error("AI Hub 비공개 raw 디렉터리 권한은 0700이어야 합니다.")

    license_path = resolve_repo_path(repo_root, config["paths"]["license_manifest"])
    try:
        license_manifest = json.loads(license_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Phase1Error("license_manifest.json을 읽을 수 없습니다.") from exc
    active_license_sources = {item["source"] for item in license_manifest.get("sources", [])}
    if active_license_sources != EXPECTED_SOURCES:
        raise Phase1Error("license manifest의 활성 원천 목록이 설정과 다릅니다.")
    excluded = {item["source"]: item for item in license_manifest.get("excluded_sources", [])}
    if excluded.get("aihub_271_keti", {}).get("usage_class") != "contract_required":
        raise Phase1Error("license manifest에서 AI Hub #271 제외 계약을 찾을 수 없습니다.")

    report_path = resolve_repo_path(repo_root, config["paths"]["inventory_report"])
    if report_path.exists() and "aihub_empathy" in manifest_files:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        aihub_report = next(
            item for item in report["sources"] if item["source"] == "aihub_empathy"
        )
        minimum = config["mix_contract"]["aihub_multiturn_min_structural_groups"]
        if aihub_report.get("aihub_multiturn_structural_group_count", 0) < minimum:
            raise Phase1Error(
                f"AI Hub #86 구조적 멀티턴 group이 최소 {minimum}개에 못 미칩니다."
            )

    overall = "verified_with_aihub_block" if allow_missing_aihub and "aihub_empathy" not in manifest_files else "verified"
    return {"status": overall, "contract": validation, "sources": results}
