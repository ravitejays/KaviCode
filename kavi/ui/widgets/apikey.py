"""Modal that asks the user to paste an API key for a provider."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class ApiKeyPromptScreen(ModalScreen[str | None]):
    """Prompt for a single API key. Dismisses with the entered key (or ``None``)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, label: str, docs: str = "", env: str | None = None, prompt_text: str | None = None, is_password: bool = True) -> None:
        super().__init__()
        self._label = label
        self._docs = docs
        self._env = env
        self._prompt_text = prompt_text or f"Enter your {self._label} API key"
        self._is_password = is_password

    def compose(self) -> ComposeResult:
        with Vertical(id="key-box"):
            yield Static(self._prompt_text, id="key-title")
            if self._docs:
                yield Static(f"Get a key at {self._docs}", id="key-sub")
            
            placeholder = "Paste your API key here…" if self._is_password else f"Paste your {self._label} here…"
            yield Input(
                placeholder=placeholder,
                password=self._is_password,
                id="key-input",
            )
            yield Static("↵ save & test connection · esc cancel", id="key-hint")

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)
