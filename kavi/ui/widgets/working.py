"""WorkingIndicator - an animated braille "thinking" line above the input bar.

Mirrors the b-code / Claude-Code experience: while the agent works, a small
braille spinner cycles alongside a status verb, the elapsed time, and an
"esc to interrupt" hint. Hidden entirely when idle.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import Static

from kavi.ui.theme import ACCENT

_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"  # ⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏

_VERBS = [
    "Thinking",
    "Working",
    "Cooking",
    "Crunching",
    "Pondering",
    "Brewing",
    "Computing",
    "Wrangling",
]


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    return f"{m}m {sec}s"


class WorkingIndicator(Static):
    """A one-line animated status shown only while the agent is busy."""

    def __init__(self) -> None:
        super().__init__("", id="working")
        self._frame = 0
        self._start_t = 0.0
        self._timer = None

    def start(self) -> None:
        self._frame = 0
        self._start_t = time.monotonic()
        self.add_class("-active")
        self._render_frame()
        if self._timer is None:
            self._timer = self.set_interval(0.1, self._tick)
        else:
            self._timer.resume()

    def stop(self) -> None:
        self.remove_class("-active")
        if self._timer is not None:
            self._timer.pause()

    def _tick(self) -> None:
        self._frame += 1
        self._render_frame()

    def _render_frame(self) -> None:
        elapsed = time.monotonic() - self._start_t
        spinner = _FRAMES[self._frame % len(_FRAMES)]
        line = Text()
        line.append(f"{spinner} ", style=f"bold {ACCENT}")
        line.append("Working\u2026 ", style="italic")
        line.append(f"({_fmt_elapsed(elapsed)} \u00b7 esc to interrupt)", style="dim")
        self.update(line)
