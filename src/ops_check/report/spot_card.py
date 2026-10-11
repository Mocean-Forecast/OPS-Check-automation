"""Teams card for the spot check: short post, detail folded away (Martijn, 11 Oct).

Layers: (1) what changed since the previous run, (2) one line per root cause with the vessels
behind a toggle, (3) fuel / missed transfers / DPR sections and the full per-vessel list, all
collapsed. Tags render as Teams tag mentions when tag ids are configured, else as bold text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ..checks.spot import SpotInput, SpotResult, needs_attention
from ..models import Finding, Severity, Tag

# Root causes: one line each, the vessels behind a toggle.
CAUSES: dict[str, tuple[Tag, str]] = {
    "route_shift": (Tag.DATA, "Portal route / timeline time-shift"),
    "vf_api": (Tag.OPS, "VF API wave data broken (zeros / empty)"),
    "source_not_in_portal": (Tag.OPS, "Wave source has no data in the portal"),
    "radar_empty": (Tag.OPS, "SOV wave radar empty"),
    "no_wave_source": (Tag.OPS, "No wave source configured"),
    "wave_other": (Tag.OPS, "Wave data missing / Hs gaps"),
}
SECTIONS = {"transit": "Transit vessels (vMRU)", "fuel": "Fuel", "missed": "Missed transfers", "dpr": "DPR"}


@dataclass
class Issue:
    vessel: str
    finding: Finding
    group: str                  # a CAUSES key or a SECTIONS key

    @property
    def key(self) -> str:
        return f"{self.vessel}|{self.finding.check}"


def classify(vessel: str, inp: SpotInput, res: SpotResult) -> list[Issue]:
    out = []
    for f in needs_attention(res):
        c = f.check.removeprefix("spot_")
        if c in ("timeline_transfers", "timeline_end", "route_end"):
            g = "route_shift"
        elif c in ("weather_empty", "hs_missing_transfers"):
            w = inp.wave
            if w and w.label == "VF API":
                g = "vf_api"
            elif w is None and inp.wave_source_hint:
                g = "source_not_in_portal"
            elif w is None:
                g = "no_wave_source"
            elif w.source_id is None and inp.vessel_type == "OSV" and c == "weather_empty":
                g = "radar_empty"
            else:
                g = "wave_other"
        elif c.startswith("fuel"):
            g = "fuel"
        elif c == "missed_transfers":
            g = "missed"
        elif c.startswith("transit"):
            g = "transit"
        else:
            g = "dpr"
        out.append(Issue(vessel, f, g))
    return out


def issue_dicts(vessel: str, inp: SpotInput, res: SpotResult) -> list[dict[str, Any]]:
    """Spot issues as stored in the run file (results/<date>/<slot>.json)."""
    return [{"vessel": i.vessel, "check": i.finding.check, "group": i.group, "message": i.finding.message,
             "severity": i.finding.severity.value, "tag": i.finding.tag.value if i.finding.tag else None,
             "rows": i.finding.details.get("rows", [])} for i in classify(vessel, inp, res)]


# ------------------------------------------------------------------ card

def _t(text: str, **kw: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **kw}


def _tag(t: Tag | None, ids: dict[Tag, str] | None, ents: list[dict]) -> str:
    if t is None:
        return ""
    if ids and t in ids:
        ents.append({"type": "mention", "text": f"<at>{t.value}</at>",
                     "mentioned": {"id": ids[t], "name": t.value, "type": "tag"}})
        return f"<at>{t.value}</at>"
    return f"**@{t.value}**"


def _toggle_row(cid: str, cells: list[tuple[str, dict]], body: list[dict], visible: bool = False) -> list[dict]:
    """A clickable row (▸) that shows / hides `body`."""
    cols = [{"type": "Column", "width": w.pop("width", "auto"), "items": [_t(txt, **w)],
             "verticalContentAlignment": "Center"} for txt, w in cells]
    cols.append({"type": "Column", "width": "auto", "items": [_t("▸", isSubtle=True)]})
    return [
        {"type": "ColumnSet", "spacing": "Small", "columns": cols,
         "selectAction": {"type": "Action.ToggleVisibility", "targetElements": [cid]}},
        {"type": "Container", "id": cid, "isVisible": visible, "spacing": "None", "style": "emphasis",
         "items": body or [_t("–", isSubtle=True)]},
    ]


def _heading(text: str) -> dict[str, Any]:
    return _t(text, weight="Bolder", spacing="Medium", separator=True)


PIPELINE = {  # chain diagnosis -> (label, tag); handover tag table
    "offline": ("No raw data, not synced", Tag.DATA),
    "stuck_mover": ("Stuck at mover (FTP_VESSEL_DATA)", Tag.DATA),
    "stuck_processing": ("Arrived, not processed (Raw_vessel_data2)", Tag.DATA),
    "not_loaded": ("Processed, not loaded into MongoDB", Tag.DATA),
    "data_issue": ("Data issue (coverage / gaps)", Tag.OPS),
}
_TAGV = {t.value: t for t in Tag}


def _vkey(r: dict[str, Any]) -> str:
    return r["vessel"]["name"]


def _pipeline_text(r: dict[str, Any]) -> str:
    msgs = [f["message"] for f in r["findings"] if f["stage"] != 0 and f["severity"] != "info"]
    return "; ".join(msgs) or PIPELINE[r["diagnosis"]][0]


def _issues(run: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Comparable issues of a run: pipeline diagnoses + spot issues (DPR excluded: per day)."""
    if not run:
        return {}
    out = {}
    transit_off = {i["vessel"] for i in (run.get("spot") or {}).get("issues", []) if i["check"] == "spot_transit_no_vmru"}
    for r in run.get("results", []):
        if r["diagnosis"] in PIPELINE and not (r["diagnosis"] == "offline" and _vkey(r) in transit_off):
            out[f"{_vkey(r)}|pipeline"] = {"vessel": _vkey(r), "group": "pipeline:" + r["diagnosis"],
                                           "message": f"{PIPELINE[r['diagnosis']][0]} – {_pipeline_text(r)}",
                                           "tag": PIPELINE[r["diagnosis"]][1].value, "severity": "fail"}
    for i in (run.get("spot") or {}).get("issues", []):
        if i["check"] == "spot_transit_no_vmru":
            # transit vessel without data = the same problem as "no raw data" in the pipeline
            out[f"{i['vessel']}|pipeline"] = i
        elif i["group"] != "dpr":
            out[f"{i['vessel']}|{i['check']}"] = i
    return out


