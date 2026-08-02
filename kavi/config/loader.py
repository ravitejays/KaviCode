"""Load and merge Kavi configuration from disk and environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import platformdirs

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from kavi.config.schema import (
    KaviConfig,
    Provider,
    ProviderCredentials,
)
from kavi.providers.presets import PROVIDER_PRESETS

APP_DIRNAME = "kavi"


def user_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_DIRNAME))


def user_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_DIRNAME))


def sessions_dir() -> Path:
    d = user_data_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def profiles_dir() -> Path:
    """Directory holding user-defined profile files (``~/.kavi/profiles``)."""
    return user_config_dir() / "profiles"


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _find_project_config(cwd: Path) -> Path | None:
    """Walk upward from cwd looking for a project-level ``.kavi.toml``."""
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".kavi.toml"
        if candidate.is_file():
            return candidate
    return None


# Extra base-url overrides beyond the per-provider preset default.
_ENV_BASE_URLS = {
    Provider.OPENAI: "OPENAI_BASE_URL",
    Provider.OLLAMA: "OLLAMA_BASE_URL",
}


def _apply_env_credentials(cfg: KaviConfig) -> None:
    """Fill in credentials from environment variables when not set in config.

    Each provider's API-key environment variable comes from its preset (for example
    ``GROQ_API_KEY`` for Groq, ``OPENROUTER_API_KEY`` for OpenRouter). Some presets
    also declare env vars for the base URL / organization (e.g. genai-rpc).
    """
    for provider in Provider:
        creds = cfg.credentials.get(provider.value, ProviderCredentials())
        preset = PROVIDER_PRESETS.get(provider.value)
        env_key = preset.api_key_env if preset else None
        if env_key and not creds.api_key:
            creds.api_key = os.environ.get(env_key)

        env_base = _ENV_BASE_URLS.get(provider) or (preset.base_url_env if preset else None)
        if env_base and not creds.base_url:
            creds.base_url = os.environ.get(env_base)

        # Ollama also supports OLLAMA_HOST (the standard env var used by the
        # ``ollama`` CLI, e.g. ``OLLAMA_HOST=0.0.0.0:11434``).
        if provider == Provider.OLLAMA and not creds.base_url:
            ollama_host = os.environ.get("OLLAMA_HOST")
            if ollama_host:
                from kavi.providers.ollama import _base_url_from_host

                creds.base_url = _base_url_from_host(ollama_host)

        env_org = preset.organization_env if preset else None
        if env_org and not creds.organization:
            creds.organization = os.environ.get(env_org)

        cfg.credentials[provider.value] = creds


def load_config(
    cwd: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> KaviConfig:
    """Load configuration by merging defaults, user config, project config, and overrides.

    ``overrides`` typically carries CLI flags (for example ``{"model": "gpt-4o"}``).
    """
    cwd = cwd or Path.cwd()

    merged: dict[str, Any] = {}
    merged = _deep_merge(merged, _read_toml(user_config_dir() / "config.toml"))

    project_cfg = _find_project_config(cwd)
    if project_cfg is not None:
        merged = _deep_merge(merged, _read_toml(project_cfg))

    if overrides:
        merged = _deep_merge(merged, {k: v for k, v in overrides.items() if v is not None})

    cfg = KaviConfig.model_validate(merged)
    _apply_env_credentials(cfg)
    return cfg


def save_user_config(updates: dict[str, Any]) -> Path:
    """Merge ``updates`` into ``~/.kavi/config.toml`` and write it back.

    Used to persist things like API keys added at runtime via the /apikey command.
    """
    import tomli_w

    path = user_config_dir() / "config.toml"
    existing = _read_toml(path)
    merged = _deep_merge(existing, updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        tomli_w.dump(merged, fh)
    return path


def save_credential(
    provider: str, api_key: str, base_url: str | None = None, set_default: bool = True
) -> Path:
    """Persist an API key (and optional base URL) for a provider to the user config."""
    creds: dict[str, Any] = {"api_key": api_key}
    if base_url:
        creds["base_url"] = base_url
    updates: dict[str, Any] = {"credentials": {provider: creds}}
    if set_default:
        updates["provider"] = provider
        preset = PROVIDER_PRESETS.get(provider)
        if preset:
            updates["model"] = preset.default_model
    return save_user_config(updates)
