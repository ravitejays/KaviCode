"""The simple, clean welcome banner shown at the top of a fresh session.

Mirrors the clean ASCII box header layout with model, directory, and tip line.
"""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from kavi import __version__
from kavi.ui.theme import ACCENT


class WelcomeBanner(Static):
    """The clean ASCII welcome header echoing the exact terminal mockup layout."""

    def __init__(self, *, provider: str, model: str, cwd: Path) -> None:
        super().__init__(classes="welcome")
        self._provider = provider
        self._model = model
        self._cwd = cwd

    def on_mount(self) -> None:
        self.update(self._build())

    def _short_cwd(self) -> str:
        cwd = str(self._cwd)
        home = str(Path.home())
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        return cwd

    def _build(self) -> Table:
        # Internal grid
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="dim", justify="left")
        grid.add_column(justify="left")
        grid.add_column(justify="right")

        # Row 1: Kavi Code (v0.1.0)
        title_row = Text()
        title_row.append("Kavi Code ", style=f"bold {ACCENT}")
        title_row.append(f"(v{__version__})", style="dim")
        grid.add_row(title_row, "", "")
        grid.add_row("", "", "")  # spacer

        # Row 2: model
        m_label = Text("model:     ", style="dim")
        m_val = Text(f"{self._provider}:{self._model}", style="white")
        m_hint = Text("/model to change", style=ACCENT)
        grid.add_row(m_label, m_val, m_hint)

        # Row 3: directory
        d_label = Text("directory: ", style="dim")
        d_val = Text(str(self._cwd), style="white")
        grid.add_row(d_label, d_val, "")

        # Box table (auto width to fit content, not extending full window)
        box_table = Table(
            box=box.ROUNDED,
            border_style=ACCENT,
            show_header=False,
            expand=False,
            padding=(0, 1),
        )
        box_table.add_column()
        box_table.add_row(grid)

        # Outer container table including Tip line
        outer = Table.grid(padding=0, expand=False)
        outer.add_column()
        outer.add_row(box_table)

        tip = Text()
        tip.append("Tip: ", style="bold white")
        tip.append("Use ", style="dim")
        tip.append("/help", style=ACCENT)
        tip.append(" to see all commands or ", style="dim")
        tip.append("/mode", style=ACCENT)
        tip.append(" to switch permission modes.", style="dim")
        outer.add_row(Text(""))  # spacing
        outer.add_row(tip)
        outer.add_row(Text(""))  # spacing

        return outer


