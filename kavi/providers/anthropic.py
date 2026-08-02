"""Anthropic (Claude) provider adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from kavi.messages import (
    ImageBlock,
    Message,
    MessageDone,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolResultBlock,
    ToolSchema,
    ToolUseArgsDelta,
    ToolUseBlock,
    ToolUseStart,
    Usage,
)
from kavi.providers.base import LLMProvider, ProviderNotConfigured

# Models that support extended thinking.
_THINKING_MODELS = ("claude-3-7", "claude-sonnet-4", "claude-opus-4", "claude-3.7")


def _to_anthropic_content(block: Any) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        out: dict[str, Any] = {"type": "thinking", "thinking": block.text}
        if block.signature:
            out["signature"] = block.signature
        return out
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }
    raise TypeError(f"Unknown block type: {block!r}")


def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    return [
        {"role": m.role, "content": [_to_anthropic_content(b) for b in m.content]}
        for m in messages
    ]


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, config) -> None:  # noqa: ANN001
        super().__init__(config)
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ProviderNotConfigured("`anthropic` package is not installed") from exc

        creds = config.creds_for(config.provider)
        if not creds.api_key:
            raise ProviderNotConfigured(
                "Anthropic API key not found. Set ANTHROPIC_API_KEY or configure credentials."
            )
        kwargs: dict[str, Any] = {"api_key": creds.api_key}
        if creds.base_url:
            kwargs["base_url"] = creds.base_url
        self._client = AsyncAnthropic(**kwargs)

    def supports_thinking(self, model: str) -> bool:
        return any(tag in model for tag in _THINKING_MODELS)

    async def list_models(self) -> list[str]:
        resp = await self._client.models.list(limit=100)
        return [m.id for m in resp.data]

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
    ) -> AsyncIterator:
        req: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": _to_anthropic_messages(messages),
        }
        if system:
            req["system"] = system
        if tools:
            req["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]

        use_thinking = thinking and self.supports_thinking(model)
        if use_thinking:
            budget = max(1024, min(thinking_budget_tokens, max_tokens - 1))
            req["thinking"] = {"type": "enabled", "budget_tokens": budget}
            req["temperature"] = 1.0  # required when thinking is enabled
        else:
            req["temperature"] = temperature

        index_to_tool_id: dict[int, str] = {}

        async with self._client.messages.stream(**req) as stream:
            async for event in stream:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        index_to_tool_id[event.index] = block.id
                        yield ToolUseStart(id=block.id, name=block.name)
                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", None)
                    if dtype == "text_delta":
                        yield TextDelta(text=delta.text)
                    elif dtype == "thinking_delta":
                        yield ThinkingDelta(text=delta.thinking)
                    elif dtype == "input_json_delta":
                        tool_id = index_to_tool_id.get(event.index, "")
                        yield ToolUseArgsDelta(id=tool_id, partial_json=delta.partial_json)

            final = await stream.get_final_message()

        yield MessageDone(
            message=_from_anthropic_final(final),
            usage=Usage(
                input_tokens=getattr(final.usage, "input_tokens", 0) or 0,
                output_tokens=getattr(final.usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(final.usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(final.usage, "cache_creation_input_tokens", 0) or 0,
            ),
            stop_reason=getattr(final, "stop_reason", None),
        )


def _from_anthropic_final(final: Any) -> Message:
    content: list[Any] = []
    for block in final.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            content.append(TextBlock(text=block.text))
        elif btype == "thinking":
            content.append(
                ThinkingBlock(text=block.thinking, signature=getattr(block, "signature", None))
            )
        elif btype == "tool_use":
            content.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))
    return Message(role="assistant", content=content)
