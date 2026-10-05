"""One run: collect -> check -> store -> build message -> post (Mark's steps 1–5).

Step 4 (LLM notes, D20) is not wired in yet: the message is complete without it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from . import CHECK_VERSION
from .checks.chain import evaluate
from .collect import collect
from .config import Settings
from .models import FileInfo, RunReport, Vessel
from .notify import teams
from .report.card import render_card, render_priority_alert, webhook_payload
from .report.diff import diff_runs
from .report.message import build_message, priority_alerts, render_text
from .sources.files import ShareScan, SystemFiles, norm, scan_shares
from . import registry_config
from .sources.mongo import DayData, MongoSource, registry_synced_at, vessel_from_doc
from .store import RunStore

log = logging.getLogger("ops_check")


class Sources(Protocol):
    def vessels(self) -> list[Vessel]: ...
    def registry_synced_at(self) -> datetime | None: ...
    def day_data(self, vessels: list[Vessel], day: date) -> DayData: ...
    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str]) -> ShareScan: ...


class LiveSources:
    def __init__(self, s: Settings):
        self.s = s
        self.mongo = MongoSource.connect(s)

    def vessels(self) -> list[Vessel]:
        return self.mongo.vessels()

    def registry_synced_at(self) -> datetime | None:
        return self.mongo.registry_synced_at()

    def day_data(self, vessels: list[Vessel], day: date) -> DayData:
        return self.mongo.day_data(vessels, day)

    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str]) -> ShareScan:
        return scan_shares(self.s.landing_dir, self.s.staging_dir, self.s.processed_dir,
                           wanted, day, ignored=ignored)


class FixtureSources:
    """Runs the whole pipeline from one JSON file: no MongoDB, no shares. See tests/fixtures."""

    def __init__(self, path: Path, s: Settings):
        self.s = s
        self.data = json.loads(Path(path).read_text())

    def vessels(self) -> list[Vessel]:
        return [vessel_from_doc(d, self.s) for d in self.data["vessels"]]

    def registry_synced_at(self) -> datetime | None:
        return registry_synced_at(self.data["vessels"], self.s)

    def day_data(self, vessels: list[Vessel], day: date) -> DayData:
        d = self.data.get("day_data", {})
        conv = lambda m: {int(k): v for k, v in m.items()}  # noqa: E731
        return DayData(
            ops_stats=conv(d.get("ops_stats", {})),
            general=set(d.get("general", [])),
            sov_general=set(d.get("sov_general", [])),
            ctv_dpr=conv(d.get("ctv_dpr", {})),
            sov_dpr=conv(d.get("sov_dpr", {})),
            transit_gaps={int(k): tuple(v) for k, v in d.get("transit_gaps", {}).items()},
        )

    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str]) -> ShareScan:
        """Fixture `files` are keyed by folder name, like the shares."""
        files = self.data.get("files", {})
        by_folder: dict[str, SystemFiles] = {}
        display: dict[str, str] = {}

        def get(folder: str) -> SystemFiles:
            display.setdefault(norm(folder), folder)
            return by_folder.setdefault(norm(folder), SystemFiles())

        for share, folders in (("landing", files.get("landing", {})), ("staging", files.get("staging", {}))):
            for folder, entries in folders.items():
                sf = get(folder)
                sf.shares.add(share)
                for e in entries:
                    getattr(sf, share).append(FileInfo(e["name"], date.fromisoformat(e["day"]),
                                                       datetime.fromisoformat(e["modified"])))
        for folder, entries in files.get("staging_days", {}).items():
            sf = get(folder)
            sf.shares.add("staging")
            for e in entries:
                d = date.fromisoformat(e["day"])
                sf.staging_days[d] = FileInfo(f"{folder}/{e['day']}", d, datetime.fromisoformat(e["modified"]))
        for folder, days in files.get("processed", {}).items():
            sf = get(folder)
            sf.shares.add("processed")
            for dstr in days:
                d = date.fromisoformat(dstr)
                sf.processed[d] = FileInfo(f"{folder}/processeddata/improveddata{dstr}.mat", d,
                                           datetime(d.year, d.month, d.day, tzinfo=timezone.utc))
        for folder in files.get("folders", []):
            get(folder).shares.add("processed")

        systems = {}
        for key, names in wanted.items():
            hit = next((by_folder[norm(n)] for n in names if norm(n) in by_folder), SystemFiles())
            systems[norm(key)] = hit
        matched = {norm(n) for ns in wanted.values() for n in ns} | {norm(n) for n in ignored}
        unknown = sorted(display[f] for f, sf in by_folder.items() if f not in matched and sf.landing)
        return ShareScan(systems, [], set(by_folder), unknown)


@dataclass
class RunOutcome:
    run: dict[str, Any]
    text: str
    card: dict[str, Any]
    priority_cards: list[dict[str, Any]]
    stored_at: Path
    posted: bool


def execute(s: Settings, sources: Sources, slot: str, now: datetime | None = None,
            data_day: date | None = None, post: bool = True) -> RunOutcome:
    tz = ZoneInfo(s.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    run_date = now.date()
    day = data_day or (run_date - timedelta(days=1))

    all_vessels = sources.vessels()
    excluded = set(s.excluded_clients)

    def is_excluded(v: Vessel) -> bool:
        return bool(excluded & set(v.clients))

    fleet = [v for v in all_vessels if not is_excluded(v)]  # D25: everything except MO4
    config_warnings = registry_config.apply(fleet, registry_config.load(s.vessels_config))
    # Off-hire vessels are not checked and not counted anywhere; only their total is shown (D25).
    vessels = [v for v in fleet if v.on_hire]
    off_hire = [v for v in fleet if not v.on_hire]
    ignored = {n for v in all_vessels if is_excluded(v) or not v.on_hire for n in v.folder_names}
    scan = sources.scan(day, {v.key: v.folder_names for v in vessels}, ignored)
    c = collect(vessels, day, scan, sources.day_data(vessels, day),
                sources.registry_synced_at(), now, s, ignored_systems=ignored)
    c.warnings += config_warnings
    results = [evaluate(v, c.evidence[v.key], day, now, s) for v in vessels]
    report = RunReport(now, slot, day, results, c.warnings, CHECK_VERSION, off_hire_count=len(off_hire))
    run = report.to_dict()
    run["posted"] = False

    store = RunStore(s.results_dir)
    stored = store.save(run_date, slot, run)

    before = store.load(run_date, "morning") if slot == "recheck" else None
    msg = build_message(run, diff_runs(before, run) if before else None)
    card = render_card(msg)
    prio = [render_priority_alert(i, msg.title) for i in priority_alerts(msg)]
    text = render_text(msg)

    posted = False
    if post:
        try:
            teams.post(s.teams_webhook_url, webhook_payload(card))
            for pc in prio:
                teams.post(s.teams_webhook_url, webhook_payload(pc))
            posted = True
            store.mark_posted(run_date, slot, True)
        except teams.PostError as e:
            log.error("Posting failed: %s", e)
            store.mark_posted(run_date, slot, False, str(e))
    return RunOutcome(run, text, card, prio, stored, posted)


def watchdog(s: Settings, slot: str, now: datetime | None = None, post: bool = True) -> str | None:
    """D7: alert if the run for `slot` hasn't posted. Returns the alert text, or None if all is well."""
    tz = ZoneInfo(s.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    run = RunStore(s.results_dir).load(now.date(), slot)
    if run and run.get("posted"):
        return None
    reason = "no run found" if run is None else f"run did not post ({run.get('post_error') or 'unknown'})"
    alert = f"OPS CHECK WATCHDOG {now.strftime('%d/%m %H:%M')}: {slot} check missing – {reason}"
    if post:
        card = {"type": "AdaptiveCard", "version": "1.4",
                "body": [{"type": "TextBlock", "text": alert, "wrap": True,
                          "weight": "Bolder", "color": "Attention"}]}
        teams.post(s.watchdog_webhook_url or s.teams_webhook_url, webhook_payload(card))
    return alert
