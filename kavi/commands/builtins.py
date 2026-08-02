"""Built-in slash commands."""


from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

from textual.containers import VerticalScroll

from kavi import __version__
from kavi.commands.base import Command, CommandOutput, Selection, SelectOption
from kavi.config.schema import DEFAULT_MODELS, Provider
from kavi.memory.loader import init_project_memory, load_memory
from kavi.providers.presets import PROVIDER_PRESETS, get_preset
from kavi.ui.widgets.messages import NoticeMessage

MODEL_SELECT_LIMIT = 300

if TYPE_CHECKING:
    from kavi.app import KaviApp


def _mask(api_key: str) -> str:
    if len(api_key) > 12:
        return api_key[:6] + "..." + api_key[-4:]
    return "***"


def _workersai_url(account_id: str) -> str:
    """Construct the Cloudflare Workers AI OpenAI-compatible endpoint."""
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id.strip()}/ai/v1"


def provider_has_key(app: KaviApp, provider_id: str) -> bool:
    """Whether a provider is usable (has a key, or needs none)."""
    preset = get_preset(provider_id)
    if preset is None:
        return False
    if preset.api_key_env is None:  # e.g. local Ollama
        return True
    creds = app.config.credentials.get(provider_id)
    if creds and creds.api_key:
        return True
    return bool(os.environ.get(preset.api_key_env))


async def _ollama_health(app: KaviApp) -> tuple[bool, str]:
    """Check Ollama server connectivity. Returns (ok, detail_message)."""
    from kavi.providers.ollama import OllamaProvider

    if isinstance(app.provider, OllamaProvider):
        return await app.provider.check_health()
    return False, "Provider is not Ollama."


class HelpCommand(Command):
    name = "help"
    description = "Show available commands."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        lines = ["Available commands:"]
        for cmd in app.command_registry.all():
            lines.append(f"  /{cmd.name:<10} {cmd.description}")
        lines.append("")
        lines.append("Type a message to talk to Kavi. Press Ctrl+C to cancel a running task.")
        return CommandOutput(text="\n".join(lines))


class NewCommand(Command):
    name = "new"
    description = "Create a new chat session."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        new_meta = app.session_store.create(
            app.cwd, app.config.provider.value, app.config.resolved_model()
        )
        from kavi.agent.context import Conversation
        from kavi.agent.prompts import build_system_prompt
        from kavi.memory.loader import load_memory

        memory = load_memory(app.cwd)
        skills_meta = [(s.name, s.description) for s in app.skills.values()] or None
        system_prompt = build_system_prompt(
            app.cwd,
            memory=memory,
            suffix=app.config.system_prompt_suffix,
            profile_prompt=app.profile.prompt,
            skills=skills_meta,
            plan_mode=app.config.permissions.effective_mode() == "plan",
            provider=app.config.provider.value,
            model=app.config.resolved_model(),
        )
        app.conversation = Conversation(system_prompt=system_prompt)
        app._build_engine()
        app.session_meta = new_meta
        app._persisted = 0
        chat = app.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        await app._add(NoticeMessage("New chat started."))
        return CommandOutput(text=f"New chat started ({new_meta.id}).")


class ClearCommand(Command):
    name = "clear"
    description = "Clear the conversation history."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        return CommandOutput(text="Conversation cleared.", clear=True)


class QuitCommand(Command):
    name = "quit"
    description = "Exit Kavi."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        return CommandOutput(quit=True)


class ExitCommand(QuitCommand):
    name = "exit"


