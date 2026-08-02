"""Tests for summarizing context compaction."""

from __future__ import annotations

from kavi.agent.context import Conversation, render_transcript
from kavi.messages import Message, TextBlock, ToolResultBlock, ToolUseBlock


def _big_convo() -> Conversation:
    convo = Conversation(system_prompt="sys", max_context_tokens=50)
    convo.add_user_text("initial task: build a thing")
    for i in range(12):
        convo.add_message(
            Message(role="assistant", content=[TextBlock(text=f"assistant turn {i} " * 20)])
        )
        convo.add_user_text(f"user reply {i} " * 20)
    return convo


def test_render_transcript_includes_roles_and_tools():
    msgs = [
        Message(role="user", content=[TextBlock(text="hello")]),
        Message(role="assistant", content=[ToolUseBlock(id="1", name="Read", input={"file_path": "x"})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="1", content="contents")]),
    ]
    out = render_transcript(msgs)
    assert "[user] hello" in out
    assert "assistant->Read" in out
    assert "tool result" in out


async def test_compact_with_summary_folds_history():
    convo = _big_convo()
    original_len = len(convo.messages)
    assert convo.needs_compaction()

    async def fake_summarize(transcript: str) -> str:
        assert "assistant turn" in transcript
        return "SUMMARY: built a thing across many steps."

    changed = await convo.compact_with_summary(fake_summarize)
    assert changed
    assert len(convo.messages) < original_len
    # First message preserved (the initial task).
    assert "initial task" in convo.messages[0].text()
    # Summary injected as the second message.
    assert "SUMMARY:" in convo.messages[1].text()


async def test_compact_with_summary_falls_back_when_empty():
    convo = _big_convo()

    async def empty_summarize(transcript: str) -> str:
        return ""

    changed = await convo.compact_with_summary(empty_summarize)
    assert changed
    # Falls back to the lossy trim notice.
    assert "trimmed to save context" in convo.messages[1].text()


async def test_no_compaction_when_small():
    convo = Conversation(system_prompt="sys", max_context_tokens=100_000)
    convo.add_user_text("hi")

    async def summarize(t: str) -> str:
        return "should not be called"

    assert not await convo.compact_with_summary(summarize)
