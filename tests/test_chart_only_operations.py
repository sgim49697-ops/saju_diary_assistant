# test_chart_only_operations.py - 운영 키·AEAD persistence·v1.4 adapter 경계를 검증한다.

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_4 import RELEASE_V14_PATH
from scripts.runtime.chart_only_adapter import (
    ChartOnlyAdapterError,
    adapter_plan,
    build_chart_only_app_adapter,
)
from scripts.runtime.chart_only_operations_contracts import (
    validate_operations_registry,
)
from scripts.runtime.chart_only_security import (
    ChartOnlySecurityError,
    EncryptedSessionStore,
    assert_key_separation,
    create_secret_key,
    load_secret_key,
)


def _private_directory(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _set_exact_solar(adapter, session_id: str, birth_date: str, birth_time: str) -> None:
    events = (
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "calendar", "value": "solar"},
        {"type": "set_slot", "field": "birth_date", "value": birth_date},
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {
                "country_code": "KR",
                "city": "서울",
                "timezone": "Asia/Seoul",
            },
        },
        {"type": "set_slot", "field": "birth_time", "value": birth_time},
    )
    for event in events:
        adapter.handle_event(session_id, event)


class ChartOnlyOperationsContractTest(unittest.TestCase):
    def test_registry_and_disabled_plan_are_valid(self) -> None:
        registry = validate_operations_registry(require_dependencies=True)
        self.assertEqual(
            registry["registry_id"], "saju-chart-only-operations-registry-v1.0.0"
        )
        self.assertEqual(adapter_plan()["status"], "planned_feature_disabled")
        disabled = build_chart_only_app_adapter()
        self.assertFalse(disabled.resources_opened)
        self.assertEqual(disabled.status()["status"], "disabled")
        with self.assertRaisesRegex(ChartOnlyAdapterError, "비활성"):
            build_chart_only_app_adapter(
                enable_adapter=False,
                hmac_key_file=Path("/not/opened"),
            )

    def test_key_files_are_separate_owned_0600_single_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-key-policy-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            key_root = _private_directory(root, "keys")
            hmac_key = create_secret_key(key_root / "hmac.key", purpose="runtime-hmac")
            encryption_key = create_secret_key(
                key_root / "session.key", purpose="session-aead"
            )
            assert_key_separation(hmac_key, encryption_key)

            duplicate_path = key_root / "duplicate.key"
            duplicate_path.write_bytes(hmac_key.material)
            duplicate_path.chmod(0o600)
            duplicate_key = load_secret_key(duplicate_path, purpose="session-aead")
            with self.assertRaisesRegex(ChartOnlySecurityError, "분리"):
                assert_key_separation(hmac_key, duplicate_key)

            weak_path = key_root / "weak.key"
            weak_path.write_bytes(os.urandom(32))
            weak_path.chmod(0o644)
            with self.assertRaisesRegex(ChartOnlySecurityError, "0600"):
                load_secret_key(weak_path, purpose="session-aead")

            hardlink_source = key_root / "hardlink-source.key"
            hardlink_source.write_bytes(os.urandom(32))
            hardlink_source.chmod(0o600)
            os.link(hardlink_source, key_root / "hardlink-alias.key")
            with self.assertRaisesRegex(ChartOnlySecurityError, "단일-link"):
                load_secret_key(hardlink_source, purpose="session-aead")


