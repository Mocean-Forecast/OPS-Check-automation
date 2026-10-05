"""Compare the 11:00 recheck with the 07:30 run (D6).

Works on stored run dicts (RunReport.to_dict()), so the recheck can load the morning run from disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Change(str, Enum):
    NEW = "new"
    RESOLVED = "resolved"
    CHANGED = "changed"
    SAME = "same"


ACTION = {"offline", "stuck_mover", "stuck_processing", "not_loaded", "data_issue"}


def _actionable(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["vessel"]["system"] or r["vessel"]["id"]: r
            for r in run["results"] if r["diagnosis"] in ACTION}


@dataclass
class RunDiff:
    by_vessel: dict[str, Change] = field(default_factory=dict)
    resolved: list[dict[str, Any]] = field(default_factory=list)  # results from the earlier run

    def change(self, key: str) -> Change:
        return self.by_vessel.get(key, Change.SAME)


def diff_runs(before: dict[str, Any] | None, after: dict[str, Any]) -> RunDiff:
    out = RunDiff()
    if before is None:
        return out
    a, b = _actionable(before), _actionable(after)
    for k, r in b.items():
        if k not in a:
            out.by_vessel[k] = Change.NEW
        elif r["diagnosis"] != a[k]["diagnosis"]:
            out.by_vessel[k] = Change.CHANGED
    for k, r in a.items():
        if k not in b:
            out.by_vessel[k] = Change.RESOLVED
            out.resolved.append(r)
    return out
