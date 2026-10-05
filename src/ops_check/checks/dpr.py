"""DPR status rules, ported 1:1 from Sjoerd's portal-api (vesselStatusService.js), see D11.

Read from MongoDB `CTV_dprInput` / `SOV_dprInput` until the Portal API service token exists.
"""

from __future__ import annotations

from typing import Any, Mapping

NO_DPR = "no-dpr-available"
NOT_FILLED = "dpr-available-not-filled"
FILLED = "dpr-filled"
SIGNED_OFF = "dpr-signed-off"


def _non_empty(v: Any) -> bool:
    return v is not None and v != ""


def ctv_dpr_status(doc: Mapping[str, Any] | None) -> str:
    if not doc:
        return NO_DPR
    if _non_empty(doc.get("accessDayType")) or _non_empty(doc.get("remarks")):
        return FILLED
    return NOT_FILLED


def sov_dpr_status(doc: Mapping[str, Any] | None) -> str:
    if not doc:
        return NO_DPR
    signed = (doc.get("signedOff") or {}).get("amount") or 0
    if signed > 0:
        return SIGNED_OFF
    access = (doc.get("accessDayType") or {}).get("status")
    if _non_empty(access) or _non_empty(doc.get("remarks")):
        return FILLED
    return NOT_FILLED


def dpr_status(operations_class: str | None, doc: Mapping[str, Any] | None) -> str:
    if operations_class == "CTV":
        return ctv_dpr_status(doc)
    if operations_class == "OSV":
        return sov_dpr_status(doc)
    return NO_DPR  # transit and others have no DPR
