"""Tests for model-aware max_tokens clamping."""

from __future__ import annotations

from kavi.messages import Message, TextBlock, ToolSchema
from kavi.providers.limits import (
    MIN_OUTPUT_TOKENS,
    clamp_max_tokens,
    estimate_input_tokens,
)


def test_no_clamp_for_unknown_provider():
    # Anthropic/OpenAI (paid, no known TPM cap) -> request passes through.
    assert clamp_max_tokens("anthropic", "claude-sonnet-4", 8192, 1000) == 8192


def test_groq_tpm_clamps_output():
    # gpt-oss-120b free tier: 8000 TPM. With ~1500 input tokens and 512 margin,
    # the reservation must fit: 8000 - 1500 - 512 = 5988.
    out = clamp_max_tokens("groq", "openai/gpt-oss-120b", 8192, 1500)
    assert out == 8000 - 1500 - 512


def test_groq_high_tpm_model_keeps_request():
    # llama-3.3-70b has a 12000 TPM cap; a 4096 request comfortably fits.
    out = clamp_max_tokens("groq", "llama-3.3-70b-versatile", 4096, 1500)
    assert out == 4096


def test_clamp_never_below_floor():
    # Huge input leaves no budget, but we still allow a minimal reply.
    out = clamp_max_tokens("groq", "openai/gpt-oss-20b", 8192, 100000)
    assert out == MIN_OUTPUT_TOKENS


def test_unknown_groq_model_uses_provider_default():
    # Falls back to the conservative Groq provider default (6000 TPM).
    out = clamp_max_tokens("groq", "some-new-model", 8192, 1000)
    assert out == 6000 - 1000 - 512


def test_estimate_input_tokens_grows_with_history():
    tools = [ToolSchema(name="Read", description="read a file", input_schema={"a": 1})]
    small = estimate_input_tokens("sys", [], tools)
    big = estimate_input_tokens(
        "sys",
        [Message(role="user", content=[TextBlock(text="x" * 4000)])],
        tools,
    )
    assert big > small
    assert big - small >= 900  # ~4000 chars / 4 tokens
