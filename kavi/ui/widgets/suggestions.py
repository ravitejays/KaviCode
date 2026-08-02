"""Live slash-command autocomplete dropdown.

Appears above the prompt as the user types a ``/command``. Navigate with the
Up/Down arrows, complete with Tab, run with Enter, dismiss with Esc.
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from kavi.ui.theme import ACCENT


class CommandSuggestions(OptionList):
    """A filtered list of matching slash commands."""

    def __init__(self, *, id: str | None = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self._commands: list[tuple[str, str]] = []
        self._names: list[str] = []
        self.display = False
        self.can_focus = False

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Register the (name, description) pairs used for matching."""
        self._commands = list(commands)

    @property
    def is_active(self) -> bool:
        return bool(self.display) and bool(self._names)

    def current(self) -> str | None:
        idx = self.highlighted
        if idx is None or not (0 <= idx < len(self._names)):
            return None
        return self._names[idx]

    def move(self, delta: int) -> None:
        if not self._names:
            return
        idx = self.highlighted or 0
        idx = (idx + delta) % len(self._names)
        self.highlighted = idx
        self.scroll_to_highlight()

    def hide(self) -> None:
        self.display = False

    def update_for(self, text: str) -> None:
        """Recompute matches for the current input text and show/hide."""
        text = text or ""
        # Only while typing the command name itself (no space yet).
        if not text.startswith("/") or " " in text:
            self.hide()
            return

        token = text[1:].lower()
        matches = [(n, d) for (n, d) in self._commands if n.startswith(token)]
        if not matches:
            self.hide()
            return

        self.clear_options()
        self._names = [n for (n, _) in matches]
        for name, desc in matches:
            self.add_option(Option(self._render_option(name, desc), id=name))
        self.highlighted = 0
        self.display = True

    @staticmethod
    def _render_option(name: str, desc: str) -> Text:
        line = Text()
        line.append(f"/{name}", style=f"bold {ACCENT}")
        if desc:
            line.append(f"   {desc}", style="dim")
        return line
