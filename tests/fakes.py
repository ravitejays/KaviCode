"""Test helpers: a scripted fake LLM provider."""

from __future__ import annotations

from collections.abc import AsyncIterator

from kavi.messages import (
    Message,
    MessageDone,
    StreamEvent,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)
from kavi.providers.base import LLMProvider


def text_turn(text: str) -> list[StreamEvent]:
    return [
        TextDelta(text=text),
        MessageDone(
            message=Message(role="assistant", content=[TextBlock(text=text)]),
            usage=Usage(input_tokens=10, output_tokens=5),
            stop_reason="end_turn",
        ),
    ]


def tool_turn(tool_id: str, name: str, tool_input: dict) -> list[StreamEvent]:
    return [
        MessageDone(
            message=Message(
                role="assistant",
                content=[ToolUseBlock(id=tool_id, name=name, input=tool_input)],
            ),
            usage=Usage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        ),
    ]


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, turns: list[list[StreamEvent]]) -> None:
        self._turns = list(turns)
        self.calls = 0

    async def stream(self, **kwargs) -> AsyncIterator[StreamEvent]:  # noqa: ANN003
        if not self._turns:
            for event in text_turn("done"):
                yield event
            return
        events = self._turns.pop(0)
        self.calls += 1
        for event in events:
            yield event


class FlakyProvider(LLMProvider):
    """Raises ``error`` for the first ``fail_times`` calls, then streams ``turns``."""

    name = "flaky"

    def __init__(
        self, fail_times: int, error: Exception, turns: list[list[StreamEvent]]
    ) -> None:
        self._fail_times = fail_times
        self._error = error
        self._turns = list(turns)
        self.calls = 0

    async def stream(self, **kwargs) -> AsyncIterator[StreamEvent]:  # noqa: ANN003
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
            yield  # pragma: no cover - makes this an async generator
        events = self._turns.pop(0) if self._turns else text_turn("done")
        for event in events:
            yield event
