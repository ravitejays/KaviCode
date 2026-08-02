"""Normalized message and streaming types shared across Kavi.

Everything internal is modeled on a Claude-style content-block message shape. Provider
adapters translate between these types and their vendor API. Keeping one internal shape
means the engine, session store, and UI never need provider-specific branching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["user", "assistant"]


# --------------------------------------------------------------------------- content blocks


@dataclass
class TextBlock:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ThinkingBlock:
    text: str
    # Opaque signature some providers require to replay thinking blocks.
    signature: str | None = None
    type: Literal["thinking"] = "thinking"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: Literal["tool_use"] = "tool_use"


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = "tool_result"


@dataclass
class ImageBlock:
    """A base64-encoded image, shown to vision-capable models in a user message."""

    data: str  # base64-encoded bytes (no data: prefix)
    media_type: str  # e.g. "image/png"
    type: Literal["image"] = "image"

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.data}"


ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock | ImageBlock


@dataclass
class Message:
    role: Role
    content: list[ContentBlock]

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]


# --------------------------------------------------------------------------- usage


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


# --------------------------------------------------------------------------- stream events
#
# Provider adapters emit these as they read a streamed response. The engine consumes them
# to update the UI incrementally and to assemble the final assistant Message.


@dataclass
class TextDelta:
    text: str
    type: Literal["text_delta"] = "text_delta"


@dataclass
class ThinkingDelta:
    text: str
    type: Literal["thinking_delta"] = "thinking_delta"


@dataclass
class ToolUseStart:
    id: str
    name: str
    type: Literal["tool_use_start"] = "tool_use_start"


@dataclass
class ToolUseArgsDelta:
    id: str
    partial_json: str
    type: Literal["tool_use_args_delta"] = "tool_use_args_delta"


@dataclass
class MessageDone:
    """Terminal event carrying the fully-assembled assistant message and usage."""

    message: Message
    usage: Usage
    stop_reason: str | None = None
    type: Literal["message_done"] = "message_done"


StreamEvent = TextDelta | ThinkingDelta | ToolUseStart | ToolUseArgsDelta | MessageDone


# --------------------------------------------------------------------------- tool schema


@dataclass
class ToolSchema:
    """Provider-agnostic description of a callable tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema
