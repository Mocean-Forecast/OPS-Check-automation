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
from .checks.spot import SpotInput, render_vessel, run_spot
from .collect import collect
from .config import Settings
from .models import FileInfo, RunReport, Vessel
from .notify import teams
from .progress import NULL as NO_PROGRESS, Progress
from .report.card import render_priority_alert, webhook_payload
from .report.spot_card import issue_dicts, render_ops_card
from .report.diff import diff_runs
from .report.message import build_message, priority_alerts, render_text
from .sources.files import ShareScan, SystemFiles, norm, scan_shares
from . import registry_config
from .sources.mongo import DayData, MongoSource, registry_synced_at, vessel_from_doc
from .sources.spot_mongo import SpotSource
from .store import RunStore

log = logging.getLogger("ops_check")


class Sources(Protocol):
    def vessels(self) -> list[Vessel]: ...
    def registry_synced_at(self) -> datetime | None: ...
    def day_data(self, vessels: list[Vessel], day: date) -> DayData: ...
    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str], progress: Any = None) -> ShareScan: ...
    def spot(self, vessels: list[Vessel], day: date) -> dict[int, SpotInput]: ...


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

    def spot(self, vessels: list[Vessel], day: date) -> dict[int, SpotInput]:
        return SpotSource.from_legacy_db(self.mongo.db).load(vessels, day)

    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str], progress: Any = None) -> ShareScan:
        return scan_shares(self.s.landing_dir, self.s.staging_dir, self.s.processed_dir,
                           wanted, day, ignored=ignored, progress=progress)


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

    def spot(self, vessels: list[Vessel], day: date) -> dict[int, SpotInput]:
        """Fixture `spot`: mmsi -> SpotInput JSON (see tests/fixtures/spot)."""
        return {int(k): SpotInput.from_json(v) for k, v in self.data.get("spot", {}).items()}

    def scan(self, day: date, wanted: dict[str, list[str]], ignored: set[str], progress: Any = None) -> ShareScan:
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
            data_day: date | None = None, post: bool = True, progress: Progress = NO_PROGRESS) -> RunOutcome:
    p = progress
    tz = ZoneInfo(s.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    run_date = now.date()
    day = data_day or (run_date - timedelta(days=1))

    p.note(f"OPS check · {slot} · data day {day:%a %d/%m/%Y}")
    with p.step("Registry (vessels, config)"):
        all_vessels = sources.vessels()
    excluded_clients = set(s.excluded_clients)
    config_warnings = registry_config.apply(all_vessels, registry_config.load(s.vessels_config))

    def is_excluded(v: Vessel) -> bool:
        return v.excluded or bool(excluded_clients & set(v.clients))

    fleet = [v for v in all_vessels if not is_excluded(v)]  # D25: all vessels except the exclude list
    # Off-hire vessels are not checked and not counted anywhere; only their total is shown (D25).
    vessels = [v for v in fleet if v.on_hire]
    off_hire = [v for v in fleet if not v.on_hire]
    ignored = {n for v in all_vessels if is_excluded(v) or not v.on_hire for n in v.folder_names}
    p.note(f"{len(vessels)} on hire, {len(off_hire)} off hire, {len(all_vessels) - len(fleet)} excluded")
    with p.step(f"Scanning shares ({len(vessels)} vessels)"):
        scan = sources.scan(day, {v.key: v.folder_names for v in vessels}, ignored, progress=p.item)
    if scan.missing_roots:
        p.note("WARNING shares not reachable: " + ", ".join(scan.missing_roots))
    with p.step("MongoDB: ops stats, processed data, DPR status"):
        day_data = sources.day_data(vessels, day)
    c = collect(vessels, day, scan, day_data,
                sources.registry_synced_at(), now, s, ignored_systems=ignored)
    c.warnings += config_warnings
    results = [evaluate(v, c.evidence[v.key], day, now, s) for v in vessels]
    report = RunReport(now, slot, day, results, c.warnings, CHECK_VERSION, off_hire_count=len(off_hire),
                       excluded_count=len(all_vessels) - len(fleet))
    run = report.to_dict()
    run["posted"] = False
    run["shares_missing"] = list(scan.missing_roots)
    with p.step("Spot check (DPR, fuel, wave, timeline, transit)"):
        run["spot"] = spot_check(vessels, results, sources, day, tz)
    pipe = sum(r.diagnosis.value in ("offline", "stuck_mover", "stuck_processing", "not_loaded", "data_issue")
               for r in results)
    p.note(f"Pipeline issues: {pipe} · spot check: {len(run['spot'].get('blocks') or {})} vessels need attention")

    store = RunStore(s.results_dir)
    stored = store.save(run_date, slot, run)

    before = store.load(run_date, "morning") if slot == "recheck" else None
    msg = build_message(run, diff_runs(before, run) if before else None)
    # Card compares with the previous run: 11:00 with 07:30, 07:30 with yesterday's final run.
    previous = before if slot == "recheck" else (store.load(run_date - timedelta(days=1), "recheck")
                                                 or store.load(run_date - timedelta(days=1), "morning"))
    card = render_ops_card(run, previous)
    prio = [render_priority_alert(i, msg.title) for i in priority_alerts(msg)]
    text = render_text(msg)

    posted = False
    if post:
        p.note("Posting to Teams …")
        try:
            teams.post(s.teams_webhook_url, webhook_payload(card))
            for pc in prio:
                teams.post(s.teams_webhook_url, webhook_payload(pc))
            posted = True
            store.mark_posted(run_date, slot, True)
            p.note("Posted.")
        except teams.PostError as e:
            log.error("Posting failed: %s", e)
            store.mark_posted(run_date, slot, False, str(e))
    return RunOutcome(run, text, card, prio, stored, posted)


# Pipeline diagnoses where the day's data isn't in MongoDB: only the DPR (crew input) is judged.
_NO_DB_DATA = {"offline", "stuck_mover", "stuck_processing", "not_loaded", "in_transit"}


def spot_check(vessels: list[Vessel], results: list, sources: Sources, day: date, tz: Any) -> dict[str, Any]:
    """DPR spot check for every on-hire vessel, alongside the chain (D2). Stored in the run file."""
    try:
        inputs = sources.spot(vessels, day)
    except Exception as e:  # noqa: BLE001 - the chain result must still post
        log.error("Spot check failed: %s", e)
        return {"error": str(e), "issues": [], "blocks": {}}
    issues: list[dict[str, Any]] = []
    blocks: dict[str, list[str]] = {}
    dpr_state: dict[str, str] = {}
    for r in results:
        v = r.vessel
        inp = inputs.get(v.mmsi) if v.mmsi is not None else None
        if inp is None or r.diagnosis.value == "known":
            continue
        res = run_spot(v.name, inp, day, tz)
        if r.diagnosis.value in _NO_DB_DATA and inp.vessel_type != "transit":  # transit: the vMRU line is the check
            res.findings = [f for f in res.findings if f.check == "spot_dpr"]
        dpr_state[v.name] = res.dpr_state
        issues += issue_dicts(v.name, inp, res)
        b = render_vessel(v.name, res)
        if b:
            blocks[v.name] = b
    return {"issues": issues, "blocks": blocks, "dpr_state": dpr_state}


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
