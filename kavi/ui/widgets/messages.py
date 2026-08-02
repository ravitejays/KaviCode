"""Chat message widgets for the Kavi REPL."""

from __future__ import annotations

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, TableElement
from rich.measure import Measurement
from rich.style import Style
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from textual.widgets import Static

from kavi.ui.theme import ACCENT

# Rich's default inline-code style is ``bold cyan on black`` and code blocks use
# a dark syntax theme. The hard "on black" fill looks broken on a light theme,
# so we override those styles per active theme (see `_ThemedCode`).
_LIGHT_MD_THEME = Theme(
    {
        "markdown.code": Style(color="#c0392b", bgcolor="#eceef1", bold=True),
        "markdown.code_block": Style(color="#24292f"),
        "markdown.h1": Style(color="#3fb950", bold=True, underline=True),
        "markdown.h1.border": Style(color="#3fb950"),
        "markdown.h2": Style(color="#3fb950", bold=True),
        "markdown.h3": Style(color="#58a6ff", bold=True),
        "markdown.h4": Style(color="#d2a8ff", bold=True),
        "markdown.h5": Style(color="#d2a8ff", bold=False, italic=True),
    },
    inherit=True,
)
_DARK_MD_THEME = Theme(
    {
        "markdown.code": Style(color="#7ee787", bgcolor="#161b22", bold=True),
        "markdown.code_block": Style(color="#e6edf3", bgcolor="default"),
        "markdown.h1": Style(color="#3fb950", bold=True, underline=True),
        "markdown.h1.border": Style(color="#3fb950"),
        "markdown.h2": Style(color="#3fb950", bold=True),
        "markdown.h3": Style(color="#58a6ff", bold=True),
        "markdown.h4": Style(color="#d2a8ff", bold=True),
        "markdown.h5": Style(color="#d2a8ff", bold=False, italic=True),
    },
    inherit=True,
)


class _GridTableElement(TableElement):
    """Render markdown tables as a full grid with clean neutral borders."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(
            box=box.ROUNDED,
            border_style="dim",
            show_edge=True,
            show_lines=True,
            pad_edge=True,
        )
        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                heading = column.content.copy()
                heading.stylize("bold")
                table.add_column(heading)
        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*(element.content for element in row.cells))
        yield table


class _KaviFenceElement(Markdown.elements["fence"]):  # type: ignore[name-defined]
    """Fenced code blocks with a near-black background instead of the theme default."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        from rich.syntax import Syntax

        code = str(self.text).rstrip()
        syntax = Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=1,
            background_color="#0d1117",
        )
        yield syntax


class KaviMarkdown(Markdown):
    """Markdown with full-grid tables and dark code blocks."""

    elements = {
        **Markdown.elements,
        "table_open": _GridTableElement,
        "fence": _KaviFenceElement,
    }


class _ThemedCode:
    """Render a markdown renderable under a theme-appropriate code style."""

    def __init__(self, renderable: KaviMarkdown, *, dark: bool) -> None:
        self._renderable = renderable
        self._theme = _DARK_MD_THEME if dark else _LIGHT_MD_THEME

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with console.use_theme(self._theme):
            yield from console.render(self._renderable, options)

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement.get(console, options, self._renderable)


class UserMessage(Static):
    """A message typed by the user."""

    def __init__(self, text: str) -> None:
        content = Text()
        content.append("> ", style="bold dim")
        content.append(text, style="bold white")
        super().__init__(content, classes="msg msg-user")


class AssistantMessage(Static):
    """A streaming assistant message; text is appended as deltas arrive."""

    def __init__(self) -> None:
        super().__init__("", classes="msg msg-assistant")
        self._buffer = ""

    def _is_dark(self) -> bool:
        """Whether the active Textual theme is dark (defaults to dark)."""
        try:
            return bool(self.app.current_theme.dark)
        except Exception:  # noqa: BLE001 - not mounted / theme unavailable
            return True

    def _markdown(self) -> _ThemedCode:
        """Markdown whose code styling adapts to the active light/dark theme.

        Rich defaults (monokai blocks + `bold cyan on black` inline code) render
        as an ugly black fill on light themes; `_ThemedCode` overrides them.
        """
        dark = self._is_dark()
        code_theme = "monokai" if dark else "friendly"
        return _ThemedCode(KaviMarkdown(self._buffer, code_theme=code_theme), dark=dark)

    def append(self, delta: str) -> None:
        self._buffer += delta
        self.update(self._markdown())

    def set_text(self, text: str) -> None:
        self._buffer = text
        self.update(self._markdown())

    @property
    def is_empty(self) -> bool:
        return not self._buffer.strip()


class ThinkingMessage(Static):
    """Dim, italic display of the model's extended thinking."""

    def __init__(self) -> None:
        super().__init__("", classes="msg msg-thinking")
        self._buffer = ""

    def append(self, delta: str) -> None:
        self._buffer += delta
        self.update(Text(self._buffer, style="italic"))

    @property
    def is_empty(self) -> bool:
        return not self._buffer.strip()


class NoticeMessage(Static):
    """A system notice (info / errors / retries)."""

    def __init__(self, text: str) -> None:
        super().__init__(Text(f"* {text}", style="italic"), classes="msg msg-notice")


class ResponseStats(Static):
    """A small dim line showing time and token stats after a response."""

    def __init__(self, elapsed_secs: float, input_tokens: int, output_tokens: int) -> None:
        parts: list[str] = []
        if elapsed_secs < 60:
            parts.append(f"{elapsed_secs:.1f}s")
        else:
            mins = int(elapsed_secs // 60)
            secs = elapsed_secs % 60
            parts.append(f"{mins}m {secs:.0f}s")
        parts.append(f"{input_tokens:,} in")
        parts.append(f"{output_tokens:,} out")
        label = " · ".join(parts)
        super().__init__(
            Text(f"  {label}", style="dim italic"),
            classes="msg msg-stats",
        )
