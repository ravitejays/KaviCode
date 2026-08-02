"""Tests for session persistence and project memory."""

from __future__ import annotations

from pathlib import Path

from kavi.memory.loader import init_project_memory, load_memory
from kavi.messages import Message, TextBlock, ToolUseBlock
from kavi.session.models import message_from_dict, message_to_dict
from kavi.session.store import SessionStore


def test_message_serialization_roundtrip():
    msg = Message(
        role="assistant",
        content=[TextBlock(text="hi"), ToolUseBlock(id="1", name="LS", input={"path": "."})],
    )
    restored = message_from_dict(message_to_dict(msg))
    assert restored.role == "assistant"
    assert isinstance(restored.content[0], TextBlock)
    assert isinstance(restored.content[1], ToolUseBlock)
    assert restored.content[1].input == {"path": "."}


def test_session_store_create_append_load(tmp_path: Path):
    store = SessionStore(root=tmp_path / "sessions")
    meta = store.create(tmp_path, "anthropic", "claude-sonnet-4")
    store.append(meta, Message(role="user", content=[TextBlock(text="hello world")]))
    store.append(meta, Message(role="assistant", content=[TextBlock(text="hi")]))

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].title == "hello world"
    assert listed[0].message_count == 2

    loaded = store.load(meta.id)
    assert loaded is not None
    assert len(loaded.messages) == 2
    assert loaded.messages[0].text() == "hello world"


def test_memory_discovery_and_init(tmp_path: Path):
    assert load_memory(tmp_path) is None
    path = init_project_memory(tmp_path)
    assert path.exists()
    mem = load_memory(tmp_path)
    assert mem is not None and "KAVI.md" in mem
