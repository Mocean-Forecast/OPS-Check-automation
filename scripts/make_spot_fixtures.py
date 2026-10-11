#!/usr/bin/env python3
"""Dump the spot-check input (SpotInput) for the test vessel-days to tests/fixtures/spot/.

Read-only. The dumps hold the documents as they are NOW; tests/test_spot.py applies the
state at the 11:00 run on top (DPRs filled or signed later, nowcast rewritten).

    MONGO_URI=... python scripts/make_spot_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pymongo import MongoClient  # noqa: E402

from ops_check.models import Vessel  # noqa: E402
from ops_check.sources.spot_mongo import SpotSource  # noqa: E402

CASES = [  # (vessel nicename, DPR day) - DPR day = check day - 1
    ("SC Onyx", "2026-10-07"), ("NOS Bolder", "2026-10-03"), ("MO8", "2026-10-03"),
    ("Windcat 51", "2026-10-03"), ("Grampian Tweed", "2026-10-03"), ("Wavemaster Horizon", "2026-10-03"),
    ("SC Amethyst", "2026-10-02"), ("Windcat 50", "2026-10-02"), ("NOS Bringer", "2026-10-02"),
    ("Grampian Kestrel", "2026-10-02"),
    # extra cases
    ("NOS Bringer", "2026-10-03"), ("Grampian Kestrel", "2026-10-04"), ("NOS Proceeder", "2026-10-10"),
    # transit vessels (vMRU continuity)
    ("Bloemgracht", "2026-10-10"), ("Xin Yao Hua", "2026-10-10"), ("Brouwersgracht", "2026-10-10"),
]


def redact(dpr: dict | None) -> dict | None:
    """Keep only empty / non-empty for people's addresses and free text."""
    if not dpr:
        return dpr
    for key in ("signOff", "signedOff"):
        for k, v in (dpr.get(key) or {}).items():
            if isinstance(v, str) and v.strip():
                dpr[key][k] = "redacted" if "@" in v else v
    if str(dpr.get("remarks") or "").strip():
        dpr["remarks"] = "(remarks redacted)"
    return dpr


def main() -> None:
    uri = os.environ.get("MONGO_URI") or os.environ.get("MONGO_ATLAS_URI")
    if not uri:
        sys.exit("Set MONGO_URI (read-only user).")
    client = MongoClient(uri, serverSelectionTimeoutMS=15_000, datetime_conversion="DATETIME_AUTO")
    legacy = client["bmo_dataviewer"]
    src = SpotSource(legacy, client["portal"])
    out = ROOT / "tests" / "fixtures" / "spot"
    out.mkdir(parents=True, exist_ok=True)
    for name, day in CASES:
        v = legacy["vessels"].find_one({"nicename": name})
        vs = Vessel(str(v["_id"]), v["mmsi"], name, None, v["operationsClass"], None, True)
        inp = src.load([vs], date.fromisoformat(day))[v["mmsi"]]
        inp.dpr = redact(inp.dpr)
        f = out / f"{name.replace(' ', '_')}_{day}.json"
        f.write_text(json.dumps({"vessel": name, "day": day, "input": inp.to_json()}, indent=1, default=str))
        print(f.relative_to(ROOT))


if __name__ == "__main__":
    main()
