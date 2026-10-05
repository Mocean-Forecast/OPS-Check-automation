"""The four-stage chain from Mark's diagram, with the decisions in the Decisions Log.

    1 Registry   expected to be quiet?            -> KNOWN
    2 Raw        did the day's archive arrive?     -> OFFLINE / IN_TRANSIT / STUCK_MOVER
    3 Processed  improveddata<day>.mat + MongoDB   -> STUCK_PROCESSING / NOT_LOADED
    4 Plausible  complete and plausible?           -> DATA_ISSUE (all stage 4 checks run, D3)
    otherwise                                      -> OK

DPR status runs alongside and never stops the chain (D2).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from ..config import Settings
from ..models import Diagnosis, Evidence, Finding, Owner, Severity, Stage, Vessel, VesselResult
from . import dpr as dpr_rules

KNOWN_STATUSES = {
    "off-hire": "off-hire",
    "offhire": "off-hire",
    "demob": "demob",
    "daughtercraft": "daughtercraft",
    "dc": "daughtercraft",
    "tablet-only": "tablet-only",
    "tablet only": "tablet-only",
    "no system": "no system",
    "no-system": "no system",
}
ACTIVE_STATUSES = {"", "active"}

Stage4Check = Callable[[Vessel, Evidence, date, Settings], Iterable[Finding]]


def _f(v: Vessel, day: date, stage: Stage, check: str, sev: Severity, owner: Owner,
       msg: str, **details) -> Finding:
    return Finding(v.key, day, stage, check, sev, owner, msg, details)


def _hours(td: timedelta) -> float:
    return round(td.total_seconds() / 3600, 1)


# ---------------------------------------------------------------- stage 1

def registry_stage(v: Vessel, day: date) -> tuple[bool, list[Finding]]:
    """Returns (is_known, findings)."""
    if not v.on_hire:
        return True, [_f(v, day, Stage.REGISTRY, "expected_status", Severity.INFO, Owner.REGISTRY,
                         "off-hire", status="off-hire")]
    status = (v.status or "").strip().lower()
    if status in ACTIVE_STATUSES:
        return False, []
    if status not in KNOWN_STATUSES:
        return False, [_f(v, day, Stage.SIDE, "registry_unparseable", Severity.SUSPECT, Owner.REGISTRY,
                          f"registry status '{v.status}' not recognised", status=v.status)]
    label = KNOWN_STATUSES[status]
    if v.status_expiry and v.status_expiry < day:
        return False, [_f(v, day, Stage.SIDE, "registry_expired", Severity.SUSPECT, Owner.REGISTRY,
                          f"registry '{label}' expired {v.status_expiry.isoformat()}, checked as active",
                          status=label, expiry=v.status_expiry.isoformat())]
    reason = f" ({v.status_reason})" if v.status_reason else ""
    return True, [_f(v, day, Stage.REGISTRY, "expected_status", Severity.INFO, Owner.REGISTRY,
                     f"{label}{reason}", status=label, reason=v.status_reason,
                     expiry=v.status_expiry.isoformat() if v.status_expiry else None)]


# ---------------------------------------------------------------- stages 2 + 3

def arrival_and_processing(v: Vessel, ev: Evidence, day: date, now: datetime,
                           s: Settings) -> tuple[Diagnosis | None, list[Finding]]:
    """Returns (diagnosis, findings). diagnosis None = passes on to stage 4."""
    tz = ZoneInfo(s.timezone)
    deadline = datetime.combine(day + timedelta(days=1), s.processing_deadline, tz)

    if ev.processed_file and ev.db_processed:
        return None, []
    if ev.db_processed and not ev.processed_file:
        return None, [_f(v, day, Stage.PROCESSED, "processed_file", Severity.INFO, Owner.DEV,
                         "in MongoDB, but improveddata .mat not found on share")]
    if ev.processed_file and not ev.db_processed:
        return Diagnosis.NOT_LOADED, [_f(v, day, Stage.PROCESSED, "db_load", Severity.FAIL, Owner.DEV,
                                         "processed .mat present, not loaded into MongoDB",
                                         file=ev.processed_file.path)]
    if ev.staging_archives:
        oldest = min(a.modified for a in ev.staging_archives)
        age = _hours(now - oldest)
        if now >= deadline:
            return Diagnosis.STUCK_PROCESSING, [_f(
                v, day, Stage.PROCESSED, "processing", Severity.FAIL, Owner.DEV,
                f"archive waiting in Raw_vessel_data2 for {age} h, not processed",
                files=[a.path for a in ev.staging_archives], age_h=age)]
        return Diagnosis.IN_TRANSIT, [_f(v, day, Stage.PROCESSED, "processing", Severity.INFO, Owner.DEV,
                                         f"archive in Raw_vessel_data2 ({age} h), awaiting processing",
                                         age_h=age)]
    if ev.landing_archives:
        oldest = min(a.modified for a in ev.landing_archives)
        age = _hours(now - oldest)
        if age >= s.mover_stuck_hours:
            return Diagnosis.STUCK_MOVER, [_f(
                v, day, Stage.RAW, "mover", Severity.FAIL, Owner.DEV,
                f"archive stuck in FTP_VESSEL_DATA for {age} h",
                files=[a.path for a in ev.landing_archives], age_h=age)]
        return Diagnosis.IN_TRANSIT, [_f(v, day, Stage.RAW, "mover", Severity.INFO, Owner.DEV,
                                         f"archive in FTP_VESSEL_DATA ({age} h), awaiting move",
                                         age_h=age)]
    msg = "no data arrived"
    if not ev.folder_found:
        msg += f" (no folder '{v.system}' on the shares)" if v.system else " (no system name in registry)"
    return Diagnosis.OFFLINE, [_f(v, day, Stage.RAW, "raw_arrival", Severity.FAIL, Owner.OPS, msg,
                                  has_recorded_code=ev.has_recorded_code)]


# ---------------------------------------------------------------- stage 4

def transit_gap_check(v: Vessel, ev: Evidence, day: date, s: Settings) -> Iterable[Finding]:
    """Ported from Sjoerd's timeTraces gap pipeline (partial-mru-gap)."""
    if v.operations_class != "transit" or ev.transit_max_gap_min is None:
        return []
    if ev.transit_max_gap_min > s.transit_gap_minutes:
        return [_f(v, day, Stage.PLAUSIBLE, "coverage_gap", Severity.FAIL, Owner.OPS,
                   f"MRU gap of {round(ev.transit_max_gap_min)} min "
                   f"(limit {round(s.transit_gap_minutes)})",
                   max_gap_min=ev.transit_max_gap_min, samples=ev.transit_samples)]
    return []


