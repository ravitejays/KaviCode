"""OpenAI (and OpenAI-compatible) provider adapter.

Works with the OpenAI API and any OpenAI-compatible endpoint (OpenRouter, vLLM,
LM Studio, etc.) by setting ``base_url``. Uses the Chat Completions API with function
calling.
"""

from __future__ import annotations

import asyncio
import json
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


_THINK_OPEN = ("<think>", "<thinking>", "<reasoning>")
_THINK_CLOSE = ("</think>", "</thinking>", "</reasoning>")


def _find_first_tag(text: str, tags: tuple[str, ...]) -> tuple[int, str]:
    """Return the (index, tag) of the earliest tag found in ``text`` (case-insensitive)."""
    low = text.lower()
    best = -1
    best_tag = ""
    for tag in tags:
        idx = low.find(tag)
        if idx != -1 and (best == -1 or idx < best):
            best, best_tag = idx, tag
    return best, best_tag


def _partial_tag_suffix(text: str, tags: tuple[str, ...]) -> int:
    """Length of the trailing substring of ``text`` that could start one of ``tags``.

    Lets us hold back e.g. a dangling ``"<thi"`` until the next chunk arrives so a
    tag split across streamed chunks is still recognised.
    """
    low = text.lower()
    longest = 0
    for tag in tags:
        upper = min(len(tag) - 1, len(low))
        for k in range(upper, 0, -1):
            if tag.startswith(low[-k:]):
                longest = max(longest, k)
                break
    return longest


