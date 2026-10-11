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
    r.add_argument("--quiet", action="store_true", help="no progress output")
    r.add_argument("--card", action="store_true", help="with --dry-run: print the Adaptive Card JSON")
    r.add_argument("--preview", type=Path, default=None,
                   help="with --dry-run: write an HTML preview of the card here (default out/preview-<day>-<slot>.html)")

    w = sub.add_parser("watchdog", help="alert if a run hasn't posted")
    w.add_argument("--slot", choices=["morning", "recheck"], required=True)
    w.add_argument("--dry-run", action="store_true")

    sub.add_parser("test-post", help="post a small test card to the Teams webhook")

    po = sub.add_parser("post", help="re-post the card of a stored run (no new scan)")
    po.add_argument("--slot", choices=["morning", "recheck"], required=True)
    po.add_argument("--date", type=date.fromisoformat, help="run date (default: today)")

    p.add_argument("--env", type=Path, default=Path(".env"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env(args.env)

    if args.cmd == "test-post":
        import socket
        from datetime import datetime as _dt

        from .notify import teams
        from .report.card import webhook_payload

        card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                "version": "1.4", "body": [
                    {"type": "TextBlock", "text": "OPS CHECK – test post", "weight": "Bolder", "size": "Large"},
                    {"type": "TextBlock", "wrap": True,
                     "text": "Connection test from the automated ops check. No vessel data. "
                             "**Bold**, and colours: "},
                    {"type": "TextBlock", "text": "NEW · red bold", "color": "Attention", "weight": "Bolder"},
                    {"type": "TextBlock", "text": "CHANGED · amber bold", "color": "Warning", "weight": "Bolder"},
                    {"type": "TextBlock", "text": "RESOLVED · green", "color": "Good"},
                    {"type": "TextBlock", "isSubtle": True, "size": "Small",
                     "text": f"{socket.gethostname()} · {_dt.now().strftime('%d/%m/%Y %H:%M')}"}]}
        teams.post(s.teams_webhook_url, webhook_payload(card))
        print("test card posted")
        return 0

    if args.cmd == "post":
        from datetime import timedelta
        from zoneinfo import ZoneInfo as _Z

        from .notify import teams
        from .report.card import webhook_payload
        from .report.spot_card import render_ops_card
        from .store import RunStore
        store = RunStore(s.results_dir)
        run_date = args.date or datetime.now(_Z(s.timezone)).date()
        run = store.load(run_date, args.slot)
        if run is None:
            print(f"no stored {args.slot} run for {run_date}")
            return 1
        prev = (store.load(run_date, "morning") if args.slot == "recheck" else
                store.load(run_date - timedelta(days=1), "recheck") or store.load(run_date - timedelta(days=1), "morning"))
        card = render_ops_card(run, prev)
        size = len(json.dumps(webhook_payload(card), ensure_ascii=False).encode())
        try:
            teams.post(s.teams_webhook_url, webhook_payload(card))
        except teams.PostError as e:
            store.mark_posted(run_date, args.slot, False, str(e))
            print(f"posting failed: {e}")
            return 1
        store.mark_posted(run_date, args.slot, True)
        print(f"posted {args.slot} run of {run_date} ({size / 1000:.1f} KB)")
        return 0

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
    from .progress import Progress
    out = execute(s, sources, args.slot, now=now, data_day=args.day, post=not args.dry_run,
                  progress=Progress(enabled=not args.quiet))
    if args.dry_run:
        if args.card:
            print(json.dumps(out.card, indent=2, ensure_ascii=False))
        else:
            print(out.text)
            spot = out.run.get("spot") or {}
            if spot.get("error"):
                print(f"\nSPOT CHECK FAILED: {spot['error']}")
            blocks = spot.get("blocks") or {}
            print(f"\nSPOT CHECK ({len(blocks)} vessels)")
            for name in sorted(blocks, key=str.lower):
                print("\n".join(blocks[name]))
        from .report.preview import card_preview_html
        prev = args.preview or Path("out") / f"preview-{out.run['data_day']}-{args.slot}.html"
        prev.parent.mkdir(parents=True, exist_ok=True)
        prev.write_text(card_preview_html(out.card, f"Dry run · {args.slot} · data {out.run['data_day']} · not posted"))
        print(f"\ncard preview: {prev.resolve()}", file=sys.stderr)
    print(f"\nstored: {out.stored_at}", file=sys.stderr)
    return 0 if (args.dry_run or out.posted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
