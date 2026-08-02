"""Tests for provider message translation (no network)."""

from __future__ import annotations

from kavi.messages import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from kavi.providers.anthropic import _to_anthropic_content, _to_anthropic_messages
from kavi.providers.openai import (
    _ThinkTagFilter,
    _is_non_vision_model,
    _to_openai_messages,
)


def _run_filter(*chunks: str) -> list[tuple[str, str]]:
    """Feed chunks through the filter and merge consecutive same-kind segments."""
    flt = _ThinkTagFilter()
    segs: list[tuple[str, str]] = []
    for chunk in chunks:
        segs.extend(flt.feed(chunk))
    segs.extend(flt.flush())
    merged: list[list[str]] = []
    for kind, text in segs:
        if merged and merged[-1][0] == kind:
            merged[-1][1] += text
        else:
            merged.append([kind, text])
    return [(k, t) for k, t in merged]


def test_anthropic_content_blocks():
    assert _to_anthropic_content(TextBlock(text="hi")) == {"type": "text", "text": "hi"}
    tu = _to_anthropic_content(ToolUseBlock(id="1", name="Read", input={"file_path": "x"}))
    assert tu["type"] == "tool_use" and tu["name"] == "Read"
    tr = _to_anthropic_content(ToolResultBlock(tool_use_id="1", content="ok"))
    assert tr["type"] == "tool_result" and tr["tool_use_id"] == "1"


def test_anthropic_messages_roundtrip_shape():
    msgs = [
        Message(role="user", content=[TextBlock(text="hello")]),
        Message(role="assistant", content=[ToolUseBlock(id="1", name="LS", input={})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="1", content="a.txt")]),
    ]
    out = _to_anthropic_messages(msgs)
    assert out[0]["role"] == "user"
    assert out[1]["content"][0]["type"] == "tool_use"
    assert out[2]["content"][0]["type"] == "tool_result"


def test_openai_translation_expands_tool_results():
    msgs = [
        Message(role="assistant", content=[ToolUseBlock(id="1", name="LS", input={})]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="1", content="a.txt")]),
    ]
    out = _to_openai_messages("system prompt", msgs)
    assert out[0] == {"role": "system", "content": "system prompt"}
    assert out[1]["role"] == "assistant"
    assert out[1]["tool_calls"][0]["function"]["name"] == "LS"
    # tool result becomes a role=tool message
    assert out[2]["role"] == "tool" and out[2]["tool_call_id"] == "1"


def test_think_filter_extracts_inline_reasoning():
    assert _run_filter("Hello <think>reasoning</think>world") == [
        ("text", "Hello "),
        ("think", "reasoning"),
        ("text", "world"),
    ]


def test_think_filter_handles_tags_split_across_chunks():
    assert _run_filter("Hello <thi", "nk>reason", "ing</thi", "nk>done") == [
        ("text", "Hello "),
        ("think", "reasoning"),
        ("text", "done"),
    ]


def test_think_filter_passes_plain_text_and_lone_angle_brackets():
    assert _run_filter("just text") == [("text", "just text")]
    assert _run_filter("compare a < b and c > d") == [
        ("text", "compare a < b and c > d")
    ]


def test_think_filter_flushes_unclosed_reasoning():
    assert _run_filter("<think>never closed") == [("think", "never closed")]


def test_openai_translation_strips_images_for_non_vision_models():
    assert _is_non_vision_model("nvidia/nemotron-3-super-120b-a12b") is True
    assert _is_non_vision_model("openai/gpt-4o") is False

    dummy_img = ImageBlock(data="aGVsbG8=", media_type="image/png")
    msgs = [
        Message(role="user", content=[TextBlock(text="look at this"), dummy_img]),
    ]

    out_normal = _to_openai_messages("", msgs, strip_images=False)
    assert isinstance(out_normal[0]["content"], list)
    assert any(part.get("type") == "image_url" for part in out_normal[0]["content"])

    out_stripped = _to_openai_messages("", msgs, strip_images=True)
    assert isinstance(out_stripped[0]["content"], str)
    assert "does not support vision" in out_stripped[0]["content"]

