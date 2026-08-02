"""ToolCard widget - renders a tool invocation inline, Claude-Code style.

A tool call is shown as a compact ``⏺ Title  args`` header with a ``⎿`` tree
branch underneath holding the first few lines of the result (dim, clipped).
No border box: the calls flow inline with the transcript, which reads much
cleaner than a boxed card for a stream of reads/greps/edits.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from kavi.tools.base import ToolResult

# Accent (brand emerald) + neutral greys, matched to the rest of the UI.
_ACCENT = "#3fb950"
_GREY = "#8b949e"
_CONNECTOR = "#6e7681"

_BULLET = "\u23fa"  # ⏺
_BRANCH = "\u23bf"  # ⎿

# How many result lines to show inline (errors get a bigger budget so a
# traceback / failing command isn't clipped to nothing).
_MAX_RESULT_LINES = 4
_MAX_ERROR_LINES = 16
# Clip over-long single lines so the branch never wraps into a mess.
_MAX_LINE_WIDTH = 120


def _line_style(text: str, is_error: bool) -> str:
    """Per-line colour for a result body row (diff-aware, error-aware)."""
    if is_error:
        return "red"
    stripped = text.lstrip()
    if stripped.startswith("+") and not stripped.startswith("+++"):
        return "green"
    if stripped.startswith("-") and not stripped.startswith("---"):
        return "red"
    return _GREY


class ToolCard(Static):
    """Shows a running tool call, updated in place when the result arrives."""

    def __init__(self, render: str) -> None:
        super().__init__(classes="tool-card")
        self._render_line = render
        self._show_running()

    # -- header ------------------------------------------------------------------

    def _header(self) -> Text:
        """``⏺ Title  args`` — accent bullet, bold title, dim argument tail."""
        line = Text()
        line.append(f"{_BULLET} ", style=f"bold {_ACCENT}")
        name, _, arg = self._render_line.partition(" ")
        line.append(name, style="bold")
        if arg:
            line.append(f"  {arg}", style=_GREY)
        return line

    # -- branch (the ⎿ result tree) ----------------------------------------------

    @staticmethod
    def _branch_row(text: str, style: str, first: bool) -> Text:
        row = Text()
        if first:
            row.append("  ")
            row.append(_BRANCH, style=_CONNECTOR)
            row.append("  ")
        else:
            row.append("     ")
        row.append(text, style=style)
        return row

    def _branch_running(self) -> Text:
        return self._branch_row("running\u2026", "dim italic", first=True)

    def _branch_result(self, result: ToolResult) -> list[Text]:
        body = result.display if result.display is not None else result.content
        text = body if (body and body.strip()) else (result.title or "(no output)")
        lines = text.splitlines() or [""]
        budget = _MAX_ERROR_LINES if result.is_error else _MAX_RESULT_LINES
        clipped = lines[:budget]
        extra = len(lines) - len(clipped)

        rows: list[Text] = []
        for i, raw in enumerate(clipped):
            shown = raw if len(raw) <= _MAX_LINE_WIDTH else raw[: _MAX_LINE_WIDTH - 1] + "\u2026"
            rows.append(self._branch_row(shown, _line_style(raw, result.is_error), first=i == 0))
        if extra > 0:
            noun = "line" if extra == 1 else "lines"
            rows.append(self._branch_row(f"\u2026 +{extra} {noun}", "dim", first=False))
        return rows

    # -- state -------------------------------------------------------------------

    def _show_running(self) -> None:
        self.update(Group(self._header(), self._branch_running()))

    def set_progress(self, output: str) -> None:
        """Update the tool card with partial progress output."""
        # Split the output and take up to the last _MAX_RESULT_LINES.
        lines = output.splitlines()
        budget = _MAX_RESULT_LINES
        clipped = lines[-budget:] if len(lines) > budget else lines
        
        rows: list[Text] = []
        for i, raw in enumerate(clipped):
            shown = raw if len(raw) <= _MAX_LINE_WIDTH else raw[: _MAX_LINE_WIDTH - 1] + "\u2026"
            rows.append(self._branch_row(shown, _line_style(raw, False), first=i == 0))
        
        self.update(Group(self._header(), *rows))

    def set_result(self, result: ToolResult) -> None:
        if result.is_error:
            self.add_class("-error")
        self.update(Group(self._header(), *self._branch_result(result)))
