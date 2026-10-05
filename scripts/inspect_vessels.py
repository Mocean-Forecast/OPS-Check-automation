"""List the fields that exist in the MongoDB `vessels` collection.

Run from the repo root (uses MONGO_URI / MONGO_DB from .env):

    python scripts/inspect_vessels.py            # field names, how many docs have them, type, example
    python scripts/inspect_vessels.py --sample   # also print one full document (check before sharing)

Read-only: only find() on `vessels`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def short(v: object, n: int = 40) -> str:
    s = json.dumps(v, default=str, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="print one full document")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    load_env(Path(".env"))
    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("MONGO_URI not set (.env)", file=sys.stderr)
        return 1
    from pymongo import MongoClient

    db = MongoClient(uri, serverSelectionTimeoutMS=15000, appname="ops-check-inspect")[
        os.environ.get("MONGO_DB") or "bmo_dataviewer"
    ]
    col = db["vessels"]
    total = col.count_documents({})
    counts: Counter[str] = Counter()
    types: dict[str, set[str]] = defaultdict(set)
    example: dict[str, object] = {}
    sample = None
    for d in col.find({}, limit=args.limit):
        sample = sample or d
        for k, v in d.items():
            counts[k] += 1
            types[k].add(type(v).__name__)
            if k not in example and v not in (None, "", [], {}):
                example[k] = v

    print(f"vessels: {total} documents (scanned {min(total, args.limit)})\n")
    print(f"{'field':32} {'docs':>5}  {'type':14} example")
    for k, c in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"{k:32} {c:>5}  {'/'.join(sorted(types[k])):14} {short(example.get(k, ''))}")

    if args.sample and sample:
        print("\nSample document:")
        print(json.dumps(sample, default=str, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