class ModelCommand(Command):
    name = "model"
    description = "Pick a model (arrow-navigable). Usage: /model [name]."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        if args:
            return await self._apply(app, args.strip())

        current = app.config.resolved_model()
        provider = app.config.provider.value
        header = f"Provider: {provider}   Current model: {current}"

        models, error = await app.list_models()
        if error:
            preset = get_preset(provider)
            hint = ""
            if preset and preset.example_models:
                hint = "\nExample models: " + ", ".join(preset.example_models)
            return CommandOutput(
                text=f"{header}\n(could not fetch model list: {error}){hint}\n"
                "Set one with: /model <name>"
            )
        if not models:
            return CommandOutput(text=f"{header}\n(no models reported by provider)")

        # Surface the provider's recommended (verified-working) models first - handy
        # when a provider lists a huge catalog where only a subset is actually served.
        preset = get_preset(provider)
        recommended = [m for m in (preset.example_models if preset else []) if m in models]
        rest = [m for m in models if m not in set(recommended)]
        ordered = recommended + rest

        def _describe(m: str) -> str:
            bits = []
            if m == current:
                bits.append("current")
            if m in recommended:
                bits.append("recommended")
            return " · ".join(bits)

        options = [
            SelectOption(id=m, label=m, description=_describe(m))
            for m in ordered[:MODEL_SELECT_LIMIT]
        ]
        return CommandOutput(
            select=Selection(
                title=f"Select a model for {provider}  ({len(models)} available)",
                options=options,
                on_select=self._apply,
                filterable=True,
            )
        )

    async def _apply(self, app: KaviApp, choice: str) -> CommandOutput:
        app.config.model = choice
        # Remember this as the default for next launch (last model wins).
        from kavi.config.loader import save_user_config

        save_user_config({"model": choice})
        # Rebuild the system prompt so the agent knows its new model.
        app.reload_memory()
        return CommandOutput(text=f"Model set to {choice}.")


class ProviderCommand(Command):
    name = "provider"
    description = "Pick a provider (arrow-navigable). Usage: /provider [id]."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        choice = args.strip().lower()
        if not choice:
            current = app.config.provider.value
            options = []
            for pid, preset in PROVIDER_PRESETS.items():
                ready = provider_has_key(app, pid)
                bits = []
                if pid == current:
                    bits.append("current")
                bits.append("key set" if ready else "needs /apikey")
                options.append(
                    SelectOption(id=pid, label=preset.label, description=" · ".join(bits))
                )
            return CommandOutput(
                select=Selection(
                    title=f"Select a provider  (current: {current})",
                    options=options,
                    on_select=self._apply,
                    filterable=True,
                )
            )
        return await self._apply(app, choice)

    async def _apply(self, app: KaviApp, choice: str) -> CommandOutput:
        try:
            provider = Provider(choice)
        except ValueError:
            valid = ", ".join(p.value for p in Provider)
            return CommandOutput(text=f"Unknown provider: {choice}\nValid: {valid}")
        app.config.provider = provider
        app.config.model = DEFAULT_MODELS[provider]
        # Remember provider + model as the default for next launch.
        from kavi.config.loader import save_user_config

        save_user_config({"provider": provider.value, "model": app.config.model})
        try:
            app.rebuild_provider()
        except Exception as exc:  # noqa: BLE001
            return CommandOutput(
                text=f"Switched to {provider.value}, but it is not ready: {exc}\n"
                f"Add a key with: /apikey {provider.value} <your-key>"
            )
        # Rebuild the system prompt so the agent knows its new provider + model.
        app.reload_memory()

        # For Ollama, run a connectivity check and auto-select an installed model.
        health_hint = ""
        if provider == Provider.OLLAMA and app.provider is not None:
            ok, detail = await _ollama_health(app)
            health_hint = f"\n{detail}"

            # The preset default may not be downloaded. Query the actually-
            # installed models and pick the first one so the user can chat
            # immediately without a 404.
            if ok:
                try:
                    installed = await app.provider.list_models()
                    if installed and app.config.resolved_model() not in installed:
                        app.config.model = installed[0]
                        save_user_config({"model": app.config.model})
                        app.reload_memory()
                        health_hint += f"\nAuto-selected model: {app.config.model} (preset default not installed)"
                except Exception:  # noqa: BLE001
                    pass

        return CommandOutput(
            text=f"Provider set to {provider.value} (model: {app.config.resolved_model()}).{health_hint}"
        )


