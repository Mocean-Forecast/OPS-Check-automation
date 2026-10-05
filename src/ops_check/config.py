"""Settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the service has no extra dependency."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class VesselFields:
    """Where each registry attribute lives in the `vessels` collection (D13)."""

    mmsi: str = "mmsi"
    name: str = "nicename"
    operations_class: str = "operationsClass"
    client: str = "client"
    on_hire: str = "onHire"
    system: str = "systemName"
    status: str = "opsStatus"
    status_reason: str = "opsStatusReason"
    status_owner: str = "opsStatusOwner"
    status_expiry: str = "opsStatusExpiry"
    priority: str = "priority"
    synced_at: str = "lastSynced"


@dataclass(frozen=True)
class Settings:
    mongo_uri: str = ""
    mongo_db: str = "bmo_dataviewer"
    fields: VesselFields = field(default_factory=VesselFields)
    excluded_clients: tuple[str, ...] = ("MO4",)

    landing_dir: Path | None = None
    staging_dir: Path | None = None
    processed_dir: Path | None = None

    mover_stuck_hours: float = 6.0
    processing_deadline: time = time(11, 0)
    transit_gap_minutes: float = 60.0
    transit_signal_id: int | None = None  # None = first signal found (as Sjoerd's code)
    registry_stale_hours: float = 26.0

    results_dir: Path = Path("results")
    teams_webhook_url: str = ""
    watchdog_webhook_url: str = ""
    timezone: str = "Europe/Amsterdam"

    @classmethod
    def from_env(cls, dotenv: Path | None = Path(".env")) -> "Settings":
        if dotenv is not None:
            _load_dotenv(dotenv)
        env = os.environ.get

        def path(key: str) -> Path | None:
            v = env(key)
            return Path(v) if v else None

        fields = VesselFields(
            system=env("VESSEL_FIELD_SYSTEM", "systemName"),
            status=env("VESSEL_FIELD_STATUS", "opsStatus"),
            status_reason=env("VESSEL_FIELD_STATUS_REASON", "opsStatusReason"),
            status_owner=env("VESSEL_FIELD_STATUS_OWNER", "opsStatusOwner"),
            status_expiry=env("VESSEL_FIELD_STATUS_EXPIRY", "opsStatusExpiry"),
            priority=env("VESSEL_FIELD_PRIORITY", "priority"),
            synced_at=env("VESSEL_FIELD_SYNCED_AT", "lastSynced"),
        )
        signal = env("TRANSIT_SIGNAL_ID")
        return cls(
            mongo_uri=env("MONGO_URI", ""),
            mongo_db=env("MONGO_DB", "bmo_dataviewer"),
            fields=fields,
            landing_dir=path("LANDING_DIR"),
            staging_dir=path("STAGING_DIR"),
            processed_dir=path("PROCESSED_DIR"),
            mover_stuck_hours=float(env("MOVER_STUCK_HOURS", "6")),
            processing_deadline=_time(env("PROCESSING_DEADLINE", "11:00")),
            transit_gap_minutes=float(env("TRANSIT_GAP_MINUTES", "60")),
            transit_signal_id=int(signal) if signal else None,
            registry_stale_hours=float(env("REGISTRY_STALE_HOURS", "26")),
            results_dir=Path(env("RESULTS_DIR", "results")),
            teams_webhook_url=env("TEAMS_WEBHOOK_URL", ""),
            watchdog_webhook_url=env("WATCHDOG_WEBHOOK_URL", ""),
            timezone=env("TIMEZONE", "Europe/Amsterdam"),
        )
