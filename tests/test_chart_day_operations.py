# test_chart_day_operations.py - v1.5 암호화 adapter·dashboard binding 경계를 검증한다.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.runtime.calculation.contracts import REPO_ROOT
from scripts.runtime.calculation.contracts_v1_5 import RELEASE_V15_PATH
from scripts.runtime.chart_day_adapter import (
    ChartDayAdapterError,
    build_chart_day_app_adapter,
)
from scripts.runtime.chart_day_dashboard_binding import (
    BINDING_ID,
    ChartDayDashboardBinding,
    ChartDayDashboardBindingError,
)
from scripts.runtime.chart_only_security import create_secret_key

EPHEMERIS = REPO_ROOT / "data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"


def _private_directory(parent: Path, name: str) -> Path:
    path = parent / name
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _events(*, precision: str = "exact") -> list[dict[str, object]]:
    values: list[dict[str, object]] = [
        {"type": "opt_in", "accepted": True},
        {"type": "set_slot", "field": "calendar", "value": "solar"},
        {"type": "set_slot", "field": "birth_date", "value": "1990-01-01"},
        {
            "type": "set_slot",
            "field": "birthplace",
            "value": {
                "country_code": "KR",
                "city": "서울",
                "timezone": "Asia/Seoul",
            },
        },
    ]
    if precision == "exact":
        values.append(
            {"type": "set_slot", "field": "birth_time", "value": "12:00"}
        )
    else:
        values.append(
            {
                "type": "set_slot",
                "field": "time_range",
                "value": {"start": "11:00", "end": "13:00"},
            }
        )
    values.append({"type": "request_chart"})
    return values


def _period_event(target: str = "2026-09-02") -> dict[str, object]:
    return {
        "type": "request_period",
        "request": {
            "period_type": "day",
            "start_date": target,
            "end_date": target,
            "timezone": "Asia/Seoul",
        },
    }


class ChartDayDisabledTests(unittest.TestCase):
    def test_disabled_adapter_opens_no_resources_and_rejects_resource_paths(self) -> None:
        disabled = build_chart_day_app_adapter()
        self.assertFalse(disabled.resources_opened)
        self.assertEqual(disabled.status()["status"], "disabled")
        with self.assertRaisesRegex(ChartDayAdapterError, "비활성"):
            build_chart_day_app_adapter(
                enable_adapter=False,
                hmac_key_file=Path("/not/opened"),
            )


@unittest.skipUnless(EPHEMERIS.is_file(), "로컬 Git 제외 DE440s가 필요합니다.")
class ChartDayIntegrationTests(unittest.TestCase):
    def _resources(self, root: Path) -> dict[str, Path]:
        key_root = _private_directory(root, "keys")
        store_root = _private_directory(root, "sessions")
        hmac_key = create_secret_key(key_root / "hmac.key", purpose="runtime-hmac")
        encryption_key = create_secret_key(
            key_root / "session.key", purpose="session-aead"
        )
        return {
            "hmac": hmac_key.path,
            "encryption": encryption_key.path,
            "store": store_root,
        }

    def test_exact_chart_and_single_day_are_encrypted_and_publicly_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-chart-day-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            resources = self._resources(root)
            with build_chart_day_app_adapter(
                enable_adapter=True,
                release_registry=RELEASE_V15_PATH,
                ephemeris_path=EPHEMERIS,
                hmac_key_file=resources["hmac"],
                encryption_key_file=resources["encryption"],
                store_root=resources["store"],
            ) as adapter:
                session_id = adapter.create_session()["session_id"]
                response = None
                for event in _events():
                    response = adapter.handle_event(session_id, event)
                assert response is not None
                self.assertEqual(response["status"], "ready")
                self.assertEqual(response["result"]["chart"]["fact_authority"], "HARD_GT")
                self.assertIsNone(response["result"]["period"])

                response = adapter.handle_event(session_id, _period_event())
                self.assertEqual(response["status"], "ready")
                facts = response["result"]["period"]["hard_facts"]
                self.assertEqual(facts["period"]["target_date"], "2026-09-02")
                self.assertEqual(facts["period"]["evaluation_local_time"], "12:00")
                self.assertFalse(
                    facts["day_assignment_evidence"]["future_physical_instant_claimed"]
                )
                public = json.dumps(response, ensure_ascii=False)
                for forbidden in (
                    "birth_input_id",
                    "birth_date",
                    "birth_time",
                    "chart_id",
                    "calculation_run_id",
                    "internal_trace",
                ):
                    self.assertNotIn(forbidden, public)
                envelope = json.dumps(adapter.store.envelope(session_id))
                self.assertNotIn("1990-01-01", envelope)
                self.assertNotIn("2026-09-02", envelope)

                changed = adapter.handle_event(session_id, _period_event("2030-01-01"))
                self.assertEqual(
                    changed["result"]["period"]["hard_facts"]["period"]["target_date"],
                    "2030-01-01",
                )

    def test_range_chart_and_malformed_period_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-chart-day-block-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            resources = self._resources(root)
            with build_chart_day_app_adapter(
                enable_adapter=True,
                release_registry=RELEASE_V15_PATH,
                ephemeris_path=EPHEMERIS,
                hmac_key_file=resources["hmac"],
                encryption_key_file=resources["encryption"],
                store_root=resources["store"],
            ) as adapter:
                session_id = adapter.create_session()["session_id"]
                for event in _events(precision="range"):
                    response = adapter.handle_event(session_id, event)
                self.assertEqual(response["result"]["chart"]["status"], "partial")
                blocked = adapter.handle_event(session_id, _period_event())
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(
                    blocked["decision"]["reason_code"], "EXACT_CHART_REQUIRED"
                )
                with self.assertRaisesRegex(ChartDayAdapterError, "field 집합"):
                    adapter.handle_event(
                        session_id,
                        {"type": "request_period", "request": {"date": "2026-09-02"}},
                    )

    def test_dashboard_binding_snapshot_contains_exact_chart_and_one_day(self) -> None:
        with tempfile.TemporaryDirectory(prefix="saju-chart-day-binding-") as directory:
            root = Path(directory)
            root.chmod(0o700)
            resources = self._resources(root)
            lease = root / "binding.lock"
            with ChartDayDashboardBinding(
                release_registry=RELEASE_V15_PATH,
                ephemeris_path=EPHEMERIS,
                hmac_key_file=resources["hmac"],
                encryption_key_file=resources["encryption"],
                store_root=resources["store"],
                process_lease_file=lease,
            ) as binding:
                status = binding.status()
                self.assertEqual(status["single_day_today_kst"], "2026-09-02")
                created = binding.create_session()
                session_id = created["session_id"]
                revision = created["state_revision"]
                for event in [*_events(), _period_event()]:
                    response = binding.handle_event(
                        session_id,
                        expected_revision=revision,
                        event=event,
                    )
                    revision = response["state_revision"]
                snapshot = binding.public_snapshot(session_id)
                self.assertEqual(snapshot["binding_id"], BINDING_ID)
                self.assertEqual(snapshot["schema_version"], "1.1.0")
                self.assertEqual(set(snapshot["value"]), {"chart", "period"})
                self.assertEqual(
                    snapshot["value"]["period"]["hard_facts"]["period"]["target_date"],
                    "2026-09-02",
                )
                with self.assertRaises(ChartDayDashboardBindingError) as caught:
                    binding.handle_event(
                        session_id,
                        expected_revision=revision - 1,
                        event=_period_event(),
                    )
                self.assertEqual(caught.exception.reason_code, "STALE_RUNTIME_REVISION")
