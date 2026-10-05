"""Run storage. One JSON file per run: results/<run date>/<slot>.json

Every finding carries vessel, day, stage, check, severity, owner and check version, so this
is the audit trail Mark asked for. A MongoDB results collection can replace this later; it
needs a user with write access to one collection (the source user stays read-only).
The same folder holds the run log of folder states that the parallel run learns from (D16).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


class RunStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, run_date: date, slot: str) -> Path:
        return self.root / run_date.isoformat() / f"{slot}.json"

    def save(self, run_date: date, slot: str, run: dict[str, Any]) -> Path:
        p = self.path(run_date, slot)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(run, indent=2, ensure_ascii=False))
        return p

    def load(self, run_date: date, slot: str) -> dict[str, Any] | None:
        p = self.path(run_date, slot)
        return json.loads(p.read_text()) if p.is_file() else None

    def mark_posted(self, run_date: date, slot: str, ok: bool, error: str | None = None) -> None:
        run = self.load(run_date, slot)
        if run is None:
            return
        run["posted"] = ok
        run["post_error"] = error
        self.save(run_date, slot, run)
