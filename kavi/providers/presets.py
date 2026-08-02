"""Provider presets.

A single table describing every LLM backend Kavi knows about: which adapter drives it,
its OpenAI-compatible base URL, the environment variable that holds its key, and a few
example model ids for display. Most modern providers (Groq, OpenRouter, Together, ...) are
OpenAI-compatible, so they all reuse the OpenAI adapter with a different base URL.

This module intentionally imports nothing from ``kavi`` to keep it dependency-free and
avoid import cycles (``config.schema`` imports it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    kind: Literal["anthropic", "openai"]  # which adapter to use
    default_model: str
    base_url: str | None = None  # None for OpenAI/Anthropic native hosts
    api_key_env: str | None = None  # None means no key required (e.g. local Ollama)
    example_models: list[str] = field(default_factory=list)
    docs: str = ""
    # Optional OpenAI-compatible extras for gateways that need them.
    organization: str | None = None
    default_headers: dict[str, str] = field(default_factory=dict)
    base_url_env: str | None = None  # env var that overrides base_url
    organization_env: str | None = None  # env var that overrides organization


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "anthropic": ProviderPreset(
        id="anthropic",
        label="Anthropic (Claude)",
        kind="anthropic",
        default_model="claude-sonnet-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
        example_models=["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-3-5-haiku-latest"],
        docs="https://console.anthropic.com/",
    ),
    "openai": ProviderPreset(
        id="openai",
        label="OpenAI",
        kind="openai",
        default_model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        example_models=["gpt-4o", "gpt-4o-mini", "o3-mini"],
        docs="https://platform.openai.com/api-keys",
    ),
    "groq": ProviderPreset(
        id="groq",
        label="Groq",
        kind="openai",
        default_model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        example_models=[
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
            "moonshotai/kimi-k2-instruct",
        ],
        docs="https://console.groq.com/keys",
    ),
    "openrouter": ProviderPreset(
        id="openrouter",
        label="OpenRouter",
        kind="openai",
        default_model="openai/gpt-4o-mini",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        example_models=["openai/gpt-4o", "anthropic/claude-sonnet-4", "meta-llama/llama-3.3-70b-instruct"],
        docs="https://openrouter.ai/keys",
    ),
    "together": ProviderPreset(
        id="together",
        label="Together AI",
        kind="openai",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
        example_models=[
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "deepseek-ai/DeepSeek-V3",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
        ],
        docs="https://api.together.ai/settings/api-keys",
    ),
    "deepseek": ProviderPreset(
        id="deepseek",
        label="DeepSeek",
        kind="openai",
        default_model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        example_models=["deepseek-chat", "deepseek-reasoner"],
        docs="https://platform.deepseek.com/api_keys",
    ),
    "xai": ProviderPreset(
        id="xai",
        label="xAI (Grok)",
        kind="openai",
        default_model="grok-2-latest",
        base_url="https://api.x.ai/v1",
        api_key_env="XAI_API_KEY",
        example_models=["grok-2-latest", "grok-2-vision-latest"],
        docs="https://console.x.ai/",
    ),
    "fireworks": ProviderPreset(
        id="fireworks",
        label="Fireworks AI",
        kind="openai",
        default_model="accounts/fireworks/models/llama-v3p3-70b-instruct",
        base_url="https://api.fireworks.ai/inference/v1",
        api_key_env="FIREWORKS_API_KEY",
        example_models=[
            "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "accounts/fireworks/models/deepseek-v3",
        ],
        docs="https://fireworks.ai/account/api-keys",
    ),
    "cerebras": ProviderPreset(
        id="cerebras",
        label="Cerebras",
        kind="openai",
        default_model="llama-3.3-70b",
        base_url="https://api.cerebras.ai/v1",
        api_key_env="CEREBRAS_API_KEY",
        example_models=["llama-3.3-70b", "llama3.1-8b"],
        docs="https://cloud.cerebras.ai/",
    ),
    "mistral": ProviderPreset(
        id="mistral",
        label="Mistral AI",
        kind="openai",
        default_model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
        example_models=["mistral-large-latest", "codestral-latest", "mistral-small-latest"],
        docs="https://console.mistral.ai/api-keys/",
    ),
    "perplexity": ProviderPreset(
        id="perplexity",
        label="Perplexity",
        kind="openai",
        default_model="sonar",
        base_url="https://api.perplexity.ai",
        api_key_env="PERPLEXITY_API_KEY",
        example_models=["sonar", "sonar-pro", "sonar-reasoning"],
        docs="https://www.perplexity.ai/settings/api",
    ),
    "moonshot": ProviderPreset(
        id="moonshot",
        label="Moonshot (Kimi)",
        kind="openai",
        default_model="kimi-k2-0711-preview",
        base_url="https://api.moonshot.ai/v1",
        api_key_env="MOONSHOT_API_KEY",
        example_models=["kimi-k2-0711-preview", "moonshot-v1-128k"],
        docs="https://platform.moonshot.ai/console/api-keys",
    ),
    "zai": ProviderPreset(
        id="zai",
        label="Z.AI (GLM)",
        kind="openai",
        default_model="glm-4.6",
        base_url="https://api.z.ai/api/paas/v4",
        api_key_env="ZAI_API_KEY",
        example_models=["glm-4.6", "glm-4.5-air"],
        docs="https://z.ai/",
    ),
    "nvidia": ProviderPreset(
        id="nvidia",
        label="NVIDIA (build.nvidia.com)",
        kind="openai",
        default_model="moonshotai/kimi-k2.6",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key_env="NVIDIA_API_KEY",
        # NVIDIA lists ~120 catalog models but only a subset are actually served to a
        # given account (others return 404 or never respond). These are verified to
        # respond reliably; they are surfaced first in the /model picker.
        example_models=[
            "moonshotai/kimi-k2.6",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3.5-397b-a17b",
            "qwen/qwen3-next-80b-a3b-instruct",
            "deepseek-ai/deepseek-v4-flash",
            "nvidia/nemotron-3-super-120b-a12b",
            "mistralai/mistral-nemotron",
            "z-ai/glm-5.2",
        ],
        docs="https://build.nvidia.com/ (generate an API key, starts with 'nvapi-')",
    ),
    "genai-rpc": ProviderPreset(
        id="genai-rpc",
        label="GenAI RPC (internal gateway)",
        kind="openai",
        default_model="gpt-4o",
        base_url="http://localhost:5436/v1",
        api_key_env="LLM_API_KEY",
        base_url_env="LLM_API_BASE",
        organization="f393aca3-1ec6-449c-87e1-5c3f7b9af033",
        organization_env="LLM_ORGANIZATION",
        default_headers={"Rpc-Service": "genai-api", "Rpc-Caller": "dco-raih"},
        example_models=["gpt-4o", "gpt-4o-mini", "claude-sonnet-4"],
        docs="internal genai-api gateway",
    ),
    "ollama": ProviderPreset(
        id="ollama",
        label="Ollama (local)",
        kind="openai",
        default_model="qwen2.5-coder:7b",
        base_url="http://localhost:11434/v1",
        api_key_env=None,  # no key needed for local
        example_models=[
            "qwen2.5-coder:7b",
            "qwen2.5-coder:14b",
            "qwen2.5-coder:32b",
            "llama3.1:8b",
            "llama3.1:70b",
            "deepseek-r1:8b",
            "deepseek-r1:14b",
            "deepseek-coder-v2:16b",
            "codellama:7b",
            "mistral:7b",
            "gemma2:9b",
            "phi3:14b",
            "codegemma:7b",
        ],
        docs="https://ollama.com/ — install models with: ollama pull <model>",
    ),
    "workersai": ProviderPreset(
        id="workersai",
        label="Cloudflare Workers AI",
        kind="openai",
        default_model="@cf/moonshotai/kimi-k2.6",
        # base_url is constructed dynamically from the user's Account ID:
        # https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1
        base_url=None,
        api_key_env="CLOUDFLARE_API_TOKEN",
        example_models=[
            "@cf/moonshotai/kimi-k2.6",
            "@cf/moonshotai/kimi-k2.7-code",
            "@cf/meta/llama-4-scout-17b-16e-instruct",
            "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
            "@cf/qwen/qwen2.5-coder-32b-instruct",
        ],
        docs="https://dash.cloudflare.com/ (create an API token + copy your Account ID)",
    ),
    "sarvam": ProviderPreset(
        id="sarvam",
        label="Sarvam AI",
        kind="openai",
        default_model="sarvam-105b",
        base_url="https://api.sarvam.ai/v1",
        api_key_env="SARVAM_API_KEY",
        example_models=["sarvam-105b"],
        docs="https://dashboard.sarvam.ai/",
    ),
}


def get_preset(provider_id: str) -> ProviderPreset | None:
    return PROVIDER_PRESETS.get(provider_id)


def openai_compatible_ids() -> list[str]:
    return [p.id for p in PROVIDER_PRESETS.values() if p.kind == "openai"]
