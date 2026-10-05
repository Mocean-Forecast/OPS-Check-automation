"""Render a Message as a Teams Adaptive Card (D6, D23).

Changes since 07:30 are marked with colour + bold: NEW = red bold, CHANGED = amber bold,
RESOLVED = green. Teams Workflow webhooks accept the card wrapped by `webhook_payload`.
"""

from __future__ import annotations

from typing import Any

from .diff import Change
from .message import Item, Message

_COLOR = {Change.NEW: "Attention", Change.CHANGED: "Warning", Change.RESOLVED: "Good", Change.SAME: "Default"}
_TAG = {Change.NEW: "NEW · ", Change.CHANGED: "CHANGED · ", Change.RESOLVED: "RESOLVED · ", Change.SAME: ""}


def _text(text: str, **kw: Any) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True, **kw}


def _heading(text: str) -> dict[str, Any]:
    return _text(text, weight="Bolder", size="Medium", spacing="Medium", separator=True)


def _item(i: Item) -> dict[str, Any]:
    prio = " **[priority]**" if i.priority else ""
    block = _text(f"{_TAG[i.change]}**{i.name}**{prio} – {i.text}", spacing="Small")
    if i.change != Change.SAME:
        block.update(color=_COLOR[i.change], weight="Bolder")
    return block


def _line(label: str, values: list[str]) -> dict[str, Any] | None:
    if not values:
        return None
    return _text(f"**{label} ({len(values)}):** {', '.join(values)}", spacing="Small")


def render_card(m: Message) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        _text(m.title, weight="Bolder", size="Large"),
        _text(m.subtitle, isSubtle=True, spacing="None"),
    ]
    if m.summary:
        body.append(_text(m.summary, spacing="Small"))

    body.append(_heading("NEEDS ACTION"))
    if not m.action and not m.resolved:
        body.append(_text("Nothing needs action.", color="Good"))
    body += [_item(i) for i in m.action]
    body += [_item(i) for i in m.resolved]

    body.append(_heading("STATUS"))
    if m.waiting:
        body.append(_text("**Waiting (%d):** " % len(m.waiting)
                          + " · ".join(f"{i.name} ({i.text})" for i in m.waiting), spacing="Small"))
    for block in (_line("DPR not filled", m.not_filled),
                  _line("DPR not signed off", m.not_signed_off),
                  _line("Known", m.known)):
        if block:
            body.append(block)
    if m.registry:
        body.append(_text("**Registry (%d):** " % len(m.registry) + " · ".join(m.registry),
                          spacing="Small", color="Warning"))
    for w in m.warnings:
        body.append(_text(f"⚠ {w}", spacing="Small", color="Warning"))
    if m.off_hire:
        body.append(_text(f"Off-hire, not checked: {m.off_hire}", isSubtle=True, size="Small",
                          spacing="Large", separator=True))

    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }


def render_priority_alert(i: Item, title: str) -> dict[str, Any]:
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            _text(f"PRIORITY VESSEL · {i.name}", weight="Bolder", size="Large", color="Attention"),
            _text(i.text, wrap=True),
            _text(title, isSubtle=True, size="Small"),
        ],
    }


def webhook_payload(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }
