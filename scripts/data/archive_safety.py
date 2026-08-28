# archive_safety.py - tar·zip 경로 탈출과 특수 member를 차단해 안전하게 처리한다.

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from scripts.data.errors import Phase1Error

HASH_CHUNK_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
MAX_ARCHIVE_EXPANDED_BYTES = 200 * 1024 * 1024 * 1024


def validate_relative_archive_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path_text = normalized.removesuffix("/")
    segments = path_text.split("/")
    if (
        not path_text
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise Phase1Error(f"archive 내부 경로가 안전하지 않습니다: {name!r}")
    return PurePosixPath(path_text)


def _sha256_stream(stream: object) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(HASH_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _remove_temporary_path(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _validate_existing_tar_extraction(
    archive: tarfile.TarFile,
    validated: list[tuple[tarfile.TarInfo, PurePosixPath]],
    destination: Path,
) -> list[str]:
    if destination.is_symlink() or not destination.is_dir():
        raise Phase1Error("기존 tar 해제 경로는 symlink가 아닌 디렉터리여야 합니다.")
    expected = {
        relative.as_posix(): member
        for member, relative in validated
        if member.isfile()
    }
    actual: dict[str, Path] = {}
    for path in sorted(destination.rglob("*")):
        if path.is_symlink():
            raise Phase1Error("tar 해제 결과에 symlink가 있습니다.")
        if path.is_dir():
            continue
        if not path.is_file() or not path.resolve().is_relative_to(destination.resolve()):
            raise Phase1Error("tar 해제 결과에 허용하지 않은 파일이 있습니다.")
        actual[path.relative_to(destination).as_posix()] = path
    if set(actual) != set(expected):
        raise Phase1Error("기존 tar 해제 결과가 archive member 목록과 다릅니다.")
    for relative, member in expected.items():
        target = actual[relative]
        if target.stat().st_size != member.size:
            raise Phase1Error(f"기존 tar 해제 파일 크기가 다릅니다: {relative}")
        source = archive.extractfile(member)
        if source is None:
            raise Phase1Error(f"tar member를 읽을 수 없습니다: {member.name!r}")
        with source, target.open("rb") as current:
            if _sha256_stream(source) != _sha256_stream(current):
                raise Phase1Error(f"기존 tar 해제 파일 SHA-256이 다릅니다: {relative}")
    return sorted(actual)


def safe_extract_tar(archive_path: Path, destination: Path) -> list[str]:
    """tar를 임시 디렉터리에 검증·추출하고 안전한 member 이름만 반환한다."""
    temporary = destination.with_name(f".{destination.name}.extracting")
    _remove_temporary_path(temporary)
    temporary.mkdir(parents=True)
    try:
        try:
            with tarfile.open(archive_path, mode="r:*") as archive:
                members = archive.getmembers()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise Phase1Error("tar member 수가 안전 한도를 넘었습니다.")
                expanded_bytes = sum(
                    member.size for member in members if member.isfile()
                )
                if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                    raise Phase1Error("tar 해제 예상 크기가 안전 한도를 넘었습니다.")
                validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
                normalized_names: set[str] = set()
                for member in members:
                    relative = validate_relative_archive_path(member.name)
                    normalized = relative.as_posix()
                    if normalized in normalized_names:
                        raise Phase1Error(
                            f"tar에 중복 member 경로가 있습니다: {member.name!r}"
                        )
                    normalized_names.add(normalized)
                    if (
                        member.issym()
                        or member.islnk()
                        or member.isdev()
                        or member.isfifo()
                    ):
                        raise Phase1Error(
                            f"tar link/device member는 허용하지 않습니다: {member.name!r}"
                        )
                    if not (member.isdir() or member.isfile()):
                        raise Phase1Error(
                            f"지원하지 않는 tar member입니다: {member.name!r}"
                        )
                    validated.append((member, relative))

                if destination.exists() or destination.is_symlink():
                    return _validate_existing_tar_extraction(
                        archive, validated, destination
                    )

                for member, relative in validated:
                    target = temporary.joinpath(*relative.parts)
                    resolved = target.resolve()
                    if not resolved.is_relative_to(temporary.resolve()):
                        raise Phase1Error("tar member가 대상 디렉터리를 벗어납니다.")
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        raise Phase1Error(
                            f"tar member를 읽을 수 없습니다: {member.name!r}"
                        )
                    with source, target.open("xb") as stream:
                        shutil.copyfileobj(source, stream, length=HASH_CHUNK_BYTES)
                    os.chmod(target, 0o600)
        except (tarfile.TarError, OSError) as exc:
            raise Phase1Error(
                f"tar archive를 안전하게 처리할 수 없습니다: {archive_path.name}"
            ) from exc
        os.replace(temporary, destination)
    finally:
        _remove_temporary_path(temporary)
    return [
        path.relative_to(destination).as_posix()
        for path in sorted(destination.rglob("*"))
        if path.is_file()
    ]


def validate_zip_paths(zip_path: Path) -> list[str]:
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise Phase1Error(f"유효한 zip archive가 아닙니다: {zip_path.name}") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_MEMBERS:
            raise Phase1Error("zip member 수가 안전 한도를 넘었습니다.")
        if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_EXPANDED_BYTES:
            raise Phase1Error("zip 해제 예상 크기가 안전 한도를 넘었습니다.")
        names: list[str] = []
        normalized_names: set[str] = set()
        for entry in entries:
            relative = validate_relative_archive_path(entry.filename)
            normalized = relative.as_posix()
            if normalized in normalized_names:
                raise Phase1Error(
                    f"zip에 중복 member 경로가 있습니다: {entry.filename!r}"
                )
            normalized_names.add(normalized)
            if entry.flag_bits & 0x1:
                raise Phase1Error(f"암호화된 zip member는 허용하지 않습니다: {entry.filename!r}")
            unix_mode = (entry.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_ISLNK(unix_mode):
                raise Phase1Error(
                    f"zip symlink member는 허용하지 않습니다: {entry.filename!r}"
                )
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise Phase1Error(
                    f"zip special member는 허용하지 않습니다: {entry.filename!r}"
                )
            names.append(entry.filename)
        return names


def _sha256_concatenated(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise Phase1Error(f"zip part는 symlink가 아닌 일반 파일이어야 합니다: {path.name}")
        with path.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_BYTES):
                digest.update(chunk)
    return digest.hexdigest()


def merge_zip_parts(extracted_root: Path) -> list[str]:
    groups: dict[Path, list[tuple[int, Path]]] = {}
    pattern = re.compile(r"^(?P<base>.+\.zip)\.part(?P<number>\d+)$", re.IGNORECASE)
    for path in extracted_root.rglob("*"):
        if not path.is_file():
            continue
        match = pattern.match(path.name)
        if not match:
            continue
        target = path.with_name(match.group("base"))
        groups.setdefault(target, []).append((int(match.group("number")), path))

    merged: list[str] = []
    for target, numbered_parts in sorted(groups.items(), key=lambda item: str(item[0])):
        numbered_parts.sort(key=lambda item: item[0])
        part_paths = [path for _, path in numbered_parts]
        _sha256_concatenated(part_paths)
        numbers = [number for number, _ in numbered_parts]
        start = numbers[0]
        if start not in {0, 1} or numbers != list(range(start, start + len(numbers))):
            raise Phase1Error(f"zip part 번호가 연속적이지 않습니다: {target.name}")
        if not target.exists():
            temporary = target.with_name(f".{target.name}.merging")
            try:
                with temporary.open("wb") as output:
                    for _, part in numbered_parts:
                        with part.open("rb") as source:
                            shutil.copyfileobj(source, output, length=HASH_CHUNK_BYTES)
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        elif target.is_symlink() or not target.is_file():
            raise Phase1Error(f"병합 zip은 symlink가 아닌 일반 파일이어야 합니다: {target.name}")
        if _sha256_concatenated(part_paths) != _sha256_concatenated([target]):
            raise Phase1Error(f"병합 zip 내용이 part 연결과 다릅니다: {target.name}")
        validate_zip_paths(target)
        merged.append(target.relative_to(extracted_root).as_posix())

    for zip_path in extracted_root.rglob("*.zip"):
        validate_zip_paths(zip_path)
        relative = zip_path.relative_to(extracted_root).as_posix()
        if relative not in merged:
            merged.append(relative)
    return sorted(merged)