class _ThinkTagFilter:
    """Split streamed content that embeds chain-of-thought in ``<think>...</think>``.

    Some OpenAI-compatible endpoints (notably NVIDIA-hosted Qwen / Kimi / DeepSeek
    models) inline reasoning inside the ``content`` field wrapped in ``<think>`` tags
    instead of the separate ``reasoning_content`` field. Left alone, that reasoning
    gets pasted into the visible answer. This filter routes tagged spans to the
    thinking channel, tolerating tags that straddle chunk boundaries.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False

    def _kind(self) -> str:
        return "think" if self._in_think else "text"

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> list[tuple[str, str]]:
        out = self._drain(final=True)
        if self._buf:
            out.append((self._kind(), self._buf))
            self._buf = ""
        return out

    def _drain(self, *, final: bool) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        while True:
            tags = _THINK_CLOSE if self._in_think else _THINK_OPEN
            idx, tag = _find_first_tag(self._buf, tags)
            if idx == -1:
                hold = 0 if final else _partial_tag_suffix(self._buf, tags)
                cut = len(self._buf) - hold
                if cut > 0:
                    out.append((self._kind(), self._buf[:cut]))
                    self._buf = self._buf[cut:]
                return out
            if idx > 0:
                out.append((self._kind(), self._buf[:idx]))
            self._buf = self._buf[idx + len(tag) :]
            self._in_think = not self._in_think


def _clean_tool_name(name: str) -> str:
    """Strip arguments some models fuse into the name (e.g. ``Glob,{...}``).

    Guards the request we send back to the provider: a tool name that carries a
    JSON blob or paren-arg list would be rejected as "not in request.tools".
    """
    for sep in (",", "{", "(", "\n", " "):
        idx = name.find(sep)
        if idx > 0:
            name = name[:idx]
    return name.strip()


# Substrings in API errors that mean the model cannot handle image content.
_MULTIMODAL_ERROR_MARKERS = (
    "multimodal processing is not enabled",
    "multimodal data",
    "enable-multimodal",
    "does not support image",
    "image_url is not supported",
)

# Models known to lack vision support. Checked before the first request so we
# don't waste a round-trip sending an image that will be rejected.
_NON_VISION_MODELS = (
    "nemotron",
    "mistral-nemotron",
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v3",
    "deepseek-v4",
    "gpt-oss-",
    "qwen3",
    "codestral",
    "llama-3.3",
    "llama-4-scout",
)


def _is_non_vision_model(model: str) -> bool:
    """Best-effort check for models that are known to reject image content."""
    low = model.lower()
    return any(tag in low for tag in _NON_VISION_MODELS)


def _to_openai_messages(
    system: str, messages: list[Message], *, strip_images: bool = False,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for m in messages:
        if m.role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for b in m.content:
                if isinstance(b, TextBlock):
                    text_parts.append(b.text)
                elif isinstance(b, ThinkingBlock):
                    continue  # OpenAI does not accept reasoning content back
                elif isinstance(b, ToolUseBlock):
                    tool_calls.append(
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": _clean_tool_name(b.name),
                                "arguments": json.dumps(b.input),
                            },
                        }
                    )
            msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            out.append(msg)
        else:  # user
            tool_results = [b for b in m.content if isinstance(b, ToolResultBlock)]
            text_parts = [b.text for b in m.content if isinstance(b, TextBlock)]
            images = [b for b in m.content if isinstance(b, ImageBlock)]
            for tr in tool_results:
                out.append(
                    {"role": "tool", "tool_call_id": tr.tool_use_id, "content": tr.content}
                )
            if images and not strip_images:
                # Multimodal user message: text parts + image_url parts.
                parts: list[dict[str, Any]] = [
                    {"type": "text", "text": t} for t in text_parts if t
                ]
                parts.extend(
                    {"type": "image_url", "image_url": {"url": img.data_url()}}
                    for img in images
                )
                out.append({"role": "user", "content": parts})
            elif images and strip_images:
                # Model cannot handle images — replace with a text note so the
                # model knows an image was provided but cannot be shown.
                fallback = "".join(text_parts)
                fallback += (
                    "\n[An image/screenshot was captured but this model does not "
                    "support vision. Rely on the page text content above instead.]"
                )
                out.append({"role": "user", "content": fallback})
            elif text_parts or not tool_results:
                out.append({"role": "user", "content": "".join(text_parts)})
    return out


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, config) -> None:  # noqa: ANN001
        super().__init__(config)
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ProviderNotConfigured("`openai` package is not installed") from exc

        creds = config.creds_for(config.provider)
        if not creds.api_key and not creds.base_url:
            raise ProviderNotConfigured(
                "OpenAI API key not found. Set OPENAI_API_KEY or configure credentials."
            )
        self._base_url = creds.base_url
        kwargs: dict[str, Any] = {"api_key": creds.api_key or "not-needed", "max_retries": 1}
        if creds.base_url:
            kwargs["base_url"] = creds.base_url
        if creds.organization:
            kwargs["organization"] = creds.organization
        headers = dict(creds.default_headers)
        if config.provider.value == "sarvam" and creds.api_key:
            headers["api-subscription-key"] = creds.api_key
        if headers:
            kwargs["default_headers"] = headers
        # A generous connect timeout keeps local gateways / port-forwards that are
        # slow to accept the first connection from tripping the SDK's 5s default
        # (surfacing as an opaque "Connection error."). The read timeout bounds the
        # gap between streamed chunks: during active generation chunks arrive
        # frequently, so a bounded value only bites when a model never responds
        # (e.g. an NVIDIA catalog model not provisioned for the account) - turning
        # an infinite "working" spinner into a clear, actionable timeout error.
        try:
            import httpx

            kwargs["timeout"] = httpx.Timeout(120.0, connect=30.0)
        except Exception:  # noqa: BLE001 - fall back to SDK defaults
            pass
        self._client = AsyncOpenAI(**kwargs)

    async def list_models(self) -> list[str]:
        if self.config.provider.value == "workersai":
            from kavi.providers.presets import get_preset

            preset = get_preset("workersai")
            return preset.example_models if preset else []

        try:
            resp = await self._client.models.list()
            return sorted(m.id for m in resp.data)
        except Exception:
            # Fallback to preset models if the API call fails or is unsupported
            from kavi.providers.presets import get_preset

            preset = get_preset(self.config.provider.value)
            if preset and preset.example_models:
                return preset.example_models
            raise

    def _reshape_error(self, exc: Exception, model: str) -> Exception:
        """Turn opaque SDK errors into short, actionable guidance."""
        # Mid-stream stall: the model sent some chunks then stopped responding.
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return RuntimeError(
                f"'{model}' stopped responding mid-stream (no data for {self._CHUNK_TIMEOUT}s). "
                "The model may be overloaded or not provisioned for your account — "
                "try again, or pick another model with /model."
            )

        from openai import APIConnectionError, APITimeoutError, NotFoundError

        if isinstance(exc, APITimeoutError):
            return RuntimeError(
                f"'{model}' didn't respond in time. It may be cold-starting or not "
                "provisioned for your account - pick another model with /model."
            )
        if isinstance(exc, NotFoundError):
            return RuntimeError(
                f"'{model}' isn't available for your account (404). It's in the catalog "
                "but not served to you - pick another model with /model."
            )
        if isinstance(exc, APIConnectionError):
            target = self._base_url or "the API endpoint"
            cause = exc.__cause__ or exc
            return RuntimeError(
                f"Could not connect to {target}. "
                f"Is the gateway / port-forward running and reachable? (details: {cause})"
            )
        return exc

    # Maximum seconds to wait for the next streamed chunk before treating the
    # model as stalled. Generous enough for slow reasoning models, short enough
    # to surface genuine hangs (e.g. NVIDIA free-tier silent throttling).
    _CHUNK_TIMEOUT = 90

    @staticmethod
    async def _timed_stream(stream, timeout: float):
        """Wrap an async iterator with a per-item timeout.

        Raises ``asyncio.TimeoutError`` if no chunk arrives within *timeout*
        seconds, turning a silent model stall into a retryable error.
        """
        ait = stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(ait.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                break
            yield chunk

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
        thinking_budget_tokens: int,  # noqa: ARG002
    ) -> AsyncIterator:
        # Check if we should strip images before the first request to avoid
        # a wasted round-trip to a non-vision model.
        strip_images = _is_non_vision_model(model)

        from kavi.providers.limits import clamp_max_tokens

        effective_max_tokens = clamp_max_tokens(self.config.provider.value, model, max_tokens, 0)

        req: dict[str, Any] = {
            "model": model,
            "messages": _to_openai_messages(system, messages, strip_images=strip_images),
            "max_tokens": effective_max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            req["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]

        text_acc: list[str] = []
        think_acc: list[str] = []
        content_filter = _ThinkTagFilter()
        # index -> {"id", "name", "args"}
        tool_acc: dict[int, dict[str, Any]] = {}
        seen_tool_ids: set[int] = set()
        usage = Usage()
        stop_reason: str | None = None

        try:
            stream = await self._client.chat.completions.create(**req)
        except Exception as exc:  # noqa: BLE001 - reshape opaque errors into guidance
            raise self._reshape_error(exc, model) from exc

        try:
            async for chunk in self._timed_stream(stream, self._CHUNK_TIMEOUT):
                if getattr(chunk, "usage", None):
                    usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens or 0,
                        output_tokens=chunk.usage.completion_tokens or 0,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if choice.finish_reason:
                    stop_reason = choice.finish_reason

                # Reasoning models (gpt-oss, deepseek-reasoner, nemotron, Kimi, Qwen,
                # ...) stream chain-of-thought either in a separate field or inline in
                # <think> tags. Only surface it when the user enabled thinking mode;
                # otherwise drop it silently so it neither clutters the chat nor leaks
                # into the visible answer.
                reasoning = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if reasoning and thinking:
                    think_acc.append(reasoning)
                    yield ThinkingDelta(text=reasoning)

                if getattr(delta, "content", None):
                    for kind, seg in content_filter.feed(delta.content):
                        if kind == "text":
                            text_acc.append(seg)
                            yield TextDelta(text=seg)
                        elif thinking:
                            think_acc.append(seg)
                            yield ThinkingDelta(text=seg)

                for tc in getattr(delta, "tool_calls", None) or []:
                    idx = tc.index
                    slot = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if idx not in seen_tool_ids and slot["id"] and slot["name"]:
                        seen_tool_ids.add(idx)
                        yield ToolUseStart(id=slot["id"], name=slot["name"])
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
                        yield ToolUseArgsDelta(id=slot["id"], partial_json=tc.function.arguments)
        except Exception as exc:  # noqa: BLE001
            # Nothing streamed back at all -> surface actionable guidance. If we already
            # produced output, let the partial result stand rather than discarding it.
            if not text_acc and not tool_acc:
                raise self._reshape_error(exc, model) from exc
            raise

        # Flush any content the tag-filter was holding back (e.g. an unclosed
        # <think> span or a dangling partial tag at end-of-stream).
        for kind, seg in content_filter.flush():
            if kind == "text":
                text_acc.append(seg)
                yield TextDelta(text=seg)
            elif thinking:
                think_acc.append(seg)
                yield ThinkingDelta(text=seg)

        content: list[Any] = []
        joined_think = "".join(think_acc)
        if joined_think:
            content.append(ThinkingBlock(text=joined_think))
        joined = "".join(text_acc)
        if joined:
            content.append(TextBlock(text=joined))
        for _idx, slot in sorted(tool_acc.items()):
            raw_name = slot["name"]
            name = _clean_tool_name(raw_name)
            raw_args = slot["args"]
            # Recover arguments a model may have fused into the name field.
            if not raw_args.strip() and len(raw_name) > len(name):
                remainder = raw_name[len(name) :].lstrip(", ")
                if remainder.startswith("{"):
                    raw_args = remainder
            try:
                parsed = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                parsed = {}
            content.append(ToolUseBlock(id=slot["id"], name=name, input=parsed))

        yield MessageDone(
            message=Message(role="assistant", content=content),
            usage=usage,
            stop_reason=stop_reason,
        )
