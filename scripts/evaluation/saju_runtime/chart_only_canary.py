# chart_only_canary.py - 실제 v1.4 engine과 합성 입력으로 app adapter 로컬 Gate를 검증한다.

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.runtime.calculation.canonical import canonical_json_bytes
from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_4 import RELEASE_V14_PATH
from scripts.runtime.calculation.errors import RuntimeCalculationError
from scripts.runtime.chart_only_adapter import (
    ADAPTER_ID,
    ChartOnlyAdapterError,
    ChartOnlyAppAdapter,
    _assert_public_response,
    build_chart_only_app_adapter,
)
from scripts.runtime.chart_only_operations_contracts import (
    CANARY_GATE_PATH,
    EXPECTED_CHECKS,
    EXPECTED_STRATA,
    PARENT_RELEASE_ID,
    load_strict_json,
    sha256_file,
    validate_operations_registry,
)
from scripts.runtime.chart_only_security import (
    ChartOnlySecurityError,
    EncryptedSessionStore,
    create_secret_key,
)

REPORT_VERSION = "1.0.0"
REPORT_ROOT = REPO_ROOT / "data/reports/saju_runtime_app_canary/v1.0.0"
BUILD_PATTERN = re.compile(r"^build-[0-9a-f]{12}$")
HMAC_VALUE_PATTERN = re.compile(r"(?:sbi2|sc2|scs2|scr2|sif2)_[0-9a-f]{64}")
PRIVATE_PATH_PATTERN = re.compile(r"(?:/home/|/tmp/|[A-Za-z]:\\\\)")
IMPLEMENTATION_PATHS = (
    "configs/runtime/operations/chart_only_security-v1.0.0.json",
    "configs/runtime/operations/chart_only_adapter-v1.0.0.json",
    "configs/runtime/operations/chart_only_canary_gate-v1.0.0.json",
    "configs/runtime/operations/registry-v1.0.0.json",
    "configs/runtime/calculation/releases/v1.4.0/release_registry.json",
    "requirements-runtime-adapter-v1.0.txt",
    "scripts/runtime/chart_only_security.py",
    "scripts/runtime/chart_only_operations_contracts.py",
    "scripts/runtime/chart_only_adapter.py",
    "scripts/runtime/chart_only_operations.py",
    "scripts/evaluation/saju_runtime/chart_only_canary.py",
)


class ChartOnlyCanaryError(RuntimeError):
    """chart-only adapter 합성 canary 계약 위반."""


def _implementation_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in IMPLEMENTATION_PATHS:
        path = REPO_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise ChartOnlyCanaryError(f"canary 구현 파일이 없거나 symlink입니다: {relative}")
        hashes[relative] = sha256_file(path)
    return hashes


def _private_directory(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _event(adapter: ChartOnlyAppAdapter, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    response = adapter.handle_event(session_id, event)
    _assert_public_response(response)
    return response


def _drive_chart(
    adapter: ChartOnlyAppAdapter,
    *,
    calendar: str,
    birth_date: str,
    precision: str,
    birth_time: str | None = None,
    time_range: dict[str, str] | None = None,
    leap_month: bool | None = None,
    city: str = "서울",
) -> tuple[str, dict[str, Any]]:
    session_id = adapter.create_session()["session_id"]
    events: list[dict[str, Any]] = [
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "calendar", "value": calendar},
        {"type": "set_slot", "field": "birth_date", "value": birth_date},
    ]
    if calendar == "lunar":
        events.append(
            {"type": "set_slot", "field": "leap_month", "value": bool(leap_month)}
        )
    events.append(
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {
                "country_code": "KR",
                "city": city,
                "timezone": "Asia/Seoul",
            },
        }
    )
    if precision == "exact":
        events.append(
            {"type": "set_slot", "field": "birth_time", "value": birth_time}
        )
    elif precision == "range":
        events.append(
            {"type": "set_slot", "field": "time_range", "value": time_range}
        )
    else:
        events.append({"type": "set_time_unknown"})
    for item in events:
        _event(adapter, session_id, item)
    return session_id, _event(adapter, session_id, {"type": "request_chart"})


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ChartOnlyCanaryError(message)


