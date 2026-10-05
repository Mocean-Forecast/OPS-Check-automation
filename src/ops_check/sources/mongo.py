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


def _scalar(v: Any) -> Any:
    """MATLAB-exported fields can hold {"_ArrayType_": ..., "_ArrayData_": ...} objects or
    empty arrays instead of plain values. Return the plain value, or None."""
    if isinstance(v, dict):
        data = v.get("_ArrayData_")
        if isinstance(data, list) and len(data) == 1:
            return data[0]
        return data if isinstance(data, (int, float, str)) else None
    if isinstance(v, list):
        return v[0] if len(v) == 1 else (None if not v else v)
    return v


def _str(v: Any) -> str | None:
    v = _scalar(v)
    return str(v).strip() or None if v is not None and not isinstance(v, list) else None


def _flag(v: Any) -> bool:
    v = _scalar(v)
    return str(v).strip().lower() in ("1", "1.0", "true", "yes")


def _parse_dt(v: Any) -> datetime | None:
    v = _scalar(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
        return datetime.fromtimestamp(v, timezone.utc)  # Unix seconds (lastModified)
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _clients(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(c).strip() for c in v if isinstance(c, (str, int)) and str(c).strip()]
    s = _str(v)
    return [s] if s else []


def vessel_from_doc(doc: dict[str, Any], s: Settings) -> Vessel:
    f = s.fields
    mmsi = _scalar(doc.get(f.mmsi))
    clients = _clients(doc.get(f.client))
    return Vessel(
        id=str(doc.get("_id", "")),
        mmsi=int(mmsi) if isinstance(mmsi, (int, float)) else None,
        name=_str(doc.get(f.name)) or "Unknown vessel",
        system=_str(doc.get(f.system)),
        operations_class=_str(doc.get(f.operations_class)),
        client=", ".join(clients) or None,
        on_hire=_flag(doc.get(f.on_hire)),
        clients=clients,
        daughtercraft=_flag(doc.get(f.daughtercraft)),
        site=_str(doc.get(f.site)),
    )


def registry_synced_at(docs: list[dict[str, Any]], s: Settings) -> datetime | None:
    """Newest `lastModified` over all vessel documents (D14)."""
    stamps = [d for d in (_parse_dt(doc.get(s.fields.synced_at)) for doc in docs) if d]
    return max(stamps) if stamps else None


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

    def _docs(self) -> list[dict[str, Any]]:
        if not hasattr(self, "_cache"):
            self._cache = list(self.db["vessels"].find({}))
        return self._cache

    def vessels(self) -> list[Vessel]:
        """All vessels, including excluded clients (filtered in run.execute, D25)."""
        return [vessel_from_doc(d, self.s) for d in self._docs()]

    def registry_synced_at(self) -> datetime | None:
        return registry_synced_at(self._docs(), self.s)

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
