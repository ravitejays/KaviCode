"""The main REPL screen: scrolling chat, working indicator, prompt, status bar."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from kavi.ui.widgets.prompt import PromptArea
from kavi.ui.widgets.status_bar import ModeLine, StatusBar
from kavi.ui.widgets.suggestions import CommandSuggestions
from kavi.ui.widgets.working import WorkingIndicator


class ReplScreen(Screen):
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat")
        yield Vertical(
            CommandSuggestions(id="suggest"),
            WorkingIndicator(),
            ModeLine(id="mode-line"),
            Horizontal(
                Static("\u276f", id="prompt-arrow"),
                PromptArea(
                    placeholder="Message Kavi  (/ for commands \u00b7 Alt+Enter for newline)",
                    id="prompt",
                ),
                id="prompt-row",
            ),
            StatusBar(id="status"),
            id="bottom-bar",
        )