class ApiKeyCommand(Command):
    name = "apikey"
    description = "Add an API key for a provider. Usage: /apikey <provider> <key>."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        parts = args.split()
        if not parts:
            options = []
            for pid, preset in PROVIDER_PRESETS.items():
                ready = provider_has_key(app, pid)
                options.append(
                    SelectOption(
                        id=pid,
                        label=preset.label,
                        description="key set" if ready else "no key yet",
                    )
                )
            return CommandOutput(
                select=Selection(
                    title="Select a provider to add an API key for",
                    options=options,
                    on_select=self._prompt_and_set,
                    filterable=True,
                )
            )

        provider_id = parts[0].lower()
        preset = get_preset(provider_id)
        if preset is None:
            valid = ", ".join(PROVIDER_PRESETS.keys())
            return CommandOutput(text=f"Unknown provider '{provider_id}'.\nValid: {valid}")

        # `/apikey <provider>` with no key -> ask for it interactively.
        if len(parts) < 2:
            return await self._prompt_and_set(app, provider_id)

        # Workers AI needs an account ID alongside the key:
        #   /apikey workersai <key> <account_id>
        if provider_id == "workersai" and len(parts) >= 3:
            return await self._finalize(
                app, provider_id, parts[1], base_url=_workersai_url(parts[2])
            )

        return await self._finalize(app, provider_id, parts[1])

    async def _prompt_and_set(self, app: KaviApp, provider_id: str) -> CommandOutput:
        preset = get_preset(provider_id)
        if preset is None:
            valid = ", ".join(PROVIDER_PRESETS.keys())
            return CommandOutput(text=f"Unknown provider '{provider_id}'.\nValid: {valid}")
        if preset.api_key_env is None:  # e.g. local Ollama - no key required
            return await self._finalize(app, provider_id, "")

        key = await app.prompt_api_key(provider_id)
        if not key:
            return CommandOutput(text="Cancelled - no API key entered.")

        # Workers AI also needs an account ID to construct the base URL.
        if provider_id == "workersai":
            from kavi.ui.widgets.apikey import ApiKeyPromptScreen

            account_id = await app.push_screen_wait(
                ApiKeyPromptScreen(
                    "Account ID",
                    "Find it on https://dash.cloudflare.com/ → any domain → Overview",
                    None,
                    prompt_text="Enter your Cloudflare Account ID",
                    is_password=False,
                )
            )
            if not account_id:
                return CommandOutput(text="Cancelled - no account ID entered.")
            return await self._finalize(
                app, provider_id, key, base_url=_workersai_url(account_id)
            )

        return await self._finalize(app, provider_id, key)

    async def _finalize(
        self, app: KaviApp, provider_id: str, api_key: str, base_url: str | None = None
    ) -> CommandOutput:
        """Persist the key, switch provider, and validate the connection live."""
        preset = get_preset(provider_id)
        label = preset.label if preset else provider_id
        try:
            path = app.set_provider_credential(provider_id, api_key, base_url=base_url)
        except Exception as exc:  # noqa: BLE001
            return CommandOutput(text=f"Saved key but provider not ready: {exc}")

        models, error = await app.list_models()
        if error:
            return CommandOutput(
                text=(
                    f"Saved {label} key to {path}, but the connection test failed:\n"
                    f"  {error}\n"
                    "Double-check the key (and any org/billing limits) and try again."
                )
            )
        masked = _mask(api_key)
        return CommandOutput(
            text=(
                f"Connected to {label}! ({len(models)} models available)\n"
                f"Saved key {masked} to {path}.\n"
                f"Provider is now '{provider_id}', model '{app.config.resolved_model()}'.\n"
                "Run /model to pick a model."
            )
        )


class LoginCommand(ApiKeyCommand):
    name = "login"
    description = "Alias for /apikey."


class CostCommand(Command):
    name = "cost"
    description = "Show token usage and estimated cost."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        return CommandOutput(text=app.cost.summary())


