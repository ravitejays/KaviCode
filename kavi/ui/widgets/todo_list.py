"""TodoList widget - a Claude-Code-style task checklist.

Renders the agent's structured task list as a boxed checklist: completed items
get a struck-through green check, the in-progress item is bold with a filled
marker, pending items are dim. Fed by the ``TodoWrite`` tool's ``ui_payload``.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from kavi.ui.theme import ACCENT

_BOX_DONE = "\u2611"  # ☑
_BOX_OPEN = "\u2610"  # ☐
_BOX_ACTIVE = "\u25c9"  # ◉
_BOX_CANCELLED = "\u2612"  # ☒


class TodoList(Static):
    """A checklist of the agent's plan, updated in place as work progresses."""

    def __init__(self, todos: list[dict] | None = None) -> None:
        super().__init__(classes="todo-list")
        self.set_todos(todos or [])

    def set_todos(self, todos: list[dict]) -> None:
        done = sum(1 for t in todos if t.get("status") == "completed")
        total = len(todos)
        header = Text()
        header.append("\u2637 ", style=f"bold {ACCENT}")  # ☷ plan glyph
        header.append("To-dos ", style="bold")
        header.append(f"({done}/{total})", style="dim")

        rows: list[Text] = [header]
        for t in todos:
            rows.append(self._row(t.get("status", "pending"), str(t.get("content", ""))))
        self.update(Group(*rows))

    @staticmethod
    def _row(status: str, content: str) -> Text:
        row = Text("  ")
        if status == "completed":
            row.append(f"{_BOX_DONE} ", style="green")
            row.append(content, style="green strike")
        elif status == "in_progress":
            row.append(f"{_BOX_ACTIVE} ", style=f"bold {ACCENT}")
            row.append(content, style="bold")
        elif status == "cancelled":
            row.append(f"{_BOX_CANCELLED} ", style="dim")
            row.append(content, style="dim strike")
        else:  # pending
            row.append(f"{_BOX_OPEN} ", style="dim")
            row.append(content, style="dim")
        return row
