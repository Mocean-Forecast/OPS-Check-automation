"""Scan the three data folders (D15/D16). Only names and timestamps are read, never contents.

Observed layout (5 Oct 2026):

    FTP_VESSEL_DATA/<vessel>/<Vessel Name>_<yyyy-mm-dd>_<n>.7z.NNN   landing: multi-volume archives;
                                                                     the prefix may contain spaces
    Raw_vessel_data2/<vessel>/<yyyy-mm-dd>/...                       unpacked day folder (sensor dirs,
                                                                     errorLog_*.txt, eventLog_*.txt)
    Raw_vessel_data2/<vessel>/<...>_<yyyy-mm-dd>_<n>.7z.NNN          (older systems keep archives here)
    vessel_data6/<vessel>/processeddata/improveddata<yyyy-mm-dd>.mat processed

Folder names differ in case between shares (windcat_41 / Windcat_41), so folders are matched on a
normalised key (lower case, spaces/hyphens -> underscores). Extra per-vessel aliases come from
config/vessels.toml. `quarantaine`, `tempUnzip`, `#recycle` and dot-folders are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from ..models import FileInfo

_ARCHIVE = re.compile(r"^(?P<prefix>.+)_(?P<day>\d{4}-\d{2}-\d{2})_(?P<n>\d+)\.7z(?:\.\d{3})?$", re.I)
_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IGNORED_ROOT = {"#recycle"}


def norm(name: str) -> str:
    return re.sub(r"[\s\-]+", "_", name.strip()).lower()


def _info(p: Path, day: date) -> FileInfo:
    st = p.stat()
    return FileInfo(str(p), day, datetime.fromtimestamp(st.st_mtime, timezone.utc), st.st_size)


def _index(root: Path | None) -> dict[str, Path]:
    """Normalised folder name -> folder, for the vessel folders directly under a share."""
    if root is None or not root.is_dir():
        return {}
    out: dict[str, Path] = {}
    for p in root.iterdir():
        if p.is_dir() and not p.name.startswith(".") and p.name not in IGNORED_ROOT:
            out.setdefault(norm(p.name), p)
    return out


def _archives(folder: Path, since: date) -> list[FileInfo]:
    """Archive volumes directly in `folder` for days >= since (sub-folders are ignored)."""
    out: list[FileInfo] = []
    for p in folder.iterdir():
        m = _ARCHIVE.match(p.name)
        if m and p.is_file():
            d = date.fromisoformat(m["day"])
            if d >= since:
                out.append(_info(p, d))
    return out


@dataclass
class SystemFiles:
    landing: list[FileInfo] = field(default_factory=list)      # archives in FTP_VESSEL_DATA
    staging: list[FileInfo] = field(default_factory=list)      # archives in Raw_vessel_data2 (older systems)
    staging_days: dict[date, FileInfo] = field(default_factory=dict)  # unpacked day folders in Raw_vessel_data2
    processed: dict[date, FileInfo] = field(default_factory=dict)
    shares: set[str] = field(default_factory=set)               # which shares have a folder

    @property
    def has_folder(self) -> bool:
        return bool(self.shares)


@dataclass
class ShareScan:
    systems: dict[str, SystemFiles]          # by normalised name
    missing_roots: list[str]
    all_folders: set[str] = field(default_factory=set)  # normalised names of every vessel folder
    unknown_active: list[str] = field(default_factory=list)  # landing folders with recent archives, no vessel

    def get(self, name: str | None) -> SystemFiles:
        return self.systems.get(norm(name), SystemFiles()) if name else SystemFiles()

    def folders(self) -> set[str]:
        return self.all_folders


def scan_shares(landing: Path | None, staging: Path | None, processed: Path | None,
                wanted: dict[str, list[str]], day: date, lookback_days: int = 14,
                ignored: set[str] | None = None) -> ShareScan:
    """Scan only the folders of the vessels in `wanted` (vessel key -> folder names incl. aliases).

    Archives and day folders older than `lookback_days` before `day` are ignored, to keep the
    scan fast on the SMB shares and the backlog list relevant.
    """
    since = date.fromordinal(day.toordinal() - lookback_days)
    roots = {"landing": landing, "staging": staging, "processed": processed}
    missing = [str(r) for r in roots.values() if r is not None and not r.is_dir()]
    idx = {k: _index(r) for k, r in roots.items()}
    systems: dict[str, SystemFiles] = {}

    for key, names in wanted.items():
        sf = SystemFiles()
        # A vessel can have several folders on one share (e.g. Brouwersgracht and
        # spliethoff_brouwersgracht in Raw_vessel_data2): merge them all.
        seen: set[Path] = set()
        for k in dict.fromkeys(norm(n) for n in names):
            if (p := idx["landing"].get(k)) and p not in seen:
                seen.add(p)
                sf.shares.add("landing")
                sf.landing += _archives(p, since)
            if (p := idx["staging"].get(k)) and p not in seen:
                seen.add(p)
                sf.shares.add("staging")
                sf.staging += _archives(p, since)
                for c in p.iterdir():
                    if _DAY_DIR.match(c.name) and c.is_dir():
                        d = date.fromisoformat(c.name)
                        if d >= since and (d not in sf.staging_days
                                           or c.stat().st_mtime > sf.staging_days[d].modified.timestamp()):
                            sf.staging_days[d] = _info(c, d)
            if (p := idx["processed"].get(k)) and p not in seen:
                seen.add(p)
                sf.shares.add("processed")
                f = p / "processeddata" / f"improveddata{day.isoformat()}.mat"
                if f.is_file() and day not in sf.processed:
                    sf.processed[day] = _info(f, day)
        systems[norm(key)] = sf

    all_folders = set().union(*(set(i) for i in idx.values())) if idx else set()
    # Landing folders that receive archives but belong to no on-hire vessel (FTP has ~50 folders: cheap).
    matched = {norm(n) for names in wanted.values() for n in names} | {norm(n) for n in (ignored or set())}
    unknown = sorted(p.name for k, p in idx["landing"].items()
                     if k not in matched and _archives(p, since))
    return ShareScan(systems, missing, all_folders, unknown)
