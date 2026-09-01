# chart_only_security.py - chart-only adapter의 분리 키와 AEAD 세션 저장소를 fail-closed로 관리한다.

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from scripts.runtime.calculation.canonical import canonical_json_bytes

KEY_BYTES = 32
NONCE_BYTES = 12
SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{24}$")
KEY_ID_PATTERN = re.compile(r"^sek1_[0-9a-f]{24}$")
ENVELOPE_SCHEMA = "saju-encrypted-session-envelope-v1.0"
ALGORITHM = "AES-256-GCM"
DEFAULT_RETENTION_SECONDS = 1800
DEFAULT_MAXIMUM_SESSIONS = 100
DEFAULT_MAXIMUM_PLAINTEXT_BYTES = 262_144
DEFAULT_MAXIMUM_ENVELOPE_BYTES = 393_216


class ChartOnlySecurityError(RuntimeError):
    """운영 키 또는 암호화 persistence 계약 위반."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ChartOnlySecurityError(
                f"암호화 session JSON duplicate key를 허용하지 않습니다: {key}"
            )
        result[key] = value
    return result


def _reject_symlink_components(path: Path, label: str) -> None:
    if not path.is_absolute():
        raise ChartOnlySecurityError(f"{label} 경로는 절대경로여야 합니다.")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ChartOnlySecurityError(f"{label} 경로에 symlink가 포함됐습니다.")


def validate_private_directory(path: Path, *, label: str) -> Path:
    """현재 사용자 소유 0700 일반 directory만 허용한다."""

    _reject_symlink_components(path, label)
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ChartOnlySecurityError(f"{label} directory를 읽을 수 없습니다.") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ChartOnlySecurityError(
            f"{label} directory는 현재 사용자 소유의 0700 directory여야 합니다."
        )
    return path.resolve()


def create_private_directory(path: Path, *, label: str) -> Path:
    """존재하지 않는 절대경로에 0700 private directory를 만든다."""

    _reject_symlink_components(path.parent, f"{label} parent")
    if path.exists() or path.is_symlink():
        return validate_private_directory(path, label=label)
    try:
        path.mkdir(mode=0o700, parents=False)
    except OSError as exc:
        raise ChartOnlySecurityError(f"{label} directory를 만들 수 없습니다.") from exc
    return validate_private_directory(path, label=label)


@dataclass(frozen=True)
class SecretKey:
    """검증된 32바이트 key와 비밀 원문을 노출하지 않는 identity."""

    purpose: str
    key_id: str
    path: Path
    device: int
    inode: int
    material: bytes = field(repr=False)


def load_secret_key(path: Path, *, purpose: str) -> SecretKey:
    """symlink·hardlink·권한 alias를 거부하고 32바이트 key를 읽는다."""

    if not isinstance(purpose, str) or not purpose or len(purpose) > 64:
        raise ChartOnlySecurityError("key purpose가 유효하지 않습니다.")
    _reject_symlink_components(path, f"{purpose} key")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ChartOnlySecurityError(f"{purpose} key를 안전하게 열지 못했습니다.") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ChartOnlySecurityError(
                f"{purpose} key는 현재 사용자 소유의 0600 단일-link 일반 파일이어야 합니다."
            )
        material = os.read(descriptor, KEY_BYTES + 1)
        if len(material) != KEY_BYTES or os.read(descriptor, 1):
            raise ChartOnlySecurityError(
                f"{purpose} key는 줄바꿈 없이 정확히 32바이트여야 합니다."
            )
    except OSError as exc:
        raise ChartOnlySecurityError(f"{purpose} key를 읽지 못했습니다.") from exc
    finally:
        os.close(descriptor)
    key_id = "sek1_" + hashlib.sha256(
        b"saju-secret-key-id-v1\x00" + purpose.encode("utf-8") + b"\x00" + material
    ).hexdigest()[:24]
    return SecretKey(
        purpose=purpose,
        key_id=key_id,
        path=path.resolve(),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        material=material,
    )


def create_secret_key(path: Path, *, purpose: str) -> SecretKey:
    """기존 파일을 덮어쓰지 않고 OS 보안 난수 key를 0600으로 만든다."""

    _reject_symlink_components(path, f"{purpose} key")
    validate_private_directory(path.parent, label=f"{purpose} key parent")
    if path.exists() or path.is_symlink():
        raise ChartOnlySecurityError(f"{purpose} key 경로가 이미 존재합니다.")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
    )
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        material = secrets.token_bytes(KEY_BYTES)
        written = 0
        while written < len(material):
            count = os.write(descriptor, material[written:])
            if count <= 0:
                raise OSError("key write returned zero bytes")
            written += count
        os.fsync(descriptor)
    except OSError as exc:
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise ChartOnlySecurityError(f"{purpose} key를 만들지 못했습니다.") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)
    return load_secret_key(path, purpose=purpose)


def assert_key_separation(hmac_key: SecretKey, encryption_key: SecretKey) -> None:
    """HMAC과 persistence key가 같은 file·내용을 쓰지 못하게 한다."""

    if hmac_key.purpose != "runtime-hmac" or encryption_key.purpose != "session-aead":
        raise ChartOnlySecurityError("HMAC·암호화 key purpose가 다릅니다.")
    same_inode = (hmac_key.device, hmac_key.inode) == (
        encryption_key.device,
        encryption_key.inode,
    )
    same_material = hmac.compare_digest(hmac_key.material, encryption_key.material)
    if same_inode or same_material:
        raise ChartOnlySecurityError("HMAC key와 암호화 key는 반드시 분리해야 합니다.")


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: Any, *, label: str) -> bytes:
    if not isinstance(value, str):
        raise ChartOnlySecurityError(f"암호화 envelope {label} 형식이 다릅니다.")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ChartOnlySecurityError(
            f"암호화 envelope {label}가 유효한 base64가 아닙니다."
        ) from exc


def _associated_data(session_id: str, key_id: str) -> bytes:
    return canonical_json_bytes(
        {
            "algorithm": ALGORITHM,
            "key_id": key_id,
            "schema_version": ENVELOPE_SCHEMA,
            "session_id": session_id,
        }
    )


class EncryptedSessionStore:
    """AES-256-GCM으로만 state를 저장하는 bounded session store."""

    def __init__(
        self,
        root: Path,
        *,
        active_key: SecretKey,
        decryption_keys: tuple[SecretKey, ...] = (),
        retention_seconds: int = DEFAULT_RETENTION_SECONDS,
        maximum_sessions: int = DEFAULT_MAXIMUM_SESSIONS,
        maximum_plaintext_bytes: int = DEFAULT_MAXIMUM_PLAINTEXT_BYTES,
        maximum_envelope_bytes: int = DEFAULT_MAXIMUM_ENVELOPE_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = validate_private_directory(root, label="session store")
        if active_key.purpose != "session-aead":
            raise ChartOnlySecurityError("active persistence key purpose가 다릅니다.")
        all_keys = (active_key, *decryption_keys)
        if not 1 <= len(all_keys) <= 2:
            raise ChartOnlySecurityError("persistence decryption key는 최대 2개입니다.")
        if any(key.purpose != "session-aead" for key in all_keys):
            raise ChartOnlySecurityError("persistence key purpose가 다릅니다.")
        key_map = {key.key_id: key for key in all_keys}
        if len(key_map) != len(all_keys):
            raise ChartOnlySecurityError("중복 persistence key를 허용하지 않습니다.")
        if (
            retention_seconds != DEFAULT_RETENTION_SECONDS
            or maximum_sessions != DEFAULT_MAXIMUM_SESSIONS
            or maximum_plaintext_bytes != DEFAULT_MAXIMUM_PLAINTEXT_BYTES
            or maximum_envelope_bytes != DEFAULT_MAXIMUM_ENVELOPE_BYTES
        ):
            raise ChartOnlySecurityError("session store 한도는 고정 운영 계약과 같아야 합니다.")
        self.active_key = active_key
        self._keys = key_map
        self.retention_seconds = retention_seconds
        self.maximum_sessions = maximum_sessions
        self.maximum_plaintext_bytes = maximum_plaintext_bytes
        self.maximum_envelope_bytes = maximum_envelope_bytes
        self._clock = clock

    def _path(self, session_id: str) -> Path:
        if SESSION_ID_PATTERN.fullmatch(session_id) is None:
            raise ChartOnlySecurityError("session ID 형식이 다릅니다.")
        return self.root / f"{session_id}.session"

    def _record_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.root.iterdir():
            if item.is_symlink() or not item.is_file():
                raise ChartOnlySecurityError("session store에 허용되지 않은 entry가 있습니다.")
            if not item.name.endswith(".session") or SESSION_ID_PATTERN.fullmatch(
                item.name.removesuffix(".session")
            ) is None:
                raise ChartOnlySecurityError("session store 파일 이름이 다릅니다.")
            paths.append(item)
        return sorted(paths)

    def _read_bytes(self, path: Path) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ChartOnlySecurityError("암호화 session 파일을 열지 못했습니다.") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or metadata.st_size > self.maximum_envelope_bytes
            ):
                raise ChartOnlySecurityError("암호화 session 파일 권한·크기가 다릅니다.")
            chunks: list[bytes] = []
            remaining = self.maximum_envelope_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
        except OSError as exc:
            raise ChartOnlySecurityError("암호화 session 파일을 읽지 못했습니다.") from exc
        finally:
            os.close(descriptor)
        if len(encoded) > self.maximum_envelope_bytes:
            raise ChartOnlySecurityError("암호화 session envelope가 너무 큽니다.")
        return encoded

    def _decode(self, session_id: str, encoded: bytes) -> tuple[dict[str, Any], str]:
        try:
            envelope = json.loads(encoded, object_pairs_hook=_strict_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChartOnlySecurityError("암호화 session envelope JSON이 손상됐습니다.") from exc
        if not isinstance(envelope, dict) or set(envelope) != {
            "algorithm",
            "ciphertext",
            "key_id",
            "nonce",
            "schema_version",
            "session_id",
        }:
            raise ChartOnlySecurityError("암호화 session envelope field가 다릅니다.")
        key_id = envelope.get("key_id")
        if (
            envelope.get("schema_version") != ENVELOPE_SCHEMA
            or envelope.get("algorithm") != ALGORITHM
            or envelope.get("session_id") != session_id
            or not isinstance(key_id, str)
            or KEY_ID_PATTERN.fullmatch(key_id) is None
        ):
            raise ChartOnlySecurityError("암호화 session envelope identity가 다릅니다.")
        key = self._keys.get(key_id)
        if key is None:
            raise ChartOnlySecurityError("암호화 session key가 현재 keyring에 없습니다.")
        nonce = _b64decode(envelope["nonce"], label="nonce")
        ciphertext = _b64decode(envelope["ciphertext"], label="ciphertext")
        if len(nonce) != NONCE_BYTES or len(ciphertext) < 16:
            raise ChartOnlySecurityError("암호화 session nonce·ciphertext 크기가 다릅니다.")
        try:
            plaintext = AESGCM(key.material).decrypt(
                nonce,
                ciphertext,
                _associated_data(session_id, key_id),
            )
        except InvalidTag as exc:
            raise ChartOnlySecurityError("암호화 session 인증 tag 검증에 실패했습니다.") from exc
        if len(plaintext) > self.maximum_plaintext_bytes:
            raise ChartOnlySecurityError("복호화 session state가 너무 큽니다.")
        try:
            record = json.loads(plaintext, object_pairs_hook=_strict_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChartOnlySecurityError("복호화 session state JSON이 손상됐습니다.") from exc
        if not isinstance(record, dict) or set(record) != {
            "created_at",
            "expires_at",
            "session_id",
            "state",
            "updated_at",
        }:
            raise ChartOnlySecurityError("복호화 session record field가 다릅니다.")
        if record.get("session_id") != session_id or not isinstance(
            record.get("state"), dict
        ):
            raise ChartOnlySecurityError("복호화 session record identity가 다릅니다.")
        for field_name in ("created_at", "updated_at", "expires_at"):
            if not isinstance(record.get(field_name), (int, float)):
                raise ChartOnlySecurityError("session record timestamp가 다릅니다.")
        return record, key_id

    def _atomic_write(self, target: Path, encoded: bytes) -> None:
        if len(encoded) > self.maximum_envelope_bytes:
            raise ChartOnlySecurityError("암호화 session envelope가 너무 큽니다.")
        if target.is_symlink():
            raise ChartOnlySecurityError("session target symlink를 허용하지 않습니다.")
        temporary = self.root / f".tmp-{secrets.token_hex(16)}"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, flags, 0o600)
            written = 0
            while written < len(encoded):
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, target)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ChartOnlySecurityError("암호화 session 파일을 원자적으로 쓰지 못했습니다.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def put(
        self,
        session_id: str,
        state: Mapping[str, Any],
        *,
        created_at: float | None = None,
    ) -> None:
        target = self._path(session_id)
        if not target.exists():
            self.purge_expired()
            if len(self._record_paths()) >= self.maximum_sessions:
                raise ChartOnlySecurityError("암호화 session 최대 개수에 도달했습니다.")
        now = float(self._clock())
        created = now if created_at is None else float(created_at)
        if created > now:
            raise ChartOnlySecurityError("session created_at이 현재보다 미래입니다.")
        record = {
            "created_at": created,
            "expires_at": now + self.retention_seconds,
            "session_id": session_id,
            "state": dict(state),
            "updated_at": now,
        }
        plaintext = canonical_json_bytes(record)
        if len(plaintext) > self.maximum_plaintext_bytes:
            raise ChartOnlySecurityError("session state가 최대 plaintext 크기를 넘었습니다.")
        nonce = secrets.token_bytes(NONCE_BYTES)
        key_id = self.active_key.key_id
        ciphertext = AESGCM(self.active_key.material).encrypt(
            nonce,
            plaintext,
            _associated_data(session_id, key_id),
        )
        envelope = {
            "algorithm": ALGORITHM,
            "ciphertext": _b64encode(ciphertext),
            "key_id": key_id,
            "nonce": _b64encode(nonce),
            "schema_version": ENVELOPE_SCHEMA,
            "session_id": session_id,
        }
        self._atomic_write(target, canonical_json_bytes(envelope))

    def create(self, state: Mapping[str, Any]) -> str:
        self.purge_expired()
        if len(self._record_paths()) >= self.maximum_sessions:
            raise ChartOnlySecurityError("암호화 session 최대 개수에 도달했습니다.")
        session_id = secrets.token_hex(12)
        while self._path(session_id).exists():
            session_id = secrets.token_hex(12)
        self.put(session_id, state)
        return session_id

    def read(self, session_id: str, *, reencrypt: bool = True) -> dict[str, Any]:
        path = self._path(session_id)
        record, key_id = self._decode(session_id, self._read_bytes(path))
        now = float(self._clock())
        if record["expires_at"] <= now:
            self.delete(session_id)
            raise ChartOnlySecurityError("session이 보존 기한을 지나 삭제됐습니다.")
        if reencrypt and key_id != self.active_key.key_id:
            self.put(session_id, record["state"], created_at=record["created_at"])
        return dict(record["state"])

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if path.is_symlink():
            raise ChartOnlySecurityError("session symlink는 삭제 대상으로 허용하지 않습니다.")
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def purge_expired(self) -> int:
        now = float(self._clock())
        deleted = 0
        for path in self._record_paths():
            session_id = path.name.removesuffix(".session")
            record, _ = self._decode(session_id, self._read_bytes(path))
            if record["expires_at"] <= now and self.delete(session_id):
                deleted += 1
        return deleted

    def count(self) -> int:
        self.purge_expired()
        return len(self._record_paths())

    def envelope(self, session_id: str) -> dict[str, Any]:
        """테스트·audit용으로 plaintext를 열지 않고 envelope만 반환한다."""

        try:
            value = json.loads(
                self._read_bytes(self._path(session_id)),
                object_pairs_hook=_strict_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChartOnlySecurityError("암호화 session envelope JSON이 손상됐습니다.") from exc
        if not isinstance(value, dict):
            raise ChartOnlySecurityError("암호화 session envelope 형식이 다릅니다.")
        return value
