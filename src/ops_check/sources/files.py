"""Scan the three data folders (D15/D16).

    FTP_VESSEL_DATA/<system>/<system>_<yyyy-mm-dd>_<n>.7z[.NNN]     landing
    Raw_vessel_data2/<system>/<system>_<yyyy-mm-dd>_<n>.7z[.NNN]    staging
    vessel_data6/<system>/processeddata/improveddata<yyyy-mm-dd>.mat

`quarantine` sub-folders are ignored. Only file names and timestamps are read,
never file contents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from ..models import FileInfo

_ARCHIVE = re.compile(r"^(?P<system>.+)_(?P<day>\d{4}-\d{2}-\d{2})_(?P<n>\d+)\.7z(?:\.\d{3})?$", re.I)
_PROCESSED = re.compile(r"^improveddata(?P<day>\d{4}-\d{2}-\d{2})\.mat$", re.I)
IGNORED_DIRS = {"quarantine"}


def _info(p: Path, day: date) -> FileInfo:
    st = p.stat()
    return FileInfo(str(p), day, datetime.fromtimestamp(st.st_mtime, timezone.utc), st.st_size)


def _archives(folder: Path) -> list[FileInfo]:
    out: list[FileInfo] = []
    if not folder.is_dir():
        return out
    for p in folder.iterdir():
        if p.is_dir():
            continue  # quarantine and anything else nested is ignored
        m = _ARCHIVE.match(p.name)
        if m:
            out.append(_info(p, date.fromisoformat(m["day"])))
    return out


@dataclass
class SystemFiles:
    landing: list[FileInfo] = field(default_factory=list)
    staging: list[FileInfo] = field(default_factory=list)
    processed: dict[date, FileInfo] = field(default_factory=dict)
    has_folder: bool = False


@dataclass
class ShareScan:
    systems: dict[str, SystemFiles]
    missing_roots: list[str]

    def folders(self) -> set[str]:
        return {s for s, f in self.systems.items() if f.has_folder}


def _subdirs(root: Path | None) -> list[Path]:
    if root is None or not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and p.name.lower() not in IGNORED_DIRS]


def scan_shares(landing: Path | None, staging: Path | None, processed: Path | None,
                processed_days: set[date]) -> ShareScan:
    """Scan all system folders. `processed_days` limits which improveddata files are stat'ed."""
    systems: dict[str, SystemFiles] = {}
    missing = [str(r) for r in (landing, staging, processed) if r is not None and not r.is_dir()]

    def get(name: str) -> SystemFiles:
        return systems.setdefault(name, SystemFiles())

    for d in _subdirs(landing):
        sf = get(d.name)
        sf.has_folder = True
        sf.landing = _archives(d)
    for d in _subdirs(staging):
        sf = get(d.name)
        sf.has_folder = True
        sf.staging = _archives(d)
    for d in _subdirs(processed):
        sf = get(d.name)
        sf.has_folder = True
        pdir = d / "processeddata"
        for day in processed_days:
            p = pdir / f"improveddata{day.isoformat()}.mat"
            if p.is_file():
                sf.processed[day] = _info(p, day)
    return ShareScan(systems, missing)
