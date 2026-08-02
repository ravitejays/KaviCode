"""Ollama provider adapter.

Ollama exposes an OpenAI-compatible API at ``/v1``, so this adapter reuses the OpenAI
implementation with a local default base URL and no real API key.

Key differences from the cloud OpenAI adapter:

* **No ``stream_options``** – Ollama's ``/v1`` endpoint does not support
  ``{"include_usage": true}`` and will reject requests that include it.
* **Generous timeouts** – local models may need 30-60 s to cold-start (load into
  VRAM) the first time they are called.  We use 300 s read / 60 s connect.
* **Native model listing** – queries ``/api/tags`` for an accurate list of
  locally-downloaded models, falling back to the OpenAI ``/v1/models`` endpoint.
* **Tool-call fallback** – many smaller local models do not support function
  calling. When a tool-call request fails with an "unsupported" error, the
  provider transparently retries without tools so the model can still answer in
  plain text.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from kavi.config.schema import Provider
from kavi.messages import (
    Message,
    StreamEvent,
    ToolSchema,
)
from kavi.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434/v1"

# Substrings in error messages that indicate the model does not support
# function/tool calling — these trigger an automatic text-only retry.
_TOOL_UNSUPPORTED_MARKERS = (
    "does not support tools",
    "does not support function",
    "tool_use is not supported",
    "tools is not supported",
    "tool use is not supported",
    "unknown parameter: tools",
    "invalid parameter: tools",
)


def _base_url_from_host(host: str) -> str:
    """Derive the OpenAI-compat base URL from an ``OLLAMA_HOST`` value.

    ``OLLAMA_HOST`` is the standard env var used by the ``ollama`` CLI. It may
    contain just a host:port (``localhost:11434``) or a full URL. We normalise
    it to ``http://<host>/v1`` for the OpenAI-compatible endpoint.
    """
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    return f"{host}/v1"


class OllamaProvider(OpenAIProvider):
    name = "ollama"

    def __init__(self, config) -> None:  # noqa: ANN001
        # Ensure a base_url and dummy key are present before the OpenAI client is built.
        creds = config.creds_for(Provider.OLLAMA)
        if not creds.base_url:
            creds.base_url = DEFAULT_BASE_URL
        if not creds.api_key:
            creds.api_key = "ollama"
        config.credentials[Provider.OLLAMA.value] = creds
        super().__init__(config)

        # Override timeouts — local models can take a long time to cold-start
        # (loading weights into VRAM). The default 120 s read timeout from the
        # OpenAI adapter is too short for large models on modest hardware.
        try:
            import httpx

            self._client.timeout = httpx.Timeout(300.0, connect=60.0)
        except Exception:  # noqa: BLE001
            pass

        # Remember the raw Ollama host for native API calls (e.g. /api/tags).
        self._ollama_base = creds.base_url.replace("/v1", "").rstrip("/")

    def supports_thinking(self, model: str) -> bool:  # noqa: ARG002
        return False

    def _reshape_error(self, exc: Exception, model: str) -> Exception:
        """Turn Ollama errors into clear, local-context guidance."""
        from openai import APIConnectionError, APITimeoutError, NotFoundError

        if isinstance(exc, NotFoundError):
            return RuntimeError(
                f"Model '{model}' is not downloaded in Ollama. "
                f"Pull it first with: ollama pull {model}\n"
                "Or pick an installed model with /model."
            )
        if isinstance(exc, APITimeoutError):
            return RuntimeError(
                f"'{model}' didn't respond in time. It may still be loading into memory "
                "(this can take a while for large models). Try again in a moment, "
                "or pick a smaller model with /model."
            )
        if isinstance(exc, APIConnectionError):
            return RuntimeError(
                f"Could not connect to Ollama at {self._ollama_base}. "
                "Make sure Ollama is running: ollama serve"
            )
        return exc

    # Ollama ignores unknown fields, but keep the request minimal.
    _extra: dict[str, Any] = {}

    # -- streaming ---------------------------------------------------------------

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
        """Stream a turn, handling Ollama-specific quirks.

        1. Strips ``stream_options`` (Ollama's ``/v1`` rejects it).
        2. If the request fails because the model doesn't support tools,
           automatically retries without the ``tools`` parameter so the model
           can still provide a plain-text answer.
        """
        try:
            async for event in self._stream_inner(
                system=system,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
                thinking_budget_tokens=thinking_budget_tokens,
            ):
                yield event
        except Exception as exc:  # noqa: BLE001
            if tools and _is_tool_unsupported_error(exc):
                logger.info(
                    "Model '%s' does not support tools; retrying without tools.", model
                )
                async for event in self._stream_inner(
                    system=system,
                    messages=messages,
                    tools=[],
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking=thinking,
                    thinking_budget_tokens=thinking_budget_tokens,
                ):
                    yield event
            else:
                raise

    async def _stream_inner(
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
        """Delegate to the parent OpenAI adapter, but monkey-patch the request
        to remove ``stream_options`` which Ollama does not support.
        """
        # We patch the client's create method to intercept and fix the request.
        original_create = self._client.chat.completions.create

        async def _patched_create(**kwargs: Any) -> Any:
            # Remove stream_options — Ollama rejects it.
            kwargs.pop("stream_options", None)
            return await original_create(**kwargs)

        self._client.chat.completions.create = _patched_create  # type: ignore[assignment]
        try:
            async for event in super().stream(
                system=system,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
                thinking_budget_tokens=thinking_budget_tokens,
            ):
                yield event
        finally:
            self._client.chat.completions.create = original_create  # type: ignore[assignment]

    # -- model listing -----------------------------------------------------------

    async def list_models(self) -> list[str]:
        """List locally-downloaded Ollama models via the native ``/api/tags`` endpoint.

        Falls back to the OpenAI-compatible ``/v1/models`` endpoint (which also
        works) if the native call fails.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._ollama_base}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    return sorted(models)
        except Exception:  # noqa: BLE001
            logger.debug("Native /api/tags failed; falling back to /v1/models")

        # Fallback to OpenAI-compat endpoint
        return await super().list_models()

    # -- health check ------------------------------------------------------------

    async def check_health(self) -> tuple[bool, str]:
        """Ping the Ollama server. Returns ``(ok, detail_message)``.

        Used by ``/provider`` and ``/doctor`` to give the user clear feedback
        about whether Ollama is running and has models downloaded.
        """
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._ollama_base}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                models = [m["name"] for m in data.get("models", [])]
                if models:
                    return True, f"Ollama is running ({len(models)} models: {', '.join(models[:5])}{'…' if len(models) > 5 else ''})"
                return True, "Ollama is running but no models are downloaded. Run: ollama pull <model>"
        except Exception as exc:  # noqa: BLE001
            return False, (
                f"Cannot reach Ollama at {self._ollama_base} ({exc}).\n"
                "Make sure Ollama is running: ollama serve"
            )


def _is_tool_unsupported_error(exc: Exception) -> bool:
    """Check if an exception indicates the model doesn't support tools."""
    message = str(exc).lower()
    return any(marker in message for marker in _TOOL_UNSUPPORTED_MARKERS)