class ToolsCommand(Command):
    name = "tools"
    description = "List available tools."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        lines = ["Available tools:"]
        for tool in app.registry.all():
            flag = "ro" if tool.is_read_only else "rw"
            lines.append(f"  [{flag}] {tool.name}")
        return CommandOutput(text="\n".join(lines))


class InitCommand(Command):
    name = "init"
    description = "Create a starter KAVI.md in the current directory."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        path = init_project_memory(app.cwd)
        app.reload_memory()
        return CommandOutput(text=f"Wrote {path}. Edit it to give Kavi project context.")


class MemoryCommand(Command):
    name = "memory"
    description = "Show the loaded project memory."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        mem = load_memory(app.cwd)
        return CommandOutput(text=mem or "No KAVI.md memory files found. Use /init to create one.")


class ResumeCommand(Command):
    name = "resume"
    description = "Pick a previous session to resume (arrow-navigable)."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        sessions = app.session_store.list(limit=30)
        if not sessions:
            return CommandOutput(text="No saved sessions yet.")

        arg = args.strip()
        if arg:  # /resume <id> loads directly
            return await self._apply(app, arg)

        options = []
        for meta in sessions:
            title = meta.title or "(untitled)"
            when = meta.updated_at[:16].replace("T", " ")
            options.append(
                SelectOption(
                    id=meta.id,
                    label=title,
                    description=f"{when} · {meta.message_count} msgs · {meta.model}",
                )
            )
        return CommandOutput(
            select=Selection(
                title="Resume a session",
                options=options,
                on_select=self._apply,
                filterable=True,
            )
        )

    async def _apply(self, app: KaviApp, choice: str) -> CommandOutput:
        ok = await app.resume_session(choice)
        if not ok:
            return CommandOutput(text=f"No session found for '{choice}'.")
        return CommandOutput()  # resume_session renders its own history + notice


class McpCommand(Command):
    name = "mcp"
    description = "Show MCP servers (incl. those imported from Claude Code / Codex)."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        manager = app.mcp_manager
        origins = getattr(app, "mcp_sources", {}) or {}
        connected = manager.connected_servers() if manager is not None else {}

        if not origins and not connected:
            return CommandOutput(text="No MCP servers configured or discovered.")

        lines = ["MCP servers:"]
        for name in sorted(origins) or connected:
            origin = origins.get(name, "")
            tag = f"  [{origin}]" if origin else ""
            if name in connected:
                tools = connected[name]
                lines.append(f"  ● {name}{tag}: {', '.join(tools) or '(no tools)'}")
            else:
                lines.append(f"  ○ {name}{tag}: not connected")
        lines.append("")
        lines.append("● connected   ○ configured but unreachable")
        return CommandOutput(text="\n".join(lines))


class AgentsCommand(Command):
    name = "agents"
    description = "List sub-agent types available to the Task tool."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        from kavi.subagents.runner import AGENT_TYPES

        lines = ["Sub-agent types (use via the Task tool):"]
        for name, spec in AGENT_TYPES.items():
            lines.append(f"  {name}: {spec['description']}")
        return CommandOutput(text="\n".join(lines))


class DoctorCommand(Command):
    name = "doctor"
    description = "Run environment diagnostics."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        import importlib.util
        import platform
        import sys

        cfg = app.config
        creds = cfg.creds_for(cfg.provider)

        optional = {"ddgs": "web search", "rg": "fast search"}
        opt_status = []
        for name, purpose in optional.items():
            present = (
                importlib.util.find_spec(name) is not None
                if name != "rg"
                else shutil.which("rg") is not None
            )
            opt_status.append(f"{name} ({purpose}): {'found' if present else 'missing'}")

        checks = [
            f"Kavi version: {__version__}",
            f"Python: {platform.python_version()} ({sys.executable})",
            f"Platform: {platform.system()} {platform.release()}",
            f"Provider: {cfg.provider.value}  Model: {cfg.resolved_model()}",
            f"API key present: {'yes' if (creds.api_key or cfg.provider == Provider.OLLAMA) else 'NO'}",
            "Optional tools: " + ", ".join(opt_status),
            f"Working dir: {app.cwd}",
            f"Tools registered: {len(app.registry.all())}",
            f"MCP servers: {len(app.mcp_manager.connected_servers()) if app.mcp_manager else 0}",
        ]

        # Ollama health check
        if cfg.provider == Provider.OLLAMA and app.provider is not None:
            ok, detail = await _ollama_health(app)
            checks.append(f"Ollama: {detail}")

        return CommandOutput(text="Doctor:\n  " + "\n  ".join(checks))


