"""MongoDB reads, ported from Sjoerd's portal-api (routes/vessels.js), see D10.

Read-only. Collections in `bmo_dataviewer`:
    vessels, operationsStats, general, SOV_general, timeTraces, CTV_dprInput, SOV_dprInput
Days are integer MATLAB datenums; a day's records are matched with [day, day + 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from ..checks import dpr as dpr_rules
from ..config import Settings
from ..datenum import to_datenum
from ..models import DprInfo, Vessel


def _parse_date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _parse_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def vessel_from_doc(doc: dict[str, Any], s: Settings) -> Vessel:
    f = s.fields
    prio = doc.get(f.priority)
    return Vessel(
        id=str(doc.get("_id", "")),
        mmsi=doc.get(f.mmsi),
        name=doc.get(f.name) or "Unknown vessel",
        system=doc.get(f.system) or None,
        operations_class=doc.get(f.operations_class),
        client=doc.get(f.client),
        on_hire=doc.get(f.on_hire) == 1,
        status=doc.get(f.status),
        status_reason=doc.get(f.status_reason),
        status_owner=doc.get(f.status_owner),
        status_expiry=_parse_date(doc.get(f.status_expiry)),
        priority=bool(prio) and str(prio).lower() not in ("0", "false", "no"),
    )


@dataclass
class DayData:
    """MongoDB evidence for all vessels on one day, keyed by mmsi."""

    ops_stats: dict[int, dict] = field(default_factory=dict)
    general: set[int] = field(default_factory=set)
    sov_general: set[int] = field(default_factory=set)
    ctv_dpr: dict[int, dict] = field(default_factory=dict)
    sov_dpr: dict[int, dict] = field(default_factory=dict)
    transit_gaps: dict[int, tuple[float | None, int]] = field(default_factory=dict)  # mmsi -> (max gap min, n)

    def dpr(self, v: Vessel) -> DprInfo | None:
        if v.mmsi is None or v.operations_class not in ("CTV", "OSV"):
            return None
        doc = (self.ctv_dpr if v.operations_class == "CTV" else self.sov_dpr).get(v.mmsi)
        return DprInfo(dpr_rules.dpr_status(v.operations_class, doc))

    def processed(self, v: Vessel) -> bool:
        if v.mmsi is None:
            return False
        if v.operations_class == "CTV":
            return v.mmsi in self.general
        if v.operations_class == "OSV":
            return v.mmsi in self.sov_general
        if v.operations_class == "transit":
            gap = self.transit_gaps.get(v.mmsi)
            return bool(gap and gap[1] > 0)
        return v.mmsi in self.general or v.mmsi in self.sov_general


class MongoSource:
    def __init__(self, db: Any, settings: Settings):
        self.db = db
        self.s = settings

    @classmethod
    def connect(cls, settings: Settings) -> "MongoSource":
        from pymongo import MongoClient  # imported lazily so tests don't need pymongo

        client = MongoClient(settings.mongo_uri, appname="ops-check", serverSelectionTimeoutMS=15000)
        return cls(client[settings.mongo_db], settings)

    # ------------------------------------------------------------ registry

    def vessels(self) -> list[Vessel]:
        """All vessels, including excluded clients (filtered in run.execute, D25)."""
        return [vessel_from_doc(d, self.s) for d in self.db["vessels"].find({})]

    def registry_synced_at(self) -> datetime | None:
        f = self.s.fields
        doc = self.db["vessels"].find_one({f.synced_at: {"$exists": True}}, sort=[(f.synced_at, -1)])
        return _parse_dt(doc.get(f.synced_at)) if doc else None

    # ------------------------------------------------------------ per day

    def day_data(self, vessels: list[Vessel], day: date) -> DayData:
        n = to_datenum(day)
        rng = {"$gte": n, "$lt": n + 1}
        by_class: dict[str, list[int]] = {}
        for v in vessels:
            if v.mmsi is not None:
                by_class.setdefault(v.operations_class or "", []).append(v.mmsi)
        all_mmsi = [m for ms in by_class.values() for m in ms]
        ctv, osv, transit = by_class.get("CTV", []), by_class.get("OSV", []), by_class.get("transit", [])
        dd = DayData()

        for d in self.db["operationsStats"].find(
                {"mmsi": {"$in": all_mmsi}, "date": rng},
                {"mmsi": 1, "hasRecordedCode": 1, "hasMissingDataCode": 1, "clients": 1}):
            dd.ops_stats.setdefault(d["mmsi"], d)
        if ctv:
            dd.general = {d["mmsi"] for d in self.db["general"].find(
                {"mmsi": {"$in": ctv}, "date": rng}, {"mmsi": 1})}
            for d in self.db["CTV_dprInput"].find(
                    {"mmsi": {"$in": ctv}, "date": rng}, {"mmsi": 1, "accessDayType": 1, "remarks": 1}):
                dd.ctv_dpr.setdefault(d["mmsi"], d)
        if osv:
            dd.sov_general = {d["mmsi"] for d in self.db["SOV_general"].find(
                {"mmsi": {"$in": osv}, "date": rng}, {"mmsi": 1})}
            for d in self.db["SOV_dprInput"].find(
                    {"mmsi": {"$in": osv}, "date": rng},
                    {"mmsi": 1, "signedOff.amount": 1, "accessDayType.status": 1, "remarks": 1}):
                dd.sov_dpr.setdefault(d["mmsi"], d)
        for m in transit:
            dd.transit_gaps[m] = self._transit_gap(m, n)
        return dd

    def _transit_gap(self, mmsi: int, n: int) -> tuple[float | None, int]:
        """Max gap (minutes) between consecutive samples of one sensor/signal on the day."""
        match: dict[str, Any] = {
            "stream.mmsi": mmsi,
            "stream.sensorId": {"$ne": "unknown"},
            "datenum": {"$gte": n, "$lt": n + 1},
        }
        if self.s.transit_signal_id is not None:
            match["stream.signalId"] = self.s.transit_signal_id
        else:
            match["stream.signalId"] = {"$exists": True, "$ne": None}
        col = self.db["timeTraces"]
        first = col.find_one(match, {"stream.sensorId": 1, "stream.signalId": 1})
        if not first:
            return None, 0
        match["stream.sensorId"] = first["stream"]["sensorId"]
        match["stream.signalId"] = first["stream"]["signalId"]
        stamps = sorted(d["datenum"] for d in col.find(match, {"datenum": 1}))
        if len(stamps) < 2:
            return None, len(stamps)
        gap = max(b - a for a, b in zip(stamps, stamps[1:]))
        return gap * 1440, len(stamps)
