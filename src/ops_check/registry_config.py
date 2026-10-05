"""Repo-side vessel config (D13): what the `vessels` collection doesn't hold.

config/vessels.toml:

    priority = ["Windcat Dorothea", "Offshore Waddenzee", "Bloemgracht"]

    [[known]]
    vessel = "Bokalift 2"          # nicename, vesselname or MMSI
    status = "demob"               # off-hire | demob | daughtercraft | tablet-only | no system
    reason = "not in use"
    until  = 2026-12-31            # optional; after this date the vessel is checked again

    exclude = ["MO4_test_wc47"]    # never checked (test / demo systems)

    [folders]                      # extra folder names when a share uses a different name
    "Bloemgracht" = ["spliethoff_bloemgracht"]

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
    folders: dict[str, list[str]] = field(default_factory=dict)  # vessel -> extra folder names
    exclude: set[str] = field(default_factory=set)  # never checked (test systems)


def _norm(s: Any) -> str:
    return str(s).strip().lower().replace(" ", "_")


def load(path: Path) -> VesselConfig:
    if not path.is_file():
        return VesselConfig()
    data = tomllib.loads(path.read_text())
    cfg = VesselConfig(priority={_norm(p) for p in data.get("priority", [])})
    cfg.labels = {_norm(p): str(p) for p in data.get("priority", []) + data.get("exclude", [])}
    cfg.exclude = {_norm(x) for x in data.get("exclude", [])}
    for vessel, names in (data.get("folders") or {}).items():
        cfg.folders[_norm(vessel)] = [str(n) for n in (names if isinstance(names, list) else [names])]
        cfg.labels[_norm(vessel)] = str(vessel)
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
        if ids & cfg.exclude:
            v.excluded = True
            used |= ids & cfg.exclude
        if ids & cfg.priority:
            v.priority = True
            used |= ids & cfg.priority
        for k in ids & set(cfg.folders):
            v.folder_aliases = cfg.folders[k]
            used.add(k)
        hit = next((k for k in ids if k in cfg.known), None)
        if hit:
            e = cfg.known[hit]
            v.status, v.status_reason, v.status_expiry, v.status_owner = e.status, e.reason, e.until, e.owner
            used.add(hit)
        elif v.daughtercraft and not v.status:
            v.status = "daughtercraft"
    unmatched = sorted((cfg.priority | set(cfg.known) | set(cfg.folders) | cfg.exclude) - used)
    warnings = list(cfg.problems)
    if unmatched:
        warnings.append(f"config/vessels.toml entries matching no vessel: {', '.join(cfg.labels.get(u, u) for u in unmatched)}")
    return warnings
