"""A generic, arrow-navigable selection modal for interactive command menus.

Used by commands like ``/provider`` and ``/model`` to let the user pick an option
with the Up/Down arrows (and optionally filter by typing) instead of retyping ids.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from kavi.commands.base import SelectOption
from kavi.ui.theme import ACCENT


class _FilterInput(Input):
    """A filter box that steers the sibling option list."""

    BINDINGS = [
        Binding("up", "list_up", show=False, priority=True),
        Binding("down", "list_down", show=False, priority=True),
        Binding("enter", "list_select", show=False, priority=True),
    ]

    def action_list_up(self) -> None:
        self.screen.move_highlight(-1)  # type: ignore[attr-defined]

    def action_list_down(self) -> None:
        self.screen.move_highlight(1)  # type: ignore[attr-defined]

    def action_list_select(self) -> None:
        self.screen.select_highlighted()  # type: ignore[attr-defined]


class SelectionScreen(ModalScreen[str | None]):
    """Present ``options`` and dismiss with the chosen option id (or ``None``)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        title: str,
        options: list[SelectOption],
        *,
        filterable: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._filtered = options
        self._filterable = filterable and len(options) > 8

    def compose(self) -> ComposeResult:
        with Vertical(id="select-box"):
            yield Static(self._title, id="select-title")
            if self._filterable:
                yield _FilterInput(placeholder="Type to filter…", id="select-filter")
            yield OptionList(id="select-list")
            yield Static("↑/↓ navigate · ↵ select · esc cancel", id="select-hint")

    def on_mount(self) -> None:
        self._populate(self._options)
        if self._filterable:
            self.query_one("#select-filter", Input).focus()
        else:
            self.query_one("#select-list", OptionList).focus()

    # -- population / filtering --------------------------------------------------

    def _populate(self, options: list[SelectOption]) -> None:
        ol = self.query_one("#select-list", OptionList)
        ol.clear_options()
        self._filtered = options
        for opt in options:
            ol.add_option(Option(self._render_option(opt), id=opt.id))
        if options:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.strip().lower()
        if not q:
            self._populate(self._options)
            return
        matches = [
            o for o in self._options if q in o.label.lower() or q in o.id.lower()
        ]
        self._populate(matches)

    # -- navigation / selection --------------------------------------------------

    def move_highlight(self, delta: int) -> None:
        ol = self.query_one("#select-list", OptionList)
        if ol.option_count == 0:
            return
        idx = (ol.highlighted or 0) + delta
        ol.highlighted = idx % ol.option_count
        ol.scroll_to_highlight()

    def select_highlighted(self) -> None:
        ol = self.query_one("#select-list", OptionList)
        idx = ol.highlighted
        if idx is not None and 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx].id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @staticmethod
    def _render_option(opt: SelectOption) -> Text:
        line = Text()
        line.append(opt.label, style=f"bold {ACCENT}")
        if opt.description:
            line.append(f"   {opt.description}", style="dim")
        return line
