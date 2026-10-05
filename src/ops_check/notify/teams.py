"""Post to Teams through a Workflow (Power Automate) webhook (D23). Standard library only."""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class PostError(RuntimeError):
    pass


def post(webhook_url: str, payload: dict[str, Any], timeout: float = 30) -> None:
    if not webhook_url:
        raise PostError("No webhook URL configured")
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 300:
                raise PostError(f"Webhook returned HTTP {resp.status}")
    except OSError as e:  # URLError, HTTPError, timeouts
        raise PostError(str(e)) from e
