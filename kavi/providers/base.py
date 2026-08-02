"""Provider abstraction.

A provider adapter knows how to talk to one LLM backend. It translates Kavi's normalized
:class:`~kavi.messages.Message` list and :class:`~kavi.messages.ToolSchema` list into a
vendor request, streams the response, and yields normalized
:class:`~kavi.messages.StreamEvent` objects.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from kavi.config.schema import KaviConfig
from kavi.messages import Message, StreamEvent, ToolSchema


class ProviderError(RuntimeError):
    """Raised when a provider request fails in a non-retryable way."""


class ProviderNotConfigured(ProviderError):
    """Raised when required credentials are missing."""


class LLMProvider(abc.ABC):
    """Base class for all provider adapters."""

    name: str = "base"

    def __init__(self, config: KaviConfig) -> None:
        self.config = config

    @abc.abstractmethod
    async def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema],
        model: str,
        max_tokens: int,
        temperature: float,
        thinking: bool,
        thinking_budget_tokens: int,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a single assistant turn.

        Must yield incremental events and finish with exactly one
        :class:`~kavi.messages.MessageDone`.
        """
        raise NotImplementedError
        # pragma: no cover - abstract async generator
        if False:  # noqa
            yield  # type: ignore[misc]

    def supports_thinking(self, model: str) -> bool:  # noqa: ARG002
        return False

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> str:
        """Non-streaming, tool-free completion returning the final text.

        A convenience built on :meth:`stream` for cheap auxiliary calls (context
        compaction summaries, WebFetch page distillation). Works for every
        adapter without provider-specific code.
        """
        from kavi.messages import MessageDone

        text = ""
        async for event in self.stream(
            system=system,
            messages=messages,
            tools=[],
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=False,
            thinking_budget_tokens=0,
        ):
            if isinstance(event, MessageDone):
                text = event.message.text()
        return text

    async def list_models(self) -> list[str]:
        """Return the model ids available from this provider (best-effort)."""
        return []