DEFAULT_STAGE4: list[Stage4Check] = [transit_gap_check]


# ---------------------------------------------------------------- side checks

def dpr_side(v: Vessel, ev: Evidence, day: date) -> tuple[str | None, list[Finding]]:
    if ev.dpr is None or v.operations_class not in ("CTV", "OSV"):
        return None, []
    status = ev.dpr.status
    if status == dpr_rules.NOT_FILLED:
        return status, [_f(v, day, Stage.SIDE, "dpr_status", Severity.INFO, Owner.ACCOUNT_MANAGER,
                           "DPR not filled in", dpr=status)]
    if status == dpr_rules.FILLED and v.operations_class == "OSV":
        return status, [_f(v, day, Stage.SIDE, "dpr_status", Severity.INFO, Owner.ACCOUNT_MANAGER,
                           "DPR not signed off", dpr=status)]
    return status, []


# ---------------------------------------------------------------- full chain

def evaluate(v: Vessel, ev: Evidence, day: date, now: datetime, s: Settings,
             stage4: list[Stage4Check] | None = None) -> VesselResult:
    findings: list[Finding] = []

    dpr_status, dpr_findings = dpr_side(v, ev, day)

    known, reg = registry_stage(v, day)
    findings += reg
    if known:
        return VesselResult(v, day, Diagnosis.KNOWN, findings + dpr_findings, dpr_status)

    diagnosis, f23 = arrival_and_processing(v, ev, day, now, s)
    findings += f23

    if diagnosis is None:
        f4 = [f for check in (stage4 if stage4 is not None else DEFAULT_STAGE4)
              for f in check(v, ev, day, s)]
        findings += f4
        diagnosis = Diagnosis.DATA_ISSUE if any(f.severity != Severity.INFO for f in f4) else Diagnosis.OK

    if ev.backlog_days:
        oldest = min(ev.backlog_days)
        n = (day - oldest).days + 1
        findings.append(_f(v, day, Stage.PROCESSED, "backlog", Severity.FAIL, Owner.DEV,
                           f"{len(ev.backlog_days)} older day(s) still in inbox, oldest "
                           f"{oldest.isoformat()} (day {n})",
                           days=[d.isoformat() for d in sorted(ev.backlog_days)]))
        if not diagnosis.needs_action:
            diagnosis = Diagnosis.STUCK_PROCESSING

    return VesselResult(v, day, diagnosis, findings + dpr_findings, dpr_status)
