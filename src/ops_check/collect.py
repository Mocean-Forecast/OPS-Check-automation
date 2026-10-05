"""Collect: turn MongoDB + share scans into one Evidence per vessel, plus run-level warnings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Settings
from .models import Evidence, Vessel
from .sources.files import ShareScan, SystemFiles
from .sources.mongo import DayData


@dataclass
class Collected:
    vessels: list[Vessel]
    evidence: dict[str, Evidence]  # by vessel.key
    warnings: list[str]


def build_evidence(v: Vessel, day: date, scan: ShareScan, dd: DayData) -> Evidence:
    sf = scan.get(v.key)
    ev = Evidence(
        landing_archives=[a for a in sf.landing if a.day == day],
        staging_archives=[a for a in sf.staging if a.day == day],
        staging_day=sf.staging_days.get(day),
        processed_file=sf.processed.get(day),
        backlog_days=sorted({a.day for a in sf.landing + sf.staging if a.day < day}),
        backlog_in_landing=any(a.day < day for a in sf.landing),
        folder_found=sf.has_folder,
        db_processed=dd.processed(v),
        dpr=dd.dpr(v),
    )
    if v.mmsi is not None:
        stats = dd.ops_stats.get(v.mmsi) or {}
        ev.has_recorded_code = stats.get("hasRecordedCode")
        ev.clients = list(stats.get("clients") or v.clients)
        gap = dd.transit_gaps.get(v.mmsi)
        if gap:
            ev.transit_max_gap_min, ev.transit_samples = gap
    return ev


def run_warnings(vessels: list[Vessel], scan: ShareScan, synced_at: datetime | None,
                 now: datetime, s: Settings, ignored_systems: set[str] | None = None) -> list[str]:
    w: list[str] = []
    if synced_at is None:
        w.append("Registry sync time unknown (no sync timestamp on `vessels`)")
    elif now - synced_at > timedelta(hours=s.registry_stale_hours):
        w.append(f"Registry not synced since {synced_at.astimezone().strftime('%d/%m %H:%M')}")
    for root in scan.missing_roots:
        w.append(f"Share not reachable: {root}")
    if scan.unknown_active:
        u = scan.unknown_active
        w.append(f"Archives arriving for {len(u)} folder(s) not matched to an on-hire vessel: "
                 f"{', '.join(u[:8])}" + (" …" if len(u) > 8 else ""))
    no_folder = [v.name for v in vessels if v.system and not scan.get(v.key).has_folder]
    if no_folder:
        w.append(f"{len(no_folder)} on-hire vessel(s) without a folder on any share: "
                 f"{', '.join(no_folder[:8])}" + (" …" if len(no_folder) > 8 else ""))
    no_system = [v.name for v in vessels if v.on_hire and not v.system]
    if no_system:
        w.append(f"{len(no_system)} on-hire vessel(s) without system name in registry: "
                 f"{', '.join(no_system[:8])}" + (" …" if len(no_system) > 8 else ""))
    return w


def collect(vessels: list[Vessel], day: date, scan: ShareScan, dd: DayData,
            synced_at: datetime | None, now: datetime, s: Settings,
            ignored_systems: set[str] | None = None) -> Collected:
    ev = {v.key: build_evidence(v, day, scan, dd) for v in vessels}
    return Collected(vessels, ev, run_warnings(vessels, scan, synced_at, now, s, ignored_systems))
