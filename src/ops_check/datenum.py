"""MATLAB datenum helpers.

MO4's MongoDB stores days as MATLAB datenums (days since year 0). The portal uses the
same number in its DPR links, e.g. 740259 == 2026-10-04.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_OFFSET = 366  # date.toordinal() counts from 0001-01-01, MATLAB from 0000-01-00


def to_datenum(d: date) -> int:
    return d.toordinal() + _OFFSET


def from_datenum(n: float) -> datetime:
    """Datenum (may be fractional) -> naive UTC datetime."""
    whole = int(n)
    frac = n - whole
    base = datetime.fromordinal(whole - _OFFSET)
    return base + timedelta(days=frac)


def from_datenum_utc(n: float) -> datetime:
    return from_datenum(n).replace(tzinfo=timezone.utc)


def portal_dpr_url(mmsi: int, d: date) -> str:
    return f"https://portal.mo4.online/reports/dpr;mmsi={mmsi}?date={to_datenum(d)}"
