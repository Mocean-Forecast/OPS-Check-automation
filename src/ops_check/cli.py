"""Command line.

    ops-check run --slot morning            # 07:30
    ops-check run --slot recheck            # 11:00, diff against the morning run (D6)
    ops-check watchdog --slot morning       # 07:45 / 11:15 (D7)

    ops-check run --slot morning --fixture tests/fixtures/sample_day.json --dry-run --now 2026-10-05T07:30
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .run import FixtureSources, LiveSources, execute, watchdog


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops-check")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the checks and post to Teams")
    r.add_argument("--slot", choices=["morning", "recheck"], required=True)
    r.add_argument("--day", type=date.fromisoformat, help="data day (default: yesterday)")
    r.add_argument("--now", help="pretend the run happens at this local time (ISO)")
    r.add_argument("--fixture", type=Path, help="run from a JSON fixture instead of MongoDB/shares")
    r.add_argument("--dry-run", action="store_true", help="don't post; print the message")
    r.add_argument("--card", action="store_true", help="with --dry-run: print the Adaptive Card JSON")

    w = sub.add_parser("watchdog", help="alert if a run hasn't posted")
    w.add_argument("--slot", choices=["morning", "recheck"], required=True)
    w.add_argument("--dry-run", action="store_true")

    p.add_argument("--env", type=Path, default=Path(".env"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env(args.env)

    if args.cmd == "watchdog":
        alert = watchdog(s, args.slot, post=not args.dry_run)
        print(alert or f"{args.slot} run posted – OK")
        return 0

    now = None
    if args.now:
        now = datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(s.timezone))
    sources = FixtureSources(args.fixture, s) if args.fixture else LiveSources(s)
    out = execute(s, sources, args.slot, now=now, data_day=args.day, post=not args.dry_run)
    if args.dry_run:
        print(json.dumps(out.card, indent=2, ensure_ascii=False) if args.card else out.text)
    print(f"\nstored: {out.stored_at}", file=sys.stderr)
    return 0 if (args.dry_run or out.posted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
