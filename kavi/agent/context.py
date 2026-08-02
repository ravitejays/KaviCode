"""Conversation context - message history and context-window management."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from kavi.messages import (
    ContentBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

# A summarizer turns a slice of transcript text into a dense hand-off summary.
Summarizer = Callable[[str], Awaitable[str]]


def estimate_tokens(messages: list[Message]) -> int:
    """Very rough token estimate (~4 chars per token) used for compaction decisions."""
    total = 0
    for m in messages:
        for b in m.content:
            if isinstance(b, (TextBlock, ThinkingBlock)):
                total += len(b.text)
            elif isinstance(b, ToolUseBlock):
                total += len(str(b.input)) + len(b.name)
            elif isinstance(b, ToolResultBlock):
                total += len(b.content)
    return total // 4


class Conversation:
    """Holds the running message history and the system prompt."""

    def __init__(self, system_prompt: str, max_context_tokens: int = 80_000) -> None:
        self.system_prompt = system_prompt
        self.messages: list[Message] = []
        self.max_context_tokens = max_context_tokens

    def add_user_text(self, text: str) -> None:
        self.messages.append(Message(role="user", content=[TextBlock(text=text)]))

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def add_tool_results(self, results: list[ToolResultBlock]) -> None:
        content: list[ContentBlock] = list(results)
        self.messages.append(Message(role="user", content=content))

    def token_estimate(self) -> int:
        return estimate_tokens(self.messages)

    def needs_compaction(self) -> bool:
        return self.token_estimate() > self.max_context_tokens

    def _split_for_compaction(self) -> tuple[Message, list[Message], list[Message]] | None:
        """Return (first, stale, tail) or None if there is nothing to compact.

        Keeps the initial task message plus the most recent ~half of the
        conversation; the middle "stale" slice is what gets dropped or
        summarized. The tail never starts with an orphan tool_result.
        """
        if len(self.messages) <= 4 or not self.needs_compaction():
            return None
        first = self.messages[0]
        # Keep only the most recent ~1/3 of the conversation to compact aggressively.
        keep_from = max(1, len(self.messages) * 2 // 3)
        tail = self.messages[keep_from:]
        while tail and any(isinstance(b, ToolResultBlock) for b in tail[0].content):
            keep_from += 1
            tail = self.messages[keep_from:]
        if not tail:
            return None
        stale = self.messages[1:keep_from]
        return first, stale, tail

    def compact(self) -> bool:
        """Drop the oldest turns while preserving the first user message.

        Lossy but cheap (no LLM call): keeps the initial task message plus the
        most recent turns. Prefer :meth:`compact_with_summary` when a summarizer
        is available.
        """
        split = self._split_for_compaction()
        if split is None:
            return False
        first, _stale, tail = split
        notice = Message(
            role="user",
            content=[TextBlock(text="[Earlier conversation was trimmed to save context.]")],
        )
        self.messages = [first, notice, *tail]
        return True

    async def compact_with_summary(self, summarize: Summarizer) -> bool:
        """Summarize stale history with a small model instead of dropping it.

        Keeps the initial task message and recent turns verbatim; folds the
        middle of the conversation into a dense summary so long tasks keep going
        without forgetting earlier decisions. Falls back to lossy trimming if the
        summarizer returns nothing.
        """
        split = self._split_for_compaction()
        if split is None:
            return False
        first, stale, tail = split
        transcript = render_transcript(stale)
        summary = ""
        if transcript.strip():
            try:
                summary = (await summarize(transcript)).strip()
            except Exception:  # noqa: BLE001 - never crash a turn on summarization
                summary = ""
        if not summary:
            return self.compact()
        note = Message(
            role="user",
            content=[
                TextBlock(
                    text="[Summary of earlier conversation, compacted to save context]\n\n"
                    + summary
                )
            ],
        )
        self.messages = [first, note, *tail]

        # Safety valve: if we are STILL over 2x the limit after summarizing
        # (e.g. the tail alone is massive), do a hard lossy trim of the tail.
        if self.token_estimate() > self.max_context_tokens * 2:
            half_tail = len(self.messages) // 2
            self.messages = [self.messages[0], self.messages[1]] + self.messages[half_tail:]

        return True


def render_transcript(messages: list[Message]) -> str:
    """Flatten messages into a plain-text transcript for summarization."""
    lines: list[str] = []
    for m in messages:
        for b in m.content:
            if isinstance(b, TextBlock):
                if b.text.strip():
                    lines.append(f"[{m.role}] {b.text}")
            elif isinstance(b, ToolUseBlock):
                lines.append(f"[{m.role}->{b.name}] {str(b.input)[:2000]}")
            elif isinstance(b, ToolResultBlock):
                lines.append(f"[tool result] {b.content[:2000]}")
    return "\n".join(lines)
