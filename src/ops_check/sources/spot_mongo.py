"""MongoDB reads for the DPR spot check (field mapping: docs/HANDOVER-spot-check.md).

Read-only, batched per collection over all vessels of the day. Legacy = bmo_dataviewer,
portal = the new portal's `portal` database on the same cluster.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..checks.spot import MissedTransfer, SpotInput, WaveSeries
from ..datenum import from_datenum_utc, to_datenum
from ..models import Vessel

TIME_MS = 30_000


def _utc(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str) and v:
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _f(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def _i(v: Any) -> int:
    return int(_f(v) or 0)


def flatten_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events or []:
        det = {x.get("name"): x.get("content") for x in e.get("details") or [] if isinstance(x, dict)}
        out.append({"event_type": e.get("event_type"), "event_name": e.get("event_name"),
                    "start_time": e.get("start_time"), "end_time": e.get("end_time"),
                    "details": {k: det.get(k) for k in ("from", "to", "port", "field", "fuel usage",
                                                         "Hs", "wave source") if k in det}})
    return out


def _hm(day: date, part: dict[str, Any] | None) -> datetime | None:
    """SOV {hour, minutes} strings on the DPR day, UTC (as the CTV rows are). '24:00' = next day."""
    if not part or part.get("hour") in (None, ""):
        return None
    h, m = _i(part.get("hour")), _i(part.get("minutes"))
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(hours=h, minutes=m)


def missed_ctv(rows: list[dict[str, Any]]) -> list[MissedTransfer]:
    out = []
    for r in rows or []:
        if not r or not any(r.get(k) for k in ("location", "paxIn", "paxOut", "cargoIn", "cargoOut")):
            continue
        out.append(MissedTransfer(
            str(r.get("location") or "?").strip(), _utc(r.get("startTimeIso")), _utc(r.get("stopTimeIso")),
            _i(r.get("paxIn")), _i(r.get("paxOut")), _i(r.get("cargoIn")), _i(r.get("cargoOut")),
            " / ".join(x for x in (str(r.get("comment") or "").strip(), str(r.get("otherComment") or "").strip()) if x)))
    return out


def missed_sov(rows: list[dict[str, Any]], day: date, kind: str) -> list[MissedTransfer]:
    out = []
    for r in rows or []:
        if not r or not any(r.get(k) for k in ("location", "paxIn", "paxOut", "cargoIn", "cargoOut")):
            continue
        out.append(MissedTransfer(str(r.get("location") or "?").strip(), _hm(day, r.get("from")), _hm(day, r.get("to")),
                                  _i(r.get("paxIn")), _i(r.get("paxOut")), _i(r.get("cargoIn")), _i(r.get("cargoOut")),
                                  kind=kind))
    return out


class SpotSource:
    def __init__(self, legacy: Any, portal: Any):
        self.b, self.p = legacy, portal

    @classmethod
    def from_legacy_db(cls, db: Any) -> "SpotSource":
        return cls(db, db.client["portal"])

    def _find(self, coll: Any, q: dict, proj: dict | None = None) -> list[dict]:
        return list(coll.find(q, proj, max_time_ms=TIME_MS))

    def load(self, vessels: list[Vessel], day: date) -> dict[int, SpotInput]:
        n = to_datenum(day)
        ctv = [v.mmsi for v in vessels if v.mmsi is not None and v.operations_class == "CTV"]
        osv = [v.mmsi for v in vessels if v.mmsi is not None and v.operations_class == "OSV"]
        transit = [v.mmsi for v in vessels if v.mmsi is not None and v.operations_class == "transit"]
        allm = ctv + osv
        out: dict[int, SpotInput] = {m: SpotInput("CTV") for m in ctv} | {m: SpotInput("OSV") for m in osv}
        for m in transit:
            out[m] = self._transit(m, day)
        if not allm:
            return out

        for d in self._find(self.b["CTV_dprInput"], {"mmsi": {"$in": ctv}, "date": n}):
            out[d["mmsi"]].dpr = out[d["mmsi"]].dpr or _clean(d)
        for d in self._find(self.b["SOV_dprInput"], {"mmsi": {"$in": osv}, "date": n}):
            out[d["mmsi"]].dpr = out[d["mmsi"]].dpr or _clean(d)

        for m, inp in out.items():
            if inp.dpr is None:
                coll = self.b["CTV_dprInput" if inp.vessel_type == "CTV" else "SOV_dprInput"]
                last = coll.find_one({"mmsi": m, "date": {"$lt": n}}, {"date": 1}, sort=[("date", -1)],
                                     max_time_ms=TIME_MS)
                if last:
                    inp.last_dpr_day = from_datenum_utc(last["date"]).date().isoformat()

        for d in self._find(self.p["analyticsVesselEquipments"],
                            {"mmsi": {"$in": allm}, "matlabDate": n, "equipmentType": "engine",
                             "engineId": "combined"}, {"mmsi": 1, "fuelTotalM3": 1}):
            v = _f(d.get("fuelTotalM3"))
            if v is not None:
                out[d["mmsi"]].fuel_measured_l = round(v * 1000, 1)

        for d in self._find(self.p["analyticsDprTimeline"], {"mmsi": {"$in": allm}, "matlabDate": n},
                            {"mmsi": 1, "events": 1, "startOfDay": 1, "endOfDay": 1}):
            inp = out[d["mmsi"]]
            inp.timeline = flatten_events(d.get("events"))
            inp.data_start, inp.data_end = _utc(d.get("startOfDay")), _utc(d.get("endOfDay"))
        for d in self._find(self.p["analyticsGeneralOverviews"], {"mmsi": {"$in": allm}, "matlabDate": n},
                            {"mmsi": 1, "route.timeStamps": 1, "timeArrivalHarbour": 1}):
            ts = [t for t in ((d.get("route") or {}).get("timeStamps") or []) if _f(t)]
            inp = out[d["mmsi"]]
            if ts:
                inp.route_start = datetime.fromtimestamp(min(ts), timezone.utc)
                inp.route_end = datetime.fromtimestamp(max(ts), timezone.utc)
            inp.harbour_arrival = _utc(d.get("timeArrivalHarbour"))

        # transfers log (what the crew sees on the transfers page)
        for d in self._find(self.b["transfers"], {"mmsi": {"$in": ctv}, "date": {"$gte": n, "$lt": n + 1},
                                                  "detector": {"$in": ["docking", "impact"]}},
                            {"mmsi": 1, "startTime": 1, "stopTime": 1, "location": 1}):
            out[d["mmsi"]].logged.append(_logged(d))
        for d in self._find(self.b["SOV_turbineTransfers"], {"mmsi": {"$in": osv}, "date": n},
                            {"mmsi": 1, "startTime": 1, "stopTime": 1, "location": 1}):
            out[d["mmsi"]].logged.append(_logged(d))
        for inp in out.values():
            inp.logged = sorted((x for x in inp.logged if x["start"]), key=lambda x: x["start"])

        # missed transfers
        for d in self._find(self.b["missedTransfers"], {"mmsi": {"$in": ctv}, "date": n}):
            out[d["mmsi"]].missed += missed_ctv(d.get("missedTransfers"))
        for m in osv:
            out[m].missed += missed_sov((out[m].dpr or {}).get("missedPaxCargo"), day, "turbine")
        for d in self._find(self.b["SOV_vessel2vesselTransfers"], {"mmsi": {"$in": osv}, "date": n},
                            {"mmsi": 1, "missedTransfers": 1}):
            out[d["mmsi"]].missed += missed_sov(d.get("missedTransfers"), day, "v2v")

        # wave: the source named by ops stats / the timeline (Hermess, client feeds); SOVs without
        # one use their onboard series (SOV_general.weatherConditions: Furuno, Miros, Nextocean, ...)
        src_ids: dict[int, str] = {}
        for inp in out.values():
            inp.recorded = False
        for d in self._find(self.b["operationsStats"], {"mmsi": {"$in": allm}, "date": n},
                            {"mmsi": 1, "waveSourceId": 1}):
            out[d["mmsi"]].recorded = True
            if d.get("waveSourceId"):
                src_ids.setdefault(d["mmsi"], d["waveSourceId"])
        for m in allm:
            if m not in src_ids:
                s = next((e["details"].get("wave source") for e in out[m].timeline
                          if e["details"].get("wave source")), None)
                if s:
                    src_ids[m] = s
        if src_ids:
            self._wave_from_sources(out, src_ids, n)
        onboard = [m for m in osv if m not in src_ids]
        for d in self._find(self.b["SOV_general"], {"mmsi": {"$in": onboard}, "date": n},
                            {"mmsi": 1, "weatherConditions": 1}) if onboard else []:
            wc = d.get("weatherConditions") or {}
            t, hs = wc.get("time") or [], wc.get("waveHs") or []
            samples = ([(from_datenum_utc(x), _f(h), True) for x, h in zip(t, hs) if _f(x)]
                       if isinstance(t, list) else [])
            out[int(d["mmsi"])].wave = WaveSeries(str(wc.get("wavesource") or "onboard"), None, 15, samples)
        return out

    def _transit(self, mmsi: int, day: date) -> SpotInput:
        """vMRU traces (timeTraces, 15 min) for the day and up to now, the sensors seen in the week
        before, the newest trace of any sensor, and the active DPR shipping project."""
        n = to_datenum(day)
        inp = SpotInput("transit")
        tt = self.b["timeTraces"]
        vm = {"stream.mmsi": mmsi, "stream.sensorId": {"$regex": "^vMRU"}}
        for d in tt.aggregate([{"$match": {**vm, "datenum": {"$gte": n, "$lt": n + 2}}},
                               {"$group": {"_id": "$stream.sensorId", "t": {"$addToSet": "$datenum"}}}],
                              maxTimeMS=TIME_MS):
            inp.vmru[d["_id"]] = sorted(from_datenum_utc(x) for x in d["t"])
        inp.vmru_expected = sorted(tt.distinct("stream.sensorId", {**vm, "datenum": {"$gte": n - 7, "$lt": n}},
                                               maxTimeMS=TIME_MS))
        last = tt.find_one({"stream.mmsi": mmsi}, {"datenum": 1}, sort=[("datenum", -1)], max_time_ms=TIME_MS)
        inp.last_trace = from_datenum_utc(last["datenum"]) if last else None
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        for p in self._find(self.b["DPR_shippingprojects"], {"mmsi": mmsi}):
            s, e = _utc(str(p.get("startDate") or "")), _utc(str(p.get("endDate") or "").replace("-T", "T"))
            if s and s < start + timedelta(days=1) and (e is None or e >= start):
                if inp.project is None or s > _utc(inp.project["start"]):
                    inp.project = {"name": p.get("projectName"), "start": s.isoformat(),
                                   "end": e.isoformat() if e else None,
                                   "origin": (p.get("origin") or {}).get("name"),
                                   "destination": (p.get("destination") or {}).get("name")}
        return inp

    def _wave_from_sources(self, out: dict[int, SpotInput], need: dict[int, str], n: int) -> None:
        ids = sorted(set(need.values()))
        portal = {d["dataviewerSourceId"]: d for d in self._find(
            self.p["analyticsMeteoSources"], {"dataviewerSourceId": {"$in": ids}},
            {"dataviewerSourceId": 1, "dataSourceDisplayName": 1, "outputResolutionMinutes": 1, "name": 1})}
        series: dict[str, list] = {}
        if portal:
            by_mongo = {str(p["_id"]): sid for sid, p in portal.items()}
            for d in self._find(self.p["analyticsMeteoData"],
                                {"sourceMongoId": {"$in": list(by_mongo)}, "matlabDate": {"$gte": n - 1, "$lte": n + 1}},
                                {"sourceMongoId": 1, "waveData": 1}):
                w = d.get("waveData") or {}
                rel = w.get("meteoIsReliable") or []
                for i, (t, h) in enumerate(zip(w.get("timeStamp") or [], w.get("hs") or [])):
                    ts = _utc(t)
                    if ts:
                        series.setdefault(by_mongo[d["sourceMongoId"]], []).append(
                            (ts, _f(h), bool(rel[i]) if i < len(rel) else True))
        legacy = {}
        try:
            from bson import ObjectId
            legacy = {str(d["_id"]): d for d in self._find(
                self.b["waveSources"], {"_id": {"$in": [ObjectId(i) for i in ids if len(i) == 24]}},
                {"name": 1, "provider": 1, "info": 1})}
        except Exception:  # noqa: BLE001 - names are context only
            pass
        for m, sid in need.items():
            p = portal.get(sid)
            if p and series.get(sid):
                out[m].wave = WaveSeries(p.get("dataSourceDisplayName") or p.get("name") or sid, sid,
                                         float(p.get("outputResolutionMinutes") or 15), sorted(series[sid]))
            else:
                l = legacy.get(sid) or {}
                out[m].wave_source_hint = (f"{l.get('name')}, {l.get('provider')}" if l
                                           else (p or {}).get("dataSourceDisplayName") or sid)


def _logged(d: dict[str, Any]) -> dict[str, Any]:
    st, sp = _f(d.get("startTime")), _f(d.get("stopTime"))
    return {"start": from_datenum_utc(st) if st else None, "stop": from_datenum_utc(sp) if sp else None,
            "location": str(d.get("location") or "")}


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    keep = ("mmsi", "date", "accessDayType", "remarks", "consumptionList", "consumption", "signOff",
            "signedOff", "missedPaxCargo", "lastModified", "schemaVersion")
    return {k: d[k] for k in keep if k in d}
