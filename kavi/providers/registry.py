"""Provider registry - construct the right :class:`LLMProvider` for a config."""

from __future__ import annotations

from kavi.config.schema import KaviConfig, Provider
from kavi.providers.base import LLMProvider
from kavi.providers.presets import get_preset


def build_provider(config: KaviConfig) -> LLMProvider:
    """Instantiate the provider adapter selected by ``config.provider``.

    Anthropic uses its native adapter. Every other provider is OpenAI-compatible and is
    served by the OpenAI adapter, with the base URL taken from the provider preset (unless
    the user configured an explicit ``base_url``).
    """
    provider = config.provider
    preset = get_preset(provider.value)

    if provider == Provider.ANTHROPIC:
        from kavi.providers.anthropic import AnthropicProvider

        return AnthropicProvider(config)

    if provider == Provider.OLLAMA:
        from kavi.providers.ollama import OllamaProvider

        return OllamaProvider(config)

    # All other providers (OpenAI, Groq, OpenRouter, Together, DeepSeek, xAI, ...) are
    # OpenAI-compatible. Inject the preset base URL into the credentials if missing.
    from kavi.providers.openai import OpenAIProvider

    if preset is not None:
        creds = config.creds_for(provider)
        if not creds.base_url and preset.base_url:
            creds.base_url = preset.base_url
        if not creds.organization and preset.organization:
            creds.organization = preset.organization
        if not creds.default_headers and preset.default_headers:
            creds.default_headers = dict(preset.default_headers)
        config.credentials[provider.value] = creds
    return OpenAIProvider(config)


__all__ = ["build_provider"]
