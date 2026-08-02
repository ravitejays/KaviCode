"""The prompt input: a soft-wrapping, multi-line text area wired to the
slash-command suggestion dropdown.

Key handling:
  - Enter            : submit (or accept the highlighted command when the menu is open)
  - Alt/Shift+Enter  : insert a newline (compose multi-line messages)
  - Up / Down        : move the suggestion highlight (when the menu is open)
  - Tab              : complete the highlighted command
  - Esc              : dismiss the menu, or interrupt a running task
Pasting multi-line text inserts it verbatim (no accidental submit).
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from kavi.ui.widgets.suggestions import CommandSuggestions


class PromptArea(TextArea):
    """A multi-line prompt that cooperates with a :class:`CommandSuggestions` dropdown."""

    class Submitted(Message):
        """Posted when the user submits the prompt."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, placeholder: str = "", id: str | None = None) -> None:  # noqa: A002
        super().__init__(
            "",
            soft_wrap=True,
            tab_behavior="focus",
            show_line_numbers=False,
            compact=True,
            placeholder=placeholder,
            id=id,
        )

    # -- helpers -----------------------------------------------------------------

    def _suggestions(self) -> CommandSuggestions | None:
        try:
            return self.app.query_one(CommandSuggestions)
        except Exception:  # noqa: BLE001
            return None

    def _submit(self) -> None:
        text = self.text
        self.text = ""
        self.post_message(self.Submitted(text))

    # -- key handling ------------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        sug = self._suggestions()
        active = bool(sug and sug.is_active)
        key = event.key

        # Newline: Alt/Shift+Enter (and Ctrl+J) compose multi-line input.
        if key in ("alt+enter", "shift+enter", "ctrl+j"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return

        if key == "enter":
            event.stop()
            event.prevent_default()
            if active and sug is not None:
                name = sug.current()
                if name:
                    self.text = f"/{name}"
                sug.hide()
            self._submit()
            return

        if active and sug is not None and key in ("up", "down", "tab"):
            event.stop()
            event.prevent_default()
            if key == "up":
                sug.move(-1)
            elif key == "down":
                sug.move(1)
            else:  # tab -> complete the highlighted command
                name = sug.current()
                if name:
                    self.text = f"/{name} "
                    self.move_cursor(self.document.end)
                sug.hide()
            return

        if key == "escape":
            event.stop()
            event.prevent_default()
            if active and sug is not None:
                sug.hide()
            else:
                interrupt = getattr(self.app, "action_interrupt_task", None)
                if callable(interrupt):
                    interrupt()
            return

        await super()._on_key(event)
