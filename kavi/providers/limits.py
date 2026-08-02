"""Model-aware token budgeting.

Some backends impose hard caps that make a large ``max_tokens`` fail outright:

* an output cap (the most tokens a model will generate in one reply), and
* a tokens-per-minute (TPM) rate cap that counts *input + reserved output*
  together - this is what trips free-tier Groq with a 413 "Request too large".

We keep a small, conservative table of known caps and clamp ``max_tokens`` so a
request fits, without the user having to tune it by hand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from kavi.messages import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)

# Never clamp below this, so replies are still useful.
MIN_OUTPUT_TOKENS = 512
# Safety headroom subtracted from a TPM budget to cover estimation error.
TPM_MARGIN = 512


@dataclass(frozen=True)
class ModelLimit:
    max_output_tokens: int | None = None  # hard cap on completion tokens
    tpm: int | None = None  # tokens-per-minute cap (counts input + output)


# Per-model overrides, keyed by exact model id.
MODEL_LIMITS: dict[str, ModelLimit] = {
    # Groq free tier (TPM counts input + reserved output).
    "llama-3.3-70b-versatile": ModelLimit(tpm=12000),
    "llama-3.1-8b-instant": ModelLimit(tpm=6000),
    "openai/gpt-oss-120b": ModelLimit(tpm=8000),
    "openai/gpt-oss-20b": ModelLimit(tpm=8000),
    "meta-llama/llama-4-scout-17b-16e-instruct": ModelLimit(tpm=30000),
    "qwen/qwen3-32b": ModelLimit(tpm=6000),
    # NVIDIA catalog — free-tier TPM caps (conservative estimates).
    "nvidia/nemotron-3-super-120b-a12b": ModelLimit(tpm=15000, max_output_tokens=4096),
    "mistralai/mistral-nemotron": ModelLimit(tpm=15000, max_output_tokens=4096),
    # Sarvam AI starter tier output token cap.
    "sarvam-105b": ModelLimit(max_output_tokens=4096),
}

# Provider-level fallback for models not in MODEL_LIMITS.
PROVIDER_LIMITS: dict[str, ModelLimit] = {
    # Conservative default for Groq's free tier.
    "groq": ModelLimit(tpm=6000),
    # NVIDIA free tier: generous but not unlimited.
    "nvidia": ModelLimit(tpm=20000),
    # Sarvam AI starter tier default cap.
    "sarvam": ModelLimit(max_output_tokens=4096),
}


def _estimate(text: str) -> int:
    # ~4 characters per token is a good rough estimate for English + code.
    return len(text) // 4


def _block_tokens(block: object) -> int:
    if isinstance(block, TextBlock | ThinkingBlock):
        return _estimate(block.text)
    if isinstance(block, ToolUseBlock):
        return _estimate(block.name) + _estimate(json.dumps(block.input))
    if isinstance(block, ToolResultBlock):
        return _estimate(block.content)
    return 0


def estimate_input_tokens(
    system: str, messages: list[Message], tools: list[ToolSchema]
) -> int:
    """Rough count of the tokens a request will send (system + history + tools)."""
    total = _estimate(system)
    for message in messages:
        total += sum(_block_tokens(b) for b in message.content) + 4
    for tool in tools:
        total += (
            _estimate(tool.name)
            + _estimate(tool.description)
            + _estimate(json.dumps(tool.input_schema))
        )
    return total + 16


def limit_for(provider_id: str, model: str) -> ModelLimit | None:
    return MODEL_LIMITS.get(model) or PROVIDER_LIMITS.get(provider_id)


def clamp_max_tokens(
    provider_id: str, model: str, requested: int, input_tokens: int
) -> int:
    """Clamp ``requested`` output tokens to fit the model's output and TPM caps."""
    limit = limit_for(provider_id, model)
    if limit is None:
        return requested

    capped = requested
    if limit.max_output_tokens:
        capped = min(capped, limit.max_output_tokens)
    if limit.tpm:
        budget = limit.tpm - input_tokens - TPM_MARGIN
        capped = min(capped, budget)

    # Keep within [MIN_OUTPUT_TOKENS, requested].
    capped = max(MIN_OUTPUT_TOKENS, capped)
    return min(capped, requested)
