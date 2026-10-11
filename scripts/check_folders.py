"""Match every on-hire vessel in MongoDB to its folders on the three shares.

Run from the repo root on the machine where the shares are mounted (uses .env):

    python scripts/check_folders.py

Prints, per on-hire vessel: vesselname and whether a folder was found in FTP_VESSEL_DATA,
Raw_vessel_data2 and vessel_data6 (case-insensitive), plus the newest processed day.
Vessels with no folder anywhere need a [folders] alias in config/vessels.toml.
Read-only: find() on `vessels`, directory listings on the shares.
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ops_check import registry_config  # noqa: E402
from ops_check.config import Settings  # noqa: E402
from ops_check.progress import Progress  # noqa: E402
from ops_check.sources.files import norm, scan_shares  # noqa: E402
from ops_check.sources.mongo import MongoSource  # noqa: E402

_MAT = re.compile(r"^improveddata(\d{4}-\d{2}-\d{2})\.mat$")


_ROOT_CACHE: dict[Path, dict[str, Path]] = {}


def newest_processed(root: Path | None, folder: str | None) -> str:
    """Newest improveddata<day>.mat. The share root is listed once (SMB listings are slow)."""
    if not root or not folder:
        return "-"
    if root not in _ROOT_CACHE:
        _ROOT_CACHE[root] = {norm(e.name): Path(e.path) for e in os.scandir(root) if e.is_dir()}
    p = _ROOT_CACHE[root].get(norm(folder))
    if p is None:
        return "-"
    try:
        days = [m[1] for e in os.scandir(p / "processeddata") if (m := _MAT.match(e.name))]
    except OSError:
        return "no processeddata"
    return max(days) if days else "none"


def main() -> int:
    s = Settings.from_env()
    vessels = [v for v in MongoSource.connect(s).vessels()
               if v.on_hire and not set(v.clients) & set(s.excluded_clients)]
    registry_config.apply(vessels, registry_config.load(s.vessels_config))
    excluded = sorted(v.name for v in vessels if v.excluded)
    vessels = [v for v in vessels if not v.excluded]   # same set as the real run
    day = date.today() - timedelta(days=1)
    print(f"{len(vessels)} on-hire vessels to check ({len(excluded)} excluded in config/vessels.toml: "
          f"{', '.join(excluded)}); scanning the shares …", flush=True)
    t0 = time.time()
    scan = scan_shares(s.landing_dir, s.staging_dir, s.processed_dir,
                       {v.key: v.folder_names for v in vessels}, day,
                       progress=Progress().item)
    print(f"share scan took {time.time() - t0:.0f} s; newest processed day per vessel …", flush=True)
    if scan.missing_roots:
        print("Share(s) not reachable:", ", ".join(scan.missing_roots))

    print(f"{'vessel':26} {'vesselname':26} FTP  RAW  VD6  newest processed")
    missing = 0
    for v in sorted(vessels, key=lambda v: v.name.lower()):
        sf = scan.get(v.key)
        flags = ["yes" if share in sf.shares else " - " for share in ("landing", "staging", "processed")]
        missing += not sf.has_folder
        print(f"{v.name[:26]:26} {str(v.system)[:26]:26} {flags[0]}  {flags[1]}  {flags[2]}  "
              f"{newest_processed(s.processed_dir, v.system)}", flush=True)
    print(f"\n{len(vessels)} on-hire vessels, {missing} without a folder on any share")
    if scan.unknown_active:
        print("Folders in FTP_VESSEL_DATA with recent archives but no on-hire vessel:",
              ", ".join(scan.unknown_active))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
