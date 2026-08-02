"""Interactive permission dialog (modal)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PermissionDialog(ModalScreen[str]):
    """Ask the user to approve a tool call. Returns 'allow', 'always', or 'deny'."""

    BINDINGS = [
        ("y", "choose('allow')", "Allow once"),
        ("a", "choose('always')", "Always allow"),
        ("n", "choose('deny')", "Deny"),
        ("escape", "choose('deny')", "Deny"),
    ]

    def __init__(self, tool_name: str, subject: str, render: str) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._subject = subject
        # NB: don't name this ``_render`` - that shadows Textual's Widget._render().
        self._render_text = render

    def compose(self) -> ComposeResult:
        with Vertical(id="perm-box"):
            yield Static(f"Allow {self._tool_name}?", id="perm-title")
            yield Static(self._render_text, id="perm-subject")
            with Horizontal(id="perm-buttons"):
                yield Button("Allow (y)", variant="success", id="allow")
                yield Button("Always (a)", variant="primary", id="always")
                yield Button("Deny (n)", variant="error", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def action_choose(self, choice: str) -> None:
        self.dismiss(choice)
