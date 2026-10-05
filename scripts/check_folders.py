"""Match every on-hire vessel in MongoDB to its folders on the three shares.

Run from the repo root on the machine where the shares are mounted (uses .env):

    python scripts/check_folders.py

Prints, per on-hire vessel: vesselname and whether a folder was found in FTP_VESSEL_DATA,
Raw_vessel_data2 and vessel_data6 (case-insensitive), plus the newest processed day.
Vessels with no folder anywhere need a [folders] alias in config/vessels.toml.
Read-only: find() on `vessels`, directory listings on the shares.
"""

from __future__ import annotations

import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ops_check import registry_config  # noqa: E402
from ops_check.config import Settings  # noqa: E402
from ops_check.sources.files import norm, scan_shares  # noqa: E402
from ops_check.sources.mongo import MongoSource  # noqa: E402

_MAT = re.compile(r"^improveddata(\d{4}-\d{2}-\d{2})\.mat$")


def newest_processed(root: Path | None, folder: str | None) -> str:
    if not root or not folder:
        return "-"
    for p in root.iterdir():
        if p.is_dir() and norm(p.name) == norm(folder):
            days = [m[1] for f in (p / "processeddata").glob("improveddata*.mat") if (m := _MAT.match(f.name))]
            return max(days) if days else "none"
    return "-"


def main() -> int:
    s = Settings.from_env()
    vessels = [v for v in MongoSource.connect(s).vessels()
               if v.on_hire and not set(v.clients) & set(s.excluded_clients)]
    registry_config.apply(vessels, registry_config.load(s.vessels_config))
    day = date.today() - timedelta(days=1)
    scan = scan_shares(s.landing_dir, s.staging_dir, s.processed_dir,
                       {v.key: v.folder_names for v in vessels}, day)
    if scan.missing_roots:
        print("Share(s) not reachable:", ", ".join(scan.missing_roots))

    print(f"{'vessel':26} {'vesselname':26} FTP  RAW  VD6  newest processed")
    missing = 0
    for v in sorted(vessels, key=lambda v: v.name.lower()):
        sf = scan.get(v.key)
        flags = ["yes" if share in sf.shares else " - " for share in ("landing", "staging", "processed")]
        missing += not sf.has_folder
        print(f"{v.name[:26]:26} {str(v.system)[:26]:26} {flags[0]}  {flags[1]}  {flags[2]}  "
              f"{newest_processed(s.processed_dir, v.system)}")
    print(f"\n{len(vessels)} on-hire vessels, {missing} without a folder on any share")
    if scan.unknown_active:
        print("Folders in FTP_VESSEL_DATA with recent archives but no on-hire vessel:",
              ", ".join(scan.unknown_active))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