class ThemeCommand(Command):
    name = "theme"
    description = "Switch color theme (light / dark, and more)."

    _ALIASES = {"dark": "textual-dark", "light": "textual-light"}

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        arg = args.strip().lower()
        if arg:
            name = self._ALIASES.get(arg, arg)
            if name not in app.available_themes:
                valid = ", ".join(sorted(app.available_themes))
                return CommandOutput(text=f"Unknown theme '{arg}'.\nAvailable: {valid}")
            return await self._apply(app, name)

        names = list(app.available_themes.keys())
        preferred = [n for n in ("textual-dark", "textual-light") if n in names]
        rest = sorted(n for n in names if n not in preferred)
        current = app.theme
        options = []
        for n in preferred + rest:
            if n == current:
                desc = "current"
            elif "dark" in n:
                desc = "dark"
            elif "light" in n:
                desc = "light"
            else:
                desc = ""
            options.append(SelectOption(id=n, label=self._pretty(n), description=desc))
        return CommandOutput(
            select=Selection(
                title="Select a theme",
                options=options,
                on_select=self._apply,
                filterable=True,
            )
        )

    async def _apply(self, app: KaviApp, choice: str) -> CommandOutput:
        app.apply_theme(choice)
        return CommandOutput(text=f"Theme set to {choice}.")

    @staticmethod
    def _pretty(name: str) -> str:
        return name.replace("-", " ").title()


class ProfileCommand(Command):
    name = "profile"
    description = "Pick a use-case profile (arrow-navigable). Usage: /profile [name]."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        from kavi.agent.profiles import all_profiles
        from kavi.config.loader import profiles_dir, save_user_config

        profiles = all_profiles(profiles_dir())
        choice = args.strip().lower()
        if choice:
            if choice not in profiles:
                available = ", ".join(sorted(profiles))
                return CommandOutput(text=f"Unknown profile: {choice}. Available: {available}")
            name = app.set_profile(choice)
            save_user_config({"profile": name})
            return CommandOutput(text=f"Profile set to '{name}'.")

        current = app.config.profile
        options = [
            SelectOption(
                id=p.name,
                label=p.name,
                description=(p.description + (" (current)" if p.name == current else "")),
            )
            for p in profiles.values()
        ]

        async def _apply(app: KaviApp, chosen: str) -> CommandOutput:
            name = app.set_profile(chosen)
            save_user_config({"profile": name})
            return CommandOutput(text=f"Profile set to '{name}'.")

        return CommandOutput(
            select=Selection(
                title=f"Select a profile  (current: {current})",
                options=options,
                on_select=_apply,
            )
        )