class EncryptedSessionStoreTest(unittest.TestCase):
    def test_encryption_tamper_retention_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-encrypted-store-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            key_root = _private_directory(root, "keys")
            store_root = _private_directory(root, "sessions")
            first = create_secret_key(key_root / "first.key", purpose="session-aead")
            second = create_secret_key(key_root / "second.key", purpose="session-aead")
            now = [1_000.0]
            first_store = EncryptedSessionStore(
                store_root, active_key=first, clock=lambda: now[0]
            )
            raw_state = {
                "birth_date": "1992-04-18",
                "birth_time": "08:30",
                "city": "서울",
            }
            session_id = first_store.create(raw_state)
            path = store_root / f"{session_id}.session"
            encoded = path.read_text(encoding="utf-8")
            self.assertNotIn("1992-04-18", encoded)
            self.assertNotIn("08:30", encoded)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(first_store.read(session_id), raw_state)

            rotated = EncryptedSessionStore(
                store_root,
                active_key=second,
                decryption_keys=(first,),
                clock=lambda: now[0],
            )
            self.assertEqual(rotated.read(session_id), raw_state)
            self.assertEqual(rotated.envelope(session_id)["key_id"], second.key_id)

            envelope = rotated.envelope(session_id)
            ciphertext = envelope["ciphertext"]
            envelope["ciphertext"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
            path.write_text(
                json.dumps(envelope, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(ChartOnlySecurityError, "tag"):
                rotated.read(session_id)

            path.unlink()
            expiring = rotated.create({"state": "short-lived"})
            now[0] += 1_801
            self.assertEqual(rotated.count(), 0)
            self.assertFalse((store_root / f"{expiring}.session").exists())


_DEFAULT_EPHEMERIS = (
    REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
)
_INTEGRATION_EPHEMERIS = Path(
    os.environ.get("SAJU_RUNTIME_TEST_DE440S", str(_DEFAULT_EPHEMERIS))
)


@unittest.skipUnless(
    _INTEGRATION_EPHEMERIS.is_file(), "로컬 Git 제외 DE440s가 필요합니다."
)
class ChartOnlyAppAdapterIntegrationTest(unittest.TestCase):
    def test_real_adapter_projects_only_public_chart_facts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-adapter-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            key_root = _private_directory(root, "keys")
            store_root = _private_directory(root, "sessions")
            hmac_key = create_secret_key(key_root / "hmac.key", purpose="runtime-hmac")
            encryption_key = create_secret_key(
                key_root / "session.key", purpose="session-aead"
            )
            with build_chart_only_app_adapter(
                enable_adapter=True,
                release_registry=RELEASE_V14_PATH,
                ephemeris_path=_INTEGRATION_EPHEMERIS,
                hmac_key_file=hmac_key.path,
                encryption_key_file=encryption_key.path,
                store_root=store_root,
            ) as adapter:
                created = adapter.create_session()
                session_id = created["session_id"]
                _set_exact_solar(adapter, session_id, "1964-09-07", "23:59")
                response = adapter.handle_event(session_id, {"type": "request_chart"})
                self.assertEqual(response["status"], "ready")
                self.assertEqual(response["result"]["fact_authority"], "HARD_GT")
                public = json.dumps(response, ensure_ascii=False)
                for forbidden in (
                    "birth_input_id",
                    "chart_id",
                    "chart_set_id",
                    "calculation_run_id",
                    "internal_trace",
                ):
                    self.assertNotIn(forbidden, public)
                envelope = json.dumps(adapter.store.envelope(session_id))
                self.assertNotIn("1964-09-07", envelope)
                self.assertNotIn("23:59", envelope)

                corrected = adapter.handle_event(
                    session_id,
                    {"type": "correct_slot", "field": "birth_time", "value": "22:00"},
                )
                self.assertEqual(corrected["status"], "needs_input")
                self.assertEqual(
                    corrected["decision"]["reason_code"], "CHART_REQUEST_REQUIRED"
                )
                recalculated = adapter.handle_event(
                    session_id, {"type": "request_chart"}
                )
                self.assertEqual(recalculated["status"], "ready")

                period = adapter.handle_event(session_id, {"type": "request_period"})
                self.assertEqual(period["status"], "blocked")
                self.assertEqual(
                    period["decision"]["reason_code"],
                    "CHART_ONLY_PERIOD_OUT_OF_SCOPE",
                )

    def test_real_adapter_preserves_scope_and_boundary_blocks(self) -> None:
        cases = (
            ("1920-01-06", "23:59", "BIRTH_DATE_OUT_OF_APPROVED_RANGE"),
            ("2026-09-01", "00:00", "BIRTH_DATE_OUT_OF_APPROVED_RANGE"),
            ("1958-05-06", "10:19", "SOLAR_TERM_BOUNDARY_UNCERTAIN"),
        )
        with tempfile.TemporaryDirectory(prefix="saju-adapter-boundary-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            key_root = _private_directory(root, "keys")
            store_root = _private_directory(root, "sessions")
            hmac_key = create_secret_key(key_root / "hmac.key", purpose="runtime-hmac")
            encryption_key = create_secret_key(
                key_root / "session.key", purpose="session-aead"
            )
            with build_chart_only_app_adapter(
                enable_adapter=True,
                release_registry=RELEASE_V14_PATH,
                ephemeris_path=_INTEGRATION_EPHEMERIS,
                hmac_key_file=hmac_key.path,
                encryption_key_file=encryption_key.path,
                store_root=store_root,
            ) as adapter:
                for birth_date, birth_time, expected_code in cases:
                    with self.subTest(birth_date=birth_date, birth_time=birth_time):
                        session_id = adapter.create_session()["session_id"]
                        _set_exact_solar(adapter, session_id, birth_date, birth_time)
                        response = adapter.handle_event(
                            session_id, {"type": "request_chart"}
                        )
                        self.assertEqual(response["status"], "blocked")
                        self.assertEqual(
                            response["decision"]["reason_code"], expected_code
                        )