def render_ops_card(run: dict[str, Any], prev: dict[str, Any] | None = None,
                    tag_ids: dict[Tag, str] | None = None, report_url: str | None = None) -> dict[str, Any]:
    """One card for the whole run: pipeline (always open) + spot check (detail folded)."""
    ents: list[dict] = []
    tg = lambda v: _tag(_TAGV.get(v) if isinstance(v, str) else v, tag_ids, ents)  # noqa: E731
    run_at = datetime.fromisoformat(run["run_at"])
    day = date.fromisoformat(run["data_day"])
    results = run["results"]
    spot = run.get("spot") or {}
    issues: list[dict[str, Any]] = spot.get("issues", [])
    blocks: dict[str, list[str]] = spot.get("blocks", {})

    spot_checks = {(i["vessel"], i["check"]) for i in issues}
    # transit vessel without data: reported by its vMRU line (Team OPS), not as a pipeline row
    pipe = [r for r in results if r["diagnosis"] in PIPELINE
            and not (r["diagnosis"] == "offline" and (_vkey(r), "spot_transit_no_vmru") in spot_checks)]
    known = [r for r in results if r["diagnosis"] == "known"]
    waiting = [r for r in results if r["diagnosis"] == "in_transit"]
    pipe_names = {_vkey(r) for r in pipe}
    attention = pipe_names | set(blocks)
    clean = sorted({_vkey(r) for r in results} - attention - {_vkey(r) for r in known + waiting}, key=str.lower)

    now_i, prev_i = _issues(run), _issues(prev)
    prev_groups = {i["group"] for i in prev_i.values()}
    # A previous run made before the spot check existed has no spot issues to compare with:
    # then only pipeline changes count as new (otherwise every spot issue would be "new" once).
    spot_baseline = prev is not None and "spot" in prev
    new = [i for k, i in now_i.items() if prev is not None and k not in prev_i and i["severity"] == "fail"
           and (spot_baseline or k.endswith("|pipeline"))
           and not (i["group"] in CAUSES and i["group"] in prev_groups)]
    # Resolved = an issue of a vessel that is checked in this run and no longer has it. Vessels that
    # dropped out of the run (excluded, off hire, known) aren't "resolved", they're just not checked.
    checked = {_vkey(r) for r in results if r["diagnosis"] != "known"}
    resolved = ([i for k, i in prev_i.items() if k not in now_i and i["vessel"] in checked]
                if prev is not None else [])

    slot = "RECHECK" if run["slot"] == "recheck" else "OPS CHECK"
    body: list[dict] = [
        _t(f"{slot} · {run_at:%a %d/%m %H:%M} · data {day:%a %d/%m}", weight="Bolder", size="Large"),
        _t(f"{len(results)} on hire · **{len(pipe)} pipeline** · **{len(blocks)} spot check** · "
           f"{len(clean)} clean · {len(known)} known", spacing="None"),
    ]
    if prev is not None:
        since = "07:30" if run["slot"] == "recheck" else "yesterday"
        body.append(_t(f"🆕 {len(new)} new · ✅ {len(resolved)} resolved since {since}",
                       spacing="Small", isSubtle=True))
    if run.get("shares_missing"):
        body.append(_t("⚠ INCOMPLETE: file shares not reachable. Pipeline judged from MongoDB only; "
                       "'No raw data' may include vessels stuck in the pipeline.",
                       color="Attention", weight="Bolder", spacing="Small"))

    # 1. pipeline: always open
    body.append(_heading("PIPELINE"))
    if not pipe:
        body.append(_t("All data arrived, processed and loaded.", color="Good", spacing="Small"))
    for diag, (label, tag) in PIPELINE.items():
        rs = sorted((r for r in pipe if r["diagnosis"] == diag), key=lambda r: _vkey(r).lower())
        if not rs:
            continue
        detail = [_t(f"• **{_vkey(r)}**{' **[priority]**' if r['vessel'].get('priority') else ''} – "
                     f"{_pipeline_text(r)}", size="Small", spacing="None") for r in rs]
        body += _toggle_row(f"p_{diag}", [(tg(tag), {"width": "90px"}), (label, {"width": "stretch"}),
                                           (f"**{len(rs)}**", {})], detail, visible=len(rs) <= 3)
    if waiting:
        body.append(_t(f"Waiting ({len(waiting)}): " + ", ".join(_vkey(r) for r in waiting),
                       isSubtle=True, size="Small", spacing="Small"))

    # 2. what changed
    body.append(_heading("NEW SINCE PREVIOUS RUN"))
    if prev is not None and not spot_baseline:
        body.append(_t("First run with the spot check: spot issues are compared from the next run on.",
                       isSubtle=True, spacing="Small"))
    if prev is None:
        body.append(_t("No previous run to compare with.", isSubtle=True, spacing="Small"))
    elif new:
        for i in sorted(new, key=lambda i: (i["tag"] != Tag.DATA.value, i["vessel"].lower())):
            body.append(_t(f"• **{i['vessel']}** – {i['message']} {tg(i['tag'])}".rstrip(), spacing="Small"))
    else:
        body.append(_t("No new issues.", color="Good", spacing="Small"))
    if resolved:
        body += _toggle_row("resolved", [(f"✅ Resolved ({len(resolved)})", {"color": "Good", "width": "stretch"})],
                            [_t(f"• **{i['vessel']}** – was: {i['message']}", spacing="None", size="Small")
                             for i in sorted(resolved, key=lambda i: i["vessel"].lower())])

    # 3. spot: root causes
    body.append(_heading("ROOT CAUSES"))
    shown = False
    for key, (tag, label) in CAUSES.items():
        its = [i for i in issues if i["group"] == key]
        if not its:
            continue
        shown = True
        names = sorted({i["vessel"] for i in its}, key=str.lower)
        detail = ([_t(f"**{v}**: " + "; ".join(i["message"] for i in its if i["vessel"] == v),
                      spacing="None", size="Small") for v in names] if key == "route_shift"
                  else [_t(", ".join(names), size="Small", spacing="None")])
        body += _toggle_row(f"c_{key}", [(tg(tag), {"width": "90px"}), (label, {"width": "stretch"}),
                                         (f"**{len(names)}** vessel{'s' if len(names) > 1 else ''}", {})], detail)
    if not shown:
        body.append(_t("None.", color="Good", spacing="Small"))

    # 4. spot: topics
    body.append(_heading("BY TOPIC"))
    for key, label in SECTIONS.items():
        its = [i for i in issues if i["group"] == key]
        if not its:
            continue
        if key == "dpr":
            groups: dict[str, list[str]] = {}
            for i in its:
                m = i["message"]
                k = "not created" if "not created" in m else "not filled" if "not filled" in m else "not signed off"
                groups.setdefault(k, []).append(i["vessel"] + (f" ({m.split('last DPR ')[-1]})" if "last DPR" in m else ""))
            summary = " · ".join(f"{len(v)} {k}" for k, v in groups.items())
            detail = [_t(f"**{k.capitalize()}:** " + ", ".join(sorted(v, key=str.lower)), size="Small", spacing="None")
                      for k, v in groups.items()]
            tag = ""
        else:
            names = {i["vessel"] for i in its}
            warn = sum(i["severity"] == "suspect" for i in its)
            summary = f"{len(names)} vessel{'s' if len(names) > 1 else ''}" + (f" (⚠ {warn} small)" if warn else "")
            detail = []
            for i in sorted(its, key=lambda i: (i["severity"] != "fail", i["vessel"].lower())):
                w = "⚠ " if i["severity"] == "suspect" else ""
                detail.append(_t(f"• **{i['vessel']}** – {w}{i['message']}", size="Small", spacing="None"))
                detail += [_t(f"   – {r}", size="Small", spacing="None", isSubtle=True) for r in i.get("rows", [])]
            tag = tg(Tag.OPS)
        body += _toggle_row(f"s_{key}", [(tag, {"width": "90px"}), (label, {"width": "stretch"}), (summary, {})],
                            detail)

    # 5. everything per vessel: pipeline line first, then the spot bullets. One text block for all
    # vessels: a TextBlock per vessel costs ~100 bytes of JSON each, and Teams caps a message at ~28 KB.
    by_name = {_vkey(r): r for r in pipe}
    per_lines: list[str] = []
    for v in sorted(attention, key=str.lower):
        lines = list(blocks.get(v, [v]))
        bullets = [l.strip()[2:].replace(" (Team Data)", "").replace(" (Team OPS)", "")
                   for l in lines[1:] if l.strip().startswith("• ")]
        if v in by_name:
            r = by_name[v]
            bullets.insert(0, f"{PIPELINE[r['diagnosis']][0]} – {_pipeline_text(r)}")
        per_lines.append(f"**{v}**: " + " · ".join(bullets))
    body.append({"type": "Container", "spacing": "Medium", "separator": True, "items": []})
    body += _toggle_row("all", [(f"All {len(attention)} vessels needing attention, per vessel",
                                 {"width": "stretch", "weight": "Bolder"})],
                        [_t("\n\n".join(per_lines), size="Small", spacing="None")] if per_lines else [])

    foot = [f"Known ({len(known)}): " + ", ".join(
        f"{_vkey(r)} ({next((f['message'] for f in r['findings'] if f['check'] == 'expected_status'), '')})"
        for r in sorted(known, key=lambda r: _vkey(r).lower())) if known else "",
            f"Clean ({len(clean)}): {', '.join(clean) or '–'}"]
    for r in results:
        for f in r["findings"]:
            if f["check"] in ("registry_expired", "registry_unparseable"):
                foot.append(f"Registry: {_vkey(r)}: {f['message']}")
    foot += [f"⚠ {w}" for w in run.get("warnings", [])
             if not (run.get("shares_missing") and w.startswith("Share(s) not reachable"))]  # banner says it
    off, exc = int(run.get("off_hire_count") or 0), int(run.get("excluded_count") or 0)
    if off or exc:
        foot.append(" · ".join(x for x in (f"Off-hire, not checked: {off}" if off else "",
                                          f"excluded: {exc}" if exc else "") if x))
    body.append(_t("\n\n".join(x for x in foot if x), isSubtle=True, size="Small", spacing="Medium"))

    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard", "version": "1.4",
        "msteams": {"width": "Full", **({"entities": ents} if ents else {})},
        "body": body,
    }
    if report_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open full report", "url": report_url}]
    return fit_card(card)