class ModeCommand(Command):
    name = "mode"
    description = "Pick a permission mode (arrow-navigable). Usage: /mode [default|auto|plan|bypass]."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        from kavi.config.loader import save_user_config
        from kavi.config.schema import PERMISSION_MODE_LABELS, PERMISSION_MODES

        choice = args.strip().lower()
        current = app.config.permissions.effective_mode()

        if choice:
            if choice not in PERMISSION_MODES:
                available = ", ".join(PERMISSION_MODES)
                return CommandOutput(text=f"Unknown mode: {choice}. Available: {available}")
            mode = app.set_mode(choice)
            save_user_config({"permissions": {"mode": mode, "yolo": mode == "bypass"}})
            return CommandOutput(
                text=f"Permission mode set to '{mode}' - {PERMISSION_MODE_LABELS[mode]}."
            )

        options = [
            SelectOption(
                id=m,
                label=m,
                description=PERMISSION_MODE_LABELS[m]
                + (" (current)" if m == current else ""),
            )
            for m in PERMISSION_MODES
        ]

        async def _apply(app: KaviApp, chosen: str) -> CommandOutput:
            mode = app.set_mode(chosen)
            save_user_config({"permissions": {"mode": mode, "yolo": mode == "bypass"}})
            return CommandOutput(
                text=f"Permission mode set to '{mode}' - {PERMISSION_MODE_LABELS[mode]}."
            )

        return CommandOutput(
            select=Selection(
                title=f"Select a permission mode  (current: {current})",
                options=options,
                on_select=_apply,
            )
        )


class SkillsCommand(Command):
    name = "skills"
    description = "List skills discovered from .kavi/.claude/.codex roots and plugins."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        skills = getattr(app, "skills", {}) or {}
        if not skills:
            return CommandOutput(
                text="No skills found. Add one under .kavi/skills/<name>/SKILL.md"
            )
        lines = ["Available skills:"]
        for s in skills.values():
            lines.append(f"  {s.name:<20} {s.description}")
        return CommandOutput(text="\n".join(lines))


class HooksCommand(Command):
    name = "hooks"
    description = "Show configured lifecycle hooks (hooks.json)."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        runner = getattr(app, "hooks", None)
        if runner is None:
            return CommandOutput(text="No hooks configured.")
        lines = ["Configured hooks:"]
        total = 0
        for event, hooks in runner.hooks.items():
            for h in hooks:
                total += 1
                matcher = h.matcher or "*"
                lines.append(f"  {event:<18} [{matcher}] -> {h.command}")
        if total == 0:
            return CommandOutput(text="No hooks configured. Add a hooks.json under .kavi/")
        return CommandOutput(text="\n".join(lines))


class PluginsCommand(Command):
    name = "plugins"
    description = "List/install/remove plugins. Usage: /plugins [install <src>|remove <name>]."

    async def run(self, app: KaviApp, args: str) -> CommandOutput:
        from kavi.extensions import plugins

        parts = args.split(maxsplit=1)
        action = parts[0].lower() if parts else ""

        if action == "install" and len(parts) > 1:
            try:
                plugin = plugins.install_plugin(parts[1].strip())
            except ValueError as exc:
                return CommandOutput(text=f"Install failed: {exc}")
            return CommandOutput(
                text=f"Installed plugin '{plugin.name}'. Restart Kavi to load it."
            )
        if action == "remove" and len(parts) > 1:
            ok = plugins.remove_plugin(parts[1].strip())
            return CommandOutput(
                text=("Removed. Restart to apply." if ok else "No such plugin.")
            )

        installed = plugins.list_plugins()
        if not installed:
            return CommandOutput(
                text="No plugins installed. Install with: /plugins install <dir|git-url>"
            )
        lines = ["Installed plugins:"]
        for p in installed:
            comps = ", ".join(p.components()) or "(empty)"
            lines.append(f"  {p.name:<20} [{comps}]")
        return CommandOutput(text="\n".join(lines))


ALL_COMMANDS: list[type[Command]] = [
    HelpCommand,
    ClearCommand,
    QuitCommand,
    ExitCommand,
    ThemeCommand,
    ModelCommand,
    ProviderCommand,
    ProfileCommand,
    ModeCommand,
    SkillsCommand,
    HooksCommand,
    PluginsCommand,
    ApiKeyCommand,
    LoginCommand,
    CostCommand,
    ToolsCommand,
    InitCommand,
    MemoryCommand,
    ResumeCommand,
    McpCommand,
    AgentsCommand,
    DoctorCommand,
    NewCommand,
]
