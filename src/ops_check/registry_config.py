"""Repo-side vessel config (D13): what the `vessels` collection doesn't hold.

config/vessels.toml:

    priority = ["Windcat Dorothea", "Offshore Waddenzee", "Bloemgracht"]

    [[known]]
    vessel = "Bokalift 2"          # nicename, vesselname or MMSI
    status = "demob"               # off-hire | demob | daughtercraft | tablet-only | no system
    reason = "not in use"
    until  = 2026-12-31            # optional; after this date the vessel is checked again

Daughtercraft and off-hire come from the collection (isDaughterCraft, onHire) and need no entry.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from .models import Vessel


@dataclass
class KnownEntry:
    status: str
    reason: str | None = None
    until: date | None = None
    owner: str | None = None


@dataclass
class VesselConfig:
    priority: set[str] = field(default_factory=set)
    known: dict[str, KnownEntry] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # entries that matched no vessel
    labels: dict[str, str] = field(default_factory=dict)  # normalised key -> name as written


def _norm(s: Any) -> str:
    return str(s).strip().lower().replace(" ", "_")


def load(path: Path) -> VesselConfig:
    if not path.is_file():
        return VesselConfig()
    data = tomllib.loads(path.read_text())
    cfg = VesselConfig(priority={_norm(p) for p in data.get("priority", [])})
    cfg.labels = {_norm(p): str(p) for p in data.get("priority", [])}
    for e in data.get("known", []):
        if "vessel" not in e or "status" not in e:
            cfg.problems.append(f"config entry without vessel/status: {e}")
            continue
        until = e.get("until")
        cfg.labels[_norm(e["vessel"])] = str(e["vessel"])
        cfg.known[_norm(e["vessel"])] = KnownEntry(
            status=str(e["status"]), reason=e.get("reason"), owner=e.get("owner"),
            until=until if isinstance(until, date) else (date.fromisoformat(until) if until else None),
        )
    return cfg


def apply(vessels: list[Vessel], cfg: VesselConfig) -> list[str]:
    """Set status / priority on the vessels; returns warnings for config entries that match nothing."""
    used: set[str] = set()
    for v in vessels:
        ids = {_norm(v.name), _norm(v.system or ""), _norm(v.mmsi or "")} - {""}
        if ids & cfg.priority:
            v.priority = True
            used |= ids & cfg.priority
        hit = next((k for k in ids if k in cfg.known), None)
        if hit:
            e = cfg.known[hit]
            v.status, v.status_reason, v.status_expiry, v.status_owner = e.status, e.reason, e.until, e.owner
            used.add(hit)
        elif v.daughtercraft and not v.status:
            v.status = "daughtercraft"
    unmatched = sorted((cfg.priority | set(cfg.known)) - used)
    warnings = list(cfg.problems)
    if unmatched:
        warnings.append(f"config/vessels.toml entries matching no vessel: {', '.join(cfg.labels.get(u, u) for u in unmatched)}")
    return warnings
