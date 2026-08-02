"""Serialization for sessions - convert messages to/from JSON-friendly dicts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from kavi.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

_BLOCK_TYPES = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_use": ToolUseBlock,
    "tool_result": ToolResultBlock,
}


def block_to_dict(block: Any) -> dict[str, Any]:
    return asdict(block)


def block_from_dict(data: dict[str, Any]) -> Any:
    btype = data.get("type")
    cls = _BLOCK_TYPES.get(btype)
    if cls is None:
        return TextBlock(text=str(data))
    payload = {k: v for k, v in data.items() if k != "type"}
    return cls(**payload)


def message_to_dict(message: Message) -> dict[str, Any]:
    return {"role": message.role, "content": [block_to_dict(b) for b in message.content]}


def message_from_dict(data: dict[str, Any]) -> Message:
    return Message(
        role=data["role"],
        content=[block_from_dict(b) for b in data.get("content", [])],
    )


@dataclass
class SessionMeta:
    id: str
    created_at: str
    updated_at: str
    cwd: str
    provider: str
    model: str
    title: str = ""
    message_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        known = {f: data.get(f) for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**known)  # type: ignore[arg-type]


@dataclass
class LoadedSession:
    meta: SessionMeta
    messages: list[Message] = field(default_factory=list)