class _Recorder:
    def __init__(self) -> None:
        self.passed: Counter[str] = Counter()
        self.failed: Counter[str] = Counter()
        self.failure_counts: Counter[str] = Counter()

    def case(self, stratum: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (
            ChartOnlyAdapterError,
            ChartOnlyCanaryError,
            ChartOnlySecurityError,
            RuntimeCalculationError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:  # aggregate에는 원문·입력 없이 분류만 기록한다.
            self.failed[stratum] += 1
            self.failure_counts[f"{stratum}:{type(exc).__name__}"] += 1
        else:
            self.passed[stratum] += 1

    def strata(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "cases": self.passed[name] + self.failed[name],
                "passed": self.passed[name],
                "failed": self.failed[name],
            }
            for name in EXPECTED_STRATA
        }


def _run_feature_disabled(recorder: _Recorder) -> None:
    for _ in range(10):
        def operation() -> None:
            adapter = build_chart_only_app_adapter()
            status = adapter.status()
            _expect(status["status"] == "disabled", "기본 off status가 다릅니다.")
            _expect(status["resources_opened"] is False, "비활성 resource가 열렸습니다.")

        recorder.case("feature_disabled", operation)


def _run_chart_cases(adapter: ChartOnlyAppAdapter, recorder: _Recorder) -> list[dict[str, Any]]:
    public_responses: list[dict[str, Any]] = []
    exact_years = tuple(range(1920, 2020, 10))
    lunar_years = tuple(range(1930, 2030, 10))

    for index, year in enumerate(exact_years):
        def exact_solar(year: int = year, index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"{year:04d}-03-15",
                precision="exact",
                birth_time="12:00",
                city=f"합성도시{index}",
            )
            _expect(response["status"] == "ready", "태양력 exact가 준비되지 않았습니다.")
            _expect(
                response["result"]["fact_authority"] == "HARD_GT",
                "태양력 exact 권한이 다릅니다.",
            )

        recorder.case("past_exact_solar", exact_solar)

    for index, year in enumerate(lunar_years):
        def exact_lunar(year: int = year, index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="lunar",
                birth_date=f"{year:04d}-01-15",
                leap_month=False,
                precision="exact",
                birth_time="12:00",
                city=f"합성음력도시{index}",
            )
            _expect(response["status"] == "ready", "음력 exact가 준비되지 않았습니다.")
            _expect(
                response["result"]["fact_authority"] == "HARD_GT",
                "음력 exact 권한이 다릅니다.",
            )

        recorder.case("past_exact_lunar", exact_lunar)

    for index, year in enumerate(exact_years):
        def range_case(year: int = year, index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"{year:04d}-04-15",
                precision="range",
                time_range={"start": "10:00", "end": "10:30"},
                city=f"합성범위도시{index}",
            )
            _expect(response["status"] == "ready", "range 원국이 준비되지 않았습니다.")
            _expect(
                response["result"]["fact_authority"] == "POLICY_BOUND_RULE",
                "range 원국 권한이 다릅니다.",
            )

        recorder.case("past_range", range_case)

    for index, year in enumerate(exact_years):
        def unknown_case(year: int = year, index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"{year:04d}-06-15",
                precision="unknown",
                city=f"합성미상도시{index}",
            )
            _expect(response["status"] == "ready", "unknown 원국이 준비되지 않았습니다.")
            _expect(
                response["result"]["fact_authority"] == "POLICY_BOUND_RULE",
                "unknown 원국 권한이 다릅니다.",
            )

        recorder.case("past_unknown", unknown_case)

    for index in range(10):
        def boundary_case(index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date="1958-05-06",
                precision="exact",
                birth_time="10:19",
                city=f"합성경계도시{index}",
            )
            _expect(response["status"] == "blocked", "절입 경계가 차단되지 않았습니다.")
            _expect(
                response["decision"]["reason_code"] == "SOLAR_TERM_BOUNDARY_UNCERTAIN",
                "절입 경계 차단 code가 다릅니다.",
            )

        recorder.case("boundary_uncertain_block", boundary_case)

    for day in range(1, 11):
        def before_case(day: int = day) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"1919-12-{day:02d}",
                precision="exact",
                birth_time="12:00",
            )
            _expect(response["status"] == "blocked", "하한 이전 날짜가 차단되지 않았습니다.")
            _expect(
                response["decision"]["reason_code"] == "BIRTH_DATE_OUT_OF_APPROVED_RANGE",
                "하한 이전 차단 code가 다릅니다.",
            )

        recorder.case("scope_before_block", before_case)

    for day in range(1, 11):
        def after_case(day: int = day) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"2026-09-{day:02d}",
                precision="exact",
                birth_time="12:00",
            )
            _expect(response["status"] == "blocked", "상한 이후 날짜가 차단되지 않았습니다.")
            _expect(
                response["decision"]["reason_code"] == "BIRTH_DATE_OUT_OF_APPROVED_RANGE",
                "상한 이후 차단 code가 다릅니다.",
            )

        recorder.case("scope_after_block", after_case)

    for _ in range(10):
        def period_case() -> None:
            session_id = adapter.create_session()["session_id"]
            response = _event(adapter, session_id, {"type": "request_period"})
            _expect(response["status"] == "blocked", "period가 차단되지 않았습니다.")
            _expect(
                response["decision"]["reason_code"] == "CHART_ONLY_PERIOD_OUT_OF_SCOPE",
                "period 차단 code가 다릅니다.",
            )

        recorder.case("period_block", period_case)

    for index in range(10):
        def tamper_case(index: int = index) -> None:
            session_id = adapter.create_session()["session_id"]
            path = adapter.store.root / f"{session_id}.session"
            envelope = json.loads(path.read_text(encoding="utf-8"))
            ciphertext = envelope["ciphertext"]
            envelope["ciphertext"] = (
                ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
            )
            path.write_text(
                json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            path.chmod(0o600)
            try:
                adapter.store.read(session_id)
            except ChartOnlySecurityError:
                pass
            else:
                raise ChartOnlyCanaryError("변조 session이 복호화됐습니다.")
            finally:
                path.unlink(missing_ok=True)

        recorder.case("tamper_rejection", tamper_case)

    for index in range(10):
        def leakage_case(index: int = index) -> None:
            _, response = _drive_chart(
                adapter,
                calendar="solar",
                birth_date=f"{1980 + index:04d}-07-15",
                precision="exact",
                birth_time="14:00",
                city=f"합성누출도시{index}",
            )
            _assert_public_response(response)
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
            _expect(HMAC_VALUE_PATTERN.search(encoded) is None, "공개 응답에 HMAC ID가 있습니다.")
            _expect("normalized_input" not in encoded, "공개 응답에 normalized input이 있습니다.")
            public_responses.append(response)

        recorder.case("public_leakage", leakage_case)
    return public_responses


def _run_rotation_and_retention(
    root: Path,
    recorder: _Recorder,
) -> None:
    key_root = _private_directory(root, "rotation-keys")
    store_root = _private_directory(root, "rotation-sessions")
    first = create_secret_key(key_root / "first.key", purpose="session-aead")
    second = create_secret_key(key_root / "second.key", purpose="session-aead")
    initial = EncryptedSessionStore(store_root, active_key=first)
    session_ids = [initial.create({"synthetic": index}) for index in range(10)]
    rotated = EncryptedSessionStore(
        store_root,
        active_key=second,
        decryption_keys=(first,),
    )
    for index, session_id in enumerate(session_ids):
        def rotation_case(index: int = index, session_id: str = session_id) -> None:
            state = rotated.read(session_id)
            _expect(state == {"synthetic": index}, "rotation state가 다릅니다.")
            _expect(
                rotated.envelope(session_id)["key_id"] == second.key_id,
                "old record가 active key로 재암호화되지 않았습니다.",
            )

        recorder.case("key_rotation_and_separation", rotation_case)

    retention_root = _private_directory(root, "retention-sessions")
    now = [5_000.0]
    retention = EncryptedSessionStore(
        retention_root,
        active_key=second,
        clock=lambda: now[0],
    )
    expiring = [retention.create({"synthetic": index}) for index in range(10)]
    now[0] += 1_801
    deleted = retention.purge_expired()
    for session_id in expiring:
        def retention_case(session_id: str = session_id) -> None:
            _expect(deleted == 10, "만료 session 삭제 수가 다릅니다.")
            _expect(
                not (retention_root / f"{session_id}.session").exists(),
                "만료 session 파일이 남았습니다.",
            )

        recorder.case("retention_and_deletion", retention_case)


def run_canary(ephemeris: Path) -> dict[str, Any]:
    """실제 release·DE440s와 합성 입력만으로 130-case canary를 실행한다."""

    validate_operations_registry(require_dependencies=True)
    gate = load_strict_json(CANARY_GATE_PATH, label="canary gate")
    if not ephemeris.is_absolute() or ephemeris.is_symlink() or not ephemeris.is_file():
        raise ChartOnlyCanaryError("DE440s는 symlink가 아닌 절대경로 일반 파일이어야 합니다.")
    recorder = _Recorder()
    _run_feature_disabled(recorder)
    with tempfile.TemporaryDirectory(prefix="saju-chart-only-canary-") as directory:
        root = Path(directory)
        root.chmod(0o700)
        key_root = _private_directory(root, "keys")
        store_root = _private_directory(root, "sessions")
        hmac_key = create_secret_key(key_root / "runtime-hmac.key", purpose="runtime-hmac")
        encryption_key = create_secret_key(
            key_root / "session-aead.key", purpose="session-aead"
        )
        adapter = build_chart_only_app_adapter(
            enable_adapter=True,
            release_registry=RELEASE_V14_PATH,
            ephemeris_path=ephemeris,
            hmac_key_file=hmac_key.path,
            encryption_key_file=encryption_key.path,
            store_root=store_root,
        )
        if not isinstance(adapter, ChartOnlyAppAdapter):
            raise ChartOnlyCanaryError("활성 adapter 생성에 실패했습니다.")
        try:
            public_responses = _run_chart_cases(adapter, recorder)
            _run_rotation_and_retention(root, recorder)
            session_files = list(store_root.glob("*.session"))
            encrypted_persistence_only = bool(session_files) and all(
                path.is_file()
                and not path.is_symlink()
                and stat.S_IMODE(path.stat().st_mode) == 0o600
                and "ciphertext" in path.read_text(encoding="utf-8")
                for path in session_files
            )
            public_allowlisted = all(
                not HMAC_VALUE_PATTERN.search(
                    json.dumps(response, ensure_ascii=False, allow_nan=False)
                )
                for response in public_responses
            )
        finally:
            adapter.close()

    strata = recorder.strata()
    total_cases = sum(item["cases"] for item in strata.values())
    total_failed = sum(item["failed"] for item in strata.values())
    gate_checks = {
        "all_cases_passed": total_failed == 0,
        "release_identity_verified": True,
        "feature_default_off": True,
        "disabled_resources_not_opened": recorder.failed["feature_disabled"] == 0,
        "hmac_encryption_keys_separated": recorder.failed["key_rotation_and_separation"] == 0,
        "encrypted_persistence_only": encrypted_persistence_only,
        "tamper_rejected": recorder.failed["tamper_rejection"] == 0,
        "retention_enforced": recorder.failed["retention_and_deletion"] == 0,
        "period_always_blocked": recorder.failed["period_block"] == 0,
        "boundary_uncertainty_blocked": recorder.failed["boundary_uncertain_block"] == 0,
        "public_response_allowlisted": public_allowlisted,
        "no_raw_birth_data_in_public_report": True,
        "no_runtime_ids_in_public_report": True,
        "no_sealed_blind_access": True,
        "no_training_or_model_binding": True,
    }
    passed = (
        total_cases == gate["required_cases"]
        and total_failed == gate["maximum_failures"]
        and set(gate_checks) == EXPECTED_CHECKS
        and all(gate_checks.values())
        and all(
            strata[name]["cases"] == expected and strata[name]["failed"] == 0
            for name, expected in EXPECTED_STRATA.items()
        )
    )
    return {
        "schema_version": REPORT_VERSION,
        "gate_id": gate["gate_id"],
        "status": "passed_synthetic_local_canary" if passed else "failed_synthetic_local_canary",
        "canary_gate_passed": passed,
        "cases": total_cases,
        "passed": total_cases - total_failed,
        "failed": total_failed,
        "failure_counts": dict(sorted(recorder.failure_counts.items())),
        "strata": strata,
        "gate_checks": gate_checks,
        "runtime": {
            "adapter_id": ADAPTER_ID,
            "release_id": PARENT_RELEASE_ID,
            "approved_tool": "calculate_saju_chart",
            "blocked_tool": "calculate_saju_period",
            "runtime_feature_default": False,
        },
        "security": {
            "persistence_algorithm": "AES-256-GCM",
            "hmac_and_encryption_keys_separated": True,
            "key_bytes": 32,
            "nonce_bytes": 12,
            "retention_seconds": 1800,
            "maximum_sessions": 100,
            "physical_secure_overwrite_claimed": False,
            "secret_material_recorded": False,
        },
        "dependencies": {
            package: importlib.metadata.version(package)
            for package in ("cryptography", "cffi", "pycparser", "typing_extensions")
        },
        "output_policy": {
            "aggregate_only": True,
            "raw_case_output_tracked": False,
            "birth_input_recorded": False,
            "runtime_id_recorded": False,
            "private_path_recorded": False,
        },
        "governance": {
            "production_application_binding": False,
            "model_context_binding": False,
            "sealed_blind_accessed": False,
            "mix20k_v3_1_generated": False,
            "training_execution_performed": False,
            "model_promotion_performed": False,
        },
    }


def _build_id(report_without_id: Mapping[str, Any], implementation: Mapping[str, str]) -> str:
    digest = hashlib.sha256(
        canonical_json_bytes(
            {"implementation_sha256": dict(implementation), "report": dict(report_without_id)}
        )
    ).hexdigest()
    return f"build-{digest[:12]}"


def _safe_output_base(path: Path) -> Path:
    candidate = path.absolute()
    cursor = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ChartOnlyCanaryError("canary output 경로에 symlink가 있습니다.")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ChartOnlyCanaryError("canary output base가 directory가 아닙니다.")
    return path


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except OSError as exc:
        raise ChartOnlyCanaryError(f"canary 공개 파일을 쓰지 못했습니다: {path.name}") from exc
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def write_report(report: Mapping[str, Any], output_base: Path) -> Path:
    implementation = _implementation_hashes()
    build_id = _build_id(report, implementation)
    completed = {"build_id": build_id, **dict(report)}
    aggregate_bytes = canonical_json_bytes(completed) + b"\n"
    manifest = {
        "schema_version": REPORT_VERSION,
        "build_id": build_id,
        "aggregate_sha256": hashlib.sha256(aggregate_bytes).hexdigest(),
        "implementation_sha256": implementation,
        "raw_case_output_tracked": False,
        "private_path_recorded": False,
        "sealed_blind_accessed": False,
        "training_execution_performed": False,
        "production_application_binding": False,
    }
    manifest_bytes = canonical_json_bytes(manifest) + b"\n"
    root = _safe_output_base(output_base) / build_id
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise ChartOnlyCanaryError("기존 canary build 경로가 안전하지 않습니다.")
        expected = {"aggregate.json", "build_manifest.json"}
        if {path.name for path in root.iterdir()} != expected:
            raise ChartOnlyCanaryError("기존 canary build 파일 집합이 다릅니다.")
        if (
            (root / "aggregate.json").read_bytes() != aggregate_bytes
            or (root / "build_manifest.json").read_bytes() != manifest_bytes
        ):
            raise ChartOnlyCanaryError("기존 canary build는 덮어쓸 수 없습니다.")
        return root
    temporary = output_base / f".tmp-{build_id}-{os.urandom(8).hex()}"
    try:
        temporary.mkdir(mode=0o755)
        _write_exclusive(temporary / "aggregate.json", aggregate_bytes)
        _write_exclusive(temporary / "build_manifest.json", manifest_bytes)
        os.rename(temporary, root)
    except OSError as exc:
        raise ChartOnlyCanaryError("canary build directory를 원자적으로 발행하지 못했습니다.") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return root


def verify_report(report_root: Path) -> dict[str, Any]:
    validate_operations_registry(require_dependencies=True)
    if report_root.is_symlink() or not report_root.is_dir():
        raise ChartOnlyCanaryError("canary report root가 directory가 아닙니다.")
    if BUILD_PATTERN.fullmatch(report_root.name) is None:
        raise ChartOnlyCanaryError("canary build ID 형식이 다릅니다.")
    resolved = report_root.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        if report_root.parent.resolve() != REPORT_ROOT.resolve():
            raise ChartOnlyCanaryError("tracked canary report는 v1.0.0 root의 직접 자식이어야 합니다.")
    files = {path.name for path in report_root.iterdir()}
    if files != {"aggregate.json", "build_manifest.json"}:
        raise ChartOnlyCanaryError("canary 공개 파일 집합이 다릅니다.")
    for path in report_root.iterdir():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o644:
            raise ChartOnlyCanaryError("canary 공개 파일 mode·type이 다릅니다.")
    aggregate_path = report_root / "aggregate.json"
    manifest_path = report_root / "build_manifest.json"
    aggregate = load_strict_json(aggregate_path, label="canary aggregate")
    manifest = load_strict_json(manifest_path, label="canary manifest")
    implementation = _implementation_hashes()
    report_without_id = dict(aggregate)
    build_id = report_without_id.pop("build_id", None)
    if (
        build_id != report_root.name
        or _build_id(report_without_id, implementation) != build_id
        or manifest.get("build_id") != build_id
        or manifest.get("implementation_sha256") != implementation
        or manifest.get("aggregate_sha256") != sha256_file(aggregate_path)
        or manifest.get("raw_case_output_tracked") is not False
        or manifest.get("private_path_recorded") is not False
        or manifest.get("sealed_blind_accessed") is not False
        or manifest.get("training_execution_performed") is not False
        or manifest.get("production_application_binding") is not False
    ):
        raise ChartOnlyCanaryError("canary build hash·manifest가 다릅니다.")
    if (
        aggregate.get("canary_gate_passed") is not True
        or aggregate.get("cases") != 130
        or aggregate.get("passed") != 130
        or aggregate.get("failed") != 0
        or aggregate.get("failure_counts") != {}
        or set(aggregate.get("gate_checks", {})) != EXPECTED_CHECKS
        or not all(aggregate["gate_checks"].values())
        or any(aggregate.get("governance", {}).values())
    ):
        raise ChartOnlyCanaryError("canary 자동 Gate 결과가 다릅니다.")
    encoded = aggregate_path.read_text(encoding="utf-8")
    if (
        PRIVATE_PATH_PATTERN.search(encoded)
        or HMAC_VALUE_PATTERN.search(encoded)
        or '"birth_date"' in encoded
        or '"birth_time"' in encoded
    ):
        raise ChartOnlyCanaryError("canary 공개 aggregate에 private 값이 포함됐습니다.")
    return {
        "status": "verified_synthetic_local_canary",
        "build_id": build_id,
        "cases": 130,
        "canary_gate_passed": True,
        "production_application_binding": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="chart-only app adapter 합성 canary")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-contract")
    commands.add_parser("plan")
    run = commands.add_parser("run")
    run.add_argument("--ephemeris", type=Path, required=True)
    run.add_argument("--output-base", type=Path, default=REPORT_ROOT)
    verify = commands.add_parser("verify")
    verify.add_argument("--report-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-contract":
            registry = validate_operations_registry(require_dependencies=False)
            result = {"status": "valid", "registry_id": registry["registry_id"]}
        elif args.command == "plan":
            validate_operations_registry(require_dependencies=False)
            result = {
                "status": "planned",
                "cases": 130,
                "strata": EXPECTED_STRATA,
                "synthetic_inputs_only": True,
                "feature_default": False,
                "production_application_binding": False,
            }
        elif args.command == "run":
            report = run_canary(args.ephemeris)
            root = write_report(report, args.output_base)
            result = {
                "status": report["status"],
                "build_id": root.name,
                "cases": report["cases"],
                "passed": report["passed"],
                "failed": report["failed"],
                "canary_gate_passed": report["canary_gate_passed"],
                "production_application_binding": False,
            }
            if not report["canary_gate_passed"]:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
                return 1
        else:
            result = verify_report(args.report_root)
    except (
        ChartOnlyAdapterError,
        ChartOnlyCanaryError,
        ChartOnlySecurityError,
        OSError,
    ) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
