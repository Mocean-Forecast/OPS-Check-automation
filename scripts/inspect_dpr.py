#!/usr/bin/env python3
"""Inspect MongoDB for the DPR spot-check fields (read-only).

Finds where DPR sign-off, fuel (measured vs entered), weather/Hs, transfers and
missed transfers live, by:
  1. listing candidate collections in every readable database and sampling their
     field structure (detecting which fields hold the vessel and the date);
  2. dumping the documents for the known test vessel-days;
  3. a presence matrix + value preview for all on-hire vessels for one DPR day
     (default: yesterday, Europe/Amsterdam) - i.e. "today's check".

Usage:
    export MONGO_ATLAS_URI="mongodb+srv://..."      # read-only user
    python scripts/inspect_dpr.py                    # test cases + yesterday
    python scripts/inspect_dpr.py --date 2026-10-10  # other DPR day
    python scripts/inspect_dpr.py --db bmo_dataviewer --db <portal_db>
    python scripts/inspect_dpr.py --skip-coverage    # test cases only

Output: out/inspect_dpr/<date>/  (summary.md is the file to read/share first)
Only reads. Every query has a time limit; timeouts are reported, not fatal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import math
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import ExecutionTimeout, OperationFailure, PyMongoError

# Known answers from the manual ops checks (vessel nicename, DPR day, expectation)
TEST_CASES = [
    ("SC Onyx", "2026-10-07", "fuel +47%; Hs missing during all turbine transfers"),
    ("NOS Bolder", "2026-10-04", "fuel 671 L measured vs 984 L entered"),
    ("MO8", "2026-10-04", "fuel 1111.3 vs 2456 L, multi-day trip"),
    ("Windcat 51", "2026-10-04", "fuel 1131.2 vs 1250 L; DPR filled + signed off"),
    ("Grampian Tweed", "2026-10-04", "SOV signed off by vessel; DP radius anomaly"),
    ("Wavemaster Horizon", "2026-10-04", "SOV not signed off"),
    ("SC Amethyst", "2026-10-03", "fuel 938.8 vs 984 L; filled, not signed off"),
    ("Windcat 50", "2026-10-03", "fuel entered missing; DPR not filled"),
    ("NOS Bringer", "2026-10-03", "fuel not filled in"),
    ("Grampian Kestrel", "2026-10-03", "SOV signed off by vessel"),
]

CANDIDATE = re.compile(
    r"dpr|transfer|mist|missed|operation|fuel|consum|weather|wave|general|stat|"
    r"timeline|nowcast|metocean|hermes|summary|vessel", re.I)
CORE = re.compile(r"dpr|transfer|mist|missed|operation|fuel|consum|weather|wave|general", re.I)
INTEREST = re.compile(
    r"fuel|consum|signed|sign_?off|^hs$|_hs|hs_|wave|weather|source|missed|mist|"
    r"transfer|pax|cargo|accessdaytype|remarks|comment|type|location|start|stop|end", re.I)
SKIP_DBS = {"admin", "local", "config"}
NAME_KEYS = {"vesselname", "nicename", "vessel", "vessel_name", "vesselnicename"}
MAX_LIST, MAX_STR, TIME_MS = 15, 300, 20_000


# ---------- helpers ----------
def unwrap(v):
    """MATLAB-exported {'_ArrayType_':..,'_ArrayData_':..} -> plain value."""
    if isinstance(v, dict) and "_ArrayData_" in v:
        d = v["_ArrayData_"]
        return d[0] if isinstance(d, list) and len(d) == 1 else d
    return v


def datenum(d: dt.date) -> int:
    return d.toordinal() + 366


def last_seg(path: str) -> str:
    return path.replace("[]", "").split(".")[-1].lower()


def qpath(path: str) -> str:
    return path.replace("[]", "")


def flatten(o, prefix="", out=None, depth=0):
    """Leaf paths -> first seen sample value. Lists descend into element 0."""
    out = {} if out is None else out
    if depth > 7:
        return out
    o = unwrap(o)
    if isinstance(o, dict):
        for k, v in o.items():
            flatten(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    elif isinstance(o, list):
        if o:
            flatten(o[0], prefix + "[]", out, depth + 1)
        else:
            out.setdefault(prefix + "[]", "<empty list>")
    else:
        out.setdefault(prefix, o)
    return out


def date_kind(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        if 6e5 < v < 8e5:
            return "datenum"
        if 1e9 < v < 1e10:
            return "unix_s"
        if 1e12 < v < 1e13:
            return "unix_ms"
        return None
    if isinstance(v, dt.datetime):
        return "datetime"
    if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
        return "iso"
    return None


def detect_keys(fields: dict):
    vkeys, dkeys = [], []
    for p, v in fields.items():
        seg = last_seg(p)
        if "mmsi" in seg and isinstance(v, (int, float, str)) and not isinstance(v, bool):
            vkeys.append((p, "mmsi"))
        elif seg in NAME_KEYS and isinstance(v, str):
            vkeys.append((p, "name"))
        if any(s in seg for s in ("date", "day", "datenum", "time", "start")):
            k = date_kind(v)
            if k:
                dkeys.append((p, k))
    # prefer top-level keys, keep the query small
    dkeys.sort(key=lambda x: (x[0].count(".") + x[0].count("[]"), x[0]))
    return vkeys[:4], dkeys[:4]


def date_cond(path, kind, d: dt.date):
    q = qpath(path)
    start = dt.datetime(d.year, d.month, d.day)  # naive = UTC for pymongo
    end = start + dt.timedelta(days=1)
    s_ts = start.replace(tzinfo=dt.timezone.utc).timestamp()
    if kind == "datenum":
        return {q: {"$gte": datenum(d), "$lt": datenum(d) + 1}}
    if kind == "unix_s":
        return {q: {"$gte": s_ts, "$lt": s_ts + 86400}}
    if kind == "unix_ms":
        return {q: {"$gte": s_ts * 1000, "$lt": (s_ts + 86400) * 1000}}
    if kind == "datetime":
        return {q: {"$gte": start, "$lt": end}}
    return {q: {"$regex": "^" + d.isoformat()}}


def vessel_cond(path, kind, vessel):
    q = qpath(path)
    if kind == "mmsi":
        return {q: {"$in": [vessel["mmsi"], str(vessel["mmsi"])]}}
    names = [n for n in (vessel.get("nicename"), vessel.get("vesselname")) if n]
    return {q: {"$in": names}} if names else None


def sanitize(o, depth=0):
    if depth > 9:
        return "…"
    if isinstance(o, dict):
        return {k: sanitize(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list):
        head = [sanitize(x, depth + 1) for x in o[:MAX_LIST]]
        return head + ([f"… {len(o) - MAX_LIST} more"] if len(o) > MAX_LIST else [])
    if isinstance(o, str) and len(o) > MAX_STR:
        return o[:MAX_STR] + "…"
    if isinstance(o, (bytes, bytearray)):
        return f"<{len(o)} bytes>"
    return o


def num_list_summary(lst):
    vals = [unwrap(x) for x in lst]
    nums = [x for x in vals if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if not vals or len(nums) < len(vals) * 0.8:
        return None
    finite = [x for x in nums if not math.isnan(x)]
    missing = len(vals) - len(finite)
    zeros = sum(1 for x in finite if x == 0)
    rng = f"min {min(finite):g} max {max(finite):g}" if finite else "no finite values"
    return f"{len(vals)} values, {missing} missing/NaN, {zeros} zero, {rng}"


def preview(o, prefix="", out=None, depth=0):
    """Interesting fields with values; numeric series summarised (for Hs gaps)."""
    out = [] if out is None else out
    if depth > 7 or len(out) > 60:
        return out
    o = unwrap(o)
    if isinstance(o, dict):
        for k, v in o.items():
            preview(v, f"{prefix}.{k}" if prefix else k, out, depth + 1)
    elif isinstance(o, list):
        if INTEREST.search(last_seg(prefix) or "") or INTEREST.search(prefix):
            s = num_list_summary(o)
            if s:
                out.append((prefix + "[]", s))
                return out
        if o and isinstance(unwrap(o[0]), dict):
            out.append((prefix + "[]", f"list of {len(o)} objects"))
            for i, el in enumerate(o[:3]):
                preview(el, f"{prefix}[{i}]", out, depth + 1)
        elif o:
            s = num_list_summary(o)
            if s and INTEREST.search(prefix):
                out.append((prefix + "[]", s))
    else:
        if INTEREST.search(last_seg(prefix)) or INTEREST.search(prefix):
            v = o if not isinstance(o, str) or len(o) < 120 else o[:120] + "…"
            out.append((prefix, repr(v)))
    return out


def safe_find(coll, query, limit):
    try:
        return list(coll.find(query, max_time_ms=TIME_MS).limit(limit)), None
    except ExecutionTimeout:
        return [], "timeout"
    except (OperationFailure, PyMongoError) as e:
        return [], f"error: {str(e)[:120]}"


def vessel_day_query(meta, vessel, d):
    vc = [c for c in (vessel_cond(p, k, vessel) for p, k in meta["vkeys"]) if c]
    dc = [date_cond(p, k, d) for p, k in meta["dkeys"]]
    if not vc or not dc:
        return None
    return {"$and": [{"$or": vc}, {"$or": dc}]}


# ---------- main steps ----------
def discover(client, dbs, all_colls, out: Path):
    metas, lines = [], ["# Collections\n"]
    for dbname in dbs:
        db = client[dbname]
        try:
            names = sorted(db.list_collection_names())
        except PyMongoError as e:
            lines.append(f"\n## {dbname}\n\n_cannot list collections: {e}_\n")
            continue
        lines.append(f"\n## {dbname} ({len(names)} collections)\n")
        lines.append("Not sampled (name not matching): " +
                     ", ".join(n for n in names if not (all_colls or CANDIDATE.search(n))) + "\n")
        for name in names:
            if not (all_colls or CANDIDATE.search(name)):
                continue
            coll = db[name]
            try:
                est = coll.estimated_document_count()
                docs = list(coll.aggregate([{"$sample": {"size": 3}}], maxTimeMS=TIME_MS))
            except PyMongoError as e:
                lines.append(f"\n### {name}\n\n_sample failed: {str(e)[:120]}_\n")
                continue
            fields = {}
            for d in docs:
                flatten(d, out=fields)
            vkeys, dkeys = detect_keys(fields)
            meta = {"db": dbname, "name": name, "coll": coll, "est": est,
                    "vkeys": vkeys, "dkeys": dkeys, "core": bool(CORE.search(name))}
            metas.append(meta)
            lines.append(f"\n### {dbname}.{name}  (~{est:,} docs)\n")
            lines.append(f"- vessel keys: {vkeys or 'NONE FOUND'}")
            lines.append(f"- date keys: {dkeys or 'NONE FOUND'}\n")
            lines.append("| field | type | sample |\n|---|---|---|")
            for p, v in list(fields.items())[:120]:
                sv = repr(v)
                sv = sv if len(sv) < 70 else sv[:70] + "…"
                lines.append(f"| `{p}` | {type(v).__name__} | {sv.replace('|', '/')} |")
            if len(fields) > 120:
                lines.append(f"\n_{len(fields) - 120} more fields not shown_")
    (out / "collections.md").write_text("\n".join(lines))
    return metas


def load_vessels(client):
    vs = []
    for v in client["bmo_dataviewer"]["vessels"].find({}):
        rec = {k: unwrap(v.get(k)) for k in
               ("mmsi", "nicename", "vesselname", "operationsClass", "onHire", "client", "isDaughterCraft")}
        if rec["mmsi"] is not None:
            try:
                rec["mmsi"] = int(rec["mmsi"])
            except (TypeError, ValueError):
                pass
        vs.append(rec)
    return vs


def find_vessel(vessels, name):
    n = name.lower().replace(" ", "")
    for v in vessels:
        for key in ("nicename", "vesselname"):
            if str(v.get(key) or "").lower().replace(" ", "").replace("_", "") == n.replace("_", ""):
                return v
    return None


def run_test_cases(metas, vessels, out: Path, limit):
    lines = ["# Test cases\n"]
    for vname, day, expect in TEST_CASES:
        d = dt.date.fromisoformat(day)
        v = find_vessel(vessels, vname)
        lines.append(f"\n## {vname} — {day}\n\nExpected: {expect}\n")
        if not v:
            lines.append("**Vessel not found in `vessels`.**\n")
            continue
        lines.append(f"mmsi {v['mmsi']}, class {v['operationsClass']}, datenum {datenum(d)}\n")
        case_dir = out / "testcases" / f"{vname.replace(' ', '_')}_{day}"
        for m in metas:
            q = vessel_day_query(m, v, d)
            if q is None:
                continue
            docs, err = safe_find(m["coll"], q, limit)
            if err:
                lines.append(f"- `{m['db']}.{m['name']}`: {err}")
                continue
            if not docs:
                continue
            case_dir.mkdir(parents=True, exist_ok=True)
            f = case_dir / f"{m['db']}.{m['name']}.json"
            f.write_text(json_util.dumps(sanitize(docs), indent=2))
            lines.append(f"\n### `{m['db']}.{m['name']}` — {len(docs)} doc(s) → `{f.relative_to(out)}`\n")
            for p, val in preview(docs[0])[:60]:
                lines.append(f"- `{p}`: {val}")
    (out / "testcases.md").write_text("\n".join(lines))


def run_coverage(metas, vessels, d: dt.date, out: Path):
    on_hire = [v for v in vessels if v.get("onHire") in (1, True, "1")]
    cols = [m for m in metas if m["core"] and m["vkeys"] and m["dkeys"]]
    rows, previews = [], ["# Value preview — " + d.isoformat() + "\n"]
    for i, v in enumerate(sorted(on_hire, key=lambda x: str(x.get("nicename")))):
        print(f"  [{i + 1}/{len(on_hire)}] {v.get('nicename')}", file=sys.stderr)
        row = {"vessel": v.get("nicename"), "mmsi": v.get("mmsi"),
               "class": v.get("operationsClass"), "DC": v.get("isDaughterCraft")}
        previews.append(f"\n## {v.get('nicename')} ({v.get('operationsClass')}, {v.get('mmsi')})\n")
        for m in cols:
            key = f"{m['db']}.{m['name']}"
            docs, err = safe_find(m["coll"], vessel_day_query(m, v, d), 3)
            row[key] = err or len(docs)
            if docs:
                previews.append(f"\n### `{key}` ({len(docs)})\n")
                previews += [f"- `{p}`: {val}" for p, val in preview(docs[0])[:40]]
        rows.append(row)
    if rows:
        with open(out / f"coverage_{d.isoformat()}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    (out / f"preview_{d.isoformat()}.md").write_text("\n".join(previews))
    return rows, cols


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="DPR day for the coverage run (default: yesterday NL)")
    ap.add_argument("--db", action="append", help="database(s) to inspect (default: all readable)")
    ap.add_argument("--all-collections", action="store_true", help="sample every collection")
    ap.add_argument("--skip-coverage", action="store_true")
    ap.add_argument("--limit", type=int, default=5, help="docs per collection per test case")
    args = ap.parse_args()

    uri = os.environ.get("MONGO_ATLAS_URI")
    if not uri:
        sys.exit("Set MONGO_ATLAS_URI (read-only user).")
    day = (dt.date.fromisoformat(args.date) if args.date
           else dt.datetime.now(ZoneInfo("Europe/Amsterdam")).date() - dt.timedelta(days=1))
    out = Path("out/inspect_dpr") / day.isoformat()
    out.mkdir(parents=True, exist_ok=True)

    client = MongoClient(uri, serverSelectionTimeoutMS=15_000,
                         datetime_conversion="DATETIME_AUTO")  # data has year<1 dates
    if args.db:
        dbs = args.db
    else:
        try:
            dbs = [n for n in client.list_database_names() if n not in SKIP_DBS]
        except PyMongoError:
            print("cannot list databases; using bmo_dataviewer only (add --db for others)", file=sys.stderr)
            dbs = ["bmo_dataviewer"]

    print(f"Databases: {dbs}", file=sys.stderr)
    metas = discover(client, dbs, args.all_collections, out)
    vessels = load_vessels(client)
    print(f"{len(metas)} collections sampled, {len(vessels)} vessels", file=sys.stderr)

    print("Test cases…", file=sys.stderr)
    run_test_cases(metas, vessels, out, args.limit)

    rows, cols = ([], [])
    if not args.skip_coverage:
        print(f"Coverage for {day}…", file=sys.stderr)
        rows, cols = run_coverage(metas, vessels, day, out)

    s = [f"# Inspect DPR — run {dt.datetime.now():%Y-%m-%d %H:%M}, DPR day {day} (datenum {datenum(day)})\n",
         f"Databases: {', '.join(dbs)}  ·  vessels: {len(vessels)}  ·  collections sampled: {len(metas)}\n",
         "\n## Collections and detected keys\n",
         "| collection | ~docs | vessel key | date key |\n|---|---|---|---|"]
    for m in metas:
        s.append(f"| `{m['db']}.{m['name']}` | {m['est']:,} | "
                 f"{', '.join(p for p, _ in m['vkeys']) or '—'} | "
                 f"{', '.join(f'{p} ({k})' for p, k in m['dkeys']) or '—'} |")
    if rows:
        s.append(f"\n## Presence on {day} — on-hire vessels with a doc per collection\n")
        s.append("| collection | vessels with doc | timeouts/errors |\n|---|---|---|")
        for m in cols:
            key = f"{m['db']}.{m['name']}"
            hit = sum(1 for r in rows if isinstance(r[key], int) and r[key] > 0)
            bad = sum(1 for r in rows if isinstance(r[key], str))
            s.append(f"| `{key}` | {hit}/{len(rows)} | {bad} |")
    s.append("\nFiles: `collections.md` (field structure), `testcases.md` + `testcases/` (known vessel-days), "
             f"`coverage_{day}.csv` and `preview_{day}.md` (all on-hire vessels).")
    (out / "summary.md").write_text("\n".join(s))
    print(f"\nDone → {out}/summary.md", file=sys.stderr)


if __name__ == "__main__":
    main()