CARD_BUDGET = 24_000   # bytes of JSON; Teams refuses messages over ~28 KB (the webhook still says 202)


def _size(card: dict[str, Any]) -> int:
    import json
    return len(json.dumps(card, ensure_ascii=False).encode())


def fit_card(card: dict[str, Any], budget: int = CARD_BUDGET) -> dict[str, Any]:
    """Shorten the folded detail lists until the card fits. The full lists stay in the run file
    (results/<date>/<slot>.json) and the dry-run preview. Order: the per-vessel list first,
    then the largest other folded sections."""
    note = "… cut to fit Teams; full list in results/<date>/<slot>.json"
    containers = [b for b in card["body"] if b.get("type") == "Container" and b.get("id")]
    order = [c for c in containers if c["id"] == "all"] + sorted(
        (c for c in containers if c["id"] != "all"), key=lambda c: -_size(c))

    def shorten_text(tb: dict[str, Any]) -> bool:
        text = tb["text"].removesuffix(f"\n\n{note}").removesuffix(f" {note}")
        if "\n\n" in text:                                   # line per vessel: drop whole lines
            parts = text.split("\n\n")
            if len(parts) <= 1:
                return False
            tb["text"] = "\n\n".join(parts[:-1]) + f"\n\n{note}"
            return True
        if len(text) <= 80:                                   # one long line: cut at a separator
            return False
        cut = text[: int(len(text) * 0.8)]
        cut = cut[: max(cut.rfind(", "), cut.rfind(" · "), 40)]
        tb["text"] = f"{cut} {note}"
        return True

    for c in order:
        items = c["items"]
        while _size(card) > budget:
            if len(items) > 1:
                if items[-1].get("text") == note:
                    if len(items) == 2:
                        break
                    items.pop(-2)
                else:
                    items.pop()
                    items.append({"type": "TextBlock", "text": note, "wrap": True, "size": "Small",
                                  "isSubtle": True, "spacing": "None"})
            elif not (items and items[0].get("type") == "TextBlock" and shorten_text(items[0])):
                break
        if _size(card) <= budget:
            break
    return card
