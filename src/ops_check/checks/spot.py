"""DPR spot check for every on-hire vessel (handover 11 Oct 2026, docs/HANDOVER-spot-check.md).

Pure functions: `SpotInput` holds everything read from MongoDB for one vessel-day
(see sources/spot_mongo.py); `run_spot` turns it into a DPR state plus findings, each
carrying its Teams tag. Runs alongside the four-stage chain (Stage.SIDE, D2); every
failure is listed (D3). The late-morning run is the final verdict.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

from ..models import Finding, Owner, Severity, Stage, Tag
from . import dpr as dpr_rules

# ------------------------------------------------------------------ settings

FUEL_MARGIN = 0.20            # |entered - measured| / measured
TIMELINE_FUEL_TOL = 0.02      # timeline sum vs measured (provisional breakdown check)
TIMELINE_FUEL_TOL_L = 5.0
MIN_FUEL_L = 50.0             # below this (idling in port) a missing entered/measured value is a warning, not a fail
# Day types without a normal trip: no missing-leg check (DPR accessDayType / status)
NO_TRIP_DAYS = {
    "NOT_REQUIRED", "OFF_CONTRACT", "WEATHER_DAY", "MAINTENANCE", "TECHNICAL",
    "Excluded day", "No access day", "Adverse weather", "Port call day", "Port call day unplanned client",
}
# Timeline event types that are transfers for the Hs check. tiedOff / impact / waiting are not.
TURBINE_TYPES = {"docking", "turbine transfer", "platform transfer"}
TIMELINE_LATE_MIN = 60        # timeline / route ending this much before the day's data or harbour arrival
MATCH_SLACK_MIN = 5           # a logged transfer is on the timeline if an event overlaps it ± this
VMRU_INTERVAL_MIN = 15        # transit vessels: vMRU traces every 15 min (timeTraces)
VMRU_GAP_MIN = 20             # a step between traces longer than this is a gap
VMRU_LAST_SLOT = timedelta(hours=23, minutes=45)  # last trace of a complete day (covers to 23:59 UTC)
V2V_TYPES = {"vessel2vessel transfer", "Vessel2Vessel"}

# DPR states
NO_DPR = "no DPR"
NOT_FILLED = "not filled"
FILLED = "filled, not signed off"
SIGNED_VESSEL = "signed off by vessel"
SIGNED_BOTH = "signed off by vessel + client"
CTV_FILLED = "filled"
CTV_SIGNED = "filled + signed off"


# ------------------------------------------------------------------ input model

@dataclass
class WaveSeries:
    label: str                      # graph label, e.g. "Hermess nowcast" / "Furuno"
    source_id: str | None
    resolution_min: float           # sample interval = tolerance
    samples: list[tuple[datetime, float | None, bool]] = field(default_factory=list)  # (utc, hs, reliable)


@dataclass
class MissedTransfer:
    location: str
    start: datetime | None          # utc
    stop: datetime | None
    pax_in: int = 0
    pax_out: int = 0
    cargo_in: int = 0
    cargo_out: int = 0
    comment: str = ""
    kind: str = "turbine"           # turbine | v2v


@dataclass
class SpotInput:
    vessel_type: str                                   # "CTV" | "OSV"
    dpr: dict[str, Any] | None = None                  # CTV_dprInput / SOV_dprInput doc
    fuel_measured_l: float | None = None               # analyticsVesselEquipments, engine "combined"
    timeline: list[dict[str, Any]] = field(default_factory=list)  # analyticsDprTimeline.events, details flattened
    wave: WaveSeries | None = None
    wave_source_hint: str | None = None                # source named in the data when no series exists
    missed: list[MissedTransfer] = field(default_factory=list)
    last_dpr_day: str | None = None                    # ISO day of the newest earlier DPR, when today's is missing
    logged: list[dict[str, Any]] = field(default_factory=list)  # transfers log: {start, stop, location}
    data_start: datetime | None = None                 # timeline startOfDay / endOfDay = recorded span
    data_end: datetime | None = None
    route_start: datetime | None = None                # analyticsGeneralOverviews.route.timeStamps
    route_end: datetime | None = None
    harbour_arrival: datetime | None = None
    recorded: bool | None = None                       # operationsStats exists for the day (None = unknown)
    vmru: dict[str, list[datetime]] = field(default_factory=dict)  # transit: vMRU sensor -> trace stamps (utc)
    vmru_expected: list[str] = field(default_factory=list)  # vMRU sensors seen in the 7 days before
    last_trace: datetime | None = None                 # transit: newest trace of any sensor
    project: dict[str, Any] | None = None              # transit: active DPR shipping project

    # JSON round trip for fixtures
    def to_json(self) -> dict[str, Any]:
        def conv(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            if isinstance(o, dict):
                return {k: conv(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [conv(v) for v in o]
            return o
        return conv(asdict(self))

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "SpotInput":
        w = d.get("wave")
        wave = None
        if w:
            wave = WaveSeries(w["label"], w.get("source_id"), float(w["resolution_min"]),
                              [(_dt(t), h, bool(r)) for t, h, r in w.get("samples", [])])
        missed = [MissedTransfer(**{**m, "start": _dt(m.get("start")), "stop": _dt(m.get("stop"))})
                  for m in d.get("missed", [])]
        return cls(d["vessel_type"], d.get("dpr"), d.get("fuel_measured_l"), d.get("timeline", []),
                   wave, d.get("wave_source_hint"), missed, d.get("last_dpr_day"), d.get("logged", []),
                   *(_dt(d.get(k)) for k in ("data_start", "data_end", "route_start", "route_end",
                                             "harbour_arrival")), d.get("recorded"),
                   {k: [_dt(t) for t in v] for k, v in (d.get("vmru") or {}).items()},
                   d.get("vmru_expected", []), _dt(d.get("last_trace")), d.get("project"))


@dataclass
class SpotResult:
    dpr_state: str
    findings: list[Finding]


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _fmt_l(v: float) -> str:
    return f"{v:,.1f}".replace(".0", "") + " L"


# ------------------------------------------------------------------ DPR state

def dpr_state(vessel_type: str, doc: dict[str, Any] | None) -> str:
    if vessel_type == "OSV":
        if not doc:
            return NO_DPR
        so = doc.get("signedOff") or {}
        amount = so.get("amount") or 0
        if amount >= 2 or (amount >= 1 and so.get("signedOffClient")):
            return SIGNED_BOTH
        if amount >= 1:
            return SIGNED_VESSEL
        return FILLED if dpr_rules.sov_dpr_status(doc) == dpr_rules.FILLED else NOT_FILLED
    if not doc:
        return NO_DPR
    if dpr_rules.ctv_dpr_status(doc) == dpr_rules.NOT_FILLED:
        return NOT_FILLED
    return CTV_SIGNED if ((doc.get("signOff") or {}).get("signOffStatus") or 0) > 0 else CTV_FILLED


# ------------------------------------------------------------------ fuel (CTV)

def fuel_entered(doc: dict[str, Any] | None) -> float | None:
    """Sum of Consumed over diesel rows; None when nothing is entered."""
    if not doc:
        return None
    rows = [r for r in doc.get("consumptionList") or [] if str(r.get("type") or "").lower() == "diesel"]
    if rows:
        total = sum(_num(r.get("consumed")) or 0 for r in rows)
    else:  # older schema
        total = _num(((doc.get("consumption") or {}).get("fuel") or {}).get("used")) or 0
    return total or None


@dataclass
class Legs:
    to_park: float | None = None     # fuel, None = no such leg
    from_park: float | None = None
    in_port: bool = False             # any port event / leg into port today


def transit_legs(events: list[dict[str, Any]]) -> Legs:
    ports = {e["details"].get("port") for e in events if e.get("event_type") == "port"} - {None}
    fields = {e["details"].get("field") for e in events if e["details"].get("field")}
    legs = Legs(in_port=bool(ports))
    for e in events:
        if e.get("event_type") != "transit":
            continue
        d = e["details"]
        a, b = d.get("from"), d.get("to")
        if not a or not b or a == b:
            continue  # "near X", "outside field", "from and to X"
        fuel = _num(d.get("fuel usage")) or 0.0
        a_port = a in ports or (b in fields and a not in fields)
        b_port = b in ports or (a in fields and b not in fields)
        if a_port and not b_port:
            legs.to_park = (legs.to_park or 0) + fuel
        elif b_port and not a_port:
            legs.from_park = (legs.from_park or 0) + fuel
            legs.in_port = True
    return legs


def check_fuel(inp: SpotInput, f: "_Mk") -> None:
    entered = fuel_entered(inp.dpr)
    measured = inp.fuel_measured_l
    access = (inp.dpr or {}).get("accessDayType")
    legs = transit_legs(inp.timeline)
    no_trip = access in NO_TRIP_DAYS

    if not measured and not entered:
        if measured is None and inp.dpr and not no_trip and legs.to_park is not None:
            f("fuel_missing", "Fuel: entered and measured both missing")
        return
    if measured is None:
        f("fuel_measured_missing", f"Fuel measured missing (entered {_fmt_l(entered)})",
          severity=Severity.FAIL if entered >= MIN_FUEL_L else Severity.SUSPECT, entered=entered)
    elif not entered:
        f("fuel_entered_missing", f"Fuel entered missing (measured {_fmt_l(measured)})",
          severity=Severity.FAIL if measured >= MIN_FUEL_L else Severity.SUSPECT, measured=measured)

    missing_leg = None
    if not no_trip and measured:
        # A leg that doesn't exist (vessel stayed out) is a multi-day trip, not a missing leg.
        if (legs.to_park or 0) > 0 and legs.from_park == 0:
            missing_leg = "transit from park"
        elif (legs.from_park or 0) > 0 and legs.to_park == 0:
            missing_leg = "transit to park"
    if missing_leg:
        f("fuel_missing_leg", f"Fuel {missing_leg} = 0 L while the other leg has fuel",
          to_park=legs.to_park, from_park=legs.from_park)

    if measured and entered:
        pct = (entered - measured) / measured
        if abs(pct) > FUEL_MARGIN:
            notes = []
            if missing_leg:
                notes.append("measured incomplete, % unreliable")
            if not legs.in_port:
                notes.append("vessel did not return to port today, entered value may cover a multi-day dip")
            msg = (f"Fuel entered {_fmt_l(entered)} vs measured {_fmt_l(measured)} ({pct:+.0%})"
                   + (f" – {'; '.join(notes)}" if notes else ""))
            f("fuel_margin", msg, entered=entered, measured=measured, pct=round(pct * 100, 1))

    # Timeline events can't burn more than the engines measured. Less is normal: fuel burnt in
    # port (engines running, cruising inside the port geofence) belongs to no event (Martijn, 11 Oct).
    if measured and inp.timeline:
        parts = sum(_num(e["details"].get("fuel usage")) or 0 for e in inp.timeline)
        if parts - measured > max(TIMELINE_FUEL_TOL * measured, TIMELINE_FUEL_TOL_L):
            f("fuel_breakdown", f"Fuel on timeline {_fmt_l(parts)} > measured total {_fmt_l(measured)}",
              parts=round(parts, 1), measured=measured)


# ------------------------------------------------------------------ weather / Hs

@dataclass
class Window:
    kind: str            # turbine | v2v | missed
    label: str
    start: datetime
    stop: datetime


def transfer_windows(inp: SpotInput) -> list[Window]:
    out: list[Window] = []
    for e in inp.timeline:
        t = e.get("event_type")
        kind = "turbine" if t in TURBINE_TYPES else "v2v" if t in V2V_TYPES else None
        s, x = _dt(e.get("start_time")), _dt(e.get("end_time"))
        if kind and s and x:
            out.append(Window(kind, e.get("event_name") or t, s, x))
    for m in inp.missed:
        if m.start and m.stop:
            out.append(Window("missed", m.location, m.start, m.stop))
    return sorted(out, key=lambda w: w.start)


def _valid(hs: float | None, reliable: bool) -> bool:
    return reliable and hs is not None and not math.isnan(hs) and hs > 0


def hs_in_window(series: WaveSeries, w: Window) -> bool:
    tol = timedelta(minutes=series.resolution_min)
    return any(_valid(h, r) for t, h, r in series.samples if w.start - tol <= t <= w.stop + tol)


def hs_gaps(series: WaveSeries, start: datetime, stop: datetime,
            windows: list[Window]) -> list[tuple[datetime, datetime]]:
    """Stretches inside [start, stop] without valid Hs, outside transfer windows."""
    tol = timedelta(minutes=series.resolution_min)
    good = sorted(t for t, h, r in series.samples if _valid(h, r) and start - tol <= t <= stop + tol)
    marks = [start - tol, *good, stop + tol]
    gaps = [(a + tol, b - tol) for a, b in zip(marks, marks[1:]) if b - a > 2 * tol]
    gaps = [(max(a, start), min(b, stop)) for a, b in gaps]
    return [(a, b) for a, b in gaps if b > a and not any(w.start <= b and w.stop >= a for w in windows)]


def _active_span(inp: SpotInput) -> tuple[datetime, datetime] | None:
    ev = [e for e in inp.timeline if e.get("event_type") not in ("system offline", "unknown", "no gps")]
    starts = [d for d in (_dt(e.get("start_time")) for e in ev) if d]
    stops = [d for d in (_dt(e.get("end_time")) for e in ev) if d]
    return (min(starts), max(stops)) if starts and stops else None


def check_weather(inp: SpotInput, f: "_Mk", tz: tzinfo) -> None:
    hm = lambda d: d.astimezone(tz).strftime("%H:%M")  # noqa: E731
    if inp.recorded is False:
        return  # vessel sent no data at all: the chain reports it (offline / known), no wave line
    w = inp.wave
    windows = transfer_windows(inp)
    if w is None:
        src = f" (source {inp.wave_source_hint})" if inp.wave_source_hint else ""
        f("weather_empty", f"Wave data not available{src}", transfers=len(windows))
        return
    numeric = [h for _, h, _ in w.samples if h is not None and not math.isnan(h)]
    if not numeric or not any(_valid(h, r) for _, h, r in w.samples) and not windows:
        f("weather_empty", f"Weather overview empty, no valid Hs (source {w.label})")
        return
    missing = [x for x in windows if not hs_in_window(w, x)]
    context = ""
    if inp.vessel_type == "CTV":
        span = _active_span(inp)
        gaps = hs_gaps(w, *span, windows) if span else []
        if gaps:
            context = " · gaps outside transfers: " + ", ".join(f"{hm(a)}–{hm(b)}" for a, b in gaps)
    if missing:
        parts = []
        for kind, name in (("turbine", "turbine transfers"), ("v2v", "V2V transfers"),
                           ("missed", "missed transfers")):
            tot = [x for x in windows if x.kind == kind]
            miss = [x for x in missing if x.kind == kind]
            if miss:
                parts.append(f"{len(miss)} of {len(tot)} {name}")
        rng = f"{hm(missing[0].start)}–{hm(missing[-1].stop)}"
        flat = " (flat 0)" if numeric and max(numeric) == 0 else ""
        f("hs_missing_transfers", f"Hs missing{flat} during {', '.join(parts)} ({rng}), source {w.label}{context}",
          missing=len(missing), transfers=len(windows), source=w.label)
    elif context:
        f("hs_gaps", f"Hs present during all transfers{context}", severity=Severity.INFO, tag=None)


# ------------------------------------------------------------------ missed transfers

def check_missed(inp: SpotInput, f: "_Mk", tz: tzinfo) -> None:
    rows = inp.missed
    if not rows:
        return
    lines = []
    for m in rows:
        t = (f", {m.start.astimezone(tz):%H:%M}–{m.stop.astimezone(tz):%H:%M}"
             if m.start and m.stop else "")
        cargo = f", cargo {m.cargo_in}↑ {m.cargo_out}↓" if (m.cargo_in or m.cargo_out) else ""
        com = f', "{m.comment}"' if m.comment else ""
        v2v = " (V2V)" if m.kind == "v2v" else ""
        lines.append(f"{m.location}{v2v}{t}, pax {m.pax_in}↑ {m.pax_out}↓{cargo}{com}")
    n = len(rows)
    f("missed_transfers", f"{n} missed transfer{'s' if n > 1 else ''} logged", rows=lines)


# ------------------------------------------------------------------ timeline / route (Team Data)

TIMELINE_TRANSFER_TYPES = TURBINE_TYPES | {"impact"}


def check_timeline(inp: SpotInput, f: "_Mk", tz: tzinfo) -> None:
    hm = lambda d: d.astimezone(tz).strftime("%H:%M")  # noqa: E731
    slack = timedelta(minutes=MATCH_SLACK_MIN)
    late = timedelta(minutes=TIMELINE_LATE_MIN)
    events = [(_dt(e.get("start_time")), _dt(e.get("end_time"))) for e in inp.timeline
              if e.get("event_type") in TIMELINE_TRANSFER_TYPES]
    events = [(a, b) for a, b in events if a and b]

    # (a) transfers in the transfers log that the timeline doesn't show
    logged = [(_dt(t["start"]), _dt(t.get("stop")) or _dt(t["start"]), t.get("location") or "?")
              for t in inp.logged if t.get("start")]
    missing = [t for t in logged if not any(a - slack <= t[1] and b + slack >= t[0] for a, b in events)]
    if missing:
        where = "no timeline" if not inp.timeline else f"{len(logged) - len(missing)} of {len(logged)} on the timeline"
        f("timeline_transfers",
          f"Timeline misses {len(missing)} logged transfer{'s' if len(missing) > 1 else ''} "
          f"({hm(missing[0][0])}–{hm(missing[-1][1])}; {where})", tag=Tag.DATA,
          missing=len(missing), logged=len(logged))

    # (b) timeline stops well before the end of the recorded data
    real = [_dt(e.get("end_time")) for e in inp.timeline
            if e.get("event_type") not in ("system offline", "unknown", "no gps")]
    real = [x for x in real if x]
    if real and inp.data_end and max(real) < inp.data_end - late:
        f("timeline_end", f"Timeline ends {hm(max(real))}, data runs to {hm(inp.data_end)}", tag=Tag.DATA,
          timeline_end=max(real), data_end=inp.data_end)

    # (c) route (map track) ends well before the harbour arrival: time base looks shifted
    if inp.route_end and inp.harbour_arrival and inp.route_end < inp.harbour_arrival - late:
        span = (f", data {hm(inp.data_start)}–{hm(inp.data_end)}" if inp.data_start and inp.data_end else "")
        f("route_end", f"Route {hm(inp.route_start or inp.route_end)}–{hm(inp.route_end)} ends before "
          f"harbour arrival {hm(inp.harbour_arrival)}{span}", tag=Tag.DATA)


# ------------------------------------------------------------------ transit vessels (vMRU)

def vmru_label(sensor: str) -> str:
    """vMRU_jviHq3ZyNLWsviXE3yqLC_70.000_0.000_10.000 -> 'vMRU jviHq3 (70, 0, 10)'."""
    parts = sensor.split("_")
    try:
        pos = ", ".join(f"{float(x):g}" for x in parts[2:5])
        return f"vMRU {parts[1][:6]} ({pos})"
    except (ValueError, IndexError):
        return sensor


def check_transit(inp: SpotInput, f: "_Mk", day: date) -> None:
    hm = lambda d: d.astimezone(timezone.utc).strftime("%H:%M")  # noqa: E731  (UTC: the check is on the UTC day)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    p = inp.project
    if p is None:
        f("transit_project", "No active transit project in the DPR", severity=Severity.SUSPECT)
    if not inp.vmru:
        last = f", last data {inp.last_trace:%d/%m %H:%M} UTC" if inp.last_trace else ", no data at all"
        f("transit_no_vmru", f"No vMRU data for {day:%d/%m}{last}")
        return
    problems = []
    for sensor in sorted(set(inp.vmru_expected) - set(inp.vmru)):
        problems.append(f"{vmru_label(sensor)} missing")
    for sensor, stamps in sorted(inp.vmru.items()):
        t = sorted(x for x in stamps if x >= start)
        day_t = [x for x in t if x < start + timedelta(days=1)]
        issues = []
        if not t or max(t) < start + VMRU_LAST_SLOT:
            issues.append(f"ends {hm(max(day_t))} UTC" if day_t else "no data")
        if day_t and day_t[0] > start + timedelta(minutes=VMRU_GAP_MIN):
            issues.append(f"starts {hm(day_t[0])} UTC")
        gaps = [(a, b) for a, b in zip(day_t, day_t[1:]) if b - a > timedelta(minutes=VMRU_GAP_MIN)]
        if gaps:
            issues.append("gaps " + ", ".join(f"{hm(a)}–{hm(b)}" for a, b in gaps[:4]) + (" …" if len(gaps) > 4 else ""))
        if issues:
            problems.append(f"{vmru_label(sensor)} {'; '.join(issues)}")
    if problems:
        f("transit_vmru", f"vMRU data not continuous to 23:59 UTC: {' · '.join(problems)}",
          sensors=len(inp.vmru), problems=len(problems))


# ------------------------------------------------------------------ entry point

class _Mk:
    """Collects findings for one vessel-day with defaults (Stage.SIDE, Team OPS)."""

    def __init__(self, vessel: str, day: date):
        self.vessel, self.day, self.out = vessel, day, []

    def __call__(self, check: str, message: str, severity: Severity = Severity.FAIL,
                 tag: Tag | None = Tag.OPS, **details: Any) -> None:
        self.out.append(Finding(self.vessel, self.day, Stage.SIDE, f"spot_{check}", severity,
                                Owner.OPS, message, details, tag))


def run_spot(vessel: str, inp: SpotInput, day: date, tz: tzinfo) -> SpotResult:
    f = _Mk(vessel, day)
    if inp.vessel_type == "transit":  # own DPR (projects + transit details): vMRU continuity only
        check_transit(inp, f, day)
        p = inp.project
        state = (f"project {p.get('name')} ({p.get('origin')} → {p.get('destination')})" if p else "no project")
        return SpotResult(state, f.out)
    state = dpr_state(inp.vessel_type, inp.dpr)
    if state == NO_DPR:  # no record at all: the crew hasn't saved the day's DPR
        last = (f", last DPR {date.fromisoformat(inp.last_dpr_day):%d/%m}" if inp.last_dpr_day
                else ", no earlier DPR")
        f("dpr", f"DPR not created{last}", tag=None, last_dpr=inp.last_dpr_day)
    elif state == NOT_FILLED:
        f("dpr", "DPR not filled", tag=None)
    elif state == FILLED:
        f("dpr", "DPR not signed off", tag=None)
    check_weather(inp, f, tz)
    check_missed(inp, f, tz)
    if inp.vessel_type == "CTV":
        check_fuel(inp, f)
    check_timeline(inp, f, tz)
    f("timeline", "timeline: DP radius / slip graph not yet checked", severity=Severity.INFO, tag=None)
    return SpotResult(state, f.out)


def render_lines(name: str, res: SpotResult) -> list[str]:
    """Plain-text lines for the message: one line per finding, tag at the end (or bold fallback)."""
    out = []
    for x in res.findings:
        tag = f" @{x.tag.value}" if x.tag else ""
        warn = "⚠ " if x.severity == Severity.SUSPECT else ""
        out.append(f"{name} – {warn}{x.message}{tag}")
        for row in x.details.get("rows", []):
            out.append(f"  • {row}")
    return out


# ------------------------------------------------------------------ grouped output

_TAG_ORDER = {Tag.DATA: 0, Tag.OPS: 1, None: 2}


def needs_attention(res: SpotResult) -> list[Finding]:
    """Findings worth a bullet: fails and warnings, not info/context lines."""
    return [x for x in res.findings if x.severity != Severity.INFO]


def render_vessel(name: str, res: SpotResult) -> list[str]:
    """One block per vessel: the vessel line (each team mentioned once), then every problem as a bullet."""
    items = sorted(needs_attention(res), key=lambda x: (_TAG_ORDER[x.tag], x.severity != Severity.FAIL))
    if not items:
        return []
    tags = sorted({x.tag for x in items if x.tag}, key=lambda t: _TAG_ORDER[t])
    head = name + (" · " + " ".join(f"@{t.value}" for t in tags) if tags else "")
    out = [head]
    for x in items:
        warn = "⚠ " if x.severity == Severity.SUSPECT else ""
        who = f" ({x.tag.value})" if x.tag and len(tags) > 1 else ""
        out.append(f"  • {warn}{x.message}{who}")
        out += [f"      – {row}" for row in x.details.get("rows", [])]
    return out


def render_report(results: list[tuple[str, SpotResult]]) -> list[str]:
    """All vessels needing attention, alphabetical; vessels without problems are left out."""
    out: list[str] = []
    for name, res in sorted(results, key=lambda r: r[0].lower()):
        block = render_vessel(name, res)
        if block:
            out += block
    return out

