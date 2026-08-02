"""Smoke test for the Textual app wiring (no network / no API key)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kavi.config.schema import KaviConfig, Provider
from kavi.ui.widgets.messages import NoticeMessage, UserMessage


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


async def test_app_boots_and_handles_command(tmp_path: Path, monkeypatch):
    # Keep session state inside the tmp dir.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    from kavi.app import KaviApp

    config = KaviConfig(provider=Provider.ANTHROPIC, model="claude-sonnet-4-test")
    app = KaviApp(config=config, cwd=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        # A ready notice should be shown on boot.
        assert len(app.query(NoticeMessage)) >= 1

        notices_before = len(app.query(NoticeMessage))

        # A command returns informational text via the registry.
        out = await app.command_registry.dispatch(app, "/help")
        assert out.text and "Available commands" in out.text

        # Submitting input echoes a UserMessage and runs the command, adding a notice.
        from kavi.ui.widgets.prompt import PromptArea

        assert app.query_one("#prompt", PromptArea) is not None
        await app.submit_text("/tools")
        await pilot.pause()
        await pilot.pause()

        assert len(app.query(UserMessage)) >= 1
        assert len(app.query(NoticeMessage)) > notices_before


async def test_permission_dialog_renders(tmp_path: Path, monkeypatch):
    # Regression: PermissionDialog must not shadow Textual's Widget._render().
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    from kavi.app import KaviApp
    from kavi.config.schema import KaviConfig, Provider
    from kavi.ui.widgets.permission import PermissionDialog

    config = KaviConfig(provider=Provider.ANTHROPIC, model="claude-sonnet-4-test")
    app = KaviApp(config=config, cwd=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(PermissionDialog("Bash", "rm -rf x", "Run: rm -rf x"))
        await pilot.pause()
        dialog = app.screen
        assert isinstance(dialog, PermissionDialog)
        # _render must still be Textual's callable method, not the string we stored.
        assert callable(dialog._render)
        dialog._render()  # would raise TypeError if shadowed by a str
        await pilot.press("y")
        await pilot.pause()
        assert not isinstance(app.screen, PermissionDialog)


async def test_new_command_resets_engine_conversation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    from kavi.app import KaviApp
    from kavi.config.schema import KaviConfig, Provider, ProviderCredentials
    from tests.fakes import FakeProvider

    monkeypatch.setattr("kavi.app.build_provider", lambda cfg: FakeProvider([]))
    monkeypatch.setattr("kavi.providers.registry.build_provider", lambda cfg: FakeProvider([]))

    config = KaviConfig(
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-test",
        credentials={"anthropic": ProviderCredentials(api_key="test-key")},
    )
    app = KaviApp(config=config, cwd=tmp_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.engine is not None

        # Add a dummy message to the current conversation
        app.conversation.add_user_text("Hello old context")
        assert len(app.conversation.messages) == 1

        # Run /new command
        await app.command_registry.dispatch(app, "/new")
        await pilot.pause()

        # Engine's conversation must now be fresh and match app.conversation
        assert len(app.conversation.messages) == 0
        assert app.engine is not None
        assert app.engine.conversation is app.conversation
