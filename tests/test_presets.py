"""Tests for provider presets, the /apikey command, and credential persistence."""

from __future__ import annotations

from pathlib import Path

from kavi.commands.builtins import ApiKeyCommand, ProviderCommand
from kavi.config.loader import load_config, save_credential
from kavi.config.schema import Provider
from kavi.providers.presets import PROVIDER_PRESETS, get_preset


def test_every_provider_has_a_preset():
    for provider in Provider:
        assert get_preset(provider.value) is not None, f"missing preset for {provider}"


def test_openai_compatible_have_base_urls():
    for pid, preset in PROVIDER_PRESETS.items():
        if preset.kind == "openai" and preset.id != "workersai":
            assert preset.base_url, f"{pid} needs a base_url"


def test_groq_preset():
    groq = get_preset("groq")
    assert groq.base_url == "https://api.groq.com/openai/v1"
    assert groq.api_key_env == "GROQ_API_KEY"
    assert groq.kind == "openai"


def test_genai_rpc_preset_and_client():
    from kavi.config.schema import KaviConfig, Provider
    from kavi.providers.registry import build_provider

    preset = get_preset("genai-rpc")
    assert preset is not None
    assert preset.organization == "f393aca3-1ec6-449c-87e1-5c3f7b9af033"
    assert preset.default_headers["Rpc-Service"] == "genai-api"

    cfg = KaviConfig(provider=Provider.GENAI_RPC, model="gpt-4o")
    cfg.credentials["genai-rpc"] = cfg.creds_for(Provider.GENAI_RPC)
    cfg.credentials["genai-rpc"].api_key = "dummy"

    provider = build_provider(cfg)
    client = provider._client
    assert client.organization == "f393aca3-1ec6-449c-87e1-5c3f7b9af033"
    headers = dict(client.default_headers)
    assert headers.get("Rpc-Service") == "genai-api"
    assert headers.get("Rpc-Caller") == "dco-raih"
    assert str(client.base_url).rstrip("/") == "http://localhost:5436/v1"


def test_genai_rpc_env_overrides(monkeypatch):
    from kavi.config.loader import _apply_env_credentials
    from kavi.config.schema import KaviConfig, Provider

    monkeypatch.setenv("LLM_API_BASE", "http://example.test:9000/v1")
    monkeypatch.setenv("LLM_ORGANIZATION", "org-from-env")
    monkeypatch.setenv("LLM_API_KEY", "key-from-env")

    cfg = KaviConfig(provider=Provider.GENAI_RPC)
    _apply_env_credentials(cfg)
    creds = cfg.creds_for(Provider.GENAI_RPC)
    assert creds.base_url == "http://example.test:9000/v1"
    assert creds.organization == "org-from-env"
    assert creds.api_key == "key-from-env"


class _FakeApp:
    from kavi.config.schema import KaviConfig

    config = KaviConfig()


async def test_apikey_command_lists_providers():
    out = await ApiKeyCommand().run(_FakeApp(), "")
    assert out.select is not None
    ids = {o.id for o in out.select.options}
    assert "groq" in ids and "openrouter" in ids


async def test_apikey_command_prompts_for_key_when_missing():
    class _App(_FakeApp):
        async def prompt_api_key(self, provider_id):  # user cancels the paste dialog
            return None

    out = await ApiKeyCommand().run(_App(), "groq")
    assert "Cancelled" in out.text


async def test_apikey_command_rejects_unknown_provider():
    out = await ApiKeyCommand().run(None, "notaprovider key123")
    assert "Unknown provider" in out.text


async def test_provider_command_lists_presets():
    out = await ProviderCommand().run(_FakeApp(), "")
    assert out.select is not None
    ids = {o.id for o in out.select.options}
    assert "groq" in ids and "anthropic" in ids
    assert "provider" in out.select.title.lower()


def test_save_credential_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("kavi.config.loader.user_config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    path = save_credential("groq", "gsk_test_key_123456")
    assert path.exists()

    cfg = load_config()
    assert cfg.provider == Provider.GROQ
    creds = cfg.creds_for(Provider.GROQ)
    assert creds.api_key == "gsk_test_key_123456"
