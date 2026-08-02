"""Status widgets for the bottom bar.

- :class:`ModeLine`  - a small right-aligned "\u25cf mode" line above the prompt.
- :class:`StatusBar` - a two-row footer: model / cost / mode, then key hints.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from kavi.ui.theme import ACCENT


class ModeLine(Static):
    """The right-aligned status dot shown just above the prompt."""

    state: reactive[str] = reactive("ready")
    mode: reactive[str] = reactive("default")

    def render(self) -> Text:
        working = self.state == "working"
        label = "working\u2026" if working else self.mode
        line = Text(justify="right")
        line.append("\u25cf ", style=ACCENT if working else "dim")
        line.append(label, style=ACCENT if working else "dim")
        return line


class StatusBar(Static):
    model: reactive[str] = reactive("")
    provider: reactive[str] = reactive("")
    cost: reactive[str] = reactive("$0.0000 | 0 tok")
    state: reactive[str] = reactive("ready")
    mode: reactive[str] = reactive("default")

    def render(self) -> Table:
        info = Text()
        info.append(f"{self.provider}:{self.model}", style=f"bold {ACCENT}")
        info.append("  |  ", style="dim")
        info.append(self.cost, style="dim")
        info.append("  |  ", style="dim")
        if self.mode and self.mode != "default":
            style = "bold red" if self.mode == "bypass" else ACCENT
            info.append(f"mode {self.mode}", style=style)
        else:
            info.append("mode default", style="dim")

        hints = Text()
        if self.state == "working":
            hints.append("esc", style=ACCENT)
            hints.append(" interrupt   ", style="dim")
            hints.append("^C", style="dim")
            hints.append(" quit", style="dim")
        else:
            hints.append("\u21b5", style="dim")
            hints.append(" send   ", style="dim")
            hints.append("/", style="dim")
            hints.append(" commands   ", style="dim")
            hints.append("^C", style="dim")
            hints.append(" quit", style="dim")

        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_row(info)
        grid.add_row(hints)
        return grid
