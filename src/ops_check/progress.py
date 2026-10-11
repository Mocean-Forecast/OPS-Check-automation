"""Progress output for a run: timed steps + a bar for the share scan.

In a terminal: a live bar on one line (stderr). Without a terminal (launchd → logs/<date>.log):
plain lines, one per vessel with its time, so the log shows where the minutes went.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Iterator, TextIO


class Progress:
    def __init__(self, stream: TextIO = sys.stderr, enabled: bool = True):
        self.out = stream
        self.enabled = enabled
        self.tty = enabled and hasattr(stream, "isatty") and stream.isatty()
        self.t0 = time.monotonic()

    def _w(self, text: str) -> None:
        if self.enabled:
            self.out.write(text)
            self.out.flush()

    def _clock(self) -> str:
        s = int(time.monotonic() - self.t0)
        return f"{s // 60:02d}:{s % 60:02d}"

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """`[00:12] Scanning shares …` … `done (4.1 s)`."""
        start = time.monotonic()
        self._w(f"[{self._clock()}] {label} …" + ("" if self.tty else "\n"))
        try:
            yield
        finally:
            took = time.monotonic() - start
            if self.tty:
                self._w(f"\r\033[K[{self._clock()}] {label} … done ({took:.1f} s)\n")
            else:
                self._w(f"[{self._clock()}] {label} done ({took:.1f} s)\n")

    def item(self, i: int, n: int, name: str, seconds: float) -> None:
        """One unit of a long step (a vessel's folders)."""
        if self.tty:
            width = 30
            filled = int(width * i / n) if n else width
            bar = "█" * filled + "░" * (width - filled)
            self._w(f"\r\033[K  {bar} {i}/{n}  {name[:28]} ({seconds:.1f} s)")
            if i == n:
                self._w("\r\033[K")
        else:
            self._w(f"  {i}/{n} {name}: {seconds:.1f} s\n")

    def note(self, text: str) -> None:
        self._w(f"[{self._clock()}] {text}\n")


NULL = Progress(enabled=False)
