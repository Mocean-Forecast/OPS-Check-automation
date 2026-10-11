"""Build the Teams message from a stored run, deterministically (D20).

The message model is rendered twice: as an Adaptive Card (card.py) and as plain text
(fallback / logs). An optional `notes` dict (vessel key -> one-line context note) is
where LLM-written notes plug in later; the structure never depends on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .diff import Change, RunDiff

LABEL = {
    "offline": "Offline, not synced",
    "stuck_mover": "Stuck at mover (FTP_VESSEL_DATA)",
    "stuck_processing": "Arrived, not processed",
    "not_loaded": "Processed, not loaded into DB",
    "data_issue": "Data issue",
}
ORDER = ["offline", "stuck_mover", "stuck_processing", "not_loaded", "data_issue"]


@dataclass
class Item:
    key: str
    name: str
    text: str
    priority: bool = False
    change: Change = Change.SAME


@dataclass
class Message:
    title: str
    subtitle: str
    action: list[Item] = field(default_factory=list)
    resolved: list[Item] = field(default_factory=list)
    waiting: list[Item] = field(default_factory=list)
    not_filled: list[str] = field(default_factory=list)
    not_signed_off: list[str] = field(default_factory=list)
    known: list[str] = field(default_factory=list)
    registry: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str | None = None
    banner: str | None = None
    off_hire: int = 0
    excluded: int = 0


def _key(r: dict[str, Any]) -> str:
    return r["vessel"]["system"] or r["vessel"]["id"]


def _headline(r: dict[str, Any]) -> str:
    msgs = [f["message"] for f in r["findings"] if f["stage"] != 0 and f["severity"] != "info"]
    return "; ".join(msgs) or LABEL.get(r["diagnosis"], r["diagnosis"])


def build_message(run: dict[str, Any], diff: RunDiff | None = None,
                  notes: dict[str, str] | None = None, summary: str | None = None) -> Message:
    diff = diff or RunDiff()
    notes = notes or {}
    run_at = datetime.fromisoformat(run["run_at"])
    day = date.fromisoformat(run["data_day"])
    results = run["results"]

    action = [r for r in results if r["diagnosis"] in LABEL]
    known = [r for r in results if r["diagnosis"] == "known"]
    checked = len(results) - len(known)
    slot = "RECHECK" if run["slot"] == "recheck" else "OPS CHECK"

    m = Message(
        title=f"{slot} {run_at.strftime('%d/%m/%Y %H:%M')} (data for {day.strftime('%d/%m')})",
        subtitle=(f"{len(results)} on hire · {checked} checked · {len(action)} need action · "
                  f"{len(known)} known"),
        off_hire=int(run.get("off_hire_count") or 0),
        excluded=int(run.get("excluded_count") or 0),
        warnings=list(run.get("warnings") or []),
        summary=summary,
    )
    if run.get("shares_missing"):
        m.banner = ("INCOMPLETE: file shares not reachable from the check machine. Only MongoDB "
                    "was checked; 'Offline' may include vessels stuck in the pipeline.")

    def item(r: dict[str, Any]) -> Item:
        k = _key(r)
        prio = bool(r["vessel"]["priority"])
        text = f"{LABEL[r['diagnosis']]} – {_headline(r)}"
        if k in notes:
            text += f" · {notes[k]}"
        return Item(k, r["vessel"]["name"], text, prio, diff.change(k))

    action.sort(key=lambda r: (not r["vessel"]["priority"],
                               diff.change(_key(r)) != Change.NEW,
                               ORDER.index(r["diagnosis"]), r["vessel"]["name"]))
    m.action = [item(r) for r in action]
    m.resolved = [Item(_key(r), r["vessel"]["name"], f"was: {LABEL.get(r['diagnosis'], r['diagnosis'])}",
                       bool(r["vessel"]["priority"]), Change.RESOLVED) for r in diff.resolved]

    for r in results:
        name = r["vessel"]["name"]
        if r["diagnosis"] == "in_transit":
            m.waiting.append(Item(_key(r), name, _info_text(r)))
        if r["dpr_status"] == "dpr-available-not-filled":
            m.not_filled.append(name)
        if r["dpr_status"] == "dpr-filled" and r["vessel"]["operations_class"] == "OSV":
            m.not_signed_off.append(name)
        for f in r["findings"]:
            if f["check"] in ("registry_expired", "registry_unparseable"):
                m.registry.append(f"{name}: {f['message']}")
    for r in sorted(known, key=lambda r: r["vessel"]["name"]):
        reason = next((f["message"] for f in r["findings"] if f["check"] == "expected_status"), "")
        m.known.append(f"{r['vessel']['name']} ({reason})" if reason else r["vessel"]["name"])
    return m


def _info_text(r: dict[str, Any]) -> str:
    return next((f["message"] for f in r["findings"] if f["stage"] in (2, 3)), "in transit")


def footer(m: Message) -> str:
    parts = [f"Off-hire, not checked: {m.off_hire}"]
    if m.excluded:
        parts.append(f"excluded test systems: {m.excluded}")
    return " · ".join(parts)


def priority_alerts(m: Message) -> list[Item]:
    """Priority vessels needing action get their own alert at every run (D8)."""
    return [i for i in m.action if i.priority]


def render_text(m: Message) -> str:
    tag = {Change.NEW: "[NEW] ", Change.CHANGED: "[CHANGED] ", Change.SAME: "", Change.RESOLVED: "[RESOLVED] "}
    lines = [f"{m.title} · {m.subtitle}", ""]
    if m.banner:
        lines += [f"!! {m.banner}", ""]
    if m.summary:
        lines += [m.summary, ""]
    lines.append("NEEDS ACTION" if m.action else "NEEDS ACTION: none")
    for i in m.action:
        prio = " [priority]" if i.priority else ""
        lines.append(f"{tag[i.change]}{i.name}{prio} – {i.text}")
    for i in m.resolved:
        lines.append(f"{tag[i.change]}{i.name} – {i.text}")
    lines += ["", "STATUS"]
    if m.waiting:
        lines.append(f"Waiting ({len(m.waiting)}): " + " · ".join(f"{i.name} ({i.text})" for i in m.waiting))
    if m.not_filled:
        lines.append(f"DPR not filled ({len(m.not_filled)}): {', '.join(m.not_filled)}")
    if m.not_signed_off:
        lines.append(f"DPR not signed off ({len(m.not_signed_off)}): {', '.join(m.not_signed_off)}")
    lines.append(f"Known ({len(m.known)}): {', '.join(m.known) or '–'}")
    if m.registry:
        lines.append(f"Registry ({len(m.registry)}): " + " · ".join(m.registry))
    for w in m.warnings:
        lines.append(f"⚠ {w}")
    if m.off_hire or m.excluded:
        lines += ["", footer(m)]
    return "\n".join(lines)
